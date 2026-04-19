"""Xbox-standard save-container parsers.

``SaveMeta.xbx`` and ``TitleMeta.xbx`` are plain-text UTF-16-LE
key/value files with a specific layout shared by every Xbox title.
Their decoder is independent of Azurik and lives here so other
reverse-engineering work on Xbox saves can reuse it.

On-disk layout
--------------

Each file starts with UTF-16-LE key/value pairs separated by CRLF
(``\r\n`` encoded as ``0D 00 0A 00``).  Some entries use CRLF+NULL
double-terminators.  The file ends with a ``=`` marker (either
ASCII ``=`` = ``3D 00`` or just the closing of the final KVP) and
sometimes an appended binary blob (Xbox live data, timestamps, etc.).

Typical SaveMeta.xbx content
----------------------------

.. code-block::

    Name=My Hero's Adventure\\r\\n
    TitleName=Azurik: Rise of Perathia\\r\\n
    NoCopy=1\\r\\n

The trailing fields / binary tail are title-specific; we expose
whatever arbitrary key names we find and pass through the raw tail
unchanged for callers that want byte-level access.

Mutation
--------

``SaveMetaXbx.to_bytes()`` round-trips to the original byte layout
provided no field names change.  Editing a value in place is safe
(same byte structure with the new UTF-16 string).  Adding / removing
fields is supported but may upset title-level validators that expect
a specific field order — use with care.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


# Well-known key names that every Xbox SaveMeta.xbx file is expected
# to carry.  Missing any of them means either the file is corrupt or
# the title has a nonstandard save container (rare).
_REQUIRED_KEYS = ("Name", "TitleName")


@dataclass
class SaveMetaField:
    """One key/value pair from a ``SaveMeta.xbx`` / ``TitleMeta.xbx`` file.

    The value is always stored as a Python ``str`` in this module
    (the on-disk encoding is UTF-16-LE but decoded on load).
    ``terminator`` captures the exact separator bytes between this
    field and the next — preserved so round-trip serialisation
    doesn't lose title-specific CRLF / null-padding quirks.
    """
    key: str
    value: str
    terminator: bytes = b"\r\n"


@dataclass
class SaveMetaXbx:
    """Parsed Xbox-standard save-metadata file.

    Fields:
        fields:     Ordered list of key/value pairs.
        tail:       Raw bytes after the last key/value pair.  Usually
                    small (a few bytes padding or a title-specific
                    binary blob).  Passed through on write.
        encoding:   Always "utf-16-le" for Xbox saves; carried so
                    tests can verify no implicit encoding assumption.
    """
    fields: list[SaveMetaField] = field(default_factory=list)
    tail: bytes = b""
    encoding: str = "utf-16-le"

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get(self, key: str, default: str | None = None) -> str | None:
        """Return the value of ``key``, or ``default`` if missing."""
        for f in self.fields:
            if f.key == key:
                return f.value
        return default

    def set(self, key: str, value: str) -> None:
        """Set (or append) ``key``'s value."""
        for f in self.fields:
            if f.key == key:
                f.value = value
                return
        self.fields.append(SaveMetaField(key=key, value=value))

    @property
    def save_name(self) -> str | None:
        """Convenience: the ``Name`` field — the display name of the
        save slot as shown in the title's menus."""
        return self.get("Name")

    @property
    def title_name(self) -> str | None:
        """Convenience: the ``TitleName`` field."""
        return self.get("TitleName")

    @property
    def no_copy(self) -> bool:
        """True if the save is marked non-copyable (``NoCopy=1``)."""
        return self.get("NoCopy") == "1"

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    @classmethod
    def from_bytes(cls, data: bytes) -> "SaveMetaXbx":
        """Parse a ``SaveMeta.xbx`` / ``TitleMeta.xbx`` byte blob.

        The parser walks UTF-16-LE code units looking for ``key=value``
        entries delimited by CRLF.  Unknown trailing bytes are preserved
        in ``tail`` so round-trips are lossless even when the file has
        a title-specific binary suffix.
        """
        fields: list[SaveMetaField] = []
        pos = 0
        # Iterate over UTF-16-LE-encoded lines.  Use the raw bytes
        # because some entries carry binary values we don't want to
        # re-encode.
        while pos + 2 <= len(data):
            # Find the next CRLF (``\r\n`` = 0D 00 0A 00) boundary.
            end = data.find(b"\r\x00\n\x00", pos)
            if end == -1:
                break
            chunk = data[pos:end]
            # Chunk might be empty (consecutive CRLFs); preserve as a
            # blank field only if it sits between two real entries.
            try:
                text = chunk.decode("utf-16-le")
            except UnicodeDecodeError:
                # Non-UTF-16 content; stash the rest as tail and bail.
                break
            if "=" in text:
                key, _, value = text.partition("=")
                # Some titles prefix a NUL character before the next
                # entry; detect and include it in the terminator.
                term = b"\r\x00\n\x00"
                after = end + 4
                if after + 2 <= len(data) and data[after:after + 2] == b"\x00\x00":
                    term += b"\x00\x00"
                    after += 2
                fields.append(SaveMetaField(key=key, value=value, terminator=term))
                pos = after
            else:
                # Can't parse; stash the remainder as tail.
                break

        tail = data[pos:]
        return cls(fields=fields, tail=tail)

    def to_bytes(self) -> bytes:
        """Re-serialise to the original byte layout."""
        out = bytearray()
        for f in self.fields:
            out += f"{f.key}={f.value}".encode(self.encoding)
            out += f.terminator
        out += self.tail
        return bytes(out)

    def __iter__(self) -> Iterator[SaveMetaField]:
        return iter(self.fields)


