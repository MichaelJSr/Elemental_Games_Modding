"""Shim scaffolder — bootstrap a new feature folder with Ghidra
pickup.

Tool #6 on the roadmap.  Replaces the shell-based
``shims/toolchain/new_shim.sh`` with a Python CLI that can:

1. **Detect calling convention** from a live Ghidra instance
   (via :class:`GhidraClient`) — parses the function's
   signature + parameter-storage list to pick between
   ``__stdcall``, ``__fastcall``, ``__thiscall``, and
   ``__cdecl`` automatically.
2. **Pre-fill ``replaced_bytes``** by reading the vanilla XBE at
   the hook VA and running :func:`plan_trampoline` to verify the
   boundary is clean.
3. **Emit a complete feature folder** — ``__init__.py`` with a
   ready-to-run ``TrampolinePatch`` declaration, ``shim.c`` with
   the correct attribute + parameter list, and a ``README.md``
   stub.
4. **Fall back gracefully** — works without Ghidra or an XBE
   (produces the same skeleton ``new_shim.sh`` used to, just
   with TODOs everywhere).

## CLI

    azurik-mod new-shim <name> [--hook 0xVA] [--xbe PATH | --iso PATH]
                               [--port 8193] [--dry-run]

Examples::

    # Just the skeleton (no Ghidra / XBE)
    azurik-mod new-shim skip_prophecy

    # With hook VA — reads 16 bytes of replaced_bytes from the XBE,
    # runs the trampoline planner, leaves VA filled in.
    azurik-mod new-shim skip_prophecy --hook 0x5F6E5 \
        --xbe /path/to/default.xbe

    # Full pickup — also pulls function signature + ABI from
    # Ghidra.  Generates a C prototype with the right attribute
    # and parameter list.
    azurik-mod new-shim skip_prophecy --hook 0x5F6E5 \
        --iso Azurik.iso --port 8193

    # Preview what would be written without touching disk:
    azurik-mod new-shim skip_prophecy --hook 0x5F6E5 \
        --iso Azurik.iso --dry-run

Design notes:

- Uses :func:`plan_trampoline` to size the trampoline and warn
  when the hook site isn't a clean instruction boundary.
- Reads the Ghidra signature via our shipped
  :class:`GhidraClient` — the scaffolder does NOT require the
  MCP bridge, just the plugin's HTTP port.
- Never overwrites an existing feature folder — safe to run
  repeatedly while iterating on a name.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

__all__ = [
    "ABIGuess",
    "ScaffoldPlan",
    "generate_init_py",
    "generate_shim_c",
    "guess_abi_from_signature",
    "plan_scaffold",
    "write_scaffold",
]


# ---------------------------------------------------------------------------
# Calling-convention detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ABIGuess:
    """Inferred calling convention + parameters for a hook site.

    Attributes
    ----------
    attribute: str
        clang attribute to emit (``"stdcall"`` / ``"cdecl"`` /
        ``"fastcall"`` / ``"thiscall"``).  Plain ``"stdcall"`` is
        the safe default when the signature is unavailable.
    return_type: str
        C return type.  ``"void"`` if Ghidra reports
        ``undefined``.
    parameters: tuple[tuple[str, str], ...]
        ``(type, name)`` pairs in declaration order.  Empty tuple
        for argless functions.
    stdcall_n: int
        Number of bytes the callee pops (``@N`` suffix).  Only
        meaningful for stdcall / fastcall; 0 otherwise.
    confidence: str
        ``"high"`` when sourced from Ghidra, ``"low"`` when
        guessed from defaults.
    raw_signature: str
        Original Ghidra signature string — preserved for docs.
    notes: list[str]
        Any heuristics-level caveats the scaffolder wants to
        surface to the author.
    """

    attribute: str = "stdcall"
    return_type: str = "void"
    parameters: tuple[tuple[str, str], ...] = ()
    stdcall_n: int = 0
    confidence: str = "low"
    raw_signature: str = ""
    notes: tuple[str, ...] = ()


_TYPE_SIZE = {
    "char": 1, "unsigned char": 1, "byte": 1, "signed char": 1,
    "short": 2, "unsigned short": 2, "word": 2,
    "int": 4, "unsigned int": 4, "dword": 4,
    "long": 4, "unsigned long": 4,
    "float": 4,
    "double": 8,
    "longlong": 8, "long long": 8, "unsigned long long": 8,
    "int64": 8, "uint64": 8,
}


def _translate_ghidra_type(raw: str) -> str:
    """Turn a Ghidra type string into a shim-friendly C type.

    Ghidra uses ``undefined`` / ``undefined4`` placeholders — we
    normalise them to ``void`` / ``unsigned int`` respectively
    since shim code can't reference ``undefined``.
    """
    t = raw.strip()
    if not t or t == "undefined":
        return "void"
    # undefined1..8 → fixed-width ints
    m = re.match(r"undefined(\d+)\s*(\*?)", t)
    if m:
        width, star = int(m.group(1)), m.group(2)
        base = {
            1: "unsigned char", 2: "unsigned short",
            4: "unsigned int", 8: "unsigned long long",
        }.get(width, "unsigned int")
        return f"{base} {star}".rstrip() if star else base
    # trailing `*` stays
    return t


def _param_byte_size(type_str: str) -> int:
    """Best-effort sizeof estimate (for ``@N`` calculation)."""
    t = type_str.strip().lower()
    if t.endswith("*"):
        return 4
    return _TYPE_SIZE.get(t, 4)  # default to pointer-sized


def guess_abi_from_signature(signature: str,
                             parameters: Iterable[dict] | None = None,
                             ) -> ABIGuess:
    """Infer an :class:`ABIGuess` from Ghidra's signature string
    + the structured ``parameters`` list it returns alongside.

    ``parameters`` is the list of ``{"name", "dataType", "storage"}``
    dicts emitted by :class:`GhidraClient`.  When ``None`` we fall
    back to parsing the signature string alone.

    Heuristics:

    * Any register-stored param beyond ECX → likely ``__fastcall``
      (ECX + EDX in order).
    * Single ECX-stored param, rest on stack → likely ``__thiscall``.
    * All params on stack → default to ``__stdcall``.  We can't
      distinguish stdcall from cdecl at the signature level
      alone; downstream tools (plan_trampoline) can verify via
      the ``RET N`` / ``RET`` byte.
    """
    notes: list[str] = []
    raw = signature or ""

    # Parse return type first so we can report it even when the
    # caller didn't give us parameter storage info.  Pattern:
    # ``<return> <name>(``.
    return_type = "void"
    m = re.match(r"^\s*([^\(]+?)\s+\w+\s*\(", raw)
    if m:
        return_type = _translate_ghidra_type(m.group(1))
        if return_type == "undefined":
            return_type = "void"

    if not parameters:
        notes.append(
            "No parameter metadata available; defaulting to "
            "__stdcall(void).")
        return ABIGuess(raw_signature=raw,
                        return_type=return_type,
                        notes=tuple(notes))

    params = list(parameters)
    storages = [(p.get("storage") or "").upper() for p in params]

    # Fast-path ABI rules
    reg_storages = [s for s in storages if s not in ("STACK", "")]
    attribute = "stdcall"
    if reg_storages == ["ECX"]:
        attribute = "thiscall"
    elif reg_storages and reg_storages[:2] == ["ECX", "EDX"]:
        attribute = "fastcall"
    elif reg_storages and reg_storages != ["ECX"]:
        # Mixed / unusual register storage — shim author must
        # verify by hand.
        notes.append(
            f"Unusual register storage: {reg_storages}.  Review "
            "the ABI in Ghidra before trusting the scaffolded "
            "attribute.")

    # Parse params into (type, name)
    translated: list[tuple[str, str]] = []
    stack_bytes = 0
    for p in params:
        c_type = _translate_ghidra_type(p.get("dataType", ""))
        name = p.get("name") or f"arg{len(translated)}"
        translated.append((c_type, name))
        if (p.get("storage") or "").upper() == "STACK":
            stack_bytes += _param_byte_size(c_type)

    # Return type was parsed above; no need to repeat.

    return ABIGuess(
        attribute=attribute,
        return_type=return_type,
        parameters=tuple(translated),
        stdcall_n=stack_bytes if attribute in ("stdcall",
                                               "fastcall") else 0,
        confidence="high",
        raw_signature=raw,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Scaffold plan
# ---------------------------------------------------------------------------


@dataclass
class ScaffoldPlan:
    """Everything the scaffolder will write (dry-run-friendly)."""

    name: str
    feature_dir: Path
    init_py_path: Path
    init_py_body: str
    shim_c_path: Path
    shim_c_body: str
    readme_path: Path
    readme_body: str
    abi: ABIGuess
    hook_va: int | None
    replaced_bytes: bytes | None
    planner_warnings: list[str] = field(default_factory=list)

    @property
    def files(self) -> list[Path]:
        return [self.init_py_path, self.shim_c_path, self.readme_path]

    def summary(self) -> str:
        lines = [
            f"Scaffold plan for feature {self.name!r}",
            f"  target folder: {self.feature_dir}",
            f"  hook VA:       "
            + (f"0x{self.hook_va:08X}" if self.hook_va is not None
               else "(unset)"),
            f"  ABI attribute: __attribute__(({self.abi.attribute}))  "
            f"[confidence: {self.abi.confidence}]",
            f"  return type:   {self.abi.return_type}",
            f"  params:        "
            + (", ".join(f"{t} {n}" for t, n in self.abi.parameters)
               if self.abi.parameters else "(none)"),
        ]
        if self.replaced_bytes:
            lines.append(
                f"  replaced:      "
                f"{self.replaced_bytes.hex()} "
                f"({len(self.replaced_bytes)} B)")
        if self.abi.notes:
            lines.append("  ABI notes:")
            for n in self.abi.notes:
                lines.append(f"    - {n}")
        if self.planner_warnings:
            lines.append("  trampoline planner warnings:")
            for w in self.planner_warnings:
                lines.append(f"    ! {w}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


_INIT_PY_TEMPLATE = string.Template("""\
\"\"\"$name — $description_one_line

