"""Regression tests for the next-wave tooling (#17 – #26).

Each tool gets a dedicated test class that pins its core
contract.  The suite is structured to be fast (no subprocess, no
live Ghidra) so it can ride along with every commit.

Covers:

- :mod:`azurik_mod.xbe_tools.decomp_cache` — read/write/invalidate
- :mod:`azurik_mod.xbe_tools.xref_aggregator` — tree build + render
- :mod:`azurik_mod.xbe_tools.call_graph` — BFS + DOT rendering
- :mod:`azurik_mod.xbe_tools.struct_diff` — header parser + diff
- :mod:`azurik_mod.xbe_tools.shim_scaffolder` — ``--emit-test``
- :mod:`azurik_mod.xbe_tools.asset_fingerprint` — build + diff
- :mod:`azurik_mod.save_format.editor` — edit spec + apply
- :mod:`azurik_mod.xbe_tools.xbr_edit` — in-place string edits
- :mod:`azurik_mod.xbe_tools.level_preview` — structural summary
- :mod:`azurik_mod.xbe_tools.bink_extract` — metadata + planner
- mock-server contract parity for all the extra endpoints
"""

from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1]))

from azurik_mod.xbe_tools.asset_fingerprint import (  # noqa: E402
    build_fingerprint, diff_fingerprints, load_fingerprint,
    save_fingerprint)
from azurik_mod.xbe_tools.call_graph import (  # noqa: E402
    build_call_graph, to_dot)
from azurik_mod.xbe_tools.decomp_cache import (  # noqa: E402
    DecompCache, program_cache_key)
from azurik_mod.xbe_tools.ghidra_client import (  # noqa: E402
    GhidraClient, GhidraClientError, GhidraDecomp,
    GhidraStruct, GhidraStructField, GhidraXref)
from azurik_mod.xbe_tools.level_preview import (  # noqa: E402
    preview_level, format_preview)
from azurik_mod.xbe_tools.mock_ghidra import (  # noqa: E402
    MockGhidraServer)
from azurik_mod.xbe_tools.struct_diff import (  # noqa: E402
    diff_structs, parse_header_structs)
from azurik_mod.xbe_tools.xref_aggregator import (  # noqa: E402
    build_xref_tree, format_tree)


_HEADER_SIZE = 0x40


def _build_synthetic_xbr(entries: list[tuple[str, bytes]]) -> bytes:
    """Tiny helper — synthesise an XBR file with a hand-picked
    set of TOC entries.  Keeps tests hermetic."""
    # Layout: 0x40 header (zero filler), TOC, then payload.
    toc_bytes = bytearray()
    payload = bytearray()
    payload_base = _HEADER_SIZE + 16 * (len(entries) + 1)
    for tag, body in entries:
        offset = payload_base + len(payload)
        toc_bytes += struct.pack(
            "<I", len(body))
        toc_bytes += tag.encode("ascii").ljust(4, b"\x00")
        toc_bytes += struct.pack("<I", 0)
        toc_bytes += struct.pack("<I", offset)
        payload += body
    # Terminator (all-zero TOC row).
    toc_bytes += b"\x00" * 16
    return bytes(bytearray(_HEADER_SIZE) + toc_bytes + payload)


# ---------------------------------------------------------------------------
# Ghidra client + mock parity for the new endpoints
# ---------------------------------------------------------------------------


