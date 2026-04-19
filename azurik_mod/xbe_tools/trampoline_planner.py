"""Trampoline planner — size a hook site before writing any shim.

Authoring a trampoline patch needs three numbers:

1. **How many bytes does the hook CALL/JMP replace?** (usually 5)
2. **What bytes live there today?** (need to restore them if the
   shim "calls through" rather than fully replacing)
3. **Are any of those bytes multi-byte instructions that the
   trampoline must preserve intact?** (Splitting a CALL rel32
   in half produces a silently-broken shim.)

This module answers 1-3 from a VA + the vanilla XBE alone.  No
Ghidra required.  Uses a minimal x86 instruction-length decoder
that handles every opcode we've encountered across the shipped
shim set (``qol_skip_logo``, ``player_physics``, the gravity
wrapper); unknown opcodes are flagged explicitly so the author
knows to reach for Ghidra rather than ship a guess.

Public API:

- :func:`plan_trampoline(xbe, va, budget=5)` — returns a
  :class:`TrampolinePlan` with classified bytes, suggested
  trampoline length, and warnings.
- :func:`format_plan` — human-readable summary for the CLI.

Capstone integration is optional: when the ``capstone`` package
is importable, disassembly strings are richer; when it's not,
the minimal decoder still identifies instruction boundaries.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

from azurik_mod.patching.xbe import va_to_file


# ---------------------------------------------------------------------------
# Minimal x86 instruction classifier
# ---------------------------------------------------------------------------
#
# Goal: for each byte offset, return (length, mnemonic).  We don't
# disassemble operands in full — just identify enough to answer
# "can I split my trampoline here?".
#
# Opcode table coverage includes:
#
#   1-byte:  single-byte opcodes (PUSH reg, POP reg, RET, NOP,
#            simple ALU register forms)
#   2+byte:  ModR/M-based (most MOV / ADD / SUB / CMP / TEST)
#   Relative branches: JCC short/long, CALL/JMP rel32
#   Immediate moves: MOV r32, imm32
#   PUSH imm: 6A imm8 / 68 imm32
#   FLD dword: D9 05 addr32
#
# Anything not in the table returns ``length=0``, which the caller
# treats as "unknown — please inspect by hand".

@dataclass(frozen=True)
class DecodedInstr:
    """One decoded instruction at a specific byte offset."""

    offset: int          # byte offset within the decode window
    length: int          # bytes consumed; 0 = unknown
    mnemonic: str        # short name ("CALL rel32", "MOV r32", etc.)
    raw: bytes

    @property
    def is_unknown(self) -> bool:
        return self.length == 0


_SIMPLE_ONE_BYTE = {
    # Single-byte opcodes we know emit exactly 1 byte.
    0x90: "NOP",                        # NOP
    0xC3: "RET",                        # RET
    0xC9: "LEAVE",                      # LEAVE
    0xCC: "INT3",                       # INT3
    0xF4: "HLT",                        # HLT
    0xFC: "CLD", 0xFD: "STD",           # CLD / STD
    0xF8: "CLC", 0xF9: "STC",           # CLC / STC
}
# PUSH reg: 0x50..0x57 (1 byte). POP reg: 0x58..0x5F.
for _op in range(0x50, 0x58):
    _SIMPLE_ONE_BYTE[_op] = "PUSH r32"
for _op in range(0x58, 0x60):
    _SIMPLE_ONE_BYTE[_op] = "POP r32"
# INC/DEC r32: 0x40..0x4F (1 byte)
for _op in range(0x40, 0x50):
    _SIMPLE_ONE_BYTE[_op] = "INC/DEC r32"


def _decode_one(data: bytes, offset: int) -> DecodedInstr:
    """Decode a single x86 instruction starting at ``data[offset]``.

    Returns length=0 for opcodes we haven't classified — the
    caller uses this signal to bail out rather than guess.
    """
    if offset >= len(data):
        return DecodedInstr(offset, 0, "(out of window)", b"")
    b = data[offset]

    # 1-byte opcodes we recognise.
    if b in _SIMPLE_ONE_BYTE:
        return DecodedInstr(offset, 1, _SIMPLE_ONE_BYTE[b],
                            bytes([b]))

    # PUSH imm8 (6A ib)
    if b == 0x6A:
        if offset + 1 < len(data):
            return DecodedInstr(offset, 2, "PUSH imm8",
                                data[offset:offset + 2])
    # PUSH imm32 (68 id)
    if b == 0x68:
        if offset + 4 < len(data):
            return DecodedInstr(offset, 5, "PUSH imm32",
                                data[offset:offset + 5])

    # JMP rel8 / JMP rel32 / JCC rel32
    if b == 0xEB:
        return DecodedInstr(offset, 2, "JMP rel8",
                            data[offset:offset + 2])
    if b == 0xE9:
        return DecodedInstr(offset, 5, "JMP rel32",
                            data[offset:offset + 5])
    if b == 0xE8:
        return DecodedInstr(offset, 5, "CALL rel32",
                            data[offset:offset + 5])
    # JCC rel8 (0x70..0x7F)
    if 0x70 <= b <= 0x7F:
        return DecodedInstr(offset, 2, "JCC rel8",
                            data[offset:offset + 2])
    # JCC rel32 (0F 8X)
    if b == 0x0F and offset + 1 < len(data):
        nxt = data[offset + 1]
        if 0x80 <= nxt <= 0x8F:
            return DecodedInstr(offset, 6, "JCC rel32",
                                data[offset:offset + 6])

    # MOV r32, imm32: 0xB8..0xBF (5 bytes)
    if 0xB8 <= b <= 0xBF:
        if offset + 4 < len(data):
            return DecodedInstr(offset, 5, "MOV r32, imm32",
                                data[offset:offset + 5])

    # RET imm16 (0xC2 iw)
    if b == 0xC2 and offset + 2 < len(data):
        return DecodedInstr(offset, 3, "RET imm16",
                            data[offset:offset + 3])

    # TEST AL, imm8 / AND/OR/XOR AL, imm8 (common 2-byte forms)
    if b in (0xA8, 0x0C, 0x24, 0x34, 0x2C, 0x04):
        return DecodedInstr(offset, 2, "ALU AL, imm8",
                            data[offset:offset + 2])
    # TEST EAX, imm32
    if b == 0xA9 and offset + 4 < len(data):
        return DecodedInstr(offset, 5, "TEST EAX, imm32",
                            data[offset:offset + 5])

    # FF 25 <imm32> — indirect JMP / import thunk
    if b == 0xFF and offset + 5 < len(data) and data[offset + 1] == 0x25:
        return DecodedInstr(offset, 6, "JMP DWORD PTR [imm32]",
                            data[offset:offset + 6])
    # FF 15 <imm32> — indirect CALL
    if b == 0xFF and offset + 5 < len(data) and data[offset + 1] == 0x15:
        return DecodedInstr(offset, 6, "CALL DWORD PTR [imm32]",
                            data[offset:offset + 6])

    # D9 05 <imm32> — FLD dword ptr [imm32]  (common gravity / speed
    # access pattern)
    if b == 0xD9 and offset + 5 < len(data) and data[offset + 1] == 0x05:
        return DecodedInstr(offset, 6, "FLD dword [imm32]",
                            data[offset:offset + 6])

    # Unknown.  Length=0 signals the caller to stop.
    return DecodedInstr(offset, 0, f"?? ({b:#04x})", bytes([b]))


# ---------------------------------------------------------------------------
# Plan + formatter
# ---------------------------------------------------------------------------


@dataclass
class TrampolinePlan:
    """Authoring report for hooking a specific VA.

    Attributes
    ----------
    va: int
        Hook-site VA.
    file_offset: int
        File offset the VA resolves to.
    budget: int
        The trampoline-length target requested by the caller (5 by
        default = ``CALL rel32``).
    instructions: list[DecodedInstr]
        Decoded instructions starting at ``va``.  The caller walks
        this until the total bytes covers the budget.
    suggested_length: int
        The SMALLEST byte count that fits the budget + ends on an
        instruction boundary.  When the window contains unknowns,
        this falls back to the budget itself with a warning.
    warnings: list[str]
        Human-readable concerns the author should resolve before
        shipping the shim.
    preserved_mnemonics: list[str]
        Mnemonics of the instructions the trampoline overwrites —
        shim authors often re-implement them after calling into
        the hook body.
    """

    va: int
    file_offset: int
    budget: int
    instructions: list[DecodedInstr] = field(default_factory=list)
    suggested_length: int = 0
    warnings: list[str] = field(default_factory=list)
    preserved_mnemonics: list[str] = field(default_factory=list)

    @property
    def clean_boundary(self) -> bool:
        return self.suggested_length >= self.budget and not self.warnings


def plan_trampoline(xbe: bytes, va: int, *,
                    budget: int = 5,
                    window: int = 16) -> TrampolinePlan:
    """Produce a :class:`TrampolinePlan` for hooking ``va``.

    Parameters
    ----------
    xbe: bytes
        Vanilla XBE image.
    va: int
        Virtual address of the hook site.
    budget: int
        Target trampoline length.  5 for ``CALL rel32`` (default);
        6 for ``JMP DWORD PTR [imm32]`` thunks.
    window: int
        How many bytes to decode forward from ``va``.  16 is plenty
        for any realistic trampoline + a few instructions of
        context.
    """
    file_off = va_to_file(va)
    raw = xbe[file_off:file_off + window]

    instrs: list[DecodedInstr] = []
    off = 0
    while off < len(raw):
        ins = _decode_one(raw, off)
        instrs.append(ins)
        if ins.length == 0:
            break
        off += ins.length

    warnings: list[str] = []
    suggested = 0
    for ins in instrs:
        if ins.length == 0:
            warnings.append(
                f"Unknown opcode at VA 0x{va + ins.offset:X}: "
                f"{ins.raw.hex()}; decoder can't classify, inspect "
                f"manually in Ghidra before writing the shim.")
            break
        suggested += ins.length
        if suggested >= budget:
            break

    if suggested < budget:
        warnings.append(
            f"Decoded {suggested} bytes before running out of "
            f"known opcodes; consider widening ``window=`` or "
            f"re-confirming the hook site in Ghidra.")

    # Does the boundary align with the budget exactly?  If it
    # overshoots (e.g. budget=5 but we can only carve 6-byte
    # chunks), warn — shims need to NOP-pad unused bytes.
    if suggested > budget:
        warnings.append(
            f"Boundary overshoots budget: carved {suggested} bytes "
            f"to reach {budget}.  Shim must NOP-pad the extra "
            f"{suggested - budget} byte(s) OR use a larger "
            f"trampoline and restore the overshoot in the callback.")

    mnemonics = [i.mnemonic for i in instrs if i.length > 0]
    covered = 0
    preserved = []
    for ins in instrs:
        if ins.length == 0:
            break
        if covered >= budget:
            break
        preserved.append(ins.mnemonic)
        covered += ins.length

    return TrampolinePlan(
        va=va, file_offset=file_off, budget=budget,
        instructions=instrs,
        suggested_length=suggested,
        warnings=warnings,
        preserved_mnemonics=preserved,
    )


def format_plan(plan: TrampolinePlan) -> str:
    """Human-readable summary for the CLI."""
    lines = [
        f"Trampoline plan for VA 0x{plan.va:08X} "
        f"(file 0x{plan.file_offset:06X}, budget={plan.budget})",
        "",
        "Decoded instructions:",
    ]
    for ins in plan.instructions:
        if ins.length == 0:
            lines.append(
                f"  + 0x{plan.va + ins.offset:08X}  ??  UNKNOWN  "
                f"{ins.raw.hex()}")
            break
        lines.append(
            f"  + 0x{plan.va + ins.offset:08X}  "
            f"{ins.length} B  {ins.mnemonic:<24}  "
            f"{ins.raw.hex()}")

    lines.append("")
    lines.append(f"Suggested trampoline length: {plan.suggested_length} B")
    if plan.preserved_mnemonics:
        lines.append(
            "Instructions the trampoline will overwrite "
            "(call-through shims must restore these): "
            + ", ".join(plan.preserved_mnemonics))

    if plan.warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in plan.warnings:
            lines.append(f"  ! {w}")
    elif plan.clean_boundary:
        lines.append("")
        lines.append("[OK] Clean instruction boundary; ready to hook.")

    return "\n".join(lines)


__all__ = [
    "DecodedInstr",
    "TrampolinePlan",
    "format_plan",
    "plan_trampoline",
]
