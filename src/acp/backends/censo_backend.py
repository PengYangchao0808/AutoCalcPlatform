"""CENSO backend wrapper — thin adapter over cccp CensoInterface.

All subprocess logic, preset handling, rcfile generation and output parsing
live in :mod:`cccp.qc.interfaces.censo`; this module only adapts the
capability interface and re-exports the public data model.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from acp.backends.base import ConformerSearcher, QCBackend
from acp.backends.registry import register_backend
from cccp.qc.interfaces.censo import (
    CensoConformerRecord,
    CensoError,
    CensoExecutionError,
    CensoInterface,
    CensoNotAvailableError,
    CensoParseError,
    CensoRunResult,
)

logger = logging.getLogger(__name__)


class CensoBackend(QCBackend, ConformerSearcher):
    """Backend wrapping the CENSO CLI (thin adapter over the cccp interface)."""

    name = "censo"

    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._interface = CensoInterface(dict(config), **kwargs)

    # ----- QCBackend ABC ---------------------------------------------------

    def is_available(self) -> bool:
        return self._interface.is_available()

    def get_version(self) -> str | None:
        return self._interface.get_version()

    # ----- Main refinement entry point -------------------------------------

    def refine_ensemble(
        self,
        ensemble_xyz: Path,
        output_dir: Path,
        *,
        preset: str | None = None,
        charge: int = 0,
        multiplicity: int = 1,
        temperature: float | None = None,
        solvent: str | None = None,
        nproc: int | None = None,
        include_refinement: bool = False,
        nconf: int | None = None,
        part_overrides: dict[str, dict[str, Any]] | None = None,
        keep_all: bool | None = None,
        part_templates: dict[str, list[str]] | None = None,
        solvent_model: str | None = None,
    ) -> CensoRunResult:
        """Run CENSO on an ensemble XYZ and return parsed results.

        Thin delegation to :meth:`CensoInterface.refine_ensemble`; see the
        cccp interface for the full parameter documentation.
        """
        return self._interface.refine_ensemble(
            ensemble_xyz,
            output_dir,
            preset=preset,
            charge=charge,
            multiplicity=multiplicity,
            temperature=temperature,
            solvent=solvent,
            nproc=nproc,
            include_refinement=include_refinement,
            nconf=nconf,
            part_overrides=part_overrides,
            keep_all=keep_all,
            part_templates=part_templates,
            solvent_model=solvent_model,
        )

    # ----- ConformerSearcher Protocol --------------------------------------

    def search(
        self,
        initial_xyz: Path,
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> Path:
        """Run CENSO ensemble generation and return the final ensemble XYZ path."""
        return self._interface.search(
            initial_xyz,
            charge=charge,
            multiplicity=multiplicity,
            output_dir=output_dir,
            **kwargs,
        )


register_backend(CensoBackend)

__all__ = [
    "CensoBackend",
    "CensoConformerRecord",
    "CensoRunResult",
    "CensoError",
    "CensoExecutionError",
    "CensoParseError",
    "CensoNotAvailableError",
]
