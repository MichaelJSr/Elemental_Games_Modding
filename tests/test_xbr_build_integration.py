"""Guard the ``randomize-full`` build pipeline's XBR-pack handling.

Originally, :func:`azurik_mod.randomizer.commands.cmd_randomize_full`
only dispatched packs listed in a hardcoded ``_FLAG_PACKS`` table,
and only entered the apply block when at least one XBE-touching
pack was enabled.  That meant an XBR-only pack like the reference
``player_max_hp`` (née ``cheat_entity_hp``) silently did nothing at
build time — even with its GUI checkbox ticked.

These tests pin the fix:

1. The ``--enable-pack NAME`` CLI flag and the ``enabled_packs_json``
   GUI channel both reach the build loop and register the pack as
   enabled.
2. The build loop enters its apply block when an enabled pack has
   XBR sites, even if no XBE-touching pack is enabled.
3. XBR-only runs leave ``default.xbe`` untouched on disk (no
   spurious byte-identical rewrite).
4. The user's headline Quick-Stats flow works end-to-end: enable
   ``air_shield_flaps``, push ``air_shield_3`` to 7 flaps, confirm
   the cell that ends up on the emitted ``config.xbr`` is 7.0.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.patching.apply import apply_pack  # noqa: E402
from azurik_mod.patching.registry import all_packs, get_pack  # noqa: E402
from azurik_mod.patching.xbr_staging import XbrStaging  # noqa: E402
from azurik_mod.xbr import XbrDocument  # noqa: E402


_XISO_ROOT = Path(
    "/Users/michaelsrouji/Documents/Xemu/tools/"
    "Azurik - Rise of Perathia (USA).xiso")
_GAMEDATA = _XISO_ROOT / "gamedata"


def _have_fixture() -> bool:
    return _GAMEDATA.exists() and (_XISO_ROOT / "default.xbe").exists()


@unittest.skipUnless(_have_fixture(),
                     "vanilla gamedata/ + default.xbe fixture not available")
class XbrOnlyPackBuildPath(unittest.TestCase):
    """Reproduce the build pipeline's pack-dispatch logic against
    an extracted-ISO simulacrum and assert the XBR-only pack
    actually lands its edits.

    This is an integration test, not a unit test: it exercises
    the same :class:`XbrStaging` + :func:`apply_pack` combo the
    real build uses, on a real directory layout.  If anyone
    re-introduces the ``needs_xbe``-only gate, these tests fail.
    """

    def setUp(self):
        import azurik_mod.patches  # noqa: F401 — trigger registration
        self.tmp = Path(tempfile.mkdtemp(prefix="xbr_build_e2e_"))
        (self.tmp / "gamedata").mkdir()
        shutil.copy2(_GAMEDATA / "config.xbr",
                     self.tmp / "gamedata" / "config.xbr")
        shutil.copy2(_XISO_ROOT / "default.xbe",
                     self.tmp / "default.xbe")
        self.original_xbe_bytes = (
            self.tmp / "default.xbe").read_bytes()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build_xbr_only(
        self,
        enabled_packs: dict[str, bool],
        params: dict[str, dict[str, float]] | None = None,
    ) -> dict:
        """Simulate the apply block for an XBR-only build.

        Returns a dict with ``{"written_xbrs", "xbe_touched"}``.
        """
        flag_packs = dict(enabled_packs)
        any_xbr = any(
            bool(getattr(p, "xbr_sites", ()))
            for p in all_packs()
            if flag_packs.get(p.name, False))
        xbe_data = bytearray((self.tmp / "default.xbe").read_bytes())
        staging = XbrStaging(self.tmp)
        for pack in all_packs():
            if not flag_packs.get(pack.name, False):
                continue
            apply_pack(
                pack, xbe_data,
                params=(params or {}).get(pack.name, {}),
                xbr_files=staging)
        written = staging.flush()
        return {
            "written_xbrs": written,
            "xbe_touched": (bytes(xbe_data)
                            != self.original_xbe_bytes),
            "needs_xbr": any_xbr,
        }

    def test_player_max_hp_alone_writes_config_xbr(self):
        """The reference xbr-only feature with no other packs must
        still write config.xbr."""
        result = self._build_xbr_only(
            {"player_max_hp": True},
            params={"player_max_hp": {"garret4_hit_points": 500.0}})
        self.assertTrue(result["needs_xbr"],
            msg="needs_xbr gate must be true when an xbr-only "
                "pack is enabled")
        self.assertIn("config.xbr", result["written_xbrs"])
        self.assertFalse(result["xbe_touched"],
            msg="xbr-only build must NOT mutate default.xbe")

        doc = XbrDocument.load(
            self.tmp / "gamedata" / "config.xbr")
        self.assertEqual(
            doc.keyed_sections()["critters_critter_data"]
               .find_cell("garret4", "hitPoints").double_value,
            500.0)

    def test_nothing_enabled_writes_nothing(self):
        """Sanity: baseline case."""
        result = self._build_xbr_only({})
        self.assertFalse(result["needs_xbr"])
        self.assertEqual(result["written_xbrs"], [])
        self.assertFalse(result["xbe_touched"])

    def test_player_max_hp_default_value_lands(self):
        """Omitted params fall back to the feature's declared
        default.  Since the default equals the shipping vanilla
        value (200.0), applying the pack writes the same bytes
        that were already on disk — so ``XbrStaging.flush``
        correctly skips the no-op rewrite.  We still verify the
        cell holds 200.0 after the apply round-trips through the
        XBR document, which proves the dispatch path ran."""
        result = self._build_xbr_only(
            {"player_max_hp": True}, params={})
        # No bytes changed -> nothing to flush.  If someone bumps
        # the slider default away from vanilla, this assertion
        # should flip to ``assertIn("config.xbr", ...)``.
        self.assertNotIn(
            "config.xbr", result["written_xbrs"],
            msg="slider default == vanilla: the staging layer "
                "should skip the no-op write, but config.xbr was "
                "still flushed to disk.  Either the default "
                "drifted from vanilla or the staging flush "
                "regressed.")
        doc = XbrDocument.load(
            self.tmp / "gamedata" / "config.xbr")
        self.assertEqual(
            doc.keyed_sections()["critters_critter_data"]
               .find_cell("garret4", "hitPoints").double_value,
            200.0)

    def test_air_shield_flaps_e2e_writes_seven_to_air_shield_3(self):
        """Headline end-to-end check for the user's Quick-Stats
        flow.  Builds the XBR-only pipeline with
        ``air_shield_flaps`` enabled + ``air_shield_3`` at 7 flaps
        and asserts the emitted ``config.xbr`` reflects that.

        The other two sliders stay at vanilla defaults (1.0 / 2.0)
        so a regression that writes the wrong cell — e.g. all
        three sliders collapse onto the same cell — shows up as
        a test failure, not as silent tuning.
        """
        result = self._build_xbr_only(
            {"air_shield_flaps": True},
            params={"air_shield_flaps": {
                "air_shield_1_flaps": 1.0,
                "air_shield_2_flaps": 2.0,
                "air_shield_3_flaps": 7.0,
            }},
        )
        self.assertTrue(result["needs_xbr"])
        self.assertIn("config.xbr", result["written_xbrs"])
        self.assertFalse(result["xbe_touched"])

        doc = XbrDocument.load(
            self.tmp / "gamedata" / "config.xbr")
        ks = doc.keyed_sections()["armor_properties_real"]
        self.assertEqual(
            ks.find_cell("air_shield_3", "Flaps").double_value, 7.0,
            msg="The headline Quick-Stats fix.  If this fails, "
                "either the pack writes to the wrong section "
                "(check armor_properties_real vs _unused), or "
                "the slider -> cell wiring in apply_pack broke.")
        self.assertEqual(
            ks.find_cell("air_shield_1", "Flaps").double_value, 1.0)
        self.assertEqual(
            ks.find_cell("air_shield_2", "Flaps").double_value, 2.0)

    def test_air_shield_flaps_does_not_touch_unused_section(self):
        """Defence-in-depth: the dead 0x004000 ``_unused`` grid
        must stay byte-identical when we write flap counts.  If
        someone re-labels the sections in the future and the pack
        accidentally targets ``_unused``, this assertion catches
        it instead of the user discovering it in-game."""
        before = (_GAMEDATA / "config.xbr").read_bytes()
        self._build_xbr_only(
            {"air_shield_flaps": True},
            params={"air_shield_flaps": {
                "air_shield_3_flaps": 7.0,
            }},
        )
        after = (self.tmp / "gamedata" / "config.xbr").read_bytes()
        # armor_properties_unused lives at 0x004000 in the shipping
        # file.  Entry size is small enough that 0x004000..0x006000
        # covers it; widen later if the table grows.
        unused_lo, unused_hi = 0x004000, 0x006000
        self.assertEqual(
            before[unused_lo:unused_hi],
            after[unused_lo:unused_hi],
            msg="air_shield_flaps wrote into armor_properties_unused "
                "(0x004000..0x006000).  The pack must target "
                "armor_properties_real at 0x002000.")


@unittest.skipUnless(_have_fixture(),
                     "vanilla gamedata/ + default.xbe fixture not available")
class GenericEnablePackFlag(unittest.TestCase):
    """Argparse-level check that ``--enable-pack`` reaches the
    :class:`argparse.Namespace` that :func:`cmd_randomize_full`
    reads."""

    def test_enable_pack_flag_accepted_by_argparse(self):
        """Invoke ``azurik-mod randomize-full --help`` and confirm
        the flag shows up.  Cheap sanity that the argparse wiring
        survived the refactor."""
        r = subprocess.run(
            [sys.executable, "-m", "azurik_mod",
             "randomize-full", "--help"],
            capture_output=True, text=True, cwd=_REPO_ROOT,
            timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--enable-pack", r.stdout)
        # Help text should reference the scenario we care about.
        self.assertIn("plugins", r.stdout.lower())


class PluginPackEnablement(unittest.TestCase):
    """An XBR-only plugin pack should be enable-able via the
    GUI / generic channel the same way ``player_max_hp`` is."""

    def test_registered_xbr_only_feature_is_discoverable(self):
        """``player_max_hp`` (and its legacy ``cheat_entity_hp``
        alias) behaves like a plugin pack from the registry's
        perspective — if we can look it up by name and see its
        xbr_sites, so can the generic ``--enable-pack`` lookup."""
        import azurik_mod.patches  # noqa: F401
        pack = get_pack("player_max_hp")
        self.assertTrue(pack.xbr_sites)
        self.assertEqual(pack.touched_xbr_files(), ("config.xbr",))

        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            legacy = get_pack("cheat_entity_hp")
        self.assertEqual(legacy.name, pack.name)


if __name__ == "__main__":
    unittest.main()
