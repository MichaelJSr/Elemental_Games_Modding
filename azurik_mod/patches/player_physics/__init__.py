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
    description=(
        "Scales v0 for the FIRST wing flap after a jump.  Acts "
        "quadratically on the gravity input to sqrt(2g*h), so "
        "2.0 makes the flap reach ~2x vanilla height, 4.0 "
        "reaches ~4x, etc.  Subsequent flaps are controlled "
        "separately by the two 'Wing-flap (2nd+ flaps)' sliders."
    ),
)

FLAP_BELOW_PEAK_SCALE = ParametricPatch(
    name="flap_below_peak_scale",
    label="Wing-flap height (2nd+ flaps, far below peak)",
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
        "Scales the vanilla 0.5x halving factor that kicks in when "
        "the player has fallen more than 6 m below their jump peak. "
        "1.0 = vanilla (halved v0).  2.0 = un-halved (same v0 as "
        "the 1st flap).  4.0 = 2x the 1st-flap v0.  Does NOT affect "
        "flaps taken within 6 m of the peak — use "
        "'Wing-flap height (2nd+ flaps, near peak)' for those."
    ),
)

# Back-compat alias — the old name pre-rename (late April 2026).
FLAP_SUBSEQUENT_SCALE = FLAP_BELOW_PEAK_SCALE

FLAP_AT_PEAK_SCALE = ParametricPatch(
    name="flap_at_peak_scale",
    label="Wing-flap height (2nd+ flaps, near peak)",
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
        "Binary toggle — any value other than 1.0 enables the fix. "
        "Vanilla caps subsequent-flap v0 at sqrt(2g x remaining_height) "
        "where remaining shrinks as the player rises, so flaps near "
        "the peak feel weak.  The fix rewrites a single FLD inside "
        "wing_flap so the cap collapses to 'flap_height', giving "
        "full first-flap v0 every time.  Slider value itself has "
        "no effect on magnitude; it's tested only against 1.0."
    ),
)

