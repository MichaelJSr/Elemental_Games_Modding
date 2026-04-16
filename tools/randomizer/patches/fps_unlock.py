"""60 FPS unlock patch definitions and application logic for Azurik."""

from __future__ import annotations

from patches.xbe_utils import va_to_file, apply_xbe_patch

# ---------------------------------------------------------------------------
# 60 FPS unlock — three independent caps must be lifted:
#
# XBE section mappings are in xbe_utils.XBE_SECTIONS / va_to_file() — use
# va_to_file(VA) for all offset calculations; never hand-compute.
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
#    hitches.  The cap of 2 matches the original game's maximum — the game's
#    state machine and death transitions are not re-entrant beyond 2 steps
#    per frame, so exceeding this causes memory corruption and Xbox BSODs.
# ---------------------------------------------------------------------------

# Patch 1a: VBlank wait 2→1  (ADD ECX, imm8 at VA 0x8FD19)
# Present wrapper waits until currentVBlank >= lastVBlank + N.
# N=2 → 30 fps, N=1 → 60 fps (one VBlank per present, still VSync'd).
# This manual loop is the SOLE frame pacer after Patch 1b disables D3D VSync.
FPS_VBLANK_OFFSET = va_to_file(0x08FD19)
FPS_VBLANK_ORIGINAL = bytes([0x83, 0xC1, 0x02])  # ADD ECX, 0x2
FPS_VBLANK_PATCH    = bytes([0x83, 0xC1, 0x01])  # ADD ECX, 0x1

# Patch 1b: Disable D3D hardware VSync in Present  (JNZ at VA 0x12635D)
# D3DDevice_Present (via FUN_001262d0) writes NV2A push buffer value 0x304
# (VSync-on-flip) when PresentationInterval != IMMEDIATE.  In xemu this may
# be emulated as a synchronous CPU block, adding a SECOND ~16.67 ms wait on
# top of the manual VBlank loop — producing the observed 30/60 fps oscillation.
# NOPing the JNZ forces the Immediate path (push buffer value 0x300), so the
# GPU flips without an extra VSync wait.  The manual VBlank loop (Patch 1a)
# remains the sole frame pacer.
FPS_PRESENT_VSYNC_OFFSET   = va_to_file(0x12635D)
FPS_PRESENT_VSYNC_ORIGINAL = bytes([0x75, 0x09])    # JNZ +9  (take VSync 0x304 path)
FPS_PRESENT_VSYNC_PATCH    = bytes([0x90, 0x90])    # NOP NOP (fall through → Immediate 0x300)

# Patch 2: rate multiplier 30.0 → 60.0  (double at VA 0x1A28C8, in .rdata)
FPS_RATE_OFFSET = va_to_file(0x1A28C8)
FPS_RATE_ORIGINAL = bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x3E, 0x40])  # double 30.0
FPS_RATE_PATCH    = bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x4E, 0x40])  # double 60.0

# Patch 3: fixed timestep 1/30 → 1/60  (float at VA 0x1983E8, in .rdata)
FPS_DT_OFFSET = va_to_file(0x1983E8)
FPS_DT_ORIGINAL = bytes([0x89, 0x88, 0x08, 0x3D])  # float 0.033333335
FPS_DT_PATCH    = bytes([0x89, 0x88, 0x88, 0x3C])  # float 0.016666668

