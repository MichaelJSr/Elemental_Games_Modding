# Changelog

## Unreleased

### C-shim modding platform (Phase 2 C1 — player-speed shim, first real deliverable)

- **Walk-speed and run-speed sliders are back** on the Patches page.
  The earlier attempt wrote to `config.xbr`'s `attacks_transitions`
  cells, which Ghidra later showed were dead data at runtime.  C1
  replaces that with a direct `default.xbe` patch at the real
  per-frame player-movement call site (`FUN_00085f50`):
  * VA `0x85F62` (`MOV EAX,[EBP+0x34]; FLD [EAX+0x40]`, 6 B) rewritten
    to `FLD [<injected walk-speed VA>]` — the base speed loaded each
    frame now comes from a per-game float instead of the dead
    `entity->runSpeed` field.
  * VA `0x849E4` (`FMUL [0x001A25BC]`, 6 B) rewritten to
    `FMUL [<injected run-multiplier VA>]` — the 3.0 constant at
    `0x001A25BC` has **45** other read sites (collision, AI, audio,
    etc.), so the patch injects a per-player copy rather than
    mutating the shared one.
  * Both floats land via the Phase 2 A1 shim-landing infrastructure
    (`.text` trailing-padding gap preferred, `SHIMS` appended section
    fallback).  Defaults `walk_scale = run_scale = 1.0` are
    byte-identical to vanilla.
- **`PatchPack.dynamic_whitelist_from_xbe`** — new optional
  callback on `PatchPack` that computes extra whitelist ranges from
  the patched XBE bytes at verify time.  Powers
  `verify-patches --strict` for packs whose apply function emits
  patches at apply-time-chosen addresses (the injected float VAs).
  `cmd_verify_patches` invokes the callback and merges its ranges
  into the whitelist diff alongside the static contributions.
- **`apply_player_speed(xbe_data, walk_scale, run_scale)`** now
  operates on the XBE directly (was config.xbr).  The CLI flags
  `--walk-speed` / `--run-speed` on `apply-physics` and
  `--player-walk-scale` / `--player-run-scale` on `randomize-full`
  route through the new path.
- **Tests (+11)** — `tests/test_player_speed.py` is rewritten end
  to end: vanilla-site invariants, apply shape on a real XBE,
  defaults-are-no-op, reapply rejection, gravity/speed cross-
  independence, and the dynamic whitelist callback behaviour on
  both vanilla and patched XBEs.  Full suite: 129 passing.
- **Docs** — `docs/PATCHES.md` `player_physics` section fully
  rewritten with the Ghidra walkthrough, instruction layouts, and
  slider semantics.

### C-shim modding platform (Phase 2 A3 — vanilla-function calls)

- **Shims can now call any registered vanilla Azurik function.**
  Phase 1 shims had to be fully self-contained; Phase 2 A3 lets a
  shim do e.g. `play_movie_fn(name, 0)` and have the resulting
  `CALL rel32` land directly at Azurik's real VA.  No runtime
  thunks — just a name → VA registry consulted by `layout_coff`
  when it encounters undefined-external COFF symbols.
- **`azurik_mod/patching/vanilla_symbols.py`** — new `VanillaSymbol`
  dataclass + registry of exposed Azurik functions.  Each entry
  declares its C name, VA, calling convention (cdecl / stdcall /
  fastcall), and argument-byte count; the mangled COFF name is
  computed from those.  Seeded with `play_movie_fn@8` (0x18980)
  and `poll_movie@4` (0x18D30).
- **`shims/include/azurik_vanilla.h`** — matching C prototypes for
  shim authors.  `#include "azurik_vanilla.h"` and call any
  declared function as you would in a normal C program; the layout
  pass handles the VA resolution.
- **`layout_coff(..., vanilla_symbols=...)`** — new optional
  parameter.  `_resolve_symbol_va` consults the dict when a symbol's
  `section_number <= 0` (undefined external); truly unresolved
  symbols still raise with an actionable error pointing shim authors
  at the registry + header.
- **Tests (+12)** — `tests/test_vanilla_thunks.py` covers mangling
  rules (cdecl / stdcall / fastcall), registry accessors, synthetic
  COFF resolution, a real compiled shim (`shims/src/_vanilla_call_test.c`)
  that calls `play_movie_fn` and has its REL32 verified to land at
  0x18980, and a drift guard that refuses to let the Python
  registry and the C header disagree.
