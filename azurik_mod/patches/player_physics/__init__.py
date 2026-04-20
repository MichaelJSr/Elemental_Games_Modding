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
    description=(
        "World gravity constant at VA 0x1980A8.  Earth is 9.8; "
        "0.0 = everything floats.  Affects the player AND every "
        "entity that uses the gravity term — projectiles, enemies, "
        "physics objects.  Jump / wing-flap velocity scales with "
        "√(2g·h), so halving gravity halves jump impulse."
    ),
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
    description=(
        "Multiplier on the ground walking-speed baseline (FLD "
        "[EAX+0x40] at VA 0x85F62).  Vanilla = 7.0 units/s.  "
        "2x ≈ double speed.  Does NOT affect rolling, swimming, "
        "or airborne horizontal movement — those have their own "
        "sliders."
    ),
)

ROLL_SPEED_SCALE = ParametricPatch(
    name="roll_speed_scale",
    label="Player roll speed (RETIRED)",
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
        "RETIRED — roll is animation root-motion driven (the "
        "magnitude field at 0x1A25BC is unused by the roll state). "
        "A shim at 0x866D9 landed bytes but produced no in-game "
        "effect; pack removed."
    ),
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
    description=(
        "Multiplier on the swim-stroke FMUL at VA 0x8B7BF.  "
        "Vanilla swim stroke = magnitude × 10.0 per frame; 2x "
        "doubles the stroke distance.  Only affects the swim "
        "state (in-water movement)."
    ),
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
    description=(
        "Scales the first-jump initial velocity (FLD at VA "
        "0x89160).  v0 = √(2g·h) so the slider is LINEAR in "
        "velocity — 2x ≈ 2x v0 ≈ 4x peak height.  Separate "
        "from wing-flap impulse."
    ),
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
    description=(
        "Scales the horizontal air-control scalar entity[+0x140] "
        "consumed by player_airborne_tick.  Affects how much "
        "the stick moves the player sideways while airborne.  "
        "Rewrites 7 imm32 sites (5 jump-init + 2 airborne-reinit)."
    ),
)

FLAP_HEIGHT_SCALE = ParametricPatch(
    name="flap_height_scale",
    label="Wing-flap: 1st flap height",
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
        "Scales the 1st wing flap's peak height.  2x ≈ 2x "
        "higher.  2nd+ flaps use the other flap slider."
    ),
)

FLAP_BELOW_PEAK_SCALE = ParametricPatch(
    name="flap_below_peak_scale",
    label="Wing-flap: far-descent recovery",
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
        "Scales v0 for 2nd+ flaps when >6m below peak (the "
        "descent-penalty halving branch).  Vanilla halves v0 "
        "via ×0.5 FMUL; 2x cancels the halving (full v0), 4x "
        "doubles it.  Does NOT affect the fuel drain in the "
        "same branch — see flap_descent_fuel_cost_scale for that."
    ),
)

# Back-compat alias — the old name pre-rename (late April 2026).
FLAP_SUBSEQUENT_SCALE = FLAP_BELOW_PEAK_SCALE

# FLAP_AT_PEAK_SCALE / SLOPE_SLIDE_SPEED_SCALE / CLIMB_SPEED_SCALE
# are kept as back-compat module-level symbols for any external
# callers or tests, but retired from PLAYER_PHYSICS_SITES below.
# Each had byte-patch attempts (and for the first three, also shim
# attempts) that landed cleanly but produced no in-game effect;
# the underlying game logic (peak latch, dynamic state-4 multiplier,
# animation root motion) bypasses every hook site we identified.
# See docs/LEARNINGS.md § "Retired physics sliders" for details.
FLAP_AT_PEAK_SCALE = ParametricPatch(
    name="flap_at_peak_scale",
    label="Wing-flap height (near peak, RETIRED)",
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
        "RETIRED — no hook site we tried (including a final-FSTP "
        "shim at 0x89409) produces observable effect. The "
        "near-peak v0=0 behaviour is what bounds every subsequent "
        "flap; raising `flap_height_scale` helps far-below-peak "
        "flaps instead."
    ),
)

