"""player_max_hp — slider for the player character's maximum hit points.

Writes to ``config.xbr`` at ``critters_critter_data.garret4.hitPoints``.

**History.**  This pack used to be called ``cheat_entity_hp``; the name
was dropped because the slider is a legitimate stat knob, not a cheat.
The backwards-compat alias lives in
:func:`azurik_mod.patching.registry.get_pack` and the saved-state
migration lives in :func:`gui.backend.migrate_legacy_pack_keys`.

**Target cell reality check.**  The planning doc for this rename
claimed the engine-read HP lives in ``critters_damage.garret4.hitPoints``
(Ghidra ``FUN_00049480`` @ ``0x4a2dd`` / ``0x4a4b7``).  Inspection of
the shipping ``config.xbr`` shows that section has **no** ``hitPoints``
row and **no** ``garret4`` column — the cell simply doesn't exist on
disk, so writing there would raise :class:`XbrStructuralError` at
build time.  The only writable garret4/hitPoints cell in the whole
file is in ``critters_critter_data`` (TOC entry 4 @ ``0x01A000``),
which is what the legacy ``cheat_entity_hp`` pack already targeted.

Two interpretations of the Ghidra trace that are consistent with
that on-disk reality:

1. The resource name ``"config/critters_damage"`` inside the engine
   maps to TOC entry 4 (our Python label ``critters_critter_data``) —
   i.e. our section labels are historical guesses and don't match
   the name-chunk entries the engine actually indexes by.
2. ``FUN_00049480``'s ``hitPoints`` lookup runs against the
   ``critters_damage`` tabl, silently returns ``-1``
   (``config_name_lookup`` miss), and the engine falls back to a
   default via ``config_cell_value``.  Player HP is then initialised
   somewhere else entirely (save-game, entity spawn script, …).

Either way, the only cell we can meaningfully write is the one in
``critters_critter_data``.  See
``docs/LEARNINGS.md`` § "Dead critters_critter_data.hitPoints (2026-04)"
for the full RE trail and the reason the user reports this slider
as ineffective in practice.

Authoring pattern for other quick-stat packs:

- Declare each slider as an :class:`XbrParametricEdit`.
- Leave ``sites=[]`` / ``apply=lambda *_: None`` — the dispatcher
  handles everything for XBR-only packs.
- Set ``subgroup="quick_stats"`` to render inside the Player tab's
  Quick Stats LabelFrame alongside sibling packs.
"""

from __future__ import annotations

from azurik_mod.patching.registry import Feature, register_feature
from azurik_mod.patching.xbr_spec import XbrParametricEdit

PLAYER_MAX_HP_SLIDER = XbrParametricEdit(
    name="garret4_hit_points",
    label="Max HP (garret4)",
    xbr_file="config.xbr",
    # critters_critter_data.garret4.hitPoints is the only writable
    # garret4/hitPoints cell in config.xbr; the plan's
    # critters_damage target doesn't exist on disk.  See module
    # docstring for the full RE mismatch story.
    section="critters_critter_data",
    entity="garret4",
    prop="hitPoints",
    default=200.0,
    slider_min=1.0,
    slider_max=9999.0,
    slider_step=1.0,
    unit="HP",
    description=(
        "Player character's maximum hit points.  Writes to "
        "config.xbr critters_critter_data.garret4.hitPoints — the "
        "only writable garret4/hitPoints cell in the file.  See "
        "module docstring for the Ghidra-vs-disk mismatch story."
    ),
)


FEATURE = register_feature(Feature(
    name="player_max_hp",
    description=(
        "Set the player character's starting hit points via a "
        "config.xbr slider.  Previously shipped as "
        "``cheat_entity_hp`` (same underlying cell, new user-"
        "facing framing as a plain stat knob inside the Player "
        "tab's Quick Stats group)."
    ),
    sites=[],
    apply=lambda xbe_data, **kw: None,
    xbr_sites=(PLAYER_MAX_HP_SLIDER,),
    default_on=False,
    included_in_randomizer_qol=False,
    category="player",
    subgroup="quick_stats",
    tags=("xbr",),
))


__all__ = ["FEATURE", "PLAYER_MAX_HP_SLIDER"]