- **Docs** — `docs/SHIMS.md` "Calling a vanilla function from a
  shim" walkthrough added.

### C-shim modding platform (Phase 2 A1+A2 — headroom + relocations)

- **Unbounded shim sizes** — `append_xbe_section` now implements
  real XBE surgery: grows the section-header array in place (shifts
  every post-array byte, rewrites the 7 image-header pointer fields
  and 3 per-section pointers whose targets moved), places section
  data at EOF with `FILE_ALIGN` / `VA_ALIGN` alignment, bumps
  `num_sections`, `size_of_headers`, and `size_of_image`.  Phase 1's
  16-byte `.text` VA-gap ceiling is gone — shims of any practical
  size spill into a per-apply `SHIMS` section instead.
- **Automatic landing strategy** — `apply._carve_shim_landing`
  picks the least-invasive home for each shim: existing `.text`
  slack first, then `.text` growth into the adjacent VA gap, then
  a newly-appended `SHIMS` section.  Subsequent applies extend the
  same `SHIMS` section in place rather than spawning new ones.
- **Relocation-aware COFF loader** — `coff.layout_coff` parses
  per-section relocation tables and applies `IMAGE_REL_I386_DIR32`
  and `IMAGE_REL_I386_REL32` fixups after section placement, using
  the resolved XBE VAs for each symbol's owning section.  Metadata
  sections (`.debug$S`, `.llvm_addrsig`, `.drectve`, `.xdata`,
  `.pdata`) are filtered out so they don't consume SHIMS space or
  force bogus relocations.  Supports arbitrary shim section layouts
  (`.text` + `.rdata` + `.data` + `.bss`), with cross-section
  references resolved correctly.
- **Auxiliary-record preservation** — the COFF symbol-table walker
  now keeps aux records as placeholder entries so relocation
  `symbol_index` values stay aligned with the raw on-disk table.
- **Section-name long-form support** — `/NN` encoding used by clang
  for section names >= 8 chars is now resolved (previously left as
  a literal `"/29"`-style placeholder).
- **Trampoline apply pipeline** picks the right loader path
  automatically: zero-relocation shims stay on the minimal
  `extract_shim_bytes` fast path; anything with relocations goes
  through `layout_coff` + in-place overwrite of placeholder bytes.
- **Tests (+19)** — `tests/test_append_xbe_section.py` (11) covers
  the header-shift round-trip, pointer-fixup regression guards, and
  the `_carve_shim_landing` fallback.  `tests/test_coff_relocations.py`
  (8) compiles a real reloc-bearing shim (`shims/src/_reloc_test.c`)
  on demand and verifies every DIR32 / REL32 field is written with
  the resolved VA.  113 total tests passing.

### Patches

