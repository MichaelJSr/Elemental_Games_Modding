# Elemental Games Modding

A reverse engineering and modding toolkit for **Azurik: Rise of Perathia** (classic Xbox, 2001).  The repo ships a library (`azurik_mod`) and a desktop GUI that wrap a patch-loader engine, a full-game randomizer, and analysis scripts — all driven from one `pip install -e .` setup.

No original game assets or proprietary binaries are included.  You must own a legitimate copy of the game and supply your own ISO.

> **New here?**  Read [`docs/ONBOARDING.md`](docs/ONBOARDING.md) —
> zero to a landed feature in two worked examples, plus a map of
> where to look when something breaks.
>
> **AI agent picking up a task?**  Read [`AGENTS.md`](AGENTS.md) —
> rules, checkpoints, and pointers to the task-specific docs.
>
> **Looking for a specific tool or script?**  Jump straight to
> [`docs/TOOLS.md`](docs/TOOLS.md) (all 31 CLI verbs) or
> [`docs/SCRIPTS.md`](docs/SCRIPTS.md) (standalone scripts).
> The full doc map lives at [`docs/INDEX.md`](docs/INDEX.md).

---

## Quick start

```bash
# 1. Clone and install (editable)
git clone https://github.com/MichaelJSr/Elemental_Games_Modding.git
cd Elemental_Games_Modding
pip install -e .

# 2. Drop your base ISO into the iso/ folder (it will be auto-detected)
cp "path/to/Azurik.iso" iso/

# 3. Launch the GUI
azurik-gui          # or: python -m gui
```

On Windows just double-click `Launch Azurik Mod Tools.bat`; on macOS / Linux use `Launch Azurik Mod Tools.command`.  Both launchers look for Python in the user's Homebrew / pyenv / Python.org locations even when Finder / Explorer strip the shell PATH, so they work without extra setup as long as Python 3.10+ is installed.

The first time you build an ISO, the tool auto-downloads `xdvdfs` for your platform into the user cache (`platformdirs.user_cache_dir("azurik_mod")`).  macOS users need to `cargo install xdvdfs-cli` because upstream does not ship a macOS binary.

---

## Repository layout

```
Elemental_Games_Modding/
  pyproject.toml
  README.md
  AGENTS.md                          AI-agent entry point (read first if you're an agent)
  CHANGELOG.md
  Launch Azurik Mod Tools.command    macOS / Linux launcher
  Launch Azurik Mod Tools.bat        Windows launcher
  docs/                              Research notes and per-subsystem docs
    INDEX.md                         doc map — every .md file with one-line summary
    ONBOARDING.md                    zero-to-landed-feature walkthrough
    AGENT_GUIDE.md                   AI-agent-specific playbook
    TOOLS.md                         catalog of every CLI verb (31 subcommands)
    SCRIPTS.md                       catalog of standalone scripts
    SHIMS.md                         C-shim platform architecture
    SHIM_AUTHORING.md                end-to-end authoring guide
    LEARNINGS.md                     accumulated RE findings
    PATCHES.md                       catalog of every feature pack
    TOOLING_ROADMAP.md               shipped + planned tooling
    D1_EXTEND.md                     runtime xboxkrnl export resolver design
    D2_NXDK.md                       (deferred) full NXDK integration plan
    SAVE_FORMAT.md                   Azurik save-game format + `save inspect` CLI
    RANDOMIZER_AUDIT.md              randomizer correctness audit + extension roadmap
    PLUGINS.md                       third-party plugin pack authoring
    MODDING_GUIDE.md                 user-facing mod-JSON authoring guide
    DECOMP.md                        pointer to the Ghidra project
    ghidra_snapshot.json             committed Ghidra state (490 funcs, 3 structs)
  azurik_mod/                        Library (pip-installable)
    cli.py                           argparse dispatcher ("azurik-mod")
    patching/                        patch engine (apply_pack, COFF, XBE map)
    patches/                         ONE FOLDER PER FEATURE
      fps_unlock/                      50-site PatchSpec pack
      player_physics/                  gravity + walk + run sliders
      qol_gem_popups/                  hide 5 first-pickup popups
      qol_other_popups/                hide 9 tutorial / powerup popups
      qol_pickup_anims/                skip pickup celebration
      qol_skip_logo/                   C-shim feature (includes shim.c)
    iso/                             xdvdfs locator / downloader / pack
    randomizer/                      solver + level editor + logic db
    config/                          config.xbr registry + schema + keyed tables
  shims/                             Shared shim library (NOT feature code)
    include/                           azurik.h, azurik_vanilla.h, azurik_kernel.h
    toolchain/                         compile.sh + new_shim.sh (scaffold)
    fixtures/                          test-only shim sources (_*.c)
    build/                             compiled .o cache (gitignored)
  gui/                               Tkinter GUI ("azurik-gui")
    app.py
    backend.py                       in-process calls into azurik_mod
    pages/                           one module per screen
  scripts/                           Stand-alone utilities
  iso/                               Drop your base ISO here (auto-detected by the GUI)
  tests/                             pytest-based unit + integration tests
```

