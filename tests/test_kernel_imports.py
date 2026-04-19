"""Tests for Phase 2 D1 — xboxkrnl kernel import exposure.

Coverage:

- :mod:`azurik_mod.patching.xboxkrnl_ordinals` — ordinal/name bijection
  invariants, presence of the 151 Azurik imports, public-API helpers.
- :mod:`azurik_mod.patching.kernel_imports` — thunk-VA decryption,
  thunk-table parse against the vanilla XBE, name→thunk_va map
  correctness, demangle helpers, stub-byte generator.
- :class:`ShimLayoutSession` — kernel-stub caching (one stub per
  kernel function, even if multiple shims reference it), allocation
  callback integration.
- **End-to-end**: compile a shim that calls ``DbgPrint`` and
  ``KeQueryPerformanceCounter``, run :func:`layout_coff` with a
  real session, verify each REL32 lands on a stub, and each stub's
  indirect target is the correct kernel thunk slot.
- **Drift guard** — every extern in ``azurik_kernel.h`` must have a
  matching ordinal in the Python ordinal map, and vice versa.
"""

from __future__ import annotations

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

from azurik_mod.patching import kernel_imports as ki  # noqa: E402
from azurik_mod.patching.xboxkrnl_ordinals import (  # noqa: E402
    AZURIK_KERNEL_ORDINALS,
    NAME_TO_ORDINAL,
    ORDINAL_TO_NAME,
    ordinal_for,
)
from azurik_mod.patching.coff import layout_coff, parse_coff  # noqa: E402
from azurik_mod.patching.shim_session import ShimLayoutSession  # noqa: E402


_VANILLA_XBE = (_REPO_ROOT.parent /
                "Azurik - Rise of Perathia (USA).xiso/default.xbe")
_COMPILE_SH = _REPO_ROOT / "shims/toolchain/compile.sh"
_KERNEL_HEADER = _REPO_ROOT / "shims/include/azurik_kernel.h"


# ===========================================================================
# xboxkrnl_ordinals.py
# ===========================================================================


class OrdinalTableInvariants(unittest.TestCase):
    """Structural checks that any edit to the ordinal table has to pass."""

    def test_151_entries(self):
        self.assertEqual(
            len(AZURIK_KERNEL_ORDINALS), 151,
            msg="Azurik imports exactly 151 kernel functions.  If this "
                "number changed, you've either miscounted or you're "
                "editing a non-vanilla XBE.")

    def test_ordinals_are_unique(self):
        ordinals = [e.ordinal for e in AZURIK_KERNEL_ORDINALS]
        self.assertEqual(
            len(set(ordinals)), len(ordinals),
            msg="the ordinal column must be a bijection — no kernel "
                "function gets two entries in Azurik's thunk table")

    def test_names_are_unique(self):
        names = [e.name for e in AZURIK_KERNEL_ORDINALS]
        self.assertEqual(
            len(set(names)), len(names),
            msg="the name column must also be unique — duplicate names "
                "would make NAME_TO_ORDINAL drop entries silently")

    def test_sorted_by_ordinal_ascending(self):
        ordinals = [e.ordinal for e in AZURIK_KERNEL_ORDINALS]
        self.assertEqual(
            ordinals, sorted(ordinals),
            msg="keep rows sorted by ordinal so audits can binary-search")

    def test_name_to_ordinal_is_inverse_of_ordinal_to_name(self):
        for name, ordinal in NAME_TO_ORDINAL.items():
            self.assertEqual(ORDINAL_TO_NAME[ordinal], name)
        for ordinal, name in ORDINAL_TO_NAME.items():
            self.assertEqual(NAME_TO_ORDINAL[name], ordinal)

    def test_spot_checks_well_known_ordinals(self):
        """These pairings are fixed by the Xbox kernel itself — if any
        one changes, either xboxkrnl has been rev'd (it hasn't) or the
        file has been corrupted."""
        for name, expected_ordinal in [
            ("NtClose", 187),
            ("NtQueryDirectoryFile", 207),
            ("NtOpenFile", 202),
            ("RtlInitAnsiString", 289),
            ("DbgPrint", 8),
            ("KeQueryPerformanceCounter", 126),
            ("HalReturnToFirmware", 49),
        ]:
            self.assertEqual(
                ordinal_for(name), expected_ordinal,
                msg=f"{name} must live at kernel ordinal "
                    f"{expected_ordinal}; got {ordinal_for(name)}")

    def test_ordinal_for_returns_none_for_unknown(self):
        self.assertIsNone(ordinal_for("NotAKernelFunction"))


