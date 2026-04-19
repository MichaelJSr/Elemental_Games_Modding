"""Patches page — tabbed browser organised by patch category.

Renders every registered :class:`~azurik_mod.patching.registry.PatchPack`
as a checkbox row inside a notebook tab.  Tabs come from
:mod:`azurik_mod.patching.category` so new categories appear
automatically when a patch declares them — no GUI code changes
needed to onboard a fresh category.

Parametric sliders for a pack live inline right under the pack's
checkbox (no more scattered "Parametric sliders" section at the
bottom of the page).

Categories hidden here
----------------------
Some categories are "owned" by a dedicated page and are omitted
from the Patches tab strip to avoid user-facing duplication:

- ``randomize`` — the shuffle-pool toggles live on the dedicated
  **Randomize** page, which also owns the seed + advanced
  options.  They're still real :class:`Feature` entries + still
  flow through ``AppState.enabled_packs``, so everything the
  Build page reads works unchanged; we just don't render them
  twice in the GUI.

See :data:`HIDDEN_CATEGORIES` below for the authoritative list.
Plugins that want to hide their own category from the Patches
tab should extend this set (or offer their own dedicated page).
"""

from __future__ import annotations

import tkinter as tk

# Importing the patches package triggers register_feature(...) side
# effects so all_packs() / packs_by_category() return the full set
# in category order.
import azurik_mod.patches  # noqa: F401
from azurik_mod.patching.registry import all_packs


#: Category ids rendered elsewhere in the GUI, so we drop them
#: from the Patches-page tab strip.  Each entry is a ``category``
#: id exactly as it appears in the registry.
HIDDEN_CATEGORIES: frozenset[str] = frozenset({
    "randomize",
})


from ..widgets import PackBrowser, Page


class PatchesPage(Page):
    title = "Patches"
    description = ("Switch to a category tab and tick the patches you "
                   "want to apply.  Everything is off by default.  "
                   "Sliders only take effect when their patch is "
                   "ticked.  Shuffle pools live on the Randomize page.")

    def _build(self) -> None:
        self._vars: dict[str, tk.BooleanVar] = {}

        # AppState holds the shared slider values so the Build page's
        # worker sees exactly what the GUI shows.  Initialise once.
        pack_params = getattr(self.app.state, "pack_params", None)
        if pack_params is None:
            pack_params = {}
            self.app.state.pack_params = pack_params

        def _mirror_param(pack_name: str, param_name: str,
                          value: float) -> None:
            # Already mirrored into ``pack_params`` by PackBrowser;
            # this hook exists for future extensions (e.g. publishing
            # a "pack_param_changed" event on the bus).
            pass

        # Filter out categories that have their own dedicated GUI
        # page (see module docstring + HIDDEN_CATEGORIES).  Packs
        # stay registered globally so the backend / CLI / plugin
        # system keep working; we just don't render their tab here.
        visible_packs = [p for p in all_packs()
                         if p.category not in HIDDEN_CATEGORIES]

        self._browser = PackBrowser(
            self._body,
            visible_packs,
            self._vars,
            pack_params=pack_params,
            on_param_change=_mirror_param,
        )
        self._browser.pack(fill=tk.BOTH, expand=True)

        # Mirror pack toggle state into AppState so the Build page's
        # worker sees the same flags.  Subscribed AFTER PackBrowser
        # has populated ``_vars`` so every registered pack gets a
        # tracer.
        for name, var in self._vars.items():
            self.app.state.enabled_packs[name] = var.get()
            var.trace_add("write",
                          lambda *_a, n=name, v=var:
                              self.app.state.set_pack(n, v.get()))

    # Legacy accessors some existing tests may reach for.
    def tabs(self) -> list[str]:
        """Category ids of currently-rendered tabs (in order)."""
        return self._browser.tabs()

    def tab_titles(self) -> list[str]:
        """User-facing tab labels."""
        return self._browser.tab_titles()