class GhidraExtendedEndpoints(unittest.TestCase):
    """#20/#21/#22/#23 all lean on the same extended client API.

    These tests pin the mock-server parity so a regression in the
    mock can't silently lie about what the real plugin returns.
    """

    def setUp(self) -> None:
        self.mock = MockGhidraServer()
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.client = GhidraClient(port=self.mock.port,
                                   timeout=30.0)

    def test_decompile_happy_path(self) -> None:
        self.mock.register_function(0x85700, "FUN_00085700")
        self.mock.register_decomp(
            0x85700, "void FUN_00085700(void){}")
        decomp = self.client.decompile(0x85700)
        self.assertIsInstance(decomp, GhidraDecomp)
        self.assertEqual(decomp.address, 0x85700)
        self.assertEqual(decomp.function_name, "FUN_00085700")
        self.assertIn("FUN_00085700", decomp.decompiled)

    def test_decompile_missing_function(self) -> None:
        with self.assertRaises(GhidraClientError):
            self.client.decompile(0xDEADBEEF)

    def test_xrefs_to_and_from(self) -> None:
        self.mock.register_xref(
            from_addr=0x860c8, to_addr=0x85700,
            from_function={"address": "00085f50",
                           "name": "caller"},
            to_function={"address": "00085700",
                         "name": "callee"})
        self.mock.register_xref(
            from_addr=0x85700, to_addr=0x12345,
            ref_type="DATA",
            from_function={"address": "00085700",
                           "name": "callee"})
        to_edges = list(self.client.iter_xrefs_to(0x85700))
        from_edges = list(self.client.iter_xrefs_from(0x85700))
        self.assertEqual(len(to_edges), 1)
        self.assertEqual(to_edges[0].from_function_name, "caller")
        self.assertEqual(to_edges[0].ref_type,
                         "UNCONDITIONAL_CALL")
        self.assertEqual(len(from_edges), 1)
        self.assertEqual(from_edges[0].ref_type, "DATA")

    def test_xrefs_empty_when_no_matches(self) -> None:
        # Client surface always sets to_addr or from_addr, so an
        # unknown VA returns an empty list rather than the
        # ``MISSING_PARAM`` error envelope.
        self.assertEqual(
            list(self.client.iter_xrefs_to(0xFACEF00D)), [])

    def test_get_struct_round_trip(self) -> None:
        self.mock.register_struct(
            "CritterData", size=272,
            fields=[
                {"name": "walkSpeed", "dataType": "float",
                 "offset": 64, "length": 4},
                {"name": "runSpeed", "dataType": "float",
                 "offset": 68, "length": 4},
            ])
        s = self.client.get_struct("CritterData")
        self.assertIsInstance(s, GhidraStruct)
        self.assertEqual(s.size, 272)
        self.assertEqual(len(s.fields), 2)
        self.assertEqual(s.fields[0].name, "walkSpeed")
        self.assertEqual(s.fields[0].offset, 64)

    def test_get_struct_not_found(self) -> None:
        with self.assertRaises(GhidraClientError):
            self.client.get_struct("NOPE")

    def test_iter_structs_pagination(self) -> None:
        for i in range(3):
            self.mock.register_struct(f"S{i}", size=8 * (i + 1))
        names = {s["name"] for s in self.client.iter_structs(
            page_size=2)}
        self.assertEqual(names, {"S0", "S1", "S2"})


# ---------------------------------------------------------------------------
# #22 Decompile cache
# ---------------------------------------------------------------------------


