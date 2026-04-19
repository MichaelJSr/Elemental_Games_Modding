# Learnings — Azurik reverse engineering knowledge base

Running accumulation of non-obvious findings from reverse-engineering
Azurik.  Check here before diving into a Ghidra session — the answer
may already be written down.

Organisation: one section per system.  Each finding cites the
Ghidra function it came from so you can re-verify.

---

## Player movement

### The shared `3.0` run-multiplier (VA 0x001A25BC) is read from 45 sites

The `.rdata` float at VA `0x001A25BC` holds the value `3.0`.  It's
the FMUL factor the running-flag branch of `FUN_00084940` uses to
boost the player's magnitude.  **But**: the same constant is read
from 44 other sites across the XBE (audio mixing, collision, AI,
unrelated physics) — touching the shared constant would affect
every reader.

✅ **Learning**: when patching a constant you think is player-
specific, always grep for every xref.  If the constant is shared,
use the C1-style redirect (rewrite the instruction at the call site
to reference a freshly-injected per-game constant) instead of
mutating the shared value.

Reference: `azurik_mod/patches/player_physics/__init__.py::apply_player_speed`.

### `CritterData.walkSpeed` and `runSpeed` are dead data

The `critters_critter_data` keyed-table has `walkSpeed` and
`runSpeed` columns.  Both **look** like they drive player movement,
but `FUN_00049480` (which populates `CritterData` from config) never
reads those columns — they're not in the column-name table at all.
The runtime `base_speed` the movement code reads from
`CritterData[+0x40]` is defaulted to `1.0` at init time and never
changed by config.

✅ **Learning**: in keyed-table-backed config, a column existing
in the `.xbr` isn't enough — verify the populating function actually
references it via `FUN_000D1420("<key>")`.  Dead columns are common.

Reference: decomp of `FUN_00049480` around the `piVar9[0xE]` /
`piVar9[0x10]` writes (they come from animation-speed keys, not
walk/run speed).

### FUN_00085F50 is the walking state; FUN_00088490 / FUN_00087F80 are climbing

Four functions call into `FUN_00085F50` — the velocity-from-magnitude
computation.  Two are quickly identifiable by their embedded sound-
path strings:

| Function       | Identifying strings                          | Purpose          |
|----------------|----------------------------------------------|------------------|
| FUN_00085F50   | `fx/sound/player/walkl`, `fx/sound/player/walkr` | Walking / running |
| FUN_00087F80   | `fx/sound/player/climb[u/d/l/r]`             | Climbing         |
| FUN_00088490   | `PTR_s_m_grab_001a9c60`                       | Grabbing ledges  |
| FUN_0008CCC0   | State-machine dispatch (calls via case 0 / 6) | Per-frame switcher |

✅ **Learning**: embedded sound-path or animation-ref strings are
the fastest way to label a movement-state function.

---

## Boot state machine

### Boot state is `DAT_001BF61C`, stepped by `FUN_0005F620`

The game has an 8-state boot state machine:

| State | Name                      | Meaning                              |
|-------|---------------------------|--------------------------------------|
| 0     | BOOT_STATE_INIT           | initial dispatch / resource loading  |
| 1     | BOOT_STATE_PLAY_LOGO      | play AdreniumLogo.bik                |
| 2     | BOOT_STATE_POLL_LOGO      | polling the logo movie               |
| 3     | BOOT_STATE_PLAY_PROPHECY  | play prophecy.bik                    |
| 4     | BOOT_STATE_POLL_PROPHECY  | polling prophecy                     |
| 5     | BOOT_STATE_FADE_IN        | post-movie transition                |
| 6     | BOOT_STATE_MENU_ENTER     | enter the main menu                  |
| 7     | BOOT_STATE_MENU           | main menu active                     |
| 8     | BOOT_STATE_LOAD_SAVE      | save-selection / load flow           |
| 9     | BOOT_STATE_INGAME         | in-game: engine update loop runs here |

`FUN_0005F620` is the state-dispatch function.  Writing directly to
`DAT_001BF61C` from a shim lets you skip forward (e.g. straight to
`BOOT_STATE_MENU`); backward jumps work but may produce visible
glitches.

### `play_movie_fn` returns `AL` to signal "enter poll state"

`FUN_00018980` (`play_movie_fn`) is the movie starter.  Its `AL`
return value tells the boot state machine what to do next:

- `AL == 1`: movie loaded, state machine advances to POLL_* to
  drive `poll_movie` per frame.
- `AL == 0`: movie didn't start, state machine skips ahead.

The `qol_skip_logo` shim exploits this: it returns `AL == 0` from
a naked `XOR AL, AL; RET 8` so the game believes the logo loaded
AND ended, skipping straight to the next state.

✅ **Learning**: before replacing any state-machine function, map
out its **return-value contract** — in this game, the boot state
machine is driven entirely by `AL` returns, and getting one wrong
produces a black screen with no error.

Reference: `azurik_mod/patches/qol_skip_logo/shim.c` — the five-line fix that replaced
the original 10-NOP byte patch.

---

## Simulation tick rate

### The 1/30 s constant is `0x3D088889` in .rdata

The per-tick delta is 1/30 second, stored as IEEE 754 float
`0x3D088889` in `.rdata`.  Multiple sites read it; the FPS-unlock
patch does NOT change this constant — it changes the CAP on how
many simulation steps run per render frame.

### FPS unlock operates at VA 0x059AFD + 0x059B37

Two sites control the sim-per-frame cap:
- `0x059AFD`: `CMP ESI, 0x2` → `CMP ESI, 0x4` (cap 2 → 4 steps)
- `0x059B37`: `PUSH 0x2` → `PUSH 0x4` (catch-up delta)

Setting the cap to 2 caused BSODs on death transitions (D3D push
buffer corruption).  Cap 4 is known-stable.  Don't go higher
without xemu testing — larger caps can produce similar corruption
on transition-heavy frames.

Reference: `azurik_mod/patches/fps_unlock/__init__.py`.

---

## Gravity

### The master gravity constant is a single `.rdata` float at VA 0x001980A8

Gravity value is `9.8` as a 32-bit float at VA `0x001980A8`.  Used
by `FUN_00085700` as `velocity.z -= gravity * dt`.

Unlike the run-multiplier, **this constant has only one effective
reader for player physics**, so mutating it in place (via
`ParametricPatch`) is safe.  Side effects on non-player falling
entities are acceptable — the user sees gravity apply uniformly.

Reference: `azurik_mod/patches/player_physics/__init__.py::GRAVITY_PATCH`.

---

## Config / keyed tables

### `config.xbr` uses a custom keyed-table format

