# Plugin authoring guide

Third-party packages can ship Azurik features via standard
Python entry points — no upstream PR required.  The
``azurik-mod`` CLI discovers installed plugins at startup and
registers their features in the same global registry shipped
packs use.

## How it works

1. Your plugin is a normal Python package with a `pyproject.toml`.
2. Declare an entry point under the ``azurik_mod.patches``
   group pointing at a module that calls
   `azurik_mod.patching.registry.register_feature(...)` at import
   time.
3. `pip install .` (or a PyPI install) makes the entry point
   discoverable.
4. The next `azurik-mod` invocation imports your module, runs
   the registration side effect, and your feature shows up in
   the GUI's Patches tabs + the CLI's `--mod` JSON.

No runtime modification of `azurik-mod` itself.  No monkey-
patching.  Just entry points + the already-public registry API.

## Minimal plugin skeleton

```
my-azurik-plugin/
├── pyproject.toml
└── my_azurik_plugin/
    └── __init__.py      # register_feature(...) lives here
```

### `pyproject.toml`

```toml
[project]
name = "my-azurik-plugin"
version = "0.1.0"
description = "My cool Azurik mod plugin."
requires-python = ">=3.10"
dependencies = [
    "azurik-mod >= 0.1.0",
]

[project.entry-points."azurik_mod.patches"]
my_cool_mod = "my_azurik_plugin"
```

The entry-point name (`my_cool_mod`) is informational — the
scanner groups discovered plugins by it.  The entry-point value
(`my_azurik_plugin`) is the dotted module path to import; import
side effects register the feature.

### `my_azurik_plugin/__init__.py`

```python
"""My cool Azurik mod — does something interesting."""

from azurik_mod.patching.registry import Feature, register_feature


def _apply(xbe_data: bytearray, **_kwargs) -> None:
    """Apply whatever your mod does."""
    # e.g. patch a byte, null a resource key, etc.
    pass


register_feature(Feature(
    name="my_cool_mod",
    description="My cool Azurik mod.",
    sites=[],
    apply=_apply,
    category="experimental",      # auto-creates if new
    default_on=False,
))
```

That's it.  After `pip install .` your feature shows up:

```bash
$ azurik-mod plugins list
1 plugin(s) discovered under entry-point group 'azurik_mod.patches':

  [OK]  my_cool_mod  (my-azurik-plugin 0.1.0)  → my_azurik_plugin
```

And in the GUI's Patches tab, under whichever `category` you
picked.

## What you can do in a plugin

Anything `register_feature` accepts:

- **Byte patches**: pass `sites=[PatchSpec(...), ...]`
- **Shim-backed trampolines**: pass `sites=[TrampolinePatch(...)]`
  + `shim=ShimSource(folder=Path(__file__).parent)`
- **Parametric sliders**: pass `ParametricPatch(...)` sites;
  the GUI auto-renders sliders
- **Custom apply logic**: `custom_apply=my_callable` bypasses
  the generic dispatcher
- **New categories**: set `category="my_category_id"` — the
  registry auto-creates it (title case-humanised).  For a nicer
  title / description / order, call
  `register_category(Category(...))` *before* the
  `register_feature` call.

See `azurik_mod/patches/<any>/__init__.py` for real examples:

- `enable_dev_menu/` — simplest byte-patch feature
- `qol_skip_logo/` — shim-backed trampoline
- `player_physics/` — parametric sliders + custom apply
- `randomize/` — category-only features (no XBE edits)

## Registering new categories

```python
from azurik_mod.patching.category import Category, register_category

register_category(Category(
    id="cheats",
    title="Cheats",
    description="Opt-in cheat-type mods.",
    order=60,        # between randomize (50) and experimental (80)
))
```

Call BEFORE your `register_feature(category="cheats", ...)`.
The GUI will grow a dedicated "Cheats" tab.

## Safety + trust

**Plugins run with full Python privileges.**  Only install
plugins you trust — exactly as with any PyPI package.

The loader sandboxes individual plugin failures: if your
plugin raises during import, `azurik-mod` logs the error and
continues with the remaining features instead of crashing.

Set `AZURIK_NO_PLUGINS=1` in the environment to skip plugin
discovery entirely — useful for CI runs or when diagnosing
an issue that might be plugin-caused.

## Distribution

Local dev:

```bash
pip install -e /path/to/my-azurik-plugin
```

PyPI:

```bash
# (once your package is on PyPI)
pip install my-azurik-plugin
```

GitHub direct:

```bash
pip install git+https://github.com/you/my-azurik-plugin.git
```

## Validating

After install, confirm the plugin registers cleanly:

```bash
azurik-mod plugins list                   # did it get discovered?
azurik-mod list  --mod my_cool_mod        # does the feature show up?
azurik-mod patch --iso Azurik.iso --mod \
    '{"my_cool_mod": true}' -o out.iso    # does it actually apply?
```

If `plugins list` shows `[ERROR]` for your plugin, the full
traceback prints beneath the summary line; fix the import /
registration error and re-run.

## See also

- [docs/PATCHES.md](PATCHES.md) — the pack catalog + feature-
  declaration surface.
- [docs/SHIMS.md](SHIMS.md) — the shim pipeline + `TrampolinePatch`
  anatomy.
- [docs/SHIM_AUTHORING.md](SHIM_AUTHORING.md) — full 8-step
  workflow with common pitfalls.
