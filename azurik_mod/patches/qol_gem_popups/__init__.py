"""qol_gem_popups — hide the first-time gem-collect popups.

The popup system looks up its message by a localisation resource key
like ``loc/english/popups/diamonds``.  Nulling the first byte of the
key in ``.rdata`` turns it into an empty string; the game's resource
lookup fails silently and no popup renders.

Because every offset is a single imperative byte-null (not a fixed
``PatchSpec``), this feature wires its logic through ``custom_apply``.
The offsets are still declared as ``extra_whitelist_ranges`` so
``verify-patches --strict`` stays happy.
"""

from __future__ import annotations

from azurik_mod.patches._qol_shared import null_resource_keys
from azurik_mod.patching.registry import Feature, register_feature


# File offsets of each gem popup's resource-key string in .rdata:
#   0x1977D8  loc/english/popups/collect_obsidians
#   0x197800  loc/english/popups/sapphires
#   0x197820  loc/english/popups/rubies
#   0x19783C  loc/english/popups/diamonds
#   0x197858  loc/english/popups/emeralds
GEM_POPUP_OFFSETS: list[int] = [
    0x197858,  # emeralds
    0x19783C,  # diamonds
    0x197820,  # rubies
    0x197800,  # sapphires
    0x1977D8,  # collect_obsidians
]


def apply_gem_popups_patch(xbe_data: bytearray) -> None:
    """Hide the first-time "Collect 100 <gem>" popup for every gem type."""
    null_resource_keys(xbe_data, GEM_POPUP_OFFSETS, "gem pickup popups")


def _custom_apply(xbe_data: bytearray, **_params) -> None:
    """Dispatcher hook — just delegate to the traditional apply_*_patch
    function so both call paths produce identical output."""
    apply_gem_popups_patch(xbe_data)


FEATURE = register_feature(Feature(
    name="qol_gem_popups",
    description="Hides first-time gem-collect popups (all 5 gem types).",
    sites=[],
    apply=apply_gem_popups_patch,
    default_on=False,
    included_in_randomizer_qol=False,
    category="qol",
    tags=(),
    # Each offset is a single-byte null into a resource-key string;
    # declare them so verify-patches --strict sees them as expected.
    extra_whitelist_ranges=tuple((off, off + 1) for off in GEM_POPUP_OFFSETS),
    custom_apply=_custom_apply,
))


__all__ = [
    "FEATURE",
    "GEM_POPUP_OFFSETS",
    "apply_gem_popups_patch",
]