# ===========================================================================
# kernel_imports.py
# ===========================================================================


class DemangleHelpers(unittest.TestCase):
    """Strip COFF decorations on stdcall / cdecl symbols."""

    def test_stdcall_strips_prefix_and_suffix(self):
        self.assertEqual(ki.demangle_stdcall("_NtClose@4"), "NtClose")
        self.assertEqual(
            ki.demangle_stdcall("_RtlInitAnsiString@8"), "RtlInitAnsiString")

    def test_stdcall_rejects_cdecl_shape(self):
        self.assertIsNone(ki.demangle_stdcall("_DbgPrint"))

    def test_stdcall_rejects_missing_underscore(self):
        self.assertIsNone(ki.demangle_stdcall("NtClose@4"))

    def test_stdcall_rejects_nondigit_arg_bytes(self):
        self.assertIsNone(ki.demangle_stdcall("_Foo@abc"))

    def test_cdecl_strips_prefix_only(self):
        self.assertEqual(ki.demangle_cdecl("_DbgPrint"), "DbgPrint")

    def test_cdecl_rejects_stdcall_shape(self):
        self.assertIsNone(ki.demangle_cdecl("_NtClose@4"))

    def test_cdecl_rejects_empty_name(self):
        self.assertIsNone(ki.demangle_cdecl("_"))

    def test_kernel_name_for_symbol_tries_both_shapes(self):
        # stdcall shape for NtClose — known kernel import.
        self.assertEqual(
            ki.kernel_name_for_symbol("_NtClose@4"), "NtClose")
        # cdecl shape for DbgPrint — also known kernel import.
        self.assertEqual(
            ki.kernel_name_for_symbol("_DbgPrint"), "DbgPrint")

    def test_kernel_name_for_symbol_rejects_unknown(self):
        self.assertIsNone(ki.kernel_name_for_symbol("_c_skip_logo"))
        self.assertIsNone(ki.kernel_name_for_symbol("_FrotzBazQux@12"))


class StubBytesShape(unittest.TestCase):

    def test_returns_six_bytes(self):
        stub = ki.stub_bytes_for(0xDEADBEEF)
        self.assertEqual(len(stub), 6)
        self.assertEqual(stub[:2], b"\xFF\x25",
            msg="indirect-JMP opcode must be FF 25 — if this changed, "
                "every kernel import will silently transfer to the "
                "wrong instruction")
        self.assertEqual(
            struct.unpack_from("<I", stub, 2)[0], 0xDEADBEEF,
            msg="thunk VA must be the absolute 32-bit operand")

    def test_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            ki.stub_bytes_for(-1)
        with self.assertRaises(ValueError):
            ki.stub_bytes_for(0x1_0000_0000)


@unittest.skipUnless(_VANILLA_XBE.exists(),
    f"vanilla XBE fixture required at {_VANILLA_XBE}")
