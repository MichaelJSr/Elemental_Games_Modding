"""Reverse-engineering session recorder.

Tool #11 on the roadmap.  Wraps a :class:`GhidraClient` with a
logging proxy that journals every request/response pair to a
Markdown file, producing an auditable session transcript.

## Why

"What did we figure out last month?" turns into transcript-log
archaeology.  When working in an agent-driven RE loop we want a
concise, grep-able log of each session's findings — not the
thousand-line raw chat transcript, but the essential Ghidra
queries + the conclusions they supported.

## Design

:class:`RecordingGhidraClient` is a drop-in replacement for
:class:`GhidraClient` that delegates every method call, records
it to a :class:`SessionLog`, and returns the original result.
Tests swap in ``RecordingGhidraClient(inner=MockGhidraServer-based-client)``
to verify the right calls are logged.

:class:`SessionLog` formats entries as a Markdown document.
Each call gets:

- Timestamp (UTC) + monotonic ms since session start
- Method name + positional args (hex-formatted for addresses)
- Summary of the response (typed dataclass → short repr)
- Any exception raised

Add free-text notes via :meth:`SessionLog.note` — the recorder
inserts them in-line with the call stream so context comments
stay near the relevant queries.

## Output

Journals live at ``docs/re-sessions/<YYYYMMDD-HHMMSS>.md`` by
default.  Override with ``log_path=`` on ``SessionLog(...)``.

## Concurrency

The recorder is single-threaded on purpose — an RE session is a
human-driven linear process.  Multiple parallel agents should
each use their own :class:`SessionLog`.
"""

from __future__ import annotations

import datetime
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .ghidra_client import (
    GhidraClient,
    GhidraClientError,
    GhidraFunction,
    GhidraLabel,
    GhidraProgramInfo,
)


__all__ = [
    "LogEntry",
    "RecordingGhidraClient",
    "SessionLog",
]


# ---------------------------------------------------------------------------
# Session log
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LogEntry:
    """One recorded event.  ``kind`` is either ``call`` (Ghidra
    query + result) or ``note`` (free-text annotation)."""

    kind: str
    ts_utc: str
    ms_since_start: int
    title: str
    body: str


@dataclass
class SessionLog:
    """Accumulates events + renders them as Markdown.

    Construct with an optional ``log_path`` — when set, every
    event is appended to the file immediately (so a crash
    mid-session still leaves a usable trail).  ``log_path=None``
    keeps the log in memory only; call :meth:`write` yourself.
    """

    log_path: Path | None = None
    title: str = "Azurik RE session"
    _start_ms: float = field(default_factory=lambda: time.monotonic() * 1000,
                             init=False)
    entries: list[LogEntry] = field(default_factory=list, init=False)

    # ------------------------------------------------------------------

    def __post_init__(self) -> None:
        if self.log_path is not None:
            self.log_path = Path(self.log_path).expanduser()
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            header = self._format_header()
            self.log_path.write_text(header, encoding="utf-8")

    def _format_header(self) -> str:
        started = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        return (f"# {self.title}\n\n"
                f"Started: {started}\n\n"
                f"---\n\n")

    def _render_entry(self, entry: LogEntry) -> str:
        if entry.kind == "note":
            return f"> **[note +{entry.ms_since_start} ms]**  {entry.title}\n\n"
        return (
            f"### +{entry.ms_since_start} ms  {entry.title}\n"
            f"<small>{entry.ts_utc}</small>\n\n"
            f"```\n{entry.body}\n```\n\n")

    # ------------------------------------------------------------------

    def record(self, kind: str, title: str, body: str) -> LogEntry:
        """Append one event.  Autoflushes when ``log_path`` is set."""
        now = datetime.datetime.utcnow().isoformat(
            timespec="milliseconds") + "Z"
        entry = LogEntry(
            kind=kind,
            ts_utc=now,
            ms_since_start=int(time.monotonic() * 1000 - self._start_ms),
            title=title,
            body=body,
        )
        self.entries.append(entry)
        if self.log_path is not None:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(self._render_entry(entry))
        return entry

    def note(self, text: str) -> LogEntry:
        """Attach a free-text annotation at the current point in
        the call stream."""
        return self.record("note", text, "")

    def write(self, path: str | Path | None = None) -> Path:
        """Flush the full log to ``path`` (or ``self.log_path``).

        Returns the resolved :class:`Path`.  Useful for
        in-memory-only sessions that decide to persist at the end
        + for testing the full rendered output in one go.
        """
        target = Path(path) if path is not None else self.log_path
        if target is None:
            raise ValueError(
                "write() needs a path (either pass one or set "
                "SessionLog.log_path at construction)")
        target = Path(target).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        contents = [self._format_header()]
        for entry in self.entries:
            contents.append(self._render_entry(entry))
        target.write_text("".join(contents), encoding="utf-8")
        return target

    def render(self) -> str:
        """Return the full Markdown as a string — handy for tests."""
        return self._format_header() + "".join(
            self._render_entry(e) for e in self.entries)


# ---------------------------------------------------------------------------
# Recording proxy
# ---------------------------------------------------------------------------


