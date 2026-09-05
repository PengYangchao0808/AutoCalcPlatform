"""PES relaxed-scan core — migrated from mechanism/bond_scan.py.

Implements the scan pipeline:

    prepare → validate_coordinate → materialize_input → run_relaxed_scan →
    extract_frames → run_single_points → build_profile → select_candidates →
    finalize

The pipeline fails fast: a backend result with ``success=False`` aborts the
run before frame extraction, so partial scan geometries are never promoted
to frames, single points, or candidates.

Candidate recommendation reuses the full path-selection policy from
:mod:`acp.calculations.pes.path_selection` (endpoint guard, reaction-progress
filter, knee detection, TS right-shift, INT plateau/midpoint, reactant-barrier
and scaffold gates) for distance scans.  When the policy cannot resolve a seed
(flat curves, gated candidates, incomplete profiles) the recommendation
falls back to the self-contained peak/minimum heuristic and records the
rejection reason in the scan-quality notes.

Selection policy overrides are read from the merged ACP config under
``pes.scan_selection`` (or the legacy ``step2.scan.selection`` block) and
forwarded to :func:`policy_from_config`.  Recognised flat keys include
``endpoint_exclusion_frames``, ``ts_min_prominence_kcal_mol``,
``int_min_basin_prominence_kcal_mol``, ``min_reaction_progress`` and
``ts_right_shift_override_A``; nested policy blocks (``knee``, ``ts_seed``,
``int_plateau``, ``admission``) are accepted as well.

Key migration changes vs bond_scan.py:
- Uses ``get_backend("orca").relaxed_scan`` (not ``ORCAInterface`` directly)
- Uses generic naming (PesScanRequest, no orchestrator identifiers)
- Single-point delegation via BatchSinglePointExecutor (todo 31)
- Result manifest via ``acp.storage.manifest.ResultManifest``
"""

# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnnecessaryIsInstance=false, reportUnusedCallResult=false, reportUnusedFunction=false, reportUnusedParameter=false

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, TypeGuard

import numpy as np

import acp.backends
from acp.backends.base import RelaxedScanCalculator
from acp.calculations.batch.singlepoint import BatchSinglePointExecutor
from acp.calculations.pes.atom_selection import (
    FunctionalAtomSelection,
    normalize_selection_kind,
    parse_functional_atom_selection,
)
from acp.calculations.pes.contracts import (
    CandidateRecommendation,
    EnergyProfile,
    PesScanRequest,
    ScanCoordinate,
    ScanFrame,
    ScanProtocol,
    ScanQuality,
    SinglePointSpec,
    StructureSource,
    validate_scan_coordinates,
    validate_scan_protocol,
)
from acp.calculations.pes.outputs import (
    PES_SCAN_DIR_NAME,
    PES_SCAN_RELATIVE_PATH,
    PES_SCAN_STAGE,
)
from acp.calculations.pes.path_analysis import build_orca_scan_profile
from acp.calculations.pes.path_selection import (
    SeedSelection,
    SelectionPolicy,
    policy_from_config,
    select_path_seeds,
)
from acp.calculations.progress import LiveMetric, ProgressReporter
from acp.storage.layout import TaskStorage
from cccp.qc.interfaces.constraints import ConstraintKind, CoordinateSpec, ReactionCoordinatePlan
from cccp.qc.interfaces.xtb_scan import RelaxedScanResult
from cccp.utils.constants import HARTREE_TO_KCAL
from cccp.utils.file_io import read_xyz, write_xyz
from cccp.utils.geometry_tools import GeometryUtils

logger = logging.getLogger(__name__)

# Corrector-acceptance bounds (RPH): |q_actual - q_target| beyond these
# marks the frame off-constraint and gates candidate recommendation.
_DEFAULT_CONSTRAINT_TOLERANCES: dict[str, float] = {
    "distance": 0.01,  # Å
    "angle": 0.5,  # degrees
    "dihedral": 1.0,  # degrees
}


def _constraint_tolerances(cfg: dict[str, Any]) -> dict[str, float]:
    """Resolve per-kind constraint-residual tolerances (config-overridable)."""
    tolerances = dict(_DEFAULT_CONSTRAINT_TOLERANCES)
    override = cfg.get("pes_scan", {}).get("constraint_residual_tolerance_angstrom")
    if override is not None:
        tolerances["distance"] = float(override)
    return tolerances


SCAN_DIR_NAME = PES_SCAN_DIR_NAME
"""Stable per-task PES scan directory name."""
PES_SCAN_STAGES = (
    "prepare",
    "validate_coordinate",
    "materialize_input",
    "run_relaxed_scan",
    "extract_frames",
    "run_single_points",
    "build_profile",
    "select_candidates",
    "finalize",
)


# ── coordinate plan ────────────────────────────────────────────────────


def build_coordinate_plan(
    coordinate: ScanCoordinate,
    *,
    coordinate_id: str | None = None,
) -> CoordinateSpec:
    """Build a 0-based :class:`CoordinateSpec` from a :class:`ScanCoordinate`."""
    kind = coordinate.kind
    if not _is_constraint_kind(kind):
        raise ValueError(f"Unsupported scan coordinate kind: {kind!r}")
    return CoordinateSpec(
        id=coordinate_id or kind,
        kind=kind,
        atoms=tuple(int(atom) for atom in coordinate.atoms),
        role="drive",
        start=coordinate.start,
        end=coordinate.end,
    )


def _is_constraint_kind(value: str) -> TypeGuard[ConstraintKind]:
    """Return whether a scan kind is supported by the constraint layer."""
    return value in ("distance", "angle", "dihedral")


# ── main pipeline ──────────────────────────────────────────────────────


