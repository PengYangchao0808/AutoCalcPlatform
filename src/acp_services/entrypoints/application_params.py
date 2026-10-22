"""Canonical parameter normalisation shared by every application entry.

B06 (plan ``.omo/plans/acp2-parallel-plan-b-runtime.md`` todo 7).  MCP, HTTP and
the Controller client must produce **the same parameters for the same logical
action**.  This module is the single normaliser: the MCP composition root
(:mod:`acp.services_mcp_bootstrap`) and the additive v1 routes both call these
functions, so a field is never interpreted differently per entry.

The module is stdlib-only and imports nothing from :mod:`acp`.  It performs
*shape* validation only — scientific/workflow validation stays in the ACP
catalog and the Controller route.  A normalisation failure raises
:class:`ApplicationParamError` carrying the frozen ``machine_output`` code
(``input_invalid``) so both the HTTP layer and the MCP layer can map it without
inventing a second vocabulary.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

__all__ = [
    "APPLICATION_SCHEMA_VERSION",
    "ApplicationParamError",
    "normalize_analyze",
    "normalize_preflight",
    "normalize_prepare",
    "normalize_read",
    "normalize_submit",
]

#: Contract major version accepted by every application command.
APPLICATION_SCHEMA_VERSION = 1

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_BUDGET_UNITS = frozenset({"task_count", "core_hours", "walltime_s"})


class ApplicationParamError(ValueError):
    """A parameter violates the frozen application contract.

    ``code`` is always a :class:`acp_services.entrypoints.machine_output.
    ErrorCode` value; ``params`` is structured context (never prose parsing).
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "input_invalid",
        params: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.params = dict(params) if params else {}


