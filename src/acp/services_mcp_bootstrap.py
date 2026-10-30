"""`acp`-side composition root for the MCP stdio adapter.

B06 (plan ``.omo/plans/acp2-parallel-plan-b-runtime.md`` todo 7).  This module is
the only place that wires the stdlib-only Controller client into the
protocol/allowlist/policy owner :mod:`acp_services.entrypoints.mcp`.

Every one of the ten tools is bound to **one Controller application command**:

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
``acp_get_guidance``                    ``GET /api/v1/guidance/{guide_id}``
``acp_list_jobs``                       ``GET /api/v1/jobs?status=&limit=``
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

from acp.mcp_extension import RESEARCH_EXTENSION_SPEC
from acp_services.entrypoints import machine_output as mo
from acp_services.entrypoints import mcp
from acp_services.entrypoints.application_params import (
    ENTRY_MCP,
    REQUEST_PREFLIGHT,
    REQUEST_PREPARE,
    REQUEST_SUBMIT,
    ApplicationParamError,
    normalize_analyze,
    normalize_read,
    validate_request_fields,
)
from acp_services.entrypoints.controller_client import (
    DEFAULT_TIMEOUT_SECONDS,
    ControllerClient,
    ControllerClientError,
    Health,
    NotPreparedError,
)
from acp_services.entrypoints.research_consumption import ControllerConsumptionPort
from acp_services.entrypoints.research_port import ControllerResearchPort
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
    # P3 (round 3): "preflight fingerprint does not match the submitted plan"
    # is a caller-side input error, never backend_failure.
    "preflight_stale": mo.ErrorCode.INPUT_INVALID,
    "idempotency_conflict": mo.ErrorCode.REVISION_CONFLICT,
    "idempotency_in_progress": mo.ErrorCode.REVISION_CONFLICT,
    "admission_denied": mo.ErrorCode.ACCESS_DENIED,
    "access_denied": mo.ErrorCode.ACCESS_DENIED,
    # B15 identity denials: a revoked/expired/unknown credential answers one
    # uniform 401 (``unauthenticated``) and must stay a typed access denial at
    # the MCP surface (M0-05 per-call revalidation).
    "unauthenticated": mo.ErrorCode.ACCESS_DENIED,
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

    def capabilities(self, *, workflow: str | None = None) -> dict[str, Any]:
        try:
            snapshot = (
                self._client.capabilities()
                if workflow is None
                else self._client.capabilities(workflow=workflow)
            )
        except ControllerClientError as exc:
            raise _tool_error("capabilities", exc) from None
        data = dict(snapshot.raw)
        data.setdefault("protocol_version", mcp.MCP_PROTOCOL_VERSION)
        data.setdefault("tools", list(mcp.TOOL_ALLOWLIST))
        # The research extension is *not* injected here: `extensions[]` is the
        # server's application-level handshake answer and is computed once by
        # `mcp.McpServer._tool_capabilities`.  A hard-coded `negotiated: true`
        # fallback (B06) claimed a delivery that does not exist and is gone.
        return data


class _ControllerPreflight:
    def __init__(self, client: ControllerClient) -> None:
        self._client = client

    def preflight(self, request: Mapping[str, Any], *, workspace_id: str) -> Mapping[str, Any]:
        """Forward the caller's preflight request unchanged (M0-06).

        Preflight is the dry run of a submit, so it must reach the Controller with
        the same fields: rebuild-through-``normalize_preflight`` dropped the
        execution-target/naming fields, which made an MCP preflight of a pinned
        (unknown/incapable) node look feasible while the HTTP submit rejected it.
        The field policy is applied first so an undeclared field is the same
        ``input_invalid`` before any network call.
        """
        del workspace_id
        payload = dict(request)
        try:
            validate_request_fields(payload, request_kind=REQUEST_PREFLIGHT, entry=ENTRY_MCP)
            return dict(self._client.preflight(payload))
        except ApplicationParamError as exc:
            raise _param_error("preflight", exc) from None
        except ControllerClientError as exc:
            raise _tool_error("preflight", exc) from None


class _ControllerSubmit:
    def __init__(self, client: ControllerClient) -> None:
        self._client = client

    def submit(self, spec: Mapping[str, Any], *, idempotency_key: str, workspace_id: str) -> Any:
        """Forward the caller's request body and raw idempotency key (M0-05/M0-06).

        The adapter no longer rebuilds a trimmed payload (field equivalence is
        M0-06) and never adds an identity: the Controller derives the principal
        and the project scope from its own credential, so ``principal_id`` and
        ``project_id`` are server facts, never request fields.  ``idempotency_key``
        is forwarded verbatim (``""`` = no key, an explicitly non-idempotent
        request); the Controller's persistent ledger owns claim/replay/conflict.

        M0-06 adds the *same* exhaustive field policy the HTTP entry applies, so an
        undeclared field (``execution_mod``) or a server-path field is the same
        ``input_invalid`` here instead of being silently dropped and forwarded.
        Nothing else about the body is rewritten.
        """
        del workspace_id
        payload = dict(spec)
        key = str(idempotency_key or "").strip()
        if key:
            payload["idempotency_key"] = key
        else:
            payload.pop("idempotency_key", None)
        try:
            validate_request_fields(payload, request_kind=REQUEST_SUBMIT, entry=ENTRY_MCP)
            record = dict(self._client.submit(payload))
        except ApplicationParamError as exc:
            raise _param_error("submit", exc) from None
        except ControllerClientError as exc:
            raise _tool_error("submit", exc) from None
        except Exception as exc:  # pragma: no cover - defensive transport failure
            raise _terminal_failure("submit", exc) from None
        payload_out = _job_reference(record)
        # Full response passthrough: a replay must deliver the same complete
        # TaskRef/attempt/admission envelope as the first call.
        for field_name, value in record.items():
            if value is not None:
                payload_out[field_name] = value
        payload_out["idempotency_key"] = record.get("idempotency_key", key or None)
        payload_out["idempotent_replay"] = bool(record.get("idempotent_replay", False))
        return payload_out


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

    def list_tasks(
        self, *, status: str | None = None, limit: int = 50, workspace_id: str = ""
    ) -> dict[str, Any]:
        del workspace_id
        try:
            return dict(self._client.list_tasks(status=status, limit=limit))
        except ControllerClientError as exc:
            raise _tool_error("list_tasks", exc) from None

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
        """Forward the prepare request unchanged (M0-06 field equivalence).

        The adapter used to rebuild the request through ``normalize_prepare`` and
        send the normalised subset, so a field the Controller's DTO declares but
        the local normaliser did not know was silently dropped before validation.
        The request now reaches the Controller exactly as the caller wrote it (the
        route normalises once, server-side); the frozen field policy is applied
        first so an undeclared field is the same ``input_invalid`` the HTTP entry
        reports.
        """
        del workspace_id
        payload = dict(request)
        key = str(idempotency_key or "").strip()
        if key:
            payload.setdefault("idempotency_key", key)
        try:
            validate_request_fields(payload, request_kind=REQUEST_PREPARE, entry=ENTRY_MCP)
            record = dict(self._client.prepare(payload))
        except ApplicationParamError as exc:
            raise _param_error("prepare", exc) from None
        except ControllerClientError as exc:
            raise _tool_error("prepare", exc) from None
        operation_ref = record.get("operation_ref") or record.get("operation_id") or ""
        payload_out = dict(record)
        payload_out["operation_ref"] = operation_ref
        payload_out.setdefault(
            "reference",
            {"kind": "operation", "id": str(operation_ref)},
        )
        return payload_out

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


class _ControllerGuidance:
    """Read-only guidance forward (plan 2026-10-30 §5: 指南读取).

    The port is a thin forward to the Controller route; version/digest and the
    explicit-omission budget policy live in the content pack behind the route,
    so this adapter adds no second guidance authority.
    """

    def __init__(self, client: ControllerClient) -> None:
        self._client = client

    def guidance(self, request: Mapping[str, Any], *, workspace_id: str) -> Any:
        del workspace_id
        guide_id = str(request.get("guide_id") or "").strip()
        if not guide_id:
            raise mcp.McpToolError(
                mo.ErrorCode.INPUT_INVALID,
                "guidance read requires a non-empty guide_id",
                params={"field": "guide_id", "rule": "missing_required_field"},
                retryable=False,
            )
        section = request.get("section")
        version = request.get("version")
        max_bytes = request.get("max_bytes")
        try:
            return dict(
                self._client.guidance(
                    guide_id,
                    section=str(section) if section is not None else None,
                    version=str(version) if version is not None else None,
                    max_bytes=int(max_bytes) if max_bytes is not None else None,
                )
            )
        except ControllerClientError as exc:
            raise _tool_error("guidance", exc) from None
        except (TypeError, ValueError) as exc:
            raise mcp.McpToolError(
                mo.ErrorCode.INPUT_INVALID,
                f"invalid guidance request: {exc}",
                params={"field": "max_bytes", "rule": "invalid_type"},
                retryable=False,
            ) from None


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
    controller_url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    api_key: str | None = None,
) -> McpRuntime:
    """Bind every MCP tool to one Controller application command.

    The in-process ``WorkspaceBudget`` is deliberately not wired here: B09 makes
    the Controller's server-side admission ledger the only resource authority,
    so a client/tool core hint can never override the server grant.

    With *api_key* the proxy is one user's transport: it asks the Controller for
    the resolved principal/project and binds its mapper to that answer, with the
    key's actions intersected with :data:`mcp.STDIO_PERMISSION_CEILING`.  Without
    a key the adapter keeps the existing trusted ``local-single`` stdio scope.

    Raises:
        ControllerClientError: the Controller is unreachable/unhealthy, or a
            supplied credential could not be resolved.
    """
    client = ControllerClient(controller_url, timeout=timeout, api_key=api_key)
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
        guidance=_ControllerGuidance(client),
        # M2-01c: the ten research tools reach the Controller application service
        # through the one transport; the adapter holds no research store.
        research=ControllerResearchPort(client),
        consumption=ControllerConsumptionPort(client),
    )
    identity = _stdio_identity_mapper(client, api_key)
    server = mcp.McpServer(
        backends=backends,
        identity=identity,
        configured_tier=mcp.STDIO_PERMISSION_SCOPE,
        # M0-09: the research extension is advertised/negotiated through
        # `acp_capabilities`; `initialize` makes no extension claim.
        extension=RESEARCH_EXTENSION_SPEC,
    )
    return McpRuntime(server=server, client=client, health=health)


def _stdio_identity_mapper(client: ControllerClient, api_key: str | None) -> mcp.IdentityMapper:
    """Bind the stdio mapper to the Controller-resolved principal (B15/M0-05).

    Without a key this is the trusted ``local-single`` operator entry.  With a
    key the binding is the Controller's ``/api/identity`` answer — never a
    client claim — and it is **revalidated before every tool call**, so a
    revoked key refuses reads, writes and control on an already-open stdio
    session (G25).
    """
    if not api_key:
        return mcp.IdentityMapper(configured_tier=mcp.STDIO_PERMISSION_SCOPE)

    def _resolve_binding() -> mcp.StdioCredentialBinding:
        try:
            return mcp.StdioCredentialBinding.from_identity_response(client.identity())
        except ControllerClientError as exc:
            raise _tool_error("identity", exc) from None
        except (TypeError, ValueError) as exc:
            raise mcp.McpToolError(
                mo.ErrorCode.ACCESS_DENIED,
                "the Controller no longer resolves this credential",
                params={"command": "identity", "exception": type(exc).__name__},
                retryable=False,
            ) from None

    binding = _resolve_binding()
    logger.info(
        "MCP stdio identity bound to principal=%s project=%s actions=%s",
        binding.principal.principal_id,
        binding.verified_project,
        list(binding.actions),
    )
    return mcp.IdentityMapper(
        configured_tier=mcp.STDIO_PERMISSION_SCOPE,
        permission_scope=mcp.STDIO_MULTIUSER_SCOPE,
        credential=binding,
        revalidate=_resolve_binding,
    )


def build_mcp_server(
    controller_url: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> mcp.McpServer:
    """Convenience wrapper returning only the protocol server."""
    return build_mcp_runtime(controller_url, timeout=timeout).server
