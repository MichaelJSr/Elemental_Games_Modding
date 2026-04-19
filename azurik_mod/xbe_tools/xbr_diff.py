"""Structural diff between two XBR files.

Tool #12 on the roadmap.  Compares the TOC of two XBR files +
the string tables inside each tag, so a modded level's diff
against vanilla surfaces as:

- Added / removed TOC entries (which asset categories changed)
- Changed byte sizes per tag (the quick "something moved" signal)
- Added / removed strings (the meaningful "this level now
  references a new texture / portal / event" signal)

Operates at the structural level — does NOT try to interpret the
record contents (see :mod:`xbr_inspect` for that).  A full
byte-for-byte diff would be useful too but would drown in
coordinate / transform numerical noise; structural + string-level
is the sweet spot for "what changed gameplay-wise?".

## Output

:class:`XbrDiff` aggregates three categories:

- ``toc_changes`` — tag additions / removals / size deltas
- ``string_changes`` — added / removed ASCII strings per tag
- ``size_deltas`` — per-tag total-byte deltas (a quick summary)

Plus the total file-size delta for the "is this a 1%/10%/100%
change?" at-a-glance signal.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path


# We reuse the xbr_parser that already lives in scripts/.  Import
# path is the same trick test_xbr_parser.py uses.
_SCRIPTS_DIR = (Path(__file__).resolve().parents[2] / "scripts")
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import xbr_parser as _xp  # noqa: E402


@dataclass(frozen=True)
class TOCChange:
    """One added/removed/size-changed TOC row."""

    kind: str              # "added" | "removed" | "size_changed"
    tag: str
    entry_index: int | None  # the row index in whichever file has it
    size_before: int | None = None
    size_after: int | None = None


@dataclass(frozen=True)
class StringChange:
    """One added/removed ASCII string (scoped by tag)."""

    kind: str           # "added" | "removed"
    tag: str
    text: str
    file_offset: int


@dataclass
class XbrDiff:
    """Full diff between two XBR files."""

    path_a: str
    path_b: str
    size_a: int = 0
    size_b: int = 0
    toc_changes: list[TOCChange] = field(default_factory=list)
    string_changes: list[StringChange] = field(default_factory=list)
    size_deltas: dict[str, int] = field(default_factory=dict)

    @property
    def total_size_delta(self) -> int:
        return self.size_b - self.size_a

    @property
    def has_changes(self) -> bool:
        return (bool(self.toc_changes)
                or bool(self.string_changes)
                or bool(self.size_deltas))

    def to_json_dict(self) -> dict:
        return {
            "path_a": self.path_a,
            "path_b": self.path_b,
            "size_a": self.size_a,
            "size_b": self.size_b,
            "total_size_delta": self.total_size_delta,
            "toc_changes": [
                {"kind": c.kind, "tag": c.tag,
                 "entry_index": c.entry_index,
                 "size_before": c.size_before,
                 "size_after": c.size_after}
                for c in self.toc_changes],
            "string_changes": [
                {"kind": c.kind, "tag": c.tag, "text": c.text,
                 "file_offset": c.file_offset}
                for c in self.string_changes],
            "size_deltas": dict(self.size_deltas),
        }


def _total_bytes_per_tag(toc: list) -> dict[str, int]:
    """Sum of every entry's declared ``size`` bucketed by tag."""
    out: dict[str, int] = {}
    for e in toc:
        out[e.tag] = out.get(e.tag, 0) + e.size
    return out


def _strings_per_tag(data: bytes, toc: list, *,
                     min_len: int = 6) -> dict[str, set[str]]:
    """Collect every ASCII string found inside each tag's byte
    range.  Uses the same scanner the parser exposes for
    ``--strings`` so results match what authors see there."""
    out: dict[str, set[str]] = {}
    for e in toc:
        hits = _xp.find_strings_in_region(
            data, e.file_offset, e.size, min_len=min_len)
        if not hits:
            continue
        bucket = out.setdefault(e.tag, set())
        for _off, s in hits:
            bucket.add(s)
    return out


