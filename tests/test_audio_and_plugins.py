"""Tests for the two remaining Tier 3 tools:

- #14 audio dump — bulk wave-blob extractor
- #16 plugin discovery — third-party pack loading via
  ``importlib.metadata`` entry points

Synthetic-fixture tests always run; vanilla-ISO tests skip
gracefully when the fx.xbr fixture isn't present.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_VANILLA_FX = (_REPO.parent /
               "Azurik - Rise of Perathia (USA).xiso" /
               "gamedata" / "fx.xbr")


# ---------------------------------------------------------------------------
# Audio dump (#14)
# ---------------------------------------------------------------------------


class EntropyHeuristics(unittest.TestCase):
    """Unit-level coverage of the entropy + classification rules."""

    def test_entropy_of_zeros_is_zero(self):
        from azurik_mod.xbe_tools.audio_dump import entropy_ratio
        self.assertEqual(entropy_ratio(b"\x00" * 256), 0.0)

    def test_entropy_of_uniform_is_one(self):
        """Each distinct byte 256 times → maximally random."""
        from azurik_mod.xbe_tools.audio_dump import entropy_ratio
        data = bytes(range(256)) * 4  # 1024 bytes, uniform histogram
        self.assertAlmostEqual(entropy_ratio(data), 1.0, places=6)

    def test_entropy_empty_is_zero(self):
        from azurik_mod.xbe_tools.audio_dump import entropy_ratio
        self.assertEqual(entropy_ratio(b""), 0.0)

    def test_classify_too_small(self):
        from azurik_mod.xbe_tools.audio_dump import classify_entry
        self.assertEqual(classify_entry(32, b"\x00" * 32),
                         "too-small")

    def test_classify_embedded_toc_tag_flags_animation(self):
        """A payload whose first 64 bytes contain a 4-byte TOC
        tag (gshd / node / rdms etc.) is structured metadata."""
        from azurik_mod.xbe_tools.audio_dump import classify_entry
        payload = b"\x08\x00\x00\x00gshd" + b"\x00" * 60
        self.assertEqual(classify_entry(len(payload), payload[:64]),
                         "likely-animation")

    def test_classify_high_entropy_without_header_is_non_audio(self):
        """Post-April-2026 — entropy alone is no longer enough to
        classify something as audio.  The engine's ``FUN_000AC400``
        rejects entries whose codec_id isn't in {0, 1}; high-entropy
        blobs without a valid header are ``non-audio`` payloads the
        game never decodes, not ``likely-audio`` decoder gaps."""
        from azurik_mod.xbe_tools.audio_dump import classify_entry
        payload = bytes(range(256)) * 4  # uniform high entropy
        self.assertEqual(
            classify_entry(len(payload), payload[:64]),
            "non-audio")

    def test_classify_low_entropy_without_tags_is_non_audio(self):
        """Low entropy + no animation TOC tags → still ``non-audio``.
        The old heuristic bucketed these as ``likely-animation`` on
        low entropy alone, but that's an overreach — only the
        explicit 4-byte-tag signature justifies the animation label.
        """
        from azurik_mod.xbe_tools.audio_dump import classify_entry
        payload = b"\x00\x01\x02\x03" * 64  # very low entropy
        self.assertEqual(
            classify_entry(len(payload), payload[:64]),
            "non-audio")


def _make_fake_fx_xbr(*, waves: list[bytes]) -> bytes:
    """Build a minimal XBR blob with the given wave payloads.

    Mirrors the TOC parser's expectations (magic + 16-byte TOC
    entries starting at 0x40).
    """
    toc_rows = bytearray()
    data_area = bytearray()
    data_start = 0x1000    # align wave data past TOC region

    for payload in waves:
        rec_offset = data_start + len(data_area)
        toc_rows.extend(struct.pack("<I", len(payload)))
        toc_rows.extend(b"wave")
        toc_rows.extend(struct.pack("<I", 0))
        toc_rows.extend(struct.pack("<I", rec_offset))
        data_area.extend(payload)

    # Terminator row (all zeros) so parse_toc stops cleanly.
    toc_rows.extend(b"\x00" * 16)

    blob = bytearray()
    blob.extend(b"xobx")
    blob.extend(b"\x00" * (0x40 - len(blob)))
    blob.extend(toc_rows)
    # Pad to the first data offset.
    blob.extend(b"\x00" * (data_start - len(blob)))
    blob.extend(data_area)
    return bytes(blob)


class AudioDumpSynthetic(unittest.TestCase):
    """End-to-end dump on a synthetic fx.xbr."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="azurik-audio-"))
        self.addCleanup(shutil.rmtree, self.tmp)

        # Three waves: one animation-shaped, one high-entropy
        # (audio-shaped), one too-small.
        animation = b"\x08\x00\x00\x00gshd" + b"\x00" * 120
        audio = bytes(range(256)) * 8       # 2 KB of uniform data
        tiny = b"\x00" * 16                 # below 64 B cutoff

        self.fx_path = self.tmp / "synthetic_fx.xbr"
        self.fx_path.write_bytes(_make_fake_fx_xbr(
            waves=[animation, audio, tiny]))
        self.output = self.tmp / "out"

    def test_default_writes_everything(self):
        from azurik_mod.xbe_tools.audio_dump import dump_waves
        report = dump_waves(self.fx_path, self.output)
        self.assertEqual(report.total_waves, 3)
        self.assertEqual(report.written, 3)
        # Post-April-2026 reclassification: the "high-entropy no
        # header" payload is ``non-audio`` (the engine doesn't
        # decode it), not ``likely-audio``.
        self.assertEqual(report.non_audio, 1)
        self.assertEqual(report.likely_animation, 1)
        self.assertEqual(report.too_small, 1)
        self.assertTrue((self.output / "manifest.json").exists())
        files = sorted((self.output / "waves").iterdir())
        self.assertEqual(len(files), 3)

    def test_only_audio_skips_non_audio_and_animation(self):
        """``--only-audio`` now requires a parsed audio header —
        high-entropy non-audio blobs are no longer in the "audio"
        bucket they used to slip into via the entropy heuristic."""
        from azurik_mod.xbe_tools.audio_dump import dump_waves
        report = dump_waves(self.fx_path, self.output,
                            only_audio=True)
        # Our synthetic fixture has zero parseable audio headers,
        # so --only-audio writes nothing.
        self.assertEqual(report.written, 0)
        outs = [e.output_rel for e in report.entries
                if e.output_rel]
        self.assertEqual(len(outs), 0)

    def test_entropy_min_filters(self):
        from azurik_mod.xbe_tools.audio_dump import dump_waves
        # 0.9 is higher than the animation + too-small entries
        # produce, so only the single high-entropy blob writes —
        # regardless of how it's later classified.
        report = dump_waves(self.fx_path, self.output,
                            entropy_min=0.9)
        self.assertEqual(report.written, 1)

    def test_manifest_json_schema(self):
        from azurik_mod.xbe_tools.audio_dump import dump_waves
        dump_waves(self.fx_path, self.output)
        manifest = json.loads(
            (self.output / "manifest.json").read_text())
        for key in ("source", "output_dir", "total_waves",
                    "written", "wav_written", "classification_counts",
                    "entries"):
            self.assertIn(key, manifest)
        for entry in manifest["entries"]:
            for key in ("index", "file_offset", "size",
                        "classification", "entropy",
                        "first_bytes_hex", "output"):
                self.assertIn(key, entry)


