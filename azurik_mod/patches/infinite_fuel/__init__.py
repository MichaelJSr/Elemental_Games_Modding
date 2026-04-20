"""infinite_fuel — never consume elemental power fuel.

## Why

Each elemental power (water, fire, air, earth) has a fuel bar
(see the "water fuel max" / "air fuel max" etc. cvars).  Using
a power — flapping for air, firing a fireball, etc. — calls
``FUN_000842D0`` which decrements ``armor.fuel_current`` by
``cost / fuel_max``.  When the bar empties, the power stops
working until the player picks up a fuel gem.

For exploration / speedrunning / sandbox builds, a "never run
out of fuel" toggle is very convenient.

## How it works

``FUN_000842D0`` is short and has one clear role:

.. code-block:: c

    undefined4 __thiscall FUN_000842D0(void *this, float cost) {
        if (armor == 0)       return 0;      // no armor equipped
        if (fuel_max == 0)    return 0;      // no fuel system (e.g. air power 0)
        if (fuel_current == 0) return 0;      // empty → caller aborts
        fuel_current -= cost / fuel_max;      // **consumption write**
        if (fuel_current < 0.5 × cost/max) {
            fuel_current = 0;                 // empty-out
            FUN_000837A0(this);               // "out of fuel" sfx/ui
        }
        return 1;                             // "consumed, proceed"
    }

Every power checks this function's return before allowing the
action.  If it returns 1, the action proceeds.  If 0, the
action is blocked (e.g. wing flap refused).

Patch goal: **always return 1, never consume**.  The cleanest
way to do that is a 5-byte prologue rewrite:

.. code-block:: asm

    Before:  51 8B 41 20 85         PUSH ECX ; MOV EAX, [ECX+0x20] ; TEST EAX, …
    After:   B0 01 C2 04 00         MOV AL, 1 ; RET 4

``RET 4`` pops the single ``float cost`` stack arg (the
function is ``__thiscall``, so ECX holds ``this`` and the
stack has one pushed float).  ``AL`` is the return-value
register for bool-returning x86 functions.  Nothing else is
touched — caller's saved registers (EBP, EBX, ESI, EDI) are
preserved by this two-instruction path.

5 bytes total.  The bytes from ``+5`` onward are never
executed so they remain identical to vanilla.

## Side effects

- The "out of fuel" audio cue + UI flash (``FUN_000837A0``)
  never fires.  Since the player never runs out, that's
  the intended UX.
- Fuel HUD bars (the ingame element-ring fuel meters) still
  render whatever ``fuel_current`` last contained.  Because
  we skip the consumption write, the bar stays at its
  starting value forever — which looks correct.
- Fuel pickup gems still increment fuel_current.  They just
  become pointless (the bar never drops).
- No interaction with ``fuel_increase`` / ``fuel-cap``
  gem systems; those run elsewhere and are untouched.

## Safety

Fully orthogonal to every other pack.  The prologue
overwrite is contained to ``FUN_000842D0``'s first 5 bytes
of ``.text`` and cannot cascade.
"""

from __future__ import annotations

from azurik_mod.patching.registry import Feature, register_feature
from azurik_mod.patching.spec import PatchSpec


# VA of FUN_000842D0's function prologue.
AZURIK_CONSUME_FUEL_VA = 0x000842D0


INFINITE_FUEL_SPEC = PatchSpec(
    label="FUN_000842D0 → always return 1 (no fuel consumed)",
    va=AZURIK_CONSUME_FUEL_VA,
    # Vanilla prologue: PUSH ECX ; MOV EAX, [ECX+0x20] ; TEST EAX, EAX.
    original=bytes.fromhex("518b412085"),
    # MOV AL, 1 ; RET 4 — returns success, pops the one float arg.
    patch=bytes.fromhex("b001c20400"),
    is_data=False,
    safety_critical=False,
)


INFINITE_FUEL_SITES: list[PatchSpec] = [INFINITE_FUEL_SPEC]


def apply_infinite_fuel_patch(xbe_data: bytearray) -> None:
    """Rewrite FUN_000842D0's first 5 bytes to ``MOV AL, 1 ; RET 4``.

    Idempotent — re-applying to an already-patched XBE is a no-op
    thanks to the ``original``-bytes guard inside
    :func:`apply_patch_spec`.
    """
    from azurik_mod.patching.apply import apply_patch_spec
    for spec in INFINITE_FUEL_SITES:
        apply_patch_spec(xbe_data, spec)


FEATURE = register_feature(Feature(
    name="infinite_fuel",
    description=(
        "Elemental powers never consume fuel.  Rewrites the "
        "fuel-consumer function (FUN_000842D0) prologue to "
        "``MOV AL, 1 ; RET 4`` — always reports success without "
        "decrementing the fuel bar.  Works for water, fire, air, "
        "and earth powers uniformly."
    ),
    sites=INFINITE_FUEL_SITES,
    apply=apply_infinite_fuel_patch,
    default_on=False,
    included_in_randomizer_qol=False,
    category="player",
    tags=("cheat", "powers"),
))


__all__ = [
    "AZURIK_CONSUME_FUEL_VA",
    "INFINITE_FUEL_SITES",
    "INFINITE_FUEL_SPEC",
    "apply_infinite_fuel_patch",
]
