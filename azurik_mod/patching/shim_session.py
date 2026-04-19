"""Shim layout session — coordinates D1 + E across multiple trampolines.

A :class:`ShimLayoutSession` wraps an XBE bytearray and caches:

- **Kernel import stubs (D1).**  Every first time a shim calls
  ``DbgPrint`` (say), the session lazily parses the XBE's kernel
  thunk table, allocates a 6-byte ``FF 25 <thunk_va>`` stub, and
  records ``{"_DbgPrint": stub_va}``.  Subsequent shims referencing
  the same kernel function reuse the cached stub VA instead of
  installing a second copy.

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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from azurik_mod.patching.coff import ExternResolver, parse_coff, layout_coff
from azurik_mod.patching.kernel_imports import (
    kernel_import_map,
    kernel_name_for_symbol,
    stub_bytes_for,
)


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

        Returns ``None`` if ``mangled`` doesn't correspond to a known
        xboxkrnl export — the caller should keep looking in other
        resolvers (vanilla registry, shared-lib exports, etc.).

        The stub is 6 bytes: ``FF 25 <thunk_va>`` (indirect JMP through
        the kernel thunk slot).  Allocation happens via the caller's
        ``allocate`` callback — typically backed by
        :func:`_carve_shim_landing` so stubs land alongside ordinary
        shim code in the SHIMS region.
        """
        # Fast path: already placed a stub for this symbol.
        if mangled in self._kernel_stubs:
            return self._kernel_stubs[mangled]

        # Map mangled → xboxkrnl name, then thunk VA.
        kernel_name = kernel_name_for_symbol(mangled)
        if kernel_name is None:
            return None
        thunks = self.kernel_thunks()
        if kernel_name not in thunks:
            # Known ordinal, but Azurik doesn't import it — D1-extend
            # territory, not what this session can handle.
            return None
        thunk_va = thunks[kernel_name]

        stub = stub_bytes_for(thunk_va)
        _, stub_va = allocate(f"__imp__{kernel_name}", stub)
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
