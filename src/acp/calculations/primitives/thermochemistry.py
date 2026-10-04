"""Unified Shermo thermochemistry primitive + ACP compat wrapper (plan todo 22).

Delegates execution and scientific normalization to the shared adapter
``cccp.qc.shermo_adapter`` / ``cccp.qc.thermo_normalize`` (plan todo 14) and
maps the outcome into ``CalculationResult``; no separate science path lives
here anymore.  All entries — ``ThermochemistryCalculator``, the cccp task
``run_thermochemistry`` and ``ExternalBackend.thermochemistry`` — share that
single implementation with exactly one Shermo launch per calculation.

``run_thermochemistry``/``execute_thermochemistry`` are the T17–T21-style
compat wrapper around :mod:`cccp.calculation.tasks.thermochemistry`; the
six-parameter ``compute`` entry and its output-path semantics are unchanged
(``ThermochemistryCalculator`` stays public API until plan todo 23).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import final

from acp.calculations.contracts import (
    ArtifactRef,
    CalculationRequest,
    CalculationResult,
    JsonValue,
)
from acp.calculations.legacy_adapters import LegacyBinding, to_legacy_result, to_task_request
from acp.calculations.primitives._common import capability_kwargs
from cccp import calculation as _cccp_calculation
from cccp.calculation.context import TaskContext
from cccp.calculation.requests import TaskKind
from cccp.calculation.results import TaskResult
from cccp.qc.shermo_adapter import execute_shermo

from ._thermochemistry_input import (
    ThermochemistryInputError,
    ThermochemistryRequest,
    standard_state_correction_kcal,
    validate_request,
)


@final
class ThermochemistryCalculator:
    """Run Shermo via the shared adapter and normalize into ``CalculationResult``."""

    def __init__(
        self,
        config: Mapping[str, JsonValue] | None = None,
        *,
        output_dir: Path | str | None = None,
        output_file: Path | str | None = None,
        runner_options: Mapping[str, JsonValue] | None = None,
    ) -> None:
        self._config: Mapping[str, JsonValue] | None = config
        self._output_dir: Path | None = Path(output_dir) if output_dir is not None else None
        self._output_file: Path | None = Path(output_file) if output_file is not None else None
        self._runner_options: dict[str, JsonValue] = dict(runner_options or {})

    def compute(
        self,
        freq_log_path: Path | str | None,
        sp_energy_hartree: float,
        temperature: float,
        pressure: float,
        standard_state: str,
    ) -> CalculationResult:
        """Compute Shermo thermochemistry from a frequency log and SP energy."""
        request = validate_request(
            ThermochemistryRequest(
                freq_log_path=freq_log_path,
                sp_energy_hartree=sp_energy_hartree,
                temperature=temperature,
                pressure=pressure,
                standard_state=standard_state,
            )
        )
        output_dir = self._output_dir or self._default_output_dir(request.freq_log_path)
        output_file = self._output_file or output_dir / "Shermo.sum"
        run = execute_shermo(
            request.freq_log_path,
            request.sp_energy_hartree,
            temperature_k=request.temperature,
            pressure_atm=request.pressure,
            standard_state=request.standard_state,
            output_dir=output_dir,
            output_file=output_file,
            config=self._config,
            runner_options=self._runner_options,
        )
        if not run.success:
            return CalculationResult(
                energy=request.sp_energy_hartree,
                status="failed",
                errors=[run.error or "Shermo returned no thermochemistry data"],
                metadata=run.metadata,
            )
        artifacts = (
            [ArtifactRef(path=run.context.output_file, type="thermochemistry", source="shermo")]
            if run.context.output_file.is_file()
            else []
        )
        return CalculationResult(
            energy=request.sp_energy_hartree,
            artifacts=artifacts,
            status="completed",
            metadata=run.metadata,
        )

    def _default_output_dir(self, freq_path: Path) -> Path:
        return freq_path.parent if freq_path.parent != Path(".") else Path.cwd()


def run_thermochemistry(req: CalculationRequest) -> CalculationResult:
    """Run one Shermo thermochemistry calculation through the cccp task core."""
    return execute_thermochemistry(req)


def execute_thermochemistry(req: CalculationRequest) -> CalculationResult:
    """ACP compat wrapper: cccp task core + legacy envelope mapping.

    ``freq_log_path`` is the input shape (the envelope placeholder structure
    is dropped before the typed validation).  Task metadata is the shared
    ``build_metadata`` projection and wins over any payload re-emission so
    both entries keep one metadata shape.
    """
    task_request, binding = to_task_request(req, TaskKind.THERMOCHEMISTRY)
    task_request = replace(task_request, structure=None)
    context = TaskContext(
        config=binding.config,
        workdir=binding.artifact_root,
        capability_extras=capability_kwargs(req),
    )
    task_result = _cccp_calculation.run_thermochemistry(task_request, context=context)
    return legacy_result(task_result, binding)


def legacy_result(task_result: TaskResult, binding: LegacyBinding) -> CalculationResult:
    """Map one typed ``TaskResult`` back to the legacy envelope."""
    legacy = to_legacy_result(task_result, binding)
    if not task_result.metadata:
        return legacy
    metadata: dict[str, JsonValue] = dict(legacy.metadata)  # type: ignore[assignment]
    metadata.update(task_result.metadata)  # type: ignore[arg-type]
    return CalculationResult(
        energy=legacy.energy,
        coords=legacy.coords,
        frequencies=legacy.frequencies,
        artifacts=legacy.artifacts,
        status=legacy.status,
        errors=legacy.errors,
        provenance=legacy.provenance,
        metadata=metadata,
    )


__all__ = [
    "ThermochemistryCalculator",
    "ThermochemistryInputError",
    "execute_thermochemistry",
    "run_thermochemistry",
    "standard_state_correction_kcal",
]
