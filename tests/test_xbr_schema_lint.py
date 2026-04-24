"""Regression tests for the registration-time XBR schema lint.

Every shipped pack's ``xbr_sites`` must land on a cell that
``azurik_mod/config/schema.json`` knows about — the schema is our
single source of truth for "valid writable config cells", and any
site that isn't there is either dead data, a typo, or a brand-new
cell the schema hasn't caught up to.

The lint wires into :func:`register_feature`.  Escape hatch:
``Feature(unchecked_xbr_sites=True)`` for packs whose authors
deliberately target undocumented cells.
"""

from __future__ import annotations

import sys
import unittest
import warnings
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class ShippedPacksPassSchemaLint(unittest.TestCase):
    """Re-import the patch registry inside a warning catcher and
    assert zero undocumented-cell warnings fire."""

    def test_no_schema_lint_warnings_for_any_shipped_pack(self):
        from azurik_mod.patching import registry
        registry._WARNED_UNDOC_TRIPLES.clear()
        registry._SCHEMA_CELL_INDEX = None
        with warnings.catch_warnings(record=True) as ws:
            warnings.simplefilter("always")
            for pack in registry.all_packs():
                if pack.xbr_sites and not pack.unchecked_xbr_sites:
                    registry._lint_xbr_sites_against_schema(pack)
        schema_warnings = [
            str(w.message) for w in ws
            if issubclass(w.category, UserWarning)
            and "xbr_site target" in str(w.message)]
        self.assertEqual(
            schema_warnings, [],
            msg=f"One or more shipped packs target undocumented "
                f"cells:\n  " + "\n  ".join(schema_warnings)
                + f"\nEither add the cells to "
                f"azurik_mod/config/schema.json or mark the pack "
                f"Feature(unchecked_xbr_sites=True).")


class SchemaLintFiresOnUndocumentedTarget(unittest.TestCase):
    """The lint must actually warn when a pack points at a cell
    that doesn't exist in the schema — otherwise it's worse than
    nothing (lulls us into thinking the schema is complete)."""

    def test_undocumented_cell_triggers_single_warning(self):
        from azurik_mod.patching import registry
        from azurik_mod.patching.xbr_spec import XbrParametricEdit
        import uuid

        pack_name = f"_schema_lint_probe_{uuid.uuid4().hex[:8]}"
        site = XbrParametricEdit(
            name="probe_slider",
            label="probe slider",
            xbr_file="config.xbr",
            section="definitely_not_a_real_section",
            entity="whatever",
            prop="definitely_not_a_real_prop",
            default=0.0,
            slider_min=0.0, slider_max=1.0, slider_step=1.0,
            unit="",
            description="intentional undocumented probe",
        )
        pack = registry.Feature(
            name=pack_name,
            description="schema-lint probe",
            sites=[],
            apply=lambda *_, **__: None,
            xbr_sites=(site,),
            default_on=False,
            category="other",
            tags=("test",),
        )

        try:
            with warnings.catch_warnings(record=True) as ws:
                warnings.simplefilter("always")
                registry.register_feature(pack)
            hits = [str(w.message) for w in ws
                    if "xbr_site target" in str(w.message)
                    and pack_name in str(w.message)]
            self.assertEqual(
                len(hits), 1,
                msg=f"expected exactly one schema-lint warning "
                    f"for undocumented probe pack, got "
                    f"{len(hits)}: {hits!r}")
        finally:
            registry._REGISTRY.pop(pack_name, None)
            registry._WARNED_UNDOC_TRIPLES.discard(
                (pack_name, "definitely_not_a_real_section",
                 "definitely_not_a_real_prop"))

    def test_unchecked_xbr_sites_suppresses_warning(self):
        from azurik_mod.patching import registry
        from azurik_mod.patching.xbr_spec import XbrParametricEdit
        import uuid

        pack_name = f"_schema_lint_probe_{uuid.uuid4().hex[:8]}"
        site = XbrParametricEdit(
            name="probe_slider",
            label="probe slider",
            xbr_file="config.xbr",
            section="definitely_not_a_real_section",
            entity="whatever",
            prop="definitely_not_a_real_prop",
            default=0.0,
            slider_min=0.0, slider_max=1.0, slider_step=1.0,
            unit="",
            description="probe",
        )
        pack = registry.Feature(
            name=pack_name,
            description="schema-lint probe (suppressed)",
            sites=[],
            apply=lambda *_, **__: None,
            xbr_sites=(site,),
            unchecked_xbr_sites=True,
            default_on=False,
            category="other",
            tags=("test",),
        )

        try:
            with warnings.catch_warnings(record=True) as ws:
                warnings.simplefilter("always")
                registry.register_feature(pack)
            hits = [w for w in ws
                    if "xbr_site target" in str(w.message)
                    and pack_name in str(w.message)]
            self.assertEqual(
                hits, [],
                msg="unchecked_xbr_sites=True must suppress the "
                    "schema lint entirely.")
        finally:
            registry._REGISTRY.pop(pack_name, None)


class CliCrossCheckFlag(unittest.TestCase):
    """Smoke-test the ``azurik-mod xbr verify --cross-check-schema``
    runtime mirror of the registration-time lint."""

    _CONFIG_CANDIDATES = [
        _REPO_ROOT / ".xbr_workspace" / "game" / "gamedata" / "config.xbr",
        Path("/Users/michaelsrouji/Documents/Xemu/tools/"
             "Azurik - Rise of Perathia (USA).xiso/gamedata/config.xbr"),
    ]

    def _find_config(self) -> Path | None:
        for candidate in self._CONFIG_CANDIDATES:
            if candidate.exists():
                return candidate
        return None

    def test_cross_check_flag_is_advertised_in_help(self):
        import subprocess
        r = subprocess.run(
            [sys.executable, "-m", "azurik_mod",
             "xbr", "verify", "--help"],
            capture_output=True, text=True, cwd=_REPO_ROOT,
            timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--cross-check-schema", r.stdout)

    def test_cross_check_runs_clean_on_shipping_config(self):
        """With the shipped schema + shipped packs, the runtime
        cross-check should report OK — no undocumented targets."""
        config = self._find_config()
        if config is None:
            self.skipTest("config.xbr fixture not available")
        import subprocess
        env = {"AZURIK_NO_PLUGINS": "1",
               "PATH": Path("/usr/bin").as_posix()}
        r = subprocess.run(
            [sys.executable, "-m", "azurik_mod",
             "xbr", "verify", str(config), "--cross-check-schema"],
            capture_output=True, text=True, cwd=_REPO_ROOT,
            timeout=60, env={**__import__("os").environ, **env})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("cross-check: OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
