"""Regression tests for the deep-audit pass.

Pins:

1. **``SolverState.has_all`` fails closed on malformed inputs**
   (was vacuously True).
2. **``parse_xbe_sections`` bounds-checks** its header fields
   instead of blowing up with opaque ``struct.error`` /
   ``ValueError: subsection not found``.
3. **``extract_xbe_from_iso`` caches by (path, mtime, size)** —
   second call on an unchanged ISO hits the cache.
4. **Solver DB parse is cached across Solver() instances** —
   instantiating two Solvers in one process reads the JSON once.
5. **``mod-template`` CLI** produces editable mod JSON from a live
   config.xbr read.
6. **``examples/`` folder is gone**, replaced by the mod-template
   workflow.
7. **Doc cross-links** no longer use the obsolete ``azurik-cli``.
8. **Launchers** carry the dep-check import guard so a stale
   install doesn't silently flash-and-close.
"""

from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ===========================================================================
# 1. SolverState.has_all fails closed
# ===========================================================================


class SolverHasAllFailsClosed(unittest.TestCase):
    """Malformed requirement dicts MUST NOT vacuously return True.

    The historical bug: a dict without recognised shape fell through
    to ``return True``, meaning a typo in ``logic_db.json`` would
    silently disable that node's gating check.  Same class of
    "silently permissive solver" bug as the power-placement audit."""

    def setUp(self):
        from azurik_mod.randomizer.solver import SolverState
        self.state = SolverState()

    def test_empty_list_is_vacuously_true(self):
        self.assertTrue(self.state.has_all([]))

    def test_empty_dict_is_vacuously_true(self):
        """A node with no requirements at all should still be reachable."""
        self.assertTrue(self.state.has_all({}))

    def test_empty_items_is_vacuously_true(self):
        self.assertTrue(self.state.has_all({"items": []}))
        self.assertTrue(self.state.has_all({"all_of": []}))

    def test_unknown_shape_fails_closed(self):
        """The headline fix: a dict with non-empty items but unknown
        type must NOT silently pass."""
        self.assertFalse(
            self.state.has_all({
                "items": ["something"],
                "type": "xor",        # unrecognised
            }),
            msg="unknown 'type' field must fail closed, not return True")

    def test_string_input_fails_closed(self):
        """A lone string is neither a list nor a dict; treat as bad
        input rather than a pass-through."""
        self.assertFalse(self.state.has_all("power_water"))

    def test_real_all_of_still_works(self):
        self.state.inventory.add("disc_of_life")
        self.assertTrue(self.state.has_all({"all_of": ["disc_of_life"]}))
        self.assertFalse(self.state.has_all({"all_of": ["disc_of_fire"]}))

    def test_real_any_of_still_works(self):
        self.state.inventory.add("disc_of_life")
        self.assertTrue(
            self.state.has_all({"any_of": ["disc_of_life", "disc_of_fire"]}))
        self.assertFalse(
            self.state.has_all({"any_of": ["disc_of_air", "disc_of_fire"]}))


# ===========================================================================
# 2. parse_xbe_sections bounds-checks
# ===========================================================================


class ParseXbeSectionsBounds(unittest.TestCase):
    """Hostile / truncated XBE inputs produce a clear ValueError, not
    a traceback from the middle of struct.unpack_from."""

    def test_too_small_rejects(self):
        from azurik_mod.patching.xbe import parse_xbe_sections
        with self.assertRaises(ValueError) as cm:
            parse_xbe_sections(b"XBEH" + b"\x00" * 10)
        self.assertIn("384", str(cm.exception))

    def test_bad_magic_rejects(self):
        from azurik_mod.patching.xbe import parse_xbe_sections
        with self.assertRaises(ValueError) as cm:
            parse_xbe_sections(b"ZZZZ" + b"\x00" * 0x400)
        self.assertIn("magic", str(cm.exception).lower())

    def test_insane_section_count_rejects(self):
        from azurik_mod.patching.xbe import parse_xbe_sections
        # Build a buffer that looks XBE-ish but claims 2^31 sections.
        buf = bytearray(0x200)
        buf[0:4] = b"XBEH"
        struct.pack_into("<I", buf, 0x104, 0x10000)   # base_addr
        struct.pack_into("<I", buf, 0x11C, 0x7FFFFFFF)  # section_count
        struct.pack_into("<I", buf, 0x120, 0x10180)   # section_headers_addr
        with self.assertRaises(ValueError) as cm:
            parse_xbe_sections(bytes(buf))
        self.assertIn("section_count", str(cm.exception))

    def test_headers_past_eof_rejects(self):
        from azurik_mod.patching.xbe import parse_xbe_sections
        buf = bytearray(0x200)
        buf[0:4] = b"XBEH"
        struct.pack_into("<I", buf, 0x104, 0x10000)
        struct.pack_into("<I", buf, 0x11C, 100)  # 100 sections
        struct.pack_into("<I", buf, 0x120, 0x10180)
        with self.assertRaises(ValueError) as cm:
            parse_xbe_sections(bytes(buf))
        self.assertIn("truncated", str(cm.exception).lower())

    def test_vanilla_xbe_still_parses(self):
        """Sanity: the new bounds checks don't reject a real XBE."""
        vanilla = (_REPO_ROOT.parent /
                   "Azurik - Rise of Perathia (USA).xiso" /
                   "default.xbe")
        if not vanilla.exists():
            self.skipTest(f"vanilla XBE not at {vanilla}")
        from azurik_mod.patching.xbe import parse_xbe_sections
        base, secs = parse_xbe_sections(vanilla.read_bytes())
        self.assertGreater(len(secs), 5)
        # Sanity: every real XBE has .text.
        self.assertIn(".text", {s["name"] for s in secs})


