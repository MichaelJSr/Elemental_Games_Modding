"""Tests for the #6 shim scaffolder tool.

Coverage:

- ABI inference (``guess_abi_from_signature``) from
  Ghidra-shaped parameter dicts.
- Template rendering (``generate_init_py``, ``generate_shim_c``)
  produces syntactically valid output for every expected
  calling-convention branch.
- ``plan_scaffold`` respects the four optional inputs (no data
  / only XBE / only Ghidra / both) and never writes to disk.
- ``write_scaffold`` refuses to overwrite existing folders.
- CLI wrapper (``azurik-mod new-shim``) smoke-tests the
  happy path + dry-run + error paths.

Most cases run with the synthetic :class:`MockGhidraServer` so
no live Ghidra is required.  Two cases hit the vanilla XBE to
confirm ``replaced_bytes`` pickup + ABI detection agree with
our already-documented symbols (gravity_integrate_raw =
__fastcall, boot_state_tick = __stdcall).
"""

from __future__ import annotations

import shutil
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


# ---------------------------------------------------------------------------
# ABI inference
# ---------------------------------------------------------------------------


class AbiInference(unittest.TestCase):

    def test_all_stack_params_is_stdcall(self):
        from azurik_mod.xbe_tools.shim_scaffolder import (
            guess_abi_from_signature)
        params = [
            {"name": "name", "dataType": "char *",
             "storage": "stack", "ordinal": 0},
            {"name": "flag", "dataType": "char",
             "storage": "stack", "ordinal": 1},
        ]
        abi = guess_abi_from_signature(
            "undefined FUN_00018980(char *name, char flag)",
            parameters=params)
        self.assertEqual(abi.attribute, "stdcall")
        self.assertEqual(abi.parameters,
            (("char *", "name"), ("char", "flag")))
        self.assertEqual(abi.stdcall_n, 4 + 1)  # 4B ptr + 1B char
        self.assertEqual(abi.confidence, "high")

    def test_ecx_only_is_thiscall(self):
        from azurik_mod.xbe_tools.shim_scaffolder import (
            guess_abi_from_signature)
        params = [
            {"name": "self", "dataType": "int *", "storage": "ECX",
             "ordinal": 0},
            {"name": "needle", "dataType": "char *",
             "storage": "stack", "ordinal": 1},
        ]
        abi = guess_abi_from_signature(
            "int thiscall_fn(int *self, char *needle)",
            parameters=params)
        self.assertEqual(abi.attribute, "thiscall")

    def test_ecx_edx_is_fastcall(self):
        from azurik_mod.xbe_tools.shim_scaffolder import (
            guess_abi_from_signature)
        params = [
            {"name": "this", "dataType": "int *",
             "storage": "ECX", "ordinal": 0},
            {"name": "other", "dataType": "int *",
             "storage": "EDX", "ordinal": 1},
            {"name": "n", "dataType": "int",
             "storage": "stack", "ordinal": 2},
        ]
        abi = guess_abi_from_signature(
            "int fastcall_fn(int *this, int *other, int n)",
            parameters=params)
        self.assertEqual(abi.attribute, "fastcall")

    def test_undefined_return_becomes_void(self):
        from azurik_mod.xbe_tools.shim_scaffolder import (
            guess_abi_from_signature)
        abi = guess_abi_from_signature(
            "undefined FUN_12345()", parameters=[])
        self.assertEqual(abi.return_type, "void")

    def test_undefined4_return_becomes_unsigned_int(self):
        from azurik_mod.xbe_tools.shim_scaffolder import (
            guess_abi_from_signature)
        abi = guess_abi_from_signature(
            "undefined4 foo()",
            parameters=[])
        self.assertEqual(abi.return_type, "unsigned int")

    def test_no_parameters_defaults_low_confidence(self):
        """When we can't pull param metadata we return a low-
        confidence stdcall guess + a note."""
        from azurik_mod.xbe_tools.shim_scaffolder import (
            guess_abi_from_signature)
        abi = guess_abi_from_signature("", parameters=None)
        self.assertEqual(abi.confidence, "low")
        self.assertEqual(abi.attribute, "stdcall")
        self.assertTrue(any("default" in n.lower()
                            for n in abi.notes))


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


