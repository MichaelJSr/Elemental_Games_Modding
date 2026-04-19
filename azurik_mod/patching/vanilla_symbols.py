"""Registry of vanilla Azurik functions that shims may call into.

Phase 2 A3 of the C-shim platform: instead of emitting runtime thunks,
we resolve undefined-external COFF symbols directly to their virtual
addresses in the vanilla XBE.  The shim's compiled REL32 / DIR32
relocation fields are then rewritten to target those VAs during
``layout_coff``.

Each entry pairs:

- A C-side declaration in :file:`shims/include/azurik_vanilla.h`.
- A Python-side (mangled-name -> VA) entry in :data:`VANILLA_SYMBOLS`.

The two MUST stay in sync.  ``tests/test_vanilla_thunks.py`` enforces
this by compiling the header with every declared extern and checking
that every unresolved COFF symbol the compiler emits has a matching
:data:`VANILLA_SYMBOLS` entry with the right mangled name.

Name mangling on i386 PE-COFF (matches what clang
``-target i386-pc-win32`` emits):

- ``__cdecl``   :  ``_name``         (leading underscore)
- ``__stdcall`` :  ``_name@N``       (underscore + ``@`` + decimal arg-byte count)
- ``__fastcall``:  ``@name@N``       (at-sign prefix + ``@N`` suffix)

Most vanilla Azurik functions we'll want to call are ``__stdcall``
(the call sites don't have ``ADD ESP, N`` cleanup, so the callee
pops).  Double-check by looking at the vanilla call site in Ghidra
before adding a function here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VanillaSymbol:
    """One vanilla Azurik function exposed to shims.

    The registry entry is the source of truth for the shim author's
    extern C declaration: if the Python field shows
    ``calling_convention="stdcall"`` and ``arg_bytes=8``, the C
    declaration in :file:`azurik_vanilla.h` must be
    ``__attribute__((stdcall)) T name(A a, B b);`` with ``sizeof(A) +
    sizeof(B) == 8``.
    """
    name: str
    """C-level identifier (no leading underscore, no suffix)."""

    va: int
    """Virtual address in the vanilla Azurik XBE."""

    calling_convention: str = "stdcall"
    """``cdecl`` / ``stdcall`` / ``fastcall``.  Affects the mangled
    PE-COFF symbol name (see module docstring)."""

    arg_bytes: int = 0
    """Total bytes of stack arguments (only meaningful for stdcall /
    fastcall; cdecl leaves the count implicit)."""

    doc: str = ""
    """Free-form note on what the function does and any gotchas the
    shim author needs to know (e.g. 'returns AL = 1 if movie started,
    0 otherwise').  Surfaced in the matching header comment."""

    @property
    def mangled(self) -> str:
        """COFF symbol name emitted by clang for this function.

        clang-i386-pe-win32 mangling cheat sheet:
          cdecl     → ``_name``
          stdcall   → ``_name@N``  (N = stack-arg bytes)
          fastcall  → ``@name@N``  (N = stack-arg bytes, including
                     the two register args ECX+EDX = 8 bytes of
                     "virtual stack" space)
          thiscall  → ``_name``    (NO @N suffix on this platform!
                     The ``this`` pointer in ECX doesn't count for
                     name decoration; stack args are caller-cleaned
                     just like cdecl.  Confirmed empirically by
                     compiling a probe; see ``tests/test_vanilla_thunks``).
        """
        if self.calling_convention == "cdecl":
            return f"_{self.name}"
        if self.calling_convention == "stdcall":
            return f"_{self.name}@{self.arg_bytes}"
        if self.calling_convention == "fastcall":
            return f"@{self.name}@{self.arg_bytes}"
        if self.calling_convention == "thiscall":
            return f"_{self.name}"
        raise ValueError(
            f"unsupported calling convention: {self.calling_convention!r}")


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------
#
# Add new entries carefully:
#
#   1. Confirm the function's VA and calling convention in Ghidra.
#      __stdcall callees do `RET N` (e.g. 0xC2 0x08 0x00 for `ret 8`)
#      and their call sites do NOT have `ADD ESP, N` cleanup.
#      __cdecl callees do `RET` (0xC3) and caller cleans via ADD ESP.
#
#   2. Add the matching C prototype to
#      :file:`shims/include/azurik_vanilla.h` using either
#      ``__attribute__((stdcall))`` or the implicit default for cdecl.
#      For stdcall the `arg_bytes` field MUST equal the sum of all
#      argument sizes on the 4-byte-aligned stack.
#
#   3. Run `pytest tests/test_vanilla_thunks.py` — the drift test
#      refuses to let the header and this registry diverge.

_REGISTRY: dict[str, VanillaSymbol] = {}


def register(sym: VanillaSymbol) -> VanillaSymbol:
    """Add `sym` to the registry, keyed by its mangled COFF name."""
    if sym.mangled in _REGISTRY:
        raise ValueError(
            f"duplicate vanilla-symbol mangled name {sym.mangled!r}; "
            f"existing entry is for {_REGISTRY[sym.mangled].name!r}")
    _REGISTRY[sym.mangled] = sym
    return sym


# ---- Seed entries (add more as shim authors need them) --------------------

register(VanillaSymbol(
    name="play_movie_fn",
    va=0x00018980,
    calling_convention="stdcall",
    arg_bytes=8,
    doc=(
        "Boot-time movie player.  `name` is a path like "
        "'AdreniumLogo.bik'; `flag` is a boolean (1 = prophecy-like, "
        "0 = logo-like).  Returns AL=1 if the movie started (enter "
        "poll state), AL=0 if it didn't (skip to next boot state)."
    ),
))

register(VanillaSymbol(
    name="poll_movie",
    va=0x00018D30,
    calling_convention="stdcall",
    arg_bytes=4,
    doc=(
        "Called once per frame while a movie is playing.  Takes a "
        "float dt.  Returns 0 = still playing, 1 = should-abort, "
        "2 = movie finished."
    ),
))

register(VanillaSymbol(
    name="entity_lookup",
    va=0x0004B510,
    calling_convention="fastcall",
    arg_bytes=8,
    doc=(
        "Looks up an entity descriptor by name.  __fastcall: "
        "name (byte *) in ECX, fallback (int *) in EDX.  Walks the "
        "global entity registry (DAT_0038C1E4..DAT_0038C1E8) "
        "comparing each entry's name against the needle; returns "
        "the matching descriptor pointer in EAX, or 0 if no match "
        "AND the fallback is NULL.  When the fallback is non-null "
        "and the lookup misses, the function registers the "
        "fallback as a new entry and returns it.\n\n"
        "ABI verified from two callers (FUN_000353F0 and "
        "FUN_0003A610) both of which do ``MOV ECX, <name>; "
        "XOR/MOV EDX, <fallback>; CALL`` without any ``ADD ESP, N`` "
        "cleanup afterward — __fastcall with 2 register args.\n\n"
        "Safe to call from shims that need to resolve named "
        "entities (critter descriptors, scripted pickups, etc.)."
    ),
))

register(VanillaSymbol(
    name="boot_state_tick",
    va=0x0005F620,
    calling_convention="stdcall",
    arg_bytes=4,
    doc=(
        "Runs one tick of the boot-state machine.  __stdcall: "
        "dt (float) on stack.  Returns a boolean-ish value in AL "
        "(caller at VA 0x59BA5 does ``TEST AL, AL; JNZ ...`` to "
        "branch on the result).\n\n"
        "This is the state-machine function that ``qol_skip_logo`` "
        "shims into — it decides which movie / logo plays next. "
        "Shims that want to INTERCEPT boot-state transitions "
        "(rather than just skip a single movie) can wrap it: "
        "call the vanilla then post-process the AL return to "
        "force a different transition.\n\n"
        "Multiple ``RET 4`` exits confirm __stdcall with 1 arg. "
        "Return type formally undefined4 in Ghidra, but the only "
        "observed caller treats it as a byte — ``unsigned char`` "
        "is a safe shim-side declaration."
    ),
))

register(VanillaSymbol(
    name="config_name_lookup",
    va=0x000D1420,
    calling_convention="thiscall",
    arg_bytes=4,
    doc=(
        "Look up a named entry in a config table.  __thiscall: "
        "``this`` (the config table object) in ECX, needle "
        "(const char *) on the stack.  Callee does RET 4.\n\n"
        "ABI pinned by: ``MOV EAX, [ECX]`` + ``MOV EDX, [ECX+4]`` "
        "prologue (ECX is ``this``) + stack arg at [ESP+0x14] after "
        "4 register pushes + return addr = first stack arg + "
        "closing ``C2 04`` (RET 4).\n\n"
        "Returns an int (probably row index or entry offset).  "
        "The table is scanned byte-by-byte for a matching name — "
        "O(N) in table entries.  Callable directly from shim C "
        "via ``__attribute__((thiscall))``; no inline-asm wrapper "
        "needed (unlike ``gravity_integrate_raw``) because this "
        "is a pure thiscall without RVO / ESI context magic."
    ),
))

register(VanillaSymbol(
    name="config_cell_value",
    va=0x000D1520,
    calling_convention="cdecl",
    arg_bytes=0,
    doc=(
        "Look up a cell value in a 2-D config grid.  __cdecl: "
        "``(int *grid, int row, int col, double *default_out)`` "
        "on the stack; returns a ``float10`` (80-bit FPU value in "
        "ST(0), which clang treats as a ``double`` return in "
        "ordinary C code).\n\n"
        "ABI pinned by: all 4 args read from [ESP+N] + bounds-check "
        "JLs + fallback INT3 panic + closing ``C3`` (cdecl RET).\n\n"
        "The function does bounds-checking on ``row`` and ``col`` "
        "against the grid's size fields (read at [ECX+8] and "
        "[ECX+0]) and panics via a logging call + INT3 if either "
        "is out of range — shims MUST NOT pass invalid indices."
    ),
))

register(VanillaSymbol(
    name="load_asset_by_fourcc",
    va=0x000A67A0,
    calling_convention="stdcall",
    arg_bytes=8,
    doc=(
        "Look up an asset in the global ``index.xbr`` table.  "
        "__stdcall(8): ``(int fourcc, int flags)`` on the stack, "
        "plus the asset index passed in EAX as an implicit "
        "register argument.  Returns the asset's byte offset "
        "(relative to its containing XBR file) in EAX, or 0 on "
        "miss.\n\n"
        "ABI partially inferred from Ghidra decomp "
        "(FUN_000A67A0): reads asset record via "
        "``piVar3[3] + (in_EAX - piVar3[2]) * 4``, checks "
        "``piVar1[2] == param_1`` (fourcc match).  The EAX-as-"
        "asset-index convention is Watcom-ish and NOT expressible "
        "via a native clang attribute — shim authors should prefer "
        "a call-through wrapper (template deferred until a real "
        "shim needs this).\n\n"
        "Typical fourcc values (little-endian packing):\n"
        "    0x78646E69  'xdni' — indx section (the index table)\n"
        "    0x79646F62  'body' — character body meshes\n"
        "    0x6D6E6162  'banm' — bone animations\n"
        "    0x65646F6E  'node' — scene-graph nodes\n"
        "    0x66727573  'surf' — surface / material data\n"
        "    0x65766177  'wave' — audio blobs\n"
        "    0x6C76656C  'levl' — level descriptors\n\n"
        "Discovered during index.xbr record-layout RE — see "
        "docs/LEARNINGS.md § index.xbr for the full format."
    ),
))

register(VanillaSymbol(
    name="dev_menu_flag_check",
    va=0x00052F50,
    calling_convention="stdcall",
    arg_bytes=8,
    doc=(
        "Boot-time dispatcher that picks which level to load.  "
        "Reads the BSS flag at ``AZURIK_DEV_MENU_FLAG_VA`` "
        "(0x001BCDD8): when the flag is ``-1`` (default), the "
        "function selects the normal opening level.  When the "
        "flag holds any other value, it sets EBP to the "
        "``levels/selector`` string VA (0x001A1E3C) so the "
        "developer cheat hub loads instead.\n\n"
        "Documented as a vanilla symbol so a future "
        "``qol_enable_dev_menu`` shim can reference it by name "
        "rather than by raw VA.  The shim itself only needs a "
        "single DIR32 write to ``AZURIK_DEV_MENU_FLAG_VA`` — no "
        "trampoline required — but having the dispatcher named "
        "makes the one-line write self-documenting.\n\n"
        "See docs/LEARNINGS.md § selector.xbr for the full gate "
        "disassembly."
    ),
))

register(VanillaSymbol(
    name="calculate_save_signature",
    va=0x0005C920,
    calling_convention="thiscall",
    arg_bytes=0,
    doc=(
        "Entry point for Azurik's save-slot sign / verify.  "
        "__thiscall: the save-slot context lives in ECX "
        "(path buffer at [ECX+0x20A], flag byte at [ECX+0x20A]'s "
        "high byte — 0x7A disables signing).\n\n"
        "Flow (from Ghidra decomp):\n"
        "  1. if ([ECX+0x20A] == 0x7A): return    # bypass\n"
        "  2. ctx = XCalculateSignatureBegin(0)   # HMAC-SHA1\n"
        "  3. FUN_0005C4B0(path, ctx)             # hash tree\n"
        "  4. XCalculateSignatureEnd(ctx, sig20)\n"
        "  5. fwrite(sig20, 20, 1, fopen(path+'/signature.sav'))\n\n"
        "Documented here so a future ``qol_skip_save_signature`` "
        "shim (see docs/SAVE_FORMAT.md § 7) can reference the "
        "function by name rather than raw VA.  The hash-tree-walk "
        "is pinned in :mod:`azurik_mod.save_format.signature` "
        "for direct Python use."
    ),
))

register(VanillaSymbol(
    name="xcalculate_signature_begin",
    va=0x000E2BC9,
    calling_convention="stdcall",
    arg_bytes=4,
    doc=(
        "XDK re-export.  ``XCalculateSignatureBegin(flags)`` — "
        "allocates + initialises an HMAC-SHA1 context keyed with "
        "``XboxSignatureKey``.  flags=0 means 'inner HMAC only' "
        "(no per-console HDKey outer layer); Azurik passes 0 "
        "from ``calculate_save_signature``.\n\n"
        "Returns the hash-context pointer in EAX (opaque; pass "
        "to Update / End).  See docs/SAVE_FORMAT.md § 7 for the "
        "save-sign trace."
    ),
))

register(VanillaSymbol(
    name="xcalculate_signature_end",
    va=0x000E2C21,
    calling_convention="stdcall",
    arg_bytes=8,
    doc=(
        "XDK re-export.  ``XCalculateSignatureEnd(ctx, out20)`` — "
        "finalises the HMAC-SHA1 accumulator from Begin/Update "
        "and writes 20 bytes to ``out20``.  When the Begin-time "
        "flags requested the HDKey outer layer, that extra "
        "HMAC-SHA1(HDKey, inner) stage happens here too; "
        "otherwise this is plain HMAC-Final.\n\n"
        "Pair with :data:`xcalculate_signature_begin`."
    ),
))

register(VanillaSymbol(
    name="gravity_integrate_raw",
    va=0x00085700,
    calling_convention="fastcall",
    arg_bytes=8,
    doc=(
        "RAW vanilla gravity-integration routine.  **Do NOT call "
        "this directly from shim C code** — its real ABI uses an "
        "MSVC-RVO extension that no clang calling-convention "
        "attribute can express natively:\n\n"
        "    ECX     = config struct pointer\n"
        "    EDX     = velocity / position ptr\n"
        "    EAX     = result struct pointer (RVO, implicit output)\n"
        "    ESI     = caller-provided player / entity context\n"
        "    [stack] = float gravity_dt (callee pops via RET 4)\n\n"
        "The 'fastcall(8)' signature here is a **deliberate lie** "
        "to clang so it emits ``call @gravity_integrate_raw@8`` — "
        "the REL32 resolves to VA 0x00085700 via this registry.  "
        "The extra EAX / ESI setup happens in the inline-asm "
        "wrapper at ``shims/shared/gravity_integrate.c``; shim "
        "authors include ``azurik_gravity.h`` and call "
        "``azurik_gravity_integrate(...)`` which has a clean "
        "stdcall(20) ABI.\n\n"
        "ABI pinned from the caller at VA 0x860C8 (MOV ECX,EBP / "
        "LEA EDX,[ESP+0x1C] / LEA EAX,[ESP+0xCC] / PUSH <gravity> "
        "/ CALL), matched against the callee prolog's "
        "``MOV EDI,[ECX]`` / ``MOV [EAX],EDI`` / ``FLD [ESI+0x28]`` "
        "accesses.  First RET is ``C2 04`` (pops 4 bytes = the "
        "stack float), confirming fastcall-with-one-stack-arg "
        "ABI plus the implicit EAX output."
    ),
))


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------


def all_symbols() -> dict[str, int]:
    """Return a fresh (mangled_name -> VA) dict.

    This is the exact shape :func:`azurik_mod.patching.coff.layout_coff`
    accepts as its ``vanilla_symbols`` parameter.
    """
    return {sym.mangled: sym.va for sym in _REGISTRY.values()}


def all_entries() -> list[VanillaSymbol]:
    """Return every registered :class:`VanillaSymbol` in insertion order."""
    return list(_REGISTRY.values())


def get(mangled: str) -> VanillaSymbol:
    """Look up a registered symbol by its mangled COFF name."""
    return _REGISTRY[mangled]
