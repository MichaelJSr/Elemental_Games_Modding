"""60 FPS unlock patch definitions and application logic for Azurik.

All patch sites are declared in `FPS_PATCH_SITES` — a single list of
`PatchSpec` entries that serves as the source of truth for:

  * `apply_fps_patches()` (this module)
  * the `--verify-patched` CLI command
  * external scanning tools (Scripts/analysis/scan_xbe_constants.py, etc.)

Do not fork the byte literals elsewhere; import `FPS_PATCH_SITES` instead.
"""

from __future__ import annotations

from azurik_mod.patching import PatchSpec, apply_patch_spec
from azurik_mod.patching.registry import PatchPack, register_pack

# ---------------------------------------------------------------------------
# 60 FPS unlock — three independent caps must be lifted:
#
# A. Render cap (manual VBlank loop): FUN_0008fbe0 (present wrapper) waits
#    for 2 VBlanks between presents via
#      ADD ECX,2; CMP EAX,ECX; JNC done; BlockUntilVerticalBlank
#    At 60 Hz display refresh this forces 30 fps rendering.
#    Patch 1a lowers N from 2 to 1 → 60 fps target.
#
# B. Render cap (D3D hardware VSync): FUN_001262d0 (buffer flip, called by
#    D3DDevice_Present) writes NV2A push buffer value 0x304 (VSync-on-flip).
#    On real hardware, VSync completes near-instantly because the manual loop
#    already waited for a VBlank.  In xemu the NV2A VSync may be emulated as
#    a synchronous CPU block, adding a SECOND ~16.67 ms wait per frame on top
#    of the manual loop — producing 30/60 fps oscillation and audio desync.
#    Patch 1b forces the Immediate path (value 0x300), eliminating the
#    double-wait while the manual VBlank loop remains the sole frame pacer.
#
# C. Simulation cap: FUN_00058e40 (main loop) calculates simulation steps as:
#      steps = TRUNC((delta - remainder) * rate)   — clamped to [1, 2]
#    then runs each step with fixed dt.  The "remainder" at [ESP+0x40] is a
#    Bresenham-style error term written by the catchup path to absorb frame
#    hitches.  The cap of 2 matches the shipped game's own hardcoded limit.
#
#    IMPORTANT NOTE ON THE CAP VALUE:
#    The cap is held at 2 to match vanilla reentrancy — it is NOT a fix for
#    any particular BSOD.  Empirically, the on-death BSOD can also occur on
#    completely unpatched 30fps Azurik, so it is a pre-existing engine bug
#    that this patch does not attempt to solve.  Raising the cap (e.g. to 4)
#    would drive the engine past 2 consecutive calls/frame, which is more
#    reentrancy than the shipped game ever produces and therefore strictly
#    more risky, not less.  Leave this at 2.
# ---------------------------------------------------------------------------

# --- Shared byte blobs for the most common constants ----------------------
_FLOAT_1_30 = bytes([0x89, 0x88, 0x08, 0x3D])                         # 0.033333335
_FLOAT_1_60 = bytes([0x89, 0x88, 0x88, 0x3C])                         # 0.016666668
_FLOAT_30   = bytes([0x00, 0x00, 0xF0, 0x41])                         # 30.0
_FLOAT_60   = bytes([0x00, 0x00, 0x70, 0x42])                         # 60.0

