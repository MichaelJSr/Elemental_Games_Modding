"""Locate (or auto-download) the xdvdfs binary.

Resolution order:

  1. `$AZURIK_XDVDFS` env var — absolute path to a xdvdfs binary.
  2. `shutil.which("xdvdfs")` on PATH (covers cargo-install on macOS).
  3. Cached binary in `platformdirs.user_cache_dir("azurik_mod")/xdvdfs[.exe]`.
  4. Download the latest release from GitHub, unzip into the cache, and
     return the extracted binary path.

Upstream ships prebuilt binaries only for Linux and Windows
(https://github.com/antangelo/xdvdfs/releases).  On macOS the user must
install xdvdfs manually (`cargo install xdvdfs-cli`) — we surface a
clear error message in that case.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

from platformdirs import user_cache_dir


RELEASES_API = "https://api.github.com/repos/antangelo/xdvdfs/releases/latest"


def _cache_root() -> Path:
    root = Path(user_cache_dir("azurik_mod", appauthor=False))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _binary_name() -> str:
    return "xdvdfs.exe" if os.name == "nt" else "xdvdfs"


def _platform_asset_tag() -> str | None:
    """Return 'windows' / 'linux', or None if the host has no prebuilt."""
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "linux":
        return "linux"
    # macOS (Darwin) and anything else: no prebuilt binary.
    return None


def _download_latest(cache_root: Path) -> Path | None:
    """Fetch the latest xdvdfs release for the current platform.

    Returns the Path to the extracted binary on success, or None on any
    failure (no prebuilt for this OS, network error, zip without a
    recognisable binary, etc.).
    """
    tag = _platform_asset_tag()
    if tag is None:
        return None

    try:
        with urllib.request.urlopen(RELEASES_API, timeout=15) as resp:
            release = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        print(f"  WARNING: could not fetch xdvdfs release metadata: {exc}",
              file=sys.stderr)
        return None

    asset_url = None
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if name.startswith(f"xdvdfs-{tag}-") and name.endswith(".zip"):
            asset_url = asset.get("browser_download_url")
            break
    if not asset_url:
        print(f"  WARNING: no xdvdfs asset found for platform '{tag}'",
              file=sys.stderr)
        return None

    zip_path = cache_root / f"xdvdfs-{tag}.zip"
    print(f"  Downloading xdvdfs from {asset_url}...")
    try:
        urllib.request.urlretrieve(asset_url, zip_path)
    except Exception as exc:  # noqa: BLE001
        print(f"  WARNING: xdvdfs download failed: {exc}", file=sys.stderr)
        return None

    binary_name = _binary_name()
    extracted: Path | None = None
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                base = Path(member).name
                if base == binary_name:
                    zf.extract(member, cache_root)
                    extracted = cache_root / member
                    break
    except zipfile.BadZipFile:
        print(f"  WARNING: downloaded xdvdfs zip is malformed",
              file=sys.stderr)
        return None
    finally:
        try:
            zip_path.unlink()
        except OSError:
            pass

    if extracted is None:
        print(f"  WARNING: zip had no '{binary_name}' entry", file=sys.stderr)
        return None

    # Move to a stable location at cache_root/<binary_name> so future
    # lookups find it even across release tags.
    final = cache_root / binary_name
    extracted.replace(final)
    # Clean up the now-empty extraction subtree.
    try:
        parent = extracted.parent
        while parent != cache_root and parent.exists():
            parent.rmdir()
            parent = parent.parent
    except OSError:
        pass

    # chmod +x on POSIX.
    if os.name != "nt":
        final.chmod(final.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print(f"  xdvdfs cached at {final}")
    return final


def get_xdvdfs() -> Path | None:
    """Resolve a usable xdvdfs binary, downloading if necessary.

    Returns None if no binary can be found or fetched (on macOS, the
    user must `cargo install xdvdfs-cli` or set $AZURIK_XDVDFS).
    """
    env_override = os.environ.get("AZURIK_XDVDFS")
    if env_override:
        p = Path(env_override)
        if p.exists():
            return p
        print(f"  WARNING: $AZURIK_XDVDFS points to missing file: {p}",
              file=sys.stderr)

    found = shutil.which("xdvdfs")
    if found:
        return Path(found)

    cache = _cache_root()
    cached = cache / _binary_name()
    if cached.exists():
        return cached

    return _download_latest(cache)


def require_xdvdfs() -> Path:
    """Like `get_xdvdfs()` but aborts with a helpful message if missing."""
    found = get_xdvdfs()
    if found:
        return found

    system = platform.system()
    print("ERROR: xdvdfs not found and no prebuilt binary is available "
          f"for your platform ({system}).")
    print("  Install with: cargo install xdvdfs-cli")
    print("  Or download manually from: "
          "https://github.com/antangelo/xdvdfs/releases")
    print("  Or set $AZURIK_XDVDFS to an absolute path.")
    sys.exit(1)


def run_xdvdfs(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run xdvdfs with `args` and print any stderr output."""
    xdvdfs = require_xdvdfs()
    cmd = [str(xdvdfs), *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  xdvdfs error: {result.stderr.strip()}")
    return result


__all__ = ["RELEASES_API", "get_xdvdfs", "require_xdvdfs", "run_xdvdfs"]