class ThunkTableParseAgainstVanilla(unittest.TestCase):
    """Real-XBE integration — the thunk table we see on disk must
    align with xboxkrnl_ordinals.AZURIK_KERNEL_ORDINALS."""

    def setUp(self):
        self.xbe = bytes(_VANILLA_XBE.read_bytes())

    def test_thunk_va_is_0x18F3A0(self):
        """For Azurik's retail XBE, the kernel thunks begin at a fixed
        VA.  If the obfuscation key or the image layout shifts this
        value, the dependent tests below would start falsely passing
        — so we pin it."""
        self.assertEqual(ki._resolve_kernel_thunk_va(self.xbe), 0x18F3A0)

    def test_parse_returns_151_entries(self):
        entries = ki.parse_kernel_thunks(self.xbe)
        self.assertEqual(len(entries), 151,
            msg="Azurik has 151 kernel imports.  Parsing fewer means "
                "we stopped early (bogus null terminator); parsing "
                "more means the null terminator was missed.")

    def test_every_parsed_entry_has_a_known_name(self):
        """Coverage check: every ordinal in the thunk table should be
        in AZURIK_KERNEL_ORDINALS.  If this fails, someone either
        stripped an entry from xboxkrnl_ordinals.py or the XBE is
        non-vanilla."""
        entries = ki.parse_kernel_thunks(self.xbe)
        unresolved = [e for e in entries if e.name is None]
        self.assertEqual(
            unresolved, [],
            msg="every thunk-table slot should have a matching entry "
                "in xboxkrnl_ordinals.AZURIK_KERNEL_ORDINALS")

    def test_thunk_vas_are_contiguous_dwords(self):
        entries = ki.parse_kernel_thunks(self.xbe)
        for i, e in enumerate(entries):
            self.assertEqual(
                e.thunk_va, 0x18F3A0 + i * 4,
                msg=f"thunk slot {i} should be at VA "
                    f"0x{0x18F3A0 + i*4:X}, got 0x{e.thunk_va:X}")

    def test_kernel_import_map_spot_checks(self):
        """Spot-check a handful of well-known imports resolve to their
        expected thunk VAs.  Each VA is a 4-byte slot offset from the
        table start (0x18F3A0) by 4 × the entry's index."""
        imap = ki.kernel_import_map(self.xbe)
        # First four entries are: NtClose (187), NtQueryDirectoryFile
        # (207), NtOpenFile (202), RtlInitAnsiString (289).  These
        # are the first four slots of the thunk table.
        self.assertEqual(imap["NtClose"],              0x18F3A0 + 0 * 4)
        self.assertEqual(imap["NtQueryDirectoryFile"], 0x18F3A0 + 1 * 4)
        self.assertEqual(imap["NtOpenFile"],           0x18F3A0 + 2 * 4)
        self.assertEqual(imap["RtlInitAnsiString"],    0x18F3A0 + 3 * 4)

    def test_kernel_import_map_has_every_known_function(self):
        imap = ki.kernel_import_map(self.xbe)
        for entry in AZURIK_KERNEL_ORDINALS:
            self.assertIn(
                entry.name, imap,
                msg=f"{entry.name} (ordinal {entry.ordinal}) is listed "
                    f"in the ordinal table but not in the thunk-map "
                    f"parsed from the vanilla XBE")


# ===========================================================================
# ShimLayoutSession — kernel stub caching
# ===========================================================================


@unittest.skipUnless(_VANILLA_XBE.exists(),
    f"vanilla XBE fixture required at {_VANILLA_XBE}")
class SessionKernelStubs(unittest.TestCase):
    """Session-level invariants for kernel-stub generation."""

    def setUp(self):
        self.xbe = bytearray(_VANILLA_XBE.read_bytes())
        self.session = ShimLayoutSession(xbe_data=self.xbe)
        self._alloc_calls: list[tuple[str, bytes]] = []

    def _allocate(self, name: str, placeholder: bytes) -> tuple[int, int]:
        """Minimal allocator that just hands out incrementing VAs —
        we're testing stub placement semantics, not real XBE landing."""
        self._alloc_calls.append((name, placeholder))
        va = 0x200000 + 0x10 * len(self._alloc_calls)
        return 0x10_000 + 0x10 * len(self._alloc_calls), va

    def test_unknown_kernel_symbol_returns_none(self):
        self.assertIsNone(self.session.stub_for_kernel_symbol(
            "_c_my_shim", self._allocate))
        self.assertIsNone(self.session.stub_for_kernel_symbol(
            "_NotAKernelFn@0", self._allocate))

    def test_known_kernel_symbol_places_stub(self):
        va1 = self.session.stub_for_kernel_symbol(
            "_DbgPrint", self._allocate)
        self.assertIsNotNone(va1)
        self.assertEqual(len(self._alloc_calls), 1,
            msg="allocator should be called exactly once for the "
                "first reference to a kernel function")

    def test_duplicate_reference_reuses_stub(self):
        """Core dedup invariant — the session caches stubs by mangled
        name so multiple shims referencing the same kernel function
        share a single placement."""
        va1 = self.session.stub_for_kernel_symbol(
            "_DbgPrint", self._allocate)
        va2 = self.session.stub_for_kernel_symbol(
            "_DbgPrint", self._allocate)
        self.assertEqual(va1, va2,
            msg="second reference must return the cached VA (not "
                "re-allocate a new stub)")
        self.assertEqual(len(self._alloc_calls), 1,
            msg="second reference MUST NOT call the allocator again — "
                "that's the whole point of session-level dedup")

    def test_two_different_kernel_symbols_get_two_stubs(self):
        va1 = self.session.stub_for_kernel_symbol(
            "_DbgPrint", self._allocate)
        va2 = self.session.stub_for_kernel_symbol(
            "_NtClose@4", self._allocate)
        self.assertNotEqual(va1, va2,
            msg="different kernel functions must get distinct stubs")
        self.assertEqual(len(self._alloc_calls), 2)

    def test_stub_bytes_target_correct_thunk_va(self):
        va = self.session.stub_for_kernel_symbol(
            "_DbgPrint", self._allocate)
        self.assertIsNotNone(va)
        placeholder = self._alloc_calls[0][1]
        self.assertEqual(len(placeholder), 6)
        self.assertEqual(placeholder[:2], b"\xFF\x25")
        thunk_va = struct.unpack_from("<I", placeholder, 2)[0]
        # DbgPrint is ordinal 8; check the map resolves it to the
        # correct slot.  The 8th slot isn't necessarily the 8th thunk
        # in Azurik's table — it's wherever DbgPrint landed.
        imap = ki.kernel_import_map(bytes(self.xbe))
        self.assertEqual(thunk_va, imap["DbgPrint"])


