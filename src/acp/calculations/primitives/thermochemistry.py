"""Unified Shermo thermochemistry primitive."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import final

from acp.calculations.contracts import ArtifactRef, CalculationResult, JsonValue
from cccp.qc.runners import run_shermo

from ._thermochemistry_input import (
    ThermochemistryInputError,
    ThermochemistryRequest,
    standard_state_correction_kcal,
    validate_request,
)
from ._thermochemistry_support import (
    ThermochemistryContext,
    ThermochemistryOutcome,
    build_metadata,
    parse_shermo_result,
    resolve_runner_settings,
    select_gibbs,
)


@final
class ThermochemistryCalculator:
    """Run Shermo and normalize its thermochemistry into ``CalculationResult``."""

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
        context = ThermochemistryContext(
            config=self._config,
            output_dir=output_dir,
            output_file=output_file,
            runner_options=self._runner_options,
            standard_state=request.standard_state,
        )
        settings = resolve_runner_settings(context)
        raw_result = run_shermo(
            freq_output=request.freq_log_path,
            sp_energy=request.sp_energy_hartree,
            output_dir=context.output_dir,
            shermo_bin=settings.shermo_bin,
            output_file=context.output_file,
            temperature_k=request.temperature,
            pressure_atm=request.pressure,
            scl_zpe=settings.scl_zpe,
            ilowfreq=settings.ilowfreq,
            imagreal=settings.imagreal,
            conc=settings.concentration,
        )
        parsed = parse_shermo_result(raw_result)
        if parsed is None:
            outcome = ThermochemistryOutcome(
                values={},
                gibbs=None,
                gibbs_source="unavailable",
                standard_delta=None,
            )
            return CalculationResult(
                energy=request.sp_energy_hartree,
                status="failed",
                errors=["Shermo returned no thermochemistry data"],
                metadata=build_metadata(request, context, settings, outcome, success=False),
            )

        gibbs, gibbs_source, standard_delta = select_gibbs(
            parsed.get("g_sum"),
            parsed.get("g_conc"),
            request.temperature,
            request.standard_state,
        )
        outcome = ThermochemistryOutcome(
            values=parsed,
            gibbs=gibbs,
            gibbs_source=gibbs_source,
            standard_delta=standard_delta,
        )
        artifacts = (
            [ArtifactRef(path=context.output_file, type="thermochemistry", source="shermo")]
            if context.output_file.is_file()
            else []
        )
        return CalculationResult(
            energy=request.sp_energy_hartree,
            artifacts=artifacts,
            status="completed",
            metadata=build_metadata(request, context, settings, outcome, success=True),
        )

    def _default_output_dir(self, freq_path: Path) -> Path:
        return freq_path.parent if freq_path.parent != Path(".") else Path.cwd()


__all__ = [
    "ThermochemistryCalculator",
    "ThermochemistryInputError",
    "standard_state_correction_kcal",
]
