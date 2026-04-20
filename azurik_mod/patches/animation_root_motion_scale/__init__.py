"""animation_root_motion_scale — DEPRECATED (round 11.13).

User testing confirmed the vtable-hook approach produces no
observable in-game movement change at any scale (tested 0.1,
0.5, 2.0, 5.0).  Likely causes:

- The CALL at 0x43066 may not reach every root-motion path.
- Scaling ``param_1[0x6C..0x71]`` before the commit may not
  be what that vtable slot does with them (maybe it reads
  from elsewhere, or the deltas are already applied).
- A sibling anim-apply function (there are several dispatchers
  around 0x42E40) may be the real commit site.

Retained as a historical artifact — the shim itself is
correctly hand-assembled and installs cleanly.  Hidden from
the GUI via ``deprecated=True``.  Anyone wanting to iterate
on root-motion scaling should start from the `_SPEC` below
as a known-good 38-byte shim template.

### Background (pre-deprecation)

## Background

Many player states (walk, roll, climb, jump-forward, swim, slide,
wing-flap) produce per-frame position deltas via animation root
motion rather than direct velocity math.  The per-frame commit
happens inside ``anim_apply_translation`` (FUN_00042E40) at its
very end:

.. code-block:: asm

    00043062  8B 03                MOV EAX, [EBX]      ; vtable ptr
    00043064  8B CB                MOV ECX, EBX
    00043066  FF 90 C0 00 00 00    CALL [EAX+0xC0]     ; <-- commit
    0004306C  POP EDI / ...        RET 0x10

By the time the function returns, the delta stored at
``param_1[0x6C..0x71]`` has been consumed.  Round-8's per-caller
post-CALL shims (``root_motion_roll`` at 0x866D9,
``root_motion_climb`` at 0x883FF) tried to scale the deltas AFTER
the vanilla call — too late, because the vtable commit already
integrated the unscaled deltas into the entity's position.

## This pack

Installs a 6-byte ``CALL rel32 + NOP`` trampoline at VA 0x43066
— the vtable-CALL site itself.  Just before the commit, the
shim scales ``param_1[0x6C..0x71]`` (6 floats: two XYZ triples,
one delta-mode and one absolute-mode) by the user's scale.  The
vtable commit then integrates the scaled deltas — exactly the
desired effect for any root-motion animation.

Because ``anim_apply_translation`` is called from ~15 sites
across the player state ticks (walk, climb, jump, flap,
airborne, swim, slope-slide, plus a handful of NPC / scene
paths), this is a GLOBAL scale — every animation-root-motion
translation gets the same multiplier.  If the user wants
per-state gating (e.g. roll-only), a future revision could add
a caller-set flag that the central shim consults.

## Shim body (hand-assembled, 38 bytes)

.. code-block:: asm

    push edx                             ; 1 — save callee-clobbers
    push esi                             ; 1
    lea  esi, [ebx+0x1B0]                ; 6 — &param_1[0x6C]
                                         ;     (index 0x6C * 4 = 0x1B0)
    mov  edx, 6                          ; 5 — 6 floats to scale
    ; loop body:
    fld  dword [esi]                     ; 2 — load delta[i]
    fmul dword [scale_va]                ; 6 — × user scale
    fstp dword [esi]                     ; 2 — store back
    add  esi, 4                          ; 3
    dec  edx                             ; 1
    jnz  -12                             ; 2 — loop back
    pop  esi                             ; 1
    pop  edx                             ; 1
    call dword [eax+0xC0]                ; 6 — replayed vtable call
    ret                                  ; 1 — return to 0x4306C

Total = 38 bytes.

On entry to the trampoline, ``EAX`` already holds the vtable
pointer (from ``MOV EAX, [EBX]`` at VA 0x43062), and ``ECX`` holds
``EBX = param_1`` (from ``MOV ECX, EBX`` at VA 0x43064).  The
shim preserves those callee-clobber registers via PUSH/POP,
scales the 6 deltas, invokes the vtable call on EAX's behalf,
then returns.
"""

from __future__ import annotations

import struct

from azurik_mod.patching.registry import Feature, register_feature
from azurik_mod.patching.shim_builder import (
    HandShimSpec,
    install_hand_shim,
    whitelist_for_hand_shim,
    with_sentinel,
)
from azurik_mod.patching.spec import ParametricPatch


# Hook: the `CALL dword [EAX+0xC0]` at VA 0x43066 inside
# anim_apply_translation.  6 bytes — replaced with a 5-byte
# `CALL rel32` + 1 NOP trampoline.
_HOOK_VA = 0x00043066
_HOOK_VANILLA = bytes.fromhex("ff90c0000000")      # CALL [EAX+0xC0]
_HOOK_RETURN_VA = _HOOK_VA + len(_HOOK_VANILLA)     # 0x4306C

_SHIM_BODY_SIZE = 38

_SPEC = HandShimSpec(
    hook_va=_HOOK_VA,
    hook_vanilla=_HOOK_VANILLA,
    trampoline_mode="call",
    hook_pad_nops=1,
    body_size=_SHIM_BODY_SIZE,
)


# ---------------------------------------------------------------------------
# Slider
# ---------------------------------------------------------------------------

ANIM_ROOT_MOTION_SCALE_SLIDER = ParametricPatch(
    name="animation_root_motion_scale",
    label="Animation root-motion scale (global)",
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
        "Scales EVERY animation-driven player translation — "
        "rolls, climbs, jumps, flaps, slides, swim strokes "
        "all move at this multiplier.  Hooks the vtable-commit "
        "call at the end of anim_apply_translation (VA 0x43066). "
        "Per-state gating not implemented; this is the 'simplest "
        "thing that might work' approach so we can verify the "
        "vtable hook itself functions."
    ),
)


