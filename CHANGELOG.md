# Changelog

## Unreleased

### Cleanup pass: deprecations + UI simplification (round 11.13)

**Slider range**: `flap_entry_fuel_cost_scale` widened from
`[-0.05, 0.05] step 0.001` to `[-5, 5] step 0.1` — the
threshold math tolerates this wider window for the per-flap
1.0f entry cost (unlike the punishing 100.0f descent penalty).

**Slider labels** — consistent "Wing-flap: …" prefix for the 5
working flap sliders:

| Old label | New label |
|---|---|
| Wing-flap height (1st flap) | Wing-flap: 1st flap height |
| Wing-flap height (2nd+ flaps) | Wing-flap: far-descent recovery |
| Wing-flap altitude ceiling | Wing-flap: altitude ceiling |
| Wing-flap descent fuel cost | Wing-flap: descent penalty fuel |
| Wing-flap per-flap fuel cost | Wing-flap: fuel cost per flap |

**Deprecations** (user testing confirmed no in-game effect):

- `animation_root_motion_scale` — vtable hook at 0x43066
  installs but scaling deltas before the vtable call produces
  no visible movement change.  `deprecated=True`, hidden from
  GUI, retained as a shim template.
- `slope_slide_speed` — state-3 constant overwrite + state-4
  shim at 0x8A095 install but don't affect observed slide
  speed.  `deprecated=True`, retained as RE scaffolding.

**UI simplification**:

- Removed the **Config Viewer** page — it was a read-only JSON
  dump of registry data that the Entity Editor already covers.
- Removed the **⚡ Apply to ISO** button from the Entity Editor
  — the Build tab's `_merge_config_edits` plumbing already
  merges pending Entity Editor edits into the
  `--config-mod` blob automatically.

**Entity Editor air_shield_3 Flaps investigation**: Ghidra
trace of `load_armor_properties_config` (FUN_0003C700)
confirms the engine loads `config/armor_properties` which
resolves to the keyed-table at config.xbr offset 0x3000 —
the same table the Entity Editor writes to under the label
`armor_properties_real`.  Cell double at offset+8 is read
via `config_cell_value`, FISTP-converted to int64, stored at
per-armor-slot `[offset 0x38]`.  The apply pipeline IS
correct (verified by `test_entity_editor_keyed_flaps.py`).
Users can verify edits landed via:

    azurik-mod dump -i built.iso -s armor_properties_real -e air_shield_3

The `dump` CLI was extended to fall back to keyed-table
parsing for sections that aren't in `config_registry.json`.

**Sync**: `load_armor_properties_config` (VA 0x3C700) registered
in `vanilla_symbols.py` + `azurik_vanilla.h`.  Ghidra plate
comments added at 0x3C700 (loader), 0x43066 (deprecated
vtable hook), 0x8A095 (deprecated slope-slide shim).

**Dead-code cleanup**:

- Dropped top-level `verify_patch_spec` import from
  `randomizer/commands.py` (it was duplicated inside
  `cmd_verify_patches`).
- Dropped unused `apply_player_speed` alias import.
- `no_fall_damage` pack file was already deleted in the
  round-10 purge; user manually confirmed the folder is
  gone.

910 tests pass.

### Docs + anchor sync pass (round 11.12)

Follow-up housekeeping on top of round 11.11:

- **`shims/include/azurik.h`**: added two new named anchors —
  `AZURIK_PATCH_FLAP_ENTRY_FUEL_VA` (VA 0x8934E, per-flap 1.0f
  entry drain) and `AZURIK_SHIM_HOOK_ANIM_APPLY_VTABLE_VA` (VA
  0x43066, vtable-commit hook inside `anim_apply_translation`).
  Both were already in use by the Python apply functions but
  weren't exposed as C-header constants.
- **`tests/test_va_audit.py`**: added both new anchor names to
  the physics allow-list so the drift audit covers them.
- **`docs/LEARNINGS.md`**: new "Wing-flap ENTRY fuel drain +
  `consume_fuel` threshold (round 11.11)" and "Animation
  root-motion vtable-commit hook (round 11.11, experimental)"
  sections documenting the full RE rationale for each.
- **`docs/PATCHES.md`**: player_physics slider count updated
  from 9 to 10; `animation_root_motion_scale` pack called out
  separately; round-10 purge paragraph re-framed as "selectively
  reversed" to reflect the round-11.8 restorations.
- **Ghidra**: pushed plate comments at VA 0x8934D (entry-fuel
  hook), VA 0x893CD (descent-fuel hook), and VA 0x43066
  (vtable-commit hook) with full RE rationale + slider names.
- **`gui/backend.py`**: dropped one unused pyflakes local
  (`except Exception as exc:` → bare `except Exception:`) in
  the catch-all around `redirect_stdout`.

Drift audit clean: all 48 mangled names in
`shims/include/azurik_vanilla.h` resolve in
`azurik_mod/patching/vanilla_symbols.py`; all physics patch
sites pinned by `AZURIK_PATCH_*_VA` macros correspond to
currently-used VAs in the Python apply functions.

### Entry fuel cost + Apply button + vtable-hook attempt (round 11.11)

**`flap_descent_fuel_cost_scale` range tightened**: changed from
``[-10.0, 1.0]`` step 0.05 to ``[-0.05, 0.05]`` step 0.001.  Most
of the old range was unusable (any cost > fuel_max×2 tripped the
clear-to-0 path); the new range stays within the "reasonable
per-flap cost" zone while still letting users reach refund
territory.

**New `flap_entry_fuel_cost_scale` slider**: scales the
``consume_fuel(this, 1.0)`` call at VA 0x89354 (the per-flap
drain that fires on EVERY flap regardless of altitude).  Same
PUSH-imm32 pattern as the descent-cost slider.

- 1.0 (default) — vanilla: 1/2/5 flaps per gauge for air1/2/3
- 0.1 — ~10× more flaps
- 0.0 — infinite flaps
- negative — gauge refills on every flap

**Entity Editor "Apply to ISO" button**: addresses user report
that edits didn't seem to take effect.  Pipeline audit
(tests/test_entity_editor_keyed_flaps.py) confirmed the apply
side IS correct, but the Build-page flow was opaque — users
weren't sure whether their edits actually reached the build.

New ⚡ Apply to ISO button on the Entity Editor toolbar:
1. Reads pending edits from ``self._edits``.
2. Calls ``get_pending_mod()`` to build the ``_keyed_patches`` /
   ``sections`` JSON blob.
3. Invokes ``cmd_patch`` directly on the user's selected ISO
   (blocking, ~5-15s for a full ISO rebuild).
4. Reports success/failure via messagebox.

Bonus fix: ``cmd_patch`` (invoked by ``azurik-mod patch``) now
handles ``_keyed_patches`` (previously only variant-section
``sections`` were applied).  Users can now ``Export Mod JSON``
from the Entity Editor and apply via the CLI as well.

**Config Editor tab** renamed to "Config Viewer" with a banner
redirecting to the Entity Editor for editing.  Making the JSON
viewer interactive would duplicate most of the Entity Editor; a
redirect is the honest UX.

**`animation_root_motion_scale` (experimental, round 11.11)**:
new pack taking a different approach to the root-motion problem.
Instead of hooking per-caller (like the deprecated
``root_motion_roll`` / ``_climb`` shims did, at 0x866D9 / 0x883FF
AFTER the vanilla CALL returned — too late because the vtable
commit fires inside ``anim_apply_translation``), this shim
hooks the **vtable-commit call itself** at VA 0x43066 — inside
``anim_apply_translation``, just before the commit.  The shim
scales ``param_1[0x6C..0x71]`` (the 6 translation-delta floats)
by the user scale, THEN invokes ``CALL [EAX+0xC0]`` so the
commit sees the scaled deltas.

Trade-off: scales EVERY animation-root-motion translation
globally — roll, climb, jump-forward, swim, slide, flap, and
any NPC / scene animation go through this hook.  That's ~15
caller sites in the player state ticks plus a few non-player
paths.  Users can verify the mechanism by setting the slider
to something dramatic (2.0 or 0.5); if it works, a follow-up
can add per-state gating via a caller-set flag.

The per-caller ``root_motion_roll`` and ``root_motion_climb``
packs stay deprecated (hidden from GUI but registered for CLI
back-compat).

910 tests pass total.

### Descriptions pass + fuel-cost math fix + deprecations (round 11.10)

**Descriptions**: Added tooltip descriptions to every slider
(previously only 3 sliders had them); trimmed the 5 long pack
descriptions for consistency:

| | Before | After |
|---|---|---|
| `gravity` (slider) | no description | 233 chars |
| `walk_speed_scale` (slider) | no description | 197 chars |
| `swim_speed_scale` (slider) | no description | 143 chars |
| `jump_speed_scale` (slider) | no description | 135 chars |
| `air_control_scale` (slider) | no description | 185 chars |
| `wing_flap_ceiling_scale` (slider) | 121 chars | same |
| `flap_descent_fuel_cost_scale` (slider) | 157 chars | 440 chars (now explains the threshold-clear math) |
| `flap_at_peak` (pack) | 113 chars | 113 chars (unchanged round 11.9) |
| `root_motion_roll` / `_climb` (pack) | 157 / 119 chars | unchanged |

Every slider now has a concise, mechanism-level tooltip that
explains what the slider does + one-sentence "what this
DOESN'T affect" disambiguation where relevant.

**`flap_descent_fuel_cost_scale` math clarified + range expanded**:

User reported that values between 0 and 1 still cleared the
gauge in a single flap.  Ghidra-verified: vanilla
``consume_fuel(this, cost)`` does
``fuel_current -= cost/fuel_max`` and THEN clears to 0 if the
new ``fuel_current < 0.5 × cost/fuel_max``.  Vanilla
``fuel_max`` is just 1 (air1), 2 (air2), or 5 (air3), so any
positive ``cost > fuel_max × 2`` triggers the clear-to-0 path
— which is every positive scale for air1/air2 and most for
air3.

Slider range expanded from ``[0.0, 1.0]`` to ``[-10.0, 1.0]``
so users can reach the useful values:

- ``1.0`` (default) — vanilla drain (gauge clears in 1 flap)
- ``0.0`` — no drain (descent flaps cost only the first-flap 1.0)
- negative — REFUND (each descent flap ADDS fuel back)

Tooltip rewrites to lay out the arithmetic explicitly.

**`slope_slide_speed` enhanced**: now patches BOTH state-3
(slow slide, constant overwrite at VA 0x1AAB68) AND state-4
(fast slide, shim at VA 0x8A095) — previously only state-4 was
covered.  Single slider drives both.

**Deprecations**: added a ``deprecated: bool`` flag to
``Feature`` / ``PatchPack``.  Deprecated packs stay registered
(CLI / tests / direct ``apply_pack`` still work) but the GUI's
``PackBrowser`` hides them so casual users don't stumble into
a checkbox that doesn't produce the expected effect.

Marked deprecated as of round 11.10:

- `root_motion_roll` — user-verified the shim has no observable
  in-game effect even after the round-11.6 wiring fix.  Probable
  cause: ``anim_apply_translation`` commits the translation
  deltas via vtable+0xC0 INSIDE the function, before our
  post-CALL shim scales them.
- `root_motion_climb` — same root cause.

Slope-slide stays active (with state-3 enhancement).
flap_at_peak stays active (superseded but composes
orthogonally with wing_flap_ceiling_scale).

**Entity Editor investigation (armor Flaps)**: user reported
edits to `armor_properties_real.Flaps` weren't applying.  Full
pipeline audit (new test `test_entity_editor_keyed_flaps.py`):

- `get_pending_mod()` emits the correct ``_keyed_patches``
  shape.
- ``cmd_randomize_full``'s keyed-patch loop correctly writes
  doubles at ``cell_offset + 8``.
- Re-read confirms the values landed.

Conclusion: the apply pipeline IS correct.  The user-reported
"doesn't apply" must be a UI-interaction issue (e.g. build ran
without a freshly-loaded ISO populating ``_keyed_tables``, or
the entity-combo didn't show `air_shield_*` rows).  Four new
tests pin the invariant.  **Workaround** for users who hit the
UI issue: use ``azurik-mod randomize-full --config-mod
flaps.json`` directly with a hand-edited JSON file:

```json
{"_keyed_patches": {"armor_properties_real":
  {"air_shield_1": {"Flaps": 10.0},
   "air_shield_2": {"Flaps": 20.0},
   "air_shield_3": {"Flaps": 50.0}}}}
```

910 tests pass total (+6).

### GUI: info-tooltip system + concise descriptions (round 11.9)

User reported that slider descriptions were crowding the
Patches page.  Addressed both sides:

**Infrastructure** — `Tooltip` helper class +
`attach_info_tooltip()` factory in `gui/widgets.py`:

- Hover-reveal popup bound to any `tk.Widget` via
  `<Enter>` / `<Leave>` events.
- Configurable show delay + grace-period hide so the cursor
  can move from trigger into the tooltip without the popup
  closing mid-read.
- Survives the trigger widget's destruction (no leaked
  Toplevels).
- `attach_info_tooltip(parent, text)` returns a styled `ⓘ`
  glyph Label with tooltip pre-wired, or `None` when `text`
  is empty so the caller can skip packing it.

**UI migration**:

- `ParametricSlider` — the multi-line wrapped description
  that used to render directly under the bold label is now
  an `ⓘ` glyph next to the label; hover shows the tooltip.
  Default view stays compact; users opt in to the detail.

**Content pass** — tightened every long description while
keeping the load-bearing info:

| Slider / pack | Before | After |
|---|---|---|
| `wing_flap_ceiling_scale` (slider) | 346 chars | 121 chars |
| `flap_descent_fuel_cost_scale` (slider) | 281 chars | 157 chars |
| `flap_at_peak_scale` (slider) | 325 chars | 191 chars |
| `roll_speed_scale` (slider) | 311 chars | 162 chars |
| `climb_speed_scale` (slider) | 253 chars | 158 chars |
| `slope_slide_speed_scale` (slider) | 203 chars | 113 chars |
| `flap_at_peak` (pack) | 338 chars | 113 chars |
| `root_motion_roll` (pack) | 316 chars | 157 chars |
| `player_physics` (pack) | 261 chars | 135 chars |
| `qol_skip_save_signature` (pack) | 246 chars | 150 chars |
| `root_motion_climb` (pack) | 240 chars | 119 chars |

Every slider description now fits on 1-2 lines in the
tooltip popup; every pack description fits on 1-2 lines in
the inline pack-row layout at `wraplength=620`.

**Tests** (`tests/test_gui_tooltip.py`, 9 new):

- `Tooltip` import-surface stability.
- `attach_info_tooltip("")` returns `None`; non-empty returns
  a Label widget with the ⓘ glyph.
- Popup lifecycle: shows on `_show()`, hides on `_hide()`;
  `set_text` re-renders an open popup; destroying the trigger
  widget cleans up the popup.
- `ParametricSlider` contract: when a description is set,
  renders the ⓘ glyph AND does NOT render the full
  description as an inline standalone label.  When empty,
  renders neither.

904 tests pass total (+9).

### Restored 4 shim-backed physics packs (round 11.8)

User-requested restoration of the four shim packs deleted in
round 10.  The round-11.6 forensic confirmed that the deletion
rationale ("doesn't work in-game") was likely a GUI wiring
false-negative — the backend silently dropped slider values for
every pack other than `player_physics`, so the shims always
applied with scale=1.0 (no-op) regardless of user input.

Now that the generic `pack_params_json` channel (round 11.6)
guarantees slider values reach `apply_pack`, users can directly
validate whether each shim produces in-game effect.

Restored packs (from git history at `d20c4dd^`):

| Pack | Hook VA | Shim body | Slider |
|---|---|---|---|
| `flap_at_peak` | 0x89409 | 43 B | `flap_at_peak_scale` |
| `root_motion_roll` | 0x866D9 | 134 B | `roll_speed_scale` |
| `root_motion_climb` | 0x883FF | 128 B | `climb_speed_scale` |
| `slope_slide_speed` | 0x8A095 | 17 B | `slope_slide_speed_scale` |

All four register via `register_feature(...)` at pack import
time from `azurik_mod/patches/__init__.py`.  60 pack-level
tests restored alongside the source; all pass.

**Clarification on `flap_at_peak` vs `flap_below_peak`**: users
sometimes conflate them.  They target different sites in
`wing_flap` with different semantics:

- `flap_below_peak_scale` — scales the ×0.5 halving factor at
  VA 0x893DD (descent-penalty branch, `fVar1 > 6`).
- `flap_at_peak_scale` — enforces a v0 floor at the final FSTP
  (VA 0x89409), bypassing the `min(fVar1, fh)` clamp near peak.

Neither is redundant with the other; both are useful knobs.
`flap_at_peak` is mostly SUPERSEDED by `wing_flap_ceiling_scale`
(which raises the `peak_z` latch) but remains as an independent
impulse-floor mechanism.

**Header / registry sync**: five new VA anchors promoted in
`azurik.h` (`AZURIK_SHIM_HOOK_FLAP_AT_PEAK_VA`,
`AZURIK_SHIM_HOOK_ROLL_ANIM_APPLY_VA`,
`AZURIK_SHIM_HOOK_CLIMB_ANIM_APPLY_VA`,
`AZURIK_SHIM_HOOK_SLOPE_FAST_SLIDE_VA`,
`AZURIK_ANCHOR_PEAK_Z_FSTP_VA`) to replace the old "deleted
in round 10" comment block.  VA-audit drift test updated to
cover them.  Ghidra plate comments pushed at each hook address
documenting the restoration + the round-10 deletion history
+ the round-11.6 wiring-bug forensic.

