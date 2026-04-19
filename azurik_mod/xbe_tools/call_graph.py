"""Call-graph explorer — #20 from ``docs/TOOLING_ROADMAP.md``.

Walks Ghidra's xref graph starting from a seed VA and emits either
a Graphviz ``.dot`` file (for dot/xdot/graphviz rendering) or a
flat list of ``(caller, callee)`` edges (for piping into
``sort``/``uniq`` / further analysis).

## Relationship to the xref aggregator

The xref aggregator (:mod:`.xref_aggregator`) produces a *tree*
rooted at one address — good for *"who calls me, three levels
deep?"*.  The call-graph explorer produces a *graph* — it tracks
every edge it discovers and emits the full DAG, which is what you
want for diagrams, cluster analysis, and tooling integrations.

Both share the Ghidra client, but the call-graph explorer:

- Emits every distinct **edge** (not just parent/child tree hops).
- Can start from multiple seeds and merge the results.
- Exports Graphviz ``.dot`` with function names as node labels.
- Recognises SCCs implicitly by revisiting the same edge multiple
  times during rendering (only once per ``(src, dst)``).

## Default output

::

    digraph call_graph {
      rankdir=LR;
      "0x00085700" [label="FUN_00085700\\n0x00085700"];
      "0x00085f50" [label="FUN_00085f50\\n0x00085f50"];
      "0x00085f50" -> "0x00085700" [label="CALL"];
    }

## Performance notes

We deliberately DON'T cache xref queries across calls — the caller
is typically the decomp cache (:mod:`.decomp_cache`) for decomps,
and for xrefs the aggregator keeps its own per-run visited set.
If you need cross-run persistence for xrefs, add a second cache —
but that hasn't shown up in any real workflow yet, so we YAGNI it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal

from azurik_mod.xbe_tools.ghidra_client import (
    GhidraClient,
    GhidraClientError,
    GhidraXref,
)

__all__ = [
    "CallGraph",
    "CallGraphEdge",
    "build_call_graph",
    "to_dot",
]


Direction = Literal["forward", "reverse"]


@dataclass(frozen=True)
class CallGraphEdge:
    """One directed edge in the graph.  Keyed by
    ``(src, dst, ref_type)`` so alternating call / data xrefs stay
    distinct."""

    src: int
    dst: int
    ref_type: str
    from_instruction: str = ""


@dataclass
class CallGraph:
    """Aggregated graph of xref edges discovered from one or more
    seed VAs.

    Instances are lightweight — a set of edges plus a dict of
    known node labels.  All heavy lifting happens during the
    initial :func:`build_call_graph` walk.
    """

    seeds: tuple[int, ...]
    direction: Direction
    edges: set[CallGraphEdge] = field(default_factory=set)
    nodes: dict[int, str] = field(default_factory=dict)
    max_depth: int = 2
    truncated: bool = False

    def edge_count(self) -> int:
        return len(self.edges)

    def node_count(self) -> int:
        return len(self.nodes)

    def iter_edges(self) -> Iterable[CallGraphEdge]:
        """Edges sorted by ``(src, dst)`` so downstream callers get
        a stable ordering — particularly helpful for snapshot
        tests and diffs across runs."""
        return sorted(self.edges,
                      key=lambda e: (e.src, e.dst, e.ref_type))

    def to_json_dict(self) -> dict:
        return {
            "seeds": [f"0x{s:08x}" for s in self.seeds],
            "direction": self.direction,
            "max_depth": self.max_depth,
            "truncated": self.truncated,
            "nodes": {
                f"0x{va:08x}": name for va, name in sorted(
                    self.nodes.items())},
            "edges": [
                {"src": f"0x{e.src:08x}",
                 "dst": f"0x{e.dst:08x}",
                 "ref_type": e.ref_type,
                 "from_instruction": e.from_instruction}
                for e in self.iter_edges()
            ],
        }


def build_call_graph(client: GhidraClient, *,
                     seeds: Iterable[int],
                     direction: Direction = "forward",
                     max_depth: int = 2,
                     max_edges: int = 500) -> CallGraph:
    """BFS over the xref graph.

    - ``direction="forward"`` walks callee links (*what does the seed
      eventually invoke?*).
    - ``direction="reverse"`` walks caller links (*who ultimately
      invokes the seed?*).

    Caps expansion at ``max_edges`` — when the cap is hit the graph
    is marked ``truncated=True`` and further expansion stops; the
    partial graph is still returned so tools can render what we
    have.
    """
    if max_depth < 0:
        raise ValueError("max_depth must be >= 0")

    seeds_tuple = tuple(seeds)
    graph = CallGraph(
        seeds=seeds_tuple,
        direction=direction,
        max_depth=max_depth,
    )

    # Seed the node table with names (best-effort).
    for seed in seeds_tuple:
        _annotate_node(client, graph, seed)

    visited: set[int] = set(seeds_tuple)
    frontier: list[int] = list(seeds_tuple)

    for _depth in range(max_depth):
        next_frontier: list[int] = []
        for addr in frontier:
            if len(graph.edges) >= max_edges:
                graph.truncated = True
                break
            try:
                if direction == "forward":
                    edges = list(client.iter_xrefs_from(addr))
                else:
                    edges = list(client.iter_xrefs_to(addr))
            except GhidraClientError:
                continue
            for edge in edges:
                src, dst = _orient_edge(edge, direction)
                if src is None or dst is None:
                    continue
                _annotate_node(client, graph, src,
                               preferred_name=edge.from_function_name)
                _annotate_node(client, graph, dst,
                               preferred_name=edge.to_function_name)
                ce = CallGraphEdge(
                    src=src, dst=dst,
                    ref_type=edge.ref_type,
                    from_instruction=edge.from_instruction)
                graph.edges.add(ce)
                other = dst if direction == "forward" else src
                if other not in visited:
                    visited.add(other)
                    next_frontier.append(other)
                if len(graph.edges) >= max_edges:
                    graph.truncated = True
                    break
            if graph.truncated:
                break
        if graph.truncated or not next_frontier:
            break
        frontier = next_frontier

    return graph


def _orient_edge(edge: GhidraXref,
                 direction: Direction,
                 ) -> tuple[int | None, int | None]:
    """Return ``(src, dst)`` in program-semantic order.

    Regardless of which side of the edge the traversal started
    from, ``src`` is always the caller (or data-emitter) and
    ``dst`` is always the callee (or data target).  Intra-
    function calls collapse onto their enclosing function VAs
    so the graph stays small and readable.

    ``direction`` is only used by the caller to decide which
    frontier side to chase — the edge itself has a fixed
    call/data orientation regardless of walk direction.
    """
    del direction  # kept for API clarity; semantic is direction-agnostic
    caller_va = edge.from_function_va or edge.from_addr
    callee_va = edge.to_function_va or edge.to_addr
    return caller_va, callee_va


def _annotate_node(client: GhidraClient, graph: CallGraph,
                   va: int,
                   *, preferred_name: str | None = None) -> None:
    """Ensure ``va`` is in ``graph.nodes`` with its best-known
    name.  Uses :meth:`GhidraClient.get_function` as a fallback
    when the edge payload didn't include a name (can happen for
    data xrefs)."""
    if va in graph.nodes and graph.nodes[va]:
        return
    name = preferred_name
    if not name:
        try:
            name = client.get_function(va).name
        except GhidraClientError:
            name = ""
    graph.nodes[va] = name or ""


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def to_dot(graph: CallGraph, *, title: str | None = None) -> str:
    """Emit a Graphviz ``.dot`` document.

    ``rankdir=LR`` is the default because call graphs read more
    naturally left-to-right (caller → callee) than top-to-bottom.
    Pass the returned string to ``dot -Tpng`` / ``xdot`` for a
    visual.
    """
    lines: list[str] = []
    header = title or "call_graph"
    lines.append(f"digraph {_dot_ident(header)} {{")
    lines.append("  rankdir=LR;")
    lines.append("  node [shape=box, fontname=\"Courier\"];")
    for va in sorted(graph.nodes):
        name = graph.nodes[va] or "??"
        label = f"{name}\\n0x{va:08x}"
        lines.append(
            f"  \"0x{va:08x}\" [label=\"{label}\"];")
    for edge in graph.iter_edges():
        rt = edge.ref_type or ""
        lines.append(
            f"  \"0x{edge.src:08x}\" -> \"0x{edge.dst:08x}\" "
            f"[label=\"{_short_ref_type(rt)}\"];")
    if graph.truncated:
        lines.append("  // NOTE: graph truncated at max_edges")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _dot_ident(s: str) -> str:
    """Normalise ``s`` into a safe Graphviz identifier (no quoting
    needed for the resulting graph name)."""
    out = []
    for ch in s:
        if ch.isalnum() or ch == "_":
            out.append(ch)
        else:
            out.append("_")
    ident = "".join(out).strip("_") or "g"
    if ident[0].isdigit():
        ident = "_" + ident
    return ident


def _short_ref_type(rt: str) -> str:
    """Collapse long ref-type strings into UI-friendly labels."""
    return {
        "UNCONDITIONAL_CALL": "CALL",
        "CONDITIONAL_CALL": "CALL?",
        "UNCONDITIONAL_JUMP": "JMP",
        "CONDITIONAL_JUMP": "JMP?",
        "COMPUTED_CALL": "CALL*",
        "COMPUTED_JUMP": "JMP*",
        "READ": "READ",
        "WRITE": "WRITE",
        "DATA": "DATA",
    }.get(rt, rt)
