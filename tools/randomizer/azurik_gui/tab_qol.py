"""QoL settings tab — quality of life patches applied to the XBE."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


QOL_PATCHES = [
    {
        "key": "disable_gem_popups",
        "label": "Disable gem first-pickup popups",
        "description": "Removes the popup messages that appear the first time you collect each gem type.",
        "default": True,
        "included_in_randomizer": True,
    },
    {
        "key": "disable_pickup_anims",
        "label": "Disable pickup celebration animations",
        "description": "Skips the celebration animation for all collectible pickups (obsidians, keys, powers, disc fragments) without affecting save persistence.",
        "default": True,
        "included_in_randomizer": True,
    },
    {
        "key": "fps_unlock",
        "label": "60 FPS unlock (experimental)",
        "description": "Unlocks 60 fps: removes the 30 fps VBlank cap, doubles the "
                       "simulation rate to 60 Hz, patches 28 subsystem timesteps, "
                       "velocity/collision constants, animation accumulators, "
                       "disables the D3D Present spin-wait to prevent frame stalls, "
                       "corrects render-phase flash timers, tunes the collision "
                       "solver bounce limit and impulse scaling for correct stair "
                       "climbing, scales the ground probe offset to fix edge-walk "
                       "velocity, uses FISTP truncation to prevent the "
                       "60→30 fps death spiral, and caps simulation steps at 2 "
                       "per frame to prevent crash on death at low FPS.",
        "default": False,
        "included_in_randomizer": False,
    },
]


class QoLTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._vars = {}
        self._build()

    def _build(self):
        ttk.Label(self, text="Quality of Life Patches",
                  font=("", 11, "bold")).pack(anchor=tk.W, padx=10, pady=(10, 5))

        ttk.Label(self, text="Toggle individual patches below. These are applied "
                  "when the randomizer's QoL option is enabled.",
                  wraplength=500, justify=tk.LEFT).pack(anchor=tk.W, padx=10, pady=(0, 10))

        for patch in QOL_PATCHES:
            frame = ttk.Frame(self)
            frame.pack(fill=tk.X, padx=10, pady=3)

            var = tk.BooleanVar(value=patch["default"])
            self._vars[patch["key"]] = var

            cb = ttk.Checkbutton(frame, text=patch["label"], variable=var)
            cb.pack(anchor=tk.W)

            desc = ttk.Label(frame, text=patch["description"],
                           foreground="gray", wraplength=450, justify=tk.LEFT)
            desc.pack(anchor=tk.W, padx=(25, 0))

            if patch.get("included_in_randomizer"):
                note = ttk.Label(frame, text="(included in randomizer QoL)",
                               foreground="#888", font=("", 8))
                note.pack(anchor=tk.W, padx=(25, 0))

        # Spacer + future section
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=10, pady=15)
        ttk.Label(self, text="More QoL patches coming soon: fast text, "
                  "skip cutscenes, custom start location...",
                  foreground="gray").pack(anchor=tk.W, padx=10)

    def get_patch_flags(self) -> dict[str, bool]:
        """Return current checkbox values keyed by patch key."""
        return {k: v.get() for k, v in self._vars.items()}
