#!/usr/bin/env python3
"""
keyed_table_parser.py - Parser for Azurik config.xbr "keyed section" binary format.

The keyed table format is used by 15 of the 18 config.xbr sections (all except
critters_walking, damage, and settings_foo which use the variant record format).

Binary Layout:
  Section start:    String pool (null-terminated ASCII strings, entity/value names)
  +0x1000:          Table header (20 bytes)
  +0x1000+0x14:     Row headers (8 bytes each, num_rows entries)
  +0x1000+cell_off: Cell data (16 bytes each, num_rows * num_cols entries)
  After cells:      String data referenced by type-2 cells (self-relative offsets)

Table Header (20 bytes = 5 x uint32):
  [0] num_rows       - Number of property rows
  [1] row_hdr_offset - Offset from table base to row header area (always 0x10)
  [2] num_cols       - Number of entity columns
  [3] total_cells    - num_rows * num_cols (redundant validation field)
  [4] cell_data_off  - Offset from table base to first cell (minus 0x10 adjustment)

Row Header Entry (8 bytes each):
  [0:4] uint32  unknown/padding
  [4:8] uint32  self-relative offset to property name string
        String address = entry_file_offset + 4 + stored_value

Cell Record (16 bytes each):
  [0:4]   uint32  type: 0=empty, 1=double, 2=string
  [4:8]   uint32  padding/unused
  [8:16]  data:
    type 0: zeros (no value; engine uses default)
    type 1: float64 (IEEE 754 double-precision) - the actual gameplay value
    type 2: [8:12] uint32 string_length, [12:16] uint32 self-relative string offset
            String address = cell_file_offset + 12 + stored_offset

Cell Indexing (column-major):
  cell_file_offset = table_base + cell_data_off + 0x10 + (num_rows * col + row) * 16

Column identification: Row 0 is always "name", and cell[col][0] contains the entity name.
Row identification: Row headers contain property names.
"""

import io
import struct
import sys
import json


def set_cell_double(data: bytearray, cell_file_offset: int, new_value: float) -> None:
    """Overwrite the 8-byte double value inside a type-1 cell in `data`.

    The cell header (type/padding) starts at `cell_file_offset` and the
    double payload lives at `cell_file_offset + 8`.  Raises ValueError
    if the cell's type field is not 1 (double).

    Intended for in-memory patches of config.xbr.  Pair with the cell
    file offset returned by `KeyedTable.get_value`.
    """
    if cell_file_offset + 16 > len(data):
        raise ValueError(
            f"Cell offset 0x{cell_file_offset:X} past end of "
            f"{len(data)}-byte buffer")
    ctype = struct.unpack_from("<I", data, cell_file_offset)[0]
    if ctype != 1:
        raise ValueError(
            f"Cell at 0x{cell_file_offset:X} has type {ctype}, not 1 (double)")
    struct.pack_into("<d", data, cell_file_offset + 8, float(new_value))


def load_table_from_bytes(data: bytes, section_offset: int,
                          section_name: str = "") -> "KeyedTable":
    """Parse a keyed table section from an in-memory buffer.

    The existing KeyedTable constructor takes a file-like object, so
    we wrap `data` in a BytesIO and reuse the parser unchanged.  The
    returned KeyedTable's cell file_offsets are valid indices into the
    original buffer, which is exactly what `set_cell_double` expects.
    """
    return KeyedTable(io.BytesIO(data), section_offset, section_name)


