"""Shim-object inspector — preview what bytes a ``.o`` will emit.

Wraps :mod:`azurik_mod.patching.coff` to produce a human-readable
summary of a compiled shim object WITHOUT running the full patching
pipeline.  Useful when authoring a new shim to verify:

- Section sizes line up with the 5- or 6-byte trampoline budget
- Relocations target the expected vanilla symbols
- Calling-convention symbol suffixes (``@N`` stdcall decoration)
  match what the vanilla_symbols registry expects
- Static-assert traps fired (or didn't) at compile time

Accepts either a direct ``.o`` path or a feature-folder path, in
which case it resolves the pack's ``ShimSource`` to find the .o.

See docs/TOOLING_ROADMAP.md § Tier 1 #3 for the motivation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from azurik_mod.patching.coff import (
    CoffFile,
    CoffRelocation,
    CoffSection,
    CoffSymbol,
    parse_coff,
)

# IMAGE_REL_I386_* → human name.  Matches the subset coff.py
# actively relocates (layout_coff raises on anything else).
_RELOC_TYPE_NAMES = {
    0x0000: "ABSOLUTE",
    0x0006: "DIR32",
    0x0007: "DIR32NB",
    0x0014: "REL32",
}

# Storage-class codes we see in practice (clang-cl + nxdk LLD).
_STORAGE_CLASS_NAMES = {
    2: "EXTERNAL",
    3: "STATIC",
    6: "LABEL",
    101: "FUNCTION",
    105: "FILE",
    103: "END_OF_STRUCT",
}


@dataclass(frozen=True)
class ShimSectionSummary:
    name: str
    raw_size: int
    flags: int
    reloc_count: int
    reloc_types: tuple[str, ...]  # unique, sorted
    first_bytes: str              # hex, up to first 32 bytes


@dataclass(frozen=True)
class ShimSymbolSummary:
    name: str
    section_number: int
    value: int
    storage_class: int
    storage_class_name: str
    is_external: bool


@dataclass(frozen=True)
class ShimRelocationSummary:
    section_name: str      # which section the reloc lives in
    offset: int            # offset within that section
    type_code: int
    type_name: str
    symbol_name: str


@dataclass
class ShimInspection:
    """Full inspection report for one compiled shim object."""

    path: str
    machine: int
    total_section_bytes: int
    sections: list[ShimSectionSummary] = field(default_factory=list)
    symbols: list[ShimSymbolSummary] = field(default_factory=list)
    relocations: list[ShimRelocationSummary] = field(default_factory=list)

    @property
    def externals(self) -> list[ShimSymbolSummary]:
        """Every external (undefined) symbol — i.e. callouts to vanilla
        functions that the COFF loader will need to resolve."""
        return [s for s in self.symbols if s.is_external]

    def to_json_dict(self) -> dict:
        return {
            "path": self.path,
            "machine": self.machine,
            "total_section_bytes": self.total_section_bytes,
            "sections": [
                {"name": s.name, "raw_size": s.raw_size,
                 "flags": s.flags, "reloc_count": s.reloc_count,
                 "reloc_types": list(s.reloc_types),
                 "first_bytes": s.first_bytes}
                for s in self.sections],
            "symbols": [
                {"name": s.name, "section_number": s.section_number,
                 "value": s.value, "storage_class": s.storage_class,
                 "storage_class_name": s.storage_class_name,
                 "is_external": s.is_external}
                for s in self.symbols],
            "relocations": [
                {"section": r.section_name, "offset": r.offset,
                 "type_code": r.type_code, "type_name": r.type_name,
                 "symbol": r.symbol_name}
                for r in self.relocations],
        }


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def inspect_object(target: Path) -> ShimInspection:
    """Inspect a ``.o`` file or a feature folder.

    When ``target`` is a directory, we look for a registered pack
    whose ``shim.folder`` matches (the canonical folder-per-feature
    layout) and resolve its ``ShimSource`` to find the compiled
    object.

    Raises :exc:`FileNotFoundError` if no matching .o is found, or
    :exc:`ValueError` if the .o doesn't parse as PE-COFF.
    """
    obj_path = _resolve_target(target)
    coff = parse_coff(obj_path.read_bytes())
    return _summarise(obj_path, coff)


def _resolve_target(target: Path) -> Path:
    """Turn a Path into a concrete ``.o`` file.

    Strategies, in order:

    1. ``target`` already points at a file.
    2. ``target`` is a feature folder — look up the registered pack
       whose ``shim.folder`` equals this directory + use its
       ``ShimSource.object_path(...)``.
    3. ``target`` is a feature folder without a registered pack —
       fall back to ``<repo>/shims/build/<folder-name>.o``.
    """
    if target.is_file():
        return target
    if not target.is_dir():
        raise FileNotFoundError(f"not a file or directory: {target}")

    import azurik_mod.patches  # noqa: F401 — populate the registry
    from azurik_mod.patching.registry import all_packs
    resolved_target = target.resolve()

    for pack in all_packs():
        if pack.shim is None:
            continue
        if pack.shim.folder.resolve() == resolved_target:
            obj = pack.shim.object_path(pack.name, _find_repo_root())
            if obj.exists():
                return obj
            raise FileNotFoundError(
                f"feature {pack.name!r} has no compiled object at "
                f"{obj}; run the build first (env "
                f"AZURIK_SHIM_FORCE_REBUILD=1 forces it)")

    # Fallback: guess shims/build/<folder>.o
    guessed = _find_repo_root() / "shims" / "build" / f"{target.name}.o"
    if guessed.exists():
        return guessed
    raise FileNotFoundError(
        f"no registered feature + no shims/build/{target.name}.o")


def _summarise(path: Path, coff: CoffFile) -> ShimInspection:
    """Render a parsed :class:`CoffFile` as a :class:`ShimInspection`."""
    sec_summaries = []
    reloc_summaries = []
    total_bytes = 0
    # Build a VA-less mapping from section_number (1-based) to name
    # so symbol printouts can show their host section.
    section_names_by_index = {i + 1: s.name for i, s in enumerate(coff.sections)}

    for section in coff.sections:
        total_bytes += section.raw_size
        reloc_types = sorted({
            _RELOC_TYPE_NAMES.get(r.type, f"UNKNOWN(0x{r.type:X})")
            for r in section.relocations})
        sec_summaries.append(ShimSectionSummary(
            name=section.name,
            raw_size=section.raw_size,
            flags=section.flags,
            reloc_count=len(section.relocations),
            reloc_types=tuple(reloc_types),
            first_bytes=(section.data[:32].hex()
                         if section.data else ""),
        ))
        for reloc in section.relocations:
            sym = _safe_symbol(coff.symbols, reloc.symbol_index)
            reloc_summaries.append(ShimRelocationSummary(
                section_name=section.name,
                offset=reloc.va,
                type_code=reloc.type,
                type_name=_RELOC_TYPE_NAMES.get(
                    reloc.type, f"UNKNOWN(0x{reloc.type:X})"),
                symbol_name=sym.name if sym else "(unknown)",
            ))

    sym_summaries = []
    for sym in coff.symbols:
        if not sym.name:
            continue  # skip placeholder aux entries (empty name)
        sym_summaries.append(ShimSymbolSummary(
            name=sym.name,
            section_number=sym.section_number,
            value=sym.value,
            storage_class=sym.storage_class,
            storage_class_name=_STORAGE_CLASS_NAMES.get(
                sym.storage_class,
                f"CLASS_{sym.storage_class}"),
            is_external=(sym.section_number == 0 and
                         sym.storage_class == 2),
        ))

    return ShimInspection(
        path=str(path),
        machine=coff.machine,
        total_section_bytes=total_bytes,
        sections=sec_summaries,
        symbols=sym_summaries,
        relocations=reloc_summaries,
    )


def _safe_symbol(symbols: list[CoffSymbol],
                 index: int) -> CoffSymbol | None:
    if 0 <= index < len(symbols):
        return symbols[index]
    return None


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------

def format_inspection(r: ShimInspection) -> str:
    lines = [
        f"=== Shim inspection: {r.path} ===",
        f"  machine:              0x{r.machine:04X}",
        f"  total section bytes:  {r.total_section_bytes}",
        "",
        "Sections:",
    ]
    for s in r.sections:
        rtypes = ", ".join(s.reloc_types) or "(none)"
        lines.append(
            f"  {s.name:<12s}  size={s.raw_size:5d}   "
            f"flags=0x{s.flags:08X}   relocs={s.reloc_count:2d}  "
            f"[{rtypes}]")
        if s.first_bytes:
            preview = s.first_bytes[:64]
            more = "..." if len(s.first_bytes) > 64 else ""
            lines.append(f"    bytes[0:32] = {preview}{more}")

    lines.append("")
    lines.append(f"Symbols ({len(r.symbols)}):")
    ext = r.externals
    if ext:
        lines.append(f"  {len(ext)} external (resolved against "
                     f"vanilla_symbols at layout time):")
        for s in ext:
            lines.append(
                f"    {s.name:<40s}  {s.storage_class_name}")
    internals = [s for s in r.symbols if not s.is_external]
    if internals:
        lines.append(f"  {len(internals)} internal / section / local:")
        for s in internals[:20]:
            lines.append(
                f"    {s.name:<40s}  sect={s.section_number}  "
                f"val=0x{s.value:X}  {s.storage_class_name}")
        if len(internals) > 20:
            lines.append(f"    ... and {len(internals) - 20} more")

    lines.append("")
    lines.append(f"Relocations ({len(r.relocations)}):")
    for reloc in r.relocations:
        lines.append(
            f"  {reloc.section_name:<12s}  off=0x{reloc.offset:04X}  "
            f"{reloc.type_name:<10s}  → {reloc.symbol_name}")

    return "\n".join(lines)


def _find_repo_root() -> Path:
    here = Path(__file__).resolve().parent
    for p in (here, *here.parents):
        if (p / "pyproject.toml").exists() and (p / "azurik_mod").is_dir():
            return p
    return Path.cwd()


__all__ = [
    "ShimInspection",
    "ShimRelocationSummary",
    "ShimSectionSummary",
    "ShimSymbolSummary",
    "format_inspection",
    "inspect_object",
]
