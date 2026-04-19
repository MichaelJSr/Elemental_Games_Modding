"""Tests for ``azurik_mod.assets`` — prefetch + filelist manifests.

Runs against the real Azurik ISO when present, falls back to
synthetic fixtures otherwise.  Pins:

- prefetch-lists stanza counts (7 globals, 24 levels, 5 extras,
  1 alias) — catches drift if someone re-generates the parser or
  hand-edits the manifest.
- neighbor graph topology invariants — no self-loops, neighbors
  reference real tags, the graph is undirected for playable
  edges (if A → B, then B → A).
- filelist.txt byte-count + MD5 format.
- Verify flow catches missing/corrupt files in a synthetic iso
  root.
- Level/global classification matches observed file layout.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve().parent
_REPO = _THIS.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from azurik_mod.assets import (  # noqa: E402
    FilelistManifest,
    PrefetchManifest,
    PrefetchTag,
    load_prefetch,
)
from azurik_mod.assets.filelist import (  # noqa: E402
    FileEntry,
    IntegrityIssue,
    load_filelist,
)

_ISO_ROOT = (_REPO.parent / "Azurik - Rise of Perathia (USA).xiso")
_PREFETCH = _ISO_ROOT / "prefetch-lists.txt"
_FILELIST = _ISO_ROOT / "filelist.txt"


# ===========================================================================
# Synthetic-fixture tests (always run)
# ===========================================================================

_SYNTHETIC_PREFETCH = textwrap.dedent("""
    tag=always
    file=index\\index.xbr
    file=%LANGUAGE%.xbr
    file=config.xbr

    tag=default
    file=training_room.xbr

    tag=a1
    file=A1.xbr
    neighbor=a6
    neighbor=e6

    tag=a6
    file=A6.xbr
    neighbor=a6-extra
    neighbor=a1
    neighbor=town

    tag=a6-extra
    file=diskreplace_air.xbr
    file=diskreplchars.xbr

    tag=town
    file=town.xbr
    neighbor=a6
