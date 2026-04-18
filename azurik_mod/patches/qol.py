"""Quality-of-life XBE patches for Azurik.

Each QoL tweak is registered as its own independent pack so the GUI's
Patches page can toggle them individually.  None are enabled by
default — the user opts into whichever they want.

Current packs:

- ``qol_gem_popups``    — skip the first-time "Collect 100 <gem>" popup
- ``qol_pickup_anims``  — skip the item-pickup celebration animation
- ``qol_other_popups``  — skip the tutorial / key / health / power-up
                          first-time popups (the death-screen "gameover"
                          popup is deliberately excluded)
- ``qol_skip_logo``     — skip the unskippable Adrenium logo cutscene
                          that plays when the game first boots

Popup-suppression mechanism
---------------------------
The popup system looks up its message by a localisation resource key
like ``loc/english/popups/diamonds``.  We null the FIRST byte of that
key string in .rdata, turning it into an empty string; the resource
lookup fails silently and the popup never renders.  The actual popup
text (e.g. "Collect 100 Diamonds") lives in a localisation ``.xbr``
referenced by the key, not in ``default.xbe``, so searching the XBE
for the popup body itself will turn up nothing.

Plus a non-pack helper:

- ``apply_player_character_patch`` — swap the player's model name from
  "garret4" to an arbitrary ≤11-char ASCII string (exposed via the
  ``--player-character`` CLI flag; no GUI surface yet).
"""

from __future__ import annotations

import os
from pathlib import Path

from azurik_mod.patching import (
    PatchSpec,
    TrampolinePatch,
    apply_patch_spec,
    apply_trampoline_patch,
)
from azurik_mod.patching.registry import PatchPack, register_pack

# Resolve the shim directory relative to this file so apply-at-runtime
# works regardless of where the user invoked the CLI from.
_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Shared helper — null the first byte of a list of resource-key paths.
# ---------------------------------------------------------------------------
def _null_resource_keys(
    xbe_data: bytearray,
    offsets: list[int],
    label: str,
) -> None:
    """Break the localisation lookup for every resource key in ``offsets``.

    Each offset is expected to point at the first byte of a
    null-terminated ASCII string like ``loc/english/popups/<name>``.
    Replacing the first byte with ``0x00`` turns the path into an empty
    string, the game's resource lookup fails silently, and the
    associated popup / tutorial never renders.

    Callers pass printable-ASCII-typed offsets; non-printable bytes are
    treated as "wrong target" and reported as a warning rather than
    blindly overwritten.
    """
    patched, skipped = 0, 0
    for off in offsets:
        if off >= len(xbe_data):
            print(f"  WARNING: {label} offset 0x{off:X} out of range")
            skipped += 1
        elif xbe_data[off] == 0x00:
            patched += 1  # already nulled from a previous run
        elif 0x20 <= xbe_data[off] <= 0x7E:
            xbe_data[off] = 0x00
            patched += 1
        else:
            print(f"  WARNING: {label} byte at 0x{off:X} is 0x{xbe_data[off]:02X} "
                  f"(expected printable ASCII), skipping")
            skipped += 1
    print(f"  Suppressed {patched} {label}"
          + (f"  [{skipped} warnings]" if skipped else ""))


# ---------------------------------------------------------------------------
# Pack 1 — skip the gem first-pickup popup
# ---------------------------------------------------------------------------
# File offsets of each gem popup's resource-key string in .rdata.  In the
# USA XBE the five keys are:
#   0x1977D8  loc/english/popups/collect_obsidians
#   0x197800  loc/english/popups/sapphires
#   0x197820  loc/english/popups/rubies
#   0x19783C  loc/english/popups/diamonds
#   0x197858  loc/english/popups/emeralds
# The in-game message is "Collect 100 <gem>" the first time you pick
# up each gem type; nulling the key's first byte makes the resource
# lookup fail so the message never renders.  Imperative patch
# (variable-length strings, not a single PatchSpec), so this pack has
# no `sites` entries but still participates in the registry via its
# own apply function.
GEM_POPUP_OFFSETS = [0x197858, 0x19783C, 0x197820, 0x197800, 0x1977D8]


