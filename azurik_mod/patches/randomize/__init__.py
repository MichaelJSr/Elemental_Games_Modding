"""Randomizer "pool" features — one Feature per shuffle pool.

These features are toggles, not patches — they have no XBE byte
sites and no ``apply`` body.  They exist purely so the GUI's
category system can surface randomisation options as a
first-class "Randomize" tab alongside Performance / Player /
Boot / QoL.

Why do this instead of leaving the toggles as hand-coded
checkboxes on the Randomize page?  Three reasons:

1. **Single source of truth.** Every pack toggle in the app now
   mirrors into ``AppState.enabled_packs``.  Before this, the
   randomize pools lived in a separate ``RandomizerConfig``
   dataclass + hand-wired ``trace_add`` callbacks.  Unifying
   under the Feature registry removes the parallel state.
2. **Automatic surfacing in the Patches page.**  The category
   system's ``PackBrowser`` renders one tab per non-empty
   category.  The moment these features register a new
   ``"randomize"`` category is populated, the Patches page grows
   a "Randomize" tab with zero GUI-code changes.
3. **Plugin hook for future shufflers.**  Third-party packs can
   now drop an additional randomizer pool by declaring a
   ``Feature(..., category="randomize")`` — no hard-coded
   dataclass field to extend.

The ``apply`` body is a no-op because the actual shuffle work
runs inside ``cmd_randomize_full`` (CLI) or
``gui.backend.run_randomizer`` (GUI builder).  Those entry
points consume the ``enabled_packs`` flags and run the
corresponding shuffler; see ``gui/pages/build.py`` +
``azurik_mod/randomizer/commands.py`` for the full pipeline.

Feature names map 1:1 to the legacy ``RandomizerConfig``
booleans so the Randomize page can continue to mirror them
without a rename:

    ``rand_major``       ↔  ``randomize_config.do_major``
    ``rand_keys``        ↔  ``randomize_config.do_keys``
    ``rand_gems``        ↔  ``randomize_config.do_gems``
    ``rand_barriers``    ↔  ``randomize_config.do_barriers``
    ``rand_connections`` ↔  ``randomize_config.do_connections``

See ``gui/pages/randomize.py`` for the bidirectional sync
+ seed / advanced-options handling (still page-local because
they don't fit the ``Feature`` model).
"""

from __future__ import annotations

from azurik_mod.patching.registry import Feature, register_feature


def _noop_apply(xbe_data, **_params) -> None:
    """No-op — the randomizer runs outside the XBE byte-patch pipeline."""


# Map of (feature_name, description) — declared here so tests can
# pin the exhaustive set.  Adding a new pool: append to this list
# AND extend the Randomize page's sync map in gui/pages/randomize.py.
RANDOMIZER_POOLS: tuple[tuple[str, str], ...] = (
    ("rand_major",
     "Shuffle major items: fragments, elemental powers, and "
     "obsidian barriers.  Affects which level contains which "
     "progression gate."),
    ("rand_keys",
     "Shuffle keys within each elemental realm (air / water / "
     "fire / earth / death).  Keeps dungeon structure intact."),
    ("rand_gems",
     "Randomize gem pickups across every level — swaps colours + "
     "counts while preserving total gem economy."),
    ("rand_barriers",
     "Randomize which element each barrier is vulnerable to.  "
     "Combine with major-item shuffle for maximum chaos."),
    ("rand_connections",
     "Reshuffle portal destinations between levels.  MAY PRODUCE "
     "UNSOLVABLE SEEDS — the solver validates every seed before "
     "building and aborts unsolvable runs.  Opt-in only."),
)


for _name, _desc in RANDOMIZER_POOLS:
    register_feature(Feature(
        name=_name,
        description=_desc,
        sites=[],
        apply=_noop_apply,
        default_on=False,
        included_in_randomizer_qol=False,
        category="randomize",
        tags=(),
    ))


__all__ = ["RANDOMIZER_POOLS"]
