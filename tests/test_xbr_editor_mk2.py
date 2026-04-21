"""Tests for the Mark-2 XBR Editor capabilities.

These cover the convenience / robustness features added after the
initial editor shipped: undo/redo, vanilla tracking, reset_cell /
reset_entity / reset_section, pending-edit persistence, session
restore, the 2D keyed_cells_grid emitter, and the workspace
auto-provisioning helper.

Split out from :mod:`tests.test_xbr_editor_gui` so the Mark-1 /
Mark-2 boundary stays legible in git blame.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gui.pages.xbr_editor import (  # noqa: E402
    XbrEditorBackend,
    XbrPendingEdit,
)
from gui.xbr_workspace import (  # noqa: E402
    SessionState,
    XbrFileInfo,
    XbrWorkspace,
    _candidate_extract_siblings,
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


def _scratch_workspace(tmpdir: Path) -> XbrWorkspace:
    """Build an isolated :class:`XbrWorkspace` under ``tmpdir`` with
    a copy of the real ``config.xbr`` so persistence tests can
    write files without stomping on the shared
    ``.xbr_workspace/``."""
    ws = XbrWorkspace(tmpdir)
    (tmpdir / "game" / "gamedata").mkdir(parents=True)
    shutil.copy2(_GAMEDATA / "config.xbr",
                 tmpdir / "game" / "gamedata" / "config.xbr")
    return ws


# ---------------------------------------------------------------------------
# Workspace module
# ---------------------------------------------------------------------------


class SiblingDetection(unittest.TestCase):
    """The sibling-of-iso heuristic that lets users pick up an
    already-extracted ISO without configuring anything."""

    def test_iso_with_sibling_xiso_dir_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            # ``resolve()`` here matters on macOS where ``/tmp`` is
            # a symlink to ``/private/tmp`` — the helper's own
            # ``iso.resolve()`` walks the link so the candidate it
            # returns is real-path-canonical.  Match that.
            root = Path(tmp).resolve()
            iso = root / "foo.iso"
            iso.write_bytes(b"")
            extract = root / "foo.xiso"
            (extract / "gamedata").mkdir(parents=True)
            (extract / "gamedata" / "config.xbr").write_bytes(
                b"xobx")
            candidates = _candidate_extract_siblings(iso)
            self.assertIn(extract, candidates)

    def test_iso_file_with_double_xiso_suffix_detected(self):
        """Real-world naming: ``foo.xiso.iso`` + ``foo.xiso/``
        alongside it — mirrors what the user on this machine has."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            iso_dir = root / "nested"
            iso_dir.mkdir()
            iso = iso_dir / "foo.xiso.iso"
            iso.write_bytes(b"")
            extract = root / "foo.xiso"
            (extract / "gamedata").mkdir(parents=True)
            candidates = _candidate_extract_siblings(iso)
            self.assertIn(extract, candidates)

    def test_no_siblings_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            iso = Path(tmp) / "foo.iso"
            iso.write_bytes(b"")
            self.assertEqual(_candidate_extract_siblings(iso), [])


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class WorkspaceDiscovery(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="xbr_ws_test_"))
        self.ws = XbrWorkspace(self._tmp)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_empty_workspace_yields_no_files(self):
        self.assertEqual(self.ws.discover_xbr_files(), [])
        self.assertFalse(self.ws.has_game_files())

    def test_ensure_game_files_via_sibling(self):
        # Build a fake ISO + sibling that ensure_game_files will
        # detect.
        iso_dir = self._tmp / "iso"
        iso_dir.mkdir()
        iso = iso_dir / "test.iso"
        iso.write_bytes(b"")
        extract = self._tmp / "test.xiso"
        (extract / "gamedata").mkdir(parents=True)
        shutil.copy2(_GAMEDATA / "config.xbr",
                     extract / "gamedata" / "config.xbr")
        self.assertTrue(
            self.ws.ensure_game_files(iso, allow_network=False))
        self.assertTrue(self.ws.has_game_files())
        files = self.ws.discover_xbr_files()
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].filename, "config.xbr")
        self.assertEqual(files[0].kind, "config")

    def test_discovery_classifies_kinds(self):
        gd = self._tmp / "game" / "gamedata"
        gd.mkdir(parents=True)
        (gd / "config.xbr").write_bytes(b"xobx")
        (gd / "a1.xbr").write_bytes(b"xobx")
        (gd / "characters.xbr").write_bytes(b"xobx")
        (gd / "index").mkdir()
        (gd / "index" / "index.xbr").write_bytes(b"xobx")
        files = {f.filename: f for f in self.ws.discover_xbr_files()}
        self.assertEqual(files["config.xbr"].kind, "config")
        self.assertEqual(files["a1.xbr"].kind, "level")
        self.assertEqual(files["characters.xbr"].kind, "data")
        self.assertEqual(files["index.xbr"].kind, "index")
        self.assertEqual(
            files["index.xbr"].relative_path, "index/index.xbr")