def run_pes_scan(
    *,
    request: dict[str, Any] | PesScanRequest,
    output_dir: Path | str,
    config: dict[str, Any] | None = None,
    progress_reporter: ProgressReporter | None = None,
) -> dict[str, Any]:
    """Run a one-dimensional PES relaxed scan.

    Args:
        request: PesScanRequest payload or dict.
        output_dir: Task output root.
        config: Merged ACP config dict.
        progress_reporter: Optional progress reporter for scheduler observation.

    Returns:
        The scan result payload with frames, profile, and candidates.

    Raises:
        ValueError: On invalid requests or protocol values.
        RuntimeError: On QC execution failure.
    """
    req = request if isinstance(request, PesScanRequest) else PesScanRequest.from_dict(request)
    if req.mode not in ("bond_length_scan", "coordinate_scan"):
        raise ValueError(
            f"request.mode must be 'bond_length_scan' or 'coordinate_scan', got {req.mode!r}"
        )

    if progress_reporter is not None:
        progress_reporter.initialize()

    try:
        cfg = config or {}
        out_root = Path(output_dir).resolve()
        work_root = out_root / "WORK"

        # -- prepare --
        if progress_reporter is not None:
            progress_reporter.start_stage("prepare")
        scan_dir = TaskStorage(out_root).stage_dir(PES_SCAN_STAGE, SCAN_DIR_NAME)
        scan_dir.mkdir(parents=True, exist_ok=True)
        if progress_reporter is not None:
            progress_reporter.complete_stage("prepare")

        # -- validate_coordinate --
        if progress_reporter is not None:
            progress_reporter.start_stage("validate_coordinate")
        scan_coordinates = req.scan_coordinates
        coordinate = scan_coordinates[0]
        validate_scan_protocol(coordinate, req.protocol)
        validate_scan_coordinates(scan_coordinates)
        protocol = req.protocol
        if progress_reporter is not None:
            progress_reporter.complete_stage("validate_coordinate")

        # -- materialize_input --
        if progress_reporter is not None:
            progress_reporter.start_stage("materialize_input")
        coords, symbols, charge, multiplicity = _materialize_structure(req.source, work_root, cfg)
        for scan_coordinate in scan_coordinates:
            for index in scan_coordinate.atoms:
                if index < 0 or index >= len(symbols):
                    raise ValueError(
                        f"Scan atom index {index} is out of range "
                        f"(structure has {len(symbols)} atoms)"
                    )
        selection = _validate_functional_selection(
            req.selection,
            scan_coordinates,
            symbols,
            coords,
        )
        input_xyz = scan_dir / "input.xyz"
        write_xyz(input_xyz, coords, symbols, title="PES scan input")
        if progress_reporter is not None:
            progress_reporter.complete_stage("materialize_input")

        # -- run_relaxed_scan --
        if progress_reporter is not None:
            progress_reporter.start_stage("run_relaxed_scan")
        scan_result = _run_relaxed_scan_backend(
            coords=coords,
            symbols=symbols,
            charge=charge,
            multiplicity=multiplicity,
            coordinates=scan_coordinates,
            protocol=protocol,
            scan_dir=scan_dir,
            cfg=cfg,
        )
        if not scan_result.success:
            # Fail fast: partial scan geometries must never reach frame
            # extraction, single points, or candidate recommendation.  The
            # per-frame artifacts already written by the backend stay on disk
            # as diagnostics only.
            raise RuntimeError(f"Relaxed scan failed: {scan_result.message}")
        if progress_reporter is not None:
            progress_reporter.complete_stage("run_relaxed_scan")

        # -- extract_frames --
        if progress_reporter is not None:
            progress_reporter.start_stage("extract_frames")
        tolerances = _constraint_tolerances(cfg)
        frames = _extract_frames(
            scan_result,
            coordinate,
            scan_dir,
            reporter=progress_reporter,
            coordinates=scan_coordinates,
            tolerances=tolerances,
        )
        if progress_reporter is not None:
            progress_reporter.complete_stage("extract_frames")

        # -- run_single_points --
        if progress_reporter is not None:
            progress_reporter.start_stage("run_single_points")
        sp_spec = protocol.single_point
        _run_single_points(
            frames,
            charge,
            multiplicity,
            sp_spec,
            scan_dir,
            cfg,
            reporter=progress_reporter,
        )
        if progress_reporter is not None:
            progress_reporter.complete_stage("run_single_points")

        # -- build_profile --
        if progress_reporter is not None:
            progress_reporter.start_stage("build_profile")
        profile = _build_energy_profile(frames, sp_spec)
        if progress_reporter is not None:
            progress_reporter.complete_stage("build_profile")

        # -- select_candidates --
        if progress_reporter is not None:
            progress_reporter.start_stage("select_candidates")
        off_constraint = [f for f in frames if f.constraint_residual_ok is False]
        constraints_satisfied = not off_constraint
        max_residual: float | None = None
        for frame in frames:
            if frame.max_constraint_residual is not None:
                if max_residual is None or frame.max_constraint_residual > max_residual:
                    max_residual = frame.max_constraint_residual
        ts_recs, int_recs, quality = _recommend_candidates(
            frames,
            coordinate,
            profile,
            cfg,
            scan_dir,
            coordinates=scan_coordinates,
            constraints_satisfied=constraints_satisfied,
            constraint_tolerance=tolerances.get(coordinate.kind),
            max_constraint_residual=max_residual,
        )
        if not constraints_satisfied:
            logger.error(
                "PES scan constraint gate: %d/%d frames off-constraint "
                "(max residual %s, tolerance %s) — candidates suppressed",
                len(off_constraint),
                len(frames),
                max_residual,
                tolerances.get(coordinate.kind),
            )
        if progress_reporter is not None:
            progress_reporter.complete_stage("select_candidates")

    except Exception as exc:
        if progress_reporter is not None:
            progress_reporter.fail_stage(progress_reporter.current_stage or "unknown", str(exc))
        raise

    return {
        "mode": req.mode,
        "frames": [f.to_dict() for f in frames],
        "profile": profile.to_dict(),
        "quality": quality.to_dict(),
        "ts_recommendations": [r.to_dict() for r in ts_recs],
        "int_recommendations": [r.to_dict() for r in int_recs],
        "coordinate": coordinate.to_dict(),
        "coordinates": [item.to_dict() for item in scan_coordinates],
        "selection": selection,
        "protocol": protocol.to_dict(),
        "scan_dir": str(scan_dir),
        "scan_dir_rel": PES_SCAN_RELATIVE_PATH,
    }


# ── structure materialisation ──────────────────────────────────────────


