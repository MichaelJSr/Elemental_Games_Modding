# Azurik: Rise of Perathia — Modding Property Registry

## Overview

This folder contains a property registry for `game_files/gamedata/config.xbr`, the main
gameplay configuration file for Azurik: Rise of Perathia (Xbox, 2001). All identified
moddable properties are catalogued with types, value ranges, file offsets, and descriptions.

---

## Files

| File | Purpose |
|------|---------|
| `property_schema.json` | Complete schema of all moddable properties per section |
| `entity_values.json` | Extracted binary values for critters_walking and settings_foo |
| `xbr_parser.py` | Python parser/CLI tool for reading and patching config.xbr |
| `README.md` | This file |

---

## Binary Format: xobx / XBR

The `.xbr` files use a custom Xbox binary resource format (`magic = "xobx"`).

```
Offset 0x000:  Header  — "xobx" magic + metadata (18 table count)
Offset 0x040:  TOC     — 18 × 16-byte entries: [size(4)]['tabl'(4)][0x08(4)][file_offset(4)]
Offset varies: Data sections — per-section string tables + binary variant records
Offset 0x08A000: Name index — maps TOC slot # → config/ path string
```

Each data section contains:
1. A null-terminated **string table** (property names + entity names)
2. A block of **variant records** (16 bytes each):
   ```
   [0x00] dword  padding / reserved (always 0)
   [0x04] double the numeric value (IEEE 754 64-bit double-precision)
   [0x0C] dword  type flag: 1=float, 2=integer, 0=unset
   ```

---

## Config Sections

| TOC # | Config Path | File Offset | Description |
|-------|------------|-------------|-------------|
| 0 | config/armor_hit_fx | 0x002000 | Armor hit visual effects |
| 1 | config/armor_properties | 0x004000 | Armor tiers, protections, costs |
| 2 | config/attacks_anims | 0x006000 | Attack animation timings + damage/fuel multipliers |
| 3 | config/attacks_transitions | 0x008000 | Attack combo/transition logic |
| 4 | config/critters_critter_data | 0x01A000 | Per-entity speeds, HP, drops, timers |
| 5 | config/critters_damage | 0x035000 | Per-entity HP and elemental resistance multipliers |
| 6 | config/critters_damage_fx | 0x044000 | Damage visual effects per entity |
| 7 | config/critters_engine | 0x05A000 | Ambient critter spawner settings |
| 8 | config/critters_flocking | 0x05D000 | Boid/flocking behaviour parameters |
| 9 | config/critters_item_data | 0x060000 | Item spawn locations and IDs |
| 10 | config/critters_maya_stuff | 0x065000 | Maya-exported rig data |
| 11 | config/critters_mutate | 0x066000 | Mutation/transformation parameters |
| 12 | config/critters_sounds | 0x077000 | Per-entity sound effect assignments |
| 13 | config/critters_special_anims | 0x07A000 | Special animation overrides |
| 14 | config/critters_walking | 0x083000 | AI combat-movement behaviour (107 entities, 18 props each) |
| 15 | config/damage | 0x086000 | Global damage type registry |
| 16 | config/magic | 0x087000 | Magic ability definitions |
| 17 | config/settings_foo | 0x088300 | Player progression: HP, fuel, fall damage |

---

## Key Moddable Properties by Category

### Player Progression (`config/settings_foo`, offset `0x088300`)
Stored as 3×16-byte groups (48 bytes per entry), with 5 element-type keys (air/water/earth/fire/life):

| Property | Type | Description |
|----------|------|-------------|
| `initial_fuel` | float | Starting fuel per element |
| `initial_fuel_cap` | float | Starting fuel capacity |
| `fuel_cap_inc` | float | Fuel cap increase per upgrade |
| `num_fuel_inc` | int | Total upgrade slots |
| `initial_hp` | int | Player starting HP |
| `max_hp` | int | Absolute max HP |
| `lev2_obs/hp` | int | Level 2 goal and HP bonus |
| `lev3_obs/hp` | int | Level 3 goal and HP bonus |
| `lev4_obs/hp` | int | Level 4 goal and HP bonus |
| `fall_height_1..4` | float | Fall distance thresholds |
| `fall_damage_1..3` | float | HP lost per fall tier |
| `fall_min_velocity` | float | Min fall speed for damage |

