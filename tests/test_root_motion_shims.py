"""Tests for ``root_motion_roll`` and ``root_motion_climb`` packs."""

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

from azurik_mod.patches.root_motion_roll import (  # noqa: E402
    ROLL_SPEED_SHIM_SLIDER,
    _HOOK_RETURN_VA as _ROLL_HOOK_RETURN,
    _HOOK_VA as _ROLL_HOOK_VA,
    _HOOK_VANILLA as _ROLL_HOOK_VANILLA,
    _SHIM_BODY_SIZE as _ROLL_SHIM_SIZE,
    _build_shim_body as _build_roll_shim,
    apply_root_motion_roll,
)
from azurik_mod.patches.root_motion_climb import (  # noqa: E402
    CLIMB_SPEED_SHIM_SLIDER,
    _HOOK_RETURN_VA as _CLIMB_HOOK_RETURN,
    _HOOK_VA as _CLIMB_HOOK_VA,
    _HOOK_VANILLA as _CLIMB_HOOK_VANILLA,
    _SHIM_BODY_SIZE as _CLIMB_SHIM_SIZE,
    _build_shim_body as _build_climb_shim,
    apply_root_motion_climb,
)
from azurik_mod.patching.xbe import va_to_file  # noqa: E402

_XBE_CANDIDATES = [
    Path("/Users/michaelsrouji/Documents/Xemu/tools/"
         "Azurik - Rise of Perathia (USA).xiso/default.xbe"),
    Path(_REPO_ROOT).parent /
        "Azurik - Rise of Perathia (USA).xiso" / "default.xbe",
    Path(_REPO_ROOT) / "tests" / "fixtures" / "default.xbe",
]
_XBE_PATH = next((p for p in _XBE_CANDIDATES if p.exists()), None)


# ---------------------------------------------------------------------------
# Spec shape
# ---------------------------------------------------------------------------

class RootMotionRollSpec(unittest.TestCase):
    def test_hook_va(self):
        self.assertEqual(_ROLL_HOOK_VA, 0x000866D9)

    def test_hook_return(self):
        self.assertEqual(_ROLL_HOOK_RETURN, _ROLL_HOOK_VA + 5)

    def test_hook_vanilla_is_call_rel32(self):
        self.assertEqual(_ROLL_HOOK_VANILLA[0], 0xE8)
        self.assertEqual(len(_ROLL_HOOK_VANILLA), 5)

    def test_shim_body_size(self):
        body = _build_roll_shim(scale_va=0x1001D0, shim_va=0x39F000)
        self.assertEqual(len(body), _ROLL_SHIM_SIZE)
        self.assertEqual(len(body), 134)

    def test_shim_starts_with_prologue(self):
        body = _build_roll_shim(scale_va=0x1001D0, shim_va=0x39F000)
        # PUSH EDI ; PUSH EBX ; MOV EBX, [ESP+0xC] ; MOV EDI, ECX
        self.assertEqual(body[0], 0x57)
        self.assertEqual(body[1], 0x53)
        self.assertEqual(body[2:6], b"\x8B\x5C\x24\x0C")
        self.assertEqual(body[6:8], b"\x8B\xF9")

    def test_shim_calls_fun_00042e40(self):
        """The CALL rel32 at offset 26 must target VA 0x42E40."""
        shim_va = 0x0039F000
        body = _build_roll_shim(scale_va=0x1001D0, shim_va=shim_va)
        self.assertEqual(body[26], 0xE8)
        rel32 = struct.unpack("<i", body[27:31])[0]
        self.assertEqual(shim_va + 31 + rel32, 0x00042E40)

    def test_shim_has_gate_check(self):
        """TEST byte [EBP+0x20], 0x40 at offset 31."""
        body = _build_roll_shim(scale_va=0x1001D0, shim_va=0x39F000)
        self.assertEqual(body[31:35], b"\xF6\x45\x20\x40")
        # JZ +92 (scale block size)
        self.assertEqual(body[35:37], b"\x74\x5C")

    def test_shim_ends_with_ret_0x10(self):
        body = _build_roll_shim(scale_va=0x1001D0, shim_va=0x39F000)
        self.assertEqual(body[-3:], b"\xC2\x10\x00")


