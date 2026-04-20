"""Tests for the ``flap_at_peak`` shim-backed pack.

The pack installs a 6-byte ``JMP + NOP`` trampoline at VA 0x89409
that diverts into a 43-byte hand-assembled shim.  The shim
guarantees every 2nd+ wing flap reaches at least
``sqrt(2g * flap_height) * scale`` by writing max(floor,
vanilla-v0) to ``entity+0x2C``.

Tests cover:

- Spec shape: ParametricPatch fields, Feature registration.
- Vanilla bytes match Ghidra at the hook site.
- Apply lands the 6-byte trampoline + carves both the K-float
  and the 43-byte shim body.
- Re-apply is detected (idempotent — prints "already applied").
- Shim body has the expected structure (FLD K, FMUL fh, FSQRT,
  FCOM, max-select, replay MOV, JMP back).
- Dynamic whitelist returns the trampoline + shim + K slots.
"""

from __future__ import annotations

import struct
import unittest

from tests._xbe_fixture import XBE_PATH, require_xbe  # noqa: E402

from azurik_mod.patches.flap_at_peak import (  # noqa: E402
    FLAP_AT_PEAK_SHIM_SLIDER,
    FLAP_AT_PEAK_SITES,
    _HOOK_RETURN_VA,
    _HOOK_VA,
    _HOOK_VANILLA,
    _SHIM_BODY_SIZE,
    _VANILLA_GRAVITY,
    _build_shim_body,
    apply_flap_at_peak,
)
from azurik_mod.patching.xbe import va_to_file  # noqa: E402


# ---------------------------------------------------------------------------
# Spec shape
# ---------------------------------------------------------------------------

class FlapAtPeakSpecShape(unittest.TestCase):
    def test_slider_name_and_label(self):
        self.assertEqual(FLAP_AT_PEAK_SHIM_SLIDER.name,
                         "flap_at_peak_scale")
        self.assertIn("Wing-flap", FLAP_AT_PEAK_SHIM_SLIDER.label)

    def test_slider_is_virtual(self):
        """va=0, size=0 — the shim doesn't overwrite a fixed
        instruction site; it carves a new shim body instead."""
        self.assertTrue(FLAP_AT_PEAK_SHIM_SLIDER.is_virtual)

    def test_slider_default_and_range(self):
        self.assertEqual(FLAP_AT_PEAK_SHIM_SLIDER.default, 1.0)
        self.assertEqual(FLAP_AT_PEAK_SHIM_SLIDER.slider_min, 0.1)
        self.assertEqual(FLAP_AT_PEAK_SHIM_SLIDER.slider_max, 10.0)

    def test_slider_has_description(self):
        self.assertIn("ceiling",
                      FLAP_AT_PEAK_SHIM_SLIDER.description.lower())

    def test_sites_is_just_the_slider(self):
        self.assertEqual(len(FLAP_AT_PEAK_SITES), 1)
        self.assertIs(FLAP_AT_PEAK_SITES[0], FLAP_AT_PEAK_SHIM_SLIDER)

    def test_hook_va_is_fstp_site(self):
        """0x89409 is the FSTP [ESI+0x2C] that writes the final
        z-velocity for the current flap."""
        self.assertEqual(_HOOK_VA, 0x00089409)

    def test_hook_return_is_after_6_byte_window(self):
        """After a 6-byte JMP + NOP trampoline at 0x89409,
        execution resumes at 0x8940F (the PUSH 0x11 for the
        upcoming CALL anim_change)."""
        self.assertEqual(_HOOK_RETURN_VA, _HOOK_VA + 6)
        self.assertEqual(_HOOK_RETURN_VA, 0x0008940F)

    def test_vanilla_bytes_shape(self):
        """Vanilla must be D9 5E 2C (FSTP) + 8B 46 20 (MOV)."""
        self.assertEqual(len(_HOOK_VANILLA), 6)
        self.assertEqual(_HOOK_VANILLA[:3], b"\xD9\x5E\x2C")
        self.assertEqual(_HOOK_VANILLA[3:], b"\x8B\x46\x20")


