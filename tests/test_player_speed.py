"""Tests for the Phase-2 player-speed patch against real config.xbr.

The patch lives inside the `attacks_transitions` keyed table of
config.xbr — characters.xbr does not contain `walkSpeed` / `runSpeed`
strings at all; that part of the original plan was based on an
overestimate.  These tests operate on a real config.xbr when it is
available, otherwise they skip gracefully so CI without game assets
still passes.
"""

from __future__ import annotations

import os
import struct
import sys
import unittest
from pathlib import Path

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from azurik_mod.config.keyed_tables import (  # noqa: E402
    load_table_from_bytes,
    set_cell_double,
)
from azurik_mod.patches.player_physics import (  # noqa: E402
    ATTACKS_TRANSITIONS_OFFSET,
    apply_player_speed,
)


# Find a real config.xbr we can use as a fixture (optional dependency).
_CONFIG_CANDIDATES = [
    Path("/Users/michaelsrouji/Documents/Xemu/tools/"
         "Azurik - Rise of Perathia (USA).xiso/gamedata/config.xbr"),
    Path(_REPO_ROOT).parent / "Azurik - Rise of Perathia (USA).xiso" /
        "gamedata" / "config.xbr",
    Path(_REPO_ROOT) / "tests" / "fixtures" / "config.xbr",
]
_CONFIG_XBR = next((p for p in _CONFIG_CANDIDATES if p.exists()), None)


@unittest.skipUnless(_CONFIG_XBR, "real config.xbr not available")
class KeyedTableWriterRoundtrip(unittest.TestCase):
    """set_cell_double write/read-back must be lossless."""

    def setUp(self):
        self.data = bytearray(_CONFIG_XBR.read_bytes())
        self.table = load_table_from_bytes(
            bytes(self.data), ATTACKS_TRANSITIONS_OFFSET,
            "attacks_transitions")

    def test_roundtrip_double(self):
        cell = self.table.get_value("garret4", "walkSpeed")
        self.assertIsNotNone(cell)
        typ, val, off = cell
        self.assertEqual(typ, "double")
        self.assertAlmostEqual(val, 5.0, places=6)

        set_cell_double(self.data, off, 13.25)
        got = struct.unpack_from("<d", self.data, off + 8)[0]
        self.assertAlmostEqual(got, 13.25, places=12)

    def test_rejects_non_double_cell(self):
        # Row 0 ("name") is a string cell for garret4 — set_cell_double
        # must refuse to clobber it.
        name = self.table.get_value("garret4", "name")
        self.assertIsNotNone(name)
        typ, _val, off = name
        self.assertEqual(typ, "string")
        with self.assertRaises(ValueError):
            set_cell_double(self.data, off, 1.0)


@unittest.skipUnless(_CONFIG_XBR, "real config.xbr not available")
class ApplyPlayerSpeed(unittest.TestCase):
    """The high-level apply_player_speed helper."""

    def setUp(self):
        self.orig = _CONFIG_XBR.read_bytes()

    def test_no_op_at_1_0(self):
        data = bytearray(self.orig)
        self.assertFalse(apply_player_speed(data))
        self.assertEqual(bytes(data), self.orig)

    def test_walk_scale_2_0(self):
        data = bytearray(self.orig)
        ok = apply_player_speed(data, walk_scale=2.0)
        self.assertTrue(ok)

        table = load_table_from_bytes(
            bytes(data), ATTACKS_TRANSITIONS_OFFSET, "attacks_transitions")
        walk = table.get_value("garret4", "walkSpeed")
        run = table.get_value("garret4", "runSpeed")
        self.assertAlmostEqual(walk[1], 10.0, places=6)
        # run unchanged
        self.assertAlmostEqual(run[1], 7.0, places=6)

    def test_walk_and_run_scales(self):
        data = bytearray(self.orig)
        ok = apply_player_speed(data, walk_scale=0.5, run_scale=1.5)
        self.assertTrue(ok)
        table = load_table_from_bytes(
            bytes(data), ATTACKS_TRANSITIONS_OFFSET, "attacks_transitions")
        walk = table.get_value("garret4", "walkSpeed")
        run = table.get_value("garret4", "runSpeed")
        self.assertAlmostEqual(walk[1], 2.5, places=6)
        self.assertAlmostEqual(run[1], 10.5, places=6)

    def test_only_two_cells_change(self):
        """At 2x/0.5x exactly two doubles should change; verify no stray
        bytes move (the file's size and everything else stay identical)."""
        data = bytearray(self.orig)
        apply_player_speed(data, walk_scale=2.0, run_scale=0.5)
        self.assertEqual(len(data), len(self.orig))
        diffs = [i for i in range(len(self.orig)) if data[i] != self.orig[i]]
        # Both values differ by small byte counts — clamp to the two
        # 8-byte cell payloads at most.
        self.assertLessEqual(len(diffs), 16)
        self.assertTrue(all(
            0x00CED4 <= d < 0x00CEDC or 0x00CEF4 <= d < 0x00CEFC
            for d in diffs
        ), msg=f"diffs bled outside walkSpeed/runSpeed cells: {diffs}")

    def test_applied_relative_to_current_buffer(self):
        """apply_player_speed multiplies the CURRENT buffer value, so a
        second call compounds.  Document this with a test."""
        data = bytearray(self.orig)
        apply_player_speed(data, walk_scale=2.0)  # 5 -> 10
        apply_player_speed(data, walk_scale=2.0)  # 10 -> 20
        table = load_table_from_bytes(
            bytes(data), ATTACKS_TRANSITIONS_OFFSET, "attacks_transitions")
        walk = table.get_value("garret4", "walkSpeed")
        self.assertAlmostEqual(walk[1], 20.0, places=6)

    def test_idempotent_from_original(self):
        """Re-applying the same scale from the ORIGINAL baseline stays
        at the single-scale value — this is the randomize-full flow."""
        a = bytearray(self.orig)
        b = bytearray(self.orig)
        apply_player_speed(a, walk_scale=2.0)
        apply_player_speed(b, walk_scale=2.0)
        self.assertEqual(bytes(a), bytes(b))


class GracefulHandlingOfGarbage(unittest.TestCase):
    """apply_player_speed must fail soft on malformed input."""

    def test_garbage_returns_false(self):
        data = bytearray(b"\x00" * 0x100000)
        self.assertFalse(apply_player_speed(data, walk_scale=2.0))


if __name__ == "__main__":
    unittest.main()
