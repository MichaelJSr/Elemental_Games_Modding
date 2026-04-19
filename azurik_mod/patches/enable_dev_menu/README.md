# enable_dev_menu

Forces Azurik's built-in developer **level-select hub**
(`selector.xbr`) to load whenever the game tries to load a
level.

> **Category**: experimental.  This overrides **every**
> level-load path — including "Load Game" and cutscene-triggered
> level transitions — so you'll always land in selector.  Keep a
> backup of your save directory.

## How to activate it

### From the GUI

1. Launch `azurik-gui` and pick your vanilla `Azurik.iso`.
2. Open the **Patches** tab → **Experimental** sub-tab.
3. Tick **`enable_dev_menu`**.
4. Switch to **Build & Logs** → click **Start build**.
5. Boot the resulting `.iso` in xemu.

### From the CLI

```bash
azurik-mod patch \
    --iso 'Azurik.iso' \
    --mod '{"enable_dev_menu": true}' \
    -o 'Azurik_devmenu.iso'
```

Pair with `qol_skip_logo` to skip the Adrenium intro:

```bash
azurik-mod patch \
    --iso 'Azurik.iso' \
    --mod '{"enable_dev_menu": true, "qol_skip_logo": true}' \
    -o 'Azurik_dev.iso'
```

## What you'll see

After booting, pick **New Game** (or any other entry that
triggers level loading).  The game loads `levels/selector`
instead of the expected level — a small room containing:

- 22 level portals — one per live level (`a1`..`w4`, `town`,
  `life`, `training_room`, etc.)
- 10 cutscene portals — Prophecy, Training 1+2, Possessed,
  DisksDestroyed, Catalisks, Airship2, Death 1, DeathMeeting 2,
  DisksRestoredAll, NewDeath.
- A self-portal back to the selector itself.

Touch a plaque, load the target, play around, return via the
self-portal, repeat.

## How it works

`dev_menu_flag_check` (`FUN_00052F50`) ends with a three-stage
level-name validator.  Each stage calls `FUN_00054520` (a read-
only "does this level asset exist?" probe) against a different
candidate string:

| Stage | Candidate               | Behaviour                |
|-------|-------------------------|--------------------------|
| 1     | caller's `param_2`      | usually a live level     |
| 2     | `pcVar10` (local var)   | set by a prior vtable check |
| 3     | `"levels/selector"`     | **unconditional**        |

Whichever stage returns non-zero wins — its string gets pushed
as `param_2` to `FUN_00053750` (the universal level loader).
Stage 3 uses `"levels/selector"` from `.rdata` at
`VA 0x001A1E3C` and stage 3 always succeeds because selector.xbr
exists in every vanilla ISO.

We patch stages 1 and 2 to always fail:

Vanilla bytes (both stages):

```
E8 97 11 00 00   CALL FUN_00054520   ; stage 1 @ VA 0x53384
E8 58 11 00 00   CALL FUN_00054520   ; stage 2 @ VA 0x533C3
```

Patched bytes:

```
31 C0 90 90 90   XOR EAX, EAX ; NOP x3   ; forces AL = 0
```

With `AL = 0` at each `TEST AL, AL` that follows the CALL, the
`JZ` fires and flow cascades through stages 2 → 3.  Stage 3
succeeds, and `FUN_00053750` is called with
`"levels/selector"` as the level name.

## Why the previous patch (JZ NOPs) didn't work

Before April 2026 this feature patched two `JZ` instructions
inside the `else` branch of `dev_menu_flag_check`'s outer
vtable gate — specifically the code that sets `pcVar10`
based on a second vtable call.  The old patch forced
`pcVar10 = "levels/selector"`.

But `pcVar10` only matters in stage 2 of the validator chain,
which fires only when stage 1 fails.  **In real gameplay,
stage 1 almost always succeeds** because the caller
(`FUN_00052910`, `FUN_00055AB0`, or `FUN_00056620`) passes a
known-valid level string.  The JZ NOPs took effect, but their
effect was invisible.

Worse, the outer JZ NOP diverted flow past important save-
bootstrap work that fires when the vtable gate returns 0 —
potentially leaving the game in a half-initialised state on
new-save flows.

The current patch lands exactly where the observable
behaviour is controlled: at the level-name decision point
itself, not at a precursor branch.

## Verifying the patch applied

```bash
azurik-mod verify-patches \
    --xbe patched.xbe --original vanilla.xbe --strict
```

Expected diff: exactly **10 bytes** differ — 5 bytes at file
offset `va_to_file(0x00053384)..+4` and 5 bytes at file offset
`va_to_file(0x000533C3)..+4`.  Both runs are `31 C0 90 90 90`.

## Known caveats

- **`levels/earth/e4` plaque soft-locks.**  Selector references
  a cut level that isn't on the ISO (see `KNOWN_CUT_LEVELS` in
  `azurik_mod.assets`).  Touch it and the game hangs waiting
  for a level XBR that never loads.
- **Overrides "Load Game" too.**  Because every call into
  `FUN_00053750` via this function ends up at selector, you
  can't load a save into its original level with this patch
  active — you'll always land in the selector room.  Rebuild
  without the patch to restore normal load behaviour.
- **May corrupt save files.**  The vanilla "New Game"
  ceremony sets up per-character init state that the selector
  level doesn't touch.  Saves made in a dev-menu build may
  behave oddly if loaded with this patch OFF.  Keep a backup.
- **Intended for level tours / speedrun practice** — not
  regular playthroughs.

## Further reading

- **LEARNINGS.md § selector.xbr** — full decode of
  `selector.xbr`'s structure + the portal-plaque wiring.
- **LEARNINGS.md § "enable_dev_menu — three-stage validator
  chain"** — the post-mortem of why the old JZ NOP patch
  didn't work and how the new XOR-EAX approach forces stage 3.
- **PATCHES.md** — catalog entry with the byte-level pin.