### Entity Combat Stats (`config/critters_damage`, offset `0x035000`)
One record block per entity (~100 entities):

| Property | Type | Description |
|----------|------|-------------|
| `hitPoints` | int | Entity base HP |
| `damageType` | string | Damage category dealt |
| `norm_1..4` | float | Resistance mult vs normal attacks (0=immune, 1=normal, 2=double) |
| `water_1..3` | float | Resistance mult vs water attacks |
| `fire_1..3` | float | Resistance mult vs fire attacks |
| `lightning_1..3` | float | Resistance mult vs lightning attacks |
| `ice_1..3` | float | Resistance mult vs ice attacks |
| `elemental_1..3` | float | Resistance mult vs elemental attacks |
| `lava_1..3` | float | Resistance mult vs lava attacks |
| (+ acid, steam, wind, smash) | float | Additional damage type resistances |

### Entity Movement & Drops (`config/critters_critter_data`, offset `0x01A000`)
Per-entity base stats. Property name strings live in the XBE `.rdata` section around
`0x19F540` (e.g. `walkSpeed` at `0x19F568`, `runSpeed` at `0x19F54C`), referenced by the
critter config loader at `FUN_00049480`. Actual values are loaded at runtime from config.xbr.

| Property | Type | Description |
|----------|------|-------------|
| `walkSpeed` | float | Base walk speed (units/s) |
| `runSpeed` | float | Base run speed (units/s) |
| `walkAnimSpeed` | float | Walk animation multiplier |
| `runAnimSpeed` | float | Run animation multiplier |
| `attackRange` | float | Melee reach distance |
| `drownTime` | float | Seconds until drown death |
| `corpseWaitTime` | float | Corpse linger time |
| `corpseFadeTime` | float | Corpse fade duration |
| `noFreeze` | int | 1 = immune to freeze |
| `ouch2Threshold` | float | Damage to trigger pain-2 |
| `ouch3Threshold` | float | Damage to trigger pain-3 |
| `ouch1/2/3Knockback` | float | Knockback force per tier |
| `dropChance1..5` | float | Drop probability (0-1) |
| `dropCount1..5` | int | Items per drop event |
| `drop1..5` | string | Item ID (see known_items) |
| `soundMaxDist` | float | Audio falloff distance |
| `maxAlive` | int | Max simultaneous instances |

### AI Behaviour (`config/critters_walking`, offset `0x083000`)
107 entity names in the string table, ~79 with non-empty data (the rest are town NPCs
with defaults). 18 properties × 16-byte variant records = 288 bytes per entity.
Binary data begins at file offset `0x084090`. All values are 64-bit doubles.

| Property | Index | Type | Description |
|----------|-------|------|-------------|
| `stalk_time_min` | 0 | float | Min stalk duration (s) |
| `stalk_time_max` | 1 | float | Max stalk duration (s) |
| `stalk_distance_cw` | 2 | float | Clockwise orbit radius |
| `stalk_distance_ccw` | 3 | float | CCW orbit radius |
| `provoke_distance` | 4 | float | Aggro trigger range |
| `ambush_time_min/max` | 5,6 | float | Ambush wait window (s) |
| `ambush_if_hit_chance` | 7 | float | Ambush-on-hit probability |
| `need_n_allies` | 8 | int | Required allies to engage |
| `max_distance` | 9 | int | Max chase range (game units) |
| `flee_after_attack_chance` | 10 | float | Flee-post-attack probability |
| `flee_if_health_less_than` | 11 | float | HP fraction flee threshold |
| `safe_distance` | 12 | float | Safe retreat distance |
| `attack_anim_rate` | 13 | float | Attack anim speed mult |
| `max_turn_rate` | 14 | float | Max rotation speed (rad/s) |
| `turn_while_attacking` | 15 | float | Can rotate during attacks |
| `left_footstep_time` | 16 | float | Walk-cycle left step time |
| `right_footstep_time` | 17 | float | Walk-cycle right step time |

