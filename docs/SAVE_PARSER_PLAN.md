# Plan: Azurik Save Format Parser

## Goal

Build a `parse_save.py` companion to `extract_save.py` that can decode, display, and
modify Azurik save files. This unlocks automated testing of the randomizer and QoL
patches, save-state debugging, and eventually a save editor in the GUI.

---

## Phase 1 — Reverse-engineer the save format

### 1a. Collect sample pairs

Use `extract_save.py` to pull saves at known game states:

| Sample | State |
|--------|-------|
| `fresh_start/` | New game, no pickups, standing at w1_beach |
| `one_obsidian/` | Pick up one obsidian, save |
| `two_obsidian/` | Pick up two obsidians, save |
| `one_key/` | Pick up a key, save |
| `one_power/` | Pick up a power-up, save |
| `one_fragment/` | Pick up a disc fragment, save |
| `full_run/` | Complete the first world with several collectibles |

### 1b. Diff the binaries

Write a small diff utility (or use `hexdump` + `diff`) to compare each pair of
extracted `.sav` files byte-by-byte. Focus on:

- `shared.sav` — likely stores collected-item flags
- `inv.sav` — likely stores inventory state (obsidian count, keys held, etc.)
- Per-level `.sav` files — likely store which pickups were despawned in that level

Track which bytes change and what value they hold for each known action.

### 1c. Cross-reference with Ghidra

Use the Ghidra MCP to find the save read/write functions in `default.xbe`:

- `FUN_00061360` (identified during QoL work) sets the "collected" flag and adds
  pickups to a save list — trace forward to find the serialization call.
- Search for FATX/file-write syscalls (`XCreateFile`, `XWriteFile`) to locate the
  code that flushes the save structs to disk.
- Map out the in-memory save struct layout: flag bitmasks, counter offsets, level
  slot indices.

### 1d. Document the format

Produce a `SAVE_FORMAT.md` describing each `.sav` file:

| File | Contents (expected) |
|------|---------------------|
| `inv.sav` | Obsidian count, key bitfield, disc fragment bitfield, power-up bitfield, gem counts |
| `magic.sav` | Unlocked spells / elemental abilities |
| `loc.sav` | Current level ID, player coordinates (x/y/z), camera angles |
| `shared.sav` | Global flags: boss-killed bits, cutscene-seen bits, world-unlock bits |
| `<level>.sav` | Per-level pickup collected bitfield, enemy respawn state |

---

## Phase 2 — Build the parser (`parse_save.py`)

### 2a. Read-only decode

```
python parse_save.py decode save_data/slot_0/
```

Output a human-readable summary:

```
=== inv.sav ===
Obsidians: 3
Keys held: [water_key, fire_key]
Disc fragments: 2/13
Powers: [power_water]

=== loc.sav ===
Level: w1_beach
Position: (-142.5, 30.0, 88.2)

=== shared.sav ===
Flags: boss_lava_defeated, cutscene_intro_seen, world2_unlocked

=== Level: w1_beach.sav ===
Collected pickups: [obsidian_0, obsidian_2, key_water]
```

### 2b. JSON export

```
python parse_save.py dump save_data/slot_0/ -o save_state.json
```

Produce a machine-readable JSON of the entire save state for use by automated tools.

### 2c. Data classes

Define Python dataclasses for each save component:

```python
@dataclass
class InventorySave:
    obsidian_count: int
    keys: list[str]
    disc_fragments: int
    powers: list[str]

@dataclass
class LocationSave:
    level_id: str
    x: float
    y: float
    z: float

@dataclass
class LevelSave:
    level_id: str
    collected_pickups: set[str]

@dataclass
class GameSave:
    inventory: InventorySave
    location: LocationSave
    shared_flags: dict[str, bool]
    levels: dict[str, LevelSave]
```

---

## Phase 3 — Save writer / editor

### 3a. Round-trip fidelity

Implement `encode()` that serializes `GameSave` back to binary and verify
`decode(encode(save)) == save` for every sample.

### 3b. CLI write mode

```
python parse_save.py edit save_data/slot_0/ \
    --set inv.obsidian_count=99 \
    --set loc.level_id=w3_core \
    -o save_data/slot_0_modded/
```

### 3c. Inject back into QCOW2

Extend `extract_save.py` (or add an `inject_save.py`) to write modified `.sav` files
back into the xemu HDD image. This requires implementing FATX write support:

- Locate the target file's cluster chain in the FAT.
- If the new data fits in the existing chain, overwrite in-place.
- If the data is larger, allocate new clusters and update the FAT + directory entry.
- Write through the QCOW2 layer (allocate host clusters for any copy-on-write pages).

---

## Phase 4 — Automated test harness

### 4a. Randomizer validation

After `azurik_mod.py randomize` builds an ISO:

1. Boot xemu headlessly (if possible) or manually play through a short sequence.
2. Extract the save via `extract_save.py`.
3. Parse it with `parse_save.py`.
4. Assert that collected pickups in the save match the randomizer's placement map
   from `solver.py`.

### 4b. QoL patch regression tests

Automate the test that was done manually for the pickup animation fix:

1. Build a patched ISO.
2. Start new game, collect one obsidian, save, reload.
3. Extract both saves (pre-reload and post-reload).
4. Parse and diff: obsidian count should be 1 in both, and the level save should
   show the obsidian as collected in both.

### 4c. Save format version detection

If future patches change the save layout (unlikely for a 2001 game, but possible if
custom save extensions are added for the randomizer), include a version/magic check
at the start of each `.sav` to detect format mismatches.

---

## Phase 5 — GUI integration

### 5a. Save viewer tab

Add a new tab to `azurik_gui` that:

- Lets the user point to their `xbox_hdd.qcow2`.
- Extracts and decodes saves automatically.
- Displays inventory, location, flags, and per-level pickup state in a tree view.

### 5b. Save editor tab

Extend the viewer to allow editing values and writing them back. Useful for:

- Testing specific game states without replaying.
- Debugging randomizer seeds that fail mid-game.
- Teleporting the player to a specific level for rapid iteration.

---

## Dependencies and prerequisites

| Dependency | Status |
|------------|--------|
| `extract_save.py` (QCOW2 + FATX reader) | Exists, working |
| Ghidra MCP access to `default.xbe` | Available |
| xemu with LLDB for runtime tracing | Available |
| Sample QCOW2 with saves at known states | Needs to be collected |
| `xbr_parser.py` (for cross-referencing entity names) | Exists, recently fixed |

## Estimated effort

| Phase | Effort | Depends on |
|-------|--------|------------|
| Phase 1 (reverse-engineer) | ~2-3 sessions | Ghidra + sample saves |
| Phase 2 (read-only parser) | ~1 session | Phase 1 |
| Phase 3 (writer/editor) | ~1-2 sessions | Phase 2 |
| Phase 4 (test harness) | ~1 session | Phase 2 + xemu automation |
| Phase 5 (GUI integration) | ~1-2 sessions | Phase 3 |

---

## Open questions

1. **Are per-level `.sav` files always present, or created on first visit?**
   Collecting saves from an early game state vs. a late game state will clarify this.

2. **Is `shared.sav` a flat bitfield or a structured record?**
   The flag count is unknown — could be a few dozen bools or a more complex struct.

3. **Does the game checksum its saves?**
   If so, the writer must recompute the checksum after edits. Check for a CRC/hash
   field in the first or last bytes of each `.sav`.

4. **Can xemu be driven headlessly for automated testing?**
   If not, the test harness (Phase 4) would need a manual step or a scripted
   input-replay approach.
