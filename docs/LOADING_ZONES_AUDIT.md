# Level loading zones — audit report

Scope: every point in vanilla _Azurik — Rise of Perathia (USA)_ where
the engine's level loader (`load_level_from_path`, VA `0x00053750`)
unloads the current level and loads a new one.

The canonical catalog lives at
[`azurik_mod/randomizer/loading_zones.py`](../azurik_mod/randomizer/loading_zones.py)
and is pinned against the shipped ISO by
[`tests/test_loading_zones.py`](../tests/test_loading_zones.py).
This doc is the human-readable companion.

**Revision history**

- **2026 initial audit** — catalogued 50 randomizable zones + 22
  selector zones + 3 cutscene-return zones + 4 hardcoded XBE zones
  + 16 implicit entities.
- **2026 deep-pass re-verification** (this revision) — re-scanned
  every `levels/…` string, every `levelSwitch` entity, every
  `load_level_from_path` caller, every `bink:` chain, and the
  save-game load path.  Updates:
  - **selector.xbr has 23 slots**, not 22.  The 23rd is a
    `levels/selector` self-re-entry that plays the prophecy intro
    before re-loading selector.xbr itself.
  - **`_IMPLICIT_ZONES` reshaped**.  Prior count (16) was inflated
    by counting generic `levelSwitch` substring hits; the real
    named-entity set is 18 entries across 11 XBRs, now catalogued
    with accurate entity names and notes.
  - **New `_ENDING_ZONES` category** added for the Spideyzar-kill
    credits chain in `d1.xbr` (7-movie bink sequence → Xbox shell
    via `return-to-shell`).  Previously uncatalogued.
  - **`level_teleport_helper` calling convention quirk documented**
    — declared `cdecl` in `vanilla_symbols.py` + `azurik_vanilla.h`
    but implicitly consumes parameters from `EDI` + `EBX`.
    Shims MUST either set those registers before calling or
    avoid calling it directly.

---

## 1. What counts as a "loading zone"

Anything that ends up calling `load_level_from_path` with a
non-empty level path string (or the sentinel `return-to-shell`
spot).  There are **five** distinct sources in the shipped game:

| # | Source                           | Lives in        | Examples                                          | Randomizable? |
| - | -------------------------------- | --------------- | ------------------------------------------------- | ------------- |
| 1 | `levelSwitch` buckets            | level XBRs      | `town -> life`, `f3 -> w4`                        | ✅            |
| 2 | `selector.xbr` cheat menu        | `selector.xbr`  | dev-menu level-select (23 slots)                  | ⛔ (cheat menu) |
| 3 | `diskreplace_*.xbr` cutscene tails | cutscene XBRs | `diskreplace_earth -> town`                       | ⛔ (cutscene)  |
| 4 | Hardcoded XBE literals           | `default.xbe`   | `levels/water/w1` (tutorial end), `levels/selector` (dev menu) | ⛔ (code) |
| 5 | Ending-cutscene chain            | `d1.xbr`        | Spideyzar-kill credits → Xbox shell               | ⛔ (end-game) |

Zones that have a `levelSwitch` _entity_ but **no** `levels/...`
destination string are tracked separately as "implicit" — the
engine's state machine (`scene_state_tick`, VA `0x00055AB0`) picks
the return level contextually (e.g. airship cutscenes, training-
room exits, boss-death triggers).

---

## 2. Headline numbers (post-deep-pass)

| Category                | Count | Note |
| ----------------------- | ----- | ---- |
| Randomizable zones      | **50** | shuffled by `azurik_mod.randomizer.shufflers` |
| `selector.xbr` zones    | **23** | cheat-menu only; **includes the cut level `e4` + the `levels/selector` self-re-entry** |
| Cutscene-return zones   | 3      | `diskreplace_earth`, `diskreplace_water`, `wirlpoolfixed` → `town` |
| Hardcoded XBE zones     | 4      | `w1` tutorial fallback + 3 dev-menu / shell-return literals |
| Ending-cutscene zones   | **1**  | `d1` Spideyzar credits chain → shell (new, 2026 deep-pass) |
| Implicit `levelSwitch`  | **18** | bink / airship / diskreplace / boss-trigger entities with no path string |

