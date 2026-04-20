"""root_motion_climb — scale player animation root-motion while climbing.

## Background

Azurik's climbing state (state 1) drives entity position via
animation root motion just like rolling — the 2.0 constant at
VA 0x001980E4 that the retired ``climb_speed_scale`` byte patch
targeted is only a physics-side scalar consumed in
``player_climb_tick``, but the actual per-frame translation comes
from ``FUN_00042E40``'s anim-apply pipeline.

See ``docs/LEARNINGS.md`` § "Retired physics patches" for context.

## How this shim works

Trampoline at the 5-byte CALL to ``FUN_00042E40`` from
``player_climb_tick`` (VA **0x000883FF**).  The shim mirrors
``root_motion_roll`` exactly but **does not gate** on any input
flag — the entire ``player_climb_tick`` function is climb-state,
so scaling applies unconditionally.

Stack layout and scaling behaviour are identical to
``root_motion_roll`` (see that module for the detailed diagram).

## Slider semantics

- ``1.0`` (default): vanilla climb speed.
- ``2.0``: climb 2x faster.
- ``0.5``: half-speed climb.

Only fires while the player is in state-1 climbing (ropes /
ledges that Azurik's code classifies as climb).  Grab / ledge-
pull handled by other state functions is untouched.
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

# 5-byte CALL to FUN_00042E40 at VA 0x883FF inside player_climb_tick.
_HOOK_VA = 0x000883FF
_HOOK_VANILLA = bytes.fromhex("e83caafbff")   # E8 3C AA FB FF
_HOOK_RETURN_VA = 0x00088404

_FUN_00042E40_VA = 0x00042E40

_SHIM_BODY_SIZE = 128   # no gate check → smaller than roll shim

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

CLIMB_SPEED_SHIM_SLIDER = ParametricPatch(
    name="climb_speed_scale",
    label="Climbing speed (root-motion shim)",
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
        "Scales the player's per-frame climbing translation.  "
        "Climb motion is animation-root-motion driven — the "
        "retired byte-patch of the 2.0 constant at 0x1980E4 "
        "didn't cover it.  This shim intercepts the anim-apply "
        "CALL at VA 0x883FF inside player_climb_tick."
    ),
)

CLIMB_SPEED_SITES: list[ParametricPatch] = [CLIMB_SPEED_SHIM_SLIDER]


# ---------------------------------------------------------------------------
# Shim assembly
# ---------------------------------------------------------------------------

def _build_shim_body(shim_va: int, scale_va: int) -> bytes:
    """Same as root_motion_roll but without the WHITE/BACK gate.

    Layout (128 bytes):

    .. code-block:: text

         0  57                        PUSH EDI
         1  53                        PUSH EBX
         2  8B 5C 24 0C               MOV  EBX, [ESP+12]     ; param_1
         6  8B F9                     MOV  EDI, ECX          ; save this
         8  FF 74 24 18               PUSH [ESP+0x18]         ; × 4
        12  FF 74 24 18               (param_4, 3, 2, 1)
        16  FF 74 24 18
        20  FF 74 24 18
        24  8B CF                     MOV  ECX, EDI           ; restore this
        26  E8 <rel32>                 CALL FUN_00042E40
        31  (scaling block, 92 bytes identical to roll)
         ; scaling always fires — no gate.
       123  DD D8                     FSTP ST(0)              ; discard scale
       125  5B                         POP EBX
       126  5F                         POP EDI
       127  C2 10 00                   RET 0x10
    """
    parts: list[bytes] = []

    parts.append(b"\x57")                         # PUSH EDI
    parts.append(b"\x53")                         # PUSH EBX
    parts.append(b"\x8B\x5C\x24\x0C")             # MOV EBX, [ESP+12]
    parts.append(b"\x8B\xF9")                     # MOV EDI, ECX

    for _ in range(4):
        parts.append(b"\xFF\x74\x24\x18")         # PUSH [ESP+0x18]

    parts.append(b"\x8B\xCF")                     # MOV ECX, EDI

    call_origin_after = shim_va + len(b"".join(parts)) + 5
    parts.append(emit_call_rel32(call_origin_after, _FUN_00042E40_VA))

    # Scale block (no gate — always fires).
    parts.append(emit_fld_abs32(scale_va))   # FLD scale
    for disp in (0x1B0, 0x1B4, 0x1B8, 0x1BC, 0x1C0, 0x1C4):
        parts.append(b"\xD9\xC0")                 # FLD ST(0)
        parts.append(b"\xD8\x8B" + struct.pack("<I", disp))  # FMUL [EBX+off]
        parts.append(b"\xD9\x9B" + struct.pack("<I", disp))  # FSTP [EBX+off]
    parts.append(b"\xDD\xD8")                     # FSTP ST(0)

    parts.append(b"\x5B")                         # POP EBX
    parts.append(b"\x5F")                         # POP EDI
    parts.append(b"\xC2\x10\x00")                 # RET 0x10

    body = b"".join(parts)
    assert len(body) == _SHIM_BODY_SIZE, (
        f"expected {_SHIM_BODY_SIZE}-byte shim, got {len(body)}")
    return body


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply_root_motion_climb(
    xbe_data: bytearray,
    *,
    scale: float = 1.0,
) -> bool:
    """Install the root-motion-climb shim.  Returns ``True`` on
    install / already-applied, ``False`` on drift."""
    scale = float(scale)
    if scale <= 0:
        scale = 1e-4

    result = install_hand_shim(
        xbe_data, _SPEC,
        data_block=with_sentinel(struct.pack("<f", scale)),
        build_body=_build_shim_body,
        label=f"Root-motion climb scale: {scale:.3f}x",
    )
    if result is not None:
        return True
    from azurik_mod.patching.xbe import va_to_file
    off = va_to_file(_HOOK_VA)
    return xbe_data[off] == 0xE8 and (
        bytes(xbe_data[off:off + 5]) != _HOOK_VANILLA)


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------

def _root_motion_climb_dynamic_whitelist(
    xbe: bytes,
) -> list[tuple[int, int]]:
    """FLD [scale_va] at offset 31 (no gate check before it)."""
    return whitelist_for_hand_shim(
        xbe, _SPEC,
        data_abs32_offsets=(31,),
        data_abs32_opcode=b"\xD9\x05",
        data_whitelist_size=8,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _custom_apply(
    xbe_data: bytearray,
    climb_speed_scale: float | None = None,
    **_extra,
) -> None:
    if climb_speed_scale is None:
        return
    apply_root_motion_climb(xbe_data, scale=float(climb_speed_scale))


FEATURE = register_feature(Feature(
    name="root_motion_climb",
    description=(
        "Scales the player's per-frame climbing translation.  "
        "Shim at VA 0x883FF inside player_climb_tick intercepts "
        "the anim-apply CALL and post-scales translation deltas "
        "on param_1 (offsets 0x6C..0x71).  Unconditional — "
        "climbing is always state 1."
    ),
    sites=CLIMB_SPEED_SITES,
    apply=lambda xbe_data: None,
    default_on=False,
    included_in_randomizer_qol=False,
    category="player",
    tags=("cheat", "movement", "c-shim", "root-motion"),
    dynamic_whitelist_from_xbe=_root_motion_climb_dynamic_whitelist,
    custom_apply=_custom_apply,
))


__all__ = [
    "FEATURE",
    "CLIMB_SPEED_SHIM_SLIDER",
    "CLIMB_SPEED_SITES",
    "apply_root_motion_climb",
]
