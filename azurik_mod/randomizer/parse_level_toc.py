#!/usr/bin/env python3
"""Parse level XBR files — extract TOC, list tags, dump node section entities.

Usage:
    python parse_level_toc.py game_files/gamedata/a3.xbr
    python parse_level_toc.py game_files/gamedata/a3.xbr --tag node
    python parse_level_toc.py game_files/gamedata/a3.xbr --strings node
    python parse_level_toc.py game_files/gamedata/a3.xbr --dump node --out a3_node.bin
"""
import argparse
import struct
import sys
from pathlib import Path


def read_u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def read_tag(data, offset):
    raw = data[offset:offset+4]
    try:
        return raw.decode("ascii")
    except (UnicodeDecodeError, ValueError):
        return raw.hex()


def parse_header(data):
    """Parse xobx header (0x00-0x3F)."""
    magic = data[0:4]
    if magic != b"xobx":
        print(f"ERROR: Bad magic: {magic!r} (expected b'xobx')")
        sys.exit(1)

    info = {
        "magic": "xobx",
        "field_04": read_u32(data, 0x04),
        "field_08": read_u32(data, 0x08),
        "field_0C": read_u32(data, 0x0C),
        "field_10": read_u32(data, 0x10),
        "field_14": read_u32(data, 0x14),
        "field_18": read_u32(data, 0x18),
        "field_1C": read_u32(data, 0x1C),
    }
    return info


def parse_toc(data):
    """Parse TOC entries starting at 0x40, each 16 bytes."""
    entries = []
    offset = 0x40
    max_offset = min(len(data), 0x40 + 0x100000)  # safety limit

    while offset + 16 <= max_offset:
        size = read_u32(data, offset)
        tag = read_tag(data, offset + 4)
        flags = read_u32(data, offset + 8)
        file_offset = read_u32(data, offset + 12)

        # Stop on all-zero sentinel
        if size == 0 and flags == 0 and file_offset == 0:
            break

        entries.append({
            "index": len(entries),
            "toc_offset": offset,
            "size": size,
            "tag": tag,
            "flags": flags,
            "file_offset": file_offset,
        })
        offset += 16

    return entries


def find_strings(data, start, length, min_len=4):
    """Find printable ASCII strings in a binary region."""
    end = start + length
    strings = []
    current = bytearray()
    string_start = start

    for i in range(start, min(end, len(data))):
        b = data[i]
        if 32 <= b < 127:
            if not current:
                string_start = i
            current.append(b)
        else:
            if len(current) >= min_len:
                strings.append((string_start, current.decode("ascii")))
            current = bytearray()

    if len(current) >= min_len:
        strings.append((string_start, current.decode("ascii")))

    return strings


def find_floats_near(data, offset, window=128):
    """Find plausible IEEE 754 floats near an offset."""
    floats = []
    start = max(0, offset - window)
    end = min(len(data) - 4, offset + window)
    for i in range(start, end, 4):
        val = struct.unpack_from("<f", data, i)[0]
        # Filter for plausible 3D coordinates (skip NaN, Inf, very tiny/huge)
        if val != 0.0 and abs(val) > 0.001 and abs(val) < 100000 and val == val:
            floats.append((i, val))
    return floats


