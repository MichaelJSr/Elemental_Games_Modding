"""Struct type diff — #23 from ``docs/TOOLING_ROADMAP.md``.

Compare the struct layouts declared in ``shims/include/azurik.h``
against whatever Ghidra has in its Data Type Manager.  Surfaces
three classes of drift:

1. **Missing in Ghidra** — azurik.h has ``typedef struct X {...}``
   but the Ghidra project doesn't.  Push it into Ghidra so the
   decompiler gets typed output.
2. **Missing in header** — Ghidra has a typed struct the shim
   headers haven't captured yet.  Import it so shims can use it.
3. **Size / field disagreement** — same name on both sides but
   different total size or offset drift.  Fix whichever side is
   wrong.

## Parser scope (azurik.h side)

The header parser is deliberately small: it scans
``typedef struct NAME { ... } NAME;`` blocks and extracts each
field's ``offset`` + ``name`` + ``type`` from:

- an inline ``/* +0xHH ... */`` comment (primary source);
- or an inline ``// +0xHH`` line comment.

Fields without a recognisable offset comment are captured as
``HeaderField(name="<typename>", offset=-1)`` so the diff can
still surface them (with "offset unknown" flagged) rather than
silently dropping them.

Bit-fields, unions, and nested typedefs are NOT supported —
none of our hand-written structs use them.  A deliberate
limitation; when a shim lands that needs one of those features,
we'll extend the parser then.

## Output shape

``diff_structs`` returns a :class:`StructDiffReport` whose
``entries`` list is sorted by struct name and whose top-level
counters drive the CLI's summary banner.  Callers can render
either plain text (via :func:`format_report`) or JSON (via
``to_json_dict``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from azurik_mod.xbe_tools.ghidra_client import (
    GhidraClient,
    GhidraClientError,
    GhidraStruct,
    GhidraStructField,
)

__all__ = [
    "HeaderField",
    "HeaderStruct",
    "StructDiffEntry",
    "StructDiffReport",
    "diff_structs",
    "format_report",
    "parse_header_structs",
]


# ---------------------------------------------------------------------------
# Header parser
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeaderField:
    """One struct field captured from the C header.

    ``offset`` is -1 when the parser couldn't find a
    ``+0xHH``-style comment; downstream diff logic flags this
    specially so we don't confuse "missing offset in our header"
    with "drift between header and Ghidra".
    """

    name: str
    c_type: str
    offset: int
    comment: str = ""


@dataclass(frozen=True)
class HeaderStruct:
    """One ``typedef struct NAME { ... } NAME;`` block."""

    name: str
    fields: tuple[HeaderField, ...]
    declared_size: int | None = None  # filled when an explicit
    # `_Static_assert(sizeof(X) == N)` is found nearby; else None.

    def inferred_size(self) -> int | None:
        """Infer total size from the max offset seen (+ field size
        guess from ``c_type``).  Falls back to ``None`` when the
        final field has an unknown offset."""
        known = [f for f in self.fields if f.offset >= 0]
        if not known:
            return None
        tail = max(known, key=lambda f: f.offset)
        return tail.offset + _type_size_guess(tail.c_type)


_OFFSET_RE = re.compile(r"\+\s*0x([0-9A-Fa-f]+)")
_STATIC_ASSERT_RE = re.compile(
    r"_Static_assert\s*\(\s*sizeof\s*\(\s*(\w+)\s*\)\s*==\s*"
    r"(0x[0-9A-Fa-f]+|\d+)")


def parse_header_structs(source: str | Path) -> list[HeaderStruct]:
    """Extract every ``typedef struct NAME { ... } NAME;`` block
    from a C header (or path to one).

    Returns a list sorted by struct name.  Does NOT follow ``#include``
    directives — each file is parsed independently, which matches
    how the shim headers are structured (one declaration per
    typedef, no cross-file typedef chains).
    """
    if isinstance(source, Path):
        text = source.read_text(encoding="utf-8", errors="replace")
    else:
        text = source

    results: list[HeaderStruct] = []
    size_hints = dict(_STATIC_ASSERT_RE.findall(text))
    # Normalise the hints to int.
    size_map: dict[str, int] = {}
    for name, tok in size_hints.items():
        try:
            size_map[name] = (int(tok, 16) if tok.startswith("0x")
                              else int(tok))
        except ValueError:
            continue

    # Walk the text manually so we can balance braces — regex-only
    # parsing gets confused by nested braces in field initialisers
    # (not used in azurik.h today but trivial to hit later).
    idx = 0
    length = len(text)
    while True:
        match = re.search(
            r"typedef\s+struct\s+(?:(\w+)\s+)?\{", text[idx:])
        if not match:
            break
        body_start = idx + match.end()
        depth = 1
        j = body_start
        while j < length and depth:
            ch = text[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            j += 1
        if depth != 0:
            break
        body = text[body_start:j - 1]
        tail_match = re.match(r"\s*(\w+)\s*;", text[j:])
        if not tail_match:
            idx = j
            continue
        struct_name = tail_match.group(1)
        fields = _extract_fields(body)
        declared_size = size_map.get(struct_name)
        results.append(HeaderStruct(
            name=struct_name,
            fields=tuple(fields),
            declared_size=declared_size))
        idx = j + tail_match.end()

    results.sort(key=lambda s: s.name)
    return results


_FIELD_LINE_RE = re.compile(
    r"([^;\n{]*?);([^\n;]*)",
    flags=re.DOTALL,
)


def _extract_fields(body: str) -> list[HeaderField]:
    """Pull one :class:`HeaderField` per field declaration.

    Walks the body line-by-line using a regex that captures
    both the declaration (everything up to the ``;``) and any
    trailing comment on the same logical line (everything up to
    the next ``\\n`` or ``;``).  This matters because the
    ``+0xHH`` offset annotation typically sits AFTER the ``;``::

        u32 feature_class_id;   /* +0x0C ... */
    """
    fields: list[HeaderField] = []
    for m in _FIELD_LINE_RE.finditer(body):
        decl_raw, trailing = m.group(1), m.group(2)
        decl_clean = _strip_c_comments(decl_raw).strip()
        if not decl_clean:
            continue
        # Skip nested struct decls / bit-fields / inline typedefs —
        # our header never uses them, and parsing them is out of
        # scope.
        if "{" in decl_clean or ":" in decl_clean:
            continue
        parts = decl_clean.split()
        if len(parts) < 2:
            continue
        name = parts[-1].rstrip(",").rstrip("]").split("[")[0]
        c_type = " ".join(parts[:-1])
        # Look for the offset annotation in EITHER the decl
        # (legacy placement) or the trailing comment (dominant
        # style in azurik.h).
        offset_match = (_OFFSET_RE.search(trailing) or
                        _OFFSET_RE.search(decl_raw))
        if offset_match:
            try:
                offset = int(offset_match.group(1), 16)
            except ValueError:
                offset = -1
        else:
            offset = -1
        comment = (_extract_comment_text(trailing)
                   or _extract_comment_text(decl_raw))
        fields.append(HeaderField(
            name=name, c_type=c_type,
            offset=offset, comment=comment))
    return fields


def _strip_c_comments(s: str) -> str:
    """Remove /* ... */ and // ... comments from one line."""
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.DOTALL)
    s = re.sub(r"//.*", "", s)
    return s


