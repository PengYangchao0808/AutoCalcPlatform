"""xTB backend wrapper."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from acp.backends.base import QCBackend, QCResult, to_qc_result
from acp.backends.registry import register_backend
from cccp.qc.interfaces.xtb import XTBInterface

logger = logging.getLogger(__name__)


class XTBBackend(QCBackend):
    """Capability-based wrapper around :class:`XTBInterface`."""

    name = "xtb"

    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(config, **kwargs)

        theory_preopt = config.get("theory", {}).get("preoptimization", {})
        interface_kwargs = dict(kwargs)
        interface_kwargs.setdefault("gfn_level", theory_preopt.get("gfn_level", 2))
        interface_kwargs.setdefault("solvent", None)
        interface_kwargs.setdefault("solvent_model", "none")

        self._interface = XTBInterface(config=config, **interface_kwargs)

    def is_available(self) -> bool:
        return self._interface.is_available()

    def optimize(
        self,
        coordinates: np.ndarray,
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
        coordinates: np.ndarray,
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

    def relaxed_scan(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        output_dir: Path,
        plan: object,
        charge: int = 0,
        multiplicity: int = 1,
        **kwargs: Any,
    ) -> object:
        """Delegate a reaction-coordinate relaxed scan to ``XTBInterface``.

        *plan* is a :class:`ReactionCoordinatePlan` (or a JSON-style dict the
        interface can compile). Returns the interface's
        :class:`RelaxedScanResult`.
        """
        return self._interface.relaxed_scan(
            coordinates,
            symbols,
            output_dir=output_dir or Path.cwd(),
            plan=plan,
            charge=charge,
            multiplicity=multiplicity,
            **kwargs,
        )


register_backend(XTBBackend)

__all__ = ["XTBBackend", "XTBInterface"]