class RecordingGhidraClient:
    """Wraps a :class:`GhidraClient` and journals every call.

    ``inner`` is any object implementing the same method surface
    (the real client, or a mock used in tests).  We don't
    subclass :class:`GhidraClient` directly because tests wire
    their own transports.
    """

    def __init__(self, inner: GhidraClient, *, log: SessionLog,
                 ) -> None:
        self._inner = inner
        self._log = log

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    @property
    def inner(self) -> GhidraClient:
        return self._inner

    @property
    def log(self) -> SessionLog:
        return self._log

    # ------------------------------------------------------------------
    # Pass-through attributes not covered below
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    # ------------------------------------------------------------------
    # Recorded methods
    # ------------------------------------------------------------------

    def _log_call(self, title: str, *,
                  error: Exception | None = None,
                  result: Any = None) -> None:
        if error is not None:
            body = (f"ERROR: {type(error).__name__}\n"
                    f"  message: {error}")
        else:
            body = self._describe_result(result)
        self._log.record("call", title, body)

    def _describe_result(self, result: Any) -> str:
        if isinstance(result, GhidraProgramInfo):
            return (f"program '{result.name}'  "
                    f"base=0x{result.image_base:08X}  "
                    f"size={result.memory_size:,}")
        if isinstance(result, GhidraFunction):
            return (f"0x{result.address:08X}  name={result.name!r}  "
                    f"sig={result.signature!r}")
        if isinstance(result, GhidraLabel):
            return (f"{result.address}  name={result.name!r}  "
                    f"type={result.symbol_type!r}")
        if isinstance(result, bool):
            return "true" if result else "false"
        if isinstance(result, (int, float, str)):
            return str(result)
        if result is None:
            return "(no return value)"
        # Fall back to JSON for dicts / other primitives.
        try:
            return json.dumps(result, default=str, indent=2)
        except TypeError:
            return repr(result)

    def ping(self) -> bool:
        try:
            result = self._inner.ping()
            self._log_call("ping", result=result)
            return result
        except Exception as exc:
            self._log_call("ping", error=exc)
            raise

    def program_info(self) -> GhidraProgramInfo:
        try:
            result = self._inner.program_info()
            self._log_call("program_info", result=result)
            return result
        except Exception as exc:
            self._log_call("program_info", error=exc)
            raise

    def get_function(self, address: int) -> GhidraFunction:
        title = f"get_function(0x{address:08X})"
        try:
            result = self._inner.get_function(address)
            self._log_call(title, result=result)
            return result
        except Exception as exc:
            self._log_call(title, error=exc)
            raise

    def rename_function(self, address: int, new_name: str) -> dict:
        title = f"rename_function(0x{address:08X}, {new_name!r})"
        try:
            result = self._inner.rename_function(address, new_name)
            self._log_call(title, result=result)
            return result
        except Exception as exc:
            self._log_call(title, error=exc)
            raise

    def set_function_signature(self, address: int,
                               signature: str) -> dict:
        title = (f"set_function_signature(0x{address:08X}, "
                 f"{signature!r})")
        try:
            result = self._inner.set_function_signature(
                address, signature)
            self._log_call(title, result=result)
            return result
        except Exception as exc:
            self._log_call(title, error=exc)
            raise

    def set_comment(self, address: int, comment: str, *,
                    kind: str = "plate") -> dict:
        title = (f"set_comment(0x{address:08X}, kind={kind!r}, "
                 f"len={len(comment)})")
        try:
            result = self._inner.set_comment(address, comment,
                                             kind=kind)
            self._log_call(title, result=result)
            return result
        except Exception as exc:
            self._log_call(title, error=exc)
            raise

    def iter_functions(self, *, page_size: int = 500
                       ) -> Iterator[GhidraFunction]:
        """Page through every function + record the total count."""
        count = 0
        try:
            for fn in self._inner.iter_functions(page_size=page_size):
                count += 1
                yield fn
        finally:
            self._log.record("call",
                             "iter_functions (pagination)",
                             f"total yielded: {count}")

    def iter_labels(self, *, page_size: int = 500
                    ) -> Iterator[GhidraLabel]:
        count = 0
        try:
            for lbl in self._inner.iter_labels(page_size=page_size):
                count += 1
                yield lbl
        finally:
            self._log.record("call",
                             "iter_labels (pagination)",
                             f"total yielded: {count}")


# ---------------------------------------------------------------------------
# Context manager convenience
# ---------------------------------------------------------------------------


@contextmanager
def recording_session(inner: GhidraClient, *,
                      log_path: str | Path | None = None,
                      title: str = "Azurik RE session"
                      ) -> Iterator[RecordingGhidraClient]:
    """Scope-bound recorder.  Opens a :class:`SessionLog`, yields
    a :class:`RecordingGhidraClient`, and closes / flushes the
    log when the block exits (even on exceptions)."""
    log = SessionLog(log_path=Path(log_path) if log_path else None,
                     title=title)
    recorder = RecordingGhidraClient(inner, log=log)
    try:
        yield recorder
    finally:
        if log_path is None:
            # In-memory only — emit a footer note so
            # recorder.log.entries has the closing marker.
            log.note("session end")
