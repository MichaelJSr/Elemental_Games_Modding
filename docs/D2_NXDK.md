# D2 — NXDK integration (deferred)

> **Status**: deferred. This doc is a design note. No code yet.
>
> **When to revisit**: when 2–3 concrete shims concretely demand native
> D3D8 / DSound / XAPI / XGraphics access. Until then, shims route
> those calls through vanilla Azurik wrappers (A3) or the runtime
> kernel-export resolver (D1-extend).

D2 wires the full [nxdk](https://github.com/XboxDev/nxdk) (open-source
Xbox SDK) headers + libraries into the C-shim toolchain. It's the
last major platform tier — once it lands, a shim can call *any*
Xbox API directly without going through an Azurik wrapper or a
runtime resolver.

---

## 1. What D2 actually unlocks

Today the shim platform exposes three layers of callable API:

| Tier  | Surface | Where it lives |
|-------|---------|----------------|
| A3    | Vanilla Azurik functions (play_movie_fn, entity_lookup, ...) | `azurik_mod/patching/vanilla_symbols.py` + `shims/include/azurik_vanilla.h` |
| D1    | The 151 xboxkrnl exports Azurik's vanilla XBE imports (DbgPrint, NtOpenFile, KeQueryPerformanceCounter, ...) | `shims/include/azurik_kernel.h` |
| D1-extend | Any xboxkrnl export, resolved at runtime via the PE export table | `shims/include/azurik_kernel_extend.h` |

D2 adds a fourth:

| Tier | Surface | Where it lives |
|------|---------|----------------|
| D2   | **Full Xbox SDK** — D3D8, DSound, XAPI, XGraphics, XOnline, XACT | `shims/nxdk/include/*.h` + `shims/nxdk/lib/*.lib` |

**Concretely, D2 lets a shim do:**

| Domain | Example shim |
|--------|--------------|
| **HUD / UI overlays** | Draw an FPS counter, position readout, debug info panel, or custom pause menu directly via `D3DDevice_DrawIndexedVertices` — without piggy-backing on Azurik's renderer. |
| **Texture / material mods** | Replace textures at load time via `XGSetTextureHeader` + `XGSwizzleRect`; add post-process filters (bloom, LUT colour grading); swap skyboxes; render debug visualisations (hitboxes, nav meshes). |
| **Audio mods** | Custom music via DirectSound streaming; SFX replacement; 3D audio repositioning; scriptable dynamic audio via XACT. |
| **Native input** | Read raw XInput state via `XInputGetState` directly instead of parsing Azurik's `ControllerState` struct; implement gesture detection, force-feedback patterns, full-range button remap. |
| **Save / persistence** | Write custom save snapshots to VMU via XAPI save APIs; cross-save over XNet; XAM-managed saves. |
| **Self-contained shims** | A shim that touches zero Azurik functions beyond its entry hook — entirely standalone mod. |

**What D2 does NOT enable** (these still need Ghidra / vanilla-symbol work):

- Reading or writing Azurik's game-state structs (those are defined in-game, not by the SDK). Keep using `azurik.h`.
- Replacing Azurik's own rendering pipeline wholesale. Azurik's renderer is statically linked into `.text` / `D3D` / `XGRPH` sections; you'd need Ghidra-level hook work to intercept its frame calls.

---

## 2. What D2 would require

### 2a. Toolchain

- ~**100 MB** of SDK on disk (nxdk source + tools + libs).
- A new `shims/toolchain/compile_nxdk.sh` (or extend `compile.sh`) that adds:
  - `-I shims/nxdk/include` for the headers.
  - `-D__NXDK__` or similar guard so D2-aware shims can detect the environment.
  - Linker flags pointing at the static `.lib` files nxdk builds.
- `compile.sh` currently never links — it stops at `.o`. D2 would add an optional link step that produces a standalone `.o` with satisfied references to nxdk's static library functions. (Alternative: keep unlinked, and let `layout_coff` resolve nxdk symbols via D1-extend. See § 3 below.)

