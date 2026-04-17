"""Project page — ISO picker, output folder, xdvdfs status, version info."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from azurik_mod import __version__
from azurik_mod.iso.xdvdfs import get_xdvdfs

from ..widgets import ISOFilePicker, OutputPicker, Page, Section


class ProjectPage(Page):
    title = "Project"
    description = ("Select your base game ISO and output location.  These values "
                   "drive every other page.")

    def _build(self) -> None:
        iso_section = Section(self._body, title="Game ISO", initially_open=True)
        iso_section.pack(fill=tk.X, pady=(0, 10))

        self._iso_picker = ISOFilePicker(
            iso_section.body, label="Base ISO:",
            on_change=self._on_iso_change,
        )
        self._iso_picker.pack(fill=tk.X, pady=4)
        # If AppState already has an ISO (auto-detected at startup or
        # loaded from a prior session), show it here.
        self._iso_picker.set_path(self.app.state.iso_path)

        self._output = OutputPicker(iso_section.body, label="Output ISO:")
        self._output.pack(fill=tk.X, pady=4)
        self._output.auto_fill(self.app.state.iso_path)

        # Re-sync when the ISO changes from another page (or is set
        # programmatically during auto-detect).
        self.app.state.bus.subscribe("iso_changed", self._on_iso_event)

        env_section = Section(self._body, title="Environment", initially_open=True)
        env_section.pack(fill=tk.X, pady=(0, 10))

        version_row = ttk.Frame(env_section.body)
        version_row.pack(fill=tk.X, pady=3)
        ttk.Label(version_row, text="azurik-mod version:",
                  width=20).pack(side=tk.LEFT)
        ttk.Label(version_row, text=__version__).pack(side=tk.LEFT)

        xdvdfs_row = ttk.Frame(env_section.body)
        xdvdfs_row.pack(fill=tk.X, pady=3)
        ttk.Label(xdvdfs_row, text="xdvdfs path:", width=20).pack(side=tk.LEFT)
        xd = get_xdvdfs()
        ttk.Label(xdvdfs_row, text=str(xd) if xd else "(not installed)",
                  foreground="gray" if xd else "#e67e22").pack(side=tk.LEFT)

    def _on_iso_change(self, path: Path | None) -> None:
        self.app.state.set_iso(path)
        self._output.auto_fill(path)

    def _on_iso_event(self, path: Path | None) -> None:
        """React to bus `iso_changed` events (e.g. auto-detect fired)."""
        # Avoid re-triggering on_change if the current entry already matches.
        if self._iso_picker.get_path() != path:
            self._iso_picker.set_path(path)
        self._output.auto_fill(path)
