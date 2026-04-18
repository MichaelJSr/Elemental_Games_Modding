"""Player physics patch pack — gravity + player walk / run speed.

- **Gravity** (``.rdata`` float at VA 0x1980A8, baseline 9.8 m/s²).
  The engine integrates gravity in ``FUN_00085700`` via
  ``v_z' = v_z - g*dt``, reading this single global — so overwriting
  it scales world gravity for everything that falls (player, enemies,
  projectiles).

- **Walk / run speed**  (Phase 2 C1).  The Ghidra investigation on
  main's earlier attempt showed the ``attacks_transitions.walkSpeed``
  cells are dead data.  The REAL player-movement formula lives at
  ``FUN_00085f50`` (called per-frame from the player tick
  ``FUN_0008c230``):

  - ``VA 0x85F62``: ``MOV EAX,[EBP+0x34]; FLD [EAX+0x40]`` — loads the
    player's "base speed" from the runtime critter struct.  That
    field is always ``1.0`` because ``critters_critter_data`` has no
    ``runSpeed`` row.
  - ``VA 0x85F69``: ``FMUL [EBP+0x124]`` — multiplies by the stick
    magnitude, which was itself multiplied by ``3.0`` in
    ``FUN_00084940`` when the running button is held
    (``FMUL float ptr [0x001A25BC]`` at VA 0x849E4).

  The shared ``3.0`` constant has **45** read sites across the engine
  (AI, collision, audio — everything unrelated to movement), so
  patching it globally is not an option.

  Our approach: inject two per-player 4-byte floats into the XBE's
  appended SHIMS section, then rewrite the two player-site instructions
  to reference those instead of the shared constants:

  - ``0x85F62``: ``8B 45 34 D9 40 40`` (6 B) ->
    ``D9 05 <va of g_walk_speed>`` (6 B).  Loads our injected float.
  - ``0x849E4``: ``D8 0D BC 25 1A 00`` (6 B) ->
    ``D8 0D <va of g_run_multiplier>`` (6 B).  Multiplies by our
    per-player constant instead of the shared one.

  Defaults ``walk_scale = 1.0`` and ``run_scale = 1.0`` produce
  injected floats of ``1.0`` and ``3.0`` respectively — byte-identical
  behaviour to vanilla under the new code path.  Sliders change the
  injected values.

  Fields exposed:

  - ``walk_speed_scale``: scales the base walking speed.  1.0 =
    vanilla; 2.0 = walking and running both become 2x faster (same
    walk-to-run ratio).
  - ``run_speed_scale``: scales the run-vs-walk multiplier.  1.0 =
    vanilla 3x running; 2.0 = running becomes 6x walking speed.
"""

from __future__ import annotations

import struct

from azurik_mod.patching import (
    ParametricPatch,
    apply_parametric_patch,
    va_to_file,
)
from azurik_mod.patching.registry import PatchPack, register_pack

# ---------------------------------------------------------------------------
# Phase 1 — gravity
# ---------------------------------------------------------------------------

GRAVITY_BASELINE = 9.8
"""Baseline world-gravity value in m/s^2 — the .rdata float at VA 0x1980A8."""

GRAVITY_PATCH = ParametricPatch(
    name="gravity",
    label="World gravity",
    va=0x001980A8,
    size=4,
    original=struct.pack("<f", GRAVITY_BASELINE),
    default=GRAVITY_BASELINE,
    # Full expressive range: 0.0 (no gravity, float-through-air) up
    # to 100.0 m/s^2 (~10x Earth — enemies slam the floor instantly).
    # The slider widget is paired with an exact-value entry for
    # precise tuning anywhere in this range.
    slider_min=0.0,
    slider_max=100.0,
    slider_step=0.1,
    unit="m/s^2",
    encode=lambda g: struct.pack("<f", float(g)),
    decode=lambda b: struct.unpack("<f", b)[0],
)


# ---------------------------------------------------------------------------
# Player-speed sliders (Phase 2 C1 — live)
# ---------------------------------------------------------------------------
#
# Both sliders are "virtual" ParametricPatches (va=0 / size=0) that
# the GUI still renders as numeric inputs.  The real patch happens in
# apply_player_speed() below, which injects two 4-byte floats into an
# appended XBE section and rewrites two player-specific instructions
# to reference them.

WALK_SPEED_SCALE = ParametricPatch(
    name="walk_speed_scale",
    label="Player walk speed",
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
)

RUN_SPEED_SCALE = ParametricPatch(
    name="run_speed_scale",
    label="Player run speed",
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
)


