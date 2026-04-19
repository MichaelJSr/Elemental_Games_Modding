"""Player physics patch pack — gravity + walk / roll / swim speed.

- **Gravity** (``.rdata`` float at VA 0x1980A8, baseline 9.8 m/s²).
  The engine integrates gravity in ``FUN_00085700`` via
  ``v_z' = v_z - g*dt``, reading this single global — so overwriting
  it scales world gravity for everything that falls (player, enemies,
  projectiles).

- **Walk speed** (Phase 2 C1; v2 April 2026 — independence).
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

  Vanilla walking speed = ``7 × raw_stick``.

- **Roll speed** (Phase 2 C1; v3 April 2026 — correct semantics).
  What ``run_speed_scale`` was previously called is actually the
  ROLL / diving boost: the 3.0 multiplier at ``VA 0x849E4`` is gated
  by ``PlayerInputState.flags & 0x40``, which is driven by the
  ``WHITE`` (or ``BACK``) controller button — NOT by a "run" button.
  Azurik has no separate run/sprint — walking is just
  ``CritterData.run_speed × stick``.  The 3.0 boost is the
  roll/dodge / dive-in-jump effect.  Sites:

  - ``VA 0x849E4`` inside ``FUN_00084940``: ``FMUL [0x001A25BC]``
    multiplies magnitude by ``3.0`` when the roll-button gate is set.
    The same post-FMUL magnitude feeds the walking state, the jump /
    glide state (``FUN_00089480``), and the swim state
    (``FUN_0008b700``), so boosting it accelerates all of them.

  The shared ``3.0`` constant at ``0x001A25BC`` has **45** readers
  across AI / collision / audio / etc., so patching it globally is
  not an option.  We patch only the player-specific load.

- **Swim speed** (new, April 2026).  The swim state function is
  ``FUN_0008b700``, dispatched from the state machine as state 6
  (set after the ``"loc/english/popups/swim"`` prompt fires):

  - ``VA 0x8B7BF``: ``FMUL [0x001A25B4]`` multiplies
    ``PlayerInputState.magnitude`` by ``10.0`` before the direction
    vector is applied.  That's the swim-stroke coefficient — vanilla
    swim speed is ``10 × raw_stick`` (plus the 3× roll boost if
    WHITE is held underwater).

  The shared ``10.0`` at ``0x001A25B4`` has 8 external readers (most
  unrelated to player movement), so we patch only the swim-state
  load.

**Our approach** — inject three per-player 4-byte floats into the
XBE's appended SHIMS section, rewrite three player-site instructions
to reference them:

- ``0x85F62``: ``8B 45 34 D9 40 40`` (6 B) ->
  ``D9 05 <va of inject_base>`` (6 B).  Loads our injected walk base.
- ``0x849E4``: ``D8 0D BC 25 1A 00`` (6 B) ->
  ``D8 0D <va of inject_roll_mult>`` (6 B).  Multiplies by our per-
  player roll multiplier instead of the shared 3.0.
- ``0x8B7BF``: ``D8 0D B4 25 1A 00`` (6 B) ->
  ``D8 0D <va of inject_swim_mult>`` (6 B).  Multiplies by our per-
  player swim coefficient instead of the shared 10.0.

**Independence math** — the walk and roll sliders both feed the
same ``FLD / FMUL`` chain, so making them independent requires
solving for the pair of injected values simultaneously.  With
slider semantics

- ``walk_scale`` = multiplier on vanilla walking
- ``roll_scale`` = multiplier on vanilla rolling (WHITE-button boost)

we set

- ``inject_base      = 7  × walk_scale``
- ``inject_roll_mult = 3  × roll_scale / walk_scale``
- ``inject_swim_mult = 10 × swim_scale``  (independent by construction)

The engine then computes:

- walking = ``inject_base × raw_stick`` = ``walk_scale × vanilla_walking``
- rolling = ``inject_base × inject_roll_mult × raw_stick``
         = ``21 × roll_scale × raw_stick``
         = ``roll_scale × vanilla_rolling``
- swimming = ``inject_swim_mult × raw_stick``
          = ``swim_scale × vanilla_swimming``

``walk_scale cancels`` cleanly in the rolling path, making each
slider scale only its own vanilla baseline.  Swim is already
independent (different site, different constant).  All three = 1.0
short-circuits the apply (vanilla bytes preserved byte-for-byte).

Fields exposed:

- ``walk_speed_scale``: multiplier on vanilla walking speed.  Does
  NOT affect rolling or swimming.
- ``roll_speed_scale``: multiplier on the WHITE/BACK-button boost
  (rolling, diving-in-midair).  Does NOT affect walking or swimming.
- ``swim_speed_scale``: multiplier on vanilla swim stroke speed.
  Does NOT affect walking or rolling.

Backwards compatibility: the legacy ``run_scale`` /
``run_speed_scale`` kwargs are still accepted and treated as
aliases for ``roll_scale`` / ``roll_speed_scale`` — the old name
was a documentation mistake (see docs/LEARNINGS.md "Roll, not run").
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
# Player-speed sliders (Phase 2 C1 — v3 April 2026, walk / roll / swim)
# ---------------------------------------------------------------------------
#
# All three sliders are "virtual" ParametricPatches (va=0 / size=0) —
# the GUI renders them as numeric inputs, but the actual patch math
# lives in apply_player_speed below (walk + roll, coupled via the
# shared FLD/FMUL chain) and apply_swim_speed (independent site).

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

ROLL_SPEED_SCALE = ParametricPatch(
    name="roll_speed_scale",
    label="Player roll speed",
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

SWIM_SPEED_SCALE = ParametricPatch(
    name="swim_speed_scale",
    label="Player swim speed",
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

# Back-compat alias: the old name `RUN_SPEED_SCALE` shipped before we
# realised the 3.0 multiplier is the roll boost, not a run modifier.
# Keep as an import alias so any external code still works.
RUN_SPEED_SCALE = ROLL_SPEED_SCALE


# Vanilla byte sequences at the three player-specific patch sites.
# If any drifts (game update, prior patch, etc.) the apply function
# bails out rather than silently corrupt the XBE.
_WALK_SITE_VA = 0x00085F62
_WALK_SITE_VANILLA = bytes([
    0x8B, 0x45, 0x34,          # MOV EAX, [EBP+0x34]
    0xD9, 0x40, 0x40,          # FLD dword [EAX+0x40]
])

_ROLL_SITE_VA = 0x000849E4
_ROLL_SITE_VANILLA = bytes([
    0xD8, 0x0D,
    0xBC, 0x25, 0x1A, 0x00,    # FMUL dword [0x001A25BC]
])
# Back-compat aliases (_RUN_SITE_* was the old name).
_RUN_SITE_VA = _ROLL_SITE_VA
_RUN_SITE_VANILLA = _ROLL_SITE_VANILLA

_SWIM_SITE_VA = 0x0008B7BF
_SWIM_SITE_VANILLA = bytes([
    0xD8, 0x0D,
    0xB4, 0x25, 0x1A, 0x00,    # FMUL dword [0x001A25B4]
])

# Baseline roll-multiplier in the vanilla code path (the `3.0` at
# 0x001A25BC).  Our injected constant becomes 3.0 × roll_scale /
# walk_scale — the division makes the two sliders independent (see
# module docstring).
_VANILLA_ROLL_MULTIPLIER = 3.0
# Back-compat alias.
_VANILLA_RUN_MULTIPLIER = _VANILLA_ROLL_MULTIPLIER

# Baseline swim coefficient (the `10.0` at 0x001A25B4).  Swim is its
# own state function (FUN_0008b700), independent of the walk/roll FLD
# chain, so the injected value is simply 10.0 × swim_scale.
_VANILLA_SWIM_MULTIPLIER = 10.0

# Vanilla runtime value of CritterData.run_speed (+0x40) for the
# player entity.  Confirmed via lldb at VA 0x00085F65 — the FLD
# [EAX+0x40] immediately after MOV EAX, [EBP+0x34] in FUN_00085F50.
# NOT populated from config.xbr (critters_critter_data has no
# runSpeed row for garret4); comes from the CritterData struct's
# default initialiser.  This is the identity baseline so
# walk_speed=1.0 preserves vanilla exactly.
_VANILLA_PLAYER_BASE_SPEED = 7.0

# Lower bound on walk_scale when it appears as a DIVISOR in the roll
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
    roll_scale: float | None = None,
    swim_scale: float | None = None,
    # Back-compat alias: callers that still pass run_scale get
    # transparently routed to roll_scale (the new name).
    run_scale: float | None = None,
    **_ignored,
) -> None:
    """Apply the XBE-side portion of the player physics pack.

    All four adjustments operate on ``default.xbe`` directly.  Speed
    sliders no longer touch ``config.xbr`` — the values there turned
    out to be dead data (see module docstring).
    """
    if gravity is not None:
        apply_parametric_patch(xbe_data, GRAVITY_PATCH, float(gravity))

    # Back-compat: run_scale -> roll_scale.  Explicit roll_scale wins.
    if roll_scale is None and run_scale is not None:
        roll_scale = run_scale

    w = 1.0 if walk_scale is None else float(walk_scale)
    r = 1.0 if roll_scale is None else float(roll_scale)
    if w != 1.0 or r != 1.0:
        apply_player_speed(xbe_data, walk_scale=w, roll_scale=r)

    s = 1.0 if swim_scale is None else float(swim_scale)
    if s != 1.0:
        apply_swim_speed(xbe_data, swim_scale=s)


def apply_player_speed(
    xbe_data: bytearray,
    *,
    walk_scale: float = 1.0,
    roll_scale: float = 1.0,
    # Back-compat kwarg alias — old code may still pass run_scale.
    run_scale: float | None = None,
) -> bool:
    """Patch ``default.xbe`` so the player walks / rolls at custom speeds.

    ``walk_scale`` and ``roll_scale`` are INDEPENDENT multipliers on
    vanilla walking and vanilla rolling respectively (see module
    docstring for the derivation).  ``1.0 / 1.0`` is a byte-identity
    no-op; anything else rewrites both player-specific instruction
    sites.

    How it works:

    1. Derives two injected floats from BOTH sliders:

         inject_base      = _VANILLA_PLAYER_BASE_SPEED × walk_scale
         inject_roll_mult = _VANILLA_ROLL_MULTIPLIER   × roll_scale / walk_scale

       so the game formula ``base × stick_mag`` (walking) and
       ``base × mult × stick_mag`` (rolling) yield
       ``walk_scale × vanilla_walking`` and ``roll_scale ×
       vanilla_rolling`` respectively — each slider scales only its
       own vanilla baseline.
    2. Injects both floats into the XBE via the shim-landing
       infrastructure (``_carve_shim_landing`` — same mechanism C-shim
       trampolines use).
    3. Rewrites ``MOV EAX,[EBP+0x34]; FLD [EAX+0x40]`` at VA 0x85F62
       into ``FLD dword [abs walk_va]`` (6 bytes).  The base now
       comes from ``inject_base`` instead of ``CritterData.run_speed``
       (which is 7.0 at runtime for the player).
    4. Rewrites ``FMUL dword [0x001A25BC]`` at VA 0x849E4 into
       ``FMUL dword [abs roll_va]``.  The shared 3.0 constant at
       0x001A25BC is left untouched — all 45 other readers keep
       vanilla behaviour.

    Returns True when the patch was applied, False if both scales are
    at the default of 1.0 (no-op) or if the patch sites have drifted
    from vanilla (already patched / game update / etc.) — in the drift
    case a warning is printed and the buffer is left untouched.
    """
    # Back-compat: accept old run_scale kwarg.  Explicit roll_scale
    # wins if the caller set both.
    if run_scale is not None and roll_scale == 1.0:
        roll_scale = run_scale

    if walk_scale == 1.0 and roll_scale == 1.0:
        return False

    # Late import to avoid the circular dependency that would happen if
    # apply.py imported this module at top level.
    from azurik_mod.patching.apply import _carve_shim_landing

    walk_off = va_to_file(_WALK_SITE_VA)
    roll_off = va_to_file(_ROLL_SITE_VA)

    # --- Safety: ensure vanilla bytes at both sites -------------------
    if bytes(xbe_data[walk_off:walk_off + 6]) != _WALK_SITE_VANILLA:
        print(f"  WARNING: player_speed — walk site at VA "
              f"0x{_WALK_SITE_VA:X} already patched or drifted, "
              f"skipping (got "
              f"{bytes(xbe_data[walk_off:walk_off + 6]).hex()})")
        return False
    if bytes(xbe_data[roll_off:roll_off + 6]) != _ROLL_SITE_VANILLA:
        print(f"  WARNING: player_speed — roll site at VA "
              f"0x{_ROLL_SITE_VA:X} already patched or drifted, "
              f"skipping (got "
              f"{bytes(xbe_data[roll_off:roll_off + 6]).hex()})")
        return False

    # --- Inject our two per-player floats -----------------------------
    # Independence math (derivation in the module docstring).  With
    # the game formula
    #     walking_speed = inject_base × raw_stick
    #     rolling_speed = inject_base × inject_roll_mult × raw_stick
    # and our slider semantics
    #     walk_scale    = multiplier on vanilla walking
    #     roll_scale    = multiplier on vanilla rolling
    # we solve for:
    #     inject_base      = _VANILLA_PLAYER_BASE_SPEED × walk_scale
    #     inject_roll_mult = _VANILLA_ROLL_MULTIPLIER × roll_scale / walk_scale
    # The division makes the sliders INDEPENDENT: walk_scale affects
    # only walking, roll_scale affects only rolling.  Clamp the
    # denominator to _WALK_SCALE_MIN so no slider extreme can produce
    # a divide-by-zero NaN float in the XBE.
    safe_walk_scale = max(_WALK_SCALE_MIN, float(walk_scale))
    inject_base = _VANILLA_PLAYER_BASE_SPEED * safe_walk_scale
    inject_roll_mult = (_VANILLA_ROLL_MULTIPLIER * float(roll_scale)
                        / safe_walk_scale)
    walk_value_bytes = struct.pack("<f", inject_base)
    roll_value_bytes = struct.pack("<f", inject_roll_mult)
    _, walk_va = _carve_shim_landing(xbe_data, walk_value_bytes)
    _, roll_va = _carve_shim_landing(xbe_data, roll_value_bytes)

    # --- Rewrite both instructions to reference our injected floats ---
    # FLD dword [abs walk_va]   encoded as D9 05 <va>
    xbe_data[walk_off:walk_off + 6] = (
        b"\xD9\x05" + struct.pack("<I", walk_va))
    # FMUL dword [abs roll_va]   encoded as D8 0D <va>
    xbe_data[roll_off:roll_off + 6] = (
        b"\xD8\x0D" + struct.pack("<I", roll_va))

    print(f"  Player walk speed: {walk_scale:.3f}x vanilla  "
          f"(injected base = {inject_base:.3f}, VA 0x{walk_va:X})")
    print(f"  Player roll speed: {roll_scale:.3f}x vanilla  "
          f"(injected roll mult = {inject_roll_mult:.3f}, "
          f"VA 0x{roll_va:X})")
    return True


def apply_swim_speed(
    xbe_data: bytearray,
    *,
    swim_scale: float = 1.0,
) -> bool:
    """Patch ``default.xbe`` so the player swims at a custom speed.

    The swim state function ``FUN_0008b700`` multiplies
    ``PlayerInputState.magnitude`` by the shared float ``10.0`` at
    VA ``0x001A25B4`` (FMUL instruction at ``VA 0x8B7BF``).  We
    inject a per-player 4-byte float ``10.0 × swim_scale`` into the
    appended SHIMS section and rewrite the FMUL to reference it,
    leaving the shared 10.0 constant intact for its 7 other readers.

    Returns True on apply, False on no-op or site drift (warning
    printed; buffer unchanged).
    """
    if swim_scale == 1.0:
        return False

    from azurik_mod.patching.apply import _carve_shim_landing

    swim_off = va_to_file(_SWIM_SITE_VA)

    if bytes(xbe_data[swim_off:swim_off + 6]) != _SWIM_SITE_VANILLA:
        print(f"  WARNING: player_speed — swim site at VA "
              f"0x{_SWIM_SITE_VA:X} already patched or drifted, "
              f"skipping (got "
              f"{bytes(xbe_data[swim_off:swim_off + 6]).hex()})")
        return False

    inject_swim = _VANILLA_SWIM_MULTIPLIER * float(swim_scale)
    swim_value_bytes = struct.pack("<f", inject_swim)
    _, swim_va = _carve_shim_landing(xbe_data, swim_value_bytes)

    # FMUL dword [abs swim_va]   encoded as D8 0D <va>
    xbe_data[swim_off:swim_off + 6] = (
        b"\xD8\x0D" + struct.pack("<I", swim_va))

    print(f"  Player swim speed: {swim_scale:.3f}x vanilla  "
          f"(injected swim mult = {inject_swim:.3f}, "
          f"VA 0x{swim_va:X})")
    return True


def _player_speed_dynamic_whitelist(
    xbe: bytes,
) -> list[tuple[int, int]]:
    """Return the byte ranges touched by :func:`apply_player_speed`
    and :func:`apply_swim_speed`.

    ``verify-patches --strict`` calls this during its whitelist-diff
    pass so the FLD/FMUL instruction rewrites AND the three injected
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
        roll_off = va_to_file(_ROLL_SITE_VA)
        swim_off = va_to_file(_SWIM_SITE_VA)
    except Exception:  # noqa: BLE001
        return []

    # Static: the three 6-byte instruction rewrite sites are always
    # whitelisted.  On a vanilla XBE these ranges are unchanged and
    # the diff reports zero bytes in them, so whitelisting them is
    # safe.
    ranges: list[tuple[int, int]] = [
        (walk_off, walk_off + 6),
        (roll_off, roll_off + 6),
        (swim_off, swim_off + 6),
    ]

    # Dynamic: if a site has been rewritten to `FLD/FMUL [abs32]`,
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

    for site_off, prefix in [
        (walk_off, b"\xD9\x05"),   # FLD  [abs32]
        (roll_off, b"\xD8\x0D"),   # FMUL [abs32]
        (swim_off, b"\xD8\x0D"),   # FMUL [abs32]
    ]:
        if len(xbe) >= site_off + 6:
            patch = xbe[site_off:site_off + 6]
            if patch[:2] == prefix:
                va = struct.unpack("<I", patch[2:6])[0]
                fo = _resolve_va_to_file(va)
                if fo is not None:
                    ranges.append((fo, fo + 4))

    return ranges


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PLAYER_PHYSICS_SITES = [
    GRAVITY_PATCH,
    WALK_SPEED_SCALE,
    ROLL_SPEED_SCALE,
    SWIM_SPEED_SCALE,
]
"""Registered Patches-page sites.  Gravity writes to a .rdata float
directly; walk/roll/swim speed sliders are "virtual" (va=0) and the
pack's apply function materialises their values into injected XBE
bytes."""


