"""XBE kernel import-table reader for the C-shim platform (D1).

At load time the Xbox kernel walks the XBE's kernel thunk table (a
null-terminated array of 32-bit values at an obfuscated VA stored in
the XBE image header) and replaces each entry with the actual address
of the corresponding ``xboxkrnl.exe`` export.  Each entry is tagged
``0x80000000 | ordinal`` — the low 16 bits pick an export; the high
bit distinguishes ordinal imports from the (unused on Xbox) by-name
form.

For shim authors this table is the existing surface area that's
reachable from shim code without any further XBE surgery.  If a shim
calls ``KeQueryPerformanceCounter`` and that function is already in
the game's thunk table, we simply generate a small

    FF 25 <thunk_va>        ; JMP [thunk_slot]      — 6 bytes

stub in the shim landing region and resolve the COFF external
``_KeQueryPerformanceCounter@4`` to the stub's VA.  The shim's
``call _KeQueryPerformanceCounter@4`` (E8 rel32) lands on the stub;
the stub jumps through the thunk slot; the kernel has already written
the actual kernel-function pointer into that slot.

This is exactly how MSVC's linker implements ``__imp__Foo@N`` stubs
for Win32 / PE imports.  We reimplement the bare minimum of that
contract here.

Adding a NEW kernel import (one Azurik does not already reference)
is Phase 2 D1-extend work and is NOT supported by this module — the
thunk table has zero trailing slack in Azurik's XBE.  Such imports
can still be called indirectly by going through an Azurik function
that wraps the kernel API (the A3 vanilla-symbol registry).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from azurik_mod.patching.xboxkrnl_ordinals import (
    NAME_TO_ORDINAL,
    ORDINAL_TO_NAME,
    ordinal_for,
)
from azurik_mod.patching.xbe import parse_xbe_sections


# XBE kernel-thunk XOR keys.  The header stores the VA XOR-encoded
# against a retail / debug / chihiro magic; we resolve all three at
# parse time and pick whichever gives a plausible in-image VA.
_KERNEL_THUNK_XOR_RETAIL  = 0x5B6D40B6
_KERNEL_THUNK_XOR_DEBUG   = 0xEFB1F152
_KERNEL_THUNK_XOR_CHIHIRO = 0x2290059D


@dataclass(frozen=True)
class ThunkEntry:
    """One slot of the kernel import thunk table.

    ``thunk_va`` is the entry's virtual address (where the game's
    ``CALL [thunk_va]`` instructions indirect through).
    ``ordinal`` is the xboxkrnl.exe export ordinal.
    ``name`` is the un-mangled function name (resolved via
    :mod:`.xboxkrnl_ordinals`), or ``None`` for ordinals this module
    doesn't yet know.
    """
    thunk_va: int
    ordinal: int
    name: str | None


def _resolve_kernel_thunk_va(xbe: bytes) -> int:
    """Decrypt the kernel-thunk VA from an XBE header.

    Tries retail / debug / chihiro XOR keys in turn; returns whichever
    produces a VA inside ``[base, base + size_of_image)``.  Raises
    ``ValueError`` for malformed headers.
    """
    if xbe[:4] != b"XBEH":
        raise ValueError("Not an XBE: magic != 'XBEH'")
    base = struct.unpack_from("<I", xbe, 0x104)[0]
    size_of_image = struct.unpack_from("<I", xbe, 0x10C)[0]
    raw = struct.unpack_from("<I", xbe, 0x158)[0]
    for key in (_KERNEL_THUNK_XOR_RETAIL,
                _KERNEL_THUNK_XOR_DEBUG,
                _KERNEL_THUNK_XOR_CHIHIRO):
        va = raw ^ key
        if base <= va < base + size_of_image:
            return va
    raise ValueError(
        f"Could not resolve kernel thunk VA from raw 0x{raw:08X}: no "
        f"XOR key produces an in-image VA "
        f"[0x{base:X}, 0x{base + size_of_image:X}).")


def parse_kernel_thunks(xbe: bytes) -> list[ThunkEntry]:
    """Parse the XBE's kernel thunk table into a list of entries.

    Walks the table from its resolved start VA forward until the null
    terminator.  Each 4-byte slot with the high bit set is interpreted
    as ``0x80000000 | ordinal``; the low 16 bits give the export
    ordinal.  Entries whose ordinal is present in
    :mod:`.xboxkrnl_ordinals` are populated with their un-mangled
    name; unknown ordinals keep ``name=None`` so callers can report
    a clean "function not yet catalogued" error.

    Raises ``ValueError`` if the XBE is malformed or the thunk table
    never terminates within a sane bound (1000 entries).
    """
    thunk_va = _resolve_kernel_thunk_va(xbe)
    _, sections = parse_xbe_sections(xbe)
    # VA → file-offset translation (local copy — we don't use the
    # module-level va_to_file because it's keyed to the Azurik XBE
    # and unit tests may feed synthetic XBEs).
    thunk_off = None
    for s in sections:
        if s["vaddr"] <= thunk_va < s["vaddr"] + s["vsize"]:
            thunk_off = s["raw_addr"] + (thunk_va - s["vaddr"])
            break
    if thunk_off is None:
        raise ValueError(
            f"Kernel thunk VA 0x{thunk_va:X} does not fall in any "
            f"XBE section — XBE header is inconsistent.")

    entries: list[ThunkEntry] = []
    for i in range(1000):
        raw = struct.unpack_from("<I", xbe, thunk_off + i * 4)[0]
        if raw == 0:
            return entries
        if not (raw & 0x80000000):
            raise ValueError(
                f"Thunk table slot {i} at VA 0x{thunk_va + i*4:X} "
                f"has raw 0x{raw:08X}; high bit not set, but Xbox "
                f"kernel thunk-table entries are always by-ordinal.")
        ordinal = raw & 0xFFFF
        entries.append(ThunkEntry(
            thunk_va=thunk_va + i * 4,
            ordinal=ordinal,
            name=ORDINAL_TO_NAME.get(ordinal),
        ))
    raise ValueError(
        "Kernel thunk table exceeded 1000 entries without a null "
        "terminator; refusing to scan further.")


def kernel_import_map(xbe: bytes) -> dict[str, int]:
    """Return ``{function_name: thunk_va}`` for every catalogued import.

    This is the map a shim layout session hands to
    :func:`stub_for_symbol` to generate JMP stubs for kernel calls.
    Ordinals that aren't in :mod:`.xboxkrnl_ordinals` are skipped so
    callers get clean "unresolved extern" errors rather than a stub
    pointing at an unknown kernel function.
    """
    return {
        e.name: e.thunk_va
        for e in parse_kernel_thunks(xbe)
        if e.name is not None
    }


# ---------------------------------------------------------------------------
# Mangling → kernel-name helpers
# ---------------------------------------------------------------------------


def demangle_stdcall(mangled: str) -> str | None:
    """Strip MSVC-style stdcall decoration from a symbol name.

    Accepts ``"_Name@N"`` (N = total arg bytes) and returns ``"Name"``.
    Returns ``None`` for any other shape so callers can keep looking
    in other resolvers (vanilla registry, shared-library exports,
    etc.) without guessing.

    >>> demangle_stdcall("_NtClose@4")
    'NtClose'
    >>> demangle_stdcall("_RtlInitAnsiString@8")
    'RtlInitAnsiString'
    >>> demangle_stdcall("_c_skip_logo")
    >>> demangle_stdcall("DbgPrint")
    """
    if not mangled.startswith("_"):
        return None
    at = mangled.rfind("@")
    if at <= 1:
        return None
    digits = mangled[at + 1:]
    if not digits.isdigit():
        return None
    return mangled[1:at]


def demangle_cdecl(mangled: str) -> str | None:
    """Strip MSVC-style cdecl decoration from a symbol name.

    Cdecl mangling is ``_Name`` (just a leading underscore, no ``@N``
    because caller-cleans-stack doesn't encode the arg-size in the
    symbol).  Returns ``None`` for shapes that look like stdcall /
    fastcall instead.

    >>> demangle_cdecl("_DbgPrint")
    'DbgPrint'
    >>> demangle_cdecl("_NtClose@4")       # stdcall — not cdecl
    >>> demangle_cdecl("DbgPrint")
    """
    if not mangled.startswith("_") or "@" in mangled:
        return None
    return mangled[1:] or None


def kernel_name_for_symbol(mangled: str) -> str | None:
    """Resolve a COFF external name to its kernel-function name.

    Tries stdcall and cdecl demangling in turn; returns the first
    shape that both demangles cleanly and resolves in the kernel
    ordinal map.  Returns ``None`` if neither matches — the layout
    pass should fall through to its other resolvers.
    """
    for decoder in (demangle_stdcall, demangle_cdecl):
        demangled = decoder(mangled)
        if demangled is not None and demangled in NAME_TO_ORDINAL:
            return demangled
    return None


# ---------------------------------------------------------------------------
# Stub generation
# ---------------------------------------------------------------------------


_JMP_INDIRECT_OPCODE = b"\xFF\x25"
"""x86 ``JMP m32`` — dereferences a 4-byte absolute address and jumps
to the resolved value.  This is the MSVC-linker idiom for imports."""

_STUB_SIZE = 6


def stub_bytes_for(thunk_va: int) -> bytes:
    """Return the 6-byte ``FF 25 <thunk_va>`` indirect-JMP stub.

    The caller places these 6 bytes somewhere executable (the shim
    landing region) and resolves the kernel import's COFF external
    symbol to the stub's VA.  When the shim's ``CALL _Name@N``
    executes, it falls through to the stub, which dereferences the
    thunk slot and transfers control to the kernel function.
    """
    if not (0 <= thunk_va <= 0xFFFFFFFF):
        raise ValueError(f"thunk VA 0x{thunk_va:X} out of 32-bit range")
    return _JMP_INDIRECT_OPCODE + struct.pack("<I", thunk_va)


# Re-export for convenience — callers sometimes want to know the
# stub size up front (e.g. to pre-reserve a scratch region) without
# having to import both the constant and a stub generator.
STUB_SIZE = _STUB_SIZE
