"""Regression guard against ``scripts/`` imports in runtime code.

The ``scripts/`` directory is excluded from the installed wheel
(see ``pyproject.toml`` § ``[tool.setuptools.packages.find]``) —
only ``azurik_mod*`` and ``gui*`` ship.  That means **anything in
those two packages that imports ``scripts.*`` at runtime will
blow up with** ``ModuleNotFoundError: No module named 'scripts'``
**when the user runs** ``pip install``-ed binaries like
``azurik-gui`` / ``azurik-mod`` from outside the repo root.

We hit this exact bug once already with
:func:`azurik_mod.xbr.document._config_xbr_offset_to_name` trying
``from scripts.xbr_parser import KEYED_SECTION_OFFSETS``.  These
tests exist to make that class of bug fail *loudly at test time*
rather than when the first end user tries to open the GUI.

Two independent probes:

1. **Static grep**: walk every Python file in ``azurik_mod/`` and
   ``gui/`` looking for ``from scripts`` / ``import scripts``.
   This catches the mistake regardless of code coverage.
2. **Dynamic import isolation**: reimport the XBR editor backend
   with ``scripts`` hidden from ``sys.modules`` and ``sys.path``,
   then exercise the code path that triggered the original
   traceback (``open_from_workspace`` → document ``keyed_sections``
   with a persisted pending edit that requires friendly-name
   lookup).
"""

from __future__ import annotations

import builtins
import importlib
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Test 1: static grep
# ---------------------------------------------------------------------------


_SCRIPTS_IMPORT_PATTERNS = (
    re.compile(r"^\s*from\s+scripts(\.|\s|$)"),
    re.compile(r"^\s*import\s+scripts(\.|\s|$)"),
)


def _python_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in root.rglob("*.py"):
        # Skip __pycache__ and the like.
        if "__pycache__" in p.parts:
            continue
        out.append(p)
    return out


class NoScriptsImportsInRuntime(unittest.TestCase):
    """Static check — runtime code must not ``import scripts``."""

    def test_azurik_mod_has_no_scripts_import(self):
        self._assert_no_scripts_imports(_REPO_ROOT / "azurik_mod")

    def test_gui_has_no_scripts_import(self):
        self._assert_no_scripts_imports(_REPO_ROOT / "gui")

    def _assert_no_scripts_imports(self, root: Path) -> None:
        offenders: list[str] = []
        for path in _python_files(root):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for lineno, line in enumerate(
                    text.splitlines(), start=1):
                for pattern in _SCRIPTS_IMPORT_PATTERNS:
                    if pattern.match(line):
                        rel = path.relative_to(_REPO_ROOT)
                        offenders.append(
                            f"{rel}:{lineno}: {line.strip()}")
        self.assertEqual(
            offenders, [],
            msg=(
                "The following runtime files import from "
                "``scripts/`` — but ``scripts/`` is excluded "
                "from the installed wheel per pyproject.toml, "
                "so these imports will crash for end users "
                "running ``pip install``-ed binaries like "
                "``azurik-gui`` from outside the repo root.\n"
                "Move the needed symbols into the ``azurik_mod`` "
                "package (or re-import them there) and update "
                "the references.\n\n"
                "Offenders:\n" + "\n".join(offenders)))


# ---------------------------------------------------------------------------
# Test 2: dynamic import-isolation smoke
# ---------------------------------------------------------------------------


_GAMEDATA_CANDIDATES = [
    Path("/Users/michaelsrouji/Documents/Xemu/tools/"
         "Azurik - Rise of Perathia (USA).xiso/gamedata"),
    _REPO_ROOT.parent / "Azurik - Rise of Perathia (USA).xiso" / "gamedata",
]


def _find_gamedata() -> Path | None:
    for p in _GAMEDATA_CANDIDATES:
        if p.exists():
            return p
    return None


_GAMEDATA = _find_gamedata()


