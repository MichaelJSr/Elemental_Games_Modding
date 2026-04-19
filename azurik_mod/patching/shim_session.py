"""Shim layout session — coordinates D1 + D1-extend + E across trampolines.

A :class:`ShimLayoutSession` wraps an XBE bytearray and caches:

- **Static kernel import stubs (D1).**  When a shim calls a kernel
  function Azurik's vanilla XBE already imports (one of the 151 at
  the static thunk table), the session emits a 6-byte
  ``FF 25 <thunk_va>`` stub that indirect-jumps through the XBE's
  resolved thunk slot.  Cached by mangled name — one stub per
  kernel function, shared across all shims in the session.

- **Runtime-resolving kernel import stubs (D1-extend).**  When a
  shim calls a kernel function that is NOT in Azurik's thunk table
  (any other xboxkrnl export), the session:

    1. Auto-places ``shims/shared/xboxkrnl_resolver.c`` (the PE-
       export-table walker) the first time any extended import is
       referenced.
    2. Emits a 33-byte resolving stub per extended import: first
       call invokes the resolver with the ordinal and caches the
       result inline; subsequent calls jump through the cache.
    3. Caches stubs by mangled name, same as the static D1 path.

- **Shared library placements (E).**  Helper functions that several
  trampolines share can live in a standalone ``.o`` file.  The
  session lays that ``.o`` out once (via :meth:`apply_shared_library`)
  and exposes its exported symbols to every subsequent
  :func:`apply_trampoline_patch` call.  No duplication; no linker
  required.

The session is a lightweight object attached to the ``xbe_data``
bytearray itself — the same bookkeeping trick used for the
trampoline ledger in :mod:`apply`.  That means a pack's apply
function can call :func:`apply_trampoline_patch` multiple times
without having to thread a session argument through every call:
passing ``xbe_data`` is enough for the session to be discovered.

Typical usage inside a pack:

.. code-block:: python

   def apply_my_pack(xbe_data: bytearray) -> None:
       sess = get_or_create_session(xbe_data)
       sess.apply_shared_library(Path("shims/build/my_lib.o"))
       apply_trampoline_patch(xbe_data, MY_TRAMPOLINE_A, repo_root=R)
       apply_trampoline_patch(xbe_data, MY_TRAMPOLINE_B, repo_root=R)

Both trampolines reference ``my_lib.o`` exports via their COFF
externs; the session resolves them against the single placement.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from azurik_mod.patching.coff import ExternResolver, parse_coff, layout_coff
from azurik_mod.patching.kernel_imports import (
    kernel_import_map,
    kernel_name_for_symbol,
    stub_bytes_for,
)
from azurik_mod.patching.xboxkrnl_ordinals import (
    NAME_TO_ORDINAL,
    is_azurik_imported,
)


# --- D1-extend resolving-stub template --------------------------------------
#
# The resolver shim (shims/shared/xboxkrnl_resolver.c) exports one
# function: ``void *xboxkrnl_resolve_by_ordinal(unsigned ordinal)``.
# Per-import resolving stubs emitted by
# :meth:`ShimLayoutSession.stub_for_extended_kernel_symbol` invoke it
# once (caching the result inline) then jump through the cache on
# subsequent calls.
#
# Stub layout (33 bytes):
#
#   off  bytes                  instruction
#   ----+---------------------+----------------------------------
#   0x00 A1 <cache_va:4>       MOV  EAX, [cache_va]
#   0x05 85 C0                 TEST EAX, EAX
#   0x07 75 12                 JNZ  +0x12  (to offset 0x1B)
#   0x09 68 <ordinal:4>        PUSH imm32 <ordinal>
#   0x0E E8 <rel32:4>          CALL xboxkrnl_resolve_by_ordinal
#   0x13 83 C4 04              ADD  ESP, 4
#   0x16 A3 <cache_va:4>       MOV  [cache_va], EAX
#   0x1B FF E0                 JMP  EAX        ← resolved path
#   0x1D 00 00 00 00           DWORD cache     ← cached_va
#
# The shim's ``CALL _NtCreateProcess@20`` REL32 resolves to offset
# 0x00 of the stub.  cache_va = stub_va + 0x1D.
#
# ``ordinal`` and ``rel32`` are fixed at apply time; cache starts
# zero and is written on first call.

_D1_EXTEND_STUB_SIZE = 0x21  # 33 bytes (code + 4-byte cache slot)
_D1_EXTEND_CACHE_OFFSET = 0x1D


def _build_extended_stub(
    stub_va: int,
    ordinal: int,
    resolver_va: int,
) -> bytes:
    """Assemble the 33-byte resolving stub for one extended import.

    ``resolver_va`` is the VA of ``xboxkrnl_resolve_by_ordinal`` in
    the XBE's SHIMS region (after the resolver .o has been placed).
    ``stub_va`` is where this stub will live — used to compute the
    REL32 for the CALL and the abs32s for the cache-slot loads.
    """
    cache_va = stub_va + _D1_EXTEND_CACHE_OFFSET
    # REL32 for the CALL instruction at offset 0x0E.  Next-instruction
    # VA = stub_va + 0x13.
    call_site_after = stub_va + 0x13
    rel32 = resolver_va - call_site_after
    if not (-0x80000000 <= rel32 <= 0x7FFFFFFF):
        raise ValueError(
            f"D1-extend resolver is too far from stub at 0x{stub_va:X} "
            f"(delta 0x{rel32:X}) — doesn't fit signed 32-bit")

    code = bytearray()
    # MOV EAX, [cache_va]
    code += b"\xA1" + struct.pack("<I", cache_va)
    # TEST EAX, EAX
    code += b"\x85\xC0"
    # JNZ +0x12
    code += b"\x75\x12"
    # PUSH imm32 <ordinal>
    code += b"\x68" + struct.pack("<I", ordinal)
    # CALL rel32 <resolver_va>
    code += b"\xE8" + struct.pack("<i", rel32)
    # ADD ESP, 4
    code += b"\x83\xC4\x04"
    # MOV [cache_va], EAX
    code += b"\xA3" + struct.pack("<I", cache_va)
    # JMP EAX
    code += b"\xFF\xE0"
    # DWORD cache (starts zero)
    code += b"\x00\x00\x00\x00"
    assert len(code) == _D1_EXTEND_STUB_SIZE, (
        f"stub layout drift: got {len(code)} B, expected "
        f"{_D1_EXTEND_STUB_SIZE} B")
    return bytes(code)


# Session attribute name — attached to the bytearray so callers who
# pass just ``xbe_data`` downstream can still reach it.  Underscore
# prefix signals "private bookkeeping, don't serialise".
_SESSION_ATTR = "_shim_layout_session"


@dataclass
class ShimLayoutSession:
    """State shared across shim applies in a single pack-apply call.

    Holds caches of already-placed kernel stubs + shared-library
    exports so :class:`ShimLayoutSession.resolve_extern` deduplicates
    across trampolines.  Exactly one session should exist per XBE
    bytearray at apply time — use :func:`get_or_create_session` to
    get the singleton.
    """

    xbe_data: bytearray
    """The XBE being mutated.  Every ``FF 25`` stub / shared-library
    section placement is carved out of this buffer via the same
    ``_carve_shim_landing`` helper that ordinary trampolines use."""

    _kernel_stubs: dict[str, int] = field(default_factory=dict)
    """``{mangled_coff_name -> stub_va}``.  Cache of JMP-through-thunk
    stubs already placed in this session.  Indexed by the mangled
    name (``_DbgPrint``, ``_NtClose@4``, ...) because that's the key
    the COFF extern resolver receives — saves a re-mangle."""

    _kernel_thunks: dict[str, int] | None = None
    """``{un_mangled_name -> thunk_va}`` — lazily populated from the
    XBE's thunk table on first kernel-import lookup."""

    _shared_exports: dict[str, int] = field(default_factory=dict)
    """``{mangled_coff_name -> export_va}`` — merged map across every
    shared library applied in this session."""

    _shared_libs_placed: set[Path] = field(default_factory=set)
    """Set of shared-library ``.o`` paths already placed.  Re-applying
    the same library is an idempotent no-op that returns the cached
    export map."""

    _resolver_va: int | None = None
    """VA of the ``xboxkrnl_resolve_by_ordinal`` function once it's
    placed in the SHIMS region.  None until the first extended kernel
    import is referenced.  See :meth:`_ensure_resolver_placed`."""

    # ------------------------------------------------------------------
    # Kernel imports (D1)
    # ------------------------------------------------------------------

    def kernel_thunks(self) -> dict[str, int]:
        """Lazy-parse the XBE's kernel thunk table.

        First call does the work; subsequent calls hit the cache so
        we don't re-scan the thunk table on every shim.
        """
        if self._kernel_thunks is None:
            self._kernel_thunks = kernel_import_map(bytes(self.xbe_data))
        return self._kernel_thunks

    def stub_for_kernel_symbol(
        self,
        mangled: str,
        allocate: Callable[[str, bytes], tuple[int, int]],
    ) -> int | None:
        """Return the stub VA for a kernel import, placing one if needed.

        Dispatch strategy:

        1. **Cache hit**: reuse an already-placed stub for ``mangled``.
        2. **D1 fast path**: if the function is in Azurik's static
           thunk table, emit a 6-byte ``FF 25 <thunk_va>`` stub
           (indirect JMP through the pre-resolved slot).
        3. **D1-extend runtime path**: if the function is in the
           extended ordinal catalogue but NOT in Azurik's thunk table,
           auto-place ``xboxkrnl_resolver.c`` (once per session) and
           emit a 33-byte resolving stub that caches the resolved
           kernel pointer on first call.
        4. **Miss**: return ``None`` so the caller can keep looking in
           vanilla-symbol / shared-library resolvers.

        Allocation happens via the caller's ``allocate`` callback —
        typically backed by :func:`_carve_shim_landing` so stubs land
        alongside ordinary shim code in the SHIMS region.
        """
        # Fast path: already placed a stub for this symbol.
        if mangled in self._kernel_stubs:
            return self._kernel_stubs[mangled]

        # Map mangled → xboxkrnl name.  If neither the name nor the
        # kernel ordinal is catalogued, we can't generate a stub.
        kernel_name = kernel_name_for_symbol(mangled)
        if kernel_name is None:
            return None

        ordinal = NAME_TO_ORDINAL.get(kernel_name)
        if ordinal is None:
            return None

        # D1 fast path: Azurik's vanilla XBE already imports this
        # function.  Use the 6-byte static thunk stub.
        if is_azurik_imported(ordinal):
            thunks = self.kernel_thunks()
            if kernel_name not in thunks:
                # Table says Azurik imports it, but our parse missed
                # the thunk.  Shouldn't happen — but if it does,
                # fall through to the D1-extend path as a safety net.
                pass
            else:
                thunk_va = thunks[kernel_name]
                stub = stub_bytes_for(thunk_va)
                _, stub_va = allocate(f"__imp__{kernel_name}", stub)
                self._kernel_stubs[mangled] = stub_va
                return stub_va

        # D1-extend runtime path: this ordinal isn't in Azurik's
        # thunk table.  Emit a resolving stub that calls the shared
        # resolver on first invocation.
        return self._place_extended_kernel_stub(
            mangled, kernel_name, ordinal, allocate)

    # ------------------------------------------------------------------
    # Kernel imports (D1-extend — runtime resolver)
    # ------------------------------------------------------------------

    def _ensure_resolver_placed(
        self,
        allocate: Callable[[str, bytes], tuple[int, int]],
    ) -> int:
        """Place ``shims/shared/xboxkrnl_resolver.c`` once, return its VA.

        The resolver is a single-function shim (cdecl, no externs, no
        relocations) so we can use the fast ``extract_shim_bytes``
        path rather than the full ``layout_coff`` pipeline.  Compiles
        on demand if the .o is missing — inherits the same auto-
        compile heuristic as ``apply_trampoline_patch``.
        """
        if self._resolver_va is not None:
            return self._resolver_va

        # Lazy imports to keep the main :mod:`apply` module free of
        # circular references back into this file.
        from azurik_mod.patching.apply import (
            _guess_shim_source,
            _auto_compile,
        )
        from azurik_mod.patching.coff import extract_shim_bytes, parse_coff

        # Locate the resolver .o.  We key the path off this session's
        # xbe_data bytearray via the repo-root discovery already wired
        # into _guess_shim_source: pass a synthetic .o path that
        # points at shims/build/xboxkrnl_resolver.o and let the helper
        # find the source if the build is stale.
        from azurik_mod.patching.shim_session import _SESSION_ATTR  # noqa
        # Repo root := directory holding shims/ and azurik_mod/.
        # shim_session.py itself lives at azurik_mod/patching/, so
        # that's three parents up.
        import pathlib as _pl
        repo_root = _pl.Path(__file__).resolve().parents[2]
        shim_obj = repo_root / "shims" / "build" / "xboxkrnl_resolver.o"
        shim_src = repo_root / "shims" / "shared" / "xboxkrnl_resolver.c"

        if not shim_obj.exists():
            # Try an auto-compile — same hook apply_trampoline_patch
            # uses for missing feature shims.
            if shim_src.exists():
                _auto_compile(
                    shim_src, shim_obj, repo_root,
                    "D1-extend resolver")

        if not shim_obj.exists():
            raise RuntimeError(
                f"D1-extend: kernel resolver .o not found at "
                f"{shim_obj} and auto-compile failed.  Run "
                f"``bash shims/toolchain/compile.sh "
                f"{shim_src} {shim_obj}`` manually, or disable "
                f"extended kernel imports.")

        coff = parse_coff(shim_obj.read_bytes())
        text_bytes, sym_offset = extract_shim_bytes(
            coff, "_xboxkrnl_resolve_by_ordinal")
        _, base_va = allocate("xboxkrnl_resolver", text_bytes)
        self._resolver_va = base_va + sym_offset
        return self._resolver_va

    def _place_extended_kernel_stub(
        self,
        mangled: str,
        kernel_name: str,
        ordinal: int,
        allocate: Callable[[str, bytes], tuple[int, int]],
    ) -> int:
        """Emit a 33-byte resolving stub for an extended kernel import.

        The first invocation of this function in a session triggers
        placement of the resolver shim via :meth:`_ensure_resolver_placed`;
        subsequent invocations reuse the cached resolver VA.

        Each extended import gets its own stub (with its own inline
        4-byte cache slot).  Stubs are cached by mangled name so
        multiple shims referencing the same extended import share a
        single placement.
        """
        # Step 1: make sure the resolver itself is placed.
        resolver_va = self._ensure_resolver_placed(allocate)

        # Step 2: allocate the 33-byte stub with placeholder zeros.
        #         We allocate FIRST (to learn stub_va) then build the
        #         real bytes, then overwrite.
        placeholder = bytes(_D1_EXTEND_STUB_SIZE)
        stub_file_off, stub_va = allocate(
            f"__impext__{kernel_name}", placeholder)

        # Step 3: assemble the real stub bytes now that we know
        #         stub_va + resolver_va.
        stub_bytes = _build_extended_stub(stub_va, ordinal, resolver_va)
        self.xbe_data[stub_file_off:stub_file_off + _D1_EXTEND_STUB_SIZE] = \
            stub_bytes

        self._kernel_stubs[mangled] = stub_va
        return stub_va

    # ------------------------------------------------------------------
    # Shared libraries (E)
    # ------------------------------------------------------------------

    def apply_shared_library(
        self,
        lib_object: Path,
        allocate: Callable[[str, bytes], tuple[int, int]],
        *,
        vanilla_symbols: dict[str, int] | None = None,
    ) -> dict[str, int]:
        """Place a shared-library ``.o`` once; return its exported map.

        The returned dict maps mangled COFF symbol names (as they'd
        appear in another shim's undefined-extern list) to their
        final VAs in the XBE.  Subsequent calls with the same path
        are no-ops that return the cached map — the library is
        placed at most once per session.

        Exports are identified as symbols with ``storage_class == 2``
        (IMAGE_SYM_CLASS_EXTERNAL) and a positive ``section_number``
        (defined in a landed section).  That catches every ``extern``
        function / global the library exposes.  Internal symbols
        (static inlines, locals) stay private by virtue of having
        storage_class 3 or an undefined section.

        Relocations inside the library itself are resolved against
        the library's own sections plus ``vanilla_symbols`` (for
        vanilla Azurik calls from inside the library).  Further
        chained shared-lib → shared-lib references are NOT supported
        in this pass; that's a Phase E extension if demand arises.
        """
        lib_object = lib_object.resolve()
        if lib_object in self._shared_libs_placed:
            return dict(self._shared_exports)

        coff = parse_coff(lib_object.read_bytes())

        # Library-internal lookup uses the session's own extern
        # resolver so the library can call kernel functions or
        # (already-placed) earlier shared libs.  Fresh resolver so
        # the recursion terminates at the session's top-level caches.
        def _inner_resolver(name: str) -> int | None:
            if name in self._shared_exports:
                return self._shared_exports[name]
            return self.stub_for_kernel_symbol(name, allocate)

        # No single entry symbol — just place every section and let
        # the exports dict catch any externally-visible symbols.
        landed = layout_coff(
            coff,
            entry_symbol=None,
            allocate=allocate,
            vanilla_symbols=vanilla_symbols,
            extern_resolver=_inner_resolver,
        )

        # Write the relocated bytes over the placeholder zeros.
        for section in landed.sections:
            self.xbe_data[section.file_offset:
                          section.file_offset + len(section.data)] = section.data

        # Harvest externally-visible symbols.  Each landed section
        # has a known VA; symbol value is the intra-section offset.
        section_vas = {s.name: s.vaddr for s in landed.sections}
        new_exports: dict[str, int] = {}
        for sym in coff.symbols:
            if sym.name == "":
                continue  # aux-record placeholder
            if sym.storage_class != 2:
                continue  # not IMAGE_SYM_CLASS_EXTERNAL
            if sym.section_number <= 0:
                continue  # undefined (external reference, not export)
            owning = coff.sections[sym.section_number - 1]
            if owning.name not in section_vas:
                continue  # e.g. skipped metadata section
            va = section_vas[owning.name] + sym.value
            new_exports[sym.name] = va

        if not new_exports:
            raise ValueError(
                f"shared library {lib_object.name} defined no externally-"
                f"visible symbols — it would never be referenced.  Check "
                f"the library's source: functions need external linkage "
                f"(non-``static``) for other shims to call them.")

        self._shared_exports.update(new_exports)
        self._shared_libs_placed.add(lib_object)
        return dict(self._shared_exports)

    # ------------------------------------------------------------------
    # Main extern resolver (the thing passed to layout_coff)
    # ------------------------------------------------------------------

    def make_extern_resolver(
        self,
        allocate: Callable[[str, bytes], tuple[int, int]],
    ) -> ExternResolver:
        """Build a resolver closure bound to the caller's allocator.

        The returned callable tries — in order — shared-library
        exports, kernel-import stubs (allocating one on demand), and
        falls back to ``None`` for the caller's outer chain.
        """

        def _resolve(name: str) -> int | None:
            if name in self._shared_exports:
                return self._shared_exports[name]
            return self.stub_for_kernel_symbol(name, allocate)

        return _resolve


def get_or_create_session(xbe_data: bytearray) -> ShimLayoutSession:
    """Return the session attached to ``xbe_data``, creating one if
    none exists yet.

    Attribute-based lookup matches the pattern used by the trampoline
    ledger (see :mod:`apply`): keeps the session implicit so pack
    apply functions don't have to plumb a new argument through every
    call site.  Works on ``bytearray`` (which allows arbitrary
    attribute assignment); falls back to a fresh session every time
    for other types so non-bytearray callers still get *something*.
    """
    sess: ShimLayoutSession | None = getattr(xbe_data, _SESSION_ATTR, None)
    if sess is not None:
        return sess
    sess = ShimLayoutSession(xbe_data=xbe_data)
    try:
        setattr(xbe_data, _SESSION_ATTR, sess)
    except (AttributeError, TypeError):
        # Can't attach — caller will get a fresh session next time.
        # Keeps tests with plain ``bytes`` working without crashing.
        pass
    return sess
