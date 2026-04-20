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

# Smoke-test hook: the state-4 fast-slide FLD at VA 0x8A095 — a
# plain ``D9 05 A0 02 39 00`` (FLD [0x003902A0]) in vanilla. The
# former ``slope_slide_speed`` pack used this site but was removed
# in round 10; we reference the raw bytes directly here so the
# shim_builder surface remains exercised end-to-end.
_SMOKE_HOOK_VA = 0x0008A095
_SMOKE_HOOK_VANILLA = b"\xD9\x05\xA0\x02\x39\x00"
_SMOKE_HOOK_RETURN_VA = _SMOKE_HOOK_VA + 6
_SMOKE_BODY_SIZE = 17


def _smoke_build_body(data_va: int, shim_va: int) -> bytes:
    """Return a 17-byte body: FLD [0x003902A0]; FMUL [data_va];
    JMP back to hook+6 (return VA)."""
    body = bytearray(b"\xD9\x05\xA0\x02\x39\x00")        # FLD [abs]
    body += emit_fmul_abs32(data_va)                      # FMUL [data_va]
    return_origin_after = shim_va + len(body) + 5
    body += emit_jmp_rel32(return_origin_after,
                           _SMOKE_HOOK_RETURN_VA)
    assert len(body) == _SMOKE_BODY_SIZE
    return bytes(body)


@require_xbe
class InstallHandShimSmoke(unittest.TestCase):
    """Smoke-test against a raw hook site (state-4 fast-slide FLD).
    Uses the shim_builder surface directly; no pack module dep."""

    def setUp(self):
        self.orig = XBE_PATH.read_bytes()
        self.spec = HandShimSpec(
            hook_va=_SMOKE_HOOK_VA,
            hook_vanilla=_SMOKE_HOOK_VANILLA,
            trampoline_mode="jmp",
            hook_pad_nops=1,
            hook_return_va=_SMOKE_HOOK_RETURN_VA,
            body_size=_SMOKE_BODY_SIZE,
        )

    def _install(self, data):
        return install_hand_shim(
            data, self.spec,
            data_block=with_sentinel(struct.pack("<f", 2.0)),
            build_body=_smoke_build_body,
            label="smoke_test",
            verbose=False)

    def test_install_lands_trampoline(self):
        data = bytearray(self.orig)
        self.assertIsNotNone(self._install(data))
        from azurik_mod.patching.xbe import va_to_file
        off = va_to_file(_SMOKE_HOOK_VA)
        self.assertEqual(data[off], 0xE9)
        self.assertEqual(data[off + 5], 0x90)

    def test_reapply_detects_trampoline(self):
        data = bytearray(self.orig)
        self.assertIsNotNone(self._install(data))
        self.assertIsNone(self._install(data),
            msg="second install must detect existing trampoline")

    def test_drift_returns_none(self):
        data = bytearray(self.orig)
        from azurik_mod.patching.xbe import va_to_file
        off = va_to_file(_SMOKE_HOOK_VA)
        data[off] = 0xAA
        self.assertIsNone(self._install(data))


@require_xbe
class WhitelistForHandShim(unittest.TestCase):
    def test_vanilla_returns_hook_only(self):
        spec = HandShimSpec(
            hook_va=_SMOKE_HOOK_VA,
            hook_vanilla=_SMOKE_HOOK_VANILLA,
            trampoline_mode="jmp",
            hook_pad_nops=1,
            hook_return_va=_SMOKE_HOOK_RETURN_VA,
            body_size=_SMOKE_BODY_SIZE,
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
