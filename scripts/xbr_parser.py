#!/usr/bin/env python3
"""
xbr_parser.py — Full parser for Azurik .xbr binary resource files.

Handles both config.xbr (18 gameplay-tuning sections) and level XBR files
(a1.xbr, town.xbr, etc.). Config sections come in two binary formats:

  Keyed-table (15 sections): column-major cell grids with typed values.
  Variant-record (3 sections): fixed-stride records with doubles + type flags.

All numeric values are 64-bit IEEE 754 doubles (NOT 32-bit floats).

Usage:
    python xbr_parser.py config.xbr                         # overview
    python xbr_parser.py config.xbr --sections               # list sections
    python xbr_parser.py config.xbr -s critters_walking       # list entities
    python xbr_parser.py config.xbr -s critters_walking -e air_elemental
    python xbr_parser.py config.xbr --find garret4            # search all sections
    python xbr_parser.py config.xbr --dump-json out.json      # full export
    python xbr_parser.py config.xbr --patch -s damage -e norm_1 -p damage -v 30.0 -o patched.xbr
    python xbr_parser.py town.xbr --toc                       # level XBR TOC
    python xbr_parser.py town.xbr --strings node              # strings in tag
"""

import argparse
import json
import shutil
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# TOC parsing (shared by config and level XBR files)
# ---------------------------------------------------------------------------

@dataclass
class TOCEntry:
    index: int
    size: int
    tag: str
    flags: int
    file_offset: int


def read_string(data: bytes, offset: int, max_len: int = 256) -> str:
    end = offset
    while end < len(data) and end - offset < max_len and data[end] != 0:
        end += 1
    return data[offset:end].decode("ascii", errors="replace")


def parse_toc(data: bytes) -> list[TOCEntry]:
    entries = []
    off = 0x40
    while off + 16 <= len(data):
        size = struct.unpack_from("<I", data, off)[0]
        tag_raw = data[off + 4:off + 8]
        flags = struct.unpack_from("<I", data, off + 8)[0]
        file_offset = struct.unpack_from("<I", data, off + 12)[0]
        if size == 0 and flags == 0 and file_offset == 0:
            break
        try:
            tag = tag_raw.decode("ascii")
        except (UnicodeDecodeError, ValueError):
            tag = tag_raw.hex()
        entries.append(TOCEntry(len(entries), size, tag, flags, file_offset))
        off += 16
    return entries


# ---------------------------------------------------------------------------
# Keyed-table section (15 of 18 config sections)
# ---------------------------------------------------------------------------

KEYED_SECTION_OFFSETS = {
    "armor_hit_fx":          0x002000,
    "armor_properties":      0x004000,
    "attacks_anims":         0x006000,
    "attacks_transitions":   0x008000,
    "critters_critter_data": 0x01A000,
    "critters_damage":       0x035000,
    "critters_damage_fx":    0x044000,
    "critters_engine":       0x05A000,
    "critters_flocking":     0x05D000,
    "critters_item_data":    0x060000,
    "critters_maya_stuff":   0x065000,
    "critters_mutate":       0x066000,
    "critters_sounds":       0x077000,
    "critters_special_anims":0x07A000,
    "magic":                 0x087000,
}