def _materialize_structure(
    source: StructureSource,
    work_root: Path,
    cfg: dict[str, Any],
) -> tuple[np.ndarray[Any, Any], list[str], int, int]:
    """Resolve the requested structure source to coords/symbols/charge/mult."""
    source_type = source.source_type
    if source_type == "xyz_text":
        xyz_path = _materialize_xyz_text(source, work_root)
    elif source_type == "structure_asset":
        if not source.asset_path:
            raise ValueError("structure_asset source requires a resolved asset_path")
        asset = Path(source.asset_path)
        if not asset.is_file():
            raise ValueError(f"Structure asset file not found: {asset}")
        xyz_path = asset
    elif source_type == "task_artifact":
        if not source.artifact_path:
            raise ValueError("task_artifact source requires source.artifact_path")
        artifact = Path(source.artifact_path)
        if not artifact.is_file():
            raise ValueError(f"Source artifact not found: {artifact}")
        xyz_path = artifact
    else:
        raise ValueError(f"Unknown source_type: {source_type!r}")

    coords, symbols = read_xyz(xyz_path)
    coords_arr = np.asarray(coords, dtype=float)
    if coords_arr.ndim != 2 or coords_arr.shape[1] != 3:
        raise ValueError(f"Invalid geometry from {xyz_path}: not an (N,3) coordinate block")
    if not np.all(np.isfinite(coords_arr)):
        raise ValueError(f"Non-finite coordinates in structure source: {xyz_path}")
    if len(symbols) < 2:
        raise ValueError("Structures with fewer than 2 atoms cannot be scanned")
    return coords_arr, [str(s) for s in symbols], source.charge, source.multiplicity


def _materialize_xyz_text(source: StructureSource, work_root: Path) -> Path:
    from acp.intake.parsers import parse_xyz_text

    text = str(source.xyz_text or "").strip()
    if not text:
        raise ValueError("xyz_text source requires non-empty source.xyz_text")
    parsed = parse_xyz_text(text)
    if not parsed.structures and parsed.errors:
        raise ValueError(f"Could not parse pasted XYZ: {'; '.join(parsed.errors[:3])}")
    if not parsed.structures:
        raise ValueError("Pasted XYZ contains no structures")
    asset = parsed.structures[0]
    inputs_dir = work_root / "01_PREPARE" / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    xyz_path = inputs_dir / "pasted.xyz"
    xyz_path.write_text(str(asset.xyz or text), encoding="utf-8")
    return xyz_path


# ── relaxed scan via backend ───────────────────────────────────────────


def _run_relaxed_scan_backend(
    *,
    coords: np.ndarray[Any, Any],
    symbols: list[str],
    charge: int,
    multiplicity: int,
    coordinate: ScanCoordinate | None = None,
    coordinates: tuple[ScanCoordinate, ...] | None = None,
    protocol: ScanProtocol,
    scan_dir: Path,
    cfg: dict[str, Any],
) -> RelaxedScanResult:
    """Execute the relaxed scan via ``get_backend("orca").relaxed_scan``.

    This is the fixed path — bond_scan.py:724 called ORCAInterface directly;
    this module resolves and validates the backend capability first.
    """
    backend_ref = acp.backends.get_backend(protocol.scan_driver.software)
    backend = backend_ref(cfg) if isinstance(backend_ref, type) else backend_ref
    if not isinstance(backend, RelaxedScanCalculator):
        raise TypeError(f"Backend {protocol.scan_driver.software!r} does not support relaxed scans")
    scan_coordinates = tuple(coordinates or ((coordinate,) if coordinate is not None else ()))
    if not scan_coordinates:
        raise ValueError("at least one scan coordinate is required")
    specs = tuple(
        build_coordinate_plan(
            item,
            coordinate_id=(item.kind if len(scan_coordinates) == 1 else f"coordinate_{index + 1}"),
        )
        for index, item in enumerate(scan_coordinates)
    )
    plan = ReactionCoordinatePlan(coordinates=specs, points=scan_coordinates[0].n_points)
    nproc = int((cfg.get("resources") or {}).get("nproc") or 1)
    result = backend.relaxed_scan(
        coords,
        symbols,
        output_dir=scan_dir,
        plan=plan,
        charge=charge,
        multiplicity=multiplicity,
        method=protocol.scan_optimizer.method,
        nprocs=nproc,
        use_scants=bool(protocol.scan_driver.use_scants),
        full_scan=bool(protocol.scan_driver.full_scan),
        geom_maxiter=int(
            protocol.scan_optimizer.max_iterations or protocol.scan_driver.max_iterations
        ),
    )
    if not isinstance(result, RelaxedScanResult):
        raise TypeError("Relaxed-scan backend returned an invalid result")
    return result


# ── frame extraction ───────────────────────────────────────────────────