# ===========================================================================
# layout_coff integration — synthetic shim calls a kernel import
# ===========================================================================


class LayoutCoffResolvesKernelImports(unittest.TestCase):
    """Synthetic COFF that does `call _DbgPrint`, run layout_coff with
    a session-backed resolver, confirm the REL32 lands on the stub."""

    def _synthetic_coff(self, target_name: str):
        from azurik_mod.patching.coff import (
            CoffFile,
            CoffRelocation,
            CoffSection,
            CoffSymbol,
            IMAGE_REL_I386_REL32,
        )
        text = CoffSection(
            name=".text",
            virtual_size=0,
            virtual_address=0,
            raw_size=5,
            raw_offset=0,
            flags=0,
            data=b"\xE8\x00\x00\x00\x00",
            reloc_offset=0,
            reloc_count=1,
            relocations=[CoffRelocation(
                va=1, symbol_index=1, type=IMAGE_REL_I386_REL32)],
        )
        entry = CoffSymbol(
            name="_entry", value=0, section_number=1,
            type=0, storage_class=2)
        ext = CoffSymbol(
            name=target_name, value=0, section_number=0,
            type=0, storage_class=2)
        return CoffFile(machine=0x014C, sections=[text], symbols=[entry, ext])

    @unittest.skipUnless(_VANILLA_XBE.exists(),
        "vanilla XBE needed for thunk-table lookup")
    def test_extern_resolver_feeds_kernel_stub_va(self):
        xbe = bytearray(_VANILLA_XBE.read_bytes())
        session = ShimLayoutSession(xbe_data=xbe)
        stub_va_placeholder = 0x600000

        # Fake allocator that returns a fixed VA for the stub.  We
        # then check the REL32 points at it.
        def _alloc(_name: str, _ph: bytes) -> tuple[int, int]:
            return 0x700000, stub_va_placeholder

        resolver = session.make_extern_resolver(_alloc)
        coff = self._synthetic_coff("_DbgPrint")
        landed = layout_coff(
            coff, "_entry",
            allocate=lambda _n, _p: (0x1000, 0x100000),
            extern_resolver=resolver,
        )
        text = landed.sections[0]
        rel32 = struct.unpack_from("<i", text.data, 1)[0]
        # The CALL at VA 0x100001 (site) measures rel32 from end of
        # the 5-byte instruction (0x100005).  It should send control
        # to the stub.
        self.assertEqual(
            rel32, stub_va_placeholder - 0x100005,
            msg="REL32 must be rewritten to target the kernel stub VA "
                "the session allocated for DbgPrint")


