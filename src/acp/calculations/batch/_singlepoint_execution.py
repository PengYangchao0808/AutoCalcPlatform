# pyright: reportAny=false, reportArgumentType=false, reportExplicitAny=false, reportPrivateUsage=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
"""Preparation and execution helpers for frame-wise single points."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock

import numpy as np
from numpy.typing import NDArray

from acp.backends import batch as batch_backend
from acp.backends.base import QCResult, SinglePointCalculator, to_qc_result
from acp.calculations.batch._items import BatchStructureItem, item_cache_key

from ._singlepoint_frames import frame_data, frame_id, method_signature, scope
from ._singlepoint_models import (
    BatchSinglePointFrameResult,
    FrameInput,
    PreparedFrame,
)


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


@dataclass(frozen=True, slots=True)
class _FrameSignalContext:
    """Worker context for frame identity, callbacks, and cache preservation."""

    frames: Sequence[PreparedFrame]
    callbacks: _FrameCallbacks
    cache_root: Path
    cache_paths: Mapping[int, Path]
    cached_records: Mapping[int, batch_backend.BatchSpFrameResult]


@dataclass(frozen=True, slots=True)
class _SignallingBackend:
    """Proxy that signals starts while preserving the shared helper's workers."""

    backend: SinglePointCalculator
    context: _FrameSignalContext

    def single_point(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: object,
    ) -> QCResult:
        """Signal a frame and delegate its single-point calculation."""
        output_name = kwargs.get("output_name")
        if not isinstance(output_name, str):
            raise TypeError("output_name must be a string")
        frame_index = int(output_name.rsplit("_", 1)[1])
        frame = self.context.frames[frame_index]
        if self.context.callbacks.on_start is not None:
            self.context.callbacks.on_start(frame.frame_id)

        cached_record = self.context.cached_records.get(frame_index)
        if cached_record is not None:
            return QCResult(
                success=True,
                energy=cached_record.energy_hartree,
                output_file=cached_record.output_path,
            )

        result = to_qc_result(
            self.backend.single_point(
                coordinates,
                symbols,
                charge=charge,
                multiplicity=multiplicity,
                output_dir=output_dir,
                **kwargs,
            )
        )
        cache_path = self.context.cache_paths.get(frame_index)
        if cache_path is not None and result.success and result.energy is not None:
            frame_dir = output_dir if output_dir is not None else Path.cwd()
            output_path = batch_backend._normalize_output_path(result.output_file, frame_dir)
            try:
                output_ref = output_path.relative_to(self.context.cache_root)
            except ValueError:
                output_ref = output_path
            batch_backend._write_cache(
                cache_path,
                {
                    "energy_hartree": float(result.energy),
                    "output_ref": str(output_ref),
                },
            )
        return result


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
) -> dict[str, BatchSinglePointFrameResult]:
    """Run normalized frames through the shared threaded/cache helper.

    Each resolved frame emits ``on_frame_start(frame_id, done_so_far, total)``
    immediately before its backend call (or cache return), followed by
    ``progress_callback(done, total)``.  With sequential workers, the
    deterministic order is ``start(f0), done(1), start(f1), done(2), ...``;
    cache hits therefore emit start and done back-to-back, and failures still
    emit done before siblings continue.  With concurrent workers, start
    callbacks may interleave; a consumer's current frame is the most recently
    started frame, so that value is an approximation under parallelism.
    """
    batch_root = settings.output_dir / ".batch_sp" / scope([frame.cache_key for frame in prepared])
    groups: dict[tuple[tuple[str, ...], int, int], list[PreparedFrame]] = {}
    for frame in prepared:
        groups.setdefault((frame.symbols, frame.charge, frame.multiplicity), []).append(frame)

    records: dict[str, BatchSinglePointFrameResult] = {}
    total_frames = len(prepared)
    completed_frames = 0
    progress_lock = Lock()
    signalling = progress_callback is not None or on_frame_start is not None

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


