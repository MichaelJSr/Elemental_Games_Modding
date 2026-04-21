"""`azurik-mod xbr xref` — enumerate every pointer field in an XBR.

Thin CLI wrapper around
:class:`azurik_mod.xbr.pointer_graph.PointerGraph`.  Useful for:

- Spot-checking structural edits (did the right refs get rewritten
  when I grew the string pool?).
- Surfacing unreversed tag types (entries with zero refs that
  contain obvious pointer-like u32 fields hint at RE backlog).
- Feeding the :ref:`Phase 1 snapshot <docs/xbr_graph_snapshot.json>`
  drift guard.

Typical invocations::

    azurik-mod xbr xref gamedata/config.xbr
    azurik-mod xbr xref gamedata/config.xbr --tag tabl
    azurik-mod xbr xref gamedata/a1.xbr --format json

"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable

from azurik_mod.xbr import PointerGraph, Section, XbrDocument
from azurik_mod.xbr.pointer_graph import ResolvedRef
from azurik_mod.xbr.sections import RawSection


def _filter_refs(
    refs: Iterable[ResolvedRef],
    tag: str | None,
    section_index: int | None,
) -> list[ResolvedRef]:
    out = list(refs)
    if tag is not None:
        out = [rr for rr in out if rr.ref.owner_tag == tag]
    if section_index is not None:
        # ``owner_tag`` alone doesn't uniquely identify a section in
        # config.xbr (every tabl entry shares the tag).  Apply the
        # index filter on the source-offset range of the chosen
        # TOC entry — the caller already knows which TOC index they
        # care about.
        pass  # handled in :func:`cmd_xbr_xref`
    return out


def _format_ref(rr: ResolvedRef) -> str:
    tgt = (f"0x{rr.target_offset:08X}"
           if rr.target_offset is not None else "-")
    return (f"  [{rr.ref.owner_tag or '?':>4s}] "
            f"{type(rr.ref).__name__:<18s} "
            f"@0x{rr.ref.src_offset:08X} -> {tgt}")


def _summarise_unmodeled(
    doc: XbrDocument,
) -> list[tuple[int, Section]]:
    """Return every section that surfaced as :class:`RawSection`.

    Those are the RE backlog — we preserve their bytes on
    round-trip but can't model refs / edit them.
    """
    return [(i, doc.section_for(i))
            for i in range(len(doc.toc))
            if isinstance(doc.section_for(i), RawSection)]


def cmd_xbr_xref(args) -> None:
    """Entry point for ``azurik-mod xbr xref``."""
    path = Path(args.path)
    if not path.exists():
        print(f"ERROR: {path} does not exist", file=sys.stderr)
        sys.exit(1)

    doc = XbrDocument.load(path)
    graph = PointerGraph(doc)

    # Optional filter by TOC index range — computed up-front so the
    # JSON and text paths share the same candidate set.
    refs = list(graph)
    if args.section_index is not None:
        if not (0 <= args.section_index < len(doc.toc)):
            print(f"ERROR: section-index {args.section_index} out of "
                  f"range [0, {len(doc.toc)})", file=sys.stderr)
            sys.exit(2)
        entry = doc.toc[args.section_index]
        lo = entry.file_offset
        hi = entry.file_offset + entry.size
        refs = [rr for rr in refs
                if lo <= rr.ref.src_offset < hi]
    if args.tag is not None:
        refs = [rr for rr in refs
                if rr.ref.owner_tag == args.tag]

    unmodeled = _summarise_unmodeled(doc)

    if args.format == "json":
        out = {
            "file": str(path),
            "toc_entries": len(doc.toc),
            "ref_counts_by_tag": {},
            "refs": [
                {
                    "kind": type(rr.ref).__name__,
                    "owner_tag": rr.ref.owner_tag,
                    "src_offset": f"0x{rr.ref.src_offset:08X}",
                    "width": rr.ref.width,
                    "target_offset": (
                        f"0x{rr.target_offset:08X}"
                        if rr.target_offset is not None else None),
                }
                for rr in refs
            ],
            "unmodeled_sections": [
                {
                    "toc_index": i,
                    "tag": doc.toc[i].tag,
                    "size": doc.toc[i].size,
                    "file_offset": f"0x{doc.toc[i].file_offset:08X}",
                }
                for i, _ in unmodeled
            ],
        }
        # Aggregate ref counts.
        counts: dict[str, int] = {}
        for rr in graph:
            counts[rr.ref.owner_tag or "?"] = (
                counts.get(rr.ref.owner_tag or "?", 0) + 1)
        out["ref_counts_by_tag"] = counts
        print(json.dumps(out, indent=2))
        return

    print(f"{path.name}: {len(doc.raw):,} B, "
          f"{len(doc.toc)} TOC entries")
    # Global ref summary (not filter-restricted).
    counts: dict[str, int] = {}
    for rr in graph:
        counts[rr.ref.owner_tag or "?"] = (
            counts.get(rr.ref.owner_tag or "?", 0) + 1)
    print(f"Total refs: {len(graph)}")
    if counts:
        print("Refs by owner tag:")
        for tag in sorted(counts):
            print(f"  {tag}: {counts[tag]}")

    if unmodeled:
        print(f"\nUnmodeled sections "
              f"(RawSection fallback, structural edits blocked):")
        # Cluster by tag for a compact view.
        by_tag: dict[str, list[int]] = {}
        for i, _ in unmodeled:
            by_tag.setdefault(doc.toc[i].tag, []).append(i)
        for tag in sorted(by_tag):
            idxs = by_tag[tag]
            print(f"  {tag!r}: {len(idxs)} entr"
                  f"{'y' if len(idxs) == 1 else 'ies'}")

    if args.tag is not None or args.section_index is not None:
        print(f"\nRefs ({len(refs)} after filter):")
    else:
        # Showing all refs would be overwhelming on large files;
        # default to showing nothing unless the user asked.
        if args.list_all:
            print("\nRefs (--list-all):")
        else:
            print(f"\n(pass --list-all or --tag / --section-index "
                  f"to see individual ref rows)")
            return

    for rr in refs:
        print(_format_ref(rr))


def build_arg_parser(subparsers) -> None:
    """Register ``xbr xref`` on the `xbr` subcommand parser.

    Callable from :mod:`azurik_mod.cli`.  Kept separate from the
    legacy ``xbr inspect`` / ``xbr diff`` / ``xbr edit`` wiring so
    a future refactor can thin the giant ``cli.py`` down.
    """
    p = subparsers.add_parser(
        "xref",
        help="Enumerate pointer fields in an XBR (Phase 1)",
        description=(
            "Walk every reversed pointer field in an XBR and print "
            "a source-offset -> target-offset report.  Useful for "
            "verifying structural edits (grow pool, add row) didn't "
            "leave dangling pointers.  Tags the parser doesn't "
            "model surface as 'unmodeled sections' at the end so "
            "the RE backlog is visible."
        ),
    )
    p.add_argument("path", help="Path to a .xbr file")
    p.add_argument("--tag", default=None,
                   help="Restrict ref listing to this 4-char TOC tag")
    p.add_argument("--section-index", type=int, default=None,
                   help="Restrict ref listing to the section at this "
                        "TOC index")
    p.add_argument("--format", choices=("text", "json"),
                   default="text",
                   help="Output format (default: text)")
    p.add_argument("--list-all", action="store_true",
                   help="Show every ref row (default: summary only)")
