# pyright: reportAny=false, reportArgumentType=false, reportExplicitAny=false, reportPrivateUsage=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
"""Preparation and execution helpers for frame-wise single points.

Production execution (plan todo 17) goes through the generic homogeneous
batch executor :func:`cccp.calculation.batch.run_batch` with the single-task
core ``run_singlepoint`` as the per-item execution function — the legacy
backend-direct entry ``acp.backends.batch`` is an isolated compat surface and
is never imported here (``legacy_batch_quarantine``).

Cache semantics (A8 — cache version / reuse conditions, defined here):

* **cache version** ``CACHE_SCHEMA_VERSION = 1`` records are produced by
  ``cccp.calculation.batch``: identity = task + backend + effective
  ``ResolvedCalculationSpec`` + charge/multiplicity + electronic state +
  input content hashes + version constraint; a hit additionally requires the
  record to be complete and its recorded artifacts to still exist with
  matching digests.
* **legacy geometry-keyed records** (``<geometry_key>.json`` written by
  ``acp.backends.batch._write_cache``) are an **explicit miss** under this
  version rule: they carry no schema version / identity / artifact digests
  and cannot prove compatibility.  Old runs therefore recompute once and
  re-populate the versioned cache (the key is allowed to change across
  versions — plan verification strategy).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from acp.backends.base import SinglePointCalculator
from acp.calculations.batch._items import BatchStructureItem, item_cache_key
from cccp.calculation._common import clear_stale_run_files
from cccp.calculation.batch import (
    BatchEntry,
    BatchItemResult,
    BatchResources,
    CacheStore,
    FileSystemCacheStore,
    ItemRunOutcome,
    MemoryCacheStore,
    run_batch,
)
from cccp.calculation.context import TaskContext
from cccp.calculation.requests import MethodSpec, StructureInput, TaskKind, TaskRequest
from cccp.calculation.results import TaskResult
from cccp.calculation.tasks.singlepoint import run_singlepoint

from ._singlepoint_frames import frame_data, frame_id, method_signature, scope
from ._singlepoint_models import (
    BatchSinglePointFrameResult,
    FrameInput,
    PreparedFrame,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FramePreparationOptions:
    """Inputs that determine frame normalization and cache identity."""

    backend_name: str
    method: str | None
    basis: str | None
    solvent: str | None
    charge: int | None
    multiplicity: int | None
    symbols: Sequence[str] | None
    frame_ids: Sequence[str] | None
    profile: str
    options: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class BatchSinglePointExecutionOptions:
    """Shared batch-helper options for one executor invocation."""

    output_dir: Path
    method: str | None
    basis: str | None
    max_workers: int | None
    solvent: str | None
    cache: bool
    config: Mapping[str, object] | None
    options: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _FrameCallbacks:
    """Callbacks used to expose frame lifecycle events from worker execution."""

    on_start: Callable[[str], None] | None
    on_done: Callable[[int, int], None]
    on_result: Callable[[str, float | None, str], None] | None = None


def _signal_frame_result(
    callbacks: _FrameCallbacks | None,
    frame_id: str,
    energy: float | None,
    status: str,
) -> None:
    """Emit one ``on_result`` frame-resolution event; never raise into workers."""
    if callbacks is None or callbacks.on_result is None:
        return
    try:
        callbacks.on_result(frame_id, energy, status)
    except Exception as exc:  # noqa: BLE001 - live view callbacks must not break workers
        logger.warning("single-point on_frame_done callback failed for %s: %s", frame_id, exc)


def _signal_frame_failure(
    callbacks: _FrameCallbacks | None,
    frame_id: str,
) -> None:
    """Emit a failed ``on_result`` event for frames that bypassed execution."""
    if callbacks is None or callbacks.on_result is None:
        return
    try:
        callbacks.on_result(frame_id, None, "failed")
    except Exception as exc:  # noqa: BLE001 - live view callbacks must not break workers
        logger.warning("single-point on_frame_done callback failed for %s: %s", frame_id, exc)


def prepare_frames(
    frames: Sequence[FrameInput],
    settings: FramePreparationOptions,
) -> tuple[list[PreparedFrame], dict[str, BatchSinglePointFrameResult], list[str]]:
    """Normalize frames and record input failures without aborting siblings."""
    if settings.frame_ids is not None and len(settings.frame_ids) != len(frames):
        raise ValueError("frame_ids must contain one id per frame")

    prepared: list[PreparedFrame] = []
    failures: dict[str, BatchSinglePointFrameResult] = {}
    ordered_ids: list[str] = []
    signature = method_signature(
        settings.backend_name,
        settings.method,
        settings.basis,
        settings.solvent,
        settings.options,
    )
    for index, frame in enumerate(frames):
        frame_id_value = frame_id(frame, index, settings.frame_ids)
        if frame_id_value in ordered_ids:
            raise ValueError("frame identifiers must be unique")
        ordered_ids.append(frame_id_value)
        try:
            (
                coordinates,
                frame_symbols,
                frame_charge,
                frame_multiplicity,
                tag,
                candidate_id,
                xyz,
            ) = frame_data(
                frame,
                frame_id_value,
                settings.symbols,
                settings.charge,
                settings.multiplicity,
            )
            item = BatchStructureItem(
                item_id=frame_id_value,
                name=frame_id_value,
                tag=tag,
                xyz=xyz,
                candidate_id=candidate_id or frame_id_value,
                charge=frame_charge,
                multiplicity=frame_multiplicity,
            )
            prepared.append(
                PreparedFrame(
                    frame_id=frame_id_value,
                    coordinates=coordinates,
                    symbols=tuple(frame_symbols),
                    charge=frame_charge,
                    multiplicity=frame_multiplicity,
                    cache_key=item_cache_key(item, settings.profile, signature),
                )
            )
        except (OSError, TypeError, ValueError) as exc:
            failures[frame_id_value] = BatchSinglePointFrameResult(
                frame_id=frame_id_value,
                energy_hartree=None,
                status="failed",
                cache_key="",
                error_message=str(exc).strip() or type(exc).__name__,
            )
    return prepared, failures, ordered_ids


def run_prepared_frames(
    backend: SinglePointCalculator,
    prepared: Sequence[PreparedFrame],
    settings: BatchSinglePointExecutionOptions,
    progress_callback: Callable[[int, int], None] | None = None,
    on_frame_start: Callable[[str, int, int], None] | None = None,
    on_frame_done: Callable[[str, float | None, str], None] | None = None,
) -> dict[str, BatchSinglePointFrameResult]:
    """Run normalized frames through the generic batch executor.

    Each resolved frame emits ``on_frame_start(frame_id, done_so_far, total)``
    immediately before its execution (or cache return), followed by
    ``progress_callback(done, total)``.  ``on_frame_done(frame_id, energy,
    status)`` fires as each frame resolves (execution return, cache hit, or
    preparation failure) so live consumers can publish incremental results.
    With sequential workers, the deterministic order is
    ``start(f0), done(1), start(f1), done(2), ...``; cache hits therefore
    emit start and done back-to-back, and failures still emit done before
    siblings continue.  With concurrent workers, start callbacks may
    interleave; a consumer's current frame is the most recently started
    frame, so that value is an approximation under parallelism.
    """
    batch_root = settings.output_dir / ".batch_sp" / scope([frame.cache_key for frame in prepared])
    groups: dict[tuple[tuple[str, ...], int, int], list[PreparedFrame]] = {}
    for frame in prepared:
        groups.setdefault((frame.symbols, frame.charge, frame.multiplicity), []).append(frame)

    records: dict[str, BatchSinglePointFrameResult] = {}
    total_frames = len(prepared)
    completed_frames = 0
    progress_lock = Lock()
    signalling = (
        progress_callback is not None or on_frame_start is not None or on_frame_done is not None
    )

    def notify_frame_start(frame_id_value: str) -> None:
        """Forward a worker start event with the global completed count."""
        with progress_lock:
            done_so_far = completed_frames
        if on_frame_start is not None:
            on_frame_start(frame_id_value, done_so_far, total_frames)

    def notify_frame_done(_group_done: int, _group_total: int) -> None:
        """Advance the global completed count after a worker result returns."""
        nonlocal completed_frames
        with progress_lock:
            completed_frames += 1
            done = completed_frames
        if progress_callback is not None:
            progress_callback(done, total_frames)

    callbacks = (
        _FrameCallbacks(
            on_start=notify_frame_start if on_frame_start is not None else None,
            on_done=notify_frame_done,
            on_result=on_frame_done,
        )
        if signalling
        else None
    )
    for group_index, group in enumerate(groups.values()):
        group_result = _run_group(
            backend,
            group,
            batch_root / f"group_{group_index:03d}",
            settings,
            callbacks,
        )
        records.update(group_result)
    return records


# ── generic batch executor glue (plan todo 17) ──────────────────────────


def _run_group(
    backend: SinglePointCalculator,
    frames: list[PreparedFrame],
    output_dir: Path,
    settings: BatchSinglePointExecutionOptions,
    callbacks: _FrameCallbacks | None = None,
) -> dict[str, BatchSinglePointFrameResult]:
    """Run one same-shape/electronic-state group through ``cccp.calculation.batch``.

    Legacy-signature adaptation: the caller-supplied ``backend`` instance is
    handed to the single-task core as the runtime backend override (the
    former ``_SignallingBackend`` proxy is gone — start signalling, cache
    serving and cache writes are handled explicitly by the executor hooks
    and the versioned cache store).
    """
    call_options = dict(settings.options)
    for key in (
        "charge",
        "multiplicity",
        "method",
        "basis",
        "solvent",
        "output_dir",
        "output_prefix",
        "max_workers",
        "cache",
        "config",
    ):
        _ = call_options.pop(key, None)

    backend_label = str(getattr(backend, "name", None) or "orca").lower()
    entries = [
        _frame_entry(index, frame, backend_label, settings)
        for index, frame in enumerate(frames)
    ]
    cache: CacheStore = (
        FileSystemCacheStore(output_dir / ".cache")
        if settings.cache
        else MemoryCacheStore()
    )
    workers = _resolve_workers(len(frames), settings)
    by_frame_index = {index: frame for index, frame in enumerate(frames)}
    frame_results: dict[str, BatchSinglePointFrameResult] = {}

    def _execute(
        request_payload: Mapping[str, object],
        _params: Any,
        item_resources: Any,
    ) -> ItemRunOutcome:
        index = int(request_payload["frame_index"])
        frame = by_frame_index[index]
        output_name = str(request_payload["output_name"])
        frame_dir = output_dir / output_name
        frame_dir.mkdir(parents=True, exist_ok=True)
        clear_stale_run_files(frame_dir, output_name)
        extras = dict(call_options)
        extras["output_name"] = output_name
        task_request = TaskRequest(
            task=TaskKind.SINGLEPOINT,
            structure=StructureInput(
                coordinates=tuple(
                    tuple(float(c) for c in row) for row in request_payload["coordinates"]
                ),
                symbols=tuple(str(s) for s in request_payload["symbols"]),
            ),
            charge=frame.charge,
            multiplicity=frame.multiplicity,
            level=_level_from_params(_params),
            backend=None,
            output_dir=frame_dir,
        )
        context = TaskContext(
            config=settings.config,
            workdir=frame_dir,
            backend=backend,
            capability_extras=extras,
        )
        result = run_singlepoint(task_request, context=context)
        if result.status != "completed" or result.energy_hartree is None:
            return ItemRunOutcome(
                success=False,
                software_version=_software_version(result),
                error_message="; ".join(result.errors) or "single-point calculation failed",
            )
        output_path = _output_path(result, frame_dir)
        payload: dict[str, Any] = {
            "energy_hartree": float(result.energy_hartree),
            "output_path": str(output_path),
        }
        artifact_paths: tuple[tuple[str, str], ...] = (
            (("output", str(output_path)),) if output_path is not None else ()
        )
        return ItemRunOutcome(
            success=True,
            payload=payload,
            software_version=_software_version(result),
            artifact_paths=artifact_paths,
        )

    def _on_item_start(entry_id: str) -> None:
        if callbacks is not None and callbacks.on_start is not None:
            callbacks.on_start(entry_id)

    def _on_item_done(item: BatchItemResult) -> None:
        frame = _frame_for_entry(item.entry_id, frames)
        if frame is None:
            return
        result = _frame_result(frame, item)
        frame_results[frame.frame_id] = result
        if callbacks is not None:
            _signal_frame_result(
                callbacks,
                frame.frame_id,
                result.energy_hartree,
                result.status,
            )
            callbacks.on_done(0, 0)

    try:
        run_batch(
            entries,
            _execute,
            cache=cache,
            resources=BatchResources(
                concurrency=workers,
                per_item_cores=1,
                total_core_budget=max(workers, 1),
            ),
            run_config=settings.config,
            on_item_start=_on_item_start if callbacks is not None else None,
            on_item_done=_on_item_done,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        message = str(exc).strip() or type(exc).__name__
        for frame in frames:
            _signal_frame_failure(callbacks, frame.frame_id)
            frame_results[frame.frame_id] = BatchSinglePointFrameResult(
                frame_id=frame.frame_id,
                energy_hartree=None,
                status="failed",
                cache_key=frame.cache_key,
                error_message=message,
            )
        return frame_results

    for frame in frames:
        if frame.frame_id not in frame_results:
            _signal_frame_failure(callbacks, frame.frame_id)
            frame_results[frame.frame_id] = BatchSinglePointFrameResult(
                frame_id=frame.frame_id,
                energy_hartree=None,
                status="failed",
                cache_key=frame.cache_key,
                error_message="batch helper returned no frame result",
            )
    return frame_results


def _frame_entry(
    index: int,
    frame: PreparedFrame,
    backend_label: str,
    settings: BatchSinglePointExecutionOptions,
) -> BatchEntry:
    parameters: dict[str, Any] = {}
    for key, value in (
        ("basis", settings.basis),
        ("solvent", settings.solvent),
    ):
        if value is not None:
            parameters[key] = value
    payload: dict[str, Any] = {
        "task": "singlepoint",
        "backend": backend_label,
        "method": settings.method,
        "parameters": parameters,
        "inputs": {"geometry": _geometry_text(frame)},
        "charge": frame.charge,
        "multiplicity": frame.multiplicity,
        "frame_index": index,
        "output_name": f"sp_{index:04d}",
        "coordinates": [[float(c) for c in row] for row in frame.coordinates],
        "symbols": list(frame.symbols),
    }
    return BatchEntry(entry_id=frame.frame_id, request=payload)


def _geometry_text(frame: PreparedFrame) -> str:
    lines = [f"{len(frame.symbols)}", frame.frame_id]
    for symbol, row in zip(frame.symbols, frame.coordinates):
        x, y, z = (float(c) for c in row)
        lines.append(f"{symbol} {x:.10f} {y:.10f} {z:.10f}")
    return "\n".join(lines)


def _level_from_params(params: Any) -> MethodSpec:
    """Rebuild the single-task level from the resolved spec.

    Only ``explicit``-source fields are projected, at their REQUESTED
    values: the single-task core renders explicit values verbatim (the
    golden-frozen translation semantics), so a batch item and a direct
    ``run_singlepoint`` call render identical backend kwargs.  Non-explicit
    fields (method defaults / run config) stay unset and are materialised
    downstream from the same method metadata, exactly like the direct path.
    """
    requested: dict[str, Any] = {}
    for resolution in getattr(params.resolved, "resolutions", ()) or ():
        if getattr(resolution, "source", None) == "explicit":
            requested[resolution.field] = resolution.requested
    return MethodSpec(
        method=params.method or "",
        basis=str(requested.get("basis") or ""),
        dispersion=requested.get("dispersion") or None,
        solvent=requested.get("solvent") or None,
        solvent_model=requested.get("solvent_model") or None,
        integration_grid=requested.get("grid") or None,
        scf=requested.get("scf_convergence") or None,
        ri_approximation=requested.get("ri_approximation") or None,
        auxiliary_basis_j=requested.get("aux_j_basis") or None,
        auxiliary_basis_c=requested.get("aux_c_basis") or None,
    )


def _software_version(result: TaskResult) -> str:
    if result.provenance is not None and result.provenance.version:
        return result.provenance.version
    return "unknown"


def _output_path(result: TaskResult, frame_dir: Path) -> Path | None:
    for artifact in result.artifacts:
        if artifact.type == "output":
            path = Path(artifact.path)
            return path if path.is_absolute() else frame_dir / path
    for artifact in result.artifacts:
        if artifact.type == "log":
            path = Path(artifact.path)
            return path if path.is_absolute() else frame_dir / path
    return None


def _frame_for_entry(
    entry_id: str, frames: Sequence[PreparedFrame]
) -> PreparedFrame | None:
    for frame in frames:
        if frame.frame_id == entry_id:
            return frame
    return None


def _resolve_workers(n_total: int, settings: BatchSinglePointExecutionOptions) -> int:
    if n_total <= 0:
        return 1
    if settings.max_workers is not None:
        return max(1, min(int(settings.max_workers), n_total))
    resources = settings.config.get("resources") if settings.config else None
    if isinstance(resources, Mapping):
        raw = resources.get("nproc")
        if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
            return min(raw, n_total)
    return min(8, n_total)


def _frame_result(
    frame: PreparedFrame,
    item: BatchItemResult,
) -> BatchSinglePointFrameResult:
    """Translate one batch item record into the executor result model."""
    if item.status == "success":
        payload = item.payload or {}
        energy = payload.get("energy_hartree")
        output_raw = payload.get("output_path")
        output_path = Path(str(output_raw)) if isinstance(output_raw, str) else None
        return BatchSinglePointFrameResult(
            frame_id=frame.frame_id,
            energy_hartree=float(energy) if isinstance(energy, (int, float)) else None,
            status="completed",
            cache_key=frame.cache_key,
            output_path=output_path,
            cache_hit=item.from_cache,
        )
    return BatchSinglePointFrameResult(
        frame_id=frame.frame_id,
        energy_hartree=None,
        status="failed",
        cache_key=frame.cache_key,
        error_message=item.error_message or "single-point calculation failed",
    )


__all__ = [
    "BatchSinglePointExecutionOptions",
    "FramePreparationOptions",
    "prepare_frames",
    "run_prepared_frames",
]
