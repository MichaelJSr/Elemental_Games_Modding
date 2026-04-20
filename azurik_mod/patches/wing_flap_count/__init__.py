"""wing_flap_count — per-air-power-level wing-flap count.

## Background

Azurik's Air power grants the player a mid-air wing-flap (hold
WHITE + press JUMP airborne).  The number of flaps per jump is
gated by ``armor.flap_count`` — a runtime field read from
``config/armor_properties.tabl``'s ``"Flaps"`` column at game
start.  Vanilla values:

    air_power_level 1  →  1 flap
    air_power_level 2  →  2 flaps
    air_power_level 3  →  5 flaps

Inside ``FUN_00089300`` (the wing-flap handler) the counter
check is:

.. code-block:: asm

    00089321  8B 42 38           MOV  EAX, [EDX+0x38]   ; armor.flap_count
    00089324  85 C0              TEST EAX, EAX
    00089326  74 0E              JZ   0x89336           ; no flaps → abort
    00089328  39 86 D8 00 00 00  CMP  [ESI+0xD8], EAX   ; flaps_used vs flaps_max
    0008932E  7C 10              JL   0x89340           ; less → allow flap
    00089330  ...                                        ; ≥ → "out of flaps"

Each wing flap reads ``armor.flap_count`` fresh from ``[EDX+0x38]``
at VA 0x89321 — i.e., the value ISN'T cached in the entity.
That gives us a clean injection point: if we replace the
``MOV EAX, [EDX+0x38]`` with "pick the right value for the
current air-power level", each level can have its own flap
budget independently.

## How it works

The current air-power level is a global ``int`` at VA
``0x001A7AE4`` (referred to as ``DAT_001A7AE4`` in the decomp;
value is 1 / 2 / 3 / 4 where 4 = "no air power").  A 5-byte
``JMP rel32`` trampoline replaces the 5-byte ``MOV + TEST``
pair at VA 0x89321 and routes into a 50-byte shim landed via
``_carve_shim_landing``.  The shim:

1. Re-runs the vanilla ``MOV EAX, [EDX+0x38]`` (so level 4 /
   unrecognised levels keep vanilla behaviour).
2. Reads ``[0x001A7AE4]`` into EDX.
3. Dispatches on ``EDX ∈ {1, 2, 3}`` and overwrites EAX with
   the user-selected int constant for that level (loaded from
   one of three 4-byte values also landed by
   ``_carve_shim_landing``).
4. Replays the ``TEST EAX, EAX`` clobbered by the trampoline.
5. ``JMP``s back to VA 0x89326 (continuation point).

Three sliders drive the three injected integers:

    flaps_air_power_1  (default 1, range 0-99)
    flaps_air_power_2  (default 2, range 0-99)
    flaps_air_power_3  (default 5, range 0-99)

At defaults (1 / 2 / 5) the injection is byte-semantically
identical to vanilla and the slider is a no-op "confirm
vanilla" control.  Any non-default value trips the trampoline
and grants that many flaps per jump.

## Slider semantics

- ``0``: zero flaps — the air-power level has NO flaps (harsh
  but intentional; useful for speedrun challenges).
- ``1``: one flap (air-power-level-1 vanilla).
- ``99``: effectively infinite flaps for a mortal jumper.
  Higher values are accepted by the CLI but the GUI caps at 99.

## Independence

Each level's flaps are independent: setting ``air_1 = 10``
only affects air-power level 1 (when the player has the
level-1 armor set equipped).  Level 2 / 3 keep their own
values.  Setting all three to the same value is equivalent
to an "everyone gets N flaps" toggle.

## Side effects

- The ``armor.flap_count`` runtime field read from ``.tabl``
  at boot is still populated (we DON'T patch the .tabl loader
  — saves are untouched).  The shim just overrides the read
  at the one site that matters.
- Every other ``puVar1[0xE]`` reader (e.g. UI elements that
  display the flap count) would still show the vanilla
  ``Flaps`` value.  As of this writing we don't know of any
  such UI element in Azurik, so this is hypothetical.
- Orthogonal to every other player_physics slider and every
  QoL pack.

## Tests

See ``tests/test_wing_flap_count.py``.
"""

