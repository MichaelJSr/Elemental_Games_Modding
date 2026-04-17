"""Invariants for the ``PatchPack.extra_whitelist_ranges`` mechanism.

This covers the hole the previous release shipped with: the popup
packs (``qol_gem_popups`` / ``qol_other_popups``) mutate bytes
imperatively rather than through ``PatchSpec`` sites, so the
``verify-patches --strict`` whitelist walker used to flag those 14
intentional flips as unexpected changes.  The fix adds an
``extra_whitelist_ranges`` field on ``PatchPack`` that both packs
populate with their offset lists; ``cmd_verify_patches`` unions them
into its allow-list so --strict stays quiet.

These tests pin:

1. The field itself exists with the right default.
2. Both popup packs declare exactly the offsets their apply functions
   will flip (5 for gem_popups, 9 for other_popups).
3. Every range is a single-byte ``(off, off + 1)`` tuple in the same
   file-offset coordinate space the whitelist walker uses.
4. The ranges are disjoint between packs (no overlap).

All checks operate on registry metadata; no XBE binary needed.
"""

from __future__ import annotations

import os
import sys
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from azurik_mod.patching.registry import PatchPack, get_pack  # noqa: E402
from azurik_mod.patches.qol import (  # noqa: E402
    GEM_POPUP_OFFSETS,
    OTHER_POPUP_OFFSETS,
)


class PatchPackField(unittest.TestCase):
    """The field itself must exist with the right default."""

    def test_default_is_empty_tuple(self):
        # Construct a trivial pack and confirm the default.
        pack = PatchPack(
            name="test_empty",
            description="",
            sites=[],
            apply=lambda *_a: None,
        )
        self.assertEqual(pack.extra_whitelist_ranges, ())

    def test_field_is_declarable(self):
        pack = PatchPack(
            name="test_with_ranges",
            description="",
            sites=[],
            apply=lambda *_a: None,
            extra_whitelist_ranges=((0x100, 0x101), (0x200, 0x205)),
        )
        self.assertEqual(pack.extra_whitelist_ranges,
                         ((0x100, 0x101), (0x200, 0x205)))


class PopupPackRanges(unittest.TestCase):
    """The popup packs must declare a range for every byte they null."""

    def test_gem_popups_ranges_match_offsets(self):
        pack = get_pack("qol_gem_popups")
        self.assertEqual(len(pack.extra_whitelist_ranges),
                         len(GEM_POPUP_OFFSETS))
        for (lo, hi), off in zip(pack.extra_whitelist_ranges,
                                 GEM_POPUP_OFFSETS):
            self.assertEqual(lo, off)
            self.assertEqual(hi, off + 1,
                msg="Gem popup ranges must be single-byte half-open "
                    "(off, off+1) tuples.")

    def test_other_popups_ranges_match_offsets(self):
        pack = get_pack("qol_other_popups")
        self.assertEqual(len(pack.extra_whitelist_ranges),
                         len(OTHER_POPUP_OFFSETS))
        for (lo, hi), off in zip(pack.extra_whitelist_ranges,
                                 OTHER_POPUP_OFFSETS):
            self.assertEqual(lo, off)
            self.assertEqual(hi, off + 1)

    def test_gem_and_other_ranges_are_disjoint(self):
        gem = set(get_pack("qol_gem_popups").extra_whitelist_ranges)
        other = set(get_pack("qol_other_popups").extra_whitelist_ranges)
        self.assertTrue(gem.isdisjoint(other),
            msg="qol_gem_popups and qol_other_popups must not claim "
                "overlapping whitelist ranges — otherwise a byte could "
                "legally come from either pack and the source of a "
                "change would be ambiguous.")

    def test_pickup_anims_and_skip_logo_do_not_need_extra_ranges(self):
        """Those two packs use PatchSpec sites, so they don't need
        extra_whitelist_ranges — the PatchSpec site range IS the
        declared allow-range."""
        self.assertEqual(
            get_pack("qol_pickup_anims").extra_whitelist_ranges, ())
        self.assertEqual(
            get_pack("qol_skip_logo").extra_whitelist_ranges, ())


class WhitelistWalkerUsesExtraRanges(unittest.TestCase):
    """End-to-end: simulate verify-patches' allow-range construction."""

    def test_extra_ranges_enter_combined_allow_list(self):
        # Mirror the logic in cmd_verify_patches.allow_ranges building.
        from azurik_mod.patching.registry import all_packs
        combined: set[tuple[int, int]] = set()
        for pack in all_packs():
            combined.update(pack.extra_whitelist_ranges)
        # Every declared popup offset must appear in the combined set.
        for off in GEM_POPUP_OFFSETS + OTHER_POPUP_OFFSETS:
            self.assertIn((off, off + 1), combined,
                msg=f"offset 0x{off:X} should be in the combined "
                    f"whitelist-ranges set that verify-patches --strict "
                    f"builds; if it's not, the walker will flag it as "
                    f"an unexpected byte change.")


if __name__ == "__main__":
    unittest.main()
