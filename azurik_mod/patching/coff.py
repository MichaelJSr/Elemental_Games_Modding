"""Minimal PE-COFF reader for shim object files.

Shims are compiled by ``shims/toolchain/compile.sh`` with
``clang -target i386-pc-win32 -ffreestanding -nostdlib -c``, producing
a ``.o`` file containing:

- A 20-byte COFF file header.
- Section headers (``.text``, optional ``.rdata`` / ``.data`` /
  ``.bss`` / LLVM metadata).
- Each section's raw bytes.
- A per-section relocation table (for ``.text`` this encodes
  references to strings in ``.rdata``, calls into other shim
  functions, etc.).
- A symbol table (18 bytes per entry) + a string table holding long
  symbol names.

Phase 1 supported the zero-relocation case only — minimum viable to
prove the pipeline on a pure-arithmetic shim.  Phase 2 adds the
missing piece: a :func:`layout_coff` pass that:

- Places each non-metadata section at a caller-chosen XBE VA.
- Applies ``IMAGE_REL_I386_DIR32`` (absolute 32-bit VA) and
  ``IMAGE_REL_I386_REL32`` (PC-relative 32-bit) relocations in
  place, rewriting addends into final XBE virtual addresses.
- Returns the relocated section bytes + the resolved entry-point VA
  ready for the trampoline's ``CALL rel32`` to target.

The simpler :func:`extract_shim_bytes` stays for zero-relocation
shims so the hot path keeps its minimal cost.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Callable

# --- COFF machine types ---------------------------------------------------
IMAGE_FILE_MACHINE_I386 = 0x014C

# --- Section flag bits we care about --------------------------------------
IMAGE_SCN_CNT_CODE = 0x00000020
IMAGE_SCN_MEM_EXECUTE = 0x20000000

# --- i386 relocation types (subset we actually handle) --------------------
IMAGE_REL_I386_ABSOLUTE = 0x0000   # no-op relocation (ignore)
IMAGE_REL_I386_DIR32    = 0x0006   # target = addend + symbol_va
IMAGE_REL_I386_REL32    = 0x0014   # target = addend + symbol_va - (site_va + 4)

# Section names we never land — MSVC / clang metadata that the Xbox
# loader doesn't care about.  The layout pass skips these so they
# don't consume SHIMS-section space or cause bogus relocations.
_SKIPPED_SECTION_PREFIXES = (
    ".drectve",   # linker directives
    ".debug",     # DWARF / CodeView
    ".llvm_",     # LLVM-internal metadata
    ".xdata",     # SEH unwind info (not applicable to Xbox)
    ".pdata",     # SEH pdata
)


@dataclass
class CoffRelocation:
    """One PE-COFF relocation entry (10 bytes in the file)."""
    va: int                 # offset within the owning section
    symbol_index: int       # index into the COFF symbol table
    type: int               # IMAGE_REL_I386_*


@dataclass
class CoffSection:
    """One section from a PE-COFF ``.o``.

    ``name`` is the short 8-char name from the section header; longer
    names (``/NN`` indirection into the string table) are resolved to
    their full form.  ``data`` is the raw bytes lifted straight out
    of the file at ``raw_offset`` for ``raw_size`` bytes.
    ``relocations`` is populated during :func:`parse_coff` from the
    per-section relocation table that lives at ``reloc_offset``.
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
    relocations: list[CoffRelocation] = field(default_factory=list)


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
    """Resolve a COFF name field.

    PE-COFF uses two different long-name encodings depending on which
    table the field lives in:

    - **Symbol table**: if the first 4 bytes are zero, the next 4
      bytes are a little-endian uint32 offset into the string table.
    - **Section header**: if the name starts with ``/``, the rest is
      an ASCII decimal string giving the offset into the string
      table.  Otherwise the 8 raw bytes hold the (possibly null-
      terminated) inline name.

    Both encodings end up as a C-style null-terminated ASCII string
    in the string table; we return the decoded name.
    """
    # Symbol-style long name: 0x00000000 + uint32 offset.
    if raw[:4] == b"\x00\x00\x00\x00":
        off = struct.unpack_from("<I", raw, 4)[0]
        end = string_table.find(b"\x00", off)
        if end == -1:
            end = len(string_table)
        return string_table[off:end].decode("ascii", errors="replace")

    # Section-style long name: "/NN...".
    if raw[:1] == b"/":
        # Parse the decimal string (up to 7 more bytes, whitespace /
        # null-terminated).  Bail to the inline path if it isn't a
        # pure decimal number — leaves sections literally named "/foo"
        # working (there aren't any in practice, but being defensive
        # here costs nothing).
        digits = raw[1:8].split(b"\x00", 1)[0].strip()
        if digits.isdigit():
            off = int(digits)
            end = string_table.find(b"\x00", off)
            if end == -1:
                end = len(string_table)
            return string_table[off:end].decode(
                "ascii", errors="replace")

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
        relocations: list[CoffRelocation] = []
        for j in range(reloc_count):
            r_off = reloc_offset + j * 10
            r_va, r_sym, r_type = struct.unpack_from("<IIH", data, r_off)
            relocations.append(CoffRelocation(
                va=r_va, symbol_index=r_sym, type=r_type))
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
            relocations=relocations,
        ))

    # Walk the symbol table.  Aux records MUST stay in the list (as
    # placeholders) so that downstream relocation entries, which
    # index into the RAW symbol table, keep pointing at the right
    # entries.  We use a sentinel name ("") for aux records — they
    # never get looked up by name.
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
        # Each aux record appears as an opaque entry (its fields are
        # interpreted relative to the preceding primary symbol's
        # class).  Insert one placeholder per aux so the raw index
        # arithmetic lines up with the COFF on-disk layout.
        for _ in range(num_aux):
            symbols.append(CoffSymbol(
                name="",
                value=0,
                section_number=0,
                type=0,
                storage_class=0,
            ))
        i += 1 + num_aux

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
            f"COFF .text has {text.reloc_count} relocations; use "
            f"layout_coff() (relocation-aware Phase 2 loader) instead "
            f"of extract_shim_bytes() for this shim.")

    return text.data, sym.value


