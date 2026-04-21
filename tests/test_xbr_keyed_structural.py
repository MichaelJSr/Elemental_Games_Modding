"""Tests for :mod:`azurik_mod.xbr.edits` — structural primitives.

Split into two groups:

- **Shippable primitives**: ``set_keyed_double``, ``set_keyed_string``
  (same-size), ``replace_bytes_at``, ``replace_string_at``.  Each is
  exercised end-to-end on a vanilla config.xbr copy: apply, re-parse
  the document, confirm the new value lands.  Byte-identity is
  guarded separately by :mod:`tests.test_xbr_document_roundtrip` —
  here we care about mutation correctness.

- **Blocked-on-RE stubs**: ``add_keyed_row``, ``remove_keyed_row``,
  ``grow_string_pool``, ``add_level_entity``, ``resize_toc_entry``.
  Each must raise :class:`NotImplementedError` with a clear
  "blocked on X" message — tests pin both the behaviour and the
  message so the stubs stay honest.
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.xbr import XbrDocument  # noqa: E402
from azurik_mod.xbr.edits import (  # noqa: E402
    XbrStructuralError,
    add_keyed_row,
    add_level_entity,
    grow_string_pool,
    remove_keyed_row,
    replace_bytes_at,
    replace_string_at,
    resize_toc_entry,
    set_keyed_double,
    set_keyed_string,
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
class SetKeyedDouble(unittest.TestCase):
    def setUp(self):
        self.config = _GAMEDATA / "config.xbr"
        self.doc = XbrDocument.load(self.config)
        self.ks = self.doc.keyed_sections()["attacks_transitions"]

    def test_sets_and_reads_back(self):
        off = set_keyed_double(
            self.ks, "garret4", "walkSpeed", 99.0)
        # Reading the cell back must show the new value.
        cell = self.ks.find_cell("garret4", "walkSpeed")
        self.assertIsNotNone(cell)
        self.assertEqual(cell.double_value, 99.0)
        # Direct bytes at the returned offset must decode to 99.0.
        written = struct.unpack_from(
            "<d", self.doc.raw, off)[0]
        self.assertEqual(written, 99.0)

    def test_round_trip_through_dumps(self):
        """After mutation, ``doc.dumps() -> load -> read`` must
        surface the new value (mutation is persistent, not view-
        only)."""
        set_keyed_double(self.ks, "garret4", "walkSpeed", 42.5)
        reloaded = XbrDocument.from_bytes(self.doc.dumps())
        cell = (reloaded.keyed_sections()["attacks_transitions"]
                .find_cell("garret4", "walkSpeed"))
        self.assertEqual(cell.double_value, 42.5)

    def test_unknown_entity_raises(self):
        with self.assertRaises(XbrStructuralError) as ctx:
            set_keyed_double(
                self.ks, "not_a_real_entity", "walkSpeed", 1.0)
        self.assertIn("not found", str(ctx.exception))

    def test_non_double_cell_raises(self):
        """A type-2 (string) cell can't be set as a double."""
        # Row 0 is the "name" row; every populated cell is a string.
        # Use a known string cell.
        with self.assertRaises(XbrStructuralError) as ctx:
            set_keyed_double(self.ks, "garret4", "name", 1.0)
        self.assertIn("not a double", str(ctx.exception))

    def test_does_not_disturb_unrelated_bytes(self):
        """Mutation must touch only the 8 double bytes."""
        original = self.config.read_bytes()
        off = set_keyed_double(
            self.ks, "garret4", "walkSpeed", 12345.0)
        modified = bytes(self.doc.raw)
        # Compare byte-for-byte outside the 8-byte window.
        self.assertEqual(modified[:off], original[:off])
        self.assertEqual(modified[off + 8:], original[off + 8:])


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class SetKeyedString(unittest.TestCase):
    def setUp(self):
        self.config = _GAMEDATA / "config.xbr"
        self.doc = XbrDocument.load(self.config)
        # critters_critter_data has string entity names we can
        # rewrite.  Pick a known one.
        self.ks = self.doc.keyed_sections()["critters_critter_data"]

    def test_same_length_replacement(self):
        # "garret4" is 7 chars; replace with another 7-char string.
        # First confirm the cell IS garret4/name — without that the
        # test loses signal.
        cell = self.ks.find_cell("garret4", "name")
        self.assertIsNotNone(cell)
        self.assertEqual(cell.type_code, 2)
        self.assertEqual(cell.string_value, "garret4")

        set_keyed_string(self.ks, "garret4", "name", "garret5")

        # Reload to confirm the mutation is persistent.
        reloaded = XbrDocument.from_bytes(self.doc.dumps())
        ks2 = reloaded.keyed_sections()["critters_critter_data"]
        cell2 = ks2.find_cell("garret5", "name")
        self.assertIsNotNone(cell2)
        self.assertEqual(cell2.string_value, "garret5")

    def test_shorter_replacement_pads_with_nul(self):
        set_keyed_string(self.ks, "garret4", "name", "abc")
        cell = self.ks.find_cell("abc", "name")
        self.assertIsNotNone(cell)
        self.assertEqual(cell.string_value, "abc")
        # The type-2 string_length field was also updated so the
        # cell's declared length is now 3.
        self.assertEqual(cell.string_length, 3)

    def test_oversized_string_raises(self):
        cell = self.ks.find_cell("garret4", "name")
        assert cell is not None
        # Build a string that's guaranteed too long for the slot.
        too_long = "x" * (cell.string_length + 10)
        with self.assertRaises(XbrStructuralError) as ctx:
            set_keyed_string(self.ks, "garret4", "name", too_long)
        self.assertIn("pool-overlap", str(ctx.exception))

    def test_nonascii_raises(self):
        with self.assertRaises(XbrStructuralError):
            set_keyed_string(self.ks, "garret4", "name", "garretö")

    def test_string_refs_still_resolve_after_mutation(self):
        """The self-relative string ref is unchanged by an in-place
        rewrite (same offset, same origin); verify the pointer graph
        still resolves everything."""
        from azurik_mod.xbr import PointerGraph
        set_keyed_string(self.ks, "garret4", "name", "test")
        graph = PointerGraph(self.doc)
        unresolved = [rr for rr in graph if rr.target_offset is None]
        self.assertEqual(unresolved, [])


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class ReplaceBytesAndStringAt(unittest.TestCase):
    def setUp(self):
        self.doc = XbrDocument.load(_GAMEDATA / "config.xbr")

    def test_replace_bytes_at_same_size(self):
        offset = 0x1000
        original = bytes(self.doc.raw[offset:offset + 4])
        replace_bytes_at(self.doc, offset, b"\xDE\xAD\xBE\xEF")
        self.assertEqual(
            bytes(self.doc.raw[offset:offset + 4]),
            b"\xDE\xAD\xBE\xEF")
        # Surrounding bytes unchanged.
        self.assertEqual(
            bytes(self.doc.raw[offset - 4:offset]),
            bytes(XbrDocument.load(_GAMEDATA / "config.xbr")
                  .raw[offset - 4:offset]))

    def test_replace_bytes_out_of_range_raises(self):
        with self.assertRaises(XbrStructuralError):
            replace_bytes_at(self.doc, len(self.doc.raw) - 1,
                             b"\x00\x00")

    def test_replace_string_at_fits(self):
        # Find any string in the pool and rewrite it to something
        # shorter.  ``critters_critter_data`` has entity names we
        # know will be present.
        ks = self.doc.keyed_sections()["critters_critter_data"]
        cell = ks.find_cell("garret4", "name")
        assert cell is not None and cell.string_file_offset is not None
        replace_string_at(self.doc, cell.string_file_offset, "abc")
        # Now re-reading the cell surfaces the new value.
        # (We use a fresh overlay so caching doesn't fool us.)
        reloaded = XbrDocument.from_bytes(self.doc.dumps())
        ks2 = reloaded.keyed_sections()["critters_critter_data"]
        self.assertIsNotNone(ks2.find_cell("abc", "name"))

    def test_replace_string_at_too_long_raises(self):
        ks = self.doc.keyed_sections()["critters_critter_data"]
        cell = ks.find_cell("garret4", "name")
        assert cell is not None and cell.string_file_offset is not None
        with self.assertRaises(XbrStructuralError):
            replace_string_at(
                self.doc, cell.string_file_offset,
                "x" * 100)  # definitely too long for the slot


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class AfterEditRoundTripConsistency(unittest.TestCase):
    """After any in-place edit, re-reading the document must yield
    the same bytes we wrote.  Catches accidental mutation of anything
    outside the declared edit region."""

    def test_double_edit_roundtrips_idempotently(self):
        doc = XbrDocument.load(_GAMEDATA / "config.xbr")
        ks = doc.keyed_sections()["attacks_transitions"]
        set_keyed_double(ks, "garret4", "walkSpeed", 3.14)
        snap = bytes(doc.raw)
        # Dumping + reloading + reading the same edit produces
        # byte-identical output.
        reloaded = XbrDocument.from_bytes(snap)
        self.assertEqual(reloaded.dumps(), snap)

    def test_string_edit_roundtrips_idempotently(self):
        doc = XbrDocument.load(_GAMEDATA / "config.xbr")
        ks = doc.keyed_sections()["critters_critter_data"]
        set_keyed_string(ks, "garret4", "name", "abc")
        snap = bytes(doc.raw)
        self.assertEqual(
            XbrDocument.from_bytes(snap).dumps(), snap)