### Key sub-packages

- `azurik_mod.patching` — `Feature` / `PatchPack` descriptor, apply/verify primitives, the unified `apply_pack(pack, xbe, params)` dispatcher, the XBE section map, and the central pack registry.
- `azurik_mod.patches.<feature>/` — one folder per feature, containing the `Feature(...)` declaration and (if shim-backed) its C source.  Importing the package runs every feature's `register_feature(...)` side effect automatically.
- `azurik_mod.randomizer` — forward-fill logic solver, shuffle pools, level editor helpers, and the logic DB.
- `shims/` — shared headers, clang toolchain wrapper, and test fixtures used by every shim-backed feature.  Feature-specific C code lives inside the feature folder, not here.
- `azurik_mod.iso.xdvdfs` — auto-downloader with `$AZURIK_XDVDFS`, PATH, and user-cache resolution.

### Library entry points

After `pip install -e .` the following commands land on your PATH:

| Command        | What it does                                                           |
|----------------|------------------------------------------------------------------------|
| `azurik-mod`   | CLI for patch / dump / diff / randomize / apply-physics / verify-patches. |
| `azurik-gui`   | Launches the Tk GUI (equivalent to `python -m gui`).                   |

---

## Using the GUI

```bash
azurik-gui
```

The GUI is now a general modding toolkit, not just a randomizer — it has dedicated pages for randomization, patches, per-entity editing, and build output.

- **Project** page auto-detects the first `.iso` it finds inside the repo's `iso/` folder and picks a sensible output name next to it.  You can override either with the Browse buttons.
- **Randomize** page exposes the seed and the shuffle pools (major items, keys, gems, barriers, level connections) plus the advanced obsidian-cost / custom-item-pool knobs.  **All options default to OFF** so an untouched build is a no-op — tick only the pools you want to randomize.
- **Patches** page is the single source of truth for every patch pack (FPS unlock, QoL, Player Physics, …).  Ticking a pack here includes it in the next build; slider values beneath each pack (gravity, walk / run speed multipliers) only take effect when the parent pack is on.
- The **Build & Logs** page merges both inputs right before kicking off the worker, so you can flip a pack on / off between runs without touching the Randomize page.
- Every page scrolls vertically, so long sections (e.g. the full patch list, all slider parameters) remain reachable on short windows.

Click **Build** and watch progress on the *Build & Logs* page.  The build runs on a worker thread; output streams live via a thread-safe message queue.

---

## Using the CLI

```bash
# Full randomizer
azurik-mod randomize-full \
  --iso  "iso/Azurik - Rise of Perathia.iso" \
  --seed 42 \
  --output iso/Azurik_randomized.iso

# Physics slider (gravity + player speed) without randomizing anything
azurik-mod apply-physics \
  --iso iso/Azurik.iso \
  --output iso/Azurik_lowgrav.iso \
  --gravity 4.9 \
  --walk-speed 1.5

# Verify a built XBE against every registered patch pack
azurik-mod verify-patches \
  --xbe      out/default.xbe \
  --original stock.xbe \
  --strict

# Dump live config values
azurik-mod dump --iso game.iso --section critters_walking --entity air_elemental

# Generate an editable mod JSON populated with vanilla defaults
# (replaces the deprecated `examples/` folder — always truthful
# because it reads the ISO live):
azurik-mod mod-template --iso game.iso \
    --section critters_walking --entity goblin -o my_goblin.json

# Preview a mod before writing
azurik-mod diff --iso game.iso --mod my_goblin.json

# Apply one or more mods
azurik-mod patch --iso game.iso --mod my_goblin.json --output out.iso

# Or use the GUI's Entity Editor tab for point-and-click editing —
# it reads the same live values and produces the same mod JSON.
```

All options (including `--no-major`, `--no-gems`, `--no-qol`, `--fps-unlock`, `--gravity`, `--player-walk-scale`, `--player-run-scale`, `--obsidian-cost`, `--item-pool`, `--config-mod`) pass straight through the CLI tree.

---

## Running the tests

