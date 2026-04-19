"""XBR write-back — #18 from ``docs/TOOLING_ROADMAP.md``.

Minimal, *safe* XBR mutation API.  Supports two operations that
cover the majority of practical mod use-cases without needing to
fully re-layout the TOC or the string pool:

1. **In-place byte replacement** at an absolute file offset (e.g.
   flipping a flag byte in a TOC entry).  Size of the replacement
   must equal the size of the region being replaced; the function
   refuses oversized writes.
2. **In-place ASCII string replacement** within a TOC-entry
   region.  The new string must fit (including a trailing NUL) in
   the same number of bytes the old string occupied, and must be
   ASCII; anything else is rejected with a specific error.

Full-fidelity structural edits (adding entries, resizing the
string pool, inserting new records) are explicitly out of scope
for this milestone — they require re-walking every pointer inside
the XBR (xrefs to the string pool are scattered across record
payloads) and we haven't reversed those yet.

When the string pool layout is understood we'll extend this
module with a ``StringPool.append`` path; the current API will
keep working unchanged.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

__all__ = [
    "XbrEditError",
    "XbrEditLog",
    "XbrEditor",
    "load_xbr",
]


class XbrEditError(ValueError):
    """Raised when an edit would corrupt the file (size mismatch,
    non-ASCII body, etc.)."""


@dataclass(frozen=True)
class _TocEntry:
    """Lightweight TOC row — mirrors the dataclass in
    :mod:`scripts.xbr_parser` but is redeclared here so the
    editor module doesn't drag the legacy ``scripts/`` package
    onto the import path."""

    index: int
    size: int
    tag: str
    flags: int
    file_offset: int


@dataclass
class XbrEditLog:
    """Audit trail of every edit applied."""

    actions: list[str] = field(default_factory=list)

    def record(self, description: str) -> None:
        self.actions.append(description)

    def format(self) -> str:
        if not self.actions:
            return "  (no edits applied)"
        return "\n".join(f"  OK  {a}" for a in self.actions)


class XbrEditor:
    """Read-modify-write wrapper around an XBR byte buffer.

    Holds the bytes in memory (XBR files are 64 KiB – 16 MiB, so
    this is cheap) and applies edits to a mutable ``bytearray``
    so callers can chain multiple operations before writing.

    Typical usage::

        editor = XbrEditor.load(Path("town.xbr"))
        editor.replace_string_in_tag(
            old="Hello", new="World!", tag="surf")
        editor.write(Path("town_modded.xbr"))
    """

    HEADER_SIZE = 0x40

    def __init__(self, data: bytes) -> None:
        self._data = bytearray(data)
        self.log = XbrEditLog()
        self._toc = _parse_toc(bytes(self._data))

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> "XbrEditor":
        """Load ``path`` into a new editor."""
        return cls(Path(path).read_bytes())

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def data(self) -> bytes:
        """Current contents — safe to copy; never mutated by
        callers unless they go through the editor API."""
        return bytes(self._data)

    @property
    def toc(self) -> tuple[_TocEntry, ...]:
        return tuple(self._toc)

    def entries_with_tag(self, tag: str) -> list[_TocEntry]:
        return [e for e in self._toc if e.tag == tag]

    # ------------------------------------------------------------------
    # Byte-level edits
    # ------------------------------------------------------------------

    def replace_bytes(self, offset: int, new: bytes) -> None:
        """Overwrite bytes at ``offset`` with ``new`` (same
        length).

        Refuses if the write would walk off the end of the file
        or if ``len(new)`` differs from the overwritten region.
        """
        end = offset + len(new)
        if offset < 0 or end > len(self._data):
            raise XbrEditError(
                f"replace_bytes out of range: "
                f"offset=0x{offset:x} end=0x{end:x} "
                f"file_size=0x{len(self._data):x}")
        self._data[offset:end] = new
        self.log.record(
            f"replace_bytes: @0x{offset:08x} "
            f"len={len(new)}")

    def replace_string_at(self, offset: int, new: str, *,
                          pad_byte: int = 0x00) -> None:
        """Overwrite an ASCII NUL-terminated string at
        ``offset``.

        Finds the existing NUL terminator to determine the
        region's upper bound; ``new`` + trailing NUL must fit
        within that region.  Remaining bytes are filled with
        ``pad_byte`` (default 0x00).
        """
        if any(ord(c) > 0x7F for c in new):
            raise XbrEditError(
                f"new string {new!r} contains non-ASCII "
                f"characters; XBR string pool is ASCII-only")
        data = self._data
        end = offset
        while end < len(data) and data[end] != 0:
            end += 1
        if end >= len(data):
            raise XbrEditError(
                f"no NUL terminator after offset 0x{offset:x}")
        old_slot = end - offset          # excludes the NUL
        new_bytes = new.encode("ascii") + b"\x00"
        if len(new_bytes) > old_slot + 1:
            raise XbrEditError(
                f"new string {new!r} ({len(new_bytes)} B "
                f"including NUL) is longer than the existing "
                f"slot ({old_slot + 1} B) at offset "
                f"0x{offset:x}; the in-place editor refuses "
                f"oversized writes")
        padded = new_bytes + bytes([pad_byte]) * (
            (old_slot + 1) - len(new_bytes))
        self._data[offset:offset + len(padded)] = padded
        self.log.record(
            f"replace_string_at: @0x{offset:08x} "
            f"old_len={old_slot} new_len={len(new)}")

    def replace_string_in_tag(self, *, old: str, new: str,
                              tag: str | None = None,
                              occurrence: int = 0) -> int:
        """Find ``old`` (ASCII, NUL-terminated) inside the first
        TOC entry whose ``tag`` matches and replace it with
        ``new``.

        Returns the file offset where the replacement happened.
        ``occurrence`` chooses the N-th match when the string
        appears multiple times; defaults to 0 (first match).
        """
        if any(ord(c) > 0x7F for c in old):
            raise XbrEditError(
                f"search string {old!r} contains non-ASCII "
                f"characters")
        search_bytes = old.encode("ascii") + b"\x00"
        regions = self._entry_regions(tag)
        matches: list[int] = []
        for start, end in regions:
            window = bytes(self._data[start:end])
            idx = 0
            while True:
                hit = window.find(search_bytes, idx)
                if hit < 0:
                    break
                matches.append(start + hit)
                idx = hit + 1
        if occurrence >= len(matches):
            raise XbrEditError(
                f"no match #{occurrence} for {old!r} "
                f"in tag {tag or '*'} (found {len(matches)})")
        self.replace_string_at(matches[occurrence], new)
        return matches[occurrence]

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def write(self, path: Path) -> None:
        """Commit the edited bytes to ``path``.  Creates parents
        if missing."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(bytes(self._data))
        self.log.record(f"wrote: {path}")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _entry_regions(self, tag: str | None
                       ) -> Iterable[tuple[int, int]]:
        """Yield ``(start, end)`` byte ranges for every TOC entry
        whose ``tag`` matches (or every entry when ``tag`` is
        None)."""
        if tag is None:
            entries = self._toc
        else:
            entries = [e for e in self._toc if e.tag == tag]
        for e in entries:
            start = e.file_offset
            end = min(start + e.size, len(self._data))
            if start < end:
                yield start, end


def load_xbr(path: Path) -> XbrEditor:
    """Convenience: ``XbrEditor.load`` but importable as a
    top-level name.  Kept here so ``from azurik_mod.xbe_tools
    import xbr_edit; xbr_edit.load_xbr(...)`` works."""
    return XbrEditor.load(path)


# ---------------------------------------------------------------------------
# TOC parsing (self-contained to avoid scripts/ import churn)
# ---------------------------------------------------------------------------


def _parse_toc(data: bytes) -> list[_TocEntry]:
    entries: list[_TocEntry] = []
    off = XbrEditor.HEADER_SIZE
    while off + 16 <= len(data):
        size = struct.unpack_from("<I", data, off)[0]
        tag_raw = data[off + 4:off + 8]
        flags = struct.unpack_from("<I", data, off + 8)[0]
        file_offset = struct.unpack_from("<I", data, off + 12)[0]
        if size == 0 and flags == 0 and file_offset == 0:
            break
        try:
            tag = tag_raw.decode("ascii")
        except (UnicodeDecodeError, ValueError):
            tag = tag_raw.hex()
        entries.append(_TocEntry(
            index=len(entries),
            size=size, tag=tag,
            flags=flags, file_offset=file_offset))
        off += 16
    return entries