@dataclass
class SaveDirectory:
    """One save slot's on-disk directory.

    Represents a folder containing the Xbox-standard ``.xbx`` files
    plus Azurik's own ``.sav`` files.  Used for introspection and
    batch export.

    Attributes:
        path:            Root directory on the host filesystem.
        meta_xbx:        Parsed ``SaveMeta.xbx`` (if present).
        title_meta_xbx:  Parsed ``TitleMeta.xbx`` (if present).
        save_image:      Raw bytes of ``SaveImage.xbx`` (if present).
        title_image:     Raw bytes of ``TitleImage.xbx`` (if present).
        sav_files:       Map of ``<relpath>.sav`` → Path for every
                         ``.sav`` file in this directory AND its
                         subdirectories.  Keys are forward-slash
                         relative paths (e.g. ``levels/water/w1.sav``)
                         so the caller can tell level saves apart
                         from root saves at a glance.  Real Azurik
                         saves nest level saves under ``levels/``.
        extra_files:     Any other files we don't know how to parse,
                         by relative path → Path.
    """
    path: Path
    meta_xbx: SaveMetaXbx | None = None
    title_meta_xbx: SaveMetaXbx | None = None
    save_image: bytes | None = None
    title_image: bytes | None = None
    sav_files: dict[str, Path] = field(default_factory=dict)
    extra_files: dict[str, Path] = field(default_factory=dict)

    @classmethod
    def from_directory(cls, path: str | Path) -> "SaveDirectory":
        """Inspect a directory and parse every recognised file.

        Recurses into subdirectories (Azurik nests level saves under
        ``levels/<element>/<level>.sav``) so a single
        ``SaveDirectory`` captures the entire save slot.  Keys in
        ``sav_files`` / ``extra_files`` are relative paths with
        forward slashes for stable cross-platform comparisons.

        Unknown files are kept in ``extra_files`` so callers can
        hex-dump or copy them without the parser pretending to know.
        """
        root = Path(path)
        if not root.is_dir():
            raise NotADirectoryError(
                f"save directory {root} does not exist or is not a folder")

        inst = cls(path=root)
        for entry in sorted(root.rglob("*")):
            if not entry.is_file():
                continue
            rel = entry.relative_to(root).as_posix()
            name = entry.name
            lowered = name.lower()
            # The Xbox-standard container files only ever sit at the
            # ROOT of the save slot — detect by relative-path depth.
            is_root_file = "/" not in rel
            if is_root_file and lowered == "savemeta.xbx":
                inst.meta_xbx = SaveMetaXbx.from_bytes(entry.read_bytes())
                continue
            if is_root_file and lowered == "titlemeta.xbx":
                inst.title_meta_xbx = SaveMetaXbx.from_bytes(entry.read_bytes())
                continue
            if is_root_file and lowered == "saveimage.xbx":
                inst.save_image = entry.read_bytes()
                continue
            if is_root_file and lowered == "titleimage.xbx":
                inst.title_image = entry.read_bytes()
                continue
            if lowered.endswith(".sav"):
                inst.sav_files[rel] = entry
            else:
                inst.extra_files[rel] = entry
        return inst

    def summary(self) -> dict[str, object]:
        """Small dict with an overview — convenient for JSON / CLI."""
        # Partition .sav files into root-level and level-nested for
        # easier scanning at a glance.
        root_savs = sorted(n for n in self.sav_files if "/" not in n)
        level_savs = sorted(n for n in self.sav_files if n.startswith("levels/"))
        other_nested = sorted(
            n for n in self.sav_files
            if "/" in n and not n.startswith("levels/"))
        return {
            "path": str(self.path),
            "save_name": self.meta_xbx.save_name if self.meta_xbx else None,
            "title_name": (
                self.meta_xbx.title_name if self.meta_xbx else None),
            "no_copy": (
                self.meta_xbx.no_copy if self.meta_xbx else None),
            "save_image_bytes": (
                len(self.save_image) if self.save_image else 0),
            "title_image_bytes": (
                len(self.title_image) if self.title_image else 0),
            "sav_files": sorted(self.sav_files),  # full flat list
            "root_sav_files": root_savs,
            "level_sav_files": level_savs,
            "other_nested_sav_files": other_nested,
            "extra_files": sorted(self.extra_files),
        }