# Tier 1-5 subsystems that store a private float 1/30 in .rdata for
# per-call timesteps / scheduler intervals.  Each must halve at 60fps.
_SUBSYSTEM_1_30_SITES = [
    # Tier 1 — High Impact (visual smoothness / game speed)
    ("camera",          0x1981C8),  # 8 xrefs — also fixes min-timestep floor
    ("animation",       0x198628),  # 10 xrefs
    ("physics",         0x198688),  # 10 xrefs
    ("character_fsm",   0x1980A0),  # 11 xrefs
    # Tier 2 — Medium Impact (gameplay feel)
    ("entity_init",     0x198410),  # 5 xrefs
    ("player_ctrl",     0x198560),  # 2 xrefs
    ("lod_blend",       0x1981E0),  # 6 xrefs
    ("movement",        0x19873C),  # 6 xrefs
    # Tier 3 — Scheduler Intervals
    ("timer_cooldown",  0x198120),  # 1 xref
    ("effect_sched",    0x1981F0),  # 2 xrefs
    ("world_sched",     0x198228),  # 1 xref
    ("minor_sched",     0x1985D0),  # 1 xref
    ("anim_blend",      0x198700),  # 4 xrefs
    ("per_tick_accum",  0x198758),  # 5 xrefs
    ("fsm_integration", 0x198788),  # 3 xrefs
    ("sched_requant",   0x198AB0),  # 2 xrefs
    ("anim_blend2",     0x1A2740),  # 4 xrefs
    # Tier 4 — Newly Discovered (previously thought dead)
    ("object_update",   0x1981B8),  # 2 xrefs
    ("entity_setup",    0x198968),  # 1 xref
    ("timestep_accum",  0x1989A8),  # 2 xrefs
    ("state_reset",     0x198C98),  # 2 xrefs
    ("critter_ai_timer",0x198660),  # 1 xref — critter AI state transitions
    ("anim_event_sched",0x198580),  # 2 xrefs — animation event scheduling dt
    # Tier 5 — Effect configs (0 Ghidra xrefs, but direct FPU loads exist
    # in .text — verified via raw-byte scan of default.xbe, each site has
    # at least one `FLD m32` or `FSUB m32` against its address).  Safe to
    # treat as dt; Ghidra's auto-analysis just hadn't hit these paths.
    ("effect_config_1", 0x198138),  # FLD m32 @ VA 0x8167E + 0x8170A
    ("effect_config_2", 0x1985B8),  # FSUB m32 @ VA 0x46F44
    ("effect_config_3", 0x1986E8),  # MOV [m32]->EAX @ VA 0x36182 (float-by-dword)
    ("effect_config_4", 0x1989C8),  # FLD m32 @ VA 0x27407; MOV @ 0x273B5
    ("effect_config_5", 0x198A38),  # FLD m32 @ VA 0x25D85; MOV @ 0x25D15
]

# Additional 30.0 constants that act as fps-rate multipliers outside the
# main rate table (HUD anim scroll, keyframe iteration).
_RATE_30_SITES = [
    ("hud_frame_conv",  0x198A74),  # 1 xref — HUD anim scroll
    ("anim_keyframe",   0x198B7C),  # 1 xref — keyframe iteration
]

# Angular xref redirects — 4 of the 20 uses of shared float 30.0 at
# 0x1A2650 compute "30 degrees" for collision/physics geometry and MUST
# keep reading 30.0.  Redirect their address operand to a naturally dead
# 30.0 copy at VA 0x1A2524.
_ANGULAR_ADDR_ORIG = bytes([0x50, 0x26, 0x1A, 0x00])   # LE 0x001A2650
_ANGULAR_ADDR_PATCH = bytes([0x24, 0x25, 0x1A, 0x00])   # LE 0x001A2524
_ANGULAR_REDIRECT_SITES = [
    ("frustum_cone",    0x4E9D9),   # FUN_0004e870 sin/cos(30 deg)
    ("projectile_rot",  0x89AAC),   # FUN_00089a70 projectile physics
    ("static_init_1",   0xFB518),   # C++ static init thunk
    ("static_init_2",   0xFB608),   # C++ static init thunk
]

