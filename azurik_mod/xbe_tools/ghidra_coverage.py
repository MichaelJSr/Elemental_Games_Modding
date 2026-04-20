"""Ghidra knowledge-coverage audit.

Cross-references three Python-side knowledge sources we maintain
against an optional snapshot of the open Ghidra project, and
reports:

- **Knowledge without label** — VAs we've documented in Python
  (``azurik.h`` anchors, ``vanilla_symbols.py`` entries, registered
  patch sites) where Ghidra still shows a generic ``FUN_xxxxxxxx``
  or no symbol at all.
- **Label without knowledge** — Ghidra has a nice function name
  (not starting with ``FUN_`` / ``LAB_`` / ``DAT_``) that the
  Python side doesn't reference yet.  Candidates for promoting to
  ``vanilla_symbols.py``.
- **Totals** — a quick overview so we can see our RE coverage
  growing over time.

## Data sources

1. ``azurik.h`` — grep for ``#define AZURIK_*_VA`` macros.  These
   are the plate-anchors shim authors work against.
2. ``vanilla_symbols.py`` — ``VANILLA_SYMBOLS`` list of
   :class:`VanillaSymbol` entries.
3. Patch-pack registry — every ``PatchSpec``, ``ParametricPatch``,
   and ``TrampolinePatch`` VA across every registered pack.

## Ghidra snapshot format

Accepts a JSON file shaped as:

.. code-block:: json

    {
        "functions": [
            {"address": "0x00085700", "name": "gravity_integrate"},
            ...
        ],
        "labels": [
            {"address": "0x0019C1AC", "name": "fx_magic_timer_str"},
            ...
        ]
    }

This matches the output of the planned ``ghidra-snapshot``
exporter (Tier 3 #15).  Until that ships, callers can produce
the JSON manually from Ghidra or use the ``mcp`` mode where we
call ``functions_list`` via the MCP bridge (not yet wired — the
snapshot path is the stable one).

The whole module works fully offline without a Ghidra snapshot —
it just skips the cross-reference and reports Python-side
totals only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Matches ``#define AZURIK_something_VA  0x001A2B3C`` (with
# optional trailing ``u`` suffix for C literals).
_VA_ANCHOR_RE = re.compile(
    r"^\s*#\s*define\s+(AZURIK_\w+_VA)\s+(0x[0-9A-Fa-f]+)u?",
    re.MULTILINE)


@dataclass(frozen=True)
class KnownSymbol:
    """One Python-side VA anchor / labelled function we care about.

    ``kind`` distinguishes the source so the coverage report can
    show at a glance how much of each knowledge bucket is covered
    by Ghidra labels.
    """

    va: int
    name: str
    kind: str  # "anchor" | "vanilla" | "patch_site"


@dataclass
class CoverageReport:
    """Accumulated coverage summary returned by
    :func:`build_coverage_report`."""

    known_symbols: list[KnownSymbol] = field(default_factory=list)
    ghidra_functions: dict[int, str] = field(default_factory=dict)
    ghidra_labels: dict[int, str] = field(default_factory=dict)
    snapshot_path: str | None = None

    # Derived views (populated by build_coverage_report).
    unlabeled_known: list[KnownSymbol] = field(default_factory=list)
    orphan_ghidra: list[tuple[int, str]] = field(default_factory=list)

    def to_json_dict(self) -> dict:
        """Stable JSON representation for ``--json`` output + tests."""
        return {
            "snapshot_path": self.snapshot_path,
            "known_symbol_count": len(self.known_symbols),
            "ghidra_function_count": len(self.ghidra_functions),
            "ghidra_label_count": len(self.ghidra_labels),
            "unlabeled_known": [
                {"va": s.va, "name": s.name, "kind": s.kind}
                for s in self.unlabeled_known],
            "orphan_ghidra": [
                {"va": va, "name": name}
                for va, name in self.orphan_ghidra],
            "by_kind": {
                kind: sum(1 for s in self.known_symbols if s.kind == kind)
                for kind in ("anchor", "vanilla", "patch_site")
            },
        }


# ---------------------------------------------------------------------------
# Python-side harvesters
# ---------------------------------------------------------------------------

def harvest_azurik_h_anchors(path: Path) -> list[KnownSymbol]:
    """Extract ``#define AZURIK_*_VA 0x...`` macros from the header.

    Skips common non-anchor macros like ``AZURIK_SHIMS_SECTION_NAME``
    that happen to match the prefix but don't end in ``_VA``.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    out: list[KnownSymbol] = []
    for m in _VA_ANCHOR_RE.finditer(text):
        name = m.group(1)
        va = int(m.group(2), 16)
        out.append(KnownSymbol(va=va, name=name, kind="anchor"))
    return out


def harvest_vanilla_symbols() -> list[KnownSymbol]:
    """Every function registered in
    :mod:`azurik_mod.patching.vanilla_symbols`."""
    from azurik_mod.patching.vanilla_symbols import all_entries
    return [
        KnownSymbol(va=s.va, name=s.name, kind="vanilla")
        for s in all_entries()
    ]


