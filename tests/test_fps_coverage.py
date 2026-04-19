"""Regression guards for the 60 FPS patch's .rdata coverage.

The existing ``fps_unlock`` patch halves the ~30 baked-in
``1/30``-second timestep constants + the handful of
``30.0`` rate multipliers sprinkled through ``.rdata``.  This
file pins that coverage so if a future game patch (or a re-RE
pass that discovers a new constant) exposes an un-patched frame
rate literal, we flip red instead of shipping a partially-60-FPS
build.

The audits that grounded this test live in:

- docs/LEARNINGS.md § 60 FPS patch re-audit (April 2026)
- docs/PATCHES.md § fps_unlock (overview)

Re-run the full scan with:

    azurik-mod xbe find-floats 0.03333 0.03334 --xbe default.xbe
    azurik-mod xbe find-floats 29.99   30.01   --xbe default.xbe
    azurik-mod xbe find-floats 59.9    60.1    --xbe default.xbe
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_VANILLA_XBE = (_REPO.parent /
                "Azurik - Rise of Perathia (USA).xiso" / "default.xbe")


# Known .rdata VAs that hold a 30.0 or 60.0 constant but are NOT
# frame-rate dependent — verified via Ghidra xrefs.  Listed here
# so the "every X.0 is patched" test can exempt them.
_NOT_FRAMERATE_30: dict[int, str] = {
    # Referenced from FUN_0003EA00 as an upper-bound threshold
    # (``if (30.0 < *(float*)(param_2 + 8))``) — speed/angle check,
    # not a rate multiplier.
    0x0019FD98: "threshold in FUN_0003EA00 (not a rate)",
    # No .text xrefs found — dead data (likely a compiler literal
    # that never made it into runtime code).
    0x001A2524: "unreferenced .rdata literal",
}

_NOT_FRAMERATE_60: dict[int, str] = {
    # All 6 literal 60.0 f32 constants in .rdata are rendering
    # pipeline scales (FOV defaults + screen-space UI math), not
    # frame-rate multipliers.  Verified via Ghidra xrefs against:
    #   FUN_0005AC80 — UI Y-position math (60.0 as px scale)
    #   FUN_00054800 — camera setup (60.0 as default FOV degrees)
    #   FUN_0002FA30 / FUN_00096260 — rendering pipeline
    0x0019D816: "pRenderer UI scale",
    0x0019D82A: "pRenderer UI scale",
    0x0019E628: "UI y-position math (FUN_0005AC80)",
    0x0019FBCE: "rendering scale",
    0x001A1ADE: "rendering scale",
    0x001A2608: "FOV default (FUN_00054800)",
}


@unittest.skipUnless(_VANILLA_XBE.exists(),
                     "vanilla default.xbe fixture required")
class FrameRateConstantCoverage(unittest.TestCase):
    """Pins the exact set of ``.rdata`` FPS constants discovered
    during the April 2026 re-audit.  If any of these counts drift,
    new constants appeared and need to be classified."""

    @classmethod
    def setUpClass(cls) -> None:
        from azurik_mod.xbe_tools.xbe_scan import find_floats_in_range
        xbe = _VANILLA_XBE.read_bytes()
        cls.third_f32 = find_floats_in_range(
            xbe, 0.03333, 0.03334, widths=(4,))
        cls.third_f64 = find_floats_in_range(
            xbe, 0.033333, 0.033334, widths=(8,))
        cls.thirty_f32 = find_floats_in_range(
            xbe, 29.99, 30.01, widths=(4,))
        cls.thirty_f64 = find_floats_in_range(
            xbe, 29.999, 30.001, widths=(8,))
        cls.sixty_f32 = find_floats_in_range(
            xbe, 59.99, 60.01, widths=(4,))
        cls.one_over_sixty = find_floats_in_range(
            xbe, 0.01666, 0.01667, widths=(4, 8))

    def test_all_1_over_30_f32_are_patched(self):
        """Every float32 1/30 in the vanilla XBE must be covered
        by fps_unlock.  A new one appearing means the scanner
        found a constant the patch doesn't halve — the shipped
        60 FPS build would run THIS subsystem at half speed."""
        from azurik_mod.patches.fps_unlock import FPS_DATA_PATCHED_VAS
        unpatched = [h.va for h in self.third_f32
                     if h.va not in FPS_DATA_PATCHED_VAS]
        self.assertEqual(unpatched, [],
            msg=("New 1/30 f32 constants in .rdata are NOT patched "
                 "by fps_unlock.  Either add them to FPS_PATCH_SITES "
                 "or document why they don't need halving:\n" +
                 "\n".join(f"  VA 0x{va:X}" for va in unpatched)))

    def test_all_1_over_30_f64_are_patched(self):
        from azurik_mod.patches.fps_unlock import FPS_DATA_PATCHED_VAS
        unpatched = [h.va for h in self.third_f64
                     if h.va not in FPS_DATA_PATCHED_VAS]
        self.assertEqual(unpatched, [],
            msg=(f"New 1/30 f64 constant(s) found: "
                 f"{[hex(va) for va in unpatched]}"))

    def test_all_30_f32_are_patched_or_classified(self):
        """Every 30.0 f32 either ships in fps_unlock's patch set
        OR is documented as non-frame-rate in ``_NOT_FRAMERATE_30``.
        A new 30.0 that's neither means the audit is incomplete."""
        from azurik_mod.patches.fps_unlock import FPS_DATA_PATCHED_VAS
        unclassified = [h.va for h in self.thirty_f32
                        if h.va not in FPS_DATA_PATCHED_VAS
                        and h.va not in _NOT_FRAMERATE_30]
        self.assertEqual(unclassified, [],
            msg=("New 30.0 f32 constant(s) that are neither patched "
                 "nor documented as non-frame-rate:\n" +
                 "\n".join(f"  VA 0x{va:X}" for va in unclassified)))

    def test_all_60_f32_are_classified(self):
        """No 60.0 f32 should be a frame-rate multiplier in vanilla
        — the game was written for 30 FPS, so a literal 60 only
        shows up in rendering / UI / FOV contexts.  Every 60.0 we
        find must be pre-classified in ``_NOT_FRAMERATE_60``."""
        unclassified = [h.va for h in self.sixty_f32
                        if h.va not in _NOT_FRAMERATE_60]
        self.assertEqual(unclassified, [],
            msg=("New 60.0 f32 constant(s) need to be audited via "
                 "Ghidra xrefs + added to _NOT_FRAMERATE_60 if not "
                 "a rate, or added to fps_unlock if they are:\n" +
                 "\n".join(f"  VA 0x{va:X}" for va in unclassified)))

    def test_no_baked_in_1_over_60(self):
        """Azurik was written for 30 FPS, so it has no ``1/60``
        constants.  If one appears, the game was re-authored for
        60 FPS (great!) or someone introduced a hardcoded
        post-unlock constant (concerning)."""
        self.assertEqual(self.one_over_sixty, [],
            msg="Unexpected 1/60 constant(s) in vanilla .rdata.")

    def test_vanilla_counts_match_audit(self):
        """Pin the actual vanilla counts so a re-dump of the XBE
        (modded / patched / re-extracted) flips this test red."""
        self.assertEqual(len(self.third_f32), 29,
            msg="Expected 29 × 1/30 f32 in vanilla .rdata")
        self.assertEqual(len(self.third_f64), 1,
            msg="Expected 1 × 1/30 f64 in vanilla .rdata")
        self.assertEqual(len(self.thirty_f32), 5,
            msg="Expected 5 × 30.0 f32 in vanilla .rdata")
        self.assertEqual(len(self.thirty_f64), 1,
            msg="Expected 1 × 30.0 f64 in vanilla .rdata")
        self.assertEqual(len(self.sixty_f32), 6,
            msg="Expected 6 × 60.0 f32 in vanilla .rdata")


if __name__ == "__main__":
    unittest.main()
