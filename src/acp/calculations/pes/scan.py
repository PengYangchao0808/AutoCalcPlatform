"""PES relaxed-scan core — migrated from mechanism/bond_scan.py.

Implements the scan pipeline:

    prepare → validate_coordinate → compile_plan → run_relaxed_scan →
    extract_frames → run_single_points → build_energy_profile →
    recommend_candidates

Key migration changes vs bond_scan.py:
- Uses ``get_backend("orca").relaxed_scan`` (not ``ORCAInterface`` directly)
- De-s2 naming (PesScanRequest, no study_id)
- Single-point delegation via BatchSinglePointExecutor (todo 31)
- Result manifest via ``acp.storage.manifest.ResultManifest``
"""

# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnnecessaryIsInstance=false, reportUnusedCallResult=false, reportUnusedFunction=false, reportUnusedParameter=false

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, TypeGuard

import numpy as np

import acp.backends
from acp.backends.base import RelaxedScanCalculator
from acp.calculations.batch.singlepoint import BatchSinglePointExecutor
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
    validate_scan_protocol,
)
from cccp.qc.interfaces.constraints import ConstraintKind, CoordinateSpec
from cccp.qc.interfaces.xtb_scan import RelaxedScanResult
from cccp.utils.constants import HARTREE_TO_KCAL
from cccp.utils.file_io import read_xyz, write_xyz
from cccp.utils.geometry_tools import GeometryUtils

logger = logging.getLogger(__name__)

SCAN_DIR_NAME = "pes_scan_001"
PES_SCAN_STAGES = (
    "prepare",
    "validate_coordinate",
    "compile_plan",
    "run_relaxed_scan",
    "extract_frames",
    "run_single_points",
    "build_energy_profile",
    "recommend_candidates",
)


# ── coordinate plan ────────────────────────────────────────────────────


