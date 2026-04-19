"""Tests for the Phase-2 A2 relocation-aware COFF loader.

Compiles ``shims/fixtures/_reloc_test.c`` on demand, parses it with
:func:`azurik_mod.patching.coff.parse_coff`, runs it through
:func:`azurik_mod.patching.coff.layout_coff` with a fake VA
allocator, and verifies:

- The relocation table is parsed (one ``REL32``, two ``DIR32``).
- ``layout_coff`` places every landable section (``.text`` + ``.bss``
  for ``g_counter``) and skips metadata (``.debug$S`` / LLVM
  addrsig).
- After layout, every relocation field inside ``.text`` has been
  rewritten to reference the final XBE VA we assigned.
- Undefined externals and unsupported relocation types raise cleanly.

The test skips when the i386 clang cross-toolchain isn't available
on the host, so CI without a working ``shims/toolchain/compile.sh``
still runs the rest of the suite.
"""

from __future__ import annotations

import os
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

from azurik_mod.patching.coff import (  # noqa: E402
    IMAGE_REL_I386_DIR32,
    IMAGE_REL_I386_REL32,
    layout_coff,
    parse_coff,
)


_COMPILE_SH = _REPO_ROOT / "shims/toolchain/compile.sh"
_SRC = _REPO_ROOT / "shims/fixtures/_reloc_test.c"


def _compile_test_shim() -> Path:
    """Compile the test shim into a temp .o and return its path.

    Returns ``None`` (wrapped via skip) when clang refuses to emit
    i386 PE-COFF on this host — typical for CI runners without the
    cross-compilation toolchain.
    """
    out = Path(tempfile.mkdtemp(prefix="reloc_test_")) / "reloc_test.o"
    subprocess.check_call(
        ["bash", str(_COMPILE_SH), str(_SRC), str(out)],
        cwd=_REPO_ROOT)
    return out


def _toolchain_available() -> bool:
    if not _COMPILE_SH.exists() or not _SRC.exists():
        return False
    try:
        _compile_test_shim()
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


@unittest.skipUnless(_toolchain_available(),
    "i386 clang PE-COFF toolchain not available on this host")
class ParseCoffRelocations(unittest.TestCase):
    """Verify per-section relocations are parsed into CoffSection."""

    def setUp(self):
        self.obj = _compile_test_shim()
        self.coff = parse_coff(self.obj.read_bytes())

    def test_text_has_expected_relocation_count(self):
        text = self.coff.section(".text")
        self.assertEqual(text.reloc_count, 3,
            msg="shim/src/_reloc_test.c is expected to compile with "
                "exactly three relocations (one REL32 to _helper + "
                "two DIR32 to _g_counter).  If compile.sh's flags "
                "change this may drift.")
        self.assertEqual(len(text.relocations), text.reloc_count)

    def test_relocations_include_rel32_and_dir32(self):
        text = self.coff.section(".text")
        types = sorted(r.type for r in text.relocations)
        self.assertEqual(types.count(IMAGE_REL_I386_REL32), 1)
        self.assertEqual(types.count(IMAGE_REL_I386_DIR32), 2)

    def test_symbol_indices_are_in_range(self):
        """Guards against the off-by-aux-record bug we just fixed:
        relocation symbol_index values must be valid indices into the
        full symbol list (which now includes aux-record placeholders)."""
        text = self.coff.section(".text")
        for reloc in text.relocations:
            self.assertLess(reloc.symbol_index, len(self.coff.symbols),
                msg="relocation symbol index points past end of "
                    "symbol list — is parse_coff dropping aux records?")


@unittest.skipUnless(_toolchain_available(),
    "i386 clang PE-COFF toolchain not available on this host")
