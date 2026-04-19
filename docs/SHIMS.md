# C-shim modding platform

> **First time in this repo?**  Read
> [`ONBOARDING.md`](./ONBOARDING.md) — zero to a landed feature,
> two worked examples, a diagram of how the pieces fit.
>
> **Authoring a shim?**  Skip to
> [`SHIM_AUTHORING.md`](./SHIM_AUTHORING.md) — it's the end-to-end
> guide.  This file covers the platform's design and history.
>
> **AI agent picking up a task?**  Start with
> [`AGENT_GUIDE.md`](./AGENT_GUIDE.md).
>
> **Looking for reverse-engineering findings?**  See
> [`LEARNINGS.md`](./LEARNINGS.md).

Most Azurik patches today are byte-level: we pin an address, declare
the original opcodes, and hand-assemble a replacement.  That's fine
for a one-line NOP but it breaks down fast for anything that actually
needs logic (a calculation, a conditional, a table lookup).

The C-shim platform replaces that workflow for non-trivial patches.
Instead of writing bytes, you write a C function, the apply pipeline
compiles it into an i386 PE-COFF ``.o``, injects the machine code
into the XBE, and wires a 5-byte ``CALL`` / ``JMP`` trampoline from
the game's vanilla site into your shim.

Phase 1 ships one shim — ``qol_skip_logo`` — as a proof of concept.
The observable behaviour matches the old 10-NOP patch, but the
underlying mechanism exercises every piece of the new pipeline so
future shims can build on a known-good foundation.

## Toolchain

Shims are compiled by [`shims/toolchain/compile.sh`](../shims/toolchain/compile.sh)
using Apple clang (or any clang that accepts ``-target i386-pc-win32``):

```bash
bash shims/toolchain/compile.sh \
  azurik_mod/patches/qol_skip_logo/shim.c \
  shims/build/qol_skip_logo.o
# → Intel 80386 COFF object, .text-only
```

No linker, no CRT, no libc.  The compile flags are tight on purpose —
Phase 1 shims must be self-contained.

Verified on:

```
Apple clang version 21.0.0 (clang-2100.0.123.102)
Target: arm64-apple-darwin25.4.0 (host)  →  i386-pc-win32 (output)
```

## Authoring a new shim — end-to-end checklist

