"""Invariants for the ``qol_skip_logo`` pack.

Two paths are exercised:

- The TrampolinePatch backed by
  ``azurik_mod/patches/qol_skip_logo/shim.c``.
- The legacy byte-level PatchSpec kept behind ``AZURIK_NO_SHIMS=1``
  (or the older ``AZURIK_SKIP_LOGO_LEGACY=1``) as an escape hatch.

Both paths share the same observable contract: the AdreniumLogo
movie never plays, the boot state machine cleanly advances to its
next state (prophecy), and the stack balance is preserved.  The
naive "just NOP the PUSH+CALL pair" approach breaks both invariants
(stack leak + undefined AL → state machine jumps to a polling state
for a movie that was never started → black-screen hang on boot).
This test file pins the behaviour that actually boots.
"""

from __future__ import annotations

import os
import sys
import subprocess
import unittest
from pathlib import Path

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from azurik_mod.patches.qol import (  # noqa: E402
    SKIP_LOGO_SPEC,
    SKIP_LOGO_TRAMPOLINE,
    apply_skip_logo_patch,
)
from azurik_mod.patching import (  # noqa: E402
    verify_patch_spec,
    verify_trampoline_patch,
)
from azurik_mod.patching.registry import get_pack  # noqa: E402


# The 10-byte window around VA 0x05F6E0 — two instructions:
#   PUSH 0x0019e150   ; &"AdreniumLogo.bik"
#   CALL play_movie_fn ; __stdcall
LEGACY_ORIGINAL = bytes([
    0x68, 0x50, 0xE1, 0x19, 0x00,
    0xE8, 0x96, 0x92, 0xFB, 0xFF,
])
# Legacy fix: pop the leftover PUSH EBP, clear AL, NOP-fill.
LEGACY_PATCH = bytes([
    0x83, 0xC4, 0x04,                   # ADD ESP, 4
    0x30, 0xC0,                         # XOR AL, AL
    0x90, 0x90, 0x90, 0x90, 0x90,       # NOP x5
])

# The 5-byte CALL at 0x05F6E5 is the trampoline site.
TRAMPOLINE_ORIGINAL = bytes([0xE8, 0x96, 0x92, 0xFB, 0xFF])

_VANILLA_XBE = Path(_REPO_ROOT).parent / "Azurik - Rise of Perathia (USA).xiso/default.xbe"


class TrampolineDescriptor(unittest.TestCase):
    """The TrampolinePatch is the authoritative Phase 1 descriptor."""

    def test_va_targets_call_instruction(self):
        self.assertEqual(SKIP_LOGO_TRAMPOLINE.va, 0x05F6E5,
            msg="The trampoline must sit on the 5-byte CALL "
                "play_movie_fn, NOT the PUSH that precedes it.  The "
                "preceding PUSH 0x19e150 has to still execute so the "
                "shim receives the two __stdcall args on its stack.")

    def test_file_offset_matches_va(self):
        self.assertEqual(SKIP_LOGO_TRAMPOLINE.file_offset, 0x04F6E5)

    def test_replaced_bytes_is_the_call(self):
        self.assertEqual(
            SKIP_LOGO_TRAMPOLINE.replaced_bytes,
            TRAMPOLINE_ORIGINAL,
            msg="replaced_bytes should cover exactly the 5-byte "
                "CALL rel32 to play_movie_fn.  Any drift here means "
                "we'd either miss the call (too short) or clobber "
                "NEG AL that follows (too long).")

    def test_mode_is_call(self):
        self.assertEqual(SKIP_LOGO_TRAMPOLINE.mode, "call",
            msg="CALL keeps the return flow intact: shim returns, "
                "NEG AL sees AL=0, state machine advances to case 3.")

    def test_shim_symbol(self):
        self.assertEqual(SKIP_LOGO_TRAMPOLINE.shim_symbol, "_c_skip_logo")

    def test_shim_object_path(self):
        # Post-reorganisation the .o is keyed on the pack name
        # (``qol_skip_logo``), not the source stem (``shim``), so two
        # features whose source both sit at ``<folder>/shim.c`` don't
        # collide in the shared ``shims/build/`` cache.
        expected = Path(_REPO_ROOT) / "shims/build/qol_skip_logo.o"
        self.assertEqual(SKIP_LOGO_TRAMPOLINE.shim_object, expected)


class LegacyPatchSpecPreserved(unittest.TestCase):
    """The old byte patch is still importable behind the
    ``AZURIK_SKIP_LOGO_LEGACY=1`` escape hatch.  It must ALSO be
    functionally correct — a broken legacy path would be a worse
    escape hatch than nothing."""

    def test_legacy_va_and_offset(self):
        self.assertEqual(SKIP_LOGO_SPEC.va, 0x05F6E0)
        self.assertEqual(SKIP_LOGO_SPEC.file_offset, 0x04F6E0)

    def test_legacy_covers_full_push_and_call(self):
        self.assertEqual(SKIP_LOGO_SPEC.original, LEGACY_ORIGINAL,
            msg="legacy byte patch must cover both the PUSH imm32 "
                "and the CALL rel32 — 10 bytes total.")

    def test_legacy_patch_preserves_stack_and_al(self):
        self.assertEqual(SKIP_LOGO_SPEC.patch, LEGACY_PATCH,
            msg="patch must be ADD ESP,4 + XOR AL,AL + NOP*5 so the "
                "4-byte PUSH EBP leftover is popped AND the state "
                "machine sees AL=0 (skip-to-case-3).  A pure NOP fill "
                "would black-screen the game at boot.")

    def test_legacy_same_length_as_original(self):
        self.assertEqual(len(SKIP_LOGO_SPEC.original),
                         len(SKIP_LOGO_SPEC.patch))
        self.assertEqual(len(SKIP_LOGO_SPEC.original), 10)


