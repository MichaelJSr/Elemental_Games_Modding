"""Push Python-side knowledge into a live Ghidra project.

Tool #4 on the roadmap.  Takes every named VA we track in Python
(``azurik.h`` anchors, ``vanilla_symbols.py`` entries, patch-site
VAs) and writes them back into the currently-open Ghidra project
as renamed functions + plate comments — so the next time a human
opens those addresses in Ghidra they see our Python-side
understanding instead of ``FUN_00085700`` and a blank annotation
pane.

## Design

Reads our knowledge bases by importing them (same harvesters
:mod:`ghidra_coverage` uses).  Builds a list of
:class:`SyncAction` records describing what SHOULD change on
the Ghidra side; optionally applies them via
:class:`GhidraClient`.

Three action kinds:

- ``rename`` — function's current name is ``FUN_*``, target name
  comes from our Python-side record.
- ``comment`` — we have a docstring-quality annotation that
  belongs as a plate comment on the function.
- ``keep`` — the Ghidra name + comment are already set the way
  we want them; no-op.

## Safety

Dry-run mode is the default.  Applying requires ``apply=True``
explicitly.  The apply path never touches an address that
Ghidra has ALREADY renamed to something meaningful — the tool
refuses to overwrite ``gravity_integrate`` → ``gravity_integrate_raw``
unless the user passes ``--force``.

## API

:func:`plan_sync(client)` → list of :class:`SyncAction` records.
:func:`apply_sync(client, actions, *, force=False)` → mutates
Ghidra state.  Returns a summary dict with counts + any errors.

See ``docs/TOOLING_ROADMAP.md`` § 4 for the original motivation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .ghidra_client import (
    GhidraClient,
    GhidraClientError,
    GhidraFunction,
)
from .ghidra_coverage import (
    KnownSymbol,
    harvest_azurik_h_anchors,
    harvest_patch_sites,
    harvest_vanilla_symbols,
)
from .struct_diff import HeaderStruct, parse_header_structs


@dataclass
class SyncAction:
    """One planned change to the Ghidra project.

    Attributes
    ----------
    va: int
        Target virtual address.
    kind: str
        ``rename`` / ``comment`` / ``keep``.
    current_name: str | None
        Ghidra's current function name at this VA (``None`` when
        the VA doesn't resolve to a function — common for data /
        BSS anchors that we still want to leave a comment on).
    new_name: str | None
        Name to PATCH to (``None`` on ``comment`` or ``keep``
        actions).
    comment: str | None
        Plate-comment text to POST (``None`` when not setting one).
    rationale: str
        One-line description used in the dry-run report.
    """

    va: int
    kind: str
    current_name: str | None = None
    new_name: str | None = None
    comment: str | None = None
    rationale: str = ""


@dataclass
class SyncReport:
    """Result of an :func:`apply_sync` run."""

    attempted: int = 0
    renamed: int = 0
    commented: int = 0
    skipped: int = 0
    structs_created: int = 0
    struct_fields_added: int = 0
    structs_skipped: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class StructAction:
    """Planned Data-Type-Manager action for one struct."""

    name: str
    size: int | None           # inferred from declared_size / fields
    field_count: int
    kind: str                   # "create" | "keep" | "recreate"
    rationale: str = ""


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def _collect_known_symbols(azurik_h: Path | None = None
                           ) -> dict[int, KnownSymbol]:
    """Merge every knowledge source into a VA-keyed dict.

    When multiple sources disagree about a VA's canonical name
    (vanilla_symbols + anchor with the same address), the
    vanilla_symbols entry wins — it's the authored one-liner we
    actually want in Ghidra.  Other sources only contribute when
    the VA isn't already claimed.
    """
    merged: dict[int, KnownSymbol] = {}
    # Priority: vanilla > anchor > patch_site.
    repo_root = _find_repo_root()
    anchors = harvest_azurik_h_anchors(
        azurik_h or (repo_root / "shims" / "include" / "azurik.h"))
    for s in anchors:
        merged.setdefault(s.va, s)
    for s in harvest_patch_sites():
        merged.setdefault(s.va, s)
    for s in harvest_vanilla_symbols():
        merged[s.va] = s  # always wins
    return merged


def _derive_target_name(sym: KnownSymbol) -> str | None:
    """Turn a :class:`KnownSymbol` into the name we want in Ghidra.

    Returns ``None`` for symbols we don't want to push (patch-site
    labels with a ``pack:name`` shape — those are useful to humans
    reading the Python code but noisy in Ghidra).
    """
    if sym.kind == "vanilla":
        return sym.name
    if sym.kind == "anchor":
        # Strip the AZURIK_ prefix + _VA suffix for readability.
        base = sym.name
        if base.startswith("AZURIK_"):
            base = base[len("AZURIK_"):]
        if base.endswith("_VA"):
            base = base[:-len("_VA")]
        # Lowercase to match Ghidra's typical naming.
        return base.lower()
    return None


def _derive_comment(sym: KnownSymbol, client_info: str | None = None
                    ) -> str:
    """Build a plate-comment string for a symbol.

    Includes the source bucket so a human reading the comment
    knows where it came from, and can click through to the
    authoritative Python file.
    """
    lines = [
        f"[azurik_mod.{sym.kind}] {sym.name}",
        f"Auto-generated by azurik-mod ghidra-sync.",
        f"Edit the Python source, then re-sync — don't edit here.",
    ]
    return "\n".join(lines)


def plan_sync(client: GhidraClient, *,
              azurik_h: Path | None = None,
              allow_data_comments: bool = True
              ) -> list[SyncAction]:
    """Produce a :class:`SyncAction` list for every known symbol.

    ``allow_data_comments`` controls whether anchors for .data /
    BSS addresses (where there's no function to rename) contribute
    comment-only actions.  Default ``True`` — having named pointers
    show up when you click on a BSS address is useful.
    """
    known = _collect_known_symbols(azurik_h=azurik_h)
    actions: list[SyncAction] = []
    for va, sym in sorted(known.items()):
        target_name = _derive_target_name(sym)
        comment = _derive_comment(sym)

        try:
            fn = client.get_function(va)
        except GhidraClientError:
            fn = None

        if fn is None:
            # No function at this VA — skip renames, optionally
            # emit a comment-only action.
            if target_name is None or not allow_data_comments:
                continue
            actions.append(SyncAction(
                va=va, kind="comment", current_name=None,
                new_name=None, comment=comment,
                rationale=(f"{sym.kind} {sym.name!r} at 0x{va:X} "
                           f"— not a function in Ghidra, annotating "
                           f"data site only")))
            continue

        if target_name is None:
            # patch-site entry — skip the rename, but still
            # annotate so future lookups see our rationale.
            actions.append(SyncAction(
                va=va, kind="comment",
                current_name=fn.name, new_name=None,
                comment=comment,
                rationale=(f"{sym.kind} {sym.name!r} at 0x{va:X} "
                           f"— patch-site annotation only")))
            continue

        # Rename + comment path.
        if fn.name == target_name:
            actions.append(SyncAction(
                va=va, kind="keep",
                current_name=fn.name, new_name=target_name,
                comment=comment,
                rationale=(f"{sym.kind} {sym.name!r} — already "
                           f"named {target_name!r}")))
        else:
            actions.append(SyncAction(
                va=va, kind="rename",
                current_name=fn.name, new_name=target_name,
                comment=comment,
                rationale=(f"{sym.kind} {sym.name!r}")))
    return actions


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _is_protected_name(name: str) -> bool:
    """Detect names we treat as "user has already set this by
    hand" — avoid overwriting without ``--force``.

    Protected = any name that ISN'T Ghidra's auto-label pattern
    (``FUN_*``, ``LAB_*``, ``DAT_*``).  A function named
    ``gravity_integrate`` is protected; a function still called
    ``FUN_00085700`` is fair game.
    """
    if not name:
        return False
    return not (name.startswith("FUN_") or
                name.startswith("LAB_") or
                name.startswith("DAT_"))


def apply_sync(client: GhidraClient, actions: Iterable[SyncAction],
               *, force: bool = False) -> SyncReport:
    """Execute the plan against ``client``.

    Parameters
    ----------
    client: GhidraClient
    actions: iterable of :class:`SyncAction`
        Typically the output of :func:`plan_sync`.
    force: bool
        When ``True``, rename actions overwrite even
        human-meaningful names.  Default ``False`` — rename-only
        if the current name is still a Ghidra auto-label.

    Returns a :class:`SyncReport` with counts + any errors.
    """
    report = SyncReport()
    for action in actions:
        report.attempted += 1
        try:
            if action.kind == "rename":
                if (action.current_name is not None
                        and _is_protected_name(action.current_name)
                        and not force):
                    report.skipped += 1
                    continue
                if action.new_name is not None:
                    client.rename_function(action.va, action.new_name)
                    report.renamed += 1
                if action.comment is not None:
                    client.set_comment(action.va, action.comment,
                                       kind="plate")
                    report.commented += 1
            elif action.kind == "comment":
                if action.comment is not None:
                    client.set_comment(action.va, action.comment,
                                       kind="plate")
                    report.commented += 1
            else:  # keep — still refresh the comment so the
                # docstring tracks the Python source.
                if action.comment is not None:
                    client.set_comment(action.va, action.comment,
                                       kind="plate")
                    report.commented += 1
        except GhidraClientError as exc:
            report.errors.append(
                f"0x{action.va:08X} ({action.kind}): {exc}")
    return report


# ---------------------------------------------------------------------------
# Struct push (Data Type Manager sync)
# ---------------------------------------------------------------------------

# Mapping from header-side C types to Ghidra's DTM type names.
# Ghidra uses lowercased builtins for primitive widths; pointers
# collapse to the ``void *`` typedef.  Anything unknown falls back
# to a typed ``undefined`` of the field's intrinsic width.
_C_TYPE_TO_GHIDRA = {
    # Signed-int typedef spellings we accept (both `i32`-style +
    # `s32`-style since the header uses `i` but some legacy notes
    # quote `s`):
    "u8":       "uchar",    "i8":       "char",    "s8":       "char",
    "u16":      "ushort",   "i16":      "short",   "s16":      "short",
    "u32":      "uint",     "i32":      "int",     "s32":      "int",
    "u64":      "ulonglong",
    "i64":      "longlong", "s64":      "longlong",
    "f32":      "float",    "f64":      "double",
    "float":    "float",    "double":   "double",
    "char":     "char",     "uchar":    "uchar",
    "byte":     "byte",     "bool":     "bool",
    "int":      "int",      "uint":     "uint",
    "long":     "long",     "ulong":    "ulong",
    "short":    "short",    "ushort":   "ushort",
    "void":     "void",
}


def _ghidra_type_for(c_type: str) -> str:
    """Map a header C type to Ghidra's DTM spelling."""
    t = c_type.strip().replace("const ", "").replace(
        "volatile ", "")
    if "*" in t or "[" in t:
        return "void *"
    bare = t.split()[-1]
    return _C_TYPE_TO_GHIDRA.get(bare, "undefined4")


def plan_struct_sync(client: GhidraClient, *,
                     azurik_h: Path | None = None,
                     recreate_existing: bool = False,
                     ) -> list[StructAction]:
    """Plan struct pushes from ``azurik.h`` into Ghidra's DTM.

    For each ``typedef struct`` block in the header:

    - If Ghidra doesn't have a struct with that name, plan
      ``kind="create"`` with all fields.
    - If Ghidra already has one AND ``recreate_existing`` is
      ``False`` (default), plan ``kind="keep"`` — leaves whatever
      Ghidra has alone.
    - If ``recreate_existing=True``, plan ``kind="recreate"``;
      apply_struct_sync will DELETE + re-create.

    Skips fields with offset -1 (our header parser's "no comment
    giving the offset" sentinel) since Ghidra needs a concrete
    offset for each member.
    """
    header = azurik_h or (_find_repo_root() / "shims" / "include"
                           / "azurik.h")
    structs = parse_header_structs(header)
    actions: list[StructAction] = []
    for hs in structs:
        has_fields = sum(1 for f in hs.fields if f.offset >= 0)
        size = hs.declared_size or hs.inferred_size() or 0
        try:
            existing = client.get_struct(hs.name)
        except GhidraClientError:
            existing = None

        if existing is None:
            actions.append(StructAction(
                name=hs.name, size=size, field_count=has_fields,
                kind="create",
                rationale=(f"new: {has_fields} fields, size={size}")))
        elif recreate_existing:
            actions.append(StructAction(
                name=hs.name, size=size, field_count=has_fields,
                kind="recreate",
                rationale=(f"recreating: Ghidra has "
                           f"{len(existing.fields)} fields, "
                           f"header has {has_fields}")))
        else:
            actions.append(StructAction(
                name=hs.name, size=size, field_count=has_fields,
                kind="keep",
                rationale=(f"Ghidra already has {hs.name!r}; pass "
                           f"--recreate-structs to overwrite")))
    return actions


def apply_struct_sync(client: GhidraClient,
                      actions: Iterable[StructAction],
                      *,
                      azurik_h: Path | None = None,
                      report: SyncReport | None = None,
                      ) -> SyncReport:
    """Execute a struct-sync plan against ``client``.

    For each ``create`` / ``recreate`` action: creates the struct
    then adds every field from the header (skipping offset=-1
    fields, which have no committed layout in the header).

    ``recreate`` actions DELETE the existing struct first — this
    wipes any Ghidra variables typed with the old layout.  Use
    cautiously.
    """
    header = azurik_h or (_find_repo_root() / "shims" / "include"
                           / "azurik.h")
    header_structs = {s.name: s for s in parse_header_structs(header)}
    report = report or SyncReport()

    for act in actions:
        if act.kind == "keep":
            report.structs_skipped += 1
            continue

        hs = header_structs.get(act.name)
        if hs is None:
            report.errors.append(
                f"struct {act.name}: no matching definition in "
                f"header — skipping")
            continue

        try:
            if act.kind == "recreate":
                try:
                    client.delete_struct(act.name)
                except GhidraClientError as exc:
                    # Not-found on delete means someone removed it
                    # between plan + apply; keep going.
                    if "not found" not in str(exc).lower():
                        raise
            client.create_struct(
                act.name,
                size=act.size or 1,
                description=f"Mirror of {hs.name} from azurik.h")
            report.structs_created += 1

            fields_with_offsets = [
                f for f in hs.fields if f.offset >= 0]
            # Sort by offset so fields land in the right order
            fields_with_offsets.sort(key=lambda f: f.offset)
            for hf in fields_with_offsets:
                try:
                    client.add_struct_field(
                        act.name,
                        field_name=hf.name,
                        field_type=_ghidra_type_for(hf.c_type),
                        offset=hf.offset,
                        comment=(hf.comment or "")[:200])
                    report.struct_fields_added += 1
                except GhidraClientError as exc:
                    report.errors.append(
                        f"struct {act.name}.{hf.name}: {exc}")

        except GhidraClientError as exc:
            report.errors.append(f"struct {act.name}: {exc}")

    return report


def format_struct_plan(actions: list[StructAction]) -> str:
    """Pretty-print a struct-sync plan (for dry-run output)."""
    if not actions:
        return "(no struct actions planned)"
    out: list[str] = []
    buckets: dict[str, list[StructAction]] = {
        "create": [], "recreate": [], "keep": []}
    for a in actions:
        buckets.setdefault(a.kind, []).append(a)
    for kind in ("create", "recreate", "keep"):
        grp = buckets.get(kind, [])
        out.append(f"=== struct {kind}  ({len(grp)}) ===")
        if not grp:
            out.append("  (none)")
            continue
        for a in grp:
            size_str = f"size={a.size}" if a.size else "size=?"
            out.append(
                f"  {a.name:30} {size_str:12} "
                f"fields={a.field_count:3d}  ({a.rationale})")
        out.append("")
    return "\n".join(out)


def format_plan(actions: list[SyncAction]) -> str:
    """Human-readable dry-run report."""
    if not actions:
        return "(no actions — all known symbols already reflect "\
               "Python-side state)"
    buckets: dict[str, list[SyncAction]] = {
        "rename": [], "comment": [], "keep": []}
    for a in actions:
        buckets.setdefault(a.kind, []).append(a)
    lines = []
    for kind in ("rename", "comment", "keep"):
        group = buckets.get(kind, [])
        lines.append(f"=== {kind}  ({len(group)}) ===")
        if not group:
            lines.append("  (none)")
            continue
        for a in group:
            if kind == "rename":
                lines.append(
                    f"  0x{a.va:08X}  "
                    f"{a.current_name!r} → {a.new_name!r}  "
                    f"({a.rationale})")
            elif kind == "comment":
                head = a.comment.splitlines()[0] if a.comment else ""
                lines.append(
                    f"  0x{a.va:08X}  "
                    f"{a.current_name!r}  annotate: {head}")
            else:
                lines.append(
                    f"  0x{a.va:08X}  {a.current_name!r}  "
                    f"{a.rationale}")
        lines.append("")
    return "\n".join(lines)


def _find_repo_root() -> Path:
    here = Path(__file__).resolve().parent
    for p in (here, *here.parents):
        if (p / "pyproject.toml").exists() and (p / "azurik_mod").is_dir():
            return p
    return Path.cwd()


__all__ = [
    "StructAction",
    "SyncAction",
    "SyncReport",
    "apply_struct_sync",
    "apply_sync",
    "format_plan",
    "format_struct_plan",
    "plan_struct_sync",
    "plan_sync",
]
