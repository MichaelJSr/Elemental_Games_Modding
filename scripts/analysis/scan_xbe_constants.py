#!/usr/bin/env python3
"""
Scan the Azurik XBE binary's sections for frame-rate-dependent constants.

Parses XBE headers to find section offsets, then searches .rdata, .data,
and .text for known float/double patterns (1/30, 30.0, 1/6, etc.) and
cross-references them against the patch list to find unpatched instances.

The set of already-patched VAs is imported directly from
`patches.fps_unlock.FPS_DATA_PATCHED_VAS` so this scanner can never drift
out of sync with the patch definitions.

Usage:
    python scan_xbe_constants.py [path/to/default.xbe]

If no path is given, defaults to the extracted ISO in the workspace.
"""
import struct
import sys
import os

# Default location for an extracted Azurik ISO is three levels up from
# this script (outside the repo).  Callers can also pass an explicit
# path on the command line.
DEFAULT_XBE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "Azurik - Rise of Perathia (USA).xiso", "default.xbe"
)

# Import the single-source-of-truth list of patched data-section VAs.
# Add the repo root to sys.path so `azurik_mod` resolves without
# needing a `pip install -e .`.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                          "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    from azurik_mod.patches.fps_unlock import (
        FPS_DATA_PATCHED_VAS as ALREADY_PATCHED,
    )
except ImportError as _e:
    print(f"WARNING: could not import FPS_DATA_PATCHED_VAS "
          f"({_e}); falling back to empty set — ALL matches will be "
          f"reported as unpatched.", file=sys.stderr)
    ALREADY_PATCHED = set()

PATTERNS_4BYTE = [
    ("Float 1/30 (0x3D088889)", b'\x89\x88\x08\x3D', 0x3D088889),
    ("Float 30.0 (0x41F00000)", b'\x00\x00\xF0\x41', 0x41F00000),
    ("Float 1/15 (0x3D888889)", b'\x89\x88\x88\x3D', 0x3D888889),
    ("Float 2.0 (0x40000000)",  b'\x00\x00\x00\x40', 0x40000000),
    ("Float 0.5 (0x3F000000)",  b'\x00\x00\x00\x3F', 0x3F000000),
    ("Float 1/6 (0x3E2AAAAB)",  b'\xAB\xAA\x2A\x3E', 0x3E2AAAAB),
]

PATTERNS_8BYTE = [
    ("Double 1/30 (0x3FA1111111111111)", b'\x11\x11\x11\x11\x11\x11\xA1\x3F'),
    ("Double 30.0 (0x403E000000000000)", b'\x00\x00\x00\x00\x00\x00\x3E\x40'),
]


def parse_xbe_sections(data):
    """Parse XBE header to find section virtual addresses and file offsets."""
    if data[:4] != b'XBEH':
        print("ERROR: Not a valid XBE file!")
        sys.exit(1)

    base_addr = struct.unpack_from('<I', data, 0x104)[0]
    section_count = struct.unpack_from('<I', data, 0x11C)[0]
    section_headers_addr = struct.unpack_from('<I', data, 0x120)[0]

    section_headers_offset = section_headers_addr - base_addr
    sections = []
    for i in range(section_count):
        off = section_headers_offset + i * 56
        flags = struct.unpack_from('<I', data, off + 0)[0]
        vaddr = struct.unpack_from('<I', data, off + 4)[0]
        vsize = struct.unpack_from('<I', data, off + 8)[0]
        raw_addr = struct.unpack_from('<I', data, off + 12)[0]
        raw_size = struct.unpack_from('<I', data, off + 16)[0]
        name_addr = struct.unpack_from('<I', data, off + 20)[0]
        name_offset = name_addr - base_addr
        name_end = data.index(b'\x00', name_offset)
        name = data[name_offset:name_end].decode('ascii', errors='replace')
        sections.append({
            'name': name,
            'vaddr': vaddr,
            'vsize': vsize,
            'raw_addr': raw_addr,
            'raw_size': raw_size,
            'flags': flags,
        })
    return base_addr, sections


