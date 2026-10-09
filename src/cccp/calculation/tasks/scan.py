# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Relaxed internal-coordinate scan — cccp task core (plan todo 20).

``run_scan`` owns the single multi-point relaxed-scan implementation: plan
compilation (``ScanOptions`` → ``ReactionCoordinatePlan``), the per-point
execution loop via the backend ``relaxed_scan`` capability, and the typed
result (``ScanPayload``).  The scan is ``relaxed`` only — ``rigid`` is
reserved and rejected in v1, and ``optimizer_level`` / ``single_point_level``
are deliberately NOT part of the v1 options contract (the calculation level
is fixed outside the loop).

Scientific artifacts written here (never a platform product):

* per-frame geometries ``scan_frame_{index:03d}.xyz`` (``frame_geometry``,
  root-relative ``geometry_ref`` on each ``ScanFrame``);
* ``scan_profile.json`` (``scan_profile``) — the energy profile: per-frame
  ``index`` (ORIGINAL frame index, never renumbered), ``progress`` (λ),
  ``values`` / id-keyed ``coordinate_values``, ``energy_hartree`` and
  ``converged``/``success``.

The platform half stays ACP-side (plan todo 20): ``RESULT/structures`` /
``RESULT/trajectories`` product materialisation and ``result_manifest.json``
registration happen in the ACP wrapper only — this module never imports or
writes a result manifest.

``capability_extras`` (translation period, todo 25) may carry the verbatim
legacy ``scan_plan`` mapping; a raw plan wins over the typed options so the
full-fidelity plan features (coupling / lambda_values / explicit values /
fixed endpoints) keep their pre-migration semantics.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from cccp.calculation._common import (
    CalculationInputs,
    _write_json_artifact,
    backend_for_request,
    classify_failure,
    electron_count,
    error_text,
    load_geometry,
    resolve_multiplicity,
)
from cccp.calculation.context import TaskContext, resolve_context
from cccp.calculation.contracts import (
    ArtifactRef,
    JsonValue,
    Provenance,
    validate_electronic_state_spec,
)
from cccp.calculation.errors import TaskInputError, UnsupportedCapabilityError
from cccp.calculation.requests import ScanOptions, TaskKind, TaskRequest, validate_request
from cccp.calculation.results import ScanFrame, ScanPayload, TaskResult
from cccp.calculation.selection import precheck_runtime, select_semantic

logger = logging.getLogger(__name__)

_BACKEND_FAILURES = (OSError, RuntimeError, ValueError)

#: Legacy resource keys that feed the scan plan / layout instead of the
#: backend capability (verbatim ``capability_extras`` filter).
SCAN_PLAN_RESOURCE_KEYS: frozenset[str] = frozenset(
    {"scan_plan", "scan_coordinates", "coordinate", "scan_points", "result_dir"}
)

#: Default number of frames when neither the options nor the raw plan set it
#: (legacy ``_build_scan_plan`` behaviour).
_DEFAULT_SCAN_POINTS = 21


