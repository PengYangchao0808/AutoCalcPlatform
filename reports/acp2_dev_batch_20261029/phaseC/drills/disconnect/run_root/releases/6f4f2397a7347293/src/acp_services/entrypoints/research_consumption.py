"""Additive, negotiated research consumer tools; no execution or store owner."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import quote, urlencode

from acp_services.contracts.access import AccessAction
from acp_services.entrypoints.controller_client import ControllerClient, ControllerClientError

__all__ = [
    "CONSUMPTION_ROUTES",
    "CONSUMPTION_VERSION",
    "CONSUMPTION_TOOL_NAMES",
    "CONSUMPTION_SCHEMAS",
    "CONSUMPTION_ACTIONS",
    "ConsumptionPort",
    "ControllerConsumptionPort",
]

# The P2 ten-tool promise remains compatible. Consumers require this additional
# observed surface after the same research-extension handshake.
CONSUMPTION_VERSION = "1.0.0"
CONSUMPTION_ROUTES = {
    "acp_research_model_plan_v1": ("POST", "/api/v2/model/plan"),
    "acp_research_model_usage_v1": ("GET", "/api/v2/model/usage"),
    "acp_research_model_statement_v1": ("GET", "/api/v2/model/statement"),
    "acp_research_record_create_v1": ("POST", "/api/v2/research/records"),
    "acp_research_record_read_v1": ("GET", "/api/v2/research/records/{research_id}"),
    "acp_research_record_append_v1": ("POST", "/api/v2/research/records/{research_id}/rounds"),
    "acp_research_evidence_read_v1": ("POST", "/api/v2/research/evidence/read"),
    "acp_research_budget_read_v1": ("GET", "/api/v2/research/usage"),
    "acp_research_checkpoint_v1": ("POST", "/api/v2/research/proposals/{proposal_ref}/checkpoint"),
    "acp_research_readiness_v1": ("GET", "/api/v2/research/readiness"),
}
CONSUMPTION_TOOL_NAMES = tuple(CONSUMPTION_ROUTES)
_FIELDS = {
    "model_plan": (
        "phase round messages max_output_tokens model session_seed project_id research_id",
        "messages model session_seed",
    ),
    "model_usage": ("session_seed project_id", "session_seed"),
    "model_statement": ("", ""),
    "record_create": (
        "project_id session_seed research_id proposal_ref approval_ref plan_definition_digest "
        "round decision task_refs evidence_refs evidence_view step_identity_refs "
        "missing_validation human_edits created_at",
        "session_seed proposal_ref approval_ref plan_definition_digest round decision "
        "task_refs evidence_refs",
    ),
    "record_read": ("research_id project_id", "research_id project_id"),
    "record_append": (
        "research_id project_id session_seed expected_revision record evidence_view "
        "idempotency_key",
        "research_id expected_revision record session_seed",
    ),
    "evidence_read": (
        "project_id session_seed research_id round task_ref report_ref",
        "session_seed research_id round task_ref report_ref",
    ),
    "budget_read": ("proposal_ref session_seed project_id", "proposal_ref session_seed"),
    "checkpoint": (
        "proposal_ref project_id session_seed expected_revision plan entry",
        "proposal_ref session_seed expected_revision plan entry",
    ),
    "readiness": ("project_id", ""),
}
_INTEGER_FIELDS = {"round", "max_output_tokens", "revision", "expected_revision"}
_OBJECT_FIELDS = {"task_ref", "report_ref", "evidence_view", "budget", "record", "plan", "entry"}
_ARRAY_FIELDS = {
    "messages",
    "task_refs",
    "evidence_refs",
    "step_identity_refs",
    "missing_validation",
    "human_edits",
}


def _field_schema(field: str) -> dict[str, Any]:
    if field in _INTEGER_FIELDS:
        return {"type": "integer", "minimum": 0 if field == "round" else 1}
    if field in _OBJECT_FIELDS:
        return {"type": "object"}
    if field in _ARRAY_FIELDS:
        return {"type": "array", "items": {"type": "object"}}
    return {"type": "string"}


CONSUMPTION_SCHEMAS = {
    name: {
        "type": "object",
        "properties": {
            field: _field_schema(field)
            for field in _FIELDS[name.removeprefix("acp_research_").removesuffix("_v1")][0].split()
        },
        "required": _FIELDS[name.removeprefix("acp_research_").removesuffix("_v1")][1].split(),
        "additionalProperties": False,
    }
    for name in CONSUMPTION_TOOL_NAMES
}
CONSUMPTION_ACTIONS = {
    name: AccessAction.WRITE
    if method == "POST" and "evidence_read" not in name
    else AccessAction.READ
    for name, (method, _) in CONSUMPTION_ROUTES.items()
}


class ConsumptionPort(Protocol):
    """Versioned application commands over the existing Controller transport."""

    def call(self, tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]: ...


class ControllerConsumptionPort:
    """Literal route mapping; path references are escaped, never interpreted."""

    def __init__(self, client: ControllerClient) -> None:
        self._client = client

    def call(self, tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        from acp_services.entrypoints.mcp import McpToolError
        from acp_services.entrypoints.research_port import _tool_error

        method, path = CONSUMPTION_ROUTES[tool]
        payload = dict(arguments)
        for field in ("research_id", "budget_id", "proposal_ref"):
            marker = "{" + field + "}"
            if marker in path:
                path = path.replace(marker, quote(str(payload.pop(field)), safe=""))
        if method == "GET" and payload:
            path += "?" + urlencode(payload)
        try:
            return self._client.research_consume(
                method, path, payload if method == "POST" else None
            )
        except ControllerClientError as exc:
            raise _tool_error(tool, exc) from None
        except (TypeError, ValueError) as exc:
            raise McpToolError("input_invalid", str(exc)) from None
