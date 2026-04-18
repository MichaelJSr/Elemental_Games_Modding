# Azurik C-shim Library

This folder holds C sources for **shim functions** that get compiled and
injected into Azurik's XBE via the `TrampolinePatch` mechanism in
[`azurik_mod.patching`](../azurik_mod/patching/).  Each shim is a small
C function; at apply time, the patcher:

1. Compiles the shim into an i386 PE-COFF `.o` (via
   [`toolchain/compile.sh`](toolchain/compile.sh)).
2. Extracts the shim's `.text` bytes and copies them into unused
   padding at the end of the XBE's `.text` section (or appends a new
   XBE section if padding is insufficient).
3. Writes a 5-byte `CALL rel32` / `JMP rel32` trampoline at the
   target VA to divert control flow into the shim.

This replaces the byte-level patch style (writing raw machine-code
bytes directly via `PatchSpec`) for features that are easier to
express in C than in hand-assembled opcodes.

## Toolchain

Apple clang (i386 cross-target) on macOS is enough for Phase 1 shims
— no kernel imports, no D3D, just arithmetic / branching.  The exact
invocation is pinned in [`toolchain/compile.sh`](toolchain/compile.sh)
and was verified to produce a working PE-COFF output on:

```
Apple clang version 21.0.0 (clang-2100.0.123.102)
Target: arm64-apple-darwin25.4.0 (host) -> i386-pc-win32 (output)
```

Other platforms (Linux, Windows) can swap in any clang that accepts
`-target i386-pc-win32`; the PE-COFF output format is stable across
clang versions.

## Directory layout

```
shims/
  README.md                     this file
  toolchain/
    compile.sh                  clang wrapper (i386 + PE-COFF + freestanding)
  include/
    azurik.h                    shared typedefs; grows as subsystems are shimmed
  src/
    skip_logo.c                 first shim: void c_skip_logo(void) {}
  build/                        .o outputs (gitignored)
```

## Authoring a new shim

1. Drop `src/<feature>.c` with your C function(s).  Export each with
   a `c_` prefix by convention (e.g. `void c_my_feature(void)`).
2. Run `toolchain/compile.sh src/<feature>.c` to produce
   `build/<feature>.o`.
3. Declare a `TrampolinePatch` in the relevant pack module (e.g.
   [`azurik_mod/patches/qol.py`](../azurik_mod/patches/qol.py)) with:
   ```python
   MY_FEATURE_TRAMPOLINE = TrampolinePatch(
       name="my_feature",
       label="My feature",
       va=0xDEADBEEF,              # site in vanilla XBE
       replaced_bytes=bytes([...]),
       shim_object=Path("shims/build/my_feature.o"),
       shim_symbol="_c_my_feature", # note the leading underscore
       mode="call",                 # or "jmp"
   )
   ```
4. Add the trampoline to a `PatchPack`'s `sites=[...]` list.
5. Write a pytest in `tests/test_*.py` that pins the VA + the expected
   `CALL rel32` after apply.
6. Run `azurik-mod verify-patches --xbe <patched>.xbe --original
   <vanilla>.xbe --strict` to confirm the whitelist diff stays clean.

## Phase 1 scope

- Shims with no imports (no `XKernel*`, no `D3D*`, no game-function
  calls).  Pure arithmetic / branching only.
- `.text`-only `.o` files (no `.data`, no `.rdata`, no relocations).
- One symbol per shim, explicitly named.

Phase 2 (later) will add:
- Kernel / D3D import table rewriting for shims that call into Xbox
  system libraries.
- Relocation handling for shims with `.rdata` or cross-section refs.
- Shim-to-vanilla calls (trampoline back into original game functions
  at known VAs).
- NXDK integration for shims that need real Xbox SDK APIs.
