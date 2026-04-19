"""Tests for the Azurik save-format module.

Covers three layers:

1. **``SaveMetaXbx``** (Xbox-standard UTF-16 key/value container):
   byte-identical round-trip on a synthetic file; field accessors;
   mutation via ``set()``; graceful handling of binary tails.

2. **``AzurikSaveFile`` + ``SaveHeader``**: 20-byte header parse /
   serialise; round-trip; payload-length invariant; path-based
   dispatch into ``SignatureSav`` vs ``LevelSav`` subclasses.

3. **``SaveDirectory``**: end-to-end introspection of a scratch
   directory containing every combination of known files (plus an
   unknown extra); recognition rules; JSON-safe summary shape.

We don't test against a real vanilla save dump because none ships
with the repo — all fixtures are synthesised in-process.
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
    AzurikSaveFile,
    LevelSav,
    SaveDirectory,
    SaveHeader,
    SaveMetaXbx,
    SaveMetaField,
    SignatureSav,
)


# ===========================================================================
# SaveMetaXbx (Xbox-standard container)
# ===========================================================================


class SaveMetaXbxParsing(unittest.TestCase):
    """Synthetic SaveMeta.xbx blobs parse cleanly + round-trip."""

    def _encode(self, fields: list[tuple[str, str]],
                tail: bytes = b"") -> bytes:
        """Build a SaveMeta.xbx blob from (key, value) pairs."""
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
        self.assertEqual(len(parsed.fields), 3)

    def test_round_trip_byte_identical(self):
        blob = self._encode([
            ("Name", "Test"),
            ("TitleName", "Azurik: Rise of Perathia"),
            ("NoCopy", "1"),
        ])
        parsed = SaveMetaXbx.from_bytes(blob)
        self.assertEqual(
            parsed.to_bytes(), blob,
            msg="SaveMetaXbx must round-trip byte-identically when "
                "no fields are mutated — otherwise Xbox-side validators "
                "may reject the modified save")

    def test_unicode_value(self):
        """Non-ASCII characters should survive the UTF-16 round-trip."""
        blob = self._encode([
            ("Name", "Sav\u00e9 Game"),   # é
            ("TitleName", "Azurik"),
        ])
        parsed = SaveMetaXbx.from_bytes(blob)
        self.assertEqual(parsed.save_name, "Sav\u00e9 Game")
        self.assertEqual(parsed.to_bytes(), blob)

    def test_tail_bytes_preserved(self):
        """A binary tail after the last field must round-trip."""
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
        expected = self._encode([("Name", "New")])
        self.assertEqual(parsed.to_bytes(), expected)

    def test_set_appends_missing_field(self):
        blob = self._encode([("Name", "T")])
        parsed = SaveMetaXbx.from_bytes(blob)
        parsed.set("NoCopy", "1")
        self.assertEqual(parsed.no_copy, True)
        self.assertEqual(len(parsed.fields), 2)

    def test_get_default_on_missing(self):
        parsed = SaveMetaXbx.from_bytes(self._encode([("Name", "T")]))
        self.assertIsNone(parsed.get("NoCopy"))
        self.assertEqual(parsed.get("NoCopy", default="0"), "0")

    def test_iter_yields_every_field(self):
        blob = self._encode([("A", "1"), ("B", "2"), ("C", "3")])
        parsed = SaveMetaXbx.from_bytes(blob)
        self.assertEqual([f.key for f in parsed], ["A", "B", "C"])


# ===========================================================================
# SaveHeader (Azurik fixed 20-byte prologue)
# ===========================================================================


class SaveHeaderRoundTrip(unittest.TestCase):
    """The 20-byte ``.sav`` header parses + serialises cleanly."""

    def test_header_size_is_20_bytes(self):
        hdr = SaveHeader(
            magic=0x41534156, version=1, payload_len=0x100,
            checksum=0xDEADBEEF, reserved=0)
        self.assertEqual(len(hdr.to_bytes()), 20)

    def test_round_trip(self):
        data = struct.pack(
            "<IIIII",
            0x41534156,  # "ASAV"
            1,            # version
            0x200,        # payload_len
            0x12345678,   # checksum
            0,            # reserved
        )
        hdr = SaveHeader.from_bytes(data)
        self.assertEqual(hdr.to_bytes(), data)
        self.assertEqual(hdr.magic, 0x41534156)
        self.assertEqual(hdr.version, 1)
        self.assertEqual(hdr.payload_len, 0x200)
        self.assertEqual(hdr.checksum, 0x12345678)

    def test_magic_as_ascii_prints_printable(self):
        hdr = SaveHeader(magic=0x41534156)
        # Little-endian: bytes are 56 41 53 41 → "VASA".
        self.assertEqual(hdr.magic_as_ascii(), "VASA")

    def test_magic_as_ascii_non_printable_dots(self):
        hdr = SaveHeader(magic=0x010203FF)
        self.assertEqual(hdr.magic_as_ascii(), "....")

    def test_too_small_raises(self):
        with self.assertRaises(ValueError):
            SaveHeader.from_bytes(b"\x00" * 10)


# ===========================================================================
# AzurikSaveFile (scaffold)
# ===========================================================================


class AzurikSaveFileRoundTrip(unittest.TestCase):
    """Parse + serialise an Azurik .sav scaffold losslessly."""

    def _synth_sav(self, payload_len: int = 64) -> bytes:
        header = struct.pack(
            "<IIIII", 0x41534156, 1, payload_len, 0xDEADBEEF, 0)
        body = bytes(range(payload_len)) if payload_len <= 256 else b"\x00" * payload_len
        return header + body

    def test_parse_has_header_and_payload(self):
        blob = self._synth_sav(64)
        sav = AzurikSaveFile.from_bytes(blob)
        self.assertEqual(sav.header.magic, 0x41534156)
        self.assertEqual(sav.header.payload_len, 64)
        self.assertEqual(len(sav.payload), 64)

    def test_round_trip_equal_to_raw(self):
        blob = self._synth_sav(128)
        sav = AzurikSaveFile.from_bytes(blob)
        self.assertEqual(sav.to_bytes(), blob)
        self.assertEqual(sav.raw, blob)

    def test_too_small_raises(self):
        with self.assertRaises(ValueError):
            AzurikSaveFile.from_bytes(b"\x00" * 10)

    def test_path_dispatch_to_signature_sav(self):
        blob = self._synth_sav()
        p = Path("/tmp/signature.sav")
        sav = AzurikSaveFile.from_bytes(blob, path=p)
        self.assertIsInstance(sav, SignatureSav)

    def test_path_dispatch_to_level_sav(self):
        blob = self._synth_sav()
        p = Path("/tmp/w4.sav")
        sav = AzurikSaveFile.from_bytes(blob, path=p)
        self.assertIsInstance(sav, LevelSav)
        self.assertEqual(sav.level_id(), "w4")

    def test_no_path_yields_generic_instance(self):
        sav = AzurikSaveFile.from_bytes(self._synth_sav())
        self.assertIs(type(sav), AzurikSaveFile)

    def test_iter_chunks_yields_single_payload_chunk_by_default(self):
        sav = AzurikSaveFile.from_bytes(self._synth_sav(32))
        chunks = list(sav.iter_chunks())
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].name, "payload")
        self.assertEqual(chunks[0].offset, 0x14)
        self.assertEqual(len(chunks[0].data), 32)

    def test_summary_has_payload_match_flag(self):
        blob = self._synth_sav(64)
        sav = AzurikSaveFile.from_bytes(blob)
        summary = sav.summary()
        self.assertTrue(summary["payload_declared_matches_actual"])
        # Corrupt: claim a different payload_len and re-parse.
        corrupt = struct.pack(
            "<IIIII", 0x41534156, 1, 100, 0, 0) + blob[20:]
        sav2 = AzurikSaveFile.from_bytes(corrupt)
        self.assertFalse(sav2.summary()["payload_declared_matches_actual"])


class AzurikSaveFileDiskIO(unittest.TestCase):
    """Read + write .sav files to disk via :meth:`from_path` / :meth:`write`."""

    def test_write_and_reread_round_trips(self):
        with tempfile.TemporaryDirectory(prefix="sav_io_") as tmp:
            p = Path(tmp) / "w1.sav"
            hdr = SaveHeader(
                magic=0x53415645, version=2, payload_len=16,
                checksum=0xCAFEBABE, reserved=0)
            sav = LevelSav(
                path=p, header=hdr,
                payload=bytes(range(16)),
                raw=hdr.to_bytes() + bytes(range(16)))
            sav.write()
            self.assertTrue(p.exists())
            rehydrated = AzurikSaveFile.from_path(p)
            self.assertEqual(rehydrated.header.magic, 0x53415645)
            self.assertEqual(rehydrated.payload, bytes(range(16)))


# ===========================================================================
# SaveDirectory (whole-slot inspection)
# ===========================================================================


class SaveDirectoryInspection(unittest.TestCase):
    """End-to-end file recognition on a scratch save directory."""

    def _make_scratch(self, tmp: Path) -> Path:
        """Build a scratch save slot with every known file type + one extra."""
        # SaveMeta.xbx
        meta = (
            b"Name=Hero\r\x00\n\x00"
            b"TitleName=Azurik\r\x00\n\x00"
            b"NoCopy=1\r\x00\n\x00"
        ).decode("latin-1").encode("utf-16-le")
        # The above string uses ASCII chars so direct encode works, but
        # Python's UTF-16-LE encoding double-encodes the carriage returns
        # we embedded. Easier: synthesise directly byte-by-byte.
        meta = bytearray()
        for line in ("Name=Hero", "TitleName=Azurik", "NoCopy=1"):
            meta += line.encode("utf-16-le")
            meta += b"\r\x00\n\x00"
        (tmp / "SaveMeta.xbx").write_bytes(bytes(meta))

        # TitleMeta.xbx (one field)
        title_meta = bytearray()
        title_meta += "TitleName=Azurik".encode("utf-16-le")
        title_meta += b"\r\x00\n\x00"
        (tmp / "TitleMeta.xbx").write_bytes(bytes(title_meta))

        # SaveImage.xbx + TitleImage.xbx — opaque bytes
        (tmp / "SaveImage.xbx").write_bytes(b"\x00" * 256)
        (tmp / "TitleImage.xbx").write_bytes(b"\xFF" * 128)

        # signature.sav
        header = struct.pack("<IIIII", 0x53494753, 1, 64, 0, 0)
        (tmp / "signature.sav").write_bytes(header + bytes(range(64)))

        # one level.sav
        (tmp / "w1.sav").write_bytes(
            struct.pack("<IIIII", 0x4C565753, 1, 32, 0, 0) + bytes(range(32)))

        # extra unknown file
        (tmp / "unknown.dat").write_bytes(b"mystery data")

        return tmp

    def test_recognises_every_known_file(self):
        with tempfile.TemporaryDirectory(prefix="save_dir_") as tmp_str:
            tmp = Path(tmp_str)
            self._make_scratch(tmp)
            slot = SaveDirectory.from_directory(tmp)
            self.assertIsNotNone(slot.meta_xbx)
            self.assertIsNotNone(slot.title_meta_xbx)
            self.assertIsNotNone(slot.save_image)
            self.assertIsNotNone(slot.title_image)
            self.assertEqual(
                set(slot.sav_files.keys()),
                {"signature.sav", "w1.sav"})
            self.assertIn("unknown.dat", slot.extra_files)

    def test_summary_is_json_serialisable(self):
        with tempfile.TemporaryDirectory(prefix="save_dir_") as tmp_str:
            tmp = Path(tmp_str)
            self._make_scratch(tmp)
            slot = SaveDirectory.from_directory(tmp)
            summary = slot.summary()
            # Must round-trip through JSON.
            rendered = json.dumps(summary, default=str)
            rehydrated = json.loads(rendered)
            self.assertEqual(rehydrated["save_name"], "Hero")
            self.assertEqual(rehydrated["title_name"], "Azurik")
            self.assertTrue(rehydrated["no_copy"])
            self.assertEqual(
                set(rehydrated["sav_files"]),
                {"signature.sav", "w1.sav"})

    def test_missing_files_are_skipped_cleanly(self):
        """A partial export (e.g. just SaveMeta.xbx) must not crash."""
        with tempfile.TemporaryDirectory(prefix="save_partial_") as tmp_str:
            tmp = Path(tmp_str)
            meta = bytearray()
            meta += "Name=A".encode("utf-16-le") + b"\r\x00\n\x00"
            (tmp / "SaveMeta.xbx").write_bytes(bytes(meta))
            slot = SaveDirectory.from_directory(tmp)
            self.assertIsNotNone(slot.meta_xbx)
            self.assertIsNone(slot.title_meta_xbx)
            self.assertIsNone(slot.save_image)
            self.assertEqual(slot.sav_files, {})

    def test_raises_on_non_directory(self):
        with self.assertRaises(NotADirectoryError):
            SaveDirectory.from_directory("/nonexistent/save/path")


# ===========================================================================
# CLI smoke test
# ===========================================================================


class CliSaveInspectSmoke(unittest.TestCase):
    """``azurik-cli save inspect`` handlers produce parseable output."""

    def test_inspect_single_sav_json(self):
        from azurik_mod.save_format.commands import cmd_save_inspect
        import contextlib

        with tempfile.TemporaryDirectory(prefix="cli_sav_") as tmp_str:
            tmp = Path(tmp_str)
            p = tmp / "sample.sav"
            blob = (struct.pack("<IIIII", 0x41534156, 1, 8, 0, 0)
                    + b"\x00" * 8)
            p.write_bytes(blob)

            buf = io.StringIO()
            class _Args: pass
            args = _Args()
            args.path = str(p)
            args.json = True

            with contextlib.redirect_stdout(buf):
                cmd_save_inspect(args)
            output = buf.getvalue()
            data = json.loads(output)
            self.assertEqual(data["size_bytes"], 28)
            self.assertEqual(data["payload_actual_bytes"], 8)

    def test_inspect_directory_human_summary(self):
        """Directory inspection produces readable human output."""
        from azurik_mod.save_format.commands import cmd_save_inspect
        import contextlib

        with tempfile.TemporaryDirectory(prefix="cli_save_dir_") as tmp_str:
            tmp = Path(tmp_str)
            # Minimal scratch: one SaveMeta.xbx + one .sav.
            meta = bytearray()
            meta += "Name=Hero".encode("utf-16-le") + b"\r\x00\n\x00"
            (tmp / "SaveMeta.xbx").write_bytes(bytes(meta))
            (tmp / "w1.sav").write_bytes(
                struct.pack("<IIIII", 0x41534156, 1, 0, 0, 0))

            buf = io.StringIO()
            class _Args: pass
            args = _Args()
            args.path = str(tmp)
            args.json = False

            with contextlib.redirect_stdout(buf):
                cmd_save_inspect(args)
            output = buf.getvalue()
            self.assertIn("save directory:", output)
            self.assertIn("'Hero'", output)
            self.assertIn("w1.sav", output)


if __name__ == "__main__":
    unittest.main()
