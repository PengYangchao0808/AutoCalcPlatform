# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""IRC calculation primitive — ACP compat wrapper (plan todo 21).

The bidirectional IRC execution core lives in
:mod:`cccp.calculation.tasks.irc`: direction resolution, the single backend
``irc`` capability call, endpoint discovery / per-direction completion
semantics and the typed ``IrcPayload``.  This module is the ACP-side compat
surface: the legacy ``run_irc(ts_artifact, …)`` entry, the legacy backend
registry seam, and the **publication half** — ``RESULT/irc`` endpoint
products (``IRC_ENDPOINT``), ``RESULT/trajectories`` trajectory products and
``result_manifest.json`` registration through the named publication entry
(:func:`acp.calculations.result_publication.register_result_manifest`).
``run_irc`` is a pure re-export alias of ``execute_irc`` (todo 23 hard
switch: no ``def run_irc`` may exist outside ``cccp.calculation.tasks.irc``).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from acp.calculations.contracts import (
    ArtifactRef,
    CalculationRequest,
    CalculationResult,
    StructureArtifact,
    StructureRole,
)
from acp.calculations.legacy_adapters import to_legacy_result, to_task_request
from acp.calculations.primitives._common import (
    backend_for_request,
    backend_name,
    capability_kwargs,
    output_dir,
)
from acp.calculations.progress import ProgressReporter
from acp.calculations.result_publication import register_result_manifest
from acp.storage.manifest import ProductKind, ResultManifest
from cccp import calculation as _cccp_calculation
from cccp.calculation.context import TaskContext
from cccp.calculation.progress import ProgressEvent, ProgressEventKind
from cccp.calculation.requests import TaskKind
from cccp.calculation.results import IrcPayload, TaskResult
from cccp.calculation.tasks import irc as _irc_task
from cccp.calculation.tasks.irc import (
    resolve_result_dir,
)
from cccp.utils import file_io

logger = logging.getLogger(__name__)

__all__ = ["IRC_PROGRESS_STAGES", "run_irc"]

IRC_PROGRESS_STAGES = ("preparing", "irc_forward", "irc_backward", "validating")

#: Compat aliases for the frozen golden generator (pre-migration helper names).
_resolve_direction = _irc_task.resolve_direction
_completed_directions = _irc_task.completed_directions


def execute_irc(
    ts_artifact: StructureArtifact,
    *,
    directions: tuple[str, ...] = ("forward", "reverse"),
    method: str = "",
    resources: dict[str, Any] | None = None,
    workflow: str = "irc",
    profile: str | None = None,
    progress_reporter: ProgressReporter | None = None,
) -> CalculationResult:
    """ACP compat wrapper: cccp task core + endpoint/trajectory publication.

    The legacy role gate (transition-state artifact) is enforced before any
    backend dispatch, exactly like the pre-migration entry point.  Endpoint
    products are materialised from the typed payload; an empty payload (the
    backend call itself failed) skips endpoint registration and only partial
    trajectory products are registered — the legacy exception-path
    publication rules.
    """
    if progress_reporter is not None:
        progress_reporter.initialize()
    if ts_artifact.role != StructureRole.TRANSITION_STATE:
        raise ValueError(
            f"IRC requires a transition-state artifact; got role={ts_artifact.role.value!r}"
        )

    resources = dict(resources or {})
    resources.setdefault("backend", "orca")
    if method:
        resources["method"] = method

    request = CalculationRequest(
        input_artifact=ts_artifact,
        method=method,
        resources=resources,
        workflow=workflow,
        profile=profile,
    )
    task_request, binding = to_task_request(request, TaskKind.IRC, directions=directions)
    selected_backend = backend_name(request)
    backend = backend_for_request(request, selected_backend, resources=task_request.resources)
    target_dir = output_dir(request) or Path.cwd() / "irc_work"
    target_dir.mkdir(parents=True, exist_ok=True)

    sink = _IrcReporterSink(progress_reporter) if progress_reporter is not None else None
    context = TaskContext(
        config=binding.config,
        workdir=binding.artifact_root or target_dir,
        backend=backend,
        capability_extras=capability_kwargs(request),
        progress=sink,
    )
    task_result = _cccp_calculation.run_irc(task_request, context=context)

    legacy = to_legacy_result(task_result, binding)
    metadata = dict(task_result.metadata)
    metadata.update(legacy.metadata)
    metadata["directions"] = list(directions)
    result_dir = resolve_result_dir(target_dir, request.resources)
    artifacts = _publish_irc_products(
        request,
        task_result,
        result_dir,
        selected_backend,
        directions,
    )
    return CalculationResult(
        energy=legacy.energy,
        coords=legacy.coords,
        frequencies=legacy.frequencies,
        artifacts=artifacts,
        status=legacy.status,
        errors=legacy.errors,
        provenance=legacy.provenance,
        metadata=metadata,
    )


#: Pure re-export alias (no ``def run_irc`` outside the cccp task core).
run_irc = execute_irc


# ── ACP-side product publication (endpoint/trajectory science comes from cccp)


def _publish_irc_products(
    request: CalculationRequest,
    task_result: TaskResult,
    result_dir: Path,
    backend: str,
    directions: tuple[str, ...],
) -> list[ArtifactRef]:
    """Materialise endpoint/trajectory products and register the manifest.

    Never raises: a missing/partial payload just skips publication (the IRC
    step is never failed due to publication alone).
    """
    try:
        return _publish_irc_products_unchecked(
            request, task_result, result_dir, backend, directions
        )
    except Exception:  # noqa: BLE001 — publication never fails the step
        logger.debug("irc: product publication failed; skipping", exc_info=True)
        return []


