"""GUI backend — wraps the `azurik_mod` library for the Tk UI.

Historically this module shelled out to `azurik_mod.py` via `subprocess`.
It now calls the library in-process on a worker thread and streams
captured stdout through a `queue.Queue` so the Tk main thread never
blocks.  Consumers poll the queue via `after()`.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import io
import json
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from platformdirs import user_log_dir

# Import `azurik_mod.patches` so every pack's `register_pack(...)` side
# effect runs once, regardless of which GUI path the user takes next.
import azurik_mod.patches  # noqa: F401,E402

# Repo root — one level above `gui/`.  The ISO picker's default folder
# lives here.
REPO_ROOT = Path(__file__).resolve().parent.parent
ISO_DIR = REPO_ROOT / "iso"

# Build logs live in the standard platform log directory so they survive
# across runs and are easy to share when reporting a bug.
#   macOS:   ~/Library/Logs/azurik_mod/
#   Linux:   ~/.local/state/azurik_mod/log/
#   Windows: %LOCALAPPDATA%\azurik_mod\Logs\
LOG_DIR = Path(user_log_dir("azurik_mod", appauthor=False))


def get_log_dir() -> Path:
    """Return the directory where per-build log files are written."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR


def latest_log_file() -> Path | None:
    """Return the most recently modified log file, or None if none exist."""
    if not LOG_DIR.exists():
        return None
    logs = sorted(LOG_DIR.glob("build-*.log"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None

# Back-compat alias that older tab code imports.
SCRIPT_DIR = REPO_ROOT


def find_base_iso() -> Path | None:
    """Look for the base game ISO in the iso/ folder."""
    if not ISO_DIR.exists():
        ISO_DIR.mkdir(exist_ok=True)
        return None
    for f in ISO_DIR.iterdir():
        if f.suffix.lower() == ".iso" and f.is_file():
            return f
    return None


def find_xdvdfs() -> str | None:
    """Locate xdvdfs executable (PATH / cache / auto-download)."""
    from azurik_mod.iso.xdvdfs import get_xdvdfs

    found = get_xdvdfs()
    return str(found) if found else None


def check_prerequisites() -> list[str]:
    """Check that required files/tools exist. Returns list of issues."""
    from azurik_mod.config import REGISTRY_PATH

    issues: list[str] = []
    if not find_xdvdfs():
        issues.append("xdvdfs not found (install or place in tools/)")
    if not REGISTRY_PATH.exists():
        issues.append(f"config registry not found at {REGISTRY_PATH}")
    return issues


@dataclass
class BuildResult:
    success: bool
    output: str
    output_path: Path | None = None
    seed: int | None = None
    log_file: Path | None = None


class _QueueWriter(io.TextIOBase):
    """Line-buffered text writer that fan-outs to a queue, a buffer, and
    optionally a log file on disk.

    - Every complete line is pushed as ("output", line) on the queue so
      the GUI's BuildPage picks it up on the main thread via after().
    - The buffer list accumulates the full text for `BuildResult.output`.
    - The log_file handle (if provided) captures the same stream on disk
      so the user has a persistent log they can open / attach to an
      issue even after closing the app.
    """

    def __init__(self, msg_queue: "queue.Queue[tuple[str, object]]",
                 on_output: Callable[[str], None] | None,
                 buffer: list[str],
                 log_file: "io.TextIOBase | None" = None) -> None:
        super().__init__()
        self._queue = msg_queue
        self._on_output = on_output
        self._buffer = buffer
        self._log_file = log_file
        self._pending = ""

    def writable(self) -> bool:  # type: ignore[override]
        return True

    def write(self, text: str) -> int:  # type: ignore[override]
        if not text:
            return 0
        self._pending += text
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            line = line + "\n"
            self._buffer.append(line)
            if self._log_file is not None:
                try:
                    self._log_file.write(line)
                except Exception:  # noqa: BLE001
                    pass
            self._queue.put(("output", line))
            if self._on_output is not None:
                self._on_output(line)
        return len(text)

    def flush(self) -> None:  # type: ignore[override]
        if self._pending:
            self._buffer.append(self._pending)
            if self._log_file is not None:
                try:
                    self._log_file.write(self._pending)
                except Exception:  # noqa: BLE001
                    pass
            self._queue.put(("output", self._pending))
            if self._on_output is not None:
                self._on_output(self._pending)
            self._pending = ""
        if self._log_file is not None:
            try:
                self._log_file.flush()
            except Exception:  # noqa: BLE001
                pass


def run_randomizer(
    iso_path: Path,
    output_path: Path,
    seed: int = 42,
    do_major: bool = False,
    do_keys: bool = False,
    do_gems: bool = False,
    do_barriers: bool = False,
    do_connections: bool = False,
    gem_popups: bool = False,
    pickup_anims: bool = False,
    fps_unlock: bool = False,
    item_pool: dict[str, int] | None = None,
    obsidian_cost: int | None = None,
    config_edits: dict | None = None,
    force_unsolvable: bool = False,
    pack_params: dict[str, dict[str, float]] | None = None,
    on_output: Callable[[str], None] | None = None,
    on_done: Callable[[BuildResult], None] | None = None,
) -> tuple[threading.Thread, "queue.Queue[tuple[str, object]]"]:
    """Run the full randomizer in a background thread using the in-process
    library.

    Returns (thread, msg_queue). The caller should poll `msg_queue` from
    the main/GUI thread via `after()` for ("output", line) and
    ("done", BuildResult) messages.
    """
    msg_queue: queue.Queue[tuple[str, object]] = queue.Queue()

    def _run() -> None:
        buffer: list[str] = []

        # Open a timestamped log file alongside previous runs.  If the
        # directory is unwritable for some reason, log-to-file is
        # skipped silently and everything still flows through the
        # queue / buffer as before.
        log_path: Path | None = None
        log_file: "io.TextIOBase | None" = None
        try:
            get_log_dir()  # ensure exists
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            log_path = LOG_DIR / f"build-{ts}-seed{seed}.log"
            log_file = open(log_path, "w", encoding="utf-8", buffering=1)
            log_file.write(
                f"# Azurik Mod Tools build log\n"
                f"# started : {datetime.datetime.now().isoformat(timespec='seconds')}\n"
                f"# seed    : {seed}\n"
                f"# iso     : {iso_path}\n"
                f"# output  : {output_path}\n"
                f"# patches : fps={fps_unlock}  gem_popups={gem_popups}  "
                f"pickup_anims={pickup_anims}\n"
                f"# pools   : major={do_major} keys={do_keys} gems={do_gems} "
                f"barriers={do_barriers} connections={do_connections}\n"
                f"# pack_params: {pack_params}\n"
                f"# ---\n"
            )
        except Exception:  # noqa: BLE001
            log_path = None
            log_file = None

        writer = _QueueWriter(msg_queue, on_output, buffer, log_file)

        # Build an argparse-style Namespace the legacy cmd_randomize_full
        # already knows how to consume.  Any field missing from the
        # namespace becomes `None` via a default factory since the code
        # uses getattr(args, 'x', default).
        # Unpack parametric slider values from pack_params.  The player
        # physics pack exposes `gravity`, `walk_speed_scale`, and
        # `run_speed_scale`; we only forward non-default values so the
        # CLI keeps `--gravity` etc. unset when the slider is at default.
        physics = (pack_params or {}).get("player_physics", {})
        gravity = physics.get("gravity")
        walk_scale = physics.get("walk_speed_scale")
        run_scale = physics.get("run_speed_scale")
        # Only forward when clearly non-default to preserve byte-identity.
        if gravity is not None and abs(gravity - 9.8) < 1e-6:
            gravity = None
        if walk_scale is not None and abs(walk_scale - 1.0) < 1e-6:
            walk_scale = None
        if run_scale is not None and abs(run_scale - 1.0) < 1e-6:
            run_scale = None

        args = argparse.Namespace(
            command="randomize-full",
            iso=str(iso_path),
            output=str(output_path),
            seed=seed,
            no_major=not do_major,
            no_keys=not do_keys,
            no_gems=not do_gems,
            no_barriers=not do_barriers,
            no_connections=not do_connections,
            hard_barriers=False,
            # New opt-in QoL flags (one per sub-patch).
            gem_popups=gem_popups,
            pickup_anims=pickup_anims,
            # Legacy grouped flags stay False so they're no-ops.
            no_qol=False,
            no_gem_popups=False,
            no_pickup_anim=False,
            obsidian_cost=obsidian_cost,
            item_pool=json.dumps(item_pool) if item_pool else None,
            force=force_unsolvable,
            player_character=None,
            fps_unlock=fps_unlock,
            config_mod=json.dumps(config_edits) if config_edits else None,
            gravity=gravity,
            player_walk_scale=walk_scale,
            player_run_scale=run_scale,
        )

        result: BuildResult
        try:
            with contextlib.redirect_stdout(writer), \
                 contextlib.redirect_stderr(writer):
                from azurik_mod.randomizer.commands import cmd_randomize_full

                try:
                    cmd_randomize_full(args)
                    success = True
                except SystemExit as exc:
                    # cmd_randomize_full calls sys.exit on fatal errors.
                    success = (exc.code == 0)
                except Exception:  # noqa: BLE001
                    # Unexpected exception — dump full traceback into the
                    # log stream so the user (and we) can see the line
                    # numbers instead of a bare one-line error message.
                    traceback.print_exc()
                    success = False
                writer.flush()

            result = BuildResult(
                success=success,
                output="".join(buffer),
                output_path=output_path if success else None,
                seed=seed,
                log_file=log_path,
            )
        except Exception as exc:  # noqa: BLE001
            # Catch-all for failures OUTSIDE the redirect_stdout block
            # (very rare — io setup, etc.).
            tb = traceback.format_exc()
            writer.flush()
            if log_file is not None:
                try:
                    log_file.write(tb)
                except Exception:  # noqa: BLE001
                    pass
            result = BuildResult(
                success=False,
                output="".join(buffer) + f"\n{tb}",
                seed=seed,
                log_file=log_path,
            )
        finally:
            if log_file is not None:
                try:
                    log_file.write(
                        f"\n# ---\n# finished: "
                        f"{datetime.datetime.now().isoformat(timespec='seconds')}\n")
                    log_file.close()
                except Exception:  # noqa: BLE001
                    pass

        msg_queue.put(("done", result))
        if on_done is not None:
            on_done(result)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread, msg_queue


def run_config_dump(iso_path: Path, section: str, entity: str | None = None) -> str:
    """Run `azurik-mod dump` in-process and return captured output."""
    from azurik_mod.randomizer.commands import cmd_dump

    args = argparse.Namespace(
        command="dump",
        iso=str(iso_path),
        input=None,
        section=section,
        entity=entity,
    )
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                cmd_dump(args)
            except SystemExit:
                pass
    except Exception as exc:  # noqa: BLE001
        buf.write(f"\nError: {exc}\n")
    return buf.getvalue()


def list_sections() -> list[str]:
    """List config sections from the registry."""
    from azurik_mod.config import REGISTRY_PATH

    if not REGISTRY_PATH.exists():
        return []
    with open(REGISTRY_PATH) as f:
        data = json.load(f)
    return sorted(data.get("sections", {}).keys())


def list_entities(section: str) -> list[str]:
    """List entities in a config section from the registry."""
    from azurik_mod.config import REGISTRY_PATH

    if not REGISTRY_PATH.exists():
        return []
    with open(REGISTRY_PATH) as f:
        data = json.load(f)
    sec = data.get("sections", {}).get(section, {})
    return sorted(sec.get("entities", {}).keys())


def load_keyed_tables(config_path: Path) -> dict | None:
    """Load all keyed tables from a config.xbr file."""
    try:
        from azurik_mod.config import keyed_tables
        return keyed_tables.load_all_tables(str(config_path))
    except Exception:  # noqa: BLE001
        return None


_temp_dirs: list[str] = []


def extract_config_xbr(iso_path: Path) -> Path | None:
    """Extract config.xbr from ISO to a temp file and return its path."""
    xdvdfs = find_xdvdfs()
    if not xdvdfs:
        return None
    tmpdir = tempfile.mkdtemp(prefix="azurik_cfg_")
    _temp_dirs.append(tmpdir)
    out_file = Path(tmpdir) / "config.xbr"
    try:
        result = subprocess.run(
            [xdvdfs, "copy-out", str(iso_path),
             "gamedata/config.xbr", str(out_file)],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and out_file.exists():
            return out_file
    except Exception:  # noqa: BLE001
        pass
    return None


def load_all_pickups() -> dict | None:
    """Load the all_pickups.json catalog if present.

    This catalog is optional — older builds shipped without it.  The
    callers tolerate `None` and fall back to a manual entity list.
    """
    path = REPO_ROOT / "azurik_mod" / "config" / "all_pickups.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def cleanup_temp_dirs() -> None:
    """Remove temp directories created by extract_config_xbr."""
    for d in _temp_dirs:
        shutil.rmtree(d, ignore_errors=True)
    _temp_dirs.clear()
