"""CLI dispatcher for ``azurik-mod xbe`` / ``ghidra-coverage`` /
``shim-inspect`` verbs.

Every handler here is a thin argparse wrapper around a pure-Python
analyser in the sibling modules (:mod:`.xbe_scan`,
:mod:`.ghidra_coverage`, :mod:`.shim_inspect`).  Keep formatting
code here; keep analysis logic in the analyser modules so tests
can exercise them without spawning a subprocess.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable

from azurik_mod.iso.pack import read_xbe_bytes


def _load_xbe(args) -> bytes:
    """Resolve ``args.iso`` / ``args.xbe`` into a bytes blob.

    ``--iso`` is preferred when both are present (so users can keep
    a patched ISO handy in their shell and toggle ``--xbe`` for
    ad-hoc reads).
    """
    if getattr(args, "iso", None):
        return bytes(read_xbe_bytes(Path(args.iso)))
    if getattr(args, "xbe", None):
        return Path(args.xbe).read_bytes()
    print("ERROR: pass --iso PATH.iso or --xbe PATH.xbe",
          file=sys.stderr)
    sys.exit(2)


def _emit(obj: Any, as_json: bool) -> None:
    """Print either human-readable text or machine-readable JSON."""
    if as_json:
        print(json.dumps(obj, indent=2))
    else:
        if isinstance(obj, list):
            for item in obj:
                print(item)
        else:
            print(obj)


# ---------------------------------------------------------------------------
# xbe addr
# ---------------------------------------------------------------------------

def cmd_xbe_addr(args) -> None:
    """``azurik-mod xbe addr <value>`` — resolve a number to (VA,
    file offset, section).  Handy for "is this a VA or a file
    offset" questions during RE."""
    from .xbe_scan import resolve_address

    xbe = _load_xbe(args)
    value = int(args.value, 0)  # accept 0x... or plain decimal
    force = None
    if args.from_ == "va":
        force = "va"
    elif args.from_ == "file":
        force = "file"
    info = resolve_address(xbe, value, force_kind=force)

    if args.json:
        _emit({
            "value": info.value,
            "kind": info.kind,
            "va": info.va,
            "file_offset": info.file_offset,
            "section": info.section,
        }, as_json=True)
    else:
        print(info.human)


# ---------------------------------------------------------------------------
# xbe hexdump
# ---------------------------------------------------------------------------

def cmd_xbe_hexdump(args) -> None:
    """``azurik-mod xbe hexdump <addr>`` — hexdump-style view with
    VA + file-offset columns."""
    from .xbe_scan import hex_dump

    xbe = _load_xbe(args)
    addr = int(args.address, 0)
    rows = hex_dump(xbe, addr, length=args.length, is_va=not args.file)
    if not rows:
        print(f"ERROR: address 0x{addr:X} is outside the XBE image",
              file=sys.stderr)
        sys.exit(1)
    if args.json:
        _emit([{"va": r.va, "file_offset": r.file_offset,
                "bytes": r.raw.hex()} for r in rows], as_json=True)
    else:
        for r in rows:
            print(r.format())


# ---------------------------------------------------------------------------
# xbe find-refs
# ---------------------------------------------------------------------------

def cmd_xbe_find_refs(args) -> None:
    """``azurik-mod xbe find-refs`` — which .text sites push a VA as
    imm32?  Accepts either a hex VA or ``--string`` to auto-locate
    the VA of a null-terminated string first."""
    from .xbe_scan import find_imm32_references, find_strings

    xbe = _load_xbe(args)

    if args.string is not None:
        # Locate the string first, then scan for refs to its start VA.
        hits = find_strings(xbe, args.string, min_len=len(args.string))
        if not hits:
            print(f"ERROR: no string matching {args.string!r} found",
                  file=sys.stderr)
            sys.exit(1)
        target_va = hits[0].va
        if not args.json:
            print(f"# target string {args.string!r} at VA 0x{target_va:X}")
    else:
        if args.va is None:
            print("ERROR: pass --va HEX or --string TEXT", file=sys.stderr)
            sys.exit(2)
        target_va = int(args.va, 0)

    refs = find_imm32_references(xbe, target_va)

    if args.json:
        _emit([{
            "callsite_va": r.callsite_va,
            "callsite_file_offset": r.callsite_file_offset,
            "kind": r.kind,
            "opcode": r.opcode,
        } for r in refs], as_json=True)
        return

    if not refs:
        print(f"(no .text references to VA 0x{target_va:X})")
        return
    print(f"{len(refs)} .text reference(s) to VA 0x{target_va:X}:")
    for r in refs:
        print(f"  {r.kind:10s}  VA 0x{r.callsite_va:08X}"
              f"   file 0x{r.callsite_file_offset:06X}"
              f"   opcode 0x{r.opcode:02X}")


