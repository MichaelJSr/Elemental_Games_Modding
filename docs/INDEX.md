# Documentation index

Complete map of every document in this repo.  Use this as a routing
table when you're looking for something specific.

> **Starting fresh?**  Read these in order:
>  1. [`../AGENTS.md`](../AGENTS.md) — if you're an AI agent
>  2. [`../README.md`](../README.md) — if you're a human new to the repo
>  3. [`ONBOARDING.md`](ONBOARDING.md) — hands-on walkthrough
>  4. One of the task-specific docs below

---

## Entry points by reader type

| I am a(n)… | Start here                                       |
|-----------|--------------------------------------------------|
| **AI agent** working on the repo | [`../AGENTS.md`](../AGENTS.md) |
| **First-time contributor** | [`ONBOARDING.md`](ONBOARDING.md) |
| **Mod author** (building a feature) | [`SHIM_AUTHORING.md`](SHIM_AUTHORING.md) |
| **End user** (playing a mod) | [`../README.md`](../README.md) |
| **RE researcher** | [`LEARNINGS.md`](LEARNINGS.md) + [`ghidra_snapshot.json`](ghidra_snapshot.json) |
| **Tool consumer** (CLI / script user) | [`TOOLS.md`](TOOLS.md) + [`SCRIPTS.md`](SCRIPTS.md) |
| **Plugin author** (third-party pack) | [`PLUGINS.md`](PLUGINS.md) |

---

## Every document, at a glance

### Agent / contributor guides

- [`AGENT_GUIDE.md`](AGENT_GUIDE.md) — **AI agent playbook**.
  Hard rules (🛑), checkpoints (✅), patterns for common tasks,
  landmines to avoid.  Required reading for any AI making changes.
- [`ONBOARDING.md`](ONBOARDING.md) — **First-time contributor
  walkthrough**.  Zero to a landed feature in two worked examples
  (byte-only QoL patch, then a C-shim trampoline).
- [`MODDING_GUIDE.md`](MODDING_GUIDE.md) — **User-facing mod
  authoring guide**.  For end-users making mod JSONs without
  touching Python.

### Tool + script references

- [`TOOLS.md`](TOOLS.md) — **Every CLI verb**, grouped by workflow
  (build / browse / verify / save / xbe / xbr / ghidra / shim /
  plugin).  One-line "what it does", example invocations, output
  formats.  31 subcommands.
- [`SCRIPTS.md`](SCRIPTS.md) — **Every standalone script** in
  `scripts/` and `scripts/analysis/`.  Usage + when to reach for
  each one vs. the equivalent CLI verb.
- [`TOOLING_ROADMAP.md`](TOOLING_ROADMAP.md) — **What's shipped vs
  planned.**  Catalogue of 26 tools built across three tiers +
  the next wave.  Useful for "has anyone built X yet?" questions.

### Platform architecture

- [`SHIMS.md`](SHIMS.md) — **C-shim platform design**.  How the
  shim infrastructure works end-to-end: COFF loader, trampolines,
  kernel imports, shared libraries.  Read before authoring a
  non-trivial shim.
- [`SHIM_AUTHORING.md`](SHIM_AUTHORING.md) — **End-to-end shim
  authoring guide**.  Practical companion to SHIMS.md —
  everything you need to write, test, and ship a C shim.
- [`D1_EXTEND.md`](D1_EXTEND.md) — Runtime xboxkrnl export
  resolver design (how `azurik_kernel.h` works at shim-apply
  time).
- [`D2_NXDK.md`](D2_NXDK.md) — NXDK bridge plan (not yet
  implemented — future work for higher-level C helpers).
- [`DECOMP.md`](DECOMP.md) — Decompile-to-C plan + current state
  (stubbed; Ghidra-export path still in design).

### Reference data

- [`PATCHES.md`](PATCHES.md) — **Catalog of every feature pack**.
  What each one does, category (performance / player / boot /
  quality-of-life / experimental / randomize), default toggle
  state, GUI surface.
- [`LEARNINGS.md`](LEARNINGS.md) — **Accumulated RE findings**.
  60 FPS constants, `config.xbr` layout, `characters.xbr`, save
  format, kernel import mechanics, etc.  The single richest doc
  in the repo for game-internals knowledge.
- [`SAVE_FORMAT.md`](SAVE_FORMAT.md) — **Save-file format
  investigation**.  Full trace of the save-signature algorithm
  (HMAC-SHA1 over a sorted `.sav` tree), known unknowns (key
  derivation), recovery workflows.
- [`RANDOMIZER_AUDIT.md`](RANDOMIZER_AUDIT.md) — Randomizer code
  audit: pools, solvability, gem-skip identifier collisions.
- [`PLUGINS.md`](PLUGINS.md) — **Plugin authoring guide**.  How
  to ship a third-party patch pack via `importlib.metadata`
  entry points.
- [`ghidra_snapshot.json`](ghidra_snapshot.json) — **Current
  Ghidra state**.  490 named functions, 4,926 labels, 3 structs
  (ControllerState / CritterData / PlayerInputState).  Regenerate
  via `azurik-mod ghidra-snapshot` after any manual Ghidra edit
  you want to preserve.

---

## Documents by task

### "I want to add a new feature"

1. [`ONBOARDING.md`](ONBOARDING.md) — worked example first
2. [`SHIM_AUTHORING.md`](SHIM_AUTHORING.md) — end-to-end guide
3. [`AGENT_GUIDE.md`](AGENT_GUIDE.md) § "add a new feature"

### "I want to understand a piece of the game"

1. [`LEARNINGS.md`](LEARNINGS.md) — grep here first
2. [`ghidra_snapshot.json`](ghidra_snapshot.json) — search by
   function name or VA
3. [`TOOLS.md`](TOOLS.md) § "ghidra" verbs — xrefs, call-graph,
   decomp-cache, struct-diff

### "I want to run tool X"

1. [`TOOLS.md`](TOOLS.md) — find the CLI verb
2. Or [`SCRIPTS.md`](SCRIPTS.md) for standalone scripts
3. `azurik-mod <verb> --help` for detailed flags

### "I want to debug why a shim / patch isn't working"

1. [`AGENT_GUIDE.md`](AGENT_GUIDE.md) § "known landmines"
2. `azurik-mod shim-inspect <pack>` — view compiled bytes
3. `azurik-mod plan-trampoline 0x<VA>` — sanity-check the hook
   site
4. `azurik-mod verify-patches <iso>` — confirm bytes landed

### "I want to ship a plugin pack"

1. [`PLUGINS.md`](PLUGINS.md) — contract + skeleton

---

## Out-of-tree references

None — everything needed to develop on this repo lives inside it.
Ghidra is the only external tool you might want running alongside;
connect it to the MCP bridge and the `ghidra-*` CLI verbs will
talk to it.
