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

    def __init__(self, parent, label: str = "Output ISO:",
                 default_name: str = "Azurik_randomized.iso"):
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
                             font=("Menlo", 10), bg="#1a1a1a", fg="#d4d4d4",
                             insertbackground="#d4d4d4", borderwidth=0,
                             highlightthickness=0)
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


# ---------------------------------------------------------------------------
# New shell widgets for the sidebar + pages layout
# ---------------------------------------------------------------------------


class Sidebar(ttk.Frame):
    """Vertical nav rail: one button per page, emits `<<PageSelected>>`.

    Each button is a ttk.Button styled as a flat row.  The currently
    selected page gets the "Accent" style (blue highlight).  A click
    triggers `event_generate("<<PageSelected>>", data=page_id)`.
    """

    def __init__(self, parent, pages: list[tuple[str, str]]):
        """pages = [(id, label), ...] in display order."""
        super().__init__(parent, padding=(8, 8))
        self._selected: str | None = None
        self._buttons: dict[str, ttk.Button] = {}

        for page_id, label in pages:
            btn = ttk.Button(
                self, text=label, width=22,
                command=lambda pid=page_id: self._select(pid),
            )
            btn.pack(fill=tk.X, pady=1)
            self._buttons[page_id] = btn

        # Initial selection.
        if pages:
            self._select(pages[0][0], notify=False)

    def _select(self, page_id: str, notify: bool = True) -> None:
        # Restyle previous / new selection.
        if self._selected and self._selected in self._buttons:
            self._buttons[self._selected].configure(style="TButton")
        self._buttons[page_id].configure(style="Accent.TButton")
        self._selected = page_id
        if notify:
            self.event_generate("<<PageSelected>>", when="tail")

    @property
    def selected(self) -> str | None:
        return self._selected

    def select(self, page_id: str) -> None:
        """Programmatically switch pages; emits the event."""
        self._select(page_id)


class ScrollableFrame(ttk.Frame):
    """A ttk.Frame whose content is vertically scrollable.

    Children should be packed / gridded into `self.inner` (NOT self).
    A canvas + vertical scrollbar wraps `self.inner` and the scrollbar
    only shows when the content overflows the visible area.  Mouse-
    wheel scrolling is active only while the cursor is over the frame
    (via <Enter>/<Leave>) so multiple scrollable pages on one root
    don't fight over the global wheel binding.

    Works on macOS (MouseWheel, delta = +/- 1..3), Windows (MouseWheel,
    delta in multiples of 120), and Linux (Button-4 / Button-5).
    """

    def __init__(self, parent: "tk.Widget") -> None:
        super().__init__(parent)

        self._canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self._scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL,
                                        command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._scrollbar.set)

        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.inner = ttk.Frame(self._canvas)
        self._window_id = self._canvas.create_window(
            (0, 0), window=self.inner, anchor="nw")

        # Keep the canvas scroll region in sync with the inner frame.
        self.inner.bind("<Configure>", self._on_inner_configure)
        # Stretch the inner frame to the canvas width so pack(fill=X)
        # behaves as if the content were inside a normal frame.
        self._canvas.bind("<Configure>", self._on_canvas_configure)

        # Mouse wheel bindings — only active while hovering over the frame.
        self.bind("<Enter>", self._bind_wheel)
        self.bind("<Leave>", self._unbind_wheel)

    # --- layout callbacks -----------------------------------------------

    def _on_inner_configure(self, _event) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self._canvas.itemconfigure(self._window_id, width=event.width)

    # --- mouse wheel ----------------------------------------------------

    def _bind_wheel(self, _event=None) -> None:
        # bind_all so wheel events over child widgets bubble up to us.
        self._canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self._canvas.bind_all("<Button-4>", self._on_mousewheel_linux)
        self._canvas.bind_all("<Button-5>", self._on_mousewheel_linux)

    def _unbind_wheel(self, _event=None) -> None:
        self._canvas.unbind_all("<MouseWheel>")
        self._canvas.unbind_all("<Button-4>")
        self._canvas.unbind_all("<Button-5>")

    def _on_mousewheel(self, event) -> None:
        # macOS sends small delta values (+/- 1..3); Windows uses multiples
        # of 120.  Normalise both to "units of one scroll line".
        delta = event.delta
        if abs(delta) >= 120:
            units = -int(delta / 120)
        else:
            units = -int(delta)
        if units:
            self._canvas.yview_scroll(units, "units")

    def _on_mousewheel_linux(self, event) -> None:
        self._canvas.yview_scroll(-1 if event.num == 4 else 1, "units")


