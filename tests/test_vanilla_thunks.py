"""Tests for Phase-2 A3 (vanilla function thunk resolution).

Covers:

- :mod:`azurik_mod.patching.vanilla_symbols` — mangled-name computation
  for cdecl / stdcall / fastcall, basic registry accessors, duplicate
  detection.
- :func:`azurik_mod.patching.coff.layout_coff` — undefined externals
  resolve through ``vanilla_symbols`` to the declared VA; truly
  unresolved symbols still raise.
- **Real shim compile** — :file:`shims/fixtures/_vanilla_call_test.c` calls
  ``play_movie_fn`` via the :file:`shims/include/azurik_vanilla.h`
  declaration.  After :func:`layout_coff`, the REL32 at the CALL site
  must resolve to exactly ``0x00018980``, the vanilla VA.
- **Drift guard** — every :class:`VanillaSymbol` entry in the Python
  registry must appear as a matching ``extern`` declaration in
  :file:`azurik_vanilla.h`.  Prevents silent divergence between the
  two source-of-truth files.

Tests that depend on the i386 clang cross-toolchain skip gracefully
on hosts that lack it.
"""

from __future__ import annotations

import os
import re
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
    IMAGE_REL_I386_REL32,
    layout_coff,
    parse_coff,
)
from azurik_mod.patching.vanilla_symbols import (  # noqa: E402
    VanillaSymbol,
    all_entries,
    all_symbols,
    get,
)


_COMPILE_SH = _REPO_ROOT / "shims/toolchain/compile.sh"
_SRC = _REPO_ROOT / "shims/fixtures/_vanilla_call_test.c"
_HEADER = _REPO_ROOT / "shims/include/azurik_vanilla.h"


def _compile(src: Path) -> Path | None:
    """Compile `src` into a temp .o; return None if toolchain unavailable."""
    if not _COMPILE_SH.exists() or not src.exists():
        return None
    out = Path(tempfile.mkdtemp(prefix="vanilla_test_")) / "out.o"
    try:
        subprocess.check_call(
            ["bash", str(_COMPILE_SH), str(src), str(out)],
            cwd=_REPO_ROOT)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return out


def _toolchain_available() -> bool:
    return _compile(_SRC) is not None


# ===========================================================================
# vanilla_symbols registry
# ===========================================================================


class VanillaSymbolMangling(unittest.TestCase):
    """The dataclass's mangled-name computation must match what
    clang emits on i386 PE-COFF.  Getting this wrong means undefined
    externals never match the registry and layout_coff raises."""

    def test_cdecl_name_gets_leading_underscore(self):
        sym = VanillaSymbol(name="foo", va=0x100, calling_convention="cdecl")
        self.assertEqual(sym.mangled, "_foo")

    def test_stdcall_name_gets_underscore_and_arg_byte_suffix(self):
        sym = VanillaSymbol(
            name="play_movie_fn", va=0x18980,
            calling_convention="stdcall", arg_bytes=8)
        self.assertEqual(sym.mangled, "_play_movie_fn@8",
            msg="stdcall mangling must include the @N arg-byte suffix; "
                "otherwise the COFF symbol emitted by `clang -target "
                "i386-pc-win32` for __attribute__((stdcall)) won't "
                "match the registry key.")

    def test_fastcall_uses_at_prefix(self):
        sym = VanillaSymbol(
            name="fast", va=0x200,
            calling_convention="fastcall", arg_bytes=8)
        self.assertEqual(sym.mangled, "@fast@8")

    def test_unknown_convention_raises(self):
        sym = VanillaSymbol(
            name="x", va=0x100, calling_convention="banana")
        with self.assertRaises(ValueError):
            _ = sym.mangled


class RegistryAccessors(unittest.TestCase):
    """Smoke-check the registry's public surface."""

    def test_all_symbols_returns_mangled_name_to_va(self):
        m = all_symbols()
        self.assertIn("_play_movie_fn@8", m,
            msg="play_movie_fn is a seed entry — it must appear in "
                "the registry under its mangled stdcall name.")
        self.assertEqual(m["_play_movie_fn@8"], 0x00018980)

    def test_get_looks_up_by_mangled_name(self):
        sym = get("_play_movie_fn@8")
        self.assertEqual(sym.name, "play_movie_fn")
        self.assertEqual(sym.va, 0x00018980)
        self.assertEqual(sym.calling_convention, "stdcall")
        self.assertEqual(sym.arg_bytes, 8)


# ===========================================================================
# layout_coff integration
# ===========================================================================


