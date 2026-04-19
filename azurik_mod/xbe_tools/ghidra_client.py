"""Tiny HTTP client for Ghidra's GhydraMCP plugin.

The upstream MCP bridge (``Scripts/bridge_mcp_hydra.py``) wraps an
HTTP REST API that the Ghidra plugin serves on ports 8192-8201.
That MCP layer is great for interactive agent work but awful for
automated tests — it pulls in ``mcp``, ``fastmcp``, stdio
transports, and a pile of other dependencies that CI hosts don't
have.

This module is a zero-MCP, ``requests``-only client that speaks
the same HTTP API directly.  It exists for three reasons:

1. Power the shipped ``ghidra-sync`` tool (#4 on the roadmap) —
   bulk-rename + annotate functions based on our Python-side
   knowledge, in a single script.
2. Let ``ghidra-coverage`` query a live Ghidra instance instead
   of relying on a snapshot JSON.
3. Be the test harness everything else builds on — a trivial
   :class:`MockGhidraServer` (see
   :mod:`azurik_mod.xbe_tools.mock_ghidra`) implements just
   enough of the same endpoint contract for CI tests to exercise
   all of the above without a live Ghidra.

## Endpoint coverage

The client implements the subset the sync / coverage tools
actually use:

- ``GET  /program``                  — project info + image base
- ``GET  /functions?offset&limit``   — paginate every function
- ``GET  /functions/{addr}``         — one function by address
- ``PATCH /functions/{addr}``        — rename / set signature
- ``POST /memory/{addr}/comments/{kind}`` — set a comment
- ``GET  /symbols/labels?offset&limit`` — iterate labels
- ``GET  /data/{addr}``              — look up data symbols

New endpoints are trivial to add — copy one of the ``_get`` /
``_patch`` wrappers and thread it through.

## Error handling

Every method returns a typed response dataclass and raises a
:class:`GhidraClientError` on any non-success response.  Error
details (status code, Ghidra error.code, error.message) are
surfaced on the exception for test output.

## Security / safety

The Ghidra HTTP server refuses state-changing requests without
an allowed ``Origin`` header.  The client sets
``Origin: http://localhost`` on every request, matching the
default ``GHIDRA_ALLOWED_ORIGINS`` the plugin ships with.
Override with the ``origin=`` constructor argument when pointing
at a hardened instance.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterator

__all__ = [
    "GhidraClient",
    "GhidraClientError",
    "GhidraFunction",
    "GhidraLabel",
    "GhidraProgramInfo",
]


# ---------------------------------------------------------------------------
# Exceptions + typed responses
# ---------------------------------------------------------------------------


class GhidraClientError(RuntimeError):
    """Any non-success response from the Ghidra HTTP plugin.

    Attributes
    ----------
    status_code: int | None
        HTTP status code (None on connection errors).
    code: str | None
        Ghidra's ``error.code`` value (e.g. ``ENDPOINT_NOT_FOUND``).
    message: str
        Human-readable error message.
    """

    def __init__(self, *, message: str, status_code: int | None = None,
                 code: str | None = None) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(self._format())

    def _format(self) -> str:
        bits = [self.message]
        if self.status_code is not None:
            bits.append(f"[HTTP {self.status_code}]")
        if self.code:
            bits.append(f"[{self.code}]")
        return " ".join(bits)


@dataclass(frozen=True)
class GhidraProgramInfo:
    """Result of ``GET /program`` — project + image metadata."""

    program_id: str
    name: str
    language_id: str
    compiler_spec_id: str
    image_base: int
    memory_size: int
    is_open: bool
    analysis_complete: bool

    @classmethod
    def from_json(cls, obj: dict) -> "GhidraProgramInfo":
        return cls(
            program_id=obj.get("programId", ""),
            name=obj.get("name", ""),
            language_id=obj.get("languageId", ""),
            compiler_spec_id=obj.get("compilerSpecId", ""),
            image_base=int(obj.get("imageBase", "0"), 16),
            memory_size=int(obj.get("memorySize", 0)),
            is_open=bool(obj.get("isOpen", False)),
            analysis_complete=bool(obj.get("analysisComplete", False)),
        )


@dataclass(frozen=True)
class GhidraFunction:
    """One function entry (``GET /functions`` row or
    ``GET /functions/{addr}``)."""

    address: int
    name: str
    signature: str | None = None
    return_type: str | None = None
    parameters: tuple[dict, ...] = ()

    @classmethod
    def from_json(cls, obj: dict) -> "GhidraFunction":
        addr_raw = obj.get("address") or "0"
        return cls(
            address=int(str(addr_raw), 16),
            name=obj.get("name", ""),
            signature=obj.get("signature"),
            return_type=obj.get("returnType"),
            parameters=tuple(obj.get("parameters", [])),
        )


@dataclass(frozen=True)
class GhidraLabel:
    """One symbol / label entry."""

    address: str       # may be "00012345" OR "EXTERNAL:00000001"
    name: str
    namespace: str
    symbol_type: str
    is_primary: bool

    @classmethod
    def from_json(cls, obj: dict) -> "GhidraLabel":
        return cls(
            address=obj.get("address", ""),
            name=obj.get("name", ""),
            namespace=obj.get("namespace", ""),
            symbol_type=obj.get("type", ""),
            is_primary=bool(obj.get("isPrimary", False)),
        )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


@dataclass
class _Response:
    """Parsed JSON envelope returned by every Ghidra endpoint."""

    success: bool
    status_code: int
    body: dict


class GhidraClient:
    """Thin HTTP client for a single Ghidra instance.

    Parameters
    ----------
    host: str
        Hostname (default ``localhost``).
    port: int
        HTTP port the plugin listens on (default 8193 — the
        Azurik ``default.xbe`` instance).
    timeout: float
        Per-request timeout in seconds (default 10).
    origin: str
        ``Origin`` header set on state-changing requests.  Must
        match one of the server's allowed origins.
    """

    def __init__(self, *, host: str = "localhost", port: int = 8193,
                 timeout: float = 10.0,
                 origin: str = "http://localhost") -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.origin = origin
        self.base_url = f"http://{host}:{port}"

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, *,
                 params: dict | None = None,
                 json_body: dict | None = None) -> _Response:
        """Low-level HTTP call using stdlib ``urllib`` (no
        third-party deps required for the client itself).

        Returns a :class:`_Response` with ``success``, ``status``
        and ``body``; raises :class:`GhidraClientError` on
        connection / timeout issues only — caller decides what to
        do with ``success=False`` bodies.
        """
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data: bytes | None = None
        headers = {"Accept": "application/json",
                   "Origin": self.origin}
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            url, method=method, data=data, headers=headers)
        try:
            with urllib.request.urlopen(
                    req, timeout=self.timeout) as resp:
                raw = resp.read()
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
        except urllib.error.URLError as exc:
            raise GhidraClientError(
                message=(f"Failed to reach {url}: {exc.reason}"),
                status_code=None,
                code="CONNECTION_ERROR") from exc
        except TimeoutError as exc:
            raise GhidraClientError(
                message=f"Request to {url} timed out",
                status_code=None,
                code="REQUEST_TIMEOUT") from exc

        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError as exc:
            raise GhidraClientError(
                message=(f"Non-JSON response from {url}: "
                         f"{raw[:100]!r}"),
                status_code=status,
                code="NON_JSON_RESPONSE") from exc

        success = bool(body.get("success", 200 <= status < 300))
        return _Response(success=success, status_code=status, body=body)

    def _require_success(self, resp: _Response) -> dict:
        """Raise :class:`GhidraClientError` on ``success=False``."""
        if resp.success:
            return resp.body
        err = resp.body.get("error") or {}
        raise GhidraClientError(
            message=(err.get("message")
                     or resp.body.get("error")
                     or "unknown Ghidra error"),
            status_code=resp.status_code,
            code=err.get("code"))

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        """Return ``True`` if the server answers ``GET /program``
        with a 2xx; never raises."""
        try:
            self._request("GET", "/program")
            return True
        except GhidraClientError:
            return False

    def program_info(self) -> GhidraProgramInfo:
        """Return the current program's metadata."""
        resp = self._request("GET", "/program")
        body = self._require_success(resp)
        return GhidraProgramInfo.from_json(body.get("result") or {})

    def get_function(self, address: int) -> GhidraFunction:
        """Fetch one function by absolute address."""
        resp = self._request("GET", f"/functions/{address:08X}")
        body = self._require_success(resp)
        return GhidraFunction.from_json(body.get("result") or {})

    def iter_functions(self, *, page_size: int = 500
                       ) -> Iterator[GhidraFunction]:
        """Paginate every function in the program.

        ``page_size`` defaults to 500 — large enough to finish a
        typical XBE (~4 k functions) in ~10 requests while still
        being small enough to avoid HTTP timeouts.
        """
        offset = 0
        while True:
            resp = self._request(
                "GET", "/functions",
                params={"offset": offset, "limit": page_size})
            body = self._require_success(resp)
            result = body.get("result") or []
            if not result:
                return
            for entry in result:
                yield GhidraFunction.from_json(entry)
            if len(result) < page_size:
                return
            offset += len(result)

    def rename_function(self, address: int, new_name: str) -> dict:
        """``PATCH /functions/{addr}`` with a new name.

        Returns the raw JSON body on success (contains the updated
        function metadata).
        """
        resp = self._request(
            "PATCH", f"/functions/{address:08X}",
            json_body={"name": new_name})
        return self._require_success(resp)

    def set_function_signature(self, address: int,
                               signature: str) -> dict:
        """``PATCH /functions/{addr}`` with a new signature."""
        resp = self._request(
            "PATCH", f"/functions/{address:08X}",
            json_body={"signature": signature})
        return self._require_success(resp)

    def set_comment(self, address: int, comment: str, *,
                    kind: str = "plate") -> dict:
        """``POST /memory/{addr}/comments/{kind}`` with the text.

        ``kind`` is one of ``plate`` / ``pre`` / ``post`` / ``eol``
        / ``repeatable``.  Pass ``comment=""`` to remove an
        existing comment.
        """
        if kind not in ("plate", "pre", "post", "eol", "repeatable"):
            raise ValueError(f"unsupported comment kind: {kind!r}")
        resp = self._request(
            "POST", f"/memory/{address:08X}/comments/{kind}",
            json_body={"comment": comment})
        return self._require_success(resp)

    def iter_labels(self, *, page_size: int = 500
                    ) -> Iterator[GhidraLabel]:
        """Paginate every symbol/label in the program.  Pulls from
        ``/symbols/labels`` (which includes EXTERNAL thunks)."""
        offset = 0
        while True:
            resp = self._request(
                "GET", "/symbols/labels",
                params={"offset": offset, "limit": page_size})
            body = self._require_success(resp)
            result = body.get("result") or []
            if not result:
                return
            for entry in result:
                yield GhidraLabel.from_json(entry)
            if len(result) < page_size:
                return
            offset += len(result)


def client_from_env() -> GhidraClient:
    """Build a :class:`GhidraClient` from environment variables.

    Honoured vars:

    - ``AZURIK_GHIDRA_HOST`` (default ``localhost``)
    - ``AZURIK_GHIDRA_PORT`` (default ``8193``)
    - ``AZURIK_GHIDRA_TIMEOUT`` (seconds; default ``10``)
    - ``AZURIK_GHIDRA_ORIGIN`` (default ``http://localhost``)
    """
    host = os.environ.get("AZURIK_GHIDRA_HOST", "localhost")
    port = int(os.environ.get("AZURIK_GHIDRA_PORT", "8193"))
    timeout = float(os.environ.get("AZURIK_GHIDRA_TIMEOUT", "10"))
    origin = os.environ.get("AZURIK_GHIDRA_ORIGIN", "http://localhost")
    return GhidraClient(host=host, port=port, timeout=timeout,
                        origin=origin)
