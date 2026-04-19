"""Command handlers for the azurik-mod CLI.

One function per CLI subcommand.  These read args, orchestrate
helpers from `shufflers`, `iso.pack`, `patches`, and `patching`, and
print progress to stdout.  The argparse wiring lives in `azurik_mod.cli`.
"""

from __future__ import annotations

import json
import random
import struct
import sys
import tempfile
from collections import Counter
from pathlib import Path

from azurik_mod.iso.pack import (
    CONFIG_XBR_REL,
    GAMEDATA_REL,
    extract_xbe_from_iso,
    read_config_data,
    read_xbe_bytes,
    run_xdvdfs,
    verify_extracted_iso,
)
from azurik_mod.iso.xdvdfs import require_xdvdfs

# Importing the patches package runs each feature folder's
# ``register_feature(...)`` side effect, so the registry is fully
# populated before cmd_randomize_full walks it.  The import is the
# POINT — nothing below references ``fps_unlock``, ``player_physics``
# etc. directly for execution; ``apply_pack(pack, xbe, params)``
# dispatches through the registry.  Keeping ``apply_player_physics``
# + ``apply_player_speed`` around below for the ``apply-physics``
# CLI shortcut which pre-dates the unified dispatcher.
import azurik_mod.patches  # noqa: F401
from azurik_mod.patches.player_physics import (
    apply_player_physics,
    apply_player_speed,
)
from azurik_mod.patches.qol import (
    apply_gem_popups_patch,
    apply_other_popups_patch,
    apply_pickup_anim_patch,
    apply_skip_logo_patch,
    apply_player_character_patch,
)
from azurik_mod.patching import verify_patch_spec

# Repo root — three levels up from this file
# (azurik_mod/randomizer/commands.py -> <repo>).  Used as the
# repo_root argument to apply_pack when shim-backed features need to
# resolve compile.sh / the build cache.
_REPO_ROOT_FOR_RANDOMIZER = Path(__file__).resolve().parent.parent.parent

from azurik_mod.randomizer.shufflers import (
    BARRIER_FOURCCS,
    BARRIER_FOURCCS_HARD,
    BARRIER_OFFSETS,
    GEM_TYPES,
    KEY_REALMS,
    LEVEL_XBRS,
    NAME_FIELD_SIZE,
    OBSIDIAN_LOCK_COUNT,
    OBSIDIAN_LOCK_ENTRY_SIZE,
    OBSIDIAN_LOCK_TABLE_A,
    OBSIDIAN_LOCK_TABLE_B,
    SCALE_OFFSETS,
    EXCLUDE_TRANSITIONS,
    TOWN_BARRIER_ITEMS,
    TOWN_BARRIER_SCALE,
    VALID_DEST_LEVELS,
    _find_all_entities_in_level,
    _find_cross_level_entities,
    _find_level_gem_entities,
    _find_level_transitions,
    _rename_all_refs,
    _write_name,
    apply_level_patches,
    flatten_mod,
    format_value,
    load_mod,
    load_registry,
    read_value,
    resolve_prop,
    write_value,
)

# ---------------------------------------------------------------------------
# CLI command handlers: list / dump / diff / patch
# ---------------------------------------------------------------------------

def cmd_list(args):
    registry = load_registry()
    sections = registry.get("sections", {})

    if args.sections:
        print("Available sections:\n")
        for name, sec in sorted(sections.items()):
            n = len(sec.get("entities", {}))
            conf = sec.get("confidence", "?")
            desc = sec.get("description", "")[:70]
            print(f"  {name:<25} {n:>4} entities  [{conf}]  {desc}")

    elif args.entities:
        section = sections.get(args.entities)
        if not section:
            print(f"ERROR: Section '{args.entities}' not found.")
            print(f"  Available: {', '.join(sorted(sections.keys()))}")
            sys.exit(1)
        entities = section.get("entities", {})
        print(f"Entities in {args.entities} ({len(entities)} total):\n")
        for name in sorted(entities.keys()):
            n = len(entities[name].get("properties", {}))
            print(f"  {name:<35} {n} properties")
    else:
        print("Use --sections or --entities <section_name>")


def cmd_dump(args):
    registry = load_registry()
    data = read_config_data(args)
    sections = registry.get("sections", {})
    section = sections.get(args.section)
    if not section:
        print(f"ERROR: Section '{args.section}' not found.")
        sys.exit(1)

    entities = section.get("entities", {})
    names = [args.entity] if args.entity else sorted(entities.keys())

    for ent_name in names:
        ent = entities.get(ent_name)
        if not ent:
            print(f"  WARNING: Entity '{ent_name}' not found")
            continue
        print(f"\n  [{args.section}] {ent_name}")
        for prop_name, prop in sorted(ent["properties"].items(),
                                       key=lambda x: x[1].get("prop_index", 0)):
            offset = int(prop["value_file_offset"], 16)
            tf = prop.get("type_flag", 0)
            val = read_value(data, offset, tf) if offset + 8 <= len(data) else "?"
            ts = {0: "unset", 1: "float", 2: "int"}.get(tf, f"?{tf}")
            print(f"    {prop_name:<30} = {format_value(val, tf):<15} [{ts}]  @0x{offset:06X}")


