"""Plugin pack distribution — discover third-party features via
``importlib.metadata`` entry points.

Tool #16 on the roadmap.  Lets external packages ship patch /
shim / randomizer features that auto-register with
:mod:`azurik_mod.patching.registry` without upstream PRs.

## The contract

A plugin is any PyPI-installable package that declares an entry
point under the group ``azurik_mod.patches``.  The entry-point
target must be an **importable module** — importing it runs its
``register_feature(...)`` side effects, same as for shipped
feature folders.

Example ``pyproject.toml`` in a third-party plugin::

    [project.entry-points."azurik_mod.patches"]
    my_cool_mod = "my_cool_mod.feature"

And inside ``my_cool_mod/feature.py``::

    from azurik_mod.patching.registry import Feature, register_feature
    register_feature(Feature(
        name="my_cool_mod",
        description="A cool thing.",
        sites=[],
        apply=lambda xbe, **kw: None,
        category="experimental",
    ))

Install with ``pip install .`` (for local dev) or ``pip install
my-cool-mod`` (from PyPI).  After install, ``azurik-mod`` picks
up the plugin automatically.

## Safety model

- Plugin loading happens inside a ``try/except`` so a broken
  plugin can't crash the CLI; the error is logged and the
  plugin is skipped.
- Each plugin loads into the SAME global registry as shipped
  packs.  Name collisions raise via
  :func:`register_pack` — same guarantee shipped packs already
  provide.
- A category the plugin picks that doesn't exist is auto-
  created via :func:`ensure_category` (inherits order=1000).
- Plugins can't touch the XBE / ISO directly during loading —
  ``register_feature`` is the only public API we expose.  A
  plugin that tries to do more gets isolated by the try/except
  but otherwise the code runs with full Python privileges.
  **Users should only install plugins they trust**, exactly
  as with any PyPI package.

## Entry-point group

We use ``azurik_mod.patches`` (dotted, plural) — the group name
mirrors the import path so it's obvious what the entry point
does.  Some other projects use ``azurik.plugins`` conventions;
we stick with ``azurik_mod.patches`` for forward compatibility
with the existing ``azurik_mod.patches.*`` namespace.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import traceback
from dataclasses import dataclass, field


__all__ = [
    "DiscoveredPlugin",
    "PluginLoadReport",
    "discover_plugins",
    "load_plugins",
]


ENTRY_POINT_GROUP = "azurik_mod.patches"


@dataclass(frozen=True)
class DiscoveredPlugin:
    """One entry-point point the plugin loader found."""

    name: str                 # entry-point name (LHS in pyproject)
    target: str               # entry-point value (RHS)
    distribution: str         # package that owns the entry point
    distribution_version: str


@dataclass
class PluginLoadReport:
    """Result of :func:`load_plugins`."""

    discovered: list[DiscoveredPlugin] = field(default_factory=list)
    loaded: list[DiscoveredPlugin] = field(default_factory=list)
    errors: list[tuple[DiscoveredPlugin, str]] = field(
        default_factory=list)

    @property
    def success(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "discovered": [{
                "name": p.name,
                "target": p.target,
                "distribution": p.distribution,
                "distribution_version": p.distribution_version,
            } for p in self.discovered],
            "loaded": [p.name for p in self.loaded],
            "errors": [{
                "name": p.name,
                "target": p.target,
                "error": err,
            } for p, err in self.errors],
        }


def _iter_entry_points() -> "list[importlib.metadata.EntryPoint]":
    """Yield every entry point in our group.

    Wraps the Python 3.8 vs 3.10+ API split: ``entry_points()``
    returned a dict-like in 3.8/3.9 and an :class:`EntryPoints`
    collection in 3.10+.  We support both.
    """
    try:
        eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    except TypeError:
        # Older Python — returns the full dict.
        eps = importlib.metadata.entry_points().get(
            ENTRY_POINT_GROUP, [])
    return list(eps)


def discover_plugins() -> list[DiscoveredPlugin]:
    """Return every plugin entry point without importing any.

    Safe to call from diagnostic tools — no plugin side effects
    fire.
    """
    out: list[DiscoveredPlugin] = []
    for ep in _iter_entry_points():
        dist = getattr(ep, "dist", None)
        if dist is not None:
            dist_name = getattr(dist, "name", "") or ""
            dist_ver = getattr(dist, "version", "") or ""
        else:
            dist_name = ""
            dist_ver = ""
        out.append(DiscoveredPlugin(
            name=ep.name,
            target=ep.value,
            distribution=dist_name,
            distribution_version=dist_ver,
        ))
    return out


def load_plugins(*, raise_on_error: bool = False) -> PluginLoadReport:
    """Import every registered plugin module, isolating errors.

    Calls :func:`importlib.import_module` on each entry-point
    target.  The import itself is expected to call
    :func:`register_feature` as a side effect; we never look at
    the module's return value.

    ``raise_on_error=True`` re-raises the first exception
    encountered (useful in tests + development).  Default
    ``False`` just logs + continues so one broken plugin can't
    take down ``azurik-mod``.
    """
    report = PluginLoadReport()
    report.discovered = discover_plugins()

    for plugin in report.discovered:
        try:
            ep_module = plugin.target.split(":")[0]
            importlib.import_module(ep_module)
            report.loaded.append(plugin)
        except Exception as exc:  # noqa: BLE001
            err = "".join(
                traceback.format_exception_only(type(exc), exc))
            report.errors.append((plugin, err.strip()))
            if raise_on_error:
                raise

    return report


def format_report(report: PluginLoadReport) -> str:
    """Human-readable summary of discovery + load results."""
    if not report.discovered:
        return ("No third-party plugins discovered.  To install a "
                "plugin:\n"
                "  pip install <plugin-name>\n"
                "Plugins must declare an entry point under the "
                f"{ENTRY_POINT_GROUP!r} group.  See docs/PLUGINS.md "
                "for the authoring guide.")

    lines = [
        f"{len(report.discovered)} plugin(s) discovered "
        f"under entry-point group {ENTRY_POINT_GROUP!r}:",
        "",
    ]
    for p in report.discovered:
        state = (
            "OK"
            if any(lp.name == p.name for lp in report.loaded)
            else "ERROR")
        lines.append(
            f"  [{state}]  {p.name}"
            + (f"  ({p.distribution} {p.distribution_version})"
               if p.distribution else "")
            + f"  → {p.target}")
    if report.errors:
        lines.append("")
        lines.append(f"Errors ({len(report.errors)}):")
        for p, err in report.errors:
            lines.append(f"  {p.name}: {err}")
    return "\n".join(lines)
