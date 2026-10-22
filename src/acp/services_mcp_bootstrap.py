"""`acp`-side composition root for the B05 MCP stdio adapter.

B05 (plan ``.omo/plans/acp2-parallel-plan-b-runtime.md`` todo 6).  This module
is the only place that wires the stdlib-only Controller client into the
protocol/allowlist/policy owner :mod:`acp_services.entrypoints.mcp`.

Discovery and read-only calls are bound for real:

* ``acp_capabilities`` -> ``GET /api/capabilities`` through
  :class:`~acp_services.entrypoints.controller_client.ControllerClient`;
* ``acp_status``       -> ``GET /api/v1/jobs/{id}`` (no connection state).

Preflight/submit/cancel/prepare/read/analyze are explicitly deferred to B06 and
return a structured ``unsupported_capability`` tool error (``binding=B06``,
``status=not_verified``).  They never fabricate a task reference.

The module imports :mod:`acp_services` only; it never imports a ``JobManager``,
opens the jobs database or starts a Controller.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from acp_services.entrypoints import machine_output as mo
from acp_services.entrypoints import mcp
from acp_services.entrypoints.controller_client import (
    DEFAULT_TIMEOUT_SECONDS,
    ControllerClient,
    ControllerClientError,
    Health,
)

logger = logging.getLogger(__name__)

__all__ = [
    "B06_DEFERRED_COMMANDS",
    "McpRuntime",
    "build_mcp_runtime",
    "build_mcp_server",
    "probe_controller",
]

#: Application commands whose Controller route is not bound until B06.
B06_DEFERRED_COMMANDS: tuple[str, ...] = (
    "preflight",
    "submit",
    "cancel",
    "prepare",
    "read",
    "analyze",
)

_TOOL_ERROR_CODES: dict[str, str] = {
    "input_invalid": mo.ErrorCode.INPUT_INVALID,
    "usage_error": mo.ErrorCode.USAGE_ERROR,
    "unsupported_capability": mo.ErrorCode.UNSUPPORTED_CAPABILITY,
    "backend_unavailable": mo.ErrorCode.BACKEND_UNAVAILABLE,
    "resource_unavailable": mo.ErrorCode.RESOURCE_UNAVAILABLE,
    "resource_not_ready": mo.ErrorCode.RESOURCE_NOT_READY,
    "revision_conflict": mo.ErrorCode.REVISION_CONFLICT,
    "access_denied": mo.ErrorCode.ACCESS_DENIED,
    "timeout": mo.ErrorCode.TIMEOUT,
    "interrupted": mo.ErrorCode.INTERRUPTED,
    "cancelled": mo.ErrorCode.CANCELLED,
}


def _tool_error(command: str, exc: ControllerClientError) -> mcp.McpToolError:
    code = _TOOL_ERROR_CODES.get(exc.code, mo.ErrorCode.BACKEND_FAILURE)
    return mcp.McpToolError(
        code,
        exc.message,
        params={"command": command, **dict(exc.params)},
        retryable=exc.retryable,
    )


def _deferred(command: str) -> mcp.McpToolError:
    return mcp.McpToolError(
        mo.ErrorCode.UNSUPPORTED_CAPABILITY,
        f"{command} binding is deferred to B06",
        params={"command": command, "binding": "B06", "status": "not_verified"},
        retryable=False,
    )


class _ControllerCapabilities:
    def __init__(self, client: ControllerClient) -> None:
        self._client = client

    def capabilities(self) -> dict[str, Any]:
        try:
            snapshot = self._client.capabilities()
        except ControllerClientError as exc:
            raise _tool_error("capabilities", exc) from None
        data = dict(snapshot.raw)
        data.setdefault("protocol_version", mcp.MCP_PROTOCOL_VERSION)
        data.setdefault("tools", list(mcp.TOOL_ALLOWLIST))
        return data


class _ControllerStatus:
    def __init__(self, client: ControllerClient) -> None:
        self._client = client

    def get(self, reference: str, *, workspace_id: str) -> Mapping[str, Any] | None:
        del workspace_id
        try:
            snapshot = self._client.status(reference)
        except ControllerClientError as exc:
            message = exc.message.lower()
            if exc.code == mo.ErrorCode.UNKNOWN and (
                "not found" in message or "http 404" in message
            ):
                return None
            raise _tool_error("status", exc) from None
        data = dict(snapshot.raw)
        data.setdefault("reference", snapshot.task_ref or reference)
        data.setdefault("state", snapshot.state)
        return data


class _DeferredPreflight:
    def preflight(self, request: Mapping[str, Any], *, workspace_id: str) -> Mapping[str, Any]:
        del request, workspace_id
        raise _deferred("preflight")


class _DeferredSubmit:
    def submit(self, spec: Mapping[str, Any], *, idempotency_key: str, workspace_id: str) -> Any:
        del spec, idempotency_key, workspace_id
        raise _deferred("submit")


class _DeferredCancel:
    def cancel(self, reference: str, *, workspace_id: str) -> Any:
        del reference, workspace_id
        raise _deferred("cancel")


class _DeferredArtifacts:
    def prepare(
        self, request: Mapping[str, Any], *, idempotency_key: str, workspace_id: str
    ) -> Any:
        del request, idempotency_key, workspace_id
        raise _deferred("prepare")

    def read(self, request: Mapping[str, Any], *, workspace_id: str) -> Any:
        del request, workspace_id
        raise _deferred("read")


class _DeferredAnalysis:
    def analyze(self, request: Mapping[str, Any], *, workspace_id: str) -> Any:
        del request, workspace_id
        raise _deferred("analyze")


@dataclass(frozen=True)
class McpRuntime:
    """A built MCP server plus the Controller facts used to build it."""

    server: mcp.McpServer
    client: ControllerClient
    health: Health


def probe_controller(controller_url: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Health:
    """Read the Controller's entry-equivalence facts (no side effects)."""
    return ControllerClient(controller_url, timeout=timeout).health()


def build_mcp_runtime(
    controller_url: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> McpRuntime:
    """Bind real discovery/read-only ports and construct the MCP server.

    Raises:
        ControllerClientError: the Controller is unreachable or unhealthy.
    """
    client = ControllerClient(controller_url, timeout=timeout)
    health = client.health()
    logger.info(
        "MCP adapter bound to Controller %s (candidate_revision=%s, "
        "contract_version=%s, contract_digest=%s)",
        client.base_url,
        health.candidate_revision,
        health.contract_version,
        health.contract_digest,
    )
    backends = mcp.McpBackends(
        capabilities=_ControllerCapabilities(client),
        preflight=_DeferredPreflight(),
        submit=_DeferredSubmit(),
        status=_ControllerStatus(client),
        cancel=_DeferredCancel(),
        artifacts=_DeferredArtifacts(),
        analysis=_DeferredAnalysis(),
    )
    server = mcp.McpServer(
        backends=backends,
        identity=mcp.IdentityMapper(configured_tier=mcp.STDIO_PERMISSION_SCOPE),
        configured_tier=mcp.STDIO_PERMISSION_SCOPE,
    )
    return McpRuntime(server=server, client=client, health=health)


def build_mcp_server(
    controller_url: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> mcp.McpServer:
    """Convenience wrapper returning only the protocol server."""
    return build_mcp_runtime(controller_url, timeout=timeout).server
