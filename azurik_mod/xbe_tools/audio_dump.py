"""Audio asset dump — extract + classify + wrap ``wave`` blobs from
Azurik's ``fx.xbr``.

Tool #14 on the roadmap.  Bulk-extracts every ``wave`` TOC entry
from a fx-style XBR file; classifies each blob by payload header;
for blobs whose 16-byte prefix matches Azurik's engine-accepted
audio header, wraps the payload in a RIFF/WAV so external tools
(vgmstream, Audacity, ffmpeg) can consume it directly; and emits
a manifest mapping every blob to its decoded metadata.

## Format — pinned in the April 2026 RE pass (final)

The authoritative parser is the game's own
``FUN_000AC400`` @ VA ``0x000AC400`` — called by the wave-init
vtable method ``FUN_000AC6F0`` (VA ``0x000AC6F0``) before the
payload is handed to ``IDirectSoundBuffer_SetBufferData``.
Ghidra MCP xrefs chain: ``load_asset_by_fourcc`` → caller
``FUN_000A20C0`` → factory ``FUN_000AE030`` → vtable slot +0x34
``FUN_000AC6F0`` → header parser ``FUN_000AC400``.

**16-byte audio header** (bytes 0x00..0x10 of each wave entry)::

    offset 0x00  u32  sample_rate       (22050 / 32000 / 44100 / …)
    offset 0x04  u32  sample_count      (duration = count / rate;
                                         unused by parser, kept for docs)
    offset 0x08  u8   channels          (1 or 2)
    offset 0x09  u8   bits_per_sample   (PCM: 8 or 16; XADPCM: 4)
    offset 0x0A  u8   (unused)
    offset 0x0B  u8   codec_id          (0 = PCM, 1 = Xbox ADPCM)
    offset 0x0C  u32  (unused — padding)
    offset 0x10  ...  codec payload fed to DirectSound

The engine REJECTS any entry whose ``codec_id`` is outside
``{0, 1}`` (``FUN_000AC400`` returns 0 → sound silently not
played).  We match that acceptance set exactly.

## Why the engine needs no custom decoder

Xbox DirectSound handles ``WAVE_FORMAT_XBOX_ADPCM`` (0x0069)
natively in hardware — ``FUN_000AC6F0`` just builds a
``WAVEFORMATEX`` from the 16-byte header + hands the raw payload
at ``entry + 16`` to ``IDirectSoundBuffer_SetBufferData``.  Our
RIFF/WAVE wrappers use the same format tag so ffmpeg /
vgmstream / Audacity decode identically.

## Distribution — vanilla ``fx.xbr`` (700 entries)

- 102  xbox-adpcm (codec_id=1, channels=1, bits=4) — the main SFX set
-   1  xbox-adpcm (codec_id=1, channels=2, bits=4) — sole stereo entry
-   0  pcm-raw (the game has the code path but no entries use it)
- 118  likely-animation (4-byte TOC tags in first 64 bytes —
        Maya particle-system curve data)
- 448  non-audio (high-entropy bytes but header fails parse —
        NOT decoded by the game either; leftover / effect data
        stored under the ``wave`` fourcc)
-  31  too-small (<64 byte payload)

An earlier draft classified the 448 "non-audio" entries as
``likely-audio`` on entropy alone; the April 2026 Ghidra walk
proved they never reach the wave-init pipeline, so there's
nothing to decode.  The ``--raw-previews`` flag still exists
for ad-hoc inspection of any non-audio blob's bytes as a raw
PCM WAV, but it's strictly a diagnostic aid, not an
audio-recovery path.

## Cross-referencing with ``index.xbr``

The game looks up every wave entry by symbolic name (``fx/sound/
<entity>/<key>``).  ``index.xbr`` stores 816 ``wave`` records +
3,000+ asset-path strings; calling ``dump_waves(..., index_xbr=...)``
emits those names alongside the raw blobs in ``manifest.json`` so
RE sessions don't have to open both files side-by-side.

## CLI

    azurik-mod audio dump FX_XBR --output DIR \\
        [--entropy-min 0.5] [--only-audio] \\
        [--index-xbr path/to/index.xbr]

Produces::

    DIR/
      manifest.json           — decoded header + classification per blob
      waves/wave_0000.bin     — raw payload for any entry (always written)
      waves/wave_0000.wav     — RIFF/WAV wrapper for recognised codecs

## What's in the manifest

::

    {
      "index": 127,
      "file_offset": 15466496,
      "size": 17584,
      "entropy": 0.92,
      "first_bytes_hex": "44ac0000 53c00000 01040001 ...",
      "classification": "xbox-adpcm",
      "header": {
        "sample_rate": 44100,
        "sample_count": 21440,
        "duration_ms": 486,
        "channels": 1,
        "bits_per_sample": 4,
        "codec_id": 1,
        "format_magic": "0x01000401"
      },
      "probable_name": "fx/sound/player/jump",
      "output": "waves/wave_0127.bin",
      "wav_output": "waves/wave_0127.wav"
    }

Classification values:

- ``xbox-adpcm``      — header parsed, codec_id=1; WAV wrapper
                         emitted with ``WAVE_FORMAT_XBOX_ADPCM`` (0x0069)
- ``pcm-raw``         — header parsed, codec_id=0; WAV wrapper
                         emitted with ``WAVE_FORMAT_PCM`` (0x0001)
- ``non-audio``       — header fails the engine's acceptance check
                         (codec_id ∉ {0, 1}).  NOT decodable as audio;
                         the game doesn't decode these either.
- ``likely-animation`` — Maya particle-system curve data (4-byte TOC
                         tags in first 64 bytes)
- ``too-small``       — < 64 bytes of payload

## Why many entries don't decode

The ``non-audio`` bucket (448 entries in vanilla ``fx.xbr``) used
to be labelled ``likely-audio`` and looked like a decoder gap.
The April 2026 Ghidra walk proved otherwise:

- ``FUN_000AC400`` (the engine's header parser) rejects anything
  with ``codec_id ∉ {0, 1}``.
- The wave-init vtable method ``FUN_000AC6F0`` silently aborts
  when the parser fails → ``FUN_000A20C0`` leaves the sound
  object NULL → no playback attempted.
- So the 448 blobs that don't parse are never consumed as audio
  BY THE GAME.  They're payloads stored under the ``wave`` fourcc
  for historical reasons (effect metadata, unused resources,
  development leftovers) — not a codec we need to reverse.

For reference the pipeline is:

::

    symbolic name (fx/sound/...)
        ↓ index.xbr
    load_asset_by_fourcc(0x65766177, 1)           → offset into fx.xbr
        ↓ FUN_000A20C0 (per-frame sound tick)
    FUN_000AE030(channel, offset, flags)          → sound-object alloc
        ↓ vtable[+0x34]
    FUN_000AC6F0(this, channel, wave_entry, flags)
        ↓
    FUN_000AC400(wave_entry)                      → WAVEFORMATEX
    DSOUND::DirectSoundCreateBuffer(desc, out)
    DSOUND::IDirectSoundBuffer_SetBufferData(buf, wave_entry+16, n)

Xbox DirectSound decodes ``WAVE_FORMAT_XBOX_ADPCM`` (0x0069) in
hardware, which is why there's no custom decoder to reverse.

If you want to poke at non-audio entries for RE purposes,
``--raw-previews`` emits ``*.preview.wav`` wrappers around their
raw bytes (16-bit mono PCM at the chosen sample rate) so you can
drop them into Audacity.  It's a generic "high-entropy bytes as
diagnostic WAV" helper — NOT an audio-recovery path.  Most users
can leave it off.
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
    "WaveHeader",
    "DumpReport",
    "classify_entry",
    "dump_waves",
    "entropy_ratio",
    "parse_wave_header",
    "build_raw_preview_wav",
]


# 4-byte fourcc tags that appear in fx.xbr's animation-curve
# wave entries (NOT audio).  Presence in the first 64 bytes of
# a wave payload strongly suggests the blob is structured
# animation data, not PCM.
_ANIMATION_TAGS = (
    b"gshd", b"ndbg", b"node", b"rdms", b"sprv",
    b"pbrw", b"pbrc", b"wave", b"surf",
)

# Standard audio sample rates we accept as "plausible enough to
# be a real sample_rate field" during header detection.  Every
# wave entry in vanilla fx.xbr that carried the 20-byte header
# used one of these; an unexpected value means the bytes at
# offset 0 aren't a sample rate.
_PLAUSIBLE_SAMPLE_RATES = frozenset({
    8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000,
})


# RIFF/WAVE format tag constants (for the WAV wrapper).
_WAVE_FORMAT_PCM = 0x0001
_WAVE_FORMAT_XBOX_ADPCM = 0x0069  # vgmstream + ffmpeg both accept this


@dataclass(frozen=True)
class WaveHeader:
    """The 20-byte audio header some ``fx.xbr`` wave entries carry.

    Only populated when :func:`parse_wave_header` successfully
    identifies the pattern; ``None`` otherwise (the blob is
    animation data or a codec we haven't decoded yet).
    """

    sample_rate: int
    sample_count: int
    channels: int
    bits_per_sample: int
    codec_id: int
    format_magic: int

    @property
    def duration_ms(self) -> int:
        """Clip length in milliseconds (ADPCM sample_count is decoded-
        sample count, which converts cleanly to ms)."""
        if self.sample_rate <= 0:
            return 0
        return int(self.sample_count * 1000 // self.sample_rate)

    def to_dict(self) -> dict:
        return {
            "sample_rate": self.sample_rate,
            "sample_count": self.sample_count,
            "duration_ms": self.duration_ms,
            "channels": self.channels,
            "bits_per_sample": self.bits_per_sample,
            "codec_id": self.codec_id,
            "format_magic": f"0x{self.format_magic:08x}",
        }


@dataclass(frozen=True)
class WaveEntry:
    """One wave-tag payload extracted from an fx.xbr."""

    index: int                  # 0-based index within the wave list
    file_offset: int            # byte offset in the source file
    size: int                   # payload size (from TOC)
    classification: str         # see module doc for the label set
    entropy: float              # Shannon ratio (0.0..1.0)
    first_bytes_hex: str        # first 32 bytes hex
    output_rel: str             # destination path relative to output dir
    header: WaveHeader | None = None
    wav_output_rel: str = ""    # "" if no WAV wrapper emitted
    probable_name: str = ""     # from index.xbr cross-reference
    duplicate_of: int = -1      # -1 or the lowest index sharing first 32B
    preview_output_rel: str = ""  # raw-PCM preview .wav (non-audio only)

    def to_dict(self) -> dict:
        d = {
            "index": self.index,
            "file_offset": self.file_offset,
            "size": self.size,
            "classification": self.classification,
            "entropy": round(self.entropy, 4),
            "first_bytes_hex": self.first_bytes_hex,
            "output": self.output_rel,
        }
        if self.header is not None:
            d["header"] = self.header.to_dict()
        if self.probable_name:
            d["probable_name"] = self.probable_name
        if self.wav_output_rel:
            d["wav_output"] = self.wav_output_rel
        if self.duplicate_of >= 0:
            d["duplicate_of"] = self.duplicate_of
        if self.preview_output_rel:
            d["preview_wav"] = self.preview_output_rel
        return d


@dataclass
class DumpReport:
    """Summary of a ``dump_waves`` run."""

    source: str
    output_dir: str
    total_waves: int = 0
    written: int = 0
    wav_written: int = 0
    preview_wav_written: int = 0
    duplicates_detected: int = 0
    xbox_adpcm: int = 0
    pcm_raw: int = 0
    non_audio: int = 0
    likely_animation: int = 0
    too_small: int = 0
    entries: list[WaveEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "output_dir": self.output_dir,
            "total_waves": self.total_waves,
            "written": self.written,
            "wav_written": self.wav_written,
            "preview_wav_written": self.preview_wav_written,
            "duplicates_detected": self.duplicates_detected,
            "classification_counts": {
                "xbox-adpcm":        self.xbox_adpcm,
                "pcm-raw":           self.pcm_raw,
                "non-audio":         self.non_audio,
                "likely-animation":  self.likely_animation,
                "too-small":         self.too_small,
            },
            "entries": [e.to_dict() for e in self.entries],
        }


# ---------------------------------------------------------------------------
# Heuristics + header parsing
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


def parse_wave_header(payload: bytes) -> WaveHeader | None:
    """Return the decoded 16-byte audio header, or ``None`` if the
    prefix doesn't match Azurik's wave layout.

    The layout is pinned from the Ghidra decomp of the game's actual
    header parser ``FUN_000AC400`` @ VA ``0x000AC400`` (the function
    that the wave-init vtable slot ``IDirectSoundBuffer_Init`` @
    ``0x000AC6F0`` calls before handing the payload to
    ``IDirectSoundBuffer_SetBufferData``):

    ::

        offset  0  u32  sample_rate       (22050 / 32000 / 44100 / …)
        offset  4  u32  sample_count      (unused by the parser;
                                           kept for duration display)
        offset  8  u8   channels          (1 or 2)
        offset  9  u8   bits_per_sample   (PCM: 8 or 16; XADPCM: 4)
        offset 10  u8   (unused)
        offset 11  u8   codec_id          (0 = PCM, 1 = Xbox ADPCM)
        offset 12  u32  (unused — padding)
        offset 16  ...  codec payload fed to DirectSound

    The game's parser REJECTS any entry with ``codec_id`` outside
    ``{0, 1}`` — so do we.  Earlier drafts of this function read
    bytes 8-11 as a little-endian ``format_magic`` ``u32`` and
    checked its integer value against a magic list; that
    accidentally excluded plain PCM (codec_id=0) and included a
    stray codec_id=2 that the game would refuse.  Aligned with the
    real engine logic now.
    """
    if len(payload) < 16:
        return None
    sample_rate, sample_count = struct.unpack_from("<II", payload, 0)
    if sample_rate not in _PLAUSIBLE_SAMPLE_RATES:
        return None
    channels = payload[8]
    bits_per_sample = payload[9]
    codec_id = payload[11]
    # The engine's acceptance set — FUN_000AC400 returns 0 otherwise.
    if codec_id not in (0, 1):
        return None
    if channels not in (1, 2):
        return None
    if bits_per_sample not in (4, 8, 16):
        return None
    # Shape guards — bits/codec must be self-consistent.  Anything
    # else would make DirectSound reject the buffer.
    if codec_id == 0 and bits_per_sample not in (8, 16):
        return None
    if codec_id == 1 and bits_per_sample != 4:
        return None
    # Sanity: sample_count must correspond to a non-insane duration.
    # Anything over 10 minutes of audio in a single fx.xbr blob is
    # almost certainly a bogus reinterpretation of noise bytes.
    if sample_count > sample_rate * 600:
        return None
    # Preserve the original "format_magic" interpretation (byte[11]
    # as high byte of the u32) so downstream consumers of the
    # manifest can still inspect it.  We keep it synthesised even
    # though it isn't how the engine actually reads the fields.
    format_magic = (
        channels
        | (bits_per_sample << 8)
        | (payload[10] << 16)
        | (codec_id << 24))
    return WaveHeader(
        sample_rate=sample_rate,
        sample_count=sample_count,
        channels=channels,
        bits_per_sample=bits_per_sample,
        codec_id=codec_id,
        format_magic=format_magic,
    )


def classify_entry(size: int, head: bytes,
                   header: WaveHeader | None = None) -> str:
    """Label a wave entry.

    Priority order: too-small → recognised codec → animation tag →
    non-audio fallback.

    **Labels**:

    - ``xbox-adpcm`` — header parsed with codec_id=1 (Xbox hardware
      ADPCM); ``.wav`` wrapper emitted using
      ``WAVE_FORMAT_XBOX_ADPCM``.
    - ``pcm-raw`` — header parsed with codec_id=0 (uncompressed PCM);
      ``.wav`` wrapper emitted using ``WAVE_FORMAT_PCM``.
    - ``likely-animation`` — structured Maya particle-system data
      (4-byte TOC tags in the first 64 bytes).  Not audio.
    - ``non-audio`` — header doesn't parse as audio AND no animation
      tags detected.  The engine's own header parser
      (``FUN_000AC400``) rejects anything with codec_id ∉ {0, 1}, so
      these blobs are NOT decoded by the game either.  They're
      payloads stored under the ``wave`` fourcc for historical
      reasons — most likely leftover effect / particle data that
      didn't get promoted to a dedicated fourcc.  Decoding them as
      audio is a dead end; the earlier ``likely-audio`` label was
      a false positive based solely on entropy and was renamed
      ``non-audio`` once the header-parser RE confirmed the engine
      never tries to decode them.
    - ``too-small`` — payload shorter than 64 bytes.
    """
    if size < 64:
        return "too-small"
    if header is not None:
        if header.bits_per_sample == 4:
            return "xbox-adpcm"
        return "pcm-raw"
    for tag in _ANIMATION_TAGS:
        if tag in head[:64]:
            return "likely-animation"
    return "non-audio"


# ---------------------------------------------------------------------------
# WAV wrapping — emit a RIFF container around the decoded payload so
# external tools can consume the audio without knowing about XBR.
# ---------------------------------------------------------------------------


def build_raw_preview_wav(
    payload: bytes,
    *,
    sample_rate: int = 22050,
    channels: int = 1,
    bits_per_sample: int = 16,
) -> bytes:
    """Wrap ``payload`` as a raw-PCM WAV using the given sample-rate
    guess.

    Use case: the 448 ``non-audio`` entries in Azurik's
    ``fx.xbr`` are high-entropy payloads stored under the ``wave``
    fourcc that the engine never decodes (see module docstring
    for the full RE trail — the header parser ``FUN_000AC400``
    rejects them, the wave-init vtable slot silently aborts).  A
    RAW-PCM WAV wrapper at a plausible sample rate lets an analyst
    drop them into Audacity to inspect byte structure visually:

    - Confirm they're not PCM masquerading as something else
    - Spot block / frame boundaries in otherwise-opaque binary
    - Identify duplicates by waveform shape

    The output is NOT intended audio; it's a diagnostic wrapper
    for any high-entropy binary blob.  ``bits_per_sample`` defaults
    to 16 because a payload aligned to 16-bit samples is the most
    common interpretation to probe; callers can override to 8.
    """
    block_align = channels * (bits_per_sample // 8)
    byte_rate = sample_rate * block_align
    fmt_chunk = struct.pack(
        "<HHIIHH",
        _WAVE_FORMAT_PCM,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
    )
    # Pad to an even byte count so the data chunk length matches
    # the sample granularity.
    if bits_per_sample == 16 and len(payload) % 2 == 1:
        payload = payload + b"\x00"
    data_chunk = b"data" + struct.pack("<I", len(payload)) + payload
    fmt_payload = b"fmt " + struct.pack("<I", len(fmt_chunk)) + fmt_chunk
    body = b"WAVE" + fmt_payload + data_chunk
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _build_wav_container(header: WaveHeader, payload: bytes) -> bytes:
    """Wrap ``payload`` in a RIFF/WAVE file using ``header``'s fields.

    For ADPCM we emit ``WAVE_FORMAT_XBOX_ADPCM`` (0x0069) with a
    typical 36-byte mono block (or 72-byte stereo block), matching
    what vgmstream / ffmpeg / Audacity all recognise.  For PCM we
    emit ``WAVE_FORMAT_PCM``.

    The header we can compute without knowing the exact codec:

    - For ADPCM at 4 bits/sample: block_align = 36 * channels,
      samples_per_block = (block_align - 7 * channels) * 2 + 2 = 64
      for mono, 128 for stereo.  These are the standard Microsoft
      ADPCM values that Xbox ADPCM inherited.
    - byte_rate = block_align * sample_rate / samples_per_block.

    When the codec is not decoded (4-bit but not IMA-style), the
    WAV file may still play through Audacity's raw-import path
    because of the correct sample_rate + channels metadata.
    """
    ch = header.channels
    sr = header.sample_rate
    bps = header.bits_per_sample

    if bps == 4:
        fmt_tag = _WAVE_FORMAT_XBOX_ADPCM
        block_align = 36 * ch
        samples_per_block = 64 if ch == 1 else 128
        byte_rate = int(block_align * sr / samples_per_block)
        # Xbox ADPCM fmt chunk: standard WAVEFORMATEX + cbSize=2 +
        # wSamplesPerBlock (u16) trailing extension.
        fmt_chunk = struct.pack(
            "<HHIIHHHH",
            fmt_tag,            # wFormatTag
            ch,                 # nChannels
            sr,                 # nSamplesPerSec
            byte_rate,          # nAvgBytesPerSec
            block_align,        # nBlockAlign
            bps,                # wBitsPerSample
            2,                  # cbSize (WAVEFORMATEX extension)
            samples_per_block,  # wSamplesPerBlock
        )
    else:
        fmt_tag = _WAVE_FORMAT_PCM
        block_align = ch * (bps // 8)
        byte_rate = sr * block_align
        fmt_chunk = struct.pack(
            "<HHIIHH",
            fmt_tag, ch, sr, byte_rate, block_align, bps)

    # Build the RIFF container.
    data_chunk = b"data" + struct.pack("<I", len(payload)) + payload
    fmt_payload = b"fmt " + struct.pack("<I", len(fmt_chunk)) + fmt_chunk
    body = b"WAVE" + fmt_payload + data_chunk
    return b"RIFF" + struct.pack("<I", len(body)) + body


# ---------------------------------------------------------------------------
# Index.xbr cross-reference — map wave entries to their symbolic names
# ---------------------------------------------------------------------------


def _collect_wave_names(index_xbr_path: str | Path) -> list[str]:
    """Return every plausible ``fx/sound/…`` or ``fx/…`` asset path
    from ``index.xbr``'s string pool, in pool order.

    Returns an empty list on any parse failure — the caller just
    skips naming.  Best-effort because we haven't fully pinned the
    record → pool mapping (see ``docs/LEARNINGS.md`` § index.xbr).
    """
    try:
        # Late import — ``azurik_mod.assets.index_xbr`` is a heavier
        # module than this file, keeping the import lazy so basic
        # ``dump_waves(..., index_xbr=None)`` calls don't pay for it.
        from azurik_mod.assets.index_xbr import load_index_xbr
        idx = load_index_xbr(index_xbr_path)
    except Exception:  # noqa: BLE001
        return []

    names: list[str] = []
    # ``iter_asset_paths`` returns every NUL-terminated printable
    # string in the pool.  We narrow to ``fx/…`` paths (sound fx
    # layer) + drop obvious duplicates while preserving order.
    seen: set[str] = set()
    for p in idx.iter_asset_paths():
        if not p.startswith("fx/"):
            continue
        # Further filter: drop file-extension refs (bar-wave01.tif)
        # + clearly-descriptive-but-not-asset strings.
        if "." in p.split("/")[-1] and not p.endswith((
                ".adpcm", ".wav", ".raw")):
            continue
        if p in seen:
            continue
        seen.add(p)
        names.append(p)
    return names


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------


def dump_waves(
    fx_xbr: str | Path,
    output_dir: str | Path,
    *,
    entropy_min: float = 0.0,
    only_audio: bool = False,
    emit_wav: bool = True,
    emit_raw_previews: bool = False,
    preview_sample_rate: int = 22050,
    index_xbr: str | Path | None = None,
) -> DumpReport:
    """Extract every ``wave`` TOC entry from ``fx_xbr``.

    Parameters
    ----------
    fx_xbr
        Path to a fx-style XBR (usually ``gamedata/fx.xbr``).
    output_dir
        Destination directory (created if missing).  A ``waves/``
        subdirectory holds one ``.bin`` per extracted blob (plus an
        optional ``.wav`` alongside when ``emit_wav=True`` and the
        header decodes cleanly); a ``manifest.json`` at the top of
        ``output_dir`` indexes them.
    entropy_min
        Minimum entropy to skip writing a blob (0.0 = write all).
    only_audio
        When ``True`` skips every entry classified as
        ``likely-animation`` / ``too-small``.
    emit_wav
        When ``True`` (default) emit a RIFF/WAVE wrapper alongside
        each ``xbox-adpcm`` / ``pcm-raw`` entry so external tools
        (vgmstream, Audacity, ffmpeg) can pick them up directly.
    emit_raw_previews
        When ``True``, emit a ``*.preview.wav`` alongside every
        ``non-audio`` entry — the raw payload wrapped as 16-bit
        mono PCM at ``preview_sample_rate``.  Output is NOT
        intended audio (the engine doesn't decode these bytes
        either) — it's a diagnostic aid for eyeballing binary
        structure in Audacity.  Most users leave this off.
    preview_sample_rate
        Sample rate to use for raw-preview wrappers (default
        22050 Hz — the most common Azurik rate, so plausible
        durations surface without further tuning).
    index_xbr
        Optional path to ``index.xbr`` — when provided, the
        manifest gets a best-effort ``probable_name`` field per
        entry pulled from the string pool.

    Returns a :class:`DumpReport`.
    """
    src = Path(fx_xbr).expanduser().resolve()
    data = src.read_bytes()
    sys.path.insert(0, str(
        Path(__file__).resolve().parents[2] / "scripts"))
    import xbr_parser as xp  # type: ignore

    toc = xp.parse_toc(data)
    waves = [e for e in toc if e.tag == "wave"]

    out = Path(output_dir).expanduser().resolve()
    waves_dir = out / "waves"
    waves_dir.mkdir(parents=True, exist_ok=True)

    # Optional asset names, in pool order.  We align them
    # 1-to-1 with the recognised-codec entries (the naming isn't
    # fully pinned; see _collect_wave_names docstring).
    pool_names: list[str] = (
        _collect_wave_names(index_xbr) if index_xbr else [])
    audio_name_iter = iter(pool_names)

    report = DumpReport(source=str(src), output_dir=str(out),
                        total_waves=len(waves))

    width = max(4, len(str(len(waves) - 1)) if waves else 4)

    # First pass — build a (first-32-bytes) -> earliest-index map so
    # the main loop can flag duplicates without re-scanning.  Many of
    # the 448 headerless ``non-audio`` entries share identical
    # prefixes across different TOC slots (same sound referenced by
    # multiple symbolic names); surfacing the redundancy cuts analysis
    # time for RE workflows.
    first_prefix_seen: dict[bytes, int] = {}

    for i, e in enumerate(waves):
        payload = data[e.file_offset:e.file_offset + e.size]
        head = payload[:64]
        header = parse_wave_header(payload)
        classification = classify_entry(e.size, head, header)
        ratio = entropy_ratio(payload[:256]) if payload else 0.0
        output_rel = f"waves/wave_{i:0{width}d}.bin"

        # Duplicate-of detection — same 32-byte prefix AND same size.
        # Requiring matching size excludes "long clip prefixed by a
        # short clip" false positives; 32 bytes is enough to
        # distinguish distinct sounds while tolerating minor trailing
        # differences.
        dup_key = (head[:32], e.size)
        duplicate_of = first_prefix_seen.get(dup_key, -1)
        if duplicate_of < 0:
            first_prefix_seen[dup_key] = i
        else:
            report.duplicates_detected += 1

        if classification == "too-small":
            report.too_small += 1
        elif classification == "xbox-adpcm":
            report.xbox_adpcm += 1
        elif classification == "pcm-raw":
            report.pcm_raw += 1
        elif classification == "non-audio":
            report.non_audio += 1
        else:
            report.likely_animation += 1

        should_write = True
        if entropy_min > 0.0 and ratio < entropy_min:
            should_write = False
        if only_audio and classification not in (
                "xbox-adpcm", "pcm-raw"):
            should_write = False

        if should_write:
            (out / output_rel).write_bytes(payload)
            report.written += 1

        wav_output_rel = ""
        if should_write and emit_wav and header is not None:
            # Strip the 16-byte header before wrapping so the WAV
            # ``data`` chunk is pure codec payload, matching what
            # the engine hands to ``IDirectSoundBuffer_SetBufferData``.
            wav_bytes = _build_wav_container(header, payload[16:])
            wav_output_rel = output_rel[:-4] + ".wav"
            (out / wav_output_rel).write_bytes(wav_bytes)
            report.wav_written += 1

        # Raw-PCM preview wrapper for non-audio entries.  The April
        # 2026 RE pass pinned that these bytes are NOT consumed as
        # audio by the engine (``FUN_000AC400`` rejects anything
        # with codec_id ∉ {0, 1}); keeping the preview path around
        # only as a generic "dump anything high-entropy as a raw-PCM
        # WAV for inspection" helper.  Most users can leave
        # ``--raw-previews`` off now that the non-audio status of
        # these entries is confirmed.  Skip duplicates — same bytes
        # would land N times otherwise.
        preview_output_rel = ""
        if (should_write and emit_raw_previews
                and classification == "non-audio"
                and duplicate_of < 0):
            preview_bytes = build_raw_preview_wav(
                payload, sample_rate=preview_sample_rate,
                channels=1, bits_per_sample=16)
            preview_output_rel = output_rel[:-4] + ".preview.wav"
            (out / preview_output_rel).write_bytes(preview_bytes)
            report.preview_wav_written += 1

        # Opportunistic naming — only for codec-recognised entries
        # (the pool order aligns with those, not with animation data).
        probable_name = ""
        if classification in ("xbox-adpcm", "pcm-raw"):
            probable_name = next(audio_name_iter, "")

        report.entries.append(WaveEntry(
            index=i,
            file_offset=e.file_offset,
            size=e.size,
            classification=classification,
            entropy=ratio,
            first_bytes_hex=head[:32].hex(),
            output_rel=output_rel if should_write else "",
            header=header,
            wav_output_rel=wav_output_rel,
            probable_name=probable_name,
            duplicate_of=duplicate_of,
            preview_output_rel=preview_output_rel,
        ))

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
        "",
        f"  total wave entries:      {report.total_waves}",
        f"  blobs written to disk:   {report.written}",
        f"  WAV wrappers emitted:    {report.wav_written}",
        f"  raw-PCM previews:        {report.preview_wav_written}",
        f"  duplicates detected:     {report.duplicates_detected}",
        "  classification:",
        f"     xbox-adpcm:           {report.xbox_adpcm}",
        f"     pcm-raw:              {report.pcm_raw}",
        f"     non-audio:            {report.non_audio}",
        f"     likely-animation:     {report.likely_animation}",
        f"     too-small:            {report.too_small}",
    ]
    if preview > 0 and report.entries:
        lines.append("")
        lines.append(f"  Preview (first {preview}):")
        for e in report.entries[:preview]:
            dur = (f" {e.header.duration_ms}ms"
                   if e.header is not None else "")
            name = f"  [{e.probable_name}]" if e.probable_name else ""
            lines.append(
                f"    [{e.index:4d}] {e.classification:<18s} "
                f"size={e.size:>6} B  entropy={e.entropy:.2f}"
                f"{dur}{name}  → {e.output_rel or '(skipped)'}")
    return "\n".join(lines)
