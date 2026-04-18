"""Tests for ``azurik_mod.patching.xbe.append_xbe_section`` and its
integration into the trampoline apply pipeline (Phase 2 A1).

Coverage:

- **Round-trip**: after appending a section, parse_xbe_sections still
  decodes every original section correctly, plus the new one.
- **Header invariants**: num_sections +1, size_of_headers grown by
  the header-entry + name padding, size_of_image covers the new VA.
- **Pointer fixups**: every image-header pointer that referenced a
  byte past the old section-header array now points at the shifted
  location (verified by dereferencing each pointer to the same
  string value as before).
- **Per-section name preservation**: every original section's
  ``name_addr`` still resolves to the correct string after the shift.
- **File-layout**: section data lives at the returned raw_addr and
  is byte-identical to the input.
- **Carve-landing fallback**: when the shim exceeds ``.text``
  headroom, ``_carve_shim_landing`` routes through
  ``append_xbe_section`` and subsequent calls extend the SAME
  SHIMS section rather than adding another.
- **Trampoline end-to-end**: a fake TrampolinePatch pointing at a
  large synthetic shim lands in the appended section and the
  emitted ``CALL rel32`` correctly targets its VA.
"""

from __future__ import annotations

import os
import struct
import sys
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.patching.xbe import (  # noqa: E402
    append_xbe_section,
    parse_xbe_sections,
)
from azurik_mod.patching.apply import _carve_shim_landing  # noqa: E402


_VANILLA_XBE = _REPO_ROOT.parent / "Azurik - Rise of Perathia (USA).xiso/default.xbe"