def _extract_frames(
    scan_result: RelaxedScanResult,
    coordinate: ScanCoordinate,
    scan_dir: Path,
    reporter: ProgressReporter | None = None,
    *,
    coordinates: tuple[ScanCoordinate, ...] | None = None,
    tolerances: dict[str, float] | None = None,
) -> list[ScanFrame]:
    """Build per-frame records from the scan result.

    Every frame carries per-coordinate constraint residuals
    (``actual - target``) plus an acceptance flag against *tolerances*;
    optimizer convergence alone does not imply the frame sits on the
    prescribed reaction-coordinate slice.
    """
    frames: list[ScanFrame] = []
    frames_dir = scan_dir / "scan_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    total_points = len(scan_result.points)
    scan_coordinates = coordinates or (coordinate,)
    resolved_tolerances = tolerances or dict(_DEFAULT_CONSTRAINT_TOLERANCES)
    coordinate_ids = [
        item.kind if len(scan_coordinates) == 1 else f"coordinate_{index + 1}"
        for index, item in enumerate(scan_coordinates)
    ]
    for point in scan_result.points:
        index = int(point.frame_index)
        frame_path = frames_dir / f"frame_{index:03d}.xyz"
        if point.coordinates is not None:
            coords = np.asarray(point.coordinates, dtype=float)
            write_xyz(
                frame_path,
                coords,
                [str(symbol) for symbol in (point.symbols or [])],
                title=f"scan frame {index} target={point.progress:.4f}",
            )
            actual_values: dict[str, float] = {}
            for coordinate_item, coordinate_id in zip(scan_coordinates, coordinate_ids):
                try:
                    actual_values[coordinate_id] = _measure_scan_coordinate(coords, coordinate_item)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Scan coordinate computation failed for frame %d (%s): %s",
                        index,
                        coordinate_id,
                        exc,
                    )
                    actual_values[coordinate_id] = float("nan")
        else:
            actual_values = {coordinate_id: float("nan") for coordinate_id in coordinate_ids}
        target_values = {
            coordinate_id: float(
                point.coordinate_values.get(
                    coordinate_id,
                    _interpolated_coordinate_target(coordinate_item, index, total_points),
                )
            )
            for coordinate_item, coordinate_id in zip(scan_coordinates, coordinate_ids)
        }
        primary_id = coordinate_ids[0]
        target = target_values[primary_id]
        actual = actual_values[primary_id]
        unit = "angstrom" if coordinate.kind == "distance" else "degree"
        residuals: dict[str, float] = {}
        invalid_reasons: list[str] = []
        for coordinate_item, coordinate_id in zip(scan_coordinates, coordinate_ids):
            value = actual_values.get(coordinate_id)
            if value is None or not np.isfinite(value):
                residuals[coordinate_id] = float("nan")
                invalid_reasons.append(f"{coordinate_id}:unmeasured")
                continue
            residual = float(value - float(target_values[coordinate_id]))
            residuals[coordinate_id] = residual
            tolerance = float(resolved_tolerances.get(coordinate_item.kind, 0.01))
            if abs(residual) > tolerance:
                invalid_reasons.append(f"{coordinate_id}:residual_{residual:+.4f}")
        finite_residuals = [abs(r) for r in residuals.values() if np.isfinite(r)]
        frames.append(
            ScanFrame(
                index=index,
                target_coordinate=target,
                actual_coordinate=actual,
                coordinate_unit=unit,
                geometry_path=str(frame_path.relative_to(scan_dir)) if frame_path.exists() else "",
                scan_energy_hartree=point.energy_hartree,
                single_point_energy_hartree=None,
                optimization_converged=bool(point.success),
                single_point_status="pending",
                source_log="scan.out",
                target_coordinates=target_values,
                actual_coordinates=actual_values,
                constraint_residuals=residuals,
                constraint_residual_ok=not invalid_reasons,
                max_constraint_residual=max(finite_residuals) if finite_residuals else None,
                invalid_reasons=tuple(invalid_reasons),
            )
        )
        if reporter is not None:
            reporter.update_stage("extract_frames", completed=len(frames), total=total_points)
            reporter.set_live_metrics(
                [
                    LiveMetric(
                        key="frames_extracted",
                        label_key="live.frames_extracted",
                        value=f"{len(frames)} / {total_points}",
                        kind="count",
                        priority=100,
                    )
                ]
            )
    return frames


def _interpolated_coordinate_target(
    coordinate: ScanCoordinate,
    index: int,
    total_points: int,
) -> float:
    """Rebuild a coordinate target when a backend omitted its target ledger."""
    if coordinate.start is None or coordinate.end is None:
        return float(index / max(total_points - 1, 1))
    progress = index / max(total_points - 1, 1)
    return float(coordinate.start + progress * (coordinate.end - coordinate.start))


def _validate_functional_selection(
    selection_payload: dict[str, Any],
    coordinates: tuple[ScanCoordinate, ...],
    symbols: list[str],
    geometry: np.ndarray[Any, Any],
) -> dict[str, Any]:
    """Validate the optional generic selector and return normalized metadata."""
    payload = dict(selection_payload or {})
    raw_kind = payload.get("kind")
    if raw_kind is None and len(coordinates) == 1:
        return payload
    selection_kind = normalize_selection_kind(raw_kind or "double_bond_scan")
    raw_atoms = payload.get("atom_indices") or payload.get("atoms")
    if raw_atoms is None:
        raw_atoms = [atom for item in coordinates for atom in item.atoms]
    parsed: FunctionalAtomSelection = parse_functional_atom_selection(
        selection_kind,
        raw_atoms,
        symbols,
        geometry,
    )
    if selection_kind == "double_bond_scan":
        if len(coordinates) != 2 or any(item.kind != "distance" for item in coordinates):
            raise ValueError("double_bond_scan requires exactly two distance coordinates")
        expected_pairs = tuple(tuple(group) for group in parsed.groups)
        actual_pairs = tuple(tuple(item.atoms) for item in coordinates)
        if {frozenset(pair) for pair in expected_pairs} != {
            frozenset(pair) for pair in actual_pairs
        }:
            raise ValueError("double-bond scan coordinates must match the selected atom groups")
    else:
        expected_kind = {
            "bond_stretch": "distance",
            "angle": "angle",
            "dihedral": "dihedral",
        }[selection_kind]
        if len(coordinates) != 1 or coordinates[0].kind != expected_kind:
            raise ValueError(f"selection.kind='{selection_kind}' does not match coordinate")
        if tuple(coordinates[0].atoms) != parsed.atoms:
            raise ValueError("coordinate atoms must preserve the selected atom order")
    normalized = dict(payload)
    normalized.update(parsed.to_dict())
    return normalized


def _measure_scan_coordinate(coords: np.ndarray[Any, Any], coordinate: ScanCoordinate) -> float:
    """Measure the driven coordinate on one optimized frame."""
    if coordinate.kind == "distance":
        return float(GeometryUtils.calculate_distance(coords, *coordinate.atoms))
    if coordinate.kind == "angle":
        return float(GeometryUtils.calculate_angle(coords, *coordinate.atoms))
    if coordinate.kind == "dihedral":
        return float(GeometryUtils.calculate_dihedral(coords, *coordinate.atoms))
    raise ValueError(f"Unsupported scan coordinate kind: {coordinate.kind!r}")


# ── single points ──────────────────────────────────────────────────────

