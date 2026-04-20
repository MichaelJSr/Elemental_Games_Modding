# D1-extend — runtime xboxkrnl export resolver

> **Status**: shipped (Phase 2 D1-extend).  Every xboxkrnl export is
> reachable from a shim — not just the 151 Azurik's vanilla XBE
> statically imports.
>
> **See also**: [`SHIMS.md`](./SHIMS.md) for the overall platform
> picture.  A full NXDK (D3D / DSound / XAPI) integration is
> deferred behind this milestone — no shipped shim has demanded it
> yet.

---

## 1. Problem statement

Azurik's vanilla XBE statically imports 151 xboxkrnl functions
(catalogued in [`xboxkrnl_ordinals.py`](../azurik_mod/patching/xboxkrnl_ordinals.py)).
Each static import is a 4-byte slot in the kernel thunk table at
`0x0018F3A0`; the Xbox loader fills each slot with the runtime
function pointer before the game entry point runs.

Static D1 (Phase 2 A3's predecessor) handles calls to those 151.
Emit a 6-byte `FF 25 <thunk_va>` stub in the SHIMS region, point
the shim's `CALL _Foo@N` REL32 at the stub, done.

The other ~218 xboxkrnl exports (`NtQueryInformationProcess`,
`DbgBreakPoint`, `KeEnterCriticalRegion`, `MmIsAddressValid`, ...)
have **no thunk slot**.  A shim that wants to call one gets
"unresolved external" from `layout_coff` — no surface area.

Options to fix it:

| Option | Cost | Gated on |
|--------|------|----------|
| **A. Extend the thunk table in place** | Shift ~10 downstream XBE header regions; rewrite every affected header pointer; risk breaking the library-version table that sits right after the thunks. | Disk-side XBE surgery that's easy to get wrong. |
| **B. Move the thunk table** | Rewrite every `CALL [thunk_va]` site in the game's `.text` (hundreds of them) to point at the new location. | Static analysis of every call site; new patch verifier. |
| **C. Runtime export resolver** | One ~50-byte helper shim + ~30-byte stub per extended import.  Zero XBE header changes. | Knowing xboxkrnl's load address + PE export table layout. |

We ship **Option C** — D1-extend — because it has by far the
smallest blast radius and doesn't compromise any existing call
site.  The runtime cost is one extra L1-cache load on the first
call per import; subsequent calls are a single indirect jump.

---

## 2. How it works

### 2a. Shared resolver

[`shims/shared/xboxkrnl_resolver.c`](../shims/shared/xboxkrnl_resolver.c)
implements:

```c
void *xboxkrnl_resolve_by_ordinal(unsigned ordinal);
```

It walks xboxkrnl.exe's PE export table, which the Xbox retail
kernel loader maps at the fixed VA `0x80010000`.  The resolver
dereferences:

1. `[0x80010000 + 0x3C]` — DOS header's `e_lfanew` (offset of the
   PE header).
2. PE header + 4 + 20 + 0x60 — data directory entry 0 (EXPORT)
   holds the RVA of the export table.
3. Export table's `AddressOfFunctions` + `Base` / `NumberOfFunctions`
   give the function-pointer array indexed by ordinal.

Output: the resolved function pointer in the live kernel image.
Self-contained — no kernel imports, no vanilla Azurik calls, no
global state.  Compiles to ~50 bytes of i386 code.

### 2b. Per-import resolving stub

For every extended kernel import a shim references, the apply
pipeline emits a 33-byte stub:

```
 off  bytes                   instruction
 ----+----------------------+----------------------------------
 0x00 A1 <cache_va:4>        MOV  EAX, [cache_va]
 0x05 85 C0                  TEST EAX, EAX
 0x07 75 12                  JNZ  +0x12  (to offset 0x1B)
 0x09 68 <ordinal:4>         PUSH imm32 <ordinal>
 0x0E E8 <rel32:4>           CALL xboxkrnl_resolve_by_ordinal
 0x13 83 C4 04               ADD  ESP, 4
 0x16 A3 <cache_va:4>        MOV  [cache_va], EAX
 0x1B FF E0                  JMP  EAX                    (resolved)
 0x1D 00 00 00 00            DWORD cache                 (zero at load)
```

First call: `EAX` is zero, fall through to PUSH + CALL resolver,
store result, JMP to resolved kernel function.

Subsequent calls: `EAX` is non-zero, JNZ +0x12 straight to JMP.
Three instructions total: one abs32 load + test + indirect jump.
No L1 overhead beyond what the CALL-site itself pays.

`cache_va = stub_va + 0x1D` — the 4-byte cache sits at the end of
the stub allocation, allocated inline so every extended import has
its own private slot.

### 2c. Dispatch in `ShimLayoutSession`

[`shim_session.py`](../azurik_mod/patching/shim_session.py)'s
`stub_for_kernel_symbol` is the single entry point the
`layout_coff` extern resolver chain calls for undefined kernel
externs.  Dispatch:

1. Cache hit → reuse existing stub VA.
2. Name → ordinal via `NAME_TO_ORDINAL` (static + extended merged).
3. `is_azurik_imported(ordinal)` → emit static D1 stub
   (`FF 25 <thunk_va>`, 6 bytes).
4. Else → `_place_extended_kernel_stub`:
   - `_ensure_resolver_placed` — auto-compile & land the resolver
     shim in the SHIMS region once per session.
   - Allocate 33 bytes, build the resolving stub bytes with the
     right `cache_va` + `ordinal` + `rel32`, write them out.
5. Cache mangled-name → stub VA.

