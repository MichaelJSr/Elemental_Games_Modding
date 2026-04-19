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
    "GhidraDecomp",
    "GhidraFunction",
    "GhidraLabel",
    "GhidraProgramInfo",
    "GhidraStruct",
    "GhidraStructField",
    "GhidraXref",
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


@dataclass(frozen=True)
class GhidraXref:
    """One cross-reference edge between two addresses.

    Matches the shape ``GET /xrefs?to_addr=HEX`` /
    ``?from_addr=HEX`` returns: edges + the enclosing functions
    + the referring-instruction mnemonic.
    """

    from_addr: int
    to_addr: int
    ref_type: str         # "UNCONDITIONAL_CALL", "DATA", ...
    from_instruction: str  # e.g. "CALL 0x00085700"
    from_function_va: int | None   # enclosing caller, if any
    from_function_name: str | None
    to_function_va: int | None     # enclosing callee, if any
    to_function_name: str | None
    is_primary: bool = True

    @classmethod
    def from_json(cls, obj: dict) -> "GhidraXref":
        def _parse(s: object) -> int:
            try:
                return int(str(s), 16)
            except (TypeError, ValueError):
                return 0

        def _fn(which: str) -> tuple[int | None, str | None]:
            fn = obj.get(which) or {}
            if not fn:
                return None, None
            return _parse(fn.get("address")), fn.get("name")

        from_va, from_name = _fn("from_function")
        to_va, to_name = _fn("to_function")
        return cls(
            from_addr=_parse(obj.get("from_addr")),
            to_addr=_parse(obj.get("to_addr")),
            ref_type=obj.get("refType", ""),
            from_instruction=obj.get("from_instruction", ""),
            from_function_va=from_va,
            from_function_name=from_name,
            to_function_va=to_va,
            to_function_name=to_name,
            is_primary=bool(obj.get("isPrimary", True)),
        )


@dataclass(frozen=True)
class GhidraDecomp:
    """Result of ``GET /functions/{addr}/decompile``."""

    address: int
    function_name: str
    decompiled: str

    @classmethod
    def from_json(cls, obj: dict,
                  *, address_hint: int = 0) -> "GhidraDecomp":
        inner = obj.get("result") if "decompiled" not in obj else obj
        fn = (inner or {}).get("function") or {}
        try:
            addr = int(str(fn.get("address")), 16)
        except (TypeError, ValueError):
            addr = address_hint
        return cls(
            address=addr,
            function_name=fn.get("name", ""),
            decompiled=(inner or {}).get("decompiled", ""),
        )


@dataclass(frozen=True)
class GhidraStructField:
    """One field inside a :class:`GhidraStruct`."""

    name: str
    data_type: str
    offset: int
    length: int
    comment: str = ""

    @classmethod
    def from_json(cls, obj: dict) -> "GhidraStructField":
        # Ghidra's REST plugin surfaces the type name as "type"
        # in per-field responses; the summary endpoint uses
        # "dataType" in some Ghidra revisions.  Accept either.
        data_type = (obj.get("type") or obj.get("dataType")
                     or obj.get("typePath", "").lstrip("/"))
        return cls(
            name=obj.get("name", ""),
            data_type=data_type,
            offset=int(obj.get("offset", 0)),
            length=int(obj.get("length", 0)),
            comment=obj.get("comment", ""),
        )


