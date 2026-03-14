"""Reusable widgets for the Azurik GUI."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path
from typing import Callable


class ISOFilePicker(ttk.Frame):
    """File picker row: label + path entry + Browse button."""

    def __init__(self, parent, label: str = "Game ISO:",
                 on_change: Callable[[Path | None], None] | None = None):
        super().__init__(parent)
        self._on_change = on_change

        ttk.Label(self, text=label).pack(side=tk.LEFT, padx=(0, 5))

        self._var = tk.StringVar()
        self._entry = ttk.Entry(self, textvariable=self._var, width=55)
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        ttk.Button(self, text="Browse...", command=self._browse).pack(side=tk.LEFT)

    def _browse(self):
        path = filedialog.askopenfilename(
            title="Select Azurik ISO",
            filetypes=[("Xbox ISO", "*.iso"), ("All files", "*.*")],
        )
        if path:
            self._var.set(path)
            if self._on_change:
                self._on_change(Path(path))

    def get_path(self) -> Path | None:
        val = self._var.get().strip()
        return Path(val) if val else None

    def set_path(self, path: Path | None):
        self._var.set(str(path) if path else "")


class SeedEntry(ttk.Frame):
    """Seed input with randomize button."""

    def __init__(self, parent):
        super().__init__(parent)
        import random

        ttk.Label(self, text="Seed:").pack(side=tk.LEFT, padx=(0, 5))

        self._var = tk.StringVar(value="42")
        self._entry = ttk.Entry(self, textvariable=self._var, width=12)
        self._entry.pack(side=tk.LEFT, padx=(0, 5))

        ttk.Button(self, text="Random", command=self._randomize).pack(side=tk.LEFT)
        self._rng = random

    def _randomize(self):
        self._var.set(str(self._rng.randint(0, 999999)))

    def get_seed(self) -> int:
        try:
            return int(self._var.get().strip())
        except ValueError:
            return 42


class ProgressFrame(ttk.Frame):
    """Progress bar + status label."""

    def __init__(self, parent):
        super().__init__(parent)

        self._label = ttk.Label(self, text="Ready")
        self._label.pack(fill=tk.X, pady=(0, 3))

        self._bar = ttk.Progressbar(self, mode="indeterminate", length=400)
        self._bar.pack(fill=tk.X)

    def start(self, message: str = "Working..."):
        self._label.config(text=message)
        self._bar.config(mode="indeterminate")
        self._bar.start(15)

    def stop(self, message: str = "Done"):
        self._bar.stop()
        self._label.config(text=message)

    def set_status(self, message: str):
        self._label.config(text=message)


class OutputPicker(ttk.Frame):
    """Output file path picker."""

    def __init__(self, parent, label: str = "Output ISO:", default_name: str = "Azurik_randomized.iso"):
        super().__init__(parent)
        self._default_name = default_name

        ttk.Label(self, text=label).pack(side=tk.LEFT, padx=(0, 5))

        self._var = tk.StringVar()
        self._entry = ttk.Entry(self, textvariable=self._var, width=55)
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        ttk.Button(self, text="Browse...", command=self._browse).pack(side=tk.LEFT)

    def _browse(self):
        path = filedialog.asksaveasfilename(
            title="Save Output ISO",
            defaultextension=".iso",
            filetypes=[("Xbox ISO", "*.iso"), ("All files", "*.*")],
            initialfile=self._default_name,
        )
        if path:
            self._var.set(path)

    def get_path(self) -> Path | None:
        val = self._var.get().strip()
        return Path(val) if val else None

    def set_path(self, path: Path | None):
        self._var.set(str(path) if path else "")

    def auto_fill(self, iso_path: Path | None):
        """Auto-fill output path based on input ISO."""
        if iso_path and not self._var.get().strip():
            self._var.set(str(iso_path.with_name(self._default_name)))


class LogBox(ttk.Frame):
    """Scrollable text log for build output."""

    def __init__(self, parent, height: int = 12):
        super().__init__(parent)

        self._text = tk.Text(self, height=height, wrap=tk.WORD, state=tk.DISABLED,
                             font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
                             insertbackground="#d4d4d4")
        scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self._text.yview)
        self._text.config(yscrollcommand=scrollbar.set)

        self._text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def append(self, text: str):
        self._text.config(state=tk.NORMAL)
        self._text.insert(tk.END, text)
        self._text.see(tk.END)
        self._text.config(state=tk.DISABLED)

    def clear(self):
        self._text.config(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        self._text.config(state=tk.DISABLED)
