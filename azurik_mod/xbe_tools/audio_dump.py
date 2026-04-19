"""Audio asset dump — extract ``wave`` blobs from ``fx.xbr``.

Tool #14 on the roadmap.  Bulk-extracts every ``wave`` TOC entry
from a fx-style XBR file, writes each as a standalone ``.bin``,
and emits a manifest mapping each blob to entropy + size
statistics so downstream RE work (eventual decoder) starts from
something greppable.

## Format status

**Partially decoded.**  During the April 2026 pass we confirmed:

- ``fx.xbr`` contains 700 ``wave`` TOC entries.
- Azurik's XBE references audio by symbolic NAME (e.g.
  ``fx/sound/player/jump``) — looked up via ``index.xbr``.
- **No standard audio magic** (RIFF / OggS / xWMA / XMA2 / etc.)
  appears anywhere in fx.xbr — the wave payload is either
  raw DSOUND samples (PCM / ADPCM) OR a proprietary container
  Azurik layered on top.
- Some wave entries clearly aren't audio at all (they carry
  embedded 4-byte tags like ``gshd`` / ``ndbg`` / ``node`` /
  ``rdms`` — animation-curve metadata, not PCM).
- High-entropy wave entries (ratio > 0.9 unique-byte / total)
  look like compressed audio.  Low-entropy entries look like
  structured animation data.

Full decoding is deferred until someone pins the exact header /
codec layout.  In the meantime this tool gets the blobs out of
fx.xbr so the RE work can proceed on plain files.

## CLI

    azurik-mod audio dump FX_XBR --output DIR [--entropy-min 0.5]

Produces:

    DIR/
      manifest.json              — one-line summary per blob
      waves/wave_0000.bin        — raw payload from TOC entry [0]
      waves/wave_0001.bin
      ...

## What's in manifest.json

Each entry looks like::

    {
      "index": 127,
      "file_offset": 15466496,
      "size": 17584,
      "entropy": 0.92,
      "first_bytes_hex": "05000000 6f000000 01000000 ...",
      "classification": "likely-audio",
      "output": "waves/wave_0127.bin"
    }

``classification`` is a heuristic label:

- ``likely-audio`` — entropy >= 0.5, no embedded 4-byte TOC tags
- ``likely-animation`` — entropy < 0.5 OR embedded TOC tags
- ``too-small`` — payload shorter than 64 bytes
"""

from __future__ import annotations

import json
import math
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path


__all__ = [
    "WaveEntry",
    "DumpReport",
    "classify_entry",
    "dump_waves",
    "entropy_ratio",
]


# 4-byte fourcc tags that appear in fx.xbr's animation-curve
# wave entries (NOT audio).  Presence in the first 64 bytes of
# a wave payload strongly suggests the blob is structured
# animation data, not PCM.
_ANIMATION_TAGS = (
    b"gshd", b"ndbg", b"node", b"rdms", b"sprv",
    b"pbrw", b"pbrc", b"wave", b"surf",
)


@dataclass(frozen=True)
class WaveEntry:
    """One wave-tag payload extracted from an fx.xbr."""

    index: int                  # 0-based index within the wave list
    file_offset: int            # byte offset in the source file
    size: int                   # payload size (from TOC)
    classification: str         # "likely-audio" | "likely-animation" | "too-small"
    entropy: float              # Shannon ratio (0.0..1.0)
    first_bytes_hex: str        # first 32 bytes hex
    output_rel: str             # destination path relative to output dir

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "file_offset": self.file_offset,
            "size": self.size,
            "classification": self.classification,
            "entropy": round(self.entropy, 4),
            "first_bytes_hex": self.first_bytes_hex,
            "output": self.output_rel,
        }


@dataclass
class DumpReport:
    """Summary of a ``dump_waves`` run."""

    source: str
    output_dir: str
    total_waves: int = 0
    written: int = 0
    likely_audio: int = 0
    likely_animation: int = 0
    too_small: int = 0
    entries: list[WaveEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "output_dir": self.output_dir,
            "total_waves": self.total_waves,
            "written": self.written,
            "likely_audio": self.likely_audio,
            "likely_animation": self.likely_animation,
            "too_small": self.too_small,
            "entries": [e.to_dict() for e in self.entries],
        }


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------


def entropy_ratio(data: bytes) -> float:
    """Shannon-entropy normalised to 0..1 (1 = maximally random).

    Quick proxy for "is this compressed audio or structured data?".
    Uses an 8-bit histogram + the standard H = -Σ p·log2(p)
    formula, divided by 8 so the result sits in ``[0, 1]``.
    """
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = len(data)
    h = 0.0
    for c in counts:
        if c:
            p = c / n
            h -= p * math.log2(p)
    return h / 8.0