Each named table has:
1. A column-name row (string tokens).
2. Rows of data, each cell typed by the column name's `.n` / `.s` /
   `.f` / `.b` suffix.

Lookup at runtime is via `FUN_000D1420("<key>")` for rows and
`FUN_000D1520("<key>")` for cells.  Both are `__thiscall` with the
table pointer in ECX — clang supports them with
`__attribute__((thiscall))`, but the ergonomics are tricky because
the first arg is register-passed.

Currently deferred: exposing these as vanilla functions.  The
`azurik_mod/config/keyed_tables.py` reader can be used offline
instead.

### Not every `.xbr` cell is referenced

See "`CritterData.walkSpeed` and `runSpeed` are dead data" above.
More broadly: `.xbr` tables often have historical columns from
editor workflows that the shipped engine ignores.  ALWAYS trace the
populating function before trusting a column.

---

## Kernel imports

### 151 kernel functions imported; thunk table at VA 0x0018F3A0

The XBE's kernel thunk table starts at virtual address `0x0018F3A0`
(after XOR-decrypting the image-header field at file offset `0x158`
with the retail key `0x5B6D40B6`).  151 four-byte slots, null-
terminated at `0x0018F5FC`.  Each slot's high bit is set, low 16
bits give the xboxkrnl export ordinal.

### No trailing slack — extending the table is hard, but D1-extend sidesteps it

The byte at `0x0018F600` (immediately after the null terminator) is
the start of the library-version table (`b1 ae cc 3b` = a
timestamp).  Can't append new thunks without moving or overwriting
that data.

D1-extend gets around this entirely via a **runtime export
resolver**: shims that need a non-imported xboxkrnl function get a
stub that walks xboxkrnl.exe's PE export table at the fixed retail
base `0x80010000` on first call and caches the result inline.
Zero XBE header surgery; zero call-site rewriting.  See
[`docs/D1_EXTEND.md`](D1_EXTEND.md).

### Xbox retail kernel is mapped at VA 0x80010000

The retail Xbox kernel (`xboxkrnl.exe`) is always loaded at
`0x80010000`.  Its PE image header lives at that base; `e_lfanew`
at `+0x3C` gives the PE header offset; the data-directory entry 0
(EXPORT) gives the RVA of the export table.  This layout is
stable across every retail kernel revision and every retail game —
the resolver hardcodes `0x80010000` without concern.

Debug and Chihiro kernels use different bases; the shim platform
targets retail only.

Reference: `shims/shared/xboxkrnl_resolver.c`.

### Each static import is called via `FF 15 <thunk_va>` (6-byte indirect)

The game's own kernel calls use `FF 15 <thunk_va>` — a 6-byte
indirect jump through the thunk slot.  Our D1 path reproduces this
exactly: we generate the same 6-byte stub in the shim landing
region and resolve the shim's `CALL _Foo@N` REL32 to the stub.

### D1-extend stubs are 33 bytes with inline cache

For imports NOT in Azurik's static 151, the D1-extend resolving
stub is 33 bytes (27 bytes of code + 4-byte cache slot + 2-byte
`JMP EAX` tail).  First call: `CALL xboxkrnl_resolve_by_ordinal` +
cache + `JMP EAX`.  Subsequent calls: `MOV EAX,[cache]; TEST
EAX,EAX; JNZ tail; JMP EAX` — three instructions, cache-hot after
the first call.  Same API surface to shim authors as D1; the
dispatch in `shim_session.stub_for_kernel_symbol` picks the path
automatically.

### Data exports exist alongside functions

Some kernel "imports" aren't functions at all — they're data:

- `ExEventObjectType`, `ExMutantObjectType`, `ExSemaphoreObjectType`,
  `ExTimerObjectType`, `PsThreadObjectType`: `POBJECT_TYPE` pointers
  used by `ObReferenceObjectByHandle` type checks.
- `XboxHardwareInfo`: DWORD with hardware revision flags.
- `XboxHDKey`, `XboxSignatureKey`: 16-byte keys.
- `KeTickCount`, `KeTimeIncrement`: DWORDs readable directly.
- `LaunchDataPage`: pointer to a page preserved across title launches.

Shims that use them must read via `&Name`, not `Name(...)`.

---

## XBE structure

### Azurik has 23 sections; `.text` is the only executable R-X one

From `parse_xbe_sections`:
```
headers  .text  BINK  BINK32  BINK32A  BINK16  BINK4444  BINK5551
BINK16MX  BINK16X2  BINK16M  BINK32MX  BINK32X2  BINK32M
D3D  DSOUND  XGRPH  D3DX  XPP  .rdata  .data  BINKDATA  DOLBY
$$XTIMAGE
```

`D3D`, `DSOUND`, `XGRPH`, `D3DX`, `DOLBY` are RWX statically-linked
library code.  `.text` is the game's own code.  `.rdata` holds
read-only constants + the kernel thunk table.

### `.text` has a 16-byte VA gap before BINK

`.text` runs `0x11000..0x1001D0`; BINK starts at `0x1001E0`.  The
16-byte gap is our "small shim" landing zone (used by `skip_logo`,
the walk/run-speed injected floats, and kernel-import stubs).

Larger shims trigger `append_xbe_section`, which adds a new
executable `SHIMS` section at EOF and shifts the XBE's header-pool
content forward by 56 bytes (the size of one section-header entry).

### Header growth: section-header array is contiguous, pointers auto-fixup

When `append_xbe_section` adds a section, it:
1. Shifts all bytes from `end-of-section-header-array` to
   `size_of_headers` forward by 56.
2. Re-writes every pointer in the image header that now points into
   shifted data (debug-pathname-addr, cert-addr, each section's
   name_addr and head/tail refs).
3. Writes the new section-header entry into the vacated 56 bytes.
4. Updates `num_sections`, `size_of_headers`, `size_of_image`.

Azurik's image has ~880 bytes between end-of-array and
`size_of_headers`, so the 56-byte growth comfortably fits.  **If
Azurik ever ships an XBE with tighter headers, this room calculation
needs redoing.**

Reference: `azurik_mod/patching/xbe.py::append_xbe_section`.

---

## COFF + layout

### Auxiliary symbol records must stay in the symbol list

PE-COFF's symbol table has aux records (size-of-function info, etc.)
interleaved with primary symbols.  Relocation entries index into the
RAW symbol stream, aux records included.

✅ **Learning**: preserve aux records as placeholder `CoffSymbol`
entries (with empty name) so the symbol-index arithmetic in
relocation entries stays correct.  Eliding them breaks REL32
resolution in non-obvious ways.

### `extern_resolver` is the extension point

