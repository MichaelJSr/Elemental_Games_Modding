#!/usr/bin/env python3
"""
Scan the Azurik XBE .text section for frame-rate-dependent instruction
patterns.

Three scans are run:

  1. Integer immediate 30 (0x1E) in CMP/MOV/PUSH — original behaviour,
     hunts for hardcoded VBlank counters, frame caps, and similar.
  2. Integer immediate 60 (0x3C) — flags any *new* 60-constants that
     the 60fps patch has left in place but which should really be dt-
     based (i.e. candidates for additional patching).
  3. x87 FMUL/FADD/FLD against the three canonical FPS .rdata anchors
     (float 1/30 @ 0x1983E8, float 30.0 @ 0x1A2650, double 30.0 @
     0x1A28C8) — enumerates every call site so no subsystem is missed.

Also searches all sections for VBlank/swap-related strings.

Usage:
    python scan_int30_instructions.py [path/to/default.xbe]
"""
import struct
import sys
import os

DEFAULT_XBE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "Azurik - Rise of Perathia (USA).xiso", "default.xbe"
)

# Add the repo root to sys.path so `azurik_mod` resolves without a
# `pip install -e .`.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                          "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    from azurik_mod.patches.fps_unlock import (
        FPS_PATCH_SITES as _FPS_PATCH_SITES,
    )
    ALREADY_PATCHED_VAS = {s.va for s in _FPS_PATCH_SITES}
except ImportError:
    ALREADY_PATCHED_VAS = set()

# Known FPS anchor constants (VAs of the canonical .rdata floats/doubles).
FPS_ANCHORS = {
    0x1983E8: "float 1/30  (main dt)",
    0x1A2650: "float 30.0  (shared velocity/rate)",
    0x1A28C8: "double 30.0 (main-loop rate)",
    0x1A2740: "float 1/30  (anim_blend2)",
    0x1A2750: "double 1/30 (anim scheduler)",
}

REG_NAMES = ['EAX', 'ECX', 'EDX', 'EBX', 'ESP', 'EBP', 'ESI', 'EDI']


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


def _find_imm_instructions(sec_data, vaddr_start, target_imm):
    """Find CMP/MOV/PUSH instructions whose immediate equals target_imm
    (int: 0..0xFFFFFFFF)."""
    results = []
    target_imm8 = target_imm & 0xFF

    for i in range(len(sec_data) - 4):
        va = vaddr_start + i
        b = sec_data[i]

        # CMP r/m32, imm8 (83 /7 ib)
        if b == 0x83 and i + 2 < len(sec_data):
            modrm = sec_data[i + 1]
            reg_field = (modrm >> 3) & 7
            if reg_field == 7 and sec_data[i + 2] == target_imm8 and target_imm < 0x80:
                mod = (modrm >> 6) & 3
                rm = modrm & 7
                if mod == 3:
                    results.append((va, f"CMP {REG_NAMES[rm]}, 0x{target_imm:X} ({target_imm})"))

        # CMP EAX, imm32 (3D imm32)
        if b == 0x3D and i + 4 < len(sec_data):
            imm32 = struct.unpack_from('<I', sec_data, i + 1)[0]
            if imm32 == target_imm:
                results.append((va, f"CMP EAX, 0x{target_imm:X} ({target_imm})"))

        # MOV r32, imm32 (B8+rd imm32)
        if 0xB8 <= b <= 0xBF and i + 4 < len(sec_data):
            imm32 = struct.unpack_from('<I', sec_data, i + 1)[0]
            if imm32 == target_imm:
                reg = REG_NAMES[b - 0xB8]
                results.append((va, f"MOV {reg}, 0x{target_imm:X} ({target_imm})"))

        # PUSH imm8 (6A ib)
        if b == 0x6A and i + 1 < len(sec_data) and sec_data[i + 1] == target_imm8 \
                and target_imm < 0x80:
            results.append((va, f"PUSH 0x{target_imm:X} ({target_imm})"))

        # PUSH imm32 (68 imm32)
        if b == 0x68 and i + 4 < len(sec_data):
            imm32 = struct.unpack_from('<I', sec_data, i + 1)[0]
            if imm32 == target_imm:
                results.append((va, f"PUSH 0x{target_imm:X} ({target_imm})"))

        # MOV [mem], imm32 (C7 /0)
        if b == 0xC7 and i + 2 < len(sec_data):
            modrm = sec_data[i + 1]
            reg_field = (modrm >> 3) & 7
            if reg_field == 0:
                mod = (modrm >> 6) & 3
                rm = modrm & 7
                if mod == 3:
                    instr_len = 2
                elif mod == 0 and rm == 5:
                    instr_len = 6
                elif mod == 0 and rm == 4:
                    instr_len = 3
                elif mod == 1:
                    instr_len = 3 + (1 if rm == 4 else 0)
                elif mod == 2:
                    instr_len = 6 + (1 if rm == 4 else 0)
                else:
                    instr_len = 2
                if i + instr_len + 3 < len(sec_data):
                    imm32 = struct.unpack_from('<I', sec_data, i + instr_len)[0]
                    if imm32 == target_imm:
                        results.append((va, f"MOV [mem], 0x{target_imm:X} ({target_imm})"))

    return results


