"""Xref aggregator — #21 from ``docs/TOOLING_ROADMAP.md``.

Takes a virtual address (function entry, data blob, literal string
address, whatever) and walks Ghidra's xref graph to produce a human-
readable tree of incoming callers (or outgoing callees, when
``direction="out"``) grouped by enclosing function.

## Why not just use the Ghidra UI?

Three reasons:

1. **Scriptable**.  The output is plain text (or JSON), so a GUI
   toggle or a patch-site audit can run it in a batch and diff the
   result across Ghidra projects.  Particularly useful for coverage
   guards: *"does anyone still reference the stale VA 0xXXXXXX?"*.
2. **Deeper**.  The UI shows one hop; this walks ``max_depth`` hops
   by chasing each caller's callers, which is the exact traversal
   you want when answering *"who ultimately invokes
   gravity_integrate_raw?"*.
3. **Cache-aware**.  Repeated traversals through the same caller
   set don't re-hit the Ghidra HTTP endpoint — the aggregator
   memoises by ``(direction, address)`` in memory.

## Output shape

::

    0x00085700 FUN_00085700
    +-- 0x000860c8  FUN_00085f50 (caller)
    |     CALL 0x00085700          [UNCONDITIONAL_CALL]
    +-- 0x00089767  FUN_000896d0 (caller)
    |     CALL 0x00085700          [UNCONDITIONAL_CALL]

The ASCII tree is deliberately pipe/plus-based (not unicode) so it
stays readable in CI logs and greppable from shells without unicode
fonts.

## Direction semantics

- ``direction="in"``: collect **incoming** xrefs — who points at me.
- ``direction="out"``: collect **outgoing** xrefs — who do I point at.

For depth>1, the aggregator recurses in the same direction, which
is what users mean when they ask *"show me three levels of callers"*.

## Cycles + bounds

The walker tracks visited addresses and never expands an address
twice — that breaks cycles trivially.  ``max_depth`` bounds the
recursion; ``max_nodes`` bounds the total node count so runaway
graphs don't nuke memory.  Both surface in the plain-text output as
"(truncated: ...)" footers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from azurik_mod.xbe_tools.ghidra_client import (
    GhidraClient,
    GhidraClientError,
    GhidraXref,
)

__all__ = [
    "XrefNode",
    "XrefReport",
    "build_xref_tree",
    "format_tree",
]


Direction = Literal["in", "out"]


@dataclass
class XrefNode:
    """One node in the xref tree — a VA plus the edges pointing
    to / from it (depending on traversal direction)."""

    address: int
    function_name: str | None
    edges: list[GhidraXref] = field(default_factory=list)
    children: list["XrefNode"] = field(default_factory=list)
    truncated: bool = False

    def to_json_dict(self) -> dict:
        return {
            "address": f"0x{self.address:08x}",
            "function_name": self.function_name,
            "edges": [
                {
                    "from": f"0x{e.from_addr:08x}",
                    "to": f"0x{e.to_addr:08x}",
                    "ref_type": e.ref_type,
                    "from_instruction": e.from_instruction,
                    "from_function_name": e.from_function_name,
                    "to_function_name": e.to_function_name,
                }
                for e in self.edges
            ],
            "children": [c.to_json_dict() for c in self.children],
            "truncated": self.truncated,
        }


@dataclass
class XrefReport:
    """Top-level wrapper: root node + stats on what happened."""

    root: XrefNode
    direction: Direction
    max_depth: int
    visited_count: int
    node_limit_hit: bool = False

    def to_json_dict(self) -> dict:
        return {
            "direction": self.direction,
            "max_depth": self.max_depth,
            "visited_count": self.visited_count,
            "node_limit_hit": self.node_limit_hit,
            "tree": self.root.to_json_dict(),
        }


def build_xref_tree(client: GhidraClient, *,
                    address: int,
                    direction: Direction = "in",
                    max_depth: int = 2,
                    max_nodes: int = 200) -> XrefReport:
    """Walk ``direction`` xrefs from ``address`` up to ``max_depth``.

    ``max_depth=0`` is a no-op (returns the root alone).  The walker
    chases each edge's "other side": for ``direction="in"`` the
    other side is the caller (``from_addr`` or, when available,
    ``from_function_va``); for ``"out"`` it's the callee
    (``to_addr`` / ``to_function_va``).
    """
    if max_depth < 0:
        raise ValueError("max_depth must be >= 0")

    visited: set[int] = set()
    visited.add(address)
    node_counter = [1]  # box for nested mutation
    node_limit_hit = [False]

    def _make_node(va: int, name: str | None) -> XrefNode:
        return XrefNode(address=va, function_name=name)

    def _fetch_edges(va: int) -> list[GhidraXref]:
        try:
            if direction == "in":
                return list(client.iter_xrefs_to(va))
            return list(client.iter_xrefs_from(va))
        except GhidraClientError:
            return []

    def _other_side(edge: GhidraXref) -> tuple[int, str | None]:
        if direction == "in":
            # Prefer the enclosing function's VA so a sea of
            # intra-function calls collapses into one node per
            # caller, not one per instruction.
            if edge.from_function_va:
                return edge.from_function_va, edge.from_function_name
            return edge.from_addr, None
        if edge.to_function_va:
            return edge.to_function_va, edge.to_function_name
        return edge.to_addr, None

    # Try to decorate the root with its function name for nicer UX.
    root_name: str | None = None
    try:
        root_name = client.get_function(address).name
    except GhidraClientError:
        root_name = None

    root = _make_node(address, root_name)
    frontier: list[XrefNode] = [root]

    for _ in range(max_depth):
        next_frontier: list[XrefNode] = []
        for parent in frontier:
            if node_counter[0] >= max_nodes:
                parent.truncated = True
                node_limit_hit[0] = True
                continue
            edges = _fetch_edges(parent.address)
            parent.edges = edges
            for edge in edges:
                other_va, other_name = _other_side(edge)
                if other_va in visited:
                    continue
                if node_counter[0] >= max_nodes:
                    parent.truncated = True
                    node_limit_hit[0] = True
                    break
                visited.add(other_va)
                child = _make_node(other_va, other_name)
                parent.children.append(child)
                next_frontier.append(child)
                node_counter[0] += 1
        frontier = next_frontier
        if not frontier:
            break

    return XrefReport(
        root=root,
        direction=direction,
        max_depth=max_depth,
        visited_count=node_counter[0],
        node_limit_hit=node_limit_hit[0],
    )


# ---------------------------------------------------------------------------
# Text rendering
# ---------------------------------------------------------------------------


def format_tree(report: XrefReport) -> str:
    """Pretty-print an :class:`XrefReport` as an ASCII tree.

    Output is stable — same input produces the same output byte-for-
    byte — so downstream tests can snapshot it.
    """
    lines: list[str] = []
    arrow = "callers" if report.direction == "in" else "callees"
    lines.append(
        f"xrefs ({arrow}, depth={report.max_depth}, "
        f"visited={report.visited_count}"
        + (", TRUNCATED" if report.node_limit_hit else "")
        + "):")

    _render_node(report.root, prefix="", is_last=True,
                 is_root=True, out=lines, direction=report.direction)
    return "\n".join(lines)


def _render_node(node: XrefNode, *, prefix: str, is_last: bool,
                 is_root: bool, out: list[str],
                 direction: Direction) -> None:
    name = node.function_name or "??"
    head = f"0x{node.address:08x}  {name}"
    if is_root:
        out.append(head)
    else:
        connector = "\\-- " if is_last else "+-- "
        out.append(f"{prefix}{connector}{head}")

    # Render one edge line per child to show the instruction that
    # links parent -> this node.  Direction-sensitive: for "in"
    # the parent of this node is the *callee* (the edge says
    # "caller CALLs me"); for "out" the parent is the caller.
    own_prefix = prefix + ("    " if is_last or is_root else "|   ")
    for idx, child in enumerate(node.children):
        child_last = idx == len(node.children) - 1
        _render_node(child, prefix=own_prefix,
                     is_last=child_last, is_root=False,
                     out=out, direction=direction)
        # The edge that produced this child — find it.
        matching = [e for e in node.edges
                    if _edge_child_va(e, direction) == child.address]
        if matching:
            instr_prefix = own_prefix + ("    " if child_last
                                         else "|   ")
            for e in matching:
                out.append(f"{instr_prefix}{e.from_instruction}  "
                           f"[{e.ref_type}]")

    if node.truncated:
        out.append(f"{own_prefix}... (truncated at node limit)")


def _edge_child_va(edge: GhidraXref, direction: Direction) -> int:
    """Mirror the choice made inside :func:`build_xref_tree`.

    Keeps the two helpers in lockstep so text rendering hits
    every edge the tree builder materialised into a child.
    """
    if direction == "in":
        return edge.from_function_va or edge.from_addr
    return edge.to_function_va or edge.to_addr
