"""Round-trip tests for the patch loader engine.

These tests operate on synthetic XBE-shaped bytearrays, so they do not
need a real Azurik binary and can run in CI.
"""

from __future__ import annotations

import os
import sys
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from azurik_mod.patching import (  # noqa: E402
    PatchSpec,
    apply_patch_spec,
    apply_xbe_patch,
    verify_patch_spec,
    va_to_file,
)
from azurik_mod.patching.xbe import XBE_SECTIONS  # noqa: E402
from azurik_mod.patching.registry import (  # noqa: E402
    PatchPack,
    all_packs,
    all_sites,
)


class TestVaToFile(unittest.TestCase):
    """Section-map translation."""

    def test_text_section(self):
        # .text VA 0x11000 maps to file 0x1000
        self.assertEqual(va_to_file(0x011000), 0x001000)
        self.assertEqual(va_to_file(0x059AFD), 0x049AFD)

    def test_rdata_section(self):
        # .rdata VA 0x18F3A0 maps to file 0x188000
        self.assertEqual(va_to_file(0x18F3A0), 0x188000)
        self.assertEqual(va_to_file(0x1983E8), 0x191048)

    def test_data_section(self):
        self.assertEqual(va_to_file(0x1A29A0), 0x19C000)

    def test_below_all_sections_raises(self):
        with self.assertRaises(ValueError):
            va_to_file(0x100)


class TestApplyVerifyRoundtrip(unittest.TestCase):
    """Apply + verify a PatchSpec and a raw apply_xbe_patch."""

    def _make_xbe(self, va: int, payload: bytes) -> bytearray:
        """Build a bytearray large enough to hold `payload` at `va`."""
        offset = va_to_file(va)
        data = bytearray(offset + len(payload) + 16)
        data[offset:offset + len(payload)] = payload
        return data

    def test_apply_patch_spec_happy_path(self):
        original = bytes([0xAA, 0xBB, 0xCC])
        patch = bytes([0x11, 0x22, 0x33])
        va = 0x059AFD  # .text
        data = self._make_xbe(va, original)
        spec = PatchSpec("test patch", va=va, original=original, patch=patch)

        self.assertEqual(verify_patch_spec(data, spec), "original")
        self.assertTrue(apply_patch_spec(data, spec))
        self.assertEqual(verify_patch_spec(data, spec), "applied")

    def test_apply_is_idempotent(self):
        data = self._make_xbe(0x059AFD, bytes([0xAA, 0xBB, 0xCC]))
        spec = PatchSpec("repeat", va=0x059AFD,
                         original=bytes([0xAA, 0xBB, 0xCC]),
                         patch=bytes([0x11, 0x22, 0x33]))
        self.assertTrue(apply_patch_spec(data, spec))
        # Re-apply — should detect "already applied" and still return True
        self.assertTrue(apply_patch_spec(data, spec))
        self.assertEqual(verify_patch_spec(data, spec), "applied")

    def test_mismatch_is_not_written(self):
        data = self._make_xbe(0x059AFD, bytes([0xDE, 0xAD, 0xBE]))
        spec = PatchSpec("bad match", va=0x059AFD,
                         original=bytes([0xAA, 0xBB, 0xCC]),
                         patch=bytes([0x11, 0x22, 0x33]))
        self.assertEqual(verify_patch_spec(data, spec), "mismatch")
        # apply returns False, does NOT write
        self.assertFalse(apply_patch_spec(data, spec))
        offset = spec.file_offset
        self.assertEqual(bytes(data[offset:offset + 3]), bytes([0xDE, 0xAD, 0xBE]))

    def test_out_of_range_returns_sentinel(self):
        spec = PatchSpec("too big", va=0x1A29A0,
                         original=b"\x00" * 8, patch=b"\x01" * 8)
        data = bytearray(1)
        self.assertEqual(verify_patch_spec(data, spec), "out-of-range")

    def test_length_mismatch_is_rejected(self):
        # apply_xbe_patch refuses to write if original and patch lengths differ
        data = bytearray(64)
        ok = apply_xbe_patch(data, "bad", offset=0,
                             original=b"\x00\x00", patch=b"\x11\x22\x33")
        self.assertFalse(ok)


class TestRegistry(unittest.TestCase):
    """Central pack registry should expose every registered pack."""

    def test_fps_and_qol_registered(self):
        # Importing the library registers every pack.
        import azurik_mod.patches.fps_unlock  # noqa: F401
        import azurik_mod.patches.qol  # noqa: F401

        names = {p.name for p in all_packs()}
        self.assertIn("fps_unlock", names)
        # QoL is now split into two independently-toggleable packs.
        self.assertIn("qol_gem_popups", names)
        self.assertIn("qol_pickup_anims", names)

    def test_all_sites_dedupes(self):
        # Real (non-virtual) sites: deduped by VA.
        # Virtual parametric sites have va=0 and are deduped by identity, so
        # multiple virtual sliders legitimately share va=0.
        real_vas = [s.va for s in all_sites() if s.va != 0]
        self.assertEqual(len(real_vas), len(set(real_vas)),
                         msg="all_sites() returned duplicate non-zero VAs")

    def test_patch_pack_fields(self):
        # PatchPack is a frozen dataclass — assignments should raise.
        from azurik_mod.patching.registry import PatchPack
        pack = PatchPack(
            name="tmp", description="", sites=[],
            apply=lambda _: None,
        )
        with self.assertRaises(Exception):
            pack.name = "changed"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
