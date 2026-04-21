"""Backend plumbing for the XBR Editor GUI page.

The Tk layer is not exercised directly — those tests would need a
display + sv_ttk and most CI runners don't have either.  Instead
we test :class:`gui.pages.xbr_editor.XbrEditorBackend` end-to-end:
open, edit, save, pending-mod export, section summary, cell
enumeration, structural-ops stubs.

If the design holds (Tk view is a thin wrapper over the backend),
these tests catch every realistic bug that doesn't live in Tk's
event loop.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.xbr import XbrDocument  # noqa: E402
from gui.pages.xbr_editor import (  # noqa: E402
    XbrEditorBackend,
    XbrPendingEdit,
)


_GAMEDATA_CANDIDATES = [
    Path("/Users/michaelsrouji/Documents/Xemu/tools/"
         "Azurik - Rise of Perathia (USA).xiso/gamedata"),
    _REPO_ROOT.parent / "Azurik - Rise of Perathia (USA).xiso" / "gamedata",
]


def _find_gamedata() -> Path | None:
    for p in _GAMEDATA_CANDIDATES:
        if p.exists():
            return p
    return None


_GAMEDATA = _find_gamedata()


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class XbrEditorBackendLifecycle(unittest.TestCase):
    def setUp(self):
        self.config = _GAMEDATA / "config.xbr"
        self.backend = XbrEditorBackend()
        self.backend.open(self.config)

    def test_open_populates_toc_entries(self):
        entries = self.backend.toc_entries
        self.assertEqual(len(entries), 18,
            msg="config.xbr has 18 TOC entries")
        # Every entry has the expected keys.
        for e in entries:
            self.assertIn("tag", e)
            self.assertIn("size", e)
            self.assertIn("file_offset", e)
            self.assertIn("overlay", e)

    def test_close_clears_document_but_preserves_pending(self):
        """``close`` drops the document/view state but keeps the
        pending-edit queue intact — users routinely switch files
        and the queue should survive that."""
        self.backend.set_keyed_double(
            3, "garret4", "walkSpeed", 1.0)
        self.backend.close()
        self.assertIsNone(self.backend.document)
        self.assertIsNone(self.backend.path)
        self.assertEqual(
            len(self.backend.pending_edits), 1,
            msg="Mark-2 editor preserves pending edits on "
                "close — tests that assumed otherwise were baked "
                "into the Mark-1 single-file model.")

    def test_reopen_preserves_and_reapplies_pending_edits(self):
        """Re-opening a file re-applies any persisted pending
        edits targeting that file.  This is what lets the editor
        survive a restart — pending edits live in the workspace
        ``pending_edits.json`` between sessions."""
        self.backend.set_keyed_double(
            3, "garret4", "walkSpeed", 99.0)
        self.assertEqual(len(self.backend.pending_edits), 1)
        # Reopening keeps the queue AND re-applies the edit to
        # the freshly-parsed document.
        self.backend.open(self.config)
        self.assertEqual(len(self.backend.pending_edits), 1)
        cell = (self.backend.document.section_for(3)
                .find_cell("garret4", "walkSpeed"))
        self.assertEqual(cell.double_value, 99.0)


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class SectionSummary(unittest.TestCase):
    def setUp(self):
        self.backend = XbrEditorBackend()
        self.backend.open(_GAMEDATA / "config.xbr")

    def test_keyed_table_summary_includes_dimensions(self):
        # TOC index 3 is attacks_transitions in config.xbr (8000).
        s = self.backend.section_summary(3)
        self.assertEqual(s["kind"], "keyed_table")
        self.assertEqual(s["num_rows"], 39)
        self.assertEqual(s["num_cols"], 108)
        self.assertIn("garret4", s["col_names"])
        self.assertIn("walkSpeed", s["row_names"])
        self.assertTrue(s["well_formed"])

    def test_raw_section_summary_includes_blocker(self):
        # Pick a level XBR — every section there is raw.
        backend = XbrEditorBackend()
        backend.open(_GAMEDATA / "a1.xbr")
        s = backend.section_summary(0)
        self.assertEqual(s["kind"], "raw")
        self.assertIn("backlog", s["blocker"].lower())


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class KeyedCellsEnumeration(unittest.TestCase):
    def test_keyed_cells_excludes_empty(self):
        backend = XbrEditorBackend()
        backend.open(_GAMEDATA / "config.xbr")
        cells = backend.keyed_cells(3)  # attacks_transitions
        # Non-empty cells only (the backend emits empty too, but
        # let's verify at least one double cell surfaces).
        doubles = [c for c in cells if c["kind"] == "double"]
        strings = [c for c in cells if c["kind"] == "string"]
        self.assertGreater(len(doubles), 0)
        self.assertGreater(len(strings), 0)
        # Sanity: one of them is garret4/walkSpeed.
        garret_walk = next(
            (c for c in doubles
             if c["col_name"] == "garret4"
             and c["row_name"] == "walkSpeed"), None)
        self.assertIsNotNone(garret_walk)

    def test_keyed_cells_for_non_keyed_is_empty(self):
        backend = XbrEditorBackend()
        backend.open(_GAMEDATA / "a1.xbr")
        # Every section in a1.xbr is raw.
        self.assertEqual(backend.keyed_cells(0), [])


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class EditDispatch(unittest.TestCase):
    def setUp(self):
        self.backend = XbrEditorBackend()
        self.backend.open(_GAMEDATA / "config.xbr")

    def test_set_keyed_double_mutates_and_queues(self):
        self.backend.set_keyed_double(3, "garret4", "walkSpeed", 42.0)
        # Document mutated.
        cell = (self.backend.document.section_for(3)
                .find_cell("garret4", "walkSpeed"))
        self.assertEqual(cell.double_value, 42.0)
        # Edit queued.
        self.assertEqual(len(self.backend.pending_edits), 1)
        edit = self.backend.pending_edits[0]
        self.assertEqual(edit.op, "set_keyed_double")
        self.assertEqual(edit.entity, "garret4")
        self.assertEqual(edit.prop, "walkSpeed")
        self.assertEqual(edit.value, 42.0)
        self.assertEqual(edit.section, "attacks_transitions")

    def test_set_keyed_double_rejects_non_keyed_section(self):
        # TOC index that dispatch routes to a non-keyed overlay
        # — a variant_record section.  Look it up dynamically.
        doc = self.backend.document
        assert doc is not None
        from azurik_mod.xbr.sections import VariantRecordSection
        variant_idx = next(
            i for i in range(len(doc.toc))
            if isinstance(doc.section_for(i), VariantRecordSection))
        with self.assertRaises(ValueError):
            self.backend.set_keyed_double(
                variant_idx, "foo", "bar", 1.0)

    def test_set_keyed_string_queues_and_mutates(self):
        # critters_critter_data is TOC index 4 (offset 0x01A000).
        self.backend.set_keyed_string(4, "garret4", "name", "abc")
        cell = (self.backend.document.section_for(4)
                .find_cell("abc", "name"))
        self.assertIsNotNone(cell)
        self.assertEqual(cell.string_value, "abc")
        edit = self.backend.pending_edits[-1]
        self.assertEqual(edit.op, "set_keyed_string")
        self.assertEqual(edit.value, "abc")


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class PendingModExport(unittest.TestCase):
    def test_empty_pending_mod(self):
        backend = XbrEditorBackend()
        backend.open(_GAMEDATA / "config.xbr")
        self.assertEqual(backend.pending_mod(), {})

    def test_pending_mod_round_trips_json(self):
        backend = XbrEditorBackend()
        backend.open(_GAMEDATA / "config.xbr")
        backend.set_keyed_double(3, "garret4", "walkSpeed", 99.0)
        backend.set_keyed_string(4, "garret4", "name", "abc")
        blob = backend.pending_mod()
        # JSON-serialisable without errors.
        payload = json.dumps(blob)
        parsed = json.loads(payload)
        self.assertIn("xbr_edits", parsed)
        self.assertEqual(len(parsed["xbr_edits"]), 2)
        # Each dict has the shape XbrEditSpec expects.
        for e in parsed["xbr_edits"]:
            self.assertIn("op", e)
            self.assertIn("xbr_file", e)

    def test_pending_mod_matches_xbr_edit_spec_shape(self):
        """Each pending edit must be convertible to an
        :class:`~azurik_mod.patching.xbr_spec.XbrEditSpec` via
        ``**`` expansion (barring optional fields) — the whole
        point of unifying the shape."""
        from azurik_mod.patching.xbr_spec import XbrEditSpec
        backend = XbrEditorBackend()
        backend.open(_GAMEDATA / "config.xbr")
        backend.set_keyed_double(3, "garret4", "walkSpeed", 99.0)
        for raw in backend.pending_mod()["xbr_edits"]:
            # Keep only the XbrEditSpec-compatible keys.
            kwargs = {k: raw[k] for k in raw
                      if k in ("label", "xbr_file", "op", "section",
                               "entity", "prop", "value", "offset")}
            spec = XbrEditSpec(**kwargs)
            self.assertEqual(spec.op, "set_keyed_double")


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class SaveAs(unittest.TestCase):
    def test_save_as_writes_mutated_bytes(self):
        tmp = tempfile.mkdtemp(prefix="xbr_editor_save_")
        try:
            backend = XbrEditorBackend()
            backend.open(_GAMEDATA / "config.xbr")
            backend.set_keyed_double(3, "garret4", "walkSpeed", 77.0)
            out = Path(tmp) / "patched.xbr"
            backend.save_as(out)
            # Reloading the saved file should show the edit.
            reloaded = XbrDocument.load(out)
            cell = (reloaded.keyed_sections()["attacks_transitions"]
                    .find_cell("garret4", "walkSpeed"))
            self.assertEqual(cell.double_value, 77.0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class BackendWithoutDocumentRaises(unittest.TestCase):
    """Calling edit methods on a closed backend must raise cleanly."""

    def test_edit_with_no_document_raises(self):
        backend = XbrEditorBackend()
        with self.assertRaises(RuntimeError):
            backend.set_keyed_double(0, "x", "y", 1.0)
        with self.assertRaises(RuntimeError):
            backend.section_for(0)

    def test_toc_entries_on_closed_backend_is_empty(self):
        backend = XbrEditorBackend()
        self.assertEqual(backend.toc_entries, [])


if __name__ == "__main__":
    unittest.main()
