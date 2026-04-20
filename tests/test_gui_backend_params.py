"""Regression tests for the GUI ``backend`` module's
``pack_params`` → argparse Namespace translation.

Round 11.5 discovered a silent wiring break: the GUI exposed
``flap_below_peak_scale`` and ``wing_flap_ceiling_scale`` sliders
on the player_physics pack, but the backend's
``build_randomized_iso`` function only translated 7 of the 8
active sliders into the argparse Namespace.  The user would drag
the sliders, the build log would show the non-default values in
``# pack_params: {...}``, but the values never reached
``cmd_randomize_full`` — the randomizer's
``getattr(args, 'player_wing_flap_ceiling_scale', None) or 1.0``
short-circuited to the vanilla path, so the XBE got built without
any ceiling shim.

The fix: extract the translation into
:func:`physics_params_to_namespace_fields` driven by
:data:`_PHYSICS_PARAM_TO_NAMESPACE`, and pin every entry here so
the next slider addition is caught automatically.

The tests don't need the XBE fixture — they only exercise the
Python translation surface.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.patches.player_physics import (  # noqa: E402
    PLAYER_PHYSICS_SITES,
)
from gui.backend import (  # noqa: E402
    _PHYSICS_PARAM_TO_NAMESPACE,
    physics_params_to_namespace_fields,
)


class CoverageAgainstPlayerPhysicsSites(unittest.TestCase):
    """Every active slider in PLAYER_PHYSICS_SITES must appear in
    the translation table.  This test fails loudly when a new
    slider lands on player_physics but nobody remembered to wire
    it through the GUI backend — the exact failure mode that hid
    the wing_flap_ceiling_scale regression."""

    def test_every_active_slider_is_translated(self):
        slider_names = {p.name for p in PLAYER_PHYSICS_SITES}
        translated = {pname for pname, _, _ in _PHYSICS_PARAM_TO_NAMESPACE}
        missing = slider_names - translated
        self.assertFalse(missing,
            msg=f"PLAYER_PHYSICS_SITES has slider(s) {sorted(missing)} "
                f"that _PHYSICS_PARAM_TO_NAMESPACE doesn't translate — "
                f"their GUI values won't reach cmd_randomize_full.")

    def test_translation_table_entries_all_present_or_retired(self):
        """The inverse: flag dead entries that reference sliders
        no longer on PLAYER_PHYSICS_SITES (so we don't grow stale
        config).  Retired-but-still-accepted sliders are
        explicitly whitelisted — these stay in the translation so
        old pack_params dicts serialised before retirement still
        apply a sensible value instead of being silently dropped.
        """
        # Sliders retired from PLAYER_PHYSICS_SITES but kept in the
        # translation for back-compat with pre-retirement param
        # dicts.  If you retire a slider, add its name here.
        RETIRED_BUT_ACCEPTED = {
            # Retired round 10 (see docs/LEARNINGS.md).  Still
            # consumed by apply_player_speed's roll kwarg.
            "roll_speed_scale",
        }
        slider_names = {p.name for p in PLAYER_PHYSICS_SITES}
        for pname, _, _ in _PHYSICS_PARAM_TO_NAMESPACE:
            if pname in RETIRED_BUT_ACCEPTED:
                continue
            self.assertIn(pname, slider_names,
                msg=f"_PHYSICS_PARAM_TO_NAMESPACE references "
                    f"{pname!r} but it's not on PLAYER_PHYSICS_SITES. "
                    f"If retired, add to RETIRED_BUT_ACCEPTED.")

    def test_every_table_entry_has_unique_namespace_field(self):
        """argparse.Namespace can't hold duplicate field names."""
        fields = [nf for _, nf, _ in _PHYSICS_PARAM_TO_NAMESPACE]
        self.assertEqual(len(fields), len(set(fields)),
            msg="duplicate namespace fields in translation table")


class TranslateEmptyInput(unittest.TestCase):
    """An empty / None ``physics`` dict must produce a fully-None
    fields dict — preserves byte-identity of the built XBE when
    nobody's touched any slider."""

    def test_none_physics_input(self):
        fields = physics_params_to_namespace_fields(None)
        self.assertTrue(
            all(v is None for v in fields.values()),
            msg=f"None input must produce all-None fields: {fields}")

    def test_empty_physics_input(self):
        fields = physics_params_to_namespace_fields({})
        self.assertTrue(all(v is None for v in fields.values()))

    def test_fields_dict_has_every_namespace_key(self):
        """Even with no input, every namespace field must be
        present (mapped to None) so the Namespace construction
        downstream doesn't have missing attrs."""
        fields = physics_params_to_namespace_fields({})
        expected = {nf for _, nf, _ in _PHYSICS_PARAM_TO_NAMESPACE}
        self.assertEqual(set(fields.keys()), expected)


