# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
"""Relaxed internal-coordinate scan calculation primitive."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Final

import numpy as np

from acp.backends.base import QCResult
from acp.calculations.contracts import (
    ArtifactRef,
    CalculationRequest,
    CalculationResult,
    JsonValue,
)
from acp.storage.manifest import ProductKind, ResultManifest
from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan
from cccp.qc.interfaces.xtb_scan import RelaxedScanResult
from cccp.utils import file_io

from ._common import (
    CalculationInputs,
    backend_for_request,
    backend_name,
    error_text,
    load_inputs,
    output_dir,
    result_from_qc,
)

logger = logging.getLogger(__name__)

_BACKEND_FAILURES = (OSError, RuntimeError, ValueError)
_SCAN_RESOURCE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "backend",
        "engine",
        "config",
        "output_dir",
        "result_dir",
        "coordinates",
        "symbols",
        "charge",
        "multiplicity",
        "method",
        "scan_coordinates",
        "coordinate",
        "scan_plan",
        "scan_points",
    }
)


class ScanCoordinateError(ValueError):
    """Raised when a scan coordinate cannot be compiled for the input geometry."""


def run_scan(req: CalculationRequest) -> CalculationResult:
    """Run a relaxed scan through the selected backend capability."""
    inputs = _load_scan_inputs(req)
    plan = _build_scan_plan(req)
    _validate_atom_indices(plan, len(inputs.symbols))

    selected_backend = backend_name(req)
    backend = backend_for_request(req, selected_backend)
    target_dir = output_dir(req) or Path.cwd() / "scan_work"
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        raw_result = backend.relaxed_scan(
            inputs.coordinates,
            list(inputs.symbols),
            output_dir=target_dir,
            plan=plan,
            charge=inputs.charge,
            multiplicity=inputs.multiplicity,
            **_scan_kwargs(req),
        )
    except _BACKEND_FAILURES as error:
        return result_from_qc(
            req,
            selected_backend,
            None,
            [error_text(error)],
            [],
            metadata=_plan_metadata(plan),
            status="failed",
        )

    if not isinstance(raw_result, RelaxedScanResult):
        return result_from_qc(
            req,
            selected_backend,
            _as_qc_result(raw_result),
            ["relaxed_scan returned an unsupported result type"],
            [],
            metadata=_plan_metadata(plan),
            status="failed",
        )

    artifacts = _write_scan_products(
        req,
        selected_backend,
        target_dir,
        plan,
        raw_result,
        inputs,
    )
    complete = _scan_completed(raw_result, plan)
    error_message = raw_result.message or "relaxed scan failed"
    errors = [] if complete else [error_message]
    best = raw_result.best_point()
    qc_result = QCResult(
        success=complete,
        energy=best.energy_hartree if best is not None else None,
        coordinates=best.coordinates if best is not None else None,
        symbols=best.symbols if best is not None else list(inputs.symbols),
        converged=complete,
    )
    metadata = _plan_metadata(plan)
    metadata["frame_count"] = len(raw_result.points)
    metadata["successful_frame_count"] = sum(
        point.success and point.coordinates is not None for point in raw_result.points
    )
    return result_from_qc(
        req,
        selected_backend,
        qc_result,
        errors,
        artifacts,
        metadata=metadata,
        status="completed" if complete else "failed",
    )


def _load_scan_inputs(request: CalculationRequest) -> CalculationInputs:
    """Load geometry while keeping coordinate definitions out of the input loader."""
    raw_coordinates = request.resources.get("coordinates")
    if raw_coordinates is None or _is_geometry_matrix(raw_coordinates):
        return load_inputs(request)

    resources = dict(request.resources)
    _ = resources.pop("coordinates", None)
    input_request = CalculationRequest(
        input_artifact=request.input_artifact,
        method=request.method,
        resources=resources,
        workflow=request.workflow,
        profile=request.profile,
    )
    return load_inputs(input_request)


def _is_geometry_matrix(value: JsonValue) -> bool:
    """Return whether a JSON value has the geometry shape ``list[list[number]]``."""
    if not isinstance(value, list) or not value:
        return False
    return all(
        isinstance(row, list)
        and len(row) == 3
        and all(
            isinstance(component, (int, float)) and not isinstance(component, bool)
            for component in row
        )
        for row in value
    )


def _build_scan_plan(request: CalculationRequest) -> ReactionCoordinatePlan:
    """Compile request coordinate values into a validated reaction-coordinate plan."""
    raw_plan = request.resources.get("scan_plan")
    if isinstance(raw_plan, dict):
        try:
            payload: dict[str, object] = {key: value for key, value in raw_plan.items()}
            plan = ReactionCoordinatePlan.from_dict(payload)
        except (TypeError, ValueError) as error:
            raise ScanCoordinateError(str(error)) from error
        raw_points = request.resources.get("scan_points")
        if raw_points is not None:
            points = _parse_points(raw_points)
            if points != plan.points:
                plan = ReactionCoordinatePlan(
                    coordinates=plan.coordinates,
                    points=points,
                    coupling=plan.coupling,
                    start_from=plan.start_from,
                )
        return plan

    raw_values = request.resources.get("scan_coordinates")
    if raw_values is None:
        raw_values = request.resources.get("coordinate")
    if raw_values is None:
        raw_values = request.resources.get("coordinates")
    values = _coordinate_values(raw_values)
    coordinates = tuple(_parse_coordinate(value, index) for index, value in enumerate(values))
    points = _parse_points(request.resources.get("scan_points", 21))
    try:
        return ReactionCoordinatePlan(coordinates=coordinates, points=points)
    except ValueError as error:
        raise ScanCoordinateError(str(error)) from error


def _coordinate_values(value: JsonValue | None) -> list[JsonValue]:
    """Normalize one coordinate value or a list of coordinate values."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and value and all(isinstance(item, (str, dict)) for item in value):
        return value
    raise ScanCoordinateError("scan requires at least one coordinate in atom1,atom2,start,end form")


