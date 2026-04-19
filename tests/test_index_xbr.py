"""Tests for ``azurik_mod.assets.index_xbr`` — index.xbr parser.

Pins the vanilla-ISO record layout + string pool invariants so a
future parser regression or a re-RE that changes field semantics
flips tests red immediately.

Format partially decoded in docs/LEARNINGS.md § index.xbr — this
test file exercises the parts we have confidence in and leaves
the unpinned semantics (exact off1/off2 semantics) to follow-up
work.
"""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from azurik_mod.assets.index_xbr import (  # noqa: E402
    IndexRecord,
    IndexXbr,
    load_index_xbr,
)

_VANILLA_INDEX = (_REPO.parent /
                  "Azurik - Rise of Perathia (USA).xiso" /
                  "gamedata" / "index" / "index.xbr")


# ---------------------------------------------------------------------------
# Synthetic-fixture tests (always run)
# ---------------------------------------------------------------------------


def _build_synthetic_indx(records: list[tuple[int, int, str, int, int]],
                          *, pool_bytes: bytes = b"\x2d\x81\x01\x00levl"
                                                 b"hello\x00world\x00"
                         ) -> bytes:
    """Build a minimal index.xbr blob for unit tests.

    ``records`` is a list of ``(length, off1, fourcc, disc, off2)``
    tuples — same layout as in the real file.  A 4 KiB XBR header
    + 16-byte indx header precede the records.  The string pool is
    appended after the records; callers supply their own payload.
    """
    blob = bytearray()

    # XBR header: "xobx" + padding to 0x40 + single TOC entry.
    blob.extend(b"xobx")
    blob.extend(b"\x00" * (0x40 - 4))
    # Minimal TOC row: size, tag, flags, file_offset (all u32).
    blob.extend(struct.pack("<I", 0))  # size — filled in below
    blob.extend(b"indx")
    blob.extend(struct.pack("<II", 8, 0))
    # Pad to 0x1000 payload offset.
    blob.extend(b"\x00" * (0x1000 - len(blob)))

    # indx 16-byte header.
    count = len(records) + 1  # +1 sentinel so count_field matches vanilla idiom
    blob.extend(struct.pack("<IIII", count, 4, 24, 0xEFFC))
    # records
    for s_len, off1, tag, disc, off2 in records:
        blob.extend(struct.pack("<II", s_len, off1))
        blob.extend(tag.encode("ascii").ljust(4, b"\x00"))
        blob.extend(bytes([disc, 0, 0, 0]))
        blob.extend(struct.pack("<I", off2))
    # Sentinel "record" — first 20 bytes of the pool masquerading as
    # a record (matches what the vanilla file actually does).
    padding = 20 - len(pool_bytes) % 20 if len(pool_bytes) % 20 else 0
    blob.extend(pool_bytes[:20] if len(pool_bytes) >= 20
                else pool_bytes.ljust(20, b"\x00"))
    # Remainder of pool.
    if len(pool_bytes) > 20:
        blob.extend(pool_bytes[20:])
    # Fix up TOC row size: payload size = everything after file offset 0x1000.
    size = len(blob) - 0x1000
    struct.pack_into("<I", blob, 0x40, size)
    return bytes(blob)


class ParserSynthetic(unittest.TestCase):
    """Unit tests using synthetic blobs — no game install needed."""

    def test_parses_simple_blob(self):
        """Two trivial records with a small string pool."""
        records = [
            (14, 0x0010, "body", 0x21, 0x0020),
            (14, 0x0020, "banm", 0x1C, 0x0030),
        ]
        blob = _build_synthetic_indx(records)
        with tempfile.NamedTemporaryFile(
                suffix=".xbr", delete=False) as tmp:
            tmp.write(blob)
            tmp_path = Path(tmp.name)
        try:
            ix = load_index_xbr(tmp_path)
            self.assertEqual(len(ix.records), 2)
            self.assertEqual(ix.records[0].fourcc, "body")
            self.assertEqual(ix.records[0].length, 14)
            self.assertEqual(ix.records[0].discriminator, 0x21)
            self.assertEqual(ix.records[1].fourcc, "banm")
        finally:
            tmp_path.unlink()

    def test_rejects_bad_magic(self):
        blob = b"XXXX" + b"\x00" * 0x20
        with tempfile.NamedTemporaryFile(
                suffix=".xbr", delete=False) as tmp:
            tmp.write(blob)
            tmp_path = Path(tmp.name)
        try:
            with self.assertRaises(ValueError):
                load_index_xbr(tmp_path)
        finally:
            tmp_path.unlink()

    def test_rejects_truncated_file(self):
        with tempfile.NamedTemporaryFile(
                suffix=".xbr", delete=False) as tmp:
            tmp.write(b"xobx\x00\x00\x00\x00")  # 8 bytes
            tmp_path = Path(tmp.name)
        try:
            with self.assertRaises(ValueError):
                load_index_xbr(tmp_path)
        finally:
            tmp_path.unlink()

    def test_stops_at_unknown_fourcc(self):
        """A bad tag (``"XXXX"``) in the middle of the record table
        must terminate parsing — the parser must NOT return
        corrupted records past the sentinel."""
        # Record 0: valid body; Record 1: "XXXX" tag → stop early.
        records = [
            (14, 0x0010, "body", 0x21, 0x0020),
            (14, 0x0020, "XXXX", 0x00, 0x0030),  # invalid tag
        ]
        blob = _build_synthetic_indx(records)
        with tempfile.NamedTemporaryFile(
                suffix=".xbr", delete=False) as tmp:
            tmp.write(blob)
            tmp_path = Path(tmp.name)
        try:
            ix = load_index_xbr(tmp_path)
            self.assertEqual(len(ix.records), 1,
                msg="parser must stop at first unknown fourcc")
            self.assertEqual(ix.records[0].fourcc, "body")
        finally:
            tmp_path.unlink()


