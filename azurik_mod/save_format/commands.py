"""CLI handlers for the ``save`` subcommand.

Split into a standalone module rather than inlined in :mod:`cli`
because save-format code doesn't need to live on the hot path of
ordinary patch/randomize runs.  Lazy-imported from ``cli.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .azurik import AzurikSave
from .container import SaveDirectory


def cmd_save_inspect(args) -> None:
    """Handle ``azurik-mod save inspect <path>`` — human / JSON summary.

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

    # Attach decoded per-file summaries for every .sav file (root +
    # nested under levels/).  Individual failures get surfaced per-
    # entry so one malformed file doesn't abort the whole scan.
    sav_summaries: list[dict] = []
    for name, sav_path in sorted(slot.sav_files.items()):
        try:
            sav = AzurikSave.from_path(sav_path)
        except ValueError as e:
            sav_summaries.append({"file": name, "error": str(e)})
            continue
        entry = sav.summary()
        entry["relpath"] = name
        sav_summaries.append(entry)
    summary["sav_details"] = sav_summaries

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
        return

    _print_human_summary(summary)


def _inspect_single_sav(path: Path, *, as_json: bool) -> None:
    """Summarise a single ``.sav`` file (no surrounding container)."""
    sav = AzurikSave.from_path(path)
    summary = sav.summary()
    if as_json:
        print(json.dumps(summary, indent=2, default=str))
        return

    print(f"file:    {summary['path']}")
    print(f"kind:    {summary['kind']}")
    print(f"size:    {summary['size_bytes']} bytes")
    if summary["kind"] == "text":
        print(f"version: {summary.get('version')}")
        print(f"lines:   {summary.get('lines')}")
        print(f"binary_tail: {summary.get('binary_tail_bytes')} B")
        for line in summary.get("preview", []):
            print(f"  | {line}")
    elif summary["kind"] == "binary":
        print(f"version:      {summary.get('version')}")
        print(f"record_count: {summary.get('record_count')}")
        print(f"body:         {summary.get('body_bytes')} bytes")
    elif summary["kind"] == "signature":
        print(f"sha1_hex: {summary.get('sha1_hex')}")


def _print_human_summary(summary: dict) -> None:
    print(f"save directory: {summary['path']}")
    print(f"  display name:     {summary['save_name']!r}")
    print(f"  title name:       {summary['title_name']!r}")
    print(f"  no-copy flag:     {summary['no_copy']}")
    print(f"  save image:       {summary['save_image_bytes']} bytes")
    print(f"  title image:      {summary['title_image_bytes']} bytes")
    if summary.get("extra_files"):
        print(f"  extra files:      {summary['extra_files']}")
    print()

    details = summary.get("sav_details", [])
    if not details:
        print("  no .sav files found")
        return

    # Split root vs level-nested for readability.
    root = [d for d in details if "/" not in d.get("relpath", "")]
    level = [d for d in details if d.get("relpath", "").startswith("levels/")]
    other = [d for d in details
             if "/" in d.get("relpath", "")
             and not d.get("relpath", "").startswith("levels/")]

    def _fmt_entry(d: dict) -> str:
        if "error" in d:
            return f"    {d['relpath']}: ERROR — {d['error']}"
        kind = d.get("kind", "?")
        size = d.get("size_bytes", "?")
        extra = ""
        if kind == "text":
            extra = (f"  v{d.get('version')}  "
                     f"{d.get('lines')} lines + "
                     f"{d.get('binary_tail_bytes')} B tail")
        elif kind == "binary":
            extra = (f"  v{d.get('version')}  "
                     f"{d.get('record_count')} records, "
                     f"{d.get('body_bytes')} B body")
        elif kind == "signature":
            extra = f"  sha1={d.get('sha1_hex', '')[:16]}..."
        return (f"    {Path(d.get('relpath','?')).name:24s} "
                f"[{kind:9s}]  {size:>6} B{extra}")

    print(f"  {len(details)} .sav file(s) — "
          f"{len(root)} root / {len(level)} under levels/"
          + (f" / {len(other)} other" if other else "") + ":")
    for d in root:
        print(_fmt_entry(d))
    if level:
        print(f"\n  Level saves:")
        for d in level:
            print(_fmt_entry(d))
    if other:
        print(f"\n  Other nested:")
        for d in other:
            print(_fmt_entry(d))
