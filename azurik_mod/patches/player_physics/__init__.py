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

# WHITE-button edge-lock inside FUN_00084f90.  The 2-byte ``JNZ +8``
# at VA 0x00085200 tests ``piVar7[0x4d]`` (the WHITE edge-lock byte)
# and, when set, skips the flag-setting code path — making WHITE a
# one-frame pulse.  NOPing this JNZ makes WHITE-held produce a
# SUSTAINED magnitude boost for every frame the button is down.
_ROLL_EDGE_LOCK_VA = 0x00085200
_ROLL_EDGE_LOCK_VANILLA = bytes.fromhex("7508")   # JNZ +8
_ROLL_EDGE_LOCK_PATCH = bytes.fromhex("9090")     # NOP NOP

# Force-always-on override.  Even after the edge-lock NOP, bit 0x40
# still needs ONE of WHITE or RIGHT_THUMB (R3 click) to be pressed —
# which some users' xemu input configs don't wire up at all, making
# ``roll_scale`` invisible to them.  To guarantee the slider is
# observable in gameplay, we additionally patch the two-instruction
# tail of the bit-0x40 XOR-update block to force DL |= 0x40 every
# frame:
#
#   VA 0x85214: ``24 40`` (AND AL, 0x40) -> ``B0 40`` (MOV AL, 0x40)
#     - Now AL is always 0x40, regardless of button state.
#   VA 0x8521C: ``32 D0`` (XOR DL, AL) -> ``0A D0`` (OR  DL, AL)
#     - XOR would TOGGLE bit 0x40 per frame (causing flicker).
#       Switching to OR makes bit 0x40 sticky-set — written to
#       [ESI+0x20] as "on" every frame.
#
# Each is a 2-byte byte-for-byte rewrite; other bits in the flag
# byte are untouched (each bit 0x01..0x20 has its own XOR-update
# block earlier in FUN_00084f90 with its own MOV DL, BL / XOR DL).
_ROLL_FORCE_ON_1_VA = 0x00085214
_ROLL_FORCE_ON_1_VANILLA = bytes.fromhex("2440")   # AND AL, 0x40
_ROLL_FORCE_ON_1_PATCH   = bytes.fromhex("b040")   # MOV AL, 0x40