def _require_mapping(payload: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ApplicationParamError(
            f"{field} must be an object", params={"field": field, "rule": "type"}
        )
    return dict(payload)


def _schema_version(payload: Mapping[str, Any]) -> int:
    raw = payload.get("schema_version", APPLICATION_SCHEMA_VERSION)
    if raw != APPLICATION_SCHEMA_VERSION:
        raise ApplicationParamError(
            "unsupported application schema version",
            params={
                "field": "schema_version",
                "rule": "unsupported_major_version",
                "expected": APPLICATION_SCHEMA_VERSION,
                "actual": raw,
            },
        )
    return APPLICATION_SCHEMA_VERSION


def _string(payload: Mapping[str, Any], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ApplicationParamError(
            f"{field} must be a non-empty string",
            params={"field": field, "rule": "type"},
        )
    return value.strip()


def _optional_int(
    payload: Mapping[str, Any], field: str, *, minimum: int | None = None
) -> int | None:
    value = payload.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApplicationParamError(
            f"{field} must be an integer", params={"field": field, "rule": "type"}
        )
    if minimum is not None and value < minimum:
        raise ApplicationParamError(
            f"{field} must be >= {minimum}",
            params={"field": field, "rule": "minimum", "minimum": minimum},
        )
    return value


def _optional_digest(payload: Mapping[str, Any], field: str) -> str | None:
    value = _string(payload, field)
    if value is None:
        return None
    if not _DIGEST.match(value):
        raise ApplicationParamError(
            f"{field} must be a sha256: digest",
            params={"field": field, "rule": "pattern", "pattern": "^sha256:[0-9a-f]{64}$"},
        )
    return value


def _optional_idempotency(payload: Mapping[str, Any]) -> str | None:
    value = _string(payload, "idempotency_key")
    if value is None:
        return None
    if not _IDEMPOTENCY.match(value):
        raise ApplicationParamError(
            "idempotency_key is malformed",
            params={
                "field": "idempotency_key",
                "rule": "pattern",
                "pattern": "^[A-Za-z0-9._:-]{8,128}$",
            },
        )
    return value


def _normalize_budget(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    budget = _require_mapping(value, field="budget")
    unit = budget.get("unit")
    if unit not in _BUDGET_UNITS:
        raise ApplicationParamError(
            "budget.unit must be one of task_count/core_hours/walltime_s",
            params={"field": "budget.unit", "rule": "enum", "allowed": sorted(_BUDGET_UNITS)},
        )
    timeout_s = budget.get("timeout_s")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or timeout_s <= 0:
        raise ApplicationParamError(
            "budget.timeout_s must be a positive integer",
            params={"field": "budget.timeout_s", "rule": "minimum", "minimum": 1},
        )
    count = budget.get("count")
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 1):
        raise ApplicationParamError(
            "budget.count must be an integer >= 1",
            params={"field": "budget.count", "rule": "minimum", "minimum": 1},
        )
    normalized = {"unit": unit, "timeout_s": timeout_s}
    if count is not None:
        normalized["count"] = count
    return normalized


def _common_submit_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    _schema_version(payload)
    workflow = _string(payload, "workflow")
    if workflow is None:
        raise ApplicationParamError(
            "workflow is required", params={"field": "workflow", "rule": "required"}
        )
    input_value = payload.get("input")
    if input_value is None:
        raise ApplicationParamError(
            "input is required", params={"field": "input", "rule": "required"}
        )
    data: dict[str, Any] = {
        "schema_version": APPLICATION_SCHEMA_VERSION,
        "workflow": workflow,
        "input": _require_mapping(input_value, field="input"),
    }
    for field in ("method", "resources", "extensions"):
        value = payload.get(field)
        if value is not None:
            data[field] = _require_mapping(value, field=field)
    project_id = _string(payload, "project_id")
    if project_id is not None:
        data["project_id"] = project_id
    return data


def normalize_preflight(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise a zero-side-effect preflight request."""
    return _common_submit_fields(_require_mapping(payload, field="request"))


def normalize_submit(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise a submit request (additive B-req-01 research fields included)."""
    data = _common_submit_fields(_require_mapping(payload, field="request"))
    plan_digest = _optional_digest(payload, "plan_digest")
    if plan_digest is not None:
        data["plan_digest"] = plan_digest
    proposal_digest = _optional_digest(payload, "proposal_digest")
    if proposal_digest is not None:
        data["proposal_digest"] = proposal_digest
    proposal_ref = _string(payload, "proposal_ref")
    if proposal_ref is not None:
        data["proposal_ref"] = proposal_ref
    budget = _normalize_budget(payload.get("budget"))
    if budget is not None:
        data["budget"] = budget
    idempotency = _optional_idempotency(payload)
    if idempotency is not None:
        data["idempotency_key"] = idempotency
    if proposal_ref is not None and plan_digest is None:
        raise ApplicationParamError(
            "a research launch requires plan_digest",
            params={"field": "plan_digest", "rule": "plan_digest_required"},
        )
    return data


def normalize_prepare(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise an explicit prepare request."""
    request = _require_mapping(payload, field="request")
    _schema_version(request)
    purpose = _string(request, "purpose")
    if purpose not in {"artifact", "structure-input"}:
        raise ApplicationParamError(
            "purpose must be artifact or structure-input",
            params={
                "field": "purpose",
                "rule": "enum",
                "allowed": ["artifact", "structure-input"],
            },
        )
    data: dict[str, Any] = {"schema_version": APPLICATION_SCHEMA_VERSION, "purpose": purpose}
    task_ref = request.get("task_ref")
    if task_ref is not None:
        data["task_ref"] = _require_mapping(task_ref, field="task_ref")
    job_id = _string(request, "job_id")
    if job_id is not None:
        data["job_id"] = job_id
    artifact_id = _string(request, "artifact_id")
    if artifact_id is not None:
        data["artifact_id"] = artifact_id
    attempt = _optional_int(request, "attempt", minimum=1)
    if attempt is not None:
        data["attempt"] = attempt
    generation = _optional_int(request, "generation", minimum=1)
    if generation is not None:
        data["generation"] = generation
    expected_revision = _optional_int(request, "expected_revision", minimum=0)
    if expected_revision is not None:
        data["expected_revision"] = expected_revision
    relative_path = _string(request, "relative_path")
    if relative_path is not None:
        data["relative_path"] = relative_path
    # B12 artifact facts: accepted verbatim (the executor re-derives the
    # committed digest/size; a supplied digest/size is a trusted expectation).
    for field in ("expected_digest", "content_digest", "media_type", "schema_ref"):
        value = _string(request, field)
        if value is not None:
            data[field] = value
    expected_size = _optional_int(request, "expected_size_bytes", minimum=0)
    if expected_size is not None:
        data["expected_size_bytes"] = expected_size
    source_refs = request.get("source_refs")
    if source_refs is not None:
        if not isinstance(source_refs, list) or not all(
            isinstance(item, Mapping) for item in source_refs
        ):
            raise ApplicationParamError(
                "source_refs must be an array of objects",
                params={"field": "source_refs", "rule": "type"},
            )
        data["source_refs"] = [dict(item) for item in source_refs]
    evidence_report = request.get("evidence_report")
    if evidence_report is not None:
        if not isinstance(evidence_report, Mapping):
            raise ApplicationParamError(
                "evidence_report must be an object",
                params={"field": "evidence_report", "rule": "type"},
            )
        data["evidence_report"] = dict(evidence_report)
    idempotency = _optional_idempotency(request)
    if idempotency is not None:
        data["idempotency_key"] = idempotency
    if purpose == "artifact":
        if "task_ref" not in data and "job_id" not in data:
            raise ApplicationParamError(
                "prepare(purpose=artifact) requires task_ref or job_id",
                params={"field": "task_ref", "rule": "required"},
            )
        if artifact_id is None:
            raise ApplicationParamError(
                "prepare(purpose=artifact) requires artifact_id",
                params={"field": "artifact_id", "rule": "required"},
            )
    return data


def normalize_read(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise a cache-only artifact read request."""
    request = _require_mapping(payload, field="request")
    _schema_version(request)
    artifact_ref = request.get("artifact_ref")
    if artifact_ref is None:
        raise ApplicationParamError(
            "artifact_ref is required", params={"field": "artifact_ref", "rule": "required"}
        )
    data: dict[str, Any] = {
        "schema_version": APPLICATION_SCHEMA_VERSION,
        "artifact_ref": _require_mapping(artifact_ref, field="artifact_ref"),
    }
    for field in ("max_bytes", "encoding"):
        value = request.get(field)
        if value is not None:
            data[field] = value
    return data


def normalize_analyze(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise an explicit analysis request (``explicit=true`` is mandatory)."""
    request = _require_mapping(payload, field="request")
    _schema_version(request)
    if request.get("explicit") is not True:
        raise ApplicationParamError(
            "analysis requires explicit=true",
            params={"field": "explicit", "rule": "explicit_required"},
        )
    analysis_id = _string(request, "analysis_id")
    if analysis_id is None:
        raise ApplicationParamError(
            "analysis_id is required", params={"field": "analysis_id", "rule": "required"}
        )
    input_refs = request.get("input_refs")
    if not isinstance(input_refs, list) or not input_refs:
        raise ApplicationParamError(
            "input_refs must be a non-empty array",
            params={"field": "input_refs", "rule": "required"},
        )
    normalized_refs = [
        _require_mapping(item, field=f"input_refs[{index}]")
        for index, item in enumerate(input_refs)
    ]
    data: dict[str, Any] = {
        "schema_version": APPLICATION_SCHEMA_VERSION,
        "analysis_id": analysis_id,
        "input_refs": normalized_refs,
        "explicit": True,
    }
    parameters = request.get("parameters")
    if parameters is not None:
        data["parameters"] = _require_mapping(parameters, field="parameters")
    plan_digest = _optional_digest(request, "plan_digest")
    if plan_digest is not None:
        data["plan_digest"] = plan_digest
    idempotency = _optional_idempotency(request)
    if idempotency is not None:
        data["idempotency_key"] = idempotency
    return data