```bash
pip install -e .[dev]
pytest
```

Covers:

- FPS safety invariants: `CMP ESI, 0x4` in TRUNC, `PUSH 0x4` + two `FADD ST0,ST0` in CATCHUP, non-overlapping patch ranges, equal patch/original byte lengths, safety-critical tagging.
- Patch loader round-trip: `apply_patch_spec` + `verify_patch_spec` on synthetic data, idempotent re-apply, mismatch detection, out-of-range handling, `ParametricPatch` apply/verify with virtual-slider handling.
- Patch registry sanity: every pack registers, no duplicate VAs across packs, parametric / spec site separation.
- Player physics: gravity encode/decode round-trip, apply/verify default vs custom, out-of-range rejection, walk/run scale idempotence on `config.xbr`.

---

## Tools & methodology

| Tool | Purpose |
|------|---------|
| [Ghidra](https://ghidra-sre.org) | Disassembler + decompiler |
| [GhydraMCP](https://github.com/starsong-consulting/GhydraMCP) | MCP bridge for AI-assisted RE |
| [xemu](https://xemu.app) | Runtime verification |
| [xdvdfs](https://github.com/antangelo/xdvdfs) | Xbox ISO extract / repack |

See [`docs/DECOMP.md`](docs/DECOMP.md) for the Ghidra project pointer and a function index, and [`docs/PATCHES.md`](docs/PATCHES.md) for the per-pack catalog.

---

## Troubleshooting

### "Python was not found" when double-clicking the launcher (macOS)

When you double-click a `.command` file from Finder, macOS invokes Bash with a minimal PATH that does not include Homebrew (`/opt/homebrew/bin`), Python.org (`/Library/Frameworks/Python.framework`), or pyenv shims.  The launcher probes those locations explicitly, sources your shell profile, and tries `python3.12`, `python3.11`, `python3.10`, `python3`, and `python` in order, so it should work out-of-the-box.  If it still fails:

1. Open Terminal, run `python3 --version`, and confirm a 3.10+ interpreter is on your PATH.
2. If it is, right-click the launcher → *Get Info* → make sure "Open with" is set to Terminal.
3. If Python is installed but `python3 --version` also fails in a fresh Terminal, add its bin directory to `~/.zprofile`.

You can always fall back to running the GUI from a Terminal in the repo root:

```bash
python3 -m gui
```

### "The file couldn't be opened because you don't have permission"

Run `chmod +x "Launch Azurik Mod Tools.command"` once after cloning — macOS sometimes strips the execute bit when downloaded.

### `Launch Azurik Mod Tools.command` prints `set: -: invalid option` and `command not found` errors

The script was saved with Windows (CRLF) line endings, which bash refuses to parse.  Fix it once with:

```bash
tr -d '\r' < "Launch Azurik Mod Tools.command" > /tmp/_fix
mv /tmp/_fix "Launch Azurik Mod Tools.command"
chmod +x "Launch Azurik Mod Tools.command"
```

The repo's [`.gitattributes`](.gitattributes) file pins `.command` and `.sh` files to LF so this cannot happen again on a `git checkout`.

### Where are the build logs?

Every run is mirrored live into the *Build & Logs* page's scrolling log box AND a persistent file on disk, so you can attach the log to an issue or diff runs after the fact.

| Platform | Log folder |
|----------|------------|
| macOS    | `~/Library/Logs/azurik_mod/` |
| Linux    | `~/.local/state/azurik_mod/log/` |
| Windows  | `%LOCALAPPDATA%\azurik_mod\Logs\` |

Files are named `build-<YYYYMMDD-HHMMSS>-seed<N>.log`.  The Build page has two buttons wired up for this:

- **Open log folder** — reveals the folder above in Finder / Explorer / your file manager.
- **Open last log** — opens the log for the most recent build of this session (or falls back to the newest log on disk if no build has run yet in the current window).

The log contains a small header (started timestamp, seed, ISO path, output path, pack flags, slider values) followed by every line of stdout / stderr from the worker thread, plus a full Python traceback if the build died with an unexpected exception.  Copy-paste the relevant lines if you file an issue.

---

## Contributing

Open issues / PRs for:

- New patch packs — register via `patches/<feature>.py` and `register_pack(...)` so they appear in the GUI and CLI automatically.
- Randomizer categories — extend `azurik_mod.randomizer`.
- Documentation — research notes go in `docs/`.
- Analysis scripts — drop them under `scripts/analysis/`.

All contributed code must be original work; do not submit decompiled output verbatim.

---

## License

MIT.  See `LICENSE`.
