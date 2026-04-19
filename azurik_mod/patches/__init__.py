"""Individual patch packs — one folder per feature.

Folder-per-feature layout: every toggleable mod lives in
``azurik_mod/patches/<name>/`` with its Python declaration in
``__init__.py`` and (if applicable) its shim C source alongside.
Simply importing this package runs every feature folder's
``register_feature(...)`` side effect so the registry is fully
populated before any caller asks for a pack.

Back-compat imports from the pre-reorganisation names
(``apply_fps_patches``, ``apply_player_physics``, ``apply_skip_logo_patch``,
etc.) are re-exported here and from ``azurik_mod.patches.qol`` for
external callers that already pinned those paths.
"""

# Feature-folder imports — each import registers the feature in the
# central registry as a side effect.
from azurik_mod.patches.fps_unlock import (
    FPS_DATA_PATCHED_VAS,
    FPS_PATCH_SITES,
    FPS_SAFETY_CRITICAL_SITES,
    apply_fps_patches,
)
from azurik_mod.patches.player_physics import (
    GRAVITY_PATCH,
    PLAYER_PHYSICS_SITES,
    RUN_SPEED_SCALE,
    WALK_SPEED_SCALE,
    apply_player_physics,
    apply_player_speed,
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

# Non-pack helpers used by the CLI (not part of the pack registry).
from azurik_mod.patches._player_character import apply_player_character_patch

# Back-compat: the old ``azurik_mod.patches.qol`` module re-exports
# everything above; importing it here keeps the grouped-QoL dispatcher
# (``apply_qol_patches``) wired for pre-reorganisation CLI callers.
from azurik_mod.patches.qol import (
    QOL_PATCH_SITES,
    apply_qol_patches,
)


__all__ = [
    "FPS_DATA_PATCHED_VAS",
    "FPS_PATCH_SITES",
    "FPS_SAFETY_CRITICAL_SITES",
    "GEM_POPUP_OFFSETS",
    "GRAVITY_PATCH",
    "OTHER_POPUP_OFFSETS",
    "PICKUP_ANIM_SPEC",
    "PLAYER_PHYSICS_SITES",
    "QOL_PATCH_SITES",
    "RUN_SPEED_SCALE",
    "SKIP_LOGO_LEGACY_SPEC",
    "SKIP_LOGO_SPEC",
    "SKIP_LOGO_TRAMPOLINE",
    "WALK_SPEED_SCALE",
    "apply_fps_patches",
    "apply_gem_popups_patch",
    "apply_other_popups_patch",
    "apply_pickup_anim_patch",
    "apply_player_character_patch",
    "apply_player_physics",
    "apply_player_speed",
    "apply_qol_patches",
    "apply_skip_logo_patch",
]
