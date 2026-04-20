"""no_fall_damage — make fall damage harmless (one-byte branch flip).

## Why

Azurik scales fall damage across three tiers ("fall height 1/2/3",
"fall damage 1/2/3") plus a minimum-velocity gate
("fall min velocity") read from ``config.xbr``.  When the player
hits the ground with ``|vz| >= fall_min_velocity``, the game
subtracts HP and plays the splat SFX.

For speedrunning, map-exploration, and general "let me fly
around with my air power" workflows, fall damage is a nuisance.
This pack disables it without touching HP max or any other
damage system.

## How it works — v2 (April 2026 late)

Pre-v2 this patch flipped the **top-of-function JNP** at VA
``0x0008AC77`` from ``JNP → JMP`` (rel32 to ``0x0008ADFC``,
the "no damage dealt" return).  User testing revealed it
prevented only *some* fall damage — high-velocity falls
(instant-death) were blocked, but moderate falls still
applied light damage.

The likely reason: certain callers reach the damage-application
site in FUN_0008AB70 through execution paths other than the
one JNP we flipped (there are 9+ conditional branches in the
function's cvar-cache init chain; not all routes to the
damage block went through our single flip).

v2 uses the more surgical approach: **rewrite the function
prologue to return immediately**.  ``FUN_0008AB70`` is
``__stdcall`` with 2 float parameters (fall_height,
fall_speed), so its correct early-return is
``XOR AL, AL ; RET 8``:

.. code-block:: asm

    0008AB70  51              PUSH ECX                 <-- before
    0008AB71  A1 98 02 39 00  MOV  EAX, [0x00390298]
    0008AB76  53              PUSH EBX                 ...

becomes:

.. code-block:: asm

    0008AB70  32 C0           XOR  AL, AL              <-- after (return 0)
    0008AB72  C2 08 00        RET  8                    (pop 2 floats, return)
    0008AB75  90              NOP                       (pad to 6 bytes)

Six bytes patched.  The entire cvar-cache init chain, tier
selector, FCOMP gauntlet, and damage application (the
``FUN_00044640`` call at 0x0008AD9B) become unreachable.

Callers that ignore the return value still get the correct
stack cleanup (``RET 8`` pops the 2 pushed floats).  Callers
that check the return value always see "no damage" (AL=0),
which they treat as "the fall was soft enough to skip the
splat SFX" — matching the intuitive in-game behaviour.

## Side effects

- The splat SFX and "fall damage" rumble never fire.  Players
  who actually *want* the splat SFX on hard landings can leave
  this patch off.
- HP is not modified on landing.  HP restoration / other
  damage paths (combat, traps, lava, drowning) are untouched.
- The one-time cvar-cache init block at the top of FUN_0008AB70
  never runs.  The cached globals (DAT_00390228 etc.) remain
  zero-initialised.  Since nothing else references those
  specific doubles, this is harmless.

The ``save_editor``, ``qol_skip_save_signature``, and every
other pack are orthogonal to this one.
"""

from __future__ import annotations

from azurik_mod.patching.registry import Feature, register_feature
from azurik_mod.patching.spec import PatchSpec


# VA of the FUN_0008AB70 function prologue (fall damage dispatcher).
NO_FALL_DAMAGE_VA = 0x0008AB70

# VA of FUN_0008BE00 — the "no-surface landing" damage path.
# This is the SECOND fall-damage path that fires when the player
# lands without a surface contact slot populated (e.g., falling
# off the world edge, water splash, etc.).  It also calls
# FUN_00044640 (damage apply).  Added late April 2026 after the
# user reported "light damage still fires with no_fall_damage on."
AZURIK_FALL_DEATH_VA = 0x0008BE00


NO_FALL_DAMAGE_SPEC = PatchSpec(
    label="Disable fall damage (XOR AL,AL ; RET 8 at FUN_0008AB70 entry)",
    va=NO_FALL_DAMAGE_VA,
    # Vanilla prologue: PUSH ECX ; MOV EAX, [0x00390298] (first 6 bytes).
    original=bytes.fromhex("51a198023900"),
    # XOR AL, AL ; RET 8 ; NOP — 6-byte always-return-0.
    patch=bytes.fromhex("32c0c2080090"),
    is_data=False,
    safety_critical=False,
)


FALL_DEATH_SPEC = PatchSpec(
    label="Disable no-surface fall damage "
          "(XOR AL,AL ; RET 4 at FUN_0008BE00 entry)",
    va=AZURIK_FALL_DEATH_VA,
    # Vanilla prologue: SUB ESP, 0x20 ; PUSH EBX ; PUSH EBP.
    original=bytes.fromhex("83ec205355"),
    # XOR AL, AL ; RET 4 — 5-byte always-return-0 (__stdcall, 1 arg).
    patch=bytes.fromhex("32c0c20400"),
    is_data=False,
    safety_critical=False,
)


NO_FALL_DAMAGE_SITES: list[PatchSpec] = [
    NO_FALL_DAMAGE_SPEC,
    FALL_DEATH_SPEC,
]


def apply_no_fall_damage_patch(xbe_data: bytearray) -> None:
    """Rewrite the top-of-function JNP in FUN_0008AB70 to JMP,
    routing every landing to the "no damage" return tail.

    Idempotent — re-applying to an already-patched XBE is a no-op
    thanks to the ``original``-bytes guard inside
    :func:`apply_patch_spec`.
    """
    from azurik_mod.patching.apply import apply_patch_spec
    for spec in NO_FALL_DAMAGE_SITES:
        apply_patch_spec(xbe_data, spec)


FEATURE = register_feature(Feature(
    name="no_fall_damage",
    description=(
        "[BROKEN — prefer the config editor] Rewrites the "
        "prologues of both fall-damage dispatchers "
        "(fall_damage_dispatch at 0x8AB70 and fall_death_dispatch "
        "at 0x8BE00) to XOR AL,AL ; RET N.  User testing on 2026-04 "
        "showed damage still fires via a third path we haven't "
        "pinned down yet.  Workaround: open the config editor, "
        "section `damage`, and raise the thresholds / reduce the "
        "multipliers for fall-height 1/2/3; or edit `critters_damage` "
        "→ hitPoints on the player row so every fall is survivable."
    ),
    sites=NO_FALL_DAMAGE_SITES,
    apply=apply_no_fall_damage_patch,
    default_on=False,
    included_in_randomizer_qol=False,
    category="player",
    tags=("cheat", "movement", "broken"),
))


__all__ = [
    "AZURIK_FALL_DEATH_VA",
    "FALL_DEATH_SPEC",
    "NO_FALL_DAMAGE_SITES",
    "NO_FALL_DAMAGE_SPEC",
    "NO_FALL_DAMAGE_VA",
    "apply_no_fall_damage_patch",
]