class DecompCacheRoundTrip(unittest.TestCase):
    """Pin the on-disk cache contract end-to-end."""

    def setUp(self) -> None:
        self.mock = MockGhidraServer()
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.mock.register_function(0x85700, "FUN_00085700")
        self.mock.register_decomp(
            0x85700, "/* body */ void FUN_00085700(void){}")
        self.client = GhidraClient(port=self.mock.port,
                                   timeout=30.0)
        self.tmp = Path(tempfile.mkdtemp(prefix="azurik-cache-"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(
                self.tmp, ignore_errors=True))

    def test_hit_after_miss(self) -> None:
        cache = DecompCache(
            client=self.client,
            program_key=program_cache_key("test"),
            root=self.tmp)
        first = cache.get(0x85700)
        self.assertIn("FUN_00085700", first.decompiled)
        # Re-fetch should not re-hit the server.
        before_requests = len([
            r for r in self.mock.request_log if r[0] == "GET"])
        cache.get(0x85700)
        after_requests = len([
            r for r in self.mock.request_log if r[0] == "GET"])
        self.assertEqual(after_requests, before_requests)

    def test_invalidate_and_stats(self) -> None:
        cache = DecompCache(
            client=self.client,
            program_key=program_cache_key("test"),
            root=self.tmp)
        cache.get(0x85700)
        stats = cache.stats()
        self.assertEqual(stats["entries_on_disk"], 1)
        self.assertTrue(cache.invalidate(0x85700))
        self.assertFalse(cache.invalidate(0x85700))
        self.assertEqual(cache.stats()["entries_on_disk"], 0)

    def test_clear_removes_everything(self) -> None:
        cache = DecompCache(
            client=self.client,
            program_key=program_cache_key("test"),
            root=self.tmp)
        cache.get(0x85700)
        removed = cache.clear()
        self.assertEqual(removed, 1)
        self.assertEqual(cache.stats()["entries_on_disk"], 0)


# ---------------------------------------------------------------------------
# #21 Xref aggregator
# ---------------------------------------------------------------------------


class XrefAggregator(unittest.TestCase):
    """Pin tree building + ASCII rendering."""

    def setUp(self) -> None:
        self.mock = MockGhidraServer()
        self.mock.start()
        self.addCleanup(self.mock.stop)
        # Build a 3-level caller chain:
        #   0x1000 -> 0x2000 -> 0x3000 -> 0x4000
        for addr in (0x1000, 0x2000, 0x3000, 0x4000):
            self.mock.register_function(addr, f"FUN_{addr:X}")
        self.mock.register_xref(
            from_addr=0x2000, to_addr=0x1000,
            from_function={"address": "00002000",
                           "name": "FUN_2000"})
        self.mock.register_xref(
            from_addr=0x3000, to_addr=0x2000,
            from_function={"address": "00003000",
                           "name": "FUN_3000"})
        self.mock.register_xref(
            from_addr=0x4000, to_addr=0x3000,
            from_function={"address": "00004000",
                           "name": "FUN_4000"})
        self.client = GhidraClient(port=self.mock.port, timeout=30.0)

    def test_tree_builds_at_expected_depth(self) -> None:
        report = build_xref_tree(
            self.client, address=0x1000, max_depth=2)
        # 0x1000 is the root; 0x2000 is depth 1; 0x3000 depth 2.
        self.assertEqual(report.root.address, 0x1000)
        self.assertEqual(len(report.root.children), 1)
        self.assertEqual(
            report.root.children[0].address, 0x2000)
        self.assertEqual(
            report.root.children[0].children[0].address, 0x3000)
        # depth=2 should NOT expand 0x4000.
        self.assertEqual(
            report.root.children[0].children[0].children, [])

    def test_node_limit_marks_truncated(self) -> None:
        report = build_xref_tree(
            self.client, address=0x1000,
            max_depth=3, max_nodes=2)
        self.assertTrue(report.node_limit_hit)

    def test_format_tree_stable(self) -> None:
        report = build_xref_tree(
            self.client, address=0x1000, max_depth=1)
        rendered = format_tree(report)
        self.assertIn("0x00001000", rendered)
        self.assertIn("0x00002000", rendered)
        # Rendering should not raise on absent edges.
        self.assertIn("callers", rendered)


# ---------------------------------------------------------------------------
# #20 Call-graph explorer
# ---------------------------------------------------------------------------


class CallGraphExplorer(unittest.TestCase):
    """BFS + DOT rendering must stay stable."""

    def setUp(self) -> None:
        self.mock = MockGhidraServer()
        self.mock.start()
        self.addCleanup(self.mock.stop)
        for addr in (0x1000, 0x2000, 0x3000):
            self.mock.register_function(
                addr, f"FUN_{addr:X}")
        # Forward graph: 0x1000 -> 0x2000 -> 0x3000 (cycle back).
        self.mock.register_xref(
            from_addr=0x1000, to_addr=0x2000,
            from_function={"address": "00001000",
                           "name": "FUN_1000"},
            to_function={"address": "00002000",
                         "name": "FUN_2000"})
        self.mock.register_xref(
            from_addr=0x2000, to_addr=0x3000,
            from_function={"address": "00002000",
                           "name": "FUN_2000"},
            to_function={"address": "00003000",
                         "name": "FUN_3000"})
        self.mock.register_xref(
            from_addr=0x3000, to_addr=0x1000,
            from_function={"address": "00003000",
                           "name": "FUN_3000"},
            to_function={"address": "00001000",
                         "name": "FUN_1000"})
        self.client = GhidraClient(port=self.mock.port, timeout=30.0)

    def test_forward_bfs_terminates_on_cycle(self) -> None:
        graph = build_call_graph(
            self.client, seeds=[0x1000],
            direction="forward", max_depth=5)
        self.assertEqual(graph.node_count(), 3)
        self.assertEqual(graph.edge_count(), 3)
        self.assertFalse(graph.truncated)

    def test_dot_rendering_shape(self) -> None:
        graph = build_call_graph(
            self.client, seeds=[0x1000],
            direction="forward", max_depth=3)
        dot = to_dot(graph, title="test_graph")
        self.assertTrue(dot.startswith("digraph test_graph {"))
        self.assertIn("rankdir=LR", dot)
        self.assertIn("\"0x00001000\" -> \"0x00002000\"", dot)

    def test_max_edges_caps_walk(self) -> None:
        graph = build_call_graph(
            self.client, seeds=[0x1000],
            direction="forward", max_depth=5, max_edges=1)
        self.assertTrue(graph.truncated)


# ---------------------------------------------------------------------------
# #23 Struct type diff
# ---------------------------------------------------------------------------


class StructDiffParser(unittest.TestCase):
    """Header parser + diff pipeline.  Uses synthetic header
    strings to isolate from churn in the shipped azurik.h."""

    HEADER_SRC = """
typedef struct Foo {
    u32 a;         /* +0x00 first */
    u32 b;         /* +0x04 second */
} Foo;

typedef struct Bar {
    float x;       /* +0x00 */
    float y;       /* +0x04 */
    float z;       /* +0x08 */
} Bar;

_Static_assert(sizeof(Foo) == 8, "Foo size wrong");
"""

    def test_parse_detects_both_structs(self) -> None:
        structs = parse_header_structs(self.HEADER_SRC)
        by_name = {s.name: s for s in structs}
        self.assertIn("Foo", by_name)
        self.assertIn("Bar", by_name)
        self.assertEqual(by_name["Foo"].declared_size, 8)
        self.assertEqual(len(by_name["Foo"].fields), 2)
        self.assertEqual(
            by_name["Foo"].fields[0].offset, 0)

    def test_diff_classifies_correctly(self) -> None:
        structs = parse_header_structs(self.HEADER_SRC)
        # Build a matching Foo + a drifted Bar in Ghidra.
        matching_foo = GhidraStruct(
            name="Foo", size=8,
            fields=(
                GhidraStructField(
                    name="a", data_type="uint",
                    offset=0, length=4),
                GhidraStructField(
                    name="b", data_type="uint",
                    offset=4, length=4),
            ))
        drifted_bar = GhidraStruct(
            name="Bar", size=16,  # header says 12
            fields=(
                GhidraStructField(
                    name="x", data_type="float",
                    offset=0, length=4),
                GhidraStructField(
                    name="y", data_type="float",
                    offset=4, length=4),
                GhidraStructField(
                    name="z", data_type="float",
                    offset=8, length=4),
                GhidraStructField(
                    name="w", data_type="float",
                    offset=12, length=4),
            ))
        report = diff_structs(
            header=structs,
            ghidra_structs=[matching_foo, drifted_bar])
        by_name = {e.name: e for e in report.entries}
        self.assertEqual(by_name["Foo"].status, "ok")
        self.assertEqual(by_name["Bar"].status, "size_mismatch")


# ---------------------------------------------------------------------------
# #19 Shim test generator — scaffolder --emit-test
# ---------------------------------------------------------------------------


class ScaffolderEmitTest(unittest.TestCase):
    """Pin the test-module shape so scaffolded tests stay useful
    as the platform evolves."""

    def test_emit_test_includes_pytest_scaffold(self) -> None:
        from azurik_mod.xbe_tools.shim_scaffolder import plan_scaffold
        tmp = Path(tempfile.mkdtemp(prefix="azurik-scaffold-"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(
                tmp, ignore_errors=True))
        plan = plan_scaffold(
            "quick_test_feature",
            repo_root=tmp,
            hook_va=0x12345,
            xbe_bytes=None,
            emit_test=True)
        self.assertIsNotNone(plan.test_path)
        self.assertIsNotNone(plan.test_body)
        body = plan.test_body or ""
        self.assertIn("class QuickTestFeature", body)
        self.assertIn("EXPECTED_HOOK_VA = 0x00012345", body)
        self.assertIn("get_pack('quick_test_feature')", body)

    def test_emit_test_off_by_default(self) -> None:
        from azurik_mod.xbe_tools.shim_scaffolder import plan_scaffold
        tmp = Path(tempfile.mkdtemp(prefix="azurik-scaffold-"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(
                tmp, ignore_errors=True))
        plan = plan_scaffold(
            "quick_no_test",
            repo_root=tmp,
            hook_va=None, xbe_bytes=None)
        self.assertIsNone(plan.test_path)
        self.assertIsNone(plan.test_body)


# ---------------------------------------------------------------------------
# #25 Asset fingerprint registry
# ---------------------------------------------------------------------------


class FingerprintRoundTrip(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="azurik-fp-"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(
                self.tmp, ignore_errors=True))
        (self.tmp / "a.txt").write_bytes(b"hello")
        (self.tmp / "b.txt").write_bytes(b"world")
        sub = self.tmp / "sub"
        sub.mkdir()
        (sub / "c.txt").write_bytes(b"!!!")

    def test_build_includes_all_files(self) -> None:
        fp = build_fingerprint(self.tmp)
        paths = sorted(e.path for e in fp.entries)
        self.assertEqual(
            paths, ["a.txt", "b.txt", "sub/c.txt"])

    def test_save_and_load_is_idempotent(self) -> None:
        fp = build_fingerprint(self.tmp)
        out = self.tmp / "fp.json"
        save_fingerprint(fp, out)
        reloaded = load_fingerprint(out)
        self.assertEqual(fp.root, reloaded.root)
        self.assertEqual(
            [e.sha1 for e in fp.entries],
            [e.sha1 for e in reloaded.entries])

    def test_diff_detects_all_change_classes(self) -> None:
        before = build_fingerprint(self.tmp)
        # Mutate: modify a.txt, add d.txt, remove sub/c.txt.
        (self.tmp / "a.txt").write_bytes(b"HELLO!")
        (self.tmp / "d.txt").write_bytes(b"new")
        (self.tmp / "sub" / "c.txt").unlink()
        after = build_fingerprint(self.tmp)
        diff = diff_fingerprints(before, after)
        self.assertEqual(
            {e.path for e in diff.added}, {"d.txt"})
        self.assertEqual(
            {e.path for e in diff.removed}, {"sub/c.txt"})
        self.assertEqual(
            {new.path for _old, new in diff.modified},
            {"a.txt"})

    def test_load_rejects_unknown_version(self) -> None:
        out = self.tmp / "bad.json"
        out.write_text(json.dumps({"version": 999}),
                       encoding="utf-8")
        with self.assertRaises(ValueError):
            load_fingerprint(out)


# ---------------------------------------------------------------------------
# #17 Save-file editor
# ---------------------------------------------------------------------------


class SaveEditorCore(unittest.TestCase):
    def setUp(self) -> None:
        from azurik_mod.save_format.editor import (
            EditSpec, parse_edit_spec, SaveEditor, SaveEditPlan)
        self.EditSpec = EditSpec
        self.SaveEditor = SaveEditor
        self.SaveEditPlan = SaveEditPlan
        self.parse_edit_spec = parse_edit_spec
        self.tmp = Path(tempfile.mkdtemp(prefix="azurik-save-"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(
                self.tmp, ignore_errors=True))
        slot = self.tmp / "in"
        slot.mkdir()
        # Craft a valid text save.
        magic_body = (
            b"fileversion=1\n1.000000\n1.000000\n1.000000\n"
            b"0\n13\n"
            b"\x00" + b"\x00" * 200)
        (slot / "magic.sav").write_bytes(magic_body)
        # And a binary save we can't edit yet.
        (slot / "inv.sav").write_bytes(
            struct.pack("<II", 7, 5) + b"\x00" * 64)
        self.slot = slot

    def test_parse_edit_spec_happy_path(self) -> None:
        spec = self.parse_edit_spec("magic.sav:2=99.000000")
        self.assertEqual(spec.file, "magic.sav")
        self.assertEqual(spec.line_index, 2)
        self.assertEqual(spec.new_value, "99.000000")

    def test_parse_edit_spec_rejects_garbage(self) -> None:
        with self.assertRaises(ValueError):
            self.parse_edit_spec("bogus")
        with self.assertRaises(ValueError):
            self.parse_edit_spec("file.sav:zzz=99")

    def test_apply_and_write(self) -> None:
        plan = self.SaveEditPlan()
        plan.add(self.EditSpec(
            file="magic.sav", line_index=0,
            new_value="99.000000"))
        plan.add(self.EditSpec(
            file="inv.sav", line_index=0,
            new_value="0"))
        plan.add(self.EditSpec(
            file="missing.sav", line_index=0,
            new_value="x"))
        editor = self.SaveEditor(self.slot)
        report = editor.apply(plan)
        self.assertEqual(len(report.applied), 1)
        self.assertEqual(len(report.skipped), 2)
        self.assertTrue(report.signature_stale)
        out = self.tmp / "out"
        report = editor.write_to(out, report=report)
        data = (out / "magic.sav").read_bytes()
        self.assertIn(b"99.000000", data)
        # Other files must have been copied unchanged.
        self.assertTrue((out / "inv.sav").exists())


# ---------------------------------------------------------------------------
# #18 XBR write-back
# ---------------------------------------------------------------------------


class XbrEditorCore(unittest.TestCase):
    def setUp(self) -> None:
        from azurik_mod.xbe_tools.xbr_edit import XbrEditor, XbrEditError
        self.XbrEditor = XbrEditor
        self.XbrEditError = XbrEditError
        self.xbr_bytes = _build_synthetic_xbr([
            ("node", b"Hello\x00world\x00\x00\x00"),
            ("surf", b"PAYLOAD\x00"),
        ])
        self.tmp = Path(tempfile.mkdtemp(prefix="azurik-xbr-"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(
                self.tmp, ignore_errors=True))

    def test_string_replace_same_size(self) -> None:
        ed = self.XbrEditor(self.xbr_bytes)
        ed.replace_string_in_tag(
            old="Hello", new="HELLO", tag="node")
        out = self.tmp / "out.xbr"
        ed.write(out)
        data = out.read_bytes()
        self.assertIn(b"HELLO\x00", data)
        self.assertNotIn(b"Hello\x00", data)

    def test_string_replace_longer_rejected(self) -> None:
        ed = self.XbrEditor(self.xbr_bytes)
        with self.assertRaises(self.XbrEditError):
            ed.replace_string_in_tag(
                old="Hello", new="HelloWorld", tag="node")

    def test_replace_bytes_length_checked(self) -> None:
        ed = self.XbrEditor(self.xbr_bytes)
        with self.assertRaises(self.XbrEditError):
            ed.replace_bytes(
                len(self.xbr_bytes) - 2, b"AAAA")


# ---------------------------------------------------------------------------
# #24 Level preview
# ---------------------------------------------------------------------------


class LevelPreviewSynthetic(unittest.TestCase):
    def test_preview_collects_strings_per_tag(self) -> None:
        data = _build_synthetic_xbr([
            ("node", b"hello_node\x00spawn_point_1\x00"),
            ("surf", b"surface_mesh_a\x00surface_mesh_b\x00"),
        ])
        tmp = Path(tempfile.mkdtemp(prefix="azurik-lvl-"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(
                tmp, ignore_errors=True))
        xbr_path = tmp / "synth.xbr"
        xbr_path.write_bytes(data)
        preview = preview_level(xbr_path)
        by_tag = {s.tag: s for s in preview.summaries}
        self.assertIn("node", by_tag)
        self.assertIn("surf", by_tag)
        self.assertIn(
            "hello_node", by_tag["node"].sample_strings)
        self.assertIn(
            "surface_mesh_a", by_tag["surf"].sample_strings)

    def test_format_preview_is_stable(self) -> None:
        data = _build_synthetic_xbr([
            ("node", b"abc\x00def\x00"),
        ])
        tmp = Path(tempfile.mkdtemp(prefix="azurik-lvl-"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(
                tmp, ignore_errors=True))
        xbr_path = tmp / "synth.xbr"
        xbr_path.write_bytes(data)
        rendered = format_preview(preview_level(xbr_path))
        self.assertIn("[node]", rendered)


# ---------------------------------------------------------------------------
# #26 Bink frame extractor — planner + metadata (no subprocess)
# ---------------------------------------------------------------------------


class BinkExtractPlanner(unittest.TestCase):
    """``plan_frame_extraction`` must never crash even when
    ffmpeg isn't on PATH; ``describe_bink`` must survive short
    malformed files with a :class:`ValueError`."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="azurik-bik-"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(
                self.tmp, ignore_errors=True))

    def test_describe_bink_rejects_non_bink(self) -> None:
        from azurik_mod.xbe_tools.bink_extract import describe_bink
        p = self.tmp / "bad.bik"
        p.write_bytes(b"NOPE" + b"\x00" * 200)
        with self.assertRaises(ValueError):
            describe_bink(p)

    def test_plan_frame_extraction_returns_plan(self) -> None:
        from azurik_mod.xbe_tools.bink_extract import (
            plan_frame_extraction)
        plan = plan_frame_extraction(
            self.tmp / "any.bik",
            self.tmp / "frames")
        # Tool identity depends on whether ffmpeg is on $PATH;
        # we only care that the plan has a human-readable reason
        # and a consistent tool tag.
        self.assertIn(plan.tool, {"ffmpeg", "none"})
        self.assertTrue(plan.reason)
        self.assertEqual(plan.available, bool(plan.command))


if __name__ == "__main__":
    unittest.main()
