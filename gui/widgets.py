"""Reusable widgets for the Azurik GUI."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path
from typing import Callable


# ---------------------------------------------------------------------------
# Tooltip — lightweight hover popup
# ---------------------------------------------------------------------------
#
# Tkinter has no built-in tooltip widget.  This small helper attaches a
# hover-reveal popup to any widget — bound to the ``<Enter>`` and
# ``<Leave>`` events of the widget itself.  We use it to move long
# slider / pack / category descriptions out of the always-visible
# layout and into an on-demand popup attached to a small ``ⓘ`` glyph.
#
# The ``hide_delay_ms`` keeps the popup visible briefly after the
# cursor leaves the trigger widget so users can move into the popup
# without it disappearing mid-read.  Text is wrapped at the given
# ``wraplength`` to keep long descriptions readable.

_TOOLTIP_BG = "#1f1f1f"
_TOOLTIP_FG = "#e5e5e5"
_TOOLTIP_HIDE_DELAY_MS = 80  # grace period after <Leave>


class Tooltip:
    """Hover-reveal tooltip bound to ``widget``.

    Usage::

        info = ttk.Label(parent, text="\u24d8", cursor="question_arrow")
        info.pack(...)
        Tooltip(info, text="The long description to show on hover.")

    The tooltip is a top-level window containing a single
    word-wrapped label.  It positions itself below-right of the
    trigger widget's bounding box.  Survives the trigger widget
    being destroyed (the popup cleans up in that case too).

    Attributes:
        widget:       the trigger widget.
        text:         the tooltip's current text.  Mutate via
                      :meth:`set_text` so the popup re-renders if
                      it's open.
        wraplength:   pixel width at which lines wrap (default
                      360 — roughly 45-55 characters per line).
    """

    def __init__(
        self,
        widget: tk.Widget,
        text: str,
        *,
        wraplength: int = 360,
        delay_ms: int = 250,
    ) -> None:
        self.widget = widget
        self._text = text
        self.wraplength = wraplength
        self.delay_ms = delay_ms
        self._tip: tk.Toplevel | None = None
        self._show_after: str | None = None
        self._hide_after: str | None = None
        widget.bind("<Enter>", self._schedule_show, add="+")
        widget.bind("<Leave>", self._schedule_hide, add="+")
        widget.bind("<ButtonPress>", self._schedule_hide, add="+")
        widget.bind("<Destroy>", self._on_destroy, add="+")

    # ---- Public API ----

    def set_text(self, text: str) -> None:
        """Update the tooltip text.  Re-renders if currently open."""
        self._text = text
        if self._tip is not None:
            for child in self._tip.winfo_children():
                child.destroy()
            self._populate(self._tip)

    # ---- Scheduling ----

    def _schedule_show(self, _event=None) -> None:
        self._cancel_hide()
        if self._tip is not None or not self._text.strip():
            return
        if self._show_after is not None:
            self.widget.after_cancel(self._show_after)
        self._show_after = self.widget.after(self.delay_ms, self._show)

    def _schedule_hide(self, _event=None) -> None:
        if self._show_after is not None:
            self.widget.after_cancel(self._show_after)
            self._show_after = None
        if self._hide_after is not None:
            return
        self._hide_after = self.widget.after(
            _TOOLTIP_HIDE_DELAY_MS, self._hide)

    def _cancel_hide(self) -> None:
        if self._hide_after is not None:
            self.widget.after_cancel(self._hide_after)
            self._hide_after = None

    # ---- Window lifecycle ----

    def _show(self) -> None:
        self._show_after = None
        if self._tip is not None:
            return
        try:
            # Anchor: bottom-right of the trigger widget, offset a bit
            # so it doesn't overlap the cursor's default position.
            x = self.widget.winfo_rootx() + self.widget.winfo_width() - 8
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        except tk.TclError:
            # Widget destroyed between Enter and the scheduled _show.
            return
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        try:
            self._tip.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        self._populate(self._tip)

    def _populate(self, parent: tk.Toplevel) -> None:
        frame = tk.Frame(
            parent,
            background=_TOOLTIP_BG,
            highlightthickness=1,
            highlightbackground="#3a3a3a",
            bd=0,
        )
        frame.pack()
        tk.Label(
            frame,
            text=self._text.strip(),
            background=_TOOLTIP_BG,
            foreground=_TOOLTIP_FG,
            wraplength=self.wraplength,
            justify=tk.LEFT,
            padx=8,
            pady=6,
        ).pack()

    def _hide(self) -> None:
        self._hide_after = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None

    def _on_destroy(self, _event=None) -> None:
        self._hide()


def attach_info_tooltip(
    parent: tk.Widget,
    text: str,
    *,
    glyph: str = "\u24d8",  # ⓘ — circled Latin small letter i
) -> tk.Widget | None:
    """Return a small ``ⓘ`` label packed into ``parent`` with a
    hover tooltip showing ``text``.

    Returns ``None`` when ``text`` is empty/whitespace — caller
    can skip packing any info-button row in that case.  The
    returned label is unpacked (caller decides layout) — use
    ``.pack()`` / ``.grid()`` as usual.

    Dedicated helper so every tab / pack / slider uses the
    identical ⓘ glyph + foreground colour + cursor + tooltip
    appearance without hand-wiring each call site.
    """
    if not text or not text.strip():
        return None
    lbl = tk.Label(
        parent,
        text=glyph,
        foreground="#6fb3ff",
        cursor="question_arrow",
    )
    Tooltip(lbl, text)
    return lbl


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
    """Category-based notebook of :class:`PatchPack` rows.

    Renders every category registered in
    :mod:`azurik_mod.patching.category` as its own tab (ordered by
    :attr:`Category.order`).  Empty categories are hidden so the tab
    strip never shows a tab with zero packs.

    Any pack that exposes :class:`ParametricPatch` sites gets an
    inline slider block directly under its checkbox, within the same
    tab — no more hunting for sliders in a separate "Parametric
    sliders" section at the bottom of the page.

    Parameters
    ----------
    parent: tk.Widget
        Container widget.
    packs: list
        Packs to render.  Typically
        ``azurik_mod.patching.registry.all_packs()``.
    pack_vars: dict[str, tk.BooleanVar]
        Shared dict the browser mutates — other consumers (e.g. the
        Randomize page) can observe the same state by passing the
        same dict.
    pack_params: dict | None
        Optional ``{pack_name: {param_name: value}}`` mapping that
        backs every ParametricSlider in the notebook.  The browser
        mirrors slider writes back into this dict via ``on_param_change``.
        Pass ``None`` (the default) to disable parametric rendering.
    on_param_change: callable | None
        Optional ``(pack_name, param_name, new_value) -> None`` hook
        called on every slider drag.  Use it to mirror changes into
        the app state / event bus.

    The class intentionally keeps a narrow, dependency-free public
    surface so headless tests can instantiate it inside a throwaway
    ``tk.Tk()`` and assert on ``tabs()``, ``get()``, etc.
    """

    def __init__(self, parent, packs: list,
                 pack_vars: dict[str, "tk.BooleanVar"],
                 pack_params: dict | None = None,
                 on_param_change: Callable[[str, str, float], None] | None
                     = None) -> None:
        super().__init__(parent)
        self._vars = pack_vars
        self._pack_params = pack_params
        self._on_param_change = on_param_change
        # One ParametricSlider per (pack, param) — exposed for tests.
        self._sliders: dict[tuple[str, str], "ParametricSlider"] = {}
        # id → ttk.Frame for every rendered tab, in insertion order.
        self._tabs: dict[str, ttk.Frame] = {}

        # Local imports so ``gui/widgets.py`` imports clean on a
        # bare tkinter install without the Azurik runtime loaded.
        from azurik_mod.patching.category import all_categories
        from azurik_mod.patching.registry import packs_by_category

        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill=tk.BOTH, expand=True)

        # Keep explicit references to pack lists by category so the
        # input ``packs`` argument (which may be a filtered subset)
        # overrides the registry's global view.  ``deprecated`` packs
        # are filtered out here — they stay in the registry for CLI /
        # test / direct-apply use, but the GUI browser hides them so
        # casual users don't stumble into a checkbox that's known to
        # not produce the expected in-game effect.
        requested = {p.name for p in packs
                     if not getattr(p, "deprecated", False)}
        grouped = packs_by_category()

        for cat in all_categories():
            cat_packs = [p for p in grouped.get(cat.id, [])
                         if p.name in requested]
            if not cat_packs:
                continue  # hide empty tabs
            tab = self._build_tab(cat, cat_packs)
            self._notebook.add(tab, text=cat.title)
            self._tabs[cat.id] = tab

        # Packs whose category id isn't registered (should be
        # impossible after register_pack's ensure_category, but
        # handle gracefully anyway).
        known = {c.id for c in all_categories()}
        stragglers = [p for p in packs
                      if p.category not in known
                      and not getattr(p, "deprecated", False)]
        if stragglers:
            from azurik_mod.patching.category import Category
            orphan = Category("other", "Other",
                              "Uncategorised", 10_000)
            tab = self._build_tab(orphan, stragglers)
            self._notebook.add(tab, text=orphan.title)
            self._tabs[orphan.id] = tab

    # ---- rendering helpers -------------------------------------------

    def _build_tab(self, category, packs: list) -> ttk.Frame:
        """Render the content of one tab (Category header + pack rows)."""
        tab = ttk.Frame(self._notebook, padding=(12, 8))
        if category.description:
            ttk.Label(tab, text=category.description,
                      foreground="gray", wraplength=680,
                      justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 8))
        for pack in packs:
            self._render_pack_row(tab, pack)
        return tab

    def _render_pack_row(self, parent, pack) -> None:
        """Render one pack: checkbox + tag badges + description +
        (optional) parametric sliders."""
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=(4, 8))

        var = self._vars.setdefault(
            pack.name, tk.BooleanVar(value=pack.default_on))

        cb_label = f"{pack.name}  ({len(pack.sites)} sites)"
        ttk.Checkbutton(row, text=cb_label, variable=var).pack(anchor=tk.W)

        if pack.tags or any(
                getattr(s, "safety_critical", False) for s in pack.sites):
            badge_row = ttk.Frame(row)
            badge_row.pack(anchor=tk.W, padx=(25, 0))
            for tag in pack.tags:
                ttk.Label(badge_row, text=f"[{tag}]",
                          foreground="#4b7bec",
                          font=("", 8)).pack(side=tk.LEFT, padx=(0, 4))
            if any(getattr(s, "safety_critical", False)
                   for s in pack.sites):
                ttk.Label(badge_row, text="[safety-critical]",
                          foreground="#e67e22", font=("", 8)).pack(
                    side=tk.LEFT, padx=(0, 4))

        ttk.Label(row, text=pack.description, foreground="gray",
                  wraplength=620, justify=tk.LEFT).pack(
                      anchor=tk.W, padx=(25, 0))

        # Parametric sliders live right under the pack so the spatial
        # relationship is obvious — no more separate bottom section.
        if self._pack_params is not None and pack.parametric_sites():
            slider_host = ttk.Frame(row)
            slider_host.pack(anchor=tk.W, padx=(25, 0),
                             pady=(4, 0), fill=tk.X)
            self._pack_params.setdefault(pack.name, {})
            for pp in pack.parametric_sites():
                initial = self._pack_params[pack.name].get(
                    pp.name, pp.default)
                self._pack_params[pack.name][pp.name] = initial

                def _make_callback(pn=pack.name, param=pp.name):
                    def on_change(value):
                        if self._pack_params is not None:
                            self._pack_params[pn][param] = value
                        if self._on_param_change is not None:
                            self._on_param_change(pn, param, value)
                    return on_change

                slider = ParametricSlider(
                    slider_host, pp,
                    initial=initial,
                    on_change=_make_callback(),
                )
                slider.pack(fill=tk.X, pady=(2, 4))
                self._sliders[(pack.name, pp.name)] = slider

    # ---- test / introspection surface --------------------------------

    def tabs(self) -> list[str]:
        """Return category ids of currently-rendered tabs, in order.

        Used by regression tests + the Randomize page to enumerate
        what was built without poking at Tk internals.
        """
        return list(self._tabs.keys())

    def tab_titles(self) -> list[str]:
        """Return the tab strip's user-facing labels."""
        return [self._notebook.tab(i, "text")
                for i in range(self._notebook.index("end"))]

    def select(self, category_id: str) -> None:
        """Raise the tab for ``category_id``; no-op if not rendered."""
        tab = self._tabs.get(category_id)
        if tab is not None:
            self._notebook.select(tab)

    def sliders(self) -> dict[tuple[str, str], "ParametricSlider"]:
        """Return a copy of the (pack, param) → ParametricSlider map."""
        return dict(self._sliders)


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

    Two bidirectional inputs:
    - `ttk.Scale` for quick visual tuning across the slider range.
      The slider range is strictly ``[slider_min, slider_max]``;
      the slider thumb clamps at those bounds.
    - `ttk.Entry` for typing an **arbitrary** numeric value — the
      entry box accepts values *outside* the slider's declared
      range so power users can dial in extreme values (e.g.
      ``gravity = 200.0``, ``walk_scale = 100.0``) that the slider
      physically can't represent.  When an out-of-range value is
      committed, the entry shows a ``[!]`` badge to communicate
      that the slider thumb no longer reflects the true value.

    Both controls share state via an internal "exact value" that
    is the source of truth — ``get_value()`` returns the exact
    value regardless of slider bounds.  When the slider thumb is
    at a position, the exact value equals the slider's numeric
    position.  When the user types an out-of-range number, the
    slider thumb sits at ``slider_min`` or ``slider_max`` (whichever
    is closer), but ``get_value()`` still returns the typed value.

    ``on_change(value)`` fires on every change.  Reset returns to
    the patch's ``default``.
    """

    def __init__(self, parent, patch, *,
                 initial=None,
                 on_change=None):
        super().__init__(parent)
        self._patch = patch
        self._on_change = on_change
        initial_value = (initial if initial is not None
                         else float(patch.default))
        # Source of truth: the exact value the user wants, WITHOUT
        # slider-bound clamping.  Callers read this via get_value().
        self._exact_value = float(initial_value)
        # Slider-backing var: tracks the slider's visual thumb
        # position, clamped to [slider_min, slider_max].
        self._var = tk.DoubleVar(value=self._clamp_to_slider(initial_value))
        self._entry_var = tk.StringVar(value=f"{self._exact_value:g}")
        self._building = False  # suppress re-entrant callbacks

        # Row 1: label + info icon + current value + range hint.
        # The description — previously rendered as a multi-line
        # wrapped paragraph under the slider — now lives in a
        # hover tooltip attached to a small ⓘ glyph next to the
        # bold label, so the default view stays compact.  Users
        # who want the full context hover the ⓘ.
        head = ttk.Frame(self)
        head.pack(fill=tk.X)
        ttk.Label(head, text=patch.label, font=("", 10, "bold")).pack(
            side=tk.LEFT)

        description = getattr(patch, "description", "") or ""
        info_lbl = attach_info_tooltip(head, description)
        if info_lbl is not None:
            info_lbl.pack(side=tk.LEFT, padx=(4, 0))

        self._value_lbl = ttk.Label(
            head,
            text=self._header_text(self._exact_value),
            foreground="gray")
        self._value_lbl.pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(
            head,
            text=(f"  slider range: {patch.slider_min:g}.."
                  f"{patch.slider_max:g}  (entry: unrestricted)"),
            foreground="gray",
        ).pack(side=tk.RIGHT)

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
        self._entry = ttk.Entry(row, textvariable=self._entry_var, width=12)
        self._entry.pack(side=tk.LEFT, padx=(0, 4))
        self._entry.bind("<Return>", self._on_entry_commit)
        self._entry.bind("<FocusOut>", self._on_entry_commit)
        ttk.Label(row, text=patch.unit,
                  foreground="gray").pack(side=tk.LEFT, padx=(0, 8))
        SecondaryButton(row, text="Reset",
                        command=self._reset).pack(side=tk.LEFT)

    # --- private helpers ----------------------------------------------

    def _clamp_to_slider(self, v: float) -> float:
        """Clamp to the slider's visual range for thumb positioning.
        Does NOT constrain ``_exact_value`` — the entry box remains
        free to hold out-of-range values."""
        return max(float(self._patch.slider_min),
                   min(float(self._patch.slider_max), float(v)))

    def _is_out_of_range(self, v: float) -> bool:
        return (v < self._patch.slider_min - 1e-9
                or v > self._patch.slider_max + 1e-9)

    def _header_text(self, value: float) -> str:
        badge = (" [!]"
                 if self._is_out_of_range(value) else "")
        return (f"= {value:g} {self._patch.unit}"
                f"  (default {self._patch.default:g}){badge}")

    # --- public --------------------------------------------------------

    def get_value(self) -> float:
        """Return the exact value the user set — possibly out of
        the slider's visual range when the user typed a power-user
        value into the entry box."""
        return float(self._exact_value)

    def set_value(self, v: float, *, clamp: bool = True) -> None:
        """Programmatic setter.

        ``clamp=True`` (default, used by slider drag + reset button):
        the value is clamped to ``[slider_min, slider_max]`` so
        the slider thumb and exact value stay in sync.

        ``clamp=False`` (used by the entry-box commit path):
        accept any finite float the user typed, move the slider
        thumb to the nearest bound, but keep ``_exact_value`` at
        the typed value so ``get_value()`` returns it verbatim.
        """
        v = float(v)
        if clamp:
            v = self._clamp_to_slider(v)
        self._exact_value = v
        self._building = True
        self._var.set(self._clamp_to_slider(v))
        self._entry_var.set(f"{v:g}")
        self._value_lbl.configure(text=self._header_text(v))
        self._building = False
        if self._on_change:
            self._on_change(v)

    def is_default(self) -> bool:
        return abs(self.get_value() - self._patch.default) < 1e-6

    # --- internals -----------------------------------------------------

    def _on_scale(self, _new):
        # Fires on every pixel-level drag on the slider.  The earlier
        # version used two different float-format strings here
        # (``%.3f`` on drag, ``%g`` in ``set_value``) which made the
        # entry field flicker between e.g. ``9.800`` and ``9.8`` when
        # the user dragged past the default, stole focus from anyone
        # typing into the entry, and produced noisy diffs in config
        # export.  Using ``%g`` consistently collapses both paths
        # onto the same canonical representation.
        if self._building:
            return
        # Slider drags always produce in-range values; sync
        # _exact_value to match.
        v = float(self._var.get())
        self._exact_value = v
        self._entry_var.set(f"{v:g}")
        self._value_lbl.configure(text=self._header_text(v))
        if self._on_change:
            self._on_change(v)

    def _on_entry_commit(self, _event=None):
        if self._building:
            return
        try:
            v = float(self._entry_var.get())
        except ValueError:
            # Snap back to last valid exact value.
            self._entry_var.set(f"{self._exact_value:g}")
            return
        # User-typed values bypass the slider clamp so power users
        # can exceed the slider's declared range.
        self.set_value(v, clamp=False)

    def _reset(self):
        # Reset uses clamp=True so the slider thumb visibly snaps
        # back to the default value.
        self.set_value(self._patch.default, clamp=True)
