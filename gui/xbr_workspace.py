"""Workspace management for the XBR Editor.

The XBR Editor needs a set of ``.xbr`` files on disk to work
against.  Getting them there used to require manually extracting
the ISO and pointing the editor at it — every session, from a file
dialog.  The :class:`XbrWorkspace` class in this module replaces
that with a persistent, auto-managed workspace directory.

Layout under ``<repo>/.xbr_workspace/``::

    game/
      gamedata/
        config.xbr
        a1.xbr
        ...
        index/
          index.xbr
      default.xbe       (extracted alongside the data files)
    pending_edits.json  (edits staged across sessions)
    session.json        (last opened file / section for restore)

The ``game/`` subtree is either:

1. **Extracted** via xdvdfs from the project's
   :func:`gui.backend.find_base_iso()` (slow: ~2-10s depending on
   host; cached so this only runs when the ISO changes).
2. **Imported** as a symlink or direct pointer to an already-
   extracted ISO directory (``*.xiso/`` folder).  Detected via
   sibling-of-iso + environment-variable heuristics so the user
   doesn't have to configure anything when they already have an
   extracted copy lying around.

The workspace is gitignored (see ``.gitignore``); nothing inside
is source content, only user-derived data + per-user state.
"""

from __future__ import annotations

import json
import os
import shutil
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent


# Legacy section names that existed in pending_edits.json before
# the "armor_hit_fx" / "armor_properties" rename.  Keys here are
# the old names, values are the new canonical names.  Used by
# :meth:`XbrWorkspace.load_pending_edits` to migrate persisted
# state on load so users who saved edits under the old names
# don't lose their work.  See docs/LEARNINGS.md §
# "armor_hit_fx vs armor_properties" for why the rename happened.
_LEGACY_SECTION_NAME_MAP: dict[str, str] = {
    "armor_hit_fx":     "armor_properties_real",
    "armor_properties": "armor_properties_unused",
}

WORKSPACE_DIR = REPO_ROOT / ".xbr_workspace"
"""Absolute path to the per-user workspace root.  Kept inside the
repo so it travels with the install, but gitignored so it never
reaches version control."""

GAME_SUBDIR = "game"
"""Subdirectory of :data:`WORKSPACE_DIR` holding the extracted ISO
contents — ``gamedata/``, ``default.xbe``, etc."""

PENDING_EDITS_FILE = "pending_edits.json"
"""File inside :data:`WORKSPACE_DIR` that carries pending
:class:`XbrEditSpec`-shaped edits between editor sessions.  The
format is JSON-serialisable (hex-encoded bytes for non-textual
payloads)."""

SESSION_FILE = "session.json"
"""File inside :data:`WORKSPACE_DIR` holding a tiny "last open"
state so the editor can restore the user's previous view."""


# ---------------------------------------------------------------------------
# Small data carriers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class XbrFileInfo:
    """One discovered ``.xbr`` file in the workspace.

    Attributes:
        filename:       Basename (``"config.xbr"``, ``"a1.xbr"``, …).
        relative_path:  Path relative to the ``gamedata/`` root
                        — includes subdirs like ``"index/index.xbr"``.
        absolute_path:  Resolved path on disk for loading.
        kind:           Human-friendly category (``"config"``,
                        ``"level"``, ``"index"``, ``"data"``).
        size_bytes:     File size (for sorting / display).
    """

    filename: str
    relative_path: str
    absolute_path: Path
    kind: str
    size_bytes: int


@dataclass
class SessionState:
    """Persisted "last view" state so re-opening the editor lands
    the user where they were.

    Everything here is optional — a missing / malformed session
    file just yields a blank state.
    """

    last_file: Optional[str] = None
    """Filename (basename, not full path) of the .xbr last viewed."""

    last_section_index: Optional[int] = None
    """TOC index last selected inside ``last_file``."""

    last_entity: Optional[str] = None
    """Entity (column) last highlighted inside the selected section.
    Used by the grid view to scroll the right row into view."""

    last_property: Optional[str] = None
    """Property (row) last highlighted."""