# Vanilla byte sequences at the two player-specific patch sites.  If
# either drifts (game update, prior patch, etc.) apply_player_speed
# bails out rather than silently corrupt the XBE.
_WALK_SITE_VA = 0x00085F62
_WALK_SITE_VANILLA = bytes([
    0x8B, 0x45, 0x34,      # MOV EAX, [EBP+0x34]
    0xD9, 0x40, 0x40,      # FLD dword [EAX+0x40]
])
_RUN_SITE_VA = 0x000849E4
_RUN_SITE_VANILLA = bytes([
    0xD8, 0x0D,
    0xBC, 0x25, 0x1A, 0x00,   # FMUL dword [0x001A25BC]
])
# Baseline run-multiplier in the vanilla code path (the `3.0` at
# 0x001A25BC).  Our injected constant starts at 3.0 * run_scale.
_VANILLA_RUN_MULTIPLIER = 3.0


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def apply_player_physics(
    xbe_data: bytearray,
    *,
    gravity: float | None = None,
    walk_scale: float | None = None,
    run_scale: float | None = None,
    **_ignored,
) -> None:
    """Apply the XBE-side portion of the player physics pack.

    All three adjustments operate on ``default.xbe`` directly.  Speed
    sliders no longer touch ``config.xbr`` — the values there turned
    out to be dead data (see module docstring).
    """
    if gravity is not None:
        apply_parametric_patch(xbe_data, GRAVITY_PATCH, float(gravity))

    w = 1.0 if walk_scale is None else float(walk_scale)
    r = 1.0 if run_scale is None else float(run_scale)
    if w != 1.0 or r != 1.0:
        apply_player_speed(xbe_data, walk_scale=w, run_scale=r)


def apply_player_speed(
    xbe_data: bytearray,
    *,
    walk_scale: float = 1.0,
    run_scale: float = 1.0,
) -> bool:
    """Patch ``default.xbe`` so the player walks / runs at custom speeds.

    How it works (see module docstring for the full story):

    1. Injects two 4-byte floats into the XBE via the shim landing
       infrastructure (``_carve_shim_landing`` — the same mechanism
       C-shim trampolines use for their code).  The floats are
       ``walk_scale`` (default 1.0) and ``3.0 * run_scale`` (default
       3.0, preserving vanilla's hardcoded run multiplier).
    2. Rewrites the ``FLD dword [EAX+0x40]`` at VA 0x85F65 (plus the
       preceding 3-byte ``MOV EAX,[EBP+0x34]``) into a 6-byte
       ``FLD dword [abs walk_va]``.  The player's base speed now
       comes from our injected constant instead of the always-1.0
       ``entity->runSpeed`` slot.
    3. Rewrites the ``FMUL dword [0x001A25BC]`` at VA 0x849E4 into
       ``FMUL dword [abs run_va]``.  The shared 3.0 constant is left
       alone — all 45 other readers (collision, AI, audio, etc.)
       keep their vanilla behaviour.

    Returns True when the patch was applied, False if both scales are
    at their default of 1.0 (no-op) or if the patch sites have drifted
    from vanilla (already patched / game update / etc.) — in the drift
    case a warning is printed and the buffer is left untouched.
    """
    if walk_scale == 1.0 and run_scale == 1.0:
        return False

    # Late import to avoid the circular dependency that would happen if
    # apply.py imported this module at top level.
    from azurik_mod.patching.apply import _carve_shim_landing

    walk_off = va_to_file(_WALK_SITE_VA)
    run_off = va_to_file(_RUN_SITE_VA)

    # --- Safety: ensure vanilla bytes at both sites -------------------
    if bytes(xbe_data[walk_off:walk_off + 6]) != _WALK_SITE_VANILLA:
        print(f"  WARNING: player_speed — walk site at VA "
              f"0x{_WALK_SITE_VA:X} already patched or drifted, "
              f"skipping (got "
              f"{bytes(xbe_data[walk_off:walk_off + 6]).hex()})")
        return False
    if bytes(xbe_data[run_off:run_off + 6]) != _RUN_SITE_VANILLA:
        print(f"  WARNING: player_speed — run site at VA "
              f"0x{_RUN_SITE_VA:X} already patched or drifted, "
              f"skipping (got "
              f"{bytes(xbe_data[run_off:run_off + 6]).hex()})")
        return False

    # --- Inject our two per-player floats -----------------------------
    walk_value_bytes = struct.pack("<f", 1.0 * walk_scale)
    run_value_bytes = struct.pack(
        "<f", _VANILLA_RUN_MULTIPLIER * run_scale)
    _, walk_va = _carve_shim_landing(xbe_data, walk_value_bytes)
    _, run_va = _carve_shim_landing(xbe_data, run_value_bytes)

    # --- Rewrite both instructions to reference our injected floats ---
    # FLD dword [abs walk_va]   encoded as D9 05 <va>
    xbe_data[walk_off:walk_off + 6] = (
        b"\xD9\x05" + struct.pack("<I", walk_va))
    # FMUL dword [abs run_va]   encoded as D8 0D <va>
    xbe_data[run_off:run_off + 6] = (
        b"\xD8\x0D" + struct.pack("<I", run_va))

    print(f"  Player walk speed: {walk_scale:.3f}x  "
          f"(base float at VA 0x{walk_va:X})")
    print(f"  Player run speed:  {run_scale:.3f}x  "
          f"(multiplier float at VA 0x{run_va:X}, "
          f"= {_VANILLA_RUN_MULTIPLIER * run_scale:.2f})")
    return True


