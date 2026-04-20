"""infinite_fuel — never consume elemental power fuel.

## Why

Each elemental power (water, fire, air, earth) has a fuel bar
(see the "water fuel max" / "air fuel max" etc. cvars).  Using
a power — flapping for air, firing a fireball, etc. — consumes
fuel; when the bar empties, the power stops working until the
player picks up a fuel gem.

For exploration / speedrunning / sandbox builds, a "never run
out of fuel" toggle is very convenient.

## How it works — v2 (late April 2026)

Azurik has **two distinct fuel drain paths**:

1. **Event-driven drain** (wing flap / attack fire) — goes
   through ``FUN_000842D0(this=armor_mgr, float cost)``.
   Called with a specific cost (e.g. 1.0 for a flap, 100.0
   for a subsequent flap beyond 6m, per-attack "Fuel
   multiplier" for element spells).
2. **Per-frame sustained drain** — runs inside
   ``FUN_00083D80`` every frame (presumably the armor-state
   tick).  Computes ``fuel_current -= (1/30) / drain_rate``
   using a 4-instruction FP sequence at VA 0x83DE3-0x83DF1.
   This is why just patching FUN_000842D0 (v1) was
   incomplete — users reported fuel still draining because
   this sustained path was untouched.

v2 patches BOTH sites:

**Site 1: ``FUN_000842D0`` prologue (5 bytes at VA 0x842D0).**
Rewritten to ``MOV AL, 1 ; RET 4`` — always returns 1
(consumed/success) without touching fuel.  Caller proceeds
with the action.

.. code-block:: asm

    Before:  51 8B 41 20 85             PUSH ECX ; MOV EAX, [ECX+0x20] ; TEST EAX, …
    After:   B0 01 C2 04 00             MOV AL, 1 ; RET 4

**Site 2: per-frame fuel-drain block (15 bytes at VA 0x83DE3).**
NOPed out so the fuel bar is never decremented per-frame.
The 4 FP instructions (FLD + FDIV + FSUBR + FSTP) have a
balanced stack delta (+1 - 1 = 0), so replacing the whole
block with 15 × ``NOP`` leaves the FP stack and rest of
the function intact.

.. code-block:: asm

    Before:  D9 05 20 81 19 00         FLD   [0x00198120]       ; 1/30 (frame time)
             D8 71 34                   FDIV  [ECX+0x34]          ; / drain_rate
             D8 6E 24                   FSUBR [ESI+0x24]          ; fuel - x
             D9 5E 24                   FSTP  [ESI+0x24]          ; write back
    After:   90 × 15                    NOP ... NOP               ; no-op

The shared ``[0x00198120] = 1/30`` constant has EXACTLY ONE
reader (the site above), so this NOP-out cannot collaterally
affect any other frame-time consumer.

## Side effects

- The "out of fuel" audio cue + UI flash
  (``FUN_000837A0``) never fires.  Since the player never
  runs out, that's the intended UX.
- Fuel HUD bars render whatever ``fuel_current`` last
  contained.  Because we skip both consumption paths, the
  bar stays at its starting value forever — which looks
  correct.
- Fuel pickup gems still increment ``fuel_current``.  They
  just become pointless.
- No interaction with ``fuel_increase`` / ``fuel-cap`` gem
  systems; those run elsewhere and are untouched.

## Open follow-up

If a user still sees fuel drain with v2, there's likely a
THIRD drain path (perhaps inside the attack-cast code we
haven't pinned yet — ``config/attacks_anims`` row reads
``"Fuel multiplier"`` into ``attack_struct[+0x14]`` which is
consumed SOMEWHERE at attack fire).  Report + we'll hunt it.

## Safety

Orthogonal to every other pack.  Both writes are contained
to short localized byte ranges in ``.text``.
"""

from __future__ import annotations

from azurik_mod.patching.registry import Feature, register_feature
from azurik_mod.patching.spec import PatchSpec


# Site 1: FUN_000842D0 prologue (wing-flap / event-driven
# fuel-consume function).
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


# Site 2: per-frame fuel-drain block inside FUN_00083D80
# (the armor/state tick).  v2 (late April 2026) — covers the
# sustained drain that FUN_000842D0 prologue patch missed.
AZURIK_PER_FRAME_DRAIN_VA = 0x00083DE3

PER_FRAME_DRAIN_SPEC = PatchSpec(
    label="FUN_00083D80 sustained fuel drain → NOP",
    va=AZURIK_PER_FRAME_DRAIN_VA,
    # Vanilla: FLD [0x00198120] ; FDIV [ECX+0x34] ;
    #          FSUBR [ESI+0x24] ; FSTP [ESI+0x24]
    # = 6 + 3 + 3 + 3 = 15 bytes.
    original=bytes.fromhex(
        "d90520811900"   # FLD  [0x00198120]  (1/30)
        "d87134"          # FDIV [ECX+0x34]
        "d86e24"          # FSUBR [ESI+0x24]
        "d95e24"          # FSTP [ESI+0x24]
    ),
    # 15 NOPs — FP stack delta of the original block is 0
    # (FLD pushes 1, FSTP pops 1), so replacing with NOPs
    # preserves FP stack state and skips the decrement.
    patch=bytes([0x90] * 15),
    is_data=False,
    safety_critical=False,
)


INFINITE_FUEL_SITES: list[PatchSpec] = [
    INFINITE_FUEL_SPEC,
    PER_FRAME_DRAIN_SPEC,
]


def apply_infinite_fuel_patch(xbe_data: bytearray) -> None:
    """Apply both infinite-fuel patch sites.

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
        "[BROKEN — prefer the config editor] Rewrites both the "
        "event-driven consumer (FUN_000842D0) and the per-frame "
        "sustained drain (FUN_00083D80 @ 0x83DE3).  User testing "
        "on 2026-04 confirms fuel still drains in-game — there is "
        "at least one more drain path (likely attack-cast fuel "
        "in `config/attacks_anims`).  Workaround: open the config "
        "editor → `armor_properties`, set `fuel_max` to a very "
        "large number (e.g. 1e6), or set every per-attack "
        "`Fuel multiplier` in `attacks_anims` to 0."
    ),
    sites=INFINITE_FUEL_SITES,
    apply=apply_infinite_fuel_patch,
    default_on=False,
    included_in_randomizer_qol=False,
    category="player",
    tags=("cheat", "powers", "broken"),
))


__all__ = [
    "AZURIK_CONSUME_FUEL_VA",
    "AZURIK_PER_FRAME_DRAIN_VA",
    "INFINITE_FUEL_SITES",
    "INFINITE_FUEL_SPEC",
    "PER_FRAME_DRAIN_SPEC",
    "apply_infinite_fuel_patch",
]
