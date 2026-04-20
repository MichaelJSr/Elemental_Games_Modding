"""Tests for the three new player-category packs:

- ``no_fall_damage`` — JNP → JMP branch flip at top of FUN_0008AB70
- ``infinite_fuel`` — MOV AL,1 ; RET 4 prologue rewrite of FUN_000842D0
- ``wing_flap_count`` — 3 per-air-power-level flap-count sliders
  backed by a trampoline + dispatch shim at VA 0x89321

Each pack is byte-identical to vanilla at default settings (no-op
when disabled / defaults), and each has a single-purpose effect
when enabled.
"""

from __future__ import annotations

import os
import struct
import sys
import unittest
from pathlib import Path

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from azurik_mod.patches.no_fall_damage import (  # noqa: E402
    NO_FALL_DAMAGE_SPEC,
    NO_FALL_DAMAGE_VA,
    apply_no_fall_damage_patch,
)
from azurik_mod.patches.infinite_fuel import (  # noqa: E402
    AZURIK_CONSUME_FUEL_VA,
    INFINITE_FUEL_SPEC,
    apply_infinite_fuel_patch,
)
from azurik_mod.patches.wing_flap_count import (  # noqa: E402
    FLAPS_AIR_1,
    FLAPS_AIR_2,
    FLAPS_AIR_3,
    _WING_FLAP_HOOK_RETURN_VA,
    _WING_FLAP_HOOK_VA,
    _WING_FLAP_HOOK_VANILLA,
    _build_shim_body,
    apply_wing_flap_count,
)
from azurik_mod.patching.xbe import parse_xbe_sections, va_to_file  # noqa: E402

_XBE_CANDIDATES = [
    Path("/Users/michaelsrouji/Documents/Xemu/tools/"
         "Azurik - Rise of Perathia (USA).xiso/default.xbe"),
    Path(_REPO_ROOT).parent /
        "Azurik - Rise of Perathia (USA).xiso" / "default.xbe",
    Path(_REPO_ROOT) / "tests" / "fixtures" / "default.xbe",
]
_XBE_PATH = next((p for p in _XBE_CANDIDATES if p.exists()), None)


# ---------------------------------------------------------------------------
# no_fall_damage
# ---------------------------------------------------------------------------

class NoFallDamageSpecShape(unittest.TestCase):
    """v2 (late April 2026) — rewrites FUN_0008AB70 prologue to
    ``XOR AL, AL ; RET 8 ; NOP`` (6-byte always-return-0).
    """

    def test_spec_bytes_are_correct_lengths(self):
        self.assertEqual(len(NO_FALL_DAMAGE_SPEC.original), 6)
        self.assertEqual(len(NO_FALL_DAMAGE_SPEC.patch), 6)

    def test_targets_function_prologue(self):
        """NO_FALL_DAMAGE_VA is FUN_0008AB70's entry, not the
        branch 0x8AC77 pre-v2 used."""
        self.assertEqual(NO_FALL_DAMAGE_VA, 0x0008AB70)

    def test_vanilla_is_push_ecx_mov_eax(self):
        # 51 A1 = PUSH ECX ; MOV EAX, abs32.
        self.assertEqual(NO_FALL_DAMAGE_SPEC.original[:2],
                         b"\x51\xA1")

    def test_patch_is_xor_ret8_nop(self):
        # 32 C0 = XOR AL, AL ; C2 08 00 = RET 8 ; 90 = NOP.
        self.assertEqual(
            NO_FALL_DAMAGE_SPEC.patch,
            b"\x32\xC0\xC2\x08\x00\x90")


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class NoFallDamageApply(unittest.TestCase):
    def setUp(self):
        self.orig = _XBE_PATH.read_bytes()

    def test_vanilla_bytes_match_ghidra(self):
        """Drift-guard: the 6 bytes at VA 0x0008AB70 must match
        what NO_FALL_DAMAGE_SPEC.original expects."""
        off = va_to_file(NO_FALL_DAMAGE_VA)
        self.assertEqual(bytes(self.orig[off:off + 6]),
                         NO_FALL_DAMAGE_SPEC.original)

    def test_apply_rewrites_prologue(self):
        data = bytearray(self.orig)
        apply_no_fall_damage_patch(data)
        off = va_to_file(NO_FALL_DAMAGE_VA)
        self.assertEqual(bytes(data[off:off + 6]),
                         NO_FALL_DAMAGE_SPEC.patch)