# Patch 4: FISTP truncation + max step clamp  (VA 0x59AFD, 58 bytes in .text)
#
# The original code uses FISTP with round-to-nearest-even to compute
#   steps = ROUND(delta * rate)
# At 60 fps, when a frame takes just over 25 ms (delta*60 = 1.5),
# ROUND(1.5) = 2, doubling the simulation workload.  The extra CPU cost
# pushes the next frame past 25 ms as well, creating a self-reinforcing
# feedback loop that locks the game at exactly 30 fps (2 steps per frame).
# No intermediate frame rates (40, 45, 50 fps) are stable — the system is
# bistable at 60 fps (1 step) and 30 fps (2 steps).
#
# Fix: temporarily switch the x87 FPU to truncation mode (round toward zero)
# before FISTP, then restore the original rounding mode.  With truncation,
# TRUNC(1.5) = 1, TRUNC(1.99) = 1.  The step count only reaches 2 when
# delta*60 >= 2.0 (frame time >= 33.33 ms), which is the mathematically
# correct threshold.  This eliminates the premature death spiral.
#
# The step cap is kept at 2 (matching the original game) because the game's
# state machine and death transitions are not safe beyond 2 calls per frame.
# Running 3-4 steps causes use-after-free in D3D push buffer descriptors
# during death/level transitions, leading to Xbox kernel BSODs.
FPS_TRUNC_OFFSET = va_to_file(0x059AFD)
FPS_TRUNC_ORIGINAL = bytes([
    0xDD, 0x5C, 0x24, 0x60,                           # FSTP double [ESP+0x60]
    0xDD, 0x44, 0x24, 0x60,                            # FLD double [ESP+0x60]
    0xDB, 0x5C, 0x24, 0x30,                            # FISTP dword [ESP+0x30]
    0x8B, 0x44, 0x24, 0x30,                            # MOV EAX, [ESP+0x30]
    0x89, 0x44, 0x24, 0x14,                            # MOV [ESP+0x14], EAX
    0xC7, 0x44, 0x24, 0x60, 0x01, 0x00, 0x00, 0x00,   # MOV dword [ESP+0x60], 1
    0x8B, 0x44, 0x24, 0x14,                            # MOV EAX, [ESP+0x14]
    0x3B, 0x44, 0x24, 0x60,                            # CMP EAX, [ESP+0x60]
    0x0F, 0x4C, 0x44, 0x24, 0x60,                      # CMOVL EAX, [ESP+0x60]
    0x89, 0x44, 0x24, 0x68,                            # MOV [ESP+0x68], EAX
    0x8B, 0x74, 0x24, 0x68,                            # MOV ESI, [ESP+0x68]
    0x83, 0xFE, 0x02,                                  # CMP ESI, 0x2
    0x89, 0x74, 0x24, 0x14,                            # MOV [ESP+0x14], ESI
    0x7E, 0x36,                                        # JLE 0x59B6D
])
FPS_TRUNC_PATCH = bytes([
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
    # --- Clamp to [1, 2], store, and branch ---
    0x8B, 0x74, 0x24, 0x30,                            # MOV ESI, [ESP+0x30]
    0x83, 0xFE, 0x01,                                  # CMP ESI, 1
    0x7D, 0x05,                                        # JGE +5 (skip min clamp)
    0xBE, 0x01, 0x00, 0x00, 0x00,                      # MOV ESI, 1
    0x89, 0x74, 0x24, 0x14,                            # MOV [ESP+0x14], ESI
    0x83, 0xFE, 0x02,                                  # CMP ESI, 0x2
    0x7E, 0x3B,                                        # JLE 0x59B6D (+0x3B from here)
    0x90, 0x90, 0x90, 0x90, 0x90,                      # 5x NOP (fill to 58 bytes)
])

