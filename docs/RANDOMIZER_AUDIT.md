# Randomizer audit — findings + extension roadmap

> **Status**: The randomizer works, produces patched ISOs, and
> passes basic solvability checks in most cases — but several
> correctness issues, foot-guns, and architectural smells were
> surfaced in a deep audit.  Two critical bugs were fixed; the rest
> are documented here as a roadmap for the planned extension work.
>
> **Scope**: `azurik_mod/randomizer/` — `commands.py`, `shufflers.py`,
> `solver.py`, plus the standalone `level_editor.py` /
> `parse_level_toc.py`.
>
> **Audience**: the next person (or AI agent) extending the
> randomizer.  Each finding has a proposed fix + rationale so you
> can prioritise without re-running the audit.

---

## Table of contents

1. [TL;DR](#tldr)
2. [Architecture overview](#architecture-overview)
3. [Fixed in this round](#fixed-in-this-round)
4. [Known bugs (NOT fixed — prioritised)](#known-bugs-not-fixed--prioritised)
5. [Magic constants + VA fragility](#magic-constants--va-fragility)
6. [Solver coverage gaps](#solver-coverage-gaps)
7. [Determinism + reproducibility](#determinism--reproducibility)
8. [Extension roadmap](#extension-roadmap)

---

## TL;DR

**Works correctly**:
- Seed determinism for a FIXED set of flags.
- Major-item shuffling with `forward_fill` + `validate_placement`.
- Gem type rebalancing in the unified `cmd_randomize_full` path.
- Connection shuffle (after the CRITICAL crash fix landed last week).

**Was broken — now fixed**:
- **Power-up solvability check was vacuous**: the `a3` power
  variant's canonical name didn't match the real entity name, so
  `check_power_placement` silently returned True for every shuffle.
  Every `cmd_randomize --powers` invocation ran with an effectively
  disabled solvability check.  Fixed in `commands.py` by passing
  the real `pu["name"]` instead of `f"power_{element}"`.
- **Gem-skip identifier collisions**: when a post-shuffle gem base
  didn't fit the field length, the code `continue`'d — leaving two
  gems with the same name possible.  Now detected + warned; fixing
  the root cause (field-aware base selection) is future work.

**Known but NOT fixed** (roadmap items):
- `cmd_randomize` major-item path falls back to a blind shuffle
  when the solver is unavailable (no solvability check at all).
- `KEY_REALMS` vs `DIRECT_SEARCH_NAMES` use two separate naming
  conventions for the same keys — easy to drift.
- `BARRIER_FOURCCS_HARD` contains `b"ice\x00"` — fourth byte is
  NUL instead of space; suspicious vs engine expectations.
- `forward_fill`'s inventory is a `set`, so duplicate pickup
  names in the same node collapse.
- Level-buffer writes fire regardless of whether any modification
  happened (every loaded level is written back).
- No rollback on mid-pipeline failure.
- Monolithic `cmd_randomize_full` — hard to extend without
  editing the orchestrator itself.

See each section below for the full story.

---

## Architecture overview

The randomizer is a **five-step pipeline** inside a temp dir:

```
ISO
 │
 │  xdvdfs unpack
 ▼
 game/gamedata/<level>.xbr  +  default.xbe  +  config.xbr
 │
 │  [1] load levels into modified_levels: dict[str, bytearray]
 │  [2] major items (frags + powers + town powers)
 │       ├─ optional solver: forward_fill + validate_placement
 │       └─ apply via _write_name + _rename_all_refs
 │  [3] keys — per-realm shuffle, _write_name only
 │  [4] gems — per-level shuffle of base-type prefix
 │  [5] barriers — fixed offsets → random fourcc
 │  [6] connections — shuffle transition destinations within length group
 │  [7] XBE patches (apply_pack dispatcher)
 │  [extra] config.xbr patches from --config-mod
 ▼
 write modified_levels → xdvdfs pack → patched ISO
```

**RNG model**: one `master_rng` seeded from the user's seed; each
step spawns its own `random.Random(master_rng.randint(0, 2**31))`
sub-RNG.  That gives per-step determinism AND prevents one step's
entropy consumption from bleeding into another — BUT changing
which steps run (via `--no-major` etc.) changes master_rng's
consumption order, so **seed reproducibility is NOT stable across
different CLI flag sets**.

**Solver model**: reachability graph in
`azurik_mod/randomizer/logic_db.json` — nodes carry `pickups`
(item names) and `requirements` (set of item names needed to enter).
`Solver.forward_fill` places progression items into reachable
nodes in passes; `Solver.solve` runs a forward reachability BFS to
verify a placement is winnable.

---

## Fixed in this round

### 1. `check_power_placement` was vacuous (CRITICAL)

**Symptom**: every call to `solver.check_power_placement(power_mapping)`
returned True, regardless of whether the placement was actually
solvable.

**Root cause** (`azurik_mod/randomizer/commands.py:583`):

```python
# BEFORE (vacuously-True solvability check):
orig_canonical = f"power_{pu['element']}"           # always "power_water"
power_mapping.append((pu["level"], orig_canonical, new_canonical))
```

The problem: some powers in `logic_db.json` use a suffixed name
(`power_water_a3` — the A3-level water power, distinct from
`power_water`).  When `build_placement_from_shuffle` walks the
node's `vanilla` pickup list looking for `"power_water"`, it
doesn't find it (the list contains `"power_water_a3"`) and returns
an empty placement dict — then `solve()` falls back to the vanilla
game state, which is obviously solvable, and reports True.

**Proof**:

```python
# With the buggy canonical name:
s.solve(s.build_placement_from_shuffle(
    power_shuffle=[("a3", "power_water", "power_life")]))
#   returns (True, ...) — LIE

# With the real entity name:
s.solve(s.build_placement_from_shuffle(
    power_shuffle=[("a3", "power_water_a3", "power_life")]))
#   returns (False, ...) — correct, moving a3's power to life softlocks
```

**Fix**: use `pu["name"]` (the real entity name) as `orig_real`.
The new name can stay canonical because `trial_elements` is a
permutation of element keywords, not real entity names.

---

### 2. Gem-skip identifier collisions (HIGH)

**Symptom** (latent — user-visible only on specific seeds): a
level could end up with two entities sharing the same name after
gem shuffle.

**Root cause** (`commands.py` in `cmd_randomize` and
`cmd_randomize_gems`):

```python
# BEFORE:
for g, new_base in zip(gems, base_types):
    new_name = new_base + g["gem_suffix"]
    if len(new_name) > g["name_len"]:
        continue                # <<< skips THIS gem only
    ...write new_name at g's offset...
```

Imagine `gems = [red, blue]` and the shuffled `base_types =
[obsidian, red]`:
- `(red, obsidian)` → `"obsidian_gem"` too long → `continue`.
  Red gem keeps its original name `"red_gem"`.
- `(blue, red)` → `"red_gem"` → written over blue gem's slot.

Result: **two `red_gem` entities**.

**Fix applied**: two-pass — plan every write, then detect
duplicates + emit a warning before committing.  The underlying
behaviour (leave-too-long-at-original-name) is preserved; the
warning is loud enough that users see the issue instead of
shipping a broken ISO.

**Still-to-do** (not in this round): re-shuffle when a collision
would occur, or pre-filter `base_types` to only those that fit
each slot.  Tracked as
[ROADMAP-G1](#g1-gem-size-aware-shuffle).

---

## Known bugs (NOT fixed — prioritised)

Every finding here includes a file:line reference, a severity
tier, and a concrete suggested fix.  Tests pin the current
behaviour so they can be addressed surgically.

### R1 — Major-item path has no-solver fallback (`commands.py` ~795)

When `--no-solver` is passed (or the solver module fails to
import) the major-item shuffle runs **without any solvability
check** and writes whatever the RNG produces.  Combined with
R2 below, this can yield silently-unsolvable builds.

**Severity**: HIGH
**Fix sketch**: at minimum print a prominent warning; better,
refuse to proceed without `--force`.

---

### R2 — Keys use `_write_name` only; no `_rename_all_refs` (`commands.py` ~942)

The major-item shuffle uses `_rename_all_refs` to update every
place the old name is referenced.  The key shuffle only updates
the primary name slot.  If the engine duplicates key names in
secondary string blobs (NDBG, nav hints, etc.) the shuffle
produces orphan references.

**Severity**: HIGH (untested — may or may not produce visible
bugs; depends on whether the engine has such refs).
**Fix sketch**: use `_rename_all_refs` + run a regression against
a known ISO to confirm the rename propagates cleanly.

---

### R3 — `BARRIER_FOURCCS_HARD` entry `b"ice\x00"` (`shufflers.py:449`)

The `fire / water / air / earth` entries end in `b" "` (space),
but `ice` uses `b"\x00"` (NUL).  Element names in Azurik's binary
resource tables are typically space-padded four-byte tags; the
NUL form is suspicious and may produce an unreadable barrier type
at runtime.

**Severity**: MEDIUM
**Fix sketch**: verify in Ghidra + in-game; change to `b"ice "`
if it's wrong.  Low-urgency because the default randomizer uses
`BARRIER_FOURCCS` (without HARD) — the hard variant is only
activated by the GUI's unreachable `hard_barriers=True` path
(see R7).

---

### R4 — `_get_reachable_state` uses a set for inventory (`solver.py` ~310)

Duplicate pickups in the same node collapse to one entry.
`logic_db.json` seems to use distinct names per slot today, but
adding an entry like `"power_air"` twice in one node would silently
undercount.

**Severity**: MEDIUM
**Fix sketch**: use a `Counter` or list so duplicates aren't
lost; cross-reference with `solve()`'s indexed `pickup_key`.

---

### R5 — `forward_fill` deadlock path silently fills (`solver.py` ~505)

When `forward_fill` can't reach any remaining slot with the
remaining items, it just assigns them arbitrarily to
whatever-remains.  The caller is SUPPOSED to `solve()` the result
and reject, but if a future caller skips validation they'll see a
"valid" placement that's genuinely unsolvable.

**Severity**: MEDIUM
**Fix sketch**: raise / return a `deadlocked=True` flag so callers
can't accidentally trust the output.

---

### R6 — Level buffers are always rewritten (`commands.py` ~1251)

Every level that was loaded into `modified_levels` is written back
unconditionally at the end of the pipeline — even if no step
touched it.  For levels where no step made changes this is wasted
I/O; it could also mask subtle byte-level drift from Python
round-tripping.

**Severity**: LOW
**Fix sketch**: attach a dirty flag per level buffer; only write
if at least one step marked it dirty.

---

### R7 — GUI doesn't expose `hard_barriers` (`gui/backend.py:283`)

`hard_barriers=False` is hardcoded in the argparse namespace the
GUI builds.  The CLI supports `--hard-barriers`; the GUI doesn't.
Either wire it up or delete the unused CLI flag.

**Severity**: LOW

---

### R8 — No rollback on mid-pipeline failure (`commands.py` various)

If connection-shuffle crashes halfway through its level loop, the
already-modified levels remain in `modified_levels` and will be
written to the temp dir + packed into the output ISO (if later
steps succeed).  Users would see a half-randomized game.

**Severity**: LOW (Python exceptions short-circuit the whole
command today so partial writes rarely land; but the architectural
shape is brittle).
**Fix sketch**: wrap the whole pipeline in a savepoint —
modifications stay in a dict that only gets committed atomically
at the end.  Any step raising drops everything.

---

### R9 — `cmd_randomize_full` has no `--levels` flag (`cli.py` randomize-full)

`cmd_randomize` and `cmd_randomize_gems` both accept `--levels
w1,w2,...` to subset; `cmd_randomize_full` always operates on
every level in `LEVEL_XBRS`.  For testing / incremental-rollout
workflows this is a friction point.

**Severity**: LOW
**Fix sketch**: thread `--levels` through from the CLI parser,
filter `LEVEL_XBRS` at the start of the pipeline.

---

## Magic constants + VA fragility

The randomizer contains several tables of hardcoded file offsets /
string constants:

| Constant | Source | Fragility |
|---|---|---|
| `LEVEL_XBRS` | list in `shufflers.py:363` | Stable across builds; would need update for DLC-style level additions. |
| `KEY_REALMS` | `shufflers.py:416` | Stable; but overlaps with `DIRECT_SEARCH_NAMES` (see below). |
| `EXCLUDE_TRANSITIONS` | `shufflers.py:509` | Heuristic — `_find_level_transitions` scans for the substring `b"levels/"`; false positives can sneak in. |
| `BARRIER_OFFSETS` | `shufflers.py:428` | **Hard file offsets** per level.  Breaks if any tool touches the levels (config-editor, manual hex edit). |
| `OBSIDIAN_LOCK_*` | `shufflers.py:454` | Same — hard offsets in `town.xbr`. |
| `DIRECT_SEARCH_NAMES` | `shufflers.py:686` | Parallel naming scheme (`key_air1` vs `KEY_REALMS`'s `key_fire1`).  Manual maintenance. |

**Recommended long-term fix**: generate these tables from a
canonical manifest (YAML / JSON) that's regenerated when the
vanilla ISO changes.  Keeps shufflers.py free of magic numbers.

See also: `docs/LEARNINGS.md` for prior RE notes that would feed
such a manifest.

---

## Solver coverage gaps

**`logic_db.json` is the single source of truth** for
reachability — it's hand-curated, not derived from the binary.
Gaps fall into three categories:

1. **Nodes missing entirely**: if the randomizer adds a new pickup
   that lives in a node `logic_db.json` doesn't model, the solver
   will place items into unreachable slots and `validate_placement`
   has no way to catch it.

2. **Requirements wrong**: a node might require `power_fire` to
   enter but the DB lists `power_fire_a3`, or vice versa.  The
   power-placement bug we just fixed was exactly this shape.

3. **Unstable/in-progress nodes**: the DB contains
   `status: "unstable"` / `"temple_items"` fields that are
   inconsistently used (`solver.py` `_get_reachable_state` filters
   by `status == "stable"`).

**Recommended**: add a CI check that walks the extracted game tree
and cross-references every entity name against `logic_db.json`.
Any entity in-game but missing from the DB triggers a warning;
any DB entry whose node doesn't correspond to real game data
errors out.  Would have caught the power-naming bug on first run.

---

## Determinism + reproducibility

### What's deterministic

- Given the same seed + same CLI flags, the same ISO bytes out.
  Tested via `test_determinism.py` (if / when written).
- Sub-RNGs are pulled from `master_rng` in a fixed order, so
  changing the SAME step's RNG consumption doesn't bleed into
  others.

### What's NOT deterministic across flags

- Changing `--no-major` / `--no-keys` / etc. changes which
  `master_rng.randint()` calls occur → changes all subsequent
  sub-seeds → different-looking randomization for the SAME user-
  visible seed.  This is subtle: two users running seed 42 with
  different flag sets will get different gem shuffles.

### What's explicitly non-deterministic

- GUI log filenames use `datetime.now()` — affects the log file
  name only, not the ISO bytes.
- Python `set` iteration order is hash-dependent; any ordering
  based on `set` iteration is non-deterministic across Python
  reboots.  Audited: the randomizer uses `sorted(...)` in all
  consumer paths of sets.  Safe today.

### Recommendation

Document the flag-dependence explicitly in CLI help so users don't
expect `--seed 42` to mean one specific outcome regardless of
other flags.  Or, alternatively, draw a FIXED number of sub-seeds
from `master_rng` at the top of the pipeline and ignore the ones
that aren't needed — stable across flags at the cost of wasted
entropy.

---

## Extension roadmap

Prioritised refactoring targets the user should tackle BEFORE
adding major new randomizer categories.  Each one pays dividends
for every future feature.

### P1 — Composable pipeline (HIGH VALUE, ~1 week)

Replace the monolithic `cmd_randomize_full` with a list of
pipeline passes, each implementing `(state) -> state`:

```python
class Pass(Protocol):
    name: str
    def run(self, state: PipelineState, rng: Random) -> None: ...
    def dry_run(self, state: PipelineState) -> list[Issue]: ...

PIPELINE = [
    LoadLevelsPass(),
    MajorItemsPass(),
    KeysPass(),
    GemsPass(),
    BarriersPass(),
    ConnectionsPass(),
    XbePatchesPass(),
    ConfigEditsPass(),
    WriteLevelsPass(),
    PackIsoPass(),
]
```

Benefits:
- Each pass has a clean interface — plug in a new pass without
  touching the orchestrator.
- `dry_run` lets tests verify solvability / consistency without
  actually writing bytes.
- Rollback for free: if `pass.run` raises, drop the state snapshot.

---

### P2 — Declarative game manifest (HIGH VALUE, ~3 days)

Move every hardcoded table (`LEVEL_XBRS`, `KEY_REALMS`,
`BARRIER_OFFSETS`, etc.) into a YAML or JSON manifest:

```yaml
# azurik_mod/randomizer/game_manifest.yaml
version: 1.0
vanilla_iso_sha256: "abc...def"
levels:
  w1:
    path: "levels/water/w1.xbr"
    transitions:
      - { src_va: 0x1234, dst: "Water2" }
    barriers:
      - { offset: 0x00A18CA0, type: "fire" }
gems:
  bases: [red, blue, green, white, black, obsidian]
  ...
```

Benefits:
- Single source of truth; editor tooling can generate the manifest
  from an extracted ISO so it's always in sync.
- Trivial to diff across game versions / regions / modded variants.
- Schema-validates so typos fail loudly.

---

### P3 — Solver / game-data sync tool (MEDIUM VALUE)

Write a script that:
1. Unpacks a vanilla ISO.
2. Walks `gamedata/levels/*.xbr` + `gamedata/config.xbr`.
3. Extracts every pickup entity and its level.
4. Cross-references against `logic_db.json`.

Reports: missing DB entries, stale DB entries, mismatched names
(power_fire vs power_fire_a3).  Run as a CI check on every PR
that touches `logic_db.json` or the vanilla ISO fixture.

Would have caught the `power_water_a3` bug at PR time.

---

### P4 — Dirty tracking on level buffers (MEDIUM VALUE, ~1 hour)

Replace `modified_levels: dict[str, bytearray]` with a thin
wrapper class:

```python
@dataclass
class LevelBuffer:
    name: str
    data: bytearray
    dirty: bool = False

    def mark_dirty(self, reason: str) -> None: ...
    def write_if_dirty(self, path: Path) -> bool: ...
```

Avoids gratuitous I/O + gives a place to assert post-conditions
("every dirty level buffer passes the xobx magic check post-write").

---

### P5 — Extract more shuffler logic into testable modules (ONGOING)

`shufflers.py` is a 900-line file mixing:
- Constants (tables)
- Entity finders (byte scanners)
- Name rewriters
- Level-patch application

Split into:
- `manifest.py` (from P2)
- `entity_scan.py` (pure scanners, testable against fixture level bytes)
- `rename.py` (`_write_name` / `_rename_all_refs`)
- `apply.py` (per-pass transformations)

Each module gets its own test file.

---

## References

- [`azurik_mod/randomizer/commands.py`](../azurik_mod/randomizer/commands.py) — CLI handlers + `cmd_randomize_full`
- [`azurik_mod/randomizer/shufflers.py`](../azurik_mod/randomizer/shufflers.py) — shuffling logic + magic tables
- [`azurik_mod/randomizer/solver.py`](../azurik_mod/randomizer/solver.py) — reachability + validation
- [`azurik_mod/randomizer/logic_db.json`](../azurik_mod/randomizer/logic_db.json) — solver reachability graph
- [`tests/test_audit_regressions.py`](../tests/test_audit_regressions.py) — connection-shuffler import crash
- [`tests/test_randomizer_audit.py`](../tests/test_randomizer_audit.py) — power-name + gem-collision fixes
