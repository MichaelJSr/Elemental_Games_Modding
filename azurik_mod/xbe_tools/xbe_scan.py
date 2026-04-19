"""Pure-Python XBE scanners used by the ``azurik-mod xbe`` verbs.

Every function here is deliberately stateless + testable: callers
pass in ``xbe_bytes`` (``bytes`` or ``bytearray``) and get back plain
dataclasses.  The ``commands.py`` dispatcher is the only module that
owns the ``--iso`` vs ``--xbe`` argument handling and the pretty
printing.

The scanners replace the bespoke Python one-liners I kept rewriting
across RE sessions:

- :func:`find_imm32_references` — which ``.text`` instructions push a
  given VA as an immediate.  Handles PUSH imm32 (``0x68``),
  MOV r32 imm32 (``0xB8..0xBF``), and FF 25 jump thunks.
- :func:`find_floats_in_range` — locate every IEEE 754 ``float32`` or
  ``double`` in ``.rdata`` whose numeric value falls in ``[min, max]``.
- :func:`find_strings` — locate null-delimited ASCII strings by
  substring, return VA + some surrounding context.
- :func:`hex_dump` — hexdump of N bytes starting at a VA or file
  offset, with ASCII gutter.

Every helper accepts an already-parsed sections list from
:func:`azurik_mod.patching.xbe.parse_xbe_sections` to keep the hot
path zero-allocation for repeat queries in the same session.

See docs/TOOLING_ROADMAP.md § Tier 1 for the design justification.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from typing import Iterable

from azurik_mod.patching.xbe import (
    file_to_va,
    parse_xbe_sections,
    va_to_file,
)


# Opcodes that can appear immediately before a 4-byte VA literal
# we're looking for.  Ordered by observed frequency in Azurik's
# .text section (PUSH imm32 is by far the most common).
_IMM32_OPCODES = frozenset({
    0x68,       # PUSH imm32
    0xB8, 0xB9, 0xBA, 0xBB, 0xBD, 0xBF,  # MOV r32, imm32 (except ESP)
})


@dataclass(frozen=True)
class Imm32Reference:
    """One ``.text`` site that loads a specific VA as an imm32.

    Attributes
    ----------
    callsite_va: int
        VA of the instruction (i.e. of the opcode byte).
    callsite_file_offset: int
        File offset of the same instruction.
    opcode: int
        The byte that precedes the 4-byte immediate.  Useful to tell
        ``PUSH`` apart from ``MOV r32``.
    kind: str
        Human-readable mnemonic: ``"push"``, ``"mov"``, or
        ``"jmp-thunk"``.
    """

    callsite_va: int
    callsite_file_offset: int
    opcode: int
    kind: str


@dataclass(frozen=True)
class FloatHit:
    """One float-constant match in ``.rdata``."""

    va: int
    file_offset: int
    width: int  # 4 or 8
    value: float

    @property
    def hex_bytes(self) -> str:
        """The raw IEEE 754 bytes, hex-formatted for display."""
        if self.width == 4:
            return struct.pack("<f", self.value).hex()
        return struct.pack("<d", self.value).hex()


@dataclass(frozen=True)
class StringHit:
    """One ``.rdata`` null-delimited ASCII string match."""

    va: int
    file_offset: int
    text: str

    @property
    def length(self) -> int:
        """String length, NOT including the trailing NUL."""
        return len(self.text)


@dataclass(frozen=True)
class HexRow:
    """One hexdump row: 16 bytes of hex + ASCII gutter + VA."""

    va: int
    file_offset: int
    raw: bytes

    def format(self) -> str:
        """Render the row the way ``hexdump -C`` would."""
        hex_half = " ".join(f"{b:02x}" for b in self.raw[:8])
        hex_half2 = " ".join(f"{b:02x}" for b in self.raw[8:])
        hex_part = f"{hex_half:<23}  {hex_half2:<23}"
        ascii_gutter = "".join(
            chr(b) if 0x20 <= b < 0x7F else "." for b in self.raw)
        return (f"0x{self.va:08X}  (file 0x{self.file_offset:06X})  "
                f"{hex_part}  |{ascii_gutter}|")


# ---------------------------------------------------------------------------
# Section resolution — all public helpers accept either a bytes blob
# (we'll parse sections on the fly) or a pre-parsed sections list.
# ---------------------------------------------------------------------------

def _resolve_section(xbe: bytes, name: str,
                     sections: list | None = None
                     ) -> dict | None:
    """Return the named section record, or ``None`` when missing."""
    if sections is None:
        _, sections = parse_xbe_sections(xbe)
    for s in sections:
        if s["name"] == name:
            return s
    return None


# ---------------------------------------------------------------------------
# Imm32 reference scanner
# ---------------------------------------------------------------------------

def find_imm32_references(xbe: bytes | bytearray, target_va: int, *,
                          sections: list | None = None
                          ) -> list[Imm32Reference]:
    """Every ``.text`` instruction that embeds ``target_va`` as an imm32.

    Scans only the main code section (``.text``).  Hits are returned
    in file-offset order.  Matches are filtered by "does the byte
    immediately before the 4-byte imm look like a recognisable
    opcode that takes imm32?" — this keeps the false-positive rate
    low without disassembling every instruction.

    Also detects ``FF 25 <ptr>`` jump thunks pointing at target_va.

    Parameters
    ----------
    xbe: bytes
        Full XBE image.
    target_va: int
        Virtual address to search for as a 4-byte little-endian
        immediate.
    sections: list, optional
        Pre-parsed sections (from
        :func:`~azurik_mod.patching.xbe.parse_xbe_sections`) to avoid
        re-parsing the header in a hot loop.
    """
    data = bytes(xbe)
    text = _resolve_section(data, ".text", sections)
    if text is None:
        return []
    lo = text["raw_addr"]
    hi = lo + text["raw_size"]

    needle = struct.pack("<I", target_va & 0xFFFFFFFF)
    out: list[Imm32Reference] = []

    pos = lo
    while pos < hi:
        p = data.find(needle, pos, hi)
        if p < 0:
            break
        if p > 0:
            prev = data[p - 1]
            if prev in _IMM32_OPCODES:
                try:
                    va = file_to_va(p - 1)
                except Exception:
                    va = 0
                kind = "push" if prev == 0x68 else "mov"
                out.append(Imm32Reference(
                    callsite_va=va,
                    callsite_file_offset=p - 1,
                    opcode=prev,
                    kind=kind,
                ))
            elif p >= 2 and data[p - 2] == 0xFF and data[p - 1] == 0x25:
                # JMP DWORD PTR [imm32]  — an import thunk.
                try:
                    va = file_to_va(p - 2)
                except Exception:
                    va = 0
                out.append(Imm32Reference(
                    callsite_va=va,
                    callsite_file_offset=p - 2,
                    opcode=0xFF,
                    kind="jmp-thunk",
                ))
        pos = p + 1

    return out


# ---------------------------------------------------------------------------
# Float constant finder
# ---------------------------------------------------------------------------

def find_floats_in_range(xbe: bytes | bytearray,
                         lo_value: float, hi_value: float, *,
                         widths: tuple[int, ...] = (4, 8),
                         sections: list | None = None
                         ) -> list[FloatHit]:
    """Every float in ``.rdata`` whose numeric value lies in
    ``[lo_value, hi_value]``.

    Scans at every byte offset (not just 4-aligned) because the
    compiler emits floats into arbitrary ``.rdata`` positions
    depending on alignment padding.  Defaults to checking both
    ``float32`` and ``float64``.

    Non-finite floats (NaN / ±inf) and zero are excluded to keep
    the hit list useful — every block of zero bytes in .rdata
    would otherwise appear as thousands of 0.0 hits.
    """
    data = bytes(xbe)
    rdata = _resolve_section(data, ".rdata", sections)
    if rdata is None:
        return []
    lo = rdata["raw_addr"]
    hi = lo + rdata["raw_size"]
    import math as _math

    hits: list[FloatHit] = []
    for width in widths:
        fmt = "<f" if width == 4 else "<d"
        for off in range(lo, hi - width + 1):
            try:
                (val,) = struct.unpack_from(fmt, data, off)
            except struct.error:
                continue
            if not _math.isfinite(val):
                continue
            if val == 0.0:
                continue
            if lo_value <= val <= hi_value:
                try:
                    va = file_to_va(off)
                except Exception:
                    va = 0
                hits.append(FloatHit(
                    va=va, file_offset=off,
                    width=width, value=val))
    # De-duplicate by file offset + width (a double at offset N is
    # distinct from a float at N even if values match numerically).
    seen: set[tuple[int, int]] = set()
    uniq: list[FloatHit] = []
    for h in hits:
        key = (h.file_offset, h.width)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(h)
    return uniq


# ---------------------------------------------------------------------------
# String scanner
# ---------------------------------------------------------------------------

_PRINTABLE = re.compile(rb"[\x20-\x7E]+")


def find_strings(xbe: bytes | bytearray, pattern: str, *,
                 min_len: int = 4,
                 regex: bool = False,
                 sections: list | None = None,
                 limit: int = 200,
                 ) -> list[StringHit]:
    """Find null-terminated ASCII strings matching ``pattern``.

    Scans ``.rdata`` + ``.data`` (the two sections Azurik stores
    string literals in).  When ``regex`` is False (default)
    ``pattern`` is treated as a case-sensitive substring.  With
    ``regex=True`` the pattern is compiled as a Python regex and
    applied to the whole decoded string.

    ``min_len`` filters out short accidental matches (the default
    of 4 cuts hits like ``".com"`` embedded in binary chaff).
    """
    data = bytes(xbe)
    if sections is None:
        _, sections = parse_xbe_sections(data)
    targets = [s for s in sections if s["name"] in (".rdata", ".data")]

    if regex:
        rx = re.compile(pattern)
        def match(s: str) -> bool: return bool(rx.search(s))
    else:
        def match(s: str) -> bool: return pattern in s

    hits: list[StringHit] = []
    for sec in targets:
        lo = sec["raw_addr"]
        hi = lo + sec["raw_size"]
        for m in _PRINTABLE.finditer(data, lo, hi):
            s = m.group(0).decode("ascii", errors="replace")
            if len(s) < min_len:
                continue
            if not match(s):
                continue
            try:
                va = file_to_va(m.start())
            except Exception:
                va = 0
            hits.append(StringHit(va=va, file_offset=m.start(), text=s))
            if len(hits) >= limit:
                return hits
    return hits


# ---------------------------------------------------------------------------
# Hexdump helpers
# ---------------------------------------------------------------------------

def hex_dump(xbe: bytes | bytearray, address: int, *,
             length: int = 64,
             is_va: bool = True,
             bss_as_zeros: bool = True) -> list[HexRow]:
    """Render ``length`` bytes starting at ``address`` as
    16-bytes-per-row :class:`HexRow` records.

    ``is_va`` controls whether ``address`` is a Virtual Address
    (default) or a raw file offset.

    ``bss_as_zeros`` handles VAs that live in a ``.data`` section
    past the file-backed portion (BSS zero-fill at runtime): when
    ``True`` (default), synthetic zero rows are returned so shim
    authors can still see the layout at that VA; when ``False``,
    the dump returns an empty list and the caller is expected to
    error out.
    """
    data = bytes(xbe)
    if is_va:
        try:
            start = va_to_file(address)
        except Exception:
            return []
        start_va = address
    else:
        start = address
        try:
            start_va = file_to_va(start)
        except Exception:
            return []

    # BSS fallback: VA resolves but the file offset is past the
    # actual file.  Synthesise NUL rows so the caller sees the
    # layout + knows those bytes will be zero at runtime.
    if start >= len(data):
        if not (is_va and bss_as_zeros):
            return []
        rows: list[HexRow] = []
        off = start
        va = start_va
        remaining = length
        while remaining > 0:
            chunk = min(remaining, 16)
            rows.append(HexRow(
                va=va, file_offset=off, raw=b"\x00" * chunk))
            off += chunk
            va += chunk
            remaining -= chunk
        return rows
    if start < 0:
        return []
    end = min(start + length, len(data))

    rows = []
    off = start
    va = start_va
    while off < end:
        chunk_end = min(off + 16, end)
        row = HexRow(va=va, file_offset=off, raw=data[off:chunk_end])
        rows.append(row)
        va += 16
        off += 16
    return rows


# ---------------------------------------------------------------------------
# Address helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AddressInfo:
    """Result of resolving a numeric address against XBE sections."""

    value: int
    kind: str  # "va" | "file"
    va: int | None
    file_offset: int | None
    section: str | None

    @property
    def human(self) -> str:
        """One-line human-readable summary."""
        va = f"VA 0x{self.va:08X}" if self.va is not None else "no-VA"
        fo = (f"file 0x{self.file_offset:06X}"
              if self.file_offset is not None else "off-image")
        sec = f"in .{self.section}" if self.section else "no-section"
        return f"{va}  {fo}  {sec}  (input kind: {self.kind})"


def resolve_address(xbe: bytes | bytearray, value: int, *,
                    force_kind: str | None = None,
                    sections: list | None = None,
                    ) -> AddressInfo:
    """Figure out whether ``value`` looks like a VA or a file offset
    and return matching representations.

    Heuristic: XBE virtual addresses start at ``base_addr`` (usually
    ``0x00010000``) and rarely exceed a few megabytes above that.
    File offsets are usually in the same numeric range but we tell
    them apart by checking which conversion succeeds.

    Force with ``force_kind="va"`` or ``force_kind="file"`` to skip
    the heuristic (handy for offsets inside the ``base_addr``
    range).
    """
    data = bytes(xbe)
    # Always need ``base_addr`` for the kind-guess heuristic; if
    # ``sections`` was supplied we only reparse the 4-byte header
    # field, not the full section table.
    if sections is None:
        base_addr, sections = parse_xbe_sections(data)
    else:
        base_addr = struct.unpack_from("<I", data, 0x104)[0]

    # Plausibility cap: image_size from the XBE header gives the
    # highest VA the loader will ever map.  Anything past
    # ``base_addr + image_size`` is guaranteed junk.
    image_size = struct.unpack_from("<I", data, 0x10C)[0]
    max_plausible_va = base_addr + max(image_size, 0x10_000_000)

    kind = force_kind
    va: int | None = None
    file_offset: int | None = None
    section: str | None = None

    if kind is None:
        kind = "va" if value >= base_addr else "file"

    try:
        if kind == "va":
            if value > max_plausible_va:
                raise ValueError(
                    f"VA 0x{value:X} is past the image's virtual end "
                    f"(0x{max_plausible_va:X})")
            file_offset = va_to_file(value)
            va = value
        else:
            va = file_to_va(value)
            file_offset = value
    except Exception:
        pass

    if va is not None:
        for sec in sections:
            vlo = sec["vaddr"]
            vhi = vlo + max(sec["vsize"], sec["raw_size"])
            if vlo <= va < vhi:
                section = sec["name"].lstrip(".")
                break

    return AddressInfo(
        value=value, kind=kind, va=va,
        file_offset=file_offset, section=section)


__all__ = [
    "AddressInfo",
    "FloatHit",
    "HexRow",
    "Imm32Reference",
    "StringHit",
    "find_floats_in_range",
    "find_imm32_references",
    "find_strings",
    "hex_dump",
    "resolve_address",
]
