"""Player physics patch pack — gravity and player-speed sliders.

Phase 1: gravity slider.  The game integrates gravity in `FUN_00085700`
via `v_z' = v_z - g * dt`, and every call site passes the .rdata float
at VA 0x1980A8 (baseline 9.8 m/s^2).  Overwriting that single float
scales world gravity.  It affects enemies and projectiles too since it
is one global constant — documented in docs and the GUI tooltip.

Phase 2: walk / run speed sliders.  The player's `walkSpeed` and
`runSpeed` live in the `attacks_transitions` keyed-table section of
config.xbr (section offset 0x8000), with garret4 as column 25.  The
initial plan assumed characters.xbr; direct byte inspection showed
`walkSpeed`/`runSpeed` strings only occur in config.xbr, so Phase 2
edits those cells in config.xbr before repack without touching the
XBE.  Both sliders are modelled as virtual ParametricPatch entries so
the GUI renders the same slider widget.
"""

from __future__ import annotations

import struct

from azurik_mod.patching import (
    ParametricPatch,
    apply_parametric_patch,
    read_parametric_value,
)
from azurik_mod.patching.registry import PatchPack, register_pack

# ---------------------------------------------------------------------------
# Phase 1 — gravity
# ---------------------------------------------------------------------------

GRAVITY_BASELINE = 9.8
"""Baseline world-gravity value in m/s^2 — the .rdata float at VA 0x1980A8."""

GRAVITY_PATCH = ParametricPatch(
    name="gravity",
    label="World gravity",
    va=0x001980A8,
    size=4,
    original=struct.pack("<f", GRAVITY_BASELINE),
    default=GRAVITY_BASELINE,
    slider_min=0.98,    # 0.1x — moon
    slider_max=29.4,    # 3.0x — heavy
    slider_step=0.1,
    unit="m/s^2",
    encode=lambda g: struct.pack("<f", float(g)),
    decode=lambda b: struct.unpack("<f", b)[0],
)


# ---------------------------------------------------------------------------
# Phase 2 — player speed (virtual sliders; consumed by apply_player_speed)
# ---------------------------------------------------------------------------
#
# These two patches are "virtual" — they have va=0 / size=0 so the generic
# apply/verify helpers no-op.  The real mutation happens in
# `apply_player_speed(config_xbr_bytes, walk_scale, run_scale)` from
# the randomize-full pipeline.  Exposing them as ParametricPatch keeps
# the GUI slider code uniform (one widget class, one registry surface).

WALK_SPEED_SCALE = ParametricPatch(
    name="walk_speed_scale",
    label="Player walk speed",
    va=0,
    size=0,
    original=b"",
    default=1.0,
    slider_min=0.25,
    slider_max=3.0,
    slider_step=0.05,
    unit="x",
    encode=lambda v: struct.pack("<d", float(v)),
    decode=lambda b: struct.unpack("<d", b)[0],
)

RUN_SPEED_SCALE = ParametricPatch(
    name="run_speed_scale",
    label="Player run speed",
    va=0,
    size=0,
    original=b"",
    default=1.0,
    slider_min=0.25,
    slider_max=3.0,
    slider_step=0.05,
    unit="x",
    encode=lambda v: struct.pack("<d", float(v)),
    decode=lambda b: struct.unpack("<d", b)[0],
)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def apply_player_physics(
    xbe_data: bytearray,
    *,
    gravity: float | None = None,
    **_ignored,
) -> None:
    """Apply the XBE-side portion of the player physics pack.

    Speed sliders (walk_speed_scale / run_speed_scale) do NOT touch the
    XBE — they are consumed by `apply_player_speed` against
    characters.xbr in the randomize-full pipeline.  We silently accept
    and ignore them here via **_ignored so both call sites share one
    dispatch surface.
    """
    if gravity is not None:
        apply_parametric_patch(xbe_data, GRAVITY_PATCH, float(gravity))