class KeyedTable:
    """Parser for a single keyed table section in config.xbr."""

    def __init__(self, f, section_offset, section_name=""):
        self.section_offset = section_offset
        self.section_name = section_name
        self.table_base = section_offset + 0x1000
        self._config_path = None  # set by load_all_tables for deferred reads

        # Read header
        f.seek(self.table_base)
        hdr = struct.unpack('<5I', f.read(20))
        self.num_rows = hdr[0]
        self.row_hdr_offset = hdr[1]
        self.num_cols = hdr[2]
        self.total_cells = hdr[3]
        self.cell_data_off = hdr[4]

        assert self.num_rows * self.num_cols == self.total_cells, \
            f"Cell count mismatch: {self.num_rows}*{self.num_cols} != {self.total_cells}"

        # Pre-read all data we need: row names, column names, and all cells
        # This avoids holding the file handle open.

        # Read row names (property names)
        self.row_names = []
        for r in range(self.num_rows):
            self.row_names.append(self._read_row_name_from_file(f, r))

        # Read ALL cells into memory
        self._cells = {}  # (col, row) -> (type_str, value, file_offset)
        for c in range(self.num_cols):
            for r in range(self.num_rows):
                cell_data = self._read_cell_from_file(f, c, r)
                if cell_data[0] != 'empty':
                    self._cells[(c, r)] = cell_data

        # Read column names (entity names) from row 0 ("name" row)
        self.col_names = []
        for c in range(self.num_cols):
            cell = self._cells.get((c, 0))
            if cell and cell[0] == 'string':
                self.col_names.append(cell[1])
            else:
                self.col_names.append(f"col_{c}")

        # Build entity -> column index lookup
        self.entity_index = {name: i for i, name in enumerate(self.col_names)}

    def _read_row_name_from_file(self, f, row_idx):
        """Read the property name for a given row index from an open file."""
        row_hdr_base = self.table_base + self.row_hdr_offset + 4
        entry_addr = row_hdr_base + row_idx * 8
        f.seek(entry_addr + 4)
        rel = struct.unpack('<I', f.read(4))[0]
        str_addr = entry_addr + 4 + rel
        return self._read_string_from_file(f, str_addr)

    @staticmethod
    def _read_string_from_file(f, addr):
        """Read a null-terminated ASCII string at the given file offset."""
        f.seek(addr)
        s = b''
        while len(s) < 256:
            c = f.read(1)
            if c == b'\x00' or not c:
                break
            s += c
        return s.decode('ascii', errors='replace')

    def _cell_addr(self, col, row):
        """Compute the file offset of a cell."""
        return (self.table_base + self.cell_data_off + 0x10 +
                (self.num_rows * col + row) * 16)

    def _read_cell_from_file(self, f, col, row):
        """Read a cell from an open file handle."""
        cell_addr = self._cell_addr(col, row)
        f.seek(cell_addr)
        cell = f.read(16)
        ctype = struct.unpack('<I', cell[0:4])[0]

        if ctype == 0:
            return ('empty', None, cell_addr)
        elif ctype == 1:
            val = struct.unpack('<d', cell[8:16])[0]
            return ('double', val, cell_addr)
        elif ctype == 2:
            string_off = struct.unpack('<I', cell[12:16])[0]
            str_addr = cell_addr + 12 + string_off
            return ('string', self._read_string_from_file(f, str_addr), cell_addr)
        else:
            return ('unknown', ctype, cell_addr)

    def read_cell(self, col, row):
        """Read a cell, returning (type_str, value, file_offset).

        type_str: 'empty', 'double', 'string', or 'unknown'
        value: None, float, str, or int (for unknown types)
        file_offset: file offset of the cell (for patching)
        """
        cell = self._cells.get((col, row))
        if cell:
            return cell
        return ('empty', None, self._cell_addr(col, row))

    def get_value(self, entity_name, property_name):
        """Look up a value by entity and property name.

        Returns (type_str, value, file_offset) or None if not found.
        """
        col = self.entity_index.get(entity_name)
        if col is None:
            return None
        try:
            row = self.row_names.index(property_name)
        except ValueError:
            return None
        return self.read_cell(col, row)

    def get_entity(self, entity_name):
        """Get all non-empty properties for an entity.

        Returns dict of {property_name: (type_str, value, file_offset)}.
        """
        col = self.entity_index.get(entity_name)
        if col is None:
            return {}
        result = {}
        for r in range(self.num_rows):
            typ, val, addr = self.read_cell(col, r)
            if typ != 'empty':
                result[self.row_names[r]] = (typ, val, addr)
        return result

    def iter_entities(self):
        """Iterate over all entities, yielding (name, col_index)."""
        for c, name in enumerate(self.col_names):
            yield name, c

    def dump_entity(self, entity_name):
        """Pretty-print all properties for an entity."""
        props = self.get_entity(entity_name)
        if not props:
            print(f"Entity '{entity_name}' not found in {self.section_name}")
            return
        print(f"=== {entity_name} in {self.section_name} ===")
        for prop_name, (typ, val, addr) in props.items():
            print(f"  {prop_name:30s} [{typ:6s}] = {val!s:20s} @ 0x{addr:06X}")


