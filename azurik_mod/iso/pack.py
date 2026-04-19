"""ISO extract / repack helpers.

Thin layer over :mod:`azurik_mod.iso.xdvdfs` that covers the everyday
use cases:

  - Unpack / repack a full ISO for the randomizer pipeline.
  - Extract a specific file (``config.xbr`` / ``default.xbe``) into
    memory without leaving temp directories lying around.

The two hot single-file extracts share the same ``copy-out + read_bytes
+ validate`` dance; :func:`_copy_out_bytes` factors it out so both
callers get consistent error handling + tempdir cleanup.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from azurik_mod.iso.xdvdfs import require_xdvdfs


# Relative path within the game folder where config.xbr lives.
CONFIG_XBR_REL = Path("gamedata") / "config.xbr"
# Level XBR files are flat inside gamedata/.
GAMEDATA_REL = Path("gamedata")


def run_xdvdfs(xdvdfs: str, args: list[str]) -> None:
    """Run xdvdfs with the given positional args; exit on non-zero.

    The xdvdfs binary path is accepted explicitly (rather than being
    re-resolved per call) so the caller controls whether
    ``require_xdvdfs()`` runs — repeated resolves are now cheap thanks
    to ``get_xdvdfs()``'s memoisation, but exposing the path keeps the
    argument shape honest about the dependency.
    """
    cmd = [str(xdvdfs), *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  xdvdfs error: {result.stderr.strip()}")
        sys.exit(1)


def _copy_out_bytes(iso_path: Path, in_iso_path: str, *,
                    expected_magic: bytes | None = None,
                    prefix: str = "azurik_extract_") -> bytearray:
    """Extract a single file from ``iso_path`` into memory.

    One helper backs both :func:`extract_config_from_iso` and
    :func:`extract_xbe_from_iso`: spawn ``xdvdfs copy-out`` into a
    temporary directory, read the bytes, validate the magic (when
    requested), and clean up.

    ``in_iso_path`` uses POSIX separators — Azurik's filesystem layout
    is flat enough that xdvdfs doesn't accept Windows-style
    backslashes here even on Windows hosts.
    """
    xdvdfs = str(require_xdvdfs())
    out_name = Path(in_iso_path).name
    with tempfile.TemporaryDirectory(prefix=prefix) as tmpdir:
        out_file = Path(tmpdir) / out_name
        run_xdvdfs(xdvdfs, ["copy-out", str(iso_path),
                            in_iso_path, str(out_file)])
        if not out_file.exists():
            print(f"ERROR: Could not extract {in_iso_path} from {iso_path}")
            sys.exit(1)
        data = bytearray(out_file.read_bytes())

    if expected_magic is not None and data[:len(expected_magic)] != expected_magic:
        print(f"ERROR: Extracted {out_name} has bad magic: "
              f"{bytes(data[:len(expected_magic)])!r} "
              f"(expected {expected_magic!r})")
        sys.exit(1)
    return data


def extract_iso_to_dir(iso_path: Path, dest: Path, *,
                       verify: bool = True) -> None:
    """Unpack an Xbox ISO into ``dest`` via ``xdvdfs unpack``.

    Parameters
    ----------
    iso_path
        Source .iso file.
    dest
        Destination directory (created if needed by xdvdfs).
    verify
        When ``True`` (default) and the unpacked tree contains
        both ``filelist.txt`` and ``prefetch-lists.txt`` (Azurik
        ISOs always do), run a quick size-only integrity check
        against the manifest.  Any mismatch is reported loudly
        but does NOT abort — a corrupted extraction often still
        produces something usable for diagnosis, and callers can
        decide how to react.

        Pass ``verify=False`` for non-Azurik ISOs or when the
        caller runs its own verification pass afterwards.
    """
    xdvdfs = str(require_xdvdfs())
    run_xdvdfs(xdvdfs, ["unpack", str(iso_path), str(dest)])
    if verify:
        _verify_extracted_iso(dest)


def verify_extracted_iso(root: Path) -> int:
    """Size-only integrity scan of ``root`` against its own
    filelist.txt.

    Returns the number of issues found (0 == OK).  Prints a
    warning block when issues are detected; does NOT raise so
    callers can decide how to react.  See docs/LEARNINGS.md
    § filelist.txt for semantics.

    The check is deliberately size-only (not MD5) because the
    extraction pipeline is the hot path — MD5 adds ~1.5 s per GB
    and the common failure mode (truncated xdvdfs output) shows
    up as size mismatches anyway.  MD5 auditing remains available
    via the ``azurik-mod iso-verify`` subcommand.

    Silently skips verification if ``filelist.txt`` isn't present
    (non-Azurik ISO) so the helper is safe to wire into generic
    unpack pipelines.
    """
    fl_path = root / "filelist.txt"
    if not fl_path.exists():
        return 0
    try:
        from azurik_mod.assets.filelist import load_filelist
        manifest = load_filelist(fl_path)
    except Exception as exc:
        print(f"  warning: could not load filelist.txt ({exc}); "
              f"skipping integrity check")
        return 0

    issues = manifest.verify(root, check_md5=False, limit=20)
    if not issues:
        return 0
    print()
    print(f"  WARNING: {len(issues)} integrity issue(s) detected "
          f"in extracted ISO at {root}:")
    for issue in issues:
        print(f"    - {issue}")
    print(f"  Use 'azurik-mod iso-verify {root}' for a full report "
          f"(including MD5 checks).")
    print()
    return len(issues)


# Backwards-compat alias for the internal helper name.
_verify_extracted_iso = verify_extracted_iso


def repack_dir_to_iso(src: Path, iso_path: Path) -> None:
    """Pack a folder produced by :func:`extract_iso_to_dir` back into
    an ISO."""
    xdvdfs = str(require_xdvdfs())
    iso_path.parent.mkdir(parents=True, exist_ok=True)
    run_xdvdfs(xdvdfs, ["pack", str(src), str(iso_path)])


def extract_config_from_iso(iso_path: Path) -> bytearray:
    """Extract ``config.xbr`` from an ISO into memory.

    xdvdfs requires POSIX separators for Xbox filesystem paths, so we
    always pass ``CONFIG_XBR_REL.as_posix()`` as the in-image path.

    Cached by ``(abspath, mtime_ns, size)``.  The Entity Editor calls
    ``run_config_dump`` once per entity per visible section on every
    tab open, which previously re-ran ``xdvdfs copy-out`` ~200 times.
    The cache collapses that back to a single extract per ISO per
    session (and re-extracts only when the ISO changes on disk).
    Each cached buffer is ~4 MB so we bound the cache at
    :data:`_CONFIG_CACHE_MAX` entries.
    """
    key = _cache_key_for(iso_path)
    if key is not None and key in _config_cache:
        return _config_cache[key]

    data = _copy_out_bytes(
        iso_path,
        CONFIG_XBR_REL.as_posix(),
        expected_magic=b"xobx",
        prefix="azurik_read_",
    )

    if key is not None:
        while len(_config_cache) >= _CONFIG_CACHE_MAX:
            _config_cache.pop(next(iter(_config_cache)))
        _config_cache[key] = data
    return data


_config_cache: dict[tuple[str, int, int], bytearray] = {}
_CONFIG_CACHE_MAX = 4


# Back-compat alias (matches the name used by the iso/__init__.py re-export).
extract_config_xbr = extract_config_from_iso


def read_config_data(args) -> bytearray:
    """Read config.xbr from either ``--iso`` or ``--input`` (raw
    ``.xbr`` file)."""
    if hasattr(args, "iso") and args.iso:
        iso_path = Path(args.iso)
        if not iso_path.exists():
            print(f"ERROR: ISO not found: {iso_path}")
            sys.exit(1)
        print(f"  Extracting config.xbr from {iso_path}...")
        return extract_config_from_iso(iso_path)
    if hasattr(args, "input") and args.input:
        p = Path(args.input)
        if not p.exists():
            print(f"ERROR: File not found: {p}")
            sys.exit(1)
        data = bytearray(p.read_bytes())
        if data[:4] != b"xobx":
            print(f"ERROR: {p} is not a valid XBR file")
            sys.exit(1)
        return data
    print("ERROR: Specify --iso (game ISO) or --input (raw config.xbr)")
    sys.exit(1)


# Cache keyed by (absolute ISO path, mtime_ns, size).  ``verify-patches
# --original`` extracts both a patched ISO and the vanilla original
# in one command, so caching by identity avoids a second
# ``xdvdfs copy-out`` for the identical file.  Cache invalidates
# automatically if the ISO is modified on disk.
#
# ``os.stat`` + ``os.path.abspath`` is ~20x faster than
# ``Path.resolve()`` because it skips the per-component symlink
# walk + existence check ``resolve()`` performs by default; that
# matters when every Entity Editor refresh re-keys the cache.
#
# Memory cost: one ~4 MB bytearray per cached ISO — trivial; we cap
# at 4 entries just to bound worst-case growth across long-running
# sessions (verify-patches is the only real consumer; it reads at
# most 2 ISOs per run).
_xbe_cache: dict[tuple[str, int, int], bytearray] = {}
_XBE_CACHE_MAX = 4


def _cache_key_for(path: Path) -> tuple[str, int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (os.path.abspath(str(path)), st.st_mtime_ns, st.st_size)


def extract_xbe_from_iso(iso_path: Path) -> bytearray:
    """Pull ``default.xbe`` out of an Xbox ISO via ``xdvdfs copy-out``.

    Cached by ``(abspath, mtime_ns, size)`` so a second call with the
    same unchanged ISO reuses the first call's bytearray (a ~4 MB
    copy).  Callers mutating the returned buffer in place will
    poison the cache — ``bytearray(result)`` produces an independent
    copy.  Current consumers (``cmd_verify_patches``,
    ``cmd_randomize_full``) are read-only so this is safe today.
    """
    key = _cache_key_for(iso_path)
    if key is not None and key in _xbe_cache:
        return _xbe_cache[key]

    data = _copy_out_bytes(
        iso_path,
        "default.xbe",
        expected_magic=None,
        prefix="azurik_verify_",
    )

    if key is not None:
        # Bounded eviction — drop oldest entry if cache is full.
        while len(_xbe_cache) >= _XBE_CACHE_MAX:
            _xbe_cache.pop(next(iter(_xbe_cache)))
        _xbe_cache[key] = data
    return data


def read_xbe_bytes(iso_or_xbe: Path) -> bytearray:
    """Return ``default.xbe`` bytes from either an ``.iso`` or a raw
    ``.xbe`` path."""
    if iso_or_xbe.suffix.lower() == ".iso":
        return extract_xbe_from_iso(iso_or_xbe)
    return bytearray(iso_or_xbe.read_bytes())


__all__ = [
    "CONFIG_XBR_REL",
    "GAMEDATA_REL",
    "extract_config_from_iso",
    "extract_config_xbr",
    "extract_iso_to_dir",
    "extract_xbe_from_iso",
    "read_config_data",
    "read_xbe_bytes",
    "repack_dir_to_iso",
    "run_xdvdfs",
    "verify_extracted_iso",
]
