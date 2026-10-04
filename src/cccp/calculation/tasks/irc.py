# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Bidirectional IRC — cccp task core (plan todo 21).

``run_irc`` owns the single IRC execution implementation: direction
resolution (``IrcOptions.directions`` → the backend ``direction`` keyword),
the one backend ``irc`` capability call, endpoint discovery and per-direction
completion semantics (ORCA iteration-limit endpoints are rejected), and the
typed ``IrcPayload`` (one entry per requested direction with endpoint
geometry / energy / converged / steps).  ``IrcOptions`` deliberately carries
NO ``ts_mode`` (v3.2 §6) — the IRC primitive does not use it.

Bidirectional runs are internal to the task: ``directions=(forward, reverse)``
becomes ONE ``irc`` call with ``direction="both"`` (legacy single-call
semantics).  One-way completion semantics are preserved exactly: a direction
that hit the iteration limit is not "completed", its endpoint is dropped, the
valid direction's sub-result is kept, and the overall status stays
``completed`` when the backend run succeeded and at least one requested
direction produced a validated endpoint (legacy per-workflow status mapping,
``irc.json`` goldens — non-delta).  ``complete`` is True only when EVERY
requested direction completed.

Scientific records written here (never a platform product):

* the ``irc_trajectory_v1`` path snapshot + per-direction path XYZs via
  :class:`cccp.calculation.irc_trajectory.IrcTrajectoryRecorder` (live
  capture while the backend runs — the same recorder the ACP surface used
  before the move);
* the legacy ``irc_{f,r}.xyz`` materialisation for ``final_geometries``-only
  backend results (endpoint discovery evidence).

The platform half stays ACP-side (plan todo 21): ``RESULT/irc`` endpoint
products, ``RESULT/trajectories`` view registration and
``result_manifest.json`` registration happen in the ACP wrapper only — this
module never imports or writes a result manifest.

``capability_extras`` (translation period, todo 25) may carry verbatim legacy
capability kwargs (``max_iter``, ``basis``, ``output_callback``, …); typed
``IrcOptions`` fields are translated onto the backend keyword names
(``maxpoints`` → ``max_iter``; ``step`` / ``initial_hessian`` keep their
legacy verbatim names).
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from cccp.backends.base import QCResult, to_qc_result
from cccp.calculation._common import (
    CalculationInputs,
    backend_for_request,
    classify_failure,
    electron_count,
    error_text,
    load_geometry,
    qc_metadata_json,
    resolve_multiplicity,
)
from cccp.calculation.context import TaskContext, resolve_context
from cccp.calculation.contracts import (
    ArtifactRef,
    JsonValue,
    Provenance,
    StructureRole,
    validate_electronic_state_spec,
)
from cccp.calculation.errors import TaskInputError
from cccp.calculation.irc_trajectory import IrcTrajectoryRecorder
from cccp.calculation.progress import ProgressEvent, ProgressEventKind
from cccp.calculation.requests import (
    IrcDirection,
    IrcOptions,
    TaskKind,
    TaskRequest,
    validate_request,
)
from cccp.calculation.results import IrcDirectionResult, IrcPayload, TaskResult
from cccp.calculation.selection import precheck_runtime, select_semantic

logger = logging.getLogger(__name__)

_BACKEND_FAILURES = (OSError, RuntimeError, ValueError)

#: Scientific progress stage vocabulary for one IRC task call.
IRC_PROGRESS_STAGES = ("preparing", "irc_forward", "irc_backward", "validating")

#: Legacy resource keys consumed as layout / typed options instead of being
#: forwarded verbatim to the backend capability.
IRC_LAYOUT_RESOURCE_KEYS: frozenset[str] = frozenset(
    {
        "result_dir",
        "directions",
        "maxpoints",
        "step",
        "initial_hessian",
        "opt_initial_hessian",
    }
)

#: Legacy ``_IRC_RESOURCE_KEYS`` shape: everything else rides along as
#: capability kwargs (translation cleanup: plan todo 25).