`layout_coff` takes an optional `extern_resolver: Callable[[str],
int | None]`.  The resolution order for undefined externals:

1. `vanilla_symbols` dict (A3).
2. `extern_resolver` callback (D1 + E).
3. Raise `ValueError` with a helpful message.

Returning `None` from the resolver means "not mine — keep asking".
The `ShimLayoutSession.make_extern_resolver` closure implements the
typical "shared libraries first, then kernel stubs" order.

---

## Tooling / ecosystem

### The OpenXDK xboxkrnl.h is a readable source of truth

`xbox-includes/include/xboxkrnl.h` is the OpenXDK-derived header
with 304 kernel-function declarations.  131 of Azurik's 151
imports have matching declarations there; the remaining 20 are
data exports or fastcall exceptions hand-written in
`scripts/gen_kernel_hdr.py`.

When adding kernel imports via D1-extend, always cross-reference
OpenXDK for the signature before inventing one.

### Xemu is the reference emulator

Test on xemu (`/Users/.../xemu-macos/`) — it's strict enough that
shim bugs cause immediate crashes or black screens, which is great
for testing.  Do NOT trust "it runs" in other emulators; some are
lenient about stack imbalance (they paper over calling-convention
bugs that xemu would catch).

### There are THREE .command launchers / the GUI is the main frontend

`Launch Azurik Mod Tools.command` (macOS/Linux) and the .bat file
(Windows) are the user-facing entrypoints.  They drop into the
Tkinter GUI (`gui/app.py`).  The GUI wraps the `azurik_mod` CLI.

---

## Historical bugs worth remembering

### The `qol_skip_logo` black-screen hang

Symptom: after applying the patch, game sits on a black screen
forever.

Root cause: The original byte-patch overwrote the whole `CALL
play_movie_fn` instruction with NOPs.  `play_movie_fn` is
`__stdcall` with 8 bytes of args — NOPping the CALL leaks 8 bytes
of stack per call AND leaves `AL` undefined.  The boot state
machine read garbage `AL`, interpreted it as "movie playing, stay
in poll state", and never advanced.

Fix: the C shim `XOR AL, AL; RET 8` preserves the stdcall ABI and
returns `AL == 0` explicitly so the state machine advances.

✅ **Learning**: never NOP a CALL to a stdcall function without
also adding the matching `ADD ESP, N` cleanup and setting the
expected return register.

### The player-speed dead-data pivot (C1)

Symptom: sliders had no effect on walk/run speed despite clear
writes to `config.xbr`.

Root cause: `critters_critter_data.walkSpeed` / `runSpeed` are dead
columns.  The effective `base_speed` reader at VA `0x85F65` reads
from a struct slot populated by a different code path.

Fix: inject two per-game floats into the XBE and rewrite the FLD
at `0x85F65` and the FMUL at `0x849E4` to reference those floats
directly.  Requires dynamic whitelisting (`dynamic_whitelist_from_xbe`)
because the injected-float VAs aren't known until apply time.

✅ **Learning**: run the `player-speed 2.0× walk` test with xemu
before declaring victory.  A config patch that writes the right
bytes but changes nothing in gameplay will pass unit tests silently.

### UnboundLocalError in `cmd_randomize_full`

Symptom: `UnboundLocalError: local variable 'xbe_path' referenced
before assignment` when only the player-physics pack was enabled.

Root cause: `xbe_path` was defined inside `if needs_xbe:`, but the
player-speed step (step 7b) referenced it unconditionally.  When
ONLY player-speed was on, `needs_xbe` was False and the block was
skipped.

Fix: define `xbe_path` up front, fold player-speed into the main
XBE block, and include the speed scales in the `needs_xbe`
calculation.

✅ **Learning**: when a variable is defined in a conditional block,
audit EVERY later reference — especially in long pipelines where
sections were refactored independently.

---

## VAs vs file offsets — the player-character trap

Spotted during a post-reorganisation header audit:
``AZURIK_PLAYER_CHAR_NAME_VA`` was set to ``0x001976C8`` and labelled
as a VA in ``azurik.h``, but it's actually the **file offset** of
the ``"garret4\0d:\\0"`` string in ``default.xbe``.  The real VA of
that string is ``0x0019EA68`` (``.rdata``).

The bug went undetected for two reasons:

1. The runtime Python code (``_player_character.py``) indexes
   ``xbe_data[PLAYER_CHAR_OFFSET:...]`` directly — it uses the value
   as a file offset, which is what it actually is, so the patch
   worked.
2. No shim had yet tried to reference the constant through a DIR32
   relocation — which would have resolved to the **wrong memory
   address** at runtime and read garbage ``.rdata`` bytes.

Fix (current): ``azurik.h`` now exposes both spellings —
``AZURIK_PLAYER_CHAR_NAME_VA = 0x0019EA68`` (real VA for shim use)
and ``AZURIK_PLAYER_CHAR_NAME_FILE_OFF = 0x001976C8`` (what the Python
code expects).  Any shim that references this anchor via ``DIR32`` now
gets the correct runtime address.

General rule: anything named ``_VA`` should survive ``va_to_file(va)``
without changing meaning.  If the raw value passes unchanged into
``xbe_data[raw_value:]`` indexing, it's a file offset and should be
named accordingly.

## Historical: pre-reorganisation layout

The repo was reorganized into folder-per-feature in an earlier
refactor.  If you're reading a commit before that reorg, you'll
see:

| Old path                                      | New path                                                |
|-----------------------------------------------|---------------------------------------------------------|
| `azurik_mod/patches/fps_unlock.py`            | `azurik_mod/patches/fps_unlock/__init__.py`             |
| `azurik_mod/patches/player_physics.py`        | `azurik_mod/patches/player_physics/__init__.py`         |
| `azurik_mod/patches/qol.py` (4 packs)         | four folders: `qol_gem_popups/`, `qol_other_popups/`, `qol_pickup_anims/`, `qol_skip_logo/` |
| `shims/src/skip_logo.c`                       | `azurik_mod/patches/qol_skip_logo/shim.c`               |
| `shims/src/_*.c` (test fixtures)              | `shims/fixtures/_*.c`                                   |
| Per-pack `AZURIK_SKIP_LOGO_LEGACY=1` env var  | Single `AZURIK_NO_SHIMS=1`                              |
| Per-pack `apply_*_patch(xbe_data, ...)`       | Unified `apply_pack(pack, xbe_data, params)`            |

Shim `.o` files are keyed on **pack name** now, not source stem,
so `shims/build/qol_skip_logo.o` (not `skip_logo.o`).  Two features
whose source files both happen to be called `shim.c` can't collide
in the shared build cache.

