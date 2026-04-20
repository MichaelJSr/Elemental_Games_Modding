"""Tests for the ``slope_slide_speed`` shim-backed revival."""

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

from azurik_mod.patches.slope_slide_speed import (  # noqa: E402
    SLOPE_SLIDE_SHIM_SLIDER,
    SLOPE_SLIDE_SITES,
    _HOOK_RETURN_VA,
    _HOOK_VA,
    _HOOK_VANILLA,
    _SHIM_BODY_SIZE,
    _VANILLA_SLOPE_DAT_VA,
    _build_shim_body,
    apply_slope_slide_speed_shim,
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


class SlopeSlideSpecShape(unittest.TestCase):
    def test_hook_va_is_state4_fld(self):
        self.assertEqual(_HOOK_VA, 0x0008A095)

    def test_hook_return_is_after_6_byte_window(self):
        self.assertEqual(_HOOK_RETURN_VA, _HOOK_VA + 6)

    def test_hook_vanilla_is_fld_3902a0(self):
        # D9 05 A0 02 39 00 = FLD [0x003902A0].
        self.assertEqual(_HOOK_VANILLA, bytes.fromhex("d905a0023900"))

    def test_shim_body_size(self):
        body = _build_shim_body(scale_va=0x1001D0, shim_va=0x39F000)
        self.assertEqual(len(body), _SHIM_BODY_SIZE)
        self.assertEqual(len(body), 17)

    def test_shim_body_structure(self):
        body = _build_shim_body(scale_va=0x1001D0, shim_va=0x39F000)
        # FLD [0x003902A0]
        self.assertEqual(body[0:2], b"\xD9\x05")
        self.assertEqual(
            struct.unpack("<I", body[2:6])[0],
            _VANILLA_SLOPE_DAT_VA)
        # FMUL [scale_va]
        self.assertEqual(body[6:8], b"\xD8\x0D")
        self.assertEqual(struct.unpack("<I", body[8:12])[0], 0x1001D0)
        # JMP <rel32>
        self.assertEqual(body[12:13], b"\xE9")
        rel32 = struct.unpack("<i", body[13:17])[0]
        self.assertEqual(0x39F000 + 17 + rel32, _HOOK_RETURN_VA)

    def test_slider_is_virtual(self):
        self.assertTrue(SLOPE_SLIDE_SHIM_SLIDER.is_virtual)

    def test_sites_single(self):
        self.assertEqual(len(SLOPE_SLIDE_SITES), 1)


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class SlopeSlideApply(unittest.TestCase):
    def setUp(self):
        self.orig = _XBE_PATH.read_bytes()

    def test_vanilla_bytes_match(self):
        off = va_to_file(_HOOK_VA)
        self.assertEqual(bytes(self.orig[off:off + 6]), _HOOK_VANILLA)

    def test_apply_installs_jmp_trampoline(self):
        data = bytearray(self.orig)
        self.assertTrue(
            apply_slope_slide_speed_shim(data, scale=2.0))
        off = va_to_file(_HOOK_VA)
        tramp = bytes(data[off:off + 6])
        self.assertEqual(tramp[0], 0xE9)
        self.assertEqual(tramp[5], 0x90)

    def test_reapply_is_idempotent(self):
        data = bytearray(self.orig)
        self.assertTrue(
            apply_slope_slide_speed_shim(data, scale=2.0))
        snap = bytes(data)
        self.assertTrue(
            apply_slope_slide_speed_shim(data, scale=2.0))
        self.assertEqual(bytes(data), snap)

    def test_whitelist(self):
        from azurik_mod.patches.slope_slide_speed import (
            _slope_slide_dynamic_whitelist,
        )
        data = bytearray(self.orig)
        apply_slope_slide_speed_shim(data, scale=1.5)
        ranges = _slope_slide_dynamic_whitelist(bytes(data))
        sizes = sorted(hi - lo for lo, hi in ranges)
        self.assertIn(6, sizes)
        self.assertIn(17, sizes)
        self.assertIn(8, sizes)


class SlopeSlideRegistered(unittest.TestCase):
    def test_registered(self):
        from azurik_mod.patching.registry import all_packs
        self.assertIn("slope_slide_speed",
                      [p.name for p in all_packs()])


if __name__ == "__main__":
    unittest.main()
