"""enable_dev_menu — force ``levels/selector`` to load at game start.

## What it does

``selector.xbr`` is a manifest-orphan developer level that ships in
the retail ISO: a small room with portal plaques to every live
level + one-click triggers for every cutscene.  Vanilla code
paths DO reach it under specific dev-flag combinations, but the
shipping game strips those flags and ``selector`` only ever
loads via a triple-fallback chain inside ``dev_menu_flag_check``
(``FUN_00052F50``).  This patch forces the third fallback —
which is hard-coded to ``"levels/selector"`` — to always win.

## Why the old JZ NOPs failed

``dev_menu_flag_check`` ends with a three-stage level-name
validator:

.. code-block:: c

    uVar6 = FUN_00054520();            // (1) validate caller's param_2
    if ((char)uVar6 == '\\0') {
        uVar6 = FUN_00054520();        // (2) validate pcVar10 string
        if ((char)uVar6 == '\\0') {
            uVar6 = FUN_00054520();    // (3) validate "levels/selector"
            if ((char)uVar6 == '\\0') {
                FUN_000a9100("can't find a level to go to.");
            }
            param_2 = "levels/selector";
        } else {
            param_2 = pcVar10;
        }
    }
    FUN_00053750(param_1, param_2, param_3, '\\0');

The pre-April-2026 patch NOPed two ``JZ`` instructions in the
branch that sets ``pcVar10 = "levels/selector"`` (VA 0x52F7E +
VA 0x52F95).  But those NOPs only mattered for validator (2);
validator (1) almost always succeeds because real callers
(``FUN_00052910``, ``FUN_00055AB0``, ``FUN_00056620``) pass a
known-valid level string.  When (1) succeeds, the caller's
``param_2`` is used and our pcVar10 fix is ignored — which
is exactly why users reported the patch as *"does nothing"*.

Worse, the outer JZ NOP skipped important save-bootstrap work
when the vtable gate would have opened the new-save path,
potentially leaving the game in a half-initialised state.

## What the new patch does

Short-circuit validators (1) and (2) to always return ``0``:

- VA ``0x00053384``: ``E8 97 11 00 00`` (CALL ``FUN_00054520``)
  → ``31 C0 90 90 90`` (``XOR EAX, EAX ; NOP ; NOP ; NOP``)
- VA ``0x000533C3``: ``E8 58 11 00 00`` (CALL ``FUN_00054520``)
  → ``31 C0 90 90 90``

Flow now cascades:

1. First validator → ``AL = 0`` → ``JZ`` fires → second try.
2. Second validator → ``AL = 0`` → ``JZ`` fires → third try.
3. Third validator runs normally with EAX = ``"levels/selector"``
   (already hard-coded in the original assembly at ``VA
   0x533E3``).  ``selector.xbr`` exists in every vanilla ISO, so
   this call returns non-zero.
4. Flow unconditionally reaches the ``PUSH "levels/selector";
   PUSH ECX; CALL FUN_00053750`` at ``VA 0x00053406``.

**Key insight**: the third fallback already pushes
``"levels/selector"`` as ``param_2``.  We don't have to modify
any strings or redirect any pointers — we just have to force
the code past the first two bailouts.

Total diff: 10 bytes, all in ``.text``.  No trampoline, no
shim, no stack tampering.

## Side-effect profile

- ``FUN_00054520`` is a read-only level-asset probe (calls
  ``FUN_000A5C10("level_name")`` which checks whether the
  asset exists).  Skipping the first two calls has no game-
  state side effects.
- Stack balance is preserved: both NOPs are 5-byte code-for-
  code replacements; EAX is scratched but the surrounding
  register save/restore (EBP / ESI / EDI / EBX pushed at
  function entry, popped at epilogue) is untouched.
- The outer vtable gate at VA 0x52F7E / inner at 0x52F95 run
  normally — save-bootstrap logic executes as vanilla when
  the vtable call returns 0 (i.e., for new-save flows).  We
  only force the final level-name to be ``levels/selector``;
  whatever setup ran before gets applied to the selector
  level, not a user-chosen level.

## Activation

1. Build the patched ISO with ``enable_dev_menu`` ticked.
2. Boot it in xemu.  You'll see the main menu as usual.
3. Click ``New Game`` (or whichever entry triggers level
   loading).  The game loads ``levels/selector`` instead of
   the expected level — a small room with portal plaques.
4. Each plaque teleports to a live level or triggers a
   cutscene directly.  Pair with ``qol_skip_logo`` to skip
   the Adrenium intro on every boot.

## Caveats

- **Overrides EVERY level-load call**, not just New Game.
  If you try to load a save from the main menu, you'll still
  land in selector — save states may appear with the player
  at the wrong position.
- **``levels/earth/e4`` plaque is a dead portal** (cut level,
  not shipped in ``KNOWN_CUT_LEVELS``).  Touching it
  soft-locks on a missing-asset wait.
- **Experimental category** — intended for level tours /
  speedrun practice, not regular play.  Back up your save
  directory before building.

## Verifying

.. code-block:: bash

    azurik-mod verify-patches --xbe patched.xbe \\
        --original vanilla.xbe --strict

Expected diff: exactly **10 bytes** at file offsets
``va_to_file(0x00053384)..+4`` and ``va_to_file(0x000533C3)..+4``
(5 contiguous bytes each, both replaced with ``31 C0 90 90 90``).
"""