def _publish_irc_products_unchecked(
    request: CalculationRequest,
    task_result: TaskResult,
    result_dir: Path,
    backend: str,
    directions: tuple[str, ...],
) -> list[ArtifactRef]:
    artifacts: list[ArtifactRef] = []
    payload = task_result.payload if isinstance(task_result.payload, IrcPayload) else None
    if payload is not None and payload.directions:
        artifacts.extend(
            _write_endpoint_products(request, task_result, payload, result_dir, backend, directions)
        )
    artifacts.extend(_register_trajectory_products(result_dir, backend))
    return artifacts


def _write_endpoint_products(
    request: CalculationRequest,
    task_result: TaskResult,
    payload: IrcPayload,
    result_dir: Path,
    backend: str,
    directions: tuple[str, ...],
) -> list[ArtifactRef]:
    """Write ``RESULT/irc/irc_{direction}.xyz`` endpoint products and register them."""
    irc_dir = result_dir / "irc"
    irc_dir.mkdir(parents=True, exist_ok=True)

    artifacts: list[ArtifactRef] = []
    manifest = ResultManifest(
        task_id="",
        workflow=request.workflow or "irc",
        status=task_result.status,
    )
    entries = {entry.direction.value: entry for entry in payload.directions}
    for direction in ("forward", "reverse"):
        entry = entries.get(direction)
        if entry is None or not entry.success or entry.coordinates is None:
            continue
        symbols = list(entry.symbols or ())
        out_path = irc_dir / f"irc_{direction}.xyz"
        file_io.write_xyz(
            out_path,
            np.asarray(entry.coordinates, dtype=float),
            symbols,
            title=f"IRC {direction} endpoint",
        )
        relative_path = str(out_path.relative_to(result_dir))
        artifacts.append(ArtifactRef(path=out_path, type="structure", source=backend))
        _ = manifest.add_product(
            id=f"irc_{direction}_endpoint",
            label=f"IRC {direction} endpoint",
            path=relative_path,
            kind=ProductKind.IRC_ENDPOINT,
            metadata={
                "source_kind": "irc_endpoint",
                "direction": direction,
                "direction_status": "completed",
                "requested_directions": list(directions),
                "optimization_status": "not_performed",
                "policy_version": 1,
            },
        )

    _ = register_result_manifest(result_dir, manifest)
    return artifacts


def _register_trajectory_products(result_dir: Path, backend: str) -> list[ArtifactRef]:
    """Register the IRC path trajectory and per-direction geometry products."""
    trajectory_path = result_dir / "trajectories" / "irc_trajectory.json"
    if not trajectory_path.is_file():
        return []
    try:
        trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(trajectory, dict):
        return []

    artifacts: list[ArtifactRef] = [
        ArtifactRef(path=trajectory_path, type="trajectory", source=backend)
    ]
    try:
        manifest = ResultManifest.read(result_dir)
    except FileNotFoundError:
        manifest = ResultManifest(
            task_id="",
            workflow="irc",
            status=str(trajectory.get("status") or "running"),
        )
    manifest.workflow = "irc"
    manifest.status = str(trajectory.get("status") or manifest.status)
    _ = manifest.add_product(
        id="irc_trajectory",
        label="IRC path trajectory",
        path="trajectories/irc_trajectory.json",
        kind=ProductKind.TRAJECTORY,
    )
    geometry_files = trajectory.get("geometry_files") or {}
    if isinstance(geometry_files, dict):
        for direction in ("forward", "reverse"):
            relative_path = geometry_files.get(direction)
            if not relative_path:
                continue
            path = result_dir / str(relative_path)
            if path.is_file():
                artifacts.append(ArtifactRef(path=path, type="trajectory", source=backend))
            _ = manifest.add_product(
                id=f"irc_{direction}_path",
                label=f"IRC {direction} path",
                path=str(relative_path),
                kind=ProductKind.TRAJECTORY,
            )
    _ = register_result_manifest(result_dir, manifest)
    return artifacts


# ── progress mapping (scientific events → ACP ProgressReporter presentation)


class _IrcReporterSink:
    """Map cccp scientific stage/metric events onto the ACP progress reporter.

    Stage names and point counts are scientific; the presentation detail
    string stays ACP-side.
    """

    def __init__(self, reporter: ProgressReporter) -> None:
        self._reporter = reporter
        self._current: str | None = None
        self._forward_count = 0
        self._reverse_count = 0

    def emit(self, event: ProgressEvent) -> None:
        if event.kind is ProgressEventKind.STAGE_STARTED:
            self._reporter.start_stage(event.stage)
            self._current = event.stage
            return
        if event.kind is ProgressEventKind.STAGE_COMPLETED:
            self._reporter.complete_stage(event.stage)
            self._current = None
            return
        if event.kind is ProgressEventKind.STAGE_FAILED:
            self._reporter.fail_stage(event.stage, event.message or "")
            return
        if event.kind is not ProgressEventKind.METRIC or event.value is None:
            return
        if event.metric == "irc_forward_points":
            self._forward_count = int(event.value)
        elif event.metric == "irc_reverse_points":
            self._reverse_count = int(event.value)
        else:
            return
        if self._current is not None:
            self._reporter.set_stage_detail(
                self._current,
                f"正向 {self._forward_count} 点 · 反向 {self._reverse_count} 点",
            )
