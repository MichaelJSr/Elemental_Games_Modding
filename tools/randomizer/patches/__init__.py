"""XBE binary patches for Azurik: Rise of Perathia."""

from patches.xbe_utils import (
    XBE_SECTIONS,
    PatchSpec,
    apply_patch_spec,
    apply_xbe_patch,
    va_to_file,
    verify_patch_spec,
)
from patches.fps_unlock import (
    FPS_DATA_PATCHED_VAS,
    FPS_PATCH_SITES,
    FPS_SAFETY_CRITICAL_SITES,
    apply_fps_patches,
)
from patches.qol_patches import apply_qol_patches, apply_player_character_patch

__all__ = [
    "FPS_DATA_PATCHED_VAS",
    "FPS_PATCH_SITES",
    "FPS_SAFETY_CRITICAL_SITES",
    "PatchSpec",
    "XBE_SECTIONS",
    "apply_fps_patches",
    "apply_patch_spec",
    "apply_player_character_patch",
    "apply_qol_patches",
    "apply_xbe_patch",
    "va_to_file",
    "verify_patch_spec",
]
