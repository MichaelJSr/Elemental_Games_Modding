# Learnings — Azurik reverse engineering knowledge base

Running accumulation of non-obvious findings from reverse-engineering
Azurik.  Check here before diving into a Ghidra session — the answer
may already be written down.

Organisation: one section per system.  Each finding cites the
Ghidra function it came from so you can re-verify.

---

## Player movement

### The shared `3.0` run-multiplier (VA 0x001A25BC) is read from 45 sites

The `.rdata` float at VA `0x001A25BC` holds the value `3.0`.  It's
the FMUL factor the running-flag branch of `FUN_00084940` uses to
boost the player's magnitude.  **But**: the same constant is read
from 44 other sites across the XBE (audio mixing, collision, AI,
unrelated physics) — touching the shared constant would affect
every reader.

✅ **Learning**: when patching a constant you think is player-
specific, always grep for every xref.  If the constant is shared,
use the C1-style redirect (rewrite the instruction at the call site
to reference a freshly-injected per-game constant) instead of
mutating the shared value.

Reference: `azurik_mod/patches/player_physics/__init__.py::apply_player_speed`.

### `CritterData.walkSpeed` and `runSpeed` are dead data

The `critters_critter_data` keyed-table has `walkSpeed` and
`runSpeed` columns.  Both **look** like they drive player movement,
but `FUN_00049480` (which populates `CritterData` from config) never
reads those columns — they're not in the column-name table at all.
The runtime `base_speed` the movement code reads from
`CritterData[+0x40]` is defaulted to `1.0` at init time and never
changed by config.

✅ **Learning**: in keyed-table-backed config, a column existing
in the `.xbr` isn't enough — verify the populating function actually
references it via `FUN_000D1420("<key>")`.  Dead columns are common.

Reference: decomp of `FUN_00049480` around the `piVar9[0xE]` /
`piVar9[0x10]` writes (they come from animation-speed keys, not
walk/run speed).

### FUN_00085F50 is the walking state; FUN_00088490 / FUN_00087F80 are climbing

Four functions call into `FUN_00085F50` — the velocity-from-magnitude
computation.  Two are quickly identifiable by their embedded sound-
path strings:

| Function       | Identifying strings                          | Purpose          |
|----------------|----------------------------------------------|------------------|
| FUN_00085F50   | `fx/sound/player/walkl`, `fx/sound/player/walkr` | Walking / running |
| FUN_00087F80   | `fx/sound/player/climb[u/d/l/r]`             | Climbing         |
| FUN_00088490   | `PTR_s_m_grab_001a9c60`                       | Grabbing ledges  |
| FUN_0008CCC0   | State-machine dispatch (calls via case 0 / 6) | Per-frame switcher |

✅ **Learning**: embedded sound-path or animation-ref strings are
the fastest way to label a movement-state function.

### Vanilla base-speed value + independence math (April 2026)

`CritterData.run_speed` at offset `+0x40` feeds BOTH walking and
the WHITE-button boost in the player tick.  Earlier docs claimed
"always 1.0" because `critters_critter_data` has no `runSpeed`
row for `garret4` — that was wrong: the field inherits from the
struct's default initialiser, and the vanilla runtime value is
**`7.0`**.

How it was pinned:

1. lldb breakpoint at VA `0x00085F65` (the `FLD [EAX+0x40]` right
   after `MOV EAX, [EBP+0x34]` in `FUN_00085F50`).
2. `p/f *(float *)($eax + 0x40)` returned `7.0`.

The movement formula simplifies to:

```
walking  = 7.0 × raw_stick
rolling  = 7.0 × raw_stick × 3.0   # 3.0 comes from FMUL [0x001A25BC]
                                   # in FUN_00084940 (gated on
                                   # PlayerInputState.flags & 0x40,
                                   # set by the WHITE/BACK button)
```

**Independence math** — the `player_physics` pack exposes two
sliders (`walk_scale`, `roll_scale`) that must each scale only
their own baseline.  Since a single field feeds both paths, we
solve for the pair of injected constants:

```
inject_base      = 7 × walk_scale
inject_roll_mult = 3 × roll_scale / walk_scale
```

so the engine produces:

```
walking = inject_base × raw_stick
        = 7 × walk_scale × raw_stick
        = walk_scale × vanilla_walking            ← indep
rolling = inject_base × inject_roll_mult × raw_stick
        = 7 × walk_scale × 3 × roll_scale/walk_scale × raw_stick
        = 21 × roll_scale × raw_stick
        = roll_scale × vanilla_rolling            ← indep
```

The `walk_scale` cancels cleanly in the rolling path, so each
slider affects only its own baseline.  Regression coverage:
`tests/test_player_speed.py::IndependenceSemantics` sweeps 6
slider combinations + verifies walking/rolling speeds stay pinned
to their expected vanilla multiples for every case.

⚠️ **Trap**: the pre-April-2026 code injected the literal value
directly (not `vanilla × slider`).  That made `walk_scale=3` drop
the player to `3.0/7.0 ≈ 43%` of vanilla speed and silently
coupled the two sliders (any non-default triggered both patches,
with the walk-site rewriting the base to a raw literal).  Future
patches that layer on top of `CritterData.run_speed` must either
preserve vanilla at scale=1 or clearly document the non-identity
semantics.

### Wing flap = FUN_00089300, NOT FUN_00089480 (April 2026)

The mid-air "wing flap" (Air-power double-jump) is its own
function: ``FUN_00089300``.  Entry conditions:

1. Input-flag bit 0x04 of ``PlayerInputState.flags`` is set
   (the JUMP button was pressed THIS frame, edge-triggered).
2. ``armor.flap_count`` (``[EDX+0x38]``) is non-zero.
3. ``entity.flap_counter`` (``[ESI+0xD8]``) is less than
   ``armor.flap_count``.

When all three hold, the function:
- Calls ``FUN_00083F90(&entity[0x140])`` which WRITES
  ``[ESI+0x140] = 12.0`` (air control) and
  ``[ESI+0x144] = 1.2`` (flap height) when the air-power
  level is 1-3, else ``9.0`` / ``1.1``.
- Consumes fuel via ``FUN_000842D0(this, 1.0)``.
- Increments ``entity.flap_counter``.
- Computes ``v0 = sqrt(2 × 9.8 × flap_height)`` at VA 0x893AE.
- Scales by ~1.5 at VA 0x893EB (shared `FMUL [0x001A26C4]`).
- Adds to ``entity.z_velocity`` and caps at the v0 ceiling.
- Sets state = 2 (airborne) and plays "fx/sound/player/jump".

**Pre-April-2026 patch target (VA 0x896EA, `FADD [0x001A25C0]`)
was inside FUN_00089480 (airborne per-frame physics), NOT
FUN_00089300.**  That FADD runs only when input flags have BOTH
bit 0x40 (WHITE/roll) AND bit 0x04 (JUMP) set, so our
user-facing "flap_height" slider appeared to do nothing —
typical xemu input configs don't route WHITE to bit 0x40.

The v2 patch at VA 0x893AE mirrors the initial-jump pattern:
rewrite the gravity FLD to reference an injected
``9.8 × flap_scale²`` so the sqrt yields ``flap_scale × v0``.

### Wing-flap v0 cap — vanilla anti-infinite-altitude design

Vanilla `wing_flap` (FUN_00089300) at VAs 0x89368-0x89393:

```
fVar1 = peak_z + flap_height - current_z
if fVar1 <= 0: fVar1 = 0
fVar2 = min(fVar1, flap_height)
v0 = sqrt(2 × g × fVar2)
```

``peak_z`` is latched to ``z_at_jump + flap_height`` in
`player_jump_init` at VA 0x8915A and is **never refreshed** by
`player_airborne_tick`.  Consequence: **subsequent flaps
cannot exceed `initial_flap_z + flap_height`**.  Below-peak
flaps are a supported recovery mechanic — the 0.5× halving at
`flap_below_peak_scale` is a clean byte-patch of a real
vanilla constant.  At-or-above-peak flaps are **intentional
anti-infinite-altitude design**: once the player has recovered
their altitude budget, further flaps produce zero v0.

**Three separate patch attempts have been abandoned** (rounds
7–10); no above-peak v0 injection currently ships:

- **v1** NOPed `FSUB [EBX+0x5C]` at 0x89381 so `fVar1 = peak+fh`
  (always large).  Landed cleanly but `fVar1 > 6m` tripped the
  halving path at 0x893C0 AND `consume_fuel(100)` at 0x893D4
  — fuel drained to zero within 1-2 flaps.
- **v2** rewrote `FLD ST(1)` → `FLD ST(0)` at 0x8939F so the
  `min(fVar1, fh)` compare became `min(fh, fh)`.  Byte-level
  verification showed the patch landed; in-game testing
  reported no observable effect.  The engine re-derives the
  cap downstream.
- **v3** (round 8) shipped a hand-assembled shim at 0x89409
  (the final `FSTP [ESI+0x2C]`) that enforced
  `max(vanilla_v0, sqrt(2g*fh)*scale)`.  Also ineffective
  in-game — `player_airborne_tick` or subsequent physics
  integration re-clamps vz before it manifests as motion.

Round 10 deleted the `flap_at_peak` pack and made
`apply_flap_at_peak` a no-op.  What works instead:

- Scaling `flap_height_scale` (the gravity FLD inside
  `wing_flap` at 0x893AE) affects EVERY flap's v0 because
  `wing_flap` runs for first + subsequent flaps alike.  But
  when `fVar2 = 0` (the ceiling case), `v0 = scale * 0 = 0`.
- Scaling `flap_below_peak_scale` (the halving FMUL at 0x893DD)
  only fires for flaps taken >6 m below peak.
- **Round 11: `wing_flap_ceiling_scale`** — scales the peak_z
  latch at the source instead of fighting downstream state.
  See "Wing-flap ceiling — shim at jump_init" below.

### Wing-flap ceiling — shim at jump_init (round 11)

The `peak_z` latch in `player_jump_init` is the **source of
truth** for the wing-flap altitude cap.  It has exactly one
writer: the `FSTP [ESI+0x164]` at VA 0x8915A, fed by the
`FADD [ESI+0x144]` at VA 0x89154 that adds the per-jump
`flap_height` field to `entity.z` (loaded by the `FLD [EDI+0x5C]`
at VA 0x8914C).  Nothing else in the binary writes
`[ESI+0x164]` for the player-shape state struct (verified by
scanning the full `.text` section for FSTP/FST/MOV patterns at
that offset; one incidental hit at VA 0x026615 writes a
different struct's field of the same offset, confirmed via
absence of `[ESI+0x144]` / `[ESI+0x5C]` touches in that
function — and via the Ghidra decompilation showing both uses
in isolation).

**Vanilla math** (from the `wing_flap` decompilation at VA
0x89300):

```c
fVar1 = peak_z + flap_height - current_z;   // clamped >= 0
fVar2 = min(fVar1, flap_height);
v0    = sqrt(2 * 9.8 * fVar2);
```

With the vanilla `peak_z = entity.z_at_jump + flap_height`:

- `fVar1 = entity.z_at_jump + 2·flap_height − current_z`
- Hard ceiling where `fVar1 = 0`:
  `current_z = entity.z_at_jump + 2·flap_height`

So the vanilla altitude envelope above the ground the player
jumped from is **2·flap_height**, not one — `wing_flap` adds
`flap_height` again on top of `peak_z` inside its own formula.

**Round 11 shim** at VA 0x89154 rewrites the FADD source with
a scaled flap_height.  Ceiling becomes:

```
peak_z_patched = entity.z_at_jump + K · flap_height
fVar1_patched  = (K + 1) · flap_height − (current_z − entity.z_at_jump)
ceiling_patched = entity.z_at_jump + (K + 1) · flap_height
```

- `K = 1` → `2·flap_height` (vanilla)
- `K = 2` → `3·flap_height` (≈1.5× vanilla ceiling headroom)
- `K = 5` → `6·flap_height` (≈3×)
- `K = 10` → `11·flap_height` (≈5.5×)
- `K = 20` (slider max) → `21·flap_height` (≈10.5×)

i.e. the "multiplier" applies to `peak_z`, and the observable
ceiling grows as `(K+1)/2` relative to vanilla.  Users who want
"double the ceiling" should set `K ≈ 3`, not `K = 2`.

Because there's no downstream re-derivation of `peak_z`, this
scaling propagates cleanly into every subsequent `wing_flap`
invocation: a bigger `peak_z` widens `fVar1`, `fVar2 = min(…,
flap_height)` stays positive further above ground, and the
`sqrt(2g·fVar2)` v0 formula produces a real impulse instead of
0 near-vanilla-peak.

Why rounds 7–10 all failed and this one doesn't: the earlier
shims hooked **downstream** of the peak_z computation (at the
min-cap in `wing_flap` itself, or at the final vz FSTP, or at
animation root-motion commit).  Every one of those sites had a
consumer that re-clamped or re-computed the value we'd
written.  Round 11 hooks **upstream** — we change what
"peak" *means*, not what `wing_flap` *does* with it, so every
reader downstream observes the new value with vanilla
semantics unchanged.

**Shim implementation** (15 bytes, hand-assembled via
`shim_builder`):

```asm
FLD   [ESI+0x144]     ; D9 86 44 01 00 00 — load flap_height
FMUL  [scale_va]      ; D8 0D <abs32>     — × ceiling_scale
FADDP ST1             ; DE C1             — pop, add to z (on ST0)
RET                   ; C3                — resume at 0x8915A
```

