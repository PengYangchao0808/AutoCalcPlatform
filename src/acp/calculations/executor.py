"""CalculationPlanExecutor — plan-driven execution with checkpoint and resume.

The executor is the single entry point for running a ``CalculationPlan``.
It orchestrates seven responsibilities (design doc §6.3):

1. Validate the plan via ``validate_plan``.
2. Create step directories under ``WORK/`` (§10.3 layout).
3. Dispatch each step to the appropriate calculation primitive.
4. Hand off optimized coordinates to downstream frequency / single-point steps.
5. Write a checkpoint after each completed step for crash-resume.
6. Record per-step errors without crashing the process.
7. Write a unique ``RESULT/result_manifest.json`` at finalization.

The executor deliberately does NOT recognise S1–S4, Lowconfirm, Highconfirm,
MechanismStudy, review, or promote — those concepts live in the mechanism
orchestrator layer above.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from acp.calculations.checkpoint import (
    load_checkpoint,
    write_checkpoint,
)
from acp.calculations.contracts import (
    CalculationPlan,
    CalculationRequest,
    CalculationResult,
    CalculationStep,
    Checkpoint,
    JsonValue,
    OptimizationMode,
    OptimizationSpec,
    StepKind,
    StructureArtifact,
    validate_plan,
)
from acp.calculations.primitives.frequency import run_frequency
from acp.calculations.primitives.optimize import run_optimize
from acp.calculations.primitives.singlepoint import run_singlepoint
from acp.storage.manifest import ProductKind, ResultManifest

logger = logging.getLogger(__name__)

# ── step-kind → WORK/ subdirectory (§10.3) ──────────────────────────────
_STEP_DIRS: dict[StepKind, str] = {
    StepKind.OPTIMIZE: "03_OPT",
    StepKind.FREQUENCY: "04_FREQ",
    StepKind.SINGLEPOINT: "05_SP",
    StepKind.THERMOCHEMISTRY: "06_THERMO",
    StepKind.SCAN: "07_PATH",
}

# ── step-kind → primitive callable ──────────────────────────────────────
_PRIMITIVE_DISPATCH: dict[StepKind, Callable[[CalculationRequest], CalculationResult]] = {
    StepKind.SINGLEPOINT: run_singlepoint,
    StepKind.OPTIMIZE: run_optimize,
    StepKind.FREQUENCY: run_frequency,
}

# Step kinds whose results feed coordinates into downstream steps.
_COORD_PRODUCING_KINDS: frozenset[StepKind] = frozenset({StepKind.OPTIMIZE})

_HANDOFF_KEY = "__handoff__"


def _json_text(value: JsonValue | None, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _json_text_list(value: JsonValue | None) -> list[str]:
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str)]


def _json_geometry(value: JsonValue | None) -> list[list[float]] | None:
    if not isinstance(value, list):
        return None
    coordinates: list[list[float]] = []
    for row in value:
        if not isinstance(row, list):
            return None
        numeric_row: list[float] = []
        for entry in row:
            if isinstance(entry, bool) or not isinstance(entry, (int, float)):
                return None
            numeric_row.append(float(entry))
        coordinates.append(numeric_row)
    return coordinates


def _json_geometry_value(coordinates: list[list[float]]) -> list[JsonValue]:
    value: list[JsonValue] = []
    for row in coordinates:
        json_row: list[JsonValue] = []
        for coordinate in row:
            json_row.append(float(coordinate))
        value.append(json_row)
    return value


def _json_text_list_value(values: list[str]) -> list[JsonValue]:
    value: list[JsonValue] = []
    for entry in values:
        value.append(entry)
    return value


def _normalise_step(step: CalculationStep | Mapping[str, JsonValue]) -> CalculationStep:
    if isinstance(step, CalculationStep):
        return step
    raw_kind = step.get("kind")
    if not isinstance(raw_kind, str):
        raise ValueError("calculation step kind must be a string")
    raw_mode = step.get("mode")
    mode = raw_mode if isinstance(raw_mode, str) else OptimizationMode.UNCONSTRAINED.value
    raw_spec = step.get("spec")
    spec = raw_spec if isinstance(raw_spec, dict) else None
    return CalculationStep(kind=StepKind(raw_kind), mode=OptimizationMode(mode), spec=spec)


def _plan_fingerprint(plan: CalculationPlan) -> str:
    """Deterministic hash of the plan content for checkpoint identity."""
    step_values: list[JsonValue] = [
        {
            "kind": step.kind.value,
            "mode": step.mode.value,
            "spec": str(step.spec),
        }
        for step in (_normalise_step(raw_step) for raw_step in plan.steps)
    ]
    payload = json.dumps(
        {
            "workflow": plan.workflow,
            "profile": plan.profile,
            "steps": step_values,
            "items": [
                str(item.path) if isinstance(item, StructureArtifact) else str(item)
                for item in plan.items
            ],
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _step_dir_name(step_kind: StepKind) -> str | None:
    """Return the §10.3 directory name for a step kind, or ``None``."""
    return _STEP_DIRS.get(step_kind)


def _extract_method(step_spec: OptimizationSpec | dict[str, JsonValue] | None, default: str) -> str:
    """Extract method from a step spec, falling back to *default*."""
    if isinstance(step_spec, OptimizationSpec):
        return step_spec.method or default
    if isinstance(step_spec, dict) and "method" in step_spec:
        return str(step_spec["method"])
    return default


def _ensure_artifact(item: StructureArtifact | Mapping[str, JsonValue]) -> StructureArtifact:
    """Coerce a plan item to ``StructureArtifact``."""
    if isinstance(item, StructureArtifact):
        return item
    path_value = item.get("path")
    if not isinstance(path_value, str):
        path_value = item.get("geometry")
    if not isinstance(path_value, str):
        path_value = "."
    return StructureArtifact(
        path=Path(path_value),
        elements=_json_text_list(item.get("elements")),
        source=_json_text(item.get("source")),
    )


def _build_request(
    step_kind: StepKind,
    item: StructureArtifact,
    method: str,
    resources: dict[str, JsonValue],
    *,
    output_dir: Path | None = None,
    coordinates: list[list[float]] | None = None,
    symbols: list[str] | None = None,
) -> CalculationRequest:
    """Build a ``CalculationRequest`` for one step + item combination."""
    merged_resources: dict[str, JsonValue] = dict(resources)
    if output_dir is not None:
        merged_resources["output_dir"] = str(output_dir)
    if coordinates is not None:
        merged_resources["coordinates"] = _json_geometry_value(coordinates)
    if symbols is not None:
        merged_resources["symbols"] = _json_text_list_value(symbols)
    return CalculationRequest(
        input_artifact=item,
        method=method,
        resources=merged_resources,
        workflow="executor",
        profile=None,
    )


def _product_kind_for_step(kind: StepKind) -> ProductKind:
    """Map a step kind to the manifest product kind."""
    mapping = {
        StepKind.OPTIMIZE: ProductKind.STRUCTURE,
        StepKind.FREQUENCY: ProductKind.FREQUENCY_MODES,
        StepKind.SINGLEPOINT: ProductKind.ENERGY_REPORT,
        StepKind.THERMOCHEMISTRY: ProductKind.THERMO_REPORT,
        StepKind.SCAN: ProductKind.TRAJECTORY,
    }
    return mapping.get(kind, ProductKind.FILE)


# ── public data classes ─────────────────────────────────────────────────


@dataclass
class StepState:
    """Mutable per-step execution record."""

    index: int
    kind: StepKind
    status: str = "pending"  # pending | completed | failed | skipped
    result: CalculationResult | None = None
    error: str = ""

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise for checkpoint persistence."""
        return {
            "index": self.index,
            "kind": self.kind.value,
            "status": self.status,
            "error": self.error,
            "energy": self.result.energy if self.result else None,
        }