def run_irc(
    request: TaskRequest,
    *,
    context: TaskContext | None = None,
) -> TaskResult:
    """Run one IRC calculation (bidirectional internally) from a TS input.

    ``context.backend`` (runtime seam) supplies an already-resolved backend
    instance and then skips the runtime precheck.  ``context.capability_extras``
    carries verbatim legacy capability kwargs until the translation-layer
    cleanup (plan todo 25).  Per-direction sub-results keep their requested
    order even when some directions fail (record-identity rule).
    """
    validate_request(request)
    if request.task is not TaskKind.IRC:
        message = f"run_irc requires task 'irc', got {request.task.value!r}"
        raise TaskInputError(message)
    if request.options is not None and not isinstance(request.options, IrcOptions):
        message = "irc requires IrcOptions"
        raise TaskInputError(message)
    ctx = resolve_context(request, context)
    options = request.options if isinstance(request.options, IrcOptions) else IrcOptions()

    stages = _StageTracker(ctx)
    recorder: IrcTrajectoryRecorder | None = None
    try:
        stages.start("preparing")

        structure = request.structure
        if structure is None:
            message = "task 'irc' requires a structure input"
            raise TaskInputError(message)
        if structure.role is not StructureRole.TRANSITION_STATE:
            message = f"IRC requires a transition-state artifact; got role={structure.role.value!r}"
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

        directions = tuple(direction.value for direction in options.directions)
        direction_str = resolve_direction(directions)

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
                    for key, value in _irc_capability_kwargs(
                        ctx.capability_extras, request, options
                    ).items()
                    if key not in {"direction", "output_callback"}
                },
            )
        )
        backend_label = str(selection.backend)
        target_dir = request.output_dir or Path.cwd() / "irc_work"
        target_dir.mkdir(parents=True, exist_ok=True)
        result_dir = resolve_result_dir(target_dir, ctx.capability_extras)

        recorder = IrcTrajectoryRecorder(result_dir, target_dir, directions=tuple(directions))
        stages.bind(recorder, direction_str)
        stages.complete_active()  # preparing → done before dispatch

        irc_kwargs = _irc_capability_kwargs(ctx.capability_extras, request, options)
        irc_kwargs["direction"] = direction_str
        if selection.backend == "orca":
            irc_kwargs["output_callback"] = recorder.feed_line
        recorder.start()
        direction_stage = "irc_backward" if direction_str == "reverse" else "irc_forward"
        stages.start(direction_stage)
        try:
            raw_result = backend.irc(
                inputs.coordinates,
                list(inputs.symbols),
                charge=inputs.charge,
                multiplicity=inputs.multiplicity,
                output_dir=target_dir,
                **irc_kwargs,
            )
        except _BACKEND_FAILURES as error:
            recorder.stop()
            stages.fail_active(error_text(error), clear=False)
            partial = recorder.finish(status="failed", complete=False)
            metadata: dict[str, JsonValue] = {"directions": list(directions)}
            if partial is not None:
                metadata["trajectory_frame_count"] = len(partial.get("frames") or [])
            # No per-direction record exists when the backend call itself
            # failed: the payload stays empty (the ACP publication seam uses
            # "payload empty ⟺ no parsed result" to mirror the legacy
            # exception-path publication rules exactly).
            return TaskResult(
                task=TaskKind.IRC,
                status="failed",
                complete=False,
                error_kind=classify_failure(raised=error),
                errors=(error_text(error),),
                symbols=tuple(symbols),
                provenance=_provenance(backend_label, request),
                payload=IrcPayload(),
                metadata=metadata,
            )
        finally:
            recorder.stop()

        qc_result = to_qc_result(raw_result) if not isinstance(raw_result, QCResult) else raw_result
        success = bool(getattr(raw_result, "success", False)) or bool(qc_result.success)
        endpoints = discover_endpoints(raw_result, target_dir, inputs)
        completed = completed_directions(raw_result, endpoints, success)
        endpoints = {
            direction: info for direction, info in endpoints.items() if direction in completed
        }
        errors: list[str] = []
        if not success:
            raw_error = getattr(raw_result, "error_message", None) or qc_result.error_message
            if raw_error:
                errors.append(str(raw_error))
            failure_message = errors[0] if errors else "IRC calculation failed"
            stages.fail_active(failure_message, clear=False)
        else:
            if ctx.progress is not None and stages.active is not None:
                recorder.refresh(force=True)
            completed_stage = stages.active
            stages.complete_active()
            if (
                direction_str == "both"
                and "reverse" in endpoints
                and completed_stage == "irc_forward"
            ):
                stages.start("irc_backward")
                stages.complete_active()
        if success:
            stages.start("validating")
        if success and not endpoints:
            success = False
            message = "IRC produced no endpoint geometries"
            errors.append(message)
            stages.fail_active(message, clear=True)

        trajectory = recorder.finish(
            status="completed" if success else "failed",
            complete=bool(success),
        )

        metadata = qc_metadata_json(qc_result.metadata)
        base_metadata: dict[str, JsonValue] = {"directions": list(directions)}
        base_metadata["endpoint_count"] = len(endpoints)
        for direction in ("forward", "reverse"):
            if direction in endpoints:
                base_metadata[f"{direction}_endpoint"] = str(endpoints[direction]["path"])
        if trajectory is not None:
            base_metadata["trajectory_path"] = "trajectories/irc_trajectory.json"
            base_metadata["trajectory_frame_count"] = len(trajectory.get("frames") or [])
            base_metadata["path_files"] = dict(trajectory.get("geometry_files") or {})
        metadata.update(base_metadata)

        if success:
            stages.complete_active()  # validating

        return TaskResult(
            task=TaskKind.IRC,
            status="completed" if success else "failed",
            complete=success and all(direction in completed for direction in directions),
            error_kind=(
                None
                if success
                else classify_failure(error_message=errors[0] if errors else "irc failed")
            ),
            errors=tuple(errors),
            energy_hartree=qc_result.energy,
            coordinates=(
                tuple(tuple(float(c) for c in row) for row in qc_result.coordinates)
                if qc_result.coordinates is not None
                else None
            ),
            symbols=(
                tuple(str(s) for s in qc_result.symbols) if qc_result.symbols else tuple(symbols)
            ),
            frequencies=tuple(float(f) for f in (qc_result.frequencies or ())),
            converged=success,
            provenance=_provenance(backend_label, request),
            payload=IrcPayload(
                directions=_direction_entries(
                    directions, endpoints, completed, trajectory, result_dir, backend_label
                )
            ),
            metadata=metadata,
        )
    except Exception as error:
        if recorder is not None:
            recorder.stop()
            recorder.finish(status="failed", complete=False)
        stages.fail_active(error_text(error), clear=False)
        raise


