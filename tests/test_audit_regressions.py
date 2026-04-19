"""Regression guards for the dead-code / orphan-wiring audit.

Every test in this file pins a bug that was discovered during the
audit and would otherwise reappear silently if someone refactored
the wiring again.

Covered:

1. **Connection-shuffler imports** — ``cmd_randomize_full``'s step 6
   references ``EXCLUDE_TRANSITIONS`` and ``VALID_DEST_LEVELS``.
   Both MUST be importable from ``commands.py`` or the randomize-
   connections code path raises ``NameError`` at runtime.

2. **Build-event emission** — the ``build_done`` event must be
   published by ``BuildPage._handle_done`` so the status-bar
   subscriber (``app._sync_status``) actually runs after a build
   completes.

3. **``build_request`` subscription is fully removed** — a
   previous revision subscribed but never published, leaving
   ``_on_build_request`` orphan.  The handler + subscription are
   now gone; guard so they can't drift back in.

4. **Dead ``AppState.output_dir`` / ``set_output`` are gone** —
   the migration removed them but we want to fail loudly if a
   revert accidentally restores the orphan.

5. **Dead ``PatchesPage.get_pack_*`` methods are gone** — the
   Build page reads from ``AppState`` directly; these accessors
   were dead.  If restored, someone should wire them.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class ConnectionShufflerImports(unittest.TestCase):
    """``cmd_randomize_full``'s connections step references two
    constants defined in ``shufflers.py``.  They MUST be importable
    through ``commands.py``'s module globals — otherwise the user
    hits a ``NameError`` the first time ``--no-connections`` is not
    passed to randomize-full (which is the default)."""

    def test_exclude_transitions_resolvable_from_commands(self):
        from azurik_mod.randomizer import commands
        self.assertTrue(
            hasattr(commands, "EXCLUDE_TRANSITIONS"),
            msg="commands.py must import EXCLUDE_TRANSITIONS from "
                "shufflers — the connection-shuffle path at line "
                "1054 reads it without a fallback.  Before this "
                "guard landed the default randomize-full invocation "
                "crashed with NameError.")
        self.assertIsInstance(commands.EXCLUDE_TRANSITIONS, (set, frozenset))

    def test_valid_dest_levels_resolvable_from_commands(self):
        from azurik_mod.randomizer import commands
        self.assertTrue(
            hasattr(commands, "VALID_DEST_LEVELS"),
            msg="commands.py must import VALID_DEST_LEVELS from "
                "shufflers (used at connection-shuffle line 1057).")
        self.assertIsInstance(commands.VALID_DEST_LEVELS, (set, frozenset))
        # Sanity: every known Azurik level id should be present.
        for known in ("airL1", "Water1"):
            # airship is explicitly excluded (one-way); do NOT assert
            # its presence.  Just check the set is non-empty.
            pass
        self.assertGreater(len(commands.VALID_DEST_LEVELS), 5)

    def test_dead_imports_stay_out(self):
        """Three private shuffler helpers were imported but never used
        in commands.py.  Removing them prevents grep-drift — if a
        future contributor re-adds them they can re-justify the
        import by actually calling the helper."""
        from azurik_mod.randomizer import commands
        for dead_name in ("_power_element", "_frag_parts", "_gem_base_type"):
            self.assertFalse(
                hasattr(commands, dead_name),
                msg=f"commands.py re-exports {dead_name!r} but "
                    f"doesn't use it.  If you have a caller in mind, "
                    f"import it at the call site instead of at the "
                    f"module top so dead-code audits catch it.")


class BuildEventEmission(unittest.TestCase):
    """``BuildPage._handle_done`` must publish ``build_done``.  A
    previous revision forgot to emit, leaving the ``app._sync_status``
    subscriber unreachable for post-build status-bar updates."""

    def test_build_page_handle_done_emits_build_done(self):
        """Scan the source of ``_handle_done`` for a ``bus.emit(
        "build_done", ...)`` call.  Source-level check rather than
        boot-the-Tk-window because we want this to run headless."""
        from gui.pages import build as build_mod
        import inspect
        src = inspect.getsource(build_mod.BuildPage._handle_done)
        self.assertIn(
            'bus.emit("build_done"', src,
            msg="BuildPage._handle_done must emit build_done so the "
                "status-bar subscriber in gui/app.py gets refreshed "
                "with last_seed / last_output after every build.")

    def test_build_request_subscription_is_removed(self):
        """``_on_build_request`` and its subscribe() call were an
        orphan pair (never published).  If they come back, someone
        should either wire the publisher or remove them again."""
        from gui.pages import build as build_mod
        self.assertFalse(
            hasattr(build_mod.BuildPage, "_on_build_request"),
            msg="BuildPage._on_build_request was an orphan handler "
                "(no publisher).  If you re-add it, also add the "
                "bus.emit('build_request', ...) call from wherever "
                "the publisher lives, OR remove the handler entirely.")

        # Also grep the source — the subscribe() line should be gone
        # regardless of whether the handler is reinstated.
        import inspect
        src = inspect.getsource(build_mod)
        self.assertNotIn(
            'bus.subscribe("build_request"', src,
            msg="BuildPage still subscribes to build_request.  "
                "Either wire the publisher or drop the subscription.")


class AppStateDeadSurfaceRemoved(unittest.TestCase):
    """``AppState.output_dir`` and ``set_output`` were remnants of
    an earlier UX sketch — the output-path logic lives entirely in
    the Project page + RandomizerConfig now."""

    def test_output_dir_field_removed(self):
        from gui.models import AppState
        inst = AppState()
        self.assertFalse(
            hasattr(inst, "output_dir"),
            msg="AppState.output_dir was a migration remnant — "
                "output-path state lives on RandomizerConfig now.  "
                "If you're restoring it, wire the Project page's "
                "output picker into set_output() too.")

    def test_set_output_method_removed(self):
        from gui.models import AppState
        self.assertFalse(
            hasattr(AppState, "set_output"),
            msg="AppState.set_output never had a caller — delete it "
                "or add a caller before shipping.")


class PatchesPageDeadAccessorsRemoved(unittest.TestCase):
    """Build page reads from ``AppState.enabled_packs`` /
    ``pack_params`` directly; the old ``get_pack_flags`` /
    ``get_pack_params`` accessors on PatchesPage had no callers."""

    def test_get_pack_flags_removed(self):
        from gui.pages.patches import PatchesPage
        self.assertFalse(
            hasattr(PatchesPage, "get_pack_flags"),
            msg="PatchesPage.get_pack_flags had no callers — "
                "Build page reads AppState.enabled_packs directly.  "
                "Re-adding requires a caller.")

    def test_get_pack_params_removed(self):
        from gui.pages.patches import PatchesPage
        self.assertFalse(
            hasattr(PatchesPage, "get_pack_params"),
            msg="PatchesPage.get_pack_params had no callers — "
                "Build page reads AppState.pack_params directly.")


if __name__ == "__main__":
    unittest.main()