**`test_categories.py`** updated: the `slider_keys` expected
list expands from 9 (player_physics only) to 13 (adds the 4
restored shim packs' single virtual slider each).

895 tests pass total (+60 from the restored pack tests).

### Wing-flap descent fuel-cost slider (round 11.7)

Companion to `wing_flap_ceiling_scale`.  User reported that
raising the ceiling alone isn't sufficient because vanilla
`wing_flap` has a second, independent anti-recovery mechanic:
when the player has fallen > 6m below their latched `peak_z`,
a `consume_fuel(this, 100.0)` call drains the entire air-power
gauge in a single flap.  After that the next flap's entry
`consume_fuel(this, 1.0)` returns 0 (refuses) and the flap
doesn't happen — perceived as "flaps fail when I descend".

The `flap_descent_fuel_cost_scale` slider (default 1.0 vanilla,
range 0.0–1.0) rewrites the 4-byte `PUSH imm32` (100.0f) at
VA 0x893CE feeding the consume_fuel call at VA 0x893D4.  Setting
to 0.0 pushes 0.0f instead; `consume_fuel(this, 0.0)` drains
nothing but still returns success, so the flap proceeds and the
gauge stays intact.

Why a PUSH-immediate rewrite instead of overwriting the 6.0
threshold at VA 0x001A25B8: that shared `.rdata` constant has
19 unrelated readers across the binary (movement, physics,
input, audio — not all obviously height-related; likely a
compiler-deduplicated numeric literal).  Scaling the PUSH
argument is code-surgical to this one call site; overwriting
the data constant would corrupt 18 other codepaths.

**Pair with `wing_flap_ceiling_scale`**: set both to unlock
high-altitude recovery flights (`wing_flap_ceiling_scale = 5`
+ `flap_descent_fuel_cost_scale = 0.0`).  Optionally add
`flap_below_peak_scale = 2.0` to cancel the v0 halving that
runs in the same branch.

**Ghidra annotations pushed**: plate comments at VA 0x893CD
(hook site) and VA 0x893C0 (6.0 threshold branch) documenting
the full mechanic and the 19-reader threshold constraint.

**Tests** (`tests/test_flap_descent_fuel_cost.py`): 14 new,
covering descriptor shape, byte landing at zero / fractional /
vanilla values, drift detection, byte-surgical assertions that
the surrounding `PUSH` opcode and `MOV ECX, EDI` survive, and
a crucial guard that the shared 6.0 threshold at 0x001A25B8
stays untouched.  835 pass total.

### Generic pack_params wiring + forensic on retired packs (round 11.6)

Follow-up to round 11.5's `wing_flap_ceiling_scale` /
`flap_below_peak_scale` fix.  Investigated whether the same
wiring-bug class affected earlier "doesn't work in-game" reports.

**Forensic finding**: at least 4 of the 7 patches purged in
round 10 were probably deleted prematurely:

| Pack | GUI wiring at test time | Likely verdict |
|---|---|---|
| `flap_at_peak` (round-8 shim pack) | broken — backend never extracted `pack_params["flap_at_peak"]` | **GUI false-negative** |
| `root_motion_roll` (round-8 shim pack) | broken — same | **GUI false-negative** |
| `root_motion_climb` (round-8 shim pack) | broken — same | **GUI false-negative** |
| `slope_slide_speed` (round-8 shim pack) | broken — same | **GUI false-negative** |
| `no_fall_damage` | boolean pack, wired via `packs` dict | legit RE failure |
| `infinite_fuel` | boolean pack, wired via `packs` dict | legit RE failure |
| `wing_flap_count` | boolean pack, wired via `packs` dict | legit RE failure |

Pre-fix, the GUI backend only extracted
`pack_params["player_physics"]` out of the whole nested dict;
slider values for every other pack were silently dropped before
reaching the apply pipeline.  Users who tested via GUI would see
the non-default values in the build log's `# pack_params:`
header, then the randomizer would call `apply_pack(pack,
xbe_data, {})` with empty params → the slider's apply function
ran with default 1.0 → no-op → "doesn't work".

The 4 affected round-8 shim packs are NOT being restored (their
git history carries reasonable apply code, but re-verifying each
one in-game is a separate workstream the user can drive if
interested — for now the deletion stands but is documented as
probably-premature rather than proven-ineffective).

**Fix**: added a generic `pack_params_json` argparse channel.
`gui/backend.py` now serialises the ENTIRE nested `pack_params`
dict (all packs, not just `player_physics`) into a single
JSON field on the randomizer's Namespace.
`cmd_randomize_full` deserialises + merges it into its local
`_PACK_PARAMS` before the `apply_pack` loop, with CLI-origin
values winning on key collisions.  Any future pack with slider
params automatically gets its GUI values forwarded — no
per-pack argparse field edits required.

**Regression tests**:
[`tests/test_gui_randomizer_slider_wiring.py`](tests/test_gui_randomizer_slider_wiring.py)
pins:

- `pack_params_json` round-trips verbatim for any pack name
  (including ones not yet registered)
- Merge semantics: GUI fills gaps in CLI-derived params; CLI
  wins on key collisions; malformed JSON degrades gracefully
- **Every `ParametricPatch` on every registered pack**
  automatically round-trips a non-default value from
  `pack_params` through to the final `apply_pack` call — this
  is the belt-and-suspenders guard that catches the bug class
  for any future slider on any future pack

11 new tests; 821 pass total.

**Cleanup**: removed 5 dead imports from
`azurik_mod/randomizer/commands.py` (`apply_pickup_anim_patch`,
`apply_skip_logo_patch`, `apply_gem_popups_patch`,
`apply_other_popups_patch`, `file_to_va` — all stale after the
`apply_pack` dispatcher took over).

### GUI slider wiring fix (round 11.5)

**Bug**: The GUI's `build_randomized_iso` translation layer only
forwarded 7 of the 8 active `player_physics` sliders from
`pack_params` into the argparse Namespace that `cmd_randomize_full`
consumes.  Users who dragged `wing_flap_ceiling_scale` or
`flap_below_peak_scale` in the Patches GUI would see the
non-default values in the build log's `# pack_params: {...}`
header but **the values never reached the apply pipeline** —
`getattr(args, 'player_wing_flap_ceiling_scale', None) or 1.0`
short-circuited to the vanilla path and the XBE got built without
either shim installed.  From the user's perspective the slider
simply had no effect.

Reported scenario from
`~/Library/Logs/azurik_mod/build-20260420-025618-seed42.log`:
`flap_height_scale=2.0, flap_below_peak_scale=100.0,
wing_flap_ceiling_scale=100.0` all set in the GUI.  Build log
showed only the flap_height_scale apply line; the other two
slider installs were silently skipped.

**Fix**: Extracted the translation into the new
[`physics_params_to_namespace_fields`](gui/backend.py) helper
driven by `_PHYSICS_PARAM_TO_NAMESPACE`, a single-source-of-truth
table mapping `ParametricPatch.name` → `argparse` field name +
vanilla default.  Every active slider on
`PLAYER_PHYSICS_SITES` appears exactly once; adding a new slider
is a one-line table edit.  Legacy aliases (`run_speed_scale`,
`run_scale`, `roll_scale`, `flap_subsequent_scale`) are resolved
to their canonical form before table lookup so pre-April-2026
serialised param dicts keep working.

**Regression test**:
[`tests/test_gui_backend_params.py`](tests/test_gui_backend_params.py)
pins (a) 100% coverage of `PLAYER_PHYSICS_SITES` entries by the
translation table with an allowed-exception list for
retired-but-still-accepted sliders, (b) inverse: no dead
translation entries, (c) reproduction of the exact bug-report
scenario, (d) every vanilla-default value round-trips to `None`
so byte-identity is preserved when sliders haven't moved.  15
new tests; 810 pass total.

### Wing-flap altitude-ceiling slider (round 11 cont.)

New `wing_flap_ceiling_scale` slider on `player_physics` that
raises (or lowers) the wing-flap altitude cap independently of
every other physics knob.  Defaults to 1.0 (vanilla).

**Background**: vanilla `player_jump_init` latches
`peak_z = entity.z + flap_height` once and never refreshes it,
making the wing-flap altitude cap equal to `flap_height` above
the jump's starting ground.  `flap_height_scale` and
`flap_below_peak_scale` scale the per-flap v0 impulse, but
neither lifts the ceiling — once the player's above `peak_z`,
both produce zero v0.  See `docs/LEARNINGS.md` §
"Wing-flap v0 cap" for the full mechanics.

**Hook**: 6-byte `FADD [ESI+0x144]` at VA 0x89154 (confirmed
single writer path to player `peak_z`; exhaustive XBE scan for
FSTP/FST/MOV `[reg+0x164]` ruled out interference).

**Shim**: 15-byte hand-assembled via `shim_builder`:

```asm
FLD   [ESI+0x144]   ; load flap_height
FMUL  [scale_va]    ; × ceiling_scale
FADDP ST1           ; pop, add to entity.z already on ST0
RET                 ; resume at 0x8915A (vanilla FSTP)
```

Installed as a 5-byte `CALL rel32` + 1-byte NOP at the vanilla
FADD.  Net effect:
`peak_z = entity.z + K * flap_height`.  The wing-flap hard
ceiling therefore becomes `(K+1) * flap_height` above the
ground the player jumped from (vanilla K=1 → 2·flap_height,
because `wing_flap`'s own formula adds another flap_height on
top of peak_z).  Concretely: K=2 ≈ 1.5× vanilla headroom, K=5
≈ 3×, K=10 ≈ 5.5×.  Orthogonal to `flap_height_scale`
(per-flap v0) and `flap_below_peak_scale` (>6m halving).

**Why this round works when rounds 7–10 didn't**: the earlier
`flap_at_peak` shim attempts hooked *downstream* of peak_z (at
the min-cap in `wing_flap`, the final vz FSTP, or animation
root-motion).  Every one of those was overridden by a later
consumer.  Round 11 hooks *upstream* at the peak_z latch
itself, changing what "peak" means rather than fighting the
downstream clamp.

Threaded through the full apply surface: `apply_wing_flap_ceiling`
Python API, `--player-wing-flap-ceiling-scale` on
`randomize-full`, `--wing-flap-ceiling` on `apply-physics`, and
GUI slider on the Patches page.  New anchor
`AZURIK_PATCH_PEAK_Z_FADD_VA = 0x00089154` in `azurik.h`.

**Tests**: 17 new tests in `tests/test_wing_flap_ceiling.py`
covering descriptor shape, hook byte pinning, apply / reapply
behaviour, shim body shape, scale-float injection decode,
end-to-end routing via `apply_player_physics` + `_custom_apply`,
and dynamic-whitelist coverage for both vanilla and patched
states.  795 tests pass (+17 over round 11 initial).

### Per-apply float injection for C shims (round 11)

New capability on the C-shim platform: late-bound float constants
that the apply pipeline rewrites at patch time instead of at
compile time.  A single compiled `.o` now serves every slider
value — no recompile when the user drags the slider.

**C side**: new `AZURIK_FLOAT_PARAM(name, default)` macro in
[`shims/include/azurik.h`](shims/include/azurik.h).  Expands to
a `volatile const float` in `.rdata` so clang emits a DIR32 load
instead of folding the value into an immediate.

**Python side**: new [`FloatParam`](azurik_mod/patching/spec.py)
NamedTuple, threadable as
`TrampolinePatch(..., float_params=(FloatParam(...),))`.  At
apply time:

1. [`layout_coff`](azurik_mod/patching/coff.py) places every
   section as before.
2. For each declared `FloatParam`, the new
   [`find_landed_symbol`](azurik_mod/patching/coff.py) helper
   resolves the COFF symbol to its landed ``.rdata`` section +
   intra-section offset.
3. `apply_trampoline_patch` writes
   ``struct.pack("<f", params[name])`` (or the declared default)
   at that file offset — after relocations have been applied,
   so the shim's DIR32 load still targets the right VA.

`apply_pack` forwards its `params` dict through to the
trampoline apply path automatically; packs with no float_params
are unaffected.

**Testing**: [`shims/fixtures/_float_param_test.c`](shims/fixtures/_float_param_test.c)
+ [`tests/test_float_param_injection.py`](tests/test_float_param_injection.py).
11 new tests (778 passing total, +11 over round 10).

**Docs**: new "Per-apply float injection" subsection in
[`docs/SHIM_AUTHORING.md`](docs/SHIM_AUTHORING.md) § 6.

**Drift sync**: fixed 6 false mangled-name comments in
[`shims/include/azurik_vanilla.h`](shims/include/azurik_vanilla.h)
(`player_walk_state`, `player_airborne_tick`, `player_climb_tick`,
`consume_fuel`, `anim_apply_translation`, `anim_change` — all
`cdecl` / `thiscall`, neither of which emits an `@N` suffix on
clang-i386-pe-win32 despite round-9 header comments claiming
otherwise).  Comments now reflect the actual mangled names.

### Player-pack triage + shim-revival cleanup (round 10)

User-driven purge after in-game testing confirmed that several
byte-patch and shim-backed attempts from rounds 8–9 were
ineffective.  When a mechanic has a cleaner config-editor or
unreachable alternative, we delete rather than keep dead code.

**Deleted packs (config-editor alternatives exist)**:

- `no_fall_damage` — use config-editor damage thresholds or
  `critters_damage` hitPoints.
- `infinite_fuel` — use config-editor
  `armor_properties.fuel_max` or zero `attacks_anims` Fuel
  multipliers.
- `wing_flap_count` — use config-editor `armor_properties`
  Flaps column.

**Deleted packs (no working hook found)**:

- `flap_at_peak` — three hook attempts (FSUB NOP at 0x89381,
  FLD-ST1 rewrite at 0x8939F, final-FSTP shim at 0x89409) all
  landed bytes cleanly but produced no in-game effect.  The
  near-peak weak-v0 behaviour is governed by state we never
  successfully intercepted.
- `root_motion_roll` — roll is animation root-motion driven.
  Shim at 0x866D9 wrapping `anim_apply_translation` landed
  bytes but gave no in-game effect (post-CALL param scaling
  happens after the vtable commit has already consumed the
  deltas).
- `root_motion_climb` — same story at 0x883FF in
  `player_climb_tick`.
- `slope_slide_speed` — state-4 fast slide uses a dynamic 500x
  multiplier from surface normal; neither the 4-byte constant
  overwrite at 0x1AAB68 nor the FLD shim at 0x8A095 moved the
  needle.

**`player_physics` back-compat surface kept; apply functions
retired as no-ops**:

- `apply_flap_at_peak` / `apply_climb_speed` /
  `apply_slope_slide_speed` now always return `False` and leave
  bytes untouched.  Their corresponding slider kwargs on
  `apply_player_physics` (`flap_at_peak_scale`, `climb_scale`,
  `slope_slide_scale`) are accepted for back-compat but do
  nothing.  GUI sliders and CLI flags for these were removed in
  the same pass.

**Wing-flap behaviour clarified (user theory, confirmed by RE)**:

The "wing-flap height (subsequent flaps)" slider can look like
it doesn't work.  The root cause is subtle:

- Vanilla wing-flap v0 = `sqrt(2g * fVar2)` where
  `fVar2 = min(peak_z + flap_height - current_z, flap_height)`.
- `flap_height_scale` rewrites the FLD inside `wing_flap` at
  0x893AE; that scales `g` *for every flap* (not just the
  first).  However, at peak the altitude-remaining term clamps
  `fVar2` to zero — and scaling `g` by anything still gives
  `v0 = 0`.
- `flap_below_peak_scale` only changes v0 for flaps taken
  >6 m below peak (the halving path at 0x893DD).

So the player's observation "subsequent flaps depend heavily on
where I am relative to peak" is exactly what vanilla computes
(intentional anti-infinite-altitude design).  Our sliders apply
to every flap, but zero-times-anything is still zero near peak.

**Docs**: `docs/LEARNINGS.md` § "Retired physics sliders" has
the per-hook rationale.

**Tests**: 767 pass (was 831 after the round-10 deletions;
earlier rounds had transient test growth).  Test surface shrank
by ~65 tests when the four shim-backed packs + three
config-editor-alternative packs were removed.

### Shim infrastructure + cleanup pass (round 9)

Follow-up to the round-8 shim revival: extract the boilerplate
every hand-assembled pack was carrying, clean up dead code, fix
stale docs.

**New shared shim-authoring helper** at
[`azurik_mod/patching/shim_builder.py`](azurik_mod/patching/shim_builder.py).
Packages the install pipeline (vanilla-drift check + idempotent
re-apply detection + carve scale/data block with 0xFF sentinel +
carve body placeholder + assemble + install E8/E9 rel32
trampoline + trailing NOP pad) plus a matching
`whitelist_for_hand_shim` callback and small emitters
(`emit_fld_abs32`, `emit_fmul_abs32`, `emit_fstp_abs32`,
`emit_jmp_rel32`, `emit_call_rel32`, `with_sentinel`).

Before the refactor each of the 5 hand-assembled packs carried
~80-150 LoC of pipeline boilerplate.  After: the 4 round-8 packs
(`flap_at_peak`, `slope_slide_speed`, `root_motion_roll`,
`root_motion_climb`) express their unique logic — the body
builder — and delegate everything else.  Net reduction of ~250
LoC across the four.  `wing_flap_count` stays on its own
hand-rolled path for now (retired + slightly different data-
block shape).

**New docs** in [`docs/SHIM_AUTHORING.md`](docs/SHIM_AUTHORING.md)
§ "Hand-assembled (Python) shims vs C shims" — when to pick
which approach, the blessed pattern, and the `__thiscall`
detour pattern with stack-shift arithmetic for wrapping a
vanilla CALL from inside a shim.

**Test fixture consolidation**.  Five test modules used to
duplicate the same ``_XBE_CANDIDATES`` + ``_XBE_PATH`` + skip-
decorator block.  Centralised in
[`tests/_xbe_fixture.py`](tests/_xbe_fixture.py) with a single
``require_xbe`` decorator; net saves ~50 LoC across tests.

**Cleanup**:

- Removed `_VANILLA_FLAPS = {1:1, 2:2, 3:5}` (dead constant in
  `wing_flap_count`).
- Removed unused `parse_xbe_sections` import in
  `wing_flap_count`'s whitelist function.
- Fixed stale docstring sizes: `root_motion_climb` body was
  labelled 130 B (actual 128), `wing_flap_count` whitelist
  doc said 50 B (actual 47).
- Fixed misleading "C shim" label in `flap_at_peak`
  description (it's a hand-assembled x86 shim, not a C shim).
- Reconciled `docs/LEARNINGS.md` § "Wing-flap v0 cap" — the
  section still read as "retired, write a C shim"; now
  correctly reflects the round-8 shipped shim at 0x89409 and
  frames the cap as intentional vanilla design.
- Updated `docs/PATCHES.md` retired-slider paragraph to point
  at the shim-backed revival table instead of the old
  "byte patches have no effect" text.

**Test suite**: 859 pass (was 845), 775 subtests (was 775).
The +14 net tests are all new coverage for `shim_builder`.

### Player packs — round 8 (shim revival of 4 retired sliders)

Four of the previously-retired physics sliders are now shipping
as their own shim-backed packs — hand-assembled trampolines at
code-flow points that byte patches couldn't reach.  Each pack
ships with a shim body, a ParametricPatch slider, spec-shape +
byte-landing + idempotent-re-apply unit tests, and a
`dynamic_whitelist_from_xbe` callback so `verify-patches
--strict` accepts the trampoline + shim + injected-scale bytes.

**Revived packs:**

- `flap_at_peak` (VA 0x89409 — final z-velocity FSTP inside
  `wing_flap`).  43-byte shim.  Wraps the final velocity write
  with `max(vanilla_v0, sqrt(2g*flap_height)*scale)`.  Bypasses
  the intentional vanilla "no altitude above first-flap height"
  ceiling, which is latched in `player_jump_init` at 0x8915A.
- `slope_slide_speed` (VA 0x8A095 — state-4 fast-slide FLD).
  17-byte shim.  Simplest of the four: replays the FLD then
  FMULs by user scale before the downstream velocity
  integration consumes the value.
- `root_motion_roll` (VA 0x866D9 — CALL to
  `anim_apply_translation` from `player_walk_state`).  134-byte
  shim.  Wraps the vanilla anim-apply, post-scales the
  translation deltas at `param_1[0x6C..0x71]` **only while
  PlayerInputState.flags & 0x40 is set** (WHITE/BACK roll gate).
  Normal walking stays vanilla.
- `root_motion_climb` (VA 0x883FF — CALL to
  `anim_apply_translation` from `player_climb_tick`).  128-byte
  shim.  Same pattern as roll but no gate — scales
  unconditionally inside climb_tick.

**Three packs stay retired** (config-editor workarounds
documented in their descriptions): `no_fall_damage`,
`infinite_fuel`, `wing_flap_count`.  All three land bytes
correctly but gameplay-side evidence suggests other code paths
dominate.

**Headers + Ghidra sync:**

- Added `anim_apply_translation` (renamed from FUN_00042E40) and
  `anim_change` (renamed from FUN_00042910) to `azurik_vanilla.h`
  and `vanilla_symbols.py`.  Also added `player_armor_state_tick`
  (VA 0x83D80) which was macro-only before.  Ghidra renamed +
  plate-commented at both VAs.
- Fixed `apply_damage` calling-convention mismatch:
  `vanilla_symbols.py` was `cdecl`, verified via Ghidra `RET 0xC`
  at 0x44735 that stdcall is correct (matching `azurik_vanilla.h`).
- Added 8 new VA macros to `azurik.h`:
  `AZURIK_FUN_ANIM_APPLY_TRANSLATION_VA`,
  `AZURIK_FUN_ANIM_CHANGE_VA`,
  `AZURIK_FUN_PLAYER_PHYSICS_INTEGRATE_ROOT_MOTION_VA`,
  `AZURIK_PATCH_FLAP_V0_FINAL_FSTP_VA`,
  `AZURIK_PATCH_PEAK_Z_WRITE_VA`,
  `AZURIK_SHIM_HOOK_WALK_ANIM_APPLY_VA`,
  `AZURIK_SHIM_HOOK_CLIMB_ANIM_APPLY_VA`,
  `AZURIK_SHIM_HOOK_SLOPE_VEL_FLD_VA`.
- 8 Ghidra plate comments added at the shim hook addresses +
  the peak-z-latching site for future RE continuity.
- `tests/test_va_audit.py` extended with the 8 new macros.

**Design note**: the flap_at_peak shim works *around* vanilla
rather than fixing a bug.  Vanilla deliberately latches
`peak_z` once per jump and caps subsequent-flap altitude at
`initial_flap_z + flap_height` as an anti-infinite-altitude
mechanic.  Below-peak flaps (controlled by the existing
`flap_below_peak_scale` byte patch) are supported vanilla
behaviour; above-peak flaps are not.  The shim documents this
framing in its pack docstring + `docs/LEARNINGS.md` §
"Wing-flap v0 cap" so future agents don't chase more byte
patches hoping to find a "fix".

**Test suite grows from 811 → 845 tests + 761 subtests.**
Four new pack test modules plus updated ``test_categories.py``
to expect the 3 additional sliders.

### Player packs — round 7 (retire broken physics sliders + packs)

After further user testing in late April 2026, three more player
physics sliders joined `roll_speed_scale` in retirement, and the
three "config-editor workaround" packs dropped their registry
entries entirely.  Every retired item's ParametricPatch / Feature
spec stays defined (tests covering byte landings still run), but
none are surfaced in the GUI or routed through the randomizer
CLI anymore.

**Retired sliders** (`PLAYER_PHYSICS_SITES` drops from 10 → 7):

- `flap_at_peak_scale` — v1 NOP at 0x89381 and v2 FLD-ST rewrite
  at 0x8939F both land cleanly but produce no observable effect.
  The engine appears to re-derive the v0 cap downstream.  Users
  consistently reported flap_at_peak flaps still give zero v0.
- `climb_speed_scale` — the 2.0 constant at 0x1980E4 IS the
  climbing velocity scalar, but climbing motion (like rolling)
  is animation-root-motion driven; the scalar isn't the
  dominant term.
- `slope_slide_speed_scale` — the 2.0 constant at 0x1AAB68 only
  governs the state-3 (slow slide) path.  State-4 fast slides
  use a dynamic 500× multiplier that isn't a static byte we can
  rewrite.

**Retired packs** (no longer `register_feature(...)`'d):

- `no_fall_damage` — prologue rewrites of FUN_0008AB70 and
  FUN_0008BE00 don't cover a third damage path.
- `infinite_fuel` — prologue + NOP cover event-driven and
  per-frame drains; attack-cast fuel is a separate, untraced
  path.
- `wing_flap_count` — 47-byte dispatch shim applies but the
  game re-reads the flap count through an animation state
  path we haven't traced.

The `docs/LEARNINGS.md` § "Retired physics patches" table
documents each with: patch site, why it doesn't land, and a
workaround (mostly "use the config editor" — fall damage via
`damage` thresholds, fuel via `armor_properties.fuel_max`,
wing-flap count via `armor_properties.Flaps`).

**Slider description trimming.**  The 7 remaining player
physics sliders use concise 1-2 sentence descriptions rendered
under the label.  Old labels like "Player wing-flap (double
jump) height" shortened to "Wing-flap height (1st flap)" +
description.  No functional change.

**Test suite stays at 785 passed + 761 subtests** — the retired
Feature specs stay defined so spec-shape / byte-landing tests
run unchanged, only the `register_feature` call is removed.

### Player packs — round 6 (flap_at_peak v2 + roll retirement + config-editor pointers)

**1. `flap_at_peak_scale` v2 — replaces the buggy v1 FSUB-NOP.**

v1 NOPed `FSUB [EBX+0x5C]` at VA 0x89381, which made
``fVar1 = peak_z + flap_height`` (always huge) but inadvertently
tripped the `fVar1 > 6m` halving check AND `consume_fuel` drain
path at VA 0x893D4.  User reported that flaps at peak "remove
upward velocity" — because the halving path consumed 100 fuel
per flap, so after 2 flaps the fuel gate refused further flaps
entirely.

v2 rewrites the 2-byte `FLD ST(1)` at VA 0x8939F to
`FLD ST(0)`.  Duplicates the just-loaded `flap_height` instead
of `fVar1`, so the subsequent FCOMP compares fh with fh (equal)
and the JP at 0x893A8 is always taken — skipping the min
selection.  `fVar2 = flap_height` every flap → full v0.  `fVar1`
is preserved untouched, so the below-6m halving check +
`flap_below_peak_scale` slider behave normally.

Site moved from `AZURIK_PATCH_FLAP_AT_PEAK_FSUB_VA` (0x89381) to
`AZURIK_PATCH_FLAP_AT_PEAK_FLD_ST_VA` (0x8939F) in `azurik.h`.
Ghidra annotated with both VAs + the v1/v2 explanation.

**2. `roll_speed_scale` retired from the GUI / randomizer.**

User testing confirms the WHITE-button roll animation
(`characters/garret4/roll_forward`) drives position via
animation root motion — the 3.0 FMUL on `magnitude` at VA
0x849E4 doesn't feed that pipeline.  The slider scaled a
magnitude multiplier the animation never consumed, so it had
no observable in-game effect.

The ParametricPatch + apply function remain defined (test
coverage unchanged), but the slider no longer appears in
`PLAYER_PHYSICS_SITES` / the Patches page / the randomizer
CLI.  A real roll-speed fix would need a C shim that
intercepts the root-motion application.  See
`docs/LEARNINGS.md` § "Roll speed is animation-root-motion".

**3. `no_fall_damage` / `infinite_fuel` / `wing_flap_count`
tagged `broken`, with config-editor pointers in description.**

User testing on 2026-04 confirmed all three are ineffective
in-game despite the byte patches landing correctly:
- `no_fall_damage` — a third damage path remains unpinned.
- `infinite_fuel` — attack-cast fuel drain (from
  `attacks_anims`'s `Fuel multiplier`) is not patched.
- `wing_flap_count` — shim applies but the game reads the
  flap count through a path we haven't traced to override.

The packs stay registered (tests still pass) but each
description now starts with `[BROKEN — prefer the config
editor]` and points to the concrete workaround:
- Fall damage → `damage` section thresholds, or
  `critters_damage` HP on the player row.
- Fuel drain → `armor_properties.fuel_max` or zero every
  `attacks_anims.Fuel multiplier`.
- Flap count → `armor_properties.Flaps` column per-armor-row.

**4. Better slider descriptions in the GUI.**

Added an optional `description: str` field on
`ParametricPatch` (backwards-compatible — default is empty).
When non-empty, the GUI renders it as a wrapped gray caption
under the bold slider label.  Populated for the three flap
sliders to clarify which is which:
- `flap_height_scale` — first flap's v0.
- `flap_below_peak_scale` — 2nd+ flaps more than 6 m below peak.
- `flap_at_peak_scale` — 2nd+ flaps near peak (binary toggle).

**5. Critter walk/run/accel/turn — confirmed not dead in the
config editor.**

User asked whether these are dead data.  Investigation via
Ghidra (FUN_0003A610 loader for `critters_walking`,
FUN_00049480 loader for `critters_critter_data`) confirms:

- `critters_walking` has 18 columns, all read at boot.
  Doesn't contain "walk speed" / "run speed" / "accel" /
  "turn speed" at all — it's AI behaviour (stalk / ambush /
  flee / footstep timing).
- `critters_critter_data` has `walkSpeed`, `runSpeed`,
  `walkAnimSpeed`, `runAnimSpeed`, all read at boot and
  consumed by `player_walk_state` / NPC AI routines.

Nothing to retire; the config editor was already pointing at
live values.  Documented in CHANGELOG here + schema
descriptions.

### Shim-header sync + code cleanup (late April 2026)

- Sync'd `azurik.h`, `azurik_vanilla.h`, `vanilla_symbols.py`, and
  Ghidra with the new findings from the previous round:
  - Added `FUN_0008BE00` (`fall_death_dispatch`, the no-surface
    fall-damage path) — function-entry VA, extern declaration,
    `VanillaSymbol` registry entry, Ghidra rename + plate
    comment.
  - Added `FUN_0008C080` (`player_landing`, the landing
    dispatcher that branches to the two fall-damage paths).
  - Added `FUN_00044640` (`apply_damage`, the generic damage-
    apply routine shared by combat + fall damage) as
    documented-but-not-patched.
  - Added `AZURIK_PATCH_FLAP_AT_PEAK_FSUB_VA` patch-site anchor
    in `azurik.h` with drift-audit coverage in
    `tests/test_va_audit.py`.
- Consolidated three duplicate `_find_repo_root()` helpers
  (`ghidra_sync.py`, `ghidra_coverage.py`, `shim_inspect.py`)
  into a single `find_repo_root()` exported from
  `azurik_mod/xbe_tools/__init__.py`; each module now imports
  the shared helper.
- Consolidated two duplicate `_resolve_va_to_file()` helpers
  (`player_physics/__init__.py`, `wing_flap_count/__init__.py`)
  into a single `resolve_va_to_file(xbe_data, va)` function
  exported from `azurik_mod/patching/xbe.py` next to the
  existing `va_to_file()` + `parse_xbe_sections()` primitives.
- Removed unused imports: `CONFIG_XBR_REL` (re-export with no
  consumer) in `shufflers.py`, and `DIRECT_SEARCH_NAMES`
  (re-export with no consumer) in `commands.py` — the latter's
  stale migration NOTE comment was removed too.
- Collapsed `from ... import RANDOMIZER_POOLS  # noqa: F401`
  into `import azurik_mod.patches.randomize  # noqa: F401` to
  signal the side-effect-only purpose explicitly.
- Deleted `docs/D2_NXDK.md` (deferred NXDK-integration plan,
  11 KB of speculative design); the concept is preserved in a
  one-line mention inside `docs/SHIMS.md` / `docs/D1_EXTEND.md`
  for when a shim concretely needs native D3D / DSound access.
- Rewrote `README.md` for concision (275 → 172 lines):
  trimmed redundant prose, removed the `examples/`-folder
  reference (long retired), collapsed doc-map table into the
  header link block.
- 785 tests + 761 subtests pass (unchanged from the previous
  round).

### Player packs — round 5 (flap-at-peak + fall-damage second path)

Another user report cycle surfaced two issues:

1. **Wing-flap "subsequent at peak" was still weak.**  The
   existing `flap_subsequent_scale` (now renamed
   `flap_below_peak_scale`) only addresses the 0.5-halving
   below 6m.  At peak the real cap is the
   `fVar2 = min(peak_z + flap_height - current_z, flap_height)`
   expression in `wing_flap` (FUN_00089300).  After the first
   flap, `current_z` drifts above `peak_z` by some tiny delta,
   so `remaining = flap_height - delta` is small, yielding
   weak v0.

   **Fix — new `flap_at_peak_scale` slider.**  Any value != 1.0
   NOPs the 3-byte `FSUB [EBX+0x5C]` at VA 0x89381, making
   `fVar1 = peak_z + flap_height` (a large positive number).
   `fVar2 = min(fVar1, flap_height)` collapses to `flap_height`,
   so every subsequent flap gets FULL `sqrt(2g * flap_height)`
   v0.  Side effect: the below-6m halving check trips more
   often, so combine with `flap_below_peak_scale = 2.0` to
   un-halve it.

   **Rename**: `flap_subsequent_scale` → `flap_below_peak_scale`
   (back-compat alias and CLI flag kept for pre-late-April-2026
   callers).

2. **`no_fall_damage` left light damage enabled.**  User
   reported "instant death prevented, but light damage still
   fires."  Investigation found `FUN_00044640` (damage apply)
   has TWO callers in the landing code:

   - `fall_damage_dispatch` (FUN_0008AB70) at VA 0x8AD9B — the
     tiered "fall damage 1/2/3" path.  v1 patch bypasses this.
   - `FUN_0008BE00` at VA 0x8BF59 — the **no-surface landing**
     path.  Fires when `[entity+0x38]` (surface contact slot)
     is null, checks the cached "fall height 4" cvar, calls
     `FUN_00044640` if fall magnitude exceeds threshold.  v1
     patch NEVER touched this.

   **Fix — patch both prologues.**  `no_fall_damage` now
   rewrites BOTH function entries to `XOR AL,AL ; RET <N>`:

   - `FUN_0008AB70`: `XOR AL,AL ; RET 8 ; NOP` (6 bytes, __stdcall 2 args)
   - `FUN_0008BE00`: `XOR AL,AL ; RET 4` (5 bytes, __stdcall 1 arg)

   This closes the second path — no more "light damage" on
   unusual landings.

3. **Open issues acknowledged (not yet fixed)**:

   - **`roll_speed_scale`** — bytes land correctly at VA 0x849E4
     (FMUL rewrite with injected `3.0 × roll_scale`), but
     user still reports no observable effect.  The FMUL is
     gated on bit 0x40 of PlayerInputState.flags, set by
     FUN_00084F90 based on `g_armor_state_per_controller[0xb]`
     or `[0xf]` (WHITE / BACK button pressures).  In modern
     xemu with default input bindings, these buttons may not
     be wired through to that flag bit, so the FMUL never
     executes in gameplay.  Workaround: verify with
     `azurik-mod inspect-physics --iso <iso>` that the bytes
     landed, then investigate input routing with lldb or
     xemu's input panel.

   - **`climb_speed_scale`** — 2.0 → N overwrite at VA 0x001980E4.
     Function `player_climb_tick` (FUN_00087F80) references
     this constant in two places, both reached when
     `stick_magnitude != 0`.  The climb-up/down sound strings
     (`fx/sound/player/climb*`) are ONLY xref'd from this
     function, confirming it IS the real climbing handler.
     Bytes land.  If the user observes no speed change, the
     climbing they're testing may go through a different state
     (slope walking, ledge grab) rather than state 1 (climb).

   - **`slope_slide_speed_scale`** — 2.0 → N overwrite at VA
     0x001AAB68.  This constant is ONLY the "slow slope slide"
     (state 3) velocity scalar.  State 4 (fast slide,
     `velocity > DAT_001aab74`) uses a dynamically-computed
     500× multiplier (`DAT_003902A0`) not targeted here.  If
     the user's test scenario triggers fast slides (common on
     steep descending slopes), our state-3 patch has no effect.

### Player packs — round 4 (infinite_fuel per-frame drain + slope_slide slider + shim header coverage)

**1. `infinite_fuel` — v2 (adds per-frame drain site).**

The v1 patch at FUN_000842D0 covered only the event-driven
fuel consumer (wing flap, 100-unit penalty, etc.).  User
testing confirmed fuel still drained, which meant a second
consumer existed.  Located it at VA 0x83DE3 inside
FUN_00083D80 (the armor-state tick):

```asm
00083DE3   D9 05 20 81 19 00   FLD  [0x00198120]  ; = 1/30 (frame time)
00083DE9   D8 71 34            FDIV [ECX+0x34]     ; / drain rate
00083DEC   D8 6E 24            FSUBR [ESI+0x24]    ; fuel - x
00083DEF   D9 5E 24            FSTP [ESI+0x24]     ; write back
```

Classic "drain fuel by dt/rate every frame" pattern.  The
`1/30 = 0.0333...` at VA 0x198120 has EXACTLY ONE reader —
this block — so we can safely NOP the entire 15-byte
sequence without collateral.  FP stack delta of the block is
0 (FLD pushes 1, FSTP pops 1), so 15 × NOP preserves FP
state and simply skips the decrement.

v2 applies BOTH patches.  Should now be truly infinite fuel
for both event-driven consumers AND the per-frame drain.
Open follow-up: if the user still sees fuel drain (attack
cast?), we'll hunt for a third path.

**2. `slope_slide_speed_scale` slider — new (player_physics).**

Patches the single-reader constant at VA 0x001AAB68 (vanilla
2.0) used by FUN_00089A70 when the player lands on a slope
> 45° from upright and enters the auto-slide state.  This
was the v3 roll target we abandoned (it's NOT the WHITE-
button dash — roll_scale covers that) but it IS a legitimate
physics axis the user wants control over.  Direct 4-byte
overwrite, no shim.  Range 0.1-10.0×.

**3. Shim headers updated with late-April 2026 RE findings.**

`shims/include/azurik.h`:
- Added player entity field offset documentation
  (magnitude, air control, jump height, flap counter,
  peak_z, flags byte, etc.).
- Added ArmorMgr pointer chain documentation
  (armor_mgr → level_struct → air_power_level).
- **IMPORTANT: corrected DAT_001A7AE4** — documented that
  it's the active XInput controller index (0-3, or 4 when
  disconnected), NOT the air-power level.  The wing_flap_count
  dispatcher now uses the correct chain.
