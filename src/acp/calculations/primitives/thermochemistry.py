"""Unified Shermo thermochemistry primitive.

Delegates execution and scientific normalization to the shared adapter
``cccp.qc.shermo_adapter`` / ``cccp.qc.thermo_normalize`` (plan todo 14) and
maps the outcome into ``CalculationResult``; no separate science path lives
here anymore.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import final

from acp.calculations.contracts import ArtifactRef, CalculationResult, JsonValue
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


__all__ = [
    "ThermochemistryCalculator",
    "ThermochemistryInputError",
    "standard_state_correction_kcal",
]
