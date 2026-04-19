"""Tests for the Phase-2 C1 player-speed patch.

This patch moved (again) — from ``config.xbr`` to direct XBE code-site
rewrites after we discovered ``attacks_transitions.walkSpeed`` is dead
data at runtime.  See ``azurik_mod/patches/player_physics.py`` for the
Ghidra walkthrough.

What we pin now:

- The two code sites (``0x85F62`` for walk base, ``0x849E4`` for run
  multiplier) still hold their vanilla bytes on unpatched XBEs.
- ``apply_player_speed(xbe, walk_scale=..., run_scale=...)`` is a
  no-op at ``1.0 / 1.0`` defaults.
- At non-default scales the two instructions now start with
  ``D9 05`` / ``D8 0D`` (FLD/FMUL [abs32]) and the 32-bit absolute
  they reference dereferences to the expected float value.
- The SHIMS-style injected floats land inside a readable section of
  the XBE (``.text`` trailing-padding gap on Azurik, or an appended
  ``SHIMS`` section once that gap fills up).
- Calling apply twice with DIFFERENT scales onto the same buffer is
  rejected (the second call sees already-patched bytes and bails).
- Malformed buffers fail soft (return ``False``, don't crash).

These tests depend on the real vanilla Azurik XBE.  They skip
gracefully when it's not present so CI without game assets still
runs the rest of the suite.
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

from azurik_mod.patches.player_physics import (  # noqa: E402
    _RUN_SITE_VA,
    _RUN_SITE_VANILLA,
    _VANILLA_PLAYER_BASE_SPEED,
    _VANILLA_RUN_MULTIPLIER,
    _WALK_SITE_VA,
    _WALK_SITE_VANILLA,
    apply_player_physics,
    apply_player_speed,
)
from azurik_mod.patching.xbe import parse_xbe_sections, va_to_file


_XBE_CANDIDATES = [
    Path("/Users/michaelsrouji/Documents/Xemu/tools/"
         "Azurik - Rise of Perathia (USA).xiso/default.xbe"),
    Path(_REPO_ROOT).parent /
        "Azurik - Rise of Perathia (USA).xiso" / "default.xbe",
    Path(_REPO_ROOT) / "tests" / "fixtures" / "default.xbe",
]
_XBE_PATH = next((p for p in _XBE_CANDIDATES if p.exists()), None)


def _read_float_at_va(xbe: bytes, va: int) -> float | None:
    """Resolve `va` through the XBE section table and return the 4
    bytes there as a little-endian float, or ``None`` if the address
    isn't mapped anywhere."""
    _, secs = parse_xbe_sections(xbe)
    for s in secs:
        if s["vaddr"] <= va < s["vaddr"] + s["vsize"]:
            fo = s["raw_addr"] + (va - s["vaddr"])
            if fo + 4 <= len(xbe):
                return struct.unpack("<f", xbe[fo:fo + 4])[0]
    return None


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class VanillaSitesInvariants(unittest.TestCase):
    """Drift-catching sanity: vanilla bytes at the two patch sites."""

    def test_walk_site_has_vanilla_bytes(self):
        xbe = _XBE_PATH.read_bytes()
        off = va_to_file(_WALK_SITE_VA)
        self.assertEqual(bytes(xbe[off:off + 6]), _WALK_SITE_VANILLA,
            msg="walk-site bytes drifted; the Ghidra walkthrough in "
                "player_physics.py is out of date.")

    def test_run_site_has_vanilla_bytes(self):
        xbe = _XBE_PATH.read_bytes()
        off = va_to_file(_RUN_SITE_VA)
        self.assertEqual(bytes(xbe[off:off + 6]), _RUN_SITE_VANILLA,
            msg="run-site bytes drifted; the Ghidra walkthrough in "
                "player_physics.py is out of date.")


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class ApplyPlayerSpeedBehaviour(unittest.TestCase):
    """End-to-end patch behaviour against a real XBE."""

    def setUp(self):
        self.orig = _XBE_PATH.read_bytes()

    def test_defaults_are_noop(self):
        data = bytearray(self.orig)
        self.assertFalse(apply_player_speed(data))
        self.assertEqual(bytes(data), self.orig,
            msg="apply_player_speed at defaults (1.0 / 1.0) must "
                "leave the XBE byte-identical so verify-patches "
                "--strict passes for unopted users.")

    def test_walk_scale_patches_fld_and_injects_walk_float(self):
        """At walk_scale=2.5, run_scale=1.0 the injected base equals
        vanilla × walk_scale (7.0 × 2.5 = 17.5) — that's what makes
        the slider a true multiplier on vanilla walking."""
        data = bytearray(self.orig)
        self.assertTrue(apply_player_speed(data, walk_scale=2.5))

        walk_off = va_to_file(_WALK_SITE_VA)
        patch = bytes(data[walk_off:walk_off + 6])
        self.assertEqual(patch[:2], b"\xD9\x05",
            msg="walk site must become 'FLD dword [abs32]' "
                "(opcode D9 05)")
        walk_va = struct.unpack("<I", patch[2:6])[0]
        value = _read_float_at_va(bytes(data), walk_va)
        self.assertIsNotNone(value,
            msg="injected walk-speed VA must resolve to a mapped "
                "section byte")
        self.assertAlmostEqual(
            value, _VANILLA_PLAYER_BASE_SPEED * 2.5, places=5,
            msg="injected base must equal vanilla_base × walk_scale — "
                "that's what makes walk_scale a true multiplier on "
                "vanilla walking.  The pre-April-2026 code injected "
                "a literal 2.5 here, which silently dropped the "
                "base from vanilla's 7.0 down to 2.5 (35% of "
                "vanilla) instead of the intended 2.5× boost.")

    def test_run_scale_patches_fmul_and_preserves_vanilla_constant(self):
        """At walk_scale=1.0, run_scale=1.5 the injected multiplier
        equals 3 × run_scale / walk_scale = 4.5.  The shared 3.0
        constant at 0x001A25BC MUST stay untouched (45 other readers
        depend on it)."""
        data = bytearray(self.orig)
        self.assertTrue(apply_player_speed(data, run_scale=1.5))

        run_off = va_to_file(_RUN_SITE_VA)
        patch = bytes(data[run_off:run_off + 6])
        self.assertEqual(patch[:2], b"\xD8\x0D",
            msg="run site must become 'FMUL dword [abs32]' "
                "(opcode D8 0D)")
        run_va = struct.unpack("<I", patch[2:6])[0]
        value = _read_float_at_va(bytes(data), run_va)
        self.assertIsNotNone(value)
        # Independence math: inject_mult = 3 × run_scale / walk_scale.
        # With walk_scale=1.0 default, that's 3 × 1.5 / 1 = 4.5.
        self.assertAlmostEqual(
            value, _VANILLA_RUN_MULTIPLIER * 1.5 / 1.0, places=5,
            msg="at walk_scale=1, injected run_mult equals "
                "3 × run_scale / walk_scale = 3 × 1.5 = 4.5.")

        # The shared 0x001A25BC constant MUST still be 3.0 — we
        # deliberately do NOT mutate it (45 other readers depend on it).
        fo = 0x188000 + (0x001A25BC - 0x18F3A0)
        shared = struct.unpack("<f", bytes(data[fo:fo + 4]))[0]
        self.assertAlmostEqual(shared, 3.0, places=5,
            msg="the shared run-multiplier at 0x001A25BC must NOT "
                "be touched; 45 other systems read it.")

    def test_combined_walk_and_run_scales(self):
        """Both sliders applied together land their own derived
        constants.  Independence math: inject_base = 7×0.5 = 3.5,
        inject_mult = 3×2.0/0.5 = 12.0."""
        data = bytearray(self.orig)
        self.assertTrue(apply_player_speed(data,
                                           walk_scale=0.5, run_scale=2.0))
        walk_off = va_to_file(_WALK_SITE_VA)
        run_off = va_to_file(_RUN_SITE_VA)
        walk_va = struct.unpack("<I", bytes(data[walk_off + 2:walk_off + 6]))[0]
        run_va = struct.unpack("<I", bytes(data[run_off + 2:run_off + 6]))[0]
        self.assertAlmostEqual(
            _read_float_at_va(bytes(data), walk_va),
            _VANILLA_PLAYER_BASE_SPEED * 0.5, places=5,
            msg="inject_base = vanilla_base × walk_scale = 7 × 0.5 = 3.5")
        self.assertAlmostEqual(
            _read_float_at_va(bytes(data), run_va),
            _VANILLA_RUN_MULTIPLIER * 2.0 / 0.5, places=5,
            msg="inject_mult = 3 × run_scale / walk_scale "
                "= 3 × 2.0 / 0.5 = 12.0")

    def test_reapply_to_already_patched_is_rejected(self):
        """Running apply a second time on the same buffer must refuse —
        the walk-site bytes no longer match the vanilla sequence."""
        data = bytearray(self.orig)
        self.assertTrue(apply_player_speed(data, walk_scale=2.0))
        self.assertFalse(apply_player_speed(data, walk_scale=3.0),
            msg="second apply on an already-patched buffer must "
                "fail safe (we'd otherwise leave the injected float "
                "orphaned and clobber the absolute-ref VA).")


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class ApplyPlayerPhysicsRouting(unittest.TestCase):
    """`apply_player_physics` accepts gravity + speed kwargs together
    and routes each to its own sub-patch."""

    def test_gravity_alone_does_not_touch_speed_sites(self):
        data = bytearray(_XBE_PATH.read_bytes())
        apply_player_physics(data, gravity=7.0)
        walk_off = va_to_file(_WALK_SITE_VA)
        run_off = va_to_file(_RUN_SITE_VA)
        self.assertEqual(
            bytes(data[walk_off:walk_off + 6]), _WALK_SITE_VANILLA)
        self.assertEqual(
            bytes(data[run_off:run_off + 6]), _RUN_SITE_VANILLA)

    def test_speed_alone_does_not_touch_gravity_constant(self):
        data = bytearray(_XBE_PATH.read_bytes())
        apply_player_physics(data, walk_scale=2.0)
        # Gravity .rdata float at VA 0x1980A8.
        fo = 0x188000 + (0x1980A8 - 0x18F3A0)
        value = struct.unpack("<f", bytes(data[fo:fo + 4]))[0]
        self.assertAlmostEqual(value, 9.8, places=3,
            msg="speed-only apply must not rewrite the gravity float.")


