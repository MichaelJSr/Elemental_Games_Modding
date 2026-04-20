"""flap_at_peak — give 2nd+ wing flaps a guaranteed minimum v0.

## Why this is a shim, not a byte patch

Vanilla Azurik caps subsequent-flap velocity at
``v0 = sqrt(2g * min(peak_z + flap_height - current_z, flap_height))``.
``peak_z`` is latched once at ``player_jump_init`` (VA 0x8915A) to
``z_at_jump + jump_height`` and is NEVER refreshed by
``player_airborne_tick``.  Once the player's ``current_z`` reaches
``peak_z + flap_height`` — the altitude ceiling — ``fVar1 <= 0``
clamps to 0 and every subsequent flap produces ``v0 = 0``.

**This is intentional vanilla design**: each air session has a
fixed altitude budget; flaps recover altitude toward the ceiling
but cannot exceed it.  See ``docs/LEARNINGS.md`` § "Wing-flap v0
cap" for the RE history and why two previous byte patches (v1 NOP
of ``FSUB [EBX+0x5C]`` at 0x89381, v2 ``FLD ST(1)``→``FLD ST(0)``
at 0x8939F) both failed to override the ceiling in-game.

The shim here is a **workaround**, not a bug fix.  It hooks the
final z-velocity write at VA 0x89409 and guarantees that every
2nd+ flap reaches at least ``sqrt(2g * flap_height) * scale``,
bypassing the vanilla ceiling.

## How it works

### Hook point

At VA 0x89409 vanilla executes ``FSTP [ESI+0x2C]`` (3 bytes,
``D9 5E 2C``) — the final per-flap z-velocity store.  Surrounding
context:

.. code-block:: asm

    00089403  D9 44 24 1C         FLD  [ESP+0x1C]       ; reload v0
    00089407  6A 04               PUSH 4
    00089409  D9 5E 2C            FSTP [ESI+0x2C]       ; <-- HOOK
    0008940C  8B 46 20            MOV  EAX, [ESI+0x20]  ; replayed
    0008940F  6A 11               PUSH 0x11
    00089411  50                  PUSH EAX
    00089412  E8 F9 94 FB FF      CALL anim_change

We install a 5-byte ``JMP rel32`` trampoline at 0x89409 with one
trailing ``NOP``, consuming the 6 bytes ``0x89409..0x8940E`` — the
FSTP AND the following MOV.  The shim replays both before jumping
back to ``0x8940F``.

### Shim body (hand-assembled, 43 bytes)

.. code-block:: asm

     0  D9 05 <K_VA>             FLD  [K_VA]                ; ST0 = K
     6  D8 8E 44 01 00 00         FMUL [ESI+0x144]            ; K * fh
    12  D9 FA                     FSQRT                       ; floor = sqrt(K*fh)
    14  D8 D1                     FCOM ST(1)                  ; compare floor vs v0_vanilla
    16  DF E0                     FNSTSW AX
    18  F6 C4 01                  TEST AH, 0x01               ; C0 (ST0 < ST1)
    21  75 07                     JNZ  +7 -> offset 30        ; floor < v0_vanilla branch
    23  D9 5E 2C                  FSTP [ESI+0x2C]             ; write floor
    26  DD D8                     FSTP ST(0)                   ; pop v0_vanilla
    28  EB 05                     JMP  +5 -> offset 35
    30  DD D8                     FSTP ST(0)                   ; pop floor
    32  D9 5E 2C                  FSTP [ESI+0x2C]             ; write v0_vanilla
    35  8B 46 20                  MOV  EAX, [ESI+0x20]         ; replay clobbered MOV
    38  E9 <rel32>                JMP  <back>                  ; resume at 0x8940F

### Injected constants

``K = 2 * 9.8 * scale^2`` is a 4-byte float carved alongside the
shim body.  ``scale`` is the slider value.  The shim's ``FLD
[K_VA]`` takes the 4-byte VA at apply time; a scale change means
re-applying the pack (same carve-and-reference pattern
``wing_flap_count`` uses for its flap-count ints).

## Slider semantics

- ``1.0`` (default) — every 2nd+ flap gets at least
  ``sqrt(2g * flap_height)`` (the vanilla first-flap v0).
  Below-peak flaps that vanilla would have made weaker are now
  at least as strong as the first flap.
- ``2.0`` — every 2nd+ flap gets at least 2x first-flap v0.
- ``0.5`` — every 2nd+ flap gets at least half the first-flap v0;
  may be weaker than vanilla for near-peak flaps, stronger for
  far-below-peak flaps.  Mostly a speedrunner knob.

## Not to be confused with

- ``flap_height_scale`` (tuning the FIRST flap's height via the
  ``FLD [0x001980A8]`` rewrite at 0x893AE).
- ``flap_below_peak_scale`` (tuning the 0.5x halving factor at
  0x893DD via a byte rewrite).  That one tunes a real vanilla
  constant — no shim needed.
"""

