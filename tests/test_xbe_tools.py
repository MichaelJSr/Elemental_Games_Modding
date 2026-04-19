"""Tests for the RE/authoring tooling shipped in
``azurik_mod.xbe_tools``.

Three tools under test:

- ``xbe`` — address arithmetic / hexdump / ref+float+string scanners
- ``ghidra-coverage`` — Python-side knowledge inventory
- ``shim-inspect`` — compiled-object preview

Tests prefer the real vanilla ``default.xbe`` fixture when present
but fall back to synthetic blobs so the suite still passes on
hosts without a game install.
"""

from __future__ import annotations

import json
import struct
import subprocess
import sys
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve().parent
_REPO = _THIS.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_ISO_ROOT = (_REPO.parent / "Azurik - Rise of Perathia (USA).xiso")
_VANILLA_XBE = _ISO_ROOT / "default.xbe"

# ---------------------------------------------------------------------------
# Scanner library unit tests (synthetic fixtures, always run)
# ---------------------------------------------------------------------------


def _make_minimal_xbe(text_bytes: bytes = b"",
                     rdata_bytes: bytes = b"",
                     base_addr: int = 0x00010000) -> bytes:
    """Build a 4-section XBE skeleton that ``parse_xbe_sections``
    accepts.  Returns a ``bytes`` object; layout:

        [header 0..0x180]
        [.text raw ...]
        [.rdata raw ...]
        [.data  raw ...]
        [section name strings]

    Keeps VAs + file offsets identical for easier assertions.  Used
    by both :class:`ScannerLibrary` and :class:`GhidraCoverageCore`
    cases.
    """
    TEXT_VA  = base_addr + 0x1000
    RDATA_VA = base_addr + 0x4000
    DATA_VA  = base_addr + 0x5000
    name_base = 0x6000  # a location we can put section-name strings

    header = bytearray(0x180)
    header[0:4] = b"XBEH"
    struct.pack_into("<I", header, 0x104, base_addr)
    struct.pack_into("<I", header, 0x11C, 3)  # 3 sections
    struct.pack_into("<I", header, 0x120, base_addr + 0x140)  # section hdrs VA

    # Build 3 section headers (56 bytes each) at file offset 0x140.
    def sec_hdr(flags, vaddr, vsize, raw_addr, raw_size, name_addr):
        hdr = bytearray(56)
        struct.pack_into("<I", hdr, 0, flags)
        struct.pack_into("<I", hdr, 4, vaddr)
        struct.pack_into("<I", hdr, 8, vsize)
        struct.pack_into("<I", hdr, 12, raw_addr)
        struct.pack_into("<I", hdr, 16, raw_size)
        struct.pack_into("<I", hdr, 20, name_addr)
        return bytes(hdr)

    # Put the actual section payloads starting at 0x200 to leave room
    # for the 3x56=168 section headers starting at 0x140.
    TEXT_OFF = 0x1000  # file offset where .text data will live
    RDATA_OFF = 0x4000
    DATA_OFF = 0x5000

    names = b".text\x00.rdata\x00.data\x00"
    name_offsets = [name_base, name_base + len(b".text\x00"),
                    name_base + len(b".text\x00.rdata\x00")]

    # Virtual addresses for the name strings (same as file offsets
    # here since base_addr == 0x10000 and we placed them at file
    # offset == name_base).
    name_va_text  = base_addr + name_offsets[0]
    name_va_rdata = base_addr + name_offsets[1]
    name_va_data  = base_addr + name_offsets[2]

    header[0x140:0x140+56]      = sec_hdr(0x36, TEXT_VA,
        max(len(text_bytes), 0x10), TEXT_OFF, len(text_bytes),
        name_va_text)
    header[0x140+56:0x140+112]  = sec_hdr(0x36, RDATA_VA,
        max(len(rdata_bytes), 0x10), RDATA_OFF, len(rdata_bytes),
        name_va_rdata)
    header[0x140+112:0x140+168] = sec_hdr(0x36, DATA_VA, 0x10,
        DATA_OFF, 0, name_va_data)

    # Assemble the file: header + zero-pad up to each section's
    # raw_addr + that section's data.
    blob = bytearray(header)
    def extend_to(off): blob.extend(b"\x00" * (off - len(blob)))

    extend_to(TEXT_OFF)
    blob.extend(text_bytes)
    extend_to(RDATA_OFF)
    blob.extend(rdata_bytes)
    extend_to(DATA_OFF)
    extend_to(name_base)
    blob.extend(names)

    return bytes(blob)