def run_scan(
    request: TaskRequest,
    *,
    context: TaskContext | None = None,
) -> TaskResult:
    """Run one multi-point relaxed scan (the single-task core).

    ``context.backend`` (runtime seam) supplies an already-resolved backend
    instance and then skips the runtime precheck.  ``context.capability_extras``
    carries verbatim legacy capability kwargs until the translation-layer
    cleanup (plan todo 25).  Failed frames keep their original index in
    ``payload.frames`` (record-identity rule); the overall status is
    ``completed`` only when every requested frame has a usable geometry.
    """
    validate_request(request)
    if request.task is not TaskKind.SCAN:
        message = f"run_scan requires task 'scan', got {request.task.value!r}"
        raise TaskInputError(message)
    if request.options is not None and not isinstance(request.options, ScanOptions):
        message = "scan requires ScanOptions"
        raise TaskInputError(message)
    ctx = resolve_context(request, context)

    structure = request.structure
    if structure is None:
        message = "task 'scan' requires a structure input"
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

    options = request.options if isinstance(request.options, ScanOptions) else None
    use_scants = _effective_use_scants(options, ctx.capability_extras)
    plan = build_scan_plan(options, raw_plan=_raw_plan(ctx.capability_extras))
    validate_atom_indices(plan, len(symbols))
    if use_scants:
        unsupported = _scants_unsupported_reason(plan)
        if unsupported is not None:
            raise TaskInputError(unsupported)

    state = request.electronic_state
    if state is not None:
        validation = validate_electronic_state_spec(
            state,
            backend=request.backend or "orca",
            n_electrons=electron_count(symbols, request.charge),
            n_atoms=len(symbols),
        )
        if validation.errors:
            message = "electronic_state validation failed: " + "; ".join(validation.errors)
            raise TaskInputError(message)
        for warning in validation.warnings:
            logger.warning("electronic_state: %s", warning)
    multiplicity = resolve_multiplicity(state, symbols, request.charge, request.multiplicity)
    inputs = CalculationInputs(
        coordinates=coordinates,
        symbols=symbols,
        charge=request.charge,
        multiplicity=multiplicity,
        electronic_state=state,
    )

    selection = select_semantic(request)
    if ctx.backend is None:
        selection = precheck_runtime(selection, ctx)
    backend = (
        ctx.backend
        if ctx.backend is not None
        else backend_for_request(
            selection.backend,
            config=ctx.config,
            constructor_kwargs={
                key: value
                for key, value in _scan_capability_kwargs(ctx.capability_extras, request).items()
                if key != "output_name"
            },
        )
    )
    backend_label = str(getattr(backend, "name", selection.backend) or selection.backend)
    target_dir = request.output_dir or Path.cwd() / "scan_work"
    target_dir.mkdir(parents=True, exist_ok=True)

    operation = getattr(backend, "relaxed_scan", None)
    if not callable(operation):
        message = f"backend {type(backend).__name__} does not implement capability 'relaxed_scan'"
        raise UnsupportedCapabilityError(message)
    kwargs = _scan_capability_kwargs(ctx.capability_extras, request)
    kwargs["use_scants"] = use_scants
    if options is not None and options.geom_maxiter is not None:
        kwargs.setdefault("geom_maxiter", options.geom_maxiter)
    try:
        raw_result = operation(
            inputs.coordinates,
            list(inputs.symbols),
            output_dir=target_dir,
            plan=plan,
            charge=inputs.charge,
            multiplicity=inputs.multiplicity,
            **kwargs,
        )
    except _BACKEND_FAILURES as error:
        return TaskResult(
            task=TaskKind.SCAN,
            status="failed",
            complete=False,
            error_kind=classify_failure(raised=error),
            errors=(error_text(error),),
            provenance=_provenance(backend_label, request),
            metadata=plan_metadata(plan),
        )

    if not isinstance(raw_result, _relaxed_scan_result_type()):
        message = "relaxed_scan returned an unsupported result type"
        return TaskResult(
            task=TaskKind.SCAN,
            status="failed",
            complete=False,
            error_kind=classify_failure(error_message=message),
            errors=(message,),
            energy_hartree=getattr(raw_result, "energy", None),
            coordinates=_coordinates(raw_result),
            symbols=_symbols(raw_result) or tuple(symbols),
            provenance=_provenance(backend_label, request),
            metadata=plan_metadata(plan),
        )

    frames, artifacts = _frame_records(raw_result.points, inputs, target_dir, backend_label, plan)
    payload = ScanPayload(
        frames=tuple(frames),
        profile_ref=_profile_ref(artifacts),
    )
    complete = _scan_completed(raw_result, plan)
    message = raw_result.message or "relaxed scan failed"
    errors = () if complete else (message,)
    best = raw_result.best_point()
    metadata = plan_metadata(plan)
    metadata["frame_count"] = len(raw_result.points)
    metadata["successful_frame_count"] = sum(
        1 for point in raw_result.points if point.success and point.coordinates is not None
    )
    return TaskResult(
        task=TaskKind.SCAN,
        status="completed" if complete else "failed",
        complete=complete,
        error_kind=None if complete else classify_failure(error_message=message),
        errors=errors,
        energy_hartree=best.energy_hartree if best is not None else None,
        coordinates=_coordinates(best) if best is not None else None,
        symbols=(_symbols(best) if best is not None else None) or tuple(symbols),
        converged=complete,
        artifacts=tuple(artifacts),
        provenance=_provenance(backend_label, request),
        payload=payload,
        metadata=metadata,
    )


# ── plan compilation (single implementation) ───────────────────────────


