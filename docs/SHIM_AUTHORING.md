# Shim authoring — complete guide

Everything you need to know to write, test, and ship a C shim for
Azurik.  Start here if you want to add any non-trivial patch.

> **New to the repo?**  Read [`ONBOARDING.md`](./ONBOARDING.md) first
> — this file assumes you know the feature-folder layout and the
> `apply_pack` dispatcher.

> **TL;DR** — For a 2-line patch (flip one byte, change one float),
> use `PatchSpec` or `ParametricPatch` directly; shims are overkill.
> For anything with logic (a conditional, a computation, a table
> lookup, a kernel call), write a C shim.

---

## 0. Mental model in 90 seconds

1. The shim is a normal C function compiled to an i386 PE-COFF `.o`.
2. Your `TrampolinePatch` declares a site in the vanilla XBE
   (e.g. a 5-byte `CALL` instruction) and the shim's entry symbol.
3. At apply time the pipeline:
   - parses the `.o` (`parse_coff`)
   - places every landable section into the XBE's `.text` slack OR a
     newly-appended `SHIMS` section (`_carve_shim_landing`)
   - resolves every undefined external (calls to vanilla Azurik,
     xboxkrnl imports, other shared-library shims) via the layout
     session
   - applies COFF relocations in place (`layout_coff`)
   - emits a 5-byte `CALL`/`JMP rel32` at the trampoline site.
4. At runtime the Xbox loader maps the XBE; your shim executes
   exactly like hand-written assembly.

That's the whole pipeline.  Everything below is detail.

---

## 1. When to write a shim vs. a simple patch

| Kind of change                                   | Use                  |
|--------------------------------------------------|----------------------|
| Flip one byte at a known offset                  | `PatchSpec`          |
| Change one float that's a `.rdata` constant      | `ParametricPatch`    |
| Skip an instruction with `NOP`s                  | `PatchSpec`          |
| Wrap / replace a function with your own logic    | **shim**             |
| Read / write a struct field conditionally        | **shim**             |
| Call a kernel API (`DbgPrint`, `NtOpenFile`, …)  | **shim**             |
| Call a vanilla Azurik function (play_movie_fn)   | **shim**             |
| Add persistent state that survives across frames | **shim** (`.data` / `.bss` sections land automatically) |

Shims cost more up front (C source + compile step + trampoline
declaration + test) but scale to arbitrarily complex logic.  A
byte patch can't.

---

## 2. Authoring workflow — 8 steps

### Step 1 — Scaffold the feature folder

```bash
bash shims/toolchain/new_shim.sh my_feature
# creates azurik_mod/patches/my_feature/__init__.py
#         azurik_mod/patches/my_feature/shim.c
```

The scaffold produces a self-contained feature folder:

```
azurik_mod/patches/my_feature/
  __init__.py    # Feature(...) declaration + apply helper
  shim.c         # starter C source, empty __stdcall body
```

Deleting the feature later means removing one folder — no orphaned
references scattered across `shims/src/` and `azurik_mod/patches/`.

The scaffold:
- Rejects invalid names (must be lowercase C identifier: `/^[a-z_][a-z0-9_]*$/`).
- Refuses to overwrite existing feature folders.
- Pre-fills the `Feature(...)` declaration with TODO placeholders for
  the trampoline VA and `replaced_bytes`, plus a sketch of the
  `apply_my_feature_patch` function.
- Generates a `shim.c` with the standard `__attribute__((stdcall))`
  annotation and the three common includes (`azurik.h`,
  `azurik_vanilla.h`, `azurik_kernel.h`).

### Step 2 — Write the C body

Prefer **named fields from `azurik.h`** over raw `[reg + 0xNN]`
offsets.  The static asserts at the bottom of the header will catch
any struct drift at compile time, so you won't get a silently-wrong
shim months from now when someone reorders a field.

```c
#include "azurik.h"
#include "azurik_vanilla.h"   /* play_movie_fn, poll_movie — call these */
#include "azurik_kernel.h"    /* DbgPrint, NtReadFile — call these */

__attribute__((stdcall))
void c_my_feature(PlayerInputState *p) {
    if (p->flags & PLAYER_FLAG_RUNNING) {
        p->magnitude *= 1.5f;
    }
}
```

