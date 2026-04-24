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
        prefs = json.loads(_PREFS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    migrate_legacy_pack_keys(prefs)
    return prefs


def save_ui_prefs(prefs: dict) -> None:
    _PREFS_DIR.mkdir(parents=True, exist_ok=True)
    _PREFS_PATH.write_text(json.dumps(prefs, indent=2))


# ---------------------------------------------------------------------------
# Legacy pack-name migration
# ---------------------------------------------------------------------------
#
# Scripts that pin pack names and any future on-disk GUI prefs that
# grow an ``enabled_packs`` / ``pack_params`` channel would otherwise
# quietly stop applying a pack the day we rename it.  We rewrite the
# stale keys once on load (and emit a single ``UserWarning``) so the
# user keeps the effect they had pre-rename.
#
# Stays in sync with
# :data:`azurik_mod.patching.registry._LEGACY_PACK_ALIASES` — add new
# renames in both places (module-level constant here so the gui
# package doesn't need to import the registry just to migrate
# prefs).

_LEGACY_PACK_RENAMES: dict[str, str] = {
    "cheat_entity_hp": "player_max_hp",
}


def migrate_legacy_pack_keys(prefs: dict) -> bool:
    """Rewrite legacy pack-name keys inside ``prefs`` in place.

    Scans the typical persistence channels (``enabled_packs``,
    ``pack_params``, top-level ``*_packs`` dicts) and replaces any
    matching legacy key with its current equivalent.  Warns once per
    process if any rename actually fires.

    Returns True when at least one key was renamed.  The return value
    lets callers decide whether to immediately ``save_ui_prefs`` the
    cleaned-up dict vs. waiting for the next normal save.

    Safe to call on any dict shape — unknown keys / non-dict values
    are ignored, so this stays future-compatible with prefs schemas
    we haven't shipped yet.
    """
    if not isinstance(prefs, dict) or not _LEGACY_PACK_RENAMES:
        return False

    dirty = False

    def _rename_keys_in(mapping: object) -> None:
        nonlocal dirty
        if not isinstance(mapping, dict):
            return
        for old, new in _LEGACY_PACK_RENAMES.items():
            if old in mapping:
                if new not in mapping:
                    mapping[new] = mapping[old]
                del mapping[old]
                dirty = True

    for channel in ("enabled_packs", "pack_params"):
        _rename_keys_in(prefs.get(channel))

    for key, value in prefs.items():
        if key.endswith("_packs") or key.endswith("_pack_params"):
            _rename_keys_in(value)

    if dirty:
        import warnings
        warnings.warn(
            "Migrated legacy pack name(s) "
            f"{sorted(_LEGACY_PACK_RENAMES)} in saved GUI prefs — "
            "prefs will be rewritten on next save.",
            UserWarning,
            stacklevel=2,
        )
    return dirty


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

    `bus` is where pages announce changes.  Live events + subscribers:

        ``iso_changed``   payload: Path | None
                          subscribers: app._sync_status (status bar)
        ``packs_changed`` payload: dict[str, bool]
                          subscribers: none currently (pages read
                          ``enabled_packs`` directly at build time)
        ``build_done``    payload: BuildResult
                          subscribers: app._sync_status (refresh the
                          status bar with last_seed / last_output)
        ``theme_changed`` payload: "dark" | "light"
                          subscribers: none; Settings page persists
                          the choice to disk directly
    """

    iso_path: Path | None = None
    last_seed: int | None = None
    last_output: Path | None = None
    enabled_packs: dict[str, bool] = field(default_factory=dict)
    # pack_params[pack_name][param_name] = float (slider values).
    pack_params: dict[str, dict[str, float]] = field(default_factory=dict)
    # Latest snapshot of the Randomize page's fields.  The Randomize
    # page pushes into this every time a widget changes (trace_add +
    # Entry commits); the Build page reads it when the user clicks
    # "Start build" so that button is now the single build entry point.
    randomize_config: "RandomizerConfig | None" = None
    theme: Theme = "dark"

    bus: EventBus = field(default_factory=EventBus)

    def set_iso(self, path: Path | None) -> None:
        self.iso_path = path
        self.bus.emit("iso_changed", path)

    # NB: output-directory state used to live on this class but the
    # output-path UX moved entirely into the Project page — each build
    # picks its own output filename adjacent to the source ISO and
    # ``RandomizerConfig.output_path`` carries the final value into the
    # pipeline.  No global output-dir bus event is emitted now.

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

    Fields marked "(pipeline-only)" are forwarded end-to-end from this
    dataclass through `gui.backend.run_randomizer` to the `azurik-mod`
    argparse namespace, but the Randomize page does not yet expose a UI
    for composing them — scripts / tests can still set them directly
    on a `RandomizerConfig` instance:

    * ``config_edits``   — dict passed as ``--config-mod`` JSON.  The
                           Entity Editor tab's ``get_pending_mod()``
                           output is merged into this by the Build
                           page at build time (see
                           ``BuildPage._merge_config_edits``); the
                           Config Editor tab is read-only and does
                           not contribute edits here today.
    * ``force_unsolvable`` — passes ``--force`` to the randomizer, for
                           rebuilding unsolvable seeds on purpose.  The
                           Build page sets this at runtime when the
                           user confirms the "Seed unsolvable → build
                           anyway?" prompt; never set from the
                           Randomize page directly.
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
    config_edits: dict | None = None    # pipeline-only (see class docstring)
    force_unsolvable: bool = False      # pipeline-only (see class docstring)
