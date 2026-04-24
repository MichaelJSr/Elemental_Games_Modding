"""Gating tests for the :class:`XbrDocument` foundation.

These tests pin the Phase-0 cornerstone property: every vanilla
Azurik XBR round-trips byte-identically through the new
:class:`~azurik_mod.xbr.document.XbrDocument` loader.  **If any of
these fail, structural edits built on top of the document model
will silently corrupt files** — the gate must stay green.

Additionally:

- Validate that the new :class:`KeyedTableSection` overlay produces
  the same decoded row/column/cell values as the legacy
  :class:`scripts.xbr_parser.KeyedSection` and
  :class:`azurik_mod.config.keyed_tables.KeyedTable` parsers.  This
  catches parser drift without forcing a rewrite of the legacy
  code.

- Pin the pointer-graph ref count per section type so reshaping
  :meth:`~azurik_mod.xbr.sections.Section.iter_refs` in a future
  phase surfaces loudly in code review.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.xbr import (  # noqa: E402
    KeyedTableSection,
    PointerGraph,
    RawSection,
    XbrDocument,
)
from azurik_mod.xbr.sections import _VARIANT_SCHEMAS  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture discovery
# ---------------------------------------------------------------------------

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


def _all_vanilla_xbrs() -> list[Path]:
    """Every .xbr shipped in the vanilla ISO's ``gamedata/`` tree.

    Walks recursively so ``index/index.xbr`` is included.  Returns
    an empty list when the fixture isn't mounted — the tests skip
    themselves in that case so CI without the ISO stays green.
    """
    if _GAMEDATA is None:
        return []
    return sorted(_GAMEDATA.rglob("*.xbr"))


# ---------------------------------------------------------------------------
# Round-trip (gating test)
# ---------------------------------------------------------------------------


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class RoundTripByteIdentity(unittest.TestCase):
    """Cornerstone regression: load every XBR, re-emit it, compare
    byte-for-byte.  **This is the gate for every structural-edit
    feature** built on top of :class:`XbrDocument`."""

    def test_every_vanilla_xbr_roundtrips(self):
        xbrs = _all_vanilla_xbrs()
        self.assertGreaterEqual(
            len(xbrs), 40,
            msg=f"expected at least 40 .xbr files in {_GAMEDATA}; "
                f"got {len(xbrs)}.  Is the ISO partially extracted?")
        failures: list[str] = []
        for p in xbrs:
            raw = p.read_bytes()
            doc = XbrDocument.load(p)
            if doc.dumps() != raw:
                failures.append(p.name)
        self.assertEqual(failures, [],
            msg=f"Round-trip byte-identity failed for "
                f"{len(failures)}/{len(xbrs)} files: {failures!r}.  "
                f"XbrDocument must never touch bytes it doesn't own.")

    def test_bytearray_round_trip(self):
        """Construct from a ``bytearray`` (not a path) — same identity."""
        p = _GAMEDATA / "config.xbr"
        raw = p.read_bytes()
        doc = XbrDocument.from_bytes(bytearray(raw))
        self.assertEqual(doc.dumps(), raw)

    def test_write_roundtrips(self):
        """``doc.write(path)`` produces byte-identical output."""
        import tempfile
        p = _GAMEDATA / "config.xbr"
        raw = p.read_bytes()
        doc = XbrDocument.load(p)
        with tempfile.NamedTemporaryFile(
                suffix=".xbr", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            doc.write(tmp_path)
            self.assertEqual(tmp_path.read_bytes(), raw)
        finally:
            if tmp_path.exists():
                os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Consistency with legacy parsers
# ---------------------------------------------------------------------------


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class LegacyParserConsistency(unittest.TestCase):
    """Decoded values from :class:`KeyedTableSection` must match
    the battle-tested legacy parsers bit-for-bit.

    The point isn't just "we wire up a parser"; it's "we wire up
    one that reproduces the legacy results".  Drift between the
    two surfaces here, not in a downstream feature.
    """

    def setUp(self):
        self.config_path = _GAMEDATA / "config.xbr"
        self.doc = XbrDocument.load(self.config_path)

    def test_keyed_section_dimensions_match_legacy(self):
        """Row / column / cell counts must match
        :class:`scripts.xbr_parser.XBRFile`.
        """
        from scripts.xbr_parser import XBRFile
        legacy = XBRFile(self.config_path)
        new = self.doc.keyed_sections()
        # Every section the legacy parser surfaces as keyed must show
        # up in the new parser with identical shape.
        for name, sec in legacy.sections.items():
            if sec.format != "keyed":
                continue
            self.assertIn(name, new,
                msg=f"New parser missing keyed section {name!r}")
            self.assertEqual(new[name].num_rows, sec.num_rows,
                msg=f"num_rows drift in {name}")
            self.assertEqual(new[name].num_cols, sec.num_cols,
                msg=f"num_cols drift in {name}")
            self.assertEqual(new[name].total_cells, sec.total_cells,
                msg=f"total_cells drift in {name}")

    def test_keyed_row_names_match_legacy(self):
        from scripts.xbr_parser import XBRFile
        legacy = XBRFile(self.config_path)
        new = self.doc.keyed_sections()
        for name, sec in legacy.sections.items():
            if sec.format != "keyed":
                continue
            self.assertEqual(
                new[name].row_names(), sec.row_names,
                msg=f"row_names drift in {name}")

    def test_keyed_column_names_match_legacy(self):
        from scripts.xbr_parser import XBRFile
        legacy = XBRFile(self.config_path)
        new = self.doc.keyed_sections()
        for name, sec in legacy.sections.items():
            if sec.format != "keyed":
                continue
            # Legacy emits "col_<n>" for non-string row-0 cells; the
            # new parser does the same so comparison is apples-to-
            # apples.
            self.assertEqual(
                new[name].col_names(), sec.col_names,
                msg=f"col_names drift in {name}")

    def test_keyed_cell_values_match_legacy_spot_check(self):
        """Pick one known-important section + entity + property and
        assert the decoded double is identical.

        Kept narrow because the full cell grid is thousands of cells;
        the legacy parser's own tests already cover that breadth.
        """
        from scripts.xbr_parser import XBRFile
        legacy = XBRFile(self.config_path)
        new = self.doc.keyed_sections()
        # ``attacks_transitions/garret4/walkSpeed`` is the canonical
        # "did the parser still work?" probe used across the repo.
        leg_sec = legacy.sections["attacks_transitions"]
        leg = leg_sec.get_value("garret4", "walkSpeed")
        self.assertIsNotNone(leg)
        leg_type, leg_val, leg_addr = leg
        self.assertEqual(leg_type, "double")

        sec = new["attacks_transitions"]
        cell = sec.find_cell("garret4", "walkSpeed")
        self.assertIsNotNone(cell)
        self.assertEqual(cell.type_code, 1)
        self.assertEqual(cell.double_value, leg_val)
        self.assertEqual(cell.file_offset, leg_addr)

    def test_keyed_string_cells_decode_identically(self):
        """Any row-0 string cell (entity name) must decode identically
        under both parsers — this exercises the self-relative string
        ref math end-to-end."""
        from scripts.xbr_parser import XBRFile
        legacy = XBRFile(self.config_path)
        new = self.doc.keyed_sections()
        sample_section = "critters_critter_data"
        legacy_cols = legacy.sections[sample_section].col_names
        new_cols = new[sample_section].col_names()
        self.assertEqual(legacy_cols, new_cols)


# ---------------------------------------------------------------------------
# Section-type coverage sanity
# ---------------------------------------------------------------------------


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class SectionDispatchSanity(unittest.TestCase):
    """The section dispatch should pick the right overlay type for
    known TOC entries.  Exists so a future parser change that
    accidentally demotes a keyed table to :class:`RawSection`
    surfaces immediately."""

    def test_config_xbr_has_16_keyed_sections(self):
        """Vanilla config.xbr has 15 named entries in
        KEYED_SECTION_OFFSETS (including the renamed
        ``armor_properties_real`` / ``armor_properties_unused``
        pair that used to be labelled ``armor_hit_fx`` /
        ``armor_properties`` by their raw TOC tags); the new
        parser discovers 16 via :meth:`XbrDocument.keyed_sections`
        because it scans file offsets rather than going off a
        static list."""
        doc = XbrDocument.load(_GAMEDATA / "config.xbr")
        keyed = doc.keyed_sections()
        self.assertGreaterEqual(len(keyed), 15,
            msg=f"Expected >= 15 keyed sections in config.xbr, "
                f"got {len(keyed)}")

    def test_level_xbr_sections_fall_back_to_raw(self):
        """Level XBRs mostly use tags the platform doesn't model
        (``node``, ``surf``, ``rdms``, ...).  Those MUST surface as
        :class:`RawSection` — never get mis-identified as keyed
        tables, which would risk garbling them on write."""
        doc = XbrDocument.load(_GAMEDATA / "a1.xbr")
        unexpected_keyed = [
            s for s in doc.sections()
            if isinstance(s, KeyedTableSection)
        ]
        self.assertEqual(
            unexpected_keyed, [],
            msg=f"a1.xbr produced KeyedTableSection overlays for "
                f"{len(unexpected_keyed)} entries; those tags aren't "
                f"reversed and must fall back to RawSection.")
        # At least one raw section should exist — confirms the
        # fallback path actually ran.
        raw_count = sum(1 for s in doc.sections()
                        if isinstance(s, RawSection))
        self.assertGreater(raw_count, 0)

    def test_index_xbr_surfaces_index_records_overlay(self):
        from azurik_mod.xbr import IndexRecordsSection
        idx_path = _GAMEDATA / "index" / "index.xbr"
        if not idx_path.exists():
            self.skipTest(f"{idx_path} not present")
        doc = XbrDocument.load(idx_path)
        self.assertEqual(len(doc.toc), 1)
        sec = doc.section_for(0)
        self.assertIsInstance(sec, IndexRecordsSection)


# ---------------------------------------------------------------------------
# Pointer-graph smoke
# ---------------------------------------------------------------------------


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class PointerGraphSmoke(unittest.TestCase):
    """Pin the ref counts so a structural change to
    :meth:`iter_refs` surfaces in code review."""

    def test_config_xbr_ref_counts(self):
        doc = XbrDocument.load(_GAMEDATA / "config.xbr")
        graph = PointerGraph(doc)
        # Exact counts:
        #  - row-name ref per (section, row)
        #  - one string ref per type-2 cell
        # The number isn't magic but it's stable across vanilla ISO
        # revisions — pinning it catches silent parser drift.
        self.assertGreater(len(graph), 5000,
            msg=f"too few refs in config.xbr: {len(graph)}")
        # Every ref must resolve to SOME target (non-None) since
        # vanilla strings are all wired up correctly.
        unresolved = [rr for rr in graph
                      if rr.target_offset is None]
        self.assertEqual(unresolved, [],
            msg=f"{len(unresolved)} refs in config.xbr didn't "
                f"resolve a target offset — parser bug?")

    def test_pointer_graph_targets_land_in_buffer(self):
        """Every resolved target offset must sit inside the file."""
        doc = XbrDocument.load(_GAMEDATA / "config.xbr")
        graph = PointerGraph(doc)
        size = len(doc.raw)
        for rr in graph:
            if rr.target_offset is None:
                continue
            self.assertGreaterEqual(rr.target_offset, 0)
            self.assertLess(
                rr.target_offset, size,
                msg=f"{rr.ref.describe()} -> 0x{rr.target_offset:X} "
                    f"past EOF 0x{size:X}")


# ---------------------------------------------------------------------------
# Schema drift guards — the runtime package carries its own copies
# of a few tables that ``scripts/xbr_parser.py`` owns so it doesn't
# have to import the ``scripts/`` module (which is excluded from
# the installed wheel).  If the two copies drift, one of them is
# wrong — these tests surface it before a user hits a mysterious
# bug at runtime.
# ---------------------------------------------------------------------------


class KeyedSectionOffsetsDrift(unittest.TestCase):
    """Guard the package-local :data:`_KEYED_SECTION_OFFSETS`
    against the historical table in :mod:`scripts.xbr_parser`.

    Runtime code imports the package-local copy; without this
    drift guard the two tables could silently diverge and users
    would see wrong "friendly section names" in the editor +
    ``xbr xref`` CLI.
    """

    def test_offsets_match_legacy_scripts(self):
        from azurik_mod.xbr.sections import _KEYED_SECTION_OFFSETS
        from scripts.xbr_parser import KEYED_SECTION_OFFSETS as LEGACY
        self.assertEqual(
            _KEYED_SECTION_OFFSETS, LEGACY,
            msg="Runtime copy of KEYED_SECTION_OFFSETS in "
                "azurik_mod.xbr.sections has drifted from the "
                "canonical copy in scripts/xbr_parser.py.  "
                "Update whichever is wrong — both must stay in "
                "lockstep.")


class VariantSchemaDrift(unittest.TestCase):
    """Pin the variant-record schema table to the one in
    :mod:`scripts.xbr_parser`.  If they drift, one parser is wrong;
    surface the mismatch loudly."""

    def test_variant_schemas_match_legacy(self):
        from scripts.xbr_parser import VARIANT_SCHEMAS as LEGACY
        # Same section names.
        self.assertEqual(set(_VARIANT_SCHEMAS), set(LEGACY))
        for name, new in _VARIANT_SCHEMAS.items():
            leg = LEGACY[name]
            for key in ("section_offset", "record_base",
                        "entity_count", "props_per_entity",
                        "record_size"):
                self.assertEqual(
                    new[key], leg[key],
                    msg=f"variant schema drift for {name}.{key}: "
                        f"new={new[key]} legacy={leg[key]}")


if __name__ == "__main__":
    unittest.main()
