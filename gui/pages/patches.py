"""Patches page — generic browser + parametric sliders.

Renders every registered pack as a checkbox row.  Any pack that
exposes ParametricPatch sites gets a collapsible section of sliders
right under its checkbox.  Slider values mirror into
`AppState.pack_params[pack_name][param_name]` so the Build page and
the backend worker see the same values.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# Importing the patches package triggers `register_pack(...)` side
# effects so `all_packs()` returns them in UI order.
import azurik_mod.patches  # noqa: F401
from azurik_mod.patching.registry import all_packs

from ..widgets import PackBrowser, Page, ParametricSlider, Section


class PatchesPage(Page):
    title = "Patches"
    description = ("Tick the patches you want to apply to the next build. "
                   "Everything is off by default — enable only the tweaks "
                   "you want.  Sliders beneath a patch only take effect "
                   "when that patch is ticked.")

    def _build(self) -> None:
        self._vars: dict[str, tk.BooleanVar] = {}
        self._sliders: dict[tuple[str, str], ParametricSlider] = {}

        # Re-use PackBrowser for the checkbox rows (per-tag grouping).
        browser = PackBrowser(self._body, all_packs(), self._vars)
        browser.pack(fill=tk.X)

        # Mirror pack toggle state into AppState.
        for name, var in self._vars.items():
            self.app.state.enabled_packs[name] = var.get()
            var.trace_add("write",
                          lambda *_a, n=name, v=var:
                              self.app.state.set_pack(n, v.get()))

        # Any pack with parametric sites gets a dedicated slider section.
        parametric_packs = [p for p in all_packs() if p.parametric_sites()]
        if parametric_packs:
            ttk.Separator(self._body, orient=tk.HORIZONTAL).pack(
                fill=tk.X, pady=(12, 6))
            ttk.Label(self._body, text="Parametric sliders",
                      font=("", 12, "bold")).pack(anchor=tk.W, pady=(0, 4))

            pack_params = getattr(self.app.state, "pack_params", None)
            if pack_params is None:
                pack_params = {}
                self.app.state.pack_params = pack_params

            for pack in parametric_packs:
                pack_params.setdefault(pack.name, {})
                section = Section(self._body, title=pack.name,
                                   initially_open=True)
                section.pack(fill=tk.X, pady=(0, 6))
                for pp in pack.parametric_sites():
                    initial = pack_params[pack.name].get(pp.name, pp.default)
                    pack_params[pack.name][pp.name] = initial

                    def on_change(value, _pack=pack.name, _param=pp.name):
                        self.app.state.pack_params[_pack][_param] = value

                    slider = ParametricSlider(
                        section.body, pp,
                        initial=initial,
                        on_change=on_change,
                    )
                    slider.pack(fill=tk.X, pady=(2, 8))
                    self._sliders[(pack.name, pp.name)] = slider

    def get_pack_flags(self) -> dict[str, bool]:
        return {n: v.get() for n, v in self._vars.items()}

    def get_pack_params(self) -> dict[str, dict[str, float]]:
        return {k: dict(v) for k, v in
                getattr(self.app.state, "pack_params", {}).items()}
