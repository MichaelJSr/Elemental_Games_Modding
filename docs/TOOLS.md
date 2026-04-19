# TOOLS.md — complete CLI reference

Every `azurik-mod` subcommand, grouped by workflow.  Use this as a
flat reference: one entry per verb, explaining what it does, when to
reach for it, and an example invocation.

> All verbs accept `--help` for detailed flags.  This doc summarises
> what each one does and when to use it — not every flag.

---

## Quick-jump table of contents

| Category | Verbs |
|---|---|
| [**Build**](#build-workflow) | `patch`, `randomize`, `randomize-gems`, `randomize-full`, `apply-physics`, `mod-template` |
| [**Browse**](#browse-workflow) | `list`, `dump`, `diff` |
| [**Verify**](#verify-workflow) | `verify-patches`, `iso-verify` |
| [**XBE inspect**](#xbe-inspection) | `xbe` (+ 6 sub-verbs) |
| [**XBR inspect**](#xbr--asset-inspection) | `xbr`, `entity`, `level`, `movies`, `audio`, `assets` |
| [**Save files**](#save-files) | `save` (+ `inspect` / `edit` / `key-recover`) |
| [**Ghidra integration**](#ghidra-integration) | `ghidra-coverage`, `ghidra-sync`, `ghidra-snapshot`, `xrefs`, `call-graph`, `struct-diff`, `decomp-cache` |
| [**Shim authoring**](#shim-authoring) | `shim-inspect`, `plan-trampoline`, `new-shim`, `test-for-va` |
| [**Plugins**](#plugins) | `plugins` |

Two entry points give you the same thing:

- `azurik-mod <verb>` — installed console script (from `pip install -e .`)
- `python -m azurik_mod <verb>` — module entry point, useful in CI

---

## Build workflow

Commands that produce a patched ISO from a vanilla Azurik ISO.  All
output ISOs are xemu-ready (no xbdm required).

### `patch` — apply a mod JSON to an ISO
Applies one-or-more entries from a mod-JSON file (produced by
`mod-template` or hand-edited) to an ISO, producing a patched ISO.
The base for every other build verb.

```bash
azurik-mod patch --iso Azurik.iso --mod mod.json -o Azurik_modded.iso
```

### `randomize` — gems + fragments + power-ups
Shuffles the three pools with a single seed.  Produces a new ISO.

```bash
azurik-mod randomize --iso Azurik.iso --seed 42 -o Azurik_rand.iso
azurik-mod randomize --iso Azurik.iso --seed 42 --no-gems -o powers_only.iso
```

### `randomize-gems` — legacy gem-only randomizer
Gems only; predates `randomize`.  Kept for back-compat.

### `randomize-full` — gems + keys + barriers + optional QoL
End-to-end shuffle with full connection logic.  The "I want everything
randomized" verb.

```bash
azurik-mod randomize-full --iso Azurik.iso --seed 42 -o Azurik_full.iso
```

### `apply-physics` — gravity + player-speed sliders
Pushes slider values (walk speed / run speed / gravity multiplier)
into the XBE directly.  Doesn't require a mod JSON.

```bash
azurik-mod apply-physics --iso Azurik.iso --gravity 0.5 --walk-speed 2 -o physics.iso
```

### `mod-template` — dump a vanilla mod JSON
Writes every editable config entry to a JSON file with vanilla
defaults.  Hand-edit it, then feed to `patch`.

```bash
azurik-mod mod-template -o vanilla.json
```

---

## Browse workflow

Read-only inspection of config.xbr contents.  No ISOs built.

### `list` — enumerate sections or entities
```bash
azurik-mod list --sections                      # all sections
azurik-mod list --entities critters_walking     # entities in one section
```

### `dump` — show current values
```bash
azurik-mod dump --iso Azurik.iso -s settings_foo -e air
azurik-mod dump --input config.xbr -s critters_walking -e garret4
```

### `diff` — preview what a mod WOULD change
```bash
azurik-mod diff --iso Azurik.iso --mod mod.json
```

---

## Verify workflow

Post-build integrity checks.

### `verify-patches` — confirm patch bytes landed
Re-walks the XBE looking for expected post-patch bytes at every
tracked VA.  Catches drift between mod JSON and reality.

```bash
azurik-mod verify-patches Azurik_modded.iso
```

### `iso-verify` — manifest-level integrity
Cross-references an unpacked ISO against `filelist.txt` +
`prefetch-lists.txt` (size + MD5).  Runs automatically after every
`xdvdfs unpack`.

```bash
azurik-mod iso-verify path/to/unpacked/
```

---

## XBE inspection

The `xbe` subcommand is the Swiss-army knife for default.xbe
analysis.  Sub-verbs:

### `xbe addr` — VA ↔ file offset translation
```bash
azurik-mod xbe addr --xbe default.xbe --va 0x00085700
azurik-mod xbe addr --xbe default.xbe --file 0x75700
```

### `xbe hexdump` — hex view at any VA
```bash
azurik-mod xbe hexdump 0x00085700 --xbe default.xbe --len 64
```

### `xbe find-refs` — list code refs to a VA or string
```bash
azurik-mod xbe find-refs --va 0x001980A8 --xbe default.xbe
azurik-mod xbe find-refs --string "levels/air/a1" --xbe default.xbe
```

### `xbe find-floats` — scan `.rdata` for a float constant
```bash
azurik-mod xbe find-floats 30.0 --xbe default.xbe
azurik-mod xbe find-floats --hex 0x41F00000 --xbe default.xbe
```

### `xbe strings` — search for literal/regex in string sections
```bash
azurik-mod xbe strings "signature" --xbe default.xbe
azurik-mod xbe strings "\\.sav$" --regex --xbe default.xbe
```

### `xbe sections` — list every section + flags
```bash
azurik-mod xbe sections --xbe default.xbe
```

---

## XBR + asset inspection

Azurik's data files use a TOC-based `.xbr` format.  These verbs read
them without modifying.

### `xbr inspect` — dump records of a specific tag
```bash
azurik-mod xbr inspect gamedata/town.xbr --tag node --entries 5
```

### `xbr diff` — structural diff between two XBR files
Shows added/removed/resized tags + string changes.

```bash
azurik-mod xbr diff gamedata/town.xbr modded/town.xbr
```

### `xbr edit` — safe same-size byte + string replacement
```bash
azurik-mod xbr edit in.xbr out.xbr --set-string 'Hello=World' --tag surf
azurik-mod xbr edit in.xbr out.xbr --replace-bytes 0x40:DEADBEEF
```

### `entity diff` — compare config.xbr entity values
```bash
azurik-mod entity diff --iso A.iso --iso B.iso -s critters_walking -e garret4
```

### `level preview` — structured level summary
Shows per-tag counts, extracted level connections, localisation keys,
cutscene refs, asset paths, identifiers.  Replaces the earlier
noise-heavy preview.

```bash
azurik-mod level preview gamedata/town.xbr
azurik-mod level preview gamedata/w1.xbr --include-raw
```

### `movies info` — Bink movie metadata
```bash
azurik-mod movies info movies/AdreniumLogo.bik
```

### `movies frames` — extract PNG frames from a Bink file
Uses ffmpeg if available; prints a plan otherwise.

```bash
azurik-mod movies frames movies/Title.bik --out frames/
azurik-mod movies frames movies/Title.bik --info
```

### `audio dump` — extract wave blobs from fx.xbr
Partial extractor — format still being reversed.

```bash
azurik-mod audio dump gamedata/fx.xbr -o audio_out/
```

### `assets fingerprint` — sha1-index a file tree
```bash
azurik-mod assets fingerprint iso/unpacked/ -o before.json
# edit, repack
azurik-mod assets fingerprint iso/unpacked/ -o after.json
azurik-mod assets fingerprint-diff before.json after.json
```

---

## Save files

### `save inspect` — decode an Azurik save slot
Handles both Xbox-container files (`.xbx`) and the four `.sav`
variants (text, binary, signature, level).

```bash
azurik-mod save inspect exported_save/
azurik-mod save inspect exported_save/magic.sav --json
```

### `save edit` — declarative text-save edits
Edits `magic.sav` / `loc.sav` / `options.sav` via `file:line=value`
specs.  Binary saves aren't editable yet.  See `SAVE_FORMAT.md` § 7
for workflow + signature caveats.

```bash
azurik-mod save edit exported/ patched/ \
    --set magic.sav:0=99.000000 --set magic.sav:3=0
```

### `save key-recover` — brute-force HMAC key from a dump
Scans a memory / binary dump for the 16-byte `XboxSignatureKey`
matching known save signatures.  Uses multiprocessing; feed ≥2
save slots to rule out false positives.

```bash
azurik-mod save key-recover --dump xemu-ram.bin \
    --save slot1/ --save slot2/ --workers 8
```

---

## Ghidra integration

Everything below talks to a live Ghidra instance via its HTTP REST
plugin (GhydraMCP).  Default port: 8193.  Use `--host` / `--port`
to override.

### `ghidra-coverage` — what we know vs what Ghidra labels
Cross-reference our Python-side symbols (vanilla_symbols, anchors,
patch sites) against Ghidra.  Surfaces unlabeled-known (ours ->
Ghidra has `FUN_*`) and labeled-unknown (Ghidra has a nice name we
haven't captured).

```bash
azurik-mod ghidra-coverage --snapshot docs/ghidra_snapshot.json
azurik-mod ghidra-coverage --live --port 8193
```

### `ghidra-sync` — push our knowledge INTO Ghidra
Renames + plate-comments functions + **pushes struct definitions**.
With `--push-structs`, populates Ghidra's Data Type Manager with
`azurik.h`-defined structs.

```bash
azurik-mod ghidra-sync                         # dry-run
azurik-mod ghidra-sync --apply                 # push names + comments
azurik-mod ghidra-sync --apply --push-structs  # + struct layouts
azurik-mod ghidra-sync --apply --push-structs --recreate-structs  # overwrite
```

### `ghidra-snapshot` — export Ghidra state to JSON
```bash
azurik-mod ghidra-snapshot docs/ghidra_snapshot.json
```

### `xrefs` — ASCII tree of callers / callees around a VA
```bash
azurik-mod xrefs 0x00085700                    # callers, depth=2
azurik-mod xrefs 0x00085700 --direction out --depth 3
```

### `call-graph` — Graphviz DOT call-graph from seed VAs
```bash
azurik-mod call-graph 0x00085700 --dot graph.dot
dot -Tpng graph.dot > graph.png
```

### `struct-diff` — azurik.h vs Ghidra DTM
```bash
azurik-mod struct-diff --verbose
azurik-mod struct-diff --offline              # skip Ghidra
```

### `decomp-cache` — on-disk decompilation cache
Wraps `GhidraClient.decompile` to avoid re-fetching the same
function 50 times per session.

```bash
azurik-mod decomp-cache stats
azurik-mod decomp-cache get 0x00085700
azurik-mod decomp-cache clear
```

---

## Shim authoring

### `new-shim` — scaffold a new feature folder
Creates `azurik_mod/patches/<name>/` with `__init__.py`, `shim.c`,
`README.md`.  With `--hook <VA> --xbe <path>`, also fills in
`replaced_bytes` and picks calling convention from Ghidra.

```bash
azurik-mod new-shim qol_skip_intro
azurik-mod new-shim qol_skip_intro --hook 0x5F6E5 --xbe default.xbe
azurik-mod new-shim qol_skip_intro --hook 0x5F6E5 --xbe default.xbe --ghidra
azurik-mod new-shim qol_skip_intro --emit-test    # + regression-test starter
```

### `plan-trampoline` — size a hook site
Returns the bytes to replace + disassembly context, used for the
`replaced_bytes` field in a `TrampolinePatch`.

```bash
azurik-mod plan-trampoline 0x5F6E5 --xbe default.xbe --budget 5
```

### `shim-inspect` — preview bytes a compiled .o emits
Shows COFF sections, symbols, relocations.  Essential for debugging
why a trampoline isn't linking.

```bash
azurik-mod shim-inspect azurik_mod/patches/qol_skip_logo
azurik-mod shim-inspect shims/build/qol_skip_logo.o
```

### `test-for-va` — find tests touching a VA or pack name
```bash
azurik-mod test-for-va 0x00085700
azurik-mod test-for-va fps_unlock
```

---

## Plugins

### `plugins list` — discovered third-party plugin packs
```bash
azurik-mod plugins list
azurik-mod plugins list --reload
```

---

## Environment variables

- `AZURIK_DECOMP_CACHE` — override decomp-cache root (default
  `~/.cache/azurik-mod/decomps`)
- `AZURIK_GHIDRA_HOST` / `AZURIK_GHIDRA_PORT` — default Ghidra
  connection
- `AZURIK_GHIDRA_TIMEOUT` — HTTP timeout in seconds (default 10)
- `AZURIK_NO_PLUGINS=1` — skip third-party plugin autoload
- `AZURIK_NO_SHIMS=1` — global legacy-fallback switch for features
  that have both byte-only + shim variants
- `XDG_CACHE_HOME` — honoured for all cache paths

---

## GUI alternative

Most of the build + browse workflow is also in the Tkinter GUI:

```bash
azurik-gui        # or: python -m gui
```

See the top-level [`README.md`](../README.md) for GUI screenshots
and tab-by-tab walkthrough.

---

## When to use CLI vs GUI

- **GUI**: end-user modding, one-off builds, "toggle QoL patches"
- **CLI**: batch operations, scripting, CI, automated workflows,
  anything with a seed-based reproduction requirement

Every GUI action eventually calls the same functions that the CLI
verbs wrap, so behaviour is identical between the two.
