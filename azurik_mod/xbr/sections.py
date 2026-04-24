"""Typed overlays over :class:`~azurik_mod.xbr.document.XbrDocument`'s
shared byte buffer.

Each subclass of :class:`Section` is a *view* — it reads and can
mutate a contiguous slice of the document's ``bytearray``, but it
doesn't own those bytes.  Round-trip byte identity is maintained
by never rewriting bytes the overlay isn't explicitly editing;
:meth:`XbrDocument.dumps` just hands back the buffer itself.

Sections are lazily constructed the first time a caller asks for
them via :meth:`XbrDocument.section_for` so the common case of
"load + inspect TOC + write back" costs no per-section parsing.

For tags the platform doesn't model yet, :class:`RawSection` is
the safe fallback — it's just a byte slice with no pointer graph.
"""

from __future__ import annotations

import re
import struct
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Optional

from azurik_mod.xbr.refs import (
    Ref,
    SelfRelativeRef,
)

if TYPE_CHECKING:
    from azurik_mod.xbr.document import TocEntry, XbrDocument


class Section(ABC):
    """Base for every typed XBR section.

    Holds a back-reference to the owning :class:`XbrDocument` plus
    the TOC entry it was constructed from.  Subclasses expose
    format-specific read / write helpers.

    The :meth:`iter_refs` method yields every pointer field this
    section contains; the :class:`~azurik_mod.xbr.pointer_graph.PointerGraph`
    walks all sections' refs to answer structural-edit queries.
    """

    def __init__(self, document: "XbrDocument", entry: "TocEntry") -> None:
        self.document = document
        self.entry = entry

    @property
    def data(self) -> bytes:
        """Bytes for this section — a fresh copy, safe to hand out
        without risk of mutation through the view."""
        return bytes(self.raw_buffer)

    @property
    def raw_buffer(self) -> memoryview:
        """Writable view into the document's buffer covering just
        this section.  Zero-copy; edits through it mutate the
        document."""
        start = self.entry.file_offset
        end = start + self.entry.size
        # memoryview on bytearray = writable slice with no copy.
        return memoryview(self.document.raw)[start:end]

    @abstractmethod
    def iter_refs(self) -> Iterable[Ref]:
        """Enumerate every pointer field inside this section."""


# ---------------------------------------------------------------------------
# Raw section — fallback for unreversed tag types.
# ---------------------------------------------------------------------------


class RawSection(Section):
    """Opaque byte slice.  No pointer graph, no structural edits.

    Used for every TOC tag the platform hasn't reversed yet.  Round-
    trip-safe because the bytes are left entirely untouched.
    """

    def iter_refs(self) -> Iterable[Ref]:
        return iter(())


# ---------------------------------------------------------------------------
# Keyed-table sections (config.xbr — 15 of 18)
# ---------------------------------------------------------------------------


# Section-local offset (within the section's TOC payload) where the
# 20-byte table header starts.  Config.xbr's keyed sections sit in
# fixed 0x1000 slots so the string pool fills the first 0x1000 bytes
# and the table header lives at ``section_start + 0x1000``.
_KEYED_TABLE_HEADER_SKEW = 0x1000


@dataclass
class _KeyedCell:
    """Decoded cell.  ``file_offset`` is absolute."""

    file_offset: int
    type_code: int       # 0 empty, 1 double, 2 string, else unknown
    double_value: Optional[float] = None
    string_value: Optional[str] = None
    string_length: Optional[int] = None
    string_file_offset: Optional[int] = None