# ===========================================================================
# 3. extract_xbe_from_iso cache
# ===========================================================================


class ExtractXbeFromIsoCache(unittest.TestCase):
    """``extract_xbe_from_iso`` caches by (path, mtime, size) so a
    second call on an unchanged ISO reuses the bytearray without
    invoking xdvdfs."""

    def _reset_cache(self):
        from azurik_mod.iso import pack
        pack._xbe_cache.clear()

    def test_cache_reuses_on_repeat_call(self):
        from azurik_mod.iso import pack
        self._reset_cache()

        with tempfile.TemporaryDirectory(prefix="xbe_cache_") as tmp_s:
            tmp = Path(tmp_s)
            fake_iso = tmp / "fake.iso"
            fake_iso.write_bytes(b"fake" * 100)

            call_count = {"n": 0}

            def fake_require_xdvdfs():
                return "/bin/true"

            def fake_run_xdvdfs(xdvdfs, args):
                call_count["n"] += 1
                # args is ["copy-out", iso, "default.xbe", out]
                out = Path(args[3])
                out.write_bytes(b"FAKE_XBE_" + bytes(100))

            with mock.patch.object(pack, "require_xdvdfs", fake_require_xdvdfs), \
                 mock.patch.object(pack, "run_xdvdfs", fake_run_xdvdfs):
                a = pack.extract_xbe_from_iso(fake_iso)
                b = pack.extract_xbe_from_iso(fake_iso)

            self.assertEqual(
                call_count["n"], 1,
                msg="second call to extract_xbe_from_iso with the "
                    "same ISO must hit the cache, not rerun xdvdfs")
            self.assertIs(
                a, b,
                msg="cache should return the same bytearray instance")

    def test_cache_invalidates_on_mtime_change(self):
        from azurik_mod.iso import pack
        self._reset_cache()

        with tempfile.TemporaryDirectory(prefix="xbe_inv_") as tmp_s:
            tmp = Path(tmp_s)
            fake_iso = tmp / "fake.iso"
            fake_iso.write_bytes(b"fake" * 100)

            import time

            call_count = {"n": 0}
            def fake_require_xdvdfs(): return "/bin/true"
            def fake_run_xdvdfs(xdvdfs, args):
                call_count["n"] += 1
                Path(args[3]).write_bytes(b"FAKE_XBE_" + bytes(100))

            with mock.patch.object(pack, "require_xdvdfs", fake_require_xdvdfs), \
                 mock.patch.object(pack, "run_xdvdfs", fake_run_xdvdfs):
                pack.extract_xbe_from_iso(fake_iso)
                time.sleep(0.05)
                # Change the ISO on disk.
                fake_iso.write_bytes(b"different content here")
                pack.extract_xbe_from_iso(fake_iso)

            self.assertEqual(
                call_count["n"], 2,
                msg="mtime/size bump must invalidate the cache and "
                    "trigger a fresh xdvdfs copy-out")


# ===========================================================================
# 4. Solver DB cache across instances
# ===========================================================================


class SolverDbCache(unittest.TestCase):
    """Two ``Solver()`` constructions in one process share the same
    parsed DB dict — json.load runs once."""

    def test_two_solvers_share_parsed_db(self):
        from azurik_mod.randomizer import solver as solver_mod
        # Clear cache to be sure we're measuring the first parse.
        solver_mod._db_cache.clear()
        s1 = solver_mod.Solver()
        s2 = solver_mod.Solver()
        # Both should hold the SAME dict object, proving the cache
        # returned the same parsed value twice.
        self.assertIs(
            s1.db, s2.db,
            msg="two Solver() instances must share one parsed DB "
                "— the module-level cache should dedupe.")


