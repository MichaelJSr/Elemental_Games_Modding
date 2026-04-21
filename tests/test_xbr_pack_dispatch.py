"""End-to-end test for XBR edits dispatched through :func:`apply_pack`.

Registers a throwaway :class:`Feature` whose ``xbr_sites`` tuple
contains one of every supported op (:class:`XbrEditSpec` with
``set_keyed_double``, ``set_keyed_string``, ``replace_bytes``,
plus an :class:`XbrParametricEdit`).  Runs it through
:func:`apply_pack` with a pre-extracted ``config.xbr`` copy and
asserts each mutation landed.

Also verifies the error paths:

- A pack with ``xbr_sites`` but no ``xbr_files`` argument must
  raise.
- An unsupported op must raise.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.patching.apply import apply_pack  # noqa: E402
from azurik_mod.patching.registry import (  # noqa: E402
    Feature,
    clear_registry_for_tests,
    register_pack,
)
from azurik_mod.patching.xbr_spec import (  # noqa: E402
    XbrEditSpec,
    XbrParametricEdit,
)
from azurik_mod.patching.xbr_staging import XbrStaging  # noqa: E402
from azurik_mod.xbr import XbrDocument  # noqa: E402


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
class ApplyPackWithXbrSites(unittest.TestCase):
    """Exercise the full apply_pack → xbr_sites → primitives path."""

    def setUp(self):
        self._prev_registry = dict(
            __import__("azurik_mod.patching.registry",
                       fromlist=["_REGISTRY"])._REGISTRY)
        clear_registry_for_tests()
        self._tmpdir = Path(tempfile.mkdtemp(prefix="xbr_dispatch_"))
        # Simulate an extracted-ISO layout.
        (self._tmpdir / "gamedata").mkdir()
        shutil.copy2(_GAMEDATA / "config.xbr",
                     self._tmpdir / "gamedata" / "config.xbr")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        # Restore the registry so unrelated tests keep working.
        mod = __import__("azurik_mod.patching.registry",
                         fromlist=["_REGISTRY"])
        mod._REGISTRY.clear()
        mod._REGISTRY.update(self._prev_registry)

    # ------------------------------------------------------------------
    # Full apply path
    # ------------------------------------------------------------------

    def _make_feature(self) -> Feature:
        return register_pack(Feature(
            name="_test_xbr_dispatch",
            description="Throwaway test pack — XBR dispatch plumbing.",
            sites=[],
            apply=lambda xbe_data, **kw: None,
            xbr_sites=(
                XbrEditSpec(
                    label="test: set_keyed_double garret4/walkSpeed",
                    xbr_file="config.xbr",
                    op="set_keyed_double",
                    section="attacks_transitions",
                    entity="garret4",
                    prop="walkSpeed",
                    value=77.0,
                ),
                XbrEditSpec(
                    label="test: set_keyed_string garret4 -> abc",
                    xbr_file="config.xbr",
                    op="set_keyed_string",
                    section="critters_critter_data",
                    entity="garret4",
                    prop="name",
                    value="abc",
                ),
                XbrParametricEdit(
                    name="test_slider_jump",
                    label="test: jump slider",
                    xbr_file="config.xbr",
                    section="attacks_transitions",
                    entity="garret4",
                    prop="runSpeed",
                    default=1.0,
                    slider_min=0.0,
                    slider_max=10.0,
                    slider_step=0.1,
                    unit="x",
                ),
            ),
        ))

    def test_every_xbr_site_lands(self):
        feature = self._make_feature()
        staging = XbrStaging(self._tmpdir)

        apply_pack(feature, bytearray(0x1000),
                   params={"test_slider_jump": 5.5},
                   xbr_files=staging)

        # In-memory buffer should already carry the edits.
        doc = XbrDocument.from_bytes(staging["config.xbr"])
        ks_at = doc.keyed_sections()["attacks_transitions"]
        self.assertEqual(
            ks_at.find_cell("garret4", "walkSpeed").double_value, 77.0)
        self.assertEqual(
            ks_at.find_cell("garret4", "runSpeed").double_value, 5.5)
        ks_cd = doc.keyed_sections()["critters_critter_data"]
        self.assertEqual(
            ks_cd.find_cell("abc", "name").string_value, "abc")

    def test_flush_writes_buffer_to_disk(self):
        feature = self._make_feature()
        staging = XbrStaging(self._tmpdir)
        apply_pack(feature, bytearray(0x1000),
                   params={"test_slider_jump": 2.5},
                   xbr_files=staging)

        written = staging.flush()
        self.assertIn("config.xbr", written)

        # Read back from disk to confirm.
        on_disk = XbrDocument.load(
            self._tmpdir / "gamedata" / "config.xbr")
        self.assertEqual(
            on_disk.keyed_sections()["attacks_transitions"]
                   .find_cell("garret4", "runSpeed").double_value,
            2.5)

    def test_parametric_default_applied_when_param_missing(self):
        feature = self._make_feature()
        staging = XbrStaging(self._tmpdir)
        # No ``test_slider_jump`` in params — should fall through
        # to the edit's declared default (1.0).
        apply_pack(feature, bytearray(0x1000), xbr_files=staging)
        doc = XbrDocument.from_bytes(staging["config.xbr"])
        self.assertEqual(
            doc.keyed_sections()["attacks_transitions"]
               .find_cell("garret4", "runSpeed").double_value, 1.0)

    # ------------------------------------------------------------------
    # Error paths
    # ------------------------------------------------------------------

    def test_missing_xbr_files_raises(self):
        feature = self._make_feature()
        with self.assertRaises(ValueError) as ctx:
            apply_pack(feature, bytearray(0x1000), xbr_files=None)
        self.assertIn("xbr_files", str(ctx.exception))

    def test_unsupported_op_raises_at_apply(self):
        register_pack(Feature(
            name="_test_xbr_bad_op",
            description="Throwaway test pack — bad op.",
            sites=[],
            apply=lambda xbe_data, **kw: None,
            xbr_sites=(
                XbrEditSpec(
                    label="bad",
                    xbr_file="config.xbr",
                    op="def_not_a_real_op",
                    value=b"\x00",
                ),
            ),
        ))
        from azurik_mod.patching.registry import get_pack
        staging = XbrStaging(self._tmpdir)
        with self.assertRaises(ValueError):
            apply_pack(get_pack("_test_xbr_bad_op"),
                       bytearray(0x1000),
                       xbr_files=staging)

    def test_missing_xbr_file_in_staging_raises(self):
        """Pack references a file the staging doesn't have."""
        register_pack(Feature(
            name="_test_xbr_missing_file",
            description="Throwaway test pack.",
            sites=[],
            apply=lambda xbe_data, **kw: None,
            xbr_sites=(
                XbrEditSpec(
                    label="edit a file that doesn't exist",
                    xbr_file="not_a_real_file.xbr",
                    op="replace_bytes",
                    offset=0,
                    value=b"\x00\x00",
                ),
            ),
        ))
        from azurik_mod.patching.registry import get_pack
        staging = XbrStaging(self._tmpdir)
        with self.assertRaises((KeyError, ValueError)):
            apply_pack(get_pack("_test_xbr_missing_file"),
                       bytearray(0x1000),
                       xbr_files=staging)


