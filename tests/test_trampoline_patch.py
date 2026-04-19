"""Low-level tests for the C-shim trampoline pipeline.

These tests pin the Phase 1 behaviour of:

- ``azurik_mod.patching.coff`` — PE-COFF section/symbol extraction.
- ``azurik_mod.patching.xbe.find_text_padding`` — landing-pad discovery
  (both in-section slack and the adjacent-VA-gap growth path).
- ``azurik_mod.patching.xbe.grow_text_section`` — header-only section
  growth.
- ``apply_trampoline_patch`` / ``verify_trampoline_patch`` — end-to-end
  on synthetic fixtures AND on the real Azurik XBE when present.
- The ``cmd_verify_patches --strict`` whitelist path that absorbs
  trampoline sites, the shim landing pad, and the grown ``.text``
  header fields.

Shim ``.o`` fixtures are compiled on demand by
``shims/toolchain/compile.sh``; tests skip gracefully if the compiler
isn't available on the host so CI environments lacking i386 clang
can still run everything else.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from azurik_mod.patching.coff import (  # noqa: E402
    extract_shim_bytes,
    parse_coff,
)
from azurik_mod.patching.spec import TrampolinePatch  # noqa: E402
from azurik_mod.patching.xbe import (  # noqa: E402
    find_text_padding,
    grow_text_section,
    parse_xbe_sections,
    va_to_file,
)
from azurik_mod.patching.apply import (  # noqa: E402
    apply_trampoline_patch,
    verify_trampoline_patch,
)


_VANILLA_XBE = _REPO_ROOT.parent / "Azurik - Rise of Perathia (USA).xiso/default.xbe"
_SHIM_SRC = _REPO_ROOT / "azurik_mod/patches/qol_skip_logo/shim.c"
_SHIM_OBJ = _REPO_ROOT / "shims/build/qol_skip_logo.o"
_COMPILE_SH = _REPO_ROOT / "shims/toolchain/compile.sh"


def _ensure_skip_logo_shim() -> bool:
    """Make sure ``shims/build/qol_skip_logo.o`` exists (compile if we
    can).  Returns True if the shim is available after this call,
    False if the host environment can't produce one.  Tests that need
    the shim skip on False.

    Post-reorganisation the source lives inside the feature folder
    at ``azurik_mod/patches/qol_skip_logo/shim.c``; the compiled .o
    is keyed on the pack name (``qol_skip_logo.o``) so two features
    whose source files both happen to be called ``shim.c`` can't
    collide in the shared build cache.
    """
    if _SHIM_OBJ.exists():
        return True
    if not _SHIM_SRC.exists() or not _COMPILE_SH.exists():
        return False
    try:
        _SHIM_OBJ.parent.mkdir(parents=True, exist_ok=True)
        subprocess.check_call(
            ["bash", str(_COMPILE_SH), str(_SHIM_SRC), str(_SHIM_OBJ)],
            cwd=_REPO_ROOT)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return _SHIM_OBJ.exists()


# ===========================================================================
# COFF parser
# ===========================================================================


@unittest.skipUnless(_ensure_skip_logo_shim(),
    "i386 PE-COFF shim toolchain unavailable — install clang with "
    "-target i386-pc-win32 support to exercise the COFF path.")
class CoffParser(unittest.TestCase):
    """Pin the minimum behaviour the trampoline pipeline depends on."""

    def setUp(self):
        self.coff = parse_coff(_SHIM_OBJ.read_bytes())

    def test_machine_is_i386(self):
        self.assertEqual(self.coff.machine, 0x014C,
            msg="Phase 1 shims MUST target i386 (0x014C).  If this "
                "drifts someone changed compile.sh's -target flag.")

    def test_has_text_section(self):
        text = self.coff.section(".text")
        self.assertGreater(text.raw_size, 0,
            msg=".text section should carry shim code bytes.")

    def test_text_section_has_no_relocations(self):
        text = self.coff.section(".text")
        self.assertEqual(text.reloc_count, 0,
            msg="Phase 1 requires zero .text relocations; the shim "
                "must be pure arithmetic / control flow.  If this fails, "
                "the shim has pulled in a global or function import.")

    def test_skip_logo_symbol_resolves(self):
        """The shim compiles to a RET — we don't care exactly where it
        lives in .text, only that we can find it by name."""
        sym = self.coff.symbol("_c_skip_logo")
        self.assertGreater(sym.section_number, 0,
            msg="_c_skip_logo must live in a real section, not be "
                "an absolute / debug symbol.")

    def test_extract_shim_bytes_returns_text_and_offset(self):
        text_bytes, sym_offset = extract_shim_bytes(
            self.coff, "_c_skip_logo")
        # The skip_logo shim is a naked `xor %al, %al; ret $8` —
        # compiled bytes should be 30 C0 C2 08 00.
        self.assertEqual(text_bytes, bytes([0x30, 0xC0, 0xC2, 0x08, 0x00]),
            msg="skip_logo shim must be exactly XOR AL,AL + RET 8 "
                "(5 bytes).  A RET without the 8-byte pop would leak "
                "caller args on every boot tick; a RET with a "
                "different immediate would corrupt the stack.")
        self.assertEqual(sym_offset, 0,
            msg="Shim symbol starts at offset 0 of its .text section.")

    def test_extract_unknown_symbol_raises(self):
        with self.assertRaises(KeyError):
            extract_shim_bytes(self.coff, "_c_nonexistent_symbol")


# ===========================================================================
# XBE surgery utilities
# ===========================================================================


@unittest.skipUnless(_VANILLA_XBE.exists(),
    f"vanilla Azurik XBE required at {_VANILLA_XBE}")
class FindTextPadding(unittest.TestCase):
    """Azurik's .text has zero in-section trailing slack but 16 bytes
    of adjacent VA-gap headroom before BINK.  find_text_padding must
    report that correctly."""

    def test_reports_sixteen_bytes_of_landing_space(self):
        xbe = _VANILLA_XBE.read_bytes()
        offset, length = find_text_padding(xbe)
        # The landing pad starts at raw_end of .text (no in-section
        # slack) and spans the 16-byte adjacent VA gap.
        self.assertEqual(length, 16,
            msg="If this drifts the XBE has been repacked or the "
                "section layout changed; every other trampoline "
                "assumption depends on this number.")
        # Offset should be inside the 0xF01D0..0xF01E0 region.
        self.assertEqual(offset, 0xF01D0,
            msg="Landing pad file offset must sit at .text raw_end.")

    def test_no_text_raises(self):
        fake = bytearray(b"XBEH" + b"\x00" * 0x200)
        # Fabricate a header with zero sections so parse_xbe_sections
        # returns no .text.  A real malformed XBE would blow up
        # earlier; we just care that find_text_padding complains.
        # section_count = 0 at +0x11C, headers_addr at +0x120 unused.
        import struct
        struct.pack_into("<I", fake, 0x104, 0)      # base_addr
        struct.pack_into("<I", fake, 0x11C, 0)      # section_count
        struct.pack_into("<I", fake, 0x120, 0)      # section_headers_addr
        with self.assertRaises(ValueError):
            find_text_padding(bytes(fake))


@unittest.skipUnless(_VANILLA_XBE.exists(),
    f"vanilla Azurik XBE required at {_VANILLA_XBE}")
class GrowTextSection(unittest.TestCase):
    def test_grow_updates_vsize_and_rawsize(self):
        xbe = bytearray(_VANILLA_XBE.read_bytes())
        _, before = parse_xbe_sections(bytes(xbe))
        text_before = next(s for s in before if s["name"] == ".text")

        grow_text_section(xbe, 4)

        _, after = parse_xbe_sections(bytes(xbe))
        text_after = next(s for s in after if s["name"] == ".text")
        self.assertEqual(text_after["vsize"],
                         text_before["vsize"] + 4)
        self.assertEqual(text_after["raw_size"],
                         text_before["raw_size"] + 4)

    def test_grow_by_zero_is_noop(self):
        xbe = bytearray(_VANILLA_XBE.read_bytes())
        snapshot = bytes(xbe)
        grow_text_section(xbe, 0)
        self.assertEqual(bytes(xbe), snapshot)


# ===========================================================================
# End-to-end trampoline apply on the vanilla Azurik XBE
# ===========================================================================


@unittest.skipUnless(_VANILLA_XBE.exists() and _ensure_skip_logo_shim(),
    "needs vanilla XBE fixture AND a working i386 shim toolchain")
class ApplyAndVerify(unittest.TestCase):

    def _patch(self) -> TrampolinePatch:
        # Points at the 5-byte CALL at 0x05F6E5.  The preceding PUSHes
        # at 0x05F6DF and 0x05F6E0 intentionally stay vanilla so the
        # shim receives both __stdcall args on its stack.
        return TrampolinePatch(
            name="skip_logo",
            label="Skip AdreniumLogo (C shim)",
            va=0x05F6E5,
            replaced_bytes=bytes([0xE8, 0x96, 0x92, 0xFB, 0xFF]),
            shim_object=Path("shims/build/qol_skip_logo.o"),
            shim_symbol="_c_skip_logo",
            mode="call",
        )

    def test_apply_emits_call_rel32_with_no_tail_pad(self):
        xbe = bytearray(_VANILLA_XBE.read_bytes())
        patch = self._patch()
        ok = apply_trampoline_patch(xbe, patch, repo_root=_REPO_ROOT)
        self.assertTrue(ok)
        off = va_to_file(patch.va)
        site = bytes(xbe[off:off + 5])
        self.assertEqual(site[0], 0xE8,
            msg="first byte of trampoline must be CALL rel32 (0xE8)")
        # replaced_bytes is exactly 5 bytes so no NOP tail should be
        # written — any NOPs would mean the pipeline miscounted.
        # Verify the NEG AL instruction at 0x05F6EA is untouched.
        neg_al_off = va_to_file(0x05F6EA)
        self.assertEqual(xbe[neg_al_off], 0xF6,
            msg="NEG AL at 0x05F6EA must survive the patch; the "
                "state machine reads the shim's AL return value here.")

    def test_rel32_points_inside_new_text(self):
        """The CALL rel32 target must land inside .text after growth."""
        import struct
        xbe = bytearray(_VANILLA_XBE.read_bytes())
        patch = self._patch()
        apply_trampoline_patch(xbe, patch, repo_root=_REPO_ROOT)

        off = va_to_file(patch.va)
        rel32 = struct.unpack_from("<i", xbe, off + 1)[0]
        target_va = patch.va + 5 + rel32

        _, secs = parse_xbe_sections(bytes(xbe))
        text = next(s for s in secs if s["name"] == ".text")
        text_vend = text["vaddr"] + text["vsize"]
        self.assertTrue(
            text["vaddr"] <= target_va < text_vend,
            msg=f"trampoline target 0x{target_va:X} must fall inside "
                f".text [0x{text['vaddr']:X}, 0x{text_vend:X})")

    def test_verify_says_applied(self):
        xbe = bytearray(_VANILLA_XBE.read_bytes())
        patch = self._patch()
        apply_trampoline_patch(xbe, patch, repo_root=_REPO_ROOT)
        self.assertEqual(verify_trampoline_patch(bytes(xbe), patch), "applied")

    def test_verify_says_original_on_vanilla(self):
        xbe = _VANILLA_XBE.read_bytes()
        patch = self._patch()
        self.assertEqual(verify_trampoline_patch(xbe, patch), "original")

    def test_apply_is_idempotent(self):
        """Running apply twice on the same buffer must not mutate it
        the second time (the "already applied" branch catches the
        double-apply)."""
        xbe = bytearray(_VANILLA_XBE.read_bytes())
        patch = self._patch()
        apply_trampoline_patch(xbe, patch, repo_root=_REPO_ROOT)
        snapshot = bytes(xbe)
        apply_trampoline_patch(xbe, patch, repo_root=_REPO_ROOT)
        self.assertEqual(bytes(xbe), snapshot,
            msg="A second apply call must detect the existing "
                "trampoline and leave every byte untouched.")

    def test_replaced_bytes_too_short_refuses(self):
        bad = self._patch()._replace(
            replaced_bytes=bytes([0xCC, 0xCC, 0xCC]))  # 3 bytes < 5
        xbe = bytearray(_VANILLA_XBE.read_bytes())
        ok = apply_trampoline_patch(xbe, bad, repo_root=_REPO_ROOT)
        self.assertFalse(ok,
            msg="replaced_bytes < 5 can't fit a rel32 trampoline")

    def test_mismatched_site_refuses(self):
        bad = self._patch()._replace(
            replaced_bytes=bytes([0xAA] * 10))  # definitely not vanilla
        xbe = bytearray(_VANILLA_XBE.read_bytes())
        ok = apply_trampoline_patch(xbe, bad, repo_root=_REPO_ROOT)
        self.assertFalse(ok,
            msg="site bytes don't match vanilla or an existing "
                "trampoline — apply must refuse rather than guess.")


# ===========================================================================
# Registry integration + strict verify whitelist
# ===========================================================================


class RegistryEnumeration(unittest.TestCase):
    def test_all_trampoline_sites_lists_skip_logo(self):
        import azurik_mod.patches  # noqa: F401
        from azurik_mod.patching.registry import all_trampoline_sites
        trampolines = all_trampoline_sites()
        names = [t.name for _pack, t in trampolines]
        self.assertIn("skip_logo", names,
            msg="all_trampoline_sites() must enumerate the "
                "qol_skip_logo.skip_logo trampoline.")


if __name__ == "__main__":
    unittest.main()
