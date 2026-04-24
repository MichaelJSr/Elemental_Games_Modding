"""Regression tests for the ``cheat_entity_hp -> player_max_hp``
legacy pack-name migration path.

Two channels:

1. Runtime alias in
   :func:`azurik_mod.patching.registry.get_pack` — ``--enable-pack
   cheat_entity_hp`` keeps resolving, with one
   :class:`DeprecationWarning` per process.
2. On-disk prefs migration in
   :func:`gui.models.migrate_legacy_pack_keys` — any future
   ``ui.json`` that grows ``enabled_packs`` / ``pack_params``
   channels inherits the rename automatically.

Both paths are kept in sync by the ``_LEGACY_PACK_ALIASES`` /
``_LEGACY_PACK_RENAMES`` tables; this test pins the behaviour so
one side can't drift without the other.
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


class GetPackAlias(unittest.TestCase):
    def test_legacy_name_resolves_with_deprecation_warning(self):
        import azurik_mod.patches  # noqa: F401
        from azurik_mod.patching import registry
        registry._WARNED_LEGACY_ALIASES.discard("cheat_entity_hp")
        with warnings.catch_warnings(record=True) as ws:
            warnings.simplefilter("always")
            pack = registry.get_pack("cheat_entity_hp")
        self.assertEqual(pack.name, "player_max_hp")
        depr = [w for w in ws
                if issubclass(w.category, DeprecationWarning)]
        self.assertEqual(
            len(depr), 1,
            msg=f"expected exactly one DeprecationWarning on the "
                f"first legacy lookup, got {len(depr)}: "
                f"{[str(w.message) for w in ws]!r}")

    def test_legacy_name_warns_only_once_per_process(self):
        import azurik_mod.patches  # noqa: F401
        from azurik_mod.patching import registry
        registry._WARNED_LEGACY_ALIASES.discard("cheat_entity_hp")
        with warnings.catch_warnings(record=True) as ws1:
            warnings.simplefilter("always")
            registry.get_pack("cheat_entity_hp")
            registry.get_pack("cheat_entity_hp")
            registry.get_pack("cheat_entity_hp")
        depr = [w for w in ws1
                if issubclass(w.category, DeprecationWarning)]
        self.assertEqual(len(depr), 1)

    def test_non_legacy_name_is_unchanged(self):
        import azurik_mod.patches  # noqa: F401
        from azurik_mod.patching import registry
        pack = registry.get_pack("player_max_hp")
        self.assertEqual(pack.name, "player_max_hp")


class MigrateLegacyPackKeys(unittest.TestCase):
    def test_enabled_packs_channel_migrates(self):
        from gui.models import migrate_legacy_pack_keys
        prefs = {"enabled_packs": {"cheat_entity_hp": True,
                                   "player_physics": False}}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            changed = migrate_legacy_pack_keys(prefs)
        self.assertTrue(changed)
        self.assertNotIn("cheat_entity_hp", prefs["enabled_packs"])
        self.assertTrue(prefs["enabled_packs"]["player_max_hp"])
        self.assertFalse(prefs["enabled_packs"]["player_physics"])

    def test_pack_params_channel_migrates(self):
        from gui.models import migrate_legacy_pack_keys
        prefs = {"pack_params": {
            "cheat_entity_hp": {"garret4_hit_points": 500.0}}}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            migrate_legacy_pack_keys(prefs)
        self.assertIn("player_max_hp", prefs["pack_params"])
        self.assertEqual(
            prefs["pack_params"]["player_max_hp"]
                 ["garret4_hit_points"],
            500.0)

    def test_noop_when_no_legacy_keys(self):
        from gui.models import migrate_legacy_pack_keys
        prefs = {"theme": "dark",
                 "enabled_packs": {"player_max_hp": True}}
        with warnings.catch_warnings(record=True) as ws:
            warnings.simplefilter("always")
            changed = migrate_legacy_pack_keys(prefs)
        self.assertFalse(changed)
        self.assertEqual(
            [w for w in ws
             if issubclass(w.category, UserWarning)
             and "Migrated legacy pack" in str(w.message)],
            [])

    def test_emits_single_user_warning_on_hit(self):
        from gui.models import migrate_legacy_pack_keys
        prefs = {"enabled_packs": {"cheat_entity_hp": True}}
        with warnings.catch_warnings(record=True) as ws:
            warnings.simplefilter("always")
            migrate_legacy_pack_keys(prefs)
        hits = [w for w in ws
                if issubclass(w.category, UserWarning)
                and "Migrated legacy pack" in str(w.message)]
        self.assertEqual(len(hits), 1)

    def test_does_not_clobber_existing_new_key(self):
        """If a prefs dict somehow contains BOTH the old and the
        new key, the new key wins (user's most recent choice)."""
        from gui.models import migrate_legacy_pack_keys
        prefs = {"enabled_packs": {"cheat_entity_hp": True,
                                   "player_max_hp": False}}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            migrate_legacy_pack_keys(prefs)
        self.assertNotIn("cheat_entity_hp", prefs["enabled_packs"])
        self.assertFalse(prefs["enabled_packs"]["player_max_hp"])


if __name__ == "__main__":
    unittest.main()
