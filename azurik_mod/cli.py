"""Command-line interface for azurik-mod.

Argparse wiring only — every subcommand handler lives in
`azurik_mod.randomizer.commands`.
"""

from __future__ import annotations

import argparse
import sys

from azurik_mod.randomizer.commands import (
    cmd_apply_physics,
    cmd_diff,
    cmd_dump,
    cmd_list,
    cmd_mod_template,
    cmd_patch,
    cmd_randomize,
    cmd_randomize_full,
    cmd_randomize_gems,
    cmd_verify_patches,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Azurik mod tool — patch game values and build xemu-ready ISOs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Primary workflow:\n"
            "  %(prog)s patch --iso Azurik.iso --mod mod.json -o Azurik_modded.iso\n"
            "\n"
            "Full randomizer (gems + fragments + powers):\n"
            "  %(prog)s randomize --iso Azurik.iso --seed 42 -o Azurik_rand.iso\n"
            "  %(prog)s randomize --iso Azurik.iso --seed 42 --no-gems -o powers_only.iso\n"
            "\n"
            "Gem-only randomizer (legacy):\n"
            "  %(prog)s randomize-gems --iso Azurik.iso --seed 42 -o Azurik_rand.iso\n"
            "\n"
            "Browse values:\n"
            "  %(prog)s list  --sections\n"
            "  %(prog)s list  --entities critters_walking\n"
            "  %(prog)s dump  --iso Azurik.iso -s settings_foo -e air\n"
            "  %(prog)s dump  --input config.xbr -s critters_walking -e garret4\n"
            "  %(prog)s diff  --iso Azurik.iso --mod mod.json\n"
        ),
    )

    sub = parser.add_subparsers(dest="command")

    # patch (primary)
    p_patch = sub.add_parser(
        "patch",
        help="Apply mod(s) to a game ISO, producing a patched ISO for xemu",
    )
    p_patch.add_argument("--iso", required=True, help="Original game .iso")
    p_patch.add_argument("--mod", "-m", action="append", required=True,
                         help="Mod JSON file (repeat for multiple mods)")
    p_patch.add_argument("--output", "-o", required=True, help="Output .iso path")

    # list
    p_list = sub.add_parser("list", help="List sections or entities in the registry")
    p_list.add_argument("--sections", action="store_true")
    p_list.add_argument("--entities", metavar="SECTION")

    # dump
    p_dump = sub.add_parser("dump", help="Show current values from a game ISO or config.xbr")
    source = p_dump.add_mutually_exclusive_group(required=True)
    source.add_argument("--iso", help="Read config.xbr from a game ISO")
    source.add_argument("--input", "-i", help="Read a raw config.xbr file directly")
    p_dump.add_argument("--section", "-s", required=True)
    p_dump.add_argument("--entity", "-e")

    # diff
    p_diff = sub.add_parser("diff", help="Preview what a mod would change")
    source2 = p_diff.add_mutually_exclusive_group(required=True)
    source2.add_argument("--iso", help="Read config.xbr from a game ISO")
    source2.add_argument("--input", "-i", help="Read a raw config.xbr file directly")
    p_diff.add_argument("--mod", "-m", action="append", required=True)

    # mod-template — one-shot "give me an editable JSON of vanilla
    # values I can tweak" workflow.  Replaces the old examples/
    # folder which shipped stale-on-arrival sample mods.
    p_tmpl = sub.add_parser(
        "mod-template",
        help="Emit an editable mod-JSON populated with vanilla defaults",
        description=(
            "Produce a self-contained mod JSON, populated with the\n"
            "CURRENT vanilla values for one or more entities, ready\n"
            "for the user to edit values and feed back through\n"
            "``azurik-mod patch`` or ``--config-mod`` on\n"
            "``randomize-full``.\n"
            "\n"
            "This replaces the old ``examples/`` folder — those files\n"
            "drifted out of sync with reality.  ``mod-template`` reads\n"
            "the vanilla values live from your ISO, so the output is\n"
            "always truthful.\n"
            "\n"
            "Workflows:\n"
            "  # One entity (common):\n"
            "  %(prog)s mod-template --iso Azurik.iso \\\n"
            "      --section critters_walking --entity goblin \\\n"
            "      -o goblin_template.json\n"
            "\n"
            "  # Whole section:\n"
            "  %(prog)s mod-template --iso Azurik.iso \\\n"
            "      --section critters_walking -o all_walkers.json\n"
            "\n"
            "  # Multiple sections in one file:\n"
            "  %(prog)s mod-template --iso Azurik.iso \\\n"
            "      --section critters_walking --section damage \\\n"
            "      -o big_template.json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    tmpl_source = p_tmpl.add_mutually_exclusive_group(required=True)
    tmpl_source.add_argument("--iso", help="Read config.xbr from a game ISO")
    tmpl_source.add_argument("--input", "-i",
                              help="Read a raw config.xbr file directly")
    p_tmpl.add_argument("--section", "-s", action="append", required=True,
                         help="Section name (repeat for multiple sections)")
    p_tmpl.add_argument("--entity", "-e",
                         help="Limit to a single entity within the section(s)")
    p_tmpl.add_argument("--output", "-o",
                         help="Output JSON path (default: stdout)")
    p_tmpl.add_argument("--name", default="my-mod",
                         help="Mod name to embed in the JSON (default: my-mod)")

    # randomize-gems (legacy, gems only)
    p_rand = sub.add_parser(
        "randomize-gems",
        help="Randomize gem types across all levels and build a new ISO",
    )
    p_rand.add_argument("--iso", required=True, help="Original game .iso")
    p_rand.add_argument("--seed", "-s", type=int, default=42,
                        help="Random seed for reproducibility (default: 42)")
    p_rand.add_argument("--output", "-o", required=True, help="Output .iso path")
    p_rand.add_argument("--levels", "-l", nargs="+", metavar="LEVEL",
                        help="Only randomize these levels (e.g. a3 w2 f1). "
                             "Default: all playable levels")

    # randomize (unified: gems + fragments + powers)
    p_rall = sub.add_parser(
        "randomize",
        help="Randomize gems, fragments, and power-ups across all levels",
        description=(
            "Unified collectible randomizer. Shuffles gem types per-level,\n"
            "fragment names cross-level, and power-up elements cross-level.\n"
            "Use --no-gems, --no-fragments, --no-powers to disable categories."
        ),
    )
    p_rall.add_argument("--iso", required=True, help="Original game .iso")
    p_rall.add_argument("--seed", "-s", type=int, default=42,
                        help="Random seed for reproducibility (default: 42)")
    p_rall.add_argument("--output", "-o", required=True, help="Output .iso path")
    p_rall.add_argument("--levels", "-l", nargs="+", metavar="LEVEL",
                        help="Only process these levels (default: all playable)")
    p_rall.add_argument("--no-gems", action="store_true",
                        help="Skip gem randomization")
    p_rall.add_argument("--no-fragments", action="store_true",
                        help="Skip fragment randomization")
    p_rall.add_argument("--no-powers", action="store_true",
                        help="Skip power-up randomization")

    # randomize-full (full game randomizer)
    p_full = sub.add_parser(
        "randomize-full",
        help="Full game randomizer: major items, keys, gems, barriers + optional patches",
        description=(
            "Full game randomizer with 5 shuffle pools:\n"
            "  1. Major items: fragments + powers + town powers + obsidians (cross-level)\n"
            "  2. Keys: shuffled within elemental realm\n"
            "  3. Gems: diamond/emerald/sapphire/ruby shuffled per-level\n"
            "  4. Barriers: element vulnerability randomized per-level\n"
            "  5. Connections: level transition destinations shuffled\n"
            "\n"
            "Use --no-major / --no-keys / --no-gems / --no-barriers / --no-connections\n"
            "to skip individual shuffle pools.\n"
            "\n"
            "Patches are OFF by default; opt in explicitly:\n"
            "  --gem-popups    Hide the first-time \"Collect 100 <gem>\" popups.\n"
            "  --other-popups  Hide the tutorial / key / health / power-up popups.\n"
            "  --pickup-anims  Skip item pickup celebration animation.\n"
            "  --skip-logo     Skip the unskippable Adrenium logo boot movie.\n"
            "  --fps-unlock    Run the game at 60 FPS (experimental).\n"
            "  --gravity N     World gravity in m/s^2 (default 9.8, range 0.98-29.4).\n"
            "  --player-walk-scale N / --player-run-scale N   Player speed multipliers."
        ),
    )
    p_full.add_argument("--iso", required=True, help="Original game .iso")
    p_full.add_argument("--seed", "-s", type=int, default=42,
                        help="Random seed for reproducibility (default: 42)")
    p_full.add_argument("--output", "-o", required=True, help="Output .iso path")
    p_full.add_argument("--no-major", action="store_true",
                        help="Skip major item randomization (fragments/powers/obsidians)")
    p_full.add_argument("--no-keys", action="store_true", help="Skip key randomization")
    p_full.add_argument("--no-gems", action="store_true", help="Skip gem randomization")
    p_full.add_argument("--no-barriers", action="store_true", help="Skip barrier randomization")
    p_full.add_argument("--hard-barriers", action="store_true",
                        help="Include multi-element combo fourccs (stem/acid/ice/litn) in barrier pool")
    p_full.add_argument("--no-connections", action="store_true",
                        help="Skip level connection randomization")
    # Individual QoL patches are opt-in.  The old grouped --no-qol and
    # inverse --no-gem-popups / --no-pickup-anim flags are still accepted
    # for back-compat (store_true, hidden from --help) but do nothing when
    # the opt-in flags are absent because defaults are already off.
    p_full.add_argument("--gem-popups", action="store_true",
                        help="Hide the first-time \"Collect 100 <gem>\" popup "
                             "for diamonds / emeralds / rubies / sapphires / "
                             "obsidians.")
    p_full.add_argument("--other-popups", action="store_true",
                        help="Hide the first-time tutorial, key, health, "
                             "power-up, and six-keys-collected popups.  "
                             "Leaves the death-screen popup alone.")
    p_full.add_argument("--pickup-anims", action="store_true",
                        help="Skip the short celebration animation after "
                             "picking up items.")
    p_full.add_argument("--skip-logo", action="store_true",
                        help="Skip the unskippable Adrenium logo movie that "
                             "plays when the game first boots.")
    p_full.add_argument("--no-qol", action="store_true",
                        help=argparse.SUPPRESS)  # deprecated alias; no-op
    p_full.add_argument("--no-gem-popups", action="store_true",
                        help=argparse.SUPPRESS)  # deprecated alias; no-op
    p_full.add_argument("--no-other-popups", action="store_true",
                        help=argparse.SUPPRESS)  # deprecated alias; no-op
    p_full.add_argument("--no-pickup-anim", action="store_true",
                        help=argparse.SUPPRESS)  # deprecated alias; no-op
    p_full.add_argument("--no-skip-logo", action="store_true",
                        help=argparse.SUPPRESS)  # deprecated alias; no-op
    p_full.add_argument("--obsidian-cost", type=int, metavar="N",
                        help="Obsidian cost per temple lock (default: 10 = locks at 10,20,...100)")
    p_full.add_argument("--item-pool",
                        help='Custom item pool as JSON (inline or file path). '
                             'Format: {"power_water": 5, "frag_air_1": 2, ...}. '
                             'Overrides the default item counts for the solver.')
    p_full.add_argument("--force", action="store_true",
                        help="Build the ISO even if no solvable placement is found")
    p_full.add_argument("--player-character",
                        help="Play as another character (e.g. evil_noreht, "
                             "overlord, flicken).  Experimental — animations "
                             "may break.  Max 11 characters.")
    p_full.add_argument("--fps-unlock", action="store_true",
                        help="Run the game at 60 FPS instead of 30.  "
                             "Experimental.")
    p_full.add_argument("--config-mod",
                        help="Apply a config mod JSON (inline or file path) "
                             "that tweaks per-entity values (damage, speed, "
                             "hit points, etc.).")
    p_full.add_argument("--gravity", type=float, metavar="M_PER_S2",
                        help="World gravity in m/s^2 (default 9.8, range "
                             "0.98-29.4).  Affects enemies and projectiles "
                             "too — it's one global value.")
    p_full.add_argument("--player-walk-scale", type=float, metavar="X",
                        help="Player walk-speed multiplier (default 1.0, "
                             "range 0.25-3.0).")
    p_full.add_argument("--player-run-scale", type=float, metavar="X",
                        help="Player run-speed multiplier (default 1.0, "
                             "range 0.25-3.0).")

    # apply-physics (standalone physics slider runner)
    p_physics = sub.add_parser(
        "apply-physics",
        help="Apply gravity / player-speed sliders to an ISO or raw files",
        description=(
            "Apply the player_physics pack.\n"
            "\n"
            "Gravity rewrites the world gravity float in default.xbe\n"
            "(affects enemies too).  Walk / run speed edit only the\n"
            "player's cells in characters.xbr.\n"
            "\n"
            "Either pass --iso (unpack, patch, repack) or the raw files\n"
            "--xbe (for gravity) and --config (for walk/run speed)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_physics.add_argument("--iso",
        help="ISO to unpack, patch, and repack (pair with --output)")
    p_physics.add_argument("--output", "-o",
        help="Output ISO path (defaults to overwriting --iso)")
    p_physics.add_argument("--xbe",
        help="Patch this raw default.xbe in place (for --gravity)")
    p_physics.add_argument("--config",
        help="Patch this raw config.xbr in place (for --walk-speed/--run-speed)")
    p_physics.add_argument("--gravity", type=float, metavar="M_PER_S2",
        help="World gravity (default 9.8 m/s^2; range 0.98-29.4).")
    p_physics.add_argument("--walk-speed", type=float, metavar="X",
        help="Player walk speed multiplier (default 1.0; range 0.25-3.0).")
    p_physics.add_argument("--run-speed", type=float, metavar="X",
        help="Player run speed multiplier (default 1.0; range 0.25-3.0).")

    # save (inspect / introspect save directories)
    p_save = sub.add_parser(
        "save",
        help="Inspect Azurik save directories (SaveMeta.xbx + .sav files)",
        description=(
            "Read-only introspection of an exported Azurik save\n"
            "directory (the folder that xemu's HDD-export gives you\n"
            "for a single save slot).\n"
            "\n"
            "Accepts a directory path containing any mix of\n"
            "SaveMeta.xbx / SaveImage.xbx / TitleMeta.xbx /\n"
            "TitleImage.xbx / signature.sav / <level>.sav files.\n"
            "Missing files are skipped cleanly.\n"
            "\n"
            "Output is either a human-readable summary (default) or\n"
            "structured JSON (--json) for downstream tooling.\n"
            "\n"
            "For on-disk format details + how to extract saves from\n"
            "an xemu qcow2 image, see docs/SAVE_FORMAT.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_save_sub = p_save.add_subparsers(dest="save_command")
    p_save_inspect = p_save_sub.add_parser(
        "inspect",
        help="Summarise every recognised file in a save directory")
    p_save_inspect.add_argument(
        "path", help="Path to an exported save directory or a single .sav file")
    p_save_inspect.add_argument(
        "--json", action="store_true",
        help="Emit machine-readable JSON instead of a human summary")

    # verify-patches (post-build sanity check)
    p_verify = sub.add_parser(
        "verify-patches",
        help="Verify 60 FPS patches are correctly applied to a built ISO/XBE",
        description=(
            "Reads a patched default.xbe (extracted from an ISO or passed as a\n"
            "raw file) and reports which FPS_PATCH_SITES are applied, still at\n"
            "original bytes, or corrupted.  Pins safety-critical patches (e.g.\n"
            "the 60fps step cap of 2) and optionally whitelist-diffs against an\n"
            "unpatched original to confirm no stray bytes were modified.\n"
            "\n"
            "Exit code is non-zero on any mismatch or safety failure, so this\n"
            "command is safe to use in CI."
        ),
    )
    verify_source = p_verify.add_mutually_exclusive_group(required=True)
    verify_source.add_argument("--iso",
        help="Patched .iso (default.xbe is extracted via xdvdfs)")
    verify_source.add_argument("--xbe",
        help="Patched default.xbe file directly")
    p_verify.add_argument("--original",
        help="Unpatched .iso or .xbe to whitelist-diff against")
    p_verify.add_argument("--strict", action="store_true",
        help="Treat unexpected whitelist-diff changes as a failure (non-zero exit)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "list": cmd_list,
        "dump": cmd_dump,
        "diff": cmd_diff,
        "patch": cmd_patch,
        "mod-template": cmd_mod_template,
        "randomize-gems": cmd_randomize_gems,
        "randomize": cmd_randomize,
        "randomize-full": cmd_randomize_full,
        "apply-physics": cmd_apply_physics,
        "verify-patches": cmd_verify_patches,
        "save": _dispatch_save,
    }
    dispatch[args.command](args)


def _dispatch_save(args) -> None:
    """Dispatch the ``save`` subcommand to its implementation."""
    from azurik_mod.save_format.commands import cmd_save_inspect
    if args.save_command in (None, "inspect"):
        cmd_save_inspect(args)
    else:
        raise SystemExit(
            f"unknown save subcommand: {args.save_command!r}.  "
            f"Try `azurik-mod save inspect --help`.")


__all__ = ["main"]


if __name__ == "__main__":
    main()