def _run_group(
    backend: SinglePointCalculator,
    frames: list[PreparedFrame],
    output_dir: Path,
    settings: BatchSinglePointExecutionOptions,
    callbacks: _FrameCallbacks | None = None,
) -> dict[str, BatchSinglePointFrameResult]:
    """Run one same-shape/electronic-state group through the batch helper."""
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

    execution_backend: SinglePointCalculator = backend
    execution_cache = settings.cache
    helper_progress: Callable[[int, int], None] | None = None
    cached_records: dict[int, batch_backend.BatchSpFrameResult] = {}
    cache_paths: dict[int, Path] = {}
    if callbacks is not None:
        helper_progress = callbacks.on_done
        execution_cache = False
        if settings.cache:
            cache_root = output_dir
            cache_dir = cache_root / ".cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            resolved_method = batch_backend._resolve_option(
                settings.method,
                "method",
                call_options,
                settings.config,
                ("theory", "single_point", "method"),
                ("theory", "optimization", "method"),
            )
            resolved_basis = batch_backend._resolve_option(
                settings.basis,
                "basis",
                call_options,
                settings.config,
                ("theory", "single_point", "basis"),
                ("theory", "optimization", "basis"),
            )
            resolved_solvent = batch_backend._resolve_option(
                settings.solvent,
                "solvent",
                call_options,
                None,
            )
            for index, frame in enumerate(frames):
                cache_key = batch_backend._geometry_cache_key(
                    frame.symbols,
                    frame.coordinates,
                    frame.charge,
                    frame.multiplicity,
                    resolved_method,
                    resolved_basis,
                    resolved_solvent,
                )
                cache_path = cache_dir / f"{cache_key}.json"
                cache_paths[index] = cache_path
                cached_record = batch_backend._read_cache(
                    cache_path,
                    index,
                    frame.coordinates,
                    cache_root,
                )
                if cached_record is not None:
                    cached_records[index] = cached_record
        execution_backend = _SignallingBackend(
            backend,
            _FrameSignalContext(
                frames=frames,
                callbacks=callbacks,
                cache_root=output_dir,
                cache_paths=cache_paths,
                cached_records=cached_records,
            ),
        )
    try:
        batch_result = batch_backend.batch_single_point(
            execution_backend,
            [frame.coordinates for frame in frames],
            list(frames[0].symbols),
            charge=frames[0].charge,
            multiplicity=frames[0].multiplicity,
            output_dir=output_dir,
            method=settings.method,
            basis=settings.basis,
            max_workers=settings.max_workers,
            solvent=settings.solvent,
            cache=execution_cache,
            config=settings.config,
            progress_callback=helper_progress,
            **call_options,  # type: ignore[arg-type]  # remaining options passed through as kwargs
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        message = str(exc).strip() or type(exc).__name__
        return {
            frame.frame_id: BatchSinglePointFrameResult(
                frame_id=frame.frame_id,
                energy_hartree=None,
                status="failed",
                cache_key=frame.cache_key,
                error_message=message,
            )
            for frame in frames
        }

    by_index = {record.index: record for record in batch_result.records}
    return {
        frame.frame_id: replace(
            _frame_result(frame, by_index.get(index)),
            cache_hit=True,
        )
        if index in cached_records
        else _frame_result(frame, by_index.get(index))
        for index, frame in enumerate(frames)
    }


def _frame_result(
    frame: PreparedFrame,
    record: batch_backend.BatchSpFrameResult | None,
) -> BatchSinglePointFrameResult:
    """Translate one shared-helper record into the executor result model."""
    if record is None:
        return BatchSinglePointFrameResult(
            frame_id=frame.frame_id,
            energy_hartree=None,
            status="failed",
            cache_key=frame.cache_key,
            error_message="batch helper returned no frame result",
        )
    success = record.success and record.energy_hartree is not None
    return BatchSinglePointFrameResult(
        frame_id=frame.frame_id,
        energy_hartree=record.energy_hartree if success else None,
        status="completed" if success else "failed",
        cache_key=frame.cache_key,
        error_message=(
            None if success else record.error_message or "single-point calculation failed"
        ),
        output_path=record.output_path,
        cache_hit=record.cache_hit,
    )


__all__ = [
    "BatchSinglePointExecutionOptions",
    "FramePreparationOptions",
    "prepare_frames",
    "run_prepared_frames",
]