# ---------------------------------------------------------------------------
# infinite_fuel
# ---------------------------------------------------------------------------

class InfiniteFuelSpecShape(unittest.TestCase):
    def test_patch_is_mov_al_1_ret_4(self):
        # B0 01 C2 04 00 = MOV AL,1 ; RET 4.
        self.assertEqual(INFINITE_FUEL_SPEC.patch, bytes.fromhex("b001c20400"))

    def test_vanilla_is_prologue_push_mov_test(self):
        # Matches what Ghidra shows for FUN_000842D0's first 5 bytes.
        self.assertEqual(INFINITE_FUEL_SPEC.original, bytes.fromhex("518b412085"))


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class InfiniteFuelApply(unittest.TestCase):
    def setUp(self):
        self.orig = _XBE_PATH.read_bytes()

    def test_vanilla_bytes_match_ghidra(self):
        off = va_to_file(AZURIK_CONSUME_FUEL_VA)
        self.assertEqual(bytes(self.orig[off:off + 5]),
                         INFINITE_FUEL_SPEC.original)

    def test_apply_replaces_prologue(self):
        data = bytearray(self.orig)
        apply_infinite_fuel_patch(data)
        off = va_to_file(AZURIK_CONSUME_FUEL_VA)
        self.assertEqual(bytes(data[off:off + 5]),
                         INFINITE_FUEL_SPEC.patch)


# ---------------------------------------------------------------------------
# wing_flap_count
# ---------------------------------------------------------------------------

class WingFlapCountShimShape(unittest.TestCase):
    """Validate ``_build_shim_body`` produces the expected 50-byte
    layout regardless of shim_va / flaps_va arguments."""

    def test_shim_is_exactly_47_bytes(self):
        """v2 (late April 2026): shim shrank from 50 → 47 bytes
        when we switched from ``MOV EDX, ds:[abs32]`` (6B) to
        ``MOV EDX, [EDX+0x8]`` (3B) — saving 3 bytes."""
        body = _build_shim_body(
            shim_va=0x390000,
            flaps1_va=0x3A0000,
            flaps2_va=0x3A0004,
            flaps3_va=0x3A0008,
        )
        self.assertEqual(len(body), 47)

    def test_shim_starts_with_vanilla_replay(self):
        """Offset 0-2: MOV EAX, [EDX+0x38] = 8B 42 38."""
        body = _build_shim_body(0x390000, 0x3A0000, 0x3A0004, 0x3A0008)
        self.assertEqual(body[0:3], b"\x8B\x42\x38")

    def test_shim_reads_air_power_level_from_edx_indirect(self):
        """v2: Offset 3-5 is ``MOV EDX, [EDX+0x8]`` = 8B 52 08.
        EDX at hook entry is the level_struct pointer (set by
        the preceding ``MOV EDX, [EDI+0x20]`` at VA 0x8931E).
        level_struct+0x8 holds the air_power_level int.  (Pre-v2
        we dispatched on DAT_001A7AE4 via ``MOV EDX, ds:[abs32]``
        — that turned out to be the controller index, NOT the
        air-power level, so the shim always fell through to the
        vanilla fallback.)"""
        body = _build_shim_body(0x390000, 0x3A0000, 0x3A0004, 0x3A0008)
        self.assertEqual(body[3:6], b"\x8B\x52\x08")

    def test_shim_references_3_injected_ints_at_expected_offsets(self):
        """``A1 <abs32>`` sites at offsets 11, 23, 35 should point
        at flaps1_va, flaps2_va, flaps3_va respectively."""
        body = _build_shim_body(0x390000, 0xABC000, 0xABC004, 0xABC008)
        for off, expected_va in ((11, 0xABC000),
                                 (23, 0xABC004),
                                 (35, 0xABC008)):
            self.assertEqual(body[off], 0xA1,
                msg=f"expected MOV EAX, abs32 (A1) at offset {off}")
            got_va = struct.unpack("<I", body[off + 1:off + 5])[0]
            self.assertEqual(got_va, expected_va)

    def test_shim_replays_test_then_jumps_back(self):
        """Offsets 40-46: TEST EAX, EAX then JMP back_va."""
        shim_va = 0x390000
        body = _build_shim_body(shim_va, 0x3A0000, 0x3A0004, 0x3A0008)
        self.assertEqual(body[40:42], b"\x85\xC0",
            msg="TEST EAX, EAX at offset 40")
        self.assertEqual(body[42], 0xE9,
            msg="JMP rel32 at offset 42")
        rel = struct.unpack("<i", body[43:47])[0]
        # JMP origin-after-inst = shim_va + 47.
        self.assertEqual(
            shim_va + 47 + rel, _WING_FLAP_HOOK_RETURN_VA,
            msg="back-JMP must target the hook return VA "
                f"(0x{_WING_FLAP_HOOK_RETURN_VA:X})")


