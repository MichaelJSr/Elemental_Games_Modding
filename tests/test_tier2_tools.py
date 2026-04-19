"""Tests for the Tier-2 RE tooling shipped in the April 2026 pass:

- ``azurik-mod test-for-va`` (tool #9)
- ``azurik-mod plan-trampoline`` (tool #5)
- ``azurik-mod entity diff`` (tool #8)
- ``azurik-mod xbr inspect`` (tool #7)
- ``pin_va_*`` helpers (tool #10)

Plus the audit-pass bug fixes:

- ``shim-inspect`` raises ``ValueError`` (not a Python traceback)
  on non-COFF input
- ``xbe hexdump`` returns synthetic zero rows for BSS VAs
- ``xbe addr`` rejects VAs past the image's virtual end

Synthetic-fixture tests run everywhere; tests marked with the
``_vanilla_fixture`` skip require the vanilla Azurik ISO to be
extracted at the workspace root.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_VANILLA_XBE = (_REPO.parent /
                "Azurik - Rise of Perathia (USA).xiso" /
                "default.xbe")
_VANILLA_CONFIG = (_REPO.parent /
                   "Azurik - Rise of Perathia (USA).xiso" /
                   "gamedata" / "config.xbr")
_VANILLA_W1 = (_REPO.parent /
               "Azurik - Rise of Perathia (USA).xiso" /
               "gamedata" / "w1.xbr")


# ---------------------------------------------------------------------------
# Audit bug-fix regression tests
# ---------------------------------------------------------------------------


class AuditFixShimInspect(unittest.TestCase):
    """``shim-inspect`` must raise a ValueError (not a traceback)
    on non-COFF input."""

    def test_raises_on_xbe_file(self):
        from azurik_mod.xbe_tools.shim_inspect import inspect_object
        with tempfile.NamedTemporaryFile(
                suffix=".xbe", delete=False) as tmp:
            tmp.write(b"XBEH" + b"\x00" * 256)
            tmp_path = Path(tmp.name)
        try:
            with self.assertRaises(ValueError) as ctx:
                inspect_object(tmp_path)
            # Must mention the file + the non-COFF nature.
            self.assertIn("COFF", str(ctx.exception))
        finally:
            tmp_path.unlink()


class AuditFixHexdumpBss(unittest.TestCase):
    """``hex_dump`` returns synthetic zero rows for BSS VAs
    (past the file-backed portion of a data section)."""

    @unittest.skipUnless(_VANILLA_XBE.exists(),
                         "vanilla default.xbe required")
    def test_bss_va_returns_zero_rows(self):
        from azurik_mod.xbe_tools.xbe_scan import hex_dump
        xbe = _VANILLA_XBE.read_bytes()
        # AZURIK_DEV_MENU_FLAG_VA — in .data past the file-backed
        # portion (zero at runtime).
        rows = hex_dump(xbe, 0x001BCDD8, length=16)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].raw, b"\x00" * 16)
        self.assertEqual(rows[0].va, 0x001BCDD8)

    @unittest.skipUnless(_VANILLA_XBE.exists(),
                         "vanilla default.xbe required")
    def test_bss_as_zeros_false_returns_empty(self):
        """Opt-out: callers that want "strict file-backed only"
        behaviour can pass ``bss_as_zeros=False``."""
        from azurik_mod.xbe_tools.xbe_scan import hex_dump
        xbe = _VANILLA_XBE.read_bytes()
        rows = hex_dump(xbe, 0x001BCDD8, length=16,
                        bss_as_zeros=False)
        self.assertEqual(rows, [])


class AuditFixAddrOutOfRange(unittest.TestCase):
    """``resolve_address`` rejects impossibly-large VAs cleanly
    instead of computing a nonsense file offset."""

    @unittest.skipUnless(_VANILLA_XBE.exists(),
                         "vanilla default.xbe required")
    def test_maximal_u32_returns_no_va(self):
        from azurik_mod.xbe_tools.xbe_scan import resolve_address
        xbe = _VANILLA_XBE.read_bytes()
        info = resolve_address(xbe, 0xFFFFFFFF)
        self.assertEqual(info.kind, "va")
        self.assertIsNone(info.va,
            msg="0xFFFFFFFF is obviously past image end; va must "
                "be None, not a bogus file offset")
        self.assertIsNone(info.file_offset)


# ---------------------------------------------------------------------------
# pin_va helpers (tool #10)
# ---------------------------------------------------------------------------


@unittest.skipUnless(_VANILLA_XBE.exists(),
                     "vanilla default.xbe required")
class PinVaHelpers(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        from azurik_mod.xbe_tools.pin_va import load_vanilla_xbe
        cls.xbe = load_vanilla_xbe()

    def test_pin_va_string_success(self):
        from azurik_mod.xbe_tools.pin_va import pin_va_string
        pin_va_string(self.xbe, va=0x001A1E3C,
                      expected="levels/selector")

    def test_pin_va_bytes_success(self):
        from azurik_mod.xbe_tools.pin_va import pin_va_bytes
        # f32 9.8 gravity (bytes cd cc 1c 41)
        pin_va_bytes(self.xbe, va=0x001980A8, expected="cdcc1c41")

    def test_pin_va_pattern_success(self):
        from azurik_mod.xbe_tools.pin_va import pin_va_pattern
        pin_va_pattern(
            self.xbe, va=0x001BCDD8, length=4,
            predicate=lambda b: b == b"" or b == b"\x00" * 4,
            description="dev-menu BSS flag")

    def test_pin_failure_has_structured_attrs(self):
        """On mismatch, ``PinFailure`` exposes attrs the test
        runner can use to build a rich diff."""
        from azurik_mod.xbe_tools.pin_va import (
            PinFailure, pin_va_string)
        with self.assertRaises(PinFailure) as ctx:
            pin_va_string(self.xbe, va=0x001A1E3C,
                          expected="wrong_value")
        self.assertEqual(ctx.exception.va, 0x001A1E3C)
        self.assertEqual(ctx.exception.section, "rdata")
        self.assertIn("wrong_value", ctx.exception.expected)

    def test_load_vanilla_xbe_cached(self):
        """Repeat loads should hit the cache (identity compare)."""
        from azurik_mod.xbe_tools.pin_va import load_vanilla_xbe
        a = load_vanilla_xbe()
        b = load_vanilla_xbe()
        self.assertIs(a, b,
            msg="load_vanilla_xbe must cache to avoid re-reading "
                "1.8 MB per test class")


# ---------------------------------------------------------------------------
# Test selector (tool #9)
# ---------------------------------------------------------------------------


class TestSelector(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="azurik-sel-"))
        # Build synthetic tests/ tree with three test files that
        # mention VA 0x85700 / pack 'my_feature'.
        (self.tmp / "test_one.py").write_text(
            "import unittest\n"
            "class AlphaTest(unittest.TestCase):\n"
            "    def test_a(self): assert 0x85700\n"
            "\n"
            "class BetaTest(unittest.TestCase):\n"
            "    def test_b(self): assert 0x00085700\n"
        )
        (self.tmp / "test_two.py").write_text(
            "class GammaTest:\n"
            "    my_feature = 1\n"
        )
        (self.tmp / "test_three.py").write_text(
            "class DeltaTest:\n"
            "    # unrelated; mentions 0x1234 only\n"
            "    other_va = 0x1234\n"
        )

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp)

    def test_find_matches_by_va(self):
        from azurik_mod.xbe_tools.test_selector import find_matches
        matches = find_matches(va=0x85700, tests_dir=self.tmp)
        names = {(m.file.name, m.class_name) for m in matches}
        self.assertEqual(names,
            {("test_one.py", "AlphaTest"), ("test_one.py", "BetaTest")})

    def test_find_matches_by_pack(self):
        from azurik_mod.xbe_tools.test_selector import find_matches
        matches = find_matches(pack="my_feature", tests_dir=self.tmp)
        names = {(m.file.name, m.class_name) for m in matches}
        self.assertEqual(names, {("test_two.py", "GammaTest")})

    def test_requires_exactly_one_of_va_or_pack(self):
        from azurik_mod.xbe_tools.test_selector import find_matches
        with self.assertRaises(ValueError):
            find_matches(tests_dir=self.tmp)
        with self.assertRaises(ValueError):
            find_matches(va=0x85700, pack="x", tests_dir=self.tmp)

    def test_hit_lines_are_sorted(self):
        from azurik_mod.xbe_tools.test_selector import find_matches
        matches = find_matches(va=0x85700, tests_dir=self.tmp)
        for m in matches:
            self.assertEqual(list(m.hit_lines), sorted(m.hit_lines))


# ---------------------------------------------------------------------------
# Trampoline planner (tool #5)
# ---------------------------------------------------------------------------


class TrampolinePlannerCore(unittest.TestCase):
    """Pure unit tests on the minimal x86 decoder + planner."""

    def _plan(self, text_bytes: bytes, va: int = 0x11000,
              budget: int = 5):
        """Build a synthetic XBE with the given .text bytes."""
        # Reuse the minimal XBE builder from test_xbe_tools.
        from tests.test_xbe_tools import _make_minimal_xbe  # noqa
        xbe = _make_minimal_xbe(text_bytes=text_bytes)
        from azurik_mod.xbe_tools.trampoline_planner import plan_trampoline
        return plan_trampoline(xbe, va, budget=budget)

    def test_call_rel32_is_clean(self):
        """CALL rel32 (E8 + 4) is exactly 5 bytes — ideal."""
        plan = self._plan(b"\xE8\x00\x00\x00\x00")
        self.assertEqual(plan.suggested_length, 5)
        self.assertTrue(plan.clean_boundary)
        self.assertEqual(plan.preserved_mnemonics, ["CALL rel32"])

    def test_push_imm32_is_clean(self):
        plan = self._plan(b"\x68\xAC\xC1\x19\x00")
        self.assertEqual(plan.suggested_length, 5)
        self.assertTrue(plan.clean_boundary)

    def test_mov_r32_imm32_is_clean(self):
        # MOV EAX, 0x14003
        plan = self._plan(b"\xB8\x03\x40\x01\x00")
        self.assertEqual(plan.suggested_length, 5)
        self.assertTrue(plan.clean_boundary)

    def test_unknown_opcode_warns(self):
        """0x8B is MOV r32, r/m32 (needs full ModR/M decode which
        the minimal decoder doesn't do).  Must flag it as unknown
        rather than guess."""
        plan = self._plan(b"\x8B\xEC")
        self.assertFalse(plan.clean_boundary)
        self.assertTrue(any("Unknown opcode" in w
                            for w in plan.warnings))

    def test_overshoot_warns(self):
        """JCC rel32 is 6 bytes; with budget=5 the carve overshoots."""
        plan = self._plan(b"\x0F\x84\x00\x00\x00\x00")
        # The 0x0F 0x84 prefix decodes as "JCC rel32" (6 bytes).
        self.assertEqual(plan.suggested_length, 6)
        self.assertTrue(any("overshoot" in w.lower()
                            for w in plan.warnings))


@unittest.skipUnless(_VANILLA_XBE.exists(),
                     "vanilla default.xbe required")
class TrampolinePlannerVanilla(unittest.TestCase):
    """Sanity check the planner against real hook sites used by
    shipped shims."""

    def test_skip_logo_callsite(self):
        from azurik_mod.xbe_tools.trampoline_planner import plan_trampoline
        xbe = _VANILLA_XBE.read_bytes()
        plan = plan_trampoline(xbe, 0x0005F6E5)
        self.assertTrue(plan.clean_boundary,
            msg=f"qol_skip_logo hook site should plan cleanly: "
                f"{plan.warnings}")
        self.assertEqual(plan.suggested_length, 5)
        self.assertIn("CALL rel32", plan.preserved_mnemonics)


# ---------------------------------------------------------------------------
# Entity diff (tool #8)
# ---------------------------------------------------------------------------


@unittest.skipUnless(_VANILLA_CONFIG.exists(),
                     "vanilla config.xbr required")
class EntityDiffVanilla(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        from azurik_mod.xbe_tools.entity_diff import diff_entities
        cls.diff = diff_entities(_VANILLA_CONFIG,
                                 "garret4", "air_elemental")

    def test_found_differences(self):
        self.assertGreater(self.diff.total_rows, 50,
            msg="garret4 vs air_elemental should produce dozens "
                "of differing properties")

    def test_only_in_b_includes_known_air_elemental_sections(self):
        """Air elemental has ``critters_damage`` + ``critters_engine``
        entries that garret4 doesn't."""
        self.assertIn("critters_damage", self.diff.only_in_b)
        self.assertIn("critters_engine", self.diff.only_in_b)

    def test_shared_sections_have_differing_hit_points(self):
        """HP differs between the two — pin it as an invariant."""
        dmg = self.diff.sections.get("critters_critter_data", [])
        hp_rows = [r for r in dmg if r.property == "hitPoints"]
        self.assertEqual(len(hp_rows), 1)
        self.assertEqual(hp_rows[0].kind, "different")
        self.assertNotEqual(hp_rows[0].a_value, hp_rows[0].b_value)

    def test_raises_on_two_missing_entities(self):
        from azurik_mod.xbe_tools.entity_diff import diff_entities
        with self.assertRaises(ValueError):
            diff_entities(_VANILLA_CONFIG,
                          "totally_nonexistent_a",
                          "totally_nonexistent_b")


# ---------------------------------------------------------------------------
# XBR record-layout inspector (tool #7)
# ---------------------------------------------------------------------------


@unittest.skipUnless(_VANILLA_W1.exists(),
                     "vanilla w1.xbr required")
class XbrInspectVanilla(unittest.TestCase):

    def test_auto_detect_surf_stride(self):
        from azurik_mod.xbe_tools.xbr_inspect import inspect_xbr
        insp = inspect_xbr(_VANILLA_W1, tag="surf", entries=3)
        self.assertTrue(insp.auto_detected_stride)
        # surf records are 16 bytes wide — pin the heuristic.
        self.assertEqual(insp.stride, 16)
        self.assertEqual(len(insp.records), 3)

    def test_fourcc_field_detected(self):
        """Column 4 of a surf record is a fourcc tag (`pbrc` /
        `rdms` / …); the classifier must flag it as ``fourcc``."""
        from azurik_mod.xbe_tools.xbr_inspect import inspect_xbr
        insp = inspect_xbr(_VANILLA_W1, tag="surf", entries=1)
        rec0 = insp.records[0]
        tag_field = next(f for f in rec0.fields if f.offset == 4)
        self.assertEqual(tag_field.best_type, "fourcc")
        # Every known surf record starts with a known asset type
        self.assertIn(tag_field.value_display,
                      {"pbrc", "rdms", "surf", "wave", "node",
                       "banm", "body", "levl", "tabl", "font"})

    def test_unknown_tag_raises(self):
        from azurik_mod.xbe_tools.xbr_inspect import inspect_xbr
        with self.assertRaises(ValueError) as ctx:
            inspect_xbr(_VANILLA_W1, tag="zzzz")
        # Error message should list available tags.
        self.assertIn("Available", str(ctx.exception))

    def test_forced_stride_overrides_auto(self):
        from azurik_mod.xbe_tools.xbr_inspect import inspect_xbr
        insp = inspect_xbr(_VANILLA_W1, tag="surf",
                           entries=1, stride=24)
        self.assertFalse(insp.auto_detected_stride)
        self.assertEqual(insp.stride, 24)


# ---------------------------------------------------------------------------
# End-to-end CLI smoke
# ---------------------------------------------------------------------------


class CliSmokeTier2(unittest.TestCase):
    """Each new subcommand spawns a subprocess + exits with the
    expected code."""

    def _run(self, *args: str) -> tuple[int, str, str]:
        out = subprocess.run(
            [sys.executable, "-m", "azurik_mod", *args],
            capture_output=True, text=True, cwd=str(_REPO))
        return out.returncode, out.stdout, out.stderr

    def test_test_for_va_cli(self):
        rc, stdout, _ = self._run("test-for-va", "0x85700")
        self.assertEqual(rc, 0)
        self.assertIn("test class", stdout.lower())

    def test_test_for_va_json(self):
        rc, stdout, _ = self._run("test-for-va", "0x85700", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(stdout)
        self.assertIsInstance(data, list)

    @unittest.skipUnless(_VANILLA_XBE.exists(),
                         "vanilla default.xbe required")
    def test_plan_trampoline_cli(self):
        rc, stdout, _ = self._run(
            "plan-trampoline", "0x5F6E5",
            "--xbe", str(_VANILLA_XBE))
        self.assertEqual(rc, 0, msg=stdout)
        self.assertIn("CALL rel32", stdout)

    @unittest.skipUnless(_VANILLA_CONFIG.exists(),
                         "vanilla config.xbr required")
    def test_entity_diff_cli(self):
        rc, stdout, _ = self._run(
            "entity", "diff", "garret4", "air_elemental",
            "--config", str(_VANILLA_CONFIG))
        self.assertEqual(rc, 0)
        self.assertIn("Entity diff", stdout)

    @unittest.skipUnless(_VANILLA_W1.exists(),
                         "vanilla w1.xbr required")
    def test_xbr_inspect_cli(self):
        rc, stdout, _ = self._run(
            "xbr", "inspect", str(_VANILLA_W1),
            "--tag", "surf", "--entries", "2")
        self.assertEqual(rc, 0)
        self.assertIn("stride=16", stdout)


if __name__ == "__main__":
    unittest.main()
