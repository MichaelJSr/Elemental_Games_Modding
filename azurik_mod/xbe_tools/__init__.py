"""XBE inspection + authoring-support subcommands.

Umbrella package for the ``azurik-mod xbe``, ``ghidra-coverage``, and
``shim-inspect`` CLI verbs.  Each module here is self-contained and
imported lazily by ``azurik_mod.cli`` so the core CLI stays
fast-to-start.

See ``docs/TOOLING_ROADMAP.md`` for the full catalogue of tools + the
prioritisation rationale.  Shipped tools live in:

- :mod:`.xbe_scan`  — address arithmetic, hexdump, ref / float /
  string scanners
- :mod:`.ghidra_coverage` — what-we-know-vs-what-Ghidra-labels audit
- :mod:`.shim_inspect` — preview bytes that a compiled .o will emit
- :mod:`.commands` — thin argparse-to-function dispatcher wrappers
"""

from pathlib import Path

from azurik_mod.xbe_tools.ghidra_client import (
    GhidraClient,
    GhidraClientError,
    GhidraDecomp,
    GhidraFunction,
    GhidraLabel,
    GhidraProgramInfo,
    GhidraStruct,
    GhidraStructField,
    GhidraXref,
    client_from_env,
)
from azurik_mod.xbe_tools.mock_ghidra import MockGhidraServer


def find_repo_root() -> Path:
    """Return the Elemental_Games_Modding repo root.

    Walks up from this file's directory until it finds a ``pyproject.toml``
    sibling of an ``azurik_mod/`` package.  Used by the xbe_tools
    subcommands (ghidra_sync, ghidra_coverage, shim_inspect) when they
    need to locate fixtures, docs, or compiled shims without a CWD
    assumption.  Falls back to ``Path.cwd()`` if no match is found
    (e.g. installed as a site-package).
    """
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if ((candidate / "pyproject.toml").exists()
                and (candidate / "azurik_mod").is_dir()):
            return candidate
    return Path.cwd()


__all__ = [
    "GhidraClient",
    "GhidraClientError",
    "GhidraDecomp",
    "GhidraFunction",
    "GhidraLabel",
    "GhidraProgramInfo",
    "GhidraStruct",
    "GhidraStructField",
    "GhidraXref",
    "MockGhidraServer",
    "client_from_env",
    "find_repo_root",
]
