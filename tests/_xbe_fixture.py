"""Shared vanilla ``default.xbe`` fixture lookup for tests.

Each test module used to duplicate this block:

    _XBE_CANDIDATES = [
        Path("/Users/michaelsrouji/Documents/Xemu/tools/"
             "Azurik - Rise of Perathia (USA).xiso/default.xbe"),
        Path(_REPO_ROOT).parent /
            "Azurik - Rise of Perathia (USA).xiso" / "default.xbe",
        Path(_REPO_ROOT) / "tests" / "fixtures" / "default.xbe",
    ]
    _XBE_PATH = next((p for p in _XBE_CANDIDATES if p.exists()), None)

with its own ``sys.path`` plumbing.  Now a single helper lives
here so new tests can ``from tests._xbe_fixture import XBE_PATH,
require_xbe`` and skip when the fixture isn't available.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _discover_xbe() -> Path | None:
    """Search the three canonical locations where a vanilla XBE
    might live and return the first that exists.  Resolution
    order:

    1. Hard-coded absolute path we use in CI / dev workstations.
    2. ``../Azurik - Rise of Perathia (USA).xiso/default.xbe`` —
       sibling of the repo.
    3. ``tests/fixtures/default.xbe`` — checked in (if ever).
    """
    candidates = [
        Path("/Users/michaelsrouji/Documents/Xemu/tools/"
             "Azurik - Rise of Perathia (USA).xiso/default.xbe"),
        Path(_REPO_ROOT).parent /
            "Azurik - Rise of Perathia (USA).xiso" / "default.xbe",
        Path(_REPO_ROOT) / "tests" / "fixtures" / "default.xbe",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


XBE_PATH: Path | None = _discover_xbe()
"""Path to a vanilla Azurik XBE, or ``None`` if no fixture is
available.  Tests that need the fixture decorate with
:func:`require_xbe`."""


def require_xbe(cls: type) -> type:
    """Decorator that ``unittest.skipUnless``-skips a TestCase
    class when :data:`XBE_PATH` is ``None``.

    Equivalent to:

    .. code-block:: python

        @unittest.skipUnless(XBE_PATH, "vanilla default.xbe fixture not available")
        class MyTest(unittest.TestCase):
            ...

    Centralised here so the skip message + condition stay
    consistent across modules.
    """
    return unittest.skipUnless(
        XBE_PATH is not None,
        "vanilla default.xbe fixture not available")(cls)
