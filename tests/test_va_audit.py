"""Comprehensive VA audit — every address referenced by the shim
authoring surface must resolve correctly against the vanilla XBE.

This complements :mod:`tests.test_shim_authoring` (which pins struct
offsets via compile-time probes) and
:mod:`tests.test_vanilla_thunks` (which pins function mangling /
layout integration) by sweeping the full set of VA constants in one
place.  The audit runs purely against the on-disk XBE — no
compilation required — so it remains fast and always-on in CI.

Covered:

1. **VA-anchor bytes** — every ``AZURIK_*_VA`` constant declared in
   ``shims/include/azurik.h`` points at the byte pattern the header
   comment promises (or, for BSS slots, at the correct empty /
   zero-filled region).

2. **Vanilla function entries** — every VA in
   ``azurik_mod/patching/vanilla_symbols.py`` lands inside the .text
   section and starts with a recognised i386 function prologue
   byte.

3. **Anchor-section consistency** — each VA maps to the exact
   section the header's inline comment documents.

4. **Header-registry drift guard** — every ``_VA`` macro in
   ``azurik.h`` is covered by this audit (catches "defined but
   never checked" drift when someone adds a new anchor).
"""

from __future__ import annotations

import re
import struct
import sys
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.patching.vanilla_symbols import all_entries  # noqa: E402
from azurik_mod.patching.xbe import (  # noqa: E402
    parse_xbe_sections,
    va_to_file,
)

_VANILLA_XBE = (_REPO_ROOT.parent /
                "Azurik - Rise of Perathia (USA).xiso/default.xbe")
_AZURIK_HEADER = _REPO_ROOT / "shims/include/azurik.h"


def _load_xbe():
    return _VANILLA_XBE.read_bytes(), parse_xbe_sections(
        _VANILLA_XBE.read_bytes())[1]


def _section_of(secs, va: int):
    for s in secs:
        if s["vaddr"] <= va < s["vaddr"] + s["vsize"]:
            return s
    return None


def _read_bytes_for_va(xbe: bytes, secs, va: int, length: int = 16) -> tuple[bytes, str]:
    """Return ``(bytes, section_name)``.

    ``bytes`` is empty for VAs past ``raw_size`` (BSS-like regions)
    and up to ``length`` otherwise.
    """
    s = _section_of(secs, va)
    if s is None:
        return b"", "<unknown>"
    off = va_to_file(va)
    max_raw = s["raw_size"] - (off - s["raw_addr"])
    if max_raw <= 0:
        return b"", s["name"]
    return xbe[off:off + min(length, max_raw)], s["name"]


# --- Expected predicates per anchor -----------------------------------------
#
# Each entry maps the ``AZURIK_*_VA`` macro name to:
#   (va, expected_section, predicate_on_bytes, description)
#
# Predicate returns True iff the bytes at that VA match what the
# header claims.  ``None`` means BSS or file-offset (skip byte
# checking, just verify the VA is reachable).

_BSS = lambda b: b == b"" or b[:4] == b"\x00\x00\x00\x00"