class RootMotionClimbSpec(unittest.TestCase):
    def test_hook_va(self):
        self.assertEqual(_CLIMB_HOOK_VA, 0x000883FF)

    def test_hook_vanilla_is_call(self):
        self.assertEqual(_CLIMB_HOOK_VANILLA[0], 0xE8)

    def test_shim_body_size(self):
        body = _build_climb_shim(scale_va=0x1001D0, shim_va=0x39F000)
        self.assertEqual(len(body), _CLIMB_SHIM_SIZE)
        self.assertEqual(len(body), 128)

    def test_no_gate_check(self):
        """Climb shim has no TEST [EBP+0x20] — scaling is
        unconditional inside player_climb_tick."""
        body = _build_climb_shim(scale_va=0x1001D0, shim_va=0x39F000)
        # The F6 45 20 40 pattern must NOT appear (roll's gate).
        self.assertNotIn(b"\xF6\x45\x20\x40", body)

    def test_calls_vanilla_fun_00042e40(self):
        shim_va = 0x39F000
        body = _build_climb_shim(scale_va=0x1001D0, shim_va=shim_va)
        # CALL is at offset 26.
        self.assertEqual(body[26], 0xE8)
        rel32 = struct.unpack("<i", body[27:31])[0]
        self.assertEqual(shim_va + 31 + rel32, 0x00042E40)


# ---------------------------------------------------------------------------
# Apply on vanilla XBE
# ---------------------------------------------------------------------------

@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class RollApplyBehaviour(unittest.TestCase):
    def setUp(self):
        self.orig = _XBE_PATH.read_bytes()

    def test_vanilla_bytes_match(self):
        off = va_to_file(_ROLL_HOOK_VA)
        self.assertEqual(bytes(self.orig[off:off + 5]),
                         _ROLL_HOOK_VANILLA)

    def test_apply_installs_call_trampoline(self):
        data = bytearray(self.orig)
        self.assertTrue(apply_root_motion_roll(data, scale=2.0))
        off = va_to_file(_ROLL_HOOK_VA)
        tramp = bytes(data[off:off + 5])
        self.assertEqual(tramp[0], 0xE8)

    def test_reapply_idempotent(self):
        data = bytearray(self.orig)
        apply_root_motion_roll(data, scale=2.0)
        snap = bytes(data)
        apply_root_motion_roll(data, scale=2.0)
        self.assertEqual(bytes(data), snap)


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class ClimbApplyBehaviour(unittest.TestCase):
    def setUp(self):
        self.orig = _XBE_PATH.read_bytes()

    def test_vanilla_bytes_match(self):
        off = va_to_file(_CLIMB_HOOK_VA)
        self.assertEqual(bytes(self.orig[off:off + 5]),
                         _CLIMB_HOOK_VANILLA)

    def test_apply_installs_trampoline(self):
        data = bytearray(self.orig)
        self.assertTrue(apply_root_motion_climb(data, scale=2.0))
        off = va_to_file(_CLIMB_HOOK_VA)
        tramp = bytes(data[off:off + 5])
        self.assertEqual(tramp[0], 0xE8)


class BothPacksRegistered(unittest.TestCase):
    def test_roll_registered(self):
        from azurik_mod.patching.registry import all_packs
        self.assertIn("root_motion_roll",
                      [p.name for p in all_packs()])

    def test_climb_registered(self):
        from azurik_mod.patching.registry import all_packs
        self.assertIn("root_motion_climb",
                      [p.name for p in all_packs()])

    def test_both_in_player_category(self):
        from azurik_mod.patching.registry import get_pack
        self.assertEqual(get_pack("root_motion_roll").category, "player")
        self.assertEqual(get_pack("root_motion_climb").category, "player")


if __name__ == "__main__":
    unittest.main()