# ---------------------------------------------------------------------------
# Master list: every 60fps patch, in the order it should be applied.
# ---------------------------------------------------------------------------
FPS_PATCH_SITES: list[PatchSpec] = [
    # --- Render cap -------------------------------------------------------
    # Patch 1a: VBlank wait 2→1 (ADD ECX, imm8 at VA 0x8FD19).
    # Present wrapper waits until currentVBlank >= lastVBlank + N.
    # N=2 → 30 fps, N=1 → 60 fps (one VBlank per present, still VSync'd).
    # After Patch 1b disables D3D VSync, this manual loop is the sole pacer.
    PatchSpec(
        label="60 FPS VBlank wait (2->1 per present)",
        va=0x08FD19,
        original=bytes([0x83, 0xC1, 0x02]),   # ADD ECX, 0x2
        patch=bytes([0x83, 0xC1, 0x01]),       # ADD ECX, 0x1
    ),

    # Patch 1b: Disable D3D hardware VSync in Present (JNZ at VA 0x12635D).
    # D3DDevice_Present (via FUN_001262d0) writes push buffer 0x304
    # (VSync-on-flip) unless PresentationInterval == IMMEDIATE.  In xemu
    # the NV2A VSync may be emulated as a synchronous CPU block, adding a
    # second ~16.67 ms wait on top of the manual VBlank loop — producing
    # 30/60 fps oscillation and audio desync.  NOPing the JNZ forces the
    # Immediate path (0x300); the subsequent JMP +7 still skips the 0x304
    # branch, so the GPU flips without an extra VSync wait.
    PatchSpec(
        label="60 FPS disable D3D Present VSync (fix double-wait)",
        va=0x12635D,
        original=bytes([0x75, 0x09]),          # JNZ +9  (take VSync 0x304 path)
        patch=bytes([0x90, 0x90]),             # NOP NOP (fall through → 0x300)
    ),

    # --- Main-loop timing -------------------------------------------------
    # Patch 2: rate multiplier 30.0 → 60.0 (double at VA 0x1A28C8, .rdata).
    PatchSpec(
        label="60 FPS rate multiplier (30->60)",
        va=0x1A28C8,
        original=bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x3E, 0x40]),  # double 30.0
        patch=bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x4E, 0x40]),     # double 60.0
        is_data=True,
    ),

    # Patch 3: fixed timestep 1/30 → 1/60 (float at VA 0x1983E8, .rdata).
    PatchSpec(
        label="60 FPS timestep (1/30->1/60)",
        va=0x1983E8,
        original=_FLOAT_1_30,
        patch=_FLOAT_1_60,
        is_data=True,
    ),

    # Patch 4: FISTP truncation + max step clamp (VA 0x59AFD, 58 bytes).
    #
    # The original code uses FISTP with round-to-nearest-even to compute
    #   steps = ROUND(delta * rate)
    # At 60 fps, a frame taking just over 25 ms (delta*60 = 1.5) gives
    # ROUND(1.5) = 2, doubling simulation workload.  The extra CPU cost
    # pushes the next frame past 25 ms as well — a self-reinforcing
    # feedback loop that locks the game at exactly 30 fps.  No intermediate
    # rates are stable: the system is bistable at 60 (1 step) and 30 (2).
    #
    # Fix: temporarily switch the x87 FPU to truncation mode (RC=11) before
    # FISTP, then restore the original rounding mode.  With truncation,
    # TRUNC(1.5)=1 and TRUNC(1.99)=1; the step count only reaches 2 when
    # delta*60 >= 2.0 (frame time >= 33.33 ms) — the mathematically correct
    # threshold.
    #
    # CAP = 4  (matches the original 30fps game's behaviour in wall-clock
    # terms).  Vanilla caps at 2 steps at 30 Hz sim, i.e. catches up for
    # render frames as slow as 15 FPS.  At 60 Hz sim we need cap=4 to
    # cover the same real-time window (15 FPS render → 4 steps/frame →
    # sim still runs at 60 Hz = full game speed).  A lower cap causes
    # the game to slow down whenever the renderer dips below 30 FPS,
    # which was reported in live testing.
    #
    # safety_critical=True: the exact cap byte must stay in sync with
    # Patch 5 (CATCHUP) — both the truncation clamp and the catchup
    # remainder computation need to agree or the timestep accumulator
    # will drift.  The on-death BSOD that originally motivated a cap=2
    # experiment also reproduces on vanilla 30fps Azurik, so it is a
    # pre-existing engine bug unrelated to this cap.
    PatchSpec(
        label="60 FPS FISTP truncation + step clamp (cap=4)",
        va=0x059AFD,
        original=bytes([
            0xDD, 0x5C, 0x24, 0x60,                            # FSTP double [ESP+0x60]
            0xDD, 0x44, 0x24, 0x60,                            # FLD double [ESP+0x60]
            0xDB, 0x5C, 0x24, 0x30,                            # FISTP dword [ESP+0x30]
            0x8B, 0x44, 0x24, 0x30,                            # MOV EAX, [ESP+0x30]
            0x89, 0x44, 0x24, 0x14,                            # MOV [ESP+0x14], EAX
            0xC7, 0x44, 0x24, 0x60, 0x01, 0x00, 0x00, 0x00,    # MOV dword [ESP+0x60], 1
            0x8B, 0x44, 0x24, 0x14,                            # MOV EAX, [ESP+0x14]
            0x3B, 0x44, 0x24, 0x60,                            # CMP EAX, [ESP+0x60]
            0x0F, 0x4C, 0x44, 0x24, 0x60,                      # CMOVL EAX, [ESP+0x60]
            0x89, 0x44, 0x24, 0x68,                            # MOV [ESP+0x68], EAX
            0x8B, 0x74, 0x24, 0x68,                            # MOV ESI, [ESP+0x68]
            0x83, 0xFE, 0x02,                                  # CMP ESI, 0x2
            0x89, 0x74, 0x24, 0x14,                            # MOV [ESP+0x14], ESI
            0x7E, 0x36,                                        # JLE 0x59B6D
        ]),
        patch=bytes([
            # --- Save FPU control word, set truncation mode ---
            0xD9, 0x7C, 0x24, 0x60,                            # FNSTCW [ESP+0x60]
            0x66, 0x8B, 0x44, 0x24, 0x60,                      # MOV AX, [ESP+0x60]
            0x66, 0x0D, 0x00, 0x0C,                            # OR AX, 0x0C00  (RC=11 truncate)
            0x66, 0x89, 0x44, 0x24, 0x62,                      # MOV [ESP+0x62], AX
            0xD9, 0x6C, 0x24, 0x62,                            # FLDCW [ESP+0x62]
            # --- Truncate delta*rate to integer ---
            0xDB, 0x5C, 0x24, 0x30,                            # FISTP dword [ESP+0x30]
            # --- Restore original FPU rounding mode ---
            0xD9, 0x6C, 0x24, 0x60,                            # FLDCW [ESP+0x60]
            # --- Clamp to [1, 4], store, and branch ---
            0x8B, 0x74, 0x24, 0x30,                            # MOV ESI, [ESP+0x30]
            0x83, 0xFE, 0x01,                                  # CMP ESI, 1
            0x7D, 0x05,                                        # JGE +5 (skip min clamp)
            0xBE, 0x01, 0x00, 0x00, 0x00,                      # MOV ESI, 1
            0x89, 0x74, 0x24, 0x14,                            # MOV [ESP+0x14], ESI
            0x83, 0xFE, 0x04,                                  # CMP ESI, 0x4  <-- step cap
            0x7E, 0x3B,                                        # JLE 0x59B6D (+0x3B from here)
            0x90, 0x90, 0x90, 0x90, 0x90,                      # 5x NOP (fill to 58 bytes)
        ]),
        safety_critical=True,
    ),

    # Patch 5: catchup code — cap to 4 steps, keep remainder (VA 0x59B37).
    #
    # The main loop uses a Bresenham-style remainder at [ESP+0x40] for
    # hitch recovery.  When a frame hitch causes steps > max, the catchup
    # block:
    #   1. Caps ESI to the max step count (4)
    #   2. Computes remainder = raw_delta - max_steps * dt
    # On the next frame, the FSUB at 0x59AF3 subtracts this remainder from
    # delta, which immediately restores 1-step-per-frame operation.
    #
    # Original: steps=2, remainder = raw_delta - 2*dt (dt=1/30)
    #           -> covers render rates down to 15 FPS with full game speed.
    # Patched:  steps=4, remainder = raw_delta - 4*dt (dt=1/60)
    #           -> same 15-FPS coverage at 60 Hz sim (4*(1/60) == 2*(1/30)).
    #
    # safety_critical=True: the cap of 4 must match Patch 4's CMP ESI, 4
    # so the TRUNC pre-check and the catchup post-check agree.  The on-
    # death BSOD that sometimes triggered during experiments with cap=2
    # also reproduces on vanilla 30fps Azurik — it is a pre-existing
    # engine bug unrelated to the sim cap.
    #
    # ESI is loaded via PUSH 0x4 / POP ESI (byte-equivalent substitute
    # for MOV ESI, 4) so the 30-byte block length is preserved.  Two
    # FADD ST0,ST0 instructions compute 4*dt (dt=1/60 -> 4/60 = 1/15),
    # matching the cap.
    PatchSpec(
        label="60 FPS catchup (ESI=4, remainder=raw_delta-4*dt)",
        va=0x059B37,
        original=bytes([
            0xD9, 0x05, 0xE8, 0x83, 0x19, 0x00,                # FLD float ptr [0x1983E8]
            0xBE, 0x02, 0x00, 0x00, 0x00,                      # MOV ESI, 0x2
            0xDC, 0xC0,                                         # FADD ST0, ST0
            0x89, 0x74, 0x24, 0x14,                             # MOV [ESP+0x14], ESI
            0xDC, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00,          # FSUBR double [ESP+0x80]
            0xDD, 0x5C, 0x24, 0x40,                             # FSTP double [ESP+0x40]
            0xEB, 0x18,                                         # JMP +0x18
        ]),
        patch=bytes([
            0xD9, 0x05, 0xE8, 0x83, 0x19, 0x00,                # FLD float ptr [0x1983E8]  (dt=1/60)
            0x6A, 0x04,                                        # PUSH 0x4     <-- step cap
            0x5E,                                              # POP ESI      (ESI = 4)
            0xDC, 0xC0,                                        # FADD ST0, ST0 (2*dt)
            0xDC, 0xC0,                                        # FADD ST0, ST0 (4*dt)
            0x89, 0x74, 0x24, 0x14,                            # MOV [ESP+0x14], ESI
            0xDC, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00,          # FSUBR double [ESP+0x80]
            0xDD, 0x5C, 0x24, 0x40,                            # FSTP double [ESP+0x40]
            0xEB, 0x18,                                        # JMP +0x18
        ]),
        safety_critical=True,
    ),
]