class WaveHeaderParser(unittest.TestCase):
    """Audio-header recognition — the 20-byte prefix that 100 of 700
    fx.xbr wave entries carry (pinned from the April 2026 RE pass)."""

    def test_rejects_too_short_payload(self):
        from azurik_mod.xbe_tools.audio_dump import parse_wave_header
        self.assertIsNone(parse_wave_header(b"\x00" * 19))

    def test_rejects_implausible_sample_rate(self):
        """A random u32 prefix (not in the allow-list of standard
        audio rates) is rejected — keeps false positives at zero."""
        from azurik_mod.xbe_tools.audio_dump import parse_wave_header
        # 0xDEADBEEF = 3735928559 — obviously not a sample rate.
        payload = struct.pack("<III", 0xDEADBEEF, 1000, 0x01000401) + b"\x00" * 8
        self.assertIsNone(parse_wave_header(payload))

    def test_decodes_22050_hz_mono_xbox_adpcm(self):
        """The 74-entry bucket in vanilla fx.xbr — sample_rate 22050,
        format_magic 0x01000401 → channels=1, bits=4, codec=1."""
        from azurik_mod.xbe_tools.audio_dump import parse_wave_header
        payload = (struct.pack("<III", 22050, 22016, 0x01000401)
                   + b"\x00" * 8)
        h = parse_wave_header(payload)
        self.assertIsNotNone(h)
        self.assertEqual(h.sample_rate, 22050)
        self.assertEqual(h.sample_count, 22016)
        self.assertEqual(h.channels, 1)
        self.assertEqual(h.bits_per_sample, 4)
        self.assertEqual(h.codec_id, 1)
        # 22016 samples at 22050 Hz ≈ 998 ms.
        self.assertAlmostEqual(h.duration_ms, 998, delta=2)

    def test_decodes_44100_hz(self):
        from azurik_mod.xbe_tools.audio_dump import parse_wave_header
        payload = (struct.pack("<III", 44100, 88200, 0x01000401)
                   + b"\x00" * 8)
        h = parse_wave_header(payload)
        self.assertIsNotNone(h)
        self.assertEqual(h.sample_rate, 44100)
        self.assertEqual(h.duration_ms, 2000)

    def test_rejects_insane_sample_count(self):
        """sample_count > 10 min of audio means we mis-parsed noise
        bytes — bail out instead of emitting garbage durations."""
        from azurik_mod.xbe_tools.audio_dump import parse_wave_header
        # 22050 * 601 = 13253450 is 10-minutes-plus of audio
        payload = (struct.pack("<III", 22050, 22050 * 601, 0x01000401)
                   + b"\x00" * 8)
        self.assertIsNone(parse_wave_header(payload))

    def test_format_magic_byte_decomposition(self):
        """The 0x01000401 dword splits into channels/bits/codec as
        described in the module docstring — unit-test that pinning
        so a future refactor can't silently flip the byte order."""
        from azurik_mod.xbe_tools.audio_dump import parse_wave_header
        payload = (struct.pack("<III", 22050, 22016, 0x01000401)
                   + b"\x00" * 8)
        h = parse_wave_header(payload)
        # 0x01000401 little-endian bytes: 01 04 00 01
        #   byte[0] = 01 → channels = 1
        #   byte[1] = 04 → bits_per_sample = 4
        #   byte[3] = 01 → codec_id = 1
        self.assertEqual(h.channels, 0x01000401 & 0xFF)
        self.assertEqual(h.bits_per_sample,
                         (0x01000401 >> 8) & 0xFF)
        self.assertEqual(h.codec_id, (0x01000401 >> 24) & 0xFF)