def apply_gem_popups_patch(xbe_data: bytearray) -> None:
    """Hide the first-time "Collect 100 <gem>" popup for every gem type."""
    _null_resource_keys(xbe_data, GEM_POPUP_OFFSETS, "gem pickup popups")


# ---------------------------------------------------------------------------
# Pack 3 — skip the remaining first-time / milestone popups
# ---------------------------------------------------------------------------
# Same resource-key nulling mechanism as qol_gem_popups, applied to the
# other nine popup keys in the XBE.  The .rdata keys are:
#   0x194A78  loc/english/popups/swim              — first-swim tutorial
#   0x197760  loc/english/popups/6keys             — six-keys milestone
#   0x19777C  loc/english/popups/key               — first key pickup
#   0x197794  loc/english/popups/chromatic_powerup
#   0x1977BC  loc/english/popups/health            — first health pickup
#   0x197874  loc/english/popups/water_powerup
#   0x197898  loc/english/popups/fire_powerup
#   0x1978B8  loc/english/popups/air_powerup
#   0x1978D8  loc/english/popups/earth_powerup
#
# IMPORTANT: 0x194910 (loc/english/popups/gameover) is NOT in this
# list.  That key drives the death-screen message, not a pickup popup;
# nulling it would make the player die silently and likely confuse
# anyone who then can't work out why they got kicked to the menu.
OTHER_POPUP_OFFSETS = [
    0x194A78,  # swim              — first-swim tutorial
    0x197760,  # 6keys             — all six keys collected milestone
    0x19777C,  # key               — first key pickup
    0x197794,  # chromatic_powerup
    0x1977BC,  # health            — first health pickup
    0x197874,  # water_powerup
    0x197898,  # fire_powerup
    0x1978B8,  # air_powerup
    0x1978D8,  # earth_powerup
]


def apply_other_popups_patch(xbe_data: bytearray) -> None:
    """Hide the remaining first-time / tutorial / milestone popups.

    Covers the swim tutorial, the "all six keys collected" milestone,
    first-time key / health pickups, and the first pickup of each
    elemental / chromatic power-up.  Leaves the death-screen popup
    alone (see OTHER_POPUP_OFFSETS for why).
    """
    _null_resource_keys(xbe_data, OTHER_POPUP_OFFSETS, "tutorial / pickup popups")


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
# Pack 4 — skip the AdreniumLogo boot movie
# ---------------------------------------------------------------------------
# The boot sequence in sim.cpp (strings at 0x196D80 confirm the source
# file) plays two BINK movies before reaching the title screen:
#
#   VA 0x05F6E0   PUSH &"AdreniumLogo.bik"   ; 68 50 E1 19 00
#   VA 0x05F6E5   CALL play_movie_fn         ; E8 96 92 FB FF
#   ... 0x5A bytes of other work ...
#   VA 0x05F73F   PUSH &"prophecy.bik"       ; same pattern
#
# The logo is unskippable in-game (player input isn't polled during
# boot), so we NOP the 10-byte PUSH+CALL pair.  The CALL is
# fire-and-forget (its return value is discarded), and the PUSH is
# balanced by the CALL's RET, so replacing both with NOPs leaves the
# stack and every subsequent instruction identical — control just
# falls through to the prophecy.bik call at 0x05F73F.
#
# We DO NOT touch prophecy.bik; that cutscene has plot content the
# user may want.  A parallel qol_skip_prophecy pack would be trivial
# to add later using the same mechanism.
# The AdreniumLogo call lives inside a boot-time state machine at
# FUN_0005f620.  The surrounding instructions matter — case 1 of the
# switch reads the __stdcall return value (AL) to decide whether to
# enter the movie-polling state (AL != 0) or skip straight to the
# next movie (AL == 0):
#
#   0x05f6df: PUSH EBP          ; EBP = 0 (scratch zero)
#   0x05f6e0: PUSH 0x0019e150   ; &"AdreniumLogo.bik"
#   0x05f6e5: CALL play_movie_fn
#   0x05f6ea: NEG AL            ; state = 2 (POLL) if AL != 0
#             SBB EAX, EAX      ; state = 3 (skip) if AL == 0
#             ADD EAX, 3
#             MOV [state], EAX
#
# `play_movie_fn` is __stdcall — the callee pops its 8 bytes of args
# via `ret 8`.  Any replacement has to preserve BOTH the AL contract
# (0 = skip to next movie) AND the stack-cleanup contract (pop 8B).
# Naively NOP-ing the PUSH+CALL pair breaks both: AL is left as
# garbage from whatever function ran previously (so state drifts
# into polling a movie that never started), and PUSH EBP leaks 4
# bytes onto the stack every iteration.  That produces a black-
# screen hang at boot.
#
# The correct replacement (both flavours below) sets AL = 0, pops
# the pre-pushed args, and lets case 1 of the state machine cleanly
# advance to case 3 (which starts prophecy.bik normally).

