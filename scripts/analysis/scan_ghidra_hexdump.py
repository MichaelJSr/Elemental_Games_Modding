#!/usr/bin/env python3
"""
Parse Ghidra hex dump output and find timing constant patterns.

This tool processes hex dump text files (e.g. from Ghidra MCP memory_read)
and cross-references found constants against the known patched addresses
from the 60 FPS unlock.

Usage:
    python scan_ghidra_hexdump.py <hex_dump_file> <base_va_hex>

Example:
    python scan_ghidra_hexdump.py hex_dumps/hex_198A80.txt 0x198A80
"""
import sys

# All VAs known to be patched — keep in sync with patches/fps_unlock.py
PATCHED_1_30 = {
    0x1981C8, 0x198628, 0x198688, 0x1980A0, 0x198410, 0x198560, 0x1981E0,
    0x19873C, 0x198120, 0x1981F0, 0x198228, 0x1985D0, 0x198700, 0x198758,
    0x198788, 0x198AB0, 0x1A2740, 0x1981B8, 0x198968, 0x1989A8, 0x198C98,
    0x198660, 0x198580, 0x1983E8, 0x198138, 0x1985B8, 0x1986E8, 0x1989C8,
    0x198A38,
}
PATCHED_30_0 = {0x198A74, 0x198B7C, 0x1A2650}
PATCHED_DOUBLE_1_30 = {0x1A2750}
PATCHED_DOUBLE_30_0 = {0x1A28C8}


def parse_hex_dump_lines(lines):
    """Parse hex dump lines (space-separated hex bytes) into raw bytes."""
    raw = bytearray()
    for line in lines:
        line = line.strip()
        if not line or line.startswith('Memory') or line.startswith('Error'):
            continue
        hex_part = line[:48]
        for token in hex_part.split():
            if len(token) == 2:
                try:
                    raw.append(int(token, 16))
                except ValueError:
                    pass
    return raw


def find_all(data, pattern, base_addr):
    results = []
    pos = 0
    while True:
        idx = data.find(pattern, pos)
        if idx == -1:
            break
        results.append(base_addr + idx)
        pos = idx + 1
    return results


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <hex_dump_file> <base_va_hex>")
        sys.exit(1)

    with open(sys.argv[1], 'r') as f:
        lines = f.readlines()

    base_addr = int(sys.argv[2], 16)
    data = parse_hex_dump_lines(lines)
    print(f"Parsed {len(data)} bytes starting at VA 0x{base_addr:06X}")

    searches = [
        ("Float 1/30 (0x3D088889)", bytes([0x89, 0x88, 0x08, 0x3D]), PATCHED_1_30),
        ("Float 30.0 (0x41F00000)", bytes([0x00, 0x00, 0xF0, 0x41]), PATCHED_30_0),
        ("Double 1/30", bytes([0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0xA1, 0x3F]), PATCHED_DOUBLE_1_30),
        ("Double 30.0", bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x3E, 0x40]), PATCHED_DOUBLE_30_0),
    ]

    for desc, pattern, patched_set in searches:
        hits = find_all(data, pattern, base_addr)
        if not hits:
            continue
        print(f"\n=== {desc} ===")
        for addr in hits:
            status = "PATCHED" if addr in patched_set else "*** NEW ***"
            print(f"  0x{addr:06X}  {status}")


if __name__ == '__main__':
    main()
