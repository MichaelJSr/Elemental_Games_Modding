"""Randomize page — shuffle pools (via PackBrowser) + seed + advanced.

The five shuffle pools are surfaced as ``category="randomize"``
features in the central registry (see
``azurik_mod/patches/randomize/__init__.py``), so this page
renders them via the shared :class:`PackBrowser` — the same
widget the Patches page uses — instead of hand-coded
checkboxes.  Seed + advanced options remain page-local
because they don't fit the Feature model.

Bidirectional sync:

- Widget → state: ``PackBrowser`` mirrors pack toggles into
  ``AppState.enabled_packs`` via its standard ``trace_add``
  path.  ``_sync_state`` then copies each ``enabled_packs["rand_*"]``
  boolean into the matching ``randomize_config.do_*`` field so
  the Build page's legacy call path keeps working.
- State → widget: nothing writes to ``enabled_packs`` behind
  our back, so we don't need reverse tracing.
"""

from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk

import azurik_mod.patches  # noqa: F401 — populate the registry
from azurik_mod.patching.registry import all_packs

from ..models import RandomizerConfig
from ..widgets import PackBrowser, Page, SecondaryButton, Section, SeedEntry

# Map from ``Feature.name`` in the randomize category to the
# matching ``RandomizerConfig`` boolean field.  Keeping this
# table explicit (vs. string-munging) makes the sync intent
# obvious + lets us add a pool without touching this page.
_POOL_TO_CONFIG_FIELD = {
    "rand_major":       "do_major",
    "rand_keys":        "do_keys",
    "rand_gems":        "do_gems",
    "rand_barriers":    "do_barriers",
    "rand_connections": "do_connections",
}


