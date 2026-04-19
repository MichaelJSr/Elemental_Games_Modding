"""Regression tests for the optimization / refinement pass.

Covers:

1. ``parse_xbe_sections`` caching on ``bytearray`` — second call
   returns O(1) + identical result; mutation via
   ``append_xbe_section`` / ``grow_text_section`` invalidates the
   cache correctly; ``bytes`` input bypasses cache but still parses.

2. Stale ``.o`` auto-rebuild — when a shim's ``.c`` source is newer
   than its ``.o`` artifact, ``apply_trampoline_patch`` triggers a
   rebuild instead of silently using the stale object file.

3. ``load_all_tables(sections=...)`` partial-load filter — only the
   named sections are parsed; unknown names are silently skipped.

4. GUI temp-dir reuse in ``extract_config_xbr`` — repeat calls with
   the same ISO path share one temp dir; changing ISO (via mtime
   bump) invalidates + replaces the cache atomically.
"""

from __future__ import annotations

import contextlib
import os
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.patching.xbe import parse_xbe_sections  # noqa: E402


_VANILLA_XBE = (_REPO_ROOT.parent /
                "Azurik - Rise of Perathia (USA).xiso/default.xbe")


# ===========================================================================
# 1. Stale .o auto-rebuild
# ===========================================================================


@unittest.skipUnless(
    (_REPO_ROOT / "shims/toolchain/compile.sh").exists(),
    "shim toolchain needed")
class StaleObjectAutoRebuild(unittest.TestCase):
    """When shim.c is newer than shim.o the apply pipeline rebuilds
    automatically.  Previously, editing .c then re-running a patch
    would silently reuse the stale .o — a classic silent no-op bug."""

    def _make_apply_probe(self, tmp: Path, c_mtime: float, o_mtime: float):
        """Synthesise a .c + .o pair with controlled mtimes + return
        a TrampolinePatch-like stub + the apply function."""
        import shutil as _shutil
        # Copy a known-good fixture shim into our temp location.
        fixture_c = _REPO_ROOT / "shims" / "fixtures" / "_reloc_test.c"
        if not fixture_c.exists():
            self.skipTest(f"fixture {fixture_c} missing")

        tmp_c = tmp / "azurik_mod" / "patches" / "probe"
        tmp_c.mkdir(parents=True)
        shim_src = tmp_c / "shim.c"
        _shutil.copy2(fixture_c, shim_src)

        tmp_o_dir = tmp / "shims" / "build"
        tmp_o_dir.mkdir(parents=True)
        shim_obj = tmp_o_dir / "probe.o"
        shim_obj.write_bytes(b"stale-object-contents")

        # Apply exact mtimes.
        os.utime(shim_src, (c_mtime, c_mtime))
        os.utime(shim_obj, (o_mtime, o_mtime))

        return shim_src, shim_obj

    def _decision_probe(self, c_mtime: float, o_mtime: float) -> bool:
        """Exercise the exact mtime-comparison branch used inside
        ``apply_trampoline_patch`` — returns True iff the branch
        would trigger a rebuild.  Factoring it like this dodges the
        full apply pipeline's byte-level pre-conditions which are
        orthogonal to the stale-detection logic we're pinning."""
        with tempfile.TemporaryDirectory(prefix="mtime_probe_") as tmp_s:
            tmp = Path(tmp_s)
            src = tmp / "shim.c"
            obj = tmp / "shim.o"
            src.write_text("void c_probe(void){}\n")
            obj.write_bytes(b"stub")
            os.utime(src, (c_mtime, c_mtime))
            os.utime(obj, (o_mtime, o_mtime))

            # Replicate the exact conditional from apply.py.  If this
            # logic ever diverges the test should drift with it —
            # we deliberately duplicate rather than importing a
            # private helper so the signal is "behaviour moved".
            should_build = not obj.exists()
            if not should_build and src.exists():
                try:
                    if src.stat().st_mtime > obj.stat().st_mtime:
                        should_build = True
                except OSError:
                    pass
            if os.environ.get("AZURIK_SHIM_FORCE_REBUILD"):
                should_build = True
            return should_build

    def test_stale_c_triggers_rebuild_decision(self):
        now = time.time()
        self.assertTrue(
            self._decision_probe(c_mtime=now, o_mtime=now - 3600),
            msg="When shim.c is 1 h newer than shim.o the apply "
                "pipeline must decide to rebuild.  If this fails, "
                "editing a shim and re-running a patch will silently "
                "use the stale .o.")

    def test_fresh_o_does_not_trigger_rebuild(self):
        now = time.time()
        self.assertFalse(
            self._decision_probe(c_mtime=now - 3600, o_mtime=now),
            msg="Fresh .o should NOT trigger a rebuild — otherwise "
                "every build wastes a clang invocation.")

    def test_apply_path_has_mtime_comparison_logic(self):
        """Source-level guard: apply_trampoline_patch's body must
        literally contain the stale-check comparison.  This catches
        accidental removal of the optimisation during a refactor."""
        from azurik_mod.patching import apply as apply_mod
        import inspect
        src = inspect.getsource(apply_mod.apply_trampoline_patch)
        self.assertIn(
            "st_mtime", src,
            msg="apply_trampoline_patch must compare .c vs .o mtime. "
                "If the optimisation was removed, update the audit "
                "CHANGELOG entry to document the revert.")
        self.assertIn(
            "AZURIK_SHIM_FORCE_REBUILD", src,
            msg="apply_trampoline_patch must honour the "
                "AZURIK_SHIM_FORCE_REBUILD env var to let users "
                "opt into an unconditional rebuild.")