# ── direction / completion semantics (legacy ``_resolve_direction`` /
#    ``_completed_directions`` — pinned by the irc.json goldens) ───────────


def resolve_direction(directions: Sequence[str]) -> str:
    """Map a directions sequence to the backend direction keyword.

    Empty input falls back to ``"both"`` (legacy semantics).
    """
    forward = "forward" in directions
    reverse = "reverse" in directions
    if forward and reverse:
        return "both"
    if forward:
        return "forward"
    if reverse:
        return "reverse"
    return "both"


def completed_directions(
    raw_result: object,
    endpoints: Mapping[str, Any],
    success: bool,
) -> set[str]:
    """Return the directions that completed; reject ORCA iteration-limit endpoints."""
    metadata = getattr(raw_result, "metadata", None) or {}
    statuses = metadata.get("direction_status") or {}
    if statuses:
        return {d for d, status in statuses.items() if status in {"completed", "converged"}}
    log_path = getattr(raw_result, "log_file", None)
    if log_path and Path(log_path).is_file():
        log = Path(log_path).read_text(encoding="utf-8", errors="replace")
        headers = list(re.finditer(r"(?:FORWARD|BACKWARD|REVERSE) IRC", log, re.I))
        if headers:
            completed: set[str] = set()
            for i, header in enumerate(headers):
                section = log[
                    header.end() : headers[i + 1].start() if i + 1 < len(headers) else len(log)
                ]
                direction = "forward" if "FORWARD" in header.group().upper() else "reverse"
                if "MAXIMUM NUMBER OF ITERATIONS REACHED" in section.upper():
                    continue
                if re.search(
                    r"IRC.*CONVERGED|IRC.*CONVERGENCE REACHED|IRC.*CONVERGENCE ACHIEVED",
                    section,
                    re.I,
                ):
                    completed.add(direction)
                    continue
                threshold = re.search(
                    r"Convergence thresholds\s+([0-9.Ee+-]+)\s+([0-9.Ee+-]+)", section
                )
                rows = re.findall(
                    r"^\s*\d+\s+[-0-9.Ee+]+\s+[-0-9.Ee+]+\s+([-0-9.Ee+]+)\s+([-0-9.Ee+]+)\s*$",
                    section,
                    re.M,
                )
                if (
                    threshold
                    and rows
                    and all(float(v) <= float(t) for v, t in zip(rows[-1], threshold.groups()))
                ):
                    completed.add(direction)
            return completed
    # Typed backend success with explicit endpoints remains supported.
    return set(endpoints) if success else set()


