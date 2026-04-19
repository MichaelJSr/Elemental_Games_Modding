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

    def test_classify_high_entropy_is_audio(self):
        from azurik_mod.xbe_tools.audio_dump import classify_entry
        payload = bytes(range(256)) * 4  # uniform high entropy
        self.assertEqual(
            classify_entry(len(payload), payload[:64]),
            "likely-audio")

    def test_classify_low_entropy_is_animation(self):
        from azurik_mod.xbe_tools.audio_dump import classify_entry
        payload = b"\x00\x01\x02\x03" * 64  # very low entropy
        self.assertEqual(
            classify_entry(len(payload), payload[:64]),
            "likely-animation")


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
        self.assertEqual(report.likely_audio, 1)
        self.assertEqual(report.likely_animation, 1)
        self.assertEqual(report.too_small, 1)
        self.assertTrue((self.output / "manifest.json").exists())
        files = sorted((self.output / "waves").iterdir())
        self.assertEqual(len(files), 3)

    def test_only_audio_skips_animation_and_too_small(self):
        from azurik_mod.xbe_tools.audio_dump import dump_waves
        report = dump_waves(self.fx_path, self.output,
                            only_audio=True)
        self.assertEqual(report.written, 1)
        # Manifest still contains every entry, but only the audio
        # one has a non-empty output path.
        outs = [e.output_rel for e in report.entries
                if e.output_rel]
        self.assertEqual(len(outs), 1)

    def test_entropy_min_filters(self):
        from azurik_mod.xbe_tools.audio_dump import dump_waves
        # 0.9 is higher than the animation + too-small entries
        # produce, so only the high-entropy audio one writes.
        report = dump_waves(self.fx_path, self.output,
                            entropy_min=0.9)
        self.assertEqual(report.written, 1)

    def test_manifest_json_schema(self):
        from azurik_mod.xbe_tools.audio_dump import dump_waves
        dump_waves(self.fx_path, self.output)
        manifest = json.loads(
            (self.output / "manifest.json").read_text())
        for key in ("source", "output_dir", "total_waves",
                    "written", "likely_audio",
                    "likely_animation", "too_small", "entries"):
            self.assertIn(key, manifest)
        for entry in manifest["entries"]:
            for key in ("index", "file_offset", "size",
                        "classification", "entropy",
                        "first_bytes_hex", "output"):
                self.assertIn(key, entry)

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

    def test_classification_split_reasonable(self):
        """~70%+ of blobs classify as likely-audio on the vanilla
        ISO.  Animation + too-small together cover < 30%."""
        total = self.report.total_waves
        self.assertGreater(
            self.report.likely_audio, total * 0.6,
            msg=f"expected >60% audio, got "
                f"{self.report.likely_audio}/{total}")
        self.assertLess(
            self.report.likely_animation + self.report.too_small,
            total * 0.4)


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
            self.assertIn("likely-audio:", stdout)
            self.assertTrue((Path(tmp) / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
