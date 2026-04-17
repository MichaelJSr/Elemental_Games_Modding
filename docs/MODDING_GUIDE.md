# Azurik: Rise of Perathia — Modding Toolkit

A set of tools for modding Azurik: Rise of Perathia (Xbox, 2001). Patch game config values, randomize collectibles, edit level entities, inspect game files, and extract save data. All tools produce xemu-ready ISOs.

## Requirements

- **Python 3.10+**
- **xdvdfs** — Xbox DVD filesystem tool ([download](https://github.com/antangelo/xdvdfs/releases))
  - Place `xdvdfs.exe` in the `tools/` folder, or install via `cargo install xdvdfs-cli`
- **Original Azurik ISO** — Your own legally-obtained copy of the game
- **xemu** — Original Xbox emulator ([xemu.app](https://xemu.app)) for testing

## Quick Start

### 1. Randomize All Collectibles

Shuffle gem types, disk fragments, and elemental power-ups across every level:

```bash
python azurik_mod.py randomize \
  --iso "Azurik - Rise of Perathia.iso" \
  --seed 42 \
  --output Azurik_randomized.iso
```

This produces a fully playable ISO where:
- **Gems** are shuffled within each level (type counts preserved per-level)
- **Disk fragments** (13 total) are shuffled across all levels
- **Elemental power-ups** (9 total) are shuffled across all levels

Use `--seed` for reproducible results. Same seed = same layout every time.

Skip specific categories if you want:
```bash
# Only randomize fragments and powers (leave gems alone)
python azurik_mod.py randomize --iso Azurik.iso --seed 42 --no-gems -o modded.iso

# Only randomize gems
python azurik_mod.py randomize --iso Azurik.iso --seed 42 --no-fragments --no-powers -o modded.iso
```

### 2. Apply a Config Mod

Patch gameplay values (enemy behavior, player stats) using a JSON mod file:

```bash
python azurik_mod.py patch \
  --iso "Azurik - Rise of Perathia.iso" \
  --mod example_enemy_buff.json \
  --output Azurik_modded.iso
```

You can stack multiple mods:
```bash
python azurik_mod.py patch --iso Azurik.iso \
  -m example_enemy_buff.json \
  -m example_player_boost.json \
  -o Azurik_modded.iso
```

### 3. Preview Changes Without Building

```bash
# See what a mod would change
python azurik_mod.py diff --iso Azurik.iso --mod example_enemy_buff.json

# Dump current values for a config section
python azurik_mod.py dump --iso Azurik.iso -s critters_walking -e catalisk
```

---

## Tools Reference

### azurik_mod.py — Main Mod Tool

The primary tool for building modded ISOs.

| Command | Description |
|---------|-------------|
| `randomize` | Randomize gems + fragments + powers, build ISO |
| `randomize-gems` | Randomize gems only (legacy), build ISO |
| `patch` | Apply JSON mod file(s) to ISO |
| `diff` | Preview what a mod would change |
| `dump` | Show current config values |
| `list` | Browse available config sections and entities |

**Browse what's moddable:**
```bash
# List all config sections
python azurik_mod.py list --sections

# List entities in a section
python azurik_mod.py list --entities critters_walking

# Dump all values for an entity
python azurik_mod.py dump --iso Azurik.iso -s critters_walking -e air_elemental
```

### level_editor.py — Level Entity Editor

Edit individual level XBR files directly (no ISO round-trip needed).

```bash
# List all entities in a level
python level_editor.py list gamedata/a3.xbr

# List only gems
python level_editor.py list gamedata/a3.xbr --category gems

# Randomize gem types in a single level
python level_editor.py randomize gamedata/a3.xbr --seed 42 -o a3_randomized.xbr

# Rename an entity (e.g., change a power-up type)
python level_editor.py rename gamedata/a3.xbr power_water_a3 power_fire_a3 -o a3_fire.xbr

# Move an entity to new coordinates
python level_editor.py move gamedata/w2.xbr diamond -- -200 50 -100 -o w2_moved.xbr

# Swap positions of two entities
python level_editor.py swap gamedata/a3.xbr diamond ruby -o a3_swapped.xbr

# Shuffle positions of all gems
python level_editor.py shuffle gamedata/a3.xbr --category gems --seed 42 -o a3_shuffled.xbr
```

**Entity categories:** `gems`, `enemies`, `powerups`, `fuel`

### parse_level_toc.py — XBR File Inspector

Inspect the internal structure of any XBR file (levels, config, characters, etc.):

```bash
# Show table of contents
python parse_level_toc.py gamedata/a3.xbr

# Show TOC + dump node section strings
python parse_level_toc.py gamedata/a3.xbr --tag node --strings node

# Dump raw hex of a specific tag
python parse_level_toc.py gamedata/a3.xbr --dump rdms
```

### extract_save.py — Save Data Extractor

Extract Azurik save files from an xemu QCOW2 hard drive image:

```bash
python extract_save.py
# Reads xbox_hdd.qcow2 from the current directory
# Outputs save files to save_data/
```

Save files extracted: `inv.sav` (inventory), `magic.sav` (spells), `loc.sav` (location), `shared.sav` (flags), plus per-level `.sav` files.

---

## Writing Mod Files

Mods are JSON files that specify which config values to change. Two formats are supported:

### Grouped Format (recommended)

```json
{
  "name": "My Mod",
  "author": "Your Name",
  "description": "What this mod does",
  "format": "grouped",
  "sections": {
    "critters_walking": {
      "air": {
        "enemies": {
          "air_elemental": {
            "provoke_distance": 30.0,
            "max_distance": 5000.0,
            "attack_anim_rate": 2.0
          },
          "catalisk": {
            "stalk_time_min": 0.5,
            "stalk_time_max": 1.0
          }
        }
      }
    },
    "settings_foo": {
      "air": {
        "initial_fuel": 9999.0,
        "initial_hp": 300.0
      }
    }
  }
}
```

### Flat Format (simpler)

```json
{
  "name": "My Mod",
  "patches": [
    {
      "section": "critters_walking",
      "entity": "catalisk",
      "property": "provoke_distance",
      "value": 50.0
    },
    {
      "section": "settings_foo",
      "entity": "air",
      "property": "initial_fuel",
      "value": 9999.0
    }
  ]
}
```

### Level Entity Patches

Directly modify entities in level files:

```json
{
  "name": "Custom Level Edits",
  "level": "a3",
  "level_patches": [
    {"entity": "diamond", "action": "rename", "new_name": "ruby"},
    {"entity": "power_water_a3", "action": "rename", "new_name": "power_fire"},
    {"entity": "emerald", "action": "move", "x": -100.0, "y": 50.0, "z": -200.0}
  ]
}
```

### Moddable Config Sections

| Section | What It Controls |
|---------|-----------------|
| `critters_walking` | Enemy AI: stalk times, provoke distance, flee behavior, attack speed (34 enemies) |
| `settings_foo` | Player stats: starting fuel, fuel cap, HP (limited to 6 values) |
| `damage` | Damage types and multipliers (11 types) |
| `critters_flocking` | Flocking/swarm behavior (8 entity groups) |
| `critters_damage` | Per-enemy damage values |
| `critters_engine` | Movement/physics per enemy |

Use `python azurik_mod.py list --sections` and `--entities` to explore all available values.

---

## Included Example Mods

| File | Description |
|------|-------------|
| `example_enemy_buff.json` | Makes catalisk, golem, and air elemental more aggressive |
| `example_player_boost.json` | Increases starting fuel for easier exploration |

---

## xemu Tips

- **Always fresh boot** when switching ISOs — close xemu completely, set the new ISO, then start
- **Disable "Cache Shaders"** in xemu settings if modded levels show vanilla data
- **Clear cache partitions** via the xemu dashboard if issues persist
- **New save recommended** when using the randomizer — existing saves track collectibles by name and may conflict with randomized names

## File Structure

```
azurik_mod.py                # Main mod tool (ISO patching + randomizer)
level_editor.py              # Level entity editor
parse_level_toc.py           # XBR file inspector
extract_save.py              # Save data extractor
config_registry.json         # Offset database (required by azurik_mod.py)
property_schema.json         # Config section schema reference
MODDING_GUIDE.md             # This file
example_enemy_buff.json      # Example: aggressive enemies
example_player_boost.json    # Example: increased starting fuel
default_settings.json        # Default game settings reference
tools/
  xdvdfs.exe                 # Xbox ISO tool (download separately)
```

## Credits

Built through reverse-engineering with Ghidra and GDB. The game engine ("Elemental") was developed by Adrenium Games for Microsoft Game Studios (2001). All tools work with a legally-obtained copy of the game ISO.