# Patch 5: catchup code — cap to 2 steps, keep remainder (VA 0x59B37, 30 bytes)
#
# The main loop uses a Bresenham-style remainder at [ESP+0x40] for hitch recovery.
# When a frame hitch causes steps > max, the catchup block:
#   1. Caps ESI to the max step count
#   2. Computes remainder = raw_delta - max_steps * dt
# On the next frame, the FSUB at 0x59AF3 subtracts this remainder from delta,
# which immediately restores 1-step-per-frame operation.  The "lost" hitch time
# is absorbed into the remainder and effectively discarded.
#
# Original: steps=2, remainder = raw_delta - 2*dt (dt=1/30)
# Patched:  steps=2, remainder = raw_delta - 2*dt (dt=1/60)
#
# The cap of 2 matches CMP ESI, 2 in Patch 4.  We compute 2*dt via a single
# FADD ST0,ST0 (dt→2dt).  The FSUBR/FSTP pair is preserved so the remainder
# mechanism keeps working.
FPS_CATCHUP_OFFSET = va_to_file(0x059B37)
FPS_CATCHUP_ORIGINAL = bytes([
    0xD9, 0x05, 0xE8, 0x83, 0x19, 0x00,              # FLD float ptr [0x1983E8]
    0xBE, 0x02, 0x00, 0x00, 0x00,                      # MOV ESI, 0x2
    0xDC, 0xC0,                                         # FADD ST0, ST0
    0x89, 0x74, 0x24, 0x14,                             # MOV [ESP+0x14], ESI
    0xDC, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00,          # FSUBR double [ESP+0x80]
    0xDD, 0x5C, 0x24, 0x40,                             # FSTP double [ESP+0x40]
    0xEB, 0x18,                                         # JMP +0x18
])
FPS_CATCHUP_PATCH = bytes([
    0xD9, 0x05, 0xE8, 0x83, 0x19, 0x00,              # FLD float ptr [0x1983E8]  (dt=1/60)
    0x6A, 0x02,                                        # PUSH 0x2
    0x5E,                                              # POP ESI                  (ESI = 2)
    0xDC, 0xC0,                                        # FADD ST0, ST0            (2*dt)
    0x90, 0x90,                                        # NOP NOP                  (was 2nd FADD)
    0x89, 0x74, 0x24, 0x14,                            # MOV [ESP+0x14], ESI
    0xDC, 0xAC, 0x24, 0x80, 0x00, 0x00, 0x00,         # FSUBR double [ESP+0x80]  (raw_delta - 2*dt)
    0xDD, 0x5C, 0x24, 0x40,                            # FSTP double [ESP+0x40]   (store remainder)
    0xEB, 0x18,                                        # JMP +0x18
])

# ---------------------------------------------------------------------------
# Patches 7+: Subsystem .rdata 1/30 → 1/60 constants
# ---------------------------------------------------------------------------
# The engine duplicates the 1/30 (0.033333335) float constant across .rdata for
# each subsystem (camera, animation, physics, FSM, scheduler, etc.). Each copy
# is used as a per-call timestep or scheduler interval.  At 60 fps these
# subsystems are invoked twice as often, so each constant must be halved to
# preserve wall-clock behaviour.
#
# Known limitation — FUN_00043a00 blend math:
#   Computes [0x198628] * [0x1A2740] = (1/30)*(1/30) = 1/900.
#   After patching both to 1/60 the product is 1/3600; at 60 fps (2x calls/sec)
#   the net blend rate is half the original wall-time rate.  Layered animation
#   transitions may take ~2x longer.  Fixing this would require code injection
#   to replace one factor with a separate constant.
#
# Known limitation — scheduler quantum:
#   FUN_000ab830 reads a per-context quantum from [ctx+0xC] that is initialized
#   at runtime, not from a static .rdata pool.  Cannot be fixed by static binary
#   patching; scheduler time-snapping may round differently at 60 Hz.
#
# Known limitation — camera per-frame damping:
#   Camera lerp factors (e.g. lerp(old, target, factor)) lack * dt scaling and
#   are buried in virtual dispatch chains.  Camera smoothing may feel slightly
#   different at 60 fps.

FPS_SUBSYSTEM_ORIGINAL = bytes([0x89, 0x88, 0x08, 0x3D])  # float 1/30
FPS_SUBSYSTEM_PATCH    = bytes([0x89, 0x88, 0x88, 0x3C])  # float 1/60

