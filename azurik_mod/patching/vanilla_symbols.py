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

# ---------------------------------------------------------------------------
# Xbox kernel / XDK re-exports (April 2026 expansion)
# ---------------------------------------------------------------------------
# These all live inside Azurik's XBE image because the build-time
# linker inlined the xboxkrnl import stubs.  Calling them from a
# shim is safe and cheap — the VAs resolve to the game's own
# inlined copies, not to cross-module thunks.
#
# Safety contract for adding new kernel/XDK entries:
#
#   1. Decompile the function in Ghidra and confirm the last
#      instruction is ``RET N`` (stdcall) or plain ``RET``
#      (cdecl / varargs).
#   2. Count the on-stack argument bytes from the function
#      signature.  For stdcall, ``arg_bytes`` == N.
#   3. The C declaration in ``shims/include/azurik_vanilla.h``
#      must match the calling convention (``__attribute__((stdcall))``
#      or default cdecl) AND the exact argument-byte count.
#   4. Run ``pytest tests/test_vanilla_thunks.py`` — the drift
#      guard compiles the header and refuses any COFF-symbol
#      mismatch.

# ---- SHA-1 (kernel crypto) --------------------------------------

register(VanillaSymbol(
    name="XcSHAInit",
    va=0x000E7E5C,
    calling_convention="stdcall",
    arg_bytes=4,
    doc=(
        "Kernel SHA-1 context initialiser.  __stdcall(4): "
        "pointer to a 0x4C-byte context buffer in ECX-after-"
        "PUSH (on the stack).  Idiomatically the shim allocates "
        "an 80-byte aligned buffer, calls XcSHAInit, then "
        "streams data with XcSHAUpdate.\n\n"
        "Vanilla VA 0x000E7E5C — matches the XDK re-export."
    ),
))
register(VanillaSymbol(
    name="XcSHAUpdate",
    va=0x000E7E56,
    calling_convention="stdcall",
    arg_bytes=12,
    doc=(
        "Kernel SHA-1 stream update.  __stdcall(12): "
        "``(ctx, data, len)`` all on the stack.  Feeds "
        "``len`` bytes of ``data`` into the accumulating "
        "hash.  Pair with XcSHAInit / XcSHAFinal."
    ),
))
register(VanillaSymbol(
    name="XcSHAFinal",
    va=0x000E7E62,
    calling_convention="stdcall",
    arg_bytes=8,
    doc=(
        "Kernel SHA-1 finaliser.  __stdcall(8): ``(ctx, "
        "out_digest)``.  Writes the final 20-byte SHA-1 "
        "digest into the ``out_digest`` buffer and zeroes the "
        "context.  Shim-side usage typically pairs with "
        "XcSHAInit + XcSHAUpdate for custom hashing."
    ),
))

# ---- Debug output ----------------------------------------------

register(VanillaSymbol(
    name="DbgPrint",
    va=0x000F5EB0,
    calling_convention="cdecl",
    arg_bytes=0,
    doc=(
        "Kernel printf-style debug output.  Varargs cdecl: "
        "``int DbgPrint(const char *fmt, ...)``.  Output "
        "appears in xemu's debug console.\n\n"
        "**The single most useful vanilla symbol for shim "
        "debugging.**  Call it from a trampoline to log what "
        "the shim sees at runtime without needing a gdb-stub.  "
        "Format specifiers match C stdio (``%d``, ``%s``, "
        "``%x``, ``%f``)."
    ),
))

register(VanillaSymbol(
    name="OutputDebugStringA",
    va=0x000F5658,
    calling_convention="stdcall",
    arg_bytes=4,
    doc=(
        "Win32-style debug string output.  __stdcall(4): "
        "``(const char *str)``.  Cheaper than DbgPrint when "
        "the shim already has a formatted string — no "
        "format-parsing overhead."
    ),
))

# ---- String operations (C runtime, cdecl) ----------------------

register(VanillaSymbol(
    name="strncmp",
    va=0x000EB240,
    calling_convention="cdecl",
    arg_bytes=0,
    doc=(
        "``int strncmp(const char *s1, const char *s2, "
        "size_t n)``.  Case-sensitive bounded compare.  "
        "Cdecl — mangled as ``_strncmp`` in COFF."
    ),
))
register(VanillaSymbol(
    name="_stricmp",
    va=0x000ECFB1,
    calling_convention="cdecl",
    arg_bytes=0,
    doc=(
        "``int _stricmp(const char *a, const char *b)`` — "
        "case-INSENSITIVE compare.  MSVC internal name "
        "(double-underscore mangle).  Shim authors should "
        "declare ``extern int _stricmp(const char *, const "
        "char *);`` so clang emits the matching "
        "``__stricmp`` undefined reference."
    ),
))
register(VanillaSymbol(
    name="_strnicmp",
    va=0x000EB561,
    calling_convention="cdecl",
    arg_bytes=0,
    doc=(
        "``int _strnicmp(const char *a, const char *b, "
        "size_t n)`` — case-insensitive bounded compare.  "
        "Mangled as ``__strnicmp``."
    ),
))
register(VanillaSymbol(
    name="strncpy",
    va=0x000EB7C0,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="``char *strncpy(char *dst, const char *src, size_t n)``.",
))
register(VanillaSymbol(
    name="strrchr",
    va=0x000EB3C0,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="``char *strrchr(const char *s, int c)`` — last-occurrence search.",
))
register(VanillaSymbol(
    name="strstr",
    va=0x000ED2E0,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="``char *strstr(const char *haystack, const char *needle)``.",
))
register(VanillaSymbol(
    name="atol",
    va=0x000EBE54,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="``long atol(const char *s)`` — parse decimal integer.",
))

# ---- Wide-character / UTF-16 ops -------------------------------

register(VanillaSymbol(
    name="wcscmp",
    va=0x000ECEE6,
    calling_convention="cdecl",
    arg_bytes=0,
    doc=(
        "``int wcscmp(const wchar_t *a, const wchar_t *b)``.  "
        "Xbox filesystem + save metadata paths are UTF-16; "
        "shims that touch SaveMeta.xbx benefit from this."
    ),
))
register(VanillaSymbol(
    name="wcsstr",
    va=0x000ECE72,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="``wchar_t *wcsstr(const wchar_t *hay, const wchar_t *needle)``.",
))

# ---- Stdio (file ops) ------------------------------------------

register(VanillaSymbol(
    name="fclose",
    va=0x000EB4E1,
    calling_convention="cdecl",
    arg_bytes=0,
    doc=(
        "``int fclose(FILE *stream)``.  Game uses FATX-backed "
        "FILE* handles for save I/O; pair with XcreateFileA / "
        "game-internal fopen wrappers."
    ),
))

# ---- Win32 synchronisation primitives --------------------------

register(VanillaSymbol(
    name="GetLastError",
    va=0x000E2DA7,
    calling_convention="stdcall",
    arg_bytes=0,
    doc=(
        "``DWORD GetLastError(void)``.  Xbox kernel stores "
        "per-thread last-error code; shims that wrap stdcall "
        "Win32 calls should consult this when a call returns "
        "FALSE / 0xFFFFFFFF."
    ),
))
register(VanillaSymbol(
    name="SetLastError",
    va=0x000E2DCF,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="``void SetLastError(DWORD code)``.",
))
register(VanillaSymbol(
    name="CreateEventA",
    va=0x000E0CA3,
    calling_convention="stdcall",
    arg_bytes=16,
    doc=(
        "``HANDLE CreateEventA(attrs, manual, initial, name)`` — "
        "create a Win32 event.  16 stack bytes "
        "(4 × u32 / pointer args)."
    ),
))
register(VanillaSymbol(
    name="SetEvent",
    va=0x000E0D60,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="``BOOL SetEvent(HANDLE h)`` — signal an event.",
))
register(VanillaSymbol(
    name="ResetEvent",
    va=0x000E0D80,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="``BOOL ResetEvent(HANDLE h)``.",
))

# ---- Xbox-specific title / launch control ----------------------

