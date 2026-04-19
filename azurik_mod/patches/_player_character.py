"""Player-character model-name swap helper (non-pack).

Not registered as a :class:`Feature` because the CLI surface
(``--player-character NAME``) takes a string, not a boolean toggle or
a float slider, so it doesn't fit the pack / slider model.  Invoked
imperatively by ``cmd_randomize_full`` when the flag is present.

Lives in ``azurik_mod/patches/`` next to the real feature folders so
its relationship to the other XBE mutations is obvious to anyone
browsing the repo.
"""

from __future__ import annotations


# FILE offset of the 'garret4\0d:\\\0' string in default.xbe.  Real
# VA is 0x0019EA68 (see azurik.h::AZURIK_PLAYER_CHAR_NAME_VA); the
# offset below is what ``xbe_data[start:end]`` indexing expects.  Don't
# pass this value to va_to_file — it IS already the file offset.
PLAYER_CHAR_OFFSET = 0x1976C8
PLAYER_CHAR_VA = 0x0019EA68
PLAYER_CHAR_ORIGINAL = bytes([
    0x67, 0x61, 0x72, 0x72, 0x65, 0x74, 0x34, 0x00,  # "garret4\0"
    0x64, 0x3A, 0x5C, 0x00,                          # "d:\\\0"
])
PLAYER_CHAR_MAX_LEN = 11  # max chars (12 bytes with null)


def apply_player_character_patch(xbe_data: bytearray, player_char: str) -> None:
    """Swap the ``"garret4"`` model-name string for an arbitrary
    ≤11-char ASCII name.

    Experimental: the target model must share garret4's skeleton or
    animation will look wrong.  Safe no-op when the source bytes have
    already been swapped or don't match the vanilla pattern.
    """
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