class TranslateVanillaValuesToNone(unittest.TestCase):
    """Sliders at their vanilla default must translate to ``None``
    so ``cmd_randomize_full``'s ``None or 1.0`` short-circuit
    keeps the XBE byte-identical."""

    def test_vanilla_gravity_is_none(self):
        fields = physics_params_to_namespace_fields({"gravity": 9.8})
        self.assertIsNone(fields["gravity"])

    def test_vanilla_scales_are_none(self):
        """Every ``*_scale`` field defaults to 1.0 — setting it
        exactly to 1.0 must round-trip to None."""
        physics = {
            pname: 1.0 for pname, _, vanilla in _PHYSICS_PARAM_TO_NAMESPACE
            if pname != "gravity"}
        fields = physics_params_to_namespace_fields(physics)
        for pname, nf, vanilla in _PHYSICS_PARAM_TO_NAMESPACE:
            if pname == "gravity":
                continue
            self.assertIsNone(fields[nf],
                msg=f"{pname}=1.0 must translate to None (got "
                    f"{fields[nf]!r})")


class TranslateNonDefaultValues(unittest.TestCase):
    """Non-default slider values must be forwarded as floats into
    the argparse Namespace.  This is the round-11.5 regression
    fix — pre-fix, ``wing_flap_ceiling_scale`` never reached
    cmd_randomize_full."""

    def test_wing_flap_ceiling_regression(self):
        """The exact scenario from the reported bug log.  A
        ``wing_flap_ceiling_scale`` slider value of 100.0 must
        reach ``player_wing_flap_ceiling_scale`` = 100.0, NOT
        ``None``."""
        fields = physics_params_to_namespace_fields(
            {"wing_flap_ceiling_scale": 100.0})
        self.assertEqual(fields["player_wing_flap_ceiling_scale"], 100.0)

    def test_flap_below_peak_regression(self):
        """Same bug class as wing_flap_ceiling — both sliders were
        missing from the pre-fix translation."""
        fields = physics_params_to_namespace_fields(
            {"flap_below_peak_scale": 100.0})
        self.assertEqual(
            fields["player_flap_below_peak_scale"], 100.0)

    def test_full_bug_report_scenario(self):
        """The complete pack_params dict from the reported log:
        flap_height_scale=2, flap_below_peak_scale=100,
        wing_flap_ceiling_scale=100; everything else at vanilla.
        All three non-default values must land in the Namespace."""
        physics = {
            "gravity": 9.8,
            "walk_speed_scale": 1.0,
            "swim_speed_scale": 1.0,
            "jump_speed_scale": 1.0,
            "air_control_scale": 1.0,
            "flap_height_scale": 2.0,
            "flap_below_peak_scale": 100.0,
            "wing_flap_ceiling_scale": 100.0,
        }
        fields = physics_params_to_namespace_fields(physics)
        self.assertEqual(fields["player_flap_scale"], 2.0)
        self.assertEqual(fields["player_flap_below_peak_scale"], 100.0)
        self.assertEqual(fields["player_wing_flap_ceiling_scale"], 100.0)
        # Sanity: every other slider still None (vanilla).
        for field in (
                "gravity",
                "player_walk_scale",
                "player_roll_scale",
                "player_swim_scale",
                "player_jump_scale",
                "player_air_control_scale"):
            self.assertIsNone(fields[field])


class TranslateLegacyAliases(unittest.TestCase):
    """Pre-April-2026 param dicts used legacy aliases.  The
    translation must still accept them for back-compat."""

    def test_run_speed_scale_aliases_roll_speed_scale(self):
        fields = physics_params_to_namespace_fields(
            {"run_speed_scale": 2.0})
        self.assertEqual(fields["player_roll_scale"], 2.0)

    def test_run_scale_aliases_roll_speed_scale(self):
        fields = physics_params_to_namespace_fields(
            {"run_scale": 2.0})
        self.assertEqual(fields["player_roll_scale"], 2.0)

    def test_flap_subsequent_scale_aliases_flap_below_peak_scale(self):
        fields = physics_params_to_namespace_fields(
            {"flap_subsequent_scale": 5.0})
        self.assertEqual(fields["player_flap_below_peak_scale"], 5.0)

    def test_canonical_key_wins_over_alias(self):
        """If both are set, the canonical name takes precedence."""
        fields = physics_params_to_namespace_fields({
            "roll_speed_scale": 3.0,
            "run_speed_scale": 99.0,
        })
        self.assertEqual(fields["player_roll_scale"], 3.0)


if __name__ == "__main__":
    unittest.main()