FPS_SUBSYSTEM_OFFSETS = [
    # Tier 1 — High Impact (visual smoothness / game speed)
    ("camera",          va_to_file(0x1981C8)),  # 8 xrefs — also fixes min-timestep floor
    ("animation",       va_to_file(0x198628)),  # 10 xrefs
    ("physics",         va_to_file(0x198688)),  # 10 xrefs
    ("character_fsm",   va_to_file(0x1980A0)),  # 11 xrefs
    # Tier 2 — Medium Impact (gameplay feel)
    ("entity_init",     va_to_file(0x198410)),  # 5 xrefs
    ("player_ctrl",     va_to_file(0x198560)),  # 2 xrefs
    ("lod_blend",       va_to_file(0x1981E0)),  # 6 xrefs
    ("movement",        va_to_file(0x19873C)),  # 6 xrefs
    # Tier 3 — Scheduler Intervals
    ("timer_cooldown",  va_to_file(0x198120)),  # 1 xref
    ("effect_sched",    va_to_file(0x1981F0)),  # 2 xrefs
    ("world_sched",     va_to_file(0x198228)),  # 1 xref
    ("minor_sched",     va_to_file(0x1985D0)),  # 1 xref
    ("anim_blend",      va_to_file(0x198700)),  # 4 xrefs
    ("per_tick_accum",  va_to_file(0x198758)),  # 5 xrefs
    ("fsm_integration", va_to_file(0x198788)),  # 3 xrefs
    ("sched_requant",   va_to_file(0x198AB0)),  # 2 xrefs
    ("anim_blend2",     va_to_file(0x1A2740)),  # 4 xrefs
    # Tier 4 — Newly Discovered (previously thought dead)
    ("object_update",   va_to_file(0x1981B8)),  # 2 xrefs
    ("entity_setup",    va_to_file(0x198968)),  # 1 xref
    ("timestep_accum",  va_to_file(0x1989A8)),  # 2 xrefs
    ("state_reset",     va_to_file(0x198C98)),  # 2 xrefs
    ("critter_ai_timer",va_to_file(0x198660)),  # 1 xref — critter AI state transitions
    ("anim_event_sched",va_to_file(0x198580)),  # 2 xrefs — animation event scheduling dt
    # Tier 5 — Effect config table (accessed via base pointer + stride, no direct xrefs)
    ("effect_config_1", va_to_file(0x198138)),  # table-driven
    ("effect_config_2", va_to_file(0x1985B8)),  # table-driven
    ("effect_config_3", va_to_file(0x1986E8)),  # table-driven
    ("effect_config_4", va_to_file(0x1989C8)),  # table-driven
    ("effect_config_5", va_to_file(0x198A38)),  # table-driven
]

# ---------------------------------------------------------------------------
# Patch: double 1/30 → 1/60 for animation time accumulators (VA 0x1A2750)
# ---------------------------------------------------------------------------
# FUN_00066D00 and FUN_00066D70 add double 1/30 per frame to animation
# scheduler clocks.  At 60fps they fire every 16.67ms, so the advance
# must be 1/60 to maintain real-time parity.
FPS_ANIM_DBL_OFFSET   = va_to_file(0x1A2750)
FPS_ANIM_DBL_ORIGINAL = bytes([0x11, 0x11, 0x11, 0x11,
                               0x11, 0x11, 0xA1, 0x3F])       # double 1/30
FPS_ANIM_DBL_PATCH    = bytes([0x11, 0x11, 0x11, 0x11,
                               0x11, 0x11, 0x91, 0x3F])       # double 1/60

# ---------------------------------------------------------------------------
# Patches: float 30.0 → 60.0  (fps-rate multipliers)
# ---------------------------------------------------------------------------
FPS_RATE_30_ORIGINAL = bytes([0x00, 0x00, 0xF0, 0x41])        # float 30.0
FPS_RATE_30_PATCH    = bytes([0x00, 0x00, 0x70, 0x42])         # float 60.0

FPS_RATE_30_OFFSETS = [
    ("hud_frame_conv",  va_to_file(0x198A74)),  # 1 xref — HUD anim scroll
    ("anim_keyframe",   va_to_file(0x198B7C)),  # 1 xref — keyframe iteration
]