from __future__ import annotations

import struct

from azurik_mod.patching.registry import Feature
from azurik_mod.patching.spec import ParametricPatch


# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

# Entry point: MOV EAX, [EDX+0x38] ; TEST EAX, EAX (5 bytes we overwrite).
_WING_FLAP_HOOK_VA = 0x00089321
# MOV EAX, [EDX+0x38] ; TEST EAX, EAX.  Verified via Ghidra.
_WING_FLAP_HOOK_VANILLA = bytes.fromhex("8b423885c0")

# Return point: just after the TEST EAX, EAX that the shim replays.
_WING_FLAP_HOOK_RETURN_VA = 0x00089326

# The air-power level is NOT at a fixed VA.  It lives in the
# armor-subsystem struct chain at:
#
#     air_power_level = *(armor_mgr + 0x20 + 0x8)
#
# When execution reaches our hook at VA 0x89321:
#     - EDI  = armor_mgr pointer (set at 0x89318 MOV EDI, [ECX+0xCC])
#     - EDX  = [EDI+0x20] (set at 0x8931E) = level_struct pointer
# So reading the level is a single ``MOV reg, [EDX+0x8]`` inside
# our shim.  (Pre-commit 5ebf... we incorrectly dispatched on
# ``DAT_001A7AE4``, which turned out to be the active XInput
# *controller* index — not the air-power level.  Shim did fire
# but always hit the "fallback, use vanilla" branch because the
# controller index is 0-3 not 1-3 when the game is actually
# running.)


# ---------------------------------------------------------------------------
# Slider descriptors
# ---------------------------------------------------------------------------

def _encode_i32(v: float) -> bytes:
    """Encode a slider value as little-endian signed 32-bit int.

    Floats are floored; negative values are clamped to 0 (negative
    flap counts have no gameplay meaning — the CMP+JL logic would
    treat negative as "already out of flaps").
    """
    n = max(0, int(v))
    return struct.pack("<i", n)


def _decode_i32(b: bytes) -> float:
    return float(struct.unpack("<i", b)[0])


def _make_slider(name: str, label: str, default: int) -> ParametricPatch:
    return ParametricPatch(
        name=name,
        label=label,
        va=0,       # virtual — the pack's custom_apply resolves these
        size=0,
        original=b"",
        default=float(default),
        slider_min=0.0,
        slider_max=99.0,
        slider_step=1.0,
        unit="flap(s)",
        encode=_encode_i32,
        decode=_decode_i32,
    )


FLAPS_AIR_1 = _make_slider(
    "flaps_air_power_1", "Wing flaps — Air Power 1", 1)
FLAPS_AIR_2 = _make_slider(
    "flaps_air_power_2", "Wing flaps — Air Power 2", 2)
FLAPS_AIR_3 = _make_slider(
    "flaps_air_power_3", "Wing flaps — Air Power 3", 5)

WING_FLAP_COUNT_SITES: list[ParametricPatch] = [
    FLAPS_AIR_1, FLAPS_AIR_2, FLAPS_AIR_3,
]


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

_VANILLA_FLAPS = {1: 1, 2: 2, 3: 5}


