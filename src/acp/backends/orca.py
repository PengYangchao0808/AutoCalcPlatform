"""ORCA backend wrapper."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from acp.backends.base import QCBackend, QCResult, to_qc_result
from acp.backends.registry import register_backend
from conformer_search.qc.interfaces.orca import ORCAInterface

logger = logging.getLogger(__name__)


class ORCABackend(QCBackend):
    """Capability-based wrapper around :class:`ORCAInterface`."""

    name = "orca"

    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(config, **kwargs)

        theory = config.get("theory", {})
        theory_opt = theory.get("optimization", {})
        theory_sp = theory.get("single_point", {})
        defaults = theory_opt if theory_opt.get("engine") == "orca" else theory_sp or theory_opt

        interface_kwargs = dict(kwargs)
        interface_kwargs.setdefault("method", defaults.get("method", "M062X"))
        interface_kwargs.setdefault("basis", defaults.get("basis", "def2-TZVPP"))
        interface_kwargs.setdefault("solvent", None)
        interface_kwargs.setdefault("solvent_model", "none")

        self._interface = ORCAInterface(config=config, **interface_kwargs)

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

    def opt_freq(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        output_name: str = "orca_optfreq",
        **kwargs: Any,
    ) -> QCResult:
        target_dir = output_dir or Path.cwd()
        return to_qc_result(
            self._interface.opt_freq(
                coordinates,
                symbols,
                charge=charge,
                multiplicity=multiplicity,
                output_dir=target_dir,
                output_name=output_name,
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
        return to_qc_result(
            self._interface.nmr_shielding(
                coordinates,
                symbols,
                charge=charge,
                multiplicity=multiplicity,
                output_dir=target_dir,
                output_name=output_name,
                **kwargs,
            )
        )


register_backend(ORCABackend)

__all__ = ["ORCABackend", "ORCAInterface"]
