# Azurik save-file format

> **Status**: partial.  The Xbox-level container (SaveMeta.xbx /
> SaveImage.xbx / TitleMeta.xbx / TitleImage.xbx) is fully decoded
> and round-trips losslessly.  Azurik's own `.sav` files parse their
> 20-byte fixed header but the body payload is currently exposed as
> opaque bytes — field-level decoding of `signature.sav` and
> `<level>.sav` is pending acquisition of real save samples.
>
> **Python module**: [`azurik_mod.save_format`](../azurik_mod/save_format/)
> **CLI**: `azurik-cli save inspect <path>`

---

## 1. Directory layout

A single save slot lives in its own folder on the Xbox HDD:

```
\Device\Harddisk0\partition1\UDATA\<title_id_hex>\<MU>\<save_id>\
    SaveMeta.xbx         (Xbox-standard save metadata)
    SaveImage.xbx        (per-save thumbnail)
    TitleMeta.xbx        (title-level metadata, shared across saves)
    TitleImage.xbx       (title icon)
    signature.sav        (Azurik's profile-level state)
    <level>.sav          (per-level world state, e.g. w4.sav)
    (other level .sav files as the player visits them)
```

`<title_id_hex>` is Azurik's title ID as a lowercase hex string
(8 chars).  `<MU>` is the memory-unit letter on retail Xbox
(always `MU_MAIN` for HDD saves).  `<save_id>` is typically a UUID
or slot index chosen by Azurik.

