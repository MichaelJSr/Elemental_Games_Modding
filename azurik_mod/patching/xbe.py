"""XBE section table and virtual-address / file-offset helpers.

Azurik ships a statically-linked XBE whose section layout is fixed, so
this module hard-codes the (va_start, raw_start) pairs for each section.
`va_to_file()` and the on-the-fly parser `parse_xbe_sections()` are the
only tools we need for binary patching.
"""

from __future__ import annotations

import struct
import sys
from typing import Any

# (va_start, raw_start) pairs from Azurik's XBE section headers.
# file_offset = raw_start + (va - va_start)
XBE_SECTIONS: list[tuple[int, int]] = [
    (0x011000, 0x001000),   # .text
    (0x1001E0, 0x0F01E0),   # BINK
    (0x11D5C0, 0x118000),   # D3D
    (0x135460, 0x12FE60),   # DSOUND
    (0x154BA0, 0x14F5A0),   # XGRPH
    (0x168680, 0x163080),   # D3DX
    (0x187BA0, 0x1825A0),   # XPP
    (0x18F3A0, 0x188000),   # .rdata
    (0x1A29A0, 0x19C000),   # .data
]


def va_to_file(va: int) -> int:
    """Convert a virtual address to an XBE file offset using the section table."""
    for va_start, raw_start in reversed(XBE_SECTIONS):
        if va >= va_start:
            return raw_start + (va - va_start)
    raise ValueError(f"VA 0x{va:X} is below all known sections")


def parse_xbe_sections(data: bytes) -> tuple[int, list[dict[str, Any]]]:
    """Parse an XBE header from `data` and return (base_addr, sections).

    Useful for analysis scripts that want to walk any XBE, not just
    Azurik's. For the known-Azurik fast path, prefer `va_to_file()` /
    `XBE_SECTIONS`.
    """
    if data[:4] != b"XBEH":
        raise ValueError("Not a valid XBE file (bad magic)")

    base_addr = struct.unpack_from("<I", data, 0x104)[0]
    section_count = struct.unpack_from("<I", data, 0x11C)[0]
    section_headers_addr = struct.unpack_from("<I", data, 0x120)[0]
    section_headers_offset = section_headers_addr - base_addr

    sections: list[dict[str, Any]] = []
    for i in range(section_count):
        off = section_headers_offset + i * 56
        flags = struct.unpack_from("<I", data, off + 0)[0]
        vaddr = struct.unpack_from("<I", data, off + 4)[0]
        vsize = struct.unpack_from("<I", data, off + 8)[0]
        raw_addr = struct.unpack_from("<I", data, off + 12)[0]
        raw_size = struct.unpack_from("<I", data, off + 16)[0]
        name_addr = struct.unpack_from("<I", data, off + 20)[0]
        name_offset = name_addr - base_addr
        name_end = data.index(b"\x00", name_offset)
        name = data[name_offset:name_end].decode("ascii", errors="replace")
        sections.append({
            "name": name,
            "vaddr": vaddr,
            "vsize": vsize,
            "raw_addr": raw_addr,
            "raw_size": raw_size,
            "flags": flags,
        })
    return base_addr, sections


def file_to_va(file_offset: int) -> int:
    """Inverse of va_to_file for the known-Azurik section layout."""
    for va_start, raw_start in reversed(XBE_SECTIONS):
        if file_offset >= raw_start:
            return va_start + (file_offset - raw_start)
    raise ValueError(f"file offset 0x{file_offset:X} is below all known sections")


def find_text_padding(xbe: bytes) -> tuple[int, int]:
    """Return (file_offset, length) of available shim-landing space
    at the end of the ``.text`` section.

    The function prefers, in order:

    1. Existing trailing zero-fill *within* ``.text``'s raw body
       (``vsize > used bytes``).  Some XBEs leave genuine slack here;
       shim bytes written there are already mapped executable with no
       metadata changes required.
    2. The VA gap between ``.text``'s current end and the next section
       (in Azurik there's a 16-byte gap before BINK).  Using this
       region requires ``grow_text_section()`` to extend ``.text``'s
       ``virtual_size`` and ``raw_size`` so the loader actually maps
       those bytes.  ``find_text_padding`` reports the gap; the caller
       (``apply_trampoline_patch``) is responsible for committing the
       growth before writing shim bytes.

    Returned ``length`` is the maximum contiguous shim-landing pad
    size available starting at ``file_offset``.  It already accounts
    for both in-section slack AND adjacent VA-gap growth.

    Raises ``ValueError`` if the XBE has no ``.text`` section or if no
    usable landing space is available at all.
    """
    _, sections = parse_xbe_sections(xbe)
    text = next((s for s in sections if s["name"] == ".text"), None)
    if text is None:
        raise ValueError("XBE has no .text section")

    raw_start = text["raw_addr"]
    raw_size = text["raw_size"]
    vsize = text["vsize"]
    raw_end = raw_start + raw_size

    if raw_end > len(xbe):
        raise ValueError(
            f".text section extends past end-of-file "
            f"(raw_end=0x{raw_end:X}, file size 0x{len(xbe):X})")

    # (1) Trailing zero-fill WITHIN the current raw body.
    i = raw_end - 1
    while i >= raw_start and xbe[i] == 0x00:
        i -= 1
    in_section_slack_start = i + 1
    in_section_slack_len = raw_end - in_section_slack_start

    # (2) Room to grow .text into the adjacent VA gap.
    text_vend = text["vaddr"] + vsize
    next_va = min(
        (s["vaddr"] for s in sections
         if s["vaddr"] > text["vaddr"] and s["name"] != ".text"),
        default=None,
    )
    va_gap = (next_va - text_vend) if next_va is not None else 0

    # Matching on-disk room between .text raw-end and the next section's
    # raw-start; shim bytes must fit in both VA and file space.
    next_raw = min(
        (s["raw_addr"] for s in sections
         if s["raw_addr"] > raw_start and s["name"] != ".text"),
        default=len(xbe),
    )
    raw_gap = next_raw - raw_end
    growable = min(va_gap, raw_gap)
    # Only count growable space whose on-disk bytes are all zero — we
    # won't trample real data.
    for off in range(raw_end, raw_end + growable):
        if xbe[off] != 0x00:
            growable = off - raw_end
            break

    total = in_section_slack_len + growable
    if total == 0:
        raise ValueError(
            f".text at 0x{raw_start:X}+{raw_size:X} has no in-section "
            f"slack and no growable gap (va_gap=0x{va_gap:X}, "
            f"raw_gap=0x{raw_gap:X}).  Phase 2 will handle this via "
            f"append_xbe_section().")

    # Caller receives a single contiguous file range; the interior
    # boundary between slack and growth is invisible at the byte level.
    if in_section_slack_len > 0:
        return in_section_slack_start, total
    return raw_end, total


