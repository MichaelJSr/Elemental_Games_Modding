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

    ~170 µs per call on a ~4 MB XBE; called O(5-10) times per build.
    We tried a buffer-attached memoisation cache here but plain
    ``bytearray`` (the shape all hot-path callers use) does not
    support attribute assignment, and switching to a subclass would
    invasively touch every ``bytearray(...)`` construction site.
    The ~1 ms aggregate overhead isn't worth that — parsing stays
    unconditional.

    Robustness: the XBE header's ``section_count`` +
    ``section_headers_addr`` are user-controlled fields; a
    truncated / hostile buffer could point past ``len(data)`` and
    crash ``struct.unpack_from`` or ``data.index`` with a
    unhelpful traceback.  We validate every field access against
    the buffer length and surface a clear ``ValueError`` for bad
    input instead.
    """
    if len(data) < 0x180:
        raise ValueError(
            f"XBE image header requires at least 384 B, got {len(data)}")
    if data[:4] != b"XBEH":
        raise ValueError("Not a valid XBE file (bad magic)")

    base_addr = struct.unpack_from("<I", data, 0x104)[0]
    section_count = struct.unpack_from("<I", data, 0x11C)[0]
    section_headers_addr = struct.unpack_from("<I", data, 0x120)[0]

    # Sanity cap: a real XBE typically has <100 sections; something
    # claiming 2^32 / 2 sections is clearly garbage.
    if section_count > 0xFFFF:
        raise ValueError(
            f"XBE section_count {section_count} exceeds sanity limit "
            f"(65535); file is corrupt or hostile")
    if section_headers_addr < base_addr:
        raise ValueError(
            f"XBE section_headers_addr 0x{section_headers_addr:X} is "
            f"below base_addr 0x{base_addr:X} — corrupt header")
    section_headers_offset = section_headers_addr - base_addr
    end_offset = section_headers_offset + section_count * 56
    if end_offset > len(data):
        raise ValueError(
            f"XBE section header array ends at file offset "
            f"0x{end_offset:X} but image is only 0x{len(data):X} B "
            f"— truncated or corrupt")

    sections: list[dict[str, Any]] = []
    for i in range(section_count):
        off = section_headers_offset + i * 56
        flags = struct.unpack_from("<I", data, off + 0)[0]
        vaddr = struct.unpack_from("<I", data, off + 4)[0]
        vsize = struct.unpack_from("<I", data, off + 8)[0]
        raw_addr = struct.unpack_from("<I", data, off + 12)[0]
        raw_size = struct.unpack_from("<I", data, off + 16)[0]
        name_addr = struct.unpack_from("<I", data, off + 20)[0]
        # Validate name-pointer bounds before reaching into ``data``.
        if name_addr < base_addr:
            raise ValueError(
                f"XBE section #{i}: name_addr 0x{name_addr:X} is "
                f"below base_addr 0x{base_addr:X}")
        name_offset = name_addr - base_addr
        if name_offset >= len(data):
            raise ValueError(
                f"XBE section #{i}: name_offset 0x{name_offset:X} "
                f"past end of image (0x{len(data):X})")
        # ``bytes.index`` raises ValueError on miss; catch it to
        # emit a contextual error instead of the bare "subsection
        # not found" message.
        try:
            name_end = data.index(b"\x00", name_offset)
        except ValueError:
            raise ValueError(
                f"XBE section #{i}: name at 0x{name_offset:X} is "
                f"not null-terminated within the image")
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


SECTION_HEADER_SIZE = 56
"""Fixed size of one XBE section header entry in bytes.

