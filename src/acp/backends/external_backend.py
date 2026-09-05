"""Capability wrapper for external conformer-search utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from acp.backends.base import QCBackend, QCResult
from acp.backends.registry import register_backend
from acp.calculations.contracts import JsonValue
from acp.calculations.primitives.thermochemistry import (
    ThermochemistryCalculator,
    ThermochemistryInputError,
)
from cccp.qc.interfaces.isostat import IsostatInterface
from cccp.software import resolve_executable


def _metadata_float(value: JsonValue) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class ExternalBackend(QCBackend):
    """Backend wrapper for ISOSTAT clustering and Shermo thermochemistry."""

    name = "external"

    def _configured_executable_path(self, key: str, default: str) -> str:
        executables = self.config.get("executables", {})
        return str(executables.get(key, {}).get("path", default))

    def _executable_path(self, key: str, default: str) -> str:
        configured_path = self._configured_executable_path(key, default)
        path = resolve_executable(key, configured_path=configured_path)
        if path is not None:
            return str(path)
        return str(Path(configured_path).expanduser().resolve())

    def is_isostat_available(self) -> bool:
        configured_path = self._configured_executable_path("isostat", "isostat")
        return resolve_executable("isostat", configured_path=configured_path) is not None

    def is_shermo_available(self) -> bool:
        configured_path = self._configured_executable_path("shermo", "Shermo")
        return resolve_executable("shermo", configured_path=configured_path) is not None

    def is_available(self) -> bool:
        return self.is_isostat_available() and self.is_shermo_available()

    def cluster(
        self,
        ensemble_xyz: Path,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> Path:
        # Legacy run_isostat used the `threads` kwarg; map it to the
        # interface's nthreads for callers of the old external route.
        if "threads" in kwargs and "nthreads" not in kwargs:
            kwargs["nthreads"] = kwargs.pop("threads")
        interface = IsostatInterface(
            self.config,
            isostat_path=self._executable_path("isostat", "isostat"),
        )
        result = interface.cluster(
            ensemble_xyz,
            output_dir or ensemble_xyz.parent,
            **kwargs,
        )
        if not result.success or result.output_file is None:
            raise RuntimeError(
                f"ISOSTAT clustering failed: {result.error_message or 'no cluster.xyz produced'}"
            )
        return Path(result.output_file)

    def thermochemistry(
        self,
        log_file: Path,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> QCResult:
        target_dir = output_dir or log_file.parent
        output_file = Path(kwargs.pop("output_file", target_dir / f"{log_file.stem}.sum"))
        sp_energy = float(kwargs.pop("sp_energy", 0.0))
        temperature = float(kwargs.pop("temperature_k", 298.15))
        pressure = float(kwargs.pop("pressure_atm", 1.0))
        standard_state = str(kwargs.pop("standard_state", "1atm"))
        runner_options: dict[str, Any] = {"shermo_bin": self._executable_path("shermo", "Shermo")}
        for key in ("scl_zpe", "ilowfreq", "imagreal", "conc"):
            if key in kwargs:
                runner_options[key] = kwargs.pop(key)

        try:
            calculation = ThermochemistryCalculator(
                self.config,
                output_dir=target_dir,
                output_file=output_file,
                runner_options=runner_options,
            ).compute(
                freq_log_path=log_file,
                sp_energy_hartree=sp_energy,
                temperature=temperature,
                pressure=pressure,
                standard_state=standard_state,
            )
        except ThermochemistryInputError as exc:
            return QCResult(
                success=False,
                energy=sp_energy,
                log_file=log_file,
                output_file=output_file,
                error_message=str(exc),
            )
        return QCResult(
            success=calculation.status == "completed",
            energy=sp_energy,
            log_file=log_file,
            output_file=output_file,
            enthalpy=_metadata_float(calculation.metadata.get("enthalpy_hartree")),
            gibbs=_metadata_float(calculation.metadata.get("gibbs_hartree")),
            entropy=_metadata_float(calculation.metadata.get("entropy_au")),
            error_message=calculation.errors[0] if calculation.errors else None,
            metadata=dict(calculation.metadata),
        )

    def batch_thermochemistry(
        self,
        log_files: list[Path],
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> list[QCResult]:
        target_dir = output_dir or Path.cwd()
        thermo_kwargs = dict(kwargs)
        if "temperature_k" not in thermo_kwargs and "temperature" in thermo_kwargs:
            thermo_kwargs["temperature_k"] = thermo_kwargs.pop("temperature")
        if "pressure_atm" not in thermo_kwargs and "pressure" in thermo_kwargs:
            thermo_kwargs["pressure_atm"] = thermo_kwargs.pop("pressure")
        results: list[QCResult] = []
        for log_file in log_files:
            results.append(
                self.thermochemistry(
                    log_file,
                    output_dir=target_dir / log_file.stem,
                    output_file=target_dir / log_file.stem / "Shermo.sum",
                    **thermo_kwargs,
                )
            )

        return results


register_backend(ExternalBackend)

__all__ = ["ExternalBackend"]