def _build_shim_body(
    shim_va: int,
    flaps1_va: int,
    flaps2_va: int,
    flaps3_va: int,
) -> bytes:
    """Assemble the 47-byte dispatch shim.

    Layout (offsets within the shim body):

        0:  MOV EAX, [EDX+0x38]           (8B 42 38)      — replay vanilla
        3:  MOV EDX, [EDX+0x8]            (8B 52 08)      — load air_power_level
        6:  CMP EDX, 1                    (83 FA 01)
        9:  JNE +7                        (75 07)
       11:  MOV EAX, ds:[flaps1_va]       (A1 <abs>)
       16:  JMP +0x16 → done              (EB 16)
       18:  CMP EDX, 2                    (83 FA 02)
       21:  JNE +7                        (75 07)
       23:  MOV EAX, ds:[flaps2_va]       (A1 <abs>)
       28:  JMP +0x0A → done              (EB 0A)
       30:  CMP EDX, 3                    (83 FA 03)
       33:  JNE +5                        (75 05)
       35:  MOV EAX, ds:[flaps3_va]       (A1 <abs>)
       40:  TEST EAX, EAX                 (85 C0)         — replay clobber
       42:  JMP back_va                   (E9 <rel32>)

    Total = 47 bytes.

    At the hook point (VA 0x89321), the register state is:
        - EDI  = armor_mgr pointer
        - EDX  = *(armor_mgr + 0x20) = level_struct pointer
        - ESI  = player entity

    The shim:
        - Reads ``armor.flap_count`` via the replayed
          ``MOV EAX, [EDX+0x38]`` (vanilla baseline).
        - Reads ``air_power_level`` via ``MOV EDX, [EDX+0x8]``
          (level_struct.level, in [1, 3] when a level is active).
        - Dispatches on EDX ∈ {1, 2, 3}; falls through for any
          other value (level 0 / 4 / unset) using vanilla EAX.
        - Replays the ``TEST EAX, EAX`` that our 5-byte
          trampoline clobbered.
        - JMPs back to VA 0x89326 (right after the replayed
          TEST) so the ``JZ 0x89336`` conditional at 0x89326
          works against the correct flags.

    ``shim_va`` is the absolute VA where the shim body starts in
    the patched XBE; used to compute the final JMP's rel32.
    """
    parts: list[bytes] = []

    # 0-2: MOV EAX, [EDX+0x38] — vanilla flap count (replay)
    parts.append(b"\x8B\x42\x38")
    # 3-5: MOV EDX, [EDX+0x8] — air_power_level (overwrites EDX;
    # EDX is dead after VA 0x89326 so this is safe).
    parts.append(b"\x8B\x52\x08")
    # 6-8: CMP EDX, 1
    parts.append(b"\x83\xFA\x01")
    # 9-10: JNE +7 → skip use_1 load
    parts.append(b"\x75\x07")
    # 11-15: MOV EAX, ds:[flaps1_va]
    parts.append(b"\xA1" + struct.pack("<I", flaps1_va))
    # 16-17: JMP +0x16 → done (offset 18 + 0x16 = 40)
    parts.append(b"\xEB\x16")
    # 18-20: CMP EDX, 2
    parts.append(b"\x83\xFA\x02")
    # 21-22: JNE +7
    parts.append(b"\x75\x07")
    # 23-27: MOV EAX, ds:[flaps2_va]
    parts.append(b"\xA1" + struct.pack("<I", flaps2_va))
    # 28-29: JMP +0x0A → done (offset 30 + 0x0A = 40)
    parts.append(b"\xEB\x0A")
    # 30-32: CMP EDX, 3
    parts.append(b"\x83\xFA\x03")
    # 33-34: JNE +5 → skip use_3, fall through to done
    parts.append(b"\x75\x05")
    # 35-39: MOV EAX, ds:[flaps3_va]
    parts.append(b"\xA1" + struct.pack("<I", flaps3_va))
    # 40-41: TEST EAX, EAX (replay clobbered instruction)
    parts.append(b"\x85\xC0")
    # 42-46: JMP <rel32> back to 0x89326
    back_va = _WING_FLAP_HOOK_RETURN_VA
    jmp_origin_after = shim_va + 47   # after the 5-byte JMP at end of shim
    rel32 = back_va - jmp_origin_after
    parts.append(b"\xE9" + struct.pack("<i", rel32))

    body = b"".join(parts)
    assert len(body) == 47, f"expected 47-byte shim, got {len(body)}"
    return body


