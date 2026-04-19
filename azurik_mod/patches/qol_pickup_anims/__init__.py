"""qol_pickup_anims — skip the post-pickup celebration animation.

Pure byte-patch feature: one ``PatchSpec`` at the start of the
non-gem pickup handler's "play celebration animation" block, replaced
with a 5-byte ``JMP rel32`` to the block's epilog.  The save-list
update and collection-flag writes still run, so picked-up items stay
collected and saves stay consistent.

No custom apply logic — :func:`apply_pack` walks ``sites`` directly.
"""

from __future__ import annotations

from azurik_mod.patching import PatchSpec, apply_patch_spec
from azurik_mod.patching.registry import Feature, register_feature


PICKUP_ANIM_SPEC = PatchSpec(
    label="Skip pickup celebration animation",
    va=0x00413EE,
    original=bytes([0x8B, 0x8A, 0xEC, 0x01, 0x00]),   # MOV ECX,[EDX+0x1EC]
    patch=bytes([0xE9, 0x7C, 0x00, 0x00, 0x00]),       # JMP 0x4146F (epilog)
)


def apply_pickup_anim_patch(xbe_data: bytearray) -> None:
    """Back-compat wrapper for callers that still invoke by name.

    The unified dispatcher (:func:`apply_pack`) runs the site directly,
    so this wrapper is no longer required for new code — it stays to
    keep pre-reorganisation imports (e.g. tests, ``cmd_randomize_full``)
    working.
    """
    apply_patch_spec(xbe_data, PICKUP_ANIM_SPEC)


FEATURE = register_feature(Feature(
    name="qol_pickup_anims",
    description="Skips the post-pickup celebration animation.",
    sites=[PICKUP_ANIM_SPEC],
    apply=apply_pickup_anim_patch,
    default_on=False,
    included_in_randomizer_qol=False,
    category="qol",
    tags=(),
))


__all__ = [
    "FEATURE",
    "PICKUP_ANIM_SPEC",
    "apply_pickup_anim_patch",
]