class WorkspacePersistence(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="xbr_ws_pers_"))
        self.ws = XbrWorkspace(self._tmp)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_pending_edits_round_trip(self):
        edits = [
            {"op": "set_keyed_double",
             "xbr_file": "config.xbr",
             "section": "s", "entity": "e", "prop": "p",
             "value": 1.5, "label": "x"},
        ]
        self.ws.save_pending_edits(edits)
        self.assertTrue(self.ws.pending_path.exists())
        loaded = self.ws.load_pending_edits()
        self.assertEqual(loaded, edits)

    def test_save_empty_deletes_file(self):
        self.ws.save_pending_edits([
            {"op": "set_keyed_double", "xbr_file": "c"},
        ])
        self.assertTrue(self.ws.pending_path.exists())
        self.ws.save_pending_edits([])
        self.assertFalse(self.ws.pending_path.exists())

    def test_session_round_trip(self):
        self.ws.save_session(SessionState(
            last_file="config.xbr", last_section_index=3,
            last_entity="garret4", last_property="walkSpeed"))
        restored = self.ws.load_session()
        self.assertEqual(restored.last_file, "config.xbr")
        self.assertEqual(restored.last_section_index, 3)
        self.assertEqual(restored.last_entity, "garret4")
        self.assertEqual(restored.last_property, "walkSpeed")

    def test_load_missing_session_is_blank(self):
        restored = self.ws.load_session()
        self.assertIsNone(restored.last_file)
        self.assertIsNone(restored.last_section_index)

    def test_malformed_session_is_blank(self):
        self.ws.session_path.write_text("not json")
        self.assertIsNone(self.ws.load_session().last_file)


# ---------------------------------------------------------------------------
# Vanilla tracking + modified detection
# ---------------------------------------------------------------------------


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class VanillaTracking(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="xbr_vanilla_"))
        self.ws = _scratch_workspace(self._tmp)
        self.backend = XbrEditorBackend(self.ws)
        self.backend.open(
            self.ws.gamedata_dir / "config.xbr")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_vanilla_raw_captured_on_open(self):
        self.assertIsNotNone(self.backend.vanilla_raw)
        self.assertEqual(
            self.backend.vanilla_raw,
            (self.ws.gamedata_dir / "config.xbr").read_bytes())

    def test_cell_reports_unmodified_initially(self):
        entries = self.backend.toc_entries
        for e in entries:
            self.assertFalse(
                e["modified"],
                msg=f"section {e['tag']} reported modified on "
                    "a fresh open")

    def test_cell_modified_after_edit(self):
        self.backend.set_keyed_double(
            3, "garret4", "walkSpeed", 99.0)
        e = self.backend.toc_entries[3]
        self.assertTrue(e["modified"])
        grid = self.backend.keyed_cells_grid(3)
        col_idx = grid["col_names"].index("garret4")
        row_idx = grid["row_names"].index("walkSpeed")
        cell = grid["cells"][col_idx][row_idx]
        self.assertIsNotNone(cell)
        self.assertTrue(cell["modified"])
        self.assertNotEqual(cell["vanilla_value"], 99.0)


