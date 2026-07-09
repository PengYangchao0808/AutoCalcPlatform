"""Gaussian backend wrapper."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from acp.backends.base import QCBackend, QCResult, to_qc_result
from acp.backends.registry import register_backend
from conformer_search.qc.interfaces.gaussian import GaussianInterface

logger = logging.getLogger(__name__)


class GaussianBackend(QCBackend):
    """Capability-based wrapper around :class:`GaussianInterface`."""

    name = "gaussian"

    _INTERFACE_KEYS = ("method", "basis", "dispersion", "solvent", "solvent_model")

    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(config, **kwargs)

        self._interface = self._build_interface("optimization")

    def _build_interface(self, calc_kind: str, **overrides: Any) -> GaussianInterface:
        theory = self.config.get("theory", {})
        theory_opt = theory.get("optimization", {})
        theory_nmr = theory.get("nmr", {})

        if calc_kind == "nmr":
            defaults = {
                "method": theory_nmr.get("method", theory_opt.get("method", "B3LYP")),
                "basis": theory_nmr.get("basis", theory_opt.get("basis", "def2-SVP")),
                "dispersion": theory_nmr.get("dispersion"),
                "solvent": theory_nmr.get("solvent", theory_opt.get("solvent")),
                "solvent_model": theory_nmr.get(
                    "solvent_model",
                    theory_opt.get("solvent_model", "smd"),
                ),
            }
        else:
            defaults = {
                "method": theory_opt.get("method", "B3LYP"),
                "basis": theory_opt.get("basis", "def2-SVP"),
                "dispersion": theory_opt.get("dispersion", "GD3BJ"),
                "solvent": theory_opt.get("solvent"),
                "solvent_model": theory_opt.get("solvent_model", "smd"),
            }

        interface_kwargs = dict(self.options)
        for key in self._INTERFACE_KEYS:
            value = overrides.get(key)
            if value is not None:
                interface_kwargs[key] = value

        for key, value in defaults.items():
            interface_kwargs.setdefault(key, value)

        return GaussianInterface(config=self.config, **interface_kwargs)

    def is_available(self) -> bool:
        executable = str(self._interface.exe_path)
        return shutil.which(executable) is not None

    def optimize(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> QCResult:
        target_dir = output_dir or Path.cwd()
        return to_qc_result(
            self._interface.optimize(
            coordinates,
            symbols,
            charge=charge,
            multiplicity=multiplicity,
            output_dir=target_dir,
            **kwargs,
            )
        )

    def single_point(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> QCResult:
        target_dir = output_dir or Path.cwd()
        return to_qc_result(
            self._interface.single_point(
            coordinates,
            symbols,
            charge=charge,
            multiplicity=multiplicity,
            output_dir=target_dir,
            **kwargs,
            )
        )

    def frequency(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> QCResult:
        target_dir = output_dir or Path.cwd()
        return to_qc_result(
            self._interface.frequency(
            coordinates,
            symbols,
            charge=charge,
            multiplicity=multiplicity,
            output_dir=target_dir,
            **kwargs,
            )
        )

    def nmr_shielding(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        output_name: str = "nmr",
        **kwargs: Any,
    ) -> QCResult:
        target_dir = output_dir or Path.cwd()
        interface_overrides = {
            key: kwargs.pop(key)
            for key in self._INTERFACE_KEYS
            if key in kwargs and kwargs[key] is not None
        }
        interface = self._build_interface("nmr", **interface_overrides)
        return to_qc_result(
            interface.nmr_shielding(
                coordinates,
                symbols,
                charge=charge,
                multiplicity=multiplicity,
                output_dir=target_dir,
                output_name=output_name,
                **kwargs,
            )
        )


register_backend(GaussianBackend)

__all__ = ["GaussianBackend", "GaussianInterface"]