SLOPE_SLIDE_SPEED_SCALE = ParametricPatch(
    name="slope_slide_speed_scale",
    label="Slope-slide speed (steep-terrain auto-slide)",
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
# Rolling speed — v4 (April 2026 late).
#
# Iteration history:
#
#   v1: FMUL rewrite at VA 0x849E4 (the WHITE-button 3×
#       magnitude boost inside FUN_00084940).  Worked for
#       rolling but required users to actually press WHITE;
#       many xemu input configs don't route WHITE so the
#       slider looked like it did nothing.
#
#   v2: v1 + force-always-on bit 0x40.  Made the slider
#       observable without WHITE held, but the boosted
#       ``magnitude`` (``entity[+0x124]``) leaked into
#       FUN_00089480's airborne physics (``horizontal_vel
#       = entity[+0x140] × magnitude``), so jumps covered
#       3-5× more ground — users described this as "gravity
#       got weaker".
#
#   v3: patched the single-reader constant at VA 0x001AAB68
#       (vanilla 2.0) used by FUN_00089A70.  Isolated — no
#       coupling.  BUT: FUN_00089A70 is the **slope-slide**
#       state (state 3/4 in FUN_0008CCC0), triggered when
#       the player lands on a slope >45° from upright.  It
#       is NOT the roll animation / WHITE-button dash the
#       user wanted to scale.  Result: slider did nothing
#       observable during player-initiated rolling.
#
# v4 reverts to the v1 target (FMUL rewrite at VA 0x849E4)
# BUT WITHOUT the force-always-on.  Semantics:
#
#   - Player presses WHITE or BACK → bit 0x40 of input flags
#     sets → FMUL scales magnitude by the injected value
#     (vanilla 3.0, slider-scaled to ``3.0 × roll_scale``).
#     Walking-speed WITH WHITE held = ``7 × 3 × roll_scale
#     × stick``.
#   - Player airborne WITH WHITE held → same magnitude scale
#     leaks into horizontal air control (matches vanilla —
#     vanilla ALSO boosts air control 3× when WHITE is held
#     mid-air).  Slider scales vanilla coupling proportionally.
#   - Player airborne WITHOUT WHITE held → vanilla (magnitude
#     × 1.0).  No effect.  The "gravity weakened" v2 bug is
#     gone because bit 0x40 is no longer force-set.
#
# Users who haven't routed WHITE/BACK in their xemu config
# will see no effect — but that's consistent with vanilla
# roll behaviour and is the correct trade-off: matching the
# button-gated coupling vs fabricating a separate
# "all-the-time" multiplier.
_ROLL_FMUL_VA = 0x000849E4
_ROLL_FMUL_VANILLA = bytes([
    0xD8, 0x0D,                      # FMUL dword [abs32]
    0xBC, 0x25, 0x1A, 0x00,           # [0x001A25BC] (shared 3.0)
])
_VANILLA_ROLL_MULT = 3.0             # the FMUL constant

# Back-compat aliases — pre-v3 public symbols kept alive as
# aliases on the new target (the FMUL instruction VA, 6 bytes).
_ROLL_SITE_VA = _ROLL_FMUL_VA
_ROLL_SITE_VANILLA = _ROLL_FMUL_VANILLA
_RUN_SITE_VA = _ROLL_FMUL_VA
_RUN_SITE_VANILLA = _ROLL_FMUL_VANILLA

# Keep the v3 CONST symbols so inspect-physics can still
# report the slope-slide constant (unchanged in v4 — we just
# stopped treating it as the "roll" patch target).
_ROLL_CONST_VA = 0x001AAB68
_VANILLA_ROLL_SPEED = 2.0
_ROLL_CONST_VANILLA = struct.pack("<f", _VANILLA_ROLL_SPEED)


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

# April 2026 v2 — the 5 sites above only run during specific
# jump-entry paths.  The DOMINANT air-control setter during
# normal gameplay is inside FUN_00083F90 (the per-frame
# airborne re-initialiser called from FUN_00089060's default
# path and FUN_00089300's wing-flap path).  That function
# writes TWO different air-control values based on the active
# air-power level:
#
#   VA 0x00083FAA   MOV [ECX], 0x41400000   ; 12.0 (air_power 1-3)
#   VA 0x00083FCC   MOV [ECX], 0x41100000   ; 9.0  (no air power / level 4)
#
# Both imm32s live at offset+2 of the 6-byte instruction.
# Each is a MOV DWORD [ECX], imm32 (opcode ``C7 01 <imm32>``).
# We scale the imm32 value read from the site by
# ``air_control_scale`` and write it back so BOTH air-power
# and no-air-power paths stay in sync with the slider.
_AIR_CONTROL_SECONDARY_SITE_VAS = [
    0x00083FAC,   # imm32 of MOV [ECX], 0x41400000 = 12.0
    0x00083FCE,   # imm32 of MOV [ECX], 0x41100000 = 9.0
]

# Wing-flap (air-power double jump) vertical impulse — v2.
#
# Pre-v2 we targeted VA 0x000896EA (``FADD [0x001A25C0]`` inside
# FUN_00089480 = airborne per-frame physics).  User testing
# confirmed that patch landed correctly but had no observable
# gameplay effect — the FADD there guards a different airborne
# maneuver (WHITE + JUMP mid-air dive boost), NOT the Air-power
# wing flap.  The REAL wing flap is computed inside
# ``FUN_00089300`` at VA 0x000893AE:
#
#    000893AE   D9 05 A8 80 19 00   FLD  [0x001980A8]    ; gravity
#    000893B4   D8 C9              FMUL ST1              ; × flap_height
#    000893B6   DC C0              FADD ST, ST           ; × 2
#    000893B8   D9 FA              FSQRT                 ; sqrt(2×g×h)
#
# Same ``v0 = sqrt(2gh)`` form as the initial jump at VA 0x89160,
# so we apply the same trick: rewrite the FLD to load an
# injected ``9.8 × flap_scale²`` constant so the sqrt scales
# v0 linearly by ``flap_scale``.
#
# Unlike 0x896EA (no-op when WHITE isn't wired), 0x893AE is
# the ONLY vertical-velocity source during wing flaps, so
# users will see the slider take effect every time they
# double-jump.
_FLAP_SITE_VA = 0x000893AE
_FLAP_SITE_VANILLA = bytes([
    0xD9, 0x05,
    0xA8, 0x80, 0x19, 0x00,    # FLD dword [0x001980A8] (gravity)
])
_VANILLA_FLAP_IMPULSE = 8.0   # legacy constant name; retained for
                              # test back-compat.  The actual
                              # scaling factor used by the math
                              # below is ``_VANILLA_JUMP_GRAVITY``.

# -----------------------------------------------------------
# Subsequent-flap height (new April 2026 v2).
#
# After the first wing flap, ``FUN_00089300`` checks the
# remaining-height-to-peak (``peak_z + flap_height -
# current_z``) against a 6.0-metre threshold at VA 0x893C0
# (``FCOMP [0x001A25B8] = 6.0``).  If the player has fallen
# more than 6m below their peak (i.e., is continuing to flap
# DOWNWARD), the subsequent flap v0 is HALVED by
# ``FMUL [0x001A2510] = 0.5`` at VA 0x893DD.  That's why
# user reports the first flap gives full boost but subsequent
# flaps feel weak.
#
# We rewrite the 0x893DD FMUL to reference an injected
# ``0.5 × flap_subsequent_scale`` so users can tune it:
#
#   - 1.0 (default): vanilla halving, subsequent flaps at 50% v0.
#   - 2.0:           subsequent flaps at 100% v0 (no halving).
#   - 4.0:           subsequent flaps at 200% v0 (boosted).
#
# The shared 0x001A2510 = 0.5 constant has 263+ readers in
# the binary (generic "half"), so we rewrite only the FMUL
# instruction target — not the constant.
_FLAP_SUBSEQUENT_SITE_VA = 0x000893DD
_FLAP_SUBSEQUENT_SITE_VANILLA = bytes([
    0xD8, 0x0D,
    0x10, 0x25, 0x1A, 0x00,    # FMUL dword [0x001A2510] (0.5)
])
_VANILLA_FLAP_SUBSEQUENT = 0.5

# -----------------------------------------------------------
# Wing-flap AT-peak cap (late April 2026 user-reported fix, v2).
#
# Inside ``wing_flap`` the per-flap v0 is derived from the cap
#
#     fVar1 = peak_z + flap_height - current_z
#     if fVar1 <= 0 then fVar1 = 0
#     fVar2 = min(fVar1, flap_height)
#     v0 = sqrt(2 * gravity * fVar2)
#
# When the player is AT peak (current_z ≈ peak_z), vanilla gives
# fVar2 ≈ flap_height, so v0 is full.  BUT between flaps the
# sprite drifts above ``peak_z`` by some tiny delta before the
# game updates peak_z itself.  So the 2nd+ flap's
# ``remaining = peak_z + flap_height - current_z`` shrinks to
# ``flap_height - delta`` (weak v0).  User observation:
# "subsequent flaps at peak are weak / have no upward velocity."
#
# v1 fix (3-byte NOP of FSUB at VA 0x89381) worked for v0 but
# inadvertently forced ``fVar1`` to be huge, which then tripped
# the ``fVar1 > 6m`` check at VA 0x893C0 — routing the control
# flow through the "below peak" halving path that ALSO drains
# 100 fuel via ``consume_fuel`` at VA 0x893D4.  Result in
# testing: upward velocity felt absent (halved) and fuel
# drained to zero within a couple of flaps (leaving subsequent
# flaps refused).  Reverted.
#
# v2 fix: patch the ``FLD ST1`` at VA 0x8939F (2 bytes,
# ``D9 C1``) to ``FLD ST0`` (``D9 C0``).  This duplicates the
# just-loaded ``flap_height`` instead of ``fVar1``, so the
# subsequent ``FCOMP ST1`` compares ``fh`` with ``fh`` (equal)
# and the JP at VA 0x893A8 is always taken — skipping the min
# selection.  ``fVar2 = flap_height`` every flap, giving full
# v0, while ``fVar1`` is preserved UNCHANGED for the below-6m
# halving check at VA 0x893C0.  Stack depth also matches vanilla
# at the JP-taken branch (verified via trace), so no FP-stack
# leak propagates downstream.
#
# Semantics:
#   flap_at_peak_scale == 1.0 → vanilla (weak subsequent at peak)
#   flap_at_peak_scale != 1.0 → full-cap fVar2 = flap_height
# (binary toggle; slider value isn't used for computation, only
# tested against 1.0).
_FLAP_PEAK_CAP_SITE_VA = 0x0008939F
_FLAP_PEAK_CAP_SITE_VANILLA = bytes([
    0xD9, 0xC1,                # FLD ST(1)  — dup fVar1
])
_FLAP_PEAK_CAP_PATCH = bytes([
    0xD9, 0xC0,                # FLD ST(0)  — dup fh (just loaded)
])

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
    flap_subsequent_scale: float | None = None,
    flap_below_peak_scale: float | None = None,
    flap_at_peak_scale: float | None = None,
    climb_scale: float | None = None,
    slope_slide_scale: float | None = None,
    # Back-compat alias: callers that still pass run_scale get
    # transparently routed to roll_scale (the new name).
    run_scale: float | None = None,
    **_ignored,
) -> None:
    """Apply the XBE-side portion of the player physics pack.

    All nine adjustments operate on ``default.xbe`` directly.
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

    # flap_below_peak_scale is the new name; flap_subsequent_scale
    # is the old alias (late April 2026 rename).  Prefer the new
    # name when both provided.
    fbelow = flap_below_peak_scale
    if fbelow is None:
        fbelow = flap_subsequent_scale
    fsub = 1.0 if fbelow is None else float(fbelow)
    if fsub != 1.0:
        apply_flap_subsequent(xbe_data, subsequent_scale=fsub)

    fatp = (1.0 if flap_at_peak_scale is None
            else float(flap_at_peak_scale))
    if fatp != 1.0:
        apply_flap_at_peak(xbe_data, at_peak_scale=fatp)

    c = 1.0 if climb_scale is None else float(climb_scale)
    if c != 1.0:
        apply_climb_speed(xbe_data, climb_scale=c)

    ss = (1.0 if slope_slide_scale is None
          else float(slope_slide_scale))
    if ss != 1.0:
        apply_slope_slide_speed(xbe_data, slope_slide_scale=ss)


def apply_player_speed(
    xbe_data: bytearray,
    *,
    walk_scale: float = 1.0,
    roll_scale: float = 1.0,
    # Back-compat kwarg alias — old code may still pass run_scale.
    run_scale: float | None = None,
) -> bool:
    """Patch ``default.xbe`` so the player walks / rolls at custom speeds.

    v4 (April 2026 late) — ``walk_scale`` is unchanged.  ``roll_scale``
    reverts to the v1 target (FMUL rewrite at VA 0x849E4) because
    v3's VA 0x1AAB68 target turned out to be the SLOPE-SLIDE state
    (FUN_00089A70, reached when landing on steep terrain), NOT the
    player-initiated roll / WHITE-button dash the user wanted to
    scale.  See module docstring for the full iteration history.

    * ``walk_scale`` — walking-state velocity multiplier (FLD
      rewrite at VA 0x85F62).
    * ``roll_scale`` — scales the 3.0 FMUL at VA 0x849E4 inside
      FUN_00084940 that triggers when WHITE or BACK is held.
      Rewrites ``FMUL [0x001A25BC]`` (shared, 45 readers) to
      ``FMUL [abs <inject_va>]`` where inject_va holds
      ``3.0 × roll_scale``.  Only the player's FMUL site is
      redirected; the shared constant stays at 3.0 for the other
      44 readers.

      Side effect (matches vanilla): when WHITE is held mid-air,
      magnitude × 3.0 × roll_scale also scales horizontal air
      control (``entity[+0x140] × magnitude`` inside
      FUN_00089480).  This is exactly the same coupling vanilla
      has; we just scale the multiplier proportionally.

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
    roll_off = va_to_file(_ROLL_FMUL_VA)

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
        bytes(xbe_data[roll_off:roll_off + 6]) != _ROLL_FMUL_VANILLA
    ):
        print(f"  WARNING: player_speed — roll FMUL at VA "
              f"0x{_ROLL_FMUL_VA:X} already patched or drifted, "
              f"skipping (got "
              f"{bytes(xbe_data[roll_off:roll_off + 6]).hex()})")
        return False

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
        # Rewrite the FMUL at VA 0x849E4 to reference an injected
        # ``3.0 × roll_scale`` constant.  The shared 3.0 at
        # 0x001A25BC is untouched (45 other readers depend on it).
        from azurik_mod.patching.apply import _carve_shim_landing

        inject_value = _VANILLA_ROLL_MULT * float(roll_scale)
        roll_value_bytes = struct.pack("<f", inject_value)
        _, roll_va = _carve_shim_landing(xbe_data, roll_value_bytes)
        # FMUL dword [abs roll_va]   encoded as D8 0D <va>
        xbe_data[roll_off:roll_off + 6] = (
            b"\xD8\x0D" + struct.pack("<I", roll_va))
        print(f"  Player roll (WHITE/BACK boost): {roll_scale:.3f}x "
              f"vanilla  (injected mult = {inject_value:.3f} at "
              f"VA 0x{roll_va:X})")

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
    """Patch ``default.xbe`` to scale airborne horizontal-control speed.

    v2 (April 2026) — now patches 7 sites total:

    1. Five ``MOV DWORD [reg+0x140], 0x41100000`` imm32 writes
       (VAs 0x84ED3, 0x856D4, 0x890EA, 0x89126, 0x8D322) used
       during specific jump-entry paths.
    2. Two dominant imm32s inside ``FUN_00083F90`` (the per-
       frame airborne re-initialiser called from ``FUN_00089060``
       and ``FUN_00089300``): VA 0x83FAC writes ``12.0`` when
       air power is 1-3, VA 0x83FCE writes ``9.0`` otherwise.
       Both are scaled by the same slider so gameplay with air
       power equipped responds correctly (previously only the
       5 static sites were patched, leaving 83F90's writes
       vanilla — which is why users with air-power reported
       "air control slider does nothing").

    Each imm32 is independently scaled: ``new = current × scale``
    (reading the current value at patch time, so 12.0 stays at
    ``12 × scale`` and 9.0 stays at ``9 × scale``).

    Returns True on any successful write, False on no-op / all-
    drifted (with per-site warnings in the drift case).
    """
    if air_control_scale == 1.0:
        return False

    patched = 0
    scale = float(air_control_scale)

    # Path 1: the 5 `MOV [reg+0x140], 0x41100000` imm32 writes.
    # All share the vanilla 9.0 imm32 so we recognise drift
    # against a fixed expected value.
    new_imm_9 = struct.pack("<f", _VANILLA_AIR_CONTROL * scale)
    for site_va in _AIR_CONTROL_SITE_VAS:
        off = va_to_file(site_va)
        current = bytes(xbe_data[off:off + 4])
        if current != _AIR_CONTROL_IMM32_VANILLA:
            print(f"  WARNING: air_control — imm32 at VA "
                  f"0x{site_va:X} drifted (got {current.hex()}); "
                  f"skipping.")
            continue
        xbe_data[off:off + 4] = new_imm_9
        patched += 1

    # Path 2: the 2 `MOV [ECX], imm32` sites in FUN_00083F90.
    # 12.0 and 9.0 are different imm32s so scale-from-current
    # preserves the distinction (12 × scale vs 9 × scale).
    for site_va in _AIR_CONTROL_SECONDARY_SITE_VAS:
        off = va_to_file(site_va)
        current_bytes = bytes(xbe_data[off:off + 4])
        try:
            current_val = struct.unpack("<f", current_bytes)[0]
        except Exception:  # noqa: BLE001
            print(f"  WARNING: air_control — secondary imm32 at "
                  f"VA 0x{site_va:X} unreadable; skipping.")
            continue
        # Safety: reject if the value looks wildly off (drift /
        # already patched).  Vanilla values are 12.0 and 9.0.
        if not (3.0 <= current_val <= 30.0):
            print(f"  WARNING: air_control — secondary imm32 at "
                  f"VA 0x{site_va:X} looks non-vanilla "
                  f"({current_val:.3f}); skipping.")
            continue
        new_val = current_val * scale
        xbe_data[off:off + 4] = struct.pack("<f", new_val)
        patched += 1

    if patched == 0:
        return False

    total = len(_AIR_CONTROL_SITE_VAS) + len(_AIR_CONTROL_SECONDARY_SITE_VAS)
    print(f"  Player air-control speed: {air_control_scale:.3f}x "
          f"vanilla  ({patched}/{total} sites rewritten)")
    return True


