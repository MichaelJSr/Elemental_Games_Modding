# Azurik XBR file format

Byte-level reference for the `.xbr` container that Azurik uses for
every bundled data file.  Paired with the in-repo parsers:

- [`azurik_mod/xbr/`](../azurik_mod/xbr/) — the structural
  document model used by the mod infrastructure.
- [`scripts/xbr_parser.py`](../scripts/xbr_parser.py) — the
  older full-fat parser kept around for its CLI + config-specific
  goodies.
- [`azurik_mod/assets/index_xbr.py`](../azurik_mod/assets/index_xbr.py)
  — index-record specifics.
- [`azurik_mod/config/keyed_tables.py`](../azurik_mod/config/keyed_tables.py)
  — config.xbr keyed-table semantics.

Everything below is pinned by
[`tests/test_xbr_document_roundtrip.py`](../tests/test_xbr_document_roundtrip.py)
— if the format changes under us (e.g. a different ISO revision),
those tests fail loudly.

---

## File anatomy

```
+-------------------------------+  0x00
| Header (0x40 bytes)           |
+-------------------------------+  0x40
| TOC row 0   (16 bytes)        |
| TOC row 1                      |
| ...                            |
| TOC row N-1                   |
| TOC terminator (16 zero bytes)|
+-------------------------------+
| (often a zero gap — preserved |
|  verbatim on round-trip)      |
+-------------------------------+
| Section payload 0             |
| Section payload 1             |
| ...                            |
+-------------------------------+  EOF
```

Each TOC entry points at a **payload region** elsewhere in the file
via its absolute `file_offset`.  Payload regions don't need to be
contiguous with the TOC — vanilla files leave gaps, often aligned
to 0x1000.  The document model preserves every byte, including
gaps, to guarantee byte-identity round-trip.

---

## Header (0x00 — 0x3F)

| Offset | Size | Field        | Status     | Notes |
|--------|------|--------------|------------|-------|
| 0x00   | 4    | `magic`      | **known**  | Always `b"xobx"`. |
| 0x04   | 4    | u32          | UNKNOWN    | Zero in every vanilla XBR. |
| 0x08   | 4    | u32          | UNKNOWN    | Zero in every vanilla XBR. |
| 0x0C   | 4    | `toc_count`  | **known**  | Number of non-terminator TOC rows. |
| 0x10   | 4    | u32          | UNKNOWN    | Zero in every vanilla XBR. |
| 0x14   | 4    | u32          | UNKNOWN    | Varies per-file; role unknown. |
| 0x18   | 4    | u32          | UNKNOWN    | Varies per-file; role unknown. |
| 0x1C   | 4    | u32          | UNKNOWN    | Varies per-file; role unknown. |
| 0x20   | 32   | opaque       | UNKNOWN    | Preserved verbatim; never rewritten. |

**Implementation note.** The document model rewrites only `toc_count`
at `0x0C` when the TOC size changes.  Everything else in the header
is round-tripped verbatim.

---

## Table of contents (from 0x40)

Each row is 16 bytes, little-endian:

| Offset | Size | Field         | Notes |
|--------|------|---------------|-------|
| +0     | 4    | `size`        | Payload length in bytes. |
| +4     | 4    | `tag`         | 4-char ASCII (e.g. `"tabl"`, `"node"`, `"surf"`, `"indx"`). |
| +8     | 4    | `flags`       | Opaque u32.  Round-tripped as-is. |
| +12    | 4    | `file_offset` | Absolute file offset of the payload. |

