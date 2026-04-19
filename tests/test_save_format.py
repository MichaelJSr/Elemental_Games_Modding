"""Tests for the Azurik save-format module.

Rewritten after the format was reverse-engineered from a real save
extracted from ``xbox_hdd.qcow2`` (see ``docs/SAVE_FORMAT.md``).
Covers three layers:

1. **``SaveMetaXbx``** (Xbox-standard UTF-16 key/value container):
   byte-identical round-trip on a synthetic file; field accessors;
   mutation via ``set()``; graceful handling of binary tails.

2. **``AzurikSave`` sum type** — the kind-based dispatcher that
   recognises text / binary / signature save variants and parses
   each according to its real on-disk shape.  Exercised with BOTH
   synthetic inputs AND real-save fixtures in
   ``tests/fixtures/save/`` (scrubbed — no personal info).

3. **``SaveDirectory``** — end-to-end recursion into ``levels/``
   subdirectories; partitions results into root vs. nested buckets
   so callers can tell level saves apart from profile saves.

Fixtures in ``tests/fixtures/save/``:

- ``signature.sav``     — 20-byte SHA-1 digest (real)
- ``SaveMeta.xbx``      — 38-byte Xbox container metadata (real)
- ``options.sav``       — 23-byte short text save (real)
- ``magic_sample.sav``  — first 256 B of magic.sav (real text save)
- ``inv_sample.sav``    — first 256 B of inv.sav (real binary save)
"""

from __future__ import annotations

import io
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.save_format import (  # noqa: E402
    AzurikSave,
    AzurikSaveFile,          # legacy alias
    BinarySave,
    LevelSav,                # legacy alias
    SaveDirectory,
    SaveHeader,              # legacy scaffold
    SaveMetaField,
    SaveMetaXbx,
    SignatureSav,            # legacy alias
    SignatureSave,
    TextSave,
)

_FIXTURE_DIR = _THIS_DIR / "fixtures" / "save"


def _has_fixtures() -> bool:
    return _FIXTURE_DIR.exists() and any(_FIXTURE_DIR.iterdir())


# ===========================================================================
# SaveMetaXbx (Xbox-standard container) — unchanged from v1
# ===========================================================================


class SaveMetaXbxParsing(unittest.TestCase):
    """Synthetic SaveMeta.xbx blobs parse cleanly + round-trip."""

    def _encode(self, fields: list[tuple[str, str]],
                tail: bytes = b"") -> bytes:
        out = bytearray()
        for k, v in fields:
            out += f"{k}={v}".encode("utf-16-le")
            out += b"\r\x00\n\x00"
        out += tail
        return bytes(out)

    def test_parse_basic_three_field_blob(self):
        blob = self._encode([
            ("Name", "My Hero's Journey"),
            ("TitleName", "Azurik"),
            ("NoCopy", "1"),
        ])
        parsed = SaveMetaXbx.from_bytes(blob)
        self.assertEqual(parsed.save_name, "My Hero's Journey")
        self.assertEqual(parsed.title_name, "Azurik")
        self.assertTrue(parsed.no_copy)

    def test_round_trip_byte_identical(self):
        blob = self._encode([
            ("Name", "Test"),
            ("TitleName", "Azurik: Rise of Perathia"),
            ("NoCopy", "1"),
        ])
        parsed = SaveMetaXbx.from_bytes(blob)
        self.assertEqual(parsed.to_bytes(), blob)

    def test_unicode_value(self):
        blob = self._encode([
            ("Name", "Sav\u00e9 Game"),
            ("TitleName", "Azurik"),
        ])
        parsed = SaveMetaXbx.from_bytes(blob)
        self.assertEqual(parsed.save_name, "Sav\u00e9 Game")
        self.assertEqual(parsed.to_bytes(), blob)

    def test_tail_bytes_preserved(self):
        tail = b"\x01\x02\x03\x04\xFF\xFE"
        blob = self._encode([("Name", "T")]) + tail
        parsed = SaveMetaXbx.from_bytes(blob)
        self.assertEqual(parsed.tail, tail)
        self.assertEqual(parsed.to_bytes(), blob)

    def test_set_updates_existing_field(self):
        blob = self._encode([("Name", "Old")])
        parsed = SaveMetaXbx.from_bytes(blob)
        parsed.set("Name", "New")
        self.assertEqual(parsed.save_name, "New")

    def test_get_default_on_missing(self):
        parsed = SaveMetaXbx.from_bytes(self._encode([("Name", "T")]))
        self.assertIsNone(parsed.get("NoCopy"))
        self.assertEqual(parsed.get("NoCopy", default="0"), "0")


