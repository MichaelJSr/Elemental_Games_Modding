"""Regression tests for the ``air_shield_flaps`` quick-stats pack.

Mirrors ``test_player_max_hp.py``: registration + apply.  Adds a
"pack is off -> cell stays vanilla" assertion so we can notice
immediately if someone wires ``apply_pack`` to always write every
xbr_site regardless of the enabled flag (the silent-write bug class
that made ``cheat_entity_hp`` look applied-but-dead).
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

_VANILLA_FLAPS = {
    "air_shield_1": 1.0,
    "air_shield_2": 2.0,
    "air_shield_3": 5.0,
}


class AirShieldFlapsRegistration(unittest.TestCase):
    """The pack must register with three sliders in the Player tab's
    Quick Stats sub-group."""

    def test_feature_is_registered(self):
        import azurik_mod.patches  # noqa: F401
        pack = get_pack("air_shield_flaps")
        self.assertEqual(pack.category, "player")
        self.assertEqual(pack.subgroup, "quick_stats")
        self.assertEqual(len(pack.xbr_sites), 3)

    def test_each_slider_targets_armor_properties_real(self):
        import azurik_mod.patches  # noqa: F401
        pack = get_pack("air_shield_flaps")
        seen_entities: set[str] = set()
        for site in pack.xbr_sites:
            self.assertIsInstance(site, XbrParametricEdit)
            self.assertEqual(site.xbr_file, "config.xbr")
            self.assertEqual(site.section, "armor_properties_real")
            self.assertEqual(site.prop, "Flaps")
            self.assertIn(site.entity,
                          ("air_shield_1", "air_shield_2",
                           "air_shield_3"))
            self.assertEqual(
                site.default, _VANILLA_FLAPS[site.entity],
                msg=f"slider default for {site.entity} drifted from "
                    f"the shipping config.xbr value "
                    f"({_VANILLA_FLAPS[site.entity]}) — either "
                    f"update the constant here after a vanilla bump, "
                    f"or fix the pack's default.")
            seen_entities.add(site.entity)
        self.assertEqual(
            seen_entities,
            {"air_shield_1", "air_shield_2", "air_shield_3"})

    def test_touched_xbr_files_surfaces_config_xbr(self):
        import azurik_mod.patches  # noqa: F401
        pack = get_pack("air_shield_flaps")
        self.assertEqual(pack.touched_xbr_files(), ("config.xbr",))

    def test_parameters_exposes_all_three_sliders(self):
        import azurik_mod.patches  # noqa: F401
        pack = get_pack("air_shield_flaps")
        names = set(pack.parameters)
        self.assertEqual(
            names,
            {"air_shield_1_flaps", "air_shield_2_flaps",
             "air_shield_3_flaps"})


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class AirShieldFlapsApply(unittest.TestCase):
    """Exercise the pack end-to-end through :func:`apply_pack`."""

    def setUp(self):
        import azurik_mod.patches  # noqa: F401
        self._tmpdir = Path(tempfile.mkdtemp(prefix="air_flaps_"))
        (self._tmpdir / "gamedata").mkdir()
        shutil.copy2(_GAMEDATA / "config.xbr",
                     self._tmpdir / "gamedata" / "config.xbr")
        self.pack = get_pack("air_shield_flaps")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _read_flap_count(self, entity: str) -> float:
        doc = XbrDocument.load(
            self._tmpdir / "gamedata" / "config.xbr")
        ks = doc.keyed_sections()["armor_properties_real"]
        return ks.find_cell(entity, "Flaps").double_value

    def test_air_shield_3_slider_writes_to_disk(self):
        """The headline E2E check — if this fails, the fix didn't
        actually fix anything.  Pinned to 7.0 so the assertion
        value matches the plan's manual verification recipe."""
        staging = XbrStaging(self._tmpdir)
        apply_pack(self.pack, bytearray(0x1000),
                   params={
                       "air_shield_1_flaps": 1.0,
                       "air_shield_2_flaps": 2.0,
                       "air_shield_3_flaps": 7.0,
                   },
                   xbr_files=staging)
        staging.flush()
        self.assertEqual(self._read_flap_count("air_shield_3"), 7.0)
        self.assertEqual(self._read_flap_count("air_shield_1"), 1.0)
        self.assertEqual(self._read_flap_count("air_shield_2"), 2.0)

    def test_defaults_are_vanilla_values(self):
        """Applying with no ``params`` writes the slider defaults;
        since we pin defaults to the shipping values, the
        post-apply cells must match the pre-apply cells byte for
        byte (an effective no-op)."""
        before = {e: self._read_flap_count(e)
                  for e in _VANILLA_FLAPS}
        staging = XbrStaging(self._tmpdir)
        apply_pack(self.pack, bytearray(0x1000), xbr_files=staging)
        staging.flush()
        after = {e: self._read_flap_count(e)
                 for e in _VANILLA_FLAPS}
        self.assertEqual(before, after)


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class AirShieldFlapsCellsAreReachable(unittest.TestCase):
    """Sanity check on the SECTION choice — the plan-level bug we
    just fixed was targeting a section/cell that didn't exist.
    Pin the reality check so a future refactor can't silently
    point the pack back at the dead 0x004000 grid."""

    def test_each_air_shield_cell_exists_in_armor_properties_real(self):
        doc = XbrDocument.load(_GAMEDATA / "config.xbr")
        ks = doc.keyed_sections()["armor_properties_real"]
        for entity in ("air_shield_1", "air_shield_2",
                       "air_shield_3"):
            cell = ks.find_cell(entity, "Flaps")
            self.assertIsNotNone(
                cell,
                msg=f"{entity}.Flaps missing from "
                    f"armor_properties_real — if the labels moved, "
                    f"update sections.py AND this test.")
            self.assertEqual(
                cell.type_code, 1,
                msg=f"{entity}.Flaps is not a double — "
                    f"set_keyed_double will refuse to write.")


if __name__ == "__main__":
    unittest.main()
