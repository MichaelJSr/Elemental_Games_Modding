"""Regression tests for the save-signature key recovery tool.

Pins the scanner's behaviour end-to-end using synthetic fixtures
so the tests are hermetic (no real Azurik save required).
"""

from __future__ import annotations

import hashlib
import hmac
import shutil
import tempfile
import unittest
from pathlib import Path

from azurik_mod.save_format.key_recover import (
    KeyCandidate,
    SaveSample,
    load_save_sample,
    recover_keys,
)
from azurik_mod.save_format.signature import (
    SIGNATURE_FILENAME,
    compute_signature,
)


def _make_slot(tmp: Path, name: str, files: dict[str, bytes],
               *, key: bytes) -> Path:
    """Build a save slot with ``files`` + a matching
    signature.sav computed via the real HMAC-SHA1 walker."""
    slot = tmp / name
    slot.mkdir(parents=True)
    for rel, data in files.items():
        target = slot / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (slot / SIGNATURE_FILENAME).write_bytes(
        compute_signature(slot, xbox_signature_key=key))
    return slot


class KeyRecoverFromDump(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="azurik-kr-"))
        self.addCleanup(
            lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        # Fixed 16-byte key we'll embed in a synthetic "RAM dump".
        self.key = bytes.fromhex(
            "0011223344556677889900aabbccddee")
        # Two slots with different content — rules out false positives.
        self.slot1 = _make_slot(
            self.tmp, "slot1",
            {"magic.sav": b"fileversion=1\n1.000000\n\x00"},
            key=self.key)
        self.slot2 = _make_slot(
            self.tmp, "slot2",
            {"magic.sav": b"fileversion=1\n99.000000\n\x00",
             "loc.sav":   b"fileversion=1\nlevels/air/a1\n\x00"},
            key=self.key)

    def _dump_containing(self, at_offset: int) -> bytes:
        """Build a test dump with our key placed at ``at_offset``
        inside a sea of zeros.  Keeps the dump aligned to 64 so
        alignment=64 still lands on the key; simpler to reason
        about."""
        dump = bytearray(1024)
        dump[at_offset:at_offset + 16] = self.key
        return bytes(dump)

    def test_recover_key_with_two_samples(self) -> None:
        dump = self._dump_containing(256)
        samples = [load_save_sample(self.slot1),
                   load_save_sample(self.slot2)]
        hits = list(recover_keys(dump, samples, alignment=1))
        self.assertTrue(hits)
        self.assertTrue(any(h.offset == 256 and h.key == self.key
                            for h in hits))

    def test_recover_respects_alignment(self) -> None:
        # Place key at odd offset 257 — alignment=4 should MISS.
        dump = self._dump_containing(257)
        samples = [load_save_sample(self.slot1)]
        hits = list(recover_keys(dump, samples, alignment=4))
        self.assertFalse(hits,
            msg="alignment=4 should not land on an offset-1 key")
        # Alignment=1 must find it.
        hits = list(recover_keys(dump, samples, alignment=1))
        self.assertTrue(hits)

    def test_no_key_in_empty_dump(self) -> None:
        empty = bytes(1024)
        samples = [load_save_sample(self.slot1)]
        self.assertEqual(
            list(recover_keys(empty, samples, alignment=1)), [])

    def test_wrong_key_rejected(self) -> None:
        """A different key must not produce a match even though
        HMAC-SHA1 is deterministic."""
        wrong = bytes.fromhex("ff" * 16)
        dump = bytearray(1024)
        dump[100:116] = wrong
        samples = [load_save_sample(self.slot1)]
        hits = list(recover_keys(bytes(dump), samples, alignment=1))
        self.assertFalse(hits)

    def test_two_samples_rule_out_false_positive(self) -> None:
        """If one slot's expected signature is tampered, the
        scanner must not report any key as matching 'both'."""
        samples = [
            load_save_sample(self.slot1),
            # Tamper the second sample's signature:
            SaveSample(
                slot_path=str(self.slot2),
                walk_bytes=load_save_sample(
                    self.slot2).walk_bytes,
                expected_signature=b"\x00" * 20),
        ]
        dump = self._dump_containing(256)
        hits = list(recover_keys(dump, samples, alignment=1))
        self.assertFalse(hits)

    def test_early_exit_stops_at_first_hit(self) -> None:
        # Plant the key at three offsets; with early_exit_after=1
        # we must get exactly one hit.
        dump = bytearray(1024)
        for off in (64, 256, 512):
            dump[off:off + 16] = self.key
        samples = [load_save_sample(self.slot1)]
        hits = list(recover_keys(
            bytes(dump), samples,
            alignment=1, early_exit_after=1))
        self.assertEqual(len(hits), 1)

    def test_load_save_sample_rejects_missing_signature(self
                                                      ) -> None:
        slot = self.tmp / "no_sig"
        slot.mkdir()
        (slot / "loc.sav").write_bytes(b"fileversion=1\nX\n\x00")
        with self.assertRaises(FileNotFoundError):
            load_save_sample(slot)

    def test_hmac_matches_stdlib(self) -> None:
        """Internal _hmac_sha1 helper must produce identical
        output to stdlib hmac.new() — regression guard for
        anyone who 'optimises' the manual implementation."""
        from azurik_mod.save_format.key_recover import _hmac_sha1
        for key_len in (1, 16, 64, 100):
            key = bytes(range(key_len))
            msg = b"the quick brown fox" * 10
            self.assertEqual(
                _hmac_sha1(key, msg),
                hmac.new(key, msg, hashlib.sha1).digest(),
                msg=f"HMAC mismatch for key_len={key_len}")


class KeyCandidateRepr(unittest.TestCase):
    def test_hex_key_round_trips(self):
        c = KeyCandidate(offset=42,
                          key=bytes.fromhex("ab" * 16))
        self.assertEqual(
            c.hex_key(), "ab" * 16)
        self.assertEqual(c.offset, 42)


if __name__ == "__main__":
    unittest.main()
