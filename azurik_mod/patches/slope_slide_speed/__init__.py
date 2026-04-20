"""slope_slide_speed — scale the state-4 fast-slide velocity.

## Why this is a shim, not a byte patch

Vanilla ``player_slope_slide_tick`` (FUN_00089A70) has two sub-paths:

- **State 3 (slow slide)** reads the constant ``[0x001AAB68]``
  (vanilla 2.0) at VA 0x89B76.  Direct 4-byte overwrite works
  there — that was the retired ``slope_slide_speed_scale``
  ParametricPatch in ``player_physics``.
- **State 4 (fast slide)** uses a **dynamically-computed**
  multiplier.  At VA 0x89AC8 the game loads
  ``[0x00389B64]`` (a per-frame dt-derived scalar), multiplies
  by ``[0x001A28B0]`` (500.0), and stores the result to
  ``[0x003902A0]`` for later consumption.  At VA **0x8A095**
  (state-4 path) the stored value is read back via
  ``FLD [0x003902A0]`` before being multiplied by the entity's
  velocity and integrated into position.

User testing in late April 2026 reported ``slope_slide_speed_scale``
had no observable effect — confirmed because steep-descent slides
trip state-4, which the constant overwrite at ``0x1AAB68``
doesn't cover.  This shim patches state-4 directly.

## How it works

Trampoline at **VA 0x8A095** is a 5-byte ``JMP rel32`` + 1 NOP
covering the 6-byte ``FLD [0x003902A0]``.  Shim body (17 bytes):

.. code-block:: asm

     0  D9 05 A0 02 39 00         FLD   [0x003902A0]     ; replay original
     6  D8 0D <scale_va>           FMUL  [scale_va]        ; × user scale
    12  E9 <rel32>                 JMP   <0x8A09B>         ; back

The scale float is carved via ``_carve_shim_landing`` alongside
the shim body.  A scale of 1.0 is byte-identity no-op after
``FMUL`` (``x * 1.0 == x``), so the install always runs.

## Slider semantics

- ``1.0`` (default): vanilla state-4 velocity.
- ``2.0``: fast slides travel 2x the velocity — useful for
  speedrun descents.
- ``0.5``: halved fast-slide velocity.

State-3 (slow slide) is untouched — if it matters, re-introduce
the 0x1AAB68 constant patch separately.
"""

from __future__ import annotations

import struct

from azurik_mod.patching.registry import Feature, register_feature
from azurik_mod.patching.shim_builder import (
    HandShimSpec,
    emit_fld_abs32,
    emit_fmul_abs32,
    emit_jmp_rel32,
    install_hand_shim,
    whitelist_for_hand_shim,
    with_sentinel,
)
from azurik_mod.patching.spec import ParametricPatch

# Hook site: FLD [0x003902A0] at VA 0x8A095.  6 bytes (5-byte JMP
# + 1-byte NOP trampoline cleanly replaces it).
_HOOK_VA = 0x0008A095
_HOOK_VANILLA = bytes.fromhex("d905a0023900")
_HOOK_RETURN_VA = 0x0008A09B   # the FMUL [EDI+0x4] that follows

# Constant the vanilla FLD reads.
_VANILLA_SLOPE_DAT_VA = 0x003902A0

# Shim body size (3 instructions: FLD, FMUL, JMP).
_SHIM_BODY_SIZE = 17

_SPEC = HandShimSpec(
    hook_va=_HOOK_VA,
    hook_vanilla=_HOOK_VANILLA,
    trampoline_mode="jmp",
    hook_pad_nops=1,
    hook_return_va=_HOOK_RETURN_VA,
    body_size=_SHIM_BODY_SIZE,
)


# ---------------------------------------------------------------------------
# Slider
# ---------------------------------------------------------------------------

SLOPE_SLIDE_SHIM_SLIDER = ParametricPatch(
    name="slope_slide_speed_scale",
    label="Slope-slide speed (steep-terrain auto-slide, state 4)",
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
        "Scales state-4 fast-slide velocity (steep-terrain "
        "auto-slide).  1.0 = vanilla.  State-3 slow-slide is "
        "unaffected."
    ),
)


