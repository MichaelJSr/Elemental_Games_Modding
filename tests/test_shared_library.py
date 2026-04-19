"""Tests for Phase 2 E — shared-library shim layout.

A ``ShimLayoutSession`` keeps a cache of shared-library placements so
multiple trampolines that reference the same helper function resolve
to a single copy, instead of each installing its own.

Coverage:

- Session's ``apply_shared_library`` places a .o once; re-applying
  the same path is idempotent.
- The returned export map is non-empty and carries mangled names
  (`_shared_double@4` etc.) with final VAs.
- When the session has already placed a shared library, a subsequent
  ``layout_coff`` call resolves undefined externs against those
  exports — without consulting any other resolver.
- End-to-end: two independent "consumer" shims reference the same
  shared helper; both REL32 relocations land at the same VA.
- Failure modes: applying a library with no exported symbols raises
  a descriptive error so shim authors notice that they forgot to
  leave a function with external linkage.
"""

from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.patching.coff import (  # noqa: E402
    IMAGE_REL_I386_REL32,
    layout_coff,
    parse_coff,
)
from azurik_mod.patching.shim_session import ShimLayoutSession  # noqa: E402


_COMPILE_SH = _REPO_ROOT / "shims/toolchain/compile.sh"
_LIB_SRC = _REPO_ROOT / "shims/fixtures/_shared_lib_test.c"
_CONSUMER_A = _REPO_ROOT / "shims/fixtures/_shared_consumer_a.c"
_CONSUMER_B = _REPO_ROOT / "shims/fixtures/_shared_consumer_b.c"


def _compile(src: Path) -> Path | None:
    if not _COMPILE_SH.exists() or not src.exists():
        return None
    out = Path(tempfile.mkdtemp(prefix="e_test_")) / (src.stem + ".o")
    try:
        subprocess.check_call(
            ["bash", str(_COMPILE_SH), str(src), str(out)],
            cwd=_REPO_ROOT)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return out


def _toolchain_available() -> bool:
    return _compile(_LIB_SRC) is not None


@unittest.skipUnless(_toolchain_available(),
    "i386 clang PE-COFF toolchain not available on this host")
class SharedLibraryPlacement(unittest.TestCase):
    """Place a tiny shared library, inspect the returned export map."""

    def setUp(self):
        self.lib_obj = _compile(_LIB_SRC)
        self.xbe = bytearray(b"\x00" * 0x10_000)
        self.session = ShimLayoutSession(xbe_data=self.xbe)
        self._alloc_calls: list[tuple[str, bytes]] = []

    def _alloc(self, name: str, placeholder: bytes) -> tuple[int, int]:
        """Linear scratch allocator — placements go in a contiguous
        region starting at offset / VA 0x1000 / 0x100000."""
        off = 0x1000 + sum(len(p) for _, p in self._alloc_calls)
        va = 0x100000 + sum(len(p) for _, p in self._alloc_calls)
        self._alloc_calls.append((name, placeholder))
        self.xbe[off:off + len(placeholder)] = placeholder
        return off, va

    def test_apply_places_library_and_returns_exports(self):
        exports = self.session.apply_shared_library(
            self.lib_obj, self._alloc)
        self.assertIn("_shared_double@4", exports,
            msg="shared_double is __stdcall with a 4-byte arg — "
                "clang mangles the COFF symbol as _shared_double@4")
        self.assertIn("_shared_triple@4", exports,
            msg="the library's second export must also appear")

    def test_second_apply_is_no_op(self):
        """Idempotence: re-applying the same library path should NOT
        re-allocate.  Pack authors who call apply_shared_library
        defensively shouldn't get duplicate placements."""
        self.session.apply_shared_library(self.lib_obj, self._alloc)
        allocs_after_first = len(self._alloc_calls)
        again = self.session.apply_shared_library(
            self.lib_obj, self._alloc)
        self.assertEqual(
            len(self._alloc_calls), allocs_after_first,
            msg="second apply_shared_library on the same path must "
                "not trigger any new allocations")
        # Export map still present (and correct).
        self.assertIn("_shared_double@4", again)

    def test_export_vas_lie_inside_placed_region(self):
        """Sanity: every exported VA must point somewhere inside
        the session's placed library bytes."""
        exports = self.session.apply_shared_library(
            self.lib_obj, self._alloc)
        allocated_vas = []
        for _, placeholder in self._alloc_calls:
            # Reconstruct the VA each allocation returned.
            base = 0x100000 + sum(
                len(p) for _, p in self._alloc_calls[:allocated_vas.__len__()])
            allocated_vas.append((base, base + len(placeholder)))
        placed_min = min(v[0] for v in allocated_vas)
        placed_max = max(v[1] for v in allocated_vas)
        for name, va in exports.items():
            self.assertTrue(
                placed_min <= va < placed_max,
                msg=f"{name!r} export VA 0x{va:X} is outside the "
                    f"placed region [0x{placed_min:X}, "
                    f"0x{placed_max:X})")

    def test_apply_library_with_no_externals_is_rejected(self):
        """Placing a library that exposes no externally-visible
        symbols is almost certainly a mistake; the session should
        refuse rather than silently cache an empty map.

        Two paths reach the rejection:

        - ``static``-only source: DCE eats the functions and the
          COFF has no landable sections.  ``layout_coff`` raises.
        - Non-static but private-only (e.g. all ``__attribute__
          ((visibility("hidden")))``): sections survive but no
          external symbols qualify.  ``apply_shared_library``'s own
          check raises with the ``no externally-visible`` message.

        Either way the shim author gets a loud error, which is what
        matters here."""
        with tempfile.NamedTemporaryFile(
            suffix=".c", delete=False, dir=_THIS_DIR) as f:
            f.write(b"static int inner(int x) { return x + 1; }\n"
                    b"static int other(int x) { return inner(x) * 2; }\n")
            src = Path(f.name)
        try:
            obj = _compile(src)
            self.assertIsNotNone(obj,
                msg="static-only source should still compile cleanly")
            s2 = ShimLayoutSession(xbe_data=bytearray(b"\x00" * 0x10_000))
            with self.assertRaises(ValueError) as ctx:
                s2.apply_shared_library(obj, self._alloc)
            msg = str(ctx.exception).lower()
            # Accept either of the two valid rejection messages.
            self.assertTrue(
                "no externally-visible" in msg
                or "no landable sections" in msg,
                msg=f"expected a 'no exports / no landable sections' "
                    f"error, got: {ctx.exception}")
        finally:
            src.unlink()