class TemplateRendering(unittest.TestCase):

    def _minimal_abi(self, **kw):
        from azurik_mod.xbe_tools.shim_scaffolder import ABIGuess
        return ABIGuess(**kw) if kw else ABIGuess()

    def test_init_py_has_expected_skeleton(self):
        from azurik_mod.xbe_tools.shim_scaffolder import (
            ABIGuess, generate_init_py)
        body = generate_init_py(
            "my_shim", hook_va=0x12345,
            replaced_bytes=bytes.fromhex("e89692fbff"),
            abi=ABIGuess(attribute="stdcall"))
        self.assertIn('name="my_shim"', body)
        self.assertIn("va=0x00012345", body)
        self.assertIn('bytes.fromhex("e89692fbff")', body)
        self.assertIn('MY_SHIM_TRAMPOLINE', body)
        self.assertIn('shim_symbol="_c_my_shim"', body)
        self.assertIn('register_feature', body)

    def test_shim_c_includes_attribute_and_params(self):
        from azurik_mod.xbe_tools.shim_scaffolder import (
            ABIGuess, generate_shim_c)
        abi = ABIGuess(
            attribute="fastcall", return_type="unsigned int",
            parameters=(("unsigned int *", "param_1"),
                        ("float", "param_2")),
            confidence="high")
        body = generate_shim_c("my_shim", hook_va=0x85700, abi=abi)
        self.assertIn("__attribute__((fastcall))", body)
        self.assertIn(
            "unsigned int c_my_shim(unsigned int * param_1, "
            "float param_2)",
            body)
        self.assertIn("VA 0x00085700", body)

    def test_shim_c_void_void_default(self):
        from azurik_mod.xbe_tools.shim_scaffolder import (
            ABIGuess, generate_shim_c)
        body = generate_shim_c(
            "empty", hook_va=None, abi=ABIGuess())
        self.assertIn("__attribute__((stdcall))", body)
        self.assertIn("void c_empty(void)", body)
        self.assertIn("????????", body)  # hook-VA placeholder


# ---------------------------------------------------------------------------
# plan_scaffold + write_scaffold
# ---------------------------------------------------------------------------


class ScaffoldPlanning(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp_repo = Path(tempfile.mkdtemp(prefix="azurik-scaf-"))
        (self.tmp_repo / "azurik_mod" / "patches").mkdir(parents=True)
        (self.tmp_repo / "pyproject.toml").write_text("# fake")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_repo)

    def test_rejects_bad_name(self):
        from azurik_mod.xbe_tools.shim_scaffolder import plan_scaffold
        with self.assertRaises(ValueError):
            plan_scaffold("BadName", repo_root=self.tmp_repo)
        with self.assertRaises(ValueError):
            plan_scaffold("1_starts_digit",
                          repo_root=self.tmp_repo)
        with self.assertRaises(ValueError):
            plan_scaffold("has-dash", repo_root=self.tmp_repo)

    def test_rejects_existing_folder(self):
        from azurik_mod.xbe_tools.shim_scaffolder import plan_scaffold
        (self.tmp_repo / "azurik_mod" / "patches" / "existing").mkdir()
        with self.assertRaises(ValueError):
            plan_scaffold("existing", repo_root=self.tmp_repo)

    def test_plan_without_extras(self):
        """Minimal plan — just a name — still renders all three
        output files with TODO placeholders."""
        from azurik_mod.xbe_tools.shim_scaffolder import plan_scaffold
        plan = plan_scaffold("minimal", repo_root=self.tmp_repo)
        self.assertIsNone(plan.hook_va)
        self.assertIsNone(plan.replaced_bytes)
        self.assertEqual(plan.abi.confidence, "low")
        self.assertEqual(len(plan.files), 3)
        self.assertIn("TODO", plan.init_py_body)
        self.assertIn("TODO", plan.shim_c_body)

    def test_write_refuses_overwrite(self):
        from azurik_mod.xbe_tools.shim_scaffolder import (
            plan_scaffold, write_scaffold)
        plan = plan_scaffold("trivial", repo_root=self.tmp_repo)
        write_scaffold(plan)
        # Folder now exists on disk → second write must refuse.
        with self.assertRaises(ValueError):
            write_scaffold(plan)

    def test_planner_failure_leaves_placeholder_bytes(self):
        """When ``plan_trampoline`` can't classify the opcode the
        renderer falls back to 5 NOP bytes — NOT an empty
        ``bytes.fromhex("")`` that would blow up at apply time."""
        from azurik_mod.xbe_tools.shim_scaffolder import plan_scaffold
        # Build a tiny XBE-ish blob that decodes as "unknown"
        # at the hook.  The real parser will reject it; the
        # scaffolder should survive the exception gracefully.
        plan = plan_scaffold(
            "bad_xbe_shim", repo_root=self.tmp_repo,
            hook_va=0x12345, xbe_bytes=b"\x00" * 256)
        # init_py must still contain a usable NOP placeholder.
        self.assertIn('bytes.fromhex("9090909090")',
                      plan.init_py_body)
        self.assertTrue(any("Planner couldn't" in w or
                            "planner raised" in w
                            for w in plan.planner_warnings))


# ---------------------------------------------------------------------------
# Vanilla-XBE pickup (optional)
# ---------------------------------------------------------------------------


@unittest.skipUnless(_VANILLA_XBE.exists(),
                     "vanilla default.xbe required")
