"""Clustering task core (plan todo 42 — ISOSTAT/Molclus clustering).

Pipeline: request validation → two-step backend selection (semantic + this
call's runtime precheck) → translation (ensemble → multi-frame XYZ, typed
options → capability kwargs) → interface execution → typed
:class:`~cccp.calculation.results.ClusteringPayload`.

Scientific scope only: cluster assignments + representative structures
keyed back to the input ensemble frame indices.  The input shape is an
ensemble (multi-frame structure input); assignments reference the original
ensemble frame indices and every input frame ends up in exactly one cluster.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from cccp.calculation._common import (
    backend_for_request,
    classify_failure,
    error_text,
    qc_metadata_json,
)
from cccp.calculation.context import TaskContext, resolve_context
from cccp.calculation.contracts import ArtifactRef, JsonValue
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import ClusteringOptions, TaskKind, TaskRequest, validate_request
from cccp.calculation.results import (
    ClusterAssignment,
    ClusteringPayload,
    ErrorKind,
    TaskResult,
)
from cccp.calculation.selection import precheck_runtime, select_semantic
from cccp.calculation.tasks.conformer_search import (
    EnsembleFrame,
    artifact_ref,
    execution_provenance,
    normalize_capability_result,
    parse_ensemble_frames,
    resolve_operation,
)
from cccp.utils.file_io import read_xyz_multiframe, write_xyz_multiframe

logger = logging.getLogger(__name__)

_BACKEND_FAILURES = (OSError, RuntimeError, ValueError)

#: Semantic capability → backend execution method and its call shape.
_CAPABILITY_METHODS: tuple[tuple[str, str], ...] = (
    ("cluster", "path"),
    ("clustering", "path"),
)

#: Typed option field → ``cluster`` capability kwarg.
_OPTION_KWARGS: tuple[tuple[str, str], ...] = (
    ("edis", "edis"),
    ("gdis", "gdis"),
    ("temperature_k", "temperature"),
    ("nout", "nout"),
)


def run_clustering(
    request: TaskRequest,
    *,
    context: TaskContext | None = None,
) -> TaskResult:
    """Run one clustering over an ensemble (assignments + representatives).

    Three-state semantics (``cccp.calculation.requests.P2_TASK_CONTRACTS``):
    success = completed with every input frame assigned to a cluster;
    partial = ``complete=False`` keeping assigned clusters and their
    representatives; empty = zero clusters for a non-empty ensemble is a
    failed result with ``error_kind=backend_failure``.
    """
    validate_request(request)
    if request.task is not TaskKind.CLUSTERING:
        message = f"run_clustering requires task 'clustering', got {request.task.value!r}"
        raise TaskInputError(message)
    if request.options is not None and not isinstance(request.options, ClusteringOptions):
        message = "clustering requires ClusteringOptions"
        raise TaskInputError(message)
    ctx = resolve_context(request, context)

    selection = select_semantic(request)
    if ctx.backend is None:
        selection = precheck_runtime(selection, ctx)

    frames = _load_ensemble(request.structure, ctx)
    if not frames:
        message = "clustering requires a non-empty ensemble input"
        raise TaskInputError(message)

    options = request.options if isinstance(request.options, ClusteringOptions) else None
    kwargs: dict[str, Any] = dict(ctx.capability_extras or {})
    if options is not None:
        for option_field, kwarg in _OPTION_KWARGS:
            value = getattr(options, option_field)
            if value is not None:
                kwargs[kwarg] = value
    if request.resources.nproc is not None:
        kwargs.setdefault("nthreads", int(request.resources.nproc))

    backend = (
        ctx.backend
        if ctx.backend is not None
        else backend_for_request(selection.backend, config=ctx.config)
    )
    backend_label = str(getattr(backend, "name", selection.backend) or selection.backend)
    target_dir = Path(
        request.output_dir if request.output_dir is not None else (ctx.workdir or Path.cwd())
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    ensemble_xyz = _write_ensemble(target_dir, frames)

    _, _, operation = resolve_operation(backend, _CAPABILITY_METHODS)
    try:
        raw_result = operation(ensemble_xyz, output_dir=target_dir, **kwargs)
    except _BACKEND_FAILURES as error:
        return TaskResult(
            task=TaskKind.CLUSTERING,
            status="failed",
            complete=False,
            error_kind=classify_failure(raised=error),
            errors=(error_text(error),),
            provenance=execution_provenance(backend_label, request),
        )

    outcome = normalize_capability_result(raw_result)
    metadata: dict[str, JsonValue] = qc_metadata_json(outcome.metadata)
    clustered_path = outcome.output_file

    errors: list[str] = []
    if outcome.error_message:
        errors.append(outcome.error_message)

    representatives: list[EnsembleFrame] = []
    if clustered_path is not None and clustered_path.is_file():
        representatives, rep_errors = parse_ensemble_frames(clustered_path)
        errors.extend(rep_errors)

    artifacts = []
    clustered_ref: ArtifactRef | None = None
    if clustered_path is not None:
        clustered_ref = artifact_ref(clustered_path, "clustered", backend_label)
        artifacts.append(clustered_ref)

    if not representatives:
        # Empty semantics: zero clusters for a non-empty ensemble is a failure.
        message = errors[0] if errors else "clustering returned zero clusters"
        return TaskResult(
            task=TaskKind.CLUSTERING,
            status="failed",
            complete=False,
            error_kind=ErrorKind.BACKEND_FAILURE,
            errors=(message,),
            artifacts=tuple(artifacts),
            provenance=execution_provenance(backend_label, request),
            metadata=metadata,
        )

    assignments = _build_assignments(frames, representatives)
    payload = ClusteringPayload(assignments=assignments, clustered_ref=clustered_ref)
    metadata["n_clusters"] = len(assignments)

    partial = bool(errors) or not outcome.success
    if partial:
        return TaskResult(
            task=TaskKind.CLUSTERING,
            status="failed",
            complete=False,
            error_kind=None,
            errors=tuple(errors) or ("clustering completed with partial output",),
            artifacts=tuple(artifacts),
            provenance=execution_provenance(backend_label, request),
            payload=payload,
            metadata=metadata,
        )
    return TaskResult(
        task=TaskKind.CLUSTERING,
        status="completed",
        complete=True,
        errors=(),
        artifacts=tuple(artifacts),
        provenance=execution_provenance(backend_label, request),
        payload=payload,
        metadata=metadata,
    )


def _load_ensemble(structure: Any, ctx: TaskContext) -> list[EnsembleFrame]:
    """Load the ensemble input shape: multi-frame path or inline multiframe geometry.

    Inline geometry keeps the legacy stacked convention (``n_frames * n_atoms``
    rows against ``n_atoms`` symbols).
    """
    if structure is None:
        raise TaskInputError("clustering requires a structure input")
    if structure.coordinates is not None and structure.symbols is not None:
        stacked = np.asarray(structure.coordinates, dtype=np.float64)
        symbols = tuple(str(s) for s in structure.symbols)
    else:
        path = structure.path
        if path is None:
            message = "structure input requires a path or inline coordinates+symbols"
            raise TaskInputError(message)
        if not path.is_absolute():
            path = ctx.input_root() / path
        if not path.is_file():
            raise TaskInputError(f"ensemble input not found: {path}")
        stacked_raw, symbols_raw = read_xyz_multiframe(path)
        stacked = np.asarray(stacked_raw, dtype=np.float64)
        symbols = tuple(str(s) for s in symbols_raw)
    if not symbols or stacked.size == 0:
        return []
    n_atoms = len(symbols)
    if stacked.ndim != 2 or stacked.shape[0] % n_atoms != 0:
        raise TaskInputError(
            "ensemble input coordinates must stack whole frames "
            f"({n_atoms} atoms per frame), got {stacked.shape}"
        )
    n_frames = stacked.shape[0] // n_atoms
    return [
        EnsembleFrame(
            index=index,
            symbols=symbols,
            coordinates=stacked[index * n_atoms : (index + 1) * n_atoms],
        )
        for index in range(n_frames)
    ]


def _write_ensemble(target_dir: Path, frames: list[EnsembleFrame]) -> Path:
    stacked = np.vstack([frame.coordinates for frame in frames])
    symbols = list(frames[0].symbols)
    path = target_dir / "ensemble_input.xyz"
    write_xyz_multiframe(path, stacked, symbols)
    return path


def _build_assignments(
    frames: list[EnsembleFrame],
    representatives: list[EnsembleFrame],
) -> tuple[ClusterAssignment, ...]:
    """Map every input frame to its nearest representative (original indices kept).

    Each representative is first matched back to its source ensemble frame
    (exact geometry match, nearest aligned RMSD as fallback); every input
    frame then joins the cluster of its nearest representative.  Order and
    numbering: ``cluster_id`` follows the clustered-ensemble order,
    ``member_indices`` are sorted original ensemble indices.
    """
    n_atoms = len(frames[0].symbols)
    reps = [_frame_coords(rep, n_atoms) for rep in representatives]
    members: list[list[int]] = [[] for _ in reps]
    for frame in frames:
        coords = _frame_coords(frame, n_atoms)
        distances = [_aligned_rmsd(coords, rep) for rep in reps]
        members[int(np.argmin(distances))].append(frame.index)

    assignments: list[ClusterAssignment] = []
    for cluster_id, rep in enumerate(representatives):
        rep_coords = reps[cluster_id]
        representative_index = min(
            range(len(frames)),
            key=lambda i: _aligned_rmsd(rep_coords, _frame_coords(frames[i], n_atoms)),
        )
        assignments.append(
            ClusterAssignment(
                cluster_id=cluster_id,
                representative_index=representative_index,
                member_indices=tuple(members[cluster_id]),
            )
        )
    return tuple(assignments)


def _frame_coords(frame: EnsembleFrame, n_atoms: int) -> NDArray[np.float64]:
    return np.asarray(frame.coordinates, dtype=np.float64).reshape(n_atoms, 3)


def _aligned_rmsd(coords_a: NDArray[np.float64], coords_b: NDArray[np.float64]) -> float:
    """Kabsch-aligned RMSD between two same-atom-order geometries."""
    from cccp.utils.geometry_tools import GeometryUtils

    aligned = GeometryUtils.align_structures(coords_a, coords_b)
    return GeometryUtils.rmsd(coords_a, aligned)


__all__ = ["run_clustering"]
