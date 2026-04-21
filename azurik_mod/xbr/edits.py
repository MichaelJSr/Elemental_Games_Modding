"""Structural edit primitives for :class:`XbrDocument`.

Phase 2 scope is deliberately conservative: we ship the primitives
that are **provably safe** against the byte-identity round-trip
contract, and we stub out the primitives that need more reverse-
engineering behind clear ``NotImplementedError`` messages.

The shippable set covers the 90% case for config.xbr mods:

- :func:`set_keyed_double` — rewrite an existing type-1 cell's
  double value.  Zero risk.
- :func:`set_keyed_string` — rewrite an existing type-2 cell's
  string, in place, same size or smaller (NUL-padded).  Zero risk
  as long as the new string fits.
- :func:`replace_bytes_at` / :func:`replace_string_at` — thin
  wrappers over the same-size primitives in the legacy
  :mod:`azurik_mod.xbe_tools.xbr_edit` (kept as a migration
  landing so :class:`XbrEditSpec` in Phase 3 can dispatch
  everything through one module).

Blocked-on-RE primitives — these raise :class:`NotImplementedError`
with a concrete pointer at what's missing:

- :func:`add_keyed_row` / :func:`remove_keyed_row` — blocked on
  config.xbr's **shared string pool** layout.  Empirically,
  sibling keyed-table sections in vanilla config.xbr overlap in
  the bytes they reference (TOC ``size`` does not bound real
  extent), so growing a section in-place risks corrupting its
  neighbour.  See ``docs/XBR_FORMAT.md`` § Backlog item 1.
- :func:`grow_string_pool` — same.
- :func:`add_level_entity` / any level-XBR structural primitive
  — blocked on level-XBR section-layout RE.  See
  ``docs/XBR_FORMAT.md`` § Backlog items 3-4.

Every shipped primitive preserves the document's pointer graph
(every :class:`Ref` still resolves) and every primitive that
would risk breaking it raises instead.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from azurik_mod.xbr.document import XbrDocument
    from azurik_mod.xbr.sections import KeyedTableSection


class XbrStructuralError(ValueError):
    """Raised when an edit can't be applied without risking data
    corruption or pointer-graph breakage.  The message always
    names what's blocking — either an oversize input or an
    unreversed-format dependency."""


# ---------------------------------------------------------------------------
# Same-size primitives
# ---------------------------------------------------------------------------


def set_keyed_double(
    section: "KeyedTableSection",
    entity: str,
    prop: str,
    value: float,
) -> int:
    """Rewrite a type-1 (double) cell in a keyed table.

    Returns the absolute file offset of the 8-byte double that was
    written.  Raises :class:`XbrStructuralError` if the cell isn't
    a double or the entity/property is missing — the caller shouldn't
    silently create new data.
    """
    cell = section.find_cell(entity, prop)
    if cell is None:
        raise XbrStructuralError(
            f"cell {entity!r}/{prop!r} not found in "
            f"{section.entry.tag!r} section at "
            f"0x{section.entry.file_offset:08X}")
    if cell.type_code != 1:
        raise XbrStructuralError(
            f"cell {entity!r}/{prop!r} has type_code={cell.type_code} "
            f"(not a double).  set_keyed_double only rewrites "
            f"existing type-1 cells; use set_keyed_string for "
            f"type-2.")
    payload_offset = cell.file_offset + 8
    struct.pack_into(
        "<d", section.document.raw, payload_offset, float(value))
    return payload_offset


def set_keyed_string(
    section: "KeyedTableSection",
    entity: str,
    prop: str,
    new: str,
) -> int:
    """Rewrite a type-2 (string) cell's payload in place.

    ``new`` (ASCII, no NUL) must fit in the existing slot — the
    current string's length bytes plus room for a NUL terminator.
    Shorter replacements are NUL-padded.

    Returns the absolute file offset of the first byte of the
    rewritten string.  Raises :class:`XbrStructuralError` when
    the string is too long, the cell isn't a string, or the
    entity/property is missing.

    Phase-2 limitation: this primitive does NOT grow the string
    pool.  Strings larger than the existing slot need growth which
    is blocked on the config.xbr pool-overlap reversal (see
    module docstring).
    """
    if any(ord(c) > 0x7F for c in new):
        raise XbrStructuralError(
            f"non-ASCII characters in {new!r}; XBR strings are "
            f"ASCII-only")
    cell = section.find_cell(entity, prop)
    if cell is None:
        raise XbrStructuralError(
            f"cell {entity!r}/{prop!r} not found in "
            f"{section.entry.tag!r} section at "
            f"0x{section.entry.file_offset:08X}")
    if cell.type_code != 2:
        raise XbrStructuralError(
            f"cell {entity!r}/{prop!r} has type_code={cell.type_code} "
            f"(not a string).  set_keyed_string only rewrites "
            f"existing type-2 cells.")

    assert cell.string_length is not None
    assert cell.string_file_offset is not None

    # Existing slot: string_length bytes + 1 NUL.  We can overwrite
    # with any string that fits.
    old_slot_total = cell.string_length + 1  # includes NUL
    new_bytes = new.encode("ascii")
    if len(new_bytes) + 1 > old_slot_total:
        raise XbrStructuralError(
            f"string {new!r} ({len(new_bytes)+1} B incl. NUL) "
            f"won't fit in the existing {old_slot_total} B slot "
            f"at 0x{cell.string_file_offset:08X} (cell "
            f"{entity!r}/{prop!r}).  Growing the string pool is "
            f"blocked on config.xbr pool-overlap RE; see "
            f"docs/XBR_FORMAT.md § Backlog.")

    buf = section.document.raw
    written = new_bytes + b"\x00" * (old_slot_total - len(new_bytes))
    assert len(written) == old_slot_total
    buf[cell.string_file_offset:
        cell.string_file_offset + old_slot_total] = written

    # Update the type-2 string_length field (cell + 8) to reflect
    # the new length.  Preserves round-trip: shrinking the declared
    # length is safe because callers that read length bytes still
    # land on our NUL-padded tail.
    struct.pack_into(
        "<I", buf, cell.file_offset + 8, len(new_bytes))

    return cell.string_file_offset


def replace_bytes_at(
    doc: "XbrDocument",
    offset: int,
    new: bytes,
) -> None:
    """Overwrite ``len(new)`` bytes at absolute file ``offset``.

    Same-size only.  Mirrors the
    :class:`azurik_mod.xbe_tools.xbr_edit.XbrEditor.replace_bytes`
    primitive so :class:`XbrEditSpec` (Phase 3) can route every
    byte-patch through this module.
    """
    end = offset + len(new)
    if offset < 0 or end > len(doc.raw):
        raise XbrStructuralError(
            f"replace_bytes out of range: offset=0x{offset:08X} "
            f"end=0x{end:08X} file_size=0x{len(doc.raw):08X}")
    doc.raw[offset:end] = new


def replace_string_at(
    doc: "XbrDocument",
    offset: int,
    new: str,
) -> None:
    """Overwrite a NUL-terminated ASCII string at absolute file
    ``offset``.  ``new`` must fit the existing slot.

    Exact same semantics as the legacy
    :meth:`azurik_mod.xbe_tools.xbr_edit.XbrEditor.replace_string_at`
    — re-implemented here so structural edits go through one
    module instead of two.
    """
    if any(ord(c) > 0x7F for c in new):
        raise XbrStructuralError(
            f"non-ASCII characters in {new!r}; XBR strings are "
            f"ASCII-only")
    buf = doc.raw
    end = offset
    while end < len(buf) and buf[end] != 0:
        end += 1
    if end >= len(buf):
        raise XbrStructuralError(
            f"no NUL terminator after offset 0x{offset:08X}; "
            f"refusing to walk off the end of the file")
    old_slot = end - offset  # bytes before NUL
    new_bytes = new.encode("ascii") + b"\x00"
    if len(new_bytes) > old_slot + 1:
        raise XbrStructuralError(
            f"string {new!r} ({len(new_bytes)} B incl. NUL) is "
            f"longer than the existing {old_slot + 1} B slot at "
            f"0x{offset:08X}")
    padded = new_bytes + b"\x00" * ((old_slot + 1) - len(new_bytes))
    buf[offset:offset + len(padded)] = padded


# ---------------------------------------------------------------------------
# Blocked-on-RE primitives
# ---------------------------------------------------------------------------


_POOL_RE_BLOCKER = (
    "Blocked on config.xbr pool-overlap reversal.  Empirically, "
    "sibling keyed-table sections in vanilla config.xbr overlap "
    "in the bytes they reference — the TOC ``size`` field is not "
    "a hard boundary — so growing any section in place risks "
    "corrupting its neighbour.  Unblocks by pinning the real "
    "section boundaries via a pass against the runtime loader.  "
    "See docs/XBR_FORMAT.md § Backlog item 1."
)


_LEVEL_RE_BLOCKER = (
    "Blocked on level-XBR section-layout reversal.  The node / "
    "surf / rdms / ents payload formats haven't been reversed; "
    "structural edits would need a per-section ref model.  See "
    "docs/XBR_FORMAT.md § Backlog items 3-4."
)


def add_keyed_row(
    section: "KeyedTableSection",
    name: str,
    values: Optional[dict] = None,
) -> None:
    """Insert a new row into a keyed table.

    Adds one 8-byte row-header record and ``num_cols * 16`` bytes
    of new cell data (column-major), updates header counters, and
    rewrites every type-2 cell's self-relative string ref so it
    still points at the correct string after the cell grid shifts.

    Raises :class:`NotImplementedError` in phase 2 — see the module
    docstring for the unblock path.
    """
    raise NotImplementedError(
        f"add_keyed_row({name!r}): {_POOL_RE_BLOCKER}")


def remove_keyed_row(
    section: "KeyedTableSection",
    name: str,
) -> None:
    """Delete a row from a keyed table.

    Symmetric inverse of :func:`add_keyed_row`; same RE dependency
    blocks the implementation.
    """
    raise NotImplementedError(
        f"remove_keyed_row({name!r}): {_POOL_RE_BLOCKER}")


def grow_string_pool(
    section: "KeyedTableSection",
    n_bytes: int,
) -> int:
    """Extend a keyed-table's string pool by ``n_bytes``.

    Returns the absolute file offset of the first newly-available
    byte (for the caller to write into).  Blocked on the same pool-
    overlap reversal that blocks row operations.
    """
    raise NotImplementedError(
        f"grow_string_pool(n_bytes={n_bytes}): {_POOL_RE_BLOCKER}")


def add_level_entity(
    doc: "XbrDocument",
    *args,
    **kwargs,
) -> None:
    """Add an entity to a level XBR (e.g. ``a1.xbr``).  Not reversed."""
    raise NotImplementedError(
        f"add_level_entity: {_LEVEL_RE_BLOCKER}")


def resize_toc_entry(
    doc: "XbrDocument",
    toc_index: int,
    new_size: int,
) -> None:
    """Resize a TOC entry's payload region.

    Requires walking every file-absolute ref in the document and
    patching it — both known file-absolute refs and any that still
    lurk inside unreversed section types.  Not shippable until the
    level-XBR section layouts are reversed.
    """
    raise NotImplementedError(
        f"resize_toc_entry(toc_index={toc_index}, "
        f"new_size={new_size}): {_LEVEL_RE_BLOCKER}")


__all__ = [
    "XbrStructuralError",
    "add_keyed_row",
    "add_level_entity",
    "grow_string_pool",
    "remove_keyed_row",
    "replace_bytes_at",
    "replace_string_at",
    "resize_toc_entry",
    "set_keyed_double",
    "set_keyed_string",
]
