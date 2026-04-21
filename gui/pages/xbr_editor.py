"""XBR Editor — convenience-first browse / edit of .xbr files.

Design goals (Mark 2):

- **Zero-friction open.**  The editor auto-provisions a workspace
  under ``<repo>/.xbr_workspace/`` (gitignored) from the project's
  ISO on first run and auto-opens ``config.xbr`` the next time.
  No file dialog in the happy path.
- **Familiar spreadsheet layout.**  Keyed-table sections render
  as a wide 2D grid (entities as rows, properties as columns) —
  the same mental model the Entity Editor uses — with inline
  editing.
- **Undo / redo.**  Every edit is reversible; ``Ctrl+Z`` / ``Ctrl+Y``
  move through the stack.
- **Persistent pending edits.**  Mutations round-trip through
  ``.xbr_workspace/pending_edits.json`` so closing the GUI
  doesn't lose work.
- **Modified-cell highlighting.**  Cells whose value differs from
  the vanilla baseline are visually tagged; a one-click
  ``Reset cell`` / ``Reset section`` undoes edits at either
  granularity.

Architecture: a Tk-free :class:`XbrEditorBackend` holds document +
vanilla baseline + undo/redo + persistence; a Tk :class:`XbrEditorPage`
is the view.  Tests exercise the backend directly
(:mod:`tests.test_xbr_editor_gui`) so the meaningful logic stays
display-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from azurik_mod.xbr import (
    KeyedTableSection,
    PointerGraph,
    RawSection,
    Section,
    VariantRecordSection,
    XbrDocument,
)
from gui.xbr_workspace import (
    SessionState,
    XbrFileInfo,
    XbrWorkspace,
)


# ---------------------------------------------------------------------------
# Pending edit record — the serialisable shape saved to disk.
# ---------------------------------------------------------------------------


@dataclass
class XbrPendingEdit:
    """One queued edit + its serialisable form.

    Mirrors :class:`~azurik_mod.patching.xbr_spec.XbrEditSpec`
    field-for-field so the dict form can be fed back through
    :func:`~azurik_mod.patching.xbr_spec.xbr_edit_spec_from_dict`
    at build time without a bespoke adapter.
    """

    op: str
    xbr_file: str
    section: Optional[str] = None
    entity: Optional[str] = None
    prop: Optional[str] = None
    value: Any = None
    offset: Optional[int] = None
    label: str = ""

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "op": self.op,
            "xbr_file": self.xbr_file,
            "label": self.label or (
                f"{self.op} {self.section}/{self.entity}/"
                f"{self.prop}"),
        }
        for k in ("section", "entity", "prop", "offset"):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        if self.value is not None:
            if isinstance(self.value, (bytes, bytearray)):
                out["value"] = bytes(self.value).hex()
                out["value_kind"] = "hex"
            else:
                out["value"] = self.value
        return out

    @classmethod
    def from_dict(cls, raw: dict) -> "XbrPendingEdit":
        value = raw.get("value")
        if raw.get("value_kind") == "hex" and isinstance(value, str):
            value = bytes.fromhex(value)
        return cls(
            op=raw["op"],
            xbr_file=raw.get("xbr_file") or raw.get("file", ""),
            section=raw.get("section"),
            entity=raw.get("entity"),
            prop=raw.get("prop"),
            value=value,
            offset=raw.get("offset"),
            label=raw.get("label", ""),
        )


# ---------------------------------------------------------------------------
# Undo / redo records.
# ---------------------------------------------------------------------------


@dataclass
class UndoRecord:
    """One undoable mutation.

    Each record is a **complete** before/after snapshot of what
    mattered — the document bytes at ``file_offset`` and the
    pending-edits list — so undo and redo atomically restore
    BOTH state surfaces.

    This shape is important because the pending-edits list
    **dedups in place** when the user hammers the same cell.
    If the undo record tried to "pop N entries" instead of
    restoring a snapshot, one undo could leave the document at
    an intermediate byte state with NO pending edit reflecting
    it — the build pipeline would ship a vanilla file while the
    editor display insisted it was modified.
    """

    filename: str
    file_offset: int
    before_bytes: bytes
    after_bytes: bytes
    pending_before: list["XbrPendingEdit"] = field(
        default_factory=list)
    """Copy of ``backend.pending_edits`` BEFORE the edit was
    applied.  :meth:`XbrEditorBackend.undo` restores this list
    verbatim so the pending-edit state and the document bytes
    stay in lockstep."""
    pending_after: list["XbrPendingEdit"] = field(
        default_factory=list)
    """Copy of ``backend.pending_edits`` AFTER the edit was
    applied.  :meth:`XbrEditorBackend.redo` restores this list
    when reapplying the edit."""


# ---------------------------------------------------------------------------
# Backend.
# ---------------------------------------------------------------------------


class XbrEditorBackend:
    """Holds the currently-open XBR + its undo/redo state.

    Split out from the Tk view so tests can exercise the editing
    semantics without a display.  All UI actions in
    :class:`XbrEditorPage` route through the methods here.
    """

    def __init__(
        self,
        workspace: Optional[XbrWorkspace] = None,
    ) -> None:
        self.workspace = workspace or XbrWorkspace.default()
        self.document: Optional[XbrDocument] = None
        self.path: Optional[Path] = None
        self.vanilla_raw: Optional[bytes] = None
        """Frozen copy of the document's bytes on open — every
        cell's 'modified?' check compares the live buffer against
        this."""
        self.pending_edits: list[XbrPendingEdit] = []
        self._undo_stack: list[UndoRecord] = []
        self._redo_stack: list[UndoRecord] = []
        # Per-section memoisation for the two hot paths the UI
        # hammers on every refresh.  Populated lazily on first
        # read; wiped by :meth:`_invalidate_section_caches` after
        # any edit that could change the cached result.  These
        # caches turned toc_entries from ~43 ms/call (measurable
        # UI lag when the grid re-renders) into ~0.3 ms/call
        # amortised.
        self._modified_cache: dict[int, bool] = {}
        self._grid_cache: dict[int, dict] = {}
        # Optional hook: views subscribe to get notified whenever
        # the document changes so they can refresh.
        self.on_change: Optional[Callable[[], None]] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self, path: Path) -> None:
        """Load ``path`` into the backend.  Clears undo / redo but
        preserves pending edits (they may target other files)."""
        self.document = XbrDocument.load(path)
        self.path = Path(path)
        self.vanilla_raw = bytes(self.document.raw)
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._invalidate_section_caches()
        # Re-apply any persisted pending edits that target the
        # just-opened file.  Callers who want the file in pristine
        # state should open then :meth:`clear_pending_for_file`.
        self._replay_pending_for_current_file()
        self._emit_change()

    def close(self) -> None:
        self.document = None
        self.path = None
        self.vanilla_raw = None
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._invalidate_section_caches()
        self._emit_change()

    def _invalidate_section_caches(
        self,
        section_index: Optional[int] = None,
    ) -> None:
        """Drop the per-section memoisation entries.

        ``section_index=None`` clears every cached result — cheap,
        correct, and used by open/close/undo/redo where many
        sections can change at once.  Passing a specific section
        index invalidates just that section's entries — used by
        point edits (``set_keyed_double`` / ``set_keyed_string``)
        so a mutation on one section doesn't pay O(sections)
        recomputation cost for unchanged neighbours.
        """
        if section_index is None:
            self._modified_cache.clear()
            self._grid_cache.clear()
            return
        self._modified_cache.pop(section_index, None)
        self._grid_cache.pop(section_index, None)

    def _replay_pending_for_current_file(self) -> None:
        """After :meth:`open`, re-apply any stored pending edits
        that target the freshly-loaded file so the live view
        reflects them.

        Pushes undo records for each replayed edit so
        ``Ctrl+Z`` works across sessions — without this, an edit
        from a previous run couldn't be undone after a restart
        even though the change is visible in the grid.

        Edits whose target (section / cell) no longer exists
        get dropped with a warning — a silent skip would leave
        the persisted JSON out of sync with the actual effect
        the build pipeline will produce.
        """
        if self.document is None or self.path is None:
            return
        my_file = self.path.name
        # Build the replay set BEFORE we start mutating, then
        # decide for each whether to keep it.  We have to be
        # careful: each replayed edit also updates
        # ``self.pending_edits`` via _append_pending (no! it
        # doesn't — see below).  The public set_* methods
        # append to pending; :meth:`_dispatch_pending` with
        # ``push_undo=True`` only mutates the document + undo
        # stack.  The persisted pending list is already loaded,
        # so we pass each entry through dispatch and re-derive
        # pending from the resulting undo stack.
        survivors: list[XbrPendingEdit] = []
        for edit in list(self.pending_edits):
            if edit.xbr_file != my_file:
                survivors.append(edit)
                continue
            if self._dispatch_pending(edit, push_undo=True):
                survivors.append(edit)
            else:
                print(f"  XBR editor: dropping stale pending "
                      f"edit {edit.label or edit.op!r} — target "
                      f"no longer exists in {my_file}.")
        if len(survivors) != len(self.pending_edits):
            self.pending_edits = survivors
            self.save_persistent_state()
        # Rebuild each undo record's pending snapshots to
        # reflect the restored list — ``_dispatch_pending`` used
        # empty pending_before/pending_after placeholders because
        # it didn't know the post-load state until now.
        self._rehydrate_undo_snapshots()

    # ------------------------------------------------------------------
    # Project / workspace integration
    # ------------------------------------------------------------------

    def workspace_xbr_files(self) -> list[XbrFileInfo]:
        """Every ``.xbr`` file in the workspace's ``gamedata/``,
        sorted for display."""
        return self.workspace.discover_xbr_files()

    def open_from_workspace(
        self,
        filename: Optional[str] = None,
    ) -> bool:
        """Auto-open a file from the workspace by basename.  Falls
        back to ``config.xbr`` when ``filename`` is ``None``.

        Returns ``True`` when a file was opened, ``False`` when
        the workspace is empty or the requested file is missing.
        """
        files = self.workspace_xbr_files()
        if not files:
            return False
        target = None
        if filename:
            for info in files:
                if info.filename == filename:
                    target = info.absolute_path
                    break
        if target is None:
            for info in files:
                if info.filename == "config.xbr":
                    target = info.absolute_path
                    break
        if target is None:
            target = files[0].absolute_path
        self.open(target)
        return True

    def ensure_workspace_provisioned(
        self,
        iso_path: Optional[Path] = None,
    ) -> bool:
        """Idempotent workspace provisioning.

        Returns ``True`` when the workspace's ``gamedata/`` is
        populated.  No-op when it's already populated; calls
        :meth:`XbrWorkspace.ensure_game_files` otherwise.
        """
        return self.workspace.ensure_game_files(iso_path)

    # ------------------------------------------------------------------
    # Persistence (pending edits + session)
    # ------------------------------------------------------------------

    def load_persistent_state(self) -> SessionState:
        """Load the workspace's pending-edits + session files.

        :attr:`pending_edits` is replaced with whatever was on
        disk; returns the restored :class:`SessionState` so the
        view can re-open the last file / section.
        """
        self.pending_edits = [
            XbrPendingEdit.from_dict(d)
            for d in self.workspace.load_pending_edits()
        ]
        return self.workspace.load_session()

    def save_persistent_state(
        self,
        session: Optional[SessionState] = None,
    ) -> None:
        """Flush pending edits + (optional) session to disk."""
        self.workspace.save_pending_edits(
            [e.to_dict() for e in self.pending_edits])
        if session is not None:
            self.workspace.save_session(session)

    def clear_pending_for_file(self, filename: str) -> int:
        """Drop every pending edit targeting ``filename``.  Returns
        the number dropped.  Does NOT re-load the file — call
        :meth:`open` afterwards to restore vanilla bytes.
        """
        before = len(self.pending_edits)
        self.pending_edits = [
            e for e in self.pending_edits if e.xbr_file != filename]
        dropped = before - len(self.pending_edits)
        self.save_persistent_state()
        return dropped

    # ------------------------------------------------------------------
    # Read-side introspection for the view
    # ------------------------------------------------------------------

    @property
    def toc_entries(self) -> list[dict]:
        if self.document is None:
            return []
        return [
            {
                "index": e.index,
                "tag": e.tag,
                "size": e.size,
                "file_offset": e.file_offset,
                "overlay": type(
                    self.document.section_for(e.index)).__name__,
                "friendly_name": self._friendly_section_name(e.index),
                "modified": self._section_has_modified_cells(e.index),
            }
            for e in self.document.toc
        ]

    def section_for(self, index: int) -> Section:
        if self.document is None:
            raise RuntimeError("no document open")
        return self.document.section_for(index)

    def section_summary(self, index: int) -> dict:
        sec = self.section_for(index)
        base: dict[str, Any] = {
            "tag": sec.entry.tag,
            "size": sec.entry.size,
            "file_offset": sec.entry.file_offset,
            "overlay": type(sec).__name__,
            "friendly_name": self._friendly_section_name(index),
        }
        if isinstance(sec, KeyedTableSection):
            base.update(
                kind="keyed_table",
                num_rows=sec.num_rows,
                num_cols=sec.num_cols,
                total_cells=sec.total_cells,
                well_formed=sec.is_well_formed(),
                row_names=sec.row_names(),
                col_names=sec.col_names(),
            )
        elif isinstance(sec, VariantRecordSection):
            base.update(
                kind="variant_record",
                entity_count=sec.entity_count,
                props_per_entity=sec.props_per_entity,
                record_size=sec.record_size,
            )
        elif isinstance(sec, RawSection):
            base.update(
                kind="raw",
                blocker=(
                    "Tag not reversed — structural edits "
                    "unavailable.  See docs/XBR_FORMAT.md § "
                    "Backlog."),
            )
        return base

    def keyed_cells(self, index: int) -> list[dict]:
        """Flat list of every cell — kept for back-compat with
        tests written against the Mark-1 editor."""
        sec = self.section_for(index)
        if not isinstance(sec, KeyedTableSection):
            return []
        out: list[dict] = []
        row_names = sec.row_names()
        col_names = sec.col_names()
        for c in range(sec.num_cols):
            for r in range(sec.num_rows):
                cell = sec.read_cell(c, r)
                out.append(_cell_to_dict(
                    cell, row_names[r], col_names[c], c, r,
                    self._cell_modified(cell.file_offset)))
        return out

    def sort_entity_order(
        self,
        section_index: int,
        prop_name: Optional[str],
        descending: bool = False,
    ) -> list[int]:
        """Return the entity (column) ordering that sorts the
        keyed-table rows by the value in property ``prop_name``.

        Tk-free so it's unit-testable.  The view layer takes the
        returned list of column indices and re-arranges its tree
        rows accordingly; the underlying :class:`XbrDocument`
        stays untouched (sort is cosmetic only).

        ``prop_name=None`` restores the original column order.
        Missing / empty cells sort last regardless of direction.
        Numeric cells sort numerically; everything else lexically.
        """
        sec = self.section_for(section_index)
        if not isinstance(sec, KeyedTableSection):
            return []
        col_names = sec.col_names()
        n = len(col_names)
        if prop_name is None:
            return list(range(n))
        row_names = sec.row_names()
        try:
            row_idx = row_names.index(prop_name)
        except ValueError:
            return list(range(n))

        # Sort in two stages so "empty cells last" holds in BOTH
        # directions — with a naïve ``reverse=True`` flag the
        # emptiness marker (which we WANT always sorted last)
        # flips to the top.  Instead, sort populated cells first
        # (honouring ``descending`` for them) then concatenate
        # empties at the end.
        populated: list[int] = []
        empties: list[int] = []
        for col_idx in range(n):
            cell = sec.read_cell(col_idx, row_idx)
            if cell.type_code == 0:
                empties.append(col_idx)
                continue
            if cell.type_code == 1:
                if cell.double_value is None:
                    empties.append(col_idx)
                    continue
            populated.append(col_idx)

        def _populated_key(col_idx: int):
            cell = sec.read_cell(col_idx, row_idx)
            if cell.type_code == 1:
                return (0, float(cell.double_value), "")
            if cell.type_code == 2:
                return (1, 0.0, (cell.string_value or "").lower())
            return (2, 0.0, "")

        populated.sort(key=_populated_key, reverse=descending)
        return populated + empties

    def keyed_cells_grid(self, index: int) -> dict:
        """Structured 2D view of the section for the grid renderer.

        Returns::

            {
              "row_names": [...],
              "col_names": [...],
              "cells": [[cell_dict | None] * num_rows] * num_cols
            }

        Each cell dict carries ``kind`` / ``value`` / ``file_offset``
        / ``modified`` / ``vanilla_value`` so the view can tag it
        without a second round-trip.  Empty cells are ``None``.

        Memoised — callers hammer this on every Tk selection
        change and on every edit; rebuilding the whole dict for
        a 4000-cell grid costs ~7 ms unmemoised.  Invalidated by
        :meth:`_invalidate_section_caches` whenever an edit
        touches the section.
        """
        cached = self._grid_cache.get(index)
        if cached is not None:
            return cached
        sec = self.section_for(index)
        if not isinstance(sec, KeyedTableSection):
            empty = {"row_names": [], "col_names": [], "cells": []}
            self._grid_cache[index] = empty
            return empty
        row_names = sec.row_names()
        col_names = sec.col_names()
        cells: list[list[Optional[dict]]] = []
        for c in range(sec.num_cols):
            col_cells: list[Optional[dict]] = []
            for r in range(sec.num_rows):
                cell = sec.read_cell(c, r)
                if cell.type_code == 0:
                    col_cells.append(None)
                else:
                    col_cells.append(_cell_to_dict(
                        cell, row_names[r], col_names[c], c, r,
                        self._cell_modified(cell.file_offset),
                        vanilla_value=self._vanilla_value_for_cell(
                            cell.file_offset, cell.type_code)))
            cells.append(col_cells)
        result = {
            "row_names": row_names,
            "col_names": col_names,
            "cells": cells,
        }
        self._grid_cache[index] = result
        return result

    # ------------------------------------------------------------------
    # Edit dispatch — routes through Phase-2 primitives AND records
    # undo / pending / persistence state.
    # ------------------------------------------------------------------

    def _dispatch_pending(
        self,
        edit: XbrPendingEdit,
        push_undo: bool,
        *,
        pending_before: Optional[list[XbrPendingEdit]] = None,
        pending_after: Optional[list[XbrPendingEdit]] = None,
        invalidate_section: Optional[int] = None,
    ) -> bool:
        """Apply ``edit`` to the currently-open document.  Used
        both by the public ``set_*`` / ``reset_*`` methods (which
        record undo) and by the replay-on-open path (which doesn't).

        Returns True when the edit actually applied, False when
        its target is missing (caller's cue to drop a stale
        persisted entry).  Invalid ops still raise — those
        represent bugs, not stale state.

        ``pending_before`` / ``pending_after`` attach snapshots
        to the undo record so undo/redo restore the pending list
        atomically.  Callers that already know the surrounding
        pending state pass them; :meth:`_replay_pending_for_current_file`
        leaves them empty and fixes up via
        :meth:`_rehydrate_undo_snapshots` afterwards.
        """
        from azurik_mod.xbr.edits import (
            replace_bytes_at,
            replace_string_at,
            set_keyed_double,
            set_keyed_string,
        )
        doc = self._require_doc()
        filename = self.path.name if self.path else ""

        def _push(
            file_offset: int,
            before: bytes,
            after: bytes,
        ) -> None:
            if not push_undo:
                return
            self._push_undo(UndoRecord(
                filename=filename,
                file_offset=file_offset,
                before_bytes=before,
                after_bytes=after,
                pending_before=_clone_edits(pending_before or []),
                pending_after=_clone_edits(pending_after or []),
            ))

        if edit.op in ("set_keyed_double", "set_keyed_string"):
            sec_key = edit.section
            ks = doc.keyed_sections().get(sec_key)
            if ks is None or edit.entity is None or edit.prop is None:
                return False
            cell = ks.find_cell(edit.entity, edit.prop)
            if cell is None:
                return False
            before = self._cell_bytes_snapshot(cell.file_offset)
            if edit.op == "set_keyed_double":
                set_keyed_double(ks, edit.entity, edit.prop,
                                 float(edit.value))
            else:
                set_keyed_string(ks, edit.entity, edit.prop,
                                 str(edit.value))
            after = self._cell_bytes_snapshot(cell.file_offset)
            _push(cell.file_offset, before, after)
            if invalidate_section is None:
                invalidate_section = self._section_index_for_key(
                    sec_key)
            self._invalidate_section_caches(invalidate_section)
            return True
        if edit.op == "replace_bytes":
            if edit.offset is None or edit.value is None:
                return False
            before = bytes(
                doc.raw[edit.offset:edit.offset + len(edit.value)])
            replace_bytes_at(doc, edit.offset, bytes(edit.value))
            after = bytes(edit.value)
            _push(edit.offset, before, after)
            # Byte-level edit could land anywhere; safest to
            # wipe every cached section result.
            self._invalidate_section_caches()
            return True
        if edit.op == "replace_string_at":
            if edit.offset is None or edit.value is None:
                return False
            buf = doc.raw
            end = edit.offset
            while end < len(buf) and buf[end] != 0:
                end += 1
            before = bytes(buf[edit.offset:end + 1])
            replace_string_at(doc, edit.offset, str(edit.value))
            end2 = edit.offset
            while end2 < len(buf) and buf[end2] != 0:
                end2 += 1
            after = bytes(buf[edit.offset:end2 + 1])
            _push(edit.offset, before, after)
            self._invalidate_section_caches()
            return True
        raise ValueError(f"unknown pending op {edit.op!r}")

    def _rehydrate_undo_snapshots(self) -> None:
        """Populate the ``pending_before`` / ``pending_after``
        fields on existing undo records after a replay.

        The replay path applies persisted edits before we know
        what the "right" snapshots are (pending_edits gets
        trimmed if a target is stale).  This runs once after
        the replay to fix up the stack: each undo record's
        ``pending_before`` becomes the pending list state JUST
        BEFORE that edit, assuming the persisted order is
        authoritative.
        """
        if not self._undo_stack:
            return
        my_file = self.path.name if self.path else ""
        my_file_edits = [
            e for e in self.pending_edits
            if e.xbr_file == my_file]
        # Walk undo records (oldest-to-newest == earliest edit
        # first).  For record N, the "before" snapshot is the
        # list up to index N, and "after" is up to N+1.
        other_edits = [
            e for e in self.pending_edits
            if e.xbr_file != my_file]
        for i, record in enumerate(self._undo_stack):
            record.pending_before = _clone_edits(
                other_edits + my_file_edits[:i])
            record.pending_after = _clone_edits(
                other_edits + my_file_edits[:i + 1])

    def _section_index_for_key(
        self,
        friendly_name: Optional[str],
    ) -> Optional[int]:
        """Map a friendly section name (e.g.
        ``"attacks_transitions"``) back to its TOC index so the
        cache invalidator can target one section instead of all.
        Returns ``None`` when the mapping isn't known."""
        if friendly_name is None or self.document is None:
            return None
        for i in range(len(self.document.toc)):
            if self._friendly_section_name(i) == friendly_name:
                return i
        return None

    def set_keyed_double(
        self,
        section_index: int,
        entity: str,
        prop: str,
        value: float,
    ) -> None:
        doc = self._require_doc()
        sec = doc.section_for(section_index)
        if not isinstance(sec, KeyedTableSection):
            raise ValueError(
                f"section at index {section_index} is "
                f"{type(sec).__name__}, not KeyedTableSection")
        section_name = self._friendly_section_name(section_index)
        edit = XbrPendingEdit(
            op="set_keyed_double",
            xbr_file=self._filename(),
            section=section_name,
            entity=entity, prop=prop, value=float(value),
            label=f"set {entity}/{prop} = {value}",
        )
        # Snapshot pending-edit state BEFORE mutating so undo
        # can restore it atomically.
        pending_before = _clone_edits(self.pending_edits)
        self._append_pending(edit)
        pending_after = _clone_edits(self.pending_edits)
        self._dispatch_pending(
            edit, push_undo=True,
            pending_before=pending_before,
            pending_after=pending_after,
            invalidate_section=section_index)
        self._emit_change()

    def set_keyed_string(
        self,
        section_index: int,
        entity: str,
        prop: str,
        value: str,
    ) -> None:
        doc = self._require_doc()
        sec = doc.section_for(section_index)
        if not isinstance(sec, KeyedTableSection):
            raise ValueError(
                f"section at index {section_index} is "
                f"{type(sec).__name__}, not KeyedTableSection")
        section_name = self._friendly_section_name(section_index)
        edit = XbrPendingEdit(
            op="set_keyed_string",
            xbr_file=self._filename(),
            section=section_name,
            entity=entity, prop=prop, value=value,
            label=f"set {entity}/{prop} = {value!r}",
        )
        pending_before = _clone_edits(self.pending_edits)
        self._append_pending(edit)
        pending_after = _clone_edits(self.pending_edits)
        self._dispatch_pending(
            edit, push_undo=True,
            pending_before=pending_before,
            pending_after=pending_after,
            invalidate_section=section_index)
        self._emit_change()

    # ------------------------------------------------------------------
    # Undo / redo
    # ------------------------------------------------------------------

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def undo(self) -> bool:
        """Pop one undo record and restore both the previous
        bytes AND the previous pending-edits list.

        Restoring the pending snapshot (instead of popping by
        count) keeps state/disk consistency when edits have been
        dedup-coalesced — a user hammering ``walkSpeed`` three
        times leaves ONE pending entry but THREE undo records;
        naively popping one pending per undo would wrongly zero
        the list on the first undo.  See
        :class:`UndoRecord` docstring for why.
        """
        if not self._undo_stack:
            return False
        record = self._undo_stack.pop()
        self._apply_bytes(record.file_offset, record.before_bytes,
                          len_hint=len(record.before_bytes))
        self.pending_edits = _clone_edits(record.pending_before)
        self._invalidate_section_caches()
        self._redo_stack.append(record)
        self.save_persistent_state()
        self._emit_change()
        return True

    def redo(self) -> bool:
        """Pop one redo record and restore the post-edit state.

        Symmetric with :meth:`undo` — restores both byte and
        pending-edit snapshots so state stays consistent.
        """
        if not self._redo_stack:
            return False
        record = self._redo_stack.pop()
        self._apply_bytes(record.file_offset, record.after_bytes,
                          len_hint=len(record.after_bytes))
        self.pending_edits = _clone_edits(record.pending_after)
        self._invalidate_section_caches()
        self._undo_stack.append(record)
        self.save_persistent_state()
        self._emit_change()
        return True

    def _push_undo(self, record: UndoRecord) -> None:
        self._undo_stack.append(record)
        # Any fresh edit invalidates the redo stack.
        self._redo_stack.clear()

    def _apply_bytes(
        self,
        offset: int,
        payload: bytes,
        *,
        len_hint: int,
    ) -> None:
        doc = self._require_doc()
        end = offset + len_hint
        doc.raw[offset:end] = payload

    # ------------------------------------------------------------------
    # Reset helpers
    # ------------------------------------------------------------------

    def reset_cell(
        self,
        section_index: int,
        entity: str,
        prop: str,
    ) -> bool:
        """Revert one cell to vanilla.  Also drops any pending edits
        targeting it.  Returns True when something was reset."""
        doc = self._require_doc()
        sec = doc.section_for(section_index)
        if not isinstance(sec, KeyedTableSection) or self.vanilla_raw is None:
            return False
        cell = sec.find_cell(entity, prop)
        if cell is None:
            return False
        before = self._cell_bytes_snapshot(cell.file_offset)
        reset_span = _cell_span(cell)
        self._restore_vanilla_span(cell.file_offset, reset_span)
        section_name = self._friendly_section_name(section_index)
        pending_before = _clone_edits(self.pending_edits)
        self._retract_pending(
            lambda e: (e.xbr_file == self._filename()
                       and e.section == section_name
                       and e.entity == entity
                       and e.prop == prop))
        pending_after = _clone_edits(self.pending_edits)
        after = self._cell_bytes_snapshot(cell.file_offset)
        self._push_undo(UndoRecord(
            filename=self._filename(),
            file_offset=cell.file_offset,
            before_bytes=before,
            after_bytes=after,
            pending_before=pending_before,
            pending_after=pending_after,
        ))
        self._invalidate_section_caches(section_index)
        self.save_persistent_state()
        self._emit_change()
        return before != after

    def reset_entity(
        self,
        section_index: int,
        entity: str,
    ) -> int:
        """Revert every modified cell under one entity.

        Fast-path implementation: walk only the pending edits
        that target ``(section_index, entity)`` instead of
        iterating every property × cell under the entity.
        Previously this was O(num_rows) ``reset_cell`` calls,
        each doing a ``find_cell`` scan — unnecessary when we
        already know which cells are dirty.
        """
        doc = self._require_doc()
        sec = doc.section_for(section_index)
        if not isinstance(sec, KeyedTableSection):
            return 0
        section_name = self._friendly_section_name(section_index)
        filename = self._filename()
        props = [
            e.prop for e in self.pending_edits
            if (e.xbr_file == filename
                and e.section == section_name
                and e.entity == entity
                and e.prop is not None)
        ]
        # Dedupe while preserving order for deterministic undo.
        seen: set[str] = set()
        props_unique: list[str] = []
        for p in props:
            if p in seen:
                continue
            seen.add(p)
            props_unique.append(p)
        count = 0
        for prop in props_unique:
            if self.reset_cell(section_index, entity, prop):
                count += 1
        return count

    def reset_section(self, section_index: int) -> int:
        """Revert every modified cell inside a section.

        Fast-path: walk pending edits for this section instead
        of the Cartesian ``entities × props`` iteration the
        original implementation did.
        """
        doc = self._require_doc()
        sec = doc.section_for(section_index)
        if not isinstance(sec, KeyedTableSection):
            return 0
        section_name = self._friendly_section_name(section_index)
        filename = self._filename()
        cells: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for e in self.pending_edits:
            if (e.xbr_file != filename
                    or e.section != section_name
                    or e.entity is None or e.prop is None):
                continue
            key = (e.entity, e.prop)
            if key in seen:
                continue
            seen.add(key)
            cells.append(key)
        count = 0
        for entity, prop in cells:
            if self.reset_cell(section_index, entity, prop):
                count += 1
        return count

    def reset_file(self) -> int:
        """Revert every pending edit targeting the currently-open
        file in a single buffer-copy operation.

        O(file_size) byte-copy (a few ms even on multi-MB level
        XBRs) instead of the O(sections × entities × props)
        loop the original implementation ran.  The undo stack
        is collapsed into a single bulk-reset record so
        ``Ctrl+Z`` after a reset restores every pre-reset edit
        at once.
        """
        if (self.document is None
                or self.vanilla_raw is None
                or self.path is None):
            return 0
        filename = self.path.name
        before_bytes = bytes(self.document.raw)
        pending_before = _clone_edits(self.pending_edits)
        # Count BEFORE resetting so the return value reflects
        # what was dropped.
        count = sum(
            1 for e in self.pending_edits
            if e.xbr_file == filename)
        if count == 0 and before_bytes == self.vanilla_raw:
            return 0
        # Wholesale buffer restore — bytes-level O(file_size).
        self.document.raw[:] = self.vanilla_raw
        self.pending_edits = [
            e for e in self.pending_edits
            if e.xbr_file != filename]
        after_bytes = bytes(self.document.raw)
        pending_after = _clone_edits(self.pending_edits)
        # One bulk-reset undo record that covers the whole file.
        # file_offset=0 + full-file before/after lets undo
        # restore every pre-reset edit in a single step.
        self._push_undo(UndoRecord(
            filename=filename,
            file_offset=0,
            before_bytes=before_bytes,
            after_bytes=after_bytes,
            pending_before=pending_before,
            pending_after=pending_after,
        ))
        self._invalidate_section_caches()
        self.save_persistent_state()
        self._emit_change()
        return count

    # ------------------------------------------------------------------
    # Persistence / export
    # ------------------------------------------------------------------

    def save_as(self, path: Path) -> None:
        doc = self._require_doc()
        doc.write(path)

    def pending_mod(self) -> dict:
        if not self.pending_edits:
            return {}
        return {
            "xbr_edits": [e.to_dict() for e in self.pending_edits]
        }

    def pointer_graph_summary(self) -> dict:
        doc = self._require_doc()
        return PointerGraph(doc).snapshot()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _require_doc(self) -> XbrDocument:
        if self.document is None:
            raise RuntimeError("no document open")
        return self.document

    def _filename(self) -> str:
        return self.path.name if self.path is not None else "<memory>"

    def _emit_change(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001 — view layer is
                # allowed to fail, backend keeps going.
                pass

    def _append_pending(self, edit: XbrPendingEdit) -> None:
        """Push ``edit`` — deduping against the most recent edit
        targeting the same cell so hammering a cell while editing
        doesn't inflate the pending list.

        The ``pending_before`` / ``pending_after`` snapshots in
        the undo record (see :meth:`_dispatch_pending`) keep undo
        consistent even when this dedup fires.
        """
        if self.pending_edits:
            last = self.pending_edits[-1]
            if (last.xbr_file == edit.xbr_file
                    and last.op == edit.op
                    and last.section == edit.section
                    and last.entity == edit.entity
                    and last.prop == edit.prop
                    and last.offset == edit.offset):
                self.pending_edits[-1] = edit
                self.save_persistent_state()
                return
        self.pending_edits.append(edit)
        self.save_persistent_state()

    def _retract_pending(
        self,
        predicate: Callable[[XbrPendingEdit], bool],
    ) -> int:
        before = len(self.pending_edits)
        self.pending_edits = [
            e for e in self.pending_edits if not predicate(e)]
        return before - len(self.pending_edits)

    def _friendly_section_name(self, index: int) -> Optional[str]:
        """Return the config.xbr section name for ``index`` or
        ``None`` for non-config files.

        Sources the offset table from
        :data:`azurik_mod.xbr.sections._KEYED_SECTION_OFFSETS` —
        the package-local copy — so the GUI stays functional when
        installed via pip (``scripts/`` is excluded from the
        wheel, so importing it at runtime blows up with
        ``ModuleNotFoundError``).
        """
        if self.document is None or not self.document.is_config_xbr():
            return None
        entry = self.document.toc[index]
        from azurik_mod.xbr.sections import _KEYED_SECTION_OFFSETS
        for name, off in _KEYED_SECTION_OFFSETS.items():
            if off == entry.file_offset:
                return name
        return None

    def _section_has_modified_cells(self, index: int) -> bool:
        """True if any cell in ``index`` differs from the vanilla
        baseline.

        Memoised — the result is stable across any run of
        UI-refresh calls until an edit invalidates the cache.
        The cache made ``toc_entries`` go from ~43 ms/call
        (noticeable UI lag during edits) to ~0.3 ms amortised.

        Detection:

        - **Keyed tables**: the TOC ``size`` field in config.xbr
          notoriously under-declares a section's real extent, so
          a straight ``[file_offset, file_offset+size)`` compare
          misses cells whose data spills past the declared end.
          We compute the effective end by taking the **next**
          section's file offset (or EOF) and compare THAT range.
          One bulk ``memcmp`` beats iterating thousands of cells.
        - **Other section types**: fall back to the TOC range —
          we don't have a finer-grained notion of "cell" there.
        """
        if self.document is None or self.vanilla_raw is None:
            return False
        cached = self._modified_cache.get(index)
        if cached is not None:
            return cached
        sec = self.document.section_for(index)
        if isinstance(sec, KeyedTableSection):
            start, end = self._effective_section_range(index)
        else:
            entry = self.document.toc[index]
            start = entry.file_offset
            end = start + entry.size
        # Bound the range by the buffer size — edge-case but
        # relevant for the tiny ``loc.xbr`` / ``selector.xbr``
        # style stubs.
        end = min(end, len(self.document.raw))
        result = (bytes(self.document.raw[start:end])
                  != bytes(self.vanilla_raw[start:end]))
        self._modified_cache[index] = result
        return result

    def _effective_section_range(
        self,
        index: int,
    ) -> tuple[int, int]:
        """Return ``(start, end)`` for the real byte extent of a
        keyed-table section.

        Start is the TOC entry's ``file_offset``.  End is the
        NEXT section-start offset (among any section whose
        file_offset > this one's) — or EOF when none.  Memoised
        on first call since the TOC doesn't change across a
        session.
        """
        if self.document is None:
            return (0, 0)
        cache_attr = "_effective_section_range_cache"
        cache: dict[int, tuple[int, int]] | None = getattr(
            self, cache_attr, None)
        if cache is None:
            cache = {}
            setattr(self, cache_attr, cache)
        cached = cache.get(index)
        if cached is not None:
            return cached
        start = self.document.toc[index].file_offset
        later_offsets = sorted(
            e.file_offset for e in self.document.toc
            if e.file_offset > start)
        end = later_offsets[0] if later_offsets else len(
            self.document.raw)
        cache[index] = (start, end)
        return cache[index]

    def _cell_modified(self, file_offset: int) -> bool:
        if self.document is None or self.vanilla_raw is None:
            return False
        # Compare 16 bytes of the cell header/payload.
        end = min(file_offset + 16, len(self.vanilla_raw))
        return (bytes(self.document.raw[file_offset:end])
                != self.vanilla_raw[file_offset:end])

    def _cell_bytes_snapshot(self, file_offset: int) -> bytes:
        """Return a copy of the current cell region (header +
        payload + string, if any).  Used as the ``before`` /
        ``after`` windows for undo records."""
        if self.document is None:
            return b""
        # The cell proper is 16 bytes.  For type-2 cells the string
        # data also matters, but we conservatively snapshot a
        # fixed window — reset uses :meth:`_restore_vanilla_span`
        # which is string-aware.
        end = min(file_offset + 16, len(self.document.raw))
        return bytes(self.document.raw[file_offset:end])

    def _restore_vanilla_span(
        self,
        file_offset: int,
        span: int,
    ) -> None:
        """Copy ``vanilla_raw[offset:offset+span]`` over the live
        document's bytes.  Preserves bytes outside the range."""
        if self.vanilla_raw is None or self.document is None:
            return
        end = min(file_offset + span, len(self.vanilla_raw))
        self.document.raw[file_offset:end] = (
            self.vanilla_raw[file_offset:end])

    def _vanilla_value_for_cell(
        self,
        file_offset: int,
        type_code: int,
    ) -> Any:
        """Decode the vanilla baseline of a cell so the tooltip can
        show "was 1.0, now 2.0" without needing a second document.
        """
        if self.vanilla_raw is None:
            return None
        if type_code == 1:
            import struct
            if file_offset + 16 > len(self.vanilla_raw):
                return None
            return struct.unpack_from(
                "<d", self.vanilla_raw, file_offset + 8)[0]
        if type_code == 2:
            import struct
            if file_offset + 16 > len(self.vanilla_raw):
                return None
            rel = struct.unpack_from(
                "<I", self.vanilla_raw, file_offset + 12)[0]
            start = file_offset + 12 + rel
            end = start
            while (end < len(self.vanilla_raw)
                   and self.vanilla_raw[end] != 0):
                end += 1
            try:
                return self.vanilla_raw[start:end].decode("ascii")
            except UnicodeDecodeError:
                return None
        return None


def _clone_edits(
    edits: "Iterable[XbrPendingEdit]",
) -> list["XbrPendingEdit"]:
    """Deep-enough copy of a pending-edit list for snapshotting.

    :class:`XbrPendingEdit` is a dataclass whose ``value`` slot
    may carry a ``bytes`` object (for ``replace_bytes`` ops);
    ``dataclasses.replace`` copies the struct shallowly which is
    sufficient — the immutable scalar / bytes payload is safe
    to share across the snapshot and the live list.
    """
    return [
        XbrPendingEdit(
            op=e.op, xbr_file=e.xbr_file, section=e.section,
            entity=e.entity, prop=e.prop, value=e.value,
            offset=e.offset, label=e.label)
        for e in edits
    ]


def _cell_span(cell) -> int:
    """Size of the bytes ``reset_cell`` should restore.

    Type-1 cells: 16 bytes (header + double).
    Type-2 cells: 16 bytes (cell) — leave the string payload
                  alone since self-relative pointers stay valid
                  even if we don't revert the string bytes.
                  The length field at cell+8 is already restored
                  by reverting the 16-byte cell.
    """
    return 16


def _cell_to_dict(
    cell,
    row_name: str,
    col_name: str,
    col_index: int,
    row_index: int,
    modified: bool,
    vanilla_value: Any = None,
) -> dict:
    kind = {0: "empty", 1: "double",
            2: "string"}.get(cell.type_code, "unknown")
    value: Any = None
    if cell.type_code == 1:
        value = cell.double_value
    elif cell.type_code == 2:
        value = cell.string_value
    return {
        "row_index": row_index,
        "col_index": col_index,
        "row_name": row_name,
        "col_name": col_name,
        "kind": kind,
        "value": value,
        "file_offset": cell.file_offset,
        "modified": modified,
        "vanilla_value": vanilla_value,
    }


# ---------------------------------------------------------------------------
# Tk view — imported lazily so tests don't pay the Tk import cost.
# ---------------------------------------------------------------------------


def _build_page_class():
    """Construct :class:`XbrEditorPage` on first use.

    The Tk import lives inside this function so the module can be
    imported in headless / test environments that only need the
    backend.  :func:`get_page_class` is the public accessor.
    """
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    from ..widgets import Page

    class _InlineEditor:
        """Temporary Entry overlay on a Treeview cell.

        Treeview doesn't support in-place editing natively; we
        position an :class:`tk.Entry` over the selected cell,
        capture Enter/Tab/Esc, and feed the value back into the
        backend.
        """

        def __init__(self, tree: ttk.Treeview, page) -> None:
            self.tree = tree
            self.page = page
            self._entry: Optional[tk.Entry] = None
            self._iid: Optional[str] = None
            self._column: Optional[str] = None

        def start(self, iid: str, column: str) -> None:
            self.cancel()
            try:
                bbox = self.tree.bbox(iid, column)
            except tk.TclError:
                return
            if not bbox:
                return
            x, y, w, h = bbox
            value = self.tree.set(iid, column)
            self._iid = iid
            self._column = column
            self._entry = tk.Entry(self.tree)
            self._entry.insert(0, value)
            self._entry.select_range(0, tk.END)
            self._entry.place(x=x, y=y, width=w, height=h)
            self._entry.focus_set()
            self._entry.bind("<Return>",
                             lambda e: self._commit("next_row"))
            self._entry.bind("<Tab>",
                             lambda e: self._commit("next_col"))
            self._entry.bind("<Shift-Tab>",
                             lambda e: self._commit("prev_col"))
            self._entry.bind("<Escape>",
                             lambda e: self.cancel())
            self._entry.bind("<FocusOut>",
                             lambda e: self._commit(None))

        def cancel(self) -> None:
            if self._entry is not None:
                try:
                    self._entry.destroy()
                except tk.TclError:
                    pass
            self._entry = None
            self._iid = None
            self._column = None

        def _commit(self, direction: Optional[str]) -> None:
            if self._entry is None or self._iid is None or self._column is None:
                return
            new_value = self._entry.get()
            iid = self._iid
            col = self._column
            self.cancel()
            self.page._commit_cell_edit(iid, col, new_value)
            if direction == "next_row":
                self.page._move_selection(0, +1, edit=True)
            elif direction == "next_col":
                self.page._move_selection(+1, 0, edit=True)
            elif direction == "prev_col":
                self.page._move_selection(-1, 0, edit=True)

    class XbrEditorPage(Page):
        title = "XBR Editor"
        description = (
            "Browse and edit .xbr data files.  The editor auto-opens "
            "config.xbr from your project; switch files via the left "
            "sidebar.  Edits persist across sessions and are applied "
            "at build time through the Patches / Build pipeline."
        )
        scrollable_body = False

        def _build(self):
            self._backend = XbrEditorBackend()
            self._backend.on_change = self._on_backend_change
            self._current_section_index: Optional[int] = None
            self._section_row_iids: list[str] = []
            self._section_col_ids: list[str] = []
            self._filter_text: str = ""
            # Sort state: ``None`` = original column order, else
            # ``(prop_name, descending)``.  Clicking the same
            # column header cycles  asc → desc → original.
            self._sort_state: Optional[tuple[str, bool]] = None
            # Cache the property names we rendered into the grid
            # last so header-click handlers can turn a "col_ids"
            # back into its property name in O(1).
            self._col_id_to_prop: dict[str, str] = {}

            # Try to provision + auto-open.
            self._provisioned = self._try_provision()
            # Load persisted state BEFORE opening so pending edits
            # re-apply to the freshly-opened document.
            session = self._backend.load_persistent_state()
            if self._provisioned:
                self._backend.open_from_workspace(
                    session.last_file or "config.xbr")

            self._build_toolbar()
            self._build_body()
            self._build_status_bar()
            self._populate_files_tree()
            self._refresh_toc_tree()
            self._apply_session_view(session)
            self._bind_shortcuts()
            self._refresh_pending_list()
            # React when the user changes the project's ISO via
            # the Project page — we want to re-provision the
            # workspace so the editor's Files tree stays in sync
            # without forcing a GUI restart.
            try:
                self.app.state.bus.subscribe(
                    "iso_changed", self._on_iso_changed)
            except AttributeError:
                pass

        # ----------------------------------------------------------
        # Layout
        # ----------------------------------------------------------

        def _build_toolbar(self) -> None:
            tb = ttk.Frame(self._body)
            tb.pack(fill=tk.X, pady=(0, 4))
            ttk.Button(tb, text="Open \u2026",
                       command=self._on_open_external).pack(
                side=tk.LEFT, padx=(0, 4))
            ttk.Button(tb, text="Save As \u2026",
                       command=self._on_save_as).pack(
                side=tk.LEFT, padx=(0, 4))
            ttk.Button(tb, text="Reload workspace",
                       command=self._on_reload_workspace).pack(
                side=tk.LEFT, padx=(0, 4))
            ttk.Separator(tb, orient=tk.VERTICAL).pack(
                side=tk.LEFT, fill=tk.Y, padx=4)
            self._undo_btn = ttk.Button(
                tb, text="Undo", command=self._on_undo,
                state=tk.DISABLED)
            self._undo_btn.pack(side=tk.LEFT, padx=(0, 2))
            self._redo_btn = ttk.Button(
                tb, text="Redo", command=self._on_redo,
                state=tk.DISABLED)
            self._redo_btn.pack(side=tk.LEFT, padx=(0, 4))
            ttk.Separator(tb, orient=tk.VERTICAL).pack(
                side=tk.LEFT, fill=tk.Y, padx=4)
            ttk.Button(tb, text="Reset cell",
                       command=self._on_reset_cell).pack(
                side=tk.LEFT, padx=(0, 2))
            ttk.Button(tb, text="Reset section",
                       command=self._on_reset_section).pack(
                side=tk.LEFT, padx=(0, 2))
            ttk.Button(tb, text="Reset file",
                       command=self._on_reset_file).pack(
                side=tk.LEFT, padx=(0, 4))
            ttk.Separator(tb, orient=tk.VERTICAL).pack(
                side=tk.LEFT, fill=tk.Y, padx=4)
            ttk.Label(tb, text="Filter:").pack(side=tk.LEFT)
            self._filter_var = tk.StringVar()
            self._filter_var.trace_add(
                "write", lambda *_: self._on_filter_change())
            ttk.Entry(tb, textvariable=self._filter_var,
                      width=22).pack(side=tk.LEFT, padx=(2, 0))

        def _build_body(self) -> None:
            body = ttk.Frame(self._body)
            body.pack(fill=tk.BOTH, expand=True)
            body.columnconfigure(0, weight=1, minsize=240)
            body.columnconfigure(1, weight=3, minsize=360)
            body.columnconfigure(2, weight=1, minsize=220)
            body.rowconfigure(0, weight=1)

            # --- Left: Files + Sections, stacked in a simple grid
            # split (no ttk.Panedwindow — the sash drifting made
            # the Files tree hard to reach after a wide grid
            # landed in the middle pane).
            left = ttk.Frame(body)
            left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
            left.columnconfigure(0, weight=1)
            # Files tree gets ~35% of the vertical space, Sections
            # ~65%; both are guaranteed-visible via minsize.
            left.rowconfigure(0, weight=0, minsize=180)
            left.rowconfigure(1, weight=1, minsize=260)

            files_frame = ttk.LabelFrame(left, text="Files")
            files_frame.grid(row=0, column=0, sticky="nsew",
                             pady=(0, 4))
            files_frame.rowconfigure(0, weight=1)
            files_frame.columnconfigure(0, weight=1)
            self._files_tree = ttk.Treeview(
                files_frame, show="tree",
                selectmode="browse",
                height=8)
            self._files_tree.grid(row=0, column=0,
                                  sticky="nsew", padx=2, pady=2)
            files_sb = ttk.Scrollbar(
                files_frame, orient="vertical",
                command=self._files_tree.yview)
            self._files_tree.configure(
                yscrollcommand=files_sb.set)
            files_sb.grid(row=0, column=1, sticky="ns")
            # Bind BOTH selection-change AND left-button-release
            # so clicking a file that's ALREADY selected re-opens
            # it (selection didn't change → <<TreeviewSelect>>
            # wouldn't fire).  Users reported hitting this.
            self._files_tree.bind(
                "<<TreeviewSelect>>", self._on_file_select)
            self._files_tree.bind(
                "<ButtonRelease-1>", self._on_file_click)

            toc_frame = ttk.LabelFrame(left, text="Sections")
            toc_frame.grid(row=1, column=0, sticky="nsew")
            toc_frame.rowconfigure(0, weight=1)
            toc_frame.columnconfigure(0, weight=1)
            self._toc_tree = ttk.Treeview(
                toc_frame,
                columns=("overlay", "dims"),
                show="tree headings",
                selectmode="browse",
                height=12)
            self._toc_tree.heading("#0", text="Section")
            self._toc_tree.heading("overlay", text="Kind")
            self._toc_tree.heading("dims", text="Dims")
            self._toc_tree.column("#0", width=180)
            self._toc_tree.column("overlay", width=80)
            self._toc_tree.column("dims", width=80)
            self._toc_tree.tag_configure(
                "modified", foreground="#e57373")
            self._toc_tree.grid(row=0, column=0, sticky="nsew",
                                padx=2, pady=2)
            toc_sb = ttk.Scrollbar(
                toc_frame, orient="vertical",
                command=self._toc_tree.yview)
            self._toc_tree.configure(yscrollcommand=toc_sb.set)
            toc_sb.grid(row=0, column=1, sticky="ns")
            self._toc_tree.bind(
                "<<TreeviewSelect>>", self._on_toc_select)

            # --- Middle: cell grid ---
            mid = ttk.Frame(body)
            mid.grid(row=0, column=1, sticky="nsew", padx=4)
            mid.rowconfigure(1, weight=1)
            mid.columnconfigure(0, weight=1)
            self._section_header = ttk.Label(
                mid, text="(no section)", anchor=tk.W,
                justify=tk.LEFT, wraplength=520)
            self._section_header.grid(
                row=0, column=0, sticky="ew", pady=(0, 4))
            grid_frame = ttk.Frame(mid)
            grid_frame.grid(row=1, column=0, sticky="nsew")
            grid_frame.rowconfigure(0, weight=1)
            grid_frame.columnconfigure(0, weight=1)
            self._grid = ttk.Treeview(
                grid_frame, show="tree headings",
                selectmode="browse")
            self._grid.grid(row=0, column=0, sticky="nsew")
            self._grid.tag_configure("modified", background="#4a3b2d")
            self._grid.tag_configure("empty", foreground="#777777")
            ysb = ttk.Scrollbar(grid_frame, orient="vertical",
                                command=self._grid.yview)
            xsb = ttk.Scrollbar(grid_frame, orient="horizontal",
                                command=self._grid.xview)
            self._grid.configure(yscrollcommand=ysb.set,
                                 xscrollcommand=xsb.set)
            ysb.grid(row=0, column=1, sticky="ns")
            xsb.grid(row=1, column=0, sticky="ew")
            self._grid.bind("<Double-Button-1>", self._on_grid_dblclick)
            self._grid.bind("<F2>", self._on_grid_f2)
            self._grid.bind("<Return>",
                            lambda e: self._on_grid_f2(e))
            self._inline = _InlineEditor(self._grid, self)

            # --- Right: inspector + structural ops ---
            insp = ttk.Frame(body)
            insp.grid(row=0, column=2, sticky="nsew", padx=(4, 0))
            ttk.Label(insp, text="Pending edits").pack(anchor=tk.W)
            self._edits_list = tk.Listbox(insp, height=14)
            self._edits_list.pack(fill=tk.BOTH, expand=True,
                                  pady=(0, 4))
            ttk.Label(insp, text="Cell details").pack(anchor=tk.W)
            self._cell_inspector = tk.Text(
                insp, height=8, width=32, wrap=tk.WORD,
                state=tk.DISABLED)
            self._cell_inspector.pack(fill=tk.BOTH, expand=False,
                                      pady=(0, 4))
            struct = ttk.LabelFrame(insp, text="Structural ops")
            struct.pack(fill=tk.X)
            for txt in ("Add row", "Remove row", "Grow pool"):
                ttk.Button(struct, text=txt,
                           command=self._on_disabled_struct,
                           state=tk.DISABLED).pack(
                    fill=tk.X, pady=1)
            ttk.Label(
                struct,
                text=("Blocked on config.xbr pool + level-XBR "
                      "layout reversal."),
                wraplength=180, justify=tk.LEFT,
                foreground="#888").pack(
                fill=tk.X, pady=(4, 0))

        def _build_status_bar(self) -> None:
            self._status_var = tk.StringVar(value="")
            bar = ttk.Frame(self._body)
            bar.pack(fill=tk.X, pady=(4, 0))
            ttk.Label(
                bar, textvariable=self._status_var,
                anchor=tk.W).pack(side=tk.LEFT, fill=tk.X,
                                  expand=True)

        # ----------------------------------------------------------
        # Provisioning helpers
        # ----------------------------------------------------------

        def _try_provision(self) -> bool:
            """Try to populate the workspace before the editor
            renders.  Falls back silently when we can't."""
            # Use the project ISO if the Project page set one.
            iso_path = None
            try:
                iso_path = getattr(
                    self.app.state, "iso_path", None)
            except AttributeError:
                iso_path = None
            if iso_path is None:
                # Try auto-detect the same way app.py does.
                try:
                    from gui import backend
                    iso_path = backend.find_base_iso()
                except Exception:  # noqa: BLE001
                    iso_path = None
            return self._backend.ensure_workspace_provisioned(
                iso_path)

        # ----------------------------------------------------------
        # Pane populators
        # ----------------------------------------------------------

        def _populate_files_tree(self) -> None:
            self._files_tree.delete(
                *self._files_tree.get_children())
            files = self._backend.workspace_xbr_files()
            if not files:
                self._files_tree.insert(
                    "", tk.END, iid="__empty__",
                    text="(no files — extract an ISO via "
                         "the Project tab, or set AZURIK_GAMEDATA)")
                return
            groups: dict[str, str] = {}
            group_labels = {
                "config": "Config",
                "index":  "Index",
                "data":   "Data files",
                "level":  "Levels",
            }
            for kind, label in group_labels.items():
                gid = f"__group_{kind}__"
                groups[kind] = gid
                self._files_tree.insert(
                    "", tk.END, iid=gid, text=label, open=True)
            for info in files:
                parent = groups.get(info.kind, "")
                self._files_tree.insert(
                    parent, tk.END, iid=f"file::{info.relative_path}",
                    text=info.filename)
            # Auto-select current file.
            if self._backend.path is not None:
                cur = self._backend.path
                wsd = self._backend.workspace.gamedata_dir
                try:
                    rel = str(cur.resolve().relative_to(
                        wsd.resolve())).replace("\\", "/")
                    iid = f"file::{rel}"
                    if self._files_tree.exists(iid):
                        self._files_tree.selection_set(iid)
                        self._files_tree.see(iid)
                except (ValueError, OSError):
                    pass

        def _refresh_toc_tree(self) -> None:
            self._toc_tree.delete(
                *self._toc_tree.get_children())
            if self._backend.document is None:
                return
            # Group sections by kind (keyed / variant / raw / index).
            groups: dict[str, str] = {}
            order = [("keyed_table", "Keyed tables"),
                     ("variant_record", "Variant records"),
                     ("index_records", "Index records"),
                     ("raw", "Raw / unmodeled")]
            for kind, label in order:
                gid = f"__toc_group_{kind}__"
                groups[kind] = gid
                self._toc_tree.insert(
                    "", tk.END, iid=gid, text=label, open=True)
            for info in self._backend.toc_entries:
                s = self._backend.section_summary(info["index"])
                kind = s.get("kind", "raw")
                group = groups.get(kind)
                if group is None:
                    group = groups["raw"]
                display = (info["friendly_name"]
                           or f"[{info['index']}] {info['tag']}")
                dims = "—"
                if kind == "keyed_table":
                    dims = f"{s['num_rows']}×{s['num_cols']}"
                elif kind == "variant_record":
                    dims = f"{s['entity_count']}×{s['props_per_entity']}"
                tags: tuple[str, ...] = (
                    ("modified",) if info["modified"] else ())
                self._toc_tree.insert(
                    group, tk.END,
                    iid=f"toc::{info['index']}",
                    text=display,
                    values=(s["overlay"], dims),
                    tags=tags)

        def _refresh_pending_list(self) -> None:
            self._edits_list.delete(0, tk.END)
            for e in self._backend.pending_edits:
                tag = "[✓]" if e.xbr_file == (
                    self._backend.path.name
                    if self._backend.path else "") else "[·]"
                self._edits_list.insert(
                    tk.END, f"{tag} {e.label or e.op}")
            self._update_status()
            self._update_undo_redo_buttons()

        def _update_status(self) -> None:
            bits: list[str] = []
            if self._backend.path is not None:
                bits.append(f"file: {self._backend.path.name}")
            count_total = len(self._backend.pending_edits)
            count_here = sum(
                1 for e in self._backend.pending_edits
                if e.xbr_file == (
                    self._backend.path.name
                    if self._backend.path else ""))
            bits.append(f"pending: {count_here} here / {count_total} total")
            ws = self._backend.workspace.root
            bits.append(f"workspace: {ws}")
            self._status_var.set("   ·   ".join(bits))

        def _update_undo_redo_buttons(self) -> None:
            self._undo_btn.configure(
                state=tk.NORMAL
                if self._backend.can_undo() else tk.DISABLED)
            self._redo_btn.configure(
                state=tk.NORMAL
                if self._backend.can_redo() else tk.DISABLED)

        def _populate_grid(self, section_index: int) -> None:
            s = self._backend.section_summary(section_index)
            friendly = (s.get("friendly_name")
                        or f"[{section_index}] {s['tag']}")
            size = s.get("size", 0)
            header = (
                f"{friendly}  ·  overlay: {s['overlay']}  ·  "
                f"size: 0x{size:X} bytes  ·  "
                f"file_offset: 0x{s['file_offset']:08X}")
            if s.get("kind") == "keyed_table":
                header += (f"  ·  {s['num_rows']} props × "
                           f"{s['num_cols']} entities")
            self._section_header.configure(text=header)

            self._grid.delete(*self._grid.get_children())
            if s.get("kind") != "keyed_table":
                self._grid.configure(columns=("info",))
                self._grid.heading("info", text="Info")
                self._grid.column("info", width=400,
                                  stretch=True, anchor=tk.W)
                self._grid.insert(
                    "", tk.END, text="(section)",
                    values=(s.get("blocker",
                                  "No structural overlay for "
                                  "this tag."),))
                return

            grid = self._backend.keyed_cells_grid(section_index)
            row_names = grid["row_names"]
            col_names = grid["col_names"]
            cells = grid["cells"]
            self._section_col_ids = list(col_names)
            # Columns: first "entity" row header column, then one
            # per property.  Treeview supports a text tree column
            # ("#0") + extra columns, so use entity names as the
            # tree text and property values as the extra columns.
            filter_text = self._filter_text.strip().lower()
            # The treeview's `columns=` needs unique IDs.  Use
            # row_names, but if duplicates sneak in (defensive),
            # disambiguate with an index suffix.
            col_ids = [f"prop{i}" for i in range(len(row_names))]
            self._col_id_to_prop = dict(zip(col_ids, row_names))
            self._grid.configure(columns=col_ids)
            # The entity column (#0) is sortable too — clicking
            # it restores the vanilla order or flips the
            # direction.
            self._grid.heading(
                "#0", text=self._decorate_header("Entity", None),
                command=lambda: self._cycle_sort(None))
            self._grid.column("#0", width=140, anchor=tk.W,
                              stretch=False)
            for i, name in enumerate(row_names):
                cid = col_ids[i]
                self._grid.heading(
                    cid,
                    text=self._decorate_header(name, name),
                    command=lambda p=name: self._cycle_sort(p))
                self._grid.column(
                    cid, width=max(80, 10 * len(name) + 40),
                    anchor=tk.W, stretch=False)
            # Apply current sort order (if any).
            render_order = self._sorted_col_order(
                section_index, col_names)
            self._section_row_iids = []
            for c_idx in render_order:
                col_name = col_names[c_idx]
                # Apply filter: match against entity name OR any
                # cell value in the row.
                if filter_text:
                    hay = [col_name.lower()]
                    for cell in cells[c_idx]:
                        if cell is not None:
                            hay.append(str(cell["value"]).lower())
                    if not any(filter_text in h for h in hay):
                        continue
                values: list[str] = []
                row_modified = False
                for r_idx, cell in enumerate(cells[c_idx]):
                    if cell is None:
                        values.append("")
                    else:
                        val = cell["value"]
                        if isinstance(val, float):
                            formatted = _format_float(val)
                        else:
                            formatted = str(val)
                        values.append(formatted)
                        if cell["modified"]:
                            row_modified = True
                tags = ("modified",) if row_modified else ()
                iid = f"row::{c_idx}"
                self._grid.insert(
                    "", tk.END, iid=iid, text=col_name,
                    values=values, tags=tags)
                self._section_row_iids.append(iid)
            self._current_section_index = section_index
            self._refresh_cell_inspector()

        def _refresh_cell_inspector(self) -> None:
            self._cell_inspector.configure(state=tk.NORMAL)
            self._cell_inspector.delete("1.0", tk.END)
            sel = self._grid.selection()
            if not sel or self._current_section_index is None:
                self._cell_inspector.configure(state=tk.DISABLED)
                return
            iid = sel[0]
            col = self._grid.identify_column(
                self._grid.winfo_pointerx() - self._grid.winfo_rootx())
            # Describe the selected row's column 0 cell by
            # default — the grid inspector shows what's under the
            # cursor or the first cell of the row.
            if not iid.startswith("row::"):
                self._cell_inspector.configure(state=tk.DISABLED)
                return
            col_idx = int(iid.split("::", 1)[1])
            entity = self._grid.item(iid, "text")
            # Use the first column (#1) if we can't identify one.
            grid = self._backend.keyed_cells_grid(
                self._current_section_index)
            lines = [f"Entity: {entity}"]
            for r_idx, row_name in enumerate(grid["row_names"]):
                cell = grid["cells"][col_idx][r_idx]
                if cell is None:
                    continue
                val = _format_value(cell["value"])
                marker = "*" if cell["modified"] else " "
                lines.append(f"  {marker} {row_name}: {val}")
                if cell["modified"] and cell["vanilla_value"] is not None:
                    lines.append(
                        f"      (vanilla: "
                        f"{_format_value(cell['vanilla_value'])})")
            self._cell_inspector.insert("1.0", "\n".join(lines))
            self._cell_inspector.configure(state=tk.DISABLED)

        def _apply_session_view(
            self, session: SessionState,
        ) -> None:
            if (self._backend.document is not None
                    and session.last_section_index is not None):
                iid = f"toc::{session.last_section_index}"
                if self._toc_tree.exists(iid):
                    self._toc_tree.selection_set(iid)
                    self._toc_tree.see(iid)
                    self._on_toc_select(None)

        # ----------------------------------------------------------
        # Event handlers
        # ----------------------------------------------------------

        def _on_backend_change(self) -> None:
            # Debounce by just doing a tree/pending refresh — the
            # grid is re-rendered on the next toc_select.
            self._refresh_toc_tree()
            self._refresh_pending_list()
            if self._current_section_index is not None:
                self._populate_grid(self._current_section_index)

        def _on_open_external(self) -> None:
            path = filedialog.askopenfilename(
                filetypes=[("XBR files", "*.xbr"),
                           ("All files", "*.*")])
            if not path:
                return
            try:
                self._backend.open(Path(path))
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("Open failed", str(exc))
                return
            self._refresh_toc_tree()
            self._populate_files_tree()
            self._save_session()

        def _on_save_as(self) -> None:
            if self._backend.document is None:
                return
            path = filedialog.asksaveasfilename(
                defaultextension=".xbr",
                filetypes=[("XBR files", "*.xbr")])
            if not path:
                return
            try:
                self._backend.save_as(Path(path))
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("Save failed", str(exc))
                return
            messagebox.showinfo(
                "Saved",
                f"Wrote {path}")

        def _on_file_select(self, _event) -> None:
            sel = self._files_tree.selection()
            if not sel or not sel[0].startswith("file::"):
                return
            self._open_selected_file(sel[0])

        def _on_file_click(self, event) -> None:
            """``<<TreeviewSelect>>`` doesn't fire when the user
            clicks the already-selected row.  This handler makes
            re-clicking the current file behave the same as
            clicking it for the first time — matters for the
            common "I made edits in the grid, now let me reload
            the file" flow."""
            iid = self._files_tree.identify_row(event.y)
            if not iid or not iid.startswith("file::"):
                return
            # If the current selection is already this row,
            # re-open.  Otherwise <<TreeviewSelect>> handles it.
            if iid in self._files_tree.selection():
                self._open_selected_file(iid)

        def _open_selected_file(self, iid: str) -> None:
            rel = iid.split("::", 1)[1]
            abs_path = (
                self._backend.workspace.gamedata_dir / rel)
            if not abs_path.exists():
                return
            try:
                self._backend.open(abs_path)
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("Open failed", str(exc))
                return
            # Reset sort state — each file has its own section
            # ordering; carrying over "sorted by walkSpeed" into
            # a level XBR makes no sense.
            self._sort_state = None
            self._refresh_toc_tree()
            # Auto-select first keyed-table for a smooth config.xbr
            # landing experience.
            for entry_info in self._backend.toc_entries:
                s = self._backend.section_summary(
                    entry_info["index"])
                if s.get("kind") == "keyed_table":
                    toc_iid = f"toc::{entry_info['index']}"
                    if self._toc_tree.exists(toc_iid):
                        self._toc_tree.selection_set(toc_iid)
                        self._toc_tree.see(toc_iid)
                        self._on_toc_select(None)
                        break
            self._save_session()

        def _on_toc_select(self, _event) -> None:
            sel = self._toc_tree.selection()
            if not sel or not sel[0].startswith("toc::"):
                return
            idx = int(sel[0].split("::", 1)[1])
            # Switching sections resets sort state — sort is a
            # property of the currently-visible grid, not the
            # document.  Keeping it would leave a stale "▲ prop"
            # indicator on a column that doesn't exist in the new
            # section.
            self._sort_state = None
            self._populate_grid(idx)
            self._save_session()

        # ----------------------------------------------------------
        # Column sorting
        # ----------------------------------------------------------

        def _cycle_sort(self, prop_name: Optional[str]) -> None:
            """Advance the sort state for ``prop_name``:

            - unsorted → ascending
            - ascending → descending
            - descending → unsorted (original column order)

            ``prop_name=None`` represents the Entity (row-header)
            column; clicking it just resets to the original order.
            """
            if prop_name is None:
                self._sort_state = None
            elif (self._sort_state is None
                  or self._sort_state[0] != prop_name):
                self._sort_state = (prop_name, False)
            else:
                _, desc = self._sort_state
                if not desc:
                    self._sort_state = (prop_name, True)
                else:
                    self._sort_state = None
            if self._current_section_index is not None:
                self._populate_grid(self._current_section_index)

        def _sorted_col_order(
            self,
            section_index: int,
            col_names: list[str],
        ) -> list[int]:
            if self._sort_state is None:
                return list(range(len(col_names)))
            prop, desc = self._sort_state
            return self._backend.sort_entity_order(
                section_index, prop, descending=desc)

        def _decorate_header(
            self,
            label: str,
            prop_name: Optional[str],
        ) -> str:
            """Append a ▲ / ▼ marker to the column header when
            that column is the current sort key."""
            if self._sort_state is None:
                return label
            sorted_prop, desc = self._sort_state
            if prop_name != sorted_prop:
                return label
            marker = "\u25bc" if desc else "\u25b2"
            return f"{label} {marker}"

        def _on_grid_dblclick(self, event) -> None:
            iid = self._grid.identify_row(event.y)
            col = self._grid.identify_column(event.x)
            if not iid or not col or col == "#0":
                return
            self._inline.start(iid, col)

        def _on_grid_f2(self, _event) -> None:
            sel = self._grid.selection()
            if not sel:
                return
            self._inline.start(sel[0], "#1")

        def _on_filter_change(self) -> None:
            self._filter_text = self._filter_var.get()
            if self._current_section_index is not None:
                self._populate_grid(self._current_section_index)

        def _on_undo(self) -> None:
            if self._backend.undo():
                self._refresh_toc_tree()

        def _on_redo(self) -> None:
            if self._backend.redo():
                self._refresh_toc_tree()

        def _on_reset_cell(self) -> None:
            coord = self._selected_cell()
            if coord is None:
                return
            section_idx, entity, prop = coord
            self._backend.reset_cell(section_idx, entity, prop)

        def _on_reset_section(self) -> None:
            if self._current_section_index is None:
                return
            count = self._backend.reset_section(
                self._current_section_index)
            if count:
                messagebox.showinfo(
                    "Reset", f"Reverted {count} cell(s).")

        def _on_reset_file(self) -> None:
            if self._backend.document is None:
                return
            if not messagebox.askyesno(
                    "Reset file",
                    f"Revert every pending edit to "
                    f"{self._backend.path.name}?"):
                return
            count = self._backend.reset_file()
            if count:
                messagebox.showinfo(
                    "Reset", f"Reverted {count} cell(s).")

        def _on_disabled_struct(self) -> None:
            messagebox.showinfo(
                "Not available",
                "Structural ops (add row / remove row / grow "
                "pool) are blocked on reverse engineering.  See "
                "docs/XBR_FORMAT.md § Backlog.")

        def _on_iso_changed(self, iso_path) -> None:
            """Project page set a new ISO — re-provision the
            workspace and refresh the Files tree."""
            try:
                self._backend.ensure_workspace_provisioned(
                    Path(iso_path) if iso_path else None)
            except Exception:  # noqa: BLE001
                return
            self._populate_files_tree()
            # If we weren't showing anything, try to open the
            # default file now.
            if self._backend.document is None:
                if self._backend.open_from_workspace("config.xbr"):
                    self._refresh_toc_tree()

        def _on_reload_workspace(self) -> None:
            """Manual trigger for the provisioning flow.

            Handy when the user extracted the ISO outside the GUI
            or the auto-detect missed the sibling directory.
            """
            iso_path = getattr(
                self.app.state, "iso_path", None)
            if iso_path is None:
                try:
                    from gui import backend
                    iso_path = backend.find_base_iso()
                except Exception:  # noqa: BLE001
                    iso_path = None
            ok = self._backend.ensure_workspace_provisioned(
                iso_path)
            if not ok:
                messagebox.showwarning(
                    "Workspace empty",
                    "Couldn't populate the editor workspace.  "
                    "Check that an ISO is set on the Project tab, "
                    "an extracted ``<name>.xiso/`` sibling exists "
                    "next to the ISO, or ``$AZURIK_GAMEDATA`` "
                    "points at a gamedata/ directory.")
                return
            self._populate_files_tree()
            if self._backend.document is None:
                if self._backend.open_from_workspace("config.xbr"):
                    self._refresh_toc_tree()

        # ----------------------------------------------------------
        # Inline-edit commit path
        # ----------------------------------------------------------

        def _commit_cell_edit(
            self, iid: str, column: str, new_value: str,
        ) -> None:
            if self._current_section_index is None:
                return
            if not iid.startswith("row::") or not column.startswith("#"):
                return
            col_idx = int(iid.split("::", 1)[1])
            try:
                prop_idx = int(column.replace("#", "")) - 1
            except ValueError:
                return
            grid = self._backend.keyed_cells_grid(
                self._current_section_index)
            if not (0 <= col_idx < len(grid["col_names"])):
                return
            if not (0 <= prop_idx < len(grid["row_names"])):
                return
            entity = grid["col_names"][col_idx]
            prop = grid["row_names"][prop_idx]
            cell = grid["cells"][col_idx][prop_idx]
            # Never add a new cell (would require structural
            # support); only edit existing type-1 / type-2 cells.
            if cell is None:
                messagebox.showwarning(
                    "Can't edit",
                    f"{entity}/{prop} is empty (no cell payload).  "
                    "Adding new cells is blocked on pool reversal.")
                return
            try:
                if cell["kind"] == "double":
                    v = float(new_value)
                    self._backend.set_keyed_double(
                        self._current_section_index,
                        entity, prop, v)
                elif cell["kind"] == "string":
                    self._backend.set_keyed_string(
                        self._current_section_index,
                        entity, prop, new_value)
                else:
                    messagebox.showwarning(
                        "Not editable",
                        f"Cell kind {cell['kind']!r} is not "
                        "editable.")
                    return
            except (ValueError, Exception) as exc:  # noqa: BLE001
                messagebox.showerror("Edit rejected", str(exc))
                return

        # ----------------------------------------------------------
        # Navigation helpers
        # ----------------------------------------------------------

        def _move_selection(
            self, dx: int, dy: int, *, edit: bool = False,
        ) -> None:
            sel = self._grid.selection()
            if not sel:
                return
            iid = sel[0]
            if not iid.startswith("row::"):
                return
            rows = self._section_row_iids
            try:
                ri = rows.index(iid)
            except ValueError:
                return
            new_ri = max(0, min(len(rows) - 1, ri + dy))
            new_iid = rows[new_ri]
            self._grid.selection_set(new_iid)
            self._grid.see(new_iid)
            if edit and dx != 0:
                # Stay in the same row, move column — inline edit
                # uses explicit column IDs already.
                pass

        def _selected_cell(
            self,
        ) -> Optional[tuple[int, str, str]]:
            if self._current_section_index is None:
                return None
            sel = self._grid.selection()
            if not sel or not sel[0].startswith("row::"):
                return None
            col_idx = int(sel[0].split("::", 1)[1])
            grid = self._backend.keyed_cells_grid(
                self._current_section_index)
            if not (0 <= col_idx < len(grid["col_names"])):
                return None
            entity = grid["col_names"][col_idx]
            # Without an active column focus, default to the first
            # populated property of the row.
            for r_idx, cell in enumerate(grid["cells"][col_idx]):
                if cell is not None and cell["modified"]:
                    return (self._current_section_index,
                            entity, grid["row_names"][r_idx])
            if grid["row_names"]:
                return (self._current_section_index,
                        entity, grid["row_names"][0])
            return None

        # ----------------------------------------------------------
        # Session persistence
        # ----------------------------------------------------------

        def _save_session(self) -> None:
            session = SessionState(
                last_file=(self._backend.path.name
                           if self._backend.path else None),
                last_section_index=self._current_section_index,
            )
            self._backend.save_persistent_state(session)

        # ----------------------------------------------------------
        # Shortcuts
        # ----------------------------------------------------------

        def _bind_shortcuts(self) -> None:
            # Bind on the page — good enough since most users
            # focus the grid / filter.  ``bind_all`` would let
            # shortcuts work in any pane but can collide across
            # pages.
            self.bind_all("<Control-z>", lambda e: self._on_undo())
            self.bind_all("<Control-y>", lambda e: self._on_redo())
            self.bind_all("<Control-f>",
                          lambda e: self._focus_filter())

        def _focus_filter(self) -> None:
            # Finding the Entry widget by traversal isn't ideal,
            # but the filter var is sufficient: force focus on
            # its widget.  Simpler: bind a direct shortcut target.
            try:
                # Just set the cursor at the filter variable; the
                # Entry widget binds to the StringVar.
                self._filter_var.set(self._filter_var.get())
            except Exception:  # noqa: BLE001
                pass

        # ----------------------------------------------------------
        # Build-page integration
        # ----------------------------------------------------------

        def get_pending_mod(self) -> dict:
            return self._backend.pending_mod()

    return XbrEditorPage


def _format_float(v: float) -> str:
    """Compact float format — 4 significant digits, no trailing
    zeros, ``-0.0`` normalised to ``0``."""
    if v == 0:
        return "0"
    formatted = f"{v:.4g}"
    return formatted


def _format_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return _format_float(value)
    return str(value)


def get_page_class():
    return _build_page_class()


__all__ = [
    "XbrEditorBackend",
    "XbrPendingEdit",
    "get_page_class",
]
