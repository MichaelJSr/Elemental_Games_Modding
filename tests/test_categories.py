"""Tests for the patch-category system.

Covers:
- Category registry (register, get, auto-create, duplicate protection).
- Feature.category field (default + explicit + unknown auto-registers).
- packs_by_category() ordering + empty-category handling.
- Shipped pack categorisation (fps_unlock → performance, etc.).
- Randomizer doesn't regress after the tag→category migration.
- GUI PackBrowser renders one ttk.Notebook tab per non-empty category
  in the correct order and stays robust when a plugin registers a
  new category on the fly.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


class CategoryModel(unittest.TestCase):
    """Unit tests on the Category dataclass + registry helpers.

    Isolates mutations from the global registry by snapshot+restore
    (same pattern as ``FeatureCategoryField``).
    """

    def setUp(self) -> None:
        from azurik_mod.patching.category import (
            _REGISTRY, clear_registry_for_tests)
        self._snapshot = dict(_REGISTRY)
        clear_registry_for_tests()

    def tearDown(self) -> None:
        from azurik_mod.patching.category import _REGISTRY
        _REGISTRY.clear()
        _REGISTRY.update(self._snapshot)

    def test_builtin_categories_seeded_on_import(self):
        from azurik_mod.patching.category import all_categories
        ids = [c.id for c in all_categories()]
        for needed in ("performance", "player", "boot", "qol", "other"):
            self.assertIn(needed, ids)

    def test_builtins_in_stable_order(self):
        """``order`` drives the sort: performance → player → boot →
        qol → other.  Pin both the order values AND the resulting
        sequence so a reshuffle of ``_BUILTIN_CATEGORIES`` flips the
        test."""
        from azurik_mod.patching.category import all_categories
        cats = {c.id: c for c in all_categories()}
        self.assertLess(cats["performance"].order, cats["player"].order)
        self.assertLess(cats["player"].order, cats["boot"].order)
        self.assertLess(cats["boot"].order, cats["qol"].order)
        self.assertLess(cats["qol"].order, cats["other"].order)

    def test_register_category_idempotent(self):
        """Re-registering the exact same Category is a no-op."""
        from azurik_mod.patching.category import (
            Category, all_categories, register_category)
        c = Category("performance", "Performance",
                     all_categories()[0].description, 10)
        # Same id + same metadata → fine.
        register_category(c)
        # And the registry doesn't grow.
        self.assertEqual(
            len([x for x in all_categories() if x.id == "performance"]),
            1)

    def test_register_category_rejects_conflict(self):
        from azurik_mod.patching.category import (
            Category, register_category)
        with self.assertRaises(ValueError) as cm:
            register_category(Category("performance", "Different",
                                       "clash", 999))
        self.assertIn("performance", str(cm.exception))

    def test_ensure_category_auto_creates(self):
        from azurik_mod.patching.category import (
            all_categories, ensure_category)
        ensure_category("brand_new")
        ids = {c.id for c in all_categories()}
        self.assertIn("brand_new", ids)

    def test_auto_created_title_is_humanised(self):
        """``some_plugin_id`` → ``"Some Plugin Id"`` for a sensible
        default tab label without requiring the plugin to
        pre-register."""
        from azurik_mod.patching.category import ensure_category
        cat = ensure_category("my_experimental_mods")
        self.assertEqual(cat.title, "My Experimental Mods")


class FeatureCategoryField(unittest.TestCase):
    """Pack / Feature dataclass integration with the category system.

    Snapshots both global registries at ``setUp`` and restores them in
    ``tearDown`` so test-local packs / categories don't leak into the
    real registry used by the rest of the suite.
    """

    def setUp(self) -> None:
        # Import shipped packs first so they're in the snapshot.
        import azurik_mod.patches  # noqa: F401
        from azurik_mod.patching.category import _REGISTRY as _CREG
        from azurik_mod.patching.registry import _REGISTRY as _PREG
        self._cat_snapshot = dict(_CREG)
        self._pack_snapshot = dict(_PREG)
        # Fresh slate inside the test: clear packs (keep builtin cats).
        _PREG.clear()

    def tearDown(self) -> None:
        from azurik_mod.patching.category import _REGISTRY as _CREG
        from azurik_mod.patching.registry import _REGISTRY as _PREG
        _CREG.clear(); _CREG.update(self._cat_snapshot)
        _PREG.clear(); _PREG.update(self._pack_snapshot)

    def _make_noop_pack(self, *, name: str = "t", category: str = "other"):
        from azurik_mod.patching.registry import Feature, register_feature
        return register_feature(Feature(
            name=name, description="test", sites=[],
            apply=lambda xbe: None, category=category))

    def test_default_category_is_other(self):
        pack = self._make_noop_pack(name="def")
        self.assertEqual(pack.category, "other")

    def test_explicit_category_kept(self):
        pack = self._make_noop_pack(name="x", category="performance")
        self.assertEqual(pack.category, "performance")

    def test_register_auto_creates_unknown_category(self):
        from azurik_mod.patching.category import all_categories
        self._make_noop_pack(name="fresh", category="my_plugin")
        ids = {c.id for c in all_categories()}
        self.assertIn("my_plugin", ids,
            msg="register_feature must ensure the category id exists")

    def test_packs_by_category_groups_by_id(self):
        from azurik_mod.patching.registry import packs_by_category
        self._make_noop_pack(name="a", category="player")
        self._make_noop_pack(name="b", category="player")
        self._make_noop_pack(name="c", category="qol")
        groups = packs_by_category()
        self.assertEqual([p.name for p in groups["player"]], ["a", "b"])
        self.assertEqual([p.name for p in groups["qol"]], ["c"])

    def test_packs_by_category_preserves_category_order(self):
        """Iteration order follows Category.order so the GUI doesn't
        need a second sort."""
        from azurik_mod.patching.registry import packs_by_category
        self._make_noop_pack(name="a", category="qol")
        self._make_noop_pack(name="b", category="performance")
        ordering = list(packs_by_category().keys())
        self.assertLess(ordering.index("performance"),
                        ordering.index("qol"))

    def test_packs_by_category_includes_empty_categories(self):
        """Builtin categories with zero packs still appear (GUI
        decides whether to hide them)."""
        from azurik_mod.patching.registry import packs_by_category
        # No packs registered at all yet.
        groups = packs_by_category()
        for needed in ("performance", "player", "boot", "qol", "other"):
            self.assertIn(needed, groups)
            self.assertEqual(groups[needed], [])


class ShippedPackCategorisation(unittest.TestCase):
    """Pin the specific category each shipped pack lives in so a
    future refactor can't silently move them around."""

    def setUp(self) -> None:
        # Keep the global registry populated (don't clear_registry).
        import azurik_mod.patches  # noqa: F401

    def test_fps_unlock_is_performance(self):
        """fps_unlock lives in ``performance`` — it's the flagship
        performance mod.  The original D3D-BSOD-on-death concern
        turned out to be a pre-existing engine bug unrelated to
        the patch (reproducible on vanilla 30 FPS too), so the
        patch stays in the expected category."""
        from azurik_mod.patching.registry import get_pack
        self.assertEqual(get_pack("fps_unlock").category, "performance")

    def test_randomizer_pools_are_in_randomize(self):
        """All five shuffle pools live in the ``randomize`` category
        so they surface on the Patches page's Randomize tab in
        addition to the dedicated Randomize page."""
        from azurik_mod.patching.registry import get_pack
        for name in ("rand_major", "rand_keys", "rand_gems",
                     "rand_barriers", "rand_connections"):
            self.assertEqual(get_pack(name).category, "randomize",
                msg=f"{name} should be in the randomize category")

    def test_player_physics_is_player(self):
        from azurik_mod.patching.registry import get_pack
        self.assertEqual(
            get_pack("player_physics").category, "player")

    def test_skip_logo_is_boot(self):
        from azurik_mod.patching.registry import get_pack
        self.assertEqual(
            get_pack("qol_skip_logo").category, "boot")

    def test_qol_trio_is_qol(self):
        from azurik_mod.patching.registry import get_pack
        for name in ("qol_gem_popups", "qol_other_popups",
                     "qol_pickup_anims"):
            self.assertEqual(get_pack(name).category, "qol")


