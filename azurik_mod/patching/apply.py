"""Apply / verify helpers for PatchSpec, ParametricPatch, TrampolinePatch."""

from __future__ import annotations

import struct
from pathlib import Path

from azurik_mod.patching.coff import (
    extract_shim_bytes,
    layout_coff,
    parse_coff,
)
from azurik_mod.patching.spec import (
    ParametricPatch,
    PatchSpec,
    TrampolinePatch,
)
from azurik_mod.patching.xbe import (
    append_xbe_section,
    file_to_va,
    find_text_padding,
    grow_text_section,
    parse_xbe_sections,
    va_to_file,
)


def apply_xbe_patch(
    xbe_data: bytearray,
    label: str,
    offset: int,
    original: bytes,
    patch: bytes,
) -> bool:
    """Apply a single raw byte patch with verification.

    The idempotent check ("already applied") lets the CLI re-run without
    erroring on a XBE that was already patched in a previous invocation.
    """
    if len(patch) != len(original):
        print(f"  ERROR: {label} — original ({len(original)}B) and "
              f"patch ({len(patch)}B) lengths differ")
        return False
    size = len(original)
    if offset + size > len(xbe_data):
        print(f"  WARNING: {label} — offset 0x{offset:X} out of range, skipping")
        return False
    current = bytes(xbe_data[offset:offset + size])
    if current == original:
        xbe_data[offset:offset + size] = patch
        print(f"  {label}")
        return True
    if current == patch:
        print(f"  {label} (already applied)")
        return True
    print(f"  WARNING: {label} — bytes at 0x{offset:X} don't match "
          f"(got {current.hex()}, expected {original.hex()})")
    return False


def apply_patch_spec(xbe_data: bytearray, spec: PatchSpec) -> bool:
    """Apply a single PatchSpec to the XBE data."""
    return apply_xbe_patch(
        xbe_data, spec.label, spec.file_offset, spec.original, spec.patch
    )


def verify_patch_spec(xbe_data: bytes, spec: PatchSpec) -> str:
    """Check whether `spec` has been applied to `xbe_data`.

    Returns one of:
        "applied"      — bytes at offset equal spec.patch
        "original"     — bytes at offset equal spec.original (not patched)
        "mismatch"     — bytes match neither
        "out-of-range" — offset is past the end of xbe_data
    """
    size = len(spec.patch)
    offset = spec.file_offset
    if offset + size > len(xbe_data):
        return "out-of-range"
    current = bytes(xbe_data[offset:offset + size])
    if current == spec.patch:
        return "applied"
    if current == spec.original:
        return "original"
    return "mismatch"


# ---------------------------------------------------------------------------
# Parametric patches — slider-driven float rewrites
# ---------------------------------------------------------------------------


def apply_parametric_patch(
    xbe_data: bytearray,
    patch: ParametricPatch,
    value: float,
) -> bool:
    """Encode `value` and write it at the patch's VA.

    Virtual parametric patches (va == 0 and size == 0) are handled by
    the pack's own apply function and this helper becomes a no-op.
    """
    if patch.is_virtual:
        return True  # caller handles via a different code path

    if not (patch.slider_min <= value <= patch.slider_max):
        print(f"  ERROR: {patch.label} — value {value} outside "
              f"[{patch.slider_min}, {patch.slider_max}], skipping")
        return False

    payload = patch.encode(float(value))
    if len(payload) != patch.size:
        print(f"  ERROR: {patch.label} — encode produced "
              f"{len(payload)} B but size is {patch.size}")
        return False

    offset = patch.file_offset
    if offset + patch.size > len(xbe_data):
        print(f"  WARNING: {patch.label} — offset 0x{offset:X} out of range")
        return False

    xbe_data[offset:offset + patch.size] = payload
    print(f"  {patch.label} = {value} {patch.unit}")
    return True


