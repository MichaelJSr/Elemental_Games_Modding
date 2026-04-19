"""enable_dev_menu — force ``levels/selector`` to load for EVERY
level transition.

## What it does

``selector.xbr`` is a manifest-orphan developer level that ships in
the retail ISO: a small room with portal plaques to every live
level + one-click triggers for every cutscene.  This patch forces
the engine to load ``levels/selector`` whenever it would otherwise
load any level (new game, load save, cutscene-end transition,
etc.) — so the user always lands in the selector regardless of the
triggering code path.

## Why previous versions failed

**v1** (up to April 2026) NOPed two ``JZ`` instructions at
VAs ``0x52F7E`` + ``0x52F95`` inside ``dev_menu_flag_check``'s
precursor branch, forcing a local variable ``pcVar10`` to
``"levels/selector"``.  But that variable is only used by stage 2
of a 3-stage level-name validator cascade, which only fires when
stage 1 (validating the caller's ``param_2``) fails.  In normal
play, callers pass valid level names → stage 1 wins → ``pcVar10``
never reached → patch had no observable effect.

**v2** pivoted to short-circuiting the ``"enable cheat buttons"``
cvar getter at VA ``0x000FFFC0``, which unlocks the in-game cheat
UI (different feature).  Retained separately as a future pack.

**v3** patched stages 1 + 2 of ``dev_menu_flag_check``'s validator
chain with ``XOR EAX, EAX`` — made the stages fail reliably,
forcing the third-stage fallback that hard-codes
``"levels/selector"``.  This works **when** ``dev_menu_flag_check``
is reached — but ``FUN_00055AB0`` (the main game state machine)
bypasses ``dev_menu_flag_check`` on cut-scene-end transitions,
calling ``FUN_00053750`` (the universal level loader) DIRECTLY
with the target level name ("levels/water/w1", etc.).  Users
never saw the selector because the New-Game → intro-cutscene →
first-level flow never hits ``dev_menu_flag_check``.

## How v4 works

Patch the prologue of ``FUN_00053750`` (the UNIVERSAL level
loader that every level-transition path eventually reaches) with
a trampoline that overwrites its ``param_2`` argument — the
level-name pointer — with ``"levels/selector"`` before the
function body runs.  Every level transition in the game, no
matter the caller, now loads the selector.

### Trampoline layout

At ``VA 0x00053750`` (function entry), replace the first 7 bytes:

.. code-block:: text

   Vanilla:
     53750: 8B 44 24 04        MOV EAX, [ESP+4]      ; param_1
     53754: 8B 48 40            MOV ECX, [EAX+0x40]
     53757: 81 EC 24 08 00 00   SUB ESP, 0x824

   Patched (bytes 0x53750..0x53756):
     53750: E9 <rel32 shim>     JMP <shim landing>
     53755: 90 90               NOP NOP  (padding to preserve
                                          the start-of-SUB-ESP
                                          at VA 0x53757 for any
                                          CFG tools)

The shim (landed via ``_carve_shim_landing``) does:

.. code-block:: text

   # Guard: only override when param_4 (at [ESP+0x10]) == 0 —
   # i.e. normal level-load calls.  The bink-movie path sets
   # param_4 != 0 and passes "bink:movies/foo.bik"; we don't
   # want to misroute a movie into the level loader.
   83 7C 24 10 00        CMP DWORD [ESP+0x10], 0
   75 08                 JNZ skip_override
   # Override: force param_2 (at [ESP+8]) to point at the
   # ``"levels/selector"`` string at VA 0x001A1E3C (.rdata).
   C7 44 24 08 3C 1E 1A 00   MOV DWORD [ESP+8], 0x001A1E3C
   skip_override:
   # Replay the clobbered instructions (MOV EAX, [ESP+4] + MOV
   # ECX, [EAX+0x40]) so EAX/ECX hold the same values as
   # vanilla at VA 0x53757.
   8B 44 24 04           MOV EAX, [ESP+4]
   8B 48 40              MOV ECX, [EAX+0x40]
   # Jump back to the instruction after the clobbered window.
   E9 <rel32 back>       JMP 0x00053757

Total shim: 27 bytes.  Fits in any shim landing slot and has
zero callees, zero relocations beyond the two rel32 fields.

## Coverage

Covers **every** call to ``FUN_00053750`` — including:

- ``FUN_00055AB0`` cut-scene-end transitions (e.g. after prophecy
  → normally loads ``"levels/water/w1"``, now → selector).
- ``dev_menu_flag_check`` fall-through paths (v3's reach).
- Load-game triggers.
- Direct developer-console ``loadlevel`` commands.

The only excluded path: bink-movie loads (``param_4 != 0``),
which keep their vanilla behaviour so cutscenes still play.

## Caveats

- **Overrides EVERY level load.**  "Load Game" can't resume a
  save in its original level; the saved state is preserved but
  the player spawns in selector regardless.
- **``levels/earth/e4`` plaque soft-locks.**  Selector references
  a cut level that isn't shipped on the ISO.
- **May corrupt save files.**  The vanilla "New Game" setup
  doesn't run when a save is loaded → selector; saved games made
  in a patched build may have odd init state when loaded on an
  un-patched ISO.  Keep a backup.
- **Experimental category.**  Intended for level tours + speedrun
  practice.

## Verifying

.. code-block:: bash

   azurik-mod verify-patches --xbe patched.xbe \\
       --original vanilla.xbe --strict

Expected diff: 7 bytes at ``FUN_00053750``'s prologue (``E9 rel32
+ 2 NOPs``) PLUS the 27-byte shim block landed at the SHIMS /
``.text``-padding spill.  ``verify-patches`` knows how to follow
the JMP and whitelist both.
"""