# ===========================================================================
# TextSave — real text-formatted saves
# ===========================================================================


class TextSaveParsing(unittest.TestCase):
    """fileversion=<N>\\n<line>\\n... layout."""

    def test_parse_minimal_text_save(self):
        data = b"fileversion=1\nhello\nworld\n"
        ts = TextSave.from_bytes(data)
        self.assertEqual(ts.version, 1)
        self.assertEqual(ts.lines, ["hello", "world"])
        self.assertEqual(ts.binary_tail, b"")

    def test_roundtrip_preserves_binary_tail(self):
        data = b"fileversion=1\nk=v\n\x00\x01\x02binary"
        ts = TextSave.from_bytes(data)
        self.assertEqual(ts.lines, ["k=v"])
        self.assertEqual(ts.binary_tail, b"\x00\x01\x02binary")
        self.assertEqual(ts.to_bytes(), data)

    def test_rejects_non_text_blob(self):
        with self.assertRaises(ValueError):
            TextSave.from_bytes(b"\x01\x02\x03\x04binary data")

    @unittest.skipUnless(_has_fixtures(), "real-save fixtures missing")
    def test_parses_real_options_sav(self):
        data = (_FIXTURE_DIR / "options.sav").read_bytes()
        # options.sav may or may not be text-shaped in all saves.
        # We just check AzurikSave classifies it consistently.
        sav = AzurikSave.from_bytes(data,
                                     path=_FIXTURE_DIR / "options.sav")
        self.assertIn(sav.kind, ("text", "binary"))

    @unittest.skipUnless(_has_fixtures(), "real-save fixtures missing")
    def test_parses_real_magic_sample(self):
        data = (_FIXTURE_DIR / "magic_sample.sav").read_bytes()
        sav = AzurikSave.from_bytes(data,
                                     path=_FIXTURE_DIR / "magic_sample.sav")
        self.assertEqual(sav.kind, "text")
        self.assertEqual(sav.text.version, 1)
        # Real magic.sav has numeric stat lines like "1.000000".
        self.assertTrue(len(sav.text.lines) > 0,
            msg="text-format magic.sav should parse at least one "
                "data line after the fileversion header")


# ===========================================================================
# BinarySave — (version, count) header + opaque body
# ===========================================================================


class BinarySaveParsing(unittest.TestCase):

    def test_parses_header_and_body(self):
        header = struct.pack("<II", 1, 42)
        body = b"\x00" * 64
        sav = BinarySave.from_bytes(header + body)
        self.assertEqual(sav.version, 1)
        self.assertEqual(sav.record_count, 42)
        self.assertEqual(sav.body, body)

    def test_round_trip(self):
        blob = struct.pack("<II", 3, 7) + bytes(range(16))
        sav = BinarySave.from_bytes(blob)
        self.assertEqual(sav.to_bytes(), blob)

    def test_too_small_raises(self):
        with self.assertRaises(ValueError):
            BinarySave.from_bytes(b"\x00\x01\x02")

    @unittest.skipUnless(_has_fixtures(), "real-save fixtures missing")
    def test_real_inv_sample_has_plausible_header(self):
        data = (_FIXTURE_DIR / "inv_sample.sav").read_bytes()
        sav = AzurikSave.from_bytes(data,
                                     path=_FIXTURE_DIR / "inv_sample.sav")
        self.assertEqual(sav.kind, "binary")
        # Real inv.sav we extracted had version=7, count=~16M of junk
        # at the truncated point.  Just verify we got a
        # version/count we can read without crashing.
        self.assertIsInstance(sav.binary.version, int)
        self.assertIsInstance(sav.binary.record_count, int)


# ===========================================================================
# SignatureSave — 20-byte SHA-1 digest
# ===========================================================================