def verify_parametric_patch(xbe_data: bytes, patch: ParametricPatch) -> str:
    """Return the current state of a parametric patch.

    Returns one of:
        "default"      — bytes at VA decode to the baseline default
        "custom"       — bytes decode to a different value within range
        "out-of-range" — VA is past the end of xbe_data
        "mismatch"     — bytes don't decode to a valid float in range
        "virtual"      — this is a virtual slider with no XBE footprint
    """
    if patch.is_virtual:
        return "virtual"
    offset = patch.file_offset
    if offset + patch.size > len(xbe_data):
        return "out-of-range"
    current = bytes(xbe_data[offset:offset + patch.size])
    if current == patch.original:
        return "default"
    try:
        value = patch.decode(current)
    except Exception:  # noqa: BLE001
        return "mismatch"
    if patch.slider_min <= value <= patch.slider_max:
        return "custom"
    return "mismatch"


def read_parametric_value(xbe_data: bytes, patch: ParametricPatch) -> float | None:
    """Decode the current value at the patch's VA, or None if unreadable."""
    if patch.is_virtual:
        return None
    offset = patch.file_offset
    if offset + patch.size > len(xbe_data):
        return None
    current = bytes(xbe_data[offset:offset + patch.size])
    try:
        return patch.decode(current)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Trampoline patches — C-shim code injection
# ---------------------------------------------------------------------------
#
# A trampoline site is a 5-byte instruction at ``va`` that hands
# control to a compiled shim.  Phase 1 uses CALL/JMP rel32 only —
# both are 5 bytes (0xE8 / 0xE9 + 4-byte signed displacement).
#
# Layout after apply:
#
#     xbe[va .. va+5]          = CALL/JMP rel32 to shim_landing
#     xbe[va+5 .. va+n]        = 0x90 NOP fill (up to len(replaced_bytes))
#     xbe[shim_landing ..]     = shim .text bytes from the .o
#     shim_entry               = shim_landing + symbol offset in .text
#
# ``shim_region_file_offset`` is recorded per-patch at apply time so
# verify can find its way back without re-scanning for padding.

_CALL_REL32 = 0xE8
_JMP_REL32 = 0xE9
_NOP = 0x90


def _shim_region_ledger(xbe_data: bytearray) -> dict[int, int]:
    """Return a mutable dict mapping ``va -> shim_file_offset``.

    We stash this on the bytearray itself as a private attribute so
    multiple trampoline applies on the same buffer can share the same
    region bookkeeping without a global.  Non-invasive: if someone
    passes a plain ``bytes`` no attribute survives the slice operation.
    """
    # bytearray allows attribute assignment in Python; use setdefault-esque pattern.
    ledger: dict[int, int] | None = getattr(xbe_data, "_trampoline_ledger", None)
    if ledger is None:
        ledger = {}
        try:
            xbe_data._trampoline_ledger = ledger  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            # Not a bytearray — caller needs to track externally.
            pass
    return ledger


_APPENDED_SHIM_SECTION_NAME = "SHIMS"
"""Name used when we have to spill shim code into a newly-appended
XBE section.  Kept short (<= 8 chars) so it fits comfortably in the
section-name pool and is easily identified in XBE dumps / Ghidra."""