**Total catalogued destinations (sources 1–5): 81.**

### Why the "implicit" count changed

Prior to the deep-pass audit, the implicit count was 16 and was
derived from a naïve substring match on `levelSwitch` in XBR
bytes — which over-counts because MAYA bakes the type-class tag
into *every* `<Name>_levelSwitch\0` instance.  Re-scanning with a
null-terminated entity-name heuristic
(`\0<CamelCase>_levelSwitch\0`) gave the accurate set of 18
distinct named entities across 11 source XBRs:

| Source XBR             | Entity                            | Notes |
| ---------------------- | --------------------------------- | ----- |
| `a6`                   | `Killed_levelSwitch`              | air-boss-death → disk cutscene |
| `a6`                   | `PlaceAirDisk_levelSwitch`        | disk-placement marker → `diskreplace_air` |
| `airship`              | `EndFight_levelSwitch`            | end-of-airship-boss → scene state machine |
| `d1`                   | `D2_movie_levelSwitch`            | d2 entrance cutscene (plays `bink:death.bik`) |
| `e5`                   | `DiskReplaced_levelSwitch`        | post-earth-disk trigger → `diskreplace_earth` |
| `f6`                   | `BackToTown_levelSwitch`          | alt-path f6 → town |
| `life`                 | `Movie_levelSwitch`               | life-movie trigger |
| `w1`                   | `AirshipFight_levelSwitch`        | airship boss-fight intro |
| `w1`                   | `BossDefeated_levelSwitch`        | w1 boss-defeat → `disksrestored` cutscene |
| `training_room`        | `EndTutorial_levelSwitch`         | end-of-tutorial (XBE fallback → `levels/water/w1`) |
| `training_room`        | (anonymous movie chain)           | tutorial intro bink chain |
| `airship_docking`      | (anonymous)                       | arrival cutscene |
| `airship_docking_water`| (anonymous)                       | water-variant arrival |
| `airship_trans`        | (anonymous)                       | airship-flight intermission |
| `diskreplace_air`      | (anonymous)                       | air-disk cutscene |
| `diskreplace_earth`    | `AirShipLanding_levelSwitch`      | sibling to explicit `BacktoTown_levelSwitch` |
| `diskreplace_fire`     | (anonymous)                       | fire-disk cutscene |
| `diskreplace_life`     | (anonymous)                       | life-disk cutscene |

"Anonymous" means the XBR has only the bare `levelSwitch\0`
type-class tag with no prefixed entity name — a single type-
instance driven entirely by `scene_state_tick` (VA `0x00055AB0`).

---

## 3. Randomizable zones — per-level breakdown

50 portals across 22 levels.  All 22 source levels appear in
`LEVEL_PATHS` inside
[`azurik_mod/randomizer/shufflers.py`](../azurik_mod/randomizer/shufflers.py).