def main():
    parser = argparse.ArgumentParser(description="Parse Azurik level XBR TOC")
    parser.add_argument("xbr", help="Path to .xbr file")
    parser.add_argument("--tag", help="Filter TOC entries by tag type (e.g., node, rdms, surf)")
    parser.add_argument("--strings", metavar="TAG",
                        help="Extract ASCII strings from entries of this tag type")
    parser.add_argument("--dump", metavar="TAG",
                        help="Dump raw binary of first entry matching this tag")
    parser.add_argument("--out", help="Output file for --dump")
    parser.add_argument("--search", help="Search for a string in the binary and show context")
    parser.add_argument("--floats", action="store_true",
                        help="Show nearby floats when using --search")
    args = parser.parse_args()

    path = Path(args.xbr)
    if not path.exists():
        print(f"ERROR: File not found: {path}")
        sys.exit(1)

    data = path.read_bytes()
    header = parse_header(data)
    entries = parse_toc(data)

    print(f"File: {path.name} ({len(data):,} bytes / {len(data)/1024/1024:.2f} MB)")
    print(f"Header: field_04={header['field_04']}, field_0C={header['field_0C']}, "
          f"field_14={header['field_14']}")
    print(f"TOC entries: {len(entries)}")

    # Tag distribution
    tag_counts = {}
    tag_sizes = {}
    for e in entries:
        t = e["tag"]
        tag_counts[t] = tag_counts.get(t, 0) + 1
        tag_sizes[t] = tag_sizes.get(t, 0) + e["size"]

    print("\nTag distribution:")
    for t in sorted(tag_counts.keys()):
        cnt = tag_counts[t]
        sz = tag_sizes[t]
        print(f"  {t}: {cnt:>5} entries, {sz:>12,} bytes ({sz/1024:.1f} KB)")

    # Search mode
    if args.search:
        needle = args.search.encode("ascii")
        print(f"\nSearching for '{args.search}'...")
        pos = 0
        found = 0
        while True:
            pos = data.find(needle, pos)
            if pos == -1:
                break
            # Find which TOC entry contains this offset
            owner = None
            for e in entries:
                if e["file_offset"] <= pos < e["file_offset"] + e["size"]:
                    owner = e
                    break
            owner_str = f"  (in [{owner['index']}] {owner['tag']} @0x{owner['file_offset']:X})" if owner else ""
            print(f"  Found at 0x{pos:08X}{owner_str}")

            if args.floats:
                nearby = find_floats_near(data, pos, window=256)
                if nearby:
                    print(f"    Nearby floats:")
                    for fo, fv in nearby[:20]:
                        rel = fo - pos
                        print(f"      0x{fo:08X} (rel {rel:+d}): {fv:.4f}")

            found += 1
            pos += 1
        print(f"  Total: {found} occurrences")
        return

    # Filter by tag
    if args.tag:
        filtered = [e for e in entries if e["tag"] == args.tag]
        print(f"\n{args.tag} entries ({len(filtered)}):")
        for e in filtered:
            print(f"  [{e['index']:4d}] size=0x{e['size']:08X} ({e['size']:>10,})"
                  f"  flags=0x{e['flags']:02X}  offset=0x{e['file_offset']:08X}")
        return

    # String extraction
    if args.strings:
        target = [e for e in entries if e["tag"] == args.strings]
        print(f"\nStrings in {args.strings} entries ({len(target)} entries):")
        for e in target:
            strings = find_strings(data, e["file_offset"], e["size"])
            if strings:
                print(f"\n  [{e['index']}] offset=0x{e['file_offset']:08X} size={e['size']:,}")
                for addr, s in strings:
                    rel = addr - e["file_offset"]
                    print(f"    0x{addr:08X} (+0x{rel:04X}): {s}")
        return

    # Dump mode
    if args.dump:
        target = [e for e in entries if e["tag"] == args.dump]
        if not target:
            print(f"ERROR: No entries with tag '{args.dump}'")
            sys.exit(1)
        e = target[0]
        blob = data[e["file_offset"]:e["file_offset"] + e["size"]]
        out_path = args.out or f"{path.stem}_{args.dump}_{e['index']}.bin"
        Path(out_path).write_bytes(blob)
        print(f"\nDumped [{e['index']}] {args.dump} ({len(blob):,} bytes) -> {out_path}")
        return

    # Default: show all entries
    if len(entries) <= 100:
        print(f"\nAll TOC entries:")
        for e in entries:
            print(f"  [{e['index']:4d}] {e['tag']}  size=0x{e['size']:08X} ({e['size']:>10,})"
                  f"  flags=0x{e['flags']:02X}  offset=0x{e['file_offset']:08X}")
    else:
        print(f"\n(Too many entries to list all — use --tag <name> to filter)")
        print(f"  First 10:")
        for e in entries[:10]:
            print(f"  [{e['index']:4d}] {e['tag']}  size=0x{e['size']:08X} ({e['size']:>10,})"
                  f"  flags=0x{e['flags']:02X}  offset=0x{e['file_offset']:08X}")
        print(f"  ...")
        print(f"  Last 5:")
        for e in entries[-5:]:
            print(f"  [{e['index']:4d}] {e['tag']}  size=0x{e['size']:08X} ({e['size']:>10,})"
                  f"  flags=0x{e['flags']:02X}  offset=0x{e['file_offset']:08X}")


if __name__ == "__main__":
    main()
