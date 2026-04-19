"""Regression tests for ``qol_skip_save_signature``.

The patch is simple — 3 bytes at a single VA — but the invariants
matter:

- Vanilla bytes at VA 0x0005C990 MUST be the ``MOV AL, [ECX+0x20A]``
  prologue we patch against.  If Ghidra drifts or a different XBE
  revision moves the function, this fixture catches it before we
  ship an ISO whose "skip-sig" patch lands in unrelated code.
- The replacement MUST be ``MOV AL, 1`` + ``RET`` — nothing else
  delivers AL=1 while leaving the stack untouched.
- The feature MUST register as ``qol_skip_save_signature`` in the
  ``qol`` category so the GUI's Patches page and
  ``verify-patches --strict`` both see it.
- ``apply_skip_save_signature_patch`` MUST be idempotent so running
  the build pipeline twice against the same buffer doesn't corrupt
  it.
- Exactly 3 bytes must differ from vanilla — any wider delta means
  the patch spec drifted and verify-patches --strict will reject.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from azurik_mod.patches.qol_skip_save_signature import (  # noqa: E402
    ALWAYS_ACCEPT_SIG_SPEC,
    AZURIK_VERIFY_SAVE_SIG_VA,
    SKIP_SAVE_SIG_SITES,
    apply_skip_save_signature_patch,
)
from azurik_mod.patching import verify_patch_spec  # noqa: E402
from azurik_mod.patching.registry import get_pack  # noqa: E402


# File offset corresponding to VA 0x0005C990 in Azurik's ``.text``
# section (image base 0x00010000, file-offset map confirmed via
# ``xbe hexdump --xbe <path> 0x5C990``).
_VERIFY_SAVE_SIG_FILE_OFFSET = 0x0004C990

# Vanilla prologue bytes (``MOV AL, [ECX+0x20A]``).
_VANILLA_PROLOGUE = bytes.fromhex("8a810a")
# Patched prologue (``MOV AL, 1 ; RET``).
_PATCHED_PROLOGUE = bytes.fromhex("b001c3")

_VANILLA_XBE = Path(_REPO_ROOT).parent / "Azurik - Rise of Perathia (USA).xiso/default.xbe"


class PatchSpecInvariants(unittest.TestCase):
    """Shape guards on the :class:`PatchSpec` itself."""

    def test_va_targets_verify_function_prologue(self):
        self.assertEqual(
            ALWAYS_ACCEPT_SIG_SPEC.va, AZURIK_VERIFY_SAVE_SIG_VA,
            msg="Skip-sig must target the verify_save_signature "
                "prologue at 0x0005C990.  If the VA drifted, the "
                "first-byte compare in apply_patch_spec will miss "
                "and verify-patches --strict will land on unrelated "
                "code.")

    def test_va_is_0x5c990(self):
        self.assertEqual(AZURIK_VERIFY_SAVE_SIG_VA, 0x0005C990,
            msg="verify_save_signature lives at 0x0005C990 — the "
                "7th entry in the save-dispatch vtable at 0x0019E260.")

    def test_original_is_vanilla_prologue(self):
        self.assertEqual(
            ALWAYS_ACCEPT_SIG_SPEC.original, _VANILLA_PROLOGUE,
            msg="original bytes must match the vanilla prologue "
                "exactly — MOV AL, [ECX+0x20A] → 8A 81 0A.  Anything "
                "else means apply_patch_spec refuses to write.")

    def test_patch_is_mov_al_1_ret(self):
        self.assertEqual(
            ALWAYS_ACCEPT_SIG_SPEC.patch, _PATCHED_PROLOGUE,
            msg="patch bytes must encode MOV AL, 1 ; RET → B0 01 "
                "C3.  Any other sequence either leaves a stack "
                "imbalance (SUB ESP hit without matching ADD) or "
                "returns the wrong AL (caller then rejects the "
                "save).")

    def test_patch_is_three_bytes(self):
        self.assertEqual(len(ALWAYS_ACCEPT_SIG_SPEC.patch), 3)
        self.assertEqual(len(ALWAYS_ACCEPT_SIG_SPEC.original), 3)

    def test_not_safety_critical(self):
        # Save-sig verify isn't in a render / sim hot path, and
        # ``PatchSpec.safety_critical`` gates extra double-checks in
        # verify-patches.  False here keeps the patch in the
        # "cosmetic / convenience" bucket it actually occupies.
        self.assertFalse(ALWAYS_ACCEPT_SIG_SPEC.safety_critical)


class RegistryEntry(unittest.TestCase):
    """The feature must show up in the central pack registry."""

    def test_pack_registered(self):
        import azurik_mod.patches  # noqa: F401 — triggers registration
        pack = get_pack("qol_skip_save_signature")
        self.assertFalse(
            pack.default_on,
            msg="qol_skip_save_signature must default to OFF — "
                "signature bypass is opt-in.  A user who never "
                "asked for save editing should not ship with "
                "engine-level verify bypass.")
        self.assertEqual(pack.category, "qol")
        self.assertIn("save-edit", pack.tags)
        self.assertIn("signature-bypass", pack.tags)
        self.assertEqual(len(pack.sites), 1)
        self.assertIs(pack.sites[0], ALWAYS_ACCEPT_SIG_SPEC)


class ApplyCore(unittest.TestCase):
    """Exercise the apply path against a synthetic buffer."""

    def _buf_with_vanilla_site(self) -> bytearray:
        buf = bytearray(1 * 1024 * 1024)
        off = _VERIFY_SAVE_SIG_FILE_OFFSET
        buf[off:off + len(_VANILLA_PROLOGUE)] = _VANILLA_PROLOGUE
        return buf

    def test_apply_writes_patched_bytes(self):
        buf = self._buf_with_vanilla_site()
        apply_skip_save_signature_patch(buf)
        off = _VERIFY_SAVE_SIG_FILE_OFFSET
        self.assertEqual(
            bytes(buf[off:off + 3]), _PATCHED_PROLOGUE,
            msg="first 3 bytes must become MOV AL, 1 ; RET.")

    def test_apply_is_idempotent(self):
        buf = self._buf_with_vanilla_site()
        apply_skip_save_signature_patch(buf)
        snapshot = bytes(buf)
        apply_skip_save_signature_patch(buf)
        self.assertEqual(bytes(buf), snapshot,
            msg="re-applying must be a no-op: the original-bytes "
                "guard inside apply_patch_spec already sees the "
                "patched sequence and refuses to re-write.")

    def test_verify_reports_applied(self):
        buf = self._buf_with_vanilla_site()
        apply_skip_save_signature_patch(buf)
        self.assertEqual(
            verify_patch_spec(buf, ALWAYS_ACCEPT_SIG_SPEC),
            "applied")

    def test_verify_reports_original_before_apply(self):
        # ``verify_patch_spec`` classifies: "applied" (post-patch
        # bytes match), "original" (vanilla bytes match), or
        # "unknown" (something else at the site).  Fresh buffer
        # should land in "original".
        buf = self._buf_with_vanilla_site()
        self.assertEqual(
            verify_patch_spec(buf, ALWAYS_ACCEPT_SIG_SPEC),
            "original")


@unittest.skipUnless(
    _VANILLA_XBE.exists(),
    f"vanilla XBE fixture not available at {_VANILLA_XBE}")
class EndToEndAgainstVanillaXbe(unittest.TestCase):
    """Smoke-test against the real XBE — catches any VA-→-file-offset
    drift that a synthetic fixture can't surface.
    """

    def test_real_prologue_matches_spec(self):
        xbe = _VANILLA_XBE.read_bytes()
        off = _VERIFY_SAVE_SIG_FILE_OFFSET
        self.assertEqual(
            bytes(xbe[off:off + 3]), _VANILLA_PROLOGUE,
            msg="vanilla XBE drift: the MOV AL, [ECX+0x20A] prologue "
                "at file offset 0x4C990 has moved.  Update the VA in "
                "qol_skip_save_signature and rerun this test.")

    def test_apply_produces_exactly_three_byte_delta(self):
        original = bytes(_VANILLA_XBE.read_bytes())
        patched = bytearray(original)
        apply_skip_save_signature_patch(patched)

        diffs = [i for i in range(len(original))
                 if patched[i] != original[i]]
        self.assertEqual(
            len(diffs), 3,
            msg=f"expected exactly 3 byte diffs; got {len(diffs)}. "
                f"First few offsets: {diffs[:8]}")
        # Contiguous 3-byte window starting at the verify prologue.
        self.assertEqual(
            diffs,
            list(range(_VERIFY_SAVE_SIG_FILE_OFFSET,
                       _VERIFY_SAVE_SIG_FILE_OFFSET + 3)),
            msg="the three diffs must form one contiguous 3-byte "
                "window at the verify prologue.  Multiple disjoint "
                "windows would indicate a second PatchSpec site "
                "leaked in.")


if __name__ == "__main__":
    unittest.main()
