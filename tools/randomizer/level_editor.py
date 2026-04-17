#!/usr/bin/env python3
"""Azurik Level XBR Entity Editor — read and modify entity positions & types.

Discovered binary format:
  - Entity names are stored inline as null-terminated ASCII strings
  - Each name is preceded by byte 0x3F (last byte of IEEE 754 float 1.0)
  - XYZ world coordinates (3 floats + 1 zero) are at exactly -96 bytes
    from the entity name string start
  - Name field is always 20 bytes, null-padded after the ASCII name

Usage:
    python level_editor.py list      game_files/gamedata/a3.xbr
    python level_editor.py list      game_files/gamedata/a3.xbr --category gems
    python level_editor.py shuffle   game_files/gamedata/a3.xbr --category gems --output a3_shuffled.xbr
    python level_editor.py swap      game_files/gamedata/a3.xbr diamond emerald --output a3_swapped.xbr
    python level_editor.py move      game_files/gamedata/a3.xbr diamond -- -200 50 -100 --output a3_moved.xbr
    python level_editor.py rename    game_files/gamedata/a3.xbr power_water_a3 power_fire_a3 --output a3_fire.xbr
    python level_editor.py randomize game_files/gamedata/a3.xbr --seed 42 --output a3_randomized.xbr
"""
import argparse
import random
import struct
import sys
from pathlib import Path


# ── Entity discovery ──────────────────────────────────────────────────────

COORD_OFFSET = -96  # bytes before name string where XYZ floats live
NAME_FIELD_SIZE = 20  # fixed-width name field in critterGenerator entries

# Coordinate offsets to try (in priority order):
#   -96:  standard child nodes (pattern 1)
#   -116: critterGenerator-typed nodes with type label at -76 (pattern 2)
#   -144: alternate parent nodes without 1.0f before name (pattern 3)
#   -140: fallback for pattern 3 when -144 doesn't validate
COORD_OFFSETS_TO_TRY = [-96, -116, -144, -140]

# The five gem types in the game
GEM_TYPES = ["diamond", "emerald", "sapphire", "obsidian", "ruby"]

# Category keywords for grouping
CATEGORIES = {
    "gems": ["diamond", "emerald", "sapphire", "obsidian", "ruby"],
    "enemies": ["elemental", "splinter", "shard", "overlord", "gargoyle",
                "channeler", "flicken", "shadow_demon"],
    "powerups": ["power_"],
    "fuel": ["frag_", "fuel"],
}

# Entity name prefixes to search for directly (catches nodes without 1.0f marker)
DIRECT_SEARCH_PREFIXES = [
    b"power_water", b"power_air", b"power_earth", b"power_fire",
    b"power_ammo", b"power_staff",
    b"frag_water", b"frag_fire", b"frag_earth", b"frag_air", b"frag_life",
]


def _try_coords(data: bytes, name_offset: int) -> tuple[float, float, float, int] | None:
    """Try multiple coordinate offsets, return (x, y, z, coord_offset) or None."""
    for co in COORD_OFFSETS_TO_TRY:
        cp = name_offset + co
        if cp < 0 or cp + 16 > len(data):
            continue
        x, y, z, w = struct.unpack_from("<4f", data, cp)
        if w == 0.0 and all(abs(v) < 50000 for v in [x, y, z] if v == v):
            return x, y, z, cp
    return None