| Source level | # portals | Destinations (spots) |
| ------------ | --------- | --------------------- |
| `town`       | 5         | life (Town_L1), e2 (Town_E2), f1 (Town_F1), d1 (Town_D1), w1 (Town_W1) |
| `life`       | 1         | town (life_disk_loc, cutscene return) |
| `a1`         | 2         | a6 (A1_A6), e6 (A1_E6) |
| `a3`         | 1         | a5 (A3_A5) |
| `a5`         | 1         | w1 (A5_W1) |
| `a6`         | 2         | a1 (A6_A1), town (air_disk_loc) |
| `airship`    | 1         | a3 (W1_A3) — **one-way cutscene, excluded** |
| `f1`         | 4         | f7 (F1_F7 — **cut level, excluded**), f6 (F1_F6), town (F1_Town), f3 (F1_F3) |
| `f2`         | 2         | e5 (F2_E5), f4 (F2_F4b) |
| `f3`         | 2         | f1 (F3_F1), w4 (F3_W4) |
| `f4`         | 1         | f2 (F4_F2) |
| `f6`         | 2         | f1 (F6_F1), town (fire_disk_loc) |
| `w1`         | 6         | w2 (W1_W2), w3 (W1_W3b), w4 (W1_W4), town (W1_Town_Waterfall), airship (**one-way cutscene, excluded**), a3 (W1_A3) |
| `w2`         | 1         | w1 (W2_W1a) |
| `w3`         | 2         | w1 (W3_W1a), town (water_disk_loc) |
| `w4`         | 2         | f3 (W4_F3), w1 (W4_W1) |
| `e2`         | 6         | town (E2_Town), e6 (E2_E6), e5 (E2_E5), e7 (E2_E7), w2 (E2_W2), e2 (**self-loop bink `catalisks.bik`, excluded**) |
| `e5`         | 3         | e2 (E5b_E2b), town (earth_disk_loc), f2 (E5_F2) |
| `e6`         | 2         | e2 (E6_E2b), a1 (E6_A1) |
| `e7`         | 1         | e2 (E7_E2) |
| `d1`         | 2         | town (D1_Town), d2 (D1_D2) |
| `d2`         | 1         | d1 (D2_D1) |

**Exclusions derived from this set (`EXCLUDE_TRANSITIONS`):**

```
{ ("f1", "f7"), ("w1", "airship"), ("airship", "a3"), ("e2", "e2") }
```

These four pairs are auto-derived from
`loading_zones.derive_exclude_transitions(cut_levels=KNOWN_CUT_LEVELS)`.
Adding a new cut level to `KNOWN_CUT_LEVELS` automatically flows
through into the randomizer's exclusion set — no manual edits
required.

---

## 4. Non-randomizable side-file zones

### 4a. `selector.xbr` — 23 cheat-menu targets

The level-select bucket at file offset `0x286604..0x2867a4` in
vanilla USA advertises 23 slots.  All 23 are catalogued in
`_SELECTOR_ZONES`:

`fire/f1`, `fire/f2`, `fire/f3`, `fire/f4`, `fire/f6`,
`air/a1`, `air/a3`, `air/a5`, `air/a6`,
`life`,
`death/d1`, `death/d2`,
`earth/e2`, **`earth/e4`** (cut — selector still advertises it),
`earth/e5`, `earth/e6`, `earth/e7`,
`water/w1`, `water/w2`, `water/w3`, `water/w4`,
`town`,
**`selector`** (self-re-entry, plays prophecy intro).

`earth/e4` is a dangling reference — no `e4.xbr` ships with the
game.  The `levels/selector` entry re-enters `selector.xbr` itself
after playing the prophecy intro movie; this is a deliberate cheat-
menu feature (letting the dev replay the intro).  Both are
surfaced so downstream integrity tools (the orphan-XBR test, the
asset-dependency walker) can flag them explicitly.

The selector also contains a second, separate movie-cutscene
playback bucket used by the dev menu to replay major story
cutscenes.  That bucket doesn't call `load_level_from_path` — it
feeds directly into the bink player — so it doesn't count as a
loading zone.

### 4b. `diskreplace_*` cutscene tails — 3 zones

| Source XBR             | Destination  | Spot             | Role                                      |
| ---------------------- | ------------ | ---------------- | ----------------------------------------- |
| `diskreplace_earth`    | `town`       | earth_disc_loc   | earth-disk placement cutscene return      |
| `diskreplace_water`    | `town`       | water_disc_loc   | water-disk placement cutscene return      |
| `wirlpoolfixed`        | `town`       | water_disc_loc   | whirlpool-fixed cutscene return (post-water-disk) |

All three point the player back to `town` when the cutscene ends.
The randomizer doesn't load these XBRs so there's nothing to
shuffle — but the catalog tracks them so future tools (cutscene
reshuffler, disk-event randomizer) have a typed anchor.

---

## 5. Implicit zones — 18 contextually-resolved `levelSwitch` entities

These entities live in a level's `sdsr` / entity bucket but carry
**no** `levels/...` destination string.  The engine's scene state
machine (`scene_state_tick`, VA `0x00055AB0`) decides where to go
based on contextual flags — parent-level name, active cutscene
flag, end-of-tutorial flag, etc.