SLOPE_SLIDE_SPEED_SCALE = ParametricPatch(
    name="slope_slide_speed_scale",
    label="Slope-slide speed (RETIRED)",
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
        "RETIRED — state-4 fast slide uses a dynamic 500x multiplier "
        "computed from surface normal; static FLD rewrite at 0x8A095 "
        "had no in-game effect."
    ),
)

CLIMB_SPEED_SCALE = ParametricPatch(
    name="climb_speed_scale",
    label="Player climbing speed (RETIRED)",
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
        "RETIRED — climb is animation root-motion driven. Shim at "
        "0x883FF (wrapping anim_apply_translation CALL) landed "
        "bytes but gave no in-game effect."
    ),
)

# Wing-flap altitude ceiling — shim-backed (round 11).
#
# Background: `player_jump_init` latches
# ``peak_z = entity.z + entity.flap_height`` via the FADD/FSTP pair
# at VA 0x89154 / 0x8915A, and that value is never refreshed during
# airborne state.  ``wing_flap``'s cap is emergent from this:
# ``fVar2 = min(peak_z + flap_height - current_z, flap_height)``, so
# subsequent flaps can't exceed ``initial_flap_z + flap_height``.
#
# This slider multiplies the FADD's second operand so the latch
# becomes ``entity.z + ceiling_scale * entity.flap_height``.  It's
# ORTHOGONAL to the per-flap impulse sliders: ``flap_height_scale``
# scales v0 for every flap, ``flap_below_peak_scale`` scales the
# >6m-below-peak halving, and ``wing_flap_ceiling_scale`` raises
# the usable altitude envelope.
#
# Implementation is a 15-byte hand-assembled trampoline (FLD /
# FMUL / FADDP / RET) rather than a byte-level operand rewrite
# because the FADD reads a per-armor field (can't precompute
# K × flap_height at patch time).
WING_FLAP_CEILING_SCALE = ParametricPatch(
    name="wing_flap_ceiling_scale",
    label="Wing-flap: altitude ceiling",
    va=0,
    size=0,
    original=b"",
    default=1.0,
    slider_min=0.1,
    slider_max=20.0,
    slider_step=0.05,
    unit="x",
    encode=lambda v: struct.pack("<d", float(v)),
    decode=lambda b: struct.unpack("<d", b)[0],
    description=(
        "Raises the wing-flap altitude ceiling.  K=1 vanilla "
        "(2*flap_height envelope); K=2 ≈ 1.5x headroom, "
        "K=5 ≈ 3x, K=10 ≈ 5.5x."
    ),
)

