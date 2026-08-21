"""Native stationary-point refinement provider for mechanism S3/S4."""

# pyright: reportAny=false, reportArgumentType=false, reportAttributeAccessIssue=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false

from __future__ import annotations

import copy
import hashlib
import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from acp.backends import get_backend
from acp.backends.base import QCResult
from acp.mechanism._helpers import distance
from acp.mechanism.identity import classify_ts_identity
from acp.mechanism.models import (
    ArtifactRef,
    Provenance,
    StationaryPoint,
    StationaryPointRequest,
    TsValidation,
)
from acp.mechanism.presets import FIDELITY_PROFILES, FidelityProfile
from acp.mechanism.refinement_manifest import REFINEMENT_MANIFEST_V1, write_refinement_manifest
from acp.mechanism.rescue import FAILURE_EXIT, apply_rescue_kwargs, build_rescue_plan
from cccp.config import load_config
from cccp.qc.interfaces.constraints import DistanceConstraint
from cccp.qc.interfaces.orca_ts import TsOptResult
from cccp.utils.file_io import read_xyz, write_xyz

from .contracts import RefinementAttempt, RefinementManifest

logger = logging.getLogger(__name__)

NATIVE_PROVIDER_NAME = "acp-native-refinement"
NATIVE_PROVIDER_VERSION = "1.0"

_ROLE_MAP = {
    "reactant": "precursor",
    "product": "product",
    "intermediate": "intermediate",
    "transition_state": "ts",
}


@dataclass(frozen=True)
class _PassResult:
    opt_result: QCResult | TsOptResult
    freq_result: QCResult | None
    coordinates: NDArray[np.float64]
    symbols: list[str]
    frequencies: list[float]
    failure_type: str | None


@dataclass(frozen=True)
class _RefinementOutcome:
    point: StationaryPoint
    summary: dict[str, Any]
    evidence: dict[str, Any]


