"""Entity Editor page — browse, edit, and randomize critter / player /
damage config values.

Subclasses `Page` with ``scrollable_body=False`` because the property
grid runs inside its own ``tk.Canvas`` (the list can be hundreds of
rows long and we want it to scroll independently while the top
controls stay fixed).  Enabling the outer ``ScrollableFrame`` on top
of that internal canvas would cause both wheel handlers to fight.
"""

from __future__ import annotations

import json
import random
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path

from .. import backend
from ..widgets import Page

# Sections available for editing, organized by user-facing category.
# format: "keyed" (uses keyed_table_parser) or "variant" (uses config_registry.json)
EDITABLE_SECTIONS = [
    # Keyed sections (8,466+ patchable values)
    ("attacks_transitions", "Entity Stats (speed, range, HP)", "keyed"),
    ("critters_critter_data", "Entity Damage Multipliers", "keyed"),
    ("critters_walking_dmg", "Damage Types (player attacks)", "keyed"),
    ("magic", "Player Global Settings", "keyed_flat"),
    ("armor_properties_real", "Armor Properties", "keyed"),
    # Variant-record sections (corrected to doubles)
    ("critters_walking", "Critters — Movement & AI", "variant"),
    ("critters_flocking", "Critters — Flocking", "variant"),
    ("damage", "Enemy Damage Overrides", "variant"),
]

# Entities to flag as "not gameplay relevant" in critters_walking
NON_GAMEPLAY_ENTITIES = {
    "garret4", "movies", "debug", "test", "good_noreht", "evil_noreht",
    "bird", "fish_big", "fish_little1", "fish_little2", "firefly",
}


