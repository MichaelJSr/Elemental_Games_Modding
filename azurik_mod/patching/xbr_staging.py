"""Lazy XBR-file staging cache for the ISO-build pipeline.

Sits between the extracted ``gamedata/`` tree and
:func:`~azurik_mod.patching.apply.apply_pack`.  Loads each XBR
on first access, accumulates in-memory mutations across every
pack's ``xbr_sites``, and flushes the modified files back to
disk in one batch.

Layout / lifecycle::

    with XbrStaging(extract_dir) as staging:
        for pack in enabled_packs:
            apply_pack(pack, xbe_data, params,
                       xbr_files=staging)
        staging.flush()

The ``XbrStaging`` object quacks like a ``dict[str, bytearray]``
so it plugs straight into :func:`apply_pack` without any
bridge code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional


# The "gamedata" subdirectory within an extracted Azurik ISO.  Every
# XBR lives somewhere under here (top-level files + index/index.xbr).
_GAMEDATA_SUBDIR = "gamedata"


class XbrStaging:
    """Path-aware, lazily-populated ``{filename: bytearray}`` view
    backed by an extracted-ISO directory.

    Looks up ``filename`` first in ``gamedata/<filename>`` (the
    canonical Azurik layout), then in ``gamedata/index/<filename>``
    (where ``index.xbr`` lives), then in ``<extract_dir>/<filename>``
    directly as a fallback for flat fixtures used in tests.

    Accessed XBRs are loaded once and cached.  Callers mutate
    ``staging[filename]`` — a ``bytearray`` — and every subsequent
    access returns the same buffer.  :meth:`flush` writes every
    touched file back to disk.
    """

    def __init__(self, extract_dir: Path) -> None:
        self.extract_dir = Path(extract_dir)
        self._cache: dict[str, bytearray] = {}
        self._paths: dict[str, Path] = {}

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def _resolve(self, filename: str) -> Optional[Path]:
        """Return the on-disk path for ``filename`` (or ``None``)."""
        if filename in self._paths:
            return self._paths[filename]
        candidates = [
            self.extract_dir / _GAMEDATA_SUBDIR / filename,
            self.extract_dir / _GAMEDATA_SUBDIR / "index" / filename,
            self.extract_dir / filename,
        ]
        for p in candidates:
            if p.exists():
                self._paths[filename] = p
                return p
        return None

    # ------------------------------------------------------------------
    # Dict-like surface — what apply_pack / apply_xbr_edit_spec expect
    # ------------------------------------------------------------------

    def __contains__(self, filename: object) -> bool:
        if not isinstance(filename, str):
            return False
        return self._resolve(filename) is not None

    def __getitem__(self, filename: str) -> bytearray:
        cached = self._cache.get(filename)
        if cached is not None:
            return cached
        p = self._resolve(filename)
        if p is None:
            raise KeyError(
                f"{filename!r} not found under "
                f"{self.extract_dir}; looked in "
                f"{_GAMEDATA_SUBDIR}/, {_GAMEDATA_SUBDIR}/index/, "
                f"and the extract root.")
        buf = bytearray(p.read_bytes())
        self._cache[filename] = buf
        return buf

    def __setitem__(self, filename: str, value: bytearray) -> None:
        self._cache[filename] = value

    def mark_dirty(self, filename: str) -> None:
        """No-op kept for API compatibility — the flush path
        already compares every loaded buffer against disk.  Extra
        hint calls by the edit dispatchers do no harm."""
        return

    def get(self, filename: str, default=None):
        """Match ``dict.get`` so :func:`apply_xbr_edit_spec` works
        without a dedicated ``apply_xbr_pack_with_staging`` bridge."""
        try:
            return self[filename]
        except KeyError:
            return default

    def keys(self) -> Iterator[str]:
        return iter(self._cache)

    def __iter__(self) -> Iterator[str]:
        """Iterating the staging yields every cached filename — the
        dict-like contract ``list(xbr_files)`` callers rely on."""
        return iter(self._cache)

    # ------------------------------------------------------------------
    # Flush
    # ------------------------------------------------------------------

    def flush(self) -> list[str]:
        """Write every mutated cached buffer back to disk.

        Returns the list of filenames that were actually written.
        Cached buffers whose contents still match the on-disk
        bytes are skipped — read-only accesses and no-op edits
        don't touch the filesystem.
        """
        written: list[str] = []
        for filename, buf in self._cache.items():
            path = self._paths.get(filename)
            if path is None:
                continue
            current = path.read_bytes()
            if current == bytes(buf):
                continue
            path.write_bytes(bytes(buf))
            written.append(filename)
        return written

    # ------------------------------------------------------------------
    # Context manager — opt-in flush semantics
    # ------------------------------------------------------------------

    def __enter__(self) -> "XbrStaging":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Don't auto-flush on exception — the caller may want to
        # bail without persisting partial edits.  Normal-path
        # callers call :meth:`flush` explicitly.
        pass


__all__ = ["XbrStaging"]
