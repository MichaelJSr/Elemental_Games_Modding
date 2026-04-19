# Changelog

## Unreleased

### Entity Editor — critical build-wire fix + UX refinements

The Entity Editor tab had a **silent orphan bug**: users could make
hundreds of property edits, click "Start build" on the Build page,
and see their edits quietly discarded.  The tab's
``get_pending_mod()`` method was defined but never called from
anywhere, and ``RandomizerConfig.config_edits`` was never populated
from the UI — the full edit buffer simply evaporated at build time.

This release wires the editor into the build pipeline and adds
several UX improvements:

#### Critical fix: edits now reach the build

- ``BuildPage._merge_config_edits`` folds the editor's pending mod
  into the CLI's ``--config-mod`` JSON at build time.  Both
  grouped ``sections`` (variant records) and ``_keyed_patches``
  (keyed-table cells) merge correctly; on per-cell conflict the
  editor's value wins over any file-sourced ``config_edits``.
- Deep-copy invariant: the merge never mutates the input dicts,
  so file-sourced edits remain intact if the build is retried.
- Build log now surfaces how many editor edits contributed to a
  given run (``+ Entity Editor contributes N pending edits``).

#### UX refinements

- **Entity search/filter**: typing in the new "Filter entities:"
  box narrows the dropdown live — essential for sections with
  500+ critters where scrolling is hopeless.  Status label
  shows "N of M match" / "M entities" depending on filter state.
- **Per-entity edit indicator**: entities with pending edits are
  prefixed with a bullet + count ("● goblin (3)") in the dropdown.
  A green "(3 edits)" label next to the combo tracks the currently-
  selected entity in real time.
- **Reset This Entity** button: clears edits for the currently-
  selected entity only, with a confirm dialog.  Complements the
  existing "Reset All Edits" (which also now has a confirm).
- **Import Mod JSON** button: round-trips any previously-exported
  mod JSON back into the editor's edit buffer — merges with
  existing edits rather than replacing them.  Parses both the
  grouped-sections and ``_keyed_patches`` shapes; malformed
  entries are skipped (not fatal) with a status-line summary.
- **Edit-count breakdown**: the edit-count label now shows
  "N edit(s) across X entities / Y sections" instead of just
  a flat count — gives users a sense of the scope of their changes.

#### Internal reshuffle

- ``_on_entity_change`` + ``_rebuild_property_grid`` defensively
  normalise the combobox value to strip the edit-indicator
  decoration before using it as a lookup key.  ``_randomize_entity``
  and every other entity-reading code path uses the same
  normalisation — no more stray decorations leaking into registry
  lookups.

#### Tests

18 new tests in ``tests/test_entity_editor.py`` (total 277, up
from 259):

- Surface-area drift guard (every expected method exists).
- Label-decoration reversibility (``_format_entity_label`` /
  ``_unformat_entity_label`` round-trip, idempotent on
  undecorated input, tolerant of manually-typed parens).
- ``get_pending_mod`` shape (variant → ``sections``, keyed →
  ``_keyed_patches``, empty edits → ``None``).
- **Build-page merge** — the critical orphan-fix:
  7 subtests covering every combination of file / editor edits
  including conflict resolution (editor-wins) and
  non-mutation invariants.
- Import round-trip (merge vs replace, malformed-entry
  skipping).

### VA audit + new `AZURIK_PLAYER_STATE_PTR_ARRAY_VA` anchor

Comprehensive VA-correctness sweep via Ghidra MCP + real XBE bytes.
All 16 existing VA anchors + 5 vanilla-function entries verified;
no drift.  New regression suite `tests/test_va_audit.py` (5 tests,
26 subtests) pins every anchor with:

- Section membership (`.rdata` vs `.data` vs BSS) — catches
  accidental VA drift that lands in the wrong section silently.
- Byte-content predicates for initialised constants (gravity == 9.8,
  run multiplier == 3.0, `garret4\0`, float constants 0.0/0.5/1.0,
  active-player index == 4).