class KeyedTableSection(Section):
    """Column-major grid of typed cells — the ``tabl``-ish layout used
    by config.xbr.

    **Binary layout** (section-local offsets unless stated):

    ===================  ================================================
    0x0000 .. 0x0FFF     String pool (NUL-terminated ASCII entity /
                         property names).
    0x1000 .. 0x1013     Table header, five ``u32`` fields:
                         ``num_rows``, ``row_hdr_offset`` (always 0x10),
                         ``num_cols``, ``total_cells`` (= rows*cols),
                         ``cell_data_off``.
    0x1000 + 0x14 + r*8  Row header record (8 bytes each, ``num_rows``
                         entries).  First 4 bytes unused; next 4 bytes
                         are a self-relative u32 to the row name
                         string (origin = entry_addr + 4).
    0x1000 + cell_off +  Cell data grid — column-major, 16 bytes per
    0x10 + ...           cell.  Column i occupies rows ``i*num_rows
                         .. (i+1)*num_rows``.
    (after cells)        String data for type-2 cells (self-relative
                         offsets from cell + 12).
    ===================  ================================================

    **Pointer graph**:

    - Row-name pointer at ``row_entry + 4`` (origin = ``row_entry + 4``).
    - Type-2 cell string-length ``u32`` at ``cell + 8`` (data, not a ref).
    - Type-2 cell string offset at ``cell + 12`` (origin = ``cell + 12``).

    The overlay is read-only for phase 0; :mod:`azurik_mod.xbr.edits`
    adds the structural primitives on top.
    """

    def __init__(self, document: "XbrDocument", entry: "TocEntry") -> None:
        super().__init__(document, entry)
        self._section_start = entry.file_offset
        self._table_base = self._section_start + _KEYED_TABLE_HEADER_SKEW

        # Decode header.  Tolerate shorter-than-header slots (e.g. stub
        # tables in non-config XBRs that happen to share the tag); we
        # just surface num_rows = 0 and let callers skip.
        buf = document.raw
        if self._table_base + 20 > len(buf):
            self.num_rows = 0
            self.row_hdr_offset = 0
            self.num_cols = 0
            self.total_cells = 0
            self.cell_data_off = 0
            return
        (self.num_rows, self.row_hdr_offset,
         self.num_cols, self.total_cells,
         self.cell_data_off) = struct.unpack_from(
            "<5I", buf, self._table_base)

    @property
    def table_base(self) -> int:
        """Absolute file offset of the 20-byte table header."""
        return self._table_base

    def is_well_formed(self) -> bool:
        """True if the header's dimension fields are self-consistent.

        Used by the auto-detection pass in :class:`XbrDocument` to
        decide whether to treat this entry as a real keyed table or
        fall back to :class:`RawSection`.
        """
        return (self.num_rows > 0 and self.num_cols > 0
                and self.num_rows * self.num_cols == self.total_cells)

    # ------------------------------------------------------------------
    # Read-side helpers
    # ------------------------------------------------------------------

    def row_header_addr(self, row: int) -> int:
        """Absolute file offset of the row-header record for row ``row``."""
        return self._table_base + self.row_hdr_offset + 4 + row * 8

    def row_name_ref(self, row: int) -> SelfRelativeRef:
        """Return a :class:`SelfRelativeRef` to the row-name string
        pointer for row ``row``.  Origin = ``row_header + 4``."""
        base = self.row_header_addr(row)
        return SelfRelativeRef(
            src_offset=base + 4,
            width=4,
            owner_tag=self.entry.tag,
            origin_offset=base + 4,
        )

    def row_name(self, row: int) -> str:
        """Decoded property name for a row index."""
        ref = self.row_name_ref(row)
        tgt = ref.target_file_offset(self.document.raw)
        if tgt is None:
            return ""
        return _read_cstring(self.document.raw, tgt)

    def cell_addr(self, col: int, row: int) -> int:
        """Absolute file offset of the 16-byte cell at ``(col, row)``."""
        return (self._table_base + self.cell_data_off + 0x10
                + (self.num_rows * col + row) * 16)

    def read_cell(self, col: int, row: int) -> _KeyedCell:
        """Decoded cell payload.  Empty cells return ``type_code=0``
        with all payload fields ``None``."""
        addr = self.cell_addr(col, row)
        buf = self.document.raw
        type_code = struct.unpack_from("<I", buf, addr)[0]
        cell = _KeyedCell(file_offset=addr, type_code=type_code)
        if type_code == 1:
            cell.double_value = struct.unpack_from("<d", buf, addr + 8)[0]
        elif type_code == 2:
            cell.string_length = struct.unpack_from("<I", buf, addr + 8)[0]
            rel = struct.unpack_from("<I", buf, addr + 12)[0]
            cell.string_file_offset = addr + 12 + rel
            cell.string_value = _read_cstring(buf, cell.string_file_offset)
        return cell

    def cell_string_ref(self, col: int, row: int) -> Optional[SelfRelativeRef]:
        """Return the :class:`SelfRelativeRef` for a type-2 cell's
        string, or ``None`` when the cell is not a string."""
        cell_addr = self.cell_addr(col, row)
        buf = self.document.raw
        if struct.unpack_from("<I", buf, cell_addr)[0] != 2:
            return None
        return SelfRelativeRef(
            src_offset=cell_addr + 12,
            width=4,
            owner_tag=self.entry.tag,
            origin_offset=cell_addr + 12,
        )

    def iter_refs(self) -> Iterable[Ref]:
        if not self.is_well_formed():
            return
        for r in range(self.num_rows):
            yield self.row_name_ref(r)
        for c in range(self.num_cols):
            for r in range(self.num_rows):
                ref = self.cell_string_ref(c, r)
                if ref is not None:
                    yield ref

    # ------------------------------------------------------------------
    # Index helpers — convenient for callers that want to look up a
    # cell by (entity name, property name) without building a full
    # legacy KeyedTable instance.
    # ------------------------------------------------------------------

    def row_names(self) -> list[str]:
        """List of property (row) names in row order."""
        return [self.row_name(r) for r in range(self.num_rows)]

    def col_names(self) -> list[str]:
        """List of entity (column) names in column order.

        Column names come from row 0 (``"name"`` row by convention);
        empty / non-string cells fall back to ``"col_<idx>"``.
        """
        names: list[str] = []
        for c in range(self.num_cols):
            cell = self.read_cell(c, 0)
            if cell.type_code == 2 and cell.string_value is not None:
                names.append(cell.string_value)
            else:
                names.append(f"col_{c}")
        return names

    def find_cell(self, entity: str, prop: str) -> Optional[_KeyedCell]:
        """Look up a cell by entity (column) name + property (row)
        name.  Returns ``None`` when either axis is absent."""
        try:
            row = self.row_names().index(prop)
        except ValueError:
            return None
        cols = self.col_names()
        try:
            col = cols.index(entity)
        except ValueError:
            return None
        return self.read_cell(col, row)