def find_entities(data: bytes) -> list[dict]:
    """Find all named entities using two complementary scan methods.

    Method 1 (1.0f marker scan): Finds entities preceded by 0x3F800000.
    Tries coords at -96 (standard) and -116 (critterGenerator-typed nodes).

    Method 2 (direct name search): Searches for known power/fragment name
    strings and validates by checking for 1.0f at name_start + 24.  Catches
    entities that lack the pre-name 1.0f marker entirely.
    """
    entities = []
    seen_offsets: set[int] = set()

    # ── Method 1: 1.0f marker scan (existing approach) ────────────────
    i = 0
    while i < len(data) - 8:
        # Pattern: 00 00 80 3F followed by printable ASCII (≥2 chars) then null
        if (data[i] == 0x00 and data[i+1] == 0x00
                and data[i+2] == 0x80 and data[i+3] == 0x3F
                and i + 4 < len(data) and 33 <= data[i+4] < 127):

            s_start = i + 4
            s_end = s_start
            while s_end < len(data) and data[s_end] != 0 and s_end - s_start < 200:
                if data[s_end] < 32 or data[s_end] >= 127:
                    break
                s_end += 1

            if s_end < len(data) and data[s_end] == 0 and s_end - s_start >= 2:
                name = data[s_start:s_end].decode("ascii")

                result = _try_coords(data, s_start)
                if result is not None:
                    x, y, z, cp = result
                    cat = categorize(name)
                    entities.append({
                        "name": name,
                        "name_offset": s_start,
                        "name_len": s_end - s_start,
                        "coord_offset": cp,
                        "x": x, "y": y, "z": z,
                        "category": cat,
                    })
                    seen_offsets.add(s_start)

                i = s_end + 1
                continue
        i += 1

    # ── Method 2: direct name search for powers/fragments ─────────────
    for prefix in DIRECT_SEARCH_PREFIXES:
        idx = 0
        while True:
            idx = data.find(prefix, idx)
            if idx == -1:
                break
            if idx in seen_offsets:
                idx += 1
                continue

            # Read full null-terminated name
            s_end = idx
            while s_end < len(data) and data[s_end] != 0 and s_end - idx < 30:
                if data[s_end] < 32 or data[s_end] >= 127:
                    break
                s_end += 1

            if s_end >= len(data) or data[s_end] != 0 or s_end - idx < 2:
                idx += 1
                continue

            name = data[idx:s_end].decode("ascii")

            # Skip suffixed debug names (e.g. "power_waterLocator")
            # Real entity names are short: power_X, frag_X_N
            if any(sub in name for sub in ["Locator", "Shape", "Snap"]):
                idx += 1
                continue

            # Validate: real node entities have 1.0f after the name field.
            # Name field size varies with name length, so scan a window.
            found_post_marker = False
            for post_off in range(20, 40, 4):
                if idx + post_off + 4 > len(data):
                    break
                if struct.unpack_from("<I", data, idx + post_off)[0] == 0x3F800000:
                    found_post_marker = True
                    break
            if not found_post_marker:
                idx += 1
                continue

            # Extract coordinates -- skip entity if coords can't be found
            # (writing to an invalid coord_offset would corrupt the file)
            result = _try_coords(data, idx)
            if result is None:
                idx = s_end + 1
                continue
            x, y, z, cp = result

            cat = categorize(name)
            entities.append({
                "name": name,
                "name_offset": idx,
                "name_len": s_end - idx,
                "coord_offset": cp,
                "x": x, "y": y, "z": z,
                "category": cat,
            })
            seen_offsets.add(idx)
            idx = s_end + 1

    return entities


def categorize(name: str) -> str:
    lower = name.lower()
    for cat, keywords in CATEGORIES.items():
        if any(kw in lower for kw in keywords):
            return cat
    return "other"


# ── Commands ──────────────────────────────────────────────────────────────

def cmd_list(args):
    data = Path(args.xbr).read_bytes()
    entities = find_entities(data)

    if args.category:
        entities = [e for e in entities if e["category"] == args.category]

    if not entities:
        print("No entities found" + (f" in category '{args.category}'" if args.category else ""))
        return

    # Group by category
    by_cat = {}
    for e in entities:
        by_cat.setdefault(e["category"], []).append(e)

    for cat in ["gems", "enemies", "powerups", "fuel", "other"]:
        group = by_cat.get(cat, [])
        if not group:
            continue
        print(f"\n  [{cat.upper()}]")
        for e in group:
            print(f"    {e['name']:<30} pos=({e['x']:10.2f}, {e['y']:10.2f}, {e['z']:10.2f})"
                  f"  @0x{e['name_offset']:08X}")

    print(f"\n  Total: {len(entities)} entities")


