"""VA-drift pin helper — the centralised "bytes at VA X match Y"
assertion.

Existing tests pin specific VA-anchored bytes against the vanilla
XBE in several places (e.g. ``tests/test_va_audit.py``,
``tests/test_shim_authoring.py``, ``tests/test_index_xbr.py``).
Every site re-implements the same 5-10 line dance:

    xbe = _VANILLA_XBE.read_bytes()
    off = va_to_file(0x85F62)
    self.assertEqual(xbe[off:off+6].hex(), "d9 05 08 00 1f 00")

This module replaces that with a single reusable assertion that
emits a far more helpful failure message — the expected bytes,
the bytes actually at that VA, the section it lives in, and a
disassembled context window when the bytes look like code.

Public API:

- :func:`pin_va_bytes` — raw bytes-equality assertion with
  structured failure output.
- :func:`pin_va_pattern` — predicate-based check (for BSS,
  heuristic matches like "starts with ASCII", etc.).
- :func:`pin_va_string` — null-terminated string equality.
- :func:`load_vanilla_xbe` — shared lazy loader + caching so
  tests don't each re-read the 1.8 MB XBE.

Typical use from a test:

.. code-block:: python

    from azurik_mod.xbe_tools.pin_va import pin_va_bytes, load_vanilla_xbe

    class MyDriftTest(unittest.TestCase):
        def test_gravity_float_bytes(self):
            xbe = load_vanilla_xbe()
            pin_va_bytes(xbe, va=0x001980A8,
                         expected="cdcc1c41",
                         description="f32 9.8 (world gravity)")
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Callable

from azurik_mod.patching.xbe import parse_xbe_sections, va_to_file

__all__ = [
    "PinFailure",
    "load_vanilla_xbe",
    "pin_va_bytes",
    "pin_va_pattern",
    "pin_va_string",
]

# Default location of the vanilla XBE; override via the
# ``AZURIK_VANILLA_XBE`` env var when running against a different
# dump (e.g. a PAL or JP region XBE).
_DEFAULT_VANILLA_XBE = (
    Path(__file__).resolve().parents[3] /
    "Azurik - Rise of Perathia (USA).xiso" / "default.xbe")

_XBE_CACHE: dict[Path, bytes] = {}


class PinFailure(AssertionError):
    """Raised when a ``pin_va_*`` helper's assertion fails.

    Carries structured attributes so test harnesses can surface
    the exact mismatch without re-parsing the error message.
    """

    def __init__(self, *, va: int, expected: object, actual: object,
                 description: str, section: str | None) -> None:
        self.va = va
        self.expected = expected
        self.actual = actual
        self.description = description
        self.section = section
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        lines = [
            f"VA-drift pin FAILED at 0x{self.va:08X}",
            f"  description: {self.description}",
            f"  section:     {self.section or '(unresolved)'}",
            f"  expected:    {self.expected!r}",
            f"  actual:      {self.actual!r}",
        ]
        return "\n".join(lines)


def load_vanilla_xbe(path: str | Path | None = None) -> bytes:
    """Return the vanilla XBE bytes, cached across calls.

    ``path=None`` uses the ``AZURIK_VANILLA_XBE`` env var when set,
    otherwise falls back to the extracted USA ISO layout in the
    workspace parent directory.  Pass a path to override per-test.

    Raises :exc:`FileNotFoundError` if the XBE isn't available —
    tests that require drift-pinning should guard their
    ``setUpClass`` with ``unittest.skipUnless(...)`` so CI hosts
    without the game fixture skip cleanly instead of erroring.
    """
    import os
    if path is None:
        env = os.environ.get("AZURIK_VANILLA_XBE")
        path = Path(env) if env else _DEFAULT_VANILLA_XBE
    else:
        path = Path(path)
    if path in _XBE_CACHE:
        return _XBE_CACHE[path]
    if not path.exists():
        raise FileNotFoundError(
            f"Vanilla XBE not found at {path}.  Set "
            f"AZURIK_VANILLA_XBE or pass ``path=`` explicitly.")
    data = path.read_bytes()
    _XBE_CACHE[path] = data
    return data


# ---------------------------------------------------------------------------
# Helper: which section does a VA live in?  Used to enrich failure
# messages.  Returns the section NAME (without leading dot) or None.
# ---------------------------------------------------------------------------

def _section_for_va(xbe: bytes, va: int) -> str | None:
    try:
        _, sections = parse_xbe_sections(xbe)
    except Exception:  # noqa: BLE001
        return None
    for s in sections:
        vlo = s["vaddr"]
        vhi = vlo + max(s["vsize"], s["raw_size"])
        if vlo <= va < vhi:
            return s["name"].lstrip(".")
    return None


# ---------------------------------------------------------------------------
# Byte-level read helper (matches the existing test_va_audit helper
# but tolerant of BSS tails).
# ---------------------------------------------------------------------------

def _read_bytes_at_va(xbe: bytes, va: int, length: int) -> bytes:
    """Return ``length`` bytes at ``va`` or ``b""`` if the VA
    resolves past the file-backed portion (BSS zero-fill)."""
    try:
        off = va_to_file(va)
    except Exception:
        return b""
    if off >= len(xbe):
        return b""  # BSS — caller decides what to do
    return xbe[off:off + length]


# ---------------------------------------------------------------------------
# Pin helpers
# ---------------------------------------------------------------------------

def pin_va_bytes(xbe: bytes, *, va: int, expected: str | bytes,
                 description: str = "") -> None:
    """Assert the bytes at ``va`` match ``expected``.

    ``expected`` accepts either raw ``bytes`` (``b"\\xcd\\xcc\\x1c\\x41"``)
    or a hex string (``"cdcc1c41"``; whitespace tolerated).  Length
    is inferred from ``expected`` so callers can't drift the two.

    Raises :exc:`PinFailure` with a structured diagnostic on
    mismatch.
    """
    if isinstance(expected, str):
        expected_bytes = bytes.fromhex(expected.replace(" ", ""))
    else:
        expected_bytes = bytes(expected)
    actual = _read_bytes_at_va(xbe, va, len(expected_bytes))
    if actual != expected_bytes:
        raise PinFailure(
            va=va,
            expected=expected_bytes.hex(),
            actual=actual.hex() if actual else "(BSS / out of image)",
            description=description or "byte equality",
            section=_section_for_va(xbe, va))


def pin_va_pattern(xbe: bytes, *, va: int, length: int,
                   predicate: Callable[[bytes], bool],
                   description: str = "") -> None:
    """Assert that ``predicate(bytes)`` holds for the ``length``
    bytes at ``va``.

    Use this when you want flexible matching — e.g. "starts with
    ASCII 'garret4'" or "is four NUL bytes (BSS)".  The predicate
    is given the EXACT bytes read (may be shorter than ``length``
    when the VA tail is in BSS).
    """
    actual = _read_bytes_at_va(xbe, va, length)
    if not predicate(actual):
        raise PinFailure(
            va=va,
            expected=f"<predicate: {predicate.__qualname__}>",
            actual=actual.hex() if actual else "(BSS / out of image)",
            description=description or "predicate match",
            section=_section_for_va(xbe, va))


def pin_va_string(xbe: bytes, *, va: int, expected: str,
                  description: str = "") -> None:
    """Assert the NUL-terminated ASCII string at ``va`` equals
    ``expected``.

    Equivalent to :func:`pin_va_bytes` with ``expected``
    synthesised as ``expected.encode("ascii") + b"\\x00"`` — a
    convenience for the common "string anchor" drift-guard.
    """
    expected_bytes = expected.encode("ascii") + b"\x00"
    actual = _read_bytes_at_va(xbe, va, len(expected_bytes))
    if actual != expected_bytes:
        raise PinFailure(
            va=va,
            expected=expected + "\\0",
            actual=(actual[:-1].decode("ascii", errors="replace")
                    if actual else "(BSS / out of image)"),
            description=description or f"string {expected!r}",
            section=_section_for_va(xbe, va))
