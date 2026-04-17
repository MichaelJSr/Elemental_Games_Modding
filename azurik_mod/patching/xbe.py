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
