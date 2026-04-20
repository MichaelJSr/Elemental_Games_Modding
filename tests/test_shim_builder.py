"""Tests for the ``shim_builder`` helper module.

Covers the small emitters (FLD/FMUL/FSTP/JMP/CALL rel32), the
``HandShimSpec`` validation, ``with_sentinel`` shape, and the
high-level ``install_hand_shim`` + ``whitelist_for_hand_shim``
happy-path + drift-detection behaviours.
"""

from __future__ import annotations

import struct
import unittest

from tests._xbe_fixture import XBE_PATH, require_xbe  # noqa: E402

from azurik_mod.patching.shim_builder import (  # noqa: E402
    HandShimSpec,
    emit_call_rel32,
    emit_fld_abs32,
    emit_fmul_abs32,
    emit_fstp_abs32,
    emit_jmp_rel32,
    install_hand_shim,
    whitelist_for_hand_shim,
    with_sentinel,
)


# ---------------------------------------------------------------------------
# Instruction emitters
# ---------------------------------------------------------------------------

class EmitterByteShapes(unittest.TestCase):
    def test_jmp_rel32_shape(self):
        out = emit_jmp_rel32(from_origin_after=0x100, to_va=0x200)
        self.assertEqual(out[0], 0xE9)
        self.assertEqual(len(out), 5)
        rel32 = struct.unpack("<i", out[1:])[0]
        self.assertEqual(0x100 + rel32, 0x200)

    def test_call_rel32_shape(self):
        out = emit_call_rel32(from_origin_after=0x100, to_va=0x200)
        self.assertEqual(out[0], 0xE8)
        self.assertEqual(len(out), 5)

    def test_negative_rel32(self):
        out = emit_jmp_rel32(from_origin_after=0x200, to_va=0x100)
        rel32 = struct.unpack("<i", out[1:])[0]
        self.assertLess(rel32, 0)
        self.assertEqual(0x200 + rel32, 0x100)

    def test_fp_emitters(self):
        for emitter, opcode in (
                (emit_fld_abs32, b"\xD9\x05"),
                (emit_fmul_abs32, b"\xD8\x0D"),
                (emit_fstp_abs32, b"\xD9\x1D")):
            out = emitter(0xDEADBEEF)
            self.assertEqual(len(out), 6)
            self.assertEqual(out[0:2], opcode)
            self.assertEqual(
                struct.unpack("<I", out[2:])[0], 0xDEADBEEF)


# ---------------------------------------------------------------------------
# with_sentinel
# ---------------------------------------------------------------------------

class WithSentinelShape(unittest.TestCase):
    def test_appends_4_ff_bytes(self):
        for payload in (b"", b"\x01", b"\x00\x00\x00\x00", b"hello"):
            out = with_sentinel(payload)
            self.assertEqual(len(out), len(payload) + 4)
            self.assertEqual(out[-4:], b"\xFF\xFF\xFF\xFF")
            self.assertEqual(out[:-4], payload)


# ---------------------------------------------------------------------------
# HandShimSpec validation
# ---------------------------------------------------------------------------

class HandShimSpecValidation(unittest.TestCase):
    def test_rejects_bad_mode(self):
        with self.assertRaises(ValueError):
            HandShimSpec(
                hook_va=0x1000,
                hook_vanilla=b"\x00" * 5,
                trampoline_mode="notamode",
                body_size=16)

    def test_rejects_wrong_vanilla_length(self):
        # width = 5 + 0 = 5
        with self.assertRaises(ValueError):
            HandShimSpec(
                hook_va=0x1000,
                hook_vanilla=b"\x00" * 6,   # wrong length
                trampoline_mode="call",
                hook_pad_nops=0,
                body_size=16)

    def test_defaults_hook_return_va(self):
        spec = HandShimSpec(
            hook_va=0x1000,
            hook_vanilla=b"\x00" * 6,
            trampoline_mode="jmp",
            hook_pad_nops=1,
            body_size=20)
        self.assertEqual(spec.hook_return_va, 0x1006)
        self.assertEqual(spec.hook_width, 6)

    def test_explicit_hook_return_va(self):
        spec = HandShimSpec(
            hook_va=0x1000,
            hook_vanilla=b"\x00" * 5,
            trampoline_mode="call",
            hook_return_va=0x1234,
            body_size=10)
        self.assertEqual(spec.hook_return_va, 0x1234)


# ---------------------------------------------------------------------------
# install_hand_shim / whitelist_for_hand_shim on vanilla XBE
# ---------------------------------------------------------------------------

