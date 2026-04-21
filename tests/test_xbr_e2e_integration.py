"""End-to-end integration of the XBR mod platform.

The individual layers each have focused tests:

- :mod:`tests.test_xbr_document_roundtrip` — document model.
- :mod:`tests.test_xbr_keyed_structural` — edit primitives.
- :mod:`tests.test_xbr_pack_dispatch` — pack dispatcher + staging.
- :mod:`tests.test_xbr_editor_gui` — GUI editor backend.
- :mod:`tests.test_xbr_cli` — CLI verbs.

This module tests the **handshakes between them** — specifically
the path the user actually exercises when they edit a cell in the
GUI:

    XbrEditorBackend.set_keyed_double
        -> pending_mod() emits {"xbr_edits": [...]}
        -> Build page merges into config_edits JSON
        -> run_randomizer ships as --config-mod=<JSON>
        -> randomize_full reads mod["xbr_edits"]
        -> apply_xbr_edit_dicts mutates the extracted config.xbr

If any layer drops the edit, the on-disk file won't show it and
this test fails.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.patching.xbr_spec import (  # noqa: E402
    apply_xbr_edit_dicts,
    xbr_edit_spec_from_dict,
)
from azurik_mod.patching.xbr_staging import XbrStaging  # noqa: E402
from azurik_mod.xbr import XbrDocument  # noqa: E402
from gui.pages.xbr_editor import XbrEditorBackend  # noqa: E402


_GAMEDATA_CANDIDATES = [
    Path("/Users/michaelsrouji/Documents/Xemu/tools/"
         "Azurik - Rise of Perathia (USA).xiso/gamedata"),
    _REPO_ROOT.parent / "Azurik - Rise of Perathia (USA).xiso" / "gamedata",
]


def _find_gamedata() -> Path | None:
    for p in _GAMEDATA_CANDIDATES:
        if p.exists():
            return p
    return None


_GAMEDATA = _find_gamedata()


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class SpecFromDict(unittest.TestCase):
    """:func:`xbr_edit_spec_from_dict` is the JSON → dataclass
    adapter the build pipeline runs on every GUI-sourced edit."""

    def test_round_trips_gui_editor_output(self):
        backend = XbrEditorBackend()
        backend.open(_GAMEDATA / "config.xbr")
        backend.set_keyed_double(3, "garret4", "walkSpeed", 77.0)
        backend.set_keyed_string(4, "garret4", "name", "abc")
        blob = backend.pending_mod()
        specs = [xbr_edit_spec_from_dict(d)
                 for d in blob["xbr_edits"]]
        self.assertEqual(specs[0].op, "set_keyed_double")
        self.assertEqual(specs[0].entity, "garret4")
        self.assertEqual(specs[0].prop, "walkSpeed")
        self.assertEqual(specs[0].value, 77.0)
        self.assertEqual(specs[1].op, "set_keyed_string")
        self.assertEqual(specs[1].value, "abc")

    def test_hex_payload_decodes(self):
        spec = xbr_edit_spec_from_dict({
            "op": "replace_bytes",
            "xbr_file": "config.xbr",
            "offset": 0x100,
            "value": "deadbeef",
            "value_kind": "hex",
        })
        self.assertEqual(spec.value, bytes.fromhex("deadbeef"))

    def test_alias_keys_accepted(self):
        """Some legacy callers use ``file`` instead of ``xbr_file``."""
        spec = xbr_edit_spec_from_dict({
            "op": "set_keyed_double",
            "file": "config.xbr",
            "section": "attacks_transitions",
            "entity": "garret4",
            "prop": "walkSpeed",
            "value": 1.0,
        })
        self.assertEqual(spec.xbr_file, "config.xbr")


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class EndToEndGuiToDisk(unittest.TestCase):
    """The cornerstone integration test.

    Simulates the full GUI-edit-to-build flow using a temp-extracted
    ``gamedata/`` directory + real :class:`XbrStaging`."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp(prefix="xbr_e2e_"))
        (self._tmpdir / "gamedata").mkdir()
        shutil.copy2(_GAMEDATA / "config.xbr",
                     self._tmpdir / "gamedata" / "config.xbr")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_full_flow_lands_on_disk(self):
        # 1. GUI backend opens config.xbr and queues edits.
        gui = XbrEditorBackend()
        gui.open(_GAMEDATA / "config.xbr")
        gui.set_keyed_double(3, "garret4", "walkSpeed", 123.0)
        gui.set_keyed_string(4, "garret4", "name", "abc")

        # 2. pending_mod serialises through JSON (same shape the
        #    Build page merges and run_randomizer ships).
        pending = gui.pending_mod()
        json_payload = json.dumps({"xbr_edits": pending["xbr_edits"]})
        mod = json.loads(json_payload)

        # 3. Dispatch via apply_xbr_edit_dicts against a fresh
        #    XbrStaging pointing at the simulated extract dir.
        staging = XbrStaging(self._tmpdir)
        count = apply_xbr_edit_dicts(staging, mod["xbr_edits"])
        self.assertEqual(count, 2)
        written = staging.flush()
        self.assertIn("config.xbr", written)

        # 4. Read the file back from disk and confirm both edits
        #    landed.
        doc = XbrDocument.load(
            self._tmpdir / "gamedata" / "config.xbr")
        at = doc.keyed_sections()["attacks_transitions"]
        self.assertEqual(
            at.find_cell("garret4", "walkSpeed").double_value, 123.0)
        ccd = doc.keyed_sections()["critters_critter_data"]
        self.assertEqual(
            ccd.find_cell("abc", "name").string_value, "abc")

    def test_multiple_edits_to_same_file_use_one_doc_parse(self):
        """Performance regression: for N edits to the same file,
        the dispatcher should parse the XBR once (document cache)
        not N times.  We can't measure directly without instrumentation,
        but the document cache is attached to the staging instance
        and is observable after dispatch."""
        staging = XbrStaging(self._tmpdir)
        edits = [
            {"op": "set_keyed_double",
             "xbr_file": "config.xbr",
             "section": "attacks_transitions",
             "entity": "garret4",
             "prop": "walkSpeed",
             "value": float(v)}
            for v in (1.0, 2.0, 3.0, 4.0)
        ]
        apply_xbr_edit_dicts(staging, edits)
        cache = getattr(staging, "_xbr_doc_cache", None)
        self.assertIsNotNone(cache,
            msg="Document cache not attached to staging — "
                "every edit will reparse the ~500 KB XBR.")
        self.assertIn("config.xbr", cache)
        # Final value wins (most recent edit).
        doc = cache["config.xbr"]
        at = doc.keyed_sections()["attacks_transitions"]
        self.assertEqual(
            at.find_cell("garret4", "walkSpeed").double_value, 4.0)

    def test_malformed_edit_raises_at_build_time(self):
        """Bad ops must fail fast — silent skipping would let a
        user think their mod landed when it didn't."""
        staging = XbrStaging(self._tmpdir)
        with self.assertRaises(ValueError):
            apply_xbr_edit_dicts(staging, [{
                "op": "totally_not_a_real_op",
                "xbr_file": "config.xbr",
            }])


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class OptimisedApplyPath(unittest.TestCase):
    """The in-place apply path must NOT copy the full buffer for
    each edit — we rely on the document cache for batches.

    Exercised by checking ``staging[filename]`` is the same object
    before and after a dispatch run."""

    def test_buffer_identity_preserved_across_edit(self):
        tmp = Path(tempfile.mkdtemp(prefix="xbr_buf_"))
        try:
            (tmp / "gamedata").mkdir()
            shutil.copy2(_GAMEDATA / "config.xbr",
                         tmp / "gamedata" / "config.xbr")
            staging = XbrStaging(tmp)
            buf_before = staging["config.xbr"]
            apply_xbr_edit_dicts(staging, [{
                "op": "set_keyed_double",
                "xbr_file": "config.xbr",
                "section": "attacks_transitions",
                "entity": "garret4",
                "prop": "walkSpeed",
                "value": 77.0,
            }])
            buf_after = staging["config.xbr"]
            # After the optimisation, the staging's bytearray is
            # replaced with the document's raw bytearray — so the
            # identity changes exactly once.  Both bytearrays carry
            # the edit; the important invariant is that a SECOND
            # edit doesn't further rotate the buffer.
            apply_xbr_edit_dicts(staging, [{
                "op": "set_keyed_double",
                "xbr_file": "config.xbr",
                "section": "attacks_transitions",
                "entity": "garret4",
                "prop": "runSpeed",
                "value": 55.0,
            }])
            buf_final = staging["config.xbr"]
            self.assertIs(buf_final, buf_after,
                msg="Second edit rotated the buffer identity — "
                    "document cache isn't being reused.")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class VariantSectionsSurface(unittest.TestCase):
    """Verify every known variant-record section is reachable via
    :meth:`XbrDocument.variant_sections`, including the ``settings_foo``
    one whose ``section_offset`` doesn't match any TOC entry in
    vanilla config.xbr."""

    @unittest.skipUnless(_GAMEDATA is not None,
                         "vanilla gamedata/ fixture not available")
    def test_all_three_variants_reachable(self):
        doc = XbrDocument.load(_GAMEDATA / "config.xbr")
        v = doc.variant_sections()
        self.assertEqual(
            set(v.keys()),
            {"critters_walking", "damage", "settings_foo"})

    @unittest.skipUnless(_GAMEDATA is not None,
                         "vanilla gamedata/ fixture not available")
    def test_settings_foo_decodes_values(self):
        doc = XbrDocument.load(_GAMEDATA / "config.xbr")
        sf = doc.variant_sections()["settings_foo"]
        # settings_foo has 1 entity, 6 props.  At least the first
        # property decodes to a finite number.
        val = sf.read_value(0, 0)
        self.assertIsNotNone(val)
        import math
        self.assertTrue(math.isfinite(val))

    @unittest.skipUnless(_GAMEDATA is not None,
                         "vanilla gamedata/ fixture not available")
    def test_variant_sections_roundtrip_safe(self):
        """Calling variant_sections() must not mutate the buffer."""
        doc = XbrDocument.load(_GAMEDATA / "config.xbr")
        raw_before = bytes(doc.raw)
        _ = doc.variant_sections()
        self.assertEqual(bytes(doc.raw), raw_before)


if __name__ == "__main__":
    unittest.main()