# ---------------------------------------------------------------------------
# Undo / redo
# ---------------------------------------------------------------------------


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class UndoRedo(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="xbr_undo_"))
        self.ws = _scratch_workspace(self._tmp)
        self.backend = XbrEditorBackend(self.ws)
        self.backend.open(
            self.ws.gamedata_dir / "config.xbr")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _current_walk_speed(self) -> float:
        return (self.backend.document.section_for(3)
                .find_cell("garret4", "walkSpeed").double_value)

    def test_undo_restores_previous_value(self):
        before = self._current_walk_speed()
        self.backend.set_keyed_double(
            3, "garret4", "walkSpeed", 42.0)
        self.assertEqual(self._current_walk_speed(), 42.0)
        self.assertTrue(self.backend.can_undo())
        self.assertTrue(self.backend.undo())
        self.assertEqual(self._current_walk_speed(), before)
        self.assertEqual(self.backend.pending_edits, [])

    def test_redo_replays_value(self):
        self.backend.set_keyed_double(
            3, "garret4", "walkSpeed", 42.0)
        self.backend.undo()
        self.assertTrue(self.backend.can_redo())
        self.assertTrue(self.backend.redo())
        self.assertEqual(self._current_walk_speed(), 42.0)

    def test_fresh_edit_clears_redo_stack(self):
        self.backend.set_keyed_double(
            3, "garret4", "walkSpeed", 1.0)
        self.backend.undo()
        self.assertTrue(self.backend.can_redo())
        # A brand new edit should invalidate the redo stack.
        self.backend.set_keyed_double(
            3, "garret4", "walkSpeed", 2.0)
        self.assertFalse(self.backend.can_redo())

    def test_undo_with_empty_stack_returns_false(self):
        self.assertFalse(self.backend.undo())
        self.assertFalse(self.backend.redo())

    def test_same_cell_edited_twice_coalesces_in_pending(self):
        """Repeated edits to the same cell shouldn't explode the
        pending list — the editor collapses them into one entry
        with the latest value."""
        self.backend.set_keyed_double(
            3, "garret4", "walkSpeed", 1.0)
        self.backend.set_keyed_double(
            3, "garret4", "walkSpeed", 2.0)
        self.backend.set_keyed_double(
            3, "garret4", "walkSpeed", 3.0)
        self.assertEqual(
            len(self.backend.pending_edits), 1,
            msg="Same-cell edits should coalesce.")
        self.assertEqual(
            self.backend.pending_edits[0].value, 3.0)


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class Reset(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="xbr_reset_"))
        self.ws = _scratch_workspace(self._tmp)
        self.backend = XbrEditorBackend(self.ws)
        self.backend.open(
            self.ws.gamedata_dir / "config.xbr")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_reset_cell_clears_pending_for_cell(self):
        self.backend.set_keyed_double(
            3, "garret4", "walkSpeed", 42.0)
        self.assertEqual(
            len(self.backend.pending_edits), 1)
        ok = self.backend.reset_cell(
            3, "garret4", "walkSpeed")
        self.assertTrue(ok)
        self.assertEqual(
            len(self.backend.pending_edits), 0)
        self.assertFalse(
            self.backend.toc_entries[3]["modified"])

    def test_reset_cell_unmodified_is_noop(self):
        ok = self.backend.reset_cell(
            3, "garret4", "walkSpeed")
        self.assertFalse(ok)

    def test_reset_section_reverts_every_cell_in_section(self):
        # Deliberately pick values we KNOW differ from vanilla —
        # on this ISO flicken's vanilla walkSpeed is exactly 2.0
        # so re-setting it to 2.0 would be a no-op and the reset
        # count would drop to 1.
        self.backend.set_keyed_double(
            3, "garret4", "walkSpeed", 999.0)
        self.backend.set_keyed_double(
            3, "flicken", "walkSpeed", 999.0)
        count = self.backend.reset_section(3)
        self.assertGreaterEqual(count, 2)
        self.assertFalse(
            self.backend.toc_entries[3]["modified"])
        self.assertFalse([
            e for e in self.backend.pending_edits
            if e.section == "attacks_transitions"])

    def test_reset_file_reverts_all_pending(self):
        self.backend.set_keyed_double(
            3, "garret4", "walkSpeed", 1.0)
        self.backend.set_keyed_double(
            4, "garret4", "hitPoints", 9.0)
        count = self.backend.reset_file()
        self.assertGreaterEqual(count, 2)
        self.assertEqual(self.backend.pending_edits, [])


