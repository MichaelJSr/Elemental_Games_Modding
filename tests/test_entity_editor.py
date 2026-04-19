"""Tests for the Entity Editor tab + Build-page merge wiring.

Covers the critical orphan-fix (editor edits actually reach the
build pipeline) plus the UX refinements:

- ``_format_entity_label`` / ``_unformat_entity_label`` decoration
  is reversible and survives a filter refresh.
- ``get_pending_mod`` / Import JSON round-trip preserves section /
  entity / property triples AND the ``_keyed_patches`` block.
- The Build page's new ``_merge_config_edits`` correctly combines
  file-sourced edits with editor-sourced edits, with editor-wins
  on per-cell conflict.

Tests that instantiate a real Tk window are skipped in headless
environments via a `:unittest.skipUnless` guard — the merge-logic
test runs unconditionally since it operates on plain dicts.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Import-only: verify the methods exist + are reachable even when Tk isn't.
# ---------------------------------------------------------------------------


class EntityEditorSurfaceArea(unittest.TestCase):
    """The tab class must expose the expected methods even on a
    headless host where we can't actually construct a window."""

    def test_class_exposes_expected_methods(self):
        from gui.pages.entity_editor import EntityEditorTab
        for name in [
            "get_pending_mod",
            "get_edit_count",
            "_format_entity_label",
            "_unformat_entity_label",
            "_on_entity_change",
            "_refresh_entity_list",
            "_refresh_entity_edit_count",
            "_reset_entity",
            "_reset_edits",
            "_import_mod",
            "_export_mod",
        ]:
            self.assertTrue(
                hasattr(EntityEditorTab, name),
                msg=f"EntityEditorTab missing expected method {name!r}")


# ---------------------------------------------------------------------------
# Label decoration — pure-function tests on tiny fake instances.
# ---------------------------------------------------------------------------


class _FakeTab:
    """Minimal stand-in that carries the bits the label-formatter
    methods touch — no Tk, no pages.  Lets us exercise the pure-
    function bits of EntityEditorTab's label decoration without
    booting a window."""

    def __init__(self, section_key: str, edits: dict):
        self._section_key = section_key
        self._edits = edits

    def _get_section_info(self):
        return (self._section_key, "display", "keyed")


class LabelDecoration(unittest.TestCase):
    """``_format_entity_label`` prepends bullet + count; inverse
    strips them cleanly.  Idempotent on undecorated inputs."""

    def _mk(self, edits: dict) -> object:
        from gui.pages.entity_editor import EntityEditorTab
        fake = _FakeTab("critters_walking", edits)
        # Bind the unbound methods to our fake — equivalent to how
        # tk would bind them to a widget instance.
        fake._format_entity_label = EntityEditorTab._format_entity_label.__get__(
            fake, _FakeTab)
        fake._unformat_entity_label = EntityEditorTab._unformat_entity_label.__get__(
            fake, _FakeTab)
        return fake

    def test_no_edits_returns_raw_name(self):
        tab = self._mk({})
        self.assertEqual(tab._format_entity_label("goblin"), "goblin")

    def test_with_edits_prepends_bullet_and_count(self):
        tab = self._mk({"critters_walking": {"goblin": {"walkSpeed": 2.0,
                                                           "runSpeed": 4.0}}})
        self.assertEqual(tab._format_entity_label("goblin"), "● goblin (2)")
        # Singular count stays ``(1)``.
        tab2 = self._mk({"critters_walking": {"goblin": {"walkSpeed": 2.0}}})
        self.assertEqual(tab2._format_entity_label("goblin"), "● goblin (1)")

    def test_roundtrip_through_decoration(self):
        tab = self._mk({"critters_walking": {"goblin": {"walkSpeed": 2.0}}})
        decorated = tab._format_entity_label("goblin")
        self.assertEqual(tab._unformat_entity_label(decorated), "goblin")

    def test_unformat_passes_through_undecorated(self):
        tab = self._mk({})
        self.assertEqual(tab._unformat_entity_label("ogre"), "ogre")
        self.assertEqual(tab._unformat_entity_label("dragon_king"),
                         "dragon_king")

    def test_unformat_tolerates_manually_typed_parens(self):
        """If the user types a name that happens to have parens at
        the end (unlikely but possible), we shouldn't strip anything
        — only the bullet-prefixed form gets unformatted."""
        tab = self._mk({})
        # Starts with ●, has a count suffix → strip.
        self.assertEqual(tab._unformat_entity_label("● foo (5)"), "foo")
        # No bullet prefix → pass through (even with a trailing paren).
        self.assertEqual(tab._unformat_entity_label("foo (not-a-count)"),
                         "foo (not-a-count)")


