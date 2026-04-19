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

    live_client = None
    if getattr(args, "live", False):
        from .ghidra_client import GhidraClient
        live_client = GhidraClient(
            host=args.host or "localhost",
            port=args.port or 8193)

    report = build_coverage_report(
        snapshot_path=Path(args.snapshot) if args.snapshot else None,
        live_client=live_client)

    if args.json:
        _emit(report.to_json_dict(), as_json=True)
    else:
        print(format_report(report))


# ---------------------------------------------------------------------------
# ghidra-sync — push Python-side knowledge to a live Ghidra
# ---------------------------------------------------------------------------

def cmd_ghidra_sync(args) -> None:
    """Dispatch into :mod:`.ghidra_sync`."""
    from .ghidra_client import GhidraClient, GhidraClientError
    from .ghidra_sync import apply_sync, format_plan, plan_sync

    client = GhidraClient(
        host=args.host or "localhost",
        port=args.port or 8193)
    if not client.ping():
        print(f"ghidra-sync: no Ghidra instance reachable at "
              f"{client.base_url}", file=sys.stderr)
        sys.exit(2)

    try:
        actions = plan_sync(client)
    except GhidraClientError as exc:
        print(f"ghidra-sync: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        _emit([{
            "va": a.va, "kind": a.kind,
            "current_name": a.current_name,
            "new_name": a.new_name,
            "comment": a.comment,
            "rationale": a.rationale,
        } for a in actions], as_json=True)
    else:
        print(format_plan(actions))

    if args.apply:
        print("\nApplying actions to "
              f"{client.base_url}  (force={args.force})")
        report = apply_sync(client, actions, force=args.force)
        print(f"  attempted:  {report.attempted}")
        print(f"  renamed:    {report.renamed}")
        print(f"  commented:  {report.commented}")
        print(f"  skipped:    {report.skipped}")
        if report.errors:
            print(f"  errors     ({len(report.errors)}):")
            for err in report.errors[:20]:
                print(f"    - {err}")
            sys.exit(1)


# ---------------------------------------------------------------------------
# shim-inspect
# ---------------------------------------------------------------------------

