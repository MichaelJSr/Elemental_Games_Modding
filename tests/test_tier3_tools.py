"""Tests for the Tier 3 RE tools + the ``enable_dev_menu`` feature.

Coverage layers mirror :mod:`tests.test_tier2_tools`:

- Synthetic-fixture unit tests (always run)
- Vanilla-ISO cases (skip when fixtures absent)
- CLI smoke tests
- ``enable_dev_menu`` byte-pattern pins against the live XBE

Shipped in this test file:

#11  RE session recorder   (``re_recorder``)
#12  XBR structural diff   (``xbr_diff``)
#13  Bink metadata dumper  (``bink_info``)
#15  Ghidra snapshot export (``ghidra_snapshot``)
     enable_dev_menu feature (patch bytes, category, patch set)
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_VANILLA_XBE = (_REPO.parent /
                "Azurik - Rise of Perathia (USA).xiso" /
                "default.xbe")
_VANILLA_W1 = (_REPO.parent /
               "Azurik - Rise of Perathia (USA).xiso" /
               "gamedata" / "w1.xbr")
_VANILLA_W2 = (_REPO.parent /
               "Azurik - Rise of Perathia (USA).xiso" /
               "gamedata" / "w2.xbr")
_VANILLA_MOVIES = (_REPO.parent /
                   "Azurik - Rise of Perathia (USA).xiso" /
                   "movies")


# ---------------------------------------------------------------------------
# enable_dev_menu feature
# ---------------------------------------------------------------------------


@unittest.skipUnless(_VANILLA_XBE.exists(),
                     "vanilla default.xbe required")
class EnableDevMenuFeature(unittest.TestCase):
    """April 2026 v4: the feature now trampolines the universal
    level-loader ``FUN_00053750``'s entry prologue.  Every call
    into that function (regardless of which upstream code path
    triggered the load) gets ``param_2`` (the level-name pointer)
    rewritten to point at the ``"levels/selector"`` string at
    VA 0x001A1E3C before the function body runs.  Every level
    transition — New Game, Load Save, cutscene-end, developer-
    console loadlevel — routes to selector.xbr.

    v1-v3 post-mortem:
      - v1 NOPed JZ instructions in a precursor dev-menu gate
        that almost never triggered → invisible at runtime.
      - v2 briefly pivoted to the cheat-UI cvar (separate feature;
        kept as docs).
      - v3 short-circuited three validators in ``dev_menu_flag_check``
        to force the third-stage fallback that hard-codes selector
        — but ``dev_menu_flag_check`` isn't the main entry point;
        cutscene-end transitions in FUN_00055AB0 call FUN_00053750
        DIRECTLY with "levels/water/w1" etc., bypassing the
        validator chain entirely.

    v4 hooks the UNIVERSAL level-loader entry so no upstream path
    can escape the selector override."""

    @classmethod
    def setUpClass(cls) -> None:
        import azurik_mod.patches  # noqa
        from azurik_mod.patching.registry import get_pack
        cls.pack = get_pack("enable_dev_menu")
        cls.xbe = _VANILLA_XBE.read_bytes()

    def test_category_is_experimental(self):
        self.assertEqual(self.pack.category, "experimental")

    def test_hook_site_at_fun_00053750_entry(self):
        """Single patch site: the prologue of the universal level
        loader at VA 0x00053750."""
        self.assertEqual(len(self.pack.sites), 1)
        site = self.pack.sites[0]
        self.assertEqual(site.va, 0x00053750,
            msg="feature must target FUN_00053750's prologue, "
                "not the old v3 validator short-circuits or the "
                "v2 cvar getter.")

    def test_vanilla_prologue_still_matches(self):
        """Drift check: FUN_00053750 must start with the
        7-byte prologue ``MOV EAX, [ESP+4] ; MOV ECX, [EAX+0x40]``
        that our trampoline replays inside the shim.  Any drift
        means the function's stack layout may have changed and
        the trampoline would corrupt it."""
        from azurik_mod.patching.xbe import va_to_file
        off = va_to_file(0x00053750)
        self.assertEqual(
            self.xbe[off:off + 7],
            bytes.fromhex("8b4424048b4840"),
            msg="FUN_00053750 prologue drifted")

    def test_levels_selector_string_present_at_vanilla_va(self):
        """Drift check: the string 'levels/selector' must live
        at VA 0x001A1E3C in ``.rdata``.  Our shim hard-codes
        that VA as the imm32 we write into param_2 — if the
        string has moved, the patch would override param_2 to
        point at garbage."""
        from azurik_mod.patching.xbe import va_to_file
        off = va_to_file(0x001A1E3C)
        self.assertEqual(
            self.xbe[off:off + 16],
            b"levels/selector\x00",
            msg="'levels/selector' string drifted from VA "
                "0x001A1E3C — shim would misroute param_2")

    def test_apply_installs_jmp_trampoline(self):
        """After apply, the first 5 bytes of FUN_00053750 must
        be ``E9 rel32`` (JMP near) and bytes 5-6 must be NOPs
        (preserves VA 0x00053757 as the start of ``SUB ESP``
        for CFG tools)."""
        import struct
        from azurik_mod.patches.enable_dev_menu import (
            apply_enable_dev_menu_patch)
        from azurik_mod.patching.xbe import va_to_file
        buf = bytearray(self.xbe)
        apply_enable_dev_menu_patch(buf)
        off = va_to_file(0x00053750)
        self.assertEqual(buf[off], 0xE9,
            msg="trampoline first byte must be 0xE9 (JMP near)")
        self.assertEqual(bytes(buf[off + 5:off + 7]),
                         b"\x90\x90",
            msg="trampoline bytes 5-6 must be NOP NOP padding")

    def test_shim_lands_within_xbe(self):
        """Follow the trampoline's JMP rel32 and confirm the
        target VA lives inside a loaded section of the patched
        XBE (either ``.text`` padding spill or an appended
        ``SHIMS`` section)."""
        import struct
        from azurik_mod.patches.enable_dev_menu import (
            apply_enable_dev_menu_patch)
        from azurik_mod.patching.xbe import parse_xbe_sections, va_to_file
        buf = bytearray(self.xbe)
        apply_enable_dev_menu_patch(buf)
        off = va_to_file(0x00053750)
        rel32 = struct.unpack("<i", bytes(buf[off + 1:off + 5]))[0]
        shim_va = 0x00053750 + 5 + rel32
        _, secs = parse_xbe_sections(bytes(buf))
        landed_in = None
        for s in secs:
            if s["vaddr"] <= shim_va < s["vaddr"] + s["vsize"]:
                landed_in = s["name"]
                break
        self.assertIsNotNone(landed_in,
            msg=f"shim at VA 0x{shim_va:X} isn't inside any "
                f"loaded section")

    def test_shim_decodes_to_expected_instructions(self):
        """Reconstruct the 27-byte shim from disk and verify it
        matches the layout in the module docstring: CMP + JNZ +
        MOV[ESP+8] + MOV EAX + MOV ECX + JMP."""
        import struct
        from azurik_mod.patches.enable_dev_menu import (
            apply_enable_dev_menu_patch)
        from azurik_mod.patching.xbe import parse_xbe_sections, va_to_file
        buf = bytearray(self.xbe)
        apply_enable_dev_menu_patch(buf)
        off = va_to_file(0x00053750)
        rel32 = struct.unpack("<i", bytes(buf[off + 1:off + 5]))[0]
        shim_va = 0x00053750 + 5 + rel32

        _, secs = parse_xbe_sections(bytes(buf))
        shim_fo = None
        for s in secs:
            if s["vaddr"] <= shim_va < s["vaddr"] + s["vsize"]:
                shim_fo = s["raw_addr"] + (shim_va - s["vaddr"])
                break
        self.assertIsNotNone(shim_fo)

        shim = bytes(buf[shim_fo:shim_fo + 27])
        # [0..4]: CMP DWORD [ESP+0x10], 0
        self.assertEqual(shim[0:5],
                         bytes.fromhex("837c241000"),
            msg="shim [0..4] isn't CMP [ESP+0x10], 0")
        # [5..6]: JNZ +8 (skip-override)
        self.assertEqual(shim[5:7], bytes.fromhex("7508"),
            msg="shim [5..6] isn't JNZ +8")
        # [7..14]: MOV DWORD [ESP+8], imm32
        self.assertEqual(shim[7:11], bytes.fromhex("c7442408"),
            msg="shim [7..10] isn't MOV DWORD [ESP+8], imm32")
        imm = struct.unpack("<I", shim[11:15])[0]
        self.assertEqual(imm, 0x001A1E3C,
            msg=f"shim imm32 is 0x{imm:X}, not the "
                f"'levels/selector' VA 0x001A1E3C")
        # [15..21]: replayed vanilla instructions
        self.assertEqual(shim[15:22], bytes.fromhex("8b4424048b4840"),
            msg="shim [15..21] doesn't replay the clobbered "
                "MOV EAX/MOV ECX instructions")
        # [22..26]: JMP rel32 back to 0x00053757
        self.assertEqual(shim[22], 0xE9,
            msg="shim tail byte isn't JMP near (0xE9)")
        tail_rel = struct.unpack("<i", shim[23:27])[0]
        tail_dst = (shim_va + 22) + 5 + tail_rel
        self.assertEqual(tail_dst, 0x00053757,
            msg=f"shim tail JMP goes to 0x{tail_dst:X}, not "
                f"0x00053757 (the post-prologue return point)")

    def test_apply_is_idempotent(self):
        """Running apply twice produces identical bytes + doesn't
        raise.  Protects against drift in the trampoline installer."""
        from azurik_mod.patches.enable_dev_menu import (
            apply_enable_dev_menu_patch)
        buf = bytearray(self.xbe)
        apply_enable_dev_menu_patch(buf)
        first_run = bytes(buf)
        apply_enable_dev_menu_patch(buf)
        self.assertEqual(bytes(buf), first_run,
            msg="re-apply changed the buffer — drift guard in "
                "apply_enable_dev_menu_patch missed the "
                "already-trampolined prologue")

    def test_dynamic_whitelist_covers_trampoline_and_shim(self):
        """verify-patches --strict whitelists the 7-byte prologue
        rewrite AND the 27-byte shim block.  On a vanilla XBE,
        only the prologue range is returned; after apply, the
        shim range is also returned."""
        from azurik_mod.patches.enable_dev_menu import (
            _dev_menu_dynamic_whitelist,
            apply_enable_dev_menu_patch,
        )
        # Vanilla XBE: 1 range (the 7-byte prologue).
        vanilla_ranges = _dev_menu_dynamic_whitelist(self.xbe)
        self.assertEqual(len(vanilla_ranges), 1,
            msg="vanilla XBE should yield 1 range (the "
                "prologue), not follow a non-existent JMP")
        # Patched XBE: 2 ranges (prologue + shim).
        buf = bytearray(self.xbe)
        apply_enable_dev_menu_patch(buf)
        patched_ranges = _dev_menu_dynamic_whitelist(bytes(buf))
        self.assertEqual(len(patched_ranges), 2,
            msg="patched XBE should yield 2 ranges (prologue "
                "+ shim)")
        # Verify the sizes: prologue = 7, shim = 27.
        sizes = sorted(hi - lo for lo, hi in patched_ranges)
        self.assertEqual(sizes, [7, 27],
            msg="expected one 7-byte and one 27-byte whitelist "
                "range")


# ---------------------------------------------------------------------------
# #11 RE session recorder
# ---------------------------------------------------------------------------


class SessionLogCore(unittest.TestCase):

    def test_record_and_render(self):
        from azurik_mod.xbe_tools.re_recorder import SessionLog
        log = SessionLog(title="unit test session")
        log.record("call", "foo_call", "body A")
        log.note("investigating the thing")
        log.record("call", "bar_call", "body B")
        out = log.render()
        # Header
        self.assertIn("# unit test session", out)
        # Entries in order
        for needle in ("foo_call", "body A",
                       "investigating the thing",
                       "bar_call", "body B"):
            self.assertIn(needle, out)
        # Order preserved
        self.assertLess(out.index("foo_call"),
                        out.index("bar_call"))

    def test_write_creates_file(self):
        from azurik_mod.xbe_tools.re_recorder import SessionLog
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.md"
            log = SessionLog()
            log.record("call", "tiny", "x")
            resolved = log.write(path)
            self.assertTrue(resolved.exists())
            self.assertIn("tiny", resolved.read_text())

    def test_auto_flush_when_log_path_given(self):
        from azurik_mod.xbe_tools.re_recorder import SessionLog
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flush.md"
            log = SessionLog(log_path=path)
            log.record("call", "immediate", "check")
            # File exists + has the entry before any explicit write()
            text = path.read_text()
            self.assertIn("immediate", text)


class RecordingGhidraClientProxying(unittest.TestCase):
    """Wrap a real client + confirm every public method is
    journalled AND delegates correctly."""

    def setUp(self) -> None:
        from azurik_mod.xbe_tools.mock_ghidra import MockGhidraServer
        from azurik_mod.xbe_tools.ghidra_client import GhidraClient
        from azurik_mod.xbe_tools.re_recorder import (
            RecordingGhidraClient, SessionLog)
        self.server = MockGhidraServer()
        self.server.start()
        self.addCleanup(self.server.stop)
        self.server.register_function(0x85700, "FUN_00085700")
        self.inner = GhidraClient(port=self.server.port)
        self.log = SessionLog()
        self.rec = RecordingGhidraClient(self.inner, log=self.log)

    def test_ping_is_delegated_and_logged(self):
        self.assertTrue(self.rec.ping())
        self.assertTrue(any(e.title == "ping"
                            for e in self.log.entries))

    def test_rename_function_logs_new_name(self):
        self.rec.rename_function(0x85700, "gravity_integrate_raw")
        title_hits = [e.title for e in self.log.entries
                      if "rename_function" in e.title]
        self.assertEqual(len(title_hits), 1)
        self.assertIn("gravity_integrate_raw", title_hits[0])

    def test_error_is_logged_not_swallowed(self):
        """Failed calls surface the exception AND leave a log
        entry for the attempt."""
        from azurik_mod.xbe_tools.ghidra_client import GhidraClientError
        with self.assertRaises(GhidraClientError):
            self.rec.get_function(0xDEADBEEF)
        last = self.log.entries[-1]
        self.assertIn("get_function", last.title)
        self.assertIn("ERROR", last.body)

    def test_note_injected_between_calls(self):
        self.rec.ping()
        self.log.note("observation: ping works")
        self.rec.ping()
        kinds = [e.kind for e in self.log.entries]
        self.assertEqual(kinds.count("note"), 1)
        # Note sits between two calls
        self.assertEqual(kinds[0], "call")
        self.assertEqual(kinds[1], "note")
        self.assertEqual(kinds[2], "call")


# ---------------------------------------------------------------------------
# #12 XBR diff
# ---------------------------------------------------------------------------


class XbrDiffSynthetic(unittest.TestCase):

    def test_identical_files_no_changes(self):
        """Diff of a file with itself must be empty."""
        from azurik_mod.xbe_tools.xbr_diff import diff_xbr
        # Build a minimal XBR on the fly — reuse tests' helper.
        from tests.test_xbe_tools import _make_minimal_xbe  # noqa
        blob = _make_minimal_xbe()
        with tempfile.NamedTemporaryFile(suffix=".xbr",
                                         delete=False) as tmp:
            tmp.write(blob)
            path = Path(tmp.name)
        try:
            diff = diff_xbr(path, path)
            self.assertFalse(diff.has_changes)
            self.assertEqual(diff.total_size_delta, 0)
        finally:
            path.unlink()

    def test_missing_file_raises(self):
        from azurik_mod.xbe_tools.xbr_diff import diff_xbr
        with self.assertRaises(FileNotFoundError):
            diff_xbr("/tmp/does/not/exist.xbr",
                     "/tmp/also/missing.xbr")


@unittest.skipUnless(_VANILLA_W1.exists() and _VANILLA_W2.exists(),
                     "vanilla w1.xbr + w2.xbr required")
class XbrDiffVanilla(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        from azurik_mod.xbe_tools.xbr_diff import diff_xbr
        cls.diff = diff_xbr(_VANILLA_W1, _VANILLA_W2)

    def test_reports_size_delta(self):
        self.assertLess(self.diff.total_size_delta, 0,
            msg="w2 is smaller than w1 in the vanilla ISO")

    def test_expected_tags_change_size(self):
        """Every standard level-XBR tag should appear in the
        size-delta bucket — it'd be suspicious if they DIDN'T
        differ between two completely different levels."""
        for needed in ("surf", "rdms", "node", "levl"):
            self.assertIn(needed, self.diff.size_deltas,
                msg=f"tag {needed!r} has no size delta; diff "
                    f"may be miscounting")

    def test_strings_deltas_recorded(self):
        self.assertGreater(len(self.diff.string_changes), 100,
            msg="w1 vs w2 should show hundreds of differing "
                "portal / pickup / texture strings")


# ---------------------------------------------------------------------------
# #13 Bink metadata
# ---------------------------------------------------------------------------


class BinkInfoSynthetic(unittest.TestCase):

    def _fake_bink(self, *, frames: int = 100,
                   width: int = 640, height: int = 480,
                   fps_num: int = 30, fps_den: int = 1) -> bytes:
        import struct
        header = struct.pack("<4s10I",
            b"BIKi", 0, frames, 1024, frames, width, height,
            fps_num, fps_den, 0, 1)
        return header + b"\x00" * 16  # tail padding

    def test_parses_synthetic_header(self):
        from azurik_mod.xbe_tools.bink_info import inspect_bink_file
        with tempfile.NamedTemporaryFile(suffix=".bik",
                                         delete=False) as tmp:
            tmp.write(self._fake_bink(frames=600, fps_num=60))
            path = Path(tmp.name)
        try:
            info = inspect_bink_file(path)
            self.assertEqual(info.resolution, "640x480")
            self.assertEqual(info.frame_count, 600)
            self.assertEqual(info.fps, 60.0)
            self.assertAlmostEqual(info.duration_seconds, 10.0, places=3)
            self.assertEqual(info.audio_track_count, 1)
        finally:
            path.unlink()

    def test_rejects_non_bink_magic(self):
        from azurik_mod.xbe_tools.bink_info import inspect_bink_file
        with tempfile.NamedTemporaryFile(suffix=".bik",
                                         delete=False) as tmp:
            tmp.write(b"NOPE" + b"\x00" * 60)
            path = Path(tmp.name)
        try:
            with self.assertRaises(ValueError):
                inspect_bink_file(path)
        finally:
            path.unlink()

    def test_rejects_too_small_file(self):
        from azurik_mod.xbe_tools.bink_info import inspect_bink_file
        with tempfile.NamedTemporaryFile(suffix=".bik",
                                         delete=False) as tmp:
            tmp.write(b"BIKi" + b"\x00" * 4)
            path = Path(tmp.name)
        try:
            with self.assertRaises(ValueError):
                inspect_bink_file(path)
        finally:
            path.unlink()


@unittest.skipUnless(_VANILLA_MOVIES.exists(),
                     "vanilla movies/ directory required")
class BinkInfoVanilla(unittest.TestCase):

    def test_inspect_every_shipped_movie(self):
        from azurik_mod.xbe_tools.bink_info import inspect_directory
        infos = inspect_directory(_VANILLA_MOVIES)
        # The ISO ships exactly 14 .bik files.
        self.assertEqual(len(infos), 14)
        # All are 640x480 @ 30 fps; one audio track each.
        for i in infos:
            self.assertEqual(i.resolution, "640x480")
            self.assertAlmostEqual(i.fps, 30.0, places=3)
            self.assertEqual(i.audio_track_count, 1)

    def test_adreniumlogo_matches_known_shape(self):
        """AdreniumLogo.bik has been observed at 573 frames /
        19.1 s duration — pin as a drift guard for the parser."""
        from azurik_mod.xbe_tools.bink_info import inspect_bink_file
        info = inspect_bink_file(
            _VANILLA_MOVIES / "AdreniumLogo.bik")
        self.assertEqual(info.frame_count, 573)
        self.assertAlmostEqual(info.duration_seconds, 19.1,
                               places=1)


# ---------------------------------------------------------------------------
# #15 Ghidra snapshot exporter
# ---------------------------------------------------------------------------


class GhidraSnapshotExport(unittest.TestCase):

    def setUp(self) -> None:
        from azurik_mod.xbe_tools.ghidra_client import GhidraClient
        from azurik_mod.xbe_tools.mock_ghidra import MockGhidraServer
        self.server = MockGhidraServer()
        self.server.start()
        self.addCleanup(self.server.stop)
        self.server.register_function(
            0x85700, "gravity_integrate_raw",
            signature="int gravity_integrate(float*)")
        self.server.register_function(0x11390, "FUN_00011390")
        self.server.register_label("0018C000", "kernel_thunks")
        self.client = GhidraClient(port=self.server.port)

    def test_named_only_by_default(self):
        from azurik_mod.xbe_tools.ghidra_snapshot import dump_snapshot
        snap, stats = dump_snapshot(self.client)
        names = {f["name"] for f in snap["functions"]}
        self.assertEqual(names, {"gravity_integrate_raw"},
            msg="Default-named (FUN_*) functions must be filtered out")
        self.assertEqual(stats.named_functions, 1)
        self.assertEqual(stats.total_functions, 2)

    def test_include_defaults(self):
        from azurik_mod.xbe_tools.ghidra_snapshot import dump_snapshot
        snap, _ = dump_snapshot(self.client,
                                include_default_names=True)
        names = {f["name"] for f in snap["functions"]}
        self.assertIn("FUN_00011390", names)

    def test_no_labels_flag(self):
        from azurik_mod.xbe_tools.ghidra_snapshot import dump_snapshot
        snap, _ = dump_snapshot(self.client, include_labels=False)
        self.assertEqual(snap["labels"], [])

    def test_write_to_disk_round_trip(self):
        from azurik_mod.xbe_tools.ghidra_snapshot import write_snapshot
        with tempfile.NamedTemporaryFile(suffix=".json",
                                         delete=False) as tmp:
            path = Path(tmp.name)
        try:
            write_snapshot(path, self.client)
            loaded = json.loads(path.read_text())
            self.assertEqual(loaded["schema"], 1)
            self.assertEqual(len(loaded["functions"]), 1)
        finally:
            path.unlink()


# ---------------------------------------------------------------------------
# CLI smokes
# ---------------------------------------------------------------------------


class CliSmokeTier3(unittest.TestCase):

    def _run(self, *args: str) -> tuple[int, str, str]:
        out = subprocess.run(
            [sys.executable, "-m", "azurik_mod", *args],
            capture_output=True, text=True, cwd=str(_REPO))
        return out.returncode, out.stdout, out.stderr

    def test_ghidra_snapshot_help(self):
        rc, stdout, _ = self._run("ghidra-snapshot", "--help")
        self.assertEqual(rc, 0)
        self.assertIn("--no-labels", stdout)

    def test_movies_help(self):
        rc, stdout, _ = self._run("movies", "info", "--help")
        self.assertEqual(rc, 0)
        self.assertIn("path", stdout.lower())

    @unittest.skipUnless(_VANILLA_MOVIES.exists(),
                         "vanilla movies/ required")
    def test_movies_info_cli_dir(self):
        rc, stdout, _ = self._run(
            "movies", "info", str(_VANILLA_MOVIES))
        self.assertEqual(rc, 0)
        self.assertIn("AdreniumLogo.bik", stdout)
        self.assertIn("Total:", stdout)

    @unittest.skipUnless(_VANILLA_W1.exists() and _VANILLA_W2.exists(),
                         "vanilla w1/w2 required")
    def test_xbr_diff_cli(self):
        rc, stdout, _ = self._run(
            "xbr", "diff", str(_VANILLA_W1), str(_VANILLA_W2))
        # Non-zero because the levels DO differ
        self.assertEqual(rc, 1)
        self.assertIn("Size delta", stdout)


if __name__ == "__main__":
    unittest.main()
