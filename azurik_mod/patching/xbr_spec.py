"""Declarative XBR edit descriptors.

Mirrors the XBE side's :class:`~azurik_mod.patching.spec.PatchSpec`
/ :class:`~azurik_mod.patching.spec.ParametricPatch` story: a
feature folder declares a static tuple of edits; the unified
:func:`~azurik_mod.patching.apply.apply_pack` dispatcher applies
them to a dict of loaded XBR buffers at ISO-build time.

Two shapes:

- :class:`XbrEditSpec` — a single fixed edit (byte replacement,
  string replacement, keyed-table set-value, etc.).  Analogous
  to :class:`PatchSpec`: static bytes, no user input.
- :class:`XbrParametricEdit` — slider-driven keyed-table numeric
  edit.  Analogous to :class:`ParametricPatch`: the value comes
  from the user via ``params[name]`` at apply time.

Both dispatch through the primitives in
:mod:`azurik_mod.xbr.edits`, so GUI edits, CLI edits, and
declarative feature edits all share the same write path.

Operations supported (phase 3):

- ``"set_keyed_double"`` — rewrite a type-1 cell in a keyed table.
- ``"set_keyed_string"`` — rewrite a type-2 cell in a keyed table
  (same-size, in-place).
- ``"replace_bytes"`` — overwrite bytes at an absolute file offset.
- ``"replace_string_at"`` — overwrite a NUL-terminated ASCII string
  at an absolute file offset.

Unsupported structural operations (``add_row`` / ``grow_pool`` /
level-XBR edits) raise :class:`NotImplementedError` via the
primitive's stub.  Feature authors who try to declare them surface
the blocker at build time rather than silently no-op'ing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Union


@dataclass(frozen=True)
class XbrEditSpec:
    """A single static edit against one XBR file.

    Attributes:
        label:   Human-readable line printed on apply.
        xbr_file: The filename (as it appears in the ISO's
                  ``gamedata/`` tree — e.g. ``"config.xbr"``,
                  ``"a1.xbr"``) that this edit targets.
        op:      One of the supported operation strings; see the
                 module docstring.
        section: Keyed-table section name for keyed ops; unused
                 for byte / string ops.
        entity:  Column (entity) name for keyed ops.
        prop:    Row (property) name for keyed ops.
        value:   Operation-specific value: a float for
                 ``set_keyed_double``, a str for
                 ``set_keyed_string`` / ``replace_string_at``,
                 a :class:`bytes` for ``replace_bytes``.
        offset:  Absolute file offset for the byte / string ops.
        safety_critical: Mirrors :class:`PatchSpec.safety_critical`
                  — hints tests should pin this edit tightly.
    """

    label: str
    xbr_file: str
    op: str
    section: Optional[str] = None
    entity: Optional[str] = None
    prop: Optional[str] = None
    value: Any = None
    offset: Optional[int] = None
    safety_critical: bool = False


@dataclass(frozen=True)
class XbrParametricEdit:
    """Slider-driven XBR edit.

    The ``params`` dict passed to :func:`apply_pack` carries the
    user's chosen value under ``params[name]``; missing keys fall
    back to :attr:`default`.

    Phase 3 only supports one op: ``"set_keyed_double"``.  Sliders
    against string fields or structural ops are not meaningful.

    Attributes mirror :class:`ParametricPatch` one-for-one where
    possible so the GUI can reuse its slider-rendering code.
    """

    name: str
    label: str
    xbr_file: str
    section: str
    entity: str
    prop: str
    default: float
    slider_min: float
    slider_max: float
    slider_step: float
    unit: str = "x"
    description: str = ""
    op: str = "set_keyed_double"


XbrSite = Union[XbrEditSpec, XbrParametricEdit]
"""Union type for everything that can live in ``Feature.xbr_sites``."""


# ---------------------------------------------------------------------------
# Dispatch helpers
# ---------------------------------------------------------------------------


def xbr_edit_spec_from_dict(raw: Mapping[str, Any]) -> XbrEditSpec:
    """Reconstruct an :class:`XbrEditSpec` from a JSON-friendly dict.

    Mirror of :meth:`XbrPendingEdit.to_dict` in
    :mod:`gui.pages.xbr_editor` — lets the build pipeline consume
    GUI-authored edits that arrived as JSON via the ``xbr_edits``
    channel in ``config_edits``.

    Handles the ``value_kind == "hex"`` escape for byte payloads
    (JSON can't carry raw ``bytes``).
    """
    value = raw.get("value")
    if raw.get("value_kind") == "hex" and isinstance(value, str):
        value = bytes.fromhex(value)
    # Accept ``xbr_file`` aliases a few GUI callers historically
    # emit (``file``, ``filename``).  Canonical key wins on conflict.
    xbr_file = (raw.get("xbr_file")
                or raw.get("file")
                or raw.get("filename", ""))
    return XbrEditSpec(
        label=raw.get("label", ""),
        xbr_file=xbr_file,
        op=raw["op"],
        section=raw.get("section"),
        entity=raw.get("entity"),
        prop=raw.get("prop"),
        value=value,
        offset=raw.get("offset"),
    )


def _document_cache_for(
    xbr_files: Mapping[str, bytearray],
) -> dict[str, object]:
    """Look up (or attach) a per-buffer ``XbrDocument`` cache.

    Mutating edits re-build an XbrDocument from the shared buffer,
    mutate its internal bytearray, then copy back — three copies
    per edit.  When multiple edits target the same file in one
    session, we cache the :class:`XbrDocument` on the ``xbr_files``
    mapping so construction happens once and ``dumps`` only runs
    at the final flush.

    Stashed via a private attribute when the mapping allows it
    (``XbrStaging`` does, a plain dict does too for our use-case).
    Falls back to a new empty dict per call when the mapping is
    read-only.
    """
    cache: dict[str, object] | None = getattr(
        xbr_files, "_xbr_doc_cache", None)
    if cache is None:
        cache = {}
        try:
            xbr_files._xbr_doc_cache = cache  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            pass
    return cache


def _load_doc_for_edit(
    xbr_files: Mapping[str, bytearray],
    filename: str,
):
    """Return an :class:`XbrDocument` whose ``raw`` bytearray IS
    the caller's buffer — mutations on it directly change the
    shared :class:`XbrStaging` buffer with no round-trip copy.

    Uses the per-mapping document cache to avoid reparsing the
    same file for each edit.
    """
    from azurik_mod.xbr import XbrDocument
    cache = _document_cache_for(xbr_files)
    cached = cache.get(filename)
    if cached is not None:
        return cached
    buf = xbr_files.get(filename)
    if buf is None:
        raise ValueError(
            f"XBR buffer {filename!r} not provided — "
            f"xbr_files keys: {list(xbr_files)}")
    # XbrDocument's constructor always copies; to keep mutations
    # coupled we construct once and splice the bytearray identity
    # back into xbr_files below.
    doc = XbrDocument.from_bytes(buf)
    # Replace the caller's buffer with the document's bytearray so
    # subsequent flush sees the edits.  XbrStaging + dict both
    # support item assignment; for read-only mappings we'd fall
    # through to the old copy-back path but in practice
    # ``xbr_files`` is always mutable here.  ``__setitem__`` also
    # marks the staging dirty so :meth:`XbrStaging.flush` writes
    # it to disk.
    try:
        xbr_files[filename] = doc.raw  # type: ignore[index]
    except (TypeError, KeyError):  # pragma: no cover
        pass
    # Extra-explicit dirty mark for staging instances — covers
    # callers that constructed a plain dict as ``xbr_files`` which
    # has no dirty tracking of its own.
    if hasattr(xbr_files, "mark_dirty"):
        xbr_files.mark_dirty(filename)
    cache[filename] = doc
    return doc


def apply_xbr_edit_spec(
    xbr_files: Mapping[str, bytearray],
    spec: XbrEditSpec,
) -> bool:
    """Apply a single :class:`XbrEditSpec` to the matching XBR
    buffer.

    ``xbr_files`` is a ``{filename: bytearray}`` dict — the ISO-
    build pipeline loads each touched XBR once and hands the same
    buffer to every spec that targets it.

    Optimised for batches: when multiple edits target the same
    file, the :class:`XbrDocument` is parsed once (cached via
    :func:`_document_cache_for`) and mutations happen directly in
    the shared bytearray — no per-edit copy-back.

    Returns True on success.  Raises :class:`ValueError` when the
    op is unsupported or the spec references an XBR not present
    in ``xbr_files``.
    """
    from azurik_mod.xbr.edits import (
        replace_bytes_at,
        replace_string_at,
        set_keyed_double,
        set_keyed_string,
    )

    doc = _load_doc_for_edit(xbr_files, spec.xbr_file)

    if spec.op == "set_keyed_double":
        if not (spec.section and spec.entity and spec.prop):
            raise ValueError(
                f"{spec.label}: set_keyed_double needs "
                f"section/entity/prop")
        if spec.value is None:
            raise ValueError(
                f"{spec.label}: set_keyed_double needs value")
        ks = doc.keyed_sections().get(spec.section)
        if ks is None:
            raise ValueError(
                f"{spec.label}: section {spec.section!r} not in "
                f"{spec.xbr_file}")
        set_keyed_double(ks, spec.entity, spec.prop, float(spec.value))
    elif spec.op == "set_keyed_string":
        if not (spec.section and spec.entity and spec.prop):
            raise ValueError(
                f"{spec.label}: set_keyed_string needs "
                f"section/entity/prop")
        if not isinstance(spec.value, str):
            raise ValueError(
                f"{spec.label}: set_keyed_string needs a string value")
        ks = doc.keyed_sections().get(spec.section)
        if ks is None:
            raise ValueError(
                f"{spec.label}: section {spec.section!r} not in "
                f"{spec.xbr_file}")
        set_keyed_string(ks, spec.entity, spec.prop, spec.value)
    elif spec.op == "replace_bytes":
        if spec.offset is None:
            raise ValueError(
                f"{spec.label}: replace_bytes needs offset")
        if not isinstance(spec.value, (bytes, bytearray)):
            raise ValueError(
                f"{spec.label}: replace_bytes needs bytes value")
        replace_bytes_at(doc, spec.offset, bytes(spec.value))
    elif spec.op == "replace_string_at":
        if spec.offset is None:
            raise ValueError(
                f"{spec.label}: replace_string_at needs offset")
        if not isinstance(spec.value, str):
            raise ValueError(
                f"{spec.label}: replace_string_at needs str value")
        replace_string_at(doc, spec.offset, spec.value)
    else:
        raise ValueError(
            f"{spec.label}: unknown op {spec.op!r}.  Supported: "
            f"set_keyed_double, set_keyed_string, replace_bytes, "
            f"replace_string_at.")

    # No copy-back needed — :func:`_load_doc_for_edit` installed
    # the document's bytearray INTO ``xbr_files`` so mutations are
    # already visible to downstream consumers.
    return True


def apply_xbr_parametric_edit(
    xbr_files: Mapping[str, bytearray],
    edit: XbrParametricEdit,
    value: float,
) -> bool:
    """Apply a slider-driven XBR edit.  Routes through the same
    primitives as :func:`apply_xbr_edit_spec`; kept separate for
    symmetry with the XBE-side ``apply_parametric_patch``."""
    from azurik_mod.xbr.edits import set_keyed_double

    if edit.op != "set_keyed_double":
        raise ValueError(
            f"{edit.label}: unsupported parametric op {edit.op!r}")
    if not (edit.slider_min <= value <= edit.slider_max):
        raise ValueError(
            f"{edit.label}: value {value} outside "
            f"[{edit.slider_min}, {edit.slider_max}]")

    doc = _load_doc_for_edit(xbr_files, edit.xbr_file)
    ks = doc.keyed_sections().get(edit.section)
    if ks is None:
        raise ValueError(
            f"{edit.label}: section {edit.section!r} not in "
            f"{edit.xbr_file}")
    set_keyed_double(ks, edit.entity, edit.prop, float(value))
    return True


def apply_xbr_edit_dicts(
    xbr_files: Mapping[str, bytearray],
    edits: "list[Mapping[str, Any]]",
) -> int:
    """Convert a list of JSON-friendly edit dicts into
    :class:`XbrEditSpec` instances and apply them.

    Returns the count of successfully-applied edits.  Malformed
    entries (missing ``op``, unsupported ``op``) raise
    :class:`ValueError` — silently skipping is worse than a loud
    failure at build time.

    Used by the ISO-build pipeline to consume the
    ``config_edits["xbr_edits"]`` blob emitted by the GUI's XBR
    Editor page (``gui.pages.xbr_editor.XbrEditorBackend.pending_mod``).
    """
    applied = 0
    for raw in edits:
        spec = xbr_edit_spec_from_dict(raw)
        apply_xbr_edit_spec(xbr_files, spec)
        applied += 1
    return applied


__all__ = [
    "XbrEditSpec",
    "XbrParametricEdit",
    "XbrSite",
    "apply_xbr_edit_dicts",
    "apply_xbr_edit_spec",
    "apply_xbr_parametric_edit",
    "xbr_edit_spec_from_dict",
]
