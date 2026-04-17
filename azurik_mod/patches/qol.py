"""Quality-of-life XBE patches for Azurik.

Each QoL tweak is registered as its own independent pack so the GUI's
Patches page can toggle them individually.  None are enabled by
default — the user opts into whichever they want.

Current packs:

- ``qol_gem_popups``    — skip the first-time gem pickup message
- ``qol_pickup_anims``  — skip the item-pickup celebration animation

Plus a non-pack helper:

- ``apply_player_character_patch`` — swap the player's model name from
  "garret4" to an arbitrary ≤11-char ASCII string (exposed via the
  ``--player-character`` CLI flag; no GUI surface yet).
"""

from __future__ import annotations

from azurik_mod.patching import PatchSpec, apply_patch_spec
from azurik_mod.patching.registry import PatchPack, register_pack


# ---------------------------------------------------------------------------
# Pack 1 — skip the gem first-pickup popup
# ---------------------------------------------------------------------------
# File offsets of each popup string's first byte.  Nulling those bytes
# terminates the string before it reaches the renderer, so the popup
# silently no-ops.  Imperative patch (variable targets, not a single
# PatchSpec), so this pack has no `sites` entries but still participates
# in the registry via its own apply function.
GEM_POPUP_OFFSETS = [0x197858, 0x19783C, 0x197820, 0x197800, 0x1977D8]


def apply_gem_popups_patch(xbe_data: bytearray) -> None:
    """Suppress every "You found X for the first time!" gem message."""
    patched, skipped = 0, 0
    for off in GEM_POPUP_OFFSETS:
        if off >= len(xbe_data):
            print(f"  WARNING: Gem popup offset 0x{off:X} out of range")
            skipped += 1
        elif xbe_data[off] == 0x00:
            patched += 1  # already nulled from a previous run
        elif 0x20 <= xbe_data[off] <= 0x7E:
            xbe_data[off] = 0x00
            patched += 1
        else:
            print(f"  WARNING: Gem popup byte at 0x{off:X} is 0x{xbe_data[off]:02X} "
                  f"(expected printable ASCII), skipping")
            skipped += 1
    print(f"  Skipping first-pickup gem popups ({patched} strings)"
          + (f"  [{skipped} warnings]" if skipped else ""))


# ---------------------------------------------------------------------------
# Pack 2 — skip the pickup celebration animation
# ---------------------------------------------------------------------------
# Replaces the first instruction of the non-gem pickup handler's "play
# celebration animation" block with a JMP to its epilog.  The save list
# + collection flag updates still run, so picked-up items remain
# collected and the save file stays consistent.
PICKUP_ANIM_SPEC = PatchSpec(
    label="Skip pickup celebration animation",
    va=0x00413EE,
    original=bytes([0x8B, 0x8A, 0xEC, 0x01, 0x00]),   # MOV ECX,[EDX+0x1EC]
    patch=bytes([0xE9, 0x7C, 0x00, 0x00, 0x00]),       # JMP 0x4146F (epilog)
)


def apply_pickup_anim_patch(xbe_data: bytearray) -> None:
    """Replace the pickup celebration animation block with an early JMP."""
    apply_patch_spec(xbe_data, PICKUP_ANIM_SPEC)


# ---------------------------------------------------------------------------
# Standalone helper — player character model swap (no pack)
# ---------------------------------------------------------------------------
PLAYER_CHAR_OFFSET = 0x1976C8
PLAYER_CHAR_ORIGINAL = bytes([0x67, 0x61, 0x72, 0x72, 0x65, 0x74, 0x34, 0x00,
                               0x64, 0x3a, 0x5c, 0x00])  # "garret4\0d:\\0"
PLAYER_CHAR_MAX_LEN = 11  # max chars (12 bytes with null)


def apply_player_character_patch(xbe_data: bytearray, player_char: str) -> None:
    """Swap the "garret4" model-name string with an arbitrary ≤11-char name."""
    if not player_char.isascii():
        print(f"  WARNING: Player character name '{player_char}' contains "
              f"non-ASCII characters, skipping")
        return
    if len(player_char) > PLAYER_CHAR_MAX_LEN:
        print(f"  WARNING: Player character name '{player_char}' too long "
              f"(max {PLAYER_CHAR_MAX_LEN} chars), skipping")
        return
    if PLAYER_CHAR_OFFSET + 12 > len(xbe_data):
        print(f"  WARNING: Player char offset out of range, skipping")
        return
    current = bytes(xbe_data[PLAYER_CHAR_OFFSET:PLAYER_CHAR_OFFSET + 12])
    if current == PLAYER_CHAR_ORIGINAL:
        new_bytes = player_char.encode("ascii") + b"\x00"
        new_bytes = new_bytes + b"\x00" * (12 - len(new_bytes))
        xbe_data[PLAYER_CHAR_OFFSET:PLAYER_CHAR_OFFSET + 12] = new_bytes
        print(f"  Player character: garret4 -> {player_char} (EXPERIMENTAL)")
    else:
        print(f"  WARNING: Player char bytes don't match expected, skipping")


# ---------------------------------------------------------------------------
# Registry — every QoL pack is its own entry so the GUI can toggle them
# independently.  Keeping each scoped to a single behaviour means the
# user can describe exactly what they want without sub-checkbox trees.
# ---------------------------------------------------------------------------
QOL_PATCH_SITES: list[PatchSpec] = [PICKUP_ANIM_SPEC]
"""All QoL PatchSpec sites (for verify-patches iteration).  Gem popup
suppression is imperative byte-null and not a PatchSpec."""


register_pack(PatchPack(
    name="qol_gem_popups",
    description=(
        "Skip the \u201cYou found X for the first time!\u201d message that "
        "pops up the first time you collect each gem type."
    ),
    sites=[],
    apply=apply_gem_popups_patch,
    default_on=False,
    included_in_randomizer_qol=False,
    tags=("qol",),
))

register_pack(PatchPack(
    name="qol_pickup_anims",
    description=(
        "Skip the short celebration animation that plays after picking "
        "up an item.  Items still get collected normally."
    ),
    sites=[PICKUP_ANIM_SPEC],
    apply=apply_pickup_anim_patch,
    default_on=False,
    included_in_randomizer_qol=False,
    tags=("qol",),
))


# ---------------------------------------------------------------------------
# Back-compat shim for the CLI's old combined --no-qol / --no-gem-popups /
# --no-pickup-anim flags.  `cmd_randomize_full` prefers the new opt-in
# flags but still accepts the old namespace for existing scripts.
# ---------------------------------------------------------------------------

def apply_qol_patches(xbe_data: bytearray, args) -> None:
    """Back-compat dispatcher for callers that still pass an argparse namespace.

    Honours three flag styles, in order of precedence:
      * opt-in   : `--gem-popups` / `--pickup-anims` (preferred)
      * opt-out  : `--no-gem-popups` / `--no-pickup-anim` (legacy)
      * grouped  : `--no-qol` (legacy; disables everything)
    """
    group_off = bool(getattr(args, "no_qol", False))

    if getattr(args, "gem_popups", False):
        apply_gem_popups_patch(xbe_data)
    elif not group_off and not getattr(args, "no_gem_popups", False):
        # Legacy default behaviour only kicks in when the old flags are
        # used; the new opt-in style leaves this branch unused.
        pass

    if getattr(args, "pickup_anims", False):
        apply_pickup_anim_patch(xbe_data)
    elif not group_off and not getattr(args, "no_pickup_anim", False):
        pass

    player_char = getattr(args, "player_character", None)
    if player_char:
        apply_player_character_patch(xbe_data, player_char)