# Legacy byte patch — rewrites the 10-byte `PUSH imm32; CALL rel32`
# as `ADD ESP, 4; XOR AL, AL; NOP x5`.  ADD ESP, 4 pops the 4 bytes
# pushed by `PUSH EBP` at 0x05f6df (which is NOT in our 10-byte
# window and still runs); the original 4-byte `PUSH 0x0019e150`
# that WAS in our window is replaced, so only EBP's push needs
# cleanup.  XOR AL, AL drives the state machine to case 3.
SKIP_LOGO_SPEC = PatchSpec(
    label="Skip AdreniumLogo startup movie (legacy byte patch)",
    va=0x05F6E0,
    original=bytes([
        0x68, 0x50, 0xE1, 0x19, 0x00,   # PUSH 0x0019E150 (&"AdreniumLogo.bik")
        0xE8, 0x96, 0x92, 0xFB, 0xFF,   # CALL play_movie_fn (rel32)
    ]),
    patch=bytes([
        0x83, 0xC4, 0x04,               # ADD ESP, 4   ; pop PUSH EBP leftover
        0x30, 0xC0,                     # XOR AL, AL   ; state = 3 (skip)
        0x90, 0x90, 0x90, 0x90, 0x90,   # NOP x5
    ]),
)

# Phase 1 C-shim implementation.  Replaces ONLY the 5-byte CALL at
# 0x05f6e5 (the site of `CALL play_movie_fn`) with a CALL into
# shims/src/skip_logo.c (which is a naked `XOR AL,AL; RET 8`).  The
# original `PUSH EBP; PUSH 0x0019e150` instructions are left intact
# so the shim receives the two __stdcall args on its stack and can
# clean them up the same way the real callee would.
SKIP_LOGO_TRAMPOLINE = TrampolinePatch(
    name="skip_logo",
    label="Skip AdreniumLogo startup movie (C shim)",
    va=0x05F6E5,
    replaced_bytes=bytes([
        0xE8, 0x96, 0x92, 0xFB, 0xFF,   # CALL play_movie_fn (rel32)
    ]),
    shim_object=Path("shims/build/skip_logo.o"),
    shim_symbol="_c_skip_logo",
    mode="call",
)


def apply_skip_logo_patch(xbe_data: bytearray) -> None:
    """Skip the unskippable AdreniumLogo boot cutscene.

    Default path is the C-shim trampoline; setting the environment
    variable ``AZURIK_SKIP_LOGO_LEGACY=1`` falls back to the byte-level
    NOP patch.  The legacy form is kept as an insurance policy — if a
    shim compile fails on an unexpected toolchain or a PE-COFF parse
    bug surfaces, users can still ship the original behaviour.
    """
    if os.environ.get("AZURIK_SKIP_LOGO_LEGACY", "").strip() in ("1", "true", "yes"):
        apply_patch_spec(xbe_data, SKIP_LOGO_SPEC)
        return

    shim_path = _REPO_ROOT / SKIP_LOGO_TRAMPOLINE.shim_object
    if not shim_path.exists():
        # Shim isn't built yet.  Rather than silently fall back to the
        # legacy byte-NOP patch (which would hide build-time issues in
        # the GUI / CI), we print a clear error and leave the XBE
        # untouched.  The caller can then either run
        # `shims/toolchain/compile.sh shims/src/skip_logo.c` or set
        # AZURIK_SKIP_LOGO_LEGACY=1 to keep the old behaviour.
        print(f"  ERROR: Skip logo — shim object not found at {shim_path}")
        print(f"         Run shims/toolchain/compile.sh shims/src/skip_logo.c")
        print(f"         or set AZURIK_SKIP_LOGO_LEGACY=1 to use the "
              f"byte-NOP implementation.")
        return

    apply_trampoline_patch(xbe_data, SKIP_LOGO_TRAMPOLINE, repo_root=_REPO_ROOT)


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
"""All QoL PatchSpec sites (for legacy iteration).  The gem-popup and
other-popup suppressors are imperative byte-nulls, not PatchSpec; the
skip-logo site is now a TrampolinePatch (SKIP_LOGO_TRAMPOLINE).  The
legacy ``SKIP_LOGO_SPEC`` byte-NOP form is kept in this module for the
``AZURIK_SKIP_LOGO_LEGACY=1`` escape hatch but is not enumerated here."""