SITES: list[ParametricPatch] = [ANIM_ROOT_MOTION_SCALE_SLIDER]


# ---------------------------------------------------------------------------
# Shim assembly
# ---------------------------------------------------------------------------

def _build_shim_body(shim_va: int, scale_va: int) -> bytes:
    """Assemble the 38-byte shim body.

    Layout (offsets are cumulative):

      0: 52                          PUSH EDX
      1: 56                          PUSH ESI
      2: 8D B3 B0 01 00 00           LEA ESI, [EBX+0x1B0]   (6 B)
      8: BA 06 00 00 00              MOV EDX, 6             (5 B)
     13: D9 06                       FLD DWORD [ESI]        (2 B)
     15: D8 0D <scale_va>            FMUL DWORD [scale_va]  (6 B)
     21: D9 1E                       FSTP DWORD [ESI]       (2 B)
     23: 83 C6 04                    ADD ESI, 4             (3 B)
     26: 4A                          DEC EDX                (1 B)
     27: 75 F0                       JNZ -16 (back to 13)   (2 B)
     29: 5E                          POP ESI
     30: 5A                          POP EDX
     31: FF 90 C0 00 00 00           CALL [EAX+0xC0]        (6 B)
     37: C3                          RET                    (1 B)

    JNZ's relative offset is from the byte AFTER the JNZ, so
    ``target - (byte-after-JNZ) = 13 - 29 = -16 = 0xF0``.  2-byte
    short JNZ covers the 16-byte loop body comfortably.
    """
    del shim_va  # intra-section addressing only; not needed here
    parts = [
        b"\x52",                                      # PUSH EDX
        b"\x56",                                      # PUSH ESI
        b"\x8D\xB3" + struct.pack("<I", 0x1B0),       # LEA ESI, [EBX+0x1B0]
        b"\xBA" + struct.pack("<I", 6),               # MOV EDX, 6
        b"\xD9\x06",                                  # FLD  DWORD [ESI]
        b"\xD8\x0D" + struct.pack("<I", scale_va),    # FMUL DWORD [scale_va]
        b"\xD9\x1E",                                  # FSTP DWORD [ESI]
        b"\x83\xC6\x04",                              # ADD  ESI, 4
        b"\x4A",                                      # DEC  EDX
        b"\x75\xF0",                                  # JNZ  -0x10 (loop)
        b"\x5E",                                      # POP  ESI
        b"\x5A",                                      # POP  EDX
        b"\xFF\x90\xC0\x00\x00\x00",                  # CALL [EAX+0xC0]
        b"\xC3",                                      # RET
    ]
    body = b"".join(parts)
    assert len(body) == _SHIM_BODY_SIZE, (
        f"anim_root_motion body is {len(body)} B, "
        f"expected {_SHIM_BODY_SIZE}")
    return body


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply_animation_root_motion_scale(
    xbe_data: bytearray,
    *,
    scale: float = 1.0,
) -> bool:
    """Install the animation-root-motion central scaling shim.

    ``scale=1.0`` is a byte-identity no-op at runtime (each delta
    is FMUL-ed by 1.0 before the commit), but the shim still
    installs so a future slider change picks up without needing
    the vanilla hook site.  Returns ``True`` on install, ``None``
    on drift / already-applied.
    """
    scale = float(scale)
    if scale <= 0:
        scale = 1e-4
    result = install_hand_shim(
        xbe_data, _SPEC,
        data_block=with_sentinel(struct.pack("<f", scale)),
        build_body=_build_shim_body,
        label=f"Animation root-motion scale: {scale:.3f}x",
    )
    return result is not None or _is_already_applied(xbe_data)


def _is_already_applied(xbe_data: bytearray) -> bool:
    from azurik_mod.patching.xbe import va_to_file
    off = va_to_file(_HOOK_VA)
    b = xbe_data[off:off + 6]
    return len(b) == 6 and b[0] == 0xE8 and b[5] == 0x90


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------

def _dynamic_whitelist(xbe: bytes) -> list[tuple[int, int]]:
    # Body: PUSHes (2) | LEA (6) | MOV (5) | loop-body — the FMUL
    # at body offset 15 carries the abs32 we want to resolve.
    return whitelist_for_hand_shim(
        xbe, _SPEC,
        data_abs32_offsets=(15,),
        data_abs32_opcode=b"\xD8\x0D",
        data_whitelist_size=8,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _custom_apply(
    xbe_data: bytearray,
    animation_root_motion_scale: float | None = None,
    **_extra,
) -> None:
    if animation_root_motion_scale is None:
        return
    apply_animation_root_motion_scale(
        xbe_data, scale=float(animation_root_motion_scale))


FEATURE = register_feature(Feature(
    name="animation_root_motion_scale",
    description=(
        "[DEPRECATED] vtable-hook at anim_apply_translation's "
        "end (VA 0x43066).  User testing round 11.13 confirmed "
        "no observable movement change at any scale."
    ),
    sites=SITES,
    apply=lambda xbe_data: None,
    default_on=False,
    included_in_randomizer_qol=False,
    category="player",
    tags=("cheat", "movement", "c-shim", "root-motion", "deprecated"),
    dynamic_whitelist_from_xbe=_dynamic_whitelist,
    custom_apply=_custom_apply,
    deprecated=True,
))


__all__ = [
    "ANIM_ROOT_MOTION_SCALE_SLIDER",
    "FEATURE",
    "SITES",
    "apply_animation_root_motion_scale",
]
