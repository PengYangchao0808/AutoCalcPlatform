"""Real newline-delimited JSON-RPC 2.0 stdio transport for the MCP adapter.

B05 (plan ``.omo/plans/acp2-parallel-plan-b-runtime.md`` todo 6).  This module
owns **framing only**.  Protocol methods, the tool allowlist, policy and
identity stay in :mod:`acp_services.entrypoints.mcp`; the injected
:class:`~acp_services.entrypoints.mcp.McpServer` is the single dispatcher.

Rules enforced here:

* one UTF-8 JSON-RPC 2.0 object per line, read from ``sys.stdin.buffer`` and
  written to ``sys.stdout.buffer``;
* stdout carries protocol frames only — every diagnostic goes to a ``stderr``
  logger;
* a hard maximum frame size (default 1 MiB); an oversized frame is rejected
  with a JSON-RPC error and the remainder is drained so the stream does not
  desynchronise;
* malformed UTF-8/JSON and JSON-RPC batch arrays become JSON-RPC errors, never
  an exception or process exit;
* EOF, ``SIGTERM`` and ``SIGINT`` close only this adapter — nothing is
  submitted or cancelled, because the Controller owns task lifetime;
* the per-connection lifecycle gate rejects ``tools/list``/``tools/call`` before
  a successful ``initialize`` (``not_initialized``), without touching a backend;
* ``server/discover`` is a **pre-initialize** method: the gate lets it through so
  the protocol layer answers it with an ordinary METHOD_NOT_FOUND (``-32601``).
  No discover result is implemented — an era-negotiating client (e.g.
  ``@modelcontextprotocol/client`` 2.2.0 with ``versionNegotiation: auto``)
  probes with ``server/discover``, treats the ordinary error as "legacy server"
  and falls back to the 2025-era ``initialize`` (ADR-006).

No threads are started and no task/storage state is opened by this module.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
from collections.abc import Mapping
from types import FrameType
from typing import IO, Any

from acp_services.entrypoints import mcp

__all__ = [
    "MAX_FRAME_BYTES",
    "JsonRpcLineTransport",
    "configure_stdio_logging",
    "run_stdio",
]

#: Hard maximum size of one frame (bytes, excluding the trailing newline).
MAX_FRAME_BYTES = 1024 * 1024

#: Methods a fresh connection may use before ``initialize`` succeeds.
#: ``server/discover`` is included so the protocol layer (not the lifecycle
#: gate) answers it: ``McpServer._invoke`` has no such handler, so the client
#: receives a plain ``-32601`` unknown-method error — never a fabricated
#: discover result and never a ``not_initialized`` lifecycle error.
_PRE_INIT_METHODS = frozenset({"initialize", "ping", "server/discover"})

logger = logging.getLogger(__name__)


class _ShutdownRequestedError(Exception):
    """Raised inside a signal handler to leave the blocking read promptly."""


def configure_stdio_logging(level: str = "INFO") -> None:
    """Attach exactly one ``stderr`` handler to this module's logger."""
    numeric = getattr(logging, str(level).upper(), logging.INFO)
    logger.setLevel(numeric)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(handler)