Termination: the first row whose `(size, flags, file_offset)` is all
zero ends the TOC.  (The tag bytes in the terminator row are also
zero in every vanilla file, but aren't required for detection.)

### Tag inventory

Populated by iterating every vanilla XBR.  Unmodeled tags use the
:class:`azurik_mod.xbr.RawSection` overlay — safe to round-trip,
but no structural edits possible.

| Tag     | Appears in                       | Parser state |
|---------|----------------------------------|--------------|
| `tabl`  | config.xbr (18x)                 | **keyed-table** or **variant-record** depending on file offset.  See below. |
| `indx`  | index.xbr (1x)                   | **index-records** (partial — pool math unreversed).  See [`index_xbr.py`](../azurik_mod/assets/index_xbr.py). |
| `node`  | every level XBR                  | RawSection.  String pool scannable; record layout heuristic-only. |
| `surf`  | every level XBR                  | RawSection.  Stride classifier in [`xbr_inspect.py`](../azurik_mod/xbe_tools/xbr_inspect.py). |
| `rdms`  | every level XBR                  | RawSection. |
| `ents`  | some level XBRs                  | RawSection. |
| `body`  | character / level XBRs           | RawSection. |
| `banm`  | animation bundles                | RawSection. |
| `wave`  | fx.xbr + level XBRs              | RawSection.  Audio codec partially reversed in [`audio_dump.py`](../azurik_mod/xbe_tools/audio_dump.py) / `docs/LEARNINGS.md`. |
| `levl`  | index.xbr pool                   | RawSection. |
| `font`  | interface.xbr, etc.              | RawSection. |

"RawSection" = bytes are preserved and indexable but the parser
doesn't decode any structure.  Structural edits on those tags
raise `NotImplementedError` clearly.

---

## Keyed-table section (15 of 18 config.xbr entries)

Section-local offsets (the section's own payload start = TOC
`file_offset`):

```
0x0000 .. 0x0FFF     String pool (NUL-terminated ASCII names)
0x1000 .. 0x1013     Table header (20 bytes = 5 x u32)
0x1000 + 0x14        Row headers (8 bytes each, num_rows rows)
0x1000 + cell_off +  Cell grid (16 bytes each, column-major)
  0x10 .. end
(after cells)        String data for type-2 cells (self-relative)
```

### Table header

| Offset | Size | Field            | Notes |
|--------|------|------------------|-------|
| +0x00  | 4    | `num_rows`       | Property count. |
| +0x04  | 4    | `row_hdr_offset` | Always `0x10` in vanilla. |
| +0x08  | 4    | `num_cols`       | Entity count. |
| +0x0C  | 4    | `total_cells`    | `num_rows * num_cols`. |
| +0x10  | 4    | `cell_data_off`  | Cell grid base, relative to table base minus 0x10. |

### Row header record (8 bytes per row)

| Offset | Size | Field | Notes |
|--------|------|-------|-------|
| +0     | 4    | opaque | Always 0 in vanilla. |
| +4     | 4    | `name_ref` | **Self-relative** u32: string address = `row_entry + 4 + name_ref`. |

### Cell record (16 bytes per cell)

Cells are laid out column-major: column `c`, row `r` lives at
`cell_base + (num_rows * c + r) * 16`.

| Offset | Size | Type     | Usage                                        |
|--------|------|----------|----------------------------------------------|
| +0     | 4    | u32      | **type code**: 0 empty, 1 double, 2 string.  |
| +4     | 4    | u32      | padding (zero).                              |
| +8     | 8    | variant  | Type-1: IEEE-754 double.  Type-2: see below. |

For **type-2** cells the `+8..+16` payload is:

| Offset | Size | Field           | Notes |
|--------|------|-----------------|-------|
| +8     | 4    | `string_length` | Length in bytes, excluding NUL. |
| +12    | 4    | `string_ref`    | **Self-relative** u32: string address = `cell + 12 + string_ref`. |

### Pointer graph

For every keyed-table section:

- One `SelfRelativeRef` per row (source = row_entry + 4, origin =
  row_entry + 4).
- One `SelfRelativeRef` per type-2 cell (source = cell + 12, origin
  = cell + 12).

Both ref types are modeled in
[`azurik_mod/xbr/refs.py`](../azurik_mod/xbr/refs.py).  The pointer
graph walks them to answer structural-edit queries ("if I grow this
string by N bytes, which fields need patching?").

---

## Variant-record sections (3 of 18 config.xbr entries)

`critters_walking`, `damage`, `settings_foo`.  Fixed-stride record
arrays; schemas are in
[`azurik_mod/xbr/sections.py`](../azurik_mod/xbr/sections.py) (also
legacy [`scripts/xbr_parser.py`](../scripts/xbr_parser.py) —
cross-checked by `tests/test_xbr_document_roundtrip.py`).

### Record layout

Per entity block: `props_per_entity * record_size` bytes.
Per property record: a leading prefix (0 or 16 bytes depending on
`record_size`), then `u32 type_flag` + `double value`.

| Schema              | `record_size` | `props_per_entity` | `entity_count` |
|---------------------|---------------|--------------------|----------------|
| `critters_walking`  | 16            | 18                 | 107            |
| `damage`            | 16            | 8                  | 11             |
| `settings_foo`      | 48            | 6                  | 1              |

### Pointer graph

No ref fields are currently reversed for variant records.  Edits
that change a record's stride would need additional RE; the
current model only supports in-place double rewrites (see
[`azurik_mod/xbr/edits.py`](../azurik_mod/xbr/edits.py)).

---

## `indx` payload (index.xbr)

Header + 20-byte records + trailing string pool.  See
[`azurik_mod/assets/index_xbr.py`](../azurik_mod/assets/index_xbr.py)
for the full reverse-engineering notes; the critical unknowns are:

1. Exact pool base math for the `off1` / `off2` columns.  Records
   point at file-name (`off1`) and asset-key (`off2`) strings in
   a pool whose start we've pinned empirically but whose base
   math doesn't line up symbol-for-symbol.
2. Role of `header_hint` (`24`) and `pool_hint` (`0xEFFC`) in the
   16-byte payload header.
3. Why `count` is `3072` when only 3071 records are real.

Until those are pinned, the index section rides as an
`IndexRecordsSection` that exposes the header fields but yields
zero refs.  Structural edits raise.

---

## Level-XBR payload sections (unknown structure)

Level XBRs (`a1.xbr`, `town.xbr`, …) carry dozens of per-section
payload types tagged `node`, `surf`, `rdms`, `ents`, …  None of
their record-level layouts have been fully reversed.  What we know:

- String pools exist *inside* each section (vanilla sections carry
  recognisable ASCII filenames, portal names, etc.) — the scanner
  in [`scripts/xbr_parser.py`](../scripts/xbr_parser.py) surfaces
  them heuristically.
- Pointer fields almost certainly exist (cross-section references
  are needed to wire entities to their data), but the exact
  record layouts haven't been pinned.
- Stride patterns are heuristic — see
  [`azurik_mod/xbe_tools/xbr_inspect.py`](../azurik_mod/xbe_tools/xbr_inspect.py).

Until those are reversed, level-XBR structural edits aren't
supported.  The document model keeps level XBRs round-trippable
(they parse as a chain of `RawSection`) and the edit primitives
raise `NotImplementedError` with pointers at exactly what RE is
needed.

---

## Known limitations

These aren't gaps in the reversal, they're intentional scope
boundaries of the current mod platform.  Documented so modders
know not to expect them.

- **`azurik-mod verify-patches` doesn't audit XBR state.**  The
  verify-patches tool scans the XBE for byte drift against every
  shipped `PatchSpec` + `TrampolinePatch`.  It does NOT currently
  re-parse patched `.xbr` files and cross-check `XbrEditSpec` /
  `XbrParametricEdit` landing.  For now, use `azurik-mod xbr
  verify <file>` manually to check round-trip / ref integrity;
  XBR-side byte drift auditing is a natural follow-up if a
  shipping XBR pack gains safety-critical semantics.

- **Migrating `randomizer/level_editor.py` to `XbrEditSpec` is
  optional.**  The randomizer has its own in-place XBR writer
  for level shuffle pools; the declarative `xbr_sites` API is
  available if a future pack wants it but the randomizer itself
  doesn't need the migration.

## Backlog

Ordered roughly by demand vs. cost.

1. **index.xbr pool base.**  Unblocks writing a valid index after
   growing any asset table — not just same-size string edits.
2. **Variant-record string refs.**  Currently `critters_walking`
   entities are discovered by a heuristic name scan.  A proper
   pointer-graph entry lets the randomizer / entity editor mutate
   names safely.
3. **Level-XBR `node` section layout.**  Highest-value level tag
   (contains entity placements + portal wiring).  Needs per-field
   reversal via Ghidra on the level loader.
4. **Level-XBR `surf` section layout.**  Triangle / material data.
   Speculative — only required if anyone ever wants to edit
   geometry from a mod.
5. **Header bytes `0x14..0x1F`.**  Empirically varies per-file but
   no reversal yet — might be a CRC or a build timestamp.
