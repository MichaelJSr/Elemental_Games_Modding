"""Tests for the GUI ``Tooltip`` helper + ``attach_info_tooltip``
factory introduced in round 11.9 to move long slider /
pack / category descriptions out of the always-visible layout
and into a hover-reveal popup.

Runs under headless tkinter (a single shared Tk root that
``withdraw()``s itself immediately).  The tests exercise the
tooltip's bind / lifecycle contract without requiring an actual
mouse-in-the-widget event: we invoke the internal scheduling
directly so the suite runs deterministically on CI.
"""

from __future__ import annotations

import os
import sys
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _tk_available() -> bool:
    """Return True when a Tk root can be created on this host.

    CI runners without a DISPLAY (Linux headless, some macOS
    minimal images) fail to init Tk — in that case skip the
    widget tests entirely rather than error."""
    if os.environ.get("DISPLAY") is None and sys.platform.startswith("linux"):
        return False
    try:
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return True
    except tk.TclError:
        return False


@unittest.skipUnless(_tk_available(),
    "Tk unavailable on this host (no DISPLAY / broken ttk)")
class TooltipLifecycle(unittest.TestCase):

    def setUp(self):
        self._root = tk.Tk()
        self._root.withdraw()

    def tearDown(self):
        self._root.update_idletasks()
        self._root.destroy()

    def test_import_surface_is_stable(self):
        """The helper's public API is tight: ``Tooltip`` class +
        ``attach_info_tooltip`` factory.  Nothing else should
        leak.  Pins the surface so refactors don't silently
        break external callers."""
        from gui import widgets
        self.assertTrue(hasattr(widgets, "Tooltip"))
        self.assertTrue(hasattr(widgets, "attach_info_tooltip"))

    def test_empty_text_skips_creation(self):
        """``attach_info_tooltip("")`` returns ``None`` so the
        caller can avoid packing an empty ⓘ glyph."""
        from gui.widgets import attach_info_tooltip
        result = attach_info_tooltip(self._root, "")
        self.assertIsNone(result)
        result = attach_info_tooltip(self._root, "   ")
        self.assertIsNone(result)

    def test_non_empty_text_returns_label(self):
        from gui.widgets import attach_info_tooltip
        result = attach_info_tooltip(self._root, "hello")
        self.assertIsNotNone(result)
        self.assertIsInstance(result, tk.Widget)
        # Info glyph is the circled-i character.
        self.assertEqual(result.cget("text"), "\u24d8")

    def test_tooltip_popup_opens_and_closes(self):
        """Direct-exercise the show/hide lifecycle without firing
        real <Enter> events (those are flaky under headless
        Tk).  Confirms the Toplevel is created on show and
        destroyed on hide."""
        from gui.widgets import Tooltip
        trigger = ttk.Label(self._root, text="⁰")
        trigger.pack()
        tip = Tooltip(trigger, "descriptive text", delay_ms=0)
        self.assertIsNone(tip._tip)
        tip._show()
        self._root.update()
        self.assertIsNotNone(tip._tip)
        # Hide.
        tip._hide()
        self._root.update()
        self.assertIsNone(tip._tip)

    def test_set_text_updates_open_tooltip(self):
        from gui.widgets import Tooltip
        trigger = ttk.Label(self._root, text="⁰")
        trigger.pack()
        tip = Tooltip(trigger, "original", delay_ms=0)
        tip._show()
        self._root.update()
        self.assertIsNotNone(tip._tip)
        tip.set_text("updated")
        # The new text must have been written into the popup's
        # label widget (re-rendered in place).
        labels = [
            child
            for frame in tip._tip.winfo_children()
            for child in frame.winfo_children()
            if isinstance(child, tk.Label)
        ]
        self.assertTrue(any(lbl.cget("text") == "updated"
                            for lbl in labels))

    def test_destroy_of_trigger_cleans_tooltip(self):
        """When the trigger widget is destroyed while the tooltip
        is open, the popup must also close — not leak."""
        from gui.widgets import Tooltip
        trigger = ttk.Label(self._root, text="⁰")
        trigger.pack()
        tip = Tooltip(trigger, "bye", delay_ms=0)
        tip._show()
        self._root.update()
        self.assertIsNotNone(tip._tip)
        trigger.destroy()
        self._root.update()
        self.assertIsNone(tip._tip,
            msg="tooltip popup must be destroyed when trigger "
                "widget is destroyed")