# ---------------------------------------------------------------------------
# Patch: shared float 30.0 → 60.0 + angular xref redirects (VA 0x1A2650)
# ---------------------------------------------------------------------------
# 20 xrefs share float 30.0 at VA 0x1A2650.  16 are fps-dependent and need
# 60.0.  4 compute "30 degrees" (deg2rad * 30) for collision/physics geometry
# and MUST keep reading 30.0.
#
# Solution: patch 0x1A2650 to 60.0, redirect the 4 angular instructions to
# read from VA 0x1A2524 — a naturally dead float 30.0 in .rdata (0 xrefs).
FPS_SHARED_30_OFFSET   = va_to_file(0x1A2650)
FPS_SHARED_30_ORIGINAL = bytes([0x00, 0x00, 0xF0, 0x41])       # float 30.0
FPS_SHARED_30_PATCH    = bytes([0x00, 0x00, 0x70, 0x42])       # float 60.0

FPS_ANGULAR_ADDR_ORIGINAL = bytes([0x50, 0x26, 0x1A, 0x00])    # LE 0x001A2650
FPS_ANGULAR_ADDR_PATCH    = bytes([0x24, 0x25, 0x1A, 0x00])    # LE 0x001A2524

FPS_ANGULAR_REDIRECTS = [
    ("frustum_cone",    va_to_file(0x4E9D9)),   # FUN_0004e870 sin/cos(30 deg)
    ("projectile_rot",  va_to_file(0x89AAC)),   # FUN_00089a70 projectile physics
    ("static_init_1",   va_to_file(0xFB518)),   # C++ static init thunk
    ("static_init_2",   va_to_file(0xFB608)),   # C++ static init thunk
]

# ---------------------------------------------------------------------------
# Patch: D3D Present spin-wait bypass (VA 0x1263E2, D3D section)
# ---------------------------------------------------------------------------
# D3DDevice_Present has a spin-wait that blocks when outstanding GPU flips >= 2.
# Even with immediate NV2A flips (Patch 1b), xemu may tie the fence completion
# counter to VBlank timing, adding up to 16.67ms stall per frame.  Changing
# JC (0x72) to JMP short (0xEB) always skips the spin-wait.  The relative
# offset (+0x18) is unchanged, so execution lands at the INC + flip path.
FPS_PRESENT_SPINWAIT_OFFSET   = va_to_file(0x1263E2)
FPS_PRESENT_SPINWAIT_ORIGINAL = bytes([0x72])                    # JC rel8
FPS_PRESENT_SPINWAIT_PATCH    = bytes([0xEB])                    # JMP rel8

# ---------------------------------------------------------------------------
# Patch: flash/sparkle timer (VA 0x19862C, .rdata)
# ---------------------------------------------------------------------------
# FUN_0003ea00 increments a per-render-frame timer by float 1/6 and also
# divides by the same constant for fade normalisation.  At 60fps the timer
# runs 2x fast; halving to 1/12 restores the correct real-time duration.
FPS_FLASH_TIMER_OFFSET   = va_to_file(0x19862C)
FPS_FLASH_TIMER_ORIGINAL = bytes([0xAB, 0xAA, 0x2A, 0x3E])     # float 1/6
FPS_FLASH_TIMER_PATCH    = bytes([0xAB, 0xAA, 0xAA, 0x3D])     # float 1/12

# ---------------------------------------------------------------------------
# Patch: collision solver bounce limit (VA 0x47EEF, .text)
# ---------------------------------------------------------------------------
# FUN_00047380 counts wall bounces per frame.  At 60 fps the halved sweep
# requires more bounces; raising 2→4 gives the same real-time budget.
FPS_COLLISION_LIMIT_OFFSET   = va_to_file(0x47EEF)
FPS_COLLISION_LIMIT_ORIGINAL = bytes([0x02])                     # CMP EAX, 0x2
FPS_COLLISION_LIMIT_PATCH    = bytes([0x04])                     # CMP EAX, 0x4

