# AGENTS.md — AI agent entry point

**Read this file first if you are an AI agent picking up work on this repo.**

This is a reverse-engineering and modding toolkit for the classic-Xbox
game **Azurik: Rise of Perathia**.  The code is ~60k lines of Python
with a tested test suite that must stay green.  The repo is structured
so an agent can arrive cold and be productive within 5 minutes of
orientation.

---

## Your three-minute orientation

1. **Run the test suite.**  It's the ground truth for what works.

   ```bash
   pip install -e .
   python -m pytest                            # ~2-3 min, should be all green
   ```

2. **Pick the entry-point doc that matches your task.**  Every task
   falls into one of four buckets; each has a dedicated guide:

   | Task                          | Read first                             |
   |-------------------------------|----------------------------------------|
   | Any code change on this repo  | [`docs/AGENT_GUIDE.md`](docs/AGENT_GUIDE.md)     |
   | Writing or patching a feature | [`docs/ONBOARDING.md`](docs/ONBOARDING.md)       |
   | Using a specific CLI tool     | [`docs/TOOLS.md`](docs/TOOLS.md)                 |
   | Running a standalone script   | [`docs/SCRIPTS.md`](docs/SCRIPTS.md)             |

3. **When stuck, check the doc-map:** [`docs/INDEX.md`](docs/INDEX.md).
   It lists every doc in the repo with a one-line summary.

---

## Hard rules (🛑)

- 🛑 **Never commit without running `python -m pytest` first.**  Every
  change that lands on `main` must keep the suite green.  If a test
  breaks, either fix it or explain why the test itself was wrong.
- 🛑 **Never commit these files: ISOs, EEPROMs, save dumps, qcow2
  images.**  `.gitignore` covers them; don't `git add -f` to work
  around it.  User saves contain per-console identity material.
- 🛑 **Never edit files in `azurik_mod.egg-info/`, `__pycache__/`, or
  `shims/build/`.**  All three are auto-generated.
- 🛑 **Never update git config, force-push, or skip pre-commit hooks
  unless the user explicitly asks.**

## Mandatory checkpoints (✅)

Before you consider any task done:

- ✅ `python -m pytest` is green (685+ tests, 1 skipped).
- ✅ Any new VA, struct field, or vanilla symbol is registered in
  both `azurik_mod/patching/vanilla_symbols.py` (if applicable)
  **and** `shims/include/azurik_vanilla.h`.  The
  `test_vanilla_thunks` / `test_va_audit` drift guards catch most
  but not all mismatches.
- ✅ Docs updated to reflect user-facing changes.  New CLI verb →
  add to `docs/TOOLS.md`.  New script → add to `docs/SCRIPTS.md`.
  New shim → add a `README.md` inside its feature folder.
- ✅ If you touched Ghidra-side state, re-run `azurik-mod
  ghidra-sync --apply --push-structs` and commit the refreshed
  `docs/ghidra_snapshot.json`.

---

## Finding things fast

- **Every CLI verb** → `docs/TOOLS.md` (31 subcommands, grouped by
  workflow: build / browse / verify / save / xbe / xbr / ghidra /
  shim / plugin).
- **Every standalone script** → `docs/SCRIPTS.md` (7 scripts in
  `scripts/` + `scripts/analysis/`).
- **Every feature (QoL patch, shim, randomizer pool)** →
  `azurik_mod/patches/<feature>/` with a one-page README per folder.
- **All reverse-engineering findings** → `docs/LEARNINGS.md`.
- **All VA anchors + function exports** →
  `shims/include/azurik.h` + `shims/include/azurik_vanilla.h`,
  both cross-referenced from `docs/ghidra_snapshot.json`.
- **`.xbr` data-file modding** (config.xbr cells, level XBRs)
  → the `azurik_mod/xbr/` package (document model + structural
  edit primitives) plus `azurik_mod/patching/xbr_spec.py`
  (declarative `XbrEditSpec` / `XbrParametricEdit` packs).  See
  [`docs/XBR_PACKS.md`](docs/XBR_PACKS.md) for authoring,
  [`docs/XBR_FORMAT.md`](docs/XBR_FORMAT.md) for the byte spec.
- **Ghidra-side state snapshot** →
  [`docs/ghidra_snapshot.json`](docs/ghidra_snapshot.json) (490
  named functions, 4,926 labels, 3 structs).  Regenerate via
  `azurik-mod ghidra-snapshot docs/ghidra_snapshot.json`.

---

## Common patterns & gotchas

### Pattern: add a new CLI verb
1. Implement the behaviour in an `azurik_mod/xbe_tools/<topic>.py`
   module.
