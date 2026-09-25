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
from dataclasses import dataclass
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
from acp.calculations.primitives.frequency import run_frequency
from acp.calculations.primitives.optimize import run_optimize
from acp.calculations.tsmode.contracts import (
    FrequencySourceBundle,
    TargetResolution,
    TsmodeError,
    TsmodeOptimizationSettings,
    TsmodeReport,
    TsmodeRequest,
)
from acp.calculations.tsmode.mode_mapping import enforce_launch_gate, resolve_target_mode
from acp.calculations.tsmode.source import (
    compute_hessian_modes,
    snapshot_bundle_files,
    verify_snapshot_hashes,
)
from acp.calculations.tsmode.validation import (
    SIGNIFICANT_IMAGINARY_THRESHOLD_CM1,
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

logger = logging.getLogger(__name__)

__all__ = ["TsmodeEngine", "TsmodeEngineResult", "compute_engine_fingerprint"]

_CHECKPOINT_NAME = "tsmode_checkpoint.json"
_EXECUTION_STATUSES = ("pending", "running", "completed", "failed")


@dataclass(frozen=True, slots=True)
class TsmodeEngineResult:
    """Terminal outcome of one engine run."""

    workflow_result: WorkflowResult
    report: TsmodeReport
    resolution: TargetResolution
    output_root: Path


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
        completed_stages: list[str] = ["prepare_source", "resolve_target"]

        attempts: list[dict[str, Any]] = []
        optimized_coords: NDArray[np.float64] | None = None
        optimization_status = "pending"

        # 3. optimize_ts — directed OptTS reading the source Hessian.
        resume_optimize = (
            checkpoint is not None
            and checkpoint.get("fingerprint") == fingerprint
            and checkpoint.get("optimization", {}).get("status") == "completed"
            and isinstance(checkpoint.get("optimization", {}).get("coordinates"), list)
        )
        if resume_optimize:
            optimized_coords = np.asarray(
                checkpoint["optimization"]["coordinates"], dtype=np.float64
            )
            optimization_status = "completed"
            attempts.append({"stage": "optimize_ts", "resumed": True})
            completed_stages.append("optimize_ts")
            warnings.append("resumed from checkpoint: optimization stage skipped")
        else:
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
                optimized_coords = np.asarray(opt_result.coords, dtype=np.float64)
                optimization_status = "completed"
                completed_stages.append("optimize_ts")
                self._write_checkpoint(
                    work_dir,
                    fingerprint,
                    optimization={
                        "status": "completed",
                        "coordinates": [[float(v) for v in row] for row in optimized_coords],
                        "energy_hartree": opt_result.energy,
                    },
                    frequency=None,
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

        # 4. frequency_final — same level, on the final structure.
        frequency_status = "pending"
        frequencies: list[float] = []
        frequency_map: dict[int, float] = {}
        frequency_vectors: dict[int, NDArray[np.float64]] = {}
        resume_frequency = (
            checkpoint is not None
            and checkpoint.get("fingerprint") == fingerprint
            and checkpoint.get("frequency", {}).get("status") == "completed"
        )
        if not request.final_frequency:
            frequency_status = "skipped"
            warnings.append("final frequency validation disabled by request")
        elif resume_frequency:
            frequencies = [float(value) for value in checkpoint["frequency"].get("frequencies", [])]
            frequency_status = "completed"
            completed_stages.append("frequency_final")
            attempts.append({"stage": "frequency_final", "resumed": True})
        else:
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
            frequency_map, frequency_vectors = self._parse_frequency_products(freq_result)
            frequencies = [float(value) for value in freq_result.frequencies]
            if freq_result.status == "completed" and frequencies:
                frequency_status = "completed"
                completed_stages.append("frequency_final")
                self._write_checkpoint(
                    work_dir,
                    fingerprint,
                    optimization=None,
                    frequency={
                        "status": "completed",
                        "frequencies": frequencies,
                        "geometry_sha_prefix": hashlib.sha256(
                            json.dumps(
                                [[round(float(v), 8) for v in row] for row in optimized_coords]
                            ).encode("utf-8")
                        ).hexdigest()[:16],
                    },
                )
            else:
                frequency_status = "failed"

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
        )
        normal_modes = self._build_normal_modes(
            bundle, frequency_map, frequency_vectors, frequencies
        )
        self._publish(
            root,
            result_dir,
            report,
            optimized_xyz="\n".join(geometry_lines) + "\n",
            normal_modes=normal_modes,
        )
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

    def _run_optimize_stage(
        self,
        bundle: FrequencySourceBundle,
        resolution: TargetResolution,
        settings: TsmodeOptimizationSettings,
        hess_file: Path,
        target_dir: Path,
    ) -> CalculationResult:
        target_dir.mkdir(parents=True, exist_ok=True)
        level_kwargs: dict[str, Any] = {}
        if bundle.level.solvent and bundle.level.solvent_model:
            level_kwargs["solvent"] = bundle.level.solvent
            level_kwargs["solvent_model"] = bundle.level.solvent_model
        if bundle.level.grid:
            level_kwargs["grid"] = bundle.level.grid
        if bundle.level.scf:
            level_kwargs["scf"] = bundle.level.scf
        if settings.convergence:
            level_kwargs["opt_level"] = settings.convergence

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
        resources.update(level_kwargs)

        artifact = StructureArtifact(
            path=Path(bundle.hessian_file),
            elements=list(bundle.elements),
            role=StructureRole.TRANSITION_STATE,
            source="tsmode",
        )
        request = CalculationRequest(
            input_artifact=artifact,
            method=bundle.level.method,
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
        level_kwargs: dict[str, Any] = {}
        if bundle.level.solvent and bundle.level.solvent_model:
            level_kwargs["solvent"] = bundle.level.solvent
            level_kwargs["solvent_model"] = bundle.level.solvent_model
        if bundle.level.grid:
            level_kwargs["grid"] = bundle.level.grid
        if bundle.level.scf:
            level_kwargs["scf"] = bundle.level.scf

        resources: dict[str, Any] = {
            "backend": "orca",
            "config": self._effective_config(),
            "coordinates": [[float(v) for v in row] for row in coordinates],
            "symbols": list(bundle.elements),
            "charge": bundle.charge,
            "multiplicity": bundle.multiplicity,
            "output_dir": str(target_dir),
        }
        resources.update(level_kwargs)
        artifact = StructureArtifact(
            path=Path(bundle.hessian_file),
            elements=list(bundle.elements),
            role=StructureRole.TRANSITION_STATE,
            source="tsmode",
        )
        request = CalculationRequest(
            input_artifact=artifact,
            method=bundle.level.method,
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
        """Parse the final-frequency log into (frequency map, mode vectors)."""
        log_path = next(
            (
                Path(artifact.path)
                for artifact in result.artifacts
                if artifact.type in ("log", "frequency_log", "output")
            ),
            None,
        )
        if log_path is None or not log_path.is_file():
            return {}, {}
        text = log_path.read_text(encoding="utf-8", errors="replace")
        return parse_ts_frequency_map(text), parse_ts_mode_vectors(text)

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
    ) -> TsmodeReport:
        imaginary: list[dict[str, Any]] = []
        if frequencies:
            for value in sorted((float(v) for v in frequencies if float(v) < 0), reverse=True):
                imaginary.append(
                    {
                        "frequency_cm1": value,
                        "significant": abs(value) >= SIGNIFICANT_IMAGINARY_THRESHOLD_CM1,
                    }
                )
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
            resolved_level=bundle.level.to_dict(),
            attempts=[dict(attempt) for attempt in attempts],
            execution_status=execution_status,
            optimization_status=optimization_status,
            frequency_status=frequency_status,
            imaginary_modes=imaginary,
            validation=validation or {},
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
                metadata={"role": "transition_state", "candidate": "tsmode"},
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
        optimization: dict[str, Any] | None,
        frequency: dict[str, Any] | None,
    ) -> None:
        path = work_dir / _CHECKPOINT_NAME
        existing = TsmodeEngine._load_checkpoint(work_dir) or {
            "fingerprint": fingerprint,
            "schema_version": "tsmode_checkpoint_v1",
        }
        if existing.get("fingerprint") != fingerprint:
            # Target or system changed — full invalidation (plan §10.2).
            existing = {
                "fingerprint": fingerprint,
                "schema_version": "tsmode_checkpoint_v1",
            }
        if optimization is not None:
            existing["optimization"] = optimization
        if frequency is not None:
            existing["frequency"] = frequency
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
