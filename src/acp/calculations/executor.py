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

The executor deliberately does NOT recognise retired stage-workflow semantics
(numbered phases, orchestrator-level review, or promote) — those concepts live in the
orchestrator layer above.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from acp.calculations.checkpoint import (
    load_checkpoint,
    write_checkpoint,
)
from acp.calculations.contracts import (
    ArtifactRef,
    CalculationPlan,
    CalculationRequest,
    CalculationResult,
    CalculationStep,
    Checkpoint,
    ExecutionPolicy,
    JsonValue,
    OptimizationMode,
    OptimizationSpec,
    StabilityMode,
    StepKind,
    StructureArtifact,
    electronic_state_config_from_dict,
    resolve_execution_policy,
    validate_plan,
)
from acp.calculations.identity import (
    IDENTITY_SCHEMA,
    compute_identity,
    current_config_digest,
    identity_fingerprint,
)
from acp.calculations.primitives.casscf import run_casscf
from acp.calculations.primitives.frequency import run_frequency
from acp.calculations.primitives.optimize import run_optimize
from acp.calculations.primitives.scan import run_scan
from acp.calculations.primitives.singlepoint import run_singlepoint
from acp.calculations.primitives.thermochemistry import execute_thermochemistry
from acp.calculations.result_publication import (
    ArtifactReference,
    ScientificResultRecord,
    load_publication_state,
    load_scientific_result,
    publish_result,
    register_result_manifest,
)
from acp.calculations.step_requirements import (
    SATISFIED,
    UPSTREAM_FAILED,
    PriorStep,
    RequirementOutcome,
    evaluate_prerequisite,
    payload_is_diagnostic,
)
from acp.calculations.step_result import (
    STEP_RESULT_FILENAME,
    STEP_RESULT_SCHEMA_VERSION,
    ResumeSource,
    dependency_artifacts,
    file_sha256,
    json_safe,
    locate_recorded_file,
    portable_path,
    read_step_result,
    resolve_resume_source,
    verify_step_result,
    write_step_result,
)
from acp.storage.manifest import MANIFEST_FILENAME, ProductKind, ResultManifest
from cccp.calculation.tasks.casscf import validate_casscf_completion

logger = logging.getLogger(__name__)

# ── step-kind → WORK/ subdirectory (§10.3) ──────────────────────────────
_STEP_DIRS: dict[StepKind, str] = {
    StepKind.OPTIMIZE: "03_OPT",
    StepKind.FREQUENCY: "04_FREQ",
    StepKind.SINGLEPOINT: "05_SP",
    StepKind.THERMOCHEMISTRY: "06_THERMO",
    StepKind.SCAN: "07_PATH",
    StepKind.CASSCF: "08_CASSCF",
}


def _resource_float(resources: Mapping[str, JsonValue], key: str) -> float | None:
    value = resources.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _run_thermochemistry(request: CalculationRequest) -> CalculationResult:
    freq_log = request.resources.get("freq_log_path")
    sp_energy = _resource_float(request.resources, "sp_energy_hartree")
    temperature = _resource_float(request.resources, "temperature") or 298.15
    pressure = _resource_float(request.resources, "pressure") or 1.0
    if not isinstance(freq_log, str) or not freq_log:
        return CalculationResult(
            status="failed",
            errors=["thermochemistry requires a frequency log"],
        )
    if sp_energy is None:
        return CalculationResult(
            status="failed",
            errors=["thermochemistry requires a single-point energy"],
        )
    resources = dict(request.resources)
    resources["temperature"] = temperature
    resources["pressure"] = pressure
    resources.setdefault("standard_state", "1atm")
    if "output_file" not in resources:
        raw_output_dir = resources.get("output_dir")
        if isinstance(raw_output_dir, str) and raw_output_dir:
            legacy_dir = Path(raw_output_dir)
        else:
            freq_parent = Path(freq_log).parent
            legacy_dir = freq_parent if freq_parent != Path(".") else Path.cwd()
        resources["output_file"] = str(legacy_dir / "Shermo.sum")
    return execute_thermochemistry(replace(request, resources=resources))


def _step_resources(step: CalculationStep) -> dict[str, JsonValue]:
    if isinstance(step.spec, dict):
        return dict(step.spec)
    if isinstance(step.spec, OptimizationSpec) and step.spec.method:
        return {"method": step.spec.method}
    return {}


def _frequency_log_path(result: CalculationResult) -> Path | None:
    for artifact in reversed(result.artifacts):
        if artifact.type in {"frequency_log", "log"}:
            return artifact.path
    return None


# ── T16 publication contract helpers (scientific result → publish → done) ──


def _step_result_id(fingerprint: str, idx: int, kind: StepKind) -> str:
    return f"{fingerprint}-step{idx}-{kind.value}"


def _step_id(idx: int, kind: StepKind) -> str:
    """Stable per-step id — same rule as the manifest ``step_{index}_{kind}``.

    Shared by every step and by the appended §9.5 stability node so a
    resumed run maps results back to the same identity (Metis m4).
    """
    return f"step_{idx}_{kind.value}"


def _stability_step_identity(identity: object, index: int) -> str | None:
    """Science identity of the derived stability diagnostic node.

    Not part of the plan's per-step tuple (the node has no plan step), so
    it is bound to the full plan identity: any science change upstream
    invalidates it, and identical plans reproduce it across resumes.
    """
    plan_identity = getattr(identity, "plan_identity", None)
    if not isinstance(plan_identity, str):
        return None
    return identity_fingerprint(
        {"scope": "stability", "index": index, "plan_identity": plan_identity}
    )


def _step_scientific_record(
    result: CalculationResult,
    *,
    result_id: str,
    kind: StepKind,
    result_dir: Path,
) -> ScientificResultRecord:
    root = result_dir.resolve()
    artifacts: list[ArtifactReference] = []
    for artifact in result.artifacts:
        path = Path(artifact.path)
        try:
            rel: Path | str = path.resolve().relative_to(root)
        except ValueError:
            rel = path
        artifacts.append(ArtifactReference(path=str(rel), type=artifact.type))
    summary: dict[str, object] = {"status": result.status, "errors": list(result.errors)}
    if result.metadata:
        # Completion facts (e.g. CAS convergence) ride the durable record so
        # the publish-retry recovery entry can run the shared validator
        # without re-executing QC (plan todo 11).
        summary["metadata"] = dict(json_safe(result.metadata) or {})
    if result.energy is not None:
        summary["energy_hartree"] = result.energy
    if result.coords is not None:
        summary["coords"] = [[float(v) for v in row] for row in result.coords]
    if result.frequencies:
        summary["frequencies"] = [float(f) for f in result.frequencies]
    return ScientificResultRecord(
        result_id=result_id,
        kind=kind.value,
        artifacts=tuple(artifacts),
        summary=summary,
    )


def _step_publication_manifest(record: ScientificResultRecord) -> ResultManifest:
    manifest = ResultManifest(task_id=record.result_id, workflow=record.kind, status="completed")
    for artifact in record.artifacts:
        manifest.add_product(artifact.path, artifact.path, artifact.path, ProductKind.FILE)
    return manifest