def harvest_patch_sites() -> list[KnownSymbol]:
    """Every patch-site VA across the feature registry.

    Synthesises a readable name from the pack + site description
    so ``unlabeled_known`` output is human-friendly.
    """
    # Trigger feature registration side effects.
    import azurik_mod.patches  # noqa: F401
    from azurik_mod.patching.registry import all_packs

    out: list[KnownSymbol] = []
    for pack in all_packs():
        for site in pack.sites:
            va = getattr(site, "va", 0)
            if not va:
                continue  # ParametricPatch with va=0 (virtual)
            label_attr = getattr(site, "label", None)
            label = (f"{pack.name}:{label_attr}"
                     if label_attr else pack.name)
            out.append(KnownSymbol(
                va=va, name=label, kind="patch_site"))
    return out


# ---------------------------------------------------------------------------
# Ghidra snapshot loader
# ---------------------------------------------------------------------------

def load_ghidra_snapshot(path: Path) -> tuple[dict[int, str],
                                              dict[int, str]]:
    """Load ``path`` and return (functions_by_va, labels_by_va)."""
    import json
    blob = json.loads(path.read_text())
    funcs: dict[int, str] = {}
    labels: dict[int, str] = {}
    for entry in blob.get("functions", []):
        va = _parse_va(entry.get("address"))
        name = entry.get("name")
        if va is not None and name:
            funcs[va] = name
    for entry in blob.get("labels", []):
        va = _parse_va(entry.get("address"))
        name = entry.get("name")
        if va is not None and name:
            labels[va] = name
    return funcs, labels


def _parse_va(raw) -> int | None:
    """Parse whatever address format a Ghidra snapshot emits.

    Ghidra's plugin serialises internal addresses as bare hex
    strings with leading zeros (``"00000470"``).  Python's
    ``int(s, 0)`` base-detect rejects these because leading
    zeros make the intended base ambiguous, so we try hex
    first (the format Ghidra always emits) and fall back to
    base-0 for the odd ``0x``-prefixed entry.

    ``EXTERNAL:...`` labels refer to kernel imports that live
    outside the XBE image; they legitimately have no VA, so
    we return ``None`` for those (the coverage report
    filters them out upstream).
    """
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    s = str(raw)
    if s.startswith("EXTERNAL:") or ":" in s:
        return None
    try:
        return int(s, 16)
    except ValueError:
        try:
            return int(s, 0)
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# Main report builder
# ---------------------------------------------------------------------------

def build_coverage_report(snapshot_path: Path | None = None, *,
                          azurik_h: Path | None = None,
                          live_client: "GhidraClient | None" = None,
                          ) -> CoverageReport:
    """Produce a :class:`CoverageReport` from the current workspace.

    ``azurik_h`` defaults to ``shims/include/azurik.h`` relative to
    the repo root.  Pass an explicit path in tests to exercise the
    harvester on a fixture header.

    Data sources for the Ghidra side are resolved in priority
    order:

    1. ``live_client`` — a
       :class:`~azurik_mod.xbe_tools.ghidra_client.GhidraClient`
       pointed at a running Ghidra instance.  Pulls functions +
       labels via paginated GETs.  Use this when Ghidra is open
       and you want a fresh read.
    2. ``snapshot_path`` — a saved JSON dump (schema documented
       in this module's docstring).  Use in CI / offline.
    3. Neither — the report runs Python-side-only with empty
       ``unlabeled_known`` / ``orphan_ghidra``.

    If both ``live_client`` and ``snapshot_path`` are supplied,
    ``live_client`` wins.
    """
    repo_root = _find_repo_root()
    azurik_h = (azurik_h or (repo_root / "shims" / "include" /
                              "azurik.h"))

    known: list[KnownSymbol] = []
    if azurik_h.exists():
        known.extend(harvest_azurik_h_anchors(azurik_h))
    known.extend(harvest_vanilla_symbols())
    known.extend(harvest_patch_sites())

    # De-duplicate by (va, kind) — same VA can legitimately appear
    # once per kind.
    seen: set[tuple[int, str]] = set()
    uniq: list[KnownSymbol] = []
    for s in known:
        key = (s.va, s.kind)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(s)

    report = CoverageReport(known_symbols=uniq)

    # Ghidra-side sources
    funcs: dict[int, str] = {}
    labels: dict[int, str] = {}
    source_label: str | None = None
    if live_client is not None:
        funcs, labels = _live_ghidra_snapshot(live_client)
        source_label = f"live:{live_client.base_url}"
    elif snapshot_path is not None and snapshot_path.exists():
        funcs, labels = load_ghidra_snapshot(snapshot_path)
        source_label = str(snapshot_path)

    if source_label is None:
        return report

    report.ghidra_functions = funcs
    report.ghidra_labels = labels
    report.snapshot_path = source_label

    # Unlabeled known: our VA isn't in funcs + isn't in labels,
    # OR the function name is still a Ghidra auto-label
    # (``FUN_``, ``LAB_``, ``DAT_``).
    def is_meaningful(name: str | None) -> bool:
        if not name:
            return False
        return not (name.startswith("FUN_") or
                    name.startswith("LAB_") or
                    name.startswith("DAT_"))

    for s in uniq:
        fn = funcs.get(s.va)
        lb = labels.get(s.va)
        if not (is_meaningful(fn) or is_meaningful(lb)):
            report.unlabeled_known.append(s)

    # Orphan Ghidra: meaningful function names that we don't
    # track Python-side.
    known_vas = {s.va for s in uniq}
    for va, name in funcs.items():
        if va in known_vas:
            continue
        if is_meaningful(name):
            report.orphan_ghidra.append((va, name))
    report.orphan_ghidra.sort(key=lambda pair: pair[0])

    return report


