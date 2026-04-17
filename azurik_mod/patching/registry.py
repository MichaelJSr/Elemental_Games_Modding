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

from azurik_mod.patching.spec import ParametricPatch, PatchSpec

SiteType = Union[PatchSpec, ParametricPatch]


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

    tags: tuple[str, ...] = field(default_factory=tuple)
    """Free-form tags like 'fps', 'qol', 'player', 'experimental'."""

    def patch_specs(self) -> list[PatchSpec]:
        """Return only the PatchSpec entries in this pack."""
        return [s for s in self.sites if isinstance(s, PatchSpec)]

    def parametric_sites(self) -> list[ParametricPatch]:
        """Return only the ParametricPatch entries in this pack."""
        return [s for s in self.sites if isinstance(s, ParametricPatch)]

    @property
    def parameters(self) -> tuple[str, ...]:
        """Names of every slider exposed by this pack (in declaration order)."""
        return tuple(p.name for p in self.parametric_sites())


_REGISTRY: dict[str, PatchPack] = {}


def register_pack(pack: PatchPack) -> PatchPack:
    """Register a patch pack. Raises on duplicate names."""
    if pack.name in _REGISTRY:
        raise ValueError(f"Duplicate patch pack name: {pack.name!r}")
    _REGISTRY[pack.name] = pack
    return pack


def get_pack(name: str) -> PatchPack:
    """Look up a pack by name. Raises KeyError if missing."""
    return _REGISTRY[name]


def all_packs() -> list[PatchPack]:
    """Return every registered pack, in registration order."""
    return list(_REGISTRY.values())


def all_sites() -> list[SiteType]:
    """Return every registered site (PatchSpec + non-virtual ParametricPatch),
    deduped by VA.  Virtual parametric sites (va=0) are included once in
    registration order."""
    seen: set[int] = set()
    out: list[SiteType] = []
    for pack in _REGISTRY.values():
        for site in pack.sites:
            va = site.va
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


def clear_registry_for_tests() -> None:
    """Testing hook — wipe the global registry between runs."""
    _REGISTRY.clear()