from __future__ import annotations

import struct

from azurik_mod.patching.registry import Feature, register_feature
from azurik_mod.patching.spec import PatchSpec
from azurik_mod.patching.xbe import parse_xbe_sections, va_to_file


# ---------------------------------------------------------------------------
# Constants — the trampoline / shim layout
# ---------------------------------------------------------------------------

# Hook site: the first 7 bytes of FUN_00053750 (the universal level
# loader).  We replace them with a 5-byte JMP rel32 to our shim +
# 2 NOPs of padding so VA 0x00053757 (start of SUB ESP) still lives
# at exactly its vanilla offset.
_FUN_00053750_VA = 0x00053750
_FUN_00053750_PROLOGUE_VANILLA = bytes.fromhex(
    "8b4424048b4840")   # MOV EAX, [ESP+4]  ;  MOV ECX, [EAX+0x40]
_FUN_00053750_RETURN_VA = 0x00053757       # where the shim JMPs back to

# VA of the UTF-8 string ``"levels/selector\\0"`` in ``.rdata`` —
# the target level that the shim forces ``param_2`` to point at.
_LEVELS_SELECTOR_STR_VA = 0x001A1E3C


def _build_shim_bytes(landing_va: int) -> bytes:
    """Return the 27-byte shim code that overrides ``param_2`` at
    ``FUN_00053750``'s entry then JMPs back.

    ``landing_va`` is where the shim will be placed — we need it to
    compute the rel32 for the final JMP back to
    ``_FUN_00053750_RETURN_VA``.

    Layout (in order):

    - ``CMP [ESP+0x10], 0``   — bail out when param_4 != 0 (bink path)
    - ``JNZ +8``
    - ``MOV [ESP+8], 0x001A1E3C``   — rewrite param_2
    - (skip target)
    - ``MOV EAX, [ESP+4]``   — replay clobbered instr #1
    - ``MOV ECX, [EAX+0x40]`` — replay clobbered instr #2
    - ``JMP 0x00053757``     — return into vanilla function body
    """
    # Prelude — guard + overwrite:
    guard = bytes.fromhex("837c241000")              # CMP [ESP+0x10], 0
    jnz_over_override = bytes.fromhex("7508")        # JNZ +8
    override = (
        bytes.fromhex("c744240800000000")            # MOV [ESP+8], imm32
        [:-4]
        + struct.pack("<I", _LEVELS_SELECTOR_STR_VA)
    )
    # sanity: `override` must be 8 bytes (matches JNZ +8 displacement).
    assert len(override) == 8, override.hex()

    # Replayed vanilla instructions:
    replay = bytes.fromhex("8b4424048b4840")         # MOV EAX/ECX

    # Tail JMP back to VA 0x53757.  JMP rel32 at address
    # (landing_va + len(guard)+len(jnz)+len(override)+len(replay)) =
    # landing_va + 22.
    jmp_origin = landing_va + len(guard) + len(jnz_over_override) + \
        len(override) + len(replay)
    rel32 = _FUN_00053750_RETURN_VA - (jmp_origin + 5)
    tail_jmp = b"\xE9" + struct.pack("<i", rel32)

    shim = guard + jnz_over_override + override + replay + tail_jmp
    assert len(shim) == 27, f"expected 27-byte shim, got {len(shim)}"
    return shim