@dataclass
class ExecutionResult:
    """Outcome of executing a ``CalculationPlan``."""

    status: str = "completed"  # completed | failed
    step_states: list[StepState] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def is_completed(self) -> bool:
        """``True`` when every step succeeded."""
        return self.status == "completed"

    @property
    def is_failed(self) -> bool:
        """``True`` when at least one step failed."""
        return self.status == "failed"


# ── executor ────────────────────────────────────────────────────────────


class CalculationPlanExecutor:
    """Execute a ``CalculationPlan`` step-by-step with checkpoint and resume.

    The executor calls calculation primitives — it does NOT re-implement
    any QC logic.  Each step dispatches to ``run_singlepoint``,
    ``run_optimize``, ``run_frequency``, etc. through a dispatch table.

    Coordinate handoff: when an optimize step succeeds, its output
    coordinates are injected into the ``resources`` of downstream
    frequency and single-point steps so they operate on the relaxed
    geometry.

    Failure isolation: a single step failure is recorded in
    ``step_states`` and ``errors`` but does NOT abort the remaining
    steps.  The overall status is ``"failed"`` if any step failed.

    Resume: on restart the executor loads the checkpoint from
    ``WORK/00_RUNTIME``.  Steps already marked ``"completed"`` are
    skipped.  A ``CheckpointMismatchError`` is raised if the plan
    fingerprint changed (stale checkpoint).
    """

    def __init__(
        self,
        *,
        backend_factory: Callable[..., object] | None = None,
    ) -> None:
        # backend_factory is accepted for API compatibility but the
        # primitives resolve backends internally via get_backend().
        self._backend_factory = backend_factory

    # ── public entry point ──────────────────────────────────────────────

    def execute(
        self,
        plan: CalculationPlan,
        task_root: Path,
        *,
        plan_fingerprint: str | None = None,
    ) -> ExecutionResult:
        """Execute *plan* under *task_root*, returning an ``ExecutionResult``.

        Args:
            plan: The calculation plan to execute.
            task_root: Root directory; ``WORK/`` and ``RESULT/`` are
                created here.
            plan_fingerprint: Optional override for the checkpoint
                fingerprint.  When ``None`` a deterministic hash of the
                plan is used.

        Returns:
            An ``ExecutionResult`` with per-step states and errors.

        Raises:
            ValueError: If the plan fails ``validate_plan``.
            CheckpointMismatchError: If an existing checkpoint belongs to
                a different plan fingerprint.
        """
        # ① validate plan
        validation_errors = validate_plan(plan)
        if validation_errors:
            message = "plan validation failed: " + "; ".join(validation_errors)
            raise ValueError(message)
        steps = [_normalise_step(raw_step) for raw_step in plan.steps]

        fingerprint = plan_fingerprint or _plan_fingerprint(plan)
        task_root = Path(task_root)

        # ② create step directories (§10.3 layout)
        work_dir = task_root / "WORK"
        runtime_dir = work_dir / "00_RUNTIME"
        result_dir = task_root / "RESULT"

        dirs_to_create: set[Path] = {runtime_dir, result_dir}
        for step in steps:
            dir_name = _step_dir_name(step.kind)
            if dir_name is not None:
                dirs_to_create.add(work_dir / dir_name)
        for d in dirs_to_create:
            d.mkdir(parents=True, exist_ok=True)

        # ⑤ resume from checkpoint
        checkpoint = load_checkpoint(runtime_dir, fingerprint)
        completed_indices: set[int] = set()
        if checkpoint is not None:
            for idx, state_data in enumerate(checkpoint.step_states):
                if isinstance(state_data, dict) and state_data.get("status") == "completed":
                    completed_indices.add(idx)
            logger.info(
                "resuming from checkpoint: %d of %d steps completed",
                len(completed_indices),
                len(steps),
            )

        # initialise step states
        step_states: list[StepState] = []
        for idx, step in enumerate(steps):
            if idx in completed_indices:
                step_states.append(StepState(index=idx, kind=step.kind, status="skipped"))
            else:
                step_states.append(StepState(index=idx, kind=step.kind))

        # resolve the first item
        if not plan.items:
            raise ValueError("plan has no items to process")
        item = _ensure_artifact(plan.items[0])

        # method from plan profile or default
        default_method = plan.profile or "r2SCAN-3c"
        base_resources: dict[str, JsonValue] = {}

        # track coordinates from optimize for downstream handoff
        handoff_coords: list[list[float]] | None = None
        handoff_symbols: list[str] | None = None

        # restore handoff from checkpoint if resuming
        if checkpoint is not None:
            saved_handoff = checkpoint.items_state.get(_HANDOFF_KEY)
            if isinstance(saved_handoff, dict):
                handoff_coords = _json_geometry(saved_handoff.get("coords"))
                handoff_symbols = _json_text_list(saved_handoff.get("symbols"))

        # ③④⑥ execute steps sequentially
        for idx, step in enumerate(steps):
            state = step_states[idx]

            # skip already-completed steps (resume)
            if state.status == "skipped":
                continue

            step_work_dir = work_dir / (_step_dir_name(step.kind) or f"step_{idx}")
            step_method = _extract_method(step.spec, default_method)

            request = _build_request(
                step.kind,
                item,
                step_method,
                base_resources,
                output_dir=step_work_dir,
                coordinates=handoff_coords,
                symbols=handoff_symbols,
            )

            # dispatch to primitive
            primitive = _PRIMITIVE_DISPATCH.get(step.kind)
            if primitive is None:
                state.status = "failed"
                state.error = f"no primitive for step kind {step.kind.value!r}"
                logger.error("step %d: %s", idx, state.error)
                self._persist_checkpoint(
                    runtime_dir,
                    fingerprint,
                    plan,
                    step_states,
                    handoff_coords,
                    handoff_symbols,
                )
                continue

            try:
                logger.info("step %d: running %s", idx, step.kind.value)
                result = primitive(request)
            except Exception as exc:
                state.status = "failed"
                state.error = str(exc) or type(exc).__name__
                logger.exception("step %d (%s) failed", idx, step.kind.value)
                self._persist_checkpoint(
                    runtime_dir,
                    fingerprint,
                    plan,
                    step_states,
                    handoff_coords,
                    handoff_symbols,
                )
                continue

            state.result = result
            if result.status == "failed":
                state.status = "failed"
                state.error = "; ".join(result.errors) or "step returned failed status"
                logger.warning(
                    "step %d (%s) returned failure: %s",
                    idx,
                    step.kind.value,
                    state.error,
                )
            else:
                state.status = "completed"
                logger.info("step %d (%s) completed", idx, step.kind.value)

            # ④ coordinate handoff from optimize → downstream steps
            if step.kind in _COORD_PRODUCING_KINDS and result.coords is not None:
                handoff_coords = [[float(v) for v in row] for row in result.coords]
                handoff_symbols = list(item.elements)

            # ⑤ write checkpoint after each step
            self._persist_checkpoint(
                runtime_dir,
                fingerprint,
                plan,
                step_states,
                handoff_coords,
                handoff_symbols,
            )

        # ⑦ finalize: write RESULT/result_manifest.json
        overall_status = "completed"
        all_errors: list[str] = []
        for state in step_states:
            if state.status == "failed":
                overall_status = "failed"
                if state.error:
                    all_errors.append(f"step {state.index} ({state.kind.value}): {state.error}")

        self._write_result_manifest(
            result_dir=result_dir,
            plan=plan,
            step_states=step_states,
            status=overall_status,
        )

        return ExecutionResult(
            status=overall_status,
            step_states=step_states,
            errors=all_errors,
        )

    # ── private helpers ─────────────────────────────────────────────────

    @staticmethod
    def _persist_checkpoint(
        runtime_dir: Path,
        fingerprint: str,
        plan: CalculationPlan,
        step_states: list[StepState],
        handoff_coords: list[list[float]] | None,
        handoff_symbols: list[str] | None,
    ) -> None:
        """Persist checkpoint including coordinate handoff state."""
        items_state: dict[str, JsonValue] = {}
        if handoff_coords is not None:
            items_state[_HANDOFF_KEY] = {
                "coords": _json_geometry_value(handoff_coords),
                "symbols": _json_text_list_value(handoff_symbols or []),
            }
        cp = Checkpoint(
            task_id="executor",
            workflow=plan.workflow,
            plan_fingerprint=fingerprint,
            step_states=[s.to_dict() for s in step_states],
            items_state=items_state,
            attempts=0,
        )
        write_checkpoint(runtime_dir, cp)

    @staticmethod
    def _write_result_manifest(
        result_dir: Path,
        plan: CalculationPlan,
        step_states: list[StepState],
        status: str,
    ) -> None:
        """Write the unique ``RESULT/result_manifest.json``."""
        manifest = ResultManifest(
            task_id="executor",
            workflow=plan.workflow,
            status=status,
        )

        for state in step_states:
            if state.result is None:
                continue
            product_kind = _product_kind_for_step(state.kind)
            product_id = f"step_{state.index}_{state.kind.value}"
            label = f"{state.kind.value} (step {state.index})"

            # register artifacts from the step result
            for artifact in state.result.artifacts:
                try:
                    rel_path = str(artifact.path.relative_to(result_dir.parent))
                except ValueError:
                    rel_path = str(artifact.path)
                manifest.add_product(
                    id=f"{product_id}_{artifact.type}",
                    label=f"{label} — {artifact.type}",
                    path=rel_path,
                    kind=product_kind,
                )

            # register energy as a file product if available
            if state.result.energy is not None:
                manifest.add_product(
                    id=f"{product_id}_energy",
                    label=f"{label} — energy",
                    path="",
                    kind=ProductKind.ENERGY_REPORT,
                )

        manifest.write(result_dir)


__all__ = [
    "CalculationPlanExecutor",
    "ExecutionResult",
    "StepState",
]
