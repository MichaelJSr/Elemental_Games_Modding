"""Invariants for the ``qol_skip_logo`` pack.

Phase 1 promotes this pack from a byte-level NOP patch to a proper
TrampolinePatch backed by a C shim (``shims/src/skip_logo.c``).  This
suite pins:

- The TrampolinePatch site's VA / replaced bytes / shim symbol.
- The legacy PatchSpec form (still importable behind
  ``AZURIK_SKIP_LOGO_LEGACY=1``).
- The pack's registry metadata (default-off, single trampoline site).
- End-to-end apply using a real compiled shim against a real XBE
  fixture (the vanilla Azurik ``default.xbe`` via the project's
  extracted copy, skipped gracefully when the fixture is absent).
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


EXPECTED_ORIGINAL = bytes([
    0x68, 0x50, 0xE1, 0x19, 0x00,   # PUSH 0x0019E150 (&"AdreniumLogo.bik")
    0xE8, 0x96, 0x92, 0xFB, 0xFF,   # CALL play_movie_fn (rel32)
])
EXPECTED_LEGACY_PATCH = bytes([0x90] * 10)

_VANILLA_XBE = Path(_REPO_ROOT).parent / "Azurik - Rise of Perathia (USA).xiso/default.xbe"


class TrampolineDescriptor(unittest.TestCase):
    """The TrampolinePatch is the authoritative Phase 1 descriptor."""

    def test_va_and_file_offset(self):
        self.assertEqual(SKIP_LOGO_TRAMPOLINE.va, 0x05F6E0)
        self.assertEqual(SKIP_LOGO_TRAMPOLINE.file_offset, 0x04F6E0)

    def test_replaced_bytes(self):
        self.assertEqual(SKIP_LOGO_TRAMPOLINE.replaced_bytes, EXPECTED_ORIGINAL,
            msg="The trampoline site must cover the same 10 bytes the "
                "legacy NOP patch used to blank out.")

    def test_mode_is_call(self):
        self.assertEqual(SKIP_LOGO_TRAMPOLINE.mode, "call",
            msg="skip_logo uses CALL so control returns to the site + 5; "
                "if we ever switch to JMP this test pins the intent.")

    def test_shim_symbol(self):
        self.assertEqual(SKIP_LOGO_TRAMPOLINE.shim_symbol, "_c_skip_logo",
            msg="PE-COFF names are leading-underscore prefixed; the "
                "C declaration is `void c_skip_logo(void)`.")

    def test_shim_object_path(self):
        # Path is expressed as a project-relative Path so the apply
        # pipeline can resolve it against any working directory.
        self.assertEqual(
            SKIP_LOGO_TRAMPOLINE.shim_object,
            Path("shims/build/skip_logo.o"))


class LegacyPatchSpecPreserved(unittest.TestCase):
    """The old byte-NOP spec must still be importable for the
    ``AZURIK_SKIP_LOGO_LEGACY=1`` escape hatch."""

    def test_legacy_va_and_offset(self):
        self.assertEqual(SKIP_LOGO_SPEC.va, 0x05F6E0)
        self.assertEqual(SKIP_LOGO_SPEC.file_offset, 0x04F6E0)

    def test_legacy_patch_is_ten_nops(self):
        self.assertEqual(SKIP_LOGO_SPEC.patch, EXPECTED_LEGACY_PATCH)

    def test_legacy_same_length_as_original(self):
        self.assertEqual(len(SKIP_LOGO_SPEC.original),
                         len(SKIP_LOGO_SPEC.patch))
        self.assertEqual(len(SKIP_LOGO_SPEC.original), 10)


class RegistryEntry(unittest.TestCase):
    def test_pack_registered_as_trampoline_site(self):
        import azurik_mod.patches  # noqa: F401  triggers registration
        pack = get_pack("qol_skip_logo")
        self.assertFalse(pack.default_on,
            msg="qol_skip_logo must default to OFF")
        self.assertIn("qol", pack.tags)
        self.assertIn("c-shim", pack.tags,
            msg="A pack backed by a compiled shim should carry the "
                "`c-shim` tag so the GUI / docs can surface that.")
        self.assertEqual(len(pack.sites), 1)
        self.assertIs(pack.sites[0], SKIP_LOGO_TRAMPOLINE)


class LegacyEscapeHatch(unittest.TestCase):
    """Set ``AZURIK_SKIP_LOGO_LEGACY=1`` and the apply path falls back
    to the byte-NOP implementation without touching the shim pipeline.
    This matters: if a user's environment can't compile i386 PE-COFF
    (no clang, weird cross-toolchain), they need a way to still ship
    a patched XBE."""

    def _buf_with_vanilla_site(self) -> bytearray:
        buf = bytearray(1 * 1024 * 1024)
        off = SKIP_LOGO_TRAMPOLINE.file_offset
        buf[off:off + 10] = EXPECTED_ORIGINAL
        return buf

    def test_legacy_mode_writes_ten_nops(self):
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

        off = SKIP_LOGO_TRAMPOLINE.file_offset
        self.assertEqual(bytes(buf[off:off + 10]), EXPECTED_LEGACY_PATCH,
            msg="Under AZURIK_SKIP_LOGO_LEGACY=1 the site must be 10 "
                "NOPs (identical to the old patch).")
        self.assertEqual(verify_patch_spec(buf, SKIP_LOGO_SPEC), "applied",
            msg="verify_patch_spec should confirm the legacy patch "
                "was applied by the escape-hatch path.")


@unittest.skipUnless(_VANILLA_XBE.exists(),
    f"vanilla XBE fixture not available at {_VANILLA_XBE}")
class TrampolineEndToEnd(unittest.TestCase):
    """Build the shim, apply against the vanilla Azurik XBE, inspect
    the resulting trampoline bytes.  This is the real end-to-end
    proof — if this test passes the C-shim pipeline works on the
    exact binary the user will ship."""

    def setUp(self):
        # Make sure the shim is compiled before we try to apply it.
        shim_object = Path(_REPO_ROOT) / SKIP_LOGO_TRAMPOLINE.shim_object
        if not shim_object.exists():
            compile_sh = Path(_REPO_ROOT) / "shims/toolchain/compile.sh"
            src = Path(_REPO_ROOT) / "shims/src/skip_logo.c"
            if not compile_sh.exists() or not src.exists():
                self.skipTest("shim sources / toolchain script missing")
            try:
                subprocess.check_call(
                    ["bash", str(compile_sh), str(src)],
                    cwd=_REPO_ROOT)
            except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                self.skipTest(f"shim compile failed: {exc}")

    def test_trampoline_applies_and_verifies(self):
        xbe = bytearray(_VANILLA_XBE.read_bytes())
        # Sanity: vanilla bytes at the site match what we declared.
        off = SKIP_LOGO_TRAMPOLINE.file_offset
        self.assertEqual(
            bytes(xbe[off:off + 10]), EXPECTED_ORIGINAL,
            msg="vanilla XBE drift: replaced_bytes don't match file")

        apply_skip_logo_patch(xbe)

        trampoline = bytes(xbe[off:off + 10])
        self.assertEqual(trampoline[0], 0xE8,
            msg="trampoline must start with CALL rel32 (0xE8)")
        for i in range(5, 10):
            self.assertEqual(trampoline[i], 0x90,
                msg=f"byte at {off + i} must be a NOP pad (0x90)")
        self.assertEqual(
            verify_trampoline_patch(bytes(xbe), SKIP_LOGO_TRAMPOLINE),
            "applied")


if __name__ == "__main__":
    unittest.main()