ANCHOR_EXPECTATIONS: dict[str, tuple[int, str, object, str]] = {
    "AZURIK_GRAVITY_VA": (
        0x001980A8, ".rdata",
        lambda b: abs(struct.unpack("<f", b[:4])[0] - 9.8) < 1e-5,
        "f32 9.8 (world gravity)"),
    "AZURIK_SHARED_RUN_MULT_VA": (
        0x001A25BC, ".rdata",
        lambda b: struct.unpack("<f", b[:4])[0] == 3.0,
        "f32 3.0 (shared run-speed multiplier)"),
    "AZURIK_FLOAT_RUN_MULT_VA": (
        0x001A25BC, ".rdata",
        lambda b: struct.unpack("<f", b[:4])[0] == 3.0,
        "alias of SHARED_RUN_MULT — value must match"),
    "AZURIK_FLOAT_ZERO_VA": (
        0x001A2508, ".rdata",
        lambda b: struct.unpack("<f", b[:4])[0] == 0.0,
        "f32 0.0 (shared zero constant)"),
    "AZURIK_FLOAT_HALF_VA": (
        0x001A9C84, ".data",
        lambda b: struct.unpack("<f", b[:4])[0] == 0.5,
        "f32 0.5 (shared half constant)"),
    "AZURIK_FLOAT_ONE_VA": (
        0x001A9C88, ".data",
        lambda b: struct.unpack("<f", b[:4])[0] == 1.0,
        "f32 1.0 (shared one constant)"),
    "AZURIK_PLAYER_CHAR_NAME_VA": (
        0x0019EA68, ".rdata",
        lambda b: b.startswith(b"garret4\x00"),
        "null-terminated 'garret4' (default player character)"),
    "AZURIK_ACTIVE_PLAYER_INDEX_VA": (
        0x001A7AE4, ".data",
        lambda b: struct.unpack("<I", b[:4])[0] == 4,
        "u32 4 (initial: no controller active)"),
    "AZURIK_BOOT_STATE_VA": (
        0x001BF61C, ".data", _BSS,
        "BSS: boot-state machine current state"),
    "AZURIK_CONTROLLER_STATE_VA": (
        0x0037BE98, ".data", _BSS,
        "BSS: 18 floats + edge state per-controller"),
    "AZURIK_PLAYER_STATE_PTR_ARRAY_VA": (
        0x001BE314, ".data", _BSS,
        "BSS: 4 × 4-byte player-state pointer slots"),
    "AZURIK_ENTITY_REGISTRY_BEGIN_VA": (
        0x0038C1E4, ".data", _BSS,
        "BSS: Entity** begin (vector start)"),
    "AZURIK_ENTITY_REGISTRY_END_VA": (
        0x0038C1E8, ".data", _BSS,
        "BSS: Entity** end"),
    "AZURIK_ENTITY_REGISTRY_CAP_VA": (
        0x0038C1EC, ".data", _BSS,
        "BSS: Entity** capacity"),
    "AZURIK_MOVIE_CONTEXT_PTR_VA": (
        0x001BCDC8, ".data", _BSS,
        "BSS: PVOID movie context (0 when idle)"),
    "AZURIK_MOVIE_IDLE_FLAG_VA": (
        0x001BCDB4, ".data",
        lambda b: b == b"" or b[:1] == b"\x00",
        "BSS: u8 movie-idle flag"),
    "AZURIK_WALKING_STATE_FLAG_VA": (
        0x0037ADEC, ".data", _BSS,
        "BSS: player walking-state transition flag"),
    # --- Added during the April 2026 index.xbr + selector.xbr pass ---
    "AZURIK_DEV_MENU_FLAG_VA": (
        0x001BCDD8, ".data", _BSS,
        "BSS: developer-menu gate flag (write non-0xFFFFFFFF "
        "to force-load selector.xbr cheat hub)"),
    "AZURIK_STR_LEVELS_SELECTOR_VA": (
        0x001A1E3C, ".rdata",
        lambda b: b.startswith(b"levels/selector\x00"),
        "ASCII 'levels/selector\\0' portal path"),
    "AZURIK_STR_LEVELS_TRAINING_VA": (
        # Test reads only the first 16 bytes at a VA; the full
        # string is 21 bytes ("levels/training_room\0") so we
        # check a 16-byte prefix instead.
        0x001A1E4C, ".rdata",
        lambda b: b.startswith(b"levels/training_"),
        "ASCII 'levels/training_room\\0' portal path "
        "(prefix-checked due to 16-byte read window)"),
    "AZURIK_STR_INDEX_XBR_PATH_VA": (
        0x0019ADB0, ".rdata",
        lambda b: b.startswith(b"index\\index.xbr\x00"),
        "ASCII 'index\\\\index.xbr\\0' loader path"),
}


