# Patch Pack Catalog

Each pack is a **folder** under [`azurik_mod/patches/`](../azurik_mod/patches/) — one folder per feature.  The folder holds `__init__.py` (the `Feature(...)` declaration + any apply logic) and, for shim-backed features, `shim.c`.  The CLI (`azurik-mod verify-patches`) and the GUI ([`gui/pages/patches.py`](../gui/pages/patches.py)) discover packs automatically by importing the package — there is no hard-coded list to update when a new pack ships.

Packs tagged **c-shim** are backed by compiled C code from the feature folder's `shim.c` rather than hand-assembled bytes.  See [docs/SHIM_AUTHORING.md](SHIM_AUTHORING.md) for the authoring workflow.

## Categories

Every pack lives in exactly one **category**, which determines the tab it appears under in the GUI's Patches page.  Categories are first-class objects declared in [`azurik_mod/patching/category.py`](../azurik_mod/patching/category.py) and ordered by `Category.order` (lower → earlier tab).  The builtin set:

| id              | Title             | Order | Contents                                  |
|-----------------|-------------------|-------|-------------------------------------------|
| `performance`   | Performance       | 10    | Frame-rate / GPU / rendering tweaks       |
| `player`        | Player            | 20    | Player-character movement + physics       |
| `boot`          | Boot / Intro      | 30    | Skip boot-time cutscenes and logos        |
| `qol`           | Quality of Life   | 40    | In-game UX and pacing improvements        |
| `randomize`     | Randomize         | 50    | Shuffle-pool toggles (also on Randomize page) |
| `experimental`  | Experimental      | 80    | Opt-in patches that may destabilise the game |
| `other`         | Other             | 9999  | Fallback for packs without an explicit id |

### Creating a new category

The easy path: just set `category="my_new_name"` on your `Feature(...)` declaration.  The registry auto-creates a placeholder `Category` — the tab label defaults to the id humanised (`"my_new_name"` → `"My New Name"`) and sort order `1000` (after every builtin).

The explicit path (recommended for shipped packs):

```python
from azurik_mod.patching.category import Category, register_category

register_category(Category(
    id="cheats",
    title="Cheats",
    description="Plugin-provided cheat / debug mods.",
    order=50,    # pick from 100+ if you don't want to compete with builtins
))
```

`register_category` is idempotent when the metadata matches exactly, so it's safe to call from multiple modules.  Conflicting re-registrations (same `id`, different `title`/`order`/`description`) raise `ValueError` to catch plugin clashes early.

---

