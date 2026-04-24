"""air_shield_flaps — three-slider Quick-Stats pack for air-shield wing flaps.

Sets the number of wing flaps granted by each of the three air-
shield tiers (Air Shield 1, 2, 3) via ``config.xbr``.  All three
sliders live in a single :class:`Feature` so the Player-tab Quick
Stats sub-group shows them as one grouped widget.

**Target cell.**  ``armor_properties_real.air_shield_N.Flaps``
(TOC entry 0 @ ``0x002000`` in ``config.xbr``).  Historically this
section was labelled ``armor_hit_fx`` (its TOC tag) even though the
engine-read armor grid sits a few bytes into that entry at
``0x003000``.  ``docs/LEARNINGS.md`` § "XBR armor table aliasing"
has the RE trail; the rename to ``armor_properties_real`` landed in
``azurik_mod/xbr/sections.py`` alongside this pack.

**Why a single Feature with three sliders?**  The user chose the
``one_pack_three_sliders`` shape in the planning questionnaire so
flipping the Feature off zeroes out all three overrides in one
click and so enabling/disabling the whole bundle is one checkbox in
the Player tab.  Individual slider values still persist
independently in GUI state.

Vanilla defaults (read from the shipping ``config.xbr`` on
2026-04-21 during pack authoring):

========== =========
Shield     Flaps
========== =========
air_shield_1   1.0
air_shield_2   2.0
air_shield_3   5.0
========== =========

The slider range ``[0, 20]`` is intentionally conservative; the
cell is a ``double`` so larger values work, but > 20 makes the
power-up visually absurd.  Stick to whole numbers — the engine
casts the read value to an int on use.
"""

from __future__ import annotations

from azurik_mod.patching.registry import Feature, register_feature
from azurik_mod.patching.xbr_spec import XbrParametricEdit

_COMMON = {
    "xbr_file": "config.xbr",
    "section": "armor_properties_real",
    "prop": "Flaps",
    "slider_min": 0.0,
    "slider_max": 20.0,
    "slider_step": 1.0,
    "unit": "flaps",
}


AIR_SHIELD_1_FLAPS = XbrParametricEdit(
    name="air_shield_1_flaps",
    label="Air Shield 1 flaps",
    entity="air_shield_1",
    default=1.0,
    description=(
        "Number of wing flaps granted by Air Shield 1 (vanilla: "
        "1).  Writes config.xbr "
        "armor_properties_real.air_shield_1.Flaps."
    ),
    **_COMMON,
)

AIR_SHIELD_2_FLAPS = XbrParametricEdit(
    name="air_shield_2_flaps",
    label="Air Shield 2 flaps",
    entity="air_shield_2",
    default=2.0,
    description=(
        "Number of wing flaps granted by Air Shield 2 (vanilla: "
        "2).  Writes config.xbr "
        "armor_properties_real.air_shield_2.Flaps."
    ),
    **_COMMON,
)

AIR_SHIELD_3_FLAPS = XbrParametricEdit(
    name="air_shield_3_flaps",
    label="Air Shield 3 flaps",
    entity="air_shield_3",
    default=5.0,
    description=(
        "Number of wing flaps granted by Air Shield 3 (vanilla: "
        "5).  Writes config.xbr "
        "armor_properties_real.air_shield_3.Flaps."
    ),
    **_COMMON,
)


FEATURE = register_feature(Feature(
    name="air_shield_flaps",
    description=(
        "Set the number of wing flaps granted by each air-shield "
        "tier (1, 2, 3).  Three sliders bundled as one pack; all "
        "write to config.xbr armor_properties_real.air_shield_N."
        "Flaps — the engine-read armor grid."
    ),
    sites=[],
    apply=lambda xbe_data, **kw: None,
    xbr_sites=(
        AIR_SHIELD_1_FLAPS,
        AIR_SHIELD_2_FLAPS,
        AIR_SHIELD_3_FLAPS,
    ),
    default_on=False,
    included_in_randomizer_qol=False,
    category="player",
    subgroup="quick_stats",
    tags=("xbr",),
))


__all__ = [
    "AIR_SHIELD_1_FLAPS",
    "AIR_SHIELD_2_FLAPS",
    "AIR_SHIELD_3_FLAPS",
    "FEATURE",
]
