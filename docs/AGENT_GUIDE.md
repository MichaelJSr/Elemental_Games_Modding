# Agent guide — working on this repo as an AI

This file is written directly for AI agents (Claude, Codex, etc.)
picking up work on the Azurik modding platform.  Start here; read
the other docs on demand from the pointers below.

> **If you've never worked in this repo before**, read
> [`ONBOARDING.md`](./ONBOARDING.md) first — it walks through the
> feature-folder layout + `apply_pack` dispatcher with two worked
> examples.

> **Convention in this file:** lines beginning with 🛑 are hard
> rules you must not violate.  Lines beginning with ✅ are mandatory
> checkpoints before you consider a task done.

---

## 1. Repo shape at a glance

```
Elemental_Games_Modding/
├── azurik_mod/                     # Python package — apply/verify engine
│   ├── patches/                    # ONE FOLDER PER FEATURE
│   │   ├── <feature>/              #   self-contained mod
│   │   │   ├── __init__.py         #     Feature(...) declaration
│   │   │   ├── shim.c              #     (optional) C source
│   │   │   └── README.md           #     (optional) notes
│   │   ├── _qol_shared.py          #   shared helper (popup nulling)
│   │   ├── _player_character.py    #   non-pack helper
│   │   └── qol.py                  #   back-compat re-exports
│   ├── patching/                   # Low-level infra
│   │   ├── feature.py              #   ShimSource helper
│   │   ├── registry.py             #   Feature dataclass + registry
│   │   ├── apply.py                #   apply_pack + primitives
│   │   ├── spec.py                 #   PatchSpec / ParametricPatch / TrampolinePatch
│   │   ├── coff.py / xbe.py        #   COFF + XBE surgery
│   │   ├── shim_session.py         #   D1 + E session
│   │   ├── vanilla_symbols.py      #   A3 registry
│   │   └── kernel_imports.py       #   D1 runtime parser
│   ├── randomizer/                 # CLI + pipelines
│   └── config/                     # .xbr keyed-table parsing
├── shims/                          # shared LIBRARY (not feature code)
│   ├── include/                    #   headers every shim pulls in
│   │   ├── azurik.h                #   runtime structs + VA anchors
│   │   ├── azurik_vanilla.h        #   vanilla function externs
│   │   └── azurik_kernel.h         #   xboxkrnl import externs (all 151)
│   ├── fixtures/                   #   test-only shim sources (_*.c)
│   ├── build/                      #   compiled .o cache (gitignored)
│   └── toolchain/
│       ├── compile.sh              #   clang -target i386-pc-win32 wrapper
│       └── new_shim.sh             #   scaffolds a whole feature folder
├── tests/                          # pytest suite — keep green (run ``pytest tests/`` for count)
├── docs/                           # Documentation (this file is one)
├── gui/                            # Tkinter GUI — separate subsystem
└── scripts/                        # Generators + one-offs
```

Three rules for where to put things:
- If it's about parsing / modifying the XBE: `azurik_mod/patching/`.
- If it's a feature users toggle: `azurik_mod/patches/<feature>/`
  (always a folder, even for byte-only patches — one source of truth).
- If it's shared infrastructure (header, toolchain, test fixture):
  `shims/` subdirectories.

---

## 2. Before you make any change

1. 🛑 **Run the existing test suite first.**  Confirm it's green.
   If it isn't, that's your first job — don't pile new work on a
   broken baseline.
   ```bash
   cd Elemental_Games_Modding && python -m pytest tests/ -q
   ```
2. 🛑 **Read `docs/SHIMS.md` for the platform state**, and this
   guide's Section 5 for known landmines.
3. ✅ **Write a plan** (use the TodoWrite tool).  Even small tasks
   benefit from a 3-line plan because it forces you to think about
   testing and docs up front, which are what usually get skipped.

---

## 3. Task-type decision tree

### Adding a new parametric / byte patch (no new logic)

1. Identify the exact VA and vanilla bytes in Ghidra.
2. Create a feature folder: `azurik_mod/patches/<name>/__init__.py`.
   Use `register_feature(Feature(...))` with a `sites=[...]` list of
   `PatchSpec` / `ParametricPatch` entries.
3. No `shim=` / `custom_apply=` needed for a pure byte / parametric
   feature — `apply_pack` walks the sites generically.
