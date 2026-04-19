"""Build & Logs page — streams randomizer output into a single log box.

Every build also writes a persistent log file on disk (see
`backend.get_log_dir()`), so the user can open the folder, diff runs,
or attach a log to a bug report after the app closes.
"""

from __future__ import annotations

import os
import platform
import queue as _queue
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .. import backend
from ..widgets import LogBox, Page, PrimaryButton, SecondaryButton


def _open_in_file_manager(path: Path) -> None:
    """Open `path` (file or folder) in the platform's native explorer."""
    path = Path(path)
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", str(path)])
        elif system == "Windows":
            if path.is_dir():
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                # Select the file in Explorer.
                subprocess.Popen(["explorer", "/select,", str(path)])
        else:  # Linux + other UNIXes
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:  # noqa: BLE001
        messagebox.showwarning("Could not open", f"{path}\n\n{exc}")


class BuildPage(Page):
    title = "Build & Logs"
    description = ("Kick off a randomizer build.  Progress streams live "
                   "from the worker thread.")

    def _build(self) -> None:
        self._thread = None
        self._msg_queue: "_queue.Queue[tuple[str, object]] | None" = None
        self._pending_force = False
        self._last_config = None

        # Controls row.
        controls = ttk.Frame(self._body)
        controls.pack(fill=tk.X, pady=(0, 10))

        self._build_btn = PrimaryButton(controls, text="Start build",
                                        command=self._start)
        self._build_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._clear_btn = SecondaryButton(controls, text="Clear log",
                                          command=self._clear_log)
        self._clear_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._open_folder_btn = SecondaryButton(
            controls, text="Open log folder",
            command=self._open_log_folder)
        self._open_folder_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._open_last_btn = SecondaryButton(
            controls, text="Open last log",
            command=self._open_last_log, state=tk.DISABLED)
        self._open_last_btn.pack(side=tk.LEFT)
        self._last_log_path: Path | None = None

        # If a previous session left a log on disk, seed the "Open last
        # log" button with it so the user can inspect prior runs without
        # having to kick off a new build first.  The button stays
        # disabled only when the log folder is genuinely empty.
        prior_log = backend.latest_log_file()
        if prior_log is not None:
            self._last_log_path = prior_log
            self._open_last_btn.configure(state=tk.NORMAL)

        # Status line.
        self._status = ttk.Label(self._body, text="Ready")
        self._status.pack(fill=tk.X, pady=(0, 4))

        self._bar = ttk.Progressbar(self._body, mode="indeterminate", length=400)
        self._bar.pack(fill=tk.X, pady=(0, 10))

        # Log area.
        self._log = LogBox(self._body, height=22)
        self._log.pack(fill=tk.BOTH, expand=True)

        # Subscribe: Randomize page emits `build_request` with a RandomizerConfig.
        self.app.state.bus.subscribe("build_request", self._on_build_request)

    # --- Public API -----------------------------------------------------

    def start_build(self, config, force: bool = False) -> None:
        """Kick off a build using the given RandomizerConfig.

        Pack enablement (`qol`, `fps_unlock`, `player_physics`, …) lives on
        the Patches page, not the Randomize page, so this method merges
        `AppState.enabled_packs` into the config right before the worker
        fires.  Parametric slider values (`AppState.pack_params`) are
        forwarded the same way, but only for packs whose checkbox is ON —
        otherwise an enabled slider without an enabled pack would silently
        ship a patch the user thought they had disabled.
        """
        iso_path = self.app.state.iso_path
        if not iso_path:
            messagebox.showerror("No ISO selected",
                                 "Pick a base ISO on the Project page first.")
            return

        output_path = (config.output_path
                       or iso_path.with_name("Azurik_randomized.iso"))
        self._last_config = config
        self._pending_force = False

        self._build_btn.configure(state=tk.DISABLED, text="Building…")
        self._log.append(f"Building {output_path.name}  (seed {config.seed})\n\n")
        self._status.configure(text="Building randomized ISO…")
        self._bar.start(15)

        packs, pack_params = self._merge_packs()
        config_edits = self._merge_config_edits(config.config_edits)

        # Pass the full enabled-packs dict through so the backend
        # doesn't need per-pack kwargs.  The unified apply_pack
        # dispatcher handles the actual site-by-site work.
        self._thread, self._msg_queue = backend.run_randomizer(
            iso_path=iso_path,
            output_path=output_path,
            seed=config.seed,
            do_major=config.do_major,
            do_keys=config.do_keys,
            do_gems=config.do_gems,
            do_barriers=config.do_barriers,
            do_connections=config.do_connections,
            packs=packs,
            pack_params=pack_params,
            item_pool=config.item_pool,
            obsidian_cost=config.obsidian_cost,
            config_edits=config_edits,
            force_unsolvable=force or config.force_unsolvable,
        )
        self._poll_queue()

    def _merge_config_edits(self, existing: dict | None) -> dict | None:
        """Merge Entity Editor pending edits into ``config_edits``.

        The Entity Editor tab buffers user-entered property tweaks in
        memory; until this merge the build pipeline never saw them
        (the tab was orphaned — :meth:`get_pending_mod` was defined
        but never called from anywhere).  Now every Start-build run
        folds them into the same ``--config-mod`` JSON blob that the
        CLI already consumes.

        If both sources define edits for the same ``section/entity/
        property`` cell, the Entity Editor wins — it represents the
        more-recent interactive state.  Keyed-table patches
        (``_keyed_patches``) are concatenated by section/entity with
        the same "editor wins on conflict" rule.

        Returns ``None`` when neither side has anything; else a fresh
        dict safe to json.dumps().
        """
        tab = getattr(self.app, "tab_entity", None)
        editor_mod = tab.get_pending_mod() if tab is not None else None
        if editor_mod is None:
            return existing

        # Deep-ish copy so we don't mutate the editor's live buffer.
        import copy
        merged = copy.deepcopy(existing) if existing else {
            "name": "Combined mod", "format": "grouped", "sections": {}}
        # Ensure top-level shape.
        merged.setdefault("format", "grouped")
        merged.setdefault("sections", {})

        # 1. Merge variant sections (section -> entity -> prop -> value).
        for sec_key, entities in editor_mod.get("sections", {}).items():
            dst = merged["sections"].setdefault(sec_key, {})
            for ent, props in entities.items():
                dst.setdefault(ent, {}).update(props)

        # 2. Merge keyed patches (top-level `_keyed_patches` blob).
        ek = editor_mod.get("_keyed_patches")
        if ek:
            dst_kp = merged.setdefault("_keyed_patches", {})
            for sec_key, entities in ek.items():
                dst_sec = dst_kp.setdefault(sec_key, {})
                for ent, props in entities.items():
                    dst_sec.setdefault(ent, {}).update(props)

        self._log.append(
            f"  + Entity Editor contributes "
            f"{tab.get_edit_count()} pending edits\n")
        return merged

    def _merge_packs(self) -> tuple[dict[str, bool], dict[str, dict[str, float]]]:
        """Read pack enablement + slider values from AppState.

        Returns (enabled_packs, pack_params) with slider values from
        disabled packs stripped so they can't smuggle a patch past the
        Patches page's checkbox.
        """
        enabled = dict(getattr(self.app.state, "enabled_packs", {}) or {})
        params = {k: dict(v) for k, v in
                  (getattr(self.app.state, "pack_params", {}) or {}).items()}

        # Respect the pack checkboxes: drop params for any pack that is
        # currently disabled.
        for pack_name in list(params.keys()):
            if not enabled.get(pack_name, False):
                params.pop(pack_name, None)
        return enabled, params

    # --- Internals ------------------------------------------------------

    def _start(self) -> None:
        """Start a build using the latest Randomize-page snapshot.

        The Randomize page mirrors its fields into
        ``AppState.randomize_config`` on every change, so the user
        can head straight here after configuring and hit Start build
        without any intermediate click.  Falls back to a fresh
        default config if the Randomize page has never been visited."""
        from ..models import RandomizerConfig
        config = (getattr(self.app.state, "randomize_config", None)
                  or self._last_config
                  or RandomizerConfig())
        self.start_build(config)

    def _clear_log(self) -> None:
        self._log.clear()

    def _open_log_folder(self) -> None:
        _open_in_file_manager(backend.get_log_dir())

    def _open_last_log(self) -> None:
        # Prefer the log file from the most recent build of this session;
        # fall back to the newest file on disk if the app was just opened.
        path = self._last_log_path or backend.latest_log_file()
        if path is None or not Path(path).exists():
            messagebox.showinfo(
                "No log yet",
                "No build has been run in this install yet.\n\n"
                f"Log folder: {backend.get_log_dir()}")
            return
        _open_in_file_manager(path)

    def _on_build_request(self, payload) -> None:
        """Randomize page published `build_request` → kick off a build."""
        self.app.show_page("build")
        self.start_build(payload)

    def _poll_queue(self) -> None:
        if self._msg_queue is None:
            return
        try:
            while True:
                msg_type, payload = self._msg_queue.get_nowait()
                if msg_type == "output":
                    self._log.append(payload)
                    if "ERROR: Could not find solvable placement" in payload:
                        self._pending_force = True
                elif msg_type == "done":
                    self._handle_done(payload)
                    return
        except _queue.Empty:
            pass
        self.after(50, self._poll_queue)

    def _handle_done(self, result) -> None:
        self._bar.stop()
        self._build_btn.configure(state=tk.NORMAL, text="Start build")

        # Remember the log file for the "Open last log" button, regardless
        # of whether the build succeeded or failed.
        if getattr(result, "log_file", None):
            self._last_log_path = Path(result.log_file)
            self._open_last_btn.configure(state=tk.NORMAL)
            self._log.append(
                f"\n[log file: {self._last_log_path}]\n")

        if self._pending_force and not result.success:
            if messagebox.askyesno(
                    "Unsolvable seed",
                    "No solvable placement found for this seed.\n\n"
                    "This can happen with custom item pools or certain category "
                    "combinations.\n\nBuild anyway? (The game may not be completable)",
                    icon="warning"):
                self._pending_force = False
                self._log.append("\nRetrying with --force…\n")
                self.start_build(self._last_config, force=True)
                return
            self._status.configure(text="Build cancelled — seed unsolvable")
            return

        if result.success:
            self.app.state.last_seed = result.seed
            self.app.state.last_output = result.output_path
            self._status.configure(
                text=f"Done!  Seed {result.seed}  →  {result.output_path}")
        else:
            log_hint = (f"  (log: {self._last_log_path.name})"
                        if self._last_log_path else "")
            self._status.configure(
                text=f"Build failed — see log above{log_hint}")