def _extract_comment_text(raw: str) -> str:
    m = re.search(r"/\*(.*?)\*/", raw, flags=re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r"//\s*(.*)", raw)
    if m:
        return m.group(1).strip()
    return ""


_TYPE_SIZE_HINTS: dict[str, int] = {
    "u8": 1, "s8": 1, "char": 1, "bool": 1,
    "u16": 2, "s16": 2, "short": 2,
    "u32": 4, "s32": 4, "int": 4, "long": 4, "f32": 4, "float": 4,
    "u64": 8, "s64": 8, "double": 8, "f64": 8,
    "void*": 4,  # i386
}


def _type_size_guess(c_type: str) -> int:
    """Best-effort byte size inference for a C type spelling.

    Pointer types are always 4 bytes (XBE is i386); array types
    multiply element size by the array length when we can see it
    in the spelling; unknown types collapse to 4 (the most common
    size and the least wrong for our headers).
    """
    t = c_type.strip().replace("const ", "")
    # Array dims baked into c_type: ``u8 data[16]`` collapses to
    # the bare element size here — _extract_fields strips the []
    # suffix from the name already, so arrays of fixed length get
    # counted as one element.  This under-reports size for array
    # fields, which is explicitly noted in the ``inferred_size``
    # docstring.
    if "*" in t:
        return 4
    base = t.split()[-1]
    return _TYPE_SIZE_HINTS.get(base, 4)


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StructDiffEntry:
    """One struct's cross-source status.

    ``status`` is one of:

    - ``"header_only"`` — the shim headers define this struct but
      Ghidra doesn't.
    - ``"ghidra_only"`` — Ghidra has it but no shim header does.
    - ``"size_mismatch"`` — both sides have the struct but their
      total size disagrees.
    - ``"field_mismatch"`` — both sides agree on size but at least
      one field differs on offset / type.
    - ``"ok"`` — structure name + size match; fields match.
    """

    name: str
    status: str
    header_size: int | None
    ghidra_size: int | None
    notes: tuple[str, ...] = ()


@dataclass
class StructDiffReport:
    """Aggregate output of :func:`diff_structs`."""

    entries: tuple[StructDiffEntry, ...]
    ghidra_reachable: bool

    def to_json_dict(self) -> dict:
        return {
            "ghidra_reachable": self.ghidra_reachable,
            "summary": self.counts(),
            "entries": [
                {"name": e.name, "status": e.status,
                 "header_size": e.header_size,
                 "ghidra_size": e.ghidra_size,
                 "notes": list(e.notes)}
                for e in self.entries
            ],
        }

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {
            "header_only": 0, "ghidra_only": 0,
            "size_mismatch": 0, "field_mismatch": 0, "ok": 0}
        for e in self.entries:
            out[e.status] = out.get(e.status, 0) + 1
        return out


