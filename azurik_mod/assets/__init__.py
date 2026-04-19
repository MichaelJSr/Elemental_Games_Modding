"""Canonical Azurik ISO metadata (prefetch + filelist).

Azurik ships two plain-text index files alongside its XBR data:

- ``prefetch-lists.txt`` — the **authoritative level manifest**.
  Lists every XBR file the game knows about, partitions them into
  "always loaded" global resources vs per-zone level packs, and
  encodes the adjacency graph used by the streaming loader.
- ``filelist.txt`` — the **authoritative integrity manifest**.
  MD5 + byte-size for every file in ``gamedata/`` (and the
  ``index/`` subfolder).  Useful for catching corrupted /
  tampered ISOs before a mod run fails mysteriously.

Historically the repo hard-coded level lists and neighbor edges
inside ``azurik_mod/randomizer/shufflers.py``.  That worked but
silently drifted from the real game manifest — see
docs/LEARNINGS.md § prefetch-lists.txt for the audit trail.

This package is the single source of truth.  Downstream code
(randomizer, xbr_parser, integrity checkers, docs generators)
should read from here instead of duplicating the tables.
"""

from azurik_mod.assets.filelist import FileEntry, FilelistManifest
from azurik_mod.assets.prefetch import (
    PrefetchManifest,
    PrefetchTag,
    load_prefetch,
)

__all__ = [
    "FileEntry",
    "FilelistManifest",
    "PrefetchManifest",
    "PrefetchTag",
    "load_prefetch",
]