def _parse_coordinate(value: JsonValue, index: int) -> CoordinateSpec:
    """Parse one CLI coordinate or JSON coordinate definition."""
    if isinstance(value, str):
        fields = [field.strip() for field in value.split(",")]
        if len(fields) != 4:
            raise ScanCoordinateError(f"coordinate {index + 1} must be atom1,atom2,start,end")
        atom_values = (_parse_atom(fields[0], index), _parse_atom(fields[1], index))
        start = _parse_float(fields[2], index, "start")
        end = _parse_float(fields[3], index, "end")
        return CoordinateSpec(
            id=f"rc{index + 1}",
            kind="distance",
            atoms=atom_values,
            start=start,
            end=end,
        )
    if isinstance(value, dict):
        payload: dict[str, object] = {key: item for key, item in value.items()}
        try:
            return CoordinateSpec.from_dict(payload)
        except (TypeError, ValueError) as error:
            raise ScanCoordinateError(str(error)) from error
    raise ScanCoordinateError(f"coordinate {index + 1} must be a string or object")


def _parse_atom(value: str, coordinate_index: int) -> int:
    """Parse one non-negative, zero-based atom index."""
    try:
        atom = int(value)
    except ValueError as error:
        raise ScanCoordinateError(
            f"coordinate {coordinate_index + 1} atom index must be an integer: {value!r}"
        ) from error
    if atom < 0:
        raise ScanCoordinateError(
            f"coordinate {coordinate_index + 1} atom index must be non-negative: {atom}"
        )
    return atom


def _parse_float(value: str, coordinate_index: int, label: str) -> float:
    """Parse one finite coordinate endpoint."""
    try:
        number = float(value)
    except ValueError as error:
        raise ScanCoordinateError(
            f"coordinate {coordinate_index + 1} {label} value must be numeric: {value!r}"
        ) from error
    if not math.isfinite(number):
        raise ScanCoordinateError(f"coordinate {coordinate_index + 1} {label} value must be finite")
    return number


def _parse_points(value: JsonValue) -> int:
    """Parse the number of scan frames."""
    if isinstance(value, bool):
        raise ScanCoordinateError("scan_points must be an integer >= 2")
    if not isinstance(value, (str, int, float)):
        raise ScanCoordinateError("scan_points must be an integer >= 2")
    try:
        points = int(value)
    except (TypeError, ValueError) as error:
        raise ScanCoordinateError("scan_points must be an integer >= 2") from error
    if points < 2:
        raise ScanCoordinateError("scan_points must be an integer >= 2")
    return points


def _validate_atom_indices(plan: ReactionCoordinatePlan, atom_count: int) -> None:
    """Reject coordinates whose zero-based atom indices are absent from the input."""
    for coordinate in plan.coordinates:
        for atom in coordinate.atoms:
            if atom >= atom_count:
                message = (
                    f"atom index {atom} is out of range for {atom_count} atoms; "
                    + f"原子索引 {atom} 超出输入分子原子数 {atom_count}"
                )
                raise ScanCoordinateError(message)


def _scan_kwargs(request: CalculationRequest) -> dict[str, JsonValue]:
    """Forward backend-specific scan options without leaking request internals."""
    kwargs = {
        key: value for key, value in request.resources.items() if key not in _SCAN_RESOURCE_KEYS
    }
    if request.method:
        kwargs["method"] = request.method
    return kwargs