class ScannerLibrary(unittest.TestCase):
    """Pure-Python scanners against a synthetic XBE."""

    def _synth(self, text_bytes=b"", rdata_bytes=b""):
        return _make_minimal_xbe(text_bytes, rdata_bytes)

    def test_resolve_address_distinguishes_va_from_file(self):
        from azurik_mod.xbe_tools.xbe_scan import resolve_address
        xbe = self._synth()
        # A value above base_addr → treated as VA.
        info = resolve_address(xbe, 0x11000)
        self.assertEqual(info.kind, "va")
        self.assertEqual(info.va, 0x11000)
        self.assertEqual(info.file_offset, 0x1000)
        self.assertEqual(info.section, "text")

        # force_kind overrides the heuristic.
        info = resolve_address(xbe, 0x1000, force_kind="file")
        self.assertEqual(info.kind, "file")
        self.assertEqual(info.file_offset, 0x1000)

    def test_find_imm32_references_detects_push_imm32(self):
        from azurik_mod.xbe_tools.xbe_scan import find_imm32_references
        # .text = PUSH 0x14003 (the imm32)
        text = b"\x68" + struct.pack("<I", 0x14003) + b"\x90" * 4
        xbe = self._synth(text_bytes=text)
        hits = find_imm32_references(xbe, 0x14003)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].kind, "push")
        self.assertEqual(hits[0].opcode, 0x68)

    def test_find_imm32_references_detects_mov(self):
        from azurik_mod.xbe_tools.xbe_scan import find_imm32_references
        # MOV EAX, 0x14003
        text = b"\xB8" + struct.pack("<I", 0x14003) + b"\x90"
        xbe = self._synth(text_bytes=text)
        hits = find_imm32_references(xbe, 0x14003)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].kind, "mov")
        self.assertEqual(hits[0].opcode, 0xB8)

    def test_find_imm32_references_detects_jmp_thunk(self):
        from azurik_mod.xbe_tools.xbe_scan import find_imm32_references
        # FF 25 <imm32>  — jump to import thunk.
        text = b"\xFF\x25" + struct.pack("<I", 0x14003) + b"\x90"
        xbe = self._synth(text_bytes=text)
        hits = find_imm32_references(xbe, 0x14003)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].kind, "jmp-thunk")

    def test_find_imm32_references_ignores_random_bytes(self):
        """A 4-byte match NOT preceded by an opcode byte is noise."""
        from azurik_mod.xbe_tools.xbe_scan import find_imm32_references
        text = b"\x00" + struct.pack("<I", 0x14003)  # 0x00 = opcode ADD
        xbe = self._synth(text_bytes=text)
        hits = find_imm32_references(xbe, 0x14003)
        self.assertEqual(len(hits), 0)

    def test_find_floats_in_range_finds_982_as_float32(self):
        """9.82f has a specific IEEE 754 representation — the scanner
        must locate it."""
        from azurik_mod.xbe_tools.xbe_scan import find_floats_in_range
        rdata = b"\x00\x00" + struct.pack("<f", 9.82)
        xbe = self._synth(rdata_bytes=rdata)
        hits = find_floats_in_range(xbe, 9.8, 9.9)
        # Should find at least one float in range.  Float-aligned scan
        # produces exactly one 9.82 match (plus occasional spurious
        # double-interpretations of unaligned bytes — filter by width
        # to pin the expected hit).
        floats = [h for h in hits if h.width == 4]
        self.assertTrue(any(abs(h.value - 9.82) < 0.01 for h in floats))

    def test_find_floats_excludes_zero(self):
        """A full block of zeros must produce zero hits — otherwise
        the report drowns in 0.0 matches."""
        from azurik_mod.xbe_tools.xbe_scan import find_floats_in_range
        xbe = self._synth(rdata_bytes=b"\x00" * 64)
        hits = find_floats_in_range(xbe, -1.0, 1.0)
        self.assertEqual(len(hits), 0)

    def test_find_strings_filters_by_min_length(self):
        from azurik_mod.xbe_tools.xbe_scan import find_strings
        rdata = b"ab\x00hello\x00longer_string\x00"
        xbe = self._synth(rdata_bytes=rdata)
        hits = find_strings(xbe, "", min_len=5)
        # Should only find "hello" + "longer_string" (len >= 5), NOT "ab".
        self.assertEqual(len(hits), 2)
        self.assertIn("hello", [h.text for h in hits])

    def test_find_strings_supports_regex(self):
        from azurik_mod.xbe_tools.xbe_scan import find_strings
        rdata = b"levels/fire/f1\x00levels/water/w1\x00other_string\x00"
        xbe = self._synth(rdata_bytes=rdata)
        hits = find_strings(xbe, r"levels/\w+/[fw]", regex=True)
        self.assertEqual(len(hits), 2)

    def test_hex_dump_emits_16_bytes_per_row(self):
        from azurik_mod.xbe_tools.xbe_scan import hex_dump
        rdata = bytes(range(32))
        xbe = self._synth(rdata_bytes=rdata)
        rows = hex_dump(xbe, 0x14000, length=32)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].raw, bytes(range(16)))
        self.assertEqual(rows[1].raw, bytes(range(16, 32)))