def build_scan_plan(
    options: ScanOptions | None = None,
    *,
    raw_plan: Mapping[str, object] | None = None,
) -> Any:
    """Compile scan coordinate inputs into a validated reaction-coordinate plan.

    A raw ``ReactionCoordinatePlan`` mapping (legacy ``scan_plan`` resource)
    wins over the typed options so full-fidelity plan features keep their
    pre-migration semantics; ``options.points`` overrides the plan's frame
    count exactly like the legacy ``scan_points`` key.  Typed
    ``ScanCoordinateSpec`` atoms are converted to the 0-based
    ``CoordinateSpec`` index base via their explicit ``atom_index_base``.
    """
    from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan

    if isinstance(raw_plan, Mapping):
        try:
            plan = ReactionCoordinatePlan.from_dict(dict(raw_plan))
        except (TypeError, ValueError) as error:
            raise TaskInputError(str(error)) from error
        points = _parse_points(options.points if options is not None else None, allow_none=True)
        if points is not None and points != plan.points:
            plan = ReactionCoordinatePlan(
                coordinates=plan.coordinates,
                points=points,
                coupling=plan.coupling,
                start_from=plan.start_from,
            )
        return plan

    if options is None or not options.coordinates:
        message = "scan requires at least one coordinate in atom1,atom2,start,end form"
        raise TaskInputError(message)
    if options.values and len(options.coordinates) != 1:
        message = "options.values is an explicit grid for exactly one scan coordinate"
        raise TaskInputError(message)

    coordinates = options.coordinates
    values = tuple(options.values) if options.values else ()
    points = _parse_points(options.points, allow_none=True)
    if points is None and values:
        points = len(values)
    points = _parse_points(points)
    specs = []
    for index, spec in enumerate(coordinates):
        base = int(spec.atom_index_base)
        atoms = tuple(int(atom) - base for atom in spec.atoms)
        try:
            specs.append(
                CoordinateSpec(
                    id=f"rc{index + 1}",
                    kind=spec.kind,
                    atoms=atoms,
                    start=spec.start,
                    end=spec.end,
                    values=values if len(coordinates) == 1 else (),
                )
            )
        except ValueError as error:
            raise TaskInputError(str(error)) from error
    try:
        return ReactionCoordinatePlan(coordinates=tuple(specs), points=points)
    except ValueError as error:
        raise TaskInputError(str(error)) from error


def validate_atom_indices(plan: Any, atom_count: int) -> None:
    """Reject coordinates whose zero-based atom indices are absent from the input."""
    for coordinate in plan.coordinates:
        for atom in coordinate.atoms:
            if atom >= atom_count:
                message = (
                    f"atom index {atom} is out of range for {atom_count} atoms; "
                    + f"原子索引 {atom} 超出输入分子原子数 {atom_count}"
                )
                raise TaskInputError(message)


def plan_metadata(plan: Any) -> dict[str, JsonValue]:
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


# ── frame collection (scientific artifacts only) ────────────────────────


def _frame_records(
    points: Sequence[Any],
    inputs: CalculationInputs,
    target_dir: Path,
    backend: str,
    plan: Any,
) -> tuple[list[ScanFrame], list[ArtifactRef]]:
    """Write per-frame geometries + the scan profile; build the typed frames.

    Failed points keep their original index and are never renumbered; only
    frames with a usable geometry get a ``frame_geometry`` artifact.
    """
    from cccp.utils import file_io

    artifacts: list[ArtifactRef] = []
    frames: list[ScanFrame] = []
    profile_frames: list[dict[str, JsonValue]] = []
    successful = 0
    for point in points:
        index = int(point.frame_index)
        coordinate_values = {
            str(key): float(value) for key, value in point.coordinate_values.items()
        }
        values = tuple(coordinate_values.values())
        geometry_ref: ArtifactRef | None = None
        if point.success and point.coordinates is not None:
            symbols = list(point.symbols or inputs.symbols)
            frame_path = target_dir / f"scan_frame_{index:03d}.xyz"
            if point.energy_hartree is not None:
                file_io.write_xyz(
                    frame_path,
                    np.asarray(point.coordinates, dtype=float),
                    symbols,
                    title=f"scan frame {index}",
                    energy=point.energy_hartree,
                )
            else:
                file_io.write_xyz(
                    frame_path,
                    np.asarray(point.coordinates, dtype=float),
                    symbols,
                    title=f"scan frame {index}",
                )
            geometry_ref = ArtifactRef(
                path=Path(frame_path.name),
                type="frame_geometry",
                checksum="",
                source=backend,
            )
            artifacts.append(geometry_ref)
            successful += 1
        frames.append(
            ScanFrame(
                index=index,
                values=values,
                energy_hartree=point.energy_hartree,
                geometry_ref=geometry_ref,
                converged=bool(point.success),
                success=bool(point.success),
            )
        )
        profile_frames.append(
            {
                "index": index,
                "progress": float(point.progress),
                "values": list(values),
                "coordinate_values": coordinate_values,
                "energy_hartree": point.energy_hartree,
                "converged": bool(point.success),
                "success": bool(point.success),
            }
        )

    profile_ref: ArtifactRef | None = None
    if frames:
        profile_path = target_dir / "scan_profile.json"
        _write_json_artifact(
            profile_path,
            {
                "schema": "scan_profile_v1",
                "workflow": "scan",
                "points": plan.points,
                "frame_count": len(frames),
                "successful_frame_count": successful,
                "frames": profile_frames,
            },
        )
        profile_ref = ArtifactRef(
            path=Path(profile_path.name),
            type="scan_profile",
            source=backend,
        )
        artifacts.append(profile_ref)
    return frames, artifacts