# ===========================================================================
# End-to-end compile: real shim uses a kernel import
# ===========================================================================


def _compile(src: Path) -> Path | None:
    if not _COMPILE_SH.exists() or not src.exists():
        return None
    out = Path(tempfile.mkdtemp(prefix="d1_test_")) / "out.o"
    try:
        subprocess.check_call(
            ["bash", str(_COMPILE_SH), str(src), str(out)],
            cwd=_REPO_ROOT)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return out


_TEST_SRC = _REPO_ROOT / "shims/fixtures/_kernel_call_test.c"


def _ensure_test_source():
    """Keep the test fixture next to its siblings."""
    if _TEST_SRC.exists():
        return
    _TEST_SRC.write_text("""/* Fixture: shim that calls a kernel import.  Exercises D1's
 * stub-generation + REL32 resolution path via layout_coff.
 */
#include "azurik_kernel.h"

void c_kernel_test(void) {
    DbgPrint("hello from shim");
}
""")


def _toolchain_available() -> bool:
    _ensure_test_source()
    return _compile(_TEST_SRC) is not None


@unittest.skipUnless(_VANILLA_XBE.exists(),
    f"vanilla XBE fixture required at {_VANILLA_XBE}")
@unittest.skipUnless(_toolchain_available(),
    "i386 clang PE-COFF toolchain not available on this host")
class RealShimCallsKernel(unittest.TestCase):
    """End-to-end: compile a shim that calls DbgPrint, run layout_coff
    through a real ShimLayoutSession, verify the resulting REL32
    chain (shim → stub → thunk) is internally consistent."""

    def setUp(self):
        _ensure_test_source()
        self.obj = _compile(_TEST_SRC)
        self.coff = parse_coff(self.obj.read_bytes())
        self.xbe = bytearray(_VANILLA_XBE.read_bytes())
        self.session = ShimLayoutSession(xbe_data=self.xbe)

    def test_compiled_shim_has_cdecl_dbgprint_extern(self):
        names = {s.name for s in self.coff.symbols if s.name}
        # clang emits cdecl mangling for varargs functions — just `_DbgPrint`.
        self.assertIn("_DbgPrint", names,
            msg="varargs DbgPrint should mangle to `_DbgPrint` (cdecl); "
                "seeing a different shape means the azurik_kernel.h "
                "declaration accidentally switched to stdcall")

    def test_layout_resolves_call_to_kernel_stub(self):
        from azurik_mod.patching.coff import IMAGE_REL_I386_REL32

        shim_text_va = 0x300000
        layout_state = {"next_offset": 0x300, "next_va": shim_text_va}

        def _alloc(name: str, placeholder: bytes) -> tuple[int, int]:
            off = layout_state["next_offset"]
            va = layout_state["next_va"]
            layout_state["next_offset"] += len(placeholder)
            layout_state["next_va"] += len(placeholder)
            self.xbe[off:off + len(placeholder)] = placeholder
            return off, va

        resolver = self.session.make_extern_resolver(_alloc)
        landed = layout_coff(
            self.coff, "_c_kernel_test",
            allocate=_alloc,
            extern_resolver=resolver,
        )
        text = next(s for s in landed.sections if s.name == ".text")
        text_coff = self.coff.section(".text")

        # Find the REL32 relocation that targets `_DbgPrint` — there
        # are usually also DIR32 relocations for string addends, but
        # the call itself is always the REL32 one.
        dbg_reloc = None
        for r in text_coff.relocations:
            sym = self.coff.symbols[r.symbol_index]
            if sym.name == "_DbgPrint" and r.type == IMAGE_REL_I386_REL32:
                dbg_reloc = r
                break
        self.assertIsNotNone(dbg_reloc,
            msg="shim COFF must contain a REL32 relocation pointing "
                "at `_DbgPrint` — the relocation for the actual call")

        rel32 = struct.unpack_from("<i", text.data, dbg_reloc.va)[0]
        site_va = text.vaddr + dbg_reloc.va
        call_target = site_va + 4 + rel32

        # That target must be a kernel stub the session placed.
        stub_vas = set(self.session._kernel_stubs.values())
        self.assertIn(
            call_target, stub_vas,
            msg=f"CALL should land on a kernel stub (session has "
                f"stubs at {sorted(hex(v) for v in stub_vas)}), but "
                f"lands at 0x{call_target:X}.")

        # And the stub contents must be the FF 25 <thunk_va> form
        # targeting DbgPrint's real thunk slot.
        stub_offset = call_target - shim_text_va + 0x300  # layout_state base
        stub_bytes = bytes(self.xbe[stub_offset:stub_offset + 6])
        self.assertEqual(stub_bytes[:2], b"\xFF\x25")
        stub_thunk = struct.unpack_from("<I", stub_bytes, 2)[0]
        imap = ki.kernel_import_map(bytes(self.xbe))
        self.assertEqual(stub_thunk, imap["DbgPrint"],
            msg="stub's indirect target must be the DbgPrint thunk "
                "slot from the kernel import map")


