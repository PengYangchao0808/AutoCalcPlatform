"""One budget mapping across the three budget producers (M2-03c / G12 + G13).

Three surfaces describe the same spend with three vocabularies:

======================================  ==============================================
producer                                shape
======================================  ==============================================
``application_params`` (v1 wire)         ``{"unit", "count", "timeout_s"}``
``research_budget`` / D09 (approval)     ``{"wall_seconds", "queue_seconds",
                                         "input_tokens", ..., "units"}``
RPH ``PlanBudget`` (C client)            ``{"model_requests", "task_refs",
                                         "recipe_steps", "qc_invocations",
                                         "wall_seconds", "queue_seconds",
                                         "input_tokens", "output_tokens"}``
======================================  ==============================================

Before this module each side parsed its own vocabulary, so:

* an RPH ``PlanBudget`` forwarded verbatim to the v1 submit field policy was a
  ``422 unknown_field`` with no hint which field was the mapped one;
* ``{"unit": "core_hours"}`` / ``{"unit": "walltime_s"}`` passed the v1 policy
  (they are registered launch units) and then raised a bare
  ``UsageUnitValidationError`` inside the ledger — an unhandled 500;
* a non-empty but unregistered approval budget reached the ledger and either
  raised or was read as "no limits".

This module is the single mapping.  It never invents a value: a missing bound
stays missing (``None``), a non-empty unknown shape is a typed
:class:`BudgetRequestError`, and the RPH field names come from
:func:`acp_services.runtime.canonical_digest`'s ``C_PLAN_BUDGET_FIELDS`` (the
frozen Proposal projection) instead of a private copy.

Isolation: stdlib + first-party ``acp_services`` only (no ``acp`` import).

Author: ACP Services (B lane, M2-03c).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from acp_services.runtime.canonical_digest import C_PLAN_BUDGET_FIELDS
from acp_services.runtime.execution_limits import (
    LAUNCH_BUDGET_UNITS,
    BudgetShape,
    ExecutionLimitError,
    ResourceLimits,
    bind_budget_shape,
    bind_launch_budget_shape,
    resolve_resource_limits,
)
from acp_services.runtime.research_budget import BudgetConfigurationError, BudgetPolicy
from acp_services.runtime.usage_units import (
    UsageUnitValidationError,
    normalize_usage_unit,
)

__all__ = [
    "BUDGET_PROJECTION_SCHEMA",
    "PLATFORM_BUDGET_UNITS",
    "PLAN_BUDGET_UNIT_FIELDS",
    "BudgetRequest",
    "BudgetRequestError",
    "budget_request_from_approval",
    "budget_request_from_plan",
    "plan_budget_to_execution_shape",
    "plan_budget_to_platform_budget",
    "plan_budget_to_unit_limits",
    "platform_budget_to_execution_shape",
    "require_budget_policy",
    "unwrap_budget_error",
]

#: Contract identity of this mapping.
BUDGET_PROJECTION_SCHEMA = "acp2.runtime.budget-projection.v1"

#: Units the v1 launch field accepts (``application_params._BUDGET_UNITS``).
PLATFORM_BUDGET_UNITS: tuple[str, ...] = ("task_count", "core_hours", "walltime_s")

#: RPH ``PlanBudget`` count fields and the usage unit each one charges.
PLAN_BUDGET_UNIT_FIELDS: Mapping[str, str] = {
    "model_requests": "model_request",
    "task_refs": "task_ref",
    "recipe_steps": "recipe_step",
    "qc_invocations": "qc_invocation",
}

#: The exact RPH ``PlanBudget`` field set (frozen by the Proposal projection).
PLAN_BUDGET_FIELDS: tuple[str, ...] = tuple(C_PLAN_BUDGET_FIELDS)


class BudgetRequestError(ValueError):
    """A budget request is invalid (typed, never a 500 and never empty limits).

    Attributes:
        rule: Stable machine rule (``unknown_budget_field`` / ``invalid_budget``).
        message: Human-readable explanation.
        params: Extra structured detail for the application error envelope.
        http_status: The status this refusal answers at the application boundary.
    """

    def __init__(
        self,
        rule: str,
        message: str,
        *,
        params: Mapping[str, Any] | None = None,
        http_status: int = 400,
    ) -> None:
        super().__init__(f"{rule}: {message}")
        self.rule = rule
        self.message = message
        self.params = dict(params or {})
        self.http_status = int(http_status)

    def detail(self) -> dict[str, Any]:
        return {
            "code": "input_invalid",
            "message": self.message,
            "params": {"rule": self.rule, **self.params},
            "retryable": False,
        }


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BudgetRequestError("invalid_budget", f"{label} must be an integer")
    if value < 0:
        raise BudgetRequestError("invalid_budget", f"{label} must be non-negative")
    return int(value)


def _is_plan_budget(source: Mapping[str, Any]) -> bool:
    return any(name in source for name in PLAN_BUDGET_UNIT_FIELDS)


def plan_budget_to_unit_limits(plan_budget: Mapping[str, Any] | None) -> dict[str, int]:
    """Map one RPH ``PlanBudget`` onto separate usage-unit limits.

    Only the charged units are returned (``task_ref``/``recipe_step``/
    ``qc_invocation``/``model_request``); time and token members are shape, not
    units, and a zero means "no allowance of that unit" — it is never dropped.
    """
    source = dict(plan_budget or {})
    unknown = sorted(str(key) for key in source if key not in PLAN_BUDGET_FIELDS)
    if unknown:
        raise BudgetRequestError(
            "unknown_budget_field",
            "budget carries field(s) outside the PlanBudget contract: " + ", ".join(unknown),
            params={"fields": unknown, "allowed": list(PLAN_BUDGET_FIELDS)},
            http_status=422,
        )
    limits: dict[str, int] = {}
    for name, unit in PLAN_BUDGET_UNIT_FIELDS.items():
        if name not in source or source[name] is None:
            continue
        limits[unit] = _integer(source[name], f"budget.{name}")
    return limits


def plan_budget_to_execution_shape(plan_budget: Mapping[str, Any] | None) -> dict[str, Any]:
    """Map one RPH ``PlanBudget`` onto the D09 execution shape (no units).

    Zero/absent time values stay absent: RPH's ``nonNegative`` maps ``0`` to
    "not limited", so an empty bound is never published as a limit.
    """
    source = dict(plan_budget or {})
    # Reuse the unit validation for the field set (no second field list).
    plan_budget_to_unit_limits(source)
    shape: dict[str, Any] = {}
    for name, key in (
        ("wall_seconds", "wall_seconds"),
        ("queue_seconds", "queue_seconds"),
        ("input_tokens", "input_tokens"),
        ("output_tokens", "output_tokens"),
    ):
        value = _integer(source[name], f"budget.{name}") if source.get(name) is not None else None
        if value:
            shape[key] = value
    return shape


def plan_budget_to_platform_budget(plan_budget: Mapping[str, Any] | None) -> dict[str, Any]:
    """Project one RPH ``PlanBudget`` onto the v1 wire budget.

    This is the Python twin of RPH's ``toPlatformBudget``: ``unit`` is
    ``task_count``, ``count`` is ``task_refs`` and ``timeout_s`` is the wall
    bound (``None`` when the plan is unbounded — the caller then omits the
    budget instead of inventing a default).
    """
    source = dict(plan_budget or {})
    limits = plan_budget_to_unit_limits(source)
    count = limits.get("task_ref")
    wall = plan_budget_to_execution_shape(source).get("wall_seconds")
    payload: dict[str, Any] = {"unit": "task_count", "timeout_s": wall}
    if count is not None:
        payload["count"] = count
    return payload


def platform_budget_to_execution_shape(
    budget: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Bridge the v1 launch unit vocabulary onto the D09 execution shape.

    ``task_count`` is a unit (``task_ref``), while ``walltime_s``/``core_hours``
    are *shape* — the pre-M2-03 ledger fed both into
    :func:`~acp_services.runtime.usage_units.normalize_usage_unit` and raised an
    unhandled ``UsageUnitValidationError`` for the latter two.  The bridge itself
    is :func:`~acp_services.runtime.execution_limits.bind_launch_budget_shape`;
    this function only returns its shape payload.
    """
    source = dict(budget or {})
    if not source:
        return {}
    unit = str(source.get("unit") or "").strip()
    if unit and unit not in LAUNCH_BUDGET_UNITS:
        raise BudgetRequestError(
            "unknown_budget_unit",
            f"budget.unit {unit!r} is not one of {list(LAUNCH_BUDGET_UNITS)}",
            params={"field": "budget.unit", "allowed": list(LAUNCH_BUDGET_UNITS)},
            http_status=422,
        )
    try:
        _, shape = bind_launch_budget_shape(source)
    except ExecutionLimitError as exc:
        raise BudgetRequestError(exc.rule, exc.message, params=dict(exc.params)) from exc
    payload = shape.to_dict()
    # The statement members are not budget fields; keep only the D09 shape keys.
    return {
        key: value
        for key, value in payload.items()
        if key in {"wall_seconds", "queue_seconds", "input_tokens", "output_tokens", "core_hours"}
        and value is not None
    }