# Section map: (toc_index, file_offset, name)
KEYED_SECTIONS = [
    (0,  0x002000, "armor_hit_fx"),
    (1,  0x004000, "armor_properties"),
    (2,  0x006000, "attacks_anims"),
    (3,  0x008000, "attacks_transitions"),
    (4,  0x01A000, "critters_critter_data"),
    (5,  0x035000, "critters_damage"),
    (6,  0x044000, "critters_damage_fx"),
    (7,  0x05A000, "critters_engine"),
    (8,  0x05D000, "critters_flocking"),
    (9,  0x060000, "critters_item_data"),
    (10, 0x065000, "critters_maya_stuff"),
    (11, 0x066000, "critters_mutate"),
    (12, 0x077000, "critters_sounds"),
    (13, 0x07A000, "critters_special_anims"),
    (14, 0x083000, "critters_walking_dmg"),
    (16, 0x087000, "magic"),
    # armor_properties: table header at 0x3000 (within armor_hit_fx extent), NOT at TOC offset 0x4000
    # 15 rows x 19 columns = 285 cells, grid at 0x308C
    (-1, 0x002000, "armor_properties_real"),
]


def load_all_tables(config_path, sections=None):
    """Load keyed tables from config.xbr. Returns dict of name -> KeyedTable.

    ``sections``: optional iterable of section names.  When provided,
    only the named sections are loaded — the rest are skipped entirely.
    This is a meaningful saving for callers that only need one or two
    tables (e.g. a ``--config-mod`` with ``_keyed_patches`` touching
    a single section): full load parses every byte of every grid in
    every section, but partial load is roughly O(|sections|) of that
    total.  Default ``None`` loads everything, preserving prior
    behaviour.

    Unknown section names are silently ignored — callers don't have to
    filter against ``KEYED_SECTIONS`` themselves.
    """
    want = set(sections) if sections is not None else None
    tables = {}
    with open(config_path, 'rb') as f:
        for toc_idx, offset, name in KEYED_SECTIONS:
            if want is not None and name not in want:
                continue
            try:
                tables[name] = KeyedTable(f, offset, name)
            except Exception as e:
                print(f"Warning: Failed to load {name}: {e}", file=sys.stderr)
    return tables