def cmd_mod_template(args):
    """Emit a mod-JSON populated with the LIVE vanilla values.

    Replaces the old ``examples/*.json`` folder, which shipped
    pre-baked samples that drifted out of sync with reality.  Reads
    the ISO / config.xbr at runtime so the output is always truthful
    and version-matched.  Users edit the resulting JSON and feed it
    back via ``--mod`` (``patch``) or ``--config-mod``
    (``randomize-full``).
    """
    registry = load_registry()
    data = read_config_data(args)
    sections = registry.get("sections", {})

    mod = {
        "name": args.name,
        "description": (
            "Generated by `azurik-mod mod-template` — vanilla values "
            "dumped from the input ISO / config.xbr.  Edit the "
            "numeric values below, then feed this file back through "
            "`azurik-mod patch --mod <this>.json` or "
            "`randomize-full --config-mod <this>.json`.  Values you "
            "leave unchanged are effectively no-ops (matching vanilla "
            "the tool dumps them back as-is)."
        ),
        "format": "grouped",
        "sections": {},
    }

    total_props = 0
    for sec_name in args.section:
        section = sections.get(sec_name)
        if not section:
            print(f"WARNING: section {sec_name!r} not found in registry; skipping",
                  file=sys.stderr)
            continue
        entities = section.get("entities", {})
        names = [args.entity] if args.entity else sorted(entities.keys())
        sec_out: dict[str, dict[str, object]] = {}
        for ent_name in names:
            ent = entities.get(ent_name)
            if not ent:
                print(f"WARNING: entity {ent_name!r} not in section "
                      f"{sec_name!r}; skipping", file=sys.stderr)
                continue
            ent_out: dict[str, object] = {}
            for prop_name, prop in sorted(
                    ent["properties"].items(),
                    key=lambda x: x[1].get("prop_index", 0)):
                offset = int(prop["value_file_offset"], 16)
                tf = prop.get("type_flag", 0)
                if offset + 8 > len(data):
                    continue
                val = read_value(data, offset, tf)
                ent_out[prop_name] = val
                total_props += 1
            if ent_out:
                sec_out[ent_name] = ent_out
        if sec_out:
            mod["sections"][sec_name] = sec_out

    rendered = json.dumps(mod, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n")
        sec_count = len(mod["sections"])
        ent_count = sum(len(e) for e in mod["sections"].values())
        print(f"Wrote {args.output}: {total_props} properties across "
              f"{ent_count} entities / {sec_count} sections.")
        print(f"Edit the values, then feed it back:")
        print(f"  azurik-mod patch --iso <ISO> --mod {args.output} -o Azurik_modded.iso")
    else:
        print(rendered)


def cmd_diff(args):
    registry = load_registry()
    data = read_config_data(args)

    all_patches = []
    for mod_path in args.mod:
        mod = load_mod(mod_path)
        print(f"Mod: {mod.get('name', mod_path)}")
        if mod.get("description"):
            print(f"  {mod['description']}")
        all_patches.extend(flatten_mod(mod, registry))

    print()
    changes = errors = 0
    for i, p in enumerate(all_patches):
        prop = resolve_prop(registry, p["section"], p["entity"], p["property"])
        if not prop:
            print(f"  [{i+1}] ERROR: {p['section']}/{p['entity']}/{p['property']} not found")
            errors += 1
            continue
        offset = int(prop["value_file_offset"], 16)
        tf = prop.get("type_flag", 0)
        cur = read_value(data, offset, tf) if offset + 8 <= len(data) else "?"
        cur_s = format_value(cur, tf)
        new_s = format_value(p["value"], tf)
        marker = "~" if cur_s != new_s else "="
        print(f"  [{i+1}] {marker} {p['section']}/{p['entity']}/{p['property']}: {cur_s} -> {new_s}")
        if cur_s != new_s:
            changes += 1

    print(f"\n  {changes} changed, {errors} errors, "
          f"{len(all_patches) - changes - errors} unchanged")


def cmd_patch(args):
    """Main command: ISO in + mod(s) -> patched ISO out."""
    xdvdfs = require_xdvdfs()
    registry = load_registry()
    iso_path = Path(args.iso)
    out_path = Path(args.output)

    if not iso_path.exists():
        print(f"ERROR: ISO not found: {iso_path}")
        sys.exit(1)
    if iso_path.resolve() == out_path.resolve():
        print("ERROR: --output must differ from --iso")
        sys.exit(1)

    # Collect all patches from all mods, separating config vs level patches
    all_config_patches = []
    all_level_mods = []  # list of (level_name, level_patches_list)
    for mod_path in args.mod:
        mod = load_mod(mod_path)
        print(f"  Mod: {mod.get('name', mod_path)}")
        all_config_patches.extend(flatten_mod(mod, registry))
        if "level_patches" in mod and "level" in mod:
            all_level_mods.append((mod["level"], mod["level_patches"]))

    has_config = len(all_config_patches) > 0
    has_level = len(all_level_mods) > 0
    total_steps = 1 + int(has_config) + int(has_level) + 1  # extract + patches + repack

    with tempfile.TemporaryDirectory(prefix="azurik_mod_") as tmpdir:
        extract_dir = Path(tmpdir) / "game"

        # Step 1: Extract full game
        step = 1
        print(f"\n[{step}/{total_steps}] Extracting {iso_path.name}...")
        run_xdvdfs(xdvdfs, ["unpack", str(iso_path), str(extract_dir)])
        verify_extracted_iso(extract_dir)

        # Validate
        if not (extract_dir / "default.xbe").exists():
            print("  ERROR: Extracted folder missing default.xbe — not a valid game ISO")
            sys.exit(1)

        # Patch config.xbr if there are config patches
        if has_config:
            step += 1
            config_xbr = extract_dir / CONFIG_XBR_REL
            if not config_xbr.exists():
                print(f"  ERROR: {CONFIG_XBR_REL} not found in extracted game")
                sys.exit(1)

            print(f"[{step}/{total_steps}] Patching config.xbr ({len(all_config_patches)} changes)...")
            data = bytearray(config_xbr.read_bytes())
            if data[:4] != b"xobx":
                print("  ERROR: config.xbr has bad magic")
                sys.exit(1)

            applied = errors = 0
            for p in all_config_patches:
                path = f"{p['section']}/{p['entity']}/{p['property']}"
                prop = resolve_prop(registry, p["section"], p["entity"], p["property"])
                if not prop:
                    print(f"    ERROR: {path} not found in registry")
                    errors += 1
                    continue

                offset = int(prop["value_file_offset"], 16)
                tf = prop.get("type_flag", 0)
                if offset + 4 > len(data):
                    print(f"    ERROR: offset 0x{offset:06X} out of range for {path}")
                    errors += 1
                    continue

                old = read_value(data, offset, tf)
                write_value(data, offset, p["value"], tf)
                new = read_value(data, offset, tf)
                print(f"    {path}: "
                      f"{format_value(old, tf)} -> {format_value(new, tf)}")
                applied += 1

            config_xbr.write_bytes(data)
            print(f"  {applied} applied, {errors} errors")

        # Patch level XBR files if there are level patches
        if has_level:
            step += 1
            print(f"[{step}/{total_steps}] Patching level files...")
            for level_name, level_patches in all_level_mods:
                level_file = extract_dir / GAMEDATA_REL / f"{level_name}.xbr"
                if not level_file.exists():
                    print(f"  ERROR: {level_name}.xbr not found in extracted game")
                    continue

                print(f"  Patching {level_name}.xbr ({len(level_patches)} changes)...")
                ldata = bytearray(level_file.read_bytes())
                if ldata[:4] != b"xobx":
                    print(f"  ERROR: {level_name}.xbr has bad magic")
                    continue

                la, le = apply_level_patches(ldata, level_patches)
                level_file.write_bytes(ldata)
                print(f"  {la} applied, {le} errors")

        # Repack full game into new ISO
        step += 1
        print(f"[{step}/{total_steps}] Building {out_path.name}...")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        run_xdvdfs(xdvdfs, ["pack", str(extract_dir), str(out_path)])

        if out_path.exists():
            size_mb = out_path.stat().st_size / (1024 * 1024)
            print(f"\n  Done! {out_path} ({size_mb:.1f} MB)")
            print(f"  Load in xemu to play.")
        else:
            print("\n  ERROR: ISO creation failed")
            sys.exit(1)



# ---------------------------------------------------------------------------
# Gem randomizer (ISO pipeline)
# ---------------------------------------------------------------------------

def cmd_randomize_gems(args):
    """Randomize gem types across all level files in an ISO."""
    xdvdfs = require_xdvdfs()
    iso_path = Path(args.iso)
    out_path = Path(args.output)

    if not iso_path.exists():
        print(f"ERROR: ISO not found: {iso_path}")
        sys.exit(1)
    if iso_path.resolve() == out_path.resolve():
        print("ERROR: --output must differ from --iso")
        sys.exit(1)

    seed = args.seed
    levels = args.levels if args.levels else LEVEL_XBRS

    print(f"Azurik Gem Randomizer")
    print(f"  Seed: {seed}")
    print(f"  Levels: {len(levels)}")

    with tempfile.TemporaryDirectory(prefix="azurik_rand_") as tmpdir:
        extract_dir = Path(tmpdir) / "game"

        # Step 1: Extract
        print(f"\n[1/3] Extracting {iso_path.name}...")
        run_xdvdfs(xdvdfs, ["unpack", str(iso_path), str(extract_dir)])
        verify_extracted_iso(extract_dir)

        if not (extract_dir / "default.xbe").exists():
            print("  ERROR: Extracted folder missing default.xbe — not a valid game ISO")
            sys.exit(1)

        # Step 2: Randomize gems in each level
        print(f"\n[2/3] Randomizing gems...")
        total_gems = 0
        total_changes = 0
        levels_with_gems = 0
        global_before = Counter()
        global_after = Counter()

        # Use a seeded RNG for the whole run.  Each level gets a
        # deterministic sub-seed derived from the master so the result
        # is stable regardless of which levels are included.
        master_rng = random.Random(seed)

        for level_name in sorted(levels):
            level_file = extract_dir / GAMEDATA_REL / f"{level_name}.xbr"
            if not level_file.exists():
                print(f"  {level_name}.xbr — not found, skipping")
                continue

            data = bytearray(level_file.read_bytes())
            if data[:4] != b"xobx":
                print(f"  {level_name}.xbr — bad magic, skipping")
                continue

            gems = _find_level_gem_entities(data)
            if len(gems) < 2:
                if gems:
                    print(f"  {level_name}.xbr — {len(gems)} gem (need 2+, skipping)")
                continue

            # Per-level deterministic seed
            level_seed = master_rng.randint(0, 2**31)
            level_rng = random.Random(level_seed)

            base_types = [g["gem_base"] for g in gems]
            before = Counter(base_types)
            level_rng.shuffle(base_types)
            after = Counter(base_types)

            # Same collision-detection pattern as cmd_randomize's
            # gem loop — see docs/RANDOMIZER_AUDIT.md.  When a
            # post-shuffle base doesn't fit the existing field,
            # we leave the gem at its original name; warn if that
            # yields duplicate identifiers in the same level.
            planned_names: dict[int, str] = {}
            skipped_slots: list[int] = []
            for i, (g, new_base) in enumerate(zip(gems, base_types)):
                new_name = new_base + g["gem_suffix"]
                if len(new_name) > g["name_len"]:
                    skipped_slots.append(i)
                    planned_names[i] = g["name"]
                else:
                    planned_names[i] = new_name
            if skipped_slots:
                seen: dict[str, list[int]] = {}
                for i, n in planned_names.items():
                    seen.setdefault(n, []).append(i)
                dupes = {n: ix for n, ix in seen.items() if len(ix) > 1}
                if dupes:
                    print(f"  WARNING: {level_name}.xbr — gem "
                          f"name-length skip produced duplicate "
                          f"identifiers.  See "
                          f"docs/RANDOMIZER_AUDIT.md.")

            changes = 0
            skip_set = set(skipped_slots)
            for i, (g, new_base) in enumerate(zip(gems, base_types)):
                if i in skip_set:
                    continue
                new_name = planned_names[i]
                field_size = max(g["name_len"], len(new_name) + 1)
                if g["name"] != new_name:
                    changes += 1
                offset = g["name_offset"]
                new_bytes = new_name.encode("ascii").ljust(field_size, b"\x00")
                data[offset:offset + field_size] = new_bytes

            level_file.write_bytes(data)

            global_before += before
            global_after += after
            total_gems += len(gems)
            total_changes += changes
            levels_with_gems += 1

            dist_str = ", ".join(f"{t}:{before.get(t, 0)}->{after.get(t, 0)}"
                                 for t in GEM_TYPES if before.get(t, 0) or after.get(t, 0))
            print(f"  {level_name}.xbr — {len(gems)} gems, {changes} changed  [{dist_str}]")

        print(f"\n  Summary: {total_gems} gems across {levels_with_gems} levels, "
              f"{total_changes} changed")
        print(f"  Global distribution: {dict(global_before)} -> {dict(global_after)}")

        # Step 3: Repack
        print(f"\n[3/3] Building {out_path.name}...")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        run_xdvdfs(xdvdfs, ["pack", str(extract_dir), str(out_path)])

        if out_path.exists():
            size_mb = out_path.stat().st_size / (1024 * 1024)
            print(f"\n  Done! {out_path} ({size_mb:.1f} MB)")
            print(f"  Seed: {seed}  (use same seed to reproduce)")
            print(f"  Load in xemu to play.")
        else:
            print("\n  ERROR: ISO creation failed")
            sys.exit(1)



# ---------------------------------------------------------------------------
# Unified randomizer: gems + fragments + power-ups
# ---------------------------------------------------------------------------

def cmd_randomize(args):
    """Randomize gems, fragments, and power-ups across all levels."""
    xdvdfs = require_xdvdfs()
    iso_path = Path(args.iso)
    out_path = Path(args.output)

    if not iso_path.exists():
        print(f"ERROR: ISO not found: {iso_path}")
        sys.exit(1)
    if iso_path.resolve() == out_path.resolve():
        print("ERROR: --output must differ from --iso")
        sys.exit(1)

    seed = args.seed
    levels = args.levels if args.levels else LEVEL_XBRS
    do_gems = not args.no_gems
    do_frags = not args.no_fragments
    do_powers = not args.no_powers

    print(f"Azurik Collectible Randomizer")
    print(f"  Seed: {seed}")
    print(f"  Levels: {len(levels)}")
    print(f"  Randomize: {', '.join(t for t, f in [('gems', do_gems), ('fragments', do_frags), ('powers', do_powers)] if f)}")

    with tempfile.TemporaryDirectory(prefix="azurik_rand_") as tmpdir:
        extract_dir = Path(tmpdir) / "game"

        # Step 1: Extract
        print(f"\n[1/4] Extracting {iso_path.name}...")
        run_xdvdfs(xdvdfs, ["unpack", str(iso_path), str(extract_dir)])
        verify_extracted_iso(extract_dir)

        if not (extract_dir / "default.xbe").exists():
            print("  ERROR: Not a valid game ISO")
            sys.exit(1)

        # Track which level files are modified (need writing back)
        modified_levels = {}  # level_name -> bytearray

        # Step 2: Randomize gems (per-level, same as before)
        master_rng = random.Random(seed)

        if do_gems:
            print(f"\n[2/4] Randomizing gems...")
            total_gems = 0
            total_gem_changes = 0
            gem_levels = 0

            for level_name in sorted(levels):
                level_file = extract_dir / GAMEDATA_REL / f"{level_name}.xbr"
                if not level_file.exists():
                    continue

                if level_name in modified_levels:
                    data = modified_levels[level_name]
                else:
                    data = bytearray(level_file.read_bytes())
                    if data[:4] != b"xobx":
                        continue

                gems = _find_level_gem_entities(data)
                if len(gems) < 2:
                    continue

                level_seed = master_rng.randint(0, 2**31)
                level_rng = random.Random(level_seed)

                base_types = [g["gem_base"] for g in gems]
                before = Counter(base_types)
                level_rng.shuffle(base_types)
                after = Counter(base_types)

                # Consistency check: any post-shuffle assignment that
                # wouldn't fit the gem's name field is skipped with a
                # ``continue`` below.  Skipping risks a DUPLICATE-NAME
                # collision — e.g. gem[0]='red_gem' keeps its old name
                # because 'obsidian_gem' was too long, but gem[1] gets
                # 'red_gem' assigned from the shuffle, so the level now
                # has two 'red_gem' entities.  See
                # docs/RANDOMIZER_AUDIT.md § "gem-skip collisions".
                #
                # We detect + warn (rather than silently produce the
                # collision) so users / contributors see the issue
                # even if the in-game effect is "invisible" until
                # gameplay testing.
                planned_names: dict[int, str] = {}
                skipped_slots: list[int] = []
                for i, (g, new_base) in enumerate(zip(gems, base_types)):
                    new_name = new_base + g["gem_suffix"]
                    if len(new_name) > g["name_len"]:
                        skipped_slots.append(i)
                        planned_names[i] = g["name"]  # keeps old name
                    else:
                        planned_names[i] = new_name

                if skipped_slots:
                    # Detect collisions: any duplicated value in
                    # planned_names is a concern.
                    seen: dict[str, list[int]] = {}
                    for i, n in planned_names.items():
                        seen.setdefault(n, []).append(i)
                    dupes = {n: ix for n, ix in seen.items() if len(ix) > 1}
                    if dupes:
                        print(f"  WARNING: {level_name}.xbr — gem "
                              f"name-length skip produced duplicate "
                              f"identifiers ({dict(list(dupes.items())[:3])}"
                              f"{'...' if len(dupes) > 3 else ''}); "
                              f"consider a different seed.  See "
                              f"docs/RANDOMIZER_AUDIT.md for the "
                              f"long-term fix.")

                changes = 0
                for i, (g, new_base) in enumerate(zip(gems, base_types)):
                    if i in set(skipped_slots):
                        continue
                    new_name = planned_names[i]
                    if g["name"] != new_name:
                        changes += 1
                    offset = g["name_offset"]
                    field_size = g["name_len"]
                    new_bytes = new_name.encode("ascii").ljust(field_size, b"\x00")
                    data[offset:offset + field_size] = new_bytes

                modified_levels[level_name] = data
                total_gems += len(gems)
                total_gem_changes += changes
                gem_levels += 1

                dist_str = ", ".join(f"{t}:{before.get(t, 0)}->{after.get(t, 0)}"
                                     for t in GEM_TYPES if before.get(t, 0) or after.get(t, 0))
                print(f"  {level_name}.xbr — {len(gems)} gems, {changes} changed  [{dist_str}]")

            print(f"  Total: {total_gems} gems across {gem_levels} levels, {total_gem_changes} changed")
        else:
            print(f"\n[2/4] Gems — skipped")

        # Step 3: Randomize fragments + power-ups (cross-level)
        if do_frags or do_powers:
            print(f"\n[3/4] Scanning for fragments and power-ups...")
            fragments, powerups, scan_data = _find_cross_level_entities(extract_dir, levels)

            # Merge scan_data into modified_levels (prefer already-modified data)
            for lname, ldata in scan_data.items():
                if lname not in modified_levels:
                    modified_levels[lname] = ldata

            # Use dedicated sub-seeds so fragment/power shuffles are independent
            frag_rng = random.Random(master_rng.randint(0, 2**31))
            power_rng = random.Random(master_rng.randint(0, 2**31))

            if do_frags and fragments:
                print(f"\n  Randomizing {len(fragments)} fragments across {len(set(f['level'] for f in fragments))} levels...")

                # Full permutation: shuffle the exact names across all locations
                frag_names = [f["name"] for f in fragments]
                frag_rng.shuffle(frag_names)

                frag_changes = 0
                for frag, new_name in zip(fragments, frag_names):
                    data = modified_levels[frag["level"]]
                    old_name = frag["name"]

                    if len(new_name) > NAME_FIELD_SIZE:
                        print(f"    WARNING: '{new_name}' exceeds {NAME_FIELD_SIZE} bytes, skipping")
                        continue

                    changed = old_name != new_name
                    if changed:
                        frag_changes += 1
                    marker = "~" if changed else "="

                    print(f"    {marker} {frag['level']:>5}: {old_name:<15} -> {new_name:<15}")

                    offset = frag["name_offset"]
                    new_bytes = new_name.encode("ascii").ljust(NAME_FIELD_SIZE, b"\x00")
                    data[offset:offset + NAME_FIELD_SIZE] = new_bytes

                print(f"  Fragments: {frag_changes}/{len(fragments)} changed")
            elif do_frags:
                print(f"\n  No fragments found in scanned levels")

            if do_powers and powerups:
                print(f"\n  Randomizing {len(powerups)} power-ups across {len(set(p['level'] for p in powerups))} levels...")

                # Shuffle element types with logic solver validation.
                # Retry with new sub-seeds until we find a solvable placement.
                elements = [p["element"] for p in powerups]
                MAX_ATTEMPTS = 100

                try:
                    from azurik_mod.randomizer.solver import Solver
                    solver = Solver()
                    has_solver = True
                except Exception as e:
                    print(f"  WARNING: Logic solver unavailable ({e}), skipping solvability check")
                    has_solver = False

                solved = False
                for attempt in range(MAX_ATTEMPTS):
                    trial_elements = list(elements)
                    power_rng.shuffle(trial_elements)

                    if has_solver:
                        # Build shuffle mapping for solver: (level,
                        # orig_power_name, new_power_name).
                        #
                        # CRITICAL: use the REAL in-game entity name
                        # (``pu["name"]``), not the synthesised
                        # canonical ``power_{element}``.  Earlier code
                        # here built ``orig_canonical =
                        # f"power_{pu['element']}"`` which silently
                        # mismatched the ``a3`` power variant — the
                        # node's vanilla pickup list contains
                        # ``power_water_a3`` but the canonical name
                        # is ``power_water``.  When
                        # ``build_placement_from_shuffle`` looked up
                        # ``orig_name in vanilla`` it found nothing,
                        # returned an empty placement dict, and
                        # ``solve()`` happily reported the VANILLA
                        # game as solvable — so the check was
                        # vacuously True for every shuffle and the
                        # solvability guarantee was a lie.  See
                        # ``docs/RANDOMIZER_AUDIT.md`` for the full
                        # trace.  New name stays canonical because
                        # the shuffle's ``trial_elements`` is a
                        # permutation of element keywords, not real
                        # entity names.
                        power_mapping = []
                        for pu, new_elem in zip(powerups, trial_elements):
                            orig_real = pu["name"]          # keep a3 suffix!
                            new_canonical = f"power_{new_elem}"
                            power_mapping.append(
                                (pu["level"], orig_real, new_canonical))

                        if solver.check_power_placement(power_mapping):
                            if attempt > 0:
                                print(f"  Found solvable placement after {attempt + 1} attempts")
                            else:
                                print(f"  Placement verified solvable")
                            elements = trial_elements
                            solved = True
                            break
                        else:
                            # Advance the RNG for next attempt
                            power_rng = random.Random(power_rng.randint(0, 2**31))
                    else:
                        elements = trial_elements
                        solved = True
                        break

                if not solved:
                    print(f"  ERROR: Could not find solvable placement in {MAX_ATTEMPTS} attempts")
                    print(f"  Try a different seed, or use --no-powers")
                    sys.exit(1)

                power_changes = 0
                for pu, new_elem in zip(powerups, elements):
                    data = modified_levels[pu["level"]]
                    old_name = pu["name"]
                    old_elem = pu["element"]
                    suffix = old_name[len(f"power_{old_elem}"):]
                    new_name = f"power_{new_elem}{suffix}"

                    if len(new_name) > NAME_FIELD_SIZE:
                        print(f"    WARNING: '{new_name}' exceeds {NAME_FIELD_SIZE} bytes, skipping")
                        continue

                    changed = old_name != new_name
                    if changed:
                        power_changes += 1
                    marker = "~" if changed else "="

                    print(f"    {marker} {pu['level']:>5}: {old_name:<20} -> {new_name:<20}")

                    offset = pu["name_offset"]
                    new_bytes = new_name.encode("ascii").ljust(NAME_FIELD_SIZE, b"\x00")
                    data[offset:offset + NAME_FIELD_SIZE] = new_bytes

                print(f"  Power-ups: {power_changes}/{len(powerups)} changed")
            elif do_powers:
                print(f"\n  No power-ups found in scanned levels")
        else:
            print(f"\n[3/4] Fragments/powers — skipped")

        # Write all modified level files back
        for level_name, data in modified_levels.items():
            level_file = extract_dir / GAMEDATA_REL / f"{level_name}.xbr"
            level_file.write_bytes(data)

        # Step 4: Repack
        print(f"\n[4/4] Building {out_path.name}...")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        run_xdvdfs(xdvdfs, ["pack", str(extract_dir), str(out_path)])

        if out_path.exists():
            size_mb = out_path.stat().st_size / (1024 * 1024)
            print(f"\n  Done! {out_path} ({size_mb:.1f} MB)")
            print(f"  Seed: {seed}  (use same seed to reproduce)")
            print(f"  Load in xemu to play.")
        else:
            print("\n  ERROR: ISO creation failed")
            sys.exit(1)


# NOTE: DIRECT_SEARCH_NAMES now lives in shufflers.py (it's consumed by
# _find_all_entities_in_level there).  Re-exported below for any CLI
# handlers that iterate the list directly.
from azurik_mod.randomizer.shufflers import DIRECT_SEARCH_NAMES  # noqa: F401



# ---------------------------------------------------------------------------
# Full game randomizer: major + keys + gems + barriers + connections + QoL
# ---------------------------------------------------------------------------

def cmd_randomize_full(args):
    """Full game randomizer: major items, keys, gems, barriers + QoL patches."""
    xdvdfs = require_xdvdfs()
    iso_path = Path(args.iso)
    out_path = Path(args.output)

    if not iso_path.exists():
        print(f"ERROR: ISO not found: {iso_path}")
        sys.exit(1)
    if iso_path.resolve() == out_path.resolve():
        print("ERROR: --output must differ from --iso")
        sys.exit(1)

    seed = args.seed
    do_major = not args.no_major
    do_keys = not args.no_keys
    do_gems = not args.no_gems
    do_barriers = not args.no_barriers
    hard_barriers = getattr(args, 'hard_barriers', False)
    do_connections = not getattr(args, 'no_connections', False)
    force_unsolvable = getattr(args, 'force', False)
    obsidian_cost = getattr(args, 'obsidian_cost', None)

    # QoL patches: opt-in.  The old grouped --no-qol / --no-gem-popups /
    # --no-pickup-anim flags are accepted (legacy) but since defaults
    # are already off they only matter if the user ALSO passed the
    # matching opt-in flag on the same invocation.
    legacy_no_qol = bool(getattr(args, 'no_qol', False))
    want_gem_popups = (bool(getattr(args, 'gem_popups', False))
                       and not legacy_no_qol
                       and not getattr(args, 'no_gem_popups', False))
    want_other_popups = (bool(getattr(args, 'other_popups', False))
                         and not legacy_no_qol
                         and not getattr(args, 'no_other_popups', False))
    want_pickup_anims = (bool(getattr(args, 'pickup_anims', False))
                         and not legacy_no_qol
                         and not getattr(args, 'no_pickup_anim', False))
    want_skip_logo = (bool(getattr(args, 'skip_logo', False))
                      and not legacy_no_qol
                      and not getattr(args, 'no_skip_logo', False))

    # Parse custom item pool if provided
    custom_pool = None
    item_pool_arg = getattr(args, 'item_pool', None)
    if item_pool_arg:
        try:
            pool_path = Path(item_pool_arg)
            if pool_path.exists():
                with open(pool_path) as f:
                    custom_pool = json.load(f)
            else:
                custom_pool = json.loads(item_pool_arg)
        except (json.JSONDecodeError, Exception) as e:
            print(f"ERROR: Could not parse --item-pool: {e}")
            sys.exit(1)
        print(f"  Custom item pool: {custom_pool}")

    qol_names = []
    if want_gem_popups: qol_names.append("gem popups")
    if want_other_popups: qol_names.append("other popups")
    if want_pickup_anims: qol_names.append("pickup anims")
    if want_skip_logo: qol_names.append("skip logo")
    qol_label = f"QoL ({', '.join(qol_names)})" if qol_names else None
    categories = [t for t, f in [("major items", do_major), ("keys", do_keys),
                                  ("gems", do_gems), ("barriers", do_barriers),
                                  ("connections", do_connections)] if f]
    if qol_label:
        categories.append(qol_label)
    print(f"Azurik Full Randomizer")
    print(f"  Seed: {seed}")
    print(f"  Categories: {', '.join(categories)}")
    if force_unsolvable:
        print(f"  Force: building even if unsolvable")

    with tempfile.TemporaryDirectory(prefix="azurik_full_") as tmpdir:
        extract_dir = Path(tmpdir) / "game"

        # Step 1: Extract
        print(f"\n[1/7] Extracting {iso_path.name}...")
        run_xdvdfs(xdvdfs, ["unpack", str(iso_path), str(extract_dir)])
        verify_extracted_iso(extract_dir)

        if not (extract_dir / "default.xbe").exists():
            print("  ERROR: Not a valid game ISO")
            sys.exit(1)

        # Load all level data
        modified_levels = {}  # level_name -> bytearray
        all_entities = {}     # level_name -> categorized entities

        for level_name in LEVEL_XBRS:
            level_file = extract_dir / GAMEDATA_REL / f"{level_name}.xbr"
            if not level_file.exists():
                continue
            data = bytearray(level_file.read_bytes())
            if data[:4] != b"xobx":
                continue
            modified_levels[level_name] = data
            all_entities[level_name] = _find_all_entities_in_level(data, level_name)

        master_rng = random.Random(seed)

        # Step 2: Major items (fragments + powers + town powers)
        # Uses solver forward-fill to guarantee completability.
        if do_major:
            print(f"\n[2/7] Randomizing major items (forward-fill)...")
            major_rng = random.Random(master_rng.randint(0, 2**31))

            # Collect all major item slots from binary scan
            # NOTE: obsidian spawn points are excluded because their NDBG
            # parent group ("obsidians") uses a different collection handler.
            major_items = []
            for level_name, ents in all_entities.items():
                major_items.extend(ents["fragments"])
                major_items.extend(ents["powers"])
                major_items.extend(ents["town_powers"])

            if len(major_items) >= 2:
                # Check all names fit in all slots
                names = [item["name"] for item in major_items]
                max_name_len = max(len(n) for n in names)
                min_field = min(item["field_size"] for item in major_items)
                if max_name_len >= min_field:
                    print(f"  WARNING: Longest name ({max_name_len}) may not fit smallest field ({min_field})")

                # Build lookup: (level, original_item_name) -> binary entity info
                binary_lookup = {}
                for item in major_items:
                    key = (item["level"], item["name"])
                    binary_lookup[key] = item

                # Use solver forward-fill for completable placement
                try:
                    from azurik_mod.randomizer.solver import Solver
                    solver = Solver()
                    has_solver = True
                except Exception as e:
                    print(f"  WARNING: Logic solver unavailable ({e}), falling back to blind shuffle")
                    has_solver = False

                if has_solver:
                    # Build custom groups if user provided an item pool
                    custom_groups = None
                    if custom_pool:
                        # Filter out gem types — those are handled in step 4
                        custom_items = []
                        for item_name, count in custom_pool.items():
                            if item_name in GEM_TYPES:
                                continue  # gem weights handled separately
                            custom_items.extend([item_name] * int(count))
                        if custom_items:
                            custom_groups = {"progression": custom_items}
                            print(f"  Custom pool: {len(custom_items)} items ({len(set(custom_items))} unique types)")

                    # Try forward-fill with increasing seeds until solvable
                    MAX_ATTEMPTS = 100
                    solved = False
                    last_placement = None
                    for attempt in range(MAX_ATTEMPTS):
                        attempt_rng = random.Random(major_rng.randint(0, 2**31))
                        placement, step_log = solver.forward_fill(
                            rng=attempt_rng,
                            groups=custom_groups,
                        )
                        last_placement = placement

                        # Validate the placement is completable
                        # forward_fill returns index-based placement which
                        # _build_pickup_map handles natively
                        ok, _ = solver.validate_placement(placement)
                        if ok:
                            if attempt > 0:
                                print(f"  Found solvable placement after {attempt + 1} attempts")
                            else:
                                print(f"  Placement verified solvable")
                            solved = True
                            break

                    if not solved:
                        if force_unsolvable:
                            print(f"  WARNING: No solvable placement found in {MAX_ATTEMPTS} attempts")
                            print(f"  --force: building with last attempted placement (NOT completable)")
                            placement = last_placement
                        else:
                            print(f"  ERROR: Could not find solvable placement in {MAX_ATTEMPTS} attempts")
                            print(f"  Try a different seed, use --no-major, or use --force to build anyway")
                            sys.exit(1)

                    # Convert solver placement to binary rename operations
                    # placement = {node_id: {pickup_idx: new_item_name}}
                    rename_ops = []  # list of (binary_item, new_name)
                    for node_id, idx_map in placement.items():
                        node_data = solver.nodes[node_id]
                        level = node_data.get("level", "")
                        vanilla_pickups = node_data.get("pickups", [])
                        for idx, new_item in idx_map.items():
                            if idx < len(vanilla_pickups):
                                orig_item = vanilla_pickups[idx]
                                key = (level, orig_item)
                                if key in binary_lookup:
                                    rename_ops.append((binary_lookup[key], new_item))
                                else:
                                    print(f"    WARNING: {level}/{orig_item} not found in binary scan")

                    # Also handle items NOT in the solver DB (stay in place)
                    solver_items = set()
                    for node_id, node_data in solver.nodes.items():
                        level = node_data.get("level", "")
                        for pickup in node_data.get("pickups", []):
                            solver_items.add((level, pickup))

                    for item in major_items:
                        key = (item["level"], item["name"])
                        if key not in solver_items:
                            rename_ops.append((item, item["name"]))  # no change
                else:
                    # Fallback: blind shuffle (no solver)
                    major_rng.shuffle(names)
                    rename_ops = list(zip(major_items, names))

                changes = 0
                levels_touched = set()
                print(f"  Placing {len(rename_ops)} items across levels:")
                for item, new_name in rename_ops:
                    data = modified_levels[item["level"]]
                    old_name = item["name"]
                    changed = old_name != new_name
                    if changed:
                        changes += 1
                        levels_touched.add(item["level"])
                    marker = "~" if changed else "="
                    print(f"    {marker} {item['level']:>5}: {old_name:<20} -> {new_name:<20}")
                    _write_name(data, item["name_offset"], new_name, item["field_size"])
                    if changed:
                        _rename_all_refs(data, old_name, new_name, item["name_offset"])
                        # Scale down non-native items placed behind town barriers
                        if (item["level"] == "town"
                                and new_name not in TOWN_BARRIER_ITEMS
                                and old_name in TOWN_BARRIER_ITEMS):
                            name_off = item["name_offset"]
                            # Verify the scale offsets contain 1.0f before patching
                            if all(data[name_off + so : name_off + so + 4] == b"\x00\x00\x80\x3f"
                                   for so in SCALE_OFFSETS):
                                for so in SCALE_OFFSETS:
                                    struct.pack_into("<f", data, name_off + so, TOWN_BARRIER_SCALE)
                                print(f"      (scaled to {TOWN_BARRIER_SCALE}x for barrier fit)")

                print(f"  Major items: {changes}/{len(rename_ops)} changed across {len(levels_touched)} levels")
            else:
                print(f"  Only {len(major_items)} major items found, skipping")
        else:
            print(f"\n[2/7] Major items — skipped")

        # Step 3: Keys (within-realm shuffle)
        if do_keys:
            print(f"\n[3/7] Randomizing keys (within realm)...")
            key_rng = random.Random(master_rng.randint(0, 2**31))
            total_key_changes = 0

            for realm, realm_keys in KEY_REALMS.items():
                # Find actual key entities in the loaded data
                realm_items = []
                for level_name, key_name in realm_keys:
                    if level_name not in all_entities:
                        continue
                    for k in all_entities[level_name]["keys"]:
                        if k["name"] == key_name:
                            realm_items.append(k)
                            break

                if len(realm_items) < 2:
                    if realm_items:
                        print(f"  {realm}: 1 key, no shuffle needed")
                    continue

                names = [k["name"] for k in realm_items]
                key_rng.shuffle(names)

                realm_changes = 0
                for item, new_name in zip(realm_items, names):
                    data = modified_levels[item["level"]]
                    old_name = item["name"]
                    changed = old_name != new_name
                    if changed:
                        realm_changes += 1
                    marker = "~" if changed else "="
                    print(f"    {marker} {realm:>6} {item['level']:>5}: {old_name:<20} -> {new_name:<20}")
                    _write_name(data, item["name_offset"], new_name, item["field_size"])

                total_key_changes += realm_changes
                print(f"  {realm}: {realm_changes}/{len(realm_items)} changed")

            print(f"  Total key changes: {total_key_changes}")
        else:
            print(f"\n[3/7] Keys — skipped")

        # Step 4: Gems (per-level shuffle, including obsidians)
        if do_gems:
            print(f"\n[4/7] Randomizing gems...")
            gem_rng = random.Random(master_rng.randint(0, 2**31))
            total_gems = 0
            total_gem_changes = 0

            # Check for custom gem weights in the item pool
            gem_weights = None
            if custom_pool:
                gw = {g: custom_pool[g] for g in GEM_TYPES if g in custom_pool}
                if gw:
                    gem_weights = gw
                    print(f"  Custom gem weights: {gem_weights}")

            for level_name in sorted(all_entities.keys()):
                # Combine regular gems + obsidians into one pool
                gems = all_entities[level_name]["gems"] + all_entities[level_name]["obsidians"]
                if len(gems) < 2:
                    continue

                level_rng = random.Random(gem_rng.randint(0, 2**31))

                if gem_weights:
                    # Weighted random: each gem slot independently drawn
                    # from the custom distribution
                    weight_types = list(gem_weights.keys())
                    weight_vals = list(gem_weights.values())
                    base_types = level_rng.choices(
                        weight_types, weights=weight_vals, k=len(gems))
                else:
                    # Default: shuffle existing types (preserves counts)
                    base_types = [g["gem_base"] for g in gems]
                    level_rng.shuffle(base_types)

                changes = 0
                for g, new_base in zip(gems, base_types):
                    new_name = new_base + g["gem_suffix"]
                    data = modified_levels[level_name]
                    if g["name"] != new_name:
                        changes += 1
                    _write_name(data, g["name_offset"], new_name, g["field_size"])

                total_gems += len(gems)
                total_gem_changes += changes
                print(f"  {level_name}: {len(gems)} gems, {changes} changed")

            print(f"  Total: {total_gems} gems, {total_gem_changes} changed")
        else:
            print(f"\n[4/7] Gems — skipped")

        # Step 5: Barriers (randomize element vulnerability)
        if do_barriers:
            print(f"\n[5/7] Randomizing barriers...")
            barrier_rng = random.Random(master_rng.randint(0, 2**31))
            barrier_changes = 0
            fourcc_pool = BARRIER_FOURCCS_HARD if hard_barriers else BARRIER_FOURCCS

            for barrier_type, level_offsets in BARRIER_OFFSETS.items():
                for level_name, offset in level_offsets.items():
                    if level_name not in modified_levels:
                        continue
                    data = modified_levels[level_name]
                    if offset + 4 > len(data):
                        print(f"    WARNING: {level_name} {barrier_type} offset 0x{offset:X} out of range")
                        continue
                    old_fourcc = bytes(data[offset:offset + 4])
                    new_fourcc = barrier_rng.choice(fourcc_pool)
                    changed = old_fourcc != new_fourcc
                    if changed:
                        barrier_changes += 1
                    data[offset:offset + 4] = new_fourcc
                    marker = "~" if changed else "="
                    print(f"    {marker} {level_name:>5} {barrier_type:>9}: {old_fourcc.decode('ascii', errors='replace'):4} -> {new_fourcc.decode('ascii'):4}")

            print(f"  Barriers: {barrier_changes} changed")
        else:
            print(f"\n[5/7] Barriers — skipped")

        # Step 6: Level connections (randomize exits between levels)
        if do_connections:
            print(f"\n[6/7] Randomizing level connections...")
            conn_rng = random.Random(master_rng.randint(0, 2**31))

            # Scan all loaded levels for transitions
            all_transitions = []
            for level_name, data in modified_levels.items():
                transitions = _find_level_transitions(bytes(data), level_name)
                for t in transitions:
                    pair = (t["level"], t["dest_level"])
                    if pair in EXCLUDE_TRANSITIONS:
                        continue
                    # Only include transitions to valid randomizable levels
                    if t["dest_level"] in VALID_DEST_LEVELS:
                        all_transitions.append(t)

            # Group transitions by path length (can only swap within same length or shorter)
            by_length: dict[int, list[dict]] = {}
            for t in all_transitions:
                by_length.setdefault(t["path_len"], []).append(t)

            print(f"  Found {len(all_transitions)} transitions in {len(by_length)} length groups:")
            for length in sorted(by_length.keys()):
                group = by_length[length]
                dests = [t["dest_level"] for t in group]
                print(f"    {length} chars: {len(group)} exits -> {sorted(set(dests))}")

            # Shuffle destinations within each length group
            conn_changes = 0
            for length, group in by_length.items():
                # Collect the destination paths and shuffle them
                dest_paths = [t["dest_path"] for t in group]
                shuffled = list(dest_paths)
                conn_rng.shuffle(shuffled)

                for t, new_dest_path in zip(group, shuffled):
                    data = modified_levels[t["level"]]
                    old_dest = t["dest_path"]
                    changed = old_dest != new_dest_path

                    if changed:
                        conn_changes += 1

                    # Write the new destination path (null-pad if shorter)
                    new_bytes = new_dest_path.encode("ascii")
                    old_len = len(old_dest)
                    padded = new_bytes + b"\x00" * (old_len - len(new_bytes) + 1)
                    data[t["offset"]:t["offset"] + len(padded)] = padded

                    # Clear the start spot name (set to empty string)
                    # This makes the player spawn at the level's default origin
                    # which is safer than leaving a mismatched spot name
                    if changed and t["spot_offset"]:
                        spot_len = len(t["spot"])
                        data[t["spot_offset"]:t["spot_offset"] + spot_len] = b"\x00" * spot_len

                    new_level = new_dest_path.split("/")[-1]
                    marker = "~" if changed else "="
                    print(f"    {marker} {t['level']:>5} -> {t['dest_level']:>8} now -> {new_level:<8}")

            print(f"  Connections: {conn_changes}/{len(all_transitions)} changed")
        else:
            print(f"\n[6/7] Level connections — skipped")

        # Step 7: XBE patches — walk the pack registry instead of
        # hardcoding each pack's apply-function call.  The user's
        # CLI / GUI flags set ``want_<pack_tag>`` booleans (see the
        # wiring table below); we flip each corresponding registered
        # pack into "enabled", collect parameter values from the
        # namespace, and then apply_pack does the dispatching.
        from azurik_mod.patching import apply_pack
        from azurik_mod.patching.registry import all_packs

        gravity_val = getattr(args, 'gravity', None)
        walk_scale = float(getattr(args, 'player_walk_scale', None) or 1.0)
        # Back-compat: accept legacy `player_run_scale` namespace
        # attr as an alias for the new `player_roll_scale`.  Explicit
        # roll wins if both are set.
        roll_scale = float(
            getattr(args, 'player_roll_scale', None)
            or getattr(args, 'player_run_scale', None)
            or 1.0)
        swim_scale = float(getattr(args, 'player_swim_scale', None) or 1.0)
        jump_scale = float(getattr(args, 'player_jump_scale', None) or 1.0)
        air_control_scale = float(
            getattr(args, 'player_air_control_scale', None) or 1.0)
        flap_scale = float(getattr(args, 'player_flap_scale', None) or 1.0)
        player_char = getattr(args, 'player_character', None)
        xbe_path = extract_dir / "default.xbe"

        # CLI-flag -> pack-name map.  Keeps cmd_randomize_full's
        # argparse surface stable while the actual apply machinery
        # discovers packs from the registry.
        _FLAG_PACKS: dict[str, bool] = {
            "qol_gem_popups": want_gem_popups,
            "qol_other_popups": want_other_popups,
            "qol_pickup_anims": want_pickup_anims,
            "qol_skip_logo": want_skip_logo,
            "fps_unlock": bool(getattr(args, 'fps_unlock', False)),
            "player_physics": (gravity_val is not None
                               or walk_scale != 1.0
                               or roll_scale != 1.0
                               or swim_scale != 1.0
                               or jump_scale != 1.0
                               or air_control_scale != 1.0
                               or flap_scale != 1.0),
        }

        needs_xbe = any(_FLAG_PACKS.values()) or bool(player_char)

        if needs_xbe:
            print(f"\n[7/7] Applying XBE patches to default.xbe...")
            xbe_data = bytearray(xbe_path.read_bytes())

            # Collect parameter values each pack might consume.  Only
            # player_physics uses this surface today — extend as
            # new parametric packs land.
            _PACK_PARAMS: dict[str, dict[str, float]] = {
                "player_physics": {
                    "gravity": (gravity_val if gravity_val is not None else 9.8),
                    "walk_speed_scale": walk_scale,
                    "roll_speed_scale": roll_scale,
                    "swim_speed_scale": swim_scale,
                    "jump_speed_scale": jump_scale,
                    "air_control_scale": air_control_scale,
                    "flap_height_scale": flap_scale,
                },
            }

            for pack in all_packs():
                if not _FLAG_PACKS.get(pack.name, False):
                    continue
                params = _PACK_PARAMS.get(pack.name, {})
                apply_pack(pack, xbe_data, params,
                           repo_root=_REPO_ROOT_FOR_RANDOMIZER)

            # Non-pack helper — standalone string-valued CLI flag.
            if player_char:
                apply_player_character_patch(xbe_data, player_char)

            xbe_path.write_bytes(xbe_data)
        else:
            print(f"\n[7/7] XBE patches — skipped (no patches opted in)")

        # Config.xbr patches (from --config-mod)
        config_mod_arg = getattr(args, 'config_mod', None)
        if config_mod_arg:
            config_xbr = extract_dir / CONFIG_XBR_REL
            if config_xbr.exists():
                print(f"\n  Applying config patches...")
                registry = load_registry()
                # Load the mod JSON (file path or inline JSON)
                try:
                    mod_path = Path(config_mod_arg)
                    if mod_path.exists():
                        mod = load_mod(str(mod_path))
                    else:
                        mod = json.loads(config_mod_arg)
                except Exception as e:
                    print(f"  WARNING: Could not parse --config-mod: {e}")
                    mod = None

                if mod:
                    config_data = bytearray(config_xbr.read_bytes())
                    applied = 0

                    # Variant-record patches (via config_registry)
                    patches = flatten_mod(mod, registry)
                    for p in patches:
                        prop = resolve_prop(registry, p["section"], p["entity"], p["property"])
                        if not prop:
                            continue
                        offset = int(prop["value_file_offset"], 16)
                        tf = prop.get("type_flag", 0)
                        if offset + 8 > len(config_data):
                            continue
                        write_value(config_data, offset, p["value"], tf)
                        applied += 1

                    # Keyed-table patches (direct cell offset doubles)
                    keyed = mod.get("_keyed_patches", {})
                    if keyed:
                        # keyed_tables ships as a proper submodule under
                        # azurik_mod.config; no need to dynamically load
                        # it from disk.
                        from azurik_mod.config import keyed_tables as ktp
                        # Only parse the sections we actually patch —
                        # load_all_tables is O(sections) so skipping
                        # the rest is a meaningful saving when the
                        # user is only editing one or two tables.
                        tables = ktp.load_all_tables(
                            str(config_xbr), sections=list(keyed.keys()))
                        for section_key, entities in keyed.items():
                            if section_key not in tables:
                                print(f"    WARNING: keyed section '{section_key}' not found")
                                continue
                            table = tables[section_key]
                            for entity_name, props in entities.items():
                                for prop_name, value in props.items():
                                    cell = table.get_value(entity_name, prop_name)
                                    if cell and cell[0] == "double":
                                        cell_off = cell[2]
                                        # Write double at cell_offset + 8
                                        struct.pack_into("<d", config_data,
                                                         cell_off + 8, float(value))
                                        applied += 1

                    config_xbr.write_bytes(config_data)
                    print(f"  Config patches: {applied} applied")

        # Obsidian lock thresholds (patched in town.xbr)
        if obsidian_cost is not None and "town" in modified_levels:
            print(f"\n  Patching obsidian lock thresholds (cost={obsidian_cost} per lock)...")
            town_data = modified_levels["town"]
            thresholds = [obsidian_cost * (i + 1) for i in range(OBSIDIAN_LOCK_COUNT)]
            patched_tables = 0
            for table_base in [OBSIDIAN_LOCK_TABLE_A, OBSIDIAN_LOCK_TABLE_B]:
                last_off = table_base + (OBSIDIAN_LOCK_COUNT - 1) * OBSIDIAN_LOCK_ENTRY_SIZE
                if last_off + 4 > len(town_data):
                    print(f"  WARNING: Lock table at 0x{table_base:X} out of range for town.xbr")
                    continue
                for i, thresh in enumerate(thresholds):
                    off = table_base + i * OBSIDIAN_LOCK_ENTRY_SIZE
                    struct.pack_into("<f", town_data, off, float(thresh))
                patched_tables += 1
            print(f"  Thresholds: {thresholds} ({patched_tables} tables patched)")

        # Write all modified level files back
        for level_name, data in modified_levels.items():
            level_file = extract_dir / GAMEDATA_REL / f"{level_name}.xbr"
            level_file.write_bytes(data)

        # Repack
        print(f"\nBuilding {out_path.name}...")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        run_xdvdfs(xdvdfs, ["pack", str(extract_dir), str(out_path)])

        if out_path.exists():
            size_mb = out_path.stat().st_size / (1024 * 1024)
            print(f"\n  Done! {out_path} ({size_mb:.1f} MB)")
            print(f"  Seed: {seed}  (use same seed to reproduce)")
        else:
            print("\n  ERROR: ISO creation failed")
            sys.exit(1)



# ---------------------------------------------------------------------------
# verify-patches command
# ---------------------------------------------------------------------------

_extract_xbe_from_iso = extract_xbe_from_iso
_read_xbe = read_xbe_bytes

def cmd_verify_patches(args):
    """Verify patches are correctly applied to a built XBE / ISO.

    Iterates every registered PatchSpec (fixed byte swap) and every
    ParametricPatch (slider-driven float).  For ParametricPatch it
    reports "default" vs "custom <value>" rather than applied/original.
    Whitelist-diff allows any byte inside a declared site range —
    parametric sites are declared, so custom slider values never
    trigger --strict failures."""
    from azurik_mod.patching import (
        verify_parametric_patch,
        verify_patch_spec,
        verify_trampoline_patch,
    )
    from azurik_mod.patching.registry import (
        all_packs,
        all_parametric_sites,
        all_patch_specs,
        all_trampoline_sites,
    )
    # Importing patches package ensures every pack has registered.
    import azurik_mod.patches  # noqa: F401

    target = Path(args.xbe if args.xbe else args.iso)
    if not target.exists():
        print(f"ERROR: target not found: {target}")
        sys.exit(1)

    print(f"Reading patched XBE from {target}...")
    patched = _read_xbe(target)
    print(f"  {len(patched)} bytes")

    specs = all_patch_specs()
    param_sites = all_parametric_sites()  # [(pack_name, ParametricPatch)]
    trampoline_sites = all_trampoline_sites()  # [(pack_name, TrampolinePatch)]

    # --- PatchSpec verification ---------------------------------------
    counts = {"applied": 0, "original": 0, "mismatch": 0, "out-of-range": 0}
    mismatches: list[tuple] = []
    unapplied: list[tuple] = []
    for spec in specs:
        status = verify_patch_spec(patched, spec)
        counts[status] += 1
        if status in ("mismatch", "out-of-range"):
            mismatches.append((spec, status))
        elif status == "original":
            unapplied.append((spec, status))

    print()
    print(f"PatchSpec status ({len(specs)} sites):")
    print(f"  applied:      {counts['applied']}")
    print(f"  original:     {counts['original']}  (patch NOT applied)")
    print(f"  mismatch:     {counts['mismatch']}  (bytes unrecognised)")
    print(f"  out-of-range: {counts['out-of-range']}")

    if unapplied:
        print()
        print("Sites still at original bytes (expected if a pack wasn't requested):")
        for spec, _ in unapplied:
            print(f"  VA=0x{spec.va:06X}  {spec.label}")

    if mismatches:
        print()
        print("*** MISMATCHES — bytes do not match original or patch ***")
        for spec, status in mismatches:
            hex_got = bytes(patched[spec.file_offset:
                                    spec.file_offset + len(spec.patch)]).hex()
            print(f"  [{status}] VA=0x{spec.va:06X}  {spec.label}")
            print(f"             got:      {hex_got}")
            print(f"             original: {spec.original.hex()}")
            print(f"             patch:    {spec.patch.hex()}")

    # --- ParametricPatch verification ---------------------------------
    if param_sites:
        print()
        print(f"Parametric sliders ({len(param_sites)} sites):")
        for pack_name, pp in param_sites:
            status = verify_parametric_patch(patched, pp)
            if status == "default":
                print(f"  [default ]  {pack_name}.{pp.name}  "
                      f"= {pp.default} {pp.unit}")
            elif status == "custom":
                from azurik_mod.patching import read_parametric_value
                v = read_parametric_value(patched, pp)
                print(f"  [custom  ]  {pack_name}.{pp.name}  "
                      f"= {v} {pp.unit}  (default {pp.default})")
            elif status == "virtual":
                print(f"  [virtual ]  {pack_name}.{pp.name}  "
                      f"(no XBE footprint; default {pp.default} {pp.unit})")
            elif status == "out-of-range":
                print(f"  [out-of-range] {pack_name}.{pp.name}  "
                      f"VA 0x{pp.va:X} past end of file")
                mismatches.append((pp, status))
            else:  # mismatch
                print(f"  [mismatch]  {pack_name}.{pp.name}  "
                      f"bytes decode to out-of-range value")
                mismatches.append((pp, status))

    # --- TrampolinePatch verification --------------------------------
    if trampoline_sites:
        print()
        print(f"Trampoline sites ({len(trampoline_sites)} sites):")
        for pack_name, tp in trampoline_sites:
            status = verify_trampoline_patch(patched, tp)
            marker = {
                "applied":      "[applied ]",
                "original":     "[original]",
                "mismatch":     "[mismatch]",
                "out-of-range": "[out-of-range]",
            }.get(status, f"[{status}]")
            print(f"  {marker}  {pack_name}.{tp.name}  VA=0x{tp.va:X}  "
                  f"shim={tp.shim_symbol}")
            if status in ("mismatch", "out-of-range"):
                mismatches.append((tp, status))

    # --- Safety-critical guard ---------------------------------------
    safety_sites = [s for s in specs if s.safety_critical]
    print()
    print(f"Safety-critical guard ({len(safety_sites)} sites):")
    safety_fail = False
    for spec in safety_sites:
        status = verify_patch_spec(patched, spec)
        ok = status in ("applied", "original")
        marker = "OK " if ok else "FAIL"
        print(f"  [{marker}] {status:<9s}  VA=0x{spec.va:06X}  {spec.label}")
        if not ok:
            safety_fail = True

    # --- Whitelist diff vs original ----------------------------------
    if args.original:
        orig_path = Path(args.original)
        if not orig_path.exists():
            print(f"\nERROR: original file not found: {orig_path}")
            sys.exit(1)
        print(f"\nWhitelist-diff against {orig_path}...")
        original = _read_xbe(orig_path)
        if len(original) != len(patched):
            print(f"  WARNING: size mismatch — original {len(original)} vs "
                  f"patched {len(patched)}; diff may be unreliable")

        # Allow ranges.  Five sources contribute:
        #   1. every fixed PatchSpec site (VA -> file offset + length);
        #   2. every non-virtual ParametricPatch site;
        #   3. each pack's `extra_whitelist_ranges`, which covers
        #      imperative byte-level patches like the popup-key nulls
        #      that aren't PatchSpecs but are still intentional writes;
        #   4. every TrampolinePatch site's 5-byte CALL/JMP + any
        #      NOP-fill bytes beyond that (site footprint);
        #   5. the shim-landing region carved out of .text padding /
        #      section growth, recovered by following each
        #      trampoline's rel32 target back to the shim;
        #   6. the .text section-header fields that grow_text_section
        #      may have rewritten (vsize + raw_size).
        allow_ranges: list[tuple[int, int]] = [
            (s.file_offset, s.file_offset + len(s.patch)) for s in specs
        ]
        for _pack, pp in param_sites:
            if not pp.is_virtual:
                allow_ranges.append(
                    (pp.file_offset, pp.file_offset + pp.size))
        for pack in all_packs():
            for lo, hi in pack.extra_whitelist_ranges:
                allow_ranges.append((lo, hi))
            # Dynamic ranges — packs whose apply function emits patches
            # at addresses chosen at apply time (e.g. player_physics
            # injects floats and rewrites FLD/FMUL refs to them).
            if pack.dynamic_whitelist_from_xbe is not None:
                try:
                    for lo, hi in pack.dynamic_whitelist_from_xbe(
                            bytes(patched)):
                        allow_ranges.append((lo, hi))
                except Exception as exc:  # noqa: BLE001
                    print(f"  WARNING: dynamic_whitelist_from_xbe for "
                          f"{pack.name!r} raised: {exc}")

        # Trampoline-specific whitelist contributions --------------------
        if trampoline_sites:
            from azurik_mod.patching import (
                file_to_va,
                parse_xbe_sections,
                va_to_file,
            )
            import struct as _struct

            for _pack, tp in trampoline_sites:
                site_off = tp.file_offset
                replaced_len = len(tp.replaced_bytes)
                # (4) the trampoline itself
                allow_ranges.append((site_off, site_off + replaced_len))

                # (5) the shim landing pad — follow the rel32 to find it.
                if site_off + 5 <= len(patched):
                    opcode = patched[site_off]
                    if opcode in (0xE8, 0xE9):
                        rel32 = _struct.unpack_from(
                            "<i", patched, site_off + 1)[0]
                        end_of_jump_va = tp.va + 5
                        target_va = end_of_jump_va + rel32
                        try:
                            target_off = va_to_file(target_va)
                            # Whitelist the shim's .text bytes.  We don't
                            # know the exact length here, but capping at
                            # the .text growth delta is a safe upper bound.
                            _, _secs = parse_xbe_sections(bytes(patched))
                            _text = next(
                                s for s in _secs if s["name"] == ".text")
                            _text_raw_end = (
                                _text["raw_addr"] + _text["raw_size"])
                            shim_end = min(target_off + 64, _text_raw_end)
                            allow_ranges.append((target_off, shim_end))
                        except ValueError:
                            pass  # shim target outside any section — will
                                  # surface as a mismatch elsewhere

            # (6) .text section-header fields (vsize at +8, raw_size at +16)
            if patched[:4] == b"XBEH":
                base_addr = _struct.unpack_from("<I", patched, 0x104)[0]
                section_count = _struct.unpack_from("<I", patched, 0x11C)[0]
                section_headers_addr = _struct.unpack_from(
                    "<I", patched, 0x120)[0]
                section_headers_offset = section_headers_addr - base_addr
                for _i in range(section_count):
                    off = section_headers_offset + _i * 56
                    name_addr = _struct.unpack_from("<I", patched, off + 20)[0]
                    name_offset = name_addr - base_addr
                    name_end = bytes(patched).index(b"\x00", name_offset)
                    name = patched[name_offset:name_end].decode(
                        "ascii", errors="replace")
                    if name == ".text":
                        # vsize at +8 (4B) and raw_size at +16 (4B).
                        allow_ranges.append((off + 8, off + 12))
                        allow_ranges.append((off + 16, off + 20))
                        break

        allow_ranges.sort()

        def _in_allow(off: int) -> bool:
            for lo, hi in allow_ranges:
                if lo <= off < hi:
                    return True
                if off < lo:
                    return False
            return False

        n = min(len(original), len(patched))
        unexpected = 0
        first_unexpected: list[int] = []
        for i in range(n):
            if original[i] != patched[i] and not _in_allow(i):
                unexpected += 1
                if len(first_unexpected) < 16:
                    first_unexpected.append(i)
        if unexpected == 0:
            print("  Clean: every differing byte is inside a declared "
                  "patch site range.")
        else:
            print(f"  *** {unexpected} bytes differ outside any declared "
                  f"patch site range ***")
            print(f"  First offsets: "
                  f"{', '.join(f'0x{o:X}' for o in first_unexpected)}")
            if args.strict:
                safety_fail = True

    if mismatches or safety_fail:
        sys.exit(1)


# ---------------------------------------------------------------------------
# apply-physics — gravity + player-speed slider handler
# ---------------------------------------------------------------------------


def cmd_inspect_physics(args):
    """Read an XBE / ISO and report the current physics-patch state.

    Dumps: which sliders are vanilla vs patched, the injected float
    values for walk/roll/swim/jump, gravity, roll force-always-on
    state, and enable_dev_menu trampoline status.  Useful for
    verifying that a built ISO actually contains the patches you
    expect — if 'doesn't work' in gameplay, run this first to
    confirm bytes are where they should be.
    """
    import struct as _struct
    from pathlib import Path as _Path
    from azurik_mod.iso.pack import extract_xbe_from_iso
    from azurik_mod.patching.xbe import parse_xbe_sections, va_to_file
    from azurik_mod.patches.player_physics import (
        _AIR_CONTROL_IMM32_VANILLA, _AIR_CONTROL_SITE_VAS,
        _FLAP_SITE_VA, _FLAP_SITE_VANILLA,
        _JUMP_SITE_VA, _JUMP_SITE_VANILLA,
        _ROLL_EDGE_LOCK_PATCH, _ROLL_EDGE_LOCK_VA,
        _ROLL_FORCE_ON_1_PATCH, _ROLL_FORCE_ON_1_VA,
        _ROLL_FORCE_ON_2_PATCH, _ROLL_FORCE_ON_2_VA,
        _ROLL_SITE_VA, _ROLL_SITE_VANILLA,
        _SWIM_SITE_VA, _SWIM_SITE_VANILLA,
        _WALK_SITE_VA, _WALK_SITE_VANILLA,
    )

    iso_arg = getattr(args, "iso", None)
    xbe_arg = getattr(args, "xbe", None)
    if iso_arg:
        iso_path = _Path(iso_arg)
        if not iso_path.exists():
            print(f"ERROR: ISO not found: {iso_path}")
            sys.exit(1)
        xbe_bytes = bytes(extract_xbe_from_iso(iso_path))
    elif xbe_arg:
        xbe_bytes = _Path(xbe_arg).read_bytes()
    else:
        print("Pass --iso or --xbe.")
        sys.exit(1)

    def _resolve(va: int) -> int | None:
        _, secs = parse_xbe_sections(xbe_bytes)
        for s in secs:
            if s["vaddr"] <= va < s["vaddr"] + s["vsize"]:
                delta = va - s["vaddr"]
                if delta < s["raw_size"]:
                    return s["raw_addr"] + delta
        return None

    def _check_site(label, va, vanilla_bytes, prefix):
        off = va_to_file(va)
        current = bytes(xbe_bytes[off:off + len(vanilla_bytes)])
        if current == vanilla_bytes:
            print(f"  {label:14s} [VANILLA]  site VA 0x{va:X}")
            return
        if current[:2] == prefix:
            inject_va = _struct.unpack(
                "<I", current[2:6])[0]
            fo = _resolve(inject_va)
            if fo is None:
                print(f"  {label:14s} [PATCHED?] site rewritten "
                      f"but inject VA 0x{inject_va:X} not "
                      f"mappable — patch may be corrupt")
                return
            val = _struct.unpack(
                "<f", xbe_bytes[fo:fo + 4])[0]
            print(f"  {label:14s} [PATCHED]  inject VA "
                  f"0x{inject_va:X} = {val:.4f}")
            return
        print(f"  {label:14s} [DRIFTED]  got {current.hex()}")

    print(f"Inspecting: {iso_arg or xbe_arg}")
    print(f"  XBE size: {len(xbe_bytes):,} bytes\n")

    print("Player physics sliders:")
    # Gravity (direct constant at 0x1980A8)
    gfo = va_to_file(0x001980A8)
    gval = _struct.unpack("<f", xbe_bytes[gfo:gfo + 4])[0]
    gstate = "VANILLA" if abs(gval - 9.8) < 1e-4 else "PATCHED"
    print(f"  {'gravity':14s} [{gstate}]  value = {gval:.4f} m/s²")

    _check_site("walk",
                _WALK_SITE_VA, _WALK_SITE_VANILLA, b"\xD9\x05")
    _check_site("roll (FMUL)",
                _ROLL_SITE_VA, _ROLL_SITE_VANILLA, b"\xD8\x0D")
    _check_site("swim",
                _SWIM_SITE_VA, _SWIM_SITE_VANILLA, b"\xD8\x0D")
    _check_site("jump (FLD)",
                _JUMP_SITE_VA, _JUMP_SITE_VANILLA, b"\xD9\x05")
    _check_site("flap (FADD)",
                _FLAP_SITE_VA, _FLAP_SITE_VANILLA, b"\xD8\x05")

    # Air-control: 5 imm32 sites.  Each either has the vanilla 9.0
    # imm32 or has been rewritten to 9.0 × air_control_scale.
    print()
    print("Air-control speed (5 imm32 sites at entity+0x140):")
    for site_va in _AIR_CONTROL_SITE_VAS:
        off = va_to_file(site_va)
        current = bytes(xbe_bytes[off:off + 4])
        if current == _AIR_CONTROL_IMM32_VANILLA:
            print(f"  VA 0x{site_va:06X} [VANILLA]  "
                  f"imm32 = 9.0")
        else:
            val = _struct.unpack("<f", current)[0]
            print(f"  VA 0x{site_va:06X} [PATCHED]  "
                  f"imm32 = {val:.4f}  (= "
                  f"{val / 9.0:.3f}× vanilla)")

    # Roll aux: edge-lock + force-on sites.
    print("\nRoll auxiliary patches:")
    el_fo = va_to_file(_ROLL_EDGE_LOCK_VA)
    el = bytes(xbe_bytes[el_fo:el_fo + 2])
    el_state = ("[NOPED]  " if el == _ROLL_EDGE_LOCK_PATCH
                else "[VANILLA]")
    print(f"  edge-lock     {el_state} bytes = {el.hex()} "
          f"(VA 0x{_ROLL_EDGE_LOCK_VA:X})")
    f1_fo = va_to_file(_ROLL_FORCE_ON_1_VA)
    f1 = bytes(xbe_bytes[f1_fo:f1_fo + 2])
    f1_state = ("[PATCHED]" if f1 == _ROLL_FORCE_ON_1_PATCH
                else "[VANILLA]")
    print(f"  force-on #1   {f1_state} bytes = {f1.hex()} "
          f"(VA 0x{_ROLL_FORCE_ON_1_VA:X})")
    f2_fo = va_to_file(_ROLL_FORCE_ON_2_VA)
    f2 = bytes(xbe_bytes[f2_fo:f2_fo + 2])
    f2_state = ("[PATCHED]" if f2 == _ROLL_FORCE_ON_2_PATCH
                else "[VANILLA]")
    print(f"  force-on #2   {f2_state} bytes = {f2.hex()} "
          f"(VA 0x{_ROLL_FORCE_ON_2_VA:X})")

    # Dev-menu trampoline at VA 0x53750.
    print("\nenable_dev_menu trampoline:")
    hook_fo = va_to_file(0x00053750)
    hook = bytes(xbe_bytes[hook_fo:hook_fo + 7])
    if hook[0] == 0xE9 and hook[5:7] == b"\x90\x90":
        rel = _struct.unpack("<i", hook[1:5])[0]
        shim_va = 0x00053750 + 5 + rel
        _, secs = parse_xbe_sections(xbe_bytes)
        for s in secs:
            if s["vaddr"] <= shim_va < s["vaddr"] + s["vsize"]:
                print(f"  [INSTALLED] hook bytes = {hook.hex()} "
                      f"-> JMP to VA 0x{shim_va:X} "
                      f"(section {s['name']!r})")
                break
        else:
            print(f"  [INSTALLED?] hook bytes = {hook.hex()} "
                  f"-> VA 0x{shim_va:X} NOT IN A LOADED "
                  f"SECTION — patch may be broken")
    else:
        print(f"  [VANILLA]   hook bytes = {hook.hex()}")

    print()


def cmd_apply_physics(args):
    """Apply the player_physics pack to an XBE / ISO in-place.

    Supports any combination of ``--gravity``, ``--walk-speed``,
    ``--roll-speed`` (alias ``--run-speed`` for back-compat), and
    ``--swim-speed``.  All four target ``default.xbe`` directly
    (Phase 2 C1 moved speed sliders from config.xbr — dead data —
    to direct XBE code-site patches; see
    azurik_mod/patches/player_physics/__init__.py).  When --iso is
    given, the ISO is unpacked, patched, and repacked.  ``--xbe``
    accepts a raw XBE and applies everything in-place.
    """
    gravity = getattr(args, "gravity", None)
    walk_scale = float(getattr(args, "walk_speed", None) or 1.0)
    # Back-compat: --run-speed still works, but --roll-speed wins if
    # both are passed.  The 3.0 multiplier at VA 0x001A25BC is the
    # WHITE-button roll/dive boost, not a run modifier (see
    # docs/LEARNINGS.md § "Roll, not run").
    roll_scale = float(
        getattr(args, "roll_speed", None)
        or getattr(args, "run_speed", None)
        or 1.0)
    swim_scale = float(getattr(args, "swim_speed", None) or 1.0)
    jump_scale = float(getattr(args, "jump_speed", None) or 1.0)
    air_control_scale = float(
        getattr(args, "air_control_speed", None) or 1.0)
    flap_scale = float(getattr(args, "flap_height", None) or 1.0)

    if (gravity is None
            and walk_scale == 1.0
            and roll_scale == 1.0
            and swim_scale == 1.0
            and jump_scale == 1.0
            and air_control_scale == 1.0
            and flap_scale == 1.0):
        print("No physics changes requested.  "
              "Pass --gravity, --walk-speed, --roll-speed (or "
              "legacy --run-speed), --swim-speed, --jump-speed, "
              "--air-control-speed, and/or --flap-height.")
        return

    def _patch_xbe_in_place(xbe_path: Path) -> None:
        data = bytearray(xbe_path.read_bytes())
        apply_player_physics(
            data,
            gravity=float(gravity) if gravity is not None else None,
            walk_scale=walk_scale if walk_scale != 1.0 else None,
            roll_scale=roll_scale if roll_scale != 1.0 else None,
            swim_scale=swim_scale if swim_scale != 1.0 else None,
            jump_scale=jump_scale if jump_scale != 1.0 else None,
            air_control_scale=(air_control_scale
                               if air_control_scale != 1.0 else None),
            flap_scale=flap_scale if flap_scale != 1.0 else None,
        )
        xbe_path.write_bytes(data)

    iso_arg = getattr(args, "iso", None)
    if iso_arg:
        iso_path = Path(iso_arg)
        output_arg = getattr(args, "output", None)
        out_path = Path(output_arg) if output_arg else iso_path
        if not iso_path.exists():
            print(f"ERROR: ISO not found: {iso_path}")
            sys.exit(1)

        xdvdfs = require_xdvdfs()
        with tempfile.TemporaryDirectory(prefix="azurik_physics_") as tmp:
            extract = Path(tmp) / "game"
            print(f"Unpacking {iso_path.name}...")
            run_xdvdfs(xdvdfs, ["unpack", str(iso_path), str(extract)])
            verify_extracted_iso(extract)

            xbe = extract / "default.xbe"
            if not xbe.exists():
                print(f"ERROR: default.xbe missing from unpacked ISO")
                sys.exit(1)
            _patch_xbe_in_place(xbe)

            out_path.parent.mkdir(parents=True, exist_ok=True)
            print(f"Packing {out_path.name}...")
            run_xdvdfs(xdvdfs, ["pack", str(extract), str(out_path)])
            if out_path.exists():
                size_mb = out_path.stat().st_size / (1024 * 1024)
                print(f"  Done! {out_path} ({size_mb:.1f} MB)")
            else:
                print("  ERROR: ISO pack failed")
                sys.exit(1)
        return

    # Raw XBE mode (no ISO).
    xbe_arg = getattr(args, "xbe", None)
    if not xbe_arg:
        print("ERROR: apply-physics requires either --iso or --xbe")
        sys.exit(1)
    xbe_path = Path(xbe_arg)
    if not xbe_path.exists():
        print(f"ERROR: XBE not found: {xbe_path}")
        sys.exit(1)
    _patch_xbe_in_place(xbe_path)
    print(f"  Wrote patched XBE: {xbe_path}")


__all__ = [
    "cmd_apply_physics",
    "cmd_list",
    "cmd_dump",
    "cmd_diff",
    "cmd_patch",
    "cmd_randomize_gems",
    "cmd_randomize",
    "cmd_randomize_full",
    "cmd_verify_patches",
]
