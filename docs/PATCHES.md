# Patch Pack Catalog

Each pack is a module under [`azurik_mod/patches/`](../azurik_mod/patches/) that exports a list of `PatchSpec` entries and registers itself with the central registry.  The CLI (`azurik-mod verify-patches`) and the GUI (`gui/tabs/patches.py`) discover packs automatically — there is no hard-coded list to update when a new pack ships.

| Pack                 | Sites | Default-on | Tags                | Module |
|----------------------|-------|------------|---------------------|--------|
| `fps_unlock`         | 50    | no         | fps, experimental   | [azurik_mod/patches/fps_unlock.py](../azurik_mod/patches/fps_unlock.py) |
| `qol_gem_popups`     | 0     | no         | qol                 | [azurik_mod/patches/qol.py](../azurik_mod/patches/qol.py) |
| `qol_pickup_anims`   | 1     | no         | qol                 | [azurik_mod/patches/qol.py](../azurik_mod/patches/qol.py) |
| `player_physics`     | 3     | no         | player, physics     | [azurik_mod/patches/player_physics.py](../azurik_mod/patches/player_physics.py) |

---

## `fps_unlock`

Unlocks 60 fps on xemu (and, in principle, faster real hardware displays).  Three caps are lifted:

- **Render cap — manual VBlank loop (`FUN_0008fbe0`):** the present wrapper waits for `currentVBlank >= lastVBlank + N`.  Patch 1a lowers `N` from 2 to 1.
- **Render cap — D3D Present VSync (`FUN_001262d0`):** the NV2A push buffer is forced to the immediate path (0x300) by NOP-ing the JNZ at VA 0x12635D, avoiding xemu's synchronous VSync wait.
- **Simulation cap — main loop (`FUN_00058e40`):** delta-to-step math switches from `ROUND` to `TRUNC`, preventing the 60 → 30 fps death spiral at frame times just over 25 ms.  Plus 28 subsystem `1/30` timestep constants and three shared `30.0` rate multipliers get halved.

The `CMP ESI, 4` / `PUSH 0x4` pair is `safety_critical=True` — both sides of the step-cap math must agree.  Cap = 4 at 60 Hz sim is the minimum that preserves real-time game speed down to 15 FPS rendered (vanilla runs at 30 Hz sim with a 2-step cap, which covers the same 15-FPS window).  A lower cap makes game time drift below real time whenever rendered FPS dips below 30, which is jarring during combat / cutscene hitches.  The on-death BSOD that reproduces on vanilla 30-FPS Azurik is a pre-existing engine bug unrelated to the cap.  `tests/test_fps_safety.py` pins the cap byte and guarantees TRUNC/CATCHUP stay in sync.

Verify with:

```bash
azurik-mod verify-patches --xbe patched.xbe --original stock.xbe --strict
```

A clean whitelist diff confirms only the 50 declared sites were modified.

### Safety-critical sites

| Site                                        | VA        | Note |
|---------------------------------------------|-----------|------|
| FISTP truncation + step clamp (cap=4)       | 0x59AFD   | `CMP ESI, 0x4` pinned by the safety test |
| Catchup remainder (raw_delta - 4*dt)        | 0x59B37   | `PUSH 0x4` + two `FADD ST0,ST0` pinned by the safety test |

### Known limitations (not in scope for static patching)

- `FUN_00043a00` blend math — product of two 1/30 constants becomes 1/3600; layered transitions may feel ~2x slower.
- Scheduler quantum at `[ctx+0xC]` — runtime-initialised, cannot be patched statically.
- Camera per-frame damping — virtual-dispatch chains; lerp factors without `*dt` scaling may feel slightly different.

---

## Quality-of-life packs

Each QoL tweak is its own pack so the GUI's Patches page can toggle them independently.  All default to OFF; the user opts in.

### `qol_gem_popups` (opt-in: `--gem-popups`)