Idempotent across multiple shims in the same pack: two trampolines
referencing `DbgBreakPoint` share the same stub and cache slot.

### 2d. Header: `azurik_kernel_extend.h`

[`shims/include/azurik_kernel_extend.h`](../shims/include/azurik_kernel_extend.h)
declares ~60 of the most useful extended imports with their
correct `NTAPI` / `FASTCALL` annotations.  Including it gives a
shim full coverage of the debug / sync / I/O / memory / runtime
library extensions.

For xboxkrnl exports not declared in this header (about 150 more):
declare the extern manually in your shim source with the correct
name + calling convention, and add the entry to
`EXTENDED_KERNEL_ORDINALS` in `xboxkrnl_ordinals.py` if it isn't
already catalogued.  The dispatch path handles it automatically —
no special-case code needed.

---

## 3. Ordinal catalogue

[`xboxkrnl_ordinals.py`](../azurik_mod/patching/xboxkrnl_ordinals.py)
now carries **two** tables:

- `AZURIK_KERNEL_ORDINALS` — 151 entries, Azurik's static imports.
- `EXTENDED_KERNEL_ORDINALS` — ~100 additional entries covering
  the most commonly-useful non-imported exports.

Their union (via `ALL_KERNEL_ORDINALS`) drives the dispatch.  The
forward lookup (`NAME_TO_ORDINAL[name]`) deliberately prefers the
Azurik static ordinal when a name appears in both — so D1's fast
path wins over D1-extend's slower first-call resolver whenever the
function is statically imported.

To add more ordinals: find the canonical ordinal number for the
function (cross-reference OpenXDK's xboxkrnl.h + Cxbx-Reloaded's
XDK export map) and append to `EXTENDED_KERNEL_ORDINALS`.  Tests
in `test_d1_extend.py::ExtendedOrdinalTable` enforce uniqueness
and "name → ordinal → name" round-trip consistency.

---

## 4. When to use D1 vs D1-extend

| Scenario | Path |
|----------|------|
| Call a function Azurik already imports (DbgPrint, NtClose, KeQueryPerformanceCounter, KeWaitForSingleObject, ...) | **D1 static** — automatic via `azurik_kernel.h`. Single indirect jump. |
| Call a function Azurik doesn't import but that's common (DbgBreakPoint, KeEnterCriticalRegion, RtlZeroMemory, MmIsAddressValid, ...) | **D1-extend** — automatic via `azurik_kernel_extend.h`. First call ~1 µs, subsequent calls ~1 ns. |
| Call a vanilla Azurik function (play_movie_fn, entity_lookup, ...) | **A3 vanilla-symbol registry** — declared in `azurik_vanilla.h`. Single CALL; no stub at all. |
| Call D3D8 / DSound / XAPI (native Xbox SDK) | **Not yet — deferred as a future NXDK integration (D2)**. |

The dispatch is entirely automatic: write `#include
"azurik_kernel_extend.h"` and call the function.  The layout
session figures out which path to use based on which table the
ordinal sits in.

---

## 5. Limitations

- **Retail kernel only**.  The hardcoded `XBOXKRNL_BASE_VA =
  0x80010000` is the retail Xbox kernel base.  Debug and Chihiro
  kernels use different bases and aren't supported.  (This matches
  the platform's existing retail-only scope.)
- **Ordinal-based only**.  The resolver doesn't walk the names
  array — xboxkrnl's retail exports are ordinal-only in practice,
  so there are no names to walk anyway.  If a future catalogue
  entry points at an ordinal that's unreserved in the exports
  array, the resolver returns NULL and the subsequent `JMP EAX`
  raises an exception on the Xbox side.  Validate ordinals
  against a known-good reference before adding new entries.
- **No way to unload**.  Once a stub's cache slot is populated,
  it stays populated until the game exits.  Fine for all current
  use cases; noted here so future work on hot-swappable shims
  knows.
- **~33 bytes per unique extended import**.  A shim that uses 20
  extended imports adds ~660 bytes of stubs + ~50 bytes of
  resolver = ~700 bytes total to the SHIMS region.  Negligible
  against the 64 KB section size headroom.

---

## 6. Testing

`tests/test_d1_extend.py` pins five layers:

1. **Ordinal catalogue**: uniqueness, non-empty, Azurik-wins-on-collision,
   spot checks of well-known ordinals.
2. **Stub byte shape**: exactly 33 bytes, pinned opcodes at
   pinned offsets, cache-VA math, REL32-overflow error.
3. **Resolver shim compile**: `.c` compiles cleanly, exports
   exactly one function, no undefined externs.
4. **Session dispatch**: static name → 6-byte stub; extended name
   → 33-byte stub; unknown → None; dedup across calls.
5. **Header drift guard**: every extern in `azurik_kernel_extend.h`
   has a catalogue entry (prevents unreachable declarations).

Run: `pytest tests/test_d1_extend.py -v`.

---

## 7. Adding a new extended import

A 5-minute workflow:

1. Identify the function name + ordinal from a canonical reference
   (OpenXDK's `xboxkrnl.h` + Cxbx-Reloaded's export map).
2. Append a `KernelOrdinal(...)` entry to `EXTENDED_KERNEL_ORDINALS`
   in `xboxkrnl_ordinals.py`.
3. (Optional, recommended) Add the extern declaration to
   `azurik_kernel_extend.h` with its `NTAPI` / `FASTCALL`
   annotation + argument types.
4. Run `pytest tests/test_d1_extend.py` — the drift guard fails
   loudly if steps 2 and 3 disagree.
5. `#include "azurik_kernel_extend.h"` in your shim and call the
   function.  Done.