# Header macros whose value is NOT a virtual address — file
# offsets, struct strides, IEEE-754 bit patterns, etc.  Excluded
# from the byte-level audit (they're sanity-checked elsewhere or
# by their usage sites).
_NON_VA_CONSTANTS = frozenset({
    "AZURIK_PLAYER_CHAR_NAME_FILE_OFF",  # file offset, not VA
    "AZURIK_CONTROLLER_STRIDE",          # sizeof(ControllerState)
    "AZURIK_SIM_DT_SECONDS_BITS",        # f32 bit pattern of 1/30
})


@unittest.skipUnless(_VANILLA_XBE.exists(),
    f"vanilla XBE required at {_VANILLA_XBE}")
class VaAnchorAudit(unittest.TestCase):
    """Every ``AZURIK_*_VA`` anchor in azurik.h points where it claims."""

    @classmethod
    def setUpClass(cls):
        cls.xbe, cls.secs = _load_xbe()

    def test_every_anchor_bytes_match_expectation(self):
        for name, (va, want_sec, pred, desc) in ANCHOR_EXPECTATIONS.items():
            with self.subTest(anchor=name, va=f"0x{va:X}"):
                data, got_sec = _read_bytes_for_va(self.xbe, self.secs, va)
                self.assertEqual(
                    got_sec, want_sec,
                    msg=f"{name} (VA 0x{va:X}) resolves to section "
                        f"{got_sec!r}, expected {want_sec!r}.  Either "
                        f"the header's VA is wrong or the vanilla XBE "
                        f"has shifted sections.")
                self.assertTrue(
                    pred(data),
                    msg=f"{name} (VA 0x{va:X}, section={got_sec}) "
                        f"bytes {data.hex()!r} do not match expectation "
                        f"({desc}).")


@unittest.skipUnless(_VANILLA_XBE.exists(),
    f"vanilla XBE required at {_VANILLA_XBE}")
class VanillaFunctionAudit(unittest.TestCase):
    """Every vanilla-symbol VA lands inside .text with a valid prologue."""

    # Common i386 function-prologue opening bytes on MSVC-compiled code:
    #   55              PUSH EBP          (classic ebp-frame prologue)
    #   53 / 56 / 57    PUSH EBX / ESI / EDI
    #   83 EC xx        SUB ESP, imm8
    #   81 EC xx xx xx  SUB ESP, imm32
    #   8B FF           MOV EDI, EDI      (hot-patch pad)
    #   A0 xx xx xx xx  MOV AL, [abs32]   (poll_movie starts this way)
    #   A1 xx xx xx xx  MOV EAX, [abs32]
    #   8B 0D / 8B 15   MOV ECX / EDX, [abs32]  (various globals-first prologues)
    #   D9 xx           FLD ...           (float prologue, gravity_integrate_raw)
    # We allow any of these as the first byte.
    _VALID_PROLOGUE_FIRSTBYTES = {
        0x55, 0x53, 0x56, 0x57,              # PUSH EBP/EBX/ESI/EDI
        0x83, 0x81,                           # SUB ESP, imm
        0x8B,                                 # MOV r32, r/m32 (global load)
        0x8A,                                 # MOV r8, r/m8 (thiscall flag-
                                              # check prologue, e.g.
                                              # calculate_save_signature)
        0xA0, 0xA1,                           # MOV AL/EAX, [abs32]
        0x33,                                 # XOR r32, r32 (e.g. XOR EAX,EAX)
        0xD9,                                 # FLD ... (float-first prologue)
        0x6A, 0x68,                           # PUSH imm8 / imm32
        # PE-COFF import thunks + kernel-variant prologues:
        0xFF,   # FF 25 <abs32>  = JMP [abs]       (import thunk)
                # FF 74 24 04    = PUSH [ESP+4]    (arg-shuffle wrapper)
        0x64,   # 64 0F B6 05 ...  = MOVZX EAX, [FS:abs]
                # (TIB-access prologue, e.g. GetLastError / SetLastError)
    }

    @classmethod
    def setUpClass(cls):
        cls.xbe, cls.secs = _load_xbe()
        cls.text_sec = next(s for s in cls.secs if s["name"] == ".text")

    def test_every_vanilla_symbol_lives_in_text(self):
        for sym in all_entries():
            with self.subTest(name=sym.name, va=f"0x{sym.va:X}"):
                ts = self.text_sec
                in_text = ts["vaddr"] <= sym.va < ts["vaddr"] + ts["vsize"]
                self.assertTrue(
                    in_text,
                    msg=f"vanilla symbol {sym.name!r} VA 0x{sym.va:X} "
                        f"is outside .text (section range 0x{ts['vaddr']:X}"
                        f"..0x{ts['vaddr']+ts['vsize']:X})")

    def test_every_vanilla_symbol_has_valid_prologue_byte(self):
        for sym in all_entries():
            with self.subTest(name=sym.name, va=f"0x{sym.va:X}"):
                off = va_to_file(sym.va)
                first = self.xbe[off]
                self.assertIn(
                    first, self._VALID_PROLOGUE_FIRSTBYTES,
                    msg=f"vanilla symbol {sym.name!r} (VA 0x{sym.va:X}) "
                        f"first byte 0x{first:02X} isn't a recognised "
                        f"function-prologue opener.  Either the VA is "
                        f"wrong or this function has an exotic prologue "
                        f"— extend _VALID_PROLOGUE_FIRSTBYTES after "
                        f"confirming via Ghidra.")


