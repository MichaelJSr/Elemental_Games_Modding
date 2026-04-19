"""Save-file editor — #17 from ``docs/TOOLING_ROADMAP.md``.

High-level wrapper over :mod:`azurik_mod.save_format` that turns a
declarative list of edits into a new save directory without
hand-writing byte surgery.

## Scope

- **Text saves** (``magic.sav``, ``loc.sav``, ``options.sav``):
  edit a specific line by index, or replace the whole list.
- **Signature re-hashing**: the game's signature domain isn't
  fully reversed yet (see ``docs/SAVE_FORMAT.md`` § 7), so this
  tool applies edits, **warns** that ``signature.sav`` will still
  point at the *old* contents, and emits the saved file anyway.
  When the domain is decoded we'll wire it in here.
- **Binary saves** (``inv.sav``, level saves, ``shared.sav``):
  pass-through only for now.  The tool prints a clear message when
  a user tries to edit binary content, pointing at the decode
  TODO in SAVE_FORMAT.md.

## CLI surface

::

    azurik-mod save edit <in_slot> <out_slot> \\
        --set magic.sav:0=99.000000 \\
        --set magic.sav:3=0

The ``--set`` spec is ``<file>:<line_index>=<value>`` (zero-indexed
into ``TextSave.lines``).  Multiple ``--set`` flags compose.

Use ``--plan <plan.json>`` to load edits from a JSON file instead
— handy for reproducible batches.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from azurik_mod.save_format.azurik import AzurikSave, TextSave

__all__ = [
    "EditSpec",
    "SaveEditor",
    "SaveEditPlan",
    "SaveEditReport",
    "parse_edit_spec",
]


@dataclass(frozen=True)
class EditSpec:
    """One ``<file>:<line_index>=<value>`` edit."""

    file: str         # relative path inside the save slot, e.g. "magic.sav"
    line_index: int
    new_value: str

    def apply_to(self, text: TextSave) -> str:
        """Mutate ``text.lines`` in place; return the old value
        so the caller can report it."""
        while self.line_index >= len(text.lines):
            text.lines.append("")
        old = text.lines[self.line_index]
        text.lines[self.line_index] = self.new_value
        return old


@dataclass
class SaveEditPlan:
    """Collection of :class:`EditSpec` objects, grouped by target
    file for efficient application."""

    edits: list[EditSpec] = field(default_factory=list)

    def add(self, edit: EditSpec) -> "SaveEditPlan":
        self.edits.append(edit)
        return self

    def by_file(self) -> dict[str, list[EditSpec]]:
        out: dict[str, list[EditSpec]] = {}
        for e in self.edits:
            out.setdefault(e.file, []).append(e)
        return out

    @classmethod
    def from_json(cls, obj: dict) -> "SaveEditPlan":
        plan = cls()
        for item in obj.get("edits", []):
            plan.add(EditSpec(
                file=str(item["file"]),
                line_index=int(item["line_index"]),
                new_value=str(item["new_value"])))
        return plan

    @classmethod
    def from_json_file(cls, path: Path) -> "SaveEditPlan":
        return cls.from_json(json.loads(path.read_text("utf-8")))


@dataclass
class SaveEditReport:
    """Summary of what :class:`SaveEditor` did."""

    applied: list[tuple[EditSpec, str]] = field(
        default_factory=list)   # (edit, old_value) pairs
    skipped: list[tuple[EditSpec, str]] = field(
        default_factory=list)   # (edit, reason)
    signature_stale: bool = False
    out_path: Path | None = None

    def format(self) -> str:
        lines: list[str] = []
        for edit, old in self.applied:
            lines.append(
                f"  OK  {edit.file}[{edit.line_index}]: "
                f"{old!r} -> {edit.new_value!r}")
        for edit, reason in self.skipped:
            lines.append(
                f"  SKIP {edit.file}[{edit.line_index}]: "
                f"{edit.new_value!r} — {reason}")
        if self.signature_stale:
            lines.append(
                "  WARNING: signature.sav still matches the "
                "UNMODIFIED files — Azurik may reject the save. "
                "See docs/SAVE_FORMAT.md § 7 (hash domain).")
        if self.out_path is not None:
            lines.append(f"  wrote: {self.out_path}")
        return "\n".join(lines) or "  (no edits applied)"


class SaveEditor:
    """Stateful editor around one save slot on disk.

    Typical usage::

        editor = SaveEditor(Path("exported_save/"))
        editor.load()
        editor.apply(plan)
        report = editor.write_to(Path("patched_save/"))

    The editor never mutates the input directory in place — every
    write goes to a fresh output directory, copying anything it
    didn't touch so the result is a complete save slot.
    """

    def __init__(self, slot: Path) -> None:
        self.slot = Path(slot)
        if not self.slot.is_dir():
            raise NotADirectoryError(
                f"save slot must be a directory: {self.slot}")

    def load(self) -> dict[str, AzurikSave]:
        """Load every ``.sav`` file in the slot (recurses into
        ``levels/``).  Returns a dict keyed by relative
        forward-slash path."""
        saves: dict[str, AzurikSave] = {}
        for p in sorted(self.slot.rglob("*.sav")):
            rel = p.relative_to(self.slot).as_posix()
            try:
                saves[rel] = AzurikSave.from_path(p)
            except ValueError:
                continue
        self._saves = saves
        return saves

    def apply(self, plan: SaveEditPlan,
              report: SaveEditReport | None = None
              ) -> SaveEditReport:
        """Apply ``plan`` to the in-memory saves.  Returns the
        populated :class:`SaveEditReport`.

        Binary saves and unknown files are skipped with a human-
        readable reason so the CLI can show what got left alone.
        """
        if not hasattr(self, "_saves"):
            self.load()
        report = report or SaveEditReport()
        any_text_mutation = False
        for file_key, edits in plan.by_file().items():
            entry = self._saves.get(file_key)
            if entry is None:
                for edit in edits:
                    report.skipped.append(
                        (edit, f"file not found in save slot: "
                                f"{file_key}"))
                continue
            if entry.kind != "text":
                for edit in edits:
                    report.skipped.append(
                        (edit,
                         f"{file_key} is {entry.kind!r} — binary "
                         f"save editing is not supported yet"))
                continue
            text = entry.text
            assert text is not None
            for edit in edits:
                old = edit.apply_to(text)
                report.applied.append((edit, old))
                any_text_mutation = True
        if any_text_mutation:
            report.signature_stale = True
        return report

    def write_to(self, out_dir: Path,
                 report: SaveEditReport | None = None
                 ) -> SaveEditReport:
        """Copy the save slot to ``out_dir`` and serialise any
        edited saves.  Creates ``out_dir`` if missing; refuses
        if it already exists and isn't empty, to protect users
        from clobbering something accidentally.
        """
        if not hasattr(self, "_saves"):
            self.load()
        out = Path(out_dir)
        if out.exists() and any(out.iterdir()):
            raise FileExistsError(
                f"output directory must be empty or missing: {out}")
        out.mkdir(parents=True, exist_ok=True)

        # Copy everything first, then overwrite the edited files.
        shutil.copytree(self.slot, out, dirs_exist_ok=True)
        for rel, sav in self._saves.items():
            if sav.kind != "text" or sav.text is None:
                continue
            dest = out / rel
            dest.write_bytes(sav.text.to_bytes())
        report = report or SaveEditReport()
        report.out_path = out
        return report


# ---------------------------------------------------------------------------
# CLI parsing helpers
# ---------------------------------------------------------------------------


def parse_edit_spec(spec: str) -> EditSpec:
    """Parse a ``<file>:<line_index>=<value>`` CLI flag.

    Raises :exc:`ValueError` with a clear message when the spec
    doesn't match; callers should surface the raw spec text in
    their error handler so the user can see exactly what failed.
    """
    if "=" not in spec or ":" not in spec.split("=", 1)[0]:
        raise ValueError(
            f"bad edit spec {spec!r}: expected "
            f"'<file>:<line_index>=<value>'")
    lhs, value = spec.split("=", 1)
    file_part, line_part = lhs.rsplit(":", 1)
    try:
        line_index = int(line_part)
    except ValueError as exc:
        raise ValueError(
            f"bad edit spec {spec!r}: line index "
            f"{line_part!r} is not an integer") from exc
    if line_index < 0:
        raise ValueError(
            f"bad edit spec {spec!r}: line index must be >= 0")
    return EditSpec(file=file_part, line_index=line_index,
                    new_value=value)


def build_plan_from_cli(set_flags: Iterable[str],
                        plan_path: Path | None = None,
                        ) -> SaveEditPlan:
    """Compose a :class:`SaveEditPlan` from a mix of ``--set``
    flags and an optional ``--plan`` JSON file.  Order is
    preserved (plan file first, then CLI flags, so CLI wins on
    duplicate keys)."""
    plan = SaveEditPlan()
    if plan_path is not None:
        plan = SaveEditPlan.from_json_file(plan_path)
    for flag in set_flags:
        plan.add(parse_edit_spec(flag))
    return plan