def apply_flap_height(
    xbe_data: bytearray,
    *,
    flap_scale: float = 1.0,
) -> bool:
    """Patch ``default.xbe`` to scale the wing-flap (Air-power
    double-jump) vertical impulse.

    v2 (April 2026) — retargets the patch from the ineffective
    ``FADD [0x001A25C0]`` site to the real wing-flap ``sqrt(2gh)``
    FLD inside ``FUN_00089300`` at VA 0x000893AE.  The mechanism
    mirrors the jump-height patch: the vanilla ``FLD [0x001980A8]``
    (gravity 9.8) is rewritten to ``FLD [inject_va]`` where the
    injected constant equals ``9.8 × flap_scale²``.  After the
    subsequent ``FMUL × flap_height``, ``FADD ST,ST`` (×2), and
    ``FSQRT``, the result becomes ``flap_scale × vanilla_v0`` —
    linear scaling of the wing-flap's initial vertical velocity.

    At ``flap_scale = 2.0``, peak wing-flap height is ~4× vanilla
    (linear v0 × linear ~= quadratic height, per projectile
    motion).  At ``flap_scale = 0.5``, it's 1/4 vanilla.

    Returns True on apply, False on no-op / site drift.
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

    # Inject 9.8 × flap_scale² so the sqrt-based v0 formula
    # scales linearly by flap_scale.  Same math as apply_jump_speed.
    scale_sq = float(flap_scale) ** 2
    inject_value = _VANILLA_JUMP_GRAVITY * scale_sq
    inject_bytes = struct.pack("<f", inject_value)
    _, inject_va = _carve_shim_landing(xbe_data, inject_bytes)

    # FLD dword [abs inject_va]   encoded as D9 05 <va>
    xbe_data[off:off + 6] = b"\xD9\x05" + struct.pack("<I", inject_va)

    print(f"  Player wing-flap height: {flap_scale:.3f}x vanilla  "
          f"(injected gravity scalar = {inject_value:.3f} at VA "
          f"0x{inject_va:X}, produces v0 = {flap_scale:.3f}× "
          f"vanilla_v0)")
    return True


def apply_slope_slide_speed(
    xbe_data: bytearray,
    *,
    slope_slide_scale: float = 1.0,
) -> bool:
    """Patch ``default.xbe`` to scale slope-slide velocity.

    When the player lands on a slope steeper than 45° from
    upright, the engine transitions to state 3 (slow slope
    slide) inside ``player_slope_slide_tick`` (FUN_00089A70).
    That function's velocity scalar reads ``[0x001AAB68]=2.0``
    at VA 0x89B76 — the ONE and ONLY reader of that constant
    in the binary.

    We overwrite the 4-byte float in-place (2.0 → 2.0 ×
    slope_slide_scale) — zero collateral, zero shim required.

    Only affects:
      * State 3 slow slope slide (e.g., sliding down moderately
        steep terrain when walking over it)
      * Subsequent-transition state 4 fast slide (reads the
        same constant indirectly via dt scaling)

    Does NOT affect:
      * Player-initiated roll / dash (that's
        ``roll_speed_scale``, the WHITE/BACK boost).
      * Walking, airborne, climbing, swimming — all independent.

    Returns True on apply, False on no-op or drift.
    """
    if slope_slide_scale == 1.0:
        return False

    off = va_to_file(_ROLL_CONST_VA)   # 0x001AAB68
    current = bytes(xbe_data[off:off + 4])
    if current != _ROLL_CONST_VANILLA:
        print(f"  WARNING: slope_slide_speed — constant at VA "
              f"0x{_ROLL_CONST_VA:X} drifted (got "
              f"{current.hex()}); skipping.")
        return False

    new_value = _VANILLA_ROLL_SPEED * float(slope_slide_scale)
    xbe_data[off:off + 4] = struct.pack("<f", new_value)
    print(f"  Slope-slide speed: {slope_slide_scale:.3f}x vanilla  "
          f"(constant {_VANILLA_ROLL_SPEED:.3f} -> {new_value:.3f} "
          f"at VA 0x{_ROLL_CONST_VA:X})")
    return True


def apply_flap_at_peak(
    xbe_data: bytearray,
    *,
    at_peak_scale: float = 1.0,
) -> bool:
    """Patch ``default.xbe`` to give subsequent wing flaps FULL
    v0 when the player is near their jump peak.

    Vanilla behaviour: each subsequent wing flap computes
    ``fVar1 = peak_z + flap_height - current_z``, clamps to >=0,
    then caps ``fVar2 = min(fVar1, flap_height)``.  The v0 is
    ``sqrt(2 * g * fVar2)``.

    After the first flap the sprite drifts above ``peak_z`` by a
    small delta before ``peak_z`` itself updates, so fVar1 =
    flap_height - delta (small) → weak v0.  Hence the
    "subsequent flaps at peak are weak" user report.

    v2 fix (late April 2026): rewrite the 2-byte ``FLD ST(1)``
    at VA 0x8939F to ``FLD ST(0)`` — duplicating the
    just-loaded ``flap_height`` instead of ``fVar1``.  The
    subsequent FCOMP always compares ``fh`` with ``fh`` (equal),
    so the JP at 0x893A8 is always taken, skipping the min
    selection.  ``fVar2 = flap_height`` every flap → full v0.
    ``fVar1`` is preserved untouched for the below-6m halving
    check at VA 0x893C0, so the ``flap_below_peak_scale`` knob
    still behaves correctly.

    v1 NOPed the FSUB at 0x89381 instead, which inadvertently
    forced ``fVar1`` large and tripped the halving path AND
    drained 100 fuel per flap via ``consume_fuel`` — user
    reported "flaps at peak remove upward velocity" because of
    that fuel drain.  v2 avoids both side effects.

    This is a binary toggle (slider value != 1.0 enables).
    Returns True on apply, False on no-op / drift.
    """
    if at_peak_scale == 1.0:
        return False

    off = va_to_file(_FLAP_PEAK_CAP_SITE_VA)
    size = len(_FLAP_PEAK_CAP_SITE_VANILLA)
    current = bytes(xbe_data[off:off + size])
    if current != _FLAP_PEAK_CAP_SITE_VANILLA:
        print(f"  WARNING: flap_at_peak — site at VA "
              f"0x{_FLAP_PEAK_CAP_SITE_VA:X} drifted (got "
              f"{current.hex()}); skipping.")
        return False

    xbe_data[off:off + size] = _FLAP_PEAK_CAP_PATCH
    print(f"  Wing-flap (at peak): full v0 for 2nd+ flaps  "
          f"(FLD ST(1)→FLD ST(0) at VA "
          f"0x{_FLAP_PEAK_CAP_SITE_VA:X})")
    return True


def apply_flap_subsequent(
    xbe_data: bytearray,
    *,
    subsequent_scale: float = 1.0,
) -> bool:
    """Patch ``default.xbe`` to scale subsequent-flap v0 when
    the player is > 6m below their peak.

    After the first wing flap, ``FUN_00089300`` halves v0 via
    ``FMUL [0x001A2510] = 0.5`` at VA 0x893DD when the player
    has fallen > 6m below their peak.  This is why the second
    and later flaps feel weaker than the first — IF the player
    is way below the peak.  For flaps near peak, see
    :func:`apply_flap_at_peak`.

    We rewrite the FMUL to reference an injected ``0.5 ×
    subsequent_scale``:

    - ``1.0`` (default): vanilla halving (v0 / 2).
    - ``2.0``:           no halving (full v0).
    - ``4.0``:           subsequent flaps now BOOST by 2× (v0 × 2).

    This is independent of ``flap_scale``: set both to 2.0 to
    get "first flap 2× + subsequent flaps also full v0 × 2" —
    i.e., every flap is consistently strong.

    Returns True on apply, False on no-op / drift.
    """
    if subsequent_scale == 1.0:
        return False

    from azurik_mod.patching.apply import _carve_shim_landing

    off = va_to_file(_FLAP_SUBSEQUENT_SITE_VA)
    current = bytes(xbe_data[off:off + 6])
    if current != _FLAP_SUBSEQUENT_SITE_VANILLA:
        print(f"  WARNING: flap_subsequent — site at VA "
              f"0x{_FLAP_SUBSEQUENT_SITE_VA:X} drifted (got "
              f"{current.hex()}); skipping.")
        return False

    inject_value = _VANILLA_FLAP_SUBSEQUENT * float(subsequent_scale)
    inject_bytes = struct.pack("<f", inject_value)
    _, inject_va = _carve_shim_landing(xbe_data, inject_bytes)

    # FMUL dword [abs inject_va]   encoded as D8 0D <va>
    xbe_data[off:off + 6] = b"\xD8\x0D" + struct.pack("<I", inject_va)

    print(f"  Wing-flap subsequent (2nd+) height: "
          f"{subsequent_scale:.3f}x vanilla  (halving factor "
          f"{_VANILLA_FLAP_SUBSEQUENT} -> {inject_value:.3f} at "
          f"VA 0x{inject_va:X})")
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
        # v4: Roll targets the FMUL instruction at VA 0x849E4
        # (6 bytes — ``FMUL [abs32]``).
        roll_off = va_to_file(_ROLL_FMUL_VA)
        ranges.append((roll_off, roll_off + 6))
    except Exception:  # noqa: BLE001
        pass
    try:
        # Slope-slide speed: 4-byte constant at VA 0x1AAB68.
        # Back on the whitelist (late April 2026) because the
        # dedicated ``slope_slide_speed_scale`` slider targets it.
        slope_off = va_to_file(_ROLL_CONST_VA)
        ranges.append((slope_off, slope_off + 4))
    except Exception:  # noqa: BLE001
        pass
    try:
        # Climb targets a 4-byte constant at VA 0x001980E4.
        ranges.append((va_to_file(_CLIMB_CONST_VA),
                       va_to_file(_CLIMB_CONST_VA) + 4))
    except Exception:  # noqa: BLE001
        pass
    # Air-control: 5 static imm32 sites + 2 dominant secondary
    # sites in FUN_00083F90 (4 bytes each).
    for site_va in (list(_AIR_CONTROL_SITE_VAS)
                    + list(_AIR_CONTROL_SECONDARY_SITE_VAS)):
        try:
            ac_off = va_to_file(site_va)
            ranges.append((ac_off, ac_off + 4))
        except Exception:  # noqa: BLE001
            continue
    # Flap: 6-byte FLD rewrite site (v2 — was FADD pre-v2).
    try:
        fl_off = va_to_file(_FLAP_SITE_VA)
        ranges.append((fl_off, fl_off + 6))
    except Exception:  # noqa: BLE001
        pass
    # Flap-subsequent: 6-byte FMUL rewrite site (v2 April 2026).
    try:
        fls_off = va_to_file(_FLAP_SUBSEQUENT_SITE_VA)
        ranges.append((fls_off, fls_off + 6))
    except Exception:  # noqa: BLE001
        pass
    # Flap at peak: 2-byte FLD-ST1 rewrite at VA 0x8939F
    # (late April 2026 v2 — previous 3-byte NOP at 0x89381
    # caused fuel-drain side effects, reverted).
    try:
        fpc_off = va_to_file(_FLAP_PEAK_CAP_SITE_VA)
        ranges.append((fpc_off, fpc_off + len(_FLAP_PEAK_CAP_SITE_VANILLA)))
    except Exception:  # noqa: BLE001
        pass

    # Dynamic: if a site has been rewritten to `FLD/FMUL [abs32]`,
    # follow the abs32 pointer through the section table and whitelist
    # a 4-byte range for the injected float constant.  Uses the
    # shared resolver from ``azurik_mod.patching.xbe``.
    from azurik_mod.patching.xbe import resolve_va_to_file

    follow_sites: list[tuple[int, bytes]] = [
        (walk_off, b"\xD9\x05"),   # FLD  [abs32] (walk base)
        (swim_off, b"\xD8\x0D"),   # FMUL [abs32] (swim mult)
    ]
    try:
        follow_sites.append((va_to_file(_ROLL_FMUL_VA), b"\xD8\x0D"))
    except Exception:  # noqa: BLE001
        pass
    try:
        follow_sites.append((va_to_file(_JUMP_SITE_VA), b"\xD9\x05"))
    except Exception:  # noqa: BLE001
        pass
    try:
        # v2: Flap site now uses FLD [abs32] (D9 05) same as the
        # jump site — we rewrote a gravity FLD, not a FADD.
        follow_sites.append((va_to_file(_FLAP_SITE_VA), b"\xD9\x05"))
    except Exception:  # noqa: BLE001
        pass
    try:
        # Flap-subsequent uses FMUL [abs32] (D8 0D) — rewrote the
        # 0.5 halving factor for 2nd+ flaps.
        follow_sites.append(
            (va_to_file(_FLAP_SUBSEQUENT_SITE_VA), b"\xD8\x0D"))
    except Exception:  # noqa: BLE001
        pass

    for site_off, prefix in follow_sites:
        if len(xbe) >= site_off + 6:
            patch = xbe[site_off:site_off + 6]
            if patch[:2] == prefix:
                va = struct.unpack("<I", patch[2:6])[0]
                fo = resolve_va_to_file(xbe, va)
                if fo is not None:
                    ranges.append((fo, fo + 4))

    return ranges


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PLAYER_PHYSICS_SITES = [
    GRAVITY_PATCH,
    WALK_SPEED_SCALE,
    # ROLL_SPEED_SCALE retired late April 2026 — the WHITE-button
    # FMUL at VA 0x849E4 scales the shared ``magnitude`` field,
    # but the actual roll animation (characters/garret4/
    # roll_forward) drives position via animation root motion,
    # which the magnitude multiplier never touches.  Users
    # consistently reported the slider had no observable effect.
    # ROLL_SPEED_SCALE is kept as a module-level symbol for
    # back-compat callers + the ``apply_player_speed`` test suite,
    # but it's no longer surfaced in the GUI / randomizer.  A
    # future root-motion shim could revive it.
    SWIM_SPEED_SCALE,
    JUMP_SPEED_SCALE,
    AIR_CONTROL_SCALE,
    FLAP_HEIGHT_SCALE,
    FLAP_BELOW_PEAK_SCALE,
    FLAP_AT_PEAK_SCALE,
    CLIMB_SPEED_SCALE,
    SLOPE_SLIDE_SPEED_SCALE,
]
"""Registered Patches-page sites.  Walk, swim, jump, and flap use
shim-landed FLD/FMUL rewrites; air-control uses imm32 overwrites
at seven call sites; climb and slope-slide use direct constant
overwrites.  Roll was retired — see note inline above."""


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
    flap_subsequent_scale: float | None = None,
    flap_below_peak_scale: float | None = None,
    flap_at_peak_scale: float | None = None,
    climb_speed_scale: float | None = None,
    slope_slide_speed_scale: float | None = None,
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
        flap_subsequent_scale=flap_subsequent_scale,
        flap_below_peak_scale=flap_below_peak_scale,
        flap_at_peak_scale=flap_at_peak_scale,
        climb_scale=climb,
        slope_slide_scale=slope_slide_speed_scale,
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
    "FLAP_AT_PEAK_SCALE",
    "FLAP_BELOW_PEAK_SCALE",
    "FLAP_HEIGHT_SCALE",
    "FLAP_SUBSEQUENT_SCALE",
    "GRAVITY_BASELINE",
    "GRAVITY_PATCH",
    "JUMP_SPEED_SCALE",
    "PLAYER_PHYSICS_SITES",
    "ROLL_SPEED_SCALE",
    "RUN_SPEED_SCALE",
    "SLOPE_SLIDE_SPEED_SCALE",
    "SWIM_SPEED_SCALE",
    "WALK_SPEED_SCALE",
    "apply_air_control_speed",
    "apply_climb_speed",
    "apply_flap_at_peak",
    "apply_flap_height",
    "apply_flap_subsequent",
    "apply_jump_speed",
    "apply_player_physics",
    "apply_player_speed",
    "apply_slope_slide_speed",
    "apply_swim_speed",
]
