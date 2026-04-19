# enable_dev_menu

Forces Azurik's built-in developer cheat menu (`selector.xbr`)
to load at game start.  The menu contains direct portals to every
level in the game plus one-click triggers for every cutscene.

> **Category**: experimental.  Keep a backup of your save
> directory — the dev menu bypasses the vanilla "New Game"
> bootstrap and may leave new save files in odd states.

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

Pair with `qol_skip_logo` if you want to skip the Adrenium
intro each run:

```bash
azurik-mod patch \
    --iso 'Azurik.iso' \
    --mod '{"enable_dev_menu": true, "qol_skip_logo": true}' \
    -o 'Azurik_dev.iso'
```

## What you'll see

After intro logos (or instantly with `qol_skip_logo`), the
game's "Start New Game" flow drops you directly into
`levels/selector` — a small room where each plaque is a portal:

- 22 level portals — one per live level (`a1`..`w4`, `town`,
  `life`, `training_room`, etc.)
- 10 cutscene portals — Prophecy, Training 1+2, Possessed,
  DisksDestroyed, Catalisks, Airship2, Death 1, DeathMeeting 2,
  DisksRestoredAll, NewDeath.
- A self-portal back to the selector itself.

Touch a plaque, load the target, play around, reload the
selector via its self-portal, repeat.

## Verifying the patch applied

```bash
azurik-mod verify-patches \
    --xbe patched.xbe --original vanilla.xbe --strict
```

Expected diff: exactly **8 bytes** differ, all in `.text` at
file offsets `0x42F7E..0x42F83` and `0x42F95..0x42F96`, all
flipped to `0x90` (NOP).

## How it works

`FUN_00052F50` in Azurik's XBE (documented Python-side as
`dev_menu_flag_check`) contains two nested `JZ` branches that
together gate the selector path:

```
0x52F7E  JZ far  — skips if vtable[+8]() == 0  (outer gate)
0x52F95  JZ short — skips if vtable[+4]() == 0  (inner gate)
```

Both vtables return 0 in the shipping XBE (dev flags stripped
at release).  We NOP out both jumps so neither skip fires —
the selector path becomes unconditional.

No trampoline, no shim, no runtime code.  8 bytes of NOPs in
`.text`, full stop.

## Known caveats

- **`levels/earth/e4` plaque soft-locks.**  Selector references
  a cut level that isn't on the ISO (see `KNOWN_CUT_LEVELS` in
  `azurik_mod.assets`).  Touch it and the game hangs waiting
  for a level XBR that never loads.
- **May corrupt save files.**  Some per-character init state
  that the vanilla New Game ceremony sets up is skipped when
  the selector loads first.  Saves made in a dev-menu build
  may behave oddly if later loaded with this patch OFF.
- **Intended for level tours / speedrun practice** — not
  regular playthroughs.  Hence the `experimental` category.

## Further reading

- **LEARNINGS.md § selector.xbr** — full decode of
  `selector.xbr`'s structure + the gate discovery trail.
- **PATCHES.md** — catalog entry with the byte-level pin.
- **TOOLING_ROADMAP.md** — the `plan-trampoline` /
  `azurik-mod xbe find-refs` tools that shortened this
  investigation from hours to minutes.
