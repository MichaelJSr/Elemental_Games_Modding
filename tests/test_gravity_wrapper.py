"""Tests for the gravity-integration wrapper shim (``FUN_00085700``).

The wrapper lives at ``shims/shared/gravity_integrate.c`` and exposes
a clean ``stdcall(20)`` API that internally calls the vanilla
MSVC-RVO function.  These tests pin:

1. **Vanilla-registry entry**: ``gravity_integrate_raw`` is
   registered with the right VA / calling convention / arg_bytes,
   and its mangled name matches what the wrapper's CALL
   instruction references.

2. **Wrapper compilation + byte shape**: the ``.c`` file compiles
   under the shim toolchain, emits exactly one REL32 relocation
   (against ``@gravity_integrate_raw@8``), and has no other
   undefined externs.  The generated code sets up EAX and ESI
   inside a single basic block immediately before the CALL.

3. **End-to-end layout_coff integration**: compile the wrapper,
   lay it out against a scratch XBE, confirm the REL32 resolves
   to the real vanilla VA ``0x00085700`` with no shifting.

4. **Header drift**: ``azurik_gravity.h`` declares both the
   internal raw symbol and the clean wrapper prototype.
"""

from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.patching.coff import parse_coff, layout_coff  # noqa: E402
from azurik_mod.patching.vanilla_symbols import get, all_symbols  # noqa: E402


_COMPILE_SH = _REPO_ROOT / "shims/toolchain/compile.sh"
_WRAPPER_SRC = _REPO_ROOT / "shims/shared/gravity_integrate.c"
_WRAPPER_HDR = _REPO_ROOT / "shims/include/azurik_gravity.h"
_VANILLA_XBE = (_REPO_ROOT.parent /
                "Azurik - Rise of Perathia (USA).xiso/default.xbe")


def _toolchain_available() -> bool:
    if not _COMPILE_SH.exists():
        return False
    with tempfile.TemporaryDirectory(prefix="gi_probe_") as tmp:
        src = Path(tmp) / "probe.c"
        src.write_text("void c_probe(void){}\n")
        try:
            subprocess.check_call(
                ["bash", str(_COMPILE_SH), str(src),
                 str(Path(tmp) / "probe.o")],
                cwd=_REPO_ROOT, stderr=subprocess.DEVNULL)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False


class VanillaRegistryEntry(unittest.TestCase):
    """Pin the registration of the raw gravity symbol."""

    def test_entry_exists(self):
        sym = get("@gravity_integrate_raw@8")
        self.assertEqual(sym.name, "gravity_integrate_raw")

    def test_va_is_correct(self):
        self.assertEqual(
            get("@gravity_integrate_raw@8").va, 0x00085700,
            msg="gravity_integrate_raw must resolve to VA 0x00085700 "
                "(confirmed by Ghidra + call-site byte pattern). A "
                "change here breaks every shim that uses the gravity "
                "wrapper.")

    def test_abi_is_fastcall8(self):
        sym = get("@gravity_integrate_raw@8")
        self.assertEqual(sym.calling_convention, "fastcall")
        self.assertEqual(sym.arg_bytes, 8)

    def test_mangled_name_matches_wrapper_reference(self):
        """The wrapper's CALL references ``@gravity_integrate_raw@8``;
        the registry's mangled name must be that exact string."""
        sym = get("@gravity_integrate_raw@8")
        self.assertEqual(sym.mangled, "@gravity_integrate_raw@8")

    def test_registry_entry_in_all_symbols(self):
        symbols = all_symbols()
        self.assertIn("@gravity_integrate_raw@8", symbols)
        self.assertEqual(symbols["@gravity_integrate_raw@8"], 0x00085700)


@unittest.skipUnless(_toolchain_available(),
    "i386 clang PE-COFF toolchain required for wrapper compile tests")
