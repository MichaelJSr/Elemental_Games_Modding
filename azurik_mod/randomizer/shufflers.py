"""Randomizer data tables and shared helpers.

Home of every module-level constant (LEVEL_XBRS, GEM_TYPES, LEVEL_PATHS,
KEY_REALMS, BARRIER_OFFSETS, BARRIER_FOURCCS, OBSIDIAN_LOCK_*, etc.) plus
the pure-function helpers that `commands.py` orchestrates into the
CLI randomizer pipelines (`cmd_randomize*`).
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

from azurik_mod.config import REGISTRY_PATH
from azurik_mod.iso.pack import GAMEDATA_REL

# ---------------------------------------------------------------------------
# Registry + binary helpers
# ---------------------------------------------------------------------------

_registry_cache = None

def load_registry() -> dict:
    global _registry_cache
    if _registry_cache is not None:
        return _registry_cache
    if not REGISTRY_PATH.exists():
        print(f"ERROR: Registry not found at {REGISTRY_PATH}")
        sys.exit(1)
    with open(REGISTRY_PATH, "r") as f:
        try:
            _registry_cache = json.load(f)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in {REGISTRY_PATH.name}: {e}")
            sys.exit(1)
    return _registry_cache


def read_value(data: bytes, offset: int, type_flag: int):
    """Read a config value from config.xbr at the given offset.

    Config values are stored as 8-byte IEEE 754 doubles (little-endian).
    The offset should point to the start of the double (record_base + 4).
    """
    if type_flag == 2:
        # Integer values are doubles that represent whole numbers
        return int(struct.unpack_from("<d", data, offset)[0])
    return struct.unpack_from("<d", data, offset)[0]


def write_value(data: bytearray, offset: int, value, type_flag: int):
    """Write a config value to config.xbr at the given offset.

    Writes an 8-byte IEEE 754 double (little-endian).
    """
    if type_flag == 2:
        struct.pack_into("<d", data, offset, float(int(value)))
    else:
        struct.pack_into("<d", data, offset, float(value))


def format_value(val, type_flag: int) -> str:
    if type_flag == 2:
        return str(int(val)) if isinstance(val, (int, float)) else str(val)
    if isinstance(val, float):
        if val == int(val):
            return f"{val:.1f}"
        return f"{val:.4f}"
    return str(val)


def resolve_prop(registry: dict, section: str, entity: str, prop: str) -> dict | None:
    sec = registry.get("sections", {}).get(section)
    if not sec:
        return None
    ent = sec.get("entities", {}).get(entity)
    if not ent:
        return None
    p = ent.get("properties", {}).get(prop)
    if p and p.get("type_flag", 0) > 2:
        # type_flag > 2 means this offset lands in the string table — invalid
        return None
    return p


def load_mod(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        print(f"ERROR: Mod file not found: {p}")
        sys.exit(1)
    with open(p, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in {p.name}")
            print(f"  Line {e.lineno}, column {e.colno}: {e.msg}")
            lines = e.doc.splitlines()
            if 0 < e.lineno <= len(lines):
                print(f"  > {lines[e.lineno - 1]}")
            print(f"\n  Hint: Check for trailing commas, missing quotes, or unmatched brackets.")
            sys.exit(1)



# ---------------------------------------------------------------------------
# Mod format handling — grouped and legacy flat
# ---------------------------------------------------------------------------

def flatten_mod(mod: dict, registry: dict) -> list[dict]:
    """Convert any mod format into a flat list of patch dicts.

    Supports:
      - Legacy flat: mod has "patches" key with list of {section, entity, property, value}
      - Grouped: mod has "sections" key with nested element/role/entity/property structure
    """
    # Legacy flat format
    if "patches" in mod:
        return mod["patches"]

    # Grouped format: walk the nested tree to find entity names
    if "sections" not in mod:
        return []

    patches = []
    reg_sections = registry.get("sections", {})

    for sec_name, sec_content in mod["sections"].items():
        if not isinstance(sec_content, dict):
            continue

        # Build set of known entity names for this section
        reg_sec = reg_sections.get(sec_name, {})
        known_entities = set(reg_sec.get("entities", {}).keys())

        def _walk(obj, path=""):
            """Recursively walk until we hit a known entity name,
            then treat its children as property: value pairs."""
            for key, val in obj.items():
                if key.startswith("_"):
                    continue
                if not isinstance(val, dict):
                    continue
                if key in known_entities:
                    # Check if children are property values (numbers) or
                    # nested groups (dicts) — handles name collisions like
                    # "debug" being both a role name and an entity name
                    has_dicts = any(isinstance(v, dict) for v in val.values())
                    if has_dicts:
                        # Children are dicts — this is a group, recurse
                        _walk(val, f"{path}/{key}")
                    else:
                        # Children are values — this is an entity
                        for prop_name, prop_val in val.items():
                            if isinstance(prop_val, (int, float)) and not isinstance(prop_val, bool):
                                patches.append({
                                    "section": sec_name,
                                    "entity": key,
                                    "property": prop_name,
                                    "value": prop_val,
                                })
                else:
                    # Group or role name — recurse deeper
                    _walk(val, f"{path}/{key}")

        _walk(sec_content)

    return patches



# ---------------------------------------------------------------------------
# Level entity patching (uses level_editor.py discovery format)
# ---------------------------------------------------------------------------

def find_level_entities(data: bytes) -> dict[str, dict]:
    """Find named entities in a level XBR using the 1.0f+name coordinate pattern.

    Returns dict keyed by entity name with coord_offset, x, y, z, name_offset, name_len.
    Tries multiple coordinate offsets: -96 (standard), -116 (typed nodes), -144, -140.
    """
    COORD_OFFSETS = [-96, -116, -144, -140]
    MARKER = b"\x00\x00\x80\x3F"
    entities = {}
    i = data.find(MARKER, 0)
    while 0 <= i < len(data) - 8:
        if i + 4 < len(data) and 33 <= data[i+4] < 127:
            s_start = i + 4
            s_end = s_start
            while s_end < len(data) and data[s_end] != 0 and s_end - s_start < 200:
                if data[s_end] < 32 or data[s_end] >= 127:
                    break
                s_end += 1
            if s_end < len(data) and data[s_end] == 0 and s_end - s_start >= 2:
                name = data[s_start:s_end].decode("ascii")
                for co in COORD_OFFSETS:
                    cp = s_start + co
                    if cp >= 0 and cp + 16 <= len(data):
                        x, y, z, w = struct.unpack_from("<4f", data, cp)
                        if (w == 0.0
                                and x == x and y == y and z == z
                                and all(abs(v) < 50000 for v in [x, y, z])):
                            entities[name] = {
                                "name_offset": s_start,
                                "name_len": s_end - s_start,
                                "coord_offset": cp,
                                "x": x, "y": y, "z": z,
                            }
                            break
                i = data.find(MARKER, s_end + 1)
                continue
        i = data.find(MARKER, i + 1)
    return entities


def find_null_terminated_string(data: bytes, name: str) -> list[dict]:
    """Find all occurrences of a null-terminated ASCII string in the file.

    Returns list of {offset, length} for each match where the string is
    preceded by a null byte (or is at position 0) and followed by a null byte.
    """
    needle = name.encode("ascii")
    results = []
    pos = 0
    while True:
        pos = data.find(needle, pos)
        if pos == -1:
            break
        end = pos + len(needle)
        # Must be null-terminated
        if end < len(data) and data[end] == 0:
            # Must be preceded by null or start of file (not mid-string)
            if pos == 0 or data[pos - 1] == 0 or data[pos - 1] < 32:
                # Verify the full string matches exactly (no trailing chars before null)
                results.append({"offset": pos, "length": len(needle)})
        pos += 1
    return results


def apply_level_patches(data: bytearray, patches: list[dict]) -> tuple[int, int]:
    """Apply move/rename patches to a level XBR. Returns (applied, errors)."""
    entities = find_level_entities(data)
    applied = errors = 0

    for p in patches:
        ent_name = p.get("entity", "")
        action = p.get("action", "move")

        if action == "move":
            ent = entities.get(ent_name)
            if not ent:
                print(f"    ERROR: Entity '{ent_name}' not found (1.0f+name pattern)")
                errors += 1
                continue
            nx, ny, nz = float(p["x"]), float(p["y"]), float(p["z"])
            old = f"({ent['x']:.2f}, {ent['y']:.2f}, {ent['z']:.2f})"
            new = f"({nx:.2f}, {ny:.2f}, {nz:.2f})"
            struct.pack_into("<3f", data, ent["coord_offset"], nx, ny, nz)
            print(f"    {ent_name}: {old} -> {new}")
            applied += 1

        elif action == "rename":
            new_name = p["new_name"]

            # First try: 1.0f+name pattern entities
            ent = entities.get(ent_name)
            if ent:
                old_len = ent["name_len"]
                if len(new_name) > old_len:
                    print(f"    ERROR: '{new_name}' ({len(new_name)} chars) > "
                          f"'{ent_name}' ({old_len} chars)")
                    errors += 1
                    continue
                offset = ent["name_offset"]
                new_bytes = new_name.encode("ascii") + b"\x00" * (old_len - len(new_name))
                data[offset:offset + old_len] = new_bytes
                print(f"    rename: '{ent_name}' -> '{new_name}' @0x{offset:08X}")
                applied += 1
                continue

            # Fallback: search for null-terminated string anywhere in file
            matches = find_null_terminated_string(data, ent_name)
            if not matches:
                print(f"    ERROR: Entity '{ent_name}' not found anywhere in file")
                errors += 1
                continue

            if len(new_name) > len(ent_name):
                print(f"    ERROR: '{new_name}' ({len(new_name)} chars) > "
                      f"'{ent_name}' ({len(ent_name)} chars) — would corrupt adjacent data")
                errors += 1
                continue

            # Rename all occurrences
            for match in matches:
                offset = match["offset"]
                old_len = match["length"]
                new_bytes = new_name.encode("ascii") + b"\x00" * (old_len - len(new_name))
                data[offset:offset + old_len] = new_bytes
                print(f"    rename: '{ent_name}' -> '{new_name}' @0x{offset:08X}")
            applied += 1

        elif action == "raw_patch":
            # Direct binary patch at a specific file offset
            file_offset = p.get("file_offset")
            if file_offset is None:
                print(f"    ERROR: raw_patch requires 'file_offset'")
                errors += 1
                continue
            if isinstance(file_offset, str):
                file_offset = int(file_offset, 0)  # supports "0x..." hex

            value = p.get("value")
            value_type = p.get("value_type", "uint16")

            if value_type == "uint16":
                if file_offset + 2 > len(data):
                    print(f"    ERROR: offset 0x{file_offset:08X} out of range")
                    errors += 1
                    continue
                old_val = struct.unpack_from("<H", data, file_offset)[0]
                struct.pack_into("<H", data, file_offset, int(value))
                print(f"    raw_patch @0x{file_offset:08X}: uint16 {old_val} -> {int(value)}"
                      f"  ({ent_name})")
                applied += 1
            elif value_type == "uint32":
                if file_offset + 4 > len(data):
                    print(f"    ERROR: offset 0x{file_offset:08X} out of range")
                    errors += 1
                    continue
                old_val = struct.unpack_from("<I", data, file_offset)[0]
                struct.pack_into("<I", data, file_offset, int(value))
                print(f"    raw_patch @0x{file_offset:08X}: uint32 {old_val} -> {int(value)}"
                      f"  ({ent_name})")
                applied += 1
            elif value_type == "float":
                if file_offset + 4 > len(data):
                    print(f"    ERROR: offset 0x{file_offset:08X} out of range")
                    errors += 1
                    continue
                old_val = struct.unpack_from("<f", data, file_offset)[0]
                struct.pack_into("<f", data, file_offset, float(value))
                print(f"    raw_patch @0x{file_offset:08X}: float {old_val:.4f} -> {float(value):.4f}"
                      f"  ({ent_name})")
                applied += 1
            else:
                print(f"    ERROR: Unknown value_type '{value_type}'")
                errors += 1

        else:
            print(f"    ERROR: Unknown action '{action}' for {ent_name}")
            errors += 1

    return applied, errors



# ---------------------------------------------------------------------------
# Gem randomizer tables and helpers
# ---------------------------------------------------------------------------

LEVEL_XBRS = [
    "a1", "a3", "a5", "a6",
    "w1", "w2", "w3", "w4",
    "f1", "f2", "f3", "f4", "f6",
    "e2", "e5", "e6", "e7",
    "d1", "d2",
    "town", "life", "training_room",
]

GEM_TYPES = ["diamond", "emerald", "sapphire", "obsidian", "ruby"]
NAME_FIELD_SIZE = 20  # fixed-width name field in critterGenerator entries


def _gem_base_type(name: str):
    """Return the base gem type if name is a gem entity, else None."""
    lower = name.lower()
    for gem in GEM_TYPES:
        if lower == gem or lower.startswith(gem + "_"):
            return gem
    return None


def _find_level_gem_entities(data: bytes) -> list[dict]:
    """Find gem entities in level data using the 1.0f+name pattern."""
    entities = find_level_entities(data)  # reuse existing function
    gems = []
    for name, info in entities.items():
        base = _gem_base_type(name)
        if base is not None:
            suffix = name[len(base):]
            gems.append({
                "name": name,
                "name_offset": info["name_offset"],
                "name_len": info["name_len"],
                "coord_offset": info["coord_offset"],
                "x": info["x"], "y": info["y"], "z": info["z"],
                "gem_base": base,
                "gem_suffix": suffix,
            })
    return gems



# ---------------------------------------------------------------------------
# Unified randomizer tables (powers / fragments / keys / barriers / obsidian locks)
# ---------------------------------------------------------------------------

POWER_ELEMENTS = ["water", "air", "earth", "fire", "ammo", "life", "staff"]

# Town temple power-up names (behind obsidian gates, not keys)
TOWN_POWERS = ["power_life", "power_ammo", "power_staff1", "power_staff2"]

# Key realm assignments for within-realm shuffling
KEY_REALMS = {
    "air":   [("a6", "key_blue"), ("a6", "key_green"), ("a6", "key_red")],
    "water": [("w1", "key_life1"), ("life", "key_life1")],
    "fire":  [("f1", "key_fire1"), ("f3", "key_fire1")],
    "earth": [("e2", "key_circuitboard"), ("e2", "key_gear"),
              ("e5", "key_fuse"), ("e7", "key_battery")],
    "death": [("d1", "key_diamondbattery"), ("d2", "key_lens")],
    "town":  [("town", "key_obsidian1"), ("town", "key_obsidian2")],
}

# Barrier fourcc offsets per level — (level_name, offset, barrier_type)
# barrier_type: "firewall" or "iceblock"
BARRIER_OFFSETS = {
    # FireWall element offsets
    "firewall": {
        "a5": 0x01934870, "a6": 0x01395FFC, "w1": 0x025C756C,
        "w2": 0x01D936D4, "f1": 0x0131BFC8, "f4": 0x01398560,
        "e2": 0x04C1DEA8, "e6": 0x01FE2DF0, "e7": 0x00D8D568,
        "town": 0x0383A018,
    },
    # IceBlock element offsets
    "iceblock": {
        "a5": 0x0191F788, "a6": 0x0139C35C, "w1": 0x025C830C,
        "w2": 0x01D8CB6C, "w3": 0x01A277FC, "f6": 0x0120F094,
        "e2": 0x04C1FA2C, "e7": 0x00D8EA30, "d1": 0x01B0F410,
    },
}

# Valid barrier fourccs — each represents an element COMBINATION the player must have active
# Single-element: watr (Water), fire (Earth+Fire), smsh (Earth+Air+Fire), wind (Earth+Air)
# Multi-element:  stem (Fire+Water), acid (Earth+Water), ice (Earth+Air+Water), litn (Air+Fire+Water)
# Using only single-element fourccs keeps barriers accessible early; combo fourccs require more powers
BARRIER_FOURCCS = [b"watr", b"fire", b"smsh", b"wind"]
BARRIER_FOURCCS_HARD = [b"watr", b"fire", b"smsh", b"wind", b"stem", b"acid", b"ice\x00", b"litn"]

# QoL patch constants (PICKUP_ANIM, GEM_POPUP, PLAYER_CHAR) live in
# patches/qol_patches.py; they are applied via apply_qol_patches().

# Obsidian lock threshold tables in town.xbr
# Two identical tables of 10 entries (48 bytes each), threshold float at +0
OBSIDIAN_LOCK_TABLE_A = 0x37DBDC4
OBSIDIAN_LOCK_TABLE_B = 0x37DC0E4
OBSIDIAN_LOCK_ENTRY_SIZE = 48
OBSIDIAN_LOCK_COUNT = 10
OBSIDIAN_LOCK_DEFAULTS = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]

# Town barrier item scale: items placed behind obsidian locks that aren't
# native to town get scaled down so they don't protrude through the force field.
# Scale is applied to the 3x3 rotation/scale matrix diagonal at name-56, name-36, name-20.
# Items that are behind obsidian lock barriers in town
TOWN_BARRIER_ITEMS = {
    "power_life", "power_ammo", "power_staff1", "power_staff2",
    "key_obsidian1", "key_obsidian2",
}
TOWN_BARRIER_SCALE = 0.5
# Offsets of scale floats relative to the entity name offset
SCALE_OFFSETS = [-56, -36, -20]



# ---------------------------------------------------------------------------
# Level connection randomization
# ---------------------------------------------------------------------------

# Ground truth for randomizable levels.  The values here are the
# ``levels/<element>/<short>`` string prefixes the game uses inside
# every level XBR to declare ``levelSwitch`` destinations.  The
# randomizer rewrites them in place, so the new name MUST fit in
# the same number of bytes — the comment on each line records that
# width as a hard constraint.
#
# CANONICAL CROSS-REFERENCE: the full list of levels known to the
# game's streaming loader lives in ``prefetch-lists.txt`` and is
# parsed by :mod:`azurik_mod.assets.prefetch`.  The delta between
# this dict and that manifest is intentional:
#
# - ``training_room`` is omitted here because it has no save-path
#   prefix (it's a bootstrapped demo zone loaded via the
#   ``default`` alias).
# - ``airship_trans`` is omitted because every entry to it is a
#   cutscene — there are no portal paths of the shape
#   ``levels/.../airship_trans`` for the randomizer to rewrite.
#
# Any other divergence is drift and is caught by
# ``tests/test_assets_manifest.py::PrefetchVsHardcodedDelta``.
LEVEL_PATHS = {
    "town": "levels/town",          # 11 chars
    "life": "levels/life",          # 11 chars
    "a1": "levels/air/a1",          # 13 chars
    "a3": "levels/air/a3",          # 13 chars
    "a5": "levels/air/a5",          # 13 chars
    "a6": "levels/air/a6",          # 13 chars
    "f1": "levels/fire/f1",         # 14 chars
    "f2": "levels/fire/f2",         # 14 chars
    "f3": "levels/fire/f3",         # 14 chars
    "f4": "levels/fire/f4",         # 14 chars
    "f6": "levels/fire/f6",         # 14 chars
    "w1": "levels/water/w1",        # 15 chars
    "w2": "levels/water/w2",        # 15 chars
    "w3": "levels/water/w3",        # 15 chars
    "w4": "levels/water/w4",        # 15 chars
    "e2": "levels/earth/e2",        # 15 chars
    "e5": "levels/earth/e5",        # 15 chars
    "e6": "levels/earth/e6",        # 15 chars
    "e7": "levels/earth/e7",        # 15 chars
    "d1": "levels/death/d1",        # 15 chars
    "d2": "levels/death/d2",        # 15 chars
    "airship": "levels/air/airship", # 18 chars
}

# Valid destination levels for randomization (exclude cut levels)
VALID_DEST_LEVELS = set(LEVEL_PATHS.keys()) - {"airship"}  # airship is one-way, special

# Transitions to exclude from randomization.
#
# Derived from the canonical
# :mod:`azurik_mod.randomizer.loading_zones` catalog so that any
# new cut level added to :data:`azurik_mod.assets.KNOWN_CUT_LEVELS`
# or any newly-catalogued non-randomizable zone (airship one-way,
# bink-return self-loop, etc.) automatically flows into the
# randomizer's exclusion set.  The snapshot below pins the
# currently-known set for readability + diffing.
#
# Current snapshot (vanilla USA ISO, April 2026):
#
#    - ("f1",      "f7")       cut level (KNOWN_CUT_LEVELS)
#    - ("w1",      "airship")  one-way cutscene into airship
#    - ("airship", "a3")       airship arrival cutscene (always W1_A3)
#    - ("e2",      "e2")       bink-return self-loop (catalisks.bik)
#
# Any drift against the live game is caught by
# ``tests/test_loading_zones.py``.
from azurik_mod.assets import KNOWN_CUT_LEVELS as _KNOWN_CUT_LEVELS
from azurik_mod.randomizer.loading_zones import (
    derive_exclude_transitions as _derive_exclude_transitions,
)

EXCLUDE_TRANSITIONS: frozenset[tuple[str, str]] = _derive_exclude_transitions(
    cut_levels=_KNOWN_CUT_LEVELS,
)


def _find_level_transitions(data: bytes, level_name: str) -> list[dict]:
    """Scan a level's XBR data for all levelSwitch transition entries.

    Returns list of dicts with:
      offset: file offset of destination path string
      dest_path: full path string (e.g. "levels/water/w1")
      dest_level: short name (e.g. "w1")
      path_len: length of path string (not including null)
      spot: start spot name in destination level
      spot_offset: file offset of spot string (0 if no spot)
      movie: movie/bink path before the transition (empty if none)
    """
    valid_levels = set(LEVEL_PATHS.keys()) | {"f7"}  # include f7 for detection
    transitions = []
    pos = 0
    search = b"levels/"

    while True:
        pos = data.find(search, pos)
        if pos == -1:
            break

        end = data.find(b"\x00", pos)
        dest_path = data[pos:end].decode("ascii", errors="replace")
        dest_level = dest_path.split("/")[-1]

        # Skip non-level destinations (fx_, etc.)
        if dest_level not in valid_levels:
            pos += 1
            continue

        # Read start spot: next non-null string after path
        spot_start = end + 1
        while spot_start < len(data) and data[spot_start] == 0:
            spot_start += 1
        spot_end = data.find(b"\x00", spot_start)
        spot = ""
        if spot_end > spot_start and spot_end - spot_start < 40:
            candidate = data[spot_start:spot_end]
            if all(32 <= b < 127 for b in candidate):
                spot = candidate.decode("ascii")

        # Check for movie/bink path immediately before
        movie = ""
        pre = pos - 1
        while pre > 0 and data[pre] == 0:
            pre -= 1
        if pre > 0:
            ps = pre
            while ps > 0 and data[ps - 1] != 0:
                ps -= 1
            pre_str = data[ps:pre + 1].decode("ascii", errors="replace")
            if pre_str.startswith("bink:") or pre_str.startswith("movies/"):
                movie = pre_str

        # Skip self-references at file end (index entries, not transitions)
        if dest_level == level_name and not spot and pos > len(data) - 2000:
            pos += 1
            continue

        transitions.append({
            "offset": pos,
            "dest_path": dest_path,
            "dest_level": dest_level,
            "path_len": len(dest_path),
            "spot": spot,
            "spot_offset": spot_start if spot else 0,
            "movie": movie,
            "level": level_name,
        })
        pos += 1

    return transitions


def _power_element(name: str) -> str | None:
    """Extract the element type from a power-up entity name.

    power_water_a3 -> water, power_fire -> fire, power_ammo -> ammo
    """
    if not name.startswith("power_"):
        return None
    rest = name[6:]  # strip "power_"
    for elem in POWER_ELEMENTS:
        if rest == elem or rest.startswith(elem + "_") or rest.startswith(elem) and rest[len(elem):].isdigit():
            return elem
    return None


def _frag_parts(name: str) -> tuple[str, str] | None:
    """Extract (element, number) from a fragment entity name.

    frag_air_1 -> ("air", "1"), frag_earth_3 -> ("earth", "3")
    """
    if not name.startswith("frag_"):
        return None
    rest = name[5:]  # strip "frag_"
    for elem in ["water", "air", "earth", "fire", "life"]:
        if rest.startswith(elem + "_"):
            num = rest[len(elem) + 1:]
            if num.isdigit():
                return (elem, num)
    return None


def _find_cross_level_entities(extract_dir: Path, levels: list[str]):
    """Scan all level files and collect fragment + power-up entities.

    Returns:
        fragments: list of {level, name, name_offset, name_len, element, number, file_path}
        powerups:  list of {level, name, name_offset, name_len, element, file_path}
        level_data: dict of {level_name: bytearray} for modified levels
    """
    fragments = []
    powerups = []
    level_data = {}  # level_name -> bytearray (loaded on demand)

    for level_name in sorted(levels):
        level_file = extract_dir / GAMEDATA_REL / f"{level_name}.xbr"
        if not level_file.exists():
            continue

        data = bytearray(level_file.read_bytes())
        if data[:4] != b"xobx":
            continue

        entities = find_level_entities(data)
        has_targets = False

        for name, info in entities.items():
            parts = _frag_parts(name)
            if parts is not None:
                fragments.append({
                    "level": level_name,
                    "name": name,
                    "name_offset": info["name_offset"],
                    "name_len": info["name_len"],
                    "element": parts[0],
                    "number": parts[1],
                    "file_path": level_file,
                })
                has_targets = True

            elem = _power_element(name)
            if elem is not None:
                powerups.append({
                    "level": level_name,
                    "name": name,
                    "name_offset": info["name_offset"],
                    "name_len": info["name_len"],
                    "element": elem,
                    "file_path": level_file,
                })
                has_targets = True

        if has_targets:
            level_data[level_name] = data

    return fragments, powerups, level_data



# ---------------------------------------------------------------------------
# Cross-level entity helpers (shared by cmd_randomize_full)
# ---------------------------------------------------------------------------

# Entities that may lack the 1.0f marker the default scanner keys off
# (non-zero w in coords, sparse level files, etc.) — `_find_all_entities_in_level`
# falls back to a direct string search for these so powers / fragments / keys
# are never missed.  Moved here from commands.py so the helper that
# consumes it has a local reference.
DIRECT_SEARCH_NAMES = [
    # Powers (some lack 1.0f marker or have non-zero w in coords)
    b"power_water", b"power_water_a3",
    b"power_air", b"power_earth", b"power_fire",
    b"power_staff1", b"power_staff2", b"power_life", b"power_ammo",
    # Fragments
    b"frag_air_1", b"frag_air_2", b"frag_air_3",
    b"frag_water_1", b"frag_water_2", b"frag_water_3",
    b"frag_fire_1", b"frag_fire_2", b"frag_fire_3",
    b"frag_earth_1", b"frag_earth_2", b"frag_earth_3",
    b"frag_life_1", b"frag_life_2", b"frag_life_3",
    b"frag_water_4", b"frag_fire_4", b"frag_earth_4", b"frag_life_4",
    # Keys
    b"key_air1", b"key_air2", b"key_air3",
    b"key_fire1", b"key_fire2", b"key_fire3",
    b"key_water1", b"key_water2",
    b"key_battery", b"key_circuitboard", b"key_diamondbattery",
    b"key_fuse", b"key_gear", b"key_lens", b"key_life1",
    b"key_obsidian1", b"key_obsidian2",
    b"key_blue", b"key_green", b"key_red",
]


def _find_all_entities_in_level(data: bytes, level_name: str):
    """Find all relevant entities in a level, categorized into pools.

    Returns dict with keys: gems, obsidians, fragments, powers, town_powers, keys
    Each value is a list of {name, name_offset, name_len, level, field_size, ...}
    """
    entities = find_level_entities(data)

    # Direct search for entities that may lack the 1.0f marker
    for needle in DIRECT_SEARCH_NAMES:
        name = needle.decode("ascii")
        if name in entities:
            continue
        # Search for the name as a null-terminated string anywhere in the file
        pos = 0
        while True:
            pos = data.find(needle, pos)
            if pos == -1:
                break
            end = pos + len(needle)
            # Must be null-terminated and not mid-string
            if end < len(data) and data[end] == 0:
                if name not in entities:
                    for co in [-96, -116, -144, -140]:
                        cp = pos + co
                        if cp < 0 or cp + 16 > len(data):
                            continue
                        x, y, z, w = struct.unpack_from("<4f", data, cp)
                        if (w == 0.0 and x == x and y == y and z == z
                                and all(abs(v) < 50000 for v in [x, y, z])):
                            entities[name] = {
                                "name_offset": pos,
                                "name_len": len(needle),
                                "coord_offset": cp,
                                "x": x, "y": y, "z": z,
                            }
                            break
            pos += 1

    result = {"gems": [], "obsidians": [], "fragments": [], "powers": [],
              "town_powers": [], "keys": []}

    for name, info in entities.items():
        entry = {
            "name": name,
            "name_offset": info["name_offset"],
            "name_len": info["name_len"],
            "level": level_name,
            "x": info["x"], "y": info["y"], "z": info["z"],
        }
        # Measure actual field size (count null bytes after name)
        end = info["name_offset"] + info["name_len"]
        field_size = info["name_len"]
        while end + (field_size - info["name_len"]) < len(data) and data[end + (field_size - info["name_len"])] == 0:
            field_size += 1
            if field_size >= 32:
                break
        entry["field_size"] = min(field_size, 32)

        # Categorize
        if name.startswith("key_"):
            result["keys"].append(entry)
        elif name.startswith("frag_") and _frag_parts(name) is not None:
            entry["element"], entry["number"] = _frag_parts(name)
            result["fragments"].append(entry)
        elif name.startswith("power_"):
            elem = _power_element(name)
            if elem is not None:
                entry["element"] = elem
                if name in TOWN_POWERS and level_name == "town":
                    result["town_powers"].append(entry)
                else:
                    result["powers"].append(entry)
        elif _gem_base_type(name) is not None:
            base = _gem_base_type(name)
            entry["gem_base"] = base
            entry["gem_suffix"] = name[len(base):]
            if base == "obsidian":
                result["obsidians"].append(entry)
            else:
                result["gems"].append(entry)

    return result


def _write_name(data: bytearray, offset: int, new_name: str, field_size: int):
    """Write a new entity name at offset, null-padded to field_size."""
    new_bytes = new_name.encode("ascii")
    if len(new_bytes) >= field_size:
        new_bytes = new_bytes[:field_size - 1]
    padded = new_bytes + b"\x00" * (field_size - len(new_bytes))
    data[offset:offset + field_size] = padded


def _rename_all_refs(data: bytearray, old_name: str, new_name: str, primary_offset: int):
    """Rename all null-terminated occurrences of old_name in data.

    Some entities have both an inline name and an NDBG debug name.
    Both must be renamed for the engine to use the new type.
    The primary_offset is renamed via _write_name; additional occurrences
    are renamed in-place (must be same length or shorter, null-padded).
    """
    old_bytes = old_name.encode("ascii")
    pos = 0
    while True:
        pos = data.find(old_bytes, pos)
        if pos == -1:
            break
        end = pos + len(old_bytes)
        if end < len(data) and data[end] == 0 and pos != primary_offset:
            # Found an additional reference — rename it in-place
            new_bytes = new_name.encode("ascii")
            if len(new_bytes) <= len(old_bytes):
                data[pos:pos + len(old_bytes)] = new_bytes + b"\x00" * (len(old_bytes) - len(new_bytes))
            # If new name is longer, we can't safely expand — skip
        pos += 1

