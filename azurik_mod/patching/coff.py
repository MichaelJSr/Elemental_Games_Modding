"""Minimal PE-COFF reader for shim object files.

Phase 1 shims are compiled by ``shims/toolchain/compile.sh`` with
``clang -target i386-pc-win32 -ffreestanding -nostdlib -c``.  The
output is a tiny ``.o`` file containing:

- A 20-byte COFF file header.
- A handful of section headers (``.text``, ``.bss``, ``.data``, a
  couple of LLVM / LINK directive sections).
- The section bodies.
- A symbol table (18 bytes per entry) + a string table holding long
  symbol names.

This module parses just enough of that format to extract:

1. The raw bytes of the ``.text`` section.
2. The offset-within-``.text`` of a named symbol.

Both pieces are all we need to lay a shim down inside an XBE and
compute the trampoline's ``CALL rel32`` target.

Phase 1 explicitly does NOT attempt to process relocations — the
first shim has none (pure arithmetic, no globals, no imports).  If a
future shim's ``.o`` carries relocation entries, the apply pipeline
raises instead of silently doing the wrong thing; Phase 2 will
extend this module to handle ``IMAGE_REL_I386_DIR32`` and friends.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# --- COFF machine types ---------------------------------------------------
IMAGE_FILE_MACHINE_I386 = 0x014C

# --- Section flag bits we care about --------------------------------------
IMAGE_SCN_CNT_CODE = 0x00000020
IMAGE_SCN_MEM_EXECUTE = 0x20000000


@dataclass
class CoffSection:
    """One section from a PE-COFF ``.o``.

    ``name`` is the short 8-char name from the section header; longer
    names (``/NN`` indirection into the string table) are resolved to
    their full form.  ``data`` is the raw bytes lifted straight out
    of the file at ``raw_offset`` for ``raw_size`` bytes.
    """
    name: str
    virtual_size: int
    virtual_address: int
    raw_size: int
    raw_offset: int
    flags: int
    data: bytes
    reloc_offset: int
    reloc_count: int


@dataclass
class CoffSymbol:
    """One symbol from the COFF symbol table.  Auxiliary records are
    skipped — we only need the primary entries."""
    name: str
    value: int           # offset within the owning section
    section_number: int  # 1-based; 0 = undefined, -1/-2 = absolute/debug
    type: int
    storage_class: int


@dataclass
class CoffFile:
    """Parsed PE-COFF object file."""
    machine: int
    sections: list[CoffSection]
    symbols: list[CoffSymbol]

    def section(self, name: str) -> CoffSection:
        for s in self.sections:
            if s.name == name:
                return s
        raise KeyError(f"section {name!r} not found in COFF file")

    def symbol(self, name: str) -> CoffSymbol:
        for s in self.symbols:
            if s.name == name:
                return s
        raise KeyError(f"symbol {name!r} not found in COFF file")


def _read_string_table(data: bytes, offset: int) -> bytes:
    """Return the raw string-table bytes starting at ``offset``.

    The COFF string table begins with a 4-byte little-endian length
    covering itself.  The table content follows immediately after."""
    size = struct.unpack_from("<I", data, offset)[0]
    if size < 4:
        # Malformed or absent string table — treat as empty.
        return b""
    return data[offset:offset + size]


def _resolve_name(raw: bytes, string_table: bytes) -> str:
    """Resolve a COFF name field (8 bytes or `/NN` indirection)."""
    if raw[:4] == b"\x00\x00\x00\x00":
        # Offset into the string table (little-endian, at +4).
        off = struct.unpack_from("<I", raw, 4)[0]
        end = string_table.find(b"\x00", off)
        if end == -1:
            end = len(string_table)
        return string_table[off:end].decode("ascii", errors="replace")
    # Inline 8-byte name, null-terminated if shorter.
    end = raw.find(b"\x00")
    if end == -1:
        end = 8
    return raw[:end].decode("ascii", errors="replace")


def parse_coff(data: bytes) -> CoffFile:
    """Parse a PE-COFF ``.o`` file into sections + symbols.

    Raises ``ValueError`` on a malformed header, unsupported machine
    type, or anything that would make symbol-address computation
    ambiguous.  The Phase 1 shim pipeline is strict by design — we'd
    rather fail loudly at apply time than ship a subtly wrong CALL.
    """
    if len(data) < 20:
        raise ValueError("COFF file too small to hold a file header")

    (machine, num_sections, _timestamp, sym_table_ptr,
     num_symbols, opt_header_size, _chars) = struct.unpack_from(
        "<HHIIIHH", data, 0)

    if machine != IMAGE_FILE_MACHINE_I386:
        raise ValueError(
            f"Unsupported COFF machine 0x{machine:04X}; expected "
            f"IMAGE_FILE_MACHINE_I386 (0x014C). Phase 1 shims must be "
            f"compiled with `clang -target i386-pc-win32`.")

    # String table starts right after the symbol table (18 bytes per
    # symbol record).  Parse it first so section-header name lookups
    # that use the /NN indirection resolve correctly.
    string_table_offset = sym_table_ptr + num_symbols * 18
    string_table = _read_string_table(data, string_table_offset)

    # Section headers follow the 20-byte file header + optional header.
    section_hdr_offset = 20 + opt_header_size
    sections: list[CoffSection] = []
    for i in range(num_sections):
        hdr_off = section_hdr_offset + i * 40
        name_raw = data[hdr_off:hdr_off + 8]
        (vsize, vaddr, raw_size, raw_offset, reloc_offset,
         _lineno_offset, reloc_count, _lineno_count, flags) = struct.unpack_from(
            "<IIIIIIHHI", data, hdr_off + 8)
        section_data = data[raw_offset:raw_offset + raw_size] if raw_size else b""
        sections.append(CoffSection(
            name=_resolve_name(name_raw, string_table),
            virtual_size=vsize,
            virtual_address=vaddr,
            raw_size=raw_size,
            raw_offset=raw_offset,
            flags=flags,
            data=section_data,
            reloc_offset=reloc_offset,
            reloc_count=reloc_count,
        ))

    # Walk the symbol table skipping auxiliary records.
    symbols: list[CoffSymbol] = []
    i = 0
    while i < num_symbols:
        sym_off = sym_table_ptr + i * 18
        name_raw = data[sym_off:sym_off + 8]
        (value, section_number, sym_type, storage_class,
         num_aux) = struct.unpack_from("<IhHBB", data, sym_off + 8)
        symbols.append(CoffSymbol(
            name=_resolve_name(name_raw, string_table),
            value=value,
            section_number=section_number,
            type=sym_type,
            storage_class=storage_class,
        ))
        i += 1 + num_aux  # skip aux records

    return CoffFile(
        machine=machine,
        sections=sections,
        symbols=symbols,
    )


def extract_shim_bytes(
    coff: CoffFile,
    symbol_name: str,
) -> tuple[bytes, int]:
    """Return (text_section_bytes, symbol_offset_in_text) for a shim.

    The trampoline pipeline uses this to find the code bytes that go
    into XBE padding and the offset-from-start where the symbol's
    entry point lives.  The shim's absolute address inside the XBE
    then becomes ``shim_region_start + symbol_offset``.

    Phase 1 constraints (will be relaxed in Phase 2):

    - The symbol MUST live in a section named ``.text``.
    - The ``.text`` section MUST NOT carry relocations.  If a shim's
      ``.text`` has any, we raise — loading it verbatim into the XBE
      would corrupt absolute / PC-relative references.
    """
    text = coff.section(".text")
    sym = coff.symbol(symbol_name)

    if sym.section_number <= 0:
        raise ValueError(
            f"symbol {symbol_name!r} has section_number "
            f"{sym.section_number}; expected a positive index pointing "
            f"at .text.  (0 = undefined, -1/-2 = absolute / debug.)")

    # COFF section numbers are 1-based — convert to 0-based index.
    target_section = coff.sections[sym.section_number - 1]
    if target_section.name != ".text":
        raise ValueError(
            f"symbol {symbol_name!r} lives in section "
            f"{target_section.name!r}, not .text.  Phase 1 shims may "
            f"only export code symbols.")

    if text.reloc_count:
        raise ValueError(
            f"COFF .text has {text.reloc_count} relocations; Phase 1 "
            f"requires relocation-free shims.  Rewrite the shim to "
            f"avoid globals / imports, or extend coff.py + "
            f"apply_trampoline_patch to handle IMAGE_REL_I386_* "
            f"entries (Phase 2 work).")

    return text.data, sym.value
