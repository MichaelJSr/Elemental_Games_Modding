"""Config Editor page — browse and (future) edit config.xbr values.

Subclasses `Page` so it participates in the scrollable-body shell and
renders the standard title + description header like every other page.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox

from .. import backend
from ..widgets import Page


class ConfigEditorTab(Page):
    title = "Config Editor"
    description = ("Browse live config.xbr values for a given section / "
                   "entity.  Editing is a work in progress — for now the "
                   "page is read-only and falls back to the registry JSON "
                   "if no ISO is selected.")

    def _build(self) -> None:
        # WIP banner (subtle, doesn't take over the whole page).
        banner = tk.Frame(self._body, bg="#CC8800")
        banner.pack(fill=tk.X, pady=(0, 10))
        tk.Label(banner,
                 text="\u26A0  This feature is a work in progress — read-only for now",
                 bg="#CC8800", fg="white", font=("", 9, "bold"),
                 pady=4).pack()

        # Section / entity selectors.
        ctrl = ttk.Frame(self._body)
        ctrl.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(ctrl, text="Section:").pack(side=tk.LEFT, padx=(0, 5))
        self._section_var = tk.StringVar()
        sections = backend.list_sections()
        self._section_combo = ttk.Combobox(
            ctrl, textvariable=self._section_var,
            values=sections, state="readonly", width=22)
        self._section_combo.pack(side=tk.LEFT, padx=(0, 10))
        self._section_combo.bind(
            "<<ComboboxSelected>>", self._on_section_change)

        ttk.Label(ctrl, text="Entity:").pack(side=tk.LEFT, padx=(0, 5))
        self._entity_var = tk.StringVar()
        self._entity_combo = ttk.Combobox(
            ctrl, textvariable=self._entity_var,
            values=[], state="readonly", width=22)
        self._entity_combo.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Button(ctrl, text="Load Values",
                   command=self._load_values).pack(side=tk.LEFT)

        # Values display.  The outer Page body is scrollable, but the
        # text widget gets its own scrollbar for long entity dumps.
        text_row = ttk.Frame(self._body)
        text_row.pack(fill=tk.BOTH, expand=True, pady=(0, 6))
        self._text = tk.Text(
            text_row, height=22, wrap=tk.WORD, state=tk.DISABLED,
            font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4")
        scrollbar = ttk.Scrollbar(
            text_row, orient=tk.VERTICAL, command=self._text.yview)
        self._text.config(yscrollcommand=scrollbar.set)
        self._text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Status line.
        self._status = ttk.Label(
            self._body,
            text="Select a section and entity, then click Load Values")
        self._status.pack(fill=tk.X, pady=(0, 4))

        # Seed the first section so the entity dropdown isn't empty on
        # open — makes the page usable without having to click through.
        if sections:
            self._section_combo.set(sections[0])
            self._on_section_change()

    # --- event handlers --------------------------------------------------

    def _on_section_change(self, event=None) -> None:
        section = self._section_var.get()
        if not section:
            return
        entities = backend.list_entities(section)
        self._entity_combo.config(values=entities)
        if entities:
            self._entity_combo.set(entities[0])

    def _load_values(self) -> None:
        iso_path = self.app.get_iso_path()
        if not iso_path or not iso_path.exists():
            messagebox.showinfo(
                "Info",
                "Select a game ISO first to load live values.\n"
                "Showing registry data instead.")
            self._show_registry_data()
            return

        section = self._section_var.get()
        entity = self._entity_var.get()
        if not section:
            return

        self._status.config(text="Loading...")
        output = backend.run_config_dump(
            iso_path, section, entity if entity else None)

        self._text.config(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        self._text.insert("1.0", output)
        self._text.config(state=tk.DISABLED)
        self._status.config(text=f"Loaded {section}/{entity or 'all'}")

    def _show_registry_data(self) -> None:
        """Fallback when no ISO is selected: dump the bundled registry JSON."""
        section = self._section_var.get()
        entity = self._entity_var.get()
        if not section:
            return

        import json
        # Share the memoised registry cache that ``list_sections`` /
        # ``list_entities`` already built — no point re-parsing 876 KB
        # just for the "no ISO loaded" fallback.
        reg = backend._load_registry()
        if not reg:
            return

        sections = reg.get("sections", {})
        if section not in sections:
            self._status.config(text=f"Section '{section}' not found in registry")
            return

        data = sections[section]
        entities = data.get("entities", {})
        if entity and entity in entities:
            data = {entity: entities[entity]}
        elif entities:
            data = entities

        text = json.dumps(data, indent=2)
        self._text.config(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        self._text.insert("1.0", text)
        self._text.config(state=tk.DISABLED)
        self._status.config(text=f"Registry data for {section}/{entity or 'all'}")