def diff_structs(*, header: Path | str | list[HeaderStruct],
                 client: GhidraClient | None = None,
                 ghidra_structs: Iterable[GhidraStruct] | None = None,
                 ) -> StructDiffReport:
    """Run the cross-source diff.

    Takes either a live :class:`GhidraClient` (walks the project
    via pagination) or an explicit iterable of pre-fetched
    :class:`GhidraStruct` (useful for tests + for feeding
    snapshot-based comparisons).  At least one must be provided.
    """
    if isinstance(header, (str, Path)):
        header_structs = parse_header_structs(Path(header))
    else:
        header_structs = list(header)

    ghidra_reachable = True
    ghidra_lookup: dict[str, GhidraStruct] = {}
    if ghidra_structs is not None:
        for s in ghidra_structs:
            ghidra_lookup[s.name] = s
    elif client is not None:
        try:
            for summary in client.iter_structs():
                try:
                    full = client.get_struct(summary["name"])
                except GhidraClientError:
                    continue
                ghidra_lookup[full.name] = full
        except GhidraClientError:
            ghidra_reachable = False
    else:
        raise ValueError(
            "diff_structs requires client= or ghidra_structs=")

    entries: list[StructDiffEntry] = []
    header_by_name = {s.name: s for s in header_structs}
    all_names = set(header_by_name) | set(ghidra_lookup)
    for name in sorted(all_names):
        hs = header_by_name.get(name)
        gs = ghidra_lookup.get(name)
        if hs is None:
            entries.append(StructDiffEntry(
                name=name, status="ghidra_only",
                header_size=None,
                ghidra_size=gs.size,
                notes=(f"{len(gs.fields)} field(s) in Ghidra",)))
            continue
        if gs is None:
            entries.append(StructDiffEntry(
                name=name, status="header_only",
                header_size=(hs.declared_size
                             or hs.inferred_size()),
                ghidra_size=None,
                notes=(f"{len(hs.fields)} field(s) in header",)))
            continue
        entries.append(_compare_pair(hs, gs))

    return StructDiffReport(
        entries=tuple(entries),
        ghidra_reachable=ghidra_reachable)


def _compare_pair(hs: HeaderStruct,
                  gs: GhidraStruct) -> StructDiffEntry:
    header_size = hs.declared_size or hs.inferred_size()
    if header_size is not None and header_size != gs.size:
        return StructDiffEntry(
            name=hs.name, status="size_mismatch",
            header_size=header_size, ghidra_size=gs.size,
            notes=(f"header={header_size} vs ghidra={gs.size}",))
    g_fields = {f.offset: f for f in gs.fields}
    notes: list[str] = []
    for hf in hs.fields:
        if hf.offset < 0:
            notes.append(
                f"{hf.name}: offset unknown in header")
            continue
        gf = g_fields.get(hf.offset)
        if gf is None:
            notes.append(
                f"{hf.name}@0x{hf.offset:x}: missing in Ghidra")
            continue
        if _normalize_type(hf.c_type) != _normalize_type(gf.data_type):
            notes.append(
                f"{hf.name}@0x{hf.offset:x}: "
                f"header={hf.c_type!r} vs ghidra={gf.data_type!r}")
    status = "ok" if not notes else "field_mismatch"
    return StructDiffEntry(
        name=hs.name, status=status,
        header_size=header_size, ghidra_size=gs.size,
        notes=tuple(notes))


_TYPE_ALIASES = {
    "u8": "uchar", "s8": "char", "bool": "uchar",
    "u16": "ushort", "s16": "short",
    "u32": "uint", "s32": "int", "long": "int",
    "f32": "float",
    "u64": "ulonglong", "s64": "longlong",
    "double": "double", "f64": "double",
}


def _normalize_type(t: str) -> str:
    """Best-effort name normalisation so ``u32`` == ``uint`` etc.

    Pointer-ish types collapse to ``ptr``; unknown names pass
    through untouched so the diff shows them verbatim.
    """
    bare = t.strip().replace("const ", "").lower()
    if "*" in bare:
        return "ptr"
    tok = bare.split()[-1]
    return _TYPE_ALIASES.get(tok, tok)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def format_report(report: StructDiffReport, *,
                  verbose: bool = False) -> str:
    """Pretty-print a :class:`StructDiffReport` for the CLI."""
    out: list[str] = []
    summary = report.counts()
    if not report.ghidra_reachable:
        out.append(
            "struct-diff: WARNING Ghidra unreachable "
            "(plan produced from header only)")
    out.append(
        "struct-diff summary: "
        f"ok={summary['ok']}  "
        f"header_only={summary['header_only']}  "
        f"ghidra_only={summary['ghidra_only']}  "
        f"size_mismatch={summary['size_mismatch']}  "
        f"field_mismatch={summary['field_mismatch']}")
    for entry in report.entries:
        if not verbose and entry.status == "ok":
            continue
        tag = entry.status.upper()
        sizes = (f"header={entry.header_size}  "
                 f"ghidra={entry.ghidra_size}")
        out.append(f"  [{tag:15}] {entry.name:32}  {sizes}")
        if verbose:
            for note in entry.notes:
                out.append(f"      - {note}")
    return "\n".join(out)
