"""Structural XBR document model — phase 0 of the XBR modding platform.

The :class:`XbrDocument` is the canonical in-memory representation of
any Azurik ``.xbr`` file.  It holds the raw bytes plus a parsed TOC
view; :meth:`XbrDocument.dumps` returns the underlying buffer so
byte-identity round-tripping is trivial.

Section subclasses (:class:`KeyedTableSection`,
:class:`VariantRecordSection`, :class:`IndexRecordsSection`,
:class:`RawSection`) are **overlays** over the shared buffer — they
read and can mutate the same bytes the document owns, so edits
applied through the overlay are reflected in
``XbrDocument.dumps()`` without an explicit re-serialise step.

Structural edits (grow string pool, add/remove rows) live in
:mod:`azurik_mod.xbr.edits` and route through the document so the
pointer graph stays consistent.

This module intentionally stays thin — every piece of reverse-
engineered knowledge lives in the format-specific section
subclass.  ``RawSection`` is the safe fallback for unreversed
tag types; it preserves bytes verbatim.

Public API:

- :class:`XbrDocument`
- :class:`TocEntry`
- :class:`Section`, :class:`KeyedTableSection`,
  :class:`VariantRecordSection`, :class:`IndexRecordsSection`,
  :class:`RawSection`
- :class:`Ref`, :class:`SelfRelativeRef`, :class:`FileAbsoluteRef`
- :class:`PointerGraph`
"""

from __future__ import annotations

from azurik_mod.xbr.document import TocEntry, XbrDocument
from azurik_mod.xbr.pointer_graph import PointerGraph
from azurik_mod.xbr.refs import (
    FileAbsoluteRef,
    PoolOffsetRef,
    Ref,
    SelfRelativeRef,
    TocEntryRef,
)
from azurik_mod.xbr.sections import (
    IndexRecordsSection,
    KeyedTableSection,
    RawSection,
    Section,
    VariantRecordSection,
)

__all__ = [
    "FileAbsoluteRef",
    "IndexRecordsSection",
    "KeyedTableSection",
    "PointerGraph",
    "PoolOffsetRef",
    "RawSection",
    "Ref",
    "Section",
    "SelfRelativeRef",
    "TocEntry",
    "TocEntryRef",
    "VariantRecordSection",
    "XbrDocument",
]
