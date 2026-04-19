"""Bink movie metadata dumper.

Tool #13 on the roadmap.  Decodes the first ~64 bytes of every
``.bik`` file under ``movies/`` and reports the fields we care
about for cutscene-balancing work: resolution, frame count,
frame rate, duration, audio track count.

## Scope

Implements Bink 1 / 1.9 header parsing ("BIKi" magic).  Azurik
ships 14 ``.bik`` files, all BIKi variant — verified empirically.
Doesn't attempt to decode frames or audio; the purpose is
*metadata*, not playback.

## Format we decode (from the Bink 1 spec + observation)

Offset  Size  Field
------  ----  --------------------------------------------------
0       4     Magic (must be ``BIKi``)
4       4     File size minus 8 (``total - 8``)
8       4     Frame count
12      4     Largest-frame byte size
16      4     Frame count (duplicate — the game reads this one)
20      4     Video width
24      4     Video height
28      4     Frame rate numerator
32      4     Frame rate denominator
36      4     Video flags bitfield
40      4     Audio track count (N)
44..    ...   Per-track audio metadata (skipped — no decoder)

Fields beyond offset 44 are audio-track-specific and get only
lightly touched (track count is reported, per-track sample
rates are not).

## CLI

    azurik-mod movies info PATH.bik
    azurik-mod movies info PATH_TO_DIR     (aggregated report)

The ``movies`` subcommand is new; dispatched by
``azurik_mod/cli.py``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


__all__ = [
    "BinkInfo",
    "format_info",
    "format_info_table",
    "inspect_bink_file",
    "inspect_directory",
]


_MAGIC = b"BIKi"


@dataclass(frozen=True)
class BinkInfo:
    """Parsed metadata for one ``.bik`` file."""

    path: str
    file_size: int
    declared_size: int         # header's (total-8); 0 when missing
    frame_count: int
    max_frame_bytes: int
    width: int
    height: int
    frame_rate_num: int
    frame_rate_den: int
    video_flags: int
    audio_track_count: int

    @property
    def fps(self) -> float:
        """Frame rate in Hz.  Returns 0.0 when denominator is 0
        (malformed header)."""
        if self.frame_rate_den == 0:
            return 0.0
        return self.frame_rate_num / self.frame_rate_den

    @property
    def duration_seconds(self) -> float:
        """Playback duration assuming exact-FPS decode."""
        fps = self.fps
        if fps <= 0 or self.frame_count <= 0:
            return 0.0
        return self.frame_count / fps

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"

    def to_json_dict(self) -> dict:
        return {
            "path": self.path,
            "file_size": self.file_size,
            "declared_size": self.declared_size,
            "frame_count": self.frame_count,
            "max_frame_bytes": self.max_frame_bytes,
            "width": self.width,
            "height": self.height,
            "frame_rate_num": self.frame_rate_num,
            "frame_rate_den": self.frame_rate_den,
            "fps": round(self.fps, 3),
            "duration_seconds": round(self.duration_seconds, 3),
            "resolution": self.resolution,
            "video_flags": self.video_flags,
            "audio_track_count": self.audio_track_count,
        }


def inspect_bink_file(path: str | Path) -> BinkInfo:
    """Parse a ``.bik`` header from disk.

    Raises
    ------
    FileNotFoundError
        ``path`` doesn't exist.
    ValueError
        File is too small or the magic isn't ``BIKi``.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(p)
    with p.open("rb") as fh:
        head = fh.read(44)
    if len(head) < 44:
        raise ValueError(
            f"{p.name}: file too small ({len(head)} B); need at "
            f"least 44 B for a Bink header")
    if head[:4] != _MAGIC:
        raise ValueError(
            f"{p.name}: bad magic {head[:4]!r}; expected {_MAGIC!r} "
            f"(Bink 1 / 1.9)")
    (_magic,
     declared_size,
     frame_count_a,
     max_frame,
     frame_count_b,
     width, height,
     rate_num, rate_den,
     flags,
     audio_tracks) = struct.unpack("<4s10I", head)
    # Game engine code typically reads frame_count from the 2nd slot
    # (+0x10); fall back to the first slot when they disagree.
    frame_count = frame_count_b or frame_count_a
    return BinkInfo(
        path=str(p),
        file_size=p.stat().st_size,
        declared_size=declared_size,
        frame_count=frame_count,
        max_frame_bytes=max_frame,
        width=width, height=height,
        frame_rate_num=rate_num, frame_rate_den=rate_den,
        video_flags=flags,
        audio_track_count=audio_tracks,
    )


def inspect_directory(path: str | Path) -> list[BinkInfo]:
    """Parse every ``*.bik`` directly in ``path`` (non-recursive)."""
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        raise NotADirectoryError(p)
    out: list[BinkInfo] = []
    for f in sorted(p.glob("*.bik")):
        try:
            out.append(inspect_bink_file(f))
        except (OSError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------


def format_info(info: BinkInfo) -> str:
    """One ``.bik`` rendered as a compact block."""
    name = Path(info.path).name
    return "\n".join([
        f"{name}",
        f"  file size     : {info.file_size:,} B",
        f"  resolution    : {info.resolution}",
        f"  frame count   : {info.frame_count:,}",
        f"  frame rate    : {info.frame_rate_num}/"
        f"{info.frame_rate_den}  ({info.fps:.3f} Hz)",
        f"  duration      : {info.duration_seconds:.2f} s",
        f"  audio tracks  : {info.audio_track_count}",
        f"  max frame     : {info.max_frame_bytes:,} B",
        f"  flags         : 0x{info.video_flags:08X}",
    ])


def format_info_table(infos: list[BinkInfo]) -> str:
    """Multiple ``.bik`` files rendered as an aligned table."""
    if not infos:
        return "(no Bink files found)"
    lines = [
        f"{'file':<24s}  {'size (MB)':>10s}  {'wxh':>9s}  "
        f"{'frames':>6s}  {'fps':>5s}  {'dur':>7s}  {'tracks':>6s}",
        f"{'----':<24s}  {'---------':>10s}  {'---':>9s}  "
        f"{'------':>6s}  {'---':>5s}  {'---':>7s}  {'------':>6s}",
    ]
    for info in infos:
        name = Path(info.path).name
        size_mb = info.file_size / (1024 * 1024)
        lines.append(
            f"{name:<24s}  {size_mb:>10.1f}  "
            f"{info.resolution:>9s}  "
            f"{info.frame_count:>6,d}  {info.fps:>5.2f}  "
            f"{info.duration_seconds:>6.1f}s  "
            f"{info.audio_track_count:>6d}")
    total_size = sum(i.file_size for i in infos)
    total_dur = sum(i.duration_seconds for i in infos)
    lines.append("")
    lines.append(f"Total: {len(infos)} files, "
                 f"{total_size / (1024*1024):.1f} MB, "
                 f"{total_dur:.1f} s playback")
    return "\n".join(lines)