# ---------------------------------------------------------------------------
# get_pending_mod shape — verified independently of the build merge.
# ---------------------------------------------------------------------------


class _FakeEditorForPendingMod:
    """Stand-in that carries just the fields ``get_pending_mod``
    reads — ``_edits`` dict + ``_keyed_tables`` lookup helper.
    Injects edits on the variant side only (keyed side lives in
    ``_keyed_patches``) so tests don't need a real table parser."""

    class _FakeTable:
        def get_value(self, entity, prop):
            return ("double", 1.0, 0xABCD00)  # fake cell offset

    def __init__(self, edits: dict):
        self._edits = edits
        self._keyed_tables = {
            "attacks_transitions": _FakeEditorForPendingMod._FakeTable(),
        }


class GetPendingModShape(unittest.TestCase):
    """Output conforms to the shape the CLI's ``--config-mod`` accepts."""

    def _mk(self, edits: dict):
        from gui.pages.entity_editor import EntityEditorTab
        fake = _FakeEditorForPendingMod(edits)
        fake.get_pending_mod = EntityEditorTab.get_pending_mod.__get__(
            fake, _FakeEditorForPendingMod)
        return fake

    def test_returns_none_when_no_edits(self):
        self.assertIsNone(self._mk({}).get_pending_mod())

    def test_variant_edits_land_in_sections(self):
        edits = {"critters_walking": {"goblin": {"walkSpeed": 2.5}}}
        mod = self._mk(edits).get_pending_mod()
        self.assertEqual(mod["format"], "grouped")
        self.assertIn("sections", mod)
        self.assertEqual(
            mod["sections"]["critters_walking"]["goblin"]["walkSpeed"], 2.5)

    def test_keyed_edits_land_in_keyed_patches(self):
        edits = {
            "attacks_transitions": {"goblin_stats": {"HP": 500.0}},
        }
        mod = self._mk(edits).get_pending_mod()
        self.assertIn("_keyed_patches", mod)
        self.assertEqual(
            mod["_keyed_patches"]["attacks_transitions"]
               ["goblin_stats"]["HP"], 500.0)


# ---------------------------------------------------------------------------
# Build-page merge: the critical orphan-fix test.
# ---------------------------------------------------------------------------