# ---------------------------------------------------------------------------
# Patches 7+: Subsystem .rdata 1/30 → 1/60 constants
# ---------------------------------------------------------------------------
# The engine duplicates the 1/30 (0.033333335) float constant across .rdata
# for each subsystem (camera, animation, physics, FSM, scheduler, etc.).
# Each copy is used as a per-call timestep or scheduler interval.  At 60
# fps these subsystems are invoked twice as often, so each constant must
# be halved to preserve wall-clock behaviour.
#
# Known limitation — FUN_00043a00 blend math:
#   Computes [0x198628] * [0x1A2740] = (1/30)*(1/30) = 1/900.
#   After patching both to 1/60 the product is 1/3600; at 60 fps (2x calls/sec)
#   the net blend rate is half the original wall-time rate.  Layered animation
#   transitions may take ~2x longer.  Fixing this would require code injection
#   to replace one factor with a separate constant.
#
# Known limitation — scheduler quantum:
#   FUN_000ab830 reads a per-context quantum from [ctx+0xC] that is
#   initialised at runtime, not from a static .rdata pool.  Cannot be fixed
#   by static binary patching; scheduler time-snapping may round
#   differently at 60 Hz.
#
# Known limitation — camera per-frame damping:
#   Camera lerp factors (e.g. lerp(old, target, factor)) lack * dt scaling
#   and are buried in virtual dispatch chains.  Camera smoothing may feel
#   slightly different at 60 fps.
#
# Verified safe — input polling (FUN_000a2df0 + FUN_000a2880):
#   XInputGetState is called at the outer-loop rate (now 60 Hz).  The poll
#   path is stateless: raw analog sticks / triggers / button bits are
#   written directly to float arrays at DAT_0037BE98+ with no frame
#   counter and no edge detection.  Consumers do their own (curr & ~prev)
#   edge detection, so button presses cannot double-fire from the poll
#   rate alone.  Menu auto-repeat timers (if any) live in UI code that
#   consumes the state — those may repeat 2x at 60 fps; see scan_int30
#   for candidate imm=0x1E countdown constants in .text.
for _name, _va in _SUBSYSTEM_1_30_SITES:
    FPS_PATCH_SITES.append(PatchSpec(
        label=f"60 FPS subsystem dt {_name} (1/30->1/60)",
        va=_va,
        original=_FLOAT_1_30,
        patch=_FLOAT_1_60,
        is_data=True,
    ))