# ---------------------------------------------------------------------------
# Shim body assembly
# ---------------------------------------------------------------------------

class FlapAtPeakShimShape(unittest.TestCase):
    """Hand-assembled shim body shape — size + instruction layout."""

    def setUp(self):
        # Arbitrary but stable VAs for deterministic output.
        self.k_va = 0x001001D0
        self.shim_va = 0x0039F000
        self.body = _build_shim_body(self.shim_va, self.k_va)

    def test_body_is_43_bytes(self):
        self.assertEqual(len(self.body), 43)
        self.assertEqual(len(self.body), _SHIM_BODY_SIZE)

    def test_first_instr_is_fld_k_va(self):
        """Offset 0..5: FLD [K_VA] = D9 05 <abs32>."""
        self.assertEqual(self.body[0:2], b"\xD9\x05")
        self.assertEqual(
            struct.unpack("<I", self.body[2:6])[0], self.k_va)

    def test_fmul_flap_height(self):
        """Offset 6..11: FMUL [ESI+0x144] = D8 8E 44 01 00 00."""
        self.assertEqual(self.body[6:12],
                         b"\xD8\x8E\x44\x01\x00\x00")

    def test_fsqrt(self):
        """Offset 12..13: FSQRT = D9 FA."""
        self.assertEqual(self.body[12:14], b"\xD9\xFA")

    def test_fcom_st1_fnstsw_test(self):
        """Offset 14..20: FCOM ST(1); FNSTSW AX; TEST AH, 0x01."""
        self.assertEqual(self.body[14:16], b"\xD8\xD1")   # FCOM ST(1)
        self.assertEqual(self.body[16:18], b"\xDF\xE0")   # FNSTSW AX
        self.assertEqual(self.body[18:21], b"\xF6\xC4\x01")  # TEST AH, 1

    def test_jnz_to_vanilla_branch(self):
        """Offset 21..22: JNZ +7 (to offset 30)."""
        self.assertEqual(self.body[21:23], b"\x75\x07")

    def test_floor_branch_writes_fstp_then_pops(self):
        """Offsets 23..29: FSTP [ESI+0x2C]; FSTP ST(0); JMP +5."""
        # FSTP [ESI+0x2C]
        self.assertEqual(self.body[23:26], b"\xD9\x5E\x2C")
        # FSTP ST(0) — pop the other
        self.assertEqual(self.body[26:28], b"\xDD\xD8")
        # JMP +5
        self.assertEqual(self.body[28:30], b"\xEB\x05")

    def test_vanilla_branch_pops_then_writes(self):
        """Offsets 30..34: FSTP ST(0); FSTP [ESI+0x2C]."""
        self.assertEqual(self.body[30:32], b"\xDD\xD8")
        self.assertEqual(self.body[32:35], b"\xD9\x5E\x2C")

    def test_replay_mov_eax_esi_20(self):
        """Offset 35..37: MOV EAX, [ESI+0x20] (8B 46 20)."""
        self.assertEqual(self.body[35:38], b"\x8B\x46\x20")

    def test_final_jmp_returns_to_0x8940f(self):
        """Offset 38..42: JMP rel32 back to _HOOK_RETURN_VA."""
        self.assertEqual(self.body[38:39], b"\xE9")
        rel32 = struct.unpack("<i", self.body[39:43])[0]
        target = self.shim_va + 43 + rel32
        self.assertEqual(target, _HOOK_RETURN_VA)


# ---------------------------------------------------------------------------
# Apply on vanilla XBE
# ---------------------------------------------------------------------------

