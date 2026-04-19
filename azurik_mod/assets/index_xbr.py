"""Parse Azurik's ``index.xbr`` — the global asset directory.

``index.xbr`` is listed under ``tag=always`` in
``prefetch-lists.txt`` so the streaming loader keeps it resident
throughout the game.  The file holds one TOC entry (``indx``)
whose payload is a flat record table mapping fourcc-typed asset
names to their locations in the other XBR files.

## Record layout (partially decoded)

The ``indx`` payload opens with a 16-byte header followed by
``count`` records of 20 bytes each and a trailing string pool:

    offset 0x0000   u32  count           (3072 in vanilla; 3071 real
                                          records + 1 sentinel row
                                          that overlaps the pool)
    offset 0x0004   u32  version         (4 in vanilla)
    offset 0x0008   u32  header_hint     (24 — role unclear; NOT the
                                          real header size which is 16)
    offset 0x000C   u32  pool_hint       (0xEFFC — role unclear)
    offset 0x0010   [count * 20 bytes]   record table
    offset ??? ..   string pool

Each 20-byte record:

    +0  u32  length          string length in bytes (no NUL included)
    +4  u32  off1             pool offset for string #1
    +8  char[4] fourcc        type tag: 'body', 'banm', 'node', 'surf',
                              'wave', 'levl', 'tabl', 'font'
    +12 u8   disc             subtype discriminator
    +13 u8   pad[3]           zero padding
    +16 u32  off2             pool offset for string #2

The string pool begins at the end of the record table and starts
with a 4-byte little-endian dword (``0x0001812D`` in vanilla —
role unclear) followed by a ``'levl'`` 4-byte marker and then
concatenated NUL-terminated asset paths.

## What we haven't decoded

- Exact pool base for ``off1`` vs ``off2``.  Empirically, using
  ``pool_base = records_start + (count * stride) + 4`` (i.e.
  skipping the 0x1812D magic) lines up the first record's
  ``off1`` with ``"characters.xbr"`` — but later records land a
  few bytes into their target strings, suggesting each record
  carries some kind of prefix offset we haven't pinned yet.
- Semantics of the two offsets — ``off1`` appears to reference
  the XBR FILE hosting the asset (``characters.xbr``,
  ``w1.xbr``, …) while ``off2`` references the ASSET KEY within
  that file.  But the exact substring semantics (why off2 lands
  mid-string for most records) are unclear.
- The meaning of the two trailing header fields
  (``header_hint``, ``pool_hint``).
- Why ``count`` is declared as 3072 when only 3071 entries are
  valid records (the 3072nd overlaps the string pool).

So this parser exposes the raw ``length``, ``off1``, ``fourcc``,
``disc``, ``off2`` fields and leaves semantic interpretation to
callers.  The ``iter_asset_paths`` helper does a best-effort
pool scan to extract all human-readable paths.

Vanilla ISO numbers (pinned in tests/test_index_xbr.py):

- 8 distinct fourcc tags
- 3071 records broken down as:
  surf=1099  wave=816  banm=712  node=230  body=160
  levl=32    tabl=18   font=4

See docs/LEARNINGS.md § index.xbr for the full RE notes.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

# Offset into the ``indx`` payload where the 16-byte header starts.
# Empirically confirmed: the ``indx`` TOC entry's ``file_offset`` is
# 0 but the real data starts at a 4 KiB alignment boundary.
_PAYLOAD_OFFSET = 0x1000

# Header fields.
_HEADER_SIZE = 16

# Record size in bytes.
_RECORD_SIZE = 20


@dataclass(frozen=True)
class IndexRecord:
    """One 20-byte entry in the ``indx`` record table.

    Attributes
    ----------
    index: int
        0-based record index within the table.
    length: int
        String length for the asset name at ``off1`` (no NUL).
    off1: int
        First pool offset — appears to reference the containing
        XBR file name (``characters.xbr``, ``w1.xbr``, …).
    fourcc: str
        Asset-type fourcc.  One of:
        ``body``, ``banm``, ``node``, ``surf``, ``wave``,
        ``levl``, ``tabl``, ``font``.
    discriminator: int
        Subtype index (1 byte).  Empirically varies 0x10..0xFF.
    off2: int
        Second pool offset — appears to reference the asset key
        within the containing file.
    """

    index: int
    length: int
    off1: int
    fourcc: str
    discriminator: int
    off2: int


@dataclass(frozen=True)
class IndexXbr:
    """Parsed ``index.xbr`` — the full record table plus the string
    pool and raw header metadata.

    Construct with :func:`load_index_xbr`.
    """

    count_field: int            # raw count field from header (3072 in vanilla)
    version: int                # raw version field (4 in vanilla)
    header_hint: int            # raw field at +0x08 (24 in vanilla)
    pool_hint: int              # raw field at +0x0C (0xEFFC in vanilla)
    records: tuple[IndexRecord, ...]
    pool_start: int             # file offset where the string pool starts
    pool_magic: int             # first 4 bytes of the pool (0x0001812D in vanilla)
    pool_tag: str               # next 4 bytes (either 'levl' in vanilla, or '')
    raw: bytes                  # full file bytes — kept for pool lookups

    def tag_counts(self) -> dict[str, int]:
        """Number of records per fourcc tag.

        Used by verification tests to pin the vanilla distribution:
        ``{surf: 1099, wave: 816, banm: 712, node: 230, body: 160,
        levl: 32, tabl: 18, font: 4}``.
        """
        out: dict[str, int] = {}
        for r in self.records:
            out[r.fourcc] = out.get(r.fourcc, 0) + 1
        return out

    def records_for_tag(self, fourcc: str) -> tuple[IndexRecord, ...]:
        """Every record with the given fourcc tag, in order."""
        return tuple(r for r in self.records if r.fourcc == fourcc)

    # ------------------------------------------------------------------
    # String pool access (partial — see module docstring)
    # ------------------------------------------------------------------

    def iter_asset_paths(self, *, min_len: int = 4) -> list[str]:
        """Best-effort extraction of every ASCII path in the string
        pool.

        Walks the pool bytes, splits on NUL, keeps every run of
        printable characters of ``min_len`` or more.  Because we
        don't fully know the pool-offset semantics, this doesn't
        map strings back to records — it's a "what paths does the
        game know about?" snapshot, useful for documenting the
        game's asset catalogue.
        """
        out: list[str] = []
        pos = self.pool_start + 8  # skip magic + tag
        while pos < len(self.raw):
            end = pos
            while (end < len(self.raw) and self.raw[end] != 0
                   and 0x20 <= self.raw[end] < 0x7F):
                end += 1
            length = end - pos
            if length >= min_len and end < len(self.raw):
                # Real string: printable run followed by NUL.
                if self.raw[end] == 0:
                    out.append(
                        self.raw[pos:end].decode("ascii",
                                                 errors="replace"))
                    pos = end + 1
                    continue
            # Skip forward past this byte (may be NUL or junk).
            pos = end + 1
        return out


def load_index_xbr(path: str | Path) -> IndexXbr:
    """Parse an ``index.xbr`` file into an :class:`IndexXbr`.

    Raises
    ------
    FileNotFoundError
        Path doesn't exist.
    ValueError
        File too small, wrong magic, or record table parses to zero
        records.
    """
    p = Path(path)
    raw = p.read_bytes()

    if len(raw) < _PAYLOAD_OFFSET + _HEADER_SIZE:
        raise ValueError(
            f"{p.name}: file too small ({len(raw)} bytes) — needs at "
            f"least 0x{_PAYLOAD_OFFSET + _HEADER_SIZE:X}")
    if raw[:4] != b"xobx":
        raise ValueError(f"{p.name}: bad XBR magic {raw[:4]!r}")

    hdr = struct.unpack_from("<IIII", raw, _PAYLOAD_OFFSET)
    count_field, version, header_hint, pool_hint = hdr

    record_table_start = _PAYLOAD_OFFSET + _HEADER_SIZE
    # Known fourcc tags that real records use.  Any row with a tag
    # outside this set is a false positive — the walker has crossed
    # into the string pool.  Add new tags here only after verifying
    # they appear in ``default.xbe``'s load path for the indx table.
    valid_tags = {"body", "banm", "node", "surf", "wave", "levl",
                  "tabl", "font"}
    # Plausible off1/off2 range.  The pool lives between the record
    # table and the end of the indx payload, so offsets > file size
    # are unambiguously junk (the 3072nd "record" in the vanilla ISO
    # gets off1 = 0x0001812D which is out of range).
    max_plausible_offset = len(raw)

    records: list[IndexRecord] = []
    for i in range(count_field):
        off = record_table_start + i * _RECORD_SIZE
        if off + _RECORD_SIZE > len(raw):
            break
        tag_bytes = raw[off + 8:off + 12]
        if not all(0x20 <= b < 0x7F for b in tag_bytes):
            break
        tag = tag_bytes.decode("ascii")
        if tag not in valid_tags:
            break
        s_len, off1 = struct.unpack_from("<II", raw, off)
        disc = raw[off + 12]
        off2 = struct.unpack_from("<I", raw, off + 16)[0]
        # Sanity: offsets must point somewhere in the file.  The
        # first row that bursts past the file is the pool-boundary
        # sentinel, not a real record.
        if off1 >= max_plausible_offset or off2 >= max_plausible_offset:
            break
        records.append(IndexRecord(
            index=i, length=s_len, off1=off1, fourcc=tag,
            discriminator=disc, off2=off2))

    if not records:
        raise ValueError(f"{p.name}: no valid records parsed")

    # After the last real record, a 4-byte sentinel precedes the
    # string pool (``11 00 00 00`` in the vanilla dump, aligning the
    # pool to the 0xC000 byte boundary of the payload).  Pool starts
    # at the first real magic dword — search in the small window
    # following the record table.
    raw_pool_start = record_table_start + len(records) * _RECORD_SIZE
    pool_start = raw_pool_start
    for probe in range(raw_pool_start, min(raw_pool_start + 64, len(raw)),
                       4):
        magic = struct.unpack_from("<I", raw, probe)[0]
        # Vanilla pool magic is 0x0001812D; any nonzero small-value
        # dword is plausible.  We prefer a dword followed by an ASCII
        # 4-char marker (``levl`` in the vanilla dump) because that's
        # the unmistakable pool header.
        tag_bytes = raw[probe + 4:probe + 8]
        if (all(0x20 <= b < 0x7F for b in tag_bytes) and
                tag_bytes.decode("ascii").isalpha()):
            pool_start = probe
            break
    pool_magic = struct.unpack_from("<I", raw, pool_start)[0]
    tag_bytes = raw[pool_start + 4:pool_start + 8]
    pool_tag = (tag_bytes.decode("ascii")
                if all(0x20 <= b < 0x7F for b in tag_bytes) else "")

    return IndexXbr(
        count_field=count_field,
        version=version,
        header_hint=header_hint,
        pool_hint=pool_hint,
        records=tuple(records),
        pool_start=pool_start,
        pool_magic=pool_magic,
        pool_tag=pool_tag,
        raw=raw,
    )


__all__ = [
    "IndexRecord",
    "IndexXbr",
    "load_index_xbr",
]
