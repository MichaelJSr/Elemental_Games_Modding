"""Regression tests for the reference XBR-side feature.

``player_max_hp`` (formerly ``cheat_entity_hp``) is the smallest
possible declarative XBR pack — one slider edits garret4's
hitPoints.  If it breaks, something in the Phase-3 wiring has
drifted.  Pin it so the reference stays trustworthy as both an
example in ``docs/XBR_PACKS.md`` and an integration canary for
``apply_pack(xbr_files=...)``.

The target cell is ``critters_critter_data.garret4.hitPoints`` —
the only writable garret4/hitPoints cell in the shipping
``config.xbr``.  The planning doc's ``critters_damage`` target
does not exist on disk; see the
``azurik_mod.patches.player_max_hp`` module docstring for the
Ghidra-vs-disk mismatch story.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.patching.apply import apply_pack  # noqa: E402
from azurik_mod.patching.registry import get_pack  # noqa: E402
from azurik_mod.patching.xbr_staging import XbrStaging  # noqa: E402
from azurik_mod.patching.xbr_spec import XbrParametricEdit  # noqa: E402
from azurik_mod.xbr import XbrDocument  # noqa: E402


_GAMEDATA_CANDIDATES = [
    Path("/Users/michaelsrouji/Documents/Xemu/tools/"
         "Azurik - Rise of Perathia (USA).xiso/gamedata"),
    _REPO_ROOT.parent / "Azurik - Rise of Perathia (USA).xiso" / "gamedata",
]


def _find_gamedata() -> Path | None:
    for p in _GAMEDATA_CANDIDATES:
        if p.exists():
            return p
    return None


_GAMEDATA = _find_gamedata()


class PlayerMaxHpRegistration(unittest.TestCase):
    """The reference feature must register itself on import."""

    def test_feature_is_registered(self):
        import azurik_mod.patches  # noqa: F401
        pack = get_pack("player_max_hp")
        self.assertEqual(pack.category, "player")
        self.assertEqual(pack.subgroup, "quick_stats")
        self.assertEqual(len(pack.xbr_sites), 1)
        site = pack.xbr_sites[0]
        self.assertIsInstance(site, XbrParametricEdit)
        self.assertEqual(site.xbr_file, "config.xbr")
        self.assertEqual(site.section, "critters_critter_data")
        self.assertEqual(site.entity, "garret4")
        self.assertEqual(site.prop, "hitPoints")

    def test_touched_xbr_files_surfaces_config_xbr(self):
        import azurik_mod.patches  # noqa: F401
        pack = get_pack("player_max_hp")
        self.assertEqual(pack.touched_xbr_files(), ("config.xbr",))

    def test_parameters_exposes_slider_name(self):
        import azurik_mod.patches  # noqa: F401
        pack = get_pack("player_max_hp")
        self.assertIn("garret4_hit_points", pack.parameters)

    def test_cheat_entity_hp_alias_resolves(self):
        """The legacy name must still resolve for back-compat.

        Anyone with ``--enable-pack cheat_entity_hp`` in a script or
        an old saved state should keep getting the same behaviour,
        just with a one-shot deprecation warning.
        """
        import azurik_mod.patches  # noqa: F401
        from azurik_mod.patching import registry
        # Other tests (test_legacy_pack_migration) run in the same
        # process and may have already tripped the one-shot warning
        # cache — reset it before asserting the warning fires.
        registry._WARNED_LEGACY_ALIASES.discard("cheat_entity_hp")
        with warnings.catch_warnings(record=True) as ws:
            warnings.simplefilter("always")
            pack = get_pack("cheat_entity_hp")
        self.assertEqual(pack.name, "player_max_hp")
        msgs = [str(w.message) for w in ws
                if issubclass(w.category, DeprecationWarning)]
        self.assertTrue(
            any("cheat_entity_hp" in m and "player_max_hp" in m
                for m in msgs),
            msg=f"expected one DeprecationWarning mentioning the "
                f"rename; got {msgs!r}")

    def test_tags_drop_cheat_framing(self):
        """The rename was specifically to drop the cheat label.

        If someone accidentally re-adds ``"cheat"`` to the tags
        tuple, the Player-tab Quick Stats group starts looking
        like a cheat menu again.  Pin the current shape.
        """
        import azurik_mod.patches  # noqa: F401
        pack = get_pack("player_max_hp")
        self.assertNotIn("cheat", pack.tags)
        self.assertIn("xbr", pack.tags)


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class PlayerMaxHpApply(unittest.TestCase):
    """Exercise the feature end-to-end through :func:`apply_pack`."""

    def setUp(self):
        import azurik_mod.patches  # noqa: F401
        self._tmpdir = Path(tempfile.mkdtemp(prefix="player_hp_"))
        (self._tmpdir / "gamedata").mkdir()
        shutil.copy2(_GAMEDATA / "config.xbr",
                     self._tmpdir / "gamedata" / "config.xbr")
        self.pack = get_pack("player_max_hp")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_applies_with_explicit_value(self):
        staging = XbrStaging(self._tmpdir)
        apply_pack(self.pack, bytearray(0x1000),
                   params={"garret4_hit_points": 500.0},
                   xbr_files=staging)
        written = staging.flush()
        self.assertIn("config.xbr", written)
        doc = XbrDocument.load(
            self._tmpdir / "gamedata" / "config.xbr")
        ks = doc.keyed_sections()["critters_critter_data"]
        self.assertEqual(
            ks.find_cell("garret4", "hitPoints").double_value, 500.0)

    def test_applies_with_default_value(self):
        staging = XbrStaging(self._tmpdir)
        apply_pack(self.pack, bytearray(0x1000), xbr_files=staging)
        staging.flush()
        doc = XbrDocument.load(
            self._tmpdir / "gamedata" / "config.xbr")
        # Default = 200.0 (the shipping vanilla value, pinned in
        # the slider declaration so "enabled, untouched" is a
        # no-op write of the value the cell already held).
        self.assertEqual(
            doc.keyed_sections()["critters_critter_data"]
               .find_cell("garret4", "hitPoints").double_value,
            200.0)

    def test_out_of_range_value_raises(self):
        staging = XbrStaging(self._tmpdir)
        with self.assertRaises(ValueError):
            apply_pack(self.pack, bytearray(0x1000),
                       params={"garret4_hit_points": 1e9},
                       xbr_files=staging)


class PackBrowserRendersXbrSliders(unittest.TestCase):
    """``player_max_hp``'s slider is an :class:`XbrParametricEdit`
    (lives in ``xbr_sites``, not ``sites``).  The Patches-page
    slider renderer must pick up both kinds — a regression where
    it only rendered ``parametric_sites()`` would leave this
    feature with a bare checkbox and no way for the user to
    change the HP value.

    Exercised through :class:`gui.widgets.PackBrowser` — the
    actual widget the GUI builds — with a dummy Tk root so we
    catch real rendering bugs, not just dict plumbing.
    """

    def setUp(self):
        import azurik_mod.patches  # noqa: F401
        import tkinter as tk
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk not available: {exc}")
        self.root.withdraw()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:  # noqa: BLE001
            pass

    def test_player_max_hp_gets_a_slider_widget(self):
        from gui.widgets import PackBrowser
        from azurik_mod.patching.registry import all_packs
        import tkinter as tk
        vars_: dict[str, tk.BooleanVar] = {}
        pack_params: dict[str, dict[str, float]] = {}
        browser = PackBrowser(
            self.root, all_packs(), vars_,
            pack_params=pack_params, on_param_change=None)
        sliders = browser.sliders()
        key = ("player_max_hp", "garret4_hit_points")
        self.assertIn(
            key, sliders,
            msg=f"player_max_hp's garret4_hit_points slider isn't "
                f"in the PackBrowser's rendered slider map "
                f"({sorted(sliders)!r}).  Likely cause: PackBrowser "
                f"only iterates parametric_sites() and skips "
                f"xbr_parametric_sites(), so XBR-only features "
                f"render as bare checkboxes.")

    def test_xbe_sliders_still_render(self):
        """Sanity check: the XBE-side slider path still works.
        ``player_physics`` has ~14 ParametricPatch sliders — they
        must all survive the XBR-site addition."""
        from gui.widgets import PackBrowser
        from azurik_mod.patching.registry import all_packs
        import tkinter as tk
        vars_: dict[str, tk.BooleanVar] = {}
        pack_params: dict[str, dict[str, float]] = {}
        browser = PackBrowser(
            self.root, all_packs(), vars_,
            pack_params=pack_params, on_param_change=None)
        sliders = browser.sliders()
        pp_sliders = [k for k in sliders
                      if k[0] == "player_physics"]
        self.assertGreater(
            len(pp_sliders), 0,
            msg="player_physics XBE-side sliders vanished — the "
                "xbr_parametric_sites() merge broke something.")


if __name__ == "__main__":
    unittest.main()
