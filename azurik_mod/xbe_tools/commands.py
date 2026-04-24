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

    # --va takes precedence over --string when both are given,
    # so users can't accidentally get the wrong lookup on a stale
    # shell-history command.  The default-value interaction is
    # made explicit in --help: "exactly one of --va or --string".
    if args.va is not None and args.string is not None:
        print("find-refs: both --va and --string passed; "
              "preferring --va.  Drop one to silence this warning.",
              file=sys.stderr)
    if args.va is not None:
        try:
            target_va = int(args.va, 0)
        except ValueError:
            print(f"ERROR: bad --va {args.va!r} (want hex or decimal)",
                  file=sys.stderr)
            sys.exit(2)
    elif args.string is not None:
        hits = find_strings(xbe, args.string, min_len=len(args.string))
        if not hits:
            print(f"ERROR: no string matching {args.string!r} found",
                  file=sys.stderr)
            sys.exit(1)
        target_va = hits[0].va
        if not args.json:
            print(f"# target string {args.string!r} at VA 0x{target_va:X}")
    else:
        print("ERROR: pass --va HEX or --string TEXT", file=sys.stderr)
        sys.exit(2)

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
    """Dispatch into :mod:`.ghidra_sync`.

    Supports two orthogonal sync surfaces:

    - **Function / comment sync** (always on): push vanilla-
      symbol renames + plate comments for anchors + patch sites.
    - **Struct sync** (``--push-structs``): create any struct
      defined in ``shims/include/azurik.h`` that Ghidra doesn't
      already have, complete with field layout.  Add
      ``--recreate-structs`` to DELETE + rebuild structs that
      already exist (destructive — wipes any Ghidra variables
      typed with the old layout).
    """
    from .ghidra_client import GhidraClient, GhidraClientError
    from .ghidra_sync import (
        apply_struct_sync, apply_sync,
        format_plan, format_struct_plan,
        plan_struct_sync, plan_sync,
        SyncReport)

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

    struct_actions = []
    if getattr(args, "push_structs", False):
        try:
            struct_actions = plan_struct_sync(
                client,
                recreate_existing=getattr(
                    args, "recreate_structs", False))
        except GhidraClientError as exc:
            print(f"ghidra-sync (structs): {exc}", file=sys.stderr)
            sys.exit(1)

    if args.json:
        _emit({
            "symbols": [
                {"va": a.va, "kind": a.kind,
                 "current_name": a.current_name,
                 "new_name": a.new_name,
                 "comment": a.comment,
                 "rationale": a.rationale}
                for a in actions
            ],
            "structs": [
                {"name": s.name, "kind": s.kind,
                 "size": s.size, "field_count": s.field_count,
                 "rationale": s.rationale}
                for s in struct_actions
            ],
        }, as_json=True)
    else:
        print(format_plan(actions))
        if getattr(args, "push_structs", False):
            print()
            print(format_struct_plan(struct_actions))

    if args.apply:
        print("\nApplying symbol actions to "
              f"{client.base_url}  (force={args.force})")
        report = apply_sync(client, actions, force=args.force)
        if struct_actions:
            print(f"Applying struct actions to "
                  f"{client.base_url}  "
                  f"(recreate={getattr(args, 'recreate_structs', False)})")
            apply_struct_sync(client, struct_actions, report=report)
        print(f"  attempted:          {report.attempted}")
        print(f"  renamed:            {report.renamed}")
        print(f"  commented:          {report.commented}")
        print(f"  skipped (renames):  {report.skipped}")
        if struct_actions:
            print(f"  structs_created:    {report.structs_created}")
            print(f"  struct_fields:      {report.struct_fields_added}")
            print(f"  structs_skipped:    {report.structs_skipped}")
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