**ABI rules you must obey:**
- Match the trampoline's calling convention exactly.  A site that
  replaces a `CALL` to a `__stdcall` function must have your shim
  also declared `__stdcall` — otherwise the stack pops don't match
  and the game crashes.
- Return type matters only if the vanilla function returned something
  the caller inspects.  Most boot-state functions return `AL` (a
  byte) — the shim's signature should match.

### Step 3 — Compile

```bash
bash shims/toolchain/compile.sh \
  azurik_mod/patches/my_feature/shim.c \
  shims/build/my_feature.o
```

Or just run the tests — `apply_pack` auto-compiles missing `.o`s on
demand (set `AZURIK_SHIM_NO_AUTOCOMPILE=1` to disable for CI).

Inspect what the compiler actually produced:

```bash
objdump -d shims/build/my_feature.o
```

Check for:
- The entry symbol you declared (`_c_my_feature@4` or similar).
- Any unexpected external references — if clang inserted a memcpy
  call for a struct assignment, you'll see an undefined
  `_memcpy` symbol the layout pass has no way to resolve.
  (Fix: rewrite the struct assignment to per-field copies, or use
  builtin memcpy annotations that clang inlines.)

**Side note on auto-compile:** as of the auto-compile refinement,
`apply_trampoline_patch` runs `compile.sh` for you when the `.o`
doesn't exist but the `.c` does.  Useful for CI and for rebuilding
after edits.  Opt out with `AZURIK_SHIM_NO_AUTOCOMPILE=1` if you
want to guarantee bit-for-bit reproducibility from a committed `.o`.

### Step 4 — Fill in `__init__.py`

The scaffold left a `TrampolinePatch` skeleton for you; replace the
TODO placeholders with real values from Ghidra:

```python
MY_FEATURE_TRAMPOLINE = TrampolinePatch(
    name="my_feature",
    label="Short human-readable description",
    va=0x00087654,                        # site in the vanilla XBE
    replaced_bytes=bytes.fromhex(         # exact bytes we overwrite
        "E8 12 34 56 78 90 90 90 90 90"   # the original CALL + NOPs
    ),
    shim_object=_SHIM.object_path("my_feature", _REPO_ROOT),
    shim_symbol="_c_my_feature@4",        # note the leading underscore
    mode="call",                          # or "jmp"
)
```

Notice `shim_object` is computed from the `_SHIM = ShimSource(...)`
helper the scaffold added at the top of the file — no hardcoded
`Path("shims/build/...")` strings.  The compiled `.o` lands at
`shims/build/my_feature.o` (shared build cache, keyed on the pack
name).

**Getting `replaced_bytes` right:**
- Open the site in Ghidra.
- Copy the hex of the instruction you're replacing, plus any NOP
  padding you're claiming.
- Minimum 5 bytes (needed for the rel32 jump); the pipeline
  NOP-fills anything past byte 5.
- If you get the bytes wrong, `apply_trampoline_patch` refuses and
  prints a diff (got X, expected Y).  It will NEVER silently
  overwrite an unrecognised sequence.

### Step 5 — Register the feature

The scaffold already wrote a `register_feature(Feature(...))` call.
Fill in the user-facing description and tags:

```python
FEATURE = register_feature(Feature(
    name="my_feature",
    description="<user-facing text for the GUI/CLI>",
    sites=[MY_FEATURE_TRAMPOLINE],
    apply=apply_my_feature_patch,
    category="qol",              # GUI tab; pick existing or new id
    tags=("c-shim",),            # secondary badge surfaced in audits
    shim=_SHIM,                  # enables auto-compile on missing .o
))
```

Importing `azurik_mod.patches` runs every feature folder's
`register_feature(...)` side effect, so the new pack appears in the
GUI, CLI, and `verify-patches --strict` without touching anything
else.

### Step 6 — Tests (non-negotiable)

A trampoline without tests WILL eventually break.  Pattern after
`tests/test_qol_skip_logo.py`:

