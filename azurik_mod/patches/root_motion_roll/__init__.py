"""root_motion_roll — scale player animation root-motion while rolling.

## Background

Azurik's WHITE / BACK roll plays the ``characters/garret4/
roll_forward`` animation, which drives entity position via
**animation root motion**: a per-frame translation delta baked
into the animation asset, sampled by ``FUN_00042E40`` and
applied to the entity.  The shared `magnitude` field
(``entity+0x124``) that vanilla's 3x FMUL at VA 0x849E4 scales
is NOT consumed by the root-motion pipeline, which is why the
retired ``roll_speed_scale`` byte patch had no in-game effect.

See ``docs/LEARNINGS.md`` § "Roll speed is animation-root-motion"
for the RE history.

## How this shim works

Trampoline at the 5-byte CALL to ``FUN_00042E40`` from
``player_walk_state`` (VA **0x000866D9**).  Stack state at
shim entry (after the trampoline CALL's return-address push):

.. code-block:: text

    [ESP+0]  = return-to-0x866DE (trampoline push)
    [ESP+4]  = param_1 (int * — anim-object scratch)
    [ESP+8]  = param_2 (float 0.0)
    [ESP+12] = param_3 (byte *)
    [ESP+16] = param_4 (float * — reference position)
    ECX      = this pointer (``[EBP+0x7C]``)
    EBP      = PlayerInputState pointer
    ESI      = player entity

The shim:

1. Saves callee-saved regs (EDI, EBX).
2. Stashes ``param_1`` in EBX for post-processing.
3. Re-pushes the 4 args (duplicated from current stack).
4. Calls vanilla ``FUN_00042E40`` (which cleans the 16 bytes of
   duped args on return, __thiscall).
5. Checks ``PlayerInputState.flags & 0x40`` (WHITE/BACK bit).
   If clear, skip scaling — normal walking is untouched.
6. If set (roll active), scales the translation deltas written
   by vanilla into ``param_1[0x6C..0x71]`` (6 floats — both the
   delta-mode and absolute-mode output slots).
7. Pops callee-saveds and returns with ``RET 0x10`` (cleans the
   original 16 bytes of args as ``FUN_00042E40``'s __thiscall
   contract demands).

## Slider semantics

- ``1.0`` (default): vanilla root-motion translation.
- ``2.0``: roll covers 2x distance per frame (feels "faster").
- ``0.5``: half-speed roll.

Only the roll state (WHITE/BACK held → flags & 0x40 set) is
scaled; normal walking, idle, and other ground animations stay
at vanilla scale.
"""

from __future__ import annotations

import struct

from azurik_mod.patching.registry import Feature, register_feature
from azurik_mod.patching.shim_builder import (
    HandShimSpec,
    emit_call_rel32,
    emit_fld_abs32,
    install_hand_shim,
    whitelist_for_hand_shim,
    with_sentinel,
)
from azurik_mod.patching.spec import ParametricPatch

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 5-byte CALL to FUN_00042E40 at VA 0x866D9 inside player_walk_state.
_HOOK_VA = 0x000866D9
_HOOK_VANILLA = bytes.fromhex("e862c7fbff")   # E8 62 C7 FB FF
_HOOK_RETURN_VA = 0x000866DE

# The vanilla function we call through.
_FUN_00042E40_VA = 0x00042E40

# WHITE/BACK roll flag bit in PlayerInputState.flags (+0x20).
_ROLL_FLAG_MASK = 0x40

# Shim body size (computed precisely in _build_shim_body).
_SHIM_BODY_SIZE = 134

_SPEC = HandShimSpec(
    hook_va=_HOOK_VA,
    hook_vanilla=_HOOK_VANILLA,
    trampoline_mode="call",
    hook_pad_nops=0,
    hook_return_va=_HOOK_RETURN_VA,
    body_size=_SHIM_BODY_SIZE,
)


# ---------------------------------------------------------------------------
# Slider
# ---------------------------------------------------------------------------

ROLL_SPEED_SHIM_SLIDER = ParametricPatch(
    name="roll_speed_scale",
    label="Roll speed (WHITE/BACK — root-motion shim)",
    va=0,
    size=0,
    original=b"",
    default=1.0,
    slider_min=0.1,
    slider_max=10.0,
    slider_step=0.05,
    unit="x",
    encode=lambda v: struct.pack("<d", float(v)),
    decode=lambda b: struct.unpack("<d", b)[0],
    description=(
        "Scales roll distance per frame.  Rolls are animation-"
        "root-motion driven; this shim post-scales the "
        "translation deltas only while the WHITE/BACK roll "
        "flag is held."
    ),
)

ROLL_SPEED_SITES: list[ParametricPatch] = [ROLL_SPEED_SHIM_SLIDER]


# ---------------------------------------------------------------------------
# Shim assembly
# ---------------------------------------------------------------------------

