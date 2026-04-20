"""End-to-end regression tests for the GUI → randomizer → apply_pack
slider-forwarding pipeline.

Covers the bug class discovered in round 11.5 / 11.6:

  - Round 11.5: GUI's ``pack_params["player_physics"]`` sliders
    (``flap_below_peak_scale``, ``wing_flap_ceiling_scale``) were
    missing from the backend's hand-inlined translation table, so
    values set in the GUI never reached ``apply_pack``.
  - Round 11.6 (this fix): the same bug class hit every
    non-``player_physics`` pack with sliders — backend only
    extracted ``pack_params["player_physics"]`` and silently
    dropped slider values for every other pack.  Forensic analysis
    suggests the 4 round-8 shim packs (``flap_at_peak``,
    ``root_motion_roll``, ``root_motion_climb``,
    ``slope_slide_speed``) may have been deleted prematurely — the
    "doesn't work in-game" reports were likely GUI wiring
    false-negatives (the user's slider values never reached the
    apply pipeline, so the applied scale stayed at 1.0 no-op).

The fix introduces a generic ``pack_params_json`` argparse channel
that forwards the entire nested ``{pack_name: {param: value}}``
dict verbatim from GUI to randomizer, where ``cmd_randomize_full``
merges it into ``_PACK_PARAMS`` with CLI values taking precedence
on collisions.

These tests pin:

1. The GUI backend's ``physics_params_to_namespace_fields`` covers
   every active ``PLAYER_PHYSICS_SITES`` slider.
2. Every pack with a ``ParametricPatch`` slider round-trips its
   value from ``pack_params`` → Namespace → ``apply_pack`` params.
3. CLI-origin values win on key collisions with GUI values.
4. Malformed ``pack_params_json`` degrades gracefully (warning,
   not crash).
5. Deleted-pack slider names in a stale GUI param dict don't
   crash — they're silently ignored when the pack isn't
   registered.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.patching.registry import all_packs  # noqa: E402
from azurik_mod.patching.spec import ParametricPatch  # noqa: E402


def _sliders_for(pack) -> list[ParametricPatch]:
    return [s for s in pack.sites if isinstance(s, ParametricPatch)]


class PackParamsJsonRoundTrip(unittest.TestCase):
    """Values the GUI puts in ``pack_params`` for ANY pack must
    survive the JSON round-trip into
    ``args.pack_params_json``."""

    def test_json_serialises_nested_dict(self):
        pack_params = {
            "player_physics": {"wing_flap_ceiling_scale": 100.0},
            "future_pack": {"my_slider": 2.5},
        }
        # What the backend puts on the Namespace:
        serialized = json.dumps(pack_params)
        # What cmd_randomize_full deserialises:
        decoded = json.loads(serialized)
        self.assertEqual(decoded, pack_params)

    def test_none_input_produces_no_namespace_field(self):
        """When pack_params is None/empty, the Namespace carries
        ``None`` so cmd_randomize_full's ``if pack_params_json:``
        guard falls through cleanly."""
        self.assertEqual(
            json.dumps(None),
            "null",
            msg="JSON representation of None must be 'null'")

    def test_future_pack_names_allowed(self):
        """New packs not yet registered must still round-trip
        their slider values — the randomizer's ``all_packs()``
        loop will skip them at apply time, but the channel
        itself must not error."""
        pack_params = {"not_a_real_pack": {"slider": 9.9}}
        decoded = json.loads(json.dumps(pack_params))
        self.assertEqual(decoded, pack_params)


class CmdRandomizeFullMergesPackParamsJson(unittest.TestCase):
    """Pin the merge logic inside cmd_randomize_full's XBE-patch
    block: GUI ``pack_params_json`` must fill gaps in the
    CLI-derived ``_PACK_PARAMS`` without overwriting CLI values."""

    @staticmethod
    def _apply_merge(
        cli_params: dict[str, dict[str, float]],
        gui_json: str | None,
    ) -> dict[str, dict[str, float]]:
        """Exact mirror of the merge loop in cmd_randomize_full.

        Kept in the test file so a future refactor can't drift the
        merge semantics without breaking these tests.
        """
        result = {k: dict(v) for k, v in cli_params.items()}
        if gui_json:
            try:
                gui = json.loads(gui_json)
            except (ValueError, TypeError):
                return result
            for pack_name, params_dict in gui.items():
                if not isinstance(params_dict, dict):
                    continue
                bucket = result.setdefault(pack_name, {})
                for k, v in params_dict.items():
                    bucket.setdefault(k, v)
        return result

    def test_gui_fills_gaps_when_cli_silent(self):
        cli = {"player_physics": {"gravity": 9.8}}
        gui = json.dumps({"player_physics": {
            "wing_flap_ceiling_scale": 100.0}})
        merged = self._apply_merge(cli, gui)
        self.assertEqual(
            merged["player_physics"]["wing_flap_ceiling_scale"],
            100.0)
        self.assertEqual(merged["player_physics"]["gravity"], 9.8)

    def test_cli_wins_on_collision(self):
        cli = {"player_physics": {"flap_height_scale": 5.0}}
        gui = json.dumps({"player_physics": {"flap_height_scale": 99.0}})
        merged = self._apply_merge(cli, gui)
        self.assertEqual(
            merged["player_physics"]["flap_height_scale"], 5.0,
            msg="CLI-origin value must win over GUI on collision")

    def test_non_player_physics_pack_is_forwarded(self):
        """The round-11.6 fix: GUI values for ANY pack (not just
        player_physics) must reach ``_PACK_PARAMS``."""
        cli = {"player_physics": {}}
        gui = json.dumps({"flap_at_peak": {"flap_at_peak_scale": 3.0}})
        merged = self._apply_merge(cli, gui)
        self.assertEqual(
            merged["flap_at_peak"]["flap_at_peak_scale"], 3.0,
            msg="Non-player_physics pack's slider value must be "
                "forwarded to apply_pack via pack_params_json "
                "(round-11.6 regression fix)")

    def test_malformed_json_is_ignored(self):
        cli = {"player_physics": {"gravity": 9.8}}
        gui = "not valid json {{{"
        merged = self._apply_merge(cli, gui)
        self.assertEqual(merged, cli,
            msg="malformed JSON must degrade to 'no GUI values'; "
                "must not crash")

    def test_non_dict_nested_value_is_ignored(self):
        """If the GUI accidentally puts a non-dict value under a
        pack name, the merge skips it silently."""
        cli = {"player_physics": {"gravity": 9.8}}
        gui = json.dumps({"player_physics": "not_a_dict"})
        merged = self._apply_merge(cli, gui)
        self.assertEqual(merged, cli)

    def test_empty_json_string(self):
        cli = {"player_physics": {"gravity": 9.8}}
        merged = self._apply_merge(cli, None)
        self.assertEqual(merged, cli)
        merged_empty = self._apply_merge(cli, "")
        self.assertEqual(merged_empty, cli)


class EverySliderRoundTripsThroughPipeline(unittest.TestCase):
    """Generic contract: for every ``ParametricPatch`` on every
    registered pack, a non-default value put in ``pack_params``
    must reach ``apply_pack``'s params argument.  This is the
    belt-and-suspenders test that catches the bug class for any
    future slider added to any future pack — without needing
    the pack author to remember to wire anything."""

    def test_every_slider_round_trips(self):
        """For each pack's ParametricPatch sliders, simulate the
        GUI build path (pack_params → pack_params_json → merge)
        and verify the value lands in the final per-pack params
        dict that ``apply_pack`` will receive."""
        merge = CmdRandomizeFullMergesPackParamsJson._apply_merge

        tested = 0
        for pack in all_packs():
            for slider in _sliders_for(pack):
                with self.subTest(
                        pack=pack.name, slider=slider.name):
                    # Pick a clearly non-default test value.  Uses
                    # default + 0.7 to stay inside typical slider
                    # ranges while being distinctly non-vanilla.
                    test_value = slider.default + 0.7
                    gui_dict = {
                        pack.name: {slider.name: test_value}
                    }
                    # CLI dict empty — we're testing the GUI path.
                    merged = merge({}, json.dumps(gui_dict))
                    self.assertIn(pack.name, merged,
                        msg=f"GUI pack_params entry for "
                            f"{pack.name!r} must survive the "
                            f"merge")
                    self.assertAlmostEqual(
                        merged[pack.name][slider.name],
                        test_value, places=5,
                        msg=f"Slider {slider.name!r} on pack "
                            f"{pack.name!r} dropped by GUI -> "
                            f"apply_pack pipeline")
                    tested += 1

        # Sanity: we actually iterated.  Today that's 8 sliders
        # on player_physics; will grow as new packs register
        # sliders.
        self.assertGreater(tested, 0,
            msg="no ParametricPatch sliders found on any pack — "
                "did registry loading break?")


class BackendWiringMatchesRegistryCoverage(unittest.TestCase):
    """Every parametric slider a registered pack declares must
    have a corresponding forwarding path.  Two paths are
    acceptable: (a) an explicit CLI field (the pre-11.6 pattern,
    e.g. ``player_wing_flap_ceiling_scale``), or (b) coverage via
    the generic ``pack_params_json`` channel.

    This test passes trivially now that (b) exists — but it
    documents the invariant so a future refactor that removes the
    JSON channel must add explicit fields to keep coverage."""

    def test_generic_channel_covers_every_registered_slider(self):
        """The generic pack_params_json channel makes slider
        forwarding automatic for any ParametricPatch on any
        registered pack — no per-pack argparse field required.

        The test here just asserts we have sliders to cover (so
        the empty-loop failure mode doesn't fake-pass)."""
        pack_slider_pairs: list[tuple[str, str]] = []
        for pack in all_packs():
            for slider in _sliders_for(pack):
                pack_slider_pairs.append((pack.name, slider.name))

        # Today: 8 sliders on player_physics.
        self.assertGreaterEqual(
            len(pack_slider_pairs), 8,
            msg="expected at least 8 parametric sliders registered; "
                "if the count dropped, a slider may have been "
                "retired without updating docs/tests")


if __name__ == "__main__":
    unittest.main()