1. **Scaffold** it from the template:

   ```bash
   bash shims/toolchain/new_shim.sh my_feature
   ```

   This produces a pre-filled feature folder at
   `azurik_mod/patches/my_feature/` containing `__init__.py` + a
   starter `shim.c` with the correct `__stdcall` annotation and
   the standard includes
   (`azurik.h` + `azurik_vanilla.h`), and a TODO comment pointing
   at the function body.  Edit that body with your actual logic.
   The scaffold rejects names that aren't valid lowercase C
   identifiers and refuses to overwrite existing shims.

   Tip for the C body: prefer the named fields in
   [`shims/include/azurik.h`](../shims/include/azurik.h) over raw
   `[reg + 0xNN]` offsets.  The header defines `PlayerInputState`
   and `CritterData` with Ghidra-verified fields and flag constants
   (`PLAYER_FLAG_RUNNING`, `PLAYER_FLAG_FALLING`).  Static asserts
   in the header catch drift if any field ever shifts.

   Phase 1 shims must be relocation-free — no globals, no imports,
   no strings.  Phase 2 shims can reference globals, call each
   other, and invoke any vanilla Azurik function declared in
   [`shims/include/azurik_vanilla.h`](../shims/include/azurik_vanilla.h)
   (see [A3 workflow below](#calling-a-vanilla-function-from-a-shim)).

2. **Compile** it with `shims/toolchain/compile.sh`.  Inspect the
   output with `objdump -d shims/build/<name>.o` to confirm the code
   looks sane.

3. **Declare a TrampolinePatch** in the appropriate pack module
   (e.g. [`azurik_mod/patches/qol.py`](../azurik_mod/patches/qol.py)):

   ```python
   MY_FEATURE_TRAMPOLINE = TrampolinePatch(
       name="my_feature",
       label="Short human description",
       va=0xDEADBEEF,                      # site in vanilla XBE
       replaced_bytes=bytes([...]),        # pinned 10-byte sequence
       shim_object=Path("shims/build/my_feature.o"),
       shim_symbol="_c_my_feature",        # note leading underscore
       mode="call",                        # or "jmp"
   )
   ```

4. **Register the pack** — add the trampoline to a `PatchPack.sites`
   list and tag it with `c-shim` so tooling can surface that:

   ```python
   register_pack(PatchPack(
       name="my_feature",
       description="...",
       sites=[MY_FEATURE_TRAMPOLINE],
       apply=apply_my_feature_patch,
       tags=("qol", "c-shim"),
   ))
   ```

5. **Write the apply function** using
   [`apply_trampoline_patch`](../azurik_mod/patching/apply.py):

   ```python
   def apply_my_feature_patch(xbe_data: bytearray) -> None:
       apply_trampoline_patch(xbe_data, MY_FEATURE_TRAMPOLINE,
                              repo_root=_REPO_ROOT)
   ```

6. **Add tests** mirroring
   [`tests/test_qol_skip_logo.py`](../tests/test_qol_skip_logo.py):

   - Pin the trampoline descriptor fields.
   - Exercise apply + verify end-to-end against the vanilla XBE.
   - Assert `verify_trampoline_patch` returns `"applied"` / `"original"`.

7. **Run the full suite**:

   ```bash
   python3 -m pytest tests/ -q
   ```

8. **Boot in xemu** to confirm the observable behaviour — shims that
   pass the unit tests still need to survive real execution.

## What happens at apply time

For every `TrampolinePatch` the pipeline:

1. Reads the compiled `.o` and parses it with
   [`azurik_mod.patching.coff`](../azurik_mod/patching/coff.py) to
   extract the `.text` bytes and the symbol offset.
2. Finds landing space at the end of the XBE's `.text` section via
   [`find_text_padding`](../azurik_mod/patching/xbe.py) — either
   existing in-section slack OR the adjacent VA gap before the next
   section (Azurik has 16 bytes of the latter and 0 of the former).
3. Copies the shim bytes into that landing pad.
4. If the landing lies past the current `.text.raw_size`, calls
   [`grow_text_section`](../azurik_mod/patching/xbe.py) to extend
   both `virtual_size` and `raw_size` in the XBE's section header so
   the Xbox loader actually maps those bytes into executable memory.
5. Emits a 5-byte `CALL rel32` (or `JMP rel32`) at the trampoline VA,
   NOP-filling any leftover bytes up to `len(replaced_bytes)`.

Idempotence: if the pipeline sees an already-installed trampoline at
the site (same opcode shape, trailing NOPs) it leaves everything
alone instead of stacking a second trampoline.

## Capabilities (Phase 2 A1+A2+A3+D1+E)

- **Unbounded shim size.**  Shims that outgrow the 16-byte `.text`
  VA gap automatically spill into a freshly-appended executable
  section (`SHIMS`).  `append_xbe_section` grows the section-header
  array in place, shifts the post-array header content, and rewrites
  every VA pointer in the image header that now references a shifted
  byte.  Azurik's ~880-byte header-to-.text VA headroom gives us
  comfortable room for the 56-byte header-entry growth.
- **Relocations.**  Shims can reference globals (`DIR32`) and call
  each other (`REL32`) — `layout_coff` walks each landable section's
  relocation table after placement and rewrites the fields to the
  final XBE VAs chosen for each symbol's owning section.  Unsupported
  relocation types raise cleanly.
- **Sidecar sections.**  Shims with `.rdata` / `.data` / `.bss` get
  each section landed independently into the SHIMS region, with
  cross-section relocations resolved correctly.  Metadata sections
  (`.debug$S`, `.llvm_addrsig`, `.drectve`, ...) are filtered out.
- **Vanilla-function calls.**  Shims can invoke any Azurik function
  that's been registered in
  [`azurik_mod/patching/vanilla_symbols.py`](../azurik_mod/patching/vanilla_symbols.py)
  and declared in [`shims/include/azurik_vanilla.h`](../shims/include/azurik_vanilla.h).
  `layout_coff` resolves undefined-external COFF symbols against the
  registry — REL32 / DIR32 relocation math then lands the call
  directly at the vanilla VA.  No runtime thunks needed; the
  pipeline is just a name → VA lookup plus the existing relocation
  math.
- **Kernel imports (D1).**  Shims can call any of the 151 xboxkrnl
  functions Azurik already imports (`DbgPrint`,
  `KeQueryPerformanceCounter`, `NtReadFile`, ...).  The shim layout
  session parses the XBE's kernel thunk table, generates a 6-byte
  `FF 25 <thunk_va>` stub for each referenced import, and resolves
  the shim's `call _Foo@N` REL32 to the stub's VA.  Stubs are
  cached by mangled name so multiple shims referencing the same
  kernel function share one stub.  Declarations live in
  [`shims/include/azurik_kernel.h`](../shims/include/azurik_kernel.h);
  the full ordinal → name table is in
  [`azurik_mod/patching/xboxkrnl_ordinals.py`](../azurik_mod/patching/xboxkrnl_ordinals.py).
- **Shared-library shim layout (E).**  Helper functions that
  multiple trampolines need can live in a standalone `.o` file
  and be placed once per session via
  `ShimLayoutSession.apply_shared_library(path)`.  Subsequent
  `apply_trampoline_patch` calls resolve those symbols against the
  single placement — no private copies, no duplicate code.  See
  [Authoring shared libraries](#authoring-shared-libraries) below.

### Calling a vanilla function from a shim

1. **Confirm the vanilla function in Ghidra.**  Note its VA, its
   calling convention (`__stdcall` → callee pops via `RET N`,
   `__cdecl` → caller cleans via `ADD ESP, N`), and the total stack
   bytes used by its arguments.
2. **Register it in Python.**  Add a `VanillaSymbol` entry in
   [`vanilla_symbols.py`](../azurik_mod/patching/vanilla_symbols.py):

   ```python
   register(VanillaSymbol(
       name="my_vanilla_fn",
       va=0x000ABCDE,
       calling_convention="stdcall",
       arg_bytes=8,
       doc="What it does; return-value meaning; gotchas."))
   ```

3. **Declare it in C.**  Add an `extern` to
   [`azurik_vanilla.h`](../shims/include/azurik_vanilla.h):

   ```c
   /* Vanilla VA: 0x000ABCDE  (mangled: _my_vanilla_fn@8) */
   __attribute__((stdcall))
   int my_vanilla_fn(int a, int b);
   ```

4. **Include the header** in your shim and call the function normally:

   ```c
   #include "azurik_vanilla.h"

   void c_my_shim(void) {
       my_vanilla_fn(1, 2);  /* resolves to 0x000ABCDE at apply time */
   }
   ```

5. **Run the tests.**  `tests/test_vanilla_thunks.py` contains a
   drift guard that refuses to merge if the Python registry and the
   C header disagree.

### Calling a kernel function from a shim

Declarations for xboxkrnl imports live in
[`shims/include/azurik_kernel.h`](../shims/include/azurik_kernel.h);
the full ordinal → name table (every function Azurik's vanilla XBE
imports) is in
[`azurik_mod/patching/xboxkrnl_ordinals.py`](../azurik_mod/patching/xboxkrnl_ordinals.py).

```c
#include "azurik_kernel.h"

void c_my_shim(void) {
    DbgPrint("frame tick from shim");
    XBOX_LARGE_INTEGER now;
    KeQueryPerformanceCounter(&now);
    /* ... use now.LowPart / now.HighPart ... */
}
```

No extra registration is required — the layout session parses the
XBE's kernel thunk table at apply time, allocates one
`JMP [thunk_slot]` stub per referenced function, and resolves the
shim's REL32 relocations to those stubs.  The stub cache is
session-wide, so multiple shims calling the same kernel function
share a single stub.

If the extern you want IS in Azurik's static 151 imports, D1's fast
path emits a 6-byte `FF 25 <thunk_va>` stub.  If it's in the extended
catalogue (any other xboxkrnl export), **D1-extend** emits a 33-byte
resolving stub that walks the kernel export table on first call and
caches the result — see [`D1_EXTEND.md`](./D1_EXTEND.md).  Include
`azurik_kernel_extend.h` for the common extended imports; for ones
not yet declared, add a `KernelOrdinal(...)` entry to
`EXTENDED_KERNEL_ORDINALS` in `xboxkrnl_ordinals.py` and use an
`extern` declaration matching the right calling convention.

The dispatcher in `shim_session.stub_for_kernel_symbol` handles the
static-vs-extended split automatically — shim authors don't need to
know which path a given function takes.

### Authoring shared libraries

If several trampolines share helper code — common math, a lookup
table, a cheat system's toggle state — factor it into a standalone
shim `.o` and have the pack's `apply` function place it once:

```python
# In the pack's apply function
from azurik_mod.patching.shim_session import get_or_create_session

def apply_my_pack(xbe_data: bytearray) -> None:
    sess = get_or_create_session(xbe_data)

    # Place the shared library first; it exposes _shared_helper@4.
    sess.apply_shared_library(
        Path("shims/build/my_shared_lib.o"),
        allocate=lambda name, ph: _carve_shim_landing(xbe_data, ph),
    )

    # Now both trampolines can resolve `_shared_helper@4` via the
    # session's extern resolver — which layout_coff picks up
    # automatically through apply_trampoline_patch.
    apply_trampoline_patch(xbe_data, TRAMPOLINE_A, repo_root=_REPO_ROOT)
    apply_trampoline_patch(xbe_data, TRAMPOLINE_B, repo_root=_REPO_ROOT)
```

Both trampolines' COFF externs (`_shared_helper@4` each) resolve to
the SAME VA — `tests/test_shared_library.py` asserts this directly.
No private copies; no duplicated machine code.

Shared libraries can themselves call vanilla Azurik functions
(the A3 registry) and kernel imports (D1 stubs), but **chained
shared-library → shared-library calls are not yet supported** —
place all shared libraries before the first consumer shim and keep
them non-recursive.

## Limitations (still)

- **No D3D / DSOUND imports**.  Xbox XBEs statically link D3D8 and
  DSound into the game's `.text` (you can see `D3D`, `DSOUND`,
  `XGRPH` as named sections in the XBE).  Those functions are
  vanilla-Azurik code from the shim platform's perspective — expose
  specific ones via `vanilla_symbols.py` + `azurik_vanilla.h` as
  demand arises.  D2 (see [`D2_NXDK.md`](./D2_NXDK.md)) would wire
  them in as a group via the Xbox SDK, but is deferred until a
  shim concretely needs them.
- **Escape hatch preserved**.  The legacy byte-patch form of every
  migrated pack stays behind an env var (`AZURIK_SKIP_LOGO_LEGACY=1`
  etc.) so users on a host without the i386 clang toolchain can
  still ship a patched XBE.

## Troubleshooting

- **`shim object not found`** — run
  `bash shims/toolchain/compile.sh azurik_mod/patches/<name>/shim.c
  shims/build/<name>.o` to build the
  `.o`, or set `AZURIK_SKIP_LOGO_LEGACY=1` to fall back to the old
  byte patch for the one shim Phase 1 migrated.
- **`shim is X B but only 16 B of .text landing space is available`**
  — recompile with `-Os` (default) or `-Oz` to shrink the code, or
  trim the C source.  Larger shims are Phase 2 work.
- **`Unsupported COFF machine 0x...`** — your clang emitted a
  non-i386 object.  Check `-target i386-pc-win32` is still on
  `compile.sh`.
- **`COFF .text has N relocations`** — your shim pulled in a symbol
  that needs relocation.  Refactor to pure arithmetic / branching
  (no globals, no function imports), or defer to Phase 2.

## Directory map

```
azurik_mod/patches/<feature>/       <-- one folder per feature (NEW)
  __init__.py                       Feature(...) declaration + apply logic
  shim.c                            (optional) C source for the trampoline
  README.md                         (optional) per-feature notes

azurik_mod/patches/                 umbrella package — importing it runs
                                    every feature folder's side-effect
                                    registration
  _qol_shared.py                    shared helpers (resource-key nulling)
  _player_character.py              player-char model-name swap helper
  qol.py                            back-compat re-exports

shims/                              shared library (NOT feature code)
  README.md                         what this folder is / isn't
  toolchain/
    compile.sh                      clang wrapper (i386 PE-COFF)
    new_shim.sh                     scaffold a new feature folder      (B3)
  include/
    azurik.h                        named structs + VA anchors         (B1)
    azurik_vanilla.h                vanilla-function externs           (A3)
    azurik_kernel.h                 xboxkrnl import externs (all 151)  (D1)
  fixtures/                         test-only shim sources
    _reloc_test.c                   A2 relocation exercise
    _vanilla_call_test.c            A3 vanilla-call exercise
    _shared_lib_test.c              E shared-library exercise
    _shared_consumer_{a,b}.c        E consumers
    _kernel_call_test.c             D1 kernel-call exercise
  build/                            compiled .o cache (gitignored)

azurik_mod/patching/
  feature.py                        ShimSource helper                (NEW)
  registry.py                       Feature / PatchPack, register_feature,
                                    apply_pack-aware metadata fields
  apply.py                          primitives + apply_pack dispatcher
                                    + auto-compile hook
  coff.py                           minimal PE-COFF reader + layout_coff
  spec.py                           site descriptors (PatchSpec,
                                    ParametricPatch, TrampolinePatch)
  xbe.py                            XBE section surgery
  vanilla_symbols.py                A3 registry of vanilla functions
  kernel_imports.py                 D1 runtime thunk-table parser
  xboxkrnl_ordinals.py              D1 static ordinal table
  shim_session.py                   D1 + E shim-layout session

tests/
  test_apply_pack.py                unified dispatcher invariants  (NEW)
  test_trampoline_patch.py          low-level COFF + xbe + apply + verify
  test_append_xbe_section.py        A1 header-shift round-trip + carve
  test_coff_relocations.py          A2 relocation-aware loader
  test_vanilla_thunks.py            A3 vanilla-symbol resolution
  test_kernel_imports.py            D1 kernel-import end-to-end
  test_shared_library.py            E shared-library layout end-to-end
  test_autocompile.py               on-demand compile heuristic
  test_shim_authoring.py            Tier B — header offsets + scaffold
  test_qol_skip_logo.py             end-to-end through the migrated pack
  test_player_speed.py              player_physics C1 slider end-to-end

docs/
  SHIMS.md                          this file (architecture)
  SHIM_AUTHORING.md                 end-to-end authoring walkthrough
  AGENT_GUIDE.md                    AI-agent-specific workflows
  LEARNINGS.md                      accumulated RE findings
  ONBOARDING.md                     zero-to-landed-feature for newcomers
  PATCHES.md                        per-feature catalog
```


## Platform status / roadmap

Snapshot of what's done, deferred, and up-for-grabs.  Update this
table whenever a tier lands or a new candidate becomes concrete.

| ID  | Tier                                                   | Status            |
|-----|--------------------------------------------------------|-------------------|
| A1  | `append_xbe_section` (unbounded shim size)             | **done**          |
| A2  | Relocation-aware COFF loader                           | **done**          |
| A3  | Vanilla-function calls (registry + layout resolution)  | **done**          |
| B1  | `azurik.h` populated with named structs + drift asserts| **done**          |
| B3  | `new_shim.sh` scaffolding                              | **done**          |
| C1  | Player-speed shim (motivating deliverable)             | **done**          |
| D1  | XKernel imports via thunk-table stubs (`azurik_kernel.h`) | **done** — all 151 ordinals declared |
| E   | Shared-library shim layout (`ShimLayoutSession`)       | **done**          |
| Docs | `SHIM_AUTHORING.md`, `AGENT_GUIDE.md`, `LEARNINGS.md` | **done**          |
| Tool | Auto-compile missing `.o` from `.c` on apply           | **done**          |
| Tool | `scripts/gen_kernel_hdr.py` regenerator                | **done**          |
| Layout | Folder-per-feature reorganisation + `apply_pack` dispatcher | **done**     |
| B2  | Unicorn-backed runtime test harness for shims          | **deferred**      |
| C-jump | Player jump-velocity patch (requested separately)   | **not started**   |
| D1-extend | Runtime resolver for any xboxkrnl export beyond the 151 | **done** — see [`D1_EXTEND.md`](./D1_EXTEND.md) |
| D2  | NXDK integration (real Xbox SDK headers)               | **not started**   |

### Near-term candidates

1. **Jump-velocity patch (Phase 3, feature shim).**  Already
   requested.  Ghidra investigation: find the jump-button handler,
   locate the write to the player's velocity-Z that starts a jump,
   check whether the impulse is a `.rdata` constant (direct patch)
   or a hardcoded immediate (needs code-byte patching or a shim).
   The `PlayerInputState` / `CritterData` struct definitions are
   now ready; all that's missing is the specific call site.

2. **Skip `prophecy.bik` (Phase 3, feature shim).**  Same shape as
   `skip_logo` but targeting the second movie in the boot state
   machine.  Declare a new `qol_skip_prophecy` pack that hooks at
   `BOOT_STATE_PLAY_PROPHECY` (see `AZURIK_BOOT_STATE_VA` in
   `azurik.h`) — fastest shim to write as a second real C-shim
   example.

3. **Camera distance / FOV patch (Phase 3, feature shim).**
   Requires a short Ghidra pass to find the camera-projection
   setup.  Likely a `.rdata` float analogous to gravity — so most
   likely a simple `ParametricPatch`, not a shim.  Quick win.

### Mid-term / infrastructural

1. **Unicorn-backed test harness (B2).**  Landed as "deferred" so
   far because no shim has nontrivial logic that justifies the
   dependency (capstone + unicorn is ~15 MB).  Revisit when a shim
   with real state (e.g. a cheat system that tracks toggles across
   frames) wants unit tests of its runtime behaviour.

2. **Expand `azurik_vanilla.h` as demand arises.**  Currently
   exposes:
   - `play_movie_fn` / `poll_movie` — boot-time movie player
     (both `__stdcall`).
   - `entity_lookup` — entity registry name lookup (`__fastcall`,
     pinned from two callers in `FUN_000353F0` and `FUN_0003A610`).

   Investigated but **deferred**:
   - `FUN_00085700` (gravity integration) — Ghidra decomps as
     `__fastcall` but the body reads `in_EAX` as an implicit
     output-pointer (MSVC RVO pattern).  clang's
     `__attribute__((fastcall))` doesn't let you declare an
     implicit EAX parameter, so safe exposure needs a naked-asm
     wrapper.  See `docs/LEARNINGS.md` "Vanilla-function exposure".
   - `FUN_000d1420` / `FUN_000d1520` (config-table lookups) —
     `__thiscall`, same naked-asm-wrapper requirement.

   Add entries as concrete shims need them, not speculatively.

3. **`ControllerState` struct in `azurik.h`** — **done** (pinned
   2026-04-18).  84-byte struct at `DAT_0037BE98 + player_idx *
   0x54`, populated by the XInput polling loop in `FUN_000a2880`.
   Exposes analog sticks, dpad, 8 analog buttons, start / back,
   stick clicks, and the 12-byte edge-detect latch array.  Unlocks
   shims that rebind controls, add combo moves, etc.  See
   `docs/LEARNINGS.md` "ControllerState struct" for the full
   byte-level map.

4. **Drop tables + range fields in `CritterData`** — **done**.
   Added `range`, `range_up`, `range_down`, `attack_range`,
   `drop_1..5`, `drop_count_1..5`, `drop_chance_1..5`.  Offsets
   pinned against `FUN_00049480`.

### Long-term

1. **D1-extend — done.**  Shims can now call ANY xboxkrnl export,
   not just the 151 Azurik statically imports.  The runtime resolver
   walks xboxkrnl.exe's PE export table at the fixed retail base
   `0x80010000`; per-import resolving stubs cache the result inline
   so second-and-later calls cost one indirect jump.  Full design
   note + when-to-use guide in [`D1_EXTEND.md`](./D1_EXTEND.md).

2. **NXDK integration (D2).**  Full Xbox SDK (D3D8, DSound, XAPI,
   XGraphics, ...) wired into the shim toolchain.  Design note +
   migration plan + concrete milestones in
   [`docs/D2_NXDK.md`](./D2_NXDK.md).  Deferred because D1-extend
   (runtime kernel-export resolver) covers the kernel-API side,
   and no shipped shim has demanded native D3D / DSound yet.

3. **Unicorn-backed test harness (B2).**  Static byte checks only
   prove the shim LANDS correctly; they don't prove the runtime
   behaviour.  A Unicorn-based harness (capstone + unicorn = ~15 MB
   dependency) would let us execute a shim against a synthetic
   memory state and assert on side effects.  Deferred because no
   shim yet has nontrivial runtime state to validate.

### Ground rules for adding new fields / structs to `azurik.h`

- **Verify every offset against Ghidra** before naming it.  The
  pass that shipped B1 had five wrongly-named `CritterData` fields
  (the `ouch*_threshold` / `ouch*_knockback` row) because they were
  named from memory instead of cross-checked against the decomp.
  Always grep the decomp for the `FUN_000d1420("<name>")` lookup
  that precedes the `piVar9[N] = ...` write; if the config-key
  string doesn't appear, mark the field `_reservedNN` or
  `(speculative)` in a comment.

- **Add a matching `_Static_assert`** at the bottom of
  `azurik.h` for every new named field that sits past an
  unnamed gap.  A probe that checks the offset at test time is
  not enough — drift should fail AT COMPILE TIME so shims don't
  silently miscompile when someone reorders the struct.

- **Prefer byte-typed fields for booleans** that the engine
  writes as `*(bool *)(base + N)` (see `use_center_basis`,
  `hits_through_walls`).  Stuffing them inside `u32 flags`
  surfaces the same offset but loses single-byte load/store
  semantics.