def _build_shim_body(shim_va: int, scale_va: int) -> bytes:
    """Hand-assemble the root_motion_roll shim body.

    The shim wraps ``FUN_00042E40``: re-push the 4 stack args,
    call vanilla, then conditionally scale the per-frame
    translation deltas that vanilla wrote into ``param_1``.

    Layout (offsets relative to ``shim_va``):

    .. code-block:: text

         0  57                        PUSH EDI
         1  53                        PUSH EBX
         2  8B 5C 24 0C               MOV  EBX, [ESP+12]   ; EBX = param_1
         6  8B F9                     MOV  EDI, ECX        ; save ECX/this
         8  FF 74 24 18               PUSH [ESP+24]        ; param_4
        12  FF 74 24 18               PUSH [ESP+24]        ; param_3
        16  FF 74 24 18               PUSH [ESP+24]        ; param_2
        20  FF 74 24 18               PUSH [ESP+24]        ; param_1
        24  8B CF                     MOV  ECX, EDI        ; restore this
        26  E8 <rel32>                 CALL FUN_00042E40    ; vanilla
        31  F6 45 20 40                TEST byte [EBP+0x20], 0x40
        35  74 <off_done>              JZ   done             ; skip scale
        37  D9 05 <scale_va>           FLD  [scale_va]       ; ST0 = scale
        43  D9 C0                      FLD  ST(0)            ; dup
        45  D8 8B <0x1B0 offset>       FMUL [EBX + 0x1B0]    ; × [0x6C]
        51  D9 9B <0x1B0 offset>       FSTP [EBX + 0x1B0]
        57  D9 C0                      FLD  ST(0)
        59  D8 8B <0x1B4 offset>       FMUL [EBX + 0x1B4]    ; × [0x6D]
        65  D9 9B <0x1B4 offset>       FSTP [EBX + 0x1B4]
        71  D9 C0                      FLD  ST(0)
        73  D8 8B <0x1B8 offset>       FMUL [EBX + 0x1B8]
        79  D9 9B <0x1B8 offset>       FSTP [EBX + 0x1B8]
        85  D9 C0                      FLD  ST(0)
        87  D8 8B <0x1BC offset>       FMUL [EBX + 0x1BC]
        93  D9 9B <0x1BC offset>       FSTP [EBX + 0x1BC]
        99  D9 C0                      FLD  ST(0)
       101  D8 8B <0x1C0 offset>       FMUL [EBX + 0x1C0]
       107  D9 9B <0x1C0 offset>       FSTP [EBX + 0x1C0]
       113  D9 C0                      FLD  ST(0)
       115  D8 8B <0x1C4 offset>       FMUL [EBX + 0x1C4]
       121  D9 9B <0x1C4 offset>       FSTP [EBX + 0x1C4]
       127  DD D8                      FSTP ST(0)           ; discard scale
     done:
       129  5B                         POP EBX
       130  5F                         POP EDI
       131  C2 10 00                   RET 0x10

    Total: 134 bytes.  Trampoline at VA 0x866D9 is a 5-byte CALL
    (consumes the full 5 bytes of vanilla CALL — no padding needed).
    """
    parts: list[bytes] = []

    # Prologue: save callee-saveds + snapshot param_1 / this.
    parts.append(b"\x57")                         # PUSH EDI  (1)
    parts.append(b"\x53")                         # PUSH EBX  (1)
    parts.append(b"\x8B\x5C\x24\x0C")             # MOV EBX, [ESP+0xC]  (4) — param_1
    parts.append(b"\x8B\xF9")                     # MOV EDI, ECX  (2) — save this

    # Duplicate 4 args onto stack for the vanilla __thiscall call.
    # Each push reads [ESP+0x18] because the PUSH itself shifts ESP
    # by 4, so the original slot shifts from +0x14 → +0x18 → +0x1C …
    # but since we're pushing and re-reading at the SAME offset each
    # time, the shifted-slot math works out — param_4 ends up on
    # top, followed by 3, 2, 1.
    for _ in range(4):
        parts.append(b"\xFF\x74\x24\x18")         # PUSH [ESP+0x18]  (4)

    parts.append(b"\x8B\xCF")                     # MOV ECX, EDI  (2) — restore this

    # CALL vanilla FUN_00042E40.  rel32 relative to end-of-CALL.
    call_origin_after = shim_va + len(b"".join(parts)) + 5
    parts.append(emit_call_rel32(call_origin_after, _FUN_00042E40_VA))

    # Gate check: PlayerInputState.flags & 0x40 (EBP+0x20).
    parts.append(b"\xF6\x45\x20\x40")             # TEST [EBP+0x20], 0x40  (4)

    # Compute the JZ displacement.  Everything from here to the
    # "done" label is the "active roll" branch.
    # Scaling block: 1× FLD scale + 6× (FLD ST(0); FMUL [ebx+off];
    # FSTP [ebx+off]) + 1× FSTP ST(0).
    # Each field: 2 + 6 + 6 = 14 bytes; 6 fields = 84 bytes.
    # Header FLD scale = 6 bytes.  Trailer FSTP ST(0) = 2 bytes.
    # Total scaling-block size = 6 + 84 + 2 = 92 bytes.
    scale_block_size = 92
    parts.append(b"\x74" + bytes([scale_block_size]))  # JZ +92  (2)

    # FLD scale — ST0 = scale.
    parts.append(emit_fld_abs32(scale_va))   # 6

    # 6 scaled slots on param_1: offsets 0x1B0 .. 0x1C4 (stride 4).
    for disp in (0x1B0, 0x1B4, 0x1B8, 0x1BC, 0x1C0, 0x1C4):
        # FLD ST(0) — duplicate scale.
        parts.append(b"\xD9\xC0")                 # 2
        # FMUL [EBX+disp32]
        parts.append(b"\xD8\x8B" + struct.pack("<I", disp))  # 6
        # FSTP [EBX+disp32]
        parts.append(b"\xD9\x9B" + struct.pack("<I", disp))  # 6

    # Discard the original scale copy.
    parts.append(b"\xDD\xD8")                     # FSTP ST(0)  (2)

    # Epilogue (label "done").
    parts.append(b"\x5B")                         # POP EBX  (1)
    parts.append(b"\x5F")                         # POP EDI  (1)
    parts.append(b"\xC2\x10\x00")                 # RET 0x10  (3)

    body = b"".join(parts)
    assert len(body) == _SHIM_BODY_SIZE, (
        f"expected {_SHIM_BODY_SIZE}-byte shim, got {len(body)}")
    return body


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply_root_motion_roll(
    xbe_data: bytearray,
    *,
    scale: float = 1.0,
) -> bool:
    """Install the root-motion-roll shim.

    Returns ``True`` on install / already-applied.  Scale 1.0 is
    technically byte-identical to vanilla (x * 1.0 == x) but we
    install unconditionally so slider changes take effect on
    rebuild.
    """
    scale = float(scale)
    if scale <= 0:
        scale = 1e-4

    result = install_hand_shim(
        xbe_data, _SPEC,
        data_block=with_sentinel(struct.pack("<f", scale)),
        build_body=_build_shim_body,
        label=f"Root-motion roll scale: {scale:.3f}x",
    )
    if result is not None:
        return True
    # Already applied? Detect our own CALL trampoline.
    from azurik_mod.patching.xbe import va_to_file
    off = va_to_file(_HOOK_VA)
    return xbe_data[off] == 0xE8 and (
        bytes(xbe_data[off:off + 5]) != _HOOK_VANILLA)


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------

