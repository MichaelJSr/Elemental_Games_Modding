#!/usr/bin/env python3
"""
Scan the Azurik XBE .text for instruction-level xrefs to known per-frame
globals (frame counters, field counters, VBlank caches).

Any function that reads or writes one of these globals is a candidate
for frame-count-dependent logic (e.g. "if (frame & 1)" flicker, "if
(last_frame != cur_frame)" cache invalidation, fixed-cadence UI timers).
Such logic is invisible to the float/double constant scanner because it
operates on integer counters and thus ticks 2x as fast at 60fps.

Usage:
    python scan_frame_counters.py [path/to/default.xbe]

Output groups xrefs by target VA.  Mnemonics are decoded for the common
x86 forms (`A1/A3`, `FF /0..7`, `8B/89 modrm disp32`, `83/81 modrm disp32
imm`).  Uncommon encodings are still flagged with the raw opcode bytes.
"""
import struct
import sys
import os

DEFAULT_XBE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "Azurik - Rise of Perathia (USA).xiso", "default.xbe"
)

REG_NAMES = ['EAX', 'ECX', 'EDX', 'EBX', 'ESP', 'EBP', 'ESI', 'EDI']

# Known per-frame globals — each entry is (VA, human label).
# Discovered via Ghidra decompilation of FUN_0008fbe0 (Present wrapper),
# FUN_00058e40 (main loop), and FUN_00058d00 (pump).  Extend freely —
# any DAT_xxxxxxxx that is mutated exactly once per render frame is a
# potential frame-count-leak candidate.
FRAME_COUNTERS = [
    (0x001A9C0C, "DAT_001a9c0c  per-Present frame counter (++ each present)"),
    (0x001AAB5C, "DAT_001aab5c  Present movie-capture field counter"),
    (0x0038DD14, "DAT_0038dd14  last-seen VBlank (manual pacer cache)"),
    (0x001BE36C, "DAT_001be36c  per-step counter (++ each FUN_00058d00 tick)"),
    (0x001BF404, "DAT_001bf404  scheduler budget (decremented per FUN_00058d00)"),
    (0x001BF5D4, "DAT_001bf5d4  texture-reupload counter (bumped in FUN_000916a0)"),
    (0x001BF5D8, "DAT_001bf5d8  texture-bytes counter (bumped in FUN_000916a0)"),
]


def parse_xbe_sections(data):
    base_addr = struct.unpack_from('<I', data, 0x104)[0]
    section_count = struct.unpack_from('<I', data, 0x11C)[0]
    section_headers_addr = struct.unpack_from('<I', data, 0x120)[0]
    section_headers_offset = section_headers_addr - base_addr
    sections = []
    for i in range(section_count):
        off = section_headers_offset + i * 56
        vaddr = struct.unpack_from('<I', data, off + 4)[0]
        vsize = struct.unpack_from('<I', data, off + 8)[0]
        raw_addr = struct.unpack_from('<I', data, off + 12)[0]
        raw_size = struct.unpack_from('<I', data, off + 16)[0]
        name_addr = struct.unpack_from('<I', data, off + 20)[0]
        name_offset = name_addr - base_addr
        name_end = data.index(b'\x00', name_offset)
        name = data[name_offset:name_end].decode('ascii', errors='replace')
        sections.append({
            'name': name, 'vaddr': vaddr, 'vsize': vsize,
            'raw_addr': raw_addr, 'raw_size': raw_size,
        })
    return base_addr, sections


# Decoder for `FF /n abs32`:
_FF_SUBOP = {
    0: ("INC ", "RW"),
    1: ("DEC ", "RW"),
    2: ("CALL ", "R"),
    4: ("JMP ",  "R"),
    6: ("PUSH ", "R"),
}

# Decoder for `83 /n` (8-bit imm op on memory).  /7 is CMP.
_ARITH_SUBOP_83 = {
    0: "ADD", 1: "OR", 2: "ADC", 3: "SBB",
    4: "AND", 5: "SUB", 6: "XOR", 7: "CMP",
}