SLOPE_SLIDE_SITES: list[ParametricPatch] = [SLOPE_SLIDE_SHIM_SLIDER]


# ---------------------------------------------------------------------------
# Shim assembly
# ---------------------------------------------------------------------------

def _build_shim_body(scale_va: int, shim_va: int) -> bytes:
    """Assemble the 17-byte slope_slide shim.

    ``scale_va`` is the absolute VA of the injected 4-byte float
    (user ``scale`` value).  ``shim_va`` is the absolute VA the
    shim body lands at — used to compute the closing JMP rel32.
    """
    body = (
        emit_fld_abs32(_VANILLA_SLOPE_DAT_VA)       # 6 B: replay FLD
        + emit_fmul_abs32(scale_va)                 # 6 B: × user scale
        + emit_jmp_rel32(                           # 5 B: back to vanilla
            from_origin_after=shim_va + _SHIM_BODY_SIZE,
            to_va=_HOOK_RETURN_VA)
    )
    assert len(body) == _SHIM_BODY_SIZE
    return body


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply_slope_slide_speed_shim(
    xbe_data: bytearray,
    *,
    scale: float = 1.0,
) -> bool:
    """Install the slope-slide state-4 velocity shim.

    Returns ``True`` on install / already-applied.  ``scale=1.0``
    is a byte-identity op (``x * 1.0 == x``), but the shim still
    installs so any future slider change takes effect immediately.
    """
    scale = float(scale)
    if scale <= 0:
        scale = 1e-4

    result = install_hand_shim(
        xbe_data, _SPEC,
        data_block=with_sentinel(struct.pack("<f", scale)),
        build_body=_build_shim_body,
        label=f"Slope-slide (state-4) speed: scale={scale:.3f}x",
    )
    # None means either "already applied" (which is also success) or
    # "drift" (which is failure).  The helper prints the appropriate
    # diagnostic; we just translate to bool.
    return result is not None or _is_already_applied(xbe_data)


def _is_already_applied(xbe_data: bytearray) -> bool:
    """Re-check the hook: ``install_hand_shim`` returns ``None``
    both on already-applied AND on drift.  To distinguish, sniff
    the hook bytes: valid trampoline shape = already applied."""
    from azurik_mod.patching.xbe import va_to_file
    off = va_to_file(_HOOK_VA)
    b = xbe_data[off:off + 6]
    return len(b) == 6 and b[0] == 0xE9 and b[5] == 0x90


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------

def _slope_slide_dynamic_whitelist(xbe: bytes) -> list[tuple[int, int]]:
    # Body layout: FLD [const] @ 0 ; FMUL [scale_va] @ 6 ; JMP @ 12.
    # Offset 6 uses opcode D8 0D (FMUL m32fp) — that's the scale ref.
    return whitelist_for_hand_shim(
        xbe, _SPEC,
        data_abs32_offsets=(6,),
        data_abs32_opcode=b"\xD8\x0D",
        data_whitelist_size=8,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _custom_apply(
    xbe_data: bytearray,
    slope_slide_speed_scale: float | None = None,
    **_extra,
) -> None:
    if slope_slide_speed_scale is None:
        return
    apply_slope_slide_speed_shim(
        xbe_data, scale=float(slope_slide_speed_scale))


FEATURE = register_feature(Feature(
    name="slope_slide_speed",
    description=(
        "Scales the state-4 (fast) slope-slide velocity via a "
        "17-byte shim at VA 0x8A095.  The state-3 (slow) slide "
        "constant at 0x1AAB68 is untouched."
    ),
    sites=SLOPE_SLIDE_SITES,
    apply=lambda xbe_data: None,
    default_on=False,
    included_in_randomizer_qol=False,
    category="player",
    tags=("cheat", "movement", "c-shim"),
    dynamic_whitelist_from_xbe=_slope_slide_dynamic_whitelist,
    custom_apply=_custom_apply,
))


__all__ = [
    "FEATURE",
    "SLOPE_SLIDE_SHIM_SLIDER",
    "SLOPE_SLIDE_SITES",
    "apply_slope_slide_speed_shim",
]