- `player_physics` walk/run speed sliders removed from the Patches
  page.  Investigation (Ghidra on FUN_00049480 / FUN_0007e7c0) showed
  the `walkSpeed` / `runSpeed` cells in `config.xbr`'s
  `attacks_transitions` section are dead data: the engine's only
  `walkSpeed` string xref is a lookup against `critters_critter_data`
  (which doesn't carry that row), so the default 1.0 is always used
  regardless of the cell value.  The `apply_player_speed` helper and
  `--player-walk-scale` / `--player-run-scale` CLI flags stay in the
  tree for a future Phase 2 fix once the real storage location is
  found; the sliders no longer register on the GUI pack so users
  don't think the feature works.
- `player_physics` gravity slider: widened range to 0.0..100.0 m/s²
  (previously 0.98..29.4) so you can go from weightless floating
  through ~10x Earth.  The slider widget's numeric entry field
  accepts any exact value inside that range, giving finer precision
  than the step size alone.

### GUI

- Single build entry point: removed the "Build randomized ISO"
  button from the Randomize page.  The page now mirrors its widget
  state into `AppState.randomize_config` on every change, and the
  "Start build" button on Build & Logs reads that snapshot directly.
  One place to click, no double-click required.
- Pack descriptions tightened across every pack (fps_unlock, qol_*,
  player_physics) to 1–2 short sentences for faster scanning.
- `ParametricSlider` widget now shows the current value alongside
  the default in its header, prints the slider's min/max range on
  the right, and widens the exact-value entry to 12 chars.

### C-shim modding platform (Phase 1)

- New `TrampolinePatch` site descriptor in `azurik_mod.patching.spec`
  joins `PatchSpec` / `ParametricPatch`.  Instead of declaring raw
  byte swaps, a trampoline patch names a C function whose compiled
  PE-COFF `.o` gets injected into the XBE; a 5-byte `CALL` / `JMP`
  rel32 at the declared VA diverts control flow into the shim.
- New `shims/` tree: C sources (`src/`), shared freestanding headers
  (`include/azurik.h`), and an Apple-clang wrapper
  (`toolchain/compile.sh` emitting i386 PE-COFF via
  `-target i386-pc-win32`).
- New `azurik_mod.patching.coff` — minimal PE-COFF reader (sections
  + symbols only, no relocations) — feeds shim bytes + entry-point
  offsets into the apply pipeline.
- `find_text_padding()` generalised: reports both in-section trailing
  zero slack AND the adjacent VA-gap growth window.  `grow_text_section()`
  commits the matching `virtual_size` / `raw_size` bump in the XBE
  section header so the Xbox loader maps injected bytes as executable.
- `apply_trampoline_patch()` / `verify_trampoline_patch()` do the
  end-to-end work (COFF parse, landing carve, section grow, rel32
  emit, NOP fill) and stay idempotent on a second apply.
- `qol_skip_logo` now replaces only the 5-byte `CALL play_movie_fn`
  at VA 0x05F6E5 with a C shim that returns `AL=0` and does `RET 8`,
  matching `play_movie_fn`'s `__stdcall` contract.  The preceding
  `PUSH EBP; PUSH 0x0019E150` instructions run as normal so the shim
  sees both args on its stack.  This replaces the earlier 10-byte
  NOP attempt, which left `AL` undefined and leaked 4 bytes of stack
  per iteration — the state machine at `FUN_0005F620` would drift
  into `case 2` (poll a movie that never started) and hang on a
  black screen at boot.  The legacy `SKIP_LOGO_SPEC` escape hatch
  (`AZURIK_SKIP_LOGO_LEGACY=1`) was simultaneously fixed to write
  `ADD ESP, 4; XOR AL, AL; NOP×5` with the same semantics.
- `verify-patches --strict` now absorbs trampoline sites, their
  shim landing pads, and the grown `.text` section-header fields
  into the whitelist diff so a legitimately-patched XBE reports
  clean.
- New docs: [docs/SHIMS.md](docs/SHIMS.md) (authoring workflow),
  `shims/README.md` (toolchain + directory map).  New tests:
  `tests/test_trampoline_patch.py` (18 tests — COFF, XBE surgery,
  apply+verify end-to-end) and an expanded
  `tests/test_qol_skip_logo.py`.

### GUI

- Rebranded launcher scripts from `Launch Randomizer.*` to
  `Launch Azurik Mod Tools.*`.  The macOS / Linux `.command` launcher
  now probes Homebrew (`/opt/homebrew/bin`, `/usr/local/bin`), pyenv
  shims, and the Python.org framework before giving up, sources the
  user's zsh / bash profile, and exec-searches
  `python3.12 … python3.10 → python3 → python`, fixing the
  "Python was not found" error on Finder double-clicks.
- Every page body is now wrapped in a `ScrollableFrame` (Canvas +
  scrollbar with `<Enter>/<Leave>`-scoped mouse-wheel bindings) so
  long pages (Patches, Randomize) remain reachable on short windows.
- The GUI auto-detects the first `.iso` in the repo's `iso/` folder
  at startup and preloads it into the Project page's ISO picker and
  auto-generated output path.
- All shuffle pool and QoL checkboxes in the Randomize page now
  default to OFF so an untouched build is a no-op.  Parametric
  sliders continue to default to their baseline values
  (gravity 9.8 m/s², walk / run 1.0×).

### Patches

- `qol_skip_logo` (new pack, default OFF): NOPs the 10-byte
  `PUSH &"AdreniumLogo.bik"; CALL play_movie` pair at VA 0x05F6E0 so
  the unskippable Adrenium logo movie no longer plays on boot.
  Noticeably shortens game launch.  The intro prophecy cutscene is
  left alone.  Surgical instruction-level patch, stack-balanced,
  passes `verify-patches --strict`.  Opt in via `--skip-logo` CLI
  flag or by ticking the pack on the Patches page.
- `qol` split: the single `qol` pack has been replaced by three
  independently-toggleable packs so users can pick exactly which QoL
  tweaks they want.  All default to OFF:
  * `qol_gem_popups` — hide the "Collect 100 &lt;gem&gt;" popup that
    appears the first time you collect each gem type.  (The old
    description said "You found X for the first time!" which was
    never the actual in-game wording.)
  * `qol_other_popups` — hide the remaining first-time popups: swim
    tutorial, first key pickup, first health pickup, first of each
    elemental / chromatic power-up, and the six-keys-collected
    milestone.  The death-screen "gameover" popup is deliberately
    left alone.
  * `qol_pickup_anims` — skip the post-pickup celebration animation.
  All three use the same "null the first byte of the localisation
  resource-key path" mechanism so the game's popup lookup silently
  fails; the popup text itself lives in a separate localisation
  `.xbr` file, not in `default.xbe`.
  CLI: former `--no-qol` / `--no-gem-popups` / `--no-pickup-anim` opt-out
  flags are deprecated (still accepted as no-ops) and replaced by
  opt-in `--gem-popups` / `--other-popups` / `--pickup-anims`.
- `fps_unlock`: raised the simulation step cap from 2 to 4.  At 60 Hz
  sim cap=2 causes game time to drift below real time whenever render
  FPS dips below 30; cap=4 preserves real-time game speed down to
  15 FPS rendered (matching vanilla's 2-step coverage at 30 Hz sim).
  Both `CMP ESI, 0x4` (TRUNC) and `PUSH 0x4` + two `FADD ST0,ST0`
  (CATCHUP) are pinned by `tests/test_fps_safety.py`.
- `player_physics` (new pack): slider-driven world gravity
  (`--gravity` / GUI slider) and player walk / run speed multipliers
  (`--player-walk-scale`, `--player-run-scale`).  Framework additions:
  `ParametricPatch`, `apply_parametric_patch`, `verify_parametric_patch`
  under `azurik_mod.patching`, plus a `ParametricSlider` GUI widget
  that picks up every `ParametricPatch` automatically.

## v0.3.0 (2026-04-17) — Repo reorganization + 60 FPS unlock

### Structure

- Flat `tools/randomizer/*.py` replaced by a pip-installable
  `azurik_mod` library package with sub-packages `patching/`,
  `patches/`, `iso/`, `randomizer/`, and `config/`.
- GUI moved out of `tools/randomizer/azurik_gui/` to a top-level
  `gui/` package with a `tabs/` subpackage; now calls the library
  in-process (no more subprocess).
- `pyproject.toml` defines `azurik-mod` and `azurik-gui` console
  entry points — install with `pip install -e .`.
- Vendored `xdvdfs.exe` removed; `azurik_mod.iso.xdvdfs` auto-downloads
  the right binary per OS into the user cache, or falls back to PATH
  (`cargo install xdvdfs-cli`).
- `claude_output/` renamed to `azurik_mod/config/`.
- Outer-workspace analysis scripts moved to `scripts/analysis/`; vanilla
  config dumps and example mods moved to `examples/`.
- Tests moved to top-level `tests/` and expanded: patch-loader
  round-trip + BSOD guard invariants (21 tests total).

### 60 FPS unlock (new patch pack)

- `azurik_mod.patches.fps_unlock` implements 50 PatchSpec sites: lifts
  the VBlank cap, halves 28 subsystem `1/30` timesteps, doubles the main
  `30.0` rate, uses FISTP truncation to avoid the 60 → 30 death spiral,
  and pins the simulation step cap at 2 (matching vanilla reentrancy).
- `safety_critical` guard on TRUNC + CATCHUP patches with a unit test
  (`tests/test_fps_safety.py`) that fails any regression to step cap 4.
- New `azurik-mod verify-patches` subcommand: applies / verifies every
  site, whitelist-diffs against an unpatched original, returns non-zero
  on mismatch — CI-safe.

### QoL unification

- Former `OBSIDIAN_ANIM` + `FIST_PUMP` pair replaced by a single
  `PICKUP_ANIM` PatchSpec (VA 0x0413EE) that preserves save persistence.
- CLI flag pair `--no-obsidian-anim` / `--no-fist-pump` collapsed into
  `--no-pickup-anim`.

---

## v0.2.0 (2026-03-15) — Major Update

### Critical Bug Fix
- **Config values are now 64-bit doubles** — Previous versions read/wrote 32-bit floats at the wrong offset, producing incorrect values and corrupting data on write. All config patching now uses correct 8-byte IEEE 754 doubles. Example corrections: initial_fuel was 2.5, actually 8.0; fuel_inc_gems was 3.25, actually 100.

### New Features

#### Randomizer
- **Level connection randomization** — Shuffles exits between levels within path-length groups. Clears start spots for safe spawning at level origin. Disabled by default (may cause unsolvable seeds).
- **Custom item pool** — Choose exactly how many of each power and fragment to include in the randomization pool via GUI spinboxes or `--item-pool` CLI flag.
- **Custom gem weights** — Set relative weights for diamond, emerald, sapphire, ruby, and obsidian gem distribution. Higher weight = more frequent.
- **Obsidian lock cost** — Customize the obsidian cost per temple lock (default 10, locks at 10/20/30.../100). GUI spinbox or `--obsidian-cost` CLI flag.
- **Obsidians included in gem shuffle** — Previously excluded, obsidians now randomize with other gems. Total shuffled: 97 gems across 20 levels.
- **Force build on unsolvable seeds** — When the solver can't find a completable placement, the GUI offers "Build Anyway" instead of just failing.
- **Player character swap** — Experimental: replace the player model with any character (e.g., `--player-character evil_noreht`). Animations may not match. Max 11 characters.

#### Entity Editor (New Tab)
- **8 editable sections** with 8,466+ patchable values:
  - Entity Stats — walkSpeed, runSpeed, attackRange, HP, knockback per entity (108 entities)
  - Entity Damage Multipliers — 62 damage type vulnerability multipliers per entity (107 entities)
  - Damage Types — Base damage, cost, delay, freeze for all 57 attack types (player + enemy)
  - Player Global Settings — Flat list: initial HP (200), max HP (400), fuel per upgrade, gems needed (100), fall damage thresholds
  - Armor Properties — All 19 armor types × 3 tiers: protection, HP, cost, hits, time, flaps
  - Critters Movement & AI — Provoke distance, stalk, flee, turn rate per enemy
  - Critters Flocking — Boids parameters
  - Enemy Damage Overrides — Per-enemy damage values
- **Randomize stats** — Set min/max percentage range, randomize single entity or entire section
- **Load from ISO** — Read current default values from the game ISO for reference
- **Export Mod JSON** — Save edits as a mod file for manual application
- **Auto-integration** — Entity editor edits automatically included when building randomized ISO

#### QoL Patches
- **All pickup celebration animations disabled** — JMP at VA 0x413EE skips the linked-list cleanup and counter update that keep the celebration animation data live, while FUN_00061360 (collected flag) and FUN_0006FC90 (pickup counter) still run for save persistence
- **Two QoL patches now**: gem first-pickup popups (5), pickup celebration animations

#### GUI Improvements
- Warning labels on Keys, Barriers, and Connections checkboxes ("may cause unsolvable seeds")
- Config Editor tab shows "Work in Progress" banner
- Entity Editor scroll only when content overflows
- Window size increased to accommodate new controls

### Bug Fixes
- **Missing entity scanner fix** — Added all power and fragment names to DIRECT_SEARCH_NAMES fallback list. Fixed 3 entities (f4/frag_fire_2, w2/power_water, a5/power_air) that were missed by the standard 1.0f marker scanner, causing item duplication.
- **Config editor "Section '_meta' not found"** — Backend now correctly reads sections from `data["sections"]` instead of top-level keys.
- **xdvdfs Windows path fix** — Uses POSIX forward slashes for in-image paths, fixing "Entry does not exist" errors when GUI runs from native Windows (not Git Bash).
- **Town barrier item scaling** — Non-native items placed behind obsidian barriers are scaled to 0.5x to prevent protruding through force fields.

### Engine Research (for developers)
- 39 registered node types fully decoded with handler addresses
- Node graph connection format decoded (12-byte triplets in NDBG)
- Complete damage system: 56 damage types, vulnerability matrix, armor system
- AI pathfinding: no navmesh, direct-to-target + Boids flocking
- Level loading: full teardown/load pipeline, start spot resolution
- Save system: 3-tier persistence, randomizer confirmed save-safe
- Collection fourcc from critters_engine config table (name-driven)
- Cross-type spawning confirmed (enemies at item locations and vice versa)
- Ghidra structures header (azurik_structures.h) with 26 structs and 12 enums

## v0.1.0 (2026-03-13) — Initial Release

- Full-game randomizer with forward-fill logic solver
- Major items, keys, gems, barriers randomization
- Seed-based reproducibility
- GUI with category checkboxes
- QoL patches: gem popups, pickup animations
- CLI and GUI interfaces