def cmd_shuffle(args):
    data = bytearray(Path(args.xbr).read_bytes())
    entities = find_entities(data)

    if args.category:
        targets = [e for e in entities if e["category"] == args.category]
    else:
        targets = entities

    if len(targets) < 2:
        print("Need at least 2 entities to shuffle")
        sys.exit(1)

    # Collect original positions
    positions = [(e["x"], e["y"], e["z"]) for e in targets]
    names = [e["name"] for e in targets]

    # Shuffle positions
    if args.seed is not None:
        random.seed(args.seed)
    random.shuffle(positions)

    # Apply shuffled positions
    print(f"Shuffling {len(targets)} {args.category or 'all'} entities:\n")
    for e, (nx, ny, nz) in zip(targets, positions):
        old = f"({e['x']:.2f}, {e['y']:.2f}, {e['z']:.2f})"
        new = f"({nx:.2f}, {ny:.2f}, {nz:.2f})"
        changed = "~" if old != new else "="
        print(f"  {changed} {e['name']:<25} {old} -> {new}")
        struct.pack_into("<3f", data, e["coord_offset"], nx, ny, nz)

    write_output(data, args)


def cmd_swap(args):
    data = bytearray(Path(args.xbr).read_bytes())
    entities = find_entities(data)

    name_map = {e["name"]: e for e in entities}
    a = name_map.get(args.entity_a)
    b = name_map.get(args.entity_b)

    if not a:
        print(f"ERROR: Entity '{args.entity_a}' not found")
        print(f"  Available: {', '.join(sorted(name_map.keys()))}")
        sys.exit(1)
    if not b:
        print(f"ERROR: Entity '{args.entity_b}' not found")
        print(f"  Available: {', '.join(sorted(name_map.keys()))}")
        sys.exit(1)

    print(f"Swapping positions:")
    print(f"  {a['name']}: ({a['x']:.2f}, {a['y']:.2f}, {a['z']:.2f}) -> ({b['x']:.2f}, {b['y']:.2f}, {b['z']:.2f})")
    print(f"  {b['name']}: ({b['x']:.2f}, {b['y']:.2f}, {b['z']:.2f}) -> ({a['x']:.2f}, {a['y']:.2f}, {a['z']:.2f})")

    struct.pack_into("<3f", data, a["coord_offset"], b["x"], b["y"], b["z"])
    struct.pack_into("<3f", data, b["coord_offset"], a["x"], a["y"], a["z"])

    write_output(data, args)


def cmd_move(args):
    data = bytearray(Path(args.xbr).read_bytes())
    entities = find_entities(data)

    name_map = {e["name"]: e for e in entities}
    e = name_map.get(args.entity)
    if not e:
        print(f"ERROR: Entity '{args.entity}' not found")
        print(f"  Available: {', '.join(sorted(name_map.keys()))}")
        sys.exit(1)

    nx, ny, nz = args.x, args.y, args.z
    print(f"Moving {e['name']}:")
    print(f"  ({e['x']:.2f}, {e['y']:.2f}, {e['z']:.2f}) -> ({nx:.2f}, {ny:.2f}, {nz:.2f})")

    struct.pack_into("<3f", data, e["coord_offset"], nx, ny, nz)

    write_output(data, args)


def cmd_rename(args):
    data = bytearray(Path(args.xbr).read_bytes())
    entities = find_entities(data)

    name_map = {e["name"]: e for e in entities}
    e = name_map.get(args.old_name)
    if not e:
        print(f"ERROR: Entity '{args.old_name}' not found")
        print(f"  Available: {', '.join(sorted(name_map.keys()))}")
        sys.exit(1)

    new_name = args.new_name
    old_len = e["name_len"]

    if len(new_name) > old_len:
        print(f"ERROR: New name '{new_name}' ({len(new_name)} chars) is longer than "
              f"old name '{e['name']}' ({old_len} chars)")
        print(f"  The new name must be <= {old_len} characters to avoid corrupting adjacent data.")
        sys.exit(1)

    # Write new name, padded with nulls
    offset = e["name_offset"]
    new_bytes = new_name.encode("ascii") + b"\x00" * (old_len - len(new_name))
    data[offset:offset + old_len] = new_bytes

    print(f"Renamed: '{e['name']}' -> '{new_name}'")
    if len(new_name) < old_len:
        print(f"  (padded with {old_len - len(new_name)} null bytes)")

    write_output(data, args)