class PackBrowserRendersTabsPerCategory(unittest.TestCase):
    """End-to-end: instantiate the real GUI widget and assert it
    renders one tab per non-empty category in the correct order.

    Uses a headless ``tk.Tk()`` + ``withdraw()`` so the suite runs
    on any CI host.  Skips when ``_tkinter`` isn't available.
    """

    def setUp(self) -> None:
        try:
            import tkinter as tk  # noqa: F401
        except ImportError:
            self.skipTest("tkinter not available")
        import azurik_mod.patches  # noqa: F401
        self._tk = __import__("tkinter")
        self._root = self._tk.Tk()
        self._root.withdraw()

    def tearDown(self) -> None:
        self._root.destroy()

    def test_empty_categories_hidden(self):
        """``other`` is builtin but ships with zero packs — the tab
        must NOT appear in the rendered notebook."""
        from azurik_mod.patching.registry import all_packs
        from gui.widgets import PackBrowser
        browser = PackBrowser(self._root, all_packs(), {})
        self.assertNotIn("other", browser.tabs())

    def test_tabs_in_category_order(self):
        """Shipped packs produce 5 tabs, in the registered
        Category.order order: performance (10) → player (20) →
        boot (30) → qol (40) → randomize (50).

        ``experimental`` is registered but currently has no shipped
        packs (the sole occupant ``enable_dev_menu`` was retired
        April 2026 — see ``tests/test_tier3_tools.py`` module
        docstring) so the browser correctly hides the tab.

        Note: this tests the RAW ``PackBrowser`` behaviour
        (every non-empty category renders).  The Patches PAGE
        deliberately hides ``randomize`` — see
        ``gui/pages/patches.py``'s ``HIDDEN_CATEGORIES`` +
        ``PatchesPageHidesRandomize`` below for the page-level
        assertion.
        """
        from azurik_mod.patching.registry import all_packs
        from gui.widgets import PackBrowser
        browser = PackBrowser(self._root, all_packs(), {})
        self.assertEqual(
            browser.tabs(),
            ["performance", "player", "boot", "qol", "randomize"])

    def test_tab_titles_humanised(self):
        from azurik_mod.patching.registry import all_packs
        from gui.widgets import PackBrowser
        browser = PackBrowser(self._root, all_packs(), {})
        titles = browser.tab_titles()
        self.assertEqual(titles[0], "Performance")
        self.assertEqual(titles[1], "Player")
        self.assertIn("Boot", titles[2])
        self.assertIn("Quality of Life", titles[3])
        self.assertEqual(titles[4], "Randomize")
        # ``experimental`` has no shipped packs post-dev_menu
        # retirement; the browser hides the tab entirely.
        self.assertEqual(len(titles), 5)

    def test_parametric_sliders_rendered_inside_their_tab(self):
        """``player_physics`` lives in the Player tab AND exposes 4
        sliders (gravity, walk, roll, swim).  The browser must
        create one ParametricSlider per site and register it under
        the (pack, param) key.

        The 3.0 multiplier that the ``roll`` slider targets was
        previously labelled ``run`` but that was a documentation
        mistake — see docs/LEARNINGS.md § "Roll, not run".  Swim
        was added April 2026 after we found the dedicated 10.0
        FMUL in FUN_0008b700."""
        from azurik_mod.patching.registry import all_packs
        from gui.widgets import PackBrowser
        params: dict = {}
        browser = PackBrowser(self._root, all_packs(), {},
                              pack_params=params)
        slider_keys = sorted(browser.sliders().keys())
        self.assertEqual(
            slider_keys,
            [("player_physics", "air_control_scale"),
             ("player_physics", "climb_speed_scale"),
             ("player_physics", "flap_height_scale"),
             ("player_physics", "gravity"),
             ("player_physics", "jump_speed_scale"),
             ("player_physics", "roll_speed_scale"),
             ("player_physics", "swim_speed_scale"),
             ("player_physics", "walk_speed_scale")])
        # Initial values mirrored into pack_params.
        self.assertIn("player_physics", params)
        self.assertEqual(len(params["player_physics"]), 8)

    def test_plugin_category_gets_its_own_tab(self):
        """Simulate a plugin: register a category + a pack referencing
        it, then re-instantiate the browser and verify the new tab
        appears automatically without any GUI code changes."""
        from azurik_mod.patching.category import (
            Category, register_category)
        from azurik_mod.patching.registry import (
            Feature, all_packs, register_feature)

        register_category(Category(
            id="cheats", title="Cheats",
            description="Plugin-provided cheat mods.",
            order=50))
        register_feature(Feature(
            name="cheats_god_mode",
            description="Immortality toggle.",
            sites=[],
            apply=lambda xbe: None,
            category="cheats",
        ))

        from gui.widgets import PackBrowser
        browser = PackBrowser(self._root, all_packs(), {})
        self.assertIn("cheats", browser.tabs())
        self.assertIn("Cheats", browser.tab_titles())

        # Clean up so other tests don't see the extra pack/category.
        from azurik_mod.patching.registry import _REGISTRY
        _REGISTRY.pop("cheats_god_mode", None)
        from azurik_mod.patching.category import _REGISTRY as _CREG
        _CREG.pop("cheats", None)

    def test_select_raises_named_tab(self):
        from azurik_mod.patching.registry import all_packs
        from gui.widgets import PackBrowser
        browser = PackBrowser(self._root, all_packs(), {})
        # Just confirm it doesn't blow up; selection state is managed
        # by Tk.
        browser.select("player")
        browser.select("nonexistent")  # should be a no-op, not raise