# ---------------------------------------------------------------------------
# Patch: ground probe offset — new float 0.05 (VA 0x1A2690, .rdata)
# ---------------------------------------------------------------------------
# FUN_00085f50 (ground walking state) casts a downward probe 0.1 units below
# the sweep result.  The velocity contribution doubles at 60 fps; halving the
# offset to 0.05 restores original behaviour.
FPS_PROBE_CONST_OFFSET   = va_to_file(0x1A2690)
FPS_PROBE_CONST_ORIGINAL = bytes([0x00, 0x00, 0x00, 0x00])      # unused padding
FPS_PROBE_CONST_PATCH    = bytes([0xCD, 0xCC, 0x4C, 0x3D])      # float 0.05

FPS_PROBE_REDIR_OFFSET   = va_to_file(0x86162)
FPS_PROBE_REDIR_ORIGINAL = bytes([0x74, 0x26, 0x1A, 0x00])      # LE addr 0x001A2674 (0.1)
FPS_PROBE_REDIR_PATCH    = bytes([0x90, 0x26, 0x1A, 0x00])      # LE addr 0x001A2690 (0.05)

# ---------------------------------------------------------------------------
# Patch: collision solver impulse scaling (FUN_00047380)
# ---------------------------------------------------------------------------
# NOP-ing the 2x doubling and halving the cap makes the impulse identical
# to 30 fps: min(L, cap/2)/(1/60) matches min(2L, cap)/(1/30).
FPS_SOLVER_NOP1_OFFSET   = va_to_file(0x47BC6)
FPS_SOLVER_NOP1_ORIGINAL = bytes([0xDC, 0xC0])                   # FADD ST0,ST0
FPS_SOLVER_NOP1_PATCH    = bytes([0x90, 0x90])                   # NOP NOP

FPS_SOLVER_NOP2_OFFSET   = va_to_file(0x47CF3)
FPS_SOLVER_NOP2_ORIGINAL = bytes([0xDC, 0xC0])                   # FADD ST0,ST0
FPS_SOLVER_NOP2_PATCH    = bytes([0x90, 0x90])                   # NOP NOP