Generated by ``azurik-mod new-shim``.  Review the TODOs below,
compile via ``shims/toolchain/compile.sh`` (auto-compile at
apply time also works), and add a test under tests/.
\"\"\"

from __future__ import annotations

from pathlib import Path

from azurik_mod.patching import (
    ShimSource,
    TrampolinePatch,
    apply_trampoline_patch,
)
from azurik_mod.patching.registry import Feature, register_feature

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent.parent

_SHIM = ShimSource(folder=_HERE, stem="shim")


# Hook site discovered via ``azurik-mod plan-trampoline 0x$hook_va_hex``
# and ``azurik-mod xbe hexdump 0x$hook_va_hex``.  If the ``replaced_bytes``
# below don't match your local XBE, re-run those commands and update.
${upper}_TRAMPOLINE = TrampolinePatch(
    name="$name",
    label="$trampoline_label",
    va=$hook_va_literal,
    replaced_bytes=bytes.fromhex("$replaced_hex"),
    shim_object=_SHIM.object_path("$name", _REPO_ROOT),
    shim_symbol="_c_$name",
    mode="call",
)


def apply_${name}_patch(xbe_data: bytearray) -> None:
    \"\"\"Apply the ``$name`` trampoline to ``xbe_data``.\"\"\"
    apply_trampoline_patch(
        xbe_data, ${upper}_TRAMPOLINE, repo_root=_REPO_ROOT)