def diff_xbr(path_a: str | Path, path_b: str | Path, *,
             min_len: int = 6) -> XbrDiff:
    """Compare two XBR files.  Raises :exc:`FileNotFoundError`
    when either input is missing."""
    pa = Path(path_a).expanduser().resolve()
    pb = Path(path_b).expanduser().resolve()
    data_a = pa.read_bytes()
    data_b = pb.read_bytes()

    toc_a = _xp.parse_toc(data_a)
    toc_b = _xp.parse_toc(data_b)

    diff = XbrDiff(path_a=str(pa), path_b=str(pb),
                   size_a=len(data_a), size_b=len(data_b))

    # ---- TOC deltas ------------------------------------------------
    tags_a = _total_bytes_per_tag(toc_a)
    tags_b = _total_bytes_per_tag(toc_b)
    all_tags = set(tags_a) | set(tags_b)
    for tag in sorted(all_tags):
        before = tags_a.get(tag, 0)
        after = tags_b.get(tag, 0)
        delta = after - before
        if delta == 0:
            continue
        diff.size_deltas[tag] = delta
        if before == 0:
            diff.toc_changes.append(TOCChange(
                kind="added", tag=tag, entry_index=None,
                size_before=None, size_after=after))
        elif after == 0:
            diff.toc_changes.append(TOCChange(
                kind="removed", tag=tag, entry_index=None,
                size_before=before, size_after=None))
        else:
            diff.toc_changes.append(TOCChange(
                kind="size_changed", tag=tag, entry_index=None,
                size_before=before, size_after=after))

    # ---- String deltas per tag ------------------------------------
    strings_a = _strings_per_tag(data_a, toc_a, min_len=min_len)
    strings_b = _strings_per_tag(data_b, toc_b, min_len=min_len)
    all_string_tags = set(strings_a) | set(strings_b)
    for tag in sorted(all_string_tags):
        set_a = strings_a.get(tag, set())
        set_b = strings_b.get(tag, set())
        for added in sorted(set_b - set_a):
            # We don't know the exact offset (sets lose positions)
            # — emit offset=-1 as a sentinel.  Consumers that need
            # offsets should re-run the scanner on the B-side file.
            diff.string_changes.append(StringChange(
                kind="added", tag=tag, text=added, file_offset=-1))
        for removed in sorted(set_a - set_b):
            diff.string_changes.append(StringChange(
                kind="removed", tag=tag, text=removed,
                file_offset=-1))

    return diff


def format_diff(diff: XbrDiff, *, max_strings: int = 40) -> str:
    """Human-readable diff summary."""
    lines = [
        f"XBR diff:",
        f"  A: {diff.path_a}  ({diff.size_a:,} B)",
        f"  B: {diff.path_b}  ({diff.size_b:,} B)",
        f"  Size delta: {diff.total_size_delta:+,} B",
    ]
    if not diff.has_changes:
        lines.append("")
        lines.append("(no structural changes detected)")
        return "\n".join(lines)

    if diff.toc_changes:
        lines.append("")
        lines.append("TOC changes:")
        for c in diff.toc_changes:
            if c.kind == "added":
                lines.append(f"  + tag {c.tag!r}  new ({c.size_after:,} B)")
            elif c.kind == "removed":
                lines.append(f"  - tag {c.tag!r}  was {c.size_before:,} B")
            else:
                delta = (c.size_after or 0) - (c.size_before or 0)
                lines.append(
                    f"  ~ tag {c.tag!r}  {c.size_before:,} → "
                    f"{c.size_after:,} B  ({delta:+,})")

    if diff.string_changes:
        lines.append("")
        lines.append(f"String changes ({len(diff.string_changes)} total):")
        grouped: dict[str, list[StringChange]] = {}
        for sc in diff.string_changes:
            grouped.setdefault(sc.tag, []).append(sc)
        for tag, rows in grouped.items():
            lines.append(f"  [{tag}]")
            for sc in rows[:max_strings]:
                sign = "+" if sc.kind == "added" else "-"
                lines.append(f"    {sign} {sc.text!r}")
            if len(rows) > max_strings:
                lines.append(
                    f"    ... and {len(rows) - max_strings} more")
    return "\n".join(lines)


__all__ = [
    "StringChange",
    "TOCChange",
    "XbrDiff",
    "diff_xbr",
    "format_diff",
]
