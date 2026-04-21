#!/usr/bin/env python3
"""Regenerate ``docs/xbr_graph_snapshot.json``.

The snapshot pins the pointer-graph summary for every vanilla
gamedata XBR so subtle reversal drift (e.g. accidentally losing a
ref class when refactoring :mod:`azurik_mod.xbr.sections`) fails
:mod:`tests.test_xbr_graph_snapshot` loudly.

Usage::

    python scripts/xbr_graph_snapshot.py
    python scripts/xbr_graph_snapshot.py --check   # fail if drift

``--check`` is what the test harness runs; it never mutates the
file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.xbr import PointerGraph, XbrDocument  # noqa: E402
from azurik_mod.xbr.sections import RawSection  # noqa: E402


_GAMEDATA_CANDIDATES = [
    Path("/Users/michaelsrouji/Documents/Xemu/tools/"
         "Azurik - Rise of Perathia (USA).xiso/gamedata"),
    _REPO_ROOT.parent / "Azurik - Rise of Perathia (USA).xiso" / "gamedata",
]

_SNAPSHOT_PATH = _REPO_ROOT / "docs" / "xbr_graph_snapshot.json"


def _find_gamedata() -> Path | None:
    for p in _GAMEDATA_CANDIDATES:
        if p.exists():
            return p
    return None


def build_snapshot(gamedata: Path) -> dict:
    """Produce the deterministic snapshot dict.

    Keys are relative paths (POSIX-style) so the file is portable
    across hosts.
    """
    xbrs = sorted(gamedata.rglob("*.xbr"))
    files: dict[str, dict] = {}
    for p in xbrs:
        rel = str(p.relative_to(gamedata)).replace("\\", "/")
        doc = XbrDocument.load(p)
        graph = PointerGraph(doc)

        # Refs per tag — stable across ISO revisions as long as
        # we don't change what we reverse.
        by_tag: dict[str, int] = {}
        for rr in graph:
            by_tag[rr.ref.owner_tag or "?"] = (
                by_tag.get(rr.ref.owner_tag or "?", 0) + 1)

        unmodeled_tags: dict[str, int] = {}
        for i in range(len(doc.toc)):
            sec = doc.section_for(i)
            if isinstance(sec, RawSection):
                t = doc.toc[i].tag
                unmodeled_tags[t] = unmodeled_tags.get(t, 0) + 1

        files[rel] = {
            "size_bytes": len(doc.raw),
            "toc_entries": len(doc.toc),
            "total_refs": len(graph),
            "refs_by_tag": dict(sorted(by_tag.items())),
            "unmodeled_tags": dict(sorted(unmodeled_tags.items())),
        }

    return {
        "schema_version": 1,
        "description": (
            "Pointer-graph summary snapshot for every vanilla "
            "Azurik XBR.  Regenerate with "
            "scripts/xbr_graph_snapshot.py after intentional "
            "parser changes."
        ),
        "gamedata_root": str(gamedata),
        "file_count": len(files),
        "files": dict(sorted(files.items())),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="Exit non-zero on drift; don't write the file.")
    ap.add_argument("--output", type=Path, default=_SNAPSHOT_PATH,
                    help=f"Output path (default: {_SNAPSHOT_PATH})")
    args = ap.parse_args()

    gamedata = _find_gamedata()
    if gamedata is None:
        print("ERROR: vanilla gamedata/ fixture not found; "
              "cannot build the snapshot.", file=sys.stderr)
        return 2

    snap = build_snapshot(gamedata)
    # Strip the gamedata_root path (machine-specific).
    portable = {k: v for k, v in snap.items() if k != "gamedata_root"}
    payload = json.dumps(portable, indent=2, sort_keys=False) + "\n"

    if args.check:
        existing = (args.output.read_text()
                    if args.output.exists() else "")
        if existing != payload:
            print(f"ERROR: {args.output} is out of date.  Run "
                  f"`python scripts/xbr_graph_snapshot.py` and "
                  f"commit the result.", file=sys.stderr)
            # Show a diff-friendly "first differing field" pointer.
            print("--- existing top-level summary ---")
            try:
                old = json.loads(existing) if existing else {}
                new = json.loads(payload)
                for k in sorted(set(old) | set(new)):
                    if old.get(k) != new.get(k):
                        print(f"  {k} drift")
            except json.JSONDecodeError:
                pass
            return 1
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload)
    print(f"wrote {args.output} ({len(payload):,} B, "
          f"{snap['file_count']} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