def decode_ref(sec_data, i, target_va):
    """Return (mnemonic, access_kind, instr_len) for a disp32 reference at
    offset i that points to target_va, or None if bytes don't decode."""
    if i >= len(sec_data):
        return None
    b0 = sec_data[i]

    # A1 disp32       MOV EAX, [disp32]
    if b0 == 0xA1 and i + 5 <= len(sec_data):
        if struct.unpack_from('<I', sec_data, i + 1)[0] == target_va:
            return ("MOV EAX, [abs32]", "R", 5)

    # A3 disp32       MOV [disp32], EAX
    if b0 == 0xA3 and i + 5 <= len(sec_data):
        if struct.unpack_from('<I', sec_data, i + 1)[0] == target_va:
            return ("MOV [abs32], EAX", "W", 5)

    # 8B /r abs32     MOV reg, [abs32]
    if b0 == 0x8B and i + 6 <= len(sec_data):
        modrm = sec_data[i + 1]
        if (modrm & 0xC7) == 0x05:  # mod=00, rm=101
            if struct.unpack_from('<I', sec_data, i + 2)[0] == target_va:
                reg = REG_NAMES[(modrm >> 3) & 7]
                return (f"MOV {reg}, [abs32]", "R", 6)

    # 89 /r abs32     MOV [abs32], reg
    if b0 == 0x89 and i + 6 <= len(sec_data):
        modrm = sec_data[i + 1]
        if (modrm & 0xC7) == 0x05:
            if struct.unpack_from('<I', sec_data, i + 2)[0] == target_va:
                reg = REG_NAMES[(modrm >> 3) & 7]
                return (f"MOV [abs32], {reg}", "W", 6)

    # 03 /r abs32     ADD reg, [abs32]
    if b0 == 0x03 and i + 6 <= len(sec_data):
        modrm = sec_data[i + 1]
        if (modrm & 0xC7) == 0x05:
            if struct.unpack_from('<I', sec_data, i + 2)[0] == target_va:
                reg = REG_NAMES[(modrm >> 3) & 7]
                return (f"ADD {reg}, [abs32]", "R", 6)

    # 3B /r abs32     CMP reg, [abs32]
    if b0 == 0x3B and i + 6 <= len(sec_data):
        modrm = sec_data[i + 1]
        if (modrm & 0xC7) == 0x05:
            if struct.unpack_from('<I', sec_data, i + 2)[0] == target_va:
                reg = REG_NAMES[(modrm >> 3) & 7]
                return (f"CMP {reg}, [abs32]", "R", 6)

    # 39 /r abs32     CMP [abs32], reg
    if b0 == 0x39 and i + 6 <= len(sec_data):
        modrm = sec_data[i + 1]
        if (modrm & 0xC7) == 0x05:
            if struct.unpack_from('<I', sec_data, i + 2)[0] == target_va:
                reg = REG_NAMES[(modrm >> 3) & 7]
                return (f"CMP [abs32], {reg}", "R", 6)

    # FF /n abs32     INC/DEC/CALL/JMP/PUSH [abs32]
    if b0 == 0xFF and i + 6 <= len(sec_data):
        modrm = sec_data[i + 1]
        if (modrm & 0xC7) == 0x05:
            if struct.unpack_from('<I', sec_data, i + 2)[0] == target_va:
                subop = (modrm >> 3) & 7
                m = _FF_SUBOP.get(subop)
                if m is not None:
                    name, kind = m
                    return (f"{name}[abs32]", kind, 6)

    # 83 /n abs32 imm8   CMP/ADD/SUB/... [abs32], imm8
    if b0 == 0x83 and i + 7 <= len(sec_data):
        modrm = sec_data[i + 1]
        if (modrm & 0xC7) == 0x05:
            if struct.unpack_from('<I', sec_data, i + 2)[0] == target_va:
                op = _ARITH_SUBOP_83[(modrm >> 3) & 7]
                imm = sec_data[i + 6]
                return (f"{op} [abs32], 0x{imm:X}", "R" if op == "CMP" else "RW", 7)

    # 81 /n abs32 imm32  CMP/ADD/SUB/... [abs32], imm32
    if b0 == 0x81 and i + 10 <= len(sec_data):
        modrm = sec_data[i + 1]
        if (modrm & 0xC7) == 0x05:
            if struct.unpack_from('<I', sec_data, i + 2)[0] == target_va:
                op = _ARITH_SUBOP_83[(modrm >> 3) & 7]
                imm = struct.unpack_from('<I', sec_data, i + 6)[0]
                return (f"{op} [abs32], 0x{imm:X}", "R" if op == "CMP" else "RW", 10)

    # C7 /0 abs32 imm32  MOV [abs32], imm32
    if b0 == 0xC7 and i + 10 <= len(sec_data):
        modrm = sec_data[i + 1]
        if (modrm & 0xC7) == 0x05 and ((modrm >> 3) & 7) == 0:
            if struct.unpack_from('<I', sec_data, i + 2)[0] == target_va:
                imm = struct.unpack_from('<I', sec_data, i + 6)[0]
                return (f"MOV [abs32], 0x{imm:X}", "W", 10)

    return None


