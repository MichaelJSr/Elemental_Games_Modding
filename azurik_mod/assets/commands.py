"""CLI handlers for the ``azurik-mod iso-verify`` subcommand."""

from __future__ import annotations

import sys
from pathlib import Path

from azurik_mod.assets.filelist import load_filelist
from azurik_mod.assets.prefetch import load_prefetch


def cmd_iso_verify(args) -> None:
    """Validate ``iso_root`` against its manifests; exit non-zero on
    any integrity failure."""
    root = Path(args.iso_root).expanduser().resolve()
    if not root.is_dir():
        print(f"iso-verify: not a directory: {root}", file=sys.stderr)
        sys.exit(2)

    pref_path = root / "prefetch-lists.txt"
    fl_path = root / "filelist.txt"

    if not fl_path.exists():
        print(f"iso-verify: missing filelist.txt at {fl_path}",
              file=sys.stderr)
        sys.exit(2)
    if not pref_path.exists():
        print(f"iso-verify: missing prefetch-lists.txt at {pref_path}",
              file=sys.stderr)
        sys.exit(2)

    pref = load_prefetch(pref_path)
    fl = load_filelist(fl_path)

    print(f"ISO root:            {root}")
    print(f"Manifest entries:    {len(fl.entries)}")
    print(f"Manifest total size: {fl.total_size():,} bytes "
          f"({fl.total_size() / 1024 / 1024:.1f} MB)")
    print(f"Prefetch stanzas:    {len(pref.tags)}  "
          f"({len(pref.level_tags())} levels, "
          f"{len(pref.extra_tags())} extras, "
          f"{len(pref.global_files())} globals)")
    print()

    exit_code = 0

    # --- Integrity pass ------------------------------------------------
    label = ("Integrity scan (size-only)" if args.no_md5
             else "Integrity scan (size + MD5)")
    print(f"=== {label} ===")
    issues = fl.verify(root, check_md5=not args.no_md5,
                       limit=args.limit)
    if not issues:
        print("  OK — every file matches the manifest")
    else:
        print(f"  {len(issues)} issue(s) detected:")
        for issue in issues:
            print(f"    - {issue}")
        exit_code = 1

    # --- Orphan scan ---------------------------------------------------
    #
    # filelist.txt scopes its paths relative to ``gamedata/`` (see
    # FilelistManifest._resolve_root).  Use that resolved root for
    # orphan detection so we don't double-count the same files.
    print()
    print("=== Orphan files (on disk but not in filelist.txt) ===")
    scoped_root = fl._resolve_root(root)
    manifest_paths = {e.path for e in fl.entries}
    orphans: list[str] = []
    for f in sorted(scoped_root.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(scoped_root).as_posix()
        if rel.startswith(".") or "/." in rel:
            continue  # skip .DS_Store and friends
        if rel not in manifest_paths:
            orphans.append(rel)
    if not orphans:
        print("  OK — every file is accounted for")
    else:
        print("  Files not declared in filelist.txt:")
        for o in orphans:
            print(f"    - {o}")

    # --- Adjacency graph (optional) ------------------------------------
    if args.graph:
        print()
        print("=== Level adjacency graph (directed prefetch hints) ===")
        for t in pref.level_tags():
            neighbors = pref.neighbors_of(t.name)
            if neighbors:
                print(f"  {t.name:16s} → {', '.join(neighbors)}")
            else:
                print(f"  {t.name:16s} → (terminal)")

    sys.exit(exit_code)


__all__ = ["cmd_iso_verify"]
