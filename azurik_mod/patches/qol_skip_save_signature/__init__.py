"""qol_skip_save_signature — bypass the HMAC-SHA1 verify on save load.

## Why

Azurik signs every save slot with an HMAC-SHA1 over a sorted file
tree (see :file:`docs/SAVE_FORMAT.md`).  The key
(``XboxSignatureKey``) is a runtime kernel global that is **not
statically recoverable** — it lives in heap memory that changes
per boot, per console, and per firmware.  That means:

- The ``save edit`` CLI produces correctly-rewritten ``.sav``
  files, but without the right signature the game rejects them on
  load.
- Today the only way to re-sign externally is to recover the key
  dynamically from a xemu RAM dump (``save key-recover``) — a
  per-user, per-session chore.

This feature patches the engine's **verify-side** so any save
file loads regardless of signature.  No key recovery needed.

## How it works

From the Ghidra decomp of ``verify_save_signature`` at VA
``0x0005C990``:

.. code-block:: asm

    MOV   AL, [ECX+0x20A]    ; flag byte
    SUB   ESP, 0x28          ; stack setup
    CMP   AL, 0x7A           ; 'z' magic — already a built-in bypass
    PUSH  ESI; PUSH EDI
    LEA   EDI, [ECX+0x20A]   ; path
    JZ    <fallthrough>      ; 'z' → return AL=1 (success)
    ...                      ; else HMAC compute + compare (REPE CMPSD)

The vanilla code already has a ``'z'`` magic-byte bypass — if the
first char of the path buffer is ``'z'`` (0x7A), the verifier
skips everything and returns AL=1.  Rather than replicate that
dance in a shim, we just force the bypass unconditionally by
rewriting the prologue to a two-instruction ``always-return-1``:

.. code-block:: asm

    MOV   AL, 1              ; B0 01
    RET                      ; C3

Three bytes patched, zero stack imbalance (we never ran SUB ESP),
zero calling-convention risk (ECX / EDX / EDI / ESI are all
caller-saved on x86 __thiscall).

The dead bytes from ``+3`` onward are never reached so they
stay identical to vanilla — ``verify-patches --strict`` reports
exactly 3 byte differences.

## Does it affect the write path?

No.  ``calculate_save_signature`` (the sibling **write** function
at VA ``0x0005C920``) is untouched.  The game still computes and
writes a valid signature when it saves; the patch only trivialises
the read-back check.  That means:

- Saves created on a patched XBE load fine on a patched XBE.
- Saves created on a patched XBE also load fine on a VANILLA
  XBE (the write path computed a real signature all along).
- Edited saves that never had a valid signature load on the
  patched XBE (this is the whole point) but WOULD still be
  rejected on vanilla.

## Use cases

- **Arbitrary save editing via** ``azurik-mod save edit`` — the
  blocker documented in ``docs/SAVE_FORMAT.md`` § 7 becomes a
  non-issue.
- **Cross-console save sharing** — the vanilla save format bakes
  in the console-specific ``XboxSignatureKey``; different xemu
  installs / real Xbox consoles see different keys.  With this
  patch the key is irrelevant.
- **Save-slot cloning / fuzzing** during mod testing.

## Safety

Minimal footprint.  Three bytes in ``.text``.  The only
observable side-effect is that signature mismatches are no longer
fatal — which is what we want.

``fps_unlock`` + ``player_physics`` + this patch can all coexist
freely; no overlap with any other QoL or performance patch.
"""

from __future__ import annotations

from azurik_mod.patching.registry import Feature, register_feature
from azurik_mod.patching.spec import PatchSpec


# VA of ``verify_save_signature`` function prologue.
#
# Confirmed via Ghidra MCP xrefs:
#   0x0019E278 — vtable slot (7th of 8 save-dispatch functions)
#   0x0019E290 — ``"signature.sav"`` string referenced by the
#                sibling tree-walker at 0x0005C4B0
#   0x0005C990 — function prologue (this patch target)
#
# Layout of the first 6 bytes + what we overwrite:
#   Vanilla:   8A 81 0A 02 00 00    MOV AL, [ECX+0x20A]
#   Patched:   B0 01 C3 02 00 00    MOV AL, 1 ; RET ; (dead)
AZURIK_VERIFY_SAVE_SIG_VA = 0x0005C990


ALWAYS_ACCEPT_SIG_SPEC = PatchSpec(
    label="verify_save_signature → always return AL=1",
    va=AZURIK_VERIFY_SAVE_SIG_VA,
    original=bytes.fromhex("8a810a"),
    patch=bytes.fromhex("b001c3"),
    is_data=False,
    safety_critical=False,
)


SKIP_SAVE_SIG_SITES: list[PatchSpec] = [ALWAYS_ACCEPT_SIG_SPEC]


def apply_skip_save_signature_patch(xbe_data: bytearray) -> None:
    """Rewrite the first 3 bytes of ``verify_save_signature`` to
    unconditionally return AL=1 (signature accepted).

    Idempotent — re-applying to an already-patched XBE is a no-op
    thanks to the ``original``-bytes guard inside
    :func:`apply_patch_spec`.
    """
    from azurik_mod.patching.apply import apply_patch_spec
    for spec in SKIP_SAVE_SIG_SITES:
        apply_patch_spec(xbe_data, spec)


FEATURE = register_feature(Feature(
    name="qol_skip_save_signature",
    description=(
        "Bypasses the HMAC-SHA1 save-file signature check on "
        "load.  Lets edited / cross-console saves load without "
        "re-signing; write-side signing is unchanged."
    ),
    sites=SKIP_SAVE_SIG_SITES,
    apply=apply_skip_save_signature_patch,
    default_on=False,
    included_in_randomizer_qol=False,
    category="qol",
    tags=("save-edit", "signature-bypass"),
))


__all__ = [
    "ALWAYS_ACCEPT_SIG_SPEC",
    "AZURIK_VERIFY_SAVE_SIG_VA",
    "SKIP_SAVE_SIG_SITES",
    "apply_skip_save_signature_patch",
]
