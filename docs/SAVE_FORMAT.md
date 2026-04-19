# Azurik save-file format

> **Status**: verified against a real save extracted from an
> `xbox_hdd.qcow2` xemu HDD image.  Directory layout, Xbox container
> files, and `.sav` file variants all decoded; per-file payload
> field structure is partially decoded (TextSave lines, BinarySave
> record counts) — full record-by-record decoders for `inv.sav`,
> `shared.sav`, and level saves remain future work.
>
> **Python module**: [`azurik_mod.save_format`](../azurik_mod/save_format/)
> **CLI**: `azurik-mod save inspect <path>`

---

## 1. Directory layout

A single save slot lives in its own folder on the Xbox HDD.
Example (extracted from a real xemu save):

```
UDATA\4d530007\<save_id>\
    SaveMeta.xbx            76 B    Xbox-standard save metadata
    TitleMeta.xbx           38 B    Title-level metadata
    SaveImage.xbx         4096 B    64×64 ARGB thumbnail
    TitleImage.xbx       10240 B    Title icon
    signature.sav           20 B    SHA-1 digest (integrity check)

    inv.sav              16384 B    Binary: inventory (v7 records)
    loc.sav              16384 B    Text:   current location
    magic.sav            16384 B    Text:   spell / power stats
    shared.sav           16384 B    Binary: cross-level flags (24 records)

    largeimage.xbx       34816 B    Bigger thumbnail (extra file)

    levels/
      air/
        a1.sav           16384 B    One per visited air level
        a3.sav  ...
      death/
        d1.sav  ...
      earth/
        e2.sav  ...
      fire/
        f1.sav  ...
      water/
        w1.sav           9316 B     Visited — non-zero
        w2.sav ...
      life.sav           16384 B
      selector.sav       16384 B
      town.sav           16384 B
      training_room.sav    992 B

TDATA\4d530007\
    options.sav             23 B    Title-level settings (text)
```

`4d530007` is Azurik's title ID (hex).  The inner UUID-like folder
(`5C0A938BD9AC` in the real save we inspected) is a random save-slot
ID generated per-save.

### Retail Xbox vs xemu HDD addressing

The runtime path is `\Device\Harddisk0\partition1\UDATA\<title>\...`
which maps to the Xbox E:\ drive.  On xemu's `xbox_hdd.qcow2` this
partition starts at a fixed byte offset inside the qcow2 image.
Empirically (from one xemu-created 8 GB HDD image):

| Partition offset | Size | Content |
|------------------|------|---------|
| `0x00080000` | 750 MB | CACHE0 (X:\) |
| `0x2EE80000` | 750 MB | CACHE1 (Y:\) |
| `0x5DC80000` | 750 MB | CACHE2 (Z:\) — game data cache |
| `0x8CA80000` | 500 MB | SYSTEM / dashboard |
| `0xABE80000` | 5.4 GB | **E:\ — retail save area** |

Inside the E:\ FATX partition:
- FAT starts at offset `0x1000` within the partition.
- Data area starts at the first cluster boundary after the FAT.
  On the xemu image we inspected this lands at partition offset
  `0x133000`, where the ROOT directory lives (cluster 1).
- Cluster size is 16 KB (32 × 512-byte sectors).
- FAT entries are 32-bit.

## 2. Extracting save data

xemu's built-in HDD export is the path of least resistance:

