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
    _JUMP_SITE_VA,
    _JUMP_SITE_VANILLA,
    _ROLL_FORCE_ON_1_PATCH,
    _ROLL_FORCE_ON_1_VA,
    _ROLL_FORCE_ON_1_VANILLA,
    _ROLL_FORCE_ON_2_PATCH,
    _ROLL_FORCE_ON_2_VA,
    _ROLL_FORCE_ON_2_VANILLA,
    _ROLL_SITE_VA,
    _ROLL_SITE_VANILLA,
    _SWIM_SITE_VA,
    _SWIM_SITE_VANILLA,
    _VANILLA_JUMP_GRAVITY,
    _VANILLA_PLAYER_BASE_SPEED,
    _VANILLA_ROLL_MULTIPLIER,
    _VANILLA_SWIM_MULTIPLIER,
    _WALK_SITE_VA,
    _WALK_SITE_VANILLA,
    apply_jump_speed,
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
        """v2 simplified semantics: inject_roll_mult = roll_scale
        (not 3 × roll_scale / walk_scale).  The force-always-on
        patches make the FMUL fire every frame of movement, so
        roll_scale acts as a permanent walking-speed multiplier.
        The shared 3.0 constant at 0x001A25BC MUST stay untouched
        (45 other readers depend on it)."""
        data = bytearray(self.orig)
        self.assertTrue(apply_player_speed(data, roll_scale=1.5))

        roll_off = va_to_file(_ROLL_SITE_VA)
        patch = bytes(data[roll_off:roll_off + 6])
        self.assertEqual(patch[:2], b"\xD8\x0D",
            msg="roll site must become 'FMUL dword [abs32]'")
        roll_va = struct.unpack("<I", patch[2:6])[0]
        value = _read_float_at_va(bytes(data), roll_va)
        self.assertIsNotNone(value)
        self.assertAlmostEqual(value, 1.5, places=5,
            msg="inject_roll_mult = roll_scale (v2 simplified)")

        # The shared 0x001A25BC constant MUST still be 3.0.
        fo = 0x188000 + (0x001A25BC - 0x18F3A0)
        shared = struct.unpack("<f", bytes(data[fo:fo + 4]))[0]
        self.assertAlmostEqual(shared, 3.0, places=5,
            msg="shared roll-multiplier at 0x001A25BC must NOT "
                "be touched; 45 other systems read it.")

    def test_combined_walk_and_roll_scales(self):
        """v2: both sliders are simple multipliers that STACK to
        give ``walk × roll`` total walking-speed multiplier."""
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
            msg="inject_base = 7 × walk_scale = 3.5")
        self.assertAlmostEqual(
            _read_float_at_va(bytes(data), roll_va),
            2.0, places=5,
            msg="inject_roll_mult = roll_scale = 2.0")

    def test_reapply_to_already_patched_is_rejected(self):
        """Running apply a second time on the same buffer must refuse —
        the walk-site bytes no longer match the vanilla sequence."""
        data = bytearray(self.orig)
        self.assertTrue(apply_player_speed(data, walk_scale=2.0))
        self.assertFalse(apply_player_speed(data, walk_scale=3.0),
            msg="second apply on an already-patched buffer must "
                "fail safe (we'd otherwise leave the injected float "
                "orphaned and clobber the absolute-ref VA).")

    def test_roll_scale_nops_white_edge_lock(self):
        """April 2026: when roll_scale != 1.0, apply_player_speed
        also NOPs the 2-byte ``JNZ +8`` at VA 0x00085200 inside
        FUN_00084f90.  That NOP removes the WHITE-button edge-lock,
        letting the 3× roll boost fire every frame WHITE is held
        rather than just the first frame of a tap.  Without this
        NOP, ``roll_scale`` is effectively invisible in sustained
        gameplay since WHITE-held resets the flag on frame 2+."""
        from azurik_mod.patches.player_physics import (
            _ROLL_EDGE_LOCK_PATCH,
            _ROLL_EDGE_LOCK_VA,
            _ROLL_EDGE_LOCK_VANILLA,
        )
        # roll_scale=1.0 must leave the edge-lock alone.
        data = bytearray(self.orig)
        self.assertTrue(apply_player_speed(data, walk_scale=2.0,
                                            roll_scale=1.0))
        off = va_to_file(_ROLL_EDGE_LOCK_VA)
        self.assertEqual(bytes(data[off:off + 2]),
                         _ROLL_EDGE_LOCK_VANILLA,
            msg="roll_scale=1.0 must not NOP the edge-lock")

        # roll_scale != 1.0 must NOP the edge-lock.
        data2 = bytearray(self.orig)
        self.assertTrue(apply_player_speed(data2, roll_scale=2.0))
        self.assertEqual(bytes(data2[off:off + 2]),
                         _ROLL_EDGE_LOCK_PATCH,
            msg="roll_scale=2.0 must NOP the edge-lock so WHITE-"
                "held gives sustained boost")

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
class ApplyJumpSpeedBehaviour(unittest.TestCase):
    """Jump slider rewrites the 6-byte ``FLD [0x001980A8]`` at VA
    0x89160 (inside FUN_00089060's ``v₀ = sqrt(2gh)`` formula) to
    load from an injected ``9.8 × jump_scale²`` constant instead.
    The resulting initial jump velocity scales linearly by
    ``jump_scale`` and peak jump height scales quadratically
    (because ``max_h = v₀² / (2g)``)."""

    def setUp(self):
        self.orig = _XBE_PATH.read_bytes()

    def test_default_is_noop(self):
        data = bytearray(self.orig)
        self.assertFalse(apply_jump_speed(data))
        self.assertEqual(bytes(data), self.orig,
            msg="jump_scale=1.0 must leave the XBE byte-identical.")

    def test_vanilla_bytes_are_fld_of_gravity(self):
        """Drift check: the site must currently hold the 6-byte
        ``FLD dword [0x001980A8]`` instruction that loads the
        gravity constant into the FPU stack in FUN_00089060."""
        off = va_to_file(_JUMP_SITE_VA)
        current = bytes(self.orig[off:off + 6])
        self.assertEqual(current, _JUMP_SITE_VANILLA,
            msg=f"jump site at VA 0x{_JUMP_SITE_VA:X} drifted "
                f"from vanilla FLD [0x001980A8] (got "
                f"{current.hex()})")

    def test_scale_2_injects_9_8_times_4(self):
        """At jump_scale=2, the injected constant is
        ``9.8 × 2² = 39.2``.  The SQRT then produces
        ``sqrt(2 × 39.2 × h) = 2 × sqrt(2gh)`` — doubled initial
        jump velocity."""
        data = bytearray(self.orig)
        self.assertTrue(apply_jump_speed(data, jump_scale=2.0))
        off = va_to_file(_JUMP_SITE_VA)
        patch = bytes(data[off:off + 6])
        self.assertEqual(patch[:2], b"\xD9\x05",
            msg="jump site must become FLD [abs32] (opcode D9 05)")
        inject_va = struct.unpack("<I", patch[2:6])[0]
        # Resolve inject_va to file offset via the section table.
        from azurik_mod.patching.xbe import parse_xbe_sections
        _, secs = parse_xbe_sections(bytes(data))
        inject_fo = None
        for s in secs:
            if s["vaddr"] <= inject_va < s["vaddr"] + s["vsize"]:
                inject_fo = s["raw_addr"] + (inject_va - s["vaddr"])
                break
        self.assertIsNotNone(inject_fo,
            msg=f"injected jump VA 0x{inject_va:X} not in any "
                f"mapped section")
        value = struct.unpack("<f",
                              bytes(data[inject_fo:inject_fo + 4]))[0]
        self.assertAlmostEqual(
            value, _VANILLA_JUMP_GRAVITY * 2.0 ** 2, places=3,
            msg=f"inject value should be 9.8 × 2² = 39.2, got {value}")

    def test_scale_05_injects_9_8_times_025(self):
        """At jump_scale=0.5, inject = 9.8 × 0.25 = 2.45, so vz =
        sqrt(2 × 2.45 × h) = 0.5 × vanilla_vz."""
        data = bytearray(self.orig)
        self.assertTrue(apply_jump_speed(data, jump_scale=0.5))
        off = va_to_file(_JUMP_SITE_VA)
        inject_va = struct.unpack("<I",
                                  bytes(data[off + 2:off + 6]))[0]
        from azurik_mod.patching.xbe import parse_xbe_sections
        _, secs = parse_xbe_sections(bytes(data))
        inject_fo = None
        for s in secs:
            if s["vaddr"] <= inject_va < s["vaddr"] + s["vsize"]:
                inject_fo = s["raw_addr"] + (inject_va - s["vaddr"])
                break
        self.assertIsNotNone(inject_fo)
        value = struct.unpack("<f",
                              bytes(data[inject_fo:inject_fo + 4]))[0]
        self.assertAlmostEqual(
            value, _VANILLA_JUMP_GRAVITY * 0.25, places=3)

    def test_gravity_constant_is_untouched(self):
        """Critical: the shared gravity float at VA 0x001980A8
        must NOT be modified by apply_jump_speed.  Every falling
        object in the engine reads that constant per-frame; if
        jump_scale perturbs it, gravity for the whole world
        changes."""
        data = bytearray(self.orig)
        self.assertTrue(apply_jump_speed(data, jump_scale=5.0))
        grav_off = va_to_file(0x001980A8)
        value = struct.unpack("<f",
                              bytes(data[grav_off:grav_off + 4]))[0]
        self.assertAlmostEqual(value, 9.8, places=3,
            msg="shared gravity at VA 0x001980A8 must NOT be "
                "modified by jump_scale — the gravity slider "
                "owns it.")

    def test_reapply_refused(self):
        data = bytearray(self.orig)
        self.assertTrue(apply_jump_speed(data, jump_scale=2.0))
        self.assertFalse(apply_jump_speed(data, jump_scale=3.0),
            msg="second apply on already-patched buffer must "
                "refuse (bytes no longer match vanilla FLD).")

    def test_does_not_touch_walk_roll_swim_sites(self):
        """Site isolation — jump apply must leave the walk / roll /
        swim instruction bytes at vanilla."""
        data = bytearray(self.orig)
        self.assertTrue(apply_jump_speed(data, jump_scale=3.0))
        for va, vanilla in (
            (_WALK_SITE_VA, _WALK_SITE_VANILLA),
            (_ROLL_SITE_VA, _ROLL_SITE_VANILLA),
            (_SWIM_SITE_VA, _SWIM_SITE_VANILLA),
        ):
            off = va_to_file(va)
            self.assertEqual(bytes(data[off:off + 6]), vanilla,
                msg=f"jump apply touched VA 0x{va:X}")

    def test_apply_player_physics_routes_jump(self):
        """High-level apply_player_physics(jump_scale=X) must
        rewrite the FLD site via apply_jump_speed."""
        data = bytearray(self.orig)
        apply_player_physics(data, jump_scale=1.5)
        off = va_to_file(_JUMP_SITE_VA)
        patch = bytes(data[off:off + 6])
        self.assertEqual(patch[:2], b"\xD9\x05",
            msg="apply_player_physics(jump_scale=...) must "
                "rewrite the FLD site")


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class RollForceAlwaysOn(unittest.TestCase):
    """April 2026 v2: when roll_scale != 1.0, apply_player_speed
    additionally installs two 2-byte patches that force bit 0x40
    of the input-state flags to always be set — so the
    roll-FMUL's injected multiplier fires every frame of
    movement regardless of whether the user presses WHITE or
    R3 (some xemu input configurations don't route those
    buttons at all, which previously made roll_scale invisible).
    """

    def test_roll_1_leaves_force_on_sites_intact(self):
        """Identity: at roll=1.0 we short-circuit and leave the
        force-on XOR-update tail untouched (vanilla WHITE/R3
        gating preserved)."""
        data = bytearray(_XBE_PATH.read_bytes())
        self.assertTrue(apply_player_speed(data, walk_scale=2.0,
                                            roll_scale=1.0))
        f1 = va_to_file(_ROLL_FORCE_ON_1_VA)
        f2 = va_to_file(_ROLL_FORCE_ON_2_VA)
        self.assertEqual(bytes(data[f1:f1 + 2]),
                         _ROLL_FORCE_ON_1_VANILLA,
            msg="roll=1 must leave force-on site 1 at vanilla")
        self.assertEqual(bytes(data[f2:f2 + 2]),
                         _ROLL_FORCE_ON_2_VANILLA,
            msg="roll=1 must leave force-on site 2 at vanilla")

    def test_roll_nondefault_installs_force_on(self):
        data = bytearray(_XBE_PATH.read_bytes())
        self.assertTrue(apply_player_speed(data, roll_scale=2.0))
        f1 = va_to_file(_ROLL_FORCE_ON_1_VA)
        f2 = va_to_file(_ROLL_FORCE_ON_2_VA)
        self.assertEqual(bytes(data[f1:f1 + 2]),
                         _ROLL_FORCE_ON_1_PATCH,
            msg="roll=2 must patch site 1 to MOV AL, 0x40")
        self.assertEqual(bytes(data[f2:f2 + 2]),
                         _ROLL_FORCE_ON_2_PATCH,
            msg="roll=2 must patch site 2 to OR DL, AL")

    def test_inject_roll_mult_is_simple_scale(self):
        """After the semantic simplification (drop the 3×
        factor + /walk_scale divisor): the injected FMUL
        constant is simply ``roll_scale``."""
        data = bytearray(_XBE_PATH.read_bytes())
        self.assertTrue(apply_player_speed(data, roll_scale=2.5))
        roll_off = va_to_file(_ROLL_SITE_VA)
        patch = bytes(data[roll_off:roll_off + 6])
        roll_va = struct.unpack("<I", patch[2:6])[0]
        from azurik_mod.patching.xbe import parse_xbe_sections
        _, secs = parse_xbe_sections(bytes(data))
        roll_fo = None
        for s in secs:
            if s["vaddr"] <= roll_va < s["vaddr"] + s["vsize"]:
                roll_fo = s["raw_addr"] + (roll_va - s["vaddr"])
                break
        value = struct.unpack("<f",
                              bytes(data[roll_fo:roll_fo + 4]))[0]
        self.assertAlmostEqual(value, 2.5, places=3,
            msg="inject_roll_mult = roll_scale (simplified from "
                "the pre-v2 3*roll/walk formula)")


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

    def test_vanilla_xbe_includes_all_static_sites(self):
        """On a vanilla XBE:
         - Four 6-byte instruction-site ranges (walk, roll, swim,
           jump-FLD)
         - 2-byte roll edge-lock range
         - Two 2-byte roll force-always-on ranges
         are ALWAYS whitelisted.  The roll + swim sites look like
        ``FMUL [abs32]`` natively so the callback also follows each
        abs32 and whitelists a 4-byte range at the shared
        constants (harmless — those never change)."""
        from azurik_mod.patches.player_physics import (
            _ROLL_EDGE_LOCK_VA,
            _player_speed_dynamic_whitelist,
        )
        xbe = _XBE_PATH.read_bytes()
        ranges = _player_speed_dynamic_whitelist(xbe)
        walk_off = va_to_file(_WALK_SITE_VA)
        roll_off = va_to_file(_ROLL_SITE_VA)
        swim_off = va_to_file(_SWIM_SITE_VA)
        jump_off = va_to_file(_JUMP_SITE_VA)
        edge_off = va_to_file(_ROLL_EDGE_LOCK_VA)
        f1_off = va_to_file(_ROLL_FORCE_ON_1_VA)
        f2_off = va_to_file(_ROLL_FORCE_ON_2_VA)
        self.assertIn((walk_off, walk_off + 6), ranges)
        self.assertIn((roll_off, roll_off + 6), ranges)
        self.assertIn((swim_off, swim_off + 6), ranges)
        self.assertIn((jump_off, jump_off + 6), ranges)
        self.assertIn((edge_off, edge_off + 2), ranges)
        self.assertIn((f1_off, f1_off + 2), ranges)
        self.assertIn((f2_off, f2_off + 2), ranges)
        # 4 instr sites (6-byte) + 3 two-byte roll-aux sites +
        # up to 3 extra 4-byte follows (shared roll constant +
        # shared swim constant + shared gravity for jump FLD).
        self.assertIn(len(ranges), (7, 8, 9, 10),
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
        self.assertTrue(apply_jump_speed(data, jump_scale=1.5))
        ranges = _player_speed_dynamic_whitelist(bytes(data))
        four_byte_ranges = [(lo, hi) for lo, hi in ranges
                            if hi - lo == 4]
        six_byte_ranges = [(lo, hi) for lo, hi in ranges
                           if hi - lo == 6]
        two_byte_ranges = [(lo, hi) for lo, hi in ranges
                           if hi - lo == 2]
        # 4 instruction-site rewrites (walk/roll/swim/jump) —
        # jump is now an FLD rewrite at VA 0x89160, not an imm32.
        self.assertEqual(len(six_byte_ranges), 4,
            msg="4 instruction-site rewrites (walk/roll/swim/jump)")
        # 4 injected floats (walk base, roll mult, swim mult,
        # jump gravity scalar).
        self.assertEqual(len(four_byte_ranges), 4,
            msg="4 injected floats (walk + roll + swim + jump)")
        # 3 two-byte ranges (edge-lock + 2 force-on sites).
        self.assertEqual(len(two_byte_ranges), 3,
            msg="3 roll-aux ranges (edge-lock + 2 force-on)")

    def test_callback_does_not_raise_on_all_zero_buffer(self):
        """Drift-safety: if called on something that isn't an XBE at
        all, the callback must return gracefully, not crash."""
        from azurik_mod.patches.player_physics import (
            _player_speed_dynamic_whitelist,
        )
        _ = _player_speed_dynamic_whitelist(b"\x00" * 0x1000)


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class SliderSemantics(unittest.TestCase):
    """After April 2026 v2's force-always-on roll patch and
    simplified inject_roll_mult, the slider semantics are:

    - walk_scale → walking-speed multiplier (always on)
    - roll_scale → EXTRA walking-speed multiplier (also always on
      when != 1.0; replaces the old WHITE/R3-gated behaviour which
      was invisible to users whose xemu didn't route those buttons)
    - swim_scale → independent swim-stroke multiplier
    - jump_scale → scales initial jump velocity (via sqrt of
      injected 9.8×jump_scale² in the jump formula)

    Walking speed at runtime with all four sliders:
      velocity = 7 × walk_scale × roll_scale × raw_stick × direction
                 (with roll_scale = 1.0, the roll-FMUL isn't
                  installed at all → velocity = 7 × walk × stick)
    """

    def test_walk_slider_is_base_multiplier(self):
        """Walking floor multiplier comes from the walk_scale
        injected base (7 × walk_scale)."""
        for walk in (0.5, 1.5, 3.0):
            with self.subTest(walk=walk):
                data = bytearray(_XBE_PATH.read_bytes())
                self.assertTrue(apply_player_speed(
                    data, walk_scale=walk, roll_scale=1.0))
                walk_off = va_to_file(_WALK_SITE_VA)
                walk_va = struct.unpack(
                    "<I", bytes(data[walk_off + 2:walk_off + 6]))[0]
                base = _read_float_at_va(bytes(data), walk_va)
                self.assertAlmostEqual(
                    base, _VANILLA_PLAYER_BASE_SPEED * walk,
                    places=3)

    def test_roll_slider_is_simple_scale(self):
        """inject_roll_mult is simply ``roll_scale`` — no 3× factor,
        no /walk_scale divisor (v2 simplification)."""
        for roll in (0.25, 2.5, 7.0):
            with self.subTest(roll=roll):
                data = bytearray(_XBE_PATH.read_bytes())
                self.assertTrue(apply_player_speed(data,
                                                   roll_scale=roll))
                roll_off = va_to_file(_ROLL_SITE_VA)
                roll_va = struct.unpack(
                    "<I", bytes(data[roll_off + 2:roll_off + 6]))[0]
                mult = _read_float_at_va(bytes(data), roll_va)
                self.assertAlmostEqual(mult, roll, places=3)

    def test_walk_scale_alone_leaves_roll_bytes_at_vanilla(self):
        """walk_scale=3, roll_scale=1 must NOT install the roll
        force-always-on patches (since roll=1 is the short-circuit
        no-op case for roll)."""
        data = bytearray(_XBE_PATH.read_bytes())
        self.assertTrue(apply_player_speed(data, walk_scale=3.0,
                                            roll_scale=1.0))
        roll_off = va_to_file(_ROLL_SITE_VA)
        f1_off = va_to_file(_ROLL_FORCE_ON_1_VA)
        f2_off = va_to_file(_ROLL_FORCE_ON_2_VA)
        self.assertEqual(bytes(data[roll_off:roll_off + 6]),
                         _ROLL_SITE_VANILLA,
            msg="roll=1 must leave FMUL site at vanilla")
        self.assertEqual(bytes(data[f1_off:f1_off + 2]),
                         _ROLL_FORCE_ON_1_VANILLA,
            msg="roll=1 must leave force-on site 1 vanilla")
        self.assertEqual(bytes(data[f2_off:f2_off + 2]),
                         _ROLL_FORCE_ON_2_VANILLA,
            msg="roll=1 must leave force-on site 2 vanilla")

    def test_swim_scale_alone_touches_only_swim_site(self):
        data = bytearray(_XBE_PATH.read_bytes())
        self.assertTrue(apply_swim_speed(data, swim_scale=5.0))
        for va, vanilla in (
            (_WALK_SITE_VA, _WALK_SITE_VANILLA),
            (_ROLL_SITE_VA, _ROLL_SITE_VANILLA),
        ):
            off = va_to_file(va)
            self.assertEqual(bytes(data[off:off + 6]), vanilla,
                msg=f"swim apply must not touch VA 0x{va:X}")
        swim_off = va_to_file(_SWIM_SITE_VA)
        self.assertNotEqual(bytes(data[swim_off:swim_off + 6]),
                            _SWIM_SITE_VANILLA)

    def test_jump_scale_touches_only_jump_site(self):
        data = bytearray(_XBE_PATH.read_bytes())
        self.assertTrue(apply_jump_speed(data, jump_scale=3.0))
        for va, vanilla in (
            (_WALK_SITE_VA, _WALK_SITE_VANILLA),
            (_ROLL_SITE_VA, _ROLL_SITE_VANILLA),
            (_SWIM_SITE_VA, _SWIM_SITE_VANILLA),
        ):
            off = va_to_file(va)
            self.assertEqual(bytes(data[off:off + 6]), vanilla,
                msg=f"jump apply must not touch VA 0x{va:X}")
        jump_off = va_to_file(_JUMP_SITE_VA)
        self.assertNotEqual(bytes(data[jump_off:jump_off + 6]),
                            _JUMP_SITE_VANILLA,
            msg="jump apply must rewrite the jump FLD site")

    def test_walk_scale_zero_does_not_produce_nan(self):
        """Defense: walk_scale=0 (from a buggy caller) must not
        produce NaN/Inf in any injected float.  The
        ``_WALK_SCALE_MIN`` clamp turns 0 into 0.01 so the
        injected base is finite."""
        import math
        data = bytearray(_XBE_PATH.read_bytes())
        self.assertTrue(apply_player_speed(
            data, walk_scale=0.0, roll_scale=1.0))
        walk_off = va_to_file(_WALK_SITE_VA)
        walk_va = struct.unpack(
            "<I", bytes(data[walk_off + 2:walk_off + 6]))[0]
        base = _read_float_at_va(bytes(data), walk_va)
        self.assertIsNotNone(base)
        self.assertTrue(math.isfinite(base),
            msg="injected walk base must be finite")


if __name__ == "__main__":
    unittest.main()
