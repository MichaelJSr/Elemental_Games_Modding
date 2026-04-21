"""Drift guard for ``docs/xbr_graph_snapshot.json``.

Rebuilds the snapshot in memory and compares against the committed
file.  Any change to
:meth:`azurik_mod.xbr.sections.Section.iter_refs` — the pointer
graph's source of truth — surfaces here instead of silently
corrupting downstream structural edits.

If this test fails AND you meant to change the graph, run::

    python scripts/xbr_graph_snapshot.py

and commit the refreshed JSON.

Skips when the vanilla gamedata/ fixture isn't mounted.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.xbr_graph_snapshot import (  # noqa: E402
    _find_gamedata,
    build_snapshot,
)


_SNAPSHOT_PATH = _REPO_ROOT / "docs" / "xbr_graph_snapshot.json"


@unittest.skipUnless(_find_gamedata() is not None,
                     "vanilla gamedata/ fixture not available")
class XbrGraphSnapshotDrift(unittest.TestCase):
    def test_snapshot_is_current(self):
        self.assertTrue(
            _SNAPSHOT_PATH.exists(),
            msg=f"{_SNAPSHOT_PATH} is missing.  Run "
                f"`python scripts/xbr_graph_snapshot.py`.")
        committed = json.loads(_SNAPSHOT_PATH.read_text())
        gamedata = _find_gamedata()
        fresh = build_snapshot(gamedata)
        # The committed file strips ``gamedata_root`` (machine-
        # specific); compare everything else.
        fresh_portable = {k: v for k, v in fresh.items()
                          if k != "gamedata_root"}
        committed_portable = {k: v for k, v in committed.items()
                              if k != "gamedata_root"}
        if fresh_portable != committed_portable:
            # Surface the first-file-that-drifts for a useful diff.
            drift_files = []
            for name in fresh_portable.get("files", {}):
                if (fresh_portable["files"].get(name)
                        != committed_portable.get("files", {}).get(name)):
                    drift_files.append(name)
            self.fail(
                f"xbr_graph_snapshot.json drift in "
                f"{len(drift_files)} file(s): "
                f"{drift_files[:3]}{'...' if len(drift_files) > 3 else ''}. "
                f"Run `python scripts/xbr_graph_snapshot.py` and "
                f"commit the result.")


if __name__ == "__main__":
    unittest.main()
