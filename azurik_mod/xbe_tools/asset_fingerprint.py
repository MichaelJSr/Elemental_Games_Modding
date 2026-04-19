"""Asset fingerprint registry — #25 from ``docs/TOOLING_ROADMAP.md``.

Collects an ISO / unpacked-tree / single-file into a deterministic,
content-addressed fingerprint and diffs it against an earlier
fingerprint so mod authors can tell, at a glance, *exactly which
files changed* across a build.

## Why a fingerprint and not a diff?

A fingerprint is the tiny JSON (~50-200 KB for vanilla Azurik) you
commit beside the mod source.  It persists cheaply, diffs with
``git diff``, and can be fed back through this tool for
*"what moved since version X?"* queries without needing two full
ISOs on hand.

## Content hashing

- Files ≤ 64 MiB: full SHA-1 (cheap, widely trusted for integrity).
- Larger files: SHA-1 over the first MiB + last MiB + size —
  adequate for XBR mutations (the game never appends to a 1 GiB
  file in-place) and keeps CLI runtime sane on laptops.

Fingerprint format (JSON)::

    {
      "version": 1,
      "root": "<relative path>",
      "generated_at": "2026-04-16T12:00:00",
      "entries": [
        {"path": "gamedata/a1.xbr",
         "size": 6144321,
         "sha1": "<40 hex>",
         "hash_mode": "full"},
        ...
      ]
    }

Humans can diff two fingerprints manually (``diff -u``) but the
CLI does a smarter :func:`diff_fingerprints` pass that groups
``added`` / ``removed`` / ``modified`` entries.

## Relationship to ``filelist.txt``

Vanilla Azurik ships its own :mod:`~azurik_mod.assets.filelist`
manifest (MD5 + size) — we prefer fingerprints because:

- They're whole-tree (covers manifest-absent files too).
- They're versioned (``version`` bumps when we add fields).
- They don't depend on ``filelist.txt`` being present, which is
  important for hand-built trees.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

__all__ = [
    "AssetFingerprint",
    "FingerprintEntry",
    "build_fingerprint",
    "diff_fingerprints",
    "load_fingerprint",
    "save_fingerprint",
]


FULL_HASH_CAP = 64 * 1024 * 1024
_CHUNK_SIZE = 1 << 20
FINGERPRINT_VERSION = 1


@dataclass(frozen=True)
class FingerprintEntry:
    """One file's contribution to a :class:`AssetFingerprint`."""

    path: str
    size: int
    sha1: str
    hash_mode: str    # "full" for ≤FULL_HASH_CAP, "sparse" otherwise

    def to_json_dict(self) -> dict:
        return {
            "path": self.path, "size": self.size,
            "sha1": self.sha1, "hash_mode": self.hash_mode,
        }

    @classmethod
    def from_json(cls, obj: dict) -> "FingerprintEntry":
        return cls(
            path=str(obj.get("path", "")),
            size=int(obj.get("size", 0)),
            sha1=str(obj.get("sha1", "")),
            hash_mode=str(obj.get("hash_mode", "full")),
        )


@dataclass(frozen=True)
class AssetFingerprint:
    """Content hash of every file under a root (with metadata)."""

    version: int
    root: str
    generated_at: str
    entries: tuple[FingerprintEntry, ...]

    def by_path(self) -> dict[str, FingerprintEntry]:
        return {e.path: e for e in self.entries}

    def to_json_dict(self) -> dict:
        return {
            "version": self.version,
            "root": self.root,
            "generated_at": self.generated_at,
            "entries": [e.to_json_dict() for e in self.entries],
        }

    @classmethod
    def from_json(cls, obj: dict) -> "AssetFingerprint":
        return cls(
            version=int(obj.get("version", 1)),
            root=str(obj.get("root", "")),
            generated_at=str(obj.get("generated_at", "")),
            entries=tuple(
                FingerprintEntry.from_json(e)
                for e in obj.get("entries", [])),
        )


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def build_fingerprint(root: Path, *,
                      include: Iterable[str] | None = None,
                      exclude: Iterable[str] | None = None,
                      max_full_hash_bytes: int = FULL_HASH_CAP,
                      ) -> AssetFingerprint:
    """Walk ``root`` and produce an :class:`AssetFingerprint`.

    ``include`` / ``exclude`` glob-match against the relative path
    (forward-slash normalised).  Globs use :meth:`Path.match`
    semantics.  Hidden / dotfiles are skipped by default (common
    noise in IDE + VCS trees).
    """
    root = Path(root).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"root does not exist: {root}")
    if root.is_file():
        files = [root]
        base = root.parent
    else:
        files = sorted(_walk_files(root))
        base = root
    include_globs = tuple(include or ())
    exclude_globs = tuple(exclude or ())

    entries: list[FingerprintEntry] = []
    for p in files:
        rel = _rel_posix(base, p)
        if _is_hidden(rel):
            continue
        if include_globs and not any(
                Path(rel).match(g) for g in include_globs):
            continue
        if any(Path(rel).match(g) for g in exclude_globs):
            continue
        entries.append(_fingerprint_file(
            p, rel_path=rel,
            max_full_hash_bytes=max_full_hash_bytes))
    return AssetFingerprint(
        version=FINGERPRINT_VERSION,
        root=str(root),
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        entries=tuple(entries),
    )


