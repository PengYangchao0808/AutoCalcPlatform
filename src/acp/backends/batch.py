"""Parallel batch single-point helper with geometry-keyed caching."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from numbers import Integral
from pathlib import Path
from typing import cast

import numpy as np
from numpy.typing import NDArray

from acp.backends.base import SinglePointCalculator, to_qc_result

logger = logging.getLogger(__name__)

GeometryLike = NDArray[np.float64] | Sequence[Sequence[float]]


@dataclass(frozen=True)
class BatchSpFrameResult:
    """Per-frame single-point result."""

    index: int
    structure_ref: GeometryLike
    energy_hartree: float | None
    success: bool
    error_message: str | None
    output_path: Path | None
    cache_hit: bool


@dataclass(frozen=True)
class BatchSpResult:
    """Batch single-point summary with per-frame records."""

    records: list[BatchSpFrameResult]
    n_total: int
    n_success: int
    n_failed: int
    n_cache_hits: int
    wall_time_s: float


def _mapping_or_none(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return cast(Mapping[str, object], value)


def _coerce_positive_int(value: object) -> int | None:
    if isinstance(value, Integral):
        parsed = int(value)
    elif isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed > 0 else None


def _resolve_config_value(
    config: Mapping[str, object] | None,
    *paths: tuple[str, ...],
) -> str | None:
    if not config:
        return None

    for path in paths:
        current: object = config
        for key in path:
            mapping = _mapping_or_none(current)
            if mapping is None:
                current = None
                break
            current = mapping.get(key)
        if current is not None:
            return str(current)
    return None


def _resolve_option(
    explicit: str | None,
    option_name: str,
    sp_kwargs: Mapping[str, object],
    config: Mapping[str, object] | None,
    *config_paths: tuple[str, ...],
) -> str | None:
    if explicit is not None:
        return explicit

    kwarg_value = sp_kwargs.get(option_name)
    if kwarg_value is not None:
        return str(kwarg_value)

    return _resolve_config_value(config, *config_paths)


def _resolve_workers(
    n_total: int,
    max_workers: int | None,
    config: Mapping[str, object] | None,
) -> int:
    if n_total <= 0:
        return 0

    if max_workers is not None:
        explicit_workers = _coerce_positive_int(max_workers)
        if explicit_workers is None:
            raise ValueError("max_workers must be a positive integer")
        return min(explicit_workers, n_total)

    resources = config.get("resources") if config else None
    config_workers = None
    resource_mapping = _mapping_or_none(resources)
    if resource_mapping is not None:
        config_workers = _coerce_positive_int(resource_mapping.get("nproc"))
    if config_workers is not None:
        return min(config_workers, n_total)

    return min(8, n_total)


def _normalize_coordinates(geometry: GeometryLike, n_symbols: int) -> NDArray[np.float64]:
    coordinates = np.asarray(geometry, dtype=np.float64)
    if coordinates.ndim != 2 or coordinates.shape[1] != 3:
        raise ValueError("Each geometry must be a 2D array-like object with shape (N, 3)")
    if coordinates.shape[0] != n_symbols:
        raise ValueError(
            f"Geometry atom count ({coordinates.shape[0]}) does not match symbols ({n_symbols})"
        )
    return coordinates


def _geometry_cache_key(
    symbols: Sequence[str],
    coordinates: NDArray[np.float64],
    charge: int,
    multiplicity: int,
    method: str | None,
    basis: str | None,
    solvent: str | None,
) -> str:
    rounded_coordinates: list[list[str]] = []
    for row_index in range(coordinates.shape[0]):
        rounded_row: list[str] = []
        for axis_index in range(3):
            coordinate_value = cast(np.float64, coordinates[row_index, axis_index])
            rounded_row.append(f"{float(coordinate_value):.10f}")
        rounded_coordinates.append(rounded_row)
    payload = {
        "symbols": list(symbols),
        "coordinates": rounded_coordinates,
        "charge": charge,
        "multiplicity": multiplicity,
        "method": method,
        "basis": basis,
        "solvent": solvent,
    }
    digest_input = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()


def _normalize_output_path(output_path: Path | str | None, frame_dir: Path) -> Path:
    if output_path is None:
        return frame_dir

    path = Path(output_path)
    return path if path.is_absolute() else frame_dir / path


def _make_cache_record(
    index: int,
    structure_ref: GeometryLike,
    output_root: Path,
    payload: dict[str, object],
) -> BatchSpFrameResult | None:
    energy = payload.get("energy_hartree")
    output_ref = payload.get("output_ref")
    if not isinstance(energy, (int, float)) or not isinstance(output_ref, str):
        return None

    output_path = Path(output_ref)
    resolved_output = output_path if output_path.is_absolute() else output_root / output_path
    if not resolved_output.exists():
        return None

    return BatchSpFrameResult(
        index=index,
        structure_ref=structure_ref,
        energy_hartree=float(energy),
        success=True,
        error_message=None,
        output_path=resolved_output,
        cache_hit=True,
    )


def _read_cache(
    cache_path: Path,
    index: int,
    structure_ref: GeometryLike,
    output_root: Path,
) -> BatchSpFrameResult | None:
    try:
        payload = cast(object, json.loads(cache_path.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    if not isinstance(payload, dict):
        return None
    return _make_cache_record(
        index,
        structure_ref,
        output_root,
        cast(dict[str, object], payload),
    )


def _write_cache(cache_path: Path, payload: Mapping[str, object]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=str(cache_path.parent),
        suffix=".tmp",
        delete=False,
        mode="w",
        encoding="utf-8",
    )
    try:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.close()
        os.replace(handle.name, cache_path)
    except Exception:
        handle.close()
        try:
            os.unlink(handle.name)
        except OSError:
            logger.exception("Failed to remove temporary cache file %s", handle.name)
        raise


def batch_single_point(
    backend: SinglePointCalculator,
    geometries: Sequence[GeometryLike],
    symbols: Sequence[str],
    *,
    charge: int = 0,
    multiplicity: int = 1,
    output_dir: Path | str,
    output_prefix: str = "sp",
    method: str | None = None,
    basis: str | None = None,
    max_workers: int | None = None,
    solvent: str | None = None,
    cache: bool = True,
    config: Mapping[str, object] | None = None,
    **sp_kwargs: object,
) -> BatchSpResult:
    """Run parallel single-point jobs with per-geometry SHA-256 caching.

    Worker selection follows ``max_workers`` when provided; otherwise it uses
    ``config["resources"]["nproc"]`` when present, or falls back to
    ``min(8, len(geometries))``.
    """

    started_at = time.perf_counter()
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    geometry_list = list(geometries)
    symbol_list = list(symbols)
    n_total = len(geometry_list)
    workers = _resolve_workers(n_total, max_workers, config)

    resolved_method = _resolve_option(
        method,
        "method",
        sp_kwargs,
        config,
        ("theory", "single_point", "method"),
        ("theory", "optimization", "method"),
    )
    resolved_basis = _resolve_option(
        basis,
        "basis",
        sp_kwargs,
        config,
        ("theory", "single_point", "basis"),
        ("theory", "optimization", "basis"),
    )
    resolved_solvent = _resolve_option(solvent, "solvent", sp_kwargs, None)
    cache_dir = output_root / ".cache"
    if cache:
        cache_dir.mkdir(parents=True, exist_ok=True)

    if n_total == 0:
        return BatchSpResult(
            records=[],
            n_total=0,
            n_success=0,
            n_failed=0,
            n_cache_hits=0,
            wall_time_s=time.perf_counter() - started_at,
        )

    results: list[BatchSpFrameResult | None] = [None] * n_total

    def _run_frame(index: int) -> tuple[int, BatchSpFrameResult]:
        structure_ref = geometry_list[index]
        frame_dir = output_root / f"{output_prefix}_{index:04d}"
        frame_dir.mkdir(parents=True, exist_ok=True)

        try:
            coordinates = _normalize_coordinates(structure_ref, len(symbol_list))
            cache_key = _geometry_cache_key(
                symbol_list,
                coordinates,
                charge,
                multiplicity,
                resolved_method,
                resolved_basis,
                resolved_solvent,
            )
            cache_path = cache_dir / f"{cache_key}.json"

            if cache:
                cached_record = _read_cache(cache_path, index, structure_ref, output_root)
                if cached_record is not None:
                    return index, cached_record

            call_kwargs: dict[str, object] = dict(sp_kwargs)
            call_kwargs["output_name"] = f"{output_prefix}_{index:04d}"
            if resolved_method is not None:
                call_kwargs["method"] = resolved_method
            if resolved_basis is not None:
                call_kwargs["basis"] = resolved_basis
            if resolved_solvent is not None:
                call_kwargs["solvent"] = resolved_solvent

            result = to_qc_result(
                backend.single_point(
                    coordinates,
                    symbol_list,
                    charge=charge,
                    multiplicity=multiplicity,
                    output_dir=frame_dir,
                    **call_kwargs,
                )
            )
        except Exception as exc:
            return index, BatchSpFrameResult(
                index=index,
                structure_ref=structure_ref,
                energy_hartree=None,
                success=False,
                error_message=str(exc),
                output_path=frame_dir,
                cache_hit=False,
            )

        output_path = _normalize_output_path(result.output_file, frame_dir)
        if not result.success or result.energy is None:
            return index, BatchSpFrameResult(
                index=index,
                structure_ref=structure_ref,
                energy_hartree=None,
                success=False,
                error_message=result.error_message or "single-point calculation failed",
                output_path=output_path,
                cache_hit=False,
            )

        if cache:
            try:
                output_ref = output_path.relative_to(output_root)
            except ValueError:
                output_ref = output_path
            _write_cache(
                cache_path,
                {
                    "energy_hartree": float(result.energy),
                    "output_ref": str(output_ref),
                },
            )

        return index, BatchSpFrameResult(
            index=index,
            structure_ref=structure_ref,
            energy_hartree=float(result.energy),
            success=True,
            error_message=None,
            output_path=output_path,
            cache_hit=False,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_frame, index): index for index in range(n_total)}
        for future in as_completed(futures):
            index, record = future.result()
            results[index] = record

    records = [record for record in results if record is not None]
    n_success = sum(1 for record in records if record.success)
    n_cache_hits = sum(1 for record in records if record.cache_hit)
    n_failed = n_total - n_success
    wall_time_s = time.perf_counter() - started_at

    logger.info(
        "batch_single_point: %d/%d succeeded (%d cache hits) in %.2fs",
        n_success,
        n_total,
        n_cache_hits,
        wall_time_s,
    )

    return BatchSpResult(
        records=records,
        n_total=n_total,
        n_success=n_success,
        n_failed=n_failed,
        n_cache_hits=n_cache_hits,
        wall_time_s=wall_time_s,
    )


__all__ = ["BatchSpFrameResult", "BatchSpResult", "batch_single_point"]
