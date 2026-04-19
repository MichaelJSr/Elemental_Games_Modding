"""Ghidra snapshot exporter — dump function + label state to JSON.

Tool #15 on the roadmap.  Pulls every function name + symbol /
label from a live Ghidra instance and writes the result to a
JSON file the rest of the toolchain can consume offline.

## Why

Three consumers want this:

1. **``ghidra-coverage --snapshot``** — runs without Ghidra open.
2. **Diff over time** — "did auto-analyze overwrite my nice
   names?" becomes a two-file diff instead of a memory game.
3. **Source of truth under version control** — tests can pin
   specific VA→name mappings against a committed snapshot.

## Scope

The shipped snapshot writes two top-level lists matching the
schema :mod:`ghidra_coverage` already loads:

- ``functions``: ``[{"address": "0x00085700", "name": "..."}, ...]``
- ``labels``:    ``[{"address": "...",          "name": "..."}, ...]``

Names that still match Ghidra's auto-label pattern (``FUN_*`` /
``LAB_*`` / ``DAT_*``) are EXCLUDED by default — otherwise the
snapshot balloons to ~4.5k function rows + ~45k label rows with
no informational value.  Pass ``include_default_names=True`` to
keep them.

## Size guard

Full Azurik snapshots: ~50 KB when filtered (named symbols
only), ~1.2 MB unfiltered.  Either is committable; the default
filter keeps git diffs readable.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from .ghidra_client import GhidraClient


def _is_default_name(name: str | None) -> bool:
    """Ghidra's auto-labels — symbols we don't need in a snapshot."""
    if not name:
        return True
    return (name.startswith("FUN_") or
            name.startswith("LAB_") or
            name.startswith("DAT_"))


@dataclass
class SnapshotStats:
    """Summary of what :func:`dump_snapshot` actually wrote."""

    total_functions: int = 0
    named_functions: int = 0
    total_labels: int = 0
    named_labels: int = 0
    total_structs: int = 0
    captured_structs: int = 0
    program_name: str = ""


def _format_address(addr: int | str) -> str:
    """Return an ``0xHHHHHHHH`` string; accepts either an int or
    a string (for EXTERNAL-namespaced addresses like
    ``EXTERNAL:00000001``)."""
    if isinstance(addr, int):
        return f"0x{addr:08X}"
    return str(addr)


def dump_snapshot(client: GhidraClient, *,
                  include_default_names: bool = False,
                  include_labels: bool = True,
                  include_structs: bool = True,
                  struct_name_prefixes: tuple[str, ...] = (),
                  ) -> tuple[dict, SnapshotStats]:
    """Pull a snapshot off ``client``.

    Parameters
    ----------
    client: GhidraClient
        Live Ghidra instance to query.
    include_default_names: bool
        When ``True``, keep ``FUN_*`` / ``LAB_*`` / ``DAT_*``
        rows.  Default ``False`` — filtered snapshots stay small
        and focus on the names a human actually assigned.
    include_labels: bool
        When ``True`` (default), paginate ``GET /symbols/labels``.
        Labels are numerous in Azurik (~45 k) so pulling them
        takes ~30 s over the wire; set to ``False`` for
        functions-only snapshots.

    Returns a ``(snapshot_dict, stats)`` pair.  ``snapshot_dict``
    has the exact schema ``ghidra_coverage.load_ghidra_snapshot``
    expects.
    """
    info = client.program_info()
    stats = SnapshotStats(program_name=info.name)

    functions: list[dict] = []
    for fn in client.iter_functions(page_size=1000):
        stats.total_functions += 1
        if not include_default_names and _is_default_name(fn.name):
            continue
        stats.named_functions += 1
        functions.append({
            "address": _format_address(fn.address),
            "name": fn.name,
            **({"signature": fn.signature} if fn.signature else {}),
        })

    structs: list[dict] = []
    if include_structs:
        # Only capture structs from our own category tree by
        # default — dumping the full Ghidra DTM (CRYPTO_VECTOR,
        # _CONTEXT, XBE headers, …) would add hundreds of KB of
        # kernel noise.  Pass ``struct_name_prefixes=("",)`` to
        # capture everything.
        our_prefixes = struct_name_prefixes or (
            # Keep this list in sync with the structs declared in
            # shims/include/azurik.h — every struct we push via
            # ``ghidra-sync --push-structs`` should show up in the
            # committed snapshot so offline consumers can see the
            # full layout.
            "Azurik",                                     # future namespacing
            "BootState", "BootStateCtx",
            "CritterData",
            "ControllerState",
            "PlayerInputState", "PlayerState", "PlayerPhysics",
            "Entity",
            "ConfigTable", "ConfigCell",
            "IndexEntry", "IndexRecord",
            "MovieContext", "MovieContextVTable",
            "SaveSlot", "SaveMeta",
            "XbeCertificate",
        )

        def _matches_our_prefixes(n: str) -> bool:
            return any(n.startswith(p) for p in our_prefixes)

        for summary in client.iter_structs(page_size=500):
            stats.total_structs += 1
            name = summary.get("name", "")
            if not _matches_our_prefixes(name):
                continue
            try:
                full = client.get_struct(name)
            except Exception:
                continue
            structs.append({
                "name": full.name,
                "size": full.size,
                "category": full.category,
                "description": full.description,
                "fields": [
                    {"name": f.name, "type": f.data_type,
                     "offset": f.offset, "length": f.length,
                     **({"comment": f.comment} if f.comment else {})}
                    for f in full.fields
                ],
            })
            stats.captured_structs += 1

    labels: list[dict] = []
    if include_labels:
        for lbl in client.iter_labels(page_size=1000):
            stats.total_labels += 1
            if not include_default_names and _is_default_name(lbl.name):
                continue
            stats.named_labels += 1
            labels.append({
                "address": _format_address(lbl.address),
                "name": lbl.name,
                "namespace": lbl.namespace,
                "type": lbl.symbol_type,
            })

    snapshot = {
        "schema": 1,
        "program": {
            "name": info.name,
            "image_base": f"0x{info.image_base:08X}",
            "memory_size": info.memory_size,
        },
        "functions": functions,
        "labels": labels,
        "structs": structs,
    }
    return snapshot, stats


def write_snapshot(path: str | Path, client: GhidraClient, *,
                   include_default_names: bool = False,
                   include_labels: bool = True,
                   include_structs: bool = True,
                   indent: int = 2) -> SnapshotStats:
    """Dump a snapshot to ``path``.  Returns the stats struct."""
    snapshot, stats = dump_snapshot(
        client,
        include_default_names=include_default_names,
        include_labels=include_labels,
        include_structs=include_structs)
    Path(path).expanduser().write_text(
        json.dumps(snapshot, indent=indent),
        encoding="utf-8")
    return stats


__all__ = [
    "SnapshotStats",
    "dump_snapshot",
    "write_snapshot",
]