from __future__ import annotations

import struct

from azurik_mod.patching.registry import Feature, register_feature
from azurik_mod.patching.shim_builder import (
    HandShimSpec,
    emit_fld_abs32,
    emit_jmp_rel32,
    install_hand_shim,
    whitelist_for_hand_shim,
    with_sentinel,
)
from azurik_mod.patching.spec import ParametricPatch

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Hook site: FSTP [ESI+0x2C] at VA 0x89409.  3 bytes of FSTP +
# 3 bytes of MOV EAX,[ESI+0x20] = 6 bytes we overwrite with a
# 5-byte JMP + 1-byte NOP.
_HOOK_VA = 0x00089409
_HOOK_VANILLA = bytes.fromhex("d95e2c8b4620")
_HOOK_RETURN_VA = 0x0008940F   # after the 6-byte window

# Gravity used by vanilla's v0 = sqrt(2g * fh) formula.
# The shim computes ``K = 2 * g * scale^2`` at apply time.
_VANILLA_GRAVITY = 9.8

# Shim body size — see docstring for the exact instruction layout.
_SHIM_BODY_SIZE = 43

_SPEC = HandShimSpec(
    hook_va=_HOOK_VA,
    hook_vanilla=_HOOK_VANILLA,
    trampoline_mode="jmp",
    hook_pad_nops=1,
    hook_return_va=_HOOK_RETURN_VA,
    body_size=_SHIM_BODY_SIZE,
)


# ---------------------------------------------------------------------------
# ParametricPatch slider (virtual)
# ---------------------------------------------------------------------------

FLAP_AT_PEAK_SHIM_SLIDER = ParametricPatch(
    name="flap_at_peak_scale",
    label="Wing-flap height (2nd+ flaps, bypass vanilla ceiling)",
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
        "Workaround for the vanilla 'no altitude above first-flap "
        "height' ceiling.  Guarantees every 2nd+ flap reaches at "
        "least sqrt(2g*flap_height) * scale.  1.0 = every flap "
        "matches the 1st-flap v0; 2.0 = 2x that.  Installed as a "
        "C-shim trampoline at VA 0x89409 — NOT a byte patch, "
        "because the vanilla peak-z cap is emergent design."
    ),
)


FLAP_AT_PEAK_SITES: list[ParametricPatch] = [FLAP_AT_PEAK_SHIM_SLIDER]


# ---------------------------------------------------------------------------
# Shim assembly
# ---------------------------------------------------------------------------

