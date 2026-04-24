"""Patch categories — extensible grouping for the GUI + CLI.

Every :class:`azurik_mod.patching.registry.PatchPack` (a.k.a. ``Feature``)
declares which **category** it belongs to via its ``category`` field.
Categories drive three consumers:

1. **GUI** — the Patches page renders one notebook tab per category
   (see ``gui/pages/patches.py``).  Tabs appear in ``Category.order``
   order and are labelled with ``Category.title``.
2. **CLI** — the ``azurik-mod list-categories`` helper prints the
   full set for scripting / help text.
3. **Docs** — generators (e.g. ``docs/PATCHES.md``) group packs by
   category for authoring-friendly navigation.

## Making a new category

The easy path: just set ``category="my_new_name"`` on your
``Feature(...)`` declaration.  If no category with that id exists
yet the registry auto-creates one with sensible defaults.  The tab
title defaults to the id title-cased (``"my_new_name"`` →
``"My New Name"``).

The explicit path: call :func:`register_category` BEFORE your
feature module imports, passing a fully-populated
:class:`Category` with a nice title + description + sort order.
This is what ``_register_builtin_categories()`` does for the four
shipped categories.

Example:

.. code-block:: python

    from azurik_mod.patching.category import Category, register_category

    register_category(Category(
        id="experimental",
        title="Experimental",
        description="Work-in-progress mods that may crash or break saves.",
        order=900,  # near the end of the tab strip
    ))

## Why a separate module

We isolate category registration from the feature registry because
the registries have different invariants:

- A *category* can exist without any features (e.g. declared by a
  plugin that hasn't loaded its packs yet).
- A *feature* always needs a category (falls back to ``"other"``).
- Categories can be re-registered from multiple modules as long as
  the ``id``/``title`` match — duplicate ids with conflicting
  metadata raise.  This lets the builtin set and a plugin both
  declare the ``qol`` category without either winning.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    """Authoring-time description of a patch category.

    Attributes
    ----------
    id: str
        Stable identifier.  Use lowercase-underscore (e.g. ``"player"``
        or ``"boot_intro"``).  This is the value feature modules set
        on ``Feature(category=...)``.
    title: str
        Human-readable label for the GUI tab.  Should be short
        (≤ 20 chars) and title-cased (e.g. ``"Quality of Life"``).
    description: str
        One-sentence description shown above the tab's pack list.
        Explains what kinds of mods live here.
    order: int
        Sort key for tabs.  Lower values appear first.  Builtin
        categories use 10/20/30/40/50; plugins should pick from
        100+ to avoid colliding with future builtins.
    """

    id: str
    title: str
    description: str
    order: int = 1000


# ---------------------------------------------------------------------------
# Builtin catalogue
# ---------------------------------------------------------------------------

# Shipped categories in order of how "fundamental" they are to the
# player's experience.  The order values leave room for intermediate
# categories without renumbering (10/20/30/40/50 → plenty of gaps).
_BUILTIN_CATEGORIES: tuple[Category, ...] = (
    Category(
        id="performance",
        title="Performance",
        description=("Frame-rate, GPU, and rendering tweaks.  "
                     "These change how the engine runs rather than how the "
                     "game plays."),
        order=10,
    ),
    Category(
        id="player",
        title="Player",
        description=("Player-character movement, physics, and combat stats.  "
                     "Affects how the protagonist moves and feels."),
        order=20,
    ),
    # NOTE: subgroups for the "player" tab are declared below in
    # _BUILTIN_SUBGROUPS — keep the ``category_id`` there in sync if
    # this tab moves or gets renamed.
    Category(
        id="boot",
        title="Boot / Intro",
        description=("Skip boot-time cutscenes, logos, and intro sequences "
                     "to reach the title screen faster."),
        order=30,
    ),
    Category(
        id="qol",
        title="Quality of Life",
        description=("In-game UX improvements — popup suppression, faster "
                     "pickup animations, and other pacing tweaks."),
        order=40,
    ),
    Category(
        id="randomize",
        title="Randomize",
        description=("Randomization passes that reshuffle game content — "
                     "gems, power-ups, and (optionally) level connections. "
                     "Pair with a seed on the Randomize page for "
                     "reproducible runs."),
        order=50,
    ),
    Category(
        id="experimental",
        title="Experimental",
        description=("Opt-in patches that may destabilise the game, break "
                     "saves, or interact badly with xemu.  Use at your own "
                     "risk and keep a backup ISO handy."),
        order=80,
    ),
    Category(
        id="other",
        title="Other",
        description=("Uncategorised patches — packs that haven't picked a "
                     "dedicated home yet."),
        order=9999,
    ),
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, Category] = {}


def register_category(cat: Category) -> Category:
    """Register ``cat`` globally.

    Re-registering the same id is idempotent if the Category values
    match bit-for-bit (so the builtin catalogue can be re-seeded
    safely across test runs).  Conflicting re-registrations raise
    :exc:`ValueError` with both values in the message — this catches
    two plugins both owning the same category id with different
    metadata.
    """
    existing = _REGISTRY.get(cat.id)
    if existing is not None and existing != cat:
        raise ValueError(
            f"Category id {cat.id!r} already registered with different "
            f"metadata.\n  existing: {existing}\n  new:      {cat}")
    _REGISTRY[cat.id] = cat
    return cat


def ensure_category(category_id: str) -> Category:
    """Return the Category for ``category_id``, auto-creating a
    placeholder if it's not yet registered.

    Use this from the feature registry when a pack declares a
    category that hasn't been explicitly registered.  The auto-
    generated Category has:

    - ``title`` derived from the id (underscores → spaces, title
      case)
    - empty description (users can override later by registering
      explicitly before the first feature loads)
    - ``order=1000`` so auto-created categories appear after every
      builtin but before very-late plugin categories.
    """
    existing = _REGISTRY.get(category_id)
    if existing is not None:
        return existing
    title = category_id.replace("_", " ").title()
    placeholder = Category(
        id=category_id,
        title=title,
        description="",
        order=1000,
    )
    _REGISTRY[category_id] = placeholder
    return placeholder


def get_category(category_id: str) -> Category:
    """Return the Category for ``category_id``.

    Unlike :func:`ensure_category` this raises :exc:`KeyError` when
    the id isn't registered.  Use this from read-only consumers
    (GUI render / docs generator) where an unknown id indicates a
    bug, not a first-seen category.
    """
    return _REGISTRY[category_id]


def all_categories() -> list[Category]:
    """Return every registered category, sorted by ``order`` then ``id``.

    Used by the GUI to lay out the Patches page's tab strip and by
    the docs generator to iterate sections of ``PATCHES.md``.
    """
    return sorted(_REGISTRY.values(), key=lambda c: (c.order, c.id))


def clear_registry_for_tests() -> None:
    """Testing hook — wipe both the Category AND Subgroup registries
    and re-seed them with their respective builtins."""
    _REGISTRY.clear()
    _register_builtin_categories()
    # Subgroup registry lives further down the file but must share a
    # reset cycle so tests that touch features get a clean slate for
    # both tables in one call.
    try:
        _SUBGROUP_REGISTRY.clear()
        _register_builtin_subgroups()
    except NameError:
        # Subgroup registry not defined yet during early import —
        # the subgroup block below will seed it on first load.
        pass


def _register_builtin_categories() -> None:
    """Idempotently register every category in :data:`_BUILTIN_CATEGORIES`."""
    for cat in _BUILTIN_CATEGORIES:
        _REGISTRY[cat.id] = cat


# Seed the registry with the builtins at import time so every
# downstream consumer sees them without needing to call anything.
_register_builtin_categories()


# ===========================================================================
# Subgroups — second-level grouping *within* a category tab
# ===========================================================================
#
# Where a :class:`Category` becomes one notebook tab in the Patches
# page, a :class:`Subgroup` becomes one labelled frame **inside** that
# tab.  This lets us cluster related quick-edit sliders (``max HP``,
# ``air-shield flaps``) at the top of the Player tab without bolting
# on a whole new category for every cluster.
#
# Authoring: set ``Feature(..., subgroup="quick_stats")`` on each
# feature that belongs to the same bucket.  If no :class:`Subgroup`
# with that id is registered yet the registry auto-creates a
# placeholder on first use (same escape hatch as
# :func:`ensure_category`).
#
# Packs with ``subgroup=None`` (the default) render directly in the
# tab body below every subgroup, preserving pre-subgroup behaviour.


@dataclass(frozen=True)
class Subgroup:
    """Authoring-time description of a second-level grouping inside a tab.

    Attributes
    ----------
    id: str
        Stable identifier.  Use lowercase-underscore (e.g.
        ``"quick_stats"``).  This is the value feature modules set on
        ``Feature(subgroup=...)``.
    category_id: str
        The :class:`Category` this subgroup lives inside.  Must match
        the ``category`` of every feature that uses it — the GUI
        silently ignores cross-category misfires (e.g. a feature in
        ``player`` declaring ``subgroup="boot_stuff"`` where
        ``boot_stuff`` is under the ``boot`` category).  Pinning
        ``category_id`` here lets the Patches page render subgroups
        at the top of the right tab with zero ambiguity.
    title: str
        Human-readable label for the ``ttk.LabelFrame`` header.
        Keep it short (~20 chars, title-cased).  Example:
        ``"Quick Stats"``.
    description: str
        One-sentence hint rendered under the LabelFrame title.
        Leave empty if the title is self-explanatory.
    order: int
        Sort key for subgroups within the tab.  Lower values appear
        first.  Builtin subgroups use 10/20/30; plugin-owned groups
        should pick 100+ to avoid colliding with future builtins.
    """

    id: str
    category_id: str
    title: str
    description: str = ""
    order: int = 1000


_BUILTIN_SUBGROUPS: tuple[Subgroup, ...] = (
    Subgroup(
        id="quick_stats",
        category_id="player",
        title="Quick Stats",
        description=("One-click sliders for the commonly-tuned gameplay "
                     "stats (max HP, air-shield flaps, ...).  Each edit "
                     "writes directly to config.xbr so no XBE changes "
                     "are needed."),
        order=10,
    ),
)


_SUBGROUP_REGISTRY: dict[str, Subgroup] = {}


def register_subgroup(sub: Subgroup) -> Subgroup:
    """Register ``sub`` globally.

    Re-registering the same id is idempotent if the Subgroup values
    match bit-for-bit (so the builtin catalogue can be re-seeded
    safely across test runs).  Conflicting re-registrations raise
    :exc:`ValueError`.
    """
    existing = _SUBGROUP_REGISTRY.get(sub.id)
    if existing is not None and existing != sub:
        raise ValueError(
            f"Subgroup id {sub.id!r} already registered with different "
            f"metadata.\n  existing: {existing}\n  new:      {sub}")
    _SUBGROUP_REGISTRY[sub.id] = sub
    return sub


def ensure_subgroup(subgroup_id: str, category_id: str) -> Subgroup:
    """Return the Subgroup for ``subgroup_id``, auto-creating a
    placeholder under ``category_id`` if it's not yet registered.

    Matches :func:`ensure_category`'s behaviour: unknown ids get a
    title-cased placeholder and ``order=1000`` so auto-created groups
    always appear after every builtin subgroup but before
    very-late plugin-owned ones.
    """
    existing = _SUBGROUP_REGISTRY.get(subgroup_id)
    if existing is not None:
        return existing
    title = subgroup_id.replace("_", " ").title()
    placeholder = Subgroup(
        id=subgroup_id,
        category_id=category_id,
        title=title,
        description="",
        order=1000,
    )
    _SUBGROUP_REGISTRY[subgroup_id] = placeholder
    return placeholder


def get_subgroup(subgroup_id: str) -> Subgroup:
    """Return the Subgroup for ``subgroup_id`` or raise :exc:`KeyError`."""
    return _SUBGROUP_REGISTRY[subgroup_id]


def subgroups_for_category(category_id: str) -> list[Subgroup]:
    """Return every Subgroup attached to ``category_id``, sorted by
    ``order`` then ``id``.  Used by the Patches page to enumerate the
    LabelFrames it has to draw at the top of a tab.
    """
    return sorted(
        (s for s in _SUBGROUP_REGISTRY.values()
         if s.category_id == category_id),
        key=lambda s: (s.order, s.id),
    )


def all_subgroups() -> list[Subgroup]:
    """Return every registered subgroup, sorted (category_id, order, id)."""
    return sorted(
        _SUBGROUP_REGISTRY.values(),
        key=lambda s: (s.category_id, s.order, s.id),
    )


def _register_builtin_subgroups() -> None:
    """Idempotently register every subgroup in :data:`_BUILTIN_SUBGROUPS`."""
    for sub in _BUILTIN_SUBGROUPS:
        _SUBGROUP_REGISTRY[sub.id] = sub


# Seed at import time, same pattern as categories.
_register_builtin_subgroups()


__all__ = [
    "Category",
    "Subgroup",
    "all_categories",
    "all_subgroups",
    "clear_registry_for_tests",
    "ensure_category",
    "ensure_subgroup",
    "get_category",
    "get_subgroup",
    "register_category",
    "register_subgroup",
    "subgroups_for_category",
]
