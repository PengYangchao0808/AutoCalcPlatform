"""Geometry-optimization task core (plan todo 18).

Pipeline: request validation → mode/``StructureRole`` consistency → two-step
backend selection → translation (``resolve_spec`` + ``render_backend_input``;
theory carriers live ONLY on the effective ``MethodSpec`` level) → interface
execution with an internal rescue chain → typed
:class:`~cccp.calculation.results.OptimizePayload` (derived diagnostics).

Contract notes (plan todo 18, review fixes):

* ``OptimizeOptions.ts`` replaces the dual-semantics ``ts_mode: bool|int`` —
  ``TsSpec(enabled, mode_index)`` separates the switch from the target.
* ``OptimizeOptions.rescue.failure_type`` is the caller restore input
  (``None`` = derive from this module's error classification); the result
  payload carries the derived ``rescue_failure_type`` / ``rescue_structure_kind``
  diagnostics — input and output never share a writable field.
* Rescue is internal task policy (never cross-task orchestration); the
  ``_RESCUE_MATRIX`` rows keyed ``intermediate``/``precursor``/``product``
  are UNREACHABLE under :class:`~cccp.calculation.contracts.StructureRole`
  (only ``ts``/``minimum`` derive) and are kept as defensive/legacy rows for
  the str-typed :func:`build_rescue_plan` entry.
* Progress events are scientific (:class:`~cccp.calculation.progress.ProgressEvent`);
  platform identity (trajectory ``item_id``) is injected by the ACP adapter.
* This module never writes manifests or platform frames.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from cccp.calculation._common import (
    CalculationInputs,
    artifacts_from_qc,
    backend_for_request,
    build_state_scf_options,
    call_capability,
    classify_failure,
    electron_count,
    error_text,
    level_explicit_fields,
    load_geometry,
    qc_metadata_json,
    render_backend_input,
    resolve_multiplicity,
    resolve_spec,
    state_result_metadata,
    theory_run_config,
    write_state_artifacts,
)
from cccp.calculation.context import TaskContext, resolve_context
from cccp.calculation.contracts import (
    ArtifactRef,
    JsonValue,
    OptimizationMode,
    Provenance,
    StructureRole,
    validate_electronic_state_spec,
)
from cccp.calculation.errors import TaskInputError
from cccp.calculation.optimization_trajectory import (
    OptimizationTrajectoryRecorder,
    finalize_optimization_trajectory,
)
from cccp.calculation.progress import ProgressEvent, ProgressEventKind
from cccp.calculation.requests import (
    FailureType,
    MethodSpec,
    OptimizeOptions,
    TaskKind,
    TaskRequest,
    validate_request,
)
from cccp.calculation.results import OptimizePayload, TaskResult
from cccp.calculation.selection import precheck_runtime, select_semantic

FRESH_HESSIAN_RESTART = "fresh_hessian_restart"
FRESH_HESSIAN_MODE_MONITOR = "fresh_hessian_mode_monitor"
TS_MODE_DIRECTED = "ts_mode_directed"
MODE_DISPLACEMENT = "mode_displacement"
SADDLE_BREAK = "saddle_break"
CALCALL_OPT = "calcall_opt"
TIGHT_OPT_CALCHESS = "tight_opt_calchess"
IRC_MIDPOINT_RECOVERY = "irc_midpoint_recovery"
SCF_INCREASE_MAXITER = "scf_increase_maxiter"
SCF_SLOWCONV = "scf_slowconv"
SCF_SOSCF = "scf_soscf"
SCF_DAMP_SHIFT = "scf_damp_shift"

FAILURE_EXIT: Final[frozenset[str]] = frozenset({"crash_timeout", "memory_failure"})
_FAILURE_TYPES: Final[frozenset[str]] = frozenset(
    {
        "geometry_not_converged",
        "higher_order_saddle",
        "ts_no_imaginary",
        "minimum_with_imaginary",
        "scf_failure",
        "crash_timeout",
        "collapsed_to_product",
        "memory_failure",
    }
)

# The intermediate/precursor/product rows are UNREACHABLE under StructureRole
# derivation (only "ts"/"minimum" derive) and are kept as defensive/legacy
# rows for the str-typed build_rescue_plan entry (plan todo 18).
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
    ("scf_failure", "ts"): (SCF_INCREASE_MAXITER, SCF_SLOWCONV, SCF_SOSCF, SCF_DAMP_SHIFT),
    (
        "scf_failure",
        "intermediate",
    ): (SCF_INCREASE_MAXITER, SCF_SLOWCONV, SCF_SOSCF, SCF_DAMP_SHIFT),
    ("scf_failure", "minimum"): (SCF_INCREASE_MAXITER, SCF_SLOWCONV, SCF_SOSCF, SCF_DAMP_SHIFT),
    ("scf_failure", "precursor"): (SCF_INCREASE_MAXITER, SCF_SLOWCONV, SCF_SOSCF, SCF_DAMP_SHIFT),
    ("scf_failure", "product"): (SCF_INCREASE_MAXITER, SCF_SLOWCONV, SCF_SOSCF, SCF_DAMP_SHIFT),
    ("memory_failure", "ts"): (),
    ("memory_failure", "intermediate"): (),
    ("memory_failure", "minimum"): (),
    ("memory_failure", "precursor"): (),
    ("memory_failure", "product"): (),
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
    SCF_INCREASE_MAXITER: "increase SCF MaxIter to 500",
    SCF_SLOWCONV: "increase SCF MaxIter to 500 with SlowConv strategy",
    SCF_SOSCF: "increase SCF MaxIter to 500 with SOSCF strategy",
    SCF_DAMP_SHIFT: "SOSCF with damping and level shift",
}

_TARGET_PRESERVING_STRATEGIES: Final[frozenset[str]] = frozenset(
    {
        SCF_INCREASE_MAXITER,
        SCF_SLOWCONV,
        SCF_SOSCF,
        SCF_DAMP_SHIFT,
    }
)

_BACKEND_FAILURES = (OSError, RuntimeError, ValueError)

#: Semantic selection capability → backend execution method name.
_CAPABILITY_METHODS: Final[dict[str, str]] = {
    "geometry_optimization": "optimize",
    "transition_state": "transition_state_opt",
    "constrained_optimization": "constrained_optimize",
}

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RescueAction:
    """One ordered optimization rescue action."""

    strategy: str
    description: str
    index: int


@dataclass(frozen=True, slots=True)
class RescuePlan:
    """Ordered rescue actions and terminal state for one failed optimization.

    ``rescue_structure_kind`` keeps the legacy rescue-matrix str semantics of
    the retained :func:`build_rescue_plan` entry (derived diagnostic source).
    """

    failure_type: str
    rescue_structure_kind: str
    actions: tuple[RescueAction, ...]
    terminal: bool


def build_rescue_plan(
    failure_type: str,
    structure_kind: str,
    *,
    explicit_ts_target: int | None = None,
) -> RescuePlan:
    """Build the migrated eight-strategy rescue plan for one failure cell.

    Keeps its str parameters (rescue-matrix vocabulary, plan todo 18
    acceptance) — task code derives ``structure_kind`` from
    :class:`~cccp.calculation.contracts.StructureRole` ("ts"/"minimum" only).

    When *explicit_ts_target* is set (a mapped TS Mode target), every
    strategy that would restart from a different geometry/Hessian or
    override ``TS_Mode`` is dropped: only SCF-recovery actions keep the
    target binding intact, and the plan is terminal otherwise (plan §10.1
    — "不能确定目标时停止并报告，不自动回到最低模式").
    """
    strategies = _RESCUE_MATRIX.get((failure_type, structure_kind), ())
    if explicit_ts_target is not None:
        strategies = tuple(
            strategy for strategy in strategies if strategy in _TARGET_PRESERVING_STRATEGIES
        )
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
        rescue_structure_kind=structure_kind,
        actions=actions,
        terminal=terminal,
    )


def derive_failure_type(message: str, *, override: FailureType | None = None) -> FailureType:
    """Classify one failure message into the rescue failure vocabulary.

    ``override`` is the caller restore input (``RescueSpec.failure_type``):
    a vocabulary token wins; anything else derives from the message tokens
    (legacy ``_failure_type`` override/derive semantics).
    """
    if isinstance(override, str) and override in _FAILURE_TYPES:
        return override
    normalized = message.lower()
    if "[scf_failure]" in normalized:
        return "scf_failure"
    if "[geometry_not_converged]" in normalized:
        return "geometry_not_converged"
    if "[memory_failure]" in normalized:
        return "memory_failure"
    if "[crash_timeout]" in normalized:
        return "crash_timeout"
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
    if strategy == SCF_INCREASE_MAXITER:
        return {"scf_maxiter": 500}
    if strategy == SCF_SLOWCONV:
        return {"scf_maxiter": 500, "scf_strategy": "slowconv"}
    if strategy == SCF_SOSCF:
        return {"scf_maxiter": 500, "scf_strategy": "soscf"}
    if strategy == SCF_DAMP_SHIFT:
        return {
            "scf_maxiter": 500,
            "scf_strategy": "soscf",
            "scf_damp": True,
            "scf_damp_fac": 0.50,
            "scf_shift": True,
            "scf_shift_fac": 0.30,
        }
    return {}


def _inject_gbw_continuation(
    source_dir: Path | None,
    target_dir: Path | None,
    kwargs: dict[str, Any],
) -> None:
    """Copy .gbw from a failed attempt into a rescue attempt directory.

    Sets ``mo_read_path`` in *kwargs* so the ORCAInterface renders a
    ``%moinp`` block and ``Moread`` route keyword, enabling orbital
    inheritance across rescue attempts.
    """
    if source_dir is None or target_dir is None:
        return
    source_dir = Path(source_dir)
    target_dir = Path(target_dir)
    if not source_dir.is_dir():
        return
    gbw_files = list(source_dir.glob("*.gbw"))
    if not gbw_files:
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / gbw_files[0].name
    shutil.copy2(gbw_files[0], dest)
    kwargs.setdefault("mo_read_path", str(dest))


def run_optimize(
    request: TaskRequest,
    *,
    context: TaskContext | None = None,
) -> TaskResult:
    """Run one geometry optimization with an internal rescue chain."""
    validate_request(request)
    if request.task is not TaskKind.OPTIMIZE:
        message = f"run_optimize requires task 'optimize', got {request.task.value!r}"
        raise TaskInputError(message)
    if request.options is not None and not isinstance(request.options, OptimizeOptions):
        message = "optimize requires OptimizeOptions"
        raise TaskInputError(message)
    ctx = resolve_context(request, context)
    options = request.options if isinstance(request.options, OptimizeOptions) else OptimizeOptions()

    structure = request.structure
    if structure is None:
        message = "task 'optimize' requires a structure input"
        raise TaskInputError(message)
    ts_enabled = options.ts is not None and options.ts.enabled
    ts_requested = options.mode is OptimizationMode.TRANSITION_STATE or ts_enabled
    expected_role = StructureRole.TRANSITION_STATE if ts_requested else StructureRole.MINIMUM
    if structure.role is not expected_role:
        message = (
            f"mode/ts and StructureRole disagree: mode={options.mode.value!r} "
            f"ts.enabled={ts_enabled} implies role {expected_role.value!r}, "
            f"got {structure.role.value!r}"
        )
        raise TaskInputError(message)

    selection = select_semantic(request)
    if ctx.backend is None:
        selection = precheck_runtime(selection, ctx)
    method = _CAPABILITY_METHODS.get(selection.capability)
    if method is None:
        message = f"no optimize execution method for capability {selection.capability!r}"
        raise TaskInputError(message)

    path = structure.path
    if path is not None and not path.is_absolute():
        path = ctx.input_root() / path
    coordinates, symbols = load_geometry(
        path=path,
        coordinates=structure.coordinates,
        symbols=structure.symbols,
        elements=structure.elements,
    )
    state = request.electronic_state
    if state is not None:
        validation = validate_electronic_state_spec(
            state,
            backend=selection.backend,
            n_electrons=electron_count(symbols, request.charge),
            n_atoms=len(symbols),
        )
        if validation.errors:
            message = "electronic_state validation failed: " + "; ".join(validation.errors)
            raise TaskInputError(message)
        for warning in validation.warnings:
            logger.warning("electronic_state: %s", warning)
    multiplicity = resolve_multiplicity(state, symbols, request.charge, request.multiplicity)
    state_scf = (
        build_state_scf_options(state)
        if state is not None and selection.backend == "orca"
        else None
    )
    inputs = CalculationInputs(
        coordinates=coordinates,
        symbols=symbols,
        charge=request.charge,
        multiplicity=multiplicity,
        scf_options=state_scf,
        electronic_state=state,
    )

    level = _effective_level(request, options)
    spec = resolve_spec(
        level.method or None,
        explicit=level_explicit_fields(level),
        run_config=theory_run_config(ctx.config),
    )
    base_kwargs = render_backend_input(
        spec,
        method=level.method or None,
        state_scf_options=state_scf,
        extras=ctx.capability_extras,
    )
    for key, value in (
        ("initial_hessian", options.initial_hessian),
        ("recalc_hess", options.recalc_hess),
        ("trust_radius", options.trust_radius),
        ("max_cycles", options.max_cycles),
        ("geom_maxiter", options.geom_maxiter),
    ):
        if value is not None:
            base_kwargs[key] = value
    if ts_enabled and options.ts is not None:
        base_kwargs["ts_mode"] = (
            options.ts.mode_index if options.ts.mode_index is not None else True
        )
    if options.constraints is not None:
        base_kwargs["constraints"] = options.constraints

    backend = (
        ctx.backend
        if ctx.backend is not None
        else backend_for_request(
            selection.backend,
            config=ctx.config,
            resources=request.resources,
            constructor_kwargs={
                key: value for key, value in base_kwargs.items() if key != "output_name"
            },
        )
    )
    backend_label = str(getattr(backend, "name", selection.backend) or selection.backend)
    target_dir = request.output_dir
    explicit_target = (
        options.ts.mode_index if (options.ts is not None and options.ts.enabled) else None
    )
    rescue_kind = "ts" if structure.role is StructureRole.TRANSITION_STATE else "minimum"

    all_artifacts: list[ArtifactRef] = []
    errors: list[str] = []
    qc_result, failure = _run_attempt(
        backend, method, inputs, target_dir, base_kwargs, selection.backend, ctx
    )
    if _successful_geometry(qc_result):
        _finalize_local_trajectory(target_dir, selection.backend)
        if qc_result is not None:
            all_artifacts = list(artifacts_from_qc(qc_result, backend_label))
        state_metadata, state_errors, forced_status = _state_outcome(state, qc_result)
        all_artifacts.extend(
            write_state_artifacts(state, _state_diagnostics(qc_result), target_dir, backend_label)
        )
        return _result(
            request,
            backend_label,
            qc_result,
            errors + state_errors,
            all_artifacts,
            OptimizePayload(
                optimization_status="converged",
                electronic_state=state_metadata or None,
                trajectory_ref=_trajectory_ref(target_dir, selection.backend),
            ),
            status=forced_status or "completed",
        )

    if qc_result is not None:
        all_artifacts.extend(artifacts_from_qc(qc_result, backend_label))
    first_failure = failure or _qc_failure_message(qc_result, method)
    errors.append(f"{method}: {first_failure}")
    failure_type = derive_failure_type(first_failure, override=options.rescue.failure_type)
    plan = build_rescue_plan(failure_type, rescue_kind, explicit_ts_target=explicit_target)
    rescue_diagnostics: dict[str, Any] = {
        "rescue_failure_type": plan.failure_type,
        "rescue_structure_kind": plan.rescue_structure_kind,
        "rescue_actions": tuple(action.strategy for action in plan.actions),
        "rescue_terminal": plan.terminal,
        "tsmode_explicit_target": explicit_target,
        "tsmode_target_preserved": True if explicit_target is not None else None,
    }
    rescue_enabled = options.rescue.policy != "off"
    max_rescue = options.rescue.max_rescue if options.rescue.max_rescue is not None else 2

    if not rescue_enabled or not plan.actions:
        _finalize_local_trajectory(target_dir, selection.backend)
        return _result(
            request,
            backend_label,
            qc_result,
            errors,
            all_artifacts,
            OptimizePayload(
                rescue_attempts=0,
                electronic_state=None,
                trajectory_ref=_trajectory_ref(target_dir, selection.backend),
                **rescue_diagnostics,
            ),
            status="failed",
        )

    last_attempt_dir = target_dir
    for action in plan.actions[:max_rescue]:
        attempt_kwargs = dict(base_kwargs)
        attempt_kwargs.update(_rescue_kwargs(action.strategy))
        attempt_dir = (
            target_dir / f"rescue_{action.index:02d}_{action.strategy}"
            if target_dir is not None
            else None
        )
        _inject_gbw_continuation(last_attempt_dir, attempt_dir, attempt_kwargs)
        qc_result, failure = _run_attempt(
            backend, method, inputs, attempt_dir, attempt_kwargs, selection.backend, ctx
        )
        last_attempt_dir = attempt_dir
        if qc_result is not None:
            all_artifacts.extend(artifacts_from_qc(qc_result, backend_label))
        if _successful_geometry(qc_result):
            _finalize_local_trajectory(target_dir, selection.backend)
            state_metadata, state_errors, forced_status = _state_outcome(state, qc_result)
            all_artifacts.extend(
                write_state_artifacts(
                    state, _state_diagnostics(qc_result), target_dir, backend_label
                )
            )
            return _result(
                request,
                backend_label,
                qc_result,
                errors + state_errors,
                all_artifacts,
                OptimizePayload(
                    optimization_status="converged",
                    rescue_attempts=action.index + 1,
                    electronic_state=state_metadata or None,
                    trajectory_ref=_trajectory_ref(target_dir, selection.backend),
                    **rescue_diagnostics,
                ),
                status=forced_status or "completed",
            )
        failure_message = failure or _qc_failure_message(qc_result, method)
        errors.append(f"{action.strategy}: {failure_message}")

    _finalize_local_trajectory(target_dir, selection.backend)
    return _result(
        request,
        backend_label,
        qc_result,
        errors,
        all_artifacts,
        OptimizePayload(
            rescue_attempts=len(plan.actions),
            electronic_state=None,
            trajectory_ref=_trajectory_ref(target_dir, selection.backend),
            **rescue_diagnostics,
        ),
        status="failed",
    )


def _result(
    request: TaskRequest,
    backend: str,
    qc_result: Any,
    errors: list[str],
    artifacts: list[ArtifactRef],
    payload: OptimizePayload,
    *,
    status: str,
) -> TaskResult:
    error_kind = None
    if status == "failed":
        error_kind = classify_failure(error_message="; ".join(errors))
    return TaskResult(
        task=TaskKind.OPTIMIZE,
        status=status,
        complete=status == "completed",
        error_kind=error_kind,
        errors=tuple(errors),
        energy_hartree=getattr(qc_result, "energy", None),
        coordinates=_coordinates(qc_result),
        symbols=_symbols(qc_result),
        artifacts=tuple(artifacts),
        provenance=_provenance(backend, request),
        payload=payload,
        metadata=qc_metadata_json(getattr(qc_result, "metadata", None) or {}),
    )


def _run_attempt(
    backend: Any,
    capability: str,
    inputs: CalculationInputs,
    target_dir: Path | None,
    kwargs: dict[str, Any],
    selected_backend: str,
    ctx: TaskContext,
) -> tuple[Any, str | None]:
    recorder = None
    attempt_kwargs = dict(kwargs)
    if selected_backend == "orca" and target_dir is not None:
        recorder = OptimizationTrajectoryRecorder(
            target_dir,
            on_cycle=_cycle_publisher(ctx),
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


def _cycle_publisher(ctx: TaskContext):
    def publish(cycle: int, status: str) -> None:
        ctx.emit_progress(
            ProgressEvent(
                kind=ProgressEventKind.METRIC,
                metric="cycle",
                value=float(cycle),
                message=status,
            )
        )

    return publish


def _finalize_local_trajectory(target_dir: Path | None, selected_backend: str) -> None:
    """Best-effort terminal trajectory rebuild on local product paths."""
    if target_dir is None or selected_backend != "orca":
        return
    try:
        finalize_optimization_trajectory(target_dir)
    except Exception:  # noqa: BLE001 — trajectory rebuild never fails a calculation
        logger.debug("Could not finalize optimization trajectory: %s", target_dir, exc_info=True)


def _trajectory_ref(target_dir: Path | None, selected_backend: str) -> ArtifactRef | None:
    if target_dir is None or selected_backend != "orca":
        return None
    path = target_dir / "optimization_trajectory.json"
    if not path.is_file():
        return None
    return ArtifactRef(path=path, type="trajectory", source="optimization_trajectory")


def _effective_level(request: TaskRequest, options: OptimizeOptions) -> MethodSpec:
    """Single theory carrier: options.level fields win over request.level."""
    base = request.level
    override = options.level
    if override is None:
        return base
    values: dict[str, Any] = {
        "method": override.method or base.method,
        "basis": override.basis or base.basis,
    }
    for name in (
        "dispersion",
        "solvent",
        "solvent_model",
        "integration_grid",
        "scf",
        "ri_approximation",
        "auxiliary_basis_j",
        "auxiliary_basis_c",
    ):
        values[name] = getattr(override, name)
        if values[name] is None:
            values[name] = getattr(base, name)
    return MethodSpec(**values)


def _state_outcome(
    state: Any,
    qc_result: Any,
) -> tuple[dict[str, JsonValue], list[str], str | None]:
    if state is None:
        return {}, [], None
    return state_result_metadata(state, _state_diagnostics(qc_result))


def _state_diagnostics(qc_result: Any) -> dict[str, object]:
    metadata = getattr(qc_result, "metadata", None)
    if not isinstance(metadata, Mapping):
        return {}
    raw = metadata.get("electronic_state_diagnostics")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _successful_geometry(result: Any) -> bool:
    return result is not None and result.success and result.coordinates is not None


def _qc_failure_message(result: Any, capability: str) -> str:
    if result is not None and result.error_message:
        return result.error_message
    if result is not None and result.success:
        return f"{capability} returned no converged coordinates"
    return f"{capability} failed"


def _coordinates(qc_result: object) -> tuple[tuple[float, float, float], ...] | None:
    raw = getattr(qc_result, "coordinates", None)
    if raw is None:
        return None
    return tuple(tuple(float(c) for c in row) for row in raw)


def _symbols(qc_result: object) -> tuple[str, ...] | None:
    raw = getattr(qc_result, "symbols", None)
    if not raw:
        return None
    return tuple(str(s) for s in raw)


def _provenance(backend: str, request: TaskRequest) -> Provenance:
    return Provenance(
        backend=backend,
        method=request.level.method,
        version="unknown",
        input_signature=str(request.structure.path) if request.structure else "",
    )


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
    "build_rescue_plan",
    "derive_failure_type",
    "run_optimize",
]