| Pack                 | Sites | Default-on | Category       | Tags          | Folder |
|----------------------|-------|------------|----------------|---------------|--------|
| `fps_unlock`         | 50    | no         | `performance`  | fps           | [azurik_mod/patches/fps_unlock/](../azurik_mod/patches/fps_unlock/) |
| `player_physics`     | 10    | no         | `player`       | physics, c-shim | [azurik_mod/patches/player_physics/](../azurik_mod/patches/player_physics/) |
| `flap_at_peak` *(DEPRECATED)*            | 1 | no | `player` | physics, c-shim, deprecated | [azurik_mod/patches/flap_at_peak/](../azurik_mod/patches/flap_at_peak/) |
| `root_motion_roll` *(DEPRECATED)*        | 1 | no | `player` | physics, c-shim, deprecated | [azurik_mod/patches/root_motion_roll/](../azurik_mod/patches/root_motion_roll/) |
| `root_motion_climb` *(DEPRECATED)*       | 1 | no | `player` | physics, c-shim, deprecated | [azurik_mod/patches/root_motion_climb/](../azurik_mod/patches/root_motion_climb/) |
| `slope_slide_speed` *(DEPRECATED)*       | 1 | no | `player` | physics, c-shim, deprecated | [azurik_mod/patches/slope_slide_speed/](../azurik_mod/patches/slope_slide_speed/) |
| `animation_root_motion_scale` *(DEPRECATED)* | 1 | no | `player` | physics, c-shim, deprecated | [azurik_mod/patches/animation_root_motion_scale/](../azurik_mod/patches/animation_root_motion_scale/) |
| `qol_skip_logo`      | 1     | no         | `boot`         | c-shim        | [azurik_mod/patches/qol_skip_logo/](../azurik_mod/patches/qol_skip_logo/) |
| `qol_gem_popups`     | 0     | no         | `qol`          | —             | [azurik_mod/patches/qol_gem_popups/](../azurik_mod/patches/qol_gem_popups/) |
| `qol_other_popups`   | 0     | no         | `qol`          | —             | [azurik_mod/patches/qol_other_popups/](../azurik_mod/patches/qol_other_popups/) |
| `qol_pickup_anims`   | 1     | no         | `qol`          | —             | [azurik_mod/patches/qol_pickup_anims/](../azurik_mod/patches/qol_pickup_anims/) |
| `qol_skip_save_signature` | 1 | no         | `qol`          | save-edit, signature-bypass | [azurik_mod/patches/qol_skip_save_signature/](../azurik_mod/patches/qol_skip_save_signature/) |
| `rand_major`         | 0     | no         | `randomize`    | —             | [azurik_mod/patches/randomize/](../azurik_mod/patches/randomize/) |
| `rand_keys`          | 0     | no         | `randomize`    | —             | [azurik_mod/patches/randomize/](../azurik_mod/patches/randomize/) |
| `rand_gems`          | 0     | no         | `randomize`    | —             | [azurik_mod/patches/randomize/](../azurik_mod/patches/randomize/) |
| `rand_barriers`      | 0     | no         | `randomize`    | —             | [azurik_mod/patches/randomize/](../azurik_mod/patches/randomize/) |
| `rand_connections`   | 0     | no         | `randomize`    | —             | [azurik_mod/patches/randomize/](../azurik_mod/patches/randomize/) |

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

### How the popup suppression works

The popup system looks up its message by a localisation resource key like `loc/english/popups/diamonds`.  We null the first byte of that key in `.rdata`, turning it into an empty string; the resource lookup fails silently and the popup never renders.  The actual popup text (e.g. "Collect 100 Diamonds") lives in a localisation `.xbr` referenced by the key, **not** in `default.xbe`, so searching the XBE for the literal popup body turns up nothing — the key is the only thing we can touch from a static binary patch.

### `qol_gem_popups` (opt-in: `--gem-popups`)

Hides the "Collect 100 &lt;gem&gt;" popup that appears the first time you collect each gem type (diamonds, emeralds, rubies, sapphires, obsidians).  Nulls five resource-key bytes:

| Offset    | Key                                |
|-----------|------------------------------------|
| `0x1977D8` | `loc/english/popups/collect_obsidians` |
| `0x197800` | `loc/english/popups/sapphires`     |
| `0x197820` | `loc/english/popups/rubies`        |
| `0x19783C` | `loc/english/popups/diamonds`      |
| `0x197858` | `loc/english/popups/emeralds`      |

### `qol_other_popups` (opt-in: `--other-popups`)

Hides the remaining non-gem first-time / milestone / tutorial popups — the swim tutorial, the "all six keys collected" milestone, first-time key / health pickups, and the first pickup of each elemental and chromatic power-up.  Nine resource-key bytes:

| Offset    | Key                                     | What it gates                  |
|-----------|-----------------------------------------|--------------------------------|
| `0x194A78` | `loc/english/popups/swim`              | first-swim tutorial            |
| `0x197760` | `loc/english/popups/6keys`             | all-six-keys milestone         |
| `0x19777C` | `loc/english/popups/key`               | first key pickup               |
| `0x197794` | `loc/english/popups/chromatic_powerup` | first chromatic power-up pickup|
| `0x1977BC` | `loc/english/popups/health`            | first health pickup            |
| `0x197874` | `loc/english/popups/water_powerup`     | first water power-up pickup    |
| `0x197898` | `loc/english/popups/fire_powerup`      | first fire power-up pickup     |
| `0x1978B8` | `loc/english/popups/air_powerup`       | first air power-up pickup      |
| `0x1978D8` | `loc/english/popups/earth_powerup`     | first earth power-up pickup    |

