# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""CASSCF / NEVPT2 — ACP compat wrapper (plan todo 22).

The task core lives in :mod:`cccp.calculation.tasks.casscf`: ORCA-only
dispatch, active-space spec validation, the single ``casscf`` capability call,
the scientific ``active_space.json`` records and the typed
:class:`~cccp.calculation.results.CasscfPayload`.  This module is the ACP-side
compat surface: the legacy ``run_casscf(req)`` entry (a pure forwarder), the
legacy ``CalculationRequest`` → typed ``TaskRequest`` conversion
(:mod:`acp.calculations.legacy_adapters`), and the legacy
``CalculationResult`` envelope mapping.  Legacy semantics are preserved: a
pre-launch input problem (missing/invalid active space) yields a FAILED
``CalculationResult`` — it never raises.
"""

from __future__ import annotations

import logging

from acp.calculations.contracts import (
    CalculationRequest,
    CalculationResult,
    JsonValue,
    Provenance,
)
from acp.calculations.legacy_adapters import LegacyBinding, to_legacy_result, to_task_request
from acp.calculations.primitives._common import capability_kwargs, error_text
from cccp.calculation.context import TaskContext
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import TaskKind
from cccp.calculation.results import TaskResult
from cccp.calculation.tasks.casscf import (
    run_casscf as _cccp_run_casscf,
)

logger = logging.getLogger(__name__)

__all__ = ["execute_casscf", "run_casscf"]


def run_casscf(req: CalculationRequest) -> CalculationResult:
    """Run a CASSCF (optionally NEVPT2) calculation on one structure."""
    return execute_casscf(req)


def execute_casscf(req: CalculationRequest) -> CalculationResult:
    """ACP compat wrapper: cccp task core + legacy envelope mapping.

    ``TaskInputError``/``ValueError`` from conversion or the task core map to
    the legacy failed result (legacy ``run_casscf`` failed the step instead of
    raising).  Verbatim legacy capability kwargs ride along as
    ``capability_extras`` (translation cleanup: plan todo 25).
    """
    try:
        task_request, binding = to_task_request(req, TaskKind.CASSCF)
        context = TaskContext(
            config=binding.config,
            workdir=binding.artifact_root,
            capability_extras=capability_kwargs(req),
        )
        task_result = _cccp_run_casscf(task_request, context=context)
    except (TaskInputError, ValueError) as error:
        return _failed_result(req, error_text(error))
    return legacy_result(task_result, binding)


def legacy_result(task_result: TaskResult, binding: LegacyBinding) -> CalculationResult:
    """Map one typed ``TaskResult`` back to the legacy envelope.

    Task metadata carries the legacy-shaped ``multireference`` (production
    keys) and the QC metadata; it wins over the payload's rebuild projection
    from ``to_legacy_result`` so the legacy metadata shape never drifts.
    """
    legacy = to_legacy_result(task_result, binding)
    if not task_result.metadata:
        return legacy
    metadata: dict[str, JsonValue] = dict(legacy.metadata)  # type: ignore[assignment]
    metadata.update(task_result.metadata)  # type: ignore[arg-type]
    return CalculationResult(
        energy=legacy.energy,
        coords=legacy.coords,
        frequencies=legacy.frequencies,
        artifacts=legacy.artifacts,
        status=legacy.status,
        errors=legacy.errors,
        provenance=legacy.provenance,
        metadata=metadata,
    )


def _failed_result(req: CalculationRequest, message: str) -> CalculationResult:
    backend = str(req.resources.get("backend", req.resources.get("engine", "orca"))).lower()
    return CalculationResult(
        artifacts=[],
        status="failed",
        errors=[message],
        provenance=Provenance(
            backend=backend,
            method=req.method,
            profile=req.profile or "default",
            version="unknown",
            input_signature=str(req.input_artifact.path),
        ),
        metadata={},
    )
