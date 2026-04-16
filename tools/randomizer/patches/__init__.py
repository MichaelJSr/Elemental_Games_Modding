"""XBE binary patches for Azurik: Rise of Perathia."""

from patches.xbe_utils import va_to_file, apply_xbe_patch
from patches.fps_unlock import apply_fps_patches
from patches.qol_patches import apply_qol_patches, apply_player_character_patch

__all__ = [
    "va_to_file",
    "apply_xbe_patch",
    "apply_fps_patches",
    "apply_qol_patches",
    "apply_player_character_patch",
]
