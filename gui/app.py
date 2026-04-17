"""Main application window — sidebar + pages shell, sv-ttk themed."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

import sv_ttk

from . import backend
from .models import AppState, load_ui_prefs, save_ui_prefs
from .pages.build import BuildPage
from .pages.config_editor import ConfigEditorTab
from .pages.entity_editor import EntityEditorTab
from .pages.patches import PatchesPage
from .pages.project import ProjectPage
from .pages.randomize import RandomizePage
from .pages.settings import SettingsPage
from .widgets import Sidebar

# The sidebar order and labels.  Each id matches a self._pages key below.
_PAGE_SPECS: list[tuple[str, str]] = [
    ("project", "Project"),
    ("randomize", "Randomize"),
    ("patches", "Patches"),
    ("entity_editor", "Entity Editor"),
    ("config_editor", "Config Editor"),
    ("build", "Build & Logs"),
    ("settings", "Settings"),
]


def main() -> None:
    """Entry point for `azurik-gui` / `python -m gui`."""
    app = AzurikApp()
    app.run()


class AzurikApp:
    """Top-level GUI app — a sidebar + swappable content area."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Azurik Modding Toolkit")
        self.root.geometry("1100x760")
        self.root.minsize(900, 600)
        self.state = AppState()

        # Load persisted preferences (theme).
        prefs = load_ui_prefs()
        theme = prefs.get("theme", "dark")
        self.state.theme = theme  # type: ignore[assignment]
        sv_ttk.set_theme(theme)

        # Auto-detect the first ISO sitting in the repo's iso/ folder
        # BEFORE we build pages, so the Project page's ISO picker and
        # output picker populate on initial render.
        auto_iso = backend.find_base_iso()
        if auto_iso:
            self.state.iso_path = auto_iso  # set directly; bus not yet wired

        self._build_ui()

        # Now that subscribers are registered, publish the iso_changed
        # event so the status bar reflects the auto-detected ISO.
        if auto_iso:
            self.state.bus.emit("iso_changed", auto_iso)

    # --- UI construction ------------------------------------------------

    def _build_ui(self) -> None:
        # 3-column grid: sidebar | separator | content.
        self.root.columnconfigure(2, weight=1)
        self.root.rowconfigure(0, weight=1)

        self._sidebar = Sidebar(self.root, _PAGE_SPECS)
        self._sidebar.grid(row=0, column=0, sticky="ns")
        self._sidebar.bind("<<PageSelected>>", self._on_page_selected)

        ttk.Separator(self.root, orient=tk.VERTICAL).grid(
            row=0, column=1, sticky="ns")

        self._content = ttk.Frame(self.root)
        self._content.grid(row=0, column=2, sticky="nsew")
        self._content.rowconfigure(0, weight=1)
        self._content.columnconfigure(0, weight=1)

        # Status bar (bottom).
        status = ttk.Frame(self.root, padding=(8, 4))
        status.grid(row=1, column=0, columnspan=3, sticky="ew")
        self._status_label = ttk.Label(status, text="Ready")
        self._status_label.pack(side=tk.LEFT)
        self.state.bus.subscribe("iso_changed", self._sync_status)
        self.state.bus.subscribe("build_done", self._sync_status)

        # Build every page up front (cheap) and stack them in the same cell.
        self._pages: dict[str, ttk.Frame] = {}
        for page_id, cls in {
            "project": ProjectPage,
            "randomize": RandomizePage,
            "patches": PatchesPage,
            "entity_editor": EntityEditorTab,  # legacy tab class still fine
            "config_editor": ConfigEditorTab,
            "build": BuildPage,
            "settings": SettingsPage,
        }.items():
            page = cls(self._content, self)
            page.grid(row=0, column=0, sticky="nsew")
            self._pages[page_id] = page

        # Bookkeeping for legacy tabs that app.py historically exposed
        # as attributes (some existing code reads `app.tab_entity`).
        self.tab_randomizer = self._pages["randomize"]
        self.tab_qol = self._pages["patches"]
        self.tab_entity = self._pages["entity_editor"]
        self.tab_config = self._pages["config_editor"]

        # Show the first page.
        self.show_page("project")

    # --- Page management ------------------------------------------------

    def show_page(self, page_id: str) -> None:
        """Raise the named page and update the sidebar selection."""
        page = self._pages.get(page_id)
        if page is None:
            return
        page.tkraise()
        self._sidebar.select(page_id)

    def _on_page_selected(self, _event) -> None:
        page_id = self._sidebar.selected
        if page_id:
            self._pages[page_id].tkraise()

    # --- Helpers --------------------------------------------------------

    def _sync_status(self, _payload) -> None:
        iso = self.state.iso_path
        iso_text = iso.name if iso else "(no ISO)"
        seed_text = (f"last seed {self.state.last_seed}"
                     if self.state.last_seed is not None else "")
        self._status_label.configure(
            text=f"{iso_text}  {seed_text}".strip())

    def get_iso_path(self) -> Path | None:
        return self.state.iso_path

    def run(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self) -> None:
        backend.cleanup_temp_dirs()
        save_ui_prefs({"theme": self.state.theme})
        self.root.destroy()