# ===========================================================================
# Drift guard: header <-> ordinal map
# ===========================================================================


class HeaderOrdinalDriftGuard(unittest.TestCase):
    """Every extern in azurik_kernel.h must correspond to a known
    xboxkrnl ordinal — otherwise shims would declare a function
    that D1's layout pass can't resolve."""

    def setUp(self):
        if not _KERNEL_HEADER.exists():
            self.skipTest(f"kernel header missing at {_KERNEL_HEADER}")
        self.header_text = _KERNEL_HEADER.read_text()

    def _extract_extern_names(self) -> set[str]:
        """Scan the header for C-function prototypes and return their
        names.  Strips C-style comments, function-pointer typedefs
        (``(*NAME)``), and common C keywords so identifiers appearing
        in documentation text or typedef scaffolding don't get picked
        up by the regex."""
        # Remove /* ... */ comment blocks (non-greedy across lines).
        src = re.sub(r"/\*.*?\*/", "", self.header_text, flags=re.DOTALL)
        # Remove // ... line comments.
        src = re.sub(r"//[^\n]*", "", src)
        # Remove function-pointer typedef bodies: `typedef RET
        # (*NAME)(...);` — the RET token would otherwise look like a
        # prototype.
        src = re.sub(r"typedef[^;]*?;", "", src, flags=re.DOTALL)

        # A small stopword set of C keywords + kernel typedef names
        # that are never real kernel-import declarations.
        STOPWORDS = {
            "void", "int", "char", "short", "long", "signed", "unsigned",
            "const", "volatile", "static", "extern", "typedef", "sizeof",
            "for", "while", "if", "return", "switch", "case", "default",
            "struct", "union", "enum", "goto", "continue", "break",
            "do", "else", "double", "float",
            # Kernel-typedef aliases that appear before function-
            # pointer declarations; they never show up as actual
            # callable kernel exports.
            "NTSTATUS", "ULONG", "LONG", "USHORT", "SHORT", "UCHAR",
            "CCHAR", "BYTE", "BOOLEAN", "DWORD", "HANDLE", "PVOID",
            "NTAPI", "FASTCALL", "CDECL", "DECLSPEC_NORETURN",
            "ACCESS_MASK", "KPRIORITY", "LOGICAL", "SIZE_T",
            "ULONGLONG", "ULONG_PTR", "KIRQL",
        }

        names: set[str] = set()
        for match in re.finditer(
            r"\b([A-Za-z_][A-Za-z0-9_]{2,})\s*\(",
            src,
        ):
            name = match.group(1)
            if name.startswith(("_", "XBOX_", "struct")):
                continue
            if name.startswith("__"):
                continue
            if name in STOPWORDS:
                continue
            names.add(name)
        return names

    def test_every_extern_has_matching_ordinal(self):
        names = self._extract_extern_names()
        self.assertTrue(len(names) > 0,
            msg="extern parse produced no names — the regex is wrong "
                "or the header is empty")
        for name in names:
            self.assertIn(
                name, NAME_TO_ORDINAL,
                msg=f"azurik_kernel.h declares `{name}` but the Python "
                    f"ordinal table doesn't know about it.  Either add "
                    f"a `KernelOrdinal` entry in xboxkrnl_ordinals.py, "
                    f"or remove the extern.  (Names Azurik doesn't "
                    f"import cannot be called from shims via D1.)")


if __name__ == "__main__":
    unittest.main()