def cmd_shim_inspect(args) -> None:
    """Dispatch into :mod:`.shim_inspect`.

    Wraps :func:`~azurik_mod.xbe_tools.shim_inspect.inspect_object`
    with user-facing error handling: bad paths and non-COFF files
    produce a single-line stderr message + exit 1, not a traceback.
    """
    from .shim_inspect import inspect_object, format_inspection

    target = Path(args.target).expanduser().resolve()
    try:
        result = inspect_object(target)
    except FileNotFoundError as exc:
        print(f"shim-inspect: {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"shim-inspect: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        _emit(result.to_json_dict(), as_json=True)
    else:
        print(format_inspection(result))


# ---------------------------------------------------------------------------
# test-for-va — run just the test classes that reference a given VA / pack
# ---------------------------------------------------------------------------

def cmd_test_for_va(args) -> None:
    """``azurik-mod test-for-va 0xHEX|PACK_NAME [--run]`` — find
    test classes that reference the target + optionally run pytest
    on just that subset."""
    from .test_selector import find_matches, run_pytest
    from pathlib import Path as _P

    # Resolve the tests dir relative to the repo (where the CLI
    # was invoked from).  Override with --tests-dir for out-of-tree
    # runs.
    tests_dir = _P(args.tests_dir).resolve() if args.tests_dir else _P.cwd() / "tests"
    if not tests_dir.is_dir():
        print(f"test-for-va: tests directory not found: {tests_dir}",
              file=sys.stderr)
        sys.exit(2)

    # Decide: is the target a hex VA or a pack name?
    target = args.target
    va: int | None = None
    pack: str | None = None
    try:
        va = int(target, 0)
    except ValueError:
        pack = target

    try:
        matches = find_matches(va=va, pack=pack, tests_dir=tests_dir)
    except ValueError as exc:
        print(f"test-for-va: {exc}", file=sys.stderr)
        sys.exit(2)

    if args.json:
        _emit([{
            "file": m.file.as_posix(),
            "class": m.class_name,
            "selector": m.pytest_selector(),
            "hit_lines": list(m.hit_lines),
        } for m in matches], as_json=True)
    else:
        if not matches:
            print(f"(no test classes reference "
                  f"{'VA ' + hex(va) if va is not None else 'pack ' + repr(pack)})")
        else:
            print(f"{len(matches)} test class(es) reference "
                  f"{'VA ' + hex(va) if va is not None else 'pack ' + repr(pack)}:")
            for m in matches:
                line_hint = (f"  (lines: {', '.join(map(str, m.hit_lines[:4]))}"
                             + ("…" if len(m.hit_lines) > 4 else "") + ")")
                print(f"  {m.pytest_selector()}{line_hint}")

    if args.run:
        rc = run_pytest(matches,
                        extra_args=list(args.pytest_args or []))
        sys.exit(rc)


# ---------------------------------------------------------------------------
# plan-trampoline — size a hook site before writing any shim
# ---------------------------------------------------------------------------

def cmd_plan_trampoline(args) -> None:
    """``azurik-mod plan-trampoline VA [--budget 5]`` — decode the
    instructions at VA, suggest a trampoline length, flag any
    multi-byte instructions the shim must preserve."""
    from .trampoline_planner import plan_trampoline, format_plan

    xbe = _load_xbe(args)
    try:
        va = int(args.va, 0)
    except ValueError:
        print(f"plan-trampoline: bad VA {args.va!r}", file=sys.stderr)
        sys.exit(2)
    plan = plan_trampoline(xbe, va,
                           budget=args.budget,
                           window=args.window)
    if args.json:
        _emit({
            "va": plan.va, "file_offset": plan.file_offset,
            "budget": plan.budget,
            "suggested_length": plan.suggested_length,
            "clean_boundary": plan.clean_boundary,
            "warnings": plan.warnings,
            "preserved_mnemonics": plan.preserved_mnemonics,
            "instructions": [{
                "offset": i.offset, "length": i.length,
                "mnemonic": i.mnemonic, "bytes": i.raw.hex(),
            } for i in plan.instructions],
        }, as_json=True)
    else:
        print(format_plan(plan))
    # Clean boundary → exit 0; warnings present → exit 1 so CI
    # wrappers can distinguish "needs review" from "error".
    sys.exit(0 if plan.clean_boundary else 1)


# ---------------------------------------------------------------------------
# entity diff — side-by-side config.xbr property compare
# ---------------------------------------------------------------------------

def cmd_entity_diff(args) -> None:
    """``azurik-mod entity diff A B``"""
    from .entity_diff import diff_entities, format_diff

    config_path = args.config
    if config_path is None and args.iso is not None:
        # Extract config.xbr from the ISO into a temp file.
        import tempfile
        from azurik_mod.iso.pack import extract_config_from_iso
        data = bytes(extract_config_from_iso(Path(args.iso)))
        tmp = tempfile.NamedTemporaryFile(
            suffix=".xbr", delete=False)
        tmp.write(data); tmp.close()
        config_path = tmp.name

    if config_path is None:
        # Try repo-local unpacked ISO as a convenience.
        guess = (Path(__file__).resolve().parents[3] /
                 "Azurik - Rise of Perathia (USA).xiso" /
                 "gamedata" / "config.xbr")
        if guess.exists():
            config_path = str(guess)
        else:
            print("entity diff: pass --config PATH or --iso PATH",
                  file=sys.stderr)
            sys.exit(2)

    try:
        diff = diff_entities(config_path, args.entity_a, args.entity_b,
                             include_equal=args.all)
    except (FileNotFoundError, ValueError) as exc:
        print(f"entity diff: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        _emit(diff.to_json_dict(include_equal=args.all), as_json=True)
    else:
        print(format_diff(diff, include_equal=args.all))


# ---------------------------------------------------------------------------
# xbr inspect — record-layout classifier
# ---------------------------------------------------------------------------

def cmd_xbr_inspect(args) -> None:
    """``azurik-mod xbr inspect FILE --tag TAG``"""
    from .xbr_inspect import inspect_xbr, format_inspection

    try:
        insp = inspect_xbr(
            args.path, tag=args.tag,
            entries=args.entries, stride=args.stride,
            fields_per_row=args.fields_per_row)
    except (FileNotFoundError, ValueError) as exc:
        print(f"xbr inspect: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        _emit({
            "tag": insp.tag,
            "stride": insp.stride,
            "auto_detected_stride": insp.auto_detected_stride,
            "records": [{
                "index": r.index,
                "file_offset": r.file_offset,
                "fields": [{
                    "offset": f.offset, "raw": f.raw.hex(),
                    "best_type": f.best_type,
                    "value_display": f.value_display,
                } for f in r.fields],
            } for r in insp.records],
        }, as_json=True)
    else:
        print(format_inspection(insp))


__all__ = [
    "cmd_entity_diff",
    "cmd_ghidra_coverage",
    "cmd_ghidra_sync",
    "cmd_plan_trampoline",
    "cmd_shim_inspect",
    "cmd_test_for_va",
    "cmd_xbe_addr",
    "cmd_xbe_find_floats",
    "cmd_xbe_find_refs",
    "cmd_xbe_hexdump",
    "cmd_xbe_sections",
    "cmd_xbe_strings",
    "cmd_xbr_inspect",
]