# SP-stage resource policy (2026-09-05 incident): the batch helper derives
# its worker count from ``resources.nproc`` while every ORCA job still
# claims the FULL task nproc via ``%pal`` — 16 workers × 16 ranks = 256 MPI
# processes on a 16-core host gridlocked in MPI bootstrap with zero output.
# Split the budget instead: cap per-job cores, derive workers from the
# remainder, hand the executor a reduced-nproc config.
_SP_MAX_NPROC_PER_JOB = 4
_SP_MAX_WORKERS = 8


def _sp_resource_plan(cfg: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Return ``(workers, sp_cfg)`` dividing the task nproc across SP workers."""
    resources = cfg.get("resources")
    resources = dict(resources) if isinstance(resources, dict) else {}
    task_nproc = int(resources.get("nproc") or 1)
    per_job = max(1, min(_SP_MAX_NPROC_PER_JOB, task_nproc))
    workers = max(1, min(task_nproc // per_job, _SP_MAX_WORKERS))
    sp_cfg = {**cfg, "resources": {**resources, "nproc": per_job}}
    return workers, sp_cfg


def _run_single_points(
    frames: list[ScanFrame],
    charge: int,
    multiplicity: int,
    sp_spec: SinglePointSpec,
    scan_dir: Path,
    cfg: dict[str, Any],
    reporter: ProgressReporter | None = None,
) -> None:
    """Run one cached, isolated single point per extracted scan frame."""
    if not sp_spec.enabled:
        for i, frame in enumerate(frames):
            frames[i] = replace(frame, single_point_status="skipped")
        return

    n_frames = len(frames)
    sp_callback: Callable[[int, int], None] | None = None
    on_frame_start: Callable[[str, int, int], None] | None = None
    if reporter is not None:
        done = 0
        current_frame: str | None = None
        reporter.set_live_metrics(
            [
                LiveMetric(
                    key="completed_total",
                    label_key="live.single_points",
                    value=f"0 / {n_frames}",
                    kind="count",
                    priority=100,
                )
            ]
        )

        def _on_frame_start(frame_id: str, _done_so_far: int, _total: int) -> None:
            nonlocal current_frame
            _, separator, raw_index = frame_id.rpartition("_")
            if not separator:
                return
            try:
                index = int(raw_index)
            except ValueError:
                return
            current_frame = f"Frame {index}"
            reporter.set_live_metrics(
                [
                    LiveMetric(
                        key="completed_total",
                        label_key="live.single_points",
                        value=f"{done} / {n_frames}",
                        kind="count",
                        priority=100,
                    ),
                    LiveMetric(
                        key="current_frame",
                        label_key="live.current_frame",
                        value=current_frame,
                        kind="text",
                        priority=90,
                    ),
                ]
            )

        def _sp_callback(completed: int, _total: int) -> None:
            nonlocal done
            done = completed
            reporter.update_stage("run_single_points", completed=done, total=n_frames)
            metrics = [
                LiveMetric(
                    key="completed_total",
                    label_key="live.single_points",
                    value=f"{done} / {n_frames}",
                    kind="count",
                    priority=100,
                )
            ]
            if current_frame is not None:
                metrics.append(
                    LiveMetric(
                        key="current_frame",
                        label_key="live.current_frame",
                        value=current_frame,
                        kind="text",
                        priority=90,
                    )
                )
            reporter.set_live_metrics(metrics)

        on_frame_start = _on_frame_start
        sp_callback = _sp_callback

    for frame in frames:
        if not frame.geometry_path:
            raise RuntimeError(
                f"scan frame {frame.index} has no geometry file; "
                "cannot run single points on an incomplete scan"
            )
    frame_paths = [scan_dir / frame.geometry_path for frame in frames]
    sp_workers, sp_cfg = _sp_resource_plan(cfg)
    result = BatchSinglePointExecutor(
        frames=frame_paths,
        method=sp_spec.method,
        backend_name=sp_spec.software,
        output_dir=scan_dir / "sp",
        basis=sp_spec.basis,
        charge=charge,
        multiplicity=multiplicity,
        frame_ids=[f"frame_{frame.index:03d}" for frame in frames],
        config=sp_cfg,
        max_workers=sp_workers,
        cache=sp_spec.resume,
        cache_profile="pes_scan",
        solvent_model=sp_spec.solvent_model,
        dispersion=sp_spec.dispersion,
        ri_approximation=sp_spec.ri_approximation,
        aux_j_basis=sp_spec.aux_j_basis,
        aux_c_basis=sp_spec.aux_c_basis,
        grid=sp_spec.grid,
        scf_convergence=sp_spec.scf_convergence,
        progress_callback=sp_callback,
        on_frame_start=on_frame_start,
    ).run()
    if reporter is not None:
        reporter.set_live_metrics(
            [
                LiveMetric(
                    key="completed_total",
                    label_key="live.single_points",
                    value=f"{n_frames} / {n_frames}",
                    kind="count",
                    priority=100,
                )
            ]
        )

    for i, frame in enumerate(frames):
        frame_result = result[f"frame_{frame.index:03d}"]
        frames[i] = replace(
            frame,
            single_point_energy_hartree=frame_result.energy_hartree,
            single_point_status=frame_result.status,
        )


# ── energy profile ─────────────────────────────────────────────────────


def _build_energy_profile(
    frames: list[ScanFrame],
    sp_spec: SinglePointSpec,
) -> EnergyProfile:
    """Build the curve — SP complete → SP; else scan energy."""
    if sp_spec.enabled:
        sp_energies = [frame.single_point_energy_hartree for frame in frames]
        completed = [value for value in sp_energies if value is not None]
        if completed and len(completed) == len(frames):
            energies = sp_energies
            energy_source = "single_point"
            sp_incomplete = False
        else:
            energies = [frame.scan_energy_hartree for frame in frames]
            energy_source = "scan"
            sp_incomplete = True
    else:
        energies = [frame.scan_energy_hartree for frame in frames]
        energy_source = "scan"
        sp_incomplete = False

    reference = next((value for value in energies if value is not None), None)
    reference_index = next(
        (index for index, value in enumerate(energies) if value is not None),
        0,
    )
    relative = [
        None
        if value is None or reference is None
        else (float(value) - float(reference)) * HARTREE_TO_KCAL
        for value in energies
    ]
    return EnergyProfile(
        energy_source=energy_source,
        unit="kcal/mol",
        reference_index=reference_index,
        relative_energies_kcal_mol=tuple(relative),
        raw_hartree=tuple(energies),
        sp_incomplete=sp_incomplete,
    )


# ── candidate recommendation ──────────────────────────────────────────


def _recommend_candidates(
    frames: list[ScanFrame],
    coordinate: ScanCoordinate,
    profile: EnergyProfile,
    cfg: dict[str, Any],
    scan_dir: Path,
    *,
    coordinates: tuple[ScanCoordinate, ...] | None = None,
    constraints_satisfied: bool = True,
    constraint_tolerance: float | None = None,
    max_constraint_residual: float | None = None,
) -> tuple[list[CandidateRecommendation], list[CandidateRecommendation], ScanQuality]:
    """Recommend TS/INT initial guesses.

    Frames whose driven coordinates drifted off the constraint targets are
    not on the prescribed reaction path (failed correctors in RPH terms);
    their energies do not represent the path and candidates are suppressed.
    """
    if not constraints_satisfied:
        quality = ScanQuality(
            status="invalid",
            scan_complete=len(frames) >= 3,
            sp_incomplete=profile.sp_incomplete,
            needs_review=True,
            notes=("constraint_residual_exceeded",),
            constraints_satisfied=False,
            constraint_tolerance=constraint_tolerance,
            max_constraint_residual=max_constraint_residual,
        )
        return [], [], quality
    if coordinate.kind != "distance":
        return _recommend_coordinate_candidates(
            frames,
            coordinate,
            profile,
            cfg,
            constraints_satisfied,
            constraint_tolerance,
            max_constraint_residual,
        )

    return _recommend_bond_candidates(
        frames,
        coordinate,
        profile,
        cfg,
        scan_dir,
        coordinates=coordinates,
        constraints_satisfied=constraints_satisfied,
        constraint_tolerance=constraint_tolerance,
        max_constraint_residual=max_constraint_residual,
    )


def _recommend_bond_candidates(
    frames: list[ScanFrame],
    coordinate: ScanCoordinate,
    profile: EnergyProfile,
    cfg: dict[str, Any],
    scan_dir: Path,
    *,
    coordinates: tuple[ScanCoordinate, ...] | None = None,
    constraints_satisfied: bool = True,
    constraint_tolerance: float | None = None,
    max_constraint_residual: float | None = None,
) -> tuple[list[CandidateRecommendation], list[CandidateRecommendation], ScanQuality]:
    """Recommend candidates for bond-length scans.

    Primary path: the full :func:`select_path_seeds` policy (endpoint guard,
    reaction-progress filter, knee detection, TS right shift, INT
    plateau/midpoint, barrier and scaffold gates).  When the policy defers
    (``resolution == "unresolved"``) the self-contained peak/minimum
    heuristic takes over and the rejection reason is recorded in the notes.
    """
    energies = list(profile.raw_hartree)
    notes: list[str] = []
    usable_energies = [value for value in energies if value is not None]
    if not usable_energies:
        notes.append("no_energies")
    if profile.sp_incomplete:
        notes.append("sp_incomplete_scan_energy_used")
    if any(not frame.optimization_converged for frame in frames):
        notes.append("non_converged_frames")

    policy = policy_from_config(_selection_config(cfg))
    selection = _select_distance_seeds(
        frames, coordinate, profile, scan_dir, policy, coordinates=coordinates
    )

    ts_recs: list[CandidateRecommendation] = []
    int_recs: list[CandidateRecommendation] = []
    policy_resolved = (
        selection is not None
        and selection.resolution != "unresolved"
        and selection.ts_search_seed is not None
    )
    if policy_resolved and selection is not None:
        notes.append("distance_selection_knee_shift_v1")
        ts_recs = _ts_recommendation_from_seed(selection, frames)
        int_recs = _int_recommendation_from_seed(selection, frames, notes)
    else:
        rejection_reason = None if selection is None else selection.rejection_reason
        notes.append(f"distance_selection_fallback:{rejection_reason or 'no_seed'}")
        ts_recs, int_recs = _fallback_bond_recommendations(frames, energies, notes)

    needs_review = False
    if any(rec.confidence == "low" for rec in ts_recs):
        needs_review = True
        notes.append("low_confidence_ts_candidate")
    if not policy_resolved:
        needs_review = True

    complete = len(frames) >= 3 and all(value is not None for value in energies)
    if not complete:
        needs_review = True
        notes.append("incomplete_scan")
    if profile.sp_incomplete:
        needs_review = True

    status = "needs_review" if needs_review else "ready_for_review"
    if not usable_energies:
        status = "partial"

    quality = ScanQuality(
        status=status,
        scan_complete=complete,
        sp_incomplete=profile.sp_incomplete,
        needs_review=needs_review,
        notes=tuple(dict.fromkeys(notes)),
        constraints_satisfied=constraints_satisfied,
        constraint_tolerance=constraint_tolerance,
        max_constraint_residual=max_constraint_residual,
    )
    return ts_recs, int_recs, quality


def _select_distance_seeds(
    frames: list[ScanFrame],
    coordinate: ScanCoordinate,
    profile: EnergyProfile,
    scan_dir: Path,
    policy: SelectionPolicy,
    *,
    coordinates: tuple[ScanCoordinate, ...] | None = None,
) -> SeedSelection | None:
    """Run the path-selection policy over the scan profile.

    Frame 0 (the scan-start/input geometry side) is treated as the reactant
    reference end, so the reactant-side tail is endpoint-excluded and the
    barrier is measured from the input geometry.  Returns ``None`` when the
    profile cannot be built (missing frames or reference geometry).

    For double-bond (multi-distance) scans every distance coordinate's atom
    pair is passed as a forming bond so the path analysis models the full
    concerted coordinate, not just the first bond.
    """
    if len(frames) < 3:
        return None
    product_xyz = scan_dir / "input.xyz"
    if not product_xyz.is_file():
        return None
    frame_paths: list[Path] = []
    for frame in frames:
        if not frame.geometry_path:
            return None
        frame_paths.append(scan_dir / frame.geometry_path)

    scan_coordinates = coordinates or (coordinate,)
    forming_bonds = [
        (int(pair[0]), int(pair[1]))
        for pair in (item.atoms[:2] for item in scan_coordinates if item.kind == "distance")
    ]
    if not forming_bonds:
        forming_bonds = [(int(coordinate.atoms[0]), int(coordinate.atoms[1]))]

    path_profile = build_orca_scan_profile(
        frames=frame_paths,
        energies_hartree=list(profile.raw_hartree),
        forming_bonds=forming_bonds,
        product_xyz=product_xyz,
        energy_source=str(profile.energy_source),
        endpoint_direction="start",
        source_provenance={
            "pipeline": "run_pes_scan",
            "coordinate": coordinate.to_dict(),
        },
    )
    return select_path_seeds(path_profile, policy)


def _ts_recommendation_from_seed(
    selection: SeedSelection,
    frames: list[ScanFrame],
) -> list[CandidateRecommendation]:
    seed = dict(selection.ts_search_seed or {})
    frame_index = int(seed.get("frame_index", -1))
    if frame_index < 0 or frame_index >= len(frames):
        return []
    frame = frames[frame_index]
    confidence = str(seed.get("confidence") or "low")
    diagnostics = dict(selection.diagnostics)
    evidence: dict[str, Any] = {
        "selection_algorithm": str(diagnostics.get("selection_algorithm") or ""),
        "selection_mode": str(seed.get("selection_mode") or ""),
        "peak_index": frame_index,
        "total_frames": len(frames),
        "knee_frame_index": diagnostics.get("knee_frame_index"),
        "knee_anchor_type": diagnostics.get("knee_anchor_type"),
        "energy_peak_index": diagnostics.get("energy_peak_index"),
        "ts_right_shift_applied_A": diagnostics.get("ts_right_shift_applied_A"),
        "barrier_from_reactant_kcal_mol": diagnostics.get("barrier_from_reactant_kcal_mol"),
        "knee_evidence": selection.knee_evidence,
        "endpoint_evidence": selection.endpoint_evidence,
        "seed_selection": selection.to_dict(),
    }
    anchor = str(diagnostics.get("knee_anchor_type") or "knee")
    return [
        CandidateRecommendation(
            candidate_id=f"ts_guess_{frame.index + 1:03d}",
            kind="ts",
            frame_index=frame.index,
            geometry_path=frame.geometry_path,
            score=_confidence_score(confidence),
            confidence=confidence,
            evidence=evidence,
            reason=f"Knee-shifted TS seed on scan profile ({anchor}) — "
            "suggest coarse TS optimization",
        )
    ]


_INT_CONFIDENCE_BY_MODE: dict[str, str] = {
    "stretch_plateau": "medium",
    "ts_to_effective_endpoint_midpoint": "low",
}


def _int_recommendation_from_seed(
    selection: SeedSelection,
    frames: list[ScanFrame],
    notes: list[str],
) -> list[CandidateRecommendation]:
    seed = dict(selection.int_search_seed or {})
    if not seed:
        return []
    if bool(seed.get("shared_with_ts")):
        notes.append("int_shared_ts_fallback")
        return []
    frame_index = int(seed.get("frame_index", -1))
    if frame_index < 0 or frame_index >= len(frames):
        return []
    frame = frames[frame_index]
    mode = str(seed.get("selection_mode") or "")
    confidence = _INT_CONFIDENCE_BY_MODE.get(mode, "low")
    evidence: dict[str, Any] = {
        "side": "right",
        "selection_mode": mode,
        "ts_index": dict(selection.diagnostics).get("ts_frame_index"),
        "seed_selection": selection.to_dict(),
    }
    reasons = {
        "stretch_plateau": "Energy plateau after TS on scan profile — "
        "suggest coarse minimum optimization",
        "ts_to_effective_endpoint_midpoint": "Midpoint seed between TS and scan endpoint — "
        "suggest coarse minimum optimization",
    }
    return [
        CandidateRecommendation(
            candidate_id=f"int_guess_{frame.index + 1:03d}",
            kind="intermediate",
            frame_index=frame.index,
            geometry_path=frame.geometry_path,
            score=_confidence_score(confidence),
            confidence=confidence,
            evidence=evidence,
            reason=reasons.get(mode, "Intermediate seed from path-selection policy"),
        )
    ]


def _fallback_bond_recommendations(
    frames: list[ScanFrame],
    energies: list[float | None],
    notes: list[str],
) -> tuple[list[CandidateRecommendation], list[CandidateRecommendation]]:
    """Peak + deepest-side-minimum heuristic for profiles the policy defers."""
    ts_recs: list[CandidateRecommendation] = []
    int_recs: list[CandidateRecommendation] = []

    energy_frames = [(i, e) for i, e in enumerate(energies) if e is not None]
    if not energy_frames:
        return ts_recs, int_recs

    peak_idx, peak_energy = max(energy_frames, key=lambda x: x[1])
    peak_frame = frames[peak_idx]
    confidence = "high"
    if peak_idx == 0 or peak_idx == len(frames) - 1:
        confidence = "low"
        notes.append("ts_at_endpoint")

    ts_recs.append(
        CandidateRecommendation(
            candidate_id=f"ts_guess_{peak_idx + 1:03d}",
            kind="ts",
            frame_index=peak_frame.index,
            geometry_path=peak_frame.geometry_path,
            score=_confidence_score(confidence),
            confidence=confidence,
            evidence={
                "peak_index": peak_idx,
                "peak_energy_hartree": peak_energy,
                "total_frames": len(frames),
            },
            reason="Highest energy frame on scan profile — suggest coarse TS optimization",
        )
    )
    if confidence == "low":
        notes.append("low_confidence_ts_candidate")

    left_min = _find_minimum(energy_frames[:peak_idx])
    right_min = _find_minimum(energy_frames[peak_idx + 1 :])
    for label, min_info in [("left", left_min), ("right", right_min)]:
        if min_info is None:
            continue
        min_idx, min_energy = min_info
        min_frame = frames[min_idx]
        depth = peak_energy - min_energy
        int_confidence = "medium" if depth > 0.001 else "low"
        int_recs.append(
            CandidateRecommendation(
                candidate_id=f"int_guess_{min_idx + 1:03d}",
                kind="intermediate",
                frame_index=min_frame.index,
                geometry_path=min_frame.geometry_path,
                score=_confidence_score(int_confidence),
                confidence=int_confidence,
                evidence={
                    "side": label,
                    "depth_hartree": depth,
                    "ts_index": peak_idx,
                },
                reason=(
                    f"Energy minimum on {label} side of TS — suggest coarse minimum optimization"
                ),
            )
        )
    return ts_recs, int_recs


def _find_minimum(
    energy_frames: list[tuple[int, float]],
) -> tuple[int, float] | None:
    if not energy_frames:
        return None
    return min(energy_frames, key=lambda x: x[1])


def _recommend_coordinate_candidates(
    frames: list[ScanFrame],
    coordinate: ScanCoordinate,
    profile: EnergyProfile,
    cfg: dict[str, Any],
    constraints_satisfied: bool = True,
    constraint_tolerance: float | None = None,
    max_constraint_residual: float | None = None,
) -> tuple[list[CandidateRecommendation], list[CandidateRecommendation], ScanQuality]:
    """Recommend extrema for angle/dihedral scans.

    Local-extrema heuristic ranked by prominence; candidate ids are rank
    based (``ts_guess_001`` is the most prominent peak) and the confidence
    thresholds come from the scan-selection config (kcal/mol, converted to
    Hartree).
    """
    energies = list(profile.raw_hartree)
    usable = [value for value in energies if value is not None]
    notes = ["coordinate_scan_extrema_heuristic"]
    selection_values = _selection_config(cfg)
    ts_min_hartree = (
        float(selection_values.get("ts_min_prominence_kcal_mol", 0.40)) / HARTREE_TO_KCAL
    )
    int_min_hartree = (
        float(selection_values.get("int_min_basin_prominence_kcal_mol", 0.50)) / HARTREE_TO_KCAL
    )
    ts_hits: list[tuple[int, float]] = []
    int_hits: list[tuple[int, float]] = []
    for index in range(1, len(frames) - 1):
        energy = energies[index]
        left = energies[index - 1]
        right = energies[index + 1]
        if energy is None or left is None or right is None:
            continue
        if energy > left and energy >= right:
            ts_hits.append((index, float(energy - max(left, right))))
        if energy < left and energy <= right:
            int_hits.append((index, float(min(left, right) - energy)))
    ts_hits.sort(key=lambda item: item[1], reverse=True)
    int_hits.sort(key=lambda item: item[1], reverse=True)
    ts_rows = [
        CandidateRecommendation(
            candidate_id=f"ts_guess_{rank:03d}",
            kind="ts",
            frame_index=frames[index].index,
            geometry_path=frames[index].geometry_path,
            score=prominence,
            confidence="medium" if prominence >= ts_min_hartree else "low",
            evidence={
                "coordinate_kind": coordinate.kind,
                "coordinate_value": frames[index].actual_coordinate,
                "prominence_hartree": prominence,
                "rank": rank,
            },
            reason=f"Local energy peak ({coordinate.kind} scan, rank {rank})",
        )
        for rank, (index, prominence) in enumerate(ts_hits[:5], start=1)
    ]
    int_rows = [
        CandidateRecommendation(
            candidate_id=f"int_guess_{rank:03d}",
            kind="intermediate",
            frame_index=frames[index].index,
            geometry_path=frames[index].geometry_path,
            score=depth,
            confidence="medium" if depth >= int_min_hartree else "low",
            evidence={
                "coordinate_kind": coordinate.kind,
                "coordinate_value": frames[index].actual_coordinate,
                "basin_depth_hartree": depth,
                "rank": rank,
            },
            reason=f"Local energy valley ({coordinate.kind} scan, rank {rank})",
        )
        for rank, (index, depth) in enumerate(int_hits[:5], start=1)
    ]
    if not usable:
        notes.append("no_energies")
    if profile.sp_incomplete:
        notes.append("sp_incomplete_scan_energy_used")
    if any(not frame.optimization_converged for frame in frames):
        notes.append("non_converged_frames")
    complete = len(frames) >= 3 and all(value is not None for value in energies)
    needs_review = True
    if not ts_rows and not int_rows:
        notes.append("no_coordinate_extrema")
    if not complete:
        notes.append("incomplete_scan")
    quality = ScanQuality(
        status="partial" if not usable else "needs_review",
        scan_complete=complete,
        sp_incomplete=profile.sp_incomplete,
        needs_review=needs_review,
        notes=tuple(dict.fromkeys(notes)),
        constraints_satisfied=constraints_satisfied,
        constraint_tolerance=constraint_tolerance,
        max_constraint_residual=max_constraint_residual,
    )
    return ts_rows, int_rows, quality


_SELECTION_DEFAULTS: dict[str, Any] = {
    "endpoint_exclusion_frames": 2,
    "ts_min_prominence_kcal_mol": 0.40,
    "int_min_basin_prominence_kcal_mol": 0.50,
    "min_reaction_progress": 0.30,
    "ts_right_shift_override_A": 0.10,
}


def _selection_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolve the seed-selection policy config for candidate selection.

    Reads ``pes.scan_selection`` (canonical) while still honouring the
    legacy ``step2.scan.selection`` block, then applies the PESsearch
    defaults.  The merged mapping is consumed by
    :func:`acp.calculations.pes.path_selection.policy_from_config`, which
    accepts both flat keys and nested policy blocks (``knee``, ``ts_seed``,
    ``int_plateau``, ``admission``).
    """
    legacy = dict((cfg.get("step2") or {}).get("scan") or {}).get("selection") or {}
    current = dict((cfg.get("pes") or {}).get("scan_selection") or {})
    merged = dict(_SELECTION_DEFAULTS)
    merged.update(dict(legacy))
    merged.update(dict(current))
    return merged


def _confidence_score(confidence: str) -> float:
    return {"high": 0.85, "medium": 0.65, "low": 0.4}.get(str(confidence).lower(), 0.4)


__all__ = [
    "PES_SCAN_STAGES",
    "SCAN_DIR_NAME",
    "build_coordinate_plan",
    "run_pes_scan",
]