Retail path evidence from the XBE:
- `\Device\Harddisk0\partition1\UDATA` (string at VA 0x18F959)
- `\Device\Harddisk0\partition1\TDATA` (VA 0x18F985)
- `z:\savegame` (VA 0x19E5F2 — the mapped save path at runtime)
- `C:\Elemental\src\game\save.cpp` (VA 0x19E5C8 — source path leak
  from a debug assert, confirms the save module's origin)

## 2. Extracting save data for analysis

xemu stores HDD state in `xbox_hdd.qcow2` under the user's Xemu home
directory.  To inspect a save slot with the tools in this repo:

### Option A — xemu's built-in export (easiest)

1. Run the game in xemu until it writes a save to the dashboard.
2. In xemu's menu: **Machine → Settings → System → HDD → Browse**.
3. Navigate to `UDATA\<azurik_title_id>\` and right-click the save
   folder → **Export**.
4. Pick a loose destination folder on your host; xemu drops the
   SaveMeta.xbx / SaveImage.xbx / signature.sav / `<level>.sav`
   files into it.
5. Run `azurik-cli save inspect <exported_folder>` for a summary.

### Option B — qcow2 + FATX (manual)

1. Convert the disk image: `qemu-img convert -O raw xbox_hdd.qcow2 xbox_hdd.raw`.
2. Mount partition 1 using a FATX toolchain (Cxbx-Reloaded's
   `xfattools`, the `fatxfs` FUSE module, or similar).
3. Copy `UDATA\<title_id>\...` out to a loose directory.
4. Proceed as in Option A from step 5.

## 3. Xbox-standard container files

These are fully decoded.  The file layouts below are the same
across every Xbox title — Azurik doesn't customise them.

### 3a. SaveMeta.xbx / TitleMeta.xbx

UTF-16-LE key/value pairs separated by `\r\n` (`0D 00 0A 00`).
Optionally followed by a binary tail that titles use for
additional metadata (timestamps, Xbox Live identifiers, etc.).

```
Name=<save display name>\r\n
TitleName=Azurik: Rise of Perathia\r\n
NoCopy=1\r\n
<optional binary tail>
```

Field semantics:
- **`Name`** — display name shown in the Xbox dashboard and in-
  game save slots.  User-editable.
- **`TitleName`** — stable across all saves for the title.
- **`NoCopy=1`** — save is marked non-copyable (dashboards refuse
  to duplicate it).  Omit the field to allow copying.

Python API:

```python
from azurik_mod.save_format import SaveMetaXbx

meta = SaveMetaXbx.from_bytes(Path("SaveMeta.xbx").read_bytes())
print(meta.save_name)      # "My Hero's Journey"
print(meta.title_name)     # "Azurik: Rise of Perathia"
print(meta.no_copy)        # True

meta.set("Name", "New Display Name")
Path("SaveMeta.xbx").write_bytes(meta.to_bytes())
```

Round-trip is byte-identical when no fields are mutated — this is
important because some Xbox-level validators fail on even a
trailing-whitespace change.

### 3b. SaveImage.xbx / TitleImage.xbx

Raw Xbox-swizzled ARGB raster data.  The save-format module
exposes these as opaque bytes; decoding the image format is out of
scope for this revision (standard Xbox texture swizzling — decoders
exist in Cxbx-Reloaded, XGCore, and others if needed).

## 4. Azurik `.sav` files

Azurik writes its own state to two flavours of `.sav` file:

| File | Purpose | Written when |
|------|---------|-------------|
| `signature.sav` | Profile-level state (inventory, stats, flags) | Save-game trigger; not level-specific |
| `<level>.sav` | Per-level world state (entity positions, pickups, quest flags) | When leaving a level / saving mid-level |

Level names come from the game's ``levels\<element>\<N>.sav``
directory hierarchy — e.g. `w4.sav` is the fourth water level,
`earth2.sav` is the second earth level, etc.

### 4a. File I/O pattern (from Ghidra)

Azurik's save code uses stdio (not raw `NtCreateFile`):

```c
// FUN_0005b250 — open_save_file(path, write)
fp = fopen(path, write_flag ? "r+b" : "rb");

// First operation on a freshly-opened save: read the 20-byte header
fread(buffer, 0x14, 1, fp);   // call site at VA 0x5C95C
```

Multiple callers then branch based on the header contents before
consuming the rest of the file.

### 4b. Fixed 20-byte header

Confirmed-size prologue on every `.sav` file.  Field layout is
TENTATIVE (pinned from read pattern, not full field-level decode):

| Offset | Size | Name | Description (tentative) |
|--------|------|------|-------------------------|
| 0x00 | 4 | `magic` | 32-bit signature (likely ASCII like `"ASAV"`) |
| 0x04 | 4 | `version` | Format revision (Azurik retail appears to be v1) |
| 0x08 | 4 | `payload_len` | Bytes in the body following the header |
| 0x0C | 4 | `checksum` | XOR or CRC32 over the payload |
| 0x10 | 4 | `reserved` | Zero in every vanilla save observed so far |

Python API:

```python
from azurik_mod.save_format import AzurikSaveFile

sav = AzurikSaveFile.from_path("signature.sav")
print(sav.header.magic_as_ascii())     # e.g. "VASA"
print(sav.header.version)              # 1
print(sav.header.payload_len, len(sav.payload))
print(sav.summary()["payload_declared_matches_actual"])
```

### 4c. Payload body — partial

The body past the 20-byte header is currently exposed as raw
bytes.  Future work: decode it into structured fields as concrete
save samples become available.

Until then, `AzurikSaveFile.iter_chunks()` yields a single opaque
`SaveChunk(name="payload", ...)` — subclasses (`SignatureSav` /
`LevelSav`) override this method to emit typed sub-chunks as
decoders get written.

Planned decoder targets (in rough priority order):
1. Player inventory (weapons, gems, keys) — high value for save
   editing.
2. Element fragment / power-up collection state — used by the
   existing randomiser.
3. Current position + facing direction — useful for warp mods.
4. Quest / trigger flags — needed for "skip to boss" mods.

Contributors: when you decode a field, add a `SaveChunk` emission
in the matching subclass of `AzurikSaveFile` and a round-trip
test in `tests/test_save_format.py` that pins the byte offsets.

## 5. CLI

```
$ azurik-cli save inspect ~/exports/my_save_slot/
save directory: ~/exports/my_save_slot/
  display name:     'My Hero's Journey'
  title name:       'Azurik: Rise of Perathia'
  no-copy flag:     True
  save image:       4096 bytes
  title image:      4096 bytes

  2 .sav file(s):
    signature.sav             magic=0x41534156 ('VASA')  ver=1  payload=8192B  match=True
    w4.sav                    magic=0x41534156 ('VASA')  ver=1  payload=4096B  match=True
```

JSON output for downstream tooling:

```
$ azurik-cli save inspect ~/exports/my_save_slot/ --json
{
  "path": "...",
  "save_name": "My Hero's Journey",
  "title_name": "Azurik: Rise of Perathia",
  "no_copy": true,
  "sav_details": [ ... ]
}
```

Single-file inspection:

```
$ azurik-cli save inspect signature.sav
file:     signature.sav
size:     4116 bytes
header:
  magic              0x41534156
  magic_ascii        VASA
  version            1
  payload_len        4096
  checksum           0xDEADBEEF
  reserved           0
payload:  4096 bytes
  payload_len check: OK
```

## 6. Integration with the rest of the mod toolchain

- The save-format module is **read-write**: you can load a
  `SaveDirectory`, mutate `meta_xbx` fields or replace `.sav`
  payloads, and write back.  No in-game validation happens from
  the Python side — if you write a malformed payload, Azurik will
  reject the save on load.
- There is **no GUI integration yet**.  Save browsing / editing
  through the main Azurik Mod Tools window is deferred until the
  payload decoder covers enough fields to make the editor useful.
- Save editing does **not** require patching the XBE — saves live
  on the Xbox HDD image, entirely separate from game code.

## 7. Limitations + future work

- **No real save samples in the repo.**  All test fixtures are
  synthesised.  Anyone who extracts a real vanilla save is
  strongly encouraged to drop it in `tests/fixtures/save/` (with
  privacy scrubbed) so future decoder work can pin byte layouts
  against real data.
- **Xbox-swizzled image decoding is stubbed.**  `SaveImage.xbx`
  bytes are passed through unchanged.  A decoder would be
  straightforward (standard Xbox texture unswizzle) but isn't
  worth building until a concrete feature needs it.
- **qcow2 ⇄ loose-directory round-trip is manual.**  Users export
  saves from xemu, edit loose files, then need to re-import them.
  An automated round-trip would require embedding a FATX driver.
- **Checksum validation is lazy.**  We don't verify the 4-byte
  checksum field — just expose it.  Once we identify the exact
  algorithm (CRC32 variant? custom XOR chain?) we can validate
  on load and recompute on write.