```python
class MyFeatureTrampolineShape(unittest.TestCase):

    def test_descriptor_fields_are_stable(self):
        self.assertEqual(MY_FEATURE_TRAMPOLINE.va, 0x00087654)
        self.assertEqual(len(MY_FEATURE_TRAMPOLINE.replaced_bytes), 10)
        self.assertEqual(MY_FEATURE_TRAMPOLINE.shim_symbol,
                         "_c_my_feature@4")

    def test_apply_verify_roundtrip(self):
        xbe = bytearray(_VANILLA_XBE.read_bytes())
        ok = apply_my_feature_patch(xbe)
        self.assertTrue(ok)
        self.assertEqual(
            verify_trampoline_patch(bytes(xbe), MY_FEATURE_TRAMPOLINE),
            "applied",
        )
```

Every pack in the registry has at least one such test.

### Step 7 — Run the suite

```bash
python -m pytest tests/ -q
```

If your shim references a vanilla function not yet in
`vanilla_symbols.py`, the header drift guard will fail loudly with
a pointer to what you need to register.  Same for kernel imports
not in `xboxkrnl_ordinals.py`.

### Step 8 — Boot in xemu

Unit tests prove the shim BYTES are correct.  They DO NOT prove
the shim BEHAVIOUR is correct — that requires an emulator or
real hardware.  Always do a final boot check:

```bash
azurik-gui            # or `azurik-mod randomize-iso ...`
# open the resulting ISO in xemu, play through the affected content
```

If you see a black screen or an instant crash, check
`~/Library/Logs/...` for the log file, and:
- Consult Ghidra to confirm your trampoline site isn't mid-
  instruction.
- Double-check the replaced_bytes match byte-for-byte.
- Run the shim in `DbgPrint` debug mode (xemu + `-debug`) — your
  shim's printfs will show up in the debug log.

---

## 3. Calling into the engine

### 3a. Vanilla Azurik functions (A3)

Declared in `shims/include/azurik_vanilla.h`; VA + ABI registered in
`azurik_mod/patching/vanilla_symbols.py`.

Example — a shim calls `play_movie_fn`:

```c
#include "azurik_vanilla.h"

__attribute__((stdcall))
int c_override_boot_movie(const char *requested_movie, char flag) {
    /* Substitute our own movie for the one the engine wanted. */
    return play_movie_fn("CustomBoot.bik", flag);
}
```

To add a new vanilla function:

1. Confirm VA and calling convention in Ghidra.  Look at a vanilla
   call site:
   - If you see `ADD ESP, N` cleanup after the call → cdecl (caller
     cleans stack).
   - If not → stdcall (callee cleans via `RET N`).
   - `MOV ECX, ...; MOV EDX, ...; CALL ...` → fastcall.
2. Register it in `vanilla_symbols.py`:

   ```python
   register(VanillaSymbol(
       name="my_fn",
       va=0x000ABCDE,
       calling_convention="stdcall",
       arg_bytes=8,
       doc="What the function does; return-value meaning."))
   ```
3. Declare it in `azurik_vanilla.h`:

   ```c
   /* Vanilla VA: 0x000ABCDE  (mangled: _my_fn@8) */
   __attribute__((stdcall))
   int my_fn(int a, int b);
   ```
4. Run `pytest tests/test_vanilla_thunks.py` — the drift guard
   enforces that the two sources of truth match.

### 3b. Kernel imports (D1)

All 151 kernel functions Azurik's XBE imports are declared in
`shims/include/azurik_kernel.h`.  Just include the header and call.

```c
#include "azurik_kernel.h"

__attribute__((stdcall))
void c_frame_time_probe(void) {
    LARGE_INTEGER now;
    KeQueryPerformanceCounter(&now);
    DbgPrint("frame tick: %u\n", now);
}
```

No registration needed — the shim layout session parses the XBE's
kernel thunk table at apply time and auto-generates a stub per
referenced kernel function.  Stubs are cached session-wide, so
multiple shims calling `DbgPrint` share a single stub.

If you need a kernel function Azurik **doesn't** already import,
see `docs/SHIMS.md` → "What this header does NOT give you" for the
options (vanilla wrapper route or D1-extend future work).

### 3c. Shared-library helpers (E)

When several trampolines want to reuse the same helper function,
factor it out:

`shims/fixtures/my_shared_lib.c` (or in a dedicated feature folder
for cross-pack sharing):
```c
__attribute__((stdcall))
int shared_compute(int a, int b) { return a * 2 + b; }
```