# ── endpoint discovery (scientific interpretation, no product writes) ─────


def discover_endpoints(
    raw_result: object,
    target_dir: Path,
    inputs: CalculationInputs,
) -> dict[str, dict[str, Any]]:
    """Extract endpoint geometries from the backend result or discover files.

    Returns a dict mapping direction →
    ``{"path": Path, "coordinates": NDArray, "symbols": list[str]}``.
    """
    from cccp.qc.interfaces.orca_ts import parse_irc_endpoints

    endpoints: dict[str, dict[str, Any]] = {}

    # 1. Explicit endpoint paths from the result (IrcResult-style)
    metadata = getattr(raw_result, "metadata", None)
    raw_endpoints = getattr(raw_result, "endpoints", None)
    if not isinstance(raw_endpoints, dict) and isinstance(metadata, dict):
        raw_endpoints = metadata.get("endpoints")
    if isinstance(raw_endpoints, dict):
        for direction, path_value in raw_endpoints.items():
            if direction in ("forward", "reverse") and path_value is not None:
                path = Path(path_value)
                if path.exists():
                    coords, symbols = _read_endpoint_xyz(path)
                    if coords is not None:
                        endpoints[direction] = {
                            "path": path,
                            "coordinates": coords,
                            "symbols": symbols,
                        }

    # 2. Final geometries from the result (materialise the legacy evidence file)
    final_geometries = getattr(raw_result, "final_geometries", None)
    if not isinstance(final_geometries, dict) and isinstance(metadata, dict):
        final_geometries = metadata.get("final_geometries")
    if isinstance(final_geometries, dict):
        for direction, geometry in final_geometries.items():
            if direction in endpoints or direction not in ("forward", "reverse"):
                continue
            if geometry is not None:
                coords = np.asarray(geometry, dtype=float)
                if coords.ndim == 2 and coords.shape[1] == 3:
                    from cccp.utils import file_io

                    endpoint_path = target_dir / f"irc_{direction[0]}.xyz"
                    file_io.write_xyz(
                        endpoint_path,
                        coords,
                        list(inputs.symbols),
                        title=f"IRC {direction} endpoint",
                    )
                    endpoints[direction] = {
                        "path": endpoint_path,
                        "coordinates": coords,
                        "symbols": list(inputs.symbols),
                    }

    # 3. Fallback: discover endpoint files via parse_irc_endpoints
    if not endpoints:
        discovered = parse_irc_endpoints("", target_dir)
        for direction, path in discovered.items():
            if direction in endpoints:
                continue
            coords, symbols = _read_endpoint_xyz(path)
            if coords is not None:
                endpoints[direction] = {"path": path, "coordinates": coords, "symbols": symbols}

    return endpoints


