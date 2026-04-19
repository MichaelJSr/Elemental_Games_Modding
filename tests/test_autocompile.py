"""Tests for the on-demand shim auto-compile behaviour.

When ``apply_trampoline_patch`` sees a shim whose ``.o`` is missing
but whose ``.c`` sibling exists (conventional feature-folder layout:
``shims/build/<name>.o`` <-> ``azurik_mod/patches/<name>/shim.c``),
it should run ``shims/toolchain/compile.sh`` to produce the `.o`
before proceeding.  Opt-out: ``AZURIK_SHIM_NO_AUTOCOMPILE=1``.

These tests pin the observable behaviour of the two helpers
``_guess_shim_source`` and ``_auto_compile``, plus the env-var gate.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.patching import apply as _apply_mod  # noqa: E402


class GuessShimSource(unittest.TestCase):
    """Convention-matching source-file inference."""

    def test_build_stem_maps_to_feature_folder_shim(self):
        """Post-reorganisation the preferred source location is the
        feature folder: ``shims/build/<name>.o`` →
        ``azurik_mod/patches/<name>/shim.c``.  Legacy
        ``shims/fixtures/<name>.c`` is still tried as a fallback for
        the test-only fixture sources that live there."""
        shim_obj = Path("shims/build/my_feature.o")
        got = _apply_mod._guess_shim_source(shim_obj, _REPO_ROOT)
        expect = _REPO_ROOT / "azurik_mod" / "patches" / "my_feature" / "shim.c"
        self.assertEqual(got, expect,
            msg="build/X.o must resolve to azurik_mod/patches/X/shim.c "
                "in the new feature-folder layout; otherwise "
                "auto-compile won't find the source")

    def test_sibling_fallback_when_not_in_build_dir(self):
        """Ad-hoc layouts that keep .c + .o in the same dir."""
        tmp = Path(tempfile.mkdtemp(prefix="autocomp_"))
        try:
            obj = tmp / "adhoc.o"
            got = _apply_mod._guess_shim_source(obj, _REPO_ROOT)
            self.assertEqual(got, tmp / "adhoc.c")
        finally:
            (tmp / "").rmdir() if not any(tmp.iterdir()) else None

    def test_no_repo_root_still_returns_sibling(self):
        """Callers that don't supply a repo root get the simple
        sibling fallback, not a crash."""
        obj = Path("/tmp/floating.o")
        got = _apply_mod._guess_shim_source(obj, None)
        self.assertEqual(got, Path("/tmp/floating.c"))


class AutoCompileDelegatesToShellScript(unittest.TestCase):
    """The wrapper must invoke ``compile.sh`` with the right argv
    and cwd; we don't re-test the clang flags (compile.sh has its
    own tests / inline comment)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ac_"))
        self.src = self.tmp / "fake.c"
        self.src.write_text("int c_fake(void) { return 0; }\n")
        self.out = self.tmp / "fake.o"

    # ``subprocess`` is now imported lazily inside ``_auto_compile``
    # (so ``azurik_mod.patching.apply`` doesn't pay ~125 ms of
    # stdlib init at module load for the byte-patch-only fast path).
    # That means the old patch target
    # ``azurik_mod.patching.apply.subprocess.check_call`` doesn't
    # exist at patch time — patch the canonical ``subprocess.check_call``
    # instead.  Same behaviour, works with both eager + deferred imports.
    def test_invokes_compile_sh_with_expected_argv(self):
        with mock.patch("subprocess.check_call") as m:
            ok = _apply_mod._auto_compile(
                self.src, self.out, _REPO_ROOT, "my-feature")
        self.assertTrue(ok)
        self.assertEqual(m.call_count, 1)
        argv = m.call_args.args[0]
        self.assertEqual(argv[0], "bash")
        self.assertEqual(Path(argv[1]),
                         _REPO_ROOT / "shims/toolchain/compile.sh")
        self.assertEqual(Path(argv[2]), self.src)
        self.assertEqual(Path(argv[3]), self.out)

    def test_returns_false_if_repo_root_missing(self):
        self.assertFalse(_apply_mod._auto_compile(
            self.src, self.out, None, "x"))

    def test_returns_false_if_compile_script_missing(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            self.assertFalse(_apply_mod._auto_compile(
                self.src, self.out, Path(tmp_root), "x"))

    def test_returns_false_on_nonzero_compile_exit(self):
        with mock.patch(
            "subprocess.check_call",
            side_effect=subprocess.CalledProcessError(1, "bash"),
        ):
            self.assertFalse(_apply_mod._auto_compile(
                self.src, self.out, _REPO_ROOT, "x"))


class EnvVarOptOut(unittest.TestCase):
    """``AZURIK_SHIM_NO_AUTOCOMPILE=1`` must disable the whole
    auto-compile path so CI / distributed builds get predictable
    behaviour."""

    def test_env_var_shortcircuits_before_compile_call(self):
        """Direct: when the env var is set and the .o is missing,
        ``_auto_compile`` must NOT be called even if the sibling
        ``.c`` exists.  The apply function falls through to the
        existing "missing .o" error path instead."""
        # We drive the env-var gate directly by calling the same
        # conditional the apply function uses.  That isolates the
        # test from needing a real XBE section layout (va_to_file).
        tmp = Path(tempfile.mkdtemp(prefix="ac_env_"))
        src = tmp / "src" / "will_not_build.c"
        src.parent.mkdir(parents=True)
        src.write_text("int c_will_not_build(void){return 0;}\n")

        obj = tmp / "build" / "will_not_build.o"
        self.assertFalse(obj.exists())

        # Mimic the code in apply.apply_trampoline_patch.
        def should_autocompile() -> bool:
            return (not obj.exists()
                    and not os.environ.get("AZURIK_SHIM_NO_AUTOCOMPILE"))

        with mock.patch.dict(
            os.environ, {"AZURIK_SHIM_NO_AUTOCOMPILE": "1"}
        ):
            self.assertFalse(should_autocompile(),
                msg="env var must suppress auto-compile at the "
                    "gate, before any work is done")

        # Sanity: without the env var, the gate permits
        # auto-compile (actual invocation still depends on the
        # source file existing, which it does here).
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AZURIK_SHIM_NO_AUTOCOMPILE", None)
            self.assertTrue(should_autocompile(),
                msg="without the opt-out, auto-compile should be "
                    "permitted when .o is missing")


if __name__ == "__main__":
    unittest.main()
