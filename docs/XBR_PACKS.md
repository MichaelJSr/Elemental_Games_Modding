# Authoring XBR-side features

Short guide for bundling declarative ``.xbr`` edits with a feature
pack.  Mirrors [`SHIM_AUTHORING.md`](SHIM_AUTHORING.md) for the
XBE side.

> **Design context**: the XBR pack infrastructure is phase 3 of
> the broader XBR mod platform.  See [`XBR_FORMAT.md`](XBR_FORMAT.md)
> for the byte-level format reference and the structural-edit
> pointer graph.

---

## Using the GUI XBR Editor

The simplest way to explore / edit `.xbr` files is the **XBR
Editor** tab in the GUI.  It auto-provisions a workspace from
your project's ISO on first launch and then auto-opens
`config.xbr` whenever you switch to the tab.

Features:

- **Files sidebar** — every `.xbr` file discovered in the
  extracted ISO, grouped by kind (Config / Index / Data / Levels).
  Click any entry to open it.  Clicking the already-open file
  reloads it (useful after reverting changes by hand).
- **Sections tree** — for each open file, every TOC entry
  grouped by overlay kind (Keyed tables / Variant records /
  Raw).  Sections with pending edits are highlighted in red.
- **2D grid view** — keyed-table sections render as a wide
  spreadsheet (entities as rows, properties as columns).
  Double-click a cell (or press `F2`) to edit in place;
  `Enter` / `Tab` / `Esc` navigate and commit.  Modified cells
  are highlighted.
- **Sortable columns** — click any property column header to
  sort entities by that value: first click = ascending (▲),
  second click = descending (▼), third click = restore vanilla
  order.  Empty cells always sort last regardless of direction.
  Clicking the ``Entity`` header reverts to vanilla order.
- **Filter** — type in the filter box to narrow the grid to
  entities whose name or any cell value matches.  Works in
  combination with sorting.
- **Undo / redo** — `Ctrl+Z` / `Ctrl+Y` walk back through
  every edit, even across GUI restarts and across coalesced
  "edit the same cell twice quickly" sequences.
- **Reset** — per-cell / per-section / per-file reset buttons
  restore the vanilla baseline without reopening.  The file-
  level reset is a single bulk-undo step, so `Ctrl+Z` brings
  every edit back at once if you clicked by mistake.