def _gem_base_type(name: str) -> str | None:
    """Return the base gem type if *name* is a gem entity, else None.

    Handles both plain names (``diamond``) and suffixed variants like
    ``sapphire_move``.  The match is case-insensitive.
    """
    lower = name.lower()
    for gem in GEM_TYPES:
        if lower == gem or lower.startswith(gem + "_"):
            return gem
    return None


def cmd_randomize(args):
    """Randomize gem types across all gem spawn points in a level.

    For every entity whose name matches a gem type (diamond, emerald,
    sapphire, obsidian, ruby — including suffixed variants like
    ``sapphire_move``), the command collects the *base* gem type,
    shuffles the list of types, and writes the new names back into the
    20-byte name fields.

    Positions are never touched — only the name field changes, which
    causes the game engine to spawn a different gem type at the same
    location.

    Suffixed names retain their suffix after randomization (e.g.
    ``sapphire_move`` may become ``ruby_move``).
    """
    data = bytearray(Path(args.xbr).read_bytes())
    entities = find_entities(data)

    # Identify gem entities
    gem_entities = []
    for e in entities:
        base = _gem_base_type(e["name"])
        if base is not None:
            suffix = e["name"][len(base):]  # e.g. "_move" or ""
            gem_entities.append({**e, "gem_base": base, "gem_suffix": suffix})

    if not gem_entities:
        print("ERROR: No gem entities found in this level.")
        sys.exit(1)

    # Optionally filter which gem types participate
    if args.types:
        allowed = set(args.types)
        invalid = allowed - set(GEM_TYPES)
        if invalid:
            print(f"ERROR: Unknown gem type(s): {', '.join(sorted(invalid))}")
            print(f"  Valid types: {', '.join(GEM_TYPES)}")
            sys.exit(1)
        participating = [e for e in gem_entities if e["gem_base"] in allowed]
        excluded = [e for e in gem_entities if e["gem_base"] not in allowed]
    else:
        participating = gem_entities
        excluded = []

    if len(participating) < 2:
        print("ERROR: Need at least 2 gem entities to randomize.")
        if excluded:
            print(f"  ({len(excluded)} gem(s) excluded by --types filter)")
        sys.exit(1)

    # Collect the base types to shuffle
    base_types = [e["gem_base"] for e in participating]

    # Seed RNG
    rng = random.Random(args.seed)
    rng.shuffle(base_types)

    # Report distribution before and after
    from collections import Counter
    before_dist = Counter(e["gem_base"] for e in participating)
    after_dist = Counter(base_types)

    print(f"Randomizing {len(participating)} gem entities (seed={args.seed}):\n")

    if excluded:
        print(f"  Excluded ({len(excluded)}, unchanged):")
        for e in excluded:
            print(f"    {e['name']:<25} pos=({e['x']:.2f}, {e['y']:.2f}, {e['z']:.2f})")
        print()

    changes = 0
    for e, new_base in zip(participating, base_types):
        old_name = e["name"]
        new_name = new_base + e["gem_suffix"]

        # Safety: new name must fit in 20-byte field
        if len(new_name) > NAME_FIELD_SIZE:
            print(f"  WARNING: '{new_name}' ({len(new_name)} chars) exceeds {NAME_FIELD_SIZE}-byte field, skipping")
            continue

        changed = old_name != new_name
        if changed:
            changes += 1
        marker = "~" if changed else "="

        print(f"  {marker} {old_name:<25} -> {new_name:<25}"
              f"  pos=({e['x']:.2f}, {e['y']:.2f}, {e['z']:.2f})")

        # Write new name into the 20-byte field (null-padded)
        offset = e["name_offset"]
        new_bytes = new_name.encode("ascii").ljust(NAME_FIELD_SIZE, b"\x00")
        data[offset:offset + NAME_FIELD_SIZE] = new_bytes

    print(f"\n  Distribution: {dict(before_dist)} -> {dict(after_dist)}")
    print(f"  Changed: {changes}/{len(participating)} entities")

    write_output(data, args)