class NativeRefinementProvider:
    """Native ACP implementation of the mechanism refinement contract."""

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        work_root: Path | str | None = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config) if config is not None else load_config()
        self.work_root: Path = (
            Path(work_root)
            if work_root is not None
            else Path.cwd() / "acp_calc"
        )

    def refine(
        self,
        requests: list[StationaryPointRequest],
        fidelity: FidelityProfile | str,
    ) -> RefinementManifest:
        profile = (
            fidelity
            if isinstance(fidelity, FidelityProfile)
            else FIDELITY_PROFILES[str(fidelity)]
        )
        stage = "s4" if profile.name == "s4" else "s3"
        orca = cast(Any, get_backend("orca")(self.config))
        manifest_id = f"{stage}-{uuid.uuid4().hex}"
        stage_root = self.work_root / stage
        stage_root.mkdir(parents=True, exist_ok=True)

        attempts: list[RefinementAttempt] = []
        successful: list[StationaryPoint] = []
        structures: list[dict[str, Any]] = []

        for request in requests:
            request_dir = stage_root / request.id
            request_dir.mkdir(parents=True, exist_ok=True)
            try:
                outcome = self._refine_one(request, profile, orca, request_dir)
                attempts.append(
                    RefinementAttempt(
                        request_id=request.id,
                        status="success",
                        stationary_point=outcome.point,
                        evidence=outcome.evidence,
                    )
                )
                successful.append(outcome.point)
                structures.append(outcome.summary)
            except Exception as exc:
                logger.warning("Native refinement failed for %s: %s", request.id, exc)
                attempts.append(
                    RefinementAttempt(
                        request_id=request.id,
                        status="failed",
                        stationary_point=None,
                        evidence={"error": str(exc)},
                    )
                )
                structures.append(self._failed_structure_summary(request, str(exc)))

        canonical_winner = next(
            (point for point in successful if point.kind == "ts"),
            successful[0] if successful else None,
        )
        payload = {
            "schema_version": REFINEMENT_MANIFEST_V1,
            "stage": stage,
            "fidelity": profile.name,
            "profile_id": profile.name,
            "run_id": manifest_id,
            "structures": structures,
            "summary": {
                "request_count": len(requests),
                "n_success": len(successful),
                "n_failed": len(requests) - len(successful),
            },
        }
        manifest_dir = stage_root / manifest_id
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / "refinement_manifest.json"
        _ = write_refinement_manifest(payload, manifest_path)
        manifest_hash = _json_hash(payload)
        return RefinementManifest(
            manifest_id=manifest_id,
            canonical_winner=canonical_winner,
            attempts=attempts,
            manifest_hash=manifest_hash,
            fidelity=profile.name,
            metadata={
                "stage": stage,
                "summary": dict(cast(dict[str, Any], payload["summary"])),
                "structures": copy.deepcopy(structures),
                "manifest_path": str(manifest_path),
            },
        )

    def _refine_one(
        self,
        request: StationaryPointRequest,
        profile: FidelityProfile,
        orca: Any,
        request_dir: Path,
    ) -> _RefinementOutcome:
        coordinates, symbols, source_geometry = self._load_request_geometry(request)
        role = _ROLE_MAP[request.role]
        is_ts = request.kind == "ts" or role == "ts"
        forming_bonds = _forming_bonds_from_plan(request.coordinate_plan)
        current_coordinates = np.asarray(coordinates, dtype=float)
        current_symbols = list(symbols)
        warmup_status = "not_run"
        rescue_history: list[dict[str, Any]] = []

        if role in {"intermediate", "ts"} and forming_bonds:
            warmup_constraints = [
                DistanceConstraint(atoms=pair, target=distance(current_coordinates, *pair))
                for pair in forming_bonds
            ]
            try:
                warmup_result = self._constrained_optimize(
                    orca,
                    current_coordinates,
                    current_symbols,
                    request=request,
                    output_dir=request_dir / "warmup",
                    output_name="warmup",
                    constraints=warmup_constraints,
                    method=profile.ts_method,
                    basis=profile.ts_basis,
                    max_cycles=profile.warmup_max_cycles(role),
                    geom_maxiter=profile.warmup_max_cycles(role),
                    solvent=profile.solvent,
                    solvent_model=profile.solvent_model,
                    grid=profile.ts_grid,
                    scf=profile.ts_scf,
                    loose=True,
                    accept_partial=True,
                )
                if warmup_result.success and warmup_result.coordinates is not None:
                    current_coordinates = np.asarray(warmup_result.coordinates, dtype=float)
                    current_symbols = list(warmup_result.symbols or current_symbols)
                    warmup_status = "complete"
                else:
                    warmup_status = "failed"
            except Exception as exc:
                logger.info("Warmup failed for %s: %s", request.id, exc)
                warmup_status = "failed"

        base_opt_kwargs = self._base_opt_kwargs(profile, role, is_ts)
        pass_result = self._run_pass(
            request=request,
            profile=profile,
            orca=orca,
            output_dir=request_dir,
            pass_name="main",
            coordinates=current_coordinates,
            symbols=current_symbols,
            is_ts=is_ts,
            base_opt_kwargs=base_opt_kwargs,
        )

        if pass_result.failure_type is not None:
            if pass_result.failure_type in FAILURE_EXIT:
                raise RuntimeError(f"{request.id}: {pass_result.failure_type}")
            rescue_plan = build_rescue_plan(
                cast(Any, pass_result.failure_type),
                cast(Any, role),
            )
            if rescue_plan.terminal or not rescue_plan.actions:
                raise RuntimeError(f"{request.id}: {pass_result.failure_type}")
            current_pass = pass_result
            for action in rescue_plan.actions:
                rescue_kwargs = dict(base_opt_kwargs)
                if is_ts and isinstance(current_pass.opt_result, TsOptResult):
                    mode_vector = getattr(current_pass.opt_result, "mode_vector", None)
                    if mode_vector is not None:
                        rescue_kwargs["mode_vector"] = mode_vector
                resolved_rescue_kwargs = cast(
                    dict[str, Any],
                    apply_rescue_kwargs(action, rescue_kwargs, include_metadata=False),
                )
                rescue_metadata = {
                    "strategy": action.strategy,
                    "supported_kwargs": {
                        key: value
                        for key, value in resolved_rescue_kwargs.items()
                        if key != "mode_vector"
                    },
                }
                current_pass = self._run_pass(
                    request=request,
                    profile=profile,
                    orca=orca,
                    output_dir=request_dir,
                    pass_name=f"rescue_{action.index:02d}_{action.strategy}",
                    coordinates=current_pass.coordinates,
                    symbols=current_pass.symbols,
                    is_ts=is_ts,
                    base_opt_kwargs=resolved_rescue_kwargs,
                )
                rescue_history.append(
                    {
                        "strategy": action.strategy,
                        "index": action.index,
                        "description": action.description,
                        "failure_type": current_pass.failure_type,
                        "rescue_metadata": rescue_metadata,
                    }
                )
                if current_pass.failure_type is None:
                    break
                if current_pass.failure_type in FAILURE_EXIT:
                    break
            if current_pass.failure_type is not None:
                raise RuntimeError(f"{request.id}: {current_pass.failure_type}")
            pass_result = current_pass

        canonical_xyz = request_dir / "canonical.xyz"
        final_coordinates = np.asarray(pass_result.coordinates, dtype=float)
        final_symbols = list(pass_result.symbols)
        write_xyz(canonical_xyz, final_coordinates, final_symbols, title=request.id)
        geometry_artifact = _artifact_ref(canonical_xyz, "stationary_point_geometry")

        freq_result = pass_result.freq_result
        if freq_result is None:
            raise RuntimeError(f"{request.id}: missing frequency result")

        sp_result = orca.single_point(
            final_coordinates,
            final_symbols,
            charge=request.charge,
            multiplicity=request.multiplicity,
            output_dir=request_dir / "sp",
            output_name="sp",
            method=profile.sp_method,
            basis=profile.sp_basis,
            solvent=profile.solvent,
            solvent_model=profile.solvent_model,
        )
        energy_hartree = _energy_precedence(sp_result, freq_result, pass_result.opt_result)
        thermochemistry = self._thermochemistry_block(sp_result, freq_result)
        identity = None
        validation = None
        if is_ts:
            significant_imaginaries = [
                freq for freq in pass_result.frequencies if float(freq) <= -50.0
            ]
            identity = classify_ts_identity(significant_imaginaries, topology_sane=True)
            validation = TsValidation(identities=[identity], selected_candidate_id=request.id)

        output_artifacts = [geometry_artifact]
        for result, kind in (
            (pass_result.opt_result, "refinement_opt_output"),
            (freq_result, "refinement_freq_output"),
            (sp_result, "refinement_sp_output"),
        ):
            artifact = _result_artifact(result, kind)
            if artifact is not None:
                output_artifacts.append(artifact)

        point = StationaryPoint(
            point_id=request.id,
            role=request.role,
            kind=request.kind,
            geometry=geometry_artifact,
            charge=request.charge,
            multiplicity=request.multiplicity,
            state_id=request.parent_state_id,
            route_id=request.route_id,
            energy_hartree=energy_hartree,
            identity=identity,
            validation=validation,
            provenance=self._point_provenance(request, profile),
            artifacts=output_artifacts,
            metadata={
                "status": "complete",
                "thermochemistry": thermochemistry,
                "symbols": list(final_symbols),
                "coordinates": final_coordinates.tolist(),
                "input_geometry": str(source_geometry),
                "warmup_status": warmup_status,
                "rescue_history": copy.deepcopy(rescue_history),
                "opt_status": _status_label(_result_success(pass_result.opt_result)),
                "frequency_status": _status_label(freq_result.success),
                "sp_status": _status_label(sp_result.success),
            },
        )
        summary = {
            "id": request.id,
            "role": role,
            "kind": request.kind,
            "status": "complete",
            "charge": request.charge,
            "multiplicity": request.multiplicity,
            "forming_bonds": [list(pair) for pair in forming_bonds],
            "opt_status": _status_label(_result_success(pass_result.opt_result)),
            "frequency_status": _status_label(freq_result.success),
            "canonical_frequency_status": _status_label(freq_result.success),
            "sp_status": _status_label(sp_result.success),
            "canonical_xyz": str(canonical_xyz),
            "opt_output": _result_output_path(pass_result.opt_result),
            "canonical_frequency_output": _result_output_path(freq_result),
            "sp_output": _result_output_path(sp_result),
            "opt_energy_hartree": _result_energy(pass_result.opt_result),
            "canonical_frequency_energy_hartree": freq_result.energy,
            "sp_energy_hartree": sp_result.energy,
            "canonical_imaginary_frequencies_cm1": [
                float(freq) for freq in pass_result.frequencies if float(freq) < 0.0
            ],
            "thermochemistry": thermochemistry,
            "warmup_status": warmup_status,
            "pass2_rescue_attempts": copy.deepcopy(rescue_history),
            "attempt_history": copy.deepcopy(rescue_history),
        }
        evidence = {
            "status": "complete",
            "request_dir": str(request_dir),
            "warmup_status": warmup_status,
            "opt_status": summary["opt_status"],
            "frequency_status": summary["frequency_status"],
            "sp_status": summary["sp_status"],
            "canonical_xyz": str(canonical_xyz),
            "rescue_history": copy.deepcopy(rescue_history),
        }
        return _RefinementOutcome(point=point, summary=summary, evidence=evidence)

    def _base_opt_kwargs(
        self,
        profile: FidelityProfile,
        role: str,
        is_ts: bool,
    ) -> dict[str, Any]:
        max_cycles = profile.max_cycles_for(role)
        if is_ts:
            kwargs: dict[str, Any] = {
                "method": profile.ts_method,
                "basis": profile.ts_basis,
                "initial_hessian": profile.initial_hessian_for(role),
                "recalc_hess": profile.ts_recalc_hess,
                "trust_radius": profile.ts_trust_radius,
                "max_cycles": max_cycles,
                "solvent": profile.solvent,
                "solvent_model": profile.solvent_model,
            }
            if profile.ts_grid:
                kwargs["grid"] = profile.ts_grid
            if profile.ts_scf:
                kwargs["scf"] = profile.ts_scf
            return kwargs
        return {
            "method": profile.ts_method,
            "basis": profile.ts_basis,
            "max_cycles": max_cycles,
            "geom_maxiter": max_cycles,
            "solvent": profile.solvent,
            "solvent_model": profile.solvent_model,
        }

    def _run_pass(
        self,
        *,
        request: StationaryPointRequest,
        profile: FidelityProfile,
        orca: Any,
        output_dir: Path,
        pass_name: str,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        is_ts: bool,
        base_opt_kwargs: dict[str, Any],
    ) -> _PassResult:
        opt_dir = output_dir / pass_name / "opt"
        freq_dir = output_dir / pass_name / "freq"
        opt_kwargs = dict(base_opt_kwargs)
        opt_kwargs["output_name"] = "ts_opt" if is_ts else "opt"
        opt_result = self._run_optimization(
            orca,
            coordinates,
            symbols,
            request=request,
            output_dir=opt_dir,
            is_ts=is_ts,
            opt_kwargs=opt_kwargs,
        )
        resolved_coordinates = np.asarray(coordinates, dtype=float)
        resolved_symbols = list(symbols)
        if _result_coordinates(opt_result) is not None:
            resolved_coordinates = np.asarray(
                cast(NDArray[np.float64], _result_coordinates(opt_result)),
                dtype=float,
            )
            resolved_symbols = list(_result_symbols(opt_result) or symbols)

        frequencies: list[float] = []
        freq_result: QCResult | None = None
        if _result_success(opt_result) and _result_coordinates(opt_result) is not None:
            freq_result = orca.frequency(
                resolved_coordinates,
                resolved_symbols,
                charge=request.charge,
                multiplicity=request.multiplicity,
                output_dir=freq_dir,
                output_name="freq",
                method=profile.freq_method,
                basis=profile.freq_basis,
                solvent=profile.solvent,
                solvent_model=profile.solvent_model,
            )
            assert freq_result is not None
            frequencies = [float(freq) for freq in freq_result.frequencies or []]

        failure_type = self._classify_failure(
            is_ts=is_ts,
            opt_result=opt_result,
            freq_result=freq_result,
        )
        return _PassResult(
            opt_result=opt_result,
            freq_result=freq_result,
            coordinates=resolved_coordinates,
            symbols=resolved_symbols,
            frequencies=frequencies,
            failure_type=failure_type,
        )

    def _run_optimization(
        self,
        orca: Any,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        *,
        request: StationaryPointRequest,
        output_dir: Path,
        is_ts: bool,
        opt_kwargs: dict[str, Any],
    ) -> QCResult | TsOptResult:
        if is_ts:
            return cast(
                TsOptResult,
                orca.transition_state_opt(
                    coordinates,
                    symbols,
                    charge=request.charge,
                    multiplicity=request.multiplicity,
                    output_dir=output_dir,
                    **opt_kwargs,
                ),
            )
        return cast(
            QCResult,
            orca.optimize(
                coordinates,
                symbols,
                charge=request.charge,
                multiplicity=request.multiplicity,
                output_dir=output_dir,
                **opt_kwargs,
            ),
        )

    def _classify_failure(
        self,
        *,
        is_ts: bool,
        opt_result: QCResult | TsOptResult,
        freq_result: QCResult | None,
    ) -> str | None:
        if not _result_success(opt_result):
            return _error_failure_type(_result_error(opt_result))
        if _result_coordinates(opt_result) is None:
            return "geometry_not_converged"
        if freq_result is None:
            return "geometry_not_converged"
        if not freq_result.success:
            return _error_failure_type(freq_result.error_message)
        frequencies = [float(freq) for freq in freq_result.frequencies or []]
        if not is_ts:
            if bool(freq_result.metadata.get("collapsed_to_product")):
                return "collapsed_to_product"
            if any(freq < 0.0 for freq in frequencies):
                return "minimum_with_imaginary"
            return None
        significant_imaginaries = [freq for freq in frequencies if freq <= -50.0]
        if len(significant_imaginaries) > 1:
            return "higher_order_saddle"
        if len(significant_imaginaries) == 0:
            return "ts_no_imaginary"
        return None

    def _constrained_optimize(
        self,
        orca: Any,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        *,
        request: StationaryPointRequest,
        output_dir: Path,
        output_name: str,
        constraints: Sequence[DistanceConstraint],
        **kwargs: Any,
    ) -> QCResult:
        optimize_callable = getattr(orca, "constrained_optimize", None)
        if callable(optimize_callable):
            return cast(
                QCResult,
                optimize_callable(
                    coordinates,
                    symbols,
                    charge=request.charge,
                    multiplicity=request.multiplicity,
                    output_dir=output_dir,
                    output_name=output_name,
                    constraints=list(constraints),
                    **kwargs,
                ),
            )
        interface = getattr(orca, "_interface", None)
        interface_callable = getattr(interface, "constrained_optimize", None)
        if callable(interface_callable):
            return cast(
                QCResult,
                interface_callable(
                    coordinates,
                    symbols,
                    charge=request.charge,
                    multiplicity=request.multiplicity,
                    output_dir=output_dir,
                    output_name=output_name,
                    constraints=list(constraints),
                    **kwargs,
                ),
            )
        raise AttributeError("ORCA backend does not expose constrained_optimize")

    def _load_request_geometry(
        self,
        request: StationaryPointRequest,
    ) -> tuple[NDArray[np.float64], list[str], Path]:
        candidates = [request.input_geometry, *request.fallback_geometries]
        errors: list[str] = []
        for artifact in candidates:
            candidate_path = Path(str(artifact.path))
            if not candidate_path.is_file():
                errors.append(f"missing file: {candidate_path}")
                continue
            try:
                coordinates, symbols = _read_geometry_file(candidate_path)
            except Exception as exc:
                errors.append(f"{candidate_path}: {exc}")
                continue
            return np.asarray(coordinates, dtype=float), list(symbols), candidate_path
        raise FileNotFoundError("; ".join(errors) or f"No readable geometry for {request.id}")

    def _point_provenance(
        self,
        request: StationaryPointRequest,
        profile: FidelityProfile,
    ) -> Provenance:
        return Provenance(
            provider=NATIVE_PROVIDER_NAME,
            provider_version=NATIVE_PROVIDER_VERSION,
            provider_commit=request.provenance.provider_commit,
            strategy=f"native-{profile.name}",
            strategy_version=NATIVE_PROVIDER_VERSION,
            profile_id=profile.name,
            schema_version=REFINEMENT_MANIFEST_V1,
            input_signature=request.provenance.input_signature,
        )

    def _thermochemistry_block(
        self,
        sp_result: QCResult,
        freq_result: QCResult,
    ) -> dict[str, Any]:
        gibbs_reference = (
            freq_result.gibbs if freq_result.gibbs is not None else freq_result.energy
        )
        g_composite = None
        if (
            sp_result.energy is not None
            and freq_result.energy is not None
            and gibbs_reference is not None
        ):
            g_composite = sp_result.energy + (gibbs_reference - freq_result.energy)
        return {
            "g_composite_hartree": g_composite,
            "frequency_energy_hartree": freq_result.energy,
            "frequency_gibbs_hartree": freq_result.gibbs,
            "sp_energy_hartree": sp_result.energy,
            "note": (
                "Composite Gibbs uses SP energy plus the independent frequency thermal "
                "correction."
            ),
        }

    def _failed_structure_summary(
        self,
        request: StationaryPointRequest,
        error: str,
    ) -> dict[str, Any]:
        return {
            "id": request.id,
            "role": _ROLE_MAP[request.role],
            "kind": request.kind,
            "status": "failed",
            "charge": request.charge,
            "multiplicity": request.multiplicity,
            "forming_bonds": [
                list(pair) for pair in _forming_bonds_from_plan(request.coordinate_plan)
            ],
            "opt_status": "failed",
            "frequency_status": "not_run",
            "canonical_frequency_status": "not_run",
            "sp_status": "not_run",
            "error": error,
        }


