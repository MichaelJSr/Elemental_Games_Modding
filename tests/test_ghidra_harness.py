"""Tests for the Ghidra HTTP client + mock server + sync tool.

Three layers of coverage:

1. **Mock-server unit tests** — always run, zero external deps.
   Exercise the full client API (``ping``, ``program_info``,
   ``iter_functions``, ``rename_function``, ``set_comment``,
   ``iter_labels``) against the in-process :class:`MockGhidraServer`.
2. **Sync-tool unit tests** — drive :func:`plan_sync` +
   :func:`apply_sync` against the mock server so the dry-run
   and apply paths are both CI-exercised.  Force / protect-
   existing-name logic gets its own cases.
3. **Live-Ghidra smoke tests** (optional) — skip when no local
   instance answers on port 8193.  Confirm the client's HTTP
   contract matches what the real plugin ships today.  Every
   live test is read-only to avoid mutating the user's Ghidra
   project.

To run just the live tests (assumes Ghidra is open on :8193):

    AZURIK_TEST_LIVE_GHIDRA=1 python3 -m pytest tests/test_ghidra_harness.py::LiveGhidraReadOnly -v
"""

from __future__ import annotations

import os
import socket
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    """Return True if something answers on ``host:port``."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Mock-server unit tests — always run
# ---------------------------------------------------------------------------


class MockServerClientRoundTrip(unittest.TestCase):
    """Full client ↔ mock round-trip for every implemented
    endpoint."""

    def setUp(self) -> None:
        from azurik_mod.xbe_tools.mock_ghidra import MockGhidraServer
        from azurik_mod.xbe_tools.ghidra_client import GhidraClient
        self.server = MockGhidraServer()
        self.server.start()
        self.addCleanup(self.server.stop)
        self.client = GhidraClient(port=self.server.port,
                                   host=self.server.host)

    def test_ping_succeeds_on_empty_program(self):
        self.assertTrue(self.client.ping())

    def test_program_info(self):
        info = self.client.program_info()
        self.assertEqual(info.name, "test.xbe")
        self.assertEqual(info.image_base, 0x00010000)

    def test_get_function(self):
        self.server.register_function(
            0x00085700, "FUN_00085700",
            signature="undefined gravity(undefined *)")
        fn = self.client.get_function(0x00085700)
        self.assertEqual(fn.address, 0x00085700)
        self.assertEqual(fn.name, "FUN_00085700")
        self.assertIn("gravity", fn.signature or "")

    def test_get_function_missing_raises(self):
        from azurik_mod.xbe_tools.ghidra_client import GhidraClientError
        with self.assertRaises(GhidraClientError) as ctx:
            self.client.get_function(0xDEADBEEF)
        self.assertEqual(ctx.exception.code, "FUNCTION_NOT_FOUND")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_iter_functions_pages(self):
        for i in range(250):
            self.server.register_function(
                0x00010000 + i * 16, f"FUN_{0x00010000 + i*16:08x}")
        funcs = list(self.client.iter_functions(page_size=100))
        self.assertEqual(len(funcs), 250)

    def test_rename_function(self):
        self.server.register_function(0x00085700, "FUN_00085700")
        self.client.rename_function(0x00085700, "gravity_integrate_raw")
        self.assertEqual(
            self.server.functions[0x00085700].name,
            "gravity_integrate_raw")

    def test_set_function_signature(self):
        self.server.register_function(0x00085700, "FUN_00085700")
        self.client.set_function_signature(
            0x00085700, "int gravity_integrate_raw(float *)")
        self.assertIn(
            "gravity_integrate_raw",
            self.server.functions[0x00085700].signature)

    def test_set_comment(self):
        self.server.register_function(0x00085700, "FUN_00085700")
        self.client.set_comment(0x00085700, "Test comment",
                                kind="plate")
        self.assertEqual(
            self.server.comments[(0x00085700, "plate")],
            "Test comment")

    def test_set_comment_rejects_bad_kind(self):
        with self.assertRaises(ValueError):
            self.client.set_comment(0x00085700, "bad", kind="banana")

    def test_iter_labels(self):
        self.server.register_label("00012345", "helper_foo")
        self.server.register_label("00012400", "helper_bar")
        labels = list(self.client.iter_labels())
        names = {lbl.name for lbl in labels}
        self.assertEqual(names, {"helper_foo", "helper_bar"})

    def test_request_log_records_every_call(self):
        self.server.register_function(0x00085700, "FUN_00085700")
        self.client.ping()
        self.client.get_function(0x00085700)
        self.client.rename_function(0x00085700, "g")
        methods = [m for m, _ in self.server.request_log]
        paths = [p for _, p in self.server.request_log]
        self.assertEqual(methods[-3:], ["GET", "GET", "PATCH"])
        self.assertTrue(any("/functions/00085700" in p for p in paths))


class MockServerErrorPaths(unittest.TestCase):
    """Client must surface Ghidra error envelopes as typed
    exceptions."""

    def setUp(self) -> None:
        from azurik_mod.xbe_tools.mock_ghidra import MockGhidraServer
        from azurik_mod.xbe_tools.ghidra_client import GhidraClient
        self.server = MockGhidraServer()
        self.server.start()
        self.addCleanup(self.server.stop)
        self.client = GhidraClient(port=self.server.port)

    def test_unknown_endpoint_raises(self):
        """``_request`` returns a _Response envelope.  Consumers
        who want exceptions on failure route it through the
        client's ``_require_success`` — which converts the
        structured error into a :class:`GhidraClientError`.
        """
        from azurik_mod.xbe_tools.ghidra_client import (
            GhidraClientError)
        resp = self.client._request("GET", "/nope")
        self.assertFalse(resp.success)
        self.assertEqual(resp.body["error"]["code"],
                         "ENDPOINT_NOT_FOUND")
        with self.assertRaises(GhidraClientError) as ctx:
            self.client._require_success(resp)
        self.assertEqual(ctx.exception.code, "ENDPOINT_NOT_FOUND")


class GhidraSyncPlanning(unittest.TestCase):
    """Drive plan_sync + apply_sync against a mocked Ghidra."""

    def setUp(self) -> None:
        import azurik_mod.patches  # ensure packs registered
        from azurik_mod.xbe_tools.ghidra_client import GhidraClient
        from azurik_mod.xbe_tools.mock_ghidra import MockGhidraServer
        self.server = MockGhidraServer()
        self.server.start()
        self.addCleanup(self.server.stop)
        self.client = GhidraClient(port=self.server.port)

        # Register a few vanilla-symbol VAs pointing at
        # default-named functions so plan_sync has something to
        # rename.
        self.server.register_function(
            0x00018980, "FUN_00018980",
            signature="undefined FUN_00018980(void)")
        self.server.register_function(
            0x00085700, "FUN_00085700",
            signature="undefined FUN_00085700(undefined *)")
        # One function is ALREADY renamed — plan_sync should
        # recognise it as "keep" not "rename".
        self.server.register_function(
            0x0004B510, "entity_lookup",
            signature="entity_lookup(char *)")

    def test_plan_classifies_rename_vs_keep(self):
        from azurik_mod.xbe_tools.ghidra_sync import plan_sync
        actions = plan_sync(self.client)
        by_kind = {a.kind: [] for a in actions}
        for a in actions:
            by_kind.setdefault(a.kind, []).append(a)
        # play_movie_fn + gravity_integrate_raw → rename (their
        # Ghidra current name is still FUN_*)
        rename_names = {a.new_name for a in by_kind.get("rename", [])}
        self.assertIn("play_movie_fn", rename_names)
        self.assertIn("gravity_integrate_raw", rename_names)
        # entity_lookup → keep (already named correctly)
        keep_vas = {a.va for a in by_kind.get("keep", [])}
        self.assertIn(0x0004B510, keep_vas)

    def test_apply_actually_renames_via_client(self):
        from azurik_mod.xbe_tools.ghidra_sync import (
            apply_sync, plan_sync)
        actions = plan_sync(self.client)
        report = apply_sync(self.client, actions)
        self.assertGreaterEqual(report.renamed, 2)
        # Verify the mock's state reflects the rename.
        self.assertEqual(
            self.server.functions[0x00018980].name, "play_movie_fn")
        self.assertEqual(
            self.server.functions[0x00085700].name,
            "gravity_integrate_raw")

    def test_apply_skips_protected_name_without_force(self):
        """A function already named ``protected_name`` (non-FUN_*)
        must NOT be overwritten unless ``force=True``."""
        # Set 0x18980 to a human-meaningful name first.
        self.server.register_function(
            0x00018980, "some_cool_custom_name")
        from azurik_mod.xbe_tools.ghidra_sync import (
            apply_sync, plan_sync)
        actions = plan_sync(self.client)
        # Find the rename action for 0x18980.
        play_movie_action = next(a for a in actions
                                 if a.va == 0x00018980)
        self.assertEqual(play_movie_action.kind, "rename")
        report = apply_sync(self.client, [play_movie_action],
                            force=False)
        self.assertEqual(report.skipped, 1)
        # Name unchanged.
        self.assertEqual(
            self.server.functions[0x00018980].name,
            "some_cool_custom_name")

    def test_apply_force_overwrites_protected_name(self):
        self.server.register_function(
            0x00018980, "some_cool_custom_name")
        from azurik_mod.xbe_tools.ghidra_sync import (
            apply_sync, plan_sync)
        actions = plan_sync(self.client)
        play_movie_action = next(a for a in actions
                                 if a.va == 0x00018980)
        apply_sync(self.client, [play_movie_action], force=True)
        self.assertEqual(
            self.server.functions[0x00018980].name, "play_movie_fn")


class GhidraCoverageLiveClient(unittest.TestCase):
    """When given a client, build_coverage_report uses it instead
    of the snapshot file."""

    def setUp(self) -> None:
        import azurik_mod.patches  # noqa
        from azurik_mod.xbe_tools.ghidra_client import GhidraClient
        from azurik_mod.xbe_tools.mock_ghidra import MockGhidraServer
        self.server = MockGhidraServer()
        self.server.start()
        self.addCleanup(self.server.stop)
        self.client = GhidraClient(port=self.server.port)

    def test_unlabeled_knowledge_populated(self):
        """Register a function AT a known VA under its default
        FUN_* name — the coverage report must flag it as
        unlabeled-known."""
        self.server.register_function(0x00085700, "FUN_00085700")
        from azurik_mod.xbe_tools.ghidra_coverage import (
            build_coverage_report)
        report = build_coverage_report(live_client=self.client)
        self.assertIsNotNone(report.snapshot_path)
        self.assertTrue(report.snapshot_path.startswith("live:"))
        unlabeled_vas = {s.va for s in report.unlabeled_known}
        self.assertIn(0x00085700, unlabeled_vas,
            msg="gravity VA is still FUN_00085700 in the mock — "
                "build_coverage_report should flag it unlabeled")

    def test_meaningful_name_not_flagged(self):
        self.server.register_function(0x00085700,
                                       "gravity_integrate_raw")
        from azurik_mod.xbe_tools.ghidra_coverage import (
            build_coverage_report)
        report = build_coverage_report(live_client=self.client)
        unlabeled_vas = {s.va for s in report.unlabeled_known}
        self.assertNotIn(0x00085700, unlabeled_vas)


# ---------------------------------------------------------------------------
# Live-Ghidra tests — opt-in via env var OR implicit when a port is up
# ---------------------------------------------------------------------------

_LIVE_PORT = int(os.environ.get("AZURIK_GHIDRA_PORT", "8193"))
_LIVE_HOST = os.environ.get("AZURIK_GHIDRA_HOST", "localhost")


@unittest.skipUnless(_port_open(_LIVE_HOST, _LIVE_PORT, timeout=0.2),
                     f"no live Ghidra on {_LIVE_HOST}:{_LIVE_PORT}")
class LiveGhidraReadOnly(unittest.TestCase):
    """Read-only smoke tests against whatever Ghidra is currently
    serving on port 8193.  Never mutate project state."""

    @classmethod
    def setUpClass(cls) -> None:
        from azurik_mod.xbe_tools.ghidra_client import GhidraClient
        cls.client = GhidraClient(host=_LIVE_HOST, port=_LIVE_PORT)

    def test_ping_and_program_info(self):
        self.assertTrue(self.client.ping())
        info = self.client.program_info()
        # The live instance might be any of Azurik's files —
        # just check the fields parse sanely.
        self.assertTrue(info.name)
        self.assertGreater(info.image_base, 0)

    def test_get_known_function_if_default_xbe(self):
        """If the live instance is default.xbe, 0x85700 should be
        a real function.  Otherwise skip this case."""
        info = self.client.program_info()
        if info.name != "default.xbe":
            self.skipTest(f"live instance is {info.name}, "
                          f"not default.xbe")
        fn = self.client.get_function(0x00085700)
        self.assertEqual(fn.address, 0x00085700)


if __name__ == "__main__":
    unittest.main()
