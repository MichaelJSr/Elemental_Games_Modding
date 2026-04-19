"""Back-compat re-export module for the legacy ``azurik_mod.patches.qol``.

Pre-reorganisation, every QoL feature lived here as one big module.
Each feature now has its own folder
(``qol_gem_popups/``, ``qol_other_popups/``, ``qol_pickup_anims/``,
``qol_skip_logo/``) and this file is just an alias so older imports —

    from azurik_mod.patches.qol import apply_skip_logo_patch

— keep resolving.  New code should import from the specific feature
folder directly.

The ``apply_qol_patches`` dispatcher is preserved here as well for
CLI back-compat (the argparse-namespace-consuming path).
"""

from __future__ import annotations

# Re-export every public symbol each feature folder ships, so existing
# `from azurik_mod.patches.qol import X` imports resolve.
from azurik_mod.patches._player_character import (
    PLAYER_CHAR_MAX_LEN,
    PLAYER_CHAR_OFFSET,
    PLAYER_CHAR_ORIGINAL,
    apply_player_character_patch,
)
from azurik_mod.patches.qol_gem_popups import (
    GEM_POPUP_OFFSETS,
    apply_gem_popups_patch,
)
from azurik_mod.patches.qol_other_popups import (
    OTHER_POPUP_OFFSETS,
    apply_other_popups_patch,
)
from azurik_mod.patches.qol_pickup_anims import (
    PICKUP_ANIM_SPEC,
    apply_pickup_anim_patch,
)
from azurik_mod.patches.qol_skip_logo import (
    SKIP_LOGO_LEGACY_SPEC,
    SKIP_LOGO_SPEC,
    SKIP_LOGO_TRAMPOLINE,
    apply_skip_logo_patch,
)

# ``QOL_PATCH_SITES`` was a legacy iteration helper — kept as the
# single PatchSpec that used to live here.
QOL_PATCH_SITES = [PICKUP_ANIM_SPEC]


def apply_qol_patches(xbe_data: bytearray, args) -> None:
    """Back-compat dispatcher for callers that still pass an argparse namespace.

    Walks the opt-in / opt-out flag surface the pre-reorganisation
    ``qol.py`` exposed and routes each enabled pack through its new
    folder-based apply function.

    Flag precedence (first match wins per pack):

    1. ``--gem-popups`` / ``--other-popups`` / ``--pickup-anims`` /
       ``--skip-logo``  — opt-in.
    2. ``--no-gem-popups`` / ``--no-other-popups`` / ``--no-pickup-anim``
       / ``--no-skip-logo``  — explicit opt-out.
    3. ``--no-qol``  — grouped opt-out (disables every QoL pack).

    New code should call :func:`azurik_mod.patching.apply.apply_pack`
    on the registered features instead.
    """
    group_off = bool(getattr(args, "no_qol", False))

    if getattr(args, "gem_popups", False):
        apply_gem_popups_patch(xbe_data)
    elif not group_off and not getattr(args, "no_gem_popups", False):
        pass

    if getattr(args, "other_popups", False):
        apply_other_popups_patch(xbe_data)
    elif not group_off and not getattr(args, "no_other_popups", False):
        pass

    if getattr(args, "pickup_anims", False):
        apply_pickup_anim_patch(xbe_data)
    elif not group_off and not getattr(args, "no_pickup_anim", False):
        pass

    if getattr(args, "skip_logo", False):
        apply_skip_logo_patch(xbe_data)
    elif not group_off and not getattr(args, "no_skip_logo", False):
        pass

    player_char = getattr(args, "player_character", None)
    if player_char:
        apply_player_character_patch(xbe_data, player_char)


__all__ = [
    "GEM_POPUP_OFFSETS",
    "OTHER_POPUP_OFFSETS",
    "PICKUP_ANIM_SPEC",
    "PLAYER_CHAR_MAX_LEN",
    "PLAYER_CHAR_OFFSET",
    "PLAYER_CHAR_ORIGINAL",
    "QOL_PATCH_SITES",
    "SKIP_LOGO_LEGACY_SPEC",
    "SKIP_LOGO_SPEC",
    "SKIP_LOGO_TRAMPOLINE",
    "apply_gem_popups_patch",
    "apply_other_popups_patch",
    "apply_pickup_anim_patch",
    "apply_player_character_patch",
    "apply_qol_patches",
    "apply_skip_logo_patch",
]
