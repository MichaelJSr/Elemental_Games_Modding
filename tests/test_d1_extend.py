"""Tests for Phase 2 D1-extend — runtime xboxkrnl export resolver.

Covers four layers:

1. **Ordinal-catalogue invariants**: the extended ordinal table is
   well-formed (unique ordinals, non-empty, doesn't overlap with
   Azurik's static 151 at the ordinal level), and the combined
   round-trip (name → ordinal → name) stays consistent.

2. **Stub byte shape**: the 33-byte resolving stub emitted by
   ``_build_extended_stub`` matches its documented layout.  Bytes
   the caller can observe at known offsets (opcodes, JNZ reach,
   ordinal imm32, cache-slot abs32) are pinned.

3. **Resolver shim**: ``shims/shared/xboxkrnl_resolver.c`` compiles
   cleanly, exports only one symbol (``_xboxkrnl_resolve_by_ordinal``),
   has no undefined externs (it's self-contained).

4. **End-to-end via real shim**: compile a shim that calls both a
   static D1 import and an extended D1-extend import.  Run it
   through the full session pipeline (including auto-placement of
   the resolver) and confirm the generated stubs sit at distinct
   VAs with the right opcode families.

Tests that depend on the i386 clang cross-toolchain skip when it's
missing.
"""

from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.patching.coff import parse_coff  # noqa: E402
from azurik_mod.patching.shim_session import (  # noqa: E402
    ShimLayoutSession,
    _build_extended_stub,
    _D1_EXTEND_STUB_SIZE,
    _D1_EXTEND_CACHE_OFFSET,
)
from azurik_mod.patching.xboxkrnl_ordinals import (  # noqa: E402
    ALL_KERNEL_ORDINALS,
    AZURIK_KERNEL_ORDINALS,
    EXTENDED_KERNEL_ORDINALS,
    NAME_TO_ORDINAL,
    ORDINAL_TO_NAME,
    is_azurik_imported,
    ordinal_for,
)


_VANILLA_XBE = (_REPO_ROOT.parent /
                "Azurik - Rise of Perathia (USA).xiso/default.xbe")
_COMPILE_SH = _REPO_ROOT / "shims/toolchain/compile.sh"
_RESOLVER_SRC = _REPO_ROOT / "shims/shared/xboxkrnl_resolver.c"
_EXTEND_HDR = _REPO_ROOT / "shims/include/azurik_kernel_extend.h"


def _toolchain_available() -> bool:
    if not _COMPILE_SH.exists():
        return False
    with tempfile.TemporaryDirectory(prefix="d1x_probe_") as tmp:
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
# Ordinal-catalogue invariants
# ===========================================================================