register(VanillaSymbol(
    name="XGetLaunchInfo",
    va=0x000DF948,
    calling_convention="stdcall",
    arg_bytes=8,
    doc=(
        "``DWORD XGetLaunchInfo(DWORD *flags_out, void "
        "*data_out)``.  Retrieves the launch-data blob "
        "passed in from the dashboard or a prior XBE "
        "invocation.  Useful for shims that want to detect "
        "how the game was launched."
    ),
))
register(VanillaSymbol(
    name="XLaunchNewImageA",
    va=0x000DFA10,
    calling_convention="stdcall",
    arg_bytes=8,
    doc=(
        "``DWORD XLaunchNewImageA(const char *xbe_path, "
        "void *data)``.  Launches a different XBE — pass "
        "NULL for ``xbe_path`` to return to the dashboard.  "
        "Companion: XapiBootToDash."
    ),
))
register(VanillaSymbol(
    name="XapiBootToDash",
    va=0x000E6A2D,
    calling_convention="stdcall",
    arg_bytes=12,
    doc=(
        "``void XapiBootToDash(arg1, arg2, arg3)`` — exit to "
        "the Xbox dashboard.  3 stack args (12 bytes).  "
        "A shim that wants a 'quit to dashboard' hotkey can "
        "call this directly."
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
# Bulk Xbox kernel / XDK / C-runtime coverage (April 2026 expansion)
# ---------------------------------------------------------------------------
#
# 242 entries below were auto-generated from the Ghidra snapshot +
# audited against the ``test_va_audit`` prologue drift guard.  Each
# call's ABI was inferred as follows:
#
#   - Cdecl for C-runtime / MSVC-internal names (``_foo`` / ``__foo``)
#     and any varargs signature.
#   - Stdcall for Xbox SDK / Win32 APIs (X*, Xc*, Xe*, Xapi*, D3D*,
#     Mm*, Ke*, Rtl*, plus a hand-curated Win32 set).
#   - Stack-arg bytes = sum of 4-byte slots (8 for ``long long`` /
#     ``double`` params).
#
# Shim authors: the matching extern declarations live in
# ``shims/include/azurik_vanilla.h``.  If a name you want isn't
# here, add it + its extern together, then run
# ``pytest tests/test_vanilla_thunks.py tests/test_va_audit.py``.


register(VanillaSymbol(
    name="XapiSelectCachePartition",
    va=0x000DFE7B,
    calling_convention="stdcall",
    arg_bytes=12,
    doc="Auto-generated stdcall(12) entry for ``XapiSelectCachePartition``. Ghidra signature: ``int XapiSelectCachePartition(int param_1, uint * param_2, undefined4 * param_3)``",
))

register(VanillaSymbol(
    name="XMountUtilityDrive",
    va=0x000E007F,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``XMountUtilityDrive``. Ghidra signature: ``undefined1 XMountUtilityDrive(int param_1)``",
))

register(VanillaSymbol(
    name="XMountAlternateTitleA",
    va=0x000E016A,
    calling_convention="stdcall",
    arg_bytes=12,
    doc="Auto-generated stdcall(12) entry for ``XMountAlternateTitleA``. Ghidra signature: ``undefined4 XMountAlternateTitleA(byte * param_1, dword param_2, char * param_3)``",
))

register(VanillaSymbol(
    name="XUnmountAlternateTitleA",
    va=0x000E02CF,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``XUnmountAlternateTitleA``. Ghidra signature: ``undefined XUnmountAlternateTitleA(byte param_1)``",
))

register(VanillaSymbol(
    name="XMUNameFromDriveLetter",
    va=0x000E0466,
    calling_convention="stdcall",
    arg_bytes=12,
    doc="Auto-generated stdcall(12) entry for ``XMUNameFromDriveLetter``. Ghidra signature: ``undefined4 XMUNameFromDriveLetter(undefined4 param_1, undefined4 param_2, undefined4 param_3)``",
))

register(VanillaSymbol(
    name="MoveFileA",
    va=0x000E06B4,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``MoveFileA``. Ghidra signature: ``undefined4 MoveFileA(undefined4 param_1, undefined4 param_2)``",
))

register(VanillaSymbol(
    name="OpenEventA",
    va=0x000E0D04,
    calling_convention="stdcall",
    arg_bytes=12,
    doc="Auto-generated stdcall(12) entry for ``OpenEventA``. Ghidra signature: ``int OpenEventA(undefined4 param_1, undefined4 param_2, int param_3)``",
))

register(VanillaSymbol(
    name="PulseEvent",
    va=0x000E0D9E,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``PulseEvent``. Ghidra signature: ``bool PulseEvent(undefined4 param_1)``",
))

register(VanillaSymbol(
    name="CreateMutexA",
    va=0x000E0E9B,
    calling_convention="stdcall",
    arg_bytes=12,
    doc="Auto-generated stdcall(12) entry for ``CreateMutexA``. Ghidra signature: ``int CreateMutexA(undefined4 param_1, undefined4 param_2, int param_3)``",
))

register(VanillaSymbol(
    name="SignalObjectAndWait",
    va=0x000E0FB3,
    calling_convention="stdcall",
    arg_bytes=16,
    doc="Auto-generated stdcall(16) entry for ``SignalObjectAndWait``. Ghidra signature: ``int SignalObjectAndWait(undefined4 param_1, undefined4 param_2, uint param_3, int param_4)``",
))

register(VanillaSymbol(
    name="XCalculateSignatureBegin",
    va=0x000E2BC9,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``XCalculateSignatureBegin``. Ghidra signature: ``undefined4 * XCalculateSignatureBegin(undefined4 param_1)``",
))

register(VanillaSymbol(
    name="XapiSetLastNTError",
    va=0x000E2DFD,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``XapiSetLastNTError``. Ghidra signature: ``undefined4 XapiSetLastNTError(undefined4 param_1)``",
))

register(VanillaSymbol(
    name="GetOverlappedResult",
    va=0x000E2F40,
    calling_convention="stdcall",
    arg_bytes=16,
    doc="Auto-generated stdcall(16) entry for ``GetOverlappedResult``. Ghidra signature: ``undefined4 GetOverlappedResult(int param_1, int * param_2, int * param_3, int param_4)``",
))

register(VanillaSymbol(
    name="XapiMapLetterToDirectory",
    va=0x000E66A4,
    calling_convention="stdcall",
    arg_bytes=24,
    doc="Auto-generated stdcall(24) entry for ``XapiMapLetterToDirectory``. Ghidra signature: ``int XapiMapLetterToDirectory(undefined4 param_1, ushort * param_2, char * param_3, int param_4, short * param_5, XBE_SECTION_HEADER * param_6)``",
))

register(VanillaSymbol(
    name="XapiInitProcess",
    va=0x000E6A92,
    calling_convention="stdcall",
    arg_bytes=0,
    doc="Auto-generated stdcall(0) entry for ``XapiInitProcess``. Ghidra signature: ``undefined XapiInitProcess()``",
))

register(VanillaSymbol(
    name="XapiFormatObjectAttributes",
    va=0x000E705E,
    calling_convention="stdcall",
    arg_bytes=12,
    doc="Auto-generated stdcall(12) entry for ``XapiFormatObjectAttributes``. Ghidra signature: ``undefined XapiFormatObjectAttributes(undefined4 param_1, undefined4 param_2, undefined4 param_3)``",
))

register(VanillaSymbol(
    name="cinit",
    va=0x000E72DC,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``_cinit``. Ghidra signature: ``undefined cinit()``",
))

register(VanillaSymbol(
    name="rtinit",
    va=0x000E7334,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``_rtinit``. Ghidra signature: ``undefined rtinit()``",
))

register(VanillaSymbol(
    name="XapiCallThreadNotifyRoutines",
    va=0x000E735D,
    calling_convention="stdcall",
    arg_bytes=0,
    doc="Auto-generated stdcall(0) entry for ``XapiCallThreadNotifyRoutines``. Ghidra signature: ``undefined XapiCallThreadNotifyRoutines()``",
))

register(VanillaSymbol(
    name="UnhandledExceptionFilter",
    va=0x000E7395,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``UnhandledExceptionFilter``. Ghidra signature: ``undefined4 UnhandledExceptionFilter(undefined4 param_1)``",
))

register(VanillaSymbol(
    name="SetThreadPriority",
    va=0x000E73B2,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``SetThreadPriority``. Ghidra signature: ``undefined4 SetThreadPriority(undefined4 param_1, int param_2)``",
))

register(VanillaSymbol(
    name="GetThreadPriority",
    va=0x000E7404,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``GetThreadPriority``. Ghidra signature: ``int GetThreadPriority(undefined4 param_1)``",
))

register(VanillaSymbol(
    name="SetThreadPriorityBoost",
    va=0x000E7458,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``SetThreadPriorityBoost``. Ghidra signature: ``bool SetThreadPriorityBoost(undefined4 param_1, int param_2)``",
))

register(VanillaSymbol(
    name="RaiseException",
    va=0x000E752B,
    calling_convention="stdcall",
    arg_bytes=16,
    doc="Auto-generated stdcall(16) entry for ``RaiseException``. Ghidra signature: ``undefined RaiseException(undefined4 param_1, uint param_2, uint param_3, undefined4 * param_4)``",
))

register(VanillaSymbol(
    name="ExitThread",
    va=0x000E75C4,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``ExitThread``. Ghidra signature: ``undefined ExitThread(undefined4 param_1)``",
))

register(VanillaSymbol(
    name="GetExitCodeThread",
    va=0x000E75D6,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``GetExitCodeThread``. Ghidra signature: ``undefined4 GetExitCodeThread(int param_1, undefined4 * param_2)``",
))

register(VanillaSymbol(
    name="XRegisterThreadNotifyRoutine",
    va=0x000E76BD,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``XRegisterThreadNotifyRoutine``. Ghidra signature: ``undefined XRegisterThreadNotifyRoutine(int param_1)``",
))

register(VanillaSymbol(
    name="CreateThread",
    va=0x000E77A5,
    calling_convention="stdcall",
    arg_bytes=24,
    doc="Auto-generated stdcall(24) entry for ``CreateThread``. Ghidra signature: ``dword CreateThread(undefined4 param_1, dword param_2, undefined4 param_3, undefined4 param_4, uint param_5, undefined4 param_6)``",
))

register(VanillaSymbol(
    name="XGetSectionSize",
    va=0x000E7A90,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``XGetSectionSize``. Ghidra signature: ``undefined4 XGetSectionSize(int param_1)``",
))

register(VanillaSymbol(
    name="XAutoPowerDownResetTimer",
    va=0x000E7A9A,
    calling_convention="stdcall",
    arg_bytes=0,
    doc="Auto-generated stdcall(0) entry for ``XAutoPowerDownResetTimer``. Ghidra signature: ``undefined XAutoPowerDownResetTimer()``",
))

register(VanillaSymbol(
    name="ExQueryNonVolatileSetting",
    va=0x000E7E0E,
    calling_convention="stdcall",
    arg_bytes=20,
    doc="Auto-generated stdcall(20) entry for ``ExQueryNonVolatileSetting``. Ghidra signature: ``NTSTATUS ExQueryNonVolatileSetting(XBX_ULONG ValueIndex, XBX_PULONG Type, XBX_PVOID Value, XBX_ULONG ValueLength, XBX_PULONG ResultLength)``",
))

register(VanillaSymbol(
    name="_onexit_lk",
    va=0x000EB278,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__onexit_lk``. Ghidra signature: ``undefined _onexit_lk()``",
))

register(VanillaSymbol(
    name="__onexitinit",
    va=0x000EB2F8,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``___onexitinit``. Ghidra signature: ``undefined4 __onexitinit()``",
))

register(VanillaSymbol(
    name="_onexit",
    va=0x000EB320,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__onexit``. Ghidra signature: ``undefined4 _onexit()``",
))

register(VanillaSymbol(
    name="atexit",
    va=0x000EB358,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``_atexit``. Ghidra signature: ``int atexit(__func * __func)``",
))

register(VanillaSymbol(
    name="_fclose_lk",
    va=0x000EB495,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__fclose_lk``. Ghidra signature: ``undefined4 _fclose_lk(int * param_1)``",
))

register(VanillaSymbol(
    name="_abstract_cw",
    va=0x000EBC8E,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__abstract_cw``. Ghidra signature: ``uint _abstract_cw()``",
))

register(VanillaSymbol(
    name="_hw_cw",
    va=0x000EBD20,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__hw_cw``. Ghidra signature: ``uint _hw_cw()``",
))

register(VanillaSymbol(
    name="_control87",
    va=0x000EBE0C,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__control87``. Ghidra signature: ``uint _control87(uint param_1, uint param_2)``",
))

register(VanillaSymbol(
    name="_controlfp",
    va=0x000EBE3E,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__controlfp``. Ghidra signature: ``undefined _controlfp(uint param_1, uint param_2)``",
))

register(VanillaSymbol(
    name="_fsopen",
    va=0x000EC09F,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__fsopen``. Ghidra signature: ``undefined4 _fsopen()``",
))

register(VanillaSymbol(
    name="_dosmaperr",
    va=0x000EC190,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__dosmaperr``. Ghidra signature: ``undefined _dosmaperr(uint param_1)``",
))

register(VanillaSymbol(
    name="_wcsdup",
    va=0x000EC203,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__wcsdup``. Ghidra signature: ``wchar_t * _wcsdup(wchar_t * param_1)``",
))

register(VanillaSymbol(
    name="_copysign",
    va=0x000EC288,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__copysign``. Ghidra signature: ``double _copysign(double __x, double __y)``",
))

register(VanillaSymbol(
    name="_chgsign",
    va=0x000EC2A9,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__chgsign``. Ghidra signature: ``float10 _chgsign(undefined4 param_1, uint param_2)``",
))

register(VanillaSymbol(
    name="_allmul",
    va=0x000EC8E0,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__allmul``. Ghidra signature: ``longlong _allmul(uint param_1, int param_2, uint param_3, int param_4)``",
))

register(VanillaSymbol(
    name="_SEH_epilog",
    va=0x000ECC91,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__SEH_epilog``. Ghidra signature: ``undefined _SEH_epilog()``",
))

register(VanillaSymbol(
    name="_global_unwind2",
    va=0x000ECCA4,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__global_unwind2``. Ghidra signature: ``undefined _global_unwind2(undefined4 param_1)``",
))

register(VanillaSymbol(
    name="_local_unwind2",
    va=0x000ECCE6,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__local_unwind2``. Ghidra signature: ``undefined _local_unwind2(int param_1, int param_2)``",
))

register(VanillaSymbol(
    name="_aullshr",
    va=0x000ECDA0,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__aullshr``. Ghidra signature: ``ulonglong _aullshr(byte param_1, uint param_2)``",
))

register(VanillaSymbol(
    name="_aullrem",
    va=0x000ECDC0,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__aullrem``. Ghidra signature: ``undefined8 _aullrem(uint param_1, uint param_2, uint param_3, uint param_4)``",
))

register(VanillaSymbol(
    name="wcsncpy",
    va=0x000ECE35,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``_wcsncpy``. Ghidra signature: ``wchar_t * wcsncpy(wchar_t * __dest, wchar_t * __src, size_t __n)``",
))

register(VanillaSymbol(
    name="_aulldiv",
    va=0x000ECF20,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__aulldiv``. Ghidra signature: ``undefined8 _aulldiv(uint param_1, uint param_2, uint param_3, uint param_4)``",
))

register(VanillaSymbol(
    name="_allshr",
    va=0x000ECF90,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__allshr``. Ghidra signature: ``undefined8 _allshr(byte param_1, int param_2)``",
))

register(VanillaSymbol(
    name="_alldiv",
    va=0x000ED000,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__alldiv``. Ghidra signature: ``undefined8 _alldiv(uint param_1, uint param_2, uint param_3, uint param_4)``",
))

register(VanillaSymbol(
    name="_wcsicmp",
    va=0x000ED0AA,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__wcsicmp``. Ghidra signature: ``int _wcsicmp(ushort * param_1, ushort * param_2)``",
))

register(VanillaSymbol(
    name="wcscpy",
    va=0x000ED15C,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``_wcscpy``. Ghidra signature: ``wchar_t * wcscpy(wchar_t * __dest, wchar_t * __src)``",
))

register(VanillaSymbol(
    name="isalpha",
    va=0x000ED399,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``_isalpha``. Ghidra signature: ``int isalpha(int param_1)``",
))

register(VanillaSymbol(
    name="isdigit",
    va=0x000ED419,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``_isdigit``. Ghidra signature: ``int isdigit(int param_1)``",
))

register(VanillaSymbol(
    name="isspace",
    va=0x000ED470,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``_isspace``. Ghidra signature: ``int isspace(int param_1)``",
))

register(VanillaSymbol(
    name="isalnum",
    va=0x000ED4C2,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``_isalnum``. Ghidra signature: ``int isalnum(int param_1)``",
))

register(VanillaSymbol(
    name="seh_longjmp_unwind",
    va=0x000ED6C9,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``_seh_longjmp_unwind``. Ghidra signature: ``undefined seh_longjmp_unwind(int param_1)``",
))

register(VanillaSymbol(
    name="_flsbuf",
    va=0x000ED997,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__flsbuf``. Ghidra signature: ``uint _flsbuf(byte param_1, int * param_2)``",
))

register(VanillaSymbol(
    name="write_char",
    va=0x000EDAB0,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``_write_char``. Ghidra signature: ``undefined write_char(int * param_1)``",
))

register(VanillaSymbol(
    name="write_multi_char",
    va=0x000EDAE3,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``_write_multi_char``. Ghidra signature: ``undefined write_multi_char(undefined4 param_1, int param_2, int * param_3)``",
))

register(VanillaSymbol(
    name="write_string",
    va=0x000EDB07,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``_write_string``. Ghidra signature: ``undefined write_string(int param_1)``",
))

register(VanillaSymbol(
    name="_close",
    va=0x000EEB6D,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__close``. Ghidra signature: ``undefined4 _close()``",
))

register(VanillaSymbol(
    name="_freebuf",
    va=0x000EEC08,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__freebuf``. Ghidra signature: ``undefined _freebuf(undefined4 * param_1)``",
))

register(VanillaSymbol(
    name="_flush",
    va=0x000EEC33,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__flush``. Ghidra signature: ``undefined4 _flush(int * param_1)``",
))

register(VanillaSymbol(
    name="_fflush_lk",
    va=0x000EEC90,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__fflush_lk``. Ghidra signature: ``int _fflush_lk(int * param_1)``",
))

register(VanillaSymbol(
    name="flsall",
    va=0x000EECBE,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``_flsall``. Ghidra signature: ``undefined4 flsall()``",
))

register(VanillaSymbol(
    name="_flushall",
    va=0x000EEDE3,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__flushall``. Ghidra signature: ``undefined _flushall()``",
))

register(VanillaSymbol(
    name="_lock_file",
    va=0x000EEEAF,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__lock_file``. Ghidra signature: ``undefined _lock_file(uint param_1)``",
))

register(VanillaSymbol(
    name="_lock_file2",
    va=0x000EEEDE,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__lock_file2``. Ghidra signature: ``undefined _lock_file2(int param_1, int param_2)``",
))

register(VanillaSymbol(
    name="_unlock_file",
    va=0x000EEF01,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__unlock_file``. Ghidra signature: ``undefined _unlock_file(uint param_1)``",
))

register(VanillaSymbol(
    name="_unlock_file2",
    va=0x000EEF30,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__unlock_file2``. Ghidra signature: ``undefined _unlock_file2(int param_1, int param_2)``",
))

register(VanillaSymbol(
    name="_hextodec",
    va=0x000EF1EB,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__hextodec``. Ghidra signature: ``uint _hextodec()``",
))

register(VanillaSymbol(
    name="_inc",
    va=0x000EF21D,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__inc``. Ghidra signature: ``uint _inc(undefined4 param_1, undefined4 * param_2)``",
))

register(VanillaSymbol(
    name="_errcode",
    va=0x000F02CE,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__errcode``. Ghidra signature: ``int _errcode(byte param_1)``",
))

register(VanillaSymbol(
    name="_umatherr",
    va=0x000F02FB,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__umatherr``. Ghidra signature: ``float10 _umatherr(int param_1, int param_2)``",
))

register(VanillaSymbol(
    name="_handle_qnan1",
    va=0x000F0399,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__handle_qnan1``. Ghidra signature: ``float10 _handle_qnan1(int param_1, double param_2)``",
))

register(VanillaSymbol(
    name="_handle_qnan2",
    va=0x000F03EC,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__handle_qnan2``. Ghidra signature: ``float10 _handle_qnan2(int param_1, double param_2, double param_3)``",
))

register(VanillaSymbol(
    name="_set_exp",
    va=0x000F05AF,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__set_exp``. Ghidra signature: ``float10 _set_exp(undefined8 param_1, short param_2)``",
))

register(VanillaSymbol(
    name="_set_bexp",
    va=0x000F0618,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__set_bexp``. Ghidra signature: ``float10 _set_bexp(undefined8 param_1, short param_2)``",
))

register(VanillaSymbol(
    name="_sptype",
    va=0x000F063D,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__sptype``. Ghidra signature: ``undefined4 _sptype(int param_1, uint param_2)``",
))

register(VanillaSymbol(
    name="_ctrlfp",
    va=0x000F0765,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__ctrlfp``. Ghidra signature: ``int _ctrlfp()``",
))

register(VanillaSymbol(
    name="_filbuf",
    va=0x000F07E2,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__filbuf``. Ghidra signature: ``uint _filbuf(undefined4 * param_1)``",
))

register(VanillaSymbol(
    name="_read",
    va=0x000F0A8C,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__read``. Ghidra signature: ``undefined4 _read()``",
))

register(VanillaSymbol(
    name="_stbuf",
    va=0x000F0B37,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__stbuf``. Ghidra signature: ``undefined4 _stbuf(int * param_1)``",
))

register(VanillaSymbol(
    name="_ftbuf",
    va=0x000F0BBF,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__ftbuf``. Ghidra signature: ``undefined _ftbuf(int param_1, int * param_2)``",
))

register(VanillaSymbol(
    name="_write",
    va=0x000F0D74,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__write``. Ghidra signature: ``undefined4 _write()``",
))

register(VanillaSymbol(
    name="_openfile",
    va=0x000F1374,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__openfile``. Ghidra signature: ``undefined4 * _openfile(undefined4 param_1, char * param_2, undefined4 param_3, undefined4 * param_4)``",
))

register(VanillaSymbol(
    name="_getstream",
    va=0x000F14DC,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__getstream``. Ghidra signature: ``undefined4 * _getstream()``",
))

register(VanillaSymbol(
    name="_forcdecpt",
    va=0x000F1831,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__forcdecpt``. Ghidra signature: ``undefined _forcdecpt(char * param_1)``",
))

register(VanillaSymbol(
    name="_fassign",
    va=0x000F18EE,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__fassign``. Ghidra signature: ``undefined _fassign(uint param_1, uint * param_2, byte * param_3)``",
))

register(VanillaSymbol(
    name="_cfltcvt",
    va=0x000F1BDE,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__cfltcvt``. Ghidra signature: ``undefined _cfltcvt(undefined4 * param_1, char * param_2, int param_3, uint param_4, int param_5)``",
))

register(VanillaSymbol(
    name="_trandisp1",
    va=0x000F1C30,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__trandisp1``. Ghidra signature: ``undefined _trandisp1(undefined4 param_1, int param_2)``",
))

register(VanillaSymbol(
    name="_trandisp2",
    va=0x000F1C97,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__trandisp2``. Ghidra signature: ``undefined _trandisp2(undefined4 param_1, int param_2)``",
))

register(VanillaSymbol(
    name="_startOneArgErrorHandling",
    va=0x000F1E13,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__startOneArgErrorHandling``. Ghidra signature: ``float10 _startOneArgErrorHandling(undefined4 param_1, int param_2, ushort param_3, undefined4 param_4, undefined4 param_5, undefined4 param_6)``",
))

register(VanillaSymbol(
    name="_fload_withFB",
    va=0x000F1E95,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__fload_withFB``. Ghidra signature: ``uint _fload_withFB(undefined4 param_1, int param_2)``",
))

register(VanillaSymbol(
    name="_math_exit",
    va=0x000F1EFB,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__math_exit``. Ghidra signature: ``undefined _math_exit(undefined4 param_1, int param_2, undefined4 param_3, undefined4 param_4, undefined4 param_5)``",
))

register(VanillaSymbol(
    name="_lseek",
    va=0x000F23F8,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__lseek``. Ghidra signature: ``undefined4 _lseek()``",
))

register(VanillaSymbol(
    name="_getbuf",
    va=0x000F252B,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__getbuf``. Ghidra signature: ``undefined _getbuf(undefined4 * param_1)``",
))

register(VanillaSymbol(
    name="_isatty",
    va=0x000F256F,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__isatty``. Ghidra signature: ``byte _isatty(uint param_1)``",
))

register(VanillaSymbol(
    name="_aulldvrm",
    va=0x000F25D0,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__aulldvrm``. Ghidra signature: ``undefined8 _aulldvrm(uint param_1, uint param_2, uint param_3, uint param_4)``",
))

register(VanillaSymbol(
    name="_get_osfhandle",
    va=0x000F28B5,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__get_osfhandle``. Ghidra signature: ``undefined4 _get_osfhandle(uint param_1)``",
))

register(VanillaSymbol(
    name="_unlock_fhandle",
    va=0x000F2969,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__unlock_fhandle``. Ghidra signature: ``undefined _unlock_fhandle(uint param_1)``",
))

register(VanillaSymbol(
    name="_ZeroTail",
    va=0x000F34C8,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__ZeroTail``. Ghidra signature: ``undefined4 _ZeroTail(int param_1, int param_2)``",
))

register(VanillaSymbol(
    name="_IncMan",
    va=0x000F34FA,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__IncMan``. Ghidra signature: ``undefined _IncMan(int param_1, int param_2)``",
))

register(VanillaSymbol(
    name="_RoundMan",
    va=0x000F3547,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__RoundMan``. Ghidra signature: ``undefined4 _RoundMan(int param_1, int param_2)``",
))

register(VanillaSymbol(
    name="_CopyMan",
    va=0x000F35B9,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__CopyMan``. Ghidra signature: ``undefined _CopyMan(int param_1, undefined4 * param_2)``",
))

register(VanillaSymbol(
    name="_IsZeroMan",
    va=0x000F35E0,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__IsZeroMan``. Ghidra signature: ``undefined4 _IsZeroMan(int param_1)``",
))

register(VanillaSymbol(
    name="_ShrMan",
    va=0x000F35F9,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__ShrMan``. Ghidra signature: ``undefined _ShrMan(int param_1, int param_2)``",
))

register(VanillaSymbol(
    name="_ld12cvt",
    va=0x000F3674,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__ld12cvt``. Ghidra signature: ``undefined4 _ld12cvt(ushort * param_1, uint * param_2, int * param_3)``",
))

register(VanillaSymbol(
    name="_ld12told",
    va=0x000F37F8,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__ld12told``. Ghidra signature: ``undefined4 _ld12told(ushort * param_1, undefined4 * param_2)``",
))

register(VanillaSymbol(
    name="_sopen",
    va=0x000F410B,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__sopen``. Ghidra signature: ``undefined4 _sopen()``",
))

register(VanillaSymbol(
    name="__dtold",
    va=0x000F4214,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``___dtold``. Ghidra signature: ``undefined __dtold(uint * param_1, uint * param_2)``",
))

register(VanillaSymbol(
    name="_flswbuf",
    va=0x000F441A,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__flswbuf``. Ghidra signature: ``undefined2 _flswbuf(undefined2 param_1, int * param_2)``",
))

register(VanillaSymbol(
    name="__addl",
    va=0x000F4542,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``___addl``. Ghidra signature: ``undefined4 __addl(uint param_1, uint param_2, uint * param_3)``",
))

register(VanillaSymbol(
    name="__add_12",
    va=0x000F4563,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``___add_12``. Ghidra signature: ``undefined __add_12(uint * param_1, uint * param_2)``",
))

register(VanillaSymbol(
    name="__shl_12",
    va=0x000F45C1,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``___shl_12``. Ghidra signature: ``undefined __shl_12(uint * param_1)``",
))

register(VanillaSymbol(
    name="__shr_12",
    va=0x000F45EF,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``___shr_12``. Ghidra signature: ``undefined __shr_12(uint * param_1)``",
))

register(VanillaSymbol(
    name="MmFreeContiguousMemory",
    va=0x000F4F09,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``MmFreeContiguousMemory``. Ghidra signature: ``void MmFreeContiguousMemory(XBX_PVOID BaseAddress)``",
))

register(VanillaSymbol(
    name="GetTimeZoneInformation",
    va=0x000F53D1,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``GetTimeZoneInformation``. Ghidra signature: ``undefined4 GetTimeZoneInformation(int * param_1)``",
))

register(VanillaSymbol(
    name="OutputDebugStringW",
    va=0x000F568A,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``OutputDebugStringW``. Ghidra signature: ``undefined OutputDebugStringW(undefined4 param_1)``",
))

register(VanillaSymbol(
    name="RtlUnwind",
    va=0x000F56DC,
    calling_convention="stdcall",
    arg_bytes=16,
    doc="Auto-generated stdcall(16) entry for ``RtlUnwind``. Ghidra signature: ``void RtlUnwind(XBX_PVOID TargetFrame, XBX_PVOID TargetIp, PEXCEPTION_RECORD ExceptionRecord, XBX_PVOID ReturnValue)``",
))

register(VanillaSymbol(
    name="XGWriteSurfaceOrTextureToXPR",
    va=0x000F5717,
    calling_convention="stdcall",
    arg_bytes=12,
    doc="Auto-generated stdcall(12) entry for ``XGWriteSurfaceOrTextureToXPR``. Ghidra signature: ``undefined4 XGWriteSurfaceOrTextureToXPR(uint * param_1, uint param_2, int param_3)``",
))

register(VanillaSymbol(
    name="_itoa",
    va=0x000F5AF3,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__itoa``. Ghidra signature: ``void * _itoa(int param_1, void * param_2, uint param_3)``",
))

register(VanillaSymbol(
    name="_ltoa",
    va=0x000F5B1D,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__ltoa``. Ghidra signature: ``void * _ltoa(int param_1, void * param_2, uint param_3)``",
))

register(VanillaSymbol(
    name="longjmp",
    va=0x000F5C18,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``_longjmp``. Ghidra signature: ``void longjmp(__jmp_buf_tag * __env, int __val)``",
))

register(VanillaSymbol(
    name="_setjmp3",
    va=0x000F5C94,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__setjmp3``. Ghidra signature: ``undefined4 _setjmp3(undefined4 * param_1, int param_2, int param_3, undefined4 param_4)``",
))

register(VanillaSymbol(
    name="rt_probe_read4",
    va=0x000F5D0F,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``_rt_probe_read4``. Ghidra signature: ``undefined rt_probe_read4()``",
))

register(VanillaSymbol(
    name="_cintrindisp2",
    va=0x000F5FDC,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__cintrindisp2``. Ghidra signature: ``undefined _cintrindisp2(undefined4 param_1, int param_2)``",
))

register(VanillaSymbol(
    name="_cintrindisp1",
    va=0x000F601A,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__cintrindisp1``. Ghidra signature: ``undefined _cintrindisp1(undefined4 param_1, int param_2)``",
))

register(VanillaSymbol(
    name="_ctrandisp2",
    va=0x000F6057,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__ctrandisp2``. Ghidra signature: ``undefined _ctrandisp2(uint param_1, int param_2, uint param_3, int param_4)``",
))

register(VanillaSymbol(
    name="_ctrandisp1",
    va=0x000F61ED,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__ctrandisp1``. Ghidra signature: ``undefined _ctrandisp1(uint param_1, int param_2)``",
))

register(VanillaSymbol(
    name="_fload",
    va=0x000F6220,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="Auto-generated cdecl(0) entry for ``__fload``. Ghidra signature: ``float10 _fload(uint param_1, int param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_GetDeviceCaps",
    va=0x0011D5D0,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_GetDeviceCaps``. Ghidra signature: ``undefined D3DDevice_GetDeviceCaps(undefined4 * param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetGammaRamp",
    va=0x0011D630,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DDevice_SetGammaRamp``. Ghidra signature: ``undefined D3DDevice_SetGammaRamp(byte param_1, undefined4 * param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_GetGammaRamp",
    va=0x0011D690,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_GetGammaRamp``. Ghidra signature: ``undefined D3DDevice_GetGammaRamp(undefined4 * param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_CreateTexture",
    va=0x0011D6C0,
    calling_convention="stdcall",
    arg_bytes=28,
    doc="Auto-generated stdcall(28) entry for ``D3DDevice_CreateTexture``. Ghidra signature: ``undefined D3DDevice_CreateTexture(uint param_1, uint param_2, uint param_3, uint param_4, uint param_5, undefined4 param_6, undefined4 * param_7)``",
))

register(VanillaSymbol(
    name="D3DDevice_CreateVolumeTexture",
    va=0x0011D6F0,
    calling_convention="stdcall",
    arg_bytes=32,
    doc="Auto-generated stdcall(32) entry for ``D3DDevice_CreateVolumeTexture``. Ghidra signature: ``undefined D3DDevice_CreateVolumeTexture(uint param_1, uint param_2, uint param_3, uint param_4, uint param_5, uint param_6, undefined4 param_7, undefined4 * param_8)``",
))

register(VanillaSymbol(
    name="D3DDevice_CreateCubeTexture",
    va=0x0011D720,
    calling_convention="stdcall",
    arg_bytes=24,
    doc="Auto-generated stdcall(24) entry for ``D3DDevice_CreateCubeTexture``. Ghidra signature: ``undefined D3DDevice_CreateCubeTexture(uint param_1, uint param_2, uint param_3, uint param_4, undefined4 param_5, undefined4 * param_6)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetTransform",
    va=0x0011D7B0,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DDevice_SetTransform``. Ghidra signature: ``undefined D3DDevice_SetTransform(int param_1, uint * param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_MultiplyTransform",
    va=0x0011D8F0,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DDevice_MultiplyTransform``. Ghidra signature: ``undefined D3DDevice_MultiplyTransform(int param_1, float * param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_Release",
    va=0x0011DB30,
    calling_convention="stdcall",
    arg_bytes=0,
    doc="Auto-generated stdcall(0) entry for ``D3DDevice_Release``. Ghidra signature: ``undefined D3DDevice_Release()``",
))

register(VanillaSymbol(
    name="D3DDevice_BlockOnFence",
    va=0x0011DB80,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_BlockOnFence``. Ghidra signature: ``undefined D3DDevice_BlockOnFence(uint param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_GetVisibilityTestResult",
    va=0x0011DC10,
    calling_convention="stdcall",
    arg_bytes=12,
    doc="Auto-generated stdcall(12) entry for ``D3DDevice_GetVisibilityTestResult``. Ghidra signature: ``undefined4 D3DDevice_GetVisibilityTestResult(uint param_1, undefined4 * param_2, undefined4 * param_3)``",
))

register(VanillaSymbol(
    name="D3DDevice_BlockUntilVerticalBlank",
    va=0x0011DC90,
    calling_convention="stdcall",
    arg_bytes=0,
    doc="Auto-generated stdcall(0) entry for ``D3DDevice_BlockUntilVerticalBlank``. Ghidra signature: ``undefined D3DDevice_BlockUntilVerticalBlank()``",
))

register(VanillaSymbol(
    name="D3DDevice_InsertFence",
    va=0x0011DF00,
    calling_convention="stdcall",
    arg_bytes=0,
    doc="Auto-generated stdcall(0) entry for ``D3DDevice_InsertFence``. Ghidra signature: ``undefined D3DDevice_InsertFence()``",
))

register(VanillaSymbol(
    name="D3DDevice_GetDisplayMode",
    va=0x0011E530,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_GetDisplayMode``. Ghidra signature: ``undefined D3DDevice_GetDisplayMode(undefined4 * param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_Reset",
    va=0x0011E590,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_Reset``. Ghidra signature: ``int D3DDevice_Reset(uint * param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetRenderTarget",
    va=0x0011E650,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DDevice_SetRenderTarget``. Ghidra signature: ``undefined D3DDevice_SetRenderTarget(uint * param_1, uint * param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_GetBackBuffer",
    va=0x0011E8B0,
    calling_convention="stdcall",
    arg_bytes=12,
    doc="Auto-generated stdcall(12) entry for ``D3DDevice_GetBackBuffer``. Ghidra signature: ``undefined D3DDevice_GetBackBuffer(int param_1, undefined4 param_2, undefined4 * param_3)``",
))

register(VanillaSymbol(
    name="D3DDevice_CopyRects",
    va=0x0011E940,
    calling_convention="stdcall",
    arg_bytes=20,
    doc="Auto-generated stdcall(20) entry for ``D3DDevice_CopyRects``. Ghidra signature: ``undefined D3DDevice_CopyRects(int param_1, uint * param_2, uint param_3, int param_4, uint * param_5)``",
))

register(VanillaSymbol(
    name="D3DDevice_GetRenderTarget",
    va=0x0011EDB0,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_GetRenderTarget``. Ghidra signature: ``undefined4 D3DDevice_GetRenderTarget(undefined4 * param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_GetDepthStencilSurface",
    va=0x0011EDD0,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_GetDepthStencilSurface``. Ghidra signature: ``undefined4 D3DDevice_GetDepthStencilSurface(undefined4 * param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetViewport",
    va=0x0011EE00,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_SetViewport``. Ghidra signature: ``undefined D3DDevice_SetViewport(uint * param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetLight",
    va=0x0011EF60,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DDevice_SetLight``. Ghidra signature: ``undefined4 D3DDevice_SetLight(float param_1, int * param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetTexture",
    va=0x0011F260,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DDevice_SetTexture``. Ghidra signature: ``undefined D3DDevice_SetTexture(int param_1, uint * param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetIndices",
    va=0x0011F480,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DDevice_SetIndices``. Ghidra signature: ``undefined D3DDevice_SetIndices(uint * param_1, undefined4 param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_BeginVisibilityTest",
    va=0x0011F5A0,
    calling_convention="stdcall",
    arg_bytes=0,
    doc="Auto-generated stdcall(0) entry for ``D3DDevice_BeginVisibilityTest``. Ghidra signature: ``undefined D3DDevice_BeginVisibilityTest()``",
))

register(VanillaSymbol(
    name="D3DDevice_EndVisibilityTest",
    va=0x0011F5D0,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_EndVisibilityTest``. Ghidra signature: ``undefined4 D3DDevice_EndVisibilityTest(uint param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_GetDisplayFieldStatus",
    va=0x0011F630,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_GetDisplayFieldStatus``. Ghidra signature: ``undefined D3DDevice_GetDisplayFieldStatus(int * param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetTile",
    va=0x0011F890,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DDevice_SetTile``. Ghidra signature: ``undefined D3DDevice_SetTile(uint param_1, uint * param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetScissors",
    va=0x0011FB60,
    calling_convention="stdcall",
    arg_bytes=12,
    doc="Auto-generated stdcall(12) entry for ``D3DDevice_SetScissors``. Ghidra signature: ``undefined D3DDevice_SetScissors(uint param_1, uint param_2, uint * param_3)``",
))

register(VanillaSymbol(
    name="D3DDevice_PersistDisplay",
    va=0x0011FCF0,
    calling_convention="stdcall",
    arg_bytes=0,
    doc="Auto-generated stdcall(0) entry for ``D3DDevice_PersistDisplay``. Ghidra signature: ``int D3DDevice_PersistDisplay()``",
))

register(VanillaSymbol(
    name="D3DDevice_SetRenderState_Simple",
    va=0x0011FEB0,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DDevice_SetRenderState_Simple``. Ghidra signature: ``undefined D3DDevice_SetRenderState_Simple(undefined4 param_1, undefined4 param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetRenderState_Deferred",
    va=0x0011FEE0,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DDevice_SetRenderState_Deferred``. Ghidra signature: ``undefined D3DDevice_SetRenderState_Deferred(int param_1, undefined4 param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetRenderStateNotInline",
    va=0x0011FF00,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DDevice_SetRenderStateNotInline``. Ghidra signature: ``undefined D3DDevice_SetRenderStateNotInline(int param_1, undefined4 param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetRenderState_FogColor",
    va=0x001201E0,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_SetRenderState_FogColor``. Ghidra signature: ``undefined D3DDevice_SetRenderState_FogColor(uint param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetRenderState_CullMode",
    va=0x00120230,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_SetRenderState_CullMode``. Ghidra signature: ``undefined D3DDevice_SetRenderState_CullMode(int param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetRenderState_TextureFactor",
    va=0x00120310,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_SetRenderState_TextureFactor``. Ghidra signature: ``undefined D3DDevice_SetRenderState_TextureFactor(undefined4 param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetRenderState_LineWidth",
    va=0x00120360,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_SetRenderState_LineWidth``. Ghidra signature: ``undefined D3DDevice_SetRenderState_LineWidth(float param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetRenderState_Dxt1NoiseEnable",
    va=0x001203C0,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_SetRenderState_Dxt1NoiseEnable``. Ghidra signature: ``undefined D3DDevice_SetRenderState_Dxt1NoiseEnable(uint param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetRenderState_FillMode",
    va=0x00120510,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_SetRenderState_FillMode``. Ghidra signature: ``undefined D3DDevice_SetRenderState_FillMode(undefined4 param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetTextureState_TexCoordIndex",
    va=0x00120640,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DDevice_SetTextureState_TexCoordIndex``. Ghidra signature: ``undefined D3DDevice_SetTextureState_TexCoordIndex(int param_1, uint param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetTextureState_BumpEnv",
    va=0x00120720,
    calling_convention="stdcall",
    arg_bytes=12,
    doc="Auto-generated stdcall(12) entry for ``D3DDevice_SetTextureState_BumpEnv``. Ghidra signature: ``undefined D3DDevice_SetTextureState_BumpEnv(uint param_1, int param_2, undefined4 param_3)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetTextureState_BorderColor",
    va=0x00120780,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DDevice_SetTextureState_BorderColor``. Ghidra signature: ``undefined D3DDevice_SetTextureState_BorderColor(int param_1, int param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetTextureState_ColorKeyColor",
    va=0x001207C0,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DDevice_SetTextureState_ColorKeyColor``. Ghidra signature: ``undefined D3DDevice_SetTextureState_ColorKeyColor(int param_1, int param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetTextureStageStateNotInline",
    va=0x00120810,
    calling_convention="stdcall",
    arg_bytes=12,
    doc="Auto-generated stdcall(12) entry for ``D3DDevice_SetTextureStageStateNotInline``. Ghidra signature: ``undefined D3DDevice_SetTextureStageStateNotInline(uint param_1, int param_2, uint param_3)``",
))

register(VanillaSymbol(
    name="D3D_CommonSetDebugRegisters",
    va=0x00120D20,
    calling_convention="stdcall",
    arg_bytes=0,
    doc="Auto-generated stdcall(0) entry for ``D3D_CommonSetDebugRegisters``. Ghidra signature: ``undefined D3D_CommonSetDebugRegisters()``",
))

register(VanillaSymbol(
    name="D3DDevice_SetRenderState_ZEnable",
    va=0x00120DF0,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_SetRenderState_ZEnable``. Ghidra signature: ``undefined D3DDevice_SetRenderState_ZEnable(int param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetRenderState_OcclusionCullEnable",
    va=0x00120F80,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_SetRenderState_OcclusionCullEnable``. Ghidra signature: ``undefined D3DDevice_SetRenderState_OcclusionCullEnable(int param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetRenderState_StencilCullEnable",
    va=0x00120FE0,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_SetRenderState_StencilCullEnable``. Ghidra signature: ``undefined D3DDevice_SetRenderState_StencilCullEnable(int param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetRenderState_RopZCmpAlwaysRead",
    va=0x00121040,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_SetRenderState_RopZCmpAlwaysRead``. Ghidra signature: ``undefined D3DDevice_SetRenderState_RopZCmpAlwaysRead(undefined4 param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetRenderState_RopZRead",
    va=0x00121060,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_SetRenderState_RopZRead``. Ghidra signature: ``undefined D3DDevice_SetRenderState_RopZRead(undefined4 param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetRenderState_DoNotCullUncompressed",
    va=0x00121080,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_SetRenderState_DoNotCullUncompressed``. Ghidra signature: ``undefined D3DDevice_SetRenderState_DoNotCullUncompressed(undefined4 param_1)``",
))

register(VanillaSymbol(
    name="D3DTexture_GetSurfaceLevel",
    va=0x00121150,
    calling_convention="stdcall",
    arg_bytes=12,
    doc="Auto-generated stdcall(12) entry for ``D3DTexture_GetSurfaceLevel``. Ghidra signature: ``undefined D3DTexture_GetSurfaceLevel(uint * param_1, uint param_2, undefined4 * param_3)``",
))

register(VanillaSymbol(
    name="D3DTexture_LockRect",
    va=0x001211A0,
    calling_convention="stdcall",
    arg_bytes=20,
    doc="Auto-generated stdcall(20) entry for ``D3DTexture_LockRect``. Ghidra signature: ``undefined D3DTexture_LockRect(uint * param_1, int param_2, undefined4 * param_3, int * param_4, uint param_5)``",
))

register(VanillaSymbol(
    name="D3DCubeTexture_GetCubeMapSurface",
    va=0x001211E0,
    calling_convention="stdcall",
    arg_bytes=16,
    doc="Auto-generated stdcall(16) entry for ``D3DCubeTexture_GetCubeMapSurface``. Ghidra signature: ``undefined D3DCubeTexture_GetCubeMapSurface(uint * param_1, int param_2, uint param_3, undefined4 * param_4)``",
))

register(VanillaSymbol(
    name="D3DCubeTexture_LockRect",
    va=0x00121240,
    calling_convention="stdcall",
    arg_bytes=24,
    doc="Auto-generated stdcall(24) entry for ``D3DCubeTexture_LockRect``. Ghidra signature: ``undefined D3DCubeTexture_LockRect(uint * param_1, uint param_2, int param_3, undefined4 * param_4, int * param_5, uint param_6)``",
))

register(VanillaSymbol(
    name="D3D_CreateTexture",
    va=0x00121300,
    calling_convention="stdcall",
    arg_bytes=36,
    doc="Auto-generated stdcall(36) entry for ``D3D_CreateTexture``. Ghidra signature: ``undefined4 D3D_CreateTexture(uint param_1, uint param_2, uint param_3, uint param_4, uint param_5, uint param_6, char param_7, uint param_8, undefined4 * param_9)``",
))

register(VanillaSymbol(
    name="D3D_CheckDeviceFormat",
    va=0x00121740,
    calling_convention="stdcall",
    arg_bytes=24,
    doc="Auto-generated stdcall(24) entry for ``D3D_CheckDeviceFormat``. Ghidra signature: ``undefined4 D3D_CheckDeviceFormat(int param_1, int param_2, int param_3, byte param_4, undefined4 param_5, int param_6)``",
))

register(VanillaSymbol(
    name="D3DDevice_CreateVertexShader",
    va=0x00121DF0,
    calling_convention="stdcall",
    arg_bytes=20,
    doc="Auto-generated stdcall(20) entry for ``D3DDevice_CreateVertexShader``. Ghidra signature: ``undefined4 D3DDevice_CreateVertexShader(void * this, uint * param_1, ushort * param_2, uint * param_3, undefined4 param_4)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetStreamSource",
    va=0x00122110,
    calling_convention="stdcall",
    arg_bytes=12,
    doc="Auto-generated stdcall(12) entry for ``D3DDevice_SetStreamSource``. Ghidra signature: ``undefined D3DDevice_SetStreamSource(int param_1, uint * param_2, int param_3)``",
))

register(VanillaSymbol(
    name="D3DDevice_LoadVertexShader",
    va=0x00122240,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DDevice_LoadVertexShader``. Ghidra signature: ``undefined D3DDevice_LoadVertexShader(int param_1, int param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_LoadVertexShaderProgram",
    va=0x001222A0,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DDevice_LoadVertexShaderProgram``. Ghidra signature: ``undefined D3DDevice_LoadVertexShaderProgram(uint * param_1, int param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_SelectVertexShader",
    va=0x00122310,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DDevice_SelectVertexShader``. Ghidra signature: ``undefined D3DDevice_SelectVertexShader(uint param_1, undefined4 param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetShaderConstantMode",
    va=0x001223D0,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_SetShaderConstantMode``. Ghidra signature: ``undefined D3DDevice_SetShaderConstantMode(uint param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_DeleteVertexShader",
    va=0x00122510,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_DeleteVertexShader``. Ghidra signature: ``undefined D3DDevice_DeleteVertexShader(int param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetVertexShader",
    va=0x00122630,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_SetVertexShader``. Ghidra signature: ``undefined D3DDevice_SetVertexShader(uint param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetVertexShaderConstant",
    va=0x00122710,
    calling_convention="stdcall",
    arg_bytes=12,
    doc="Auto-generated stdcall(12) entry for ``D3DDevice_SetVertexShaderConstant``. Ghidra signature: ``undefined D3DDevice_SetVertexShaderConstant(int param_1, uint * param_2, int param_3)``",
))

register(VanillaSymbol(
    name="D3DDevice_Clear",
    va=0x00122F60,
    calling_convention="stdcall",
    arg_bytes=24,
    doc="Auto-generated stdcall(24) entry for ``D3DDevice_Clear``. Ghidra signature: ``undefined D3DDevice_Clear(int param_1, uint * param_2, uint param_3, uint param_4, float param_5, uint param_6)``",
))

register(VanillaSymbol(
    name="D3DDevice_DrawVerticesUP",
    va=0x00123590,
    calling_convention="stdcall",
    arg_bytes=16,
    doc="Auto-generated stdcall(16) entry for ``D3DDevice_DrawVerticesUP``. Ghidra signature: ``undefined D3DDevice_DrawVerticesUP(undefined4 param_1, uint param_2, uint param_3, int param_4)``",
))

register(VanillaSymbol(
    name="D3DDevice_DrawIndexedVerticesUP",
    va=0x001236D0,
    calling_convention="stdcall",
    arg_bytes=20,
    doc="Auto-generated stdcall(20) entry for ``D3DDevice_DrawIndexedVerticesUP``. Ghidra signature: ``undefined D3DDevice_DrawIndexedVerticesUP(undefined4 param_1, uint param_2, ushort * param_3, uint param_4, int param_5)``",
))

register(VanillaSymbol(
    name="D3DDevice_DrawVertices",
    va=0x00123810,
    calling_convention="stdcall",
    arg_bytes=12,
    doc="Auto-generated stdcall(12) entry for ``D3DDevice_DrawVertices``. Ghidra signature: ``undefined D3DDevice_DrawVertices(undefined4 param_1, uint param_2, uint param_3)``",
))

register(VanillaSymbol(
    name="D3DDevice_DrawIndexedVertices",
    va=0x001238B0,
    calling_convention="stdcall",
    arg_bytes=12,
    doc="Auto-generated stdcall(12) entry for ``D3DDevice_DrawIndexedVertices``. Ghidra signature: ``undefined D3DDevice_DrawIndexedVertices(int * param_1, uint param_2, int * param_3)``",
))

register(VanillaSymbol(
    name="D3DSurface_GetDesc",
    va=0x00123E00,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DSurface_GetDesc``. Ghidra signature: ``undefined D3DSurface_GetDesc(uint * param_1, uint * param_2)``",
))

register(VanillaSymbol(
    name="D3DSurface_LockRect",
    va=0x00123E20,
    calling_convention="stdcall",
    arg_bytes=16,
    doc="Auto-generated stdcall(16) entry for ``D3DSurface_LockRect``. Ghidra signature: ``undefined D3DSurface_LockRect(uint * param_1, undefined4 * param_2, int * param_3, uint param_4)``",
))

register(VanillaSymbol(
    name="D3D_SetPushBufferSize",
    va=0x00124140,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3D_SetPushBufferSize``. Ghidra signature: ``undefined D3D_SetPushBufferSize(undefined4 param_1, undefined4 param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_CreateIndexBuffer",
    va=0x00124200,
    calling_convention="stdcall",
    arg_bytes=0,
    doc="Auto-generated stdcall(0) entry for ``D3DDevice_CreateIndexBuffer``. Ghidra signature: ``undefined4 D3DDevice_CreateIndexBuffer()``",
))

register(VanillaSymbol(
    name="D3DDevice_CreateVertexBuffer",
    va=0x00124320,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_CreateVertexBuffer``. Ghidra signature: ``undefined4 D3DDevice_CreateVertexBuffer(undefined4 param_1)``",
))

register(VanillaSymbol(
    name="D3DVertexBuffer_Lock",
    va=0x00124380,
    calling_convention="stdcall",
    arg_bytes=20,
    doc="Auto-generated stdcall(20) entry for ``D3DVertexBuffer_Lock``. Ghidra signature: ``undefined D3DVertexBuffer_Lock(uint * param_1, int param_2, undefined4 param_3, int * param_4, byte param_5)``",
))

register(VanillaSymbol(
    name="D3DResource_GetType",
    va=0x001244F0,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DResource_GetType``. Ghidra signature: ``char D3DResource_GetType(uint * param_1)``",
))

register(VanillaSymbol(
    name="D3DResource_BlockUntilNotBusy",
    va=0x001245A0,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DResource_BlockUntilNotBusy``. Ghidra signature: ``undefined D3DResource_BlockUntilNotBusy(uint * param_1)``",
))

register(VanillaSymbol(
    name="D3D_DestroyResource",
    va=0x00124740,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3D_DestroyResource``. Ghidra signature: ``undefined D3D_DestroyResource(uint * param_1)``",
))

register(VanillaSymbol(
    name="D3DResource_AddRef",
    va=0x00124860,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DResource_AddRef``. Ghidra signature: ``uint D3DResource_AddRef(uint * param_1)``",
))

register(VanillaSymbol(
    name="D3DResource_Release",
    va=0x001248A0,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DResource_Release``. Ghidra signature: ``uint D3DResource_Release(uint * param_1)``",
))

register(VanillaSymbol(
    name="D3DResource_Register",
    va=0x001249A0,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DResource_Register``. Ghidra signature: ``undefined D3DResource_Register(uint * param_1, int param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_RunPushBuffer",
    va=0x00125250,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DDevice_RunPushBuffer``. Ghidra signature: ``undefined D3DDevice_RunPushBuffer(int * param_1, int param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_GetPushBufferOffset",
    va=0x00125530,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_GetPushBufferOffset``. Ghidra signature: ``undefined D3DDevice_GetPushBufferOffset(int * param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_Present",
    va=0x001263C0,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_Present``. Ghidra signature: ``undefined D3DDevice_Present(uint * param_1)``",
))

register(VanillaSymbol(
    name="D3D_AllocContiguousMemory",
    va=0x00126570,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3D_AllocContiguousMemory``. Ghidra signature: ``undefined D3D_AllocContiguousMemory(int param_1, undefined4 param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_CreatePixelShader",
    va=0x001268B0,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3DDevice_CreatePixelShader``. Ghidra signature: ``undefined4 D3DDevice_CreatePixelShader(undefined4 * param_1, undefined4 * param_2)``",
))

register(VanillaSymbol(
    name="D3DDevice_DeletePixelShader",
    va=0x00126900,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_DeletePixelShader``. Ghidra signature: ``undefined D3DDevice_DeletePixelShader(int * param_1)``",
))

register(VanillaSymbol(
    name="D3DDevice_SetPixelShader",
    va=0x00126DC0,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3DDevice_SetPixelShader``. Ghidra signature: ``undefined D3DDevice_SetPixelShader(uint param_1)``",
))

register(VanillaSymbol(
    name="D3D_UpdateProjectionViewportTransform",
    va=0x00127680,
    calling_convention="stdcall",
    arg_bytes=0,
    doc="Auto-generated stdcall(0) entry for ``D3D_UpdateProjectionViewportTransform``. Ghidra signature: ``undefined D3D_UpdateProjectionViewportTransform()``",
))

register(VanillaSymbol(
    name="D3D_LazySetPointParams",
    va=0x00127990,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3D_LazySetPointParams``. Ghidra signature: ``undefined D3D_LazySetPointParams(uint * param_1)``",
))

register(VanillaSymbol(
    name="D3D_SetFence",
    va=0x0012A410,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3D_SetFence``. Ghidra signature: ``uint D3D_SetFence(byte param_1)``",
))

register(VanillaSymbol(
    name="D3D_BlockOnTime",
    va=0x0012A4B0,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="Auto-generated stdcall(8) entry for ``D3D_BlockOnTime``. Ghidra signature: ``undefined D3D_BlockOnTime(uint param_1, int param_2)``",
))

register(VanillaSymbol(
    name="D3D_KickOffAndWaitForIdle",
    va=0x0012A790,
    calling_convention="stdcall",
    arg_bytes=0,
    doc="Auto-generated stdcall(0) entry for ``D3D_KickOffAndWaitForIdle``. Ghidra signature: ``undefined D3D_KickOffAndWaitForIdle()``",
))

register(VanillaSymbol(
    name="D3D_BlockOnResource",
    va=0x0012A7B0,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``D3D_BlockOnResource``. Ghidra signature: ``undefined D3D_BlockOnResource(uint * param_1)``",
))

register(VanillaSymbol(
    name="XMETAL_StartPush",
    va=0x0012A840,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="Auto-generated stdcall(4) entry for ``XMETAL_StartPush``. Ghidra signature: ``undefined XMETAL_StartPush(uint * param_1)``",
))



register(VanillaSymbol(
    name="XGSwizzleRect",
    va=0x0015CFED,
    calling_convention="stdcall",
    arg_bytes=32,
    doc="Auto-generated stdcall(32) entry for ``XGSwizzleRect``. Ghidra signature: ``undefined XGSwizzleRect(undefined4 * param_1, int param_2, int * param_3, undefined4 * param_4, uint param_5, uint param_6, uint * param_7, undefined8 * param_8)``",
))



register(VanillaSymbol(
    name="XGUnswizzleBox",
    va=0x0015E0B0,
    calling_convention="stdcall",
    arg_bytes=40,
    doc="Auto-generated stdcall(40) entry for ``XGUnswizzleBox``. Ghidra signature: ``undefined XGUnswizzleBox(int param_1, uint param_2, uint param_3, uint param_4, uint * param_5, undefined4 * param_6, uint param_7, int param_8, int * param_9, uint param_10)``",
))


register(VanillaSymbol(
    name="XGSetTextureHeader",
    va=0x0015E597,
    calling_convention="stdcall",
    arg_bytes=36,
    doc="Auto-generated stdcall(36) entry for ``XGSetTextureHeader``. Ghidra signature: ``undefined XGSetTextureHeader(uint param_1, uint param_2, uint param_3, uint param_4, uint param_5, undefined4 param_6, undefined4 * param_7, undefined4 param_8, uint param_9)``",
))




# ---------------------------------------------------------------------------
# SDK / Xbox-API additions — batch #2 (April 2026)
# XInput, XGraphics, XAudio, DSound top-level, Direct3D entry points
# that practical shims may call.  Signatures mined from Ghidra.
# --------------------------------------------------------------------------

# ---- XInput (XAPILIB) ----

register(VanillaSymbol(
    name="XInitDevices",
    va=0x00187E87,
    calling_convention="stdcall",
    arg_bytes=4,
    doc="SDK entry point (XInput (XAPILIB)).  Ghidra signature: ``undefined XInitDevices(undefined4 param_1)``",
))

# ---- XGraphics (XGRPH) ----

# ---- XAudio (DSOUND) ----

# ---- DirectSound (DSOUND) ----

register(VanillaSymbol(
    name="DirectSoundCreate",
    va=0x0013807C,
    calling_convention="stdcall",
    arg_bytes=8,
    doc="SDK entry point (DirectSound (DSOUND)).  Ghidra signature: ``int DirectSoundCreate(undefined4 param_1, uint * param_2)``",
))

register(VanillaSymbol(
    name="DirectSoundDoWork",
    va=0x00137205,
    calling_convention="stdcall",
    arg_bytes=0,
    doc="SDK entry point (DirectSound (DSOUND)).  Ghidra signature: ``undefined DirectSoundDoWork(void)``",
))

# ---- Direct3D (D3D) ----

register(VanillaSymbol(
    name="Direct3D_CreateDevice",
    va=0x00124160,
    calling_convention="stdcall",
    arg_bytes=0,
    doc="SDK entry point (Direct3D (D3D)).  Ghidra signature: ``int Direct3D_CreateDevice(void)``",
))




# ---------------------------------------------------------------------------
# C-runtime + compiler intrinsics - batch #3 (April 2026)
# 64-bit arithmetic helpers clang emits implicitly + string/file stdlib.
# ---------------------------------------------------------------------------

register(VanillaSymbol(
    name="__alldiv",
    va=0x000ED000,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="64-bit signed divide (compiler intrinsic). Clang emits CALL __alldiv for (int64_t) / (int64_t).",
))

register(VanillaSymbol(
    name="__allmul",
    va=0x000EC8E0,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="64-bit multiply (compiler intrinsic).",
))

register(VanillaSymbol(
    name="__allshr",
    va=0x000ECF90,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="64-bit signed shift-right (intrinsic).",
))

register(VanillaSymbol(
    name="__aulldiv",
    va=0x000ECF20,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="64-bit unsigned divide (intrinsic).",
))

register(VanillaSymbol(
    name="__aullrem",
    va=0x000ECDC0,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="64-bit unsigned remainder (intrinsic).",
))

register(VanillaSymbol(
    name="__aullshr",
    va=0x000ECDA0,
    calling_convention="cdecl",
    arg_bytes=0,
    doc="64-bit unsigned shift-right (intrinsic).",
))


# ---------------------------------------------------------------------------
# Player physics / movement (April 2026 late RE pass)
# ---------------------------------------------------------------------------
#
# These are the player per-frame physics functions dispatched from
# player_physics_state_machine (FUN_0008CCC0).  Every shim-callable
# function in shims/include/azurik_vanilla.h has a matching entry
# here so tests/test_vanilla_thunks.py can enforce the contract.
#
# Calling-convention notes (confirmed via Ghidra call-site
# inspection):
#   - cdecl    → callers balance the stack (no RET N)
#   - stdcall  → callees pop their args (RET N at exit)
#   - fastcall → ECX + EDX carry the first 2 args; remainder on stack
#                (stack-arg bytes only)
#   - thiscall → ECX = this pointer; stack args caller-cleaned
#                unless RET N (MSVC thiscall) — check per function

register(VanillaSymbol(
    name="player_walk_state",
    va=0x00085F50,
    calling_convention="cdecl",
    arg_bytes=0,
    doc=(
        "Main walking-state per-frame function (state 0).  Reads "
        "CritterData.run_speed * entity.magnitude * direction "
        "and produces horizontal velocity.  The FLD at VA 0x85F62 "
        "(MOV EAX,[EBP+0x34]; FLD [EAX+0x40]) is the "
        "walk_speed_scale patch target."
    ),
))

register(VanillaSymbol(
    name="player_jump_init",
    va=0x00089060,
    calling_convention="cdecl",
    arg_bytes=0,
    doc=(
        "Initial jump from ground (state 5 -> 2 transition).  "
        "Sets entity.air_control=9.0 (imm32 at 0x890E4/0x89120) "
        "and entity.jump_height=1.1 (imm32 at 0x890D4/0x89110), "
        "then computes v0 = sqrt(2*gravity*jump_height).  The "
        "FLD [0x001980A8] at VA 0x89160 is the jump_speed_scale "
        "patch target.  Calls player_airborne_reinit on the air-"
        "power path (overwrites air_control/jump_height with "
        "air-power-specific values)."
    ),
))

register(VanillaSymbol(
    name="player_airborne_tick",
    va=0x00089480,
    calling_convention="cdecl",
    arg_bytes=0,
    doc=(
        "Per-frame airborne physics (state 2).  Computes "
        "horizontal velocity = entity.air_control * "
        "entity.magnitude and applies gravity to z.  Calls "
        "wing_flap (FUN_00089300) when the flap-button edge is "
        "triggered.  The FADD [0x001A25C0] at VA 0x896EA was the "
        "pre-v2 flap_height_scale target (wrong maneuver \u2014 see "
        "comment there)."
    ),
))

register(VanillaSymbol(
    name="wing_flap",
    va=0x00089300,
    calling_convention="stdcall",
    arg_bytes=4,
    doc=(
        "Wing flap / Air-power double-jump.  Gated by "
        "([input_state+0x20] & 0x04) + armor.flap_count != 0 + "
        "entity.flap_counter < armor.flap_count.  Consumes fuel "
        "via consume_fuel (1.0 first flap, 100.0 subsequent > 6m "
        "beyond peak).  v0 = sqrt(2 * 9.8 * flap_height) at "
        "VA 0x893AE (flap_height_scale patch target), halved by "
        "FMUL [0x001A2510]=0.5 at VA 0x893DD for subsequent flaps "
        "(flap_subsequent_scale patch target), scaled by "
        "FMUL [0x001A26C4]=1.5 at VA 0x893EB.  Also hosts the "
        "wing_flap_count shim trampoline at VA 0x89321."
    ),
))

register(VanillaSymbol(
    name="player_airborne_reinit",
    va=0x00083F90,
    calling_convention="fastcall",
    arg_bytes=0,
    doc=(
        "Per-frame airborne re-initialiser.  __fastcall: ECX = "
        "&entity[+0x140] (air-control out), EAX = armor_mgr (in).  "
        "Writes entity.air_control (12.0 if air-power-level in "
        "[1,3] else 9.0) and entity.jump_height (1.2 / 1.1 resp.) "
        "every frame.  This is WHY the static jump_init imm32 "
        "writes at 0x890E4/0x89126/etc. aren't enough to patch "
        "air-control \u2014 this function overrides them each frame.  "
        "The 2 imm32s at VA 0x83FAC (12.0) and 0x83FCE (9.0) are "
        "the air_control_scale secondary patch targets."
    ),
))

register(VanillaSymbol(
    name="player_input_tick",
    va=0x00084940,
    calling_convention="fastcall",
    arg_bytes=0,
    doc=(
        "Per-frame input composer.  __fastcall: ECX = "
        "PlayerInputState pointer.  Reads raw stick/buttons, "
        "computes entity.magnitude ([entity+0x124]) and "
        "entity.direction ([entity+0x128..0x130]).  When bit 0x40 "
        "of [entity+0x20] is set (WHITE or BACK held), multiplies "
        "magnitude by 3.0 at VA 0x849E4 (FMUL [0x001A25BC]).  That "
        "FMUL is the roll_speed_scale patch target."
    ),
))

register(VanillaSymbol(
    name="player_climb_tick",
    va=0x00087F80,
    calling_convention="cdecl",
    arg_bytes=0,
    doc=(
        "Climbing-state per-frame function (state 1).  Reads "
        "g_climb_speed_const (VA 0x001980E4 = 2.0) at two call "
        "sites: VA 0x87FA7 (primary climb velocity) and "
        "VA 0x88357 (secondary climb-retarget).  The constant has "
        "EXACTLY 2 readers (both here), so climb_speed_scale "
        "patches the constant in place."
    ),
))

register(VanillaSymbol(
    name="player_slope_slide_tick",
    va=0x00089A70,
    calling_convention="cdecl",
    arg_bytes=0,
    doc=(
        "Slope-slide states 3 & 4 per-frame function.  Entered "
        "via player_ground_land_tick (FUN_0008AE10 transition) "
        "when the player lands on a slope > 45\u00b0 from upright.  "
        "State 3 uses g_slope_slide_speed_const (VA 0x001AAB68 = "
        "2.0) \u2014 single reader at VA 0x89B76 \u2014 patched by "
        "slope_slide_speed_scale via direct 4-byte float "
        "overwrite.  State 4 has separate fast-slide physics "
        "with dynamic-init globals at 0x003902A0..9C."
    ),
))

register(VanillaSymbol(
    name="player_swim_tick",
    va=0x0008B700,
    calling_convention="stdcall",
    arg_bytes=4,
    doc=(
        "Swim state per-frame function (state 6).  Reads "
        "[0x001A25B4]=10.0 (shared) at VA 0x8B7BF as the stroke "
        "multiplier.  The FMUL there is the swim_speed_scale "
        "patch target."
    ),
))

register(VanillaSymbol(
    name="fall_damage_dispatch",
    va=0x0008AB70,
    calling_convention="stdcall",
    arg_bytes=8,
    doc=(
        "Fall-damage tiered dispatcher (surface-landing path).  "
        "__stdcall(float fall_height, float fall_speed).  Called "
        "from player_landing at VA 0x8C173.  Reads 7 cvars "
        "from config.xbr on first call ('fall min velocity', "
        "'fall height 1/2/3', 'fall damage 1/2/3') cached as "
        "static doubles at 0x00390228..290.  Applies damage via "
        "apply_damage (FUN_00044640) when thresholds are "
        "breached.  Our no_fall_damage v2 rewrites the prologue "
        "to 'XOR AL,AL ; RET 8 ; NOP' \u2014 always returns 0 "
        "without running tier selector."
    ),
))

register(VanillaSymbol(
    name="fall_death_dispatch",
    va=0x0008BE00,
    calling_convention="stdcall",
    arg_bytes=4,
    doc=(
        "Fall-damage dispatcher (no-surface landing path).  "
        "__stdcall(int entity).  Called from player_landing at "
        "VA 0x8C095 when [entity+0x38] (surface-contact slot) "
        "is NULL \u2014 the player landed without resolving a "
        "floor.  Reads the cached 'fall height 4' cvar, "
        "computes fall magnitude, calls apply_damage if it "
        "exceeds threshold, plays 'fx/sound/player/fallingdeath' "
        "SFX, sets death flag ([entity+0x16C] |= 1).  Our "
        "no_fall_damage v2 rewrites the prologue to "
        "'XOR AL,AL ; RET 4' \u2014 closes the second fall-damage "
        "leak user reported as 'light damage still fires' after "
        "v1 (which only patched FUN_0008AB70)."
    ),
))

register(VanillaSymbol(
    name="apply_damage",
    va=0x00044640,
    calling_convention="cdecl",
    arg_bytes=12,
    doc=(
        "Generic damage-apply routine.  Called from ~22 sites "
        "spanning combat, enemy impact, and environmental "
        "hazards.  Fall-damage callers (VA 0x8AD9B inside "
        "fall_damage_dispatch and VA 0x8BF59 inside "
        "fall_death_dispatch) are both bypassed by our "
        "no_fall_damage v2 pack.  NOT directly patched \u2014 "
        "touching this shared routine would affect non-fall "
        "damage too."
    ),
))

register(VanillaSymbol(
    name="player_landing",
    va=0x0008C080,
    calling_convention="stdcall",
    arg_bytes=8,
    doc=(
        "Player landing handler.  Dispatches to "
        "fall_death_dispatch (no-surface path, VA 0x8C095) "
        "or fall_damage_dispatch (surface path, VA 0x8C173) "
        "based on [entity+0x38] (surface-contact slot).  "
        "Called per-frame from player_airborne_tick's "
        "transition-to-ground logic."
    ),
))

register(VanillaSymbol(
    name="consume_fuel",
    va=0x000842D0,
    calling_convention="thiscall",
    arg_bytes=0,
    doc=(
        "Event-driven fuel consumer.  __thiscall (ECX = "
        "armor_mgr, float cost on stack; stack arg cleaned by "
        "callee via 'RET 4'). Decrements "
        "armor_mgr.fuel_current ([this+0x24]) by "
        "cost / armor.fuel_max ([[this+0x20]+0x38]) and returns 1 "
        "on success, 0 on refuse.  ONLY 2 callers \u2014 both in "
        "wing_flap (0x89354=1.0 cost, 0x893D4=100.0 penalty).  "
        "Per-frame sustained drain is SEPARATE, in "
        "player_armor_state_tick at VA 0x83DE3.  Our infinite_fuel "
        "patch rewrites this function's prologue to "
        "'MOV AL, 1 ; RET 4'."
    ),
))

register(VanillaSymbol(
    name="cvar_get_double",
    va=0x0005E620,
    calling_convention="cdecl",
    arg_bytes=0,
    doc=(
        "Cvar value fetcher.  Called to read a cvar's current "
        "value by name (e.g. 'fall min velocity').  Uses a "
        "caller-maintained cached-return pattern: caller keeps a "
        "static double + 'cached' byte, zeroes the byte on first "
        "call, passes the cvar-name pointer in ESI (register).  "
        "The arg-in-ESI is a Watcom-ish convention not expressible "
        "in portable C, so shims using this would need a wrapper."
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
