"""xTB backend wrapper."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from cccp.backends.base import QCBackend, QCResult, to_qc_result
from cccp.backends.registry import register_backend
from cccp.qc.interfaces.constraints import (
    CoordinateConstraint,
    ReactionCoordinatePlan,
)
from cccp.qc.interfaces.xtb import XTBInterface
from cccp.qc.interfaces.xtb_path import PathSearchResult, XTBPathInterface
from cccp.qc.interfaces.xtb_thermo import XTBThermoResult

logger = logging.getLogger(__name__)


def _to_mrrho_qc_result(result: XTBThermoResult, output_dir: Path) -> QCResult:
    """Translate :class:`XTBThermoResult` into ACP's :class:`QCResult`."""

    thermo_dir = output_dir / "xtb_enso"
    output_file = thermo_dir / "xtb_enso.json"
    return QCResult(
        success=result.success,
        converged=result.success,
        output_file=output_file if result.success else None,
        error_message=result.error,
        zpe=result.zpve if result.success else None,
        enthalpy=result.h_total if result.success else None,
        gibbs=result.g_total if result.success else None,
        metadata={
            "thermo": {
                "g_total": result.g_total,
                "zpve": result.zpve,
                "h_total": result.h_total,
            }
        },
    )


class XTBBackend(QCBackend):
    """Capability-based wrapper around :class:`XTBInterface`."""

    name: str = "xtb"

    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(config, **kwargs)

        theory_preopt = config.get("theory", {}).get("preoptimization", {})
        interface_kwargs = dict(kwargs)
        interface_kwargs.setdefault("gfn_level", theory_preopt.get("gfn_level", 2))
        interface_kwargs.setdefault("solvent", None)
        interface_kwargs.setdefault("solvent_model", "none")

        self._interface: XTBInterface = XTBInterface(config=config, **interface_kwargs)
        self._path_interface: XTBPathInterface = XTBPathInterface(
            config=config,
            gfn_level=interface_kwargs.get("gfn_level", 2),
            solvent=interface_kwargs.get("solvent"),
            solvent_model=interface_kwargs.get("solvent_model", "none"),
        )

    def is_available(self) -> bool:
        return self._interface.is_available()

    def optimize(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> QCResult:
        return to_qc_result(
            self._interface.optimize(
                coordinates,
                symbols,
                output_dir=output_dir or Path.cwd(),
                charge=charge,
                multiplicity=multiplicity,
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
        return to_qc_result(
            self._interface.single_point(
                coordinates,
                symbols,
                output_dir=output_dir or Path.cwd(),
                charge=charge,
                multiplicity=multiplicity,
                **kwargs,
            )
        )

    def constrained_optimize(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        output_name: str = "xtb_constrained_opt",
        constraints: Sequence[CoordinateConstraint] | None = None,
        **kwargs: Any,
    ) -> QCResult:
        if constraints is None:
            raise ValueError("constraints are required for constrained_optimize")

        return to_qc_result(
            self._interface.constrained_optimize(
                coordinates,
                symbols,
                output_dir=output_dir or Path.cwd(),
                output_name=output_name,
                constraints=constraints,
                charge=charge,
                multiplicity=multiplicity,
                **kwargs,
            )
        )

    def enso_thermo(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> QCResult:
        target_dir = output_dir or Path.cwd()
        return _to_mrrho_qc_result(
            self._interface.enso_thermo(
                coordinates,
                symbols,
                output_dir=target_dir,
                charge=charge,
                multiplicity=multiplicity,
                **kwargs,
            ),
            output_dir=target_dir,
        )

    def relaxed_scan(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        output_dir: Path,
        plan: ReactionCoordinatePlan | dict[str, object],
        charge: int = 0,
        multiplicity: int = 1,
        **kwargs: Any,
    ) -> object:
        """Delegate a reaction-coordinate relaxed scan to ``XTBInterface``.

        *plan* is a :class:`ReactionCoordinatePlan` (or a JSON-style dict the
        interface can compile). Returns the interface's
        :class:`RelaxedScanResult`.
        """
        scan_plan = kwargs.pop("scan_plan", None)
        compiled_plan: ReactionCoordinatePlan | None
        if isinstance(plan, dict):
            scan_plan = plan
            compiled_plan = None
        else:
            compiled_plan = plan

        return self._interface.relaxed_scan(
            coordinates,
            symbols,
            output_dir=output_dir or Path.cwd(),
            plan=compiled_plan,
            charge=charge,
            multiplicity=multiplicity,
            scan_plan=scan_plan if isinstance(scan_plan, dict) else None,
            **kwargs,
        )

    def path_search(
        self,
        start_xyz: Path,
        end_xyz: Path,
        output_dir: Path,
        *,
        nrun: int = 1,
        npoint: int = 25,
        anopt: int = 10,
        kpush: float = 0.003,
        kpull: float = -0.015,
        ppull: float = 0.05,
        alp: float = 1.2,
        charge: int = 0,
        multiplicity: int = 1,
        uhf: int = 0,
        gfn_level: int | None = None,
        solvent: str | None = None,
        etemp: float | None = None,
        timeout: int | None = None,
        path_inp_text: str | None = None,
        extra_args: Sequence[str] = (),
        seed: int | None = None,
    ) -> PathSearchResult:
        """Delegate an xTB ``--path`` metadynamics search to ``XTBPathInterface``.

        When *path_inp_text* is provided it is written verbatim as ``path.inp``;
        otherwise the bias block is generated from the keyword arguments.
        ``--gfn`` and ``--uhf`` are always passed on the command line.

        Returns:
            PathSearchResult from the underlying interface.
        """
        return self._path_interface.path_search(
            start_xyz,
            end_xyz,
            output_dir,
            nrun=nrun,
            npoint=npoint,
            anopt=anopt,
            kpush=kpush,
            kpull=kpull,
            ppull=ppull,
            alp=alp,
            charge=charge,
            multiplicity=multiplicity,
            uhf=uhf,
            gfn_level=gfn_level,
            solvent=solvent,
            etemp=etemp,
            timeout=timeout,
            path_inp_text=path_inp_text,
            extra_args=extra_args,
            seed=seed,
        )


register_backend(XTBBackend)

__all__ = ["PathSearchResult", "XTBBackend", "XTBInterface"]
