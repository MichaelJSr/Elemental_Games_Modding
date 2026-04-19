"""enable_dev_menu — unlock Azurik's native in-game cheat UI.

## What this patch enables

Azurik has a built-in developer cheat UI (referenced in the shipping
XBE as strings from ``\\Elemental\\src\\game\\cheats.cpp``):

- ``"Game state..."`` submenu
- ``"magic level: %d"`` live editor for each element (Fire / Water /
  Air / Earth / Chromatic)
- ``"Change level..."`` + ``"startspot"`` level picker
- ``"foc cam"`` / ``"cheatsave"`` / ``"srcSpecies"`` hooks
- three developer toggles: ``"enable snapshot"``,
  ``"enable debug camera"``, and ``"enable cheat buttons"``

``"enable cheat buttons"`` is the MASTER gate.  When it's off
(vanilla), the cheat UI exists in the binary but never triggers —
the button combos and menu entries are silently ignored.  When it's
on, holding **LEFT TRIGGER** and pressing face buttons brings up the
cheat menu (see "Activation" below).

## How the gate works

The bool is stored at ``VA 0x0037AF20`` (BSS — zero-initialised at
load time, so it's ``false`` by default).  Every code site that
needs its value goes through a tiny wrapper function at
``VA 0x000FFFC0``:

.. code-block:: text

    FUN_000fffc0:
        68 20 AF 37 00   PUSH  0x0037AF20      ; &enable_cheat_buttons
        E8 86 E1 FC FF   CALL  cvar_read       ; -> EAX
        C3               RET
        90 90 90 90 90   NOP padding

The function returns whatever the CVar system reads for the
``"enable cheat buttons"`` cvar.  Because the storage is in BSS,
we can't flip the default by patching ``.data`` — there are no
stored bytes to flip.  Instead we short-circuit the GETTER:

.. code-block:: text

    Patched FUN_000fffc0:
        B8 01 00 00 00   MOV EAX, 1
        C3               RET
        (86 E1 FC FF C3) unreachable tail (unchanged)
        90 90 90 90 90   NOP padding (unchanged)

Every caller now reads ``1`` without ever touching the real cvar.
Total diff: 6 bytes.  Clean byte patch, no shim, no trampoline.

## Activation (after building a patched ISO)

1. Build with ``enable_dev_menu`` ticked:

   .. code-block:: bash

      azurik-mod patch --iso Azurik.iso --mod \\
        '{"enable_dev_menu": true}' -o Azurik_devmenu.iso

   Or from the GUI: **Patches → Experimental → tick
   ``enable_dev_menu`` → Build & Logs → Start build**.

2. Boot the patched ISO in xemu.  No special boot-time combo — the
   cheat buttons are live from the first frame.

3. **In-game, hold LEFT TRIGGER** and press one of the face
   buttons (A / B / X / Y) to trigger the cheat dispatcher
   (``FUN_00083d80`` -> ``FUN_00083410(0..3)``).  The four slots
   map to the four developer actions registered in
   ``FUN_000721b0``'s command table — typically "Game state",
   "magic level", "Change level", and either snapshot or foc-cam
   depending on the build.

4. If a cheat enters a modal menu (magic level editor, level
   picker), the DPad / analog stick navigates it and A / B confirm
   or cancel.

## Caveats

- **The two companion cheat cvars are still off.**  ``"enable
  debug camera"`` lives at ``VA 0x0037B148`` and ``"enable
  snapshot"`` at ``VA 0x0037AFA0``; if you want them too, either
  build similar byte patches on their getters
  (``FUN_000FFFD0`` and ``FUN_000FFFE0``) or write a C shim that
  pokes all three BSS bytes to 1 at startup.
- **Save files may behave oddly.**  The cheat UI edits player
  stats and bypasses the vanilla "New Game" init ceremony —
  saving a game with cheat-modified stats and then loading it on
  an un-patched ISO isn't something the shipping game tests for.
  Keep a backup of your save directory.
- **Category is ``experimental``** — ship quality isn't promised;
  this is a developer tool the team happened to leave compiled
  into the binary.

## Why the previous patch (selector.xbr) was wrong

Before April 2026 this feature patched two ``JZ`` instructions in
``FUN_00052F50`` to force ``levels/selector`` (a dev level-select
hub level) to load at game start.  That's a SEPARATE feature from
the in-game cheat UI and didn't give us magic-level editing or any
runtime cheats — it just swapped "New Game" for "Load Selector
Level".  The user report "doesn't do anything" was correct: the
selector loads, but only on the New Game flow, so casual testing
(boot + explore) never saw the change.  The new patch lands where
the user expects.

## Verifying the patch applied

.. code-block:: bash

    azurik-mod verify-patches --xbe patched.xbe --original vanilla.xbe --strict

Expected diff: exactly **6 bytes** differ, all at file offsets
``va_to_file(0x000FFFC0)..va_to_file(0x000FFFC5)``.  Any other
diff means something else ran.
"""