4. Add a test pinning the VA, vanilla bytes, and applied bytes.
5. `pytest tests/`, boot in xemu if the change is visible.

### Adding a new shim (has logic)

1. Follow `docs/SHIM_AUTHORING.md` end-to-end.  Eight steps; don't
   skip any.
2. ✅ Drift guards catch header/registry mismatches — run the full
   suite (not just the one pack's tests).

### Reverse-engineering a new system (e.g. camera, AI)

1. Open the relevant xbr file in Ghidra (characters.xbr,
   config.xbr, …).
2. Grep `azurik_mod/config/keyed_tables.py` and
   `scripts/analysis/` — there's probably existing work to build on.
3. Document findings in `docs/LEARNINGS.md` as you go.  Your future
   self (or another agent) will thank you.

### Refactoring / tooling improvements

1. Respect existing patterns.  The repo is small enough that
   consistency matters more than individual brilliance.
2. Keep public APIs backward-compatible when feasible.
3. Never change `azurik_mod/patching/coff.py`'s `CoffFile`
   dataclass without updating `layout_coff` + every test that
   synthesises a COFF manually (grep for `CoffFile(`).

### Writing or expanding documentation

1. Decide the audience (human author vs. agent vs. end user) and
   pick the right doc file.  `SHIM_AUTHORING.md` is human-focused;
   `AGENT_GUIDE.md` (this file) is agent-focused;
   `LEARNINGS.md` is a knowledge-base for both.
2. Cross-reference between docs at the point the reader will want
   the pointer — avoid "see §2.3 of X".

---

## 4. Things that WILL trip you up

### 4a. Ghidra VAs vs. file offsets

Ghidra shows **virtual addresses** (e.g. `0x00085F62`).  The XBE
file on disk is laid out differently.  To convert:

```python
from azurik_mod.patching.xbe import va_to_file
offset = va_to_file(0x85F62)   # → file offset in default.xbe
```

🛑 Never hardcode a file offset.  Always declare VAs and convert.

### 4b. XOR-obfuscated XBE header fields

Several 32-bit fields in the XBE header are XOR-encrypted against
a hardcoded magic (retail/debug/chihiro).  See
`azurik_mod/patching/kernel_imports._resolve_kernel_thunk_va` for
the pattern — try all three keys, pick the one that yields an in-
image VA.

### 4c. Calling-convention mangling on i386 PE-COFF

Clang mangles symbols as:
- `__cdecl`    → `_Name`
- `__stdcall`  → `_Name@N`  (N = total arg bytes)
- `__fastcall` → `@Name@N`

🛑 For stdcall, `N` is the sum of **argument sizes on the 4-byte-
aligned stack**.  `BOOLEAN` / `UCHAR` / `SHORT` all count as 4.
`LARGE_INTEGER*` is a pointer = 4.

Getting the count wrong means the mangled name won't match the
registry entry and the layout pass refuses the shim.

### 4d. XBE section VA gap before BINK

Azurik's `.text` ends at VA `0x1001D0`; the next section (BINK)
starts at `0x1001E0`.  That's a **16-byte gap** we use for small
shims.  Longer shims spill into an appended `SHIMS` section via
`append_xbe_section`.

The 16-byte figure is specific to this build of Azurik.  If anyone
ever rebuilds the XBE, the gap may shift.  Don't hardcode `16` —
call `find_text_padding(xbe)` which discovers the gap dynamically.

### 4e. Kernel imports — two surfaces, automatic dispatch

D1 exposes the 151 kernel functions Azurik's vanilla XBE already
imports via static `FF 25 <thunk_va>` stubs (fastest path).  For
ANY other xboxkrnl export, **D1-extend** (shipped; see
`docs/D1_EXTEND.md`) emits a runtime-resolving stub that walks
xboxkrnl.exe's PE export table at the fixed retail kernel base
`0x80010000` on first call and caches the result inline.

Authoring:
- Static 151: include `shims/include/azurik_kernel.h`.
- Extended (anything else): include
  `shims/include/azurik_kernel_extend.h`.

`shim_session.stub_for_kernel_symbol` dispatches between the two
paths automatically based on the ordinal catalogue in
`azurik_mod/patching/xboxkrnl_ordinals.py` — shim authors don't
pick.  The fallback-to-vanilla-wrapper workaround the old docs
recommended is no longer necessary for kernel-level calls.

### 4f. `config.xbr` dead data

Many keyed-table cells in `config.xbr` exist but the engine never
reads them.  The most-burned example: `critters_critter_data`'s
`walkSpeed` and `runSpeed` rows.  These look promising but writing
them achieves nothing — the engine's effective `base_speed` comes
from a different source entirely.

🛑 Before declaring a config-driven patch, **verify in Ghidra that
the cell's value actually flows into gameplay logic**.  Grep for
the field's offset inside the populated critter struct in
`FUN_00049480`, then trace backward to a `FUN_000D1420("<key>")`
lookup.  If the lookup isn't there, the data is dead and you
need a code-level patch instead.

### 4g. `azurik.h` struct static asserts

The static asserts at the bottom of `shims/include/azurik.h` fire
at COMPILE time, not at test time.  If a shim fails to build with
an assertion message mentioning a struct offset, you broke the
struct layout — undo whatever you did, or add a compensating
change in the header.

### 4h. Folder-per-feature invariant

Every feature is ONE folder under `azurik_mod/patches/<name>/`.
- The folder name **must** match the pack name (what `Feature.name`
  says, what the GUI checkbox toggles, what `--<flag>` uses).
- `shim.c` (if any) compiles to `shims/build/<name>.o` — keyed on
  the pack name, not the source stem.  Don't hardcode
  `Path("shims/build/something.o")` — use the `ShimSource` helper.
- If you see `shims/src/` in a new commit, you're editing the
  pre-reorganisation layout — `shims/src/` no longer exists (test
  fixtures moved to `shims/fixtures/`, feature shims to the feature
  folder).

---

## 5. Standard workflows

### Workflow: "Reverse-engineer + patch a new game behaviour"

```
1. Identify the symptom the user wants to change in xemu.
2. Open default.xbe in Ghidra (port 8193 by convention).
3. Grep + xref your way to the function(s) involved.
4. Decide: is this a constant (ParametricPatch), a byte-level
   tweak (PatchSpec), or logic (shim)?
5. Implement in azurik_mod/patches/<feature>.py.
6. Write tests pinning VAs + behaviour.
7. Update docs/PATCHES.md with the new entry.
8. Run `pytest tests/` and boot in xemu.
```

### Workflow: "Add a field to azurik.h"

```
1. Open Ghidra, find the struct in question (usually via the
   populating function — FUN_00049480 for CritterData,
   FUN_00084f90 for PlayerInputState).
2. Confirm the offset and type.  If named via a FUN_000D1420
   lookup, copy the config-key string for the comment.
3. Replace _reservedNN with the named field in azurik.h.
4. Add a _Static_assert at the bottom of the file.
5. Run `pytest tests/test_shim_authoring.py` — the header-drift
   test compiles the header with the new field.
```

### Workflow: "Expose a new vanilla function"

```
1. Ghidra: VA + calling convention.
   - Callers with `ADD ESP, N` after the call → cdecl.
   - Callers with no cleanup → stdcall (callee pops via RET N).
   - Callers with MOV ECX / MOV EDX pre-call → fastcall.
2. Count arg bytes:
   - 4 bytes for every pointer, int, float (floats take a slot).
   - 4 bytes (padded) for BOOLEAN/UCHAR/SHORT.
   - 4 bytes for a pointer to LARGE_INTEGER (it's by-pointer).
3. Register in azurik_mod/patching/vanilla_symbols.py.
4. Declare in shims/include/azurik_vanilla.h (with the matching
   VA in the doc comment — the drift test looks for it).
5. Run `pytest tests/test_vanilla_thunks.py`.
```

### Workflow: "Figure out why a shim crashes the game"

```
1. Did the unit test pass?  If no → fix that first.
2. Open the POST-APPLY XBE in Ghidra.  Navigate to the trampoline
   VA.  Confirm the CALL/JMP target is your shim's entry.
3. Disassemble the shim's landed bytes in Ghidra.  Do the
   relocations look right?
4. Insert a DbgPrint as the FIRST statement in your shim.  Rebuild,
   re-apply, boot in xemu -debug.  If you see the DbgPrint, the
   shim is running; if not, the trampoline isn't firing.
5. Common culprits after "the shim runs":
   - Calling convention mismatch (stack over/under-popped).
   - Undefined external resolved to garbage (session bug).
   - Stack overflow from a too-large local (check function's
     SUB ESP, N prologue).
```

### Workflow: "Auto-compile just broke in CI"

The auto-compile feature runs `compile.sh` when a `.o` is missing.
If CI doesn't have clang:

- Set `AZURIK_SHIM_NO_AUTOCOMPILE=1` in the CI env so missing
  `.o`s hard-fail with a clear message.
- Alternatively, ship pre-built `.o` files (commit them) so
  auto-compile never triggers.

---

## 6. Running subagents / parallel work

When you (an agent) launch subagents:

1. 🛑 Never trigger more than one `apply_trampoline_patch` on the
   same XBE bytearray from parallel processes.  The bytearray has
   implicit session state that isn't thread-safe.
2. For exploring unfamiliar code, use readonly subagents with the
   `explore` subagent_type.
3. For long-running mechanical tasks (regenerate all .o files,
   run a lengthy test suite), prefer the `shell` subagent_type
   with `run_in_background: true`.

---

## 7. "I want to..." quick links

| Goal                                   | Start reading                                              |
|----------------------------------------|------------------------------------------------------------|
| Add a slider-driven float patch        | `azurik_mod/patches/player_physics/__init__.py` (gravity example) |
| Add a byte-level QoL patch             | `azurik_mod/patches/qol_gem_popups/__init__.py` (or sibling `qol_*` folder) |
| Write a new C shim                     | `docs/SHIM_AUTHORING.md`                                   |
| Expose a new vanilla Azurik function   | `azurik_mod/patching/vanilla_symbols.py`                   |
| Call a kernel function from a shim     | `shims/include/azurik_kernel.h` (all 151 declared)         |
| Expand a header struct                 | `shims/include/azurik.h` + static asserts                  |
| Add a test for an existing pack        | `tests/test_qol_skip_logo.py` (copy the pattern)           |
| Find learnings from previous debugging | `docs/LEARNINGS.md`                                        |
| See what's done / planned              | `docs/SHIMS.md` → Platform status / roadmap                |

---

## 8. Commit hygiene

🛑 Do not commit unless the user explicitly asks.  Even if the
user says "get this done", interpret it as "do the work" not "push
to main".

When committing IS requested:
- ✅ `pytest tests/` is green.
- ✅ No linter errors on files you touched (`ReadLints`).
- ✅ CHANGELOG updated under "Unreleased" with a terse but specific
  description.
- ✅ Docs updated for anything user-visible.
- Use a conventional commit-style message.  See `git log
  --oneline -n 20` for the repo's style.

---

## 9. Common agent failure modes (observed, documented)

1. **Assuming GHidra's display address === thunk-table order.**
   Ghidra displays imports as `EXTERNAL:NNN` where NNN is a
   sequential *display index*, NOT the kernel ordinal.  Always
   parse the thunk table directly.
2. **Writing speculative struct fields.**  If you can't name it
   via a `FUN_000D1420("<key>")` lookup, leave it `_reservedNN`.
   Do NOT guess — a wrong name is actively worse than no name.
3. **Forgetting the `@N` suffix in stdcall mangling.**  Count
   carefully.  Run the drift test early.
4. **Creating a `PatchSpec` against bytes that aren't the
   instruction boundary.**  Always view the site in Ghidra's
   disassembly, not the hex editor — the instruction boundary
   matters.
5. **Not testing with a real XBE.**  Synthetic COFFs verify the
   layout pipeline; they don't prove the shim actually runs.
   Every shim-backed pack needs a `boot in xemu` checklist item.
6. **Over-committing.**  One focused commit per logical change
   beats a 20-file mega-commit.  See `git log` for scale reference.

---

## 10. When stuck — checkpoint list

If something isn't working after 20 minutes of reasonable effort:

- [ ] Did I re-read `docs/SHIM_AUTHORING.md` §4 (common pitfalls)?
- [ ] Did I check the drift guards (vanilla / kernel / header)?
- [ ] Did I verify the VA in Ghidra is what I assume?
- [ ] Did I check `docs/LEARNINGS.md` for prior analysis of this
      system?
- [ ] Did I try `git blame` on the surrounding code?  Recent
      commits often carry context comments.

If still stuck: surface the blocker to the user with a concrete
question.  Don't keep spinning — blocked agents are more valuable
asking good questions than writing bad code.
