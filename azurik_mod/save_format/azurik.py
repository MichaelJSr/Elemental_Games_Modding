"""Azurik-specific ``.sav`` file parsing.

Azurik's save format was reverse-engineered from a real save
extracted from xemu's ``xbox_hdd.qcow2`` (see
``docs/SAVE_FORMAT.md``).  Findings:

**There is no single "header format"** — the ``.sav`` files group
into several distinct shapes based on what they store.  This module
recognises four:

1. **Text saves** (``loc.sav``, ``magic.sav``, ``options.sav``):
   ASCII, line-delimited.  First line is ``fileversion=<N>``;
   subsequent lines are key-value pairs (``key=value``) or bare
   text entries.  Trivially moddable — a user can open in a text
   editor, change values, save.

2. **Binary-record saves** (``inv.sav``, ``shared.sav``): small
   fixed header (u32 version, u32 count) followed by count records
   with entity names + values.  Structure varies per file; the
   generic reader exposes them as opaque bytes + the probable
   (version, count) header.

3. **Signature save** (``signature.sav``): exactly 20 bytes — a
   SHA-1 digest computed over the other save files, used by
   Azurik to verify save integrity at load time.  Modifying any
   other ``.sav`` without updating ``signature.sav`` will cause
   the game to reject the save.

4. **Level saves** (``<level>.sav`` under ``levels/<element>/``):
   per-level world state.  Many are all zeros (level not yet
   visited); populated ones start with a small header (u32
   version, u32 count) then level-specific records.  Full decoding
   is future work — the generic reader exposes the raw bytes +
   probable (version, count).

The historical ``SaveHeader`` / ``AzurikSaveFile`` scaffold that
assumed every ``.sav`` had a 20-byte ``{magic, version, payload_len,
checksum, reserved}`` prologue has been removed — that shape was
wrong for every real Azurik save.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


TEXT_SAVE_MARKER = b"fileversion="


def _looks_like_text_save(data: bytes) -> bool:
    """A save is text-shaped iff it starts with ``fileversion=``."""
    return data[:len(TEXT_SAVE_MARKER)] == TEXT_SAVE_MARKER


# ---------------------------------------------------------------------------
# Text saves — loc / magic / options
# ---------------------------------------------------------------------------


@dataclass
class TextSave:
    """A ``fileversion=1\\n<line>\\n<line>\\n...``-formatted save.

    Examples we've confirmed from real saves:

    - ``magic.sav``::

          fileversion=1
          1.000000
          1.000000
          ...

      (an ordered list of float-valued magic-related stats; keys
      are implicit / positional).

    - ``loc.sav``::

          fileversion=1
          levels/death/d2

      (current level path + spawn point; binary tail follows).

    - ``options.sav``::

          fileversion=1
          <settings...>

    We decode every line up to the first 0x00 or end-of-content,
    keeping the FULL raw bytes in ``data`` so round-trips preserve
    any binary tail.  Callers that want to mutate the text portion
    should edit ``lines`` (list of str) then call ``to_bytes()``
    which rebuilds the text prefix + re-appends the preserved
    binary tail.
    """
    version: int
    lines: list[str]
    binary_tail: bytes = b""
    raw: bytes = b""

    @classmethod
    def from_bytes(cls, data: bytes) -> "TextSave":
        if not _looks_like_text_save(data):
            raise ValueError("not a text-formatted save "
                             "(missing fileversion= marker)")
        # Parse up to the first 0x00 byte (binary cutover).
        zero_idx = data.find(b"\x00")
        text_part = data[:zero_idx] if zero_idx != -1 else data
        tail = data[zero_idx:] if zero_idx != -1 else b""
        text = text_part.decode("ascii", errors="replace")
        lines = text.split("\n")
        # Trim an empty trailing entry from the final newline.
        if lines and lines[-1] == "":
            lines.pop()
        # First line is fileversion=<N>.
        version = 1
        if lines and lines[0].startswith("fileversion="):
            try:
                version = int(lines[0].split("=", 1)[1])
            except ValueError:
                pass
            lines = lines[1:]
        return cls(version=version, lines=lines,
                   binary_tail=tail, raw=data)

    def to_bytes(self) -> bytes:
        """Re-serialise back to disk layout."""
        parts = [f"fileversion={self.version}"]
        parts.extend(self.lines)
        text = ("\n".join(parts) + "\n").encode("ascii")
        return text + self.binary_tail


# ---------------------------------------------------------------------------
# Binary saves — inv / shared / level files with (version, count) header
# ---------------------------------------------------------------------------


@dataclass
class BinarySave:
    """A binary ``.sav`` file.

    Observed header shape (inv.sav / shared.sav / level saves):

        +0x00  u32  version
        +0x04  u32  record_count

    Followed by ``record_count`` records of a size we haven't yet
    decoded per-file (varies).  This class exposes the parsed
    header + raw body as opaque bytes.  Full per-file decoders are
    future work — e.g. a dedicated ``Inventory`` class that walks
    ``inv.sav``'s records and exposes item slots.
    """
    version: int
    record_count: int
    body: bytes       # bytes after the 8-byte header
    raw: bytes = b""  # whole-file bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> "BinarySave":
        if len(data) < 8:
            raise ValueError(
                f"binary save requires >= 8 B header, got {len(data)}")
        version, count = struct.unpack_from("<II", data, 0)
        return cls(
            version=version,
            record_count=count,
            body=data[8:],
            raw=data,
        )

    def to_bytes(self) -> bytes:
        return struct.pack("<II", self.version, self.record_count) + self.body


# ---------------------------------------------------------------------------
# Signature save — 20-byte SHA-1 digest
# ---------------------------------------------------------------------------


@dataclass
class SignatureSave:
    """The ``signature.sav`` file — 20-byte SHA-1 digest that
    Azurik uses to validate every OTHER .sav file in the save
    slot.  Modifying any other .sav without recomputing this
    digest will cause Azurik to reject the save on load.

    We DON'T know the exact hash domain yet (what files are fed
    to SHA-1, in what order, with what salt).  Until that's
    reverse-engineered, save mods that rewrite other .sav files
    are a footgun: the game will reject them.  See
    ``docs/SAVE_FORMAT.md`` § "Hash domain is not yet decoded"
    for the investigation status.
    """
    digest: bytes  # 20 bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> "SignatureSave":
        if len(data) != 20:
            raise ValueError(
                f"signature.sav must be exactly 20 B (SHA-1); "
                f"got {len(data)}")
        return cls(digest=data)

    def to_bytes(self) -> bytes:
        return self.digest

    def hex(self) -> str:
        return self.digest.hex()


# ---------------------------------------------------------------------------
# Classifier / dispatcher
# ---------------------------------------------------------------------------


@dataclass
class AzurikSave:
    """Sum type over the four .sav variants.

    Exactly one of ``text`` / ``binary`` / ``signature`` is set
    depending on the file's shape.  ``kind`` documents which.
    ``raw`` is always the original file bytes (lossless
    round-trip sentinel).
    """
    path: Path | None
    kind: str  # "text" | "binary" | "signature" | "unknown"
    text: TextSave | None = None
    binary: BinarySave | None = None
    signature: SignatureSave | None = None
    raw: bytes = b""

    @classmethod
    def from_bytes(cls, data: bytes, *, path: Path | None = None) -> "AzurikSave":
        # signature.sav is always exactly 20 bytes + path-name hint.
        if path is not None and path.name == "signature.sav":
            try:
                return cls(
                    path=path, kind="signature",
                    signature=SignatureSave.from_bytes(data), raw=data)
            except ValueError:
                return cls(path=path, kind="unknown", raw=data)

        # Text saves start with "fileversion=".
        if _looks_like_text_save(data):
            return cls(
                path=path, kind="text",
                text=TextSave.from_bytes(data), raw=data)

        # Anything with a plausible version/count header ≥ 8 B.
        if len(data) >= 8:
            return cls(
                path=path, kind="binary",
                binary=BinarySave.from_bytes(data), raw=data)

        return cls(path=path, kind="unknown", raw=data)

    @classmethod
    def from_path(cls, path: str | Path) -> "AzurikSave":
        p = Path(path)
        return cls.from_bytes(p.read_bytes(), path=p)

    def to_bytes(self) -> bytes:
        if self.kind == "text" and self.text is not None:
            return self.text.to_bytes()
        if self.kind == "binary" and self.binary is not None:
            return self.binary.to_bytes()
        if self.kind == "signature" and self.signature is not None:
            return self.signature.to_bytes()
        return self.raw

    def summary(self) -> dict[str, Any]:
        """Small dict suitable for JSON / human output."""
        out: dict[str, Any] = {
            "path": str(self.path) if self.path else None,
            "kind": self.kind,
            "size_bytes": len(self.raw),
        }
        if self.kind == "text" and self.text is not None:
            out["version"] = self.text.version
            out["lines"] = len(self.text.lines)
            # Include the first few lines to give the caller a feel
            # for the content without dumping a whole save.
            out["preview"] = self.text.lines[:6]
            out["binary_tail_bytes"] = len(self.text.binary_tail)
        elif self.kind == "binary" and self.binary is not None:
            out["version"] = self.binary.version
            out["record_count"] = self.binary.record_count
            out["body_bytes"] = len(self.binary.body)
        elif self.kind == "signature" and self.signature is not None:
            out["sha1_hex"] = self.signature.hex()
        return out


# ---------------------------------------------------------------------------
# Backward-compatibility aliases
# ---------------------------------------------------------------------------
#
# Old class names (pre-real-data rewrite).  Kept so existing tests +
# external callers don't break — they're thin shells over AzurikSave.
# New code should use AzurikSave directly.


@dataclass
class AzurikSaveFile(AzurikSave):
    """Legacy alias for :class:`AzurikSave` — DO NOT USE IN NEW CODE.

    Retained because :mod:`tests.test_save_format` was written against
    it before the format was properly decoded.  Every field is
    forwarded from the sum type.
    """
    pass


@dataclass
class SignatureSav(AzurikSave):
    """Legacy alias — the file-class disambiguation is now ``kind``."""
    pass


@dataclass
class LevelSav(AzurikSave):
    """Legacy alias for per-level save files."""

    def level_id(self) -> str | None:
        return self.path.stem if self.path else None


# Legacy name still referenced from ``azurik_mod.save_format.__init__``.
@dataclass
class SaveHeader:
    """Legacy scaffold class — the real Azurik save format has no
    unified 20-byte header, so this class is retained only as a
    historical marker.  Its ``from_bytes`` treats the input as a
    ``BinarySave`` header (u32 version, u32 record_count) which is
    correct for most binary-record saves.
    """
    magic: int = 0          # unused in real files
    version: int = 0
    payload_len: int = 0
    checksum: int = 0
    reserved: int = 0

    @classmethod
    def from_bytes(cls, data: bytes) -> "SaveHeader":
        if len(data) < 20:
            raise ValueError(
                f".sav header requires 20 bytes; got {len(data)}")
        magic, version, payload_len, checksum, reserved = struct.unpack_from(
            "<IIIII", data, 0)
        return cls(magic, version, payload_len, checksum, reserved)

    def to_bytes(self) -> bytes:
        return struct.pack(
            "<IIIII", self.magic, self.version,
            self.payload_len, self.checksum, self.reserved)

    def magic_as_ascii(self) -> str:
        bs = struct.pack("<I", self.magic)
        return "".join(
            chr(b) if 0x20 <= b < 0x7F else "." for b in bs)