def build_coordinate_plan(coordinate: ScanCoordinate) -> CoordinateSpec:
    """Build a 0-based :class:`CoordinateSpec` from a :class:`ScanCoordinate`."""
    kind = coordinate.kind
    if not _is_constraint_kind(kind):
        raise ValueError(f"Unsupported scan coordinate kind: {kind!r}")
    return CoordinateSpec(
        id=kind,
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
) -> dict[str, Any]:
    """Run a one-dimensional PES relaxed scan.

    Args:
        request: PesScanRequest payload or dict.
        output_dir: Task output root.
        config: Merged ACP config dict.

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

    cfg = config or {}
    out_root = Path(output_dir).resolve()
    work_root = out_root / "WORK"
    scan_dir = work_root / "02_SEARCH" / SCAN_DIR_NAME
    scan_dir.mkdir(parents=True, exist_ok=True)

    # Validate
    validate_scan_protocol(req.coordinate, req.protocol)
    coordinate = req.coordinate
    protocol = req.protocol

    # Materialise structure
    coords, symbols, charge, multiplicity = _materialize_structure(req.source, work_root, cfg)
    for index in coordinate.atoms:
        if index < 0 or index >= len(symbols):
            raise ValueError(
                f"Scan atom index {index} is out of range (structure has {len(symbols)} atoms)"
            )

    input_xyz = scan_dir / "input.xyz"
    write_xyz(input_xyz, coords, symbols, title="PES scan input")

    # Run relaxed scan via backend
    scan_result = _run_relaxed_scan_backend(
        coords=coords,
        symbols=symbols,
        charge=charge,
        multiplicity=multiplicity,
        coordinate=coordinate,
        protocol=protocol,
        scan_dir=scan_dir,
        cfg=cfg,
    )
    if not scan_result.success and not scan_result.points:
        raise RuntimeError(f"Relaxed scan failed: {scan_result.message}")

    # Extract frames
    frames = _extract_frames(scan_result, coordinate, scan_dir)

    # Run single points (delegates to BatchSinglePointExecutor when available)
    sp_spec = protocol.single_point
    _run_single_points(frames, charge, multiplicity, sp_spec, scan_dir, cfg)

    # Build energy profile
    profile = _build_energy_profile(frames, sp_spec)

    # Recommend candidates
    ts_recs, int_recs, quality = _recommend_candidates(frames, coordinate, profile, cfg)

    return {
        "frames": [f.to_dict() for f in frames],
        "profile": profile.to_dict(),
        "quality": quality.to_dict(),
        "ts_recommendations": [r.to_dict() for r in ts_recs],
        "int_recommendations": [r.to_dict() for r in int_recs],
        "coordinate": coordinate.to_dict(),
        "protocol": protocol.to_dict(),
        "scan_dir": str(scan_dir),
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
    coordinate: ScanCoordinate,
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
    spec = build_coordinate_plan(coordinate)
    nproc = int((cfg.get("resources") or {}).get("nproc") or 1)
    result = backend.relaxed_scan(
        coords,
        symbols,
        output_dir=scan_dir,
        plan=spec,
        charge=charge,
        multiplicity=multiplicity,
        method=protocol.scan_optimizer.method,
        points=coordinate.n_points,
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
) -> list[ScanFrame]:
    """Build per-frame records from the scan result."""
    frames: list[ScanFrame] = []
    frames_dir = scan_dir / "scan_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
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
            try:
                actual = _measure_scan_coordinate(coords, coordinate)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Scan coordinate computation failed for frame %d: %s", index, exc)
                actual = float("nan")
        else:
            actual = float("nan")
        target = float(point.coordinate_values.get(coordinate.kind, point.progress))
        unit = "angstrom" if coordinate.kind == "distance" else "degree"
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
            )
        )
    return frames


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


def _run_single_points(
    frames: list[ScanFrame],
    charge: int,
    multiplicity: int,
    sp_spec: SinglePointSpec,
    scan_dir: Path,
    cfg: dict[str, Any],
) -> None:
    """Run one cached, isolated single point per extracted scan frame."""
    if not sp_spec.enabled:
        for i, frame in enumerate(frames):
            frames[i] = replace(frame, single_point_status="skipped")
        return

    frame_paths = [scan_dir / frame.geometry_path for frame in frames]
    result = BatchSinglePointExecutor(
        frames=frame_paths,
        method=sp_spec.method,
        backend_name=sp_spec.software,
        output_dir=scan_dir / "sp",
        basis=sp_spec.basis,
        charge=charge,
        multiplicity=multiplicity,
        frame_ids=[f"frame_{frame.index:03d}" for frame in frames],
        config=cfg,
        cache=sp_spec.resume,
        cache_profile="pes_scan",
        solvent_model=sp_spec.solvent_model,
        dispersion=sp_spec.dispersion,
        ri_approximation=sp_spec.ri_approximation,
        aux_j_basis=sp_spec.aux_j_basis,
        aux_c_basis=sp_spec.aux_c_basis,
        grid=sp_spec.grid,
        scf_convergence=sp_spec.scf_convergence,
    ).run()

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
) -> tuple[list[CandidateRecommendation], list[CandidateRecommendation], ScanQuality]:
    """Recommend TS/INT initial guesses."""
    if coordinate.kind != "distance":
        return _recommend_coordinate_candidates(frames, coordinate, profile)

    return _recommend_bond_candidates(frames, coordinate, profile, cfg)


def _recommend_bond_candidates(
    frames: list[ScanFrame],
    coordinate: ScanCoordinate,
    profile: EnergyProfile,
    cfg: dict[str, Any],
) -> tuple[list[CandidateRecommendation], list[CandidateRecommendation], ScanQuality]:
    """Recommend candidates for bond-length scans.

    Self-contained implementation (no mechanism imports).  Identifies the
    highest-energy frame as the TS guess and the deepest minimum on each
    side as intermediate guesses.
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

    ts_recs: list[CandidateRecommendation] = []
    int_recs: list[CandidateRecommendation] = []
    needs_review = False

    # Find TS: highest energy frame (excluding None)
    energy_frames = [(i, e) for i, e in enumerate(energies) if e is not None]
    if energy_frames:
        peak_idx, peak_energy = max(energy_frames, key=lambda x: x[1])
        peak_frame = frames[peak_idx]
        confidence = "high"
        if peak_idx == 0 or peak_idx == len(frames) - 1:
            confidence = "low"
            needs_review = True
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
                reason="Highest energy frame on scan profile — suggest S3 TS opt",
            )
        )
        if confidence == "low":
            needs_review = True
            notes.append("low_confidence_ts_candidate")

        # Find intermediates: deepest minima on each side of the peak
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
                    reason=f"Energy minimum on {label} side of TS — suggest S3 minimum opt",
                )
            )

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
    )
    return ts_recs, int_recs, quality


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
) -> tuple[list[CandidateRecommendation], list[CandidateRecommendation], ScanQuality]:
    """Recommend extrema for angle/dihedral scans."""
    energies = list(profile.raw_hartree)
    usable = [value for value in energies if value is not None]
    notes = ["coordinate_scan_extrema_heuristic"]
    ts_rows: list[CandidateRecommendation] = []
    int_rows: list[CandidateRecommendation] = []
    for index in range(1, len(frames) - 1):
        energy = energies[index]
        left = energies[index - 1]
        right = energies[index + 1]
        if energy is None or left is None or right is None:
            continue
        if energy > left and energy >= right:
            prominence = float(energy - max(left, right))
            ts_rows.append(
                CandidateRecommendation(
                    candidate_id=f"ts_guess_{len(ts_rows) + 1:03d}",
                    kind="ts",
                    frame_index=frames[index].index,
                    geometry_path=frames[index].geometry_path,
                    score=prominence,
                    confidence="medium" if prominence >= 0.0005 else "low",
                    evidence={
                        "coordinate_kind": coordinate.kind,
                        "coordinate_value": frames[index].actual_coordinate,
                        "prominence_hartree": prominence,
                    },
                    reason=f"Local energy peak ({coordinate.kind} scan)",
                )
            )
        if energy < left and energy <= right:
            depth = float(min(left, right) - energy)
            int_rows.append(
                CandidateRecommendation(
                    candidate_id=f"int_guess_{len(int_rows) + 1:03d}",
                    kind="intermediate",
                    frame_index=frames[index].index,
                    geometry_path=frames[index].geometry_path,
                    score=depth,
                    confidence="medium" if depth >= 0.0005 else "low",
                    evidence={
                        "coordinate_kind": coordinate.kind,
                        "coordinate_value": frames[index].actual_coordinate,
                        "basin_depth_hartree": depth,
                    },
                    reason=f"Local energy valley ({coordinate.kind} scan)",
                )
            )
    ts_rows.sort(key=lambda row: row.score, reverse=True)
    int_rows.sort(key=lambda row: row.score, reverse=True)
    ts_rows = ts_rows[:5]
    int_rows = int_rows[:5]
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
    )
    return ts_rows, int_rows, quality


def _selection_config(cfg: dict[str, Any]) -> dict[str, Any]:
    scan_selection = dict((cfg.get("step2") or {}).get("scan") or {})
    base = dict(scan_selection.get("selection") or {})
    base.setdefault("endpoint_exclusion_frames", 2)
    base.setdefault("ts_min_prominence_kcal_mol", 0.40)
    base.setdefault("int_min_basin_prominence_kcal_mol", 0.50)
    base.setdefault("min_reaction_progress", 0.30)
    base.setdefault("ts_right_shift_override_A", 0.10)
    return base


def _confidence_score(confidence: str) -> float:
    return {"high": 0.85, "medium": 0.65, "low": 0.4}.get(str(confidence).lower(), 0.4)


__all__ = [
    "PES_SCAN_STAGES",
    "SCAN_DIR_NAME",
    "build_coordinate_plan",
    "run_pes_scan",
]
