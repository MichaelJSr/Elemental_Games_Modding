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
    """April 2026 rewrite (v3): the feature now forces
    ``dev_menu_flag_check``'s third-try fallback to always win.
    The third try is already hard-coded to
    ``PUSH "levels/selector"`` before ``CALL FUN_00053750`` at
    VA 0x00053406 — we just have to force the first two level-
    name validators (CALLs to FUN_00054520 at VA 0x00053384 and
    0x000533C3) to return 0 so their ``JZ`` instructions fire
    and flow cascades to the selector-forcing branch.

    The v1 patch (NOPing two JZs in the outer vtable gate) only
    controlled the second validator stage, which in real
    gameplay almost never fires because the caller passes a
    known-valid level.  That's why users reported v1 as
    ineffective.  v2 briefly pivoted to patching the "enable
    cheat buttons" cvar getter — but that enabled a different
    feature (in-game cheat UI), not the selector.xbr level the
    user wanted.  v3 lands exactly at the level-name decision
    point."""

    @classmethod
    def setUpClass(cls) -> None:
        import azurik_mod.patches  # noqa
        from azurik_mod.patching.registry import get_pack
        cls.pack = get_pack("enable_dev_menu")
        cls.xbe = _VANILLA_XBE.read_bytes()

    def test_category_is_experimental(self):
        self.assertEqual(self.pack.category, "experimental")

    def test_two_patch_sites(self):
        """Two 5-byte patches — one per validator stage we need
        to short-circuit."""
        self.assertEqual(len(self.pack.sites), 2)

    def test_validator_sites_target_correct_vas(self):
        """Sites must land at VA 0x53384 (first validator) and
        0x533C3 (second validator) — both CALLs to FUN_00054520
        (the level-asset probe) inside dev_menu_flag_check."""
        vas = sorted(s.va for s in self.pack.sites)
        self.assertEqual(vas, [0x00053384, 0x000533C3],
            msg="validator-site VAs drifted from expectation; "
                "check dev_menu_flag_check hasn't been re-laid-out")

    def test_vanilla_bytes_are_call_to_fun_00054520(self):
        """Vanilla bytes at each validator site must be an
        ``E8 rel32`` CALL whose target is FUN_00054520
        (VA 0x00054520).  If anything else is there, the
        surrounding validator chain has drifted and this patch
        will corrupt it."""
        import struct
        from azurik_mod.patching.xbe import va_to_file
        for site in self.pack.sites:
            off = va_to_file(site.va)
            vanilla = self.xbe[off:off + 5]
            self.assertEqual(vanilla[0], 0xE8,
                msg=f"site at VA 0x{site.va:X} is not a CALL "
                    f"(first byte {vanilla[0]:#04x})")
            rel = struct.unpack("<i", vanilla[1:5])[0]
            target = site.va + 5 + rel
            self.assertEqual(target, 0x00054520,
                msg=f"site at VA 0x{site.va:X} CALLs "
                    f"0x{target:X}, not FUN_00054520 "
                    f"(0x00054520)")

    def test_patch_bytes_are_xor_eax_plus_nops(self):
        """Replacement must be ``XOR EAX, EAX ; NOP ; NOP ; NOP``
        (5 bytes: 31 C0 90 90 90).  That forces AL=0 at the
        following TEST/JZ, which makes the JZ fire and flow fall
        into the next validator stage."""
        for site in self.pack.sites:
            self.assertEqual(site.patch,
                             bytes.fromhex("31c0909090"),
                msg=f"patch at VA 0x{site.va:X} is not "
                    f"XOR+NOPs: {site.patch.hex()}")

    def test_third_validator_is_left_intact(self):
        """The third validator at VA 0x000533E8 MUST NOT be in
        our site list — it's the fallback that actually loads
        ``"levels/selector"``.  If we short-circuited it too,
        the function would hit its ``can't find a level`` panic
        handler at VA 0x00053583."""
        third_va = 0x000533E8
        for site in self.pack.sites:
            self.assertNotEqual(site.va, third_va,
                msg="third validator MUST remain intact — it's "
                    "what actually triggers the selector load.")

    def test_third_validator_still_pushes_levels_selector(self):
        """Drift check: at VA 0x00053400..0x00053405 the
        assembly must still be ``PUSH imm32`` where the imm32
        dereferences to ``"levels/selector"``.  If this drifts,
        we're no longer forcing selector — our XOR EAX patches
        would send flow into an unknown string."""
        import struct
        from azurik_mod.patching.xbe import va_to_file
        push_off = va_to_file(0x00053400)
        push_bytes = self.xbe[push_off:push_off + 5]
        self.assertEqual(push_bytes[0], 0x68,
            msg="third-try setup must start with PUSH imm32 "
                "(opcode 0x68)")
        imm_va = struct.unpack("<I", push_bytes[1:5])[0]
        # Read the string the immediate points at.
        str_off = va_to_file(imm_va)
        self.assertEqual(self.xbe[str_off:str_off + 15],
                         b"levels/selector",
            msg=f"third-try PUSH points at VA 0x{imm_va:X} "
                f"which is not 'levels/selector' — the patch "
                f"would route flow to an unknown string")

    def test_apply_lands_ten_byte_diff(self):
        """Applying the patch must change exactly 10 bytes — 5
        per validator site, all contiguous within each site's
        window.  Catches accidental over-writes."""
        from azurik_mod.patches.enable_dev_menu import (
            apply_enable_dev_menu_patch)
        from azurik_mod.patching.xbe import va_to_file
        buf = bytearray(self.xbe)
        apply_enable_dev_menu_patch(buf)
        diffs = [i for i, (a, b) in enumerate(zip(self.xbe, buf))
                 if a != b]
        self.assertEqual(len(diffs), 10,
            msg=f"expected exactly 10 byte flips, got "
                f"{len(diffs)}")
        # Every flipped byte must live inside one of the two
        # 5-byte site windows.
        windows = []
        for site in self.pack.sites:
            start = va_to_file(site.va)
            windows.append((start, start + 5))
        for d in diffs:
            self.assertTrue(
                any(lo <= d < hi for lo, hi in windows),
                msg=f"diff at offset {d:#x} is outside the "
                    f"site windows {[hex(lo) for lo,_ in windows]}")

    def test_apply_is_idempotent(self):
        """Running apply twice produces identical bytes + doesn't
        raise — protects against drift in the apply_patch_spec
        pipeline."""
        from azurik_mod.patches.enable_dev_menu import (
            apply_enable_dev_menu_patch)
        buf = bytearray(self.xbe)
        apply_enable_dev_menu_patch(buf)
        first_run = bytes(buf)
        apply_enable_dev_menu_patch(buf)
        self.assertEqual(bytes(buf), first_run)

    def test_patched_bytes_make_validator_return_zero(self):
        """Semantic check: after patch, both validator sites
        must decode to ``XOR EAX, EAX ; NOP x3`` — if a
        future change uses a different encoding (e.g. ``MOV
        EAX, 0``), the following TEST AL, AL might still fire
        correctly, but the drift guard catches refactors that
        forgot to update the expected bytes."""
        from azurik_mod.patches.enable_dev_menu import (
            apply_enable_dev_menu_patch)
        from azurik_mod.patching.xbe import va_to_file
        buf = bytearray(self.xbe)
        apply_enable_dev_menu_patch(buf)
        for site in self.pack.sites:
            off = va_to_file(site.va)
            self.assertEqual(
                bytes(buf[off:off + 5]),
                bytes.fromhex("31c0909090"),
                msg=f"patched bytes at VA 0x{site.va:X} drifted")


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
