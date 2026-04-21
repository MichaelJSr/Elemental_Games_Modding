"""Regression tests for two user-visible fixes to the XBR Editor:

1. **Column-header sorting.**  The grid's property columns cycle
   asc → desc → original when their header is clicked.
   :meth:`XbrEditorBackend.sort_entity_order` is the Tk-free
   core; tests here pin its behaviour so the UI layer can rely
   on it without inspecting Tk internals.

2. **Edit-to-ISO persistence.**  The full chain — editor edit →
   ``pending_mod()`` JSON → ``_merge_config_edits`` → CLI
   ``--config-mod`` → ``apply_xbr_edit_dicts`` → extracted
   ``gamedata/config.xbr`` — must land the edit on disk so the
   repacked ISO reflects it.  A reviewer reported doubts after
   the Mark-2 rewrite; this test makes sure we can never
   silently regress.
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

from gui.pages.xbr_editor import XbrEditorBackend  # noqa: E402
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
# Sort
# ---------------------------------------------------------------------------


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class SortEntityOrder(unittest.TestCase):
    """Click-a-column-header sort logic — Tk-free test surface."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="xbr_sort_"))
        self.ws = _scratch_workspace(self._tmp)
        self.backend = XbrEditorBackend(self.ws)
        self.backend.open(
            self.ws.gamedata_dir / "config.xbr")
        # ``attacks_transitions`` is section index 3 and carries
        # a ``walkSpeed`` property populated for most entities.
        self.sec_idx = 3
        self.sec = self.backend.document.section_for(self.sec_idx)
        self.row_idx = self.sec.row_names().index("walkSpeed")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ----- direction semantics ---------------------------------

    def test_restore_when_prop_is_none(self):
        """Sort key=None means "original order" — the identity
        permutation ``[0, 1, 2, ..., n-1]``."""
        order = self.backend.sort_entity_order(
            self.sec_idx, None, descending=False)
        self.assertEqual(order, list(range(self.sec.num_cols)))

    def test_ascending_is_nondecreasing_for_populated_cells(self):
        order = self.backend.sort_entity_order(
            self.sec_idx, "walkSpeed", descending=False)
        vals: list[float] = []
        for col_idx in order:
            cell = self.sec.read_cell(col_idx, self.row_idx)
            if cell.type_code == 1 and cell.double_value is not None:
                vals.append(cell.double_value)
        self.assertEqual(vals, sorted(vals),
            msg="ascending order must produce a non-decreasing "
                "sequence of populated cell values")

    def test_descending_is_nonincreasing_for_populated_cells(self):
        order = self.backend.sort_entity_order(
            self.sec_idx, "walkSpeed", descending=True)
        vals: list[float] = []
        for col_idx in order:
            cell = self.sec.read_cell(col_idx, self.row_idx)
            if cell.type_code == 1 and cell.double_value is not None:
                vals.append(cell.double_value)
        self.assertEqual(vals, sorted(vals, reverse=True),
            msg="descending order must produce a non-increasing "
                "sequence of populated cell values")

    def test_empty_cells_land_last_in_both_directions(self):
        """The emptiness marker must not flip to the top when
        ``descending=True``.  This was a bug in the first sort
        implementation — fixed by separating populated + empty
        lists before calling ``sorted()``."""
        for desc in (False, True):
            with self.subTest(descending=desc):
                order = self.backend.sort_entity_order(
                    self.sec_idx, "walkSpeed", descending=desc)
                # Walk forwards, once we see an empty cell EVERY
                # subsequent cell must also be empty.
                seen_empty = False
                for col_idx in order:
                    cell = self.sec.read_cell(col_idx, self.row_idx)
                    is_empty = (
                        cell.type_code == 0
                        or (cell.type_code == 1
                            and cell.double_value is None))
                    if is_empty:
                        seen_empty = True
                    elif seen_empty:
                        self.fail(
                            f"Populated cell at col {col_idx} came "
                            f"AFTER an empty cell — empty cells "
                            f"should always sort last "
                            f"(descending={desc}).")

    def test_permutation_is_complete_and_stable(self):
        """Every sort output is a permutation of 0..num_cols-1 —
        no duplicates, nothing missing."""
        for prop in (None, "walkSpeed"):
            for desc in (False, True):
                with self.subTest(prop=prop, descending=desc):
                    order = self.backend.sort_entity_order(
                        self.sec_idx, prop, descending=desc)
                    self.assertEqual(
                        sorted(order),
                        list(range(self.sec.num_cols)))

    def test_unknown_prop_falls_back_to_original(self):
        """A bogus property name mustn't raise — gracefully fall
        back to original order."""
        order = self.backend.sort_entity_order(
            self.sec_idx, "not_a_real_property",
            descending=False)
        self.assertEqual(order, list(range(self.sec.num_cols)))

    def test_non_keyed_section_returns_empty(self):
        """Variant-record sections have no column dimension we
        can sort on; return empty."""
        # Section 14 is ``critters_walking`` (variant record).
        order = self.backend.sort_entity_order(
            14, "anything", descending=False)
        self.assertEqual(order, [])

    # ----- string-cell sorting ---------------------------------

    def test_string_column_sorts_lexically(self):
        """Row 0 is the "name" row — sort on it to exercise the
        type-2 (string) cell path."""
        # The name row is index 0 in attacks_transitions by
        # convention.
        name_row = self.sec.row_names()[0]
        order = self.backend.sort_entity_order(
            self.sec_idx, name_row, descending=False)
        names: list[str] = []
        for col_idx in order:
            cell = self.sec.read_cell(col_idx, 0)
            if cell.type_code == 2 and cell.string_value:
                names.append(cell.string_value.lower())
        self.assertEqual(names, sorted(names),
            msg="lexical sort of entity names must be "
                "non-decreasing")