See the table in section 2 for the full enumeration.  Grouped by
role:

- **Boss-trigger / disk-placement** (5) — `a6 Killed`,
  `a6 PlaceAirDisk`, `airship EndFight`, `w1 AirshipFight`,
  `w1 BossDefeated`.  Fire on combat-state changes; engine picks
  the bink chain to play based on which disk is being collected.
- **Disk-replacement markers** (2) — `e5 DiskReplaced`,
  `diskreplace_earth AirShipLanding`.  Markers attached to the
  cutscene timeline; engine routes the return via the sibling
  explicit `*BacktoTown*` portal.
- **Cutscene triggers** (3) — `d1 D2_movie`, `life Movie`,
  `f6 BackToTown`.  Play a bink sequence then load the sibling
  explicit portal.
- **Tutorial** (2) — `training_room EndTutorial` (+ anonymous
  intro bink chain).  Engine falls through to the hardcoded XBE
  zone `levels/water/w1` with spot `Town_W1_Movie`.
- **Airship cutscene XBRs** (3 anonymous) — `airship_docking`,
  `airship_docking_water`, `airship_trans`.  Return target set by
  caller's scene state (typically A3 / W1).
- **Disk-replacement cutscene XBRs** (3 anonymous) —
  `diskreplace_air`, `diskreplace_fire`, `diskreplace_life`.

All 18 have no stable destination string, so they're unreachable
for the connection shuffler by construction.  They're enumerated
here so the implicit surface is legible.

---

## 6. Hardcoded XBE zones

Four `levels/...` path literals baked into `default.xbe` itself.
They fire from code, not from XBR data, so they bypass every
data-side editor.  Documented with Ghidra VAs so future analysis
can find the sites:

| Destination path        | Spot               | Fires from                          | Function VA  | Data VA of string |
| ----------------------- | ------------------ | ----------------------------------- | ------------ | ----------------- |
| `levels/water/w1`       | `Town_W1_Movie`    | `scene_state_tick` (tutorial end)   | `0x00055AB0` | `0x0019ECF8` |
| `levels/selector`       | —                  | `dev_menu_flag_check` (stage-3 fallback) | `0x00052F50` | `0x001A1E3C` |
| `levels/training_room`  | —                  | `dev_menu_flag_check` (stage-2)     | `0x00052F50` | `0x001A1E4C` |
| (any)                   | `return-to-shell`  | `level_teleport_helper` (shell-return flag) | `0x00052950` | — |

All four Ghidra functions are registered in
[`vanilla_symbols.py`](../azurik_mod/patching/vanilla_symbols.py)
and declared in
[`shims/include/azurik_vanilla.h`](../shims/include/azurik_vanilla.h).
All four have been renamed in the live Ghidra project and carry
plate comments pointing back to this audit.

### 6a. `level_teleport_helper` calling-convention quirk

Discovered during the 2026 deep-pass: `level_teleport_helper`
(VA `0x00052950`) is declared with the `cdecl` ABI in
`vanilla_symbols.py` and `azurik_vanilla.h`, but its prologue
doesn't actually pop its arguments from the stack — it implicitly
consumes parameters from the `EDI` and `EBX` registers that the
caller is expected to pre-populate:

- `EDI` → pointer to scene state (`gScene`)
- `EBX` → destination level-path C string (or `0` for shell-return)

This isn't a real `cdecl` mismatch per se (the callee doesn't
clean up the stack regardless of convention), but shim authors
calling `level_teleport_helper` directly need to set up the
registers before the call.  The cleanest workaround is to call
`load_level_from_path` instead — it's a real cdecl function and
`level_teleport_helper`'s only interesting work is the
`return-to-shell` branch, which can be replicated by passing
`NULL` and `"return-to-shell"` to `load_level_from_path`.

Logged as a TODO in both `vanilla_symbols.py` and
`azurik_vanilla.h` so future shim reviewers can't accidentally
wire it up with a cdecl thunk.

---

