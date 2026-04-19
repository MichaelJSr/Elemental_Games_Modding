"""Feature authoring surface — unifies byte / parametric / shim patches.

A *feature folder* (``azurik_mod/patches/<name>/``) is the physical
home of one toggleable mod.  Everything that belongs to the mod lives
inside the folder:

  - ``__init__.py`` — the ``Feature(...)`` declaration + any apply
    logic that doesn't fit the generic dispatcher.
  - ``shim.c`` (optional) — C source for a trampoline-backed mod.
  - ``README.md`` (optional) — per-feature notes for authors.

A :class:`Feature` is just a :class:`PatchPack` with a few new fields
that bundle authoring metadata the old ``PatchPack`` surface was
missing:

  - :class:`ShimSource` tells the apply pipeline where this feature's
    C source / compiled object live.  No hardcoded
    ``Path("shims/build/...")`` strings anywhere in feature modules.
  - ``legacy_sites`` is a list of ``PatchSpec`` sites that replaces
    the feature's trampoline patches when ``AZURIK_NO_SHIMS=1`` is
    set.  Standardises the per-pack ``AZURIK_SKIP_LOGO_LEGACY=1``
    env-var sprawl.
  - ``custom_apply`` is an escape hatch for packs whose apply logic
    isn't expressible as a flat "iterate sites and apply each" pass
    (e.g. ``player_physics`` injects floats + rewrites two
    instructions; that interaction can't be expressed as independent
    sites).

The :func:`apply_pack` dispatcher (in :mod:`.apply`) reads all of
these fields and runs the appropriate primitive — callers don't need
to know which kind of patch a pack uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ShimSource:
    """Declarative pointer to a feature's shim source + build artifact.

    Construct one with the pack's folder path (typically
    ``Path(__file__).parent``) and the stem of the C source file
    (default ``"shim"``).  At apply time the dispatcher resolves:

    - ``src``: ``<pack_folder>/<stem>.c``
    - ``obj``: ``<repo_root>/shims/build/<pack_name>.o``

    The ``.o`` filename is keyed on the pack name (not the source
    stem) so two features can both have their source at
    ``<folder>/shim.c`` without colliding in the shared build cache.

    Typical usage from a feature ``__init__.py``:

    .. code-block:: python

        from pathlib import Path
        _HERE = Path(__file__).resolve().parent
        FEATURE = Feature(
            ...,
            shim=ShimSource(folder=_HERE, stem="shim"),
            ...
        )

    The auto-compile path (see :mod:`.apply._auto_compile`) uses
    ``src`` to rebuild the object when it's missing.
    """

    folder: Path
    """Absolute path to the feature's directory."""

    stem: str = "shim"
    """Filename stem of the C source inside ``folder``.  Defaults to
    ``shim``, producing ``<folder>/shim.c`` — the scaffold convention
    ``shims/toolchain/new_shim.sh`` emits."""

    def source_path(self) -> Path:
        """Full path to the C source file (``<folder>/<stem>.c``)."""
        return self.folder / f"{self.stem}.c"

    def object_path(self, pack_name: str, repo_root: Path) -> Path:
        """Full path to the compiled PE-COFF object for ``pack_name``.

        Keyed on ``pack_name`` (not ``stem``) so two features whose
        source both live at ``<folder>/shim.c`` cannot collide in the
        shared ``shims/build/`` cache.
        """
        return repo_root / "shims" / "build" / f"{pack_name}.o"