def _root_motion_roll_dynamic_whitelist(
    xbe: bytes,
) -> list[tuple[int, int]]:
    """FLD [scale_va] lives at shim offset 37 (after prologue +
    arg-duplication + CALL vanilla + TEST-and-JZ gate)."""
    return whitelist_for_hand_shim(
        xbe, _SPEC,
        data_abs32_offsets=(37,),
        data_abs32_opcode=b"\xD9\x05",
        data_whitelist_size=8,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _custom_apply(
    xbe_data: bytearray,
    roll_speed_scale: float | None = None,
    **_extra,
) -> None:
    if roll_speed_scale is None:
        return
    apply_root_motion_roll(xbe_data, scale=float(roll_speed_scale))


FEATURE = register_feature(Feature(
    name="root_motion_roll",
    description=(
        "Scales WHITE/BACK-triggered roll distance.  Rolls are "
        "animation-root-motion driven; vanilla's 3x FMUL on "
        "`magnitude` doesn't reach them.  Walk stays vanilla."
    ),
    sites=ROLL_SPEED_SITES,
    apply=lambda xbe_data: None,
    # Deprecated as of round 11.10: user-verified that the shim
    # installs bytes cleanly but produces no observable in-game
    # effect even with the round-11.6 wiring bug fixed.  Probable
    # cause: ``anim_apply_translation`` commits the translation
    # deltas via the vtable+0xC0 call INSIDE the function, before
    # our post-CALL shim gets a chance to scale them.  A correct
    # shim would need to hook the vtable call itself (deeper RE).
    # Hidden from the GUI pack browser; CLI / direct apply_pack
    # still work for anyone wanting to experiment.
    deprecated=True,
    default_on=False,
    included_in_randomizer_qol=False,
    category="player",
    tags=("cheat", "movement", "c-shim", "root-motion"),
    dynamic_whitelist_from_xbe=_root_motion_roll_dynamic_whitelist,
    custom_apply=_custom_apply,
))


__all__ = [
    "FEATURE",
    "ROLL_SPEED_SHIM_SLIDER",
    "ROLL_SPEED_SITES",
    "apply_root_motion_roll",
]
