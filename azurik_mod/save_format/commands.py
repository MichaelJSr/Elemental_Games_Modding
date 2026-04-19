"""CLI handlers for the ``save`` subcommand.

Split into a standalone module rather than inlined in :mod:`cli`
because save-format code doesn't need to live on the hot path of
ordinary patch/randomize runs.  Lazy-imported from ``cli.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .azurik import AzurikSaveFile
from .container import SaveDirectory


def cmd_save_inspect(args) -> None:
    """Handle ``azurik-cli save inspect <path>`` — human / JSON summary.

    ``<path>`` may be either a single ``.sav`` file or a directory
    containing the Xbox save-container bundle.  Missing files are
    skipped cleanly — the command never errors on a partially-
    complete export.
    """
    path = Path(args.path)
    if not path.exists():
        print(f"error: {path} does not exist", file=sys.stderr)
        raise SystemExit(2)

    if path.is_file():
        _inspect_single_sav(path, as_json=args.json)
        return

    # Directory — full save-slot inspection.
    slot = SaveDirectory.from_directory(path)
    summary = slot.summary()

    # Attach decoded per-file summaries for any .sav files.
    sav_summaries: list[dict] = []
    for name, sav_path in sorted(slot.sav_files.items()):
        try:
            sav = AzurikSaveFile.from_path(sav_path)
        except ValueError as e:
            sav_summaries.append({
                "file": name,
                "error": str(e),
            })
            continue
        sav_summaries.append(sav.summary())
    summary["sav_details"] = sav_summaries

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
        return

    _print_human_summary(summary)


def _inspect_single_sav(path: Path, *, as_json: bool) -> None:
    """Summarise a single ``.sav`` file (no surrounding container)."""
    sav = AzurikSaveFile.from_path(path)
    summary = sav.summary()
    if as_json:
        print(json.dumps(summary, indent=2, default=str))
        return

    print(f"file:     {summary['path']}")
    print(f"size:     {summary['size_bytes']} bytes")
    print(f"header:")
    for k, v in summary["header"].items():
        print(f"  {k:18s} {v}")
    print(f"payload:  {summary['payload_actual_bytes']} bytes")
    status = (
        "OK" if summary["payload_declared_matches_actual"]
        else "MISMATCH (header.payload_len != actual)"
    )
    print(f"  payload_len check: {status}")


def _print_human_summary(summary: dict) -> None:
    print(f"save directory: {summary['path']}")
    print(f"  display name:     {summary['save_name']!r}")
    print(f"  title name:       {summary['title_name']!r}")
    print(f"  no-copy flag:     {summary['no_copy']}")
    print(f"  save image:       {summary['save_image_bytes']} bytes")
    print(f"  title image:      {summary['title_image_bytes']} bytes")
    if summary["extra_files"]:
        print(f"  extra files:      {summary['extra_files']}")
    print()
    details = summary.get("sav_details", [])
    if not details:
        print("  no .sav files found")
        return
    print(f"  {len(details)} .sav file(s):")
    for d in details:
        if "error" in d:
            print(f"    {d['file']}: ERROR — {d['error']}")
            continue
        path = Path(d["path"])
        h = d["header"]
        print(f"    {path.name:24s}  "
              f"magic={h['magic']} ({h['magic_ascii']!r})  "
              f"ver={h['version']}  "
              f"payload={d['payload_actual_bytes']}B  "
              f"match={d['payload_declared_matches_actual']}")