class BuildPageMergeConfigEdits(unittest.TestCase):
    """The newly-wired ``_merge_config_edits`` on BuildPage folds
    Entity-Editor edits into the CLI's ``--config-mod`` JSON.  This
    test exercises the merge logic directly against a fake tab —
    no Tk, no build pipeline — to verify:

    - Neither side has edits → returns the existing input unchanged
      (``None`` stays ``None``).
    - Only editor has edits → returns a fresh grouped-mod dict.
    - Only file has edits → passes through unchanged.
    - Both have edits on disjoint cells → merged union.
    - Both have edits on the SAME cell → editor wins (represents
      more-recent interactive state).
    - ``_keyed_patches`` blobs merge with the same rules.
    """

    def _make_merger(self, pending_mod):
        """Return a bound ``_merge_config_edits`` method that uses
        ``pending_mod`` as the editor's pending-mod return value."""
        # Minimal stand-in BuildPage: only needs .app.tab_entity with
        # a get_pending_mod() + get_edit_count(), and a ._log.append().
        from gui.pages.build import BuildPage

        class _FakeLog:
            def append(self, *args, **kw): pass

        class _FakeTab:
            def __init__(self, mod):
                self._mod = mod
            def get_pending_mod(self):
                return self._mod
            def get_edit_count(self):
                if not self._mod:
                    return 0
                return sum(
                    len(p)
                    for s in self._mod.get("sections", {}).values()
                    for p in s.values()
                ) + sum(
                    len(p)
                    for s in self._mod.get("_keyed_patches", {}).values()
                    for p in s.values()
                )

        class _FakeApp:
            def __init__(self, mod):
                self.tab_entity = _FakeTab(mod)

        class _FakeBuildPage:
            def __init__(self, mod):
                self.app = _FakeApp(mod)
                self._log = _FakeLog()

        fake = _FakeBuildPage(pending_mod)
        fake._merge_config_edits = BuildPage._merge_config_edits.__get__(
            fake, _FakeBuildPage)
        return fake

    def test_none_plus_none_is_none(self):
        fake = self._make_merger(None)
        self.assertIsNone(fake._merge_config_edits(None))

    def test_editor_only_produces_fresh_dict(self):
        editor_mod = {
            "sections": {"critters_walking": {"goblin": {"walkSpeed": 2.5}}},
        }
        fake = self._make_merger(editor_mod)
        merged = fake._merge_config_edits(None)
        self.assertIsNotNone(merged)
        self.assertEqual(
            merged["sections"]["critters_walking"]["goblin"]["walkSpeed"],
            2.5)
        # Original editor dict must NOT have been mutated.
        self.assertEqual(
            editor_mod["sections"]["critters_walking"]["goblin"]["walkSpeed"],
            2.5)

    def test_file_only_passes_through_when_editor_empty(self):
        file_mod = {"sections": {"damage": {"fire": {"value": 10}}}}
        fake = self._make_merger(None)
        merged = fake._merge_config_edits(file_mod)
        self.assertEqual(merged, file_mod)

    def test_disjoint_cells_are_unioned(self):
        file_mod = {"sections": {"critters_walking": {"ogre": {"HP": 100}}}}
        editor_mod = {
            "sections": {"critters_walking": {"goblin": {"walkSpeed": 2.5}}},
        }
        fake = self._make_merger(editor_mod)
        merged = fake._merge_config_edits(file_mod)
        self.assertEqual(
            merged["sections"]["critters_walking"]["ogre"]["HP"], 100)
        self.assertEqual(
            merged["sections"]["critters_walking"]["goblin"]["walkSpeed"],
            2.5)

    def test_editor_wins_on_per_cell_conflict(self):
        """If both sides set ``critters_walking/goblin/walkSpeed`` the
        editor's value must survive — it represents the more-recent
        interactive state."""
        file_mod = {
            "sections": {"critters_walking": {"goblin": {"walkSpeed": 1.0}}},
        }
        editor_mod = {
            "sections": {"critters_walking": {"goblin": {"walkSpeed": 2.5}}},
        }
        fake = self._make_merger(editor_mod)
        merged = fake._merge_config_edits(file_mod)
        self.assertEqual(
            merged["sections"]["critters_walking"]["goblin"]["walkSpeed"],
            2.5)

    def test_keyed_patches_merge_with_editor_wins(self):
        file_mod = {"_keyed_patches": {
            "attacks_transitions": {
                "goblin_stats": {"HP": 100, "speed": 5},
            }}}
        editor_mod = {"_keyed_patches": {
            "attacks_transitions": {
                "goblin_stats": {"HP": 500},  # override HP, keep speed
                "ogre_stats": {"HP": 800},    # brand new entity
            }}}
        fake = self._make_merger(editor_mod)
        merged = fake._merge_config_edits(file_mod)
        kp = merged["_keyed_patches"]["attacks_transitions"]
        # HP — editor overrides file.
        self.assertEqual(kp["goblin_stats"]["HP"], 500)
        # speed — only file had it.
        self.assertEqual(kp["goblin_stats"]["speed"], 5)
        # ogre — only editor had it.
        self.assertEqual(kp["ogre_stats"]["HP"], 800)

    def test_original_file_mod_is_not_mutated(self):
        """Deep-copy invariant: merging must not write through into
        the file-side input (caller may still want the unmerged dict
        for logging / diagnostics)."""
        file_mod = {"sections": {"critters_walking": {"goblin": {"walkSpeed": 1.0}}}}
        editor_mod = {"sections": {"critters_walking": {"goblin": {"walkSpeed": 9.0}}}}
        fake = self._make_merger(editor_mod)
        _ = fake._merge_config_edits(file_mod)
        self.assertEqual(
            file_mod["sections"]["critters_walking"]["goblin"]["walkSpeed"],
            1.0,
            msg="file_mod was mutated — the merge must return a fresh "
                "dict")


# ---------------------------------------------------------------------------
# Import round-trip — exercises the full `_import_mod` parser against
# a real JSON file on disk (no Tk; we just call the parsing code).
# ---------------------------------------------------------------------------