# ---------------------------------------------------------------------------
# Persistent state round-trip
# ---------------------------------------------------------------------------


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class PersistentState(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="xbr_persist_"))
        self.ws = _scratch_workspace(self._tmp)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_edit_persists_across_backend_restart(self):
        """Edit → save_persistent_state → fresh backend →
        load_persistent_state → edit re-appears in
        ``pending_edits`` AND is replayed on open()."""
        backend1 = XbrEditorBackend(self.ws)
        backend1.open(self.ws.gamedata_dir / "config.xbr")
        backend1.set_keyed_double(
            3, "garret4", "walkSpeed", 77.0)
        backend1.save_persistent_state(SessionState(
            last_file="config.xbr", last_section_index=3))

        backend2 = XbrEditorBackend(self.ws)
        restored = backend2.load_persistent_state()
        self.assertEqual(restored.last_file, "config.xbr")
        self.assertEqual(restored.last_section_index, 3)
        self.assertEqual(len(backend2.pending_edits), 1)
        # Open and verify the edit is re-applied.
        backend2.open(self.ws.gamedata_dir / "config.xbr")
        self.assertEqual(
            backend2.document.section_for(3)
            .find_cell("garret4", "walkSpeed").double_value, 77.0)

    def test_undo_works_across_sessions(self):
        """Persisted pending edits populate the undo stack on
        :meth:`open` so ``Ctrl+Z`` can reverse them after a
        restart — without this a user who reopens the GUI can't
        walk their history backwards."""
        b1 = XbrEditorBackend(self.ws)
        b1.open(self.ws.gamedata_dir / "config.xbr")
        b1.set_keyed_double(3, "garret4", "walkSpeed", 42.5)

        b2 = XbrEditorBackend(self.ws)
        b2.load_persistent_state()
        b2.open(self.ws.gamedata_dir / "config.xbr")
        self.assertTrue(b2.can_undo())
        self.assertTrue(b2.undo())
        cell = (b2.document.section_for(3)
                .find_cell("garret4", "walkSpeed"))
        self.assertNotEqual(cell.double_value, 42.5,
            msg="undo after restart didn't revert the replayed "
                "edit.")
        self.assertEqual(b2.pending_edits, [])

    def test_clear_pending_for_file_drops_persisted_edits(self):
        backend = XbrEditorBackend(self.ws)
        backend.open(self.ws.gamedata_dir / "config.xbr")
        backend.set_keyed_double(3, "garret4", "walkSpeed", 1.0)
        dropped = backend.clear_pending_for_file("config.xbr")
        self.assertEqual(dropped, 1)
        # Re-read from disk — should reflect the clear.
        loaded = self.ws.load_pending_edits()
        self.assertEqual(loaded, [])


# ---------------------------------------------------------------------------
# Keyed-cells grid shape
# ---------------------------------------------------------------------------


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class KeyedCellsGrid(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="xbr_grid_"))
        self.ws = _scratch_workspace(self._tmp)
        self.backend = XbrEditorBackend(self.ws)
        self.backend.open(
            self.ws.gamedata_dir / "config.xbr")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_grid_shape_matches_section_dimensions(self):
        grid = self.backend.keyed_cells_grid(3)
        self.assertEqual(len(grid["col_names"]),
                         len(grid["cells"]))
        for col in grid["cells"]:
            self.assertEqual(len(col), len(grid["row_names"]))

    def test_empty_cells_are_None(self):
        grid = self.backend.keyed_cells_grid(3)
        # At least some cells in a 39x108 table should be empty.
        has_empty = any(
            cell is None
            for col in grid["cells"] for cell in col)
        self.assertTrue(has_empty)

    def test_non_keyed_section_yields_empty_grid(self):
        grid = self.backend.keyed_cells_grid(14)  # critters_walking
        # Variant record — not keyed.
        if self.backend.section_summary(14).get("kind") != "keyed_table":
            self.assertEqual(grid["cells"], [])


# ---------------------------------------------------------------------------
# Auto-open from workspace
# ---------------------------------------------------------------------------


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class WorkspaceAutoOpen(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="xbr_autoopen_"))
        self.ws = _scratch_workspace(self._tmp)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_open_from_workspace_picks_config_by_default(self):
        backend = XbrEditorBackend(self.ws)
        ok = backend.open_from_workspace()
        self.assertTrue(ok)
        self.assertEqual(backend.path.name, "config.xbr")

    def test_open_from_workspace_accepts_specific_file(self):
        # Provision a second file so we have a real choice.
        shutil.copy2(
            _GAMEDATA / "a1.xbr",
            self.ws.gamedata_dir / "a1.xbr")
        backend = XbrEditorBackend(self.ws)
        self.assertTrue(backend.open_from_workspace("a1.xbr"))
        self.assertEqual(backend.path.name, "a1.xbr")

    def test_open_from_empty_workspace_returns_false(self):
        empty = Path(tempfile.mkdtemp(prefix="xbr_empty_"))
        try:
            backend = XbrEditorBackend(XbrWorkspace(empty))
            self.assertFalse(backend.open_from_workspace())
        finally:
            shutil.rmtree(empty, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