class SignatureSaveParsing(unittest.TestCase):

    def test_parses_20_bytes(self):
        data = bytes(range(20))
        sig = SignatureSave.from_bytes(data)
        self.assertEqual(sig.digest, data)
        self.assertEqual(sig.hex(), data.hex())

    def test_rejects_wrong_length(self):
        with self.assertRaises(ValueError):
            SignatureSave.from_bytes(b"\x00" * 19)
        with self.assertRaises(ValueError):
            SignatureSave.from_bytes(b"\x00" * 21)

    def test_round_trip(self):
        data = bytes(range(20))
        self.assertEqual(SignatureSave.from_bytes(data).to_bytes(), data)

    @unittest.skipUnless(_has_fixtures(), "real-save fixtures missing")
    def test_real_signature_fixture_is_20_bytes(self):
        path = _FIXTURE_DIR / "signature.sav"
        sav = AzurikSave.from_path(path)
        self.assertEqual(sav.kind, "signature")
        self.assertEqual(len(sav.signature.digest), 20)


# ===========================================================================
# AzurikSave classifier
# ===========================================================================


class AzurikSaveClassifier(unittest.TestCase):
    """Dispatches to the correct sub-variant based on file contents +
    path hint for signature.sav."""

    def test_signature_sav_name_dispatches_to_signature(self):
        data = bytes(range(20))
        sav = AzurikSave.from_bytes(data, path=Path("/x/signature.sav"))
        self.assertEqual(sav.kind, "signature")
        self.assertIsNotNone(sav.signature)

    def test_text_prefix_dispatches_to_text(self):
        data = b"fileversion=1\nhello\n"
        sav = AzurikSave.from_bytes(data, path=Path("/x/loc.sav"))
        self.assertEqual(sav.kind, "text")

    def test_raw_binary_dispatches_to_binary(self):
        data = struct.pack("<II", 1, 4) + b"\x00" * 32
        sav = AzurikSave.from_bytes(data, path=Path("/x/shared.sav"))
        self.assertEqual(sav.kind, "binary")

    def test_short_blob_dispatches_to_unknown(self):
        sav = AzurikSave.from_bytes(b"\x00\x01", path=Path("/x/foo.sav"))
        self.assertEqual(sav.kind, "unknown")

    def test_round_trip_preserves_bytes(self):
        for payload in (
            b"fileversion=1\nline\n",
            struct.pack("<II", 1, 4) + b"\x00" * 32,
            bytes(range(20)),
        ):
            path = Path("/x/signature.sav") if len(payload) == 20 \
                else Path("/x/foo.sav")
            sav = AzurikSave.from_bytes(payload, path=path)
            self.assertEqual(sav.to_bytes(), payload,
                msg=f"round-trip failed for kind={sav.kind}")


# ===========================================================================
# SaveDirectory — recurses into levels/
# ===========================================================================


class SaveDirectoryRecurses(unittest.TestCase):
    """Real Azurik saves nest level saves under ``levels/<element>/``."""

    def _make_slot(self, tmp: Path) -> Path:
        # Root-level files
        (tmp / "SaveMeta.xbx").write_bytes(
            "Name=Hero".encode("utf-16-le") + b"\r\x00\n\x00")
        (tmp / "signature.sav").write_bytes(bytes(range(20)))
        (tmp / "inv.sav").write_bytes(
            struct.pack("<II", 1, 0) + b"\x00" * 8)
        (tmp / "loc.sav").write_bytes(b"fileversion=1\nlevels/water/w1\n")

        # Nested level saves.
        levels = tmp / "levels"
        (levels / "water").mkdir(parents=True)
        (levels / "water" / "w1.sav").write_bytes(
            struct.pack("<II", 2, 5) + b"\x00" * 32)
        (levels / "air").mkdir()
        (levels / "air" / "a1.sav").write_bytes(
            struct.pack("<II", 2, 0) + b"\x00" * 8)
        return tmp

    def test_walks_levels_subdirectory(self):
        with tempfile.TemporaryDirectory(prefix="sav_walk_") as tmp_s:
            tmp = Path(tmp_s)
            self._make_slot(tmp)
            slot = SaveDirectory.from_directory(tmp)
            self.assertIn("signature.sav", slot.sav_files)
            self.assertIn("inv.sav", slot.sav_files)
            self.assertIn("loc.sav", slot.sav_files)
            self.assertIn("levels/water/w1.sav", slot.sav_files)
            self.assertIn("levels/air/a1.sav", slot.sav_files)

    def test_summary_partitions_root_vs_level(self):
        with tempfile.TemporaryDirectory(prefix="sav_walk2_") as tmp_s:
            tmp = Path(tmp_s)
            self._make_slot(tmp)
            slot = SaveDirectory.from_directory(tmp)
            summary = slot.summary()
            self.assertEqual(summary["save_name"], "Hero")
            self.assertIn("signature.sav", summary["root_sav_files"])
            self.assertIn("levels/water/w1.sav", summary["level_sav_files"])
            self.assertIn("levels/air/a1.sav", summary["level_sav_files"])

    def test_json_serialisable(self):
        with tempfile.TemporaryDirectory(prefix="sav_json_") as tmp_s:
            tmp = Path(tmp_s)
            self._make_slot(tmp)
            slot = SaveDirectory.from_directory(tmp)
            rendered = json.dumps(slot.summary(), default=str)
            rehydrated = json.loads(rendered)
            self.assertEqual(
                set(rehydrated["level_sav_files"]),
                {"levels/water/w1.sav", "levels/air/a1.sav"})