def _error_payload(
    request_id: Any,
    code: int,
    message: str,
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": int(code), "message": str(message)}
    if data:
        error["data"] = dict(data)
    return {"jsonrpc": mcp.JSONRPC_VERSION, "id": request_id, "error": error}


class JsonRpcLineTransport:
    """Read, dispatch and write JSON-RPC lines without owning protocol policy.

    Args:
        server: The protocol dispatcher (single MCP policy owner).
        stdin: Binary input stream (defaults to ``sys.stdin.buffer``).
        stdout: Binary output stream (defaults to ``sys.stdout.buffer``).
        max_frame_bytes: Hard per-frame byte limit.
        log_level: Diagnostic level for the stderr logger.
    """

    def __init__(
        self,
        server: mcp.McpServer,
        *,
        stdin: IO[bytes] | None = None,
        stdout: IO[bytes] | None = None,
        max_frame_bytes: int = MAX_FRAME_BYTES,
        log_level: str = "INFO",
    ) -> None:
        if max_frame_bytes < 1024:
            raise ValueError("max_frame_bytes must be at least 1024")
        self._server = server
        self._stdin = stdin if stdin is not None else sys.stdin.buffer
        self._stdout = stdout if stdout is not None else sys.stdout.buffer
        self._max_frame_bytes = int(max_frame_bytes)
        self._log_level = log_level
        self._initialized = False
        self._previous_handlers: dict[int, Any] = {}

    def serve_forever(self) -> int:
        """Serve until EOF or a shutdown signal; returns the process exit code."""
        configure_stdio_logging(self._log_level)
        self._install_signal_handlers()
        logger.info(
            "mcp stdio transport ready (max_frame_bytes=%d)",
            self._max_frame_bytes,
        )
        try:
            while True:
                frame = self._read_frame()
                if frame is None:
                    logger.info("stdin EOF; closing stdio transport")
                    return 0
                body, oversized = frame
                if oversized:
                    self._write_frame(
                        _error_payload(
                            None,
                            mcp.JsonRpcErrorCode.INVALID_REQUEST,
                            "frame exceeds max_frame_bytes",
                            {
                                "code": "frame_too_large",
                                "max_frame_bytes": self._max_frame_bytes,
                            },
                        )
                    )
                    continue
                self._dispatch(body)
        except _ShutdownRequestedError as exc:
            logger.info("shutdown signal %s; closing stdio transport", exc)
            return 0
        except (BrokenPipeError, ConnectionResetError):
            logger.info("client closed stdout; closing stdio transport")
            return 0
        finally:
            self._restore_signal_handlers()

    def _read_frame(self) -> tuple[str, bool] | None:
        """Return ``(text, oversized)`` or ``None`` on EOF."""
        raw = self._stdin.readline(self._max_frame_bytes + 1)
        if not raw:
            return None
        if raw.endswith(b"\n"):
            body = raw[:-1]
            if body.endswith(b"\r"):
                body = body[:-1]
            if len(body) > self._max_frame_bytes:
                return "", True
            return body.decode("utf-8", errors="replace"), False
        if len(raw) > self._max_frame_bytes:
            self._drain_to_newline()
            return "", True
        return raw.decode("utf-8", errors="replace"), False

    def _drain_to_newline(self) -> None:
        while True:
            chunk = self._stdin.readline(self._max_frame_bytes + 1)
            if not chunk or chunk.endswith(b"\n"):
                return

    def _dispatch(self, text: str) -> None:
        stripped = text.strip()
        if not stripped:
            logger.warning("rejected empty frame")
            self._write_frame(
                _error_payload(None, mcp.JsonRpcErrorCode.PARSE_ERROR, "parse error: empty frame")
            )
            return
        try:
            request = json.loads(stripped)
        except ValueError:
            logger.warning("rejected malformed JSON frame (%d bytes)", len(text))
            self._write_frame(_error_payload(None, mcp.JsonRpcErrorCode.PARSE_ERROR, "parse error"))
            return
        if isinstance(request, list):
            logger.warning("rejected JSON-RPC batch frame (%d items)", len(request))
            self._write_frame(
                _error_payload(
                    None,
                    mcp.JsonRpcErrorCode.INVALID_REQUEST,
                    "JSON-RPC batch requests are not supported",
                    {"code": "batch_not_supported"},
                )
            )
            return
        if not isinstance(request, Mapping):
            self._write_frame(
                _error_payload(
                    None,
                    mcp.JsonRpcErrorCode.INVALID_REQUEST,
                    "request must be a JSON object",
                )
            )
            return

        method = request.get("method")
        request_id = request.get("id")
        is_notification = "id" not in request
        if not self._is_pre_init_allowed(method):
            if is_notification:
                return
            logger.warning("rejected %r before initialize", method)
            self._write_frame(
                _error_payload(
                    request_id,
                    mcp.JsonRpcErrorCode.INVALID_REQUEST,
                    "MCP session is not initialized",
                    {"code": "not_initialized"},
                )
            )
            return

        try:
            response = self._server.handle(request)
        except mcp.McpProtocolError as exc:
            response = _error_payload(request_id, exc.code, str(exc), exc.data)
        if response is None:
            return
        if method == "initialize" and isinstance(response.get("result"), Mapping):
            self._initialized = True
            negotiated = response["result"].get("protocolVersion")
            logger.info("MCP session initialized (protocolVersion=%s)", negotiated)
        self._write_frame(response)

    def _is_pre_init_allowed(self, method: Any) -> bool:
        if self._initialized:
            return True
        if not isinstance(method, str):
            return False
        return method in _PRE_INIT_METHODS or method.startswith("notifications/")

    def _write_frame(self, payload: Mapping[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
        self._stdout.write(data + b"\n")
        self._stdout.flush()
        logger.debug("wrote protocol frame (%d bytes)", len(data))

    def _install_signal_handlers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                self._previous_handlers[int(sig)] = signal.getsignal(sig)
                signal.signal(sig, self._on_signal)
            except (ValueError, OSError):
                self._previous_handlers.pop(int(sig), None)

    def _restore_signal_handlers(self) -> None:
        for signum, previous in self._previous_handlers.items():
            try:
                signal.signal(signum, previous)
            except (ValueError, OSError):
                pass
        self._previous_handlers.clear()

    def _on_signal(self, signum: int, _frame: FrameType | None) -> None:
        raise _ShutdownRequestedError(signum)


def run_stdio(
    server: mcp.McpServer,
    *,
    stdin: IO[bytes] | None = None,
    stdout: IO[bytes] | None = None,
    max_frame_bytes: int = MAX_FRAME_BYTES,
    log_level: str = "INFO",
) -> int:
    """Serve *server* over newline-delimited JSON-RPC until EOF/signal."""
    transport = JsonRpcLineTransport(
        server,
        stdin=stdin,
        stdout=stdout,
        max_frame_bytes=max_frame_bytes,
        log_level=log_level,
    )
    return transport.serve_forever()