class AnchorCoverageDrift(unittest.TestCase):
    """Every ``AZURIK_*_VA`` / ``AZURIK_*`` constant in the header must
    be covered by this audit — catches "declared but never validated"
    drift when someone adds a new anchor."""

    def test_every_va_macro_is_covered(self):
        if not _AZURIK_HEADER.exists():
            self.skipTest(f"header missing at {_AZURIK_HEADER}")
        text = _AZURIK_HEADER.read_text()

        # Match `#define AZURIK_FOO_VA  0x...u?`.  We only capture the
        # macro NAME; the value doesn't matter for the drift check.
        pattern = re.compile(
            r"^\s*#define\s+(AZURIK_[A-Z0-9_]+)\s+0x[0-9A-Fa-f]+u?\b",
            re.MULTILINE,
        )
        declared = set(pattern.findall(text))
        # Strip non-VA constants (file offsets, strides) which don't
        # need byte-level auditing.
        declared -= _NON_VA_CONSTANTS

        covered = set(ANCHOR_EXPECTATIONS.keys())
        missing = declared - covered
        extra   = covered - declared

        self.assertFalse(
            missing,
            msg=f"anchors declared in azurik.h but NOT audited by "
                f"ANCHOR_EXPECTATIONS: {sorted(missing)}.  Add each "
                f"one to ANCHOR_EXPECTATIONS above with its expected "
                f"section + byte predicate.")
        self.assertFalse(
            extra,
            msg=f"ANCHOR_EXPECTATIONS references macros that no "
                f"longer exist in azurik.h: {sorted(extra)}.  Remove "
                f"stale entries.")

    def test_header_declares_non_va_constants(self):
        """Sanity: the file offset + stride constants we exclude from
        the audit still exist in the header (otherwise _NON_VA_CONSTANTS
        is carrying a stale name)."""
        if not _AZURIK_HEADER.exists():
            self.skipTest(f"header missing at {_AZURIK_HEADER}")
        text = _AZURIK_HEADER.read_text()
        for name in _NON_VA_CONSTANTS:
            self.assertIn(
                name, text,
                msg=f"_NON_VA_CONSTANTS mentions {name!r} but it is "
                    f"not declared in azurik.h — remove from the "
                    f"exclusion set.")


if __name__ == "__main__":
    unittest.main()