def _forming_bonds_from_plan(coordinate_plan: Any) -> list[tuple[int, int]]:
    bonds: list[tuple[int, int]] = []
    for spec in getattr(coordinate_plan, "coordinates", ()):
        if str(getattr(spec, "kind", "")).lower() != "distance":
            continue
        atoms = tuple(int(atom) for atom in getattr(spec, "atoms", ())[:2])
        if len(atoms) != 2:
            continue
        pair = (min(atoms), max(atoms))
        if pair not in bonds:
            bonds.append(pair)
    return bonds


def _artifact_ref(path: Path | str, kind: str) -> ArtifactRef:
    candidate = Path(str(path))
    if candidate.is_file():
        checksum = _file_sha256(candidate)
        resolved_path = str(candidate)
    else:
        resolved_path = str(path)
        checksum = _sha256_text(resolved_path)
    return ArtifactRef(path=resolved_path, sha256=checksum, kind=kind)


def _result_artifact(result: object, kind: str) -> ArtifactRef | None:
    output_path = _result_output_path(result)
    if output_path is None:
        return None
    return _artifact_ref(Path(output_path), kind)


def _result_output_path(result: object) -> str | None:
    log_file = getattr(result, "log_file", None)
    output_file = getattr(result, "output_file", None)
    candidate = log_file or output_file
    if candidate is None:
        return None
    return str(candidate)


