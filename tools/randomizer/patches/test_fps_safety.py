"""Safety-critical invariants for the 60 FPS patch.

These tests pin the simulation step cap at 2 so it cannot silently drift
back to 4 during a future refactor.

Rationale: the shipped game caps itself at 2 sim calls per frame, so that
is the only reentrancy depth we have evidence the engine tolerates.  The
on-death BSOD that originally motivated the 4 -> 2 change has also been
reproduced on unpatched 30fps Azurik, so it is a pre-existing engine bug
and the cap was not its cause.  Raising the cap at 60fps would still be
the wrong direction because it would exceed vanilla reentrancy, so these
tests fail any patch that would push the cap above 2.

Run with:

    python -m unittest patches/test_fps_safety.py

(or just `python patches/test_fps_safety.py` — it falls back to unittest
main()).  The tests work entirely on static byte blobs; no XBE binary is
needed and they are safe in CI.
"""

from __future__ import annotations

import os
import sys
import unittest

# Make sure the `patches` package is importable when the test file is
# run directly (e.g. `python patches/test_fps_safety.py`).
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT   = os.path.dirname(_THIS_DIR)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from patches.fps_unlock import (  # noqa: E402
    FPS_PATCH_SITES,
    FPS_SAFETY_CRITICAL_SITES,
)


def _spec_by_va(va: int):
    for spec in FPS_PATCH_SITES:
        if spec.va == va:
            return spec
    raise AssertionError(f"No PatchSpec at VA 0x{va:X}")


# Canonical VAs for the two BSOD-guard patches.
TRUNC_VA   = 0x059AFD
CATCHUP_VA = 0x059B37

# The literal x86 byte sequence that encodes `CMP ESI, 2`.
CMP_ESI_2  = bytes([0x83, 0xFE, 0x02])

# `PUSH 0x2` — byte-equivalent substitute for `MOV ESI, 2` that keeps the
# catchup block at 30 bytes and sits at a fixed offset inside the patch.
PUSH_2     = bytes([0x6A, 0x02])

# Offset within FPS_CATCHUP_PATCH where PUSH 0x2 / POP ESI appears.
# Layout: FLD float ptr [0x1983E8] (6B), then PUSH 0x2 (2B), then POP ESI.
CATCHUP_PUSH_OFFSET = 6


class StepCapInvariants(unittest.TestCase):
    """Pin the BSOD guard — the step cap must stay at 2."""

    def test_safety_critical_sites_are_exactly_trunc_and_catchup(self):
        safety_vas = {s.va for s in FPS_SAFETY_CRITICAL_SITES}
        self.assertEqual(safety_vas, {TRUNC_VA, CATCHUP_VA},
            msg="safety-critical tag drifted; only the TRUNC (0x59AFD) and "
                "CATCHUP (0x59B37) patches should be tagged as "
                "safety-critical. Review any changes carefully — the tag "
                "pins the 60fps BSOD guard.")

    def test_trunc_patch_still_caps_at_2(self):
        spec = _spec_by_va(TRUNC_VA)
        count = spec.patch.count(CMP_ESI_2)
        self.assertEqual(count, 1,
            msg=f"FPS_TRUNC_PATCH should contain exactly one `CMP ESI, 2` "
                f"(the vanilla reentrancy guard) but found {count} "
                f"occurrences. If this failed because the cap was raised "
                f"to 4, DO NOT bypass this test — the shipped game never "
                f"drives the sim past 2 calls per frame, so cap=4 exceeds "
                f"every empirically-safe reentrancy depth.")

    def test_trunc_patch_does_not_contain_cmp_esi_4(self):
        spec = _spec_by_va(TRUNC_VA)
        self.assertNotIn(bytes([0x83, 0xFE, 0x04]), spec.patch,
            msg="FPS_TRUNC_PATCH contains `CMP ESI, 4` — the pre-BSOD-fix "
                "value.  The cap MUST stay at 2; see fps_unlock.py for the "
                "root-cause analysis.")

    def test_catchup_patch_push_2_at_fixed_offset(self):
        spec = _spec_by_va(CATCHUP_VA)
        got = spec.patch[CATCHUP_PUSH_OFFSET:CATCHUP_PUSH_OFFSET + 2]
        self.assertEqual(got, PUSH_2,
            msg=f"FPS_CATCHUP_PATCH bytes {CATCHUP_PUSH_OFFSET}..+2 should "
                f"be `PUSH 0x2` (6A 02) — this is the catchup-side copy "
                f"of the step-cap guard that must match the TRUNC "
                f"`CMP ESI, 2`.  Got {got.hex()} instead.  If this failed "
                f"because the cap was raised to 4, DO NOT bypass this "
                f"test.")

    def test_catchup_patch_does_not_contain_push_4(self):
        spec = _spec_by_va(CATCHUP_VA)
        self.assertNotIn(bytes([0x6A, 0x04]), spec.patch,
            msg="FPS_CATCHUP_PATCH contains `PUSH 0x4` — the pre-BSOD-fix "
                "value.  The cap MUST stay at 2.")

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