## ControllerState struct (XInput polling) — pinned 2026-04-18

Per-player gamepad state lives at `DAT_0037BE98 + player_idx * 0x54`
(up to 4 players).  Populated every frame by the XInput polling loop
`FUN_000a2df0 → FUN_000a2880` which calls `XInputGetState` and
normalises the raw XInput fields into floats:

| Offset | Type | Field |
|:--|:--|:--|
| 0x00 | f32 | left_stick_x (sThumbLX normalised to [-1, 1]) |
| 0x04 | f32 | left_stick_y |
| 0x08 | f32 | right_stick_x |
| 0x0C | f32 | right_stick_y |
| 0x10 | f32 | dpad_y (-1 / 0 / +1) |
| 0x14 | f32 | dpad_x (-1 / 0 / +1) |
| 0x18..0x34 | f32 × 8 | button_a, button_b, button_x, button_y, button_black, button_white, trigger_left, trigger_right (analog 0..1) |
| 0x38 | f32 | stick_left_click (digital 0 / 1) |
| 0x3C | f32 | stick_right_click |
| 0x40 | f32 | start_button |
| 0x44 | f32 | back_button |
| 0x48..0x53 | u8 × 12 | edge_state[] — latches that the polling loop clears when the corresponding button returns to 0.0, so the engine can implement "consume rising edge once per press" |

Active-player index at `DAT_001A7AE4` (0..3, or 4 = "no controller").
Stick dead zone is raw-unit ±12000 of centre.  Analog-button dead zone is raw-unit 30.

Exposed in `azurik.h` as `ControllerState` with `_Static_assert`s
pinning every late field.  Compile-time regression guards in
`tests/test_shim_authoring.py::test_controller_state_fields_resolve_to_expected_offsets`.

Reference: `FUN_000a2880` decomp (the XInput-write side of the
polling loop) — each `DAT_0037be{9c,a0,a4,a8,ac,b0...}` target maps
1:1 to a struct field in this table.

## Vanilla-function exposure — __fastcall edge cases

When adding functions to `vanilla_symbols.py`, `__fastcall` works
cleanly in clang as long as the function TRULY is fastcall.  The
callers' register-setup pattern is the authoritative signal:

- `MOV ECX, <arg1>; XOR/MOV EDX, <arg2>; CALL` → __fastcall, 2
  register args + optional stack args.
- `PUSH ...; PUSH ...; MOV ECX, this; CALL` → __thiscall, 1
  register arg (ECX=this) + N stack args.  **clang's
  `__attribute__((thiscall))` works on i386-pc-win32 but emits
  ``@name@N`` mangling that doesn't match what Ghidra / vanilla
  Azurik emitted** — you need a naked-asm wrapper that shuffles
  registers before the call.  Deferred.

Working example: `FUN_0004B510` (entity_lookup) — both callers
confirmed pure __fastcall (ECX=name, EDX=fallback).  Now registered
at `entity_lookup@8`.

### Handling MSVC-RVO ABIs (ECX + EDX + EAX + ESI + stack float)

`FUN_00085700` (gravity integration) is the poster child for an
ABI clang can't express directly:

- ECX = config (fastcall arg 1)
- EDX = velocity pointer (fastcall arg 2)
- EAX = output struct pointer (MSVC RVO — implicit)
- ESI = caller-provided entity context (callee-saved, but the
  vanilla function relies on the caller having set it)
- `[ESP+4]` = float gravity_dt_product (callee pops via RET 4)

Solution pattern, now shipping in `shims/shared/gravity_integrate.c`:

1. **Register the vanilla with a LIE**.  In `vanilla_symbols.py`,
   declare the function as plain `fastcall(8)` — just ECX+EDX.
   Mangled name becomes `@name@8`.  Clang's `CALL @name@8`
   resolves via the normal REL32 layout path.
2. **Write a C wrapper that uses inline asm** to set up the
   extra registers right before the CALL.  Key constraint:
   every register load + the CALL must live inside ONE atomic
   `__asm__ volatile` block — clang can't reorder anything
   inside a single asm block, so EAX survives to the CALL.
3. **Clobber list declares every touched register** (`"eax",
   "ecx", "edx", "esi"`) so clang's allocator saves/restores
   as needed around the block.
4. **Satisfy `__fltused` locally** via an asm-label override:
   ``int __fltused __asm__("__fltused") = 0;`` — stops clang
   emitting an undefined external for the float linker marker.

The wrapper exposes a clean `stdcall(N)` C API to shim authors
who just call it like any other function.  Tested end-to-end in
`tests/test_gravity_wrapper.py`.

This pattern generalises to any vanilla function with "weird"
register setup (thiscall, custom calling conventions, implicit
register inputs).  When adding a new one, copy
`shims/shared/gravity_integrate.c` and adjust the register
constraints.

## hourglass.xbr + fx.xbr — data-file scope + XBE hooks