def _carve_shim_landing(
    xbe_data: bytearray,
    shim_bytes: bytes,
) -> tuple[int, int]:
    """Write ``shim_bytes`` into the XBE and return ``(file_offset, vaddr)``.

    Landing order (tightest -> most invasive):

    1. Existing trailing zero-slack inside ``.text``'s raw body, if
       the vanilla XBE has any.
    2. Growing ``.text`` into the adjacent VA gap before the next
       section (Azurik has 16 bytes of such headroom before BINK).
    3. Appending a new executable section to the XBE via
       :func:`append_xbe_section`, when the shim is larger than what
       options (1) + (2) together can hold.  The new section carries
       the single per-apply ``_APPENDED_SHIM_SECTION_NAME`` name so
       subsequent applies against the same buffer can detect and
       extend the existing spill section rather than adding another.

    ``file_offset`` is where the bytes live on disk — used for the
    per-apply ledger bookkeeping.  ``vaddr`` is the virtual address
    the Xbox loader will map the first byte to — used to compute the
    CALL/JMP ``rel32`` displacement at the trampoline site.  Returning
    both lets the trampoline apply pipeline work without calling
    ``file_to_va`` (which is hard-coded to the vanilla section map
    and doesn't know about newly-appended sections).
    """
    # --- 1 + 2: .text landing path --------------------------------------
    try:
        padding_start, padding_len = find_text_padding(bytes(xbe_data))
    except ValueError:
        padding_len = 0
        padding_start = -1

    if padding_len >= len(shim_bytes):
        _, sections = parse_xbe_sections(bytes(xbe_data))
        text = next(s for s in sections if s["name"] == ".text")
        raw_end = text["raw_addr"] + text["raw_size"]
        write_end = padding_start + len(shim_bytes)
        growth_needed = max(0, write_end - raw_end)

        xbe_data[padding_start:padding_start + len(shim_bytes)] = shim_bytes
        if growth_needed > 0:
            grow_text_section(xbe_data, growth_needed)
        # Landed inside .text — VA comes from the vanilla section map.
        return padding_start, file_to_va(padding_start)

    # --- 3: spill into a newly-appended section -------------------------
    # Reuse an existing SHIMS section if a previous apply already
    # created one in this buffer.  Extend it in place by growing its
    # raw_size / virtual_size and appending bytes at its raw_end.
    _, sections = parse_xbe_sections(bytes(xbe_data))
    existing = next(
        (s for s in sections if s["name"] == _APPENDED_SHIM_SECTION_NAME),
        None,
    )
    if existing is not None:
        raw, va = _extend_appended_section(xbe_data, existing, shim_bytes)
        return raw, va

    # Otherwise append a brand-new section.  append_xbe_section
    # handles header growth + pointer fixups + data placement.
    info = append_xbe_section(
        xbe_data, _APPENDED_SHIM_SECTION_NAME, shim_bytes,
        flags=0x00000006,  # EXECUTABLE | PRELOAD
    )
    return info["raw_addr"], info["vaddr"]


def _extend_appended_section(
    xbe_data: bytearray,
    section: dict,
    shim_bytes: bytes,
) -> tuple[int, int]:
    """Grow an existing appended SHIMS section by ``len(shim_bytes)``.

    Only legal when the section sits at / past the current end-of-file
    (which our append strategy guarantees: every SHIMS section is
    appended at EOF with FILE_ALIGN padding).  Returns ``(file_offset,
    vaddr)`` of the newly-written region, consistent with
    :func:`_carve_shim_landing`.
    """
    raw_end = section["raw_addr"] + section["raw_size"]
    if raw_end != len(xbe_data):
        raise RuntimeError(
            f"cannot extend appended SHIMS section: its raw_end "
            f"0x{raw_end:X} is not the current EOF 0x{len(xbe_data):X} "
            f"(did another apply write past it?)")

    import struct as _struct
    base_addr = _struct.unpack_from("<I", xbe_data, 0x104)[0]
    num_sections = _struct.unpack_from("<I", xbe_data, 0x11C)[0]
    section_headers_addr = _struct.unpack_from("<I", xbe_data, 0x120)[0]
    section_headers_offset = section_headers_addr - base_addr
    hdr_off = None
    for i in range(num_sections):
        off = section_headers_offset + i * 56
        vaddr = _struct.unpack_from("<I", xbe_data, off + 4)[0]
        if vaddr == section["vaddr"]:
            hdr_off = off
            break
    if hdr_off is None:
        raise RuntimeError("could not locate SHIMS section header for extend")

    new_size = section["raw_size"] + len(shim_bytes)
    _struct.pack_into("<I", xbe_data, hdr_off + 8,  new_size)
    _struct.pack_into("<I", xbe_data, hdr_off + 16, new_size)
    xbe_data.extend(shim_bytes)

    # size_of_image may need a bump if the section now runs past the
    # previous highest VA.
    vaddr = section["vaddr"]
    new_vend_va = vaddr + new_size
    cur_size_of_image = _struct.unpack_from("<I", xbe_data, 0x10C)[0]
    required = ((new_vend_va + 0xFFF) & ~0xFFF) - base_addr
    if required > cur_size_of_image:
        _struct.pack_into("<I", xbe_data, 0x10C, required)

    shim_vaddr = vaddr + section["raw_size"]
    return raw_end, shim_vaddr