- **Persistent pending edits** — changes survive restarts in
  `.xbr_workspace/pending_edits.json` and flow into the Build
  pipeline automatically via the `xbr_edits` channel.  Stale
  edits (e.g. from a plugin pack you've since uninstalled)
  are dropped on load with a console warning — they never
  silently inflate the pending count.

The workspace directory (`<repo>/.xbr_workspace/`) is
gitignored — it caches a copy of the extracted ISO plus your
in-progress edit state; nothing inside ever reaches git.

### When the sidebar says "(no files — extract an ISO...)"

The editor needs an extracted ISO to browse.  Options:

1. Drop your base ISO at `<repo>/iso/<name>.iso` — the
   editor's "Reload workspace" button runs xdvdfs to extract
   into `.xbr_workspace/game/`.
2. Manually extract the ISO and rename the extract folder to
   `<name>.xiso/` alongside the ISO file — the editor's
   sibling-detection heuristic picks it up.
3. Set `AZURIK_GAMEDATA` in your environment to point at an
   existing `gamedata/` directory.

## 60-second model

- Features declare XBR edits in their ``Feature(...)`` entry via
  the ``xbr_sites`` tuple.
- Each entry is either an :class:`XbrEditSpec` (fixed edit) or an
  :class:`XbrParametricEdit` (slider-driven).
- At ISO-build time, :func:`apply_pack` is handed an
  :class:`XbrStaging` that lazy-loads touched XBR files on demand
  and flushes the modified buffers back to the extracted ISO
  tree before repack.
- GUI + CLI + declarative feature edits all route through the
  same primitives in :mod:`azurik_mod.xbr.edits`, so anything
  you can do in the GUI you can also bake into a feature pack.

---

## The shippable ops (Phase 2 scope)

| Op                       | What it does                                              | Slot constraint |
|--------------------------|-----------------------------------------------------------|-----------------|
| ``set_keyed_double``     | Rewrite a type-1 cell's double value                      | Same size (8 B).     |
| ``set_keyed_string``     | Rewrite a type-2 cell's string, in place                  | New string ≤ old slot. |
| ``replace_bytes``        | Overwrite N bytes at an absolute file offset              | Same size.           |
| ``replace_string_at``    | Overwrite a NUL-terminated ASCII string at a file offset  | New string ≤ old slot. |

Operations that would grow the file / re-layout the string pool
(e.g. ``add_keyed_row`` / ``grow_string_pool`` / any level-XBR
structural change) are **blocked on reverse-engineering work** —
see [`XBR_FORMAT.md` § Backlog](XBR_FORMAT.md#backlog) for the
unblock path.  They raise :class:`NotImplementedError` with the
concrete blocker named, so feature authors surface the issue at
build time instead of silently no-op'ing.

---

## Reference feature: ``cheat_entity_hp``

Lives at
[`azurik_mod/patches/cheat_entity_hp/`](../azurik_mod/patches/cheat_entity_hp/).
A single slider that scales ``critters_critter_data.garret4.hitPoints``
inside ``config.xbr``.  Zero XBE bytes touched.

```python
from azurik_mod.patching.registry import Feature, register_feature
from azurik_mod.patching.xbr_spec import XbrParametricEdit

GARRET4_HP_SLIDER = XbrParametricEdit(
    name="garret4_hit_points",
    label="Garret4 hit points",
    xbr_file="config.xbr",
    section="critters_critter_data",
    entity="garret4",
    prop="hitPoints",
    default=100.0,
    slider_min=1.0,
    slider_max=9999.0,
    slider_step=1.0,
    unit="HP",
)

register_feature(Feature(
    name="cheat_entity_hp",
    description="Adjust player HP via config.xbr.",
    sites=[],                             # no XBE patches
    apply=lambda xbe_data, **kw: None,   # nothing to do
    xbr_sites=(GARRET4_HP_SLIDER,),      # the declarative edit
    category="player",
    tags=("cheat", "xbr"),
))
```

That's it.  The dispatcher handles load + apply + flush.

---

## Mixing XBE and XBR edits

Features can declare both.  The dispatcher applies XBE sites first
(so shim landing / trampoline emission completes normally) and
then XBR sites.  A pack with both:

```python
register_feature(Feature(
    name="some_hybrid_feature",
    description="...",
    sites=[
        ParametricPatch(name="gravity", va=0x12345, ...),
    ],
    xbr_sites=(
        XbrEditSpec(
            label="Boost garret4 runSpeed",
            xbr_file="config.xbr",
            op="set_keyed_double",
            section="attacks_transitions",
            entity="garret4",
            prop="runSpeed",
            value=20.0,
        ),
    ),
    apply=lambda xbe_data, **kw: None,
))
```

Both edits apply in one call to :func:`apply_pack`.

---

## Authoring workflow

1. **Identify the target.**  Load the XBR in the editor or use
   ``azurik-mod xbr inspect`` / ``xbr xref`` to find the cell
   (section + entity + prop) you want to edit.

2. **Decide fixed vs slider.**  Fixed values (``"always write 5.0
   here"``) → :class:`XbrEditSpec`.  User-tunable → :class:`XbrParametricEdit`.

3. **Check the slot fits.**  For string edits, confirm your new
   string is ≤ the existing slot.  If it isn't, your only option
   today is to pick a different (shorter) replacement; pool growth
   isn't shipped.

4. **Declare the feature** in ``azurik_mod/patches/<name>/__init__.py``
   — see the reference implementation above.

5. **Register it** in ``azurik_mod/patches/__init__.py`` with an
   ``import azurik_mod.patches.<name>  # noqa: F401`` line so the
   side-effectful ``register_feature(...)`` runs at startup.

6. **Test it** in ``tests/test_<name>.py``.  Use
   :class:`XbrStaging` and :func:`apply_pack` to exercise the
   full build-time path — see
   [`tests/test_xbr_pack_dispatch.py`](../tests/test_xbr_pack_dispatch.py)
   for the pattern.

7. **Run the suite**::

       python -m pytest

8. **Test in-game** — the dispatcher's correctness guarantees
   only cover the byte level.  Always boot the patched ISO in
   xemu and confirm the gameplay effect is what you expected
   before calling the feature done.

---

## Error messages you might hit

- ``xbr_files keys: [...]`` — your :class:`XbrEditSpec` names an
  ``xbr_file`` that the staging cache can't find.  Double-check
  the filename is canonical (``config.xbr``, not ``Config.xbr``
  or ``/gamedata/config.xbr``).

- ``won't fit in the existing N B slot`` — your replacement string
  is too long.  Shorten it, or wait for the pool-growth primitive
  to land (see [XBR_FORMAT backlog](XBR_FORMAT.md#backlog)).

- ``section 'foo' not in config.xbr`` — typo in the ``section``
  name.  The canonical names come from
  [`scripts/xbr_parser.py`](../scripts/xbr_parser.py)
  ``KEYED_SECTION_OFFSETS`` — run ``azurik-mod xbr inspect
  config.xbr --sections`` to enumerate them.

- ``NotImplementedError: pool-overlap`` — you tried to add / remove
  a row.  Blocked on config.xbr pool reversal — see
  [XBR_FORMAT backlog](XBR_FORMAT.md#backlog).

- ``apply_pack was called without xbr_files=...`` — the pack
  declares ``xbr_sites`` but the caller didn't supply a staging
  dict.  The randomizer build pipeline threads it automatically;
  direct ``apply_pack`` calls from tests or scripts need to pass
  it explicitly.

---

## Where the code lives

| File                                                                  | Role |
|-----------------------------------------------------------------------|------|
| [`azurik_mod/xbr/document.py`](../azurik_mod/xbr/document.py)         | :class:`XbrDocument` — load / dumps / TOC. |
| [`azurik_mod/xbr/sections.py`](../azurik_mod/xbr/sections.py)         | :class:`KeyedTableSection` + siblings. |
| [`azurik_mod/xbr/refs.py`](../azurik_mod/xbr/refs.py)                 | Typed pointer fields. |
| [`azurik_mod/xbr/pointer_graph.py`](../azurik_mod/xbr/pointer_graph.py) | :class:`PointerGraph`. |
| [`azurik_mod/xbr/edits.py`](../azurik_mod/xbr/edits.py)               | ``set_keyed_double`` / ``set_keyed_string`` / stubs. |
| [`azurik_mod/patching/xbr_spec.py`](../azurik_mod/patching/xbr_spec.py) | :class:`XbrEditSpec`, :class:`XbrParametricEdit`, dispatchers. |
| [`azurik_mod/patching/xbr_staging.py`](../azurik_mod/patching/xbr_staging.py) | :class:`XbrStaging` — lazy load/flush cache. |
| [`azurik_mod/patching/apply.py`](../azurik_mod/patching/apply.py)     | Unified :func:`apply_pack` dispatcher. |
| [`azurik_mod/xbe_tools/xbr_xref.py`](../azurik_mod/xbe_tools/xbr_xref.py) | ``azurik-mod xbr xref`` CLI. |
