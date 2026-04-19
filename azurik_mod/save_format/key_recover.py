"""Signature-key recovery — brute-force search for an HMAC-SHA1 key.

## Why this exists

The save-signature HMAC key (``XboxSignatureKey``) is a runtime-
derived kernel global we can't compute statically (see
``docs/SAVE_FORMAT.md`` § 7).  Every static source we've tried has
failed:

- XBE certificate signature_key / lan_key / alt_signature_keys
- EEPROM bytes (encrypted; xemu's RC4 key version unknown)
- XBE body scan (4-byte aligned 3.7 MB)
- SaveMeta.xbx / saveimage.xbx
- HMAC-SHA1(A, B) derivations from every ingredient permutation

The remaining path is **dynamic recovery**: dump xemu's guest RAM
(via xemu's debug monitor, gdb-stub, or a memory dump during a
save operation) and scan the dump for the 16 key bytes.  With N
saves + expected signatures, this tool brute-forces every 16-byte
window (optionally at any alignment) against the saves' walks
and returns keys that match all of them.

## Usage

    azurik-mod save key-recover \\
        --dump xemu-ram.bin \\
        --save scripts/save1_extracted/5C0A938BD9AC \\
        --save scripts/save2_extracted/5C0A938BD9AC \\
        [--alignment 4] [--json]

Feed at least **2 save dirs** to rule out random collisions
(HMAC-SHA1 output has ~2^-160 false-positive probability per
window; one sample confirms by coincidence at rate ~2^-128 per
256 MB dump, still astronomical but good to double-check).

With one save: emits every candidate for inspection.
With two or more: only keys that match ALL saves are reported.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from azurik_mod.save_format.signature import (
    SIGNATURE_FILENAME,
    compute_signature_walk,
)

__all__ = [
    "KeyCandidate",
    "SaveSample",
    "load_save_sample",
    "recover_keys",
]


@dataclass(frozen=True)
class SaveSample:
    """One (walk_bytes, expected_signature) pair."""

    slot_path: str
    walk_bytes: bytes
    expected_signature: bytes


@dataclass(frozen=True)
class KeyCandidate:
    """A 16-byte value from the dump that produces a valid
    HMAC-SHA1 for every provided :class:`SaveSample`."""

    offset: int
    key: bytes      # always 16 bytes

    def hex_key(self) -> str:
        return self.key.hex()


# ---------------------------------------------------------------------------
# Walk/sample helpers
# ---------------------------------------------------------------------------


def load_save_sample(slot: Path) -> SaveSample:
    """Build a :class:`SaveSample` from an exported save slot.

    Reads ``signature.sav`` and runs the in-house walker to
    pre-compute the byte sequence the signer sees.  Raises
    :exc:`FileNotFoundError` if the slot is missing its
    signature.
    """
    slot = Path(slot)
    sig_path = slot / SIGNATURE_FILENAME
    if not sig_path.exists():
        raise FileNotFoundError(
            f"{sig_path} missing — can't verify candidate keys "
            f"against this slot")

    class _Rec:
        def __init__(self) -> None:
            self.buf = bytearray()

        def update(self, data: bytes) -> None:
            self.buf.extend(data)

    rec = _Rec()
    compute_signature_walk(slot, rec)
    return SaveSample(
        slot_path=str(slot),
        walk_bytes=bytes(rec.buf),
        expected_signature=sig_path.read_bytes(),
    )


# ---------------------------------------------------------------------------
# Manual HMAC-SHA1 (faster than hmac.new() in a tight loop)
# ---------------------------------------------------------------------------


_IPAD_BYTE = 0x36
_OPAD_BYTE = 0x5C
_BLOCK = 64


def _hmac_sha1(key: bytes, msg: bytes) -> bytes:
    """HMAC-SHA1 with slightly lower Python overhead than
    stdlib ``hmac.new(...).digest()``.

    Avoids the ``HMAC`` object construction; matters when we're
    testing hundreds of thousands of candidates.
    """
    if len(key) > _BLOCK:
        key = hashlib.sha1(key).digest() + b"\x00" * 44
    else:
        key = key + b"\x00" * (_BLOCK - len(key))
    ipad = bytes(k ^ _IPAD_BYTE for k in key)
    opad = bytes(k ^ _OPAD_BYTE for k in key)
    inner = hashlib.sha1(ipad + msg).digest()
    return hashlib.sha1(opad + inner).digest()


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------


def recover_keys(dump: bytes,
                 samples: list[SaveSample],
                 *,
                 alignment: int = 4,
                 early_exit_after: int | None = None,
                 progress_cb=None,
                 ) -> Iterator[KeyCandidate]:
    """Scan ``dump`` for 16-byte windows that HMAC-SHA1 every
    ``samples[i].walk_bytes`` to ``samples[i].expected_signature``.

    ``alignment`` controls the byte-step of the scan; ``4`` is
    sufficient for every Xbox-SDK-aligned structure we've seen
    and runs ~4× faster than byte alignment.  Use ``1`` for
    paranoid unaligned scans.

    ``early_exit_after`` stops after N full matches; pass
    ``None`` (default) for an exhaustive scan.

    ``progress_cb`` is invoked as
    ``progress_cb(bytes_scanned, total_bytes)`` every ~1M
    candidates so long scans can report progress.
    """
    if alignment < 1:
        raise ValueError("alignment must be >= 1")
    if not samples:
        raise ValueError("at least one SaveSample required")

    # Fast-path: verify against the first sample only in the
    # inner loop; fully verify against the remaining samples
    # only on a hit (vanishingly rare false positives).
    primary = samples[0]
    tail = samples[1:]
    primary_walk = primary.walk_bytes
    primary_sig = primary.expected_signature

    total = max(0, len(dump) - 16)
    found = 0
    progress_step = max(1_000_000, total // 50)

    for off in range(0, total, alignment):
        key = bytes(dump[off:off + 16])
        if _hmac_sha1(key, primary_walk) == primary_sig:
            if all(
                _hmac_sha1(key, s.walk_bytes) == s.expected_signature
                for s in tail
            ):
                yield KeyCandidate(offset=off, key=key)
                found += 1
                if (early_exit_after is not None
                        and found >= early_exit_after):
                    return
        if progress_cb is not None and off % progress_step == 0:
            progress_cb(off, total)

    if progress_cb is not None:
        progress_cb(total, total)
