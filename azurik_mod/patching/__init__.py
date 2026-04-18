"""Patch engine: site descriptors + apply/verify + XBE offset map."""

from azurik_mod.patching.spec import (
    ParametricPatch,
    PatchSpec,
    TrampolinePatch,
)
from azurik_mod.patching.apply import (
    apply_parametric_patch,
    apply_patch_spec,
    apply_trampoline_patch,
    apply_xbe_patch,
    read_parametric_value,
    verify_parametric_patch,
    verify_patch_spec,
    verify_trampoline_patch,
)
from azurik_mod.patching.xbe import (
    XBE_SECTIONS,
    append_xbe_section,
    file_to_va,
    find_text_padding,
    grow_text_section,
    parse_xbe_sections,
    va_to_file,
)

__all__ = [
    "ParametricPatch",
    "PatchSpec",
    "TrampolinePatch",
    "XBE_SECTIONS",
    "append_xbe_section",
    "apply_parametric_patch",
    "apply_patch_spec",
    "apply_trampoline_patch",
    "apply_xbe_patch",
    "file_to_va",
    "find_text_padding",
    "grow_text_section",
    "parse_xbe_sections",
    "read_parametric_value",
    "va_to_file",
    "verify_parametric_patch",
    "verify_patch_spec",
    "verify_trampoline_patch",
]