def classify_entry(size: int, head: bytes) -> str:
    """Label a wave entry as ``likely-audio`` / ``likely-animation``
    / ``too-small``.

    Heuristic:
    - < 64 bytes → ``too-small`` (unlikely to be useful audio).
    - Any of :data:`_ANIMATION_TAGS` appears in the first 64
      bytes → ``likely-animation`` (structured metadata, not PCM).
    - Entropy >= 0.5 on first 256 bytes → ``likely-audio``.
    - Otherwise → ``likely-animation``.
    """
    if size < 64:
        return "too-small"
    for tag in _ANIMATION_TAGS:
        if tag in head[:64]:
            return "likely-animation"
    if entropy_ratio(head[:256]) >= 0.5:
        return "likely-audio"
    return "likely-animation"


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------


def dump_waves(fx_xbr: str | Path, output_dir: str | Path, *,
               entropy_min: float = 0.0,
               only_audio: bool = False) -> DumpReport:
    """Extract every ``wave`` TOC entry from ``fx_xbr``.

    Parameters
    ----------
    fx_xbr
        Path to a fx-style XBR (usually ``gamedata/fx.xbr``).
    output_dir
        Destination directory (created if missing).  A ``waves/``
        subdirectory holds one file per extracted blob; a
        ``manifest.json`` at the top of ``output_dir`` indexes
        them.
    entropy_min
        Minimum entropy to skip writing a blob (0.0 = write all).
        Handy for "give me only the high-entropy / likely-audio
        entries".
    only_audio
        When ``True`` skips every entry classified as
        ``likely-animation`` / ``too-small``.

    Returns a :class:`DumpReport`.
    """
    src = Path(fx_xbr).expanduser().resolve()
    data = src.read_bytes()
    # Re-use the shipping XBR parser.
    sys.path.insert(0, str(
        Path(__file__).resolve().parents[2] / "scripts"))
    import xbr_parser as xp  # type: ignore

    toc = xp.parse_toc(data)
    waves = [e for e in toc if e.tag == "wave"]

    out = Path(output_dir).expanduser().resolve()
    waves_dir = out / "waves"
    waves_dir.mkdir(parents=True, exist_ok=True)

    report = DumpReport(source=str(src), output_dir=str(out),
                        total_waves=len(waves))

    width = max(4, len(str(len(waves) - 1)) if waves else 4)

    for i, e in enumerate(waves):
        payload = data[e.file_offset:e.file_offset + e.size]
        head = payload[:64]
        classification = classify_entry(e.size, head)
        ratio = entropy_ratio(payload[:256]) if payload else 0.0
        output_rel = f"waves/wave_{i:0{width}d}.bin"

        if classification == "too-small":
            report.too_small += 1
        elif classification == "likely-audio":
            report.likely_audio += 1
        else:
            report.likely_animation += 1

        should_write = True
        if entropy_min > 0.0 and ratio < entropy_min:
            should_write = False
        if only_audio and classification != "likely-audio":
            should_write = False

        if should_write:
            (out / output_rel).write_bytes(payload)
            report.written += 1

        report.entries.append(WaveEntry(
            index=i,
            file_offset=e.file_offset,
            size=e.size,
            classification=classification,
            entropy=ratio,
            first_bytes_hex=head[:32].hex(),
            output_rel=output_rel if should_write else "",
        ))

    # Manifest always written regardless of --only-audio filter.
    (out / "manifest.json").write_text(
        json.dumps(report.to_dict(), indent=2),
        encoding="utf-8")

    return report


def format_report(report: DumpReport, *, preview: int = 0) -> str:
    """Human-readable summary.  ``preview`` shows the first N
    entries inline."""
    lines = [
        f"Audio dump from {report.source}",
        f"  → {report.output_dir}",
        f"",
        f"  total wave entries:      {report.total_waves}",
        f"  written to disk:         {report.written}",
        f"  classification:",
        f"     likely-audio:         {report.likely_audio}",
        f"     likely-animation:     {report.likely_animation}",
        f"     too-small:            {report.too_small}",
    ]
    if preview > 0 and report.entries:
        lines.append("")
        lines.append(f"  Preview (first {preview}):")
        for e in report.entries[:preview]:
            lines.append(
                f"    [{e.index:4d}] {e.classification:<18s} "
                f"size={e.size:>6} B  entropy={e.entropy:.2f}  "
                f"→ {e.output_rel or '(skipped)'}")
    return "\n".join(lines)