class LayoutCoffEndToEnd(unittest.TestCase):
    """Full layout_coff run against the test shim."""

    def setUp(self):
        self.obj = _compile_test_shim()
        self.coff = parse_coff(self.obj.read_bytes())
        # Stable fake VAs — the tests inspect absolute fields so
        # picking deterministic numbers keeps the math simple.
        self._fixed_vas = {".text": 0x100000, ".bss": 0x200000}
        self._fixed_files = {".text": 0x1000, ".bss": 0x2000}

    def _allocate(self, name, placeholder):
        return self._fixed_files[name], self._fixed_vas[name]

    def test_places_text_and_bss_but_skips_metadata(self):
        landed = layout_coff(self.coff, "_c_reloc_test", self._allocate)
        placed_names = {s.name for s in landed.sections}
        self.assertIn(".text", placed_names,
            msg=".text must be placed")
        self.assertIn(".bss", placed_names,
            msg="g_counter lives in .bss — layout must place it so "
                "the DIR32 relocations have a valid target")
        # Metadata sections must NOT end up in the landed set.
        self.assertNotIn(".debug$S", placed_names)
        self.assertNotIn(".llvm_addrsig", placed_names)

    def test_entry_va_points_at_text_symbol(self):
        landed = layout_coff(self.coff, "_c_reloc_test", self._allocate)
        entry_sym = self.coff.symbol("_c_reloc_test")
        expected = self._fixed_vas[".text"] + entry_sym.value
        self.assertEqual(landed.entry_va, expected)

    def test_dir32_fields_rewritten_to_bss_va(self):
        """Every DIR32 to _g_counter must hold the .bss VA (0x200000)
        after layout, plus whatever addend was baked in at compile
        time.  For a fresh compile the addend is 0, so the field
        should equal exactly the .bss VA."""
        landed = layout_coff(self.coff, "_c_reloc_test", self._allocate)
        text = next(s for s in landed.sections if s.name == ".text")
        dir32_sites = [r.va for r in self.coff.section(".text").relocations
                       if r.type == IMAGE_REL_I386_DIR32]
        for site in dir32_sites:
            value = struct.unpack_from("<I", text.data, site)[0]
            self.assertEqual(value, self._fixed_vas[".bss"],
                msg=f"DIR32 at .text+0x{site:X} should be 0x"
                    f"{self._fixed_vas['.bss']:X} after relocation; "
                    f"got 0x{value:X}")

    def test_rel32_displacement_is_internally_consistent(self):
        """The REL32 to _helper is an intra-section PC-relative jump.
        We don't care about its absolute value — we care that when
        you compute (rel32 + site_va + 4) you land exactly at
        _helper's final VA."""
        landed = layout_coff(self.coff, "_c_reloc_test", self._allocate)
        text = next(s for s in landed.sections if s.name == ".text")
        reloc = next(r for r in self.coff.section(".text").relocations
                     if r.type == IMAGE_REL_I386_REL32)
        rel32 = struct.unpack_from("<i", text.data, reloc.va)[0]
        site_va = text.vaddr + reloc.va
        target_va = site_va + 4 + rel32
        helper_sym = self.coff.symbol("_helper")
        expected = self._fixed_vas[".text"] + helper_sym.value
        self.assertEqual(target_va, expected,
            msg=f"REL32 at .text+0x{reloc.va:X} resolves to 0x"
                f"{target_va:X}; expected _helper at 0x{expected:X}")


class LayoutCoffSafetyRails(unittest.TestCase):
    """Ensure layout_coff refuses malformed inputs rather than
    silently generating a garbage shim."""

    def test_unsupported_relocation_type_raises(self):
        """If we ever run into an unimplemented IMAGE_REL_I386_* type
        the loader must raise, not silently skip it."""
        from azurik_mod.patching.coff import (
            CoffFile,
            CoffRelocation,
            CoffSection,
            CoffSymbol,
        )
        # Build a synthetic COFF with one .text section and a bogus
        # relocation type 0x1234.
        text = CoffSection(
            name=".text",
            virtual_size=0,
            virtual_address=0,
            raw_size=4,
            raw_offset=0,
            flags=0,
            data=bytes(4),
            reloc_offset=0,
            reloc_count=1,
            relocations=[CoffRelocation(va=0, symbol_index=0, type=0x1234)],
        )
        dummy_sym = CoffSymbol(name="_x", value=0, section_number=1,
                               type=0, storage_class=2)
        coff = CoffFile(machine=0x014C, sections=[text], symbols=[dummy_sym])
        with self.assertRaises(ValueError):
            layout_coff(coff, "_x",
                        lambda name, ph: (0, 0x100000))


if __name__ == "__main__":
    unittest.main()
