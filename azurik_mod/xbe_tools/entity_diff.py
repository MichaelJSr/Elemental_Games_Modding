"""Entity diff — side-by-side property compare across two
``config.xbr`` entries.

Motivation: "why does this enemy have 5× the HP of that one?" —
currently answered via ``azurik-mod dump -s critters_damage -e X``
twice + mental diff.  This module does the mental part for you.

## Semantics

Loads every keyed-table section (see
:mod:`azurik_mod.config.keyed_tables`) and joins each section's
two entities on property name.  A property is reported when it
satisfies at least one of:

- **different** — both entities have it but values differ
- **A-only**  — present on entity A, missing on entity B
- **B-only**  — present on entity B, missing on entity A

Shared-equal rows are suppressed from the default output to
keep diffs readable; pass ``include_equal=True`` to include
them (useful for JSON export).

## Output shape

:class:`EntityDiff` aggregates rows per section so the CLI can
render a grouped report.  The JSON form is flat for easier
downstream processing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from azurik_mod.config.keyed_tables import KeyedTable, load_all_tables


@dataclass(frozen=True)
class DiffRow:
    """One property where entities A and B differ / one is
    missing."""

    section: str
    property: str
    kind: str  # "different" | "a_only" | "b_only" | "equal"
    a_type: str | None
    a_value: object
    b_type: str | None
    b_value: object


@dataclass
class EntityDiff:
    """Full diff between two entities.

    Attributes
    ----------
    entity_a / entity_b: str
        Names of the compared entities.
    sections: dict[str, list[DiffRow]]
        ``section_name → ordered list of rows``.  Sections where
        neither entity has any properties are omitted.
    only_in_a: list[str]
        Section names where A has properties but B doesn't
        (entity B isn't registered in that section).
    only_in_b: list[str]
        Mirror of the above.
    """

    entity_a: str
    entity_b: str
    sections: dict[str, list[DiffRow]] = field(default_factory=dict)
    only_in_a: list[str] = field(default_factory=list)
    only_in_b: list[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(len(v) for v in self.sections.values())

    def to_json_dict(self, include_equal: bool = False) -> dict:
        rows: list[dict] = []
        for section, section_rows in self.sections.items():
            for r in section_rows:
                if r.kind == "equal" and not include_equal:
                    continue
                rows.append({
                    "section": section,
                    "property": r.property,
                    "kind": r.kind,
                    "a_type": r.a_type,
                    "a_value": r.a_value,
                    "b_type": r.b_type,
                    "b_value": r.b_value,
                })
        return {
            "entity_a": self.entity_a,
            "entity_b": self.entity_b,
            "only_in_a": self.only_in_a,
            "only_in_b": self.only_in_b,
            "rows": rows,
        }


# ---------------------------------------------------------------------------
# Core diff logic
# ---------------------------------------------------------------------------


def _diff_entity_in_section(tbl: KeyedTable, a: str, b: str,
                            include_equal: bool) -> list[DiffRow]:
    """Produce DiffRows for two entities inside one keyed table."""
    props_a = tbl.get_entity(a)
    props_b = tbl.get_entity(b)
    if not props_a and not props_b:
        return []
    keys = sorted(set(props_a) | set(props_b))
    out: list[DiffRow] = []
    for key in keys:
        pa = props_a.get(key)
        pb = props_b.get(key)
        if pa is None:
            out.append(DiffRow(
                section=tbl.section_name, property=key, kind="b_only",
                a_type=None, a_value=None,
                b_type=pb[0], b_value=pb[1]))
        elif pb is None:
            out.append(DiffRow(
                section=tbl.section_name, property=key, kind="a_only",
                a_type=pa[0], a_value=pa[1],
                b_type=None, b_value=None))
        elif _value_differs(pa, pb):
            out.append(DiffRow(
                section=tbl.section_name, property=key, kind="different",
                a_type=pa[0], a_value=pa[1],
                b_type=pb[0], b_value=pb[1]))
        elif include_equal:
            out.append(DiffRow(
                section=tbl.section_name, property=key, kind="equal",
                a_type=pa[0], a_value=pa[1],
                b_type=pb[0], b_value=pb[1]))
    return out


def _value_differs(a: tuple, b: tuple) -> bool:
    """Pragmatic equality: same type + same value.

    Float values are compared with a tiny epsilon so cosmetic
    round-trip differences (``1.0`` vs ``1.0000001``) don't flood
    the report.  Different types always count as different.
    """
    if a[0] != b[0]:
        return True
    if a[0] == "num":
        try:
            return abs(float(a[1]) - float(b[1])) > 1e-9
        except (TypeError, ValueError):
            pass
    return a[1] != b[1]


def diff_entities(config_path: str | Path,
                  entity_a: str,
                  entity_b: str, *,
                  include_equal: bool = False) -> EntityDiff:
    """Diff two entities across every keyed-table section in a
    ``config.xbr`` file.

    Raises
    ------
    FileNotFoundError
        ``config_path`` doesn't exist.
    ValueError
        Neither entity appears in any section — likely a typo.
    """
    path = Path(config_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"config.xbr not found: {path}")
    tables = load_all_tables(str(path))

    diff = EntityDiff(entity_a=entity_a, entity_b=entity_b)
    total_a = 0
    total_b = 0
    for name, tbl in tables.items():
        has_a = entity_a in tbl.entity_index
        has_b = entity_b in tbl.entity_index
        if not (has_a or has_b):
            continue
        if has_a and not has_b:
            diff.only_in_a.append(name)
        if has_b and not has_a:
            diff.only_in_b.append(name)
        if has_a:
            total_a += 1
        if has_b:
            total_b += 1
        rows = _diff_entity_in_section(
            tbl, entity_a, entity_b, include_equal)
        if rows:
            diff.sections[name] = rows

    if total_a == 0 and total_b == 0:
        raise ValueError(
            f"Neither {entity_a!r} nor {entity_b!r} appear in any "
            f"keyed-table section.  Check names with "
            f"`azurik-mod list --entities <section>`.")

    return diff


# ---------------------------------------------------------------------------
# Pretty printer
# ---------------------------------------------------------------------------


def format_diff(diff: EntityDiff, *, include_equal: bool = False) -> str:
    """Grouped-by-section human-readable report."""
    lines = [
        f"Entity diff: {diff.entity_a!r}  vs  {diff.entity_b!r}",
        f"  sections with differences: {len(diff.sections)}  "
        f"(total rows: {diff.total_rows})",
    ]
    if diff.only_in_a:
        lines.append(
            f"  sections only {diff.entity_a!r} appears in: "
            f"{', '.join(diff.only_in_a)}")
    if diff.only_in_b:
        lines.append(
            f"  sections only {diff.entity_b!r} appears in: "
            f"{', '.join(diff.only_in_b)}")

    for section_name in sorted(diff.sections):
        rows = diff.sections[section_name]
        if not include_equal:
            rows = [r for r in rows if r.kind != "equal"]
        if not rows:
            continue
        lines.append("")
        lines.append(f"[{section_name}]")
        # Column widths — keep modest for terminal wrapping.
        for r in rows:
            if r.kind == "different":
                lines.append(
                    f"  ~ {r.property:<28s}  "
                    f"A({r.a_type}) {r.a_value!s:<14s}  "
                    f"B({r.b_type}) {r.b_value!s}")
            elif r.kind == "a_only":
                lines.append(
                    f"  - {r.property:<28s}  "
                    f"A({r.a_type}) {r.a_value!s:<14s}  "
                    f"(B missing)")
            elif r.kind == "b_only":
                lines.append(
                    f"  + {r.property:<28s}  (A missing)             "
                    f"  B({r.b_type}) {r.b_value!s}")
            else:
                lines.append(
                    f"  = {r.property:<28s}  "
                    f"{r.a_type} {r.a_value}")
    return "\n".join(lines)


__all__ = [
    "DiffRow",
    "EntityDiff",
    "diff_entities",
    "format_diff",
]