def apply_wing_flap_count(
    xbe_data: bytearray,
    *,
    flaps_air_power_1: int | None = None,
    flaps_air_power_2: int | None = None,
    flaps_air_power_3: int | None = None,
) -> bool:
    """Apply the wing-flap-count shim + injected int constants.

    Returns True on apply.  Returns False (and leaves the buffer
    untouched) when:

    - All three sliders are at their vanilla defaults (1 / 2 / 5).
    - The patch site at VA 0x89321 has drifted (warn + skip).
    """
    # Default to vanilla counts (1 / 2 / 5) so a partial spec
    # (e.g. user only cranked flaps_air_3) doesn't disturb the
    # others' counts.
    f1 = int(flaps_air_power_1) if flaps_air_power_1 is not None else 1
    f2 = int(flaps_air_power_2) if flaps_air_power_2 is not None else 2
    f3 = int(flaps_air_power_3) if flaps_air_power_3 is not None else 5

    if f1 == 1 and f2 == 2 and f3 == 5:
        return False

    from azurik_mod.patching.apply import _carve_shim_landing
    from azurik_mod.patching.xbe import va_to_file

    hook_off = va_to_file(_WING_FLAP_HOOK_VA)
    current = bytes(xbe_data[hook_off:hook_off + 5])
    if current != _WING_FLAP_HOOK_VANILLA:
        print(f"  WARNING: wing_flap_count — hook site at VA "
              f"0x{_WING_FLAP_HOOK_VA:X} drifted "
              f"(got {current.hex()}); skipping.  "
              f"Already applied?")
        return False

    # Carve THREE int32s in a SINGLE 16-byte allocation (12 bytes
    # of data + 4 bytes of 0xFF sentinel).  Two reasons for both
    # the packing AND the sentinel:
    #
    # 1. Packing: `_carve_shim_landing`'s first-fit strategy
    #    scans ``find_text_padding``, which walks back from
    #    raw_end skipping *zero* bytes.  After allocation N
    #    writes bytes with a trailing-zero suffix (e.g. int
    #    ``0A 00 00 00``), allocation N+1 mistakes those zeros
    #    for free padding and overwrites them.  Packing the 3
    #    ints keeps them atomic with respect to the allocator.
    # 2. Sentinel: even atomic packing isn't enough when the
    #    LAST int ends in zero bytes (e.g. ``32 00 00 00`` for
    #    count 50).  The next allocation's back-scan stops at
    #    the non-zero ``32`` — overwriting the three trailing
    #    zero bytes of that int.  A 4-byte ``0xFF FF FF FF``
    #    sentinel right after the int block makes the back-
    #    scan stop at the sentinel, protecting our ints.  The
    #    sentinel bytes themselves are never executed / read
    #    (no code references those 4 bytes).
    ints_block = struct.pack("<iii", f1, f2, f3) + b"\xFF\xFF\xFF\xFF"
    _, ints_va = _carve_shim_landing(xbe_data, ints_block)
    flaps1_va = ints_va
    flaps2_va = ints_va + 4
    flaps3_va = ints_va + 8

    # Carve a 47-byte placeholder for the shim body; we need its
    # VA before we can assemble the body (for the back-JMP rel32).
    # The placeholder is all ``0xCC`` (INT 3) so it doesn't look
    # like padding to the allocator AND a mis-directed jump into
    # it would trap immediately instead of corrupting state.
    placeholder = b"\xCC" * 47
    body_file_off, shim_va = _carve_shim_landing(xbe_data, placeholder)

    # Assemble the real shim body now that we know shim_va and
    # overwrite the placeholder IN PLACE using the file offset
    # returned by the allocator — we can't re-derive it from
    # ``va_to_file`` because the shim may have landed in a newly
    # appended section that the vanilla section map doesn't know
    # about.
    body = _build_shim_body(shim_va, flaps1_va, flaps2_va, flaps3_va)
    xbe_data[body_file_off:body_file_off + 47] = body

    # Install the 5-byte trampoline at the hook site.
    rel32 = shim_va - (_WING_FLAP_HOOK_VA + 5)
    trampoline = b"\xE9" + struct.pack("<i", rel32)
    xbe_data[hook_off:hook_off + 5] = trampoline

    print(f"  Wing flaps per air-power level: "
          f"L1={f1} L2={f2} L3={f3}  "
          f"(shim @ VA 0x{shim_va:X}, +47 bytes; "
          f"flaps VAs 0x{flaps1_va:X}/0x{flaps2_va:X}/"
          f"0x{flaps3_va:X})")
    return True


# ---------------------------------------------------------------------------
# Whitelist (for verify-patches --strict)
# ---------------------------------------------------------------------------

