# Azurik XBE Analysis Tools

Tools for reverse-engineering and verifying Azurik's `default.xbe` binary,
used during development of the 60 FPS unlock and other patches.

## Scripts

### `scan_xbe_constants.py`

The primary verification tool. Reads the XBE binary directly, parses its
section headers, and searches `.rdata`, `.data`, and `.text` for all known
frame-rate-dependent constant patterns (float 1/30, float 30.0, double 1/30,
float 1/6, etc.). Cross-references every hit against the known patch list
and reports any unpatched instances.

```
python scan_xbe_constants.py [path/to/default.xbe]
```

### `scan_int30_instructions.py`

Scans the `.text` code section for x86 instructions that use immediate
values 30 (0x1E) or 60 (0x3C) — `CMP`, `MOV`, `PUSH` — which may
indicate VBlank counters, frame caps, or newly introduced 60-constants.
Also decodes x87 `FMUL/FADD/FLD m32|m64` references to the canonical
FPS anchors (`0x1983E8`, `0x1A2650`, `0x1A28C8`, `0x1A2740`, `0x1A2750`).

```
python scan_int30_instructions.py [path/to/default.xbe]
```

### `scan_frame_counters.py`

Dumps every disp32 xref to known per-frame globals (`DAT_001a9c0c`,
`DAT_001be36c`, `DAT_0038dd14`, …) so frame-cadence logic (e.g. "tick
twice as often at 60fps") can be audited at a glance.

```
python scan_frame_counters.py [path/to/default.xbe]
```

### `scan_ghidra_hexdump.py`

Parses raw hex dump text output (e.g. from Ghidra MCP `memory_read`) and
searches for timing constant byte patterns. Useful for verifying specific
memory regions without needing the full XBE file.

```
python scan_ghidra_hexdump.py hex_dumps/hex_198A80.txt 0x198A80
```

## `hex_dumps/`

Raw hex dump text files from Ghidra memory reads of `.rdata` regions.
Each file covers a 512-byte window starting at the VA in its filename:

| File              | VA Range                |
|-------------------|-------------------------|
| `hex_198280.txt`  | `0x198280` – `0x198480` |
| `hex_198480.txt`  | `0x198480` – `0x198680` |
| `hex_198680.txt`  | `0x198680` – `0x198880` |
| `hex_198880.txt`  | `0x198880` – `0x198A80` |
| `hex_198A80.txt`  | `0x198A80` – `0x198C80` |
| `hex_198C80.txt`  | `0x198C80` – `0x198E80` |

## XBE Section Mappings

For reference, the Azurik XBE section layout (VA start → file offset):

| Section  | VA Start   | Raw Start  |
|----------|------------|------------|
| `.text`  | `0x011000` | `0x001000` |
| `BINK`   | `0x1001E0` | `0x0F01E0` |
| `D3D`    | `0x11D5C0` | `0x118000` |
| `DSOUND` | `0x135460` | `0x12FE60` |
| `XGRPH`  | `0x154BA0` | `0x14F5A0` |
| `D3DX`   | `0x168680` | `0x163080` |
| `XPP`    | `0x187BA0` | `0x1825A0` |
| `.rdata` | `0x18F3A0` | `0x188000` |
| `.data`  | `0x1A29A0` | `0x19C000` |

Use `va_to_file()` from [`azurik_mod.patching.xbe`](../../azurik_mod/patching/xbe.py) for automated conversion.  All three scanners import `azurik_mod.patches.fps_unlock.FPS_PATCH_SITES` so the "already patched" markers cannot drift out of sync with the patch definitions.