@dataclass(frozen=True, slots=True)
class BudgetRequest:
    """One normalized budget request: unit limits + execution shape + surfaces."""

    limits: Mapping[str, int] = field(default_factory=dict)
    shape: BudgetShape = field(default_factory=BudgetShape)
    resources: ResourceLimits = field(default_factory=ResourceLimits)
    source_format: str = "empty"
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def bounded(self) -> bool:
        return bool(self.limits) or self.shape.bounded

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": BUDGET_PROJECTION_SCHEMA,
            "source_format": self.source_format,
            "limits": dict(self.limits),
            "shape": self.shape.to_dict(),
            "resources": self.resources.to_dict(),
            "bounded": self.bounded,
        }


def budget_request_from_plan(plan_budget: Mapping[str, Any] | None) -> BudgetRequest:
    """Normalize one RPH ``PlanBudget`` (units + D09 shape + resource surfaces)."""
    source = dict(plan_budget or {})
    limits = plan_budget_to_unit_limits(source)
    shape_payload = plan_budget_to_execution_shape(source)
    return _request(limits, shape_payload, "plan-budget", source)


def budget_request_from_approval(budget: Mapping[str, Any] | None) -> BudgetRequest:
    """Normalize one approval/v1 budget, accepting every frozen vocabulary.

    Recognized formats (in this order): an empty budget, the RPH ``PlanBudget``,
    and the D09/v1 union (``unit``/``count``/``timeout_s`` plus
    ``wall_seconds``/``queue_seconds``/tokens/``units``/``unit_limits``).
    Anything else is a typed :class:`BudgetRequestError` — never "no limits".
    """
    source = dict(budget or {})
    if not source:
        return _request({}, {}, "empty", source)
    if _is_plan_budget(source):
        return budget_request_from_plan(source)
    unit = str(source.get("unit") or "").strip()
    if unit and unit not in PLATFORM_BUDGET_UNITS:
        raise BudgetRequestError(
            "unknown_budget_unit",
            f"budget.unit {unit!r} is not one of {list(PLATFORM_BUDGET_UNITS)}",
            params={"field": "budget.unit", "allowed": list(PLATFORM_BUDGET_UNITS)},
            http_status=422,
        )
    try:
        unit_limits, shape = bind_launch_budget_shape(source)
    except ExecutionLimitError as exc:
        raise BudgetRequestError(exc.rule, exc.message, params=dict(exc.params)) from exc
    limits: dict[str, int] = {}
    for raw_unit, value in unit_limits.items():
        try:
            canonical = normalize_usage_unit(raw_unit)
        except UsageUnitValidationError as exc:
            raise BudgetRequestError(
                "unknown_budget_unit",
                str(exc),
                params={"field": f"budget.units.{raw_unit}"},
                http_status=422,
            ) from exc
        limits[canonical] = _integer(value, f"budget.units.{raw_unit}")
    return BudgetRequest(
        limits=limits,
        shape=shape,
        resources=resolve_resource_limits(platform_budget_to_execution_shape(source)),
        source_format="execution-shape",
        raw=source,
    )