class KeyedSection:
    """Parser for a keyed-table config section."""

    def __init__(self, data: bytes, section_offset: int, name: str = ""):
        self.name = name
        self.format = "keyed"
        self.section_offset = section_offset
        self.table_base = section_offset + 0x1000

        hdr = struct.unpack_from("<5I", data, self.table_base)
        self.num_rows = hdr[0]
        self.row_hdr_offset = hdr[1]
        self.num_cols = hdr[2]
        self.total_cells = hdr[3]
        self.cell_data_off = hdr[4]

        if self.num_rows * self.num_cols != self.total_cells:
            raise ValueError(f"{name}: cell mismatch {self.num_rows}*{self.num_cols} != {self.total_cells}")

        self.row_names = [self._read_row_name(data, r) for r in range(self.num_rows)]

        self._cells: dict[tuple[int, int], tuple[str, object, int]] = {}
        for c in range(self.num_cols):
            for r in range(self.num_rows):
                cell = self._parse_cell(data, c, r)
                if cell[0] != "empty":
                    self._cells[(c, r)] = cell

        self.col_names = []
        for c in range(self.num_cols):
            cell = self._cells.get((c, 0))
            self.col_names.append(cell[1] if cell and cell[0] == "string" else f"col_{c}")

        self.entity_index = {n: i for i, n in enumerate(self.col_names)}

    def _read_row_name(self, data: bytes, row: int) -> str:
        entry_addr = self.table_base + self.row_hdr_offset + 4 + row * 8
        rel = struct.unpack_from("<I", data, entry_addr + 4)[0]
        return read_string(data, entry_addr + 4 + rel)

    def _cell_addr(self, col: int, row: int) -> int:
        return self.table_base + self.cell_data_off + 0x10 + (self.num_rows * col + row) * 16

    def _parse_cell(self, data: bytes, col: int, row: int):
        addr = self._cell_addr(col, row)
        ctype = struct.unpack_from("<I", data, addr)[0]
        if ctype == 0:
            return ("empty", None, addr)
        if ctype == 1:
            return ("double", struct.unpack_from("<d", data, addr + 8)[0], addr)
        if ctype == 2:
            str_off = struct.unpack_from("<I", data, addr + 12)[0]
            return ("string", read_string(data, addr + 12 + str_off), addr)
        return ("unknown", ctype, addr)

    def read_cell(self, col: int, row: int):
        return self._cells.get((col, row), ("empty", None, self._cell_addr(col, row)))

    def get_value(self, entity: str, prop: str):
        col = self.entity_index.get(entity)
        if col is None:
            return None
        try:
            row = self.row_names.index(prop)
        except ValueError:
            return None
        return self.read_cell(col, row)

    def get_entity(self, entity: str) -> dict:
        col = self.entity_index.get(entity)
        if col is None:
            return {}
        return {
            self.row_names[r]: self.read_cell(col, r)
            for r in range(self.num_rows)
            if self.read_cell(col, r)[0] != "empty"
        }

    def iter_entities(self):
        for c, name in enumerate(self.col_names):
            yield name, c


# ---------------------------------------------------------------------------
# Variant-record section (critters_walking, damage, settings_foo)
# ---------------------------------------------------------------------------

VARIANT_SCHEMAS = {
    "critters_walking": {
        "section_offset": 0x083000,
        "record_base":    0x084090,
        "entity_count":   107,
        "props_per_entity": 18,
        "record_size":    16,
        "properties": [
            "stalk_time_min", "stalk_time_max", "stalk_distance_cw",
            "stalk_distance_ccw", "provoke_distance", "ambush_time_min",
            "ambush_time_max", "ambush_if_hit_chance", "need_n_allies",
            "max_distance", "flee_after_attack_chance", "flee_if_health_less_than",
            "safe_distance", "attack_anim_rate", "max_turn_rate",
            "turn_while_attacking", "left_footstep_time", "right_footstep_time",
        ],
    },
    "damage": {
        "section_offset": 0x086000,
        "record_base":    0x086000,
        "entity_count":   11,
        "props_per_entity": 8,
        "record_size":    16,
        "properties": [
            "damage_multiplier", "damage", "delay", "cost",
            "freeze", "color_r", "color_g", "color_b",
        ],
    },
    "settings_foo": {
        "section_offset": 0x088300,
        "record_base":    0x088300,
        "entity_count":   1,
        "props_per_entity": 6,
        "record_size":    48,
        "properties": [
            "initial_fuel", "initial_fuel_cap", "fuel_cap_inc",
            "num_fuel_inc", "fuel_inc_gems", "initial_hp",
        ],
    },
}