@unittest.skipUnless(_tk_available(),
    "Tk unavailable on this host")
class ParametricSliderUsesTooltip(unittest.TestCase):
    """Integration: a ``ParametricSlider`` with a non-empty
    description must NOT render the description as a wrapped
    paragraph in the main flow — that clutter was the original
    complaint.  Instead the description lives on a tooltipped
    ⓘ glyph next to the bold label."""

    def setUp(self):
        self._root = tk.Tk()
        self._root.withdraw()

    def tearDown(self):
        self._root.update_idletasks()
        self._root.destroy()

    def test_no_standalone_description_label_rendered(self):
        from azurik_mod.patches.player_physics import (
            WING_FLAP_CEILING_SCALE,
        )
        from gui.widgets import ParametricSlider
        slider = ParametricSlider(self._root, WING_FLAP_CEILING_SCALE)
        slider.pack()
        self._root.update_idletasks()

        # Find every Label descendant and grep for the
        # description text.  Pre-round-11.9, the description
        # was rendered as a wrapped paragraph directly in the
        # slider frame — that label must no longer exist as a
        # visible row.  The tooltip's Toplevel is a SEPARATE
        # window hierarchy (not a child of `slider`), so it
        # won't show up in this walk even when open.
        full_desc = WING_FLAP_CEILING_SCALE.description.strip()
        self.assertTrue(full_desc,
            msg="fixture sanity: this slider must have a "
                "non-empty description for the test to mean "
                "anything")
        for widget in _walk_descendants(slider):
            if isinstance(widget, (tk.Label, ttk.Label)):
                text = widget.cget("text")
                self.assertNotEqual(
                    text, full_desc,
                    msg="full description must NOT be rendered "
                        "as a standalone label — it should "
                        "live in a tooltip instead")

    def test_info_glyph_present_when_description_set(self):
        from azurik_mod.patches.player_physics import (
            WING_FLAP_CEILING_SCALE,
        )
        from gui.widgets import ParametricSlider
        slider = ParametricSlider(self._root, WING_FLAP_CEILING_SCALE)
        slider.pack()
        self._root.update_idletasks()
        # Look for the ⓘ glyph.
        seen = False
        for widget in _walk_descendants(slider):
            if isinstance(widget, (tk.Label, ttk.Label)):
                if widget.cget("text") == "\u24d8":
                    seen = True
                    break
        self.assertTrue(seen,
            msg="slider with description must render the "
                "ⓘ info glyph as a tooltip trigger")

    def test_no_info_glyph_when_description_empty(self):
        """A slider with no description must NOT show an ⓘ
        glyph — no point triggering an empty tooltip."""
        from azurik_mod.patches.player_physics import (
            WALK_SPEED_SCALE,   # no description
        )
        from gui.widgets import ParametricSlider
        self.assertFalse(
            (getattr(WALK_SPEED_SCALE, 'description', '') or '').strip(),
            msg="fixture sanity: WALK_SPEED_SCALE has no description")
        slider = ParametricSlider(self._root, WALK_SPEED_SCALE)
        slider.pack()
        self._root.update_idletasks()
        for widget in _walk_descendants(slider):
            if isinstance(widget, (tk.Label, ttk.Label)):
                self.assertNotEqual(
                    widget.cget("text"), "\u24d8",
                    msg="no ⓘ glyph for empty descriptions")


def _walk_descendants(widget: tk.Widget):
    """Yield every descendant widget of ``widget``, recursively."""
    for child in widget.winfo_children():
        yield child
        yield from _walk_descendants(child)


if __name__ == "__main__":
    unittest.main()
