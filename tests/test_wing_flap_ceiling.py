"""Tests for the wing-flap altitude-ceiling shim slider.

Option A from the round-11 design discussion: scale the peak_z
latch at VA 0x89154 (inside ``player_jump_init``) so the wing-flap
altitude cap becomes ``entity.z + K * flap_height`` instead of
``entity.z + flap_height`` — orthogonal to per-flap impulse
sliders.

The shim is 15 bytes of hand-assembled x87 + RET, installed via
``shim_builder`` at a 5-byte CALL trampoline + 1 NOP pad.  Tests
cover:

- Slider-descriptor shape (virtual ParametricPatch registered
  on ``player_physics`` PLAYER_PHYSICS_SITES).
- Apply: no-op at scale 1.0; installs trampoline at scale != 1.0.
- Idempotent re-apply is detected, not double-installed.
- Shim body has the expected FLD/FMUL/FADDP/RET shape.
- The injected scale float lands in a mapped section at the
  abs32 referenced by the FMUL operand.
- End-to-end routing via ``apply_player_physics`` +
  ``_custom_apply`` (the dispatcher hook used by ``apply_pack``).
- The dynamic whitelist picks up the hook + body + data ranges.
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
    PLAYER_PHYSICS_SITES,
    WING_FLAP_CEILING_SCALE,
    _PEAK_Z_HOOK_VA,
    _PEAK_Z_HOOK_VANILLA,
    _PEAK_Z_SHIM_BODY_SIZE,
    _player_speed_dynamic_whitelist,
    apply_player_physics,
    apply_wing_flap_ceiling,
)
from azurik_mod.patching.xbe import (  # noqa: E402
    parse_xbe_sections,
    resolve_va_to_file,
    va_to_file,
)
from tests._xbe_fixture import XBE_PATH, require_xbe  # noqa: E402


# ---------------------------------------------------------------------------
# Unit / descriptor checks — don't need the XBE fixture.
# ---------------------------------------------------------------------------

class SliderDescriptor(unittest.TestCase):
    def test_slider_is_virtual(self):
        self.assertTrue(WING_FLAP_CEILING_SCALE.is_virtual,
            msg="wing_flap_ceiling_scale is shim-backed — must be "
                "a virtual ParametricPatch (va=0, size=0)")

    def test_slider_name_and_defaults(self):
        self.assertEqual(WING_FLAP_CEILING_SCALE.name,
                         "wing_flap_ceiling_scale")
        self.assertEqual(WING_FLAP_CEILING_SCALE.default, 1.0)
        self.assertEqual(WING_FLAP_CEILING_SCALE.unit, "x")

    def test_slider_registered_in_player_physics_sites(self):
        self.assertIn(WING_FLAP_CEILING_SCALE, PLAYER_PHYSICS_SITES,
            msg="slider must be surfaced in the Patches-page site list")

    def test_slider_range_accepts_large_ceilings(self):
        """Users want to reach '10x = effectively uncapped'; the
        slider_max must permit that."""
        self.assertGreaterEqual(WING_FLAP_CEILING_SCALE.slider_max, 10.0)


class HookBytesPinned(unittest.TestCase):
    def test_hook_vanilla_is_6b_fadd_esi_0x144(self):
        # Opcode D8 /0 m32fp = FADD [mem]; modrm 0x86 = [ESI+disp32];
        # disp32 = 0x144 = entity.flap_height.
        self.assertEqual(_PEAK_Z_HOOK_VA, 0x00089154)
        self.assertEqual(
            _PEAK_Z_HOOK_VANILLA,
            bytes([0xD8, 0x86, 0x44, 0x01, 0x00, 0x00]),
            msg="hook_vanilla must decode to FADD [ESI+0x144]")

    def test_shim_body_is_15_bytes(self):
        self.assertEqual(_PEAK_Z_SHIM_BODY_SIZE, 15,
            msg="15 B = FLD(6) + FMUL(6) + FADDP(2) + RET(1)")


# ---------------------------------------------------------------------------
# Apply behaviour — needs the XBE fixture.
# ---------------------------------------------------------------------------

@require_xbe
class ApplyBehaviour(unittest.TestCase):

    def setUp(self):
        self.orig = XBE_PATH.read_bytes()

    def test_default_scale_is_noop(self):
        data = bytearray(self.orig)
        self.assertFalse(apply_wing_flap_ceiling(data, ceiling_scale=1.0))
        self.assertEqual(bytes(data), self.orig,
            msg="scale=1.0 must leave XBE byte-identical")

    def test_nondefault_scale_installs_trampoline(self):
        data = bytearray(self.orig)
        self.assertTrue(apply_wing_flap_ceiling(data, ceiling_scale=3.0))
        off = va_to_file(_PEAK_Z_HOOK_VA)
        # 5-byte CALL rel32 + 1-byte NOP.
        self.assertEqual(data[off], 0xE8, msg="trampoline opcode = CALL")
        self.assertEqual(data[off + 5], 0x90, msg="NOP pad")

    def test_reapply_is_idempotent(self):
        data = bytearray(self.orig)
        self.assertTrue(apply_wing_flap_ceiling(data, ceiling_scale=3.0))
        self.assertFalse(apply_wing_flap_ceiling(data, ceiling_scale=3.0),
            msg="second apply must detect trampoline + refuse")

    def test_hook_untouched_when_scale_is_one(self):
        data = bytearray(self.orig)
        apply_wing_flap_ceiling(data, ceiling_scale=1.0)
        off = va_to_file(_PEAK_Z_HOOK_VA)
        self.assertEqual(bytes(data[off:off + 6]), _PEAK_Z_HOOK_VANILLA)


@require_xbe
class ShimBodyShape(unittest.TestCase):
    """The landed shim body must match the designed layout and the
    FMUL operand must resolve to a valid XBE section."""

    def test_body_has_expected_fpu_shape(self):
        data = bytearray(XBE_PATH.read_bytes())
        self.assertTrue(apply_wing_flap_ceiling(data, ceiling_scale=2.0))
        # Read the trampoline rel32 to find the shim entry VA.
        off = va_to_file(_PEAK_Z_HOOK_VA)
        rel32 = struct.unpack("<i", bytes(data[off + 1:off + 5]))[0]
        shim_va = _PEAK_Z_HOOK_VA + 5 + rel32
        shim_off = resolve_va_to_file(bytes(data), shim_va)
        self.assertIsNotNone(shim_off,
            msg="shim entry VA must map to an XBE section")
        body = bytes(data[shim_off:shim_off + _PEAK_Z_SHIM_BODY_SIZE])
        # FLD [ESI+0x144]
        self.assertEqual(body[0:6],
                         bytes([0xD9, 0x86, 0x44, 0x01, 0x00, 0x00]))
        # FMUL [abs32] — opcode + modrm pinned; abs32 is dynamic
        self.assertEqual(body[6:8], b"\xD8\x0D")
        # FADDP ST(1)
        self.assertEqual(body[12:14], b"\xDE\xC1")
        # RET
        self.assertEqual(body[14:15], b"\xC3")

    def test_fmul_operand_points_at_correct_scale(self):
        """Decode the FMUL's abs32 operand, follow it through the
        section table, and verify the 4 bytes decode to the
        user-supplied scale."""
        for scale in (0.5, 2.0, 7.75):
            with self.subTest(scale=scale):
                data = bytearray(XBE_PATH.read_bytes())
                self.assertTrue(apply_wing_flap_ceiling(
                    data, ceiling_scale=scale))
                off = va_to_file(_PEAK_Z_HOOK_VA)
                rel32 = struct.unpack(
                    "<i", bytes(data[off + 1:off + 5]))[0]
                shim_va = _PEAK_Z_HOOK_VA + 5 + rel32
                shim_off = resolve_va_to_file(bytes(data), shim_va)
                assert shim_off is not None
                fmul_operand = struct.unpack(
                    "<I",
                    bytes(data[shim_off + 8:shim_off + 12]))[0]
                data_off = resolve_va_to_file(bytes(data), fmul_operand)
                self.assertIsNotNone(data_off,
                    msg="FMUL operand VA must resolve to a section")
                got = struct.unpack(
                    "<f",
                    bytes(data[data_off:data_off + 4]))[0]
                self.assertAlmostEqual(got, scale, places=5,
                    msg=f"injected scale must equal {scale}")


@require_xbe
class RoutedViaApplyPlayerPhysics(unittest.TestCase):
    """The pack-wide entrypoint must accept the new slider kwarg
    and forward it to ``apply_wing_flap_ceiling``."""

    def test_apply_player_physics_installs_shim(self):
        data = bytearray(XBE_PATH.read_bytes())
        apply_player_physics(data, wing_flap_ceiling_scale=2.5)
        off = va_to_file(_PEAK_Z_HOOK_VA)
        self.assertEqual(data[off], 0xE8,
            msg="apply_player_physics must route through the shim")

    def test_custom_apply_dispatcher_threads_through(self):
        """``_custom_apply`` is the hook apply_pack uses; it must
        accept ``wing_flap_ceiling_scale`` as a slider kwarg."""
        from azurik_mod.patches.player_physics import _custom_apply
        data = bytearray(XBE_PATH.read_bytes())
        _custom_apply(data, wing_flap_ceiling_scale=4.0)
        off = va_to_file(_PEAK_Z_HOOK_VA)
        self.assertEqual(data[off], 0xE8)

    def test_noop_when_scale_is_one_in_dispatcher(self):
        from azurik_mod.patches.player_physics import _custom_apply
        orig = XBE_PATH.read_bytes()
        data = bytearray(orig)
        _custom_apply(data, wing_flap_ceiling_scale=1.0)
        off = va_to_file(_PEAK_Z_HOOK_VA)
        self.assertEqual(bytes(data[off:off + 6]), _PEAK_Z_HOOK_VANILLA)


@require_xbe
class DynamicWhitelistCoverage(unittest.TestCase):
    """After apply, the pack's ``dynamic_whitelist_from_xbe``
    callback must include the shim's hook, body, AND data ranges
    so ``verify-patches --strict`` doesn't flag them as drift."""

    def test_vanilla_whitelist_includes_hook_only(self):
        xbe = XBE_PATH.read_bytes()
        ranges = _player_speed_dynamic_whitelist(xbe)
        # Pre-apply: trampoline-shape heuristic doesn't fire, so
        # only the hook slot should be in the whitelist.
        hook_off = va_to_file(_PEAK_Z_HOOK_VA)
        hook_range = (hook_off, hook_off + 6)
        self.assertIn(hook_range, ranges,
            msg="vanilla whitelist must cover the 6-byte hook slot")

    def test_patched_whitelist_includes_shim_body_and_data(self):
        data = bytearray(XBE_PATH.read_bytes())
        self.assertTrue(apply_wing_flap_ceiling(data, ceiling_scale=3.0))
        ranges = _player_speed_dynamic_whitelist(bytes(data))

        # Hook still in there.
        hook_off = va_to_file(_PEAK_Z_HOOK_VA)
        self.assertIn((hook_off, hook_off + 6), ranges)

        # Shim body range.
        off = va_to_file(_PEAK_Z_HOOK_VA)
        rel32 = struct.unpack("<i", bytes(data[off + 1:off + 5]))[0]
        shim_va = _PEAK_Z_HOOK_VA + 5 + rel32
        shim_off = resolve_va_to_file(bytes(data), shim_va)
        assert shim_off is not None
        body_range = (shim_off, shim_off + _PEAK_Z_SHIM_BODY_SIZE)
        self.assertIn(body_range, ranges,
            msg="whitelist must cover the 15-byte shim body")

        # Data (4-byte scale float) range.
        fmul_operand = struct.unpack(
            "<I", bytes(data[shim_off + 8:shim_off + 12]))[0]
        data_off = resolve_va_to_file(bytes(data), fmul_operand)
        assert data_off is not None
        self.assertIn((data_off, data_off + 4), ranges,
            msg="whitelist must cover the 4-byte injected scale")


if __name__ == "__main__":
    unittest.main()