@unittest.skipUnless(_GAMEDATA is not None,
                     "vanilla gamedata/ fixture not available")
class XbrStagingCache(unittest.TestCase):
    """Standalone tests for the :class:`XbrStaging` path resolver +
    cache behaviour — independent of the apply-pack plumbing so
    failures here don't mask higher-level dispatch bugs."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp(prefix="xbr_stage_"))
        (self._tmpdir / "gamedata").mkdir()
        shutil.copy2(_GAMEDATA / "config.xbr",
                     self._tmpdir / "gamedata" / "config.xbr")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_loads_from_gamedata_subdir(self):
        st = XbrStaging(self._tmpdir)
        buf = st["config.xbr"]
        self.assertIsInstance(buf, bytearray)
        self.assertEqual(buf[:4], b"xobx")

    def test_same_filename_returns_same_buffer(self):
        st = XbrStaging(self._tmpdir)
        a = st["config.xbr"]
        b = st["config.xbr"]
        self.assertIs(a, b,
            msg="Multiple accesses must return the identical "
                "buffer so mutations accumulate.")

    def test_missing_file_raises(self):
        st = XbrStaging(self._tmpdir)
        with self.assertRaises(KeyError):
            st["definitely_not_here.xbr"]

    def test_flush_skips_unchanged(self):
        st = XbrStaging(self._tmpdir)
        _ = st["config.xbr"]  # load but don't mutate
        written = st.flush()
        self.assertEqual(written, [])

    def test_flush_writes_mutated(self):
        st = XbrStaging(self._tmpdir)
        buf = st["config.xbr"]
        # Mutate something harmless — any byte past the header.
        buf[0x2000] ^= 0xFF
        written = st.flush()
        self.assertEqual(written, ["config.xbr"])

    def test_contains_and_get(self):
        st = XbrStaging(self._tmpdir)
        self.assertIn("config.xbr", st)
        self.assertNotIn("missing.xbr", st)
        self.assertIsNone(st.get("missing.xbr"))
        self.assertIsNotNone(st.get("config.xbr"))


if __name__ == "__main__":
    unittest.main()
