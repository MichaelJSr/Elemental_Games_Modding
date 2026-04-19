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
import hmac
import multiprocessing as mp
import os
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
# HMAC-SHA1 shim
# ---------------------------------------------------------------------------
#
# Benchmark note (2026-04, M-series Mac):
#
#   stdlib hmac.new(...).digest()        : ~24,000 calls/sec (101 KB msg)
#   hand-rolled bytes(k ^ 0x36 for ...)  :  ~2,300 calls/sec
#
# The stdlib implementation wins by 10× because its ``translate()``-
# based ipad/opad computation is C-level, and the per-key HMAC object
# construction overhead is small relative to SHA-1 over 101 KB.  We
# just delegate.


def _hmac_sha1(key: bytes, msg: bytes) -> bytes:
    """HMAC-SHA1 of ``msg`` with ``key`` — thin stdlib wrapper."""
    return hmac.new(key, msg, hashlib.sha1).digest()


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------


def recover_keys(dump: bytes,
                 samples: list[SaveSample],
                 *,
                 alignment: int = 4,
                 early_exit_after: int | None = None,
                 progress_cb=None,
                 workers: int = 1,
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

    ``workers`` spawns a multiprocessing pool for parallel
    scanning.  Each worker gets a non-overlapping slice of the
    dump; the parent merges hits at the end.  Set to ``1``
    (default) for the single-process path that supports
    progress callbacks + early-exit.  Multi-worker mode
    suppresses progress (workers can't cheaply gossip) and
    ignores ``early_exit_after`` — use it for bulk scans where
    you want the complete hit list anyway.
    """
    if alignment < 1:
        raise ValueError("alignment must be >= 1")
    if not samples:
        raise ValueError("at least one SaveSample required")

    if workers > 1:
        yield from _recover_parallel(
            dump, samples, alignment=alignment, workers=workers)
        return

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


# ---------------------------------------------------------------------------
# Multiprocess helpers
# ---------------------------------------------------------------------------


# Worker-side globals — populated via the initializer so the forked
# child doesn't re-pickle the 64 MB dump for every chunk.
_WORKER_DUMP: bytes | None = None
_WORKER_SAMPLES: list[SaveSample] | None = None


def _worker_init(dump: bytes,
                 samples: list[SaveSample]) -> None:
    """Pool initializer — stashes the big bytes buffer and
    samples into module globals so chunk tasks don't have to
    re-transfer them."""
    global _WORKER_DUMP, _WORKER_SAMPLES
    _WORKER_DUMP = dump
    _WORKER_SAMPLES = samples


def _worker_scan(args: tuple[int, int, int]
                 ) -> list[tuple[int, bytes]]:
    """Scan ``[start, end)`` with ``alignment`` step.
    Returns ``(offset, key)`` tuples for every full match."""
    start, end, alignment = args
    assert _WORKER_DUMP is not None
    assert _WORKER_SAMPLES is not None
    dump = _WORKER_DUMP
    samples = _WORKER_SAMPLES
    primary_walk = samples[0].walk_bytes
    primary_sig = samples[0].expected_signature
    tail = samples[1:]
    hits: list[tuple[int, bytes]] = []
    for off in range(start, end, alignment):
        if off + 16 > len(dump):
            break
        key = bytes(dump[off:off + 16])
        if _hmac_sha1(key, primary_walk) == primary_sig:
            if all(_hmac_sha1(key, s.walk_bytes) == s.expected_signature
                   for s in tail):
                hits.append((off, key))
    return hits


def _recover_parallel(dump: bytes,
                       samples: list[SaveSample],
                       *,
                       alignment: int,
                       workers: int,
                       ) -> Iterator[KeyCandidate]:
    """Parallel scan — splits the dump into one chunk per worker.

    Dominant cost is the SHA-1 over the 101 KB message per
    candidate (~40 µs in C-level stdlib on Apple Silicon).  Linear
    speedup with core count, modulo the Python multiprocessing
    overhead which is negligible at 64 MB dump size.
    """
    total = max(0, len(dump) - 16)
    if total == 0:
        return
    # Round chunk size up so the last worker covers the remainder.
    chunk = (total + workers - 1) // workers
    jobs = []
    for i in range(workers):
        a = i * chunk
        b = min((i + 1) * chunk, total)
        if a < b:
            jobs.append((a, b, alignment))
    # ``fork`` on POSIX inherits the parent's memory map so the
    # 64 MB dump isn't re-transferred per chunk; ``spawn`` on
    # macOS would re-pickle it.  Force fork where possible to
    # keep startup cheap.
    ctx = mp.get_context("fork") if os.name == "posix" else mp.get_context()
    with ctx.Pool(
            processes=workers,
            initializer=_worker_init,
            initargs=(dump, samples)) as pool:
        for chunk_hits in pool.imap_unordered(_worker_scan, jobs):
            for off, key in chunk_hits:
                yield KeyCandidate(offset=off, key=key)
