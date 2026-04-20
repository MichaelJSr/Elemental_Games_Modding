"""Hand-assembled shim authoring primitives.

A growing family of feature packs ships machine-code shims
assembled directly in Python (``wing_flap_count``,
``flap_at_peak``, ``slope_slide_speed``, ``root_motion_roll``,
``root_motion_climb``).  They all share the same pipeline:

    1. Verify the hook-site bytes match vanilla (with idempotent
       "already applied" detection for re-runs).
    2. Carve a ``scale`` / ``int`` data block via
       :func:`_carve_shim_landing` with a 4-byte ``0xFF`` sentinel
       to defend against the allocator's zero-padding back-scan.
    3. Carve a shim-body placeholder (all ``0xCC`` / INT 3 so
       mis-directed jumps trap immediately), get its VA.
    4. Build the real shim body (caller-supplied callback that
       takes ``(shim_va, data_vas)`` and returns the bytes) and
       overwrite the placeholder in place.
    5. Install the 5-byte ``CALL``/``JMP`` trampoline at the hook
       site, optionally padded with a ``NOP``.

Pre-helper each pack hand-rolled ~80-150 LoC of boilerplate.
This module packages it up so new hand-assembled shims can
express their unique bit (the body builder) and get everything
else for free.  C-shim users keep the existing
:class:`TrampolinePatch` + :class:`ShimSource` path — this
module is for the "hand-assembled in Python" pattern, not a
replacement for the COFF toolchain.

Typical usage::

    from azurik_mod.patching.shim_builder import (
        HandShimSpec, install_hand_shim, whitelist_for_hand_shim,
        emit_jmp_rel32, emit_fld_abs32,
    )

    _SPEC = HandShimSpec(
        hook_va=0x89409,
        hook_vanilla=bytes.fromhex("d95e2c8b4620"),
        trampoline_mode="jmp",       # "jmp" or "call"
        hook_pad_nops=1,              # total hook width = 5 + 1 = 6 B
        hook_return_va=0x8940F,
        body_size=43,
    )

    def _build_body(shim_va, k_va):
        ...

    def apply_feature(xbe, scale):
        return install_hand_shim(
            xbe, _SPEC,
            data_block=pack("<f", k) + b"\\xFF" * 4,
            build_body=lambda shim_va, data_va: _build_body(shim_va, data_va),
            label="flap_at_peak",
        )

See :mod:`azurik_mod.patches.flap_at_peak` for a worked example.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable, Optional

__all__ = [
    "HandShimSpec",
    "InstalledShim",
    "install_hand_shim",
    "whitelist_for_hand_shim",
    "emit_jmp_rel32",
    "emit_call_rel32",
    "emit_fld_abs32",
    "emit_fmul_abs32",
    "emit_fstp_abs32",
    "with_sentinel",
]


# ---------------------------------------------------------------------------
# Spec + result dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HandShimSpec:
    """Declarative description of a hand-assembled shim's hook site.

    Attributes:
        hook_va:        Virtual address where the trampoline
                        instruction lives.
        hook_vanilla:   Full original bytes at ``hook_va`` (length
                        = 5 + ``hook_pad_nops`` when the trampoline
                        replaces exactly the hook window).
        trampoline_mode: ``"call"`` (emit ``E8 rel32``) or ``"jmp"``
                        (emit ``E9 rel32``).  Use ``"call"`` when
                        the shim ends with ``RET`` and resumes at
                        the instruction AFTER the trampoline; use
                        ``"jmp"`` when the shim emits its own final
                        JMP back to ``hook_return_va``.
        hook_pad_nops:  Number of ``0x90`` padding bytes appended
                        after the 5-byte rel32 trampoline (e.g. 1
                        when the trampoline covers a 6-byte window).
                        0 for flush 5-byte replacements.
        hook_return_va: Where the shim ultimately returns control.
                        For ``trampoline_mode == "call"`` this is
                        implicit (hook_va + 5 + hook_pad_nops).
                        For ``"jmp"`` mode the shim body uses this
                        to compute its closing JMP rel32.
        body_size:      Size of the hand-assembled shim body in
                        bytes.  Verified via ``assert`` inside
                        ``build_body``.
    """

    hook_va: int
    hook_vanilla: bytes
    trampoline_mode: str
    hook_pad_nops: int = 0
    hook_return_va: int = 0
    body_size: int = 0

    def __post_init__(self) -> None:
        if self.trampoline_mode not in ("call", "jmp"):
            raise ValueError(
                f"trampoline_mode must be 'call' or 'jmp', "
                f"got {self.trampoline_mode!r}")
        expected_window = 5 + self.hook_pad_nops
        if len(self.hook_vanilla) != expected_window:
            raise ValueError(
                f"hook_vanilla must be exactly 5 + hook_pad_nops "
                f"= {expected_window} bytes, got "
                f"{len(self.hook_vanilla)}")
        if self.hook_return_va == 0:
            # Default: the instruction right after the full hook window.
            object.__setattr__(
                self, "hook_return_va", self.hook_va + expected_window)

    @property
    def hook_width(self) -> int:
        """Total bytes overwritten at the hook site (5 + NOP pad)."""
        return 5 + self.hook_pad_nops


@dataclass(frozen=True)
class InstalledShim:
    """Result of a successful :func:`install_hand_shim` call.

    Returned so callers can log details (sizes + VAs) and so
    follow-up whitelist helpers have a single struct to consume.
    """

    shim_va: int
    """Absolute VA where the shim body was landed."""

    shim_file_offset: int
    """File offset of the shim body inside the XBE buffer."""

    data_va: int
    """Absolute VA of the carved data block (scale float / ints)."""

    data_size: int
    """Size of the data block in bytes (incl. sentinel)."""

    body_size: int
    """Size of the shim body in bytes."""


# ---------------------------------------------------------------------------
# Small emitters for common FP + jump instructions
# ---------------------------------------------------------------------------

def emit_jmp_rel32(from_origin_after: int, to_va: int) -> bytes:
    """Emit ``E9 rel32`` where rel32 targets ``to_va``.

    ``from_origin_after`` is the VA of the instruction *after* the
    JMP (i.e. ``jmp_va + 5``) — the convention x86 uses to compute
    the signed 32-bit displacement.
    """
    rel32 = to_va - from_origin_after
    return b"\xE9" + struct.pack("<i", rel32)


def emit_call_rel32(from_origin_after: int, to_va: int) -> bytes:
    """Emit ``E8 rel32`` targeting ``to_va`` (same calling convention
    as :func:`emit_jmp_rel32`)."""
    rel32 = to_va - from_origin_after
    return b"\xE8" + struct.pack("<i", rel32)


def emit_fld_abs32(abs32: int) -> bytes:
    """``FLD dword [abs32]`` — 6 bytes (``D9 05 <abs32>``)."""
    return b"\xD9\x05" + struct.pack("<I", abs32)


def emit_fmul_abs32(abs32: int) -> bytes:
    """``FMUL dword [abs32]`` — 6 bytes (``D8 0D <abs32>``)."""
    return b"\xD8\x0D" + struct.pack("<I", abs32)


def emit_fstp_abs32(abs32: int) -> bytes:
    """``FSTP dword [abs32]`` — 6 bytes (``D9 1D <abs32>``)."""
    return b"\xD9\x1D" + struct.pack("<I", abs32)


# ---------------------------------------------------------------------------
# Data-block helpers
# ---------------------------------------------------------------------------

_FF_SENTINEL_LEN = 4


def with_sentinel(data: bytes) -> bytes:
    """Append a 4-byte ``0xFF`` sentinel to ``data``.

    The :func:`_carve_shim_landing` allocator's ``find_text_padding``
    back-scan from the section end skips trailing zero bytes,
    which lets a *subsequent* allocation silently overwrite the
    zero tail of a prior allocation (e.g. the last byte of a
    small integer).  The sentinel stops the back-scan cleanly so
    packed ``pack("<iii", …)`` / ``pack("<f", …)`` blocks stay
    intact.  Every hand-shim pack uses this helper — centralised
    here so the rationale lives in one place.
    """
    return data + b"\xFF" * _FF_SENTINEL_LEN


# ---------------------------------------------------------------------------
# Core installer
# ---------------------------------------------------------------------------

# Sentinel for "already-installed trampoline detected, skip".
class _AlreadyInstalled:
    pass


def install_hand_shim(
    xbe_data: bytearray,
    spec: HandShimSpec,
    *,
    data_block: bytes,
    build_body: Callable[[int, int], bytes],
    label: str = "hand_shim",
    verbose: bool = True,
) -> Optional[InstalledShim]:
    """Install a hand-assembled shim at ``spec.hook_va``.

    Pipeline:

    1. Read ``spec.hook_width`` bytes at the hook.  If they match
       an already-installed trampoline of the expected shape
       (``E8``/``E9`` opcode + signed rel32 + NOP padding), return
       ``None`` and print "(already applied)".
    2. Otherwise, verify the bytes equal ``spec.hook_vanilla``.
       Drift → warn + return ``None``.
    3. Carve ``data_block`` via ``_carve_shim_landing`` — get its
       VA.  (Callers are expected to include the 0xFF sentinel
       themselves via :func:`with_sentinel`.)
    4. Carve ``spec.body_size`` bytes of ``0xCC`` placeholder,
       get the shim's VA.
    5. Call ``build_body(shim_va, data_va)`` and overwrite the
       placeholder with the result.  Build-body is responsible
       for asserting its returned length equals ``spec.body_size``.
    6. Install the trampoline at ``spec.hook_va``: 5-byte
       ``E8``/``E9`` + rel32 + ``spec.hook_pad_nops`` NOPs.

    Returns :class:`InstalledShim` on success, ``None`` on no-op
    / drift / already-applied.
    """
    # Lazy imports so this module stays importable in tests that
    # don't exercise the full XBE pipeline.
    from azurik_mod.patching.apply import _carve_shim_landing
    from azurik_mod.patching.xbe import va_to_file

    hook_off = va_to_file(spec.hook_va)
    width = spec.hook_width
    current = bytes(xbe_data[hook_off:hook_off + width])

    # Order matters: vanilla comparison FIRST so we don't mistake
    # a vanilla E8-CALL at the hook for an already-installed
    # trampoline (root_motion_roll / _climb both hook vanilla
    # E8 CALLs; the trampoline-shape heuristic can't tell them
    # apart without the content check).
    if current == spec.hook_vanilla:
        # Vanilla — proceed with install below.
        pass
    else:
        # Not vanilla.  Is it OUR trampoline shape?
        #   byte 0:              E8 (call) or E9 (jmp)
        #   bytes 1..4:          rel32 (signed 32-bit, any value)
        #   bytes 5..5+pad_nops: 0x90 padding
        expected_opcode = (
            0xE8 if spec.trampoline_mode == "call" else 0xE9)
        looks_like_trampoline = (
            current[0] == expected_opcode
            and all(b == 0x90 for b in current[5:width]))
        if looks_like_trampoline:
            if verbose:
                print(f"  {label} (already applied)")
            return None
        # Bytes drifted and don't match a trampoline shape either.
        print(f"  WARNING: {label} — hook site at VA "
              f"0x{spec.hook_va:X} drifted (got {current.hex()}); "
              f"skipping.")
        return None

    # Carve the data block (caller pre-includes the sentinel).
    _, data_va = _carve_shim_landing(xbe_data, data_block)

    # Carve a placeholder body, assemble real bytes, overwrite.
    placeholder = b"\xCC" * spec.body_size
    body_off, shim_va = _carve_shim_landing(xbe_data, placeholder)
    body = build_body(shim_va, data_va)
    if len(body) != spec.body_size:
        raise AssertionError(
            f"{label}: build_body returned {len(body)} bytes, "
            f"expected {spec.body_size}")
    xbe_data[body_off:body_off + spec.body_size] = body

    # Install the trampoline.
    rel32 = shim_va - (spec.hook_va + 5)
    if spec.trampoline_mode == "call":
        tramp = b"\xE8" + struct.pack("<i", rel32)
    else:
        tramp = b"\xE9" + struct.pack("<i", rel32)
    tramp += b"\x90" * spec.hook_pad_nops
    xbe_data[hook_off:hook_off + width] = tramp

    if verbose:
        print(f"  {label}: shim @ VA 0x{shim_va:X} (+{spec.body_size} B); "
              f"data @ VA 0x{data_va:X} (+{len(data_block)} B)")

    return InstalledShim(
        shim_va=shim_va,
        shim_file_offset=body_off,
        data_va=data_va,
        data_size=len(data_block),
        body_size=spec.body_size,
    )


# ---------------------------------------------------------------------------
# Whitelist helper
# ---------------------------------------------------------------------------

def whitelist_for_hand_shim(
    xbe: bytes,
    spec: HandShimSpec,
    *,
    data_whitelist_size: int = 8,
    data_abs32_offsets: tuple[int, ...] = (),
    data_abs32_opcode: bytes = b"\xD9\x05",
) -> list[tuple[int, int]]:
    """Produce ``(lo, hi)`` byte-offset ranges for ``verify-patches
    --strict`` to accept as expected post-apply drift.

    Always whitelists the ``spec.hook_width``-byte trampoline slot
    at ``spec.hook_va``.  Follows the trampoline (if installed) to
    the shim body and whitelists ``spec.body_size`` bytes there.
    Then, for each offset in ``data_abs32_offsets``, parses the
    shim body for a ``data_abs32_opcode``+``<abs32>`` pattern and
    whitelists ``data_whitelist_size`` bytes at the decoded VA.

    For packs that carry >1 data block or use a non-``FLD [abs32]``
    reference, pass a custom ``data_abs32_opcode`` (e.g. ``b"\\xA1"``
    for wing_flap_count's ``MOV EAX, ds:[abs32]`` pattern, though
    that one still has bespoke logic for the 3× packed int case).

    Returns an empty list when ``hook_va`` is out of range (the
    callback must be graceful for verify-patches to work on
    pre-apply / non-Azurik buffers).
    """
    from azurik_mod.patching.xbe import resolve_va_to_file, va_to_file

    try:
        hook_off = va_to_file(spec.hook_va)
    except Exception:  # noqa: BLE001
        return []

    width = spec.hook_width
    ranges: list[tuple[int, int]] = [(hook_off, hook_off + width)]

    if len(xbe) < hook_off + width:
        return ranges

    tramp = xbe[hook_off:hook_off + width]
    expected_opcode = 0xE8 if spec.trampoline_mode == "call" else 0xE9
    if tramp[0] != expected_opcode:
        return ranges
    if not all(b == 0x90 for b in tramp[5:width]):
        return ranges

    rel32 = struct.unpack("<i", tramp[1:5])[0]
    shim_va = spec.hook_va + 5 + rel32
    shim_off = resolve_va_to_file(xbe, shim_va)
    if shim_off is None:
        return ranges
    ranges.append((shim_off, shim_off + spec.body_size))

    # Decode data-reference abs32 from the body.
    body = xbe[shim_off:shim_off + spec.body_size]
    opcode_len = len(data_abs32_opcode)
    for offset in data_abs32_offsets:
        end = offset + opcode_len + 4
        if end > len(body):
            continue
        if body[offset:offset + opcode_len] != data_abs32_opcode:
            continue
        abs32 = struct.unpack(
            "<I", body[offset + opcode_len:end])[0]
        data_off = resolve_va_to_file(xbe, abs32)
        if data_off is not None:
            ranges.append(
                (data_off, data_off + data_whitelist_size))

    return ranges
