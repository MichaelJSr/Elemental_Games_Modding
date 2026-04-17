"""Azurik: Rise of Perathia modding toolkit — library package.

Public sub-packages:
    patching    — PatchSpec engine (apply / verify / XBE offset map).
    patches     — individual patch packs (FPS unlock, QoL, ...).
    iso         — ISO extract/pack via xdvdfs (auto-downloaded).
    randomizer  — collectible randomizer and solver.
    config      — config.xbr registry + schema + keyed-table parser.
"""

__version__ = "0.3.0"

# Proactively import the patches sub-package so each pack's
# `register_pack(...)` side effect runs once.  Downstream callers
# (GUI / CLI / scripts) can then rely on
# `azurik_mod.patching.registry.all_packs()` being fully populated
# without needing to import individual pack modules themselves.
from azurik_mod import patches as _patches  # noqa: F401,E402