def _read_endpoint_xyz(path: Path) -> tuple[NDArray[np.float64] | None, list[str]]:
    """Read one single-frame endpoint XYZ; reject multi-frame/invalid files."""
    try:
        text = path.read_text(encoding="utf-8")
        if not _single_geometry(text):
            return None, []
        from cccp.utils import file_io

        coordinates, symbols = file_io.read_xyz(path)
        return np.asarray(coordinates, dtype=float), [str(s) for s in symbols]
    except (FileNotFoundError, ValueError, OSError):
        return None, []


def _single_geometry(text: str) -> bool:
    """Validate exactly one complete XYZ frame (never a trajectory)."""
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    try:
        count = int(lines[0].strip())
        if count <= 0 or len(lines) < count + 2 or any(line.strip() for line in lines[count + 2 :]):
            return False
        for line in lines[2 : count + 2]:
            fields = line.split()
            if len(fields) != 4 or not fields[0]:
                return False
            if not all(math.isfinite(float(v)) for v in fields[1:]):
                return False
        return True
    except (ValueError, IndexError, TypeError):
        return False


# ── typed payload (per-direction record identity) ────────────────────────


def _direction_entries(
    directions: tuple[str, ...],
    endpoints: Mapping[str, Mapping[str, Any]],
    completed: set[str],
    trajectory: Mapping[str, Any] | None,
    result_dir: Path,
    backend: str,
) -> tuple[IrcDirectionResult, ...]:
    """One entry per requested direction (original order, never renumbered).

    ``steps`` / ``energy_hartree`` come from validated path points only
    (``irc_trajectory_v1`` frames) — never from ORCA header counts (the
    "no unverified point count" rule).
    """
    frames_by_direction: dict[str, list[dict[str, Any]]] = {}
    geometry_files: dict[str, str] = {}
    if trajectory is not None:
        for frame in trajectory.get("frames") or []:
            if isinstance(frame, Mapping):
                direction = str(frame.get("direction") or "")
                if direction in ("forward", "reverse"):
                    frames_by_direction.setdefault(direction, []).append(dict(frame))
        raw_files = trajectory.get("geometry_files")
        if isinstance(raw_files, Mapping):
            geometry_files = {str(key): str(value) for key, value in raw_files.items()}

    entries: list[IrcDirectionResult] = []
    for direction in directions:
        info = endpoints.get(direction)
        frames = sorted(
            frames_by_direction.get(direction) or [],
            key=lambda frame: int(frame.get("index", 0)),
        )
        steps: int | None = len(frames) if frames else None
        energy: float | None = None
        if frames:
            last_energy = frames[-1].get("energy_hartree")
            if isinstance(last_energy, (int, float)) and not isinstance(last_energy, bool):
                energy = float(last_energy)
        trajectory_ref: ArtifactRef | None = None
        relative_path = geometry_files.get(direction)
        if relative_path and (result_dir / relative_path).is_file():
            trajectory_ref = ArtifactRef(
                path=Path(relative_path), type="trajectory", source=backend
            )
        entries.append(
            IrcDirectionResult(
                direction=IrcDirection(direction),
                energy_hartree=energy,
                coordinates=(
                    tuple(tuple(float(c) for c in row) for row in info["coordinates"])
                    if info is not None
                    else None
                ),
                symbols=(
                    tuple(str(s) for s in info.get("symbols") or ()) if info is not None else None
                ),
                converged=direction in completed,
                steps=steps,
                success=info is not None,
                trajectory_ref=trajectory_ref,
            )
        )
    return tuple(entries)


# ── capability kwargs / layout / progress plumbing ───────────────────────


