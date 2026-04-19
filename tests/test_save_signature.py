"""Regression tests for the save-signature helper (``azurik_mod.save_format.signature``).

Covers the tree-walk behaviour we reverse-engineered from Ghidra.
The HMAC-SHA1 hash itself is platform-trivial (stdlib ``hmac`` +
``hashlib.sha1``); what matters for correctness + future
investigation is that we feed the same byte sequence the game's
signer does, in the same order.

See ``docs/SAVE_FORMAT.md`` § 7 for the full RE writeup.
"""

from __future__ import annotations

import hashlib
import hmac
import shutil
import tempfile
import unittest
from pathlib import Path

from azurik_mod.save_format.signature import (
    SIGNATURE_FILENAME,
    compute_signature,
    compute_signature_walk,
)


class _ByteStream:
    """Mock hash context that records the exact byte sequence fed
    into it — lets us assert order without relying on HMAC itself."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def update(self, data: bytes) -> None:
        self.chunks.append(bytes(data))

    @property
    def joined(self) -> bytes:
        return b"".join(self.chunks)


class SignatureWalkOrder(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="azurik-sig-"))
        self.addCleanup(
            lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _populate(self, tree: dict) -> None:
        """Materialise a dict-shaped tree on disk.

        Keys are filename components; values are either bytes
        (leaf file) or nested dicts (subdirectory).
        """
        def write(root: Path, node: dict) -> None:
            root.mkdir(parents=True, exist_ok=True)
            for name, value in node.items():
                target = root / name
                if isinstance(value, bytes):
                    target.write_bytes(value)
                elif isinstance(value, dict):
                    write(target, value)
                else:
                    raise TypeError(
                        f"unsupported tree value: {value!r}")
        write(self.tmp, tree)

    def test_basic_file_walk_order(self) -> None:
        self._populate({
            "beta.sav":  b"BETA-CONTENT",
            "alpha.sav": b"ALPHA-CONTENT",
            # .xbx files must not appear in the hash stream.
            "SaveMeta.xbx": b"IGNORED",
            # signature.sav itself must be excluded.
            SIGNATURE_FILENAME: b"\xaa" * 20,
        })
        stream = _ByteStream()
        files = compute_signature_walk(self.tmp, stream)
        self.assertEqual(files, 2)
        expected = (
            b"alpha.sav\x00ALPHA-CONTENT"
            b"beta.sav\x00BETA-CONTENT")
        self.assertEqual(stream.joined, expected)

    def test_subdir_name_hashed_then_contents(self) -> None:
        self._populate({
            "root.sav": b"ROOT",
            "levels": {
                "life.sav": b"LIFE",
                "town.sav": b"TOWN",
            },
        })
        stream = _ByteStream()
        files = compute_signature_walk(self.tmp, stream)
        self.assertEqual(files, 3)
        expected = (
            b"root.sav\x00ROOT"
            b"levels\x00"
            b"life.sav\x00LIFE"
            b"town.sav\x00TOWN")
        self.assertEqual(stream.joined, expected)

    def test_files_sorted_before_subdirs(self) -> None:
        """The game processes ALL current-directory files before
        recursing into subdirs, regardless of alphabetical
        interleaving."""
        self._populate({
            "z_first.sav": b"Z",
            "a_subdir": {"nested.sav": b"NESTED"},
            "m_middle.sav": b"M",
        })
        stream = _ByteStream()
        compute_signature_walk(self.tmp, stream)
        expected = (
            # Files in alpha order first ...
            b"m_middle.sav\x00M"
            b"z_first.sav\x00Z"
            # ... then subdirs (name prefix + contents).
            b"a_subdir\x00"
            b"nested.sav\x00NESTED")
        self.assertEqual(stream.joined, expected)

    def test_non_sav_files_at_root_skipped(self) -> None:
        """Files without a ``.sav`` suffix must not appear in the
        hash stream — matches the ``stricmp(...'.sav')`` filter
        in the game."""
        self._populate({
            "loc.sav": b"X",
            "SaveMeta.xbx": b"IGNORE_ME",
            "saveimage.xbx": b"IGNORE_ME_TOO",
            "readme.txt": b"IGNORE",
        })
        stream = _ByteStream()
        compute_signature_walk(self.tmp, stream)
        self.assertEqual(stream.joined, b"loc.sav\x00X")

    def test_empty_directory_is_valid(self) -> None:
        stream = _ByteStream()
        files = compute_signature_walk(self.tmp, stream)
        self.assertEqual(files, 0)
        self.assertEqual(stream.joined, b"")

    def test_compute_signature_is_hmac_sha1(self) -> None:
        self._populate({"loc.sav": b"X"})
        key = bytes.fromhex("00" * 16)
        sig = compute_signature(self.tmp, xbox_signature_key=key)
        reference = hmac.new(
            key, b"loc.sav\x00X",
            digestmod=hashlib.sha1).digest()
        self.assertEqual(sig, reference)

    def test_compute_signature_rejects_bad_key_length(self) -> None:
        with self.assertRaises(ValueError):
            compute_signature(
                self.tmp, xbox_signature_key=b"tooshort")

    def test_signature_determinism(self) -> None:
        """Stable output across multiple invocations — protects
        against subtle order-of-iteration regressions."""
        self._populate({
            "a.sav": b"A",
            "b.sav": b"B",
            "sub": {"c.sav": b"C"},
        })
        key = bytes(16)
        first = compute_signature(self.tmp, xbox_signature_key=key)
        second = compute_signature(self.tmp, xbox_signature_key=key)
        third = compute_signature(self.tmp, xbox_signature_key=key)
        self.assertEqual(first, second)
        self.assertEqual(first, third)


class SaveEditorSignatureIntegration(unittest.TestCase):
    """End-to-end: editor.write_to re-signs the slot when a key
    is provided and leaves ``signature_stale`` cleared."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="azurik-edit-"))
        self.addCleanup(
            lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        slot = self.tmp / "in"
        slot.mkdir()
        # Valid text save.
        (slot / "magic.sav").write_bytes(
            b"fileversion=1\n1.000000\n\x00")
        # Stale signature (will be recomputed).
        (slot / SIGNATURE_FILENAME).write_bytes(b"\xff" * 20)
        self.slot = slot

    def test_write_to_with_key_resigns(self) -> None:
        from azurik_mod.save_format.editor import (
            EditSpec, SaveEditor, SaveEditPlan)

        plan = SaveEditPlan().add(
            EditSpec(file="magic.sav", line_index=0,
                     new_value="99.000000"))
        editor = SaveEditor(self.slot)
        report = editor.apply(plan)
        self.assertTrue(report.signature_stale)

        out = self.tmp / "out"
        key = bytes.fromhex("00" * 16)
        report = editor.write_to(out, report=report,
                                 xbox_signature_key=key)
        self.assertFalse(report.signature_stale)
        written_sig = (out / SIGNATURE_FILENAME).read_bytes()
        expected_sig = compute_signature(out,
                                         xbox_signature_key=key)
        self.assertEqual(written_sig, expected_sig)

    def test_write_to_without_key_preserves_warning(self) -> None:
        from azurik_mod.save_format.editor import (
            EditSpec, SaveEditor, SaveEditPlan)

        plan = SaveEditPlan().add(
            EditSpec(file="magic.sav", line_index=0,
                     new_value="99.000000"))
        editor = SaveEditor(self.slot)
        report = editor.apply(plan)
        out = self.tmp / "out"
        report = editor.write_to(out, report=report)
        # Warning stays because no key was provided.
        self.assertTrue(report.signature_stale)


if __name__ == "__main__":
    unittest.main()