ATTACKS_TRANSITIONS_OFFSET = 0x008000
"""File offset of the `attacks_transitions` keyed-table section in
config.xbr.  Mirrors the entry in `keyed_tables.KEYED_SECTIONS`.
This section owns garret4's walkSpeed, walkAnimSpeed, runSpeed,
runAnimSpeed rows (among others)."""


def apply_player_speed(
    config_xbr_data: bytearray,
    *,
    walk_scale: float = 1.0,
    run_scale: float = 1.0,
) -> bool:
    """Scale garret4's walkSpeed / runSpeed cells in config.xbr in place.

    Phase-2 discovery showed these doubles live in config.xbr's
    `attacks_transitions` section, not in characters.xbr as the plan
    originally assumed.  We reuse the existing KeyedTable parser.

    Returns True if anything was written, False if both scales are 1.0
    (no-op) or if the section could not be parsed / garret4 not found.

    Scaling is applied relative to whatever value is CURRENTLY in the
    buffer.  Callers that want idempotency should always pass the
    original extracted bytes (e.g. the randomize-full pipeline
    re-extracts config.xbr every run, so this is the natural flow).
    """
    if walk_scale == 1.0 and run_scale == 1.0:
        return False

    # Late import so azurik_mod.patches can be imported in environments
    # where keyed_tables' optional deps (path / io tools) aren't wired.
    from azurik_mod.config.keyed_tables import (
        load_table_from_bytes,
        set_cell_double,
    )

    try:
        table = load_table_from_bytes(
            bytes(config_xbr_data),
            ATTACKS_TRANSITIONS_OFFSET,
            "attacks_transitions",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  WARNING: player_physics — could not parse "
              f"attacks_transitions: {exc}")
        return False

    wrote = False

    if walk_scale != 1.0:
        walk = table.get_value("garret4", "walkSpeed")
        if walk and walk[0] == "double":
            _, base, cell_off = walk
            new_walk = base * walk_scale
            set_cell_double(config_xbr_data, cell_off, new_walk)
            print(f"  Player walkSpeed: {base:.3f} -> {new_walk:.3f}  "
                  f"(x{walk_scale})")
            wrote = True
        else:
            print("  WARNING: walkSpeed cell not found for garret4 "
                  "(or not double)")

    if run_scale != 1.0:
        run = table.get_value("garret4", "runSpeed")
        if run and run[0] == "double":
            _, base, cell_off = run
            new_run = base * run_scale
            set_cell_double(config_xbr_data, cell_off, new_run)
            print(f"  Player runSpeed:  {base:.3f} -> {new_run:.3f}  "
                  f"(x{run_scale})")
            wrote = True
        else:
            print("  WARNING: runSpeed cell not found for garret4 "
                  "(or not double)")

    return wrote


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PLAYER_PHYSICS_SITES = [
    GRAVITY_PATCH,
    WALK_SPEED_SCALE,
    RUN_SPEED_SCALE,
]


def _apply_defaults(xbe_data: bytearray) -> None:
    """Default pack apply — noop at baseline.  The real work happens via
    apply_player_physics(..., gravity=...) / apply_player_speed(...)
    from the randomize-full pipeline."""


register_pack(PatchPack(
    name="player_physics",
    description=(
        "Adjust player movement and world gravity with the sliders "
        "below.  Gravity is world-wide — it also affects enemies and "
        "projectiles.  Walk and run speed affect only the player."
    ),
    sites=PLAYER_PHYSICS_SITES,
    apply=_apply_defaults,
    default_on=False,
    included_in_randomizer_qol=False,
    tags=("player", "physics"),
))


__all__ = [
    "GRAVITY_BASELINE",
    "GRAVITY_PATCH",
    "PLAYER_PHYSICS_SITES",
    "RUN_SPEED_SCALE",
    "WALK_SPEED_SCALE",
    "apply_player_physics",
    "apply_player_speed",
]
