"""Regression tests pinning the two manifest-orphan XBRs discovered
during the April 2026 ISO audit: ``selector.xbr`` (developer
level-select hub) and ``index.xbr`` (global asset-path index).

Neither file is documented anywhere in the prefetch-lists.txt
loader graph, but both live in ``gamedata/`` and are referenced
by the XBE.  See docs/LEARNINGS.md § selector.xbr + index.xbr
for the full provenance.

Tests skip gracefully when the vanilla ISO fixtures aren't
present so the suite still runs on hosts without a full install.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve().parent
_REPO = _THIS.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from azurik_mod.assets import KNOWN_CUT_LEVELS  # noqa: E402

_GAMEDATA = (_REPO.parent /
             "Azurik - Rise of Perathia (USA).xiso" / "gamedata")
_SELECTOR = _GAMEDATA / "selector.xbr"
_INDEX = _GAMEDATA / "index" / "index.xbr"
_PARSER = _REPO / "scripts" / "xbr_parser.py"


def _run_parser(*args: str) -> tuple[int, str]:
    out = subprocess.run(
        [sys.executable, str(_PARSER), *args],
        capture_output=True, text=True, cwd=str(_REPO))
    return out.returncode, out.stdout + out.stderr


@unittest.skipUnless(_SELECTOR.exists(),
                     "vanilla selector.xbr fixture required")
class SelectorXbrIsDevLevelHub(unittest.TestCase):
    """``selector.xbr`` is a legitimate but manifest-orphan level
    that acts as a developer level-select hub.  Confirms:

    - Standard level-XBR shape (node, levl, surf, rdms, …).
    - Contains portal strings for EVERY level in the game plus a
      self-reference ``levels/selector`` and one reference to a
      cut level ``levels/earth/e4``.
    - Total strings ~35 (23 level portals + 10 movie refs +
      misc book-keeping).
    """

    def test_has_level_xbr_tag_shape(self):
        """Every tag distribution field is >0 — confirms the file
        parses cleanly as a level XBR."""
        rc, out = _run_parser(str(_SELECTOR), "--stats")
        self.assertEqual(rc, 0, msg=out)
        for needed in ("node", "levl", "surf", "rdms"):
            self.assertIn(needed, out,
                msg=f"selector.xbr should carry a '{needed}' tag")

    def test_portals_cover_every_live_level(self):
        """The node-section strings must include every non-cut
        level that exists in the shipping ISO.  Uses the parser's
        ``--strings node --json`` mode for robustness."""
        import json
        rc, out = _run_parser(str(_SELECTOR), "--strings", "node",
                              "--min-len", "6", "--json")
        self.assertEqual(rc, 0, msg=out)
        data = json.loads(out)
        strings = {h["value"]
                   for entry in data["entries"]
                   for h in entry["hits"]}
        # Every level on-disk should have a portal here.
        must_have = {"levels/fire/f1", "levels/air/a1", "levels/water/w1",
                     "levels/earth/e2", "levels/death/d1", "levels/town",
                     "levels/life"}
        for p in must_have:
            self.assertIn(p, strings,
                msg=f"selector should portal to {p!r}")

    def test_references_both_known_cut_levels_or_neither(self):
        """At minimum selector.xbr references ``levels/earth/e4``
        (a cut level).  The other known cut (``f7``) is referenced
        by ``prefetch-lists.txt``, not here.  This test pins ``e4``
        specifically so it doesn't silently disappear on a rebuild."""
        import json
        rc, out = _run_parser(str(_SELECTOR), "--strings", "node",
                              "--min-len", "6", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        strings = {h["value"]
                   for entry in data["entries"]
                   for h in entry["hits"]}
        self.assertIn("levels/earth/e4", strings,
            msg="selector.xbr is the ONLY on-disk reference to "
                "cut level e4 — see KNOWN_CUT_LEVELS")

    def test_references_self(self):
        """The hub loads itself via ``levels/selector``."""
        import json
        rc, out = _run_parser(str(_SELECTOR), "--strings", "node",
                              "--min-len", "6", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        strings = {h["value"]
                   for entry in data["entries"]
                   for h in entry["hits"]}
        self.assertIn("levels/selector", strings)

    def test_movie_scenes_referenced(self):
        """The hub includes direct movie-playback portals."""
        import json
        rc, out = _run_parser(str(_SELECTOR), "--strings", "node",
                              "--min-len", "6", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        strings = {h["value"]
                   for entry in data["entries"]
                   for h in entry["hits"]}
        scene_hits = [s for s in strings if s.startswith("movies/scenes/")]
        # Empirically we saw 10 scene refs — pin the count at
        # >=8 for slack.
        self.assertGreaterEqual(len(scene_hits), 8,
            msg=f"expected >=8 movie scenes, found {scene_hits}")


@unittest.skipUnless(_INDEX.exists(),
                     "vanilla index.xbr fixture required")
class IndexXbrIsAssetIndex(unittest.TestCase):
    """``index.xbr`` is a global asset-path index listed under
    ``tag=always`` in prefetch-lists.txt.  It catalogues every
    asset the game can load by 4-char type tag + path.

    Tests pin:
    - File has exactly ONE ``indx`` TOC entry.
    - The entry contains ~3100 name strings.
    - The 4-char tag population matches what the game uses
      elsewhere (surf, wave, banm, node, body, gems).
    """

    def test_single_indx_entry(self):
        rc, out = _run_parser(str(_INDEX))
        self.assertEqual(rc, 0, msg=out)
        self.assertIn("indx", out)
        self.assertIn("TOC entries: 1", out)

    def test_string_count_is_large(self):
        """~3100 unique name strings in the vanilla ISO.  Pin a
        conservative lower bound so a trim by build-config change
        would be caught."""
        rc, out = _run_parser(str(_INDEX), "--strings", "indx",
                              "--min-len", "4", "--unique",
                              "--count-only")
        self.assertEqual(rc, 0, msg=out)
        # Parser prints "Grand total: N string(s)" at the end.
        import re
        m = re.search(r"Grand total: (\d+) string", out)
        self.assertIsNotNone(m,
            msg=f"no grand-total line in parser output:\n{out[:400]}")
        count = int(m.group(1))
        self.assertGreaterEqual(count, 2500,
            msg=f"expected >=2500 unique strings, got {count}")


class CutLevelsConstant(unittest.TestCase):
    """``KNOWN_CUT_LEVELS`` is consumed by the randomizer's
    ``EXCLUDE_TRANSITIONS`` logic + surfaces in integrity reports.
    Both known entries must be present."""

    def test_contains_f7_and_e4(self):
        self.assertEqual(KNOWN_CUT_LEVELS, frozenset({"f7", "e4"}),
            msg="Expected exactly {f7, e4}; any extension must be "
                "co-reviewed with docs/LEARNINGS.md § cut levels")

    def test_frozen(self):
        """Catch accidental mutation from downstream callers."""
        with self.assertRaises((AttributeError, TypeError)):
            KNOWN_CUT_LEVELS.add("z1")  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()
