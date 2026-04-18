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
        """COFF symbol name emitted by clang for this function."""
        if self.calling_convention == "cdecl":
            return f"_{self.name}"
        if self.calling_convention == "stdcall":
            return f"_{self.name}@{self.arg_bytes}"
        if self.calling_convention == "fastcall":
            return f"@{self.name}@{self.arg_bytes}"
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