**Deliberately excluded:** `0x194910` (`loc/english/popups/gameover`) is **not** in the offset list.  That key drives the death-screen message, not a pickup popup; nulling it would leave the player with no feedback on death, which is bad UX.  [`tests/test_qol_other_popups.py`](../tests/test_qol_other_popups.py) pins this exclusion.

### `qol_pickup_anims` (opt-in: `--pickup-anims`)

Skips the short celebration animation that plays after picking up an item.  Implementation: replaces the first instruction of the non-gem pickup handler's animation block with a `JMP` to its epilog at VA 0x4146F (file offset 0x313EE, 5 bytes).  The "collected" flag and save-list update still run, so picked-up items remain collected and saves stay consistent.  Supersedes the earlier OBSIDIAN_ANIM + FIST_PUMP pair that could drop state.

### `qol_skip_logo` (opt-in: `--skip-logo`)  *(C-shim)*

Skips the unskippable Adrenium logo movie that plays when the game first boots, cutting launch time noticeably.  The intro prophecy cutscene that plays immediately after is deliberately left alone.

**Why a naive NOP breaks this.**  The Adrenium-logo call lives inside a boot-time state machine (`FUN_0005f620`).  The instructions around it aren't just "play a movie" — they form a tightly-coupled sequence that reads `play_movie_fn`'s `AL` return value to decide whether to enter the movie-polling state or skip to the next movie:

```
0x05F6DF: 55                 PUSH EBP             ; EBP = 0 (scratch zero); char-flag arg
0x05F6E0: 68 50 E1 19 00     PUSH 0x0019E150      ; &"AdreniumLogo.bik"
0x05F6E5: E8 96 92 FB FF     CALL play_movie_fn   ; __stdcall — callee pops 8 B via `ret 8`
0x05F6EA: F6 D8              NEG AL               ; CF = (AL != 0)
0x05F6EC: 1B C0              SBB EAX, EAX         ; EAX = 0 (skip) or -1 (poll)
0x05F6EE: 83 C0 03            ADD EAX, 3          ; state = 3 (skip) or 2 (poll)
0x05F6F1: A3 1C F6 1B 00     MOV [0x001BF61C], EAX
```

Replacing the 10-byte `PUSH imm32; CALL rel32` pair with 10 NOPs (as an earlier version of this patch tried) corrupts the game in two ways: `PUSH EBP` leaks 4 bytes of stack every iteration, and `NEG AL` operates on whatever garbage AL happens to hold from a prior function — so the state machine drifts into **case 2 (poll a movie that never started)** and spins forever.  That's the black-screen-on-boot symptom.

**C-shim implementation.**  A `TrampolinePatch` replaces **only the 5-byte CALL** at VA 0x05F6E5 with `CALL rel32` into [`azurik_mod/patches/qol_skip_logo/shim.c`](../azurik_mod/patches/qol_skip_logo/shim.c).  The preceding two PUSHes are left intact, so the shim receives both `__stdcall` args on its stack and can clean them up the same way the real callee would.  The shim itself is a naked 5-byte stub:

```c
__attribute__((naked))
void c_skip_logo(void) {
    __asm__ volatile (
        "xorb %al, %al\n\t"   /* AL = 0 → state machine chooses case 3 (skip) */
        "ret  $8            "  /* __stdcall: pop the 2 caller-pushed args    */
    );
}
```

Compiled with `-Os` this is `30 C0 C2 08 00` (exactly 5 bytes).  It lands in the 16-byte VA-gap just past `.text` (file offset `0x0F01D0`, VA `0x001001D0`); the XBE's `.text` section header is grown by 5 bytes so the Xbox loader maps the new region executable.

```
BEFORE (5 B at VA 0x05F6E5):
  E8 96 92 FB FF     CALL play_movie_fn

AFTER (5 B at VA 0x05F6E5):
  E8 .. .. .. ..     CALL rel32 → 0x1001D0   ; shim in grown .text

INJECTED SHIM (5 B at VA 0x001001D0):
  30 C0              XOR AL, AL               ; return 0 (movie didn't start)
  C2 08 00           RET 8                    ; __stdcall pop of 2 args
```

