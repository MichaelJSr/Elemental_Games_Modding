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
from azurik_mod.patching.spec import ParametricPatch

# Hook site: FLD [0x003902A0] at VA 0x8A095.  6 bytes.
_HOOK_VA = 0x0008A095
_HOOK_VANILLA = bytes.fromhex("d905a0023900")
_HOOK_RETURN_VA = 0x0008A09B   # the FMUL [EDI+0x4] that follows

# Constant the vanilla FLD reads.
_VANILLA_SLOPE_DAT_VA = 0x003902A0

# Shim body size (3 instructions: FLD, FMUL, JMP).
_SHIM_BODY_SIZE = 17


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
        "Scales the state-4 fast-slide velocity (runtime-computed "
        "multiplier at 0x003902A0).  1.0 = vanilla.  Installed as "
        "a 17-byte shim at VA 0x8A095 — the state-3 slow-slide "
        "constant at 0x1AAB68 stays vanilla."
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
    parts: list[bytes] = []
    # 0-5: FLD [0x003902A0] — replay the vanilla read.
    parts.append(b"\xD9\x05" + struct.pack("<I", _VANILLA_SLOPE_DAT_VA))
    # 6-11: FMUL [scale_va] — multiply ST0 by user scale.
    parts.append(b"\xD8\x0D" + struct.pack("<I", scale_va))
    # 12-16: JMP <rel32> back to HOOK_RETURN_VA.
    jmp_origin_after = shim_va + _SHIM_BODY_SIZE
    rel32 = _HOOK_RETURN_VA - jmp_origin_after
    parts.append(b"\xE9" + struct.pack("<i", rel32))
    body = b"".join(parts)
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

    Returns ``True`` on install.  ``scale=1.0`` is a byte-identity
    operation (``x * 1.0 == x``), but the shim still installs so
    any future slider change takes effect immediately.
    """
    scale = float(scale)
    if scale <= 0:
        scale = 1e-4

    from azurik_mod.patching.apply import _carve_shim_landing
    from azurik_mod.patching.xbe import va_to_file

    hook_off = va_to_file(_HOOK_VA)
    current = bytes(xbe_data[hook_off:hook_off + 6])
    if current != _HOOK_VANILLA:
        if current[0] == 0xE9 and current[5] == 0x90:
            print(f"  slope_slide_speed (already applied)")
            return True
        print(f"  WARNING: slope_slide_speed — hook site at VA "
              f"0x{_HOOK_VA:X} drifted (got {current.hex()}); "
              f"skipping.")
        return False

    # Carve the 4-byte scale float + 4-byte 0xFF sentinel.
    scale_block = struct.pack("<f", scale) + b"\xFF\xFF\xFF\xFF"
    _, scale_va = _carve_shim_landing(xbe_data, scale_block)

    # Carve the shim body.
    placeholder = b"\xCC" * _SHIM_BODY_SIZE
    body_off, shim_va = _carve_shim_landing(xbe_data, placeholder)
    body = _build_shim_body(scale_va, shim_va)
    xbe_data[body_off:body_off + _SHIM_BODY_SIZE] = body

    # Trampoline: 5-byte JMP + 1 NOP covering the 6-byte FLD.
    rel32 = shim_va - (_HOOK_VA + 5)
    trampoline = b"\xE9" + struct.pack("<i", rel32) + b"\x90"
    xbe_data[hook_off:hook_off + 6] = trampoline

    print(f"  Slope-slide (state-4) speed: scale={scale:.3f}x  "
          f"(shim @ VA 0x{shim_va:X}, +{_SHIM_BODY_SIZE} bytes; "
          f"scale @ VA 0x{scale_va:X})")
    return True


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------

def _slope_slide_dynamic_whitelist(xbe: bytes) -> list[tuple[int, int]]:
    from azurik_mod.patching.xbe import resolve_va_to_file, va_to_file

    try:
        hook_off = va_to_file(_HOOK_VA)
    except Exception:  # noqa: BLE001
        return []

    ranges: list[tuple[int, int]] = [(hook_off, hook_off + 6)]
    if len(xbe) >= hook_off + 6:
        tramp = xbe[hook_off:hook_off + 6]
        if tramp[0] == 0xE9 and tramp[5] == 0x90:
            rel32 = struct.unpack("<i", tramp[1:5])[0]
            shim_va = _HOOK_VA + 5 + rel32
            shim_off = resolve_va_to_file(xbe, shim_va)
            if shim_off is not None:
                ranges.append((shim_off, shim_off + _SHIM_BODY_SIZE))
                body = xbe[shim_off:shim_off + _SHIM_BODY_SIZE]
                # FMUL [scale_va] at offset 6: D8 0D <abs32>
                if len(body) >= 12 and body[6:8] == b"\xD8\x0D":
                    scale_va = struct.unpack("<I", body[8:12])[0]
                    off = resolve_va_to_file(xbe, scale_va)
                    if off is not None:
                        ranges.append((off, off + 8))
    return ranges


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