class VariantSection:
    """Parser for a variant-record config section."""

    def __init__(self, data: bytes, schema: dict, name: str = ""):
        self.name = name
        self.format = "variant"
        self.section_offset = schema["section_offset"]
        self.record_base = schema["record_base"]
        self.entity_count = schema["entity_count"]
        self.props_per_entity = schema["props_per_entity"]
        self.record_size = schema["record_size"]
        self.row_names = list(schema["properties"])
        self.num_rows = len(self.row_names)
        self.num_cols = 0

        self._entities: dict[str, dict[str, tuple[str, object, int]]] = {}
        self._entity_names: list[str] = []

        self._discover_entities(data)

    def _discover_entities(self, data: bytes):
        """Discover entity names from the keyed-table in the same TOC slot."""
        base = self.record_base
        stride = self.props_per_entity * self.record_size

        for i in range(self.entity_count):
            block_start = base + i * stride
            props = {}
            for p in range(self.props_per_entity):
                rec_off = block_start + p * self.record_size
                value_off = rec_off + 4
                if self.record_size == 48:
                    value_off = rec_off + 16 + 4
                if value_off + 8 > len(data):
                    continue
                val = struct.unpack_from("<d", data, value_off)[0]
                type_flag_off = value_off + 8
                if type_flag_off + 4 > len(data):
                    tf = 0
                else:
                    tf = struct.unpack_from("<I", data, type_flag_off)[0]
                type_str = {0: "unset", 1: "float", 2: "int"}.get(tf)
                if type_str is None:
                    continue
                props[self.row_names[p]] = (type_str, val if tf != 2 else int(val), value_off)

            name = f"entity_{i}"
            self._entity_names.append(name)
            self._entities[name] = props

        self.num_cols = len(self._entity_names)
        self.col_names = list(self._entity_names)
        self.entity_index = {n: i for i, n in enumerate(self.col_names)}

    def set_entity_names(self, names: list[str]):
        """Override discovered entity names with actual names from the keyed table."""
        old_entities = self._entities
        self._entities = {}
        self._entity_names = []
        for i, new_name in enumerate(names):
            old_name = f"entity_{i}"
            if old_name in old_entities:
                self._entity_names.append(new_name)
                self._entities[new_name] = old_entities[old_name]
        self.num_cols = len(self._entity_names)
        self.col_names = list(self._entity_names)
        self.entity_index = {n: i for i, n in enumerate(self.col_names)}

    def get_value(self, entity: str, prop: str):
        ent = self._entities.get(entity)
        if ent is None:
            return None
        return ent.get(prop)

    def get_entity(self, entity: str) -> dict:
        return dict(self._entities.get(entity, {}))

    def read_cell(self, col: int, row: int):
        if col < len(self._entity_names) and row < len(self.row_names):
            ent = self._entities.get(self._entity_names[col], {})
            return ent.get(self.row_names[row], ("empty", None, 0))
        return ("empty", None, 0)

    def iter_entities(self):
        for i, name in enumerate(self._entity_names):
            yield name, i


# ---------------------------------------------------------------------------
# XBRFile — top-level parser
# ---------------------------------------------------------------------------