# ---------------------------------------------------------------------------
# config.xbr keyed-table section offsets
# ---------------------------------------------------------------------------
#
# Pinned map of section name → absolute file offset inside vanilla
# ``config.xbr``.  The canonical reference copy lives in
# ``scripts/xbr_parser.KEYED_SECTION_OFFSETS`` (historical — it
# predates the :mod:`azurik_mod.xbr` package), but ``scripts/`` is
# **not** part of the installed wheel (see ``pyproject.toml`` §
# ``[tool.setuptools.packages.find]``).  So we redeclare the table
# here in the runtime package to keep the document model importable
# when the user runs ``azurik-mod`` / ``azurik-gui`` from an
# installed package with no ``scripts/`` in sight.
#
# Drift between this copy and the ``scripts/`` one is guarded by
# :class:`tests.test_xbr_document_roundtrip.KeyedSectionOffsetsDrift`
# so the two can never silently diverge.


# NOTE on the ``armor_*`` entries below: the TOC tag at 0x002000 is
# ``armor_hit_fx`` and the one at 0x004000 is ``armor_properties``,
# which would suggest the "flap count" armor data lives in the latter.
# Decompile of the runtime config loader (``FUN_00049480`` reads
# ``config/armor_properties``) shows the opposite: the **engine-read**
# grid is the 15x19 table at +0x1000 inside the 0x002000 TOC entry
# (i.e. the one labelled ``armor_hit_fx``), while the 16x24 grid at
# 0x004000 is dead data that nothing references at runtime.  The XBR
# Editor / pack authors want those two tables to be labelled by what
# the engine actually does with them — hence ``armor_properties_real``
# and ``armor_properties_unused``.  See ``docs/LEARNINGS.md`` §
# "armor_hit_fx vs armor_properties".
_KEYED_SECTION_OFFSETS: dict[str, int] = {
    "armor_properties_real":   0x002000,
    "armor_properties_unused": 0x004000,
    "attacks_anims":           0x006000,
    "attacks_transitions":     0x008000,
    "critters_critter_data":   0x01A000,
    "critters_damage":         0x035000,
    "critters_damage_fx":      0x044000,
    "critters_engine":         0x05A000,
    "critters_flocking":       0x05D000,
    "critters_item_data":      0x060000,
    "critters_maya_stuff":     0x065000,
    "critters_mutate":         0x066000,
    "critters_sounds":         0x077000,
    "critters_special_anims":  0x07A000,
    "magic":                   0x087000,
}


