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

    # iso-verify (manifest integrity + level graph report)
    p_iso = sub.add_parser(
        "iso-verify",
        help=("Validate an unpacked ISO against the game's own "
              "prefetch-lists.txt + filelist.txt manifests"),
        description=(
            "Azurik's ISO ships with two plain-text index files:\n"
            "  prefetch-lists.txt  — the streaming loader's level manifest\n"
            "  filelist.txt        — md5 + size for every file\n"
            "This command reads both and reports:\n"
            "  * any size or MD5 mismatches (by default — skip with --no-md5)\n"
            "  * the level adjacency graph (handy for randomizer sanity)\n"
            "  * files on disk that aren't in either manifest\n"
            "\n"
            "Exit code is non-zero when integrity mismatches are found."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_iso.add_argument("iso_root",
        help="Path to an UNPACKED ISO directory (must contain "
             "prefetch-lists.txt, filelist.txt and gamedata/)")
    p_iso.add_argument("--no-md5", action="store_true",
        help="Skip MD5 verification (size-only, ~50x faster on SSD)")
    p_iso.add_argument("--graph", action="store_true",
        help="Print the level adjacency graph after the integrity report")
    p_iso.add_argument("--limit", type=int, default=None,
        help="Stop after N integrity issues (default: report all)")

    # ------------------------------------------------------------------
    # xbe (reverse-engineering swiss-army knife)
    # See docs/TOOLING_ROADMAP.md for rationale.
    # ------------------------------------------------------------------
    p_xbe = sub.add_parser(
        "xbe",
        help="Inspect default.xbe — address arithmetic, hexdump, "
             "reference finder, float / string scanners",
        description=(
            "Thin CLI around azurik_mod.xbe_tools.xbe_scan.  Every\n"
            "verb accepts --iso PATH.iso (extracts via xdvdfs) or\n"
            "--xbe PATH.xbe (raw file).  Add --json for machine-\n"
            "readable output.\n\n"
            "Common recipes:\n"
            "  xbe addr 0x85700                       VA → file offset\n"
            "  xbe addr 0x75700 --from file           file → VA\n"
            "  xbe hexdump 0x19C1AC --length 64       byte context\n"
            "  xbe find-refs --va 0x19C1AC            who pushes this VA?\n"
            "  xbe find-refs --string fx_magic_timer  locate + find-refs in one go\n"
            "  xbe find-floats 9.7 9.9                gravity constants\n"
            "  xbe strings 'levels/water'             grep strings .rdata\n"
            "  xbe sections                           dump section table\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _xbe_sub = p_xbe.add_subparsers(dest="xbe_command", required=True)

    def _add_source_args(p):
        p.add_argument("--iso", help="Path to Azurik ISO (extracts XBE)")
        p.add_argument("--xbe", help="Path to raw default.xbe")
        p.add_argument("--json", action="store_true",
            help="Emit JSON instead of human-readable text")

    p_addr = _xbe_sub.add_parser("addr",
        help="Resolve a number to (VA, file offset, section)")
    _add_source_args(p_addr)
    p_addr.add_argument("value",
        help="Hex (0x...) or decimal number to resolve")
    p_addr.add_argument("--from", dest="from_",
        choices=("auto", "va", "file"), default="auto",
        help="Force treating the input as a VA or file offset "
             "(default: auto — guess from magnitude)")

    p_hex = _xbe_sub.add_parser("hexdump",
        help="Hexdump bytes starting at a VA or file offset")
    _add_source_args(p_hex)
    p_hex.add_argument("address",
        help="VA (default) or file offset (with --file) to start at")
    p_hex.add_argument("--length", type=int, default=64,
        help="How many bytes to dump (default 64)")
    p_hex.add_argument("--file", action="store_true",
        help="Treat the address as a file offset instead of a VA")

    p_refs = _xbe_sub.add_parser("find-refs",
        help="Find .text instructions that push a VA as imm32")
    _add_source_args(p_refs)
    p_refs.add_argument("--va",
        help="Target VA to search for (hex 0x... or decimal)")
    p_refs.add_argument("--string",
        help="Locate this string's VA first, then find refs to it")

    p_flt = _xbe_sub.add_parser("find-floats",
        help="Find float/double constants in .rdata in a value range")
    _add_source_args(p_flt)
    p_flt.add_argument("min", help="Lower bound (inclusive)")
    p_flt.add_argument("max", help="Upper bound (inclusive)")
    p_flt.add_argument("--width", choices=("float", "double", "both"),
        default="both",
        help="Restrict to float32 / float64 / both (default both)")

    p_str = _xbe_sub.add_parser("strings",
        help="Find printable ASCII strings in .rdata / .data")
    _add_source_args(p_str)
    p_str.add_argument("pattern",
        help="Substring (default) or regex (with --regex)")
    p_str.add_argument("--regex", action="store_true",
        help="Treat PATTERN as a Python regex")
    p_str.add_argument("--min-len", type=int, default=4,
        help="Ignore matches shorter than this (default 4)")
    p_str.add_argument("--limit", type=int, default=200,
        help="Cap results at N (default 200)")

    p_sec = _xbe_sub.add_parser("sections",
        help="Dump the XBE section table")
    _add_source_args(p_sec)

    # ------------------------------------------------------------------
    # ghidra-coverage — knowledge-vs-labeled gap report
    # ------------------------------------------------------------------
    p_gc = sub.add_parser(
        "ghidra-coverage",
        help="Audit what we know vs what Ghidra labels",
        description=(
            "Cross-references our azurik.h VA anchors + vanilla_symbols\n"
            "+ patch-site registry against an optional Ghidra snapshot\n"
            "JSON.  Lists VAs we document but Ghidra still shows FUN_*\n"
            "for (prime sync candidates) + Ghidra labels the Python side\n"
            "doesn't track yet (promotion candidates).\n\n"
            "Runs Python-side-only when no snapshot is given; pass\n"
            "--snapshot PATH.json to activate the full diff."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_gc.add_argument("--snapshot",
        help="Path to a Ghidra snapshot JSON "
             "(schema documented in azurik_mod/xbe_tools/ghidra_coverage.py)")
    p_gc.add_argument("--live", action="store_true",
        help="Pull from a running Ghidra instance over HTTP "
             "instead of a snapshot file")
    p_gc.add_argument("--host", default=None,
        help="Ghidra host (default localhost; only used with --live)")
    p_gc.add_argument("--port", type=int, default=None,
        help="Ghidra port (default 8193; only used with --live)")
    p_gc.add_argument("--json", action="store_true",
        help="Emit JSON instead of the human-readable report")

    # ------------------------------------------------------------------
    # ghidra-sync — push Python-side knowledge to a live Ghidra
    # ------------------------------------------------------------------
    p_gs = sub.add_parser(
        "ghidra-sync",
        help="Rename + annotate functions in a live Ghidra project "
             "based on our Python-side knowledge",
        description=(
            "Takes every named VA we track in Python (azurik.h\n"
            "anchors + vanilla_symbols + registered patch sites)\n"
            "and writes them back into the open Ghidra project as\n"
            "renamed functions + plate comments.\n\n"
            "Dry-run is the default.  Pass --apply to actually\n"
            "modify Ghidra; --force lets rename overwrite functions\n"
            "that already have a human-meaningful name (otherwise\n"
            "the tool skips them).\n\n"
            "Examples:\n"
            "  azurik-mod ghidra-sync               # dry-run plan\n"
            "  azurik-mod ghidra-sync --apply       # apply to :8193\n"
            "  azurik-mod ghidra-sync --apply --force --port 8193"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_gs.add_argument("--apply", action="store_true",
        help="Actually modify Ghidra state (default: dry-run only)")
    p_gs.add_argument("--force", action="store_true",
        help="Overwrite functions that already have a user-meaningful "
             "name (default: skip)")
    p_gs.add_argument("--host", default=None,
        help="Ghidra host (default localhost)")
    p_gs.add_argument("--port", type=int, default=None,
        help="Ghidra port (default 8193)")
    p_gs.add_argument("--json", action="store_true",
        help="Emit JSON plan instead of the human-readable report")

    # ------------------------------------------------------------------
    # shim-inspect — preview bytes a compiled shim .o will emit
    # ------------------------------------------------------------------
    p_si = sub.add_parser(
        "shim-inspect",
        help="Inspect a compiled shim object (bytes / relocations / symbols)",
        description=(
            "Parses a PE-COFF .o (as produced by the shim build\n"
            "pipeline) and reports section sizes, the symbol table,\n"
            "and every relocation.  Use this to verify a shim's\n"
            "trampoline fits the budget and that its external symbols\n"
            "match vanilla_symbols expectations BEFORE running a full\n"
            "build+patch cycle.\n\n"
            "Accepts either an explicit .o path or a feature folder\n"
            "(azurik_mod/patches/<name>/) — in the latter case the\n"
            "pack's ShimSource determines which .o to load."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_si.add_argument("target",
        help="Path to a .o file OR a feature folder")
    p_si.add_argument("--json", action="store_true",
        help="Emit JSON instead of the human-readable report")

    # ------------------------------------------------------------------
    # test-for-va — pytest narrowing by VA / pack name
    # ------------------------------------------------------------------
    p_tfv = sub.add_parser(
        "test-for-va",
        help="Find test classes that reference a VA or pack name",
        description=(
            "Scans tests/ for classes that mention a VA (hex) or a "
            "pack/feature name as a bareword, optionally launches "
            "pytest on just that subset.  Useful when iterating on "
            "one patch without paying for the full ~470-test suite."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_tfv.add_argument("target",
        help="Hex VA (0x85F62) OR a pack / feature name")
    p_tfv.add_argument("--run", action="store_true",
        help="Invoke pytest on the matches (default: just print them)")
    p_tfv.add_argument("--json", action="store_true",
        help="Machine-readable output")
    p_tfv.add_argument("--tests-dir",
        help="Override the tests/ directory (default: ./tests)")
    p_tfv.add_argument("pytest_args", nargs="*",
        help="Extra args forwarded to pytest when --run is set")

    # ------------------------------------------------------------------
    # plan-trampoline — size a hook site
    # ------------------------------------------------------------------
    p_pt = sub.add_parser(
        "plan-trampoline",
        help="Size a trampoline hook site (bytes to replace, asm context)",
        description=(
            "Given a hook-site VA, decode the instructions starting "
            "there, suggest the smallest byte count that fits the "
            "target trampoline budget + ends on an instruction "
            "boundary, and flag any multi-byte instructions the shim "
            "must preserve / restore.\n\n"
            "Exit codes:\n"
            "  0 — clean boundary; ready to hook\n"
            "  1 — decoder produced warnings; review before shipping"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_source_args(p_pt)
    p_pt.add_argument("va",
        help="Hex VA of the hook site (e.g. 0x5F6E5)")
    p_pt.add_argument("--budget", type=int, default=5,
        help="Target trampoline size in bytes (5 for CALL rel32, "
             "6 for FF25 thunk); default 5")
    p_pt.add_argument("--window", type=int, default=16,
        help="Bytes to decode forward from the VA; default 16")

    # ------------------------------------------------------------------
    # entity — diff subcommand
    # ------------------------------------------------------------------
    p_entity = sub.add_parser(
        "entity",
        help="Inspect config.xbr entities",
        description=("Tools operating on named entities in the "
                     "game's config.xbr keyed-table sections."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _entity_sub = p_entity.add_subparsers(
        dest="entity_command", required=True)
    p_ediff = _entity_sub.add_parser(
        "diff",
        help="Compare two entities property-by-property")
    p_ediff.add_argument("entity_a", help="First entity name")
    p_ediff.add_argument("entity_b", help="Second entity name")
    p_ediff.add_argument("--config",
        help="Path to a raw config.xbr file")
    p_ediff.add_argument("--iso",
        help="Extract config.xbr from this ISO (xdvdfs)")
    p_ediff.add_argument("--all", action="store_true",
        help="Include shared-equal rows in the output "
             "(default: show only differing rows)")
    p_ediff.add_argument("--json", action="store_true",
        help="Machine-readable output")

    # ------------------------------------------------------------------
    # xbr — inspect subcommand
    # ------------------------------------------------------------------
    p_xbr = sub.add_parser(
        "xbr",
        help="Inspect raw .xbr resource files",
        description=("Tools for poking at Azurik's binary resource "
                     "files (config.xbr + level XBRs)."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _xbr_sub = p_xbr.add_subparsers(
        dest="xbr_command", required=True)
    p_xbri = _xbr_sub.add_parser(
        "inspect",
        help="Classify the first N records of a TOC-tagged section")
    p_xbri.add_argument("path", help="Path to an .xbr file")
    p_xbri.add_argument("--tag", required=True,
        help="4-char TOC tag (e.g. 'surf', 'node', 'rdms')")
    p_xbri.add_argument("--entries", type=int, default=3,
        help="How many records to inspect (default 3)")
    p_xbri.add_argument("--stride", type=int, default=None,
        help="Force a specific record stride (default: auto-detect)")
    p_xbri.add_argument("--fields-per-row", type=int, default=6,
        help="Cap on 4-byte columns per record (default 6)")
    p_xbri.add_argument("--json", action="store_true",
        help="Machine-readable output")

    p_xbrd = _xbr_sub.add_parser(
        "diff",
        help="Structural diff between two XBR files")
    p_xbrd.add_argument("path_a", help="First XBR")
    p_xbrd.add_argument("path_b", help="Second XBR")
    p_xbrd.add_argument("--min-len", type=int, default=6,
        help="Minimum ASCII run length for string-diff (default 6)")
    p_xbrd.add_argument("--max-strings", type=int, default=40,
        help="Cap on per-tag string changes shown (default 40)")
    p_xbrd.add_argument("--json", action="store_true")

    # ------------------------------------------------------------------
    # ghidra-snapshot (tier 3 #15)
    # ------------------------------------------------------------------
    p_gsn = sub.add_parser(
        "ghidra-snapshot",
        help="Dump Ghidra function + label state to a JSON "
             "snapshot (consumed by ghidra-coverage offline)",
        description=(
            "Pulls every function + symbol from a running Ghidra\n"
            "instance and writes a JSON file matching the schema\n"
            "azurik_mod.xbe_tools.ghidra_coverage.load_ghidra_snapshot\n"
            "expects.  Default-named Ghidra labels (FUN_* / LAB_*\n"
            "/ DAT_*) are filtered out to keep the snapshot size\n"
            "reasonable (~50 KB vs ~1.2 MB raw)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_gsn.add_argument("path",
        help="Output JSON file path")
    p_gsn.add_argument("--host", default=None)
    p_gsn.add_argument("--port", type=int, default=None,
        help="Ghidra port (default 8193)")
    p_gsn.add_argument("--keep-default-names", action="store_true",
        help="Include FUN_* / LAB_* / DAT_* rows (default: drop them)")
    p_gsn.add_argument("--no-labels", action="store_true",
        help="Skip the labels section (functions-only snapshot)")

    # ------------------------------------------------------------------
    # new-shim (tier 2 #6 — scaffolder with ABI pickup)
    # ------------------------------------------------------------------
    p_ns = sub.add_parser(
        "new-shim",
        help="Scaffold a new trampoline-backed feature folder",
        description=(
            "Replaces the legacy shell scaffolder "
            "(shims/toolchain/new_shim.sh) with a Python-testable\n"
            "version that can pull ABI info from a live Ghidra\n"
            "and pre-fill replaced_bytes from the vanilla XBE.\n\n"
            "With just a name: identical behaviour to new_shim.sh.\n"
            "With --hook + --xbe: runs plan-trampoline and fills\n"
            "  the replaced_bytes field in the generated __init__.py.\n"
            "With --hook + --xbe + --ghidra: also pulls the function's\n"
            "  calling convention from Ghidra and picks the correct\n"
            "  __attribute__(()) for the shim.c prototype.\n\n"
            "Examples:\n"
            "  azurik-mod new-shim skip_prophecy\n"
            "  azurik-mod new-shim skip_prophecy --hook 0x5F6E5 \\\n"
            "      --xbe /path/to/default.xbe\n"
            "  azurik-mod new-shim skip_prophecy --hook 0x5F6E5 \\\n"
            "      --iso Azurik.iso --ghidra --dry-run"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_ns.add_argument("name",
        help="Feature name (lowercase identifier: [a-z][a-z0-9_]*)")
    p_ns.add_argument("--hook",
        help="Hook-site VA (hex like 0x5F6E5 or decimal)")
    p_ns.add_argument("--xbe",
        help="Path to vanilla default.xbe for replaced_bytes pickup")
    p_ns.add_argument("--iso",
        help="Path to an ISO (extracted XBE used for replaced_bytes)")
    p_ns.add_argument("--ghidra", action="store_true",
        help="Fetch calling convention from the live Ghidra "
             "instance (implies --port 8193 when --port isn't set)")
    p_ns.add_argument("--host", default=None,
        help="Ghidra host (default localhost)")
    p_ns.add_argument("--port", type=int, default=None,
        help="Ghidra port (default 8193)")
    p_ns.add_argument("--category", default="experimental",
        help="Category the feature registers under "
             "(default 'experimental')")
    p_ns.add_argument("--dry-run", action="store_true",
        help="Print the rendered files without writing them")
    p_ns.add_argument("--emit-test", action="store_true",
        help="Also emit test_<name>.py pinning the scaffolded "
             "feature registration + trampoline constants (#19)")

    # ------------------------------------------------------------------
    # movies info (tier 3 #13)
    # ------------------------------------------------------------------
    p_movies = sub.add_parser(
        "movies",
        help="Inspect Bink movies",
        description="Tools for the game's `.bik` cutscene files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _movies_sub = p_movies.add_subparsers(
        dest="movies_command", required=True)
    p_mi = _movies_sub.add_parser(
        "info",
        help="Print Bink header metadata")
    p_mi.add_argument("path",
        help="Path to a .bik file OR a directory (aggregates)")
    p_mi.add_argument("--json", action="store_true")

    # ------------------------------------------------------------------
    # audio (tier 3 #14 — wave-blob bulk extractor)
    # ------------------------------------------------------------------
    p_audio = sub.add_parser(
        "audio",
        help="Extract audio blobs from fx.xbr (partial — format "
             "only partially decoded)",
        description=(
            "Bulk-extract every ``wave`` TOC entry from fx.xbr,\n"
            "classifying each as 'likely-audio' (high-entropy raw\n"
            "bytes) vs 'likely-animation' (Maya-particle-system\n"
            "curve data).  Produces one file per blob + a\n"
            "manifest.json.\n\n"
            "Full codec decoding is NOT implemented — the wave\n"
            "payload has no RIFF/XMA/etc. header.  Shipped so RE\n"
            "work can proceed on plain files; see the module\n"
            "docstring for format status."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _audio_sub = p_audio.add_subparsers(
        dest="audio_command", required=True)
    p_ad = _audio_sub.add_parser(
        "dump",
        help="Bulk-extract wave blobs + write a manifest")
    p_ad.add_argument("fx_xbr",
        help="Path to gamedata/fx.xbr")
    p_ad.add_argument("--output", "-o", required=True,
        help="Output directory (created if missing)")
    p_ad.add_argument("--entropy-min", type=float, default=0.0,
        help="Skip blobs with Shannon entropy below this (0..1). "
             "Higher = more-likely-audio; 0 writes everything.")
    p_ad.add_argument("--only-audio", action="store_true",
        help="Skip blobs classified as likely-animation / too-small")
    p_ad.add_argument("--preview", type=int, default=0,
        help="Show the first N manifest entries inline")
    p_ad.add_argument("--json", action="store_true")

    # ------------------------------------------------------------------
    # plugins (tier 3 #16 — third-party pack distribution)
    # ------------------------------------------------------------------
    p_plugins = sub.add_parser(
        "plugins",
        help="Inspect discovered third-party plugin packs",
        description=(
            "Third-party ``azurik-mod`` packs install themselves\n"
            "via the ``azurik_mod.patches`` entry-point group in\n"
            "pyproject.toml.  After ``pip install <pkg>`` the CLI\n"
            "picks them up automatically at startup.\n\n"
            "``plugins list`` shows every plugin the loader\n"
            "discovered.  See docs/PLUGINS.md for the authoring\n"
            "guide."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _plugins_sub = p_plugins.add_subparsers(
        dest="plugins_command", required=True)
    p_pl = _plugins_sub.add_parser(
        "list",
        help="List every discovered plugin + load status")
    p_pl.add_argument("--reload", action="store_true",
        help="Re-run the loader now (default: use the auto-loaded "
             "state)")
    p_pl.add_argument("--json", action="store_true")

    # ------------------------------------------------------------------
    # Next-wave tools (#17 – #26 from docs/TOOLING_ROADMAP.md)
    # ------------------------------------------------------------------

    # #21 xrefs — walk Ghidra's xref graph up to N hops
    p_xrefs = sub.add_parser(
        "xrefs",
        help="Walk Ghidra's xref graph around a VA (callers / callees)",
        description=(
            "Pulls xrefs from a live Ghidra instance and renders\n"
            "them as an ASCII tree (or JSON).  Collapses intra-\n"
            "function edges onto their enclosing function so the\n"
            "tree stays readable.\n\n"
            "Examples:\n"
            "  azurik-mod xrefs 0x85700\n"
            "  azurik-mod xrefs 0x85700 --direction out --depth 3\n"
            "  azurik-mod xrefs 0x85700 --json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_xrefs.add_argument("va",
        help="Virtual address (hex like 0x85700 or decimal)")
    p_xrefs.add_argument("--direction",
        choices=("in", "out"), default="in",
        help="'in' = callers of VA, 'out' = what VA calls")
    p_xrefs.add_argument("--depth", type=int, default=2,
        help="Maximum hops from the seed (default 2)")
    p_xrefs.add_argument("--max-nodes", type=int, default=200,
        help="Hard cap on total nodes to keep terminal output sane")
    p_xrefs.add_argument("--host", default=None)
    p_xrefs.add_argument("--port", type=int, default=None)
    p_xrefs.add_argument("--json", action="store_true")

    # #20 call-graph — Graphviz DOT / JSON out to N hops
    p_cg = sub.add_parser(
        "call-graph",
        help="Emit a Graphviz DOT call-graph from one or more seeds",
        description=(
            "BFS over Ghidra's xref graph, emitting a Graphviz\n"
            ".dot file that ``dot -Tpng`` can render.  Collapses\n"
            "intra-function CALLs onto their enclosing functions\n"
            "so the graph stays legible.\n\n"
            "Examples:\n"
            "  azurik-mod call-graph 0x85700 --depth 2 > g.dot\n"
            "  azurik-mod call-graph 0x85700 --direction reverse \\\n"
            "      --depth 3 --dot callers.dot\n"
            "  azurik-mod call-graph 0x85700 0x42000 --json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_cg.add_argument("seeds", nargs="+",
        help="One or more seed VAs (hex or decimal)")
    p_cg.add_argument("--direction",
        choices=("forward", "reverse"), default="forward",
        help="'forward' chases callees, 'reverse' chases callers")
    p_cg.add_argument("--depth", type=int, default=2)
    p_cg.add_argument("--max-edges", type=int, default=500,
        help="Cap the total edge count (default 500)")
    p_cg.add_argument("--dot", default=None,
        help="Write the DOT document to this path instead of stdout")
    p_cg.add_argument("--host", default=None)
    p_cg.add_argument("--port", type=int, default=None)
    p_cg.add_argument("--json", action="store_true")

    # #23 struct-diff — azurik.h vs live Ghidra
    p_sd = sub.add_parser(
        "struct-diff",
        help="Diff struct layouts in shims/include/azurik.h vs Ghidra",
        description=(
            "Surfaces three drift classes:\n"
            "  * header_only  — azurik.h declares it, Ghidra doesn't\n"
            "  * ghidra_only  — Ghidra has it, azurik.h doesn't\n"
            "  * size_mismatch / field_mismatch — present on both\n"
            "    sides but at least one side is stale.\n\n"
            "Use --offline to skip Ghidra (dry-run of the header\n"
            "parser).  --verbose prints per-field diffs on mismatch.\n\n"
            "Examples:\n"
            "  azurik-mod struct-diff\n"
            "  azurik-mod struct-diff --verbose\n"
            "  azurik-mod struct-diff --offline"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_sd.add_argument("--header", default=None,
        help="Override path to azurik.h")
    p_sd.add_argument("--offline", action="store_true",
        help="Skip Ghidra; dry-run the header parser only")
    p_sd.add_argument("--verbose", "-v", action="store_true",
        help="Show per-field diffs on mismatch")
    p_sd.add_argument("--host", default=None)
    p_sd.add_argument("--port", type=int, default=None)
    p_sd.add_argument("--json", action="store_true")

    # #22 decomp-cache — memoise GhidraClient.decompile on disk
    p_dc = sub.add_parser(
        "decomp-cache",
        help="Inspect / clear the on-disk decompilation cache",
        description=(
            "The cache lives under ~/.cache/azurik-mod/decomps\n"
            "(or $AZURIK_DECOMP_CACHE if set).  Verbs:\n"
            "  stats   — entries per program (no Ghidra required)\n"
            "  clear   — nuke the current Ghidra program's cache\n"
            "  get VA  — fetch one decomp via the cache"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _dc_sub = p_dc.add_subparsers(
        dest="cache_command", required=True)
    p_dcs = _dc_sub.add_parser("stats",
        help="Show on-disk cache footprint")
    p_dcs.add_argument("--root", default=None,
        help="Override cache root (default: XDG-respecting)")
    p_dcs.add_argument("--json", action="store_true")
    p_dcc = _dc_sub.add_parser("clear",
        help="Clear the cache for the current Ghidra program")
    p_dcc.add_argument("--root", default=None)
    p_dcc.add_argument("--host", default=None)
    p_dcc.add_argument("--port", type=int, default=None)
    p_dcg = _dc_sub.add_parser("get",
        help="Fetch one decomp via the cache")
    p_dcg.add_argument("va")
    p_dcg.add_argument("--root", default=None)
    p_dcg.add_argument("--host", default=None)
    p_dcg.add_argument("--port", type=int, default=None)
    p_dcg.add_argument("--json", action="store_true")

    # #25 assets fingerprint (+ fingerprint-diff)
    p_afp = sub.add_parser(
        "assets",
        help="Asset fingerprint + diff utilities (#25)",
        description=(
            "Content-addressed fingerprints of an ISO / unpacked\n"
            "tree / single file.  Commit the JSON beside your mod\n"
            "and diff it against a later build to see exactly\n"
            "which files changed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _afp_sub = p_afp.add_subparsers(
        dest="assets_command", required=True)
    p_afpi = _afp_sub.add_parser(
        "fingerprint",
        help="Emit a fingerprint JSON for a directory / file")
    p_afpi.add_argument("root",
        help="Directory or file to fingerprint")
    p_afpi.add_argument("--out", default=None,
        help="Write the JSON here (otherwise prints a summary)")
    p_afpi.add_argument("--include", action="append", default=None,
        help="Glob include filter (can repeat)")
    p_afpi.add_argument("--exclude", action="append", default=None,
        help="Glob exclude filter (can repeat)")
    p_afpi.add_argument("--json", action="store_true")
    p_afpd = _afp_sub.add_parser(
        "fingerprint-diff",
        help="Diff two fingerprint JSON files")
    p_afpd.add_argument("before")
    p_afpd.add_argument("after")
    p_afpd.add_argument("--json", action="store_true")

    # #17 save edit
    p_save_edit = p_save_sub.add_parser(
        "edit",
        help="Apply a declarative set of edits to a save slot (#17)",
        description=(
            "Load the input save slot, apply one or more edits,\n"
            "and write the result to a fresh output directory.\n"
            "Only text saves (magic.sav / loc.sav / options.sav)\n"
            "are editable today — see docs/SAVE_FORMAT.md § 7.\n\n"
            "Edit spec format:\n"
            "  <file>:<line_index>=<value>\n"
            "\n"
            "Examples:\n"
            "  azurik-mod save edit exported/ patched/ \\\n"
            "      --set magic.sav:0=99.000000\n"
            "  azurik-mod save edit exported/ patched/ \\\n"
            "      --plan edits.json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_save_edit.add_argument("input", help="Source save directory")
    p_save_edit.add_argument("output",
        help="Destination directory (must not exist yet)")
    p_save_edit.add_argument("--set", action="append", default=None,
        help="<file>:<line_index>=<value> (repeatable)")
    p_save_edit.add_argument("--plan", default=None,
        help="JSON file with an {\"edits\":[...]} block")
    p_save_edit.add_argument("--json", action="store_true")

    # #18 xbr edit (extends existing xbr subcommand)
    p_xbre = _xbr_sub.add_parser(
        "edit",
        help="Apply byte / string patches to an XBR file (#18)",
        description=(
            "Safe XBR mutation.  Supports in-place byte and ASCII\n"
            "string replacement at equal-sized slots.  Full\n"
            "structural edits (adding entries, resizing the string\n"
            "pool) are not supported yet; see azurik_mod/xbe_tools\n"
            "/xbr_edit.py for the current scope.\n\n"
            "Examples:\n"
            "  azurik-mod xbr edit in.xbr out.xbr \\\n"
            "      --set-string 'Hello=World' --tag surf\n"
            "  azurik-mod xbr edit in.xbr out.xbr \\\n"
            "      --replace-bytes 0x0040:DEADBEEF"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_xbre.add_argument("input", help="Source XBR")
    p_xbre.add_argument("output", help="Destination XBR")
    p_xbre.add_argument("--set-string", action="append", default=None,
        help="'old=new' ASCII string replacement; repeatable")
    p_xbre.add_argument("--tag", default=None,
        help="Restrict string search to this 4-char TOC tag")
    p_xbre.add_argument("--replace-bytes", action="append",
        default=None,
        help="'OFFSET:HEX' raw byte replacement; repeatable")

    # #24 level preview
    p_lp = sub.add_parser(
        "level",
        help="Level XBR inspection utilities (#24)",
        description=(
            "preview: structured summary of a level XBR — TOC\n"
            "          entry counts, strings per gameplay tag, and\n"
            "          plausible position triples."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _lp_sub = p_lp.add_subparsers(
        dest="level_command", required=True)
    p_lpp = _lp_sub.add_parser("preview",
        help="Structural preview of a level XBR")
    p_lpp.add_argument("path", help="Path to <level>.xbr")
    p_lpp.add_argument("--include-raw", action="store_true",
        help="Also show strings that passed the quality filter "
             "but didn't match a structured category (noisy)")
    p_lpp.add_argument("--max-items", type=int, default=30,
        help="Cap per-category listings at this many rows "
             "(default 30; use a large value when piping to a "
             "file)")
    p_lpp.add_argument("--json", action="store_true")

    # #26 movies frames (extends movies subcommand)
    p_mframes = _movies_sub.add_parser(
        "frames",
        help="Plan / extract PNG frames from a .bik file (#26)",
        description=(
            "Bink 1.x is proprietary; this tool relies on ffmpeg's\n"
            "open-source 'bink' decoder.  Without --run the command\n"
            "PLANS the extraction and prints the ffmpeg invocation\n"
            "(useful for CI pipelines / dry-runs).\n\n"
            "--info flag dumps metadata + per-frame offsets without\n"
            "requiring any decoder."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_mframes.add_argument("path", help="Path to a .bik file")
    p_mframes.add_argument("--out", default=None,
        help="Output directory (default: <bik>.frames/)")
    p_mframes.add_argument("--pattern", default="frame_%04d.png",
        help="Output filename template (default frame_%%04d.png)")
    p_mframes.add_argument("--info", action="store_true",
        help="Dump metadata only; no extraction")
    p_mframes.add_argument("--dry-run", action="store_true",
        help="Print the plan instead of running ffmpeg")
    p_mframes.add_argument("--json", action="store_true")

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
        "iso-verify": _dispatch_iso_verify,
        "xbe": _dispatch_xbe,
        "ghidra-coverage": _dispatch_ghidra_coverage,
        "ghidra-sync": _dispatch_ghidra_sync,
        "ghidra-snapshot": _dispatch_ghidra_snapshot,
        "shim-inspect": _dispatch_shim_inspect,
        "test-for-va": _dispatch_test_for_va,
        "plan-trampoline": _dispatch_plan_trampoline,
        "entity": _dispatch_entity,
        "xbr": _dispatch_xbr,
        "movies": _dispatch_movies,
        "new-shim": _dispatch_new_shim,
        "audio": _dispatch_audio,
        "plugins": _dispatch_plugins,
        "xrefs": _dispatch_xrefs,
        "call-graph": _dispatch_call_graph,
        "struct-diff": _dispatch_struct_diff,
        "decomp-cache": _dispatch_decomp_cache,
        "assets": _dispatch_assets,
        "level": _dispatch_level,
    }
    dispatch[args.command](args)


def _dispatch_xrefs(args) -> None:
    from azurik_mod.xbe_tools.commands import cmd_xrefs
    cmd_xrefs(args)


def _dispatch_call_graph(args) -> None:
    from azurik_mod.xbe_tools.commands import cmd_call_graph
    cmd_call_graph(args)


def _dispatch_struct_diff(args) -> None:
    from azurik_mod.xbe_tools.commands import cmd_struct_diff
    cmd_struct_diff(args)


def _dispatch_decomp_cache(args) -> None:
    from azurik_mod.xbe_tools.commands import cmd_decomp_cache
    cmd_decomp_cache(args)


def _dispatch_assets(args) -> None:
    from azurik_mod.xbe_tools.commands import (
        cmd_assets_fingerprint, cmd_assets_fingerprint_diff)
    verbs = {
        "fingerprint": cmd_assets_fingerprint,
        "fingerprint-diff": cmd_assets_fingerprint_diff,
    }
    verb = verbs.get(args.assets_command)
    if verb is None:
        raise SystemExit(
            f"unknown assets verb: {args.assets_command!r}")
    verb(args)


def _dispatch_level(args) -> None:
    from azurik_mod.xbe_tools.commands import cmd_level_preview
    verbs = {"preview": cmd_level_preview}
    verb = verbs.get(args.level_command)
    if verb is None:
        raise SystemExit(
            f"unknown level verb: {args.level_command!r}")
    verb(args)


def _dispatch_ghidra_sync(args) -> None:
    from azurik_mod.xbe_tools.commands import cmd_ghidra_sync
    cmd_ghidra_sync(args)


def _dispatch_test_for_va(args) -> None:
    from azurik_mod.xbe_tools.commands import cmd_test_for_va
    cmd_test_for_va(args)


def _dispatch_plan_trampoline(args) -> None:
    from azurik_mod.xbe_tools.commands import cmd_plan_trampoline
    cmd_plan_trampoline(args)


def _dispatch_entity(args) -> None:
    """Route the ``entity`` subcommand to the right verb."""
    from azurik_mod.xbe_tools.commands import cmd_entity_diff
    verbs = {"diff": cmd_entity_diff}
    verb = verbs.get(args.entity_command)
    if verb is None:
        raise SystemExit(
            f"unknown entity verb: {args.entity_command!r}")
    verb(args)


def _dispatch_xbr(args) -> None:
    from azurik_mod.xbe_tools.commands import (
        cmd_xbr_diff, cmd_xbr_edit, cmd_xbr_inspect)
    verbs = {
        "inspect": cmd_xbr_inspect,
        "diff": cmd_xbr_diff,
        "edit": cmd_xbr_edit,
    }
    verb = verbs.get(args.xbr_command)
    if verb is None:
        raise SystemExit(
            f"unknown xbr verb: {args.xbr_command!r}")
    verb(args)


def _dispatch_ghidra_snapshot(args) -> None:
    from azurik_mod.xbe_tools.commands import cmd_ghidra_snapshot
    cmd_ghidra_snapshot(args)


def _dispatch_movies(args) -> None:
    from azurik_mod.xbe_tools.commands import (
        cmd_movies_frames, cmd_movies_info)
    verbs = {"info": cmd_movies_info, "frames": cmd_movies_frames}
    verb = verbs.get(args.movies_command)
    if verb is None:
        raise SystemExit(
            f"unknown movies verb: {args.movies_command!r}")
    verb(args)


def _dispatch_new_shim(args) -> None:
    from azurik_mod.xbe_tools.commands import cmd_new_shim
    cmd_new_shim(args)


def _dispatch_audio(args) -> None:
    from azurik_mod.xbe_tools.commands import cmd_audio_dump
    verbs = {"dump": cmd_audio_dump}
    verb = verbs.get(args.audio_command)
    if verb is None:
        raise SystemExit(f"unknown audio verb: {args.audio_command!r}")
    verb(args)


def _dispatch_plugins(args) -> None:
    from azurik_mod.xbe_tools.commands import cmd_plugins_list
    verbs = {"list": cmd_plugins_list}
    verb = verbs.get(args.plugins_command)
    if verb is None:
        raise SystemExit(
            f"unknown plugins verb: {args.plugins_command!r}")
    verb(args)


def _dispatch_iso_verify(args) -> None:
    from azurik_mod.assets.commands import cmd_iso_verify
    cmd_iso_verify(args)


def _dispatch_xbe(args) -> None:
    """Route the ``xbe`` subcommand to the right verb handler."""
    from azurik_mod.xbe_tools.commands import (
        cmd_xbe_addr,
        cmd_xbe_find_floats,
        cmd_xbe_find_refs,
        cmd_xbe_hexdump,
        cmd_xbe_sections,
        cmd_xbe_strings,
    )
    verbs = {
        "addr": cmd_xbe_addr,
        "hexdump": cmd_xbe_hexdump,
        "find-refs": cmd_xbe_find_refs,
        "find-floats": cmd_xbe_find_floats,
        "strings": cmd_xbe_strings,
        "sections": cmd_xbe_sections,
    }
    verb = verbs.get(args.xbe_command)
    if verb is None:
        raise SystemExit(f"unknown xbe verb: {args.xbe_command!r}")
    verb(args)


def _dispatch_ghidra_coverage(args) -> None:
    from azurik_mod.xbe_tools.commands import cmd_ghidra_coverage
    cmd_ghidra_coverage(args)


def _dispatch_shim_inspect(args) -> None:
    from azurik_mod.xbe_tools.commands import cmd_shim_inspect
    cmd_shim_inspect(args)


def _dispatch_save(args) -> None:
    """Dispatch the ``save`` subcommand to its implementation."""
    from azurik_mod.save_format.commands import cmd_save_inspect
    from azurik_mod.xbe_tools.commands import cmd_save_edit
    if args.save_command in (None, "inspect"):
        cmd_save_inspect(args)
    elif args.save_command == "edit":
        cmd_save_edit(args)
    else:
        raise SystemExit(
            f"unknown save subcommand: {args.save_command!r}.  "
            f"Try `azurik-mod save inspect --help`.")


__all__ = ["main"]


if __name__ == "__main__":
    main()