def _request(
    limits: Mapping[str, int],
    shape_payload: Mapping[str, Any],
    source_format: str,
    raw: Mapping[str, Any],
) -> BudgetRequest:
    try:
        shape = bind_budget_shape(shape_payload)
    except ExecutionLimitError as exc:
        raise BudgetRequestError(exc.rule, exc.message, params=dict(exc.params)) from exc
    return BudgetRequest(
        limits=dict(limits),
        shape=shape,
        resources=resolve_resource_limits(shape_payload),
        source_format=source_format,
        raw=dict(raw),
    )


def require_budget_policy(
    request: BudgetRequest,
    *,
    budget_id: str,
    approved: Mapping[str, Any] | None = None,
    approval_ref: str = "",
    project_id: str = "",
) -> BudgetPolicy:
    """Build the durable :class:`BudgetPolicy` for one normalized request.

    ``approved`` is the approved range (defaults to the request itself).  A
    requested limit above the approved range is a
    :class:`BudgetRequestError` (``budget_exceeds_approved_range``), not a
    silently clamped policy.
    """
    approved_request = request if approved is None else budget_request_from_approval(approved)
    try:
        return BudgetPolicy(
            budget_id=str(budget_id),
            limits=dict(request.limits),
            approved_limits=dict(approved_request.limits),
            timeouts=dict(request.shape.timeouts),
            approval_ref=str(approval_ref),
            project_id=str(project_id),
            shape=request.shape.to_dict(),
        )
    except BudgetConfigurationError as exc:
        raise BudgetRequestError(
            "budget_exceeds_approved_range",
            str(exc),
            params={"budget_id": str(budget_id)},
            http_status=422,
        ) from exc


def unwrap_budget_error(exc: BaseException) -> BudgetRequestError:
    """Wrap one ledger/policy failure into the single typed budget error.

    ``UsageUnitValidationError`` (a bare ``ValueError`` from the unit counter),
    ``ExecutionLimitError`` and ``BudgetConfigurationError`` all become one
    :class:`BudgetRequestError` so no surface answers 500 and none reports
    "empty limits".
    """
    if isinstance(exc, BudgetRequestError):
        return exc
    if isinstance(exc, UsageUnitValidationError):
        return BudgetRequestError("unknown_budget_unit", str(exc), http_status=422)
    if isinstance(exc, ExecutionLimitError):
        return BudgetRequestError(exc.rule, exc.message, params=dict(exc.params))
    if isinstance(exc, BudgetConfigurationError):
        return BudgetRequestError("invalid_budget", str(exc))
    raise exc
