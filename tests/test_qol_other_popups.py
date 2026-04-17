"""Invariants for the `qol_other_popups` pack.

Pins the 9 byte offsets and confirms:

- ``apply_other_popups_patch`` nulls exactly those 9 bytes on a fresh
  buffer and touches nothing else.
- Running the patch twice is idempotent (the second call is a no-op).
- The pack is registered with ``default_on=False`` under the qol tag.
- ``gameover`` at 0x194910 is explicitly NOT in the offset list (that
  popup drives the death screen — suppressing it would break UX).

All checks operate on synthetic byte buffers so the tests stay offline
and don't need a real XBE.
"""

from __future__ import annotations

import os
import sys
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from azurik_mod.patches.qol import (  # noqa: E402
    GEM_POPUP_OFFSETS,
    OTHER_POPUP_OFFSETS,
    apply_other_popups_patch,
)
from azurik_mod.patching.registry import get_pack  # noqa: E402


# The exact 9 offsets we expect, in declaration order.  Written out
# here rather than re-imported from the module so this test catches
# accidental mutation of the declared list.
EXPECTED_OFFSETS = [
    0x194A78,  # swim
    0x197760,  # 6keys
    0x19777C,  # key
    0x197794,  # chromatic_powerup
    0x1977BC,  # health
    0x197874,  # water_powerup
    0x197898,  # fire_powerup
    0x1978B8,  # air_powerup
    0x1978D8,  # earth_powerup
]

GAMEOVER_OFFSET = 0x194910


class OffsetInvariants(unittest.TestCase):
    def test_nine_offsets_in_expected_order(self):
        self.assertEqual(OTHER_POPUP_OFFSETS, EXPECTED_OFFSETS,
            msg="OTHER_POPUP_OFFSETS drifted; update both this test and "
                "the pack docstring if you intentionally added / removed a "
                "popup key, or fix the offset if this failed by mistake.")

    def test_gameover_excluded(self):
        self.assertNotIn(GAMEOVER_OFFSET, OTHER_POPUP_OFFSETS,
            msg="0x194910 (loc/english/popups/gameover) must NOT be in "
                "OTHER_POPUP_OFFSETS — it drives the death-screen popup, "
                "not a pickup popup.  Suppressing it leaves the player "
                "with no feedback on death.")

    def test_no_overlap_with_gem_offsets(self):
        self.assertTrue(set(OTHER_POPUP_OFFSETS).isdisjoint(GEM_POPUP_OFFSETS),
            msg="qol_other_popups and qol_gem_popups must target "
                "disjoint offsets so toggling one does not affect the "
                "other.")


class ApplyOtherPopupsPatch(unittest.TestCase):
    """Behaviour of the byte-nulling apply function."""

    def _make_buffer(self) -> bytearray:
        """2 MB buffer with printable ASCII at every declared offset so
        the patch's safety guard (``0x20 <= byte <= 0x7E``) is satisfied
        and the null actually happens."""
        buf = bytearray(2 * 1024 * 1024)
        for off in OTHER_POPUP_OFFSETS:
            buf[off] = ord('l')  # first byte of "loc/english/..."
        return buf

    def test_nulls_exactly_nine_bytes(self):
        buf = self._make_buffer()
        before = bytes(buf)
        apply_other_popups_patch(buf)
        diffs = [i for i in range(len(buf)) if buf[i] != before[i]]
        self.assertEqual(sorted(diffs), sorted(OTHER_POPUP_OFFSETS),
            msg="apply_other_popups_patch must null exactly the 9 declared "
                f"offsets, but changed: {[hex(d) for d in diffs]}")

    def test_nulled_bytes_are_zero(self):
        buf = self._make_buffer()
        apply_other_popups_patch(buf)
        for off in OTHER_POPUP_OFFSETS:
            self.assertEqual(buf[off], 0x00,
                msg=f"byte at 0x{off:X} should be 0x00 after patch")

    def test_idempotent(self):
        buf = self._make_buffer()
        apply_other_popups_patch(buf)
        snapshot = bytes(buf)
        apply_other_popups_patch(buf)  # second run — every byte already 0x00
        self.assertEqual(bytes(buf), snapshot,
            msg="re-applying the patch must be a no-op (second run "
                "should see 0x00 bytes and leave them alone)")

    def test_out_of_range_offset_is_safe(self):
        """A truncated / tiny buffer should not crash — patch just warns."""
        tiny = bytearray(1024)  # way smaller than any declared offset
        apply_other_popups_patch(tiny)  # must not raise
        self.assertEqual(bytes(tiny), b'\x00' * 1024,
            msg="tiny buffer must stay zero-filled after patch skips")


class RegistryEntry(unittest.TestCase):
    def test_pack_registered_with_expected_metadata(self):
        import azurik_mod.patches  # noqa: F401  — triggers registration
        pack = get_pack("qol_other_popups")
        self.assertFalse(pack.default_on,
            msg="qol_other_popups must default to OFF")
        self.assertIn("qol", pack.tags)
        self.assertEqual(pack.sites, [],
            msg="qol_other_popups has no PatchSpec sites — it's an "
                "imperative null-byte patch on a list of offsets")


if __name__ == "__main__":
    unittest.main()
