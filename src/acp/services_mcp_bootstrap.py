"""`acp`-side composition root for the MCP stdio adapter.

B06 (plan ``.omo/plans/acp2-parallel-plan-b-runtime.md`` todo 7).  This module is
the only place that wires the stdlib-only Controller client into the
protocol/allowlist/policy owner :mod:`acp_services.entrypoints.mcp`.

Every one of the eight tools is bound to **one Controller application command**:

======================================  =========================================
MCP tool                                Controller command
======================================  =========================================
``acp_capabilities``                    ``GET /api/capabilities``
``acp_preflight``                       ``POST /api/v1/preflight``
``acp_submit``                          ``POST /api/v1/jobs``
``acp_status``                          ``GET /api/v1/jobs/{id}`` /
                                        ``GET /api/v1/operations/{id}``
``acp_cancel``                          ``POST /api/v1/jobs/{id}/cancel``
``acp_prepare_artifact``                ``POST /api/v1/jobs/{id}/prepare``
``acp_read_artifact``                   ``GET /api/v1/jobs/{id}/prepared/{r}``
``acp_analyze``                         ``POST /api/v1/jobs/{id}/analyze``
======================================  =========================================

Parameters pass through the shared :mod:`acp_services.entrypoints.
application_params` normaliser, so MCP, HTTP and CLI cannot drift.  A submit
returns the durable ``task_ref`` immediately; the adapter never waits for QC and
never owns task state.  Status/read are query-only.  A missing artifact returns
``resource_not_ready`` (``not_prepared``), never an empty body.

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
from acp_services.entrypoints.application_params import (
    ApplicationParamError,
    normalize_analyze,
    normalize_preflight,
    normalize_prepare,
    normalize_read,
    normalize_submit,
)
from acp_services.entrypoints.controller_client import (
    DEFAULT_TIMEOUT_SECONDS,
    ControllerClient,
    ControllerClientError,
    Health,
    NotPreparedError,
)
from acp_services.entrypoints.status_projection import (
    project_operation_status,
    project_task_status,
)

logger = logging.getLogger(__name__)

__all__ = [
    "B06_DEFERRED_COMMANDS",
    "McpRuntime",
    "build_mcp_runtime",
    "build_mcp_server",
    "probe_controller",
]

#: Kept for backwards compatibility with B05 callers.  B06 binds every command.
B06_DEFERRED_COMMANDS: tuple[str, ...] = ()

_TOOL_ERROR_CODES: dict[str, str] = {
    "input_invalid": mo.ErrorCode.INPUT_INVALID,
    "usage_error": mo.ErrorCode.USAGE_ERROR,
    "unsupported_capability": mo.ErrorCode.UNSUPPORTED_CAPABILITY,
    "backend_unavailable": mo.ErrorCode.BACKEND_UNAVAILABLE,
    "resource_unavailable": mo.ErrorCode.RESOURCE_UNAVAILABLE,
    "resource_not_ready": mo.ErrorCode.RESOURCE_NOT_READY,
    "revision_conflict": mo.ErrorCode.REVISION_CONFLICT,
    "idempotency_conflict": mo.ErrorCode.REVISION_CONFLICT,
    "idempotency_in_progress": mo.ErrorCode.REVISION_CONFLICT,
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


def _param_error(command: str, exc: ApplicationParamError) -> mcp.McpToolError:
    return mcp.McpToolError(
        _TOOL_ERROR_CODES.get(exc.code, mo.ErrorCode.INPUT_INVALID),
        exc.message,
        params={"command": command, **dict(exc.params)},
        retryable=False,
    )


def _terminal_failure(command: str, exc: Exception) -> mcp.McpToolError:
    return mcp.McpToolError(
        mo.ErrorCode.BACKEND_FAILURE,
        f"{command} failed: {type(exc).__name__}",
        params={"command": command, "exception": type(exc).__name__},
        retryable=False,
    )


def _job_reference(record: Mapping[str, Any]) -> dict[str, Any]:
    task_ref = record.get("task_ref")
    if isinstance(task_ref, Mapping):
        reference = dict(task_ref)
        job_id = reference.get("task_id") or reference.get("job_id") or ""
    else:
        job_id = str(record.get("job_id") or record.get("task_id") or record.get("id") or "")
        reference = {"task_id": job_id, "job_id": job_id}
        attempt = record.get("attempt")
        if isinstance(attempt, int):
            reference["attempt"] = attempt
        release_id = record.get("release_id")
        if isinstance(release_id, str) and release_id:
            reference["release"] = {
                "release_id": release_id,
                "release_digest": record.get("release_digest"),
            }
    return {
        "reference": {"kind": "job", "id": str(job_id)},
        "task_ref": dict(reference),
    }


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
        data.setdefault(
            "extensions",
            [{"id": "acp.research.v1", "version": "1.1.0", "negotiated": True}],
        )
        return data


class _ControllerPreflight:
    def __init__(self, client: ControllerClient) -> None:
        self._client = client

    def preflight(self, request: Mapping[str, Any], *, workspace_id: str) -> Mapping[str, Any]:
        del workspace_id
        try:
            normalized = normalize_preflight(request)
            return dict(self._client.preflight(normalized))
        except ApplicationParamError as exc:
            raise _param_error("preflight", exc) from None
        except ControllerClientError as exc:
            raise _tool_error("preflight", exc) from None


class _ControllerSubmit:
    def __init__(self, client: ControllerClient) -> None:
        self._client = client

    def submit(self, spec: Mapping[str, Any], *, idempotency_key: str, workspace_id: str) -> Any:
        del workspace_id
        try:
            normalized = normalize_submit(spec)
            normalized.setdefault("idempotency_key", idempotency_key)
            record = dict(self._client.submit(normalized))
        except ApplicationParamError as exc:
            raise _param_error("submit", exc) from None
        except ControllerClientError as exc:
            raise _tool_error("submit", exc) from None
        except Exception as exc:  # pragma: no cover - defensive transport failure
            raise _terminal_failure("submit", exc) from None
        payload = _job_reference(record)
        for key in (
            "state",
            "status",
            "plan_digest",
            "proposal_digest",
            "idempotency_key",
            "idempotent_replay",
            "admission",
            "submission_id",
            "result",
            "approval_ref",
        ):
            if record.get(key) is not None:
                payload[key] = record[key]
        return payload


class _ControllerStatus:
    def __init__(self, client: ControllerClient) -> None:
        self._client = client

    @staticmethod
    def _operation_reference(reference: Any) -> str:
        if isinstance(reference, Mapping):
            kind = str(reference.get("kind") or "")
            value = reference.get("id") or reference.get("operation_id") or ""
            if kind == "operation":
                return str(value)
            return ""
        value = str(reference or "")
        return value if value.startswith("op_") else ""

    def get(self, reference: Any, *, workspace_id: str) -> Mapping[str, Any] | None:
        del workspace_id
        operation_id = self._operation_reference(reference)
        try:
            if operation_id:
                raw = dict(self._client.operation(operation_id))
                projection = project_operation_status(raw)
                projection.setdefault("reference", reference)
                return projection
            snapshot = self._client.status(reference)
        except NotPreparedError:
            return None
        except ControllerClientError as exc:
            message = exc.message.lower()
            if exc.code == mo.ErrorCode.UNKNOWN and (
                "not found" in message or "http 404" in message
            ):
                return None
            raise _tool_error("status", exc) from None
        projection = project_task_status(snapshot.raw, fallback_task_id=snapshot.task_id)
        projection["reference"] = reference
        return projection


class _ControllerCancel:
    def __init__(self, client: ControllerClient) -> None:
        self._client = client

    def cancel(self, reference: str, *, workspace_id: str) -> Any:
        del workspace_id
        try:
            return dict(self._client.cancel(reference))
        except ControllerClientError as exc:
            raise _tool_error("cancel", exc) from None


class _ControllerArtifacts:
    def __init__(self, client: ControllerClient) -> None:
        self._client = client

    def prepare(
        self, request: Mapping[str, Any], *, idempotency_key: str, workspace_id: str
    ) -> Any:
        del workspace_id
        try:
            normalized = normalize_prepare(request)
            normalized.setdefault("idempotency_key", idempotency_key)
            record = dict(self._client.prepare(normalized))
        except ApplicationParamError as exc:
            raise _param_error("prepare", exc) from None
        except ControllerClientError as exc:
            raise _tool_error("prepare", exc) from None
        operation_ref = record.get("operation_ref") or record.get("operation_id") or ""
        payload = dict(record)
        payload["operation_ref"] = operation_ref
        payload.setdefault(
            "reference",
            {"kind": "operation", "id": str(operation_ref)},
        )
        return payload

    def read(self, request: Mapping[str, Any], *, workspace_id: str) -> Any:
        del workspace_id
        try:
            normalized = normalize_read(request)
            return dict(self._client.read(normalized))
        except ApplicationParamError as exc:
            raise _param_error("read", exc) from None
        except ControllerClientError as exc:
            raise _tool_error("read", exc) from None


class _ControllerAnalysis:
    def __init__(self, client: ControllerClient) -> None:
        self._client = client

    def analyze(self, request: Mapping[str, Any], *, workspace_id: str) -> Any:
        del workspace_id
        try:
            normalized = normalize_analyze(request)
            return dict(self._client.analyze(normalized))
        except ApplicationParamError as exc:
            raise _param_error("analyze", exc) from None
        except ControllerClientError as exc:
            raise _tool_error("analyze", exc) from None


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
    """Bind every MCP tool to one Controller application command.

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
        preflight=_ControllerPreflight(client),
        submit=_ControllerSubmit(client),
        status=_ControllerStatus(client),
        cancel=_ControllerCancel(client),
        artifacts=_ControllerArtifacts(client),
        analysis=_ControllerAnalysis(client),
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
