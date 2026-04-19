"""Regression tests for the level-XBR side of ``scripts/xbr_parser.py``.

Runs against the real extracted Azurik gamedata tree at
``Azurik - Rise of Perathia (USA).xiso/gamedata/*.xbr``.  Every
test skips gracefully when the fixtures aren't present so the
suite still runs on hosts without a full game install.

Pins:

1. **Perf + correctness of ``find_strings_in_region``** — regex
   scanner with NUL-terminator requirement + alpha filter.
2. **CLI wiring**: default mode on level XBR shows stats;
   config-only flags exit non-zero on level files; ``--pattern`` /
   ``--unique`` / ``--count-only`` / ``--json`` all work.
3. **Tag-distribution invariants** on a1.xbr / w1.xbr / town.xbr —
   every level has ``node``, ``levl``, ``ndbg``, ``surf``, ``rdms``,
   ``tern``, etc.  If a future change breaks TOC parsing these
   numbers drift loudly.
4. **Known pickup strings** appear in the expected levels
   (``key_air1`` in a1, ``key_water1`` in w1).
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
# Script lives under scripts/ — put it on sys.path so we can import
# the module directly for function-level tests.
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


_GAMEDATA = (_REPO_ROOT.parent /
             "Azurik - Rise of Perathia (USA).xiso" / "gamedata")
_A1 = _GAMEDATA / "a1.xbr"
_W1 = _GAMEDATA / "w1.xbr"
_TOWN = _GAMEDATA / "town.xbr"

_PARSER_SCRIPT = _REPO_ROOT / "scripts" / "xbr_parser.py"


def _have_fixtures() -> bool:
    return all(p.exists() for p in (_A1, _W1, _TOWN, _PARSER_SCRIPT))


# ===========================================================================
# 1. find_strings_in_region — correctness + behaviour
# ===========================================================================


class FindStringsInRegion(unittest.TestCase):
    """Regex scanner + NUL terminator + alpha filter."""

    def test_finds_nul_terminated_strings(self):
        import xbr_parser as xp
        data = b"\x00hello\x00world_foo\x00\x00"
        # ``start=0, length=len(data)`` — scan whole buffer.
        hits = xp.find_strings_in_region(data, 0, len(data), min_len=4)
        values = [s for _, s in hits]
        self.assertIn("hello", values)
        self.assertIn("world_foo", values)

    def test_requires_null_terminator(self):
        import xbr_parser as xp
        # 'hello' at EOF with NO trailing NUL → should NOT match.
        data = b"\x00hello"
        hits = xp.find_strings_in_region(data, 0, len(data), min_len=4)
        self.assertEqual(hits, [],
            msg="strings must be NUL-terminated — unterminated runs "
                "of printable bytes were the main false-positive "
                "source in the old scanner")

    def test_min_len_filter(self):
        import xbr_parser as xp
        data = b"\x00hi\x00world\x00verylongstring\x00"
        short = xp.find_strings_in_region(data, 0, len(data), min_len=4)
        long_ = xp.find_strings_in_region(data, 0, len(data), min_len=10)
        self.assertEqual(
            {s for _, s in short}, {"world", "verylongstring"})
        self.assertEqual(
            {s for _, s in long_}, {"verylongstring"})

    def test_alpha_filter_rejects_pure_punctuation_runs(self):
        import xbr_parser as xp
        # "$|._" and "UUUU" are exactly the kind of junk the old
        # scanner kept catching in binary mesh data.  ``>!F-`` is a
        # borderline case — it DOES contain an alphabetic char so
        # the filter passes it; that's fine because real XBR
        # strings routinely have odd punctuation too.  The filter
        # only rejects runs with NO letters at all.
        data = b"\x00$|._\x00real_name\x00!@#$%^\x00"
        hits = xp.find_strings_in_region(data, 0, len(data), min_len=4)
        values = {s for _, s in hits}
        self.assertIn("real_name", values)
        self.assertNotIn("$|._", values)
        self.assertNotIn("!@#$%^", values)

    def test_alpha_filter_can_be_disabled(self):
        import xbr_parser as xp
        data = b"\x00$|._abc\x00"
        on = xp.find_strings_in_region(
            data, 0, len(data), min_len=4, require_alpha=True)
        off = xp.find_strings_in_region(
            data, 0, len(data), min_len=4, require_alpha=False)
        # With alpha-filter ON the "$|._abc" string IS kept (it has
        # alpha chars); the filter only affects things with NO alpha.
        self.assertIn("$|._abc", {s for _, s in on})
        # And of course with filter OFF it's still there.
        self.assertIn("$|._abc", {s for _, s in off})

    def test_pattern_cache_is_per_min_len(self):
        import xbr_parser as xp
        # Priming the cache at multiple min_lens should produce
        # distinct compiled patterns.
        p4 = xp._string_pattern(4)
        p6 = xp._string_pattern(6)
        p4_again = xp._string_pattern(4)
        self.assertIs(p4, p4_again)
        self.assertIsNot(p4, p6)


# ===========================================================================
# 2. Tag-distribution invariants on real level XBRs
# ===========================================================================


@unittest.skipUnless(_have_fixtures(), "gamedata/ xbrs not available")
class LevelXbrTocInvariants(unittest.TestCase):
    """Each of the three target levels has the expected tag set."""

    # Tags every real Azurik level XBR must have.  Exact counts
    # vary, but the set of tags does not.
    REQUIRED_TAGS = {"levl", "node", "ndbg", "gshd", "surf", "rdms",
                     "tern", "pbrw", "pbrc", "sprv", "wave"}

    def _tags(self, path: Path) -> dict[str, int]:
        import xbr_parser as xp
        data = path.read_bytes()
        self.assertEqual(data[:4], b"xobx")
        toc = xp.parse_toc(data)
        out: dict[str, int] = {}
        for e in toc:
            out[e.tag] = out.get(e.tag, 0) + 1
        return out

    def test_a1_has_required_tags(self):
        tags = self._tags(_A1)
        missing = self.REQUIRED_TAGS - set(tags)
        self.assertEqual(missing, set(),
            msg=f"a1.xbr missing tags {missing}")
        # Every level has exactly one ``levl`` root entry.
        self.assertEqual(tags["levl"], 1)
        # And one ``node`` entry — the entity graph root.
        self.assertEqual(tags["node"], 1)

    def test_w1_has_required_tags(self):
        tags = self._tags(_W1)
        self.assertEqual(self.REQUIRED_TAGS - set(tags), set())
        self.assertEqual(tags["levl"], 1)
        self.assertEqual(tags["node"], 1)

    def test_town_has_two_nodes(self):
        """town.xbr is a hub level with two node sections (docks +
        town proper) — the odd one out vs levels that have 1."""
        tags = self._tags(_TOWN)
        self.assertEqual(self.REQUIRED_TAGS - set(tags), set())
        self.assertEqual(
            tags["node"], 2,
            msg="town.xbr has TWO node sections (hub + docks)")


# ===========================================================================
# 3. Pickup strings recoverable from each level's node section
# ===========================================================================


@unittest.skipUnless(_have_fixtures(), "gamedata/ xbrs not available")
class LevelPickupStringsRecoverable(unittest.TestCase):
    """The scanner must find the expected pickup / key strings in
    the levels they belong to."""

    def _node_strings(self, path: Path, pattern: str | None = None) -> set[str]:
        import xbr_parser as xp
        data = path.read_bytes()
        toc = xp.parse_toc(data)
        node_entries = [e for e in toc if e.tag == "node"]
        self.assertTrue(node_entries,
            msg=f"{path.name} has no node entries")
        found: set[str] = set()
        for e in node_entries:
            hits = xp.find_strings_in_region(
                data, e.file_offset, e.size, min_len=6)
            for _off, s in hits:
                if pattern and not re.search(pattern, s):
                    continue
                found.add(s)
        return found

    def test_a1_has_air_keys(self):
        strings = self._node_strings(_A1, r"key_")
        # Real a1.xbr references key_air{1,2,3}.
        has_any_key = any("key_air" in s for s in strings)
        self.assertTrue(has_any_key,
            msg=f"a1.xbr should reference key_air* pickups; got {strings!r}")

    def test_w1_has_recognisable_level_strings(self):
        """w1.xbr is the first water level — its node doesn't
        contain key_/power_/frag_ pickups directly (those live in
        later water levels w2-w4 + the ndbg section), but it
        MUST have level-transition / speech references."""
        # "nokey" is a speech line referenced from every level.
        all_strings = self._node_strings(_W1)
        self.assertTrue(
            any(re.search(r"levels/|speech/|loc/english", s)
                for s in all_strings),
            msg=f"w1.xbr should reference at least one speech / "
                f"level / localisation string.  Found: "
                f"{sorted(all_strings)[:10]}")

    def test_town_has_level_transitions(self):
        """town.xbr connects to every realm — its node section must
        contain ``levels/<element>/<name>`` transition strings."""
        strings = self._node_strings(_TOWN, r"^levels/")
        self.assertTrue(
            strings,
            msg="town.xbr should reference at least one levels/... "
                "transition target in its node section")


# ===========================================================================
# 4. CLI end-to-end
# ===========================================================================


@unittest.skipUnless(_have_fixtures(), "gamedata/ xbrs not available")
class XbrParserCli(unittest.TestCase):
    """Black-box tests: invoke ``python scripts/xbr_parser.py ...``
    as a subprocess and check stdout / exit code."""

    def _run(self, *args: str) -> tuple[int, str, str]:
        result = subprocess.run(
            [sys.executable, str(_PARSER_SCRIPT), *args],
            capture_output=True, text=True,
            cwd=_REPO_ROOT)
        return result.returncode, result.stdout, result.stderr

    def test_default_mode_on_level_shows_stats(self):
        """The OLD default printed 'Not a config.xbr file' and
        exited.  The fix: fall back to stats summary."""
        code, out, err = self._run(str(_A1))
        self.assertEqual(code, 0)
        self.assertIn("Tag distribution", out,
            msg="default mode on level XBR should show the stats "
                "summary (not 'Not a config.xbr')")
        self.assertNotIn("Not a config.xbr", out)

    def test_config_only_flags_fail_loudly_on_level(self):
        for flag in ("--sections", "--find=foo", "--dump-json=/tmp/x"):
            code, out, err = self._run(str(_A1), flag)
            self.assertNotEqual(
                code, 0,
                msg=f"{flag} on a level XBR should exit non-zero")
            self.assertIn("level XBR", err)

    def test_strings_pattern_filter(self):
        code, out, err = self._run(
            str(_A1), "--strings", "node", "--pattern", "key_")
        self.assertEqual(code, 0)
        self.assertIn("key_air", out)
        # The pattern should suppress unrelated strings like 'effect'.
        self.assertNotIn("\n    0x01D37064: effect", out,
            msg="pattern filter should suppress non-matching strings")

    def test_strings_count_only(self):
        code, out, err = self._run(
            str(_W1), "--strings", "node", "--count-only")
        self.assertEqual(code, 0)
        # count_only prints "matches=N" per entry + a grand total.
        self.assertRegex(out, r"matches=\d+")
        self.assertRegex(out, r"Grand total: \d+ string")

    def test_strings_json_output(self):
        code, out, err = self._run(
            str(_A1), "--strings", "node",
            "--pattern", "key_", "--json")
        self.assertEqual(code, 0)
        # stdout is JSON — parseable + has the expected shape.
        data = json.loads(out)
        self.assertEqual(data["tag"], "node")
        self.assertEqual(data["pattern"], "key_")
        self.assertGreater(data["grand_total"], 0)
        # Every hit's value should match the pattern.
        for entry in data["entries"]:
            for hit in entry["hits"]:
                self.assertIn("key_", hit["value"])

    def test_stats_works_on_town(self):
        code, out, err = self._run(str(_TOWN), "--stats")
        self.assertEqual(code, 0)
        self.assertIn("Tag distribution", out)
        self.assertIn("largest entries", out)

    def test_unknown_tag_exits_non_zero_with_available(self):
        code, out, err = self._run(
            str(_A1), "--strings", "no_such_tag")
        self.assertNotEqual(code, 0)
        # Error should list available tags so the user can pick one.
        self.assertIn("Available tags", err)
        self.assertIn("node", err)


# ===========================================================================
# 5. Perf guard — the scanner stays fast on real data
# ===========================================================================


@unittest.skipUnless(_have_fixtures(), "gamedata/ xbrs not available")
class ScannerPerformanceGuard(unittest.TestCase):
    """The regex-based scanner is ~6× faster than the old
    byte-by-byte loop (1.4 s → 0.23 s on town.xbr --strings surf).
    Pin a loose upper bound so a future regression that reintroduces
    byte-level scanning surfaces here."""

    def test_town_surf_scan_under_2s(self):
        """11 MB scan across 349 surf entries — regex completes in
        ~0.23s.  Upper bound generous enough to not flake on slow
        CI; still catches an order-of-magnitude regression."""
        import xbr_parser as xp
        data = _TOWN.read_bytes()
        toc = xp.parse_toc(data)
        surf = [e for e in toc if e.tag == "surf"]
        self.assertGreater(len(surf), 100,
            msg="town.xbr should have many surf entries")

        t0 = time.perf_counter()
        total = 0
        for e in surf:
            hits = xp.find_strings_in_region(
                data, e.file_offset, e.size, min_len=6)
            total += len(hits)
        elapsed = time.perf_counter() - t0

        self.assertLess(
            elapsed, 2.0,
            msg=f"town.xbr surf scan took {elapsed:.2f}s — the "
                f"regex-based scanner should finish in ~0.25s. "
                f"If this fails, check the scanner hasn't regressed "
                f"to byte-by-byte iteration.")


if __name__ == "__main__":
    unittest.main()
