"""qol_other_popups — hide tutorial / key / health / power-up first-time popups.

Same mechanism as ``qol_gem_popups``: null the first byte of each
localisation resource-key string in ``.rdata`` so the game's resource
lookup returns nothing.

Deliberately does NOT touch ``loc/english/popups/gameover`` — that's
the death-screen message, not a pickup popup; suppressing it would
silently kick the player to the menu with no explanation.
"""

from __future__ import annotations

from azurik_mod.patches._qol_shared import null_resource_keys
from azurik_mod.patching.registry import Feature, register_feature


OTHER_POPUP_OFFSETS: list[int] = [
    0x194A78,  # swim              — first-swim tutorial
    0x197760,  # 6keys             — all six keys collected milestone
    0x19777C,  # key               — first key pickup
    0x197794,  # chromatic_powerup
    0x1977BC,  # health            — first health pickup
    0x197874,  # water_powerup
    0x197898,  # fire_powerup
    0x1978B8,  # air_powerup
    0x1978D8,  # earth_powerup
]


def apply_other_popups_patch(xbe_data: bytearray) -> None:
    """Hide the remaining first-time / tutorial / milestone popups."""
    null_resource_keys(xbe_data, OTHER_POPUP_OFFSETS, "tutorial / pickup popups")


def _custom_apply(xbe_data: bytearray, **_params) -> None:
    apply_other_popups_patch(xbe_data)


FEATURE = register_feature(Feature(
    name="qol_other_popups",
    description=(
        "Hides first-time tutorial, key, health, and power-up popups "
        "(death-screen popup is left alone)."
    ),
    sites=[],
    apply=apply_other_popups_patch,
    default_on=False,
    included_in_randomizer_qol=False,
    category="qol",
    tags=(),
    extra_whitelist_ranges=tuple((off, off + 1) for off in OTHER_POPUP_OFFSETS),
    custom_apply=_custom_apply,
))


__all__ = [
    "FEATURE",
    "OTHER_POPUP_OFFSETS",
    "apply_other_popups_patch",
]
