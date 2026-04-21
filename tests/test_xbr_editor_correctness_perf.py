"""Post-audit regression tests for the XBR Editor backend.

This file pins two classes of fix identified during a deep
audit of the Mark-2 editor:

1. **Undo / pending-edit consistency under dedup.**  The editor
   collapses repeated edits to the same cell into one pending
   entry; before the fix, each undo naively popped one pending
   entry, so the first undo after two edits on the same cell
   left the document mid-sequence but reported an empty
   pending list.  Build would then ship vanilla bytes while
   the editor UI insisted the cell was still 10.0 — silent
   state/disk divergence.

2. **Cache-invalidation-sensitive hot paths.**  ``toc_entries``
   /  ``keyed_cells_grid`` / ``_section_has_modified_cells`` /
   ``reset_file`` were O(cells) or worse per call and fired
   many times per UI event.  Memoisation + byte-range compares
   cut ~11 seconds off ``reset_file`` and turned
   ``toc_entries`` from 43 ms/call into sub-ms.  These tests
   pin the speedup as a loose "must run under X seconds"
   budget so a future refactor that regresses the cache
   surfaces in CI instead of as a user complaint.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gui.pages.xbr_editor import (  # noqa: E402
    UndoRecord,
    XbrEditorBackend,
    XbrPendingEdit,
    _clone_edits,
)
from gui.xbr_workspace import XbrWorkspace  # noqa: E402


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
    ws = XbrWorkspace(tmpdir)
    (tmpdir / "game" / "gamedata").mkdir(parents=True)
    shutil.copy2(_GAMEDATA / "config.xbr",
                 tmpdir / "game" / "gamedata" / "config.xbr")
    return ws


# ---------------------------------------------------------------------------
# Correctness: undo/redo + pending-dedup consistency
# ---------------------------------------------------------------------------


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class UndoDedupConsistency(unittest.TestCase):
    """Regression tests for the dedup-vs-undo bug: two rapid
    edits to the same cell must undo cleanly, one step at a
    time, with the pending-edits list tracking the doc state
    at every step."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="xbr_dedup_"))
        ws = _scratch_workspace(self._tmp)
        self.backend = XbrEditorBackend(ws)
        self.backend.open(ws.gamedata_dir / "config.xbr")
        self.vanilla = (
            self.backend.document.section_for(3)
            .find_cell("garret4", "walkSpeed").double_value)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _current(self) -> float:
        return (self.backend.document.section_for(3)
                .find_cell("garret4", "walkSpeed").double_value)

    def _pending_value(self) -> float | None:
        for e in self.backend.pending_edits:
            if (e.section == "attacks_transitions"
                    and e.entity == "garret4"
                    and e.prop == "walkSpeed"):
                return e.value
        return None

    def test_two_edits_coalesce_but_undo_steps_through_both(self):
        """Repeated edits dedup into one pending entry, but the
        undo stack still carries one record per edit — so undo
        must walk back through each intermediate value."""
        self.backend.set_keyed_double(
            3, "garret4", "walkSpeed", 10.0)
        self.backend.set_keyed_double(
            3, "garret4", "walkSpeed", 20.0)
        self.assertEqual(len(self.backend.pending_edits), 1)
        self.assertEqual(
            len(self.backend._undo_stack), 2,
            msg="dedup should only affect pending_edits, NOT "
                "the undo stack — each keypress is independently "
                "undoable.")

        # One undo → doc back to intermediate 10.0, pending
        # list updated to reflect THAT value.
        self.assertTrue(self.backend.undo())
        self.assertEqual(self._current(), 10.0)
        self.assertEqual(self._pending_value(), 10.0,
            msg="after one undo, pending_edits must reflect the "
                "intermediate 10.0 value — previously it wrongly "
                "went straight to empty, desynchronising "
                "editor state from what the build pipeline sees.")

        # Second undo → vanilla + empty pending.
        self.assertTrue(self.backend.undo())
        self.assertEqual(self._current(), self.vanilla)
        self.assertEqual(self.backend.pending_edits, [])

    def test_undo_redo_ping_pong_preserves_state(self):
        self.backend.set_keyed_double(
            3, "garret4", "walkSpeed", 7.0)
        self.backend.set_keyed_double(
            3, "garret4", "walkSpeed", 13.0)
        self.backend.undo()
        self.backend.redo()
        self.assertEqual(self._current(), 13.0)
        self.assertEqual(self._pending_value(), 13.0)
        self.backend.undo()
        self.backend.undo()
        self.backend.redo()
        self.backend.redo()
        self.assertEqual(self._current(), 13.0)
        self.assertEqual(self._pending_value(), 13.0)

    def test_multi_cell_dedup_across_different_cells(self):
        """Edits targeting DIFFERENT cells must not dedup against
        each other — each gets its own pending entry."""
        self.backend.set_keyed_double(
            3, "garret4", "walkSpeed", 5.5)
        self.backend.set_keyed_double(
            3, "flicken", "walkSpeed", 6.5)
        self.assertEqual(
            len(self.backend.pending_edits), 2,
            msg="different cells shouldn't coalesce.")
        # One undo undoes flicken; garret4 stays modified.
        self.backend.undo()
        self.assertEqual(
            len(self.backend.pending_edits), 1)
        self.assertEqual(
            self.backend.pending_edits[0].entity, "garret4")


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class ResetFileUndoContract(unittest.TestCase):
    """``reset_file`` is now a single bulk-revert op instead of a
    Cartesian sweep; one undo must restore every pre-reset edit
    at once."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="xbr_reset_"))
        ws = _scratch_workspace(self._tmp)
        self.backend = XbrEditorBackend(ws)
        self.backend.open(ws.gamedata_dir / "config.xbr")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_reset_file_is_one_undo_step(self):
        self.backend.set_keyed_double(
            3, "garret4", "walkSpeed", 1.0)
        self.backend.set_keyed_double(
            3, "flicken", "walkSpeed", 2.0)
        self.backend.set_keyed_double(
            4, "garret4", "hitPoints", 999.0)
        self.assertEqual(len(self.backend.pending_edits), 3)

        count = self.backend.reset_file()
        self.assertEqual(count, 3)
        self.assertEqual(self.backend.pending_edits, [])

        # One undo brings all three back.
        self.assertTrue(self.backend.undo())
        self.assertEqual(
            len(self.backend.pending_edits), 3,
            msg="reset_file undo should reinstate every "
                "pre-reset edit in one step.")
        self.assertEqual(
            (self.backend.document.section_for(3)
             .find_cell("garret4", "walkSpeed").double_value),
            1.0)


# ---------------------------------------------------------------------------
# Correctness: stale persisted edits are dropped
# ---------------------------------------------------------------------------


class StalePendingEditHandling(unittest.TestCase):
    """Persisted edits whose target section / cell no longer
    exists must be discarded rather than silently noop'd.

    Catches the class of bug where a plugin pack gets
    uninstalled between sessions — its pending edits would
    otherwise linger in ``pending_edits.json`` forever,
    inflating the list and confusing the pending-count display.
    """

    def test_replay_drops_edit_for_missing_section(self):
        tmp = Path(tempfile.mkdtemp(prefix="xbr_stale_"))
        try:
            ws = XbrWorkspace(tmp)
            # Pre-seed a pending edit targeting a section that
            # won't exist in the vanilla config.xbr.
            ws.save_pending_edits([
                {"op": "set_keyed_double",
                 "xbr_file": "config.xbr",
                 "section": "nonexistent_plugin_section",
                 "entity": "x", "prop": "y", "value": 1.0},
            ])
            # Provision the workspace with real config.xbr.
            gd = _find_gamedata()
            if gd is None:
                self.skipTest("gamedata fixture not available")
            (tmp / "game" / "gamedata").mkdir(parents=True)
            shutil.copy2(gd / "config.xbr",
                         tmp / "game" / "gamedata" / "config.xbr")
            backend = XbrEditorBackend(ws)
            backend.load_persistent_state()
            self.assertEqual(
                len(backend.pending_edits), 1,
                msg="should load the stale edit before replay.")
            backend.open(ws.gamedata_dir / "config.xbr")
            self.assertEqual(
                backend.pending_edits, [],
                msg="stale persisted edit targeting a missing "
                    "section should be dropped on replay, not "
                    "silently kept.")
            # And the drop must have persisted to disk so a
            # subsequent session doesn't see it again.
            self.assertEqual(ws.load_pending_edits(), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Performance budgets
# ---------------------------------------------------------------------------


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class PerformanceBudgets(unittest.TestCase):
    """Loose time budgets that catch cache-regressing refactors.

    The headline figures from the audit were ~43 ms/call for
    ``toc_entries`` and ~11 s for ``reset_file`` after 50 edits.
    Today both run in sub-millisecond time thanks to memoisation
    + bulk byte copies.  These tests pick budgets well above the
    current timings so flaky CI runners don't false-fail, while
    still catching a real regression (e.g. accidentally dropping
    the cache).
    """

    TOC_ENTRIES_MAX_MS = 5.0
    KEYED_GRID_MAX_MS = 5.0
    RESET_FILE_MAX_MS = 50.0

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="xbr_perf_"))
        ws = _scratch_workspace(self._tmp)
        self.backend = XbrEditorBackend(ws)
        self.backend.open(ws.gamedata_dir / "config.xbr")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _measure_ms(self, fn, repeats: int = 20) -> float:
        # Warm-up once so cache population isn't in the timing.
        fn()
        t0 = time.perf_counter()
        for _ in range(repeats):
            fn()
        return ((time.perf_counter() - t0) * 1000) / repeats

    def test_toc_entries_cached_path_is_fast(self):
        ms = self._measure_ms(lambda: self.backend.toc_entries)
        self.assertLess(
            ms, self.TOC_ENTRIES_MAX_MS,
            msg=f"toc_entries averaged {ms:.2f} ms/call — cache "
                f"regressed; budget is "
                f"{self.TOC_ENTRIES_MAX_MS} ms.")

    def test_keyed_cells_grid_cached_path_is_fast(self):
        ms = self._measure_ms(
            lambda: self.backend.keyed_cells_grid(3))
        self.assertLess(
            ms, self.KEYED_GRID_MAX_MS,
            msg=f"keyed_cells_grid averaged {ms:.2f} ms/call — "
                f"cache regressed; budget is "
                f"{self.KEYED_GRID_MAX_MS} ms.")

    def test_reset_file_is_bounded_regardless_of_edit_count(self):
        # 100 pending edits across several cells.
        for i in range(100):
            entity = ["garret4", "flicken", "channeler",
                      "air_elemental", "water_elemental"][i % 5]
            self.backend.set_keyed_double(
                3, entity, "walkSpeed", float(i + 1))
        t0 = time.perf_counter()
        self.backend.reset_file()
        ms = (time.perf_counter() - t0) * 1000
        self.assertLess(
            ms, self.RESET_FILE_MAX_MS,
            msg=f"reset_file took {ms:.1f} ms — bulk-copy path "
                f"regressed; budget is "
                f"{self.RESET_FILE_MAX_MS} ms.")


# ---------------------------------------------------------------------------
# _clone_edits helper
# ---------------------------------------------------------------------------


class CloneEditsHelper(unittest.TestCase):
    def test_clone_is_independent(self):
        original = [
            XbrPendingEdit(op="set_keyed_double",
                           xbr_file="c.xbr",
                           section="s", entity="e", prop="p",
                           value=1.0, label="l"),
        ]
        copy = _clone_edits(original)
        self.assertEqual(copy, original)
        self.assertIsNot(copy, original)
        self.assertIsNot(copy[0], original[0])
        # Mutating the copy must not affect the original.
        copy[0].value = 99.0
        self.assertEqual(original[0].value, 1.0)


if __name__ == "__main__":
    unittest.main()