1. Run the game in xemu until it writes a save.
2. **Machine → Settings → System → HDD → Browse**.
3. Navigate to `UDATA\<azurik_title_id>\` and right-click → **Export**.
4. xemu drops the container files into a loose folder.
5. `azurik-mod save inspect <exported_folder>` for a summary.

For the adventurous, the repo ships a minimal pure-Python
`qcow2_reader.py` + `fatx_reader.py` pair (not installed; see
`/tmp/azurik_hdd_probe/` when reproducing the decode work) that
can walk a qcow2 directly — no qemu-img or FATX tools needed.
Not wired into the main CLI today; ask before depending on them.

## 3. Xbox-standard container files

Fully decoded.  The file layouts below are the same across every
Xbox title — Azurik doesn't customise them.

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

Python API (`SaveMetaXbx`): `from_bytes()`, `get()`, `set()`,
`save_name`, `title_name`, `no_copy`, `to_bytes()`.  Round-trip is
byte-identical when no fields are mutated.

### 3b. SaveImage.xbx / TitleImage.xbx

Raw Xbox-swizzled ARGB raster data.  Exposed as opaque bytes;
decoding Xbox texture swizzle is out of scope (standard routines
exist in Cxbx-Reloaded, XGCore, etc.).

## 4. Azurik `.sav` file variants

Unlike the initial scaffold assumed, there is **no single 20-byte
header** across all `.sav` files.  The format splits into four
shapes, classified by `AzurikSave.kind`:

### 4a. Text saves (`loc.sav`, `magic.sav`, `options.sav`)

ASCII, line-delimited.  Header is literally the string
`fileversion=<N>\n`, followed by value lines (`key=value` OR bare
numeric/path tokens), optionally followed by a binary tail starting
at the first `\x00` byte.

Real example (first 80 bytes of `loc.sav`):

```
fileversion=1\nlevels/death/d2\n\x00\x6b\xe1\x1e\xbf\xc9\x5e\x73...
```

Real `magic.sav`:

```
fileversion=1\n1.000000\n1.000000\n1.000000\n0\n13\n1.000000\n...
```

Python API (`TextSave`): `version`, `lines`, `binary_tail`, `raw`,
`to_bytes()`.  Editing `lines` then calling `to_bytes()`
re-emits the file with the preserved binary tail intact — so a
user can open `magic.sav` in a text editor and change `1.000000` →
`99.000000` to buff a stat, then save (and re-sign: see § 4c).

### 4b. Binary-record saves (`inv.sav`, `shared.sav`, level saves)

8-byte header:

| Offset | Size | Field |
|--------|------|-------|
| 0x00 | u32 | `version` |
| 0x04 | u32 | `record_count` |

Followed by `record_count` records of format TBD (varies per
file).  We verify across real samples:

| File | Version | Record count | Body size |
|------|---------|---------------|-----------|
| `inv.sav`         | 7 | 1057679148 (!) | 16376 B |
| `shared.sav`      | 1 | 24 | 16376 B |
| `d2.sav` (visited) | 2 | 201 | 12248 B |
| `w1.sav` (visited) | 2 | 153 | 9308 B |
| `a1.sav` (not visited) | 0 | 0 | 16376 B all zero |
| `e5.sav` (not visited, large) | 0 | 0 | 32760 B all zero |

The huge record_count for `inv.sav` suggests that file uses a
different header shape — either a single u32 version followed by
count-less packed records, or the u32 at +4 is actually a SEED /
flags word.  Full decoding is future work.

Python API (`BinarySave`): `version`, `record_count`, `body`,
`raw`, `to_bytes()`.  Per-file record decoders (e.g. an
`Inventory` class that walks `inv.sav`'s records and exposes
items slots) are future work.

### 4c. `signature.sav` — SHA-1 integrity digest

Exactly 20 bytes — a SHA-1 digest that Azurik uses to validate
every OTHER `.sav` file in the save slot.

**Consequence for save editing**: modifying any of the other
`.sav` files without recomputing `signature.sav` will make
Azurik reject the save on load.  We haven't yet reverse-engineered
the hash domain (what exact files are fed to SHA-1, in what order,
with what salt) — that's tracked as the main TODO for save editing.

Python API (`SignatureSave`): `digest`, `hex()`, `to_bytes()`.

### 4d. Level saves (`levels/<element>/<level>.sav`)

Same shape as (4b) binary saves.  Empirical taxonomy:

- `version=0` + `record_count=0` + all-zero body → level never
  visited.  About 20 of 29 sampled.
- `version=2` + nonzero record_count + structured body → visited.

Naming convention matches the engine's `levels/<element>/<N>`
path format seen in Ghidra string constants.

## 5. Python API

```python
from azurik_mod.save_format import AzurikSave, SaveDirectory

# Classify any .sav file automatically.
sav = AzurikSave.from_path("signature.sav")
assert sav.kind == "signature"
print(sav.signature.hex())

# Text saves — edit + round-trip.
magic = AzurikSave.from_path("magic.sav")
assert magic.kind == "text"
magic.text.lines[0] = "99.000000"         # tweak first stat
Path("magic_modded.sav").write_bytes(magic.to_bytes())

# Walk a whole save slot (recurses into levels/).
slot = SaveDirectory.from_directory("exported_save/")
print(slot.summary()["level_sav_files"])  # list of levels/*.sav
```

## 6. CLI

```
$ azurik-mod save inspect ~/exports/my_save_slot/
save directory: ~/exports/my_save_slot/
  display name:     'Hero'
  title name:       'Azurik: Rise of Perathia'
  no-copy flag:     False
  save image:       4096 bytes

  29 .sav file(s) — 5 root / 24 under levels/:
    inv.sav        [binary]  16384 B  v7 ...
    loc.sav        [text]    16384 B  v1  2 lines + 16346 B tail
    magic.sav      [text]    16384 B  v1  5 lines + 16338 B tail
    signature.sav  [signature] 20 B   sha1=aff04da2...

  Level saves:
    w1.sav         [binary]  9316 B   v2  153 records, 9308 B body
    d2.sav         [binary]  12256 B  v2  201 records, 12248 B body
    ...
