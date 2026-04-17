# Decompilation & Analysis

This document points at the external Ghidra project and enumerates the functions that matter for the patch packs shipped in this repo.

## Ghidra project

The live Ghidra project is NOT committed to this repo (it's a multi-megabyte binary directory).  To continue analysis:

1. Install [Ghidra 11.x+](https://ghidra-sre.org/).
2. Open the project at the path referenced by your local workspace (historically: `../Azurik Ghidra.rep` relative to this repo, alongside `Azurik Ghidra.gpr`).
3. Import `default.xbe` from a legitimately-owned copy of Azurik.
4. Run auto-analysis.  Most of the notable functions below will be named `FUN_xxxxxxxx`; rename them in your local project as you confirm their roles.

For AI-assisted RE, connect Ghidra to [GhydraMCP](https://github.com/starsong-consulting/GhydraMCP) and set the default port to `8193` (or call `instances_use(<port>)` from the MCP client).

## XBE section map

Mirror of [`azurik_mod.patching.xbe.XBE_SECTIONS`](../azurik_mod/patching/xbe.py).  Every patch VA resolves through `va_to_file(va)` using this table.

| Section | VA start   | Raw start  |
|---------|------------|------------|
| .text   | 0x011000   | 0x001000   |
| BINK    | 0x1001E0   | 0x0F01E0   |
| D3D     | 0x11D5C0   | 0x118000   |
| DSOUND  | 0x135460   | 0x12FE60   |
| XGRPH   | 0x154BA0   | 0x14F5A0   |
| D3DX    | 0x168680   | 0x163080   |
| XPP     | 0x187BA0   | 0x1825A0   |
| .rdata  | 0x18F3A0   | 0x188000   |
| .data   | 0x1A29A0   | 0x19C000   |

## Notable functions

Names are `FUN_<VA>` in Ghidra's default auto-analysis output.

### Main loop and timing (FPS patch territory)

| Function          | Role |
|-------------------|------|
| `FUN_00058e40`    | Top-level main loop: builds dt, calls sim steps, drives renderer.  Home of the step-count FMUL + FISTP that Patch 4 rewrites. |
| `FUN_0008fbe0`    | Present wrapper (manual VBlank pacer).  Patch 1a lowers N from 2 to 1 at VA 0x08FD19. |
| `FUN_001262d0`    | D3D buffer-flip push-buffer builder.  Writes 0x300 (immediate) vs 0x304 (vsync).  Patch 1b NOPs the JNZ at VA 0x12635D to force the immediate path. |
| `FUN_0005f620`    | BINK cutscene state machine.  Takes dt as `param_1`; forwards it to `FUN_00018d30` etc. |
| `FUN_00058d00`    | Scheduler pump; increments `DAT_001be36c` (per-step counter) and services cross-thread queues. |

### Input

| Function       | Role |
|----------------|------|
| `FUN_000a2df0` | Outer input tick: calls XGetDeviceChanges, opens/closes controllers, iterates `FUN_000a2880` for each pad. |
| `FUN_000a2880` | Per-controller poll: reads XInputGetState, normalises axes / triggers / buttons into `DAT_0037BE98+`. Stateless — no edge detection here. |

### Collision / physics (solver impulse patches)

| Function       | Role |
|----------------|------|
| `FUN_00047380` | Collision solver main routine.  Holds the bounce limit (patched 2→4 at VA 0x47EEF) and the correction cap (patched at VA 0x1AA230 in .rdata). |
| `FUN_00085f50` | Ground walking state.  Reads the probe offset whose pointer we re-target at VA 0x86162 from 0x1A2674 (0.1) to 0x1A2690 (0.05). |

### Per-frame globals

| Address           | Role |
|-------------------|------|
| `DAT_001a9c0c`    | Per-Present frame counter (++ each present). Read by `FUN_000916a0` for texture "last frame seen" cache invalidation. |
| `DAT_0038dd14`    | Last-seen VBlank for the manual pacer.  Read/written by `FUN_0008fbe0`. |
| `DAT_001be36c`    | Per-step counter (++ each `FUN_00058d00` call). 23 xrefs. |
| `DAT_001bf404`    | Scheduler budget (decremented per pump). |

### Constants (.rdata anchors for fps math)

| VA        | Original | Patched | Notes |
|-----------|----------|---------|-------|
| 0x1983E8  | 1/30     | 1/60    | Main dt; 3 xrefs in `FUN_00058e40`. |
| 0x1A28C8  | double 30.0 | double 60.0 | Rate multiplier; 1 xref at `FUN_00058e40` + 0x59AF7. |
| 0x1A2650  | float 30.0 | float 60.0 | Shared velocity / rate; 20 xrefs (16 fps-dependent, 4 angular redirected to the dead copy at 0x1A2524). |
| 0x1A2740  | float 1/30 | float 1/60 | `anim_blend2`. |
| 0x1A2750  | double 1/30 | double 1/60 | Anim scheduler accumulator. |

## Analysis scripts

All in [`scripts/analysis/`](../scripts/analysis/):

| Script                                          | Purpose |
|-------------------------------------------------|---------|
| `scan_xbe_constants.py`                         | Scan .rdata / .data / .text for float/double patterns and flag anything outside `FPS_DATA_PATCHED_VAS`. |
| `scan_int30_instructions.py`                    | Find `CMP/MOV/PUSH` with imm 0x1E or 0x3C plus x87 FMUL/FADD/FLD references to the canonical FPS anchors. |
| `scan_frame_counters.py`                        | Dump every disp32 xref to per-frame globals (`DAT_001a9c0c`, `DAT_001be36c`, etc.).  Handy for surfacing frame-cadence logic. |
| `scan_ghidra_hexdump.py`                        | Parse text memory dumps from Ghidra `memory_read` into a usable table. |

Raw Ghidra hex dumps saved during the RE of 0x198280–0x198E80 live in `scripts/analysis/hex_dumps/`.