def cmd_new_shim(args) -> None:
    """``azurik-mod new-shim NAME`` — scaffold a feature folder.

    See :mod:`azurik_mod.xbe_tools.shim_scaffolder` for the
    planning + rendering logic."""
    from .shim_scaffolder import plan_scaffold, write_scaffold

    repo_root = Path(__file__).resolve().parents[2]

    hook_va: int | None = None
    if args.hook:
        try:
            hook_va = int(args.hook, 0)
        except ValueError:
            print(f"new-shim: bad --hook value {args.hook!r} "
                  "(want hex like 0x5F6E5)", file=sys.stderr)
            sys.exit(2)

    xbe_bytes: bytes | None = None
    if args.xbe or args.iso:
        try:
            xbe_bytes = bytes(_load_xbe(args))
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"new-shim: failed to load XBE: {exc}",
                  file=sys.stderr)
            sys.exit(2)

    ghidra_client = None
    if args.port is not None or args.ghidra:
        from .ghidra_client import GhidraClient
        ghidra_client = GhidraClient(
            host=args.host or "localhost",
            port=args.port or 8193)
        if not ghidra_client.ping():
            print("new-shim: --ghidra / --port passed but no live "
                  f"Ghidra on {ghidra_client.base_url}; continuing "
                  f"without ABI pickup", file=sys.stderr)
            ghidra_client = None

    try:
        plan = plan_scaffold(
            args.name, repo_root=repo_root,
            hook_va=hook_va, xbe_bytes=xbe_bytes,
            ghidra_client=ghidra_client,
            category=args.category,
            emit_test=bool(getattr(args, "emit_test", False)))
    except ValueError as exc:
        print(f"new-shim: {exc}", file=sys.stderr)
        sys.exit(1)

    print(plan.summary())
    print()

    if args.dry_run:
        print("--dry-run: files that WOULD be written:")
        for f in plan.files:
            print(f"  {f}")
        return

    try:
        write_scaffold(plan)
    except ValueError as exc:
        print(f"new-shim: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Created feature folder: {plan.feature_dir}")
    for f in plan.files:
        print(f"  - {f.name}")
    print()
    print("Next steps:")
    print(f"  1. Edit {plan.shim_c_path} — replace the TODO body "
          f"with real logic.")
    print(f"  2. Edit {plan.init_py_path} — fill in the "
          f"description + trampoline label.")
    print(f"  3. Compile:   "
          f"bash shims/toolchain/compile.sh {plan.shim_c_path} "
          f"shims/build/{plan.name}.o")
    print(f"  4. Inspect:   "
          f"azurik-mod shim-inspect azurik_mod/patches/{plan.name}")
    if plan.hook_va is not None:
        print(f"  5. Verify:   "
              f"azurik-mod plan-trampoline 0x{plan.hook_va:X} "
              "--xbe default.xbe")


def cmd_ghidra_snapshot(args) -> None:
    """``azurik-mod ghidra-snapshot PATH`` — write a snapshot JSON."""
    from .ghidra_client import GhidraClient
    from .ghidra_snapshot import write_snapshot

    client = GhidraClient(
        host=args.host or "localhost",
        port=args.port or 8193)
    if not client.ping():
        print(f"ghidra-snapshot: no Ghidra on "
              f"{client.base_url}", file=sys.stderr)
        sys.exit(2)

    stats = write_snapshot(
        Path(args.path).expanduser(),
        client,
        include_default_names=args.keep_default_names,
        include_labels=not args.no_labels,
        include_structs=not getattr(args, "no_structs", False))
    print(f"Snapshot written to {args.path}")
    print(f"  program:         {stats.program_name}")
    print(f"  functions:       {stats.named_functions} named / "
          f"{stats.total_functions} total")
    print(f"  labels:          {stats.named_labels} named / "
          f"{stats.total_labels} total")
    if stats.total_structs:
        print(f"  structs:         {stats.captured_structs} captured / "
              f"{stats.total_structs} total")