class WrapperCompile(unittest.TestCase):
    """The wrapper .c file compiles cleanly with the expected shape."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="gi_wrap_")
        cls.obj_path = Path(cls.tmp.name) / "gravity_integrate.o"
        subprocess.check_call(
            ["bash", str(_COMPILE_SH), str(_WRAPPER_SRC), str(cls.obj_path)],
            cwd=_REPO_ROOT)
        cls.coff = parse_coff(cls.obj_path.read_bytes())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_compiles(self):
        self.assertTrue(self.obj_path.exists())

    def test_wrapper_symbol_defined(self):
        names = {s.name for s in self.coff.symbols if s.name}
        self.assertIn(
            "_azurik_gravity_integrate@20", names,
            msg="wrapper's stdcall(20) symbol must be defined — "
                "shims reference it by that exact name")

    def test_single_undefined_extern(self):
        """The wrapper's only external dependency should be the
        raw vanilla function.  ``__fltused`` is satisfied locally
        via the asm-label override."""
        undef = [
            s.name for s in self.coff.symbols
            if s.section_number == 0 and s.name
            and not s.name.startswith("@feat")
            and s.storage_class == 2]
        self.assertEqual(
            undef, ["@gravity_integrate_raw@8"],
            msg=f"wrapper has unexpected externs: {undef}. Only "
                f"@gravity_integrate_raw@8 should remain undefined; "
                f"__fltused should be satisfied by the local "
                f"asm-label definition.")

    def test_single_rel32_to_vanilla(self):
        text = self.coff.section(".text")
        rel_targets = [
            self.coff.symbols[r.symbol_index].name
            for r in text.relocations]
        self.assertEqual(
            rel_targets.count("@gravity_integrate_raw@8"), 1,
            msg="wrapper must emit exactly one REL32 relocation "
                "against the raw vanilla symbol")


@unittest.skipUnless(_toolchain_available() and _VANILLA_XBE.exists(),
    "E2E test needs both toolchain and vanilla XBE")
class WrapperRelocationResolvesToVanillaVA(unittest.TestCase):
    """After lay-out_coff, the wrapper's CALL lands at 0x00085700."""

    def test_rel32_resolves_to_gravity_vanilla(self):
        import tempfile, subprocess
        with tempfile.TemporaryDirectory(prefix="gi_e2e_") as tmp:
            obj = Path(tmp) / "gi.o"
            subprocess.check_call(
                ["bash", str(_COMPILE_SH), str(_WRAPPER_SRC), str(obj)],
                cwd=_REPO_ROOT)
            coff = parse_coff(obj.read_bytes())

        cursor = [0x400000]
        scratch = bytearray(0x1000)

        def _allocate(name, placeholder):
            off = cursor[0]
            va = 0x500000 + off
            sz = len(placeholder)
            scratch[off:off + sz] = placeholder
            cursor[0] += max(sz, 4)
            return off, va

        landed = layout_coff(
            coff,
            entry_symbol="_azurik_gravity_integrate@20",
            allocate=_allocate,
            vanilla_symbols=all_symbols(),
            extern_resolver=None)

        text = next(s for s in landed.sections if s.name == ".text")
        # The only REL32 in .text targets @gravity_integrate_raw@8.
        text_section = coff.section(".text")
        rel = text_section.relocations[0]
        rel_site = rel.va
        rel32 = struct.unpack_from("<i", text.data, rel_site)[0]
        site_va = text.vaddr + rel_site
        target = site_va + 4 + rel32
        self.assertEqual(
            target, 0x00085700,
            msg=f"wrapper's CALL REL32 should land at vanilla VA "
                f"0x85700 (FUN_00085700), but lands at 0x{target:X}. "
                f"Either vanilla_symbols.py has the wrong VA, the "
                f"mangled name has drifted, or layout_coff has a bug.")


class HeaderHasBothDeclarations(unittest.TestCase):
    """``azurik_gravity.h`` must declare both the raw symbol and
    the clean wrapper, with the internal-use warning intact."""

    def setUp(self):
        if not _WRAPPER_HDR.exists():
            self.skipTest(f"header missing at {_WRAPPER_HDR}")
        self.text = _WRAPPER_HDR.read_text()

    def test_raw_symbol_declared(self):
        self.assertIn("gravity_integrate_raw", self.text,
            msg="header must declare the raw symbol for drift-guard "
                "purposes even though shim authors shouldn't call it")

    def test_wrapper_declared_with_stdcall20(self):
        # Wrapper has 5 arg pointers / floats = 20 bytes total.
        self.assertIn("azurik_gravity_integrate", self.text)
        self.assertIn("stdcall", self.text,
            msg="wrapper must be declared __attribute__((stdcall)) "
                "so the CALL-site ABI matches what's compiled into "
                "shims/shared/gravity_integrate.c")

    def test_internal_warning_present(self):
        """The header should make it unmistakable that
        gravity_integrate_raw is NOT for direct shim use."""
        lower = self.text.lower()
        self.assertTrue(
            any(phrase in lower for phrase in (
                "not for direct use",
                "do not call",
                "deliberate lie")),
            msg="the raw-symbol block should carry a visible "
                "warning against direct shim invocation")


if __name__ == "__main__":
    unittest.main()
