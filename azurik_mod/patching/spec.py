"""PatchSpec, ParametricPatch, TrampolinePatch — site descriptors.

A patch pack holds a mixed list of these three shapes:

- `PatchSpec`:        fixed byte swap at a VA.  Used by FPS / QoL packs.
- `ParametricPatch`:  float-valued rewrite at a VA whose bytes are
                      derived from a user parameter at apply time.
                      Used for sliders (gravity, player speed, ...).
                      A VA of 0 + size of 0 marks a "virtual" slider
                      whose value is consumed by a custom apply_*
                      function (e.g. walk_speed_scale writes into
                      characters.xbr instead of default.xbe).
- `TrampolinePatch`:  code-injection site.  Compiled C from the
                      ``shims/`` tree is dropped into XBE padding (or
                      a new appended section); a 5-byte CALL / JMP
                      rel32 at the declared VA diverts control flow
                      into the shim.  Enables modding in C rather
                      than hand-assembled bytes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, NamedTuple

from azurik_mod.patching.xbe import va_to_file


class PatchSpec(NamedTuple):
    """Descriptor for one fixed XBE binary patch.

    Attributes:
        label:           Human-readable description (printed on apply).
        va:              Virtual address of the first patched byte.
        original:        Expected bytes at `va` before patching.
        patch:           Bytes to write at `va`.
        is_data:         True if the region lives in .rdata/.data.
        safety_critical: True if a regression here could cause memory
                         corruption or a BSOD.  Pinned by safety tests.
    """

    label: str
    va: int
    original: bytes
    patch: bytes
    is_data: bool = False
    safety_critical: bool = False

    @property
    def file_offset(self) -> int:
        return va_to_file(self.va)


class ParametricPatch(NamedTuple):
    """Descriptor for a slider-driven patch.

    Unlike `PatchSpec`, the bytes written at `va` are computed at apply
    time from a caller-supplied float parameter via `encode(value)`.

    A "virtual" ParametricPatch (va=0, size=0, original=b"") is
    accepted: the pack's own `apply` function is expected to consume
    the parameter via another path (e.g. writing to characters.xbr).
    The GUI still renders a slider for it.

    Attributes:
        name:         Stable slider identifier (e.g. "gravity").
        label:        Human-readable label for the slider / logs.
        va:           Virtual address to rewrite (0 for virtual sliders).
        size:         Byte count to rewrite (0 for virtual sliders).
        original:     Baseline bytes at `va` (b"" for virtual sliders).
        default:      Baseline float value shown at slider centre.
        slider_min:   Inclusive minimum the UI / CLI accepts.
        slider_max:   Inclusive maximum the UI / CLI accepts.
        slider_step:  Slider tick size for the GUI.
        unit:         Display unit (e.g. "m/s^2", "x").
        encode:       Callable that turns a float value into `size` bytes.
        decode:       Callable that turns bytes back into a float (for
                      verify reporting).
        safety_critical: Pins this site in safety tests.
    """

    name: str
    label: str
    va: int
    size: int
    original: bytes
    default: float
    slider_min: float
    slider_max: float
    slider_step: float
    unit: str
    encode: Callable[[float], bytes]
    decode: Callable[[bytes], float]
    safety_critical: bool = False

    @property
    def is_virtual(self) -> bool:
        return self.va == 0 and self.size == 0

    @property
    def file_offset(self) -> int:
        if self.is_virtual:
            raise ValueError(
                f"ParametricPatch {self.name!r} is virtual — no file offset")
        return va_to_file(self.va)


class TrampolinePatch(NamedTuple):
    """Descriptor for a code-injection site backed by a compiled C shim.

    At apply time the patcher:

    1. Reads ``shim_object`` (an i386 PE-COFF ``.o`` produced by
       ``shims/toolchain/compile.sh``) and extracts the machine-code
       bytes for ``shim_symbol`` from the file's ``.text`` section.
    2. Finds a home for those bytes inside the XBE — preferably
       trailing padding in ``.text``; falls back to appending a new
       executable section when padding is tight.
    3. Emits a 5-byte ``CALL rel32`` (mode ``"call"``) or
       ``JMP rel32`` (mode ``"jmp"``) at ``va`` that jumps to the
       shim's entry point, NOP-padding any leftover bytes of
       ``replaced_bytes``.

    The shim and its trampoline are reversible: ``verify_trampoline_patch``
    checks both the trampoline instruction shape AND that the shim
    bytes at the resolved address still hash as expected.

    Attributes:
        name:            Stable short id (e.g. ``"skip_logo"``).  Used
                         in log lines and test fixtures.
        label:           Human-readable description printed on apply.
        va:              Virtual address of the trampoline's first
                         byte — the site that diverts into the shim.
        replaced_bytes:  Original bytes under the trampoline in vanilla
                         XBE (for the pre-patch verify check).  Length
                         must be >= 5 (minimum CALL/JMP rel32 size).
                         Bytes beyond the 5-byte trampoline become NOPs.
        shim_object:     Path (repo-relative) to the compiled PE-COFF
                         ``.o`` containing the shim symbol.
        shim_symbol:     Symbol name as it appears in the ``.o``.  Note
                         the Windows / MSVC convention: ``void c_foo(void)``
                         exports as ``"_c_foo"`` (leading underscore).
        mode:            ``"call"`` emits ``E8 rel32`` (return to the
                         instruction after the trampoline).
                         ``"jmp"`` emits ``E9 rel32`` (no return — the
                         shim has to handle its own control flow).
        safety_critical: Mirrors the flag on ``PatchSpec``; pins the
                         site in safety tests.
    """

    name: str
    label: str
    va: int
    replaced_bytes: bytes
    shim_object: Path
    shim_symbol: str
    mode: str = "call"
    safety_critical: bool = False

    @property
    def file_offset(self) -> int:
        """File offset of the trampoline (not of the shim's landing pad
        — that one is computed at apply time and recorded on the XBE)."""
        return va_to_file(self.va)

    @property
    def trampoline_size(self) -> int:
        """The trampoline itself is always 5 bytes (opcode + rel32).
        Anything beyond that in ``replaced_bytes`` is NOP-filled."""
        return 5
