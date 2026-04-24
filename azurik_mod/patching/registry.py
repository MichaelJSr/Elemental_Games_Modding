"""Central registry of available patch packs.

Every patch pack registers itself here so that the CLI's `verify-patches`
command, the GUI's Patches page, and any future patch browser can
iterate the full set of patches with zero manual upkeep.

A pack's `sites` list may hold a mix of `PatchSpec` (fixed byte swaps)
and `ParametricPatch` (slider-driven float rewrites), so helpers
`patch_specs()` / `parametric_sites()` are provided for callers that
need a typed view.

Register a pack via `register_pack(PatchPack(name=..., sites=[...],
apply=...))` from the pack module itself — see
`azurik_mod.patches.fps_unlock`, `azurik_mod.patches.qol`, and
`azurik_mod.patches.player_physics` for examples.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Union

from azurik_mod.patching.category import ensure_category, ensure_subgroup
from azurik_mod.patching.feature import ShimSource
from azurik_mod.patching.spec import (
    ParametricPatch,
    PatchSpec,
    TrampolinePatch,
)
from azurik_mod.patching.xbr_spec import (
    XbrEditSpec,
    XbrParametricEdit,
    XbrSite,
)

SiteType = Union[PatchSpec, ParametricPatch, TrampolinePatch]


@dataclass(frozen=True)
class PatchPack:
    """Metadata describing one patch pack."""

    name: str
    """Short identifier used in CLI flags and the GUI registry."""

    description: str
    """One-paragraph human-readable summary."""

    sites: list[SiteType]
    """Ordered sites that make up the pack (PatchSpec or ParametricPatch)."""

    apply: Callable[..., None]
    """Apply function.  Signature depends on the pack:
       - packs with only PatchSpec sites: `apply(xbe_data)`
       - packs with ParametricPatch sites: `apply(xbe_data, **params)` where
         params are keyword args keyed by ParametricPatch.name"""

    default_on: bool = False
    """Whether the pack should be enabled by default in the GUI."""

    included_in_randomizer_qol: bool = False
    """If True, `randomize-full --no-qol` disables this pack too."""

    category: str = "other"
    """Primary category — drives GUI tab grouping.  Declare new
    categories simply by using a fresh string here; the registry
    auto-creates a placeholder :class:`~azurik_mod.patching.category.Category`
    on first use.  For a nicer title/description/sort-order,
    register the category explicitly via
    :func:`azurik_mod.patching.category.register_category` before
    importing any feature that references it.

    Canonical builtin ids (each with a pre-registered Category):
    ``"performance"``, ``"player"``, ``"boot"``, ``"qol"``,
    ``"other"``.  See ``azurik_mod/patching/category.py``."""

    subgroup: str | None = None
    """Optional secondary grouping **within** a :attr:`category`.

    When set, the GUI's Patches page renders this feature inside a
    labelled box (headed by the Subgroup's title) at the top of the
    category tab, so related quick-edit sliders (e.g. ``player_max_hp``
    + ``air_shield_flaps`` under ``quick_stats``) live together
    rather than mixed in with the longer-form player-physics packs.

    Subgroup metadata (title, description, order-within-tab) lives
    in :mod:`azurik_mod.patching.category` alongside :class:`Category`.
    Like categories, a subgroup id that isn't pre-registered gets an
    auto-created placeholder.

    ``None`` (the default) means "render directly in the tab body",
    below any subgroups, matching pre-subgroup behaviour."""

    tags: tuple[str, ...] = field(default_factory=tuple)
    """Secondary free-form classifications surfaced in the GUI as
    badges (e.g. ``"c-shim"``, ``"experimental"``, ``"physics"``).

    Do NOT use ``tags`` to carry the primary category — set
    :attr:`category` instead.  ``tags`` is for additional
    metadata that doesn't fit the one-category-per-pack model."""

    extra_whitelist_ranges: tuple[tuple[int, int], ...] = field(default_factory=tuple)
    """Extra ``(lo, hi)`` file-offset half-open byte ranges this pack is
    allowed to touch at apply time, beyond the ranges implied by its
    ``PatchSpec`` / ``ParametricPatch`` sites.

    Packs that mutate bytes imperatively (e.g. the popup-suppression
    packs that null the first byte of a localisation resource key)
    declare their offsets here so ``verify-patches --strict`` doesn't
    flag those byte flips as unexpected.  Ranges are compared against
    the XBE's FILE offset space (not VA), matching the whitelist diff's
    coordinate system.  Callers typically spell them as
    ``tuple((off, off + 1) for off in OFFSETS)`` for single-byte nulls.
    """

    dynamic_whitelist_from_xbe: Callable[[bytes], list[tuple[int, int]]] | None = None
    """Optional callback that computes extra whitelist ranges from the
    patched XBE bytes themselves.

    Used by packs whose apply function emits patches at addresses it
    chose at apply time (e.g. the player-speed patch injects per-game
    floats into dynamically-allocated locations, then rewrites two
    instructions to reference them).  The callback is invoked by
    ``verify-patches --strict`` with the patched XBE as input and
    must return a list of ``(lo, hi)`` file-offset ranges covering
    every byte the apply function could have touched beyond the
    static ``extra_whitelist_ranges``.

    Keep the callback pure (no side effects, reads only) and robust
    against vanilla XBEs — it's called on EVERY pack for EVERY
    verify-patches invocation, regardless of whether the user opted
    into the pack or not.  If the pack's "has been applied?" check
    fails, return an empty list.
    """

    # --- Folder-per-feature authoring surface ---------------------------

    shim: ShimSource | None = None
    """Optional ``ShimSource`` pointing at this feature's C source and
    compiled object.  Set when the feature uses a trampoline; the
    :func:`apply_pack` dispatcher uses it to auto-compile the ``.o``
    on demand and to fill in any ``TrampolinePatch`` sites whose
    ``shim_object`` wasn't explicitly set.

    None for byte-only / parametric-only features."""

    legacy_sites: tuple[PatchSpec, ...] = field(default_factory=tuple)
    """Byte-patch fallbacks used when ``AZURIK_NO_SHIMS=1`` is set in
    the environment.  The dispatcher substitutes these in place of
    every :class:`TrampolinePatch` site, so hosts without a working
    i386 clang toolchain can still ship a patched XBE.

    Empty tuple for packs that have no shim to fall back from."""

    custom_apply: Callable[..., None] | None = None
    """Escape hatch for packs whose apply logic isn't expressible as
    "iterate sites, apply each".  When set, :func:`apply_pack`
    delegates the whole pack to this callable instead of running the
    generic dispatcher.

    Signature: ``custom_apply(xbe_data: bytearray, **params) -> None``.
    Use sparingly — every custom_apply is a special case that
    downstream tooling has to understand separately."""

    xbr_sites: tuple[XbrSite, ...] = field(default_factory=tuple)
    """Declarative XBR edits bundled with this feature.

    Mirror of :attr:`sites` but for ``.xbr`` data files (config.xbr,
    level XBRs).  Supported shapes:

    - :class:`~azurik_mod.patching.xbr_spec.XbrEditSpec` — fixed
      edit (set value / rewrite string / replace bytes).  Analogous
      to :class:`PatchSpec`.
    - :class:`~azurik_mod.patching.xbr_spec.XbrParametricEdit` —
      slider-driven numeric edit.  Analogous to
      :class:`ParametricPatch`.

    At ISO-build time, :func:`apply_pack` receives a dict of
    ``{xbr_filename: bytearray}`` and dispatches each xbr site
    against it via
    :func:`~azurik_mod.patching.xbr_spec.apply_xbr_edit_spec` /
    :func:`~azurik_mod.patching.xbr_spec.apply_xbr_parametric_edit`.

    Byte-only / XBE-only packs leave this empty — no build cost."""

    deprecated: bool = False
    """When True, the pack stays registered (so CLI + tests + direct
    ``apply_pack`` calls still work) but the GUI's Patches page hides
    it from the pack browser so casual users don't stumble into a
    checkbox that's known to not produce the expected effect.

    Use this — rather than deleting the pack — when a shim applies
    bytes correctly but in-game validation shows the hook doesn't
    achieve the intended gameplay effect, and we've decided to leave
    the code in tree as RE reference rather than rip it out.  See
    docs/LEARNINGS.md § "Deprecated physics packs" for the current
    entries."""

    unchecked_xbr_sites: bool = False
    """When True, suppress the registration-time schema lint against
    :data:`azurik_mod.config.schema`.  The lint warns once per
    ``(pack, section, prop)`` triple that isn't documented in the
    schema, since undocumented targets are the number-one cause of
    "edit lands on disk but nothing changes in-game" bugs (see the
    dead ``critters_critter_data.hitPoints`` case in
    ``docs/LEARNINGS.md``).

    Set to ``True`` for feature modules whose target cells are
    intentionally undocumented (experimental packs, plugins that
    know they're poking sections the upstream schema hasn't caught
    up to yet).  Leave the default ``False`` for every shipped
    pack so the registry stays a canary for schema drift."""

    def patch_specs(self) -> list[PatchSpec]:
        """Return only the PatchSpec entries in this pack."""
        return [s for s in self.sites if isinstance(s, PatchSpec)]

    def parametric_sites(self) -> list[ParametricPatch]:
        """Return only the ParametricPatch entries in this pack."""
        return [s for s in self.sites if isinstance(s, ParametricPatch)]

    def trampoline_sites(self) -> list[TrampolinePatch]:
        """Return only the TrampolinePatch entries in this pack."""
        return [s for s in self.sites if isinstance(s, TrampolinePatch)]

    def xbr_parametric_sites(self) -> list[XbrParametricEdit]:
        """Return only the XbrParametricEdit entries in ``xbr_sites``."""
        return [s for s in self.xbr_sites
                if isinstance(s, XbrParametricEdit)]

    def xbr_static_sites(self) -> list[XbrEditSpec]:
        """Return only the static XbrEditSpec entries in ``xbr_sites``."""
        return [s for s in self.xbr_sites
                if isinstance(s, XbrEditSpec)]

    def touched_xbr_files(self) -> tuple[str, ...]:
        """Filenames (``"config.xbr"``, ``"a1.xbr"``, …) this pack
        edits.  Deduped but declaration-order-preserving."""
        seen: set[str] = set()
        out: list[str] = []
        for site in self.xbr_sites:
            if site.xbr_file in seen:
                continue
            seen.add(site.xbr_file)
            out.append(site.xbr_file)
        return tuple(out)

    @property
    def parameters(self) -> tuple[str, ...]:
        """Names of every slider exposed by this pack (in declaration order).

        Includes both XBE-side ParametricPatch sliders and XBR-side
        XbrParametricEdit sliders — GUI consumers need the unified
        view to render one slider UI per parameter regardless of
        where the bytes land.
        """
        xbe = tuple(p.name for p in self.parametric_sites())
        xbr = tuple(p.name for p in self.xbr_parametric_sites())
        return xbe + xbr


#: Primary feature-authoring type — identical to :class:`PatchPack`.
#: New feature modules should use this name.  The old ``PatchPack``
#: spelling stays supported so existing code keeps working during the
#: folder-per-feature migration.
Feature = PatchPack


_REGISTRY: dict[str, PatchPack] = {}

#: Legacy pack-name aliases.  Maps old, removed pack identifiers to
#: their current names so CLI flags (``--enable-pack cheat_entity_hp``)
#: and saved GUI/script state keep resolving after a rename.  Each
#: hit warns once via :mod:`warnings` so scripts get a nudge to update.
#:
#: Add new entries when you rename a pack; keep the old name here
#: indefinitely — dropping an alias silently breaks third-party
#: scripts that pinned the old name.
_LEGACY_PACK_ALIASES: dict[str, str] = {
    "cheat_entity_hp": "player_max_hp",
}

_WARNED_LEGACY_ALIASES: set[str] = set()


def _resolve_legacy_alias(name: str) -> str:
    """Map a legacy pack name to its current name (warning once).

    Returns ``name`` unchanged when it isn't a known legacy alias.
    """
    new_name = _LEGACY_PACK_ALIASES.get(name)
    if new_name is None:
        return name
    if name not in _WARNED_LEGACY_ALIASES:
        import warnings
        warnings.warn(
            f"pack {name!r} was renamed to {new_name!r}; update your "
            f"scripts / saved state to stop seeing this warning",
            DeprecationWarning,
            stacklevel=3,
        )
        _WARNED_LEGACY_ALIASES.add(name)
    return new_name


def register_pack(pack: PatchPack) -> PatchPack:
    """Register a patch pack. Raises on duplicate names.

    Also auto-creates a placeholder
    :class:`~azurik_mod.patching.category.Category` for the pack's
    ``category`` id if no category with that id is registered yet
    (and, for packs that declare a ``subgroup``, a placeholder
    :class:`~azurik_mod.patching.category.Subgroup` scoped to that
    category), so feature authors can spin up new groupings simply
    by picking a fresh name.

    Packs that declare ``xbr_sites`` are lint-checked against
    :data:`azurik_mod.config.schema.json`: every ``(section, prop)``
    that isn't in the schema warns once.  Suppress via
    ``Feature(unchecked_xbr_sites=True)`` for intentional
    undocumented targets (experimental / plugin packs).
    """
    if pack.name in _REGISTRY:
        raise ValueError(f"Duplicate patch pack name: {pack.name!r}")
    ensure_category(pack.category)
    if pack.subgroup is not None:
        ensure_subgroup(pack.subgroup, pack.category)
    _REGISTRY[pack.name] = pack
    if pack.xbr_sites and not pack.unchecked_xbr_sites:
        _lint_xbr_sites_against_schema(pack)
    return pack


# ---- schema-lint plumbing --------------------------------------------
#
# Loaded lazily on first registration that needs it so ``import
# azurik_mod.patching.registry`` stays cheap.  The {(section, prop)}
# lookup set is rebuilt each test run via
# :func:`clear_registry_for_tests`.
#
# We cache misses per triple so large pack collections don't spam
# the same warning N times.

_SCHEMA_CELL_INDEX: frozenset[tuple[str, str]] | None = None
_WARNED_UNDOC_TRIPLES: set[tuple[str, str, str]] = set()


def _schema_cell_index() -> frozenset[tuple[str, str]]:
    """Return a cached ``{(section_name, prop_key)}`` set built from
    ``azurik_mod/config/schema.json``.

    Empty set when the schema file is missing or malformed — the
    lint silently no-ops in that case so a broken schema never
    blocks pack registration.
    """
    global _SCHEMA_CELL_INDEX
    if _SCHEMA_CELL_INDEX is not None:
        return _SCHEMA_CELL_INDEX

    import json
    from pathlib import Path

    schema_path = (Path(__file__).resolve().parent.parent
                   / "config" / "schema.json")
    try:
        raw = json.loads(schema_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        _SCHEMA_CELL_INDEX = frozenset()
        return _SCHEMA_CELL_INDEX

    pairs: set[tuple[str, str]] = set()
    for sect_name, sect in (raw.get("sections") or {}).items():
        if not isinstance(sect, dict):
            continue
        for prop in sect.get("properties") or ():
            if isinstance(prop, dict) and "key" in prop:
                pairs.add((sect_name, prop["key"]))
    _SCHEMA_CELL_INDEX = frozenset(pairs)
    return _SCHEMA_CELL_INDEX


def _lint_xbr_sites_against_schema(pack: PatchPack) -> None:
    """Warn once per undocumented ``(pack, section, prop)`` triple.

    No-ops for sites whose shape doesn't carry ``section`` / ``prop``
    (e.g. raw-bytes / string-replace edits).
    """
    import warnings

    index = _schema_cell_index()
    if not index:
        return
    for site in pack.xbr_sites:
        section = getattr(site, "section", None)
        prop = getattr(site, "prop", None)
        if not section or not prop:
            continue
        triple = (pack.name, section, prop)
        if triple in _WARNED_UNDOC_TRIPLES:
            continue
        if (section, prop) in index:
            continue
        _WARNED_UNDOC_TRIPLES.add(triple)
        warnings.warn(
            f"pack {pack.name!r}: xbr_site target {section!r}/"
            f"{prop!r} is not documented in "
            f"azurik_mod/config/schema.json — either add the cell "
            f"to schema.json or set Feature(unchecked_xbr_sites="
            f"True) to intentionally suppress this check.",
            UserWarning,
            stacklevel=4,
        )


def register_feature(feature: Feature) -> Feature:
    """Alias for :func:`register_pack` used by feature modules."""
    return register_pack(feature)


def get_pack(name: str) -> PatchPack:
    """Look up a pack by name.  Raises KeyError if missing.

    Legacy pack names (see ``_LEGACY_PACK_ALIASES``) are transparently
    remapped to their current names, with a one-shot
    :class:`DeprecationWarning` on first hit so existing
    ``--enable-pack <old_name>`` CLI flags and saved GUI state keep
    working across renames.
    """
    resolved = _resolve_legacy_alias(name)
    return _REGISTRY[resolved]


def all_packs() -> list[PatchPack]:
    """Return every registered pack, in registration order."""
    return list(_REGISTRY.values())


def packs_by_category() -> dict[str, list[PatchPack]]:
    """Return packs grouped by ``category`` id.

    The returned dict's iteration order matches
    :func:`~azurik_mod.patching.category.all_categories` (by
    ``Category.order`` then id), so consumers can render tab
    strips directly without an extra sort.  Categories that
    currently have zero registered packs are included with an
    empty list — the GUI can then decide whether to hide them.
    """
    from azurik_mod.patching.category import all_categories

    groups: dict[str, list[PatchPack]] = {
        cat.id: [] for cat in all_categories()}
    for pack in _REGISTRY.values():
        groups.setdefault(pack.category, []).append(pack)
    return groups


def all_sites() -> list[SiteType]:
    """Return every registered site (PatchSpec / ParametricPatch /
    TrampolinePatch), deduped by VA.  Virtual parametric sites
    (va=0) are included once in registration order."""
    seen: set[int] = set()
    out: list[SiteType] = []
    for pack in _REGISTRY.values():
        for site in pack.sites:
            va = getattr(site, "va", 0)
            key = id(site) if va == 0 else va
            if key in seen:
                continue
            seen.add(key)
            out.append(site)
    return out


def all_patch_specs() -> list[PatchSpec]:
    """Return every PatchSpec across every pack, deduped by VA."""
    seen: set[int] = set()
    out: list[PatchSpec] = []
    for pack in _REGISTRY.values():
        for site in pack.patch_specs():
            if site.va in seen:
                continue
            seen.add(site.va)
            out.append(site)
    return out


def all_parametric_sites() -> list[tuple[str, ParametricPatch]]:
    """Return (pack_name, ParametricPatch) for every registered slider."""
    out: list[tuple[str, ParametricPatch]] = []
    for pack in _REGISTRY.values():
        for site in pack.parametric_sites():
            out.append((pack.name, site))
    return out


def all_trampoline_sites() -> list[tuple[str, TrampolinePatch]]:
    """Return (pack_name, TrampolinePatch) for every registered shim site."""
    out: list[tuple[str, TrampolinePatch]] = []
    for pack in _REGISTRY.values():
        for site in pack.trampoline_sites():
            out.append((pack.name, site))
    return out


def clear_registry_for_tests() -> None:
    """Testing hook — wipe the global registry between runs."""
    global _SCHEMA_CELL_INDEX
    _REGISTRY.clear()
    _WARNED_LEGACY_ALIASES.clear()
    _WARNED_UNDOC_TRIPLES.clear()
    _SCHEMA_CELL_INDEX = None
