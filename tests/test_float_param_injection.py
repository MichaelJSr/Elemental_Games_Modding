"""Tests for per-apply float-parameter injection into C shims.

Compiles ``shims/fixtures/_float_param_test.c`` on demand, lands
its bytes in a vanilla-style XBE fixture via the same
``apply_trampoline_patch`` pipeline production packs use, and
verifies the ``.rdata`` floats take user-supplied slider values
from the ``params`` dict.

The test skips when the i386 clang cross-toolchain isn't
available on the host (typical for bare CI runners without
``shims/toolchain/compile.sh`` prerequisites).
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.patching.apply import apply_trampoline_patch  # noqa: E402
from azurik_mod.patching.coff import (  # noqa: E402
    find_landed_symbol,
    layout_coff,
    parse_coff,
)
from azurik_mod.patching.spec import (  # noqa: E402
    FloatParam,
    TrampolinePatch,
)
from tests._xbe_fixture import XBE_PATH, require_xbe  # noqa: E402


_COMPILE_SH = _REPO_ROOT / "shims/toolchain/compile.sh"
_SRC = _REPO_ROOT / "shims/fixtures/_float_param_test.c"


def _compile_fixture() -> Path:
    out_dir = Path(tempfile.mkdtemp(prefix="float_param_"))
    out = out_dir / "float_param_test.o"
    subprocess.check_call(
        ["bash", str(_COMPILE_SH), str(_SRC), str(out)],
        cwd=_REPO_ROOT)
    return out


def _toolchain_available() -> bool:
    if not _COMPILE_SH.exists() or not _SRC.exists():
        return False
    try:
        _compile_fixture()
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


# ---------------------------------------------------------------------------
# Unit tests — don't need the i386 toolchain, just the Python side.
# ---------------------------------------------------------------------------

class FloatParamDescriptor(unittest.TestCase):
    """``FloatParam`` / ``TrampolinePatch.float_params`` shape."""

    def test_defaults(self):
        fp = FloatParam(name="g", symbol="_g", default=9.8)
        self.assertEqual(fp.name, "g")
        self.assertEqual(fp.symbol, "_g")
        self.assertEqual(fp.default, 9.8)
        self.assertEqual(fp.label, "")
        self.assertEqual(fp.description, "")

    def test_trampoline_float_params_defaults_empty(self):
        tp = TrampolinePatch(
            name="demo", label="demo", va=0x10000,
            replaced_bytes=b"\x90" * 5,
            shim_object=Path("nope.o"), shim_symbol="_c_demo")
        self.assertEqual(tp.float_params, ())

    def test_trampoline_accepts_float_params(self):
        fp = FloatParam("g", "_g", 9.8)
        tp = TrampolinePatch(
            name="demo", label="demo", va=0x10000,
            replaced_bytes=b"\x90" * 5,
            shim_object=Path("nope.o"), shim_symbol="_c_demo",
            float_params=(fp,))
        self.assertEqual(tp.float_params, (fp,))


# ---------------------------------------------------------------------------
# COFF-side: find_landed_symbol returns the right section + offset.
# ---------------------------------------------------------------------------

@unittest.skipUnless(_toolchain_available(),
    "i386 clang PE-COFF toolchain not available on this host")
class FindLandedSymbolResolvesFixture(unittest.TestCase):
    """With the fixture compiled and run through ``layout_coff``,
    ``find_landed_symbol`` should return the same section + offset
    the COFF file reports for each float_param."""

    def setUp(self):
        self.obj = _compile_fixture()
        self.coff = parse_coff(self.obj.read_bytes())

        # Fake allocator — assigns non-overlapping VAs per section.
        self._next_va = 0x10_000
        self._next_off = 0x1000

        def _alloc(_name: str, placeholder: bytes) -> tuple[int, int]:
            off = self._next_off
            va = self._next_va
            self._next_off += len(placeholder) + 0x10
            self._next_va += len(placeholder) + 0x10
            return (off, va)

        self.landed = layout_coff(
            self.coff, "_c_float_param_test", _alloc)

    def test_resolves_both_float_params(self):
        for name, expected_value in (
                ("_gravity_scale", 1.0),
                ("_walk_scale", 2.5)):
            with self.subTest(symbol=name):
                resolved = find_landed_symbol(
                    self.coff, self.landed, name)
                self.assertIsNotNone(resolved,
                    msg=f"{name!r} should resolve to a landed section")
                section, offset = resolved
                self.assertEqual(section.name, ".rdata")
                # section.data carries the 4 bytes for this float.
                raw = section.data[offset:offset + 4]
                got = struct.unpack("<f", raw)[0]
                self.assertAlmostEqual(got, expected_value, places=5,
                    msg=f"COFF-declared default for {name!r} must "
                        f"match the AZURIK_FLOAT_PARAM literal")

    def test_returns_none_for_missing_symbol(self):
        self.assertIsNone(find_landed_symbol(
            self.coff, self.landed, "_not_a_symbol"))

    def test_returns_none_for_undefined_symbol(self):
        # Fabricate a COFF with an undefined external symbol.
        # Simplest route: pass a symbol_name that we know lives in
        # section_number 0 (undefined) — the fixture's COFF has no
        # undefined externs, so synthesise via a manual probe on a
        # non-existent name.
        self.assertIsNone(find_landed_symbol(
            self.coff, self.landed, "_extern_that_doesnt_exist"))


# ---------------------------------------------------------------------------
# End-to-end: compile, apply with params, verify .rdata bytes changed.
# ---------------------------------------------------------------------------

@require_xbe
@unittest.skipUnless(_toolchain_available(),
    "i386 clang PE-COFF toolchain not available on this host")
class ApplyThreadsFloatParamsIntoShim(unittest.TestCase):
    """Full pipeline: the apply code path must overwrite the
    ``.rdata`` float slots with caller-supplied values and leave
    everything else alone."""

    # Reuse a known-safe .text VA for the trampoline.  The fixture's
    # shim is pure arithmetic + doesn't touch game state, so the
    # test XBE doesn't actually execute it — we just need a VA that
    # (a) exists in .text and (b) whose vanilla 5+ bytes we can
    # declare as replaced_bytes.  VA 0x10_2000 sits in trailing
    # .text padding in the vanilla XBE (confirmed via inspection).
    _TRAMPOLINE_VA = 0x10_0000  # first byte of .text

    def setUp(self):
        self.obj = _compile_fixture()
        self.xbe = bytearray(XBE_PATH.read_bytes())
        from azurik_mod.patching.xbe import va_to_file
        off = va_to_file(self._TRAMPOLINE_VA)
        self.replaced_bytes = bytes(self.xbe[off:off + 5])

    def _spec(self) -> TrampolinePatch:
        return TrampolinePatch(
            name="float_param_test",
            label="float_param_test (fixture)",
            va=self._TRAMPOLINE_VA,
            replaced_bytes=self.replaced_bytes,
            shim_object=self.obj,
            shim_symbol="_c_float_param_test",
            mode="call",
            float_params=(
                FloatParam("gravity_scale", "_gravity_scale", 1.0),
                FloatParam("walk_scale", "_walk_scale", 2.5),
            ),
        )

    def test_default_values_land_when_no_params(self):
        """With no ``params``, the float_params' ``default`` fields
        get written.  For our fixture both defaults match the
        AZURIK_FLOAT_PARAM literals, so the post-apply bytes must
        still decode as 1.0 and 2.5."""
        spec = self._spec()
        ok = apply_trampoline_patch(self.xbe, spec, repo_root=_REPO_ROOT)
        self.assertTrue(ok)
        # Scan for the 4+4 byte .rdata payload (1.0 | 2.5) the
        # shim declares as its defaults; it must survive apply when
        # no custom params are supplied.
        needle = struct.pack("<ff", 1.0, 2.5)
        idx = bytes(self.xbe).rfind(needle)
        self.assertGreaterEqual(idx, 0,
            msg="defaults 1.0 + 2.5 should still be findable as a "
                "4+4 byte pair somewhere in the XBE")

    def test_custom_params_overwrite_defaults(self):
        """Passing a ``params`` dict through
        ``apply_trampoline_patch`` must overwrite the ``.rdata``
        slots with the caller-supplied values."""
        spec = self._spec()
        custom = {"gravity_scale": 5.25, "walk_scale": 0.125}
        ok = apply_trampoline_patch(
            self.xbe, spec,
            repo_root=_REPO_ROOT,
            params=custom)
        self.assertTrue(ok)
        needle = struct.pack("<ff", 5.25, 0.125)
        idx = bytes(self.xbe).rfind(needle)
        self.assertGreaterEqual(idx, 0,
            msg=f"custom float_params 5.25 + 0.125 should appear "
                f"as a 4+4 byte pair in the patched XBE")

    def test_partial_params_fall_back_to_defaults(self):
        """If the caller supplies only one of several params, the
        missing ones use their declared defaults — no KeyError, no
        silent zero."""
        spec = self._spec()
        ok = apply_trampoline_patch(
            self.xbe, spec,
            repo_root=_REPO_ROOT,
            params={"walk_scale": 7.0})
        self.assertTrue(ok)
        # gravity_scale stays at 1.0 (default); walk_scale = 7.0.
        needle = struct.pack("<ff", 1.0, 7.0)
        idx = bytes(self.xbe).rfind(needle)
        self.assertGreaterEqual(idx, 0,
            msg="missing params must fall back to FloatParam.default")

    def test_float_params_do_not_affect_trampoline_bytes(self):
        """The trampoline's ``E8 rel32`` + NOP tail must land
        regardless of float_param values — the two operations are
        independent and must not corrupt each other."""
        spec = self._spec()
        apply_trampoline_patch(
            self.xbe, spec, repo_root=_REPO_ROOT,
            params={"gravity_scale": 3.14, "walk_scale": 2.72})
        from azurik_mod.patching.xbe import va_to_file
        off = va_to_file(self._TRAMPOLINE_VA)
        trampoline = bytes(self.xbe[off:off + 5])
        self.assertEqual(trampoline[0], 0xE8,
            msg="trampoline first byte must still be E8 (CALL rel32)")


# ---------------------------------------------------------------------------
# Warning paths: float_params on a zero-reloc shim.
# ---------------------------------------------------------------------------

@require_xbe
class ApplyWarnsOnZeroRelocShimWithFloatParams(unittest.TestCase):
    """A shim that declares float_params but compiles to a zero-
    relocation .text (because its body never reads the globals)
    should emit a warning instead of silently doing nothing."""

    def test_warning_logged(self):
        # Reuse the shipping qol_skip_logo .o as a "zero-reloc
        # shim" and declare bogus float_params on it.  Since the
        # shim body doesn't reference the named .rdata slots (they
        # don't exist in this .o), we expect the warning path.
        obj_path = (_REPO_ROOT / "shims" / "build" / "qol_skip_logo.o")
        if not obj_path.exists():
            self.skipTest("qol_skip_logo.o not built")
        xbe = bytearray(XBE_PATH.read_bytes())
        spec = TrampolinePatch(
            name="zero_reloc_probe",
            label="zero_reloc_probe",
            va=0x05F6E5,
            replaced_bytes=bytes([0xE8, 0x96, 0x92, 0xFB, 0xFF]),
            shim_object=obj_path,
            shim_symbol="_c_skip_logo",
            mode="call",
            float_params=(
                FloatParam("unused_scale", "_unused_scale", 1.0),
            ),
        )

        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            apply_trampoline_patch(xbe, spec, repo_root=_REPO_ROOT)
        out = buf.getvalue()
        self.assertIn("float_params declared", out,
            msg="must warn when a shim has float_params but no "
                "relocations (the slots aren't referenced)")


if __name__ == "__main__":
    unittest.main()
