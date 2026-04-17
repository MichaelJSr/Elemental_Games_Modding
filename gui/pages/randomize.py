"""Randomize page — collapsible sections + 'Build' handoff to BuildPage."""

from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox, ttk

from ..models import RandomizerConfig
from ..widgets import Page, PrimaryButton, SecondaryButton, Section, SeedEntry


class RandomizePage(Page):
    title = "Randomize"
    description = ("Seed and shuffle-pool toggles for the randomizer. "
                   "Patch packs (QoL, 60 FPS unlock, player physics sliders, …) "
                   "live on the Patches page and are applied on top of whichever "
                   "pools you enable here.  Hit 'Build' to run both through the "
                   "Build & Logs page.")

    def _build(self) -> None:
        self._pool_vars: dict[str, tk.BooleanVar] = {}
        self._adv_vars: dict[str, tk.Variable] = {}

        # --- Seed row -----------------------------------------------------
        seed_row = ttk.Frame(self._body)
        seed_row.pack(fill=tk.X, pady=(0, 8))
        self._seed = SeedEntry(seed_row)
        self._seed.pack(side=tk.LEFT)

        # --- Pools --------------------------------------------------------
        pools = Section(self._body, title="Shuffle pools", initially_open=True)
        pools.pack(fill=tk.X, pady=(0, 6))

        # All options default OFF — the user explicitly opts in.
        for key, label, default in [
            ("do_major", "Major items (fragments + powers + obsidians)", False),
            ("do_keys", "Keys (within elemental realm)", False),
            ("do_gems", "Gems (per-level)", False),
            ("do_barriers", "Barriers (element vulnerability)", False),
            ("do_connections", "Level connections (may cause unsolvable seeds)", False),
        ]:
            var = tk.BooleanVar(value=default)
            self._pool_vars[key] = var
            ttk.Checkbutton(pools.body, text=label, variable=var).pack(
                anchor=tk.W, pady=2)

        # --- Advanced -----------------------------------------------------
        adv = Section(self._body, title="Advanced", initially_open=False)
        adv.pack(fill=tk.X, pady=(0, 6))

        hard_var = tk.BooleanVar(value=False)
        self._adv_vars["hard_barriers"] = hard_var
        ttk.Checkbutton(adv.body,
                        text="Include multi-element barrier fourccs (hard)",
                        variable=hard_var).pack(anchor=tk.W, pady=2)

        cost_row = ttk.Frame(adv.body)
        cost_row.pack(fill=tk.X, pady=4)
        ttk.Label(cost_row, text="Obsidian cost per lock:", width=22).pack(
            side=tk.LEFT)
        self._adv_vars["obsidian_cost"] = tk.StringVar(value="")
        ttk.Entry(cost_row, textvariable=self._adv_vars["obsidian_cost"],
                  width=8).pack(side=tk.LEFT)
        ttk.Label(cost_row, text="(default: 10 -> 10,20,...100)",
                  foreground="gray").pack(side=tk.LEFT, padx=(6, 0))

        pool_row = ttk.Frame(adv.body)
        pool_row.pack(fill=tk.X, pady=4)
        ttk.Label(pool_row, text="Custom item pool JSON:").pack(anchor=tk.W)
        self._adv_vars["item_pool"] = tk.StringVar(value="")
        ttk.Entry(pool_row, textvariable=self._adv_vars["item_pool"],
                  width=60).pack(fill=tk.X, pady=(2, 0))

        # --- Build button -------------------------------------------------
        action = ttk.Frame(self._body)
        action.pack(fill=tk.X, pady=(12, 4))
        PrimaryButton(action, text="Build randomized ISO",
                      command=self._build_click).pack(side=tk.LEFT, padx=(0, 8))
        SecondaryButton(action, text="Open Build page",
                        command=lambda: self.app.show_page("build")).pack(
            side=tk.LEFT)

    # --- internals ------------------------------------------------------

    def _build_click(self) -> None:
        if self.app.state.iso_path is None:
            messagebox.showerror("No ISO selected",
                                 "Pick a base ISO on the Project page first.")
            return

        # Pack enablement (QoL, FPS unlock, player physics, …) lives on
        # the Patches page.  BuildPage merges that state into the config
        # right before kicking off the worker; see BuildPage._merge_packs.
        config = RandomizerConfig(
            seed=self._seed.get_seed(),
            do_major=self._pool_vars["do_major"].get(),
            do_keys=self._pool_vars["do_keys"].get(),
            do_gems=self._pool_vars["do_gems"].get(),
            do_barriers=self._pool_vars["do_barriers"].get(),
            do_connections=self._pool_vars["do_connections"].get(),
            obsidian_cost=self._parse_obsidian_cost(),
            item_pool=self._parse_item_pool(),
        )

        # Fire event — BuildPage picks this up, flips to itself, and runs.
        self.app.state.bus.emit("build_request", config)

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