def _build_shim_body(shim_va: int, k_va: int) -> bytes:
    """Hand-assemble the 43-byte flap_at_peak shim.

    ``shim_va`` is the absolute VA where the shim body starts in
    the patched XBE — used to compute the closing ``JMP`` rel32
    back to 0x8940F.  ``k_va`` is the absolute VA of the injected
    4-byte float (``K = 2g * scale^2``).

    Stack state at shim entry (CALL-less JMP trampoline):
        ST0 = v0_vanilla (vanilla's proposed z-velocity)
        ESI = player entity pointer

    Register side effects:
        EAX — clobbered (replaying ``MOV EAX, [ESI+0x20]`` the
              6-byte trampoline displaced).
    """
    body = b"".join([
        # 0-5: FLD [K_VA] — ST0 = K, ST1 = v0_vanilla
        emit_fld_abs32(k_va),
        # 6-11: FMUL [ESI+0x144] — ST0 *= flap_height
        b"\xD8\x8E\x44\x01\x00\x00",
        # 12-13: FSQRT — ST0 = floor = scale * sqrt(2g*fh)
        b"\xD9\xFA",
        # 14-15: FCOM ST(1) — compare floor with v0_vanilla
        b"\xD8\xD1",
        # 16-17: FNSTSW AX
        b"\xDF\xE0",
        # 18-20: TEST AH, 0x01 — check C0 (ST0 < operand)
        b"\xF6\xC4\x01",
        # 21-22: JNZ +7 → offset 30 (v0_vanilla > floor branch)
        b"\x75\x07",
        # 23-25: FSTP [ESI+0x2C] — write floor
        b"\xD9\x5E\x2C",
        # 26-27: FSTP ST(0) — pop v0_vanilla
        b"\xDD\xD8",
        # 28-29: JMP +5 → offset 35
        b"\xEB\x05",
        # 30-31: FSTP ST(0) — pop floor (v0_vanilla branch)
        b"\xDD\xD8",
        # 32-34: FSTP [ESI+0x2C] — write v0_vanilla
        b"\xD9\x5E\x2C",
        # 35-37: MOV EAX, [ESI+0x20] (replay clobbered MOV)
        b"\x8B\x46\x20",
        # 38-42: JMP <rel32> back to HOOK_RETURN_VA
        emit_jmp_rel32(
            from_origin_after=shim_va + _SHIM_BODY_SIZE,
            to_va=_HOOK_RETURN_VA),
    ])
    assert len(body) == _SHIM_BODY_SIZE
    return body


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply_flap_at_peak(
    xbe_data: bytearray,
    *,
    scale: float = 1.0,
) -> bool:
    """Install the flap_at_peak shim with the given scale factor.

    Returns ``True`` on install (or already-applied).  Returns
    ``False`` only when the hook-site bytes drift from vanilla.

    ``scale == 1.0`` IS meaningful — it guarantees every 2nd+
    flap reaches at least first-flap v0, which vanilla does NOT
    guarantee (ceiling-clamped flaps would be zero).  We always
    install when asked.  ``scale`` is defensively clamped to a
    tiny positive value to keep K (=2g·scale²) strictly positive
    so the shim's FSQRT doesn't return NaN.
    """
    scale = float(scale)
    if scale < 1e-4:
        scale = 1e-4

    # K = 2g * scale^2 — precomputed at apply time, baked into
    # a 4-byte float carved into .text padding with a 4-byte
    # 0xFF sentinel (see with_sentinel docstring).
    k_value = 2.0 * _VANILLA_GRAVITY * (scale * scale)
    result = install_hand_shim(
        xbe_data, _SPEC,
        data_block=with_sentinel(struct.pack("<f", float(k_value))),
        build_body=_build_shim_body,
        label=(
            f"Wing-flap at-peak bypass: scale={scale:.3f}x "
            f"(K=2g*scale^2={k_value:.4f})"),
    )
    if result is not None:
        return True
    # result=None means either already-applied (success) or drift.
    from azurik_mod.patching.xbe import va_to_file
    off = va_to_file(_HOOK_VA)
    b = xbe_data[off:off + 6]
    return len(b) == 6 and b[0] == 0xE9 and b[5] == 0x90


# ---------------------------------------------------------------------------
# Whitelist (for verify-patches --strict)
# ---------------------------------------------------------------------------

def _flap_at_peak_dynamic_whitelist(xbe: bytes) -> list[tuple[int, int]]:
    """Whitelist: trampoline (6 B), shim body (43 B), K+sentinel (8 B)."""
    # The shim's first instruction is FLD [K_VA] at offset 0 —
    # D9 05 <abs32>, so data_abs32_offsets=(0,).
    return whitelist_for_hand_shim(
        xbe, _SPEC,
        data_abs32_offsets=(0,),
        data_abs32_opcode=b"\xD9\x05",
        data_whitelist_size=8,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _custom_apply(
    xbe_data: bytearray,
    flap_at_peak_scale: float | None = None,
    **_extra,
) -> None:
    """Route the ParametricPatch slider value into the XBE apply."""
    if flap_at_peak_scale is None:
        return
    apply_flap_at_peak(xbe_data, scale=float(flap_at_peak_scale))


FEATURE = register_feature(Feature(
    name="flap_at_peak",
    description=(
        "Wing-flap subsequent-flap height — bypasses the vanilla "
        "no-altitude-above-first-flap-height ceiling via a "
        "hand-assembled shim trampoline at VA 0x89409.  "
        "Guarantees every 2nd+ flap reaches at least "
        "sqrt(2g * flap_height) * scale.  The cap this bypasses "
        "is INTENTIONAL vanilla design, not a bug — see "
        "docs/LEARNINGS.md § 'Wing-flap v0 cap'."
    ),
    sites=FLAP_AT_PEAK_SITES,
    apply=lambda xbe_data: None,   # no-op; custom_apply is used
    default_on=False,
    included_in_randomizer_qol=False,
    category="player",
    tags=("cheat", "movement", "air-power", "c-shim"),
    dynamic_whitelist_from_xbe=_flap_at_peak_dynamic_whitelist,
    custom_apply=_custom_apply,
))


__all__ = [
    "FEATURE",
    "FLAP_AT_PEAK_SHIM_SLIDER",
    "FLAP_AT_PEAK_SITES",
    "apply_flap_at_peak",
]