def _player_speed_dynamic_whitelist(
    xbe: bytes,
) -> list[tuple[int, int]]:
    """Return the byte ranges touched by :func:`apply_player_speed`.

    ``verify-patches --strict`` calls this during its whitelist-diff
    pass so the FLD/FMUL instruction rewrites AND the two injected
    per-player floats don't register as unexpected byte flips.  The
    float VAs are baked into the rewritten instructions at apply
    time — we follow the ``abs32`` field in each patched instruction
    to find them, then resolve to file offsets via the live section
    table (which covers an appended ``SHIMS`` section if the floats
    ended up there).

    Always returns the static 6-byte rewrite ranges; adds the
    dynamic 4-byte float ranges only when the apply is detected.
    Invoked on vanilla XBEs too (the pack may not have been applied)
    so must never raise on unrecognised bytes.
    """
    from azurik_mod.patching.xbe import parse_xbe_sections, va_to_file

    try:
        walk_off = va_to_file(_WALK_SITE_VA)
        run_off = va_to_file(_RUN_SITE_VA)
    except Exception:  # noqa: BLE001
        return []

    # Static: the two 6-byte instruction rewrite sites are always
    # whitelisted.  On a vanilla XBE these ranges are unchanged and
    # the diff reports zero bytes in them, so whitelisting them is
    # safe.
    ranges: list[tuple[int, int]] = [
        (walk_off, walk_off + 6),
        (run_off, run_off + 6),
    ]

    # Dynamic: if either site has been rewritten to `FLD/FMUL [abs32]`,
    # follow the abs32 pointer through the section table and whitelist
    # a 4-byte range for the injected float constant.
    def _resolve_va_to_file(va: int) -> int | None:
        _, secs = parse_xbe_sections(xbe)
        for s in secs:
            if s["vaddr"] <= va < s["vaddr"] + s["vsize"]:
                delta = va - s["vaddr"]
                if delta < s["raw_size"]:
                    return s["raw_addr"] + delta
        return None

    if len(xbe) >= walk_off + 6:
        walk_bytes = xbe[walk_off:walk_off + 6]
        if walk_bytes[:2] == b"\xD9\x05":
            walk_va = struct.unpack("<I", walk_bytes[2:6])[0]
            fo = _resolve_va_to_file(walk_va)
            if fo is not None:
                ranges.append((fo, fo + 4))

    if len(xbe) >= run_off + 6:
        run_bytes = xbe[run_off:run_off + 6]
        if run_bytes[:2] == b"\xD8\x0D":
            run_va = struct.unpack("<I", run_bytes[2:6])[0]
            fo = _resolve_va_to_file(run_va)
            if fo is not None:
                ranges.append((fo, fo + 4))

    return ranges


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PLAYER_PHYSICS_SITES = [
    GRAVITY_PATCH,
    WALK_SPEED_SCALE,
    RUN_SPEED_SCALE,
]
"""Registered Patches-page sites.  Gravity writes to a .rdata float
directly; walk/run speed sliders are "virtual" (va=0) and the pack's
apply function materialises their values into injected XBE bytes."""


def _apply_defaults(xbe_data: bytearray) -> None:
    """Default pack apply — noop at baseline.  The real work happens via
    apply_player_physics(..., gravity=..., walk_scale=..., run_scale=...)
    from the randomize-full / apply-physics pipelines."""


register_pack(PatchPack(
    name="player_physics",
    description=(
        "Scales world gravity and player walk / run speed.  "
        "Gravity is global; walk and run are player-only "
        "(enemies keep their vanilla behaviour)."
    ),
    sites=PLAYER_PHYSICS_SITES,
    apply=_apply_defaults,
    default_on=False,
    included_in_randomizer_qol=False,
    tags=("player", "physics"),
    dynamic_whitelist_from_xbe=_player_speed_dynamic_whitelist,
))


__all__ = [
    "GRAVITY_BASELINE",
    "GRAVITY_PATCH",
    "PLAYER_PHYSICS_SITES",
    "RUN_SPEED_SCALE",
    "WALK_SPEED_SCALE",
    "apply_player_physics",
    "apply_player_speed",
]