`azurik_mod/patches/my_feature_a/shim.c`:
```c
__attribute__((stdcall))
int shared_compute(int, int);     /* declared here, defined in my_shared_lib */

__attribute__((stdcall))
int c_trampoline_a(int x) {
    return shared_compute(x, 1);
}
```

Pack apply function:

```python
from azurik_mod.patching.shim_session import get_or_create_session

def apply_my_pack(xbe_data: bytearray) -> None:
    sess = get_or_create_session(xbe_data)
    sess.apply_shared_library(
        _REPO_ROOT / "shims/build/my_shared_lib.o",
        allocate=lambda _n, ph: _carve_shim_landing(xbe_data, ph),
    )
    apply_trampoline_patch(xbe_data, TRAMPOLINE_A, repo_root=_REPO_ROOT)
    apply_trampoline_patch(xbe_data, TRAMPOLINE_B, repo_root=_REPO_ROOT)
```

Both consumers' COFF externs resolve to the same placement — no
duplicated machine code.  `tests/test_shared_library.py` asserts
this directly.

---

## 4. Common pitfalls

### Calling-convention mismatch

**Symptom:** game crashes shortly after the shim returns, or
subsequent function calls look normal but start using garbage
arguments.

**Cause:** Your shim was declared `__stdcall` but the vanilla caller
uses cdecl (or vice versa).  The stack is either over-popped or
under-popped; subsequent code runs on a misaligned stack.

**Fix:** re-check the Ghidra decomp of the callers of your site.
- `CALL shim` followed by `ADD ESP, 8` → cdecl. Your shim declaration
  must NOT have `__stdcall`.
- `CALL shim` followed directly by the next non-stack instruction →
  stdcall. Your shim declaration MUST have `__attribute__((stdcall))`.

### Wrong struct offset

**Symptom:** the game behaves as if the shim's read/write went to a
different field.  Physics goes crazy, animations play the wrong one,
etc.

**Cause:** you're using a raw `[ebp + 0x34]` or a struct field whose
offset is wrong in `azurik.h`.

**Fix:** use the named fields from `azurik.h`.  If a named field
doesn't exist for what you need, add it — verify the offset against
Ghidra, then add a `_Static_assert(__builtin_offsetof(...) == 0xNN)`
line to the bottom of the header.  That static assert will fire at
compile time if the field ever drifts.

### Relocation-out-of-range

**Symptom:** `apply_trampoline_patch` raises
`REL32 displacement 0xXXX does not fit signed 32-bit`.

