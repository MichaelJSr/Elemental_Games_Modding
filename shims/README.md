# Azurik C-shim library

This folder is the **shared library** for Azurik's C-shim modding
platform.  It holds the headers, toolchain, and compiled build cache
used by every shim-backed feature in the repo — but it does NOT hold
individual feature code anymore.

**Feature shims live next to their Python declaration** under
[`azurik_mod/patches/<name>/`](../azurik_mod/patches/).  For example,
the `qol_skip_logo` feature is a self-contained folder:

```
azurik_mod/patches/qol_skip_logo/
  __init__.py   # Feature(...) declaration + Python apply logic
  shim.c        # C source that implements the trampoline body
```

The feature-folder layout means deleting a feature = removing one
folder, and every moving part for a given mod is visible in one
`ls` command.

## What lives here

```
shims/
  README.md              # this file
  include/               # shared C headers every shim pulls in
    azurik.h             #   - runtime struct layouts (CritterData, ...)
    azurik_vanilla.h     #   - externs for vanilla Azurik functions
    azurik_kernel.h      #   - externs for xboxkrnl imports (151 of them)
  toolchain/
    compile.sh           # clang wrapper: i386 PE-COFF + freestanding
    new_shim.sh          # scaffold a new feature folder with shim.c
  fixtures/              # test-only shim sources — NOT shipped features
    _reloc_test.c        #   A2 relocation exercise
    _vanilla_call_test.c #   A3 vanilla-function-call exercise
    _shared_lib_test.c   #   E shared-library layout exercise
    _shared_consumer_a.c #   E consumer #1
    _shared_consumer_b.c #   E consumer #2
    _kernel_call_test.c  #   D1 kernel-import exercise
  build/                 # compiled .o cache (gitignored)
```

Every `.o` file in `build/` is keyed on its **pack name** (not the
source stem) so two features whose sources both happen to be called
`shim.c` can't collide.  The auto-compile hook in `apply_trampoline_patch`
maps `shims/build/<pack>.o` back to `azurik_mod/patches/<pack>/shim.c`.

## Adding a new shim-backed feature

Use the scaffold:

```bash
bash shims/toolchain/new_shim.sh my_feature
# creates azurik_mod/patches/my_feature/__init__.py
#         azurik_mod/patches/my_feature/shim.c
```

Then:

1. Fill in the trampoline VA + `replaced_bytes` in `__init__.py`
   (use Ghidra to confirm).
2. Write your shim body in `shim.c`.
3. Run the tests (`python -m pytest tests/ -q`).  Auto-compile
   rebuilds `shims/build/my_feature.o` on demand.
4. Boot the patched ISO in xemu to verify gameplay behaviour.

Full walkthrough: [`docs/SHIM_AUTHORING.md`](../docs/SHIM_AUTHORING.md).

## Toolchain

Apple clang (i386 cross-target) is enough — no NXDK, no linker, no
CRT.  `compile.sh` pins the exact invocation:

```
clang -target i386-pc-win32 -ffreestanding -nostdlib -fno-pic \
      -fno-stack-protector -fno-asynchronous-unwind-tables \
      -I shims/include -Os -c -o <out>.o <src>.c
```

Verified on:

```
Apple clang version 21.0.0 (clang-2100.0.123.102)
Target: arm64-apple-darwin25.4.0 (host) -> i386-pc-win32 (output)
```

Other platforms (Linux, Windows) can swap in any clang that accepts
`-target i386-pc-win32`.

## Relationship to the Python pipeline

| Python              | C-side                                      |
|---------------------|---------------------------------------------|
| `Feature(...)` in   | `shim.c` in the same folder                 |
| `azurik_mod/patches/<name>/__init__.py` |                         |
| `TrampolinePatch`   | Emits a 5-byte `CALL rel32` into the shim   |
| `apply_pack(...)`   | Invokes `compile.sh` → places `.text` →     |
|                     | wires REL32 relocations via `layout_coff`   |
| `ShimLayoutSession` | Dedupes kernel-import stubs / shared libs   |

Docs: [`docs/SHIMS.md`](../docs/SHIMS.md) (architecture) and
[`docs/SHIM_AUTHORING.md`](../docs/SHIM_AUTHORING.md) (end-to-end
walkthrough).

## Phase status

All platform pieces are done and regression-tested:

| Tier  | Feature                                         | Status |
|-------|-------------------------------------------------|--------|
| A1    | `append_xbe_section` (unbounded shim size)      | done   |
| A2    | Relocation-aware COFF loader                    | done   |
| A3    | Vanilla-function calls via `vanilla_symbols`    | done   |
| B1    | `azurik.h` populated with named structs         | done   |
| B3    | `new_shim.sh` scaffold                          | done   |
| C1    | Player-speed shim (motivating deliverable)      | done   |
| D1    | xboxkrnl imports (all 151)                      | done   |
| E     | Shared-library shim layout                      | done   |
| Tool  | Auto-compile missing `.o` on apply              | done   |
| Layout| Folder-per-feature reorganisation               | done   |

See [`docs/SHIMS.md`](../docs/SHIMS.md) for the full roadmap + what
remains deferred (Unicorn-backed test harness, D1-extend, NXDK).