def _result_dir(request: CalculationRequest, target_dir: Path) -> Path:
    """Resolve the task RESULT directory from an explicit path or WORK layout."""
    raw_result_dir = request.resources.get("result_dir")
    if isinstance(raw_result_dir, str) and raw_result_dir:
        return Path(raw_result_dir)
    for parent in (target_dir, *target_dir.parents):
        if parent.name == "RESULT":
            return parent
        if parent.name == "WORK":
            return parent.parent / "RESULT"
    return target_dir / "RESULT"


def _write_scan_products(
    request: CalculationRequest,
    backend: str,
    target_dir: Path,
    plan: ReactionCoordinatePlan,
    scan_result: RelaxedScanResult,
    inputs: CalculationInputs,
) -> list[ArtifactRef]:
    """Persist scan frames and the trajectory descriptor, then register products."""
    result_dir = _result_dir(request, target_dir)
    structures_dir = result_dir / "structures"
    trajectories_dir = result_dir / "trajectories"
    structures_dir.mkdir(parents=True, exist_ok=True)
    trajectories_dir.mkdir(parents=True, exist_ok=True)

    artifacts: list[ArtifactRef] = []
    frame_payloads: list[JsonValue] = []
    manifest = ResultManifest(
        task_id="",
        workflow=request.workflow or "scan",
        status="completed" if _scan_completed(scan_result, plan) else "failed",
    )
    for point in scan_result.points:
        if not point.success or point.coordinates is None:
            continue
        symbols = point.symbols or list(inputs.symbols)
        frame_path = structures_dir / f"scan_frame_{point.frame_index:03d}.xyz"
        if point.energy_hartree is not None:
            file_io.write_xyz(
                frame_path,
                np.asarray(point.coordinates, dtype=float),
                symbols,
                title=f"scan frame {point.frame_index}",
                energy=point.energy_hartree,
            )
        else:
            file_io.write_xyz(
                frame_path,
                np.asarray(point.coordinates, dtype=float),
                symbols,
                title=f"scan frame {point.frame_index}",
            )
        relative_path = str(frame_path.relative_to(result_dir))
        artifacts.append(ArtifactRef(path=frame_path, type="structure", source=backend))
        _ = manifest.add_product(
            id=f"scan_frame_{point.frame_index:03d}",
            label=f"Scan frame {point.frame_index}",
            path=relative_path,
            kind=ProductKind.STRUCTURE,
        )
        frame_payloads.append(
            {
                "index": point.frame_index,
                "path": relative_path,
                "progress": point.progress,
                "energy_hartree": point.energy_hartree,
                "coordinate_values": dict(point.coordinate_values),
            }
        )

    trajectory_path = trajectories_dir / "scan_trajectory.json"
    trajectory_payload: dict[str, JsonValue] = {
        "workflow": request.workflow or "scan",
        "frame_count": len(scan_result.points),
        "successful_frame_count": len(frame_payloads),
        "points": plan.points,
        "frames": frame_payloads,
    }
    _ = trajectory_path.write_text(
        json.dumps(trajectory_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    trajectory_relative_path = str(trajectory_path.relative_to(result_dir))
    artifacts.append(ArtifactRef(path=trajectory_path, type="trajectory", source=backend))
    _ = manifest.add_product(
        id="scan_trajectory",
        label="Relaxed scan trajectory",
        path=trajectory_relative_path,
        kind=ProductKind.TRAJECTORY,
    )
    _ = manifest.write(result_dir)
    return artifacts


def _scan_completed(result: RelaxedScanResult, plan: ReactionCoordinatePlan) -> bool:
    """Return whether every requested scan frame has a usable geometry."""
    return (
        result.success
        and len(result.points) == plan.points
        and all(point.success and point.coordinates is not None for point in result.points)
    )


def _plan_metadata(plan: ReactionCoordinatePlan) -> dict[str, JsonValue]:
    """Serialize stable plan metadata for the unified calculation result."""
    return {
        "scan_points": plan.points,
        "scan_coordinates": [
            {
                "id": coordinate.id,
                "kind": coordinate.kind,
                "atoms": list(coordinate.atoms),
                "role": coordinate.role,
                "start": coordinate.start,
                "end": coordinate.end,
            }
            for coordinate in plan.coordinates
        ],
    }


def _as_qc_result(value: QCResult | RelaxedScanResult) -> QCResult | None:
    """Keep a legacy QC result available when a test backend returns one."""
    return value if isinstance(value, QCResult) else None


__all__ = ["ScanCoordinateError", "run_scan"]