## 7. Ending-cutscene zones — the Spideyzar credits chain

The `EndGame_levelSwitch` entity in `d1.xbr` is the terminal
loading zone of the game.  Its stored data is a semicolon-delimited
`bink:` chain of seven movies:

```
bink:spideyzardeath.bik;disksrestored.bik;newdeath.bik;credits.bik;credits2.bik;credits3.bik;credits4.bik
```

followed by the spot literal `return-to-shell`.

`scene_state_tick` plays the movie chain one reel at a time, then
calls `load_level_from_path` with a NULL level path and spot
`return-to-shell`.  That spot is the same sentinel
`level_teleport_helper` (VA `0x00052950`) uses for debug-console
exit — both funnel into the shell-return branch, which tears down
the game state and drops the player back to the Xbox shell.

Catalogued as the single entry in `_ENDING_ZONES`.  Not
randomizable (destination is the shell, not a level).

---

## 8. Ghidra sync

All level-loading Ghidra functions have been renamed and
commented during this audit:

| Old name      | New name                 | VA         |
| ------------- | ------------------------ | ---------- |
| `FUN_00053750` | `load_level_from_path`   | `0x00053750` |
| `FUN_00055AB0` | `scene_state_tick`       | `0x00055AB0` |
| `FUN_00052950` | `level_teleport_helper`  | `0x00052950` |
| `dev_menu_flag_check` (already named) | — | `0x00052F50` |

`docs/ghidra_snapshot.json` has been regenerated to capture the
new names, and `azurik-mod ghidra-sync` confirms all four are
reflected in both the snapshot and the live :8193 Ghidra
instance.  `tests/test_vanilla_thunks.py` (header ↔ Python
registry drift guard) passes with the three new externs.

---

## 9. Deep-pass methodology (2026 re-verification)

The re-verification ran six exhaustive phases against the shipped
USA ISO to flush out anything the initial audit might have
missed:

### Phase 1 — Enumerate every `levels/` string

Scanned `default.xbe` and all `.xbr` files in `gamedata/` for
`levels/` prefixes.  Every string was bucketed against the
catalog.  Discrepancies found:

- **selector.xbr had 23 slots, not 22.**  The 23rd is
  `levels/selector`, a self-re-entry previously missed.
  Catalog updated.
- **Asset-path noise filtered** — `index.xbr` and
  `training_room.xbr` contain `levels/…` substrings in FX asset
  paths; those don't reach `load_level_from_path` and are
  correctly excluded by `_find_level_transitions`.
- **Mid-file self-references validated** — `e2.xbr` and
  `selector.xbr` both contain self-references in the middle of
  the file (not end-of-file asset indexes).  Confirmed these
  are real portals: the `e2 -> e2` is the catalisk bink self-
  loop; the `selector -> selector` is the prophecy-intro re-
  entry.

### Phase 2 — Analyze every `load_level_from_path` caller

No direct cross-references to VA `0x00053750` appeared in the
Ghidra function-index scan (the engine calls through a function-
pointer slot), but decompilation of the three parent functions
confirmed the caller set:

1. `scene_state_tick` (VA `0x00055AB0`) — per-frame state
   machine that dispatches the movie chain → hardcoded fallback
   path.  Sources the `levels/water/w1` + `Town_W1_Movie`
   tutorial-end literal.
2. `level_teleport_helper` (VA `0x00052950`) — debug-console
   shell-return + the `return-to-shell` spot handler.
3. `dev_menu_flag_check` (VA `0x00052F50`) — dev-menu stage-2/3
   fallbacks (`levels/training_room`, `levels/selector`).

All three paths are covered by `_HARDCODED_XBE_ZONES`.  During
this phase the `level_teleport_helper` calling-convention quirk
(see §6a) was discovered and documented.

### Phase 3 — Verify every `levelSwitch` entity

Refined from naïve substring matching to null-terminated entity
scanning (`\0<Name>_levelSwitch\0` + bare `\0levelSwitch\0`).
Enumerated 18 distinct named `_levelSwitch` entities across 11
files (see §5).  Every entity was cross-referenced with its
sibling `levels/…` destination string (if any) to split
"explicit" vs "implicit" buckets.

