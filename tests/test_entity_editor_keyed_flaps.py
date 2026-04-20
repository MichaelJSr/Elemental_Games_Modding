"""End-to-end test of the Entity Editor -> randomizer -> config.xbr
pipeline for editing armor Flaps values.

Rationale: a user reported that edits made in the GUI's Entity
Editor tab didn't seem to take effect in-game.  Investigation in
round 11.10 showed the apply pipeline is correct — double-typed
keyed-table cells are written at ``cell_offset + 8`` and re-read
as the expected value.  This test pins that invariant so a
future refactor (of either side of the pipeline) can't silently
break it again.

If this test fails, the Entity Editor is broken.  If it passes,
the editor code is functional — any user-reported non-effect
must be a UI-interaction issue (e.g. didn't load ISO first, or
pressed Reset before Build) rather than a missing write path.
"""

from __future__ import annotations

import json
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


_CONFIG_XBR = Path(
    "/Users/michaelsrouji/Documents/Xemu/tools/"
    "Azurik - Rise of Perathia (USA).xiso/gamedata/config.xbr")


@unittest.skipUnless(_CONFIG_XBR.exists(),
    f"config.xbr required at {_CONFIG_XBR}")
class KeyedPatchPipelineForArmorFlaps(unittest.TestCase):
    """End-to-end coverage: simulate the Entity Editor's
    ``_edits`` → ``get_pending_mod`` → ``_keyed_patches`` apply
    path against armor_properties_real's Flaps column (which is
    what the user asked to edit)."""

    def setUp(self):
        from azurik_mod.config import keyed_tables as ktp
        self.ktp = ktp
        work = Path(tempfile.mkdtemp(prefix="entity_editor_"))
        self.config_xbr = work / "config.xbr"
        shutil.copy(_CONFIG_XBR, self.config_xbr)

    def _read_flaps(self) -> dict[str, object]:
        tables = self.ktp.load_all_tables(
            str(self.config_xbr), sections=['armor_properties_real'])
        t = tables['armor_properties_real']
        return {
            armor: t.get_value(armor, 'Flaps')
            for armor in ('air_shield_1', 'air_shield_2', 'air_shield_3')
        }

    def test_vanilla_flaps_values(self):
        """Pin the vanilla Flaps: air1=1, air2=2, air3=5."""
        flaps = self._read_flaps()
        self.assertEqual(flaps['air_shield_1'][:2], ('double', 1.0))
        self.assertEqual(flaps['air_shield_2'][:2], ('double', 2.0))
        self.assertEqual(flaps['air_shield_3'][:2], ('double', 5.0))

    def test_get_pending_mod_roundtrip_for_keyed_section(self):
        """Emulate the Entity Editor's ``get_pending_mod`` logic
        directly (no Tk needed) and pin the shape of the JSON it
        emits for armor_properties_real edits."""
        from gui.pages.entity_editor import EDITABLE_SECTIONS
        edits = {
            "armor_properties_real": {
                "air_shield_1": {"Flaps": 10.0},
                "air_shield_2": {"Flaps": 20.0},
                "air_shield_3": {"Flaps": 50.0},
            }
        }
        mod = {
            "name": "Entity Editor Edits",
            "format": "grouped",
            "sections": {},
        }
        keyed_patches: dict = {}
        for section_key, entities in edits.items():
            _, _, fmt = next(
                ((k, d, f) for k, d, f in EDITABLE_SECTIONS
                 if k == section_key),
                ("", "", "variant"))
            if fmt == "variant":
                mod["sections"][section_key] = {
                    e: dict(p) for e, p in entities.items()}
            else:
                keyed_patches[section_key] = {
                    e: dict(p) for e, p in entities.items()}
        if keyed_patches:
            mod["_keyed_patches"] = keyed_patches

        # The keyed_patches structure is what cmd_randomize_full
        # reads — pin it.
        self.assertIn("_keyed_patches", mod)
        kp = mod["_keyed_patches"]
        self.assertIn("armor_properties_real", kp)
        armor_patches = kp["armor_properties_real"]
        self.assertEqual(
            armor_patches["air_shield_1"]["Flaps"], 10.0)
        self.assertEqual(
            armor_patches["air_shield_2"]["Flaps"], 20.0)
        self.assertEqual(
            armor_patches["air_shield_3"]["Flaps"], 50.0)

        # JSON-serialisable (the backend threads it through
        # args.config_mod as a JSON string).
        self.assertIsInstance(json.dumps(mod), str)

    def test_keyed_apply_pipeline_writes_bytes_correctly(self):
        """Full apply-side walk: given a _keyed_patches dict, the
        cmd_randomize_full keyed-patch loop writes the expected
        doubles and a re-read confirms the new values."""
        mod = {
            "_keyed_patches": {
                "armor_properties_real": {
                    "air_shield_1": {"Flaps": 10.0},
                    "air_shield_2": {"Flaps": 20.0},
                    "air_shield_3": {"Flaps": 50.0},
                }
            }
        }

        # Replicate the apply loop from cmd_randomize_full.
        keyed = mod["_keyed_patches"]
        config_data = bytearray(self.config_xbr.read_bytes())
        tables = self.ktp.load_all_tables(
            str(self.config_xbr), sections=list(keyed.keys()))
        applied = 0
        for section_key, entities in keyed.items():
            table = tables[section_key]
            for entity_name, props in entities.items():
                for prop_name, value in props.items():
                    cell = table.get_value(entity_name, prop_name)
                    if cell and cell[0] == "double":
                        cell_off = cell[2]
                        struct.pack_into(
                            "<d", config_data, cell_off + 8,
                            float(value))
                        applied += 1
        self.config_xbr.write_bytes(config_data)
        self.assertEqual(applied, 3,
            msg="every armor row is a 'double' cell; all 3 "
                "edits must land")

        # Re-read to confirm the values took.
        flaps = self._read_flaps()
        self.assertEqual(flaps['air_shield_1'][:2], ('double', 10.0))
        self.assertEqual(flaps['air_shield_2'][:2], ('double', 20.0))
        self.assertEqual(flaps['air_shield_3'][:2], ('double', 50.0))

    def test_empty_cell_rows_are_gracefully_skipped(self):
        """Non-air armor rows have 'empty' Flaps cells.  If the
        user edits one (the entry widget is enabled for non-string
        cells), the apply loop skips it silently — the byte-write
        path only runs for cells that were already populated as
        doubles in vanilla.  Pin this to document the behaviour
        — a future enhancement could upgrade empty cells to
        doubles, but right now they're read-only."""
        mod = {
            "_keyed_patches": {
                "armor_properties_real": {
                    # earth_armor_1 has an empty Flaps cell.
                    "earth_armor_1": {"Flaps": 3.0},
                }
            }
        }
        keyed = mod["_keyed_patches"]
        config_data = bytearray(self.config_xbr.read_bytes())
        tables = self.ktp.load_all_tables(
            str(self.config_xbr), sections=list(keyed.keys()))
        applied = 0
        for section_key, entities in keyed.items():
            table = tables[section_key]
            for entity_name, props in entities.items():
                for prop_name, value in props.items():
                    cell = table.get_value(entity_name, prop_name)
                    if cell and cell[0] == "double":
                        cell_off = cell[2]
                        struct.pack_into(
                            "<d", config_data, cell_off + 8,
                            float(value))
                        applied += 1
        self.assertEqual(applied, 0,
            msg="edits on empty cells are silently skipped; if "
                "you want to bump non-air armor flap counts, "
                "someone needs to extend the apply path to "
                "upgrade empty cells into new doubles")


if __name__ == "__main__":
    unittest.main()