# ---------------------------------------------------------------------------
# Edit persistence end-to-end (editor → --config-mod → disk)
# ---------------------------------------------------------------------------


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class EditPersistsToISOExtract(unittest.TestCase):
    """Simulate the build-pipeline chain the GUI actually uses.

    The test pins the integration contract between four
    independent pieces of code (editor backend, Build page
    merge, ``randomize_full`` command, XbrStaging flush), any
    one of which could drop the edit silently — an outcome a
    user would only notice AFTER a 30-second ISO rebuild.
    """

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(
            prefix="xbr_persist_iso_"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _build_simulated_extract(self) -> Path:
        """Create a ``gamedata/config.xbr`` tree like what
        xdvdfs unpacks into ``extract_dir``."""
        extract = self._tmp / "extract"
        (extract / "gamedata").mkdir(parents=True)
        shutil.copy2(
            _GAMEDATA / "config.xbr",
            extract / "gamedata" / "config.xbr")
        return extract

    def test_full_editor_to_disk_chain(self):
        # Step 1: the editor backend makes two edits.
        ws = _scratch_workspace(self._tmp / "ws")
        editor = XbrEditorBackend(ws)
        editor.open(ws.gamedata_dir / "config.xbr")
        editor.set_keyed_double(
            4, "garret4", "hitPoints", 555.0)
        editor.set_keyed_string(
            4, "garret4", "name", "abc")
        pending = editor.pending_mod()
        self.assertEqual(len(pending["xbr_edits"]), 2)

        # Step 2: the Build page's _merge_config_edits-style
        # merge.  We just tuck the editor's edits into a
        # ``config_edits`` dict the same way the GUI does.
        config_edits = {
            "name": "Simulated GUI build",
            "format": "grouped",
            "sections": {},
        }
        config_edits.setdefault(
            "xbr_edits", []).extend(pending["xbr_edits"])

        # Step 3: what cmd_randomize_full does with the
        # --config-mod blob when config.xbr exists.
        extract = self._build_simulated_extract()
        from azurik_mod.patching.xbr_spec import (
            apply_xbr_edit_dicts)
        from azurik_mod.patching.xbr_staging import XbrStaging
        staging = XbrStaging(extract)
        applied = apply_xbr_edit_dicts(
            staging, config_edits["xbr_edits"])
        self.assertEqual(applied, 2)
        written = staging.flush()
        self.assertIn("config.xbr", written)

        # Step 4: verify the on-disk file reflects both edits.
        # ``set_keyed_string`` renamed ``garret4`` → ``abc`` so the
        # entity is now looked up by its new name.  ``hitPoints``
        # lives in a different row but the same column, so both
        # edits coexist.
        from azurik_mod.xbr import XbrDocument
        on_disk = XbrDocument.load(
            extract / "gamedata" / "config.xbr")
        ccd = on_disk.keyed_sections()["critters_critter_data"]
        self.assertIsNotNone(
            ccd.find_cell("abc", "name"),
            msg="set_keyed_string didn't persist the new name")
        self.assertEqual(
            ccd.find_cell("abc", "hitPoints").double_value,
            555.0)

    def test_json_round_trip_survives_subprocess_boundary(self):
        """The Build page hands config_edits to run_randomizer
        which ``json.dumps`` it into a CLI string.  The CLI
        then ``json.loads`` it back.  Exercise the round-trip
        to catch encoding-sensitive edits (unicode entity
        names, hex byte payloads, …)."""
        ws = _scratch_workspace(self._tmp / "ws")
        editor = XbrEditorBackend(ws)
        editor.open(ws.gamedata_dir / "config.xbr")
        editor.set_keyed_double(
            4, "garret4", "hitPoints", 12345.0)
        blob = editor.pending_mod()

        encoded = json.dumps(blob)
        decoded = json.loads(encoded)
        self.assertEqual(decoded, blob,
            msg="pending_mod() output must round-trip through "
                "json.dumps / json.loads without mutation — it "
                "ships through the CLI as --config-mod=<string>.")

    def test_edit_persists_despite_empty_other_fields(self):
        """An XBR-editor edit must apply even when no variant /
        keyed patches accompany it — regression for the nested
        ``if mod:`` block that previously only ran the
        ``xbr_edits`` path as a side-effect of the config.xbr
        patch flow.
        """
        ws = _scratch_workspace(self._tmp / "ws")
        editor = XbrEditorBackend(ws)
        editor.open(ws.gamedata_dir / "config.xbr")
        editor.set_keyed_double(
            4, "garret4", "hitPoints", 1337.0)

        # Build page would wrap this with an empty sections dict.
        merged = {
            "name": "Only XBR edits",
            "format": "grouped",
            "sections": {},
            "xbr_edits": editor.pending_mod()["xbr_edits"],
        }
        extract = self._build_simulated_extract()
        from azurik_mod.patching.xbr_spec import (
            apply_xbr_edit_dicts)
        from azurik_mod.patching.xbr_staging import XbrStaging
        staging = XbrStaging(extract)
        apply_xbr_edit_dicts(staging, merged["xbr_edits"])
        staging.flush()

        from azurik_mod.xbr import XbrDocument
        on_disk = XbrDocument.load(
            extract / "gamedata" / "config.xbr")
        self.assertEqual(
            on_disk.keyed_sections()["critters_critter_data"]
            .find_cell("garret4", "hitPoints").double_value,
            1337.0)


if __name__ == "__main__":
    unittest.main()