def _apply_defaults(xbe_data: bytearray) -> None:
    """Back-compat apply (no params).  The unified dispatcher uses
    ``_custom_apply`` below; this wrapper stays for callers that still
    invoke ``apply=FEATURE.apply`` with no parameters."""


def _custom_apply(
    xbe_data: bytearray,
    gravity: float | None = None,
    walk_speed_scale: float | None = None,
    roll_speed_scale: float | None = None,
    swim_speed_scale: float | None = None,
    # Back-compat: the old kwarg spellings.  New callers should use
    # the *_speed_scale forms, but CLI / serialized configs that
    # predate the rename still work.
    run_speed_scale: float | None = None,
    walk_scale: float | None = None,
    roll_scale: float | None = None,
    run_scale: float | None = None,
    swim_scale: float | None = None,
    **_extra,
) -> None:
    """Unified-dispatcher hook — forwards slider kwargs to the full
    ``apply_player_physics`` implementation.

    ``params`` on the dispatcher side is ``{"gravity": ...,
    "walk_speed_scale": ..., "roll_speed_scale": ...,
    "swim_speed_scale": ...}`` (matching the ParametricPatch names).
    We also accept the short aliases and the legacy ``run_*`` names
    used by older CLI code.
    """
    walk = walk_speed_scale if walk_speed_scale is not None else walk_scale
    roll = (
        roll_speed_scale if roll_speed_scale is not None
        else roll_scale if roll_scale is not None
        else run_speed_scale if run_speed_scale is not None
        else run_scale
    )
    swim = swim_speed_scale if swim_speed_scale is not None else swim_scale
    apply_player_physics(
        xbe_data,
        gravity=gravity,
        walk_scale=walk,
        roll_scale=roll,
        swim_scale=swim,
    )


FEATURE = register_feature(Feature(
    name="player_physics",
    description=(
        "Scales world gravity and player walk / roll / swim speed.  "
        "Gravity is global; walk, roll (WHITE-button boost), and "
        "swim are player-only (enemies keep their vanilla "
        "behaviour)."
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
    "ROLL_SPEED_SCALE",
    "RUN_SPEED_SCALE",
    "SWIM_SPEED_SCALE",
    "WALK_SPEED_SCALE",
    "apply_player_physics",
    "apply_player_speed",
    "apply_swim_speed",
]