# Patch: double 1/30 → 1/60 for animation time accumulators (VA 0x1A2750).
# FUN_00066D00 and FUN_00066D70 add double 1/30 per frame to animation
# scheduler clocks.  At 60fps they fire every 16.67ms, so the advance
# must be 1/60 to maintain real-time parity.
FPS_PATCH_SITES.append(PatchSpec(
    label="60 FPS anim scheduler double (1/30->1/60)",
    va=0x1A2750,
    original=bytes([0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0xA1, 0x3F]),  # double 1/30
    patch=bytes([0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x91, 0x3F]),     # double 1/60
    is_data=True,
))

# Patches: float 30.0 → 60.0  (fps-rate multipliers outside the main table).
for _name, _va in _RATE_30_SITES:
    FPS_PATCH_SITES.append(PatchSpec(
        label=f"60 FPS rate multiplier {_name} (30->60)",
        va=_va,
        original=_FLOAT_30,
        patch=_FLOAT_60,
        is_data=True,
    ))

# Patch: shared float 30.0 → 60.0 + angular xref redirects (VA 0x1A2650).
# 20 xrefs share float 30.0 at VA 0x1A2650.  16 are fps-dependent and need
# 60.0.  4 compute "30 degrees" (deg2rad * 30) for collision/physics
# geometry and MUST keep reading 30.0.  Solution: patch 0x1A2650 to 60.0,
# redirect the 4 angular instructions to read from VA 0x1A2524 — a
# naturally dead float 30.0 in .rdata (0 xrefs).
FPS_PATCH_SITES.append(PatchSpec(
    label="60 FPS shared velocity constant (30->60)",
    va=0x1A2650,
    original=_FLOAT_30,
    patch=_FLOAT_60,
    is_data=True,
))
for _name, _va in _ANGULAR_REDIRECT_SITES:
    FPS_PATCH_SITES.append(PatchSpec(
        label=f"60 FPS angular redirect {_name} (keep 30deg)",
        va=_va,
        original=_ANGULAR_ADDR_ORIG,
        patch=_ANGULAR_ADDR_PATCH,
    ))