def apply_trampoline_patch(
    xbe_data: bytearray,
    patch: TrampolinePatch,
    repo_root: Path | None = None,
) -> bool:
    """Apply a TrampolinePatch to the XBE data.

    Steps (in order):

    1. Read and parse the shim's PE-COFF object file.
    2. Verify the pre-patch bytes at ``va`` match ``replaced_bytes``
       (idempotent: if a trampoline is already there pointing at a
       previously-installed shim, we accept it as "already applied").
    3. Copy the shim's ``.text`` bytes into ``.text`` padding and
       compute the shim's entry-point VA.
    4. Emit the trampoline: opcode + signed rel32 displacement, plus
       any trailing NOP padding up to len(replaced_bytes).

    ``repo_root`` resolves relative ``shim_object`` paths; defaults to
    the caller's current working directory when ``None``.
    """
    site_offset = patch.file_offset
    replaced_len = len(patch.replaced_bytes)
    if replaced_len < 5:
        print(f"  ERROR: {patch.label} — replaced_bytes must be >= 5 B "
              f"(got {replaced_len}) to fit a rel32 jump")
        return False
    if site_offset + replaced_len > len(xbe_data):
        print(f"  WARNING: {patch.label} — VA 0x{patch.va:X} out of "
              f"range, skipping")
        return False

    current = bytes(xbe_data[site_offset:site_offset + replaced_len])
    opcode = _CALL_REL32 if patch.mode == "call" else _JMP_REL32
    if patch.mode not in ("call", "jmp"):
        print(f"  ERROR: {patch.label} — unknown mode {patch.mode!r}, "
              f"expected 'call' or 'jmp'")
        return False

    if current == patch.replaced_bytes:
        pass  # vanilla, proceed
    elif current[0] == opcode and all(b == _NOP for b in current[5:replaced_len]):
        # Already carries an identically-shaped trampoline.  Refuse to
        # overwrite silently — the user has either already applied
        # this patch or hand-patched the site, and in either case we'd
        # rather leave it alone than double-apply and pick new padding.
        print(f"  {patch.label} (already applied)")
        return True
    else:
        print(f"  WARNING: {patch.label} — bytes at 0x{site_offset:X} "
              f"don't match vanilla or an existing trampoline "
              f"(got {current.hex()}, expected {patch.replaced_bytes.hex()})")
        return False

    # --- 1. load + parse the shim .o -------------------------------------
    shim_path = patch.shim_object
    if not shim_path.is_absolute() and repo_root is not None:
        shim_path = repo_root / shim_path
    if not shim_path.exists():
        print(f"  ERROR: {patch.label} — shim object not found at "
              f"{shim_path}.  Run shims/toolchain/compile.sh first.")
        return False

    coff = parse_coff(shim_path.read_bytes())

    # --- 2. land the shim bytes in the XBE -------------------------------
    # Two codepaths depending on whether the shim's .text carries any
    # relocation entries:
    #
    #   * Zero relocations — use the fast `extract_shim_bytes` path.
    #     The shim's .text is copied verbatim into the XBE; no
    #     post-write fixups required.  Every Phase 1 shim took this
    #     branch (skip_logo is pure arithmetic with no externals).
    #
    #   * Any relocations — use the Phase 2 `layout_coff` pipeline.
    #     Every landable section (.text, optional .rdata, .data) is
    #     placed via `_carve_shim_landing`, then their relocation
    #     tables are walked and DIR32 / REL32 fields are rewritten to
    #     reference the resolved XBE VAs.  The placed bytes are
    #     overwritten in-place with the final relocated content.
    text_section = None
    try:
        text_section = coff.section(".text")
    except KeyError:
        pass
    has_relocs = (text_section is not None and text_section.reloc_count > 0)

    if not has_relocs:
        try:
            text_bytes, sym_offset = extract_shim_bytes(
                coff, patch.shim_symbol)
        except (KeyError, ValueError) as exc:
            print(f"  ERROR: {patch.label} — {exc}")
            return False
        try:
            shim_file_offset, shim_base_va = _carve_shim_landing(
                xbe_data, text_bytes)
        except (RuntimeError, ValueError) as exc:
            print(f"  ERROR: {patch.label} — {exc}")
            return False
        shim_entry_va = shim_base_va + sym_offset
    else:
        # Relocation-aware layout.  `_carve_shim_landing` is used as
        # the per-section allocator; placeholder bytes (zeros) are
        # reserved first, then layout_coff applies relocations and
        # returns the finalised bytes that we write over the
        # placeholders.
        def _allocate(
            _name: str,
            placeholder: bytes,
        ) -> tuple[int, int]:
            return _carve_shim_landing(xbe_data, placeholder)

        try:
            landed = layout_coff(coff, patch.shim_symbol, _allocate)
        except (KeyError, ValueError, RuntimeError) as exc:
            print(f"  ERROR: {patch.label} — {exc}")
            return False

        # Overwrite each section's placeholder with its relocated
        # bytes.  `_carve_shim_landing` guarantees the file_offset
        # returned during allocation is still valid (we only ever
        # extended the XBE, never shifted existing content).
        for section in landed.sections:
            xbe_data[section.file_offset:
                     section.file_offset + len(section.data)] = section.data

        shim_entry_va = landed.entry_va
        # For the ledger + log we pick the .text file offset as the
        # "representative" landing site.
        try:
            text_landed = next(s for s in landed.sections
                               if s.name == ".text")
            shim_file_offset = text_landed.file_offset
        except StopIteration:
            shim_file_offset = landed.sections[0].file_offset

    # --- 3. emit the trampoline ------------------------------------------
    # rel32 is measured from the END of the 5-byte jump instruction.
    end_of_jump_va = patch.va + 5
    rel32 = shim_entry_va - end_of_jump_va
    # signed 32-bit bounds check
    if not -0x80000000 <= rel32 <= 0x7FFFFFFF:
        print(f"  ERROR: {patch.label} — shim too far for rel32 "
              f"(delta 0x{rel32:X})")
        return False

    trampoline = bytes([opcode]) + struct.pack("<i", rel32)
    tail = bytes([_NOP] * (replaced_len - 5))
    xbe_data[site_offset:site_offset + replaced_len] = trampoline + tail

    # Ledger so verify can re-locate the shim without guessing.
    ledger = _shim_region_ledger(xbe_data)
    ledger[patch.va] = shim_file_offset

    # Report the shim's .text size.  In the fast path we have it as
    # `text_bytes`; in the relocation path, read it off the landed
    # section record.
    if has_relocs:
        shim_text_len = next(
            (len(s.data) for s in landed.sections if s.name == ".text"),
            sum(len(s.data) for s in landed.sections))
    else:
        shim_text_len = len(text_bytes)
    print(f"  {patch.label} (shim @ 0x{shim_entry_va:X}, +{shim_text_len} B)")
    return True


def verify_trampoline_patch(xbe_data: bytes, patch: TrampolinePatch) -> str:
    """Check whether a trampoline patch has been applied.

    Returns one of:
        "applied"      — site has an opcode-matching rel32 trampoline
        "original"     — site still holds ``patch.replaced_bytes``
        "mismatch"     — site holds something unexpected
        "out-of-range" — file offset is past end-of-data

    Does NOT re-validate the shim's bytes at the jump target.  That
    check lives in the strict ``verify-patches`` path, which has the
    shim ``.o`` available and can cross-reference.
    """
    site_offset = patch.file_offset
    replaced_len = len(patch.replaced_bytes)
    if site_offset + replaced_len > len(xbe_data):
        return "out-of-range"
    current = bytes(xbe_data[site_offset:site_offset + replaced_len])
    if current == patch.replaced_bytes:
        return "original"

    opcode = _CALL_REL32 if patch.mode == "call" else _JMP_REL32
    if current[0] == opcode and all(b == _NOP for b in current[5:replaced_len]):
        return "applied"
    return "mismatch"