@require_xbe
class InstallHandShimSmoke(unittest.TestCase):
    """Smoke-test against ``slope_slide_speed``'s hook site —
    simplest live site we have (single FLD, 17-byte body)."""

    def setUp(self):
        from azurik_mod.patches.slope_slide_speed import (
            _HOOK_VA, _HOOK_VANILLA, _HOOK_RETURN_VA,
            _SHIM_BODY_SIZE, _build_shim_body,
        )
        self.hook_va = _HOOK_VA
        self.hook_vanilla = _HOOK_VANILLA
        self.hook_return_va = _HOOK_RETURN_VA
        self.body_size = _SHIM_BODY_SIZE
        self.build_body = _build_shim_body
        self.orig = XBE_PATH.read_bytes()

    def test_install_lands_trampoline(self):
        spec = HandShimSpec(
            hook_va=self.hook_va,
            hook_vanilla=self.hook_vanilla,
            trampoline_mode="jmp",
            hook_pad_nops=1,
            hook_return_va=self.hook_return_va,
            body_size=self.body_size,
        )
        data = bytearray(self.orig)
        result = install_hand_shim(
            data, spec,
            data_block=with_sentinel(struct.pack("<f", 2.0)),
            build_body=lambda shim_va, data_va: self.build_body(
                data_va, shim_va),
            label="smoke_test",
            verbose=False)
        self.assertIsNotNone(result)
        # Trampoline bytes at hook.
        from azurik_mod.patching.xbe import va_to_file
        off = va_to_file(self.hook_va)
        self.assertEqual(data[off], 0xE9)
        self.assertEqual(data[off + 5], 0x90)

    def test_reapply_detects_trampoline(self):
        spec = HandShimSpec(
            hook_va=self.hook_va,
            hook_vanilla=self.hook_vanilla,
            trampoline_mode="jmp",
            hook_pad_nops=1,
            hook_return_va=self.hook_return_va,
            body_size=self.body_size,
        )
        data = bytearray(self.orig)
        r1 = install_hand_shim(
            data, spec,
            data_block=with_sentinel(struct.pack("<f", 2.0)),
            build_body=lambda shim_va, data_va: self.build_body(
                data_va, shim_va),
            label="smoke_test", verbose=False)
        self.assertIsNotNone(r1)

        r2 = install_hand_shim(
            data, spec,
            data_block=with_sentinel(struct.pack("<f", 2.0)),
            build_body=lambda shim_va, data_va: self.build_body(
                data_va, shim_va),
            label="smoke_test", verbose=False)
        # Second call should detect the trampoline and return None.
        self.assertIsNone(r2)

    def test_drift_returns_none(self):
        spec = HandShimSpec(
            hook_va=self.hook_va,
            hook_vanilla=self.hook_vanilla,
            trampoline_mode="jmp",
            hook_pad_nops=1,
            hook_return_va=self.hook_return_va,
            body_size=self.body_size,
        )
        data = bytearray(self.orig)
        from azurik_mod.patching.xbe import va_to_file
        off = va_to_file(self.hook_va)
        data[off] = 0xAA   # corrupt
        result = install_hand_shim(
            data, spec,
            data_block=with_sentinel(struct.pack("<f", 2.0)),
            build_body=lambda s, d: self.build_body(d, s),
            label="drift_test", verbose=False)
        self.assertIsNone(result)


@require_xbe
class WhitelistForHandShim(unittest.TestCase):
    def test_vanilla_returns_hook_only(self):
        """Before apply, whitelist must return just the hook
        trampoline slot (since the trampoline bytes aren't present
        yet — just vanilla code)."""
        from azurik_mod.patches.slope_slide_speed import (
            _HOOK_VA, _HOOK_VANILLA, _HOOK_RETURN_VA,
            _SHIM_BODY_SIZE,
        )
        spec = HandShimSpec(
            hook_va=_HOOK_VA,
            hook_vanilla=_HOOK_VANILLA,
            trampoline_mode="jmp",
            hook_pad_nops=1,
            hook_return_va=_HOOK_RETURN_VA,
            body_size=_SHIM_BODY_SIZE,
        )
        data = XBE_PATH.read_bytes()
        ranges = whitelist_for_hand_shim(data, spec)
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0][1] - ranges[0][0], 6)

    def test_unresolvable_hook_returns_only_hook_range(self):
        """Whitelist must return the hook range even if the buffer
        is too small to parse the trampoline (graceful — not a
        crash, not a spurious empty result)."""
        spec = HandShimSpec(
            hook_va=0x11000,   # valid .text start
            hook_vanilla=b"\x00" * 5,
            trampoline_mode="jmp",
            body_size=4,
        )
        # 2-byte buffer — too small to parse the 5-byte trampoline.
        ranges = whitelist_for_hand_shim(b"\x00\x00", spec)
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0][1] - ranges[0][0], 5)


if __name__ == "__main__":
    unittest.main()
