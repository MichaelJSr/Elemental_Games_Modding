"""Bink frame extractor — #26 from ``docs/TOOLING_ROADMAP.md``.

## Current status: metadata + plan only

The Bink 1.9 codec is proprietary and has no open-source
decoder we can redistribute safely.  Extracting actual decoded
frames requires one of:

- **RAD Game Tools' ``bink2raw``** (closed-source, royalty-free
  for tooling) — produces AVI/RGB frames but has a per-seat
  license term.
- **FFmpeg's ``bink`` decoder** — open-source, covers Bink 1 /
  Bink 2; the easiest vendor for mod tooling today.
- **Roll our own** — infeasible.  Bink's wavelet + mcomp
  pipeline is a multi-month RE project on its own.

This module therefore ships in two modes:

1. **Metadata mode** (always available): wraps
   :mod:`.bink_info` and emits the per-frame offset table we
   already have access to, plus a sidecar JSON suitable for
   downstream tooling to consume.
2. **FFmpeg mode** (opt-in): shells out to ``ffmpeg`` if it's on
   ``$PATH`` and asks it to dump PNG frames for a ``.bik`` file
   into a caller-chosen directory.  This is the path most users
   will take — ``brew install ffmpeg`` or ``apt install
   ffmpeg`` and you're done.

If neither ``ffmpeg`` nor a pinned extractor is available, the
module returns a clear "action required" report so automation
can surface it.

## API

- :func:`describe_bink` — pure metadata (frame count, fps,
  resolution, audio tracks, per-frame offsets).  Never shells
  out.
- :func:`plan_frame_extraction` — return the command we'd run
  *if* the tool were available, plus a reason when it isn't.
- :func:`extract_frames_via_ffmpeg` — actually run ffmpeg
  (raises when missing).  Deliberately thin wrapper so tests
  can mock the subprocess.

## Format: per-frame offset table

Bink 1.9 stores a ``frame_count`` table of 32-bit offsets after
the fixed header.  The low bit of each offset is the *keyframe*
flag — masked off here because it would otherwise produce bogus
offsets.  Exposed as :class:`BinkFrameTable` for callers that
want to seek per-frame without a full decoder.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

from azurik_mod.xbe_tools.bink_info import (
    BinkInfo,
    inspect_bink_file,
)

__all__ = [
    "BinkFrameTable",
    "BinkFramePlan",
    "describe_bink",
    "extract_frames_via_ffmpeg",
    "plan_frame_extraction",
]


@dataclass(frozen=True)
class BinkFrameTable:
    """Decoded per-frame offset table."""

    info: BinkInfo
    offsets: tuple[int, ...]
    keyframes: tuple[int, ...]  # indices of keyframes

    def frame_size(self, idx: int) -> int:
        """Byte size of frame ``idx`` (distance to next
        offset, or to EOF for the last frame)."""
        if idx < 0 or idx >= len(self.offsets):
            raise IndexError(idx)
        start = self.offsets[idx]
        end = (self.offsets[idx + 1] if idx + 1 < len(self.offsets)
               else self.info.file_size)
        return max(end - start, 0)


@dataclass
class BinkFramePlan:
    """Output of :func:`plan_frame_extraction` — describes what
    the caller should run to get PNGs."""

    tool: str              # "ffmpeg" | "none"
    command: list[str]     # full argv (empty when ``tool == "none"``)
    reason: str            # why we picked this plan
    available: bool        # can the tool actually run right now?

    def to_json_dict(self) -> dict:
        return {
            "tool": self.tool,
            "command": list(self.command),
            "reason": self.reason,
            "available": self.available,
        }


# ---------------------------------------------------------------------------
# Metadata + per-frame offsets
# ---------------------------------------------------------------------------


_BINK_FIXED_HEADER = 0x2C       # BinkInfo reads exactly this many bytes
# Per ffmpeg/libavformat/bink.c each audio track consumes 16 bytes:
# 4 bytes (max packet size) + 8 bytes (track header: sample_rate /
# channels / flags) + 4 bytes (track id).  Earlier Bink revisions
# used 12 B/track — we auto-detect below so both layouts work.
_BINK_AUDIO_TRACK_SIZES = (16, 12, 8)


def describe_bink(path: Path) -> BinkFrameTable:
    """Full metadata + per-frame offset table for a Bink 1.x
    file.

    The Bink 1.9 "BIKi" container puts audio-track descriptors
    between the fixed header and the per-frame offset table.
    Per-track size varies between revisions (16 B for 1.9b as
    shipped with Azurik; 12 B and 8 B for earlier revisions seen
    in other games), so we auto-detect by probing candidate
    layouts and picking the first one that yields a strictly
    monotonic offset sequence where the first offset matches
    ``header + audio_bytes + 4*(frame_count+1)`` (there's one
    extra offset after the last frame for the end-of-stream
    sentinel).

    Raises :exc:`ValueError` on non-Bink files or when no
    candidate layout fits.
    """
    info = inspect_bink_file(path)
    data = Path(path).read_bytes()
    if data[:4] != b"BIKi":
        raise ValueError(
            f"{path} is not a Bink 1 file (magic={data[:4]!r})")

    if info.frame_count <= 0:
        # Malformed header — bail before we try to unpack garbage.
        raise ValueError(
            f"{path}: header reports {info.frame_count} frames")

    last_err: str | None = None
    for per_track in _BINK_AUDIO_TRACK_SIZES:
        table_start = (_BINK_FIXED_HEADER
                       + per_track * max(0, info.audio_track_count))
        # +1 for the Bink EOF sentinel offset (frame_count + 1 entries).
        table_end = table_start + 4 * (info.frame_count + 1)
        if table_end > len(data):
            last_err = (f"table with per_track={per_track} runs off "
                        f"EOF (end=0x{table_end:x}, "
                        f"size=0x{len(data):x})")
            continue
        raw = struct.unpack_from(
            f"<{info.frame_count + 1}I", data, table_start)
        offsets = tuple(v & ~1 for v in raw)
        # Layout check: offsets must be monotonic, first offset
        # must land exactly AT table_end (i.e., the first frame
        # follows the offset table immediately), and the
        # sentinel at the end must be ≤ file size.
        if (offsets[0] == table_end
                and all(offsets[i] <= offsets[i + 1]
                        for i in range(len(offsets) - 1))
                and offsets[-1] <= len(data)):
            keyframes = tuple(
                i for i, v in enumerate(raw[:-1]) if v & 1)
            return BinkFrameTable(
                info=info,
                offsets=offsets[:-1],  # drop the EOF sentinel
                keyframes=keyframes)
        last_err = (f"per_track={per_track}: first=0x{offsets[0]:x} "
                    f"expected=0x{table_end:x}")
    raise ValueError(
        f"{path}: couldn't auto-detect offset table layout; "
        f"last attempt: {last_err}")


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def plan_frame_extraction(path: Path,
                          out_dir: Path,
                          *,
                          pattern: str = "frame_%04d.png",
                          ) -> BinkFramePlan:
    """Pick the best available extraction tool.

    Doesn't execute anything — returns the plan so the caller
    can ``print`` it, run it, or refuse based on its own
    policy.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is not None:
        command = [
            ffmpeg, "-y",
            "-i", str(path),
            "-vf", "format=rgb24",
            str(out_dir / pattern),
        ]
        return BinkFramePlan(
            tool="ffmpeg",
            command=command,
            reason=("ffmpeg detected on PATH — covers Bink 1.x "
                    "with the 'bink' decoder"),
            available=True)
    return BinkFramePlan(
        tool="none",
        command=[],
        reason=("no suitable decoder found — install ffmpeg "
                "(brew install ffmpeg / apt install ffmpeg) "
                "or RAD Tools' bink2raw"),
        available=False)


def extract_frames_via_ffmpeg(path: Path,
                              out_dir: Path,
                              *,
                              pattern: str = "frame_%04d.png",
                              run: bool = True,
                              ) -> BinkFramePlan:
    """Build the plan and optionally run it.

    ``run=False`` returns the plan without invoking subprocess;
    ``run=True`` attempts the command and raises
    :exc:`RuntimeError` if ffmpeg isn't available (covers the
    plan-says-available-but-PATH-changed race).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = plan_frame_extraction(path, out_dir, pattern=pattern)
    if not run or plan.tool == "none":
        return plan
    try:
        subprocess.run(plan.command, check=True,
                       capture_output=True)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"ffmpeg not found when running plan: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"ffmpeg failed with exit {exc.returncode}: "
            f"{(exc.stderr or b'').decode(errors='replace')[:400]}"
        ) from exc
    return plan