Cross-referenced findings from opening the two data files in
Ghidra alongside ``default.xbe``.  Both are **DATA-only**
(Ghidra won't find functions in them directly), but each has
specific code paths in the XBE that reference their contents.

### hourglass.xbr — UI loading spinner, NOT a timing resource

Despite the suggestive name, ``hourglass.xbr`` is just **sprite
geometry for the UI hourglass icon** that appears during loading
screens.  37 KB total:

- 20 ``surf`` entries, 1,152 bytes each.  Each is one frame of
  the spinning hourglass animation.
- No timing / scheduler data.  The 60 FPS patch does not interact
  with this file.

Source-path leak at VA ``0x00198E93`` confirms the module
identity: ``C:\Elemental\src\mud\hourglass.cpp``.  The loader
path pushes ``"interface/hourglass/..."`` at VA ``0x000D0F08``
(inside ``FUN_000D0EE0``), which is the only in-XBE site that
references the data file.

**Takeaway**: nothing actionable for modding unless someone
wants to replace the loading spinner graphic.  No action taken.

### fx.xbr — Maya-exported particle-system effect library

36.5 MB visual-effects library.  3,572 TOC entries with 113
distinct effect graphs (each has matching ``node``, ``ndbg``,
``sprv``, ``gshd`` sections).  Built from Maya source files
visible in the blob as paths like
``c:\Elemental\gamedata\fx\damage\fx_acid_2.ma``.

Effect graph shape (from the string-table dump):

- **Named timers**: ``AcidHit_timer``, ``EarthHit_timer``,
  ``fx_magic_timer`` — per-effect countdown / max-value
  accumulators.
- **Particle-system nodes**: Maya standard names
  (``pEmitterTop``, ``pRendererShape``, ``pSystem``, ``pSink``,
  ``pAccelerator``) preserved in the runtime graph.
- **Lifecycle**: ``Unload_effectControl`` nodes for cleanup,
  ``effectOrigin`` / ``effectControl`` for graph roots.

Source-path leak at VA ``0x0019DE34`` confirms the module
identity: ``C:\Elemental\src\mud\effectGraph.cpp``.

**XBE hooks** into the effect system (found by grepping .text
for pushed string-VAs):

- **``FUN_00066830``** — "find effect node by name".  Takes a
  ``this`` pointer in EAX (!) and a name on the stack; returns
  the matching node or NULL.  Called from 3 sites referencing
  ``fx_magic_timer`` (VA ``0x19C1AC``).  Unusual ABI (EAX-this
  instead of ECX-this) — would need a gravity-style inline-asm
  wrapper to expose via ``vanilla_symbols.py``.  Not currently
  exposed — no shim has asked for effect playback yet.
- **``FUN_00083000``** — "update effect timer max" method.
  Reads the magic-timer node via ``FUN_00066830``, compares a
  float argument against the stored max, updates if greater +
  sets a dirty flag.  Called from a dispatch table (VTABLE-
  style) at VA ``0x19C174``.

**For 60 FPS investigations**: no frame-rate-dependent pattern
found in the callers examined.  The effect timers receive
``dt``-scaled floats from their callers (not raw frame counts),
so the 60 FPS patch's simulation cap should not break effect
timing.  If future testing reveals visual-effect speed drift at
60 FPS, re-examine the effect-update dispatcher at
``0x830E0..0x832A1`` for the specific ``dt`` multiplier.

**Takeaway**: effect-by-name lookup is a potential future
vanilla-symbol addition (wraps around the Maya-name graph for
shim authors who want to trigger specific effects).  Deferred
until a concrete shim needs it; the inline-asm wrapper cost
(à la gravity) isn't worth paying speculatively.

### fx.xbr wave codec — what's decoded, what isn't (April 2026)

``fx.xbr`` contains **700** ``wave`` TOC entries.  Post-April-2026
they split into four buckets:

- **103 xbox-adpcm** — entries whose payload starts with the
  20-byte header ``[sample_rate u32][sample_count u32]
  [format_magic u32][reserved u32][reserved u32]``.  The
  ``format_magic`` dword decomposes byte-for-byte as
  ``channels = byte[0]``, ``bits_per_sample = byte[1]``,
  ``codec_id = byte[3]``; 97 of the 100 header-carrying entries
  use ``0x01000401`` = mono, 4-bit, codec 1 (Xbox ADPCM).  The
  ``audio dump`` tool wraps these in RIFF/WAVE containers using
  ``WAVE_FORMAT_XBOX_ADPCM`` (0x0069).  vgmstream / Audacity /
  ffmpeg can play them directly.
- **448 likely-audio** — high-entropy payloads with NO recognisable
  header.  The exact codec is not yet reversed.  What we've
  ruled out:
  * Raw 16-bit / 8-bit PCM — mean ``|Δ|`` between adjacent int16
    samples ≈ 30 000 (near-uniform-random).  Real PCM audio
    runs ``|Δ| ≲ 3 000``.
  * Headerless IMA ADPCM with either ``(predictor=0, step=0)``
    start or first-4-bytes-as-header — both produce noise.
  * MS / Xbox ADPCM 36-byte block codec — 0 of 448 sizes divide
    cleanly by 36, 72, 140, or any other standard ADPCM block.
  * Common containers: no RIFF / XMA / XMA2 / xWMA / FSB /
    OggS / BNK magic anywhere in the blob range.

  What we've confirmed about the 448:

  * **~50% of sizes divide by 8**, another 29% by 16 — a
    block-based codec with a non-standard (or varying) block size
    is most plausible.
  * **48 entries are exact duplicates** of earlier entries (same
    first 32 bytes + same total size).  Likely the same SFX
    referenced by multiple ``fx/sound/...`` symbolic names.  The
    ``audio dump`` tool surfaces this via ``duplicate_of`` in the
    manifest + skips redundant preview emission.
  * **Most likely decoder callsite**: ``load_asset_by_fourcc``
    at VA ``0x000A67A0`` → ``wave``-tag branch.  Bisecting from
    there would isolate the decoder.

- **118 likely-animation** — Maya-particle-system curve data.
  First 64 bytes contain 4-byte TOC tags (``gshd`` / ``ndbg`` /
  ``node`` / ``rdms``), no audio codec structure.  Not audio.
- **31 too-small** — payloads under 64 bytes.  Likely terminator
  rows or null-sentinel entries.

**Practical workflow for the likely-audio bucket**: run ``audio
dump --raw-previews`` to emit ``*.preview.wav`` wrappers that
treat each payload as 16-bit mono PCM at 22050 Hz.  The result
is NOT the intended audio (the real codec isn't decoded), but
it's valid RIFF so analysts can open each blob in Audacity to
eyeball the waveform / spectrogram for codec-frame boundaries or
recognisable envelope shapes.  ``build_raw_preview_wav`` in
``azurik_mod/xbe_tools/audio_dump.py`` exposes the same helper
for Python callers.

**Takeaway for future RE**: the decoder callsite is likely
reachable from ``load_asset_by_fourcc``'s wave branch; the best
next step is setting a breakpoint there in xemu, stepping into
the wave-specific handler, and documenting which function
consumes the payload bytes.  Once named, dumping the decoder's
C decompile + comparing to standard ADPCM variants should
surface the variant quickly.  Tooling is ready; the RE
investment is the outstanding cost.

## prefetch-lists.txt — the level manifest goldmine

Azurik's ISO ships with a plain-text **level manifest** at
``prefetch-lists.txt`` (ISO root, not inside ``gamedata/``).  For a
long time the repo hard-coded its own level tables in
``azurik_mod/randomizer/shufflers.py``; we now read the canonical
manifest via ``azurik_mod.assets.prefetch``.

### File format

Stanza-based INI:

    tag=always
    file=index\\index.xbr
    file=hourglass.xbr
    file=%LANGUAGE%.xbr
    file=interface.xbr
    file=config.xbr
    file=fx.xbr
    file=characters.xbr

    tag=a1
    file=A1.xbr
    neighbor=a6
    neighbor=e6

    tag=a6-extra
    file=diskreplace_air.xbr
    file=diskreplchars.xbr

Four stanza shapes:

- ``tag=always`` — **7 globals** the streaming loader keeps
  resident across all levels.  ``%LANGUAGE%`` is substituted at
  runtime to ``english``/``french``/… (see
  ``PrefetchManifest.resolve_language``).
- ``tag=default`` — build-system alias for ``training_room``.
  Not a playable level in its own right; flagged by
  ``PrefetchTag.is_alias``.
- ``tag=<level>`` — **24 playable levels** (a1, a3, a5, a6,
  airship, airship_trans, d1, d2, e2, e5, e6, e7, f1, f2, f3,
  f4, f6, life, town, training_room, w1-w4).
- ``tag=<level>-extra`` — **5 extras packs** (``a6-extra``,
  ``e5-extra``, ``f6-extra``, ``life-extra``, ``w3-extra``)
  containing the per-element ``diskreplace_*.xbr`` bundles.

### Key insight: the graph is DIRECTED, not symmetric

The ``neighbor=`` edges are **streaming-loader prefetch hints**,
not portal declarations.  Out of ~70 edges in the vanilla
manifest, at least 15 are asymmetric:

    a6 → town               # town → life, e2, f1, d1, w1 only
    w1 → airship_trans      # airship_trans has ZERO neighbors
    training_room → w1      # w1 doesn't list training_room back

``airship_trans`` is the extreme case — every airport-adjacent
zone prefetches it, it prefetches nothing.

This matters because **the randomizer can't use this graph as a
reachability solver input**.  For that we still scrape the
portal strings out of the level XBRs themselves.  The manifest
is useful for:

- Authoritative level-set enumeration (24 tags)
- Classification: is this XBR a level, a global, or an alias?
- Integrity check: does every file mentioned here exist on disk?
- Orphan detection: which XBRs on disk aren't in any stanza?
  (Answer: ``selector.xbr`` + ``loc.xbr`` — dev/UI artefacts.)

### Known drift from the randomizer's hardcoded table

``LEVEL_PATHS`` in ``shufflers.py`` ships **22 levels**, missing:

- ``training_room`` — no ``levels/.../training_room`` save-path
  prefix exists (it's bootstrapped through the ``default`` alias).
- ``airship_trans`` — every entry is cutscene-driven; no portal
  strings to rewrite.

Cut content surfaces too: ``f1 → f7`` references a cut level
that has no ``tag=f7`` stanza.  The randomizer already special-
cases this via ``EXCLUDE_TRANSITIONS``.

The ``tests/test_assets_manifest.py::PrefetchVsHardcodedDelta``
test flips red if any other drift appears.

## filelist.txt — the integrity manifest

Sibling file at the ISO root.  DOS-ish format:

    \\
    f <md5> <bytes> a1.xbr
    f <md5> <bytes> a3.xbr
    ...
    d index

    \\index\\
    f <md5> <bytes> index.xbr

``azurik_mod.assets.filelist`` parses it and exposes
``FilelistManifest.verify(iso_root, check_md5=True)`` for
byte-level integrity validation.  Full MD5 scan of the
vanilla 951 MB dump runs in ~1.5 s on an M1 SSD.

Exposed end-to-end through:

    azurik-mod iso-verify <unpacked-iso-dir> [--no-md5] [--graph]

Exit code is non-zero on any integrity mismatch — safe to wire
into CI/pre-build hooks.

### Path-scoping gotcha

``filelist.txt`` declares paths relative to the **``gamedata/``
subdirectory** (its top-level scope line is just ``\\``).  But
the file itself lives at the ISO root, one level up from
``gamedata/``.  ``FilelistManifest._resolve_root`` auto-detects
this mismatch by probing the first three entries against both
candidate roots and using whichever matches more files.  If
Microsoft's ISO layout ever changes this could need extending,
but the heuristic has zero false positives today.

### Extract-pipeline integration

Every ``run_xdvdfs ... unpack`` call in
``azurik_mod/randomizer/commands.py`` is followed by a
``verify_extracted_iso(extract_dir)`` call.  The helper runs a
size-only integrity scan (no MD5 — too expensive on the hot
path) and prints a warning block with up to 20 mismatches if
anything looks wrong.  It never raises, so a corrupted
extraction still produces something usable for diagnosis, but
the user gets a loud heads-up pointing them at
``azurik-mod iso-verify`` for the full MD5 audit.

Size-only scan cost: ~3 ms for 42 entries on an M1 SSD, vs
~1.5 s for the full MD5 pass.

## selector.xbr — the developer level-select hub

Discovered during the filelist/prefetch cross-check: 2 MB level
XBR that IS on disk, IS referenced by the XBE, but is NOT in
``prefetch-lists.txt``.

### What it is

``selector.xbr`` is a legitimate, playable in-game level built on
the same layout as every other level (``node``, ``levl``, ``surf``,
``rdms``, etc.).  Its ``node`` section carries **35 portal
strings** that between them reach every live level in the game,
plus direct cutscene triggers:

- 22 regular level portals — ``levels/fire/f1`` through
  ``levels/water/w4``, ``levels/life``, ``levels/town``, etc.
- 1 self-reference (``levels/selector``)
- 1 portal to a **cut level** (``levels/earth/e4``) — the only
  on-disk reference to this level anywhere
- 10 movie-scene triggers (``movies/scenes/prophecy``,
  ``movies/scenes/training1``, …``disksdestroyed``,
  ``catalisks``, ``airship2``, ``death1``, ``deathmeeting2``,
  ``disks_restoredall``, ``newdeath``)

So it's a developer cheat menu — loading this level gives you
single-click access to every level + cutscene.

### How to activate it

The XBE has 4 ``.text`` callsites that push the VA of the
``"levels/selector"`` string at ``VA 0x1A1E3C``:

- ``VA 0x12C56``  — probably init/boot-path
- ``VA 0x52FA7, 0x533E3, 0x53400`` — inside ``FUN_00052F50``,
  a conditional load gated on a boot-flag read from BSS
  ``VA 0x001BCDD8``.  Disassembly shows:

```
  mov  esi, [0x001BCDD8]    ; read debug-mode flag
  cmp  esi, -1
  jnz  +0x05
  mov  esi, 0x3             ; default when flag unset
  mov  ebp, 0x1A1E3C        ; "levels/selector" string
```

So a ``qol_enable_dev_menu`` shim could force-enable the cheat
menu by priming ``[0x001BCDD8]`` to a non-``-1`` value during
boot.  Not shipped today — no one has asked for it — but the
plumbing is a ~20-line shim when someone does.

### Why it's a prefetch-manifest orphan

The streaming loader doesn't see it because it's never on a
level's ``neighbor=`` list.  It's loaded directly by
``FUN_00052F50`` which bypasses the prefetch system entirely.
The ``azurik-mod iso-verify`` orphan-detector lists it
(alongside ``loc.xbr``) as a manifest orphan — both are
legitimate, both are unused by the normal game flow.

### Cut-level discoveries

Two cut levels are now documented as ``KNOWN_CUT_LEVELS``:

- ``f7`` — referenced only by ``f1``'s ``neighbor=`` list in
  ``prefetch-lists.txt``.  The randomizer's
  ``EXCLUDE_TRANSITIONS`` already knows about it.
- ``e4`` — referenced only by ``selector.xbr``'s portal list.
  No XBE code paths reference it.

Both are useful flags for a future "randomizer finds all known
dead portals" audit pass.

## index.xbr — the global asset-path index

Second orphan-looking file that's actually in the ``always``
stanza of ``prefetch-lists.txt`` — the streaming loader keeps
it resident throughout the game.

### Structure

168 KB file with exactly ONE TOC entry tagged ``indx``.  Payload
contains ~3,100 unique name strings (parser-extracted) and the
4-char type tags the game uses to disambiguate asset kinds:

| tag    | purpose                                      |
|--------|----------------------------------------------|
| ``surf`` | surface / material reference               |
| ``wave`` | audio or animation wave resource           |
| ``banm`` | bone animation (``b``one-``anm``)           |
| ``node`` | scene-graph node                           |
| ``body`` | character body mesh                        |
| ``gems`` | gem-pickup definition                      |
| ``indx`` | index entry self-tag                       |

Each name string is followed by a single discriminator byte
(``!``, ``"``, ``#``, …) which likely encodes an asset-version
or sub-type index.

### What it's used for (inferred)

Based on the tag distribution + prefetch-manifest placement:

- Global asset **directory** — maps every named asset (e.g.
  ``characters/garret4/body``) to a lookup record the engine
  uses to locate the data inside the other XBRs.
- **Always loaded** — so any level, any config value, any
  character spec can reference an asset by name without a
  chain of file-open calls.

We haven't fully decoded the index-entry record layout.  It's
not blocking anything today: level / character / effect mods go
through their native XBRs (``config.xbr``, ``characters.xbr``,
level files) rather than through this index.  If a future mod
wants to add NEW assets (not just modify existing ones), the
index will need to be extended too — tracked as a future
project in docs/ONBOARDING.md.

### Why we don't need to parse it further today

The ``config.xbr``-driven modding workflow we've built operates
entirely on keyed-table entries that ALREADY exist in the game.
We rename gems, swap power-ups, tweak drop tables — all
in-place edits to records the engine already indexes.  The only
time we'd need the index is to add *new* entity types, which
isn't on any current roadmap.

### Full record layout (April 2026 pass)

After the initial survey, a second RE pass decoded the actual
binary format:

**File layout:**

```
0x0000..0x0008  xobx magic + version
0x0040..0x0050  TOC (1 entry): indx tag, size 0x2713F
0x1000..0x1010  indx header (16 bytes)
0x1010..0x10000 record table (3071 entries × 20 bytes)
0x10000..EOF    string pool
```

**indx header (16 bytes at file offset 0x1000):**

| offset | field         | vanilla value | notes                         |
|--------|---------------|---------------|-------------------------------|
| +0x00  | count         | 3072          | declared records; 3071 real + 1 sentinel |
| +0x04  | version       | 4             | format version                |
| +0x08  | header_hint   | 24            | role unclear (NOT actual header size = 16) |
| +0x0C  | pool_hint     | 0xEFFC        | role unclear (probably pool size or offset)  |

**Record (20 bytes each):**

| offset | field        | type | notes                            |
|--------|--------------|------|----------------------------------|
| +0     | length       | u32  | string length for off1's string  |
| +4     | off1         | u32  | pool offset — appears to reference a FILE name (e.g. ``characters.xbr``) |
| +8     | fourcc       | char[4] | asset type: ``body``, ``banm``, ``node``, ``surf``, ``wave``, ``levl``, ``tabl``, ``font`` |
| +12    | disc         | u8   | subtype discriminator (0x10..0xFF) |
| +13    | pad          | u8[3] | zero padding                    |
| +16    | off2         | u32  | pool offset — appears to reference an ASSET KEY within the file at off1 |

**Tag distribution across 3071 records:**

- ``surf``: 1099 (surface / material references)
- ``wave``: 816 (audio blobs)
- ``banm``: 712 (bone animations)
- ``node``: 230 (scene-graph nodes)
- ``body``: 160 (character body meshes)
- ``levl``: 32 (level descriptors)
- ``tabl``: 18 (config tables)
- ``font``: 4 (font assets)

**String pool:**

Starts at file offset 0x10000 with:

- 4-byte magic dword: ``0x0001812D`` (role unclear)
- 4-byte tag: ``levl``
- Concatenated NUL-terminated asset paths (``characters.xbr``,
  ``characters/air_elemental/attack_1``, …)

### What remains uncharted

- Exact pool base for ``off1`` vs ``off2`` (they differ and the
  strings don't land cleanly at ``pool_start + offN`` — each
  record seems to carry some unknown prefix offset).
- Semantics of the two trailing header fields.
- Why ``count`` is 3072 when only 3071 entries are valid
  records (the 3072nd overlaps the pool magic).

Both the parser (:mod:`azurik_mod.assets.index_xbr`) and the
tests (``tests/test_index_xbr.py``) pin the decoded portions
and expose the raw fields so a follow-up RE session can
continue from here.

## Shim-system sanity check — April 2026

Cross-referenced the recent discoveries (hourglass + fx +
selector + index + prefetch audits) against the shipped shim
headers and ``vanilla_symbols.py`` registry.  New additions:

**``azurik.h`` VA anchors (4 added, now 20 total):**

- ``AZURIK_DEV_MENU_FLAG_VA`` (0x001BCDD8) — BSS flag that the
  selector.xbr loader reads.  Write non-``-1`` to force-load
  the dev menu.
- ``AZURIK_STR_LEVELS_SELECTOR_VA`` (0x001A1E3C) — string
  ``"levels/selector"``.
- ``AZURIK_STR_LEVELS_TRAINING_VA`` (0x001A1E4C) — string
  ``"levels/training_room"``.
- ``AZURIK_STR_INDEX_XBR_PATH_VA`` (0x0019ADB0) — string
  ``"index\\index.xbr"``.

**``azurik_vanilla.h`` / ``vanilla_symbols.py`` (2 added, now 9 total):**

- ``dev_menu_flag_check`` @ 0x00052F50 — the dispatcher that
  reads ``AZURIK_DEV_MENU_FLAG_VA`` and picks which level to
  load.  Purely documentary — a ``qol_enable_dev_menu`` shim
  won't call it, but referencing the function by name makes
  the one-line DIR32-store shim self-explanatory.
- ``load_asset_by_fourcc`` @ 0x000A67A0 — the index-table
  dispatcher.  Declared with a deliberately-wrong
  ``stdcall(8)`` signature so clang's mangling resolves to
  the right VA; a wrapper (gravity-style inline asm) will
  be needed before a real shim can call it.

**Coverage growth:** 76 → 82 unique Python-side VAs (16 → 20
anchors, 7 → 9 vanilla symbols, 53 patch-site VAs unchanged).

All four new anchors + both new vanilla entries are drift-
guarded by tests: ``tests/test_va_audit.py`` pins the bytes,
``tests/test_vanilla_thunks.py`` pins the header<->registry
equivalence.

## 60 FPS patch re-audit (April 2026)

Second-pass audit of ``fps_unlock`` against every frame-rate-
adjacent constant in ``default.xbe``'s ``.rdata``, motivated by
the fx.xbr record-layout RE + the new ``azurik-mod xbe
find-floats`` tooling.

### Scope

Exhaustive scan of ``.rdata`` for any IEEE 754 constant in the
``1/30``, ``30.0``, ``60.0``, or ``1/60`` neighbourhoods —
plus float64 counterparts for each.  Every hit cross-referenced
against ``FPS_DATA_PATCHED_VAS`` to classify:

- **Patched** ⇒ halved at apply time (no action needed).
- **Unpatched + frame-rate-dependent** ⇒ BUG, needs patching.
- **Unpatched + not frame-rate-dependent** ⇒ needs documenting
  so the regression guard doesn't flip on it.

### Findings

| Category    | Count | Status                                    |
|-------------|-------|-------------------------------------------|
| 1/30 f32    | 29    | 29/29 patched ✅                           |
| 1/30 f64    | 1     | 1/1 patched ✅                             |
| 30.0 f32    | 5     | 3 patched + 2 classified non-rate ✅       |
| 30.0 f64    | 1     | 1/1 patched ✅                             |
| 60.0 f32    | 6     | all 6 classified non-rate ✅               |
| 1/60 f32/64 | 0     | no baked-in "60 FPS assumed" math ✅       |

**Zero missed patches.**  The 2 unpatched 30.0 constants and all
6 of the 60.0 constants are in rendering / UI / threshold code
paths:

- ``0x0019FD98`` — threshold in ``FUN_0003EA00``
  (``if (30.0 < *(float*)(param_2 + 8))``) — speed / angle test,
  not a rate multiplier.
- ``0x001A2524`` — dead data (no .text xrefs).
- All 6 × 60.0 — FOV defaults (``FUN_00054800``) + screen-space
  UI scale math (``FUN_0005AC80`` etc.).  Decoded pattern:
  ``fVar13 = (float10)60.0; fptan(fVar13 * 0.5)`` — the classic
  camera-projection ``tan(fov/2)`` setup.

### fx.xbr-specific audit

The 3 ``fx_magic_timer`` XBE callsites (discovered during the
earlier fx.xbr audit) were re-decompiled:

- **``FUN_00083000``** — update: "if new > max, store".  No dt.
- **``FUN_00083050``** — spawn: reads stored max into new
  effect.  No dt.
- **``FUN_00083230``** — serialise: writes effects to save.
  No dt.

The effect-timer system stores values as *numbers*, not as
frame counts.  60 FPS unlock is SAFE w.r.t. fx.xbr.

### Regression guard

``tests/test_fps_coverage.py`` (new) pins the exact vanilla
counts so any future re-dump of the XBE that introduces a new
frame-rate constant (or drops one) flips red immediately.  The
test has six cases:

1. Every 1/30 f32 is patched
2. Every 1/30 f64 is patched
3. Every 30.0 f32 is patched OR classified as non-rate
4. Every 60.0 f32 is classified as non-rate
5. No 1/60 constants exist (the game is 30 FPS native)
6. Vanilla counts match the audit's ground truth

If (3) or (4) flips red, add the new VA to either the fps_unlock
patch set or the ``_NOT_FRAMERATE_*`` dict (with a Ghidra-xref
note in the comment).  If (1), (2), or (5) flips red, the
discovered constant IS a genuine frame-rate dep that needs
patching.

## Patch categories reorg (April 2026)

Second-pass at the category tab layout:

- **fps_unlock** moved from ``performance`` → ``experimental``.
  The patch triggers a pre-existing D3D push-buffer BSOD on
  player death (engine bug, unrelated to the patch bytes) and
  introduces visual-timing drift in a few subsystems we don't
  statically patch.  An ``experimental`` category signals
  "opt-in, keep a backup ISO" more clearly than ``performance``.
- **``randomize`` category added** — the five shuffle pools
  (``rand_major``, ``rand_keys``, ``rand_gems``, ``rand_barriers``,
  ``rand_connections``) are now first-class ``Feature`` entries
  with ``sites=[]`` + ``apply=noop``.  The Randomize page
  renders them via the same ``PackBrowser`` the Patches page
  uses, and the Patches page automatically grows a "Randomize"
  tab that mirrors the same state.
- The ``performance`` category is now EMPTY (fps_unlock was its
  only resident).  It stays registered so a future performance
  mod can slot into it without touching ``category.py``; the
  GUI hides empty categories from the tab strip.

## What to add here next

Things we haven't pinned down but should when a shim needs them:

- [ ] **Camera projection + FOV**.  Likely a `.rdata` float similar
      to gravity.  Quick win if found.
- [ ] **Player jump impulse**.  Tracked as `C-jump` in SHIMS.md.
- [ ] **`FUN_000d1420` / `FUN_000d1520` (config lookup)** —
      __thiscall; needs naked-asm wrappers to call from shims
      (same pattern as the gravity wrapper — see "Handling
      MSVC-RVO ABIs" above).

**Recently pinned** (as of this pass):

- [x] **Save-file format** — Xbox-standard container + 20-byte
      header decoded; per-level payload decoding deferred.  See
      `docs/SAVE_FORMAT.md` + `azurik_mod.save_format`.
- [x] **`FUN_00085700` (gravity integration)** — inline-asm
      wrapper shipped at `shims/shared/gravity_integrate.c`,
      exposed via `azurik_gravity_integrate()`.
- [x] **Controller input struct** — done, see section above.
- [x] **Drop-table fields in `CritterData`** — `range`, `range_up`,
      `range_down`, `attack_range`, `drop_1..5`, `drop_count_1..5`,
      `drop_chance_1..5` all exposed.  Offsets pinned against
      `FUN_00049480`.
- [x] **`entity_lookup` (FUN_0004b510)** — registered in
      `vanilla_symbols.py` as __fastcall.