# Section names that are in the file but which the engine never reads.
# The XBR Editor uses this set to pop a warning banner when the user
# opens one of these sections so casual editors don't burn an hour
# tuning values that do nothing at runtime.  Authored alongside
# :data:`_KEYED_SECTION_OFFSETS` so the two stay in lockstep.
DEAD_SECTION_NAMES: frozenset[str] = frozenset({
    "armor_properties_unused",
})


# ---------------------------------------------------------------------------
# Variant-record sections (config.xbr — 3 of 18)
# ---------------------------------------------------------------------------


# Known variant-record section layouts.  These were reversed alongside
# the keyed-tables by ``scripts/xbr_parser.py`` — see ``VARIANT_SCHEMAS``
# there for the canonical ground truth.  We re-declare here (rather
# than importing) to avoid pulling ``scripts/`` onto the import path;
# a drift guard in :mod:`tests.test_xbr_document_roundtrip` compares
# the two tables at collection time.
_VARIANT_SCHEMAS: dict[str, dict] = {
    "critters_walking": {
        "section_offset": 0x083000,
        "record_base":    0x084090,
        "entity_count":   107,
        "props_per_entity": 18,
        "record_size":    16,
    },
    "damage": {
        "section_offset": 0x086000,
        "record_base":    0x086000,
        "entity_count":   11,
        "props_per_entity": 8,
        "record_size":    16,
    },
    "settings_foo": {
        "section_offset": 0x088300,
        "record_base":    0x088300,
        "entity_count":   1,
        "props_per_entity": 6,
        "record_size":    48,
    },
}


class VariantRecordSection(Section):
    """Fixed-stride record arrays used by ``critters_walking`` /
    ``damage`` / ``settings_foo`` in config.xbr.

    Numeric values only — string pointers (if any) inside the
    record bodies are not yet reversed, so :meth:`iter_refs`
    currently yields nothing.  That's safe for structural edits
    *within* a record (the 16/48-byte stride is rigid), but means
    we can't resize the section without additional RE.
    """

    def __init__(
        self,
        document: "XbrDocument",
        entry: "TocEntry",
        schema: dict,
    ) -> None:
        super().__init__(document, entry)
        self.schema = schema
        self.entity_count = int(schema["entity_count"])
        self.props_per_entity = int(schema["props_per_entity"])
        self.record_size = int(schema["record_size"])
        self.record_base = int(schema["record_base"])

    def stride(self) -> int:
        return self.props_per_entity * self.record_size

    def record_offset(self, entity_idx: int, prop_idx: int) -> int:
        return (self.record_base + entity_idx * self.stride()
                + prop_idx * self.record_size)

    def value_offset(self, entity_idx: int, prop_idx: int) -> int:
        rec = self.record_offset(entity_idx, prop_idx)
        # 48-byte records have a 16-byte prefix before the
        # conventional ``+4`` value slot.
        return rec + 16 + 4 if self.record_size == 48 else rec + 4

    def read_value(self, entity_idx: int, prop_idx: int) -> Optional[float]:
        off = self.value_offset(entity_idx, prop_idx)
        buf = self.document.raw
        if off + 8 > len(buf):
            return None
        return struct.unpack_from("<d", buf, off)[0]

    def iter_refs(self) -> Iterable[Ref]:
        # Pointer fields in variant records are not yet reversed.
        # The section is structurally rigid so this is safe — edits
        # that stay within the existing record layout don't need
        # pointer fixups.
        return iter(())