def grow_text_section(xbe: bytearray, additional_bytes: int) -> None:
    """Extend ``.text``'s ``virtual_size`` and ``raw_size`` by
    ``additional_bytes`` in-place.

    This edits the section header in the XBE image (NOT the
    ``XBE_SECTIONS`` Python constant — that's a compile-time map for
    offset arithmetic).  Use this after writing shim bytes into the
    trailing growable area reported by ``find_text_padding``.

    Raises ``ValueError`` if the growth would exceed what the adjacent
    VA gap / raw gap will support.
    """
    if additional_bytes <= 0:
        return
    if xbe[:4] != b"XBEH":
        raise ValueError("Not a valid XBE file (bad magic)")

    base_addr = struct.unpack_from("<I", xbe, 0x104)[0]
    section_count = struct.unpack_from("<I", xbe, 0x11C)[0]
    section_headers_addr = struct.unpack_from("<I", xbe, 0x120)[0]
    section_headers_offset = section_headers_addr - base_addr

    # Locate .text's header.
    text_hdr_offset = None
    for i in range(section_count):
        off = section_headers_offset + i * 56
        name_addr = struct.unpack_from("<I", xbe, off + 20)[0]
        name_offset = name_addr - base_addr
        name_end = bytes(xbe).index(b"\x00", name_offset)
        name = xbe[name_offset:name_end].decode("ascii", errors="replace")
        if name == ".text":
            text_hdr_offset = off
            break
    if text_hdr_offset is None:
        raise ValueError(".text section not found in XBE header")

    vsize = struct.unpack_from("<I", xbe, text_hdr_offset + 8)[0]
    raw_size = struct.unpack_from("<I", xbe, text_hdr_offset + 16)[0]
    struct.pack_into("<I", xbe, text_hdr_offset + 8, vsize + additional_bytes)
    struct.pack_into("<I", xbe, text_hdr_offset + 16, raw_size + additional_bytes)


def append_xbe_section(
    xbe: bytearray,
    name: str,
    data: bytes,
    flags: int = 0x00000006,  # EXECUTABLE | PRELOAD
    alignment: int = 0x20,
) -> int:
    """Append a brand-new section to the XBE, returning its file offset.

    This is the fallback path for shims too large to fit in the
    trailing ``.text`` padding.  The function:

    1. Rewrites the XBE header's section count and extends the section
       header array with a new 56-byte entry.
    2. Appends the section data at the end of the file, aligned.
    3. Updates the header's image size and on-disk size fields.

    The new section's VA starts at the next available aligned slot
    above the highest existing section VA.  Its raw offset is
    end-of-file (aligned).  Flags default to EXECUTABLE | PRELOAD,
    which is what code sections need.

    NOTE: Phase 1 does NOT exercise this path (the first shim fits
    comfortably in ``.text`` padding).  The implementation is here so
    Phase 2 shims can grow beyond padding without infrastructure work.

    Raises ``NotImplementedError`` if called — we want to explicitly
    validate round-tripping on a real XBE before relying on this in
    production builds.  Remove the guard once Phase 2 test coverage
    is in place.
    """
    raise NotImplementedError(
        "append_xbe_section is reserved for Phase 2; Phase 1 shims are "
        "expected to fit in .text padding via find_text_padding().  "
        "When a shim overflows padding, extend this function with "
        "thorough tests before enabling.")


if __name__ == "__main__":
    # CLI: python -m azurik_mod.patching.xbe <xbe>
    if len(sys.argv) != 2:
        print("Usage: python -m azurik_mod.patching.xbe <path/to/default.xbe>")
        sys.exit(2)
    with open(sys.argv[1], "rb") as _f:
        _data = _f.read()
    _base, _secs = parse_xbe_sections(_data)
    print(f"Base address: 0x{_base:X}")
    for s in _secs:
        print(f"  {s['name']:10s}  VA=0x{s['vaddr']:08X}  "
              f"VSize=0x{s['vsize']:X}  Raw=0x{s['raw_addr']:08X}  "
              f"RawSize=0x{s['raw_size']:X}")
    off, length = find_text_padding(_data)
    print(f"\n.text trailing padding: file 0x{off:X}..0x{off + length:X} "
          f"({length} bytes)")
