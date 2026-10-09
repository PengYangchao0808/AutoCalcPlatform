# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Geometry optimization — ACP compat wrapper (plan todo 18).

The task core lives in :mod:`cccp.calculation.tasks.optimize` (cleaned typed
options contract, internal rescue chain, stdlib trajectory recorder, failure
classification).  This module is the ACP-side compat surface: legacy
``CalculationRequest`` → typed ``TaskRequest`` conversion, the legacy backend
registry seam, the ``ProgressReporter`` → ``TaskProgressSink`` adapter (UI
metric fields are ACP-only), trajectory ``item_id`` injection (platform
identity), and the legacy ``CalculationResult`` envelope mapping.
``run_optimize`` is a pure forwarder (the dual-root uniqueness guard
classifies it as a shim).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from acp.calculations.contracts import CalculationRequest, CalculationResult
from acp.calculations.legacy_adapters import to_legacy_result, to_task_request
from acp.calculations.primitives._common import (
    backend_for_request,
    backend_name,
    capability_kwargs,
    output_dir,
)
from acp.calculations.progress import LiveMetric, ProgressReporter
from cccp import calculation as _cccp_calculation
from cccp.calculation.context import TaskContext
from cccp.calculation.optimization_trajectory import finalize_optimization_trajectory
from cccp.calculation.progress import ProgressEvent, ProgressEventKind
from cccp.calculation.requests import TaskKind
from cccp.calculation.results import TaskResult
from cccp.calculation.tasks.optimize import (
    _FAILURE_TYPES as _FAILURE_TYPES,
)
from cccp.calculation.tasks.optimize import (
    _RESCUE_DESCRIPTIONS as _RESCUE_DESCRIPTIONS,
)
from cccp.calculation.tasks.optimize import (
    _RESCUE_MATRIX as _RESCUE_MATRIX,
)
from cccp.calculation.tasks.optimize import (
    CALCALL_OPT,
    FAILURE_EXIT,
    FRESH_HESSIAN_MODE_MONITOR,
    FRESH_HESSIAN_RESTART,
    IRC_MIDPOINT_RECOVERY,
    MODE_DISPLACEMENT,
    SADDLE_BREAK,
    SCF_DAMP_SHIFT,
    SCF_INCREASE_MAXITER,
    SCF_SLOWCONV,
    SCF_SOSCF,
    TIGHT_OPT_CALCHESS,
    TS_MODE_DIRECTED,
    RescueAction,
    RescuePlan,
    _rescue_kwargs,
    build_rescue_plan,
    derive_failure_type,
)
from cccp.calculation.tasks.optimize import (
    _inject_gbw_continuation as _inject_gbw_continuation,
)

logger = logging.getLogger(__name__)

_OPTIMIZATION_PROGRESS_REPORTER: ContextVar[ProgressReporter | None] = ContextVar(
    "optimization_progress_reporter", default=None
)


@contextmanager
def optimization_progress_context(reporter: ProgressReporter | None) -> Iterator[None]:
    """Make a reporter available to an optimization dispatched by a plan."""
    token = _OPTIMIZATION_PROGRESS_REPORTER.set(reporter)
    try:
        yield
    finally:
        _OPTIMIZATION_PROGRESS_REPORTER.reset(token)


class _ReporterSink:
    """Map scientific cycle events onto ACP live metrics (UI fields stay ACP)."""

    def __init__(self, reporter: ProgressReporter) -> None:
        self._reporter = reporter

    def emit(self, event: ProgressEvent) -> None:
        if (
            event.kind is not ProgressEventKind.METRIC
            or event.metric != "cycle"
            or event.value is None
        ):
            return
        cycle = int(event.value)
        status = event.message or "running"
        convergence = {
            "running": "running",
            "converged": "converged",
            "failed": "failed",
        }.get(status, "running")
        self._reporter.update_live_metrics(
            [
                LiveMetric(
                    key="opt_step",
                    label_key="live.opt_step",
                    value=f"Step {cycle}",
                    kind="iteration",
                    priority=100,
                ),
                LiveMetric(
                    key="opt_convergence",
                    label_key="live.opt_convergence",
                    value=convergence,
                    kind="status",
                    priority=90,
                ),
            ]
        )


def run_optimize(
    req: CalculationRequest,
    *,
    progress_reporter: ProgressReporter | None = None,
) -> CalculationResult:
    """Optimize a structure and retry recoverable backend failures."""
    return execute_optimize(req, progress_reporter=progress_reporter)


def execute_optimize(
    req: CalculationRequest,
    *,
    progress_reporter: ProgressReporter | None = None,
) -> CalculationResult:
    """ACP compat wrapper: cccp task core + legacy envelope mapping.

    Verbatim legacy capability kwargs (``opt_level``, ``scf_maxiter``, …) ride
    along as ``capability_extras`` (translation cleanup: plan todo 25); the
    ``trajectory_item_id`` platform identity is injected into the finalized
    trajectory here (the task only knows local product paths).
    """
    if progress_reporter is None:
        progress_reporter = _OPTIMIZATION_PROGRESS_REPORTER.get()
    task_request, binding = to_task_request(req, TaskKind.OPTIMIZE)
    selected_backend = backend_name(req)
    backend = backend_for_request(req, selected_backend)
    sink = _ReporterSink(progress_reporter) if progress_reporter is not None else None
    context = TaskContext(
        config=binding.config,
        workdir=binding.artifact_root,
        backend=backend,
        capability_extras=capability_kwargs(req),
        progress=sink,
    )
    task_result = _cccp_calculation.run_optimize(task_request, context=context)
    target_dir = output_dir(req)
    if target_dir is not None:
        _finalize_trajectory(target_dir, selected_backend, binding.trajectory_item_id or "")
    return _legacy_result(task_result, binding)


def _legacy_result(task_result: TaskResult, binding: Any) -> CalculationResult:
    """Map one typed ``TaskResult`` back to the legacy envelope (lossless)."""
    legacy = to_legacy_result(task_result, binding)
    if not task_result.metadata:
        return legacy
    metadata = dict(task_result.metadata)
    metadata.update(legacy.metadata)
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


def _finalize_trajectory(target_dir: Path | None, selected_backend: str, item_id: str) -> None:
    """ACP-side terminal trajectory rebuild with platform identity injection."""
    if target_dir is None or selected_backend != "orca":
        return
    try:
        finalize_optimization_trajectory(target_dir, item_id=item_id)
    except Exception:  # noqa: BLE001
        logger.debug("Could not finalize optimization trajectory: %s", target_dir, exc_info=True)


def _failure_type(request: CalculationRequest, message: str) -> str:
    """Legacy failure-classification entry (override/derive, adapter only)."""
    raw = request.resources.get("failure_type")
    return derive_failure_type(message, override=raw if isinstance(raw, str) else None)


__all__ = [
    "CALCALL_OPT",
    "FAILURE_EXIT",
    "FRESH_HESSIAN_MODE_MONITOR",
    "FRESH_HESSIAN_RESTART",
    "IRC_MIDPOINT_RECOVERY",
    "MODE_DISPLACEMENT",
    "RescueAction",
    "RescuePlan",
    "SADDLE_BREAK",
    "SCF_DAMP_SHIFT",
    "SCF_INCREASE_MAXITER",
    "SCF_SLOWCONV",
    "SCF_SOSCF",
    "TIGHT_OPT_CALCHESS",
    "TS_MODE_DIRECTED",
    "_failure_type",
    "_rescue_kwargs",
    "build_rescue_plan",
    "execute_optimize",
    "optimization_progress_context",
    "run_optimize",
]
