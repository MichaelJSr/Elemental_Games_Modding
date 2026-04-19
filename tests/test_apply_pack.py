"""Tests for the unified :func:`apply_pack` dispatcher.

``apply_pack`` is the single entry point every caller (CLI, GUI,
tests) should use to apply a feature — it hides whether the pack
is backed by byte patches, parametric sliders, a trampoline shim,
or a ``custom_apply`` callback.  These tests pin the dispatch rules:

- Pure ``PatchSpec`` pack → generic dispatcher calls
  :func:`apply_patch_spec` for each site.
- Pack with a ``ParametricPatch`` site → value from ``params`` dict
  (or the site's ``default`` if absent) drives the rewrite.
- Pack with a ``TrampolinePatch`` → dispatcher invokes
  :func:`apply_trampoline_patch` with the declared ``repo_root``.
- ``custom_apply`` short-circuits the generic walk.
- ``AZURIK_NO_SHIMS=1`` + ``legacy_sites`` → every ``TrampolinePatch``
  is swapped for the pack's legacy byte-patch fallback.
- Virtual parametric sites (``va == 0``) are skipped unless the pack
  has a ``custom_apply`` to consume them.
"""

from __future__ import annotations

import os
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.patching import (  # noqa: E402
    ParametricPatch,
    PatchSpec,
    TrampolinePatch,
    apply_pack,
    apply_patch_spec,
    verify_patch_spec,
)
from azurik_mod.patching.registry import (  # noqa: E402
    Feature,
    PatchPack,
)


# A tiny synthetic XBE-like buffer.  Real apply flows touch file
# offsets (not VAs) so we construct a byte buffer large enough for
# the file offsets used in each test.  The VA ↔ offset conversion
# for Azurik is hardcoded in `azurik_mod.patching.xbe.XBE_SECTIONS`;
# we pick VAs that fall cleanly inside the `.text` section.

# Site VAs inside `.text` that we know map to concrete file offsets
# without triggering header arithmetic.
_SITE_VA = 0x00015000        # well inside .text
_SITE_OFFSET = _SITE_VA - 0x11000 + 0x1000  # va_to_file of VA 0x15000


def _new_buffer(size: int = 0x10_0000) -> bytearray:
    """Fresh bytearray big enough for the synthetic apply sites."""
    return bytearray(size)


class DispatchRoutes(unittest.TestCase):
    """Confirms each site type hits the right primitive."""

    def test_patch_spec_route(self):
        """A pure PatchSpec pack lands bytes via apply_patch_spec."""
        spec = PatchSpec(
            label="test bytes",
            va=_SITE_VA,
            original=bytes([0x00, 0x00, 0x00]),
            patch=bytes([0xAA, 0xBB, 0xCC]),
        )
        pack = Feature(
            name="__test_patchspec",
            description="byte-only",
            sites=[spec],
            apply=lambda _xbe: None,
            tags=(),
        )
        buf = _new_buffer()
        apply_pack(pack, buf)
        self.assertEqual(
            bytes(buf[_SITE_OFFSET:_SITE_OFFSET + 3]),
            bytes([0xAA, 0xBB, 0xCC]),
            msg="apply_pack must route PatchSpec sites through "
                "apply_patch_spec so the vanilla-bytes check runs")

    def test_parametric_route_uses_params(self):
        """A ParametricPatch pulls its value from the params dict."""
        site = ParametricPatch(
            name="my_slider",
            label="test slider",
            va=_SITE_VA,
            size=4,
            original=struct.pack("<f", 1.0),
            default=1.0,
            slider_min=0.0,
            slider_max=10.0,
            slider_step=0.01,
            unit="x",
            encode=lambda v: struct.pack("<f", float(v)),
            decode=lambda b: struct.unpack("<f", b)[0],
        )
        pack = Feature(
            name="__test_parametric",
            description="slider-only",
            sites=[site],
            apply=lambda _xbe: None,
            tags=(),
        )
        buf = _new_buffer()
        apply_pack(pack, buf, params={"my_slider": 7.5})
        written = struct.unpack(
            "<f", bytes(buf[_SITE_OFFSET:_SITE_OFFSET + 4]))[0]
        self.assertAlmostEqual(written, 7.5, places=5,
            msg="parametric dispatch must use params[site.name] to "
                "drive the encoded value")

    def test_parametric_default_when_param_missing(self):
        """Missing param names fall through to site.default."""
        site = ParametricPatch(
            name="absent_slider",
            label="default test",
            va=_SITE_VA,
            size=4,
            original=struct.pack("<f", 1.0),
            default=3.14,
            slider_min=0.0,
            slider_max=10.0,
            slider_step=0.01,
            unit="x",
            encode=lambda v: struct.pack("<f", float(v)),
            decode=lambda b: struct.unpack("<f", b)[0],
        )
        pack = Feature(
            name="__test_parametric_default",
            description="default fallback",
            sites=[site],
            apply=lambda _xbe: None,
            tags=(),
        )
        buf = _new_buffer()
        apply_pack(pack, buf, params={})
        written = struct.unpack(
            "<f", bytes(buf[_SITE_OFFSET:_SITE_OFFSET + 4]))[0]
        self.assertAlmostEqual(written, 3.14, places=5,
            msg="missing params must fall back to site.default")

    def test_virtual_parametric_is_skipped(self):
        """Virtual sites (va=0, size=0) have no concrete site to
        rewrite; they're expected to be consumed by a custom_apply
        hook and must be silently skipped by the generic dispatcher."""
        site = ParametricPatch(
            name="virtual_slider",
            label="virtual",
            va=0,
            size=0,
            original=b"",
            default=1.0,
            slider_min=0.1,
            slider_max=10.0,
            slider_step=0.05,
            unit="x",
            encode=lambda v: b"",
            decode=lambda b: 1.0,
        )
        pack = Feature(
            name="__test_virtual",
            description="virtual only",
            sites=[site],
            apply=lambda _xbe: None,
            tags=(),
        )
        buf = _new_buffer()
        # No exception + no bytes touched.
        apply_pack(pack, buf, params={"virtual_slider": 2.0})
        self.assertEqual(bytes(buf[:64]), bytes(64))