# ---------------------------------------------------------------------------
# xbe find-floats
# ---------------------------------------------------------------------------

def cmd_xbe_find_floats(args) -> None:
    """``azurik-mod xbe find-floats MIN MAX`` — locate every IEEE
    754 float / double in .rdata whose value lies in [MIN, MAX]."""
    from .xbe_scan import find_floats_in_range

    xbe = _load_xbe(args)
    lo = float(args.min)
    hi = float(args.max)
    widths_map = {"float": (4,), "double": (8,), "both": (4, 8)}
    widths = widths_map.get(args.width, (4, 8))

    hits = find_floats_in_range(xbe, lo, hi, widths=widths)

    if args.json:
        _emit([{
            "va": h.va, "file_offset": h.file_offset,
            "width": h.width, "value": h.value,
            "hex_bytes": h.hex_bytes,
        } for h in hits], as_json=True)
        return

    print(f"{len(hits)} float(s) in [{lo}, {hi}]:")
    for h in hits:
        print(f"  VA 0x{h.va:08X}  file 0x{h.file_offset:06X}"
              f"   width={h.width}  value={h.value!r}  "
              f"bytes={h.hex_bytes}")


# ---------------------------------------------------------------------------
# xbe strings
# ---------------------------------------------------------------------------

def cmd_xbe_strings(args) -> None:
    """``azurik-mod xbe strings <pattern>`` — find strings in .rdata
    / .data by substring or regex."""
    from .xbe_scan import find_strings

    xbe = _load_xbe(args)
    hits = find_strings(
        xbe, args.pattern,
        min_len=args.min_len,
        regex=args.regex,
        limit=args.limit,
    )

    if args.json:
        _emit([{
            "va": h.va, "file_offset": h.file_offset,
            "text": h.text, "length": h.length,
        } for h in hits], as_json=True)
        return

    print(f"{len(hits)} string(s) matching {args.pattern!r}:")
    for h in hits:
        preview = h.text if len(h.text) < 80 else h.text[:77] + "..."
        print(f"  VA 0x{h.va:08X}  file 0x{h.file_offset:06X}"
              f"   len={h.length:4d}  {preview!r}")


# ---------------------------------------------------------------------------
# xbe sections
# ---------------------------------------------------------------------------

def cmd_xbe_sections(args) -> None:
    """``azurik-mod xbe sections`` — the XBE section table."""
    from azurik_mod.patching.xbe import parse_xbe_sections
    xbe = _load_xbe(args)
    base_addr, sections = parse_xbe_sections(xbe)

    if args.json:
        _emit([{
            "name": s["name"], "vaddr": s["vaddr"],
            "vsize": s["vsize"], "raw_addr": s["raw_addr"],
            "raw_size": s["raw_size"], "flags": s["flags"],
        } for s in sections], as_json=True)
        return

    print(f"base_addr = 0x{base_addr:08X}")
    print(f"{'name':<14s}  {'vaddr':<10s}  {'vsize':<10s}  "
          f"{'raw_addr':<10s}  {'raw_size':<10s}  {'flags':<10s}")
    for s in sections:
        print(f"{s['name']:<14s}  0x{s['vaddr']:08X}  "
              f"0x{s['vsize']:08X}  0x{s['raw_addr']:08X}  "
              f"0x{s['raw_size']:08X}  0x{s['flags']:08X}")


# ---------------------------------------------------------------------------
# ghidra-coverage
# ---------------------------------------------------------------------------

def cmd_ghidra_coverage(args) -> None:
    """Dispatch into :mod:`.ghidra_coverage`."""
    from .ghidra_coverage import build_coverage_report, format_report

    report = build_coverage_report(
        snapshot_path=Path(args.snapshot) if args.snapshot else None)

    if args.json:
        _emit(report.to_json_dict(), as_json=True)
    else:
        print(format_report(report))


# ---------------------------------------------------------------------------
# shim-inspect
# ---------------------------------------------------------------------------

def cmd_shim_inspect(args) -> None:
    """Dispatch into :mod:`.shim_inspect`."""
    from .shim_inspect import inspect_object, format_inspection

    target = Path(args.target).expanduser().resolve()
    result = inspect_object(target)

    if args.json:
        _emit(result.to_json_dict(), as_json=True)
    else:
        print(format_inspection(result))


__all__ = [
    "cmd_ghidra_coverage",
    "cmd_shim_inspect",
    "cmd_xbe_addr",
    "cmd_xbe_find_floats",
    "cmd_xbe_find_refs",
    "cmd_xbe_hexdump",
    "cmd_xbe_sections",
    "cmd_xbe_strings",
]
