# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Relaxed internal-coordinate scan — ACP compat wrapper (plan todo 20).

The multi-point relaxed-scan task core lives in
:mod:`cccp.calculation.tasks.scan`: plan compilation
(``ScanOptions`` → ``ReactionCoordinatePlan``), the per-point relaxed
optimisation loop and the typed ``ScanPayload`` (frames keep their ORIGINAL
index; ``frame_geometry``/``scan_profile`` scientific artifacts).  This
module is the ACP-side compat surface: legacy ``CalculationRequest`` →
typed ``TaskRequest`` (``acp.calculations.legacy_adapters``), the legacy
backend registry seam, and the **publication half** — ``RESULT/structures``
frame products, the ``RESULT/trajectories/scan_trajectory.json`` view
product and ``result_manifest.json`` registration (``structures`` /
``trajectories`` products) stay ACP-side.  ``run_scan`` is a pure forwarder
(the dual-root uniqueness guard classifies it as a shim).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from acp.calculations.contracts import (
    ArtifactRef,
    CalculationRequest,
    CalculationResult,
    JsonValue,
)
from acp.calculations.legacy_adapters import to_legacy_result, to_task_request
from acp.calculations.primitives._common import (
    backend_for_request,
    backend_name,
    capability_kwargs,
    output_dir,
)
from acp.calculations.result_publication import register_result_manifest
from acp.storage.manifest import ProductKind, ResultManifest
from cccp.calculation.context import TaskContext
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import ScanOptions, TaskKind
from cccp.calculation.results import ScanPayload, TaskResult
from cccp.calculation.tasks.scan import (
    build_scan_plan,
    plan_metadata,
)
from cccp.calculation.tasks.scan import (
    run_scan as _cccp_run_scan,
)
from cccp.qc.interfaces.constraints import ReactionCoordinatePlan

logger = logging.getLogger(__name__)

__all__ = ["ScanCoordinateError", "run_scan"]

#: Compat alias for the frozen golden generator (plan metadata serialiser).
_plan_metadata = plan_metadata


class ScanCoordinateError(ValueError):
    """Raised when a scan coordinate cannot be compiled for the input geometry."""


def run_scan(req: CalculationRequest) -> CalculationResult:
    """Run a relaxed scan through the cccp task core."""
    return execute_scan(req)


def execute_scan(req: CalculationRequest) -> CalculationResult:
    """ACP compat wrapper: cccp task core + product publication.

    Verbatim legacy capability kwargs ride along as ``capability_extras``
    (translation cleanup: plan todo 25).  Coordinate/plan validation errors
    surface as :class:`ScanCoordinateError` (the CLI usage-error seam); the
    ``structures``/``trajectories`` platform products are materialised from
    the typed payload and registered through the named publication entry.
    """
    try:
        task_request, binding = to_task_request(req, TaskKind.SCAN)
    except TaskInputError as error:
        raise ScanCoordinateError(str(error)) from error
    selected_backend = backend_name(req)
    backend = backend_for_request(req, selected_backend)
    target_dir = output_dir(req) or Path.cwd() / "scan_work"
    target_dir.mkdir(parents=True, exist_ok=True)

    context = TaskContext(
        config=binding.config,
        workdir=binding.artifact_root or target_dir,
        backend=backend,
        capability_extras=capability_kwargs(req),
    )
    try:
        task_result = _cccp_run_scan(task_request, context=context)
    except TaskInputError as error:
        raise ScanCoordinateError(str(error)) from error

    legacy = to_legacy_result(task_result, binding)
    metadata = dict(task_result.metadata)
    metadata.update(legacy.metadata)
    published = _publish_scan_products(
        req,
        task_result,
        target_dir,
        selected_backend,
        metadata,
    )
    return CalculationResult(
        energy=legacy.energy,
        coords=legacy.coords,
        frequencies=legacy.frequencies,
        artifacts=published,
        status=legacy.status,
        errors=legacy.errors,
        provenance=legacy.provenance,
        metadata=metadata,
    )


def _build_scan_plan(request: CalculationRequest) -> ReactionCoordinatePlan:
    """Compile request coordinate resources into a validated scan plan.

    Thin adapter over the cccp plan compiler (kept importable for the frozen
    golden generator); coordinate errors surface as ``ScanCoordinateError``.
    """
    try:
        task_request, _binding = to_task_request(request, TaskKind.SCAN)
        raw_plan = request.resources.get("scan_plan")
        options = task_request.options if isinstance(task_request.options, ScanOptions) else None
        return build_scan_plan(
            options,
            raw_plan=raw_plan if isinstance(raw_plan, dict) else None,
        )
    except TaskInputError as error:
        raise ScanCoordinateError(str(error)) from error


# ── ACP-side product publication (frame/profile science comes from cccp) ──


