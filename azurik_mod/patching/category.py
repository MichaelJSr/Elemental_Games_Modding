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
    """Testing hook — wipe the registry and re-seed with builtins."""
    _REGISTRY.clear()
    _register_builtin_categories()


def _register_builtin_categories() -> None:
    """Idempotently register every category in :data:`_BUILTIN_CATEGORIES`."""
    for cat in _BUILTIN_CATEGORIES:
        _REGISTRY[cat.id] = cat


# Seed the registry with the builtins at import time so every
# downstream consumer sees them without needing to call anything.
_register_builtin_categories()


__all__ = [
    "Category",
    "all_categories",
    "clear_registry_for_tests",
    "ensure_category",
    "get_category",
    "register_category",
]