# Companion to wing_flap_ceiling_scale.  Vanilla ``wing_flap`` has
# a second, independent anti-ground-recovery mechanic: when the
# player has fallen > 6m below their peak_z envelope, a
# ``consume_fuel(this, 100.0)`` call at VA 0x893D4 drains the
# entire air-power gauge in a single flap AND the v0 is halved
# via the FMUL at 0x893DD.  The fuel drain is what users typically
# perceive as "flaps fail when I descend too far" — after one
# below-peak flap the gauge is empty, so the next
# consume_fuel(this, 1.0) at VA 0x89354 refuses and the flap
# doesn't happen.
#
# This slider scales the 100.0 fuel cost directly by overwriting
# the PUSH immediate at VA 0x893CE (the bytes feeding the 100.0
# argument to consume_fuel).  At 0.0 the fuel cost is zero and
# descent flaps no longer drain the gauge; at 1.0 vanilla behaviour
# is preserved.  Pair with ``flap_below_peak_scale = 2.0`` to also
# cancel the v0 halving — the two sliders together turn the
# descent penalty into a no-op.
FLAP_DESCENT_FUEL_COST_SCALE = ParametricPatch(
    name="flap_descent_fuel_cost_scale",
    label="Wing-flap: descent penalty fuel",
    va=0,
    size=0,
    original=b"",
    default=1.0,
    # Useful range is [-0.05, 0.05]: vanilla fuel_max is 1 / 2 /
    # 5 (air1 / air2 / air3), and the ``cost / fuel_max`` must
    # stay under ``fuel_max × 2`` to avoid tripping the clear-
    # on-low-fuel path in consume_fuel.  At scale=1.0 (vanilla
    # 100.0 cost), cost/fuel_max is huge → always clears.  At
    # scale=0.01 (cost 1.0, same as first-flap), cost/fuel_max
    # is 1.0 for air1 / 0.5 for air2 / 0.2 for air3 — reasonable.
    # Negative values refund fuel.  Keep default=1.0 (vanilla);
    # the entry box still accepts out-of-range values for
    # expert tuning.
    slider_min=-0.05,
    slider_max=0.05,
    slider_step=0.001,
    unit="x",
    encode=lambda v: struct.pack("<d", float(v)),
    decode=lambda b: struct.unpack("<d", b)[0],
    description=(
        "Scales the descent-penalty fuel cost (fires when "
        "you've fallen >6m below peak).  Vanilla cost = 100 "
        "but fuel_max is only 1-5, so the clear-to-0 path "
        "trips for any noticeable positive scale.  Useful "
        "settings: 0.01 ≈ same as a normal flap, 0.0 = no "
        "drain, negative = REFUND (descent flaps add fuel).  "
        "Default 1.0 keeps vanilla."
    ),
)