def _walk_files(root: Path) -> Iterator[Path]:
    for p in root.rglob("*"):
        if p.is_file():
            yield p


def _rel_posix(base: Path, p: Path) -> str:
    try:
        return p.relative_to(base).as_posix()
    except ValueError:
        return p.as_posix()


def _is_hidden(rel_path: str) -> bool:
    return any(part.startswith(".") for part in rel_path.split("/"))


def _fingerprint_file(p: Path, *, rel_path: str,
                      max_full_hash_bytes: int
                      ) -> FingerprintEntry:
    size = p.stat().st_size
    digest = hashlib.sha1()
    if size <= max_full_hash_bytes:
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
                digest.update(chunk)
        hash_mode = "full"
    else:
        with p.open("rb") as fh:
            digest.update(fh.read(_CHUNK_SIZE))
            fh.seek(-_CHUNK_SIZE, 2)
            digest.update(fh.read(_CHUNK_SIZE))
        digest.update(size.to_bytes(8, "little"))
        hash_mode = "sparse"
    return FingerprintEntry(
        path=rel_path, size=size,
        sha1=digest.hexdigest(), hash_mode=hash_mode)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_fingerprint(fp: AssetFingerprint, path: Path) -> None:
    """Write a fingerprint to disk as indented JSON (diff-friendly)."""
    path.write_text(
        json.dumps(fp.to_json_dict(), indent=2),
        encoding="utf-8")


def load_fingerprint(path: Path) -> AssetFingerprint:
    """Read a fingerprint from disk; raises on bad JSON or an
    unknown ``version``."""
    raw = path.read_text(encoding="utf-8")
    obj = json.loads(raw)
    ver = int(obj.get("version", 0))
    if ver != FINGERPRINT_VERSION:
        raise ValueError(
            f"unsupported fingerprint version {ver} in {path} "
            f"(tool supports v{FINGERPRINT_VERSION})")
    return AssetFingerprint.from_json(obj)


# ---------------------------------------------------------------------------
# Diffing
# ---------------------------------------------------------------------------


@dataclass
class FingerprintDiff:
    """Structured diff between two fingerprints."""

    added: tuple[FingerprintEntry, ...]
    removed: tuple[FingerprintEntry, ...]
    modified: tuple[tuple[FingerprintEntry,
                          FingerprintEntry], ...]
    unchanged: int = 0

    def changed_paths(self) -> list[str]:
        paths: list[str] = []
        paths.extend(e.path for e in self.added)
        paths.extend(e.path for e in self.removed)
        paths.extend(new.path for _old, new in self.modified)
        return sorted(paths)

    def to_json_dict(self) -> dict:
        return {
            "added": [e.to_json_dict() for e in self.added],
            "removed": [e.to_json_dict() for e in self.removed],
            "modified": [
                {"old": old.to_json_dict(),
                 "new": new.to_json_dict()}
                for old, new in self.modified
            ],
            "unchanged": self.unchanged,
        }


def diff_fingerprints(before: AssetFingerprint,
                      after: AssetFingerprint) -> FingerprintDiff:
    """Compute a per-path diff between two fingerprints.

    Sorts every list so the output is byte-for-byte stable across
    runs — downstream tests snapshot exact output.
    """
    a = before.by_path()
    b = after.by_path()
    all_paths = set(a) | set(b)
    added: list[FingerprintEntry] = []
    removed: list[FingerprintEntry] = []
    modified: list[tuple[FingerprintEntry,
                         FingerprintEntry]] = []
    unchanged = 0
    for path in sorted(all_paths):
        old = a.get(path)
        new = b.get(path)
        if old is None and new is not None:
            added.append(new)
        elif new is None and old is not None:
            removed.append(old)
        elif old != new and old is not None and new is not None:
            if (old.size == new.size and old.sha1 == new.sha1):
                unchanged += 1
            else:
                modified.append((old, new))
        else:
            unchanged += 1
    return FingerprintDiff(
        added=tuple(added),
        removed=tuple(removed),
        modified=tuple(modified),
        unchanged=unchanged,
    )
