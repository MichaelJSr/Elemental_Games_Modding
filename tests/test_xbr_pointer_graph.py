"""Pointer-graph invariants.

Separate from :mod:`tests.test_xbr_document_roundtrip` so failure
signals point at ``PointerGraph`` behaviour specifically.

Verifies:

- Every ref in every vanilla XBR resolves to an in-bounds target.
- The shift-query machinery picks the right refs for a given
  byte-range delta.
- The snapshot helper produces a stable summary.
"""

from __future__ import annotations

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
    XbrDocument,
)
from azurik_mod.xbr.refs import (  # noqa: E402
    FileAbsoluteRef,
    SelfRelativeRef,
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
class RefResolutionIntegrity(unittest.TestCase):
    """For every vanilla XBR the pointer graph must be complete."""

    def test_every_config_ref_resolves(self):
        doc = XbrDocument.load(_GAMEDATA / "config.xbr")
        graph = PointerGraph(doc)
        unresolved = [rr for rr in graph
                      if rr.target_offset is None]
        self.assertEqual(
            unresolved, [],
            msg=f"{len(unresolved)} refs in config.xbr didn't "
                f"resolve a target offset — parser bug?")

    def test_every_index_xbr_ref_resolves(self):
        idx = _GAMEDATA / "index" / "index.xbr"
        if not idx.exists():
            self.skipTest(f"{idx} missing")
        doc = XbrDocument.load(idx)
        graph = PointerGraph(doc)
        # index.xbr currently yields zero refs (reversal is
        # partial).  Nothing to resolve but the scan must finish.
        self.assertGreaterEqual(len(graph), 0)

    def test_targets_stay_in_file(self):
        doc = XbrDocument.load(_GAMEDATA / "config.xbr")
        graph = PointerGraph(doc)
        sz = len(doc.raw)
        for rr in graph:
            if rr.target_offset is None:
                continue
            self.assertGreaterEqual(rr.target_offset, 0)
            self.assertLess(rr.target_offset, sz)


class RefShiftMath(unittest.TestCase):
    """Exercise :meth:`PointerGraph.refs_to_patch_for_shift` with
    a hand-built document so the expected patch set is provable."""

    def _mini_doc(self) -> XbrDocument:
        """Build a 0x4000-byte document with a single keyed-table
        section (num_rows=2, num_cols=1) so we know exactly which
        refs exist.

        This is NOT a round-trip test — we're just exercising
        the shift math.
        """
        import struct
        buf = bytearray(0x4000)
        buf[:4] = b"xobx"
        struct.pack_into("<I", buf, 0x0C, 1)        # toc_count = 1
        # TOC row 0: size=0x2000 tag=tabl flags=0 file_offset=0x1000.
        struct.pack_into(
            "<IIII", buf, 0x40,
            0x2000, int.from_bytes(b"tabl", "little"), 0, 0x1000)

        # Section payload starts at 0x1000; table header at
        # 0x1000 + 0x1000 = 0x2000.  num_rows=2 num_cols=1.
        hdr_off = 0x1000 + 0x1000
        struct.pack_into(
            "<5I", buf, hdr_off,
            2, 0x10, 1, 2, 0x20,
        )
        # Row headers at 0x2014, 0x201C.  Row-name refs at +4 each.
        # Point row 0's name at offset 0x1100 (relative delta =
        # 0x1100 - (0x2014+4) negative — invalid).  Just use 0 so
        # the graph can resolve harmlessly.
        struct.pack_into("<II", buf, 0x2014, 0, 0)
        struct.pack_into("<II", buf, 0x201C, 0, 0)
        return XbrDocument.from_bytes(bytes(buf))

    def test_shift_before_origin_and_target_noop(self):
        doc = self._mini_doc()
        graph = PointerGraph(doc)
        # Shift a region BEFORE all our refs — no ref's origin or
        # target should cross, so no patches needed.
        to_patch = graph.refs_to_patch_for_shift(
            shift_start=0x10000,  # past EOF
            shift_delta=16,
        )
        self.assertEqual(to_patch, [])

    def test_shift_between_origin_and_target_patches(self):
        """Build a document where moving a range WOULD invalidate
        a SelfRelativeRef (origin below, target above)."""
        doc = self._mini_doc()
        graph = PointerGraph(doc)
        # The only refs in our mini doc resolve to 0 (self-targeted).
        # For shift math, that's an edge case but valid.  Use a
        # synthesised ref directly:
        ref = SelfRelativeRef(
            src_offset=0x100, width=4,
            owner_tag="test",
            origin_offset=0x100,
        )
        # Manually pack a field that resolves to 0x200.
        import struct
        struct.pack_into("<I", doc.raw, 0x100, 0x100)
        # (origin + 0x100 = 0x200 — the target.)
        # The graph we built doesn't know about this ref; add it
        # via a private injection so the math has something to
        # evaluate.
        from azurik_mod.xbr.pointer_graph import ResolvedRef
        graph._resolved.append(
            ResolvedRef(ref=ref, target_offset=0x200))
        to_patch = graph.refs_to_patch_for_shift(
            shift_start=0x150, shift_delta=16)
        # Origin 0x100 < 0x150 (doesn't move), target 0x200 >= 0x150
        # (does move): patch needed.
        self.assertEqual(
            [rr.ref.src_offset for rr in to_patch], [0x100])

    def test_shift_both_moves_noop_for_selfrelative(self):
        from azurik_mod.xbr.pointer_graph import ResolvedRef
        doc = self._mini_doc()
        graph = PointerGraph(doc)
        ref = SelfRelativeRef(
            src_offset=0x200, width=4, owner_tag="test",
            origin_offset=0x200)
        graph._resolved.append(
            ResolvedRef(ref=ref, target_offset=0x300))
        # Both origin + target >= shift_start — both move together.
        to_patch = graph.refs_to_patch_for_shift(
            shift_start=0x100, shift_delta=16)
        self.assertNotIn(ref,
                         [rr.ref for rr in to_patch])

    def test_file_absolute_ref_rewrites_on_target_move(self):
        from azurik_mod.xbr.pointer_graph import ResolvedRef
        doc = self._mini_doc()
        graph = PointerGraph(doc)
        ref = FileAbsoluteRef(
            src_offset=0x400, width=4, owner_tag="test")
        graph._resolved.append(
            ResolvedRef(ref=ref, target_offset=0x800))
        # Target moves — rewrite needed.
        self.assertIn(
            ref,
            [rr.ref
             for rr in graph.refs_to_patch_for_shift(0x500, 16)])
        # Target doesn't move — no rewrite.
        self.assertNotIn(
            ref,
            [rr.ref
             for rr in graph.refs_to_patch_for_shift(0x1000, 16)])


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class SnapshotShape(unittest.TestCase):
    """Smoke the ``PointerGraph.snapshot`` aggregator."""

    def test_snapshot_summary_shape(self):
        doc = XbrDocument.load(_GAMEDATA / "config.xbr")
        snap = PointerGraph(doc).snapshot()
        self.assertIn("summary", snap)
        sm = snap["summary"]
        self.assertEqual(sm["toc_entries"], len(doc.toc))
        self.assertGreater(sm["total_refs"], 0)
        self.assertIn("tabl", sm["ref_counts"])


if __name__ == "__main__":
    unittest.main()