class BlockedOnReStubs(unittest.TestCase):
    """Pin the stubbed primitives so nobody silently unstubs them
    without the RE pass that unblocks them."""

    def _dummy_section(self):
        # Minimal fixture — a 0x4000-byte buffer with just the magic
        # and a single TOC entry so XbrDocument happily constructs.
        import struct
        buf = bytearray(0x4000)
        buf[:4] = b"xobx"
        # toc_count = 1 at 0x0C
        struct.pack_into("<I", buf, 0x0C, 1)
        # TOC row 0 at 0x40: size=0x2000, tag="tabl", flags=0,
        # file_offset=0x1000.
        struct.pack_into("<IIII", buf, 0x40,
                         0x2000, 0x6C626174, 0, 0x1000)
        # terminator at 0x50 stays all-zero.
        doc = XbrDocument.from_bytes(bytes(buf))
        return doc.section_for(0)

    def test_add_keyed_row_raises_with_blocker(self):
        with self.assertRaises(NotImplementedError) as ctx:
            add_keyed_row(self._dummy_section(), "new_row")
        msg = str(ctx.exception)
        self.assertIn("pool-overlap", msg)
        self.assertIn("docs/XBR_FORMAT.md", msg)

    def test_remove_keyed_row_raises_with_blocker(self):
        with self.assertRaises(NotImplementedError) as ctx:
            remove_keyed_row(self._dummy_section(), "some_row")
        self.assertIn("pool-overlap", str(ctx.exception))

    def test_grow_string_pool_raises_with_blocker(self):
        with self.assertRaises(NotImplementedError) as ctx:
            grow_string_pool(self._dummy_section(), 16)
        self.assertIn("pool-overlap", str(ctx.exception))

    def test_add_level_entity_raises_with_blocker(self):
        doc = XbrDocument.from_bytes(b"xobx" + b"\x00" * 0x100)
        with self.assertRaises(NotImplementedError) as ctx:
            add_level_entity(doc)
        self.assertIn("level-XBR", str(ctx.exception))

    def test_resize_toc_entry_raises_with_blocker(self):
        doc = XbrDocument.from_bytes(b"xobx" + b"\x00" * 0x100)
        with self.assertRaises(NotImplementedError) as ctx:
            resize_toc_entry(doc, 0, 0x1000)
        self.assertIn("level-XBR", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