Installed at VA 0x89154 (the vanilla FADD site) as a 5-byte
`CALL rel32` + 1-byte NOP.  On shim entry ST(0) = `entity.z`
(loaded by the vanilla FLD at 0x8914C and preserved across the
CALL's return-address push).  After RET the shim returns to
the unchanged `FSTP [ESI+0x164]` at 0x8915A, which writes the
adjusted sum to `peak_z`.

The slider is orthogonal to `flap_height_scale` (per-flap v0)
and `flap_below_peak_scale` (>6m-below halving) — the three
compose cleanly.

**Design intent check**: this IS a workaround to vanilla's
intentional anti-infinite-altitude clamp.  The game caps
subsequent-flap altitude on purpose, and scaling the ceiling
past 5x or so is clearly outside the designer's intent.  The
slider defaults to 1.0 (vanilla) and is opt-in.

### Wing-flap descent-penalty fuel drain (round 11.7)

A second anti-recovery mechanic lives inside `wing_flap`
itself, independent of `peak_z`.  From the decompilation:

```c
if (6.0 < fVar1) {                 // fell > 6m below peak envelope
    consume_fuel(this, 100.0);     // drain 100 fuel
    param_1 = param_1 * 0.5;       // halve v0
}
```

The 100-fuel call is what clears the air-power gauge in a
single flap — after that, the next flap's entry
`consume_fuel(this, 1.0)` at VA 0x89354 returns 0 (refuses to
start the flap) because there's no fuel left.  Users who pair
a high `wing_flap_ceiling_scale` with deep descent see this as
"first descent flap works, second flap fails entirely".

Three sites compose the branch:

| VA | Instruction | Role |
|---|---|---|
| 0x893C0 | `FCOMP [0x001A25B8]` | `6.0 < fVar1` test (constant has 19 readers — can't overwrite) |
| 0x893CD | `PUSH 0x42C80000` | pushes 100.0f as the fuel-cost argument |
| 0x893DD | `FMUL [0x001A2510]` | ×0.5 halving (targeted by `flap_below_peak_scale`) |

The `flap_descent_fuel_cost_scale` slider rewrites the 4-byte
PUSH immediate at VA 0x893CE (bytes 1..4 after the 0x68 PUSH
opcode).  Setting to 0.0 pushes 0.0f instead of 100.0f;
`consume_fuel(this, 0.0)` is then a legal no-op that drains
nothing but still returns 1 (success) — so the flap proceeds
and the gauge stays intact.  Pair with
`flap_below_peak_scale = 2.0` if you also want to cancel the
v0 halving that runs in the same branch.

Unlike the `peak_z` latch, the 6.0 threshold constant at VA
0x001A25B8 is shared across 19 unrelated readers in the binary
(movement / physics / input / audio — not all of them obvious
consumers of a height threshold; probably a deduplicated
numeric literal in the original source).  Overwriting the
constant would corrupt all 18 unrelated sites.  The
PUSH-immediate approach is surgical to this one call site.

This patch is CODE-surgical rather than DATA-surgical, which
generalises: if you ever need to scale a numeric argument
passed to a callee, and the literal is a `PUSH imm32` sourced
directly in the caller (not an `FLD [shared_const]`), you can
rewrite the 4-byte immediate in place without any relocation
or shim machinery — see `apply_flap_descent_fuel_cost` for the
2-line apply function.

### Wing-flap ENTRY fuel drain + `consume_fuel` threshold (round 11.11)

The descent-penalty drain at 0x893CE is only the second of
**two** fuel-consuming sites in `wing_flap`.  The first fires
unconditionally on every flap:

```
00089349  PUSH 0x3F800000           ; 1.0f (the cost)
0008934E  PUSH ESI                  ; `this`
0008934F  CALL consume_fuel         ; returns 1 on success, 0 if blocked
00089354  TEST AL, AL
00089356  JZ   flap_fails           ; no fuel → flap aborts
```

The 4-byte immediate at VA `0x8934E` (bytes 1..4 after the 0x68
PUSH opcode) is the per-flap cost.  `flap_entry_fuel_cost_scale`
rewrites this from 1.0f to `scale * 1.0f`:

| Scale | Behaviour |
|---|---|
| 1.0  | Vanilla (air1 = 1 flap per gauge, air3 = 5 flaps) |
| 0.1  | ~10× more flaps per gauge |
| 0.0  | **Infinite** flaps (`consume_fuel(this, 0.0)` is legal no-op) |
| -1.0 | Gauge **refills** by 1 unit per flap (see below) |

The `consume_fuel` implementation at FUN_000842D0 decompiles to
roughly:

```c
int consume_fuel(this, float cost) {
    fuel -= cost;                  // subtract (negative → refill)
    if (cost / fuel_max > 0.???) { // some large-cost threshold
        fuel = 0;                  // clear if cost too large
        return 0;                  // refuse
    }
    if (fuel < 0) return 0;        // out of fuel
    return 1;                      // ok
}
```

Because `fuel_max` is a tiny integer per armor level (1 / 2 / 5
for air1 / air2 / air3), the `cost / fuel_max` threshold trips
the "clear to zero" branch for most *positive* descent-cost
values.  That's why `flap_descent_fuel_cost_scale` is bounded to
`[-0.05, +0.05]` with 0.001 steps — even 0.1× the vanilla 100.0
descent cost (= 10.0) divided by `fuel_max = 5` gives 2.0, which
trips the clear branch on most armor levels.  Negative values
produce a refund because the subtract flips sign.

The entry-cost site (1.0f) is much gentler on the threshold —
`1.0 / fuel_max` for all armor tiers is well below the clear
threshold — so `flap_entry_fuel_cost_scale` has a useful
`[-0.05, +0.05]` window plus values out to `±1.0` for balance
purposes.  The two sliders compose: entry-cost governs the
cheap per-flap drain, descent-cost governs the punishment for
trying to flap too far below peak_z.

### Armor config loader — engine reads the 0x3000 table, not TOC entry 1 (round 11.13)

`load_armor_properties_config` at VA 0x3C700 populates the
per-armor-slot struct array at `DAT_0038C4D4` at boot.  It
loads the asset by string name `config/armor_properties`
(string at 0x19FEC8), then reads columns `Name`, `Type`,
`Level`, `Strong vs`, `Weak vs`, `Strong protection`,
`Normal protection`, `Flaps`, `Features`, etc. via
`config_cell_value`.

**Crucial naming gotcha**: the config.xbr file has TWO
keyed tables with overlapping names:

| File offset | TOC label | Actual columns | Engine use |
|---|---|---|---|
| 0x3000 (inside TOC[0] extent 0x2000) | "armor_hit_fx" in section-name table | 15×19 — Name, Type, Level, Strong vs, Weak vs, ..., Flaps, Features | THIS is loaded as `config/armor_properties` |
| 0x5000 (TOC[1], offset 0x4000) | "armor_properties" in section-name table | 16×24 — name, Anim, Damage multiplier, Fuel multiplier, Rate, ..., Sound 4 | Probably attack-animation data |

The engine's asset-manager lookup-by-filename resolves
`config/armor_properties` to the 0x3000 table, not the TOC[1]
table that shares the string.  Our `keyed_tables.py` labels
the 0x3000 table as `armor_properties_real` to avoid the
ambiguity — the Entity Editor writes to this label, which is
correct for the engine's read path.

Flaps conversion: `config_cell_value` reads the 8-byte double
at cell+8, `FUN_000F5A40` (FPU FISTP) converts to int64, the
low 32 bits land at `puVar1[0xE]` (struct offset 0x38).  The
wing-flap runtime read at VA 0x89321 (`MOV EAX, [EDX+0x38]`)
picks up whatever we wrote.

Users can verify their edits round-trip by extracting config.xbr
from a built ISO and running
`azurik-mod dump -i built.iso -s armor_properties_real -e air_shield_3`
— the output shows the live double value as stored, not the
registry default.

### Animation root-motion vtable-commit hook (round 11.11, experimental — DEPRECATED 11.13)

Round-8 installed `root_motion_roll` / `root_motion_climb` /
`slope_slide_speed` as post-CALL shims that scaled
`param_1[0x6C..0x71]` *after* `anim_apply_translation`
returned.  Player testing consistently reported no observable
effect.  The round-11.6 forensic cleared the "slider value
never reached the pack" theory, leaving the analysis-time
hypothesis (vtable commit happens *inside* the function, so
post-CALL scaling is too late) as the remaining suspect.

`anim_apply_translation` (FUN_00042E40) ends with a vtable
dispatch:

```
00043062  MOV EAX, [EBX]                 ; vtable ptr
00043064  MOV ECX, EBX                   ; this = param_1
00043066  CALL DWORD PTR [EAX+0xC0]      ; commit deltas  ← hook
0004306C  POP EDI / ... / RET 0x10
```

By the time control returns to the caller (at e.g. 0x866D9 for
roll), the vtable commit has already consumed the deltas at
`param_1[0x6C..0x71]`.

The `animation_root_motion_scale` pack hooks the CALL itself —
the only hook site in the codebase that lands *inside* a
vanilla function instead of at its prologue or an imm32 load.
A 38-byte hand-assembled shim:

1. Saves EDX / ESI (callee-clobber).
2. Loads `LEA ESI, [EBX+0x1B0]` — the address of
   `param_1[0x6C]` (index × 4).
3. Runs a 6-iteration loop: `FLD [ESI] / FMUL [scale_va] /
   FSTP [ESI] / ADD ESI, 4 / DEC EDX / JNZ`.
4. Restores ESI / EDX.
5. Replays the vanilla `CALL [EAX+0xC0]` on EAX's behalf.
6. `RET` back to `0x4306C`.

The vtable call then commits the already-scaled deltas.
Because `anim_apply_translation` is called from ~15 sites
(walk, roll, climb, jump, flap, airborne, swim, slope-slide
plus non-player callers), the scale is **global** — there's no
per-animation gating today.  A future revision could add a
caller-set flag that the central shim consults, but the
current experiment is "does hooking the vtable call itself
change anything observable in-game?"

The pack is marked `default_on=False` and lives outside the
randomizer-QoL set; it's slider-controlled via the GUI's
`animation_root_motion_scale` knob.  If it proves effective,
the old `root_motion_roll` / `root_motion_climb` packs can
probably be retired in favour of gated versions of this
central shim.

**Update (round 11.13, DEPRECATED)**: user testing confirmed
no observable in-game effect at any scale (0.1, 0.5, 2.0,
5.0).  Either the vtable slot doesn't commit the deltas at
`param_1[0x6C..0x71]`, or a sibling anim-apply dispatcher
around 0x42E40 is the real commit site.  Pack marked
`deprecated=True`, hidden from GUI, retained as a known-good
38-byte shim template for future iteration.

### Air-control speed has TWO dominant writer sites — FUN_00083F90 (April 2026)

``entity[+0x140]`` is the airborne horizontal-control scalar
consumed every frame by ``FUN_00089480``'s horizontal physics.
Static jump-entry code (FUN_00089060 and 4 sibling paths)
writes it with `MOV [reg+0x140], 0x41100000` = 9.0.

But during normal gameplay (player has air power, or triggers
a wing flap), the DOMINANT writer is ``FUN_00083F90`` — a
per-frame re-initialiser called from both the main jump and
the wing flap.  It writes 12.0 when ``air_power_level ∈ [1, 3]``
or 9.0 otherwise via:

```asm
00083FAA  C7 01 00 00 40 41   MOV [ECX], 0x41400000    ; 12.0
00083FCC  C7 01 00 00 10 41   MOV [ECX], 0x41100000    ; 9.0
```

Pre-April-2026 the `air_control_scale` slider only patched the
5 static sites — users with air power equipped saw no effect
because FUN_00083F90 kept overwriting ``entity[+0x140]`` every
frame.  The April-2026 fix scales both `+0x140` writers
simultaneously (12.0 → 12 × scale, 9.0 → 9 × scale) so the
slider is observable regardless of which code path set the
field.

### Flap count lives in armor_properties[level][0xE]

The number of wing flaps per jump is loaded from
``config/armor_properties.tabl``'s ``"Flaps"`` column at boot
into offset ``0xE * 4 = 0x38`` of the per-armor-slot struct
at ``DAT_0038C4D4``.  The runtime slot is indexed by the
active armor type.  ``FUN_00089300`` reads it fresh at VA
0x89321 (``MOV EAX, [EDX+0x38]``), compares against
``entity.flap_counter`` (``[ESI+0xD8]``), and enforces the
per-jump budget.

**Key insight for modding:** because the read is fresh at
each flap attempt (not cached in the entity), we can hook
the 5-byte `MOV EAX, [EDX+0x38] ; TEST EAX, EAX` with a
trampoline + dispatch shim that consults
``DAT_001A7AE4`` (the current air-power level, 1/2/3/4) and
substitutes a user-chosen value.  That's how the
``wing_flap_count`` pack gives per-level flap control without
touching the .tabl config file.

### Fall damage lives in TWO paths — FUN_0008AB70 AND FUN_0008BE00

Fall damage enters via `FUN_0008C080` (player_landing), which
branches on `[entity+0x38]` (surface contact slot):

1. **With surface** → `fall_damage_dispatch` (FUN_0008AB70).
   Reads 7 cvars from `config.xbr` on first call
   ("fall min velocity", "fall height 1/2/3", "fall damage
   1/2/3"), tiers damage by fall height, calls `FUN_00044640`
   (damage apply) for each tier that fires.

2. **Without surface** → `FUN_0008BE00` (added to LEARNINGS
   late April 2026 after user-reported "light damage still
   fires").  Fires on no-floor landings (falling off map,
   water splash at low surface).  Reads the cached "fall
   height 4" cvar, computes fall magnitude from velocity and
   height drop, calls `FUN_00044640` if magnitude exceeds
   threshold.  Plays "fx/sound/player/fallingdeath" SFX and
   sets entity death flag (0x01 at offset 0x16C).

**Both paths call FUN_00044640**, so a complete "no fall
damage" patch MUST bypass both.  v1 of the pack only addressed
path 1, leaving path 2 active — hence the "instant-death
prevented but light damage still fires" user report.  v2
(late April 2026) rewrites BOTH function prologues to
`XOR AL, AL ; RET <N>`:

- `FUN_0008AB70` prologue (6 bytes, __stdcall 2 args → RET 8)
- `FUN_0008BE00` prologue (5 bytes, __stdcall 1 arg → RET 4)

Pre-v2 the patch flipped `JNP → JMP` at VA 0x8AC77 — that
only covered the fall-height gate and left both the cvar
init chain and FUN_0008BE00 untouched.  The prologue rewrite
is cleaner and covers both.

### Fuel consumption is a single short function (FUN_000842D0)

``FUN_000842D0(__thiscall, float cost)`` is called by every
elemental-power action to decrement ``armor.fuel_current``.
The guarded write at VA 0x84300 does
``[ECX+0x24] -= cost / fuel_max``.  Caller checks the return
value — 0 means "out of fuel, action refused", 1 means
"consumed, action proceeds".  Rewriting the prologue to
``MOV AL, 1 ; RET 4`` (5 bytes) short-circuits to "always
succeed without consuming", yielding infinite fuel for every
power uniformly.

### Roll v3 — the rolling-GROUND-state constant at VA 0x001AAB68 (April 2026)

The v2 roll approach (FMUL rewrite at 0x849E4 + force-on bit
0x40 at 0x85214/0x8521C) had a real bug that user testing
caught: since bit 0x40 gates the shared ``magnitude`` variable,
force-always-on coupled ``roll_scale`` into *airborne horizontal
speed* via ``FUN_00089480``'s ``entity[+0x140] × magnitude``
formula.  At ``roll_scale=3`` jumps covered 3× more horizontal
distance, which felt identical to "gravity got weaker".

v3 retargets ``roll_speed_scale`` at the correct, isolated
surface.  ``FUN_00089A70`` is the rolling/sliding ground-state
physics function (reached via state-machine cases 3/4 from
``FUN_0008CCC0``).  Its velocity FMUL at VA ``0x00089B76``
reads the constant ``[0x001AAB68]`` (vanilla ``2.0``).  That
constant has **exactly one reader in the entire binary**, so
the patch is a 4-byte direct overwrite:

```asm
00089b6d: D9 47 04            FLD  [EDI + 0x4]         ; dt
00089b70: D8 8F 24 01 00 00   FMUL [EDI + 0x124]       ; × magnitude
00089b76: D8 0D 68 AB 1A 00   FMUL [0x001AAB68]        ; × 2.0  ← patched
```

Setting ``roll_speed_scale=3`` overwrites the 4 bytes at the
constant's file offset with ``float(6.0)``.  The WHITE
edge-lock, the force-on sites, and the old FMUL at 0x849E4 all
remain vanilla — so:

- Walking: vanilla (the magnitude × 3 coupling is gone).
- Airborne horizontal: vanilla (ditto).
- Swimming: vanilla.
- Rolling / sliding ground state: `dt × magnitude × (2.0 ×
  roll_scale)` — scales linearly with the slider.

### Climbing — the .rdata 2.0 at VA 0x001980E4 (new April 2026)

``FUN_00087F80`` is the climbing / hanging-ledge state.  Its
per-frame velocity scalar reads ``[0x001980E4]`` (value
``2.0``) — used as ``dt × magnitude × [0x001980E4]`` for the
primary climb FLD and as a secondary climb-retarget FLD.  The
constant has **exactly two readers, both in FUN_00087F80**.
Patching the constant in place therefore scales only climbing
motion.  Implemented as ``apply_climb_speed(xbe, climb_scale=X)``
inside ``player_physics/__init__.py``; slider range 0.1-10.0×.

### WHITE-button sustained roll — bypassing the edge-lock (pre-v3, historical)

**Historical.** In v2 (shipped April 2026, replaced by v3 the
same month) ``apply_player_speed`` additionally NOPed a 2-byte
``JNZ +8`` at VA ``0x00085200`` and installed two 2-byte
"force-always-on" rewrites so the magnitude-boost bit 0x40 was
set every frame.  That approach is gone in v3 — the force-on +
edge-lock sites are now left at vanilla because the new roll
target doesn't need them.  The analysis below is preserved
for anyone investigating the WHITE-button input chain.



The 3× boost at VA `0x849E4` (`FMUL [0x001A25BC]` inside
`FUN_00084940`) is gated by bit `0x40` of the input-state flags
byte at `+0x20`.  That bit is set by `FUN_00084f90` when either:

1. `RIGHT_THUMB` (R3 click) is held — sustained activation, or
2. `WHITE` analog button is tapped for the first frame —
   edge-locked to a single frame per tap.

The edge-lock is enforced by a 2-byte `JNZ +8` at VA
`0x00085200`:

```asm
0x851FB  8A 41 4D   MOV AL, [ECX + 0x4D]     ; WHITE edge-lock byte
0x851FE  84 C0       TEST AL, AL
0x85200  75 08       JNZ 0x0008520A           ; if set, SKIP setting
                                              ; bit 0x40 for this frame
0x85202  C6 41 4D 01 MOV [ECX + 0x4D], 1      ; set edge-lock
0x85206  B0 01       MOV AL, 1                 ; cVar5 = 1 (set bit 0x40)
```

So pressing WHITE:
- Frame 1: edge-lock byte is 0 → JNZ doesn't fire → set
  edge-lock to 1, set bit 0x40, magnitude × 3
- Frame 2+: edge-lock byte is 1 → JNZ fires → skip, bit 0x40
  NOT set, magnitude × 1

This makes `roll_speed_scale` effectively invisible in sustained
gameplay — the 3× boost fires for a SINGLE frame per WHITE tap,
producing a velocity pulse too short to feel.

**Fix**: NOP the 2-byte `JNZ +8` at VA `0x00085200` (replace
`75 08` with `90 90`).  With the skip gone, the `MOV [ECX+0x4D], 1`
+ `MOV AL, 1` run unconditionally every frame WHITE is held —
producing a SUSTAINED 3× boost.  (Other bits in the
`cVar5`-accumulation chain have their own per-button edge-lock
bytes at `+0x48..+0x4C`, so this NOP is safely isolated to the
WHITE / bit 0x40 path.)

`apply_player_speed` now installs this NOP automatically whenever
`roll_scale != 1.0`, so users who configure `roll_speed_scale`
see the effect on WHITE-held input without any extra steps.
Vanilla activation (single-frame tap) is preserved if the user
keeps `roll_scale = 1.0`.

### Roll, not run — the 3.0 at VA 0x001A25BC is WHITE-button only

The slider that shipped as `run_speed_scale` until April 2026
was actually controlling **rolling / diving / dodging speed**,
not a "run" speed.  Azurik has **no separate run**: walking is
simply `CritterData.run_speed × stick_magnitude` (the "run_speed"
field name is a Azurik-internal misnomer — it's the baseline
movement coefficient, consumed as walking speed).

How the roll gate actually works:

1. Per-frame input tick (`FUN_00084f90`) calls `FUN_00084940`.
2. Inside `FUN_00084940`, bit `0x40` of `PlayerInputState.flags +
   0x20` is set when the **WHITE** or **BACK** button is held:
   ```c
   if ((*(byte *)(param_1 + 0x20) & 0x40) != 0) {
     *(float *)(param_1 + 0x124) = *(float *)(param_1 + 0x124)
                                    * 3.0;   // ← VA 0x849E4
   }
   ```
3. The WHITE button in Azurik is the roll / dive button — never
   a sprint button.  That's why earlier users reported *"rolling
   got faster when I set run_scale=3, but walking stayed slow"*:
   the 3.0 multiplier only fires during the roll state.
4. The controller-table slot that drives this (`piVar7[0xb]` in
   the decomp) maps to Xbox `XINPUT_GAMEPAD_WHITE` (the 6th
   analog button byte after A / B / X / Y / BLACK).
   `piVar7[0xf]` maps to BACK (digital bit `0x80`).  Either
   held = 3.0× boost active.

The slider is therefore now labelled **`roll_speed_scale`**.
The old `run_*` kwargs / attr names remain as transparent
aliases so pinned callers don't break, but all documentation
and tests use the new name.

### Retired physics sliders (round-10 purge, late April 2026)

**Deleted: config-editor alternatives exist**

| Pack | Attempted hooks | Use instead |
|---|---|---|
| `no_fall_damage` | prologue RET at 0x8AB70, JNP rewrite at 0x8BE00 | Config editor: `damage` section (raise fall-height thresholds) or `critters_damage` hitPoints. |
| `infinite_fuel` | prologue RET at 0x842D0, NOP at 0x83DE3 | Config editor: `armor_properties.fuel_max` very large, or zero every `attacks_anims` Fuel multiplier. |
| `wing_flap_count` | 47-byte dispatch shim at 0x89321 | Config editor: `armor_properties.Flaps` column. |

### Restored in round 11.8 — previously misdiagnosed as broken

The round-10 purge also deleted four shim packs that landed bytes
cleanly but the user reported "no observable effect in-game".
The round-11.6 forensic later revealed that the "no effect"
symptom was caused by a GUI wiring bug: the backend's
`build_randomized_iso` function only forwarded
`pack_params["player_physics"]` to the randomizer, silently
dropping slider values for every other pack.  The four shim
packs thus always applied with scale=1.0 (no-op) regardless of
what the user entered.

Restored in round 11.8 now that the generic `pack_params_json`
channel ensures slider values actually reach `apply_pack`:

| Pack | Hook VA | Shim strategy |
|---|---|---|
| `flap_at_peak` | 0x89409 | 43-byte shim; replays the final `FSTP [ESI+0x2C]` with `max(vanilla_v0, sqrt(2g·fh)·scale)` — enforces a v0 floor. Largely superseded by `wing_flap_ceiling_scale` but usable as an independent impulse-floor knob. |
| `root_motion_roll` | 0x866D9 | 134-byte `__thiscall` wrap around `anim_apply_translation`; post-scales `param_1[0x6C..0x71]` translation deltas only when `PlayerInputState.flags & 0x40` (WHITE/BACK = roll) is set. |
| `root_motion_climb` | 0x883FF | 128-byte `__thiscall` wrap, ungated (entire function is climb-state). |
| `slope_slide_speed` | 0x8A095 | 17-byte `FLD [abs32]; FMUL [scale_va]; JMP back` — scales the state-4 fast-slide velocity scalar. |

**Caveat for users**: the round-10 deletion rationale included
analysis suggesting the root-motion shims might hook too late
(`anim_apply_translation` commits deltas via vtable+0xC0 inside
the call), but that analysis was confounded with the wiring bug
and was never independently validated. The restored packs may or
may not actually produce in-game effect; they're back so users
can test directly with their values actually reaching the apply
pipeline.

`flap_at_peak` in particular is **related to but distinct from**
`wing_flap_ceiling_scale`:

| Slider | Hook | Semantics |
|---|---|---|
| `wing_flap_ceiling_scale` | FADD at 0x89154 in `player_jump_init` | Raises `peak_z` latch → wing_flap's `fVar1 = peak_z + fh - current_z` stays positive higher up → full v0 naturally |
| `flap_at_peak` | FSTP at 0x89409 in `wing_flap` | Enforces `v0 ≥ sqrt(2g·fh)·scale` as a floor on the FINAL write, regardless of `fVar1` clamp |
| `flap_below_peak_scale` | FMUL at 0x893DD in `wing_flap` | Scales the ×0.5 halving when `fVar1 > 6` (far below peak) |

`wing_flap_ceiling_scale` and `flap_at_peak` both address the
"flaps weaken near peak" problem but via different mechanisms —
envelope expansion vs output-floor enforcement.  They compose
cleanly; the floor catches any edge case where the envelope
expansion doesn't land.

### Airborne horizontal-control speed (new April 2026)

While the player is airborne, horizontal movement velocity is
computed per-frame in `FUN_00089480` as:

```
local_16c = entity[+0x140] × magnitude
```

`entity + 0x140` stores a speed scalar written during jump
initialisation by five `MOV DWORD [reg+0x140], 0x41100000`
(= 9.0) imm32 instructions at VAs `0x84ED3`, `0x856D4`,
`0x890EA`, `0x89126`, `0x8D322` (main ground jump +
4 alternate airborne-state entry paths).

`apply_air_control_speed` rewrites the imm32 at all 5 sites to
`9.0 × air_control_scale`.  Per-site imm32-in-place patch; no
shim, no shared-constant touched.

✅ **Learning**: **don't confuse the initial jump velocity
scalar with mid-air horizontal control**.  I initially (v1
jump patch) conflated them because both get written on jump
entry.  They're two separate entity fields, `+0x140` (air
control / horizontal) and `+0x144` (height / vertical),
consumed by different physics paths.  The correct mapping only
became clear after reading the consuming FPU chain in
`FUN_00089480`.

### Wing-flap (double-jump) vertical impulse (new April 2026)

When the player holds the Air-power / flap button mid-air,
`FUN_00089480`'s airborne physics adds a fixed vertical impulse
to `entity + 0x2C` (velocity.z):

```asm
0008967B: 0F 84 B8 00 00 00   JZ (skip if roll flag 0x40 not set)
0008969A: F6 C1 04             TEST CL, 0x04                      ; flap button
0008969D: 0F 84 96 00 00 00    JZ (skip if flap not pressed)
000896E0: D9 44 24 28          FLD [ESP+0x28]
000896E4: D8 05 C0 25 1A 00    FADD [0x001A25C0]   ; + 8.0
000896EA: D9 5C 24 28          FSTP [ESP+0x28]
```

Gated on BOTH the flap button (input flag 0x04) AND the
roll/air-boost flag (input flag 0x40, which
``apply_player_speed`` force-always-on already enables) being
set, the impulse adds `8.0` to the z-component.  The 8.0 lives
at VA `0x001A25C0` and has 5 readers, only this one on the
player path.

`apply_flap_height` rewrites the FADD at VA `0x896EA` to
`FADD [inject_va]` where `inject_va` holds `8.0 × flap_scale`.
At `flap_scale = 2`, each wing flap adds `16.0` to vertical
velocity — doubling the height gained per flap press.

### Jump velocity: the v2 correction (April 2026)

`FUN_00089060` is the main jump initiation (plays
`fx/sound/player/jump` and transitions into state 2).  My v1
analysis was **wrong**: I saw 5 `MOV [reg+0x140], 0x41100000`
(9.0) imm32 writes and assumed those set the jump velocity.
They don't.  `entity + 0x140` is the **horizontal air-control
speed** — used by `FUN_00089480` (the per-frame airborne physics
function) as a multiplier on stick input for mid-air steering.

The actual jump-velocity formula is at VA `0x89160` inside
`FUN_00089060`:

```asm
VA 0x89160: D9 05 A8 80 19 00    FLD  [0x001980A8]   ; g = 9.8
VA 0x89166: D8 8E 44 01 00 00    FMUL [ESI + 0x144]   ; × h (jump height)
VA 0x8916C: DC C0                 FADD ST0, ST0        ; × 2
VA 0x8916E: D9 FA                 FSQRT                ; v₀ = sqrt(2gh)
VA 0x89170: D9 5C 24 0C           FSTP [ESP + 0xC]    ; store v₀
```

Classic projectile formula `v₀ = sqrt(2gh)`.  `entity + 0x144`
is populated from `FUN_00083F90` for standard jumps or
`*(entity+0x68)` for charged jumps; the imm32 `0x3F8CCCCD`
(1.1) writes around it are transient initializer values that
get immediately overwritten.

**v2 patch**: rewrite the FLD at `0x89160` to
`FLD [inject_va]` where `inject_va` holds
`9.8 × jump_scale²`.  The SQRT then produces
`sqrt(2 × 9.8 × jump_scale² × h) = jump_scale × sqrt(2gh) =
jump_scale × vanilla_v₀` — linear scaling on initial velocity,
quadratic on peak height (because `max_h = v₀² / (2g)`).

Critical property: the shared gravity constant at
`0x001980A8` is NOT touched.  The gravity slider continues to
own that constant (which `FUN_00085700` reads per-frame to drag
velocity down).  Jump and gravity are now independent controls.

✅ **Learning**: **imm32s written to entity fields around
physics sites are NOT always the physics parameter**.  Some are
transient init values that get overwritten before use.  Always
trace the RELEVANT FORMULA (the FPU chain that produces the
output) to find the actual parameter, not just the stored
fields near it.

✅ **Learning**: **the post-mortem is the patch**.  v1 shipped
with correct byte manipulation against the WRONG site and the
user's "doesn't work" report was 100% accurate — my assumption
about what `+0x140` meant was the bug.  Spending 10 minutes on
the disassembly of the consuming formula BEFORE picking the
target would have avoided v1 entirely.

### Jump velocity (v1, historical) — imm32 at 5 call sites (WRONG TARGET)

The main jump function is `FUN_00089060` (plays
`fx/sound/player/jump` and transitions the player into airborne
state 2).  It writes a **plain immediate** into the player
entity's jump velocity slot at `+0x140`:

```asm
   C7 87 40 01 00 00 00 00 10 41
   │  │  └─ disp32 = 0x00000140 ─┘└─ imm32 = 0x41100000 = 9.0
   │  └─ ModR/M = [EDI+disp32]
   └─ MOV r/m32, imm32
```

Vanilla jump velocity = `9.0`.  The airborne-state physics
function (`FUN_00089480`) later reads `+0x140` and multiplies it
by `PlayerInputState.magnitude` for the per-frame velocity, so
scaling the initial value cleanly scales jump height.

Five airborne-state entry sites all write the same `0x41100000`
imm32 into `entity+0x140`:

| VA         | Context                                         |
|-----------:|:------------------------------------------------|
| `0x84ECD` | airborne init reachable from ground walk path   |
| `0x856CE` | early jump entry branch                          |
| `0x890E4` | `FUN_00089060` (main jump), `+0x68 == 0` branch  |
| `0x89120` | `FUN_00089060`, non-zero `+0x68` branch          |
| `0x8D31C` | alternate mid-air state transition               |

Patching the imm32 in-place (no shim, no trampoline) scales
every jump variant uniformly.  `apply_jump_speed` rewrites all
five with `9.0 × jump_scale` packed LE IEEE-754 — zero side
effects on shared constants.

✅ **Learning**: when a game stores a physics parameter as an
immediate in MULTIPLE `MOV r/m32, imm32` instructions, scanning
the XBE for the packed float bytes (here `00 00 10 41`) inside
a disp32 context is faster than tracing each code path by hand.
The 5 sites found here were discovered in one regex pass.

### Swim speed lives in FUN_0008b700 (April 2026)

The player has a dedicated swim state handler, `FUN_0008b700`,
entered when `entity->flags_at_+0x135 & 1` is set (the "in
water" gate, triggered after 4 seconds submerged — which also
fires the `"loc/english/popups/swim"` popup).  The speed
calculation is:

```asm
0008b7b9  D98624010000  FLD  [ESI + 0x124]        ; magnitude
0008b7bf  D80DB4251A00  FMUL float [0x001A25B4]   ; × 10.0
0008b7c5  D95C244C      FSTP [ESP + 0x4c]         ; stroke vel
```

The shared `10.0` at VA `0x001A25B4` has 8 readers; most are
unrelated to player movement, so we patch only the player
site (VA `0x0008B7BF`) by rewriting the `FMUL [abs32]` to
point at an injected `10.0 × swim_scale`.

Because swim is a separate state + separate constant, it has
**no coupling** with walk or roll — the independence math is
trivial: `inject_swim_mult = 10 × swim_scale` and you're done.

Note the second-order coupling: the magnitude read by
`FUN_0008b700` at `+0x124` is still the output of
`FUN_00084940`, so WHITE-button-held underwater triggers the
3.0 boost BEFORE the swim 10.0 stroke.  Vanilla swim stroke =
`10 × raw_stick`; WHITE-hold swim stroke = `30 × raw_stick`.
The `swim_speed_scale` slider multiplies BOTH, which is
usually what the user wants.

---

## Boot state machine

### Boot state is `DAT_001BF61C`, stepped by `FUN_0005F620`

The game has an 8-state boot state machine:

| State | Name                      | Meaning                              |
|-------|---------------------------|--------------------------------------|
| 0     | BOOT_STATE_INIT           | initial dispatch / resource loading  |
| 1     | BOOT_STATE_PLAY_LOGO      | play AdreniumLogo.bik                |
| 2     | BOOT_STATE_POLL_LOGO      | polling the logo movie               |
| 3     | BOOT_STATE_PLAY_PROPHECY  | play prophecy.bik                    |
| 4     | BOOT_STATE_POLL_PROPHECY  | polling prophecy                     |
| 5     | BOOT_STATE_FADE_IN        | post-movie transition                |
| 6     | BOOT_STATE_MENU_ENTER     | enter the main menu                  |
| 7     | BOOT_STATE_MENU           | main menu active                     |
| 8     | BOOT_STATE_LOAD_SAVE      | save-selection / load flow           |
| 9     | BOOT_STATE_INGAME         | in-game: engine update loop runs here |

`FUN_0005F620` is the state-dispatch function.  Writing directly to
`DAT_001BF61C` from a shim lets you skip forward (e.g. straight to
`BOOT_STATE_MENU`); backward jumps work but may produce visible
glitches.

### `play_movie_fn` returns `AL` to signal "enter poll state"

`FUN_00018980` (`play_movie_fn`) is the movie starter.  Its `AL`
return value tells the boot state machine what to do next:

- `AL == 1`: movie loaded, state machine advances to POLL_* to
  drive `poll_movie` per frame.
- `AL == 0`: movie didn't start, state machine skips ahead.

The `qol_skip_logo` shim exploits this: it returns `AL == 0` from
a naked `XOR AL, AL; RET 8` so the game believes the logo loaded
AND ended, skipping straight to the next state.

✅ **Learning**: before replacing any state-machine function, map
out its **return-value contract** — in this game, the boot state
machine is driven entirely by `AL` returns, and getting one wrong
produces a black screen with no error.

Reference: `azurik_mod/patches/qol_skip_logo/shim.c` — the five-line fix that replaced
the original 10-NOP byte patch.

---

## Simulation tick rate

### The 1/30 s constant is `0x3D088889` in .rdata

The per-tick delta is 1/30 second, stored as IEEE 754 float
`0x3D088889` in `.rdata`.  Multiple sites read it; the FPS-unlock
patch does NOT change this constant — it changes the CAP on how
many simulation steps run per render frame.

### FPS unlock operates at VA 0x059AFD + 0x059B37

Two sites control the sim-per-frame cap:
- `0x059AFD`: `CMP ESI, 0x2` → `CMP ESI, 0x4` (cap 2 → 4 steps)
- `0x059B37`: `PUSH 0x2` → `PUSH 0x4` (catch-up delta)

Setting the cap to 2 caused BSODs on death transitions (D3D push
buffer corruption).  Cap 4 is known-stable.  Don't go higher
without xemu testing — larger caps can produce similar corruption
on transition-heavy frames.

Reference: `azurik_mod/patches/fps_unlock/__init__.py`.

---

## Gravity

### The master gravity constant is a single `.rdata` float at VA 0x001980A8

Gravity value is `9.8` as a 32-bit float at VA `0x001980A8`.  Used
by `FUN_00085700` as `velocity.z -= gravity * dt`.

Unlike the run-multiplier, **this constant has only one effective
reader for player physics**, so mutating it in place (via
`ParametricPatch`) is safe.  Side effects on non-player falling
entities are acceptable — the user sees gravity apply uniformly.

Reference: `azurik_mod/patches/player_physics/__init__.py::GRAVITY_PATCH`.

---

## Config / keyed tables

### `config.xbr` uses a custom keyed-table format

Each named table has:
1. A column-name row (string tokens).
2. Rows of data, each cell typed by the column name's `.n` / `.s` /
   `.f` / `.b` suffix.

Lookup at runtime is via `FUN_000D1420("<key>")` for rows and
`FUN_000D1520("<key>")` for cells.  Both are `__thiscall` with the
table pointer in ECX — clang supports them with
`__attribute__((thiscall))`, but the ergonomics are tricky because
the first arg is register-passed.

Currently deferred: exposing these as vanilla functions.  The
`azurik_mod/config/keyed_tables.py` reader can be used offline
instead.

### Not every `.xbr` cell is referenced

See "`CritterData.walkSpeed` and `runSpeed` are dead data" above.
More broadly: `.xbr` tables often have historical columns from
editor workflows that the shipped engine ignores.  ALWAYS trace the
populating function before trusting a column.

---

## Kernel imports

### 151 kernel functions imported; thunk table at VA 0x0018F3A0

The XBE's kernel thunk table starts at virtual address `0x0018F3A0`
(after XOR-decrypting the image-header field at file offset `0x158`
with the retail key `0x5B6D40B6`).  151 four-byte slots, null-
terminated at `0x0018F5FC`.  Each slot's high bit is set, low 16
bits give the xboxkrnl export ordinal.

### No trailing slack — extending the table is hard, but D1-extend sidesteps it

The byte at `0x0018F600` (immediately after the null terminator) is
the start of the library-version table (`b1 ae cc 3b` = a
timestamp).  Can't append new thunks without moving or overwriting
that data.

D1-extend gets around this entirely via a **runtime export
resolver**: shims that need a non-imported xboxkrnl function get a
stub that walks xboxkrnl.exe's PE export table at the fixed retail
base `0x80010000` on first call and caches the result inline.
Zero XBE header surgery; zero call-site rewriting.  See
[`docs/D1_EXTEND.md`](D1_EXTEND.md).

### Xbox retail kernel is mapped at VA 0x80010000

The retail Xbox kernel (`xboxkrnl.exe`) is always loaded at
`0x80010000`.  Its PE image header lives at that base; `e_lfanew`
at `+0x3C` gives the PE header offset; the data-directory entry 0
(EXPORT) gives the RVA of the export table.  This layout is
stable across every retail kernel revision and every retail game —
the resolver hardcodes `0x80010000` without concern.

Debug and Chihiro kernels use different bases; the shim platform
targets retail only.

Reference: `shims/shared/xboxkrnl_resolver.c`.

### Each static import is called via `FF 15 <thunk_va>` (6-byte indirect)

The game's own kernel calls use `FF 15 <thunk_va>` — a 6-byte
indirect jump through the thunk slot.  Our D1 path reproduces this
exactly: we generate the same 6-byte stub in the shim landing
region and resolve the shim's `CALL _Foo@N` REL32 to the stub.

### D1-extend stubs are 33 bytes with inline cache

For imports NOT in Azurik's static 151, the D1-extend resolving
stub is 33 bytes (27 bytes of code + 4-byte cache slot + 2-byte
`JMP EAX` tail).  First call: `CALL xboxkrnl_resolve_by_ordinal` +
cache + `JMP EAX`.  Subsequent calls: `MOV EAX,[cache]; TEST
EAX,EAX; JNZ tail; JMP EAX` — three instructions, cache-hot after
the first call.  Same API surface to shim authors as D1; the
dispatch in `shim_session.stub_for_kernel_symbol` picks the path
automatically.

### Data exports exist alongside functions

Some kernel "imports" aren't functions at all — they're data:

- `ExEventObjectType`, `ExMutantObjectType`, `ExSemaphoreObjectType`,
  `ExTimerObjectType`, `PsThreadObjectType`: `POBJECT_TYPE` pointers
  used by `ObReferenceObjectByHandle` type checks.
- `XboxHardwareInfo`: DWORD with hardware revision flags.
- `XboxHDKey`, `XboxSignatureKey`: 16-byte keys.
- `KeTickCount`, `KeTimeIncrement`: DWORDs readable directly.
- `LaunchDataPage`: pointer to a page preserved across title launches.

Shims that use them must read via `&Name`, not `Name(...)`.

---

## XBE structure

### Azurik has 23 sections; `.text` is the only executable R-X one

From `parse_xbe_sections`:
```
headers  .text  BINK  BINK32  BINK32A  BINK16  BINK4444  BINK5551
BINK16MX  BINK16X2  BINK16M  BINK32MX  BINK32X2  BINK32M
D3D  DSOUND  XGRPH  D3DX  XPP  .rdata  .data  BINKDATA  DOLBY
$$XTIMAGE
```

`D3D`, `DSOUND`, `XGRPH`, `D3DX`, `DOLBY` are RWX statically-linked
library code.  `.text` is the game's own code.  `.rdata` holds
read-only constants + the kernel thunk table.

### `.text` has a 16-byte VA gap before BINK

`.text` runs `0x11000..0x1001D0`; BINK starts at `0x1001E0`.  The
16-byte gap is our "small shim" landing zone (used by `skip_logo`,
the walk/run-speed injected floats, and kernel-import stubs).

Larger shims trigger `append_xbe_section`, which adds a new
executable `SHIMS` section at EOF and shifts the XBE's header-pool
content forward by 56 bytes (the size of one section-header entry).

### Header growth: section-header array is contiguous, pointers auto-fixup

When `append_xbe_section` adds a section, it:
1. Shifts all bytes from `end-of-section-header-array` to
   `size_of_headers` forward by 56.
2. Re-writes every pointer in the image header that now points into
   shifted data (debug-pathname-addr, cert-addr, each section's
   name_addr and head/tail refs).
3. Writes the new section-header entry into the vacated 56 bytes.
4. Updates `num_sections`, `size_of_headers`, `size_of_image`.

Azurik's image has ~880 bytes between end-of-array and
`size_of_headers`, so the 56-byte growth comfortably fits.  **If
Azurik ever ships an XBE with tighter headers, this room calculation
needs redoing.**

Reference: `azurik_mod/patching/xbe.py::append_xbe_section`.

---

## COFF + layout

### Auxiliary symbol records must stay in the symbol list

PE-COFF's symbol table has aux records (size-of-function info, etc.)
interleaved with primary symbols.  Relocation entries index into the
RAW symbol stream, aux records included.

✅ **Learning**: preserve aux records as placeholder `CoffSymbol`
entries (with empty name) so the symbol-index arithmetic in
relocation entries stays correct.  Eliding them breaks REL32
resolution in non-obvious ways.

### `extern_resolver` is the extension point

`layout_coff` takes an optional `extern_resolver: Callable[[str],
int | None]`.  The resolution order for undefined externals:

1. `vanilla_symbols` dict (A3).
2. `extern_resolver` callback (D1 + E).
3. Raise `ValueError` with a helpful message.

Returning `None` from the resolver means "not mine — keep asking".
The `ShimLayoutSession.make_extern_resolver` closure implements the
typical "shared libraries first, then kernel stubs" order.

---

## Tooling / ecosystem

### The OpenXDK xboxkrnl.h is a readable source of truth

`xbox-includes/include/xboxkrnl.h` is the OpenXDK-derived header
with 304 kernel-function declarations.  131 of Azurik's 151
imports have matching declarations there; the remaining 20 are
data exports or fastcall exceptions hand-written in
`scripts/gen_kernel_hdr.py`.

When adding kernel imports via D1-extend, always cross-reference
OpenXDK for the signature before inventing one.

### Xemu is the reference emulator

Test on xemu (`/Users/.../xemu-macos/`) — it's strict enough that
shim bugs cause immediate crashes or black screens, which is great
for testing.  Do NOT trust "it runs" in other emulators; some are
lenient about stack imbalance (they paper over calling-convention
bugs that xemu would catch).

### There are THREE .command launchers / the GUI is the main frontend

`Launch Azurik Mod Tools.command` (macOS/Linux) and the .bat file
(Windows) are the user-facing entrypoints.  They drop into the
Tkinter GUI (`gui/app.py`).  The GUI wraps the `azurik_mod` CLI.

---

## Historical bugs worth remembering

### The `qol_skip_logo` black-screen hang

Symptom: after applying the patch, game sits on a black screen
forever.

Root cause: The original byte-patch overwrote the whole `CALL
play_movie_fn` instruction with NOPs.  `play_movie_fn` is
`__stdcall` with 8 bytes of args — NOPping the CALL leaks 8 bytes
of stack per call AND leaves `AL` undefined.  The boot state
machine read garbage `AL`, interpreted it as "movie playing, stay
in poll state", and never advanced.

Fix: the C shim `XOR AL, AL; RET 8` preserves the stdcall ABI and
returns `AL == 0` explicitly so the state machine advances.

✅ **Learning**: never NOP a CALL to a stdcall function without
also adding the matching `ADD ESP, N` cleanup and setting the
expected return register.

### The player-speed dead-data pivot (C1)

Symptom: sliders had no effect on walk/run speed despite clear
writes to `config.xbr`.

Root cause: `critters_critter_data.walkSpeed` / `runSpeed` are dead
columns.  The effective `base_speed` reader at VA `0x85F65` reads
from a struct slot populated by a different code path.

Fix: inject two per-game floats into the XBE and rewrite the FLD
at `0x85F65` and the FMUL at `0x849E4` to reference those floats
directly.  Requires dynamic whitelisting (`dynamic_whitelist_from_xbe`)
because the injected-float VAs aren't known until apply time.

✅ **Learning**: run the `player-speed 2.0× walk` test with xemu
before declaring victory.  A config patch that writes the right
bytes but changes nothing in gameplay will pass unit tests silently.

### UnboundLocalError in `cmd_randomize_full`

Symptom: `UnboundLocalError: local variable 'xbe_path' referenced
before assignment` when only the player-physics pack was enabled.

Root cause: `xbe_path` was defined inside `if needs_xbe:`, but the
player-speed step (step 7b) referenced it unconditionally.  When
ONLY player-speed was on, `needs_xbe` was False and the block was
skipped.

Fix: define `xbe_path` up front, fold player-speed into the main
XBE block, and include the speed scales in the `needs_xbe`
calculation.

✅ **Learning**: when a variable is defined in a conditional block,
audit EVERY later reference — especially in long pipelines where
sections were refactored independently.

---

## VAs vs file offsets — the player-character trap

Spotted during a post-reorganisation header audit:
``AZURIK_PLAYER_CHAR_NAME_VA`` was set to ``0x001976C8`` and labelled
as a VA in ``azurik.h``, but it's actually the **file offset** of
the ``"garret4\0d:\\0"`` string in ``default.xbe``.  The real VA of
that string is ``0x0019EA68`` (``.rdata``).

The bug went undetected for two reasons:

1. The runtime Python code (``_player_character.py``) indexes
   ``xbe_data[PLAYER_CHAR_OFFSET:...]`` directly — it uses the value
   as a file offset, which is what it actually is, so the patch
   worked.
2. No shim had yet tried to reference the constant through a DIR32
   relocation — which would have resolved to the **wrong memory
   address** at runtime and read garbage ``.rdata`` bytes.

Fix (current): ``azurik.h`` now exposes both spellings —
``AZURIK_PLAYER_CHAR_NAME_VA = 0x0019EA68`` (real VA for shim use)
and ``AZURIK_PLAYER_CHAR_NAME_FILE_OFF = 0x001976C8`` (what the Python
code expects).  Any shim that references this anchor via ``DIR32`` now
gets the correct runtime address.

General rule: anything named ``_VA`` should survive ``va_to_file(va)``
without changing meaning.  If the raw value passes unchanged into
``xbe_data[raw_value:]`` indexing, it's a file offset and should be
named accordingly.

## Historical: pre-reorganisation layout

The repo was reorganized into folder-per-feature in an earlier
refactor.  If you're reading a commit before that reorg, you'll
see:

| Old path                                      | New path                                                |
|-----------------------------------------------|---------------------------------------------------------|
| `azurik_mod/patches/fps_unlock.py`            | `azurik_mod/patches/fps_unlock/__init__.py`             |
| `azurik_mod/patches/player_physics.py`        | `azurik_mod/patches/player_physics/__init__.py`         |
| `azurik_mod/patches/qol.py` (4 packs)         | four folders: `qol_gem_popups/`, `qol_other_popups/`, `qol_pickup_anims/`, `qol_skip_logo/` |
| `shims/src/skip_logo.c`                       | `azurik_mod/patches/qol_skip_logo/shim.c`               |
| `shims/src/_*.c` (test fixtures)              | `shims/fixtures/_*.c`                                   |
| Per-pack `AZURIK_SKIP_LOGO_LEGACY=1` env var  | Single `AZURIK_NO_SHIMS=1`                              |
| Per-pack `apply_*_patch(xbe_data, ...)`       | Unified `apply_pack(pack, xbe_data, params)`            |

Shim `.o` files are keyed on **pack name** now, not source stem,
so `shims/build/qol_skip_logo.o` (not `skip_logo.o`).  Two features
whose source files both happen to be called `shim.c` can't collide
in the shared build cache.

## ControllerState struct (XInput polling) — pinned 2026-04-18

Per-player gamepad state lives at `DAT_0037BE98 + player_idx * 0x54`
(up to 4 players).  Populated every frame by the XInput polling loop
`FUN_000a2df0 → FUN_000a2880` which calls `XInputGetState` and
normalises the raw XInput fields into floats:

| Offset | Type | Field |
|:--|:--|:--|
| 0x00 | f32 | left_stick_x (sThumbLX normalised to [-1, 1]) |
| 0x04 | f32 | left_stick_y |
| 0x08 | f32 | right_stick_x |
| 0x0C | f32 | right_stick_y |
| 0x10 | f32 | dpad_y (-1 / 0 / +1) |
| 0x14 | f32 | dpad_x (-1 / 0 / +1) |
| 0x18..0x34 | f32 × 8 | button_a, button_b, button_x, button_y, button_black, button_white, trigger_left, trigger_right (analog 0..1) |
| 0x38 | f32 | stick_left_click (digital 0 / 1) |
| 0x3C | f32 | stick_right_click |
| 0x40 | f32 | start_button |
| 0x44 | f32 | back_button |
| 0x48..0x53 | u8 × 12 | edge_state[] — latches that the polling loop clears when the corresponding button returns to 0.0, so the engine can implement "consume rising edge once per press" |

Active-player index at `DAT_001A7AE4` (0..3, or 4 = "no controller").
Stick dead zone is raw-unit ±12000 of centre.  Analog-button dead zone is raw-unit 30.

Exposed in `azurik.h` as `ControllerState` with `_Static_assert`s
pinning every late field.  Compile-time regression guards in
`tests/test_shim_authoring.py::test_controller_state_fields_resolve_to_expected_offsets`.

Reference: `FUN_000a2880` decomp (the XInput-write side of the
polling loop) — each `DAT_0037be{9c,a0,a4,a8,ac,b0...}` target maps
1:1 to a struct field in this table.

## Vanilla-function exposure — __fastcall edge cases

When adding functions to `vanilla_symbols.py`, `__fastcall` works
cleanly in clang as long as the function TRULY is fastcall.  The
callers' register-setup pattern is the authoritative signal:

- `MOV ECX, <arg1>; XOR/MOV EDX, <arg2>; CALL` → __fastcall, 2
  register args + optional stack args.
- `PUSH ...; PUSH ...; MOV ECX, this; CALL` → __thiscall, 1
  register arg (ECX=this) + N stack args.  **clang's
  `__attribute__((thiscall))` works on i386-pc-win32 but emits
  ``@name@N`` mangling that doesn't match what Ghidra / vanilla
  Azurik emitted** — you need a naked-asm wrapper that shuffles
  registers before the call.  Deferred.

Working example: `FUN_0004B510` (entity_lookup) — both callers
confirmed pure __fastcall (ECX=name, EDX=fallback).  Now registered
at `entity_lookup@8`.

### Handling MSVC-RVO ABIs (ECX + EDX + EAX + ESI + stack float)

`FUN_00085700` (gravity integration) is the poster child for an
ABI clang can't express directly:

- ECX = config (fastcall arg 1)
- EDX = velocity pointer (fastcall arg 2)
- EAX = output struct pointer (MSVC RVO — implicit)
- ESI = caller-provided entity context (callee-saved, but the
  vanilla function relies on the caller having set it)
- `[ESP+4]` = float gravity_dt_product (callee pops via RET 4)

Solution pattern, now shipping in `shims/shared/gravity_integrate.c`:

1. **Register the vanilla with a LIE**.  In `vanilla_symbols.py`,
   declare the function as plain `fastcall(8)` — just ECX+EDX.
   Mangled name becomes `@name@8`.  Clang's `CALL @name@8`
   resolves via the normal REL32 layout path.
2. **Write a C wrapper that uses inline asm** to set up the
   extra registers right before the CALL.  Key constraint:
   every register load + the CALL must live inside ONE atomic
   `__asm__ volatile` block — clang can't reorder anything
   inside a single asm block, so EAX survives to the CALL.
3. **Clobber list declares every touched register** (`"eax",
   "ecx", "edx", "esi"`) so clang's allocator saves/restores
   as needed around the block.
4. **Satisfy `__fltused` locally** via an asm-label override:
   ``int __fltused __asm__("__fltused") = 0;`` — stops clang
   emitting an undefined external for the float linker marker.

The wrapper exposes a clean `stdcall(N)` C API to shim authors
who just call it like any other function.  Tested end-to-end in
`tests/test_gravity_wrapper.py`.

This pattern generalises to any vanilla function with "weird"
register setup (thiscall, custom calling conventions, implicit
register inputs).  When adding a new one, copy
`shims/shared/gravity_integrate.c` and adjust the register
constraints.

## hourglass.xbr + fx.xbr — data-file scope + XBE hooks

Cross-referenced findings from opening the two data files in
Ghidra alongside ``default.xbe``.  Both are **DATA-only**
(Ghidra won't find functions in them directly), but each has
specific code paths in the XBE that reference their contents.

### hourglass.xbr — UI loading spinner, NOT a timing resource

Despite the suggestive name, ``hourglass.xbr`` is just **sprite
geometry for the UI hourglass icon** that appears during loading
screens.  37 KB total:

- 20 ``surf`` entries, 1,152 bytes each.  Each is one frame of
  the spinning hourglass animation.
- No timing / scheduler data.  The 60 FPS patch does not interact
  with this file.

Source-path leak at VA ``0x00198E93`` confirms the module
identity: ``C:\Elemental\src\mud\hourglass.cpp``.  The loader
path pushes ``"interface/hourglass/..."`` at VA ``0x000D0F08``
(inside ``FUN_000D0EE0``), which is the only in-XBE site that
references the data file.

**Takeaway**: nothing actionable for modding unless someone
wants to replace the loading spinner graphic.  No action taken.

### fx.xbr — Maya-exported particle-system effect library

36.5 MB visual-effects library.  3,572 TOC entries with 113
distinct effect graphs (each has matching ``node``, ``ndbg``,
``sprv``, ``gshd`` sections).  Built from Maya source files
visible in the blob as paths like
``c:\Elemental\gamedata\fx\damage\fx_acid_2.ma``.

Effect graph shape (from the string-table dump):

- **Named timers**: ``AcidHit_timer``, ``EarthHit_timer``,
  ``fx_magic_timer`` — per-effect countdown / max-value
  accumulators.
- **Particle-system nodes**: Maya standard names
  (``pEmitterTop``, ``pRendererShape``, ``pSystem``, ``pSink``,
  ``pAccelerator``) preserved in the runtime graph.
- **Lifecycle**: ``Unload_effectControl`` nodes for cleanup,
  ``effectOrigin`` / ``effectControl`` for graph roots.

Source-path leak at VA ``0x0019DE34`` confirms the module
identity: ``C:\Elemental\src\mud\effectGraph.cpp``.

**XBE hooks** into the effect system (found by grepping .text
for pushed string-VAs):

- **``FUN_00066830``** — "find effect node by name".  Takes a
  ``this`` pointer in EAX (!) and a name on the stack; returns
  the matching node or NULL.  Called from 3 sites referencing
  ``fx_magic_timer`` (VA ``0x19C1AC``).  Unusual ABI (EAX-this
  instead of ECX-this) — would need a gravity-style inline-asm
  wrapper to expose via ``vanilla_symbols.py``.  Not currently
  exposed — no shim has asked for effect playback yet.
- **``FUN_00083000``** — "update effect timer max" method.
  Reads the magic-timer node via ``FUN_00066830``, compares a
  float argument against the stored max, updates if greater +
  sets a dirty flag.  Called from a dispatch table (VTABLE-
  style) at VA ``0x19C174``.

**For 60 FPS investigations**: no frame-rate-dependent pattern
found in the callers examined.  The effect timers receive
``dt``-scaled floats from their callers (not raw frame counts),
so the 60 FPS patch's simulation cap should not break effect
timing.  If future testing reveals visual-effect speed drift at
60 FPS, re-examine the effect-update dispatcher at
``0x830E0..0x832A1`` for the specific ``dt`` multiplier.

**Takeaway**: effect-by-name lookup is a potential future
vanilla-symbol addition (wraps around the Maya-name graph for
shim authors who want to trigger specific effects).  Deferred
until a concrete shim needs it; the inline-asm wrapper cost
(à la gravity) isn't worth paying speculatively.

### fx.xbr wave codec — the full RE trail (April 2026, final)

``fx.xbr`` contains **700** ``wave`` TOC entries.  The April 2026
Ghidra walk pinned the complete pipeline and proved there's **no
custom codec** to reverse — Xbox DirectSound decodes everything
in hardware.

**Static call chain** (dumped via ``azurik-mod xrefs`` /
``call-graph``):

::

    symbolic name (fx/sound/...)               ← index.xbr lookup
        ↓
    load_asset_by_fourcc(0x65766177, 1)        @ VA 0x000A67A0
        ↓  called from
    FUN_000A20C0 (per-frame sound tick)        @ VA 0x000A20C0
        ↓  allocates sound object
    FUN_000AE030 (factory)                     @ VA 0x000AE030
        ↓  vtable slot [+0x34] → init method
    FUN_000AC6F0                               @ VA 0x000AC6F0
        ↓  delegates header parse
    FUN_000AC400                               @ VA 0x000AC400
        ↓  fills WAVEFORMATEX
    DSOUND::DirectSoundCreateBuffer
    DSOUND::IDirectSoundBuffer_SetBufferData(buf, wave_entry + 16, N)

**Engine-canonical 16-byte wave header** (as read by
``FUN_000AC400`` byte-for-byte):

::

    offset 0x00  u32  sample_rate       (22050 / 32000 / 44100 / …)
    offset 0x04  u32  sample_count      (unused by parser, kept for
                                         duration display)
    offset 0x08  u8   channels          (1 or 2)
    offset 0x09  u8   bits_per_sample   (PCM: 8 or 16; XADPCM: 4)
    offset 0x0A  u8   (unused)
    offset 0x0B  u8   codec_id          (0 = PCM, 1 = Xbox ADPCM)
    offset 0x0C  u32  (unused — padding)
    offset 0x10  ...  codec payload fed to DirectSound

``FUN_000AC400`` returns 0 (rejection) for any entry with
``codec_id`` outside ``{0, 1}``.  ``FUN_000AC6F0`` checks that
return value and silently aborts the sound alloc on failure —
the game NEVER attempts to decode those bytes as audio.

**Distribution** (vanilla ``fx.xbr``, after re-classification):

| Classification      | Count | Notes                                  |
|---------------------|------:|----------------------------------------|
| ``xbox-adpcm``      |   103 | 102 mono + 1 stereo; WAV wrapper works |
| ``pcm-raw``         |     0 | Code path exists; no entries use it    |
| ``likely-animation``|     9 | 4-byte TOC tags in first 64 bytes      |
| ``non-audio``       |   557 | Fails engine header check; not audio   |
| ``too-small``       |    31 | < 64 byte payload                      |

The 557 ``non-audio`` entries are what used to be labelled
``likely-audio`` on a pure-entropy heuristic.  They're high-
entropy payloads stored under the ``wave`` fourcc for historical
reasons — effect metadata, unused resources, development
leftovers — that never flow through the wave-init pipeline
because the engine's own parser rejects their headers.

**Why the audio "decoder RE" problem evaporated**: Xbox
DirectSound handles ``WAVE_FORMAT_XBOX_ADPCM`` (0x0069) natively
in hardware.  ``FUN_000AC6F0`` just builds a ``WAVEFORMATEX``
from the 16-byte header and hands the raw payload at
``wave_entry + 16`` to ``IDirectSoundBuffer_SetBufferData``.  No
game-side codec exists to reverse.  Our ``audio dump`` tool's
RIFF/WAVE wrappers use the same format tag so ffmpeg / vgmstream
/ Audacity decode identically to what DirectSound plays.

**Ruled out during the investigation** (kept for reference —
don't rechase these):

- Straight 16-bit / 8-bit PCM reinterpretation of the non-audio
  blobs: mean ``|Δ|`` between adjacent int16 samples ≈ 30 000
  (near-uniform-random).
- Headerless IMA / MS / Xbox ADPCM decode with either zero
  start-state or first-4-bytes-as-header: all produce noise.
- Non-standard block size (36 / 72 / 140 bytes) for the
  non-audio entries: 0 of 557 sizes divide cleanly.

**Takeaways**:

1. The ``--raw-previews`` CLI flag is kept as a generic
   "inspect any high-entropy binary blob in Audacity" helper but
   is NOT an audio-recovery path.  Most users can leave it off.
2. ``duplicate_of`` detection in the manifest still has value —
   surfaces 48 redundant entries in vanilla fx.xbr where the
   same bytes appear under multiple indices.
3. Engine-side vanilla symbols worth registering for future
   shim authoring: ``FUN_000AC400`` (header parser),
   ``FUN_000AC6F0`` (wave-init vtable slot), ``FUN_000AE030``
   (sound-object factory).  Not added yet — deferred until a
   concrete shim needs them.

## prefetch-lists.txt — the level manifest goldmine

Azurik's ISO ships with a plain-text **level manifest** at
``prefetch-lists.txt`` (ISO root, not inside ``gamedata/``).  For a
long time the repo hard-coded its own level tables in
``azurik_mod/randomizer/shufflers.py``; we now read the canonical
manifest via ``azurik_mod.assets.prefetch``.

### File format

Stanza-based INI:

    tag=always
    file=index\\index.xbr
    file=hourglass.xbr
    file=%LANGUAGE%.xbr
    file=interface.xbr
    file=config.xbr
    file=fx.xbr
    file=characters.xbr

    tag=a1
    file=A1.xbr
    neighbor=a6
    neighbor=e6

    tag=a6-extra
    file=diskreplace_air.xbr
    file=diskreplchars.xbr

Four stanza shapes:

- ``tag=always`` — **7 globals** the streaming loader keeps
  resident across all levels.  ``%LANGUAGE%`` is substituted at
  runtime to ``english``/``french``/… (see
  ``PrefetchManifest.resolve_language``).
- ``tag=default`` — build-system alias for ``training_room``.
  Not a playable level in its own right; flagged by
  ``PrefetchTag.is_alias``.
- ``tag=<level>`` — **24 playable levels** (a1, a3, a5, a6,
  airship, airship_trans, d1, d2, e2, e5, e6, e7, f1, f2, f3,
  f4, f6, life, town, training_room, w1-w4).
- ``tag=<level>-extra`` — **5 extras packs** (``a6-extra``,
  ``e5-extra``, ``f6-extra``, ``life-extra``, ``w3-extra``)
  containing the per-element ``diskreplace_*.xbr`` bundles.

### Key insight: the graph is DIRECTED, not symmetric

The ``neighbor=`` edges are **streaming-loader prefetch hints**,
not portal declarations.  Out of ~70 edges in the vanilla
manifest, at least 15 are asymmetric:

    a6 → town               # town → life, e2, f1, d1, w1 only
    w1 → airship_trans      # airship_trans has ZERO neighbors
    training_room → w1      # w1 doesn't list training_room back

``airship_trans`` is the extreme case — every airport-adjacent
zone prefetches it, it prefetches nothing.

This matters because **the randomizer can't use this graph as a
reachability solver input**.  For that we still scrape the
portal strings out of the level XBRs themselves.  The manifest
is useful for:

- Authoritative level-set enumeration (24 tags)
- Classification: is this XBR a level, a global, or an alias?
- Integrity check: does every file mentioned here exist on disk?
- Orphan detection: which XBRs on disk aren't in any stanza?
  (Answer: ``selector.xbr`` + ``loc.xbr`` — dev/UI artefacts.)

### Known drift from the randomizer's hardcoded table

``LEVEL_PATHS`` in ``shufflers.py`` ships **22 levels**, missing:

- ``training_room`` — no ``levels/.../training_room`` save-path
  prefix exists (it's bootstrapped through the ``default`` alias).
- ``airship_trans`` — every entry is cutscene-driven; no portal
  strings to rewrite.

Cut content surfaces too: ``f1 → f7`` references a cut level
that has no ``tag=f7`` stanza.  The randomizer already special-
cases this via ``EXCLUDE_TRANSITIONS``.

The ``tests/test_assets_manifest.py::PrefetchVsHardcodedDelta``
test flips red if any other drift appears.

## filelist.txt — the integrity manifest

Sibling file at the ISO root.  DOS-ish format:

    \\
    f <md5> <bytes> a1.xbr
    f <md5> <bytes> a3.xbr
    ...
    d index

    \\index\\
    f <md5> <bytes> index.xbr

``azurik_mod.assets.filelist`` parses it and exposes
``FilelistManifest.verify(iso_root, check_md5=True)`` for
byte-level integrity validation.  Full MD5 scan of the
vanilla 951 MB dump runs in ~1.5 s on an M1 SSD.

Exposed end-to-end through:

    azurik-mod iso-verify <unpacked-iso-dir> [--no-md5] [--graph]

Exit code is non-zero on any integrity mismatch — safe to wire
into CI/pre-build hooks.

### Path-scoping gotcha

``filelist.txt`` declares paths relative to the **``gamedata/``
subdirectory** (its top-level scope line is just ``\\``).  But
the file itself lives at the ISO root, one level up from
``gamedata/``.  ``FilelistManifest._resolve_root`` auto-detects
this mismatch by probing the first three entries against both
candidate roots and using whichever matches more files.  If
Microsoft's ISO layout ever changes this could need extending,
but the heuristic has zero false positives today.

### Extract-pipeline integration

Every ``run_xdvdfs ... unpack`` call in
``azurik_mod/randomizer/commands.py`` is followed by a
``verify_extracted_iso(extract_dir)`` call.  The helper runs a
size-only integrity scan (no MD5 — too expensive on the hot
path) and prints a warning block with up to 20 mismatches if
anything looks wrong.  It never raises, so a corrupted
extraction still produces something usable for diagnosis, but
the user gets a loud heads-up pointing them at
``azurik-mod iso-verify`` for the full MD5 audit.

Size-only scan cost: ~3 ms for 42 entries on an M1 SSD, vs
~1.5 s for the full MD5 pass.

## selector.xbr — the developer level-select hub

Discovered during the filelist/prefetch cross-check: 2 MB level
XBR that IS on disk, IS referenced by the XBE, but is NOT in
``prefetch-lists.txt``.

### What it is

``selector.xbr`` is a legitimate, playable in-game level built on
the same layout as every other level (``node``, ``levl``, ``surf``,
``rdms``, etc.).  Its ``node`` section carries **35 portal
strings** that between them reach every live level in the game,
plus direct cutscene triggers:

- 22 regular level portals — ``levels/fire/f1`` through
  ``levels/water/w4``, ``levels/life``, ``levels/town``, etc.
- 1 self-reference (``levels/selector``)
- 1 portal to a **cut level** (``levels/earth/e4``) — the only
  on-disk reference to this level anywhere
- 10 movie-scene triggers (``movies/scenes/prophecy``,
  ``movies/scenes/training1``, …``disksdestroyed``,
  ``catalisks``, ``airship2``, ``death1``, ``deathmeeting2``,
  ``disks_restoredall``, ``newdeath``)

So it's a developer cheat menu — loading this level gives you
single-click access to every level + cutscene.

### How to activate it

The XBE has 4 ``.text`` callsites that push the VA of the
``"levels/selector"`` string at ``VA 0x1A1E3C``:

- ``VA 0x12C56``  — probably init/boot-path
- ``VA 0x52FA7, 0x533E3, 0x53400`` — inside ``FUN_00052F50``,
  a conditional load gated on a boot-flag read from BSS
  ``VA 0x001BCDD8``.  Disassembly shows:

```
  mov  esi, [0x001BCDD8]    ; read debug-mode flag
  cmp  esi, -1
  jnz  +0x05
  mov  esi, 0x3             ; default when flag unset
  mov  ebp, 0x1A1E3C        ; "levels/selector" string
```

So a ``qol_enable_dev_menu`` shim could force-enable the cheat
menu by priming ``[0x001BCDD8]`` to a non-``-1`` value during
boot.  Not shipped today — no one has asked for it — but the
plumbing is a ~20-line shim when someone does.

### Why it's a prefetch-manifest orphan

The streaming loader doesn't see it because it's never on a
level's ``neighbor=`` list.  It's loaded directly by
``FUN_00052F50`` which bypasses the prefetch system entirely.
The ``azurik-mod iso-verify`` orphan-detector lists it
(alongside ``loc.xbr``) as a manifest orphan — both are
legitimate, both are unused by the normal game flow.

### Cut-level discoveries

Two cut levels are now documented as ``KNOWN_CUT_LEVELS``:

- ``f7`` — referenced only by ``f1``'s ``neighbor=`` list in
  ``prefetch-lists.txt``.  The randomizer's
  ``EXCLUDE_TRANSITIONS`` already knows about it.
- ``e4`` — referenced only by ``selector.xbr``'s portal list.
  No XBE code paths reference it.

Both are useful flags for a future "randomizer finds all known
dead portals" audit pass.

## enable_dev_menu — retired as unshippable (April 2026, historical)

**The `enable_dev_menu` patch was removed from the shipped pack
list after four iteration attempts all failed to produce an
observable effect in-game.**  Every version (JZ NOPs, cheat-cvar
short-circuit, stage-1+2 short-circuit, `FUN_00053750`
trampoline) landed its target bytes correctly in the XBE; the
``inspect-physics`` diagnostic confirmed ``[INSTALLED]`` on
each.  But the actual cutscene-end → first-level transition
evidently routes through a code path we haven't mapped, so
none of them rerouted the first level load to ``selector.xbr``.
The research below is preserved because the validator-chain +
state-machine analysis will still be useful for any future
attempt to force the developer level hub.

## enable_dev_menu — three-stage validator chain (April 2026)

### Why the old JZ NOPs didn't force selector.xbr

The `dev_menu_flag_check` function (`FUN_00052F50`) ends with a
**three-stage level-name validator chain**.  Each stage calls
`FUN_00054520` (a read-only "does this level asset exist?" probe)
against a different candidate string:

```c
uVar6 = FUN_00054520();           // stage 1: caller's param_2
if ((char)uVar6 == '\0') {
    uVar6 = FUN_00054520();       // stage 2: pcVar10 (dev branch)
    if ((char)uVar6 == '\0') {
        uVar6 = FUN_00054520();   // stage 3: "levels/selector" (hardcoded)
        if ((char)uVar6 == '\0') {
            FUN_000a9100("can't find a level to go to.");
        }
        param_2 = "levels/selector";
    } else {
        param_2 = pcVar10;
    }
}
FUN_00053750(param_1, param_2, param_3, '\0');
```

The pre-April-2026 `enable_dev_menu` patch NOPed two `JZ`
instructions (VAs `0x52F7E` and `0x52F95`) in a precursor
vtable branch that sets `pcVar10 = "levels/selector"` vs
`"levels/training_room"`.  That made stage 2 load selector —
but stage 2 only fires when stage 1 fails, and stage 1 almost
always succeeds because real callers (`FUN_00052910`,
`FUN_00055AB0`, `FUN_00056620`) pass a known-valid level
string.  **The NOPs took effect, but their effect was
invisible** because stage 1 consistently won.

### The new patch: force stages 1 and 2 to fail

Stage 3 already hard-codes `PUSH "levels/selector"` before the
final `CALL FUN_00053750` at VA `0x00053406`:

```asm
0x533E3  B8 3C 1E 1A 00           MOV  EAX, 0x001A1E3C    ; "levels/selector"
0x533E8  E8 33 11 00 00           CALL FUN_00054520        ; stage 3 validation
0x533ED  84 C0                    TEST AL, AL
0x533EF  0F 84 8E 01 00 00        JZ   error_handler
0x533F5  8B 8C 24 4C 01 00 00     MOV  ECX, [ESP+0x14C]
0x533FC  6A 00                    PUSH 0
0x533FE  6A 00                    PUSH 0
0x53400  68 3C 1E 1A 00           PUSH "levels/selector"
0x53405  51                       PUSH ECX
0x53406  E8 45 03 00 00           CALL FUN_00053750
```

To force stage 3 to win, we just need stages 1 and 2 to fail.
Replace their `CALL FUN_00054520` instructions with `XOR EAX,
EAX ; NOP ; NOP ; NOP`:

```asm
VA 0x53384:  E8 97 11 00 00  ->  31 C0 90 90 90   ; stage 1 fail
VA 0x533C3:  E8 58 11 00 00  ->  31 C0 90 90 90   ; stage 2 fail
```

`AL = 0` after each replacement, the following `TEST AL, AL`
sets `ZF = 1`, the `JZ` fires, flow cascades through to stage
3.  Selector.xbr exists in every vanilla ISO so stage 3's
validation always succeeds, and the final `CALL FUN_00053750`
loads `"levels/selector"`.

### Lessons

✅ **Branch patching matters where the *observable* decision is
made**, not where a precursor sets up candidate values.  The
v1 patch sat one branch too early in the decision chain.

✅ **Three-stage fallback patterns** are a gift when you want to
force a specific outcome — just make the earlier stages
artificially fail and let the last stage (usually a hardcoded
safe default) win.

### v4: trampoline FUN_00053750 directly (April 2026)

After shipping v3 (XOR-EAX on validators 1 and 2), byte patches
verified correct, tests green — but users still reported
*"still does nothing"*.  The real blocker: **`FUN_00055AB0`
(main game state machine) calls `FUN_00053750` DIRECTLY** for
cutscene-end transitions, passing hardcoded level names.  That
path never enters `dev_menu_flag_check`, so v3's validator short-
circuits had nothing to intercept.

**v4 strategy**: hook the universal entry.  Patch
`FUN_00053750`'s prologue (first 7 bytes → `JMP rel32 + 2 NOPs`)
to jump into a 27-byte shim that:

1. Guards: only override when `param_4 == 0` (non-bink path) —
   cutscenes continue to play normally.
2. Rewrites `param_2` at `[ESP+8]` to point at the
   `"levels/selector"` string at VA `0x001A1E3C`.
3. Replays the clobbered `MOV EAX, [ESP+4] ; MOV ECX, [EAX+0x40]`
   so register state matches vanilla at the return point.
4. `JMP`s back to `SUB ESP, 0x824` at VA `0x00053757`.

Now every level transition — regardless of upstream caller —
routes to selector.  Bink movie loads (`param_4 != 0`) pass
through untouched.

✅ **When an upstream patch has "no visible effect" despite
correct bytes, the target isn't on the execution path.**  Scan
the call graph for direct paths to the SHARED downstream
function and patch THAT — avoids the combinatorics of
enumerating every upstream caller.

✅ **Trampoline-based stack-argument rewriting** is viable with
zero register clobbers if you (a) guard on unrelated stack args
(here `param_4`) to preserve alternative code paths, and (b)
replay the clobbered-prologue instructions inside the shim before
JMPing back.  27 bytes of shim is enough to hook any `__cdecl`
function entry that doesn't immediately branch on the replaced
instructions' outputs.

✅ **Preserving stack balance** is trivial with `XOR reg,reg +
NOPs` because the replacement is exactly the same size as the
original `CALL rel32` (5 bytes) and doesn't push / pop
anything.  No epilogue changes needed.

✅ **Read-only probes are safe to skip.**  `FUN_00054520` only
checks asset existence; short-circuiting its calls has no
side effects on game state.

⚠️ **Side effect**: this patch overrides every level load in
the game, including "Load Game".  Users who want selector only
on New Game would need a more targeted patch (e.g., gate the
XOR on a save-bootstrap flag).  The broader override is fine
for the "experimental" category.

## Native cheat UI — cheats.cpp (April 2026)

Separate from selector.xbr, Azurik has an **in-game cheat UI**
compiled from `\Elemental\src\game\cheats.cpp`.  It lives
behind the `enable cheat buttons` cvar and provides:

- `"Game state..."` submenu (save / restore / dump)
- `"magic level: %d"` editor for Fire / Water / Air / Earth /
  Chromatic magic levels
- `"Change level..."` + `"startspot"` level picker
- `"foc cam"` floating-camera toggle
- `"cheatsave"` / `"srcSpecies"` developer hooks
- Two companion toggles: `"enable debug camera"` + `"enable
  snapshot"`

### CVar layout

All three cheat cvars follow the same pattern:

| CVar                    | Storage VA | Getter VA    |
|-------------------------|-----------:|-------------:|
| `enable cheat buttons`  | `0x37AF20` | `0x000FFFC0` |
| `enable debug camera`   | `0x37B148` | `0x000FFFD0` |
| `enable snapshot`       | `0x37AFA0` | `0x000FFFE0` |

Each storage byte is in BSS (zero-initialised, so the cvar is
`false` by default).  Each getter is an 11-byte stub of the form:

```asm
PUSH  <storage_va>         ; 5 bytes  (68 xx xx xx xx)
CALL  cvar_read_generic    ; 5 bytes  (E8 rel32)
RET                        ; 1 byte   (C3)
(padding)                  ; 5 bytes  (90 NOPs)
```

The storage-to-getter binding is 1:1 — each cvar has its own
stub because the cvar system dispatches via a table of
(name, storage, getter) triples registered by `FUN_000721b0`
(the cheat-registration block at VAs 0xFE0D0..0xFE340).

### Why we short-circuit the getter, not the storage

Since the storage lives in BSS, there are no stored bytes in
the XBE to flip.  We'd need either (a) a C-shim that pokes the
BSS byte to 1 at startup, or (b) a byte patch somewhere that
forces the reads to return 1 regardless of the BSS value.

Option (b) is simpler and smaller: replace the first 6 bytes
of the getter with `MOV EAX, 1 ; RET`.  The function now
unconditionally returns 1, and callers don't care whether the
cvar system was even queried.  That's the `qol_enable_dev_menu`
patch.

### Activation (in-game)

Once the cvar returns 1, the cheat dispatcher in `FUN_00083d80`
lights up.  Gate:

```c
if ((float)puVar5[0xc] != 0.0) {   // LT analog > 0
  // poll the four face buttons (A/B/X/Y)
  // each dispatches to FUN_00083410(0..3)
}
```

`puVar5[0xc]` is the LEFT TRIGGER analog value at offset
`0x30` in the per-controller state table.  So the activation
combo is **hold LT + press a face button**.  FUN_00083410
then dispatches to one of the four registered cheat actions
(indices 0..3 map to the entries registered at
`FUN_000721b0`'s first four `(&DAT_001bcde4)[iVar1 * 2]`
writes — typically Game-state, magic-level, change-level,
snapshot or foc-cam depending on build).

### Why we previously patched selector.xbr instead

The pre-April-2026 `qol_enable_dev_menu` patched two `JZ`
instructions in `FUN_00052F50` to force `levels/selector`
(the selector.xbr level-loader hub) to load at game start.
That's a DIFFERENT feature: selector.xbr is a static level
room with portal plaques to every level + cutscene, loaded by
the engine's standard level loader once the JZ checks are
bypassed.  It does NOT enable any runtime cheats — no
magic-level editing, no game-state tools.  Users reported the
patch as *"doesn't do anything"* because:

1. The selector.xbr load only triggers on the New-Game flow.
2. Without actually pressing "New Game", the JZ NOPs are
   invisible.
3. Even when you do reach selector.xbr, it's a ONE-WAY trip
   to a level — none of the UI overlay cheats are accessible.

The cvar-getter patch delivers what users actually expect:
real cheats live from boot.

### Controller-table offsets (Azurik convention)

Reverse-engineering the cheat-activation combo required
mapping `&DAT_0037be98 + ctrl_idx * 0x54` to XInput slots.
Layout per-controller (as populated by `FUN_000a2880` using
`XAPILIB::XInputGetState`):

| Offset | Slot | Meaning                    |
|-------:|-----:|:---------------------------|
| +0x00  | 0    | LX-axis (signed -1..1)     |
| +0x04  | 1    | LY-axis                    |
| +0x08  | 2    | RX-axis                    |
| +0x0C  | 3    | RY-axis                    |
| +0x10  | 4    | D-Pad L/R (-1, 0, +1)      |
| +0x14  | 5    | D-Pad U/D                  |
| +0x18  | 6    | A (analog 0..1)            |
| +0x1C  | 7    | B                          |
| +0x20  | 8    | X                          |
| +0x24  | 9    | Y                          |
| +0x28  | 10   | BLACK                      |
| +0x2C  | 11   | **WHITE** (roll)           |
| +0x30  | 12   | **LEFT TRIGGER** (cheat)   |
| +0x34  | 13   | RIGHT TRIGGER              |
| +0x38  | 14   | START (digital 0 or 1)     |
| +0x3C  | 15   | **BACK** (alt. roll gate)  |
| +0x40  | 16   | L-thumb click              |
| +0x44  | 17   | R-thumb click              |

✅ **Learning**: Azurik's button semantics are non-standard.
Any speed/cheat/etc. patch that gates on a controller-table
slot MUST be named after its physical button (WHITE, LT,
BACK), not its Azurik-internal label (`piVar7[0xf]`).  Both
the roll-rename and cheat-UI discoveries came from reading
`FUN_000a2880` side-by-side with the consuming state
handlers.

## index.xbr — the global asset-path index

Second orphan-looking file that's actually in the ``always``
stanza of ``prefetch-lists.txt`` — the streaming loader keeps
it resident throughout the game.

### Structure

168 KB file with exactly ONE TOC entry tagged ``indx``.  Payload
contains ~3,100 unique name strings (parser-extracted) and the
4-char type tags the game uses to disambiguate asset kinds:

| tag    | purpose                                      |
|--------|----------------------------------------------|
| ``surf`` | surface / material reference               |
| ``wave`` | audio or animation wave resource           |
| ``banm`` | bone animation (``b``one-``anm``)           |
| ``node`` | scene-graph node                           |
| ``body`` | character body mesh                        |
| ``gems`` | gem-pickup definition                      |
| ``indx`` | index entry self-tag                       |

Each name string is followed by a single discriminator byte
(``!``, ``"``, ``#``, …) which likely encodes an asset-version
or sub-type index.

### What it's used for (inferred)

Based on the tag distribution + prefetch-manifest placement:

- Global asset **directory** — maps every named asset (e.g.
  ``characters/garret4/body``) to a lookup record the engine
  uses to locate the data inside the other XBRs.
- **Always loaded** — so any level, any config value, any
  character spec can reference an asset by name without a
  chain of file-open calls.

We haven't fully decoded the index-entry record layout.  It's
not blocking anything today: level / character / effect mods go
through their native XBRs (``config.xbr``, ``characters.xbr``,
level files) rather than through this index.  If a future mod
wants to add NEW assets (not just modify existing ones), the
index will need to be extended too — tracked as a future
project in docs/ONBOARDING.md.

### Why we don't need to parse it further today

The ``config.xbr``-driven modding workflow we've built operates
entirely on keyed-table entries that ALREADY exist in the game.
We rename gems, swap power-ups, tweak drop tables — all
in-place edits to records the engine already indexes.  The only
time we'd need the index is to add *new* entity types, which
isn't on any current roadmap.

### Full record layout (April 2026 pass)

After the initial survey, a second RE pass decoded the actual
binary format:

**File layout:**

```
0x0000..0x0008  xobx magic + version
0x0040..0x0050  TOC (1 entry): indx tag, size 0x2713F
0x1000..0x1010  indx header (16 bytes)
0x1010..0x10000 record table (3071 entries × 20 bytes)
0x10000..EOF    string pool
```

**indx header (16 bytes at file offset 0x1000):**

| offset | field         | vanilla value | notes                         |
|--------|---------------|---------------|-------------------------------|
| +0x00  | count         | 3072          | declared records; 3071 real + 1 sentinel |
| +0x04  | version       | 4             | format version                |
| +0x08  | header_hint   | 24            | role unclear (NOT actual header size = 16) |
| +0x0C  | pool_hint     | 0xEFFC        | role unclear (probably pool size or offset)  |

**Record (20 bytes each):**

| offset | field        | type | notes                            |
|--------|--------------|------|----------------------------------|
| +0     | length       | u32  | string length for off1's string  |
| +4     | off1         | u32  | pool offset — appears to reference a FILE name (e.g. ``characters.xbr``) |
| +8     | fourcc       | char[4] | asset type: ``body``, ``banm``, ``node``, ``surf``, ``wave``, ``levl``, ``tabl``, ``font`` |
| +12    | disc         | u8   | subtype discriminator (0x10..0xFF) |
| +13    | pad          | u8[3] | zero padding                    |
| +16    | off2         | u32  | pool offset — appears to reference an ASSET KEY within the file at off1 |

**Tag distribution across 3071 records:**

- ``surf``: 1099 (surface / material references)
- ``wave``: 816 (audio blobs)
- ``banm``: 712 (bone animations)
- ``node``: 230 (scene-graph nodes)
- ``body``: 160 (character body meshes)
- ``levl``: 32 (level descriptors)
- ``tabl``: 18 (config tables)
- ``font``: 4 (font assets)

**String pool:**

Starts at file offset 0x10000 with:

- 4-byte magic dword: ``0x0001812D`` (role unclear)
- 4-byte tag: ``levl``
- Concatenated NUL-terminated asset paths (``characters.xbr``,
  ``characters/air_elemental/attack_1``, …)

### What remains uncharted

- Exact pool base for ``off1`` vs ``off2`` (they differ and the
  strings don't land cleanly at ``pool_start + offN`` — each
  record seems to carry some unknown prefix offset).
- Semantics of the two trailing header fields.
- Why ``count`` is 3072 when only 3071 entries are valid
  records (the 3072nd overlaps the pool magic).

Both the parser (:mod:`azurik_mod.assets.index_xbr`) and the
tests (``tests/test_index_xbr.py``) pin the decoded portions
and expose the raw fields so a follow-up RE session can
continue from here.

## Shim-system sanity check — April 2026

Cross-referenced the recent discoveries (hourglass + fx +
selector + index + prefetch audits) against the shipped shim
headers and ``vanilla_symbols.py`` registry.  New additions:

**``azurik.h`` VA anchors (4 added, now 20 total):**

- ``AZURIK_DEV_MENU_FLAG_VA`` (0x001BCDD8) — BSS flag that the
  selector.xbr loader reads.  Write non-``-1`` to force-load
  the dev menu.
- ``AZURIK_STR_LEVELS_SELECTOR_VA`` (0x001A1E3C) — string
  ``"levels/selector"``.
- ``AZURIK_STR_LEVELS_TRAINING_VA`` (0x001A1E4C) — string
  ``"levels/training_room"``.
- ``AZURIK_STR_INDEX_XBR_PATH_VA`` (0x0019ADB0) — string
  ``"index\\index.xbr"``.

**``azurik_vanilla.h`` / ``vanilla_symbols.py`` (2 added, now 9 total):**

- ``dev_menu_flag_check`` @ 0x00052F50 — the dispatcher that
  reads ``AZURIK_DEV_MENU_FLAG_VA`` and picks which level to
  load.  Purely documentary — a ``qol_enable_dev_menu`` shim
  won't call it, but referencing the function by name makes
  the one-line DIR32-store shim self-explanatory.
- ``load_asset_by_fourcc`` @ 0x000A67A0 — the index-table
  dispatcher.  Declared with a deliberately-wrong
  ``stdcall(8)`` signature so clang's mangling resolves to
  the right VA; a wrapper (gravity-style inline asm) will
  be needed before a real shim can call it.

**Coverage growth:** 76 → 82 unique Python-side VAs (16 → 20
anchors, 7 → 9 vanilla symbols, 53 patch-site VAs unchanged).

All four new anchors + both new vanilla entries are drift-
guarded by tests: ``tests/test_va_audit.py`` pins the bytes,
``tests/test_vanilla_thunks.py`` pins the header<->registry
equivalence.

## 60 FPS patch re-audit (April 2026)

Second-pass audit of ``fps_unlock`` against every frame-rate-
adjacent constant in ``default.xbe``'s ``.rdata``, motivated by
the fx.xbr record-layout RE + the new ``azurik-mod xbe
find-floats`` tooling.

### Scope

Exhaustive scan of ``.rdata`` for any IEEE 754 constant in the
``1/30``, ``30.0``, ``60.0``, or ``1/60`` neighbourhoods —
plus float64 counterparts for each.  Every hit cross-referenced
against ``FPS_DATA_PATCHED_VAS`` to classify:

- **Patched** ⇒ halved at apply time (no action needed).
- **Unpatched + frame-rate-dependent** ⇒ BUG, needs patching.
- **Unpatched + not frame-rate-dependent** ⇒ needs documenting
  so the regression guard doesn't flip on it.

### Findings

| Category    | Count | Status                                    |
|-------------|-------|-------------------------------------------|
| 1/30 f32    | 29    | 29/29 patched ✅                           |
| 1/30 f64    | 1     | 1/1 patched ✅                             |
| 30.0 f32    | 5     | 3 patched + 2 classified non-rate ✅       |
| 30.0 f64    | 1     | 1/1 patched ✅                             |
| 60.0 f32    | 6     | all 6 classified non-rate ✅               |
| 1/60 f32/64 | 0     | no baked-in "60 FPS assumed" math ✅       |

**Zero missed patches.**  The 2 unpatched 30.0 constants and all
6 of the 60.0 constants are in rendering / UI / threshold code
paths:

- ``0x0019FD98`` — threshold in ``FUN_0003EA00``
  (``if (30.0 < *(float*)(param_2 + 8))``) — speed / angle test,
  not a rate multiplier.
- ``0x001A2524`` — dead data (no .text xrefs).
- All 6 × 60.0 — FOV defaults (``FUN_00054800``) + screen-space
  UI scale math (``FUN_0005AC80`` etc.).  Decoded pattern:
  ``fVar13 = (float10)60.0; fptan(fVar13 * 0.5)`` — the classic
  camera-projection ``tan(fov/2)`` setup.

### fx.xbr-specific audit

The 3 ``fx_magic_timer`` XBE callsites (discovered during the
earlier fx.xbr audit) were re-decompiled:

- **``FUN_00083000``** — update: "if new > max, store".  No dt.
- **``FUN_00083050``** — spawn: reads stored max into new
  effect.  No dt.
- **``FUN_00083230``** — serialise: writes effects to save.
  No dt.

The effect-timer system stores values as *numbers*, not as
frame counts.  60 FPS unlock is SAFE w.r.t. fx.xbr.

### Regression guard

``tests/test_fps_coverage.py`` (new) pins the exact vanilla
counts so any future re-dump of the XBE that introduces a new
frame-rate constant (or drops one) flips red immediately.  The
test has six cases:

1. Every 1/30 f32 is patched
2. Every 1/30 f64 is patched
3. Every 30.0 f32 is patched OR classified as non-rate
4. Every 60.0 f32 is classified as non-rate
5. No 1/60 constants exist (the game is 30 FPS native)
6. Vanilla counts match the audit's ground truth

If (3) or (4) flips red, add the new VA to either the fps_unlock
patch set or the ``_NOT_FRAMERATE_*`` dict (with a Ghidra-xref
note in the comment).  If (1), (2), or (5) flips red, the
discovered constant IS a genuine frame-rate dep that needs
patching.

## Patch categories reorg (April 2026)

Second-pass at the category tab layout:

- **fps_unlock** moved from ``performance`` → ``experimental``.
  The patch triggers a pre-existing D3D push-buffer BSOD on
  player death (engine bug, unrelated to the patch bytes) and
  introduces visual-timing drift in a few subsystems we don't
  statically patch.  An ``experimental`` category signals
  "opt-in, keep a backup ISO" more clearly than ``performance``.
- **``randomize`` category added** — the five shuffle pools
  (``rand_major``, ``rand_keys``, ``rand_gems``, ``rand_barriers``,
  ``rand_connections``) are now first-class ``Feature`` entries
  with ``sites=[]`` + ``apply=noop``.  The Randomize page
  renders them via the same ``PackBrowser`` the Patches page
  uses, and the Patches page automatically grows a "Randomize"
  tab that mirrors the same state.
- The ``performance`` category is now EMPTY (fps_unlock was its
  only resident).  It stays registered so a future performance
  mod can slot into it without touching ``category.py``; the
  GUI hides empty categories from the tab strip.

## What to add here next

Things we haven't pinned down but should when a shim needs them:

- [ ] **Camera projection + FOV**.  Likely a `.rdata` float similar
      to gravity.  Quick win if found.
- [ ] **Player jump impulse**.  Tracked as `C-jump` in SHIMS.md.
- [ ] **`FUN_000d1420` / `FUN_000d1520` (config lookup)** —
      __thiscall; needs naked-asm wrappers to call from shims
      (same pattern as the gravity wrapper — see "Handling
      MSVC-RVO ABIs" above).

**Recently pinned** (as of this pass):

- [x] **Save-file format** — Xbox-standard container + 20-byte
      header decoded; per-level payload decoding deferred.  See
      `docs/SAVE_FORMAT.md` + `azurik_mod.save_format`.
- [x] **`FUN_00085700` (gravity integration)** — inline-asm
      wrapper shipped at `shims/shared/gravity_integrate.c`,
      exposed via `azurik_gravity_integrate()`.
- [x] **Controller input struct** — done, see section above.
- [x] **Drop-table fields in `CritterData`** — `range`, `range_up`,
      `range_down`, `attack_range`, `drop_1..5`, `drop_count_1..5`,
      `drop_chance_1..5` all exposed.  Offsets pinned against
      `FUN_00049480`.
- [x] **`entity_lookup` (FUN_0004b510)** — registered in
      `vanilla_symbols.py` as __fastcall.
