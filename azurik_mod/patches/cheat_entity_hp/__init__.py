"""cheat_entity_hp — scale the player entity's hitPoints in config.xbr.

Reference implementation for the Phase 3 XBR pack infrastructure.
Demonstrates the declarative ``xbr_sites`` path end-to-end:

1. An :class:`XbrParametricEdit` declares a slider that edits
   ``critters_critter_data.garret4.hitPoints`` inside
   ``config.xbr``.
2. :func:`apply_pack` dispatches the edit via
   :func:`apply_xbr_parametric_edit` against the XBR staging
   cache during ISO build.
3. The ISO's ``gamedata/config.xbr`` is rewritten in place; no
   :class:`~azurik_mod.patching.spec.PatchSpec` or custom_apply
   needed.

Authoring pattern other XBR-side features can follow:

- Declare each edit as an :class:`XbrEditSpec`
  (:class:`XbrParametricEdit` for sliders).
- Leave ``sites=[]``, ``apply=lambda *_: None`` — the dispatcher
  does all the work.
- Add the filename(s) being edited to ``xbr_sites``; the ISO
  build pipeline handles load/flush automatically.
"""

from __future__ import annotations

from azurik_mod.patching.registry import Feature, register_feature
from azurik_mod.patching.xbr_spec import XbrParametricEdit

GARRET4_HP_SLIDER = XbrParametricEdit(
    name="garret4_hit_points",
    label="Garret4 hit points",
    xbr_file="config.xbr",
    section="critters_critter_data",
    entity="garret4",
    prop="hitPoints",
    default=100.0,
    slider_min=1.0,
    slider_max=9999.0,
    slider_step=1.0,
    unit="HP",
    description=(
        "Scales the player character's starting hit points.  Writes "
        "to config.xbr critters_critter_data.garret4.hitPoints; no "
        "XBE changes required.  Reference implementation for the "
        "declarative XBR pack API (docs/XBR_PACKS.md)."
    ),
)


FEATURE = register_feature(Feature(
    name="cheat_entity_hp",
    description=(
        "Adjust the player entity's hit points via a config.xbr "
        "slider.  Demonstrates the declarative XBR pack API."
    ),
    sites=[],
    apply=lambda xbe_data, **kw: None,
    xbr_sites=(GARRET4_HP_SLIDER,),
    default_on=False,
    included_in_randomizer_qol=False,
    category="player",
    tags=("cheat", "xbr"),
))


__all__ = ["FEATURE", "GARRET4_HP_SLIDER"]
