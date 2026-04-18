"""Individual patch packs (one module per feature)."""

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
from azurik_mod.patches.qol import (
    QOL_PATCH_SITES,
    SKIP_LOGO_SPEC,
    SKIP_LOGO_TRAMPOLINE,
    apply_gem_popups_patch,
    apply_other_popups_patch,
    apply_pickup_anim_patch,
    apply_player_character_patch,
    apply_qol_patches,
    apply_skip_logo_patch,
)

__all__ = [
    "FPS_DATA_PATCHED_VAS",
    "FPS_PATCH_SITES",
    "FPS_SAFETY_CRITICAL_SITES",
    "GRAVITY_PATCH",
    "PLAYER_PHYSICS_SITES",
    "QOL_PATCH_SITES",
    "RUN_SPEED_SCALE",
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