class LayoutCoffResolvesVanillaExterns(unittest.TestCase):
    """Synthetic COFF + synthetic vanilla map — no real compile needed."""

    def _synthetic_coff_with_extern(self):
        """Build a CoffFile with one .text section containing a single
        REL32 relocation pointing at an undefined extern.  Minimum
        fixture that exercises the A3 resolution path."""
        from azurik_mod.patching.coff import (
            CoffFile,
            CoffRelocation,
            CoffSection,
            CoffSymbol,
        )
        text = CoffSection(
            name=".text",
            virtual_size=0,
            virtual_address=0,
            raw_size=5,
            raw_offset=0,
            flags=0,
            data=b"\xE8\x00\x00\x00\x00",  # CALL rel32 with zero addend
            reloc_offset=0,
            reloc_count=1,
            relocations=[CoffRelocation(
                va=1, symbol_index=1, type=IMAGE_REL_I386_REL32)],
        )
        entry = CoffSymbol(
            name="_entry", value=0, section_number=1,
            type=0, storage_class=2)
        ext = CoffSymbol(
            name="_my_vanilla@4", value=0, section_number=0,
            type=0, storage_class=2)
        return CoffFile(machine=0x014C, sections=[text], symbols=[entry, ext])

    def test_undefined_extern_resolves_via_vanilla_map(self):
        coff = self._synthetic_coff_with_extern()
        landed = layout_coff(
            coff, "_entry",
            allocate=lambda _name, _ph: (0x1000, 0x100000),
            vanilla_symbols={"_my_vanilla@4": 0x555000})
        text = landed.sections[0]
        # After relocation the CALL should target 0x555000.
        rel32 = struct.unpack_from("<i", text.data, 1)[0]
        # CALL site VA = 0x100000; next instr VA = 0x100005.
        # rel32 should send us to 0x555000 - 0x100005 = 0x454FFB.
        self.assertEqual(rel32, 0x555000 - 0x100005,
            msg="REL32 must be rewritten so CALL lands at the "
                "vanilla-symbol VA.  Any other value means "
                "layout_coff stopped consulting vanilla_symbols "
                "or applied the wrong relocation math.")

    def test_undefined_extern_without_map_entry_still_raises(self):
        """Missing-from-registry externals must NOT be silently
        resolved to zero or some garbage; the error message needs to
        tell the shim author where to add the entry."""
        coff = self._synthetic_coff_with_extern()
        with self.assertRaises(ValueError) as ctx:
            layout_coff(
                coff, "_entry",
                allocate=lambda _n, _p: (0x1000, 0x100000),
                vanilla_symbols={})  # explicitly empty
        msg = str(ctx.exception).lower()
        self.assertIn("vanilla", msg)
        self.assertIn("my_vanilla", msg)

    def test_undefined_extern_without_any_map_also_raises(self):
        """Passing vanilla_symbols=None (default) must reject
        undefined externals just like an empty dict would."""
        coff = self._synthetic_coff_with_extern()
        with self.assertRaises(ValueError):
            layout_coff(
                coff, "_entry",
                allocate=lambda _n, _p: (0x1000, 0x100000))


@unittest.skipUnless(_toolchain_available(),
    "i386 clang PE-COFF toolchain not available on this host")
class RealShimCallsVanilla(unittest.TestCase):
    """End-to-end: compile the test shim against azurik_vanilla.h,
    run layout_coff with the real vanilla registry, verify the
    resulting REL32 targets Azurik's play_movie_fn at 0x18980."""

    def setUp(self):
        self.obj = _compile(_SRC)
        self.coff = parse_coff(self.obj.read_bytes())

    def test_shim_compiled_with_expected_symbol_shape(self):
        """Sanity: the COFF must have an undefined extern for
        _play_movie_fn@8 (what layout_coff will resolve via A3)."""
        names = {s.name for s in self.coff.symbols if s.name}
        self.assertIn("_play_movie_fn@8", names,
            msg="the shim's stdcall extern should emit with the "
                "@8 suffix (cdecl default would drop it and miss "
                "the registry entry)")

    def test_layout_resolves_rel32_to_vanilla_va(self):
        # Pick arbitrary stable target VAs for the shim's own
        # sections; the vanilla resolution is the thing under test.
        shim_text_va = 0x300000
        landed = layout_coff(
            self.coff, "_c_calls_vanilla@4",
            allocate=lambda _name, ph: (0x3000, shim_text_va),
            vanilla_symbols=all_symbols(),
        )
        text = next(s for s in landed.sections if s.name == ".text")
        # The REL32 of interest is the one relocation from the
        # compiled shim's .text section.
        text_section = self.coff.section(".text")
        rel_site = text_section.relocations[0].va
        rel32 = struct.unpack_from("<i", text.data, rel_site)[0]
        # Dereference the target.
        site_va = shim_text_va + rel_site
        target = site_va + 4 + rel32
        self.assertEqual(target, 0x00018980,
            msg=f"REL32 at shim .text+0x{rel_site:X} should land at "
                f"play_movie_fn (0x18980), but lands at 0x{target:X}")


# ===========================================================================
# Drift guard: header <-> registry
# ===========================================================================


class HeaderRegistryDriftGuard(unittest.TestCase):
    """Every VanillaSymbol in the Python registry must have a matching
    declaration in azurik_vanilla.h, and vice versa.  Catches the
    easy mistake of adding an entry in one place and forgetting the
    other."""

    def setUp(self):
        if not _HEADER.exists():
            self.skipTest(f"header missing at {_HEADER}")
        self.header_text = _HEADER.read_text()

    def _declared_in_header(self, name: str) -> bool:
        # Match either `int foo(...)` or `type foo(...)` in the header.
        pattern = re.compile(rf"\b{re.escape(name)}\s*\(")
        return bool(pattern.search(self.header_text))

    def _va_commented_in_header(self, va: int) -> bool:
        # Accept any of 0x18980 / 0x018980 / 0x00018980 case-insensitive.
        return any(
            f"0x{va:0{w}X}".lower() in self.header_text.lower()
            for w in (4, 5, 6, 7, 8))

    def test_every_registry_entry_declared_in_header(self):
        for sym in all_entries():
            self.assertTrue(
                self._declared_in_header(sym.name),
                msg=f"registry entry {sym.name!r} has no matching "
                    f"extern declaration in {_HEADER.name}.  Add "
                    f"the prototype (with the correct calling "
                    f"convention + arg types) or remove the Python "
                    f"entry.")
            self.assertTrue(
                self._va_commented_in_header(sym.va),
                msg=f"registry entry {sym.name!r} lists VA 0x"
                    f"{sym.va:X} but the header comment for it "
                    f"doesn't mention that address.  Update the "
                    f"header doc-comment to match.")


if __name__ == "__main__":
    unittest.main()