# ===========================================================================
# CLI smoke test
# ===========================================================================


class CliSaveInspectSmoke(unittest.TestCase):
    """``azurik-mod save inspect`` handlers produce parseable output."""

    def test_inspect_single_text_sav_json(self):
        from azurik_mod.save_format.commands import cmd_save_inspect
        import contextlib

        with tempfile.TemporaryDirectory(prefix="cli_sav_") as tmp_s:
            p = Path(tmp_s) / "sample.sav"
            p.write_bytes(b"fileversion=1\nhello\nworld\n")

            buf = io.StringIO()
            class _Args: pass
            args = _Args()
            args.path = str(p)
            args.json = True

            with contextlib.redirect_stdout(buf):
                cmd_save_inspect(args)
            data = json.loads(buf.getvalue())
            self.assertEqual(data["kind"], "text")
            self.assertEqual(data["version"], 1)
            self.assertEqual(data["lines"], 2)

    def test_inspect_signature_sav_json(self):
        from azurik_mod.save_format.commands import cmd_save_inspect
        import contextlib

        with tempfile.TemporaryDirectory(prefix="cli_sig_") as tmp_s:
            p = Path(tmp_s) / "signature.sav"
            p.write_bytes(bytes(range(20)))

            buf = io.StringIO()
            class _Args: pass
            args = _Args()
            args.path = str(p)
            args.json = True

            with contextlib.redirect_stdout(buf):
                cmd_save_inspect(args)
            data = json.loads(buf.getvalue())
            self.assertEqual(data["kind"], "signature")
            self.assertIn("sha1_hex", data)

    def test_inspect_directory_includes_level_saves(self):
        from azurik_mod.save_format.commands import cmd_save_inspect
        import contextlib

        with tempfile.TemporaryDirectory(prefix="cli_dir_") as tmp_s:
            tmp = Path(tmp_s)
            # Minimal scratch: SaveMeta.xbx + signature + level nested.
            (tmp / "SaveMeta.xbx").write_bytes(
                "Name=Hero".encode("utf-16-le") + b"\r\x00\n\x00")
            (tmp / "signature.sav").write_bytes(bytes(range(20)))
            (tmp / "levels" / "water").mkdir(parents=True)
            (tmp / "levels" / "water" / "w1.sav").write_bytes(
                struct.pack("<II", 2, 1) + b"\x00" * 16)

            buf = io.StringIO()
            class _Args: pass
            args = _Args()
            args.path = str(tmp)
            args.json = True

            with contextlib.redirect_stdout(buf):
                cmd_save_inspect(args)
            data = json.loads(buf.getvalue())
            self.assertEqual(data["save_name"], "Hero")
            self.assertIn("levels/water/w1.sav", data["sav_files"])
            relpaths = {d["relpath"] for d in data["sav_details"]}
            self.assertIn("signature.sav", relpaths)
            self.assertIn("levels/water/w1.sav", relpaths)


# ===========================================================================
# Legacy alias smoke — old class names still importable
# ===========================================================================


class LegacyAliases(unittest.TestCase):
    """The old class names (``AzurikSaveFile``, ``SignatureSav``,
    ``LevelSav``, ``SaveHeader``) are preserved as thin aliases so
    pre-rewrite code doesn't break.  New code should use
    ``AzurikSave`` + its typed variants."""

    def test_old_names_importable(self):
        for cls in (AzurikSaveFile, SignatureSav, LevelSav, SaveHeader):
            self.assertTrue(callable(cls))

    def test_legacy_save_header_still_parses_20_bytes(self):
        hdr = SaveHeader.from_bytes(bytes(range(20)))
        self.assertEqual(hdr.magic, struct.unpack("<I", bytes(range(4)))[0])


if __name__ == "__main__":
    unittest.main()
