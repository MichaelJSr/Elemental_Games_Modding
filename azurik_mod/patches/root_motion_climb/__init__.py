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

def _build_shim_body(scale_va: int, shim_va: int) -> bytes:
    """Same as root_motion_roll but without the WHITE/BACK gate.

    Layout (130 bytes):

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

    call_origin = shim_va + len(b"".join(parts))
    call_end = call_origin + 5
    call_rel32 = _FUN_00042E40_VA - call_end
    parts.append(b"\xE8" + struct.pack("<i", call_rel32))   # CALL vanilla

    # Scale block (no gate — always fires).
    parts.append(b"\xD9\x05" + struct.pack("<I", scale_va))   # FLD scale
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
    scale = float(scale)
    if scale <= 0:
        scale = 1e-4

    from azurik_mod.patching.apply import _carve_shim_landing
    from azurik_mod.patching.xbe import va_to_file

    hook_off = va_to_file(_HOOK_VA)
    current = bytes(xbe_data[hook_off:hook_off + 5])
    if current != _HOOK_VANILLA:
        if current[0] == 0xE8:
            print(f"  root_motion_climb (already applied / existing CALL trampoline)")
            return True
        print(f"  WARNING: root_motion_climb — hook site at VA "
              f"0x{_HOOK_VA:X} drifted (got {current.hex()}); skipping.")
        return False

    scale_block = struct.pack("<f", scale) + b"\xFF\xFF\xFF\xFF"
    _, scale_va = _carve_shim_landing(xbe_data, scale_block)

    placeholder = b"\xCC" * _SHIM_BODY_SIZE
    body_off, shim_va = _carve_shim_landing(xbe_data, placeholder)
    body = _build_shim_body(scale_va, shim_va)
    xbe_data[body_off:body_off + _SHIM_BODY_SIZE] = body

    rel32 = shim_va - (_HOOK_VA + 5)
    trampoline = b"\xE8" + struct.pack("<i", rel32)
    xbe_data[hook_off:hook_off + 5] = trampoline

    print(f"  Root-motion climb scale: {scale:.3f}x  "
          f"(shim @ VA 0x{shim_va:X}, +{_SHIM_BODY_SIZE} bytes; "
          f"scale @ VA 0x{scale_va:X})")
    return True


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------

def _root_motion_climb_dynamic_whitelist(
    xbe: bytes,
) -> list[tuple[int, int]]:
    from azurik_mod.patching.xbe import resolve_va_to_file, va_to_file

    try:
        hook_off = va_to_file(_HOOK_VA)
    except Exception:  # noqa: BLE001
        return []

    ranges: list[tuple[int, int]] = [(hook_off, hook_off + 5)]
    if len(xbe) >= hook_off + 5 and xbe[hook_off] == 0xE8:
        rel32 = struct.unpack("<i", xbe[hook_off + 1:hook_off + 5])[0]
        shim_va = _HOOK_VA + 5 + rel32
        shim_off = resolve_va_to_file(xbe, shim_va)
        if shim_off is not None:
            ranges.append((shim_off, shim_off + _SHIM_BODY_SIZE))
            # FLD [scale_va] at offset 31 in this shim (no gate
            # check before it).
            body = xbe[shim_off:shim_off + _SHIM_BODY_SIZE]
            if len(body) >= 37 and body[31:33] == b"\xD9\x05":
                scale_va = struct.unpack("<I", body[33:37])[0]
                off = resolve_va_to_file(xbe, scale_va)
                if off is not None:
                    ranges.append((off, off + 8))
    return ranges


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
