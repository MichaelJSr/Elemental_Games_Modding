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

## Tier 3 — Shipped

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

Status: **shipped** (see
`azurik_mod/xbe_tools/audio_dump.py`).

``azurik-mod audio dump FX_XBR --output DIR [--index-xbr IDX]``
bulk-extracts every ``wave`` TOC entry from ``fx.xbr``,
decodes the 20-byte audio header when present, wraps
recognised codecs in RIFF/WAVE so external tools can consume
them, and writes a ``manifest.json`` indexing every blob with
its decoded metadata.

**Format — pinned in the April 2026 RE pass**:

The 20-byte header that 100 of 700 wave entries carry
decomposes as::

    +0x00  u32  sample_rate    (8000 / 11025 / 22050 / 32000 / 44100)
    +0x04  u32  sample_count   (duration = count / rate)
    +0x08  u32  format_magic   (0x01000401 = mono 4-bit Xbox ADPCM)
    +0x0C  u32  reserved (0)
    +0x10  u32  reserved (0)
    +0x14  ...  codec payload

The ``format_magic`` dword splits byte-for-byte into
``channels = byte[0]``, ``bits_per_sample = byte[1]``,
``codec_id = byte[3]`` — matching what ``dump_waves`` surfaces
in the manifest + what the WAV wrapper uses to pick
``WAVE_FORMAT_XBOX_ADPCM`` (0x0069) vs ``WAVE_FORMAT_PCM``.

**Classification labels** (output in the manifest):

- ``xbox-adpcm``       — 20-byte header recognised; ``.wav``
                          wrapper emitted via RIFF ``WAVE_FORMAT_XBOX_ADPCM``
- ``pcm-raw``          — 8/16-bit linear PCM header; ``.wav``
                          wrapper emitted with ``WAVE_FORMAT_PCM``
- ``likely-audio``     — entropy ≥ 0.5, no recognised header;
                          raw ``.bin`` only
- ``likely-animation`` — Maya-particle-system curve data
                          (animation TOC tags in first 64 bytes
                          or low entropy)
- ``too-small``        — < 64 bytes of payload

**Naming** — pass ``--index-xbr path/to/index.xbr`` and the
manifest's recognised-codec entries pick up their symbolic
``fx/sound/<entity>/<key>`` names from the index.xbr string
pool, so RE sessions don't have to open both files side-by-side.

Vanilla ``fx.xbr`` breakdown (700 wave entries):

- 103 **xbox-adpcm** (header-decoded, .wav written)
- 448 likely-audio (no header, raw bytes only)
- 118 likely-animation (Maya particle-system data)
-  31 too-small

Filters: ``--entropy-min 0.5`` skips low-entropy blobs;
``--only-audio`` writes only audio-classified entries;
``--no-wav`` suppresses RIFF wrapping when you want raw
``.bin`` files only.

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
|14 | Audio asset dump            | 4     | 4    | 4   | **shipped** |
|15 | Ghidra snapshot exporter    | 5     | 2    | 6   | **shipped** |
|16 | Plugin pack distribution    | 4     | 3    | 5   | **shipped** |

The top three shipped today deliver ≥25 minutes of saved time
per RE session, based on real measurement against the
conversation transcript.

---

## Next wave — catalogued candidates (2026-04 pass)

