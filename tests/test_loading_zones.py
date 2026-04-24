"""Pins the :mod:`azurik_mod.randomizer.loading_zones` catalog to the
shipped game data.

Three layers of guards:

1. :class:`CatalogShape` — invariants that don't require an ISO
   (counts, uniqueness, kind coverage, `EXCLUDE_TRANSITIONS`
   derivation).  Always runs.
2. :class:`CatalogVsIso` — when a vanilla ISO is present (via the
   ``AZURIK_VANILLA_ISO`` env var or the default decompiled tree),
   verifies every randomizable zone declared in the catalog is
   actually present in the shipped level XBR with the expected
   destination path + spot.  Skipped when the ISO is unavailable.
3. :class:`IsoVsCatalog` — the reverse check: every
   ``levels/...`` portal the scanner finds in a randomizable level
   must have a matching catalog entry.  Catches drift in the
   opposite direction (catalog missed a real zone).
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from azurik_mod.assets import KNOWN_CUT_LEVELS
from azurik_mod.randomizer import loading_zones
from azurik_mod.randomizer.shufflers import (
    EXCLUDE_TRANSITIONS,
    LEVEL_PATHS,
    VALID_DEST_LEVELS,
    _find_level_transitions,
)


_DEFAULT_GAMEDATA = Path(
    "/Users/michaelsrouji/Documents/Xemu/tools/"
    "Azurik - Rise of Perathia (USA).xiso/gamedata"
)


def _find_vanilla_gamedata() -> Path | None:
    """Resolve a decompiled vanilla gamedata directory.

    Lookup order:

    1. ``AZURIK_VANILLA_GAMEDATA`` env var (explicit opt-in).
    2. The default decompiled tree (dev workstation convention).

    Returns ``None`` when neither is available — the ISO-backed tests
    are skipped in that case.
    """
    env = os.environ.get("AZURIK_VANILLA_GAMEDATA")
    if env:
        p = Path(env)
        return p if p.is_dir() else None
    if _DEFAULT_GAMEDATA.is_dir():
        return _DEFAULT_GAMEDATA
    return None


class CatalogShape(unittest.TestCase):
    """Invariants you can check without any game data on disk."""

    def test_randomizable_zone_count_matches_documented_50(self):
        self.assertEqual(
            loading_zones.randomizable_zone_count(), 50,
            msg="The audit doc documents 50 randomizable zones; "
                "if this count changes, update "
                "``docs/LOADING_ZONES_AUDIT.md`` and the "
                "``RANDOMIZABLE_ZONE_COUNT`` constant in the test.")

    def test_every_randomizable_zone_sources_a_known_level(self):
        for zone in loading_zones.randomizable_zones():
            self.assertIn(
                zone.src_level, LEVEL_PATHS,
                msg=f"zone {zone} sources from {zone.src_level!r}, "
                    "which isn't in LEVEL_PATHS — catalog drift")

    def test_randomizable_zones_have_unique_src_offset_pairs(self):
        seen: set[tuple[str, str, str]] = set()
        for zone in loading_zones.randomizable_zones():
            key = (zone.src_level, zone.dest_path, zone.spot)
            self.assertNotIn(
                key, seen,
                msg=f"duplicate randomizable zone (src={zone.src_level}, "
                    f"dest={zone.dest_path}, spot={zone.spot}) — one "
                    "of these is a data-entry copy-paste bug")
            seen.add(key)

    def test_selector_zones_include_cut_level_e4(self):
        """``e4`` is :data:`KNOWN_CUT_LEVELS` but ``selector.xbr``
        still advertises it.  The catalog must surface the dangling
        reference so downstream integrity tools can flag it."""
        dest_levels = {z.dest_level for z in loading_zones.selector_zones()}
        self.assertIn("e4", dest_levels)
        self.assertIn("e4", KNOWN_CUT_LEVELS)

    def test_hardcoded_xbe_zones_cover_known_ghidra_anchors(self):
        """The XBE has at least three documented hardcoded path
        literals (``levels/water/w1``, ``levels/selector``,
        ``levels/training_room``).  The catalog must enumerate all
        three; any addition means a new Ghidra anchor is in play."""
        expected = {"levels/water/w1", "levels/selector",
                    "levels/training_room"}
        got = {z.dest_path for z in loading_zones.hardcoded_xbe_zones()
               if z.dest_path.startswith("levels/")}
        self.assertTrue(
            expected.issubset(got),
            msg=f"hardcoded-XBE zones missing {expected - got} — "
                "update loading_zones.py when new XBE path literals "
                "are found and re-run ghidra-sync")

    def test_selector_has_self_reentry_slot(self):
        """The selector's level-select bucket has a 23rd slot
        (``levels/selector``) that re-enters selector.xbr with the
        prophecy-intro cutscene.  Discovered in the 2026 deep-pass
        audit."""
        dests = {z.dest_path for z in loading_zones.selector_zones()}
        self.assertIn("levels/selector", dests,
            msg="selector self-re-entry slot missing from catalog")
        self.assertEqual(
            len(loading_zones.selector_zones()), 23,
            msg="selector.xbr has 23 level-select slots in vanilla USA "
                "(22 normal + 1 self-re-entry).  If this changes, "
                "verify against the ISO's selector.xbr TOC at "
                "file offset 0x286604.")

    def test_ending_zones_include_credits_chain(self):
        """The end-of-game credits sequence in ``d1.xbr`` is a
        catalogued ending-cutscene zone (not a normal load zone)."""
        eends = loading_zones.ending_zones()
        self.assertEqual(
            len(eends), 1,
            msg="Currently only the d1 Spideyzar credits chain is a "
                "catalogued ending zone; adding a new one means a new "
                "`return-to-shell` trigger was found.")
        zone = eends[0]
        self.assertEqual(zone.src_level, "d1")
        self.assertEqual(zone.spot, "return-to-shell")
        self.assertIn("credits.bik", zone.movie)
        self.assertIn("spideyzardeath.bik", zone.movie)

    def test_implicit_zones_include_2026_discoveries(self):
        """Spot-check the implicit-zone entries surfaced in the 2026
        deep-pass audit so they don't regress out of the catalog."""
        implicit = loading_zones.implicit_zones()
        names_by_src: dict[str, set[str]] = {}
        for src, entity, _notes in implicit:
            names_by_src.setdefault(src, set()).add(entity)
        # Key entity names that must be present
        self.assertIn("Killed_levelSwitch", names_by_src.get("a6", set()))
        self.assertIn("PlaceAirDisk_levelSwitch",
                      names_by_src.get("a6", set()))
        self.assertIn("EndFight_levelSwitch",
                      names_by_src.get("airship", set()))
        self.assertIn("AirShipLanding_levelSwitch",
                      names_by_src.get("diskreplace_earth", set()))
        self.assertIn("AirshipFight_levelSwitch",
                      names_by_src.get("w1", set()))
        self.assertIn("BossDefeated_levelSwitch",
                      names_by_src.get("w1", set()))

    def test_exclude_transitions_derived_from_catalog(self):
        """``EXCLUDE_TRANSITIONS`` must be kept in sync with the
        declarative catalog.  Hand-editing it breaks this test."""
        expected = loading_zones.derive_exclude_transitions(
            cut_levels=KNOWN_CUT_LEVELS,
        )
        self.assertEqual(EXCLUDE_TRANSITIONS, expected)

    def test_exclude_transitions_contains_known_pairs(self):
        """Regression pin for the four currently-documented
        exclusions.  Changes here need a co-update in
        ``docs/LOADING_ZONES_AUDIT.md``."""
        self.assertIn(("f1", "f7"), EXCLUDE_TRANSITIONS)
        self.assertIn(("w1", "airship"), EXCLUDE_TRANSITIONS)
        self.assertIn(("airship", "a3"), EXCLUDE_TRANSITIONS)
        self.assertIn(("e2", "e2"), EXCLUDE_TRANSITIONS)

    def test_valid_dest_levels_excludes_airship(self):
        """One-way cutscene destination; randomizing an entry into
        it would orphan the airship cutscene flow."""
        self.assertNotIn("airship", VALID_DEST_LEVELS)


