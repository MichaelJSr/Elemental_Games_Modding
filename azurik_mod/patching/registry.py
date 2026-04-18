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

from azurik_mod.patching.spec import (
    ParametricPatch,
    PatchSpec,
    TrampolinePatch,
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

    tags: tuple[str, ...] = field(default_factory=tuple)
    """Free-form tags like 'fps', 'qol', 'player', 'experimental'."""

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

    def patch_specs(self) -> list[PatchSpec]:
        """Return only the PatchSpec entries in this pack."""
        return [s for s in self.sites if isinstance(s, PatchSpec)]

    def parametric_sites(self) -> list[ParametricPatch]:
        """Return only the ParametricPatch entries in this pack."""
        return [s for s in self.sites if isinstance(s, ParametricPatch)]

    def trampoline_sites(self) -> list[TrampolinePatch]:
        """Return only the TrampolinePatch entries in this pack."""
        return [s for s in self.sites if isinstance(s, TrampolinePatch)]

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
    _REGISTRY.clear()
