"""Shared helpers for QoL feature folders.

Several QoL features (``qol_gem_popups``, ``qol_other_popups``) apply
the same mechanism: null the first byte of a list of localisation
resource-key strings in ``.rdata``.  The shared helper lives here so
each feature folder can import it without round-tripping through a
top-level module.
"""

from __future__ import annotations


def null_resource_keys(
    xbe_data: bytearray,
    offsets: list[int] | tuple[int, ...],
    label: str,
) -> None:
    """Break the localisation lookup for every resource key in ``offsets``.

    Each offset is expected to point at the first byte of a
    null-terminated ASCII string like ``loc/english/popups/<name>``.
    Replacing the first byte with ``0x00`` turns the path into an empty
    string, the game's resource lookup fails silently, and the
    associated popup / tutorial never renders.

    Callers pass printable-ASCII-typed offsets; non-printable bytes are
    treated as "wrong target" and reported as a warning rather than
    blindly overwritten.
    """
    patched, skipped = 0, 0
    for off in offsets:
        if off >= len(xbe_data):
            print(f"  WARNING: {label} offset 0x{off:X} out of range")
            skipped += 1
        elif xbe_data[off] == 0x00:
            patched += 1  # already nulled from a previous run
        elif 0x20 <= xbe_data[off] <= 0x7E:
            xbe_data[off] = 0x00
            patched += 1
        else:
            print(f"  WARNING: {label} byte at 0x{off:X} is 0x{xbe_data[off]:02X} "
                  f"(expected printable ASCII), skipping")
            skipped += 1
    print(f"  Suppressed {patched} {label}"
          + (f"  [{skipped} warnings]" if skipped else ""))