### 2b. Runtime

Most nxdk APIs ultimately resolve to xboxkrnl imports — which D1-extend already covers. So for most D2 usage we don't actually need a new runtime mechanism; D1-extend's resolver handles it.

The exception is **D3D8 state objects** (device, textures, vertex buffers) and similar libraries that nxdk provides as statically-linked helper code. Those would need to be compiled into the shim itself — which means either:

- **Option A**: Fully link nxdk libs into the shim's `.o`. The shim gets ~10–50 KB of inline helper code, linked at compile time. Works out-of-the-box with `layout_coff` once the `.o` is produced.
- **Option B**: Link nxdk into a shared-library shim (via E) and let individual shims reference its exports. Smaller individual shims but more setup per pack.

### 2c. Headers

- Import nxdk's header tree under `shims/nxdk/include/`. Headers are MIT-licensed.
- Minor patching needed:
  - Replace `#pragma once` with `#ifndef`/`#define` guards if any header uses it (clang's freestanding mode handles both, but inconsistency matters for cross-compiler support).
  - Strip any GCC/MSVC-specific pragma that clang doesn't grok on `-target i386-pc-win32`.
  - Confirm zero overlap with our `azurik_kernel.h` — shouldn't be any since our header only declares 151 functions and nxdk declares the same set the same way.

---

## 3. Proposed architecture

```
shims/
├── include/                      [existing]
│   ├── azurik.h                  [structs, VA anchors]
│   ├── azurik_vanilla.h          [vanilla-function externs]
│   ├── azurik_kernel.h           [151 static xboxkrnl imports]
│   └── azurik_kernel_extend.h    [any xboxkrnl export via D1-extend]
├── nxdk/                         [NEW under D2]
│   ├── include/
│   │   ├── d3d8.h
│   │   ├── d3d8types.h
│   │   ├── dsound.h
│   │   ├── xapi.h
│   │   ├── xgraphics.h
│   │   ├── xonline.h
│   │   └── xacteng.h
│   └── lib/
│       ├── libd3d8.a
│       ├── libdsound.a
│       └── libxapi.a              [static libs nxdk builds]
└── toolchain/
    ├── compile.sh                 [existing — stops at .o]
    └── compile_nxdk.sh            [NEW — includes nxdk/ + optional link]
```

A shim that wants native D3D8:

```c
/* azurik_mod/patches/custom_hud/shim.c */
#include "azurik.h"
#include "azurik_kernel.h"
#include "d3d8.h"        /* NXDK */

extern IDirect3DDevice8 *g_pDevice;  /* wired to vanilla Azurik's device via A3 */

__attribute__((stdcall))
void c_custom_hud(void) {
    /* Draw an FPS counter quad. */
    IDirect3DDevice8_SetTexture(g_pDevice, 0, g_my_font_tex);
    IDirect3DDevice8_DrawIndexedPrimitive(g_pDevice, ...);
}
```

Compile path:

```bash
bash shims/toolchain/compile_nxdk.sh \
    azurik_mod/patches/custom_hud/shim.c \
    shims/build/custom_hud.o
```

The layout pipeline (`layout_coff`) doesn't change — it already handles relocations against externs resolved via the session's resolver chain. `d3d8.h` declarations map to kernel ordinals or to nxdk's static-linked helpers, both of which are resolvable at apply time.

---

## 4. Implementation milestones

| # | Milestone | Effort | Gate |
|---|-----------|--------|------|
| 1 | Vendor nxdk's include/ tree into `shims/nxdk/include/`. Patch for clang compat. | 2–3 h | Header probe shim compiles through `compile_nxdk.sh`. |
| 2 | Build nxdk's `.lib` artifacts (static libs) on macOS/Linux via nxdk's CMake. | 4–6 h | `.lib` files exist and `file` reports them as i386 archives. |
| 3 | Extend `compile.sh` → `compile_nxdk.sh` (or add a `--nxdk` flag) with the `-I` / `-L` / `-l` plumbing. | 1 h | A 3-line shim calling `IDirect3DDevice8_BeginScene()` compiles + produces a valid COFF. |
| 4 | `layout_coff` extern-resolver hook: when a symbol comes from a nxdk static lib, absorb its `.text` into the SHIMS section the same way D1-extend's resolver stubs are. | 3–5 h | A test shim calls a real D3D8 function; apply produces a working XBE; xemu shows the expected side effect. |
| 5 | First real shim: a minimal FPS-counter overlay (`azurik_mod/patches/hud_fps_counter/`). | 1–2 days | Visible in-game; survives scene transitions. |
| 6 | Docs + CHANGELOG + SHIMS.md status table flip to "done". | 2 h | N/A |

Total: ~1–2 weeks of focused work, mostly in milestones 2 and 4.

---

## 5. Why it's still deferred

1. **No concrete demand yet.** Every pack shipped so far (FPS unlock, popup suppression, skip logo, player physics) can be expressed as byte patches, parametric sliders, or small logic shims through A3 / D1. None of them need native D3D / DSound. D1-extend covers any remaining kernel-level need.

2. **100 MB toolchain footprint.** Adds meaningful weight to the repo and CI. Not worth it until it unlocks something users want.

3. **Xemu quirks.** Some nxdk call paths (especially DirectSound / XACT) hit emulator-specific edge cases in xemu that don't reproduce on real hardware. A D2 shim might work on real Xbox and fail in emulation, or vice versa. Testing surface doubles.

4. **Ghidra escape hatch is enough today.** For features that "want to call D3D", nine times out of ten the right answer is: find Azurik's own call path via Ghidra, add that function to the vanilla-symbol registry (A3), and call it. Gives you the same behaviour for 10× less effort.

---

## 6. First concrete shim that would *need* D2

Probably:
- **`hud_fps_counter`** (render an FPS counter via custom D3D calls). Could arguably do this via A3 if Azurik has an exposable text-render helper, but a native D3D path is much simpler.
- **`texture_hotload`** (swap textures based on an external file). Requires XAPI's `NtCreateFile` + `XGSetTextureHeader` — the latter is a non-Azurik-imported function.
- **`custom_pause_menu`** (full UI mod). Needs D3D, DSound, and input handling in ways Azurik's menu system doesn't offer.

When one of these lands as a concrete request, revisit this doc and start from § 4.

---

## 7. Decisions to make at revisit time

When someone picks this up:

- [ ] **nxdk vs OpenXDK**. nxdk is more actively maintained (2020s vs OpenXDK's 2000s). Recommend nxdk.
- [ ] **Static link vs D1-extend lookup for nxdk helpers**. For kernel-level helpers, D1-extend is simpler. For D3D state-object wrappers, static link is mandatory.
- [ ] **How to expose `IDirect3DDevice8 *g_pDevice`**. Azurik holds its D3D device pointer in a global — identify via Ghidra and expose as a VA anchor in `azurik.h` so shims don't have to create their own device.
- [ ] **CI story**. nxdk's build depends on a specific clang version + Wine for some cross-compile steps. Either ship a pre-built binary blob in the repo or require contributors to have nxdk installed locally.

---

## 8. See also

- [`docs/SHIMS.md`](./SHIMS.md) — platform architecture + status table.
- [`docs/D1_EXTEND.md`](./D1_EXTEND.md) — the approach we DID ship for "call any xboxkrnl function".
- [`docs/SHIM_AUTHORING.md`](./SHIM_AUTHORING.md) — authoring workflow.
- [nxdk GitHub](https://github.com/XboxDev/nxdk) — the upstream SDK.
- [OpenXDK](https://openxdk.sourceforge.net/) — historical reference; where our `azurik_kernel.h` signatures came from.