def _live_ghidra_snapshot(client,
                          include_labels: bool = False
                          ) -> tuple[dict[int, str], dict[int, str]]:
    """Pull the function + label table off a live Ghidra over HTTP.

    Returns ``(funcs_by_va, labels_by_va)`` matching the shape the
    snapshot-file loader produces.  Labels whose address isn't a
    plain hex VA (e.g. ``EXTERNAL:00000001`` thunks) are skipped
    — they don't map to a Python-side VA anyway.

    Label iteration is OFF by default because Azurik's Ghidra
    project has ~45 k symbols (kernel imports dominate); pulling
    all of them takes ~30 s and adds no coverage info the
    function list doesn't already provide.  Pass
    ``include_labels=True`` when you actually need them.
    """
    funcs: dict[int, str] = {}
    for fn in client.iter_functions(page_size=1000):
        funcs[fn.address] = fn.name
    labels: dict[int, str] = {}
    if include_labels:
        for lbl in client.iter_labels(page_size=1000):
            try:
                addr = int(lbl.address, 16)
            except ValueError:
                continue
            labels[addr] = lbl.name
    return funcs, labels


def format_report(report: CoverageReport) -> str:
    """Human-readable rendering of a :class:`CoverageReport`."""
    lines: list[str] = []
    lines.append("=== Python-side knowledge inventory ===")
    by_kind = {k: 0 for k in ("anchor", "vanilla", "patch_site")}
    for s in report.known_symbols:
        by_kind[s.kind] = by_kind.get(s.kind, 0) + 1
    lines.append(
        f"  azurik.h VA anchors:       {by_kind['anchor']}")
    lines.append(
        f"  vanilla_symbols functions: {by_kind['vanilla']}")
    lines.append(
        f"  patch-site VAs:            {by_kind['patch_site']}")
    lines.append(
        f"  TOTAL unique entries:      {len(report.known_symbols)}")

    if report.snapshot_path is None:
        lines.append("")
        lines.append("(No Ghidra snapshot provided — pass "
                     "--snapshot <ghidra-dump.json> to cross-check)")
        return "\n".join(lines)

    lines.append("")
    lines.append(f"=== Ghidra snapshot ({report.snapshot_path}) ===")
    lines.append(f"  functions: {len(report.ghidra_functions)}")
    lines.append(f"  labels:    {len(report.ghidra_labels)}")

    lines.append("")
    lines.append(
        f"=== Unlabeled knowledge "
        f"({len(report.unlabeled_known)} VA(s)) ===")
    lines.append(
        "  Python documents these VAs but Ghidra still shows FUN_* "
        "or no name.")
    lines.append(
        "  Prime candidates for the next Ghidra knowledge-sync pass.")
    lines.append("")
    for s in report.unlabeled_known[:40]:
        lines.append(
            f"    VA 0x{s.va:08X}  [{s.kind:10s}]  {s.name}")
    if len(report.unlabeled_known) > 40:
        lines.append(
            f"    ... and {len(report.unlabeled_known) - 40} more")

    lines.append("")
    lines.append(
        f"=== Orphan Ghidra labels "
        f"({len(report.orphan_ghidra)} VA(s)) ===")
    lines.append(
        "  Ghidra has meaningful names Python doesn't track yet.")
    lines.append(
        "  Candidates to promote into vanilla_symbols.py.")
    lines.append("")
    for va, name in report.orphan_ghidra[:40]:
        lines.append(f"    VA 0x{va:08X}  {name}")
    if len(report.orphan_ghidra) > 40:
        lines.append(
            f"    ... and {len(report.orphan_ghidra) - 40} more")
    return "\n".join(lines)


from azurik_mod.xbe_tools import find_repo_root as _find_repo_root  # noqa: E402


__all__ = [
    "CoverageReport",
    "KnownSymbol",
    "build_coverage_report",
    "format_report",
    "harvest_azurik_h_anchors",
    "harvest_patch_sites",
    "harvest_vanilla_symbols",
    "load_ghidra_snapshot",
]
