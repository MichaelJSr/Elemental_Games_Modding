"""XBE binary patching utilities — section table and VA-to-file-offset conversion."""

from __future__ import annotations

# (va_start, raw_start) pairs from the XBE section headers.
# file_offset = raw_start + (va - va_start)
XBE_SECTIONS = [
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


def apply_xbe_patch(xbe_data: bytearray, label: str, offset: int,
                    original: bytes, patch: bytes) -> bool:
    """Apply a single XBE binary patch with verification."""
    if len(patch) != len(original):
        print(f"  ERROR: {label} — original ({len(original)}B) and "
              f"patch ({len(patch)}B) lengths differ")
        return False
    size = len(original)
    if offset + size > len(xbe_data):
        print(f"  WARNING: {label} — offset 0x{offset:X} out of range, skipping")
        return False
    current = bytes(xbe_data[offset:offset + size])
    if current == original:
        xbe_data[offset:offset + size] = patch
        print(f"  {label}")
        return True
    if current == patch:
        print(f"  {label} (already applied)")
        return True
    print(f"  WARNING: {label} — bytes at 0x{offset:X} don't match "
          f"(got {current.hex()}, expected {original.hex()})")
    return False