class AudioDumpWithHeader(unittest.TestCase):
    """End-to-end dump on a synthetic fx.xbr that includes an
    xbox-adpcm entry — verify header decoding + WAV wrapping."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="azurik-audio-hdr-"))
        self.addCleanup(shutil.rmtree, self.tmp)

        # One xbox-adpcm entry: 20-byte header + 180 bytes of payload
        # (5 ADPCM blocks × 36 bytes).  Sample_rate 22050, sample_count
        # 320 (= 5 blocks × 64 samples/block) → ~14.5 ms duration.
        header = struct.pack("<III", 22050, 320, 0x01000401)
        header += b"\x00" * 8       # 8 reserved bytes
        payload = header + b"\xaa" * 180
        self.fx_path = self.tmp / "synthetic_fx.xbr"
        self.fx_path.write_bytes(_make_fake_fx_xbr(waves=[payload]))
        self.output = self.tmp / "out"

    def test_classification_is_xbox_adpcm(self):
        from azurik_mod.xbe_tools.audio_dump import dump_waves
        report = dump_waves(self.fx_path, self.output)
        self.assertEqual(report.total_waves, 1)
        self.assertEqual(report.xbox_adpcm, 1)
        self.assertEqual(report.wav_written, 1)

    def test_wav_file_has_riff_header(self):
        from azurik_mod.xbe_tools.audio_dump import dump_waves
        dump_waves(self.fx_path, self.output)
        wav = (self.output / "waves" / "wave_0000.wav").read_bytes()
        self.assertEqual(wav[:4], b"RIFF")
        self.assertEqual(wav[8:12], b"WAVE")
        self.assertIn(b"fmt ", wav[:64])
        self.assertIn(b"data", wav)

    def test_manifest_has_decoded_header(self):
        from azurik_mod.xbe_tools.audio_dump import dump_waves
        dump_waves(self.fx_path, self.output)
        manifest = json.loads(
            (self.output / "manifest.json").read_text())
        entry = manifest["entries"][0]
        self.assertIn("header", entry)
        self.assertIn("wav_output", entry)
        self.assertEqual(entry["header"]["sample_rate"], 22050)
        self.assertEqual(entry["header"]["channels"], 1)
        self.assertEqual(entry["header"]["bits_per_sample"], 4)

    def test_no_wav_flag_suppresses_wrapping(self):
        from azurik_mod.xbe_tools.audio_dump import dump_waves
        report = dump_waves(self.fx_path, self.output,
                            emit_wav=False)
        self.assertEqual(report.wav_written, 0)
        self.assertFalse(
            (self.output / "waves" / "wave_0000.wav").exists())


class RawPreviewWav(unittest.TestCase):
    """The raw-PCM preview wrapper for non-audio entries — blobs
    whose header fails the engine's acceptance check.  It's a
    diagnostic helper for inspecting binary structure in Audacity;
    NOT intended playback (the engine doesn't decode these either)."""

    def test_wraps_payload_as_riff_16bit_mono(self):
        from azurik_mod.xbe_tools.audio_dump import build_raw_preview_wav
        payload = bytes(range(256)) * 4  # 1 KiB of uniform data
        wav = build_raw_preview_wav(payload, sample_rate=22050)
        self.assertEqual(wav[:4], b"RIFF")
        self.assertEqual(wav[8:12], b"WAVE")
        # Format-chunk at offset 12..
        self.assertEqual(wav[12:16], b"fmt ")
        fmt_tag, channels, rate, _br, block_align, bits = struct.unpack_from(
            "<HHIIHH", wav, 20)
        self.assertEqual(fmt_tag, 0x0001)   # WAVE_FORMAT_PCM
        self.assertEqual(channels, 1)
        self.assertEqual(rate, 22050)
        self.assertEqual(bits, 16)
        # Data chunk follows the fmt block.
        self.assertIn(b"data", wav)

    def test_sample_rate_override(self):
        from azurik_mod.xbe_tools.audio_dump import build_raw_preview_wav
        wav = build_raw_preview_wav(b"\x00" * 128, sample_rate=44100)
        _tag, _ch, rate, *_ = struct.unpack_from("<HHIIHH", wav, 20)
        self.assertEqual(rate, 44100)

    def test_odd_length_payload_padded(self):
        """RIFF data chunks must be an even byte count for 16-bit
        samples — an odd-length payload gets a single zero-byte pad."""
        from azurik_mod.xbe_tools.audio_dump import build_raw_preview_wav
        wav = build_raw_preview_wav(b"\x01" * 7)
        # data-chunk payload should be 8 bytes (7 + 1 pad)
        data_idx = wav.rfind(b"data")
        data_size = struct.unpack_from("<I", wav, data_idx + 4)[0]
        self.assertEqual(data_size, 8)


class DuplicateDetection(unittest.TestCase):
    """Duplicate-of detection surfaces wave entries that share
    first-32-bytes + size — same SFX referenced by multiple
    ``fx/sound/...`` names in the vanilla ISO."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="azurik-dup-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        # Build three entries: two identical, one distinct.  All
        # fall into the ``non-audio`` bucket (high entropy, no
        # parseable engine header) — same path the real-world
        # 448-entry cluster takes.
        common = bytes(range(256)) * 2        # 512 B, uniform histogram
        distinct = bytes(reversed(range(256))) * 2
        self.fx_path = self.tmp / "synthetic_fx.xbr"
        self.fx_path.write_bytes(_make_fake_fx_xbr(
            waves=[common, common, distinct]))
        self.output = self.tmp / "out"

    def test_duplicate_pointed_at_earliest_index(self):
        from azurik_mod.xbe_tools.audio_dump import dump_waves
        report = dump_waves(self.fx_path, self.output)
        self.assertEqual(report.duplicates_detected, 1)
        # Entry 0 is the canonical, entry 1 duplicates entry 0,
        # entry 2 is distinct.
        self.assertEqual(report.entries[0].duplicate_of, -1)
        self.assertEqual(report.entries[1].duplicate_of, 0)
        self.assertEqual(report.entries[2].duplicate_of, -1)

    def test_duplicate_flagged_in_manifest_json(self):
        from azurik_mod.xbe_tools.audio_dump import dump_waves
        dump_waves(self.fx_path, self.output)
        manifest = json.loads(
            (self.output / "manifest.json").read_text())
        self.assertEqual(manifest["entries"][1]["duplicate_of"], 0)
        # Canonical entry doesn't carry the field.
        self.assertNotIn("duplicate_of", manifest["entries"][0])

    def test_raw_previews_skip_duplicates(self):
        """Running with --raw-previews should NOT emit the preview
        for an entry whose bytes are identical to an earlier one —
        Audacity would just show the same waveform twice."""
        from azurik_mod.xbe_tools.audio_dump import dump_waves
        report = dump_waves(self.fx_path, self.output,
                            emit_raw_previews=True)
        self.assertEqual(report.preview_wav_written, 2,
            msg="canonical entries 0 + 2 get previews; duplicate 1 "
                "should be skipped to avoid redundant output")
        self.assertFalse(
            (self.output / "waves" / "wave_0001.preview.wav").exists())
        self.assertTrue(
            (self.output / "waves" / "wave_0000.preview.wav").exists())
        self.assertTrue(
            (self.output / "waves" / "wave_0002.preview.wav").exists())

    def test_missing_file_raises(self):
        from azurik_mod.xbe_tools.audio_dump import dump_waves
        with self.assertRaises(FileNotFoundError):
            dump_waves("/tmp/does/not/exist.xbr", self.output)


@unittest.skipUnless(_VANILLA_FX.exists(),
                     "vanilla fx.xbr fixture required")
class AudioDumpVanilla(unittest.TestCase):
    """Pins the vanilla-fx.xbr classification distribution.

    Guards against regressions in the TOC parser or the
    heuristic classifier.
    """

    @classmethod
    def setUpClass(cls) -> None:
        from azurik_mod.xbe_tools.audio_dump import dump_waves
        cls.tmp = Path(tempfile.mkdtemp(prefix="azurik-fx-"))
        cls.addClassCleanup(lambda: shutil.rmtree(cls.tmp))
        cls.report = dump_waves(
            _VANILLA_FX, cls.tmp / "out",
            only_audio=False,
            entropy_min=0.0)

    def test_total_waves_matches_audit(self):
        """fx.xbr ships with 700 wave TOC entries — pinned from
        the April 2026 RE pass."""
        self.assertEqual(self.report.total_waves, 700)

    def test_xbox_adpcm_count_pinned(self):
        """vanilla fx.xbr ships exactly 103 entries whose header
        passes the engine's acceptance check — 102 mono + 1 stereo,
        all Xbox ADPCM.  If this count drifts the engine-header
        parser regressed or fx.xbr changed."""
        self.assertEqual(self.report.xbox_adpcm, 103)

    def test_non_audio_bucket_matches_engine_reality(self):
        """Most wave TOC entries don't decode as audio because
        their header fails ``FUN_000AC400``'s check — they're
        non-audio payloads stored under the ``wave`` fourcc.
        Expected ~550+ in vanilla; anything <500 would mean the
        classifier started mistaking non-audio for something else.
        """
        self.assertGreater(self.report.non_audio, 500)
        # And xbox-adpcm + non-audio + likely-animation +
        # too-small + pcm-raw must account for every entry.
        total = (self.report.xbox_adpcm + self.report.pcm_raw
                 + self.report.non_audio + self.report.likely_animation
                 + self.report.too_small)
        self.assertEqual(total, self.report.total_waves)


# ---------------------------------------------------------------------------
# Plugin discovery (#16)
# ---------------------------------------------------------------------------


class PluginDiscoveryWithoutEntryPoints(unittest.TestCase):

    def test_empty_when_no_plugins_installed(self):
        """No third-party plugin is installed in the test env, so
        the discovery result is empty."""
        from azurik_mod.plugins import (
            discover_plugins, load_plugins)
        self.assertEqual(discover_plugins(), [])
        report = load_plugins()
        self.assertEqual(report.discovered, [])
        self.assertEqual(report.loaded, [])
        self.assertEqual(report.errors, [])

    def test_format_report_empty(self):
        from azurik_mod.plugins import (
            PluginLoadReport, format_report)
        text = format_report(PluginLoadReport())
        self.assertIn("No third-party plugins discovered", text)
        self.assertIn("azurik_mod.patches", text)


class PluginDiscoveryMockedEntryPoints(unittest.TestCase):
    """Simulate ``importlib.metadata`` returning a fake entry
    point + exercise the loader's happy + failure paths."""

    def setUp(self):
        """Invalidate the plugin discovery cache so the mocked
        ``_iter_entry_points`` actually runs.

        ``load_plugins`` got a site-packages-fingerprint cache to
        short-circuit the ~500 ms ``importlib.metadata`` walk when
        the user has no plugins installed.  Tests that feed in
        synthetic entry points must skip that cache, otherwise the
        loader returns before the mock is consulted.
        """
        from azurik_mod.plugins import _cache_path
        cache_file = _cache_path()
        if cache_file.exists():
            try:
                cache_file.unlink()
            except OSError:
                pass
        # Restore the cache after the test so other tests don't
        # re-pay the ~500 ms walk either.
        self.addCleanup(
            lambda: cache_file.unlink(missing_ok=True))

    def _entry_point(self, *, name: str, value: str,
                     dist_name: str = "", dist_ver: str = ""):
        """Build a duck-typed EntryPoint replacement."""
        class _Dist:
            name = dist_name
            version = dist_ver
        class _EP:
            pass
        ep = _EP()
        ep.name = name
        ep.value = value
        ep.dist = _Dist() if dist_name else None
        return ep

    def test_load_success(self):
        # Create a tiny tmp package with a registered feature, add
        # it to sys.path, and point an entry point at it.
        pkg_dir = Path(tempfile.mkdtemp(prefix="azurik-plugtest-"))
        self.addCleanup(shutil.rmtree, pkg_dir)
        (pkg_dir / "fake_plugin.py").write_text(
            "import azurik_mod.patches  # ensure registry loaded\n"
            "from azurik_mod.patching.registry import "
            "Feature, register_feature\n"
            "register_feature(Feature(\n"
            "    name='__fake_plugin_test__',\n"
            "    description='synthetic plugin', sites=[],\n"
            "    apply=lambda xbe, **kw: None,\n"
            "    category='experimental',\n"
            "))\n")
        sys.path.insert(0, str(pkg_dir))
        self.addCleanup(lambda: sys.path.remove(str(pkg_dir)))

        ep = self._entry_point(
            name="__fake_plugin_test__",
            value="fake_plugin",
            dist_name="fake-plugin", dist_ver="0.1.0")
        with mock.patch(
                "azurik_mod.plugins._iter_entry_points",
                return_value=[ep]):
            from azurik_mod.plugins import load_plugins
            report = load_plugins()

        self.assertEqual(len(report.loaded), 1)
        self.assertEqual(report.loaded[0].name,
                         "__fake_plugin_test__")
        self.assertFalse(report.errors)

        # Verify the feature actually registered.
        from azurik_mod.patching.registry import get_pack
        pack = get_pack("__fake_plugin_test__")
        self.assertEqual(pack.category, "experimental")

        # Clean up the test feature from the registry so we don't
        # poison later tests.
        from azurik_mod.patching.registry import _REGISTRY
        _REGISTRY.pop("__fake_plugin_test__", None)

    def test_load_failure_is_isolated(self):
        """A plugin that raises at import time is caught + logged;
        one bad plugin never takes down the loader."""
        ep = self._entry_point(
            name="broken_plugin", value="nonexistent_module_xxx",
            dist_name="broken", dist_ver="0.0.0")
        with mock.patch(
                "azurik_mod.plugins._iter_entry_points",
                return_value=[ep]):
            from azurik_mod.plugins import load_plugins
            report = load_plugins(raise_on_error=False)
        self.assertEqual(report.loaded, [])
        self.assertEqual(len(report.errors), 1)
        err_plugin, err_text = report.errors[0]
        self.assertEqual(err_plugin.name, "broken_plugin")
        self.assertIn("No module named", err_text)

    def test_raise_on_error_re_raises(self):
        ep = self._entry_point(
            name="broken_plugin",
            value="nonexistent_module_xxx")
        with mock.patch(
                "azurik_mod.plugins._iter_entry_points",
                return_value=[ep]):
            from azurik_mod.plugins import load_plugins
            with self.assertRaises(ModuleNotFoundError):
                load_plugins(raise_on_error=True)

    def test_discover_does_not_import(self):
        """``discover_plugins`` must NOT call ``import_module``."""
        ep = self._entry_point(
            name="would_break_on_import",
            value="nonexistent_module_xxx")
        with mock.patch(
                "azurik_mod.plugins._iter_entry_points",
                return_value=[ep]):
            with mock.patch(
                    "importlib.import_module") as import_mock:
                from azurik_mod.plugins import discover_plugins
                plugins = discover_plugins()
        self.assertEqual(len(plugins), 1)
        self.assertEqual(import_mock.call_count, 0,
            msg="discover_plugins must not trigger imports")


class AzurikNoPluginsEnvVar(unittest.TestCase):
    """``AZURIK_NO_PLUGINS=1`` turns plugin auto-loading off."""

    def test_env_var_blocks_autoload(self):
        """The side-effect import in ``azurik_mod.patches``
        respects the env var.  Test by running a subprocess with
        the var set and confirming no plugin-related chatter
        appears in the output."""
        import subprocess as sp
        env = dict(os.environ)
        env["AZURIK_NO_PLUGINS"] = "1"
        # Just run `plugins list` — would still show 'no plugins
        # discovered' which is safe.
        out = sp.run(
            [sys.executable, "-m", "azurik_mod",
             "plugins", "list"],
            capture_output=True, text=True, cwd=str(_REPO),
            env=env)
        self.assertEqual(out.returncode, 0)
        self.assertIn("No third-party plugins", out.stdout)


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


class CliSmokeAudioPlugins(unittest.TestCase):

    def _run(self, *args: str, env: dict | None = None
             ) -> tuple[int, str, str]:
        proc_env = dict(os.environ)
        if env:
            proc_env.update(env)
        out = subprocess.run(
            [sys.executable, "-m", "azurik_mod", *args],
            capture_output=True, text=True, cwd=str(_REPO),
            env=proc_env)
        return out.returncode, out.stdout, out.stderr

    def test_audio_help(self):
        rc, stdout, _ = self._run("audio", "dump", "--help")
        self.assertEqual(rc, 0)
        self.assertIn("--only-audio", stdout)
        self.assertIn("--entropy-min", stdout)

    def test_plugins_help(self):
        rc, stdout, _ = self._run("plugins", "list", "--help")
        self.assertEqual(rc, 0)
        self.assertIn("--reload", stdout)

    def test_plugins_list_empty_environment(self):
        rc, stdout, _ = self._run("plugins", "list")
        self.assertEqual(rc, 0)
        self.assertIn("azurik_mod.patches", stdout)

    @unittest.skipUnless(_VANILLA_FX.exists(),
                         "vanilla fx.xbr required")
    def test_audio_dump_cli_writes_manifest(self):
        with tempfile.TemporaryDirectory(
                prefix="azurik-audio-cli-") as tmp:
            rc, stdout, _ = self._run(
                "audio", "dump", str(_VANILLA_FX),
                "--output", tmp, "--only-audio")
            self.assertEqual(rc, 0)
            self.assertIn("non-audio:", stdout)
            self.assertTrue((Path(tmp) / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
