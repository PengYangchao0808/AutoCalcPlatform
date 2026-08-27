"""Frame-wise single-point execution for calculation workflows."""

# pyright: reportAny=false, reportArgumentType=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import final

import acp.backends
from acp.backends.base import SinglePointCalculator

from ._singlepoint_execution import (
    BatchSinglePointExecutionOptions,
    FramePreparationOptions,
    prepare_frames,
    run_prepared_frames,
)
from ._singlepoint_models import (
    BackendFactory,
    BatchSinglePointFrameResult,
    BatchSinglePointResult,
    FrameInput,
)


@final
class BatchSinglePointExecutor:
    """Adapt frame inputs to the shared threaded and cached SP helper."""

    def __init__(
        self,
        frames: Sequence[FrameInput] | None = None,
        method: str | None = None,
        *,
        backend_factory: BackendFactory | None = None,
        backend_name: str = "orca",
        config: Mapping[str, object] | None = None,
        output_dir: Path | str | None = None,
        basis: str | None = None,
        charge: int | None = None,
        multiplicity: int | None = None,
        symbols: Sequence[str] | None = None,
        frame_ids: Sequence[str] | None = None,
        max_workers: int | None = None,
        solvent: str | None = None,
        cache: bool = True,
        cache_profile: str = "singlepoint",
        **sp_kwargs: object,
    ) -> None:
        self._frames = list(frames) if frames is not None else None
        self._method = method
        self._basis = basis
        self._backend_factory = backend_factory
        self._backend_name = backend_name
        self._config = config
        self._output_dir = (
            Path(output_dir) if output_dir is not None else Path.cwd() / "acp_calc" / "batch_sp"
        )
        self._charge = charge
        self._multiplicity = multiplicity
        self._symbols = tuple(symbols) if symbols is not None else None
        self._frame_ids = tuple(frame_ids) if frame_ids is not None else None
        self._max_workers = max_workers
        self._solvent = solvent
        self._cache = cache
        self._cache_profile = cache_profile
        self._sp_kwargs = dict(sp_kwargs)

    def run(
        self,
        frames: Sequence[FrameInput] | None = None,
        method: str | None = None,
        *,
        output_dir: Path | str | None = None,
        basis: str | None = None,
        charge: int | None = None,
        multiplicity: int | None = None,
        symbols: Sequence[str] | None = None,
        frame_ids: Sequence[str] | None = None,
        max_workers: int | None = None,
        solvent: str | None = None,
        cache: bool | None = None,
        cache_profile: str | None = None,
        **sp_kwargs: object,
    ) -> BatchSinglePointResult:
        """Run every frame and return results keyed by the frame identifier."""
        raw_frames = list(frames) if frames is not None else list(self._frames or [])
        if not raw_frames:
            return BatchSinglePointResult({})
        resolved_method = self._method if method is None else method
        resolved_basis = self._basis if basis is None else basis
        resolved_charge = self._charge if charge is None else charge
        resolved_multiplicity = self._multiplicity if multiplicity is None else multiplicity
        resolved_symbols = self._symbols if symbols is None else tuple(symbols)
        resolved_ids = self._frame_ids if frame_ids is None else tuple(frame_ids)
        resolved_workers = self._max_workers if max_workers is None else max_workers
        resolved_solvent = self._solvent if solvent is None else solvent
        resolved_cache = self._cache if cache is None else cache
        profile = self._cache_profile if cache_profile is None else cache_profile
        options = dict(self._sp_kwargs)
        options.update(sp_kwargs)
        prepared, failures, ordered_ids = prepare_frames(
            raw_frames,
            FramePreparationOptions(
                backend_name=self._backend_name,
                method=resolved_method,
                basis=resolved_basis,
                solvent=resolved_solvent,
                charge=resolved_charge,
                multiplicity=resolved_multiplicity,
                symbols=resolved_symbols,
                frame_ids=resolved_ids,
                profile=profile,
                options=options,
            ),
        )
        records = dict(failures)
        if prepared:
            backend = self._resolve_backend()
            records.update(
                run_prepared_frames(
                    backend,
                    prepared,
                    BatchSinglePointExecutionOptions(
                        output_dir=Path(output_dir) if output_dir is not None else self._output_dir,
                        method=resolved_method,
                        basis=resolved_basis,
                        max_workers=resolved_workers,
                        solvent=resolved_solvent,
                        cache=resolved_cache,
                        config=self._config,
                        options=options,
                    ),
                )
            )
        return BatchSinglePointResult(
            {frame_id_value: records[frame_id_value] for frame_id_value in ordered_ids}
        )

    def _resolve_backend(self) -> SinglePointCalculator:
        factory = self._backend_factory or acp.backends.get_backend
        reference = factory(self._backend_name)
        if isinstance(reference, type):
            candidate = reference(dict(self._config or {}))
            if not isinstance(candidate, SinglePointCalculator):
                raise TypeError(
                    f"Backend {self._backend_name!r} does not implement single-point capability"
                )
            return candidate
        return reference


__all__ = [
    "BackendFactory",
    "BatchSinglePointExecutor",
    "BatchSinglePointFrameResult",
    "BatchSinglePointResult",
    "FrameInput",
]
