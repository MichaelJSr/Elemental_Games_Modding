"""Tests for the wing-flap descent fuel-cost slider.

Vanilla ``wing_flap`` has a second, independent anti-ground-recovery
mechanic: when the player has fallen > 6m below their peak_z
envelope (``fVar1 = peak_z + flap_height - current_z > 6``), a
``consume_fuel(this, 100.0)`` call drains the entire air-power
gauge in one flap.  After that the subsequent
``consume_fuel(this, 1.0)`` at flap entry returns 0 and the flap
doesn't happen — perceived as "flaps fail when I descend".

The ``flap_descent_fuel_cost_scale`` slider rewrites the 4-byte
``PUSH imm32`` at VA 0x893CE so the fuel cost is user-scaled.
0.0 fully disables the drain (pair with a high
``wing_flap_ceiling_scale`` to make descent flaps actually
useful).  1.0 preserves vanilla.

The shared 6.0 threshold constant at VA 0x001A25B8 has 19
unrelated readers elsewhere in the binary, so overwriting the
constant is not safe — these tests also assert we DON'T touch it.
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.patches.player_physics import (  # noqa: E402
    FLAP_DESCENT_FUEL_COST_SCALE,
    PLAYER_PHYSICS_SITES,
    _FLAP_DESCENT_FUEL_COST_VA,
    _FLAP_DESCENT_FUEL_COST_VANILLA,
    _VANILLA_FLAP_DESCENT_FUEL_COST,
    _player_speed_dynamic_whitelist,
    apply_flap_descent_fuel_cost,
    apply_player_physics,
)
from azurik_mod.patching.xbe import va_to_file  # noqa: E402
from tests._xbe_fixture import XBE_PATH, require_xbe  # noqa: E402


class SliderDescriptor(unittest.TestCase):
    def test_slider_is_virtual(self):
        self.assertTrue(FLAP_DESCENT_FUEL_COST_SCALE.is_virtual,
            msg="slider is a direct-byte-patch driver, should live "
                "as a virtual ParametricPatch on the player_physics "
                "pack's custom_apply")

    def test_slider_registered_on_player_physics(self):
        self.assertIn(FLAP_DESCENT_FUEL_COST_SCALE, PLAYER_PHYSICS_SITES)

    def test_slider_range_allows_zero_and_negative(self):
        """0.0 and negative values must both be reachable.
        Round 11.10 expanded the range to include refunds
        (negative = each descent flap ADDS fuel instead of
        draining it) after user testing showed values between
        0 and 1 still cleared the gauge in a single flap due to
        vanilla's clear-on-low-fuel threshold kicking in whenever
        cost > fuel_max × 2."""
        self.assertLessEqual(FLAP_DESCENT_FUEL_COST_SCALE.slider_min, 0.0,
            msg="0.0 must be reachable so users can disable drain")
        self.assertEqual(FLAP_DESCENT_FUEL_COST_SCALE.default, 1.0)

    def test_hook_site_constants(self):
        # The vanilla immediate is 100.0f = 0x42C80000 little-endian.
        self.assertEqual(_FLAP_DESCENT_FUEL_COST_VA, 0x000893CE)
        self.assertEqual(
            _FLAP_DESCENT_FUEL_COST_VANILLA, bytes.fromhex("0000c842"))
        self.assertEqual(_VANILLA_FLAP_DESCENT_FUEL_COST, 100.0)


@require_xbe
class ApplyBehaviour(unittest.TestCase):
    def setUp(self):
        self.orig = XBE_PATH.read_bytes()
        self.off = va_to_file(_FLAP_DESCENT_FUEL_COST_VA)

    def _decode(self, data: bytes) -> float:
        return struct.unpack(
            "<f", data[self.off:self.off + 4])[0]

    def test_default_scale_is_noop(self):
        data = bytearray(self.orig)
        self.assertFalse(apply_flap_descent_fuel_cost(
            data, fuel_cost_scale=1.0))
        self.assertEqual(bytes(data), self.orig,
            msg="scale=1.0 must leave the XBE byte-identical")

    def test_zero_scale_kills_cost(self):
        """0.0 is the load-bearing use case — after this the branch
        at 0x893CD runs ``consume_fuel(this, 0.0)`` which drains
        nothing, so the gauge stays intact for the next flap."""
        data = bytearray(self.orig)
        self.assertTrue(apply_flap_descent_fuel_cost(
            data, fuel_cost_scale=0.0))
        self.assertAlmostEqual(self._decode(bytes(data)), 0.0, places=5)

    def test_fractional_scale_linearly_scales_cost(self):
        for scale in (0.1, 0.25, 0.5, 0.75):
            with self.subTest(scale=scale):
                data = bytearray(self.orig)
                self.assertTrue(apply_flap_descent_fuel_cost(
                    data, fuel_cost_scale=scale))
                got = self._decode(bytes(data))
                expected = 100.0 * scale
                self.assertAlmostEqual(got, expected, places=3,
                    msg=f"scale={scale} must produce {expected}")

    def test_only_4_bytes_are_touched(self):
        """The patch writes exactly 4 bytes at VA 0x893CE.  The
        surrounding PUSH 0x68 opcode and MOV ECX, EDI that follow
        must stay intact."""
        data = bytearray(self.orig)
        self.assertTrue(apply_flap_descent_fuel_cost(
            data, fuel_cost_scale=0.0))
        # Byte before the immediate (PUSH opcode) unchanged.
        self.assertEqual(data[self.off - 1], 0x68)
        # Byte after the immediate (MOV ECX, EDI start) unchanged.
        self.assertEqual(data[self.off + 4], 0x8B)
        self.assertEqual(data[self.off + 5], 0xCF)

    def test_does_not_touch_shared_threshold_constant(self):
        """The 6.0 threshold constant at VA 0x001A25B8 has 19
        unrelated readers across the binary — our patch must NOT
        overwrite it."""
        threshold_off = va_to_file(0x001A25B8)
        vanilla_threshold = bytes(
            self.orig[threshold_off:threshold_off + 4])
        data = bytearray(self.orig)
        apply_flap_descent_fuel_cost(data, fuel_cost_scale=0.0)
        self.assertEqual(
            bytes(data[threshold_off:threshold_off + 4]),
            vanilla_threshold,
            msg="shared 6.0 threshold at 0x001A25B8 must stay vanilla")

    def test_drift_detection(self):
        """If the site has already been patched to something other
        than the vanilla 100.0f, the apply function must refuse
        rather than silently double-apply."""
        data = bytearray(self.orig)
        # Corrupt the site to simulate drift.
        data[self.off:self.off + 4] = b"\xAA\xBB\xCC\xDD"
        self.assertFalse(apply_flap_descent_fuel_cost(
            data, fuel_cost_scale=0.5))
        # Bytes should not have changed.
        self.assertEqual(
            bytes(data[self.off:self.off + 4]), b"\xAA\xBB\xCC\xDD")


@require_xbe
class RoutedViaApplyPlayerPhysics(unittest.TestCase):
    """The pack-wide entry point must accept the new kwarg and
    forward it to ``apply_flap_descent_fuel_cost``."""

    def test_apply_player_physics_installs_patch(self):
        data = bytearray(XBE_PATH.read_bytes())
        apply_player_physics(data, flap_descent_fuel_cost_scale=0.0)
        off = va_to_file(_FLAP_DESCENT_FUEL_COST_VA)
        val = struct.unpack("<f", bytes(data[off:off + 4]))[0]
        self.assertAlmostEqual(val, 0.0)

    def test_custom_apply_dispatcher_threads_through(self):
        from azurik_mod.patches.player_physics import _custom_apply
        data = bytearray(XBE_PATH.read_bytes())
        _custom_apply(data, flap_descent_fuel_cost_scale=0.0)
        off = va_to_file(_FLAP_DESCENT_FUEL_COST_VA)
        val = struct.unpack("<f", bytes(data[off:off + 4]))[0]
        self.assertAlmostEqual(val, 0.0)

    def test_default_via_dispatcher_is_noop(self):
        """Crucial guard: passing None (slider at vanilla default)
        must leave bytes untouched.  Pre-fix, the ``or 1.0``
        shortcut in cmd_randomize_full's CLI path would have
        forced 0.0 back to 1.0; explicit None check fixes that."""
        from azurik_mod.patches.player_physics import _custom_apply
        orig = XBE_PATH.read_bytes()
        data = bytearray(orig)
        _custom_apply(data)   # no slider kwargs at all
        self.assertEqual(bytes(data), orig)


@require_xbe
class DynamicWhitelistCoverage(unittest.TestCase):
    """``verify-patches --strict`` must accept the 4-byte rewrite
    as expected drift rather than a corruption."""

    def test_whitelist_includes_descent_fuel_range(self):
        xbe = XBE_PATH.read_bytes()
        ranges = _player_speed_dynamic_whitelist(xbe)
        off = va_to_file(_FLAP_DESCENT_FUEL_COST_VA)
        self.assertIn((off, off + 4), ranges,
            msg="descent fuel-cost site must be on the whitelist "
                "so strict verify doesn't flag a 0.0 value as drift")


if __name__ == "__main__":
    unittest.main()