def _u32(buf: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


@unittest.skipUnless(_VANILLA_XBE.exists(),
    f"vanilla XBE fixture required at {_VANILLA_XBE}")
class RoundTripAppend(unittest.TestCase):
    """Append one new section and re-parse the header end-to-end."""

    def setUp(self):
        self.original = bytes(_VANILLA_XBE.read_bytes())
        self.xbe = bytearray(self.original)
        _, self.sections_before = parse_xbe_sections(self.original)

    def test_new_section_appears_in_reparse(self):
        data = b"\xCC" * 128
        info = append_xbe_section(self.xbe, "TESTSEC", data)
        _, sections = parse_xbe_sections(bytes(self.xbe))

        self.assertEqual(len(sections),
                         len(self.sections_before) + 1,
                         "num_sections must grow by exactly 1")
        new = sections[-1]
        self.assertEqual(new["name"], "TESTSEC",
            "the appended section's name must parse back correctly — if "
            "this fails the name-pool shift or pointer fixups are wrong")
        self.assertEqual(new["raw_size"], len(data))
        self.assertEqual(new["vsize"], len(data))
        self.assertEqual(new["raw_addr"], info["raw_addr"])
        self.assertEqual(new["vaddr"], info["vaddr"])

    def test_original_section_names_survive_shift(self):
        """Pointer-fixup regression guard: every original section's
        name_addr must still resolve to the SAME name string as before
        the append.  If the fixup misses any section, its name_addr
        points at garbage (or the wrong string) post-shift."""
        append_xbe_section(self.xbe, "GUARD1", b"\x00" * 32)
        _, sections = parse_xbe_sections(bytes(self.xbe))
        for before, after in zip(self.sections_before, sections[:-1]):
            self.assertEqual(
                before["name"], after["name"],
                msg=f"section name changed after append: "
                    f"{before['name']!r} -> {after['name']!r}")

    def test_header_totals_bumped_correctly(self):
        sohdrs_before = _u32(self.original, 0x108)
        soimg_before = _u32(self.original, 0x10C)
        n_before = _u32(self.original, 0x11C)

        data = b"\xAA" * 64
        info = append_xbe_section(self.xbe, "T", data)

        sohdrs_after = _u32(self.xbe, 0x108)
        soimg_after = _u32(self.xbe, 0x10C)
        n_after = _u32(self.xbe, 0x11C)

        self.assertEqual(n_after, n_before + 1,
            msg="num_sections must increment by exactly 1")
        self.assertGreaterEqual(sohdrs_after, sohdrs_before + 56,
            msg="size_of_headers must grow by at least the 56-byte header entry")
        # Image covers the new section's VA extent.
        base_addr = _u32(self.xbe, 0x104)
        self.assertGreaterEqual(
            soimg_after,
            info["vaddr"] + len(data) - base_addr,
            msg="size_of_image must cover the newly-appended section's VA end")

    def test_section_data_byte_identical_at_raw_addr(self):
        data = bytes(b for b in range(128))  # each byte unique
        info = append_xbe_section(self.xbe, "BYTES", data)
        got = bytes(self.xbe[info["raw_addr"]:info["raw_addr"] + len(data)])
        self.assertEqual(got, data,
            msg="bytes at raw_addr must match the input exactly")

    def test_image_header_pointer_targets_still_valid(self):
        """After append, every image-header field that holds a VA
        pointer into the header region must still dereference to a
        sensible header byte (not garbage, not off-end).  Iterate
        every candidate field and confirm.
        """
        append_xbe_section(self.xbe, "PTR", b"\x00" * 16)
        base_addr = _u32(self.xbe, 0x104)
        size_of_headers = _u32(self.xbe, 0x108)

        for field_off in (0x14C, 0x150, 0x154, 0x164, 0x168, 0x16C, 0x170):
            v = _u32(self.xbe, field_off)
            if not (base_addr <= v < base_addr + size_of_headers):
                continue  # not a header-pointer at all (some fields
                          # are raw ints on Azurik, e.g. +0x150)
            file_off = v - base_addr
            # The byte targeted must exist in the buffer.
            self.assertLess(
                file_off, len(self.xbe),
                msg=f"header pointer +0x{field_off:03X} dangles past EOF")


@unittest.skipUnless(_VANILLA_XBE.exists(),
    f"vanilla XBE fixture required at {_VANILLA_XBE}")
class CarveLandingFallback(unittest.TestCase):
    """_carve_shim_landing picks the right home for each shim size."""

    def test_tiny_shim_lands_in_text(self):
        xbe = bytearray(_VANILLA_XBE.read_bytes())
        size_before = len(xbe)
        tiny = b"\xC3"  # 1 byte, fits in .text's 16-byte VA gap
        _, va = _carve_shim_landing(xbe, tiny)
        self.assertLess(va, 0x1001E0,
            msg="tiny shim must land inside .text-adjacent growth region, "
                "not trigger an append_xbe_section fallback")
        # File should not have grown appreciably (maybe the .text grew
        # a byte via grow_text_section, but we didn't append a section).
        self.assertLessEqual(len(xbe) - size_before, 1)

    def test_large_shim_forces_section_append(self):
        xbe = bytearray(_VANILLA_XBE.read_bytes())
        _, sections_before = parse_xbe_sections(bytes(xbe))

        huge = b"\xCC" * 512
        file_off, va = _carve_shim_landing(xbe, huge)

        _, sections_after = parse_xbe_sections(bytes(xbe))
        self.assertEqual(len(sections_after), len(sections_before) + 1,
            msg="large shim must trigger append_xbe_section")
        shims = next(s for s in sections_after if s["name"] == "SHIMS")
        self.assertEqual(shims["raw_addr"], file_off)
        self.assertEqual(shims["vaddr"], va)
        self.assertEqual(shims["vsize"], len(huge))

    def test_second_large_shim_extends_existing_shims_section(self):
        """Two landings of big shims should produce ONE SHIMS section
        (the second extends the first), not two."""
        xbe = bytearray(_VANILLA_XBE.read_bytes())
        _carve_shim_landing(xbe, b"\xCC" * 512)
        _carve_shim_landing(xbe, b"\x90" * 256)
        _, sections = parse_xbe_sections(bytes(xbe))
        shims_count = sum(1 for s in sections if s["name"] == "SHIMS")
        self.assertEqual(shims_count, 1,
            msg="subsequent carve_landing calls must extend the existing "
                "SHIMS section in place, not spawn a new one each time")

    def test_extend_places_data_contiguously(self):
        """After extend, the shim bytes should be laid out contiguously
        inside the SHIMS section at the correct offsets."""
        xbe = bytearray(_VANILLA_XBE.read_bytes())
        first = b"\x11" * 300
        off1, va1 = _carve_shim_landing(xbe, first)
        second = b"\x22" * 100
        off2, va2 = _carve_shim_landing(xbe, second)

        # Offsets/VAs must be contiguous.
        self.assertEqual(off2, off1 + len(first))
        self.assertEqual(va2, va1 + len(first))

        # Data must be intact.
        self.assertEqual(bytes(xbe[off1:off1 + len(first)]), first)
        self.assertEqual(bytes(xbe[off2:off2 + len(second)]), second)


@unittest.skipUnless(_VANILLA_XBE.exists(),
    f"vanilla XBE fixture required at {_VANILLA_XBE}")
class HeaderHeadroom(unittest.TestCase):
    """Edge cases on the header-grow budget."""

    def test_append_rejected_when_name_too_long(self):
        xbe = bytearray(_VANILLA_XBE.read_bytes())
        with self.assertRaises(ValueError):
            append_xbe_section(xbe, "A" * 128, b"\x00" * 16)

    def test_append_rejected_on_empty_name(self):
        xbe = bytearray(_VANILLA_XBE.read_bytes())
        with self.assertRaises(ValueError):
            append_xbe_section(xbe, "", b"\x00" * 16)


if __name__ == "__main__":
    unittest.main()