def search_section(data, section, patterns_4, patterns_8):
    """Search a section's raw data for byte patterns."""
    raw_start = section['raw_addr']
    raw_end = raw_start + section['raw_size']
    vaddr_start = section['vaddr']
    section_data = data[raw_start:raw_end]

    results = []

    for desc, pattern, _hex_val in patterns_4:
        idx = 0
        while True:
            pos = section_data.find(pattern, idx)
            if pos == -1:
                break
            va = vaddr_start + pos
            if pos % 4 == 0:
                results.append((va, desc, 4))
            idx = pos + 1

    for desc, pattern in patterns_8:
        idx = 0
        while True:
            pos = section_data.find(pattern, idx)
            if pos == -1:
                break
            va = vaddr_start + pos
            if pos % 8 == 0:
                results.append((va, desc, 8))
            elif pos % 4 == 0:
                results.append((va, desc + " [4-aligned only]", 8))
            idx = pos + 1

    return results


def main():
    xbe_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_XBE_PATH
    xbe_path = os.path.abspath(xbe_path)

    print(f"Reading XBE: {xbe_path}")
    with open(xbe_path, 'rb') as f:
        data = f.read()
    print(f"File size: {len(data)} bytes")

    base_addr, sections = parse_xbe_sections(data)
    print(f"XBE base address: 0x{base_addr:X}")
    print(f"Sections ({len(sections)}):")
    for sec in sections:
        print(f"  {sec['name']:15s}  VA=0x{sec['vaddr']:08X}  "
              f"VSize=0x{sec['vsize']:X}  Raw=0x{sec['raw_addr']:08X}  "
              f"RawSize=0x{sec['raw_size']:X}")
    print()

    search_sections = ['.rdata', '.data', '.text']
    print(f"Searching sections: {search_sections}")
    print("=" * 80)

    all_results = []
    for sec in sections:
        if sec['name'] not in search_sections:
            continue
        results = search_section(data, sec, PATTERNS_4BYTE, PATTERNS_8BYTE)
        for va, desc, size in results:
            all_results.append((va, desc, size, sec['name']))
    all_results.sort(key=lambda x: x[0])

    by_pattern = {}
    for va, desc, size, sec_name in all_results:
        by_pattern.setdefault(desc, []).append((va, sec_name))

    for desc in sorted(by_pattern.keys()):
        matches = by_pattern[desc]
        new_matches = [(va, sn) for va, sn in matches if va not in ALREADY_PATCHED]
        patched = [(va, sn) for va, sn in matches if va in ALREADY_PATCHED]

        print(f"\n{'='*60}")
        print(f"  {desc}")
        print(f"  Total: {len(matches)} | Patched: {len(patched)} | New: {len(new_matches)}")
        print(f"{'='*60}")

        if patched:
            print(f"  Already patched:")
            for va, sn in patched:
                print(f"    0x{va:06X} [{sn}]")

        if new_matches:
            print(f"  *** POTENTIALLY UNPATCHED: ***")
            for va, sn in new_matches:
                print(f"    >>> 0x{va:06X} [{sn}]")
        else:
            print(f"  All instances accounted for.")

    print("\n" + "=" * 80)
    print("SUMMARY — POTENTIALLY UNPATCHED CONSTANTS")
    print("=" * 80)

    unpatched = [(va, desc, sec_name)
                 for va, desc, size, sec_name in all_results
                 if va not in ALREADY_PATCHED]
    unpatched.sort()

    if not unpatched:
        print("  No unpatched frame-rate constants found!")
    else:
        for va, desc, sec_name in unpatched:
            print(f"  0x{va:06X}  [{sec_name:8s}]  {desc}")

    print(f"\nTotal unpatched candidates: {len(unpatched)}")


if __name__ == '__main__':
    main()