# ---------------------------------------------------------------------------
# Relocation-aware layout (Phase 2 A2)
# ---------------------------------------------------------------------------


@dataclass
class LandedSection:
    """One shim section after layout + relocation.

    ``data`` is the post-relocation byte blob ready to be dropped at
    ``vaddr`` in the final XBE.  ``file_offset`` is the file offset
    inside the XBE where those bytes were landed by the caller-provided
    placement callback (used by :mod:`azurik_mod.patching.apply` for
    its per-apply bookkeeping ledger).
    """
    name: str
    data: bytes
    vaddr: int
    file_offset: int


@dataclass
class LandedShim:
    """The full result of :func:`layout_coff` for one shim.

    Fields:
        sections:   Every non-metadata COFF section landed into the XBE,
                    with relocations applied.
        entry_va:   The virtual address of the named entry symbol,
                    ready to be used as the trampoline's ``rel32``
                    target.
    """
    sections: list[LandedSection]
    entry_va: int


def _is_landable(section: CoffSection) -> bool:
    """Return True if this COFF section should be placed in the XBE."""
    if not section.data:
        return False
    for skip in _SKIPPED_SECTION_PREFIXES:
        if section.name.startswith(skip):
            return False
    return True


def _resolve_symbol_va(
    symbol: CoffSymbol,
    coff: CoffFile,
    section_vas: dict[str, int],
) -> int:
    """Compute the final XBE VA for a COFF symbol.

    The symbol must live in a landable section; ``section_vas`` maps
    section name -> final VA in the XBE.  Undefined / absolute / debug
    symbols (``section_number`` <= 0) are rejected — Phase 2 shims are
    still self-contained; calling into vanilla game functions is Phase
    2 A3 work that will plumb through a separate thunk mechanism.
    """
    if symbol.section_number <= 0:
        raise ValueError(
            f"symbol {symbol.name!r} has section_number "
            f"{symbol.section_number}; only defined symbols can be "
            f"relocated.  Undefined externals need Phase 2 A3 (vanilla "
            f"function thunks) — rewrite the shim to avoid the "
            f"reference or wait for A3.")
    owning = coff.sections[symbol.section_number - 1]
    if owning.name not in section_vas:
        raise ValueError(
            f"symbol {symbol.name!r} lives in section "
            f"{owning.name!r} which was not landed; Phase 2 layout "
            f"expects every section that carries a referenced symbol "
            f"to be landable (non-empty, not in the skip-list).")
    return section_vas[owning.name] + symbol.value


