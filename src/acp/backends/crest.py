"""CREST backend wrapper."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from acp.backends.base import QCBackend, QCResult, to_qc_result
from acp.backends.registry import register_backend
from conformer_search.qc.interfaces.crest import CRESTInterface
from conformer_search.utils.file_io import read_xyz

logger = logging.getLogger(__name__)


class CrestBackend(QCBackend):
    """Thin wrapper around :class:`CRESTInterface`."""

    name = "crest"

    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(config, **kwargs)

        theory_preopt = config.get("theory", {}).get("preoptimization", {})
        crest_config = config.get("executables", {}).get("crest", {})

        interface_kwargs = dict(kwargs)
        interface_kwargs.setdefault("gfn_level", crest_config.get("gfn_level", 2))
        interface_kwargs.setdefault("solvent", theory_preopt.get("solvent"))
        interface_kwargs.setdefault("solvent_model", theory_preopt.get("solvent_model", "none"))

        self._interface = CRESTInterface(config=config, **interface_kwargs)

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
        raise NotImplementedError(
            "CrestBackend does not support geometry optimization. "
            "Use xTB/ORCA backends for optimisation tasks."
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
        raise NotImplementedError(
            "CrestBackend does not support single-point energy. "
            "Use ORCA backends for SP calculations."
        )

    def frequency(
        self,
        coordinates: NDArray[np.float64] | None = None,
        symbols: list[str] | None = None,
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> QCResult:
        raise NotImplementedError(
            "CrestBackend does not support frequency calculations. "
            "Use ORCA backends."
        )

    def run_conformer_search(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        output_dir: Path | None = None,
        charge: int = 0,
        multiplicity: int = 1,
        **kwargs: Any,
    ) -> QCResult:
        return to_qc_result(
            self._interface.run_conformer_search(
            coordinates,
            symbols,
            output_dir=output_dir or Path.cwd(),
            charge=charge,
            multiplicity=multiplicity,
            **kwargs,
            )
        )

    def run_two_stage_search(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        output_dir: Path | None = None,
        charge: int = 0,
        multiplicity: int = 1,
        **kwargs: Any,
    ) -> tuple[QCResult, QCResult]:
        stage1_result, stage2_result = self._interface.run_two_stage_search(
            coordinates,
            symbols,
            output_dir=output_dir or Path.cwd(),
            charge=charge,
            multiplicity=multiplicity,
            **kwargs,
        )
        return to_qc_result(stage1_result), to_qc_result(stage2_result)

    def search(
        self,
        initial_xyz: Path,
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> Path:
        result = self.run_conformer_search(
            *read_xyz(initial_xyz),
            output_dir=output_dir or initial_xyz.parent,
            output_name=initial_xyz.stem,
            charge=charge,
            multiplicity=multiplicity,
            **kwargs,
        )
        if not result.success:
            raise RuntimeError(f"CREST search failed: {result.error_message}")
        if result.output_file is None:
            raise RuntimeError("CREST search succeeded without an ensemble output file")
        return result.output_file


register_backend(CrestBackend)

__all__ = ["CrestBackend", "CRESTInterface"]
