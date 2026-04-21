"""Regression tests for the reference XBR-side feature.

``cheat_entity_hp`` is the smallest possible declarative XBR pack
— one slider edits garret4's hitPoints.  If it breaks, something
in the Phase-3 wiring has drifted.  Pin it so the reference stays
trustworthy as both an example in docs/XBR_PACKS.md AND an
integration canary for ``apply_pack(xbr_files=...)``.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
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


class CheatEntityHpRegistration(unittest.TestCase):
    """The reference feature must register itself on import."""

    def test_feature_is_registered(self):
        # Triggers the side-effectful import if it hasn't happened.
        import azurik_mod.patches  # noqa: F401
        pack = get_pack("cheat_entity_hp")
        self.assertEqual(pack.category, "player")
        self.assertEqual(len(pack.xbr_sites), 1)
        site = pack.xbr_sites[0]
        self.assertIsInstance(site, XbrParametricEdit)
        self.assertEqual(site.xbr_file, "config.xbr")
        self.assertEqual(site.section, "critters_critter_data")
        self.assertEqual(site.entity, "garret4")
        self.assertEqual(site.prop, "hitPoints")

    def test_touched_xbr_files_surfaces_config_xbr(self):
        import azurik_mod.patches  # noqa: F401
        pack = get_pack("cheat_entity_hp")
        self.assertEqual(pack.touched_xbr_files(), ("config.xbr",))

    def test_parameters_exposes_slider_name(self):
        import azurik_mod.patches  # noqa: F401
        pack = get_pack("cheat_entity_hp")
        self.assertIn("garret4_hit_points", pack.parameters)


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class CheatEntityHpApply(unittest.TestCase):
    """Exercise the feature end-to-end through :func:`apply_pack`."""

    def setUp(self):
        import azurik_mod.patches  # noqa: F401
        self._tmpdir = Path(tempfile.mkdtemp(prefix="cheat_hp_"))
        (self._tmpdir / "gamedata").mkdir()
        shutil.copy2(_GAMEDATA / "config.xbr",
                     self._tmpdir / "gamedata" / "config.xbr")
        self.pack = get_pack("cheat_entity_hp")

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
        # Default = 100.0 per the feature declaration.
        self.assertEqual(
            doc.keyed_sections()["critters_critter_data"]
               .find_cell("garret4", "hitPoints").double_value,
            100.0)

    def test_out_of_range_value_raises(self):
        staging = XbrStaging(self._tmpdir)
        # slider_max is 9999 per the feature declaration.
        with self.assertRaises(ValueError):
            apply_pack(self.pack, bytearray(0x1000),
                       params={"garret4_hit_points": 1e9},
                       xbr_files=staging)


if __name__ == "__main__":
    unittest.main()