- Added 8 constant macros + expectations: slope_slide,
  climb, gravity, flap_boost, flap_halving, flap_threshold,
  roll_boost, swim_boost (each with VA + vanilla value).

`shims/include/azurik_vanilla.h`:
- Added 10 extern declarations for the player-physics
  functions we've mapped: `player_walk_state`,
  `player_jump_init`, `player_airborne_tick`, `wing_flap`,
  `player_airborne_reinit`, `player_input_tick`,
  `player_climb_tick`, `player_slope_slide_tick`,
  `player_swim_tick`, `fall_damage_dispatch`, `consume_fuel`.
- Added 2 config loader externs: `config_cell_value`,
  `cvar_get_double`.
- Each extern includes VA, calling convention, a comprehensive
  comment block describing the function's role + any patch
  sites inside it.

`tests/test_va_audit.py`:
- Added `ANCHOR_EXPECTATIONS` entries for all 8 new azurik.h
  constant macros.  Each entry validates section + vanilla
  byte pattern.

**4. Documentation: config editor vs XBE-side patch trade-offs.**

Question: "could any of our patches be more easily achieved
through the config editor?"

Answer: some, but mostly NO because critter_data config
values are dead data at runtime.  Specifically:

- **Fall damage values** (`fall_height_1/2/3`,
  `fall_damage_1/2/3`, `fall min velocity`): DO live in
  `config.xbr` and ARE read at runtime (cached as static
  doubles in FUN_0008AB70 on first call).  Could be edited
  via the config editor to change thresholds.  Our
  `no_fall_damage` patch uses a more-robust XBE prologue
  bypass so it works even if those cvars are at extreme
  values.

- **Fuel amounts** (`air fuel max`, `initial fuel`, etc.):
  LIVE in `config.xbr`.  User COULD set `air fuel max` to
  a huge value as a "pseudo-infinite" alternative — the bar
  would start very full and drain imperceptibly.  But our
  `infinite_fuel` XBE patch is strictly better (doesn't
  depend on users having/using a config editor).

- **Critter movement** (walk/run speeds, accel, turn rate):
  The `config.xbr` rows for these fields turned out to be
  DEAD DATA at runtime — they're read into the struct but
  never consumed by the physics engine (see
  `docs/LEARNINGS.md` § "The player-speed dead-data pivot").
  XBE-side patches are the ONLY way to change these, which
  is why all our speed sliders are XBE-code rewrites.

- **Attack fuel multipliers**: LIVE in
  `config/attacks_anims` tabl.  Editable via config editor
  to reduce/zero attack fuel costs.  Would be a complementary
  approach to our `infinite_fuel` if a third drain path is
  found.

- **Armor/flap counts**: LIVE in `config/armor_properties`
  tabl's ``"Flaps"`` column.  Our `wing_flap_count` shim
  overrides at runtime via a trampoline; editing the tabl
  would be an alternative static approach.

Tests: 779 unit tests all pass (+3 for the new infinite_fuel
per-frame-drain assertions).

### Player packs — round 3 (roll retarget / wing-flap-count fix / full fall-damage / flap_subsequent slider)

User feedback on the previous round identified four concrete
issues and asked for three follow-ups.  All addressed below.

**1. Roll speed — v4: revert target to the WHITE-button FMUL.**

The v3 approach targeted ``VA 0x001AAB68`` (the 4-byte
``_DAT_001AAB68 = 2.0`` constant used by ``FUN_00089A70``).
User testing proved that FUN_00089A70 is the **slope-slide**
physics state — reached when the player lands on a slope >
45° from upright and the avatar auto-slides.  It is NOT the
roll animation / WHITE-button dash players trigger
manually.

