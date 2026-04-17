"""Safety-critical invariants for the 60 FPS patch.

These tests pin the simulation step cap at 4 so it cannot silently
drift back to 2 (which causes wall-clock slowdown whenever render FPS
drops below 30) or balloon to some larger value.

Rationale:

- Vanilla Azurik runs sim at 30 Hz with a 2-step catchup cap, which
  maintains real-time speed down to 15 FPS rendered.
- At 60 Hz sim we need cap = 4 to cover the same 15-FPS-render window
  (4 * (1/60) == 2 * (1/30)).  Cap = 2 at 60 Hz would start slowing
  game time whenever render FPS dips below 30, which was reported in
  live play.
- The on-death BSOD is reproduced on vanilla 30 FPS Azurik too and is
  therefore unrelated to the step cap.

The TRUNC (CMP ESI, 0x4) and CATCHUP (PUSH 0x4 followed by two
FADD ST0,ST0) patches MUST agree on the cap byte.  Any drift between
them desynchronises the accumulator math and produces a subtle
long-frame drift.

Run with:

    python -m unittest tests.test_fps_safety

The tests work entirely on static byte blobs; no XBE binary is needed
and they are safe in CI.
"""

from __future__ import annotations

import os
import sys
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from azurik_mod.patches.fps_unlock import (  # noqa: E402
    FPS_PATCH_SITES,
    FPS_SAFETY_CRITICAL_SITES,
)


def _spec_by_va(va: int):
    for spec in FPS_PATCH_SITES:
        if spec.va == va:
            return spec
    raise AssertionError(f"No PatchSpec at VA 0x{va:X}")


# Canonical VAs for the two step-cap patches.
TRUNC_VA   = 0x059AFD
CATCHUP_VA = 0x059B37

# The literal x86 byte sequence that encodes `CMP ESI, 4`.
CMP_ESI_4  = bytes([0x83, 0xFE, 0x04])

# `PUSH 0x4` — byte-equivalent substitute for `MOV ESI, 4` that keeps
# the catchup block at 30 bytes and sits at a fixed offset inside the
# patch.
PUSH_4     = bytes([0x6A, 0x04])

# `FADD ST0, ST0` — two of these in the catchup patch compute 4*dt
# (dt is loaded from [0x1983E8] = 1/60, so after two doublings ST0
# holds 1/15, matching cap = 4).
FADD_ST0_ST0 = bytes([0xDC, 0xC0])

# Offset within FPS_CATCHUP_PATCH where PUSH 0x4 / POP ESI appears.
# Layout: FLD float ptr [0x1983E8] (6B), then PUSH 0x4 (2B), then POP ESI.
CATCHUP_PUSH_OFFSET = 6