def _irc_capability_kwargs(
    extras: Mapping[str, Any] | None,
    request: TaskRequest,
    options: IrcOptions,
) -> dict[str, Any]:
    """Assemble backend ``irc`` kwargs: verbatim extras + typed options.

    Typed options win on conflict; ``maxpoints`` maps to the backend's
    ``max_iter`` keyword while ``step`` / ``initial_hessian`` keep their
    legacy verbatim names (forwarded unchanged — the ORCA interface treats
    unknown kwargs as unused, exactly as before the split).
    """
    kwargs = {
        key: value for key, value in (extras or {}).items() if key not in IRC_LAYOUT_RESOURCE_KEYS
    }
    if request.level.method:
        kwargs["method"] = request.level.method
    if request.level.basis:
        kwargs.setdefault("basis", request.level.basis)
    if options.maxpoints is not None:
        kwargs["max_iter"] = int(options.maxpoints)
    if options.step is not None:
        kwargs["step"] = options.step
    if options.initial_hessian is not None:
        kwargs["initial_hessian"] = options.initial_hessian
    return kwargs


def resolve_result_dir(target_dir: Path, extras: Mapping[str, Any] | None) -> Path:
    """Resolve the task RESULT directory from an explicit path or WORK layout."""
    raw_result_dir = (extras or {}).get("result_dir")
    if isinstance(raw_result_dir, str) and raw_result_dir:
        return Path(raw_result_dir)
    if isinstance(raw_result_dir, Path):
        return raw_result_dir
    for parent in (target_dir, *target_dir.parents):
        if parent.name == "RESULT":
            return parent
        if parent.name == "WORK":
            return parent.parent / "RESULT"
    return target_dir / "RESULT"


def _provenance(backend: str, request: TaskRequest) -> Provenance:
    return Provenance(
        backend=backend,
        method=request.level.method,
        version="unknown",
        input_signature=str(request.structure.path) if request.structure else "",
    )


class _StageTracker:
    """Scientific stage lifecycle events for one IRC task call (R6-safe).

    Mirrors the legacy ``active_stage`` bookkeeping exactly; presentation
    (labels, detail strings) is the ACP sink's concern.
    """

    def __init__(self, ctx: TaskContext) -> None:
        self._ctx = ctx
        self._active: str | None = None
        self._recorder: IrcTrajectoryRecorder | None = None
        self._direction_str = "both"
        self._forward_count = 0
        self._reverse_count = 0

    @property
    def active(self) -> str | None:
        return self._active

    def bind(self, recorder: IrcTrajectoryRecorder, direction_str: str) -> None:
        self._recorder = recorder
        self._direction_str = direction_str
        recorder.on_snapshot = self._on_snapshot

    def start(self, stage: str) -> None:
        self._active = stage
        self._ctx.emit_progress(ProgressEvent(kind=ProgressEventKind.STAGE_STARTED, stage=stage))

    def complete_active(self) -> None:
        if self._active is None:
            return
        stage = self._active
        self._active = None
        self._ctx.emit_progress(ProgressEvent(kind=ProgressEventKind.STAGE_COMPLETED, stage=stage))

    def fail_active(self, message: str, *, clear: bool) -> None:
        if self._active is None:
            return
        stage = self._active
        if clear:
            self._active = None
        self._ctx.emit_progress(
            ProgressEvent(kind=ProgressEventKind.STAGE_FAILED, stage=stage, message=message)
        )

    def _on_snapshot(self, payload: dict[str, Any]) -> None:
        frames = payload.get("frames") or []
        self._forward_count = sum(frame.get("direction") == "forward" for frame in frames)
        self._reverse_count = sum(frame.get("direction") == "reverse" for frame in frames)
        if self._direction_str == "both" and self._reverse_count and self._active == "irc_forward":
            self.complete_active()
            self.start("irc_backward")
        if self._active is not None:
            self._ctx.emit_progress(
                ProgressEvent(
                    kind=ProgressEventKind.METRIC,
                    metric="irc_forward_points",
                    value=float(self._forward_count),
                    unit="point",
                )
            )
            self._ctx.emit_progress(
                ProgressEvent(
                    kind=ProgressEventKind.METRIC,
                    metric="irc_reverse_points",
                    value=float(self._reverse_count),
                    unit="point",
                )
            )


__all__ = [
    "IRC_LAYOUT_RESOURCE_KEYS",
    "IRC_PROGRESS_STAGES",
    "completed_directions",
    "discover_endpoints",
    "resolve_direction",
    "resolve_result_dir",
    "run_irc",
]