from __future__ import annotations

from azurik_mod.patching.registry import Feature, register_feature
from azurik_mod.patching.spec import PatchSpec


# ---------------------------------------------------------------------------
# Byte patch: short-circuit the "enable cheat buttons" cvar getter
# so it unconditionally returns 1.
# ---------------------------------------------------------------------------
#
# The getter stub lives at VA 0x000FFFC0 and is 11 bytes long
# (plus 5 bytes of NOP padding to align the next function):
#
#   68 20 AF 37 00  PUSH  0x0037AF20   ; &enable_cheat_buttons
#   E8 86 E1 FC FF  CALL  cvar_read
#   C3              RET
#   90 x 5          NOP padding
#
# We replace the first 6 bytes with `MOV EAX, 1; RET`, leaving the
# last 5 bytes of code (CALL tail + RET) as effectively unused
# padding that the RET above never reaches.
#
# Replacement:
#   B8 01 00 00 00  MOV EAX, 1
#   C3              RET
#
# 6 bytes, entirely in .text.  Keeps the same function size so no
# call-site offsets shift.

CHEAT_GETTER_SPEC = PatchSpec(
    label="enable_cheat_buttons getter (short-circuit to true)",
    va=0x000FFFC0,
    original=bytes.fromhex("6820af3700e8"),   # PUSH 0x37AF20 ; CALL (first byte)
    patch=bytes.fromhex("b801000000c3"),      # MOV EAX, 1 ; RET
    is_data=False,
    safety_critical=False,
)
# Replacement layout: 6 bytes total.  After the RET at offset 5, the
# original CALL's trailing 4 bytes (`86 E1 FC FF`) and the original
# RET byte (`C3`) become unreachable tail — they're never executed,
# and the stub's only external xref is to 0xFFFC0 (function start),
# so no other code jumps mid-function.  Bytes 6..15 are left alone
# (original CALL tail + original RET + 5 NOPs of alignment padding).


DEV_MENU_SITES: list[PatchSpec] = [CHEAT_GETTER_SPEC]


def apply_enable_dev_menu_patch(xbe_data: bytearray) -> None:
    """Unlock the in-game cheat UI by forcing the
    ``"enable cheat buttons"`` cvar getter to always return 1.

    Idempotent — re-applying to an already-patched XBE is a no-op
    thanks to the ``original``-bytes check in ``apply_patch_spec``.
    """
    from azurik_mod.patching.apply import apply_patch_spec
    for spec in DEV_MENU_SITES:
        apply_patch_spec(xbe_data, spec)


FEATURE = register_feature(Feature(
    name="enable_dev_menu",
    description=(
        "Unlocks Azurik's built-in cheat UI (magic-level editor, "
        "level picker, game-state tools).  Hold LEFT TRIGGER + "
        "press A/B/X/Y in-game to open the menu.  "
        "Experimental: bypasses runtime stat checks, may leave "
        "save files in odd states."
    ),
    sites=DEV_MENU_SITES,
    apply=apply_enable_dev_menu_patch,
    default_on=False,
    included_in_randomizer_qol=False,
    category="experimental",
    tags=("cheat", "dev"),
))


__all__ = [
    "CHEAT_GETTER_SPEC",
    "DEV_MENU_SITES",
    "apply_enable_dev_menu_patch",
]
