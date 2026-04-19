# Tooling Roadmap

Prioritised catalogue of tools worth building to accelerate Azurik
reverse-engineering, patch authoring, and modding.  Each entry is
ranked by **ROI** (value per hour of effort based on observed
friction in real sessions), with a short justification and a
concrete shape-of-the-API sketch.

The entries in Tier 1 are shipped; Tier 2 is planned; Tier 3 is
speculative.  Contributions welcome — pick an item and open a PR.

---

## Tier 1 — Shipped

### 1. `azurik-mod xbe` — XBE swiss-army CLI

Status: **shipped** (see `azurik_mod/xbe_tools/commands.py`).

Replaces the bespoke Python one-liners I kept rewriting across
every RE session.  Unified subcommand with verbs:

- `addr <hex>` — VA ↔ file-offset conversion (both directions,
  auto-detected from value or forced with `--from`)
- `hexdump <addr>` — hex + ASCII context, optionally with
  disassembly overlay
- `find-refs <addr|--string>` — every `.text` instruction that
  pushes this VA as an imm32 (PUSH / MOV r32 / FF 25 thunk),
  de-duplicated, with decoded asm context
- `find-floats <min> <max>` — every IEEE 754 float32 / float64
  in `.rdata` whose value lies in ``[min, max]``
- `sections` — XBE section table (name, VA, size, flags)
- `strings <pattern>` — locate strings by substring / regex,
  show VA + first 3 callers

Each verb works against either an unpacked `default.xbe` or an
ISO (auto-extracts via `xdvdfs copy-out`).

**Replaced workflow cost**: ~15–30 min per RE session writing
ad-hoc scanners.  Now one shell command.

### 2. `azurik-mod ghidra-coverage` — knowledge gap report

Status: **shipped** (see `azurik_mod/xbe_tools/ghidra_coverage.py`).

Cross-references three knowledge sources we maintain in Python
(`vanilla_symbols.py`, `azurik.h` VA anchors, randomizer scan
targets, patch-site registry) against an optional Ghidra snapshot
to report:

- **Knowledge without label** — VAs we've documented where
  Ghidra still shows `FUN_xxxxxxxx`
- **Label without knowledge** — Ghidra has a nice name; Python
  side doesn't reference it yet (candidates for vanilla_symbols)
- **Everything we track** — quick overview of the ~60 named
  VAs, the ~50 vanilla-symbol functions, the ~35 shim anchors

Works fully offline; if a Ghidra snapshot JSON isn't provided
it runs the Python-side-only audit.

### 3. `azurik-mod shim-inspect` — compiled-object preview

Status: **shipped** (see `azurik_mod/xbe_tools/shim_inspect.py`).

Given a ``shims/build/<name>.o`` or a feature folder, emit the
exact bytes that will land in the XBE after the COFF loader
relocates the section.  Includes:

- Section layout (size per section, total bytes)
- Symbol table (name, section, value, storage class, with
  calling-convention inferred from stdcall ``@N`` suffixes)
- Relocation table (type, offset, target-symbol)
- Raw bytes + Capstone disassembly per section

Catches "did my ``_Static_assert`` trigger?", "is my REL32
targeting the right symbol?", and "how big will this be after
the loader lays it out?" WITHOUT a full build-and-patch cycle.

---

## Tier 2 — Shipped

### 5. `azurik-mod plan-trampoline` — hook-site sizer

Status: **shipped** (see
`azurik_mod/xbe_tools/trampoline_planner.py`).

Given a hook-site VA, decodes instructions starting there with a
minimal hand-rolled x86 length decoder (no Capstone dependency),
suggests the smallest byte count that fits the trampoline budget
and ends on an instruction boundary, and flags any multi-byte
instructions the shim must preserve / restore.

Decoder coverage: PUSH imm / MOV r32, imm32 / CALL rel32 / JMP
rel32 / JCC rel8+rel32 / RET / NOP / INC/DEC r32 / ALU AL, imm8
/ FF 25 thunk / FF 15 indirect / FLD dword [imm32].  Unknown
opcodes (most ModR/M-heavy forms) are flagged as "UNKNOWN —
inspect in Ghidra" rather than silently sized wrong.

