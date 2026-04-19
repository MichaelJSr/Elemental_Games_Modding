"""Mock Ghidra HTTP server for CI tests.

Implements just enough of the real GhydraMCP plugin's HTTP
contract to exercise :class:`GhidraClient` + every downstream
tool (``ghidra-sync``, ``ghidra-coverage``, the shim scaffolder)
without a live Ghidra running.

## Scope

Implements the same subset :mod:`ghidra_client` consumes:

- ``GET  /program``
- ``GET  /functions?offset&limit``
- ``GET  /functions/{addr}``
- ``PATCH /functions/{addr}``   (rename / set signature)
- ``POST /memory/{addr}/comments/{kind}``
- ``GET  /symbols/labels?offset&limit``

Everything else returns the same ``ENDPOINT_NOT_FOUND`` envelope
the real plugin emits, so tests catch regressions in the sync
tool that try to call out-of-contract endpoints.

## State model

Functions + labels live in a pair of in-memory dicts the test
constructs via :meth:`MockGhidraServer.register_function` and
:meth:`register_label`.  Mutating endpoints (rename, set
signature, set comment) update the internal state so the same
server instance can be probed + tweaked + re-probed across a
test.

## Thread model

The server runs in a daemon thread bound to an ephemeral port.
Use it as a context manager to guarantee shutdown:

.. code-block:: python

    with MockGhidraServer() as mock:
        client = GhidraClient(port=mock.port)
        mock.register_function(0x85700, "FUN_00085700", ...)
        client.rename_function(0x85700, "gravity_integrate_raw")
        assert mock.functions[0x85700]["name"] == "gravity_integrate_raw"

The class is deliberately simple — no auth, no Origin validation
(tests set ``Origin: http://localhost`` anyway), no CORS.  The
goal is to test OUR client's behaviour, not to re-implement
Ghidra's hardening.
"""

from __future__ import annotations

import json
import socket
import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse


@dataclass
class _FunctionState:
    """One registered function's mutable state."""
    address: int
    name: str
    signature: str = "undefined stub(void)"
    return_type: str = "undefined"
    parameters: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "address": f"{self.address:08x}",
            "name": self.name,
            "signature": self.signature,
            "returnType": self.return_type,
            "parameters": list(self.parameters),
        }


@dataclass
class _LabelState:
    address: str
    name: str
    namespace: str = ""
    symbol_type: str = "Label"
    is_primary: bool = True

    def to_dict(self) -> dict:
        return {
            "address": self.address,
            "name": self.name,
            "namespace": self.namespace,
            "type": self.symbol_type,
            "isPrimary": self.is_primary,
        }


class MockGhidraServer:
    """Threaded HTTP server emulating the Ghidra plugin."""

    def __init__(self, host: str = "127.0.0.1",
                 port: int = 0) -> None:
        self.host = host
        self._requested_port = port
        self.functions: dict[int, _FunctionState] = {}
        self.labels: list[_LabelState] = []
        self.comments: dict[tuple[int, str], str] = {}
        self.program_info: dict[str, Any] = {
            "programId": "mock/test.xbe",
            "name": "test.xbe",
            "languageId": "x86:LE:32:default",
            "compilerSpecId": "windows",
            "imageBase": "00010000",
            "memorySize": 1024,
            "isOpen": True,
            "analysisComplete": True,
        }
        # List of (method, path) tuples recorded in order — tests
        # assert on exact API call sequences.
        self.request_log: list[tuple[str, str]] = []
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Fixture helpers (called from tests)
    # ------------------------------------------------------------------

    def register_function(self, address: int, name: str, *,
                          signature: str | None = None,
                          return_type: str = "undefined",
                          parameters: list[dict] | None = None
                          ) -> None:
        self.functions[address] = _FunctionState(
            address=address, name=name,
            signature=(signature
                       or f"undefined {name}(void)"),
            return_type=return_type,
            parameters=parameters or [])

    def register_label(self, address: str, name: str, *,
                       namespace: str = "",
                       symbol_type: str = "Label",
                       is_primary: bool = True) -> None:
        self.labels.append(_LabelState(
            address=address, name=name, namespace=namespace,
            symbol_type=symbol_type, is_primary=is_primary))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._server is not None:
            return
        handler = _make_handler(self)
        # Bind to the caller-requested port; port=0 → OS picks free.
        #
        # ThreadingHTTPServer spawns a handler thread per request so
        # a slow / hung handler can't block the next incoming call.
        # This matters under pytest load — single-threaded HTTPServer
        # was timing out when other tests left sockets in TIME_WAIT
        # or the interpreter was under GIL pressure.
        self._server = ThreadingHTTPServer(
            (self.host, self._requested_port), handler)
        # Daemon threads so Python exit doesn't hang on pending
        # requests.
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name=f"mock-ghidra-{self.port}",
            daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None

    def __enter__(self) -> "MockGhidraServer":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# Request handler factory — closure over a MockGhidraServer instance