```

Single-file mode (pipe-friendly JSON):

```
$ azurik-mod save inspect loc.sav --json
{
  "kind": "text",
  "version": 1,
  "lines": 2,
  "preview": ["levels/death/d2"],
  "binary_tail_bytes": 16346
}
```

## 7. Signature algorithm

**Algorithm traced (April 2026).**  Ghidra decomp of
``FUN_0005c4b0`` + its surrounding caller at VA ``0x0005c920``
shows the sign / verify path uses Xbox's stock
``XCalculateSignatureBegin / ...Update / ...End`` trio with
``flags=0`` (no per-console HDKey outer layer), which reduces
to **HMAC-SHA1(XboxSignatureKey, tree_bytes)**.

### Tree-walk order (from the decomp)

```
hmac = HMAC-SHA1.Init(XboxSignatureKey)

def walk(dir):
    files = [f for f in sorted(dir) if f.name.lower() != "signature.sav"
                                   and  f.name.lower().endswith(".sav")]
    subdirs = [d for d in sorted(dir) if d.is_dir()
                                     and d.name not in (".", "..")]

    for f in files:            # sorted alphabetically
        hmac.update(f.name.encode("ascii") + b"\0")
        with open(f, "rb") as fh:
            while chunk := fh.read(0x4000):
                hmac.update(chunk)

    for sd in subdirs:         # sorted alphabetically
        hmac.update(sd.name.encode("ascii") + b"\0")
        walk(sd)

