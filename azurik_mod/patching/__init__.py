"""Patch engine: PatchSpec + apply/verify + XBE offset map."""

from azurik_mod.patching.spec import ParametricPatch, PatchSpec
from azurik_mod.patching.apply import (
    apply_parametric_patch,
    apply_patch_spec,
    apply_xbe_patch,
    read_parametric_value,
    verify_parametric_patch,
    verify_patch_spec,
)
from azurik_mod.patching.xbe import XBE_SECTIONS, va_to_file, parse_xbe_sections

__all__ = [
    "ParametricPatch",
    "PatchSpec",
    "XBE_SECTIONS",
    "apply_parametric_patch",
    "apply_patch_spec",
    "apply_xbe_patch",
    "parse_xbe_sections",
    "read_parametric_value",
    "va_to_file",
    "verify_parametric_patch",
    "verify_patch_spec",
]
