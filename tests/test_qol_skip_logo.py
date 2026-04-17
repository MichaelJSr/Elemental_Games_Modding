"""Invariants for the `qol_skip_logo` pack.

Pins the 10-byte `PUSH &"AdreniumLogo.bik"; CALL play_movie_fn` site at
VA 0x05F6E0 and confirms the pack NOPs the call cleanly without
touching anything else.  All tests operate on synthetic buffers so the
suite stays offline and CI-safe.
"""

from __future__ import annotations

import os
import sys
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from azurik_mod.patches.qol import (  # noqa: E402
    SKIP_LOGO_SPEC,
    apply_skip_logo_patch,
)
from azurik_mod.patching import verify_patch_spec  # noqa: E402
from azurik_mod.patching.registry import get_pack  # noqa: E402


EXPECTED_ORIGINAL = bytes([
    0x68, 0x50, 0xE1, 0x19, 0x00,   # PUSH 0x0019E150 (&"AdreniumLogo.bik")
    0xE8, 0x96, 0x92, 0xFB, 0xFF,   # CALL play_movie_fn (rel32)
])
EXPECTED_PATCH = bytes([0x90] * 10)


class SkipLogoSpecInvariants(unittest.TestCase):
    def test_va_and_file_offset(self):
        self.assertEqual(SKIP_LOGO_SPEC.va, 0x05F6E0,
            msg="qol_skip_logo must target VA 0x05F6E0 (the "
                "PUSH &AdreniumLogo.bik instruction in .text).")
        self.assertEqual(SKIP_LOGO_SPEC.file_offset, 0x04F6E0,
            msg="VA 0x05F6E0 must map to file offset 0x04F6E0 under "
                "the XBE section table.")

    def test_original_bytes(self):
        self.assertEqual(SKIP_LOGO_SPEC.original, EXPECTED_ORIGINAL,
            msg="Original bytes drifted.  The instruction pair must stay "
                "68 50 E1 19 00 E8 96 92 FB FF  (PUSH imm32; CALL rel32).")

    def test_patch_is_ten_nops(self):
        self.assertEqual(SKIP_LOGO_SPEC.patch, EXPECTED_PATCH,
            msg="Patch must be exactly ten 0x90 NOPs so control falls "
                "through to the next instruction without touching the "
                "stack, flags, or memory.")

    def test_original_and_patch_same_length(self):
        self.assertEqual(len(SKIP_LOGO_SPEC.original),
                         len(SKIP_LOGO_SPEC.patch),
            msg="Patch length must match original length or surrounding "
                "code shifts and every later offset breaks.")
        self.assertEqual(len(SKIP_LOGO_SPEC.original), 10,
            msg="The patch site is a 10-byte PUSH imm32; CALL rel32 pair.")


class ApplyAndVerify(unittest.TestCase):
    """End-to-end: apply, verify, idempotence, scope of the change."""

    def _make_buffer(self) -> bytearray:
        """1 MB buffer with only the original 10 bytes placed at the
        declared file offset.  Everything else is zero."""
        buf = bytearray(1 * 1024 * 1024)
        off = SKIP_LOGO_SPEC.file_offset
        buf[off:off + 10] = EXPECTED_ORIGINAL
        return buf

    def test_verify_original_before_patch(self):
        buf = self._make_buffer()
        self.assertEqual(verify_patch_spec(buf, SKIP_LOGO_SPEC), "original")

    def test_apply_changes_exactly_ten_bytes(self):
        buf = self._make_buffer()
        before = bytes(buf)
        apply_skip_logo_patch(buf)
        diffs = [i for i in range(len(buf)) if buf[i] != before[i]]
        off = SKIP_LOGO_SPEC.file_offset
        self.assertEqual(
            sorted(diffs), list(range(off, off + 10)),
            msg=f"apply_skip_logo_patch must flip exactly the 10 bytes at "
                f"0x{off:X}, but changed: {[hex(d) for d in diffs]}")

    def test_all_flipped_bytes_are_nops(self):
        buf = self._make_buffer()
        apply_skip_logo_patch(buf)
        off = SKIP_LOGO_SPEC.file_offset
        for i in range(10):
            self.assertEqual(buf[off + i], 0x90,
                msg=f"byte at 0x{off + i:X} must be 0x90 (NOP) after patch")

    def test_verify_applied_after_patch(self):
        buf = self._make_buffer()
        apply_skip_logo_patch(buf)
        self.assertEqual(verify_patch_spec(buf, SKIP_LOGO_SPEC), "applied")

    def test_idempotent(self):
        """apply_patch_spec recognises the already-applied state and is a
        no-op on the second run."""
        buf = self._make_buffer()
        apply_skip_logo_patch(buf)
        snapshot = bytes(buf)
        apply_skip_logo_patch(buf)  # second run — must not touch anything
        self.assertEqual(bytes(buf), snapshot,
            msg="re-applying the patch must be a no-op; the apply helper "
                "recognises the already-NOP state via the `patch == current` "
                "branch in apply_xbe_patch.")


class RegistryEntry(unittest.TestCase):
    def test_pack_registered_with_expected_metadata(self):
        import azurik_mod.patches  # noqa: F401  — triggers registration
        pack = get_pack("qol_skip_logo")
        self.assertFalse(pack.default_on,
            msg="qol_skip_logo must default to OFF")
        self.assertIn("qol", pack.tags)
        self.assertEqual(len(pack.sites), 1,
            msg="qol_skip_logo has exactly one PatchSpec site.")
        self.assertIs(pack.sites[0], SKIP_LOGO_SPEC)


if __name__ == "__main__":
    unittest.main()