# ===========================================================================
# 5. mod-template CLI
# ===========================================================================


class ModTemplateCommand(unittest.TestCase):
    """``azurik-mod mod-template`` produces a structured mod JSON
    populated with LIVE vanilla values from the given config.xbr."""

    def _loose_config(self) -> Path:
        return (_REPO_ROOT.parent /
                "Azurik - Rise of Perathia (USA).xiso" /
                "gamedata" / "config.xbr")

    def test_template_outputs_structured_mod(self):
        cfg = self._loose_config()
        if not cfg.exists():
            self.skipTest(f"config.xbr fixture not at {cfg}")
        from azurik_mod.randomizer.commands import cmd_mod_template

        with tempfile.TemporaryDirectory(prefix="mod_tmpl_") as tmp_s:
            out = Path(tmp_s) / "template.json"

            class _Args:
                pass
            args = _Args()
            args.input = str(cfg)
            args.iso = None
            args.section = ["damage"]
            args.entity = "norm_1"
            args.output = str(out)
            args.name = "test-mod"

            cmd_mod_template(args)

            mod = json.loads(out.read_text())
            self.assertEqual(mod["name"], "test-mod")
            self.assertEqual(mod["format"], "grouped")
            self.assertIn("damage", mod["sections"])
            self.assertIn("norm_1", mod["sections"]["damage"])
            # damage_multiplier + damage + delay + cost + freeze +
            # color_r/g/b = 8 properties for norm_1.
            self.assertEqual(
                len(mod["sections"]["damage"]["norm_1"]), 8,
                msg="norm_1 template must include all 8 damage "
                    "properties from the registry")


# ===========================================================================
# 6. examples/ folder is gone
# ===========================================================================


class ExamplesFolderDeleted(unittest.TestCase):
    def test_no_examples_dir(self):
        self.assertFalse(
            (_REPO_ROOT / "examples").exists(),
            msg="the examples/ folder has been deleted — its content "
                "drifted out of sync with live values and was "
                "replaced by the ``azurik-mod mod-template`` command "
                "which reads the ISO at runtime.  Don't re-introduce "
                "it.")


# ===========================================================================
# 7. Docs use azurik-mod not azurik-cli
# ===========================================================================


class DocsUseCanonicalCliName(unittest.TestCase):
    """No doc / public string should reference ``azurik-cli`` — the
    installed console script is ``azurik-mod`` per pyproject.toml."""

    def test_no_azurik_cli_references(self):
        hits = []
        search_roots = [
            _REPO_ROOT / "docs",
            _REPO_ROOT / "README.md",
            _REPO_ROOT / "CHANGELOG.md",
            _REPO_ROOT / "azurik_mod",
        ]
        for root in search_roots:
            if root.is_file():
                paths = [root]
            elif root.is_dir():
                paths = list(root.rglob("*.md")) + list(root.rglob("*.py"))
            else:
                continue
            for p in paths:
                try:
                    text = p.read_text()
                except (OSError, UnicodeDecodeError):
                    continue
                if "azurik-cli" in text:
                    hits.append(str(p.relative_to(_REPO_ROOT)))
        self.assertEqual(
            hits, [],
            msg=f"these files still reference the obsolete "
                f"`azurik-cli` console script: {hits}.  The canonical "
                f"name is `azurik-mod` (see pyproject.toml).")


# ===========================================================================
# 8. Launchers carry the dep-check guard
# ===========================================================================


class LaunchersHaveDepCheck(unittest.TestCase):
    """Both launcher scripts must import-check the ``gui`` package
    before running it, so a stale install produces a helpful error
    instead of a silent flash-and-close."""

    def test_command_has_import_guard(self):
        cmd = (_REPO_ROOT / "Launch Azurik Mod Tools.command").read_text()
        self.assertIn(
            'import gui', cmd,
            msg="Launch Azurik Mod Tools.command must try `python -c "
                "'import gui'` before running `python -m gui` so a "
                "missing dep gives a useful error instead of a flash.")
        self.assertIn(
            "pip install -e", cmd,
            msg=".command must point users at `pip install -e .` "
                "when the import check fails")

    def test_bat_has_import_guard(self):
        bat = (_REPO_ROOT / "Launch Azurik Mod Tools.bat").read_text()
        self.assertIn(
            'import gui', bat,
            msg="Launch Azurik Mod Tools.bat must try `python -c "
                "'import gui'` before running `pythonw -m gui`.")
        self.assertIn(
            "pip install -e", bat,
            msg=".bat must point Windows users at `pip install -e .` "
                "when the import check fails")


if __name__ == "__main__":
    unittest.main()
