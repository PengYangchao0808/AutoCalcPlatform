# pyright: reportAny=false, reportArgumentType=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

"""Preparation and execution helpers for frame-wise single points."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from acp.backends import batch as batch_backend
from acp.backends.base import SinglePointCalculator
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
) -> dict[str, BatchSinglePointFrameResult]:
    """Run normalized frames through the shared threaded/cache helper."""
    batch_root = settings.output_dir / ".batch_sp" / scope([frame.cache_key for frame in prepared])
    groups: dict[tuple[tuple[str, ...], int, int], list[PreparedFrame]] = {}
    for frame in prepared:
        groups.setdefault((frame.symbols, frame.charge, frame.multiplicity), []).append(frame)

    records: dict[str, BatchSinglePointFrameResult] = {}
    for group_index, group in enumerate(groups.values()):
        records.update(
            _run_group(
                backend,
                group,
                batch_root / f"group_{group_index:03d}",
                settings,
            )
        )
    return records


def _run_group(
    backend: SinglePointCalculator,
    frames: list[PreparedFrame],
    output_dir: Path,
    settings: BatchSinglePointExecutionOptions,
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
    try:
        batch_result = batch_backend.batch_single_point(
            backend,
            [frame.coordinates for frame in frames],
            list(frames[0].symbols),
            charge=frames[0].charge,
            multiplicity=frames[0].multiplicity,
            output_dir=output_dir,
            method=settings.method,
            basis=settings.basis,
            max_workers=settings.max_workers,
            solvent=settings.solvent,
            cache=settings.cache,
            config=settings.config,
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
        frame.frame_id: _frame_result(frame, by_index.get(index))
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
