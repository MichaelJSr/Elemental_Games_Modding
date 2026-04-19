"""ISO extract / repack helpers.

Thin layer over `azurik_mod.iso.xdvdfs` that covers the two everyday
use cases:

  - Unpack / repack a full ISO for the randomizer pipeline.
  - Extract a specific file (config.xbr, default.xbe) into memory
    without leaving temp directories lying around.
"""

from __future__ import annotations

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
    """Run xdvdfs with the given positional args; exit on non-zero."""
    cmd = [str(xdvdfs), *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  xdvdfs error: {result.stderr.strip()}")
        sys.exit(1)


def extract_iso_to_dir(iso_path: Path, dest: Path) -> None:
    """Unpack an Xbox ISO into `dest` via `xdvdfs unpack`."""
    xdvdfs = require_xdvdfs()
    run_xdvdfs(str(xdvdfs), ["unpack", str(iso_path), str(dest)])


def repack_dir_to_iso(src: Path, iso_path: Path) -> None:
    """Pack a folder produced by `extract_iso_to_dir` back into an ISO."""
    xdvdfs = require_xdvdfs()
    iso_path.parent.mkdir(parents=True, exist_ok=True)
    run_xdvdfs(str(xdvdfs), ["pack", str(src), str(iso_path)])


def extract_config_from_iso(iso_path: Path) -> bytearray:
    """Extract config.xbr from an ISO into memory via a temp dir.

    xdvdfs requires POSIX separators for Xbox filesystem paths, so we
    always pass CONFIG_XBR_REL.as_posix() as the in-image path.
    """
    xdvdfs = require_xdvdfs()
    with tempfile.TemporaryDirectory(prefix="azurik_read_") as tmpdir:
        tmp = Path(tmpdir)
        out_file = tmp / "config.xbr"
        run_xdvdfs(str(xdvdfs), ["copy-out", str(iso_path),
                                 CONFIG_XBR_REL.as_posix(), str(out_file)])
        if not out_file.exists():
            print(f"ERROR: Could not extract {CONFIG_XBR_REL} from {iso_path}")
            sys.exit(1)
        data = bytearray(out_file.read_bytes())

    if data[:4] != b"xobx":
        print(f"ERROR: Extracted config.xbr has bad magic: {data[:4]!r}")
        sys.exit(1)
    return data


# Back-compat alias (matches the name used by the iso/__init__.py re-export).
extract_config_xbr = extract_config_from_iso


def read_config_data(args) -> bytearray:
    """Read config.xbr from either --iso or --input (raw .xbr file)."""
    if hasattr(args, "iso") and args.iso:
        iso_path = Path(args.iso)
        if not iso_path.exists():
            print(f"ERROR: ISO not found: {iso_path}")
            sys.exit(1)
        print(f"  Extracting config.xbr from {iso_path}...")
        return extract_config_from_iso(iso_path)
    elif hasattr(args, "input") and args.input:
        p = Path(args.input)
        if not p.exists():
            print(f"ERROR: File not found: {p}")
            sys.exit(1)
        data = bytearray(p.read_bytes())
        if data[:4] != b"xobx":
            print(f"ERROR: {p} is not a valid XBR file")
            sys.exit(1)
        return data
    else:
        print("ERROR: Specify --iso (game ISO) or --input (raw config.xbr)")
        sys.exit(1)


# Cache keyed by (resolved ISO path, mtime, size).  ``verify-patches
# --original`` extracts both a patched ISO and the vanilla original
# in one command, so caching by identity avoids a second
# ``xdvdfs copy-out`` for the identical file.  Cache invalidates
# automatically if the ISO is modified on disk.
#
# Memory cost: one ~4 MB bytearray per cached ISO — trivial; we cap
# at 4 entries just to bound worst-case growth across long-running
# sessions (verify-patches is the only real consumer; it reads at
# most 2 ISOs per run).
_xbe_cache: dict[tuple[str, float, int], bytearray] = {}
_XBE_CACHE_MAX = 4


def _cache_key_for(path: Path) -> tuple[str, float, int] | None:
    try:
        st = path.stat()
        return (str(path.resolve()), st.st_mtime, st.st_size)
    except OSError:
        return None


def extract_xbe_from_iso(iso_path: Path) -> bytearray:
    """Pull default.xbe out of an Xbox ISO via `xdvdfs copy-out`.

    Cached by ``(resolved_path, mtime, size)`` so a second call with
    the same unchanged ISO reuses the first call's bytearray (a
    ~4 MB copy).  Callers mutating the returned buffer in place
    will poison the cache — they should ``bytearray(result)`` first
    if they need an independent copy.  Current consumers
    (``cmd_verify_patches``, ``cmd_randomize_full``) read-only, so
    this is safe today.
    """
    key = _cache_key_for(iso_path)
    if key is not None and key in _xbe_cache:
        return _xbe_cache[key]

    xdvdfs = require_xdvdfs()
    with tempfile.TemporaryDirectory(prefix="azurik_verify_") as tmpdir:
        out_file = Path(tmpdir) / "default.xbe"
        run_xdvdfs(str(xdvdfs), ["copy-out", str(iso_path),
                                 "default.xbe", str(out_file)])
        if not out_file.exists():
            print(f"ERROR: Could not extract default.xbe from {iso_path}")
            sys.exit(1)
        data = bytearray(out_file.read_bytes())

    if key is not None:
        # Bounded eviction — drop oldest entry if cache is full.
        while len(_xbe_cache) >= _XBE_CACHE_MAX:
            _xbe_cache.pop(next(iter(_xbe_cache)))
        _xbe_cache[key] = data
    return data


def read_xbe_bytes(iso_or_xbe: Path) -> bytearray:
    """Return default.xbe bytes from either an .iso or a raw .xbe path."""
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
]