# ---------------------------------------------------------------------------
# Kind classification — presentation hint for the Files tree.
# ---------------------------------------------------------------------------


_KIND_HINTS: tuple[tuple[str, str], ...] = (
    ("config.xbr", "config"),
    ("index/index.xbr", "index"),
    ("hourglass.xbr", "data"),
    ("characters.xbr", "data"),
    ("fx.xbr", "data"),
    ("interface.xbr", "data"),
    ("english.xbr", "data"),
    ("selector.xbr", "level"),
    ("airship_docking.xbr", "level"),
    ("airship_trans.xbr", "level"),
    ("training_room.xbr", "level"),
    ("life.xbr", "data"),
    ("loc.xbr", "data"),
)


def _classify(relpath: str) -> str:
    """Return a :class:`XbrFileInfo.kind` for ``relpath``.

    Rules, in order:

    1. Exact match in the explicit table.
    2. ``diskreplace_*`` / ``diskreplchars.xbr`` → ``"level"``
       (per-element level bundles).
    3. Short 2-3 char stem like ``a1.xbr`` / ``f3.xbr`` → ``"level"``.
    4. Anything else → ``"data"``.
    """
    for name, kind in _KIND_HINTS:
        if relpath == name:
            return kind
    stem = Path(relpath).stem
    lower = stem.lower()
    if lower.startswith("diskreplace_") or lower == "diskreplchars":
        return "level"
    if len(stem) <= 3 and stem[:1].isalpha():
        return "level"
    return "data"


# ---------------------------------------------------------------------------
# XbrWorkspace
# ---------------------------------------------------------------------------


