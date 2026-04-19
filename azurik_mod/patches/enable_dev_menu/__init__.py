"""enable_dev_menu — force ``selector.xbr`` (developer cheat hub)
to load at game start.

## What gets loaded

``selector.xbr`` is a legitimate but manifest-orphan level that
acts as a developer level-select hub: 22 portal strings to every
live level in the game + 10 direct cutscene triggers (prophecy,
training, possessed, etc.).  See docs/LEARNINGS.md § selector.xbr
for the full decode.

The vanilla ``default.xbe`` already contains every string + loader
code path needed to reach the selector — it just doesn't GET
reached because of two runtime checks in ``FUN_00052F50``
(documented Python-side as ``dev_menu_flag_check``).

## How the gate works

From the Ghidra decomp of ``FUN_00052F50``:

.. code-block:: c

    cVar3 = (**(code **)(*(int *)param_1[10] + 8))();
    if (cVar3 == '\\0') {
        // Big "new-save" bootstrap path — loads nothing.
    } else {
        cVar3 = (**(code **)(*(int *)param_1[10] + 4))();
        if (cVar3 == '\\0') {
            iVar11 = 1;
            pcVar10 = "levels/training_room";     // training gate
        } else {
            iVar11 = DAT_001bcdd8;
            if (DAT_001bcdd8 == -1) iVar11 = 3;
            pcVar10 = "levels/selector";           // dev menu!
        }
    }

Both vtable calls have to return non-zero for the selector path
to execute.  Shipping Azurik's vtables return 0 (the dev flags
were stripped at release time), so vanilla always falls into the
"training_room" branch.

## Our patch

We NOP-out the two ``JZ`` instructions that guard the selector
path.  Both jumps live at fixed file offsets we can patch
directly — no trampoline, no shim, no runtime code needed.

- ``0x42F7E``  ``0F 84 FB 00 00 00`` (6-byte JZ far) → 6× NOP
- ``0x42F95``  ``74 1C``             (2-byte JZ)     → 2× NOP

Total: 8 bytes of replacement, all in the ``.text`` section.
Every byte comes out identical on a NOP-ped XBE except those 8.

After the patch the vtable results are IGNORED and the selector
path is unconditional.  The BSS flag at VA ``0x001BCDD8`` then
controls WHICH SPOT in selector.xbr you spawn at (default 3 when
the flag's uninitialised / -1).

## Activation (what to do after you ship a patched ISO)

1. Build the patched ISO with ``enable_dev_menu`` ticked:

   .. code-block:: bash

      azurik-mod patch --iso Azurik.iso --mod \\
        '{"enable_dev_menu": true}' -o Azurik_devmenu.iso

   Or from the GUI: **Patches tab → Experimental → tick
   ``enable_dev_menu`` → Build & Logs → Start build**.

2. Boot the resulting ISO in xemu.  Skip / sit through the
   intro logos as usual (pair with ``qol_skip_logo`` for a
   faster iteration loop).

3. The game's "Start New Game" flow will now drop you directly
   into ``levels/selector`` — a small room with a wall of
   portal plaques.  Each plaque teleports you to one level or
   directly plays one cutscene.

4. Save (if you like) and quit.  The dev menu becomes your new
   "Start New Game" destination until you rebuild without the
   patch.

## Caveats

- **May break the original save flow.**  The vanilla "New Game"
  ceremony sets up player-character init state that the
  selector level doesn't.  Save files created after using the
  dev menu may behave oddly if loaded with this patch OFF.  Keep
  a backup of Azurik.iso before saving any new campaigns.
- **Selector references cut level ``levels/earth/e4``** — the
  plaque for e4 will portal to a missing level and soft-lock.
  Documented in ``KNOWN_CUT_LEVELS`` (azurik_mod.assets).
- **Intended for developers + speedrunners / level tours**, not
  regular play.  Hence the ``experimental`` category.

## Verifying the patch applied

After building, run:

.. code-block:: bash

    azurik-mod verify-patches --xbe patched.xbe --original vanilla.xbe --strict

Expected output: exactly 8 byte differences, all at file offsets
``0x42F7E..0x42F83`` + ``0x42F95..0x42F96``, and all matching
``0x90``.  Any other diff means something else ran.
"""

from __future__ import annotations

from azurik_mod.patching.registry import Feature, register_feature
from azurik_mod.patching.spec import PatchSpec


# Dev-menu gate — two nested JZ instructions in FUN_00052F50.
# Patching them to NOP forces the selector path regardless of
# what the vtable calls return.
#
# VAs (for reference / test pinning):
#   0x00052F7E  outer JZ  (6 bytes)  → skips if vtable[+8]() == 0
#   0x00052F95  inner JZ  (2 bytes)  → skips if vtable[+4]() == 0
#
# File offsets (what PatchSpec actually rewrites):
#   0x00042F7E = 0x00052F7E - base_addr(0x10000) + file_offset_of_text
#     Computed below via VA → file math in ``azurik_mod.patching.xbe``.
#
# We declare these as two separate PatchSpec sites so verify-patches
# reports them individually.

OUTER_JZ_SPEC = PatchSpec(
    label="Dev-menu outer gate (NOP out JZ far)",
    va=0x00052F7E,
    original=bytes.fromhex("0f84fb000000"),   # JZ rel32 → +0xFB
    patch=b"\x90" * 6,
    is_data=False,
    safety_critical=False,
)

INNER_JZ_SPEC = PatchSpec(
    label="Dev-menu inner gate (NOP out JZ short)",
    va=0x00052F95,
    original=bytes.fromhex("741c"),           # JZ rel8 → +0x1C
    patch=b"\x90" * 2,
    is_data=False,
    safety_critical=False,
)


DEV_MENU_SITES: list[PatchSpec] = [OUTER_JZ_SPEC, INNER_JZ_SPEC]


def apply_enable_dev_menu_patch(xbe_data: bytearray) -> None:
    """Patch both JZs in ``FUN_00052F50``'s selector gate.

    Idempotent — re-applying to an already-patched XBE is a no-op
    thanks to the ``replaced_bytes`` check in ``apply_patch_spec``.
    """
    from azurik_mod.patching.apply import apply_patch_spec
    for spec in DEV_MENU_SITES:
        apply_patch_spec(xbe_data, spec)


FEATURE = register_feature(Feature(
    name="enable_dev_menu",
    description=("Forces the developer cheat-menu level "
                 "(``selector.xbr``) to load at game start — "
                 "portals to every level and cutscene.  "
                 "Experimental: bypasses the vanilla 'New Game' "
                 "bootstrap, can corrupt new saves."),
    sites=DEV_MENU_SITES,
    apply=apply_enable_dev_menu_patch,
    default_on=False,
    included_in_randomizer_qol=False,
    category="experimental",
    tags=("cheat", "dev"),
))


__all__ = [
    "DEV_MENU_SITES",
    "INNER_JZ_SPEC",
    "OUTER_JZ_SPEC",
    "apply_enable_dev_menu_patch",
]