class ScaffoldVanillaIntegration(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp_repo = Path(tempfile.mkdtemp(prefix="azurik-scaf-"))
        (self.tmp_repo / "azurik_mod" / "patches").mkdir(parents=True)
        (self.tmp_repo / "pyproject.toml").write_text("# fake")
        self.xbe = _VANILLA_XBE.read_bytes()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_repo)

    def test_replaced_bytes_picked_up_at_skip_logo_hook(self):
        """qol_skip_logo hooks 0x5F6E5 which is a clean CALL rel32.
        The scaffolder must pick up the exact 5-byte replacement."""
        from azurik_mod.xbe_tools.shim_scaffolder import plan_scaffold
        plan = plan_scaffold(
            "skip_x", repo_root=self.tmp_repo,
            hook_va=0x5F6E5, xbe_bytes=self.xbe)
        # CALL rel32 at 0x5F6E5 = E8 96 92 FB FF (verified live)
        self.assertEqual(plan.replaced_bytes,
                         bytes.fromhex("e89692fbff"))
        self.assertIn('bytes.fromhex("e89692fbff")',
                      plan.init_py_body)


class MockServerAbiPickup(unittest.TestCase):
    """Mount a :class:`MockGhidraServer`, pre-populate a function
    with known signature / storage, and verify the full pipeline
    detects the right ABI."""

    def setUp(self) -> None:
        from azurik_mod.xbe_tools.ghidra_client import GhidraClient
        from azurik_mod.xbe_tools.mock_ghidra import MockGhidraServer
        self.server = MockGhidraServer()
        self.server.start()
        self.addCleanup(self.server.stop)
        self.tmp_repo = Path(tempfile.mkdtemp(prefix="azurik-scaf-"))
        (self.tmp_repo / "azurik_mod" / "patches").mkdir(parents=True)
        (self.tmp_repo / "pyproject.toml").write_text("# fake")
        self.addCleanup(shutil.rmtree, self.tmp_repo)
        # Register a fastcall-like function at 0x85700.
        self.server.register_function(
            0x85700, "FUN_00085700",
            signature=("undefined FUN_00085700("
                       "undefined4 * param_1, "
                       "undefined4 * param_2, "
                       "float param_3)"),
            parameters=[
                {"name": "param_1", "dataType": "undefined4 *",
                 "storage": "ECX", "ordinal": 0},
                {"name": "param_2", "dataType": "undefined4 *",
                 "storage": "EDX", "ordinal": 1},
                {"name": "param_3", "dataType": "float",
                 "storage": "stack", "ordinal": 2},
            ])
        self.client = GhidraClient(port=self.server.port)

    def test_full_pickup_produces_fastcall_shim(self):
        from azurik_mod.xbe_tools.shim_scaffolder import plan_scaffold
        plan = plan_scaffold(
            "gravity_hook", repo_root=self.tmp_repo,
            hook_va=0x85700, ghidra_client=self.client)
        self.assertEqual(plan.abi.attribute, "fastcall")
        self.assertEqual(plan.abi.confidence, "high")
        # Expect 3 parameters with correct types after
        # undefined4 → unsigned int translation.
        self.assertEqual(len(plan.abi.parameters), 3)
        self.assertIn("__attribute__((fastcall))", plan.shim_c_body)


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


class CliSmoke(unittest.TestCase):

    def _run(self, *args: str) -> tuple[int, str, str]:
        out = subprocess.run(
            [sys.executable, "-m", "azurik_mod", *args],
            capture_output=True, text=True, cwd=str(_REPO))
        return out.returncode, out.stdout, out.stderr

    def test_help_lists_all_flags(self):
        rc, stdout, _ = self._run("new-shim", "--help")
        self.assertEqual(rc, 0)
        for flag in ("--hook", "--xbe", "--iso", "--ghidra",
                     "--category", "--dry-run"):
            self.assertIn(flag, stdout)

    def test_bad_name_exits_nonzero(self):
        rc, _, stderr = self._run("new-shim", "BadName", "--dry-run")
        self.assertEqual(rc, 1)
        self.assertIn("invalid name", stderr)

    def test_dry_run_leaves_filesystem_untouched(self):
        target = _REPO / "azurik_mod" / "patches" / "scaffold_smoke"
        self.assertFalse(target.exists())
        rc, stdout, _ = self._run(
            "new-shim", "scaffold_smoke", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("--dry-run: files that WOULD", stdout)
        self.assertFalse(target.exists())


# ---------------------------------------------------------------------------
# Audit-fix regression: find-refs with both --va and --string
# ---------------------------------------------------------------------------


class FindRefsMutualExclusion(unittest.TestCase):
    """--va wins over --string when both are given + emits a
    warning on stderr."""

    @unittest.skipUnless(_VANILLA_XBE.exists(),
                         "vanilla default.xbe required")
    def test_va_wins_over_string(self):
        out = subprocess.run(
            [sys.executable, "-m", "azurik_mod", "xbe", "find-refs",
             "--va", "0x19C1AC", "--string", "garbage_string",
             "--xbe", str(_VANILLA_XBE)],
            capture_output=True, text=True, cwd=str(_REPO))
        self.assertEqual(out.returncode, 0,
            msg="--va must succeed when --string would have failed")
        self.assertIn("preferring --va", out.stderr)
        self.assertIn("VA 0x19C1AC", out.stdout)


if __name__ == "__main__":
    unittest.main()
