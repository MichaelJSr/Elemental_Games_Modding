"""qol_skip_logo — skip the unskippable Adrenium logo at boot.

The boot state machine (``FUN_0005F620``) plays two BINK movies
before reaching the title screen:

    VA 0x05F6E0   PUSH &"AdreniumLogo.bik"
    VA 0x05F6E5   CALL play_movie_fn         <-- we replace this 5-byte CALL
    ...
    VA 0x05F73F   PUSH &"prophecy.bik"       (untouched — has plot content)

Primary implementation: a 5-byte ``CALL`` trampoline into the C shim
at ``shim.c`` (a naked ``XOR AL,AL; RET 8``).  The shim matches the
original ``__stdcall`` ABI so the state machine sees a valid return
and advances cleanly to the prophecy movie.

Legacy fallback: when ``AZURIK_NO_SHIMS=1`` is set, the dispatcher
substitutes ``SKIP_LOGO_LEGACY_SPEC`` — a 10-byte rewrite of the
``PUSH+CALL`` pair that achieves the same effect without needing a
C toolchain.  See ``docs/LEARNINGS.md`` for why the original naive
NOP patch caused a black-screen hang.
"""

from __future__ import annotations

from pathlib import Path

from azurik_mod.patching import (
    PatchSpec,
    ShimSource,
    TrampolinePatch,
    apply_patch_spec,
    apply_trampoline_patch,
)
from azurik_mod.patching.registry import Feature, register_feature

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent.parent


# --- Legacy byte-patch (AZURIK_NO_SHIMS=1 fallback) ------------------
# Rewrites the 10-byte ``PUSH imm32; CALL rel32`` as
# ``ADD ESP, 4; XOR AL, AL; NOP x5``.  Matches the __stdcall ABI the
# boot state machine expects.  See qol.py's original comment block
# (now preserved in docs/LEARNINGS.md) for the full rationale.
SKIP_LOGO_LEGACY_SPEC = PatchSpec(
    label="Skip AdreniumLogo startup movie (legacy byte patch)",
    va=0x05F6E0,
    original=bytes([
        0x68, 0x50, 0xE1, 0x19, 0x00,   # PUSH 0x0019E150 (&"AdreniumLogo.bik")
        0xE8, 0x96, 0x92, 0xFB, 0xFF,   # CALL play_movie_fn (rel32)
    ]),
    patch=bytes([
        0x83, 0xC4, 0x04,               # ADD ESP, 4   ; pop PUSH EBP leftover
        0x30, 0xC0,                     # XOR AL, AL   ; state = 3 (skip)
        0x90, 0x90, 0x90, 0x90, 0x90,   # NOP x5
    ]),
)

# Legacy alias — some downstream scripts / tests still import the old
# ``SKIP_LOGO_SPEC`` spelling.  Preserved as a simple alias.
SKIP_LOGO_SPEC = SKIP_LOGO_LEGACY_SPEC


# --- Primary C-shim trampoline ---------------------------------------
# The shim object lives at ``shims/build/qol_skip_logo.o`` — the
# ShimSource helper computes that path from the pack name.  Auto-
# compile from ``shim.c`` kicks in when the .o is missing.
_SHIM = ShimSource(folder=_HERE, stem="shim")

SKIP_LOGO_TRAMPOLINE = TrampolinePatch(
    name="skip_logo",
    label="Skip AdreniumLogo startup movie (C shim)",
    va=0x05F6E5,
    replaced_bytes=bytes([
        0xE8, 0x96, 0x92, 0xFB, 0xFF,   # CALL play_movie_fn (rel32)
    ]),
    shim_object=_SHIM.object_path("qol_skip_logo", _REPO_ROOT),
    shim_symbol="_c_skip_logo",
    mode="call",
)


def apply_skip_logo_patch(xbe_data: bytearray) -> None:
    """Back-compat apply function.

    The unified :func:`apply_pack` dispatcher handles this feature
    end-to-end; this wrapper exists so pre-reorganisation callers
    (tests, the CLI's legacy direct-call path) keep working.
    Delegates to :func:`apply_trampoline_patch` with the pack's
    declared repo_root, and honours ``AZURIK_NO_SHIMS=1`` by falling
    back to :data:`SKIP_LOGO_LEGACY_SPEC`.
    """
    import os

    if os.environ.get("AZURIK_NO_SHIMS", "").strip().lower() in (
            "1", "true", "yes", "on"):
        apply_patch_spec(xbe_data, SKIP_LOGO_LEGACY_SPEC)
        return

    # Pre-reorganisation env var — still honoured for one release so
    # users with the old spelling don't break.
    if os.environ.get("AZURIK_SKIP_LOGO_LEGACY", "").strip().lower() in (
            "1", "true", "yes", "on"):
        apply_patch_spec(xbe_data, SKIP_LOGO_LEGACY_SPEC)
        return

    apply_trampoline_patch(
        xbe_data, SKIP_LOGO_TRAMPOLINE, repo_root=_REPO_ROOT)


FEATURE = register_feature(Feature(
    name="qol_skip_logo",
    description=(
        "Skips the unskippable Adrenium logo at boot (prophecy intro "
        "still plays).  Implemented via a C shim — see docs/SHIMS.md."
    ),
    sites=[SKIP_LOGO_TRAMPOLINE],
    apply=apply_skip_logo_patch,
    default_on=False,
    included_in_randomizer_qol=False,
    category="boot",
    tags=("c-shim",),
    shim=_SHIM,
    legacy_sites=(SKIP_LOGO_LEGACY_SPEC,),
))


__all__ = [
    "FEATURE",
    "SKIP_LOGO_LEGACY_SPEC",
    "SKIP_LOGO_SPEC",
    "SKIP_LOGO_TRAMPOLINE",
    "apply_skip_logo_patch",
]