def scan_text(data, section, targets):
    """Scan the .text section for disp32 xrefs to any VA in `targets`."""
    raw_start = section['raw_addr']
    sec_data = data[raw_start:raw_start + section['raw_size']]
    vaddr_start = section['vaddr']
    target_set = set(targets)

    # Build a fast index of every 4-byte little-endian occurrence of a
    # target VA, then step back 1..2 bytes and try to decode.
    hits = {va: [] for va in target_set}
    n = len(sec_data)
    for i in range(n - 4):
        abs32 = struct.unpack_from('<I', sec_data, i)[0]
        if abs32 not in target_set:
            continue
        # Try decoding at i-1 (A1/A3 form) and i-2 (ModR/M form).
        for back in (1, 2):
            if i - back < 0:
                continue
            decoded = decode_ref(sec_data, i - back, abs32)
            if decoded is None:
                continue
            mnemonic, kind, _ilen = decoded
            hits[abs32].append((vaddr_start + i - back, mnemonic, kind))
            break  # first successful decode wins
    return hits


def main():
    xbe_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_XBE_PATH
    xbe_path = os.path.abspath(xbe_path)

    with open(xbe_path, 'rb') as f:
        data = f.read()

    _base_addr, sections = parse_xbe_sections(data)
    text = next((s for s in sections if s['name'] == '.text'), None)
    if not text:
        print("No .text section found!")
        return

    target_vas = [va for va, _ in FRAME_COUNTERS]
    labels = dict(FRAME_COUNTERS)

    print("Scanning .text for xrefs to per-frame globals...")
    print(f"  .text VA=0x{text['vaddr']:X}  size=0x{text['raw_size']:X}")
    print()

    hits = scan_text(data, text, target_vas)

    total = 0
    for va, _ in FRAME_COUNTERS:
        refs = hits.get(va, [])
        print("=" * 70)
        print(f"0x{va:08X}  {labels[va]}")
        print(f"  {len(refs)} xref(s)")
        total += len(refs)
        if refs:
            reads = [r for r in refs if r[2] == "R"]
            writes = [r for r in refs if r[2] == "W"]
            rw = [r for r in refs if r[2] == "RW"]
            if writes:
                print("  Writes:")
                for va_ref, mn, _ in writes:
                    print(f"    0x{va_ref:06X}  {mn}")
            if rw:
                print("  Read-modify-write:")
                for va_ref, mn, _ in rw:
                    print(f"    0x{va_ref:06X}  {mn}")
            if reads:
                print("  Reads:")
                for va_ref, mn, _ in reads:
                    print(f"    0x{va_ref:06X}  {mn}")
        print()

    print(f"Total xrefs: {total}")
    print()
    print("NOTE: Reads of a per-frame counter by code outside the Present")
    print("wrapper / main loop are the interesting ones — they may encode")
    print("frame-cadence logic that ticks 2x at 60fps (flickers, cache")
    print("invalidation based on `last_frame != cur_frame`, etc.).")


if __name__ == '__main__':
    main()
