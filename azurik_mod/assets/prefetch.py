"""Parse Azurik's ``prefetch-lists.txt`` into a typed manifest.

The file format is a flat INI-ish stanza layout:

    tag=always
    file=index\\index.xbr
    file=hourglass.xbr
    file=%LANGUAGE%.xbr
    file=interface.xbr
    file=config.xbr
    file=fx.xbr
    file=characters.xbr

    tag=a1
    file=A1.xbr
    neighbor=a6
    neighbor=e6

    tag=a6-extra
    file=diskreplace_air.xbr
    file=diskreplchars.xbr

Three stanza shapes exist:

1. **always** — global resources the game keeps resident regardless
   of which level is active (``config.xbr``, ``fx.xbr``, …).
2. **<level>** — per-zone pack.  ``file=`` lines name the XBRs to
   load when entering the zone; ``neighbor=`` lines name the zones
   the streaming loader should also prefetch (so the player can
   cross a portal without a hard load).
3. **<level>-extra** — auxiliary content attached to a parent zone.
   Referenced by ``neighbor=<level>-extra`` from the parent
   stanza.  Contains the per-element ``diskreplace_*.xbr`` packs.

The CLI ``default.xbe`` translates ``%LANGUAGE%`` at runtime (to
``english``, ``french``, …).  This parser leaves placeholders intact
so callers can see the raw manifest; use
:py:meth:`PrefetchManifest.resolve_language` to substitute.

See docs/LEARNINGS.md § prefetch-lists.txt for the full corpus
analysis + how this module is used by the randomizer and the
xbr_parser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "PrefetchTag",
    "PrefetchManifest",
    "load_prefetch",
]


@dataclass(frozen=True)
class PrefetchTag:
    """One stanza from ``prefetch-lists.txt``.

    Attributes
    ----------
    name: str
        The tag name (``"always"``, ``"a1"``, ``"w3-extra"``, …).
    files: tuple[str, ...]
        XBR file paths to load (forward-slash normalised).  May
        contain unresolved placeholders like ``"%LANGUAGE%.xbr"``
        for the ``always`` stanza.
    neighbors: tuple[str, ...]
        Tags to also prefetch when this one activates.
    """

    name: str
    files: tuple[str, ...] = ()
    neighbors: tuple[str, ...] = ()

    @property
    def is_extra(self) -> bool:
        """``True`` for the ``*-extra`` pack stanzas."""
        return self.name.endswith("-extra")

    @property
    def is_always(self) -> bool:
        """``True`` for the global ``always`` stanza."""
        return self.name == "always"

    @property
    def is_alias(self) -> bool:
        """``True`` for build-system alias stanzas (``default``).

        ``tag=default`` in the vanilla manifest is a fallback
        entry that points at ``training_room.xbr``.  It is **not**
        a playable level in its own right — ``training_room`` has
        its own dedicated stanza with a real neighbor graph.
        """
        return self.name in _ALIAS_TAGS

    @property
    def is_level(self) -> bool:
        """``True`` if the tag names a playable/streamable level
        (i.e. not ``always``, not an extras pack, not an alias)."""
        return not (self.is_always or self.is_extra or self.is_alias)


@dataclass(frozen=True)
class PrefetchManifest:
    """Parsed ``prefetch-lists.txt``.

    Use :func:`load_prefetch` to construct from a file.

    The canonical vanilla manifest exposes:

    - 7 global (``always``) files
    - 24 levels (``a1``, ``a3``, ``a5``, ``a6``, ``airship``,
      ``airship_trans``, ``d1``, ``d2``, ``e2``, ``e5``, ``e6``,
      ``e7``, ``f1``, ``f2``, ``f3``, ``f4``, ``f6``, ``life``,
      ``town``, ``training_room``, ``w1``, ``w2``, ``w3``, ``w4``)
    - 5 ``-extra`` packs (one per element + the ``a6-extra``
      airship-docking bundle)

    Tests in ``tests/test_prefetch.py`` pin these counts against
    regressions.
    """

    tags: tuple[PrefetchTag, ...]

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def tag(self, name: str) -> PrefetchTag | None:
        for t in self.tags:
            if t.name == name:
                return t
        return None

    def level_tags(self) -> tuple[PrefetchTag, ...]:
        """All stanzas where :attr:`PrefetchTag.is_level` is true.

        Ordered as they appear in the file.
        """
        return tuple(t for t in self.tags if t.is_level)

    def level_names(self) -> tuple[str, ...]:
        """Short names of every level (``"a1"``, ``"w3"``, …)."""
        return tuple(t.name for t in self.level_tags())

    def extra_tags(self) -> tuple[PrefetchTag, ...]:
        """All ``*-extra`` stanzas."""
        return tuple(t for t in self.tags if t.is_extra)

    def global_files(self) -> tuple[str, ...]:
        """Files from the ``always`` stanza (global resources)."""
        t = self.tag("always")
        return t.files if t is not None else ()

    def all_referenced_files(self) -> tuple[str, ...]:
        """Every file path referenced by any stanza, order-preserved
        + deduplicated."""
        seen: dict[str, None] = {}
        for t in self.tags:
            for f in t.files:
                seen.setdefault(f, None)
        return tuple(seen.keys())

    # ------------------------------------------------------------------
    # Classification helpers (by filename basename)
    # ------------------------------------------------------------------

    def _basenames(self, files: tuple[str, ...]) -> set[str]:
        return {Path(p).name.lower() for p in files}

    def _global_basenames(self) -> set[str]:
        # ``%LANGUAGE%.xbr`` resolves to any language at runtime;
        # match any *.xbr that is declared ONLY in the ``always``
        # stanza.  Substitute the placeholder with all known
        # language tokens so callers don't need to know them.
        raw = self._basenames(self.global_files())
        resolved: set[str] = set()
        for name in raw:
            if "%language%" in name:
                for lang in _KNOWN_LANGUAGES:
                    resolved.add(name.replace("%language%", lang))
            else:
                resolved.add(name)
        return resolved

    def is_global_file(self, filename: str | Path) -> bool:
        """True if ``filename`` (basename, case-insensitive) is a
        global/always-resident resource."""
        return Path(filename).name.lower() in self._global_basenames()

    def is_level_file(self, filename: str | Path) -> bool:
        """True if ``filename`` belongs to a level or extras pack
        (i.e. referenced by ANY non-``always`` stanza).

        ``selector.xbr``, ``loc.xbr`` and any manifest-absent XBR
        return ``False`` — these are either auxiliary game-mode
        data or dev artefacts and should not be classified as
        regular level payload.
        """
        basename = Path(filename).name.lower()
        if basename in self._global_basenames():
            return False
        for t in self.tags:
            if t.is_always:
                continue
            if basename in self._basenames(t.files):
                return True
        return False

    # ------------------------------------------------------------------
    # Adjacency graph
    # ------------------------------------------------------------------

    def neighbors_of(self, level: str) -> tuple[str, ...]:
        """Direct neighbors (``neighbor=…`` entries) for ``level``.

        Includes ``-extra`` packs the level depends on.  An empty
        tuple is returned for unknown / leaf levels.
        """
        t = self.tag(level)
        return t.neighbors if t is not None else ()

    def playable_neighbors(self, level: str) -> tuple[str, ...]:
        """Neighbors that are themselves levels (excludes the
        ``*-extra`` packs)."""
        return tuple(n for n in self.neighbors_of(level)
                     if not n.endswith("-extra"))

    def adjacency(self) -> dict[str, tuple[str, ...]]:
        """Level → playable-neighbors graph as a plain dict.
        Handy for feeding a randomizer solver or graph viz."""
        return {t.name: self.playable_neighbors(t.name)
                for t in self.level_tags()}

    # ------------------------------------------------------------------
    # Placeholder resolution
    # ------------------------------------------------------------------

    def resolve_language(self, lang: str) -> "PrefetchManifest":
        """Return a new manifest with ``%LANGUAGE%`` replaced by
        ``lang`` in every file path.

        ``lang`` should be one of :data:`KNOWN_LANGUAGES` or any
        string the game recognises.  Match is case-insensitive on
        the placeholder but preserves ``lang`` verbatim.
        """
        new_tags = []
        for t in self.tags:
            new_files = tuple(_sub_language(f, lang) for f in t.files)
            new_tags.append(PrefetchTag(
                name=t.name, files=new_files, neighbors=t.neighbors))
        return PrefetchManifest(tags=tuple(new_tags))


# ---------------------------------------------------------------------------
# File loader
# ---------------------------------------------------------------------------

# Tag names that look like levels but are really build-system
# aliases / defaults.  Any tag in this set is excluded from
# :py:meth:`PrefetchManifest.level_tags`.
_ALIAS_TAGS = frozenset({"default"})

# Levels that appear as strings / references in the shipped ISO
# but have no corresponding stanza in ``prefetch-lists.txt`` and
# no XBR file on disk.  ``f7`` is referenced by ``f1``'s neighbor
# list; ``e4`` is referenced by ``selector.xbr``'s dev level-select
# hub.  See docs/LEARNINGS.md § selector.xbr for the provenance.
KNOWN_CUT_LEVELS = frozenset({"f7", "e4"})

# Languages we've seen Azurik ship (matches the %LANGUAGE%.xbr slot).
# Kept conservative; extend when / if localisations surface.
_KNOWN_LANGUAGES = ("english", "french", "german", "spanish", "italian",
                    "japanese")

#: Public copy of :data:`_KNOWN_LANGUAGES` for code that needs to list them.
KNOWN_LANGUAGES = _KNOWN_LANGUAGES


def _sub_language(s: str, lang: str) -> str:
    """Case-insensitive replacement of ``%LANGUAGE%`` in a path."""
    lower = s.lower()
    idx = lower.find("%language%")
    if idx < 0:
        return s
    return s[:idx] + lang + s[idx + len("%language%"):]


def _normalise_path(raw: str) -> str:
    """Canonicalise ``file=`` paths: forward slashes, trim quotes +
    whitespace.  Relative paths are preserved as-is."""
    return raw.strip().strip('"').replace("\\", "/")


def load_prefetch(path: str | Path) -> PrefetchManifest:
    """Parse a ``prefetch-lists.txt`` file into a
    :class:`PrefetchManifest`.

    The parser accepts ASCII + UTF-8 + UTF-16 (BOM-sniffed).  Blank
    lines and comments (leading ``#`` or ``;``) are ignored.  Each
    ``tag=…`` line opens a new stanza; ``file=`` and ``neighbor=``
    accumulate into the currently-open stanza.

    Raises
    ------
    FileNotFoundError
        ``path`` does not exist.
    ValueError
        A ``file=`` or ``neighbor=`` line appears before any
        ``tag=`` line, or the file contains no stanzas.
    """
    p = Path(path)
    raw = p.read_bytes()
    # BOM sniff — some tools save this file as UTF-16 LE.
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8", errors="replace")

    current_name: str | None = None
    current_files: list[str] = []
    current_neighbors: list[str] = []
    tags: list[PrefetchTag] = []

    def flush() -> None:
        nonlocal current_name, current_files, current_neighbors
        if current_name is None:
            return
        tags.append(PrefetchTag(
            name=current_name,
            files=tuple(current_files),
            neighbors=tuple(current_neighbors),
        ))
        current_name = None
        current_files = []
        current_neighbors = []

    for lineno, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            # Silently ignore malformed lines — the official file is
            # well-formed but downstream mods may append commentary.
            continue
        key = key.strip().lower()
        value = value.strip()

        if key == "tag":
            flush()
            current_name = value
        elif key == "file":
            if current_name is None:
                raise ValueError(
                    f"{p.name}:{lineno}: 'file=' before any 'tag='")
            current_files.append(_normalise_path(value))
        elif key == "neighbor":
            if current_name is None:
                raise ValueError(
                    f"{p.name}:{lineno}: 'neighbor=' before any 'tag='")
            current_neighbors.append(value)
        # Unknown keys silently ignored for forward-compat.

    flush()

    if not tags:
        raise ValueError(f"{p.name}: no stanzas found")

    return PrefetchManifest(tags=tuple(tags))
