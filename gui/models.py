"""Data models + tiny event bus for the Azurik GUI."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from platformdirs import user_config_dir


# ---------------------------------------------------------------------------
# Persistent UI prefs (theme, window geometry)
# ---------------------------------------------------------------------------

_PREFS_DIR = Path(user_config_dir("azurik_mod", appauthor=False))
_PREFS_PATH = _PREFS_DIR / "ui.json"

Theme = Literal["dark", "light"]


def load_ui_prefs() -> dict:
    try:
        return json.loads(_PREFS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_ui_prefs(prefs: dict) -> None:
    _PREFS_DIR.mkdir(parents=True, exist_ok=True)
    _PREFS_PATH.write_text(json.dumps(prefs, indent=2))


# ---------------------------------------------------------------------------
# Tiny event bus — single-threaded, fires synchronously on the Tk main thread
# ---------------------------------------------------------------------------


class EventBus:
    """Name-based pub/sub for in-process cross-page notifications.

    Designed to run only on the Tk main thread — callbacks execute
    synchronously from `emit()`.  Worker threads must marshal events
    through `root.after(0, bus.emit, name, payload)`.
    """

    def __init__(self) -> None:
        self._subs: dict[str, list[Callable[[object], None]]] = {}

    def subscribe(self, name: str, callback: Callable[[object], None]) -> None:
        self._subs.setdefault(name, []).append(callback)

    def emit(self, name: str, payload: object = None) -> None:
        for cb in list(self._subs.get(name, [])):
            try:
                cb(payload)
            except Exception as exc:  # noqa: BLE001
                print(f"  EventBus listener for {name!r} raised: {exc}")


# ---------------------------------------------------------------------------
# Shared application state
# ---------------------------------------------------------------------------


@dataclass
class AppState:
    """Shared state surface consumed by every page.

    `bus` is where pages announce changes:

        ``iso_changed`` payload: Path | None
        ``output_changed`` payload: Path | None
        ``packs_changed`` payload: dict[str, bool]
        ``build_started`` payload: RandomizerConfig
        ``build_log`` payload: str
        ``build_done`` payload: BuildResult
        ``theme_changed`` payload: "dark" | "light"
    """

    iso_path: Path | None = None
    output_dir: Path | None = None
    last_seed: int | None = None
    last_output: Path | None = None
    enabled_packs: dict[str, bool] = field(default_factory=dict)
    # pack_params[pack_name][param_name] = float (slider values).
    pack_params: dict[str, dict[str, float]] = field(default_factory=dict)
    theme: Theme = "dark"

    bus: EventBus = field(default_factory=EventBus)

    def set_iso(self, path: Path | None) -> None:
        self.iso_path = path
        self.bus.emit("iso_changed", path)

    def set_output(self, path: Path | None) -> None:
        self.output_dir = path
        self.bus.emit("output_changed", path)

    def set_pack(self, name: str, enabled: bool) -> None:
        self.enabled_packs[name] = enabled
        self.bus.emit("packs_changed", dict(self.enabled_packs))

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.bus.emit("theme_changed", theme)


@dataclass
class RandomizerConfig:
    """Shuffle-pool + advanced options for a randomizer run.

    Patch pack toggles (QoL sub-patches, 60 FPS unlock, player physics,
    …) live on the Patches page and are merged into the build thread
    separately — they are NOT fields on this dataclass.  Everything here
    defaults to OFF so an untouched build is a no-op.
    """

    seed: int = 42
    do_major: bool = False
    do_keys: bool = False
    do_gems: bool = False
    do_barriers: bool = False
    do_connections: bool = False
    output_path: Path | None = None
    item_pool: dict[str, int] | None = None
    obsidian_cost: int | None = None
    config_edits: dict | None = None
    force_unsolvable: bool = False

    def to_args(self, iso_path: Path) -> list[str]:
        """Build CLI argument list for `azurik-mod randomize-full`.

        Only shuffle-pool / advanced flags are emitted here.  Patch
        packs (`--gem-popups`, `--pickup-anims`, `--fps-unlock`,
        `--gravity`, `--player-walk-scale`, `--player-run-scale`) are
        added by the Build page from the Patches-page state before
        this method is called — or passed straight into
        `backend.run_randomizer` which bypasses CLI serialisation.
        """
        args = [
            "randomize-full",
            "--iso", str(iso_path),
            "--seed", str(self.seed),
            "--output", str(self.output_path or
                            iso_path.with_name("Azurik_randomized.iso")),
        ]
        if not self.do_major:
            args.append("--no-major")
        if not self.do_keys:
            args.append("--no-keys")
        if not self.do_gems:
            args.append("--no-gems")
        if not self.do_barriers:
            args.append("--no-barriers")
        if not self.do_connections:
            args.append("--no-connections")
        if self.item_pool:
            args.extend(["--item-pool", json.dumps(self.item_pool)])
        if self.obsidian_cost is not None:
            args.extend(["--obsidian-cost", str(self.obsidian_cost)])
        if self.config_edits:
            args.extend(["--config-mod", json.dumps(self.config_edits)])
        if self.force_unsolvable:
            args.append("--force")
        return args