class WingFlapCountSliderShape(unittest.TestCase):
    def test_all_three_sliders_are_virtual(self):
        for p in (FLAPS_AIR_1, FLAPS_AIR_2, FLAPS_AIR_3):
            self.assertTrue(p.is_virtual,
                msg=f"{p.name} should be virtual (va=0)")

    def test_sliders_have_vanilla_defaults(self):
        self.assertEqual(FLAPS_AIR_1.default, 1.0)
        self.assertEqual(FLAPS_AIR_2.default, 2.0)
        self.assertEqual(FLAPS_AIR_3.default, 5.0)

    def test_encode_rounds_to_int(self):
        # Slider values come from tkinter as floats; encode must
        # round to an int and clamp negatives to 0.
        self.assertEqual(FLAPS_AIR_1.encode(3.0), struct.pack("<i", 3))
        self.assertEqual(FLAPS_AIR_1.encode(3.7), struct.pack("<i", 3))
        self.assertEqual(FLAPS_AIR_1.encode(-5.0), struct.pack("<i", 0))


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class WingFlapCountApply(unittest.TestCase):
    def setUp(self):
        self.orig = _XBE_PATH.read_bytes()

    def test_vanilla_hook_bytes_match_ghidra(self):
        off = va_to_file(_WING_FLAP_HOOK_VA)
        self.assertEqual(bytes(self.orig[off:off + 5]),
                         _WING_FLAP_HOOK_VANILLA)

    def test_defaults_are_noop(self):
        data = bytearray(self.orig)
        self.assertFalse(apply_wing_flap_count(
            data, flaps_air_power_1=1, flaps_air_power_2=2,
            flaps_air_power_3=5))
        self.assertEqual(bytes(data), self.orig,
            msg="apply at defaults (1/2/5) must leave the XBE "
                "byte-identical")

    def test_nondefault_installs_trampoline_and_shim(self):
        data = bytearray(self.orig)
        self.assertTrue(apply_wing_flap_count(
            data, flaps_air_power_1=10, flaps_air_power_2=20,
            flaps_air_power_3=50))

        # Trampoline at hook site is a 5-byte E9 <rel32>.
        hook_off = va_to_file(_WING_FLAP_HOOK_VA)
        tramp = bytes(data[hook_off:hook_off + 5])
        self.assertEqual(tramp[0], 0xE9,
            msg="hook must be rewritten to JMP rel32")

        # Follow trampoline → shim body.
        rel = struct.unpack("<i", tramp[1:5])[0]
        shim_va = _WING_FLAP_HOOK_VA + 5 + rel
        _, secs = parse_xbe_sections(bytes(data))
        shim_fo = None
        for s in secs:
            if s["vaddr"] <= shim_va < s["vaddr"] + s["vsize"]:
                shim_fo = s["raw_addr"] + (shim_va - s["vaddr"])
                break
        self.assertIsNotNone(shim_fo,
            msg="shim VA must resolve through the (possibly new) "
                "section table")

        # Shim body layout sanity.
        body = bytes(data[shim_fo:shim_fo + 47])
        self.assertEqual(body[0:3], b"\x8B\x42\x38",
            msg="shim must replay MOV EAX, [EDX+0x38]")
        self.assertEqual(body[3:6], b"\x8B\x52\x08",
            msg="shim must load air_power_level from [EDX+0x8]")
        self.assertEqual(body[40:42], b"\x85\xC0",
            msg="shim must replay TEST EAX, EAX before JMP")

        # Follow each A1 <abs32> site → verify it stores the
        # correct user-provided int value.
        for off, expected in ((11, 10), (23, 20), (35, 50)):
            self.assertEqual(body[off], 0xA1)
            flap_va = struct.unpack("<I", body[off + 1:off + 5])[0]
            flap_fo = None
            for s in secs:
                if s["vaddr"] <= flap_va < s["vaddr"] + s["vsize"]:
                    flap_fo = s["raw_addr"] + (flap_va - s["vaddr"])
                    break
            self.assertIsNotNone(flap_fo)
            val = struct.unpack("<i", data[flap_fo:flap_fo + 4])[0]
            self.assertEqual(val, expected,
                msg=f"A1 site at shim[{off}] should load int "
                    f"{expected} (user input), got {val}")

    def test_reapply_to_patched_buffer_is_rejected(self):
        data = bytearray(self.orig)
        self.assertTrue(apply_wing_flap_count(
            data, flaps_air_power_1=7))
        # Second apply: hook bytes now differ from vanilla
        # (starts with E9, not 8B) so the spec guard must refuse.
        self.assertFalse(apply_wing_flap_count(
            data, flaps_air_power_1=99),
            msg="drifted hook must not be re-trampolined")

    def test_three_flap_ints_land_in_contiguous_12_byte_block(self):
        """Regression: a prior bug carved the 3 ints as 3 separate
        4-byte allocations, and the allocator's zero-trailing-slack
        detection mistook the inner zero bytes of small ints as
        padding — resulting in 3 VAs only 1 byte apart.  The fix
        is to pack all 3 into one 12-byte allocation."""
        data = bytearray(self.orig)
        self.assertTrue(apply_wing_flap_count(
            data, flaps_air_power_1=3, flaps_air_power_2=7,
            flaps_air_power_3=9))
        hook_off = va_to_file(_WING_FLAP_HOOK_VA)
        tramp = bytes(data[hook_off:hook_off + 5])
        shim_va = _WING_FLAP_HOOK_VA + 5 + struct.unpack(
            "<i", tramp[1:5])[0]
        _, secs = parse_xbe_sections(bytes(data))
        shim_fo = next(
            s["raw_addr"] + (shim_va - s["vaddr"])
            for s in secs
            if s["vaddr"] <= shim_va < s["vaddr"] + s["vsize"])
        body = bytes(data[shim_fo:shim_fo + 47])
        # v2 A1 site offsets: 11 / 23 / 35.  Absolute VA lives at
        # bytes [off+1 .. off+5].
        flaps1_va = struct.unpack("<I", body[12:16])[0]
        flaps2_va = struct.unpack("<I", body[24:28])[0]
        flaps3_va = struct.unpack("<I", body[36:40])[0]
        self.assertEqual(flaps2_va - flaps1_va, 4)
        self.assertEqual(flaps3_va - flaps2_va, 4)


if __name__ == "__main__":
    unittest.main()