def _scan_completed(result: Any, plan: Any) -> bool:
    """Return whether every requested scan frame has a usable geometry."""
    return (
        result.success
        and len(result.points) == plan.points
        and all(point.success and point.coordinates is not None for point in result.points)
    )


# ── small helpers ────────────────────────────────────────────────────────


def _raw_plan(extras: Mapping[str, Any] | None) -> Mapping[str, object] | None:
    """Verbatim legacy ``scan_plan`` mapping (translation-period input)."""
    if not extras:
        return None
    candidate = extras.get("scan_plan")
    return candidate if isinstance(candidate, Mapping) else None


def _effective_use_scants(options: ScanOptions | None, extras: Mapping[str, Any] | None) -> bool:
    """Resolve the effective ScanTS toggle for one scan call.

    The typed option is authoritative when it asks for ScanTS; otherwise a
    verbatim legacy ``use_scants`` capability extra (the PES pipeline seam)
    is honoured, and the plain default is OFF.  The ORCA interface default
    (``True``) is never relied upon — the task layer always overrides it.
    """
    if options is not None and options.use_scants:
        return True
    if extras:
        raw = extras.get("use_scants")
        if raw is not None:
            return bool(raw)
    return False


def _scants_unsupported_reason(plan: Any) -> str | None:
    """Explicit error for ScanTS branches ORCA cannot deliver.

    A synchronous multi-coordinate path, an explicit per-frame grid or a
    fixed-endpoint reference path runs through the interface's synchronous
    loop, which cannot apply ``ScanTS``; silently ignoring the request is
    forbidden.
    """
    synchronous = (
        len(plan.drive_coordinates()) > 1
        or bool(getattr(plan, "fixed_endpoints", False))
        or any(coordinate.values for coordinate in plan.coordinates)
    )
    if not synchronous:
        return None
    return (
        "use_scants (ORCA ScanTS) is unavailable for synchronous "
        "multi-coordinate, explicit-grid or fixed-endpoint scans; "
        "use a single-coordinate scan or disable ScanTS"
    )


def _scan_capability_kwargs(
    extras: Mapping[str, Any] | None, request: TaskRequest
) -> dict[str, Any]:
    """Verbatim legacy capability kwargs minus the plan/layout keys.

    The calculation level is fixed outside the loop: ``method`` is forwarded
    exactly when the request carries one (legacy ``_scan_kwargs`` semantics —
    no implicit default is invented).
    """
    kwargs = {
        key: value for key, value in (extras or {}).items() if key not in SCAN_PLAN_RESOURCE_KEYS
    }
    if request.level.method:
        kwargs["method"] = request.level.method
    if request.level.basis:
        kwargs.setdefault("basis", request.level.basis)
    return kwargs


def _parse_points(value: object, *, allow_none: bool = False) -> int | None:
    """Parse the number of scan frames (legacy default 21)."""
    if value is None:
        return None if allow_none else _DEFAULT_SCAN_POINTS
    if isinstance(value, bool):
        raise TaskInputError("scan_points must be an integer >= 2")
    if not isinstance(value, (str, int, float)):
        raise TaskInputError("scan_points must be an integer >= 2")
    try:
        points = int(value)
    except (TypeError, ValueError) as error:
        raise TaskInputError("scan_points must be an integer >= 2") from error
    if points < 2:
        raise TaskInputError("scan_points must be an integer >= 2")
    return points


def _relaxed_scan_result_type() -> type:
    from cccp.qc.interfaces.xtb_scan import RelaxedScanResult

    return RelaxedScanResult


def _profile_ref(artifacts: Sequence[ArtifactRef]) -> ArtifactRef | None:
    for artifact in artifacts:
        if artifact.type == "scan_profile":
            return artifact
    return None


def _coordinates(result: object) -> tuple[tuple[float, float, float], ...] | None:
    raw = getattr(result, "coordinates", None)
    if raw is None:
        return None
    return tuple(tuple(float(c) for c in row) for row in raw)


def _symbols(result: object) -> tuple[str, ...] | None:
    raw = getattr(result, "symbols", None)
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
    "SCAN_PLAN_RESOURCE_KEYS",
    "build_scan_plan",
    "plan_metadata",
    "run_scan",
    "validate_atom_indices",
]
