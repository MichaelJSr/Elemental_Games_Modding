"""Level preview — #24 from ``docs/TOOLING_ROADMAP.md``.

Extracts **high-signal, mod-relevant** information from a level
XBR so authors can understand a level without cracking it open
in Ghidra or the byte-level ``xbr_parser``.

## What the preview surfaces

1. **File + TOC structure** — size, TOC entry count, one-line
   roll-up of every tag (count, total bytes, largest entry).
2. **Level connections** — strings of the form ``levels/<elem>/<name>``
   pulled from the ``node`` section; these are the portal graph
   / loading-screen targets other levels transition to.
3. **Character + FX references** — ``characters/<name>/<sub>/<file>``
   paths (cutscenes, weapon FX, NPC models) the level uses.
4. **Localisation keys** — ``loc/<lang>/<path>`` resource keys,
   useful for spotting what popups / speech a level triggers.
5. **Identifier-style strings** — e.g. ``water_elemental``,
   ``seal_air``.  Surfaced as a last-resort bucket for things
   the structured categories miss.
6. **(Optional) raw strings** — everything else that passed the
   quality filter, behind ``include_raw=True`` for drill-down.

## Design: why structured categories instead of raw strings?

The first version of this tool emitted *"50 sample position
triples (e.g. (0,0,0))"* and *"strings: 'tdBg~T^', 'dBb!',
'P|v&[`"* — i.e. **noise** the user had to mentally filter.
Replacing that with structured regex buckets changes the
artefact from "here's a bag of bytes" to "here's what this
level actually references" — something a human mod author can
act on without further processing.

The position scanner was removed entirely.  It was producing
thousands of (0,0,0) / (0,0,1) / (0,0,tiny) triples with no
way to tell signal from noise without heavy context we don't
have yet (vertex count headers, mesh section types, etc.).  If
a real spatial preview lands later it'll come from a
structured parser, not a byte-level scan.

## Performance

We only scan TOC tags known to contain strings
(:data:`_STRING_BEARING_TAGS`).  On vanilla ``a1.xbr`` this is
~25% of total bytes; on ``town.xbr`` ~20%.  Skipping ``rdms``
/ ``surf`` / ``tern`` / ``wave`` / ``sdsr`` saves the parser
from scanning ~40 MiB of mesh/sound/terrain payload per level
and makes the preview run in tens of ms rather than seconds.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "LevelPreview",
    "TagStats",
    "format_preview",
    "preview_level",
]


# Tags known (empirically, from Azurik's vanilla levels) to carry
# ASCII asset references / localisation keys / identifiers.  All
# other tags are binary-only (mesh vertices, terrain heightmaps,
# sound blobs, material curves, etc.) and are skipped during
# string extraction both for performance and for noise reduction.
_STRING_BEARING_TAGS: frozenset[str] = frozenset({
    "node",  # scene graph — asset paths + level connections
    "levl",  # level metadata (partial; some noise)
})


# Every other TOC tag we've observed in Azurik level XBRs — kept
# here so tag-stats reporting can flag unexpected tags as
# "unknown" (likely a format change or a mod that added new
# content).
_KNOWN_BINARY_TAGS: frozenset[str] = frozenset({
    "surf", "rdms", "pbrc", "tern", "pbrw", "wave",
    "sdsr", "ndbg", "sprv", "gshd",
})


_HEADER_SIZE = 0x40


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TagStats:
    """Per-tag roll-up — purely structural (no payload scanning)."""

    tag: str
    count: int
    total_bytes: int
    largest_bytes: int
    classification: str   # "string_bearing" | "binary" | "unknown"

    def to_json_dict(self) -> dict:
        return {
            "tag": self.tag, "count": self.count,
            "total_bytes": self.total_bytes,
            "largest_bytes": self.largest_bytes,
            "classification": self.classification,
        }


@dataclass(frozen=True)
class LevelPreview:
    """High-signal summary of a level XBR."""

    path: str
    file_size: int
    toc_entries: int
    tag_stats: tuple[TagStats, ...]
    level_connections: tuple[str, ...]
    asset_references: tuple[str, ...]
    localisation_keys: tuple[str, ...]
    cutscene_refs: tuple[str, ...]
    identifiers: tuple[str, ...]
    raw_strings: tuple[str, ...]    # empty unless include_raw=True

    def to_json_dict(self) -> dict:
        return {
            "path": self.path,
            "file_size": self.file_size,
            "toc_entries": self.toc_entries,
            "tag_stats": [s.to_json_dict() for s in self.tag_stats],
            "level_connections": list(self.level_connections),
            "asset_references": list(self.asset_references),
            "localisation_keys": list(self.localisation_keys),
            "cutscene_refs": list(self.cutscene_refs),
            "identifiers": list(self.identifiers),
            "raw_strings": list(self.raw_strings),
        }


# ---------------------------------------------------------------------------
# Parsers
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
        size, tag_raw, flags, file_offset = struct.unpack_from(
            "<I4sII", data, off)
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
# String extraction + classification
# ---------------------------------------------------------------------------

# Candidate strings — printable ASCII with length ≥ 4.  We cast a
# wider net than the classifier allows because the classifier
# requires specific shape; strings that don't match any pattern
# are still surfaced via ``raw_strings`` (when requested).
_CAND_RE = re.compile(rb"[\x20-\x7E]{4,}")

# Matches ``levels/<element>/<name>`` — the portal/adjacency info
# every Azurik level embeds in its ``node`` tag.  Elements are
# always lowercase (air/earth/fire/water/death/life plus the two
# non-element levels town/training_room/selector).  We accept any
# lowercase element name to stay robust against future content.
_LEVEL_CONN_RE = re.compile(r"\blevels/[a-z_]+/[a-z0-9_]+")

# Matches ``characters/<subdir>/…/<leaf>`` — NPC / weapon / FX
# asset paths.
_ASSET_RE = re.compile(
    r"\b(?:characters|effects|items|fx|shaders|sounds)/"
    r"[a-zA-Z0-9_/]+")

# Matches ``loc/<language>/<path>`` — localisation resource keys.
_LOC_RE = re.compile(r"\bloc/[a-z]+/[a-z0-9_/]+")

# Matches ``bink:<name>.bik`` — cutscene movie references.  Every
# level we've inspected uses this exact format for triggering
# cutscenes; capturing them separately makes the preview a useful
# "which movies does this level play?" lookup table.
_CUTSCENE_RE = re.compile(r"\bbink:[A-Za-z0-9_]+\.bik")

# Matches ``<word>_<word>`` identifier style (``seal_air``,
# ``water_elemental``, ``gem_count_max``).  Must contain at least
# one ``_`` AND one alpha char on each side to avoid matching
# random "AA_BB" junk.
_IDENT_RE = re.compile(
    r"\b[a-z][a-z0-9]{2,}(?:_[a-z][a-z0-9]*){1,4}\b")

# Minimum fraction of alphanumeric characters for a "raw string"
# to qualify as meaningful.  Empirical: 0.6 kills ~95% of
# noise in vanilla level XBRs while retaining every human-
# readable string we've seen.
_ALPHANUM_THRESHOLD = 0.6


@dataclass
class _ScanBuckets:
    """Scratch buckets populated by :func:`_scan_strings`.

    Using a mutable container keeps the per-blob loop tight and
    avoids the 5-tuple-returning contortion the initial draft
    used.  The final :class:`LevelPreview` freezes each bucket
    into a tuple before returning.
    """

    levels: set[str]
    assets: set[str]
    locs: set[str]
    cutscenes: set[str]
    idents: set[str]
    raw: set[str]


def _scan_strings(blob: bytes, buckets: _ScanBuckets) -> None:
    """Scan one blob and bucket every hit by category (in place).

    The structured regexes run inside a single pass over
    printable-ASCII candidates.  A string that matches any
    structured bucket is NOT added to ``raw`` — this keeps the
    raw bucket focused on things we haven't categorised yet.
    """
    for m in _CAND_RE.finditer(blob):
        s = m.group(0).decode("ascii", errors="replace")
        categorised = False
        for hit in _LEVEL_CONN_RE.findall(s):
            buckets.levels.add(hit)
            categorised = True
        for hit in _ASSET_RE.findall(s):
            buckets.assets.add(hit)
            categorised = True
        for hit in _LOC_RE.findall(s):
            buckets.locs.add(hit)
            categorised = True
        for hit in _CUTSCENE_RE.findall(s):
            buckets.cutscenes.add(hit)
            categorised = True
        for hit in _IDENT_RE.findall(s):
            buckets.idents.add(hit)
            categorised = True
        if categorised:
            continue
        if _looks_meaningful(s):
            buckets.raw.add(s)


_RUN_RE = re.compile(r"(.)\1{3,}")


def _looks_meaningful(s: str) -> bool:
    """Heuristic quality check for the raw-strings bucket.

    Rejects strings that are mostly punctuation, all-one-char,
    or have no word-shape at all.  Intentionally conservative
    — a false negative here just means the string isn't shown
    in the raw-strings bucket; it doesn't affect structured
    categories (which have already matched by the time this
    runs).
    """
    if len(s) < 6:
        return False
    alnum = sum(1 for c in s if c.isalnum())
    if alnum / len(s) < _ALPHANUM_THRESHOLD:
        return False
    letters = sum(1 for c in s if c.isalpha())
    if letters < 3:
        return False
    if _RUN_RE.search(s):
        return False
    return True


# ---------------------------------------------------------------------------
# Top-level API
# ---------------------------------------------------------------------------


def preview_level(path: Path | str, *,
                  include_raw: bool = False,
                  max_raw_strings: int = 50,
                  ) -> LevelPreview:
    """Build a structured preview of ``path``.

    ``include_raw`` surfaces every string that passed the quality
    filter but didn't match one of the structured categories.
    Off by default because it can be noisy on large levels; turn
    it on when hunting for undocumented string conventions.
    """
    p = Path(path)
    data = p.read_bytes()
    toc = _parse_toc(data)

    # Per-tag structural roll-up first — always cheap.
    by_tag: dict[str, list[_TocEntry]] = {}
    for entry in toc:
        by_tag.setdefault(entry.tag, []).append(entry)
    tag_stats: list[TagStats] = []
    for tag in sorted(by_tag):
        rows = by_tag[tag]
        classification = (
            "string_bearing" if tag in _STRING_BEARING_TAGS
            else "binary" if tag in _KNOWN_BINARY_TAGS
            else "unknown")
        tag_stats.append(TagStats(
            tag=tag,
            count=len(rows),
            total_bytes=sum(r.size for r in rows),
            largest_bytes=max(r.size for r in rows),
            classification=classification,
        ))

    # String-bearing scan — only the tags we know carry strings.
    buckets = _ScanBuckets(
        levels=set(), assets=set(), locs=set(),
        cutscenes=set(), idents=set(), raw=set())
    for tag in _STRING_BEARING_TAGS:
        for entry in by_tag.get(tag, []):
            end = min(entry.file_offset + entry.size, len(data))
            _scan_strings(data[entry.file_offset:end], buckets)

    # Cap the raw-strings bucket so the preview stays readable
    # when ``include_raw`` is on.  Sort longer-first since long
    # strings tend to be the most informative.
    raw_list: list[str] = []
    if include_raw:
        raw_list = sorted(
            buckets.raw, key=lambda s: (-len(s), s))[:max_raw_strings]

    return LevelPreview(
        path=str(p),
        file_size=len(data),
        toc_entries=len(toc),
        tag_stats=tuple(tag_stats),
        level_connections=tuple(sorted(buckets.levels)),
        asset_references=tuple(sorted(buckets.assets)),
        localisation_keys=tuple(sorted(buckets.locs)),
        cutscene_refs=tuple(sorted(buckets.cutscenes)),
        identifiers=tuple(sorted(buckets.idents)),
        raw_strings=tuple(raw_list),
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def format_preview(preview: LevelPreview, *,
                   max_items_per_category: int = 30,
                   ) -> str:
    """Pretty-print a :class:`LevelPreview`.

    Output is stable (alphabetised within each section) so
    downstream snapshot tests can pin it byte-for-byte.
    """
    out: list[str] = []
    out.append(f"level: {preview.path}")
    out.append(
        f"  size:        {_fmt_bytes(preview.file_size)}  "
        f"toc_entries: {preview.toc_entries}")
    out.append("")

    if preview.tag_stats:
        out.append("  TOC roll-up (count · total · largest):")
        for s in preview.tag_stats:
            flag = ""
            if s.classification == "unknown":
                flag = "  [UNKNOWN TAG]"
            elif s.classification == "string_bearing":
                flag = "  [strings]"
            out.append(
                f"    {s.tag:6}  {s.count:5}  "
                f"{_fmt_bytes(s.total_bytes):>10}  "
                f"{_fmt_bytes(s.largest_bytes):>10}{flag}")
        out.append("")

    out.append("  Level connections ({}):".format(
        len(preview.level_connections)))
    _emit(out, preview.level_connections,
          max_items_per_category, prefix="    ")
    out.append("")
    out.append("  Localisation keys ({}):".format(
        len(preview.localisation_keys)))
    _emit(out, preview.localisation_keys,
          max_items_per_category, prefix="    ")
    out.append("")
    out.append("  Cutscenes ({}):".format(
        len(preview.cutscene_refs)))
    _emit(out, preview.cutscene_refs,
          max_items_per_category, prefix="    ")
    out.append("")
    out.append("  Asset references ({}):".format(
        len(preview.asset_references)))
    _emit(out, preview.asset_references,
          max_items_per_category, prefix="    ")
    out.append("")
    out.append("  Identifiers ({}):".format(
        len(preview.identifiers)))
    _emit(out, preview.identifiers,
          max_items_per_category, prefix="    ")

    if preview.raw_strings:
        out.append("")
        out.append("  Raw strings ({}; --include-raw):".format(
            len(preview.raw_strings)))
        _emit(out, preview.raw_strings,
              max_items_per_category, prefix="    ")

    return "\n".join(out)


def _emit(out: list[str], items: tuple[str, ...],
          limit: int, *, prefix: str) -> None:
    if not items:
        out.append(f"{prefix}(none)")
        return
    for item in items[:limit]:
        out.append(f"{prefix}{item}")
    if len(items) > limit:
        out.append(
            f"{prefix}... (+{len(items) - limit} more)")


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KiB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MiB"
    return f"{n / (1024 * 1024 * 1024):.1f} GiB"