FEATURE = register_feature(Feature(
    name="$name",
    description=(
        "TODO: one-paragraph user-facing summary of what this "
        "patch does (this text surfaces in the GUI tab)."
    ),
    sites=[${upper}_TRAMPOLINE],
    apply=apply_${name}_patch,
    default_on=False,
    included_in_randomizer_qol=False,
    category="$category",
    tags=("c-shim",),
    shim=_SHIM,
))


__all__ = [
    "FEATURE",
    "${upper}_TRAMPOLINE",
    "apply_${name}_patch",
]
""")


_SHIM_C_TEMPLATE = string.Template("""\
/* $name — shim body generated by `azurik-mod new-shim`.
 *
 * Hook site: VA 0x$hook_va_hex$hook_site_note
 *
 * Calling convention: $attribute  [$confidence confidence]
$abi_notes_block *
 * Available headers:
 *   azurik.h          — struct layouts (CritterData, PlayerInputState)
 *                        and VA anchors.
 *   azurik_vanilla.h  — extern prototypes for vanilla Azurik functions.
 *   azurik_kernel.h   — extern prototypes for the 151 xboxkrnl imports
 *                        Azurik already pulls in.
 *
 * Remove includes you don't actually use.
 */

#include "azurik.h"
#include "azurik_vanilla.h"


/* Shim entry point.  Linker symbol is ``_c_$name`` (the extra
 * underscore matches clang's i386-pc-win32 mangling convention —
 * see shim_symbol in __init__.py). */