class RandomizePage(Page):
    title = "Randomize"
    description = (
        "Pick a seed, tick the shuffle pools you want, and optionally "
        "tweak the advanced options.  Patch packs live on the Patches "
        "page.  When you're ready, hop over to Build & Logs and hit "
        "Start build."
    )

    def _build(self) -> None:
        self._pool_vars: dict[str, tk.BooleanVar] = {}
        self._adv_vars: dict[str, tk.Variable] = {}

        # --- Seed row -----------------------------------------------------
        seed_row = ttk.Frame(self._body)
        seed_row.pack(fill=tk.X, pady=(0, 8))
        self._seed = SeedEntry(seed_row)
        self._seed.pack(side=tk.LEFT)
        try:
            self._seed._var.trace_add("write", lambda *_a: self._sync_state())
        except Exception:  # noqa: BLE001
            pass

        # --- Shuffle pools (via the shared PackBrowser) -------------------
        #
        # Filter ``all_packs()`` to the randomize category so the
        # browser only renders the 5 pool toggles here (the Patches
        # page shows every category).  Empty-category hiding means
        # the browser collapses to a single tab labelled "Randomize",
        # which keeps the Randomize page looking like the old
        # hand-coded section.
        randomize_packs = [p for p in all_packs()
                           if p.category == "randomize"]
        pool_host = ttk.Frame(self._body)
        pool_host.pack(fill=tk.X, pady=(0, 6))
        self._browser = PackBrowser(
            pool_host, randomize_packs, self._pool_vars,
            pack_params=None,     # no sliders on shuffle pools
            on_param_change=None,
        )
        self._browser.pack(fill=tk.X)

        # Mirror pool toggles → AppState.enabled_packs + kick off a
        # ``_sync_state`` so ``randomize_config.do_*`` keeps in lock-
        # step.  Using the same trace pattern PatchesPage uses.
        for name, var in self._pool_vars.items():
            self.app.state.enabled_packs[name] = var.get()
            var.trace_add(
                "write",
                lambda *_a, n=name, v=var: self._on_pool_toggle(n, v))

        # --- Advanced -----------------------------------------------------
        adv = Section(self._body, title="Advanced", initially_open=False)
        adv.pack(fill=tk.X, pady=(0, 6))

        hard_var = tk.BooleanVar(value=False)
        self._adv_vars["hard_barriers"] = hard_var
        hard_var.trace_add("write", lambda *_a: self._sync_state())
        ttk.Checkbutton(adv.body,
                        text="Include multi-element barrier fourccs (hard)",
                        variable=hard_var).pack(anchor=tk.W, pady=2)

        cost_row = ttk.Frame(adv.body)
        cost_row.pack(fill=tk.X, pady=4)
        ttk.Label(cost_row, text="Obsidian cost per lock:", width=22).pack(
            side=tk.LEFT)
        cost_var = tk.StringVar(value="")
        self._adv_vars["obsidian_cost"] = cost_var
        cost_var.trace_add("write", lambda *_a: self._sync_state())
        ttk.Entry(cost_row, textvariable=cost_var, width=8).pack(side=tk.LEFT)
        ttk.Label(cost_row, text="(default: 10 -> 10,20,...100)",
                  foreground="gray").pack(side=tk.LEFT, padx=(6, 0))

        pool_row = ttk.Frame(adv.body)
        pool_row.pack(fill=tk.X, pady=4)
        ttk.Label(pool_row, text="Custom item pool JSON:").pack(anchor=tk.W)
        pool_var = tk.StringVar(value="")
        self._adv_vars["item_pool"] = pool_var
        pool_var.trace_add("write", lambda *_a: self._sync_state())
        ttk.Entry(pool_row, textvariable=pool_var, width=60).pack(
            fill=tk.X, pady=(2, 0))

        # --- Nav helper ---------------------------------------------------
        nav = ttk.Frame(self._body)
        nav.pack(fill=tk.X, pady=(12, 4))
        SecondaryButton(
            nav,
            text="Go to Build & Logs →",
            command=lambda: self.app.show_page("build"),
        ).pack(side=tk.LEFT)
        ttk.Label(
            nav,
            text="(the Start build button on that page now launches the build)",
            foreground="gray",
        ).pack(side=tk.LEFT, padx=(8, 0))

        # Seed an initial snapshot so Build can fire immediately even
        # if the user hasn't touched anything on this page.
        self._sync_state()

    # ---- pool toggle handling ----------------------------------------

    def _on_pool_toggle(self, pack_name: str,
                        var: "tk.BooleanVar") -> None:
        """Mirror a shuffle-pool checkbox into AppState."""
        self.app.state.set_pack(pack_name, var.get())
        self._sync_state()

    # ---- state mirroring ---------------------------------------------

    def _sync_state(self) -> None:
        """Push current widget state into AppState.randomize_config.

        The 5 pool booleans are drawn from ``enabled_packs`` (the
        PackBrowser's source of truth); seed + advanced come from
        our local widgets.  Exceptions from half-typed numeric
        fields are swallowed so the snapshot stays sane mid-edit.
        """
        try:
            self.app.state.randomize_config = self._read_config()
        except Exception:  # noqa: BLE001
            pass

    def _read_config(self) -> RandomizerConfig:
        return RandomizerConfig(
            seed=self._seed.get_seed(),
            do_major=self._pack_enabled("rand_major"),
            do_keys=self._pack_enabled("rand_keys"),
            do_gems=self._pack_enabled("rand_gems"),
            do_barriers=self._pack_enabled("rand_barriers"),
            do_connections=self._pack_enabled("rand_connections"),
            obsidian_cost=self._parse_obsidian_cost(),
            item_pool=self._parse_item_pool(),
        )

    def _pack_enabled(self, name: str) -> bool:
        """Read a ``rand_*`` flag from the app-wide enabled_packs dict,
        falling back to the local BooleanVar when the bus hasn't
        propagated yet (e.g. on first ``_sync_state`` before the
        initial trace_add fires).
        """
        if name in self.app.state.enabled_packs:
            return bool(self.app.state.enabled_packs[name])
        var = self._pool_vars.get(name)
        return bool(var.get()) if var is not None else False

    def _parse_obsidian_cost(self) -> int | None:
        raw = self._adv_vars["obsidian_cost"].get().strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def _parse_item_pool(self) -> dict | None:
        raw = self._adv_vars["item_pool"].get().strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
