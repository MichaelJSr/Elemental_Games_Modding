# SCRIPTS.md — standalone script reference

Scripts under `scripts/` are one-off utilities that don't fit the
`azurik-mod` CLI shape.  Three reasons a script lives here instead
of in `azurik_mod/`:

1. **Side-channel I/O** — talks to qcow2 / FATX / raw disk images.
2. **Research-mode** — used during a specific RE campaign; not part
   of the user-facing toolchain.
3. **One-shot generators** — produce a file that gets committed,
   then aren't re-run.

When a script matures into a recurring workflow, migrate it into
`azurik_mod/xbe_tools/` as a proper CLI verb and delete the script.

---

## `scripts/` — top-level utilities

### `extract_save.py`
Pull Azurik save files out of an xemu qcow2 HDD image.  Walks the
FATX partition at `E:\UDATA\4d530007\` and dumps every `.sav` +
`.xbx` into `scripts/save_data/<slot_id>/`.

```bash
# Copy the qcow2 next to this script, then:
cd scripts && python extract_save.py
```

**When to use**: you have a qcow2 with a real save and want to
feed the files into `azurik-mod save inspect` or the
`key_recover` tool.

**Known issues**: assumes the xemu default partition offsets
(`0xABE80000` for E:\).  If xemu's HDD layout changes upstream,
the offsets may need updating — see `scripts/extract_save.py`
constants at the top.

---

### `xbr_parser.py`
The original `.xbr` format parser.  Predates the CLI
`azurik-mod xbr inspect` verb and still has capabilities the CLI
doesn't expose:

- `--stats`  — full tag histogram + top-10 largest entries
- `--strings <tag>`  — quality-filtered strings per TOC tag
- `--toc`  — raw table-of-contents dump
- `--patch`  — byte-level modification (one-off use only)

```bash
python scripts/xbr_parser.py gamedata/town.xbr --stats
python scripts/xbr_parser.py gamedata/a1.xbr --strings node
```

**When to use**: research-mode XBR inspection that doesn't fit
any CLI verb.  For routine usage, prefer:

- `azurik-mod xbr inspect` for per-record decoding
- `azurik-mod level preview` for level-specific summaries

---

### `gen_kernel_hdr.py`
Generates `shims/include/azurik_kernel.h` from the xboxkrnl
ordinal table.  Emits one `extern` per kernel import Azurik
could plausibly use (~151 entries).  The output is committed to
git; regenerate only when adding a new kernel function to the
platform.

```bash
python scripts/gen_kernel_hdr.py > shims/include/azurik_kernel.h
```

**When to use**: you need to extend the kernel-import allow-list
and want a fresh header.  See `docs/D1_EXTEND.md` for the
runtime resolution path that consumes this header.

---

## `scripts/analysis/` — RE research tools

Four scripts used during the 60 FPS unlock campaign + preserved as
reference implementations.  All read `default.xbe` directly without
going through the CLI's section-aware helpers.

### `scan_xbe_constants.py`
Verification pass for FPS-relevant float constants.  Searches
`.rdata` / `.data` / `.text` for known frame-rate anchor values
(`1/30`, `30.0`, `double 1/30`, `1/6`) and cross-references each
hit against the FPS patch list.

```bash
python scripts/analysis/scan_xbe_constants.py default.xbe
```

**Current status**: superseded by `azurik-mod xbe find-floats`
for everyday use but kept as a regression harness for the
60 FPS coverage tests.  Don't delete — `tests/test_fps_coverage.py`
expects its semantics.

### `scan_int30_instructions.py`
Scans `.text` for x86 instructions with immediate values 30 (0x1E)
or 60 (0x3C) — `CMP imm8`, `MOV imm8`, `PUSH imm8` — to find
frame-rate-related constants that wouldn't show up as `.rdata`
floats.

```bash
python scripts/analysis/scan_int30_instructions.py default.xbe
```

**When to use**: adding a new FPS constant or checking for
overlooked frame-cadence logic.

### `scan_frame_counters.py`
Lists disp32 xrefs to known per-frame globals (DAT_001A9C0C,
DAT_001BE36C, DAT_0038DD14, etc.).  Useful for "tick-twice-as-often"
audits at 60 FPS.

```bash
python scripts/analysis/scan_frame_counters.py default.xbe
```

### `scan_ghidra_hexdump.py`
Converts Ghidra's hex-dump copy-paste format into raw bytes.
Obsolete now that `azurik-mod xbe hexdump` exists but kept for
research sessions where copy-pasting from Ghidra is easier than
running the CLI.

```bash
python scripts/analysis/scan_ghidra_hexdump.py < pasted.txt
```

---

## Vanilla entity-values reference

`azurik_mod/config/entity_values.json` (canonical — read by the
tools directly) holds the vanilla values for every editable config
entity.  Use it as a schema reference when hand-authoring a mod
JSON; `azurik-mod mod-template --iso <iso>` is the preferred way to
regenerate it against a specific ISO.

*(An earlier duplicate at ``scripts/configs/entity_values.json``
was removed in the April 2026 cleanup pass — nothing read from it
and it drifted over time.  Point at the ``azurik_mod/config/``
copy instead.)*

---

## Analysis hex-dumps (`scripts/analysis/hex_dumps/`)

Committed hex-dumps of interesting regions captured during
specific RE sessions.  Grep these files rather than re-running
`xbe hexdump` when investigating historical state.

---

## Adding a new script

Before adding a new `scripts/` entry, consider:

1. **Could this be a CLI verb?**  If it's useful more than once,
   it should be.  Move it to `azurik_mod/xbe_tools/<topic>.py` and
   register a subparser in `azurik_mod/cli.py`.
2. **Is it permanently research-mode?**  If yes, add an entry
   here documenting what it does and why.
3. **Does it need to keep working?**  If yes, add a regression
   test in `tests/` that exercises its output against a committed
   fixture.  Scripts with no tests rot quickly.

All scripts should have a proper module docstring explaining the
*purpose* and *contract* (inputs, outputs, assumptions), not just
"what it does".