@unittest.skipUnless(_find_vanilla_gamedata() is not None,
                     "vanilla decompiled gamedata fixture required")
class CatalogVsIso(unittest.TestCase):
    """For every declared randomizable zone, assert the shipped
    level XBR contains a matching portal with the right destination
    path + start spot."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gamedata = _find_vanilla_gamedata()

    def test_every_randomizable_zone_found_in_iso(self):
        by_src: dict[str, list[loading_zones.LoadingZone]] = {}
        for z in loading_zones.randomizable_zones():
            by_src.setdefault(z.src_level, []).append(z)

        for src_level, zones in by_src.items():
            xbr = self.gamedata / f"{src_level}.xbr"
            self.assertTrue(xbr.exists(),
                msg=f"{xbr} not found — decompiled tree missing level")
            data = xbr.read_bytes()
            iso_portals = _find_level_transitions(data, src_level)
            iso_keys = {(p["dest_path"], p["spot"]) for p in iso_portals}
            for z in zones:
                self.assertIn(
                    (z.dest_path, z.spot), iso_keys,
                    msg=f"catalog says {src_level} -> {z.dest_path} "
                        f"(spot={z.spot!r}) but the ISO doesn't have "
                        "that portal.  Likely catalog drift — rerun "
                        "the LOADING_ZONES audit.")

    def test_every_cutscene_return_zone_found_in_iso(self):
        """``diskreplace_earth/water`` and ``wirlpoolfixed`` each
        contain a single real ``levels/town`` portal that the
        catalog has to track."""
        for z in loading_zones.cutscene_return_zones():
            xbr = self.gamedata / f"{z.src_level}.xbr"
            self.assertTrue(xbr.exists(), msg=f"{xbr} missing")
            data = xbr.read_bytes()
            # Simple raw scan — these side files fail the main
            # scanner's "skip self-references" rule.
            import struct  # noqa: F401
            self.assertIn(z.dest_path.encode("ascii"), data,
                msg=f"{z.src_level}.xbr has no {z.dest_path!r} — "
                    "cutscene-return catalog drift")

    def test_selector_zones_match_iso(self):
        xbr = self.gamedata / "selector.xbr"
        self.assertTrue(xbr.exists())
        data = xbr.read_bytes()
        for z in loading_zones.selector_zones():
            self.assertIn(
                z.dest_path.encode("ascii"), data,
                msg=f"selector.xbr does not reference {z.dest_path!r}")


@unittest.skipUnless(_find_vanilla_gamedata() is not None,
                     "vanilla decompiled gamedata fixture required")
class IsoVsCatalog(unittest.TestCase):
    """Reverse drift — every randomizable ``levels/...`` portal the
    scanner finds in a shuffle-eligible level must be catalogued."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gamedata = _find_vanilla_gamedata()

    def test_no_extra_portals_in_randomizable_levels(self):
        catalog_keys = {
            (z.src_level, z.dest_path, z.spot)
            for z in loading_zones.randomizable_zones()
        }
        missing: list[str] = []
        for src_level in LEVEL_PATHS:
            xbr = self.gamedata / f"{src_level}.xbr"
            if not xbr.exists():
                continue
            data = xbr.read_bytes()
            for p in _find_level_transitions(data, src_level):
                key = (src_level, p["dest_path"], p["spot"])
                if key not in catalog_keys:
                    missing.append(
                        f"ISO portal not in catalog: {key}")
        self.assertEqual(missing, [],
            msg="New portals found in the ISO that the catalog "
                "doesn't know about — update "
                "azurik_mod/randomizer/loading_zones.py")


if __name__ == "__main__":
    unittest.main()
