"""Typed pointer fields inside an XBR.

Every embedded pointer in an XBR falls into one of a few categories:

- :class:`SelfRelativeRef` — the field at ``src_offset`` is a 32-bit
  unsigned delta added to some origin (usually the field's own
  address plus a small constant) to reach the target.  Keyed-table
  row-name pointers and type-2 cell string pointers are both this
  shape.

- :class:`FileAbsoluteRef` — the field holds an absolute file
  offset (rare in vanilla XBRs but shows up in the TOC
  ``file_offset`` column and, speculatively, in some level-XBR
  sections).

- :class:`TocEntryRef` — the field holds an index into the TOC
  (not encountered in the reversed sections today; declared so the
  graph can model them when a section type that uses them is
  reversed).

- :class:`PoolOffsetRef` — the field holds an offset relative to a
  dedicated string / data pool base somewhere else in the file
  (e.g. ``index.xbr``'s ``off1`` / ``off2`` columns).

Each ref knows how to read its target offset out of the document's
byte buffer and — critically — how to **rewrite** its field when the
target address moves.  The rewrite half is what makes structural
edits (grow pool, add row) tractable: the :class:`PointerGraph`
walks every ref and patches only the ones whose target crossed the
shifted region.

Phase 0 models the refs keyed-tables need; additional types are
added as new section formats are reversed.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Ref:
    """Base class for every XBR pointer field.

    Concrete subclasses override :meth:`target_file_offset`,
    :meth:`rewrite`, and optionally :meth:`describe`.  ``owner_tag``
    is the TOC tag of the section that owns this ref — handy for
    debugging and for the ``xbr xref`` CLI.
    """

    src_offset: int
    """Absolute file offset where the pointer field starts."""

    width: int = 4
    """Size of the pointer field in bytes.  Vanilla Azurik XBRs use
    32-bit offsets throughout; 64-bit variants are not currently
    seen."""

    owner_tag: str = ""
    """TOC tag of the section this ref lives inside.  Populated by
    the document loader; empty string when the ref was constructed
    standalone (e.g. in unit tests)."""

    def target_file_offset(self, data: bytes) -> Optional[int]:
        """Return the absolute file offset this ref resolves to,
        or ``None`` when the ref is a sentinel / empty slot.

        Concrete subclasses override this.  The default raises.
        """
        raise NotImplementedError

    def rewrite(self, data: bytearray, new_target_file_offset: int) -> None:
        """Patch the pointer field so it resolves to
        ``new_target_file_offset``.  Concrete subclasses override.
        """
        raise NotImplementedError

    def describe(self) -> str:
        """Short one-line description — used by ``xbr xref`` output."""
        return f"{type(self).__name__}@0x{self.src_offset:08X}"


@dataclass(frozen=True)
class SelfRelativeRef(Ref):
    """A 32-bit delta added to ``origin_offset`` to get the target.

    In the keyed-table format, row names use ``origin_offset =
    entry_addr + 4`` (the ``+4`` skipping the unused first u32 of
    the row header); type-2 cell strings use ``origin_offset =
    cell_addr + 12`` (the offset lives at cell+12 and is relative
    to its own address).
    """

    origin_offset: int = 0
    """Absolute file offset that the stored delta is relative to."""

    def target_file_offset(self, data: bytes) -> Optional[int]:
        rel = struct.unpack_from("<I", data, self.src_offset)[0]
        if rel == 0:
            return None
        return self.origin_offset + rel

    def rewrite(self, data: bytearray, new_target_file_offset: int) -> None:
        rel = new_target_file_offset - self.origin_offset
        if rel < 0 or rel > 0xFFFFFFFF:
            raise ValueError(
                f"SelfRelativeRef @0x{self.src_offset:08X}: new target "
                f"0x{new_target_file_offset:08X} out of range for "
                f"origin 0x{self.origin_offset:08X} (delta {rel})")
        struct.pack_into("<I", data, self.src_offset, rel)

    def describe(self) -> str:
        return (f"SelfRelativeRef@0x{self.src_offset:08X} "
                f"(origin=0x{self.origin_offset:08X})")


@dataclass(frozen=True)
class FileAbsoluteRef(Ref):
    """The field's raw u32 value IS the absolute file offset."""

    def target_file_offset(self, data: bytes) -> Optional[int]:
        return struct.unpack_from("<I", data, self.src_offset)[0]

    def rewrite(self, data: bytearray, new_target_file_offset: int) -> None:
        if new_target_file_offset < 0 or new_target_file_offset > 0xFFFFFFFF:
            raise ValueError(
                f"FileAbsoluteRef @0x{self.src_offset:08X}: new target "
                f"0x{new_target_file_offset:X} out of 32-bit range")
        struct.pack_into("<I", data, self.src_offset,
                         new_target_file_offset)


@dataclass(frozen=True)
class TocEntryRef(Ref):
    """The field holds an index into the TOC table.

    Provided for completeness; no currently-reversed section format
    uses this shape.  The :meth:`target_file_offset` resolves via the
    document's TOC which the caller must supply out-of-band
    (see :class:`azurik_mod.xbr.pointer_graph.PointerGraph`).
    """

    toc_index: int = 0
    """Cached TOC index this ref resolves to (populated by the
    section loader; authoritative source is the document's TOC
    list)."""

    def target_file_offset(self, data: bytes) -> Optional[int]:
        # Can't resolve without the TOC; callers that need this
        # field go through PointerGraph which has the TOC handy.
        raise NotImplementedError(
            "TocEntryRef resolution requires the document's TOC; "
            "call PointerGraph.resolve(ref) instead")

    def rewrite(self, data: bytearray, new_target_file_offset: int) -> None:
        raise NotImplementedError(
            "TocEntryRef rewrites go through the TOC, not the raw field")


@dataclass(frozen=True)
class PoolOffsetRef(Ref):
    """The field holds an offset into a named pool region.

    ``pool_base_file_offset`` is the absolute file offset of the
    pool's first byte.  Semantics match :class:`SelfRelativeRef`
    mathematically but the graph treats them differently: pool
    offsets move when the pool grows, self-relative offsets move
    only when their origin moves relative to their target.
    """

    pool_base_file_offset: int = 0

    def target_file_offset(self, data: bytes) -> Optional[int]:
        rel = struct.unpack_from("<I", data, self.src_offset)[0]
        return self.pool_base_file_offset + rel

    def rewrite(self, data: bytearray, new_target_file_offset: int) -> None:
        rel = new_target_file_offset - self.pool_base_file_offset
        if rel < 0 or rel > 0xFFFFFFFF:
            raise ValueError(
                f"PoolOffsetRef @0x{self.src_offset:08X}: new target "
                f"0x{new_target_file_offset:08X} out of range for "
                f"pool base 0x{self.pool_base_file_offset:08X}")
        struct.pack_into("<I", data, self.src_offset, rel)