""").strip()


class PrefetchSynthetic(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False)
        self.tmp.write(_SYNTHETIC_PREFETCH)
        self.tmp.close()
        self.path = Path(self.tmp.name)
        self.m = load_prefetch(self.path)

    def tearDown(self) -> None:
        os.unlink(self.path)

    def test_stanza_counts(self):
        self.assertEqual(len(self.m.tags), 6)
        # Of the 6 stanzas: ``always`` is global, ``default`` is an
        # alias, ``a6-extra`` is an extras pack → exactly 3 real
        # levels (``a1``, ``a6``, ``town``).
        names = [t.name for t in self.m.level_tags()]
        self.assertEqual(set(names), {"a1", "a6", "town"})
        self.assertEqual(len(self.m.level_tags()), 3)

    def test_default_is_alias_not_level(self):
        default = self.m.tag("default")
        self.assertIsNotNone(default)
        self.assertTrue(default.is_alias)
        self.assertFalse(default.is_level)

    def test_extras_detected(self):
        extras = [t.name for t in self.m.extra_tags()]
        self.assertEqual(extras, ["a6-extra"])
        self.assertTrue(self.m.tag("a6-extra").is_extra)

    def test_global_vs_level_classification(self):
        self.assertTrue(self.m.is_global_file("config.xbr"))
        self.assertTrue(self.m.is_global_file("english.xbr"),
            msg="%LANGUAGE% placeholder should resolve to 'english'")
        self.assertTrue(self.m.is_global_file("french.xbr"))
        self.assertFalse(self.m.is_global_file("a1.xbr"))

        self.assertTrue(self.m.is_level_file("a1.xbr"))
        self.assertTrue(self.m.is_level_file("diskreplace_air.xbr"))
        self.assertFalse(self.m.is_level_file("config.xbr"))
        self.assertFalse(self.m.is_level_file("nonexistent.xbr"),
            msg="Files absent from the manifest are NOT levels")

    def test_neighbors_roundtrip(self):
        self.assertEqual(set(self.m.neighbors_of("a1")), {"a6", "e6"})
        # Playable-neighbors excludes *-extra
        self.assertEqual(
            set(self.m.playable_neighbors("a6")),
            {"a1", "town"})
        self.assertIn("a6-extra", self.m.neighbors_of("a6"))

    def test_language_resolution(self):
        french = self.m.resolve_language("french")
        self.assertIn("french.xbr", french.global_files())
        self.assertNotIn("%LANGUAGE%.xbr", french.global_files())
        # Case-insensitivity of the placeholder:
        weird_in = textwrap.dedent("""
            tag=always
            file=%Language%.xbr
        """).strip()
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False)
        tmp.write(weird_in); tmp.close()
        try:
            m = load_prefetch(tmp.name)
            self.assertIn("spanish.xbr",
                          m.resolve_language("spanish").global_files())
        finally:
            os.unlink(tmp.name)

    def test_malformed_file_before_tag(self):
        bad = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False)
        bad.write("file=a.xbr\n"); bad.close()
        try:
            with self.assertRaises(ValueError):
                load_prefetch(bad.name)
        finally:
            os.unlink(bad.name)


class FilelistSynthetic(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        (self.root / "gamedata").mkdir()
        # Create two files matching a hand-rolled manifest.
        good = b"hello" * 100
        (self.root / "gamedata" / "good.bin").write_bytes(good)
        bad_size = b"nope"
        (self.root / "gamedata" / "wrong_size.bin").write_bytes(bad_size)
        corrupt = b"X" * 500
        (self.root / "gamedata" / "corrupt.bin").write_bytes(corrupt)
        self.manifest_text = (
            "\\gamedata\\\n"
            f"f {hashlib.md5(good).hexdigest()} {len(good)} good.bin\n"
            # Declare a larger size than the actual file:
            f"f {hashlib.md5(bad_size).hexdigest()} 10 wrong_size.bin\n"
            # Correct size, WRONG md5:
            f"f {'0' * 32} {len(corrupt)} corrupt.bin\n"
            f"f {hashlib.md5(b'x').hexdigest()} 1 missing.bin\n"
        )
        self.fl_path = self.root / "filelist.txt"
        self.fl_path.write_text(self.manifest_text)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.root)

    def test_parses_entries(self):
        m = load_filelist(self.fl_path)
        self.assertEqual(len(m.entries), 4)
        paths = [e.path for e in m.entries]
        self.assertEqual(paths, [
            "gamedata/good.bin", "gamedata/wrong_size.bin",
            "gamedata/corrupt.bin", "gamedata/missing.bin"])

    def test_verify_detects_all_three_issue_kinds(self):
        m = load_filelist(self.fl_path)
        issues = m.verify(self.root)
        kinds = {i.kind: i for i in issues}
        self.assertIn("missing", kinds)
        self.assertIn("size_mismatch", kinds)
        self.assertIn("md5_mismatch", kinds)
        self.assertEqual(len(issues), 3,
            msg=f"expected 3 issues, got {[str(i) for i in issues]}")

    def test_verify_no_md5_only_checks_size(self):
        m = load_filelist(self.fl_path)
        issues = m.verify(self.root, check_md5=False)
        kinds = {i.kind for i in issues}
        self.assertIn("missing", kinds)
        self.assertIn("size_mismatch", kinds)
        self.assertNotIn("md5_mismatch", kinds,
            msg="size-only verify must NOT report hash mismatches")

    def test_verify_limit_caps_output(self):
        m = load_filelist(self.fl_path)
        limited = m.verify(self.root, limit=1)
        self.assertEqual(len(limited), 1)

    def test_empty_file_rejected(self):
        empty = self.root / "empty.txt"
        empty.write_text("")
        with self.assertRaises(ValueError):
            load_filelist(empty)


# ===========================================================================
# Real-ISO tests (skip when fixtures missing)
# ===========================================================================


@unittest.skipUnless(_PREFETCH.exists(),
                     "vanilla prefetch-lists.txt fixture required")
class VanillaPrefetchManifest(unittest.TestCase):
    """Pins canonical counts + topology of the shipped manifest.

    These are ground-truth: if any of them change, a re-gen of the
    file has happened or the ISO is tampered.  See
    docs/LEARNINGS.md § prefetch-lists.txt.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.m = load_prefetch(_PREFETCH)

    def test_seven_global_files(self):
        self.assertEqual(len(self.m.global_files()), 7)

    def test_twenty_four_levels(self):
        self.assertEqual(len(self.m.level_tags()), 24)

    def test_five_extra_packs(self):
        self.assertEqual(len(self.m.extra_tags()), 5)
        self.assertEqual(
            {t.name for t in self.m.extra_tags()},
            {"a6-extra", "e5-extra", "f6-extra", "life-extra",
             "w3-extra"})

    def test_expected_level_set(self):
        expected = {"a1", "a3", "a5", "a6", "airship", "airship_trans",
                    "d1", "d2", "e2", "e5", "e6", "e7",
                    "f1", "f2", "f3", "f4", "f6", "life", "town",
                    "training_room", "w1", "w2", "w3", "w4"}
        self.assertEqual(set(self.m.level_names()), expected)

    def test_no_self_loops(self):
        for t in self.m.level_tags():
            self.assertNotIn(t.name, t.neighbors,
                msg=f"{t.name} lists itself as neighbor")

    def test_only_cut_level_is_unknown_neighbor(self):
        """Every ``neighbor=`` reference resolves to a real stanza,
        with ONE documented exception: ``f7`` is a cut / removed
        level that ``f1`` still advertises.  See
        ``azurik_mod/randomizer/shufflers.py`` ``EXCLUDE_TRANSITIONS``.
        """
        known = {t.name for t in self.m.tags}
        unknown: dict[str, set[str]] = {}
        for t in self.m.level_tags():
            for n in t.neighbors:
                if n not in known:
                    unknown.setdefault(t.name, set()).add(n)
        self.assertEqual(unknown, {"f1": {"f7"}},
            msg="Only 'f1 → f7' (cut level) may reference an "
                "unknown tag; anything else is manifest drift")

    def test_adjacency_is_directed_not_symmetric(self):
        """DOCUMENTS a subtle property of this manifest: the
        ``neighbor=`` graph is a **directed prefetch hint** used
        by the streaming loader — NOT an undirected portal graph.
        Many zones advertise neighbors that do not advertise them
        back (e.g. ``a6 → town`` but ``town`` only points at its
        five main-hub neighbors).  ``airship_trans`` is the extreme
        case: every airport-adjacent zone prefetches it, but it
        has zero outbound neighbors of its own.

        Any code that treats this graph as undirected MUST either
        explicitly symmetrise it or use the raw portal data scraped
        from the level XBRs instead.
        """
        adj = self.m.adjacency()
        asymmetric = [(a, b) for a, ns in adj.items() for b in ns
                      if a not in adj.get(b, ())]
        self.assertGreater(len(asymmetric), 10,
            msg="Expected the manifest to be asymmetric — if it's "
                "now symmetric, the game's prefetch layout changed "
                "and downstream code needs to be re-audited")

    def test_airship_trans_is_terminal(self):
        """``airship_trans`` is a one-way transition zone — many
        levels prefetch it, it prefetches no one."""
        trans = self.m.tag("airship_trans")
        self.assertIsNotNone(trans)
        self.assertEqual(trans.neighbors, ())

    def test_training_room_is_separate_level(self):
        """Verifies the ``default`` alias handling doesn't collapse
        ``training_room`` into it."""
        tr = self.m.tag("training_room")
        self.assertIsNotNone(tr)
        self.assertTrue(tr.is_level)
        self.assertIn("w1", tr.neighbors)

    def test_always_lists_seven_known_globals(self):
        got = list(self.m.global_files())
        for needed in ("config.xbr", "fx.xbr", "characters.xbr",
                       "hourglass.xbr", "interface.xbr",
                       "index/index.xbr"):
            self.assertIn(needed, got)
        self.assertTrue(
            any("%LANGUAGE%" in f for f in got),
            msg="Globals should reference the %LANGUAGE% placeholder")


