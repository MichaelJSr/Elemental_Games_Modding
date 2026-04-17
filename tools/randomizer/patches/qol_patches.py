"""Quality-of-life XBE patches for Azurik (non-FPS)."""

from __future__ import annotations

from patches.xbe_utils import apply_xbe_patch

# ---------------------------------------------------------------------------
# Gem first-pickup popup suppression
# ---------------------------------------------------------------------------
# Null the first byte of each popup string to disable the message.
# file offsets into the XBE for the start of each gem popup string.
GEM_POPUP_OFFSETS = [0x197858, 0x19783C, 0x197820, 0x197800, 0x1977D8]

# ---------------------------------------------------------------------------
# Pickup celebration animation skip (unified)
# ---------------------------------------------------------------------------
# Disable all pickup celebration animations (file 0x0313EE, VA 0x0413EE).
# The pickup handler FUN_00041390 enters a block for non-gem pickups that:
#   (A) FUN_00061360  — sets "collected" flag, adds to save list
#   (B) virtual call  — FUN_0006FC90, decrements pickup counter (persistence)
#   (C) linked-list cleanup — zeroes [this+0x1EC/0x1F0]
#   (D) counter update — writes [[[this+0x154]+0xD8]+4]
# Steps C and D keep the pickup entity's animation data live, which its
# per-frame update (FUN_00037950 → FUN_00037AB0) reads to play anim 0x52
# (the celebration).  We replace the first instruction of step C with a JMP
# to the epilog (0x4146F), skipping C+D.  Steps A+B still run for persistence.
# Both null-check JZ branches (0x413CA, 0x413D1) already target 0x413EE,
# so they naturally hit our JMP — no other code changes needed.
#
# This unified patch supersedes the earlier OBSIDIAN_ANIM (first-obsidian
# only) and FIST_PUMP (per-pickup JE→JMP) patches and preserves save
# persistence where the older pair could sometimes drop state.
PICKUP_ANIM_OFFSET = 0x0313EE
PICKUP_ANIM_ORIGINAL = bytes([0x8B, 0x8A, 0xEC, 0x01, 0x00])  # MOV ECX,[EDX+0x1EC] (first 5 bytes)
PICKUP_ANIM_PATCH = bytes([0xE9, 0x7C, 0x00, 0x00, 0x00])     # JMP 0x4146F (epilog)

# ---------------------------------------------------------------------------
# Player character model swap
# ---------------------------------------------------------------------------
# At file offset 0x1976C8, "garret4\0d:\" = 12 bytes.
PLAYER_CHAR_OFFSET = 0x1976C8
PLAYER_CHAR_ORIGINAL = bytes([0x67, 0x61, 0x72, 0x72, 0x65, 0x74, 0x34, 0x00,
                               0x64, 0x3a, 0x5c, 0x00])  # "garret4\0d:\\0"
PLAYER_CHAR_MAX_LEN = 11  # max chars (12 bytes with null)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def apply_qol_patches(xbe_data: bytearray, args) -> None:
    """Apply non-FPS quality-of-life patches to the XBE data.

    Respects per-patch opt-out flags on args:
      --no-gem-popups  -> skip gem popup suppression
      --no-pickup-anim -> skip pickup celebration animation patch
    """
    if not getattr(args, 'no_gem_popups', False):
        patched, skipped = 0, 0
        for off in GEM_POPUP_OFFSETS:
            if off >= len(xbe_data):
                print(f"  WARNING: Gem popup offset 0x{off:X} out of range")
                skipped += 1
            elif xbe_data[off] == 0x00:
                patched += 1  # already applied
            elif 0x20 <= xbe_data[off] <= 0x7E:
                xbe_data[off] = 0x00
                patched += 1
            else:
                print(f"  WARNING: Gem popup byte at 0x{off:X} is 0x{xbe_data[off]:02X} "
                      f"(expected printable ASCII), skipping")
                skipped += 1
        print(f"  Disabled {patched} gem first-pickup popups"
              + (f" ({skipped} warnings)" if skipped else ""))
    else:
        print(f"  Gem popup patch — skipped")

    if not getattr(args, 'no_pickup_anim', False):
        apply_xbe_patch(xbe_data, "Disabled pickup celebration animations",
                        PICKUP_ANIM_OFFSET, PICKUP_ANIM_ORIGINAL, PICKUP_ANIM_PATCH)
    else:
        print(f"  Pickup celebration animation patch — skipped")

    player_char = getattr(args, 'player_character', None)
    if player_char:
        apply_player_character_patch(xbe_data, player_char)


def apply_player_character_patch(xbe_data: bytearray, player_char: str) -> None:
    """Apply player character model swap independently of QoL toggle."""
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