**All ten items (#17 – #26) shipped in the April 2026 pass.**
Every entry below carries its current CLI surface + Python
module so readers can jump straight to the code; the design
notes are preserved as a record of the prioritisation that led
to the build-out.

### Authoring workflow

**#17 Save-file editor** — **SHIPPED**
`azurik-mod save edit <in> <out> --set <file>:<line>=<value>`,
driven by :mod:`azurik_mod.save_format.editor`.  Applies
declarative edits to text saves (``magic.sav`` / ``loc.sav`` /
``options.sav``) and copies the rest of the slot through
unchanged.  The signature-verification blocker from the April
2026 pass landed as the ``qol_skip_save_signature`` patch pack
(3-byte ``MOV AL, 1 ; RET`` at VA ``0x0005C990``) — edited
saves now load on a patched XBE without any key-recovery
ceremony; see ``docs/PATCHES.md`` for the full writeup.
Binary-save editing (inventory / position blobs with non-text
keys) still requires deeper RE and is tracked in
``docs/SAVE_FORMAT.md``.

**#18 XBR write-back support** — **SHIPPED**
`azurik-mod xbr edit <in> <out> --set-string 'old=new' --tag <T>`
(+ `--replace-bytes OFFSET:HEX`).  Conservative same-size
in-place string / byte replacement via
:mod:`azurik_mod.xbe_tools.xbr_edit`.  Full structural edits
(add records, grow string pool) deferred until the pool layout
is fully reversed.

**#19 Shim test generator** — **SHIPPED**
`azurik-mod new-shim <name> --emit-test` extends the scaffolder
to also write ``test_<name>.py`` with drift-guards for the
feature registration + ``replaced_bytes`` / hook-VA constants.
Edit the asserts as the feature solidifies — the goal is to
FORCE conscious diffs when a constant moves.

### RE workflow

**#20 Call-graph explorer** — **SHIPPED**
`azurik-mod call-graph <seed...> --depth N --direction
forward|reverse [--dot graph.dot]` in
:mod:`azurik_mod.xbe_tools.call_graph`.  BFS over the Ghidra
xref graph with Graphviz DOT rendering (``dot -Tpng g.dot``
gives you the picture).  Collapses intra-function CALLs onto
their enclosing functions so the graph stays legible.

**#21 Xref aggregator** — **SHIPPED**
`azurik-mod xrefs <VA> --direction in|out --depth N` in
:mod:`azurik_mod.xbe_tools.xref_aggregator`.  ASCII-tree dump
of callers/callees around a seed VA; handy when you want *"who
ultimately invokes gravity_integrate_raw?"* without leaving the
terminal.  JSON mode feeds structured tooling.

**#22 Decompile cache** — **SHIPPED**
`azurik-mod decomp-cache <stats|clear|get>` in
:mod:`azurik_mod.xbe_tools.decomp_cache`.  On-disk memoisation
of ``GhidraClient.decompile`` keyed by (program_id, VA); cache
lives under ``~/.cache/azurik-mod/decomps`` (XDG-respecting).
Saves 50–300 ms per repeat fetch which matters for batch
operations (struct-diff, call-graph, grep-over-decomps).

**#23 Struct type diff** — **SHIPPED**
`azurik-mod struct-diff [--verbose|--offline]` in
:mod:`azurik_mod.xbe_tools.struct_diff`.  Parses
``shims/include/azurik.h`` for ``typedef struct ... { ... }
NAME;`` blocks (with the ``+0xHH`` comment convention picked up
from both placement styles) and diffs against every struct in
the live Ghidra DTM.  Surfaces ``header_only`` /
``ghidra_only`` / ``size_mismatch`` / ``field_mismatch``
classes.

### Asset workflow

**#24 Level previewer** — **SHIPPED**
`azurik-mod level preview <xbr>` in
:mod:`azurik_mod.xbe_tools.level_preview`.  Pragmatic
structural preview — strings + plausible ``(f32,f32,f32)``
position triples per gameplay tag (``node``, ``surf``,
``rdms``, ``levl``, …) plus entry-count / byte-count totals.
A graphical 3D viewer is a natural follow-up; not blocking the
structured-data use case.

**#25 Asset fingerprint registry** — **SHIPPED**
`azurik-mod assets fingerprint <root> [--out FILE]` +
`assets fingerprint-diff <before> <after>` in
:mod:`azurik_mod.xbe_tools.asset_fingerprint`.  SHA-1
fingerprints for every file under a root (full hash ≤64 MiB,
sparse hash for larger).  Emit the JSON once, commit it beside
your mod, and diff later to see exactly what moved.

**#26 Bink frame extractor** — **SHIPPED (partial)**
`azurik-mod movies frames <bik> [--info|--dry-run]` in
:mod:`azurik_mod.xbe_tools.bink_extract`.  Metadata + per-frame
offset table always available; frame extraction shells out to
``ffmpeg`` (open-source Bink 1.x decoder) when it's on
``$PATH``.  Includes ``plan_frame_extraction`` so CI can dry-
run and surface *"ffmpeg not installed"* cleanly.

### Deferred from prior passes

**#4** ghidra-sync extensions — variable renaming via
``PATCH /variables/...`` endpoints.  Struct push + recreate
shipped in the April 2026 pass; the variable-rename side is
still open.

(The April 2026 audio pass closed out the original
``#14`` deferred item — ``xbox-adpcm`` header decoding + WAV
wrapping ship directly from ``audio dump`` now.  Decoding the
remaining 448 ``likely-audio`` entries that don't carry the
20-byte header is still open and tracked as a future RE
pass, not a tooling gap.)

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
