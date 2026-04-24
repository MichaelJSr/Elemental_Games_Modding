"""In-memory representation of a whole .xbr file.

See :mod:`azurik_mod.xbr` for the big-picture overview.

Byte-identity round-trip is the cornerstone property: ``XbrDocument
.load(p).dumps() == p.read_bytes()`` must hold for **every** vanilla
Azurik XBR.  The model enforces this by keeping the underlying
buffer verbatim — sections are mutable overlays, not owners, and
the TOC is a parsed view we only re-serialise on demand.

The :attr:`XbrDocument.raw` ``bytearray`` is the source of truth.
:meth:`dumps` returns ``bytes(self.raw)``.  Edits that mutate
anything other than the TOC (e.g. inside a section) simply mutate
``self.raw`` in place, and :meth:`dumps` reflects them for free.

Edits that change the TOC itself go through :meth:`_rewrite_toc`
which packs the Python TOC list back into its on-disk layout — it
preserves the terminator sentinel and any trailing bytes that the
vanilla file keeps between the terminator and the first section.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Union

from azurik_mod.xbr.sections import (
    IndexRecordsSection,
    KeyedTableSection,
    RawSection,
    Section,
    VariantRecordSection,
    _KEYED_SECTION_OFFSETS,
    _VARIANT_SCHEMAS,
)


# ---------------------------------------------------------------------------
# Module-level cached reverse maps
# ---------------------------------------------------------------------------

_CONFIG_XBR_OFFSET_TO_NAME_CACHE: dict[int, str] | None = None


def _config_xbr_offset_to_name() -> dict[int, str]:
    """Return the reverse map ``{file_offset: section_name}`` for
    config.xbr's keyed-table sections.

    Memoised at module scope — dict construction only happens
    once per process, not per :meth:`keyed_sections` call.
    Sources the table from
    :data:`azurik_mod.xbr.sections._KEYED_SECTION_OFFSETS` (a
    package-local copy) rather than the historical
    ``scripts.xbr_parser`` module so the runtime stays importable
    from a ``pip install``-ed package where ``scripts/`` isn't
    bundled.  Drift between the two copies is caught by the test
    suite.
    """
    global _CONFIG_XBR_OFFSET_TO_NAME_CACHE
    if _CONFIG_XBR_OFFSET_TO_NAME_CACHE is None:
        _CONFIG_XBR_OFFSET_TO_NAME_CACHE = {
            off: name for name, off in _KEYED_SECTION_OFFSETS.items()
        }
    return _CONFIG_XBR_OFFSET_TO_NAME_CACHE

# --- Header constants ------------------------------------------------------

HEADER_SIZE = 0x40
"""Bytes reserved for the XBR header.  Most of this region is
unreversed; only :data:`HEADER_TOC_COUNT_OFFSET` is known to be
meaningful."""

HEADER_TOC_COUNT_OFFSET = 0x0C
"""u32 field inside the header holding the number of TOC entries
(not counting the terminator row)."""

TOC_START = HEADER_SIZE
"""Absolute file offset where the first TOC row starts."""

TOC_ROW_SIZE = 16
"""Size of a single TOC row: ``u32 size, char[4] tag, u32 flags,
u32 file_offset``."""

MAGIC = b"xobx"
"""Magic bytes at file offset 0x00."""


@dataclass
class TocEntry:
    """One 16-byte TOC row.

    Attributes:
        index:        Position in the TOC (0-based).  Informational —
                      regenerated on write.
        size:         Payload byte count the entry covers.
        tag:          4-char ASCII tag (``"node"``, ``"surf"``,
                      ``"tabl"`` …).
        flags:        Opaque flags u32.  Preserved verbatim.
        file_offset:  Absolute file offset of the payload.
    """

    index: int
    size: int
    tag: str
    flags: int
    file_offset: int


class XbrDocument:
    """Canonical parsed view of a single .xbr file.

    Construct via :meth:`load` (from a path) or :meth:`from_bytes`.
    Re-serialise via :meth:`dumps` / :meth:`write`.  Sections are
    lazily constructed — pay the parsing cost only for the ones
    you actually touch.
    """

    # Known TOC tags we have structural overlays for.  When a future
    # phase adds a new Section subclass, wire it in via
    # :meth:`_build_section` so callers get richer semantics for
    # free.  Unrecognised tags fall back to :class:`RawSection`.

    def __init__(
        self,
        raw: Union[bytes, bytearray],
        path: Optional[Path] = None,
    ) -> None:
        # Always hold a bytearray so section overlays can mutate it.
        self.raw = bytearray(raw)
        self.path = path
        self._validate_header()
        self.toc = self._parse_toc()
        self._section_cache: dict[int, Section] = {}

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Union[str, Path]) -> "XbrDocument":
        """Load an XBR file from disk.

        Raises :class:`ValueError` if the magic is wrong.
        """
        p = Path(path)
        return cls(p.read_bytes(), path=p)

    @classmethod
    def from_bytes(cls, raw: Union[bytes, bytearray]) -> "XbrDocument":
        """Construct a document from an in-memory buffer (tests,
        fixtures, etc.)."""
        return cls(raw)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def dumps(self) -> bytes:
        """Return the current byte image of the document.

        For a freshly-loaded, unedited document this is byte-
        identical to the file on disk — that's enforced by
        :mod:`tests.test_xbr_document_roundtrip`.
        """
        return bytes(self.raw)

    def write(self, path: Union[str, Path]) -> None:
        """Commit :meth:`dumps` to ``path``.  Parent directory is
        created if missing."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.dumps())

    # ------------------------------------------------------------------
    # Header access
    # ------------------------------------------------------------------

    @property
    def header(self) -> bytes:
        """Raw 0x40 header bytes.  Preserved verbatim on re-emit."""
        return bytes(self.raw[:HEADER_SIZE])

    @property
    def header_toc_count(self) -> int:
        """TOC count declared in the header at 0x0C."""
        return struct.unpack_from(
            "<I", self.raw, HEADER_TOC_COUNT_OFFSET)[0]

    def _validate_header(self) -> None:
        # Only the magic is mandatory.  Vanilla Azurik ships a few
        # stub XBRs (``loc.xbr``, some dev artefacts) that are
        # shorter than the full 0x40 header — they still start with
        # the magic and must round-trip losslessly.  Shorter files
        # just produce an empty TOC; the buffer is preserved verbatim.
        if len(self.raw) < 4:
            raise ValueError(
                f"XBR too short to carry magic: {len(self.raw)} B")
        if bytes(self.raw[:4]) != MAGIC:
            raise ValueError(
                f"Bad XBR magic: {bytes(self.raw[:4])!r} "
                f"(expected {MAGIC!r})")

    # ------------------------------------------------------------------
    # TOC parsing + packing
    # ------------------------------------------------------------------

    def _parse_toc(self) -> list[TocEntry]:
        """Walk the TOC starting at 0x40 until we hit the zero-row
        sentinel.

        Preserves every non-sentinel row in declaration order.  The
        parser matches :func:`scripts.xbr_parser.parse_toc` byte-for-
        byte (drift-guard in tests/test_xbr_document_roundtrip.py).

        Files shorter than ``TOC_START`` yield an empty TOC — a few
        stub XBRs (``loc.xbr``) ship without a real TOC.
        """
        entries: list[TocEntry] = []
        if len(self.raw) < TOC_START:
            return entries
        off = TOC_START
        while off + TOC_ROW_SIZE <= len(self.raw):
            size = struct.unpack_from("<I", self.raw, off)[0]
            tag_raw = bytes(self.raw[off + 4:off + 8])
            flags = struct.unpack_from("<I", self.raw, off + 8)[0]
            file_offset = struct.unpack_from(
                "<I", self.raw, off + 12)[0]
            if size == 0 and flags == 0 and file_offset == 0:
                break
            try:
                tag = tag_raw.decode("ascii")
            except (UnicodeDecodeError, ValueError):
                tag = tag_raw.hex()
            entries.append(TocEntry(
                index=len(entries),
                size=size,
                tag=tag,
                flags=flags,
                file_offset=file_offset,
            ))
            off += TOC_ROW_SIZE
        return entries

    def _rewrite_toc(self) -> None:
        """Serialise :attr:`toc` back into the TOC region.

        Preserves any bytes BETWEEN the terminator sentinel and the
        first section payload (vanilla XBRs often leave a zero gap
        that we don't want to disturb).  Called by structural edits
        in :mod:`azurik_mod.xbr.edits`; Phase 0 never invokes it.
        """
        off = TOC_START
        for entry in self.toc:
            struct.pack_into(
                "<I", self.raw, off, entry.size)
            tag_bytes = entry.tag.encode("ascii").ljust(4, b" ")[:4]
            self.raw[off + 4:off + 8] = tag_bytes
            struct.pack_into(
                "<I", self.raw, off + 8, entry.flags)
            struct.pack_into(
                "<I", self.raw, off + 12, entry.file_offset)
            off += TOC_ROW_SIZE
        # Terminator sentinel.
        struct.pack_into(
            "<IIII", self.raw, off, 0, 0, 0, 0)
        # Header's TOC count field.
        struct.pack_into(
            "<I", self.raw, HEADER_TOC_COUNT_OFFSET, len(self.toc))

    # ------------------------------------------------------------------
    # Section access
    # ------------------------------------------------------------------

    def section_for(self, index: int) -> Section:
        """Return (lazily construct) the typed overlay for the TOC
        entry at ``index``.

        Caches the overlay so multiple calls return the same object.
        Unrecognised tags fall back to :class:`RawSection`.
        """
        if index < 0 or index >= len(self.toc):
            raise IndexError(
                f"TOC index {index} out of range [0, {len(self.toc)})")
        cached = self._section_cache.get(index)
        if cached is not None:
            return cached
        section = self._build_section(self.toc[index])
        self._section_cache[index] = section
        return section

    def sections(self) -> Iterable[Section]:
        """Yield every section in TOC order.  Construction is lazy per
        section but the caller should expect all of them to be
        realised after one full pass."""
        for i in range(len(self.toc)):
            yield self.section_for(i)

    def keyed_sections(self) -> dict[str, KeyedTableSection]:
        """Return every TOC entry that parses as a well-formed
        keyed-table, keyed by the heuristic ``<tag>_<index>`` /
        looked up via the config.xbr section-offset table when one
        matches.

        For config.xbr specifically, this lets callers say
        ``doc.keyed_sections()["armor_properties_real"]`` without
        knowing the TOC index.  For other files every keyed-table
        entry still shows up keyed by ``f"{tag}_{index}"``.

        Memoised on first call — the dispatch pass is a linear
        scan of the TOC and the result doesn't change across
        a single document's lifetime (edits reuse the same
        :class:`KeyedTableSection` instances via the section
        cache).
        """
        cached: dict[str, KeyedTableSection] | None = getattr(
            self, "_keyed_sections_cache", None)
        if cached is not None:
            return cached
        offset_to_name = _config_xbr_offset_to_name()
        out: dict[str, KeyedTableSection] = {}
        for i, entry in enumerate(self.toc):
            section = self.section_for(i)
            if isinstance(section, KeyedTableSection):
                key = offset_to_name.get(entry.file_offset,
                                         f"{entry.tag}_{i}")
                out[key] = section
        self._keyed_sections_cache = out
        return out

    def variant_sections(self) -> dict[str, VariantRecordSection]:
        """Return every variant-record section in the document.

        Uses the same TOC-file_offset dispatch as :meth:`section_for`
        PLUS a fallback that scans every known :data:`_VARIANT_SCHEMAS`
        entry and instantiates a free-floating :class:`VariantRecordSection`
        overlay even when the section's ``section_offset`` doesn't
        match any TOC row.  ``settings_foo`` lives past the last
        TOC entry in vanilla config.xbr and needs this fallback
        to be reachable.
        """
        out: dict[str, VariantRecordSection] = {}
        for name, schema in _VARIANT_SCHEMAS.items():
            # 1. If the schema's section_offset matches a TOC entry,
            #    prefer the cached Section instance so overlay
            #    identity is consistent with :meth:`section_for`.
            toc_match = next(
                (e for e in self.toc
                 if e.file_offset == schema["section_offset"]),
                None)
            if toc_match is not None:
                sec = self.section_for(toc_match.index)
                if isinstance(sec, VariantRecordSection):
                    out[name] = sec
                    continue
            # 2. Synthesize a floating VariantRecordSection.  The
            #    TocEntry we build is informational only — callers
            #    never round-trip it back into the TOC.
            entry = TocEntry(
                index=-1,
                size=0,  # unknown
                tag="(synthetic)",
                flags=0,
                file_offset=schema["section_offset"],
            )
            out[name] = VariantRecordSection(self, entry, schema)
        return out

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_section(self, entry: TocEntry) -> Section:
        """Dispatch a TOC entry to the right Section subclass.

        The dispatch is intentionally conservative: we only promote
        to a structural overlay when the entry smells right
        (well-formed header, known tag, reasonable size).  Everything
        else stays as :class:`RawSection` so round-trip is never at
        risk.
        """
        # 1. index.xbr's one record-table entry.
        if entry.tag == "indx":
            return IndexRecordsSection(self, entry)

        # 2. Variant-record sections in config.xbr — identified by
        #    the fixed section-offset table.  Multiple tags carry
        #    these (``vrnt`` / ``tabl``); go by file_offset.
        for _, schema in _VARIANT_SCHEMAS.items():
            if entry.file_offset == schema["section_offset"]:
                return VariantRecordSection(self, entry, schema)

        # 3. Keyed-table: a well-formed 20-byte header at
        #    section_start + 0x1000.
        keyed = KeyedTableSection(self, entry)
        if keyed.is_well_formed():
            # Reject absurdly wide tables — guards against a random
            # ``tabl``-ish looking entry in a level XBR that isn't
            # actually a keyed table.  Vanilla config.xbr ranges up
            # to num_cols ~= 170, so 512 is a comfortable ceiling.
            if (keyed.num_rows <= 1024
                    and keyed.num_cols <= 512
                    and keyed.total_cells <= 0x20000):
                return keyed

        return RawSection(self, entry)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def is_config_xbr(self) -> bool:
        """Heuristic: does this look like config.xbr?

        True iff at least 10 TOC entries carry the ``tabl`` tag
        AND we can parse at least one keyed-table section.
        """
        tabl_count = sum(1 for e in self.toc if e.tag == "tabl")
        if tabl_count < 10:
            return False
        return any(isinstance(self.section_for(i), KeyedTableSection)
                   for i in range(len(self.toc)))

    def summary(self) -> str:
        """One-line human summary for logs / CLI."""
        return (f"XbrDocument(path={self.path}, "
                f"size={len(self.raw):,}, "
                f"toc_entries={len(self.toc)})")

    def __repr__(self) -> str:  # pragma: no cover — trivial
        return self.summary()
