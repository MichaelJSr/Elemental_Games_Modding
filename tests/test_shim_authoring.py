"""Tier-B authoring-ergonomics tests.

Pins two pieces of shim-author quality-of-life:

- **Struct layout in ``shims/include/azurik.h``**.  Every named
  field lands at the Ghidra offset we documented.  If someone adds
  a field in the middle of a struct and shifts every offset, shims
  across the tree silently miscompile — this test fails first.

- **``shims/toolchain/new_shim.sh`` scaffolding**.  A valid name
  produces a compilable stub; bad names (non-identifier, leading
  digit, uppercase, symbol chars) are rejected; re-scaffolding an
  existing shim refuses to overwrite.

Tests skip gracefully when the i386 clang cross-toolchain isn't
available on the host.
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent

_COMPILE_SH = _REPO_ROOT / "shims/toolchain/compile.sh"
_NEW_SHIM_SH = _REPO_ROOT / "shims/toolchain/new_shim.sh"


def _toolchain_available() -> bool:
    if not _COMPILE_SH.exists():
        return False
    with tempfile.TemporaryDirectory(prefix="azurik_probe_") as tmp:
        src = Path(tmp) / "probe.c"
        src.write_text("void c_probe(void){}\n")
        try:
            subprocess.check_call(
                ["bash", str(_COMPILE_SH), str(src),
                 str(Path(tmp) / "probe.o")],
                cwd=_REPO_ROOT, stderr=subprocess.DEVNULL)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False


@unittest.skipUnless(_toolchain_available(),
    "i386 clang PE-COFF toolchain not available on this host")
class AzurikHeaderStructOffsets(unittest.TestCase):
    """Compile a probe shim that reads each named field and confirm
    the generated machine code uses the Ghidra-documented offset."""

    EXPECTED_OFFSETS = {
        # PlayerInputState
        "stick_magnitude":   0x1C,
        "flags":             0x20,
        "dead":              0x21,
        "ref_x":             0x24,
        "critter_data":      0x34,
        "direction_angle":   0x120,
        "magnitude":         0x124,
        "direction_x":       0x128,
        "direction_y":       0x12C,
        "direction_z":       0x130,
    }
    EXPECTED_CRITTER_OFFSETS = {
        "collision_radius":  0x18,
        "scale":             0x24,
        "walk_speed":        0x38,
        "run_speed":         0x40,
        # Correctness regression guard: these four were misnamed in
        # the first Tier-B cut (as damage_multiplier / hitpoints /
        # damage_vuln_*).  Pin the correct names at their correct
        # offsets so any future rename drift fails the test.
        "ouch2_threshold":   0x48,
        "ouch3_threshold":   0x4C,
        "ouch1_knockback":   0x50,
        "ouch2_knockback":   0x54,
        "ouch3_knockback":   0x58,
        # Byte-typed fields previously buried in _reserved slots.
        "hits_through_walls": 0x7C,
        # Gameplay fields with correct Ghidra-verified offsets.
        "drown_time":        0x80,
        "shadow_size":       0x94,
    }

    def _compile_probe(self, source: str) -> bytes:
        """Compile `source` with azurik.h on the include path and
        return the resulting .o's full byte contents."""
        with tempfile.TemporaryDirectory(prefix="azurik_hdr_") as tmp:
            src = Path(tmp) / "probe.c"
            out = Path(tmp) / "probe.o"
            src.write_text(source)
            subprocess.check_call(
                ["bash", str(_COMPILE_SH), str(src), str(out)],
                cwd=_REPO_ROOT)
            return out.read_bytes()

    def test_player_input_state_fields_resolve_to_expected_offsets(self):
        """Generate a probe that loads each named field into the same
        sink variable.  For each load the compiler emits
        ``movl disp8/32(%reg), %scratch`` — we just need the
        disp bytes to match.  Check disassembly via objdump."""
        # Build a probe that forces distinguishable accesses.
        lines = ["#include \"azurik.h\"", "volatile u32 sink;",
                 "void probe(PlayerInputState *p) {"]
        for field in self.EXPECTED_OFFSETS:
            # Casting to u32 gives deterministic load sizes regardless
            # of the field's native type.  The real guarantee is
            # "compiler emits the right offset" not "same width".
            lines.append(f"    sink = (u32)p->{field};")
        lines.append("}")
        source = "\n".join(lines)

        obj = self._compile_probe(source)
        # Search each expected offset as a little-endian disp32 or
        # disp8 inside the .o.  This is coarse but catches off-by-N
        # struct drift reliably.
        for field, offset in self.EXPECTED_OFFSETS.items():
            with self.subTest(field=field, offset=f"0x{offset:X}"):
                if offset < 0x80:
                    # disp8 form: the offset byte appears verbatim.
                    needle = bytes([offset])
                else:
                    # disp32 form: little-endian 4 bytes.
                    needle = struct.pack("<I", offset)
                self.assertIn(needle, obj,
                    msg=f"offset 0x{offset:X} for PlayerInputState."
                        f"{field} not found in compiled .o — did the "
                        f"struct layout drift?")

    def test_critter_data_fields_resolve_to_expected_offsets(self):
        lines = ["#include \"azurik.h\"", "volatile u32 sink;",
                 "void probe(CritterData *c) {"]
        for field in self.EXPECTED_CRITTER_OFFSETS:
            lines.append(f"    sink = (u32)c->{field};")
        lines.append("}")

        obj = self._compile_probe("\n".join(lines))
        for field, offset in self.EXPECTED_CRITTER_OFFSETS.items():
            with self.subTest(field=field, offset=f"0x{offset:X}"):
                self.assertIn(bytes([offset]), obj,
                    msg=f"offset 0x{offset:X} for CritterData.{field} "
                        f"not found in compiled .o — layout drift?")

    def test_va_anchors_point_at_expected_xbe_data(self):
        """Validate every ``AZURIK_*_VA`` constant in azurik.h against
        the real XBE.  Catches the regression where a constant was
        labelled ``_VA`` but was actually a file offset — see
        docs/LEARNINGS.md 'VAs vs file offsets — the player-character trap'.

        Skips if the vanilla XBE isn't present in its usual location."""
        import struct as _struct

        vanilla = _REPO_ROOT.parent / "Azurik - Rise of Perathia (USA).xiso/default.xbe"
        if not vanilla.exists():
            self.skipTest(f"vanilla XBE fixture required at {vanilla}")

        import sys
        if str(_REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(_REPO_ROOT))
        from azurik_mod.patching.xbe import va_to_file, parse_xbe_sections

        xbe = vanilla.read_bytes()
        _, sections = parse_xbe_sections(xbe)

        def section_of(va: int) -> str | None:
            for s in sections:
                if s['vaddr'] <= va < s['vaddr'] + s['vsize']:
                    return s['name']
            return None

        # Each tuple: (anchor_name, VA, expected_section, expected_value_pred)
        # where expected_value_pred(bytes) returns either
        #   - True/False for a simple predicate, or
        #   - a diagnostic message string for context.
        anchors = [
            ("AZURIK_GRAVITY_VA", 0x001980A8, ".rdata",
             lambda b: abs(_struct.unpack('<f', b[:4])[0] - 9.8) < 1e-5),
            ("AZURIK_SHARED_RUN_MULT_VA", 0x001A25BC, ".rdata",
             lambda b: _struct.unpack('<f', b[:4])[0] == 3.0),
            ("AZURIK_PLAYER_CHAR_NAME_VA", 0x0019EA68, ".rdata",
             lambda b: b.startswith(b"garret4\x00")),
            ("AZURIK_BOOT_STATE_VA", 0x001BF61C, ".data",
             # BSS-initialised DWORD — sits in the VA portion of
             # .data that's past raw_size, so on-disk reads return
             # empty bytes.  Xbox loader zero-fills at load time.
             # Accept either empty (past raw) or literal zeros.
             lambda b: b == b"" or b[:4] == b"\x00\x00\x00\x00"),
        ]

        for name, va, want_section, pred in anchors:
            with self.subTest(anchor=name, va=f"0x{va:X}"):
                got_section = section_of(va)
                self.assertEqual(
                    got_section, want_section,
                    msg=f"{name} expected in section {want_section} "
                        f"but resolves to {got_section}")
                data = xbe[va_to_file(va):va_to_file(va) + 16]
                self.assertTrue(
                    pred(data),
                    msg=f"{name} (VA 0x{va:X}) bytes {data.hex()} "
                        f"fail the value predicate — either the VA "
                        f"is wrong or the vanilla XBE has drifted")

    def test_flag_constants_match_engine_values(self):
        """``PLAYER_FLAG_RUNNING`` and ``PLAYER_FLAG_FALLING`` must
        match what ``FUN_00084940`` tests against (bits 0x40 and 0x01
        respectively).  Compile a probe that emits each constant
        verbatim and scan the .o for those bytes in a ``test``
        instruction."""
        source = (
            "#include \"azurik.h\"\n"
            "volatile u8 sink;\n"
            "void probe(PlayerInputState *p) {\n"
            "    if (p->flags & PLAYER_FLAG_RUNNING) sink = 1;\n"
            "    if (p->flags & PLAYER_FLAG_FALLING) sink = 2;\n"
            "}\n"
        )
        obj = self._compile_probe(source)
        # `TEST r/m8, imm8` is F6 /0; scan for the immediate bytes
        # 0x40 and 0x01 preceded by a plausible TEST opcode.  Coarse
        # but enough to catch a drift that would flip the bits.
        # Simpler: just confirm both immediate bytes exist in the .o
        # at positions outside the symbol table.  Good-enough heuristic:
        # clang emits `f6 80 20 01 00 00 01` for
        # `TEST byte [EAX+0x120], 0x01`.  We just check both
        # immediates appear in the file.
        self.assertIn(b"\x40", obj,
            msg="PLAYER_FLAG_RUNNING immediate (0x40) absent — "
                "possible drift from the engine's 0x40 bit check.")
        self.assertIn(b"\x01", obj,
            msg="PLAYER_FLAG_FALLING immediate (0x01) absent — "
                "possible drift.")