@unittest.skipUnless(_toolchain_available(),
    "i386 clang PE-COFF toolchain not available on this host")
class TwoConsumersShareTheSameHelper(unittest.TestCase):
    """End-to-end: compile two independent consumer shims that both
    call `shared_double`.  Apply the shared library once, lay out
    each consumer in turn, verify both REL32s target the same VA."""

    def setUp(self):
        self.lib_obj = _compile(_LIB_SRC)
        self.a_obj = _compile(_CONSUMER_A)
        self.b_obj = _compile(_CONSUMER_B)
        self.xbe = bytearray(b"\x00" * 0x10_000)
        self.session = ShimLayoutSession(xbe_data=self.xbe)
        self._alloc_cursor = 0
        self._va_cursor = 0x100000

    def _alloc(self, _name: str, placeholder: bytes) -> tuple[int, int]:
        off = 0x1000 + self._alloc_cursor
        va = self._va_cursor
        self._alloc_cursor += len(placeholder)
        self._va_cursor += len(placeholder)
        self.xbe[off:off + len(placeholder)] = placeholder
        return off, va

    def _layout_consumer(
        self,
        obj: Path,
        entry: str,
    ) -> tuple[int, object]:
        coff = parse_coff(obj.read_bytes())
        resolver = self.session.make_extern_resolver(self._alloc)
        landed = layout_coff(
            coff, entry,
            allocate=self._alloc,
            extern_resolver=resolver,
        )
        text_coff = coff.section(".text")
        rel = next(
            r for r in text_coff.relocations
            if coff.symbols[r.symbol_index].name == "_shared_double@4"
            and r.type == IMAGE_REL_I386_REL32)
        text_landed = next(s for s in landed.sections if s.name == ".text")
        rel32 = struct.unpack_from("<i", text_landed.data, rel.va)[0]
        site_va = text_landed.vaddr + rel.va
        call_target = site_va + 4 + rel32
        return call_target, landed

    def test_both_consumers_resolve_to_same_helper_va(self):
        """Core E assertion: two independent shims that both call
        `shared_double` end up with REL32 targets equal to the SAME
        VA (the shared helper's single placement)."""
        self.session.apply_shared_library(self.lib_obj, self._alloc)
        helper_va = self.session._shared_exports["_shared_double@4"]

        target_a, _ = self._layout_consumer(self.a_obj, "_c_consumer_a@4")
        target_b, _ = self._layout_consumer(self.b_obj, "_c_consumer_b@4")

        self.assertEqual(
            target_a, helper_va,
            msg="consumer A's CALL must target the shared-lib helper VA")
        self.assertEqual(
            target_b, helper_va,
            msg="consumer B's CALL must target the shared-lib helper VA")
        self.assertEqual(
            target_a, target_b,
            msg="both consumers must resolve to the SAME VA — that's "
                "the entire point of shared-library layout (E); "
                "diverging VAs means each consumer installed its "
                "own private copy")

    def test_shared_lib_not_re_placed_between_consumers(self):
        """Allocation count should reflect: placing the library once,
        then placing each consumer's sections.  NOT placing the
        library twice."""
        self.session.apply_shared_library(self.lib_obj, self._alloc)
        calls_after_lib = self._alloc_cursor
        self._layout_consumer(self.a_obj, "_c_consumer_a@4")
        after_a = self._alloc_cursor
        self._layout_consumer(self.b_obj, "_c_consumer_b@4")
        after_b = self._alloc_cursor

        # Each consumer should have added at most its own sections
        # (.text + .rdata + maybe .data).  If the library's sections
        # were re-placed, we'd see the alloc cursor jump by the whole
        # library size again — roughly doubling the post-library
        # delta.
        a_delta = after_a - calls_after_lib
        b_delta = after_b - after_a
        lib_size = calls_after_lib  # rough proxy
        self.assertLess(
            a_delta, lib_size,
            msg="consumer A's allocations grew by ~the library's size "
                "— the library was almost certainly re-placed")
        self.assertLess(
            b_delta, lib_size,
            msg="consumer B's allocations grew by ~the library's size "
                "— the library was almost certainly re-placed")


if __name__ == "__main__":
    unittest.main()