def _step_result_from_record(record: ScientificResultRecord, result_dir: Path) -> CalculationResult:
    summary = record.summary
    energy_raw = summary.get("energy_hartree")
    coords_raw = summary.get("coords")
    freq_raw = summary.get("frequencies")
    raw_metadata = summary.get("metadata")
    return CalculationResult(
        energy=float(energy_raw) if isinstance(energy_raw, (int, float)) else None,
        coords=(
            [[float(v) for v in row] for row in coords_raw]
            if isinstance(coords_raw, list)
            else None
        ),
        frequencies=[float(f) for f in freq_raw] if isinstance(freq_raw, list) else [],
        artifacts=[
            ArtifactRef(path=result_dir / artifact.path, type=artifact.type)
            for artifact in record.artifacts
        ],
        status=str(summary.get("status", "completed")),
        errors=[str(e) for e in summary.get("errors") or []],
        metadata=dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {},
    )


# ── step-kind → primitive callable ──────────────────────────────────────
_PRIMITIVE_DISPATCH: dict[StepKind, Callable[[CalculationRequest], CalculationResult]] = {
    StepKind.SINGLEPOINT: run_singlepoint,
    StepKind.OPTIMIZE: run_optimize,
    StepKind.FREQUENCY: run_frequency,
    StepKind.SCAN: run_scan,
    StepKind.THERMOCHEMISTRY: _run_thermochemistry,
    StepKind.CASSCF: run_casscf,
}

# Step kinds whose results feed coordinates into downstream steps.
_COORD_PRODUCING_KINDS: frozenset[StepKind] = frozenset({StepKind.OPTIMIZE})

_HANDOFF_KEY = "__handoff__"


