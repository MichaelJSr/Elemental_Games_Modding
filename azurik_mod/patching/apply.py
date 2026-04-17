"""Apply / verify helpers for PatchSpec, ParametricPatch, and raw byte patches."""

from __future__ import annotations

from azurik_mod.patching.spec import ParametricPatch, PatchSpec


def apply_xbe_patch(
    xbe_data: bytearray,
    label: str,
    offset: int,
    original: bytes,
    patch: bytes,
) -> bool:
    """Apply a single raw byte patch with verification.

    The idempotent check ("already applied") lets the CLI re-run without
    erroring on a XBE that was already patched in a previous invocation.
    """
    if len(patch) != len(original):
        print(f"  ERROR: {label} — original ({len(original)}B) and "
              f"patch ({len(patch)}B) lengths differ")
        return False
    size = len(original)
    if offset + size > len(xbe_data):
        print(f"  WARNING: {label} — offset 0x{offset:X} out of range, skipping")
        return False
    current = bytes(xbe_data[offset:offset + size])
    if current == original:
        xbe_data[offset:offset + size] = patch
        print(f"  {label}")
        return True
    if current == patch:
        print(f"  {label} (already applied)")
        return True
    print(f"  WARNING: {label} — bytes at 0x{offset:X} don't match "
          f"(got {current.hex()}, expected {original.hex()})")
    return False


def apply_patch_spec(xbe_data: bytearray, spec: PatchSpec) -> bool:
    """Apply a single PatchSpec to the XBE data."""
    return apply_xbe_patch(
        xbe_data, spec.label, spec.file_offset, spec.original, spec.patch
    )


def verify_patch_spec(xbe_data: bytes, spec: PatchSpec) -> str:
    """Check whether `spec` has been applied to `xbe_data`.

    Returns one of:
        "applied"      — bytes at offset equal spec.patch
        "original"     — bytes at offset equal spec.original (not patched)
        "mismatch"     — bytes match neither
        "out-of-range" — offset is past the end of xbe_data
    """
    size = len(spec.patch)
    offset = spec.file_offset
    if offset + size > len(xbe_data):
        return "out-of-range"
    current = bytes(xbe_data[offset:offset + size])
    if current == spec.patch:
        return "applied"
    if current == spec.original:
        return "original"
    return "mismatch"


# ---------------------------------------------------------------------------
# Parametric patches — slider-driven float rewrites
# ---------------------------------------------------------------------------


def apply_parametric_patch(
    xbe_data: bytearray,
    patch: ParametricPatch,
    value: float,
) -> bool:
    """Encode `value` and write it at the patch's VA.

    Virtual parametric patches (va == 0 and size == 0) are handled by
    the pack's own apply function and this helper becomes a no-op.
    """
    if patch.is_virtual:
        return True  # caller handles via a different code path

    if not (patch.slider_min <= value <= patch.slider_max):
        print(f"  ERROR: {patch.label} — value {value} outside "
              f"[{patch.slider_min}, {patch.slider_max}], skipping")
        return False

    payload = patch.encode(float(value))
    if len(payload) != patch.size:
        print(f"  ERROR: {patch.label} — encode produced "
              f"{len(payload)} B but size is {patch.size}")
        return False

    offset = patch.file_offset
    if offset + patch.size > len(xbe_data):
        print(f"  WARNING: {patch.label} — offset 0x{offset:X} out of range")
        return False

    xbe_data[offset:offset + patch.size] = payload
    print(f"  {patch.label} = {value} {patch.unit}")
    return True


def verify_parametric_patch(xbe_data: bytes, patch: ParametricPatch) -> str:
    """Return the current state of a parametric patch.

    Returns one of:
        "default"      — bytes at VA decode to the baseline default
        "custom"       — bytes decode to a different value within range
        "out-of-range" — VA is past the end of xbe_data
        "mismatch"     — bytes don't decode to a valid float in range
        "virtual"      — this is a virtual slider with no XBE footprint
    """
    if patch.is_virtual:
        return "virtual"
    offset = patch.file_offset
    if offset + patch.size > len(xbe_data):
        return "out-of-range"
    current = bytes(xbe_data[offset:offset + patch.size])
    if current == patch.original:
        return "default"
    try:
        value = patch.decode(current)
    except Exception:  # noqa: BLE001
        return "mismatch"
    if patch.slider_min <= value <= patch.slider_max:
        return "custom"
    return "mismatch"


def read_parametric_value(xbe_data: bytes, patch: ParametricPatch) -> float | None:
    """Decode the current value at the patch's VA, or None if unreadable."""
    if patch.is_virtual:
        return None
    offset = patch.file_offset
    if offset + patch.size > len(xbe_data):
        return None
    current = bytes(xbe_data[offset:offset + patch.size])
    try:
        return patch.decode(current)
    except Exception:  # noqa: BLE001
        return None
