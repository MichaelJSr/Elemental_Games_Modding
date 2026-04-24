"""Headless GUI regression for the Player-tab Quick Stats sub-group.

Builds a :class:`gui.widgets.PackBrowser` against the shipping
registry and walks the resulting Tk widget tree to confirm that:

- The Player tab contains a ``ttk.LabelFrame`` titled "Quick Stats".
- Both ``player_max_hp`` and ``air_shield_flaps`` render inside
  that LabelFrame (not in the flat tab body).
- Other Player-tab packs (e.g. ``player_physics``) render outside
  the Quick Stats frame.

If a future refactor accidentally drops the ``subgroup='quick_stats'``
field from either pack, or if ``PackBrowser._build_tab`` stops
honouring it, this test fails loudly — much better than the visual
drift we'd otherwise notice only when a user reports it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _walk(widget):
    """Depth-first iterator over every descendant of ``widget``
    (self excluded)."""
    stack = list(widget.winfo_children())
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.winfo_children())


def _find_ancestor_labelframe(widget, title: str):
    """Return the nearest ancestor ``ttk.LabelFrame`` with the given
    title, or ``None`` if the widget isn't inside one."""
    import tkinter.ttk as ttk

    cur = widget.master
    while cur is not None:
        if isinstance(cur, ttk.LabelFrame):
            try:
                if cur.cget("text") == title:
                    return cur
            except Exception:  # noqa: BLE001
                pass
        cur = cur.master
    return None


class QuickStatsSubgroupRenders(unittest.TestCase):
    """The Player tab must surface a Quick Stats LabelFrame."""

    def setUp(self):
        import azurik_mod.patches  # noqa: F401
        import tkinter as tk
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk not available: {exc}")
        self.root.withdraw()

        from gui.widgets import PackBrowser
        from azurik_mod.patching.registry import all_packs

        self._vars: dict[str, tk.BooleanVar] = {}
        self._pack_params: dict[str, dict[str, float]] = {}
        self.browser = PackBrowser(
            self.root, all_packs(), self._vars,
            pack_params=self._pack_params, on_param_change=None)

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:  # noqa: BLE001
            pass

    def _find_pack_checkbox(self, pack_name: str):
        """Return the :class:`ttk.Checkbutton` whose label starts
        with ``pack_name``; raises :class:`AssertionError` if it
        can't be found."""
        import tkinter.ttk as ttk

        expected_prefix = f"{pack_name}  ("
        for node in _walk(self.browser):
            if not isinstance(node, ttk.Checkbutton):
                continue
            try:
                text = node.cget("text")
            except Exception:  # noqa: BLE001
                continue
            if isinstance(text, str) and text.startswith(
                    expected_prefix):
                return node
        raise AssertionError(
            f"checkbox for pack {pack_name!r} not found anywhere "
            f"under the PackBrowser.")

    def test_quick_stats_labelframe_exists(self):
        import tkinter.ttk as ttk
        found_titles = set()
        for node in _walk(self.browser):
            if isinstance(node, ttk.LabelFrame):
                try:
                    found_titles.add(node.cget("text"))
                except Exception:  # noqa: BLE001
                    pass
        self.assertIn(
            "Quick Stats", found_titles,
            msg=f"Quick Stats LabelFrame missing from the "
                f"PackBrowser.  Seen: {sorted(found_titles)!r}.  "
                f"Likely cause: either the subgroup registry lost "
                f"the ``quick_stats`` entry, or no pack currently "
                f"declares ``subgroup='quick_stats'`` (empty "
                f"subgroups are skipped on purpose).")

    def test_player_max_hp_lives_inside_quick_stats(self):
        cb = self._find_pack_checkbox("player_max_hp")
        frame = _find_ancestor_labelframe(cb, "Quick Stats")
        self.assertIsNotNone(
            frame,
            msg="player_max_hp's checkbox is not under the "
                "Quick Stats LabelFrame — the pack either lost "
                "its ``subgroup='quick_stats'`` field or the "
                "builder skipped it.")

    def test_air_shield_flaps_lives_inside_quick_stats(self):
        cb = self._find_pack_checkbox("air_shield_flaps")
        frame = _find_ancestor_labelframe(cb, "Quick Stats")
        self.assertIsNotNone(
            frame,
            msg="air_shield_flaps' checkbox is not under the "
                "Quick Stats LabelFrame.")

    def test_player_physics_stays_outside_quick_stats(self):
        """A non-quick-stats pack must NOT sneak into the group.

        Guards against a builder regression where every player-
        category pack gets boxed into the first available subgroup.
        """
        try:
            cb = self._find_pack_checkbox("player_physics")
        except AssertionError:
            self.skipTest("player_physics not registered in this run")
        frame = _find_ancestor_labelframe(cb, "Quick Stats")
        self.assertIsNone(
            frame,
            msg="player_physics was rendered inside the Quick "
                "Stats LabelFrame — the subgroup grouping logic "
                "is over-eager and is sucking ungrouped packs in.")

    def test_all_three_air_shield_sliders_render(self):
        """The three flap sliders must all be present under the
        pack, proving the one-pack-three-sliders shape is wired
        end-to-end (not silently collapsed to a single slider)."""
        sliders = self.browser.sliders()
        for entity_id in ("air_shield_1_flaps",
                          "air_shield_2_flaps",
                          "air_shield_3_flaps"):
            self.assertIn(
                ("air_shield_flaps", entity_id), sliders,
                msg=f"slider {entity_id!r} missing from the "
                    f"air_shield_flaps pack — the three-slider "
                    f"shape got collapsed.")


if __name__ == "__main__":
    unittest.main()
