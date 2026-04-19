"""XBR record-layout inspector.

Takes an XBR file + a TOC entry tag and tries to classify the
byte layout of the first N records with heuristic field types.
This accelerates RE of per-level record layouts without a full
Ghidra round-trip.

## Heuristics

For every 4-byte window within a record we run a fixed panel of
predicates and emit the best-fitting type label:

- ``f32``  — IEEE 754 float in a plausible gameplay range
  (``|x| < 1e9``, non-NaN, non-subnormal, exponent sane).
- ``int32`` — small-ish signed int (``-2**20 < x < 2**20``).
- ``u32``  — large-ish unsigned int / bitfield.
- ``off``  — value plausibly points inside the file (used for
  record → string-pool offset hints).
- ``tag``  — all four bytes are printable ASCII (fourcc).
- ``ptr``  — value in the 0x00010000..image_end range (XBE VA).
- ``zero`` — all four bytes are 0.

Classifications are shown alongside the raw hex so the user can
spot patterns across records (e.g. "column 4 is always a small
float" → "that's gotta be a scale").

## CLI

``azurik-mod xbr inspect FILE.xbr --tag surf --entries 3``

Shows the first 3 ``surf`` entries decoded as byte tables.  Pass
``--stride N`` to force a fixed record stride when you already
know it; otherwise the inspector auto-probes strides 16 / 20 /
24 / 32 and picks the one that produces the most "clean" rows.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path


_PRINTABLE = set(range(0x20, 0x7F))


@dataclass(frozen=True)
class FieldClassification:
    """Guess for what type of value lives at ``offset`` inside a
    record."""

    offset: int
    raw: bytes
    best_type: str
    value_display: str


@dataclass
class RecordInspection:
    """One record's byte-level classification."""

    index: int
    file_offset: int
    stride: int
    fields: list[FieldClassification] = field(default_factory=list)


@dataclass
class SectionInspection:
    """Full report for one TOC-tag-filtered section."""

    tag: str
    stride: int
    records: list[RecordInspection] = field(default_factory=list)
    auto_detected_stride: bool = False


# ---------------------------------------------------------------------------
# Classifiers (run in priority order; first match wins)
# ---------------------------------------------------------------------------


def _looks_like_zero(raw: bytes) -> bool:
    return raw == b"\x00\x00\x00\x00"


def _looks_like_fourcc(raw: bytes) -> bool:
    if len(raw) != 4:
        return False
    if not all(b in _PRINTABLE for b in raw):
        return False
    ascii_s = raw.decode("ascii")
    # Alphanumeric + underscore + all-lowercase first char → fourcc.
    if not ascii_s.isalnum():
        return False
    return True


def _looks_like_f32(raw: bytes) -> tuple[bool, float | None]:
    (val,) = struct.unpack("<f", raw)
    if not math.isfinite(val):
        return False, None
    if abs(val) > 1e9 or (val != 0.0 and abs(val) < 1e-30):
        return False, None
    return True, val


def _looks_like_ptr(raw: bytes, image_base: int, image_end: int) -> bool:
    (val,) = struct.unpack("<I", raw)
    return image_base <= val < image_end


def _looks_like_off(raw: bytes, file_size: int) -> bool:
    (val,) = struct.unpack("<I", raw)
    return 0 < val < file_size


def _looks_like_small_int(raw: bytes) -> tuple[bool, int | None]:
    (val,) = struct.unpack("<i", raw)
    if -0x10_0000 <= val <= 0x10_0000:
        return True, val
    return False, None


def _classify_u32(raw: bytes, image_base: int, image_end: int,
                  file_size: int) -> FieldClassification:
    """Pick the most-specific label for a 4-byte window."""
    # Order matters: check rare markers (zero, fourcc) before
    # falling into float / int / pointer / offset.
    if _looks_like_zero(raw):
        return FieldClassification(offset=0, raw=raw,
                                   best_type="zero",
                                   value_display="0")
    if _looks_like_fourcc(raw):
        return FieldClassification(offset=0, raw=raw,
                                   best_type="fourcc",
                                   value_display=raw.decode("ascii"))
    ok_f, fval = _looks_like_f32(raw)
    if ok_f and fval is not None and fval == int(fval) and \
            -0x100 < fval < 0x100:
        # A plausible float that's also a small integer is ambiguous.
        # Prefer the int reading to reduce false-"f32" positives on
        # common 1/2/3 values.
        return FieldClassification(offset=0, raw=raw,
                                   best_type="int32",
                                   value_display=str(int(fval)))
    if ok_f and fval is not None:
        return FieldClassification(offset=0, raw=raw,
                                   best_type="f32",
                                   value_display=f"{fval:.4g}")
    if _looks_like_ptr(raw, image_base, image_end):
        (val,) = struct.unpack("<I", raw)
        return FieldClassification(offset=0, raw=raw,
                                   best_type="ptr",
                                   value_display=f"0x{val:08X}")
    if _looks_like_off(raw, file_size):
        (val,) = struct.unpack("<I", raw)
        return FieldClassification(offset=0, raw=raw,
                                   best_type="off",
                                   value_display=f"+0x{val:X}")
    ok_i, ival = _looks_like_small_int(raw)
    if ok_i and ival is not None:
        return FieldClassification(offset=0, raw=raw,
                                   best_type="int32",
                                   value_display=str(ival))
    (val,) = struct.unpack("<I", raw)
    return FieldClassification(offset=0, raw=raw,
                               best_type="u32",
                               value_display=f"0x{val:08X}")


