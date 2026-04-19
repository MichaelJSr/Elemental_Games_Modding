# enable_dev_menu

Unlocks Azurik's built-in **in-game developer cheat UI** —
magic-level editing, level picker, game-state tools, etc.

> **Category**: experimental.  The cheat UI bypasses runtime stat
> checks, so save files made after using it may behave oddly if
> later loaded on an un-patched ISO.  Keep a backup.

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

Pair with `qol_skip_logo` if you want to skip the Adrenium intro
each run:

```bash
azurik-mod patch \
    --iso 'Azurik.iso' \
    --mod '{"enable_dev_menu": true, "qol_skip_logo": true}' \
    -o 'Azurik_dev.iso'
```

## Using the cheat UI in-game

After the patch lands, the cheat buttons are live from the first
frame — no special boot combo.

**In-game**, **hold LEFT TRIGGER** and press one of the face
buttons (A / B / X / Y) to trigger a developer action.  The four
slots are registered by `FUN_000721b0` in the shipping XBE and
typically cover:

- Game state menu (save / restore / dump)
- Magic level editor (Fire / Water / Air / Earth / Chromatic)
- Level picker with `startspot` selection
- Snapshot or floating-camera toggle (build-dependent)

If a cheat enters a modal menu (magic-level editor, level picker),
the D-Pad / analog stick navigates it and A / B confirm or cancel.

## What it actually patches

Azurik's `cheats.cpp` registers three boolean cvars, all defaulting
to `false`:

| CVar                    | Storage VA | Getter VA |
|-------------------------|-----------:|----------:|
| `enable cheat buttons`  | `0x0037AF20` | `0x000FFFC0` |
| `enable debug camera`   | `0x0037B148` | `0x000FFFD0` |
| `enable snapshot`       | `0x0037AFA0` | `0x000FFFE0` |

The storage lives in BSS (zero-initialised at load), so there are
no stored bytes in the XBE to flip.  Instead, we patch the
**getter function** at `0x000FFFC0` to unconditionally return
`1`:

Vanilla getter (16 bytes, 11 code + 5 NOP padding):

```
68 20 AF 37 00   PUSH  0x0037AF20        ; &enable_cheat_buttons
E8 86 E1 FC FF   CALL  cvar_read
C3               RET
90 90 90 90 90   NOP padding
```

Patched getter:

```
B8 01 00 00 00   MOV   EAX, 1
C3               RET
86 E1 FC FF C3   (unreachable tail — unchanged)
90 90 90 90 90   NOP padding (unchanged)
```

Diff: exactly **6 bytes** in `.text` at file offset
`va_to_file(0x000FFFC0)`.  No trampoline, no shim.

## Verifying the patch applied

```bash
azurik-mod verify-patches \
    --xbe patched.xbe --original vanilla.xbe --strict
```

Expected diff: exactly **6 bytes** differ, all at file offset
`va_to_file(0x000FFFC0)..+5`.

## Known caveats

- **`enable debug camera` + `enable snapshot` are still off.**
  Their getters live at VA `0x000FFFD0` and `0x000FFFE0` with the
  same 6-byte layout — duplicating this patch there would unlock
  them too, but we don't ship that by default because the snapshot
  hook has been reported to crash in some emulator configurations.
- **Save files may behave oddly** if stat-edited saves are loaded
  on an un-patched ISO (the vanilla "New Game" ceremony sets up
  some per-character init state that the cheat UI doesn't touch).
- **Intended for developers / speedrunners / level tours** —
  hence the `experimental` category.

## History

Before April 2026, this feature patched two `JZ` instructions in
`FUN_00052F50` to force `levels/selector` (a developer level-select
hub) to load at game start.  That's a SEPARATE feature — it
swapped "New Game" for "Load Selector Level" but didn't enable
any in-game cheats.  The user report *"doesn't do anything"* was
correct: the selector level loads, but only on the New Game flow
and only if you actually start a new game, so casual testing
(boot + explore the main menu) never saw the change.

The current patch targets the cvar getter instead — the cheat
buttons light up immediately, which is what users expect from a
patch named "enable dev menu".  The previous JZ-NOP approach
could be revived as a separate `qol_force_selector_menu` feature
if it ever becomes useful again (documented in
`docs/LEARNINGS.md` § selector.xbr).

## Further reading

- **docs/LEARNINGS.md § "Native cheat UI"** — full decode of the
  `cheats.cpp` registration + button-dispatch chain.
- **docs/LEARNINGS.md § selector.xbr** — the alternate dev menu
  this patch used to target.
- **PATCHES.md** — catalog entry with the byte-level pin.