# ---------------------------------------------------------------------------
# Vanilla-ISO tests (skip when fixture absent)
# ---------------------------------------------------------------------------


@unittest.skipUnless(_VANILLA_INDEX.exists(),
                     "vanilla index.xbr fixture required")
class VanillaIndex(unittest.TestCase):
    """Ground-truth invariants pinned from the shipped vanilla file.

    These were measured during the April 2026 RE pass.  If any of
    them change, the file was re-dumped or our parser regressed.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.ix = load_index_xbr(_VANILLA_INDEX)

    def test_3071_real_records(self):
        """The header declares 3072 but the 3072nd is the pool
        sentinel — the parser should return exactly 3071 valid
        records."""
        self.assertEqual(len(self.ix.records), 3071)
        self.assertEqual(self.ix.count_field, 3072)

    def test_header_fields(self):
        self.assertEqual(self.ix.version, 4)
        self.assertEqual(self.ix.header_hint, 24)
        self.assertEqual(self.ix.pool_hint, 0xEFFC)

    def test_tag_distribution_matches_RE_pass(self):
        """Counts exactly match what we decoded during the April
        2026 RE session.  If this drifts the parser lost some
        records — check the fourcc filter in load_index_xbr."""
        expected = {
            "surf": 1099,
            "wave": 816,
            "banm": 712,
            "node": 230,
            "body": 160,
            "levl": 32,
            "tabl": 18,
            "font": 4,
        }
        self.assertEqual(self.ix.tag_counts(), expected)

    def test_pool_starts_at_0x10000_with_magic(self):
        """The string pool begins at file offset 0x10000 with
        magic dword 0x0001812D and 4-char tag 'levl'."""
        self.assertEqual(self.ix.pool_start, 0x10000)
        self.assertEqual(self.ix.pool_magic, 0x0001812D)
        self.assertEqual(self.ix.pool_tag, "levl")

    def test_first_record_is_body_characters_xbr(self):
        """The very first record maps a ``body`` asset to
        characters.xbr.  The 14-byte length matches
        ``len("characters.xbr")``."""
        r0 = self.ix.records[0]
        self.assertEqual(r0.fourcc, "body")
        self.assertEqual(r0.length, 14)
        self.assertEqual(r0.discriminator, 0x21)

    def test_iter_asset_paths_finds_all_expected_categories(self):
        """Pool walk should surface at least the four big asset
        categories (characters, levels, fx, interface)."""
        paths = self.ix.iter_asset_paths(min_len=8)
        categories = {"characters/", "levels/", "fx/", "interface/"}
        for cat in categories:
            self.assertTrue(
                any(p.startswith(cat) for p in paths),
                msg=f"no paths starting with {cat!r} found")

    def test_known_character_assets_present(self):
        """Specific known characters should show up in the pool."""
        paths = set(self.ix.iter_asset_paths(min_len=8))
        # Garret is the main character, air_elemental is the tutorial foe.
        known = (
            "characters/air_elemental/attack_1",
            "characters.xbr",
        )
        for asset in known:
            self.assertIn(asset, paths,
                msg=f"expected asset {asset!r} not in pool")


# ---------------------------------------------------------------------------
# Shim-header drift guards
# ---------------------------------------------------------------------------


class ShimHeaderCoverage(unittest.TestCase):
    """Pins newly-discovered VA anchors against their ground-truth
    bytes in the vanilla XBE.  Catches hand-edit drift in
    azurik.h."""

    @classmethod
    def setUpClass(cls) -> None:
        xbe_path = (_REPO.parent /
                    "Azurik - Rise of Perathia (USA).xiso" /
                    "default.xbe")
        if not xbe_path.exists():
            raise unittest.SkipTest("vanilla default.xbe required")
        cls.xbe = xbe_path.read_bytes()

    def _find_string_va(self, needle: bytes) -> int | None:
        """Locate a NUL-terminated ASCII string and return its VA."""
        from azurik_mod.patching.xbe import file_to_va
        p = self.xbe.find(needle + b"\x00")
        return file_to_va(p) if p >= 0 else None

    def test_dev_menu_string_anchor(self):
        va = self._find_string_va(b"levels/selector")
        self.assertEqual(va, 0x001A1E3C,
            msg="AZURIK_STR_LEVELS_SELECTOR_VA drifted")

    def test_training_room_string_anchor(self):
        va = self._find_string_va(b"levels/training_room")
        self.assertEqual(va, 0x001A1E4C,
            msg="AZURIK_STR_LEVELS_TRAINING_VA drifted")

    def test_index_xbr_path_string_anchor(self):
        va = self._find_string_va(b"index\\index.xbr")
        self.assertEqual(va, 0x0019ADB0,
            msg="AZURIK_STR_INDEX_XBR_PATH_VA drifted")

    def test_new_vanilla_symbols_registered(self):
        """Both newly-documented vanilla symbols must be
        registered (coverage audit would otherwise miss them)."""
        from azurik_mod.patching.vanilla_symbols import all_entries
        names = {s.name for s in all_entries()}
        self.assertIn("load_asset_by_fourcc", names)
        self.assertIn("dev_menu_flag_check", names)


if __name__ == "__main__":
    unittest.main()
