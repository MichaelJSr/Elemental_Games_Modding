"""Tests for the player_physics parametric pack (Phase 1 — gravity)."""

from __future__ import annotations

import os
import struct
import sys
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from azurik_mod.patches.player_physics import (  # noqa: E402
    AIR_CONTROL_SCALE,
    CLIMB_SPEED_SCALE,
    FLAP_HEIGHT_SCALE,
    FLAP_SUBSEQUENT_SCALE,
    GRAVITY_BASELINE,
    GRAVITY_PATCH,
    JUMP_SPEED_SCALE,
    ROLL_SPEED_SCALE,
    RUN_SPEED_SCALE,   # back-compat alias -> ROLL_SPEED_SCALE
    SWIM_SPEED_SCALE,
    WALK_SPEED_SCALE,
    apply_player_physics,
)
from azurik_mod.patching import (  # noqa: E402
    apply_parametric_patch,
    read_parametric_value,
    verify_parametric_patch,
)


class GravityPatchShape(unittest.TestCase):
    """ParametricPatch descriptor invariants for gravity."""

    def test_baseline_bytes_match_9_8(self):
        self.assertEqual(GRAVITY_PATCH.original,
                         struct.pack("<f", 9.8))
        self.assertEqual(GRAVITY_PATCH.default, 9.8)
        self.assertEqual(GRAVITY_BASELINE, 9.8)

    def test_encode_decode_roundtrips(self):
        for g in (0.98, 2.45, 4.9, 9.8, 14.7, 19.6, 29.4):
            encoded = GRAVITY_PATCH.encode(g)
            self.assertEqual(len(encoded), GRAVITY_PATCH.size)
            decoded = GRAVITY_PATCH.decode(encoded)
            self.assertAlmostEqual(decoded, g, places=3)

    def test_slider_range_spans_weightless_to_extreme(self):
        """Wide enough for weightless (0.0) through crushing 10x Earth."""
        self.assertAlmostEqual(GRAVITY_PATCH.slider_min, 0.0, places=3)
        self.assertAlmostEqual(GRAVITY_PATCH.slider_max, 100.0, places=3)


class ApplyVerifyRoundtrip(unittest.TestCase):
    """Happy path: apply a value, read it back via verify/read helpers."""

    def _make_xbe(self) -> bytearray:
        """bytearray large enough to hold the gravity cell at its file offset."""
        off = GRAVITY_PATCH.file_offset
        buf = bytearray(off + 8)
        buf[off:off + 4] = GRAVITY_PATCH.original
        return buf

    def test_default_state_is_default(self):
        buf = self._make_xbe()
        self.assertEqual(verify_parametric_patch(buf, GRAVITY_PATCH), "default")
        self.assertAlmostEqual(
            read_parametric_value(buf, GRAVITY_PATCH), 9.8, places=3)

    def test_apply_then_verify_custom(self):
        buf = self._make_xbe()
        self.assertTrue(apply_parametric_patch(buf, GRAVITY_PATCH, 4.9))
        self.assertEqual(verify_parametric_patch(buf, GRAVITY_PATCH), "custom")
        self.assertAlmostEqual(
            read_parametric_value(buf, GRAVITY_PATCH), 4.9, places=3)

    def test_apply_default_matches_original_bytes(self):
        buf = self._make_xbe()
        buf[GRAVITY_PATCH.file_offset:GRAVITY_PATCH.file_offset + 4] = b"\x00\x00\x00\x00"
        self.assertTrue(apply_parametric_patch(buf, GRAVITY_PATCH, 9.8))
        off = GRAVITY_PATCH.file_offset
        self.assertEqual(bytes(buf[off:off + 4]), GRAVITY_PATCH.original)
        self.assertEqual(verify_parametric_patch(buf, GRAVITY_PATCH), "default")


