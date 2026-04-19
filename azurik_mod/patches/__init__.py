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
    AIR_CONTROL_SCALE,
    FLAP_HEIGHT_SCALE,
    GRAVITY_PATCH,
    JUMP_SPEED_SCALE,
    PLAYER_PHYSICS_SITES,
    ROLL_SPEED_SCALE,
    RUN_SPEED_SCALE,           # back-compat alias -> ROLL_SPEED_SCALE
    SWIM_SPEED_SCALE,
    WALK_SPEED_SCALE,
    apply_air_control_speed,
    apply_flap_height,
    apply_jump_speed,
    apply_player_physics,
    apply_player_speed,
    apply_swim_speed,
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
from azurik_mod.patches.qol_skip_save_signature import (
    ALWAYS_ACCEPT_SIG_SPEC,
    AZURIK_VERIFY_SAVE_SIG_VA,
    SKIP_SAVE_SIG_SITES,
    apply_skip_save_signature_patch,
)
from azurik_mod.patches.enable_dev_menu import (
    DEV_MENU_SITES,
    apply_enable_dev_menu_patch,
)

# ``randomize`` has no byte patches — it surfaces the randomizer
# shuffle pools as ``Feature(category="randomize")`` entries so
# the category-aware GUI + CLI can treat them uniformly with the
# patch packs.  Importing the module triggers its
# ``register_feature(...)`` side effects.
from azurik_mod.patches.randomize import RANDOMIZER_POOLS  # noqa: F401

# Non-pack helpers used by the CLI (not part of the pack registry).
from azurik_mod.patches._player_character import apply_player_character_patch

# Back-compat: the old ``azurik_mod.patches.qol`` module re-exports
# everything above; importing it here keeps the grouped-QoL dispatcher
# (``apply_qol_patches``) wired for pre-reorganisation CLI callers.
from azurik_mod.patches.qol import (
    QOL_PATCH_SITES,
    apply_qol_patches,
)


# Third-party plugin discovery — happens AFTER every shipped
# feature has registered so plugin collisions fail loud on their
# specific (colliding) name rather than on an arbitrary earlier
# import.  Broken plugins are caught individually; one bad
# plugin never takes down azurik-mod.
#
# Set ``AZURIK_NO_PLUGINS=1`` in the environment to skip this
# entirely (useful for CI parity against vanilla installs).
import os as _os
if not _os.environ.get("AZURIK_NO_PLUGINS"):
    try:
        from azurik_mod.plugins import load_plugins as _load_plugins
        _load_plugins()  # best-effort; errors logged inside
    except Exception:  # noqa: BLE001
        # Plugins are optional by design — if the loader itself
        # blows up we keep going with just the shipped features.
        pass


__all__ = [
    "ALWAYS_ACCEPT_SIG_SPEC",
    "AZURIK_VERIFY_SAVE_SIG_VA",
    "FPS_DATA_PATCHED_VAS",
    "FPS_PATCH_SITES",
    "FPS_SAFETY_CRITICAL_SITES",
    "GEM_POPUP_OFFSETS",
    "GRAVITY_PATCH",
    "OTHER_POPUP_OFFSETS",
    "PICKUP_ANIM_SPEC",
    "AIR_CONTROL_SCALE",
    "FLAP_HEIGHT_SCALE",
    "JUMP_SPEED_SCALE",
    "PLAYER_PHYSICS_SITES",
    "QOL_PATCH_SITES",
    "ROLL_SPEED_SCALE",
    "RUN_SPEED_SCALE",
    "SKIP_LOGO_LEGACY_SPEC",
    "SKIP_LOGO_SPEC",
    "SKIP_LOGO_TRAMPOLINE",
    "SKIP_SAVE_SIG_SITES",
    "SWIM_SPEED_SCALE",
    "WALK_SPEED_SCALE",
    "apply_air_control_speed",
    "apply_flap_height",
    "apply_fps_patches",
    "apply_gem_popups_patch",
    "apply_jump_speed",
    "apply_other_popups_patch",
    "apply_pickup_anim_patch",
    "apply_player_character_patch",
    "apply_player_physics",
    "apply_player_speed",
    "apply_qol_patches",
    "apply_skip_logo_patch",
    "apply_skip_save_signature_patch",
    "apply_swim_speed",
]