def _result_success(result: object) -> bool:
    return bool(getattr(result, "success", False))


def _result_error(result: object) -> str | None:
    return cast(str | None, getattr(result, "error_message", None))


def _result_coordinates(result: object) -> NDArray[np.float64] | None:
    coordinates = getattr(result, "coordinates", None)
    if coordinates is None:
        return None
    return np.asarray(coordinates, dtype=float)


def _result_symbols(result: object) -> list[str] | None:
    symbols = getattr(result, "symbols", None)
    if symbols is None:
        return None
    return [str(symbol) for symbol in symbols]


def _result_energy(result: object) -> float | None:
    if hasattr(result, "energy_hartree"):
        energy = getattr(result, "energy_hartree", None)
        if energy is not None:
            return float(energy)
    energy = getattr(result, "energy", None)
    return float(energy) if energy is not None else None


def _energy_precedence(
    sp_result: QCResult,
    freq_result: QCResult,
    opt_result: QCResult | TsOptResult,
) -> float | None:
    for result in (sp_result, freq_result, opt_result):
        energy = _result_energy(result)
        if energy is not None:
            return energy
    return None


def _status_label(success: bool) -> str:
    return "complete" if success else "failed"


def _error_failure_type(error_message: str | None) -> str:
    normalized = str(error_message or "").lower()
    if "scf" in normalized:
        return "scf_failure"
    if "timeout" in normalized or "timed out" in normalized or "time out" in normalized:
        return "crash_timeout"
    return "geometry_not_converged"


