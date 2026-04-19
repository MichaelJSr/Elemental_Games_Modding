"""Azurik save-slot signature (HMAC-SHA1) helper.

Derived from reverse-engineering the save-sign path in
``default.xbe`` (``FUN_0005c4b0`` + the caller at VA
``0x0005c920``).  The tree-walk order is fully pinned here so
callers don't have to re-solve it.

See ``docs/SAVE_FORMAT.md`` § 7 for the full RE writeup.

## API

- :func:`compute_signature_walk` — run the HMAC-SHA1 update
  sequence over a save-slot directory in the exact order
  Azurik's signer does.  Accepts any ``hmac.HMAC`` / hashlib-
  compatible context; the caller picks the key.
- :func:`compute_signature` — convenience wrapper that
  constructs an ``hmac.HMAC`` with the caller-provided
  ``xbox_signature_key`` and returns the 20-byte digest.

## Known-good key derivation

On retail hardware the ``XboxSignatureKey`` global is derived
at runtime from the EEPROM HDKey + XBE cert.  We don't
currently emulate that derivation — see SAVE_FORMAT.md § 7.
Callers who've extracted the key from a live xemu session can
pass it via ``xbox_signature_key``; callers who haven't should
use the "re-sign via game round-trip" workflow that
:class:`~azurik_mod.save_format.editor.SaveEditor` documents.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Protocol

__all__ = [
    "SIGNATURE_FILENAME",
    "compute_signature",
    "compute_signature_walk",
]


SIGNATURE_FILENAME = "signature.sav"
_CHUNK_SIZE = 0x4000       # matches the game's own fread buffer
_SAV_SUFFIX = ".sav"


class _HashCtx(Protocol):
    """Minimal hashlib/HMAC-compatible interface."""

    def update(self, data: bytes) -> None: ...


def compute_signature_walk(root: Path, ctx: _HashCtx) -> int:
    """Run the save-signature tree walk against ``root``.

    Feeds ``ctx.update`` with the exact byte sequence the
    Xbox-side signer produces.  Returns the count of files
    hashed so callers can sanity-check against a known slot.

    The walk is purely destination-agnostic: it doesn't care
    whether ``ctx`` is HMAC-SHA1, plain SHA-1, or a mock —
    that flexibility keeps the tests tight and lets future
    work drop in alternative hashing once the key derivation
    is decoded.
    """
    return _walk_dir(Path(root), ctx)


def _walk_dir(directory: Path, ctx: _HashCtx) -> int:
    files: list[Path] = []
    subdirs: list[Path] = []
    for child in directory.iterdir():
        if child.name in (".", ".."):
            continue
        if child.is_file():
            if child.name.lower() == SIGNATURE_FILENAME:
                continue
            if child.suffix.lower() != _SAV_SUFFIX:
                continue
            files.append(child)
        elif child.is_dir():
            subdirs.append(child)

    # Sort alphabetically case-insensitively.  FATX is case-
    # insensitive by design, and the game's enumeration order
    # matches Windows' FindFirstFile which (on FATX) returns
    # entries in their on-disk order — effectively lowercase
    # alphabetical for all the names we care about.
    files.sort(key=lambda p: p.name.lower())
    subdirs.sort(key=lambda p: p.name.lower())

    hashed_count = 0
    for f in files:
        _hash_name(ctx, f.name)
        with f.open("rb") as fh:
            while True:
                chunk = fh.read(_CHUNK_SIZE)
                if not chunk:
                    break
                ctx.update(chunk)
        hashed_count += 1
    for sd in subdirs:
        _hash_name(ctx, sd.name)
        hashed_count += _walk_dir(sd, ctx)
    return hashed_count


def _hash_name(ctx: _HashCtx, name: str) -> None:
    """Feed ``name`` + NUL into ``ctx`` the same way the game
    does.  Non-ASCII filenames are not expected — FATX
    enforces ASCII — but we encode with ``errors="replace"``
    to stay defensive."""
    ctx.update(name.encode("ascii", errors="replace") + b"\x00")


def compute_signature(root: Path, *,
                      xbox_signature_key: bytes) -> bytes:
    """Return the 20-byte HMAC-SHA1 signature Azurik expects.

    ``xbox_signature_key`` must be the 16-byte value that the
    Xbox kernel computed as ``XboxSignatureKey`` at boot time.
    We currently don't emulate that derivation, so callers are
    expected to extract it from a live xemu session (or future
    work: decode the derivation and compute it here).

    See ``docs/SAVE_FORMAT.md`` § 7 for the state of the
    key-derivation RE.
    """
    if len(xbox_signature_key) != 16:
        raise ValueError(
            f"xbox_signature_key must be 16 bytes "
            f"(HMAC-SHA1 key), got {len(xbox_signature_key)}")
    h = hmac.new(xbox_signature_key, digestmod=hashlib.sha1)
    compute_signature_walk(Path(root), h)
    return h.digest()
