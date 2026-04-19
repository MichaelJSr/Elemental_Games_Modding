"""Parse Azurik's ``filelist.txt`` — the game's integrity manifest.

Layout (observed in the vanilla ISO):

    \\
    f 3e4434a1670779cb9e6d146fb6b9371d 33246174 a1.xbr
    f 95f12691d35e92e40ca04db1deac74de 30721030 a3.xbr
    ...
    d index

    \\index\\
    f 38019152ff8e383aa6eb3f41efff33d2   167958 index.xbr

- ``\\`` / ``\\subdir\\`` lines declare the current directory scope.
- ``f <md5> <bytes> <name>`` — regular file entry.
- ``d <name>`` — subdirectory (followed later by its own scope line).

The parser flattens everything into path-keyed entries.  Use
:py:meth:`FilelistManifest.verify` to check an unpacked ISO against
the manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import md5
from pathlib import Path

__all__ = [
    "FileEntry",
    "FilelistManifest",
    "IntegrityIssue",
    "load_filelist",
]


@dataclass(frozen=True)
class FileEntry:
    """One ``f <md5> <bytes> <name>`` row.

    Attributes
    ----------
    path: str
        Relative path (forward-slash separated) from the ISO root.
    md5: str
        Lowercase hex MD5 digest (32 chars).
    size: int
        Byte size the manifest expects.
    """

    path: str
    md5: str
    size: int


@dataclass(frozen=True)
class IntegrityIssue:
    """One mismatch discovered by :py:meth:`FilelistManifest.verify`."""

    path: str
    kind: str  # "missing" | "size_mismatch" | "md5_mismatch"
    expected: str | int | None = None
    actual: str | int | None = None

    def __str__(self) -> str:
        if self.kind == "missing":
            return f"missing: {self.path}"
        if self.kind == "size_mismatch":
            return (f"size mismatch: {self.path} "
                    f"(expected {self.expected}, got {self.actual})")
        return (f"md5 mismatch: {self.path} "
                f"(expected {self.expected}, got {self.actual})")


@dataclass(frozen=True)
class FilelistManifest:
    """Parsed ``filelist.txt``.

    Use :func:`load_filelist` to build from disk.
    """

    entries: tuple[FileEntry, ...]

    # ------------------------------------------------------------------

    def by_path(self) -> dict[str, FileEntry]:
        """Dict view keyed by forward-slash path."""
        return {e.path: e for e in self.entries}

    def _resolve_root(self, root: Path) -> Path:
        """Determine whether the manifest's paths live directly
        under ``root`` or one level deeper inside ``gamedata/``.

        The vanilla Azurik ISO uses the latter: its ``filelist.txt``
        sits at the ISO root but declares paths like ``a1.xbr``
        which actually live at ``gamedata/a1.xbr``.  We pick the
        layout that matches the MOST first-three manifest entries
        to stay robust against either layout.
        """
        if not self.entries:
            return root
        probe = self.entries[:3]
        direct_hits = sum(1 for e in probe if (root / e.path).exists())
        nested = root / "gamedata"
        nested_hits = sum(1 for e in probe if (nested / e.path).exists())
        if nested_hits > direct_hits:
            return nested
        return root

    def lookup(self, filename: str | Path) -> FileEntry | None:
        """Find an entry by basename (case-insensitive).

        Returns the FIRST match; basenames are unique in the
        vanilla manifest so this is deterministic for shipped
        files.
        """
        basename = Path(filename).name.lower()
        for e in self.entries:
            if Path(e.path).name.lower() == basename:
                return e
        return None

    def total_size(self) -> int:
        """Sum of all declared byte sizes."""
        return sum(e.size for e in self.entries)

    # ------------------------------------------------------------------

    def verify(self, iso_root: str | Path, *,
               check_md5: bool = True,
               limit: int | None = None
               ) -> list[IntegrityIssue]:
        """Compare declared manifest vs actual files under ``iso_root``.

        Parameters
        ----------
        iso_root: path
            Directory the manifest paths are relative to.  Azurik's
            vanilla ``filelist.txt`` uses paths relative to the
            ``gamedata/`` subdirectory (its top-level scope line is
            just ``\\``), but this helper auto-detects and falls
            back to ``iso_root / "gamedata"`` when the direct path
            doesn't exist.
        check_md5: bool
            When ``False`` only sizes are compared.  Skipping MD5
            is useful for a quick sanity pass on a large ISO
            (``filelist`` hashes ~1 GB of data).
        limit: int, optional
            Stop after this many issues are detected.  ``None``
            means scan everything.

        Returns
        -------
        list[IntegrityIssue]
            Empty list = manifest matches.  Entries describe the
            first ``limit`` problems encountered.
        """
        root = Path(iso_root)
        resolved_root = self._resolve_root(root)
        issues: list[IntegrityIssue] = []
        for entry in self.entries:
            if limit is not None and len(issues) >= limit:
                break
            full = resolved_root / entry.path
            if not full.exists():
                issues.append(IntegrityIssue(
                    path=entry.path, kind="missing"))
                continue
            actual_size = full.stat().st_size
            if actual_size != entry.size:
                issues.append(IntegrityIssue(
                    path=entry.path, kind="size_mismatch",
                    expected=entry.size, actual=actual_size))
                continue
            if check_md5:
                # Stream the hash to avoid loading 90 MB XBRs
                # into memory all at once.
                digest = md5()
                with full.open("rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        digest.update(chunk)
                got = digest.hexdigest()
                if got != entry.md5.lower():
                    issues.append(IntegrityIssue(
                        path=entry.path, kind="md5_mismatch",
                        expected=entry.md5, actual=got))
        return issues


# ---------------------------------------------------------------------------

def _normalise(subdir: str, name: str) -> str:
    sub = subdir.strip().strip("\\").strip("/").replace("\\", "/")
    n = name.strip()
    return f"{sub}/{n}" if sub else n


def load_filelist(path: str | Path) -> FilelistManifest:
    """Parse ``filelist.txt`` into a :class:`FilelistManifest`.

    The file is ASCII-only in every vanilla dump we've seen.
    Unknown row kinds (anything other than ``f`` / ``d`` / scope)
    are ignored for forward-compat.

    Raises
    ------
    FileNotFoundError
        ``path`` does not exist.
    ValueError
        The file parses to zero entries (almost certainly a wrong
        file was passed).
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")

    current_subdir = ""
    entries: list[FileEntry] = []

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        # Scope line — starts with a backslash (Azurik's native
        # DOS-ish convention).
        if line.startswith("\\"):
            current_subdir = line
            continue
        parts = line.split(None, 3)
        if not parts:
            continue
        kind = parts[0]
        if kind == "f" and len(parts) == 4:
            _, md5_hex, size_s, name = parts
            try:
                size = int(size_s)
            except ValueError:
                continue
            entries.append(FileEntry(
                path=_normalise(current_subdir, name),
                md5=md5_hex.lower(),
                size=size,
            ))
        # "d" rows define subdirectories; we don't need to track
        # them explicitly — their contents appear later under a
        # matching scope line.

    if not entries:
        raise ValueError(f"{p.name}: no file entries parsed")

    return FilelistManifest(entries=tuple(entries))