class XBRFile:
    """Parser for any .xbr file (config or level)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = self.path.read_bytes()

        if self.data[:4] != b"xobx":
            raise ValueError(f"Bad magic: {self.data[:4]!r} (expected b'xobx')")

        self.header = {
            "magic": "xobx",
            "size": len(self.data),
            "toc_count": struct.unpack_from("<I", self.data, 0x0C)[0],
        }
        self.toc = parse_toc(self.data)
        self.sections: dict[str, KeyedSection | VariantSection] = {}
        self._is_config = self._detect_config()

        if self._is_config:
            self._load_config_sections()

    def _detect_config(self) -> bool:
        """Check if this is config.xbr by looking for the 'tabl' tag in TOC."""
        tabl_count = sum(1 for e in self.toc if e.tag == "tabl")
        return tabl_count >= 10

    def _load_config_sections(self):
        for name, offset in KEYED_SECTION_OFFSETS.items():
            if offset + 0x1014 < len(self.data):
                try:
                    self.sections[name] = KeyedSection(self.data, offset, name)
                except Exception as e:
                    print(f"  Warning: {name}: {e}", file=sys.stderr)

        keyed_walking = self.sections.get("critters_walking_dmg") if "critters_walking_dmg" not in KEYED_SECTION_OFFSETS else None

        for name, schema in VARIANT_SCHEMAS.items():
            if schema["record_base"] + 16 < len(self.data):
                try:
                    vs = VariantSection(self.data, schema, name)
                    if name == "critters_walking":
                        kt = self.sections.get("attacks_transitions")
                        if kt:
                            vs.set_entity_names(kt.col_names[:vs.entity_count])
                    elif name == "damage":
                        existing_names = _discover_variant_names(
                            self.data, schema, fallback_prefix="dmg")
                        if existing_names:
                            vs.set_entity_names(existing_names)
                    self.sections[name] = vs
                except Exception as e:
                    print(f"  Warning: {name}: {e}", file=sys.stderr)

    def get_section(self, name: str):
        return self.sections.get(name)

    def list_sections(self):
        return sorted(self.sections.keys())

    def find_entity(self, entity: str) -> dict[str, dict]:
        results = {}
        for sec_name, sec in self.sections.items():
            props = sec.get_entity(entity)
            if props:
                results[sec_name] = props
        return results

    def write_patched(self, output: str | Path, section: str, entity: str,
                      prop: str, value: float):
        sec = self.sections.get(section)
        if sec is None:
            raise ValueError(f"Section '{section}' not found")
        result = sec.get_value(entity, prop)
        if result is None:
            raise ValueError(f"'{entity}/{prop}' not found in {section}")
        typ, old_val, offset = result
        if typ not in ("double", "float", "int", "unset"):
            raise ValueError(f"Cannot patch type '{typ}' at 0x{offset:06X}")

        out_path = Path(output)
        shutil.copy2(self.path, out_path)
        with open(out_path, "r+b") as f:
            f.seek(offset)
            f.write(struct.pack("<d", float(value)))
        print(f"Patched {section}/{entity}/{prop}: {old_val} -> {value} @ 0x{offset:06X}")
        print(f"Written to {out_path}")


def _discover_variant_names(data: bytes, schema: dict, fallback_prefix: str = "entity") -> list[str]:
    """Try to find entity names from the section's string table."""
    sec_off = schema["section_offset"]
    string_region = data[sec_off:sec_off + 0x1000]
    names = []
    i = 0
    while i < len(string_region):
        if string_region[i] == 0:
            i += 1
            continue
        end = i
        while end < len(string_region) and string_region[end] != 0:
            if string_region[end] < 32 or string_region[end] >= 127:
                break
            end += 1
        if end > i and end < len(string_region) and string_region[end] == 0:
            s = string_region[i:end].decode("ascii", errors="replace")
            if len(s) >= 2 and not s.startswith("config/"):
                names.append(s)
        i = end + 1
    return names[:schema["entity_count"]] if names else [
        f"{fallback_prefix}_{i}" for i in range(schema["entity_count"])
    ]


# ---------------------------------------------------------------------------
# Level XBR helpers
# ---------------------------------------------------------------------------

def find_strings_in_region(data: bytes, start: int, length: int,
                           min_len: int = 4) -> list[tuple[int, str]]:
    end = min(start + length, len(data))
    results = []
    current = bytearray()
    s_start = start
    for i in range(start, end):
        b = data[i]
        if 32 <= b < 127:
            if not current:
                s_start = i
            current.append(b)
        else:
            if len(current) >= min_len:
                results.append((s_start, current.decode("ascii")))
            current = bytearray()
    if len(current) >= min_len:
        results.append((s_start, current.decode("ascii")))
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_val(typ: str, val) -> str:
    if val is None:
        return "-"
    if isinstance(val, float):
        return f"{val:.4g}" if val != int(val) or abs(val) > 1e6 else f"{val:.1f}"
    return str(val)