# ---------------------------------------------------------------------------
# Stride auto-detection
# ---------------------------------------------------------------------------

_COMMON_STRIDES = (16, 20, 24, 28, 32, 40, 48, 64, 80, 96)


def _stride_quality(data: bytes, base: int, size: int, stride: int,
                    max_records: int = 8) -> float:
    """Score a candidate stride by how many records start with a
    "clean" pattern (zeroed padding, fourcc-looking ASCII, or a
    plausible float)."""
    if stride <= 0 or size < stride:
        return 0.0
    count = min(size // stride, max_records)
    if count < 2:
        return 0.0
    clean = 0
    for i in range(count):
        raw = data[base + i * stride:base + i * stride + 4]
        if _looks_like_zero(raw) or _looks_like_fourcc(raw):
            clean += 1
        else:
            ok_f, _ = _looks_like_f32(raw)
            ok_i, _ = _looks_like_small_int(raw)
            if ok_f or ok_i:
                clean += 0.5
    return clean / count


def _auto_detect_stride(data: bytes, base: int, size: int) -> int:
    """Pick the stride with the highest heuristic score; fall back
    to 16 when every candidate ties at zero."""
    best_stride = 16
    best_score = -1.0
    for stride in _COMMON_STRIDES:
        score = _stride_quality(data, base, size, stride)
        if score > best_score:
            best_score = score
            best_stride = stride
    return best_stride


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _parse_toc_simple(data: bytes) -> list[tuple[str, int, int]]:
    """Minimal standalone TOC parser (to avoid importing
    ``scripts/xbr_parser.py`` at runtime).  Returns
    ``[(tag, size, file_offset), ...]``."""
    if data[:4] != b"xobx":
        raise ValueError("not an XBR file")
    # Azurik's TOC starts at 0x40 with 16-byte records:
    #   u32 size, char[4] tag, u32 flags, u32 file_offset
    out: list[tuple[str, int, int]] = []
    off = 0x40
    while off + 16 <= len(data):
        size = struct.unpack_from("<I", data, off)[0]
        tag_bytes = data[off + 4:off + 8]
        flags = struct.unpack_from("<I", data, off + 8)[0]
        file_off = struct.unpack_from("<I", data, off + 12)[0]
        if size == 0 and flags == 0 and file_off == 0:
            break
        try:
            tag = tag_bytes.decode("ascii")
        except UnicodeDecodeError:
            tag = tag_bytes.hex()
        out.append((tag, size, file_off))
        off += 16
    return out


def inspect_xbr(path: str | Path, *, tag: str, entries: int = 3,
                stride: int | None = None,
                fields_per_row: int = 6
                ) -> SectionInspection:
    """Inspect the first ``entries`` records of the section
    matching ``tag``.

    ``stride=None`` auto-probes; otherwise the caller's value is
    used verbatim.  ``fields_per_row`` caps how many 4-byte
    columns are classified per record (keep small for terminal
    rendering).
    """
    p = Path(path).expanduser().resolve()
    data = p.read_bytes()

    toc = _parse_toc_simple(data)
    target = next((e for e in toc if e[0] == tag), None)
    if target is None:
        available = sorted({t for t, _, _ in toc})
        raise ValueError(
            f"{p.name} has no TOC entry tagged {tag!r}.  "
            f"Available: {available}")

    _, size, base = target

    if stride is None:
        stride = _auto_detect_stride(data, base, size)
        auto_detected = True
    else:
        auto_detected = False

    # XBE VA range guess (for ptr classification inside records
    # that might store VAs from default.xbe):  most XBE images
    # are mapped 0x00010000..0x00400000.  Using a conservative
    # window doesn't hurt false positives much.
    image_base = 0x00010000
    image_end = 0x00400000

    records: list[RecordInspection] = []
    for i in range(min(entries, size // max(stride, 1))):
        off = base + i * stride
        rec = RecordInspection(index=i, file_offset=off, stride=stride)
        for field_idx in range(fields_per_row):
            col_off = field_idx * 4
            if col_off + 4 > stride:
                break
            raw = data[off + col_off:off + col_off + 4]
            if len(raw) < 4:
                break
            cls = _classify_u32(raw, image_base, image_end, len(data))
            rec.fields.append(FieldClassification(
                offset=col_off, raw=raw,
                best_type=cls.best_type,
                value_display=cls.value_display))
        records.append(rec)

    return SectionInspection(
        tag=tag, stride=stride, records=records,
        auto_detected_stride=auto_detected)


def format_inspection(insp: SectionInspection) -> str:
    """Render a :class:`SectionInspection` as a human-readable
    table."""
    auto_note = ("(auto-detected)" if insp.auto_detected_stride
                 else "(explicit)")
    lines = [
        f"Section {insp.tag!r}  stride={insp.stride} B  "
        f"{auto_note}",
        f"{len(insp.records)} record(s) inspected.",
    ]
    for rec in insp.records:
        lines.append("")
        lines.append(
            f"Record #{rec.index}  file 0x{rec.file_offset:06X}:")
        for f in rec.fields:
            lines.append(
                f"  + {f.offset:3d}  {f.raw.hex():<8s}  "
                f"[{f.best_type:<6s}]  {f.value_display}")
    return "\n".join(lines)


__all__ = [
    "FieldClassification",
    "RecordInspection",
    "SectionInspection",
    "format_inspection",
    "inspect_xbr",
]