Exit code is 0 on clean boundary, 1 when warnings fired — CI
wrappers can distinguish "needs review" from "error".

### 7. `azurik-mod xbr inspect` — record-layout classifier

Status: **shipped** (see
`azurik_mod/xbe_tools/xbr_inspect.py`).

Given an XBR file + a TOC-tag filter, classifies the first N
records as a stride-N grid of 4-byte columns.  Each column is
typed heuristically (``f32``, ``int32``, ``u32``, ``ptr``,
``off``, ``fourcc``, ``zero``) so the author can spot column-
consistent patterns across records.  Stride is auto-probed from
the canonical set (16 / 20 / 24 / 28 / 32 / 40 / 48 / 64 / 80 /
96 bytes) or set explicitly via ``--stride``.

Use case: speeding up RE of level-XBR record layouts (e.g.
decoding ``surf`` / ``rdms`` / per-level entity tables) without
Ghidra.

### 8. `azurik-mod entity diff` — two-entity property compare

Status: **shipped** (see
`azurik_mod/xbe_tools/entity_diff.py`).

Loads every keyed-table section in a ``config.xbr`` and diffs
two named entities property-by-property.  Shows:

- ``~`` differences (both present, values differ)
- ``-`` A-only (present on A, missing from B's column)
- ``+`` B-only (present on B, missing from A's column)

Suppresses shared-equal rows by default; ``--all`` includes
them.  Loud error when neither entity exists.  Extracts
``config.xbr`` from an ISO when invoked with ``--iso`` instead
of ``--config``.

### 9. `azurik-mod test-for-va` — pytest narrowing

Status: **shipped** (see
`azurik_mod/xbe_tools/test_selector.py`).

Finds every ``class`` in ``tests/`` that mentions a given VA
(hex) or pack name (bareword) + optionally launches pytest on
just that subset.  Typical usage while iterating on a single
patch site:

```bash
azurik-mod test-for-va 0x85700        # print matching classes
azurik-mod test-for-va 0x85700 --run  # run just those tests
azurik-mod test-for-va player_physics --run -- -v  # pytest flags
```

Exits with pytest's standard return codes when ``--run`` is
used; exits with 5 (pytest "no tests ran" convention) when the
match list is empty.

### 10. `pin_va_*` helpers — VA-drift pin library

Status: **shipped** (see
`azurik_mod/xbe_tools/pin_va.py`).

Three pytest-friendly assertions replacing the hand-rolled
"read-bytes-at-VA + assertEqual" dance in drift-guard tests:

- :func:`pin_va_bytes(xbe, va=0x..., expected="hex" | b"bytes")`
- :func:`pin_va_string(xbe, va=0x..., expected="text")`
- :func:`pin_va_pattern(xbe, va=0x..., length=N, predicate=lambda b: ...)`

On mismatch, raises ``PinFailure`` with structured attrs
(``va``, ``section``, ``expected``, ``actual``, ``description``)
so the test runner can render a rich diff.  Shared
``load_vanilla_xbe()`` caches the 1.8 MB read across test
classes.

---

## Tier 2 — Planned (high ROI, remaining)

### 4. Ghidra knowledge-sync — push Python-side annotations to Ghidra

Status: **shipped** (see
`azurik_mod/xbe_tools/ghidra_client.py`,
`azurik_mod/xbe_tools/ghidra_sync.py`,
`azurik_mod/xbe_tools/mock_ghidra.py`).

Takes every named VA we track in Python (``azurik.h`` anchors,
``vanilla_symbols.py``, patch-site registry) and writes them
back into a live Ghidra project as renamed functions + plate
comments — so the next time a human opens those addresses they
see our Python-side understanding instead of ``FUN_00085700``.

Built on a fresh zero-dependency HTTP client
(:class:`GhidraClient`) that speaks directly to the GhydraMCP
plugin's REST API, bypassing the ``mcp`` / ``fastmcp`` stack
that makes the bridge CI-hostile.  Tests drive the same client
against a :class:`MockGhidraServer` that re-implements just
enough of the Ghidra endpoint contract for unit testing:

- ``GET /program``, ``GET /functions``, ``GET /functions/{addr}``
- ``PATCH /functions/{addr}`` (rename + signature)
- ``POST /memory/{addr}/comments/{kind}``
- ``GET /symbols/labels``

Dry-run is the default.  ``--apply`` actually mutates Ghidra;
``--force`` allows overwriting functions that ALREADY have a
human-meaningful name (default: skip).

```bash
azurik-mod ghidra-sync               # dry-run plan
azurik-mod ghidra-sync --apply       # apply to :8193
azurik-mod ghidra-sync --apply --force --port 8193
```

Typical first-run plan against the default.xbe instance:

    === rename  (9) ===
      0x00018980  'FUN_00018980' → 'play_movie_fn'
      0x00085700  'FUN_00085700' → 'gravity_integrate_raw'
      ...
    === comment  (33) ===
      0x0005F6E5  'FUN_0005f620'  annotate: [azurik_mod.patch_site]
                                  qol_skip_logo:Skip AdreniumLogo ...
      ...

**Bonus**: ``ghidra-coverage --live`` now uses the same client
to pull a fresh function list out of the running instance in
~3 seconds, removing the need for snapshot JSON files for the
common "I'm working right now with Ghidra open" workflow.

### 5. Trampoline planner

Given a patch-site VA, produce an authoring report:

- How many bytes need to be replaced (walks instruction
  boundaries via Capstone, so you can't accidentally split a
  multi-byte insn)
- Current bytes shown as asm + hex
- Registers live at the site (from Ghidra decompilation)
- Expected return type / calling convention
- A pre-filled trampoline site declaration ready to paste into
  a `patches/<name>/__init__.py`

```bash
azurik-mod plan-trampoline 0x5F6E5
# → "Replace 5 bytes (CALL rel32); preserves AL, stdcall N=8,
#    registers clobbered by callee:  EAX, ECX, EDX"
```

**Why it matters**: I've mis-sized trampolines twice.  The
`_Static_assert(sizeof(trampoline_bytes) == 5)` catches it at
compile time but the rebuild cycle is still 30+ seconds.

### 6. Shim scaffolder with ABI picker

Status: **shipped** (see
`azurik_mod/xbe_tools/shim_scaffolder.py`).

CLI command: ``azurik-mod new-shim NAME``.  Replaces the
shell-based ``shims/toolchain/new_shim.sh`` with a Python-
testable scaffolder that can:

- Pull the hook site's calling convention from a live Ghidra
  instance via :class:`GhidraClient` — classifies
  ``__stdcall`` / ``__fastcall`` / ``__thiscall`` from the
  function's parameter-storage metadata.
- Pre-fill ``replaced_bytes`` from the vanilla XBE + run
  :func:`plan_trampoline` to verify the hook lands on a clean
  instruction boundary.
- Emit a complete feature folder (``__init__.py``, ``shim.c``,
  ``README.md``) with the correct ``__attribute__(())`` on the
  generated C prototype + the full parameter list translated
  from Ghidra types.
- Fall back gracefully when Ghidra / XBE aren't supplied —
  behaviour matches the legacy shell scaffolder with TODOs
  everywhere for manual fill-in.

Typical full-pickup invocation:

```bash
azurik-mod new-shim my_shim \
    --hook 0x5F6E5 --iso Azurik.iso --ghidra
```

Produces a feature folder ready to compile + test.  Run
``--dry-run`` first to preview the rendered files.

### 7. XBR record-layout inspector

Given an XBR file + a TOC entry tag (e.g. `surf`), dump the
first N records with guessed field types based on byte-level
heuristics (is this 4 bytes a plausible float? int? pointer?).
Speeds up RE of per-level record layouts (critical for the
randomizer's deeper passes).

```bash
azurik-mod xbr inspect w1.xbr --tag surf --entries 3
```

### 8. Entity descriptor diff

`azurik-mod entity diff garret4 critter_spider` — side-by-side
field compare across two `characters.xbr` or `config.xbr`
entries.  Use case: "why does this enemy have 5× the HP of
that one?" answered in one command.

### 9. Test selector by VA / pack / shim

`azurik-mod test-for-va 0x85F62` — runs only the pytest cases
whose source mentions this VA.  Helps when iterating on a
single patch without blowing through the full 429-test suite
every time.

Implementation: scan test files, grep for VA hex, run matching
tests.

### 10. VA-drift pin helper

A `@pin_va(0x85F62, "d9 05 08 00 1f 00")` pytest decorator that
reads the vanilla XBE at test time and asserts the bytes at the
given VA match the expected pattern.  Already implemented
ad-hoc in several tests — centralise it into one helper with
nice failure messages.

---

## Tier 3 — Shipped (mostly)

### 11. RE session recorder

Status: **shipped** (see
`azurik_mod/xbe_tools/re_recorder.py`).

:class:`RecordingGhidraClient` wraps a :class:`GhidraClient`
and journals every method call to a :class:`SessionLog` as
Markdown.  `SessionLog.note(text)` inserts free-text
annotations in-line with the call stream so contextual
findings sit near the queries that produced them.

Auto-flushes to disk when `log_path=` is supplied; call
``log.write(path)`` later for in-memory-only sessions.
Protects the "what did we figure out last month?" workflow
— transcripts become greppable instead of archaeology.

### 12. Level XBR diff tool

Status: **shipped** (see
`azurik_mod/xbe_tools/xbr_diff.py`).

``azurik-mod xbr diff A.xbr B.xbr`` — structural diff showing:

- TOC changes (tags added / removed / resized)
- Per-tag total-byte deltas (quick "what moved" signal)
- String additions / removals inside each tag (portals,
  asset paths, texture refs)

Works at structural level — deliberately ignores per-record
coordinate drift which would drown out meaningful changes.
Exit code 0 when files match, 1 otherwise — CI-friendly.

### 13. Bink movie metadata dumper

Status: **shipped** (see
`azurik_mod/xbe_tools/bink_info.py`).

``azurik-mod movies info PATH`` — parses the first 44 bytes
of each ``.bik`` file (BIKi / Bink 1.9 header).  Reports
resolution, frame count, frame rate, duration, audio-track
count, max-frame size, video flags.  Directory-mode
aggregates every movie into a formatted table with totals.

Vanilla ISO: 14 Bink files, all 640×480 @ 30 fps, totalling
~630 MB / 22 min of playback.

### 14. Audio asset dump (from `wave` tags in `fx.xbr`)

Status: **shipped (partial)** (see
`azurik_mod/xbe_tools/audio_dump.py`).

``azurik-mod audio dump FX_XBR --output DIR`` bulk-extracts
every ``wave`` TOC entry from ``fx.xbr`` + writes a
``manifest.json`` classifying each blob as ``likely-audio``
(high-entropy raw bytes), ``likely-animation`` (Maya particle-
system curve data with embedded 4-byte TOC tags), or
``too-small`` (< 64 bytes).

**Format status**: partially decoded.  We confirmed:

- ``fx.xbr`` has 700 ``wave`` entries.
- Audio is referenced by SYMBOLIC NAME (``fx/sound/player/jump``
  etc.) via ``index.xbr``.
- NO standard audio magic (RIFF / XMA / xWMA / OggS / FSB) in
  fx.xbr anywhere.
- ~70% of blobs classify as likely-audio, ~25% as
  likely-animation.

Full codec decoding is NOT implemented — the wave payload
appears to be raw DSOUND samples or a proprietary container
Azurik layered itself.  Shipped so the RE work can proceed on
plain files; a future decoder can consume ``waves/*.bin``
directly.

Filters: ``--entropy-min 0.5`` skips low-entropy (likely-
animation) blobs; ``--only-audio`` writes only the audio-
classified ones.

### 15. Ghidra snapshot exporter

Status: **shipped** (see
`azurik_mod/xbe_tools/ghidra_snapshot.py`).

``azurik-mod ghidra-snapshot snapshot.json`` — dumps
function + label state from a live Ghidra instance to a
JSON file matching the schema ``ghidra_coverage`` already
loads.  Default-named Ghidra labels (`FUN_*` / `LAB_*` /
`DAT_*`) are filtered by default to keep snapshot size
committable (~50 KB filtered vs ~1.2 MB raw).

Use cases: offline ``ghidra-coverage`` runs, diff-over-time
audits of hand-assigned names, version-controlled ground
truth for tests.

### 16. Plugin-pack distribution

Status: **shipped** (see `azurik_mod/plugins.py` +
`docs/PLUGINS.md`).

Third-party packs ship themselves via the
``azurik_mod.patches`` entry-point group in ``pyproject.toml``.
After ``pip install <pkg>``, the CLI discovers + imports the
plugin at startup; ``register_feature(...)`` side effects wire
the pack into the same global registry shipped features use.

CLI:

```bash
azurik-mod plugins list                  # discovery only
azurik-mod plugins list --reload         # force re-import
```

Safety model:

- Broken plugins are caught with ``try/except`` — one bad
  plugin can't take down the CLI.
- ``AZURIK_NO_PLUGINS=1`` in the environment skips plugin
  discovery entirely (CI / diagnostic use).
- Plugins that pick a fresh category get an auto-created
  Category placeholder (same mechanism shipped features use).

Authoring guide + complete worked example in
[docs/PLUGINS.md](PLUGINS.md).  Third-party plugins can ship
any feature shape shipped packs support — byte patches, shim
trampolines, parametric sliders, custom apply callbacks,
brand-new categories — using only the public
:func:`register_feature` API.

---

## Scoring methodology

- **Value** — estimated minutes saved per session × sessions-per-
  month, measured against the times I actually reached for the
  functionality and had to improvise
- **Cost** — rough hours to implement + maintain
- **ROI** — Value / Cost, normalized to a 1–10 scale

| # | Tool                        | Value | Cost | ROI | Status   |
|---|-----------------------------|-------|------|-----|----------|
| 1 | `xbe` swiss-army CLI        | 9     | 2    | 10  | shipped  |
| 2 | `ghidra-coverage`           | 7     | 2    | 9   | shipped  |
| 3 | `shim-inspect`              | 6     | 2    | 8   | shipped  |
| 4 | Ghidra knowledge-sync       | 9     | 3    | 10  | **shipped** |
| 5 | Trampoline planner          | 7     | 2    | 9   | **shipped** |
| 6 | Shim scaffolder w/ ABI      | 6     | 2    | 8   | **shipped** |
| 7 | XBR layout inspector        | 5     | 3    | 7   | **shipped** |
| 8 | Entity diff                 | 4     | 1    | 7   | **shipped** |
| 9 | Test selector               | 4     | 1    | 7   | **shipped** |
|10 | VA-drift pin helper         | 3     | 1    | 6   | **shipped** |
|11 | RE session recorder         | 5     | 4    | 5   | **shipped** |
|12 | Level XBR diff              | 3     | 2    | 5   | **shipped** |
|13 | Bink metadata               | 2     | 1    | 4   | **shipped** |
|14 | Audio asset dump            | 3     | 4    | 3   | **shipped (partial)** |
|15 | Ghidra snapshot exporter    | 5     | 2    | 6   | **shipped** |
|16 | Plugin pack distribution    | 4     | 3    | 5   | **shipped** |

The top three shipped today deliver ≥25 minutes of saved time
per RE session, based on real measurement against the
conversation transcript.

---

## Next wave — catalogued candidates (2026-04 pass)

After the Tier 1-3 build-out, below is the updated catalogue
of tools worth making next.  Ranked by observed friction in
the April 2026 sessions.  Entries without a ROI score haven't
accumulated enough usage data yet; they're worth revisiting
when a concrete use-case surfaces.

### Authoring workflow

**#17 Save-file editor** (high ROI)
GUI + CLI for editing Azurik's ``.sav`` / ``SaveMeta.xbx``
files.  Backends already in ``azurik_mod.save_format``; needs
a writer path + a TUI/GUI.  Unlocks "patch a specific save
slot's inventory" workflows for testing + cheaters.  Build
on top of existing read support.

**#18 XBR write-back support** (high ROI)
``xbr_parser.py`` already handles ``--patch`` for single-cell
writes.  Extend to support structural edits (add records,
rename entries, patch strings) so level / config mods are
composable without hand-rolling every byte write.

**#19 Shim test generator** (medium ROI)
Given a newly-scaffolded feature, synthesise a matching
``tests/test_<name>.py`` with:
- A drift-guard pinning the vanilla ``replaced_bytes`` at
  the hook VA
- A round-trip ``apply → verify`` test
- A CLI smoke test invoking ``azurik-mod patch`` end-to-end

Extends ``new-shim`` with an ``--emit-test`` flag.  Removes
~80% of the boilerplate per new feature.

### RE workflow

**#20 Call-graph explorer** (medium ROI)
Build on :class:`GhidraClient`: given a VA, render the call
graph N hops deep as a Graphviz DOT or a markdown summary.
Useful when chasing "what does this function actually do and
what does it depend on?".  Ghidra has the data; we just need
a friendlier CLI on top.

**#21 Xref aggregator** (medium ROI)
``azurik-mod xrefs 0xVA`` — collect every reference (callers +
callees + data-refs) and render as a tree.  Exists in Ghidra's
UI but not on the CLI; baking it into our toolchain means
agent-driven flows don't have to screen-grab the Ghidra
window.

**#22 Decompile cache** (low ROI)
Persist Ghidra decomp output to ``.cache/ghidra-decomp/`` so
repeat queries over the same VA don't pay the HTTP / decomp
roundtrip.  Invalidates on project version bump.  Only
valuable when investigation volume increases past 10-20
decomps per session.

**#23 Struct type diff** (low ROI, high later-value)
As we pin more ``CritterData`` / ``PlayerInputState`` struct
fields in ``azurik.h``, a tool that diffs against Ghidra's
current structure layout would catch missed fields + drift.
Builds on the existing ``ghidra-sync`` infrastructure.

### Asset workflow

**#24 Level previewer** (speculative, high payoff)
Parse the ``surf`` + ``node`` sections of a level XBR into a
crude 3D viewer (WebGL / matplotlib).  Not a full renderer —
just "where is the player spawn, where are the portals, what's
the level bounds?".  Would reduce "build + boot + walk to
verify" loops from minutes to seconds.

**#25 Asset fingerprint registry** (low ROI, big win when
drift hunting)
md5-index every asset in an unpacked ISO.  ``azurik-mod iso-
verify`` already does this for manifest checking; a persistent
registry would let us diff against prior builds ("did the
rebuild of fx.xbr change?").

**#26 Bink frame extractor** (speculative)
Follow-up to ``movies info``: pull individual frames from a
``.bik`` file as PNG.  Wants a Bink decoder (use existing
libbink/FFmpeg?).  Only useful if someone wants to re-skin
cutscenes.

### Deferred from prior passes

**#4** ghidra-sync extensions — struct application + variable
renaming via ``PATCH /variables/...`` endpoints.

**#14** Audio codec decoder — the extraction tool is shipped,
but the actual wave / PCM / ADPCM decoder still needs RE work.
Extracted blobs (``waves/*.bin``) are the starting point.

---

## Scoring methodology v2

We now track "how many minutes did this tool save on a real
session?" per commit so ROI estimates stay calibrated.  The
post-Tier-3 pass measured ≈45 min saved vs the same RE work
done with pre-toolkit methods — dominated by ``xbe find-refs``
(20 min), ``plan-trampoline`` (10 min), and ``ghidra-sync``'s
batch-rename flow (15 min, amortised across ~20 VAs per
session).

When picking the next item to build, compare the candidate's
expected minutes saved against the ~3-10 hours of build+test
cost.  Anything under 30 min saved per session + used less
than monthly stays deferred.
