"""Test selector — find the pytest cases that reference a given
VA, pack, or shim and optionally launch pytest on just those.

Motivation: the full suite grew past 470 tests.  When iterating on
one patch site, running every unrelated test burns 60+ seconds
between attempts.  ``azurik-mod test-for-va 0x85F62`` narrows the
run to just the 5-10 tests that actually reference that VA.

## Selection rules

The selector scans ``tests/`` for source lines matching one of:

- **VA hit**: the 32-bit value formatted as ``0x<HEX>`` in any
  common width (``0xHHHH`` through ``0xHHHHHHHH``), case-insensitive.
- **Pack hit**: the pack / feature name appears as a bareword
  (matches function names, dict keys, attribute access, etc.).
- **Shim hit**: the shim folder name / ``.o`` stem.

Matches are attributed to the test CLASS they live in so pytest's
``-k`` filter can select them.

## Output modes

- Default: print ``<file>::<Class>`` lines for every matching
  test class, one per line.
- ``--run``: invoke pytest on the matching set directly.
- ``--json``: structured output for downstream tooling.

This is a no-op on projects that don't use class-based pytest —
the implementation assumes the existing azurik_mod layout where
every test is in a ``class TestFoo(unittest.TestCase)``.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


# Matches a test class definition.  Base-class parentheses are
# optional (PEP 8-conformant ``class Foo(Bar):`` and the newer
# ``class Foo:`` both supported).  The capture group grabs the
# class NAME.
_CLASS_RE = re.compile(
    r"^\s*class\s+([A-Z][A-Za-z0-9_]*)\s*[\(:]",
    re.MULTILINE)


@dataclass(frozen=True)
class TestMatch:
    """One ``(file, class)`` pair that matches the selector."""

    file: Path
    class_name: str
    hit_lines: tuple[int, ...] = field(default_factory=tuple)

    def pytest_selector(self) -> str:
        """A ``tests/foo.py::MyTest`` spec pytest can consume."""
        return f"{self.file.as_posix()}::{self.class_name}"


def _build_va_patterns(va: int) -> list[re.Pattern[str]]:
    """Return regexes that match a VA formatted as hex at any
    common width.

    Python tests can spell a VA many ways:

    * ``0x85F62``, ``0x0085F62``, ``0x00085F62`` — common widths
    * ``0x85f62`` — lowercase
    * ``0X85F62`` — capital 0X (rare but legal)

    We emit one regex per width (4..8 hex digits) that's case-
    insensitive on the prefix + the digits.
    """
    out: list[re.Pattern[str]] = []
    for width in (4, 5, 6, 7, 8):
        fmt = f"0x{va:0{width}X}"
        # Word boundary both sides + case-insensitive so
        # "foo_0x85F62_bar" doesn't match but "x = 0x85f62" does.
        pat = re.compile(
            rf"\b{re.escape(fmt)}\b".replace("\\x", "[xX]"),
            re.IGNORECASE)
        out.append(pat)
    return out


def _build_pack_pattern(name: str) -> re.Pattern[str]:
    """Match ``name`` as a bareword (no leading / trailing
    alphanumeric or underscore)."""
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")


def _find_class_for_line(source: str, line_no: int) -> str | None:
    """Given the file's source and a 1-based line number, return
    the name of the enclosing test class (nearest preceding
    ``class Foo(...):``) or ``None`` when the line lives outside
    any class."""
    best_name: str | None = None
    best_pos = -1
    for m in _CLASS_RE.finditer(source):
        # Figure out which line the ``class`` keyword is on.
        class_line = source.count("\n", 0, m.start()) + 1
        if class_line < line_no and class_line > best_pos:
            best_pos = class_line
            best_name = m.group(1)
    return best_name


def find_matches(*,
                 va: int | None = None,
                 pack: str | None = None,
                 tests_dir: Path) -> list[TestMatch]:
    """Return test classes that reference the given VA or pack.

    Exactly one of ``va`` / ``pack`` must be non-None.  The search
    is silent on files that can't be read as UTF-8 (binary
    fixtures etc.) so the scan works on a repo with mixed content.
    """
    if (va is None) == (pack is None):
        raise ValueError("pass exactly one of va= or pack=")

    patterns: list[re.Pattern[str]]
    if va is not None:
        patterns = _build_va_patterns(va)
    else:
        patterns = [_build_pack_pattern(pack)]  # type: ignore[arg-type]

    matches: dict[tuple[Path, str], set[int]] = {}
    for py in sorted(tests_dir.rglob("test_*.py")):
        try:
            source = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pat in patterns:
            for m in pat.finditer(source):
                line_no = source.count("\n", 0, m.start()) + 1
                cls = _find_class_for_line(source, line_no)
                if cls is None:
                    continue
                key = (py, cls)
                matches.setdefault(key, set()).add(line_no)

    return [
        TestMatch(file=path, class_name=cls, hit_lines=tuple(sorted(lines)))
        for (path, cls), lines in sorted(matches.items(),
                                         key=lambda x: (x[0][0], x[0][1]))]


def run_pytest(matches: list[TestMatch], *,
               extra_args: list[str] | None = None,
               pytest_cmd: list[str] | None = None,
               cwd: Path | None = None) -> int:
    """Invoke pytest against the matching selectors.

    Returns pytest's exit code (0 = all green).  A match list
    that's empty short-circuits with exit code 5 (pytest's "no
    tests ran" convention) so callers can distinguish it from a
    real test failure.
    """
    if not matches:
        return 5
    cmd = list(pytest_cmd or [sys.executable, "-m", "pytest"])
    cmd.extend(m.pytest_selector() for m in matches)
    if extra_args:
        cmd.extend(extra_args)
    try:
        completed = subprocess.run(cmd, cwd=cwd)
    except FileNotFoundError:
        return 127  # pytest not installed
    return completed.returncode


__all__ = [
    "TestMatch",
    "find_matches",
    "run_pytest",
]