# ---------------------------------------------------------------------------
# Ghidra coverage unit tests
# ---------------------------------------------------------------------------


class GhidraCoverageCore(unittest.TestCase):
    """Cover the knowledge-vs-labeled diff logic."""

    def test_anchor_harvester_parses_header(self):
        import tempfile
        from azurik_mod.xbe_tools.ghidra_coverage import (
            harvest_azurik_h_anchors)
        content = (
            "#define AZURIK_FOO_VA  0x001A2B3C\n"
            "#define AZURIK_BAR_VA 0xCAFE1234u\n"
            "#define AZURIK_SHIMS_SECTION_NAME \"SHIMS\"\n"
            "int main(void) { return 0; }\n")
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".h", delete=False) as tmp:
            tmp.write(content)
            tmp.flush()
            anchors = harvest_azurik_h_anchors(Path(tmp.name))
        self.assertEqual(len(anchors), 2)
        self.assertEqual(anchors[0].name, "AZURIK_FOO_VA")
        self.assertEqual(anchors[0].va, 0x001A2B3C)
        self.assertEqual(anchors[1].va, 0xCAFE1234)
        # Non-_VA macros are ignored.
        self.assertFalse(
            any(a.name == "AZURIK_SHIMS_SECTION_NAME" for a in anchors))

    def test_snapshot_diff_classifies_correctly(self):
        """Given a knowledge set and a snapshot, verify the three-way
        classification: named / FUN_-labeled / orphan."""
        import tempfile
        from azurik_mod.xbe_tools.ghidra_coverage import (
            CoverageReport, KnownSymbol, build_coverage_report)

        # Fake Ghidra snapshot:
        # - 0x00085700 → "gravity_integrate"  (good, known)
        # - 0x0019C1AC → "FUN_0019C1AC"       (generic — unlabeled)
        # - 0x00012345 → "helper_foo"          (orphan, not in Python)
        snapshot = {
            "functions": [
                {"address": "0x00085700", "name": "gravity_integrate"},
                {"address": "0x0019C1AC", "name": "FUN_0019C1AC"},
                {"address": "0x00012345", "name": "helper_foo"},
            ],
            "labels": [],
        }
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False) as tmp:
            tmp.write(json.dumps(snapshot))
            tmp.flush()
            snap_path = Path(tmp.name)

        # Mock our Python-side knowledge via monkeypatch.
        import azurik_mod.xbe_tools.ghidra_coverage as gc
        orig_anchor = gc.harvest_azurik_h_anchors
        orig_vanilla = gc.harvest_vanilla_symbols
        orig_patch = gc.harvest_patch_sites
        gc.harvest_azurik_h_anchors = lambda p: [
            KnownSymbol(va=0x00085700, name="gravity",
                        kind="anchor"),
            KnownSymbol(va=0x0019C1AC, name="fx_magic_timer",
                        kind="anchor"),
        ]
        gc.harvest_vanilla_symbols = lambda: []
        gc.harvest_patch_sites = lambda: []
        try:
            report = build_coverage_report(snapshot_path=snap_path)
        finally:
            gc.harvest_azurik_h_anchors = orig_anchor
            gc.harvest_vanilla_symbols = orig_vanilla
            gc.harvest_patch_sites = orig_patch

        # 0x85700 is named in Ghidra → not unlabeled.
        unlabeled_vas = {s.va for s in report.unlabeled_known}
        self.assertNotIn(0x00085700, unlabeled_vas)
        # 0x19C1AC has FUN_ prefix → counts as unlabeled.
        self.assertIn(0x0019C1AC, unlabeled_vas)

        # 0x12345 is a Ghidra name that Python doesn't track.
        orphan_vas = {va for va, _ in report.orphan_ghidra}
        self.assertIn(0x00012345, orphan_vas)

    def test_offline_mode_works_without_snapshot(self):
        """No snapshot → report has only Python-side inventory, no
        unlabeled_known / orphan_ghidra entries."""
        from azurik_mod.xbe_tools.ghidra_coverage import (
            build_coverage_report, format_report)
        report = build_coverage_report(snapshot_path=None)
        self.assertEqual(report.unlabeled_known, [])
        self.assertEqual(report.orphan_ghidra, [])
        # Formatting works offline.
        text = format_report(report)
        self.assertIn("knowledge inventory", text)
        self.assertIn("No Ghidra snapshot provided", text)


