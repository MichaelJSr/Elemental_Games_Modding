"""Regression tests for the deep randomizer audit fixes + pins.

Pins the two CRITICAL bugs we fixed + the behavioural contracts
around the KNOWN-NOT-YET-FIXED bugs so future refactors don't
silently undo the fixes or change the warning surface.

Covered:

1. **Power-placement solvability check** (CRITICAL): building the
   mapping with the real entity name (``power_water_a3``) must
   reject clearly-unsolvable placements that would have been
   accepted with the old canonical-name path.

2. **Gem-skip collision detection** (HIGH): when a post-shuffle
   gem base is too long for the field, we now detect identifier
   duplicates and emit a warning line.

3. **`commands.py` uses ``pu["name"]`` not a synthesised canonical**
   (source-level guard): prevents accidental reversion.

4. **Audit doc exists + is linked from the randomizer modules**
   (keeps the roadmap discoverable).

See ``docs/RANDOMIZER_AUDIT.md`` for the narrative + rationale.
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest import mock

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ===========================================================================
# 1. Power-placement solvability check
# ===========================================================================


class PowerPlacementNameMismatch(unittest.TestCase):
    """``check_power_placement`` must use the real in-game entity
    name (``power_water_a3``), not the synthesised canonical
    (``power_water``).  The old buggy code made the check vacuously
    return True for every shuffle involving ``a3`` powers."""

    def setUp(self):
        from azurik_mod.randomizer.solver import Solver
        self.s = Solver()

    def test_canonical_name_produces_empty_placement(self):
        """Baseline: confirm the bug-reproducing input produces an
        empty placement dict.  If this ever stops being True the
        solver internals have changed and the audit needs revisiting."""
        buggy = [("a3", "power_water", "power_fire")]
        placement = self.s.build_placement_from_shuffle(
            power_shuffle=buggy)
        self.assertEqual(
            placement, {},
            msg="Empty placement from canonical name is the bug's "
                "smoking gun — it's what lets the solver vacuously "
                "return True.")

    def test_real_name_produces_non_empty_placement(self):
        """The fixed path: using ``power_water_a3`` (the real entity
        name) produces a non-empty placement dict that the solver
        can actually reason about."""
        real = [("a3", "power_water_a3", "power_fire")]
        placement = self.s.build_placement_from_shuffle(
            power_shuffle=real)
        self.assertIn("a3_core", placement)
        self.assertEqual(
            placement["a3_core"]["power_water_a3"], "power_fire",
            msg="real-name path must actually bind the shuffle to "
                "the logic_db node's vanilla pickup list")

    def test_buggy_path_vacuously_solvable(self):
        """Prove the bug's impact: a placement that canonical-naming
        'proves solvable' is actually unsolvable with real names."""
        buggy = [("a3", "power_water", "power_life")]
        real = [("a3", "power_water_a3", "power_life")]

        ok_buggy, _ = self.s.solve(
            self.s.build_placement_from_shuffle(power_shuffle=buggy))
        ok_real, _ = self.s.solve(
            self.s.build_placement_from_shuffle(power_shuffle=real))

        self.assertTrue(
            ok_buggy,
            msg="buggy path should vacuously pass (that's the bug)")
        self.assertFalse(
            ok_real,
            msg="real-name path correctly rejects the placement "
                "— moving a3's water power to life softlocks")

    def test_commands_source_uses_real_name(self):
        """Source-level guard: ``cmd_randomize``'s power-shuffle
        path must read ``pu["name"]`` to build the solver mapping.
        If someone re-introduces ``f"power_{pu['element']}"`` the
        vacuous-check bug is back."""
        import inspect
        from azurik_mod.randomizer import commands
        src = inspect.getsource(commands.cmd_randomize)
        self.assertIn(
            'orig_real = pu["name"]',
            src,
            msg="cmd_randomize's power-shuffle must build the solver "
                "mapping from pu['name'] (the real entity name), "
                "not f'power_{pu[\"element\"]}'.  See "
                "docs/RANDOMIZER_AUDIT.md § 'Fixed in this round' "
                "for why.")


# ===========================================================================
# 2. Gem-skip collision detection
# ===========================================================================


class GemSkipCollisionWarning(unittest.TestCase):
    """``cmd_randomize`` and ``cmd_randomize_gems`` must WARN when
    the gem-name-length-skip path produces identifier duplicates
    in a single level."""

    def test_commands_source_has_collision_check(self):
        import inspect
        from azurik_mod.randomizer import commands

        for fn in (commands.cmd_randomize, commands.cmd_randomize_gems):
            src = inspect.getsource(fn)
            with self.subTest(fn=fn.__name__):
                self.assertIn(
                    "skipped_slots",
                    src,
                    msg=f"{fn.__name__} must use the two-pass "
                        f"planned_names / skipped_slots pattern "
                        f"that detects post-skip duplicates.")
                # The WARNING message is split across f-string
                # continuations, so check for the distinctive
                # ``dupes`` + ``duplicate`` markers separately.
                self.assertIn(
                    "duplicate", src,
                    msg=f"{fn.__name__} must emit a warning when "
                        f"the gem skip produces duplicate "
                        f"identifiers.  See RANDOMIZER_AUDIT.md.")
                self.assertIn(
                    "dupes", src,
                    msg=f"{fn.__name__} must compute the duplicate "
                        f"map so users can see which identifiers "
                        f"collide.")


# ===========================================================================
# 3. Audit doc exists + cross-linked
# ===========================================================================


class AuditDocDiscoverable(unittest.TestCase):
    """``docs/RANDOMIZER_AUDIT.md`` must exist and be referenced
    from the code + tests that depend on its findings."""

    def test_doc_file_exists(self):
        doc = _REPO_ROOT / "docs" / "RANDOMIZER_AUDIT.md"
        self.assertTrue(
            doc.exists(),
            msg=f"audit doc missing at {doc}.  This doc pins known "
                f"bugs + extension plans — don't delete without "
                f"moving the findings elsewhere.")

    def test_doc_references_key_bugs(self):
        doc_text = (_REPO_ROOT / "docs" / "RANDOMIZER_AUDIT.md").read_text()
        # Key sections / bug IDs the tests below reference.
        for anchor in (
            "power_water_a3",
            "Gem-skip identifier collisions",
            "R1", "R2", "R3", "R4", "R5",   # bug IDs
            "P1", "P2", "P3",                # roadmap items
        ):
            with self.subTest(anchor=anchor):
                self.assertIn(
                    anchor, doc_text,
                    msg=f"audit doc missing expected section/bug "
                        f"id {anchor!r}")

    def test_commands_source_references_audit_doc(self):
        import inspect
        from azurik_mod.randomizer import commands
        src = inspect.getsource(commands)
        self.assertIn(
            "RANDOMIZER_AUDIT.md", src,
            msg="commands.py should reference the audit doc near "
                "the fix sites so future contributors find the "
                "rationale without re-running the audit.")


# ===========================================================================
# 4. Known-bug contract tests (pin current behaviour)
# ===========================================================================


class KnownBugContracts(unittest.TestCase):
    """These tests pin the CURRENT behaviour of known-but-not-yet-
    fixed bugs so a future contributor fixing them knows to update
    the contract.  Think of them as TODO markers with teeth.

    Each test's docstring links back to the audit doc's bug ID.
    Failing these tests indicates someone silently fixed (or
    regressed) the behaviour — either way, review + update the
    audit doc to match.
    """

    def test_R4_inventory_is_set(self):
        """R4 — ``_get_reachable_state`` uses a set for inventory.
        Once fixed to a Counter / multiset, update this test +
        flip the audit doc entry to "fixed"."""
        import inspect
        from azurik_mod.randomizer import solver
        src = inspect.getsource(solver.Solver._get_reachable_state)
        self.assertIn(
            "set(", src,
            msg="R4 pin: _get_reachable_state uses a set today.  "
                "If you've moved to Counter / multiset, update "
                "docs/RANDOMIZER_AUDIT.md § R4 and remove this "
                "contract.")

    def test_R6_levels_always_rewritten(self):
        """R6 — every loaded level is written back at the end of
        the pipeline, dirty or not.  Once dirty tracking lands,
        update the audit doc + this test."""
        import inspect
        from azurik_mod.randomizer import commands
        src = inspect.getsource(commands.cmd_randomize_full)
        # Source contains the loop that writes modified_levels
        # unconditionally.
        self.assertIn(
            "for level_name, data in modified_levels.items()",
            src,
            msg="R6 pin: level-write loop writes every entry "
                "regardless of dirty state.  See audit doc § R6.")


if __name__ == "__main__":
    unittest.main()