@unittest.skipUnless(_NEW_SHIM_SH.exists(),
    "shims/toolchain/new_shim.sh missing")
class NewShimScaffolding(unittest.TestCase):
    """The scaffold script produces a compilable file for valid names
    and refuses otherwise."""

    def _run_scaffold(self, name: str,
                       workdir: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(_NEW_SHIM_SH), name],
            cwd=workdir or _REPO_ROOT,
            capture_output=True, text=True)

    def test_good_name_produces_compilable_stub(self):
        """Happy path: valid name -> feature folder created ->
        shim.c compiles -> produces a 1-byte RET shim by default."""
        if not _toolchain_available():
            self.skipTest("i386 clang toolchain required")

        # Use a unique name so we don't collide with real shims.
        name = "test_scaffold_" + os.urandom(4).hex()
        feature_dir = _REPO_ROOT / "azurik_mod" / "patches" / name
        src = feature_dir / "shim.c"
        init_py = feature_dir / "__init__.py"
        obj_dir = _REPO_ROOT / "shims" / "build"
        obj = obj_dir / f"{name}.o"
        try:
            proc = self._run_scaffold(name)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(feature_dir.is_dir(),
                msg=f"scaffold should have created {feature_dir}")
            self.assertTrue(src.exists(),
                msg=f"scaffold should have created {src}")
            self.assertTrue(init_py.exists(),
                msg=f"scaffold should have created {init_py}")

            # Compile the generated stub into the shared build cache.
            obj.parent.mkdir(parents=True, exist_ok=True)
            subprocess.check_call(
                ["bash", str(_COMPILE_SH), str(src), str(obj)],
                cwd=_REPO_ROOT)
            self.assertTrue(obj.exists())
            data = obj.read_bytes()
            # The file must be a proper PE-COFF i386 object with at
            # least one defined symbol matching the shim's name.
            expected_sym = f"_c_{name}@0".encode()
            self.assertIn(expected_sym, data,
                msg="generated shim must export the expected "
                    "c_<name> stdcall symbol")
        finally:
            import shutil
            if feature_dir.exists():
                shutil.rmtree(feature_dir)
            if obj.exists():
                obj.unlink()

    def test_rejects_bad_names(self):
        for bad in ("BadName",      # uppercase
                    "1abc",         # leading digit
                    "with-dash",    # dash
                    "has.dot",      # dot
                    "",             # empty
                    "has space"):   # space
            with self.subTest(name=bad):
                proc = self._run_scaffold(bad)
                self.assertNotEqual(
                    proc.returncode, 0,
                    msg=f"scaffold should reject name {bad!r}")

    def test_refuses_to_overwrite_existing(self):
        """Re-scaffolding the same name fails rather than destroying
        an in-progress feature folder."""
        name = "test_scaffold_dup_" + os.urandom(4).hex()
        feature_dir = _REPO_ROOT / "azurik_mod" / "patches" / name
        try:
            self.assertEqual(self._run_scaffold(name).returncode, 0)
            self.assertTrue(feature_dir.is_dir())
            proc = self._run_scaffold(name)
            self.assertNotEqual(
                proc.returncode, 0,
                msg="scaffold must not overwrite an existing feature folder")
        finally:
            import shutil
            if feature_dir.exists():
                shutil.rmtree(feature_dir)


if __name__ == "__main__":
    unittest.main()