def _wing_flap_count_dynamic_whitelist(
    xbe: bytes,
) -> list[tuple[int, int]]:
    """Whitelist ranges for verify-patches --strict.

    Always whitelists the 5-byte trampoline slot at VA 0x89321.
    Follows the trampoline to the shim body when installed and
    whitelists the 50-byte body + its 3 × 4-byte injected int
    constants.
    """
    from azurik_mod.patching.xbe import parse_xbe_sections, va_to_file

    try:
        hook_off = va_to_file(_WING_FLAP_HOOK_VA)
    except Exception:  # noqa: BLE001
        return []

    ranges: list[tuple[int, int]] = [(hook_off, hook_off + 5)]

    from azurik_mod.patching.xbe import resolve_va_to_file

    # If a JMP trampoline is installed, follow it to the shim and
    # whitelist the shim body + referenced int constants.
    if len(xbe) >= hook_off + 5:
        patch = xbe[hook_off:hook_off + 5]
        if patch[:1] == b"\xE9":
            rel32 = struct.unpack("<i", patch[1:5])[0]
            shim_va = _WING_FLAP_HOOK_VA + 5 + rel32
            shim_off = resolve_va_to_file(xbe, shim_va)
            if shim_off is not None:
                ranges.append((shim_off, shim_off + 47))
                # Shim body parse: pull the 3 `A1 <abs32>` sites.
                # All three point into a single packed 12-byte
                # int block (flaps1_va, flaps2_va=+4, flaps3_va=+8),
                # so whitelisting the first VA + 12 covers all
                # three.  We still emit per-site ranges for
                # robustness (if future refactors split them).
                body = xbe[shim_off:shim_off + 47]
                for a1_offset in (11, 23, 35):
                    if (a1_offset + 5 <= len(body)
                            and body[a1_offset] == 0xA1):
                        inj_va = struct.unpack(
                            "<I",
                            body[a1_offset + 1:a1_offset + 5])[0]
                        inj_off = resolve_va_to_file(xbe, inj_va)
                        if inj_off is not None:
                            ranges.append((inj_off, inj_off + 4))

    return ranges


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _custom_apply(
    xbe_data: bytearray,
    flaps_air_power_1: float | None = None,
    flaps_air_power_2: float | None = None,
    flaps_air_power_3: float | None = None,
    **_extra,
) -> None:
    """Route parametric slider values into the XBE apply."""
    apply_wing_flap_count(
        xbe_data,
        flaps_air_power_1=(None if flaps_air_power_1 is None
                           else int(flaps_air_power_1)),
        flaps_air_power_2=(None if flaps_air_power_2 is None
                           else int(flaps_air_power_2)),
        flaps_air_power_3=(None if flaps_air_power_3 is None
                           else int(flaps_air_power_3)),
    )


_FEATURE_SPEC = Feature(
    name="wing_flap_count",
    description=(
        "[RETIRED] 47-byte dispatch shim that overrode "
        "per-air-power-level flap counts inside FUN_00089300. "
        "Shim applies cleanly but the game re-reads the count "
        "through a path we haven't traced.  Workaround: edit "
        "config.xbr → `armor_properties` → `Flaps` column per "
        "armor row; the game reads it fresh each flap."
    ),
    sites=WING_FLAP_COUNT_SITES,
    apply=lambda xbe_data: None,   # no-op; custom_apply is used
    default_on=False,
    included_in_randomizer_qol=False,
    category="player",
    tags=("cheat", "movement", "air-power", "retired"),
    dynamic_whitelist_from_xbe=_wing_flap_count_dynamic_whitelist,
    custom_apply=_custom_apply,
)
# Not registered — pack is retired.  Keep spec defined so tests
# covering byte landings still work.
FEATURE = _FEATURE_SPEC


__all__ = [
    "FLAPS_AIR_1",
    "FLAPS_AIR_2",
    "FLAPS_AIR_3",
    "WING_FLAP_COUNT_SITES",
    "_WING_FLAP_HOOK_RETURN_VA",
    "_WING_FLAP_HOOK_VA",
    "_WING_FLAP_HOOK_VANILLA",
    "_build_shim_body",
    "apply_wing_flap_count",
]