# Patch: D3D Present spin-wait bypass (VA 0x1263E2, D3D section).
# D3DDevice_Present has a spin-wait that blocks when outstanding GPU flips
# >= 2.  Even with immediate NV2A flips (Patch 1b), xemu may tie the fence
# completion counter to VBlank timing, adding up to 16.67 ms stall per
# frame.  Changing JC (0x72) to JMP short (0xEB) always skips the
# spin-wait.  The relative offset (+0x18) is unchanged, so execution lands
# at the INC + flip path.
FPS_PATCH_SITES.append(PatchSpec(
    label="60 FPS disable Present spin-wait (fix frame stall)",
    va=0x1263E2,
    original=bytes([0x72]),                    # JC rel8
    patch=bytes([0xEB]),                       # JMP rel8
))

# Patch: flash/sparkle timer (VA 0x19862C, .rdata).
# FUN_0003ea00 increments a per-render-frame timer by float 1/6 and also
# divides by the same constant for fade normalisation.  At 60fps the timer
# runs 2x fast; halving to 1/12 restores the correct real-time duration.
FPS_PATCH_SITES.append(PatchSpec(
    label="60 FPS flash timer (1/6->1/12)",
    va=0x19862C,
    original=bytes([0xAB, 0xAA, 0x2A, 0x3E]),  # float 1/6
    patch=bytes([0xAB, 0xAA, 0xAA, 0x3D]),     # float 1/12
    is_data=True,
))

# Patch: collision solver bounce limit (VA 0x47EEF, .text).
# FUN_00047380 counts wall bounces per frame.  At 60 fps the halved sweep
# requires more bounces; raising 2→4 gives the same real-time budget.
#
# Verified safe — disassembly at 0x47EED/0x47EEE shows `INC EAX; CMP EAX, 2`,
# i.e. a plain loop iteration count (not a buffer size / array index).
# Raising the cap to 4 only increases the per-frame work budget; no
# buffer overrun risk.
FPS_PATCH_SITES.append(PatchSpec(
    label="60 FPS collision bounce limit (2->4)",
    va=0x47EEF,
    original=bytes([0x02]),                    # CMP EAX, 0x2
    patch=bytes([0x04]),                       # CMP EAX, 0x4
))

