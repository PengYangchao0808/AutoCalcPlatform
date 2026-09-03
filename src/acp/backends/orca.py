"""ORCA backend wrapper."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from acp.backends.base import QCBackend, QCResult, to_qc_result
from acp.backends.registry import register_backend
from cccp.qc.interfaces.constraints import ReactionCoordinatePlan
from cccp.qc.interfaces.orca import ORCAInterface
from cccp.qc.interfaces.xtb_scan import RelaxedScanResult
from cccp.software import detect_version

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
        self._version: str | None = None
        self._version_checked = False

    def is_available(self) -> bool:
        return self._interface.is_available()

    def get_version(self) -> str | None:
        if self._version_checked:
            return self._version

        self._version = detect_version("orca", self._interface.executable)
        self._version_checked = True
        return self._version

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
        nuclei: list[str] | None = None,
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
                nuclei=nuclei,
                **kwargs,
            )
        )

    def transition_state_opt(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> object:
        """Delegate an OptTS + frequency run to ``ORCAInterface``.

        Returns the interface's :class:`TsOptResult` (energies, imaginary
        frequencies and converged geometry).
        """
        target_dir = output_dir or Path.cwd()
        return self._interface.transition_state_opt(
            coordinates,
            symbols,
            charge=charge,
            multiplicity=multiplicity,
            output_dir=target_dir,
            **kwargs,
        )

    def irc(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> object:
        """Delegate an IRC run to ``ORCAInterface``.

        Returns the interface's :class:`IrcResult` (endpoints + step counts).
        """
        target_dir = output_dir or Path.cwd()
        return self._interface.irc(
            coordinates,
            symbols,
            charge=charge,
            multiplicity=multiplicity,
            output_dir=target_dir,
            **kwargs,
        )

    def relaxed_scan(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        output_dir: Path,
        plan: ReactionCoordinatePlan,
        charge: int = 0,
        multiplicity: int = 1,
        **kwargs: Any,
    ) -> RelaxedScanResult:
        """Delegate a relaxed scan to ``ORCAInterface``.

        A plan with multiple drive coordinates is kept synchronous by the
        interface: every frame constrains all coordinates at the same
        interpolation value.
        """
        drive_coordinates = plan.drive_coordinates()
        if not drive_coordinates:
            raise ValueError("ORCA relaxed_scan requires at least one drive coordinate")
        if len(drive_coordinates) > 1:
            return self._interface.relaxed_scan(
                coordinates,
                symbols,
                plan=plan,
                points=plan.points,
                charge=charge,
                multiplicity=multiplicity,
                output_dir=output_dir,
                **kwargs,
            )
        return self._interface.relaxed_scan(
            coordinates,
            symbols,
            scan_coordinate=drive_coordinates[0],
            points=plan.points,
            charge=charge,
            multiplicity=multiplicity,
            output_dir=output_dir,
            **kwargs,
        )


register_backend(ORCABackend)

__all__ = ["ORCABackend", "ORCAInterface"]