# ---------------------------------------------------------------------------


def _envelope(*, instance: str, success: bool = True,
              result: Any = None, error: dict | None = None,
              extra: dict | None = None) -> dict:
    env = {
        "id": str(uuid.uuid4()),
        "instance": instance,
        "success": success,
    }
    if result is not None:
        env["result"] = result
    if error is not None:
        env["error"] = error
    if extra:
        env.update(extra)
    return env


def _make_handler(server: MockGhidraServer) -> type:
    """Produce a :class:`BaseHTTPRequestHandler` bound to
    ``server``'s state."""
    outer = server

    class Handler(BaseHTTPRequestHandler):
        # Silence the noisy default access log — keeps pytest
        # output readable.
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa
            return

        # ---- plumbing -------------------------------------------

        def _respond(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _instance_url(self) -> str:
            return f"http://{outer.host}:{outer.port}"

        def _not_found(self) -> None:
            self._respond(404, _envelope(
                instance=self._instance_url(),
                success=False,
                error={"message": "Endpoint not found",
                       "code": "ENDPOINT_NOT_FOUND"}))

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if not length:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}

        # ---- dispatch -------------------------------------------

        def _route(self, method: str) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = {k: v[0] for k, v in
                     parse_qs(parsed.query).items()}
            outer.request_log.append((method, path))
            try:
                handler = _dispatch_table.get((method, _pattern_of(path)))
                if handler is None:
                    self._not_found()
                    return
                handler(self, path, query)
            except Exception as exc:  # noqa: BLE001
                self._respond(500, _envelope(
                    instance=self._instance_url(),
                    success=False,
                    error={"message": f"handler raised: {exc}",
                           "code": "HANDLER_ERROR"}))

        def do_GET(self) -> None:    # noqa: N802 (stdlib contract)
            self._route("GET")

        def do_POST(self) -> None:   # noqa: N802
            self._route("POST")

        def do_PATCH(self) -> None:  # noqa: N802
            self._route("PATCH")

        # ---- handlers (indirected through _dispatch_table) ------

        def handle_program(self, path: str, query: dict) -> None:
            self._respond(200, _envelope(
                instance=self._instance_url(),
                result=dict(outer.program_info)))

        def handle_functions_list(self, path, query) -> None:
            offset = int(query.get("offset", "0"))
            limit = int(query.get("limit", "100"))
            items = [f.to_dict() for f in
                     sorted(outer.functions.values(),
                            key=lambda f: f.address)]
            slice_ = items[offset:offset + limit]
            self._respond(200, _envelope(
                instance=self._instance_url(),
                result=slice_,
                extra={"offset": offset, "limit": limit,
                       "size": len(items)}))

        def handle_function_get(self, path, query) -> None:
            addr = _addr_from_path(path, "/functions/")
            fn = outer.functions.get(addr)
            if fn is None:
                self._respond(404, _envelope(
                    instance=self._instance_url(),
                    success=False,
                    error={"message": f"No function at 0x{addr:X}",
                           "code": "FUNCTION_NOT_FOUND"}))
                return
            self._respond(200, _envelope(
                instance=self._instance_url(),
                result=fn.to_dict()))

        def handle_function_patch(self, path, query) -> None:
            addr = _addr_from_path(path, "/functions/")
            fn = outer.functions.get(addr)
            if fn is None:
                self._respond(404, _envelope(
                    instance=self._instance_url(),
                    success=False,
                    error={"message": f"No function at 0x{addr:X}",
                           "code": "FUNCTION_NOT_FOUND"}))
                return
            body = self._read_json()
            if not body:
                self._respond(400, _envelope(
                    instance=self._instance_url(),
                    success=False,
                    error={"message": "No changes specified",
                           "code": "NO_CHANGES"}))
                return
            if "name" in body:
                fn.name = str(body["name"])
            if "signature" in body:
                fn.signature = str(body["signature"])
            self._respond(200, _envelope(
                instance=self._instance_url(),
                result=fn.to_dict()))

        def handle_set_comment(self, path, query) -> None:
            # /memory/{addr}/comments/{kind}
            parts = path.strip("/").split("/")
            if len(parts) != 4:
                self._not_found()
                return
            try:
                addr = int(parts[1], 16)
            except ValueError:
                self._not_found()
                return
            kind = parts[3]
            body = self._read_json()
            comment = str(body.get("comment", ""))
            outer.comments[(addr, kind)] = comment
            self._respond(200, _envelope(
                instance=self._instance_url(),
                result={"address": f"{addr:08x}",
                        "comment_type": kind,
                        "comment": comment}))

        def handle_labels_list(self, path, query) -> None:
            offset = int(query.get("offset", "0"))
            limit = int(query.get("limit", "100"))
            items = [lbl.to_dict() for lbl in outer.labels]
            slice_ = items[offset:offset + limit]
            self._respond(200, _envelope(
                instance=self._instance_url(),
                result=slice_,
                extra={"offset": offset, "limit": limit,
                       "size": len(items)}))

    # ---- dispatch table: (method, path pattern) → handler-method ---

    global _dispatch_table  # reused on handler reinstantiation
    _dispatch_table = {
        ("GET", "/program"): Handler.handle_program,
        ("GET", "/functions"): Handler.handle_functions_list,
        ("GET", "/functions/{addr}"): Handler.handle_function_get,
        ("PATCH", "/functions/{addr}"): Handler.handle_function_patch,
        ("POST", "/memory/{addr}/comments/{kind}"):
            Handler.handle_set_comment,
        ("GET", "/symbols/labels"): Handler.handle_labels_list,
    }
    return Handler


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------


def _pattern_of(path: str) -> str:
    """Turn a concrete path into a loose pattern for dispatch.

    ``/functions/00085700`` → ``/functions/{addr}``
    ``/memory/00085700/comments/plate`` → ``/memory/{addr}/comments/{kind}``
    Anything unrecognised falls through literally.
    """
    parts = path.strip("/").split("/")
    # /functions/{addr}
    if len(parts) == 2 and parts[0] == "functions" and \
            _looks_like_addr(parts[1]):
        return "/functions/{addr}"
    # /memory/{addr}/comments/{kind}
    if (len(parts) == 4 and parts[0] == "memory"
            and parts[2] == "comments"
            and _looks_like_addr(parts[1])):
        return "/memory/{addr}/comments/{kind}"
    return "/" + "/".join(parts)


def _looks_like_addr(token: str) -> bool:
    try:
        int(token, 16)
    except ValueError:
        return False
    return True


def _addr_from_path(path: str, prefix: str) -> int:
    """Extract a hex address from a path like ``/functions/00085700``."""
    assert path.startswith(prefix)
    tail = path[len(prefix):].split("/")[0]
    return int(tail, 16)


__all__ = ["MockGhidraServer"]