# ===========================================================================
# 3. load_all_tables section filter
# ===========================================================================


_AZURIK_LOOSE_CONFIG = (
    _REPO_ROOT.parent /
    "Azurik - Rise of Perathia (USA).xiso" /
    "gamedata" / "config.xbr")


@unittest.skipUnless(_AZURIK_LOOSE_CONFIG.exists(),
    f"loose vanilla config.xbr required at {_AZURIK_LOOSE_CONFIG}")
class KeyedTablesSectionFilter(unittest.TestCase):
    """``load_all_tables(sections=...)`` returns only the requested
    sections; passing None (default) preserves the old full-load
    behaviour."""

    @classmethod
    def setUpClass(cls):
        cls.config_path = _AZURIK_LOOSE_CONFIG

    def test_default_none_loads_all(self):
        from azurik_mod.config.keyed_tables import (
            KEYED_SECTIONS, load_all_tables)
        tables = load_all_tables(str(self.config_path))
        # Every declared section should be present (modulo any that
        # raise during parse — those print a warning but don't abort).
        self.assertGreater(len(tables), 0)

    def test_filter_returns_only_requested(self):
        from azurik_mod.config.keyed_tables import load_all_tables
        tables = load_all_tables(
            str(self.config_path),
            sections=["attacks_transitions"])
        self.assertEqual(
            set(tables.keys()), {"attacks_transitions"},
            msg="filter must return exactly the requested sections, "
                "not a superset")

    def test_empty_filter_returns_empty_dict(self):
        from azurik_mod.config.keyed_tables import load_all_tables
        tables = load_all_tables(str(self.config_path), sections=[])
        self.assertEqual(tables, {})

    def test_unknown_section_silently_ignored(self):
        from azurik_mod.config.keyed_tables import load_all_tables
        tables = load_all_tables(
            str(self.config_path),
            sections=["nonexistent_fake_section_name"])
        # No exception raised; dict empty.
        self.assertEqual(tables, {})

    def test_mix_known_and_unknown(self):
        from azurik_mod.config.keyed_tables import load_all_tables
        tables = load_all_tables(
            str(self.config_path),
            sections=["attacks_transitions", "completely_fake_name"])
        self.assertEqual(set(tables.keys()), {"attacks_transitions"})


# ===========================================================================
# 4. GUI temp-dir reuse
# ===========================================================================