class Page(ttk.Frame):
    """Base class for content pages swapped into the main area.

    Sub-classes override `_build()` to lay out their content inside
    `self._body`.  A `title` and optional `description` are rendered
    as a non-scrolling header at the top; everything below the header
    is wrapped in a `ScrollableFrame` so long pages remain reachable
    on short windows.

    Pages that manage their own internal scrolling (e.g. the Entity
    Editor's property grid inside its own Canvas) should set
    ``scrollable_body = False`` to opt out of the outer
    ScrollableFrame — stacking two canvas-based scrollers makes the
    mouse-wheel event fight between both.  In that case ``self._body``
    is a plain ``ttk.Frame`` and ``self._scroll`` is ``None``.
    """

    title: str = "Page"
    description: str = ""
    scrollable_body: bool = True

    def __init__(self, parent, app):
        super().__init__(parent, padding=(16, 12))
        self.app = app

        # Non-scrolling header — stays pinned.
        header = ttk.Frame(self)
        header.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(header, text=self.title,
                  font=("", 16, "bold")).pack(anchor=tk.W)
        if self.description:
            ttk.Label(header, text=self.description,
                      foreground="gray", wraplength=720, justify=tk.LEFT).pack(
                anchor=tk.W, pady=(2, 0))

        # Body — scrollable by default, or a plain Frame if the page
        # manages its own scrolling.
        if self.scrollable_body:
            self._scroll: ScrollableFrame | None = ScrollableFrame(self)
            self._scroll.pack(fill=tk.BOTH, expand=True)
            self._body = self._scroll.inner
        else:
            self._scroll = None
            self._body = ttk.Frame(self)
            self._body.pack(fill=tk.BOTH, expand=True)
        self._build()

    def _build(self) -> None:  # pragma: no cover - overridden by subclasses
        """Subclasses build their content inside `self._body`."""


class Section(ttk.Frame):
    """Collapsible titled section with a chevron toggle.

    Not a real ttk.Labelframe — we build the header ourselves so it can
    expand/collapse cleanly on click.
    """

    def __init__(self, parent, title: str, *, initially_open: bool = True):
        super().__init__(parent)
        self._open = initially_open

        header = ttk.Frame(self)
        header.pack(fill=tk.X)
        self._chevron = ttk.Label(header, text="▾" if initially_open else "▸",
                                  width=2, anchor=tk.W)
        self._chevron.pack(side=tk.LEFT)
        title_label = ttk.Label(header, text=title, font=("", 11, "bold"))
        title_label.pack(side=tk.LEFT)

        # Whole header row is clickable.
        for w in (header, self._chevron, title_label):
            w.bind("<Button-1>", self._toggle)

        self.body = ttk.Frame(self, padding=(18, 6, 6, 6))
        if initially_open:
            self.body.pack(fill=tk.X, pady=(2, 6))

    def _toggle(self, _event=None) -> None:
        if self._open:
            self.body.pack_forget()
            self._chevron.configure(text="▸")
        else:
            self.body.pack(fill=tk.X, pady=(2, 6))
            self._chevron.configure(text="▾")
        self._open = not self._open


class PackBrowser(ttk.Frame):
    """Checkbox list of PatchPack rows, grouped by the pack's first tag.

    Mutates a shared `dict[str, tk.BooleanVar]` passed in at construction
    time, so the Randomize page (or any other consumer) can observe the
    same state.
    """

    def __init__(self, parent, packs: list, pack_vars: dict[str, "tk.BooleanVar"]):
        super().__init__(parent)
        self._vars = pack_vars

        # Group by first tag (fall back to "other").
        groups: dict[str, list] = {}
        for pack in packs:
            tag = pack.tags[0] if pack.tags else "other"
            groups.setdefault(tag, []).append(pack)

        for tag in sorted(groups.keys()):
            grp = groups[tag]
            section = Section(self, title=tag.upper(), initially_open=True)
            section.pack(fill=tk.X, pady=(4, 4))
            for pack in grp:
                self._render_pack_row(section.body, pack)

    def _render_pack_row(self, parent, pack) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=3)

        var = self._vars.setdefault(pack.name, tk.BooleanVar(value=pack.default_on))

        cb_label = f"{pack.name}  ({len(pack.sites)} sites)"
        ttk.Checkbutton(row, text=cb_label, variable=var).pack(anchor=tk.W)

        badge_row = ttk.Frame(row)
        badge_row.pack(anchor=tk.W, padx=(25, 0))
        for tag in pack.tags:
            ttk.Label(badge_row, text=f"[{tag}]", foreground="#4b7bec",
                      font=("", 8)).pack(side=tk.LEFT, padx=(0, 4))
        if any(s.safety_critical for s in pack.sites):
            ttk.Label(badge_row, text="[safety-critical]",
                      foreground="#e67e22", font=("", 8)).pack(
                side=tk.LEFT, padx=(0, 4))

        ttk.Label(row, text=pack.description, foreground="gray",
                  wraplength=620, justify=tk.LEFT).pack(anchor=tk.W, padx=(25, 0))