Hides the "You found X for the first time!" message that pops up the first time you collect each gem type.  Implementation: nulls the first byte of each popup string at five file offsets (0x1977D8 … 0x197858), terminating the string before it reaches the renderer.

### `qol_pickup_anims` (opt-in: `--pickup-anims`)

Skips the short celebration animation that plays after picking up an item.  Implementation: replaces the first instruction of the non-gem pickup handler's animation block with a `JMP` to its epilog at VA 0x4146F (file offset 0x313EE, 5 bytes).  The "collected" flag and save-list update still run, so picked-up items remain collected and saves stay consistent.  Supersedes the earlier OBSIDIAN_ANIM + FIST_PUMP pair that could drop state.

### Player character swap (`--player-character <name>`)

Replaces the `garret4` string at file offset 0x1976C8 with an arbitrary ≤11-char ASCII model name.  Not a pack — there's no GUI toggle yet, only the CLI flag.  Marked experimental; animation mismatches are likely.

---

## `player_physics`

Slider-driven player physics tweaks.  Every slider is declared as a `ParametricPatch`, so the same descriptor drives both the CLI flags and the GUI sliders.

### Gravity (`--gravity M_PER_S2`)

- VA `0x1980A8`, 4-byte float (file offset `0x190D08`).  Baseline bytes `CD CC 1C 41` = `9.8f`.
- Range `0.98 … 29.4` m/s² (0.1× to 3.0× baseline).
- Global — affects enemy falls and projectile arcs too.  Two other `9.8f` constants at `0x198704` and `0x198740` are unrelated (camera / animation scalars) and remain untouched.
- `--gravity 9.8` produces a byte-identical XBE so the whitelist diff stays clean.

### Walk speed (`--player-walk-scale X`) and run speed (`--player-run-scale X`)

- Both are multiplicative scales on garret4's baseline values in `config.xbr`'s `attacks_transitions` keyed-table section (column 25).
  - `walkSpeed` cell at file offset `0x00CECC` (double payload at `0x00CED4`, baseline 5.0).
  - `runSpeed` cell at file offset `0x00CEEC` (double payload at `0x00CEF4`, baseline 7.0).
- Range `0.25 … 3.0`×, default `1.0×` (no-op, byte-identical).
- Only garret4 is scaled — `walkAnimSpeed` / `runAnimSpeed` and every other entity are left alone.  Extending to other characters is a one-line addition to `apply_player_speed`.

### CLI

```bash
# Slider-only physics without touching any randomizer pool
azurik-mod apply-physics --iso iso/Azurik.iso --output iso/low_grav.iso \
    --gravity 4.9 --walk-speed 1.5 --run-speed 1.5

# Roll into a full randomize-full build
azurik-mod randomize-full --iso iso/Azurik.iso --output out.iso \
    --seed 42 --gravity 7.0 --player-walk-scale 1.25
```

### GUI

The Patches page renders one `ParametricSlider` per parameter under the `player_physics` section.  Slider values live on `AppState.pack_params["player_physics"]` and are forwarded to `cmd_randomize_full` by `gui/backend.run_randomizer`.

---

## Writing a new patch pack

1. Create `azurik_mod/patches/<feature>.py`.
2. Declare `PatchSpec` entries and collect them in `FOO_PATCH_SITES`.
3. Write an `apply_foo_patches(xbe_data: bytearray)` that iterates the list and calls `apply_patch_spec`.
4. Register:

   ```python
   from azurik_mod.patching.registry import PatchPack, register_pack

   register_pack(PatchPack(
       name="foo",
       description="...",
       sites=FOO_PATCH_SITES,
       apply=apply_foo_patches,
       default_on=True,
       tags=("cosmetic",),
   ))
   ```

5. Add to [`azurik_mod/patches/__init__.py`](../azurik_mod/patches/__init__.py).
6. Update this file.

The GUI's generic patches tab ([`gui/tabs/patches.py`](../gui/tabs/patches.py)) and `azurik-mod verify-patches` will pick the new pack up automatically.