def _publish_scan_products(
    request: CalculationRequest,
    task_result: TaskResult,
    target_dir: Path,
    backend: str,
    metadata: dict[str, JsonValue],
) -> list[ArtifactRef]:
    """Materialise ``structures``/``trajectories`` products and register them.

    The task wrote the frame geometries (``frame_geometry``) and the energy
    profile (``scan_profile``) as scientific artifacts; this function copies
    the frames under ``RESULT/structures``, writes the
    ``scan_trajectory.json`` view product under ``RESULT/trajectories`` and
    registers both product kinds in ``RESULT/result_manifest.json`` through
    :func:`acp.calculations.result_publication.register_result_manifest`.
    Never raises: a missing/partial payload just skips publication (the
    scan step is never failed due to publication alone).
    """
    payload = task_result.payload
    if not isinstance(payload, ScanPayload) or not payload.frames:
        return []
    try:
        return _publish_scan_products_unchecked(
            request, task_result, payload, target_dir, backend, metadata
        )
    except Exception:  # noqa: BLE001 — publication never fails the step
        logger.debug("scan: product publication failed; skipping", exc_info=True)
        return []


def _publish_scan_products_unchecked(
    request: CalculationRequest,
    task_result: TaskResult,
    payload: ScanPayload,
    target_dir: Path,
    backend: str,
    metadata: dict[str, JsonValue],
) -> list[ArtifactRef]:
    result_dir = _result_dir(request, target_dir)
    structures_dir = result_dir / "structures"
    trajectories_dir = result_dir / "trajectories"
    structures_dir.mkdir(parents=True, exist_ok=True)
    trajectories_dir.mkdir(parents=True, exist_ok=True)

    profile_records = {
        int(record.get("index", -1)): record
        for record in _profile_frames(payload, target_dir)
        if isinstance(record, dict)
    }
    plan_points = metadata.get("scan_points")
    points = int(plan_points) if isinstance(plan_points, int) else len(payload.frames)

    artifacts: list[ArtifactRef] = []
    frame_payloads: list[JsonValue] = []
    manifest = ResultManifest(
        task_id="",
        workflow=request.workflow or "scan",
        status=task_result.status,
    )
    for frame in payload.frames:
        if not frame.success or frame.geometry_ref is None:
            continue
        source = _resolve_artifact(target_dir, frame.geometry_ref)
        if not source.is_file():
            logger.debug("scan: frame geometry missing at %s; skipping", source)
            continue
        frame_path = structures_dir / f"scan_frame_{frame.index:03d}.xyz"
        _atomic_copy(source, frame_path)
        relative_path = str(frame_path.relative_to(result_dir))
        artifacts.append(ArtifactRef(path=frame_path, type="structure", source=backend))
        _ = manifest.add_product(
            id=f"scan_frame_{frame.index:03d}",
            label=f"Scan frame {frame.index}",
            path=relative_path,
            kind=ProductKind.STRUCTURE,
        )
        record = profile_records.get(frame.index, {})
        frame_payloads.append(
            {
                "index": frame.index,
                "path": relative_path,
                "progress": _frame_progress(record, frame, points),
                "energy_hartree": frame.energy_hartree,
                "coordinate_values": _frame_coordinate_values(record, frame, metadata),
            }
        )

    trajectory_path = trajectories_dir / "scan_trajectory.json"
    trajectory_payload: dict[str, JsonValue] = {
        "workflow": request.workflow or "scan",
        "frame_count": len(payload.frames),
        "successful_frame_count": len(frame_payloads),
        "points": points,
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
    _ = register_result_manifest(result_dir, manifest)
    return artifacts


def _profile_frames(payload: ScanPayload, target_dir: Path) -> list[object]:
    """Per-frame records from the task's ``scan_profile`` artifact (best effort)."""
    if payload.profile_ref is None:
        return []
    profile_path = _resolve_artifact(target_dir, payload.profile_ref)
    try:
        document = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    frames = document.get("frames") if isinstance(document, dict) else None
    return list(frames) if isinstance(frames, list) else []


def _frame_progress(record: object, frame: Any, points: int) -> float:
    """λ for one frame: the profile's verbatim progress, else the λ rule."""
    if isinstance(record, dict):
        progress = record.get("progress")
        if isinstance(progress, (int, float)) and not isinstance(progress, bool):
            return float(progress)
    return frame.index / max(points - 1, 1)


def _frame_coordinate_values(
    record: object, frame: Any, metadata: dict[str, JsonValue]
) -> dict[str, float]:
    """Id-keyed coordinate targets: the profile's verbatim table, else zip."""
    if isinstance(record, dict):
        values = record.get("coordinate_values")
        if isinstance(values, dict):
            return {str(key): float(value) for key, value in values.items()}
    coordinates = metadata.get("scan_coordinates")
    ids: list[str] = []
    if isinstance(coordinates, list):
        ids = [
            str(entry.get("id"))
            for entry in coordinates
            if isinstance(entry, dict) and entry.get("id") is not None
        ]
    return {identifier: float(value) for identifier, value in zip(ids, frame.values)}


def _resolve_artifact(root: Path, artifact: ArtifactRef) -> Path:
    """Resolve a root-relative task artifact against the capability output dir."""
    path = Path(artifact.path)
    return path if path.is_absolute() else root / path


def _atomic_copy(source: Path, destination: Path) -> None:
    """Copy *source* to *destination* through an atomic replace."""
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_bytes(source.read_bytes())
    os.replace(temporary, destination)


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
