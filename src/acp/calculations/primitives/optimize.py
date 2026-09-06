# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnnecessaryComparison=false
"""Geometry optimization primitive with a backend-independent rescue chain."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from typing_extensions import assert_never

from acp.backends.base import QCResult
from acp.calculations.contracts import (
    ArtifactRef,
    CalculationRequest,
    CalculationResult,
    JsonValue,
    StructureRole,
)
from acp.calculations.progress import LiveMetric, ProgressReporter

from ._common import (
    CalculationInputs,
    artifacts_from_qc,
    backend_for_request,
    backend_name,
    call_capability,
    capability_kwargs,
    error_text,
    load_inputs,
    output_dir,
    result_from_qc,
)
from .optimization_trajectory import (
    OptimizationTrajectoryRecorder,
    finalize_optimization_trajectory,
)

FRESH_HESSIAN_RESTART = "fresh_hessian_restart"
FRESH_HESSIAN_MODE_MONITOR = "fresh_hessian_mode_monitor"
TS_MODE_DIRECTED = "ts_mode_directed"
MODE_DISPLACEMENT = "mode_displacement"
SADDLE_BREAK = "saddle_break"
CALCALL_OPT = "calcall_opt"
TIGHT_OPT_CALCHESS = "tight_opt_calchess"
IRC_MIDPOINT_RECOVERY = "irc_midpoint_recovery"

FAILURE_EXIT: Final[frozenset[str]] = frozenset({"scf_failure", "crash_timeout"})
_STRUCTURE_KINDS: Final[frozenset[str]] = frozenset(
    {"ts", "intermediate", "minimum", "precursor", "product"}
)
_FAILURE_TYPES: Final[frozenset[str]] = frozenset(
    {
        "geometry_not_converged",
        "higher_order_saddle",
        "ts_no_imaginary",
        "minimum_with_imaginary",
        "scf_failure",
        "crash_timeout",
        "collapsed_to_product",
    }
)

_RESCUE_MATRIX: Final[dict[tuple[str, str], tuple[str, ...]]] = {
    ("geometry_not_converged", "ts"): (
        FRESH_HESSIAN_RESTART,
        TS_MODE_DIRECTED,
        CALCALL_OPT,
    ),
    ("geometry_not_converged", "intermediate"): (FRESH_HESSIAN_RESTART, CALCALL_OPT),
    ("geometry_not_converged", "minimum"): (FRESH_HESSIAN_RESTART, CALCALL_OPT),
    ("higher_order_saddle", "ts"): (SADDLE_BREAK, TS_MODE_DIRECTED, CALCALL_OPT),
    ("ts_no_imaginary", "ts"): (
        FRESH_HESSIAN_MODE_MONITOR,
        TS_MODE_DIRECTED,
        CALCALL_OPT,
    ),
    ("minimum_with_imaginary", "intermediate"): (MODE_DISPLACEMENT,),
    ("minimum_with_imaginary", "minimum"): (MODE_DISPLACEMENT,),
    ("collapsed_to_product", "intermediate"): (IRC_MIDPOINT_RECOVERY,),
    ("scf_failure", "ts"): (),
    ("scf_failure", "intermediate"): (),
    ("scf_failure", "minimum"): (),
    ("scf_failure", "precursor"): (),
    ("scf_failure", "product"): (),
    ("crash_timeout", "ts"): (),
    ("crash_timeout", "intermediate"): (),
    ("crash_timeout", "minimum"): (),
    ("crash_timeout", "precursor"): (),
    ("crash_timeout", "product"): (),
}

_RESCUE_DESCRIPTIONS: Final[dict[str, str]] = {
    FRESH_HESSIAN_RESTART: "restart with CalcHess + RecalcHess=5",
    FRESH_HESSIAN_MODE_MONITOR: "restart with fresh Hessian while monitoring the target mode",
    TS_MODE_DIRECTED: "re-run with TS_Mode targeting the lowest imaginary mode",
    CALCALL_OPT: "re-run with RecalcHess=1 (CalcAll semantics)",
    SADDLE_BREAK: "displace along the second imaginary mode to break the saddle",
    MODE_DISPLACEMENT: "displace ±0.30 Å along the imaginary mode",
    TIGHT_OPT_CALCHESS: "tight optimization with calculated Hessian",
    IRC_MIDPOINT_RECOVERY: "re-seed from the IRC midpoint (collapsed INT recovery)",
}
_BACKEND_FAILURES = (OSError, RuntimeError, ValueError)
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


@dataclass(frozen=True, slots=True)
class RescueAction:
    """One ordered optimization rescue action."""

    strategy: str
    description: str
    index: int


@dataclass(frozen=True, slots=True)
class RescuePlan:
    """Ordered rescue actions and terminal state for one failed optimization."""

    failure_type: str
    structure_kind: str
    actions: tuple[RescueAction, ...]
    terminal: bool


def build_rescue_plan(failure_type: str, structure_kind: str) -> RescuePlan:
    """Build the migrated eight-strategy rescue plan for one failure cell."""
    strategies = _RESCUE_MATRIX.get((failure_type, structure_kind), ())
    terminal = failure_type in FAILURE_EXIT or not strategies
    actions = tuple(
        RescueAction(
            strategy=strategy,
            description=_RESCUE_DESCRIPTIONS[strategy],
            index=index,
        )
        for index, strategy in enumerate(strategies)
    )
    return RescuePlan(
        failure_type=failure_type,
        structure_kind=structure_kind,
        actions=actions,
        terminal=terminal,
    )


def _finalize_trajectory(target_dir: Path | None, selected_backend: str, item_id: str) -> None:
    """Best-effort terminal trajectory rebuild; never fails the calculation."""
    if target_dir is None or selected_backend != "orca":
        return
    try:
        finalize_optimization_trajectory(target_dir, item_id=item_id)
    except Exception:  # noqa: BLE001
        logger.debug(
            "Could not finalize optimization trajectory: %s", target_dir, exc_info=True
        )


def run_optimize(
    req: CalculationRequest,
    *,
    progress_reporter: ProgressReporter | None = None,
) -> CalculationResult:
    """Optimize a structure and retry recoverable backend failures."""
    if progress_reporter is None:
        progress_reporter = _OPTIMIZATION_PROGRESS_REPORTER.get()
    inputs = load_inputs(req)
    selected_backend = backend_name(req)
    backend = backend_for_request(req, selected_backend)
    structure_kind = _structure_kind(req)
    capability = "transition_state_opt" if structure_kind == "ts" else "optimize"
    target_dir = output_dir(req)
    base_kwargs = capability_kwargs(req)
    all_artifacts: list[ArtifactRef] = []
    errors: list[str] = []
    trajectory_item_id = str(req.resources.get("trajectory_item_id") or "")

    qc_result, failure = _run_attempt(
        backend,
        capability,
        inputs,
        target_dir,
        base_kwargs,
        selected_backend=selected_backend,
        trajectory_item_id=trajectory_item_id,
        progress_reporter=progress_reporter,
    )
    if _successful_geometry(qc_result):
        _finalize_trajectory(target_dir, selected_backend, trajectory_item_id)
        if qc_result is not None:
            all_artifacts = artifacts_from_qc(qc_result, selected_backend, all_artifacts)
        return result_from_qc(req, selected_backend, qc_result, errors, all_artifacts)

    if qc_result is not None:
        all_artifacts = artifacts_from_qc(qc_result, selected_backend, all_artifacts)
    first_failure = failure or _qc_failure_message(qc_result, capability)
    errors.append(f"{capability}: {first_failure}")
    failure_type = _failure_type(req, first_failure)
    plan = build_rescue_plan(failure_type, structure_kind)
    rescue_metadata = _plan_metadata(plan)

    for action in plan.actions:
        attempt_kwargs = dict(base_kwargs)
        attempt_kwargs.update(_rescue_kwargs(action.strategy))
        attempt_dir = (
            target_dir / f"rescue_{action.index:02d}_{action.strategy}"
            if target_dir is not None
            else None
        )
        qc_result, failure = _run_attempt(
            backend,
            capability,
            inputs,
            attempt_dir,
            attempt_kwargs,
            selected_backend=selected_backend,
            trajectory_item_id=trajectory_item_id,
            progress_reporter=progress_reporter,
        )
        if qc_result is not None:
            all_artifacts = artifacts_from_qc(qc_result, selected_backend, all_artifacts)
        if _successful_geometry(qc_result):
            _finalize_trajectory(target_dir, selected_backend, trajectory_item_id)
            rescue_metadata["rescue_attempts"] = action.index + 1
            return result_from_qc(
                req,
                selected_backend,
                qc_result,
                errors,
                all_artifacts,
                rescue_metadata,
            )
        failure_message = failure or _qc_failure_message(qc_result, capability)
        errors.append(f"{action.strategy}: {failure_message}")

    rescue_metadata["rescue_attempts"] = len(plan.actions)
    _finalize_trajectory(target_dir, selected_backend, trajectory_item_id)
    return result_from_qc(
        req,
        selected_backend,
        qc_result,
        errors,
        all_artifacts,
        rescue_metadata,
        status="failed",
    )


def _structure_kind(request: CalculationRequest) -> str:
    raw_kind = request.resources.get("structure_kind")
    if isinstance(raw_kind, str) and raw_kind in _STRUCTURE_KINDS:
        return raw_kind
    match request.input_artifact.role:
        case StructureRole.TRANSITION_STATE:
            return "ts"
        case StructureRole.MINIMUM:
            return "minimum"
        case unreachable:
            assert_never(unreachable)


def _run_attempt(
    backend: Any,
    capability: str,
    inputs: CalculationInputs,
    target_dir: Path | None,
    kwargs: dict[str, Any],
    *,
    selected_backend: str,
    trajectory_item_id: str = "",
    progress_reporter: ProgressReporter | None = None,
) -> tuple[QCResult | None, str | None]:
    recorder = None
    attempt_kwargs = dict(kwargs)
    if selected_backend == "orca" and target_dir is not None:
        if progress_reporter is not None:
            reporter = progress_reporter

            def publish_cycle(cycle: int, status: str) -> None:
                convergence = {
                    "running": "running",
                    "converged": "converged",
                    "failed": "failed",
                }.get(status, "running")
                reporter.set_live_metrics(
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

            recorder = OptimizationTrajectoryRecorder(
                target_dir,
                item_id=trajectory_item_id or target_dir.parent.name,
                on_cycle=publish_cycle,
            )
        else:
            recorder = OptimizationTrajectoryRecorder(
                target_dir,
                item_id=trajectory_item_id or target_dir.parent.name,
            )
        attempt_kwargs["output_callback"] = recorder.feed_line
    try:
        result = call_capability(backend, capability, inputs, target_dir, attempt_kwargs)
        if recorder is not None:
            recorder.finish(
                converged=bool(result.success),
                status="completed" if result.success else "failed",
            )
        return result, None
    except _BACKEND_FAILURES as error:
        if recorder is not None:
            recorder.finish(converged=False, status="failed")
        return None, error_text(error)


def _successful_geometry(result: QCResult | None) -> bool:
    return result is not None and result.success and result.coordinates is not None


def _qc_failure_message(result: QCResult | None, capability: str) -> str:
    if result is not None and result.error_message:
        return result.error_message
    if result is not None and result.success:
        return f"{capability} returned no converged coordinates"
    return f"{capability} failed"


def _failure_type(request: CalculationRequest, message: str) -> str:
    override = request.resources.get("failure_type")
    if isinstance(override, str) and override in _FAILURE_TYPES:
        return override
    normalized = message.lower()
    if "scf" in normalized:
        return "scf_failure"
    if "timeout" in normalized or "timed out" in normalized or "time out" in normalized:
        return "crash_timeout"
    if "higher order" in normalized or "multiple imaginary" in normalized:
        return "higher_order_saddle"
    if "no imaginary" in normalized or "no negative" in normalized:
        return "ts_no_imaginary"
    if "imaginary" in normalized:
        return "minimum_with_imaginary"
    if "collapsed" in normalized:
        return "collapsed_to_product"
    return "geometry_not_converged"


def _rescue_kwargs(strategy: str) -> dict[str, JsonValue]:
    if strategy in {FRESH_HESSIAN_RESTART, FRESH_HESSIAN_MODE_MONITOR}:
        return {"initial_hessian": "calculate", "recalc_hess": 5}
    if strategy == TS_MODE_DIRECTED:
        return {"ts_mode": True, "trust_radius": 0.15}
    if strategy == CALCALL_OPT:
        return {"recalc_hess": 1}
    if strategy in {SADDLE_BREAK, MODE_DISPLACEMENT}:
        return {"mode_displacement": 0.30}
    if strategy == TIGHT_OPT_CALCHESS:
        return {"opt_level": "tight", "initial_hessian": "calculate"}
    if strategy == IRC_MIDPOINT_RECOVERY:
        return {"rescue_metadata": {"irc_midpoint_reseed": True}}
    return {}


def _plan_metadata(plan: RescuePlan) -> dict[str, JsonValue]:
    return {
        "rescue_failure_type": plan.failure_type,
        "rescue_structure_kind": plan.structure_kind,
        "rescue_actions": [action.strategy for action in plan.actions],
        "rescue_terminal": plan.terminal,
    }


__all__ = [
    "FAILURE_EXIT",
    "RescueAction",
    "RescuePlan",
    "build_rescue_plan",
    "optimization_progress_context",
    "run_optimize",
]