from __future__ import annotations

from azurik_mod.patching.registry import Feature, register_feature
from azurik_mod.patching.spec import PatchSpec


# ---------------------------------------------------------------------------
# Force dev_menu_flag_check's third-try fallback to always win
# ---------------------------------------------------------------------------
#
# Replace the first two CALLs to FUN_00054520 (the level-asset
# validator) with ``XOR EAX, EAX`` so AL=0 at each TEST/JZ.  The
# following JZs then fire unconditionally, cascading flow into the
# third-try branch which hard-codes ``PUSH "levels/selector"``
# before the final CALL FUN_00053750.
#
# Both replacements are 5-byte code-for-code rewrites (3 bytes of
# XOR + 3 NOPs, padded to the original 5-byte CALL width).  No
# stack impact, no function-signature changes, no trampolines.


FIRST_VALIDATOR_SPEC = PatchSpec(
    label="Force first level-name validator to fail "
          "(caller's param_2 is bypassed)",
    va=0x00053384,
    original=bytes.fromhex("e897110000"),   # CALL FUN_00054520
    patch=bytes.fromhex("31c0909090"),      # XOR EAX, EAX ; NOP*3
    is_data=False,
    safety_critical=False,
)

SECOND_VALIDATOR_SPEC = PatchSpec(
    label="Force second level-name validator to fail "
          "(pcVar10 branch is bypassed)",
    va=0x000533C3,
    original=bytes.fromhex("e858110000"),   # CALL FUN_00054520
    patch=bytes.fromhex("31c0909090"),      # XOR EAX, EAX ; NOP*3
    is_data=False,
    safety_critical=False,
)


DEV_MENU_SITES: list[PatchSpec] = [
    FIRST_VALIDATOR_SPEC,
    SECOND_VALIDATOR_SPEC,
]


def apply_enable_dev_menu_patch(xbe_data: bytearray) -> None:
    """Force ``dev_menu_flag_check`` to route to its third-try
    fallback, which loads ``levels/selector``.

    Idempotent — re-applying to an already-patched XBE is a no-op
    thanks to the ``original``-bytes check in ``apply_patch_spec``.
    """
    from azurik_mod.patching.apply import apply_patch_spec
    for spec in DEV_MENU_SITES:
        apply_patch_spec(xbe_data, spec)


FEATURE = register_feature(Feature(
    name="enable_dev_menu",
    description=(
        "Forces the developer level-select hub (levels/selector) "
        "to load when the game tries to load ANY level.  The "
        "selector room has portal plaques to every live level "
        "and cutscene.  Experimental: overrides all normal "
        "level-load paths, including save loading."
    ),
    sites=DEV_MENU_SITES,
    apply=apply_enable_dev_menu_patch,
    default_on=False,
    included_in_randomizer_qol=False,
    category="experimental",
    tags=("cheat", "dev"),
))


__all__ = [
    "DEV_MENU_SITES",
    "FIRST_VALIDATOR_SPEC",
    "SECOND_VALIDATOR_SPEC",
    "apply_enable_dev_menu_patch",
]
