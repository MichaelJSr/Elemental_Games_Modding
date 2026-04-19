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
import os
import queue
import shutil
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

    def _emit(self, line: str) -> None:
        """Fan one line out to buffer + log + queue + optional hook.

        Factored out of :meth:`write` / :meth:`flush` so the two
        paths can't drift (buffer always matches queue order) and
        so the per-line try/except on the log write sits in exactly
        one place.
        """
        self._buffer.append(line)
        log = self._log_file
        if log is not None:
            try:
                log.write(line)
            except Exception:  # noqa: BLE001
                pass
        self._queue.put(("output", line))
        if self._on_output is not None:
            self._on_output(line)

    def write(self, text: str) -> int:  # type: ignore[override]
        if not text:
            return 0
        self._pending += text
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            self._emit(line + "\n")
        return len(text)

    def flush(self) -> None:  # type: ignore[override]
        if self._pending:
            self._emit(self._pending)
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
    packs: dict[str, bool] | None = None,
    pack_params: dict[str, dict[str, float]] | None = None,
    item_pool: dict[str, int] | None = None,
    obsidian_cost: int | None = None,
    config_edits: dict | None = None,
    force_unsolvable: bool = False,
    on_output: Callable[[str], None] | None = None,
    on_done: Callable[[BuildResult], None] | None = None,
    # --- Back-compat keyword kwargs (pre-reorganisation) -------------
    # Old call sites passed each pack as its own boolean kwarg.  Any
    # True value here is folded into ``packs`` before dispatch so
    # downstream scripts don't break mid-migration.
    gem_popups: bool = False,
    other_popups: bool = False,
    pickup_anims: bool = False,
    skip_logo: bool = False,
    fps_unlock: bool = False,
) -> tuple[threading.Thread, "queue.Queue[tuple[str, object]]"]:
    """Run the full randomizer in a background thread using the in-process
    library.

    ``packs`` is a ``{pack_name: enabled}`` dict driven by the Patches
    page; ``pack_params`` carries slider values keyed by pack and
    parameter name.  The legacy per-pack boolean kwargs are still
    accepted for one release so downstream scripts have time to
    migrate.

    Returns (thread, msg_queue). The caller should poll `msg_queue` from
    the main/GUI thread via `after()` for ("output", line) and
    ("done", BuildResult) messages.
    """
    # Merge legacy kwargs into the unified packs dict so the rest of
    # the function only deals with one input shape.
    packs = dict(packs or {})
    for old_name, canonical in (
        ("gem_popups", "qol_gem_popups"),
        ("other_popups", "qol_other_popups"),
        ("pickup_anims", "qol_pickup_anims"),
        ("skip_logo", "qol_skip_logo"),
        ("fps_unlock", "fps_unlock"),
    ):
        if locals()[old_name]:
            packs[canonical] = True
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
            enabled = sorted(n for n, v in packs.items() if v)
            log_file.write(
                f"# Azurik Mod Tools build log\n"
                f"# started : {datetime.datetime.now().isoformat(timespec='seconds')}\n"
                f"# seed    : {seed}\n"
                f"# iso     : {iso_path}\n"
                f"# output  : {output_path}\n"
                f"# packs   : {', '.join(enabled) or '(none)'}\n"
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
        # physics pack exposes `gravity`, `walk_speed_scale`,
        # `roll_speed_scale`, and `swim_speed_scale`; we only forward
        # non-default values so the CLI keeps `--gravity` etc. unset
        # when the slider is at its default.  Back-compat: still
        # accepts the old `run_speed_scale` key as an alias for
        # `roll_speed_scale` so pre-April-2026 serialized param dicts
        # from older GUI sessions keep working.
        physics = (pack_params or {}).get("player_physics", {})
        gravity = physics.get("gravity")
        walk_scale = physics.get("walk_speed_scale")
        roll_scale = (physics.get("roll_speed_scale")
                      if physics.get("roll_speed_scale") is not None
                      else physics.get("run_speed_scale"))
        swim_scale = physics.get("swim_speed_scale")
        # Only forward when clearly non-default to preserve byte-identity.
        if gravity is not None and abs(gravity - 9.8) < 1e-6:
            gravity = None
        if walk_scale is not None and abs(walk_scale - 1.0) < 1e-6:
            walk_scale = None
        if roll_scale is not None and abs(roll_scale - 1.0) < 1e-6:
            roll_scale = None
        if swim_scale is not None and abs(swim_scale - 1.0) < 1e-6:
            swim_scale = None

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
            # Pack toggles derived from the unified packs dict.  One
            # field per pack that cmd_randomize_full's CLI surface
            # still understands; new packs just need an entry here.
            gem_popups=packs.get("qol_gem_popups", False),
            other_popups=packs.get("qol_other_popups", False),
            pickup_anims=packs.get("qol_pickup_anims", False),
            skip_logo=packs.get("qol_skip_logo", False),
            fps_unlock=packs.get("fps_unlock", False),
            # Legacy grouped flags stay False so they're no-ops.
            no_qol=False,
            no_gem_popups=False,
            no_other_popups=False,
            no_pickup_anim=False,
            no_skip_logo=False,
            obsidian_cost=obsidian_cost,
            item_pool=json.dumps(item_pool) if item_pool else None,
            force=force_unsolvable,
            player_character=None,
            config_mod=json.dumps(config_edits) if config_edits else None,
            # Parametric slider values for player_physics.  Use the
            # new (roll) names; cmd_randomize_full still accepts
            # legacy `player_run_scale` for pinned external callers
            # but the GUI has no reason to emit both.
            gravity=gravity,
            player_walk_scale=walk_scale,
            player_roll_scale=roll_scale,
            player_swim_scale=swim_scale,
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


# Registry JSON is ~876 KB and gets queried by the Config Editor on
# every section-dropdown change (``list_entities``) + once at page
# build (``list_sections``).  Re-parsing 876 KB for every flick of
# the dropdown was a 20-30 ms stall per click.  Memoise by
# ``(path, mtime_ns, size)`` so the cache drops transparently if the
# registry file is regenerated on disk.
_registry_cache: tuple[tuple[str, int, int], dict] | None = None


def _load_registry() -> dict:
    """Return the bundled registry JSON as a dict, cached across calls.

    Returns an empty dict if the file is missing or unreadable — the
    list-returning wrappers below then yield the empty list.
    """
    global _registry_cache
    from azurik_mod.config import REGISTRY_PATH

    if not REGISTRY_PATH.exists():
        return {}
    try:
        st = os.stat(REGISTRY_PATH)
    except OSError:
        return {}
    key = (str(REGISTRY_PATH), st.st_mtime_ns, st.st_size)
    if _registry_cache is not None and _registry_cache[0] == key:
        return _registry_cache[1]
    try:
        with open(REGISTRY_PATH) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    _registry_cache = (key, data)
    return data


def list_sections() -> list[str]:
    """List config sections from the registry."""
    data = _load_registry()
    return sorted(data.get("sections", {}).keys())


def list_entities(section: str) -> list[str]:
    """List entities in a config section from the registry."""
    data = _load_registry()
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

# Cache key: ISO abspath + mtime_ns + size.  If the user reloads the
# SAME ISO (common: Entity Editor tab repeatedly opened during one
# session) we hand back the already-extracted ``config.xbr`` instead
# of spawning another ``xdvdfs copy-out`` + ``tempfile.mkdtemp`` pair
# that would slowly pile up gigabytes of unpacked data over a long
# session.
#
# Cache invalidates automatically if the ISO changes on disk (mtime
# / size delta) — then the cached copy is stale.
#
# ``os.stat`` is ~20x faster than ``Path.resolve()`` because it
# skips the per-component symlink + existence walk.  Matters here
# because Entity Editor reopens extract_config_xbr on every tab
# focus to rebuild its row cache.
_cached_config: tuple[tuple[str, int, int], Path] | None = None


def extract_config_xbr(iso_path: Path) -> Path | None:
    """Extract ``config.xbr`` from an ISO to a temp file and return
    its path.

    Cached per-ISO: a second call with the same unchanged ISO reuses
    the first call's temp file.  Changing ISOs (or letting the
    original be rewritten on disk) transparently invalidates the
    cache and runs a fresh extract.

    Returns ``None`` if xdvdfs isn't available OR if the extract
    fails for any reason — callers already tolerate ``None``.
    """
    global _cached_config

    if not find_xdvdfs():
        return None

    try:
        st = os.stat(iso_path)
        key = (os.path.abspath(str(iso_path)), st.st_mtime_ns, st.st_size)
    except OSError:
        key = None

    if key is not None and _cached_config is not None:
        cached_key, cached_path = _cached_config
        if cached_key == key and cached_path.exists():
            return cached_path

    # Stale cache entry (different ISO, or same ISO modified on disk)
    # — release its temp dir.
    if _cached_config is not None:
        _, old_path = _cached_config
        old_dir = str(old_path.parent)
        shutil.rmtree(old_dir, ignore_errors=True)
        if old_dir in _temp_dirs:
            _temp_dirs.remove(old_dir)
        _cached_config = None

    # Delegate to the shared extract-to-memory helper, then spill the
    # bytes into a tempfile on disk.  Using one code path for both
    # CLI + GUI consumers means bug fixes (magic-byte validation,
    # xdvdfs error surfacing) automatically reach both.
    try:
        from azurik_mod.iso.pack import extract_config_from_iso
    except ImportError:
        return None
    try:
        data = extract_config_from_iso(iso_path)
    except SystemExit:
        # extract_config_from_iso calls sys.exit on fatal errors;
        # the GUI should survive that instead of terminating.
        return None
    except Exception:  # noqa: BLE001
        return None

    tmpdir = tempfile.mkdtemp(prefix="azurik_cfg_")
    _temp_dirs.append(tmpdir)
    out_file = Path(tmpdir) / "config.xbr"
    try:
        out_file.write_bytes(data)
    except OSError:
        shutil.rmtree(tmpdir, ignore_errors=True)
        if tmpdir in _temp_dirs:
            _temp_dirs.remove(tmpdir)
        return None
    if key is not None:
        _cached_config = (key, out_file)
    return out_file


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
    global _cached_config
    for d in _temp_dirs:
        shutil.rmtree(d, ignore_errors=True)
    _temp_dirs.clear()
    _cached_config = None
