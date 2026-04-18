"""Randomize page — shuffle-pool toggles, seed, and advanced options.

This page no longer carries its own "Build" button.  It mirrors its
field state into ``AppState.randomize_config`` on every change, and
the Build & Logs page reads that snapshot when the user clicks
"Start build".  One build entry point, no re-click.
"""

from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk

from ..models import RandomizerConfig
from ..widgets import Page, SecondaryButton, Section, SeedEntry


class RandomizePage(Page):
    title = "Randomize"
    description = (
        "Configure the shuffle pools and seed.  Patch packs live on "
        "the Patches page.  When you're ready, hop over to Build & "
        "Logs and hit Start build."
    )

    def _build(self) -> None:
        self._pool_vars: dict[str, tk.BooleanVar] = {}
        self._adv_vars: dict[str, tk.Variable] = {}

        # --- Seed row -----------------------------------------------------
        seed_row = ttk.Frame(self._body)
        seed_row.pack(fill=tk.X, pady=(0, 8))
        self._seed = SeedEntry(seed_row)
        self._seed.pack(side=tk.LEFT)
        # SeedEntry doesn't expose a trace API directly — wrap its
        # internal var via its public `get_seed()` + periodic syncs
        # on focus-out / Return.
        try:
            self._seed._var.trace_add("write", lambda *_a: self._sync_state())
        except Exception:  # noqa: BLE001
            pass

        # --- Pools --------------------------------------------------------
        pools = Section(self._body, title="Shuffle pools", initially_open=True)
        pools.pack(fill=tk.X, pady=(0, 6))

        for key, label, default in [
            ("do_major", "Major items (fragments + powers + obsidians)", False),
            ("do_keys", "Keys (within elemental realm)", False),
            ("do_gems", "Gems (per-level)", False),
            ("do_barriers", "Barriers (element vulnerability)", False),
            ("do_connections", "Level connections (may cause unsolvable seeds)", False),
        ]:
            var = tk.BooleanVar(value=default)
            self._pool_vars[key] = var
            var.trace_add("write", lambda *_a: self._sync_state())
            ttk.Checkbutton(pools.body, text=label, variable=var).pack(
                anchor=tk.W, pady=2)

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

    # --- state mirroring -----------------------------------------------

    def _sync_state(self) -> None:
        """Push current widget state into AppState.randomize_config.

        Runs on every widget change.  We deliberately swallow exceptions
        for half-typed numeric fields (e.g. user is mid-edit on the
        obsidian cost box) so the snapshot stays sane."""
        try:
            self.app.state.randomize_config = self._read_config()
        except Exception:  # noqa: BLE001
            pass

    def _read_config(self) -> RandomizerConfig:
        return RandomizerConfig(
            seed=self._seed.get_seed(),
            do_major=self._pool_vars["do_major"].get(),
            do_keys=self._pool_vars["do_keys"].get(),
            do_gems=self._pool_vars["do_gems"].get(),
            do_barriers=self._pool_vars["do_barriers"].get(),
            do_connections=self._pool_vars["do_connections"].get(),
            obsidian_cost=self._parse_obsidian_cost(),
            item_pool=self._parse_item_pool(),
        )

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