@unittest.skipUnless(_FILELIST.exists(),
                     "vanilla filelist.txt fixture required")
class VanillaFilelistManifest(unittest.TestCase):
    """Pins counts + sizes of the shipped manifest."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.m = load_filelist(_FILELIST)

    def test_expected_entry_count(self):
        # 41 XBRs in gamedata/ + 1 in gamedata/index/ = 42.
        self.assertEqual(len(self.m.entries), 42)

    def test_total_size_roughly_1gb(self):
        total = self.m.total_size()
        self.assertGreater(total, 950_000_000)
        self.assertLess(total, 1_050_000_000)

    def test_md5_format(self):
        for e in self.m.entries:
            self.assertEqual(len(e.md5), 32)
            int(e.md5, 16)  # must parse as hex

    def test_lookup_consistency(self):
        self.assertIsNotNone(self.m.lookup("a1.xbr"))
        self.assertIsNotNone(self.m.lookup("A1.XBR"))  # case-insensitive
        self.assertIsNone(self.m.lookup("nope.xbr"))


@unittest.skipUnless(_ISO_ROOT.exists() and _PREFETCH.exists()
                     and _FILELIST.exists(),
                     "full vanilla ISO directory required")
class VanillaCrossCheck(unittest.TestCase):
    """Cross-reference the two manifests against the actual
    directory contents.  Everything referenced by prefetch should
    appear in filelist, and vice versa."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.pref = load_prefetch(_PREFETCH)
        cls.fl = load_filelist(_FILELIST)

    def test_every_prefetch_file_appears_in_filelist(self):
        pref_basenames = {f.split("/")[-1].lower()
                          for f in self.pref.all_referenced_files()
                          if "%language%" not in f.lower()}
        fl_basenames = {Path(e.path).name.lower()
                        for e in self.fl.entries}
        missing = pref_basenames - fl_basenames
        self.assertFalse(missing,
            msg=f"prefetch lists files not in filelist: {missing}")

    def test_english_resolved_global_exists(self):
        resolved = self.pref.resolve_language("english")
        self.assertIn("english.xbr", resolved.global_files())
        self.assertIsNotNone(self.fl.lookup("english.xbr"))

    def test_orphan_xbrs_are_loc_and_selector(self):
        """Files in filelist but NOT in prefetch = dev artefacts."""
        pref = {f.split("/")[-1].lower()
                for f in self.pref.all_referenced_files()
                if "%language%" not in f.lower()}
        # Resolve language placeholder so english.xbr isn't flagged.
        pref |= {f.replace("%LANGUAGE%", lang).lower()
                 for f in self.pref.global_files()
                 for lang in ("english",)}
        orphans = {Path(e.path).name.lower()
                   for e in self.fl.entries
                   if e.path.endswith(".xbr")
                   and Path(e.path).name.lower() not in pref}
        # Empirically: loc.xbr + selector.xbr (never streamed by
        # the level loader; selector handles element-disk UI).
        self.assertEqual(orphans, {"loc.xbr", "selector.xbr"},
            msg=f"Unexpected manifest orphans: {orphans}")