def cmd_xbr_diff(args) -> None:
    """``azurik-mod xbr diff A.xbr B.xbr``"""
    from .xbr_diff import diff_xbr, format_diff

    try:
        diff = diff_xbr(args.path_a, args.path_b,
                        min_len=args.min_len)
    except (FileNotFoundError, ValueError) as exc:
        print(f"xbr diff: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        _emit(diff.to_json_dict(), as_json=True)
    else:
        print(format_diff(diff,
                          max_strings=args.max_strings))
    sys.exit(0 if not diff.has_changes else 1)


def cmd_audio_dump(args) -> None:
    """``azurik-mod audio dump FX.XBR --output DIR [--index-xbr ... --no-wav]``"""
    from .audio_dump import dump_waves, format_report

    try:
        report = dump_waves(
            args.fx_xbr, args.output,
            entropy_min=args.entropy_min,
            only_audio=args.only_audio,
            emit_wav=not getattr(args, "no_wav", False),
            emit_raw_previews=getattr(args, "raw_previews", False),
            preview_sample_rate=getattr(
                args, "preview_sample_rate", 22050),
            index_xbr=getattr(args, "index_xbr", None))
    except (FileNotFoundError, ValueError) as exc:
        print(f"audio dump: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        _emit(report.to_dict(), as_json=True)
    else:
        print(format_report(report, preview=args.preview))


def cmd_plugins_list(args) -> None:
    """``azurik-mod plugins list``"""
    from azurik_mod.plugins import (
        discover_plugins, format_report, load_plugins)

    if args.reload:
        report = load_plugins()
    else:
        # Fast path — just discover without importing anything.
        from azurik_mod.plugins import PluginLoadReport
        report = PluginLoadReport(discovered=discover_plugins())

    if args.json:
        _emit(report.to_dict(), as_json=True)
    else:
        print(format_report(report))


def cmd_movies_info(args) -> None:
    """``azurik-mod movies info PATH``"""
    from .bink_info import (
        format_info, format_info_table,
        inspect_bink_file, inspect_directory)

    p = Path(args.path).expanduser().resolve()
    try:
        if p.is_dir():
            infos = inspect_directory(p)
            if args.json:
                _emit([i.to_json_dict() for i in infos], as_json=True)
            else:
                print(format_info_table(infos))
        else:
            info = inspect_bink_file(p)
            if args.json:
                _emit(info.to_json_dict(), as_json=True)
            else:
                print(format_info(info))
    except (FileNotFoundError, ValueError,
            NotADirectoryError) as exc:
        print(f"movies info: {exc}", file=sys.stderr)
        sys.exit(1)


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


# ---------------------------------------------------------------------------
# Next-wave tools (#17 – #26)
# ---------------------------------------------------------------------------


def _ghidra_client_from_args(args):
    """Shared helper: build + ping a GhidraClient from standard
    ``--host`` / ``--port`` flags.  Exits with a clear error when
    the instance isn't reachable."""
    from .ghidra_client import GhidraClient
    client = GhidraClient(
        host=getattr(args, "host", None) or "localhost",
        port=getattr(args, "port", None) or 8193)
    if not client.ping():
        print(f"ERROR: no Ghidra instance reachable at {client.base_url}",
              file=sys.stderr)
        sys.exit(2)
    return client


def _parse_va(arg: str) -> int:
    """Accept hex (``0x85700``), decimal (``547584``), or bare hex
    (``85700``) — matches ``xbe addr`` / ``plan-trampoline`` UX."""
    try:
        return int(arg, 0)
    except ValueError:
        try:
            return int(arg, 16)
        except ValueError as exc:
            raise SystemExit(
                f"bad VA {arg!r}: expected hex or decimal") from exc


def cmd_xrefs(args) -> None:
    """``azurik-mod xrefs <VA>`` — walk the Ghidra xref graph."""
    from .xref_aggregator import build_xref_tree, format_tree

    client = _ghidra_client_from_args(args)
    va = _parse_va(args.va)
    report = build_xref_tree(
        client, address=va,
        direction=args.direction,
        max_depth=args.depth,
        max_nodes=args.max_nodes)
    if args.json:
        _emit(report.to_json_dict(), as_json=True)
    else:
        print(format_tree(report))


def cmd_call_graph(args) -> None:
    """``azurik-mod call-graph <VA>`` — Graphviz DOT out to N hops."""
    from .call_graph import build_call_graph, to_dot

    client = _ghidra_client_from_args(args)
    seeds = [_parse_va(s) for s in args.seeds]
    graph = build_call_graph(
        client, seeds=seeds,
        direction=args.direction,
        max_depth=args.depth,
        max_edges=args.max_edges)
    if args.json:
        _emit(graph.to_json_dict(), as_json=True)
        return
    dot = to_dot(graph)
    if args.dot:
        Path(args.dot).write_text(dot, encoding="utf-8")
        print(f"wrote {args.dot}  "
              f"(nodes={graph.node_count()}, "
              f"edges={graph.edge_count()})")
    else:
        print(dot)


def cmd_struct_diff(args) -> None:
    """``azurik-mod struct-diff`` — azurik.h vs live Ghidra."""
    from .struct_diff import diff_structs, format_report

    header_path = (
        Path(args.header) if args.header
        else Path(__file__).resolve().parents[2]
        / "shims" / "include" / "azurik.h")
    if not header_path.exists():
        print(f"struct-diff: header not found: {header_path}",
              file=sys.stderr)
        sys.exit(1)

    client = None
    structs = None
    if args.offline:
        structs = ()
    else:
        client = _ghidra_client_from_args(args)

    report = diff_structs(header=header_path, client=client,
                          ghidra_structs=structs)
    if args.json:
        _emit(report.to_json_dict(), as_json=True)
    else:
        print(format_report(report, verbose=args.verbose))


def cmd_decomp_cache(args) -> None:
    """``azurik-mod decomp-cache <stats|clear|get>``."""
    from .decomp_cache import DecompCache, cache_root_default

    if args.cache_command == "stats":
        root = (Path(args.root) if args.root
                else cache_root_default())
        total = 0
        programs: list[dict] = []
        if root.exists():
            for prog_dir in sorted(root.iterdir()):
                if not prog_dir.is_dir():
                    continue
                entries = list(prog_dir.glob("*.json"))
                total += len(entries)
                programs.append({
                    "program_key": prog_dir.name,
                    "entries": len(entries),
                })
        out = {"root": str(root), "total_entries": total,
               "programs": programs}
        if args.json:
            _emit(out, as_json=True)
        else:
            print(f"cache root: {root}")
            print(f"  total entries: {total}")
            for p in programs:
                print(f"    {p['program_key']:16}  "
                      f"{p['entries']} entries")
        return

    if args.cache_command == "clear":
        client = _ghidra_client_from_args(args)
        cache = DecompCache.for_client(
            client,
            root=Path(args.root) if args.root else None)
        removed = cache.clear()
        print(f"cleared {removed} cached decomps for "
              f"program_key={cache.program_key}")
        return

    if args.cache_command == "get":
        client = _ghidra_client_from_args(args)
        cache = DecompCache.for_client(
            client,
            root=Path(args.root) if args.root else None)
        decomp = cache.get(_parse_va(args.va))
        if args.json:
            _emit({"address": f"0x{decomp.address:08x}",
                   "function_name": decomp.function_name,
                   "decompiled": decomp.decompiled},
                  as_json=True)
        else:
            print(f"// function_name: {decomp.function_name}")
            print(decomp.decompiled)
        return

    print(f"decomp-cache: unknown verb {args.cache_command!r}",
          file=sys.stderr)
    sys.exit(2)


def cmd_assets_fingerprint(args) -> None:
    """``azurik-mod assets fingerprint <root>``."""
    from .asset_fingerprint import (
        build_fingerprint, save_fingerprint)

    fp = build_fingerprint(
        Path(args.root),
        include=args.include or None,
        exclude=args.exclude or None,
    )
    if args.out:
        save_fingerprint(fp, Path(args.out))
        print(f"wrote: {args.out}  "
              f"(entries={len(fp.entries)})")
    elif args.json:
        _emit(fp.to_json_dict(), as_json=True)
    else:
        print(f"root: {fp.root}")
        print(f"  entries:      {len(fp.entries)}")
        print(f"  generated_at: {fp.generated_at}")
        for entry in fp.entries[:10]:
            print(f"    {entry.path:40}  {entry.size:>10} B  "
                  f"{entry.sha1[:16]}...  [{entry.hash_mode}]")
        if len(fp.entries) > 10:
            print(f"    ... (+{len(fp.entries) - 10} more)")


def cmd_assets_fingerprint_diff(args) -> None:
    """``azurik-mod assets fingerprint-diff <before> <after>``."""
    from .asset_fingerprint import (
        diff_fingerprints, load_fingerprint)

    before = load_fingerprint(Path(args.before))
    after = load_fingerprint(Path(args.after))
    diff = diff_fingerprints(before, after)
    if args.json:
        _emit(diff.to_json_dict(), as_json=True)
        return
    print(f"before: {args.before}")
    print(f"after:  {args.after}")
    print(f"  added:    {len(diff.added)}")
    print(f"  removed:  {len(diff.removed)}")
    print(f"  modified: {len(diff.modified)}")
    print(f"  unchanged: {diff.unchanged}")
    for entry in diff.added:
        print(f"  + {entry.path}  ({entry.size} B)")
    for entry in diff.removed:
        print(f"  - {entry.path}")
    for old, new in diff.modified:
        print(f"  ~ {new.path}  "
              f"({old.size}->{new.size} B, "
              f"sha1 {old.sha1[:8]}->{new.sha1[:8]})")


def cmd_save_key_recover(args) -> None:
    """``azurik-mod save key-recover --dump <binary> --save <slot> …``.

    Brute-force search a memory / binary dump for the 16-byte
    HMAC-SHA1 key that signs one-or-more known save slots.
    See :mod:`azurik_mod.save_format.key_recover` for the full
    design note.
    """
    import time
    from azurik_mod.save_format.key_recover import (
        load_save_sample, recover_keys)

    dump_path = Path(args.dump)
    if not dump_path.is_file():
        print(f"save key-recover: dump not found: {dump_path}",
              file=sys.stderr)
        sys.exit(1)

    samples = []
    for slot in args.save or []:
        try:
            samples.append(load_save_sample(Path(slot)))
        except FileNotFoundError as exc:
            print(f"save key-recover: {exc}", file=sys.stderr)
            sys.exit(1)
    if not samples:
        print("save key-recover: pass --save <slot> at least once",
              file=sys.stderr)
        sys.exit(2)

    dump = dump_path.read_bytes()
    print(f"scanning {len(dump):,} B of {dump_path.name} "
          f"against {len(samples)} save slot(s), "
          f"alignment={args.alignment}")
    t0 = time.perf_counter()
    last_pct = [-1]

    def progress(done, total):
        pct = 100 * done // max(1, total)
        if pct != last_pct[0] and pct % 5 == 0:
            last_pct[0] = pct
            elapsed = time.perf_counter() - t0
            print(f"  {pct:3d}%  ({done:,} / {total:,}, "
                  f"{elapsed:.1f}s elapsed)", file=sys.stderr)

    workers = max(1, getattr(args, "workers", 1))
    hits = list(recover_keys(
        dump, samples,
        alignment=args.alignment,
        early_exit_after=args.max_hits or None,
        progress_cb=(progress if not args.quiet and workers == 1
                     else None),
        workers=workers,
    ))
    elapsed = time.perf_counter() - t0

    if not hits:
        print(f"\nNo matching 16-byte key found in {dump_path.name} "
              f"(scanned {len(dump):,} B in {elapsed:.1f}s).")
        if args.json:
            _emit({"hits": [], "elapsed_seconds": round(elapsed, 3)},
                  as_json=True)
        sys.exit(3)

    print(f"\n{len(hits)} matching key(s):")
    for h in hits:
        print(f"  off=0x{h.offset:08x}  key={h.hex_key()}")
    print(f"\nScan completed in {elapsed:.1f}s.")
    if args.json:
        _emit({
            "hits": [
                {"offset": h.offset, "key": h.hex_key()}
                for h in hits
            ],
            "elapsed_seconds": round(elapsed, 3),
        }, as_json=True)


def cmd_save_edit(args) -> None:
    """``azurik-mod save edit <in> <out> --set <spec>``."""
    from azurik_mod.save_format.editor import (
        SaveEditor, build_plan_from_cli)

    plan = build_plan_from_cli(
        args.set or (),
        plan_path=Path(args.plan) if args.plan else None)
    editor = SaveEditor(Path(args.input))
    report = editor.apply(plan)

    # Optional re-signing path.  Expect a 32-char hex key (16 B).
    sig_key: bytes | None = None
    if getattr(args, "xbox_signature_key", None):
        try:
            sig_key = bytes.fromhex(args.xbox_signature_key)
        except ValueError as exc:
            print(f"save edit: bad --xbox-signature-key "
                  f"{args.xbox_signature_key!r}: {exc}",
                  file=sys.stderr)
            sys.exit(2)
        if len(sig_key) != 16:
            print(f"save edit: --xbox-signature-key must be "
                  f"16 bytes (32 hex chars); got {len(sig_key)} B",
                  file=sys.stderr)
            sys.exit(2)

    report = editor.write_to(
        Path(args.output), report=report,
        xbox_signature_key=sig_key)
    if args.json:
        _emit({
            "applied": [
                {"file": e.file, "line_index": e.line_index,
                 "new_value": e.new_value, "old_value": old}
                for e, old in report.applied],
            "skipped": [
                {"file": e.file, "line_index": e.line_index,
                 "new_value": e.new_value, "reason": r}
                for e, r in report.skipped],
            "signature_stale": report.signature_stale,
            "out_path": str(report.out_path),
        }, as_json=True)
    else:
        print(report.format())


def cmd_xbr_edit(args) -> None:
    """``azurik-mod xbr edit <in> <out>``.

    Supports both the legacy same-size byte / string replacements
    (via :class:`azurik_mod.xbe_tools.xbr_edit.XbrEditor`) and the
    Phase-2 structural primitives (via
    :mod:`azurik_mod.xbr.edits`) when ``--set-value`` is passed.
    The two modes share the same XBR buffer so you can mix them
    in one invocation.
    """
    from .xbr_edit import XbrEditError, XbrEditor
    from azurik_mod.xbr import XbrDocument
    from azurik_mod.xbr.edits import (
        XbrStructuralError,
        set_keyed_double,
        set_keyed_string,
    )

    # Reject blocked-on-RE flags up front so partial state never
    # hits disk when the user mixed a shippable op with a blocked
    # one in the same invocation.
    blocked_flags = (
        getattr(args, "add_row", None),
        getattr(args, "remove_row", None),
        getattr(args, "grow_pool", None),
    )
    if any(blocked_flags):
        print("xbr edit: --add-row / --remove-row / --grow-pool are "
              "not shippable yet.  See docs/XBR_FORMAT.md § Backlog "
              "for the unblock path.", file=sys.stderr)
        sys.exit(2)

    editor = XbrEditor.load(Path(args.input))
    try:
        for spec in args.set_string or ():
            if "=" not in spec:
                raise XbrEditError(
                    f"bad --set-string spec {spec!r}: "
                    f"expected 'old=new'")
            old, new = spec.split("=", 1)
            editor.replace_string_in_tag(
                old=old, new=new, tag=args.tag)
        for spec in args.replace_bytes or ():
            if ":" not in spec:
                raise XbrEditError(
                    f"bad --replace-bytes spec {spec!r}: "
                    f"expected 'OFFSET:HEX'")
            offs_part, hex_part = spec.split(":", 1)
            offset = int(offs_part, 0)
            editor.replace_bytes(offset, bytes.fromhex(hex_part))
    except XbrEditError as exc:
        print(f"xbr edit: {exc}", file=sys.stderr)
        sys.exit(1)

    # Structural edits (Phase 2).  Reuse the same bytearray so the
    # legacy XbrEditor log and the structural edits end up in one
    # output file.
    structural = list(getattr(args, "set_value", None) or ())
    structural_str = list(getattr(args, "set_keyed_string", None) or ())
    if structural or structural_str:
        # XbrDocument always copies on construction so mutations
        # happen on a fresh buffer; we reflect them back into the
        # editor's buffer at the end of the structural block.
        doc = XbrDocument.from_bytes(editor._data)
        try:
            for spec in structural:
                # "section/entity/prop=value"
                if "=" not in spec or "/" not in spec:
                    raise XbrStructuralError(
                        f"bad --set-value spec {spec!r}: expected "
                        f"'section/entity/prop=value'")
                path, val = spec.split("=", 1)
                try:
                    section, entity, prop = path.split("/", 2)
                except ValueError:
                    raise XbrStructuralError(
                        f"bad --set-value path {path!r}: expected "
                        f"'section/entity/prop'")
                sec = doc.keyed_sections().get(section)
                if sec is None:
                    raise XbrStructuralError(
                        f"section {section!r} not a keyed table in "
                        f"{args.input}")
                set_keyed_double(sec, entity, prop, float(val))
                editor.log.record(
                    f"set_value: {section}/{entity}/{prop} = {val}")
            for spec in structural_str:
                if "=" not in spec or "/" not in spec:
                    raise XbrStructuralError(
                        f"bad --set-keyed-string spec {spec!r}: "
                        f"expected 'section/entity/prop=string'")
                path, val = spec.split("=", 1)
                section, entity, prop = path.split("/", 2)
                sec = doc.keyed_sections().get(section)
                if sec is None:
                    raise XbrStructuralError(
                        f"section {section!r} not a keyed table in "
                        f"{args.input}")
                set_keyed_string(sec, entity, prop, val)
                editor.log.record(
                    f"set_keyed_string: {section}/{entity}/{prop} "
                    f"= {val!r}")
        except XbrStructuralError as exc:
            print(f"xbr edit: {exc}", file=sys.stderr)
            sys.exit(1)
        # Commit mutations back into the editor's buffer so the
        # subsequent write() picks them up.
        editor._data[:] = doc.raw

    editor.write(Path(args.output))
    print(editor.log.format())


def cmd_xbr_verify(args) -> None:
    """``azurik-mod xbr verify <file> [--cross-check-schema]``.

    Round-trips the file through :class:`XbrDocument` and reports:

    - Whether the raw bytes round-trip losslessly.
    - Whether every pointer ref resolves to an in-bounds target.
    - Whether any unmodeled tag types are present (informational —
      not a failure).

    With ``--cross-check-schema``, additionally iterate every
    registered patch pack's ``xbr_sites`` and report any ``(section,
    prop)`` triple that isn't documented in
    ``azurik_mod/config/schema.json``.  Mirror of the
    registration-time lint in
    :func:`azurik_mod.patching.registry.register_feature` — useful
    for catching schema drift in third-party plugin packs.

    Exit code 0 when every invariant holds; 1 on drift (any FAIL
    bumps the exit code).  Schema-lint misses are reported as
    warnings and do NOT affect the exit code (so CI that uses
    ``xbr verify`` today doesn't break the day we adopt a pack
    that targets a temporarily-undocumented cell).
    """
    from azurik_mod.xbr import PointerGraph, XbrDocument
    from azurik_mod.xbr.sections import RawSection

    path = Path(args.path)
    if not path.exists():
        print(f"ERROR: {path} does not exist", file=sys.stderr)
        sys.exit(1)

    raw = path.read_bytes()
    try:
        doc = XbrDocument.load(path)
    except ValueError as exc:
        print(f"xbr verify: parse failed: {exc}", file=sys.stderr)
        sys.exit(1)

    ok = True
    if doc.dumps() != raw:
        print("xbr verify: FAIL — round-trip byte-identity broken",
              file=sys.stderr)
        ok = False

    graph = PointerGraph(doc)
    unresolved = [rr for rr in graph if rr.target_offset is None]
    if unresolved:
        print(f"xbr verify: WARN — {len(unresolved)} refs didn't "
              f"resolve (partial parser coverage?)")

    sz = len(doc.raw)
    out_of_bounds = [rr for rr in graph
                     if rr.target_offset is not None
                     and not (0 <= rr.target_offset < sz)]
    if out_of_bounds:
        print(f"xbr verify: FAIL — {len(out_of_bounds)} refs point "
              f"past EOF", file=sys.stderr)
        ok = False

    raw_count = sum(1 for i in range(len(doc.toc))
                    if isinstance(doc.section_for(i), RawSection))

    if getattr(args, "cross_check_schema", False):
        _cross_check_schema_against_registry()

    if ok:
        print(f"xbr verify: OK  ({len(doc.toc)} TOC entries, "
              f"{len(graph)} refs, {raw_count} unmodeled sections)")
        sys.exit(0)
    else:
        sys.exit(1)


def _cross_check_schema_against_registry() -> None:
    """Run the schema-lint over every registered pack, printing a
    human-readable report to stdout.

    Kept in sync with the register_feature-side lint: both share
    :data:`azurik_mod.patching.registry._SCHEMA_CELL_INDEX` via
    :func:`azurik_mod.patching.registry._schema_cell_index`, so a
    schema update or an ``unchecked_xbr_sites=True`` opt-out lands
    in both paths simultaneously.
    """
    # Importing azurik_mod.patches at function scope keeps plain
    # ``xbr verify`` (no cross-check) fast — the pack tree never
    # gets imported when the flag is off.
    import azurik_mod.patches  # noqa: F401
    from azurik_mod.patching.registry import (
        _schema_cell_index,
        all_packs,
    )

    index = _schema_cell_index()
    if not index:
        print("xbr verify: cross-check: schema.json unreadable — "
              "skipping.")
        return

    misses: list[tuple[str, str, str]] = []
    suppressed: list[str] = []
    for pack in all_packs():
        if not pack.xbr_sites:
            continue
        if pack.unchecked_xbr_sites:
            suppressed.append(pack.name)
            continue
        for site in pack.xbr_sites:
            section = getattr(site, "section", None)
            prop = getattr(site, "prop", None)
            if not section or not prop:
                continue
            if (section, prop) not in index:
                misses.append((pack.name, section, prop))

    if not misses:
        suffix = (f" ({len(suppressed)} pack(s) opted out via "
                  f"unchecked_xbr_sites)" if suppressed else "")
        print(f"xbr verify: cross-check: OK{suffix}")
        return

    print(f"xbr verify: cross-check: {len(misses)} undocumented "
          f"pack target(s):")
    for pack_name, section, prop in misses:
        print(f"  - {pack_name}: {section}.{prop}")
    if suppressed:
        print(f"  ({len(suppressed)} pack(s) opted out: "
              f"{', '.join(sorted(suppressed))})")


def cmd_level_preview(args) -> None:
    """``azurik-mod level preview <xbr>``."""
    from .level_preview import format_preview, preview_level

    preview = preview_level(
        Path(args.path),
        include_raw=bool(getattr(args, "include_raw", False)))
    if args.json:
        _emit(preview.to_json_dict(), as_json=True)
    else:
        print(format_preview(
            preview,
            max_items_per_category=getattr(
                args, "max_items", 30)))


def cmd_movies_frames(args) -> None:
    """``azurik-mod movies frames <bik>`` — plan / extract PNGs."""
    from .bink_extract import (
        describe_bink, extract_frames_via_ffmpeg,
        plan_frame_extraction)

    bik = Path(args.path)
    out_dir = Path(args.out) if args.out else bik.parent / (
        bik.stem + ".frames")
    if args.info:
        try:
            table = describe_bink(bik)
        except ValueError as exc:
            print(f"movies frames: {exc}", file=sys.stderr)
            sys.exit(1)
        payload = {
            "file": str(bik),
            "magic": "BIKi",
            "frame_count": table.info.frame_count,
            "fps": round(table.info.fps, 3),
            "width": table.info.width,
            "height": table.info.height,
            "audio_tracks": table.info.audio_track_count,
            "keyframe_count": len(table.keyframes),
        }
        if args.json:
            _emit(payload, as_json=True)
        else:
            for k, v in payload.items():
                print(f"  {k}: {v}")
        return

    plan = plan_frame_extraction(bik, out_dir,
                                 pattern=args.pattern)
    if args.dry_run or not plan.available:
        if args.json:
            _emit(plan.to_json_dict(), as_json=True)
        else:
            print(f"plan: {plan.reason}")
            if plan.command:
                print("  command: " + " ".join(plan.command))
            if not plan.available:
                print("  available: NO")
        return

    try:
        extract_frames_via_ffmpeg(bik, out_dir,
                                  pattern=args.pattern)
    except RuntimeError as exc:
        print(f"movies frames: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"extracted frames into {out_dir}")


__all__ = [
    "cmd_assets_fingerprint",
    "cmd_assets_fingerprint_diff",
    "cmd_audio_dump",
    "cmd_call_graph",
    "cmd_decomp_cache",
    "cmd_entity_diff",
    "cmd_ghidra_coverage",
    "cmd_ghidra_snapshot",
    "cmd_ghidra_sync",
    "cmd_level_preview",
    "cmd_movies_frames",
    "cmd_movies_info",
    "cmd_new_shim",
    "cmd_plan_trampoline",
    "cmd_plugins_list",
    "cmd_save_edit",
    "cmd_shim_inspect",
    "cmd_struct_diff",
    "cmd_test_for_va",
    "cmd_xbr_verify",
    "cmd_xbe_addr",
    "cmd_xbe_find_floats",
    "cmd_xbe_find_refs",
    "cmd_xbe_hexdump",
    "cmd_xbe_sections",
    "cmd_xbe_strings",
    "cmd_xbr_diff",
    "cmd_xbr_edit",
    "cmd_xbr_inspect",
    "cmd_xrefs",
]
