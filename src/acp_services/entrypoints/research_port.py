"""Controller-backed research port for the MCP adapter (M2-01c).

The ten MCP research tools are *live tools*: each one must reach the Controller's
application service through the one transport
(:class:`acp_services.entrypoints.controller_client.ControllerClient`), never a
local store.  This module is that adapter: one method per P2 operation, each
forwarding the already schema-validated arguments and mapping a Controller error
into a typed ``McpToolError`` (an in-band ``tools/call`` refusal, never a
transport error and never a fabricated success).

The port deliberately contains no policy:

* it never decides an approval, never mints a revision and never re-derives a
  digest — the Controller owns all three;
* identity is server-resolved on both sides: the MCP session's binding is passed
  as the advisory project scope only where the contract declares one, and the
  Controller re-resolves the principal from its own credential;
* the wait timeout is bounded by the contract (``max_wait_s <= 60``) before the
  request leaves this process.

Isolation: stdlib plus first-party ``acp_services``; ``acp`` is never imported.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from acp_services.entrypoints import machine_output as mo
from acp_services.entrypoints.controller_client import (
    P2_OPERATION_METHODS,
    ControllerClient,
    ControllerClientError,
    InvalidParameterError,
)
from acp_services.entrypoints.mcp import McpToolError

__all__ = ["ControllerResearchPort"]

logger = logging.getLogger(__name__)

#: Controller error code -> MCP error code (the same mapping the P1 ports use).
_TOOL_ERROR_CODES: dict[str, str] = {
    "input_invalid": mo.ErrorCode.INPUT_INVALID,
    "unsupported_capability": mo.ErrorCode.UNSUPPORTED_CAPABILITY,
    "revision_conflict": mo.ErrorCode.REVISION_CONFLICT,
    "idempotency_key_reuse": mo.ErrorCode.REVISION_CONFLICT,
    "resource_not_ready": mo.ErrorCode.RESOURCE_NOT_READY,
    "resource_unavailable": mo.ErrorCode.RESOURCE_UNAVAILABLE,
    "access_denied": mo.ErrorCode.ACCESS_DENIED,
    "not_found": mo.ErrorCode.RESOURCE_NOT_READY,
    "backend_unavailable": mo.ErrorCode.BACKEND_UNAVAILABLE,
    "bound_exceeded": mo.ErrorCode.INPUT_INVALID,
    "missing_science_field": mo.ErrorCode.INPUT_INVALID,
    "plan_digest_mismatch": mo.ErrorCode.INPUT_INVALID,
    "proposal_digest_mismatch": mo.ErrorCode.INPUT_INVALID,
}


def _tool_error(operation: str, exc: ControllerClientError) -> McpToolError:
    code = _TOOL_ERROR_CODES.get(exc.code, mo.ErrorCode.BACKEND_FAILURE)
    return McpToolError(
        code,
        exc.message,
        params={"operation": operation, **dict(exc.params)},
        retryable=exc.retryable,
    )


def _invalid(operation: str, message: str, field: str) -> McpToolError:
    return McpToolError(
        mo.ErrorCode.INPUT_INVALID,
        message,
        params={"operation": operation, "field": field, "rule": "missing_required_field"},
        retryable=False,
    )


class ControllerResearchPort:
    """The ten P2 operations over the Controller's HTTP application commands."""

    def __init__(self, client: ControllerClient) -> None:
        # Duck-typed on purpose: any object exposing the ten P2 client methods is
        # a valid transport (the frozen client is the production one, tests use a
        # recording double).
        missing = [
            method
            for method in P2_OPERATION_METHODS.values()
            if not callable(getattr(client, method, None))
        ]
        if missing:
            raise TypeError(f"research port client lacks: {', '.join(sorted(missing))}")
        self._client = client

    # -- P2-R1 -------------------------------------------------------------

    def proposal_create(self, arguments: Mapping[str, Any], *, workspace_id: str) -> Any:
        del workspace_id  # the Controller re-resolves the project from its credential
        request = {
            "proposal": dict(arguments.get("proposal") or {}),
            "plan": dict(arguments.get("plan") or {}),
        }
        for name in ("project_id", "session_seed", "budget", "expires_at", "idempotency_key"):
            if arguments.get(name) is not None:
                request[name] = arguments[name]
        try:
            return dict(self._client.create_proposal(request))
        except ControllerClientError as exc:
            raise _tool_error("proposal_create", exc) from None

    def proposal_read(self, arguments: Mapping[str, Any], *, workspace_id: str) -> Any:
        del workspace_id
        proposal_ref = str(arguments.get("proposal_ref") or "").strip()
        if not proposal_ref:
            raise _invalid("proposal_read", "proposal_ref is required", "proposal_ref")
        try:
            return dict(
                self._client.read_proposal(
                    proposal_ref,
                    project_id=arguments.get("project_id"),
                    revision=arguments.get("revision"),
                )
            )
        except ControllerClientError as exc:
            raise _tool_error("proposal_read", exc) from None

    def proposal_update_cas(self, arguments: Mapping[str, Any], *, workspace_id: str) -> Any:
        del workspace_id
        proposal_ref = str(arguments.get("proposal_ref") or "").strip()
        if not proposal_ref:
            raise _invalid("proposal_update_cas", "proposal_ref is required", "proposal_ref")
        request = {
            "proposal": dict(arguments.get("proposal") or {}),
            "plan": dict(arguments.get("plan") or {}),
            "expected_revision": int(arguments.get("expected_revision") or 0),
        }
        for name in ("project_id", "session_seed", "budget", "expires_at", "idempotency_key"):
            if arguments.get(name) is not None:
                request[name] = arguments[name]
        try:
            return dict(self._client.update_proposal_cas(proposal_ref, request))
        except ControllerClientError as exc:
            raise _tool_error("proposal_update_cas", exc) from None

    # -- P2-R2 / P2-R3 -----------------------------------------------------

    def approval_request(self, arguments: Mapping[str, Any], *, workspace_id: str) -> Any:
        del workspace_id
        request = {
            "proposal_ref": str(arguments.get("proposal_ref") or ""),
            "plan_digest": str(arguments.get("plan_digest") or ""),
        }
        for name in (
            "plan",
            "proposal",
            "proposal_digest",
            "science_digest",
            "budget",
            "allowed_change_range",
            "expires_at",
            "idempotency_key",
        ):
            if arguments.get(name) is not None:
                request[name] = arguments[name]
        if arguments.get("comment"):
            request["comment"] = str(arguments["comment"])
        try:
            return dict(self._client.request_approval(request))
        except ControllerClientError as exc:
            raise _tool_error("approval_request", exc) from None

    def approval_query(self, arguments: Mapping[str, Any], *, workspace_id: str) -> Any:
        del workspace_id
        try:
            return dict(
                self._client.query_approval(
                    approval_ref=arguments.get("approval_ref"),
                    proposal_ref=arguments.get("proposal_ref"),
                    status=arguments.get("status"),
                    cursor=arguments.get("cursor"),
                    limit=arguments.get("limit"),
                    project_id=arguments.get("project_id"),
                )
            )
        except ControllerClientError as exc:
            raise _tool_error("approval_query", exc) from None

    def approval_decide(self, arguments: Mapping[str, Any], *, workspace_id: str) -> Any:
        del workspace_id
        approval_ref = str(arguments.get("approval_ref") or "").strip()
        if not approval_ref:
            raise _invalid("approval_decide", "approval_ref is required", "approval_ref")
        try:
            return dict(
                self._client.decide_approval(
                    approval_ref,
                    decision=str(arguments.get("decision") or ""),
                    expected_revision=int(arguments.get("expected_revision") or 0),
                    reason=arguments.get("reason"),
                    idempotency_key=arguments.get("idempotency_key"),
                )
            )
        except InvalidParameterError as exc:
            raise _tool_error("approval_decide", exc) from None
        except ControllerClientError as exc:
            raise _tool_error("approval_decide", exc) from None

    def approval_revoke(self, arguments: Mapping[str, Any], *, workspace_id: str) -> Any:
        del workspace_id
        approval_ref = str(arguments.get("approval_ref") or "").strip()
        if not approval_ref:
            raise _invalid("approval_revoke", "approval_ref is required", "approval_ref")
        try:
            return dict(
                self._client.revoke_approval(
                    approval_ref,
                    expected_revision=int(arguments.get("expected_revision") or 0),
                    reason=arguments.get("reason"),
                    idempotency_key=arguments.get("idempotency_key"),
                )
            )
        except ControllerClientError as exc:
            raise _tool_error("approval_revoke", exc) from None

    # -- P2-R4 / P2-R5 -----------------------------------------------------

    def wait(self, arguments: Mapping[str, Any], *, workspace_id: str) -> Any:
        del workspace_id
        kinds = arguments.get("kinds")
        if not isinstance(kinds, list | tuple) or not kinds:
            raise _invalid("wait", "kinds must name at least one kind", "kinds")
        try:
            return dict(
                self._client.wait_research(
                    [str(kind) for kind in kinds],
                    cursor=arguments.get("cursor"),
                    max_wait_s=arguments.get("max_wait_s"),
                    limit=arguments.get("limit"),
                    project_id=arguments.get("project_id"),
                )
            )
        except ControllerClientError as exc:
            raise _tool_error("wait", exc) from None

    def snapshot(self, arguments: Mapping[str, Any], *, workspace_id: str) -> Any:
        del workspace_id
        refs = arguments.get("refs")
        if not isinstance(refs, list | tuple) or not refs:
            raise _invalid("snapshot", "refs must name at least one reference", "refs")
        try:
            return dict(
                self._client.snapshot_research(
                    [str(ref) for ref in refs], project_id=arguments.get("project_id")
                )
            )
        except ControllerClientError as exc:
            raise _tool_error("snapshot", exc) from None

    def resume(self, arguments: Mapping[str, Any], *, workspace_id: str) -> Any:
        del workspace_id
        try:
            return dict(
                self._client.resume_research(
                    str(arguments.get("proposal_ref") or ""),
                    str(arguments.get("plan_digest") or ""),
                    task_ref=arguments.get("task_ref"),
                    cursor=arguments.get("cursor"),
                    diagnostic_only=bool(arguments.get("diagnostic_only") or False),
                    project_id=arguments.get("project_id"),
                )
            )
        except ControllerClientError as exc:
            raise _tool_error("resume", exc) from None