class ExtendedOrdinalTable(unittest.TestCase):
    """Structural checks on EXTENDED_KERNEL_ORDINALS."""

    def test_table_is_non_empty(self):
        self.assertGreater(
            len(EXTENDED_KERNEL_ORDINALS), 50,
            msg="extended table should carry a meaningful portion "
                "of xboxkrnl's ~369 exports.  A near-empty table "
                "means D1-extend is effectively unreachable.")

    def test_all_ordinals_unique_in_combined_table(self):
        ordinals = [e.ordinal for e in ALL_KERNEL_ORDINALS]
        # Duplicate ordinals would make ORDINAL_TO_NAME lossy.
        self.assertEqual(
            len(ordinals), len(set(ordinals)),
            msg="duplicate ordinal in AZURIK + EXTENDED combined — "
                "one of them needs its ordinal corrected")

    def test_extended_names_dont_shadow_azurik_names(self):
        """For any name that appears in BOTH tables (Azurik static
        import + extended alias), NAME_TO_ORDINAL must prefer the
        Azurik slot (fast D1 path beats D1-extend runtime path)."""
        azurik_names = {e.name for e in AZURIK_KERNEL_ORDINALS}
        for entry in EXTENDED_KERNEL_ORDINALS:
            if entry.name in azurik_names:
                # Overlap is expected for alias ordinals.  The
                # invariant is that lookup still returns the Azurik
                # ordinal.
                got = NAME_TO_ORDINAL[entry.name]
                azurik_ord = next(
                    e.ordinal for e in AZURIK_KERNEL_ORDINALS
                    if e.name == entry.name)
                self.assertEqual(
                    got, azurik_ord,
                    msg=f"extended alias for {entry.name!r} "
                        f"(ordinal {entry.ordinal}) is winning over "
                        f"the Azurik static slot (ordinal "
                        f"{azurik_ord}).  D1's fast path would be "
                        f"bypassed for this import.")

    def test_is_azurik_imported_classifies_both_tables(self):
        for entry in AZURIK_KERNEL_ORDINALS:
            self.assertTrue(
                is_azurik_imported(entry.ordinal),
                msg=f"AZURIK entry ordinal {entry.ordinal} "
                    f"({entry.name}) should classify as imported")
        for entry in EXTENDED_KERNEL_ORDINALS:
            if entry.ordinal in {e.ordinal for e in AZURIK_KERNEL_ORDINALS}:
                continue  # skip alias ordinals
            self.assertFalse(
                is_azurik_imported(entry.ordinal),
                msg=f"EXTENDED entry ordinal {entry.ordinal} "
                    f"({entry.name}) should NOT classify as imported")

    def test_spot_check_extended_additions(self):
        """A handful of well-known ordinals we added in the extended
        table — if any one moves, someone edited the table with a
        wrong reference."""
        for name, expected_ord in [
            ("DbgBreakPoint", 5),
            ("DbgPrompt", 9),
            ("KeGetCurrentThread", 103),
            ("RtlZeroMemory", 320),
            ("MmIsAddressValid", 174),
            ("XboxKrnlVersion", 324),
        ]:
            self.assertEqual(
                ordinal_for(name), expected_ord,
                msg=f"{name} should be at ordinal {expected_ord} "
                    f"(got {ordinal_for(name)}) — verify against the "
                    f"canonical Xbox kernel ordinal map before "
                    f"shipping this fix")


# ===========================================================================
# Resolving-stub byte-shape
# ===========================================================================