class PrimaryButton(ttk.Button):
    """Accent-styled primary action button (used for 'Build', 'Apply')."""

    def __init__(self, parent, **kwargs):
        kwargs.setdefault("style", "Accent.TButton")
        super().__init__(parent, **kwargs)


class SecondaryButton(ttk.Button):
    """Neutral-styled secondary button."""

    def __init__(self, parent, **kwargs):
        kwargs.setdefault("style", "TButton")
        super().__init__(parent, **kwargs)


class ParametricSlider(ttk.Frame):
    """Slider + numeric entry + reset button for a ParametricPatch.

    Calls `on_change(value)` whenever the value changes (either via the
    slider or via Return in the entry).  Reset returns the slider to
    the ParametricPatch's `default`.  The widget caps input at the
    patch's `slider_min` / `slider_max` range so callers can pass the
    raw float to `apply_parametric_patch` without extra validation.
    """

    def __init__(self, parent, patch, *,
                 initial=None,
                 on_change=None):
        super().__init__(parent)
        self._patch = patch
        self._on_change = on_change
        self._var = tk.DoubleVar(
            value=initial if initial is not None else patch.default)
        self._entry_var = tk.StringVar(value=f"{self._var.get():g}")
        self._building = False  # suppress re-entrant callbacks

        # Row 1: label + units badge + current value
        head = ttk.Frame(self)
        head.pack(fill=tk.X)
        ttk.Label(head, text=patch.label, font=("", 10, "bold")).pack(
            side=tk.LEFT)
        ttk.Label(head, text=f"({patch.default} {patch.unit} default)",
                  foreground="gray").pack(side=tk.LEFT, padx=(6, 0))

        # Row 2: slider + numeric entry + reset
        row = ttk.Frame(self)
        row.pack(fill=tk.X, pady=(2, 4))
        self._scale = ttk.Scale(
            row,
            orient=tk.HORIZONTAL,
            from_=patch.slider_min,
            to=patch.slider_max,
            variable=self._var,
            command=self._on_scale,
        )
        self._scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self._entry = ttk.Entry(row, textvariable=self._entry_var, width=10)
        self._entry.pack(side=tk.LEFT, padx=(0, 4))
        self._entry.bind("<Return>", self._on_entry_commit)
        self._entry.bind("<FocusOut>", self._on_entry_commit)
        ttk.Label(row, text=patch.unit,
                  foreground="gray").pack(side=tk.LEFT, padx=(0, 8))
        SecondaryButton(row, text="Reset",
                        command=self._reset).pack(side=tk.LEFT)

    # --- public --------------------------------------------------------

    def get_value(self) -> float:
        return float(self._var.get())

    def set_value(self, v: float) -> None:
        v = max(self._patch.slider_min, min(self._patch.slider_max, float(v)))
        self._building = True
        self._var.set(v)
        self._entry_var.set(f"{v:g}")
        self._building = False
        if self._on_change:
            self._on_change(v)

    def is_default(self) -> bool:
        return abs(self.get_value() - self._patch.default) < 1e-6

    # --- internals -----------------------------------------------------

    def _on_scale(self, _new):
        if self._building:
            return
        v = float(self._var.get())
        self._entry_var.set(f"{v:.3f}")
        if self._on_change:
            self._on_change(v)

    def _on_entry_commit(self, _event=None):
        if self._building:
            return
        try:
            v = float(self._entry_var.get())
        except ValueError:
            # Snap back to last valid value
            self._entry_var.set(f"{self._var.get():g}")
            return
        self.set_value(v)

    def _reset(self):
        self.set_value(self._patch.default)