def _apply_relocation(
    section_bytes: bytearray,
    section_vaddr: int,
    reloc: CoffRelocation,
    target_va: int,
) -> None:
    """Rewrite the 4-byte relocation field at ``reloc.va`` in place.

    The field already contains the ADDEND that the compiler left behind
    (typically 0 for DIR32 and -4 for REL32 when the assembler
    pre-bakes the "next-instruction" offset; occasionally a non-trivial
    constant for ``symbol + N`` expressions).  We read it, combine it
    with the symbol's final VA per the type's rule, and write it back.
    """
    site = reloc.va
    if site + 4 > len(section_bytes):
        raise ValueError(
            f"relocation at offset 0x{site:X} would read/write past "
            f"section end (size 0x{len(section_bytes):X})")
    addend = struct.unpack_from("<i", section_bytes, site)[0]

    if reloc.type == IMAGE_REL_I386_ABSOLUTE:
        return  # no-op: COFF-level alignment marker
    elif reloc.type == IMAGE_REL_I386_DIR32:
        # Absolute 32-bit target VA.
        final = (addend + target_va) & 0xFFFFFFFF
        struct.pack_into("<I", section_bytes, site, final)
    elif reloc.type == IMAGE_REL_I386_REL32:
        # PC-relative displacement, measured from the byte after the
        # 4-byte field (i.e. site_va + 4).
        site_va = section_vaddr + site
        final = (addend + target_va - (site_va + 4)) & 0xFFFFFFFF
        # signed 32-bit bounds check for cleanliness
        signed = struct.unpack("<i", struct.pack("<I", final))[0]
        if not -0x80000000 <= signed <= 0x7FFFFFFF:
            raise ValueError(
                f"REL32 displacement 0x{signed:X} at section offset "
                f"0x{site:X} does not fit signed 32-bit; shim target "
                f"is too far from its reference site.")
        struct.pack_into("<i", section_bytes, site, signed)
    else:
        raise ValueError(
            f"unsupported i386 relocation type 0x{reloc.type:04X} at "
            f"section offset 0x{site:X}.  Supported: DIR32 (0x0006), "
            f"REL32 (0x0014).  Extend _apply_relocation to cover more "
            f"types as shim features grow.")


def layout_coff(
    coff: CoffFile,
    entry_symbol: str,
    allocate: Callable[[str, bytes], tuple[int, int]],
) -> LandedShim:
    """Place every landable section via ``allocate``, apply relocations,
    and return the final byte blobs + entry-point VA.

    ``allocate(name, placeholder_bytes)`` is a callback that reserves
    ``len(placeholder_bytes)`` bytes of XBE space for the named
    section and returns ``(file_offset, vaddr)``.  The caller
    typically backs this with
    :func:`azurik_mod.patching.apply._carve_shim_landing`, which
    either extends ``.text`` or appends into the per-apply SHIMS
    section.  The placeholder bytes are zeros at allocation time —
    the relocated final bytes are written over them in a second pass.

    Order of operations:

    1. Collect every landable section (skipping metadata) in COFF
       declaration order.
    2. Call ``allocate`` for each in turn; record the returned VA.
    3. Build the symbol VA map from the section VAs.
    4. For each section, copy its raw bytes into a mutable buffer and
       apply every relocation using the resolved target VAs.
    5. Write the finalised buffer over the placeholder bytes.

    Returns a :class:`LandedShim` populated with the placed sections
    (each carrying its final bytes + VA + file offset) and the
    resolved VA of ``entry_symbol``.

    Raises ``ValueError`` for unsupported relocation types, missing
    symbols, or undefined externals.
    """
    placements: dict[str, tuple[int, int, bytearray]] = {}

    # --- 1 + 2. Placeholder-allocate each landable section ---------------
    for section in coff.sections:
        if not _is_landable(section):
            continue
        placeholder = bytes(len(section.data))  # all zeros
        file_off, vaddr = allocate(section.name, placeholder)
        # Keep a mutable buffer pre-seeded with the original bytes so
        # we can rewrite relocation fields in place.
        placements[section.name] = (file_off, vaddr, bytearray(section.data))

    if not placements:
        raise ValueError(
            "COFF has no landable sections; did compile.sh pass the "
            "right flags, or did the shim become empty after DCE?")

    # --- 3. Resolve symbol VAs -------------------------------------------
    section_vas = {name: vaddr for name, (_, vaddr, _) in placements.items()}

    # --- 4. Apply relocations in each placed section ---------------------
    landed: list[LandedSection] = []
    for section in coff.sections:
        if section.name not in placements:
            continue
        file_off, vaddr, buf = placements[section.name]
        for reloc in section.relocations:
            symbol = coff.symbols[reloc.symbol_index]
            target_va = _resolve_symbol_va(symbol, coff, section_vas)
            _apply_relocation(buf, vaddr, reloc, target_va)
        landed.append(LandedSection(
            name=section.name,
            data=bytes(buf),
            vaddr=vaddr,
            file_offset=file_off,
        ))

    # --- 5. Resolve entry-point VA ---------------------------------------
    entry = coff.symbol(entry_symbol)
    entry_va = _resolve_symbol_va(entry, coff, section_vas)

    return LandedShim(sections=landed, entry_va=entry_va)