class ApplyRejectsOutOfRange(unittest.TestCase):
    def setUp(self):
        off = GRAVITY_PATCH.file_offset
        self.buf = bytearray(off + 8)
        self.buf[off:off + 4] = GRAVITY_PATCH.original

    def test_below_minimum_refused(self):
        # Range now starts at 0.0, so anything negative must still be rejected.
        self.assertFalse(apply_parametric_patch(self.buf, GRAVITY_PATCH, -1.0))
        off = GRAVITY_PATCH.file_offset
        self.assertEqual(bytes(self.buf[off:off + 4]), GRAVITY_PATCH.original)

    def test_above_maximum_refused(self):
        self.assertFalse(apply_parametric_patch(self.buf, GRAVITY_PATCH, 200.0))
        off = GRAVITY_PATCH.file_offset
        self.assertEqual(bytes(self.buf[off:off + 4]), GRAVITY_PATCH.original)


class VirtualSlidersAreHandled(unittest.TestCase):
    """Walk / roll / swim speed sliders are virtual — apply_parametric_patch
    must no-op, and verify returns 'virtual'."""

    def test_walk_scale_is_virtual(self):
        self.assertTrue(WALK_SPEED_SCALE.is_virtual)
        self.assertTrue(ROLL_SPEED_SCALE.is_virtual)
        self.assertTrue(SWIM_SPEED_SCALE.is_virtual)
        self.assertTrue(JUMP_SPEED_SCALE.is_virtual)
        self.assertTrue(AIR_CONTROL_SCALE.is_virtual)
        self.assertTrue(FLAP_HEIGHT_SCALE.is_virtual)
        self.assertTrue(FLAP_SUBSEQUENT_SCALE.is_virtual)
        self.assertTrue(CLIMB_SPEED_SCALE.is_virtual)
        # Back-compat alias points at the same descriptor as roll.
        self.assertIs(RUN_SPEED_SCALE, ROLL_SPEED_SCALE)

    def test_apply_on_virtual_is_noop_success(self):
        buf = bytearray(8)
        self.assertTrue(apply_parametric_patch(buf, WALK_SPEED_SCALE, 2.0))
        self.assertEqual(buf, bytearray(8))  # untouched

    def test_verify_virtual_reports_virtual(self):
        buf = bytearray(8)
        self.assertEqual(verify_parametric_patch(buf, WALK_SPEED_SCALE),
                         "virtual")


class ApplyPlayerPhysicsKwargs(unittest.TestCase):
    """The high-level apply_player_physics helper routes kwargs correctly."""

    def _make_xbe(self) -> bytearray:
        off = GRAVITY_PATCH.file_offset
        buf = bytearray(off + 8)
        buf[off:off + 4] = GRAVITY_PATCH.original
        return buf

    def test_gravity_none_leaves_baseline(self):
        buf = self._make_xbe()
        apply_player_physics(buf)
        self.assertEqual(verify_parametric_patch(buf, GRAVITY_PATCH),
                         "default")

    def test_gravity_4_9_applies(self):
        buf = self._make_xbe()
        apply_player_physics(buf, gravity=4.9)
        self.assertAlmostEqual(
            read_parametric_value(buf, GRAVITY_PATCH), 4.9, places=3)

    def test_speed_kwargs_do_not_touch_gravity_cell(self):
        """Phase 2 C1 made walk / roll / swim scales XBE-targeting,
        so apply_player_physics now mutates default.xbe for all
        four sliders.  What it must NOT do is mutate the gravity
        site as a side effect of a speed-only call."""
        buf = self._make_xbe()
        apply_player_physics(buf, walk_scale=2.0, roll_scale=2.0,
                             swim_scale=2.0)
        self.assertEqual(verify_parametric_patch(buf, GRAVITY_PATCH),
                         "default",
            msg="speed-only apply must not rewrite the gravity "
                "ParametricPatch's bytes.")

    def test_legacy_run_scale_still_accepted(self):
        """Back-compat: apply_player_physics(run_scale=...) must
        not crash — it's routed to roll_scale internally."""
        buf = self._make_xbe()
        apply_player_physics(buf, run_scale=2.0)  # legacy kwarg
        self.assertEqual(verify_parametric_patch(buf, GRAVITY_PATCH),
                         "default")


if __name__ == "__main__":
    unittest.main()