class StepCapInvariants(unittest.TestCase):
    """Pin the step cap — both TRUNC and CATCHUP sites must agree on 4."""

    def test_safety_critical_sites_are_exactly_trunc_and_catchup(self):
        safety_vas = {s.va for s in FPS_SAFETY_CRITICAL_SITES}
        self.assertEqual(safety_vas, {TRUNC_VA, CATCHUP_VA},
            msg="safety-critical tag drifted; only the TRUNC (0x59AFD) and "
                "CATCHUP (0x59B37) patches should be tagged as "
                "safety-critical.  Both pin the sim step cap.")

    def test_trunc_patch_caps_at_4(self):
        spec = _spec_by_va(TRUNC_VA)
        count = spec.patch.count(CMP_ESI_4)
        self.assertEqual(count, 1,
            msg=f"FPS_TRUNC_PATCH should contain exactly one `CMP ESI, 4` "
                f"but found {count} occurrences.  At 60 Hz sim the cap "
                f"must be 4 so the game runs at real-time speed down to "
                f"15 FPS render (matching vanilla's 2-step coverage at "
                f"30 Hz sim).  If this failed because the cap was lowered "
                f"to 2, DO NOT bypass this test — cap=2 starts slowing "
                f"game time below 30 FPS render.")

    def test_trunc_patch_does_not_contain_cmp_esi_2(self):
        spec = _spec_by_va(TRUNC_VA)
        self.assertNotIn(bytes([0x83, 0xFE, 0x02]), spec.patch,
            msg="FPS_TRUNC_PATCH contains `CMP ESI, 2` — the low-cap "
                "value that causes wall-clock slowdown below 30 FPS "
                "render.  The cap MUST stay at 4 at 60 Hz sim.")

    def test_catchup_patch_push_4_at_fixed_offset(self):
        spec = _spec_by_va(CATCHUP_VA)
        got = spec.patch[CATCHUP_PUSH_OFFSET:CATCHUP_PUSH_OFFSET + 2]
        self.assertEqual(got, PUSH_4,
            msg=f"FPS_CATCHUP_PATCH bytes {CATCHUP_PUSH_OFFSET}..+2 should "
                f"be `PUSH 0x4` (6A 04) — the catchup-side copy of the "
                f"step cap that must match the TRUNC `CMP ESI, 4`.  Got "
                f"{got.hex()} instead.")

    def test_catchup_patch_does_not_contain_push_2(self):
        spec = _spec_by_va(CATCHUP_VA)
        self.assertNotIn(bytes([0x6A, 0x02]), spec.patch,
            msg="FPS_CATCHUP_PATCH contains `PUSH 0x2` — the low-cap "
                "value that causes slowdown below 30 FPS render.")

    def test_catchup_patch_has_two_fadd_st0_st0(self):
        """Two FADD ST0,ST0 instructions multiply dt by 4 (dt=1/60 → 1/15),
        matching cap=4 in the remainder computation.  Cap=2 would use
        only one FADD."""
        spec = _spec_by_va(CATCHUP_VA)
        count = spec.patch.count(FADD_ST0_ST0)
        self.assertEqual(count, 2,
            msg=f"FPS_CATCHUP_PATCH should contain exactly two `FADD ST0, "
                f"ST0` (DC C0) so the remainder computes raw_delta - 4*dt "
                f"to match cap=4, but found {count}.  If only one FADD "
                f"is present the remainder uses 2*dt which desyncs from "
                f"the TRUNC cap.")

    def test_trunc_and_catchup_blocks_preserve_original_lengths(self):
        # Patch lengths must match originals byte-for-byte or the XBE
        # section layout breaks.
        for spec in (_spec_by_va(TRUNC_VA), _spec_by_va(CATCHUP_VA)):
            self.assertEqual(
                len(spec.patch), len(spec.original),
                msg=f"{spec.label}: patch length {len(spec.patch)} != "
                    f"original length {len(spec.original)}; XBE patch "
                    f"would shift subsequent instructions.")


class PatchSpecIntegrity(unittest.TestCase):
    """General patch-list sanity — no accidental offset collisions, every
    safety-critical patch is also in the master list, etc."""

    def test_no_overlapping_patch_ranges(self):
        ranges = sorted((s.file_offset,
                         s.file_offset + len(s.patch),
                         s.label) for s in FPS_PATCH_SITES)
        for (lo1, hi1, lbl1), (lo2, hi2, lbl2) in zip(ranges, ranges[1:]):
            self.assertLessEqual(
                hi1, lo2,
                msg=f"Patch ranges overlap: {lbl1} [0x{lo1:X},0x{hi1:X}) "
                    f"and {lbl2} [0x{lo2:X},0x{hi2:X})")

    def test_every_patch_has_equal_length_original_and_patch(self):
        for spec in FPS_PATCH_SITES:
            self.assertEqual(
                len(spec.patch), len(spec.original),
                msg=f"{spec.label}: patch and original byte lengths differ "
                    f"({len(spec.patch)} vs {len(spec.original)}).")

    def test_safety_critical_sites_are_in_master_list(self):
        master_vas = {s.va for s in FPS_PATCH_SITES}
        for spec in FPS_SAFETY_CRITICAL_SITES:
            self.assertIn(spec.va, master_vas,
                msg=f"{spec.label} (VA=0x{spec.va:X}) is tagged "
                    f"safety-critical but not in FPS_PATCH_SITES.")


if __name__ == "__main__":
    unittest.main()
