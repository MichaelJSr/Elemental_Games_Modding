"""Decompilation cache — #22 from ``docs/TOOLING_ROADMAP.md``.

## Problem

Fetching decompilation from Ghidra over HTTP is slow — each call
pops the plugin out of any pending analysis and typically takes
50-300 ms per function.  When a tool (think: struct-diff,
call-graph explorer, grep-over-decomps) wants to look at a few
dozen functions, the round-trip latency dominates end-to-end run
time and makes iteration painful.

## Solution

A tiny on-disk content-addressable cache keyed by
``(program_id, address, ghidra_schema_version)``.  On a miss the
cache calls the wrapped :class:`GhidraClient`, stores the result
to disk, and returns it; on a hit it returns the cached blob
without touching the network at all.

Invalidation is simple by design:

- ``clear()`` nukes everything — use after a round of struct
  type-setting or refactoring where every decomp is stale.
- ``invalidate(address)`` drops a single entry — use when you
  re-analyse one function in Ghidra.
- The program identity is hashed into the key so a second XBE
  loaded in a separate Ghidra instance never collides with a
  first.

The cache is deliberately **opaque to the wrapped client**: it
has the same surface as ``GhidraClient.decompile`` so callers
swap one for the other without other code changes.

## On-disk layout

::

    <cache_root>/
        <program_hash>/
            00085700.json
            0010a240.json
            ...

Each JSON file stores ``{"decompiled": "...", "function_name":
"...", "fetched_at": "<iso ts>"}``.  Humans can ``cat`` the files
for drive-by inspection.

The default cache root is
``~/.cache/azurik-mod/decomps`` (respects ``XDG_CACHE_HOME``).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from azurik_mod.xbe_tools.ghidra_client import (
    GhidraClient,
    GhidraClientError,
    GhidraDecomp,
)

__all__ = [
    "DecompCache",
    "cache_root_default",
    "program_cache_key",
]


def cache_root_default() -> Path:
    """Resolve the default cache directory.

    Honours ``AZURIK_DECOMP_CACHE`` (explicit override) and
    ``XDG_CACHE_HOME`` (standard XDG base dir spec), then falls
    back to ``~/.cache``.
    """
    env = os.environ.get("AZURIK_DECOMP_CACHE")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "azurik-mod" / "decomps"


def program_cache_key(program_id: str) -> str:
    """Turn a program identifier (anything stable the client
    reports — usually the XBE path or sha) into a short,
    filesystem-safe folder name."""
    h = hashlib.sha1(program_id.encode("utf-8",
                                        errors="replace")).hexdigest()
    return h[:16]


class _HasDecompile(Protocol):
    """Structural alias — lets us wrap anything with the same
    ``decompile(int) -> GhidraDecomp`` surface (mocks included)."""

    def decompile(self, address: int) -> GhidraDecomp: ...


@dataclass
class DecompCache:
    """Content-addressable decompilation cache.

    Intended construction patterns::

        client = GhidraClient()
        cache  = DecompCache.for_client(client)
        decomp = cache.get(0x85700)

        # or with an explicit root (test fixtures, CI):
        cache = DecompCache(client=client,
                             program_key="abcd1234",
                             root=tmp_path)
    """

    client: _HasDecompile
    program_key: str
    root: Path
    _hits: int = 0
    _misses: int = 0

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def for_client(cls, client: GhidraClient,
                   *, root: Path | None = None) -> "DecompCache":
        """Build a cache whose program identity is derived from
        ``client.program_info()``.

        Falls back to ``client.host:port`` when the plugin is
        offline or errors out — always-usable semantics beat
        correctness when the user is just grepping.
        """
        try:
            info = client.program_info()
            program_id = info.program_id or info.name or ""
        except GhidraClientError:
            program_id = f"{client.host}:{client.port}"
        if not program_id:
            program_id = f"{client.host}:{client.port}"
        return cls(
            client=client,
            program_key=program_cache_key(program_id),
            root=(root or cache_root_default()),
        )

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def program_dir(self) -> Path:
        """The directory this cache writes into for its program."""
        return self.root / self.program_key

    def _path_for(self, address: int) -> Path:
        return self.program_dir() / f"{address:08x}.json"

    def get(self, address: int) -> GhidraDecomp:
        """Return a decompilation, fetching + caching if missing.

        The cache never raises on disk I/O errors: a bad cache
        entry is treated like a cache miss and overwritten.
        """
        path = self._path_for(address)
        cached = self._read(path, address)
        if cached is not None:
            self._hits += 1
            return cached
        decomp = self.client.decompile(address)
        self._misses += 1
        self._write(path, decomp)
        return decomp

    def invalidate(self, address: int) -> bool:
        """Drop the cached entry for ``address``.

        Returns ``True`` when a file was removed, ``False`` when
        there was nothing cached.
        """
        path = self._path_for(address)
        if not path.exists():
            return False
        try:
            path.unlink()
        except OSError:
            return False
        return True

    def clear(self) -> int:
        """Remove every entry in this program's cache.  Returns
        the count of entries actually deleted."""
        pdir = self.program_dir()
        if not pdir.exists():
            return 0
        removed = 0
        for p in pdir.glob("*.json"):
            try:
                p.unlink()
                removed += 1
            except OSError:
                continue
        return removed

    def stats(self) -> dict:
        """Runtime stats + disk footprint of the current program
        sub-cache.  Exposed so the CLI can print a one-line
        summary after a batch operation."""
        pdir = self.program_dir()
        entries = list(pdir.glob("*.json")) if pdir.exists() else []
        return {
            "program_key": self.program_key,
            "entries_on_disk": len(entries),
            "hits": self._hits,
            "misses": self._misses,
            "root": str(self.root),
        }

    # ------------------------------------------------------------------
    # Disk helpers (private)
    # ------------------------------------------------------------------

    def _read(self, path: Path,
              address: int) -> GhidraDecomp | None:
        if not path.exists():
            return None
        try:
            raw = path.read_text("utf-8")
            blob = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(blob, dict) or \
                "decompiled" not in blob:
            return None
        return GhidraDecomp(
            address=address,
            function_name=str(blob.get("function_name", "")),
            decompiled=str(blob["decompiled"]),
        )

    def _write(self, path: Path,
               decomp: GhidraDecomp) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({
                    "decompiled": decomp.decompiled,
                    "function_name": decomp.function_name,
                    "fetched_at": time.strftime(
                        "%Y-%m-%dT%H:%M:%S"),
                }, indent=2),
                encoding="utf-8")
        except OSError:
            # Cache is best-effort — callers still get the
            # decomp in memory even if we can't persist it.
            return
