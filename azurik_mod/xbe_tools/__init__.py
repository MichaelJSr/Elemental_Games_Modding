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

from azurik_mod.xbe_tools.ghidra_client import (
    GhidraClient,
    GhidraClientError,
    GhidraFunction,
    GhidraLabel,
    GhidraProgramInfo,
    client_from_env,
)
from azurik_mod.xbe_tools.mock_ghidra import MockGhidraServer

__all__ = [
    "GhidraClient",
    "GhidraClientError",
    "GhidraFunction",
    "GhidraLabel",
    "GhidraProgramInfo",
    "MockGhidraServer",
    "client_from_env",
]