The `NEG AL; SBB EAX, EAX; ADD EAX, 3; MOV [state], EAX` block at `0x05F6EA` is untouched and now always writes **state = 3**.  On the next main-loop tick, case 3 of the state machine runs and starts `prophecy.bik` normally.  The `AdreniumLogo.bik` string at file offset `0x196DB0` is left intact, keeping `.rdata` clean.  `verify-patches --strict` absorbs the trampoline, the shim landing pad, and the grown `.text` section-header fields into its whitelist.

**Escape hatch.**  Set `AZURIK_SKIP_LOGO_LEGACY=1` before applying to use the byte-level `PatchSpec` form instead.  That fallback rewrites the 10 bytes at VA `0x05F6E0` as `ADD ESP, 4; XOR AL, AL; NOP×5` — same semantics as the shim (pop the PUSH EBP leftover, force AL=0) but with no injected code.  Useful if the i386 PE-COFF toolchain (clang + `-target i386-pc-win32`) isn't available on the build host.

The adjacent call to `prophecy.bik` uses the same calling pattern at VA 0x05F73F.  Adding a parallel `qol_skip_prophecy` pack is a trivial follow-up — another 5-byte trampoline with the same shim reused, or its own byte-level `ADD ESP, 4 + XOR AL, AL` patch.

### `qol_skip_save_signature` (opt-in: Patches tab → QoL → `qol_skip_save_signature`)

Bypasses the HMAC-SHA1 signature check the save-file loader runs against every slot — lets `azurik-mod save edit`'s output load without re-signing, and makes save slots portable between consoles.

**Why this matters.**  Azurik signs each save with HMAC-SHA1 keyed by `XboxSignatureKey` — a runtime kernel global that lives in heap memory, is not statically recoverable, and differs per console / firmware.  Without this patch the only ways to produce a loadable edited save are:

1. Recover the key dynamically via `azurik-mod save key-recover` against an xemu RAM dump (per-session chore).
2. Round-trip through the game (write → let game save → load → write again).
3. Run on softmodded hardware / modified kernels that skip the check.

With this patch applied, **none of those are needed** — any save loads regardless of signature.

**The patch itself.**  Three bytes at VA `0x0005C990`, the prologue of `verify_save_signature`:

```asm
; Vanilla (first 3 bytes of a longer prologue):
0x5C990: 8A 81 0A 02 00 00    MOV AL, [ECX+0x20A]   ; flag byte
0x5C996: 83 EC 28             SUB ESP, 0x28
         ...                   ; HMAC compute + REPE CMPSD against signature.sav

; Patched (3-byte overwrite):
0x5C990: B0 01                MOV AL, 1             ; always report "verified"
0x5C992: C3                   RET
0x5C993: 02 00 00 ...          ; dead bytes (never reached)
```

The vanilla code already contains a `CMP AL, 0x7A` ("skip if first path char is `'z'`") bypass further down — we just force that bypass unconditionally by returning AL=1 before the SUB ESP / stack setup runs.  Zero stack imbalance (no push yet), zero calling-convention risk (`__thiscall` doesn't require callee-preserved EDI/ESI when they weren't pushed).

**What's untouched.**  `calculate_save_signature` (the sibling *write* function at VA `0x0005C920`) is left vanilla.  The game still computes a real signature when saving, so saves created on a patched XBE also load on a vanilla XBE.  The asymmetry is intentional.

**Verify with:**

```bash
azurik-mod verify-patches --xbe patched.xbe --original stock.xbe --strict
```

Expected delta: exactly **3 bytes** at file offset `0x0004C990..0x0004C992` (`8A 81 0A` → `B0 01 C3`).  Any other diff means another pack ran.  [`tests/test_qol_skip_save_signature.py`](../tests/test_qol_skip_save_signature.py) pins this end-to-end against the vanilla XBE.

### Player character swap (`--player-character <name>`)

Replaces the `garret4` string at file offset `0x1976C8` (VA `0x0019EA68`, in `.rdata`) with an arbitrary ≤11-char ASCII model name.  Not a pack — there's no GUI toggle yet, only the CLI flag.  Marked experimental; animation mismatches are likely.

---

## `player_physics`