FPS_SOLVER_CAP_OFFSET    = va_to_file(0x1AA230)
FPS_SOLVER_CAP_ORIGINAL  = bytes([0x6F, 0x12, 0x83, 0x3A])      # float ~0.001
FPS_SOLVER_CAP_PATCH     = bytes([0x6F, 0x12, 0x03, 0x3A])      # float ~0.0005


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def apply_fps_patches(xbe_data: bytearray) -> None:
    """Apply all 60 FPS unlock patches to the XBE data."""
    apply_xbe_patch(xbe_data, "60 FPS VBlank wait (2->1 per present)",
                    FPS_VBLANK_OFFSET, FPS_VBLANK_ORIGINAL, FPS_VBLANK_PATCH)
    apply_xbe_patch(xbe_data, "60 FPS disable D3D Present VSync (fix double-wait)",
                    FPS_PRESENT_VSYNC_OFFSET, FPS_PRESENT_VSYNC_ORIGINAL,
                    FPS_PRESENT_VSYNC_PATCH)
    apply_xbe_patch(xbe_data, "60 FPS rate multiplier (30->60)",
                    FPS_RATE_OFFSET, FPS_RATE_ORIGINAL, FPS_RATE_PATCH)
    apply_xbe_patch(xbe_data, "60 FPS timestep (1/30->1/60)",
                    FPS_DT_OFFSET, FPS_DT_ORIGINAL, FPS_DT_PATCH)
    apply_xbe_patch(xbe_data, "60 FPS FISTP truncation + step clamp (anti-death-spiral)",
                    FPS_TRUNC_OFFSET, FPS_TRUNC_ORIGINAL, FPS_TRUNC_PATCH)
    apply_xbe_patch(xbe_data, "60 FPS catchup (ESI=2, remainder=raw_delta-2*dt)",
                    FPS_CATCHUP_OFFSET, FPS_CATCHUP_ORIGINAL, FPS_CATCHUP_PATCH)

    for name, offset in FPS_SUBSYSTEM_OFFSETS:
        apply_xbe_patch(xbe_data, f"60 FPS subsystem dt {name} (1/30->1/60)",
                        offset, FPS_SUBSYSTEM_ORIGINAL, FPS_SUBSYSTEM_PATCH)

    apply_xbe_patch(xbe_data, "60 FPS anim scheduler double (1/30->1/60)",
                    FPS_ANIM_DBL_OFFSET, FPS_ANIM_DBL_ORIGINAL, FPS_ANIM_DBL_PATCH)

    for name, offset in FPS_RATE_30_OFFSETS:
        apply_xbe_patch(xbe_data, f"60 FPS rate multiplier {name} (30->60)",
                        offset, FPS_RATE_30_ORIGINAL, FPS_RATE_30_PATCH)

    apply_xbe_patch(xbe_data, "60 FPS shared velocity constant (30->60)",
                    FPS_SHARED_30_OFFSET, FPS_SHARED_30_ORIGINAL, FPS_SHARED_30_PATCH)

    for name, offset in FPS_ANGULAR_REDIRECTS:
        apply_xbe_patch(xbe_data, f"60 FPS angular redirect {name} (keep 30deg)",
                        offset, FPS_ANGULAR_ADDR_ORIGINAL, FPS_ANGULAR_ADDR_PATCH)

    apply_xbe_patch(xbe_data, "60 FPS disable Present spin-wait (fix frame stall)",
                    FPS_PRESENT_SPINWAIT_OFFSET, FPS_PRESENT_SPINWAIT_ORIGINAL,
                    FPS_PRESENT_SPINWAIT_PATCH)
    apply_xbe_patch(xbe_data, "60 FPS flash timer (1/6->1/12)",
                    FPS_FLASH_TIMER_OFFSET, FPS_FLASH_TIMER_ORIGINAL,
                    FPS_FLASH_TIMER_PATCH)
    apply_xbe_patch(xbe_data, "60 FPS collision bounce limit (2->4)",
                    FPS_COLLISION_LIMIT_OFFSET, FPS_COLLISION_LIMIT_ORIGINAL,
                    FPS_COLLISION_LIMIT_PATCH)

    apply_xbe_patch(xbe_data, "60 FPS ground probe constant (write 0.05)",
                    FPS_PROBE_CONST_OFFSET, FPS_PROBE_CONST_ORIGINAL,
                    FPS_PROBE_CONST_PATCH)
    apply_xbe_patch(xbe_data, "60 FPS ground probe redirect (0.1->0.05)",
                    FPS_PROBE_REDIR_OFFSET, FPS_PROBE_REDIR_ORIGINAL,
                    FPS_PROBE_REDIR_PATCH)

    apply_xbe_patch(xbe_data, "60 FPS solver impulse NOP doubling (branch 1)",
                    FPS_SOLVER_NOP1_OFFSET, FPS_SOLVER_NOP1_ORIGINAL,
                    FPS_SOLVER_NOP1_PATCH)
    apply_xbe_patch(xbe_data, "60 FPS solver impulse NOP doubling (branch 2)",
                    FPS_SOLVER_NOP2_OFFSET, FPS_SOLVER_NOP2_ORIGINAL,
                    FPS_SOLVER_NOP2_PATCH)
    apply_xbe_patch(xbe_data, "60 FPS solver correction cap (0.001->0.0005)",
                    FPS_SOLVER_CAP_OFFSET, FPS_SOLVER_CAP_ORIGINAL,
                    FPS_SOLVER_CAP_PATCH)