def write_output(data: bytearray, args):
    if not args.output:
        print("\n  Use --output <path> to save. (Dry run — no file written)")
        return

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    size_mb = len(data) / (1024 * 1024)
    print(f"\n  Wrote: {out} ({size_mb:.2f} MB)")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Azurik level XBR entity editor — shuffle, swap, move, rename",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s list    a3.xbr\n"
            "  %(prog)s list    a3.xbr --category gems\n"
            "  %(prog)s shuffle a3.xbr --category gems -o a3_shuffled.xbr\n"
            "  %(prog)s swap    a3.xbr diamond emerald -o a3_swapped.xbr\n"
            "  %(prog)s move    a3.xbr diamond -- -200 50 -100 -o a3_moved.xbr\n"
            "  %(prog)s rename  a3.xbr power_water_a3 power_fire_a3 -o a3_fire.xbr\n"
            "  %(prog)s randomize a3.xbr --seed 42 -o a3_randomized.xbr\n"
            "  %(prog)s randomize a3.xbr --seed 42 --types diamond ruby -o a3_rand.xbr\n"
        ),
    )

    sub = parser.add_subparsers(dest="command")

    # list
    p_list = sub.add_parser("list", help="List all entities with coordinates")
    p_list.add_argument("xbr", help="Level .xbr file")
    p_list.add_argument("--category", "-c",
                        choices=list(CATEGORIES.keys()) + ["other"],
                        help="Filter by category")

    # shuffle
    p_shuf = sub.add_parser("shuffle", help="Randomly shuffle entity positions")
    p_shuf.add_argument("xbr", help="Level .xbr file")
    p_shuf.add_argument("--category", "-c",
                         choices=list(CATEGORIES.keys()) + ["other"],
                         help="Only shuffle entities in this category")
    p_shuf.add_argument("--seed", "-s", type=int, help="Random seed for reproducibility")
    p_shuf.add_argument("--output", "-o", help="Output file path")

    # swap
    p_swap = sub.add_parser("swap", help="Swap positions of two entities")
    p_swap.add_argument("xbr", help="Level .xbr file")
    p_swap.add_argument("entity_a", help="First entity name")
    p_swap.add_argument("entity_b", help="Second entity name")
    p_swap.add_argument("--output", "-o", help="Output file path")

    # move
    p_move = sub.add_parser("move", help="Move an entity to specific coordinates")
    p_move.add_argument("xbr", help="Level .xbr file")
    p_move.add_argument("entity", help="Entity name")
    p_move.add_argument("x", type=float, help="X coordinate")
    p_move.add_argument("y", type=float, help="Y coordinate")
    p_move.add_argument("z", type=float, help="Z coordinate")
    p_move.add_argument("--output", "-o", help="Output file path")

    # rename
    p_ren = sub.add_parser("rename", help="Rename an entity (e.g., change power-up type)")
    p_ren.add_argument("xbr", help="Level .xbr file")
    p_ren.add_argument("old_name", help="Current entity name")
    p_ren.add_argument("new_name", help="New entity name (must be <= current length)")
    p_ren.add_argument("--output", "-o", help="Output file path")

    # randomize
    p_rand = sub.add_parser("randomize",
                            help="Randomize gem types across spawn points",
                            description=(
                                "Shuffle gem type names across all gem spawn points in a level XBR.\n"
                                "Positions stay fixed — only the 20-byte name field changes,\n"
                                "causing different gem types to spawn at existing locations.\n"
                                "Total gem count per level is preserved."
                            ))
    p_rand.add_argument("xbr", help="Level .xbr file")
    p_rand.add_argument("--seed", "-s", type=int, default=None,
                         help="Random seed for reproducible results")
    p_rand.add_argument("--types", "-t", nargs="+",
                         choices=GEM_TYPES, metavar="TYPE",
                         help="Only shuffle these gem types (default: all five)")
    p_rand.add_argument("--output", "-o", help="Output file path")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    xbr_path = Path(args.xbr)
    if not xbr_path.exists():
        print(f"ERROR: File not found: {xbr_path}")
        sys.exit(1)

    cmds = {"list": cmd_list, "shuffle": cmd_shuffle,
            "swap": cmd_swap, "move": cmd_move, "rename": cmd_rename,
            "randomize": cmd_randomize}
    cmds[args.command](args)


if __name__ == "__main__":
    main()