# ---------------------------------------------------------------------------
# Shim-inspect unit tests
# ---------------------------------------------------------------------------

_SKIP_LOGO_OBJ = _REPO / "shims" / "build" / "qol_skip_logo.o"


@unittest.skipUnless(_SKIP_LOGO_OBJ.exists(),
                     "shims/build/qol_skip_logo.o required")
class ShimInspectPre(unittest.TestCase):
    """Verify the inspector on the shipped qol_skip_logo.o.  That .o
    is the smallest interesting shim (a 5-byte ``XOR AL,AL; RET 8``)
    so regressions here are obvious."""

    def test_reports_5_byte_text_section(self):
        from azurik_mod.xbe_tools.shim_inspect import inspect_object
        r = inspect_object(_SKIP_LOGO_OBJ)
        text = next(s for s in r.sections if s.name == ".text")
        self.assertEqual(text.raw_size, 5)
        # XOR AL,AL; RET 8 encoded as 30 C0 C2 08 00.
        self.assertEqual(text.first_bytes, "30c0c20800")

    def test_global_symbol_is_c_skip_logo(self):
        """``_c_skip_logo`` is a GLOBAL in this object (defined here,
        marked EXTERNAL for linker visibility).  The inspector must
        surface it in the symbol table even though it's not an
        undefined external — authors need to see the entry point.

        A genuinely-external symbol has ``section_number == 0``;
        ``_c_skip_logo`` has ``section_number == 1`` (the .text
        section) so ``is_external`` stays ``False``, but it still
        appears in :attr:`ShimInspection.symbols`.
        """
        from azurik_mod.xbe_tools.shim_inspect import inspect_object
        r = inspect_object(_SKIP_LOGO_OBJ)
        names = [s.name for s in r.symbols]
        self.assertIn("_c_skip_logo", names)
        sym = next(s for s in r.symbols if s.name == "_c_skip_logo")
        self.assertEqual(sym.storage_class_name, "EXTERNAL")
        self.assertEqual(sym.section_number, 1,
            msg="_c_skip_logo is DEFINED in .text (section 1), so "
                "it's a global, not an undefined extern")
        self.assertFalse(sym.is_external,
            msg="is_external must mean 'UNDEFINED extern' to be "
                "useful — defined globals have section_number > 0")

    def test_feature_folder_resolves_to_same_obj(self):
        """Passing the feature folder should resolve to the same .o."""
        from azurik_mod.xbe_tools.shim_inspect import inspect_object
        r_folder = inspect_object(
            _REPO / "azurik_mod" / "patches" / "qol_skip_logo")
        r_direct = inspect_object(_SKIP_LOGO_OBJ)
        self.assertEqual(r_folder.total_section_bytes,
                         r_direct.total_section_bytes)
        self.assertEqual(len(r_folder.sections), len(r_direct.sections))