Eight sliders, all patching `default.xbe` directly: **world
gravity**, **walk speed**, **roll (ground-state) speed**,
**climb speed**, **swim speed**, **jump height**, **air-control
speed**, and **wing-flap (double-jump) impulse**.  Each slider
is scoped to one physics axis and cannot cross-contaminate
(e.g. roll_scale no longer affects airborne horizontal speed
as it did in v1/v2 — see CHANGELOG under "player_physics v3").

### Gravity (`--gravity M_PER_S2`)

- VA `0x1980A8`, 4-byte float (file offset `0x190D08`).  Baseline bytes `CD CC 1C 41` = `9.8f`.
- Range `0.0 … 100.0` m/s² (weightless through ~10× Earth).
- Global — affects the player, enemies that fall, and projectile arcs.  Two other `9.8f` constants at `0x198704` and `0x198740` are unrelated (camera / animation scalars) and remain untouched.
- `--gravity 9.8` produces a byte-identical XBE so the `verify-patches --strict` whitelist diff stays clean.
- GUI: exact-value entry field next to the slider for precise tuning.

### Walk speed (`--walk-speed X`)

`FUN_00085F50` (walking ground state) computes per-frame
velocity as ``CritterData.run_speed × stick_magnitude``, where
``run_speed`` is 7.0 at runtime for the player.  We rewrite
the 6-byte ``MOV EAX,[EBP+0x34]; FLD [EAX+0x40]`` at VA
``0x85F62`` into ``FLD dword [abs <walk_va>]``, where the
shim-landed float equals ``7.0 × walk_scale``.  Only the
player's walking path is affected — enemy walking keeps
vanilla behaviour.

Range `0.1 … 10.0`, default `1.0` (byte-identity).

### Roll (ground-state) speed (`--roll-speed X`)

**v3 (April 2026)** — targets the isolated rolling-ground-state
constant at VA ``0x001AAB68`` (vanilla 2.0), used by
``FUN_00089A70``'s velocity FMUL at VA ``0x00089B76``:

```asm
00089b6d: D9 47 04           FLD  [EDI + 0x4]       ; dt
00089b70: D8 8F 24 01 00 00  FMUL [EDI + 0x124]     ; × magnitude
00089b76: D8 0D 68 AB 1A 00  FMUL [0x001AAB68]      ; × 2.0  ← target
```

The constant has **exactly one reader** in the entire binary,
so the patch is a direct 4-byte float overwrite.  Pre-v3
versions rewrote the FMUL at 0x849E4 (the WHITE-button boost
inside ``FUN_00084940``) and force-always-on'd bit 0x40 of
the input flags, which coupled ``roll_scale`` into airborne
horizontal speed via ``FUN_00089480``'s shared ``magnitude``
variable.  That coupling bug is fixed in v3 — the
FMUL-at-0x849E4 and the force-on sites now stay at vanilla.

Range `0.1 … 10.0`, default `1.0`.

### Climb speed (`--climb-speed X`)  *(new April 2026)*

``FUN_00087F80`` (climbing / hanging-ledge state) reads its
baseline climb velocity from the .rdata float at VA
``0x001980E4`` (vanilla 2.0).  Used twice, both inside the
climbing function:

```asm
00087fa7: D9 05 E4 80 19 00  FLD  [0x001980E4]      ; primary climb vel
00088357: D9 05 E4 80 19 00  FLD  [0x001980E4]      ; secondary climb retarget
```

Direct 4-byte overwrite scales both climb-motion paths
uniformly.  Range `0.1 … 10.0`, default `1.0`.

### Swim speed (`--swim-speed X`)

The swim-state function `FUN_0008b700` is entered via the
state dispatcher (state 6) once the "in water" flag at
`entity + 0x135 & 1` trips.  The stroke velocity is
computed at VA `0x8B7BF`:

```asm
FLD  [ESI + 0x124]              ; magnitude
FMUL float [0x001A25B4]         ; × 10.0  ← the swim coefficient
```

Shared `10.0` at VA `0x001A25B4` has 8 readers globally, most
unrelated to player movement.  We patch only the player site:
rewrite the 6-byte `FMUL [abs32]` at VA `0x8B7BF` to
reference an injected `10.0 × swim_scale` float.