__attribute__(($attribute))
$return_type c_${name}($params)
{
    /* TODO: replace the body with your actual logic.  Example
     * patterns to study:
     *
     *   - qol_skip_logo:  plain __stdcall + ret (AL-styled).
     *   - player_physics: float-returning __stdcall with FPU math.
     *   - gravity_integrate (shared): inline-asm wrapper for an
     *     MSVC-RVO-ABI vanilla call.
     *
     * Until you implement something, this shim is a no-op. */
}
""")


_README_TEMPLATE = string.Template("""\
# $name

TODO: user-facing description (what behaviour this feature
changes, how to enable it, what to expect after a build).

## Hook site

- **VA**: ${hook_va_display}
- **Replaced bytes** (${replaced_len} B): `${replaced_hex_display}`

Run `azurik-mod plan-trampoline ${hook_va_display} --xbe
default.xbe` to re-verify the instruction boundary + disassemble
the current bytes.

## ABI

- Attribute: `__attribute__(($attribute))`
- Return type: `$return_type`
- Parameters: $params_description
- Confidence: $confidence (guessed from Ghidra signature)

## Activation

```bash
azurik-mod patch \\
    --iso 'Azurik.iso' \\
    --mod '{"$name": true}' \\
    -o 'Azurik_$name.iso'
```

Or tick the feature in the GUI under its configured category
tab.

## Verification

```bash
azurik-mod verify-patches \\
    --xbe patched.xbe --original vanilla.xbe --strict
```