- BSS verification (empty-past-raw-size OR zero-filled on disk) for
  runtime-init anchors.
- First-byte prologue check on every vanilla function VA.
- **Drift guard**: regex-scans `azurik.h` for every `AZURIK_*_VA`
  macro and fails if one isn't covered by `ANCHOR_EXPECTATIONS`,
  so new anchors can't land without a matching audit entry.

One new anchor added during the gap analysis:
- **`AZURIK_PLAYER_STATE_PTR_ARRAY_VA` = `0x001BE314`** — 4 × 4-byte
  per-player state-object pointer slots, indexed by the XInput
  polling path (`FUN_000A2880`).  BSS; pairs naturally with the
  controller-state block at `0x0037BE98`.

Also verified against Ghidra:
- `AZURIK_CONTROLLER_STRIDE = 0x54` confirmed by
  `FUN_000A2880`'s ``IMUL ESI, ESI, 0x54`` at VA `0x000A288D`.
- All 151 static kernel ordinals in `AZURIK_KERNEL_ORDINALS`
  match the XBE thunk table at `0x18F3A0` exactly (zero drift).
- `EXTENDED_KERNEL_ORDINALS` has no ordinal collisions with the
  static set.
- Spot-checks pass for 10 canonical ordinals across static +
  extended (DbgPrint, NtClose, NtCreateFile, NtOpenFile,
  KeQueryPerformanceCounter, HalReturnToFirmware, RtlInitAnsiString,
  DbgBreakPoint, RtlZeroMemory, XboxKrnlVersion).

### FUN_00085700 gravity-integration wrapper + save-file format scaffold

Two substantial additions in one pass.  Both landed with full test
coverage + documentation; both exposed through the standard
authoring surfaces (shim C headers, Python module, CLI).

#### Gravity-integration wrapper (A3-plus)

Vanilla `FUN_00085700` uses an MSVC-style fastcall + RVO ABI
(`ECX + EDX + EAX-for-output + ESI-for-context + stack float`)
that no clang calling-convention attribute expresses natively.
New infrastructure to bridge the gap:

- **Inline-asm wrapper** at `shims/shared/gravity_integrate.c`
  exposes a clean `stdcall(20)` C API (`azurik_gravity_integrate`)
  and manually sets up every register inside a single atomic
  inline-asm block before the CALL — so clang can't reorder
  register setup past the EAX write.  Satisfies `__fltused`
  locally via an `__asm__` label so the wrapper has zero
  external dependencies beyond the vanilla target.
- **`gravity_integrate_raw` registered** in `vanilla_symbols.py`
  as `fastcall(8) → 0x00085700` (mangled
  `@gravity_integrate_raw@8`).  The "fastcall 2-reg" signature
  is a deliberate lie to clang so the REL32 lands; the EAX/ESI
  setup happens only in the wrapper's asm.
- **New header** `shims/include/azurik_gravity.h` with the clean
  wrapper prototype + a clearly-marked internal declaration of
  the raw vanilla symbol for drift-guard purposes.
- **Drift guard generalised**: `tests/test_vanilla_thunks.py`
  now accepts declarations in `azurik_vanilla.h` OR companion
  shim headers (listed in `_COMPANION_HEADERS`).
- **13 new tests** in `test_gravity_wrapper.py` covering the
  registry entry, wrapper compilation + byte shape + single-REL32
  invariant, end-to-end layout_coff → REL32 resolves to the
  correct vanilla VA, and header-doc-warning presence.

#### Save-file format — initial scaffold

New top-level Python module `azurik_mod.save_format` + CLI
subcommand `azurik-cli save inspect` for introspecting Azurik
save slots exported from xemu's HDD image.

- **Xbox-standard container files fully decoded**:
  - `SaveMetaXbx` / `TitleMetaXbx` — UTF-16-LE key/value parser
    with lossless byte-identical round-trip, field get/set,
    Unicode support, binary-tail preservation.
  - `SaveImage.xbx` / `TitleImage.xbx` — opaque bytes (image
    swizzle decoding deferred).