**Cause:** Your shim (or the vanilla function it's calling) is more
than 2 GiB away from the call site.  On a 4 MiB Xbox image this
should be impossible in practice, so this usually means the VA math
is wrong somewhere — maybe the shim was placed in the wrong section.

**Fix:** print `shim_entry_va` and the trampoline site VA at apply
time, compute the delta, and check it's a reasonable small number
(<1 MiB).  If it is, the VA arithmetic is broken upstream.

### Vanilla-symbol mangling mismatch

**Symptom:** drift guard fires with `name foo not registered`.

**Cause:** the Python `VanillaSymbol` entry has the wrong
`calling_convention` or `arg_bytes`, so its `mangled` property
doesn't match the symbol the compiler emitted.

**Fix:**
- Recount arg bytes (remember: `BOOLEAN` counts as 4 bytes on the
  stack; `LARGE_INTEGER*` is a pointer = 4 bytes, not 8).
- Double-check cdecl vs stdcall at the vanilla call site in Ghidra.

### DCE eating your shim

**Symptom:** `extract_shim_bytes` or `layout_coff` raises `COFF has
no landable sections`.

**Cause:** clang saw your shim as unused and DCE'd the whole `.text`
section.  Usually happens when the shim calls no externals and has
no side effects visible to the optimiser.

**Fix:** mark the entry point `__attribute__((used))` or add an
external dependency (e.g. `DbgPrint` from `azurik_kernel.h`).

---

## 5. Debugging a broken shim

Order of investigation when something goes wrong:

1. **Unit test first.**  Did it pass?
   - If not, the shim bytes aren't what you think they are.  Run
     the test with `-v` and read the specific assertion.
2. **objdump the .o.**  Does your entry symbol exist with the right
   mangled name?  Do the relocations look sane?
3. **Apply in isolation.**  Build a small Python script that calls
   `apply_trampoline_patch` on the vanilla XBE and writes the result
   to a temp file.  Open the temp XBE in Ghidra, navigate to the
   trampoline VA, and confirm the CALL target is in your placed
   shim region.
4. **Hex-diff vanilla vs patched XBE.**  Spots any surprise writes.
5. **Ghidra-disassemble the shim's landed bytes.**  Are the
   relocations resolved correctly?  Is the shim body the same
   logic you wrote in C?
6. **Boot in xemu with debug.**  `xemu -debug` + `DbgPrint` from
   inside the shim tells you exactly when it runs.

---

## 6. Advanced topics

### Long shims (> 16 bytes of `.text` slack)

Don't worry about it — `_carve_shim_landing` handles this.  The
pipeline:
1. Tries the existing `.text` trailing slack.
2. Grows `.text` into the VA gap before the next section (Azurik has
   16 bytes of gap before `BINK`).
3. Appends a brand-new `SHIMS` section via `append_xbe_section` if
   you exceed the gap.

The appended section grows the XBE's section-header array in place,
shifts every post-array byte forward by 56, and rewrites every image-
header pointer that moved.  Azurik has ~880 bytes of header-to-.text
headroom, so there's plenty of room for the 56-byte growth.

### Idempotent re-apply

`apply_trampoline_patch` detects already-installed trampolines (same
opcode + trailing NOPs at the site) and leaves them alone.  Running
apply twice is a safe no-op — you won't stack two trampolines.

### The legacy byte-patch escape hatch

A feature can declare `legacy_sites=(...,)` in its `Feature(...)`
call to list the byte-patch fallback sites that should run when
the environment variable `AZURIK_NO_SHIMS=1` is set.  `apply_pack`
swaps every `TrampolinePatch` in the pack for the legacy list when
the variable is active — hosts without a working i386 clang toolchain
can still ship a patched XBE.

One env var covers every shim-backed pack.  No per-pack
`AZURIK_SKIP_LOGO_LEGACY` / `AZURIK_MY_FEATURE_LEGACY` sprawl.

---

## 7. File reference

| Role                          | Path                                                     |
|-------------------------------|----------------------------------------------------------|
| Scaffold generator            | `shims/toolchain/new_shim.sh`                            |
| Clang wrapper                 | `shims/toolchain/compile.sh`                             |
| Feature folder                | `azurik_mod/patches/<name>/`                             |
| Feature Python spec           | `azurik_mod/patches/<name>/__init__.py`                  |
| Feature shim source           | `azurik_mod/patches/<name>/shim.c` (optional)            |
| Compiled .o                   | `shims/build/<name>.o` (shared cache)                    |
| Runtime struct declarations   | `shims/include/azurik.h`                                 |
| Vanilla function externs      | `shims/include/azurik_vanilla.h`                         |
| Kernel import externs         | `shims/include/azurik_kernel.h`                          |
| Test fixture shim sources     | `shims/fixtures/_*.c`                                    |
| Vanilla-function registry     | `azurik_mod/patching/vanilla_symbols.py`                 |
| Kernel-import ordinal table   | `azurik_mod/patching/xboxkrnl_ordinals.py`               |
| Kernel-import runtime parser  | `azurik_mod/patching/kernel_imports.py`                  |
| Shim layout session (D1 + E)  | `azurik_mod/patching/shim_session.py`                    |
| COFF parser + layout_coff     | `azurik_mod/patching/coff.py`                            |
| XBE section surgery           | `azurik_mod/patching/xbe.py`                             |
| `apply_pack` dispatcher       | `azurik_mod/patching/apply.py`                           |
| `ShimSource` helper           | `azurik_mod/patching/feature.py`                         |
| Feature / pack registry       | `azurik_mod/patching/registry.py`                        |

---

## 8. See also

- [`SHIMS.md`](./SHIMS.md) — high-level platform overview + status
  table.
- [`AGENT_GUIDE.md`](./AGENT_GUIDE.md) — AI-agent-specific workflow.
- [`LEARNINGS.md`](./LEARNINGS.md) — accumulated reverse-engineering
  findings that might save you a Ghidra session.
- [`PATCHES.md`](./PATCHES.md) — catalog of every pack currently in
  the repo, including which are shim-backed.