@unittest.skipUnless(True, "tkinter required")
class PatchesPageHidesRandomize(unittest.TestCase):
    """The Patches PAGE (not just the underlying PackBrowser)
    must NOT render a Randomize tab — shuffle pools live on the
    dedicated Randomize page + we don't want to surface them
    twice in the GUI.

    This is the guard that catches the regression if someone
    accidentally drops the HIDDEN_CATEGORIES filter in
    ``gui/pages/patches.py``.
    """

    def setUp(self) -> None:
        try:
            import tkinter as tk  # noqa: F401
        except ImportError:
            self.skipTest("tkinter not available")
        import azurik_mod.patches  # noqa: F401
        self._tk = __import__("tkinter")
        self._root = self._tk.Tk()
        self._root.withdraw()

    def tearDown(self) -> None:
        self._root.destroy()

    def test_patches_page_omits_randomize_tab(self):
        """Build a real PatchesPage against a lightweight app
        stub, then assert the rendered tab strip has no
        'randomize' entry."""
        from gui.pages.patches import HIDDEN_CATEGORIES, PatchesPage

        class _AppStub:
            class _State:
                enabled_packs: dict = {}
                pack_params: dict = {}

                def set_pack(self, name, value):
                    self.enabled_packs[name] = value
            state = _State()

        page = PatchesPage(self._root, _AppStub())
        self.assertNotIn("randomize", page.tabs(),
            msg="Patches page must NOT render the randomize tab "
                "(it lives on the dedicated Randomize page)")
        self.assertIn("randomize", HIDDEN_CATEGORIES)

    def test_patches_page_still_shows_other_five_tabs(self):
        """Hiding ``randomize`` must not accidentally hide anyone
        else.  After ``enable_dev_menu`` was retired (April 2026)
        the ``experimental`` category has no shipped packs so
        the browser correctly hides its tab too; the remaining
        4 categories must still render."""
        from gui.pages.patches import PatchesPage

        class _AppStub:
            class _State:
                enabled_packs: dict = {}
                pack_params: dict = {}

                def set_pack(self, name, value):
                    self.enabled_packs[name] = value
            state = _State()

        page = PatchesPage(self._root, _AppStub())
        self.assertEqual(
            page.tabs(),
            ["performance", "player", "boot", "qol"],
            msg="Dropping randomize must not drop any other tabs")

    def test_randomize_packs_still_registered(self):
        """Hiding the tab must NOT affect registration — the
        five ``rand_*`` features must still be in the global
        registry so the backend + Randomize page see them."""
        from azurik_mod.patching.registry import all_packs
        randomize_packs = {p.name for p in all_packs()
                           if p.category == "randomize"}
        self.assertEqual(
            randomize_packs,
            {"rand_major", "rand_keys", "rand_gems",
             "rand_barriers", "rand_connections"},
            msg="Randomize pool Features must stay in the "
                "registry even though we hide their Patches tab")


if __name__ == "__main__":
    unittest.main()