- **Azurik `.sav` scaffold**:
  - `SaveHeader` — 20-byte fixed prologue
    (`magic / version / payload_len / checksum / reserved`)
    with round-trip + `magic_as_ascii()` convenience.
  - `AzurikSaveFile` base + `SignatureSav` / `LevelSav`
    subclasses for profile-level / per-level saves.
    Current decoder emits a single opaque `SaveChunk`; the
    `iter_chunks()` extension point is where future field-level
    decoders plug in.
  - Path-based dispatch: `AzurikSaveFile.from_bytes(..., path=...)`
    returns the right subclass based on filename.
- **`SaveDirectory`** recognises every file type in a save slot
  (SaveMeta / TitleMeta / SaveImage / TitleImage / `.sav` files)
  and keeps unknowns in `extra_files`.  JSON-serialisable
  `summary()` for tooling.
- **CLI**: `azurik-cli save inspect <path>` with `--json` flag.
  Handles both directory and single-file inspection.  Lazy-imports
  the module so normal patch workflows don't pay its cost.
- **28 new tests** in `test_save_format.py` pinning parser
  correctness, round-trips, dispatch rules, JSON summaries,
  partial-export handling, and CLI smoke tests.
- **New docs** [`docs/SAVE_FORMAT.md`](docs/SAVE_FORMAT.md):
  directory layout, qcow2 / xemu extraction workflow, byte-level
  details for the decoded portions, limitations, and a priority
  list of decoder targets for future work.

Source-level evidence for the save format: call sites
`FUN_0005b250` (fopen wrapper), `FUN_0005c4b0` (directory scan),
`FUN_0005c95c` (`fread(buf, 0x14, 1, fp)` — pinned the header
size), and the leaked source path `C:\Elemental\src\game\save.cpp`
at VA 0x19E5C8.

Full impact:
- 254 tests passing (up from 213; +13 gravity wrapper + 28 save
  format).
- 4 new documentation files in docs/ (D1_EXTEND.md already in;
  SAVE_FORMAT.md, plus the existing D2_NXDK.md and gravity notes).
- 3 new shim-authoring-surface files (azurik_gravity.h,
  azurik_kernel_extend.h, gravity_integrate.c).

### D1-extend — runtime xboxkrnl export resolver + comprehensive coverage pass

Shims can now call **any** xboxkrnl export, not just the 151 Azurik's
vanilla XBE statically imports.  Full design note:
[`docs/D1_EXTEND.md`](docs/D1_EXTEND.md).  D2 (full NXDK integration)
is documented separately in [`docs/D2_NXDK.md`](docs/D2_NXDK.md) and
intentionally deferred.

- **Runtime resolver shim** (`shims/shared/xboxkrnl_resolver.c`).
  Single self-contained function `xboxkrnl_resolve_by_ordinal(n)`
  that walks xboxkrnl.exe's PE export table from the fixed retail
  base `0x80010000`.  ~50 bytes of i386 code; zero undefined
  externs; auto-placed by `ShimLayoutSession` the first time any
  extended import is referenced.

- **Per-import resolving stubs** (33 bytes each).  On first call:
  `CALL xboxkrnl_resolve_by_ordinal(ordinal); cache inline; JMP EAX`.
  On subsequent calls: 3 instructions (load cache + test + indirect
  jump).  Dispatch lives in `shim_session.stub_for_kernel_symbol`
  which auto-routes between D1 static-thunk (fast path, 6 bytes)
  and D1-extend resolver (slow-first-call path, 33 bytes) based on
  whether the ordinal is in Azurik's 151.