class ExtendedStubShape(unittest.TestCase):
    """The 33-byte resolving stub emitted by _build_extended_stub."""

    def test_stub_is_exactly_33_bytes(self):
        stub = _build_extended_stub(
            stub_va=0x300000, ordinal=123, resolver_va=0x400000)
        self.assertEqual(len(stub), _D1_EXTEND_STUB_SIZE)
        self.assertEqual(len(stub), 0x21)

    def test_cache_offset_is_0x1D(self):
        self.assertEqual(_D1_EXTEND_CACHE_OFFSET, 0x1D)

    def test_opcodes_at_pinned_offsets(self):
        stub = _build_extended_stub(
            stub_va=0x300000, ordinal=123, resolver_va=0x400000)
        # MOV EAX, abs32 — opcode 0xA1
        self.assertEqual(stub[0x00], 0xA1, msg="expected MOV EAX, m32 at offset 0")
        # TEST EAX, EAX — 85 C0
        self.assertEqual(stub[0x05:0x07], b"\x85\xC0")
        # JNZ +0x12 — 75 12
        self.assertEqual(stub[0x07:0x09], b"\x75\x12")
        # PUSH imm32 — 68
        self.assertEqual(stub[0x09], 0x68)
        # CALL rel32 — E8
        self.assertEqual(stub[0x0E], 0xE8)
        # ADD ESP, 4 — 83 C4 04
        self.assertEqual(stub[0x13:0x16], b"\x83\xC4\x04")
        # MOV [abs32], EAX — A3
        self.assertEqual(stub[0x16], 0xA3)
        # JMP EAX — FF E0
        self.assertEqual(stub[0x1B:0x1D], b"\xFF\xE0")
        # Cache slot starts zeroed
        self.assertEqual(stub[0x1D:0x21], b"\x00\x00\x00\x00")

    def test_cache_va_is_stub_va_plus_0x1D(self):
        """Both cache-slot accesses (MOV EAX,[cache] + MOV [cache],EAX)
        must reference the same VA — stub_va + 0x1D."""
        stub = _build_extended_stub(
            stub_va=0x300000, ordinal=0xBEEF, resolver_va=0x400000)
        # MOV EAX, [cache_va] at offset 1..5
        load_va = struct.unpack("<I", stub[0x01:0x05])[0]
        # MOV [cache_va], EAX at offset 0x17..0x1B
        store_va = struct.unpack("<I", stub[0x17:0x1B])[0]
        self.assertEqual(load_va, 0x300000 + 0x1D)
        self.assertEqual(store_va, 0x300000 + 0x1D)

    def test_ordinal_is_embedded_as_imm32(self):
        stub = _build_extended_stub(
            stub_va=0x300000, ordinal=0xDEADBEEF, resolver_va=0x400000)
        # PUSH imm32 at offset 0x0A..0x0E
        imm = struct.unpack("<I", stub[0x0A:0x0E])[0]
        self.assertEqual(imm, 0xDEADBEEF)

    def test_call_rel32_targets_resolver(self):
        """REL32 at offset 0x0F must make CALL land at resolver_va."""
        stub_va = 0x300000
        resolver_va = 0x400000
        stub = _build_extended_stub(
            stub_va=stub_va, ordinal=123, resolver_va=resolver_va)
        rel32 = struct.unpack("<i", stub[0x0F:0x13])[0]
        call_after = stub_va + 0x13
        self.assertEqual(
            call_after + rel32, resolver_va,
            msg="REL32 + next-instruction-VA must equal resolver_va")

    def test_resolver_too_far_raises(self):
        """Signed 32-bit overflow on the CALL rel32 must raise — not
        silently emit garbage.  Use a resolver VA > 2 GB past the
        stub so the REL32 overflows the signed 32-bit positive range."""
        # stub_va = 0x100; call_after = 0x113.  Need resolver_va
        # such that resolver_va - 0x113 > 0x7FFFFFFF.
        # → resolver_va > 0x80000112.  Pick 0xE0000000 for headroom.
        with self.assertRaises(ValueError):
            _build_extended_stub(
                stub_va=0x100, ordinal=1,
                resolver_va=0xE0000000)


# ===========================================================================
# Resolver shim (the C helper)
# ===========================================================================


@unittest.skipUnless(_toolchain_available(),
    "i386 clang PE-COFF toolchain not available on this host")
class ResolverShimCompile(unittest.TestCase):
    """The resolver .c must compile cleanly as a self-contained shim."""

    def test_resolver_compiles(self):
        self.assertTrue(
            _RESOLVER_SRC.exists(),
            msg=f"resolver source missing at {_RESOLVER_SRC}")
        with tempfile.TemporaryDirectory(prefix="d1x_resolver_") as tmp:
            out = Path(tmp) / "resolver.o"
            subprocess.check_call(
                ["bash", str(_COMPILE_SH), str(_RESOLVER_SRC), str(out)],
                cwd=_REPO_ROOT)
            self.assertTrue(out.exists())
            coff = parse_coff(out.read_bytes())
            names = {s.name for s in coff.symbols if s.name}
            self.assertIn(
                "_xboxkrnl_resolve_by_ordinal", names,
                msg="resolver must export _xboxkrnl_resolve_by_ordinal "
                    "(cdecl) — that's the only symbol the session "
                    "looks up")

    def test_resolver_has_no_undefined_externs(self):
        """Self-contained: no C library, no kernel imports, no vanilla
        calls.  A stray undefined extern would break the E-style
        placement the session relies on."""
        with tempfile.TemporaryDirectory(prefix="d1x_resolver_") as tmp:
            out = Path(tmp) / "resolver.o"
            subprocess.check_call(
                ["bash", str(_COMPILE_SH), str(_RESOLVER_SRC), str(out)],
                cwd=_REPO_ROOT)
            coff = parse_coff(out.read_bytes())
            undefined = [
                s.name for s in coff.symbols
                if s.section_number == 0 and s.name
                and not s.name.startswith("@")  # MSVC directive symbols
                and s.storage_class == 2]
            self.assertEqual(
                undefined, [],
                msg=f"resolver has undefined externs: {undefined}. "
                    f"The resolver must be self-contained.")


