"""Backend — wraps azurik_mod.py, level_editor.py, and solver.py for the GUI."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SCRIPT_DIR = Path(__file__).resolve().parent.parent
ISO_DIR = SCRIPT_DIR / "iso"


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
    """Locate xdvdfs executable."""
    import shutil
    found = shutil.which("xdvdfs")
    if found:
        return found
    for name in ("xdvdfs", "xdvdfs.exe"):
        local = SCRIPT_DIR / "tools" / name
        if local.exists():
            return str(local)
    return None


def check_prerequisites() -> list[str]:
    """Check that required files/tools exist. Returns list of issues."""
    issues = []
    if not find_xdvdfs():
        issues.append("xdvdfs not found (install or place in tools/)")
    registry = SCRIPT_DIR / "claude_output" / "config_registry.json"
    if not registry.exists():
        issues.append(f"config_registry.json not found at {registry}")
    return issues


@dataclass
class BuildResult:
    success: bool
    output: str
    output_path: Path | None = None
    seed: int | None = None


def run_randomizer(
    iso_path: Path,
    output_path: Path,
    seed: int = 42,
    do_major: bool = True,
    do_keys: bool = True,
    do_gems: bool = True,
    do_barriers: bool = True,
    do_connections: bool = True,
    do_qol: bool = True,
    fps_unlock: bool = False,
    item_pool: dict[str, int] | None = None,
    obsidian_cost: int | None = None,
    config_edits: dict | None = None,
    force_unsolvable: bool = False,
    on_output: Callable[[str], None] | None = None,
    on_done: Callable[[BuildResult], None] | None = None,
) -> threading.Thread:
    """Run the full randomizer in a background thread.

    on_output is called with each line of stdout (from the GUI thread via after()).
    on_done is called with the result when complete.
    Returns the thread (already started).
    """
    def _run():
        azurik_mod = SCRIPT_DIR / "azurik_mod.py"
        args = [
            sys.executable, str(azurik_mod),
            "randomize-full",
            "--iso", str(iso_path),
            "--seed", str(seed),
            "--output", str(output_path),
        ]
        if not do_major:
            args.append("--no-major")
        if not do_keys:
            args.append("--no-keys")
        if not do_gems:
            args.append("--no-gems")
        if not do_barriers:
            args.append("--no-barriers")
        if not do_connections:
            args.append("--no-connections")
        if not do_qol:
            args.append("--no-qol")
        if fps_unlock:
            args.append("--fps-unlock")
        if item_pool:
            args.extend(["--item-pool", json.dumps(item_pool)])
        if obsidian_cost is not None:
            args.extend(["--obsidian-cost", str(obsidian_cost)])
        if config_edits:
            args.extend(["--config-mod", json.dumps(config_edits)])
        if force_unsolvable:
            args.append("--force")

        try:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(SCRIPT_DIR),
            )
            full_output = []
            for line in proc.stdout:
                full_output.append(line)
                if on_output:
                    on_output(line)
            proc.wait()

            result = BuildResult(
                success=proc.returncode == 0,
                output="".join(full_output),
                output_path=output_path if proc.returncode == 0 else None,
                seed=seed,
            )
        except Exception as e:
            result = BuildResult(success=False, output=f"Error: {e}")

        if on_done:
            on_done(result)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def run_config_dump(iso_path: Path, section: str, entity: str | None = None) -> str:
    """Run azurik_mod.py dump and return output."""
    azurik_mod = SCRIPT_DIR / "azurik_mod.py"
    args = [sys.executable, str(azurik_mod), "dump", "--iso", str(iso_path),
            "--section", section]
    if entity:
        args.extend(["--entity", entity])
    result = subprocess.run(args, capture_output=True, text=True, cwd=str(SCRIPT_DIR))
    return result.stdout + result.stderr


def list_sections() -> list[str]:
    """List config sections from the registry."""
    registry = SCRIPT_DIR / "claude_output" / "config_registry.json"
    if not registry.exists():
        return []
    with open(registry) as f:
        data = json.load(f)
    # Sections are nested under "sections" key; skip metadata keys like _meta
    sections = data.get("sections", {})
    return sorted(sections.keys())


def list_entities(section: str) -> list[str]:
    """List entities in a config section from the registry."""
    registry = SCRIPT_DIR / "claude_output" / "config_registry.json"
    if not registry.exists():
        return []
    with open(registry) as f:
        data = json.load(f)
    sec = data.get("sections", {}).get(section, {})
    entities = sec.get("entities", {})
    return sorted(entities.keys())


def load_keyed_tables(config_path: Path) -> dict | None:
    """Load all keyed tables from a config.xbr file.

    Returns dict of section_name -> KeyedTable, or None on failure.
    """
    parser_path = SCRIPT_DIR / "claude_output" / "keyed_table_parser.py"
    if not parser_path.exists():
        return None
    # Import the parser module dynamically
    import importlib.util
    spec = importlib.util.spec_from_file_location("keyed_table_parser", str(parser_path))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod.load_all_tables(str(config_path))
    except Exception:
        return None


def extract_config_xbr(iso_path: Path) -> Path | None:
    """Extract config.xbr from ISO to a temp file and return its path."""
    import tempfile
    xdvdfs = find_xdvdfs()
    if not xdvdfs:
        return None
    tmpdir = tempfile.mkdtemp(prefix="azurik_cfg_")
    out_file = Path(tmpdir) / "config.xbr"
    try:
        import subprocess
        result = subprocess.run(
            [xdvdfs, "copy-out", str(iso_path), "gamedata/config.xbr", str(out_file)],
            capture_output=True, text=True)
        if result.returncode == 0 and out_file.exists():
            return out_file
    except Exception:
        pass
    return None


def load_all_pickups() -> dict | None:
    """Load the all_pickups.json catalog."""
    path = SCRIPT_DIR / "claude_output" / "all_pickups.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)