### Attack Definitions (`config/attacks_anims`, offset `0x006000`)

| Property | Type | Description |
|----------|------|-------------|
| `Damage multiplier` | float | Scales hit damage (1.0 = normal) |
| `Fuel multiplier` | float | Scales fuel cost |
| `Rate` | float | Animation rate override |
| `Anim start/end` | float | Animation frame window (normalised 0-1) |
| `Move start/end1/end2` | float | Root-motion frame window |
| `Aim angle` | float | Attack aim direction (degrees) |

### Global Damage Types (`config/damage`, offset `0x086000`)
~55 named damage types (norm, water, fire, wind, acid, steam, lightning, smash, ice, elemental, lava, …):

| Property | Type | Description |
|----------|------|-------------|
| `Level` | int | Damage tier (1–3) |
| `Damage` | float | Base HP damage per hit |
| `Delay` | float | Application delay (seconds) |
| `Cost` | float | Elemental fuel cost |
| `Freeze` | float | Freeze effect duration (seconds) |

---

## Usage: xbr_parser.py

```bash
# Print all critters_walking values
python xbr_parser.py --section critters_walking

# Print just the player settings
python xbr_parser.py --section settings_foo

# Show one entity
python xbr_parser.py --entity air_elemental

# Dump everything to JSON
python xbr_parser.py --dump-json my_export.json

# Default XBR path is game_files/gamedata/config.xbr
# Override with --file path/to/config.xbr
```

To **patch a value** use the `XBRParser.write_float()` method:
```python
from xbr_parser import XBRParser, SECTION_SCHEMAS

p = XBRParser("game_files/gamedata/config.xbr")

# Double the stalk_time_max for air_elemental (entity 0, property index 1)
RECORD = 16
base   = SECTION_SCHEMAS["critters_walking"]["record_base_offset"]
entity = 0   # air_elemental
prop   = 1   # stalk_time_max
offset = base + (entity * 18 + prop) * RECORD

current = p.read_float(offset)
print(f"Current stalk_time_max: {current}")
p.write_float("config_patched.xbr", offset, current * 2)
```

---

## Entity List (critters_walking)

107 AI entities:
`air_elemental`, `air_octopus`, `barnacle`, `tesla_tree`, `tesla_tree_x`, `bird`,
`blaze_sentinel`, `blaze_move`, `fire_bot`, `catalisk`, `catalisk_baby`, `cat_dead`,
`channeler`, `evil_noreht`, `fire_elemental`, `firedrake`, `firefly`, `fish_big`,
`fish_little1`, `fish_little2`, `flicken`, `flicken of peril`, `gargoylestone`,
`garret4`, `kingkong`, `golem`, `good_noreht`, `harvester`, `harvester_train`,
`magmar`, `keylord`, `overlord`, `overlord and gargoyle`, `overlord and golem`,
`rock_shard`, `ice_shard`, `shadow_demon`, `shard`, `fire_shard`, `skrit_water`,
`skrit_fire`, `skrit_earth`, `skrit_air`, `sleeth`, `sleeth_momma`, `sleeth_x`,
`splinter`, `water_elemental`, `guard_air`, `guard_air_clone`, `guard_earth`,
`guard_fire1`, `guard_fire2`, `guard_fire3`, `guard_water`, `water_tentacle1..3`,
`balthazar`, `spideyzar`, `rock_shard_boss`, `air_elemental_boss`, `boulder`,
`catalisk_grp`, `catalisk_baby_grp`, `rock_shard_grp`, `town_*` (NPCs)

---

## Notes

- Property names in `critters_critter_data` were confirmed from the XBE string table
  (string addresses around 0x19F540 in `default.xbe`, loaded by `FUN_00049480`).
- The `settings_foo` records use 48-byte groups (3 variant records each).
- Many entities have `type=0` (unset) for properties that do not apply to them;
  these are safely ignored and treated as no-override.
- Some integer fields (e.g. `max_distance`) store large game-world unit values
  (e.g. 9904 game units ≈ engagement range in the world coordinate system).
- The `xbe_addr` field in property_schema.json gives the XBE code address where
  that property string appears, useful for Ghidra cross-referencing.
