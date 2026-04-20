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

- **Air-control speed** (new April 2026).  While airborne, the
  player's horizontal movement is computed per-frame in
  ``FUN_00089480`` as ``local_16c = entity[+0x140] × magnitude``.
  Five ``MOV DWORD [reg+0x140], 0x41100000`` (= 9.0) imm32
  writes across the airborne-state init functions set this
  field.  We rewrite each imm32 to ``9.0 × air_control_scale``.
  Independent of jump height (different physics field).  Higher
  values = faster mid-air steering / more horizontal distance
  per jump.

- **Wing-flap height** (new April 2026).  The Air-power
  double-jump / wing-flap adds a fixed vertical impulse to
  ``entity + 0x2C`` (velocity.z) via a single
  ``FADD [0x001A25C0]`` at VA ``0x000896EA``, gated on both
  flap-button (input flag 0x04) AND roll (input flag 0x40).
  Default impulse is ``8.0``.  We rewrite the FADD target to
  reference an injected ``8.0 × flap_scale`` constant,
  preserving the shared 0x001A25C0 for its 4 other non-player
  readers.

- **Jump height / velocity** (v2 April 2026 — correct formula).
  The main jump initiation ``FUN_00089060`` (plays
  ``fx/sound/player/jump``) computes the initial vertical
  velocity via the classic projectile formula
  ``v₀ = sqrt(2 × g × h)``:

  .. code-block:: text

     VA 0x89160: FLD  [0x001980A8]          ; load g = 9.8 (GRAVITY)
     VA 0x89166: FMUL [ESI + 0x144]         ; × h = entity+0x144
     VA 0x8916C: FADD ST0, ST0               ; × 2
     VA 0x8916E: FSQRT                        ; v₀ = sqrt(2gh)
     VA 0x89170: FSTP [ESP + 0xC]            ; store v₀

  The ``entity + 0x144`` field is the per-jump target HEIGHT,
  NOT a direct velocity scalar.  It gets written from
  ``*(entity+0x68)`` for charged jumps or populated by
  ``FUN_00083F90`` for normal jumps — so patching the rare
  ``0x3F8CCCCD`` (= 1.1) imm32 literals around it doesn't
  actually affect the runtime height (they're immediately
  overwritten).  Meanwhile ``entity + 0x140`` (which we
  incorrectly targeted in v1) is the HORIZONTAL AIR-CONTROL
  speed — scaling it produces no visible jump-height change.

  **Correct target**: rewrite the ``FLD [0x001980A8]`` at VA
  ``0x89160`` to ``FLD [inject_va]`` where ``inject_va`` holds
  ``9.8 × jump_scale²``.  The SQRT then produces
  ``sqrt(2 × 9.8 × jump_scale² × h) = jump_scale ×
  sqrt(2 × 9.8 × h) = jump_scale × vanilla_v₀``.  Clean linear
  scaling on jump HEIGHT (since max height = v₀² / (2g), the
  effect on peak altitude is jump_scale² — doubling the slider
  quadruples jump height, which is the physically-correct
  relationship).

  Shared ``0x001980A8`` is the world gravity constant read by
  ``FUN_00085700`` every frame to drag velocity down.  We do
  NOT modify that constant — our patch rewrites only the one
  FLD in ``FUN_00089060`` (the jump initiator) to reference an
  independent constant.  The gravity slider continues to patch
  ``0x001980A8`` directly and that remains the global gravity
  for all falling objects; ``jump_scale`` decouples from it.

  Single patch site (6 bytes rewrite + 4-byte injected float);
  no trampoline, no shim code.

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

JUMP_SPEED_SCALE = ParametricPatch(
    name="jump_speed_scale",
    label="Player jump height",
    va=0,
    size=0,
    original=b"",
    default=1.0,
    slider_min=0.1,
    slider_max=5.0,
    slider_step=0.05,
    unit="x",
    encode=lambda v: struct.pack("<d", float(v)),
    decode=lambda b: struct.unpack("<d", b)[0],
)