class XbrWorkspace:
    """Per-user state for the XBR Editor.

    Use:
        ws = XbrWorkspace.default()
        ws.ensure_game_files(iso_path)  # one-time extract
        for info in ws.discover_xbr_files():
            ...
        ws.save_session(SessionState(last_file="config.xbr"))
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.game_dir = self.root / GAME_SUBDIR
        self.gamedata_dir = self.game_dir / "gamedata"

    @classmethod
    def default(cls) -> "XbrWorkspace":
        """Return the workspace rooted under the repo's
        :data:`WORKSPACE_DIR`.  Creates the directory if missing."""
        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        return cls(WORKSPACE_DIR)

    # ------------------------------------------------------------------
    # Game-file provisioning
    # ------------------------------------------------------------------

    def has_game_files(self) -> bool:
        """True iff ``gamedata/`` is populated with at least
        ``config.xbr``.  Used as a gate for the extract step."""
        return (self.gamedata_dir / "config.xbr").exists()

    def ensure_game_files(
        self,
        iso_path: Optional[Path] = None,
        *,
        allow_network: bool = True,
    ) -> bool:
        """Populate :attr:`game_dir` if it's empty.

        Tries, in order:

        1. An already-populated :attr:`gamedata_dir` (no-op).
        2. Importing from a sibling-of-ISO extract directory
           (``<iso_parent>/<stem>.xiso/`` or similar — matches the
           convention xdvdfs uses when the user extracts manually).
        3. ``$AZURIK_GAMEDATA`` environment override pointing at an
           existing extract.
        4. Running xdvdfs against ``iso_path`` to produce a fresh
           extract.

        Returns ``True`` if :meth:`has_game_files` is now true,
        ``False`` when none of the options worked.  Every failure
        mode prints a single explanatory line so the GUI can
        surface it.

        ``allow_network=False`` skips the xdvdfs auto-download
        hook inside :func:`gui.backend.find_xdvdfs` — useful for
        tests that must not hit the network.
        """
        if self.has_game_files():
            return True

        # Option 2: sibling-of-iso extract directory.
        if iso_path is not None:
            for sibling in _candidate_extract_siblings(iso_path):
                if (sibling / "gamedata" / "config.xbr").exists():
                    self._import_from(sibling)
                    return True

        # Option 3: env-var override.
        env = os.environ.get("AZURIK_GAMEDATA")
        if env:
            src = Path(env)
            # Accept both "<extract>/gamedata" and "<extract>" —
            # in the first case the parent is the extract root.
            if src.name == "gamedata" and (src / "config.xbr").exists():
                src = src.parent
            if (src / "gamedata" / "config.xbr").exists():
                self._import_from(src)
                return True

        # Option 4: fresh xdvdfs extract.
        if iso_path is not None and iso_path.exists():
            from gui.backend import find_xdvdfs
            xdvdfs = find_xdvdfs() if allow_network else None
            if not xdvdfs:
                # Try WITHOUT the auto-download hook first; if still
                # nothing, bail.
                if allow_network:
                    print("xbr workspace: xdvdfs not found; cannot "
                          "auto-extract ISO.")
                return False
            if self.game_dir.exists():
                shutil.rmtree(self.game_dir)
            try:
                import subprocess
                subprocess.check_call(
                    [xdvdfs, "unpack",
                     str(iso_path), str(self.game_dir)])
            except (subprocess.CalledProcessError, OSError) as exc:
                print(f"xbr workspace: xdvdfs unpack failed: {exc}")
                return False
            return self.has_game_files()

        return False

    def _import_from(self, extract_dir: Path) -> None:
        """Copy (not symlink — portability) the gamedata + default.xbe
        from ``extract_dir`` into the workspace.  The source is
        expected to be a read-only ISO extract; copying gives us a
        mutation-safe working copy."""
        if self.game_dir.exists():
            shutil.rmtree(self.game_dir)
        self.game_dir.mkdir(parents=True, exist_ok=True)
        src_gamedata = extract_dir / "gamedata"
        dst_gamedata = self.game_dir / "gamedata"
        shutil.copytree(src_gamedata, dst_gamedata)
        # default.xbe is handy for a few build-adjacent flows; copy
        # if present so the workspace is fully self-contained.
        xbe = extract_dir / "default.xbe"
        if xbe.exists():
            shutil.copy2(xbe, self.game_dir / "default.xbe")

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover_xbr_files(self) -> list[XbrFileInfo]:
        """Walk :attr:`gamedata_dir` and return every ``.xbr``
        file as an :class:`XbrFileInfo`.

        Sort order: kind (config → level → data → index → other)
        then filename.  Picks up nested paths like ``index/index.xbr``.
        """
        if not self.gamedata_dir.exists():
            return []
        out: list[XbrFileInfo] = []
        for p in sorted(self.gamedata_dir.rglob("*.xbr")):
            rel = p.relative_to(self.gamedata_dir)
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            out.append(XbrFileInfo(
                filename=p.name,
                relative_path=str(rel).replace("\\", "/"),
                absolute_path=p,
                kind=_classify(str(rel).replace("\\", "/")),
                size_bytes=size,
            ))
        order_map = {"config": 0, "index": 1, "data": 2, "level": 3}
        out.sort(key=lambda i: (order_map.get(i.kind, 9), i.filename))
        return out

    # ------------------------------------------------------------------
    # Pending-edits persistence
    # ------------------------------------------------------------------

    @property
    def pending_path(self) -> Path:
        return self.root / PENDING_EDITS_FILE

    def load_pending_edits(self) -> list[dict]:
        """Load the persisted pending-edits list.  Returns an empty
        list when the file is missing or malformed.

        Also migrates legacy section names stored before the
        ``armor_hit_fx`` / ``armor_properties`` rename (see
        :data:`_LEGACY_SECTION_NAME_MAP`).  Migrated edits are
        rewritten in-place and a single :mod:`warnings` hint is
        emitted per call so the user sees what happened, but no
        prompting or disk IO is required — the migration is applied
        lazily every time the file is loaded.
        """
        if not self.pending_path.exists():
            return []
        try:
            payload = json.loads(self.pending_path.read_text("utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(payload, dict):
            return []
        edits = payload.get("xbr_edits")
        if not isinstance(edits, list):
            return []
        out = [e for e in edits if isinstance(e, dict)]
        migrated = 0
        for edit in out:
            old = edit.get("section")
            if not isinstance(old, str):
                continue
            new = _LEGACY_SECTION_NAME_MAP.get(old)
            if new is None:
                continue
            edit["section"] = new
            migrated += 1
        if migrated:
            warnings.warn(
                f"Migrated {migrated} pending XBR edit(s) from legacy "
                f"section names ({', '.join(sorted(_LEGACY_SECTION_NAME_MAP))}) "
                f"to their renamed counterparts.  See docs/LEARNINGS.md "
                f"for why the rename happened.  Save the workspace (or "
                f"apply any edit) to persist the migration.",
                stacklevel=2,
            )
        return out

    def save_pending_edits(self, edits: list[dict]) -> None:
        """Persist the edit list.  Overwrites any previous file.

        An empty list deletes the file so the workspace directory
        stays tidy when the user reverts every edit."""
        self.root.mkdir(parents=True, exist_ok=True)
        if not edits:
            if self.pending_path.exists():
                try:
                    self.pending_path.unlink()
                except OSError:
                    pass
            return
        payload = {"xbr_edits": edits}
        tmp = self.pending_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=False),
            encoding="utf-8")
        tmp.replace(self.pending_path)

    # ------------------------------------------------------------------
    # Session state
    # ------------------------------------------------------------------

    @property
    def session_path(self) -> Path:
        return self.root / SESSION_FILE

    def load_session(self) -> SessionState:
        if not self.session_path.exists():
            return SessionState()
        try:
            payload = json.loads(
                self.session_path.read_text("utf-8"))
        except (OSError, ValueError):
            return SessionState()
        if not isinstance(payload, dict):
            return SessionState()
        return SessionState(
            last_file=payload.get("last_file"),
            last_section_index=payload.get("last_section_index"),
            last_entity=payload.get("last_entity"),
            last_property=payload.get("last_property"),
        )

    def save_session(self, state: SessionState) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.session_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(asdict(state), indent=2), encoding="utf-8")
        tmp.replace(self.session_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candidate_extract_siblings(iso_path: Path) -> list[Path]:
    """Return plausible sibling directories where a user might
    have manually extracted the ISO.

    Users commonly name the extract ``<stem>.xiso`` or
    ``<stem>_extract`` alongside the ``.iso`` file.  We search a
    few ancestor directories because real-world layouts nest the
    ISO inside ``<repo>/iso/`` while the extract sits alongside
    the repo at ``<repo>/../<stem>.xiso/``.
    """
    candidates: list[Path] = []
    iso_path = iso_path.resolve()
    stem = iso_path.stem
    if stem.endswith(".xiso"):
        # ``Azurik - Rise of Perathia (USA).xiso.iso`` → strip the
        # trailing ``.xiso`` to get a friendlier match.
        alt_stem = stem[: -len(".xiso")]
    else:
        alt_stem = stem
    # Walk up to three ancestor levels so layouts like
    # ``tools/repo/iso/foo.iso`` with extract at ``tools/foo.xiso/``
    # are covered.
    ancestors: list[Path] = []
    seen_anchors: set[Path] = set()
    cur = iso_path.parent
    for _ in range(4):
        if cur in seen_anchors:
            break
        seen_anchors.add(cur)
        ancestors.append(cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    for anchor in ancestors:
        for name in (
                f"{stem}",
                f"{stem}.xiso",
                f"{alt_stem}.xiso",
                f"{alt_stem}_extract",
                f"{stem}_extract",
                alt_stem,
        ):
            candidate = anchor / name
            if candidate.is_dir():
                candidates.append(candidate)
    # Dedupe while preserving order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        unique.append(c)
    return unique


__all__ = [
    "GAME_SUBDIR",
    "PENDING_EDITS_FILE",
    "SESSION_FILE",
    "SessionState",
    "WORKSPACE_DIR",
    "XbrFileInfo",
    "XbrWorkspace",
]