def main():
    parser = argparse.ArgumentParser(
        description="Azurik XBR file parser — config.xbr and level XBR files",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("xbr", help="Path to .xbr file")
    parser.add_argument("--sections", action="store_true",
                        help="List all config sections with entity/property counts")
    parser.add_argument("-s", "--section", help="Section name to examine")
    parser.add_argument("-e", "--entity", help="Entity name to dump")
    parser.add_argument("-p", "--property", help="Property to look up (with -e)")
    parser.add_argument("--find", metavar="NAME",
                        help="Search for an entity across all sections")
    parser.add_argument("--dump-json", metavar="FILE",
                        help="Export all sections to JSON")
    parser.add_argument("--toc", action="store_true",
                        help="Show raw TOC entries (works for any XBR)")
    parser.add_argument("--strings", metavar="TAG",
                        help="Extract ASCII strings from TOC entries with this tag")
    parser.add_argument("--patch", action="store_true",
                        help="Patch a value (requires -s, -e, -p, -v, -o)")
    parser.add_argument("-v", "--value", type=float, help="New value for --patch")
    parser.add_argument("-o", "--output", help="Output file for --patch")
    parser.add_argument("--json", action="store_true", help="JSON output mode")
    args = parser.parse_args()

    path = Path(args.xbr)
    if not path.exists():
        print(f"ERROR: File not found: {path}")
        sys.exit(1)

    # -- Raw TOC mode (any XBR) --
    if args.toc:
        data = path.read_bytes()
        if data[:4] != b"xobx":
            print(f"ERROR: Bad magic: {data[:4]!r}")
            sys.exit(1)
        toc = parse_toc(data)
        print(f"File: {path.name} ({len(data):,} bytes)")
        print(f"TOC entries: {len(toc)}\n")
        tag_counts: dict[str, int] = {}
        for e in toc:
            tag_counts[e.tag] = tag_counts.get(e.tag, 0) + 1
        print("Tag distribution:")
        for t in sorted(tag_counts):
            print(f"  {t}: {tag_counts[t]} entries")
        if len(toc) <= 100:
            print(f"\nAll entries:")
            for e in toc:
                print(f"  [{e.index:4d}] {e.tag}  size=0x{e.size:08X}"
                      f"  flags=0x{e.flags:02X}  offset=0x{e.file_offset:08X}")
        return

    # -- String extraction (any XBR) --
    if args.strings:
        data = path.read_bytes()
        if data[:4] != b"xobx":
            print(f"ERROR: Bad magic")
            sys.exit(1)
        toc = parse_toc(data)
        targets = [e for e in toc if e.tag == args.strings]
        print(f"Strings in '{args.strings}' entries ({len(targets)}):")
        for e in targets:
            strings = find_strings_in_region(data, e.file_offset, e.size)
            if strings:
                print(f"\n  [{e.index}] offset=0x{e.file_offset:08X} size={e.size:,}")
                for addr, s in strings:
                    print(f"    0x{addr:08X}: {s}")
        return

    # -- Config parsing mode --
    xbr = XBRFile(path)

    if not xbr._is_config:
        print(f"File: {path.name} ({len(xbr.data):,} bytes)")
        print(f"TOC entries: {len(xbr.toc)}")
        print("Not a config.xbr file. Use --toc or --strings for level XBR files.")
        return

    # --sections
    if args.sections:
        print(f"{'Section':<30s} {'Format':<8s} {'Entities':>8s} {'Properties':>10s}")
        print("-" * 60)
        for name in sorted(xbr.sections):
            sec = xbr.sections[name]
            print(f"{name:<30s} {sec.format:<8s} {sec.num_cols:>8d} {sec.num_rows:>10d}")
        return

    # --find
    if args.find:
        results = xbr.find_entity(args.find)
        if not results:
            print(f"Entity '{args.find}' not found in any section.")
            return
        print(f"Entity '{args.find}' found in {len(results)} section(s):\n")
        for sec_name, props in results.items():
            doubles = sum(1 for t, _, _ in props.values() if t in ("double", "float", "int"))
            strings = sum(1 for t, _, _ in props.values() if t == "string")
            print(f"  {sec_name}: {doubles} numeric, {strings} string values")
        if args.json:
            export = {}
            for sec_name, props in results.items():
                export[sec_name] = {
                    k: {"type": t, "value": v, "offset": f"0x{a:06X}"}
                    for k, (t, v, a) in props.items()
                }
            print(json.dumps(export, indent=2))
        return

    # --dump-json
    if args.dump_json:
        export = {}
        for sec_name in sorted(xbr.sections):
            sec = xbr.sections[sec_name]
            sec_data = {}
            for ent_name, _ in sec.iter_entities():
                props = sec.get_entity(ent_name)
                if props:
                    sec_data[ent_name] = {
                        k: {"type": t, "value": v, "offset": f"0x{a:06X}"}
                        for k, (t, v, a) in props.items()
                    }
            export[sec_name] = sec_data
        out_path = Path(args.dump_json)
        with open(out_path, "w") as f:
            json.dump(export, f, indent=2)
        total = sum(len(v) for v in export.values())
        print(f"Exported {len(export)} sections, {total} entities -> {out_path}")
        return

    # --patch
    if args.patch:
        if not all([args.section, args.entity, args.property, args.value is not None, args.output]):
            print("ERROR: --patch requires -s, -e, -p, -v, and -o")
            sys.exit(1)
        xbr.write_patched(args.output, args.section, args.entity,
                          args.property, args.value)
        return

    # -s (section)
    if args.section:
        sec = xbr.get_section(args.section)
        if sec is None:
            print(f"Section '{args.section}' not found.")
            print(f"Available: {', '.join(xbr.list_sections())}")
            return

        if args.entity:
            props = sec.get_entity(args.entity)
            if not props:
                print(f"Entity '{args.entity}' not found in {args.section}")
                return

            if args.property:
                result = sec.get_value(args.entity, args.property)
                if result is None:
                    print(f"Property '{args.property}' not found")
                else:
                    typ, val, addr = result
                    if args.json:
                        print(json.dumps({"type": typ, "value": val,
                                          "offset": f"0x{addr:06X}"}))
                    else:
                        print(f"{val}  (type={typ}, offset=0x{addr:06X})")
            else:
                print(f"\n  [{args.section}] {args.entity}")
                for prop_name, (typ, val, addr) in props.items():
                    print(f"    {prop_name:<30s} = {_format_val(typ, val):<15s}"
                          f" [{typ}]  @0x{addr:06X}")
        else:
            print(f"\n{args.section} ({sec.format}, {sec.num_cols} entities,"
                  f" {sec.num_rows} properties):\n")
            for ent_name, _ in sec.iter_entities():
                n_set = len(sec.get_entity(ent_name))
                print(f"  {ent_name:<35s} {n_set} values")
        return

    # Default: overview
    print(f"File: {path.name} ({len(xbr.data):,} bytes)")
    print(f"TOC entries: {len(xbr.toc)}")
    print(f"Config sections: {len(xbr.sections)}\n")
    print(f"{'Section':<30s} {'Format':<8s} {'Entities':>8s} {'Properties':>10s}")
    print("-" * 60)
    for name in sorted(xbr.sections):
        sec = xbr.sections[name]
        print(f"{name:<30s} {sec.format:<8s} {sec.num_cols:>8d} {sec.num_rows:>10d}")


if __name__ == "__main__":
    main()