class ImportJsonRoundTrip(unittest.TestCase):
    """Export → Import preserves every (section, entity, property)
    triple in both variant and keyed shapes."""

    def _make_parser(self):
        """Build a minimal fake with just ``_import_mod`` bound +
        empty edit state / dummy widgets."""
        from gui.pages.entity_editor import EntityEditorTab

        class _FakeStatus:
            def __init__(self): self.text = ""
            def config(self, **kw): self.text = kw.get("text", "")

        class _Fake:
            def __init__(self):
                self._edits = {}
                self._status = _FakeStatus()
            def _rebuild_property_grid(self): pass
            def _update_edit_count(self): pass
        fake = _Fake()
        # We need to monkeypatch filedialog.askopenfilename + messagebox
        # so the method returns deterministically.
        return fake, EntityEditorTab._import_mod

    def test_import_merges_sections_and_keyed_patches(self):
        fake, _import_mod = self._make_parser()

        export = {
            "name": "smoke",
            "format": "grouped",
            "sections": {
                "critters_walking": {
                    "goblin": {"walkSpeed": 2.5, "runSpeed": 4.0},
                    "ogre": {"HP": 800},
                }
            },
            "_keyed_patches": {
                "attacks_transitions": {
                    "goblin_stats": {"damage": 15}
                }
            },
        }
        with tempfile.TemporaryDirectory(prefix="import_mod_") as tmp:
            path = Path(tmp) / "entity_edits.json"
            path.write_text(json.dumps(export))

            # Patch the file dialog to return our fixture path, and
            # messagebox / filedialog calls the method would issue.
            import gui.pages.entity_editor as mod_under_test
            orig_filedialog = mod_under_test.filedialog
            orig_messagebox = mod_under_test.messagebox
            try:
                class _FakeFd:
                    def askopenfilename(self, **kw): return str(path)
                    def asksaveasfilename(self, **kw): return ""
                class _FakeMb:
                    def showerror(self, *a, **kw): pass
                    def showinfo(self, *a, **kw): pass
                    def askyesno(self, *a, **kw): return True
                mod_under_test.filedialog = _FakeFd()
                mod_under_test.messagebox = _FakeMb()

                _import_mod(fake)
            finally:
                mod_under_test.filedialog = orig_filedialog
                mod_under_test.messagebox = orig_messagebox

        # Variant edits imported.
        self.assertEqual(
            fake._edits["critters_walking"]["goblin"]["walkSpeed"], 2.5)
        self.assertEqual(
            fake._edits["critters_walking"]["goblin"]["runSpeed"], 4.0)
        self.assertEqual(
            fake._edits["critters_walking"]["ogre"]["HP"], 800)
        # Keyed edits imported too.
        self.assertEqual(
            fake._edits["attacks_transitions"]["goblin_stats"]["damage"], 15)
        self.assertIn("Imported", fake._status.text)

    def test_malformed_entries_are_skipped_not_fatal(self):
        fake, _import_mod = self._make_parser()
        # Mix of good + bad entries.
        export = {
            "sections": {
                "critters_walking": {
                    "goblin": {"walkSpeed": 2.5, "broken": "not-a-number"},
                    "ogre": "not-a-dict",  # whole-entity malformed
                }
            },
        }
        with tempfile.TemporaryDirectory(prefix="import_bad_") as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps(export))
            import gui.pages.entity_editor as mod_under_test
            orig_fd, orig_mb = mod_under_test.filedialog, mod_under_test.messagebox
            try:
                class _FakeFd:
                    def askopenfilename(self, **kw): return str(path)
                class _FakeMb:
                    def showerror(self, *a, **kw): pass
                    def showinfo(self, *a, **kw): pass
                mod_under_test.filedialog = _FakeFd()
                mod_under_test.messagebox = _FakeMb()
                _import_mod(fake)
            finally:
                mod_under_test.filedialog = orig_fd
                mod_under_test.messagebox = orig_mb

        # Good edit came through.
        self.assertEqual(
            fake._edits["critters_walking"]["goblin"]["walkSpeed"], 2.5)
        # "broken" should be skipped, not imported.
        self.assertNotIn(
            "broken",
            fake._edits.get("critters_walking", {}).get("goblin", {}))
        # ogre was "not-a-dict" at the entity level → whole entry skipped.
        self.assertNotIn(
            "ogre", fake._edits.get("critters_walking", {}))
        self.assertIn("skipped", fake._status.text)


if __name__ == "__main__":
    unittest.main()