def _json_hash(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


def _sha256_text(payload: str) -> str:
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _read_geometry_file(path: Path) -> tuple[NDArray[np.float64], list[str]]:
    if path.suffix.lower() == ".xyz":
        coordinates, symbols = read_xyz(path)
        return np.asarray(coordinates, dtype=float), [str(symbol) for symbol in symbols]
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError(f"Geometry JSON must be an object: {path}")
        geometry_payload = payload.get("geometry")
        coordinates_payload = payload.get("coordinates")
        symbols_payload = payload.get("symbols")
        if isinstance(geometry_payload, Mapping):
            coordinates_payload = geometry_payload.get("coordinates", coordinates_payload)
            symbols_payload = geometry_payload.get("symbols", symbols_payload)
        elif geometry_payload is not None and coordinates_payload is None:
            coordinates_payload = geometry_payload
        if coordinates_payload is None or symbols_payload is None:
            raise ValueError(f"Geometry JSON requires coordinates/geometry and symbols: {path}")
        coordinates = np.asarray(coordinates_payload, dtype=float)
        symbols = [str(symbol) for symbol in cast(Sequence[object], symbols_payload)]
        return coordinates, symbols
    raise ValueError(f"Unsupported geometry artifact: {path}")


__all__ = ["NativeRefinementProvider"]