# Near-peak fuel cost (the `consume_fuel(this, 1.0)` at the flap
# ENTRY path, fires every flap regardless of altitude).  Same
# PUSH-imm32 pattern as the descent-cost site — just a different
# VA.  0.0 = no cost (infinite flaps on a single fuel charge).
FLAP_ENTRY_FUEL_COST_SCALE = ParametricPatch(
    name="flap_entry_fuel_cost_scale",
    label="Wing-flap: fuel cost per flap",
    va=0,
    size=0,
    original=b"",
    default=1.0,
    # Useful range is [-5, 5] step 0.1: vanilla cost is 1.0 and
    # fuel_max is 1 / 2 / 5 (air1/2/3), so ±5 spans "gauge
    # refunds 5/flap" to "gauge drains 5× faster".  Outside that
    # the clear-to-0 threshold trips or the refund saturates.
    slider_min=-5.0,
    slider_max=5.0,
    slider_step=0.1,
    unit="x",
    encode=lambda v: struct.pack("<d", float(v)),
    decode=lambda b: struct.unpack("<d", b)[0],
    description=(
        "Scales the per-flap fuel cost (fires on every flap, "
        "regardless of altitude).  Vanilla = 1.0, giving "
        "1 / 2 / 5 flaps per gauge for air1 / air2 / air3.  "
        "0.1 ≈ 10× more flaps; 0.0 = infinite; negative "
        "refunds fuel.  Default 1.0 = vanilla."
    ),
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
# Historical v2 byte-patch site (retired round 10 — no in-game
# effect observed despite clean byte landing).  Kept as a drift
# anchor for inspect/verify.
_FLAP_PEAK_CAP_SITE_VA = 0x0008939F
_FLAP_PEAK_CAP_SITE_VANILLA = bytes([
    0xD9, 0xC1,                # FLD ST(1)  — dup fVar1
])

# Wing-flap ceiling shim (round 11).
#
# Hooks the ``FADD [ESI+0x144]`` in ``player_jump_init`` that feeds
# the ``FSTP [ESI+0x164]`` peak_z latch.  We replace the 6-byte
# FADD with a ``CALL rel32 + NOP`` trampoline; the shim does
# ``FLD [ESI+0x144] ; FMUL [scale_va] ; FADDP ST1 ; RET``.  Net
# effect: ``peak_z = entity.z + ceiling_scale * flap_height``.
#
# At shim entry the FPU stack has the vanilla-loaded ``entity.z``
# on top (from ``FLD [EDI+0x5C]`` at VA 0x8914C, which the
# trampoline leaves untouched).  FADDP ST1 pops the scaled
# flap_height and adds it into that ``entity.z``, matching the
# vanilla FADD's semantics exactly — only the magnitude of the
# added term changes.  After RET, execution resumes at the
# untouched ``FSTP [ESI+0x164]`` at VA 0x8915A.
_PEAK_Z_HOOK_VA = 0x00089154
_PEAK_Z_HOOK_VANILLA = bytes([
    0xD8, 0x86, 0x44, 0x01, 0x00, 0x00,   # FADD [ESI+0x144]
])
_PEAK_Z_SHIM_BODY_SIZE = 15

# Wing-flap descent fuel-cost site (round 11.7).
#
# Inside ``wing_flap`` at VA 0x893CD vanilla executes
#   PUSH 0x42C80000   ; 100.0f — cost argument to consume_fuel
#   MOV  ECX, EDI     ; this-pointer
#   CALL consume_fuel ; at VA 0x893D4
# iff the `6.0 < fVar1` branch at VA 0x893C0 was taken (i.e. the
# player is > 6m below peak_z).  The PUSH immediate lives at
# VA 0x893CE (the 4 bytes after the 0x68 opcode).  Rewriting those
# 4 bytes with ``struct.pack("<f", 100.0 * scale)`` — 0.0 kills the
# fuel cost, 1.0 preserves vanilla.
#
# The 6.0 threshold itself lives at VA 0x001A25B8 in .rdata, but
# that constant has 19 readers across the binary (unrelated
# subsystems), so overwriting it would cause wide collateral
# damage.  Scaling the PUSH immediate instead affects ONLY this
# wing_flap call site.
_FLAP_DESCENT_FUEL_COST_VA = 0x000893CE
_FLAP_DESCENT_FUEL_COST_VANILLA = bytes.fromhex("0000c842")   # 100.0f
_VANILLA_FLAP_DESCENT_FUEL_COST = 100.0

# Entry-path fuel cost inside ``wing_flap``.  VA 0x8934D executes
# ``PUSH 0x3F800000 (= 1.0f)`` as the ``cost`` argument to the
# ``consume_fuel(this, 1.0)`` call at VA 0x89354.  This call fires
# on EVERY flap attempt and its return decides whether the flap
# actually happens (if it returns 0 due to empty fuel, the flap
# silently fails).  Same 4-byte PUSH-imm32 pattern as the descent
# cost above — rewrite the 4 bytes after the 0x68 opcode.
_FLAP_ENTRY_FUEL_COST_VA = 0x0008934E
_FLAP_ENTRY_FUEL_COST_VANILLA = bytes.fromhex("0000803f")     # 1.0f
_VANILLA_FLAP_ENTRY_FUEL_COST = 1.0

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
    wing_flap_ceiling_scale: float | None = None,
    flap_descent_fuel_cost_scale: float | None = None,
    flap_entry_fuel_cost_scale: float | None = None,
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

    # flap_at_peak_scale / climb_scale / slope_slide_scale are
    # accepted for back-compat but produce no in-game effect (the
    # underlying patches were removed in round 10 after consistent
    # user reports of no observable change).  See docs/LEARNINGS.md
    # § "Retired physics sliders" for the full RE rationale.
    _ = (flap_at_peak_scale, climb_scale, slope_slide_scale)

    wfc = (1.0 if wing_flap_ceiling_scale is None
           else float(wing_flap_ceiling_scale))
    if wfc != 1.0:
        apply_wing_flap_ceiling(xbe_data, ceiling_scale=wfc)

    fdfc = (1.0 if flap_descent_fuel_cost_scale is None
            else float(flap_descent_fuel_cost_scale))
    if fdfc != 1.0:
        apply_flap_descent_fuel_cost(
            xbe_data, fuel_cost_scale=fdfc)

    fefc = (1.0 if flap_entry_fuel_cost_scale is None
            else float(flap_entry_fuel_cost_scale))
    if fefc != 1.0:
        apply_flap_entry_fuel_cost(
            xbe_data, fuel_cost_scale=fefc)


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
    """Retired no-op (round 10).

    The climbing constant at VA 0x1980E4 IS the reference baseline,
    but climb motion is animation root-motion driven — scaling the
    constant had no observable gameplay effect.  A shim wrapping
    ``anim_apply_translation`` inside ``player_climb_tick`` also
    failed.  Kept as a stable entry point.
    """
    del xbe_data, climb_scale  # unused
    return False


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


def apply_wing_flap_ceiling(
    xbe_data: bytearray,
    *,
    ceiling_scale: float = 1.0,
) -> bool:
    """Install the wing-flap altitude-ceiling shim.

    Replaces the 6-byte ``FADD [ESI+0x144]`` at VA 0x89154 (inside
    ``player_jump_init``, which computes the ``peak_z`` latch used
    by every subsequent wing flap's altitude cap) with a 5-byte
    ``CALL rel32`` + 1 ``NOP``.  The 15-byte shim body performs:

    .. code-block:: asm

        FLD   [ESI+0x144]         ; D9 86 44 01 00 00 — flap_height
        FMUL  [ceiling_scale_va]  ; D8 0D <abs32>     — × user K
        FADDP ST1                 ; DE C1             — pop, add to ST0
        RET                       ; C3                — resume at 0x8915A

    Post-RET control returns to the unchanged ``FSTP [ESI+0x164]``
    at VA 0x8915A, which writes the adjusted sum to ``peak_z``.

    Net effect: ``peak_z = entity.z + K * flap_height`` instead of
    vanilla's ``entity.z + flap_height``.  The consumer in
    ``wing_flap`` (FUN_00089300) evaluates

    .. code-block:: text

        fVar1 = peak_z + flap_height - current_z

    so the hard ceiling above the jump's starting ground becomes
    ``(K+1) * flap_height`` (vanilla K=1 → 2 × flap_height).  See
    ``docs/LEARNINGS.md`` § "Wing-flap ceiling — shim at jump_init"
    for the full (K+1) math and why this hook point works where
    the round-7..10 downstream attempts didn't.

    Orthogonal to ``flap_height_scale`` (per-flap v0) and
    ``flap_below_peak_scale`` (2nd+ flap halving factor) — those
    three knobs cleanly compose.

    Returns ``True`` on apply, ``False`` on no-op / drift / already-
    installed.  Idempotent (second apply is a no-op thanks to the
    trampoline-shape detection in :mod:`shim_builder`).
    """
    if ceiling_scale == 1.0:
        return False

    from azurik_mod.patching.shim_builder import (
        HandShimSpec,
        emit_fmul_abs32,
        install_hand_shim,
        with_sentinel,
    )

    spec = HandShimSpec(
        hook_va=_PEAK_Z_HOOK_VA,
        hook_vanilla=_PEAK_Z_HOOK_VANILLA,
        trampoline_mode="call",
        hook_pad_nops=1,
        body_size=_PEAK_Z_SHIM_BODY_SIZE,
    )

    def _build_body(shim_va: int, data_va: int) -> bytes:
        # ST(0) = entity.z (loaded vanilla at 0x8914C and untouched
        # by the trampoline call's stack push).  We load
        # flap_height, scale it, add-pop to ST(0), return.
        body = (
            b"\xD9\x86\x44\x01\x00\x00"   # FLD  [ESI+0x144]
            + emit_fmul_abs32(data_va)     # FMUL [data_va]
            + b"\xDE\xC1"                 # FADDP ST1
            + b"\xC3"                      # RET
        )
        assert len(body) == _PEAK_Z_SHIM_BODY_SIZE, (
            f"wing_flap_ceiling body is {len(body)} B, expected "
            f"{_PEAK_Z_SHIM_BODY_SIZE}")
        return body

    result = install_hand_shim(
        xbe_data, spec,
        data_block=with_sentinel(struct.pack("<f", float(ceiling_scale))),
        build_body=_build_body,
        label=f"Wing-flap altitude ceiling: {ceiling_scale:.3f}x",
    )
    return result is not None


def apply_flap_descent_fuel_cost(
    xbe_data: bytearray,
    *,
    fuel_cost_scale: float = 1.0,
) -> bool:
    """Scale the fuel cost of the wing-flap descent-penalty branch.

    Overwrites the 4-byte ``PUSH 100.0f`` immediate at VA 0x893CE
    feeding ``consume_fuel(this, cost)`` at VA 0x893D4.  The branch
    only fires when ``wing_flap``'s ``fVar1 = peak_z + flap_height -
    current_z`` exceeds 6.0 — i.e. the player has fallen > 6m below
    their latched peak.  In vanilla that drains the entire air-power
    gauge in one flap, leaving subsequent flaps unable to start
    (``consume_fuel(this, 1.0)`` at the flap entry returns 0 when
    fuel is empty).

    Scaling the PUSH immediate at 0x893CE is surgical: only this
    one call site is affected.  The shared 6.0 threshold at
    VA 0x001A25B8 has 19 unrelated readers across the binary, so
    overwriting that constant is not an option.

    - ``fuel_cost_scale = 1.0`` (default) preserves vanilla drain.
    - ``fuel_cost_scale = 0.0`` kills the drain entirely — descent
      flaps no longer clear the gauge.
    - Partial values scale linearly (``0.5`` → 50.0 fuel drained).

    Pair with ``flap_below_peak_scale = 2.0`` if you also want to
    cancel the v0 halving that runs in the same branch.

    Returns ``True`` on apply, ``False`` on no-op / drift.
    """
    if fuel_cost_scale == 1.0:
        return False

    off = va_to_file(_FLAP_DESCENT_FUEL_COST_VA)
    current = bytes(xbe_data[off:off + 4])
    if current != _FLAP_DESCENT_FUEL_COST_VANILLA:
        print(f"  WARNING: flap_descent_fuel_cost — site at VA "
              f"0x{_FLAP_DESCENT_FUEL_COST_VA:X} drifted (got "
              f"{current.hex()}); skipping.")
        return False

    new_cost = _VANILLA_FLAP_DESCENT_FUEL_COST * float(fuel_cost_scale)
    xbe_data[off:off + 4] = struct.pack("<f", new_cost)
    print(f"  Wing-flap descent fuel cost: {fuel_cost_scale:.3f}x "
          f"vanilla  ({_VANILLA_FLAP_DESCENT_FUEL_COST:.1f} -> "
          f"{new_cost:.3f} at VA 0x{_FLAP_DESCENT_FUEL_COST_VA:X})")
    return True


def apply_flap_entry_fuel_cost(
    xbe_data: bytearray,
    *,
    fuel_cost_scale: float = 1.0,
) -> bool:
    """Scale the fuel cost of the wing-flap entry / per-flap drain.

    Overwrites the 4-byte ``PUSH 1.0f`` immediate at VA 0x8934E
    feeding ``consume_fuel(this, 1.0)`` at VA 0x89354.  This call
    fires on EVERY flap regardless of altitude and its return
    decides whether the flap actually happens (empty fuel → flap
    silently refused).  Scaling this is orthogonal to
    ``flap_descent_fuel_cost_scale`` (which only affects the
    >6m-below-peak drain).

    Useful values:
    - 1.0 (default) — vanilla: 1/2/5 flaps per gauge for air1/2/3
    - 0.1 — ~10× more flaps per gauge
    - 0.0 — infinite flaps (gauge never drains from flapping)
    - negative — gauge refills on every flap

    Returns ``True`` on apply, ``False`` on no-op / drift.
    """
    if fuel_cost_scale == 1.0:
        return False

    off = va_to_file(_FLAP_ENTRY_FUEL_COST_VA)
    current = bytes(xbe_data[off:off + 4])
    if current != _FLAP_ENTRY_FUEL_COST_VANILLA:
        print(f"  WARNING: flap_entry_fuel_cost — site at VA "
              f"0x{_FLAP_ENTRY_FUEL_COST_VA:X} drifted (got "
              f"{current.hex()}); skipping.")
        return False

    new_cost = _VANILLA_FLAP_ENTRY_FUEL_COST * float(fuel_cost_scale)
    xbe_data[off:off + 4] = struct.pack("<f", new_cost)
    print(f"  Wing-flap entry fuel cost: {fuel_cost_scale:.3f}x "
          f"vanilla  ({_VANILLA_FLAP_ENTRY_FUEL_COST:.1f} -> "
          f"{new_cost:.3f} at VA 0x{_FLAP_ENTRY_FUEL_COST_VA:X})")
    return True


def apply_slope_slide_speed(
    xbe_data: bytearray,
    *,
    slope_slide_scale: float = 1.0,
) -> bool:
    """Retired no-op (round 10).

    Rewriting [0x1AAB68] only covers state 3 (slow slide); state 4
    (fast slide) uses a dynamic 500x multiplier from surface normal
    that we never successfully intercepted (neither the 4-byte
    constant overwrite nor a FLD shim at 0x8A095 produced in-game
    effect).  Kept as a stable entry point.
    """
    del xbe_data, slope_slide_scale  # unused
    return False


def apply_flap_at_peak(
    xbe_data: bytearray,
    *,
    at_peak_scale: float = 1.0,
) -> bool:
    """Retired no-op (round 10).

    Previous v1/v2/v3 attempts (FSUB-NOP at 0x89381, FLD-ST1
    rewrite at 0x8939F, and a final-FSTP shim at 0x89409) all
    landed bytes cleanly but produced no in-game effect.  The
    near-peak weak-v0 ceiling is governed by state we never
    successfully hooked.  Kept as a stable entry point so external
    callers (older scripts, randomizer) don't break.
    """
    del xbe_data, at_peak_scale  # unused
    return False


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
    from azurik_mod.patching.xbe import va_to_file

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
    # Slope-slide / climb 4-byte constants used to be on the
    # whitelist (round 9) but the apply_* functions that wrote
    # them were retired in round 10 as no-ops — whitelist entries
    # dropped to match.
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
    # Flap-at-peak 2-byte rewrite used to be here (round 9) — the
    # apply_flap_at_peak byte patch was retired in round 10 as a
    # no-op, so the range is gone.

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

    # Wing-flap ceiling shim (round 11).  The shim writes: 6 bytes
    # at the hook (CALL rel32 + NOP), a 15-byte shim body, and a
    # 4-byte ``float`` scale block.  Delegate to the shim_builder
    # helper so the body + data VAs are parsed out of the landed
    # trampoline automatically.
    try:
        from azurik_mod.patching.shim_builder import (
            HandShimSpec, whitelist_for_hand_shim)
        spec = HandShimSpec(
            hook_va=_PEAK_Z_HOOK_VA,
            hook_vanilla=_PEAK_Z_HOOK_VANILLA,
            trampoline_mode="call",
            hook_pad_nops=1,
            body_size=_PEAK_Z_SHIM_BODY_SIZE,
        )
        ranges.extend(whitelist_for_hand_shim(
            xbe, spec,
            # Body layout: FLD[ESI+0x144] (6 B) | FMUL[abs32] (6 B)
            # | FADDP (2 B) | RET (1 B).  The FMUL operand sits at
            # body offset 6; opcode bytes are D8 0D.
            data_abs32_offsets=(6,),
            data_abs32_opcode=b"\xD8\x0D",
            data_whitelist_size=4,
        ))
    except Exception:  # noqa: BLE001
        # Graceful for pre-apply / non-Azurik buffers.
        pass

    # Wing-flap descent fuel-cost site (round 11.7).  4-byte PUSH
    # immediate at VA 0x893CE is overwritten in place.  Always on
    # the whitelist so verify-patches doesn't flag the difference
    # when flap_descent_fuel_cost_scale != 1.0.
    try:
        fdfc_off = va_to_file(_FLAP_DESCENT_FUEL_COST_VA)
        ranges.append((fdfc_off, fdfc_off + 4))
    except Exception:  # noqa: BLE001
        pass

    # Wing-flap entry fuel-cost site (round 11.11).  4-byte PUSH
    # immediate at VA 0x8934E, same pattern as the descent site.
    try:
        fefc_off = va_to_file(_FLAP_ENTRY_FUEL_COST_VA)
        ranges.append((fefc_off, fefc_off + 4))
    except Exception:  # noqa: BLE001
        pass

    return ranges


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PLAYER_PHYSICS_SITES = [
    GRAVITY_PATCH,
    WALK_SPEED_SCALE,
    SWIM_SPEED_SCALE,
    JUMP_SPEED_SCALE,
    AIR_CONTROL_SCALE,
    FLAP_HEIGHT_SCALE,
    FLAP_BELOW_PEAK_SCALE,
    WING_FLAP_CEILING_SCALE,
    FLAP_DESCENT_FUEL_COST_SCALE,
    FLAP_ENTRY_FUEL_COST_SCALE,
]
"""Registered Patches-page sites (9 working sliders).

Retired sliders (kept as module symbols for back-compat / tests,
but no longer surfaced in the GUI or randomizer):

- ``ROLL_SPEED_SCALE`` — roll is animation-root-motion driven.
- ``FLAP_AT_PEAK_SCALE`` — the near-peak cap is latched to
  initial-flap z; byte patches at 0x89381 and 0x8939F landed
  cleanly but the engine re-derives the cap downstream.
- ``CLIMB_SPEED_SCALE`` — constant at VA 0x1980E4 IS the climb
  velocity scalar, but the climbing animation appears to drive
  position via root motion similar to roll.
- ``SLOPE_SLIDE_SPEED_SCALE`` — constant at VA 0x1AAB68 is only
  the state-3 (slow slide) scalar; state-4 (fast slide, common
  on steep descents) uses a dynamic 500x multiplier we can't
  scale with a byte rewrite.

All four need C shims that intercept either animation root
motion or runtime state-derived velocity.  See
docs/LEARNINGS.md § "Retired physics patches"."""


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
    wing_flap_ceiling_scale: float | None = None,
    flap_descent_fuel_cost_scale: float | None = None,
    flap_entry_fuel_cost_scale: float | None = None,
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
        wing_flap_ceiling_scale=wing_flap_ceiling_scale,
        flap_descent_fuel_cost_scale=flap_descent_fuel_cost_scale,
        flap_entry_fuel_cost_scale=flap_entry_fuel_cost_scale,
    )


FEATURE = register_feature(Feature(
    name="player_physics",
    description=(
        "Scales world gravity and player movement: walk, swim, "
        "jump, air-control, and wing-flap (impulse, altitude "
        "ceiling, descent fuel drain)."
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
    "WING_FLAP_CEILING_SCALE",
    "FLAP_DESCENT_FUEL_COST_SCALE",
    "FLAP_ENTRY_FUEL_COST_SCALE",
    "apply_air_control_speed",
    "apply_climb_speed",
    "apply_flap_at_peak",
    "apply_flap_descent_fuel_cost",
    "apply_flap_entry_fuel_cost",
    "apply_flap_height",
    "apply_flap_subsequent",
    "apply_jump_speed",
    "apply_player_physics",
    "apply_player_speed",
    "apply_slope_slide_speed",
    "apply_swim_speed",
    "apply_wing_flap_ceiling",
]