@dataclass
class _Handoff:
    """Full downstream handoff (V03): geometry, freq log, SP energy + unit.

    ``frequency_log_path`` is stored relative to the task root (portable
    across attempt archives); ``single_point_energy`` is always hartree —
    the unit of the ``sp_energy_hartree`` thermo parameter.  Geometry is
    bound to its producing step via ``geometry_identity`` (step index +
    coords sha256 + symbols).
    """

    coords: list[list[float]] | None = None
    symbols: list[str] | None = None
    frequency_log_path: str | None = None
    single_point_energy: float | None = None
    energy_unit: str = "hartree"
    geometry_step_index: int | None = None
    geometry_coords_sha256: str | None = None

    @staticmethod
    def _coords_sha256(coords: list[list[float]]) -> str:
        blob = json.dumps(coords, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()

    def set_geometry(self, step_index: int, coords: list[list[float]], symbols: list[str]) -> None:
        self.coords = [[float(value) for value in row] for row in coords]
        self.symbols = list(symbols)
        self.geometry_step_index = step_index
        self.geometry_coords_sha256 = self._coords_sha256(self.coords)

    def set_frequency_log(self, path: Path | str | None, task_root: Path) -> None:
        if path is None:
            return
        self.frequency_log_path = portable_path(path, task_root)

    def set_energy(self, energy: float) -> None:
        self.single_point_energy = float(energy)
        self.energy_unit = "hartree"

    def locate_frequency_log(self, roots: tuple[Path, ...]) -> Path | None:
        if not self.frequency_log_path:
            return None
        return locate_recorded_file(roots, self.frequency_log_path)

    def to_checkpoint_value(self) -> dict[str, JsonValue] | None:
        payload: dict[str, JsonValue] = {}
        if self.coords is not None:
            payload["coords"] = _json_geometry_value(self.coords)
            payload["symbols"] = _json_text_list_value(self.symbols or [])
        if self.frequency_log_path is not None:
            payload["frequency_log_path"] = self.frequency_log_path
        if self.single_point_energy is not None:
            payload["single_point_energy"] = self.single_point_energy
            payload["energy_unit"] = self.energy_unit
        if (
            self.coords is not None
            and self.geometry_step_index is not None
            and self.geometry_coords_sha256 is not None
        ):
            payload["geometry_identity"] = {
                "step_index": self.geometry_step_index,
                "coords_sha256": self.geometry_coords_sha256,
                "symbols": _json_text_list_value(self.symbols or []),
            }
        return payload or None

    @classmethod
    def from_checkpoint(cls, raw: JsonValue | None) -> _Handoff:
        handoff = cls()
        if not isinstance(raw, dict):
            return handoff
        handoff.coords = _json_geometry(raw.get("coords"))
        handoff.symbols = _json_text_list(raw.get("symbols")) or None
        frequency_log_path = raw.get("frequency_log_path")
        if isinstance(frequency_log_path, str) and frequency_log_path:
            handoff.frequency_log_path = frequency_log_path
        energy = raw.get("single_point_energy")
        if isinstance(energy, (int, float)) and not isinstance(energy, bool):
            unit = raw.get("energy_unit")
            if unit is None or unit == "hartree":
                handoff.single_point_energy = float(energy)
            else:
                logger.warning("handoff energy unit %r unsupported — energy not restored", unit)
        geometry_identity = raw.get("geometry_identity")
        if isinstance(geometry_identity, dict):
            step_index = geometry_identity.get("step_index")
            if isinstance(step_index, int) and not isinstance(step_index, bool):
                handoff.geometry_step_index = step_index
            coords_sha256 = geometry_identity.get("coords_sha256")
            if isinstance(coords_sha256, str):
                handoff.geometry_coords_sha256 = coords_sha256
        return handoff


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


def legacy_plan_fingerprint(plan: CalculationPlan) -> str:
    """Legacy (pre-v2) deterministic hash of the plan content.

    Kept for old-checkpoint compatibility and fixture generation only —
    ``execute`` binds checkpoints with the v2 identity from
    :mod:`acp.calculations.identity` (``legacy fingerprints alone never
    authorize reuse``).
    """
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


#: Back-compat alias (fixtures + cross-version tests import ``_plan_fingerprint``).
_plan_fingerprint = legacy_plan_fingerprint


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


def _extract_method_from_resources(resources: dict[str, JsonValue], default: str) -> str:
    """Extract method from merged step resources, falling back to *default*."""
    method = resources.get("method")
    return str(method) if isinstance(method, str) and method else default


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


def _diagnostic_metadata(base: dict[str, object], diagnostic: bool) -> dict[str, object]:
    """Stamp ``diagnostic_only`` on a product; diagnostics are never reusable."""
    if not diagnostic:
        return base
    merged = {key: value for key, value in base.items() if key != "auto_reusable"}
    merged["diagnostic_only"] = True
    return merged


# ── public data classes ─────────────────────────────────────────────────


@dataclass
class StepState:
    """Mutable per-step execution record.

    ``executed_this_run`` separates "executed during this run" from the
    durable ``status`` fact: a step recovered from the checkpoint keeps
    ``status="completed"`` while ``executed_this_run=False`` (V01).  The
    legacy ``skipped`` status remains available for strategic skipping; a
    resume never writes it.  ``blocked`` is a STEP-level status word only
    (never a global ``JobStatus``): an unmet prerequisite under the default
    execution policy, with the machine-readable ``blocked_reason``.
    """

    index: int
    kind: StepKind
    status: str = "pending"  # pending | completed | failed | blocked | skipped
    result: CalculationResult | None = None
    error: str = ""
    #: ``upstream_failed`` | ``missing_requirement`` when ``status == "blocked"``.
    blocked_reason: str = ""
    executed_this_run: bool = True
    last_executed_attempt: int | None = None
    #: Durable ``step_result.json`` reference (V02, contract C): path is
    #: relative to the task root, ``sha256`` binds the file content.
    result_ref: dict[str, str] | None = None
    #: ``jobs.attempt`` of the attempt whose science was adopted (V02).
    reused_from_attempt: int | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise for checkpoint persistence."""
        return {
            "index": self.index,
            "kind": self.kind.value,
            "status": self.status,
            "error": self.error,
            "blocked_reason": self.blocked_reason,
            "energy": self.result.energy if self.result else None,
            "executed_this_run": self.executed_this_run,
            "last_executed_attempt": self.last_executed_attempt,
            "result_ref": dict(self.result_ref) if self.result_ref else None,
            "reused_from_attempt": self.reused_from_attempt,
        }


def _jobs_attempt(task_root: Path) -> int | None:
    """Read ``jobs.attempt`` from the scheduler ``job.json`` marker (V01).

    Returns ``None`` for CLI runs without a scheduler job — there is no
    ``jobs.attempt`` to record and no second attempt counter is invented.
    """
    try:
        payload: object = json.loads((task_root / "job.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("attempt")
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    return None


def _jobs_job_id(task_root: Path) -> str | None:
    """Read the scheduler job id from ``job.json`` (execution identity only)."""
    try:
        payload: object = json.loads((task_root / "job.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    job_id = payload.get("id")
    return job_id if isinstance(job_id, str) and job_id else None


@dataclass(frozen=True)
class _Adoption:
    """Outcome of one V02 step_result adoption attempt."""

    result: CalculationResult | None = None
    reason: str = ""
    integrity_failed: bool = False
    attempt: int | None = None
    result_ref: dict[str, str] | None = None


def _merge_completed_facts(
    fresh: list[dict[str, JsonValue]],
    loaded: Mapping[int, dict[str, JsonValue]],
) -> list[dict[str, JsonValue]]:
    """Persisted step dicts merged with checkpoint-completed facts (V01).

    A fresh ``pending`` state never overwrites a loaded ``completed`` fact;
    for steps still reported ``completed`` but not executed this run, prior
    recorded facts (``energy``/``error``/``last_executed_attempt``) fill
    fields this run never re-populated.  Fresh ``failed``/``completed``
    (executed) states win over the loaded record.
    """
    if not loaded:
        return fresh
    merged: list[dict[str, JsonValue]] = []
    for idx, state in enumerate(fresh):
        prior = loaded.get(idx)
        if prior is None or prior.get("status") != "completed":
            merged.append(state)
            continue
        if state.get("status") == "pending":
            merged.append(dict(prior))
            continue
        combined = dict(state)
        if combined.get("energy") is None:
            combined["energy"] = prior.get("energy")
        if not combined.get("error"):
            combined["error"] = prior.get("error")
        if combined.get("last_executed_attempt") is None:
            combined["last_executed_attempt"] = prior.get("last_executed_attempt")
        merged.append(combined)
    return merged


@dataclass
class ExecutionResult:
    """Outcome of executing a ``CalculationPlan``.

    ``status`` is ``failed`` when any step failed OR any required step is
    blocked.  ``errors`` keeps ONLY the original step failures; blocked
    steps are reported separately via ``blocked_reasons``
    (``{"index", "reason"}`` entries) so the root cause is never lost.
    """

    status: str = "completed"  # completed | failed
    step_states: list[StepState] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    blocked_reasons: list[dict[str, JsonValue]] = field(default_factory=list)

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

    Failure isolation (D07): before each step the ``StepRequirement``
    table (``acp.calculations.step_requirements``) is re-evaluated — on
    every run, resume included.  An unmet prerequisite under the default
    ``block`` policy marks the step ``blocked`` (with ``blocked_reason``)
    without invoking its primitive; dependents block transitively while
    independent steps still execute.  The explicit ``diagnostics`` policy
    lets the step run, but the result and its manifest products carry
    ``metadata["diagnostic_only"]=True`` and can never satisfy a normal
    downstream prerequisite.  The overall status is ``"failed"`` if any
    step failed or any required step is blocked.

    Resume: on restart the executor loads the checkpoint from
    ``WORK/00_RUNTIME``.  Steps already marked ``"completed"`` keep that
    status for this run and are not re-executed — ``executed_this_run=False``
    records the separate "not executed here" observation.  A fingerprint
    mismatch never raises — the checkpoint is
    ignored (conservative recompute) and per-step adoption continues from
    ``resume_source`` when available.  Diagnostic purposes persisted in
    ``step_result.json`` are re-judged: a diagnostic result is never
    adopted as a normal completed result under the default policy.
    """

    def __init__(
        self,
        *,
        backend_factory: Callable[..., object] | None = None,
    ) -> None:
        # backend_factory is accepted for API compatibility but the
        # primitives resolve backends internally via the cccp registry.
        self._backend_factory = backend_factory
        self._execution_record: dict[str, JsonValue] = {}
        self._jobs_attempt: int | None = None
        self._job_id: str | None = None
        self._resume_count: int = 0
        self._loaded_completed_facts: dict[int, dict[str, JsonValue]] = {}
        self._execution_policy: ExecutionPolicy = ExecutionPolicy()

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
                fingerprint.  When ``None`` the v2 science identity of the
                plan is used (content-bound effective parameters).

        Returns:
            An ``ExecutionResult`` with per-step states and errors.

        Raises:
            ValueError: If the plan fails ``validate_plan`` or has no items.
            IdentityInputMissing: If a plan item's structure file is
                unreadable (identity cannot be computed).

        A fingerprint mismatch does NOT raise ``CheckpointMismatchError``:
        the stale checkpoint is ignored and steps recompute conservatively.
        """
        # ① validate plan
        validation_errors = validate_plan(plan)
        if validation_errors:
            message = "plan validation failed: " + "; ".join(validation_errors)
            raise ValueError(message)
        steps = [_normalise_step(raw_step) for raw_step in plan.steps]
        self._execution_policy = resolve_execution_policy(plan)

        identity = None if plan_fingerprint is not None else compute_identity(plan)
        fingerprint = plan_fingerprint if plan_fingerprint is not None else identity.plan_identity
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

        # ⑤ resume from checkpoint (v2 identity compare; never raises).
        # V02: ``continue``/edit-recalculate read the attempt referenced by
        # ``resume_source.json`` (archived attempt when it owns the science,
        # the in-place attempt otherwise); a receipt declared protocol-
        # incompatible refuses adoption entirely (full recompute).  ``rerun``
        # never carries the receipt, so it always starts clean.
        self._jobs_attempt = _jobs_attempt(task_root)
        self._job_id = _jobs_job_id(task_root)
        resume_source: ResumeSource | None = resolve_resume_source(task_root, kind="executor")
        if resume_source is not None and not resume_source.compatible:
            logger.warning(
                "recovery.protocol_incompatible: %s — full recompute (no adoption)",
                resume_source.reason or "declared incompatible",
            )
        adoption_enabled = resume_source is None or resume_source.compatible
        science_root = resume_source.science_root if resume_source is not None else task_root
        checkpoint_dir = runtime_dir
        if resume_source is not None and resume_source.compatible:
            checkpoint_dir = resume_source.checkpoint_dir
        checkpoint = load_checkpoint(checkpoint_dir, fingerprint) if adoption_enabled else None
        config_changed = False
        if checkpoint is not None:
            checkpoint, config_changed = self._drop_on_config_digest_change(checkpoint)
        if checkpoint is not None and identity is not None:
            self._log_path_remaps(checkpoint, plan)
        self._resume_count = checkpoint.resume_count + 1 if checkpoint is not None else 0
        completed_indices: set[int] = set()
        self._loaded_completed_facts: dict[int, dict[str, JsonValue]] = {}
        if checkpoint is not None:
            for idx, state_data in enumerate(checkpoint.step_states):
                if isinstance(state_data, dict) and state_data.get("status") == "completed":
                    completed_indices.add(idx)
                    self._loaded_completed_facts[idx] = state_data
            logger.info(
                "resuming from checkpoint: %d of %d steps completed",
                len(completed_indices),
                len(steps),
            )

        self._execution_record = self._build_execution_record(checkpoint, self._jobs_attempt)

        # initialise step states — V01: loaded completed facts keep their
        # status; "not executed in this run" is the separate
        # ``executed_this_run`` observation (the loop skips those steps
        # without rewriting ``status``).
        step_states: list[StepState] = []
        for idx, step in enumerate(steps):
            if idx in completed_indices:
                prior = self._loaded_completed_facts[idx]
                last_attempt = prior.get("last_executed_attempt")
                step_states.append(
                    StepState(
                        index=idx,
                        kind=step.kind,
                        status="completed",
                        executed_this_run=False,
                        last_executed_attempt=(
                            last_attempt
                            if isinstance(last_attempt, int) and not isinstance(last_attempt, bool)
                            else None
                        ),
                    )
                )
            else:
                step_states.append(StepState(index=idx, kind=step.kind))

        # resolve the first item
        if not plan.items:
            raise ValueError("plan has no items to process")
        item = _ensure_artifact(plan.items[0])

        # method from plan profile or default
        default_method = plan.profile or "r2SCAN-3c"
        base_resources: dict[str, JsonValue] = {}

        # track the downstream handoff (geometry / freq log / SP energy)
        handoff = _Handoff.from_checkpoint(
            checkpoint.items_state.get(_HANDOFF_KEY) if checkpoint is not None else None
        )

        # ③④⑥ execute steps sequentially
        for idx, step in enumerate(steps):
            state = step_states[idx]
            integrity_failed = False

            # D07 prerequisite gate — re-evaluated on EVERY run (resume
            # included): a loaded completed fact never outranks an unmet
            # prerequisite, and a diagnostic purpose is re-judged here.
            prerequisite = SATISFIED
            if state.status != "skipped":
                prior_states = [
                    PriorStep(
                        kind=prior.kind,
                        status=prior.status,
                        result=prior.result,
                    )
                    for prior in step_states[:idx]
                ]
                # D07/invariant 4 (T10/D5): a failed or blocked CASSCF step
                # is an unmet required upstream — every later plan step is
                # blocked (upstream_failed) instead of consuming an
                # incomplete multireference stage.
                if any(
                    prior.kind is StepKind.CASSCF and prior.status in ("failed", "blocked")
                    for prior in prior_states
                ):
                    prerequisite = RequirementOutcome(satisfied=False, reason=UPSTREAM_FAILED)
                else:
                    prerequisite = evaluate_prerequisite(
                        prior_states,
                        step.kind,
                        item.elements,
                    )
            run_diagnostic = False
            if not prerequisite.satisfied:
                if self._execution_policy.upstream_failure == "block":
                    state.status = "blocked"
                    state.blocked_reason = prerequisite.reason
                    state.executed_this_run = True
                    state.result = None
                    state.result_ref = None
                    logger.warning(
                        "step %d (%s) blocked: %s — prerequisite not met",
                        idx,
                        step.kind.value,
                        prerequisite.reason,
                    )
                    self._write_result_manifest_tolerant(result_dir, plan, step_states, "running")
                    self._persist_checkpoint(
                        runtime_dir,
                        fingerprint,
                        plan,
                        step_states,
                        handoff,
                    )
                    continue
                run_diagnostic = True
                logger.warning(
                    "step %d (%s): prerequisite not met (%s) — running under "
                    "the diagnostics policy (results marked diagnostic_only)",
                    idx,
                    step.kind.value,
                    prerequisite.reason,
                )

            if state.status != "skipped":
                # V02 adoption: a verified durable step_result means the
                # science already happened (resume, crash window between
                # result and checkpoint, publication retry) — never re-run QC.
                step_rel = _step_dir_name(step.kind) or f"step_{idx}"
                step_work_dir = work_dir / step_rel
                step_identity = (
                    identity.step_identities[idx]
                    if identity is not None and idx < len(identity.step_identities)
                    else None
                )
                was_completed = state.status == "completed" and not state.executed_this_run
                result_id = _step_result_id(fingerprint, idx, step.kind)
                adoption = self._adopt_step_result(
                    idx=idx,
                    step_identity=step_identity,
                    science_root=science_root,
                    task_root=task_root,
                    rel=f"WORK/{step_rel}/{STEP_RESULT_FILENAME}",
                    enabled=adoption_enabled and not config_changed,
                    was_completed=was_completed,
                    kind=step.kind,
                )
                if adoption.result is not None:
                    state.result = adoption.result
                    if run_diagnostic:
                        state.result = replace(
                            state.result,
                            metadata={**state.result.metadata, "diagnostic_only": True},
                        )
                    state.status = "completed"
                    state.executed_this_run = False
                    state.reused_from_attempt = adoption.attempt
                    state.result_ref = adoption.result_ref
                    logger.info(
                        "recovery.step_adopted: step %d (%s) reused from attempt %s",
                        idx,
                        step.kind.value,
                        adoption.attempt,
                    )
                    publish_error = self._ensure_publication(
                        step_work_dir=step_work_dir,
                        result=adoption.result,
                        result_id=result_id,
                        kind=step.kind,
                    )
                    if publish_error:
                        state.status = "failed"
                        state.error = publish_error
                    if state.status == "completed":
                        if step.kind is StepKind.FREQUENCY:
                            handoff.set_frequency_log(
                                _frequency_log_path(adoption.result), task_root
                            )
                        elif (
                            step.kind is StepKind.SINGLEPOINT and adoption.result.energy is not None
                        ):
                            handoff.set_energy(adoption.result.energy)
                        if (
                            step.kind in _COORD_PRODUCING_KINDS
                            and adoption.result.coords is not None
                        ):
                            handoff.set_geometry(
                                idx,
                                [[float(value) for value in row] for row in adoption.result.coords],
                                list(item.elements),
                            )
                    self._write_result_manifest_tolerant(result_dir, plan, step_states, "running")
                    self._persist_checkpoint(
                        runtime_dir,
                        fingerprint,
                        plan,
                        step_states,
                        handoff,
                    )
                    continue
                if adoption.reason:
                    integrity_failed = adoption.integrity_failed
                    logger.warning(
                        "recovery.step_not_adopted: step %d (%s): %s — recomputing",
                        idx,
                        step.kind.value,
                        adoption.reason,
                    )
                    # the stale completed fact must not survive the merge
                    self._loaded_completed_facts.pop(idx, None)
                    state.status = "pending"
                    state.executed_this_run = True
                    state.result = None
                    state.result_ref = None

            # V01 resume: completed facts loaded from the checkpoint are not
            # re-executed and their status is NOT rewritten; "skipped" stays
            # reserved for strategic skipping.
            if state.status == "skipped" or (
                state.status == "completed" and not state.executed_this_run
            ):
                continue

            step_work_dir = work_dir / (_step_dir_name(step.kind) or f"step_{idx}")
            step_method = _extract_method(step.spec, default_method)

            step_resources = _step_resources(step)
            if step.kind is StepKind.THERMOCHEMISTRY:
                freq_log = handoff.locate_frequency_log((science_root, task_root))
                if freq_log is not None:
                    step_resources["freq_log_path"] = str(freq_log)
                if handoff.single_point_energy is not None:
                    step_resources["sp_energy_hartree"] = handoff.single_point_energy

            request = _build_request(
                step.kind,
                item,
                step_method,
                {**base_resources, **step_resources},
                output_dir=step_work_dir,
                coordinates=handoff.coords,
                symbols=handoff.symbols,
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
                    handoff,
                )
                continue

            result_id = _step_result_id(fingerprint, idx, step.kind)
            prior_record = load_scientific_result(step_work_dir)
            # An incompatible resume source refuses ALL reuse — including
            # the legacy publication-only branch (unverifiable → recompute).
            recovered = (
                adoption_enabled
                and not integrity_failed
                and not config_changed
                and prior_record is not None
                and prior_record.result_id == result_id
            )
            if recovered and prior_record is not None and step.kind is StepKind.CASSCF:
                # Shared science gate (plan todo 11): restoring a completed
                # result from the WORK-layer scientific record must pass the
                # SAME validator the step_result adoption path calls.  The
                # record carries no per-artifact digests, so log re-judgement
                # is not allowed here — a record whose CAS convergence fact
                # is false/absent recomputes instead of restoring completed.
                candidate = _step_result_from_record(prior_record, step_work_dir)
                verdict = validate_casscf_completion(
                    candidate.metadata,
                    artifact_paths=[Path(artifact.path) for artifact in candidate.artifacts],
                    integrity_valid=True,
                    allow_log_rejudge=False,
                )
                if not verdict.passed:
                    logger.warning(
                        "recovery.scientific_result_not_reusable: step %d (%s): %s — recomputing",
                        idx,
                        step.kind.value,
                        verdict.reason,
                    )
                    recovered = False
            if recovered and prior_record is not None:
                logger.info(
                    "step %d (%s): stored scientific result found — publication retry only",
                    idx,
                    step.kind.value,
                )
                try:
                    result = _step_result_from_record(prior_record, step_work_dir)
                    publish_result(
                        step_work_dir,
                        record=prior_record,
                        manifest=_step_publication_manifest(prior_record),
                    )
                except Exception as exc:
                    state.status = "failed"
                    state.error = f"publication retry failed: {exc or type(exc).__name__}"
                    logger.exception("step %d (%s) publication retry failed", idx, step.kind.value)
                    self._persist_checkpoint(
                        runtime_dir,
                        fingerprint,
                        plan,
                        step_states,
                        handoff,
                    )
                    continue
            else:
                try:
                    logger.info("step %d: running %s", idx, step.kind.value)
                    if self._jobs_attempt is not None:
                        state.last_executed_attempt = self._jobs_attempt
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
                        handoff,
                    )
                    continue

            if run_diagnostic:
                result = replace(
                    result,
                    metadata={**result.metadata, "diagnostic_only": True},
                )
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
                # Contract C order: ① durable step_result + artifact digests →
                # ② checkpoint references the result → ③ manifest publish →
                # ④ publish-complete marker.  A publication failure stays a
                # publication failure: the next resume adopts the result and
                # retries only the publish (never QC).
                self._write_step_result(
                    state=state,
                    result=result,
                    task_root=task_root,
                    step_work_dir=step_work_dir,
                    idx=idx,
                    kind=step.kind,
                    step_identity=(
                        identity.step_identities[idx]
                        if identity is not None and idx < len(identity.step_identities)
                        else None
                    ),
                    symbols=handoff.symbols if handoff.symbols else list(item.elements),
                    request=request,
                )
                state.status = "completed"
                self._persist_checkpoint(
                    runtime_dir,
                    fingerprint,
                    plan,
                    step_states,
                    handoff,
                )
                publish_error = self._ensure_publication(
                    step_work_dir=step_work_dir,
                    result=result,
                    result_id=result_id,
                    kind=step.kind,
                )
                if publish_error:
                    state.status = "failed"
                    state.error = publish_error
                    logger.warning(
                        "step %d (%s) publication failed: %s",
                        idx,
                        step.kind.value,
                        publish_error,
                    )
                elif recovered:
                    logger.info(
                        "step %d (%s) completed (publication recovered)",
                        idx,
                        step.kind.value,
                    )
                else:
                    logger.info("step %d (%s) completed", idx, step.kind.value)

            if state.status == "completed":
                if step.kind is StepKind.FREQUENCY:
                    handoff.set_frequency_log(_frequency_log_path(result), task_root)
                elif step.kind is StepKind.SINGLEPOINT and result.energy is not None:
                    handoff.set_energy(result.energy)

                if step.kind in _COORD_PRODUCING_KINDS and result.coords is not None:
                    handoff.set_geometry(
                        idx,
                        [[float(v) for v in row] for row in result.coords],
                        list(item.elements),
                    )

            # Durable structures survive a later exception or cancellation.
            self._write_result_manifest_tolerant(result_dir, plan, step_states, "running")

            # ⑤ write checkpoint after each step
            self._persist_checkpoint(
                runtime_dir,
                fingerprint,
                plan,
                step_states,
                handoff,
            )

        # ③④⑥ post-hoc stability SP node (§3.4, §9.5): OPT/FREQ never carry
        # STABPerform; a dedicated SP diagnostic runs on the final geometry
        # and is recorded as a visible step state + manifest product.
        stability_state = self._run_post_stability_node(
            plan=plan,
            steps=steps,
            item=item,
            work_dir=work_dir,
            handoff_coords=handoff.coords,
            handoff_symbols=handoff.symbols,
            base_resources=base_resources,
            identity=identity,
            fingerprint=fingerprint,
            task_root=task_root,
            science_root=science_root,
            adoption_enabled=adoption_enabled and not config_changed,
        )
        if stability_state is not None:
            step_states.append(stability_state)
            self._persist_checkpoint(
                runtime_dir,
                fingerprint,
                plan,
                step_states,
                handoff,
            )

        # ⑦ finalize: write RESULT/result_manifest.json
        # Overall status: failed on ANY failed step OR ANY blocked required
        # step — "no failed, only blocked" must never read as completed.
        overall_status = "completed"
        all_errors: list[str] = []
        blocked_reasons: list[dict[str, JsonValue]] = []
        for state in step_states:
            if state.status == "failed":
                overall_status = "failed"
                if state.error:
                    all_errors.append(f"step {state.index} ({state.kind.value}): {state.error}")
            elif state.status == "blocked":
                overall_status = "failed"
                blocked_reasons.append({"index": state.index, "reason": state.blocked_reason})

        self._write_result_manifest_tolerant(
            result_dir=result_dir,
            plan=plan,
            step_states=step_states,
            status=overall_status,
        )

        return ExecutionResult(
            status=overall_status,
            step_states=step_states,
            errors=all_errors,
            blocked_reasons=blocked_reasons,
        )

    # ── private helpers ─────────────────────────────────────────────────

    def _drop_on_config_digest_change(
        self, checkpoint: Checkpoint
    ) -> tuple[Checkpoint | None, bool]:
        """Conservative recompute when the resolved config moved since first execution.

        Returns ``(checkpoint, config_changed)`` — a mismatch drops the
        checkpoint AND suppresses stored-result adoption (attempt metadata
        is not science, but it gates reuse).
        """
        stored_record = checkpoint.items_state.get("execution_record")
        stored_digest: object = None
        if isinstance(stored_record, dict):
            stored_digest = stored_record.get("config_digest")
        if not isinstance(stored_digest, str) or not stored_digest:
            return checkpoint, False
        current_digest = current_config_digest()
        if stored_digest == current_digest:
            return checkpoint, False
        logger.info(
            "identity.config_digest_changed: stored %s != current %s — "
            "conservative recompute (attempt metadata, not science hash)",
            stored_digest,
            current_digest,
        )
        return None, True

    @staticmethod
    def _log_path_remaps(checkpoint: Checkpoint, plan: CalculationPlan) -> None:
        """Record ``identity.path_remapped`` when an item moved (content-bound)."""
        stored_paths = checkpoint.items_state.get("paths")
        if not isinstance(stored_paths, dict):
            return
        for index, raw_item in enumerate(plan.items):
            current = str(_ensure_artifact(raw_item).path)
            previous = stored_paths.get(str(index))
            if isinstance(previous, str) and previous and previous != current:
                logger.info(
                    "identity.path_remapped: item %s %s -> %s (content-bound identity unchanged)",
                    index,
                    previous,
                    current,
                )

    # ── V02: durable step results, adoption, publication retry ──────────

    def _write_step_result(
        self,
        *,
        state: StepState,
        result: CalculationResult,
        task_root: Path,
        step_work_dir: Path,
        idx: int,
        kind: StepKind,
        step_identity: str | None,
        symbols: list[str],
        request: CalculationRequest,
    ) -> None:
        """Contract C step ① — atomically persist the step's science.

        Platform execution identity (``job_id``/``attempt``/``code_release``)
        rides along for provenance but never enters ``step_identity``.
        """
        payload = result.to_step_result_dict(root=task_root)
        payload["schema_version"] = STEP_RESULT_SCHEMA_VERSION
        if result.metadata.get("diagnostic_only") is True:
            payload["diagnostic_only"] = True
        payload["step_identity"] = step_identity
        payload["step_id"] = _step_id(idx, kind)
        payload["index"] = idx
        payload["kind"] = kind.value
        payload["symbols"] = [str(symbol) for symbol in symbols]
        payload["job_id"] = self._job_id
        payload["attempt"] = self._jobs_attempt
        payload["code_release"] = str(self._execution_record.get("code_release") or "")
        payload["config_digest"] = self._execution_record.get("config_digest")
        payload["dependency_artifacts"] = dependency_artifacts(request.resources, task_root)
        path = step_work_dir / STEP_RESULT_FILENAME
        digest = write_step_result(path, payload)
        state.result_ref = {"path": portable_path(path, task_root), "sha256": digest}

    def _adopt_step_result(
        self,
        *,
        idx: int,
        step_identity: str | None,
        science_root: Path,
        task_root: Path,
        rel: str,
        enabled: bool,
        was_completed: bool,
        kind: StepKind | None = None,
    ) -> _Adoption:
        """Verify the durable ``step_result.json`` for one step (V02 gate).

        Adoption requires ``step_identity`` equality (todo-10 science
        identity) AND every recorded artifact present with an equal sha256.
        Anything unverifiable → ``reason`` set with ``integrity_failed`` so
        the caller recomputes and the legacy publication-only branch stays
        suppressed for that step.

        For a CASSCF step the verified receipt must additionally pass the
        shared science validator (:func:`validate_casscf_completion`) —
        identity/digest validity alone never proves CAS convergence.
        """
        located: Path | None = None
        for base in (science_root, task_root):
            candidate = base / rel
            if candidate.is_file():
                located = candidate
                break
        if located is None:
            if was_completed:
                return _Adoption(reason="step_result_missing", integrity_failed=True)
            return _Adoption()
        if not enabled:
            return _Adoption(reason="adoption_disabled", integrity_failed=True)
        payload = read_step_result(located)
        if payload is None:
            return _Adoption(reason="step_result_unreadable", integrity_failed=True)
        # D07 resume re-judgement: a diagnostic purpose persisted in
        # step_result.json must never be adopted as a normal result under
        # the default (block) policy — the step recomputes instead.
        if self._execution_policy.upstream_failure != "diagnostics" and payload_is_diagnostic(
            payload
        ):
            return _Adoption(
                reason="diagnostic_result_not_reusable",
                integrity_failed=True,
            )

        prior_ref: Mapping[str, JsonValue] | None = None
        if was_completed:
            raw_ref = self._loaded_completed_facts.get(idx, {}).get("result_ref")
            if isinstance(raw_ref, dict):
                prior_ref = raw_ref
        reason = verify_step_result(
            payload,
            expected_identity=step_identity,
            roots=(science_root, task_root),
            expected_ref_path=(
                str(prior_ref.get("path"))
                if prior_ref is not None and isinstance(prior_ref.get("path"), str)
                else None
            ),
            expected_ref_sha256=(
                str(prior_ref.get("sha256"))
                if prior_ref is not None and isinstance(prior_ref.get("sha256"), str)
                else None
            ),
            expected_config_digest=self._execution_record.get("config_digest"),
        )
        if reason:
            return _Adoption(reason=reason, integrity_failed=True)

        result = CalculationResult.from_step_result_dict(payload, roots=(science_root, task_root))
        if kind is StepKind.CASSCF:
            # Shared science gate (plan todo 11): the same validator the
            # scientific-record publish-retry entry calls.  Receipt digests
            # were verified above, so an absent/stale convergence fact may
            # be re-judged from the digest-verified original log.
            log_path = next(
                (Path(artifact.path) for artifact in result.artifacts if artifact.type == "log"),
                None,
            )
            verdict = validate_casscf_completion(
                result.metadata,
                artifact_paths=[Path(artifact.path) for artifact in result.artifacts],
                log_path=log_path,
                integrity_valid=True,
                allow_log_rejudge=True,
            )
            if not verdict.passed:
                return _Adoption(reason=verdict.reason, integrity_failed=True)
        raw_attempt = payload.get("attempt")
        attempt = (
            raw_attempt
            if isinstance(raw_attempt, int) and not isinstance(raw_attempt, bool)
            else None
        )
        return _Adoption(
            result=result,
            attempt=attempt,
            result_ref={"path": rel, "sha256": file_sha256(located) or ""},
        )

    def _ensure_publication(
        self,
        *,
        step_work_dir: Path,
        result: CalculationResult,
        result_id: str,
        kind: StepKind,
    ) -> str:
        """Contract C steps ③④ — publish unless already complete; ``""`` on ok.

        Idempotent via ``publish_result`` (stable ``result_id``): a resume
        that adopted a result only re-runs the publication sequence when the
        marker or the per-step manifest is missing.
        """
        existing_record = load_scientific_result(step_work_dir)
        existing_state = load_publication_state(step_work_dir)
        if (
            existing_state is not None
            and existing_state.complete
            and existing_state.result_id == result_id
            and existing_record is not None
            and existing_record.result_id == result_id
            and (step_work_dir / MANIFEST_FILENAME).is_file()
        ):
            return ""
        try:
            record = _step_scientific_record(
                result, result_id=result_id, kind=kind, result_dir=step_work_dir
            )
            publish_result(
                step_work_dir, record=record, manifest=_step_publication_manifest(record)
            )
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("step %s publication failed: %s", result_id, exc, exc_info=True)
            return f"publication failed: {exc}"
        return ""

    @staticmethod
    def _write_result_manifest_tolerant(
        result_dir: Path,
        plan: CalculationPlan,
        step_states: list[StepState],
        status: str,
    ) -> None:
        """Publish ``RESULT/result_manifest.json``; a write failure is pending.

        A manifest failure must never become a science failure — the next
        resume rebuilds and re-publishes it from the adopted results.
        """
        try:
            CalculationPlanExecutor._write_result_manifest(result_dir, plan, step_states, status)
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(
                "result_manifest_write_pending: publication retried on next resume (%s)",
                exc,
            )

    @staticmethod
    def _build_execution_record(
        checkpoint: Checkpoint | None, jobs_attempt: int | None
    ) -> dict[str, JsonValue]:
        """Attempt metadata bound to the checkpoint — never part of the science hash.

        ``attempt`` mirrors ``jobs.attempt`` (1 for CLI runs without a
        scheduler job); the checkpoint's own resume counter lives in
        ``Checkpoint.resume_count`` — no second attempt counter.
        """
        from cccp.version import __version__ as platform_version

        return {
            "code_release": str(platform_version),
            "config_digest": current_config_digest(),
            "software_version": None,
            "attempt": jobs_attempt if jobs_attempt is not None else 1,
        }

    def _run_post_stability_node(
        self,
        *,
        plan: CalculationPlan,
        steps: list[CalculationStep],
        item: StructureArtifact,
        work_dir: Path,
        handoff_coords: list[list[float]] | None,
        handoff_symbols: list[str] | None,
        base_resources: dict[str, JsonValue],
        identity: object,
        fingerprint: str,
        task_root: Path,
        science_root: Path,
        adoption_enabled: bool,
    ) -> StepState | None:
        """Append the §9.5 stability SP on the final geometry, or ``None``.

        Only fires when the plan contains OPT/FREQ steps (a pure SP plan
        already carries ``STABPerform`` in its own input) and every such
        step completed.  Metis m4: the appended node carries the STABLE
        ``step_{index}_{kind}`` id and adopts its durable ``step_result``
        on resume, so it is never re-run or re-published twice.
        """
        geometry_kinds = {StepKind.OPTIMIZE, StepKind.FREQUENCY}
        if not any(step.kind in geometry_kinds for step in steps):
            return None

        state_resources = self._find_electronic_state_resources(steps)
        if state_resources is None:
            return None
        raw_state = state_resources.get("electronic_state")
        if not isinstance(raw_state, dict):
            return None
        try:
            config = electronic_state_config_from_dict(raw_state)
        except ValueError:
            logger.warning("post-stability node: invalid electronic_state payload", exc_info=True)
            return None
        state = config.selected_state()
        if state is None or state.diagnostics.stability is not StabilityMode.FINAL_GEOMETRY:
            return None

        stability_dir = work_dir / "05_SP" / "stability"
        stability_dir.mkdir(parents=True, exist_ok=True)
        stability_index = len(steps)
        stability_identity = _stability_step_identity(identity, stability_index)
        rel = f"WORK/05_SP/stability/{STEP_RESULT_FILENAME}"

        adoption = self._adopt_step_result(
            idx=stability_index,
            step_identity=stability_identity,
            science_root=science_root,
            task_root=task_root,
            rel=rel,
            enabled=adoption_enabled,
            was_completed=(
                self._loaded_completed_facts.get(stability_index, {}).get("status") == "completed"
            ),
            kind=StepKind.SINGLEPOINT,
        )
        if adoption.result is not None:
            adopted = StepState(
                index=stability_index,
                kind=StepKind.SINGLEPOINT,
                status="completed",
                result=adoption.result,
                executed_this_run=False,
                reused_from_attempt=adoption.attempt,
                result_ref=adoption.result_ref,
            )
            logger.info(
                "recovery.step_adopted: stability node reused from attempt %s",
                adoption.attempt,
            )
            return adopted
        if adoption.reason:
            logger.warning(
                "recovery.step_not_adopted: stability node: %s — recomputing",
                adoption.reason,
            )
            self._loaded_completed_facts.pop(stability_index, None)

        resources: dict[str, JsonValue] = {**base_resources, **state_resources}
        resources["stability_check"] = True
        resources.pop("freq_log_path", None)
        request = _build_request(
            StepKind.SINGLEPOINT,
            item,
            _extract_method_from_resources(resources, plan.profile or "r2SCAN-3c"),
            resources,
            output_dir=stability_dir,
            coordinates=handoff_coords,
            symbols=handoff_symbols,
        )

        state_record = StepState(index=stability_index, kind=StepKind.SINGLEPOINT, status="pending")
        logger.info("post-stability: running SCF stability diagnostic on the final geometry")
        state_record.last_executed_attempt = self._jobs_attempt
        try:
            result = run_singlepoint(request)
        except Exception as exc:
            state_record.status = "failed"
            state_record.error = str(exc) or type(exc).__name__
            return state_record
        state_record.result = result
        if result.status == "failed":
            state_record.status = "failed"
            state_record.error = "; ".join(result.errors) or "stability diagnostic failed"
        else:
            state_record.status = "completed"
            self._write_step_result(
                state=state_record,
                result=result,
                task_root=task_root,
                step_work_dir=stability_dir,
                idx=stability_index,
                kind=StepKind.SINGLEPOINT,
                step_identity=stability_identity,
                symbols=handoff_symbols if handoff_symbols else list(item.elements),
                request=request,
            )
            logger.debug("post-stability: step_result persisted at %s", rel)
        return state_record

    @staticmethod
    def _find_electronic_state_resources(
        steps: list[CalculationStep],
    ) -> dict[str, JsonValue] | None:
        for step in steps:
            resources = _step_resources(step)
            if isinstance(resources.get("electronic_state"), dict):
                return resources
        return None

    def _persist_checkpoint(
        self,
        runtime_dir: Path,
        fingerprint: str,
        plan: CalculationPlan,
        step_states: list[StepState],
        handoff: _Handoff,
    ) -> None:
        """Persist checkpoint including the full downstream handoff (V03)."""
        items_state: dict[str, JsonValue] = {}
        handoff_value = handoff.to_checkpoint_value()
        if handoff_value is not None:
            items_state[_HANDOFF_KEY] = handoff_value
        items_state["paths"] = {
            str(index): str(_ensure_artifact(raw_item).path)
            for index, raw_item in enumerate(plan.items)
        }
        if self._execution_record:
            items_state["execution_record"] = dict(self._execution_record)
        step_dicts = _merge_completed_facts(
            [s.to_dict() for s in step_states], self._loaded_completed_facts
        )
        cp = Checkpoint(
            task_id="executor",
            workflow=plan.workflow,
            plan_fingerprint=fingerprint,
            step_states=step_dicts,
            items_state=items_state,
            resume_count=self._resume_count,
            identity_schema=IDENTITY_SCHEMA,
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

        # Track the OPTIMIZE step's product id and geometry ref for binding.
        optimize_product_id: str | None = None
        optimize_geometry_ref: str | None = None

        for state in step_states:
            if state.status == "blocked":
                product_id = f"step_{state.index}_{state.kind.value}"
                manifest.add_product(
                    id=f"{product_id}_blocked",
                    label=f"{state.kind.value} (step {state.index}) — blocked",
                    path="",
                    kind=ProductKind.FILE,
                    metadata={
                        "status": "blocked",
                        "stage_status": "blocked",
                        "blocked_reason": state.blocked_reason,
                        "reusable": False,
                        "policy_version": 1,
                    },
                )
                continue
            if state.result is None:
                continue
            diagnostic = state.result.metadata.get("diagnostic_only") is True

            product_id = f"step_{state.index}_{state.kind.value}"
            label = f"{state.kind.value} (step {state.index})"

            if (
                state.kind is StepKind.OPTIMIZE
                and state.result.metadata.get("optimization_status") == "converged"
            ):
                from acp.results.frame_candidate_store import atomic_write_text
                from acp.results.structure_policy import single_geometry

                source_item = plan.items[0] if plan.items else None
                symbols = (
                    source_item.elements
                    if isinstance(source_item, StructureArtifact)
                    else list(
                        (source_item or {}).get("elements")
                        or (source_item or {}).get("symbols")
                        or []
                    )
                )
                coords = state.result.coords
                if coords is not None and len(symbols) == len(coords):
                    text = (
                        str(len(symbols))
                        + "\n"
                        + label
                        + "\n"
                        + "\n".join(
                            f"{symbol} {float(row[0]):.10f} {float(row[1]):.10f} "
                            f"{float(row[2]):.10f}"
                            for symbol, row in zip(symbols, coords)
                        )
                        + "\n"
                    )
                    geometry = single_geometry(text)
                    if geometry:
                        rel = f"structures/{product_id}.xyz"
                        atomic_write_text(result_dir / rel, text)
                        downstream = [s for s in step_states if s.index > state.index]
                        freq = next(
                            (s.status for s in downstream if s.kind is StepKind.FREQUENCY),
                            "pending",
                        )
                        manifest.add_product(
                            product_id,
                            label,
                            rel,
                            ProductKind.STRUCTURE,
                            metadata=_diagnostic_metadata(
                                {
                                    **geometry,
                                    "source_kind": "optimization",
                                    "optimization_status": "converged",
                                    "frequency_status": freq,
                                    "stage_id": product_id,
                                    "downstream_status": [
                                        {"kind": s.kind.value, "status": s.status}
                                        for s in downstream
                                    ],
                                    "auto_reusable": True,
                                    "policy_version": 1,
                                },
                                diagnostic,
                            ),
                        )
                        optimize_product_id = product_id
                        optimize_geometry_ref = "RESULT/" + rel

            # FREQUENCY steps: register normal_modes product with geometry binding.
            if state.kind is StepKind.FREQUENCY:
                normal_modes_ref = None
                other_artifacts = []
                for artifact in state.result.artifacts:
                    if artifact.type == "normal_modes":
                        normal_modes_ref = artifact
                    else:
                        other_artifacts.append(artifact)

                if normal_modes_ref is not None:
                    freq_dir = result_dir / "frequencies"
                    freq_dir.mkdir(parents=True, exist_ok=True)
                    dest = freq_dir / "normal_modes.json"
                    try:
                        import shutil

                        shutil.copy2(normal_modes_ref.path, dest)
                        geo_fingerprint = ""
                        if optimize_geometry_ref is not None:
                            geo_path = result_dir.parent / optimize_geometry_ref
                            if geo_path.is_file():
                                geo_fingerprint = hashlib.sha256(geo_path.read_bytes()).hexdigest()[
                                    :16
                                ]
                        manifest.add_product(
                            id=f"{product_id}_normal_modes",
                            label=f"{label} — normal modes",
                            path="frequencies/normal_modes.json",
                            kind=ProductKind.FREQUENCY_MODES,
                            metadata=_diagnostic_metadata(
                                {
                                    "geometry_product_id": optimize_product_id,
                                    "geometry_ref": optimize_geometry_ref,
                                    "geometry_fingerprint": geo_fingerprint,
                                },
                                diagnostic,
                            ),
                        )
                    except OSError:
                        logger.debug(
                            "executor: could not copy normal_modes.json to RESULT; skipping"
                        )

                # Remaining frequency artifacts (logs, etc.) as FILE products.
                for artifact in other_artifacts:
                    try:
                        rel_path = str(artifact.path.relative_to(result_dir.parent))
                    except ValueError:
                        rel_path = str(artifact.path)
                    manifest.add_product(
                        id=f"{product_id}_{artifact.type}",
                        label=f"{label} — {artifact.type}",
                        path=rel_path,
                        kind=ProductKind.FILE,
                        metadata=_diagnostic_metadata({}, diagnostic),
                    )
            else:
                # Only converged, valid single-geometry artifacts are reusable.
                from acp.results.structure_policy import single_geometry

                # Non-frequency steps: register artifacts with step-mapped kind.
                for artifact in state.result.artifacts:
                    try:
                        rel_path = str(artifact.path.relative_to(result_dir.parent))
                    except ValueError:
                        rel_path = str(artifact.path)
                    manifest.add_product(
                        id=f"{product_id}_{artifact.type}",
                        label=f"{label} — {artifact.type}",
                        path=rel_path,
                        kind=ProductKind.FILE,
                        metadata=_diagnostic_metadata(
                            {
                                "stage_status": state.status,
                                "stage_id": product_id,
                                "optimization_status": state.result.metadata.get(
                                    "optimization_status", "unknown"
                                ),
                                "policy_version": 1,
                            },
                            diagnostic,
                        ),
                    )

            # Publish energy as a real RESULT/energy file (plan D6/T11).
            if state.result.energy is not None:
                from acp.results.frame_candidate_store import atomic_write_text

                energy_hartree = float(state.result.energy)
                method = plan.profile or ""
                if state.index < len(plan.steps):
                    method = _extract_method_from_resources(
                        _step_resources(_normalise_step(plan.steps[state.index])), method
                    )
                rel = f"energy/{product_id}.json"
                atomic_write_text(
                    result_dir / rel,
                    json.dumps(
                        {
                            "schema_version": "acp_energy_product_v1",
                            "step_id": product_id,
                            "step_kind": state.kind.value,
                            "energy": energy_hartree,
                            "energy_hartree": energy_hartree,
                            "unit": "hartree",
                            "method": method,
                            "source": "scientific_result.json",
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                )
                manifest.add_product(
                    id=f"{product_id}_energy",
                    label=f"{label} — energy",
                    path=rel,
                    kind=ProductKind.ENERGY_REPORT,
                    metadata=_diagnostic_metadata({"energy_hartree": energy_hartree}, diagnostic),
                )

        register_result_manifest(result_dir, manifest)


__all__ = [
    "CalculationPlanExecutor",
    "ExecutionResult",
    "StepState",
]
