"""Level preview — #24 from ``docs/TOOLING_ROADMAP.md``.

Extracts a structured summary of a level XBR so mod authors can
see **what's in a level** without cracking it open in Ghidra:

- Strings inside each gameplay TOC entry (``node``, ``surf``,
  ``levl``, ``rdms``, etc.) grouped by tag.
- Likely position coordinates — triples of ``f32`` that look like
  plausible gameplay positions (magnitude < 10 000, finite).
- Per-tag size + entry count.

The goal is to land a *navigable text preview* we can render
anywhere (CI logs, CLI, future GUI tab).  Matplotlib-backed
scatter plots / 3D renders are a natural follow-up but are not
required to see most modding-relevant structure.

## Output shape

``preview_level`` returns a :class:`LevelPreview` dataclass.
Renders to either plain text (via :func:`format_preview`) or JSON
(``to_json_dict``).

Scanning is size-bounded: each entry contributes up to
``max_strings_per_tag`` strings and ``max_positions_per_tag``
positions so a huge level doesn't produce a 10 MB preview.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "LevelPreview",
    "TagSummary",
    "format_preview",
    "preview_level",
]


_GAMEPLAY_TAGS = (
    "node", "surf", "levl", "rdms", "wave", "indx",
    "mesh", "coll", "anim", "spwn", "trig", "lgts",
)


@dataclass(frozen=True)
class TagSummary:
    """Per-tag roll-up for one TOC tag within a level."""

    tag: str
    entry_count: int
    total_bytes: int
    sample_strings: tuple[str, ...]
    sample_positions: tuple[tuple[float, float, float], ...]

    def to_json_dict(self) -> dict:
        return {
            "tag": self.tag,
            "entry_count": self.entry_count,
            "total_bytes": self.total_bytes,
            "sample_strings": list(self.sample_strings),
            "sample_positions": [list(p)
                                 for p in self.sample_positions],
        }


@dataclass(frozen=True)
class LevelPreview:
    """High-level summary of a level XBR."""

    path: str
    file_size: int
    toc_entries: int
    summaries: tuple[TagSummary, ...]

    def to_json_dict(self) -> dict:
        return {
            "path": self.path,
            "file_size": self.file_size,
            "toc_entries": self.toc_entries,
            "summaries": [s.to_json_dict() for s in self.summaries],
        }


_STRING_RE = re.compile(rb"[\x20-\x7E]{4,}")
_HEADER_SIZE = 0x40


def preview_level(path: Path, *,
                  tags: tuple[str, ...] = _GAMEPLAY_TAGS,
                  max_strings_per_tag: int = 25,
                  max_positions_per_tag: int = 50,
                  ) -> LevelPreview:
    """Build a :class:`LevelPreview` for ``path``."""
    data = Path(path).read_bytes()
    toc = _parse_toc(data)
    tag_filter = set(tags) if tags else None

    grouped: dict[str, list[tuple[int, int]]] = {}
    for entry in toc:
        if tag_filter and entry.tag not in tag_filter:
            continue
        grouped.setdefault(entry.tag, []).append(
            (entry.file_offset, entry.size))

    summaries: list[TagSummary] = []
    for tag in sorted(grouped):
        ranges = grouped[tag]
        sample_strings: list[str] = []
        positions: list[tuple[float, float, float]] = []
        total = 0
        for start, size in ranges:
            end = min(start + size, len(data))
            blob = data[start:end]
            total += len(blob)
            if len(sample_strings) < max_strings_per_tag:
                sample_strings.extend(_extract_strings(
                    blob, remaining=(max_strings_per_tag
                                     - len(sample_strings))))
            if len(positions) < max_positions_per_tag:
                positions.extend(_extract_positions(
                    blob, remaining=(max_positions_per_tag
                                     - len(positions))))
        summaries.append(TagSummary(
            tag=tag,
            entry_count=len(ranges),
            total_bytes=total,
            sample_strings=tuple(
                dict.fromkeys(sample_strings)),  # dedup preserve-order
            sample_positions=tuple(positions),
        ))

    return LevelPreview(
        path=str(path),
        file_size=len(data),
        toc_entries=len(toc),
        summaries=tuple(summaries),
    )


def _extract_strings(blob: bytes, *,
                     remaining: int) -> list[str]:
    out: list[str] = []
    for m in _STRING_RE.finditer(blob):
        out.append(m.group(0).decode("ascii", errors="replace"))
        if len(out) >= remaining:
            break
    return out


def _extract_positions(blob: bytes, *, remaining: int,
                       ) -> list[tuple[float, float, float]]:
    """Scan for ``(f32, f32, f32)`` triples that look like
    gameplay positions.

    We aggressively filter: coordinates must be finite, in a
    magnitude range typical of level-scale positions (< 10 000),
    and not all-zero (zero triples are common padding).
    """
    positions: list[tuple[float, float, float]] = []
    if len(blob) < 12:
        return positions
    # Scan 4-byte aligned triples; this gives a reasonable balance
    # between recall (catches axis-aligned positions) and precision
    # (avoids the garbage you get at arbitrary byte offsets).
    step = 4
    for i in range(0, len(blob) - 12 + 1, step):
        try:
            x, y, z = struct.unpack_from("<fff", blob, i)
        except struct.error:
            continue
        if not (_is_sane(x) and _is_sane(y) and _is_sane(z)):
            continue
        if x == 0.0 and y == 0.0 and z == 0.0:
            continue
        positions.append((x, y, z))
        if len(positions) >= remaining:
            break
    return positions


def _is_sane(v: float) -> bool:
    import math
    if not math.isfinite(v):
        return False
    return abs(v) < 10_000.0


# ---------------------------------------------------------------------------
# TOC parsing (duplicated from xbr_edit to avoid scripts/ import)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _TocEntry:
    index: int
    size: int
    tag: str
    flags: int
    file_offset: int


def _parse_toc(data: bytes) -> list[_TocEntry]:
    entries: list[_TocEntry] = []
    off = _HEADER_SIZE
    while off + 16 <= len(data):
        size = struct.unpack_from("<I", data, off)[0]
        tag_raw = data[off + 4:off + 8]
        flags = struct.unpack_from("<I", data, off + 8)[0]
        file_offset = struct.unpack_from("<I", data, off + 12)[0]
        if size == 0 and flags == 0 and file_offset == 0:
            break
        try:
            tag = tag_raw.decode("ascii")
        except (UnicodeDecodeError, ValueError):
            tag = tag_raw.hex()
        entries.append(_TocEntry(
            index=len(entries),
            size=size, tag=tag,
            flags=flags, file_offset=file_offset))
        off += 16
    return entries


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def format_preview(preview: LevelPreview, *,
                   max_strings_shown: int = 8) -> str:
    lines: list[str] = []
    lines.append(f"level: {preview.path}")
    lines.append(
        f"  size: {preview.file_size:,} B  "
        f"toc_entries: {preview.toc_entries}")
    if not preview.summaries:
        lines.append("  (no gameplay tags found)")
        return "\n".join(lines)
    lines.append("")
    for summary in preview.summaries:
        lines.append(
            f"  [{summary.tag}]  entries={summary.entry_count}  "
            f"bytes={summary.total_bytes:,}")
        strings = summary.sample_strings[:max_strings_shown]
        if strings:
            lines.append(
                "    strings: " + ", ".join(
                    repr(s) for s in strings))
            if len(summary.sample_strings) > max_strings_shown:
                lines.append(
                    f"      (+{len(summary.sample_strings) - max_strings_shown}"
                    f" more)")
        if summary.sample_positions:
            lines.append(
                f"    sample_positions: "
                f"{len(summary.sample_positions)} candidate "
                f"(f32,f32,f32) triples")
            first = summary.sample_positions[0]
            lines.append(
                f"      e.g. ({first[0]:.2f}, "
                f"{first[1]:.2f}, {first[2]:.2f})")
    return "\n".join(lines)
