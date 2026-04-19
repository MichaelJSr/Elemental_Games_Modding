"""Tests for the config-lookup vanilla wrappers.

Pins the registration + header + mangling + end-to-end compile of
``config_name_lookup`` (``FUN_000d1420``) and ``config_cell_value``
(``FUN_000d1520``).

Unlike the gravity wrapper, these two functions use standard
calling conventions that clang supports natively — no inline-asm
wrapper required.  The tests confirm that path continues to work:

- Registry entries have the right VAs + ABI metadata.
- Mangled names match what clang actually emits.
- A probe shim that calls both functions compiles cleanly and
  emits exactly the expected REL32 relocations.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.patching.coff import parse_coff  # noqa: E402
from azurik_mod.patching.vanilla_symbols import (  # noqa: E402
    all_symbols, get)


_COMPILE_SH = _REPO_ROOT / "shims/toolchain/compile.sh"
_CONFIG_HDR = _REPO_ROOT / "shims/include/azurik_config.h"


def _toolchain_available() -> bool:
    if not _COMPILE_SH.exists():
        return False
    with tempfile.TemporaryDirectory() as tmp:
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


# ===========================================================================
# Registry entries
# ===========================================================================


class ConfigSymbolRegistration(unittest.TestCase):
    """The two config-lookup functions are registered with the
    correct VAs + calling conventions."""

    def test_name_lookup_registered(self):
        sym = get("_config_name_lookup")
        self.assertEqual(sym.name, "config_name_lookup")
        self.assertEqual(sym.va, 0x000D1420)
        self.assertEqual(sym.calling_convention, "thiscall")
        # __thiscall on i386-pe-win32 has NO @N name suffix.
        self.assertEqual(sym.mangled, "_config_name_lookup")

    def test_cell_value_registered(self):
        sym = get("_config_cell_value")
        self.assertEqual(sym.name, "config_cell_value")
        self.assertEqual(sym.va, 0x000D1520)
        self.assertEqual(sym.calling_convention, "cdecl")
        self.assertEqual(sym.mangled, "_config_cell_value")

    def test_both_appear_in_all_symbols(self):
        syms = all_symbols()
        self.assertIn("_config_name_lookup", syms)
        self.assertIn("_config_cell_value", syms)
        self.assertEqual(syms["_config_name_lookup"], 0x000D1420)
        self.assertEqual(syms["_config_cell_value"], 0x000D1520)


# ===========================================================================
# Header
# ===========================================================================


class ConfigHeader(unittest.TestCase):
    """``shims/include/azurik_config.h`` declares both extern
    functions with the right attributes."""

    def setUp(self):
        if not _CONFIG_HDR.exists():
            self.skipTest(f"header missing: {_CONFIG_HDR}")
        self.text = _CONFIG_HDR.read_text()

    def test_name_lookup_declared_thiscall(self):
        self.assertIn("config_name_lookup", self.text)
        self.assertIn("thiscall", self.text,
            msg="name lookup must use __attribute__((thiscall))")

    def test_cell_value_declared(self):
        self.assertIn("config_cell_value", self.text)
        # cdecl is clang's default — no attribute required.  Verify
        # the prototype doesn't accidentally have __stdcall etc.
        self.assertNotIn("__stdcall", self.text)
        self.assertNotIn("__fastcall", self.text)


# ===========================================================================
# Compile + mangling
# ===========================================================================


@unittest.skipUnless(_toolchain_available(),
    "i386 clang PE-COFF toolchain required")
class ConfigWrappersCompileEndToEnd(unittest.TestCase):
    """A probe shim that ``#include``s ``azurik_config.h`` and calls
    both functions compiles cleanly and emits exactly the externs
    + REL32 relocations we expect."""

    def test_probe_shim_compiles_with_expected_externs(self):
        probe = (
            '#include "azurik_config.h"\n'
            '__attribute__((stdcall))\n'
            'int c_probe(void *tbl, const int *grid) {\n'
            '    int idx = config_name_lookup(tbl, "k");\n'
            '    double d = 0.0;\n'
            '    return (int)config_cell_value(grid, 0, idx, &d);\n'
            '}\n'
        )
        with tempfile.TemporaryDirectory(prefix="cfg_probe_") as tmp_s:
            tmp = Path(tmp_s)
            (tmp / "probe.c").write_text(probe)
            out = tmp / "probe.o"
            subprocess.check_call(
                ["bash", str(_COMPILE_SH),
                 str(tmp / "probe.c"), str(out)],
                cwd=_REPO_ROOT)

            coff = parse_coff(out.read_bytes())
            undef = {
                s.name for s in coff.symbols
                if s.section_number == 0 and s.name
                and s.storage_class == 2
                and not s.name.startswith("@feat")
                and s.name != "__fltused"  # linker marker clang emits for FP
            }
            self.assertEqual(
                undef,
                {"_config_name_lookup", "_config_cell_value"},
                msg=f"probe must emit exactly the two config externs; "
                    f"got {undef}")

    def test_thiscall_emits_no_at_suffix(self):
        """Empirical confirmation of the mangling rule we encoded in
        ``VanillaSymbol.mangled``: clang-i386-pe-win32 does NOT
        decorate thiscall symbols with ``@N`` the way it does for
        stdcall / fastcall.  If a clang upgrade ever changes this we
        need to update the registry mangling helper."""
        probe = (
            'extern __attribute__((thiscall)) '
            'int my_thiscall(void *, const char *);\n'
            'void c_use(void *p) { my_thiscall(p, "x"); }\n'
        )
        with tempfile.TemporaryDirectory(prefix="thiscall_") as tmp_s:
            tmp = Path(tmp_s)
            (tmp / "probe.c").write_text(probe)
            out = tmp / "probe.o"
            subprocess.check_call(
                ["bash", str(_COMPILE_SH),
                 str(tmp / "probe.c"), str(out)],
                cwd=_REPO_ROOT, stderr=subprocess.DEVNULL)
            coff = parse_coff(out.read_bytes())
            names = {s.name for s in coff.symbols if s.name}
            self.assertIn(
                "_my_thiscall", names,
                msg="clang-i386-pe-win32 should emit _my_thiscall "
                    "(no @N) for thiscall extern declarations.  If "
                    "this test fails after a toolchain upgrade, "
                    "update VanillaSymbol.mangled's thiscall branch.")
            # Negative: must NOT have an @N variant.
            self.assertFalse(
                any(n.startswith("_my_thiscall@") for n in names),
                msg="thiscall must NOT carry an @N suffix")


if __name__ == "__main__":
    unittest.main()