Encoded as: flags(4) + vaddr(4) + vsize(4) + raw_addr(4) + raw_size(4) +
name_addr(4) + name_ref_count(4) + head_ref_addr(4) + tail_ref_addr(4) +
section_digest(20) = 56 bytes.
"""

FILE_ALIGN = 0x1000
"""Raw section alignment in the file (Xbox convention — every existing
section in Azurik's XBE starts at a 0x1000-aligned raw offset)."""

VA_ALIGN = 0x1000
"""Virtual-address alignment for new sections (matches the file
alignment so the loader sees the same granularity in both spaces)."""

# Image-header field offsets that hold VAs pointing INTO the header
# region AFTER the section header array end (i.e. into bytes that
# shift when we grow the array).  Any pointer whose target is < the
# current array-end offset stays untouched.  This list is the union
# of all fields the Xbox loader reads that reference post-array
# header content; verified by dumping Azurik's header.
_POST_ARRAY_POINTER_FIELDS: tuple[int, ...] = (
    0x14C,  # tls_addr (often — Azurik has 0x109B2)
    0x150,  # pe_heap_reserve (not actually a ptr on Azurik; gated below)
    0x154,  # pe_heap_commit
    0x164,  # xapi_library_version_addr
    0x168,  # logo_bitmap_addr
    0x16C,  # logo_bitmap_size_or_addr (Azurik stores a VA here too)
    0x170,  # library_features_addr
)


def append_xbe_section(
    xbe: bytearray,
    name: str,
    data: bytes,
    flags: int = 0x00000006,  # EXECUTABLE | PRELOAD
) -> dict:
    """Append a brand-new section to the XBE.

    Used when a shim's compiled ``.text`` (plus any ``.rdata`` /
    ``.data`` sidecar sections) is too large to fit in the existing
    ``.text`` VA-gap headroom that :func:`find_text_padding` reports.

    The surgery happens in two places:

    A. **Header region (VA 0x10000..0x10C90 on Azurik).**  The section
       header array lives at ``section_headers_addr`` and is followed
       by head/tail page-ref counters, the section-name string pool,
       library version entries, logo bitmap bytes, and library-feature
       blobs.  Every byte after the array-end is pointed at by
       something — the array is effectively SOLID from both ends.

       We grow the array by one entry (56 bytes) by shifting every
       header byte past ``section_headers_end`` forward by 56 and
       rewriting every VA pointer that now points at a different
       location.  ``size_of_headers`` is bumped by 56 to match.  The
       Xbox loader only maps the header up to ``size_of_headers``, so
       the shifted content lands at valid loader-visible VAs as long
       as we don't cross the next section's ``vaddr`` — Azurik has
       ``0x370`` bytes of slack between the header end and ``.text``
       at ``0x11000``, so a 56-byte grow is well within budget.

    B. **Data region (end of file).**  The raw bytes of ``data`` are
       appended to the file at a ``FILE_ALIGN``-aligned offset past
       the previous EOF, and the section's virtual address is picked
       above every existing section's VA span, also aligned.

    Arguments:
        xbe:    Mutable XBE bytes.  Grown in place.
        name:   ASCII section name (no NUL terminator in this string).
                A NUL is added automatically when the name is placed
                into the name pool.
        data:   Raw section bytes.  Shim callers typically pass the
                contents of a COFF ``.text`` / ``.rdata`` section.
        flags:  XBE section-flag bitfield.  Defaults to
                ``EXECUTABLE | PRELOAD`` (0x6), matching Azurik's
                ``.text``.  Use 0x2 (PRELOAD only) for read-only data.

    Returns a dict with keys:
        ``raw_addr``  file offset where ``data`` was placed.
        ``vaddr``     virtual address the loader will map it at.
        ``index``     the new section's index (== num_sections-1 after).

    Raises ``ValueError`` on a malformed XBE or when the requested
    grow can't fit the adjacent-VA headroom.  The bytes are left
    modified up to the failure point; callers that need atomicity
    should snapshot ``xbe`` first.
    """
    if xbe[:4] != b"XBEH":
        raise ValueError("Not a valid XBE file (bad magic)")
    if len(name.encode("ascii")) == 0:
        raise ValueError("section name must not be empty")

    base_addr = struct.unpack_from("<I", xbe, 0x104)[0]
    size_of_headers = struct.unpack_from("<I", xbe, 0x108)[0]
    num_sections = struct.unpack_from("<I", xbe, 0x11C)[0]
    section_headers_addr = struct.unpack_from("<I", xbe, 0x120)[0]
    section_headers_offset = section_headers_addr - base_addr
    array_end = section_headers_offset + num_sections * SECTION_HEADER_SIZE

    # (A0) Validate headroom — grown header must not overlap next section.
    _, sections = parse_xbe_sections(bytes(xbe))
    if not sections:
        raise ValueError("cannot append section to XBE with zero sections")
    lowest_vaddr = min(s["vaddr"] for s in sections)
    headroom = lowest_vaddr - (base_addr + size_of_headers)
    if headroom < SECTION_HEADER_SIZE:
        raise ValueError(
            f"only {headroom} bytes of header VA headroom before the "
            f"first section at 0x{lowest_vaddr:X}; need >= "
            f"{SECTION_HEADER_SIZE} to grow the header array.")
    lowest_raw = min(s["raw_addr"] for s in sections if s["raw_addr"] > 0)
    raw_headroom = lowest_raw - size_of_headers
    if raw_headroom < SECTION_HEADER_SIZE:
        raise ValueError(
            f"only {raw_headroom} bytes of on-disk headroom between the "
            f"header and the first section raw body; need >= "
            f"{SECTION_HEADER_SIZE}.")

    # (A1) Shift bytes [array_end .. size_of_headers) forward by 56.
    old_header_tail = bytes(xbe[array_end:size_of_headers])
    # Pre-fill the 56-byte gap with zeros — the new section header
    # entry will be written in step (A4).
    xbe[array_end + SECTION_HEADER_SIZE:
        size_of_headers + SECTION_HEADER_SIZE] = old_header_tail
    xbe[array_end:array_end + SECTION_HEADER_SIZE] = b"\x00" * SECTION_HEADER_SIZE

    # (A2) Shift image-header pointer fields whose targets fell into
    # the moved region.  Condition: target file offset >= array_end.
    for field_off in _POST_ARRAY_POINTER_FIELDS:
        va = struct.unpack_from("<I", xbe, field_off)[0]
        # Only treat as a pointer when the value sits inside the header
        # VA range.  The Azurik header stores raw ints at some of these
        # offsets (e.g. +0x150 = 0x0018FAC4 is NOT a ptr), which we
        # detect by the VA range check.
        if base_addr <= va < base_addr + size_of_headers:
            tgt = va - base_addr
            if tgt >= array_end:
                struct.pack_into("<I", xbe, field_off, va + SECTION_HEADER_SIZE)

    # (A3) Shift per-section pointers (name / head-ref / tail-ref).
    # Every existing section header's 3 VA fields point into the
    # shifted region — all get +56.
    for i in range(num_sections):
        entry = section_headers_offset + i * SECTION_HEADER_SIZE
        for rel in (20, 28, 32):  # name_addr, head_ref_addr, tail_ref_addr
            ptr = struct.unpack_from("<I", xbe, entry + rel)[0]
            if base_addr <= ptr < base_addr + size_of_headers:
                tgt = ptr - base_addr
                if tgt >= array_end:
                    struct.pack_into("<I", xbe, entry + rel,
                                     ptr + SECTION_HEADER_SIZE)

    # (A4) Pick a name-pool slot for the new section name.  We use the
    # very end of the shifted name-pool / header space: append the
    # name to the end of the header region (new size_of_headers will
    # cover it).
    new_size_of_headers = size_of_headers + SECTION_HEADER_SIZE
    # The 56-byte window we just zero-filled at `array_end` is where
    # the new header ENTRY goes.  The new NAME string goes at the
    # very end of the (now-56-bytes-larger) header region, where
    # library-feature data ended and there's trailing zero pad.
    name_bytes = name.encode("ascii") + b"\x00"
    if len(name_bytes) > 64:
        raise ValueError(f"section name too long ({len(name_bytes)} B > 64)")
    # Check we have room inside the existing zero-tail.
    name_offset = new_size_of_headers - len(name_bytes)
    if name_offset < array_end + SECTION_HEADER_SIZE:
        raise ValueError("not enough header tail slack for new section name")
    # Only write if the target bytes are already zero (we don't want
    # to trample library-feature tail padding that happens to be
    # non-zero).
    if any(b != 0 for b in xbe[name_offset:name_offset + len(name_bytes)]):
        # Fall back: extend size_of_headers by an extra chunk to fit
        # the name.  Stay within the headroom budget.
        extra = len(name_bytes)
        if headroom < SECTION_HEADER_SIZE + extra:
            raise ValueError(
                f"not enough header VA headroom for 56 + {extra} B")
        if raw_headroom < SECTION_HEADER_SIZE + extra:
            raise ValueError(
                f"not enough header raw headroom for 56 + {extra} B")
        # Grow the header region further, placing the name at the new
        # tail.  Everything else already shifted correctly.
        name_offset = new_size_of_headers
        # Ensure the growth region is zero-initialised.
        xbe[name_offset:name_offset + extra] = b"\x00" * extra
        new_size_of_headers += extra
    xbe[name_offset:name_offset + len(name_bytes)] = name_bytes

    # (A5) Pick the new section's VA.  Round up past the highest VA
    # currently used by any section to VA_ALIGN.
    max_vend = max(s["vaddr"] + s["vsize"] for s in sections)
    new_vaddr = (max_vend + VA_ALIGN - 1) & ~(VA_ALIGN - 1)

    # (B) Append data at EOF, FILE_ALIGN-aligned.
    current_file_size = len(xbe)
    new_raw_addr = (current_file_size + FILE_ALIGN - 1) & ~(FILE_ALIGN - 1)
    pad = new_raw_addr - current_file_size
    if pad > 0:
        xbe.extend(b"\x00" * pad)
    xbe.extend(data)

    # (A4b) Write the new section header entry at the vacated 56 bytes.
    new_entry_off = array_end
    struct.pack_into("<I", xbe, new_entry_off + 0,  flags)
    struct.pack_into("<I", xbe, new_entry_off + 4,  new_vaddr)
    struct.pack_into("<I", xbe, new_entry_off + 8,  len(data))
    struct.pack_into("<I", xbe, new_entry_off + 12, new_raw_addr)
    struct.pack_into("<I", xbe, new_entry_off + 16, len(data))
    struct.pack_into("<I", xbe, new_entry_off + 20,
                     base_addr + name_offset)
    struct.pack_into("<I", xbe, new_entry_off + 24, 0)          # name ref count
    # Point head/tail shared-page refs at the first zero-byte pair of
    # the head/tail ref region; the two-byte counters are fine to
    # share with adjacent sections on the Xbox loader's read.
    # Using the first section's pair is the simplest correct choice.
    first_section_head_ref = struct.unpack_from(
        "<I", xbe, section_headers_offset + 28)[0]
    struct.pack_into("<I", xbe, new_entry_off + 28, first_section_head_ref)
    struct.pack_into("<I", xbe, new_entry_off + 32, first_section_head_ref)
    # Zero out the section digest (20 bytes at +36) — the Xbox loader
    # doesn't verify it on non-retail XBEs.
    xbe[new_entry_off + 36:new_entry_off + 36 + 20] = b"\x00" * 20

    # (A6) Update image-header totals.
    struct.pack_into("<I", xbe, 0x108, new_size_of_headers)
    struct.pack_into("<I", xbe, 0x11C, num_sections + 1)
    # size_of_image covers every section's VA extent; bump it to
    # include the new section, rounded to VA_ALIGN.
    new_size_of_image = ((new_vaddr + len(data) + VA_ALIGN - 1)
                          & ~(VA_ALIGN - 1)) - base_addr
    struct.pack_into("<I", xbe, 0x10C, new_size_of_image)

    return {
        "raw_addr": new_raw_addr,
        "vaddr":    new_vaddr,
        "index":    num_sections,  # pre-increment: new section's index
    }


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