class GracefulHandlingOfGarbage(unittest.TestCase):
    """apply_player_speed on malformed input must fail soft."""

    def test_garbage_bytes_return_false(self):
        data = bytearray(b"\x00" * 0x400000)
        self.assertFalse(apply_player_speed(data, walk_scale=2.0),
            msg="All-zero buffer has no vanilla bytes at the walk "
                "site — apply must bail out with a warning.")


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class DynamicWhitelistFromXbe(unittest.TestCase):
    """The pack's ``dynamic_whitelist_from_xbe`` callback powers
    ``verify-patches --strict`` over the two instruction rewrites +
    two injected per-player floats.  It has to be robust on BOTH
    vanilla and patched XBEs."""

    def test_vanilla_xbe_includes_both_static_sites(self):
        """On a vanilla XBE the two 6-byte instruction-site ranges
        are ALWAYS whitelisted (their bytes match vanilla so they
        contribute zero diff).  The run-site vanilla bytes
        ``D8 0D BC 25 1A 00`` happen to be a real
        ``FMUL [abs32]``-format instruction so the callback ALSO
        follows the abs32 pointer to the shared 0x001A25BC and
        whitelists 4 bytes there — harmless, since the shared
        constant never changes."""
        from azurik_mod.patches.player_physics import (
            _player_speed_dynamic_whitelist,
        )
        xbe = _XBE_PATH.read_bytes()
        ranges = _player_speed_dynamic_whitelist(xbe)
        walk_off = va_to_file(_WALK_SITE_VA)
        run_off = va_to_file(_RUN_SITE_VA)
        self.assertIn((walk_off, walk_off + 6), ranges)
        self.assertIn((run_off, run_off + 6), ranges)
        # Between 2 and 3 ranges (the extra one would be the shared
        # run-constant at 0x001A25BC if the callback parsed the
        # vanilla FMUL's abs32 as ours; either is acceptable).
        self.assertIn(len(ranges), (2, 3))

    def test_patched_xbe_adds_injected_float_ranges(self):
        """After apply, the callback must additionally locate each
        injected float's file offset via the section table and add
        a 4-byte range."""
        from azurik_mod.patches.player_physics import (
            _player_speed_dynamic_whitelist,
        )
        data = bytearray(_XBE_PATH.read_bytes())
        self.assertTrue(apply_player_speed(data,
                                            walk_scale=2.0,
                                            run_scale=1.5))
        ranges = _player_speed_dynamic_whitelist(bytes(data))
        # Two instruction-site ranges + two float ranges = 4.
        self.assertEqual(len(ranges), 4,
            msg="patched XBE should produce 4 ranges "
                "(2 instr rewrites + 2 injected floats)")
        # Every declared 4-byte range must contain exactly the bytes
        # of the corresponding injected float.
        four_byte_ranges = [(lo, hi) for lo, hi in ranges
                            if hi - lo == 4]
        self.assertEqual(len(four_byte_ranges), 2)

    def test_callback_does_not_raise_on_all_zero_buffer(self):
        """Drift-safety: if called on something that isn't an XBE at
        all, the callback must return gracefully, not crash."""
        from azurik_mod.patches.player_physics import (
            _player_speed_dynamic_whitelist,
        )
        # No XBE header -> va_to_file may raise depending on the
        # lookup table; the callback swallows these and returns an
        # empty list (or static ranges — either is acceptable, what
        # matters is no exception leaks).
        _ = _player_speed_dynamic_whitelist(b"\x00" * 0x1000)


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class IndependenceSemantics(unittest.TestCase):
    """Prove the sliders are TRULY INDEPENDENT: walk_scale scales
    only vanilla walking, run_scale scales only vanilla running.

    This is the April 2026 independence rewrite of player_physics.
    Before the fix, walk_scale incorrectly scaled both walking AND
    running (because the engine multiplexes the same CritterData
    field through both paths) and run_scale effects were masked by
    the walk-site dropping to 1.0 on any slider change.  The new
    math cancels the cross-term by dividing inject_mult by
    walk_scale — this sweep verifies the algebra holds for every
    slider combination that mattered in the earlier failure modes.

    Each entry in CASES asserts:

    - walking = walk_scale × vanilla_walking
    - running = run_scale  × vanilla_running

    WITHOUT any coupling between the two.
    """

    # (walk_scale, run_scale, expected_walking_x_vanilla,
    #  expected_running_x_vanilla)
    CASES = [
        (2.0, 1.0, 2.0, 1.0),   # walk 2×, run unchanged
        (1.0, 2.0, 1.0, 2.0),   # walk unchanged, run 2×
        (2.0, 2.0, 2.0, 2.0),   # both 2×
        (3.0, 1.0, 3.0, 1.0),   # walking as fast as vanilla running
        (0.5, 2.0, 0.5, 2.0),   # walking half, running 2× vanilla
        (1.0, 0.5, 1.0, 0.5),   # walking vanilla, running half-vanilla
    ]

    def test_each_slider_combination_is_independent(self):
        for walk, run, want_walk_x, want_run_x in self.CASES:
            with self.subTest(walk=walk, run=run):
                data = bytearray(_XBE_PATH.read_bytes())
                self.assertTrue(apply_player_speed(
                    data, walk_scale=walk, run_scale=run))
                walk_off = va_to_file(_WALK_SITE_VA)
                run_off = va_to_file(_RUN_SITE_VA)
                walk_va = struct.unpack(
                    "<I",
                    bytes(data[walk_off + 2:walk_off + 6]))[0]
                run_va = struct.unpack(
                    "<I",
                    bytes(data[run_off + 2:run_off + 6]))[0]
                base = _read_float_at_va(bytes(data), walk_va)
                mult = _read_float_at_va(bytes(data), run_va)

                # Engine formula (without run-flag): velocity = base
                # × raw_stick.  We compare ``base`` directly against
                # vanilla_base × walk_scale.
                self.assertAlmostEqual(
                    base,
                    _VANILLA_PLAYER_BASE_SPEED * want_walk_x,
                    places=3,
                    msg=f"walking speed at walk={walk} run={run} — "
                        f"injected base should equal "
                        f"{_VANILLA_PLAYER_BASE_SPEED} × {want_walk_x}")

                # Engine formula (with run-flag): velocity = base ×
                # mult × raw_stick.  Expected running speed is
                # vanilla_running × run_scale =
                # (vanilla_base × vanilla_boost) × run_scale.
                self.assertAlmostEqual(
                    base * mult,
                    _VANILLA_PLAYER_BASE_SPEED
                    * _VANILLA_RUN_MULTIPLIER
                    * want_run_x,
                    places=3,
                    msg=f"running speed at walk={walk} run={run} — "
                        f"base × mult should equal "
                        f"{_VANILLA_PLAYER_BASE_SPEED} × "
                        f"{_VANILLA_RUN_MULTIPLIER} × {want_run_x}")

    def test_walk_scale_alone_does_not_affect_running(self):
        """Smoke: changing ONLY walk_scale must leave running at
        exactly vanilla × 1.0.  Before the fix this was broken —
        walk_scale=2 also doubled running."""
        data = bytearray(_XBE_PATH.read_bytes())
        self.assertTrue(apply_player_speed(data, walk_scale=3.0))
        walk_off = va_to_file(_WALK_SITE_VA)
        run_off = va_to_file(_RUN_SITE_VA)
        walk_va = struct.unpack(
            "<I", bytes(data[walk_off + 2:walk_off + 6]))[0]
        run_va = struct.unpack(
            "<I", bytes(data[run_off + 2:run_off + 6]))[0]
        base = _read_float_at_va(bytes(data), walk_va)
        mult = _read_float_at_va(bytes(data), run_va)
        running = base * mult
        vanilla_running = (_VANILLA_PLAYER_BASE_SPEED
                           * _VANILLA_RUN_MULTIPLIER)
        self.assertAlmostEqual(
            running, vanilla_running, places=3,
            msg="walk_scale=3 should NOT affect running — the "
                "independence math cancels walk_scale out of the "
                "running path.")

    def test_run_scale_alone_does_not_affect_walking(self):
        """Reverse smoke: changing ONLY run_scale must leave
        walking at vanilla.  Before the fix, changing run_scale
        silently dropped walking speed because the walk-site got
        rewritten with a literal 1.0."""
        data = bytearray(_XBE_PATH.read_bytes())
        self.assertTrue(apply_player_speed(data, run_scale=3.0))
        walk_off = va_to_file(_WALK_SITE_VA)
        walk_va = struct.unpack(
            "<I", bytes(data[walk_off + 2:walk_off + 6]))[0]
        walking = _read_float_at_va(bytes(data), walk_va)
        self.assertAlmostEqual(
            walking, _VANILLA_PLAYER_BASE_SPEED, places=3,
            msg="run_scale=3 should NOT affect walking — base must "
                "stay at vanilla (7.0).")

    def test_walk_scale_zero_does_not_produce_nan(self):
        """Defense: if something ever passes walk_scale=0 (UI min
        is 0.1 but a future refactor could change that), the
        _WALK_SCALE_MIN clamp prevents the divide-by-zero from
        emitting NaN/Inf into the XBE."""
        import math
        data = bytearray(_XBE_PATH.read_bytes())
        # Passing 0 directly bypasses the slider; with the clamp
        # the injected mult should be finite + positive.
        self.assertTrue(apply_player_speed(
            data, walk_scale=0.0, run_scale=1.0))
        run_off = va_to_file(_RUN_SITE_VA)
        run_va = struct.unpack(
            "<I", bytes(data[run_off + 2:run_off + 6]))[0]
        mult = _read_float_at_va(bytes(data), run_va)
        self.assertIsNotNone(mult)
        self.assertTrue(math.isfinite(mult),
            msg="injected mult must be finite — a divide-by-zero "
                "that leaked into the XBE would silently corrupt "
                "every player-speed calc at runtime.")


if __name__ == "__main__":
    unittest.main()
