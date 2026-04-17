"""Config Editor tab — browse and (future) edit config.xbr values."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox

from . import backend


class ConfigEditorTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._build()

    def _build(self):
        # -- WIP banner --
        banner = tk.Frame(self, bg="#CC8800")
        banner.pack(fill=tk.X, padx=10, pady=(10, 0))
        tk.Label(banner, text="\u26A0  This feature is still a work in progress",
                 bg="#CC8800", fg="white", font=("Segoe UI", 9, "bold"),
                 pady=4).pack()

        # -- Section/entity selectors --
        ctrl = ttk.Frame(self)
        ctrl.pack(fill=tk.X, padx=10, pady=(10, 5))

        ttk.Label(ctrl, text="Section:").pack(side=tk.LEFT, padx=(0, 5))
        self._section_var = tk.StringVar()
        sections = backend.list_sections()
        self._section_combo = ttk.Combobox(ctrl, textvariable=self._section_var,
                                           values=sections, state="readonly", width=22)
        self._section_combo.pack(side=tk.LEFT, padx=(0, 10))
        self._section_combo.bind("<<ComboboxSelected>>", self._on_section_change)

        ttk.Label(ctrl, text="Entity:").pack(side=tk.LEFT, padx=(0, 5))
        self._entity_var = tk.StringVar()
        self._entity_combo = ttk.Combobox(ctrl, textvariable=self._entity_var,
                                          values=[], state="readonly", width=22)
        self._entity_combo.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Button(ctrl, text="Load Values", command=self._load_values).pack(side=tk.LEFT)

        # -- Values display --
        self._text = tk.Text(self, height=22, wrap=tk.WORD, state=tk.DISABLED,
                             font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4")
        scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self._text.yview)
        self._text.config(yscrollcommand=scrollbar.set)

        self._text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=5)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 10), pady=5)

        # -- Status --
        self._status = ttk.Label(self, text="Select a section and entity, then click Load Values")
        self._status.pack(fill=tk.X, padx=10, pady=(0, 10))

        # Set default
        if sections:
            self._section_combo.set(sections[0])
            self._on_section_change()

    def _on_section_change(self, event=None):
        section = self._section_var.get()
        if not section:
            return
        entities = backend.list_entities(section)
        self._entity_combo.config(values=entities)
        if entities:
            self._entity_combo.set(entities[0])

    def _load_values(self):
        iso_path = self.app.get_iso_path()
        if not iso_path or not iso_path.exists():
            messagebox.showinfo("Info", "Select a game ISO first to load live values.\n"
                               "Showing registry data instead.")
            self._show_registry_data()
            return

        section = self._section_var.get()
        entity = self._entity_var.get()
        if not section:
            return

        self._status.config(text="Loading...")
        output = backend.run_config_dump(iso_path, section, entity if entity else None)

        self._text.config(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        self._text.insert("1.0", output)
        self._text.config(state=tk.DISABLED)
        self._status.config(text=f"Loaded {section}/{entity or 'all'}")

    def _show_registry_data(self):
        """Show raw registry info without an ISO."""
        section = self._section_var.get()
        entity = self._entity_var.get()
        if not section:
            return

        import json
        registry_path = backend.SCRIPT_DIR / "claude_output" / "config_registry.json"
        if not registry_path.exists():
            return

        with open(registry_path) as f:
            reg = json.load(f)

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