def apply_enable_dev_menu_patch(xbe_data: bytearray) -> None:
    """Install the FUN_00053750-entry trampoline that forces every
    level load to target ``levels/selector``.

    Idempotent — re-applying to an already-patched XBE is a no-op
    thanks to the prologue-byte drift check.
    """
    from azurik_mod.patching.apply import _carve_shim_landing

    hook_off = va_to_file(_FUN_00053750_VA)
    vanilla = bytes(xbe_data[hook_off:hook_off + 7])

    # Drift check — bail on anything other than the known vanilla
    # bytes so repeated applies stay idempotent.
    if vanilla[:7] != _FUN_00053750_PROLOGUE_VANILLA:
        if vanilla[0] == 0xE9 and vanilla[5:7] == b"\x90\x90":
            # Already patched (JMP rel32 + 2 NOPs).  No-op.
            return
        print(f"  WARNING: enable_dev_menu — FUN_00053750 prologue "
              f"at VA 0x{_FUN_00053750_VA:X} drifted from vanilla; "
              f"skipping (got {vanilla.hex()}).")
        return

    # Carve the shim into whatever landing slot is available
    # (.text padding, gap-expand, or appended SHIMS section).
    # `_carve_shim_landing` returns (file_offset, vaddr) — we only
    # need vaddr to compute the trampoline JMP.
    # We first have to know landing_va to build the shim's tail JMP,
    # so we temporarily carve a 27-byte placeholder, note the
    # landing VA, build the real shim, then overwrite the placeholder
    # with the real bytes.
    placeholder = b"\xCC" * 27                       # 27 × INT3
    land_fo, land_va = _carve_shim_landing(xbe_data, placeholder)
    shim_bytes = _build_shim_bytes(land_va)
    xbe_data[land_fo:land_fo + len(shim_bytes)] = shim_bytes

    # Write the trampoline: JMP rel32 to the shim landing, plus 2
    # NOPs of padding to preserve VA 0x53757 as the start of the
    # original SUB ESP.
    jmp_rel32 = land_va - (_FUN_00053750_VA + 5)
    trampoline = b"\xE9" + struct.pack("<i", jmp_rel32) + b"\x90\x90"
    xbe_data[hook_off:hook_off + 7] = trampoline

    print(f"  enable_dev_menu: trampolined FUN_00053750 "
          f"(VA 0x{_FUN_00053750_VA:X}) -> shim at "
          f"VA 0x{land_va:X}; all level loads now target "
          f"\"levels/selector\".")


# ---------------------------------------------------------------------------
# PatchSpec declarations for the verify-patches harness
# ---------------------------------------------------------------------------

# The prologue site we rewrite with a JMP.  Verify-patches compares
# against this original.
PROLOGUE_TRAMPOLINE_SPEC = PatchSpec(
    label="Trampoline FUN_00053750 (force levels/selector)",
    va=_FUN_00053750_VA,
    original=_FUN_00053750_PROLOGUE_VANILLA,
    patch=bytes.fromhex("e90000000090"),   # placeholder — filled at apply time
    is_data=False,
    safety_critical=False,
)


DEV_MENU_SITES: list[PatchSpec] = [PROLOGUE_TRAMPOLINE_SPEC]


def _dev_menu_dynamic_whitelist(xbe: bytes) -> list[tuple[int, int]]:
    """Return the byte ranges touched by :func:`apply_enable_dev_menu_patch`.

    Static 7-byte prologue always whitelisted.  If the apply is
    detected (first byte == 0xE9), follow the JMP rel32 to find the
    shim landing and whitelist its 27-byte block there too so
    ``verify-patches --strict`` doesn't flag the injected code as
    unexpected bytes.
    """
    try:
        hook_off = va_to_file(_FUN_00053750_VA)
    except Exception:  # noqa: BLE001
        return []

    ranges: list[tuple[int, int]] = [(hook_off, hook_off + 7)]

    if len(xbe) < hook_off + 7 or xbe[hook_off] != 0xE9:
        return ranges

    rel32 = struct.unpack("<i", xbe[hook_off + 1:hook_off + 5])[0]
    shim_va = _FUN_00053750_VA + 5 + rel32
    # Resolve shim VA to file offset via the live section table
    # (handles .text padding, gap-expansion, and appended SHIMS).
    try:
        _, secs = parse_xbe_sections(xbe)
    except Exception:  # noqa: BLE001
        return ranges
    for s in secs:
        if s["vaddr"] <= shim_va < s["vaddr"] + s["vsize"]:
            delta = shim_va - s["vaddr"]
            if delta < s["raw_size"]:
                shim_fo = s["raw_addr"] + delta
                ranges.append((shim_fo, shim_fo + 27))
                break
    return ranges


FEATURE = register_feature(Feature(
    name="enable_dev_menu",
    description=(
        "Forces the developer level-select hub (levels/selector) "
        "to load for every level transition (new game, load save, "
        "cutscene end, etc.).  selector.xbr contains portal "
        "plaques to every live level and cutscene.  Experimental: "
        "overrides ALL level-load paths, not just New Game."
    ),
    sites=DEV_MENU_SITES,
    apply=apply_enable_dev_menu_patch,
    default_on=False,
    included_in_randomizer_qol=False,
    category="experimental",
    tags=("cheat", "dev"),
    dynamic_whitelist_from_xbe=_dev_menu_dynamic_whitelist,
))


__all__ = [
    "DEV_MENU_SITES",
    "PROLOGUE_TRAMPOLINE_SPEC",
    "apply_enable_dev_menu_patch",
]