@dataclass(frozen=True)
class GhidraStruct:
    """Result of ``GET /structs/{name}`` — a typed struct layout."""

    name: str
    size: int
    fields: tuple[GhidraStructField, ...]
    category: str = ""
    description: str = ""

    @classmethod
    def from_json(cls, obj: dict) -> "GhidraStruct":
        fields_raw = obj.get("fields") or []
        return cls(
            name=obj.get("name", ""),
            size=int(obj.get("size", 0)),
            fields=tuple(
                GhidraStructField.from_json(f) for f in fields_raw),
            category=obj.get("category", ""),
            description=obj.get("description", ""),
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

    def decompile(self, address: int) -> GhidraDecomp:
        """Fetch C-like decompilation for the function at ``address``.

        Returns a :class:`GhidraDecomp` whose ``decompiled``
        field is the raw Ghidra output (same string the UI
        shows).  Raises :class:`GhidraClientError` when the
        address doesn't resolve to a known function.
        """
        resp = self._request(
            "GET", f"/functions/{address:08X}/decompile")
        body = self._require_success(resp)
        return GhidraDecomp.from_json(body, address_hint=address)

    def iter_xrefs_to(self, address: int, *,
                      page_size: int = 200,
                      ) -> Iterator[GhidraXref]:
        """Paginate every INCOMING xref to ``address``.

        The Ghidra endpoint returns one row per referring
        instruction.  Call-callers / data-referrers are
        surfaced the same way — filter on ``ref_type`` if the
        caller only wants certain kinds.
        """
        offset = 0
        while True:
            resp = self._request(
                "GET", "/xrefs",
                params={"to_addr": f"{address:08X}",
                        "offset": offset, "limit": page_size})
            body = self._require_success(resp)
            result = body.get("result") or {}
            refs = result.get("references") or []
            if not refs:
                return
            for entry in refs:
                yield GhidraXref.from_json(entry)
            if len(refs) < page_size:
                return
            offset += len(refs)

    def iter_xrefs_from(self, address: int, *,
                        page_size: int = 200,
                        ) -> Iterator[GhidraXref]:
        """Paginate every OUTGOING xref from ``address`` (calls /
        data references the code at this VA emits).

        Useful for call-graph traversal: from a function entry,
        the outgoing xrefs give every call it makes.
        """
        offset = 0
        while True:
            resp = self._request(
                "GET", "/xrefs",
                params={"from_addr": f"{address:08X}",
                        "offset": offset, "limit": page_size})
            body = self._require_success(resp)
            result = body.get("result") or {}
            refs = result.get("references") or []
            if not refs:
                return
            for entry in refs:
                yield GhidraXref.from_json(entry)
            if len(refs) < page_size:
                return
            offset += len(refs)

    def get_struct(self, name: str) -> GhidraStruct:
        """Fetch one struct layout by name.

        Raises :class:`GhidraClientError` (``STRUCT_NOT_FOUND``)
        when the struct isn't defined in the Ghidra project.
        """
        resp = self._request(
            "GET", f"/structs/{name}")
        body = self._require_success(resp)
        return GhidraStruct.from_json(body.get("result") or {})

    def create_struct(self, name: str, *,
                      size: int = 1,
                      category: str = "",
                      description: str = "",
                      ) -> dict:
        """Create a new empty struct in Ghidra's Data Type Manager.

        ``size`` is ignored by Ghidra when the struct has no fields
        (it starts as the minimum 1-byte unit) — pass it anyway for
        documentation; fields are added via :meth:`add_struct_field`
        in a second step.

        Raises :class:`GhidraClientError` if Ghidra already has a
        struct with this name (use :meth:`delete_struct` first if
        you want to re-create one from scratch).
        """
        body = {"name": name, "size": int(size)}
        if category:
            body["category"] = category
        if description:
            body["description"] = description
        resp = self._request("POST", "/structs", json_body=body)
        return self._require_success(resp).get("result") or {}

    def add_struct_field(self, name: str, *, field_name: str,
                         field_type: str,
                         offset: int | None = None,
                         length: int | None = None,
                         comment: str = "",
                         ) -> dict:
        """Append a field to an existing struct.

        Ghidra's field-type names differ from C spelling in a few
        cases (``uint`` / ``ushort`` / ``float`` / ``char`` /
        ``void *`` / etc.); if in doubt, call
        :meth:`iter_datatypes` first and pick a known type.
        """
        body: dict = {
            "fieldType": field_type, "name": field_name,
        }
        if offset is not None:
            body["offset"] = int(offset)
        if length is not None:
            body["length"] = int(length)
        if comment:
            body["comment"] = comment
        resp = self._request(
            "POST", f"/structs/{name}/fields", json_body=body)
        return self._require_success(resp).get("result") or {}

    def delete_struct(self, name: str) -> dict:
        """Remove a struct from the DTM.  Returns the deleted
        struct's summary or raises if it didn't exist."""
        resp = self._request(
            "DELETE", f"/structs/{name}")
        return self._require_success(resp).get("result") or {}

    def iter_structs(self, *, page_size: int = 200
                     ) -> Iterator[dict]:
        """Paginate every struct in the project.

        Yields the raw JSON dict per entry because the list
        endpoint returns summaries (path / size / name /
        numFields) without full field detail — call
        :meth:`get_struct` for the detailed layout.
        """
        offset = 0
        while True:
            resp = self._request(
                "GET", "/structs",
                params={"offset": offset, "limit": page_size})
            body = self._require_success(resp)
            result = body.get("result") or []
            if not result:
                return
            for entry in result:
                yield entry
            if len(result) < page_size:
                return
            offset += len(result)

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