class _ScriptsHiddenContext:
    """Context manager that makes ``import scripts`` fail the way
    it would fail from an installed wheel.

    Restores :data:`sys.modules` / :data:`sys.path` +
    :func:`builtins.__import__` on exit.
    """

    def __enter__(self):
        self._saved_modules = dict(sys.modules)
        # Purge any already-imported ``scripts.*`` entries so the
        # import machinery re-resolves and fails.
        for name in list(sys.modules):
            if name == "scripts" or name.startswith("scripts."):
                del sys.modules[name]
        self._saved_path = list(sys.path)
        # Remove repo root from sys.path so ``scripts/`` isn't
        # discoverable as a top-level package.
        sys.path[:] = [
            p for p in sys.path
            if Path(p).resolve() != _REPO_ROOT.resolve()]
        self._saved_import = builtins.__import__

        def blocking_import(name, *args, **kwargs):
            # Enforce: any request for ``scripts.*`` at runtime
            # raises the same way an installed wheel would.
            if name == "scripts" or name.startswith("scripts."):
                raise ModuleNotFoundError(
                    f"No module named 'scripts' "
                    f"(simulated installed-wheel environment)")
            return self._saved_import(name, *args, **kwargs)

        builtins.__import__ = blocking_import
        # Purge the caches in our own modules so the next call
        # rebuilds without any leftover ``scripts``-import state.
        _reset_runtime_caches()
        return self

    def __exit__(self, *exc):
        builtins.__import__ = self._saved_import
        sys.path[:] = self._saved_path
        sys.modules.clear()
        sys.modules.update(self._saved_modules)
        # Clear caches one more time so post-test state matches
        # pre-test state.
        _reset_runtime_caches()


def _reset_runtime_caches() -> None:
    """Wipe per-module caches that could carry stale
    ``scripts``-sourced data.  Safe to call while the module is
    imported; re-imports from the blocking context still work for
    everything except ``scripts.*``."""
    try:
        from azurik_mod.xbr import document as _doc
        _doc._CONFIG_XBR_OFFSET_TO_NAME_CACHE = None
    except ImportError:
        pass


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class RuntimeWorksWithoutScripts(unittest.TestCase):
    """End-to-end: exercise the exact traceback path the user
    reported, with ``scripts`` unreachable."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(
            prefix="xbr_no_scripts_"))
        # Build a minimal workspace.
        (self._tmp / "game" / "gamedata").mkdir(parents=True)
        shutil.copy2(
            _GAMEDATA / "config.xbr",
            self._tmp / "game" / "gamedata" / "config.xbr")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_editor_opens_and_replays_pending_without_scripts(self):
        """Reproduces the GUI startup traceback from the bug
        report:

        - Workspace has a persisted pending edit targeting
          ``config.xbr``.
        - The backend opens ``config.xbr`` → calls
          ``_replay_pending_for_current_file`` →
          ``_dispatch_pending`` → ``doc.keyed_sections()`` →
          ``_config_xbr_offset_to_name`` → used to hit
          ``from scripts.xbr_parser import KEYED_SECTION_OFFSETS``.

        With the fix in place this must complete without a
        ``ModuleNotFoundError``.
        """
        # First: seed a pending edit while ``scripts/`` is still
        # available so the persistence format is realistic.
        from gui.pages.xbr_editor import XbrEditorBackend
        from gui.xbr_workspace import XbrWorkspace

        ws = XbrWorkspace(self._tmp)
        seeder = XbrEditorBackend(ws)
        seeder.open(ws.gamedata_dir / "config.xbr")
        seeder.set_keyed_double(
            3, "garret4", "walkSpeed", 99.0)
        seeder.save_persistent_state()

        # Now reimport everything WITHOUT ``scripts/`` reachable.
        with _ScriptsHiddenContext():
            # Proof the hiding works: direct import fails.
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module("scripts.xbr_parser")
            # The actual regression — must not raise.
            backend = XbrEditorBackend(ws)
            backend.load_persistent_state()
            backend.open(ws.gamedata_dir / "config.xbr")
            # Friendly-name lookup is the specific call that
            # crashed before.
            entries = backend.toc_entries
            # Section 3 is ``attacks_transitions`` — its friendly
            # name must still resolve without ``scripts/``.
            friendly = {
                e["index"]: e["friendly_name"] for e in entries}
            self.assertEqual(
                friendly.get(3), "attacks_transitions",
                msg="Friendly section name lookup fell back to "
                    "None — the package-local offsets table "
                    "isn't wired into the backend.")
            # And the pending edit is re-applied.
            cell = (backend.document.section_for(3)
                    .find_cell("garret4", "walkSpeed"))
            self.assertEqual(cell.double_value, 99.0)


if __name__ == "__main__":
    unittest.main()