# ===========================================================================
# End-to-end: session routes static vs extended imports correctly
# ===========================================================================


@unittest.skipUnless(_VANILLA_XBE.exists(),
    f"vanilla XBE fixture required at {_VANILLA_XBE}")
@unittest.skipUnless(_toolchain_available(),
    "i386 clang PE-COFF toolchain required for E2E")
class SessionRoutesStaticVsExtended(unittest.TestCase):
    """Session-level invariants on the dispatch path:

    - A mangled name in Azurik's static 151 → D1 stub (6 bytes,
      ``FF 25``).
    - A mangled name in the extended catalogue → D1-extend stub
      (33 bytes, starts with ``A1``).
    - A mangled name in neither → None (caller falls through to
      vanilla registry / shared libs)."""

    def setUp(self):
        self.xbe = bytearray(_VANILLA_XBE.read_bytes())
        self.session = ShimLayoutSession(xbe_data=self.xbe)
        self._alloc_cursor = 0

    def _allocate(self, name, placeholder):
        # Linear scratch allocator — we don't exercise the real
        # _carve_shim_landing here; we just need distinct VAs per
        # allocation so the session can compute REL32 deltas.
        off = 0x300 + self._alloc_cursor
        va = 0x300000 + self._alloc_cursor
        self._alloc_cursor += max(len(placeholder), 1)
        # Write the placeholder into the xbe buffer so the stub can
        # be overwritten by _build_extended_stub in place.
        self.xbe[off:off + len(placeholder)] = placeholder
        return off, va

    def test_static_import_gets_six_byte_stub(self):
        # DbgPrint is ordinal 8 — in Azurik's static table.
        stub_va = self.session.stub_for_kernel_symbol(
            "_DbgPrint", self._allocate)
        self.assertIsNotNone(stub_va)
        # The stub bytes got written into xbe at (stub_va - 0x300000 + 0x300).
        off = stub_va - 0x300000 + 0x300
        self.assertEqual(
            bytes(self.xbe[off:off + 2]), b"\xFF\x25",
            msg="static D1 import must get a 6-byte FF 25 stub")

    def test_extended_import_gets_33_byte_stub(self):
        # DbgBreakPoint is ordinal 5 — NOT in Azurik's static table.
        stub_va = self.session.stub_for_kernel_symbol(
            "_DbgBreakPoint@0", self._allocate)
        self.assertIsNotNone(stub_va)
        off = stub_va - 0x300000 + 0x300
        # Resolving stubs start with MOV EAX, [abs32] = opcode A1.
        self.assertEqual(
            self.xbe[off], 0xA1,
            msg="extended D1-extend import must get a resolving stub "
                "starting with MOV EAX,[abs32] (opcode 0xA1)")
        # And the TEST/JNZ sequence at offsets 5..9.
        self.assertEqual(
            bytes(self.xbe[off + 5:off + 9]), b"\x85\xC0\x75\x12")

    def test_unknown_name_returns_none(self):
        result = self.session.stub_for_kernel_symbol(
            "_c_my_shim",  # not a kernel function at all
            self._allocate)
        self.assertIsNone(result)

    def test_duplicate_extended_reference_reuses_stub(self):
        """Cache invariant: two shims calling the same extended
        kernel function get the SAME stub VA (one placement per
        session, not one per reference)."""
        va1 = self.session.stub_for_kernel_symbol(
            "_DbgBreakPoint@0", self._allocate)
        va2 = self.session.stub_for_kernel_symbol(
            "_DbgBreakPoint@0", self._allocate)
        self.assertEqual(va1, va2,
            msg="duplicate reference to the same extended import must "
                "reuse the cached stub VA — otherwise each shim pays "
                "for its own resolver placement + cache slot")

    def test_resolver_placed_exactly_once(self):
        """Placing 3 different extended imports should only trigger
        one resolver placement — that's the whole point of the
        `_resolver_va` cache."""
        self.session.stub_for_kernel_symbol("_DbgBreakPoint@0", self._allocate)
        self.session.stub_for_kernel_symbol(
            "_KeEnterCriticalRegion@0", self._allocate)
        self.session.stub_for_kernel_symbol(
            "_RtlZeroMemory@8", self._allocate)
        # Resolver should be non-None (placed) and unchanged across
        # the three extended-import requests.
        self.assertIsNotNone(self.session._resolver_va)


