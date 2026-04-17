"""Settings page — theme toggle + xdvdfs cache pointer + env-var help."""

from __future__ import annotations

import os
import platform
import tkinter as tk
from tkinter import ttk

import sv_ttk
from platformdirs import user_cache_dir

from ..models import save_ui_prefs
from ..widgets import Page, Section


class SettingsPage(Page):
    title = "Settings"
    description = ("Theme toggles, cache locations, and environment "
                   "overrides for the toolkit.")

    def _build(self) -> None:
        theme_section = Section(self._body, title="Appearance",
                                initially_open=True)
        theme_section.pack(fill=tk.X, pady=(0, 10))

        row = ttk.Frame(theme_section.body)
        row.pack(fill=tk.X, pady=4)
        ttk.Label(row, text="Theme:", width=16).pack(side=tk.LEFT)
        self._theme_var = tk.StringVar(value=self.app.state.theme)
        for label, value in [("Dark", "dark"), ("Light", "light")]:
            ttk.Radiobutton(row, text=label, variable=self._theme_var,
                            value=value, command=self._apply_theme).pack(
                side=tk.LEFT, padx=4)

        env_section = Section(self._body, title="Environment",
                              initially_open=True)
        env_section.pack(fill=tk.X, pady=(0, 10))

        cache_row = ttk.Frame(env_section.body)
        cache_row.pack(fill=tk.X, pady=4)
        ttk.Label(cache_row, text="xdvdfs cache:", width=20).pack(side=tk.LEFT)
        ttk.Label(cache_row,
                  text=user_cache_dir("azurik_mod", appauthor=False),
                  foreground="gray").pack(side=tk.LEFT)

        env_row = ttk.Frame(env_section.body)
        env_row.pack(fill=tk.X, pady=4)
        ttk.Label(env_row, text="$AZURIK_XDVDFS:", width=20).pack(side=tk.LEFT)
        override = os.environ.get("AZURIK_XDVDFS", "(unset)")
        ttk.Label(env_row, text=override, foreground="gray").pack(side=tk.LEFT)

        platform_row = ttk.Frame(env_section.body)
        platform_row.pack(fill=tk.X, pady=4)
        ttk.Label(platform_row, text="Platform:", width=20).pack(side=tk.LEFT)
        ttk.Label(platform_row,
                  text=f"{platform.system()} {platform.machine()}",
                  foreground="gray").pack(side=tk.LEFT)

    def _apply_theme(self) -> None:
        theme = self._theme_var.get()
        sv_ttk.set_theme(theme)
        self.app.state.set_theme(theme)
        save_ui_prefs({"theme": theme})