- Independent of `walk_scale` and `roll_scale` by construction
  (different site, different constant, no cross-coupling).
- Magnitude feeding the FMUL is the `FUN_00084940` output,
  so WHITE-button-held underwater produces a 3× stack on top
  of swim_scale (vanilla WHITE-swim = 30 × raw_stick).

Range `0.1 … 10.0`, default `1.0` (byte-identity).

### CLI

```bash
# Just gravity
azurik-mod apply-physics --iso iso/Azurik.iso --output iso/lowgrav.iso \
    --gravity 4.9

# Turbo-walk + faster rolling + faster climbing + faster swimming
azurik-mod apply-physics --xbe default.xbe \
    --walk-speed 1.5 --roll-speed 2.0 --climb-speed 2.0 --swim-speed 1.5

# Full suite baked into a randomize-full build
azurik-mod randomize-full --iso iso/Azurik.iso --output out.iso \
    --seed 42 --gravity 7.0 \
    --player-walk-scale 1.2 --player-roll-scale 1.5 \
    --player-climb-scale 1.5 --player-swim-scale 1.3 \
    --player-jump-scale 1.5 --player-air-control-scale 1.2 \
    --player-flap-scale 1.5
```

`--player-run-scale` / `--run-speed` are still accepted as
deprecated aliases for `--player-roll-scale` / `--roll-speed`.

### GUI

The Patches page renders 10 working `ParametricSlider` widgets under `player_physics`: `gravity`, `walk_speed_scale`, `swim_speed_scale`, `jump_speed_scale`, `air_control_scale`, `flap_height_scale` ("Wing-flap: 1st flap height"), `flap_below_peak_scale` ("Wing-flap: far-descent recovery"), `wing_flap_ceiling_scale` ("Wing-flap: altitude ceiling"), `flap_entry_fuel_cost_scale` ("Wing-flap: fuel cost per flap", range [-5, 5] step 0.1 — vanilla 1.0, 0.0 = infinite, negative = refund), and `flap_descent_fuel_cost_scale` ("Wing-flap: descent penalty fuel", range [-0.05, 0.05] step 0.001).  Slider values live on `AppState.pack_params["player_physics"]` and carry a long-form description rendered via a hover-tooltip `ⓘ` glyph next to the label.

All five restored shim packs (`flap_at_peak`, `root_motion_roll`, `root_motion_climb`, `slope_slide_speed`, `animation_root_motion_scale`) are registered but marked `deprecated=True` — user testing confirmed no observable in-game effect for any of them.  They're hidden from the Patches page but remain importable for CLI / RE use.  `no_fall_damage`, `infinite_fuel`, and `wing_flap_count` remain deleted in favour of config-editor workarounds.  The only active player-physics surface is `player_physics` (10 sliders).

**Config-editor workarounds for the deleted cheat packs**:
- **No fall damage** → `config.xbr` / `damage` section: raise fall-height thresholds.  Or `critters_damage` → bump player row's `hitPoints`.
- **Infinite fuel** → `config.xbr` / `armor_properties`: set `fuel_max` to a large number; or `attacks_anims`: zero every `Fuel multiplier`.
- **Wing-flap count** → `config.xbr` / `armor_properties`: edit the `Flaps` column per armor row (fire1..3 / water1..3 / air1..3 / earth1..3) — read fresh each flap so changes land immediately.

### Diagnostics

Run `azurik-mod inspect-physics --iso built.iso` to dump the
current state of every physics site — each will show
`[VANILLA]`, `[PATCHED]` (with the injected float), or
`[DRIFTED]` (bytes don't match either).  Use this first when a
patch "doesn't seem to do anything" to confirm bytes actually
landed in the built ISO.

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
       category="cosmetic",    # auto-registers a new GUI tab for you
       tags=(),                # optional secondary badges
   ))
   ```

5. Add to [`azurik_mod/patches/__init__.py`](../azurik_mod/patches/__init__.py).
6. Update this file.

The GUI's generic Patches page ([`gui/pages/patches.py`](../gui/pages/patches.py)) and `azurik-mod verify-patches` will pick the new pack up automatically.