@require_xbe
class FlapAtPeakApply(unittest.TestCase):
    def setUp(self):
        self.orig = XBE_PATH.read_bytes()

    def test_vanilla_bytes_match_ghidra(self):
        """Drift-guard: the 6 bytes at VA 0x89409 must match
        what _HOOK_VANILLA expects."""
        off = va_to_file(_HOOK_VA)
        self.assertEqual(bytes(self.orig[off:off + 6]),
                         _HOOK_VANILLA)

    def test_apply_installs_trampoline(self):
        data = bytearray(self.orig)
        self.assertTrue(apply_flap_at_peak(data, scale=2.0))
        off = va_to_file(_HOOK_VA)
        tramp = bytes(data[off:off + 6])
        self.assertEqual(tramp[0], 0xE9, "expected JMP rel32")
        self.assertEqual(tramp[5], 0x90, "expected trailing NOP")

    def test_apply_injects_k_with_correct_value(self):
        """After apply(scale=2.0), the K float at the
        shim-referenced VA should equal 2 * 9.8 * 2^2 = 78.4."""
        from azurik_mod.patching.xbe import resolve_va_to_file
        data = bytearray(self.orig)
        apply_flap_at_peak(data, scale=2.0)
        off = va_to_file(_HOOK_VA)
        tramp = bytes(data[off:off + 6])
        rel32 = struct.unpack("<i", tramp[1:5])[0]
        shim_va = _HOOK_VA + 5 + rel32
        shim_off = resolve_va_to_file(bytes(data), shim_va)
        self.assertIsNotNone(shim_off)
        body = bytes(data[shim_off:shim_off + _SHIM_BODY_SIZE])
        self.assertEqual(body[0:2], b"\xD9\x05")
        k_va = struct.unpack("<I", body[2:6])[0]
        k_off = resolve_va_to_file(bytes(data), k_va)
        self.assertIsNotNone(k_off)
        k_value = struct.unpack("<f", bytes(data[k_off:k_off + 4]))[0]
        expected = 2.0 * _VANILLA_GRAVITY * (2.0 * 2.0)  # = 78.4
        self.assertAlmostEqual(k_value, expected, places=3)

    def test_apply_scale_1_still_installs(self):
        """scale=1.0 is the baseline "match first-flap v0 every
        2nd+ flap" case — not a no-op.  Must install."""
        data = bytearray(self.orig)
        self.assertTrue(apply_flap_at_peak(data, scale=1.0))
        off = va_to_file(_HOOK_VA)
        self.assertEqual(bytes(data[off:off + 1]), b"\xE9")

    def test_drift_detection(self):
        """If the 6 bytes at 0x89409 aren't vanilla, apply refuses."""
        data = bytearray(self.orig)
        off = va_to_file(_HOOK_VA)
        data[off] = 0xAA    # corrupt
        self.assertFalse(apply_flap_at_peak(data, scale=2.0))

    def test_reapply_is_idempotent(self):
        """Applying twice leaves the trampoline byte-identical."""
        data = bytearray(self.orig)
        self.assertTrue(apply_flap_at_peak(data, scale=2.0))
        snapshot = bytes(data)
        # Re-apply — should detect the existing trampoline.
        self.assertTrue(apply_flap_at_peak(data, scale=2.0))
        self.assertEqual(bytes(data), snapshot,
            msg="re-apply must not double-install")

    def test_whitelist_covers_trampoline_shim_and_k(self):
        from azurik_mod.patches.flap_at_peak import (
            _flap_at_peak_dynamic_whitelist,
        )
        data = bytearray(self.orig)
        apply_flap_at_peak(data, scale=2.0)
        ranges = _flap_at_peak_dynamic_whitelist(bytes(data))
        # Expect at least 3 ranges: trampoline (6), shim body (43),
        # K + sentinel (8).
        sizes = sorted(hi - lo for lo, hi in ranges)
        self.assertIn(6, sizes)
        self.assertIn(43, sizes)
        self.assertIn(8, sizes)


# ---------------------------------------------------------------------------
# Feature registration
# ---------------------------------------------------------------------------

class FlapAtPeakFeature(unittest.TestCase):
    def test_pack_is_registered(self):
        from azurik_mod.patching.registry import all_packs
        names = [p.name for p in all_packs()]
        self.assertIn("flap_at_peak", names)

    def test_pack_is_in_player_category(self):
        from azurik_mod.patching.registry import get_pack
        pack = get_pack("flap_at_peak")
        self.assertEqual(pack.category, "player")
        self.assertIn("c-shim", pack.tags)


if __name__ == "__main__":
    unittest.main()
