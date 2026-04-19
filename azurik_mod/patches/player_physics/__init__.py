"""Player physics patch pack — gravity + player walk / run speed.

- **Gravity** (``.rdata`` float at VA 0x1980A8, baseline 9.8 m/s²).
  The engine integrates gravity in ``FUN_00085700`` via
  ``v_z' = v_z - g*dt``, reading this single global — so overwriting
  it scales world gravity for everything that falls (player, enemies,
  projectiles).

- **Walk / run speed** (Phase 2 C1; v2 April 2026 — independence).
  The REAL player-movement formula is in ``FUN_00085F50`` (called
  per-frame from the player tick) + ``FUN_00084940`` (per-frame input
  normaliser):

  - ``VA 0x85F62``: ``MOV EAX,[EBP+0x34]; FLD [EAX+0x40]`` — loads
    ``CritterData.run_speed`` (+0x40).  Vanilla runtime value is
    ``7.0`` for the player entity (confirmed via lldb — the earlier
    docstring claim of ``always 1.0`` was wrong).  NOT populated from
    ``config.xbr``; comes from the struct's default initialiser.
  - ``VA 0x85F69``: ``FMUL [EBP+0x124]`` — multiplies by
    ``PlayerInputState.magnitude`` (+0x124), populated by
    ``FUN_00084940`` from raw controller input (range 0..1).
  - ``VA 0x849E4`` (inside ``FUN_00084940``): ``FMUL [0x001A25BC]``
    multiplies the magnitude by ``3.0`` when the running-button gate
    ``PlayerInputState.flags & 0x40`` is set.

  Resulting vanilla speeds: walking = ``7 × raw_stick``, running =
  ``21 × raw_stick``.

  The shared ``3.0`` constant at ``0x001A25BC`` has **45** readers
  across AI / collision / audio / etc., so patching it globally is
  not an option.

  **Our approach** — inject two per-player 4-byte floats into the
  XBE's appended SHIMS section, rewrite the two player-site
  instructions to reference those constants:

  - ``0x85F62``: ``8B 45 34 D9 40 40`` (6 B) ->
    ``D9 05 <va of inject_base>`` (6 B).  Loads our injected base.
  - ``0x849E4``: ``D8 0D BC 25 1A 00`` (6 B) ->
    ``D8 0D <va of inject_mult>`` (6 B).  Multiplies by our per-
    player multiplier instead of the shared 3.0.

  **Independence math** — one base feeds both code paths, so making
  the two sliders independent requires solving for both injected
  values simultaneously.  With slider semantics

  - ``walk_scale`` = multiplier on vanilla walking
  - ``run_scale``  = multiplier on vanilla running

  we set

  - ``inject_base = 7 × walk_scale``
  - ``inject_mult = 3 × run_scale / walk_scale``

  so that the engine computes

  - walking  = ``inject_base × raw_stick``
             = ``7 × walk_scale × raw_stick``
             = ``walk_scale × vanilla_walking``
  - running  = ``inject_base × inject_mult × raw_stick``
             = ``(7 × walk_scale) × (3 × run_scale / walk_scale) × raw_stick``
             = ``21 × run_scale × raw_stick``
             = ``run_scale × vanilla_running``

  The ``walk_scale`` cancels cleanly in the running path, making
  each slider scale only its own vanilla baseline.

  ``walk_scale=1 AND run_scale=1`` short-circuits the apply (no
  patch bytes touched — vanilla preserved byte-for-byte).  Any
  other combination is a true independent multiplier.

  Fields exposed:

  - ``walk_speed_scale``: multiplier on vanilla walking speed.
    1.0 = vanilla, 2.0 = 2× vanilla walking, 0.5 = half speed.
    Does NOT affect running.
  - ``run_speed_scale``: multiplier on vanilla running speed.
    1.0 = vanilla running (which is 3× walking), 2.0 = 2× vanilla
    running.  Does NOT affect walking.
"""

from __future__ import annotations

import struct

from azurik_mod.patching import (
    ParametricPatch,
    apply_parametric_patch,
    va_to_file,
)
from azurik_mod.patching.registry import Feature, register_feature

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
# Player-speed sliders (Phase 2 C1 — v2 April 2026, independent semantics)
# ---------------------------------------------------------------------------
#
# Both sliders are "virtual" ParametricPatches (va=0 / size=0) — the
# GUI still renders them as numeric inputs, but the actual patch math
# derives the two injected floats from BOTH sliders together (see
# apply_player_speed below).  walk_scale multiplies vanilla walking
# only; run_scale multiplies vanilla running only.  The cross-term
# cancels cleanly because one slider appears as a divisor in the
# other's injected value.

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
# 0x001A25BC).  Our injected constant starts at 3.0 * run_scale /
# walk_scale (see apply_player_speed for the independence math).
_VANILLA_RUN_MULTIPLIER = 3.0

# Vanilla runtime value of CritterData.run_speed (+0x40) for the
# player entity.  Confirmed via lldb at VA 0x00085F65 — the FLD
# [EAX+0x40] immediately after MOV EAX, [EBP+0x34] in FUN_00085F50.
# NOT populated from config.xbr (critters_critter_data has no
# runSpeed row for garret4); comes from the CritterData struct's
# default initialiser.  This is the identity baseline so
# walk_speed=1.0 preserves vanilla exactly.
_VANILLA_PLAYER_BASE_SPEED = 7.0