class IsoVerifyCli(unittest.TestCase):
    """End-to-end CLI smoke tests for ``azurik-mod iso-verify``.

    Runs against a tiny synthetic ISO tree so the test doesn't
    depend on the real 1 GB game install.  Exercises the
    happy-path, the ``missing file → exit 1`` path, and the
    ``--no-md5`` fast-path.
    """

    @classmethod
    def setUpClass(cls) -> None:
        import shutil
        import tempfile
        cls._tmp = Path(tempfile.mkdtemp(prefix="azurik-iso-"))
        gd = cls._tmp / "gamedata"
        gd.mkdir()
        (gd / "index").mkdir()

        # One small "good" xbr + one file we'll poke in variants.
        good = b"X" * 256
        (gd / "tiny.xbr").write_bytes(good)
        (gd / "index" / "index.xbr").write_bytes(b"I" * 128)

        (cls._tmp / "prefetch-lists.txt").write_text(textwrap.dedent("""
            tag=always
            file=index\\index.xbr
            file=tiny.xbr

            tag=town
            file=tiny.xbr
        """).strip())
        (cls._tmp / "filelist.txt").write_text(
            "\\\n"
            f"f {hashlib.md5(good).hexdigest()} {len(good)} tiny.xbr\n"
            "d index\n"
            "\\index\\\n"
            f"f {hashlib.md5(b'I' * 128).hexdigest()} 128 index.xbr\n")

    @classmethod
    def tearDownClass(cls) -> None:
        import shutil
        shutil.rmtree(cls._tmp)

    def _run(self, *flags: str) -> tuple[int, str, str]:
        import subprocess
        out = subprocess.run(
            [sys.executable, "-m", "azurik_mod", "iso-verify",
             str(self._tmp), *flags],
            capture_output=True, text=True, cwd=str(_REPO))
        return out.returncode, out.stdout, out.stderr

    def test_happy_path_exit_zero(self):
        rc, stdout, _ = self._run("--no-md5")
        self.assertEqual(rc, 0, msg=stdout)
        self.assertIn("OK", stdout)

    def test_graph_prints_town(self):
        rc, stdout, _ = self._run("--no-md5", "--graph")
        self.assertEqual(rc, 0)
        self.assertIn("town", stdout)
        self.assertIn("Level adjacency graph", stdout)

    def test_missing_file_exits_nonzero(self):
        # Corrupt the ISO by removing tiny.xbr.
        target = self._tmp / "gamedata" / "tiny.xbr"
        backup = target.read_bytes()
        target.unlink()
        try:
            rc, stdout, _ = self._run("--no-md5")
            self.assertEqual(rc, 1)
            self.assertIn("missing: tiny.xbr", stdout)
        finally:
            target.write_bytes(backup)

    def test_md5_mismatch_exits_nonzero(self):
        target = self._tmp / "gamedata" / "tiny.xbr"
        backup = target.read_bytes()
        target.write_bytes(b"Y" * len(backup))  # same size, wrong md5
        try:
            rc, stdout, _ = self._run()
            self.assertEqual(rc, 1)
            self.assertIn("md5 mismatch", stdout)
        finally:
            target.write_bytes(backup)