class RegistryEntry(unittest.TestCase):
    def test_pack_registered_as_trampoline_site(self):
        import azurik_mod.patches  # noqa: F401
        pack = get_pack("qol_skip_logo")
        self.assertFalse(pack.default_on,
            msg="qol_skip_logo must default to OFF")
        self.assertIn("qol", pack.tags)
        self.assertIn("c-shim", pack.tags)
        self.assertEqual(len(pack.sites), 1)
        self.assertIs(pack.sites[0], SKIP_LOGO_TRAMPOLINE)


class LegacyEscapeHatch(unittest.TestCase):
    """``AZURIK_SKIP_LOGO_LEGACY=1`` falls back to the byte patch.
    Exercise the full apply path on a synthetic buffer to make sure
    the corrected legacy bytes land at the right offset."""

    def _buf_with_vanilla_site(self) -> bytearray:
        buf = bytearray(1 * 1024 * 1024)
        off = SKIP_LOGO_SPEC.file_offset
        buf[off:off + 10] = LEGACY_ORIGINAL
        return buf

    def test_legacy_mode_writes_corrected_bytes(self):
        buf = self._buf_with_vanilla_site()
        orig_env = os.environ.get("AZURIK_SKIP_LOGO_LEGACY")
        os.environ["AZURIK_SKIP_LOGO_LEGACY"] = "1"
        try:
            apply_skip_logo_patch(buf)
        finally:
            if orig_env is None:
                del os.environ["AZURIK_SKIP_LOGO_LEGACY"]
            else:
                os.environ["AZURIK_SKIP_LOGO_LEGACY"] = orig_env

        off = SKIP_LOGO_SPEC.file_offset
        self.assertEqual(bytes(buf[off:off + 10]), LEGACY_PATCH,
            msg="Under AZURIK_SKIP_LOGO_LEGACY=1 the 10 bytes must "
                "become ADD ESP,4 + XOR AL,AL + 5 NOPs.  Anything "
                "else is a regression to the black-screen bug.")
        self.assertEqual(verify_patch_spec(buf, SKIP_LOGO_SPEC), "applied")


@unittest.skipUnless(_VANILLA_XBE.exists(),
    f"vanilla XBE fixture not available at {_VANILLA_XBE}")
class TrampolineEndToEnd(unittest.TestCase):
    """Build the shim, apply against the vanilla Azurik XBE, and
    inspect the trampoline + shim bytes."""

    def setUp(self):
        shim_object = Path(SKIP_LOGO_TRAMPOLINE.shim_object)
        if not shim_object.is_absolute():
            shim_object = Path(_REPO_ROOT) / shim_object
        if not shim_object.exists():
            compile_sh = Path(_REPO_ROOT) / "shims/toolchain/compile.sh"
            src = Path(_REPO_ROOT) / "azurik_mod/patches/qol_skip_logo/shim.c"
            if not compile_sh.exists() or not src.exists():
                self.skipTest("shim sources / toolchain script missing")
            try:
                subprocess.check_call(
                    ["bash", str(compile_sh), str(src), str(shim_object)],
                    cwd=_REPO_ROOT)
            except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                self.skipTest(f"shim compile failed: {exc}")

    def test_vanilla_bytes_match_declared_call(self):
        xbe = _VANILLA_XBE.read_bytes()
        off = SKIP_LOGO_TRAMPOLINE.file_offset
        self.assertEqual(
            bytes(xbe[off:off + 5]), TRAMPOLINE_ORIGINAL,
            msg="vanilla XBE drift: the 5-byte CALL at 0x05F6E5 "
                "has moved or been rewritten.")

    def test_trampoline_applies_and_leaves_push_intact(self):
        """After apply, PUSH 0x0019e150 must still exist at 0x05F6E0."""
        xbe = bytearray(_VANILLA_XBE.read_bytes())
        apply_skip_logo_patch(xbe)

        push_off = 0x04F6E0  # file offset of PUSH imm32
        self.assertEqual(
            bytes(xbe[push_off:push_off + 5]),
            bytes([0x68, 0x50, 0xE1, 0x19, 0x00]),
            msg="The PUSH 0x0019E150 that precedes the CALL must "
                "survive the trampoline so the shim can see both "
                "__stdcall args on its stack.")

    def test_trampoline_is_call_rel32(self):
        xbe = bytearray(_VANILLA_XBE.read_bytes())
        apply_skip_logo_patch(xbe)
        off = SKIP_LOGO_TRAMPOLINE.file_offset
        self.assertEqual(xbe[off], 0xE8,
            msg="first byte of trampoline must be CALL rel32 (0xE8)")

    def test_verify_says_applied(self):
        xbe = bytearray(_VANILLA_XBE.read_bytes())
        apply_skip_logo_patch(xbe)
        self.assertEqual(
            verify_trampoline_patch(bytes(xbe), SKIP_LOGO_TRAMPOLINE),
            "applied")


if __name__ == "__main__":
    unittest.main()