# x87 FPU-mem instructions of the form: <opcode> <ModR/M> <abs32 disp>
# where ModR/M has mod=00 rm=101 (absolute memory).  Opcode+ModR/M uniquely
# identify the op (FADD/FMUL/FLD/FSTP etc. at m32 or m64).
_FPU_MNEMONICS = {
    # (opcode, reg_field_of_ModRM) -> mnemonic
    (0xD8, 0): "FADD m32",
    (0xD8, 1): "FMUL m32",
    (0xD8, 4): "FSUB m32",
    (0xD8, 6): "FDIV m32",
    (0xD9, 0): "FLD m32",
    (0xD9, 2): "FST m32",
    (0xD9, 3): "FSTP m32",
    (0xDC, 0): "FADD m64",
    (0xDC, 1): "FMUL m64",
    (0xDC, 4): "FSUB m64",
    (0xDC, 5): "FSUBR m64",
    (0xDC, 6): "FDIV m64",
    (0xDD, 0): "FLD m64",
    (0xDD, 2): "FST m64",
    (0xDD, 3): "FSTP m64",
}


def find_fpu_anchor_refs(sec_data, vaddr_start, anchors):
    """Scan for x87 FPU instructions that directly address a VA in
    `anchors` via the mod=00 rm=101 (abs32) addressing form."""
    results = []
    for i in range(len(sec_data) - 6):
        va = vaddr_start + i
        opcode = sec_data[i]
        if opcode not in (0xD8, 0xD9, 0xDC, 0xDD):
            continue
        modrm = sec_data[i + 1]
        mod = (modrm >> 6) & 3
        rm = modrm & 7
        if not (mod == 0 and rm == 5):
            continue  # not abs32
        reg_field = (modrm >> 3) & 7
        mnemonic = _FPU_MNEMONICS.get((opcode, reg_field))
        if mnemonic is None:
            continue
        target_va = struct.unpack_from('<I', sec_data, i + 2)[0]
        if target_va in anchors:
            results.append((va, mnemonic, target_va, anchors[target_va]))
    return results


def find_vblank_strings(data, sections):
    """Search for VBlank/swap-related strings in all sections."""
    results = []
    needles = [b'VBlank', b'vblank', b'VBLANK', b'Swap', b'swap',
               b'Present', b'present', b'D3DDevice_Swap', b'SetFlickerFilter']
    for sec in sections:
        if sec['name'] not in ['.text', '.rdata', '.data']:
            continue
        raw_start = sec['raw_addr']
        sec_data = data[raw_start:raw_start + sec['raw_size']]
        vaddr_start = sec['vaddr']
        for needle in needles:
            idx = 0
            while True:
                pos = sec_data.find(needle, idx)
                if pos == -1:
                    break
                va = vaddr_start + pos
                results.append((va, f"String: {needle.decode()}", sec['name']))
                idx = pos + 1
    return results


def _print_imm_hits(title, results):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)
    print(f"Found {len(results)} instruction(s):")
    for va, desc in results:
        marker = " [patched]" if va in ALREADY_PATCHED_VAS else ""
        print(f"  0x{va:06X}  {desc}{marker}")


def main():
    xbe_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_XBE_PATH
    xbe_path = os.path.abspath(xbe_path)

    with open(xbe_path, 'rb') as f:
        data = f.read()

    base_addr, sections = parse_xbe_sections(data)
    text_section = next((s for s in sections if s['name'] == '.text'), None)
    if not text_section:
        print("No .text section found!")
        return

    raw_start = text_section['raw_addr']
    sec_data = data[raw_start:raw_start + text_section['raw_size']]
    vaddr_start = text_section['vaddr']

    imm30 = _find_imm_instructions(sec_data, vaddr_start, 30)
    _print_imm_hits("Instructions with immediate value 30 (0x1E) in .text", imm30)

    imm60 = _find_imm_instructions(sec_data, vaddr_start, 60)
    _print_imm_hits("Instructions with immediate value 60 (0x3C) in .text", imm60)

    print()
    print("=" * 70)
    print("x87 FMUL/FADD/FLD references to canonical FPS anchors")
    print("=" * 70)
    fpu_hits = find_fpu_anchor_refs(sec_data, vaddr_start, FPS_ANCHORS)
    if not fpu_hits:
        print("  None found — unexpected, fps_unlock patches should leave at least a few.")
    else:
        by_anchor = {}
        for va, mnemonic, target_va, anchor_label in fpu_hits:
            by_anchor.setdefault((target_va, anchor_label), []).append((va, mnemonic))
        for (target_va, anchor_label), hits in sorted(by_anchor.items()):
            print(f"\n  0x{target_va:06X}  {anchor_label}  ({len(hits)} xref(s))")
            for va, mnemonic in hits:
                marker = " [patched]" if va in ALREADY_PATCHED_VAS else ""
                print(f"    0x{va:06X}  {mnemonic}{marker}")

    print(f"\n{'=' * 70}")
    print("VBlank/swap-related strings")
    print("=" * 70)
    vblank_results = find_vblank_strings(data, sections)
    for va, desc, sec_name in vblank_results:
        print(f"  0x{va:06X}  [{sec_name}]  {desc}")
    if not vblank_results:
        print("  None found.")


if __name__ == '__main__':
    main()