@unittest.skipUnless(_PREFETCH.exists(),
                     "vanilla prefetch-lists.txt fixture required")
class PrefetchVsHardcodedDelta(unittest.TestCase):
    """Guards the documented delta between the prefetch manifest
    and the randomizer's ``LEVEL_PATHS``.

    If somebody adds a new level to the game or corrects an
    omission, this test flips red so the mismatch is spotted
    immediately instead of silently excluded from randomization.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.pref = load_prefetch(_PREFETCH)
        from azurik_mod.randomizer.shufflers import LEVEL_PATHS
        cls.hardcoded = set(LEVEL_PATHS.keys())

    def test_expected_delta(self):
        """Exactly ``{training_room, airship_trans}`` may be in the
        prefetch manifest but missing from ``LEVEL_PATHS`` — both
        are documented in shufflers.py's comment block."""
        manifest = set(self.pref.level_names())
        missing_from_hardcoded = manifest - self.hardcoded
        self.assertEqual(
            missing_from_hardcoded,
            {"training_room", "airship_trans"},
            msg=("Unexpected drift: prefetch manifest contains "
                 f"{sorted(missing_from_hardcoded)} but the randomizer's "
                 "LEVEL_PATHS doesn't — either add them to LEVEL_PATHS "
                 "or document the exclusion in the comment block"))
        missing_from_manifest = self.hardcoded - manifest
        self.assertEqual(
            missing_from_manifest, set(),
            msg=("LEVEL_PATHS references levels that don't exist in "
                 f"the game's prefetch manifest: {missing_from_manifest}"))


class VerifyExtractedIsoHook(unittest.TestCase):
    """``azurik_mod.iso.pack.verify_extracted_iso`` is called by
    every unpack path in the builder pipeline.  It must:

    - Detect size-mismatches against filelist.txt
    - Return the issue count (0 == clean)
    - Silently skip when filelist.txt is absent (non-Azurik ISO)
    - Never raise — corrupted extractions should warn, not abort
    """

    def setUp(self) -> None:
        import shutil
        import tempfile
        self.root = Path(tempfile.mkdtemp(prefix="azurik-xh-"))
        (self.root / "gamedata").mkdir()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.root)

    def _write_manifest(self, payload: bytes, declared_size: int) -> None:
        (self.root / "gamedata" / "t.xbr").write_bytes(payload)
        md5_hex = hashlib.md5(payload).hexdigest()
        (self.root / "filelist.txt").write_text(
            "\\\n"
            f"f {md5_hex} {declared_size} t.xbr\n")

    def test_clean_extraction_returns_zero(self):
        from azurik_mod.iso.pack import verify_extracted_iso
        payload = b"Y" * 100
        self._write_manifest(payload, len(payload))
        self.assertEqual(verify_extracted_iso(self.root), 0)

    def test_missing_filelist_skipped_silently(self):
        """Not every ISO is Azurik — absent filelist.txt is OK."""
        from azurik_mod.iso.pack import verify_extracted_iso
        self.assertEqual(verify_extracted_iso(self.root), 0)

    def test_size_mismatch_detected_not_raised(self):
        from azurik_mod.iso.pack import verify_extracted_iso
        # Write 50 bytes but declare 100 — a truncated extraction
        # scenario that xdvdfs can hit silently.
        (self.root / "gamedata" / "t.xbr").write_bytes(b"Y" * 50)
        md5_hex = hashlib.md5(b"Y" * 50).hexdigest()
        (self.root / "filelist.txt").write_text(
            "\\\n"
            f"f {md5_hex} 100 t.xbr\n")
        # Must return >0 and NOT raise.
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            n = verify_extracted_iso(self.root)
        self.assertGreater(n, 0)
        self.assertIn("WARNING", buf.getvalue())
        self.assertIn("size mismatch", buf.getvalue())

    def test_missing_file_detected_not_raised(self):
        from azurik_mod.iso.pack import verify_extracted_iso
        # Manifest declares a file that doesn't exist on disk.
        (self.root / "filelist.txt").write_text(
            "\\\n"
            "f abc 42 phantom.xbr\n")
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            n = verify_extracted_iso(self.root)
        self.assertEqual(n, 1)
        self.assertIn("missing", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
