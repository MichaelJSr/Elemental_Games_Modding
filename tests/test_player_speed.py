"""Tests for the Phase-2 C1 player-speed patch (walk + roll + swim).

This patch moved (again) — from ``config.xbr`` to direct XBE code-site
rewrites after we discovered ``attacks_transitions.walkSpeed`` is dead
data at runtime, and renamed ``run_scale`` → ``roll_scale`` in April
2026 once we confirmed the 3.0 multiplier at VA 0x001A25BC is gated
by the WHITE/BACK controller button (roll / dive / dodge), NOT a
"run" button.  See ``azurik_mod/patches/player_physics/__init__.py``
for the Ghidra walkthrough.

What we pin now:

- The three code sites (``0x85F62`` walk base, ``0x849E4`` roll
  multiplier, ``0x8B7BF`` swim multiplier) still hold their vanilla
  bytes on unpatched XBEs.
- ``apply_player_speed(xbe, walk_scale=..., roll_scale=...)`` is a
  no-op at ``1.0 / 1.0`` defaults.
- ``apply_swim_speed(xbe, swim_scale=...)`` is a no-op at ``1.0``.
- At non-default scales the instructions start with ``D9 05`` /
  ``D8 0D`` (FLD/FMUL [abs32]) and the 32-bit absolute dereferences
  to the expected float value.
- The SHIMS-style injected floats land inside a readable section of
  the XBE.
- Calling apply twice on the same buffer is rejected.
- Malformed buffers fail soft (return ``False``, don't crash).
- The legacy ``run_scale`` / ``run_speed_scale`` kwargs still map to
  the roll sliders (back-compat).

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
    _ROLL_SITE_VA,
    _ROLL_SITE_VANILLA,
    _SWIM_SITE_VA,
    _SWIM_SITE_VANILLA,
    _VANILLA_PLAYER_BASE_SPEED,
    _VANILLA_ROLL_MULTIPLIER,
    _VANILLA_SWIM_MULTIPLIER,
    _WALK_SITE_VA,
    _WALK_SITE_VANILLA,
    apply_player_physics,
    apply_player_speed,
    apply_swim_speed,
)
# Back-compat aliases — old name re-exported for callers that pinned
# the pre-April-2026 names.  These tests double as a contract that
# the renames preserve the old import surface.
from azurik_mod.patches.player_physics import (  # noqa: E402
    _RUN_SITE_VA,              # -> _ROLL_SITE_VA
    _RUN_SITE_VANILLA,         # -> _ROLL_SITE_VANILLA
    _VANILLA_RUN_MULTIPLIER,   # -> _VANILLA_ROLL_MULTIPLIER
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


class BackCompatAliases(unittest.TestCase):
    """Legacy ``_RUN_*`` / ``_VANILLA_RUN_MULTIPLIER`` imports must
    still resolve — back-compat for external tools that pinned the
    pre-April-2026 names."""

    def test_run_aliases_equal_roll_values(self):
        self.assertEqual(_RUN_SITE_VA, _ROLL_SITE_VA)
        self.assertEqual(_RUN_SITE_VANILLA, _ROLL_SITE_VANILLA)
        self.assertEqual(_VANILLA_RUN_MULTIPLIER,
                         _VANILLA_ROLL_MULTIPLIER)


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class VanillaSitesInvariants(unittest.TestCase):
    """Drift-catching sanity: vanilla bytes at all three patch sites."""

    def test_walk_site_has_vanilla_bytes(self):
        xbe = _XBE_PATH.read_bytes()
        off = va_to_file(_WALK_SITE_VA)
        self.assertEqual(bytes(xbe[off:off + 6]), _WALK_SITE_VANILLA,
            msg="walk-site bytes drifted; the Ghidra walkthrough in "
                "player_physics/__init__.py is out of date.")

    def test_roll_site_has_vanilla_bytes(self):
        xbe = _XBE_PATH.read_bytes()
        off = va_to_file(_ROLL_SITE_VA)
        self.assertEqual(bytes(xbe[off:off + 6]), _ROLL_SITE_VANILLA,
            msg="roll-site bytes drifted; the Ghidra walkthrough in "
                "player_physics/__init__.py is out of date.")

    def test_swim_site_has_vanilla_bytes(self):
        xbe = _XBE_PATH.read_bytes()
        off = va_to_file(_SWIM_SITE_VA)
        self.assertEqual(bytes(xbe[off:off + 6]), _SWIM_SITE_VANILLA,
            msg="swim-site bytes drifted.  Vanilla should be "
                "`FMUL [0x001A25B4]` = D8 0D B4 25 1A 00 inside "
                "FUN_0008b700 (swim state).")


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class ApplyPlayerSpeedBehaviour(unittest.TestCase):
    """End-to-end walk + roll patch behaviour against a real XBE."""

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
        """At walk_scale=2.5, roll_scale=1.0 the injected base equals
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
                "vanilla walking.")

    def test_roll_scale_patches_fmul_and_preserves_vanilla_constant(self):
        """At walk_scale=1.0, roll_scale=1.5 the injected multiplier
        equals 3 × roll_scale / walk_scale = 4.5.  The shared 3.0
        constant at 0x001A25BC MUST stay untouched (45 other readers
        depend on it)."""
        data = bytearray(self.orig)
        self.assertTrue(apply_player_speed(data, roll_scale=1.5))

        roll_off = va_to_file(_ROLL_SITE_VA)
        patch = bytes(data[roll_off:roll_off + 6])
        self.assertEqual(patch[:2], b"\xD8\x0D",
            msg="roll site must become 'FMUL dword [abs32]' "
                "(opcode D8 0D)")
        roll_va = struct.unpack("<I", patch[2:6])[0]
        value = _read_float_at_va(bytes(data), roll_va)
        self.assertIsNotNone(value)
        # Independence math: inject_roll_mult = 3 × roll_scale / walk_scale.
        # With walk_scale=1.0 default, that's 3 × 1.5 / 1 = 4.5.
        self.assertAlmostEqual(
            value, _VANILLA_ROLL_MULTIPLIER * 1.5 / 1.0, places=5,
            msg="at walk_scale=1, injected roll_mult equals "
                "3 × roll_scale / walk_scale = 3 × 1.5 = 4.5.")

        # The shared 0x001A25BC constant MUST still be 3.0 — we
        # deliberately do NOT mutate it (45 other readers depend on it).
        fo = 0x188000 + (0x001A25BC - 0x18F3A0)
        shared = struct.unpack("<f", bytes(data[fo:fo + 4]))[0]
        self.assertAlmostEqual(shared, 3.0, places=5,
            msg="the shared roll-multiplier at 0x001A25BC must NOT "
                "be touched; 45 other systems read it.")

    def test_combined_walk_and_roll_scales(self):
        """Both sliders applied together land their own derived
        constants.  Independence math: inject_base = 7×0.5 = 3.5,
        inject_roll_mult = 3×2.0/0.5 = 12.0."""
        data = bytearray(self.orig)
        self.assertTrue(apply_player_speed(
            data, walk_scale=0.5, roll_scale=2.0))
        walk_off = va_to_file(_WALK_SITE_VA)
        roll_off = va_to_file(_ROLL_SITE_VA)
        walk_va = struct.unpack(
            "<I", bytes(data[walk_off + 2:walk_off + 6]))[0]
        roll_va = struct.unpack(
            "<I", bytes(data[roll_off + 2:roll_off + 6]))[0]
        self.assertAlmostEqual(
            _read_float_at_va(bytes(data), walk_va),
            _VANILLA_PLAYER_BASE_SPEED * 0.5, places=5,
            msg="inject_base = vanilla_base × walk_scale = 7 × 0.5 = 3.5")
        self.assertAlmostEqual(
            _read_float_at_va(bytes(data), roll_va),
            _VANILLA_ROLL_MULTIPLIER * 2.0 / 0.5, places=5,
            msg="inject_roll_mult = 3 × roll_scale / walk_scale "
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

    def test_legacy_run_scale_kwarg_still_works(self):
        """Back-compat: the old ``run_scale`` kwarg must still
        route to the roll multiplier.  Ensures users with pinned
        CLI flags / serialized configs don't break on first
        upgrade."""
        data_legacy = bytearray(self.orig)
        self.assertTrue(apply_player_speed(data_legacy, run_scale=2.0))
        data_new = bytearray(self.orig)
        self.assertTrue(apply_player_speed(data_new, roll_scale=2.0))
        self.assertEqual(bytes(data_legacy), bytes(data_new),
            msg="run_scale=X must produce the exact same bytes as "
                "roll_scale=X — otherwise the legacy kwarg silently "
                "diverges.")


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class ApplySwimSpeedBehaviour(unittest.TestCase):
    """Swim slider is a dedicated site (FUN_0008b700 inside the
    swim state) patched independently of walk + roll.  Verify the
    byte rewrite + injected float resolve correctly."""

    def setUp(self):
        self.orig = _XBE_PATH.read_bytes()

    def test_default_is_noop(self):
        data = bytearray(self.orig)
        self.assertFalse(apply_swim_speed(data))
        self.assertEqual(bytes(data), self.orig,
            msg="swim_scale=1.0 must leave the XBE byte-identical.")

    def test_swim_scale_2_injects_20(self):
        """swim_scale=2.0 should inject 10.0 × 2.0 = 20.0.  The
        vanilla swim stroke is ``magnitude × 10.0`` per frame, so
        doubling swim_scale doubles the stroke distance."""
        data = bytearray(self.orig)
        self.assertTrue(apply_swim_speed(data, swim_scale=2.0))
        swim_off = va_to_file(_SWIM_SITE_VA)
        patch = bytes(data[swim_off:swim_off + 6])
        self.assertEqual(patch[:2], b"\xD8\x0D",
            msg="swim site must become 'FMUL dword [abs32]' "
                "(opcode D8 0D).")
        swim_va = struct.unpack("<I", patch[2:6])[0]
        value = _read_float_at_va(bytes(data), swim_va)
        self.assertIsNotNone(value,
            msg="injected swim-speed VA must resolve to a mapped "
                "section byte.")
        self.assertAlmostEqual(
            value, _VANILLA_SWIM_MULTIPLIER * 2.0, places=5,
            msg="inject_swim_mult = vanilla_swim × swim_scale "
                "= 10 × 2 = 20.")

    def test_swim_scale_does_not_touch_shared_constant(self):
        """The shared 10.0 at 0x001A25B4 has 8 readers (most not
        player-related); we must leave it alone."""
        data = bytearray(self.orig)
        self.assertTrue(apply_swim_speed(data, swim_scale=3.0))
        fo = 0x188000 + (0x001A25B4 - 0x18F3A0)
        shared = struct.unpack("<f", bytes(data[fo:fo + 4]))[0]
        self.assertAlmostEqual(shared, 10.0, places=5,
            msg="shared 10.0 at 0x001A25B4 must NOT be modified; "
                "8 external readers depend on it.")

    def test_swim_scale_does_not_touch_walk_or_roll_sites(self):
        """Site isolation: swim apply must rewrite ONLY the
        FUN_0008b700 FMUL, never the walk FLD or roll FMUL."""
        data = bytearray(self.orig)
        self.assertTrue(apply_swim_speed(data, swim_scale=5.0))
        walk_off = va_to_file(_WALK_SITE_VA)
        roll_off = va_to_file(_ROLL_SITE_VA)
        self.assertEqual(
            bytes(data[walk_off:walk_off + 6]), _WALK_SITE_VANILLA)
        self.assertEqual(
            bytes(data[roll_off:roll_off + 6]), _ROLL_SITE_VANILLA)

    def test_reapply_rejected(self):
        data = bytearray(self.orig)
        self.assertTrue(apply_swim_speed(data, swim_scale=2.0))
        self.assertFalse(apply_swim_speed(data, swim_scale=3.0),
            msg="second swim apply must fail soft on drifted bytes.")


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class ApplyPlayerPhysicsRouting(unittest.TestCase):
    """`apply_player_physics` accepts gravity + walk + roll + swim
    kwargs together and routes each to its own sub-patch."""

    def test_gravity_alone_does_not_touch_speed_sites(self):
        data = bytearray(_XBE_PATH.read_bytes())
        apply_player_physics(data, gravity=7.0)
        for va, vanilla in (
            (_WALK_SITE_VA, _WALK_SITE_VANILLA),
            (_ROLL_SITE_VA, _ROLL_SITE_VANILLA),
            (_SWIM_SITE_VA, _SWIM_SITE_VANILLA),
        ):
            off = va_to_file(va)
            self.assertEqual(bytes(data[off:off + 6]), vanilla,
                msg=f"gravity-only apply must not touch VA 0x{va:X}")

    def test_speed_alone_does_not_touch_gravity_constant(self):
        data = bytearray(_XBE_PATH.read_bytes())
        apply_player_physics(data, walk_scale=2.0)
        # Gravity .rdata float at VA 0x1980A8.
        fo = 0x188000 + (0x1980A8 - 0x18F3A0)
        value = struct.unpack("<f", bytes(data[fo:fo + 4]))[0]
        self.assertAlmostEqual(value, 9.8, places=3,
            msg="speed-only apply must not rewrite the gravity float.")

    def test_swim_kwarg_routes_through_dispatcher(self):
        """Ensure `apply_player_physics(swim_scale=...)` actually
        lands the swim patch (not silently ignored)."""
        data = bytearray(_XBE_PATH.read_bytes())
        apply_player_physics(data, swim_scale=2.0)
        swim_off = va_to_file(_SWIM_SITE_VA)
        patch = bytes(data[swim_off:swim_off + 6])
        self.assertEqual(patch[:2], b"\xD8\x0D",
            msg="apply_player_physics(swim_scale=...) must rewrite "
                "the swim FMUL site.")

    def test_legacy_run_scale_routes_to_roll(self):
        """apply_player_physics(run_scale=X) must behave identically
        to apply_player_physics(roll_scale=X)."""
        data_legacy = bytearray(_XBE_PATH.read_bytes())
        data_new = bytearray(_XBE_PATH.read_bytes())
        apply_player_physics(data_legacy, run_scale=2.0)
        apply_player_physics(data_new, roll_scale=2.0)
        self.assertEqual(bytes(data_legacy), bytes(data_new))


class GracefulHandlingOfGarbage(unittest.TestCase):
    """apply_* on malformed input must fail soft."""

    def test_garbage_bytes_return_false_walk(self):
        data = bytearray(b"\x00" * 0x400000)
        self.assertFalse(apply_player_speed(data, walk_scale=2.0))

    def test_garbage_bytes_return_false_swim(self):
        data = bytearray(b"\x00" * 0x400000)
        self.assertFalse(apply_swim_speed(data, swim_scale=2.0))


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class DynamicWhitelistFromXbe(unittest.TestCase):
    """The pack's ``dynamic_whitelist_from_xbe`` callback powers
    ``verify-patches --strict`` over the THREE instruction rewrites
    (walk, roll, swim) + up to three injected per-player floats."""

    def test_vanilla_xbe_includes_all_three_static_sites(self):
        """On a vanilla XBE the three 6-byte instruction-site ranges
        are ALWAYS whitelisted.  The roll + swim sites look like
        ``FMUL [abs32]`` natively so the callback also follows each
        abs32 and whitelists a 4-byte range at the shared constants
        (harmless — those never change)."""
        from azurik_mod.patches.player_physics import (
            _player_speed_dynamic_whitelist,
        )
        xbe = _XBE_PATH.read_bytes()
        ranges = _player_speed_dynamic_whitelist(xbe)
        walk_off = va_to_file(_WALK_SITE_VA)
        roll_off = va_to_file(_ROLL_SITE_VA)
        swim_off = va_to_file(_SWIM_SITE_VA)
        self.assertIn((walk_off, walk_off + 6), ranges)
        self.assertIn((roll_off, roll_off + 6), ranges)
        self.assertIn((swim_off, swim_off + 6), ranges)
        # 3 instr sites + up to 2 extra 4-byte ranges (shared roll
        # constant + shared swim constant) = 3..5.
        self.assertIn(len(ranges), (3, 4, 5),
            msg=f"unexpected range count {len(ranges)}: {ranges}")

    def test_patched_xbe_adds_injected_float_ranges(self):
        """After apply, the callback must additionally locate each
        injected float's file offset via the section table and add
        4-byte ranges."""
        from azurik_mod.patches.player_physics import (
            _player_speed_dynamic_whitelist,
        )
        data = bytearray(_XBE_PATH.read_bytes())
        self.assertTrue(apply_player_speed(
            data, walk_scale=2.0, roll_scale=1.5))
        self.assertTrue(apply_swim_speed(data, swim_scale=2.0))
        ranges = _player_speed_dynamic_whitelist(bytes(data))
        # Three instruction-site ranges + three float ranges = 6.
        four_byte_ranges = [(lo, hi) for lo, hi in ranges
                            if hi - lo == 4]
        six_byte_ranges = [(lo, hi) for lo, hi in ranges
                           if hi - lo == 6]
        self.assertEqual(len(six_byte_ranges), 3,
            msg="3 instruction-site rewrites")
        self.assertEqual(len(four_byte_ranges), 3,
            msg="3 injected float ranges (walk base + roll mult + "
                "swim mult)")

    def test_callback_does_not_raise_on_all_zero_buffer(self):
        """Drift-safety: if called on something that isn't an XBE at
        all, the callback must return gracefully, not crash."""
        from azurik_mod.patches.player_physics import (
            _player_speed_dynamic_whitelist,
        )
        _ = _player_speed_dynamic_whitelist(b"\x00" * 0x1000)


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class IndependenceSemantics(unittest.TestCase):
    """Prove the sliders are TRULY INDEPENDENT: walk_scale scales
    only vanilla walking, roll_scale scales only vanilla rolling,
    swim_scale scales only vanilla swimming.

    This is the April 2026 independence rewrite of player_physics.
    Before the fix, walk_scale incorrectly scaled both walking AND
    rolling (because the engine multiplexes the same CritterData
    field through both paths) and roll_scale (then called
    ``run_scale``) effects were masked by the walk-site dropping to
    1.0 on any slider change.  The new math cancels the cross-term
    by dividing inject_roll_mult by walk_scale — this sweep
    verifies the algebra holds for every slider combination that
    mattered in the earlier failure modes.  Swim is already
    independent by construction (separate site, separate constant).

    Each entry in CASES asserts:

    - walking = walk_scale × vanilla_walking
    - rolling = roll_scale × vanilla_rolling

    WITHOUT any coupling between the two.
    """

    # (walk_scale, roll_scale, expected_walking_x_vanilla,
    #  expected_rolling_x_vanilla)
    CASES = [
        (2.0, 1.0, 2.0, 1.0),   # walk 2×, roll unchanged
        (1.0, 2.0, 1.0, 2.0),   # walk unchanged, roll 2×
        (2.0, 2.0, 2.0, 2.0),   # both 2×
        (3.0, 1.0, 3.0, 1.0),   # walking as fast as vanilla rolling
        (0.5, 2.0, 0.5, 2.0),   # walking half, rolling 2× vanilla
        (1.0, 0.5, 1.0, 0.5),   # walking vanilla, rolling half-vanilla
    ]

    def test_each_slider_combination_is_independent(self):
        for walk, roll, want_walk_x, want_roll_x in self.CASES:
            with self.subTest(walk=walk, roll=roll):
                data = bytearray(_XBE_PATH.read_bytes())
                self.assertTrue(apply_player_speed(
                    data, walk_scale=walk, roll_scale=roll))
                walk_off = va_to_file(_WALK_SITE_VA)
                roll_off = va_to_file(_ROLL_SITE_VA)
                walk_va = struct.unpack(
                    "<I",
                    bytes(data[walk_off + 2:walk_off + 6]))[0]
                roll_va = struct.unpack(
                    "<I",
                    bytes(data[roll_off + 2:roll_off + 6]))[0]
                base = _read_float_at_va(bytes(data), walk_va)
                mult = _read_float_at_va(bytes(data), roll_va)

                # Engine formula (without roll-flag): velocity = base
                # × raw_stick.  Compare ``base`` directly against
                # vanilla_base × walk_scale.
                self.assertAlmostEqual(
                    base,
                    _VANILLA_PLAYER_BASE_SPEED * want_walk_x,
                    places=3,
                    msg=f"walking at walk={walk} roll={roll} — "
                        f"injected base should equal "
                        f"{_VANILLA_PLAYER_BASE_SPEED} × {want_walk_x}")

                # Engine formula (with roll-flag): velocity = base ×
                # mult × raw_stick.  Expected rolling speed is
                # vanilla_rolling × roll_scale =
                # (vanilla_base × vanilla_boost) × roll_scale.
                self.assertAlmostEqual(
                    base * mult,
                    _VANILLA_PLAYER_BASE_SPEED
                    * _VANILLA_ROLL_MULTIPLIER
                    * want_roll_x,
                    places=3,
                    msg=f"rolling at walk={walk} roll={roll} — "
                        f"base × mult should equal "
                        f"{_VANILLA_PLAYER_BASE_SPEED} × "
                        f"{_VANILLA_ROLL_MULTIPLIER} × {want_roll_x}")

    def test_walk_scale_alone_does_not_affect_rolling(self):
        """Smoke: changing ONLY walk_scale must leave rolling at
        exactly vanilla × 1.0.  Before the fix this was broken —
        walk_scale=2 also doubled rolling."""
        data = bytearray(_XBE_PATH.read_bytes())
        self.assertTrue(apply_player_speed(data, walk_scale=3.0))
        walk_off = va_to_file(_WALK_SITE_VA)
        roll_off = va_to_file(_ROLL_SITE_VA)
        walk_va = struct.unpack(
            "<I", bytes(data[walk_off + 2:walk_off + 6]))[0]
        roll_va = struct.unpack(
            "<I", bytes(data[roll_off + 2:roll_off + 6]))[0]
        base = _read_float_at_va(bytes(data), walk_va)
        mult = _read_float_at_va(bytes(data), roll_va)
        rolling = base * mult
        vanilla_rolling = (_VANILLA_PLAYER_BASE_SPEED
                           * _VANILLA_ROLL_MULTIPLIER)
        self.assertAlmostEqual(
            rolling, vanilla_rolling, places=3,
            msg="walk_scale=3 should NOT affect rolling — the "
                "independence math cancels walk_scale out of the "
                "rolling path.")

    def test_roll_scale_alone_does_not_affect_walking(self):
        """Reverse smoke: changing ONLY roll_scale must leave
        walking at vanilla.  Before the fix, changing roll_scale
        silently dropped walking speed because the walk-site got
        rewritten with a literal 1.0."""
        data = bytearray(_XBE_PATH.read_bytes())
        self.assertTrue(apply_player_speed(data, roll_scale=3.0))
        walk_off = va_to_file(_WALK_SITE_VA)
        walk_va = struct.unpack(
            "<I", bytes(data[walk_off + 2:walk_off + 6]))[0]
        walking = _read_float_at_va(bytes(data), walk_va)
        self.assertAlmostEqual(
            walking, _VANILLA_PLAYER_BASE_SPEED, places=3,
            msg="roll_scale=3 should NOT affect walking — base must "
                "stay at vanilla (7.0).")

    def test_swim_scale_alone_does_not_affect_walking_or_rolling(self):
        """Swim is a dedicated state function — its slider must
        leave both walk-site bytes and roll-site bytes at
        vanilla."""
        data = bytearray(_XBE_PATH.read_bytes())
        self.assertTrue(apply_swim_speed(data, swim_scale=5.0))
        walk_off = va_to_file(_WALK_SITE_VA)
        roll_off = va_to_file(_ROLL_SITE_VA)
        self.assertEqual(
            bytes(data[walk_off:walk_off + 6]), _WALK_SITE_VANILLA,
            msg="swim_scale must not rewrite walk-site bytes.")
        self.assertEqual(
            bytes(data[roll_off:roll_off + 6]), _ROLL_SITE_VANILLA,
            msg="swim_scale must not rewrite roll-site bytes.")

    def test_walk_roll_swim_fully_independent(self):
        """All three sliders applied together — every rewritten
        site's injected float must equal its own independent
        formula, with no cross-coupling."""
        data = bytearray(_XBE_PATH.read_bytes())
        walk, roll, swim = 2.0, 0.5, 3.0
        self.assertTrue(apply_player_speed(
            data, walk_scale=walk, roll_scale=roll))
        self.assertTrue(apply_swim_speed(data, swim_scale=swim))

        walk_off = va_to_file(_WALK_SITE_VA)
        roll_off = va_to_file(_ROLL_SITE_VA)
        swim_off = va_to_file(_SWIM_SITE_VA)
        walk_va = struct.unpack(
            "<I", bytes(data[walk_off + 2:walk_off + 6]))[0]
        roll_va = struct.unpack(
            "<I", bytes(data[roll_off + 2:roll_off + 6]))[0]
        swim_va = struct.unpack(
            "<I", bytes(data[swim_off + 2:swim_off + 6]))[0]

        # walk: inject_base = 7 × 2 = 14
        self.assertAlmostEqual(
            _read_float_at_va(bytes(data), walk_va),
            _VANILLA_PLAYER_BASE_SPEED * walk, places=3)
        # roll: inject_roll_mult = 3 × 0.5 / 2 = 0.75
        self.assertAlmostEqual(
            _read_float_at_va(bytes(data), roll_va),
            _VANILLA_ROLL_MULTIPLIER * roll / walk, places=3)
        # swim: inject_swim_mult = 10 × 3 = 30
        self.assertAlmostEqual(
            _read_float_at_va(bytes(data), swim_va),
            _VANILLA_SWIM_MULTIPLIER * swim, places=3)

    def test_walk_scale_zero_does_not_produce_nan(self):
        """Defense: if something ever passes walk_scale=0 (UI min
        is 0.1 but a future refactor could change that), the
        _WALK_SCALE_MIN clamp prevents the divide-by-zero from
        emitting NaN/Inf into the XBE."""
        import math
        data = bytearray(_XBE_PATH.read_bytes())
        self.assertTrue(apply_player_speed(
            data, walk_scale=0.0, roll_scale=1.0))
        roll_off = va_to_file(_ROLL_SITE_VA)
        roll_va = struct.unpack(
            "<I", bytes(data[roll_off + 2:roll_off + 6]))[0]
        mult = _read_float_at_va(bytes(data), roll_va)
        self.assertIsNotNone(mult)
        self.assertTrue(math.isfinite(mult),
            msg="injected mult must be finite — a divide-by-zero "
                "that leaked into the XBE would silently corrupt "
                "every player-speed calc at runtime.")


if __name__ == "__main__":
    unittest.main()