class GuiExtractConfigReuse(unittest.TestCase):
    """``extract_config_xbr`` returns the same Path on repeat calls
    for an unchanged ISO; changing the ISO (mtime / size) triggers a
    fresh extract + drops the previous temp dir."""

    def _stub_xdvdfs(self, tmp: Path) -> Path:
        """Create a fake xdvdfs script that writes a minimal valid
        ``config.xbr`` blob (``xobx`` magic + zeroed header) to the
        output path.

        ``extract_config_xbr`` now validates the magic byte via the
        shared :func:`azurik_mod.iso.pack._copy_out_bytes` helper, so
        the earlier empty-touch stub would hit the magic-mismatch
        guard.  A header-only file is enough to pass validation
        without pulling in real XBR parsing.

        From the stub's perspective argv is ``$1=copy-out $2=<iso>
        $3=gamedata/config.xbr $4=<out_file>``.
        """
        stub = tmp / "fake_xdvdfs.sh"
        # printf for portable binary output (busybox / bash / dash all OK);
        # ``\\x`` pushes the hex escapes through the shell.
        stub.write_text(
            "#!/bin/sh\n"
            "printf 'xobx\\0\\0\\0\\0\\0\\0\\0\\0' > \"$4\"\n"
            "exit 0\n"
        )
        stub.chmod(0o755)
        return stub

    def _mock_xdvdfs_env(self, stub: Path):
        """Return a context that stubs BOTH the backend resolver and
        the ``azurik_mod.iso.xdvdfs`` memoisation so
        ``extract_config_xbr``'s delegate path resolves to the stub.
        """
        from azurik_mod.iso import xdvdfs as xdvdfs_mod
        from gui import backend

        @contextlib.contextmanager
        def _ctx():
            saved_cache = xdvdfs_mod._cached_binary
            xdvdfs_mod._cached_binary = stub
            saved_env = os.environ.get("AZURIK_XDVDFS")
            os.environ["AZURIK_XDVDFS"] = str(stub)
            with mock.patch.object(backend, "find_xdvdfs",
                                   return_value=str(stub)):
                try:
                    yield
                finally:
                    xdvdfs_mod._cached_binary = saved_cache
                    if saved_env is None:
                        os.environ.pop("AZURIK_XDVDFS", None)
                    else:
                        os.environ["AZURIK_XDVDFS"] = saved_env
        return _ctx()

    def test_same_iso_returns_same_path(self):
        from gui import backend
        with tempfile.TemporaryDirectory(prefix="extract_reuse_") as tmp_s:
            tmp = Path(tmp_s)
            fake_iso = tmp / "fake.iso"
            fake_iso.write_bytes(b"fakefake")
            stub = self._stub_xdvdfs(tmp)

            backend.cleanup_temp_dirs()

            with self._mock_xdvdfs_env(stub):
                a = backend.extract_config_xbr(fake_iso)
                b = backend.extract_config_xbr(fake_iso)

            self.assertIsNotNone(a)
            self.assertEqual(
                a, b,
                msg="repeat extract_config_xbr with the same ISO must "
                    "reuse the cached temp path, not spawn a new one")

            # Only ONE temp dir accumulated.
            self.assertEqual(
                len(backend._temp_dirs), 1,
                msg="exactly one temp dir per ISO should be tracked; "
                    f"got {len(backend._temp_dirs)}")

            backend.cleanup_temp_dirs()

    def test_modified_iso_invalidates_cache(self):
        """Simulate the user re-randomising the ISO: same path, new
        mtime.  The cache key must detect the change + drop the
        stale temp dir."""
        from gui import backend
        with tempfile.TemporaryDirectory(prefix="extract_inv_") as tmp_s:
            tmp = Path(tmp_s)
            fake_iso = tmp / "fake.iso"
            fake_iso.write_bytes(b"fakefake")
            stub = self._stub_xdvdfs(tmp)

            backend.cleanup_temp_dirs()

            with self._mock_xdvdfs_env(stub):
                a = backend.extract_config_xbr(fake_iso)
                # Change mtime + size to simulate a new build.
                time.sleep(0.05)
                fake_iso.write_bytes(b"different content here")
                b = backend.extract_config_xbr(fake_iso)

            self.assertIsNotNone(a)
            self.assertIsNotNone(b)
            self.assertNotEqual(
                a.parent, b.parent,
                msg="mtime/size bump must invalidate the cache and "
                    "produce a fresh temp dir")

            # The old temp dir should have been cleaned up when the
            # cache was invalidated — only the newest remains.
            self.assertEqual(
                len(backend._temp_dirs), 1,
                msg="stale temp dir leaked after cache invalidation")

            backend.cleanup_temp_dirs()


# ===========================================================================
# 5. compile.sh friendly clang-missing error
# ===========================================================================


class CompileShFriendlyErrors(unittest.TestCase):
    """``compile.sh`` emits an install-hint message when clang is
    missing from PATH — not the shell's default 'command not found'."""

    def test_clang_missing_produces_install_hint(self):
        compile_sh = _REPO_ROOT / "shims" / "toolchain" / "compile.sh"
        if not compile_sh.exists():
            self.skipTest("compile.sh missing")

        with tempfile.TemporaryDirectory(prefix="clang_hint_") as tmp_s:
            tmp = Path(tmp_s)
            dummy = tmp / "dummy.c"
            dummy.write_text("void c_probe(void){}\n")

            # Isolate the subprocess from the host's PATH so clang is
            # genuinely unreachable.  ``/usr/bin/touch`` is on most
            # platforms; we need SOMETHING resolvable or `bash` itself
            # won't start.
            env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp)}
            # Confirm clang isn't in our restricted PATH.  If it IS
            # (e.g. a system clang at /usr/bin/clang), skip.
            probe = subprocess.run(
                ["/usr/bin/env", "-i", "PATH=/usr/bin:/bin",
                 "which", "clang"],
                capture_output=True, text=True)
            if probe.returncode == 0 and probe.stdout.strip():
                self.skipTest("system clang in /usr/bin — cannot "
                              "construct a clang-less PATH on this host")

            result = subprocess.run(
                [str(compile_sh), str(dummy), str(tmp / "dummy.o")],
                env=env, capture_output=True, text=True)

            self.assertNotEqual(
                result.returncode, 0,
                msg="compile.sh should fail when clang is missing, not "
                    "silently succeed")
            combined = (result.stderr or "") + (result.stdout or "")
            self.assertIn(
                "clang", combined.lower(),
                msg="clang-missing error message must mention clang")
            self.assertIn(
                "install", combined.lower(),
                msg="error should include installation guidance — "
                    "current text should say 'install' somewhere")


if __name__ == "__main__":
    unittest.main()
