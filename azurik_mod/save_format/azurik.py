"""Azurik-specific ``.sav`` file parsing.

Azurik stores game state across a pair of file flavours:

- ``signature.sav`` — profile-level state (inventory, stats, learned
  abilities).  One per save slot.
- ``<level>.sav`` — per-level world state (entity positions, quest
  progress, picked-up items).  One per level the player has visited.

Both flavours share a common 20-byte fixed header that every Azurik
save-load path reads FIRST (observed at call site 0x5C95C:
``fread(buf, 0x14, 1, fp)`` before any branching on content).  The
body past the header is flavour-specific and not yet fully decoded.

This module provides:

- :class:`SaveHeader` — decodes the 20-byte prologue.
- :class:`AzurikSaveFile` — base class with byte-level access + an
  :meth:`AzurikSaveFile.iter_chunks` extension point for future
  field-level decoders.
- :class:`SignatureSav` / :class:`LevelSav` — type-tagged subclasses
  that plug in flavour-specific decoders as they get reverse-
  engineered.  Current implementation is a scaffold: both classes
  round-trip the entire file bit-for-bit and expose the raw bytes +
  header; richer decoding is future work.

Header byte layout (pinned from ``save.cpp`` at VA 0x5C95C + 0x5BEC5)
---------------------------------------------------------------------

Offsets below are tentative — pinned against the reading pattern
rather than a full field-level reverse.  The 20-byte buffer is
scanned as:

    +0x00  u32   magic       possibly ASCII 'ASAV' or similar title ID
    +0x04  u32   version     save-format revision (Azurik was v1)
    +0x08  u32   payload_len content bytes that follow the header
    +0x0C  u32   checksum    xor or CRC over the payload
    +0x10  u32   reserved    zero in every vanilla save we've seen
                             (possibly flags)

We do NOT assert magic / version values — the reader accepts any
20-byte header and exposes the fields as-is.  Writers that care
about validity should set ``magic``, ``version``, ``payload_len``,
and ``checksum`` consistent with the game's expectations before
serialising back.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path


_HEADER_SIZE = 0x14  # 20 bytes, confirmed by fread(buf, 0x14, 1, fp)


@dataclass
class SaveHeader:
    """Fixed 20-byte header at the start of every ``.sav`` file."""
    magic: int = 0
    version: int = 0
    payload_len: int = 0
    checksum: int = 0
    reserved: int = 0

    @classmethod
    def from_bytes(cls, data: bytes) -> "SaveHeader":
        """Parse the first 20 bytes of a ``.sav`` file."""
        if len(data) < _HEADER_SIZE:
            raise ValueError(
                f".sav header requires {_HEADER_SIZE} bytes; got {len(data)}")
        magic, version, payload_len, checksum, reserved = struct.unpack_from(
            "<IIIII", data, 0)
        return cls(
            magic=magic,
            version=version,
            payload_len=payload_len,
            checksum=checksum,
            reserved=reserved,
        )

    def to_bytes(self) -> bytes:
        """Serialise back to the 20-byte on-disk layout."""
        return struct.pack(
            "<IIIII",
            self.magic,
            self.version,
            self.payload_len,
            self.checksum,
            self.reserved,
        )

    def magic_as_ascii(self) -> str:
        """Best-effort ASCII rendering of ``magic``.

        Non-printable bytes are shown as ``.`` so the return value is
        always 4 characters and safe to print in reports.
        """
        bs = struct.pack("<I", self.magic)
        return "".join(
            chr(b) if 0x20 <= b < 0x7F else "." for b in bs)


@dataclass
class SaveChunk:
    """A (tentative) logical chunk inside the payload body.

    Current decoders don't emit real chunks — the whole body is
    returned as a single ``SaveChunk(name='payload', data=...)``
    entry.  When future work decodes specific structures (inventory
    table, entity positions, quest flags), they'll produce named
    chunks here.
    """
    name: str
    offset: int
    data: bytes


@dataclass
class AzurikSaveFile:
    """Base class for both ``signature.sav`` and ``<level>.sav``.

    Splits the file into a typed ``header`` + an opaque ``payload``
    byte string.  Round-trips losslessly: ``to_bytes()`` of a
    freshly-parsed file equals the input exactly.

    Subclasses (:class:`SignatureSav` / :class:`LevelSav`) override
    :meth:`iter_chunks` to emit structured sub-chunks as decoders
    get written.  The default implementation emits the payload as
    one chunk.
    """

    path: Path | None = None
    header: SaveHeader = field(default_factory=SaveHeader)
    payload: bytes = b""
    raw: bytes = b""
    """Original file bytes (header + payload concatenated, unmodified
    since read).  Useful for round-trip diffing when debugging
    decoders — any modification to ``header`` or ``payload`` leaves
    ``raw`` as the original on-disk reference."""

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_bytes(cls, data: bytes, *, path: Path | None = None) -> "AzurikSaveFile":
        """Parse a ``.sav`` byte blob.

        Dispatches to :class:`SignatureSav` or :class:`LevelSav`
        based on the file path's basename when available; otherwise
        returns a generic :class:`AzurikSaveFile` the caller can
        wrap as they see fit.
        """
        if len(data) < _HEADER_SIZE:
            raise ValueError(
                f".sav blob too small ({len(data)} B) — need at least "
                f"{_HEADER_SIZE} B for the header")

        header = SaveHeader.from_bytes(data[:_HEADER_SIZE])
        payload = data[_HEADER_SIZE:]

        if path is not None:
            name = path.name.lower()
            if name == "signature.sav":
                return SignatureSav(
                    path=path, header=header, payload=payload, raw=data)
            if name.endswith(".sav"):
                return LevelSav(
                    path=path, header=header, payload=payload, raw=data)

        return cls(path=path, header=header, payload=payload, raw=data)

    @classmethod
    def from_path(cls, path: str | Path) -> "AzurikSaveFile":
        p = Path(path)
        return cls.from_bytes(p.read_bytes(), path=p)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_bytes(self) -> bytes:
        """Serialise back to the on-disk byte layout.

        Uses the current ``header`` + ``payload`` — NOT ``raw``.
        Modifications made via the header / payload mutators carry
        through to the output.  For byte-identical round-tripping
        of an unmodified file, use ``raw`` directly.
        """
        return self.header.to_bytes() + self.payload

    def write(self, path: str | Path | None = None) -> Path:
        """Write this save file to disk.  Defaults to ``self.path``."""
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ValueError("no path provided and self.path is unset")
        target.write_bytes(self.to_bytes())
        return target

    # ------------------------------------------------------------------
    # Introspection (extended by subclasses)
    # ------------------------------------------------------------------

    def iter_chunks(self):
        """Yield logical sub-chunks of the payload.

        Default implementation emits the entire payload as one
        opaque chunk.  Subclasses override to expose structured
        fields as decoders are written.
        """
        yield SaveChunk(name="payload", offset=_HEADER_SIZE, data=self.payload)

    def summary(self) -> dict[str, object]:
        """Small dict suitable for JSON / CLI output."""
        return {
            "path": str(self.path) if self.path else None,
            "size_bytes": len(self.raw) if self.raw else len(self.to_bytes()),
            "header": {
                "magic": f"0x{self.header.magic:08X}",
                "magic_ascii": self.header.magic_as_ascii(),
                "version": self.header.version,
                "payload_len": self.header.payload_len,
                "checksum": f"0x{self.header.checksum:08X}",
                "reserved": self.header.reserved,
            },
            "payload_actual_bytes": len(self.payload),
            "payload_declared_matches_actual": (
                self.header.payload_len == len(self.payload)),
        }


@dataclass
class SignatureSav(AzurikSaveFile):
    """Profile-level save (``signature.sav``).

    Contains the player's persistent cross-level state: inventory,
    equipped character, currency, completed quests.  Format decoding
    is future work; current implementation is a pass-through
    scaffold.
    """

    # When a decoder is written, add typed fields here (e.g.,
    # ``inventory_slots: list[InventoryItem]``) + override
    # :meth:`iter_chunks` to yield them.
    pass


@dataclass
class LevelSav(AzurikSaveFile):
    """Per-level save (``w4.sav``, ``earth2.sav``, etc.).

    Contains the level's entity state, quest flags, and world-event
    progress.  Naming convention extracted from the game's
    ``levels/<element>/<N>.sav`` directory structure.  Format
    decoding is future work.
    """

    def level_id(self) -> str | None:
        """Best-effort extraction of the level identifier from the
        filename (``w4.sav`` → ``w4``).  Returns None when we don't
        have a path context."""
        if self.path is None:
            return None
        stem = self.path.stem  # strips .sav
        return stem or None
