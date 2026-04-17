"""Main application window."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from pathlib import Path

from . import backend
from .models import AppState
from .widgets import ISOFilePicker
from .tab_randomizer import RandomizerTab
from .tab_qol import QoLTab
from .tab_entity_editor import EntityEditorTab
from .tab_config_editor import ConfigEditorTab


class AzurikApp:
    """Main GUI application."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Azurik: Rise of Perathia — Mod Tool")
        self.root.geometry("750x700")
        self.root.minsize(650, 550)
        self.state = AppState()

        self._build_ui()

    def _build_ui(self):
        # -- ISO picker (shared across all tabs) --
        iso_frame = ttk.Frame(self.root)
        iso_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        self.iso_picker = ISOFilePicker(iso_frame, on_change=self._on_iso_change)
        self.iso_picker.pack(fill=tk.X)

        ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=10, pady=5)

        # -- Tab notebook --
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self.tab_randomizer = RandomizerTab(self.notebook, self)
        self.tab_qol = QoLTab(self.notebook, self)
        self.tab_entity = EntityEditorTab(self.notebook, self)
        self.tab_config = ConfigEditorTab(self.notebook, self)

        self.notebook.add(self.tab_randomizer, text="Randomizer")
        self.notebook.add(self.tab_qol, text="QoL")
        self.notebook.add(self.tab_entity, text="Entity Editor")
        self.notebook.add(self.tab_config, text="Config Editor")

        # Auto-detect base ISO in iso/ folder
        base_iso = backend.find_base_iso()
        if base_iso:
            self.iso_picker.set_path(base_iso)
            self._on_iso_change(base_iso)

    def _on_iso_change(self, path: Path | None):
        self.state.iso_path = path
        self.tab_randomizer.auto_fill_output(path)

    def get_iso_path(self) -> Path | None:
        return self.iso_picker.get_path()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self):
        backend.cleanup_temp_dirs()
        self.root.destroy()
