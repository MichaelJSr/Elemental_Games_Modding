"""
Azurik: Rise of Perathia — Randomizer Logic Solver

Inspired by Randovania's resolver architecture. Given a placement of items
across the game world, determines whether the game is completable.

Architecture:
  - World graph loaded from logic_db.json (nodes, edges, requirements)
  - BFS reachability: expand from starting node, collect reachable items,
    re-expand until no new nodes are reachable (fixed-point)
  - Victory check: can all victory conditions be satisfied?

Usage:
  from solver import Solver
  s = Solver()
  solvable, state = s.solve(placement)
  # placement = {"node_id": {"pickup_name": "actual_item_placed", ...}, ...}
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


POWER_NAMES = {
    "power_water", "power_water_a3", "power_air",
    "power_earth", "power_fire", "power_ammo",
}
# Map duplicate power pickups to their base element for level tracking
POWER_BASE = {
    "power_water": "power_water",
    "power_water_a3": "power_water",
    "power_air": "power_air",
    "power_earth": "power_earth",
    "power_fire": "power_fire",
    "power_ammo": "power_ammo",
}


@dataclass
class SolverState:
    """Tracks what the player has collected and where they can reach."""
    inventory: set[str] = field(default_factory=set)
    visited_nodes: set[str] = field(default_factory=set)
    reachable_nodes: set[str] = field(default_factory=set)
    collected_pickups: set[str] = field(default_factory=set)
    triggered_events: set[str] = field(default_factory=set)
    steps: list[str] = field(default_factory=list)
    power_levels: dict[str, int] = field(default_factory=dict)

    def has_all(self, requirements: list[str] | dict) -> bool:
        """Check if inventory satisfies a requirement expression.

        Fail CLOSED (return False) when handed a malformed requirement
        dict whose shape we don't understand — previously this fell
        through to a vacuous ``return True``, which is the same class
        of "silently-permissive solver check" that let the power-
        placement bug ship undetected for months.  If a hand-edited
        ``logic_db.json`` uses an unrecognised schema, the solver
        should report UNSOLVABLE (or the caller should catch the
        mismatch) rather than silently pass every check.

        Accepted shapes:
          - ``[]`` / ``[item, ...]``                 — all of the list
          - ``{"items": [...], "type": "all_of"}``   — all of
          - ``{"items": [...], "type": "any_of"}``   — any of
          - ``{"all_of": [...]}``                    — all of
          - ``{"any_of": [...]}``                    — any of
          - ``{}`` / ``{"items": []}`` / empty lists — vacuously True
            (a node with no requirements is always reachable)
        """
        if isinstance(requirements, list):
            return all(r in self.inventory for r in requirements)
        if isinstance(requirements, dict):
            # Empty dict / empty-items dict: vacuously true — a node
            # that lists no requirements is always accessible.
            items = (requirements.get("items")
                     or requirements.get("all_of")
                     or requirements.get("any_of"))
            if not items:
                return True
            req_type = requirements.get(
                "type",
                "all_of" if "all_of" in requirements
                else "any_of" if "any_of" in requirements
                else None)
            if req_type == "all_of":
                return all(self.has_all([r]) if isinstance(r, str)
                          else self.has_all(r)
                          for r in items)
            if req_type == "any_of":
                return any(self.has_all([r]) if isinstance(r, str)
                          else self.has_all(r)
                          for r in items)
            # Unknown shape — fail closed.  Previously fell through to
            # ``return True``; see docstring for the rationale.
            return False
        # Not a list or dict — also fail closed.
        return False


# Module-level cache for parsed ``logic_db.json`` — keyed by
# ``(resolved_path, mtime)`` so an edit to the DB file invalidates
# cleanly.  Previously every ``Solver()`` construction re-read +
# re-parsed the ~50 KB JSON; a single ``randomize-full`` run
# instantiates the Solver at least twice (major items + powers).
# Amortising the parse is a free ~10 ms per run for zero behavioural
# change.
_db_cache: dict[tuple[str, float], dict] = {}


def _load_logic_db(path: Path) -> dict:
    """Return the parsed logic DB for ``path``, caching by mtime."""
    try:
        st = path.stat()
        key = (str(path.resolve()), st.st_mtime)
    except OSError:
        key = None

    if key is not None and key in _db_cache:
        return _db_cache[key]

    with open(path, "r") as f:
        db = json.load(f)

    if key is not None:
        _db_cache[key] = db
    return db


class Solver:
    def __init__(self, logic_db_path: str | Path | None = None):
        if logic_db_path is None:
            logic_db_path = Path(__file__).parent / "logic_db.json"
        self.db = _load_logic_db(Path(logic_db_path))
        self.nodes = self.db["nodes"]
        self.edges = self.db["edges"]
        self.disc_sources = self.db["disc_sources"]
        self.disc_from_fragments = self.db.get("disc_from_fragments", {})
        self.victory = self.db["victory_condition"]
        self.starting_node = self.db["starting_node"]

    def solve(self, placement: dict[str, dict[str, str]] | None = None,
              verbose: bool = False) -> tuple[bool, SolverState]:
        """
        Run the solver with an optional item placement override.

        placement: {node_id: {original_pickup: replacement_pickup, ...}, ...}
            If None, uses the vanilla placement from logic_db.json.

        Returns (solvable, final_state).
        """
        state = SolverState()
        state.inventory.update(self.db.get("starting_items", []))

        # Build the effective pickup map: node_id -> list of actual items there
        pickup_map = self._build_pickup_map(placement)

        # Fixed-point BFS: keep expanding until no new nodes are reached
        state.reachable_nodes.add(self.starting_node)
        changed = True
        iteration = 0

        while changed:
            changed = False
            iteration += 1
            if verbose:
                print(f"\n--- Iteration {iteration} ---")
                print(f"  Inventory: {sorted(state.inventory)}")
                print(f"  Reachable: {sorted(state.reachable_nodes)}")

            # Collect pickups at newly-reachable nodes
            for node_id in list(state.reachable_nodes):
                if node_id not in state.visited_nodes:
                    state.visited_nodes.add(node_id)
                    changed = True

                    for pickup_idx, item in enumerate(pickup_map.get(node_id, [])):
                        pickup_key = f"{node_id}:{pickup_idx}:{item}"
                        if pickup_key not in state.collected_pickups:
                            state.collected_pickups.add(pickup_key)
                            state.inventory.add(item)
                            state.steps.append(f"Collect {item} at {node_id}")
                            if verbose:
                                print(f"  + Collect: {item} @ {node_id}")

                            base = POWER_BASE.get(item)
                            if base:
                                level = state.power_levels.get(base, 0) + 1
                                state.power_levels[base] = level
                                if level >= 2:
                                    marker = f"{base}_L{level}"
                                    state.inventory.add(marker)
                                    state.steps.append(f"  -> {base} upgraded to L{level}")
                                    changed = True
                                    if verbose:
                                        print(f"    -> Power level: {marker}")

            # Re-evaluate events at ALL reachable nodes every iteration.
            # Events may require items obtained after the node was first visited
            # (e.g. placing discs at town_core after collecting them elsewhere).
            for node_id in list(state.reachable_nodes):
                node_data = self.nodes[node_id]
                for event_def in node_data.get("events", []):
                    event_id = event_def["event"]
                    if event_id in state.triggered_events:
                        continue
                    reqs = event_def.get("requires", [])
                    if state.has_all(reqs):
                        state.triggered_events.add(event_id)
                        state.inventory.add(event_id)
                        state.steps.append(f"Trigger {event_id} at {node_id}")
                        changed = True
                        if verbose:
                            print(f"  * Event: {event_id} @ {node_id}")

                        if event_id in self.disc_sources:
                            drops = self.disc_sources[event_id]
                            if isinstance(drops, str):
                                drops = [drops]
                            for drop in drops:
                                if drop not in state.inventory:
                                    state.inventory.add(drop)
                                    state.steps.append(f"  -> Receive {drop}")
                                    changed = True
                                    if verbose:
                                        print(f"    -> Drop: {drop}")

            # Check if collecting fragments completes a disc
            for disc_name, frags in self.disc_from_fragments.items():
                if disc_name not in state.inventory and all(f in state.inventory for f in frags):
                    state.inventory.add(disc_name)
                    state.steps.append(f"  -> Assemble {disc_name} from fragments")
                    changed = True
                    if verbose:
                        print(f"  * Disc assembled: {disc_name}")

            # Expand reachable nodes via connections
            for node_id in list(state.reachable_nodes):
                node_data = self.nodes[node_id]
                for conn_id, conn_data in node_data.get("connections", {}).items():
                    reqs = conn_data.get("requires", [])
                    if not state.has_all(reqs):
                        continue

                    # Find the target node for this connection
                    target = self._resolve_connection(node_id, conn_id)
                    if target and target not in state.reachable_nodes:
                        state.reachable_nodes.add(target)
                        changed = True
                        if verbose:
                            print(f"  > Reach: {target} (via {conn_id})")

        # Check victory condition
        victory = state.has_all(self.victory)
        if verbose:
            print(f"\n=== Result: {'SOLVABLE' if victory else 'UNSOLVABLE'} ===")
            if not victory:
                missing = self._find_missing(state)
                print(f"  Missing: {missing}")

        return victory, state

    def _build_pickup_map(self, placement: dict | None) -> dict[str, list[str]]:
        """Build node_id -> [items] map, applying placement overrides.

        Supports two placement formats:
        - Name-based: {node_id: {original_item: new_item}} (legacy, no duplicates)
        - Index-based: {node_id: {pickup_index: new_item}} (supports duplicates)
        """
        pickup_map: dict[str, list[str]] = {}

        for node_id, node_data in self.nodes.items():
            vanilla_pickups = list(node_data.get("pickups", []))
            if placement and node_id in placement:
                overrides = placement[node_id]
                resolved = list(vanilla_pickups)
                for key, new_item in overrides.items():
                    if isinstance(key, int):
                        # Index-based override
                        resolved[key] = new_item
                    else:
                        # Name-based override (legacy) — replace first match
                        for i, orig in enumerate(resolved):
                            if orig == key:
                                resolved[i] = new_item
                                break
                pickup_map[node_id] = resolved
            else:
                pickup_map[node_id] = vanilla_pickups

        return pickup_map

    def _resolve_connection(self, from_node: str, conn_id: str) -> str | None:
        """Given a connection ID, find the target node (the other end of the edge)."""
        # First check if conn_id is a direct reference to another node's connection
        if conn_id in self.edges:
            endpoints = self.edges[conn_id]
            for ep in endpoints:
                if ep != from_node:
                    return ep
            # Self-loop edge (shouldn't happen, but handle it)
            return endpoints[0] if endpoints else None

        # Check if conn_id matches a node name directly (for one-way connections)
        if conn_id in self.nodes:
            return conn_id

        return None

    def _find_missing(self, state: SolverState) -> list[str]:
        """Find what victory conditions are not met."""
        missing = []
        items = None
        if isinstance(self.victory, dict):
            items = self.victory.get("items") or self.victory.get("all_of")
        if items:
            for item in items:
                if isinstance(item, str) and item not in state.inventory:
                    missing.append(item)
        return missing

    def _count_locked_locations(self, start_node: str,
                               already_reachable: set[str],
                               unfilled_locations: list) -> int:
        """Count unfilled randomizable locations transitively reachable from
        start_node, ignoring requirement checks (assumes all gates passable).
        This estimates the value of unlocking start_node."""
        visited: set[str] = set()
        queue = [start_node]
        count = 0
        while queue:
            node = queue.pop()
            if node in visited:
                continue
            visited.add(node)
            # Count unfilled locations at this node
            for loc in unfilled_locations:
                if loc[0] == node:
                    count += 1
            # Traverse all connections (ignore requirements)
            node_data = self.nodes.get(node, {})
            for conn_id in node_data.get("connections", {}):
                target = self._resolve_connection(node, conn_id)
                if target and target not in visited and target not in already_reachable:
                    queue.append(target)
        return count

    def _extract_requirement_items(self, reqs: list | dict | str) -> set[str]:
        """Extract all individual item names from a requirement expression."""
        items: set[str] = set()
        if isinstance(reqs, str):
            items.add(reqs)
        elif isinstance(reqs, list):
            for r in reqs:
                items.update(self._extract_requirement_items(r))
        elif isinstance(reqs, dict):
            inner = reqs.get("items") or reqs.get("all_of") or reqs.get("any_of") or []
            for r in inner:
                items.update(self._extract_requirement_items(r))
        return items

    def _get_reachable_state(self, inventory: set[str],
                            skip_pickups: set[str] | None = None,
                            power_levels: dict[str, int] | None = None) -> SolverState:
        """Run reachability with a given inventory.

        Collects fixed pickups (keys, fuel, etc.) at reachable nodes,
        but skips items in skip_pickups (randomizable items whose placement
        is being determined).

        power_levels: pre-seeded power level counts (for tracking duplicates
        that sets can't represent).
        """
        state = SolverState()
        state.inventory = set(inventory)
        state.inventory.update(self.db.get("starting_items", []))
        if power_levels:
            state.power_levels = dict(power_levels)
        state.reachable_nodes.add(self.starting_node)
        if skip_pickups is None:
            skip_pickups = set()

        changed = True
        while changed:
            changed = False
            for node_id in list(state.reachable_nodes):
                if node_id not in state.visited_nodes:
                    state.visited_nodes.add(node_id)
                    changed = True

                    node_data = self.nodes[node_id]
                    for pickup in node_data.get("pickups", []):
                        if pickup not in skip_pickups and pickup not in state.inventory:
                            state.inventory.add(pickup)
                            base = POWER_BASE.get(pickup)
                            if base:
                                level = state.power_levels.get(base, 0) + 1
                                state.power_levels[base] = level
                                if level >= 2:
                                    state.inventory.add(f"{base}_L{level}")
                            changed = True

            # Re-evaluate events at all reachable nodes (not just first visit)
            for node_id in list(state.reachable_nodes):
                node_data = self.nodes[node_id]
                for event_def in node_data.get("events", []):
                    event_id = event_def["event"]
                    if event_id in state.triggered_events:
                        continue
                    reqs = event_def.get("requires", [])
                    if state.has_all(reqs):
                        state.triggered_events.add(event_id)
                        state.inventory.add(event_id)
                        changed = True
                        if event_id in self.disc_sources:
                            drops = self.disc_sources[event_id]
                            if isinstance(drops, str):
                                drops = [drops]
                            for drop in drops:
                                state.inventory.add(drop)
                                changed = True

            for node_id in list(state.reachable_nodes):
                for disc_name, frags in self.disc_from_fragments.items():
                    if disc_name not in state.inventory and all(
                        f in state.inventory for f in frags
                    ):
                        state.inventory.add(disc_name)
                        changed = True

                node_data = self.nodes[node_id]
                for conn_id, conn_data in node_data.get("connections", {}).items():
                    reqs = conn_data.get("requires", [])
                    if not state.has_all(reqs):
                        continue
                    target = self._resolve_connection(node_id, conn_id)
                    if target and target not in state.reachable_nodes:
                        state.reachable_nodes.add(target)
                        changed = True

        return state

    def get_randomizer_groups(self) -> dict[str, dict]:
        """Return group definitions from the DB."""
        return dict(self.db.get("randomizer_groups", {}))

    def forward_fill(
        self,
        rng: Any | None = None,
        item_pool: list[str] | None = None,
        groups: dict[str, list[str]] | None = None,
        verbose: bool = False,
    ) -> tuple[dict[str, dict[str, str]], list[str]]:
        """
        Forward-fill randomizer: place items one at a time, always choosing
        progression items for reachable locations.

        Items shuffle WITHIN their group — a power can only go to a location
        that originally held an item from the same group.

        groups: {group_name: [item1, item2, ...], ...}
            Items shuffle within each group independently.
            If None, uses DB randomizer_groups (powers + fragments by default).
            Pass {"all": [...items...]} to merge everything into one pool.

        item_pool: (legacy) flat list, treated as a single group "pool".

        Returns (placement_dict, step_log)
        """
        import random as _random
        if rng is None:
            rng = _random.Random()

        # Build groups from arguments or DB defaults
        if groups is None and item_pool is not None:
            # Legacy: flat pool = single group
            groups = {"pool": list(item_pool)}
        elif groups is None:
            # Default: merge all stable groups into one pool
            # (separate groups are too restrictive for most seeds)
            db_groups = self.db.get("randomizer_groups", {})
            merged: list[str] = []
            for gname, gdata in db_groups.items():
                if gname.startswith("_"):
                    continue
                if isinstance(gdata, dict) and gdata.get("status") == "stable":
                    merged.extend(gdata.get("items", []))
            groups = {"progression": merged} if merged else {}

        # Build per-group location lists and the combined pool
        # location = (node_id, pickup_idx, original_item, group_name)
        item_pool_combined: list[str] = []
        locations: list[tuple[str, int, str, str]] = []
        item_to_group: dict[int, str] = {}  # index in item_pool -> group

        for group_name, group_items in groups.items():
            if not group_items:
                continue
            # Find locations in the world graph that hold these items
            group_item_counts: dict[str, int] = {}
            for item in group_items:
                group_item_counts[item] = group_item_counts.get(item, 0) + 1

            for node_id, node_data in self.nodes.items():
                remaining = dict(group_item_counts)
                for idx, pickup in enumerate(node_data.get("pickups", [])):
                    if remaining.get(pickup, 0) > 0:
                        remaining[pickup] -= 1
                        locations.append((node_id, idx, pickup, group_name))
                        pool_idx = len(item_pool_combined)
                        item_pool_combined.append(pickup)
                        item_to_group[pool_idx] = group_name

            # Add items from group to combined pool (use locations as authority)
            # Already added above via item_pool_combined

        # Rebuild clean item list from locations (ensures pool matches locations)
        item_pool = [loc[2] for loc in locations]
        # Build group membership: item_index -> group
        loc_groups: dict[int, str] = {}
        for i, loc in enumerate(locations):
            loc_groups[i] = loc[3]

        unplaced_items = list(item_pool)
        unfilled_locations = list(locations)
        # Track which group each unplaced item belongs to
        unplaced_groups: list[str] = [loc[3] for loc in locations]
        # Set of all randomizable item names
        randomizable_items = set(item_pool)
        # placement[node_id][original_item] = new_item
        placement: dict[str, dict[str, str]] = {}
        step_log: list[str] = []
        current_inventory: set[str] = set()
        # Track power counts separately (sets can't store duplicates)
        placed_power_counts: dict[str, int] = {}

        if verbose:
            print(f"Forward fill: {len(unplaced_items)} items, {len(unfilled_locations)} locations")

        # Unified loop: always place at reachable locations.
        # Priority: progression > partial progression > fill (after victory met)
        victory_reached = False
        while unplaced_items:
            # Get current reachability with items placed so far
            # skip_pickups = randomizable items not yet placed (they're empty slots)
            state = self._get_reachable_state(current_inventory,
                                              skip_pickups=randomizable_items,
                                              power_levels=placed_power_counts)
            reachable = state.reachable_nodes

            # Find reachable unfilled locations
            reachable_locs = [
                loc for loc in unfilled_locations
                if loc[0] in reachable
            ]

            if not reachable_locs:
                # No reachable locations remain — this placement attempt
                # has deadlocked. Place remaining items arbitrarily so the
                # caller can validate and retry with a different seed.
                step_log.append(
                    f"DEADLOCK: {len(unplaced_items)} items with no reachable locations"
                )
                if verbose:
                    print(f"  DEADLOCK: {len(unplaced_items)} items stranded")
                # Place remaining items at matching-group locations
                for i, item in enumerate(unplaced_items):
                    grp = unplaced_groups[i]
                    for loc in unfilled_locations:
                        if loc[3] == grp:
                            if loc[0] not in placement:
                                placement[loc[0]] = {}
                            placement[loc[0]][loc[1]] = item
                            unfilled_locations.remove(loc)
                            break
                break

            # Pressure check: if reachable locations <= remaining items,
            # we MUST place items that unlock more locations or we'll deadlock.
            # In this case, restrict to only the highest-scored partial
            # progression items (skip fill entirely).
            under_pressure = len(reachable_locs) <= len(unplaced_items)

            # Check if victory is reachable
            if not victory_reached and state.has_all(self.victory):
                victory_reached = True
                step_log.append("Victory reachable — filling remaining items at reachable locations")
                if verbose:
                    print(f"\n  Victory reachable! Filling {len(unplaced_items)} remaining items")

            if victory_reached:
                # Victory already met — just fill remaining items at reachable spots
                chosen_item = rng.choice(unplaced_items)
                step_log.append(f"Fill: {chosen_item}")
                if verbose:
                    print(f"  Fill: {chosen_item}")
            else:
                # Find progression items: which unplaced items would unlock new nodes?
                progression_items = []
                for item in unplaced_items:
                    test_inv = set(current_inventory)
                    test_inv.add(item)
                    base = POWER_BASE.get(item)
                    if base:
                        count = placed_power_counts.get(base, 0) + 1
                        if count >= 2:
                            test_inv.add(f"{base}_L{count}")

                    # Build test power levels with the hypothetical item
                    test_power = dict(placed_power_counts)
                    if base:
                        test_power[base] = test_power.get(base, 0) + 1
                    test_state = self._get_reachable_state(test_inv,
                                                           skip_pickups=randomizable_items,
                                                           power_levels=test_power)
                    new_nodes = test_state.reachable_nodes - reachable
                    if new_nodes:
                        progression_items.append((item, new_nodes))

                if progression_items:
                    # Prefer progression that unlocks more nodes
                    progression_items.sort(key=lambda x: len(x[1]), reverse=True)
                    # Weight toward higher-value but keep randomness
                    weights = [len(unlocked) for _, unlocked in progression_items]
                    total = sum(weights)
                    r = rng.random() * total
                    cumulative = 0
                    chosen_item = progression_items[0][0]
                    unlocked = progression_items[0][1]
                    for item, nodes in progression_items:
                        cumulative += len(nodes)
                        if r <= cumulative:
                            chosen_item = item
                            unlocked = nodes
                            break
                    step_log.append(
                        f"Progression: {chosen_item} -> unlocks {len(unlocked)} nodes"
                    )
                    if verbose:
                        print(f"  Progression: {chosen_item} (unlocks {sorted(unlocked)[:3]}...)")
                else:
                    # No single item unlocks new nodes — find items that partially
                    # satisfy multi-item gates, scored by how many locked locations
                    # sit behind each gate
                    import re
                    item_scores: dict[str, float] = {}

                    for node_id in reachable:
                        node_data = self.nodes[node_id]
                        # Score connections to unreachable nodes
                        for conn_id, conn_data in node_data.get("connections", {}).items():
                            target = self._resolve_connection(node_id, conn_id)
                            if target and target not in reachable:
                                reqs = conn_data.get("requires", [])
                                needed = self._extract_requirement_items(reqs)
                                missing = needed - current_inventory
                                if not missing:
                                    continue
                                # Count how many locations are behind this gate
                                # (transitively reachable from target)
                                locked_count = self._count_locked_locations(
                                    target, reachable, unfilled_locations)
                                score = max(locked_count, 1)
                                for req_item in missing:
                                    # Direct match
                                    if req_item in unplaced_items:
                                        item_scores[req_item] = item_scores.get(
                                            req_item, 0) + score
                                    # Power level match
                                    m = re.match(r"^(power_\w+)_L(\d+)$", req_item)
                                    if m:
                                        base_power = m.group(1)
                                        for item in unplaced_items:
                                            if POWER_BASE.get(item) == base_power:
                                                item_scores[item] = item_scores.get(
                                                    item, 0) + score

                        # Score unmet events similarly
                        for event_def in node_data.get("events", []):
                            event_id = event_def["event"]
                            if event_id in state.triggered_events:
                                continue
                            reqs = event_def.get("requires", [])
                            needed = self._extract_requirement_items(reqs)
                            missing = needed - current_inventory
                            if not missing:
                                continue
                            for req_item in missing:
                                if req_item in unplaced_items:
                                    item_scores[req_item] = item_scores.get(
                                        req_item, 0) + 1
                                m = re.match(r"^(power_\w+)_L(\d+)$", req_item)
                                if m:
                                    base_power = m.group(1)
                                    for item in unplaced_items:
                                        if POWER_BASE.get(item) == base_power:
                                            item_scores[item] = item_scores.get(
                                                item, 0) + 1

                    if item_scores:
                        scored_items = sorted(item_scores.items(),
                                              key=lambda x: x[1], reverse=True)
                        if under_pressure:
                            # Deterministic: pick the highest-scored item
                            chosen_item = scored_items[0][0]
                        else:
                            # Weighted random selection
                            weights = [s for _, s in scored_items]
                            total = sum(weights)
                            r = rng.random() * total
                            cumulative = 0.0
                            chosen_item = scored_items[0][0]
                            for item, s in scored_items:
                                cumulative += s
                                if r <= cumulative:
                                    chosen_item = item
                                    break
                        step_log.append(
                            f"Partial progression: {chosen_item} "
                            f"(score={item_scores[chosen_item]:.0f})")
                        if verbose:
                            print(f"  Partial progression: {chosen_item} "
                                  f"(score={item_scores[chosen_item]:.0f})")
                    elif under_pressure:
                        # Under pressure with no scored items — bad state,
                        # but we must place something. Pick a fragment
                        # (less likely to gate things than powers).
                        frags = [i for i in unplaced_items
                                 if i.startswith("frag_")]
                        chosen_item = rng.choice(frags if frags else unplaced_items)
                        step_log.append(f"Pressure fill: {chosen_item}")
                        if verbose:
                            print(f"  Pressure fill: {chosen_item}")
                    else:
                        chosen_item = rng.choice(unplaced_items)
                        step_log.append(f"Fill (pre-victory): {chosen_item}")
                        if verbose:
                            print(f"  Fill (pre-victory): {chosen_item}")

            # Find which group this item belongs to
            item_idx = unplaced_items.index(chosen_item)
            chosen_group = unplaced_groups[item_idx]

            # Filter reachable locations to same group
            group_locs = [loc for loc in reachable_locs if loc[3] == chosen_group]
            if not group_locs:
                # No reachable location in this group — try a different item
                # from a group that HAS reachable locations
                available_groups = {loc[3] for loc in reachable_locs}
                fallback_items = [
                    (i, item) for i, item in enumerate(unplaced_items)
                    if unplaced_groups[i] in available_groups
                ]
                if fallback_items:
                    fi, chosen_item = rng.choice(fallback_items)
                    chosen_group = unplaced_groups[fi]
                    group_locs = [loc for loc in reachable_locs
                                  if loc[3] == chosen_group]
                else:
                    # Truly stuck -- no reachable location for any remaining item
                    break

            chosen_loc = rng.choice(group_locs)
            loc_node, loc_idx, loc_orig, loc_group = chosen_loc

            if loc_node not in placement:
                placement[loc_node] = {}
            placement[loc_node][loc_idx] = chosen_item

            step_log.append(f"  Place {chosen_item} at {loc_node} (was {loc_orig}) [{chosen_group}]")
            if verbose:
                print(f"    -> Placed at {loc_node} (slot: {loc_orig}) [{chosen_group}]")

            # Update state
            current_inventory.add(chosen_item)
            base = POWER_BASE.get(chosen_item)
            if base:
                placed_power_counts[base] = placed_power_counts.get(base, 0) + 1
                count = placed_power_counts[base]
                if count >= 2:
                    current_inventory.add(f"{base}_L{count}")

            rm_idx = unplaced_items.index(chosen_item)
            unplaced_items.pop(rm_idx)
            unplaced_groups.pop(rm_idx)
            unfilled_locations.remove(chosen_loc)

        return placement, step_log

    def validate_placement(self, placement: dict[str, dict[str, str]],
                           verbose: bool = False) -> tuple[bool, SolverState]:
        """
        Validate a randomizer placement. Convenience wrapper for solve().
        """
        return self.solve(placement, verbose=verbose)

    def get_all_pickup_locations(self) -> dict[str, list[str]]:
        """Return all nodes and their vanilla pickup lists."""
        result = {}
        for node_id, node_data in self.nodes.items():
            pickups = node_data.get("pickups", [])
            if pickups:
                result[node_id] = list(pickups)
        return result

    def get_all_randomizable_items(self) -> dict[str, list[str]]:
        """Return item lists by category from the DB."""
        return {
            "powers": list(self.db["items"]["powers"]),
            "fragments": list(self.db["items"]["fragments"]),
        }

    def get_groups_with_status(self) -> dict[str, dict]:
        """Return all randomizer groups with their status and items."""
        result = {}
        for gname, gdata in self.db.get("randomizer_groups", {}).items():
            if gname.startswith("_"):
                continue
            if isinstance(gdata, dict):
                result[gname] = {
                    "status": gdata.get("status", "unknown"),
                    "description": gdata.get("description", ""),
                    "items": list(gdata.get("items", [])),
                }
        return result

    def build_custom_groups(
        self,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        merge: list[str] | None = None,
        moves: dict[str, str] | None = None,
    ) -> dict[str, list[str]]:
        """
        Build custom group configuration from the DB defaults + user overrides.

        include: group names to activate (default: stable groups only)
        exclude: group names to skip
        merge:   list of group names to merge into one "merged" group
        moves:   {item_name: target_group} — move items between groups

        Returns {group_name: [items...]} ready for forward_fill(groups=...).
        """
        db_groups = self.db.get("randomizer_groups", {})
        result: dict[str, list[str]] = {}

        # Step 1: Select which groups to include
        for gname, gdata in db_groups.items():
            if gname.startswith("_") or not isinstance(gdata, dict):
                continue
            if include is not None:
                if gname not in include:
                    continue
            else:
                # Default: stable only
                if gdata.get("status") != "stable":
                    continue
            if exclude and gname in exclude:
                continue
            items = gdata.get("items", [])
            if items:
                result[gname] = list(items)

        # Step 2: Apply item moves
        if moves:
            for item_name, target_group in moves.items():
                # Remove from current group
                for gname, gitems in result.items():
                    while item_name in gitems:
                        gitems.remove(item_name)
                # Add to target group
                if target_group not in result:
                    result[target_group] = []
                result[target_group].append(item_name)

        # Step 3: Merge specified groups
        if merge:
            merged_items: list[str] = []
            for gname in merge:
                if gname in result:
                    merged_items.extend(result.pop(gname))
            if merged_items:
                result["merged"] = merged_items

        # Step 4: Remove empty groups
        result = {k: v for k, v in result.items() if v}

        # Default: if multiple groups remain, merge all into one
        # (separate groups have very low solvability)
        if len(result) > 1 and not merge and not moves:
            all_items: list[str] = []
            for items in result.values():
                all_items.extend(items)
            result = {"progression": all_items}

        return result

    def get_level_to_nodes(self) -> dict[str, list[str]]:
        """Return mapping of level names to node IDs."""
        level_map: dict[str, list[str]] = {}
        for node_id, node_data in self.nodes.items():
            level = node_data.get("level", "")
            if level:
                level_map.setdefault(level, []).append(node_id)
        return level_map

    def build_placement_from_shuffle(
        self,
        power_shuffle: list[tuple[str, str, str]] | None = None,
        frag_shuffle: list[tuple[str, str, str]] | None = None,
    ) -> dict[str, dict[str, str]]:
        """
        Build a solver placement dict from randomizer shuffle results.

        power_shuffle: list of (level, original_power_name, new_power_name)
            e.g. [("w3", "power_water", "power_fire"), ("e2", "power_fire", "power_water"), ...]
        frag_shuffle: list of (level, original_frag_name, new_frag_name)

        Returns placement dict: {node_id: {original_item: replacement_item, ...}}
        """
        level_to_nodes = self.get_level_to_nodes()
        placement: dict[str, dict[str, str]] = {}

        for shuffle_list in [power_shuffle, frag_shuffle]:
            if not shuffle_list:
                continue
            for level, orig_name, new_name in shuffle_list:
                # Find which node(s) in this level contain the original item
                node_ids = level_to_nodes.get(level, [])
                for node_id in node_ids:
                    vanilla = self.nodes[node_id].get("pickups", [])
                    if orig_name in vanilla:
                        if node_id not in placement:
                            placement[node_id] = {}
                        placement[node_id][orig_name] = new_name

        return placement

    def check_power_placement(
        self,
        power_shuffle: list[tuple[str, str, str]],
        verbose: bool = False,
    ) -> bool:
        """
        Quick check: is this power-up placement solvable?
        power_shuffle: [(level, original_power, new_power), ...]
        """
        placement = self.build_placement_from_shuffle(power_shuffle=power_shuffle)
        ok, _ = self.solve(placement, verbose=verbose)
        return ok


def solve_vanilla(verbose: bool = True) -> bool:
    """Quick test: is the vanilla game solvable?"""
    s = Solver()
    ok, state = s.solve(verbose=verbose)
    return ok


def solve_with_placement(placement_file: str, verbose: bool = True) -> bool:
    """Solve with a placement from a JSON file."""
    with open(placement_file) as f:
        placement = json.load(f)
    s = Solver()
    ok, state = s.solve(placement, verbose=verbose)
    if verbose:
        print(f"\nSteps taken ({len(state.steps)}):")
        for step in state.steps:
            print(f"  {step}")
    return ok


def list_groups():
    """Show all randomizer groups with status."""
    s = Solver()
    groups = s.get_groups_with_status()
    print("=== Randomizer Groups ===\n")
    for gname, gdata in groups.items():
        status = gdata["status"].upper()
        marker = "OK" if status == "STABLE" else "!!"
        items = gdata["items"]
        print(f"[{marker}] {gname} ({status}) — {len(items)} items")
        print(f"    {gdata['description']}")
        if items:
            # Show items in columns
            for i in range(0, len(items), 4):
                chunk = items[i:i+4]
                print(f"      {', '.join(chunk)}")
        print()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--placement":
        if len(sys.argv) < 3:
            print("Usage: python solver.py --placement <placement.json>")
            sys.exit(1)
        ok = solve_with_placement(sys.argv[2])
    elif len(sys.argv) > 1 and sys.argv[1] == "--groups":
        list_groups()
        sys.exit(0)
    elif len(sys.argv) > 1 and sys.argv[1] == "--forward-fill":
        import random
        seed = int(sys.argv[2]) if len(sys.argv) > 2 else 42
        s = Solver()
        rng = random.Random(seed)
        placement, log = s.forward_fill(rng=rng, verbose=True)
        ok, state = s.solve(placement)
        print(f"\nSeed {seed}: {'SOLVABLE' if ok else 'UNSOLVABLE'}")
        sys.exit(0 if ok else 1)
    else:
        print("=== Vanilla Game Solvability Check ===")
        ok = solve_vanilla()

    sys.exit(0 if ok else 1)