### Phase 4 — Survey other entity types that could trigger loads

Scanned for `portal`, `teleport`, `door`, and other plausible
load-trigger entity tags.  Findings:

- `portal` entities (appear in `characters.xbr`) are visual
  effects, not level loaders.
- `teleport` entities (appear in several level XBRs) are
  intra-level fast-travel markers, not cross-level transitions.
- No other entity tag calls into `load_level_from_path`.

### Phase 5 — Save-game load path

Traced `last-save-spot` / `savegame` string references through
the XBE.  Save-game loading funnels through the already-
catalogued `scene_state_tick` + `dev_menu_flag_check` mechanisms,
using the same hardcoded path literals.  No bypass.

### Phase 6 — `bink:` chain double-check

Scanned every XBR + the XBE for `bink:` references to find any
semicolon-delimited chains that end in a level load.  Two chains
stood out:

- **Tutorial intro** (`training_room.xbr`) —
  `training.bik;possessed.bik;disksdestroyed.bik` → spot
  `start-playing` → XBE fallback `levels/water/w1`.  Already
  catalogued.
- **End-game credits** (`d1.xbr`, `EndGame_levelSwitch`) —
  seven bink reels → spot `return-to-shell` → shell.  **New**
  category, now catalogued as `_ENDING_ZONES`.

All other `bink:` references are single-movie cutscenes played
mid-level (no level-load follow-up).

---

## 10. How the audit stays accurate

Three layers of drift protection guard this audit:

1. **Shape tests** (`tests/test_loading_zones.py::CatalogShape`)
   check invariants that don't need the ISO: catalog size = 50,
   selector size = 23, every source level is in `LEVEL_PATHS`,
   `EXCLUDE_TRANSITIONS` is derived from the catalog, selector
   includes `e4` + the `levels/selector` self-slot, ending zone
   contains the Spideyzar chain, implicit zones contain the
   2026 named entities.  **11 shape tests**, always run.
2. **Catalog → ISO tests** (`CatalogVsIso`) assert every
   catalogued randomizable / cutscene-return / selector zone
   actually appears in the shipped `.xbr` with the documented
   path + spot.  Skipped automatically when no vanilla ISO is
   available; runs by default on dev workstations.
3. **ISO → catalog tests** (`IsoVsCatalog`) scan the shipped
   randomizable levels with `_find_level_transitions` and assert
   **every** portal the scanner finds is catalogued — catches
   drift in the opposite direction (catalog missed a real zone).

Workflow: when changing any `levelSwitch` entry in a vanilla-
equivalent XBR or catalog entry, run
`pytest tests/test_loading_zones.py` — drift in either
direction fails loudly.

---

## 11. Known limitations

- **No scanner for bink-only zones.**  Level transitions whose
  `path` starts with `bink:` (e.g. `w1 -> airship` plays
  `airship2.bik`) are caught by the implicit-zone category; the
  scanner doesn't currently enumerate the `bink:` prefix.  This
  is fine for the randomizer — you can't shuffle a movie file —
  but a future cutscene randomizer will want to walk them.  The
  end-game credits chain (§7) is an example of a bink-only
  sequence the scanner can't follow.
- **No quest-flag gating.**  Some `levelSwitch` entities only
  fire after a quest flag is set (e.g. `W1_A3` after clearing
  the airship tutorial).  The catalog records the portal but
  doesn't encode the gate.  This is out of scope for the
  loading-zone layer; `azurik_mod/randomizer/quest_state.py`
  handles gates separately.
- **`level_teleport_helper` ABI.**  Declared `cdecl` but
  actually consumes parameters from `EDI` + `EBX` registers.
  Shim authors should call `load_level_from_path` directly
  instead, or set up the registers explicitly.  See §6a.
- **`xbe_tools/mock_ghidra.py` isn't loaded with the new names
  yet** — it's a self-contained fixture for unit tests that
  doesn't pull from the snapshot.  If any test references
  `FUN_00053750` etc. by old name, it should be updated.
