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

## How it works

The fall-damage dispatcher lives in ``FUN_0008AB70``.  Its
first gameplay check (after the one-time cvar-cache init
block at the top) is:

.. code-block:: asm

    0008AC66   D9 44 24 14        FLD  [ESP+0x14]       ; fall_speed
    0008AC6A   D9 E1              FABS
    0008AC6C   DC 1D 90 02 39 00  FCOMP [0x00390290]    ; vs fall_min_velocity
    0008AC72   DF E0              FNSTSW AX
    0008AC74   F6 C4 05           TEST AH, 5
    0008AC77   0F 8B 7F 01 00 00  JNP 0x0008ADFC        ; ↓ "no damage" return

At VA 0x0008ADFC the function does ``XOR AL, AL; RET 8`` —
i.e., "return 0 (no damage dealt)".  The ``JNP`` fires when
``|fall_speed| < fall_min_velocity`` (player landed softly).
Flipping the conditional to unconditional routes every
landing — no matter how violent — to the no-damage return.

## Patch

6 bytes at VA ``0x0008AC77``:

    Before: 0F 8B 7F 01 00 00    JNP  rel32 → 0x0008ADFC
    After:  E9 80 01 00 00 90    JMP  rel32 → 0x0008ADFC  +  NOP

``rel32`` is recomputed because a 5-byte near-JMP's origin-
after-instruction differs from the 6-byte JNP's by one byte:

    JNP:  target = 0x0008AC77 + 6 + 0x0000017F = 0x0008ADFC
    JMP:  target = 0x0008AC77 + 5 + 0x00000180 = 0x0008ADFC

Trailing NOP (0x90) preserves the 6-byte slot so the
instruction boundaries of the surrounding code remain
identical — no fall-through hazards, no disassembler
confusion.

## Side effects

- The splat SFX and "fall damage" rumble never fire.  Players
  who actually *want* the splat SFX on hard landings can leave
  this patch off.
- HP is not modified on landing, but also is not *restored*:
  if the player took damage before falling, they keep that
  damage.
- The cvar-cache init block at the top of FUN_0008AB70 (ensures
  "fall height 1", "fall damage 1" etc. are cached as static
  doubles) still runs once per boot.  Harmless but visible
  in a memory-trace.

The ``save_editor``, ``qol_skip_save_signature``, and every
other pack are orthogonal to this one.
"""

from __future__ import annotations

from azurik_mod.patching.registry import Feature, register_feature
from azurik_mod.patching.spec import PatchSpec


# VA of the conditional branch at the top of FUN_0008AB70.
NO_FALL_DAMAGE_VA = 0x0008AC77


NO_FALL_DAMAGE_SPEC = PatchSpec(
    label="Disable fall damage (JNP → JMP at top of FUN_0008AB70)",
    va=NO_FALL_DAMAGE_VA,
    # JNP rel32 → 0x0008ADFC (the "return 0 / no damage" tail).
    original=bytes.fromhex("0f8b7f010000"),
    # JMP rel32 → 0x0008ADFC + trailing NOP (preserves the 6-byte slot).
    patch=bytes.fromhex("e980010000") + b"\x90",
    is_data=False,
    safety_critical=False,
)


NO_FALL_DAMAGE_SITES: list[PatchSpec] = [NO_FALL_DAMAGE_SPEC]


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
        "Disables fall damage.  Flips the top-level branch in "
        "FUN_0008AB70 so every landing — no matter the velocity — "
        "routes to the \"no damage dealt\" return path.  Leaves "
        "the HP max, damage-multiplier, and other damage systems "
        "untouched; only fall damage is affected."
    ),
    sites=NO_FALL_DAMAGE_SITES,
    apply=apply_no_fall_damage_patch,
    default_on=False,
    included_in_randomizer_qol=False,
    category="player",
    tags=("cheat", "movement"),
))


__all__ = [
    "NO_FALL_DAMAGE_SITES",
    "NO_FALL_DAMAGE_SPEC",
    "NO_FALL_DAMAGE_VA",
    "apply_no_fall_damage_patch",
]