register_pack(PatchPack(
    name="qol_gem_popups",
    description="Hides first-time gem-collect popups (all 5 gem types).",
    sites=[],
    apply=apply_gem_popups_patch,
    default_on=False,
    included_in_randomizer_qol=False,
    tags=("qol",),
    # Every offset is a single-byte null into a resource-key string.
    # Declare them so verify-patches --strict whitelist-diff stays clean.
    extra_whitelist_ranges=tuple((off, off + 1) for off in GEM_POPUP_OFFSETS),
))

register_pack(PatchPack(
    name="qol_pickup_anims",
    description="Skips the post-pickup celebration animation.",
    sites=[PICKUP_ANIM_SPEC],
    apply=apply_pickup_anim_patch,
    default_on=False,
    included_in_randomizer_qol=False,
    tags=("qol",),
))

register_pack(PatchPack(
    name="qol_other_popups",
    description=(
        "Hides first-time tutorial, key, health, and power-up popups "
        "(death-screen popup is left alone)."
    ),
    sites=[],
    apply=apply_other_popups_patch,
    default_on=False,
    included_in_randomizer_qol=False,
    tags=("qol",),
    # Same single-byte-null mechanism as qol_gem_popups.
    extra_whitelist_ranges=tuple((off, off + 1) for off in OTHER_POPUP_OFFSETS),
))

register_pack(PatchPack(
    name="qol_skip_logo",
    description=(
        "Skips the unskippable Adrenium logo at boot (prophecy intro "
        "still plays).  Implemented via a C shim — see docs/SHIMS.md."
    ),
    sites=[SKIP_LOGO_TRAMPOLINE],
    apply=apply_skip_logo_patch,
    default_on=False,
    included_in_randomizer_qol=False,
    tags=("qol", "c-shim"),
))


# ---------------------------------------------------------------------------
# Back-compat shim for the CLI's old combined --no-qol / --no-gem-popups /
# --no-pickup-anim flags.  `cmd_randomize_full` prefers the new opt-in
# flags but still accepts the old namespace for existing scripts.
# ---------------------------------------------------------------------------

def apply_qol_patches(xbe_data: bytearray, args) -> None:
    """Back-compat dispatcher for callers that still pass an argparse namespace.

    Honours three flag styles, in order of precedence:
      * opt-in   : `--gem-popups` / `--other-popups` / `--pickup-anims`
                   / `--skip-logo`
      * opt-out  : `--no-gem-popups` / `--no-other-popups`
                   / `--no-pickup-anim` / `--no-skip-logo`
      * grouped  : `--no-qol` (disables every QoL pack)
    """
    group_off = bool(getattr(args, "no_qol", False))

    if getattr(args, "gem_popups", False):
        apply_gem_popups_patch(xbe_data)
    elif not group_off and not getattr(args, "no_gem_popups", False):
        # Legacy default behaviour only kicks in when the old flags are
        # used; the new opt-in style leaves this branch unused.
        pass

    if getattr(args, "other_popups", False):
        apply_other_popups_patch(xbe_data)
    elif not group_off and not getattr(args, "no_other_popups", False):
        pass

    if getattr(args, "pickup_anims", False):
        apply_pickup_anim_patch(xbe_data)
    elif not group_off and not getattr(args, "no_pickup_anim", False):
        pass

    if getattr(args, "skip_logo", False):
        apply_skip_logo_patch(xbe_data)
    elif not group_off and not getattr(args, "no_skip_logo", False):
        pass

    player_char = getattr(args, "player_character", None)
    if player_char:
        apply_player_character_patch(xbe_data, player_char)