Expected diff: the bytes at VA ${hook_va_display} replaced by a
5-byte `CALL rel32` into the SHIMS section.  All other bytes
unchanged.
""")


def generate_init_py(name: str, *,
                     hook_va: int | None,
                     replaced_bytes: bytes | None,
                     abi: ABIGuess,
                     category: str = "experimental",
                     description_one_line: str = "TODO: describe",
                     trampoline_label: str | None = None,
                     ) -> str:
    """Render the ``__init__.py`` body."""
    upper = name.upper()
    hook_va_hex = f"{hook_va:08X}" if hook_va is not None else "00000000"
    hook_va_literal = (f"0x{hook_va:08X}"
                       if hook_va is not None else "0x00000000")
    replaced_hex = (replaced_bytes or b"\x90" * 5).hex()
    return _INIT_PY_TEMPLATE.safe_substitute(
        name=name,
        upper=upper,
        hook_va_hex=hook_va_hex,
        hook_va_literal=hook_va_literal,
        replaced_hex=replaced_hex,
        description_one_line=description_one_line,
        trampoline_label=(
            trampoline_label
            or f"TODO: label for {name} trampoline"),
        category=category,
    )


def generate_shim_c(name: str, *,
                    hook_va: int | None,
                    abi: ABIGuess) -> str:
    """Render the ``shim.c`` body."""
    params_str = (", ".join(f"{t} {n}" for t, n in abi.parameters)
                  if abi.parameters else "void")
    hook_va_hex = f"{hook_va:08X}" if hook_va is not None else "????????"
    hook_site_note = (""
                      if hook_va is not None
                      else "  (!!! fill in before shipping)")
    # ABI-notes lines preceded by " * " so they render cleanly
    # inside the banner comment.  Empty when no notes.
    abi_notes_block = (
        "".join(f" * Note: {note}\n" for note in abi.notes)
        if abi.notes else "")
    return _SHIM_C_TEMPLATE.safe_substitute(
        name=name,
        hook_va_hex=hook_va_hex,
        hook_site_note=hook_site_note,
        attribute=abi.attribute,
        confidence=abi.confidence,
        abi_notes_block=abi_notes_block,
        return_type=abi.return_type,
        params=params_str,
    )


def _render_readme(name: str, *,
                   hook_va: int | None,
                   replaced_bytes: bytes | None,
                   abi: ABIGuess) -> str:
    params_description = (", ".join(f"`{t}` {n}"
                                    for t, n in abi.parameters)
                          if abi.parameters else "(none)")
    hook_va_display = (f"0x{hook_va:08X}"
                       if hook_va is not None else "0x???")
    replaced_hex_display = (replaced_bytes.hex()
                            if replaced_bytes else "?? ?? ?? ?? ??")
    replaced_len = len(replaced_bytes) if replaced_bytes else 5
    return _README_TEMPLATE.safe_substitute(
        name=name,
        hook_va_display=hook_va_display,
        replaced_hex_display=replaced_hex_display,
        replaced_len=replaced_len,
        attribute=abi.attribute,
        return_type=abi.return_type,
        params_description=params_description,
        confidence=abi.confidence,
    )


# ---------------------------------------------------------------------------
# High-level entry points
# ---------------------------------------------------------------------------


_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def plan_scaffold(name: str, *,
                  repo_root: Path,
                  hook_va: int | None = None,
                  xbe_bytes: bytes | None = None,
                  ghidra_client: "GhidraClient | None" = None,
                  category: str = "experimental",
                  ) -> ScaffoldPlan:
    """Build a :class:`ScaffoldPlan` without touching disk.

    Raises :exc:`ValueError` on a bad name / existing folder so
    the CLI can refuse early.
    """
    if not _NAME_RE.match(name):
        raise ValueError(
            f"invalid name {name!r}: must match [a-z][a-z0-9_]* "
            f"(lowercase identifier starting with a letter)")

    feature_dir = repo_root / "azurik_mod" / "patches" / name
    if feature_dir.exists():
        raise ValueError(
            f"{feature_dir} already exists; pick a different name "
            f"or delete the existing folder before re-scaffolding")

    # Pull Ghidra ABI + replaced bytes + trampoline plan.
    abi = ABIGuess()
    replaced_bytes: bytes | None = None
    planner_warnings: list[str] = []

    if hook_va is not None and xbe_bytes is not None:
        from .trampoline_planner import plan_trampoline
        try:
            plan = plan_trampoline(xbe_bytes, hook_va, budget=5)
            if plan.suggested_length > 0:
                replaced_bytes = bytes(xbe_bytes[
                    plan.file_offset: plan.file_offset
                    + plan.suggested_length])
            else:
                # Planner couldn't classify the opcode; leave
                # ``replaced_bytes`` as None so the renderer emits
                # the 5-byte NOP sentinel with a TODO.
                planner_warnings.append(
                    "Planner couldn't determine instruction "
                    "boundary; leaving replaced_bytes as a NOP "
                    "placeholder (fill in manually after "
                    "consulting Ghidra).")
            if plan.warnings:
                planner_warnings.extend(plan.warnings)
        except Exception as exc:  # noqa: BLE001
            planner_warnings.append(
                f"trampoline planner raised: {exc}")

    if hook_va is not None and ghidra_client is not None:
        try:
            fn = ghidra_client.get_function(hook_va)
            abi = guess_abi_from_signature(
                fn.signature or "",
                parameters=fn.parameters)
        except Exception as exc:  # noqa: BLE001
            planner_warnings.append(
                f"Ghidra ABI lookup failed: {exc}")

    if abi.confidence != "high":
        # Give a hint that the author should verify before shipping.
        abi = ABIGuess(
            attribute=abi.attribute,
            return_type=abi.return_type,
            parameters=abi.parameters,
            stdcall_n=abi.stdcall_n,
            confidence=abi.confidence,
            raw_signature=abi.raw_signature,
            notes=abi.notes + (
                "No Ghidra ABI pickup — the scaffolded attribute is "
                "a guess.  Verify against Ghidra before shipping.",
            ),
        )

    init_py_body = generate_init_py(
        name, hook_va=hook_va, replaced_bytes=replaced_bytes,
        abi=abi, category=category)
    shim_c_body = generate_shim_c(
        name, hook_va=hook_va, abi=abi)
    readme_body = _render_readme(
        name, hook_va=hook_va, replaced_bytes=replaced_bytes,
        abi=abi)

    return ScaffoldPlan(
        name=name,
        feature_dir=feature_dir,
        init_py_path=feature_dir / "__init__.py",
        init_py_body=init_py_body,
        shim_c_path=feature_dir / "shim.c",
        shim_c_body=shim_c_body,
        readme_path=feature_dir / "README.md",
        readme_body=readme_body,
        abi=abi,
        hook_va=hook_va,
        replaced_bytes=replaced_bytes,
        planner_warnings=planner_warnings,
    )


def write_scaffold(plan: ScaffoldPlan) -> None:
    """Commit a :class:`ScaffoldPlan` to disk.  Refuses to
    overwrite an existing folder so multiple invocations can't
    stomp each other."""
    if plan.feature_dir.exists():
        raise ValueError(
            f"{plan.feature_dir} already exists; scaffold refused")
    plan.feature_dir.mkdir(parents=True, exist_ok=False)
    plan.init_py_path.write_text(plan.init_py_body, encoding="utf-8")
    plan.shim_c_path.write_text(plan.shim_c_body, encoding="utf-8")
    plan.readme_path.write_text(plan.readme_body, encoding="utf-8")
