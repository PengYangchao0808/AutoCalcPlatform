"""TS Mode workflow engine (plan §3/§9).

Stage order: ``prepare_source`` → ``resolve_target`` → ``optimize_ts`` →
``frequency_final`` → ``validate_ts`` → ``publish_results``.  Composes the
existing TS-optimize and frequency primitives — it never re-implements the
subprocess layer and never recurses into another workflow.

Layout under the task root (plan §11)::

    INPUT/tsmode/           source.xyz / source.hess / source_modes.json /
                            source_bundle.json
    WORK/tsmode/            mapping/ optimize/attempt_001/ frequency/
                            tsmode_checkpoint.json
    RESULT/tsmode/          target_resolution.json tsmode_report.json
                            optimized.xyz normal_modes.json
    RESULT/result_manifest.json

Resume (plan §10.2): the checkpoint fingerprint covers the source
geometry/Hessian/vector identity, target id, level of theory, and key
optimization parameters.  When only the final frequency is missing, the
engine resumes from the frequency stage; any target/system change
invalidates everything.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from acp.calculations.contracts import (
    CalculationRequest,
    CalculationResult,
    StructureArtifact,
    StructureRole,
)
from acp.calculations.identity import config_digest
from acp.calculations.primitives.frequency import run_frequency
from acp.calculations.primitives.optimize import run_optimize
from acp.calculations.step_result import locate_recorded_file, portable_path
from acp.calculations.tsmode.contracts import (
    FrequencyCredential,
    FrequencySourceBundle,
    OptimizeCredential,
    PublicationState,
    SourceLevelOfTheory,
    TargetResolution,
    TsmodeError,
    TsmodeOptimizationSettings,
    TsmodeReport,
    TsmodeRequest,
    optimized_structure_digest,
    sha256_file,
    validate_coordinate_array,
    validate_mode_completeness,
)
from acp.calculations.tsmode.mode_mapping import enforce_launch_gate, resolve_target_mode
from acp.calculations.tsmode.source import (
    compute_hessian_modes,
    snapshot_bundle_files,
    verify_snapshot_hashes,
)
from acp.calculations.tsmode.validation import (
    compare_mode_correspondence,
    validate_ts_frequencies,
)
from acp.core.workflow import WorkflowResult
from cccp.config import load_config
from cccp.qc.interfaces.hess_file import parse_orca_hess_file
from cccp.qc.interfaces.orca_ts import (
    parse_ts_frequency_map,
    parse_ts_mode_vectors,
)
from cccp.qc.interfaces.route_render import orca_keyword_context
from cccp.qc.method_meta import method_meta

logger = logging.getLogger(__name__)

__all__ = ["TsmodeEngine", "TsmodeEngineResult", "compute_engine_fingerprint"]

_CHECKPOINT_NAME = "tsmode_checkpoint.json"
_CHECKPOINT_SCHEMA_V2 = "tsmode_checkpoint_v2"
_EXECUTION_STATUSES = ("pending", "running", "completed", "failed")
#: Artifact types that may carry the final frequency table, best first.
#: ORCA's ``QCResult.output_file`` (type ``"output"``) names the ``.inp``
#: route file — it never contains the table — so type priority alone is
#: not enough when artifacts arrive in interface field order.
_FREQUENCY_LOG_ARTIFACT_TYPES: tuple[str, ...] = ("frequency_log", "log", "output")


@dataclass(frozen=True, slots=True)
class TsmodeEngineResult:
    """Terminal outcome of one engine run."""

    workflow_result: WorkflowResult
    report: TsmodeReport
    resolution: TargetResolution
    output_root: Path


@dataclass(frozen=True, slots=True)
class _FrequencyScience:
    """Canonical frequency data extracted from one stage result or checkpoint."""

    frequency_map: dict[int, float] = field(default_factory=dict)
    frequency_vectors: dict[int, NDArray[np.float64]] = field(default_factory=dict)
    expected_mode_indices: list[int] = field(default_factory=list)
    frequencies: list[float] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def compute_engine_fingerprint(
    bundle: FrequencySourceBundle,
    resolution: TargetResolution,
    settings: TsmodeOptimizationSettings,
) -> str:
    """Checkpoint fingerprint (plan §10.2)."""
    payload = {
        "hessian_sha256": bundle.hessian_sha256,
        "geometry_hash": bundle.geo_hash,
        "target_mode_id": resolution.target_mode_id,
        "source_mode_index": resolution.source_mode_index,
        "level": bundle.level.to_dict(),
        "charge": bundle.charge,
        "multiplicity": bundle.multiplicity,
        "settings": settings.to_dict(),
        "initial_hessian": "read_source",
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "fp_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


_LEVEL_RESOURCE_FIELDS: tuple[str, ...] = (
    "basis",
    "dispersion",
    "solvent",
    "solvent_model",
    "grid",
    "scf",
)
_ROUTE_NOOP_VALUES: frozenset[str] = frozenset({"none", "normal"})
_GFN_FAMILIES: frozenset[str] = frozenset({"gfn", "gfnff"})


def _level_route_carriers(level: SourceLevelOfTheory) -> list[str]:
    """Tokens for inherited level fields the ORCA frequency capability drops.

    ``ORCAInterface.frequency`` consumes basis/solvent/solvent_model/
    scf_convergence but not dispersion/grid (unlike single_point and OptTS);
    the explicit value is passed verbatim through the sanctioned
    ``route_extras`` channel so the rendered Frequency input carries the same
    inherited level as the OptTS input.  Method-inherent values are skipped.
    Follow-up: forward dispersion/grid through the cccp frequency capability
    and drop this bridge.
    """
    meta = method_meta(level.method) or {}
    family, _implementation = orca_keyword_context(level.method)
    carriers: list[str] = []
    dispersion = (level.dispersion or "").strip()
    if (
        dispersion
        and dispersion.lower() not in _ROUTE_NOOP_VALUES
        and not meta.get("builtin_dispersion")
    ):
        carriers.append(dispersion)
    grid = (level.grid or "").strip()
    if grid and grid.lower() not in _ROUTE_NOOP_VALUES and family not in _GFN_FAMILIES:
        carriers.append(grid)
    return carriers


def _project_source_level(level: SourceLevelOfTheory) -> tuple[str, dict[str, Any]]:
    """Project the frequency-source level onto one legacy request shape.

    Returns ``(method, resources)``.  ``method`` rides on the top-level
    ``CalculationRequest.method``; every present level field is emitted under
    its legacy resource key so ``to_task_request`` can lift it into the typed
    ``MethodSpec`` (``grid`` into ``integration_grid``, ``scf`` into ``scf``).
    Absent fields are never emitted (no fabricated default); unknown empty
    values stay absent and are reported as unconfirmed.
    """
    resources: dict[str, Any] = {}
    for level_field in _LEVEL_RESOURCE_FIELDS:
        value = getattr(level, level_field)
        if value:
            resources[level_field] = value
    carriers = _level_route_carriers(level)
    if carriers:
        resources["route_extras"] = carriers
    return level.method, resources


def _collect_stage_input_evidence(
    root: Path,
    snapshot: dict[str, Path],
    stage_dirs: list[Path],
) -> list[dict[str, Any]]:
    """Attach staged source artifacts and rendered backend inputs with digests."""
    evidence: list[dict[str, Any]] = []
    for snapshot_path in snapshot.values():
        path = Path(snapshot_path)
        if path.is_file():
            evidence.append(
                {
                    "kind": "staged_input",
                    "path": str(path.relative_to(root)),
                    "sha256": sha256_file(path),
                }
            )
    for directory in stage_dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.inp")):
            evidence.append(
                {
                    "kind": "backend_input",
                    "path": str(path.relative_to(root)),
                    "sha256": sha256_file(path),
                }
            )
    return evidence


def _unconfirmed_level_fields(level: SourceLevelOfTheory) -> list[str]:
    """Level fields with no recorded value; never silently resolved."""
    return [field for field in ("method", "basis") if not getattr(level, field)]


class TsmodeEngine:
    """Orchestrates the directed TS Mode optimization task."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config

    # ── public entry ──────────────────────────────────────────────────────

    def run(
        self,
        request: TsmodeRequest,
        bundle: FrequencySourceBundle,
        output_root: str | Path,
        *,
        progress_reporter: Any = None,
    ) -> TsmodeEngineResult:
        """Execute all stages for one validated request + bundle."""
        root = Path(output_root)
        root.mkdir(parents=True, exist_ok=True)
        input_dir = root / "INPUT" / "tsmode"
        work_dir = root / "WORK" / "tsmode"
        result_dir = root / "RESULT" / "tsmode"
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "mapping").mkdir(exist_ok=True)

        if progress_reporter is not None:
            progress_reporter.initialize()

        warnings: list[str] = list(bundle.warnings)
        settings = request.optimization
        unconfirmed_level = _unconfirmed_level_fields(bundle.level)
        if unconfirmed_level:
            warnings.append("level of theory unconfirmed for: " + ", ".join(unconfirmed_level))

        # 1. prepare_source — immutable snapshot owned by this task.
        snapshot = snapshot_bundle_files(bundle, input_dir)
        verify_snapshot_hashes(input_dir, bundle)

        # 2. resolve_target — mapping + launch gate.
        resolution = resolve_target_mode(
            bundle,
            request.source_mode_index,
            hessian=parse_orca_hess_file(snapshot["hessian"]).hessian,
        )
        (work_dir / "mapping" / "target_resolution.json").write_text(
            json.dumps(resolution.to_dict(), indent=2), encoding="utf-8"
        )
        enforce_launch_gate(
            resolution,
            require_verified=settings.require_verified_mapping,
            orca_version=bundle.level.orca_version,
        )
        assert resolution.optimizer_mode_index is not None  # gate guarantees

        fingerprint = compute_engine_fingerprint(bundle, resolution, settings)
        checkpoint = self._load_checkpoint(work_dir)
        if (
            checkpoint is None
            or checkpoint.get("schema_version") != _CHECKPOINT_SCHEMA_V2
            or checkpoint.get("fingerprint") != fingerprint
        ):
            checkpoint = None

        completed_stages: list[str] = ["prepare_source", "resolve_target"]
        attempts: list[dict[str, Any]] = []
        optimized_coords: NDArray[np.float64] | None = None
        optimization_status = "pending"
        optimize_credential: OptimizeCredential | None = None

        # 3. optimize_ts — directed OptTS reading the source Hessian.
        resume_optimize, optimize_reason = self._adopt_optimize_credential(
            checkpoint, bundle, resolution, settings, root
        )
        if resume_optimize and checkpoint is not None:
            optimize_credential = OptimizeCredential.from_dict(checkpoint["optimize_credential"])
            assert optimize_credential is not None  # adoption validated the payload
            optimized_coords = np.asarray(optimize_credential.coordinates, dtype=np.float64)
            optimization_status = "completed"
            attempts.append({"stage": "optimize_ts", "resumed": True})
            completed_stages.append("optimize_ts")
            warnings.append("resumed from checkpoint: optimization stage skipped")
        else:
            if checkpoint is not None and checkpoint.get("optimize_credential") is not None:
                warnings.append(
                    f"optimize credential invalid ({optimize_reason}); recomputing optimize"
                )
            opt_result = self._run_optimize_stage(
                bundle,
                resolution,
                settings,
                snapshot["hessian"],
                work_dir / "optimize" / "attempt_001",
            )
            attempts.append(
                {
                    "stage": "optimize_ts",
                    "status": opt_result.status,
                    "errors": list(opt_result.errors),
                    "directory": "WORK/tsmode/optimize/attempt_001",
                }
            )
            if opt_result.coords is not None and opt_result.status == "completed":
                candidate_coords = np.asarray(opt_result.coords, dtype=np.float64)
                coordinate_problem = validate_coordinate_array(candidate_coords, bundle.n_atoms)
                if coordinate_problem:
                    optimization_status = "failed"
                    warnings.append(
                        "optimization produced invalid coordinates: " + coordinate_problem
                    )
                else:
                    optimized_coords = candidate_coords
                    optimization_status = "completed"
                    completed_stages.append("optimize_ts")
                    optimize_credential = self._build_optimize_credential(
                        bundle, resolution, settings, optimized_coords, opt_result, root
                    )
                    self._write_checkpoint(
                        work_dir,
                        fingerprint,
                        optimize_credential=optimize_credential.to_dict(),
                        invalidate_frequency=True,
                    )
            else:
                optimization_status = "failed"

        if optimized_coords is None:
            report = self._report(
                request,
                bundle,
                resolution,
                attempts,
                execution_status="failed",
                optimization_status="failed",
                frequency_status="skipped",
                frequencies=None,
                warnings=warnings + ["optimization failed; last valid structure retained in WORK"],
                artifacts=_collect_stage_input_evidence(root, snapshot, [work_dir / "optimize"]),
            )
            self._publish(root, result_dir, report, optimized_xyz=None, normal_modes=None)
            return TsmodeEngineResult(
                workflow_result=self._workflow_result(
                    "failed", attempts, error="optimize_ts failed", stages=completed_stages
                ),
                report=report,
                resolution=resolution,
                output_root=root,
            )

        geometry_lines = [str(bundle.n_atoms), f"TS candidate {resolution.target_mode_id}"]
        for symbol, row in zip(bundle.elements, optimized_coords):
            geometry_lines.append(f"{symbol:2s} {row[0]:15.10f} {row[1]:15.10f} {row[2]:15.10f}")

        pending_report = self._report(
            request,
            bundle,
            resolution,
            attempts,
            execution_status="running",
            optimization_status="completed",
            frequency_status="pending",
            frequencies=None,
            warnings=warnings,
            artifacts=_collect_stage_input_evidence(root, snapshot, [work_dir / "optimize"]),
        )
        self._publish(
            root,
            result_dir,
            pending_report,
            optimized_xyz="\n".join(geometry_lines) + "\n",
            normal_modes=None,
        )

        # 4. frequency_final — same level, on the final structure.
        frequency_status = "pending"
        frequencies: list[float] = []
        frequency_map: dict[int, float] = {}
        frequency_vectors: dict[int, NDArray[np.float64]] = {}
        mode_data_incomplete = False
        if not request.final_frequency:
            frequency_status = "skipped"
            warnings.append("final frequency validation disabled by request")
        else:
            if resume_optimize:
                resume_frequency, frequency_reason, frequency_credential = (
                    self._adopt_frequency_credential(checkpoint, optimize_credential, bundle, root)
                )
            else:
                resume_frequency = False
                frequency_reason = "optimize_recomputed"
                frequency_credential = None
            if resume_frequency and frequency_credential is not None:
                frequency_map = {
                    index: float(value)
                    for index, value in frequency_credential.mode_frequencies.items()
                }
                frequency_vectors = {
                    index: np.asarray(rows, dtype=np.float64)
                    for index, rows in frequency_credential.mode_vectors.items()
                }
                frequencies = [float(value) for value in frequency_credential.frequencies]
                frequency_status = "completed"
                completed_stages.append("frequency_final")
                attempts.append({"stage": "frequency_final", "resumed": True})
            else:
                if checkpoint is not None and checkpoint.get("frequency_credential") is not None:
                    warnings.append(
                        f"frequency credential invalid ({frequency_reason}); recomputing frequency"
                    )
                freq_result = self._run_frequency_stage(
                    bundle,
                    optimized_coords,
                    work_dir / "frequency",
                )
                attempts.append(
                    {
                        "stage": "frequency_final",
                        "status": freq_result.status,
                        "errors": list(freq_result.errors),
                        "directory": "WORK/tsmode/frequency",
                    }
                )
                science = self._evaluate_frequency_science(freq_result, bundle.n_atoms, root)
                frequency_map = science.frequency_map
                frequency_vectors = science.frequency_vectors
                frequencies = (
                    [float(value) for value in freq_result.frequencies]
                    if freq_result.frequencies
                    else science.frequencies
                )
                mode_data_incomplete = bool(science.problems)
                if freq_result.status == "completed" and frequencies and not science.problems:
                    frequency_status = "completed"
                    completed_stages.append("frequency_final")
                    if optimize_credential is not None:
                        credential = self._build_frequency_credential(
                            bundle, optimize_credential, science
                        )
                        self._write_checkpoint(
                            work_dir,
                            fingerprint,
                            frequency_credential=credential.to_dict(),
                        )
                else:
                    frequency_status = "failed"
                    if science.problems:
                        warnings.append(
                            "final mode data incomplete: " + "; ".join(science.problems)
                        )

        # 5. validate_ts — chemical validation separate from execution.
        validation = validate_ts_frequencies(frequencies)
        mode_correspondence = self._mode_correspondence_evidence(
            bundle, resolution, frequency_map, frequency_vectors
        )
        validation = type(validation)(
            classification=validation.classification,
            significant_imaginary_cm1=validation.significant_imaginary_cm1,
            all_imaginary_cm1=validation.all_imaginary_cm1,
            threshold_cm1=validation.threshold_cm1,
            threshold_source=validation.threshold_source,
            mode_correspondence=mode_correspondence,
            notes=validation.notes,
        )

        execution_status = (
            "completed"
            if optimization_status == "completed" and frequency_status in ("completed", "skipped")
            else "failed"
            if optimization_status == "failed"
            else "completed_with_warnings"
        )

        # 6. publish_results.
        report = self._report(
            request,
            bundle,
            resolution,
            attempts,
            execution_status=execution_status,
            optimization_status=optimization_status,
            frequency_status=frequency_status,
            frequencies=frequencies,
            warnings=warnings,
            validation=validation.to_dict(),
            artifacts=_collect_stage_input_evidence(
                root, snapshot, [work_dir / "optimize", work_dir / "frequency"]
            ),
        )
        normal_modes = (
            None
            if mode_data_incomplete
            else self._build_normal_modes(bundle, frequency_map, frequency_vectors, frequencies)
        )
        self._publish(
            root,
            result_dir,
            report,
            optimized_xyz="\n".join(geometry_lines) + "\n",
            normal_modes=normal_modes,
        )
        self._record_publication(work_dir, fingerprint, result_dir)
        status = "completed" if execution_status == "completed" else "failed"
        return TsmodeEngineResult(
            workflow_result=self._workflow_result(status, attempts, stages=completed_stages),
            report=report,
            resolution=resolution,
            output_root=root,
        )

    # ── stages ────────────────────────────────────────────────────────────

    def _effective_config(self) -> dict[str, Any]:
        return load_config(overrides=self._config) if self._config else load_config()

    def _effective_config_digest(self) -> str | None:
        return config_digest(self._effective_config())

    def _build_optimize_credential(
        self,
        bundle: FrequencySourceBundle,
        resolution: TargetResolution,
        settings: TsmodeOptimizationSettings,
        coordinates: NDArray[np.float64],
        result: CalculationResult,
        root: Path,
    ) -> OptimizeCredential:
        rows = [[float(value) for value in row] for row in coordinates]
        return OptimizeCredential(
            source_content_sha256=bundle.source_revision(),
            target_mode_id=resolution.target_mode_id,
            optimizer_mode_index=resolution.optimizer_mode_index,
            effective_level=bundle.level.to_dict(),
            optimization_parameters=settings.to_dict(),
            effective_config_digest=self._effective_config_digest(),
            optimized_structure_sha256=optimized_structure_digest(list(bundle.elements), rows),
            coordinates=rows,
            elements=list(bundle.elements),
            required_completion_artifacts=self._collect_credential_artifacts(result, root),
            energy_hartree=result.energy,
        )

    def _build_frequency_credential(
        self,
        bundle: FrequencySourceBundle,
        optimize_credential: OptimizeCredential,
        science: _FrequencyScience,
    ) -> FrequencyCredential:
        modes = [
            {
                "mode_index": int(index),
                "frequency_cm1": float(science.frequency_map[index]),
                "vectors": [
                    [float(value) for value in row]
                    for row in science.frequency_vectors.get(index, [])
                ],
            }
            for index in sorted(science.frequency_map)
        ]
        return FrequencyCredential(
            adopted_optimized_structure_sha256=(optimize_credential.optimized_structure_sha256),
            effective_level=bundle.level.to_dict(),
            expected_mode_indices=list(science.expected_mode_indices),
            modes=modes,
            frequencies=[float(value) for value in science.frequencies],
            artifacts=[dict(entry) for entry in science.artifacts],
        )

    @staticmethod
    def _collect_credential_artifacts(
        result: CalculationResult, root: Path
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for artifact in result.artifacts:
            path = Path(artifact.path)
            if not path.is_file():
                continue
            entries.append(
                {
                    "type": artifact.type,
                    "path": portable_path(path, root),
                    "sha256": sha256_file(path),
                }
            )
        return entries

    @staticmethod
    def _verify_credential_artifacts(entries: list[dict[str, Any]], root: Path) -> str:
        for entry in entries:
            recorded = entry.get("path")
            if not isinstance(recorded, str) or not recorded:
                return "artifact_path_missing"
            digest = entry.get("sha256")
            if not isinstance(digest, str) or not digest:
                continue
            located = locate_recorded_file([root], recorded)
            if located is None:
                return f"artifact_missing:{recorded}"
            if sha256_file(located) != digest:
                return f"artifact_digest_mismatch:{recorded}"
        return ""

    def _adopt_optimize_credential(
        self,
        checkpoint: dict[str, Any] | None,
        bundle: FrequencySourceBundle,
        resolution: TargetResolution,
        settings: TsmodeOptimizationSettings,
        root: Path,
    ) -> tuple[bool, str]:
        if checkpoint is None:
            return False, "no_checkpoint"
        credential = OptimizeCredential.from_dict(checkpoint.get("optimize_credential"))
        if credential is None:
            return False, "missing_or_malformed"
        if credential.source_content_sha256 != bundle.source_revision():
            return False, "source_content_changed"
        if credential.target_mode_id != resolution.target_mode_id:
            return False, "target_mode_changed"
        if credential.optimizer_mode_index != resolution.optimizer_mode_index:
            return False, "optimizer_mode_index_changed"
        if credential.effective_level != bundle.level.to_dict():
            return False, "effective_level_changed"
        if credential.optimization_parameters != settings.to_dict():
            return False, "optimization_parameters_changed"
        if credential.effective_config_digest != self._effective_config_digest():
            return False, "effective_config_changed"
        if credential.elements != list(bundle.elements):
            return False, "element_order_changed"
        coordinate_problem = validate_coordinate_array(credential.coordinates, bundle.n_atoms)
        if coordinate_problem:
            return False, coordinate_problem
        expected_digest = optimized_structure_digest(credential.elements, credential.coordinates)
        if credential.optimized_structure_sha256 != expected_digest:
            return False, "optimized_structure_digest_mismatch"
        artifact_problem = self._verify_credential_artifacts(
            credential.required_completion_artifacts, root
        )
        if artifact_problem:
            return False, artifact_problem
        return True, ""

    def _adopt_frequency_credential(
        self,
        checkpoint: dict[str, Any] | None,
        optimize_credential: OptimizeCredential | None,
        bundle: FrequencySourceBundle,
        root: Path,
    ) -> tuple[bool, str, FrequencyCredential | None]:
        if checkpoint is None or optimize_credential is None:
            return False, "no_validated_optimize", None
        credential = FrequencyCredential.from_dict(checkpoint.get("frequency_credential"))
        if credential is None:
            return False, "missing_or_malformed", None
        if (
            credential.adopted_optimized_structure_sha256
            != optimize_credential.optimized_structure_sha256
        ):
            return False, "adopted_structure_mismatch", None
        if credential.effective_level != bundle.level.to_dict():
            return False, "effective_level_changed", None
        artifact_problem = self._verify_credential_artifacts(credential.artifacts, root)
        if artifact_problem:
            return False, artifact_problem, None
        problems = validate_mode_completeness(
            credential.expected_mode_indices,
            credential.mode_frequencies,
            credential.mode_vectors,
            bundle.n_atoms,
        )
        if problems:
            return False, "mode_data_incomplete: " + "; ".join(problems), None
        return True, "", credential

    @staticmethod
    def _read_wrapper_normal_modes(
        result: CalculationResult,
    ) -> tuple[dict[int, float], dict[int, list[list[float]]]]:
        for artifact in result.artifacts:
            if artifact.type != "normal_modes":
                continue
            path = Path(artifact.path)
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            raw_modes = payload.get("modes")
            if not isinstance(raw_modes, list):
                continue
            frequency_map: dict[int, float] = {}
            vectors: dict[int, list[list[float]]] = {}
            for mode in raw_modes:
                if not isinstance(mode, dict):
                    continue
                index = mode.get("mode_index")
                if not isinstance(index, int) or isinstance(index, bool):
                    continue
                frequency = mode.get("frequency_cm1")
                if isinstance(frequency, (int, float)):
                    frequency_map[int(index)] = float(frequency)
                raw_vectors = mode.get("vectors")
                if isinstance(raw_vectors, list) and raw_vectors:
                    try:
                        vectors[int(index)] = [
                            [float(component) for component in row] for row in raw_vectors
                        ]
                    except (TypeError, ValueError):
                        continue
            if frequency_map:
                return frequency_map, vectors
        return {}, {}

    def _evaluate_frequency_science(
        self, result: CalculationResult, n_atoms: int, root: Path
    ) -> _FrequencyScience:
        artifacts = self._collect_credential_artifacts(result, root)
        log_map, log_vectors = self._parse_frequency_products(result)
        wrapper_map, wrapper_vectors = self._read_wrapper_normal_modes(result)
        expected = sorted(set(log_map) | set(wrapper_map))
        frequencies = [float(value) for value in result.frequencies]
        if not expected:
            return _FrequencyScience(frequencies=frequencies, artifacts=artifacts)
        for source_map, source_vectors in (
            (wrapper_map, wrapper_vectors),
            (log_map, log_vectors),
        ):
            if not source_map:
                continue
            problems = validate_mode_completeness(expected, source_map, source_vectors, n_atoms)
            if not problems:
                return _FrequencyScience(
                    frequency_map={index: float(value) for index, value in source_map.items()},
                    frequency_vectors={
                        index: np.asarray(rows, dtype=np.float64)
                        for index, rows in source_vectors.items()
                    },
                    expected_mode_indices=expected,
                    frequencies=frequencies,
                    artifacts=artifacts,
                )
        problems = validate_mode_completeness(expected, log_map, log_vectors, n_atoms)
        return _FrequencyScience(
            frequency_map={index: float(value) for index, value in log_map.items()},
            frequency_vectors={
                index: np.asarray(rows, dtype=np.float64) for index, rows in log_vectors.items()
            },
            expected_mode_indices=expected,
            frequencies=frequencies,
            artifacts=artifacts,
            problems=problems,
        )

    def _record_publication(self, work_dir: Path, fingerprint: str, result_dir: Path) -> None:
        normal_modes = result_dir / "normal_modes.json"
        digest = sha256_file(normal_modes) if normal_modes.is_file() else ""
        state = PublicationState(
            status="published" if digest else "pending",
            normal_modes_sha256=digest or None,
        )
        self._write_checkpoint(work_dir, fingerprint, publication=state.to_dict())

    def _run_optimize_stage(
        self,
        bundle: FrequencySourceBundle,
        resolution: TargetResolution,
        settings: TsmodeOptimizationSettings,
        hess_file: Path,
        target_dir: Path,
    ) -> CalculationResult:
        target_dir.mkdir(parents=True, exist_ok=True)
        method, level_resources = _project_source_level(bundle.level)

        resources: dict[str, Any] = {
            "backend": "orca",
            "config": self._effective_config(),
            "coordinates": bundle.coordinates_angstrom,
            "symbols": list(bundle.elements),
            "charge": bundle.charge,
            "multiplicity": bundle.multiplicity,
            "structure_kind": "ts",
            "output_dir": str(target_dir),
            "ts_mode": int(resolution.optimizer_mode_index or 0),
            "hess_file": str(hess_file),
            "initial_hessian": "read",
            "opt_rescue_policy": "adaptive",
            "opt_max_rescue": settings.retry_limit,
        }
        if settings.recalc_hess is not None:
            resources["recalc_hess"] = settings.recalc_hess
        if settings.trust_radius is not None:
            resources["trust_radius"] = settings.trust_radius
        if settings.max_iterations is not None:
            resources["geom_maxiter"] = settings.max_iterations
        if settings.convergence:
            resources["opt_level"] = settings.convergence
        resources.update(level_resources)

        artifact = StructureArtifact(
            path=Path(bundle.hessian_file),
            elements=list(bundle.elements),
            role=StructureRole.TRANSITION_STATE,
            source="tsmode",
        )
        request = CalculationRequest(
            input_artifact=artifact,
            method=method,
            resources=resources,
            workflow="tsmode",
            profile="default",
        )
        return run_optimize(request)

    def _run_frequency_stage(
        self,
        bundle: FrequencySourceBundle,
        coordinates: NDArray[np.float64],
        target_dir: Path,
    ) -> CalculationResult:
        target_dir.mkdir(parents=True, exist_ok=True)
        method, level_resources = _project_source_level(bundle.level)

        resources: dict[str, Any] = {
            "backend": "orca",
            "config": self._effective_config(),
            "coordinates": [[float(v) for v in row] for row in coordinates],
            "symbols": list(bundle.elements),
            "charge": bundle.charge,
            "multiplicity": bundle.multiplicity,
            "output_dir": str(target_dir),
        }
        resources.update(level_resources)
        artifact = StructureArtifact(
            path=Path(bundle.hessian_file),
            elements=list(bundle.elements),
            role=StructureRole.TRANSITION_STATE,
            source="tsmode",
        )
        request = CalculationRequest(
            input_artifact=artifact,
            method=method,
            resources=resources,
            workflow="tsmode",
            profile="default",
        )
        return run_frequency(request)

    # ── helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_frequency_products(
        result: CalculationResult,
    ) -> tuple[dict[int, float], dict[int, NDArray[np.float64]]]:
        """Parse the final-frequency log into (frequency map, mode vectors).

        Candidates are ordered by artifact-type priority
        (``frequency_log`` > ``log`` > ``output``), then by ``.out``
        suffix, then by artifact order; every existing candidate is parsed
        until one yields a non-empty frequency map.  The ``.out`` rule
        matters because ORCA's type ``"output"`` artifact is the ``.inp``
        route file (interface field order puts it before the real log).
        """
        candidates = [
            (artifact.type, Path(artifact.path))
            for artifact in result.artifacts
            if artifact.type in _FREQUENCY_LOG_ARTIFACT_TYPES
        ]
        candidates.sort(
            key=lambda item: (
                _FREQUENCY_LOG_ARTIFACT_TYPES.index(item[0]),
                0 if item[1].suffix.lower() == ".out" else 1,
            )
        )
        for _artifact_type, path in candidates:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            frequency_map = parse_ts_frequency_map(text)
            if len(frequency_map) > 0:
                return frequency_map, parse_ts_mode_vectors(text)
        return {}, {}

    def _mode_correspondence_evidence(
        self,
        bundle: FrequencySourceBundle,
        resolution: TargetResolution,
        frequency_map: dict[int, float],
        frequency_vectors: dict[int, NDArray[np.float64]],
    ) -> dict[str, Any]:
        if not frequency_map or not frequency_vectors:
            return {
                "verdict": "manual_review",
                "note": "final mode vectors unavailable — correspondence not assessed",
            }
        imaginary_indices = sorted(
            (index for index, frequency in frequency_map.items() if frequency < 0),
            key=lambda index: frequency_map[index],
        )
        if not imaginary_indices:
            return {"verdict": "manual_review", "note": "no imaginary mode in final analysis"}
        source_mode = bundle.mode_by_index(resolution.source_mode_index)
        if source_mode is None:
            return {"verdict": "manual_review", "note": "source mode record missing"}
        final_vector = frequency_vectors.get(imaginary_indices[0])
        if final_vector is None or final_vector.shape != (bundle.n_atoms, 3):
            return {
                "verdict": "manual_review",
                "note": "final imaginary mode vectors missing/incomplete",
            }
        try:
            hess_data = parse_orca_hess_file(bundle.hessian_file)
            modes = compute_hessian_modes(
                hess_data.hessian,
                np.asarray(bundle.masses_amu, dtype=np.float64),
                np.asarray(bundle.coordinates_angstrom, dtype=np.float64),
            )
            return compare_mode_correspondence(
                np.asarray(source_mode.vectors, dtype=np.float64),
                final_vector,
                modes.masses_amu,
                np.asarray(bundle.coordinates_angstrom, dtype=np.float64),
                modes.projector,
            )
        except (TsmodeError, ValueError, OSError) as exc:
            logger.debug("mode correspondence evidence failed: %s", exc)
            return {"verdict": "manual_review", "note": f"correspondence check failed: {exc}"}

    @staticmethod
    def _build_normal_modes(
        bundle: FrequencySourceBundle,
        frequency_map: dict[int, float],
        frequency_vectors: dict[int, NDArray[np.float64]],
        fallback_frequencies: list[float],
    ) -> dict[str, Any] | None:
        """Emit a ``normal_modes_v1`` payload for the final analysis.

        Uses ORCA-native indices from the parsed frequency output; falls
        back to the result frequencies (index = descending position) when
        the log could not be re-parsed.
        """
        if not fallback_frequencies and not frequency_map:
            return None
        modes_payload: list[dict[str, Any]] = []
        warnings: list[str] = []
        if frequency_map:
            for mode_index in sorted(frequency_map):
                vectors = frequency_vectors.get(mode_index)
                modes_payload.append(
                    {
                        "mode_index": int(mode_index),
                        "frequency_cm1": float(frequency_map[mode_index]),
                        "vectors": (
                            [[float(v) for v in row] for row in vectors]
                            if vectors is not None
                            else []
                        ),
                    }
                )
            if not frequency_vectors:
                warnings.append("final mode vectors unavailable")
        else:
            warnings.append(
                "final frequency table unavailable; modes indexed by descending position"
            )
            for position, frequency in enumerate(sorted(fallback_frequencies, reverse=True)):
                modes_payload.append(
                    {
                        "mode_index": position,
                        "frequency_cm1": float(frequency),
                        "vectors": [],
                    }
                )
        return {
            "schema_version": "normal_modes_v1",
            "units": {"frequency": "cm-1", "displacement": "dimensionless_orca_normal_mode"},
            "atom_count": bundle.n_atoms,
            "geometry_product_id": "tsmode_optimized",
            "modes": modes_payload,
            "warnings": warnings,
        }

    def _report(
        self,
        request: TsmodeRequest,
        bundle: FrequencySourceBundle,
        resolution: TargetResolution,
        attempts: list[dict[str, Any]],
        *,
        execution_status: str,
        optimization_status: str,
        frequency_status: str,
        frequencies: list[float] | None,
        warnings: list[str],
        validation: dict[str, Any] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
    ) -> TsmodeReport:
        imaginary: list[dict[str, Any]] = []
        if frequencies:
            for value in sorted((float(v) for v in frequencies if float(v) < 0), reverse=True):
                imaginary.append(
                    {
                        "frequency_cm1": value,
                    }
                )
        level = bundle.level.to_dict()
        return TsmodeReport(
            source={
                "bundle_id": bundle.bundle_id,
                "revision": bundle.source_revision(),
                "origin": dict(bundle.origin),
                "geometry_hash": bundle.geo_hash,
                "hessian_sha256": bundle.hessian_sha256,
                "orca_version": bundle.level.orca_version,
            },
            target=resolution.to_dict(),
            mapping={
                "status": resolution.status,
                "method": resolution.mapping_method,
                "version": resolution.mapping_version,
                "verified_against_orca": resolution.evidence.get("verified_against_orca"),
            },
            resolved_level=dict(level),
            source_level=dict(level),
            attempts=[dict(attempt) for attempt in attempts],
            execution_status=execution_status,
            optimization_status=optimization_status,
            frequency_status=frequency_status,
            imaginary_modes=imaginary,
            validation=validation or {},
            artifacts=[dict(entry) for entry in (artifacts or [])],
            warnings=list(warnings),
        )

    def _publish(
        self,
        root: Path,
        result_dir: Path,
        report: TsmodeReport,
        *,
        optimized_xyz: str | None,
        normal_modes: dict[str, Any] | None,
    ) -> None:
        from acp.storage.manifest import ProductKind, ResultManifest

        result_dir.mkdir(parents=True, exist_ok=True)
        (result_dir / "target_resolution.json").write_text(
            json.dumps(report.target, indent=2), encoding="utf-8"
        )
        (result_dir / "tsmode_report.json").write_text(
            json.dumps(report.to_dict(), indent=2), encoding="utf-8"
        )
        if optimized_xyz is not None:
            (result_dir / "optimized.xyz").write_text(optimized_xyz, encoding="utf-8")
        if normal_modes is not None:
            (result_dir / "normal_modes.json").write_text(
                json.dumps(normal_modes, indent=2), encoding="utf-8"
            )

        manifest = ResultManifest(
            task_id=str(root.name), workflow="tsmode", status=report.execution_status
        )
        manifest.add_product(
            id="tsmode_target_resolution",
            label="TS Mode target resolution",
            path="tsmode/target_resolution.json",
            kind=ProductKind.FILE,
        )
        if optimized_xyz is not None:
            manifest.add_product(
                id="tsmode_optimized",
                label="TS Mode optimized structure (saddle-point candidate)",
                path="tsmode/optimized.xyz",
                kind=ProductKind.STRUCTURE,
                metadata={
                    "role": "transition_state",
                    "candidate": "tsmode",
                    "optimization_status": "converged",
                    "source_kind": "optimization",
                    "frequency_status": report.frequency_status,
                    "validation": report.validation,
                    "policy_version": 1,
                },
            )
        if normal_modes is not None:
            manifest.add_product(
                id="tsmode_normal_modes",
                label="Final frequency normal modes",
                path="tsmode/normal_modes.json",
                kind=ProductKind.FREQUENCY_MODES,
            )
        manifest.add_product(
            id="tsmode_report",
            label="TS Mode report",
            path="tsmode/tsmode_report.json",
            kind=ProductKind.REPORT,
        )
        manifest.write(root / "RESULT")

    # ── checkpoint ────────────────────────────────────────────────────────

    @staticmethod
    def _load_checkpoint(work_dir: Path) -> dict[str, Any] | None:
        path = work_dir / _CHECKPOINT_NAME
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _write_checkpoint(
        work_dir: Path,
        fingerprint: str,
        *,
        optimize_credential: dict[str, Any] | None = None,
        frequency_credential: dict[str, Any] | None = None,
        publication: dict[str, Any] | None = None,
        invalidate_frequency: bool = False,
    ) -> None:
        path = work_dir / _CHECKPOINT_NAME
        existing = TsmodeEngine._load_checkpoint(work_dir) or {}
        if (
            existing.get("fingerprint") != fingerprint
            or existing.get("schema_version") != _CHECKPOINT_SCHEMA_V2
        ):
            existing = {
                "fingerprint": fingerprint,
                "schema_version": _CHECKPOINT_SCHEMA_V2,
            }
        if invalidate_frequency:
            existing.pop("frequency_credential", None)
        if optimize_credential is not None:
            existing["optimize_credential"] = optimize_credential
        if frequency_credential is not None:
            existing["frequency_credential"] = frequency_credential
        if publication is not None:
            existing["publication"] = publication
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def _workflow_result(
        status: str,
        attempts: list[dict[str, Any]],
        error: str | None = None,
        *,
        stages: list[str] | None = None,
    ) -> WorkflowResult:
        return WorkflowResult(
            status=status,
            stages_completed=list(stages or []),
            error=error,
            metadata={"attempts": [dict(attempt) for attempt in attempts]},
        )