# ---------------------------------------------------------------------------
# Index-records section (index.xbr)
# ---------------------------------------------------------------------------


class IndexRecordsSection(Section):
    """``indx`` payload: header + 20-byte records + string pool.

    The pointer-base math for the ``off1`` / ``off2`` columns is
    not fully pinned (:mod:`azurik_mod.assets.index_xbr` documents
    what's known).  Phase 0 treats the records as raw 20-byte
    rows for round-trip purposes; future phases can overlay
    :class:`~azurik_mod.xbr.refs.PoolOffsetRef` fields on them
    once the base is reversed.
    """

    def __init__(self, document: "XbrDocument", entry: "TocEntry") -> None:
        super().__init__(document, entry)
        self._payload_start = entry.file_offset
        buf = document.raw
        if self._payload_start + 16 > len(buf):
            self.count = 0
            self.version = 0
            self.header_hint = 0
            self.pool_hint = 0
            self.records_start = self._payload_start
            return
        (self.count, self.version,
         self.header_hint, self.pool_hint) = struct.unpack_from(
            "<4I", buf, self._payload_start)
        self.records_start = self._payload_start + 16

    RECORD_SIZE = 20

    def record_offset(self, index: int) -> int:
        return self.records_start + index * self.RECORD_SIZE

    def iter_refs(self) -> Iterable[Ref]:
        # off1 / off2 reversed but their exact pool base isn't
        # pinned — see azurik_mod/assets/index_xbr.py docstring.
        return iter(())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_cstring(data, offset: int, max_len: int = 512) -> str:
    """Read a NUL-terminated ASCII string from ``data`` at
    ``offset``.  Returns the decoded string without its NUL.

    ``data`` may be bytes, bytearray, or memoryview.  Returns the
    empty string when ``offset`` is past the buffer.
    """
    if offset < 0 or offset >= len(data):
        return ""
    end = offset
    lim = min(len(data), offset + max_len)
    while end < lim and data[end] != 0:
        end += 1
    return bytes(data[offset:end]).decode("ascii", errors="replace")


# Exported for keyed-table callers who want a fresh ``str -> offset``
# map of every NUL-terminated ASCII string that appears inside this
# section's first 0x1000 pool region.  Used by
# :mod:`azurik_mod.xbr.edits` to find existing strings before
# duplicating them.
def scan_string_pool(data, start: int, length: int,
                     min_len: int = 1) -> dict[str, int]:
    """Return ``{string: absolute_file_offset}`` for every ASCII
    NUL-terminated string of length ``>= min_len`` inside
    ``[start, start+length)``.  When the same string appears multiple
    times, the first occurrence wins.
    """
    end = min(start + length, len(data))
    out: dict[str, int] = {}
    pat = re.compile(rb"([\x20-\x7E]{%d,})\x00" % max(1, min_len))
    for m in pat.finditer(bytes(data[start:end])):
        s = m.group(1).decode("ascii")
        if s not in out:
            out[s] = start + m.start(1)
    return out


# The ``field`` import is used in downstream overlays; keep it in the
# module's public surface so subclasses in
# :mod:`azurik_mod.xbr.edits` can reuse it.
__all__ = [
    "IndexRecordsSection",
    "KeyedTableSection",
    "RawSection",
    "Section",
    "VariantRecordSection",
    "scan_string_pool",
]
