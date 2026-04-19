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