def patch_double_value(config_path, cell_file_offset, new_value):
    """Patch a type-1 (double) cell value in config.xbr.

    cell_file_offset: from read_cell() return value
    new_value: float to write as IEEE 754 double

    The double is at cell_offset + 8 (bytes 8-15 of the 16-byte cell).
    Cell type must already be 1 (double). Does NOT change type field.
    """
    with open(config_path, 'r+b') as f:
        # Verify cell type is 1
        f.seek(cell_file_offset)
        ctype = struct.unpack('<I', f.read(4))[0]
        if ctype != 1:
            raise ValueError(
                f"Cell at 0x{cell_file_offset:06X} has type {ctype}, expected 1 (double). "
                f"Cannot patch non-double cells.")
        # Write new double at offset + 8
        f.seek(cell_file_offset + 8)
        f.write(struct.pack('<d', new_value))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Parse Azurik config.xbr keyed table sections")
    parser.add_argument("config_xbr", nargs="?", default="game_files/gamedata/config.xbr",
                        help="Path to config.xbr")
    parser.add_argument("--section", "-s", help="Section name to examine")
    parser.add_argument("--entity", "-e", help="Entity name to dump")
    parser.add_argument("--property", "-p", help="Property name to look up (requires --entity)")
    parser.add_argument("--list-sections", action="store_true", help="List all keyed sections")
    parser.add_argument("--list-entities", action="store_true", help="List entities in section")
    parser.add_argument("--list-properties", action="store_true", help="List properties in section")
    parser.add_argument("--find", help="Find an entity across all sections")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--registry", action="store_true",
                        help="Build full registry of all patchable double values")
    args = parser.parse_args()

    tables = load_all_tables(args.config_xbr)

    if args.list_sections:
        print(f"{'Section':30s} {'Rows':>5s} {'Cols':>5s} {'Cells':>6s}")
        print("-" * 50)
        for name, tbl in tables.items():
            print(f"{name:30s} {tbl.num_rows:5d} {tbl.num_cols:5d} {tbl.total_cells:6d}")
        return

    if args.find:
        print(f"Searching for entity '{args.find}' across all sections:")
        for name, tbl in tables.items():
            if args.find in tbl.entity_index:
                props = tbl.get_entity(args.find)
                doubles = sum(1 for t, _, _ in props.values() if t == 'double')
                strings = sum(1 for t, _, _ in props.values() if t == 'string')
                print(f"  {name:30s} col={tbl.entity_index[args.find]:3d}  "
                      f"{doubles} doubles, {strings} strings")
        return

    if args.registry:
        registry = {}
        for sec_name, tbl in tables.items():
            for entity_name, col in tbl.iter_entities():
                for r in range(tbl.num_rows):
                    typ, val, addr = tbl.read_cell(col, r)
                    if typ == 'double':
                        key = f"{sec_name}/{entity_name}/{tbl.row_names[r]}"
                        registry[key] = {
                            "section": sec_name,
                            "entity": entity_name,
                            "property": tbl.row_names[r],
                            "value": val,
                            "file_offset": addr,
                            "value_offset": addr + 8,
                            "type": "double"
                        }
        if args.json:
            print(json.dumps(registry, indent=2))
        else:
            print(f"Total patchable double values: {len(registry)}")
            by_section = {}
            for info in registry.values():
                sec = info["section"]
                by_section[sec] = by_section.get(sec, 0) + 1
            for sec, count in sorted(by_section.items()):
                print(f"  {sec:30s} {count:5d}")
        return

    if args.section:
        tbl = tables.get(args.section)
        if not tbl:
            print(f"Section '{args.section}' not found. Available: {list(tables.keys())}")
            return

        if args.list_entities:
            for name, c in tbl.iter_entities():
                print(f"  [{c:3d}] {name}")
            return

        if args.list_properties:
            for i, name in enumerate(tbl.row_names):
                print(f"  [{i:2d}] {name}")
            return

        if args.entity:
            if args.property:
                result = tbl.get_value(args.entity, args.property)
                if result is None:
                    print(f"Not found: {args.entity}/{args.property}")
                else:
                    typ, val, addr = result
                    if args.json:
                        print(json.dumps({"type": typ, "value": val,
                                          "file_offset": f"0x{addr:06X}"}))
                    else:
                        print(f"{val} (type={typ}, offset=0x{addr:06X})")
            else:
                tbl.dump_entity(args.entity)
            return

    # Default: show garret4 walkSpeed/runSpeed as proof of concept
    print("=== Proof of Concept: Key Gameplay Values ===\n")
    for entity in ["garret4", "flicken", "channeler"]:
        print(f"--- {entity} ---")
        # attacks_transitions has walkSpeed, runSpeed
        at = tables.get("attacks_transitions")
        if at:
            for prop in ["walkSpeed", "runSpeed", "attackRange"]:
                result = at.get_value(entity, prop)
                if result and result[0] == 'double':
                    typ, val, addr = result
                    print(f"  {prop:20s} = {val:8.2f}  (double @ 0x{addr:06X}, patch at 0x{addr+8:06X})")

        # critters_critter_data has hitPoints
        cd = tables.get("critters_critter_data")
        if cd:
            for prop in ["hitPoints"]:
                result = cd.get_value(entity, prop)
                if result and result[0] == 'double':
                    typ, val, addr = result
                    print(f"  {prop:20s} = {val:8.2f}  (double @ 0x{addr:06X}, patch at 0x{addr+8:06X})")

        # critters_damage_fx has collisionRadius
        df = tables.get("critters_damage_fx")
        if df:
            for prop in ["collisionRadius", "scale"]:
                result = df.get_value(entity, prop)
                if result and result[0] == 'double':
                    typ, val, addr = result
                    print(f"  {prop:20s} = {val:8.2f}  (double @ 0x{addr:06X}, patch at 0x{addr+8:06X})")
        print()

    # Magic section (key-value pairs)
    print("--- Magic / Player Settings ---")
    mg = tables.get("magic")
    if mg:
        for c in range(mg.num_cols):
            key_t, key_v, _ = mg.read_cell(c, 0)
            val_t, val_v, val_addr = mg.read_cell(c, 1)
            if val_t == 'double':
                print(f"  {key_v:25s} = {val_v:8.2f}  (double @ 0x{val_addr:06X})")


if __name__ == "__main__":
    main()
