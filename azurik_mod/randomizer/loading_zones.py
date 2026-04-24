"""Canonical catalog of every loading zone in vanilla Azurik.

A **loading zone** is any point in the game that causes the engine's
level loader (``load_level_from_path``, vanilla VA ``0x00053750``) to
unload the current level and load a new one.  In Azurik these come
from five distinct places:

1. **Level-XBR `levelSwitch` buckets** — every randomizable level has a
   single config bucket (near the end of the file) that stores
   ``(destination_path, start_spot)`` pairs.  The engine parses this
   bucket when a `levelSwitch` entity fires in game.  These are the
   only zones the :mod:`azurik_mod.randomizer.shufflers` connection
   shuffler touches.
2. **Side-file buckets** — ``selector.xbr`` (the dev / cheat menu) and
   the ``diskreplace_*.xbr`` cutscene levels also contain
   ``levels/...`` strings, but the randomizer doesn't load them so
   they aren't shuffled.  They ARE loading zones, just not
   randomizable ones.
3. **Implicit / movie-only zones** — some `levelSwitch` entities have
   NO ``levels/...`` destination string.  They play a bink sequence
   and let the engine's state machine (``scene_state_tick``, VA
   ``0x00055AB0``) pick the return level.  Training-room exits,
   airship docking / trans zones, and every ``diskreplace_*`` cutscene
   work this way, plus a handful of boss-trigger / disk-placement
   entities in the main levels.
4. **Hardcoded XBE zones** — the XBE itself contains a handful of
   level-path literals used as fallbacks when the state machine can't
   derive a destination (e.g. ``"levels/water/w1"`` with spot
   ``"Town_W1_Movie"`` as the tutorial-end fallback; ``"levels/selector"``
   as the dev-menu stage-3 fallback).  These fire from code, not from
   level data.
5. **Ending-cutscene zones** — the Spideyzar-kill credits chain in
   ``d1.xbr`` (``EndGame_levelSwitch``) plays seven bink movies in
   sequence and then invokes ``load_level_from_path`` with the
   ``return-to-shell`` spot, which drops the player back out to the
   Xbox shell.  Cross-references the same ``return-to-shell`` spot
   that ``level_teleport_helper`` (VA 0x00052950) uses for debug-
   console exit.

This module catalogs EVERY zone of types (1)-(4) so downstream tools
have a single, typed source of truth.  The scanner in
:func:`azurik_mod.randomizer.shufflers._find_level_transitions` is
derived from the same truth table, and :mod:`tests.test_loading_zones`
pins the two against each other against a vanilla ISO fixture.

Data was extracted by scanning the shipped USA ISO's decompiled
``gamedata/`` tree in 2026 and cross-referenced with:

- ``prefetch-lists.txt`` neighbour graph
- ``selector.xbr``'s level-select bucket
- Ghidra decompilation of ``FUN_00053750`` / ``FUN_00055AB0`` /
  ``FUN_00052950`` / ``FUN_00052F50`` (``dev_menu_flag_check``)

Cut-level references (``f7``, ``e4``) are surfaced via
:data:`azurik_mod.assets.KNOWN_CUT_LEVELS`; the randomizer's
:data:`EXCLUDE_TRANSITIONS` is derived from that set below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, Iterable


@dataclass(frozen=True)
class LoadingZone:
    """A single loading zone: one destination path + optional spot.

    Attributes
    ----------
    src_level:
        Short name of the level whose XBR (or XBE code) owns this
        zone — e.g. ``"w1"``, ``"selector"``, ``"<xbe>"`` for a
        hardcoded literal.
    dest_path:
        Raw ``levels/<element>/<short>`` string exactly as stored in
        the XBR (or XBE).  This is the string the engine passes to
        ``load_level_from_path``.
    dest_level:
        Short name of the destination — the suffix after the last
        ``/`` in :attr:`dest_path`.  For pseudo-zones (empty string
        or ``movies/...``) this is empty.
    spot:
        Named spawn point inside the destination level (``"Town_W1"``
        etc.).  Empty when the engine defaults to the level's
        origin.
    movie:
        Optional ``bink:...`` or ``movies/...`` cutscene path played
        BEFORE the level load.  Empty for plain portals.
    kind:
        One of ``randomizable`` | ``cheat_menu`` | ``cutscene_return`` |
        ``implicit`` | ``hardcoded_xbe``.  Used by the randomizer to
        decide eligibility; documented as a stable public field.
    notes:
        Optional free-form context (Ghidra VA, disk-replace element,
        cut-level flag, ...).
    """
    src_level: str
    dest_path: str
    dest_level: str
    spot: str = ""
    movie: str = ""
    kind: str = "randomizable"
    notes: str = ""


# ---------------------------------------------------------------------------
# 1. Randomizable zones — extracted from level XBR `levelSwitch` buckets
# ---------------------------------------------------------------------------
#
# 50 zones across 22 levels, all extracted by
# :func:`azurik_mod.randomizer.shufflers._find_level_transitions` on
# the shipped USA ISO.  File offsets are documented in
# ``docs/LOADING_ZONES_AUDIT.md``; they're NOT hardcoded here because
# they shift if any upstream editor touches the level.
#
# Three entries have been flagged as non-randomizable:
#
# - ``f1 -> f7``:  ``f7`` is :data:`KNOWN_CUT_LEVELS`.  Opening this
#   portal crashes with a missing-level error.
# - ``w1 -> airship``: ``airship`` is a one-way cutscene zone that
#   returns via ``airship_docking`` -> ``a3``.  Shuffling the entry
#   would orphan the cutscene.
# - ``e2 -> e2``: self-loop that's actually a bink-cutscene "return
#   to this level when done" marker (plays ``catalisks.bik``).
#   Rewriting it to a different destination hangs the cutscene
#   system.

_RANDOMIZABLE_ZONES: tuple[LoadingZone, ...] = (
    LoadingZone("town", "levels/life",        "life",    "Town_L1"),
    LoadingZone("town", "levels/earth/e2",    "e2",      "Town_E2"),
    LoadingZone("town", "levels/fire/f1",     "f1",      "Town_F1"),
    LoadingZone("town", "levels/death/d1",    "d1",      "Town_D1"),
    LoadingZone("town", "levels/water/w1",    "w1",      "Town_W1"),

    LoadingZone("life", "levels/town",        "town",    "life_disk_loc",
                movie="movies/scenes/diskreplace_life",
                notes="Life-disk return cutscene"),

    LoadingZone("a1",   "levels/air/a6",      "a6",      "A1_A6"),
    LoadingZone("a1",   "levels/earth/e6",    "e6",      "A1_E6"),
    LoadingZone("a3",   "levels/air/a5",      "a5",      "A3_A5"),
    LoadingZone("a5",   "levels/water/w1",    "w1",      "A5_W1"),
    LoadingZone("a6",   "levels/air/a1",      "a1",      "A6_A1"),
    LoadingZone("a6",   "levels/town",        "town",    "air_disk_loc"),

    LoadingZone("f1",   "levels/fire/f7",     "f7",      "F1_F7",
                kind="cut_level",
                notes="f7 is a cut level; opening this portal crashes"),
    LoadingZone("f1",   "levels/fire/f6",     "f6",      "F1_F6"),
    LoadingZone("f1",   "levels/town",        "town",    "F1_Town"),
    LoadingZone("f1",   "levels/fire/f3",     "f3",      "F1_F3"),
    LoadingZone("f2",   "levels/earth/e5",    "e5",      "F2_E5"),
    LoadingZone("f2",   "levels/fire/f4",     "f4",      "F2_F4b"),
    LoadingZone("f3",   "levels/fire/f1",     "f1",      "F3_F1"),
    LoadingZone("f3",   "levels/water/w4",    "w4",      "F3_W4"),
    LoadingZone("f4",   "levels/fire/f2",     "f2",      "F4_F2"),
    LoadingZone("f6",   "levels/fire/f1",     "f1",      "F6_F1"),
    LoadingZone("f6",   "levels/town",        "town",    "fire_disk_loc",
                movie="movies/scenes/diskreplace_fire",
                notes="Fire-disk return cutscene"),

    LoadingZone("w1",   "levels/water/w2",    "w2",      "W1_W2"),
    LoadingZone("w1",   "levels/water/w3",    "w3",      "W1_W3b"),
    LoadingZone("w1",   "levels/water/w4",    "w4",      "W1_W4"),
    LoadingZone("w1",   "levels/town",        "town",    "W1_Town_Waterfall"),
    LoadingZone("w1",   "levels/air/airship", "airship", "",
                movie="bink:airship2.bik",
                kind="one_way_cutscene",
                notes="Airship boarding; one-way, returns via airship_docking"),
    LoadingZone("w1",   "levels/air/a3",      "a3",      "W1_A3"),
    LoadingZone("w2",   "levels/water/w1",    "w1",      "W2_W1a"),
    LoadingZone("w3",   "levels/water/w1",    "w1",      "W3_W1a"),
    LoadingZone("w3",   "levels/town",        "town",    "water_disk_loc",
                movie="movies/scenes/diskreplace_water",
                notes="Water-disk return cutscene"),
    LoadingZone("w4",   "levels/fire/f3",     "f3",      "W4_F3"),
    LoadingZone("w4",   "levels/water/w1",    "w1",      "W4_W1"),

    LoadingZone("e2",   "levels/town",        "town",    "E2_Town"),
    LoadingZone("e2",   "levels/earth/e6",    "e6",      "E2_E6"),
    LoadingZone("e2",   "levels/earth/e5",    "e5",      "E2_E5"),
    LoadingZone("e2",   "levels/earth/e7",    "e7",      "E2_E7"),
    LoadingZone("e2",   "levels/water/w2",    "w2",      "E2_W2"),
    LoadingZone("e2",   "levels/earth/e2",    "e2",      "",
                movie="bink:catalisks.bik",
                kind="self_loop_cutscene",
                notes="Catalisk bink cutscene; e2 loops back to itself"),
    LoadingZone("e5",   "levels/earth/e2",    "e2",      "E5b_E2b"),
    LoadingZone("e5",   "levels/town",        "town",    "earth_disk_loc",
                movie="movies/scenes/diskreplace_earth",
                notes="Earth-disk return cutscene"),
    LoadingZone("e5",   "levels/fire/f2",     "f2",      "E5_F2"),
    LoadingZone("e6",   "levels/earth/e2",    "e2",      "E6_E2b"),
    LoadingZone("e6",   "levels/air/a1",      "a1",      "E6_A1"),
    LoadingZone("e7",   "levels/earth/e2",    "e2",      "E7_E2"),
    LoadingZone("d1",   "levels/town",        "town",    "D1_Town"),
    LoadingZone("d1",   "levels/death/d2",    "d2",      "D1_D2"),
    LoadingZone("d2",   "levels/death/d1",    "d1",      "D2_D1"),

    LoadingZone("airship", "levels/air/a3",   "a3",      "W1_A3",
                movie="movies/scenes/airship_docking",
                kind="one_way_cutscene",
                notes="Airship arrival cutscene; always lands at W1_A3 spot"),
)


# ---------------------------------------------------------------------------
# 2. Non-randomizable side-file zones — audit-only
# ---------------------------------------------------------------------------
#
# These appear in XBRs the randomizer doesn't load.  They're real
# loading zones (the engine follows them) but shuffling them would
# break cutscene flow, so they're enumerated here purely for
# documentation + auditing.

# The shipped ``selector.xbr`` carries a 23-slot level-select bucket
# (file offset 0x286604..0x2867a4 in vanilla USA) followed by a
# movie-cutscene playback bucket the dev menu uses to replay major
# story cutscenes.  The level-select bucket's final slot is
# ``levels/selector`` itself — a self-re-entry that plays the
# prophecy intro movie before re-loading selector.xbr.  Found during
# the 2026 deep-pass audit (see docs/LOADING_ZONES_AUDIT.md).
_SELECTOR_ZONES: tuple[LoadingZone, ...] = tuple(
    LoadingZone("selector", dp, dp.split("/")[-1], kind="cheat_menu",
                notes="selector.xbr level-select hub (cheat menu only)")
    for dp in (
        "levels/fire/f1", "levels/fire/f2", "levels/fire/f3",
        "levels/fire/f4", "levels/fire/f6",
        "levels/air/a1", "levels/air/a3", "levels/air/a5", "levels/air/a6",
        "levels/life",
        "levels/death/d1", "levels/death/d2",
        "levels/earth/e2",
        "levels/earth/e4",   # KNOWN_CUT_LEVELS — selector still advertises it
        "levels/earth/e5", "levels/earth/e6", "levels/earth/e7",
        "levels/water/w1", "levels/water/w2",
        "levels/water/w3", "levels/water/w4",
        "levels/town",
        "levels/selector",   # self-re-entry slot (plays prophecy intro)
    )
)

_CUTSCENE_RETURN_ZONES: tuple[LoadingZone, ...] = (
    LoadingZone("diskreplace_earth", "levels/town", "town", "earth_disc_loc",
                movie="movies/scenes/airship_docking_water",
                kind="cutscene_return",
                notes="Earth-disk placement cutscene -> town"),
    LoadingZone("diskreplace_water", "levels/town", "town", "water_disc_loc",
                movie="movies/scenes/wirlpoolfixed",
                kind="cutscene_return",
                notes="Water-disk placement cutscene -> town"),
    LoadingZone("wirlpoolfixed", "levels/town", "town", "water_disc_loc",
                movie="movies/scenes/airship_docking_water",
                kind="cutscene_return",
                notes="Whirlpool-fixed cutscene -> town (post-water-disk)"),
)


# ---------------------------------------------------------------------------
# 3. Implicit zones — `levelSwitch` entities without a `levels/...` path
# ---------------------------------------------------------------------------
#
# These entities live in the XBR but have NO destination string —
# the engine's state machine (``scene_state_tick``, VA 0x00055AB0)
# picks the return level based on contextual flags (parent-level
# name, cutscene flag, end-of-tutorial flag, etc.).  Enumerated here
# for documentation; they cannot be shuffled because there's no path
# to rewrite.
#
# Each tuple is (source_xbr, entity_name, notes).
#
# Enumeration method (2026 deep-pass audit): scan every XBR for
# strings of the form ``<name>_levelSwitch\0`` (MAYA export
# convention).  Cross-reference with the destination-string scan to
# split "explicit" (entity has a sibling ``levels/...`` string) from
# "implicit" (entity has none).  Files that carry only the bare
# ``levelSwitch\0`` type-class tag (no prefixed entity name) have a
# single anonymous type-instance that's driven entirely by the scene
# state machine — those are catalogued with ``(anonymous)`` as the
# entity name.

_IMPLICIT_ZONES: tuple[tuple[str, str, str], ...] = (
    # ---- Main-level implicit entities (discovered 2026 deep-pass) ----
    ("a6", "Killed_levelSwitch",
     "Air-boss death trigger; plays disk-acquisition cutscene"),
    ("a6", "PlaceAirDisk_levelSwitch",
     "Air-disk placement marker; engine chains into diskreplace_air "
     "which then fires the explicit a6 -> town portal"),
    ("airship", "EndFight_levelSwitch",
     "End-of-airship-boss-fight trigger; returns via scene state "
     "machine (no explicit levels/... path)"),
    ("d1", "D2_movie_levelSwitch",
     "d2 entrance cutscene trigger; plays bink:death.bik with spot "
     "D1_D2_Fall then loads d2 via the main D1_D2_levelSwitch path"),
    ("e5", "DiskReplaced_levelSwitch",
     "Post-earth-disk-replacement trigger in e5; routes through "
     "diskreplace_earth -> town"),
    ("f6", "BackToTown_levelSwitch",
     "Non-disk-triggered f6 -> town trigger (alt-path); engine "
     "routes through the explicit F6_Town portal data"),
    ("life", "Movie_levelSwitch",
     "life-movie cutscene trigger; plays a life-flavour bink before "
     "chaining to the explicit Town_levelSwitch portal"),
    ("w1", "AirshipFight_levelSwitch",
     "Airship boss-fight intro trigger; routes through the "
     "airship cutscene chain (w1 -> airship via bink:airship2)"),
    ("w1", "BossDefeated_levelSwitch",
     "w1 boss-defeat trigger; routes through the disksrestored "
     "cutscene and back to w1"),

    # ---- Tutorial ----
    ("training_room", "EndTutorial_levelSwitch",
     "End-of-tutorial trigger; engine routes via XBE fallback "
     "(see HARDCODED_XBE_ZONES entry for levels/water/w1 / "
     "Town_W1_Movie).  Spot: 'start-playing'"),
    ("training_room", "(anonymous movie-chain)",
     "Tutorial intro movie chain: training.bik / possessed.bik / "
     "disksdestroyed.bik with spot 'start-playing'; scene state "
     "machine picks up the hardcoded levels/water/w1 after"),

    # ---- Airship cutscene XBRs (each has a single anonymous entity) ----
    ("airship_docking", "(anonymous)",
     "Airship docking cutscene; return target set by caller's "
     "scene state (typically A3/W1)"),
    ("airship_docking_water", "(anonymous)",
     "Water-variant docking cutscene"),
    ("airship_trans", "(anonymous)",
     "Airship travel transition (flight intermission between "
     "airship and a3/a5/w1)"),

    # ---- Disk-replacement cutscene XBRs (each has a single anon entity) ----
    ("diskreplace_air", "(anonymous)",
     "Air-disk replacement cutscene; implicit return via movie "
     "chain (no sibling BacktoTown entity)"),
    ("diskreplace_earth", "AirShipLanding_levelSwitch",
     "Earth-disk cutscene's airship-landing marker (sibling to "
     "the explicit BacktoTown_levelSwitch portal)"),
    ("diskreplace_fire", "(anonymous)",
     "Fire-disk replacement cutscene; implicit return via movie "
     "chain"),
    ("diskreplace_life", "(anonymous)",
     "Life-disk replacement cutscene; implicit return via movie "
     "chain"),
)


# ---------------------------------------------------------------------------
# 3b. Ending-cutscene zones — load_level_from_path with 'return-to-shell'
# ---------------------------------------------------------------------------
#
# The "end of game" credits sequence sits in ``d1.xbr`` as the
# ``EndGame_levelSwitch`` entity.  Its stored data is a
# semicolon-delimited bink chain of seven movies (spideyzar death +
# 'disks restored' + 4 credits reels) followed by the
# ``return-to-shell`` spot literal.  ``scene_state_tick`` plays the
# movie chain and then calls ``load_level_from_path`` with a null
# level path + spot ``"return-to-shell"`` — the same shell-return
# path the ``level_teleport_helper`` uses for debug-console exit.
#
# Discovered in the 2026 deep-pass audit.  Not randomizable
# (destination is the Xbox shell, not a level).

_ENDING_ZONES: tuple[LoadingZone, ...] = (
    LoadingZone("d1", "(shell)", "", "return-to-shell",
                movie=("bink:spideyzardeath.bik;disksrestored.bik;"
                       "newdeath.bik;credits.bik;credits2.bik;"
                       "credits3.bik;credits4.bik"),
                kind="ending_cutscene",
                notes="Spideyzar-kill end-of-game trigger. Plays 7-movie "
                      "credits chain then exits to the Xbox shell via "
                      "the shared 'return-to-shell' spot handler in "
                      "level_teleport_helper (VA 0x00052950)."),
)


# ---------------------------------------------------------------------------
# 4. Hardcoded XBE zones — level-path literals baked into default.xbe
# ---------------------------------------------------------------------------
#
# Each of these is a ``levels/...`` string literal + associated spot
# baked into the game executable itself (NOT into any XBR).  They
# act as final fallbacks when the scene state machine can't derive a
# destination from the active levelSwitch entity.
#
# VAs are documented so ghidra-sync can annotate them + so future
# changes can find these sites.

_HARDCODED_XBE_ZONES: tuple[LoadingZone, ...] = (
    LoadingZone("<xbe>", "levels/water/w1", "w1", "Town_W1_Movie",
                kind="hardcoded_xbe",
                notes="End-of-tutorial fallback in scene_state_tick "
                      "(VA 0x00055AB0).  Fired after the "
                      "training.bik / possessed.bik / disksdestroyed.bik "
                      "movie chain when the player hits the "
                      "'start-playing' spot.  String literal at "
                      "data VA 0x0019ECF8."),
    LoadingZone("<xbe>", "levels/selector", "selector", "",
                kind="hardcoded_xbe",
                notes="Dev-menu stage-3 fallback in dev_menu_flag_check "
                      "(VA 0x00052F50).  Used when stages 1 and 2 of "
                      "the level-path validator chain fail.  String "
                      "literal at data VA 0x001A1E3C.  The disabled "
                      "enable_dev_menu shim targets this path."),
    LoadingZone("<xbe>", "levels/training_room", "training_room", "",
                kind="hardcoded_xbe",
                notes="Dev-menu stage-2 candidate in dev_menu_flag_check "
                      "(VA 0x00052F50).  String literal at data VA "
                      "0x001A1E4C."),
    LoadingZone("<xbe>", "(any)", "", "return-to-shell",
                kind="hardcoded_xbe",
                notes="Spot literal 'return-to-shell' pushed into "
                      "load_level_from_path by level_teleport_helper "
                      "(VA 0x00052950) when invoked with the "
                      "shell-return flag.  Takes the player back to "
                      "the main shell / menu."),
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def randomizable_zones() -> tuple[LoadingZone, ...]:
    """Return the 50 zones embedded in level XBRs the randomizer
    shuffles connections across.  Three of these are tagged with
    non-``randomizable`` ``kind`` values (cut_level, one_way_cutscene,
    self_loop_cutscene) to document the entries the shuffler must
    skip."""
    return _RANDOMIZABLE_ZONES


def randomizable_zone_count() -> int:
    """Quick helper — number of entries in :func:`randomizable_zones`."""
    return len(_RANDOMIZABLE_ZONES)


def selector_zones() -> tuple[LoadingZone, ...]:
    """Every ``levels/...`` destination advertised by ``selector.xbr``.

    The cheat-menu level-select bucket has 23 slots in vanilla USA:

    - 22 normal level paths (including the cut level ``e4`` which the
      menu still advertises but has no corresponding XBR on disk)
    - 1 ``levels/selector`` self-re-entry slot (plays the prophecy
      intro cutscene before re-loading selector.xbr)
    """
    return _SELECTOR_ZONES


def cutscene_return_zones() -> tuple[LoadingZone, ...]:
    """Side-file zones (``diskreplace_*``, ``wirlpoolfixed``) that
    still carry a ``levels/...`` path but aren't in the randomizer's
    shuffle set."""
    return _CUTSCENE_RETURN_ZONES


def implicit_zones() -> tuple[tuple[str, str, str], ...]:
    """`levelSwitch` entities that carry NO ``levels/...`` path.  The
    engine derives the destination contextually; the randomizer has
    no string to rewrite.

    Returns a tuple of ``(source_xbr, entity_name, notes)`` tuples.
    """
    return _IMPLICIT_ZONES


def hardcoded_xbe_zones() -> tuple[LoadingZone, ...]:
    """Level-path literals compiled into ``default.xbe``.  These fire
    from code, not from level data, and are never shuffled."""
    return _HARDCODED_XBE_ZONES


def ending_zones() -> tuple[LoadingZone, ...]:
    """Ending-cutscene zones that terminate the game by loading the
    Xbox shell via ``return-to-shell`` spot handling.  Currently a
    single entry (``d1`` Spideyzar-kill credits chain)."""
    return _ENDING_ZONES


def all_zones() -> tuple[LoadingZone, ...]:
    """Every catalogued :class:`LoadingZone` (excluding implicit
    zones, which don't have a destination path)."""
    return (_RANDOMIZABLE_ZONES + _SELECTOR_ZONES +
            _CUTSCENE_RETURN_ZONES + _HARDCODED_XBE_ZONES +
            _ENDING_ZONES)


def derive_exclude_transitions(
    *,
    cut_levels: Iterable[str],
    extra: Iterable[tuple[str, str]] = (),
) -> FrozenSet[tuple[str, str]]:
    """Compute the ``(src_level, dest_level)`` pairs the randomizer's
    connection shuffler must skip.

    Derivation rules:

    1. For every :class:`LoadingZone` in :func:`randomizable_zones`
       whose ``dest_level`` is in ``cut_levels``, emit ``(src, dest)``.
    2. For every :class:`LoadingZone` with a non-``randomizable``
       ``kind`` tag (cut_level, one_way_cutscene, self_loop_cutscene,
       cutscene_return, cheat_menu, hardcoded_xbe), emit ``(src, dest)``
       when ``src`` and ``dest`` are both valid short names.
    3. Union in any explicit ``extra`` pairs (back-compat hook for
       entries the catalog hasn't absorbed yet).

    Returns a frozen set so callers can't accidentally mutate the
    exclusion list.
    """
    cut = frozenset(cut_levels)
    out: set[tuple[str, str]] = set()
    for zone in _RANDOMIZABLE_ZONES:
        if zone.dest_level in cut:
            out.add((zone.src_level, zone.dest_level))
        if zone.kind != "randomizable" and zone.src_level and zone.dest_level:
            out.add((zone.src_level, zone.dest_level))
    out.update(extra)
    return frozenset(out)