class CustomApplyShortCircuits(unittest.TestCase):
    """If a pack declares ``custom_apply``, it fully owns the apply
    flow — the generic site loop is not run."""

    def test_custom_apply_called_with_params_as_kwargs(self):
        received: dict = {}

        def custom(xbe_data, **params):
            received["xbe_len"] = len(xbe_data)
            received["params"] = dict(params)

        pack = Feature(
            name="__test_custom_apply",
            description="custom",
            sites=[PatchSpec(                       # this must NOT run
                label="unreachable",
                va=_SITE_VA,
                original=bytes([0x00]),
                patch=bytes([0xFF]),
            )],
            apply=lambda _xbe: None,
            tags=(),
            custom_apply=custom,
        )
        buf = _new_buffer()
        apply_pack(pack, buf, params={"alpha": 1, "beta": "hello"})
        self.assertEqual(received["params"], {"alpha": 1, "beta": "hello"})
        # The PatchSpec site was NOT executed — buffer still vanilla.
        self.assertEqual(buf[_SITE_OFFSET], 0x00,
            msg="custom_apply must short-circuit the generic site "
                "loop — the unreachable PatchSpec should NOT land")


class LegacyFallbackEnvVar(unittest.TestCase):
    """``AZURIK_NO_SHIMS=1`` swaps every TrampolinePatch for the
    pack's declared legacy_sites (if any).  Non-shim sites are
    unaffected."""

    def _tramp_feature(self) -> Feature:
        # Put the trampoline and its legacy fallback at the same VA
        # so one test buffer can observe either path.  replaced_bytes
        # is what the trampoline expects to overwrite — also the
        # legacy spec's `original`, so they describe the same sites.
        original = bytes([0x00, 0x00, 0x00, 0x00, 0x00])
        tramp = TrampolinePatch(
            name="__test_trampoline",
            label="trampoline",
            va=_SITE_VA,
            replaced_bytes=original,
            shim_object=Path("/tmp/__this_will_never_resolve__.o"),
            shim_symbol="_c_never",
            mode="call",
        )
        legacy = PatchSpec(
            label="legacy fallback",
            va=_SITE_VA,
            original=original,
            patch=bytes([0xDE, 0xAD, 0xBE, 0xEF, 0xCA]),
        )
        return Feature(
            name="__test_legacy_fallback",
            description="tramp + legacy",
            sites=[tramp],
            apply=lambda _xbe: None,
            tags=(),
            legacy_sites=(legacy,),
        )

    def test_no_shims_env_swaps_in_legacy_sites(self):
        pack = self._tramp_feature()
        buf = _new_buffer()
        with mock.patch.dict(os.environ, {"AZURIK_NO_SHIMS": "1"}):
            apply_pack(pack, buf)
        self.assertEqual(
            bytes(buf[_SITE_OFFSET:_SITE_OFFSET + 5]),
            bytes([0xDE, 0xAD, 0xBE, 0xEF, 0xCA]),
            msg="With AZURIK_NO_SHIMS=1 the legacy PatchSpec must "
                "run in place of the TrampolinePatch")

    def test_without_env_var_trampoline_path_is_attempted(self):
        """Without the env var, the dispatcher tries the trampoline
        path.  The shim_object we gave it doesn't exist, so the
        underlying primitive logs a warning and returns False — but
        the dispatcher must NOT fall back to legacy in that case
        (that would hide a real toolchain bug)."""
        pack = self._tramp_feature()
        buf = _new_buffer()
        # Ensure the env var is not set during this test.
        env = dict(os.environ)
        env.pop("AZURIK_NO_SHIMS", None)
        with mock.patch.dict(os.environ, env, clear=True):
            # The trampoline apply will fail-without-raising because
            # the shim .o doesn't exist.  The buffer stays vanilla.
            apply_pack(pack, buf)
        self.assertEqual(
            bytes(buf[_SITE_OFFSET:_SITE_OFFSET + 5]),
            bytes([0x00, 0x00, 0x00, 0x00, 0x00]),
            msg="Without AZURIK_NO_SHIMS, a failing trampoline apply "
                "must NOT silently fall through to legacy_sites — "
                "the failure should surface for the user to fix")


class TypeValidation(unittest.TestCase):
    """apply_pack is strict about its arguments."""

    def test_non_patchpack_raises(self):
        with self.assertRaises(TypeError):
            apply_pack("not a pack", _new_buffer())

    def test_unknown_site_type_raises(self):
        class Oddball:
            pass

        pack = Feature(
            name="__test_unknown_site",
            description="oddball",
            sites=[Oddball()],
            apply=lambda _xbe: None,
            tags=(),
        )
        with self.assertRaises(TypeError):
            apply_pack(pack, _new_buffer())


if __name__ == "__main__":
    unittest.main()
