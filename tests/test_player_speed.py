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
    _AIR_CONTROL_IMM32_VANILLA,
    _AIR_CONTROL_SITE_VAS,
    _CLIMB_CONST_VA,
    _CLIMB_CONST_VANILLA,
    _FLAP_SITE_VA,
    _FLAP_SITE_VANILLA,
    _JUMP_SITE_VA,
    _JUMP_SITE_VANILLA,
    _ROLL_CONST_VA,             # slope-slide constant (kept for inspect;
                                # NOT the roll_scale target in v4)
    _ROLL_CONST_VANILLA,
    _ROLL_FMUL_VA,              # v4 roll target: WHITE-button FMUL
    _ROLL_FMUL_VANILLA,
    _ROLL_SITE_VA,              # back-compat: aliased to _ROLL_FMUL_VA
    _ROLL_SITE_VANILLA,         # back-compat: aliased to _ROLL_FMUL_VANILLA
    _SWIM_SITE_VA,
    _SWIM_SITE_VANILLA,
    _VANILLA_AIR_CONTROL,
    _VANILLA_CLIMB_SPEED,
    _VANILLA_FLAP_IMPULSE,
    _VANILLA_JUMP_GRAVITY,
    _VANILLA_PLAYER_BASE_SPEED,
    _VANILLA_ROLL_MULT,         # 3.0 — the WHITE-button FMUL constant
    _VANILLA_ROLL_MULTIPLIER,   # legacy constant alias (3.0)
    _VANILLA_ROLL_SPEED,        # 2.0 — slope-slide constant (NOT
                                # the roll_scale target)
    _VANILLA_SWIM_MULTIPLIER,
    _WALK_SITE_VA,
    _WALK_SITE_VANILLA,
    apply_air_control_speed,
    apply_climb_speed,
    apply_flap_height,
    apply_jump_speed,
    apply_player_physics,
    apply_player_speed,
    apply_swim_speed,
)
# Back-compat aliases — old name re-exported for callers that pinned
# the pre-April-2026 names.  These tests double as a contract that
# the renames preserve the old import surface.
from azurik_mod.patches.player_physics import (  # noqa: E402
    _RUN_SITE_VA,              # v4: -> _ROLL_FMUL_VA (WHITE-button FMUL)
    _RUN_SITE_VANILLA,         # v4: -> _ROLL_FMUL_VANILLA
    _VANILLA_RUN_MULTIPLIER,   # -> _VANILLA_ROLL_MULTIPLIER (unchanged)
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
    pre-April-2026 names.

    v4 (late April 2026): ``_ROLL_SITE_VA`` re-shifts from the
    v3 constant VA (0x1AAB68 — slope-slide) back to the v1 FMUL
    instruction VA (0x849E4 — WHITE-button boost).  v3 turned
    out to target the wrong physics state (slope-slide, which
    user tests showed is NOT what the roll animation uses).
    """

    def test_run_aliases_equal_roll_fmul(self):
        self.assertEqual(_RUN_SITE_VA, _ROLL_FMUL_VA)
        self.assertEqual(_RUN_SITE_VANILLA, _ROLL_FMUL_VANILLA)
        self.assertEqual(_VANILLA_RUN_MULTIPLIER,
                         _VANILLA_ROLL_MULTIPLIER)

    def test_roll_site_aliases_point_at_fmul(self):
        # v4: _ROLL_SITE_* now aliases the FMUL instruction
        # target, not the v3 slope-slide constant.
        self.assertEqual(_ROLL_SITE_VA, _ROLL_FMUL_VA)
        self.assertEqual(_ROLL_SITE_VANILLA, _ROLL_FMUL_VANILLA)

    def test_slope_slide_const_is_separate_from_roll_site(self):
        # The v3 slope-slide constant is still exposed (inspect-
        # physics reads it) but is no longer the roll patch target.
        self.assertNotEqual(_ROLL_CONST_VA, _ROLL_FMUL_VA)
        self.assertEqual(_ROLL_CONST_VA, 0x001AAB68)
        self.assertEqual(_ROLL_FMUL_VA, 0x000849E4)


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

    def test_roll_constant_has_vanilla_bytes(self):
        xbe = _XBE_PATH.read_bytes()
        off = va_to_file(_ROLL_CONST_VA)
        self.assertEqual(bytes(xbe[off:off + 4]), _ROLL_CONST_VANILLA,
            msg="rolling-state speed constant at VA 0x001AAB68 "
                "drifted; FUN_00089A70 ground-roll physics docs "
                "are out of date (expected vanilla 2.0).")

    def test_climb_constant_has_vanilla_bytes(self):
        xbe = _XBE_PATH.read_bytes()
        off = va_to_file(_CLIMB_CONST_VA)
        self.assertEqual(bytes(xbe[off:off + 4]), _CLIMB_CONST_VANILLA,
            msg="climbing-state speed constant at VA 0x001980E4 "
                "drifted; FUN_00087F80 climbing physics docs "
                "are out of date (expected vanilla 2.0).")

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
    """End-to-end walk + roll patch behaviour against a real XBE.

    v3 (April 2026): walk still uses shim-landed FLD rewrite at
    VA 0x85F62.  Roll switched from FMUL-rewrite-plus-force-on
    (which coupled airborne speed) to a direct 4-byte constant
    overwrite at VA 0x1AAB68 — vanilla 2.0, scaled by roll_scale.
    """

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
        """At walk_scale=2.5 the injected base equals vanilla ×
        walk_scale (7.0 × 2.5 = 17.5)."""
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
            msg="injected base must equal vanilla_base × "
                "walk_scale")

    def test_roll_scale_rewrites_fmul_instruction(self):
        """v4 semantics: ``roll_scale`` rewrites the FMUL at VA
        0x849E4 (inside FUN_00084940) so the WHITE-button 3.0
        boost becomes ``3.0 × roll_scale``.  The shared 3.0 at
        VA 0x001A25BC (44 other readers) stays at 3.0; only
        this one player FMUL is redirected via shim-land.
        """
        data = bytearray(self.orig)
        self.assertTrue(apply_player_speed(data, roll_scale=1.5))

        roll_off = va_to_file(_ROLL_FMUL_VA)
        patch = bytes(data[roll_off:roll_off + 6])
        self.assertEqual(patch[:2], b"\xD8\x0D",
            msg="roll FMUL must be rewritten to FMUL [abs32]")
        inject_va = struct.unpack("<I", patch[2:6])[0]
        value = _read_float_at_va(bytes(data), inject_va)
        self.assertIsNotNone(value)
        self.assertAlmostEqual(
            value, _VANILLA_ROLL_MULT * 1.5, places=5,
            msg="injected roll multiplier = 3.0 × roll_scale = 4.5")

        # The v3 slope-slide constant at 0x1AAB68 must NOT be
        # touched — that's not the roll target anymore.
        slide_off = va_to_file(_ROLL_CONST_VA)
        self.assertEqual(
            bytes(data[slide_off:slide_off + 4]),
            _ROLL_CONST_VANILLA,
            msg="v4 roll must NOT touch the v3 slope-slide "
                "constant — that's a separate physics state.")

    def test_combined_walk_and_roll_scales(self):
        """Both sliders can be set together; each lands at its
        own site with no cross-contamination."""
        data = bytearray(self.orig)
        self.assertTrue(apply_player_speed(
            data, walk_scale=0.5, roll_scale=2.0))
        walk_off = va_to_file(_WALK_SITE_VA)
        walk_va = struct.unpack(
            "<I", bytes(data[walk_off + 2:walk_off + 6]))[0]
        self.assertAlmostEqual(
            _read_float_at_va(bytes(data), walk_va),
            _VANILLA_PLAYER_BASE_SPEED * 0.5, places=5,
            msg="inject_base = 7 × walk_scale = 3.5")
        roll_off = va_to_file(_ROLL_FMUL_VA)
        patch = bytes(data[roll_off:roll_off + 6])
        inject_va = struct.unpack("<I", patch[2:6])[0]
        roll_value = _read_float_at_va(bytes(data), inject_va)
        self.assertAlmostEqual(
            roll_value, _VANILLA_ROLL_MULT * 2.0, places=5,
            msg="injected roll mult = 3.0 × roll_scale = 6.0")

    def test_roll_scale_does_not_touch_airborne_sites(self):
        """v4 regression: confirm roll_scale no longer touches the
        force-always-on sites or the v3 slope-slide constant.
        (The v2 force-always-on approach was dropped because
        bit 0x40, which coupled roll_scale into airborne horizontal
        speed via magnitude.  v3 must leave the force-on sites,
        the edge-lock, and the old FMUL instruction ALL at vanilla.
        """
        data = bytearray(self.orig)
        self.assertTrue(apply_player_speed(data, roll_scale=3.0))
        # The WHITE edge-lock (old VA 0x85200) must be vanilla.
        self.assertEqual(bytes(data[va_to_file(0x00085200):
                                    va_to_file(0x00085200) + 2]),
                         bytes.fromhex("7508"),
            msg="v3 roll must not NOP the WHITE edge-lock")
        # Force-on sites (old VAs 0x85214, 0x8521C) must be vanilla.
        self.assertEqual(bytes(data[va_to_file(0x00085214):
                                    va_to_file(0x00085214) + 2]),
                         bytes.fromhex("2440"),
            msg="v3 roll must not patch force-on #1")
        self.assertEqual(bytes(data[va_to_file(0x0008521C):
                                    va_to_file(0x0008521C) + 2]),
                         bytes.fromhex("32d0"),
            msg="v3 roll must not patch force-on #2")

    def test_reapply_to_already_patched_is_rejected(self):
        """Running apply a second time on the same buffer must
        refuse — the walk site no longer matches vanilla and
        the roll constant no longer matches 2.0."""
        data = bytearray(self.orig)
        self.assertTrue(apply_player_speed(data, walk_scale=2.0))
        self.assertFalse(apply_player_speed(data, walk_scale=3.0),
            msg="second walk apply on an already-patched buffer "
                "must fail safe.")
        data2 = bytearray(self.orig)
        self.assertTrue(apply_player_speed(data2, roll_scale=2.0))
        self.assertFalse(apply_player_speed(data2, roll_scale=3.0),
            msg="second roll apply on an already-patched buffer "
                "must fail safe (constant already non-vanilla).")

    def test_legacy_run_scale_kwarg_still_works(self):
        """Back-compat: the old ``run_scale`` kwarg must still
        route to the roll constant overwrite.
        """
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
class ApplyClimbSpeedRetired(unittest.TestCase):
    """``apply_climb_speed`` was retired in round 10 — every value
    is a no-op because the 4-byte constant overwrite produced no
    observable in-game effect."""

    def test_every_scale_is_noop(self):
        orig = _XBE_PATH.read_bytes()
        for scale in (1.0, 0.5, 2.0, 5.0):
            with self.subTest(scale=scale):
                data = bytearray(orig)
                self.assertFalse(apply_climb_speed(data, climb_scale=scale))
                self.assertEqual(bytes(data), orig,
                    msg=f"climb_scale={scale} must leave XBE byte-identical")


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
        FUN_0008b700 FMUL, never the walk FLD or roll const."""
        data = bytearray(self.orig)
        self.assertTrue(apply_swim_speed(data, swim_scale=5.0))
        walk_off = va_to_file(_WALK_SITE_VA)
        roll_off = va_to_file(_ROLL_CONST_VA)
        self.assertEqual(
            bytes(data[walk_off:walk_off + 6]), _WALK_SITE_VANILLA)
        self.assertEqual(
            bytes(data[roll_off:roll_off + 4]), _ROLL_CONST_VANILLA)

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
        """Site isolation — jump apply must leave the walk /
        swim instruction bytes and the roll constant at vanilla."""
        data = bytearray(self.orig)
        self.assertTrue(apply_jump_speed(data, jump_scale=3.0))
        for va, vanilla, n in (
            (_WALK_SITE_VA, _WALK_SITE_VANILLA, 6),
            (_SWIM_SITE_VA, _SWIM_SITE_VANILLA, 6),
            (_ROLL_CONST_VA, _ROLL_CONST_VANILLA, 4),
        ):
            off = va_to_file(va)
            self.assertEqual(bytes(data[off:off + n]), vanilla,
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
class ApplyAirControlBehaviour(unittest.TestCase):
    """``air_control_speed_scale`` rewrites the 5 imm32 sites that
    initialise ``entity + 0x140`` (the airborne horizontal-steering
    scalar consumed per-frame by FUN_00089480)."""

    def setUp(self):
        self.orig = _XBE_PATH.read_bytes()

    def test_default_is_noop(self):
        data = bytearray(self.orig)
        self.assertFalse(apply_air_control_speed(data))
        self.assertEqual(bytes(data), self.orig,
            msg="air_control_scale=1.0 must leave the XBE "
                "byte-identical.")

    def test_all_five_sites_vanilla_is_9_point_0(self):
        for va in _AIR_CONTROL_SITE_VAS:
            off = va_to_file(va)
            current = bytes(self.orig[off:off + 4])
            self.assertEqual(current, _AIR_CONTROL_IMM32_VANILLA,
                msg=f"site at VA 0x{va:X} drifted from 9.0 (got "
                    f"{current.hex()})")

    def test_scale_2_rewrites_all_sites_to_18(self):
        data = bytearray(self.orig)
        self.assertTrue(apply_air_control_speed(
            data, air_control_scale=2.0))
        for va in _AIR_CONTROL_SITE_VAS:
            off = va_to_file(va)
            value = struct.unpack("<f",
                                  bytes(data[off:off + 4]))[0]
            self.assertAlmostEqual(value, 18.0, places=3)

    def test_does_not_touch_jump_or_walk_sites(self):
        """Isolation: air-control patch must leave the jump FLD
        site and the walk MOV+FLD site at vanilla bytes."""
        data = bytearray(self.orig)
        self.assertTrue(apply_air_control_speed(
            data, air_control_scale=3.0))
        for va, vanilla in (
            (_JUMP_SITE_VA, _JUMP_SITE_VANILLA),
            (_WALK_SITE_VA, _WALK_SITE_VANILLA),
        ):
            off = va_to_file(va)
            self.assertEqual(bytes(data[off:off + 6]), vanilla,
                msg=f"air-control touched VA 0x{va:X}")

    def test_apply_player_physics_routes_air_control(self):
        data = bytearray(self.orig)
        apply_player_physics(data, air_control_scale=1.5)
        off = va_to_file(_AIR_CONTROL_SITE_VAS[0])
        value = struct.unpack("<f",
                              bytes(data[off:off + 4]))[0]
        self.assertAlmostEqual(
            value, _VANILLA_AIR_CONTROL * 1.5, places=3)


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class ApplyFlapHeightBehaviour(unittest.TestCase):
    """``flap_height_scale`` rewrites the single
    ``FADD [0x001A25C0]`` at VA 0x896EA (inside FUN_00089480) to
    reference an injected ``8.0 × flap_scale`` constant.  Affects
    the vertical impulse added on the Air-power double-jump
    (wing flap) — ``velocity.z += 8.0 × flap_scale`` per trigger."""

    def setUp(self):
        self.orig = _XBE_PATH.read_bytes()

    def test_default_is_noop(self):
        data = bytearray(self.orig)
        self.assertFalse(apply_flap_height(data))
        self.assertEqual(bytes(data), self.orig,
            msg="flap_scale=1.0 must leave the XBE "
                "byte-identical.")

    def test_vanilla_bytes_are_fadd_of_flap_constant(self):
        off = va_to_file(_FLAP_SITE_VA)
        current = bytes(self.orig[off:off + 6])
        self.assertEqual(current, _FLAP_SITE_VANILLA,
            msg=f"flap site at VA 0x{_FLAP_SITE_VA:X} drifted "
                f"(got {current.hex()})")

    def test_scale_2_injects_gravity_scalar(self):
        """v2: flap now rewrites FLD [0x001980A8] (gravity) at VA
        0x893AE to reference an injected ``9.8 × flap_scale²``
        constant.  The sqrt formula (2 × g × h) then yields a
        v0 scaled linearly by ``flap_scale``.
        """
        from azurik_mod.patches.player_physics import (
            _VANILLA_JUMP_GRAVITY,
        )
        data = bytearray(self.orig)
        self.assertTrue(apply_flap_height(data, flap_scale=2.0))
        off = va_to_file(_FLAP_SITE_VA)
        patch = bytes(data[off:off + 6])
        self.assertEqual(patch[:2], b"\xD9\x05",
            msg="flap site must become FLD [abs32] (opcode D9 05)")
        inject_va = struct.unpack("<I", patch[2:6])[0]
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
        # 9.8 × 2.0² = 39.2
        self.assertAlmostEqual(
            value, _VANILLA_JUMP_GRAVITY * 4.0, places=3)

    def test_does_not_touch_jump_walk_or_air_control_sites(self):
        """v2 regression: flap patch must NOT touch the jump FLD,
        walk FLD, or air-control imm32 sites (it should only
        modify the one FLD at VA 0x893AE)."""
        data = bytearray(self.orig)
        self.assertTrue(apply_flap_height(data, flap_scale=2.5))
        # Walk remains vanilla
        off = va_to_file(_WALK_SITE_VA)
        self.assertEqual(bytes(data[off:off + 6]), _WALK_SITE_VANILLA)
        # Jump remains vanilla (both sites are distinct FLD [abs32]
        # rewrites — flap_scale must not influence the jump).
        off = va_to_file(_JUMP_SITE_VA)
        self.assertEqual(bytes(data[off:off + 6]), _JUMP_SITE_VANILLA)
        for va in _AIR_CONTROL_SITE_VAS:
            off = va_to_file(va)
            self.assertEqual(
                bytes(data[off:off + 4]),
                _AIR_CONTROL_IMM32_VANILLA,
                msg=f"flap patch touched air-control VA "
                    f"0x{va:X}")

    def test_apply_player_physics_routes_flap(self):
        data = bytearray(self.orig)
        apply_player_physics(data, flap_scale=2.0)
        off = va_to_file(_FLAP_SITE_VA)
        self.assertEqual(bytes(data[off:off + 2]), b"\xD9\x05",
            msg="apply_player_physics(flap_scale=...) must "
                "rewrite the FLD site (v2 — was FADD pre-v2)")


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class ApplyPlayerPhysicsRouting(unittest.TestCase):
    """`apply_player_physics` accepts gravity + walk + roll + swim
    kwargs together and routes each to its own sub-patch."""

    def test_gravity_alone_does_not_touch_speed_sites(self):
        data = bytearray(_XBE_PATH.read_bytes())
        apply_player_physics(data, gravity=7.0)
        for va, vanilla, n in (
            (_WALK_SITE_VA, _WALK_SITE_VANILLA, 6),
            (_SWIM_SITE_VA, _SWIM_SITE_VANILLA, 6),
            (_ROLL_CONST_VA, _ROLL_CONST_VANILLA, 4),
            (_CLIMB_CONST_VA, _CLIMB_CONST_VANILLA, 4),
        ):
            off = va_to_file(va)
            self.assertEqual(bytes(data[off:off + n]), vanilla,
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
    ``verify-patches --strict``.

    v4 (late April 2026) range layout on a vanilla XBE:

     - 6 × 6-byte instruction-site ranges
       (walk-FLD, swim-FMUL, jump-FLD, flap-FLD, roll-FMUL,
        flap_subsequent-FMUL)
     - 5 × 4-byte imm32 ranges (primary air-control sites)
     - 2 × 4-byte imm32 ranges (secondary air-control sites
       inside FUN_00083F90 — ``12.0`` and ``9.0``)
     - 2 × 4-byte direct-constant ranges (climb + slope_slide
       at VA 0x1AAB68 — the latter added back as a dedicated
       slider target)
    """

    def test_vanilla_xbe_includes_all_static_sites(self):
        from azurik_mod.patches.player_physics import (
            _FLAP_SUBSEQUENT_SITE_VA,
            _ROLL_FMUL_VA,
            _player_speed_dynamic_whitelist,
        )
        xbe = _XBE_PATH.read_bytes()
        ranges = _player_speed_dynamic_whitelist(xbe)
        walk_off = va_to_file(_WALK_SITE_VA)
        roll_off = va_to_file(_ROLL_FMUL_VA)
        swim_off = va_to_file(_SWIM_SITE_VA)
        jump_off = va_to_file(_JUMP_SITE_VA)
        flap_off = va_to_file(_FLAP_SITE_VA)
        flap_sub_off = va_to_file(_FLAP_SUBSEQUENT_SITE_VA)
        self.assertIn((walk_off, walk_off + 6), ranges)
        self.assertIn((swim_off, swim_off + 6), ranges)
        self.assertIn((jump_off, jump_off + 6), ranges)
        self.assertIn((flap_off, flap_off + 6), ranges)
        self.assertIn((roll_off, roll_off + 6), ranges)
        self.assertIn((flap_sub_off, flap_sub_off + 6), ranges)
        for ac_va in _AIR_CONTROL_SITE_VAS:
            ac_off = va_to_file(ac_va)
            self.assertIn((ac_off, ac_off + 4), ranges,
                msg=f"air-control site 0x{ac_va:X} missing "
                    f"from whitelist")
        # Round 10: climb / slope_slide / flap-at-peak whitelist
        # entries were dropped when their apply_* functions became
        # no-ops.  Remaining static ranges on vanilla:
        #   6 six-byte instr sites (walk/swim/jump/flap/roll/
        #     flap_subsequent)
        # + 1 six-byte wing_flap_ceiling hook (always whitelisted)
        # + 5 primary air-control + 2 secondary air-control imm32
        # + 1 four-byte flap_descent_fuel_cost imm (round 11.7)
        # = 15 on vanilla; after apply, up to 6 injected-float
        # follows land.
        self.assertIn(len(ranges), range(15, 22),
            msg=f"unexpected range count {len(ranges)}: {ranges}")

    def test_patched_xbe_adds_injected_float_ranges(self):
        """After apply, the callback must additionally locate each
        injected float's file offset via the section table and add
        4-byte ranges."""
        from azurik_mod.patches.player_physics import (
            _player_speed_dynamic_whitelist,
            apply_flap_subsequent,
        )
        data = bytearray(_XBE_PATH.read_bytes())
        self.assertTrue(apply_player_speed(
            data, walk_scale=2.0, roll_scale=1.5))
        # climb is retired no-op (returns False).  Omit from apply set.
        self.assertTrue(apply_swim_speed(data, swim_scale=2.0))
        self.assertTrue(apply_jump_speed(data, jump_scale=1.5))
        self.assertTrue(apply_air_control_speed(data,
                                                air_control_scale=2.0))
        self.assertTrue(apply_flap_height(data, flap_scale=2.0))
        self.assertTrue(apply_flap_subsequent(
            data, subsequent_scale=2.0))
        ranges = _player_speed_dynamic_whitelist(bytes(data))
        four_byte_ranges = [(lo, hi) for lo, hi in ranges
                            if hi - lo == 4]
        six_byte_ranges = [(lo, hi) for lo, hi in ranges
                           if hi - lo == 6]
        two_byte_ranges = [(lo, hi) for lo, hi in ranges
                           if hi - lo == 2]
        # 7 six-byte instruction-site rewrites: walk / swim / jump /
        # flap / roll-FMUL / flap_subsequent-FMUL / wing_flap_ceiling
        # hook slot (always whitelisted even pre-apply — the shim
        # trampoline at VA 0x89154 is a 5-byte CALL + 1-byte NOP
        # covered by a 6-byte range).  Round 11 added the last one.
        self.assertEqual(len(six_byte_ranges), 7,
            msg="7 instr-site rewrites (walk/swim/jump/flap/roll/"
                "flap_subsequent/wing_flap_ceiling-hook)")
        # 4-byte ranges:
        #   - 5 primary air-control imm32 sites
        #   - 2 secondary air-control imm32 sites (inside FUN_00083F90)
        #   - 1 flap_descent_fuel_cost imm32 (round 11.7 — always
        #     whitelisted regardless of whether it was rewritten)
        #   - 1 flap_entry_fuel_cost imm32 (round 11.11 — same)
        #   - 6 injected-float follows (walk base, swim mult,
        #     jump gravity scalar, flap gravity scalar, roll mult,
        #     flap_subsequent halving factor)
        # = 15 four-byte ranges total.
        self.assertEqual(len(four_byte_ranges), 15,
            msg="5 primary + 2 secondary air-control + 2 fuel "
                "cost imm32 + 6 injected floats "
                "= 15 four-byte ranges "
                f"(got {len(four_byte_ranges)}: {four_byte_ranges})")
        # Round 10 retired every 2-byte rewrite.
        self.assertEqual(len(two_byte_ranges), 0,
            msg="no 2-byte rewrites expected after round 10 cleanup")

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
    """v3 (April 2026) slider semantics:

    - walk_scale → walking-speed multiplier (shim-landed FLD)
    - roll_scale → rolling/sliding GROUND-state speed multiplier
      (direct 4-byte constant overwrite at VA 0x1AAB68)
    - climb_scale → climbing-state speed multiplier
      (direct 4-byte constant overwrite at VA 0x1980E4)
    - swim_scale → independent swim-stroke multiplier
    - jump_scale → scales initial jump velocity (sqrt of injected
      9.8×jump_scale² in the jump formula)

    Each slider is a dedicated physics axis — no cross-coupling.
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

    def test_roll_slider_rewrites_fmul_and_injects_multiplier(self):
        """v4: roll_scale rewrites the FMUL at VA 0x849E4 to
        reference an injected ``3.0 × roll_scale`` constant.
        """
        for roll in (0.25, 2.5, 7.0):
            with self.subTest(roll=roll):
                data = bytearray(_XBE_PATH.read_bytes())
                self.assertTrue(apply_player_speed(data,
                                                   roll_scale=roll))
                roll_off = va_to_file(_ROLL_FMUL_VA)
                patch = bytes(data[roll_off:roll_off + 6])
                self.assertEqual(patch[:2], b"\xD8\x0D")
                inject_va = struct.unpack("<I", patch[2:6])[0]
                value = _read_float_at_va(bytes(data), inject_va)
                self.assertAlmostEqual(
                    value, _VANILLA_ROLL_MULT * roll, places=3)

    def test_climb_slider_is_retired_noop(self):
        """Round 10: apply_climb_speed always returns False and
        leaves bytes unchanged — scaling the constant at 0x1980E4
        had no observable in-game effect."""
        for climb in (0.5, 2.0, 5.0):
            with self.subTest(climb=climb):
                data = bytearray(_XBE_PATH.read_bytes())
                self.assertFalse(apply_climb_speed(data,
                                                   climb_scale=climb))
                off = va_to_file(_CLIMB_CONST_VA)
                self.assertEqual(bytes(data[off:off + 4]),
                                 _CLIMB_CONST_VANILLA)

    def test_walk_scale_alone_leaves_roll_constant_at_vanilla(self):
        """walk_scale=3, roll_scale=1 must leave roll constant
        untouched (roll=1 is the no-op short-circuit)."""
        data = bytearray(_XBE_PATH.read_bytes())
        self.assertTrue(apply_player_speed(data, walk_scale=3.0,
                                            roll_scale=1.0))
        roll_off = va_to_file(_ROLL_CONST_VA)
        self.assertEqual(bytes(data[roll_off:roll_off + 4]),
                         _ROLL_CONST_VANILLA,
            msg="roll=1 must leave rolling-state constant at vanilla")

    def test_swim_scale_alone_touches_only_swim_site(self):
        data = bytearray(_XBE_PATH.read_bytes())
        self.assertTrue(apply_swim_speed(data, swim_scale=5.0))
        for va, vanilla, n in (
            (_WALK_SITE_VA, _WALK_SITE_VANILLA, 6),
            (_ROLL_CONST_VA, _ROLL_CONST_VANILLA, 4),
            (_CLIMB_CONST_VA, _CLIMB_CONST_VANILLA, 4),
        ):
            off = va_to_file(va)
            self.assertEqual(bytes(data[off:off + n]), vanilla,
                msg=f"swim apply must not touch VA 0x{va:X}")
        swim_off = va_to_file(_SWIM_SITE_VA)
        self.assertNotEqual(bytes(data[swim_off:swim_off + 6]),
                            _SWIM_SITE_VANILLA)

    def test_jump_scale_touches_only_jump_site(self):
        data = bytearray(_XBE_PATH.read_bytes())
        self.assertTrue(apply_jump_speed(data, jump_scale=3.0))
        for va, vanilla, n in (
            (_WALK_SITE_VA, _WALK_SITE_VANILLA, 6),
            (_SWIM_SITE_VA, _SWIM_SITE_VANILLA, 6),
            (_ROLL_CONST_VA, _ROLL_CONST_VANILLA, 4),
        ):
            off = va_to_file(va)
            self.assertEqual(bytes(data[off:off + n]), vanilla,
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


@unittest.skipUnless(_XBE_PATH,
    "vanilla default.xbe fixture not available")
class ApplyFlapAtPeakRetired(unittest.TestCase):
    """``apply_flap_at_peak`` was retired in round 10.  Every
    scale (including != 1.0) is a no-op and leaves the vanilla
    FLD ST(1) bytes at VA 0x8939F untouched."""

    def test_every_scale_is_noop(self):
        from azurik_mod.patches.player_physics import (
            _FLAP_PEAK_CAP_SITE_VA,
            _FLAP_PEAK_CAP_SITE_VANILLA,
            apply_flap_at_peak,
        )
        orig = _XBE_PATH.read_bytes()
        off = va_to_file(_FLAP_PEAK_CAP_SITE_VA)
        size = len(_FLAP_PEAK_CAP_SITE_VANILLA)
        for scale in (1.0, 0.5, 2.0, 5.0):
            with self.subTest(scale=scale):
                data = bytearray(orig)
                self.assertFalse(
                    apply_flap_at_peak(data, at_peak_scale=scale))
                self.assertEqual(bytes(data[off:off + size]),
                                 _FLAP_PEAK_CAP_SITE_VANILLA)

    def test_routed_via_apply_player_physics_is_noop(self):
        orig = _XBE_PATH.read_bytes()
        data = bytearray(orig)
        apply_player_physics(data, flap_at_peak_scale=2.0)
        self.assertEqual(bytes(data), orig,
            msg="flap_at_peak_scale routing is retired; must not "
                "touch any bytes")


if __name__ == "__main__":
    unittest.main()