- **Expanded ordinal catalogue** (`xboxkrnl_ordinals.py`).  Split
  into two tables: `AZURIK_KERNEL_ORDINALS` (151, unchanged) +
  new `EXTENDED_KERNEL_ORDINALS` (~100 curated entries covering
  Debug, Executive, I/O, Kernel services, Memory Manager, Object
  Manager, Process, Runtime, Crypto, and Xbox-specific APIs).
  `ALL_KERNEL_ORDINALS` gives the union; `NAME_TO_ORDINAL`
  prefers Azurik's static slot when a name appears in both
  (so D1's fast path always wins over D1-extend when possible).
  New public helper: `is_azurik_imported(ordinal)`.

- **New header** `shims/include/azurik_kernel_extend.h`.  Declares
  ~60 of the most useful extended imports with correct `NTAPI` /
  `FASTCALL` annotations: DbgBreakPoint, DbgPrompt, the Ex*/Ke*
  / Io* / Mm* / Ob* / Ps* / Rtl* surface areas not in Azurik's
  static imports, plus `snprintf` / `sprintf` / `XboxKrnlVersion`.
  Shim authors just `#include` and call.

- **New VA anchors** in `shims/include/azurik.h` for commonly-
  read globals: `AZURIK_FLOAT_ZERO_VA` / `AZURIK_FLOAT_HALF_VA` /
  `AZURIK_FLOAT_ONE_VA` (shared numerical constants at
  `0x001A2508` / `0x001A9C84` / `0x001A9C88`);
  `AZURIK_ENTITY_REGISTRY_BEGIN_VA` / `_END_VA` / `_CAP_VA`
  (runtime entity-pointer vector at `0x0038C1E4..EC`);
  `AZURIK_MOVIE_CONTEXT_PTR_VA` / `AZURIK_MOVIE_IDLE_FLAG_VA`
  (boot movie state at `0x001BCDC8` / `0x001BCDB4`);
  `AZURIK_WALKING_STATE_FLAG_VA` (`0x0037ADEC`).  Real on-disk
  bytes + BSS placement pinned via new regression tests.

- **New vanilla function** `boot_state_tick` (`FUN_0005F620`)
  registered as `__stdcall(float)` with verified `RET 4` exits
  and AL-return convention.  Declared in `azurik_vanilla.h`.
  Lets shims wrap the boot-state machine (extension path for
  future `qol_skip_prophecy`-style patches).

- **Tests**: 213 passing (+ 20 new in `test_d1_extend.py`).
  Pinned: ordinal-catalogue invariants, static-vs-extended
  dispatch, stub byte-shape + opcode offsets + rel32 overflow,
  resolver `.c` compiles + has zero undefined externs, end-to-end
  session dispatch against the real vanilla XBE, and a drift
  guard between `azurik_kernel_extend.h` and
  `EXTENDED_KERNEL_ORDINALS`.

- **New docs**: [`docs/D1_EXTEND.md`](docs/D1_EXTEND.md) (full
  design + authoring workflow for extended imports),
  [`docs/D2_NXDK.md`](docs/D2_NXDK.md) (deferred — NXDK
  integration plan + deferral rationale).

### Small headers fill-in pass — ControllerState, drop tables, entity_lookup

- **`ControllerState` struct** added to `shims/include/azurik.h`.
  84-byte layout (`AZURIK_CONTROLLER_STRIDE = 0x54`), per-player
  at `AZURIK_CONTROLLER_STATE_VA + player_idx * 0x54`.  Covers
  analog sticks, D-pad, 8 analog buttons, triggers, stick clicks,
  start / back, plus the 12-byte `edge_state[]` latch array.
  Pinned from Ghidra's `FUN_000a2880` (XInput poll) — every write
  maps 1:1 to a named field.  Active-player index anchor
  `AZURIK_ACTIVE_PLAYER_INDEX_VA = 0x001A7AE4`.  Compile-time
  `_Static_assert`s pin `sizeof(ControllerState) == 0x54` and every
  critical offset.

- **CritterData drop-table + range fields** pulled into
  `shims/include/azurik.h`: `range`, `range_up`, `range_down`,
  `attack_range`, `drop_1..5`, `drop_count_1..5`, `drop_chance_1..5`
  at offsets `0xB8..0x10C`.  Offsets verified against
  `FUN_00049480`'s `"dropN"` / `"dropChanceN"` / `"rangeN"` writes.

- **`entity_lookup` (`FUN_0004B510`)** registered in
  `vanilla_symbols.py` + declared in `azurik_vanilla.h`.  Verified
  `__fastcall` (`@entity_lookup@8`) by reading two real callers —
  both emit `MOV ECX,<name>; MOV EDX,<fallback>; CALL` with no
  `ADD ESP, N` cleanup.  Lets shims resolve named entities at
  runtime without going through a config-table wrapper.

- **Skipped** `FUN_00085700` (gravity integration) — Ghidra decomps
  it as `__fastcall` but the body reads `in_EAX` as an implicit
  output-pointer (MSVC RVO pattern), so clean clang exposure
  requires a naked-asm wrapper.  Reasoning documented in
  `docs/LEARNINGS.md::Vanilla-function exposure` for future
  reference.

- **Tests**: 193 passing + 57 subtests (up from 192 + 32).  New
  drift-guards in `tests/test_shim_authoring.py` pin 10 CritterData
  drop-table offsets and 15 ControllerState offsets as compile-
  observable facts.

- **Docs**: LEARNINGS.md gains a "ControllerState struct" section
  with the full byte-level map + a "Vanilla-function exposure" note
  covering the fastcall-vs-thiscall-vs-RVO ABI edge cases.  SHIMS.md
  roadmap updated: mid-term #3 and #4 marked done; stale "Long-term"
  section cleaned up (D1 and E were duplicated there despite being
  done; replaced with D1-extend + D2 + B2 future-work entries).

### Folder-per-feature reorganisation + unified `apply_pack` dispatcher

- **Every feature is now one self-contained folder** under
  `azurik_mod/patches/<name>/` — Python declaration in `__init__.py`,
  optional shim C source alongside as `shim.c`, optional `README.md`
  for per-feature notes.  Deleting a feature = removing one folder;
  no orphaned references scattered across `shims/src/` and
  `azurik_mod/patches/`.  The six pre-existing packs migrated:
  `fps_unlock/`, `player_physics/`, `qol_gem_popups/`,
  `qol_other_popups/`, `qol_pickup_anims/`, `qol_skip_logo/`.
- **`shims/` is now a shared library, not a feature bucket.**
  `shims/src/` → `shims/fixtures/` (only test-only shim sources
  remain — `_reloc_test.c`, `_vanilla_call_test.c`, `_shared_lib_test.c`,
  `_shared_consumer_{a,b}.c`, `_kernel_call_test.c`).  Feature shims
  (currently `skip_logo.c`) moved into their feature folders.
- **`Feature` descriptor + `ShimSource` helper** (new
  `azurik_mod/patching/feature.py` + extended `registry.py`).  Three
  new optional fields on `PatchPack` / `Feature`:
  - `shim: ShimSource` — no hardcoded `Path("shims/build/...")`.
  - `legacy_sites: tuple[PatchSpec, ...]` — byte-patch fallback.
  - `custom_apply: Callable` — multi-step apply escape hatch.
- **Unified `apply_pack(pack, xbe_data, params)` dispatcher**
  (`azurik_mod/patching/apply.py`).  Dispatches by site type;
  `params` values feed parametric sliders; `custom_apply` short-
  circuits the generic loop; `AZURIK_NO_SHIMS=1` swaps every
  `TrampolinePatch` for the pack's `legacy_sites`.  One env var now
  replaces the per-pack sprawl (`AZURIK_SKIP_LOGO_LEGACY=1` still
  works, kept as an alias).
- **`cmd_randomize_full` walks the registry.**  Replaced the
  handwritten `if want_gem_popups: apply_gem_popups_patch(...)` /
  `if want_skip_logo: ...` / … pipeline with a single loop that
  calls `apply_pack` on every enabled feature.  Pack-specific
  apply-function names stay exported for backward compat; the
  randomizer uses the dispatcher.
- **GUI backend simplified.**  `gui/backend.run_randomizer` now
  accepts unified `packs: dict[str, bool]` + `pack_params` dicts
  instead of per-pack boolean kwargs.  Legacy kwargs still accepted
  and folded into `packs` before dispatch.  `gui/pages/build.py`
  passes the dicts directly.
- **`shims/toolchain/new_shim.sh`** scaffolds a full feature folder
  (`__init__.py` + `shim.c`) instead of just writing a C file.
- **Auto-compile heuristic** updated for the new layout:
  `shims/build/<name>.o` looks for the source at
  `azurik_mod/patches/<name>/shim.c` first, then
  `shims/fixtures/<name>.c` for test fixtures.  `.o` filenames are
  now keyed on the pack name (not the source stem) so two features
  whose source both happens to be called `shim.c` can't collide in
  the shared build cache.
- **Tests (+9)** — `tests/test_apply_pack.py` pins every dispatch
  route: pure `PatchSpec`, parametric (including default fallback
  and virtual-site skip), `TrampolinePatch`, `custom_apply`,
  `AZURIK_NO_SHIMS=1` fallback, type validation.  Existing tests
  updated to the new paths; full suite at 191 passing.
- **Docs refreshed** — `SHIMS.md` directory map,
  `SHIM_AUTHORING.md` scaffold step + authoring flow,
  `AGENT_GUIDE.md` repo-shape + "folder-per-feature invariant"
  landmine, `PATCHES.md` pack catalog table, `LEARNINGS.md`
  "Historical: pre-reorganisation layout" lookup table,
  `shims/README.md` rewritten as library overview,
  `docs/ONBOARDING.md` written for newcomers.

### C-shim modding platform (polish — full header coverage + auto-compile + docs)

- **`shims/include/azurik_kernel.h` now covers ALL 151 xboxkrnl imports** Azurik's vanilla
  XBE references.  Previously only ~10 hand-picked functions were declared; the expanded
  header groups every import by subsystem (Av / Dbg / Ex / Fsc / Hal / Io / Ke / Kf /
  Mm / Nt / Ob / Ps / Rtl / Xbox / Xc) and ships with a full set of kernel typedefs
  (`NTSTATUS`, `HANDLE`, `PVOID`, `LARGE_INTEGER*`, the object-type aliases, the
  `PK*_ROUTINE` callback types).  Drift guard updated to skip C keywords and
  function-pointer typedef scaffolding.
- **`scripts/gen_kernel_hdr.py`** regenerates the header from OpenXDK's `xboxkrnl.h`
  (at `xbox-includes/include/xboxkrnl.h`) zipped against
  `azurik_mod/patching/xboxkrnl_ordinals.py`.  131 of 151 signatures come from OpenXDK
  directly; the remaining 20 (data exports, fastcall exceptions, varargs) are hand-
  written at the top of the generator and documented there.
- **Auto-compile** — `apply_trampoline_patch` now invokes `shims/toolchain/compile.sh`
  on demand when a shim's `.o` is missing but its `.c` source exists.  Heuristic:
  `shims/build/<name>.o` ↔ `shims/src/<name>.c`.  Opt out with
  `AZURIK_SHIM_NO_AUTOCOMPILE=1` (used in CI to pin pre-built artifacts).
- **Documentation pass** (three new files in `docs/`):
  * `docs/SHIM_AUTHORING.md` — end-to-end authoring guide (decision tree, 8-step
    workflow, common pitfalls, debug playbook).
  * `docs/AGENT_GUIDE.md` — AI-agent-specific guide with standard workflows, observed
    failure modes, and "before you make any change" checklist.
  * `docs/LEARNINGS.md` — accumulated reverse-engineering findings (the 151-import
    ceiling, `config.xbr` dead-data pattern, boot-state machine contract, the
    UnboundLocalError regression, etc.).  Cited from Ghidra function names so future
    agents can re-verify.
- **`azurik.h`** picked up a small "Time / frame pacing" section pointing at the 1/30 s
  constant and cross-referencing `azurik_kernel.h`.
- **Cross-refs** — every header now points at its companions; `docs/SHIMS.md` status
  table updated to mark the coverage work done.

### C-shim modding platform (Phase 2 D1 — xboxkrnl kernel imports)

- **Shims can now call xboxkrnl kernel functions directly.**  Any of
  the 151 kernel ordinals Azurik's vanilla XBE already imports
  (`DbgPrint`, `KeQueryPerformanceCounter`, `NtReadFile`,
  `HalReturnToFirmware`, ...) can be declared as a C extern in
  `shims/include/azurik_kernel.h` and called from a shim exactly
  like a local function.  The shim layout session parses the XBE's
  kernel thunk table, generates a 6-byte `FF 25 <thunk_va>` stub
  per referenced import, and resolves the shim's `call _Foo@N`
  REL32 to the stub's VA.  No XBE import-table surgery; no runtime
  loader; no name-resolution code injected into the game.
- **`azurik_mod/patching/xboxkrnl_ordinals.py`** — full ordinal →
  name table for the 151 imports Azurik ships with, cross-checked
  against Ghidra's import pane and the parsed thunk table on disk.
  Bijective (no duplicates); sorted by ordinal for binary-search
  audits.
- **`azurik_mod/patching/kernel_imports.py`** — XBE thunk-table
  decryption (retail / debug / chihiro XOR keys tried in turn),
  parser that walks the table to its null terminator and yields
  `(thunk_va, ordinal, name)` entries, `demangle_stdcall` /
  `demangle_cdecl` helpers, and a `stub_bytes_for(va)` generator.
- **`shims/include/azurik_kernel.h`** — extern declarations for the
  imports we've so far needed in shims: debug (`DbgPrint`), timing
  (`KeQueryPerformanceCounter` / `Frequency`, `KeStallExecution-
  Processor`, `KeTickCount`), synchronisation (`KeSetEvent`,
  `KeWaitForSingleObject`), and title management
  (`HalReturnToFirmware`).  The header carries an ABI checklist
  shim authors must follow and a "what cannot be called" note for
  kernel functions Azurik doesn't already import.
- **Tests (+33 tests)** — `tests/test_kernel_imports.py` covers:
  ordinal-table invariants (count, uniqueness, sorting), demangle
  helpers, stub-byte shape, thunk-table parse against the vanilla
  XBE (VA `0x18F3A0`; 151 entries; every parsed ordinal resolves
  to a known name), `ShimLayoutSession` stub caching (allocator
  called exactly once per kernel function, dedup across shims), an
  end-to-end compile that has a shim call `DbgPrint` and asserts
  the REL32 lands on a stub whose indirect target is the correct
  thunk slot, and a header ↔ ordinal-map drift guard.
- **Not yet supported**: adding a NEW kernel import (one Azurik
  doesn't already reference).  The thunk table has zero trailing
  slack in Azurik's XBE, so extending it would require a move +
  re-link of every existing `CALL [thunk_va]` in the game.
  Tracked as `D1-extend` in `docs/SHIMS.md`.

### C-shim modding platform (Phase 2 E — shared-library shim layout)

- **`ShimLayoutSession.apply_shared_library(path)`** places a shim
  `.o` once per session and exposes its exported symbols to every
  subsequent `apply_trampoline_patch` call.  Two trampolines that
  both reference `_shared_helper@4` now resolve to a SINGLE VA —
  no duplicated machine code, no linker required.
- **`azurik_mod/patching/shim_session.py`** — new module that
  unifies D1 (kernel stubs) and E (shared libraries) under a single
  session object attached to the XBE bytearray.  The extern
  resolver threaded into `layout_coff` consults, in order: vanilla-
  symbol registry → shared-library exports → kernel-import stubs
  (auto-allocated) → session's fallback.  Stubs and library
  placements are cached for idempotence.
- **`azurik_mod/patching/coff.layout_coff`** gains an
  `extern_resolver: Callable[[str], int | None]` parameter.
  Unresolved externals that aren't in `vanilla_symbols` are passed
  to the resolver; `None` means "not mine, keep going".  The old
  `vanilla_symbols` dict-only API still works — `extern_resolver`
  is additive and defaults to `None`.  `layout_coff` also accepts
  `entry_symbol=None` for library-style placements (no single
  entry point to resolve).
- **`apply_trampoline_patch`** now instantiates / reuses a
  `ShimLayoutSession` attached to `xbe_data` automatically — pack
  apply functions can pre-place shared libraries via
  `get_or_create_session` without plumbing a new argument through.
- **Fixtures** — three new files under `shims/src/`:
  `_shared_lib_test.c` exports two stdcall helpers;
  `_shared_consumer_a.c` and `_shared_consumer_b.c` each call the
  first helper.  Used by the test below.
- **Tests (+6 tests)** — `tests/test_shared_library.py` covers:
  a shared library places its two exports with unique VAs, re-
  applying the same path is idempotent, export VAs lie inside the
  placed region, the "no externally-visible" error fires on
  static-only / DCE'd sources, and — the headline assertion — two
  independent consumer shims' REL32s resolve to the same helper VA.

### C-shim modding platform (Tier B — authoring ergonomics)

- **`shims/include/azurik.h` grew real struct definitions.**  Shim
  authors now get named fields for two key engine structs:
  * `CritterData` — what `FUN_00049480` populates for every critter
    (walk/run speed, collision radius, flocking fields, hitpoints,
    drown/corpse timers, ...).  Field offsets documented with their
    Ghidra piVar9 indices.
  * `PlayerInputState` — the per-frame player-movement struct used
    by `FUN_00084f90` / `FUN_00084940` / `FUN_00085f50`.  Key
    fields (magnitude at +0x124, direction vector at +0x128, flags
    at +0x20) are now named with Ghidra-verified offsets.
  Flag constants (`PLAYER_FLAG_RUNNING = 0x40`,
  `PLAYER_FLAG_FALLING = 0x01`) and fixed-width integer aliases
  (`u8`, `u16`, `u32`, `i8`..`i32`, `f32`, `f64`) live alongside.
  Compile-time `_Static_assert`s pin the minimum struct size so
  drift fails at compile time rather than producing silently-wrong
  machine code.
- **`shims/toolchain/new_shim.sh NAME`** — new scaffolding script.
  Generates a pre-filled `shims/src/<name>.c` with the correct
  `__stdcall` annotation, the two standard includes, and a TODO
  comment pointing at the function body.  Rejects names that
  aren't valid lowercase C identifiers; refuses to overwrite
  existing shims.  Next-step checklist printed on success.
- **Tests (+6 test classes, +20 subtests)** —
  `tests/test_shim_authoring.py` pins both pieces: a probe shim
  verifies every named field in the header compiles to the
  Ghidra-documented `[reg + 0xNN]` offset; the scaffold script is
  exercised with valid / invalid / duplicate names; the generated
  stub is compiled end-to-end and the exported
  `_c_<name>@0` stdcall symbol is sanity-checked.
- **Docs** — `docs/SHIMS.md` "Authoring a new shim" walkthrough
  now starts at `new_shim.sh` and references the named struct
  fields in `azurik.h`.  Directory map updated.
- **Deferred**: adding `FUN_000d1420` / `FUN_000d1520` (config-table
  lookups) as exposed vanilla functions.  Both use MSVC `__thiscall`
  (first arg in ECX, rest on stack) — clang supports the attribute,
  but it complicates the ergonomics enough that it belongs in a
  follow-up once we have a concrete shim that needs table queries.

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