_ROLL_FORCE_ON_2_VA = 0x0008521C
_ROLL_FORCE_ON_2_VANILLA = bytes.fromhex("32d0")   # XOR DL, AL
_ROLL_FORCE_ON_2_PATCH   = bytes.fromhex("0ad0")   # OR  DL, AL

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
    # Back-compat alias: callers that still pass run_scale get
    # transparently routed to roll_scale (the new name).
    run_scale: float | None = None,
    **_ignored,
) -> None:
    """Apply the XBE-side portion of the player physics pack.

    All seven adjustments operate on ``default.xbe`` directly.
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

    # --- Safety: ensure vanilla bytes at the sites we plan to touch.
    # When roll_scale == 1.0 we leave the roll site alone entirely
    # (no FMUL rewrite, no force-on), so we don't need to check its
    # bytes — and leaving them at vanilla means the roll FMUL keeps
    # its original WHITE/R3-gated behaviour as a pure no-op (×1.0
    # would be equivalent anyway but saves a dynamic_whitelist
    # entry).
    if walk_scale != 1.0 and (
        bytes(xbe_data[walk_off:walk_off + 6]) != _WALK_SITE_VANILLA
    ):
        print(f"  WARNING: player_speed — walk site at VA "
              f"0x{_WALK_SITE_VA:X} already patched or drifted, "
              f"skipping (got "
              f"{bytes(xbe_data[walk_off:walk_off + 6]).hex()})")
        return False
    if roll_scale != 1.0 and (
        bytes(xbe_data[roll_off:roll_off + 6]) != _ROLL_SITE_VANILLA
    ):
        print(f"  WARNING: player_speed — roll site at VA "
              f"0x{_ROLL_SITE_VA:X} already patched or drifted, "
              f"skipping (got "
              f"{bytes(xbe_data[roll_off:roll_off + 6]).hex()})")
        return False

    # --- Inject our two per-player floats -----------------------------
    # Engine formulas (paired with our force-always-on roll patch
    # applied further below):
    #     velocity = inject_base × magnitude × direction
    #     magnitude = raw_stick × (inject_roll_mult when flag set,
    #                              else 1.0)
    # With force-always-on making the flag perpetually set and our
    # simple slider semantics:
    #     walk_scale = walking-speed multiplier
    #     roll_scale = EXTRA walking-speed multiplier (stacks)
    # we choose:
    #     inject_base      = _VANILLA_PLAYER_BASE_SPEED × walk_scale
    #     inject_roll_mult = roll_scale    (NOT 3 × roll_scale;
    #                                       the 3× "roll" factor was
    #                                       the original WHITE-button
    #                                       boost — by redirecting
    #                                       the FMUL target to our
    #                                       injected constant, we
    #                                       replace the 3× with a
    #                                       clean user-controlled
    #                                       multiplier.)
    # Result:
    #     walking = 7 × walk_scale × roll_scale × raw_stick × direction
    # Short-circuit at (walk=1, roll=1) preserves vanilla bytes.
    safe_walk_scale = max(_WALK_SCALE_MIN, float(walk_scale))
    inject_base = _VANILLA_PLAYER_BASE_SPEED * safe_walk_scale
    inject_roll_mult = float(roll_scale)

    # Only patch the walk site when walk_scale != 1.0, and only
    # patch the roll site when roll_scale != 1.0.  Keeps the
    # "one slider changed, other site unchanged" invariant that
    # makes verify-patches --strict diffs clean and lets users
    # opt into one axis at a time without collateral bytes.
    walk_va = None
    roll_va = None
    if walk_scale != 1.0:
        walk_value_bytes = struct.pack("<f", inject_base)
        _, walk_va = _carve_shim_landing(xbe_data, walk_value_bytes)
        # FLD dword [abs walk_va]   encoded as D9 05 <va>
        xbe_data[walk_off:walk_off + 6] = (
            b"\xD9\x05" + struct.pack("<I", walk_va))
    if roll_scale != 1.0:
        roll_value_bytes = struct.pack("<f", inject_roll_mult)
        _, roll_va = _carve_shim_landing(xbe_data, roll_value_bytes)
        # FMUL dword [abs roll_va]   encoded as D8 0D <va>
        xbe_data[roll_off:roll_off + 6] = (
            b"\xD8\x0D" + struct.pack("<I", roll_va))

    # --- When roll_scale != 1.0, also disable the WHITE-button
    # edge-lock AND force bit 0x40 to be set every frame so the
    # slider is visible in gameplay regardless of the user's
    # controller configuration (some xemu input configs don't
    # wire WHITE / R3 at all, which would make the slider
    # invisible otherwise).
    if roll_scale != 1.0:
        # 1. NOP the WHITE edge-lock so if the user DOES press
        # WHITE, it gives sustained (not one-frame) boost.
        el_off = va_to_file(_ROLL_EDGE_LOCK_VA)
        el_current = bytes(xbe_data[el_off:el_off + 2])
        if el_current == _ROLL_EDGE_LOCK_VANILLA:
            xbe_data[el_off:el_off + 2] = _ROLL_EDGE_LOCK_PATCH
        elif el_current != _ROLL_EDGE_LOCK_PATCH:
            print(f"  WARNING: player_speed — roll edge-lock at VA "
                  f"0x{_ROLL_EDGE_LOCK_VA:X} drifted (got "
                  f"{el_current.hex()}); skipping that sub-patch.")

        # 2. Force-always-on: make bit 0x40 set every frame
        # regardless of button state.  Two tiny rewrites in the
        # XOR-update tail of FUN_00084f90 produce ``DL |= 0x40``
        # as the final write to [ESI+0x20].  Without this, users
        # whose xemu config doesn't route WHITE/R3 correctly
        # would see no effect from the slider.
        f1_off = va_to_file(_ROLL_FORCE_ON_1_VA)
        f2_off = va_to_file(_ROLL_FORCE_ON_2_VA)
        f1_current = bytes(xbe_data[f1_off:f1_off + 2])
        f2_current = bytes(xbe_data[f2_off:f2_off + 2])
        if (f1_current == _ROLL_FORCE_ON_1_VANILLA
                and f2_current == _ROLL_FORCE_ON_2_VANILLA):
            xbe_data[f1_off:f1_off + 2] = _ROLL_FORCE_ON_1_PATCH
            xbe_data[f2_off:f2_off + 2] = _ROLL_FORCE_ON_2_PATCH
            print(f"  Player roll force-on: enabled — bit 0x40 "
                  f"always set, so magnitude is multiplied by "
                  f"the injected value every frame of movement.")
        elif not (f1_current == _ROLL_FORCE_ON_1_PATCH
                  and f2_current == _ROLL_FORCE_ON_2_PATCH):
            print(f"  WARNING: player_speed — roll force-on sites "
                  f"drifted (f1={f1_current.hex()}, "
                  f"f2={f2_current.hex()}); skipping.")

    if walk_va is not None:
        print(f"  Player walk speed: {walk_scale:.3f}x vanilla  "
              f"(injected base = {inject_base:.3f}, "
              f"VA 0x{walk_va:X})")
    if roll_va is not None:
        print(f"  Player roll speed: {roll_scale:.3f}x vanilla  "
              f"(injected roll mult = "
              f"{inject_roll_mult:.3f}, VA 0x{roll_va:X})")
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

    # Static: the four 6-byte instruction rewrite sites + the
    # 2-byte roll edge-lock JNZ + two 2-byte roll always-on patches
    # are always whitelisted.  On a vanilla XBE these ranges hold
    # their pristine bytes so the diff reports zero, keeping
    # whitelisting safe.
    ranges: list[tuple[int, int]] = [
        (walk_off, walk_off + 6),
        (roll_off, roll_off + 6),
        (swim_off, swim_off + 6),
    ]
    try:
        jump_off = va_to_file(_JUMP_SITE_VA)
        ranges.append((jump_off, jump_off + 6))
    except Exception:  # noqa: BLE001
        pass
    try:
        el_off = va_to_file(_ROLL_EDGE_LOCK_VA)
        ranges.append((el_off, el_off + 2))
    except Exception:  # noqa: BLE001
        pass
    try:
        fon1 = va_to_file(_ROLL_FORCE_ON_1_VA)
        fon2 = va_to_file(_ROLL_FORCE_ON_2_VA)
        ranges.append((fon1, fon1 + 2))
        ranges.append((fon2, fon2 + 2))
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
        (roll_off, b"\xD8\x0D"),   # FMUL [abs32] (roll mult)
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
]
"""Registered Patches-page sites.  Gravity writes to a .rdata float
directly; walk/roll/swim/jump speed sliders are "virtual" (va=0)
and the pack's apply function materialises their values into
injected XBE bytes (walk/roll/swim via shim-landing FLD/FMUL
rewrites, jump via direct imm32 overwrites at five call sites)."""


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
    apply_player_physics(
        xbe_data,
        gravity=gravity,
        walk_scale=walk,
        roll_scale=roll,
        swim_scale=swim,
        jump_scale=jump,
        air_control_scale=air_control_scale,
        flap_scale=flap,
    )


FEATURE = register_feature(Feature(
    name="player_physics",
    description=(
        "Scales world gravity and every player movement / speed "
        "parameter we've RE'd: walking, rolling (WHITE boost "
        "made permanent), swimming, jumping, horizontal air-"
        "control speed, and wing-flap (Air-power double-jump) "
        "impulse.  Gravity is global; the rest are player-only."
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
    "apply_flap_height",
    "apply_jump_speed",
    "apply_player_physics",
    "apply_player_speed",
    "apply_swim_speed",
]
