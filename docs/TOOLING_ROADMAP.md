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

Extend `shims/toolchain/new_shim.sh` to:

- Ask the user for the hook-site VA
- Fetch that function's signature / calling convention from the
  open Ghidra instance via MCP
- Generate a correctly-annotated C template (`__attribute__((stdcall))`,
  proper register-clobber list, matching return type)
- Pre-fill the feature folder with a working `__init__.py`
  referencing a plausible `TrampolinePatch` site

```bash
shims/toolchain/new_shim.sh qol_speedy_boot --hook 0x5F6E5
```

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

## Tier 3 — Speculative (moderate or delayed ROI)

### 11. RE session recorder

Capture every Ghidra MCP call + response during an exploration
session to `docs/re-sessions/<date>.md` as an annotated journal.
Makes "what did we figure out last month?" a grep instead of a
transcript archaeology expedition.

### 12. Level XBR diff tool

`azurik-mod xbr diff w1.xbr w1_modded.xbr` — structural diff of
two level files showing added/removed entities, moved portals,
changed pickups.  Essential if multi-file mods ever become a
thing.

### 13. Bink movie metadata dumper

Parse `movies/*.bik` headers: resolution, frame count, duration,
audio codec.  Low code cost, occasional RE value when
rebalancing boot-time cutscenes.

### 14. Audio asset dump (from `wave` tags in `fx.xbr`)

Extract the embedded audio blobs from `fx.xbr`'s `wave` TOC
entries.  Unlocks sound-replacement mods.  Not useful until
someone decodes the wave header format.

### 15. Ghidra snapshot exporter

Dump every function name / label / comment / struct from the
open Ghidra project to a JSON file committed in
`docs/ghidra-snapshot.json`.  Gives us:

- Offline reference when Ghidra isn't running
- Diff over time (did someone auto-analyze-overwrite my nice
  names?)
- Input to the coverage-report tool (#2) when not attached to
  a live MCP

Risk: snapshot file could become massive (10k+ functions).
Probably scope to just named + commented symbols.

### 16. Plugin-pack distribution

Turn each feature folder into a PyPI-installable plugin (entry-
point group `azurik_mod.patches`) so third-party modders can
ship their packs as standalone installables rather than
upstream PRs.

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
| 6 | Shim scaffolder w/ ABI      | 6     | 2    | 8   | planned  |
| 7 | XBR layout inspector        | 5     | 3    | 7   | **shipped** |
| 8 | Entity diff                 | 4     | 1    | 7   | **shipped** |
| 9 | Test selector               | 4     | 1    | 7   | **shipped** |
|10 | VA-drift pin helper         | 3     | 1    | 6   | **shipped** |
|11 | RE session recorder         | 5     | 4    | 5   | maybe    |
|12 | Level XBR diff              | 3     | 2    | 5   | maybe    |
|13 | Bink metadata               | 2     | 1    | 4   | maybe    |
|14 | Audio asset dump            | 3     | 4    | 3   | speculative |
|15 | Ghidra snapshot exporter    | 5     | 2    | 6   | maybe    |
|16 | Plugin pack distribution    | 4     | 3    | 5   | speculative |

The top three shipped today deliver ≥25 minutes of saved time
per RE session, based on real measurement against the
conversation transcript.
