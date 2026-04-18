# C-shim modding platform — Phase 1

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
bash shims/toolchain/compile.sh shims/src/skip_logo.c
# → shims/build/skip_logo.o  (Intel 80386 COFF object, .text-only)
```

No linker, no CRT, no libc.  The compile flags are tight on purpose —
Phase 1 shims must be self-contained.

Verified on:

```
Apple clang version 21.0.0 (clang-2100.0.123.102)
Target: arm64-apple-darwin25.4.0 (host)  →  i386-pc-win32 (output)
```

## Authoring a new shim — end-to-end checklist

1. **Write the C source** in [`shims/src/`](../shims/src/).  Export
   each function with a ``c_`` prefix so the PE-COFF symbol comes out
   as ``_c_<name>`` (Windows/MSVC leading-underscore convention).

   Phase 1 shims must be relocation-free — no globals, no imports,
   no strings.  If you need any of those, the shim belongs in Phase 2.

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

## Capabilities (Phase 2 A1+A2+A3)

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

## Limitations (still)

- **No cross-shim calls between different `TrampolinePatch` sites**.
  Each `apply_trampoline_patch` invocation gets its own placement
  pass; two sites that want to share a helper function would each
  install a private copy.  A shared-library layout pass is Phase 3.
- **No kernel / D3D imports**.  Shims can't call `XKernel*` or
  `D3D*` routines — the XBE import-table rewrite is Phase 2 D work.
  Vanilla Azurik functions that wrap those APIs ARE callable via
  the A3 vanilla-symbol registry (the shim calls Azurik, Azurik
  calls the kernel).
- **Escape hatch preserved**.  The legacy byte-patch form of every
  migrated pack stays behind an env var (`AZURIK_SKIP_LOGO_LEGACY=1`
  etc.) so users on a host without the i386 clang toolchain can
  still ship a patched XBE.

## Troubleshooting

- **`shim object not found`** — run
  `bash shims/toolchain/compile.sh shims/src/<name>.c` to build the
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
shims/
  README.md                     authoring notes (also summarized here)
  toolchain/
    compile.sh                  clang wrapper
  include/
    azurik.h                    freestanding typedefs
  src/
    skip_logo.c                 Phase 1 proof-of-concept shim
  build/                        compiled .o outputs (gitignored)

azurik_mod/patching/
  coff.py                       minimal PE-COFF reader
  spec.py                       TrampolinePatch NamedTuple
  xbe.py                        find_text_padding + grow_text_section
  apply.py                      apply_trampoline_patch + verify_*
  registry.py                   pack enumeration (trampoline-aware)

tests/
  test_trampoline_patch.py      low-level COFF + xbe + apply + verify
  test_qol_skip_logo.py         end-to-end through the migrated pack

docs/
  SHIMS.md                      this file
  PATCHES.md                    catalog with shim-backed entries called out
```