AIR_CONTROL_SCALE = ParametricPatch(
    name="air_control_scale",
    label="Player air-control speed",
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

FLAP_HEIGHT_SCALE = ParametricPatch(
    name="flap_height_scale",
    label="Player wing-flap (double jump) height",
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

CLIMB_SPEED_SCALE = ParametricPatch(
    name="climb_speed_scale",
    label="Player climbing speed",
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

# -----------------------------------------------------------
# Rolling / sliding ground-state speed (v3 April 2026).
#
# The previous roll slider rewrote the FMUL at VA 0x849E4 (the
# WHITE-button 3× magnitude boost inside FUN_00084940) and
# additionally force-always-on'd bit 0x40 of the input flags.
# That approach had a subtle coupling bug: the boosted magnitude
# then propagated into FUN_00089480's airborne physics (via
# ``entity[+0x140] × magnitude``), so cranking roll_scale made
# mid-air horizontal travel MUCH faster, which users
# reasonably perceived as "gravity changed / longer air-time".
#
# v3 replaces that approach entirely.  ``FUN_00089A70`` is the
# true rolling/sliding GROUND-state physics function (reached
# via state machine cases 3 / 4 from FUN_0008CCC0).  Its
# rolling-velocity FMUL at VA 0x00089B76 reads the constant
# ``[0x001AAB68]`` (vanilla value: 2.0) — and that constant
# has EXACTLY ONE reader in the entire binary (the site we
# want to scale).  Single reader means we can patch the
# 4-byte float IN PLACE with zero shim overhead and zero
# collateral damage to other systems.
#
# Semantics under v3:
#   rolling_ground_velocity = dt × magnitude × (2.0 × roll_scale)
# Only the rolling/sliding ground state is affected.  Walking,
# airborne physics, swimming, jumping, and the WHITE button
# behaviour are left at vanilla.
_ROLL_CONST_VA = 0x001AAB68
_VANILLA_ROLL_SPEED = 2.0
_ROLL_CONST_VANILLA = struct.pack("<f", _VANILLA_ROLL_SPEED)

# Back-compat aliases — these symbols were public in the pre-v3
# roll approach.  Keep them pointing at the new target so any
# pinned external code that imports them keeps working.
_ROLL_SITE_VA = _ROLL_CONST_VA   # semantic shift: now the CONST,
                                  # not the FMUL instruction
_ROLL_SITE_VANILLA = _ROLL_CONST_VANILLA
_RUN_SITE_VA = _ROLL_CONST_VA
_RUN_SITE_VANILLA = _ROLL_CONST_VANILLA


# -----------------------------------------------------------
# Climbing-state speed (new April 2026).
#
# ``FUN_00087F80`` (climbing / hanging-ledge state) reads a
# climbing-speed baseline from the .rdata float at VA
# ``0x001980E4`` (value ``2.0``).  The constant has EXACTLY TWO
# readers, BOTH inside FUN_00087F80 (VAs 0x87FA7 and 0x88357 —
# the main climb-velocity FLD and a secondary climb-retarget
# FLD).  Patching the constant directly scales ALL climb
# motion uniformly with no collateral.
_CLIMB_CONST_VA = 0x001980E4
_VANILLA_CLIMB_SPEED = 2.0
_CLIMB_CONST_VANILLA = struct.pack("<f", _VANILLA_CLIMB_SPEED)

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

# Jump-velocity formula site: the 6-byte
# ``FLD dword [0x001980A8]`` at VA 0x00089160 inside FUN_00089060.
# This loads gravity (9.8) which the subsequent ``FMUL [ESI+0x144]``
# + ``FADD ST0, ST0`` + ``FSQRT`` combines into the initial jump
# velocity ``v₀ = sqrt(2 × 9.8 × height)``.  Rewriting this single
# FLD to reference an injected ``9.8 × jump_scale²`` makes the
# SQRT result scale by ``jump_scale`` without disturbing the
# shared gravity global that every falling object reads each
# frame.
_JUMP_SITE_VA = 0x00089160
_JUMP_SITE_VANILLA = bytes([
    0xD9, 0x05,
    0xA8, 0x80, 0x19, 0x00,    # FLD dword [0x001980A8]
])
_VANILLA_JUMP_GRAVITY = 9.8

# Air-control speed: the horizontal mid-air speed scalar at
# ``entity + 0x140``, stored by five ``MOV DWORD [reg+0x140],
# 0x41100000`` imm32 writes across the airborne-state init
# functions (main ground jump in FUN_00089060, plus four
# alternate entry points).  Vanilla imm32 is 9.0.  Consumed by
# FUN_00089480 per-frame as ``local_16c = entity[+0x140] ×
# magnitude`` — the per-frame horizontal steering velocity while
# airborne.  Higher values = more mid-air steering freedom /
# faster horizontal movement during jumps.  These 5 sites are
# the same sites the v1 jump patch mistakenly targeted — they
# don't affect jump HEIGHT (that comes from VA 0x89160's SQRT
# formula above), but they DO meaningfully affect airborne
# horizontal movement, which is why we keep them as a separate
# slider.
_AIR_CONTROL_SITE_VAS = [
    0x00084ED3,   # airborne init reachable from walk/ground state
    0x000856D4,   # early jump-entry branch
    0x000890EA,   # FUN_00089060 (main jump), `+0x68 == 0` branch
    0x00089126,   # FUN_00089060, non-zero +0x68 branch
    0x0008D322,   # alternate mid-air entry (state dispatch path)
]
_VANILLA_AIR_CONTROL = 9.0
_AIR_CONTROL_IMM32_VANILLA = struct.pack("<f", _VANILLA_AIR_CONTROL)

# Wing-flap (air-power double jump) vertical impulse: the
# 6-byte ``FADD dword [0x001A25C0]`` at VA 0x000896EA inside
# FUN_00089480 (airborne per-frame physics).  Adds 8.0 to the
# vertical velocity component when BOTH input-flag bit 0x04
# (flap button) AND bit 0x40 (roll/air-boost flag, which the
# apply_player_speed force-always-on patch already enables) are
# set — giving the player an additional vertical impulse when
# they trigger the Air-power double jump / wing flap.  Shared
# constant ``0x001A25C0`` has 5 readers total; only this one
# player site is player-movement-related, so rewriting the FADD
# target to our injected ``8.0 × flap_scale`` float leaves the
# other callers untouched.
_FLAP_SITE_VA = 0x000896EA
_FLAP_SITE_VANILLA = bytes([
    0xD8, 0x05,
    0xC0, 0x25, 0x1A, 0x00,    # FADD dword [0x001A25C0]
])
_VANILLA_FLAP_IMPULSE = 8.0

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
    jump_scale: float | None = None,
    air_control_scale: float | None = None,
    flap_scale: float | None = None,
    climb_scale: float | None = None,
    # Back-compat alias: callers that still pass run_scale get
    # transparently routed to roll_scale (the new name).
    run_scale: float | None = None,
    **_ignored,
) -> None:
    """Apply the XBE-side portion of the player physics pack.

    All eight adjustments operate on ``default.xbe`` directly.
    Speed sliders no longer touch ``config.xbr`` — the values
    there turned out to be dead data (see module docstring).
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

    j = 1.0 if jump_scale is None else float(jump_scale)
    if j != 1.0:
        apply_jump_speed(xbe_data, jump_scale=j)

    a = 1.0 if air_control_scale is None else float(air_control_scale)
    if a != 1.0:
        apply_air_control_speed(xbe_data, air_control_scale=a)

    f = 1.0 if flap_scale is None else float(flap_scale)
    if f != 1.0:
        apply_flap_height(xbe_data, flap_scale=f)

    c = 1.0 if climb_scale is None else float(climb_scale)
    if c != 1.0:
        apply_climb_speed(xbe_data, climb_scale=c)


def apply_player_speed(
    xbe_data: bytearray,
    *,
    walk_scale: float = 1.0,
    roll_scale: float = 1.0,
    # Back-compat kwarg alias — old code may still pass run_scale.
    run_scale: float | None = None,
) -> bool:
    """Patch ``default.xbe`` so the player walks / rolls at custom speeds.

    v3 (April 2026) — ``walk_scale`` and ``roll_scale`` are
    INDEPENDENT, UNCOUPLED multipliers on distinct physics systems:

    * ``walk_scale`` scales the walking-state velocity via an
      injected FLD constant (replaces ``CritterData.run_speed`` =
      7.0 for the player only).
    * ``roll_scale`` scales the rolling/sliding GROUND-state
      velocity by overwriting the single-reader constant at VA
      ``0x001AAB68`` (vanilla 2.0) used by ``FUN_00089A70``.  This
      targets ONLY the ground-roll physics — walking, airborne
      flight, jumping, and swimming are completely unaffected.
      (Prior versions used an FMUL rewrite + force-always-on in
      ``FUN_00084f90``, which coupled roll_scale to airborne
      horizontal speed via the shared ``magnitude`` variable.
      That coupling is what made roll_scale look like "gravity
      got weaker" to users — it's gone in v3.)

    Returns True when the patch was applied, False if both scales
    are at the default of 1.0 (no-op) or if the patch sites have
    drifted from vanilla — in the drift case a warning is printed
    and the buffer is left untouched.
    """
    # Back-compat: accept old run_scale kwarg.  Explicit roll_scale
    # wins if the caller set both.
    if run_scale is not None and roll_scale == 1.0:
        roll_scale = run_scale

    if walk_scale == 1.0 and roll_scale == 1.0:
        return False

    walk_off = va_to_file(_WALK_SITE_VA)
    roll_off = va_to_file(_ROLL_CONST_VA)

    # --- Safety: ensure vanilla bytes at the sites we plan to touch.
    if walk_scale != 1.0 and (
        bytes(xbe_data[walk_off:walk_off + 6]) != _WALK_SITE_VANILLA
    ):
        print(f"  WARNING: player_speed — walk site at VA "
              f"0x{_WALK_SITE_VA:X} already patched or drifted, "
              f"skipping (got "
              f"{bytes(xbe_data[walk_off:walk_off + 6]).hex()})")
        return False
    if roll_scale != 1.0 and (
        bytes(xbe_data[roll_off:roll_off + 4]) != _ROLL_CONST_VANILLA
    ):
        print(f"  WARNING: player_speed — roll constant at VA "
              f"0x{_ROLL_CONST_VA:X} already patched or drifted, "
              f"skipping (got "
              f"{bytes(xbe_data[roll_off:roll_off + 4]).hex()}, "
              f"expected {_ROLL_CONST_VANILLA.hex()})")
        return False

    walk_va = None
    roll_new_value: float | None = None

    if walk_scale != 1.0:
        # Late import to avoid the circular dep at module-load.
        from azurik_mod.patching.apply import _carve_shim_landing

        safe_walk_scale = max(_WALK_SCALE_MIN, float(walk_scale))
        inject_base = _VANILLA_PLAYER_BASE_SPEED * safe_walk_scale
        walk_value_bytes = struct.pack("<f", inject_base)
        _, walk_va = _carve_shim_landing(xbe_data, walk_value_bytes)
        # FLD dword [abs walk_va]   encoded as D9 05 <va>
        xbe_data[walk_off:walk_off + 6] = (
            b"\xD9\x05" + struct.pack("<I", walk_va))
        print(f"  Player walk speed: {walk_scale:.3f}x vanilla  "
              f"(injected base = {inject_base:.3f}, "
              f"VA 0x{walk_va:X})")

    if roll_scale != 1.0:
        # Direct constant overwrite — no shim, no trampoline.
        # VA 0x001AAB68 is a single-reader .rdata float, so we
        # can swap vanilla 2.0 for 2.0 × roll_scale in-place and
        # the change takes effect on every frame of ground-roll
        # physics without any instruction rewriting.
        roll_new_value = _VANILLA_ROLL_SPEED * float(roll_scale)
        xbe_data[roll_off:roll_off + 4] = (
            struct.pack("<f", roll_new_value))
        print(f"  Player roll (ground) speed: {roll_scale:.3f}x vanilla  "
              f"(constant {_VANILLA_ROLL_SPEED:.3f} -> "
              f"{roll_new_value:.3f} at VA 0x{_ROLL_CONST_VA:X})")

    return True


def apply_climb_speed(
    xbe_data: bytearray,
    *,
    climb_scale: float = 1.0,
) -> bool:
    """Patch ``default.xbe`` so the player climbs at a custom speed.

    Scales the climbing-velocity baseline constant at VA
    ``0x001980E4`` (vanilla 2.0).  The constant has EXACTLY TWO
    readers, both in ``FUN_00087F80`` (the climbing/hanging-ledge
    state), so a direct 4-byte float overwrite affects ONLY
    climbing motion — every other physics state is untouched.

    Returns True when the patch was applied, False if ``climb_scale``
    is 1.0 (no-op) or if the constant has drifted from vanilla.
    """
    if climb_scale == 1.0:
        return False

    off = va_to_file(_CLIMB_CONST_VA)
    current = bytes(xbe_data[off:off + 4])
    if current != _CLIMB_CONST_VANILLA:
        print(f"  WARNING: climb_speed — constant at VA "
              f"0x{_CLIMB_CONST_VA:X} already patched or drifted, "
              f"skipping (got {current.hex()}, "
              f"expected {_CLIMB_CONST_VANILLA.hex()})")
        return False

    new_value = _VANILLA_CLIMB_SPEED * float(climb_scale)
    xbe_data[off:off + 4] = struct.pack("<f", new_value)
    print(f"  Player climbing speed: {climb_scale:.3f}x vanilla  "
          f"(constant {_VANILLA_CLIMB_SPEED:.3f} -> {new_value:.3f} "
          f"at VA 0x{_CLIMB_CONST_VA:X})")
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


def apply_jump_speed(
    xbe_data: bytearray,
    *,
    jump_scale: float = 1.0,
) -> bool:
    """Patch ``default.xbe`` so the player jumps at a custom height.

    Rewrites the 6-byte ``FLD [0x001980A8]`` at VA ``0x00089160``
    (inside ``FUN_00089060``'s ``v₀ = sqrt(2gh)`` formula) to
    ``FLD [inject_va]``, where ``inject_va`` holds ``9.8 ×
    jump_scale²``.  The SQRT then produces
    ``jump_scale × sqrt(2 × 9.8 × h)`` — linear scaling on
    initial vertical velocity and quadratic scaling on peak
    jump height (``max_h = v₀² / (2g)``).

    The shared gravity constant at ``0x001980A8`` is NOT touched;
    the ``gravity`` slider continues to own it.  Only the ONE
    FLD in the jump initialiser reads from our injected constant,
    keeping jump and gravity physics independent.

    Returns True when the patch was applied, False on no-op or
    when the site has drifted from vanilla (warning printed;
    buffer left untouched in the drift case).
    """
    if jump_scale == 1.0:
        return False

    from azurik_mod.patching.apply import _carve_shim_landing

    off = va_to_file(_JUMP_SITE_VA)
    current = bytes(xbe_data[off:off + 6])
    if current != _JUMP_SITE_VANILLA:
        print(f"  WARNING: player_speed — jump site at VA "
              f"0x{_JUMP_SITE_VA:X} drifted from vanilla "
              f"(got {current.hex()}); skipping.  If the XBE is "
              f"already patched, re-apply on a fresh copy.")
        return False

    inject_value = _VANILLA_JUMP_GRAVITY * float(jump_scale) ** 2
    inject_bytes = struct.pack("<f", inject_value)
    _, inject_va = _carve_shim_landing(xbe_data, inject_bytes)

    # FLD dword [abs inject_va]   encoded as D9 05 <va>
    xbe_data[off:off + 6] = b"\xD9\x05" + struct.pack("<I", inject_va)

    print(f"  Player jump height: {jump_scale:.3f}x vanilla  "
          f"(injected gravity scalar = {inject_value:.3f} at VA "
          f"0x{inject_va:X}, produces v0 = {jump_scale:.3f} × "
          f"vanilla_v0)")
    return True


def apply_air_control_speed(
    xbe_data: bytearray,
    *,
    air_control_scale: float = 1.0,
) -> bool:
    """Patch ``default.xbe`` to scale the airborne horizontal-
    control speed.

    The player entity's ``+0x140`` field is the per-frame
    mid-air horizontal steering velocity (FUN_00089480 reads it
    as ``local_16c = entity[+0x140] × magnitude`` every airborne
    frame).  Vanilla stores ``9.0`` into this field on jump
    entry via five ``MOV DWORD [reg+0x140], 0x41100000`` imm32
    writes — we rewrite each imm32 to ``9.0 × air_control_scale``.

    Does NOT affect jump HEIGHT (that's computed from
    ``sqrt(2 × 9.8 × entity[+0x144])`` at VA 0x89160, targeted
    by ``apply_jump_speed``).  Only affects horizontal motion
    while in the air.

    Returns True on apply, False on no-op or when ALL sites
    drifted (a warning is printed per-drifted site).
    """
    if air_control_scale == 1.0:
        return False

    new_imm = struct.pack("<f",
                          _VANILLA_AIR_CONTROL * float(air_control_scale))
    patched = 0
    for site_va in _AIR_CONTROL_SITE_VAS:
        off = va_to_file(site_va)
        current = bytes(xbe_data[off:off + 4])
        if current != _AIR_CONTROL_IMM32_VANILLA:
            print(f"  WARNING: air_control — imm32 at VA "
                  f"0x{site_va:X} drifted (got {current.hex()}); "
                  f"skipping.")
            continue
        xbe_data[off:off + 4] = new_imm
        patched += 1

    if patched == 0:
        return False

    print(f"  Player air-control speed: "
          f"{air_control_scale:.3f}x vanilla  "
          f"({patched}/{len(_AIR_CONTROL_SITE_VAS)} sites "
          f"rewritten, imm32 = "
          f"{_VANILLA_AIR_CONTROL * air_control_scale:.3f})")
    return True


def apply_flap_height(
    xbe_data: bytearray,
    *,
    flap_scale: float = 1.0,
) -> bool:
    """Patch ``default.xbe`` to scale the wing-flap (Air-power
    double-jump) vertical impulse.

    ``FUN_00089480`` (airborne per-frame physics) adds ``8.0``
    to the player's vertical velocity when BOTH input-flag bit
    0x04 (the flap-button / second-jump trigger) AND bit 0x40
    (the roll/air-boost flag that ``apply_player_speed``'s
    force-always-on already enables) are set.  The FADD lives
    at VA ``0x000896EA`` as ``FADD dword [0x001A25C0]`` — we
    rewrite it to ``FADD dword [inject_va]`` where ``inject_va``
    holds ``8.0 × flap_scale`` in the shim landing.  The shared
    ``8.0`` at VA ``0x001A25C0`` has 5 total readers, most
    unrelated to player movement, so only this one player site
    is modified.

    At ``flap_scale = 2.0``, the wing flap adds ``16.0`` to
    velocity.z per press — roughly doubling the double-jump
    height gained per flap.

    Returns True on apply, False on no-op or site drift.
    """
    if flap_scale == 1.0:
        return False

    from azurik_mod.patching.apply import _carve_shim_landing

    off = va_to_file(_FLAP_SITE_VA)
    current = bytes(xbe_data[off:off + 6])
    if current != _FLAP_SITE_VANILLA:
        print(f"  WARNING: flap_height — site at VA "
              f"0x{_FLAP_SITE_VA:X} drifted (got "
              f"{current.hex()}); skipping.")
        return False

    inject_value = _VANILLA_FLAP_IMPULSE * float(flap_scale)
    inject_bytes = struct.pack("<f", inject_value)
    _, inject_va = _carve_shim_landing(xbe_data, inject_bytes)

    # FADD dword [abs inject_va]   encoded as D8 05 <va>
    xbe_data[off:off + 6] = b"\xD8\x05" + struct.pack("<I", inject_va)

    print(f"  Player wing-flap impulse: {flap_scale:.3f}x vanilla  "
          f"(injected impulse = {inject_value:.3f} at VA "
          f"0x{inject_va:X})")
    return True


def _player_speed_dynamic_whitelist(
    xbe: bytes,
) -> list[tuple[int, int]]:
    """Return the byte ranges touched by the player-physics pack.

    ``verify-patches --strict`` calls this during its whitelist-diff
    pass so instruction rewrites AND injected/overwritten float
    constants don't register as unexpected byte flips.

    Always returns the static rewrite ranges; adds the dynamic
    4-byte float ranges only when the apply is detected.  Invoked
    on vanilla XBEs too (the pack may not have been applied) so
    must never raise on unrecognised bytes.
    """
    from azurik_mod.patching.xbe import parse_xbe_sections, va_to_file

    try:
        walk_off = va_to_file(_WALK_SITE_VA)
        swim_off = va_to_file(_SWIM_SITE_VA)
    except Exception:  # noqa: BLE001
        return []

    ranges: list[tuple[int, int]] = [
        # Instruction rewrites (6 bytes each).
        (walk_off, walk_off + 6),
        (swim_off, swim_off + 6),
    ]
    try:
        jump_off = va_to_file(_JUMP_SITE_VA)
        ranges.append((jump_off, jump_off + 6))
    except Exception:  # noqa: BLE001
        pass
    try:
        # Roll now targets a 4-byte constant at VA 0x001AAB68.
        ranges.append((va_to_file(_ROLL_CONST_VA),
                       va_to_file(_ROLL_CONST_VA) + 4))
    except Exception:  # noqa: BLE001
        pass
    try:
        # Climb targets a 4-byte constant at VA 0x001980E4.
        ranges.append((va_to_file(_CLIMB_CONST_VA),
                       va_to_file(_CLIMB_CONST_VA) + 4))
    except Exception:  # noqa: BLE001
        pass
    # Air-control: 5 imm32 sites (4 bytes each).
    for site_va in _AIR_CONTROL_SITE_VAS:
        try:
            ac_off = va_to_file(site_va)
            ranges.append((ac_off, ac_off + 4))
        except Exception:  # noqa: BLE001
            continue
    # Flap: 6-byte FADD rewrite site.
    try:
        fl_off = va_to_file(_FLAP_SITE_VA)
        ranges.append((fl_off, fl_off + 6))
    except Exception:  # noqa: BLE001
        pass

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

    follow_sites: list[tuple[int, bytes]] = [
        (walk_off, b"\xD9\x05"),   # FLD  [abs32] (walk base)
        (swim_off, b"\xD8\x0D"),   # FMUL [abs32] (swim mult)
    ]
    try:
        follow_sites.append((va_to_file(_JUMP_SITE_VA), b"\xD9\x05"))
    except Exception:  # noqa: BLE001
        pass
    try:
        # Flap site uses FADD [abs32] (opcode D8 05) — same prefix
        # as FLD/FMUL in terms of addressing form; follow the
        # abs32 to whitelist the injected float.
        follow_sites.append((va_to_file(_FLAP_SITE_VA), b"\xD8\x05"))
    except Exception:  # noqa: BLE001
        pass

    for site_off, prefix in follow_sites:
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
    JUMP_SPEED_SCALE,
    AIR_CONTROL_SCALE,
    FLAP_HEIGHT_SCALE,
    CLIMB_SPEED_SCALE,
]
"""Registered Patches-page sites.  Gravity, roll, and climb
sliders write to isolated .rdata floats directly; walk, swim,
and jump use shim-landed FLD/FMUL rewrites; air-control uses
imm32 overwrites at five call sites; flap uses a single FADD
rewrite."""


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
    jump_speed_scale: float | None = None,
    air_control_scale: float | None = None,
    flap_height_scale: float | None = None,
    climb_speed_scale: float | None = None,
    # Back-compat: the old kwarg spellings.  New callers should use
    # the *_speed_scale forms, but CLI / serialized configs that
    # predate the rename still work.
    run_speed_scale: float | None = None,
    walk_scale: float | None = None,
    roll_scale: float | None = None,
    run_scale: float | None = None,
    swim_scale: float | None = None,
    jump_scale: float | None = None,
    flap_scale: float | None = None,
    climb_scale: float | None = None,
    **_extra,
) -> None:
    """Unified-dispatcher hook — forwards slider kwargs to the full
    ``apply_player_physics`` implementation.

    ``params`` on the dispatcher side is a dict keyed by the
    ParametricPatch names (``walk_speed_scale``,
    ``roll_speed_scale``, ``swim_speed_scale``,
    ``jump_speed_scale``, ``air_control_scale``,
    ``flap_height_scale``).  We also accept the short aliases and
    the legacy ``run_*`` names for pre-v2 callers.
    """
    walk = walk_speed_scale if walk_speed_scale is not None else walk_scale
    roll = (
        roll_speed_scale if roll_speed_scale is not None
        else roll_scale if roll_scale is not None
        else run_speed_scale if run_speed_scale is not None
        else run_scale
    )
    swim = swim_speed_scale if swim_speed_scale is not None else swim_scale
    jump = jump_speed_scale if jump_speed_scale is not None else jump_scale
    flap = (flap_height_scale if flap_height_scale is not None
            else flap_scale)
    climb = (climb_speed_scale if climb_speed_scale is not None
             else climb_scale)
    apply_player_physics(
        xbe_data,
        gravity=gravity,
        walk_scale=walk,
        roll_scale=roll,
        swim_scale=swim,
        jump_scale=jump,
        air_control_scale=air_control_scale,
        flap_scale=flap,
        climb_scale=climb,
    )


FEATURE = register_feature(Feature(
    name="player_physics",
    description=(
        "Scales world gravity and every player movement / speed "
        "parameter we've RE'd: walking, rolling (ground state), "
        "climbing, swimming, jumping, horizontal air-control "
        "speed, and wing-flap (Air-power double-jump) impulse.  "
        "Gravity is global; the rest are player-only."
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
    "AIR_CONTROL_SCALE",
    "CLIMB_SPEED_SCALE",
    "FLAP_HEIGHT_SCALE",
    "GRAVITY_BASELINE",
    "GRAVITY_PATCH",
    "JUMP_SPEED_SCALE",
    "PLAYER_PHYSICS_SITES",
    "ROLL_SPEED_SCALE",
    "RUN_SPEED_SCALE",
    "SWIM_SPEED_SCALE",
    "WALK_SPEED_SCALE",
    "apply_air_control_speed",
    "apply_climb_speed",
    "apply_flap_height",
    "apply_jump_speed",
    "apply_player_physics",
    "apply_player_speed",
    "apply_swim_speed",
]
