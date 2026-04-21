"""Smoke tests for the Phase-1 and Phase-5 XBR CLI verbs.

Exercises ``azurik-mod xbr xref`` / ``xbr verify`` / ``xbr edit
--set-value`` end-to-end against a temp-copied vanilla config.xbr
by invoking ``python -m azurik_mod`` as a subprocess.  Focus is
wiring: ``argparse`` + dispatch + exit codes + structural-edit
round-trip through the CLI boundary.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


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


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "azurik_mod", *args],
        capture_output=True, text=True, cwd=_REPO_ROOT,
        timeout=60,
    )


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class XbrXrefCli(unittest.TestCase):
    def test_xref_summary_prints_ref_counts(self):
        r = _run_cli("xbr", "xref",
                     str(_GAMEDATA / "config.xbr"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Total refs:", r.stdout)
        self.assertIn("tabl:", r.stdout)

    def test_xref_json(self):
        r = _run_cli("xbr", "xref",
                     str(_GAMEDATA / "config.xbr"),
                     "--format", "json")
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        self.assertIn("refs", payload)
        self.assertIn("ref_counts_by_tag", payload)
        self.assertGreater(payload["ref_counts_by_tag"]["tabl"],
                           5000)

    def test_xref_on_level_surfaces_unmodeled_tags(self):
        r = _run_cli("xbr", "xref",
                     str(_GAMEDATA / "a1.xbr"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Unmodeled sections", r.stdout)


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class XbrVerifyCli(unittest.TestCase):
    def test_verify_clean_config_xbr(self):
        r = _run_cli("xbr", "verify",
                     str(_GAMEDATA / "config.xbr"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK", r.stdout)

    def test_verify_clean_level_xbr(self):
        r = _run_cli("xbr", "verify",
                     str(_GAMEDATA / "a1.xbr"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK", r.stdout)


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class XbrEditCli(unittest.TestCase):
    def test_set_value_round_trips_through_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "edited.xbr"
            r = _run_cli(
                "xbr", "edit",
                str(_GAMEDATA / "config.xbr"), str(out),
                "--set-value",
                "attacks_transitions/garret4/walkSpeed=42.0",
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("set_value", r.stdout)
            # Verify the edited file.
            r2 = _run_cli("xbr", "verify", str(out))
            self.assertEqual(r2.returncode, 0, r2.stderr)
            # Re-read via the XbrDocument API and confirm the edit.
            from azurik_mod.xbr import XbrDocument
            doc = XbrDocument.load(out)
            self.assertEqual(
                doc.keyed_sections()["attacks_transitions"]
                   .find_cell("garret4", "walkSpeed").double_value,
                42.0)

    def test_set_keyed_string_applies(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "edited.xbr"
            r = _run_cli(
                "xbr", "edit",
                str(_GAMEDATA / "config.xbr"), str(out),
                "--set-keyed-string",
                "critters_critter_data/garret4/name=abc",
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            from azurik_mod.xbr import XbrDocument
            doc = XbrDocument.load(out)
            self.assertIsNotNone(
                doc.keyed_sections()["critters_critter_data"]
                   .find_cell("abc", "name"))

    def test_add_row_surfaces_blocker(self):
        """Blocked-on-RE ops must exit non-zero with a clear
        message, not silently no-op."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "edited.xbr"
            r = _run_cli(
                "xbr", "edit",
                str(_GAMEDATA / "config.xbr"), str(out),
                "--add-row", "some_row",
            )
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("not shippable yet", r.stderr)

    def test_blocker_checked_before_legal_edits_run(self):
        """If the user mixes a legal --set-value with a blocked
        --add-row, the whole invocation must fail without writing
        the output file.  Guards against the earlier ordering
        where --set-value partially mutated + --add-row exited
        after the write."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "edited.xbr"
            r = _run_cli(
                "xbr", "edit",
                str(_GAMEDATA / "config.xbr"), str(out),
                "--set-value",
                "attacks_transitions/garret4/walkSpeed=77.0",
                "--add-row", "some_row",
            )
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse(
                out.exists(),
                msg="Blocker flag didn't stop the write — output "
                    "file was created anyway, potentially with "
                    "partial edits applied.")

    def test_bad_set_value_syntax_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "edited.xbr"
            r = _run_cli(
                "xbr", "edit",
                str(_GAMEDATA / "config.xbr"), str(out),
                "--set-value", "this_isnt_valid",
            )
            self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