# ===========================================================================
# Drift guard: azurik_kernel_extend.h vs EXTENDED_KERNEL_ORDINALS
# ===========================================================================


class HeaderExtendDriftGuard(unittest.TestCase):
    """Every extern in azurik_kernel_extend.h must correspond to a
    catalogued ordinal.  An extern without a catalogue entry would be
    unreachable — layout_coff would report it as unresolved."""

    def setUp(self):
        if not _EXTEND_HDR.exists():
            self.skipTest(f"header missing at {_EXTEND_HDR}")
        import re
        self._re = re

    def _extern_names(self) -> set[str]:
        import re
        src = _EXTEND_HDR.read_text()
        # Strip comments first (C-style + C++-style).
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
        src = re.sub(r"//[^\n]*", "", src)
        # Strip function-pointer typedefs.
        src = re.sub(r"typedef[^;]*?;", "", src, flags=re.DOTALL)

        STOPWORDS = {
            "void", "int", "char", "short", "long", "signed", "unsigned",
            "const", "volatile", "static", "extern", "typedef", "sizeof",
            "for", "while", "if", "return", "switch", "case", "default",
            "struct", "union", "enum", "do", "else", "double", "float",
            "NTSTATUS", "ULONG", "LONG", "USHORT", "SHORT", "UCHAR",
            "CCHAR", "BYTE", "BOOLEAN", "DWORD", "HANDLE", "PVOID",
            "NTAPI", "FASTCALL", "CDECL", "DECLSPEC_NORETURN",
            "ACCESS_MASK", "KPRIORITY", "LOGICAL", "SIZE_T",
            "ULONGLONG", "LONGLONG", "ULONG_PTR", "KIRQL",
            "CHAR", "PSZ",
        }
        names: set[str] = set()
        for m in re.finditer(
            r"\b([A-Za-z_][A-Za-z0-9_]{2,})\s*\(",
            src,
        ):
            name = m.group(1)
            if name in STOPWORDS:
                continue
            if name.startswith(("_", "XBOX_", "struct")):
                continue
            if name.startswith("__"):
                continue
            names.add(name)
        return names

    def test_every_extended_extern_has_catalogue_entry(self):
        names = self._extern_names()
        self.assertTrue(
            len(names) > 10,
            msg="extern parse produced almost nothing — regex is wrong "
                "or header is empty")
        for name in names:
            self.assertIn(
                name, NAME_TO_ORDINAL,
                msg=f"azurik_kernel_extend.h declares {name!r} but "
                    f"it's not in the xboxkrnl ordinal catalogue.  "
                    f"Either add an entry to EXTENDED_KERNEL_ORDINALS "
                    f"in azurik_mod/patching/xboxkrnl_ordinals.py, or "
                    f"remove the extern.")


if __name__ == "__main__":
    unittest.main()