signature_bytes = hmac.digest()      # 20 bytes
Path("signature.sav").write_bytes(signature_bytes)
```

The entry point at ``0x0005c920`` confirms ``flags=0`` via the
``PUSH 0x0`` two instructions before ``CALL XCalculateSignatureBegin``
(see `docs/ghidra_snapshot.json` → ``calculate_save_signature``).

### Why we don't emit valid signatures yet

The open unknown is **what `XboxSignatureKey` actually is**.
On retail hardware it's a runtime value the kernel derives
from a combination of the XBE certificate (bzSignatureKey @
cert+0x110) and the EEPROM's HDKey (per console).

### Exhaustive recovery attempts (April 2026)

We proved this the hard way with three real save slots, the
EEPROM, and two RAM dumps (64 MB and 256 MB) taken mid-game:

| Source scanned | Alignment | Candidates | Hits |
|----------------|-----------|------------|------|
| XBE certificate (464 B) | 4 | 116 | 0 |
| Every XBE cert field by name | — | 11 | 0 |
| Raw EEPROM (256 B) | 1 | 240 | 0 |
| EEPROM v1.0 / v1.1 / v1.2 / v1.6 RC4-decrypted HDKey | — | 4 | 0 |
| XBE body (3.7 MiB) | 4 | 446 460 | 0 |
| SaveMeta.xbx / saveimage.xbx heads | 1 | 262 | 0 |
| HMAC-SHA1(A, B)[:16] for every (A, B) ∈ 10 ingredients² | — | 100 | 0 |
| SHA-1(A ‖ B)[:16] for same ingredient set | — | 100 | 0 |
| **64 MB RAM dump (pmemsave 0 0x04000000)** | 4 | 16 777 212 | **0** |
| **64 MB RAM dump** | 16 | 4 194 304 | **0** |
| **256 MB RAM dump (pmemsave 0 0x10000000)** | 1 | 268 435 440 | **0** |

The 256 MB byte-aligned scan is **exhaustive** — no 16-byte
window at any offset in the dump HMACs to the expected
signatures under any of our tested walk variants (plain SHA-1,
HMAC-empty-key, filename-with-NUL, filename-without-NUL,
root-only vs recursive).

**The pointer changes between boots** — RAM[0x0018F4D4] was
``0x8B084A89`` in one session, ``0xC73B0018`` in a later
session, despite the same EEPROM.  That's consistent with
per-boot heap allocation of the key storage.

**xemu's monitor rejects ``memsave`` on both pointer values**
("Invalid addr").  The guest MMU doesn't translate those VAs
when the monitor is paused, suggesting either:

- the key lives in a page that's only resident during the
  HMAC call itself (allocated on demand, freed immediately),
- or xemu's synthetic kernel uses a non-standard VA→phys
  mapping that doesn't correspond to any documented Xbox
  memory region (retail 64 MB, dev-kit 128 MB, nor
  0xC0000000 system area).

We've run out of static recovery vectors.

### Path forward (pragmatic) — SHIPPED

The actually-useful deliverable here is the
**``qol_skip_save_signature``** patch.  It's now shipped — tick
it on the GUI's Patches tab (or pass via the CLI pack selector)
and the verify callsite at VA ``0x0005C990`` gets rewritten to
``MOV AL, 1 ; RET`` so any ``.sav`` edit loads regardless of
whether ``signature.sav`` matches.

Three-byte patch, 1 site, ``category="qol"``.  Full
documentation lives in
[``docs/PATCHES.md`` § ``qol_skip_save_signature``](PATCHES.md).
Regression tests pin the end-to-end invariant:
[``tests/test_qol_skip_save_signature.py``](../tests/test_qol_skip_save_signature.py).

How it was found (April 2026 pass):

- ``calculate_save_signature`` (the *write* side) lives at VA
  ``0x0005C920`` — already in ``vanilla_symbols.py``.
- Direct xrefs point only at its vtable slot (``0x0019E278``),
  not at any call site, because the engine dispatches the
  whole save-handling family through a function-pointer
  table at ``0x0019E260``.
- The ``"signature.sav"`` string at ``0x0019E290`` is
  referenced by ``FUN_0005C4B0`` (the recursive tree-walker
  that updates the HMAC) — that pointed us at the
  sibling functions starting at ``0x0005C990``.
- ``verify_save_signature`` at ``0x0005C990`` has the same
  ``MOV AL, [ECX+0x20A]`` prologue as the sign function, runs
  the HMAC, and does ``REPE CMPSD`` against the bytes read
  from ``signature.sav``.  That's the verify callsite.
- Overwriting the first 3 bytes with ``B0 01 C3``
  (``MOV AL, 1 ; RET``) always-succeeds the check without
  touching the write side.

The key-recovery scanner
(:mod:`azurik_mod.save_format.key_recover`) stays in the
toolbox as a second option — useful when you need a save to
also load on a vanilla (unpatched) build, e.g. for
distribution.  Dump xemu RAM with the ``pmemsave`` monitor
command, run the scanner against your own ``.sav`` slots,
and feed the recovered key to
``azurik-mod save edit --xbox-signature-key HEX32``.

### Two practical unblockers

1. **`qol_skip_save_signature` patch** (shipped; recommended).
   Tick the pack in the GUI's Patches tab (or pass via
   ``--packs qol_skip_save_signature`` in the CLI) and rebuild
   the ISO.  Any edited save loads on the patched XBE.  Write-
   side signing is unchanged so saves created on the patched
   XBE also load on vanilla.
2. **Round-trip through the game** (zero code, works on any
   build).  Edit the slot's text saves with
   ``azurik-mod save edit``, drop the result back into
   ``UDATA\\4d530007\\<slot>\\``, boot the game, let it
   auto-save once — at that point the game re-signs with the
   correct console key.  Subsequent loads succeed.
3. **Dynamic key recovery** — dump xemu RAM during a save
   operation (via xemu's debug monitor, ``gdb-stub``, or
   ``qemu -monitor``) and feed the dump to the built-in
   scanner:

   ```
   azurik-mod save key-recover \\
       --dump xemu-ram.bin \\
       --save exported/slot1 \\
       --save exported/slot2
   ```

   The scanner brute-forces every 16-byte window in the dump
   against the known (walk, signature) pairs and returns keys
   that match.  Provide at least two slots to rule out random
   2^-160 collisions.  Once recovered, feed the key back via
   ``azurik-mod save edit --xbox-signature-key HEX32`` for
   portable signed edits on this specific console.

### Python helper

The traced walker lives in
:func:`azurik_mod.save_format.signature.compute_signature_walk`
so future work can iterate on the key-derivation side without
re-solving the tree-walk order.  The recovery brute-forcer
lives in :mod:`azurik_mod.save_format.key_recover`.
``SaveEditor`` exposes an ``--xbox-signature-key`` override so
power-users who've recovered their console's key can produce a
valid signature in-place.
- **`inv.sav` full record layout.**  The implausibly-large
  `record_count` field suggests the second u32 isn't a count.
  Candidates: item-bitmask, seed, or flags.  Dump a save with
  known inventory contents + diff against vanilla to reverse.
- **Level-save record structure.**  A visited w1.sav has 153
  records at 61 B each.  Candidates per record: entity state
  + spawn position + pickup-collected bitmask.  Again: diff a
  "first visit" vs "full completion" save.
- **`SaveImage.xbx` decoder.**  Opaque bytes today; Xbox ARGB
  swizzle decode would let users preview thumbnails in a future
  GUI save-editor tab.

## 8. Relationship to other docs

- Code: [`azurik_mod/save_format/`](../azurik_mod/save_format/)
- Tests: [`tests/test_save_format.py`](../tests/test_save_format.py)
  with small scrubbed real-save fixtures in
  [`tests/fixtures/save/`](../tests/fixtures/save/).
