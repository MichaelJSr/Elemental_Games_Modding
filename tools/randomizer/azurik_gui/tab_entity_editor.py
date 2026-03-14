"""Entity Editor tab — browse and (future) edit entities across levels."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import backend


CATEGORY_DISPLAY = {
    "fragments": "Disk Fragments",
    "keys": "Keys",
    "gems": "Gems",
    "powers": "Powers",
    "fuel": "Fuel",
}


def _flatten_pickups(data: dict, category: str) -> list[dict]:
    """Flatten the nested all_pickups.json structure into a list of row dicts."""
    section = data.get(category, {})
    rows = []

    if category == "fragments":
        # element -> entity_name -> {level, region, position, ...}
        for element, entities in section.items():
            if element.startswith("_"):
                continue
            if not isinstance(entities, dict):
                continue
            for entity_name, info in entities.items():
                if not isinstance(info, dict):
                    continue
                pos = info.get("position") or [None, None, None]
                rows.append({
                    "entity": entity_name,
                    "level": info.get("level", ""),
                    "region": info.get("region", element),
                    "x": pos[0] if isinstance(pos, list) and len(pos) > 0 else "",
                    "y": pos[1] if isinstance(pos, list) and len(pos) > 1 else "",
                    "z": pos[2] if isinstance(pos, list) and len(pos) > 2 else "",
                    "notes": info.get("notes", ""),
                })

    elif category == "gems":
        # level -> {region, gem_count, entities: {instance_id -> {type, position, ...}}}
        for level, level_data in section.items():
            if level.startswith("_"):
                continue
            if not isinstance(level_data, dict):
                continue
            region = level_data.get("region", "")
            entities = level_data.get("entities", {})
            for instance_id, info in entities.items():
                if not isinstance(info, dict):
                    continue
                pos = info.get("position") or [None, None, None]
                rows.append({
                    "entity": instance_id,
                    "level": level,
                    "region": region,
                    "x": pos[0] if isinstance(pos, list) and len(pos) > 0 else "",
                    "y": pos[1] if isinstance(pos, list) and len(pos) > 1 else "",
                    "z": pos[2] if isinstance(pos, list) and len(pos) > 2 else "",
                    "notes": info.get("type", "") + (" " + info.get("variant", "")).rstrip(),
                })

    elif category in ("keys", "powers", "fuel"):
        # level -> entity_name -> {position, category/element, notes, ...}
        for level, level_data in section.items():
            if level.startswith("_"):
                continue
            if not isinstance(level_data, dict):
                continue
            for entity_name, info in level_data.items():
                if not isinstance(info, dict):
                    continue
                pos = info.get("position") or [None, None, None]
                note_parts = []
                if info.get("category"):
                    note_parts.append(info["category"])
                if info.get("element"):
                    note_parts.append(info["element"])
                if info.get("notes"):
                    note_parts.append(info["notes"])
                rows.append({
                    "entity": entity_name,
                    "level": level,
                    "region": info.get("region", ""),
                    "x": pos[0] if isinstance(pos, list) and len(pos) > 0 else "",
                    "y": pos[1] if isinstance(pos, list) and len(pos) > 1 else "",
                    "z": pos[2] if isinstance(pos, list) and len(pos) > 2 else "",
                    "notes": " / ".join(note_parts),
                })

    return rows


class EntityEditorTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._pickups = None
        self._build()

    def _build(self):
        # -- Top controls --
        ctrl = ttk.Frame(self)
        ctrl.pack(fill=tk.X, padx=10, pady=(10, 5))

        ttk.Label(ctrl, text="Category:").pack(side=tk.LEFT, padx=(0, 5))
        self._cat_var = tk.StringVar(value="fragments")
        cat_combo = ttk.Combobox(ctrl, textvariable=self._cat_var,
                                 values=list(CATEGORY_DISPLAY.keys()),
                                 state="readonly", width=15)
        cat_combo.pack(side=tk.LEFT, padx=(0, 10))
        cat_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh())

        ttk.Label(ctrl, text="Filter:").pack(side=tk.LEFT, padx=(0, 5))
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", lambda *_: self._refresh())
        ttk.Entry(ctrl, textvariable=self._filter_var, width=20).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Button(ctrl, text="Reload", command=self._load_data).pack(side=tk.LEFT)

        # -- Treeview --
        cols = ("level", "region", "entity", "x", "y", "z", "notes")
        self._tree = ttk.Treeview(self, columns=cols, show="headings", height=18)
        self._tree.heading("level", text="Level")
        self._tree.heading("region", text="Region")
        self._tree.heading("entity", text="Entity Name")
        self._tree.heading("x", text="X")
        self._tree.heading("y", text="Y")
        self._tree.heading("z", text="Z")
        self._tree.heading("notes", text="Notes")

        self._tree.column("level", width=60, anchor=tk.CENTER)
        self._tree.column("region", width=60, anchor=tk.CENTER)
        self._tree.column("entity", width=160)
        self._tree.column("x", width=80, anchor=tk.E)
        self._tree.column("y", width=80, anchor=tk.E)
        self._tree.column("z", width=80, anchor=tk.E)
        self._tree.column("notes", width=150)

        scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.config(yscrollcommand=scrollbar.set)

        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=5)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 10), pady=5)

        # -- Status bar --
        self._status = ttk.Label(self, text="Click Reload to load pickup data")
        self._status.pack(fill=tk.X, padx=10, pady=(0, 10))

        # Auto-load
        self._load_data()

    def _load_data(self):
        self._pickups = backend.load_all_pickups()
        if self._pickups:
            self._status.config(text="Pickup data loaded")
            self._refresh()
        else:
            self._status.config(text="No pickup data found (claude_output/all_pickups.json)")

    def _refresh(self):
        self._tree.delete(*self._tree.get_children())
        if not self._pickups:
            return

        category = self._cat_var.get()
        filter_text = self._filter_var.get().lower()

        rows = _flatten_pickups(self._pickups, category)
        count = 0
        for row in rows:
            entity = row.get("entity", "")
            level = row.get("level", "") or ""
            region = row.get("region", "") or ""
            x = row.get("x", "")
            y = row.get("y", "")
            z = row.get("z", "")
            notes = row.get("notes", "")

            # Apply filter
            if filter_text:
                searchable = f"{entity} {level} {region} {notes}".lower()
                if filter_text not in searchable:
                    continue

            def fmt(v):
                if v is None or v == "":
                    return ""
                if isinstance(v, (int, float)):
                    return f"{v:.1f}"
                return str(v)

            self._tree.insert("", tk.END, values=(
                str(level), str(region), str(entity),
                fmt(x), fmt(y), fmt(z), str(notes),
            ))
            count += 1

        display_name = CATEGORY_DISPLAY.get(category, category)
        self._status.config(text=f"{display_name}: {count} items")
