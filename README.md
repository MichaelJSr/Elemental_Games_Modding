# Elemental Games Modding

A reverse-engineering and modding toolkit for **Azurik: Rise of Perathia** (classic Xbox, 2001).  Ships a library (`azurik_mod`), a desktop GUI (`azurik-gui`), a patch-loader engine, a full-game randomizer, and analysis tooling — all installed by one `pip install -e .`.

No game assets or proprietary binaries are included.  You must own a legitimate copy of the game and supply your own ISO.

> - **New here?** — [`docs/ONBOARDING.md`](docs/ONBOARDING.md) (zero to a landed feature).
> - **AI agent?** — [`AGENTS.md`](AGENTS.md).
> - **Looking for a tool or script?** — [`docs/TOOLS.md`](docs/TOOLS.md) + [`docs/SCRIPTS.md`](docs/SCRIPTS.md).
> - **Full doc map** — [`docs/INDEX.md`](docs/INDEX.md).

---

## Quick start

```bash
git clone https://github.com/MichaelJSr/Elemental_Games_Modding.git
cd Elemental_Games_Modding
pip install -e .

cp "path/to/Azurik.iso" iso/     # any *.iso in iso/ is auto-detected
azurik-gui                       # or: python -m gui
```

Windows users can double-click `Launch Azurik Mod Tools.bat`; macOS / Linux users use `Launch Azurik Mod Tools.command`.  Both launchers probe Homebrew / pyenv / Python.org locations when Finder / Explorer strip the shell PATH, so they work out-of-the-box as long as Python 3.10+ is installed.

First-build auto-downloads `xdvdfs` for your platform into the user cache (`platformdirs.user_cache_dir("azurik_mod")`).  macOS users need `cargo install xdvdfs-cli` because upstream has no macOS binary.

---

## Repository layout

```
Elemental_Games_Modding/
  azurik_mod/              pip-installable library
    cli.py                   argparse dispatcher (`azurik-mod`)
    patching/                patch engine + COFF loader + XBE section map
    patches/<feature>/       ONE FOLDER PER FEATURE
    iso/                     xdvdfs locator / downloader / pack
    randomizer/              solver, shufflers, logic DB
    save_format/             save-file parse + edit
    xbe_tools/               ghidra client, shim_inspect, call_graph, …
  shims/                   shared shim toolchain
    include/                   azurik.h / azurik_vanilla.h / azurik_kernel.h
    toolchain/                 compile.sh + new_shim.sh
  gui/                     Tkinter GUI (`azurik-gui`)
  scripts/                 standalone utilities
  docs/                    research notes + per-subsystem docs
  tests/                   pytest unit + integration tests
  iso/                     drop your base ISO here (auto-detected)
```

Two executables land on your PATH after install:

| Command      | Purpose                                                              |
|--------------|----------------------------------------------------------------------|
| `azurik-mod` | CLI: patch / dump / diff / randomize / apply-physics / verify-patches / xbe / xbr / ghidra / shim / save. |
| `azurik-gui` | Tk GUI (equivalent to `python -m gui`).                              |

See [`docs/TOOLS.md`](docs/TOOLS.md) for the full catalog of 31+ CLI verbs.

---

## GUI

The GUI is a general modding toolkit — dedicated pages for randomization, patches, per-entity editing, and build output.

- **Project** — auto-detects the first ISO under `iso/`.
- **Randomize** — seed + shuffle pools (items / keys / gems / barriers / connections) + obsidian-cost / custom-pool knobs.  All options default to **OFF** (untouched build = no-op).
- **Patches** — single source of truth for every patch pack (FPS unlock, QoL, Player Physics, …).  Slider values apply only when the parent pack is ticked.
- **Build & Logs** — merges both inputs, runs the build on a worker thread, streams output live.

Every page scrolls vertically so long sections remain reachable on short windows.

---

## CLI (common recipes)

```bash
# Full randomizer
azurik-mod randomize-full --iso iso/Azurik.iso --seed 42 --output iso/Azurik_rand.iso

# Player-physics sliders without randomizing anything
azurik-mod apply-physics --iso iso/Azurik.iso --output iso/Azurik_mod.iso \
    --gravity 4.9 --walk-speed 1.5 --wing-flap-ceiling 3.0

# Verify a built XBE against every registered patch pack
azurik-mod verify-patches --xbe out/default.xbe --original stock.xbe --strict

# Dump live config values
azurik-mod dump --iso game.iso --section critters_walking --entity air_elemental

# Generate an editable mod JSON populated with vanilla defaults
azurik-mod mod-template --iso game.iso --section critters_walking \
    --entity goblin -o my_goblin.json

# Apply one or more mods
azurik-mod patch --iso game.iso --mod my_goblin.json --output out.iso
```

Run `azurik-mod --help` for the full verb list; `azurik-mod <verb> --help` for per-verb flags.

---

## Tests

```bash
pip install -e .[dev]
pytest                # ~40 s on a modern CPU; 780+ tests
```

Coverage: patch loader round-trip, every pack's apply/verify, FPS safety invariants, player-physics byte landings, VA-anchor drift guards, shim COFF loader, GUI pack browser + slider routing, save-file editor round-trip, randomizer pools + solver.

---

## Tooling & methodology

| Tool | Purpose |
|------|---------|
| [Ghidra](https://ghidra-sre.org) | Disassembly + decompilation |
| [GhydraMCP](https://github.com/starsong-consulting/GhydraMCP) | MCP bridge for AI-assisted RE |
| [xemu](https://xemu.app) | Runtime verification |
| [xdvdfs](https://github.com/antangelo/xdvdfs) | Xbox ISO extract / repack |

See [`docs/DECOMP.md`](docs/DECOMP.md) for the XBE section map + notable-function reference, and [`docs/PATCHES.md`](docs/PATCHES.md) for the per-pack catalog.

---

## Troubleshooting

### "Python was not found" when double-clicking the launcher (macOS)

macOS launches `.command` files with a minimal PATH.  The launcher probes Homebrew / Python.org / pyenv explicitly — if it still fails, run `python3 --version` in Terminal to confirm a 3.10+ interpreter is installed, then add its `bin` to `~/.zprofile`.

Fallback: `python3 -m gui` from the repo root.

### "The file couldn't be opened because you don't have permission"

Run `chmod +x "Launch Azurik Mod Tools.command"` once after cloning.

### `set: -: invalid option` when running the launcher

Line endings got CRLF-mangled.  The repo's `.gitattributes` pins `.command`/`.sh` to LF, so a fresh `git checkout` fixes it.  Manual fix:

```bash
tr -d '\r' < "Launch Azurik Mod Tools.command" > /tmp/_fix
mv /tmp/_fix "Launch Azurik Mod Tools.command"
chmod +x "Launch Azurik Mod Tools.command"
```

### Where are the build logs?

Every build is mirrored into `~/Library/Logs/azurik_mod/` (macOS), `~/.local/state/azurik_mod/log/` (Linux), or `%LOCALAPPDATA%\azurik_mod\Logs\` (Windows).  The GUI's Build page has **Open log folder** / **Open last log** buttons.

---

## Contributing

Open issues / PRs for:

- **New patch packs** — drop a folder under `azurik_mod/patches/<feature>/` with a `register_feature(...)` call; the GUI + CLI pick it up automatically.
- **Randomizer extensions** — extend `azurik_mod.randomizer`.
- **Docs / RE notes** — `docs/`.
- **Analysis scripts** — `scripts/analysis/`.

All contributed code must be original work; do not submit decompiled output verbatim.

---

## License

MIT.  See `LICENSE`.