# Patch: ground probe constant + redirect (VA 0x1A2690 / 0x86162).
# FUN_00085f50 (ground walking state) casts a downward probe 0.1 units
# below the sweep result.  The velocity contribution doubles at 60 fps;
# halving the offset to 0.05 restores original behaviour.  The .rdata
# slot at 0x1A2690 was padding; we write 0.05 there and retarget the
# instruction at 0x86162 to read from it.
FPS_PATCH_SITES.append(PatchSpec(
    label="60 FPS ground probe constant (write 0.05)",
    va=0x1A2690,
    original=bytes([0x00, 0x00, 0x00, 0x00]),  # unused padding
    patch=bytes([0xCD, 0xCC, 0x4C, 0x3D]),     # float 0.05
    is_data=True,
))
FPS_PATCH_SITES.append(PatchSpec(
    label="60 FPS ground probe redirect (0.1->0.05)",
    va=0x86162,
    original=bytes([0x74, 0x26, 0x1A, 0x00]),  # LE addr 0x001A2674 (0.1)
    patch=bytes([0x90, 0x26, 0x1A, 0x00]),     # LE addr 0x001A2690 (0.05)
))

# Patches: collision solver impulse scaling (FUN_00047380).
# NOP-ing the 2x doubling and halving the cap makes the impulse identical
# to 30 fps: min(L, cap/2)/(1/60) matches min(2L, cap)/(1/30).
#
# Verified safe — decompile of FUN_00047380 shows the cap is compared
# against `L + L` (FADD ST0,ST0 on a 1D length `local_174`), NOT against
# L² or a squared magnitude.  The cap is therefore a linear threshold;
# halving it is algebraically correct.  Both FADD sites (0x47BC6 /
# 0x47CF3) operate on the same scalar length, so both get NOP'd.
FPS_PATCH_SITES.append(PatchSpec(
    label="60 FPS solver impulse NOP doubling (branch 1)",
    va=0x47BC6,
    original=bytes([0xDC, 0xC0]),              # FADD ST0,ST0
    patch=bytes([0x90, 0x90]),                 # NOP NOP
))
FPS_PATCH_SITES.append(PatchSpec(
    label="60 FPS solver impulse NOP doubling (branch 2)",
    va=0x47CF3,
    original=bytes([0xDC, 0xC0]),              # FADD ST0,ST0
    patch=bytes([0x90, 0x90]),                 # NOP NOP
))
FPS_PATCH_SITES.append(PatchSpec(
    label="60 FPS solver correction cap (0.001->0.0005)",
    va=0x1AA230,
    original=bytes([0x6F, 0x12, 0x83, 0x3A]),  # float ~0.001
    patch=bytes([0x6F, 0x12, 0x03, 0x3A]),     # float ~0.0005
    is_data=True,
))


# ---------------------------------------------------------------------------
# Convenience views (derived — do not duplicate byte literals here).
# ---------------------------------------------------------------------------

# VAs of .rdata/.data float/double constants that the 60fps patch touches.
# Used by scan_xbe_constants.py to mark which data-section hits are
# accounted for.
FPS_DATA_PATCHED_VAS: set[int] = {s.va for s in FPS_PATCH_SITES if s.is_data}

# VAs of patches that must never regress (BSOD guards, etc).
FPS_SAFETY_CRITICAL_SITES: list[PatchSpec] = [
    s for s in FPS_PATCH_SITES if s.safety_critical
]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def apply_fps_patches(xbe_data: bytearray) -> None:
    """Apply every 60 FPS unlock patch in FPS_PATCH_SITES to `xbe_data`."""
    for spec in FPS_PATCH_SITES:
        apply_patch_spec(xbe_data, spec)


# ---------------------------------------------------------------------------
# Register with the central patch-pack registry
# ---------------------------------------------------------------------------
register_pack(PatchPack(
    name="fps_unlock",
    description=(
        "Run the game at 60 FPS instead of 30.  Experimental: some "
        "animations and physics may feel subtly different from vanilla, "
        "and older saves should still be compatible."
    ),
    sites=FPS_PATCH_SITES,
    apply=apply_fps_patches,
    default_on=False,
    included_in_randomizer_qol=False,
    tags=("fps", "experimental"),
))
