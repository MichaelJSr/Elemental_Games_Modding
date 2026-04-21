"""Apply / verify helpers for PatchSpec, ParametricPatch, TrampolinePatch."""

from __future__ import annotations

import os
import struct
from pathlib import Path

# ``subprocess`` is only used by ``_auto_compile_shim`` below, which
# fires when a trampoline patch names a shim that hasn't been
# built.  For the byte-patch-only code path (every ``qol_*``, every
# ``fps_unlock`` site) we never need subprocess, so defer its import
# to the callsite.  Skips ~125 ms of stdlib init on cold startup.

from azurik_mod.patching.coff import (
    extract_shim_bytes,
    find_landed_symbol,
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


def _guess_shim_source(
    shim_object: Path,
    repo_root: Path | None,
) -> Path | None:
    """Infer the C source file for a compiled shim object.

    Lookup order (first hit wins):

    1. **Feature-folder convention** (primary):
       ``<repo>/shims/build/<name>.o`` → ``<repo>/azurik_mod/patches/<name>/shim.c``.
       Each feature's Python declaration and its shim C live in the
       same folder; the .o's stem is the pack's name.

    2. **Test-fixtures layout**:
       ``<repo>/shims/build/<name>.o`` → ``<repo>/shims/fixtures/<name>.c``.
       Used by the test-only shim sources (``_reloc_test.c``,
       ``_vanilla_call_test.c``, ``_shared_lib_test.c``, …) that
       exercise the layout pipeline without shipping as features.

    3. **Sibling-directory fallback**: ``<dir>/<stem>.o`` →
       ``<dir>/<stem>.c``.  Handy for ad-hoc setups / one-off tests.

    Returns the first candidate that exists on disk, or — if none do —
    falls back to the feature-folder guess so the caller's auto-compile
    error message mentions the canonical path.
    """
    stem = shim_object.stem
    if not stem:
        return None

    candidates: list[Path] = []
    if shim_object.parent.name == "build" and repo_root is not None:
        candidates.append(
            repo_root / "azurik_mod" / "patches" / stem / "shim.c")
        candidates.append(
            repo_root / "shims" / "fixtures" / f"{stem}.c")
    candidates.append(shim_object.with_suffix(".c"))

    for c in candidates:
        if c.exists():
            return c
    # Nothing exists — return the new-layout guess so the error
    # message points at where the source SHOULD live.
    return candidates[0]


def _auto_compile(
    src: Path,
    expected_out: Path,
    repo_root: Path | None,
    label: str,
) -> bool:
    """Run ``shims/toolchain/compile.sh`` to produce ``expected_out``.

    Returns True on success, False otherwise — callers fall through
    to the usual "missing .o" error message if compilation fails so
    the shim author sees both the toolchain output and a pointer to
    the manual invocation.
    """
    if repo_root is None:
        return False
    script = repo_root / "shims/toolchain/compile.sh"
    if not script.exists():
        return False
    expected_out.parent.mkdir(parents=True, exist_ok=True)
    print(f"  {label} — auto-compiling {src.name} "
          f"(AZURIK_SHIM_NO_AUTOCOMPILE=1 to disable)")
    import subprocess  # deferred — see module header note
    try:
        subprocess.check_call(
            ["bash", str(script), str(src), str(expected_out)],
            cwd=repo_root,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"  {label} — auto-compile failed ({exc}); falling "
              f"back to manual-build hint")
        return False
    return True


def apply_trampoline_patch(
    xbe_data: bytearray,
    patch: TrampolinePatch,
    repo_root: Path | None = None,
    *,
    params: dict[str, float] | None = None,
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
    5. If ``patch.float_params`` is non-empty, overwrite each named
       ``.rdata`` constant with ``params[param.name]`` (or
       ``param.default`` when the caller didn't supply a value).
       This is the "per-apply float injection" channel — sliders can
       thread user values into a compiled C shim without a recompile.

    ``repo_root`` resolves relative ``shim_object`` paths; defaults to
    the caller's current working directory when ``None``.

    ``params`` is an optional ``{slider_name: float_value}`` dict
    consumed by :attr:`TrampolinePatch.float_params`.  Shims with no
    float params ignore it.
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

    # Auto-compile on demand.  If the .o doesn't exist but its .c
    # sibling does, invoke compile.sh to build it.  Matches the
    # conventional feature-folder layout: `shims/build/<name>.o` <->
    # `azurik_mod/patches/<name>/shim.c` (or, for test fixtures,
    # `shims/fixtures/<name>.c`).  Opt out with
    # AZURIK_SHIM_NO_AUTOCOMPILE=1.
    #
    # STALE-.o DETECTION: even if the .o exists, rebuild it when the
    # matching .c source has a newer mtime.  This is the edit-shim-
    # and-rebuild developer loop: previously editing `shim.c` and
    # re-running a patch would silently reuse the stale .o, leading
    # to confused "why didn't my change take effect?" debugging.
    # Opt out via AZURIK_SHIM_NO_AUTOCOMPILE; explicit override via
    # AZURIK_SHIM_FORCE_REBUILD=1 also triggers a rebuild.
    if not os.environ.get("AZURIK_SHIM_NO_AUTOCOMPILE"):
        src = _guess_shim_source(shim_path, repo_root)
        should_build = not shim_path.exists()
        if not should_build and src is not None and src.exists():
            try:
                if src.stat().st_mtime > shim_path.stat().st_mtime:
                    print(f"  {patch.label} — {src.name} is newer than "
                          f"{shim_path.name}, rebuilding")
                    should_build = True
            except OSError:
                pass
        if os.environ.get("AZURIK_SHIM_FORCE_REBUILD"):
            should_build = True
        if should_build and src is not None and src.exists():
            if _auto_compile(src, shim_path, repo_root, patch.label):
                pass  # fall through to the existence check below
    if not shim_path.exists():
        c_hint = _guess_shim_source(shim_path, repo_root)
        hint = (f" (matching source at {c_hint})"
                if c_hint and c_hint.exists() else "")
        print(f"  ERROR: {patch.label} — shim object not found at "
              f"{shim_path}{hint}.  Run "
              f"shims/toolchain/compile.sh first, or unset "
              f"AZURIK_SHIM_NO_AUTOCOMPILE.")
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

        # Vanilla-function externs the shim may call.  Imported lazily
        # so the main `apply` module stays importable in environments
        # that haven't loaded the vanilla-symbol registry yet.
        from azurik_mod.patching.vanilla_symbols import all_symbols
        # Session-wide resolver handles kernel imports (D1) + shared
        # library exports (E).  Sessions are attached to the bytearray
        # so pack apply functions can pre-place shared libraries and
        # see them here without an explicit session argument.
        from azurik_mod.patching.shim_session import get_or_create_session
        session = get_or_create_session(xbe_data)
        extern_resolver = session.make_extern_resolver(_allocate)
        try:
            landed = layout_coff(
                coff, patch.shim_symbol, _allocate,
                vanilla_symbols=all_symbols(),
                extern_resolver=extern_resolver)
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

    # --- 4. per-apply float-parameter injection --------------------------
    # If the trampoline declared `float_params`, overwrite each named
    # .rdata constant with the caller-supplied slider value (or its
    # default when absent).  The shim has already been landed + any
    # relocations applied; we're writing into already-committed bytes
    # inside the XBE using the section file offsets layout_coff returned.
    #
    # Shims that reference `AZURIK_FLOAT_PARAM` constants necessarily
    # carry a DIR32 relocation from .text -> .rdata, which forces the
    # has_relocs branch above.  We check for that and report a warning
    # if a shim author declared float_params on an unexpectedly
    # zero-reloc shim (their constants weren't actually referenced;
    # patching would be a no-op but probably signals a bug).
    if patch.float_params:
        if not has_relocs:
            print(f"  WARNING: {patch.label} — float_params declared "
                  f"but shim has no relocations; the .rdata slots "
                  f"aren't referenced from .text.  Check that the "
                  f"shim reads each AZURIK_FLOAT_PARAM name.")
        else:
            for param in patch.float_params:
                resolved = find_landed_symbol(
                    coff, landed, param.symbol)
                if resolved is None:
                    print(f"  WARNING: {patch.label} — float_param "
                          f"{param.name!r} (symbol {param.symbol!r}) "
                          f"not found in landed shim sections; "
                          f"skipping.")
                    continue
                section, sym_offset = resolved
                value = float(
                    (params or {}).get(param.name, param.default))
                field_offset = section.file_offset + sym_offset
                if field_offset + 4 > len(xbe_data):
                    print(f"  WARNING: {patch.label} — float_param "
                          f"{param.name!r} file offset "
                          f"0x{field_offset:X} past end of XBE; "
                          f"skipping.")
                    continue
                xbe_data[field_offset:field_offset + 4] = (
                    struct.pack("<f", value))
                display_label = param.label or param.name
                print(f"    {display_label} = {value:g}  "
                      f"(float_param {param.symbol} @ "
                      f"file 0x{field_offset:X})")

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


# ---------------------------------------------------------------------------
# Unified pack dispatcher
# ---------------------------------------------------------------------------
#
# ``apply_pack(pack, xbe_data, params)`` lets callers run any feature
# without knowing which primitive it uses underneath.  Walks each site in
# declaration order, dispatches by type, respects the ``AZURIK_NO_SHIMS``
# legacy-fallback env var, and hands off to ``custom_apply`` when the pack
# needs multi-step logic that can't be expressed as independent sites.

_NO_SHIMS_ENV = "AZURIK_NO_SHIMS"


def _no_shims_requested() -> bool:
    """Return True when the user asked for the legacy byte-patch form.

    Any truthy spelling (``1`` / ``true`` / ``yes``, case-insensitive)
    of ``AZURIK_NO_SHIMS`` flips the dispatcher into fallback mode.
    """
    return os.environ.get(_NO_SHIMS_ENV, "").strip().lower() in (
        "1", "true", "yes", "on")


def apply_pack(
    pack,  # registry.PatchPack — imported lazily to avoid circular imports
    xbe_data: bytearray,
    params: dict[str, float] | None = None,
    *,
    repo_root: Path | None = None,
    xbr_files: dict[str, bytearray] | None = None,
) -> None:
    """Apply every site in ``pack`` to ``xbe_data``.

    If the pack has any ``xbr_sites`` and the caller provides an
    ``xbr_files`` dict (``{filename: bytearray}``), each XBR edit
    is dispatched against the matching buffer via
    :mod:`azurik_mod.patching.xbr_spec`.  Packs without xbr_sites
    ignore ``xbr_files`` completely; packs with xbr_sites but no
    buffers surface a clear error (can't silently no-op a data
    edit, the user asked for it).

    Dispatch rules, in order:

    1. If ``pack.custom_apply`` is set, delegate the whole pack to it.
       ``params`` is forwarded as keyword arguments — slider names
       become kwargs.  Use this for packs (like ``player_physics``)
       whose apply logic crosses multiple sites.
    2. If ``AZURIK_NO_SHIMS`` is truthy AND the pack has
       ``legacy_sites``, every :class:`TrampolinePatch` in the pack
       is replaced by the legacy-site list.  Byte-only packs are
       unaffected.
    3. Walk the (possibly-substituted) site list in declaration order.
       Per type:
         - :class:`PatchSpec` → :func:`apply_patch_spec`
         - :class:`ParametricPatch` (non-virtual) →
           :func:`apply_parametric_patch` with the value from
           ``params[site.name]`` (or ``site.default`` if absent).
           Virtual parametric sites (``va == 0``) are silently
           skipped — the pack's ``custom_apply`` owns them.
         - :class:`TrampolinePatch` → :func:`apply_trampoline_patch`
           with ``repo_root`` (defaults to the pack's shim folder's
           repo root when the pack has a ``ShimSource``) and the
           full ``params`` dict (consumed by any ``float_params``
           declared on the trampoline — shims without float params
           ignore it).

    Returns ``None``.  Individual site failures print warnings via
    the underlying primitives but don't raise — keeps batch apply
    going when one site's vanilla bytes have drifted.

    ``params`` is a dict of ``{parameter_name: float_value}`` where
    keys are the ``name`` fields of the pack's parametric sites.
    Missing keys fall back to the site's ``default``.
    """
    # Lazy import — avoids the registry <-> apply circular dependency.
    from azurik_mod.patching.registry import PatchPack

    if not isinstance(pack, PatchPack):
        raise TypeError(
            f"apply_pack expected a PatchPack, got {type(pack).__name__}")

    params = params or {}

    # 1. Custom apply wins outright.  Pack authors who need multi-step
    #    logic (e.g. the player-physics gravity + injected-floats combo)
    #    opt in via the `custom_apply` field.
    if pack.custom_apply is not None:
        pack.custom_apply(xbe_data, **params)
        _dispatch_xbr_sites(pack, xbr_files, params)
        return

    # 2. Derive the effective site list.  AZURIK_NO_SHIMS swaps every
    #    TrampolinePatch for the pack's declared legacy fallbacks.
    sites = list(pack.sites)
    if _no_shims_requested() and pack.legacy_sites:
        sites = [s for s in sites if not isinstance(s, TrampolinePatch)]
        sites.extend(pack.legacy_sites)

    # 3. Resolve repo_root.  If the caller didn't supply one but the
    #    pack ships a shim, take it from the shim folder (folder is
    #    `<repo>/azurik_mod/patches/<name>/`).
    effective_repo_root = repo_root
    if effective_repo_root is None and pack.shim is not None:
        # shim.folder sits at <repo>/azurik_mod/patches/<name>/.
        effective_repo_root = pack.shim.folder.parent.parent.parent

    # 4. Walk sites in declaration order.
    for site in sites:
        if isinstance(site, PatchSpec):
            apply_patch_spec(xbe_data, site)
        elif isinstance(site, ParametricPatch):
            if site.is_virtual:
                continue  # owned by custom_apply if the pack has one
            value = params.get(site.name, site.default)
            apply_parametric_patch(xbe_data, site, float(value))
        elif isinstance(site, TrampolinePatch):
            # If the pack declared a ShimSource and the site's
            # shim_object is a stub, substitute the ShimSource-derived
            # path so feature modules don't have to hardcode a build path.
            effective_site = site
            if (pack.shim is not None and effective_repo_root is not None):
                derived = pack.shim.object_path(pack.name, effective_repo_root)
                if (site.shim_object.name == f"{pack.name}.o"
                        or site.shim_object == derived):
                    # Already canonical — leave alone.
                    pass
                else:
                    effective_site = site._replace(shim_object=derived)
            apply_trampoline_patch(
                xbe_data, effective_site,
                repo_root=effective_repo_root,
                params=params)
        else:
            raise TypeError(
                f"pack {pack.name!r} has site of unsupported type "
                f"{type(site).__name__}")

    # 5. XBR-side edits.  Skipped for packs without xbr_sites
    #    (byte / shim-only packs pay zero cost).
    _dispatch_xbr_sites(pack, xbr_files, params)


def _dispatch_xbr_sites(
    pack,
    xbr_files: dict[str, bytearray] | None,
    params: dict[str, float],
) -> None:
    """Walk ``pack.xbr_sites`` and apply each one.

    Packs with no xbr_sites are silent no-ops.  Packs with
    xbr_sites but no ``xbr_files`` buffers raise cleanly — the
    apply was asked to mutate XBR data without being handed any,
    so a silent no-op would corrupt the user's expectation.
    """
    sites = getattr(pack, "xbr_sites", None)
    if not sites:
        return
    if xbr_files is None:
        raise ValueError(
            f"pack {pack.name!r} has {len(sites)} xbr_sites but "
            f"apply_pack was called without xbr_files={{}} — the "
            f"ISO build pipeline must load and hand in the XBR "
            f"buffers this pack edits.  Touched files: "
            f"{list(getattr(pack, 'touched_xbr_files', lambda: ())())}")
    # Lazy imports keep the XBR plumbing off the hot path for
    # every XBE-only pack.
    from azurik_mod.patching.xbr_spec import (
        XbrEditSpec,
        XbrParametricEdit,
        apply_xbr_edit_spec,
        apply_xbr_parametric_edit,
    )
    for site in sites:
        if isinstance(site, XbrEditSpec):
            print(f"  {site.label}")
            apply_xbr_edit_spec(xbr_files, site)
        elif isinstance(site, XbrParametricEdit):
            value = float(params.get(site.name, site.default))
            print(f"  {site.label} = {value} {site.unit}")
            apply_xbr_parametric_edit(xbr_files, site, value)
        else:
            raise TypeError(
                f"pack {pack.name!r} has xbr_site of unsupported "
                f"type {type(site).__name__}")


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