# Lower bound on walk_scale when it appears as a DIVISOR in the run
# multiplier formula.  UI min is 0.1, but defend against future
# changes (or direct Python callers) that could pass 0.
_WALK_SCALE_MIN = 0.01


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

    ``walk_scale`` and ``run_scale`` are INDEPENDENT multipliers on
    vanilla walking and vanilla running respectively (see module
    docstring for the derivation).  ``1.0 / 1.0`` is a byte-identity
    no-op; anything else rewrites both player-specific instruction
    sites.

    How it works:

    1. Derives two injected floats from BOTH sliders:

         inject_base = _VANILLA_PLAYER_BASE_SPEED × walk_scale
         inject_mult = _VANILLA_RUN_MULTIPLIER × run_scale / walk_scale

       so the game formula ``base × stick_mag`` (walking) and
       ``base × mult × stick_mag`` (running) yield
       ``walk_scale × vanilla_walking`` and ``run_scale ×
       vanilla_running`` respectively — each slider scales only its
       own vanilla baseline.
    2. Injects both floats into the XBE via the shim-landing
       infrastructure (``_carve_shim_landing`` — same mechanism C-shim
       trampolines use).
    3. Rewrites ``MOV EAX,[EBP+0x34]; FLD [EAX+0x40]`` at VA 0x85F62
       into ``FLD dword [abs walk_va]`` (6 bytes).  The base now
       comes from ``inject_base`` instead of ``CritterData.run_speed``
       (which is 7.0 at runtime for the player).
    4. Rewrites ``FMUL dword [0x001A25BC]`` at VA 0x849E4 into
       ``FMUL dword [abs run_va]``.  The shared 3.0 constant at
       0x001A25BC is left untouched — all 45 other readers keep
       vanilla behaviour.

    Returns True when the patch was applied, False if both scales are
    at the default of 1.0 (no-op) or if the patch sites have drifted
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
    # Independence math (derivation in the module docstring).  With
    # the game formula
    #     walking_speed = inject_base × raw_stick
    #     running_speed = inject_base × inject_mult × raw_stick
    # and our slider semantics
    #     walk_scale    = multiplier on vanilla walking
    #     run_scale     = multiplier on vanilla running
    # we solve for:
    #     inject_base   = _VANILLA_PLAYER_BASE_SPEED × walk_scale
    #     inject_mult   = _VANILLA_RUN_MULTIPLIER × run_scale / walk_scale
    # The division makes the sliders INDEPENDENT: walk_scale affects
    # only walking, run_scale affects only running.  Clamp the
    # denominator to _WALK_SCALE_MIN so no slider extreme can produce
    # a divide-by-zero NaN float in the XBE.
    safe_walk_scale = max(_WALK_SCALE_MIN, float(walk_scale))
    inject_base = _VANILLA_PLAYER_BASE_SPEED * safe_walk_scale
    inject_mult = (_VANILLA_RUN_MULTIPLIER * float(run_scale)
                   / safe_walk_scale)
    walk_value_bytes = struct.pack("<f", inject_base)
    run_value_bytes = struct.pack("<f", inject_mult)
    _, walk_va = _carve_shim_landing(xbe_data, walk_value_bytes)
    _, run_va = _carve_shim_landing(xbe_data, run_value_bytes)

    # --- Rewrite both instructions to reference our injected floats ---
    # FLD dword [abs walk_va]   encoded as D9 05 <va>
    xbe_data[walk_off:walk_off + 6] = (
        b"\xD9\x05" + struct.pack("<I", walk_va))
    # FMUL dword [abs run_va]   encoded as D8 0D <va>
    xbe_data[run_off:run_off + 6] = (
        b"\xD8\x0D" + struct.pack("<I", run_va))

    print(f"  Player walk speed: {walk_scale:.3f}x vanilla  "
          f"(injected base = {inject_base:.3f}, VA 0x{walk_va:X})")
    print(f"  Player run speed:  {run_scale:.3f}x vanilla  "
          f"(injected run mult = {inject_mult:.3f}, VA 0x{run_va:X})")
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
    """Back-compat apply (no params).  The unified dispatcher uses
    ``_custom_apply`` below; this wrapper stays for callers that still
    invoke ``apply=FEATURE.apply`` with no parameters."""


def _custom_apply(
    xbe_data: bytearray,
    gravity: float | None = None,
    walk_speed_scale: float | None = None,
    run_speed_scale: float | None = None,
    # Accept the old kwarg spellings too so the CLI doesn't have to
    # rename them mid-migration.
    walk_scale: float | None = None,
    run_scale: float | None = None,
    **_extra,
) -> None:
    """Unified-dispatcher hook — forwards slider kwargs to the full
    ``apply_player_physics`` implementation.

    ``params`` on the dispatcher side is ``{"gravity": ...,
    "walk_speed_scale": ..., "run_speed_scale": ...}`` (matching the
    ParametricPatch names).  We also accept the short aliases
    (``walk_scale`` / ``run_scale``) used by older CLI code.
    """
    apply_player_physics(
        xbe_data,
        gravity=gravity,
        walk_scale=walk_speed_scale if walk_speed_scale is not None else walk_scale,
        run_scale=run_speed_scale if run_speed_scale is not None else run_scale,
    )


FEATURE = register_feature(Feature(
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
    category="player",
    tags=("physics",),
    dynamic_whitelist_from_xbe=_player_speed_dynamic_whitelist,
    custom_apply=_custom_apply,
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