v4 reverts the target to the v1 FMUL rewrite at ``VA 0x849E4``
(inside ``FUN_00084940``) — the 3.0 multiplier gated by
``PlayerInputState.flags & 0x40`` (WHITE or BACK held).
No force-always-on (v2's "make it observable without the
button" trick introduced the airborne-coupling bug).  Now:

- Press WHITE/BACK → magnitude × (3.0 × roll_scale).  Walking
  with WHITE held scales linearly with roll_scale.
- No button held → vanilla (magnitude × 1.0).  No coupling.
- Mid-air WHITE held → same scale as ground (matches vanilla
  coupling; we just scale vanilla's 3× proportionally).

Users who haven't routed WHITE/BACK in their xemu config will
see no effect — but that's consistent with vanilla roll
behaviour and is the correct trade-off vs. fabricating an
always-on multiplier.

The v3 slope-slide constant at 0x1AAB68 is still exposed by
``inspect-physics`` as a diagnostic (should always show
VANILLA on v4-patched ISOs).

**2. Wing-flap-count — dispatcher was reading the wrong variable.**

The previous shim dispatched on ``ds:[0x001A7AE4]``, claiming
that was the air-power level.  Ghidra-chase of the only writer
(``FUN_000A2DF0``) reveals that variable is actually the
**active XInput controller index** (0-3 for port; 4 for
"disconnected") — not the air-power level.  So the shim
always fell through to the vanilla fallback branch.

v2 shim reads the air-power level from its true home:
``[EDX+0x8]`` at the hook site.  At VA 0x89321, EDX already
holds the ``level_struct`` pointer (set at VA 0x8931E by
``MOV EDX, [EDI+0x20]``).  ``level_struct + 0x8`` holds the
level int (1-3 when air power is active, 0/4 otherwise).
The shim now uses ``MOV EDX, [EDX+0x8]`` (3 bytes) instead
of ``MOV EDX, ds:[abs32]`` (6 bytes), saving 3 bytes —
total shim size: 50 → 47 bytes.

**3. No fall damage — full function bypass.**

The JNP→JMP flip at VA 0x8AC77 prevented instant-death falls
but some light damage still came through (users confirmed).
v2 rewrites the ``FUN_0008AB70`` prologue (VA 0x8AB70) to
``XOR AL, AL ; RET 8 ; NOP`` — a 6-byte always-return-0 that
short-circuits the entire function BEFORE the cvar-cache
init / tier selector / damage-application branches run.
Callers see "no damage dealt" regardless of fall velocity
or height.

**4. New slider: ``flap_subsequent_scale``.**

After the first wing flap, ``FUN_00089300`` halves v0 via
``FMUL [0x001A2510] = 0.5`` at VA 0x893DD when the player
has fallen > 6m below their peak.  This is why second+ flaps
feel weaker than the first.  The new slider rewrites the
FMUL to reference an injected ``0.5 × subsequent_scale``:

- ``1.0`` (default): vanilla halving (subsequent flaps at 50% v0).
- ``2.0``:           no halving (subsequent flaps at 100% v0).
- ``4.0``:           subsequent flaps BOOSTED (200% v0).

Independent of ``flap_height_scale``: set both to 2.0 to get
"first flap 2× AND subsequent flaps full v0 × 2" (every flap
consistently strong).

**Investigations that didn't ship:**

- **Camera zoom**: no static float constant in ``default.xbe``,
  ``characters.xbr``, or ``config.xbr`` cross-references as a
  camera distance/FOV.  Camera parameters are either per-level
  (in level ``.xbr`` files) or embedded in camera-data assets
  that the game script/animation system references.  Would
  need ``.xbr``-side modding infrastructure to ship.  Left as
  an open follow-up.

- **Additional movement**: ``max accel up / down / xy``, ``max
  turn rate``, ``strafe height``, etc. exist in config.xbr as
  per-character rows (``config/critters_move`` or similar) —
  not patchable via XBE code changes without an ``.xbr``-side
  config editor.

**Diagnostic:** ``azurik-mod inspect-physics`` now reports the
roll FMUL + flap-subsequent FMUL sites separately, plus
(carried over) primary/secondary air-control and v3 slope-
slide constants as their own lines.  ``no_fall_damage`` /
``infinite_fuel`` / ``wing_flap_count`` still show at the
bottom.

**Tests:** 776 unit tests all pass.  The ``DynamicWhitelistFromXbe``
test now verifies 6 × 6-byte rewrite sites (walk, swim,
jump, flap, roll, flap_subsequent) + 8 × 4-byte direct sites
(5 primary air-control + 2 secondary + climb) + up to 6 extra
injected-float follows after a full apply.

### New player packs + player_physics flap/air-control fixes

Five user-driven additions focused on the Air-power / wing-flap
system and general movement quality-of-life.

**Fixes to existing player_physics sliders:**

1. **`air_control_scale` now patches 7 sites (up from 5).**  The
   5 original `MOV [reg+0x140], 0x41100000` writes only fire
   during specific jump-entry paths.  The DOMINANT air-control
   setter during normal gameplay is inside `FUN_00083F90` — a
   per-frame airborne re-initialiser called from the main jump
   and the wing flap.  Its two `MOV [ECX], imm32` writes (12.0
   for air-power 1-3, 9.0 for no-air-power) at VAs 0x83FAC /
   0x83FCE are now scaled too.  Previously users with air power
   equipped saw no effect from the slider because FUN_00083F90
   kept overwriting `entity[+0x140]` with vanilla 12.0 / 9.0
   every frame.  Each imm32 is now scaled *from its current
   value* so 12.0 stays distinct from 9.0 (just both scaled
   together).

2. **`flap_height_scale` retargeted to the REAL wing-flap site.**
   Pre-v2 the slider rewrote `FADD [0x001A25C0]` at VA 0x896EA
   inside `FUN_00089480` (airborne per-frame physics).  User
   testing confirmed the bytes landed correctly but had no
   observable gameplay effect — that FADD turned out to gate a
   different airborne maneuver (not the Air-power wing flap).
   The real wing flap lives in `FUN_00089300` at VA 0x893AE as
   a `FLD [0x001980A8]` gravity load inside a sqrt(2gh) v0
   formula.  v2 rewrites that FLD to reference an injected
   `9.8 × flap_scale²` — mirror of how jump_height_scale works.
   `flap_scale` now linearly scales the wing flap's initial
   vertical velocity (quadratic effect on peak flap height).

**Three new packs in the Player category:**

3. **`no_fall_damage` (new pack).**  Single-site 6-byte patch
   at VA 0x8AC77: flips the top-level `JNP rel32` conditional
   in `FUN_0008AB70` to an unconditional `JMP rel32 + NOP`.
   The target address (0x8ADFC — the `XOR AL,AL ; RET 8` "no
   damage dealt" tail) is unchanged.  Every landing now takes
   the no-damage path regardless of fall velocity.  Splat SFX
   and damage rumbles never fire; HP max / other damage
   systems are untouched.  Idempotent.

4. **`infinite_fuel` (new pack).**  Single-site 5-byte patch
   at VA 0x842D0: replaces the prologue of `FUN_000842D0` (the
   fuel consumer called by every elemental power) with
   `MOV AL, 1 ; RET 4`.  Always returns success without
   decrementing `armor.fuel_current`.  Works uniformly for
   water / fire / air / earth powers.

5. **`wing_flap_count` (new pack).**  Three per-air-power-level
   sliders (Air Power 1 / 2 / 3; vanilla values 1 / 2 / 5
   respectively, range 0-99 each).  Installs a 5-byte JMP
   trampoline at VA 0x89321 (inside `FUN_00089300`'s
   flap-count check) into a 50-byte dispatch shim that:
   (a) replays the vanilla `MOV EAX, [EDX+0x38]`,
   (b) reads `[0x001A7AE4]` (current air-power level),
   (c) overwrites EAX with the user-selected int for levels
       1 / 2 / 3,
   (d) replays the clobbered `TEST EAX, EAX` and JMPs back.
   Values at vanilla defaults (1 / 2 / 5) produce a byte-
   identity no-op.  Per-level independence preserves the air-
   power-upgrade sense of progression while letting users
   tune flap counts freely.

   **Bug caught in development:** `_carve_shim_landing`'s
   zero-trailer back-scan was stomping the trailing zero bytes
   of small ints (e.g. flap count 50 = `32 00 00 00` — the
   three zeros got overwritten by the next allocation).  Fix:
   the 3 ints are packed into a single 16-byte allocation with
   a trailing 4-byte `0xFF FF FF FF` sentinel.

**Diagnostic:** `azurik-mod inspect-physics` now reports:
- Air-control primary sites (5 × imm32 at entity+0x140) AND
  secondary sites inside FUN_00083F90 separately (so you can
  verify both paths landed).
- Fall-damage flag: `[VANILLA]` / `[PATCHED]` / `[DRIFTED]`.
- Infinite-fuel flag: ditto.
- Wing-flap-count trampoline + per-level counts.

**Tests:** +22 new unit tests in `tests/test_new_player_packs.py`
(775 total, all passing).  Existing dynamic-whitelist counts
updated to account for the 2 new air-control secondary sites
(13 4-byte ranges on vanilla, was 11).

**Camera zoom slider:** Requested but deferred.  The camera
distance / FOV parameters don't appear to live in `default.xbe`
as a static .rdata float — no obvious "zoom" / FOV string
cross-references an isolated constant.  They're likely in a
config .xbr (probably `camera.xbr` / `cinematic.xbr`) loaded at
runtime, which would require an .xbr-side mod or a config-
file-rewrite pack rather than an XBE patch.  Open follow-up.

### player_physics v3 — roll redesigned, climb added, dev-menu retired

Three-part user-driven revamp of the player-movement system.

**#1. `enable_dev_menu` removed (dead end).**  Across four design
iterations (two ``JZ``→``NOP``s, cheat-cvar short-circuit,
three-stage-validator short-circuit, and finally a trampoline on
the universal level loader `FUN_00053750`), every approach landed
the planned bytes correctly but the user could never observe the
forced "levels/selector" boot path in-game.  The actual
cutscene→first-level transition seems to route through a path we
haven't yet mapped.  Rather than ship a feature that only LOOKS
right in the binary diff, the whole pack is retired.  Research
notes in `docs/LEARNINGS.md` § "enable_dev_menu — three-stage
validator chain" are kept as historical reference for any future
attempt.  Removed:

- `azurik_mod/patches/enable_dev_menu/__init__.py` + README
- `EnableDevMenuFeature` test class in `tests/test_tier3_tools.py`
- `test_enable_dev_menu_is_experimental` in `tests/test_categories.py`
- Dev-menu probe block from `azurik-mod inspect-physics`
- Row in `docs/PATCHES.md`, PLUGINS.md example reference

The `experimental` category is still registered but has zero
shipped packs — the browser correctly hides the tab.

**#2. `roll_speed_scale` retargeted to the rolling-GROUND state.**
v2's approach (rewrite FMUL at 0x849E4 + force-always-on bit
0x40) had a real coupling bug user testing caught: boosted
``magnitude`` propagated through `FUN_00089480` into airborne
horizontal speed (`entity[+0x140] × magnitude`), which felt like
"gravity got weaker" because the player covered way more ground
per jump.  v3 scraps FMUL-rewrite + force-on entirely and
targets `FUN_00089A70`'s rolling/sliding ground-state velocity
FMUL directly by overwriting the 4-byte float constant at VA
**`0x001AAB68`** (vanilla 2.0) to `2.0 × roll_scale`.  The
constant has exactly one reader in the entire binary, so the
patch is byte-minimal (4 bytes) and cannot leak into any other
physics system.  The WHITE-button edge-lock, the force-on
sites, and the old FMUL instruction all stay at vanilla.

**#3. New `climb_speed_scale` slider.**  `FUN_00087F80`
(climbing / hanging-ledge state) reads its baseline climb
velocity from the .rdata float at VA **`0x001980E4`** (vanilla
2.0).  The constant has exactly two readers, both in
FUN_00087F80, so a direct 4-byte overwrite affects only
climbing.  Range 0.1-10.0×.

**Net effect on player_physics pack**: 8 sliders total (gravity,
walk, roll, swim, jump, air-control, flap, climb).  The roll
slider now does what its name advertises — affects rolling, not
airborne speed.  The climb slider fills the last obvious
vanilla-movement-speed axis users can tweak.

New / changed APIs:

- `apply_climb_speed(xbe, climb_scale=X)` in `player_physics/__init__.py`
- `_ROLL_CONST_VA` / `_ROLL_CONST_VANILLA` / `_VANILLA_ROLL_SPEED`
- `_CLIMB_CONST_VA` / `_CLIMB_CONST_VANILLA` / `_VANILLA_CLIMB_SPEED`
- Back-compat: `_ROLL_SITE_VA` / `_ROLL_SITE_VANILLA` now alias
  the new constant VA/bytes (semantic shift; pinned byte lengths
  changed from 6 → 4).
- Removed: `_ROLL_EDGE_LOCK_*`, `_ROLL_FORCE_ON_1_*`,
  `_ROLL_FORCE_ON_2_*`.
- CLI: `--player-climb-scale` on `randomize-full`, `--climb-speed`
  on `apply-physics`.

`inspect-physics` now reports roll/climb as `[VANILLA]` /
`[PATCHED]` 4-byte direct-constant overwrites (instead of the
old FMUL-pointer-chase + force-on triple).

Dynamic-whitelist counts for ``verify-patches --strict``: 11
ranges on vanilla (4 × 6-byte instr sites, 5 × 4-byte
air-control imm32, 2 × 4-byte direct-constant roll+climb), up
to 15 ranges post-apply (adds 4 × 4-byte injected-float follows
for walk/swim/jump/flap).

Tests: 753 unit tests all green.  Physics-related test count
unchanged.  `RollForceAlwaysOn` test class deleted;
`ApplyClimbSpeedBehaviour` added; `ApplyPlayerSpeedBehaviour`,
`DynamicWhitelistFromXbe`, `SliderSemantics` rewritten for v3
semantics.

### player_physics — air-control + wing-flap sliders (v2 April 2026)

Two new player-movement patches, bringing the pack to **7
sliders** (gravity + walk + roll + swim + jump + air-control +
flap).  Both requested by user testing; both correctly
isolated to player-only physics (no shared-constant disturbance).

**#1. Horizontal air-control speed.**  `entity + 0x140` stores a
per-frame mid-air horizontal steering multiplier that
`FUN_00089480` applies every airborne frame.  Vanilla `9.0`.
Written by 5 `MOV DWORD [reg+0x140], 0x41100000` imm32
instructions across the airborne-state entry paths (main
ground jump in `FUN_00089060`, plus 4 alternate paths).
``apply_air_control_speed`` rewrites each imm32 to
``9.0 × air_control_scale``.  5-site in-place patch; no shim.

Does NOT affect jump HEIGHT (which is computed from the SQRT
formula in `FUN_00089060` reading `entity + 0x144`, owned by
`apply_jump_speed`).  Only affects mid-air horizontal
movement.  These are the 5 sites the pre-v2 jump patch
mistakenly targeted — they DO meaningfully affect movement,
just horizontally not vertically, so they're kept as a
separate slider now.

**#2. Wing-flap / Air-power double-jump height.**
`FUN_00089480` adds `8.0` to the z-velocity when BOTH the
flap button (input flag 0x04) AND the roll flag (0x40) are
set — the Air-power double-jump.  The FADD lives at VA
`0x000896EA` as `FADD dword [0x001A25C0]`.
``apply_flap_height`` rewrites it to
`FADD dword [inject_va]` where `inject_va` holds
`8.0 × flap_scale`.  Single 6-byte rewrite + 4-byte
injected float; shim-landed.  The shared `8.0` at
`0x001A25C0` has 4 non-player readers and is left untouched.

**User-facing changes**:
- GUI Patches page now shows two new sliders:
  "Player air-control speed" (range 0.1–10.0) and
  "Player wing-flap (double jump) height" (range 0.1–10.0).
  Both default 1.0, text-box unclamped like the others.
- CLI: `--player-air-control-scale` and `--player-flap-scale`
  on `randomize-full`; `--air-control-speed` and
  `--flap-height` on `apply-physics`.
- `apply_player_physics` accepts new kwargs
  `air_control_scale` and `flap_scale` alongside the existing
  ones.
- `azurik-mod inspect-physics` reports both new patches
  (per-site for air-control's 5 imm32s, FADD rewrite state +
  injected value for flap).

**Tests**: 759 passed (+11 new):
- `ApplyAirControlBehaviour` (5 tests) pins all 5 imm32 sites,
  verifies isolation from jump/walk, scale=2 imm32
  replacement, apply_player_physics routing.
- `ApplyFlapHeightBehaviour` (6 tests) pins the FADD rewrite,
  verifies the shared 8.0 at VA 0x001A25C0 stays intact,
  isolation from jump/walk/air-control, apply_player_physics
  routing.
- Dynamic-whitelist tests updated for new site counts: vanilla
  yields 13-17 ranges; patched yields 5 six-byte instruction
  rewrites + 10 four-byte (5 injected floats + 5 air-control
  imm32) + 3 two-byte roll-aux.

**Docs updated**:
- `docs/LEARNINGS.md`: new "Airborne horizontal-control speed"
  and "Wing-flap (double-jump) vertical impulse" sections with
  the FUN_00089060/FUN_00089480 decode.

### player_physics — jump-formula fix + roll force-always-on + inspect-physics

Three user-reported "still doesn't work" issues traced to root
cause and fixed.  **All three previous patches landed bytes
correctly on disk**, but the bytes were either targeting the
wrong physics field or the runtime gate they modified wasn't
firing in the user's configuration.

**#1. Jump: wrong field targeted in v1.**  v1 patched the
`MOV [reg+0x140], 0x41100000` imm32 at 5 call sites, but
`entity + 0x140` is the HORIZONTAL AIR-CONTROL speed (used by
`FUN_00089480` as a per-frame multiplier while airborne), NOT
the jump height.  The actual jump formula is at VA `0x89160`
inside `FUN_00089060`:

```
FLD  [0x001980A8]            ; 9.8 (gravity)
FMUL [ESI + 0x144]            ; × height scalar
FADD ST0, ST0                 ; × 2
FSQRT                          ; v₀ = sqrt(2gh)
```

v2 rewrites the FLD to load from an injected `9.8 × jump_scale²`
constant instead of the shared gravity global.  The SQRT then
produces `jump_scale × sqrt(2 × 9.8 × h)` — linear scaling on
initial jump velocity, quadratic on peak height.  No shared
constant touched; gravity slider remains independent.

Single 6-byte FLD rewrite + 4-byte injected float, down from
v1's 20-byte multi-site imm32 patch.

**#2. Roll: byte patches applied, but WHITE/R3 wasn't firing
at runtime for users whose xemu input config didn't route those
buttons.**  v2 adds a "force-always-on" sub-patch (2 × 2 bytes
at VAs `0x85214`, `0x8521C`) that makes bit `0x40` of the
input-state flags unconditionally set every frame when
`roll_scale != 1.0`.  The injected FMUL multiplier then fires
on every movement frame regardless of controller input.

Simplified `inject_roll_mult` to just `roll_scale` (was `3 ×
roll_scale / walk_scale` in v3-pre-force).  With force-always-on
in effect, the old "3× WHITE boost" meaning no longer applies;
`roll_scale` is now a pure secondary walking-speed multiplier
that stacks with `walk_scale`:

```
velocity = 7 × walk_scale × roll_scale × raw_stick × direction
```

Both sliders at `1.0` short-circuit to identity.  Existing
`roll_scale != 1.0` configurations behave differently (became
permanent multiplier, not WHITE-gated), but the effect is now
always observable which is what users actually want.

**#3. Dev menu: v4 trampoline bytes land correctly.**  Verified
the FUN_00053750 entry trampoline installs at VA `0x53750`,
redirects to the SHIMS section at VA `0x39F000`, and the SHIMS
section has proper `flags=0x06` (EXECUTABLE | PRELOADED).  If
users still see no effect, the issue is almost certainly that
their build pipeline isn't actually including the patched XBE.

**New CLI: `azurik-mod inspect-physics --iso <path>` / `--xbe
<path>`.**  Diagnostic that reads a built ISO or raw XBE and
reports — per slider — whether the bytes are `[VANILLA]`,
`[PATCHED]` (with injected float values), or `[DRIFTED]`.  Also
dumps the roll edge-lock / force-on state and checks the
enable_dev_menu trampoline.  Run this FIRST when a patch seems
inert — it confirms the bytes actually landed:

```text
$ azurik-mod inspect-physics --iso Azurik_patched.iso

Player physics sliders:
  gravity        [VANILLA]  value = 9.8000 m/s²
  walk           [PATCHED]  inject VA 0x1001D0 = 14.0000
  roll (FMUL)    [PATCHED]  inject VA 0x1001D4 = 3.0000
  swim           [PATCHED]  inject VA 0x1001D8 = 15.0000
  jump (FLD)     [PATCHED]  inject VA 0x1001DC = 39.2000

Roll auxiliary patches:
  edge-lock     [NOPED]   bytes = 9090 (VA 0x85200)
  force-on #1   [PATCHED] bytes = b040 (VA 0x85214)
  force-on #2   [PATCHED] bytes = 0ad0 (VA 0x8521C)

enable_dev_menu trampoline:
  [INSTALLED] hook bytes = e9abb834009090 -> JMP to VA 0x39F000
              (section 'SHIMS')
```

**Tests** (748 passed, +4 from 744):
- `ApplyJumpSpeedBehaviour` rewritten: tests the new FLD-site
  target, verifies the shared gravity constant is untouched,
  site isolation, reapply rejection.
- `RollForceAlwaysOn` added: pins the 2-byte patches at
  `0x85214` + `0x8521C` and the simplified `inject_roll_mult =
  roll_scale` formula.
- `SliderSemantics` replaces the old `IndependenceSemantics`
  suite: walk/roll are now compounding multipliers (stack), not
  independent.  Each slider's site isolation is still tested.
- `DynamicWhitelistFromXbe` updated for the new site counts: 4
  instruction sites (6-byte) + 3 two-byte roll-aux + up to 4
  four-byte injected-float follows (walk, roll, swim, jump).

### player_physics — jump slider + roll edge-lock NOP; GUI slider unclamp

Four user-reported issues addressed in one pass.

**#1. GUI text-box now accepts values outside the slider range.**
`ParametricSlider` previously clamped typed values to
`[slider_min, slider_max]` on commit.  Power users who wanted to
push `walk_speed_scale=25` couldn't — the typed value got silently
snapped to 10.  The text-box is now unclamped: any finite float
commits verbatim as the exact value, the slider thumb rests at
whichever bound is closer, and a `[!]` badge in the header
indicates the exact value is outside the slider's visual range.
`get_value()` returns the exact typed value.  Slider drags still
operate inside the declared range.

**#2. New `jump_speed_scale` slider.**  Reverse-engineered the
main jump initialiser (`FUN_00089060`, plays
`fx/sound/player/jump`).  The jump velocity scalar lives directly
in the player entity at `+0x140`, written by five
`MOV [reg+0x140], 0x41100000` instructions (5 airborne-state
entry paths — main ground jump, water exit, wall kick, double-
jump, mid-air state transition).  The new slider rewrites the
4-byte IEEE-754 imm32 at each site with `9.0 × jump_scale`.  No
shared constants touched; no shim required.

- `JUMP_SPEED_SCALE` ParametricPatch added to `PLAYER_PHYSICS_SITES`.
- CLI: `--player-jump-scale` (full build), `--jump-speed`
  (physics-only) accept any float.
- GUI Patches page renders a new "Player jump height" slider
  (range 0.1–5.0, default 1.0, text-box unclamped).
- Dynamic whitelist auto-whitelists the 5 imm32 sites.

**#3. Roll slider now fires on sustained WHITE-button hold.**
Confirmed via fresh Ghidra trace that the 3× boost at VA
`0x849E4` IS player-specific, but its activation flag is gated
by either `RIGHT_THUMB` (click) or `WHITE` (one-frame tap, then
edge-locked).  Result: `roll_scale` was invisible during normal
play because the engine set the flag for only a single frame per
WHITE tap.  Fix: additionally NOP the 2-byte `JNZ +8` at VA
`0x00085200` whenever `roll_scale != 1.0`, removing the WHITE
edge-lock so holding WHITE now gives sustained 3× magnitude
boost for every frame the button is down.  The byte patch was
always correct — this change makes it observable in gameplay.

**#4. `enable_dev_menu` rewritten (v4) to trampoline
`FUN_00053750`'s entry prologue.**  v1-v3 patched various upstream
branches inside `dev_menu_flag_check`, but the main New-Game →
cutscene → first-level flow goes through `FUN_00055AB0` which
calls the universal level loader `FUN_00053750` **directly** with
a hardcoded level name (e.g. `"levels/water/w1"` after the
prophecy cutscene), bypassing `dev_menu_flag_check` entirely.
That's why v1-v3 looked like "nothing happens".

v4 hooks the universal entry point itself.  At VA `0x00053750`,
install a 7-byte trampoline (5-byte `JMP rel32` + 2 NOPs) that
jumps to a 27-byte shim landed in the shim-landing slot.  The
shim:

1. Checks `param_4` (at `[ESP+0x10]`) — if nonzero (bink movie
   path) it skips the override so cutscenes still play.
2. Overwrites `param_2` (the level-name pointer at `[ESP+8]`)
   with the VA of the `"levels/selector"` string at
   `0x001A1E3C`.
3. Replays the clobbered `MOV EAX, [ESP+4] ; MOV ECX, [EAX+0x40]`
   instructions so EAX/ECX are correct when the function
   continues.
4. Jumps back to the `SUB ESP, 0x824` at VA `0x00053757`.

Every level transition in the game — New Game, Load Save,
cutscene-end, developer console loadlevel — now routes to
`levels/selector` regardless of the upstream caller's intent.
Movies (bink:) keep playing.

**Docs updated**:

- `docs/LEARNINGS.md`: new "WHITE-button sustained roll" section
  documenting the edge-lock mechanism and the NOP fix.
  Previous "enable_dev_menu — three-stage validator chain"
  section supplemented with the direct-call-from-FUN_00055AB0
  discovery note.
- `docs/PATCHES.md`: updated player_physics entry for the 4th
  slider (jump).  Updated enable_dev_menu site count to 1.
- `azurik_mod/patches/enable_dev_menu/README.md` + module
  docstring rewritten to document the trampoline design.

**Tests**: 744 passed (+7).  New coverage:
- `ApplyJumpSpeedBehaviour` (6 tests: the 5 imm32 sites, scale
  multipliers, site-isolation guards, `apply_player_physics`
  routing).
- `test_roll_scale_nops_white_edge_lock` (pins edge-lock NOP
  behaviour).
- `DynamicWhitelistFromXbe` updated for the new jump + edge-
  lock ranges.
- `EnableDevMenuFeature` rewritten for the v4 trampoline
  (8 tests pinning hook VA, vanilla prologue, selector-string
  drift, JMP installation, shim layout byte-by-byte, section-
  landing, idempotency, whitelist shape).

### player_physics — rename run→roll, add swim slider

Two semantic fixes triggered by user testing ("run speed seems to
do nothing; rolling got faster; swim speed is unaffected").

**#1. `run_speed_scale` is actually roll speed.**  The 3.0
multiplier at VA `0x001A25BC` is applied when
`PlayerInputState.flags & 0x40` is set, and that flag is set by
the **WHITE** (or **BACK**) controller button — which in Azurik
is the roll / dive / dodge button, not a sprint button.  Azurik
has no separate run speed; walking is simply
`CritterData.run_speed × stick_magnitude`.  All renames:

- `RUN_SPEED_SCALE` → `ROLL_SPEED_SCALE` (back-compat alias kept)
- `run_scale` kwarg → `roll_scale` (back-compat alias kept)
- CLI: `--player-run-scale` → `--player-roll-scale`
  (`--player-run-scale` stays as a deprecated alias)
- CLI: `--run-speed` → `--roll-speed` (both accepted)
- GUI label "Player run speed" → "Player roll speed"
- Module constants: `_RUN_SITE_VA` → `_ROLL_SITE_VA`,
  `_VANILLA_RUN_MULTIPLIER` → `_VANILLA_ROLL_MULTIPLIER`, etc.

Existing configs / ISOs built with the old names keep working —
the Python module re-exports every old name as an alias and
`apply_player_physics(run_scale=...)` still routes correctly.

**#2. New swim-speed slider.**  Reverse-engineered
`FUN_0008b700` (swim state handler).  Swim velocity = magnitude
× `10.0` (shared constant at `VA 0x001A25B4`, 8 readers
globally).  Patch the player-site FMUL at `VA 0x8B7BF` to
reference an injected `10.0 × swim_scale` float.  Fully
independent of walk / roll (different site, different constant,
no cross-coupling).

New slider: `swim_speed_scale`, default 1.0, range 0.1–10.0,
exposed as `--player-swim-scale` (CLI full) / `--swim-speed`
(CLI physics-only).

**Docs updated**:

- `docs/LEARNINGS.md` § "Vanilla base-speed value + independence
  math": renamed to match new terminology + reworked the
  derivation for roll.  Added two sub-sections: "Roll, not run"
  (explains the WHITE/BACK gate + why previous docs were wrong)
  and "Swim speed lives in FUN_0008b700" (records the
  FUN_0008b700 decode + the 10.0 constant + the second-order
  coupling with roll).

**Tests**: 734 passing (up from 719, +15 new): new
`ApplySwimSpeedBehaviour` and extended `IndependenceSemantics`
test classes exercise the full walk×roll×swim slider surface +
back-compat kwargs + `_WALK_SCALE_MIN` divide-by-zero defense.

### enable_dev_menu — actually force selector.xbr this time

The pre-April-2026 version NOPed two `JZ` instructions at
VAs `0x52F7E` + `0x52F95` in a precursor vtable branch.  That
made `pcVar10 = "levels/selector"` in the middle of
`dev_menu_flag_check`, but `pcVar10` is only used by **stage 2
of a three-stage level-name validator chain** at the end of
the function.  Stage 1 (which runs before stage 2) validates
the caller's `param_2` — and in real gameplay it almost
always succeeds because callers pass a known-valid level
string.  Result: the NOPs applied cleanly but had no visible
effect.

The brief April v2 pivot to the "enable cheat buttons" cvar
getter enabled a *different* feature (in-game cheat UI overlay)
and has now been reverted.

**v3 lands at the actual decision point.**  Stage 3 of the
validator chain already hard-codes `PUSH "levels/selector"`
before the final `CALL FUN_00053750` — we just need stages 1
and 2 to fail so flow falls into stage 3.  Two 5-byte
patches, both `CALL FUN_00054520` → `XOR EAX, EAX ; NOP×3`:

```
VA 0x00053384:  E8 97 11 00 00  ->  31 C0 90 90 90
VA 0x000533C3:  E8 58 11 00 00  ->  31 C0 90 90 90
```

After each XOR, `AL = 0` → following `TEST AL, AL` sets
`ZF = 1` → `JZ` fires → flow skips to the next stage.
Stage 3 succeeds naturally (selector.xbr exists in every
vanilla ISO).  Every `FUN_00053750` call from
`dev_menu_flag_check` now loads `levels/selector`.

**Side effect**: this overrides *every* level load through
`dev_menu_flag_check`, including "Load Game" and cutscene
transitions.  Users land in selector regardless of which
level they tried to load.  That's acceptable for the
experimental category; users who want a narrower override
can author a more targeted plugin.

**Activation**: build with `enable_dev_menu`, boot, pick
`New Game` (or any level-loading entry).  The game drops you
into the selector room with portal plaques to every live
level and cutscene.

**Docs updated**:

- `azurik_mod/patches/enable_dev_menu/__init__.py` docstring +
  `README.md` fully rewritten with the v3 design, including a
  post-mortem of why v1 didn't work.
- `docs/LEARNINGS.md` § "enable_dev_menu — three-stage
  validator chain" added — full decode of the validator
  cascade + the XOR-EAX short-circuit approach + generalised
  lessons about branch-patching where the *observable*
  decision is made rather than at a precursor.
- `docs/LEARNINGS.md` § "Native cheat UI — cheats.cpp" kept
  from v2 as a separate discovery record (the cvar-getter
  approach is a viable separate feature for anyone who wants
  the in-game cheat overlay instead of selector).

**Tests**: 8 tests in `EnableDevMenuFeature` pin the two site
VAs, verify vanilla is a CALL to `FUN_00054520`, check the
XOR+NOP replacement, guard against third-validator drift
(confirms `PUSH "levels/selector"` is still wired at
VA 0x53400), assert the 10-byte diff window, confirm
idempotency, and verify the patched bytes decode to XOR+NOPs.

### player_physics — walk/run sliders now independent multipliers

Fix two coupled bugs in the `player_physics` pack.

**What was broken** (pre-April-2026):

1. `walk_scale=3` made the player ~43% of vanilla speed, not 3×.
   The patch injected a literal `3.0` into the XBE under the
   docstring's claim that vanilla `CritterData.run_speed` was
   always `1.0`.  lldb at VA `0x00085F65` proved vanilla is
   actually `7.0` — so injecting `3.0` dropped the base below
   vanilla instead of boosting it.
2. `run_scale` had "no noticeable effect" because any non-default
   slider triggered BOTH patch sites.  Setting `run_scale=3` alone
   also silently rewrote the walk-site base to `1.0 × walk_scale
   (=1.0) = 1.0`, dropping the base from 7.0 to 1.0 and masking
   the run boost.

**What the fix does**:

Reinterpret the two injected floats as a derived PAIR rather than
two independent literals.  With the engine formula:

```
walking = inject_base × raw_stick
running = inject_base × inject_mult × raw_stick
```

and the slider semantics we want:

- `walk_scale`  ≡ multiplier on vanilla walking
- `run_scale`   ≡ multiplier on vanilla running

we solve for:

```
inject_base = _VANILLA_PLAYER_BASE_SPEED × walk_scale   # = 7 × walk_scale
inject_mult = _VANILLA_RUN_MULTIPLIER    × run_scale / walk_scale   # = 3 × run_scale / walk_scale
```

so that:

- walking = `7 × walk_scale × raw_stick` = `walk_scale × vanilla_walking`
- running = `7 × walk_scale × 3 × run_scale / walk_scale × raw_stick`
         = `21 × run_scale × raw_stick` = `run_scale × vanilla_running`

The `walk_scale` cancels cleanly in the running path — each
slider now scales only its own baseline.

**Verification** — new `IndependenceSemantics` test class in
`tests/test_player_speed.py` sweeps 6 slider combinations
including all the pre-fix failure modes
(`(walk=3, run=1)` must NOT affect running;
`(walk=1, run=3)` must NOT affect walking) and asserts the
expected walking/running speeds for each combo.  Plus defensive
`_WALK_SCALE_MIN = 0.01` clamp on the divide-by-zero edge of the
independence math.

**User impact** — if you already built an ISO with
`walk_scale ≠ 1.0` or `run_scale ≠ 1.0`, rebuild with the new
code to get the intended multiplier behavior.  The pre-fix
behavior was consistently "silently slower + weird coupling"
rather than what the slider label promised.

**Docs updated**:

- `shims/include/azurik.h` CritterData comments: the `(= 1.0)`
  annotation on `walk_speed` / `run_speed` was a lie inherited
  from the old patch's assumption; corrected to
  `(vanilla=7.0 for player; see above)`.
- `docs/LEARNINGS.md` § Player movement: new sub-section pinning
  the `7.0` vanilla value + the independence math derivation so
  future patches layering on top don't fall into the same trap.

**Drift guards**: 719 passed / 1 skipped (up from 715 — 4 new
IndependenceSemantics cases landed).

### Audio cleanup — unused imports out, gitignore for extraction dirs

Small cleanup pass on the audio module after the codec-RE closed.

- Removed unused ``asdict`` + ``re`` imports from
  ``azurik_mod.xbe_tools.audio_dump`` (neither referenced after
  the ``likely-audio`` → ``non-audio`` relabel).
- Added gitignore entries for ``audio_out/`` + ``Azurik Audio/``
  + ``**/waves/`` so users who extract game audio into a working
  tree don't accidentally commit derived game content.  The
  ``audio dump`` tool produces all three patterns; one
  ``manifest.json`` per source XBR + hundreds of ``.bin``/``.wav``
  files under ``waves/`` adds up fast.
- Added a bulk-extraction recipe to ``docs/TOOLS.md`` for
  running ``audio dump`` against every wave-bearing XBR in the
  ISO at once — vanilla yields 255 playable ``.wav`` files
  across 36 XBRs (2,266 total TOC entries, most empty
  placeholders or non-audio data per the April 2026 RE).

**Drift guards**: 715 passed / 1 skipped.

### fx.xbr wave codec — RE closed (no custom decoder exists)

The xemu-debug breakpoint at ``load_asset_by_fourcc`` (hit by the
user) combined with a Ghidra xref walk from there pinned the
entire wave pipeline.  The key finding: **Azurik has no custom
wave codec to reverse.**

**Static call chain** (via ``azurik-mod xrefs`` +
``call-graph``):

::

    load_asset_by_fourcc(0x65766177, 1)        @ VA 0x000A67A0
        ↓  called from
    FUN_000A20C0 (per-frame sound tick)        @ VA 0x000A20C0
        ↓  allocates sound object
    FUN_000AE030 (factory)                     @ VA 0x000AE030
        ↓  vtable slot +0x34 → init method
    FUN_000AC6F0                               @ VA 0x000AC6F0
        ↓  delegates header parse
    FUN_000AC400                               @ VA 0x000AC400
        ↓  fills WAVEFORMATEX
    DSOUND::DirectSoundCreateBuffer
    DSOUND::IDirectSoundBuffer_SetBufferData(buf, wave_entry + 16, N)

Xbox DirectSound decodes ``WAVE_FORMAT_XBOX_ADPCM`` (0x0069) in
hardware; ``FUN_000AC6F0`` just builds a WAVEFORMATEX from the
16-byte header and hands the raw bytes at ``wave_entry + 16`` to
``IDirectSoundBuffer_SetBufferData``.

**Corrected 16-byte header** (was mis-decoded earlier as
20 bytes with a ``format_magic`` u32 at +0x08):

::

    +0x00  u32  sample_rate
    +0x04  u32  sample_count
    +0x08  u8   channels          (1 or 2)
    +0x09  u8   bits_per_sample   (PCM: 8 or 16; XADPCM: 4)
    +0x0A  u8   (unused)
    +0x0B  u8   codec_id          (0 = PCM, 1 = Xbox ADPCM)
    +0x0C  u32  (unused — padding)
    +0x10  ...  payload fed to DirectSound

The engine's parser rejects any entry whose ``codec_id`` isn't
in ``{0, 1}``; ``FUN_000AC6F0`` silently aborts on failure →
sound object never created → no playback attempted.

**Reclassified fx.xbr distribution** (700 entries):

| Classification    | Count | Was (before) |
|-------------------|------:|-------------:|
| ``xbox-adpcm``    |   103 |          103 |
| ``pcm-raw``       |     0 |            0 |
| ``non-audio`` ★   |   557 | 448 ``likely-audio`` + 109 mis-tagged animation |
| ``likely-animation`` |  9 |          118 |
| ``too-small``     |    31 |           31 |

★ The ``non-audio`` classification replaces ``likely-audio``.
The earlier label implied "audio we haven't decoded"; the RE
proved those bytes are NEVER consumed as audio by the engine
either, so there's nothing for us to decode.  Relabelled
accordingly so users stop hunting for a codec that doesn't exist.

**Code changes**:

- ``parse_wave_header`` now uses the engine's byte-per-field
  layout (``channels / bits_per_sample / codec_id`` as
  individual ``u8`` reads, not a packed ``format_magic`` u32).
  Matches what the real parser (``FUN_000AC400``) does.
- Payload stripping uses ``wave_entry + 16`` (was 20), aligning
  with ``IDirectSoundBuffer_SetBufferData``'s actual argument.
- Classification labels: ``likely-audio`` → ``non-audio``
  throughout.  ``DumpReport.likely_audio`` field → ``non_audio``.
  Old JSON consumers must rename the key.
- Module + CLI docstrings rewritten around the full RE trail;
  "we don't know what codec these use" disclaimer removed.
- ``docs/LEARNINGS.md`` § fx.xbr wave codec rewritten with the
  final conclusions + ruled-out hypotheses for posterity.

**Regression coverage**: 2 new tests pinning the 103-entry
xbox-adpcm count and the non-audio >500 invariant.  Updated 5
existing tests that used the old ``likely-audio`` label.

**Drift guards**: 715 passed / 1 skipped (up from 714).

### Audio extractor — duplicate detection + raw-PCM previews for the 448 undecoded entries

The April 2026 audio pass decoded the 103 ``xbox-adpcm``
entries, but the remaining **448 ``likely-audio`` entries** carry
no recognisable header and their codec isn't reversed yet.  This
commit ships the pragmatic workflow for those 448 entries:

**Duplicate detection** (default, always on).  Every entry whose
first 32 bytes + total size match an earlier one gets a
``duplicate_of`` field in ``manifest.json`` pointing at the
canonical index.  In vanilla ``fx.xbr`` this surfaces **48
duplicates** across all classifications — same SFX referenced
by multiple symbolic names from ``index.xbr``.  Deduplicating
the working set cuts RE cycles on redundant payloads.

**Raw-PCM preview wrappers** (opt-in via ``--raw-previews``).
Emits ``*.preview.wav`` alongside every likely-audio entry,
wrapping the raw bytes as 16-bit mono PCM at 22050 Hz (the
most common Azurik rate).  The output is **NOT** the intended
audio — the real codec isn't decoded — it's a diagnostic WAV
that lets an analyst drop each blob into Audacity for waveform
/ spectrogram inspection.  Useful for spotting codec-frame
boundaries by eye, confirming duplicates visually, and
validating that a blob is actually audio vs binary garbage.
Preview sample rate override: ``--preview-sample-rate 44100``.

**The RE trail**.  ``docs/LEARNINGS.md`` § fx.xbr wave codec
documents what's been ruled out (raw PCM, headerless IMA,
standard MS/Xbox ADPCM block sizes, every common container
magic) + the most likely decoder callsite to bisect:
``load_asset_by_fourcc`` @ VA ``0x000A67A0``.  Future RE
sessions have a concrete starting point instead of a
blank-page problem.

Vanilla ``fx.xbr`` numbers after this pass (700 entries):

- 103 xbox-adpcm (header decoded, ``.wav`` emitted)
- 448 likely-audio — **421 preview WAVs** + **27 duplicates skipped**
-  48 total duplicates detected (across all classifications)
- 118 likely-animation
-  31 too-small

**API additions**:

- ``build_raw_preview_wav(payload, *, sample_rate, channels,
  bits_per_sample)`` — public helper for Python callers that
  want to wrap arbitrary bytes as a diagnostic RIFF/WAVE.
- ``WaveEntry.duplicate_of`` + ``WaveEntry.preview_output_rel``
  fields + corresponding ``manifest.json`` shape.
- ``DumpReport.duplicates_detected`` / ``preview_wav_written``
  counters for scripts consuming the report.

6 new regression tests in ``RawPreviewWav`` +
``DuplicateDetection`` test classes pin the RIFF shape, the
sample-rate override, odd-payload padding, canonical-index
pointing, and the dedup-aware preview skip.

### Audio extractor now decodes headers + wraps WAV; roadmap cleanup

**`audio dump` — xbox-adpcm header decoding + WAV wrapping shipped**

During the April 2026 audio pass we identified the 20-byte header
that 100 of 700 ``fx.xbr`` wave entries carry:

    +0x00  u32  sample_rate    (8000 / 11025 / 22050 / 32000 / 44100)
    +0x04  u32  sample_count   (duration = count / rate)
    +0x08  u32  format_magic   (0x01000401 = mono 4-bit Xbox ADPCM)
    +0x0C  u32  reserved (0)
    +0x10  u32  reserved (0)
    +0x14  ...  codec payload

The ``format_magic`` dword decomposes byte-for-byte as
``channels = byte[0]``, ``bits_per_sample = byte[1]``,
``codec_id = byte[3]`` — matching what the new ``parse_wave_header``
helper returns in ``WaveHeader``.  For every recognised header
the tool now emits a proper RIFF/WAVE file using
``WAVE_FORMAT_XBOX_ADPCM`` (0x0069) or ``WAVE_FORMAT_PCM``
alongside the raw ``.bin``, so vgmstream / Audacity / ffmpeg can
pick them up directly.

**Other new capabilities**:

- ``--index-xbr path/to/index.xbr`` pulls symbolic asset names
  (``fx/sound/<entity>/<key>``) from the index.xbr string pool
  into every recognised-codec manifest entry as
  ``probable_name``.
- ``--no-wav`` to suppress the RIFF wrapping for users who want
  raw bytes only.
- Two new manifest classifications: ``xbox-adpcm`` and
  ``pcm-raw`` (on top of the existing ``likely-audio`` /
  ``likely-animation`` / ``too-small`` labels).
- Richer manifest: every xbox-adpcm entry now ships with
  decoded ``{sample_rate, sample_count, duration_ms, channels,
  bits_per_sample, codec_id, format_magic}``.

**Vanilla ``fx.xbr`` breakdown** (700 entries):

- 103 **xbox-adpcm** (header-decoded, ``.wav`` written)
-   0 pcm-raw
- 448 likely-audio (no header, raw bytes only)
- 118 likely-animation (Maya particle-system data)
-  31 too-small

Regression coverage: 10 new tests in
``tests/test_audio_and_plugins.py`` (``WaveHeaderParser`` +
``AudioDumpWithHeader`` classes) pin the header parse, the
byte-for-byte format-magic decomposition, and the WAV wrapper's
RIFF shape.  The ``AudioDumpVanilla`` end-to-end test now
verifies the 103-entry xbox-adpcm count against the real
``fx.xbr``.

**Roadmap cleanup**

- Deleted the duplicate "Tier 2 — Planned (high ROI, remaining)"
  section in ``docs/TOOLING_ROADMAP.md``.  Every entry (4, 5, 6,
  7, 8, 9, 10) was already documented as shipped in the "Tier 2
  — Shipped" block above it — the "Planned" block was stale
  since the Tier 2 batch landed.  Roadmap lost ~150 lines of
  duplication.
- "Tier 3 — Shipped (mostly)" renamed to "Tier 3 — Shipped"
  now that the audio codec gap is closed.
- Scoring table bumps #14 from ROI=3 (shipped partial) → ROI=4
  (shipped).
- Entry #17 (Save-file editor) reworded: the "signature
  re-hashing TODO" dangler was superseded by
  ``qol_skip_save_signature``; the doc now points to
  ``docs/PATCHES.md`` for the unblock.
- ``docs/TOOLS.md`` ``audio dump`` section rewritten with the
  new flag surface + usage examples.

**Drift guards**: 708 passed / 1 skipped (up from 698).

### Entity Editor mouse-wheel bug fix + registry memoisation

**Bug fixes**

- **Entity Editor mouse-wheel leak** — ``gui/pages/entity_editor.py``
  used ``canvas.bind_all("<MouseWheel>", ...)`` with no
  ``<Enter>`` / ``<Leave>`` gating, so wheel events on ANY other
  page (Randomize, Patches, Build & Logs, …) fired into the
  Entity Editor's canvas in addition to their own scroller.  The
  invisible Entity Editor would jitter-scroll while the user was
  reading a different page; switching tabs made the scrollbar
  jump by whatever delta accumulated.  Also the delta-normalisation
  (``event.delta / 120``) was Windows-only — macOS
  (delta ±1..±3) and Linux (``<Button-4>`` / ``<Button-5>``)
  produced zero or wrong scroll.  Fixed by copying the
  ``widgets.ScrollableFrame`` pattern: enter/leave gating + a
  three-axis delta normaliser that handles Windows / macOS / Linux.

**Perf**

- **Config registry now memoised in-process**
  (``gui/backend._load_registry``).  The 876 KB JSON was re-parsed
  on every ``list_sections()`` / ``list_entities(section)`` call —
  20-30 ms per dropdown flick in the Config Editor.  Memoised by
  ``(path, mtime_ns, size)`` so an edit on disk transparently
  drops the cache; wall-clock benchmark drops from ~40 ms per
  (sections + entities) pair to **0.013 ms** (~3000× faster).
  ``gui/pages/config_editor._show_registry_data`` shares the same
  cache instead of re-parsing again.

**Cleanup**

- Removed the lingering unused ``from azurik_mod.patches.fps_unlock
  import apply_fps_patches`` in ``azurik_mod/randomizer/commands.py``
  — ``apply_pack(pack, xbe, params)`` dispatches through the
  registry, so the direct ``apply_fps_patches`` handle hasn't been
  needed since the unified-dispatcher reorganisation.  The comment
  now documents why the ``import azurik_mod.patches`` line is kept
  (for its ``register_feature`` side effects).

**Patch + GUI audits**

Spot-checked every non-FPS patch's ``PatchSpec`` + ``TrampolinePatch``
``va`` / ``original`` bytes against the real vanilla XBE:

| Pack                          | Sites | Status |
|-------------------------------|-------|--------|
| ``player_physics``            | 3     | OK (gravity .rdata f32 + 2 sliders w/ vanilla guard) |
| ``qol_gem_popups``            | 5     | OK (``loc/english/popups/<gem>`` strings) |
| ``qol_other_popups``          | 9     | OK (``loc/english/popups/<misc>`` strings) |
| ``qol_pickup_anims``          | 1     | OK (``MOV EAX,[EBP+0xC]; FMUL`` @ 0x4C3EE) |
| ``qol_skip_logo``             | 1     | OK (trampoline @ VA 0x5F6E5) |
| ``qol_skip_save_signature``   | 1     | OK (3-byte ``MOV AL, 1 ; RET`` @ 0x5C990) |
| ``enable_dev_menu``           | 2     | OK (two ``JZ`` → ``NOP`` @ 0x52F7E + 0x52F95) |

All vanilla-byte guards match.  Config Editor + Entity Editor
audited page-by-page; the mouse-wheel + registry-cache issues
above were the only real finds.

**Drift guards**: 698 passed / 1 skipped.

### qol_skip_save_signature + startup-perf pass

**New pack: ``qol_skip_save_signature``** (category ``qol``, opt-in).
Three-byte rewrite of ``verify_save_signature`` @ VA ``0x0005C990``
to ``MOV AL, 1 ; RET`` — any save loads regardless of signature, so
``azurik-mod save edit`` output no longer needs the dynamic key
recovery dance documented in ``docs/SAVE_FORMAT.md``.  Write-side
signing is untouched so saves created on a patched XBE still load
on vanilla.  13 new regression tests ([
``tests/test_qol_skip_save_signature.py``](tests/test_qol_skip_save_signature.py))
pin the VA anchor, the 3-byte patch shape, registry entry, and
end-to-end byte delta against the real XBE.  The callsite was
located via Ghidra MCP xrefs on ``"signature.sav"`` + the save-
handling vtable at ``0x0019E260``; write-up in
``docs/PATCHES.md`` § ``qol_skip_save_signature``.

**Startup perf**:

- ``importlib.metadata`` import deferred into ``_iter_entry_points``
  so the ~500 ms ``email.*`` stdlib init only runs if someone actually
  walks plugin entry points (``azurik_mod.plugins``).
- Plugin discovery now **file-cached** under
  ``platformdirs.user_cache_dir("azurik_mod")/plugins_cache.json``
  keyed by ``(purelib_mtime_ns, platlib_mtime_ns, python_version)``.
  When the fingerprint hasn't changed and the last scan found zero
  plugins, ``load_plugins()`` short-circuits without importing
  ``importlib.metadata`` at all.  Common machine with no plugins
  installed goes from ~550 ms → ~360 ms on every ``import
  azurik_mod.patches`` (measured wall-clock, bytecode cache warm).
- ``subprocess`` is now deferred-imported inside
  ``_auto_compile`` — the byte-patch-only code path (every ``qol_*``,
  every ``fps_unlock`` site) no longer pays ~125 ms of stdlib init
  at module load.

**Drift guards**: 698 passed / 1 skipped.

### ISO + GUI perf pass, xdvdfs lookup memoisation, repo cleanup

Optimisation and cleanup pass across the I/O-heavy paths the GUI +
CLI share, plus dead-data trimming.

**Perf**

- **xdvdfs binary lookup memoised** (`iso/xdvdfs.py`).  The
  `$AZURIK_XDVDFS` → `shutil.which` → user-cache → GitHub-release
  probe chain now runs once per process.  Hot callers
  (`extract_iso_to_dir`, `run_xdvdfs` wrappers) skipped the
  resolve tax each invocation before; now they pay it exactly
  once.
- **Cache-key fast path** (`iso/pack.py`, `gui/backend.py`).
  Replaced `Path.resolve() + Path.stat()` with `os.stat +
  os.path.abspath` in the ISO-cache keys — **5.5× faster**
  micro-bench, which matters because Entity Editor tab focus
  re-keys on every paint cycle.
- **`extract_config_from_iso` is now cached** (4-entry LRU keyed
  by ``abspath + mtime_ns + size``, mirroring `extract_xbe_from_iso`).
  The Entity Editor's `_load_variant_defaults` previously ran
  `xdvdfs copy-out` once per entity across every variant section
  on every tab open — ~200 copy-outs per refresh; now collapsed
  to one per ISO per session (and auto-invalidated when the ISO
  changes on disk).
- **Single-file extract helper factored out**
  (`iso/pack.py::_copy_out_bytes`).  `extract_config_from_iso`
  and `extract_xbe_from_iso` used to re-implement the same
  tempdir + `copy-out` + `read_bytes` + validate dance; now they
  both delegate to one function.  Bug fixes (magic-byte
  validation, xdvdfs error surfacing, cleanup on exception)
  now land on both call paths automatically.
- **GitHub API User-Agent**.  `_download_latest` now sends a
  non-default UA header — GitHub rate-limits the default
  `Python-urllib/X.Y` UA aggressively, so first-run xdvdfs fetch
  on a fresh checkout is less likely to 403.
- **`_QueueWriter` line fan-out** (`gui/backend.py`).  Buffer /
  log-file / queue / hook dispatch is now in one `_emit`
  helper; the per-line try/except on log writes is de-duped.
- **ParametricSlider drag flicker** (`gui/widgets.py`).  The
  drag callback used `%.3f` while `set_value` used `%g`, which
  made the numeric entry flicker between ``9.800`` and ``9.8``
  when the user dragged across the default.  Both paths now
  use `%g` uniformly.

**Cleanup**

- **Removed** `scripts/configs/` — duplicate `entity_values.json`
  + legacy README with zero consumers (the canonical copy lives
  at `azurik_mod/config/entity_values.json` and is read by the
  tools directly).
- **`gui/backend.py::extract_config_xbr`** now delegates to
  `azurik_mod.iso.pack.extract_config_from_iso` instead of
  spawning its own `subprocess.run` — one less place for xdvdfs
  behaviour to drift across layers.

**Docs**

- Reworked the `level preview` entry in `docs/TOOLS.md` with the
  full list of what it surfaces + an explicit *"can it render
  maps/images?"* answer (no — it's a text-only asset-reference
  scanner; spatial rendering would need `rdms`/`surf`/`tern`
  parsers we don't have yet).
- `docs/SCRIPTS.md`: dropped the stale `scripts/configs/` section
  and pointed readers at the canonical `azurik_mod/config/` copy.

**Drift guards**: 685 passed / 1 skipped.

### Coverage top-up — vanilla_symbols 272 → 282, +6 save-UI anchors

Audit pass over Ghidra's named-function snapshot vs. our registry
surfaced a handful of genuinely-useful entry points that were
missing, plus several that looked reachable but turned out to be
IAT-thunk label artifacts (Ghidra tagged a name at +5 inside a
``FF 15 <abs32>`` 6-byte ``CALL [mem]`` stub, i.e. mid-operand —
those are dropped in-source with a note).

**New in ``vanilla_symbols.py`` (+10 net)** — all verified to have
valid x86 prologue bytes at the claimed VA:

- *XAPILIB / Xbox device init*: ``XInitDevices`` (the true
  ``0x00187E87`` entry — NOT the ``0x001889CF`` thunk Ghidra
  snapshots also list under the same name).
- *DirectSound*: ``DirectSoundCreate``, ``DirectSoundDoWork``.
- *Direct3D*: ``Direct3D_CreateDevice``.
- *Compiler intrinsics that clang emits implicitly* (cdecl):
  ``__alldiv`` / ``__allmul`` / ``__allshr`` /
  ``__aulldiv`` / ``__aullrem`` / ``__aullshr`` —
  shim authors can now use ``int64_t`` / ``uint64_t`` arithmetic
  without having to link their own 64-bit helpers.

**Dropped (IAT-thunk mid-instruction labels)** — documented inline
in ``azurik_vanilla.h`` so future passes don't re-add them: all
five ``XInput*`` entries, ``XGetDevices``, ``XGetDeviceChanges``,
``XGIsSwizzledFormat``, ``XGUnswizzleRect``, ``XGSwizzleBox``,
``XGSetSurfaceHeader``, ``XAudioCalculatePitch``,
``DirectSoundCreateBuffer``, ``DirectSoundCreateStream``,
``DirectSoundUseFullHRTF``, ``DirectSoundEnterCriticalSection``.
When a shim genuinely needs one of these the fix is to resolve
the IAT slot (``DAT_0018F50C`` + company) and point through it,
not to call the ``FF 15`` stub directly.

**New anchors in ``azurik.h`` (+6)** — UTF-16LE save-slot UI
strings that can be intercepted to rename save slots, localise
playtime copy, or reshape the dev/scratch save flow:

- ``AZURIK_STR_SAVEGAME_FMT_W_VA`` (L"SaveGame #%d")
- ``AZURIK_STR_START_NEW_GAME_W_VA`` (L"Start New Game")
- ``AZURIK_STR_SCRATCH_GAME_W_VA`` (L"scratch game")
- ``AZURIK_STR_DUMMY_TEMP_GAME_W_VA`` (L"DummyTempGame")
- ``AZURIK_STR_DAYS_FMT_W_VA`` (L"%d days")
- ``AZURIK_STR_ONE_DAY_W_VA`` (L"1 day")

``tests/test_va_audit.py::ANCHOR_EXPECTATIONS`` extended for each
with a UTF-16 prefix predicate so drift would immediately fail
the audit.

**Drift guards:** 685 passed / 1 skipped.

### Struct coverage expansion — azurik.h grew 3 → 10 structs

Pinned seven more game-internal struct layouts so shim authors can
reach beyond the player-physics + controller-input surface.  Mined
from Ghidra decomp of ``entity_lookup``, ``config_name_lookup`` /
``config_cell_value``, ``load_asset_by_fourcc``, ``play_movie_fn`` /
``poll_movie``, and ``boot_state_tick`` (plus the April 2026
index.xbr RE in ``docs/LEARNINGS.md``).

**New types in ``shims/include/azurik.h``:**

- **``Entity``** — partial: pinned the ``const char *name`` slot
  at ``+0x00`` (the only universally-present field); tail varies
  per subsystem and is left opaque.
- **``ConfigTable``** + **``ConfigCell``** — runtime handle + cell
  stride for ``config.xbr``'s keyed-table sections.  All five
  header-word offsets (``num_cols`` / ``col_hdr_offset`` /
  ``num_rows`` / ``total_cells`` / ``cell_data_offset``) plus the
  16-byte cell stride are locked with ``_Static_assert``.  Note:
  per-decomp orientation (row-major with ``num_cols`` as the
  innermost stride); the legacy ``scripts/xbr_parser.py`` uses
  the inverted naming, documented inline.
- **``IndexEntry``** + **``IndexRecord``** — index.xbr dispatcher
  entry exposed by ``load_asset_by_fourcc`` + the 20-byte record
  layout decoded in ``LEARNINGS.md`` § index.xbr.  Asserts pin
  ``first_record_idx`` / ``records`` / ``file_base_offset`` /
  ``flags`` on the live entry and the full record stride.
- **``MovieContext``** + **``MovieContextVTable``** — Bink-owned
  opaque state with the vtable-at-offset-0 pattern.  Vtable slots
  observed from ``poll_movie`` (``advance`` / ``is_done``) and
  ``boot_state_tick`` case 2 (``destroy`` at ``+0x10``).  Tail
  is Bink-internal and explicitly left unpinned.

**New VA anchors (4 added):**

- ``AZURIK_MOVIE_STAGED_PATH_VA`` / ``AZURIK_MOVIE_SKIP_TARGET_VA``
  — the two BSS ``char *`` globals the boot state machine reads in
  case 0 to decide what Bink to play next.
- ``AZURIK_FEATURE_CLASS_REGISTRY_BEGIN_VA`` / ``_END_VA`` — the
  parallel name→u32 registry behind ``FUN_000493D0`` (used by
  CritterData feature-class ID population).

**Toolchain fixes shaken out by the push:**

- ``GhidraSyncPlanner._ghidra_type_for`` now accepts ``i32``/``s32``
  as equivalent spellings (the header uses ``i32``; legacy notes
  sometimes quote ``s32``).  Adds ``i8``/``i16``/``i64`` symmetry.
- ``struct_diff._extract_fields`` collapses leading ``*`` from
  field-name tokens back into the type string, so
  ``struct Foo *records`` now parses as name=``records`` with
  c_type=``struct Foo *`` (was lost, causing ``ghidra-sync``
  to push pointer fields as ``undefined4``).
- ``ghidra_snapshot.py``'s default struct-name prefix allow-list
  extended to cover every struct declared in ``azurik.h`` so the
  committed snapshot reflects the full set (10/138 captured,
  up from 3/138).
- ``tests/test_va_audit.py::ANCHOR_EXPECTATIONS`` extended for
  the four new anchors; drift guard still green.

**Drift guards still green:** 685 passed / 1 skipped, including
the ``test_shim_authoring`` battery that compiles the full
``azurik.h`` and verifies every new static-assert resolves.

### Next-wave tooling audit pass — correctness, perf, level-preview rework

Follow-up to the next-wave landing: audited every tool for
correctness, optimisation, and cleanup.

**Correctness fixes:**

- **Bink offset-table layout** (``azurik_mod.xbe_tools.bink_extract``):
  per-audio-track header is 16 B for Bink 1.9 (not 12 B as the
  first draft assumed).  Off-by-4 on the first frame offset
  made ``frame_size(0)`` return the wrong size on every vanilla
  ``.bik``.  Fixed by auto-detecting the layout (16 → 12 → 8 B
  candidates, pick the one that yields monotonic offsets with
  a matching table-end equality) + consuming the trailing
  end-of-stream sentinel the Bink container appends.
- **Decomp cache atomic writes** (``decomp_cache._write``): write
  to ``<path>.tmp`` + ``replace()`` so an interrupted write
  can't leave a half-serialised JSON file the next reader
  trips over.
- **Call-graph edge orientation** (``call_graph._orient_edge``):
  the dead ``if direction == "forward"`` branch returned the
  same tuple as the fallthrough.  Collapsed to a direction-
  agnostic helper (matches the real semantics: call edges have
  fixed caller→callee orientation regardless of walk direction)
  and documented the invariant.

**Optimisations:**

- ``XbrEditor.replace_string_in_tag`` now calls
  ``bytearray.find(needle, lo, hi)`` directly on the in-memory
  buffer instead of copying each TOC entry into a new ``bytes``
  before searching.  Drops allocations to zero on this path.

**Level-preview rework (``xbe_tools.level_preview``):**

Complete rewrite.  The first version emitted *"50 sample
position triples (e.g. (0,0,0))"* and *"strings: 'tdBg~T^',
'dBb!', 'P|v&['"* — i.e. noise the user had to mentally
filter.  The new version emits **structured, mod-actionable
categories**:

- `level_connections`  — ``levels/<elem>/<name>`` (portal
  graph / adjacency).
- `asset_references`   — ``characters/.../...`` etc.
- `localisation_keys`  — ``loc/<lang>/<path>``.
- `cutscene_refs`       — ``bink:<name>.bik``.
- `identifiers`         — ``snake_case`` two-word+ identifiers.
- `raw_strings`         — opt-in catch-all with strict quality
  filter (high-alphanumeric ratio, no repeating runs).

Only string-bearing TOC tags (``node``, ``levl``) are scanned;
binary-heavy tags (``rdms``, ``surf``, ``tern``, ``wave``,
``sdsr``) are skipped for both performance and noise.  28 ms
per 60 MiB level (was seconds and mostly junk).

The `sample_positions` feature was removed.  Every scan
produced (0,0,0) / (0,0,1) / tiny-float noise without a way
to distinguish signal; a proper spatial preview belongs in a
structured parser that understands vertex-count headers, not a
byte-level scan.

**Cleanup:**

- Removed unused imports (``field``, ``Sequence``) from
  ``struct_diff``, ``asset_fingerprint``, ``bink_extract``,
  ``level_preview``.

**Tests:** 9 new regression tests.  651 pass (was 642).

---

### Next-wave tooling (#17 – #26) shipped

All ten next-wave tools from ``docs/TOOLING_ROADMAP.md`` land in
one pass.  Every tool is CLI-accessible via ``azurik-mod`` + has
a Python API for programmatic use + ships with regression tests.

**Shipped:**

- **#17 Save-file editor** — ``azurik-mod save edit`` with
  declarative ``--set`` / ``--plan`` edits for text saves.  Code
  in :mod:`azurik_mod.save_format.editor`.
- **#18 XBR write-back** — ``azurik-mod xbr edit`` with safe
  in-place string + byte replacement
  (:mod:`azurik_mod.xbe_tools.xbr_edit`).
- **#19 Shim test generator** — ``new-shim --emit-test`` emits
  ``test_<name>.py`` with drift-guards for hook VA + replaced
  bytes + feature registration.
- **#20 Call-graph explorer** — ``azurik-mod call-graph`` with
  Graphviz DOT output
  (:mod:`azurik_mod.xbe_tools.call_graph`).
- **#21 Xref aggregator** — ``azurik-mod xrefs`` with ASCII-tree
  dump of callers/callees
  (:mod:`azurik_mod.xbe_tools.xref_aggregator`).
- **#22 Decompile cache** — ``azurik-mod decomp-cache`` with
  content-addressed on-disk memoisation
  (:mod:`azurik_mod.xbe_tools.decomp_cache`).
- **#23 Struct type diff** — ``azurik-mod struct-diff`` against
  live Ghidra (:mod:`azurik_mod.xbe_tools.struct_diff`).
- **#24 Level previewer** — ``azurik-mod level preview`` for
  structural level summaries
  (:mod:`azurik_mod.xbe_tools.level_preview`).
- **#25 Asset fingerprint registry** — ``azurik-mod assets
  fingerprint`` + ``fingerprint-diff``
  (:mod:`azurik_mod.xbe_tools.asset_fingerprint`).
- **#26 Bink frame extractor** — ``azurik-mod movies frames``
  with ffmpeg-backed decoder + metadata-only mode
  (:mod:`azurik_mod.xbe_tools.bink_extract`).

**Foundations:**

- :class:`GhidraClient` gained ``decompile()``,
  ``iter_xrefs_to()``, ``iter_xrefs_from()``, ``get_struct()``,
  ``iter_structs()``; :class:`MockGhidraServer` grew matching
  endpoint coverage (``/functions/{addr}/decompile``,
  ``/xrefs``, ``/structs``, ``/structs/{name}``).
- Mock-server test timeout bumped from 10s to 30s to tolerate
  full-suite parallelism (was flaking under load).

**Tests:** 34 new regression tests.  642 tests passing overall
(up from 608), zero tolerated failures under full-suite load.

### Level-XBR parser — correctness fixes, 6× speedup, new features

``scripts/xbr_parser.py`` verified against real Azurik level files
(``a1.xbr``, ``w1.xbr``, ``town.xbr``) and overhauled for
correctness + performance + ergonomics.  357 tests passing (+20
new).

**Fixes:**

- **Default mode now useful on level XBRs.**  Running
  ``xbr_parser.py a1.xbr`` with no flags used to print
  ``Not a config.xbr file`` and exit 0.  It now shows a full
  stats summary (tag distribution by total bytes + top-10
  largest entries) like ``--stats``.
- **Config-only flags fail loudly on level files.**
  ``--sections`` / ``--find`` / ``--dump-json`` / ``--patch`` /
  ``-s`` / ``-e`` on a level XBR now exits with code 2 and a
  clear error pointing at ``--toc`` / ``--stats`` /
  ``--strings``.  Previously they silently did nothing.
- **``--strings`` noise reduction.**  The old byte-by-byte
  scanner found any run of 4+ printable bytes — producing heaps
  of false positives like ``$|._``, ``UUUU``, ``>!F-``.  The
  new scanner:
    - Requires a **NUL terminator** after the string (40× fewer
      false positives on binary-heavy mesh / surf sections).
    - Filters out candidates with **no alphabetic characters**
      (cuts remaining punctuation chaff).
    - Default ``--min-len`` bumped from 4 to **6** to match real
      Azurik filename / path lengths.
- **Unknown-tag errors.**  ``--strings no_such_tag`` used to
  print an empty result + exit 0; now exits non-zero with the
  list of tags actually present.

**Performance:**

- ``find_strings_in_region`` rewritten from a Python byte loop to
  a compiled-regex ``re.finditer`` pass.  ~6× speedup on real
  data (``town.xbr --strings surf``: **1.4s → 0.23s**).  Pattern
  compilation is cached per min_len across calls.

**New features:**

- **``--stats``**: level-XBR overview mode.  Tag distribution
  sorted by total bytes, % of file, plus top-10 largest individual
  entries with their file offsets.  Answers "what's in this
  level?" at a glance.
- **``--min-len N``**: configurable string-length threshold.
- **``--pattern REGEX``**: filter ``--strings`` output by Python
  regex (e.g. ``'key_|power_|frag_'`` finds real Azurik
  pickups).
- **``--unique``**: dedupe ``--strings`` output; first occurrence
  wins.  Useful for surveying distinct entity names in a level.
- **``--count-only``**: skip per-string output, print only per-
  entry counts + grand total.  Fast summary mode.
- **``--json``**: machine-readable ``--strings`` output with
  offset + value per hit, ready for downstream tooling.

**Tests** (``tests/test_xbr_parser.py``, 20 new):

- 6 cover ``find_strings_in_region`` semantics (NUL-termination,
  min-len threshold, alpha filter, alpha-disable, pattern
  caching).
- 3 pin per-level tag-distribution invariants (every real level
  has ``levl``, ``node``, ``ndbg``, ``surf``, ``rdms``, ``tern``,
  ``wave``, etc.; ``town.xbr`` has 2 node entries as a hub).
- 3 cover pickup-string recoverability from real level files
  (``a1.xbr`` → ``key_air*``; ``town.xbr`` → ``levels/...``
  transitions; ``w1.xbr`` → speech / loc references).
- 7 CLI integration tests: default mode, config-only flag
  rejection, ``--pattern``, ``--count-only``, ``--json``,
  ``--stats`` on town, unknown-tag error surface.
- 1 perf guard: ``town.xbr`` surf scan must complete in under 2s
  (currently ~0.25s; catches any regression to byte-by-byte
  scanning).

All tests skip gracefully when the gamedata fixtures aren't
present, so the suite still runs on hosts without a full Azurik
game install.

### Save-format verified against a real xemu HDD + config-lookup wrappers

Two substantial additions end-to-end: (a) extracted a real Azurik
save from the user's ``xbox_hdd.qcow2`` to validate + correct the
save-format module; (b) implemented the long-deferred
``FUN_000d1420`` / ``FUN_000d1520`` config-lookup wrappers.  337
tests passing (+18 new).

#### Save format — validated + rewritten against real data

Wrote a pure-Python QCOW2 sparse-sector reader + FATX reader (no
qemu / FATX tools needed) and walked xemu's 8 GB HDD image to
extract the full Azurik save container.  Findings contradicted
the initial scaffold's single-20-byte-header assumption — the
scaffold's ``payload_declared_matches_actual`` was **always False**
on real data.

Real format (documented in ``docs/SAVE_FORMAT.md``):

- **No unified header.**  Four distinct ``.sav`` variants, now
  recognised via an ``AzurikSave.kind`` sum type:
  - **Text saves** (``loc.sav``, ``magic.sav``, ``options.sav``):
    ``fileversion=1\n<line>\n<line>\n…`` in ASCII + optional
    binary tail.  Trivially moddable; ``TextSave`` class exposes
    ``lines`` as a list of str and round-trips with the tail
    preserved.
  - **Binary-record saves** (``inv.sav``, ``shared.sav``, level
    saves): 8-byte ``{version, record_count}`` header + opaque
    body.  Per-record decoders are still future work.
  - **Signature** (``signature.sav``): exactly 20 bytes — SHA-1
    digest Azurik uses to validate every other save file.  Hash
    domain (what files are fed to SHA-1 in what order) is the
    remaining unknown blocking write-mods.
  - **Unknown** (short blobs / anything else).
- ``SaveDirectory.from_directory`` now **recurses** into
  subdirectories.  Real saves nest per-level state under
  ``levels/<element>/<level>.sav``; previously the walker only
  saw the root-level files and reported 5 saves when there were
  29.  Summary dict partitions results into ``root_sav_files``
  / ``level_sav_files`` for easier scanning.
- CLI ``save inspect`` output updated: groups root vs level saves,
  shows ``[kind]`` tag, ``version``, ``record_count`` or line
  count + binary-tail bytes.

Small scrubbed real-save fixtures added at
``tests/fixtures/save/``: ``signature.sav`` (20-byte digest, no
PII), ``SaveMeta.xbx`` (38 B UTF-16 metadata, no save names),
truncated 256-byte ``magic_sample.sav`` / ``inv_sample.sav``
for text + binary variants.  Tests use them opportunistically
(skipped when absent so the suite still runs on hosts without
the fixtures).

Legacy aliases preserved (``AzurikSaveFile``, ``SignatureSav``,
``LevelSav``, ``SaveHeader``) so pre-rewrite importers don't
break; they're thin shells over ``AzurikSave``.

#### FUN_000d1420 + FUN_000d1520 config-lookup wrappers

The last two "deferred" vanilla functions from ``docs/SHIMS.md``
§ near-term — both now exposed.  Ghidra investigation revealed
they use **classic calling conventions** that clang supports
natively, so no inline-asm wrapper was needed (unlike the
gravity wrapper's MSVC-RVO contortions):

- **``config_name_lookup``** (``FUN_000d1420``, ``__thiscall``):
  ECX = config table object; stack arg = const char *name;
  callee does ``RET 4``.  Scans the table byte-by-byte for a
  matching name entry, returns an int index/offset.
- **``config_cell_value``** (``FUN_000d1520``, ``__cdecl``):
  all 4 args on stack — ``(int *grid, int row, int col,
  double *default_out)``; returns 80-bit FPU ``float10`` in
  ST(0) which clang handles as a normal ``double`` return.
  Panics (INT3) on out-of-range indices.

Registered in ``vanilla_symbols.py``; new
``VanillaSymbol.calling_convention == "thiscall"`` branch maps
thiscall to ``_name`` (no ``@N`` suffix — empirically confirmed
by a probe compile — matches clang-i386-pe-win32 mangling).

New header ``shims/include/azurik_config.h`` declares both
functions with the right calling-convention attributes.  Shim
authors call them like any other C function.

Tests (``tests/test_config_wrappers.py``, 7 new):
- Registry entries (VAs, calling conv, mangled names).
- Header declarations present + attributes correct.
- Probe shim compiles with exactly the two expected externs.
- Thiscall-mangling contract test — no ``@N`` suffix, so a
  future clang upgrade that changes this surfaces here.

### Deep second-pass audit — correctness + optimisation + UX

A wide correctness + optimisation + UX pass, followed by full
docs overhaul and launcher hardening.  326 tests passing (+20 new).

#### Correctness fixes

- **``SolverState.has_all`` fails closed on malformed inputs**
  (``azurik_mod/randomizer/solver.py``).  Previously a dict with
  non-empty ``items`` but an unrecognised ``type`` fell through
  to ``return True`` — the same class of silently-permissive
  solver check that let the power-placement bug ship.  Now
  returns False for unknown shapes + non-list non-dict inputs;
  pure-empty forms still evaluate as vacuously true (a node
  with no requirements is always reachable).
- **``parse_xbe_sections`` bounds-checks header fields**
  (``azurik_mod/patching/xbe.py``).  Previously a truncated /
  hostile XBE could crash ``struct.unpack_from`` mid-parse or
  raise a bare "subsection not found".  Now validates image
  length, magic, section-count sanity cap, ``base_addr`` vs
  ``section_headers_addr``, end-of-header-array vs image size,
  and each name-pointer before indexing into the buffer.
  Surfaces descriptive ``ValueError``s instead.

#### Optimisations

- **``extract_xbe_from_iso`` caches by (path, mtime, size)**.
  Mirrors the GUI's ``extract_config_xbr`` cache.
  ``cmd_verify_patches --original`` used to extract twice per
  run (once for patched, once for vanilla); now the second call
  is O(0) when the input ISO hasn't changed.  Bounded at 4
  entries to avoid runaway memory on long sessions.
- **Solver DB parse cached at module level**.  Constructing
  ``Solver()`` twice in one run (major items + powers paths)
  used to re-read + re-parse ``logic_db.json`` every time.  Now
  ``_load_logic_db(path)`` memoises by ``(resolved_path, mtime)``;
  two Solver instances share one dict.  Edits to the DB file
  invalidate cleanly.

#### examples/ → ``azurik-mod mod-template``

The ``examples/`` folder shipped three JSONs that drifted out of
sync with reality (``default_settings.json`` in particular had
pre-correction field alignments in deeper sections).  Deleted
entirely; replaced by a new CLI subcommand:

```
azurik-mod mod-template --iso Azurik.iso \
    --section critters_walking --entity goblin -o goblin.json
```

Reads the ISO / config.xbr at runtime, so the output JSON is
ALWAYS truthful.  Users edit values, feed back through
``azurik-mod patch --mod`` or ``randomize-full --config-mod``.
The GUI's Entity Editor tab produces the same JSON shape.
Accepts multiple ``--section`` flags, optional ``--entity``
narrowing, ``--name`` for the embedded mod name, and defaults
to stdout if ``-o`` is omitted (pipe-friendly).

#### Launcher hardening

Both ``Launch Azurik Mod Tools.command`` (macOS/Linux) and
``Launch Azurik Mod Tools.bat`` (Windows) now ``python -c
'import gui'`` BEFORE running the GUI, so a stale install /
missing deps produces a specific "run ``pip install -e .``"
message instead of a flash-and-close console window.

#### Docs overhaul

Second audit found a web of stale references; this pass fixes:

- **Obsolete ``azurik``-``cli`` name → ``azurik-mod``** across 6 files (docs +
  test strings + CLI error messages).  The installed console
  script is ``azurik-mod`` per ``pyproject.toml``; every doc
  example now matches.
- **D1-extend contradictions** resolved.  ``SHIMS.md`` +
  ``AGENT_GUIDE.md`` + ``kernel_imports.py`` docstring were
  still framing D1-extend as deferred future work; they now
  reflect that the runtime resolver shipped (see
  ``docs/D1_EXTEND.md``).
- **Gravity wrapper deferred → shipped** in ``SHIMS.md``.  The
  ``FUN_00085700`` inline-asm wrapper landed weeks ago; the
  "Investigated but deferred" section now documents it as
  shipped + points at the pattern for future RVO targets.
- **Stale tutorial code** in ``SHIMS.md`` step 3-5 updated to
  current ``Feature`` + ``ShimSource`` + feature-folder layout
  (was still referencing ``register_pack`` + flat ``qol.py``).
- **MODDING_GUIDE.md** had 11 broken ``python azurik_mod.py``
  invocations (that script doesn't exist).  Rewrote to
  ``azurik-mod`` + added a top banner pointing at the modern
  targeted docs.
- **Test-count references** removed.  The hardcoded "190+" /
  "306" figures drifted every release; docs now say "run
  ``pytest tests/`` for the count".
- **Path references** fixed: ``patches/<feature>.py`` →
  ``patches/<feature>/__init__.py`` (feature-folder layout is
  now canonical).
- **README docs/ index** now lists ``D1_EXTEND.md``,
  ``D2_NXDK.md``, ``SAVE_FORMAT.md``, ``RANDOMIZER_AUDIT.md``
  which landed without README updates.
- **RANDOMIZER_AUDIT.md broken anchor** fixed; ``G1 —
  Gem-size-aware shuffle`` now points at a real section.

#### Tests (``tests/test_deep_pass.py``, 20 new)

- 7 tests pin ``SolverState.has_all`` behaviour (empty list /
  empty dict / empty items → vacuously True; unknown shape /
  string input → False; real all_of / any_of still work).
- 5 tests cover ``parse_xbe_sections`` bounds
  (too-small / bad-magic / insane-section-count / headers-past-
  EOF all rejected; vanilla XBE still parses).
- 2 tests pin the ``extract_xbe_from_iso`` cache
  (reuse + mtime invalidation).
- 1 test pins the module-level Solver DB cache.
- 1 test exercises the ``mod-template`` CLI end-to-end
  against a real config.xbr.
- 1 test enforces the ``examples/`` folder stays deleted.
- 1 test greps every doc + every .py file for the obsolete ``azurik``-``cli``
  references + fails if any appear.
- 2 tests pin the dep-check ``import gui`` guard + ``pip
  install -e .`` hint in both launchers.

### Deep randomizer audit — 2 CRITICAL bugs fixed + extension roadmap

Full correctness + robustness audit of the randomizer subsystem
(``azurik_mod/randomizer/``) in preparation for the user's planned
heavy extension work.  Two critical bugs found + fixed; several
known-but-not-yet-fixed bugs documented as pinned contract tests
so they can't silently change; complete extension roadmap in a
new top-level doc.  306 tests passing (+10 new).

#### CRITICAL — power-placement solvability check was vacuous

``cmd_randomize``'s power-shuffle path built the solver-check
mapping with a synthesised canonical name
(``f"power_{pu['element']}"``) instead of the real entity name
(``pu["name"]``).  Powers named with the ``_a3`` suffix
(``power_water_a3``, the A3-level water power) never matched
their real node's vanilla pickup list, so
``build_placement_from_shuffle`` returned an empty placement dict
and ``solve()`` happily reported the VANILLA game as solvable —
every shuffle passed regardless of whether it was actually
winnable.

Proof of impact: the SAME placement that canonical-naming proves
"solvable" was rejected by the fixed code.  E.g. moving the A3
water power to ``power_life`` softlocks the game (no water power
remains in a reachable node); buggy check said OK, fixed check
correctly rejects.

Every ``cmd_randomize --powers`` invocation that used the broken
solver check shipped with a silently-bypassed solvability
guarantee.  Fix: pass ``pu["name"]`` (the real entity name) so
the lookup succeeds.

#### HIGH — gem-shuffle skip produced identifier collisions

When a post-shuffle gem base (e.g. ``obsidian``) didn't fit the
target gem's existing field length, the code ``continue``'d —
leaving that gem at its original name while the SHUFFLE had
already rotated other names into place.  Result: two gems in the
same level could end up with the same identifier.

Fixed by switching to a two-pass pattern (``planned_names`` /
``skipped_slots``) that detects post-skip duplicates and emits a
clear ``WARNING`` with the collision set.  Applies to both
``cmd_randomize`` (unified gem path) and ``cmd_randomize_gems``
(legacy).  The proper long-term fix (size-aware shuffle) is
tracked as roadmap item G1.

#### New top-level doc: ``docs/RANDOMIZER_AUDIT.md``

Comprehensive audit output covering:

- **Pipeline correctness** — walk of every step in
  ``cmd_randomize_full``, assumptions, failure modes, and
  whether the output is validated before the next step.
- **Fixed in this round** — the two bugs above with proofs
  and before/after snippets.
- **9 known bugs** (R1..R9) — each with file:line, severity,
  rationale, and proposed fix.  Pinned as contract tests so a
  future contributor fixing them must update the doc.
- **Magic constants + VA fragility** — every hardcoded table
  in ``shufflers.py`` with a risk assessment + long-term fix
  recommendation.
- **Solver coverage gaps** — why ``logic_db.json`` can lie
  about solvability + a CI cross-reference idea.
- **Determinism analysis** — what IS stable across seeds,
  what IS NOT stable across flag sets.
- **Extension roadmap** — five prioritised refactoring targets
  (P1..P5) with effort estimates for the user's planned
  heavy extension work.

#### Regression tests (``tests/test_randomizer_audit.py``, 10 tests)

- 4 tests pin the power-placement fix (empty-placement-from-
  canonical-name baseline, non-empty-from-real-name, buggy path
  vacuously passes while real path correctly rejects, source
  uses ``pu["name"]``).
- 2 tests pin the gem collision check in both ``cmd_randomize``
  and ``cmd_randomize_gems``.
- 2 tests guarantee the audit doc exists + is cross-linked from
  the code (so the roadmap stays discoverable).
- 2 tests pin known-but-NOT-yet-fixed bugs as contract tests
  (R4 inventory-is-set, R6 unconditional-level-write) so the
  next contributor fixing them sees a clear "update the audit
  doc" prompt in the test failure.

### Optimization + developer-ergonomics pass

Round of surgical optimizations + quality-of-life refinements
based on a full-tree profiling / dead-code audit.  Every change
includes a regression test so nothing silently undoes itself.
296 tests passing (+10 new).

#### Developer ergonomics — stale-.o auto-rebuild

Editing ``shim.c`` and re-running a patch used to silently reuse
the stale ``.o`` because ``apply_trampoline_patch`` only built
when the ``.o`` was missing outright.  Now ``apply.py`` compares
``shim.c`` vs ``shim.o`` mtimes and rebuilds whenever the source
is newer.  ``AZURIK_SHIM_FORCE_REBUILD=1`` env var forces an
unconditional rebuild; ``AZURIK_SHIM_NO_AUTOCOMPILE=1`` still
disables the whole mechanism.  Fixes a classic "why didn't my
change take effect?" debugging trap.

#### Developer ergonomics — friendly clang-missing error

``shims/toolchain/compile.sh`` now checks for ``clang`` on PATH
BEFORE ``exec``ing it.  When missing, prints a multi-line install-
hint covering macOS (Xcode CLT / Homebrew), Debian/Ubuntu, Fedora,
Arch, and Windows — instead of the shell's default ``clang:
command not found``.

#### Performance — keyed-tables partial load

``load_all_tables(sections=[...])`` accepts an optional
iterable of section names and only parses those.  Used from
``cmd_randomize_full``'s ``_keyed_patches`` path so a
``--config-mod`` that touches one section doesn't force parsing
of all keyed tables.  Default ``None`` preserves the old full-
load behaviour.  Unknown section names are silently ignored so
callers don't have to pre-filter against ``KEYED_SECTIONS``.

#### Performance — GUI temp-dir reuse + cache invalidation

``gui/backend.py`` ``extract_config_xbr`` was creating a fresh
``tempfile.mkdtemp`` on every call, accumulating gigabytes of
unpacked config data over a long session (Entity Editor tab
reloads).  Now caches by ISO-path + mtime + size; repeat calls
with the same unchanged ISO reuse the temp file, modifying the
ISO invalidates atomically, and ``cleanup_temp_dirs`` clears the
cache too.

#### examples/ hygiene

``examples/default_settings.json`` had an authoritative-sounding
description but deeper sections (critters_walking etc.) carry
pre-correction row/column alignments.  The name+description now
explicitly flag it as a historical dump, with guidance to use
``azurik-mod dump --iso ...`` for the canonical live view.

#### Considered + declined

- **Memoising `parse_xbe_sections`**: implemented + reverted.
  Plain ``bytearray`` doesn't support attribute assignment, so
  the cache never attached in practice.  Benchmarking showed
  real-world overhead is ~25 ms per build (168 µs × ~150 parses)
  — not a bottleneck worth the complexity of bytearray subclassing
  or id()-keyed dict bookkeeping.  Documented in the docstring.

#### Regression tests (``tests/test_optimizations.py``, 11 tests)

- 3 tests on the stale-``.o`` decision logic (stale → rebuild,
  fresh → skip, source-level guard that the mtime comparison
  stays in ``apply_trampoline_patch``).
- 5 tests on the keyed-table section filter (default loads all,
  filter limits results, unknown names ignored, mixed known +
  unknown, empty filter returns ``{}``).
- 2 tests on the GUI temp-dir cache (same ISO reuses temp, ISO
  mtime/size bump invalidates atomically).
- 1 test on the clang-missing message (skipped on hosts with
  system clang at /usr/bin).

### Dead-code + orphan-wiring audit — critical fix + cleanup

Comprehensive sweep of `azurik_mod/`, `gui/`, and `scripts/` for
dead code, orphan wiring (like the Entity Editor bug), and stale
migration remnants.  Report in this commit + regression tests in
`tests/test_audit_regressions.py` (9 new tests; 286 total, up
from 277).

#### Critical bug fix

- **Connection-shuffler `NameError` crash** (`cmd_randomize_full`
  step 6).  The connections-randomisation path at
  `azurik_mod/randomizer/commands.py:1054-1057` references
  `EXCLUDE_TRANSITIONS` and `VALID_DEST_LEVELS` without having
  imported them from `shufflers.py`.  With default CLI flags
  (`--no-connections` NOT passed), this path runs on EVERY
  `randomize-full` invocation and would raise `NameError` as
  soon as any level transition was scanned.  Adding the two
  imports + regression tests that pin them resolvable through
  `commands` module globals.

#### Orphan wiring cleanup

- **`build_request` pub/sub path removed** — `BuildPage`
  subscribed but no page ever published.  `_on_build_request`
  handler and the subscribe() call are both deleted; the
  single build entry point is now the Start-build button
  (which has always been the only real trigger).
- **`build_done` event is now actually published** — previously
  the `app._sync_status` subscriber wired into this event
  never ran because `BuildPage._handle_done` forgot to emit.
  Emitted now on every build completion (success or failure)
  so the status bar refreshes with `last_seed` / `last_output`.

#### Dead code removed

- **`commands.py` unused imports**: `_power_element`,
  `_frag_parts`, `_gem_base_type` were imported from
  `shufflers.py` but never used.  Removed.
- **`AppState.output_dir` field + `set_output` method**: a
  migration remnant — the output-path UX moved into the
  Project page per-build, with `RandomizerConfig.output_path`
  carrying the final value.  No callers; removed.
- **`PatchesPage.get_pack_flags` / `get_pack_params`**: dead
  accessors.  The Build page reads `AppState.enabled_packs`
  and `AppState.pack_params` directly; the widget-side getters
  had zero callers.

#### Regression guards

`tests/test_audit_regressions.py` pins every fix:

- Connection-shuffler imports are resolvable through
  `commands` module globals, and the three dead imports stay
  out.
- `BuildPage._handle_done` source literally contains
  `bus.emit("build_done"`.
- `build_request` subscribe line is gone AND the orphan
  handler is gone.
- `AppState.output_dir` / `set_output` / PatchesPage getters
  stay removed.  Each test includes a 3-line rationale so
  anyone re-adding the symbol gets a clear "here's why this
  was dead, here's what to wire" message.

#### Preserved as intentional standalone / utility code

The audit flagged these but they're NOT dead — they're
intentional standalone surfaces:

- `azurik_mod/randomizer/level_editor.py` +
  `parse_level_toc.py` — standalone CLI utilities, not
  imported by the main pipeline.
- `azurik_mod/randomizer/solver.py` query helpers
  (`get_randomizer_groups`, `get_all_pickup_locations`, etc.)
  — used by solver's `__main__` block.
- `azurik_mod/config/keyed_tables.py` helpers — used by the
  module's `main()` for script-mode inspection.
- `scripts/*` directory — RE / analysis utilities
  documented in `MODDING_GUIDE.md`, not invoked by the build
  pipeline.

#### Docstring fixes

- `AppState` bus-event docstring now lists only the events
  that are actually emitted + their real subscribers (was
  listing phantom `output_changed`, `packs_changed` watchers).
- `RandomizerConfig.config_edits` docstring updated to
  reference the Entity Editor (which DOES populate it via
  the Build page merge) instead of the Config Editor (which
  is read-only).

### Entity Editor — critical build-wire fix + UX refinements

The Entity Editor tab had a **silent orphan bug**: users could make
hundreds of property edits, click "Start build" on the Build page,
and see their edits quietly discarded.  The tab's
``get_pending_mod()`` method was defined but never called from
anywhere, and ``RandomizerConfig.config_edits`` was never populated
from the UI — the full edit buffer simply evaporated at build time.

This release wires the editor into the build pipeline and adds
several UX improvements:

#### Critical fix: edits now reach the build

- ``BuildPage._merge_config_edits`` folds the editor's pending mod
  into the CLI's ``--config-mod`` JSON at build time.  Both
  grouped ``sections`` (variant records) and ``_keyed_patches``
  (keyed-table cells) merge correctly; on per-cell conflict the
  editor's value wins over any file-sourced ``config_edits``.
- Deep-copy invariant: the merge never mutates the input dicts,
  so file-sourced edits remain intact if the build is retried.
- Build log now surfaces how many editor edits contributed to a
  given run (``+ Entity Editor contributes N pending edits``).

#### UX refinements

- **Entity search/filter**: typing in the new "Filter entities:"
  box narrows the dropdown live — essential for sections with
  500+ critters where scrolling is hopeless.  Status label
  shows "N of M match" / "M entities" depending on filter state.
- **Per-entity edit indicator**: entities with pending edits are
  prefixed with a bullet + count ("● goblin (3)") in the dropdown.
  A green "(3 edits)" label next to the combo tracks the currently-
  selected entity in real time.
- **Reset This Entity** button: clears edits for the currently-
  selected entity only, with a confirm dialog.  Complements the
  existing "Reset All Edits" (which also now has a confirm).
- **Import Mod JSON** button: round-trips any previously-exported
  mod JSON back into the editor's edit buffer — merges with
  existing edits rather than replacing them.  Parses both the
  grouped-sections and ``_keyed_patches`` shapes; malformed
  entries are skipped (not fatal) with a status-line summary.
- **Edit-count breakdown**: the edit-count label now shows
  "N edit(s) across X entities / Y sections" instead of just
  a flat count — gives users a sense of the scope of their changes.

#### Internal reshuffle

- ``_on_entity_change`` + ``_rebuild_property_grid`` defensively
  normalise the combobox value to strip the edit-indicator
  decoration before using it as a lookup key.  ``_randomize_entity``
  and every other entity-reading code path uses the same
  normalisation — no more stray decorations leaking into registry
  lookups.

#### Tests

18 new tests in ``tests/test_entity_editor.py`` (total 277, up
from 259):

- Surface-area drift guard (every expected method exists).
- Label-decoration reversibility (``_format_entity_label`` /
  ``_unformat_entity_label`` round-trip, idempotent on
  undecorated input, tolerant of manually-typed parens).
- ``get_pending_mod`` shape (variant → ``sections``, keyed →
  ``_keyed_patches``, empty edits → ``None``).
- **Build-page merge** — the critical orphan-fix:
  7 subtests covering every combination of file / editor edits
  including conflict resolution (editor-wins) and
  non-mutation invariants.
- Import round-trip (merge vs replace, malformed-entry
  skipping).

### VA audit + new `AZURIK_PLAYER_STATE_PTR_ARRAY_VA` anchor

Comprehensive VA-correctness sweep via Ghidra MCP + real XBE bytes.
All 16 existing VA anchors + 5 vanilla-function entries verified;
no drift.  New regression suite `tests/test_va_audit.py` (5 tests,
26 subtests) pins every anchor with:

- Section membership (`.rdata` vs `.data` vs BSS) — catches
  accidental VA drift that lands in the wrong section silently.
- Byte-content predicates for initialised constants (gravity == 9.8,
  run multiplier == 3.0, `garret4\0`, float constants 0.0/0.5/1.0,
  active-player index == 4).
- BSS verification (empty-past-raw-size OR zero-filled on disk) for
  runtime-init anchors.
- First-byte prologue check on every vanilla function VA.
- **Drift guard**: regex-scans `azurik.h` for every `AZURIK_*_VA`
  macro and fails if one isn't covered by `ANCHOR_EXPECTATIONS`,
  so new anchors can't land without a matching audit entry.

One new anchor added during the gap analysis:
- **`AZURIK_PLAYER_STATE_PTR_ARRAY_VA` = `0x001BE314`** — 4 × 4-byte
  per-player state-object pointer slots, indexed by the XInput
  polling path (`FUN_000A2880`).  BSS; pairs naturally with the
  controller-state block at `0x0037BE98`.

Also verified against Ghidra:
- `AZURIK_CONTROLLER_STRIDE = 0x54` confirmed by
  `FUN_000A2880`'s ``IMUL ESI, ESI, 0x54`` at VA `0x000A288D`.
- All 151 static kernel ordinals in `AZURIK_KERNEL_ORDINALS`
  match the XBE thunk table at `0x18F3A0` exactly (zero drift).
- `EXTENDED_KERNEL_ORDINALS` has no ordinal collisions with the
  static set.
- Spot-checks pass for 10 canonical ordinals across static +
  extended (DbgPrint, NtClose, NtCreateFile, NtOpenFile,
  KeQueryPerformanceCounter, HalReturnToFirmware, RtlInitAnsiString,
  DbgBreakPoint, RtlZeroMemory, XboxKrnlVersion).

### FUN_00085700 gravity-integration wrapper + save-file format scaffold

Two substantial additions in one pass.  Both landed with full test
coverage + documentation; both exposed through the standard
authoring surfaces (shim C headers, Python module, CLI).

#### Gravity-integration wrapper (A3-plus)

Vanilla `FUN_00085700` uses an MSVC-style fastcall + RVO ABI
(`ECX + EDX + EAX-for-output + ESI-for-context + stack float`)
that no clang calling-convention attribute expresses natively.
New infrastructure to bridge the gap:

- **Inline-asm wrapper** at `shims/shared/gravity_integrate.c`
  exposes a clean `stdcall(20)` C API (`azurik_gravity_integrate`)
  and manually sets up every register inside a single atomic
  inline-asm block before the CALL — so clang can't reorder
  register setup past the EAX write.  Satisfies `__fltused`
  locally via an `__asm__` label so the wrapper has zero
  external dependencies beyond the vanilla target.
- **`gravity_integrate_raw` registered** in `vanilla_symbols.py`
  as `fastcall(8) → 0x00085700` (mangled
  `@gravity_integrate_raw@8`).  The "fastcall 2-reg" signature
  is a deliberate lie to clang so the REL32 lands; the EAX/ESI
  setup happens only in the wrapper's asm.
- **New header** `shims/include/azurik_gravity.h` with the clean
  wrapper prototype + a clearly-marked internal declaration of
  the raw vanilla symbol for drift-guard purposes.
- **Drift guard generalised**: `tests/test_vanilla_thunks.py`
  now accepts declarations in `azurik_vanilla.h` OR companion
  shim headers (listed in `_COMPANION_HEADERS`).
- **13 new tests** in `test_gravity_wrapper.py` covering the
  registry entry, wrapper compilation + byte shape + single-REL32
  invariant, end-to-end layout_coff → REL32 resolves to the
  correct vanilla VA, and header-doc-warning presence.

#### Save-file format — initial scaffold

New top-level Python module `azurik_mod.save_format` + CLI
subcommand `azurik-mod save inspect` for introspecting Azurik
save slots exported from xemu's HDD image.

- **Xbox-standard container files fully decoded**:
  - `SaveMetaXbx` / `TitleMetaXbx` — UTF-16-LE key/value parser
    with lossless byte-identical round-trip, field get/set,
    Unicode support, binary-tail preservation.
  - `SaveImage.xbx` / `TitleImage.xbx` — opaque bytes (image
    swizzle decoding deferred).
- **Azurik `.sav` scaffold**:
  - `SaveHeader` — 20-byte fixed prologue
    (`magic / version / payload_len / checksum / reserved`)
    with round-trip + `magic_as_ascii()` convenience.
  - `AzurikSaveFile` base + `SignatureSav` / `LevelSav`
    subclasses for profile-level / per-level saves.
    Current decoder emits a single opaque `SaveChunk`; the
    `iter_chunks()` extension point is where future field-level
    decoders plug in.
  - Path-based dispatch: `AzurikSaveFile.from_bytes(..., path=...)`
    returns the right subclass based on filename.
- **`SaveDirectory`** recognises every file type in a save slot
  (SaveMeta / TitleMeta / SaveImage / TitleImage / `.sav` files)
  and keeps unknowns in `extra_files`.  JSON-serialisable
  `summary()` for tooling.
- **CLI**: `azurik-mod save inspect <path>` with `--json` flag.
  Handles both directory and single-file inspection.  Lazy-imports
  the module so normal patch workflows don't pay its cost.
- **28 new tests** in `test_save_format.py` pinning parser
  correctness, round-trips, dispatch rules, JSON summaries,
  partial-export handling, and CLI smoke tests.
- **New docs** [`docs/SAVE_FORMAT.md`](docs/SAVE_FORMAT.md):
  directory layout, qcow2 / xemu extraction workflow, byte-level
  details for the decoded portions, limitations, and a priority
  list of decoder targets for future work.

Source-level evidence for the save format: call sites
`FUN_0005b250` (fopen wrapper), `FUN_0005c4b0` (directory scan),
`FUN_0005c95c` (`fread(buf, 0x14, 1, fp)` — pinned the header
size), and the leaked source path `C:\Elemental\src\game\save.cpp`
at VA 0x19E5C8.

Full impact:
- 254 tests passing (up from 213; +13 gravity wrapper + 28 save
  format).
- 4 new documentation files in docs/ (D1_EXTEND.md already in;
  SAVE_FORMAT.md, plus the existing D2_NXDK.md and gravity notes).
- 3 new shim-authoring-surface files (azurik_gravity.h,
  azurik_kernel_extend.h, gravity_integrate.c).

### D1-extend — runtime xboxkrnl export resolver + comprehensive coverage pass

Shims can now call **any** xboxkrnl export, not just the 151 Azurik's
vanilla XBE statically imports.  Full design note:
[`docs/D1_EXTEND.md`](docs/D1_EXTEND.md).  D2 (full NXDK integration)
is documented separately in [`docs/D2_NXDK.md`](docs/D2_NXDK.md) and
intentionally deferred.

- **Runtime resolver shim** (`shims/shared/xboxkrnl_resolver.c`).
  Single self-contained function `xboxkrnl_resolve_by_ordinal(n)`
  that walks xboxkrnl.exe's PE export table from the fixed retail
  base `0x80010000`.  ~50 bytes of i386 code; zero undefined
  externs; auto-placed by `ShimLayoutSession` the first time any
  extended import is referenced.

- **Per-import resolving stubs** (33 bytes each).  On first call:
  `CALL xboxkrnl_resolve_by_ordinal(ordinal); cache inline; JMP EAX`.
  On subsequent calls: 3 instructions (load cache + test + indirect
  jump).  Dispatch lives in `shim_session.stub_for_kernel_symbol`
  which auto-routes between D1 static-thunk (fast path, 6 bytes)
  and D1-extend resolver (slow-first-call path, 33 bytes) based on
  whether the ordinal is in Azurik's 151.

- **Expanded ordinal catalogue** (`xboxkrnl_ordinals.py`).  Split
  into two tables: `AZURIK_KERNEL_ORDINALS` (151, unchanged) +
  new `EXTENDED_KERNEL_ORDINALS` (~100 curated entries covering
  Debug, Executive, I/O, Kernel services, Memory Manager, Object
  Manager, Process, Runtime, Crypto, and Xbox-specific APIs).
  `ALL_KERNEL_ORDINALS` gives the union; `NAME_TO_ORDINAL`
  prefers Azurik's static slot when a name appears in both
  (so D1's fast path always wins over D1-extend when possible).
  New public helper: `is_azurik_imported(ordinal)`.

- **New header** `shims/include/azurik_kernel_extend.h`.  Declares
  ~60 of the most useful extended imports with correct `NTAPI` /
  `FASTCALL` annotations: DbgBreakPoint, DbgPrompt, the Ex*/Ke*
  / Io* / Mm* / Ob* / Ps* / Rtl* surface areas not in Azurik's
  static imports, plus `snprintf` / `sprintf` / `XboxKrnlVersion`.
  Shim authors just `#include` and call.

- **New VA anchors** in `shims/include/azurik.h` for commonly-
  read globals: `AZURIK_FLOAT_ZERO_VA` / `AZURIK_FLOAT_HALF_VA` /
  `AZURIK_FLOAT_ONE_VA` (shared numerical constants at
  `0x001A2508` / `0x001A9C84` / `0x001A9C88`);
  `AZURIK_ENTITY_REGISTRY_BEGIN_VA` / `_END_VA` / `_CAP_VA`
  (runtime entity-pointer vector at `0x0038C1E4..EC`);
  `AZURIK_MOVIE_CONTEXT_PTR_VA` / `AZURIK_MOVIE_IDLE_FLAG_VA`
  (boot movie state at `0x001BCDC8` / `0x001BCDB4`);
  `AZURIK_WALKING_STATE_FLAG_VA` (`0x0037ADEC`).  Real on-disk
  bytes + BSS placement pinned via new regression tests.

- **New vanilla function** `boot_state_tick` (`FUN_0005F620`)
  registered as `__stdcall(float)` with verified `RET 4` exits
  and AL-return convention.  Declared in `azurik_vanilla.h`.
  Lets shims wrap the boot-state machine (extension path for
  future `qol_skip_prophecy`-style patches).

- **Tests**: 213 passing (+ 20 new in `test_d1_extend.py`).
  Pinned: ordinal-catalogue invariants, static-vs-extended
  dispatch, stub byte-shape + opcode offsets + rel32 overflow,
  resolver `.c` compiles + has zero undefined externs, end-to-end
  session dispatch against the real vanilla XBE, and a drift
  guard between `azurik_kernel_extend.h` and
  `EXTENDED_KERNEL_ORDINALS`.

- **New docs**: [`docs/D1_EXTEND.md`](docs/D1_EXTEND.md) (full
  design + authoring workflow for extended imports),
  [`docs/D2_NXDK.md`](docs/D2_NXDK.md) (deferred — NXDK
  integration plan + deferral rationale).

### Small headers fill-in pass — ControllerState, drop tables, entity_lookup

- **`ControllerState` struct** added to `shims/include/azurik.h`.
  84-byte layout (`AZURIK_CONTROLLER_STRIDE = 0x54`), per-player
  at `AZURIK_CONTROLLER_STATE_VA + player_idx * 0x54`.  Covers
  analog sticks, D-pad, 8 analog buttons, triggers, stick clicks,
  start / back, plus the 12-byte `edge_state[]` latch array.
  Pinned from Ghidra's `FUN_000a2880` (XInput poll) — every write
  maps 1:1 to a named field.  Active-player index anchor
  `AZURIK_ACTIVE_PLAYER_INDEX_VA = 0x001A7AE4`.  Compile-time
  `_Static_assert`s pin `sizeof(ControllerState) == 0x54` and every
  critical offset.

- **CritterData drop-table + range fields** pulled into
  `shims/include/azurik.h`: `range`, `range_up`, `range_down`,
  `attack_range`, `drop_1..5`, `drop_count_1..5`, `drop_chance_1..5`
  at offsets `0xB8..0x10C`.  Offsets verified against
  `FUN_00049480`'s `"dropN"` / `"dropChanceN"` / `"rangeN"` writes.

- **`entity_lookup` (`FUN_0004B510`)** registered in
  `vanilla_symbols.py` + declared in `azurik_vanilla.h`.  Verified
  `__fastcall` (`@entity_lookup@8`) by reading two real callers —
  both emit `MOV ECX,<name>; MOV EDX,<fallback>; CALL` with no
  `ADD ESP, N` cleanup.  Lets shims resolve named entities at
  runtime without going through a config-table wrapper.

- **Skipped** `FUN_00085700` (gravity integration) — Ghidra decomps
  it as `__fastcall` but the body reads `in_EAX` as an implicit
  output-pointer (MSVC RVO pattern), so clean clang exposure
  requires a naked-asm wrapper.  Reasoning documented in
  `docs/LEARNINGS.md::Vanilla-function exposure` for future
  reference.

- **Tests**: 193 passing + 57 subtests (up from 192 + 32).  New
  drift-guards in `tests/test_shim_authoring.py` pin 10 CritterData
  drop-table offsets and 15 ControllerState offsets as compile-
  observable facts.

- **Docs**: LEARNINGS.md gains a "ControllerState struct" section
  with the full byte-level map + a "Vanilla-function exposure" note
  covering the fastcall-vs-thiscall-vs-RVO ABI edge cases.  SHIMS.md
  roadmap updated: mid-term #3 and #4 marked done; stale "Long-term"
  section cleaned up (D1 and E were duplicated there despite being
  done; replaced with D1-extend + D2 + B2 future-work entries).

### Folder-per-feature reorganisation + unified `apply_pack` dispatcher

- **Every feature is now one self-contained folder** under
  `azurik_mod/patches/<name>/` — Python declaration in `__init__.py`,
  optional shim C source alongside as `shim.c`, optional `README.md`
  for per-feature notes.  Deleting a feature = removing one folder;
  no orphaned references scattered across `shims/src/` and
  `azurik_mod/patches/`.  The six pre-existing packs migrated:
  `fps_unlock/`, `player_physics/`, `qol_gem_popups/`,
  `qol_other_popups/`, `qol_pickup_anims/`, `qol_skip_logo/`.
- **`shims/` is now a shared library, not a feature bucket.**
  `shims/src/` → `shims/fixtures/` (only test-only shim sources
  remain — `_reloc_test.c`, `_vanilla_call_test.c`, `_shared_lib_test.c`,
  `_shared_consumer_{a,b}.c`, `_kernel_call_test.c`).  Feature shims
  (currently `skip_logo.c`) moved into their feature folders.
- **`Feature` descriptor + `ShimSource` helper** (new
  `azurik_mod/patching/feature.py` + extended `registry.py`).  Three
  new optional fields on `PatchPack` / `Feature`:
  - `shim: ShimSource` — no hardcoded `Path("shims/build/...")`.
  - `legacy_sites: tuple[PatchSpec, ...]` — byte-patch fallback.
  - `custom_apply: Callable` — multi-step apply escape hatch.
- **Unified `apply_pack(pack, xbe_data, params)` dispatcher**
  (`azurik_mod/patching/apply.py`).  Dispatches by site type;
  `params` values feed parametric sliders; `custom_apply` short-
  circuits the generic loop; `AZURIK_NO_SHIMS=1` swaps every
  `TrampolinePatch` for the pack's `legacy_sites`.  One env var now
  replaces the per-pack sprawl (`AZURIK_SKIP_LOGO_LEGACY=1` still
  works, kept as an alias).
- **`cmd_randomize_full` walks the registry.**  Replaced the
  handwritten `if want_gem_popups: apply_gem_popups_patch(...)` /
  `if want_skip_logo: ...` / … pipeline with a single loop that
  calls `apply_pack` on every enabled feature.  Pack-specific
  apply-function names stay exported for backward compat; the
  randomizer uses the dispatcher.
- **GUI backend simplified.**  `gui/backend.run_randomizer` now
  accepts unified `packs: dict[str, bool]` + `pack_params` dicts
  instead of per-pack boolean kwargs.  Legacy kwargs still accepted
  and folded into `packs` before dispatch.  `gui/pages/build.py`
  passes the dicts directly.
- **`shims/toolchain/new_shim.sh`** scaffolds a full feature folder
  (`__init__.py` + `shim.c`) instead of just writing a C file.
- **Auto-compile heuristic** updated for the new layout:
  `shims/build/<name>.o` looks for the source at
  `azurik_mod/patches/<name>/shim.c` first, then
  `shims/fixtures/<name>.c` for test fixtures.  `.o` filenames are
  now keyed on the pack name (not the source stem) so two features
  whose source both happens to be called `shim.c` can't collide in
  the shared build cache.
- **Tests (+9)** — `tests/test_apply_pack.py` pins every dispatch
  route: pure `PatchSpec`, parametric (including default fallback
  and virtual-site skip), `TrampolinePatch`, `custom_apply`,
  `AZURIK_NO_SHIMS=1` fallback, type validation.  Existing tests
  updated to the new paths; full suite at 191 passing.
- **Docs refreshed** — `SHIMS.md` directory map,
  `SHIM_AUTHORING.md` scaffold step + authoring flow,
  `AGENT_GUIDE.md` repo-shape + "folder-per-feature invariant"
  landmine, `PATCHES.md` pack catalog table, `LEARNINGS.md`
  "Historical: pre-reorganisation layout" lookup table,
  `shims/README.md` rewritten as library overview,
  `docs/ONBOARDING.md` written for newcomers.

### C-shim modding platform (polish — full header coverage + auto-compile + docs)

- **`shims/include/azurik_kernel.h` now covers ALL 151 xboxkrnl imports** Azurik's vanilla
  XBE references.  Previously only ~10 hand-picked functions were declared; the expanded
  header groups every import by subsystem (Av / Dbg / Ex / Fsc / Hal / Io / Ke / Kf /
  Mm / Nt / Ob / Ps / Rtl / Xbox / Xc) and ships with a full set of kernel typedefs
  (`NTSTATUS`, `HANDLE`, `PVOID`, `LARGE_INTEGER*`, the object-type aliases, the
  `PK*_ROUTINE` callback types).  Drift guard updated to skip C keywords and
  function-pointer typedef scaffolding.
- **`scripts/gen_kernel_hdr.py`** regenerates the header from OpenXDK's `xboxkrnl.h`
  (at `xbox-includes/include/xboxkrnl.h`) zipped against
  `azurik_mod/patching/xboxkrnl_ordinals.py`.  131 of 151 signatures come from OpenXDK
  directly; the remaining 20 (data exports, fastcall exceptions, varargs) are hand-
  written at the top of the generator and documented there.
- **Auto-compile** — `apply_trampoline_patch` now invokes `shims/toolchain/compile.sh`
  on demand when a shim's `.o` is missing but its `.c` source exists.  Heuristic:
  `shims/build/<name>.o` ↔ `shims/src/<name>.c`.  Opt out with
  `AZURIK_SHIM_NO_AUTOCOMPILE=1` (used in CI to pin pre-built artifacts).
- **Documentation pass** (three new files in `docs/`):
  * `docs/SHIM_AUTHORING.md` — end-to-end authoring guide (decision tree, 8-step
    workflow, common pitfalls, debug playbook).
  * `docs/AGENT_GUIDE.md` — AI-agent-specific guide with standard workflows, observed
    failure modes, and "before you make any change" checklist.
  * `docs/LEARNINGS.md` — accumulated reverse-engineering findings (the 151-import
    ceiling, `config.xbr` dead-data pattern, boot-state machine contract, the
    UnboundLocalError regression, etc.).  Cited from Ghidra function names so future
    agents can re-verify.
- **`azurik.h`** picked up a small "Time / frame pacing" section pointing at the 1/30 s
  constant and cross-referencing `azurik_kernel.h`.
- **Cross-refs** — every header now points at its companions; `docs/SHIMS.md` status
  table updated to mark the coverage work done.

### C-shim modding platform (Phase 2 D1 — xboxkrnl kernel imports)

- **Shims can now call xboxkrnl kernel functions directly.**  Any of
  the 151 kernel ordinals Azurik's vanilla XBE already imports
  (`DbgPrint`, `KeQueryPerformanceCounter`, `NtReadFile`,
  `HalReturnToFirmware`, ...) can be declared as a C extern in
  `shims/include/azurik_kernel.h` and called from a shim exactly
  like a local function.  The shim layout session parses the XBE's
  kernel thunk table, generates a 6-byte `FF 25 <thunk_va>` stub
  per referenced import, and resolves the shim's `call _Foo@N`
  REL32 to the stub's VA.  No XBE import-table surgery; no runtime
  loader; no name-resolution code injected into the game.
- **`azurik_mod/patching/xboxkrnl_ordinals.py`** — full ordinal →
  name table for the 151 imports Azurik ships with, cross-checked
  against Ghidra's import pane and the parsed thunk table on disk.
  Bijective (no duplicates); sorted by ordinal for binary-search
  audits.
- **`azurik_mod/patching/kernel_imports.py`** — XBE thunk-table
  decryption (retail / debug / chihiro XOR keys tried in turn),
  parser that walks the table to its null terminator and yields
  `(thunk_va, ordinal, name)` entries, `demangle_stdcall` /
  `demangle_cdecl` helpers, and a `stub_bytes_for(va)` generator.
- **`shims/include/azurik_kernel.h`** — extern declarations for the
  imports we've so far needed in shims: debug (`DbgPrint`), timing
  (`KeQueryPerformanceCounter` / `Frequency`, `KeStallExecution-
  Processor`, `KeTickCount`), synchronisation (`KeSetEvent`,
  `KeWaitForSingleObject`), and title management
  (`HalReturnToFirmware`).  The header carries an ABI checklist
  shim authors must follow and a "what cannot be called" note for
  kernel functions Azurik doesn't already import.
- **Tests (+33 tests)** — `tests/test_kernel_imports.py` covers:
  ordinal-table invariants (count, uniqueness, sorting), demangle
  helpers, stub-byte shape, thunk-table parse against the vanilla
  XBE (VA `0x18F3A0`; 151 entries; every parsed ordinal resolves
  to a known name), `ShimLayoutSession` stub caching (allocator
  called exactly once per kernel function, dedup across shims), an
  end-to-end compile that has a shim call `DbgPrint` and asserts
  the REL32 lands on a stub whose indirect target is the correct
  thunk slot, and a header ↔ ordinal-map drift guard.
- **Not yet supported**: adding a NEW kernel import (one Azurik
  doesn't already reference).  The thunk table has zero trailing
  slack in Azurik's XBE, so extending it would require a move +
  re-link of every existing `CALL [thunk_va]` in the game.
  Tracked as `D1-extend` in `docs/SHIMS.md`.

### C-shim modding platform (Phase 2 E — shared-library shim layout)

- **`ShimLayoutSession.apply_shared_library(path)`** places a shim
  `.o` once per session and exposes its exported symbols to every
  subsequent `apply_trampoline_patch` call.  Two trampolines that
  both reference `_shared_helper@4` now resolve to a SINGLE VA —
  no duplicated machine code, no linker required.
- **`azurik_mod/patching/shim_session.py`** — new module that
  unifies D1 (kernel stubs) and E (shared libraries) under a single
  session object attached to the XBE bytearray.  The extern
  resolver threaded into `layout_coff` consults, in order: vanilla-
  symbol registry → shared-library exports → kernel-import stubs
  (auto-allocated) → session's fallback.  Stubs and library
  placements are cached for idempotence.
- **`azurik_mod/patching/coff.layout_coff`** gains an
  `extern_resolver: Callable[[str], int | None]` parameter.
  Unresolved externals that aren't in `vanilla_symbols` are passed
  to the resolver; `None` means "not mine, keep going".  The old
  `vanilla_symbols` dict-only API still works — `extern_resolver`
  is additive and defaults to `None`.  `layout_coff` also accepts
  `entry_symbol=None` for library-style placements (no single
  entry point to resolve).
- **`apply_trampoline_patch`** now instantiates / reuses a
  `ShimLayoutSession` attached to `xbe_data` automatically — pack
  apply functions can pre-place shared libraries via
  `get_or_create_session` without plumbing a new argument through.
- **Fixtures** — three new files under `shims/src/`:
  `_shared_lib_test.c` exports two stdcall helpers;
  `_shared_consumer_a.c` and `_shared_consumer_b.c` each call the
  first helper.  Used by the test below.
- **Tests (+6 tests)** — `tests/test_shared_library.py` covers:
  a shared library places its two exports with unique VAs, re-
  applying the same path is idempotent, export VAs lie inside the
  placed region, the "no externally-visible" error fires on
  static-only / DCE'd sources, and — the headline assertion — two
  independent consumer shims' REL32s resolve to the same helper VA.

### C-shim modding platform (Tier B — authoring ergonomics)

- **`shims/include/azurik.h` grew real struct definitions.**  Shim
  authors now get named fields for two key engine structs:
  * `CritterData` — what `FUN_00049480` populates for every critter
    (walk/run speed, collision radius, flocking fields, hitpoints,
    drown/corpse timers, ...).  Field offsets documented with their
    Ghidra piVar9 indices.
  * `PlayerInputState` — the per-frame player-movement struct used
    by `FUN_00084f90` / `FUN_00084940` / `FUN_00085f50`.  Key
    fields (magnitude at +0x124, direction vector at +0x128, flags
    at +0x20) are now named with Ghidra-verified offsets.
  Flag constants (`PLAYER_FLAG_RUNNING = 0x40`,
  `PLAYER_FLAG_FALLING = 0x01`) and fixed-width integer aliases
  (`u8`, `u16`, `u32`, `i8`..`i32`, `f32`, `f64`) live alongside.
  Compile-time `_Static_assert`s pin the minimum struct size so
  drift fails at compile time rather than producing silently-wrong
  machine code.
- **`shims/toolchain/new_shim.sh NAME`** — new scaffolding script.
  Generates a pre-filled `shims/src/<name>.c` with the correct
  `__stdcall` annotation, the two standard includes, and a TODO
  comment pointing at the function body.  Rejects names that
  aren't valid lowercase C identifiers; refuses to overwrite
  existing shims.  Next-step checklist printed on success.
- **Tests (+6 test classes, +20 subtests)** —
  `tests/test_shim_authoring.py` pins both pieces: a probe shim
  verifies every named field in the header compiles to the
  Ghidra-documented `[reg + 0xNN]` offset; the scaffold script is
  exercised with valid / invalid / duplicate names; the generated
  stub is compiled end-to-end and the exported
  `_c_<name>@0` stdcall symbol is sanity-checked.
- **Docs** — `docs/SHIMS.md` "Authoring a new shim" walkthrough
  now starts at `new_shim.sh` and references the named struct
  fields in `azurik.h`.  Directory map updated.
- **Deferred**: adding `FUN_000d1420` / `FUN_000d1520` (config-table
  lookups) as exposed vanilla functions.  Both use MSVC `__thiscall`
  (first arg in ECX, rest on stack) — clang supports the attribute,
  but it complicates the ergonomics enough that it belongs in a
  follow-up once we have a concrete shim that needs table queries.

### C-shim modding platform (Phase 2 C1 — player-speed shim, first real deliverable)

- **Walk-speed and run-speed sliders are back** on the Patches page.
  The earlier attempt wrote to `config.xbr`'s `attacks_transitions`
  cells, which Ghidra later showed were dead data at runtime.  C1
  replaces that with a direct `default.xbe` patch at the real
  per-frame player-movement call site (`FUN_00085f50`):
  * VA `0x85F62` (`MOV EAX,[EBP+0x34]; FLD [EAX+0x40]`, 6 B) rewritten
    to `FLD [<injected walk-speed VA>]` — the base speed loaded each
    frame now comes from a per-game float instead of the dead
    `entity->runSpeed` field.
  * VA `0x849E4` (`FMUL [0x001A25BC]`, 6 B) rewritten to
    `FMUL [<injected run-multiplier VA>]` — the 3.0 constant at
    `0x001A25BC` has **45** other read sites (collision, AI, audio,
    etc.), so the patch injects a per-player copy rather than
    mutating the shared one.
  * Both floats land via the Phase 2 A1 shim-landing infrastructure
    (`.text` trailing-padding gap preferred, `SHIMS` appended section
    fallback).  Defaults `walk_scale = run_scale = 1.0` are
    byte-identical to vanilla.
- **`PatchPack.dynamic_whitelist_from_xbe`** — new optional
  callback on `PatchPack` that computes extra whitelist ranges from
  the patched XBE bytes at verify time.  Powers
  `verify-patches --strict` for packs whose apply function emits
  patches at apply-time-chosen addresses (the injected float VAs).
  `cmd_verify_patches` invokes the callback and merges its ranges
  into the whitelist diff alongside the static contributions.
- **`apply_player_speed(xbe_data, walk_scale, run_scale)`** now
  operates on the XBE directly (was config.xbr).  The CLI flags
  `--walk-speed` / `--run-speed` on `apply-physics` and
  `--player-walk-scale` / `--player-run-scale` on `randomize-full`
  route through the new path.
- **Tests (+11)** — `tests/test_player_speed.py` is rewritten end
  to end: vanilla-site invariants, apply shape on a real XBE,
  defaults-are-no-op, reapply rejection, gravity/speed cross-
  independence, and the dynamic whitelist callback behaviour on
  both vanilla and patched XBEs.  Full suite: 129 passing.
- **Docs** — `docs/PATCHES.md` `player_physics` section fully
  rewritten with the Ghidra walkthrough, instruction layouts, and
  slider semantics.

### C-shim modding platform (Phase 2 A3 — vanilla-function calls)

- **Shims can now call any registered vanilla Azurik function.**
  Phase 1 shims had to be fully self-contained; Phase 2 A3 lets a
  shim do e.g. `play_movie_fn(name, 0)` and have the resulting
  `CALL rel32` land directly at Azurik's real VA.  No runtime
  thunks — just a name → VA registry consulted by `layout_coff`
  when it encounters undefined-external COFF symbols.
- **`azurik_mod/patching/vanilla_symbols.py`** — new `VanillaSymbol`
  dataclass + registry of exposed Azurik functions.  Each entry
  declares its C name, VA, calling convention (cdecl / stdcall /
  fastcall), and argument-byte count; the mangled COFF name is
  computed from those.  Seeded with `play_movie_fn@8` (0x18980)
  and `poll_movie@4` (0x18D30).
- **`shims/include/azurik_vanilla.h`** — matching C prototypes for
  shim authors.  `#include "azurik_vanilla.h"` and call any
  declared function as you would in a normal C program; the layout
  pass handles the VA resolution.
- **`layout_coff(..., vanilla_symbols=...)`** — new optional
  parameter.  `_resolve_symbol_va` consults the dict when a symbol's
  `section_number <= 0` (undefined external); truly unresolved
  symbols still raise with an actionable error pointing shim authors
  at the registry + header.
- **Tests (+12)** — `tests/test_vanilla_thunks.py` covers mangling
  rules (cdecl / stdcall / fastcall), registry accessors, synthetic
  COFF resolution, a real compiled shim (`shims/src/_vanilla_call_test.c`)
  that calls `play_movie_fn` and has its REL32 verified to land at
  0x18980, and a drift guard that refuses to let the Python
  registry and the C header disagree.
- **Docs** — `docs/SHIMS.md` "Calling a vanilla function from a
  shim" walkthrough added.

### C-shim modding platform (Phase 2 A1+A2 — headroom + relocations)

- **Unbounded shim sizes** — `append_xbe_section` now implements
  real XBE surgery: grows the section-header array in place (shifts
  every post-array byte, rewrites the 7 image-header pointer fields
  and 3 per-section pointers whose targets moved), places section
  data at EOF with `FILE_ALIGN` / `VA_ALIGN` alignment, bumps
  `num_sections`, `size_of_headers`, and `size_of_image`.  Phase 1's
  16-byte `.text` VA-gap ceiling is gone — shims of any practical
  size spill into a per-apply `SHIMS` section instead.
- **Automatic landing strategy** — `apply._carve_shim_landing`
  picks the least-invasive home for each shim: existing `.text`
  slack first, then `.text` growth into the adjacent VA gap, then
  a newly-appended `SHIMS` section.  Subsequent applies extend the
  same `SHIMS` section in place rather than spawning new ones.
- **Relocation-aware COFF loader** — `coff.layout_coff` parses
  per-section relocation tables and applies `IMAGE_REL_I386_DIR32`
  and `IMAGE_REL_I386_REL32` fixups after section placement, using
  the resolved XBE VAs for each symbol's owning section.  Metadata
  sections (`.debug$S`, `.llvm_addrsig`, `.drectve`, `.xdata`,
  `.pdata`) are filtered out so they don't consume SHIMS space or
  force bogus relocations.  Supports arbitrary shim section layouts
  (`.text` + `.rdata` + `.data` + `.bss`), with cross-section
  references resolved correctly.
- **Auxiliary-record preservation** — the COFF symbol-table walker
  now keeps aux records as placeholder entries so relocation
  `symbol_index` values stay aligned with the raw on-disk table.
- **Section-name long-form support** — `/NN` encoding used by clang
  for section names >= 8 chars is now resolved (previously left as
  a literal `"/29"`-style placeholder).
- **Trampoline apply pipeline** picks the right loader path
  automatically: zero-relocation shims stay on the minimal
  `extract_shim_bytes` fast path; anything with relocations goes
  through `layout_coff` + in-place overwrite of placeholder bytes.
- **Tests (+19)** — `tests/test_append_xbe_section.py` (11) covers
  the header-shift round-trip, pointer-fixup regression guards, and
  the `_carve_shim_landing` fallback.  `tests/test_coff_relocations.py`
  (8) compiles a real reloc-bearing shim (`shims/src/_reloc_test.c`)
  on demand and verifies every DIR32 / REL32 field is written with
  the resolved VA.  113 total tests passing.

### Patches

- `player_physics` walk/run speed sliders removed from the Patches
  page.  Investigation (Ghidra on FUN_00049480 / FUN_0007e7c0) showed
  the `walkSpeed` / `runSpeed` cells in `config.xbr`'s
  `attacks_transitions` section are dead data: the engine's only
  `walkSpeed` string xref is a lookup against `critters_critter_data`
  (which doesn't carry that row), so the default 1.0 is always used
  regardless of the cell value.  The `apply_player_speed` helper and
  `--player-walk-scale` / `--player-run-scale` CLI flags stay in the
  tree for a future Phase 2 fix once the real storage location is
  found; the sliders no longer register on the GUI pack so users
  don't think the feature works.
- `player_physics` gravity slider: widened range to 0.0..100.0 m/s²
  (previously 0.98..29.4) so you can go from weightless floating
  through ~10x Earth.  The slider widget's numeric entry field
  accepts any exact value inside that range, giving finer precision
  than the step size alone.

### GUI

- Single build entry point: removed the "Build randomized ISO"
  button from the Randomize page.  The page now mirrors its widget
  state into `AppState.randomize_config` on every change, and the
  "Start build" button on Build & Logs reads that snapshot directly.
  One place to click, no double-click required.
- Pack descriptions tightened across every pack (fps_unlock, qol_*,
  player_physics) to 1–2 short sentences for faster scanning.
- `ParametricSlider` widget now shows the current value alongside
  the default in its header, prints the slider's min/max range on
  the right, and widens the exact-value entry to 12 chars.

### C-shim modding platform (Phase 1)

- New `TrampolinePatch` site descriptor in `azurik_mod.patching.spec`
  joins `PatchSpec` / `ParametricPatch`.  Instead of declaring raw
  byte swaps, a trampoline patch names a C function whose compiled
  PE-COFF `.o` gets injected into the XBE; a 5-byte `CALL` / `JMP`
  rel32 at the declared VA diverts control flow into the shim.
- New `shims/` tree: C sources (`src/`), shared freestanding headers
  (`include/azurik.h`), and an Apple-clang wrapper
  (`toolchain/compile.sh` emitting i386 PE-COFF via
  `-target i386-pc-win32`).
- New `azurik_mod.patching.coff` — minimal PE-COFF reader (sections
  + symbols only, no relocations) — feeds shim bytes + entry-point
  offsets into the apply pipeline.
- `find_text_padding()` generalised: reports both in-section trailing
  zero slack AND the adjacent VA-gap growth window.  `grow_text_section()`
  commits the matching `virtual_size` / `raw_size` bump in the XBE
  section header so the Xbox loader maps injected bytes as executable.
- `apply_trampoline_patch()` / `verify_trampoline_patch()` do the
  end-to-end work (COFF parse, landing carve, section grow, rel32
  emit, NOP fill) and stay idempotent on a second apply.
- `qol_skip_logo` now replaces only the 5-byte `CALL play_movie_fn`
  at VA 0x05F6E5 with a C shim that returns `AL=0` and does `RET 8`,
  matching `play_movie_fn`'s `__stdcall` contract.  The preceding
  `PUSH EBP; PUSH 0x0019E150` instructions run as normal so the shim
  sees both args on its stack.  This replaces the earlier 10-byte
  NOP attempt, which left `AL` undefined and leaked 4 bytes of stack
  per iteration — the state machine at `FUN_0005F620` would drift
  into `case 2` (poll a movie that never started) and hang on a
  black screen at boot.  The legacy `SKIP_LOGO_SPEC` escape hatch
  (`AZURIK_SKIP_LOGO_LEGACY=1`) was simultaneously fixed to write
  `ADD ESP, 4; XOR AL, AL; NOP×5` with the same semantics.
- `verify-patches --strict` now absorbs trampoline sites, their
  shim landing pads, and the grown `.text` section-header fields
  into the whitelist diff so a legitimately-patched XBE reports
  clean.
- New docs: [docs/SHIMS.md](docs/SHIMS.md) (authoring workflow),
  `shims/README.md` (toolchain + directory map).  New tests:
  `tests/test_trampoline_patch.py` (18 tests — COFF, XBE surgery,
  apply+verify end-to-end) and an expanded
  `tests/test_qol_skip_logo.py`.

### GUI

- Rebranded launcher scripts from `Launch Randomizer.*` to
  `Launch Azurik Mod Tools.*`.  The macOS / Linux `.command` launcher
  now probes Homebrew (`/opt/homebrew/bin`, `/usr/local/bin`), pyenv
  shims, and the Python.org framework before giving up, sources the
  user's zsh / bash profile, and exec-searches
  `python3.12 … python3.10 → python3 → python`, fixing the
  "Python was not found" error on Finder double-clicks.
- Every page body is now wrapped in a `ScrollableFrame` (Canvas +
  scrollbar with `<Enter>/<Leave>`-scoped mouse-wheel bindings) so
  long pages (Patches, Randomize) remain reachable on short windows.
- The GUI auto-detects the first `.iso` in the repo's `iso/` folder
  at startup and preloads it into the Project page's ISO picker and
  auto-generated output path.
- All shuffle pool and QoL checkboxes in the Randomize page now
  default to OFF so an untouched build is a no-op.  Parametric
  sliders continue to default to their baseline values
  (gravity 9.8 m/s², walk / run 1.0×).

### Patches

- `qol_skip_logo` (new pack, default OFF): NOPs the 10-byte
  `PUSH &"AdreniumLogo.bik"; CALL play_movie` pair at VA 0x05F6E0 so
  the unskippable Adrenium logo movie no longer plays on boot.
  Noticeably shortens game launch.  The intro prophecy cutscene is
  left alone.  Surgical instruction-level patch, stack-balanced,
  passes `verify-patches --strict`.  Opt in via `--skip-logo` CLI
  flag or by ticking the pack on the Patches page.
- `qol` split: the single `qol` pack has been replaced by three
  independently-toggleable packs so users can pick exactly which QoL
  tweaks they want.  All default to OFF:
  * `qol_gem_popups` — hide the "Collect 100 &lt;gem&gt;" popup that
    appears the first time you collect each gem type.  (The old
    description said "You found X for the first time!" which was
    never the actual in-game wording.)
  * `qol_other_popups` — hide the remaining first-time popups: swim
    tutorial, first key pickup, first health pickup, first of each
    elemental / chromatic power-up, and the six-keys-collected
    milestone.  The death-screen "gameover" popup is deliberately
    left alone.
  * `qol_pickup_anims` — skip the post-pickup celebration animation.
  All three use the same "null the first byte of the localisation
  resource-key path" mechanism so the game's popup lookup silently
  fails; the popup text itself lives in a separate localisation
  `.xbr` file, not in `default.xbe`.
  CLI: former `--no-qol` / `--no-gem-popups` / `--no-pickup-anim` opt-out
  flags are deprecated (still accepted as no-ops) and replaced by
  opt-in `--gem-popups` / `--other-popups` / `--pickup-anims`.
- `fps_unlock`: raised the simulation step cap from 2 to 4.  At 60 Hz
  sim cap=2 causes game time to drift below real time whenever render
  FPS dips below 30; cap=4 preserves real-time game speed down to
  15 FPS rendered (matching vanilla's 2-step coverage at 30 Hz sim).
  Both `CMP ESI, 0x4` (TRUNC) and `PUSH 0x4` + two `FADD ST0,ST0`
  (CATCHUP) are pinned by `tests/test_fps_safety.py`.
- `player_physics` (new pack): slider-driven world gravity
  (`--gravity` / GUI slider) and player walk / run speed multipliers
  (`--player-walk-scale`, `--player-run-scale`).  Framework additions:
  `ParametricPatch`, `apply_parametric_patch`, `verify_parametric_patch`
  under `azurik_mod.patching`, plus a `ParametricSlider` GUI widget
  that picks up every `ParametricPatch` automatically.

## v0.3.0 (2026-04-17) — Repo reorganization + 60 FPS unlock

### Structure

- Flat `tools/randomizer/*.py` replaced by a pip-installable
  `azurik_mod` library package with sub-packages `patching/`,
  `patches/`, `iso/`, `randomizer/`, and `config/`.
- GUI moved out of `tools/randomizer/azurik_gui/` to a top-level
  `gui/` package with a `tabs/` subpackage; now calls the library
  in-process (no more subprocess).
- `pyproject.toml` defines `azurik-mod` and `azurik-gui` console
  entry points — install with `pip install -e .`.
- Vendored `xdvdfs.exe` removed; `azurik_mod.iso.xdvdfs` auto-downloads
  the right binary per OS into the user cache, or falls back to PATH
  (`cargo install xdvdfs-cli`).
- `claude_output/` renamed to `azurik_mod/config/`.
- Outer-workspace analysis scripts moved to `scripts/analysis/`; vanilla
  config dumps and example mods moved to `examples/`.
- Tests moved to top-level `tests/` and expanded: patch-loader
  round-trip + BSOD guard invariants (21 tests total).

### 60 FPS unlock (new patch pack)

- `azurik_mod.patches.fps_unlock` implements 50 PatchSpec sites: lifts
  the VBlank cap, halves 28 subsystem `1/30` timesteps, doubles the main
  `30.0` rate, uses FISTP truncation to avoid the 60 → 30 death spiral,
  and pins the simulation step cap at 2 (matching vanilla reentrancy).
- `safety_critical` guard on TRUNC + CATCHUP patches with a unit test
  (`tests/test_fps_safety.py`) that fails any regression to step cap 4.
- New `azurik-mod verify-patches` subcommand: applies / verifies every
  site, whitelist-diffs against an unpatched original, returns non-zero
  on mismatch — CI-safe.

### QoL unification

- Former `OBSIDIAN_ANIM` + `FIST_PUMP` pair replaced by a single
  `PICKUP_ANIM` PatchSpec (VA 0x0413EE) that preserves save persistence.
- CLI flag pair `--no-obsidian-anim` / `--no-fist-pump` collapsed into
  `--no-pickup-anim`.

---

## v0.2.0 (2026-03-15) — Major Update

### Critical Bug Fix
- **Config values are now 64-bit doubles** — Previous versions read/wrote 32-bit floats at the wrong offset, producing incorrect values and corrupting data on write. All config patching now uses correct 8-byte IEEE 754 doubles. Example corrections: initial_fuel was 2.5, actually 8.0; fuel_inc_gems was 3.25, actually 100.

### New Features

#### Randomizer
- **Level connection randomization** — Shuffles exits between levels within path-length groups. Clears start spots for safe spawning at level origin. Disabled by default (may cause unsolvable seeds).
- **Custom item pool** — Choose exactly how many of each power and fragment to include in the randomization pool via GUI spinboxes or `--item-pool` CLI flag.
- **Custom gem weights** — Set relative weights for diamond, emerald, sapphire, ruby, and obsidian gem distribution. Higher weight = more frequent.
- **Obsidian lock cost** — Customize the obsidian cost per temple lock (default 10, locks at 10/20/30.../100). GUI spinbox or `--obsidian-cost` CLI flag.
- **Obsidians included in gem shuffle** — Previously excluded, obsidians now randomize with other gems. Total shuffled: 97 gems across 20 levels.
- **Force build on unsolvable seeds** — When the solver can't find a completable placement, the GUI offers "Build Anyway" instead of just failing.
- **Player character swap** — Experimental: replace the player model with any character (e.g., `--player-character evil_noreht`). Animations may not match. Max 11 characters.

#### Entity Editor (New Tab)
- **8 editable sections** with 8,466+ patchable values:
  - Entity Stats — walkSpeed, runSpeed, attackRange, HP, knockback per entity (108 entities)
  - Entity Damage Multipliers — 62 damage type vulnerability multipliers per entity (107 entities)
  - Damage Types — Base damage, cost, delay, freeze for all 57 attack types (player + enemy)
  - Player Global Settings — Flat list: initial HP (200), max HP (400), fuel per upgrade, gems needed (100), fall damage thresholds
  - Armor Properties — All 19 armor types × 3 tiers: protection, HP, cost, hits, time, flaps
  - Critters Movement & AI — Provoke distance, stalk, flee, turn rate per enemy
  - Critters Flocking — Boids parameters
  - Enemy Damage Overrides — Per-enemy damage values
- **Randomize stats** — Set min/max percentage range, randomize single entity or entire section
- **Load from ISO** — Read current default values from the game ISO for reference
- **Export Mod JSON** — Save edits as a mod file for manual application
- **Auto-integration** — Entity editor edits automatically included when building randomized ISO

#### QoL Patches
- **All pickup celebration animations disabled** — JMP at VA 0x413EE skips the linked-list cleanup and counter update that keep the celebration animation data live, while FUN_00061360 (collected flag) and FUN_0006FC90 (pickup counter) still run for save persistence
- **Two QoL patches now**: gem first-pickup popups (5), pickup celebration animations

#### GUI Improvements
- Warning labels on Keys, Barriers, and Connections checkboxes ("may cause unsolvable seeds")
- Config Editor tab shows "Work in Progress" banner
- Entity Editor scroll only when content overflows
- Window size increased to accommodate new controls

### Bug Fixes
- **Missing entity scanner fix** — Added all power and fragment names to DIRECT_SEARCH_NAMES fallback list. Fixed 3 entities (f4/frag_fire_2, w2/power_water, a5/power_air) that were missed by the standard 1.0f marker scanner, causing item duplication.
- **Config editor "Section '_meta' not found"** — Backend now correctly reads sections from `data["sections"]` instead of top-level keys.
- **xdvdfs Windows path fix** — Uses POSIX forward slashes for in-image paths, fixing "Entry does not exist" errors when GUI runs from native Windows (not Git Bash).
- **Town barrier item scaling** — Non-native items placed behind obsidian barriers are scaled to 0.5x to prevent protruding through force fields.

### Engine Research (for developers)
- 39 registered node types fully decoded with handler addresses
- Node graph connection format decoded (12-byte triplets in NDBG)
- Complete damage system: 56 damage types, vulnerability matrix, armor system
- AI pathfinding: no navmesh, direct-to-target + Boids flocking
- Level loading: full teardown/load pipeline, start spot resolution
- Save system: 3-tier persistence, randomizer confirmed save-safe
- Collection fourcc from critters_engine config table (name-driven)
- Cross-type spawning confirmed (enemies at item locations and vice versa)
- Ghidra structures header (azurik_structures.h) with 26 structs and 12 enums

## v0.1.0 (2026-03-13) — Initial Release

- Full-game randomizer with forward-fill logic solver
- Major items, keys, gems, barriers randomization
- Seed-based reproducibility
- GUI with category checkboxes
- QoL patches: gem popups, pickup animations
- CLI and GUI interfaces