class EntityEditorTab(Page):
    title = "Entity Editor"
    description = ("Browse, edit, and randomize critter / player / damage "
                   "config values.  Load values from an ISO, tweak "
                   "properties, then run a build — pending edits are "
                   "merged into the Build tab's --config-mod blob "
                   "automatically (no explicit 'Apply' click needed).  "
                   "Verify a built ISO kept your edits with: "
                   "`azurik-mod dump -i built.iso -s armor_properties_real -e air_shield_3`")
    scrollable_body = False  # internal canvas handles scrolling

    def _build(self):
        # Subclass-specific state (initialised here rather than __init__
        # because Page.__init__ runs _build() before returning).
        self._registry = None
        self._schema = None
        self._keyed_tables = None
        self._config_xbr_path = None
        self._edits: dict[str, dict[str, dict[str, float]]] = {}
        self._value_widgets: dict[str, tk.Variable] = {}
        self._default_values: dict[str, dict[str, dict[str, float]]] = {}

        # -- Top controls --
        ctrl = ttk.Frame(self._body)
        ctrl.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(ctrl, text="Section:").pack(side=tk.LEFT, padx=(0, 5))
        self._section_var = tk.StringVar()
        self._section_combo = ttk.Combobox(
            ctrl, textvariable=self._section_var,
            values=[s[1] for s in EDITABLE_SECTIONS],
            state="readonly", width=30)
        self._section_combo.pack(side=tk.LEFT, padx=(0, 10))
        self._section_combo.bind("<<ComboboxSelected>>", lambda e: self._on_section_change())

        ttk.Label(ctrl, text="Entity:").pack(side=tk.LEFT, padx=(0, 5))
        self._entity_var = tk.StringVar()
        self._entity_combo = ttk.Combobox(
            ctrl, textvariable=self._entity_var,
            values=[], state="readonly", width=24)
        self._entity_combo.pack(side=tk.LEFT, padx=(0, 5))
        self._entity_combo.bind(
            "<<ComboboxSelected>>",
            lambda e: self._on_entity_change())

        # Entity-specific edit indicator (e.g. "(3 edits)") right
        # next to the combobox.  Refreshed on every value-edit /
        # selection change.
        self._entity_edits_label = ttk.Label(ctrl, text="",
                                              foreground="#2a6")
        self._entity_edits_label.pack(side=tk.LEFT, padx=(0, 10))

        # -- Search / filter row --
        search_frame = ttk.Frame(self._body)
        search_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(search_frame, text="Filter entities:").pack(
            side=tk.LEFT, padx=(0, 5))
        self._search_var = tk.StringVar()
        self._search_entry = ttk.Entry(
            search_frame, textvariable=self._search_var, width=30)
        self._search_entry.pack(side=tk.LEFT, padx=(0, 5))
        self._search_var.trace_add(
            "write", lambda *_: self._refresh_entity_list())
        ttk.Button(search_frame, text="Clear",
                   command=lambda: self._search_var.set("")).pack(
            side=tk.LEFT, padx=(0, 10))
        self._filter_status = ttk.Label(search_frame, text="",
                                         foreground="gray")
        self._filter_status.pack(side=tk.LEFT)

        # -- Button row --
        btn_frame = ttk.Frame(self._body)
        btn_frame.pack(fill=tk.X, pady=(0, 5))

        ttk.Button(btn_frame, text="Load from ISO",
                   command=self._load_from_iso).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="Import Mod JSON",
                   command=self._import_mod).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="Export Mod JSON",
                   command=self._export_mod).pack(side=tk.LEFT, padx=(0, 5))
        self._edit_count_label = ttk.Label(
            btn_frame, text="", font=("", 10, "bold"),
            foreground="#2a6")
        self._edit_count_label.pack(side=tk.LEFT, padx=(10, 0))

        ttk.Button(btn_frame, text="Reset All Edits",
                   command=self._reset_edits).pack(side=tk.RIGHT)
        ttk.Button(btn_frame, text="Reset This Entity",
                   command=self._reset_entity).pack(
            side=tk.RIGHT, padx=(0, 5))

        # -- Randomize controls --
        rand_frame = ttk.LabelFrame(self._body, text="Randomize Stats")
        rand_frame.pack(fill=tk.X, pady=(0, 5))

        rand_inner = ttk.Frame(rand_frame)
        rand_inner.pack(fill=tk.X, padx=5, pady=4)

        ttk.Label(rand_inner, text="Range:").pack(side=tk.LEFT, padx=(0, 3))
        self._rand_min_var = tk.IntVar(value=50)
        ttk.Spinbox(rand_inner, from_=1, to=500, textvariable=self._rand_min_var,
                     width=4).pack(side=tk.LEFT)
        ttk.Label(rand_inner, text="% to").pack(side=tk.LEFT, padx=3)
        self._rand_max_var = tk.IntVar(value=150)
        ttk.Spinbox(rand_inner, from_=1, to=500, textvariable=self._rand_max_var,
                     width=4).pack(side=tk.LEFT)
        ttk.Label(rand_inner, text="% of default").pack(side=tk.LEFT, padx=(0, 10))

        ttk.Button(rand_inner, text="Randomize This Entity",
                   command=self._randomize_entity).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(rand_inner, text="Randomize All in Section",
                   command=self._randomize_section).pack(side=tk.LEFT, padx=(0, 5))

        # -- Property editor (scrollable) --
        editor_frame = ttk.Frame(self._body)
        editor_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        canvas = tk.Canvas(editor_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(editor_frame, orient=tk.VERTICAL,
                                   command=canvas.yview)
        self._prop_frame = ttk.Frame(canvas)

        self._prop_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self._prop_frame, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Mouse-wheel handling.  Two bugs in the earlier draft:
        #   (1) ``bind_all("<MouseWheel>", ...)`` fires on EVERY widget
        #       in the app, not just this canvas.  Scrolling on the
        #       Randomize / Patches pages would also drive this
        #       canvas, visibly jitter-scrolling an invisible tab.
        #   (2) ``event.delta / 120`` assumes Windows semantics;
        #       macOS sends delta = ±1..±3 (not multiples of 120) so
        #       every wheel tick scrolled by zero units.  Linux
        #       doesn't use ``<MouseWheel>`` at all — it uses
        #       ``<Button-4>`` / ``<Button-5>``.
        # Fix both by copying the enter/leave gating + delta
        # normalisation pattern from ``widgets.ScrollableFrame``.
        def _yview_wheel(units: int) -> None:
            if not units:
                return
            bbox = canvas.bbox("all")
            if bbox and bbox[3] > canvas.winfo_height():
                canvas.yview_scroll(units, "units")

        def _on_wheel(event):
            delta = event.delta
            if abs(delta) >= 120:
                units = -int(delta / 120)
            else:
                units = -int(delta)
            _yview_wheel(units)

        def _on_wheel_linux(event):
            _yview_wheel(-1 if event.num == 4 else 1)

        def _bind_wheel(_event=None):
            canvas.bind_all("<MouseWheel>", _on_wheel)
            canvas.bind_all("<Button-4>", _on_wheel_linux)
            canvas.bind_all("<Button-5>", _on_wheel_linux)

        def _unbind_wheel(_event=None):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        editor_frame.bind("<Enter>", _bind_wheel)
        editor_frame.bind("<Leave>", _unbind_wheel)

        self._canvas = canvas

        # -- Status --
        self._status = ttk.Label(self._body,
            text="Select a section and entity, then Load from ISO")
        self._status.pack(fill=tk.X, pady=(0, 10))

        # Load data
        self._load_registry()
        if EDITABLE_SECTIONS:
            self._section_combo.set(EDITABLE_SECTIONS[0][1])
            self._on_section_change()

    def _load_registry(self):
        from azurik_mod.config import REGISTRY_PATH as reg_path, SCHEMA_PATH as schema_path
        try:
            with open(reg_path) as f:
                self._registry = json.load(f)
        except Exception:
            self._registry = None
        try:
            with open(schema_path) as f:
                self._schema = json.load(f)
        except Exception:
            self._schema = None

    def _get_section_info(self) -> tuple[str, str, str]:
        """Return (section_key, display_name, format_type) for current selection."""
        display = self._section_var.get()
        for key, disp, fmt in EDITABLE_SECTIONS:
            if disp == display:
                return key, disp, fmt
        return "", "", ""

    def _get_all_entities(self) -> list[str]:
        """Return every entity for the currently-selected section,
        unfiltered.  Returns ``["(all settings)"]`` for keyed_flat
        sections (they don't have a real entity dimension)."""
        section_key, _, fmt = self._get_section_info()
        if not section_key:
            return []
        if fmt == "keyed_flat":
            return ["(all settings)"]
        if fmt == "keyed" and self._keyed_tables and section_key in self._keyed_tables:
            return sorted(self._keyed_tables[section_key].col_names)
        if fmt == "variant" and self._registry:
            return sorted(
                self._registry.get("sections", {})
                .get(section_key, {}).get("entities", {}).keys())
        return []

    def _format_entity_label(self, entity: str) -> str:
        """Prepend an edit-count prefix to entities that have pending
        edits (``* goblin (3)``) so they're visually distinct in the
        dropdown list without losing their raw name."""
        section_key, _, _ = self._get_section_info()
        n = len(self._edits.get(section_key, {}).get(entity, {}))
        if n == 0:
            return entity
        return f"● {entity} ({n})"

    def _unformat_entity_label(self, label: str) -> str:
        """Inverse of :meth:`_format_entity_label` — strip the
        decoration to recover the raw entity name.  A label without
        decoration passes through unchanged."""
        if label.startswith("● "):
            # strip "● <name> (<n>)"
            rest = label[2:]
            if rest.endswith(")") and " (" in rest:
                return rest.rsplit(" (", 1)[0]
            return rest
        return label

    def _on_entity_change(self):
        """Entity combobox selection handler.  Normalises the selected
        label (stripping any edit-indicator prefix) and refreshes the
        property grid + entity-edit count label."""
        raw = self._entity_var.get()
        clean = self._unformat_entity_label(raw)
        if clean != raw:
            # Silently overwrite the decorated label with the raw
            # name so downstream lookups (table.col_names etc.) work.
            self._entity_var.set(clean)
        self._rebuild_property_grid()
        self._refresh_entity_edit_count()

    def _refresh_entity_edit_count(self):
        """Update the per-entity '(N edits)' label next to the combo."""
        section_key, _, _ = self._get_section_info()
        entity = self._entity_var.get()
        n = len(self._edits.get(section_key, {}).get(entity, {}))
        self._entity_edits_label.config(
            text=f"({n} edit{'s' if n != 1 else ''})" if n else "")

    def _refresh_entity_list(self):
        """Re-compute the combobox values from current section + filter.

        Called on every section change AND every keystroke in the
        search box.  Re-applies edit-indicator decorations so
        already-edited entities remain visually distinct after a
        filter narrows the list.
        """
        section_key, _, fmt = self._get_section_info()
        if not section_key:
            return

        all_entities = self._get_all_entities()
        needle = self._search_var.get().strip().lower()
        if fmt == "keyed_flat":
            # No search / decoration for flat-mode single-entry lists.
            self._entity_combo.config(values=all_entities)
            self._filter_status.config(text="")
            if all_entities:
                self._entity_combo.set(all_entities[0])
            return

        if needle:
            filtered = [e for e in all_entities if needle in e.lower()]
        else:
            filtered = all_entities

        decorated = [self._format_entity_label(e) for e in filtered]
        self._entity_combo.config(values=decorated)

        # Preserve selection if still visible; otherwise pick first.
        current = self._unformat_entity_label(self._entity_var.get())
        if current in filtered:
            # Re-select with the potentially-updated decoration.
            self._entity_var.set(self._format_entity_label(current))
        elif filtered:
            self._entity_var.set(self._format_entity_label(filtered[0]))
            self._on_entity_change()
        else:
            self._entity_var.set("")
            for w in self._prop_frame.winfo_children():
                w.destroy()

        # Status: "42 of 512" when filtering, "512 entities" when not.
        if needle:
            self._filter_status.config(
                text=f"{len(filtered)} of {len(all_entities)} match")
        else:
            self._filter_status.config(
                text=f"{len(all_entities)} entities" if all_entities else "")

    def _on_section_change(self):
        section_key, _, fmt = self._get_section_info()
        if not section_key:
            return

        if fmt == "keyed_flat":
            # Flat sections show all settings in one list, no entity dropdown
            self._search_entry.config(state=tk.DISABLED)
            self._refresh_entity_list()
            self._rebuild_property_grid()
            return

        self._search_entry.config(state=tk.NORMAL)
        self._refresh_entity_list()

        # On section change, force-rebuild against the freshly-chosen
        # entity (if any) so the property grid reflects the new section.
        if self._entity_var.get():
            self._rebuild_property_grid()
            self._refresh_entity_edit_count()
        else:
            self._status.config(text="No entities loaded — click 'Load from ISO' first")

    def _build_flat_grid(self, section_key: str):
        """Build a flat property list for sections like magic where each 'entity' is really a setting."""
        if not self._keyed_tables or section_key not in self._keyed_tables:
            self._status.config(text="No data — click 'Load from ISO' first")
            return

        table = self._keyed_tables[section_key]

        # Headers
        for col, (text, width) in enumerate([
            ("Setting", 24), ("Default", 12), ("New Value", 10),
        ]):
            ttk.Label(self._prop_frame, text=text, font=("Segoe UI", 9, "bold"),
                      width=width).grid(row=0, column=col, sticky=tk.W, padx=5, pady=2)

        ttk.Separator(self._prop_frame, orient=tk.HORIZONTAL).grid(
            row=1, column=0, columnspan=3, sticky=tk.EW, pady=2)

        row = 2
        count = 0
        # Each "entity" (column) in magic is a setting name with a "Value" property
        for entity_name, col_idx in table.iter_entities():
            # Get the Value cell for this entity
            cell = table.get_value(entity_name, "Value")
            if not cell:
                # Try row index 1 directly
                typ, val, addr = table.read_cell(col_idx, 1)
            else:
                typ, val, addr = cell

            if typ != "double":
                continue

            default_str = f"{val:.4g}" if val != int(val) or abs(val) > 1e6 else f"{val:.1f}"
            count += 1

            # Cache default
            self._default_values.setdefault(section_key, {}).setdefault(
                entity_name, {})["Value"] = val

            # Pending edit
            edit_val = self._edits.get(section_key, {}).get(entity_name, {}).get("Value")

            ttk.Label(self._prop_frame, text=entity_name, width=24).grid(
                row=row, column=0, sticky=tk.W, padx=5, pady=1)

            ttk.Label(self._prop_frame, text=default_str, width=12,
                      anchor=tk.E).grid(row=row, column=1, padx=5, pady=1)

            var = tk.StringVar(value=str(edit_val) if edit_val is not None else "")
            widget_key = f"{section_key}/{entity_name}/Value"
            self._value_widgets[widget_key] = var

            entry = ttk.Entry(self._prop_frame, textvariable=var, width=12)
            entry.grid(row=row, column=2, padx=5, pady=1)
            if edit_val is not None:
                entry.config(foreground="blue")
            var.trace_add("write", lambda *_, wk=widget_key, v=var: self._on_value_edit(wk, v))

            row += 1

        self._status.config(text=f"{section_key}: {count} settings")

    def _load_from_iso(self):
        iso_path = self.app.get_iso_path()
        if not iso_path or not iso_path.exists():
            messagebox.showerror("Error", "Select a valid game ISO first.")
            return

        self._status.config(text="Extracting config.xbr from ISO...")
        self.update_idletasks()

        config_path = backend.extract_config_xbr(iso_path)
        if not config_path:
            messagebox.showerror("Error", "Failed to extract config.xbr from ISO.")
            return

        self._config_xbr_path = config_path
        self._status.config(text="Loading keyed tables...")
        self.update_idletasks()

        self._keyed_tables = backend.load_keyed_tables(config_path)
        if self._keyed_tables:
            count = sum(t.num_cols for t in self._keyed_tables.values())
            self._status.config(text=f"Loaded {len(self._keyed_tables)} sections, {count} entities")
        else:
            self._status.config(text="Failed to load keyed tables")

        # Also load defaults for variant sections
        self._load_variant_defaults(iso_path)

        # Refresh entity list
        self._on_section_change()

    def _load_variant_defaults(self, iso_path):
        """Load default values for all variant-record sections."""
        for key, _, fmt in EDITABLE_SECTIONS:
            if fmt != "variant":
                continue
            if not self._registry:
                continue
            entities = self._registry.get("sections", {}).get(key, {}).get("entities", {})
            for entity in entities:
                output = backend.run_config_dump(iso_path, key, entity)
                values = {}
                for line in output.splitlines():
                    line = line.strip()
                    if "=" in line and "@" in line:
                        parts = line.split("=", 1)
                        prop_name = parts[0].strip()
                        rest = parts[1].strip()
                        val_str = rest.split("[")[0].strip()
                        try:
                            values[prop_name] = float(val_str)
                        except ValueError:
                            pass
                if values:
                    self._default_values.setdefault(key, {})[entity] = values

    def _rebuild_property_grid(self):
        section_key, _, fmt = self._get_section_info()
        # The dropdown text may carry our edit-indicator decoration
        # (``● goblin (3)``); strip it before using as a lookup key.
        raw = self._entity_var.get()
        entity = self._unformat_entity_label(raw)
        if not section_key:
            return

        for w in self._prop_frame.winfo_children():
            w.destroy()
        self._value_widgets.clear()

        if fmt == "keyed_flat":
            self._build_flat_grid(section_key)
        elif fmt == "keyed":
            if not entity:
                return
            self._build_keyed_grid(section_key, entity)
        else:
            if not entity:
                return
            self._build_variant_grid(section_key, entity)

    def _build_keyed_grid(self, section_key: str, entity: str):
        """Build property grid from keyed table data, showing ALL rows including empty (default)."""
        if not self._keyed_tables or section_key not in self._keyed_tables:
            self._status.config(text="No data — click 'Load from ISO' first")
            return

        table = self._keyed_tables[section_key]
        col_idx = table.entity_index.get(entity)
        if col_idx is None:
            self._status.config(text=f"Entity '{entity}' not found")
            return

        # Get explicitly set values
        props = table.get_entity(entity)

        # Headers
        for col, (text, width) in enumerate([
            ("Property", 24), ("Default", 12), ("New Value", 10), ("Type", 6),
        ]):
            ttk.Label(self._prop_frame, text=text, font=("Segoe UI", 9, "bold"),
                      width=width).grid(row=0, column=col, sticky=tk.W, padx=5, pady=2)

        ttk.Separator(self._prop_frame, orient=tk.HORIZONTAL).grid(
            row=1, column=0, columnspan=4, sticky=tk.EW, pady=2)

        row = 2
        editable_count = 0

        # Iterate ALL rows (properties), not just non-empty ones
        for r_idx, prop_name in enumerate(table.row_names):
            if prop_name == "name":
                continue

            # Read the cell (may be empty, double, or string)
            typ, val, addr = table.read_cell(col_idx, r_idx)

            # Pending edit
            edit_val = self._edits.get(section_key, {}).get(entity, {}).get(prop_name)

            # Default value display
            if typ == "double" and val is not None:
                if val == int(val) and abs(val) < 1e6:
                    default_str = f"{val:.1f}"
                else:
                    default_str = f"{val:.4g}"
                self._default_values.setdefault(section_key, {}).setdefault(entity, {})[prop_name] = val
            elif typ == "string":
                default_str = str(val)[:20]
            elif typ == "empty":
                default_str = "(default)"
            else:
                default_str = "\u2014"

            # Property name — dim for empty/default entries
            name_color = "" if typ == "double" else "gray"
            lbl = ttk.Label(self._prop_frame, text=prop_name, width=24,
                            foreground=name_color)
            lbl.grid(row=row, column=0, sticky=tk.W, padx=5, pady=1)

            ttk.Label(self._prop_frame, text=default_str, width=12,
                      anchor=tk.E, foreground="gray" if typ == "empty" else "").grid(
                row=row, column=1, padx=5, pady=1)

            # All non-string rows are editable (doubles and empties)
            if typ != "string":
                var = tk.StringVar(value=str(edit_val) if edit_val is not None else "")
                widget_key = f"{section_key}/{entity}/{prop_name}"
                self._value_widgets[widget_key] = var

                entry = ttk.Entry(self._prop_frame, textvariable=var, width=12)
                entry.grid(row=row, column=2, padx=5, pady=1)
                if edit_val is not None:
                    entry.config(foreground="blue")
                var.trace_add("write", lambda *_, wk=widget_key, v=var: self._on_value_edit(wk, v))
                editable_count += 1
            else:
                ttk.Label(self._prop_frame, text="(string)", width=10,
                          foreground="gray").grid(row=row, column=2, padx=5, pady=1)

            type_str = typ if typ != "empty" else "default"
            ttk.Label(self._prop_frame, text=type_str, width=6,
                      foreground="gray").grid(row=row, column=3, padx=5, pady=1)

            row += 1

        note = ""
        if entity in NON_GAMEPLAY_ENTITIES and section_key == "critters_walking":
            note = " (non-gameplay entity)"
        set_count = sum(1 for _, (t, _, _) in props.items() if t == "double")
        self._status.config(
            text=f"{section_key}/{entity}: {set_count} set, {editable_count} editable{note}")

    def _build_variant_grid(self, section_key: str, entity: str):
        """Build property grid from variant-record registry data."""
        if not self._registry:
            self._status.config(text="Registry not loaded")
            return

        reg_section = self._registry.get("sections", {}).get(section_key, {})
        reg_entity = reg_section.get("entities", {}).get(entity, {})
        reg_props = reg_entity.get("properties", {})
        defaults = self._default_values.get(section_key, {}).get(entity, {})

        schema_props = []
        if self._schema:
            sec = self._schema.get("sections", {}).get(section_key, {})
            schema_props = sec.get("properties", [])
        schema_by_key = {p["key"]: p for p in schema_props if isinstance(p, dict)}

        # Headers
        for col, (text, width) in enumerate([
            ("Property", 24), ("Default", 12), ("New Value", 10), ("Description", 30),
        ]):
            ttk.Label(self._prop_frame, text=text, font=("Segoe UI", 9, "bold"),
                      width=width).grid(row=0, column=col, sticky=tk.W, padx=5, pady=2)

        ttk.Separator(self._prop_frame, orient=tk.HORIZONTAL).grid(
            row=1, column=0, columnspan=4, sticky=tk.EW, pady=2)

        prop_order = []
        for prop_key in reg_props:
            sp = schema_by_key.get(prop_key, {})
            prop_order.append((sp.get("index", 999), prop_key))
        prop_order.sort()

        row = 2
        for _, prop_key in prop_order:
            prop_data = reg_props[prop_key]
            sp = schema_by_key.get(prop_key, {})
            display_name = sp.get("display", prop_key)
            description = sp.get("description", "")
            type_flag = prop_data.get("type_flag", 0)

            default_val = defaults.get(prop_key)
            if default_val is not None:
                default_str = f"{default_val:.4g}" if type_flag != 2 else str(int(default_val))
            else:
                default_str = "\u2014"

            edit_val = self._edits.get(section_key, {}).get(entity, {}).get(prop_key)

            ttk.Label(self._prop_frame, text=display_name, width=24).grid(
                row=row, column=0, sticky=tk.W, padx=5, pady=1)

            ttk.Label(self._prop_frame, text=default_str, width=12,
                      anchor=tk.E).grid(row=row, column=1, padx=5, pady=1)

            var = tk.StringVar(value=str(edit_val) if edit_val is not None else "")
            widget_key = f"{section_key}/{entity}/{prop_key}"
            self._value_widgets[widget_key] = var

            entry = ttk.Entry(self._prop_frame, textvariable=var, width=12)
            entry.grid(row=row, column=2, padx=5, pady=1)
            if edit_val is not None:
                entry.config(foreground="blue")
            var.trace_add("write", lambda *_, wk=widget_key, v=var: self._on_value_edit(wk, v))

            ttk.Label(self._prop_frame, text=description[:50], wraplength=250,
                      foreground="gray", font=("Segoe UI", 8)).grid(
                row=row, column=3, sticky=tk.W, padx=5, pady=1)

            row += 1

        note = ""
        if entity in NON_GAMEPLAY_ENTITIES:
            note = " (non-gameplay entity)"
        self._status.config(
            text=f"{section_key}/{entity}: {len(reg_props)} properties{note}")

    def _on_value_edit(self, widget_key: str, var: tk.Variable):
        parts = widget_key.split("/", 2)
        if len(parts) != 3:
            return
        section, entity, prop = parts
        val_str = var.get().strip()
        if not val_str:
            if section in self._edits and entity in self._edits[section]:
                self._edits[section][entity].pop(prop, None)
                if not self._edits[section][entity]:
                    del self._edits[section][entity]
                if not self._edits[section]:
                    del self._edits[section]
        else:
            try:
                val = float(val_str)
                self._edits.setdefault(section, {}).setdefault(entity, {})[prop] = val
            except ValueError:
                pass
        self._update_edit_count()

    def _update_edit_count(self):
        total = sum(len(p) for e in self._edits.values() for p in e.values())
        # Count distinct (section, entity) pairs that carry edits.
        n_entities = sum(len(ents) for ents in self._edits.values())
        n_sections = len(self._edits)
        if total:
            breakdown = (
                f"{total} edit(s) pending across "
                f"{n_entities} entit{'y' if n_entities == 1 else 'ies'} / "
                f"{n_sections} section{'s' if n_sections != 1 else ''}"
            )
        else:
            breakdown = ""
        self._edit_count_label.config(text=breakdown)

        # Refresh the entity-combo decorations so newly-edited items
        # gain / lose their bullet marker.  Cheap — a few-dozen item
        # dropdown at worst.
        if hasattr(self, "_search_entry"):
            self._refresh_entity_list()
        self._refresh_entity_edit_count()

    def _ensure_defaults(self, section_key: str, entity: str) -> dict[str, float]:
        defaults = self._default_values.get(section_key, {}).get(entity, {})
        if not defaults:
            if self._keyed_tables and section_key in self._keyed_tables:
                table = self._keyed_tables[section_key]
                props = table.get_entity(entity)
                for pname, (typ, val, _) in props.items():
                    if typ == "double" and val is not None and pname != "name":
                        self._default_values.setdefault(section_key, {}).setdefault(entity, {})[pname] = val
                defaults = self._default_values.get(section_key, {}).get(entity, {})
        return defaults

    def _randomize_entity(self):
        section_key, _, _ = self._get_section_info()
        entity = self._unformat_entity_label(self._entity_var.get())
        if not section_key or not entity:
            return
        defaults = self._ensure_defaults(section_key, entity)
        if not defaults:
            messagebox.showerror("Error", "Load defaults from ISO first.")
            return
        self._apply_random_to_entity(section_key, entity, defaults)
        self._rebuild_property_grid()
        self._update_edit_count()

    def _randomize_section(self):
        section_key, _, fmt = self._get_section_info()
        if not section_key:
            return

        if fmt in ("keyed", "keyed_flat") and self._keyed_tables and section_key in self._keyed_tables:
            entities = list(self._keyed_tables[section_key].col_names)
        elif fmt == "variant" and self._registry:
            entities = list(
                self._registry.get("sections", {}).get(section_key, {}).get("entities", {}).keys())
        else:
            messagebox.showerror("Error", "Load defaults from ISO first.")
            return

        self._status.config(text=f"Randomizing {len(entities)} entities...")
        self.update_idletasks()

        count = 0
        for entity in entities:
            if entity in NON_GAMEPLAY_ENTITIES and section_key == "critters_walking":
                continue
            defaults = self._ensure_defaults(section_key, entity)
            if defaults:
                count += self._apply_random_to_entity(section_key, entity, defaults)

        self._rebuild_property_grid()
        self._update_edit_count()
        self._status.config(text=f"Randomized {count} properties across {len(entities)} entities")

    def _apply_random_to_entity(self, section_key, entity, defaults) -> int:
        try:
            min_pct = self._rand_min_var.get() / 100.0
            max_pct = self._rand_max_var.get() / 100.0
        except (tk.TclError, ValueError):
            min_pct, max_pct = 0.5, 1.5
        if min_pct > max_pct:
            min_pct, max_pct = max_pct, min_pct

        count = 0
        for prop_key, default_val in defaults.items():
            if default_val == 0.0:
                continue
            new_val = default_val * random.uniform(min_pct, max_pct)
            self._edits.setdefault(section_key, {}).setdefault(entity, {})[prop_key] = round(new_val, 4)
            count += 1
        return count

    def get_pending_mod(self) -> dict | None:
        """Return pending edits as a grouped mod dict for the randomizer pipeline."""
        if not self._edits:
            return None
        # Separate variant edits (go through config_registry) from keyed edits
        mod = {"name": "Entity Editor Edits", "format": "grouped", "sections": {}}
        keyed_patches = {}

        for section_key, entities in self._edits.items():
            _, _, fmt = next(
                ((k, d, f) for k, d, f in EDITABLE_SECTIONS if k == section_key),
                ("", "", "variant"))
            if fmt == "variant":
                mod_section = {}
                for entity, props in entities.items():
                    mod_section[entity] = dict(props)
                mod["sections"][section_key] = mod_section
            else:
                # Keyed edits need cell offsets — store separately
                keyed_patches[section_key] = {}
                for entity, props in entities.items():
                    keyed_patches[section_key][entity] = dict(props)

        if keyed_patches:
            mod["_keyed_patches"] = keyed_patches

        return mod if (mod["sections"] or mod.get("_keyed_patches")) else None

    def get_edit_count(self) -> int:
        return sum(len(p) for e in self._edits.values() for p in e.values())

    def _reset_edits(self):
        if self._edits and not messagebox.askyesno(
                "Reset all edits?",
                f"Discard all {self.get_edit_count()} pending edit(s)?  "
                f"This can't be undone."):
            return
        self._edits.clear()
        self._rebuild_property_grid()
        self._update_edit_count()

    def _reset_entity(self):
        """Clear pending edits for the currently-selected entity only.

        Useful when a user experimented on one creature, decided they
        don't like the tweaks, but wants to keep edits on every other
        creature.  No-op + informational message if the entity has
        no edits to reset.
        """
        section_key, _, _ = self._get_section_info()
        entity = self._entity_var.get()
        if not section_key or not entity:
            return
        ent_edits = self._edits.get(section_key, {}).get(entity)
        if not ent_edits:
            self._status.config(
                text=f"{section_key}/{entity} has no pending edits to reset.")
            return
        n = len(ent_edits)
        if not messagebox.askyesno(
                "Reset this entity?",
                f"Discard {n} pending edit(s) on {section_key}/{entity}?"):
            return
        del self._edits[section_key][entity]
        if not self._edits[section_key]:
            del self._edits[section_key]
        self._rebuild_property_grid()
        self._update_edit_count()
        self._status.config(
            text=f"Reset {n} edit(s) on {section_key}/{entity}")

    def _import_mod(self):
        """Load a previously-exported Mod JSON back into the editor.

        Merges the file's edits into the current in-memory buffer —
        does NOT replace them, so a user can combine a saved preset
        with fresh interactive tweaks.  Format auto-detected: both
        the grouped-section shape (``sections: {key: {entity: {prop:
        value}}}``) and the ``_keyed_patches`` shape are accepted
        (the same file can carry both — matches what Export emits).
        """
        path = filedialog.askopenfilename(
            title="Import Mod JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path) as f:
                mod = json.load(f)
        except Exception as e:
            messagebox.showerror(
                "Import failed",
                f"Could not parse {Path(path).name}:\n{e}")
            return

        # Expected shape: {"sections": {<key>: {<entity>: {<prop>: value}}}}.
        imported = 0
        skipped = 0

        # 1. Grouped "sections" (variant-record format).
        for sec_key, entities in mod.get("sections", {}).items():
            if not isinstance(entities, dict):
                skipped += 1; continue
            for entity, props in entities.items():
                if not isinstance(props, dict):
                    skipped += 1; continue
                for prop_key, value in props.items():
                    try:
                        f_val = float(value)
                    except (TypeError, ValueError):
                        skipped += 1; continue
                    self._edits.setdefault(sec_key, {}).setdefault(
                        entity, {})[prop_key] = f_val
                    imported += 1

        # 2. Keyed patches (top-level _keyed_patches).
        for sec_key, entities in mod.get("_keyed_patches", {}).items():
            if not isinstance(entities, dict):
                skipped += 1; continue
            for entity, props in entities.items():
                if not isinstance(props, dict):
                    skipped += 1; continue
                for prop_key, value in props.items():
                    try:
                        f_val = float(value)
                    except (TypeError, ValueError):
                        skipped += 1; continue
                    self._edits.setdefault(sec_key, {}).setdefault(
                        entity, {})[prop_key] = f_val
                    imported += 1

        self._rebuild_property_grid()
        self._update_edit_count()
        summary = f"Imported {imported} edit(s) from {Path(path).name}"
        if skipped:
            summary += f" ({skipped} malformed entr{'ies' if skipped != 1 else 'y'} skipped)"
        self._status.config(text=summary)

    def _export_mod(self):
        if not self._edits:
            messagebox.showinfo("No Edits", "No property edits to export.")
            return

        # Build a combined export with both variant and keyed edits
        export = {"name": "Entity Editor Export", "format": "grouped", "sections": {}}

        # For keyed sections, include cell offsets for direct patching
        keyed_offsets = {}

        for section_key, entities in self._edits.items():
            _, _, fmt = next(
                ((k, d, f) for k, d, f in EDITABLE_SECTIONS if k == section_key),
                ("", "", "variant"))

            mod_section = {}
            for entity, props in entities.items():
                mod_entity = {}
                for prop_key, value in props.items():
                    mod_entity[prop_key] = value
                    # For keyed sections, also record the cell offset
                    if fmt == "keyed" and self._keyed_tables and section_key in self._keyed_tables:
                        table = self._keyed_tables[section_key]
                        cell = table.get_value(entity, prop_key)
                        if cell:
                            keyed_offsets.setdefault(section_key, {}).setdefault(
                                entity, {})[prop_key] = {
                                "value": value,
                                "cell_offset": f"0x{cell[2]:06X}",
                                "format": "keyed_double"
                            }
                mod_section[entity] = mod_entity
            export["sections"][section_key] = mod_section

        if keyed_offsets:
            export["_keyed_cell_offsets"] = keyed_offsets

        path = filedialog.asksaveasfilename(
            title="Save Mod JSON",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile="entity_edits.json",
        )
        if not path:
            return

        with open(path, "w") as f:
            json.dump(export, f, indent=2)

        total = sum(len(p) for e in self._edits.values() for p in e.values())
        self._status.config(text=f"Exported {total} edits to {Path(path).name}")
