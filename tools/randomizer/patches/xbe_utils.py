"""XBE binary patching utilities — section table, VA-to-file-offset conversion,
and the PatchSpec single-source-of-truth descriptor used by every patch site."""

from __future__ import annotations

from typing import NamedTuple

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


class PatchSpec(NamedTuple):
    """Single-source-of-truth descriptor for one XBE binary patch.

    Every patch site (code or data) is declared as a PatchSpec so that
    application, verification, and external scanning tools can all iterate
    the exact same list without duplicating hex constants.

    Attributes:
        label:           Human-readable description (also printed on apply).
        va:              Virtual address of the first patched byte.
        original:        Expected bytes currently at `va` before patching.
        patch:           Bytes to write at `va`.
        is_data:         True if the region lives in .rdata/.data (matters
                         for constant-scanning tools such as
                         scan_xbe_constants.py that only look at data
                         sections).
        safety_critical: True if regressing this patch could cause memory
                         corruption or a BSOD — used by safety tests to
                         pin invariants (e.g. the 60fps step cap of 2).
    """
    label: str
    va: int
    original: bytes
    patch: bytes
    is_data: bool = False
    safety_critical: bool = False

    @property
    def file_offset(self) -> int:
        return va_to_file(self.va)


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


def apply_patch_spec(xbe_data: bytearray, spec: PatchSpec) -> bool:
    """Apply a single PatchSpec; thin wrapper over apply_xbe_patch."""
    return apply_xbe_patch(xbe_data, spec.label, spec.file_offset,
                           spec.original, spec.patch)


def verify_patch_spec(xbe_data: bytes, spec: PatchSpec) -> str:
    """Check whether a PatchSpec has been applied to `xbe_data`.

    Returns one of:
        "applied"      — bytes at offset equal spec.patch
        "original"     — bytes at offset equal spec.original (not patched)
        "mismatch"     — bytes at offset match neither
        "out-of-range" — offset is past the end of xbe_data
    """
    size = len(spec.patch)
    offset = spec.file_offset
    if offset + size > len(xbe_data):
        return "out-of-range"
    current = bytes(xbe_data[offset:offset + size])
    if current == spec.patch:
        return "applied"
    if current == spec.original:
        return "original"
    return "mismatch"
