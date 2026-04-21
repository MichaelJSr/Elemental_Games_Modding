"""Pointer-graph helper for structural XBR edits.

Given an :class:`~azurik_mod.xbr.document.XbrDocument`, this module
walks every section's :meth:`~azurik_mod.xbr.sections.Section.iter_refs`
and builds an index of:

- Every pointer field by source offset.
- Every pointer field's current target offset.
- A fast "which refs cross a given byte range" query used by
  structural-edit primitives in :mod:`azurik_mod.xbr.edits`.

The graph also powers the ``xbr xref`` CLI verb (Phase 1) and the
drift-guard snapshot in ``docs/xbr_graph_snapshot.json``.

Lifecycle: graphs are cheap to build (one full-document scan) and
cheap to re-build, so callers that want a fresh view after an edit
can just recreate.  There's no incremental update path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Optional, TYPE_CHECKING

from azurik_mod.xbr.refs import Ref

if TYPE_CHECKING:
    from azurik_mod.xbr.document import XbrDocument
    from azurik_mod.xbr.sections import Section


@dataclass(frozen=True)
class ResolvedRef:
    """A ref plus its current target file offset, for reporting."""

    ref: Ref
    target_offset: Optional[int]


class PointerGraph:
    """Walks a document's pointer graph and answers "what breaks if
    I shift bytes ``[start, end)`` by ``delta``?" queries.

    Construct by passing the :class:`XbrDocument`; build cost is one
    linear scan over every section's :meth:`iter_refs`.
    """

    def __init__(self, document: "XbrDocument") -> None:
        self.document = document
        # (ref, target_offset).  target_offset may be None for
        # sentinel-0 entries.
        self._resolved: list[ResolvedRef] = []
        # Fast lookup by source offset.  Within a single document a
        # source offset uniquely identifies a ref.
        self._by_src: dict[int, ResolvedRef] = {}
        self._build()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        buf = bytes(self.document.raw)
        for section in self.document.sections():
            for ref in section.iter_refs():
                tgt = self._resolve(ref, buf)
                rr = ResolvedRef(ref=ref, target_offset=tgt)
                self._resolved.append(rr)
                self._by_src[ref.src_offset] = rr

    @staticmethod
    def _resolve(ref: Ref, data: bytes) -> Optional[int]:
        try:
            return ref.target_file_offset(data)
        except NotImplementedError:
            return None

    # ------------------------------------------------------------------
    # Iteration / lookup
    # ------------------------------------------------------------------

    def __iter__(self) -> Iterator[ResolvedRef]:
        return iter(self._resolved)

    def __len__(self) -> int:
        return len(self._resolved)

    def resolved_refs(self) -> list[ResolvedRef]:
        return list(self._resolved)

    def by_source_offset(self, src_offset: int) -> Optional[ResolvedRef]:
        return self._by_src.get(src_offset)

    def refs_in_range(
        self,
        start: int,
        end: int,
    ) -> list[ResolvedRef]:
        """Refs whose **source** field sits in ``[start, end)``."""
        return [rr for rr in self._resolved
                if start <= rr.ref.src_offset < end]

    def refs_targeting_range(
        self,
        start: int,
        end: int,
    ) -> list[ResolvedRef]:
        """Refs whose **target** sits in ``[start, end)``.  Missing
        targets (``None``) are skipped."""
        return [rr for rr in self._resolved
                if rr.target_offset is not None
                and start <= rr.target_offset < end]

    # ------------------------------------------------------------------
    # Structural-edit queries
    # ------------------------------------------------------------------

    def refs_to_patch_for_shift(
        self,
        shift_start: int,
        shift_delta: int,
    ) -> list[ResolvedRef]:
        """Which refs need their fields rewritten if every byte at
        offsets ``>= shift_start`` moves by ``+shift_delta``?

        Rule of thumb for a ``SelfRelativeRef`` with origin ``O``
        and target ``T``:

        * Target moves   (T >= shift_start)   but origin doesn't
          (O < shift_start): rewrite needed (field goes up).
        * Origin moves but target doesn't: rewrite needed (field
          goes down).
        * Both move together OR both stay: no rewrite.

        For a :class:`FileAbsoluteRef`, rewrite iff the **target**
        moves (because the field IS the absolute offset).

        Refs with ``target_offset is None`` are skipped — sentinel
        / empty slots have nothing to fix.
        """
        from azurik_mod.xbr.refs import (
            FileAbsoluteRef,
            PoolOffsetRef,
            SelfRelativeRef,
        )
        to_patch: list[ResolvedRef] = []
        for rr in self._resolved:
            if rr.target_offset is None:
                continue
            tgt = rr.target_offset
            target_moves = tgt >= shift_start

            if isinstance(rr.ref, FileAbsoluteRef):
                if target_moves:
                    to_patch.append(rr)
                continue
            if isinstance(rr.ref, PoolOffsetRef):
                # Pool refs: target moves rewrites the field; pool
                # base moving would need to be signalled separately.
                if target_moves:
                    to_patch.append(rr)
                continue
            if isinstance(rr.ref, SelfRelativeRef):
                origin_moves = rr.ref.origin_offset >= shift_start
                if origin_moves != target_moves:
                    to_patch.append(rr)
                continue
            # Unknown ref type — conservative: flag for review.
            to_patch.append(rr)
        return to_patch

    # ------------------------------------------------------------------
    # Snapshot / drift guard
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Serialise the graph in a JSON-friendly form.

        The ``xbr_graph_snapshot.json`` drift guard (Phase 1) pins
        this output so structural changes to the parser surface in
        code review.
        """
        by_tag: dict[str, list[dict]] = {}
        for rr in self._resolved:
            tag = rr.ref.owner_tag or "?"
            by_tag.setdefault(tag, []).append({
                "kind": type(rr.ref).__name__,
                "src_offset": f"0x{rr.ref.src_offset:08X}",
                "width": rr.ref.width,
                "target_offset": (
                    f"0x{rr.target_offset:08X}"
                    if rr.target_offset is not None else None),
            })
        # Stable ordering for deterministic snapshots.
        summary = {
            "toc_entries": len(self.document.toc),
            "ref_counts": {tag: len(refs) for tag, refs in by_tag.items()},
            "total_refs": len(self._resolved),
        }
        return {"summary": summary}


def iter_refs(document: "XbrDocument") -> Iterable[Ref]:
    """Convenience: yield every ref across every section in the
    document.  Same as :meth:`PointerGraph.__iter__` but skips the
    target-resolution work when callers don't need it."""
    for section in document.sections():
        yield from section.iter_refs()