2. Add a `cmd_<verb>` thin wrapper in
   `azurik_mod/xbe_tools/commands.py`.
3. Register the argparse subparser in `azurik_mod/cli.py`.
4. Document in `docs/TOOLS.md`.
5. Add a regression test in `tests/test_<topic>.py` (use the
   `MockGhidraServer` for anything Ghidra-touching).

### Pattern: add a new vanilla symbol
1. Verify the VA + ABI with Ghidra (decomp → confirm `RET N` vs
   `RET` for stdcall/cdecl, count stack-arg bytes).
2. Add a `register(VanillaSymbol(...))` block in
   `azurik_mod/patching/vanilla_symbols.py`.
3. Add the matching `extern` in `shims/include/azurik_vanilla.h`
   with the right `__attribute__((stdcall))` decoration.
4. `python -m pytest tests/test_vanilla_thunks.py
   tests/test_va_audit.py` — drift guards must pass.

### Gotcha: `_stricmp` vs `stricmp` naming
- PE-COFF always prefixes undefined references with `_`.
- If Ghidra shows the symbol as `__stricmp`, the C name is
  `_stricmp`, so `VanillaSymbol(name="_stricmp", ...)` generates
  mangled `__stricmp` which matches.
- If Ghidra shows `_strncmp`, C name is `strncmp`, so
  `VanillaSymbol(name="strncmp", ...)` generates mangled
  `_strncmp`.

### Pattern: add an XBR-side feature (data-file edit)
1. Confirm the target cell via `azurik-mod xbr inspect` or the
   GUI's XBR Editor page.
2. Create `azurik_mod/patches/<name>/__init__.py` declaring a
   `Feature` with `xbr_sites=(...)` — usually one
   `XbrParametricEdit` for a slider or `XbrEditSpec` for a fixed
   edit.  Leave `sites=[]` and `apply=lambda *_: None`.  See
   [`docs/XBR_PACKS.md`](docs/XBR_PACKS.md) and
   [`azurik_mod/patches/player_max_hp/`](azurik_mod/patches/player_max_hp/)
   for the canonical template (renamed from ``cheat_entity_hp``
   in round 12.1 — the legacy name still resolves through
   ``get_pack`` for backward compatibility).
3. Register the side-effect import in
   [`azurik_mod/patches/__init__.py`](azurik_mod/patches/__init__.py).
4. Add a regression test mirroring
   [`tests/test_player_max_hp.py`](tests/test_player_max_hp.py).
5. CLI users enable the pack via `--enable-pack <name>` on
   `randomize-full`; the GUI Patches page picks it up
   automatically once it appears in the registry.

### Gotcha: Ghidra prologue bytes
`tests/test_va_audit.py` pins an allow-list of first-byte
prologue patterns.  If you add a vanilla symbol whose first
byte isn't in the list, the test fails.  Either:
- The VA is wrong (land on mid-function code) — **fix the VA**,
- Or it's a legitimate unusual prologue — verify via disasm,
  then extend `_VALID_PROLOGUE_FIRSTBYTES` with a comment.

---

## Anatomy of a typical session

```
1. Read user's request + any cited files.
2. python -m pytest (verify baseline green).
3. Plan via TodoWrite (3-5 concrete steps).
4. Implement + test incrementally.
5. Run FULL test suite again.
6. Update docs touched by the change.
7. Commit with a descriptive message.
8. Don't push unless user asks.
```

---

## Where to find things that didn't make it into this file

- **Architecture + platform internals**: [`docs/SHIMS.md`](docs/SHIMS.md)
- **How to author a C shim end-to-end**: [`docs/SHIM_AUTHORING.md`](docs/SHIM_AUTHORING.md)
- **What every existing feature does**: [`docs/PATCHES.md`](docs/PATCHES.md)
- **Reverse-engineering notes**: [`docs/LEARNINGS.md`](docs/LEARNINGS.md)
- **Tool roadmap** (what's shipped vs planned): [`docs/TOOLING_ROADMAP.md`](docs/TOOLING_ROADMAP.md)
- **Save-file format investigation**: [`docs/SAVE_FORMAT.md`](docs/SAVE_FORMAT.md)
- **Decompile reference** (XBE section map + notable functions): [`docs/DECOMP.md`](docs/DECOMP.md)
- **Plugin authoring (third-party pack distribution)**: [`docs/PLUGINS.md`](docs/PLUGINS.md)
- **`.xbr` byte-level spec**: [`docs/XBR_FORMAT.md`](docs/XBR_FORMAT.md)
- **XBR-side feature authoring**: [`docs/XBR_PACKS.md`](docs/XBR_PACKS.md)

When in doubt, [`docs/INDEX.md`](docs/INDEX.md) is the complete
doc map.
