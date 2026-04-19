"""Azurik save-file format (``azurik_mod.save_format``).

Parse and introspect Azurik save data that lives on the Xbox HDD
under ``\\Device\\Harddisk0\\partition1\\UDATA\\<title_id>\\<save_id>\\``.

What's in a save directory
--------------------------

Each Azurik save slot contains standard Xbox save-container files
wrapping Azurik's own per-level / per-profile data:

    SaveMeta.xbx     Xbox-standard save metadata (UTF-16 key/value
                     pairs: save display name, NoCopy flag, etc.).
                     Format is stable across all Xbox titles — see
                     :class:`SaveMetaXbx`.

    SaveImage.xbx    Per-save thumbnail (ARGB raster, Xbox-swizzled).
                     Unparsed here; exposed as raw bytes.

    TitleMeta.xbx    Title-level metadata (shared across saves).
                     Same key/value format as SaveMeta.xbx.

    TitleImage.xbx   Title icon (64x64 ARGB typically).  Raw bytes.

    signature.sav    Azurik's profile-level save data — contains the
                     player's persistent state (inventory, stats,
                     flags) independent of which level they're in.
                     Internal format decoded by :class:`SignatureSav`.

    <level>.sav      Per-level save state (e.g. ``w4.sav`` for the
                     fourth water level).  Contains the level's
                     entity state, quest flags, and dropped items.
                     Format decoded by :class:`LevelSav`.

    (other files...) Level-specific assets the game writes on demand
                     — cached pre-computed nav meshes, player-
                     chosen camera positions, etc.

On-disk hierarchy
-----------------

Retail Xbox:

    \\Device\\Harddisk0\\partition1\\UDATA\\4d410006\\<MU>\\<save_id>\\
                                          ^^^^^^^^^
                                          Azurik's title ID in hex

xemu maps this to ``xbox_hdd.qcow2`` in the user's Xemu home.  To
extract a save for analysis, the user needs to:

    1. Convert qcow2 → raw disk: ``qemu-img convert -O raw ...``
    2. Mount the partition-1 FATX filesystem (tools like
       ``xfattools`` or Cxbx-Reloaded's HDD tools).
    3. Navigate to ``UDATA\\4d410006\\``.

Or, in xemu, right-click "HDD" → "Eject / Import" to export a
specific save slot as a loose folder the parsers in this module
can directly consume.

Source-level evidence
---------------------

The format details here come from reading Azurik's own save code in
Ghidra (source filename leaked via an assert string at VA 0x19E5C8:
``C:\\Elemental\\src\\game\\save.cpp``).  Key findings:

- Save I/O uses stdio (``fopen`` modes ``rb`` / ``w+b`` / ``r+b``)
  rather than raw ``NtCreateFile`` — so the format is byte-stream
  oriented, not record-sectored.
- ``FUN_0005b250`` opens a save file: takes a path + write-flag,
  builds ``<z:\\savegame>\\<subdir>\\<name>.sav``, returns a
  ``FILE *``.
- ``FUN_0005c4b0`` recursively walks the save directory to find
  all ``.sav`` files during profile load.
- The save-load path reads the FIRST 20 BYTES as a fixed header,
  then branches on content (see :class:`SaveHeader`).

Intentionally not implemented here
----------------------------------

- **Full field-by-field decoding of signature.sav and *.sav.**
  Those byte layouts are opaque without real save samples; this
  module provides scaffolding + a clean extension point (see
  :meth:`AzurikSaveFile.iter_chunks`) where future work can plug
  in decoders once we get hands on a vanilla save dump.

- **Writing modified saves back to the qcow2.**  The qcow2 ⇄ FATX
  round-trip is out of scope.  This module reads and writes loose
  save *directories*; the user is responsible for re-injecting
  them into an xemu HDD image if they want in-game validation.
"""

from __future__ import annotations

from .container import (
    SaveDirectory,
    SaveMetaXbx,
    SaveMetaField,
)
from .azurik import (
    AzurikSaveFile,
    SaveHeader,
    SignatureSav,
    LevelSav,
)

__all__ = [
    "SaveDirectory",
    "SaveMetaXbx",
    "SaveMetaField",
    "AzurikSaveFile",
    "SaveHeader",
    "SignatureSav",
    "LevelSav",
]
