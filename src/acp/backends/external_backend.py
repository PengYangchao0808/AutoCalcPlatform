"""Capability wrapper for external conformer-search utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from acp.backends.base import QCBackend, QCResult
from acp.backends.external import batch_process_thermo, run_shermo
from acp.backends.registry import register_backend
from cccp.qc.interfaces.isostat import IsostatInterface
from cccp.software import resolve_executable


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

        thermo = run_shermo(
            freq_output=log_file,
            sp_energy=sp_energy,
            output_dir=target_dir,
            shermo_bin=self._executable_path("shermo", "Shermo"),
            output_file=output_file,
            **kwargs,
        )
        if thermo is None:
            return QCResult(
                success=False,
                energy=sp_energy,
                log_file=log_file,
                output_file=output_file,
                error_message=f"Shermo thermochemistry failed for {log_file}",
            )

        return QCResult(
            success=True,
            energy=sp_energy,
            log_file=log_file,
            output_file=output_file,
            enthalpy=thermo.get("h_sum"),
            gibbs=thermo.get("g_sum"),
            entropy=thermo.get("s_total"),
            metadata={
                "u_sum": thermo.get("u_sum"),
                "g_conc": thermo.get("g_conc"),
                "thermo": thermo,
            },
        )

    def batch_thermochemistry(
        self,
        log_files: list[Path],
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> list[QCResult]:
        target_dir = output_dir or Path.cwd()
        executables = dict(self.config.get("executables", {}))
        shermo_config = dict(executables.get("shermo", {}))
        shermo_config["path"] = self._executable_path("shermo", "Shermo")
        executables["shermo"] = shermo_config
        config = dict(self.config)
        config["executables"] = executables
        thermo_results = batch_process_thermo(
            log_files=log_files,
            output_dir=target_dir,
            config=config,
            **kwargs,
        )

        results: list[QCResult] = []
        for log_file in log_files:
            output_file = target_dir / log_file.stem / "Shermo.sum"
            thermo = thermo_results.get(log_file.stem)
            if thermo is None:
                results.append(
                    QCResult(
                        success=False,
                        log_file=log_file,
                        output_file=output_file,
                        error_message=f"Shermo thermochemistry failed for {log_file}",
                    )
                )
                continue

            results.append(
                QCResult(
                    success=True,
                    log_file=log_file,
                    output_file=output_file,
                    enthalpy=thermo.get("h_sum"),
                    gibbs=thermo.get("g_sum"),
                    entropy=thermo.get("s_total"),
                    metadata={
                        "u_sum": thermo.get("u_sum"),
                        "g_conc": thermo.get("g_conc"),
                        "thermo": thermo,
                    },
                )
            )

        return results


register_backend(ExternalBackend)

__all__ = ["ExternalBackend"]