class ShimInspectErrorPaths(unittest.TestCase):
    def test_nonexistent_path_raises(self):
        from azurik_mod.xbe_tools.shim_inspect import inspect_object
        with self.assertRaises(FileNotFoundError):
            inspect_object(Path("/tmp/definitely-does-not-exist-12345"))


# ---------------------------------------------------------------------------
# CLI-level smoke tests
# ---------------------------------------------------------------------------


class CliSmoke(unittest.TestCase):
    """End-to-end ``python -m azurik_mod <verb>`` checks that all
    three new verbs wire into the argparse dispatcher correctly."""

    def _run(self, *args: str) -> tuple[int, str, str]:
        out = subprocess.run(
            [sys.executable, "-m", "azurik_mod", *args],
            capture_output=True, text=True, cwd=str(_REPO))
        return out.returncode, out.stdout, out.stderr

    def test_xbe_help_lists_all_verbs(self):
        rc, stdout, _ = self._run("xbe", "--help")
        self.assertEqual(rc, 0)
        for verb in ("addr", "hexdump", "find-refs",
                     "find-floats", "strings", "sections"):
            self.assertIn(verb, stdout)

    def test_ghidra_coverage_runs_offline(self):
        rc, stdout, stderr = self._run("ghidra-coverage")
        self.assertEqual(rc, 0, msg=stderr)
        self.assertIn("knowledge inventory", stdout)

    def test_ghidra_coverage_json_mode(self):
        rc, stdout, _ = self._run("ghidra-coverage", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(stdout)
        self.assertIn("known_symbol_count", data)
        self.assertIn("by_kind", data)
        for kind in ("anchor", "vanilla", "patch_site"):
            self.assertIn(kind, data["by_kind"])

    @unittest.skipUnless(_VANILLA_XBE.exists(),
                         "vanilla default.xbe required")
    def test_xbe_addr_roundtrip(self):
        rc, stdout, _ = self._run(
            "xbe", "addr", "0x85700", "--xbe", str(_VANILLA_XBE),
            "--json")
        self.assertEqual(rc, 0)
        data = json.loads(stdout)
        self.assertEqual(data["va"], 0x85700)
        self.assertEqual(data["section"], "text")

    @unittest.skipUnless(_VANILLA_XBE.exists(),
                         "vanilla default.xbe required")
    def test_xbe_find_refs_string_mode(self):
        """End-to-end: locate fx_magic_timer string + find its .text
        callsites in one command.  Replicates a real RE workflow
        we've used multiple times this session."""
        rc, stdout, _ = self._run(
            "xbe", "find-refs", "--string", "fx_magic_timer",
            "--xbe", str(_VANILLA_XBE), "--json")
        self.assertEqual(rc, 0)
        data = json.loads(stdout)
        # Earlier audit found exactly 3 callsites; pin that count.
        self.assertEqual(len(data), 3)
        for entry in data:
            self.assertEqual(entry["kind"], "push")

    @unittest.skipUnless(_VANILLA_XBE.exists(),
                         "vanilla default.xbe required")
    def test_xbe_find_floats_finds_gravity(self):
        rc, stdout, _ = self._run(
            "xbe", "find-floats", "9.7", "9.9",
            "--xbe", str(_VANILLA_XBE), "--width", "float",
            "--json")
        self.assertEqual(rc, 0)
        data = json.loads(stdout)
        self.assertGreater(len(data), 0,
            msg="Azurik's gravity constant (9.8) should be findable")
        for entry in data:
            self.assertEqual(entry["width"], 4)
            self.assertGreaterEqual(entry["value"], 9.7)
            self.assertLessEqual(entry["value"], 9.9)

    @unittest.skipUnless(_SKIP_LOGO_OBJ.exists(),
                         "qol_skip_logo.o required")
    def test_shim_inspect_cli(self):
        rc, stdout, _ = self._run(
            "shim-inspect",
            "azurik_mod/patches/qol_skip_logo", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(stdout)
        self.assertIn("sections", data)
        text_section = next(s for s in data["sections"]
                            if s["name"] == ".text")
        self.assertEqual(text_section["raw_size"], 5)

    def test_missing_source_exits_nonzero(self):
        rc, _, stderr = self._run("xbe", "addr", "0x1000")
        self.assertNotEqual(rc, 0)
        self.assertIn("--iso", stderr)


if __name__ == "__main__":
    unittest.main()
