"""CENSO backend wrapper — thin adapter over cccp CensoInterface.

All subprocess logic, preset handling, rcfile generation and output parsing
live in :mod:`cccp.qc.interfaces.censo`; this module only adapts the
capability interface and re-exports the public data model.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from acp.backends.base import QCBackend, ConformerSearcher
from acp.backends.registry import register_backend
from cccp.qc.interfaces.censo import (
    CensoConformerRecord,
    CensoError,
    CensoExecutionError,
    CensoInterface,
    CensoNotAvailableError,
    CensoParseError,
    CensoRunResult,
    _PART_FLAGS,
    _PART_INDEX_MAP,
    _PARSE_PRIORITY,
    _PRESETS,
    _part_index,
)

logger = logging.getLogger(__name__)


class CensoBackend(QCBackend, ConformerSearcher):
    """Backend wrapping the CENSO CLI (thin adapter over the cccp interface)."""

    name = "censo"

    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._interface = CensoInterface(dict(config), **kwargs)

        # Mirrored attributes for API compatibility (config passthrough).
        self._censo_path = self._interface._censo_path
        self._orca_path = self._interface._orca_path
        self._xtb_path = self._interface._xtb_path
        self._default_preset = self._interface._default_preset
        self._default_solvent = self._interface._default_solvent
        self._temperature = self._interface._temperature
        self._keep_all = self._interface._keep_all
        self._solvent_model = self._interface._solvent_model
        self._nproc = self._interface._nproc

    # ----- QCBackend ABC ---------------------------------------------------

    def is_available(self) -> bool:
        return self._interface.is_available()

    def get_version(self) -> str | None:
        return self._interface.get_version()

    # ----- Preset helpers --------------------------------------------------

    def _resolve_preset(self, preset: str | None) -> dict[str, Any]:
        return self._interface._resolve_preset(preset)

    # ----- rcfile generation -----------------------------------------------

    def _generate_rcfile(
        self,
        preset_cfg: dict[str, Any],
        output_dir: Path,
        charge: int,
        multiplicity: int,
        solvent: str | None,
        templated_parts: set[str] | None = None,
        solvent_model: str | None = None,
    ) -> Path:
        return self._interface._generate_rcfile(
            preset_cfg,
            output_dir,
            charge,
            multiplicity,
            solvent,
            templated_parts=templated_parts,
            solvent_model=solvent_model,
        )

    # ----- Advanced-field template injection (per-run HOME isolation) -------

    def _write_part_templates(
        self,
        output_dir: Path,
        part_templates: dict[str, list[str]],
    ) -> Path:
        return self._interface._write_part_templates(output_dir, part_templates)

    # ----- CLI construction ------------------------------------------------

    def _build_cli(
        self,
        input_xyz: Path,
        rcfile: Path,
        preset_cfg: dict[str, Any],
        nproc: int,
        temperature: float,
        solvent: str | None,
        nconf: int | None = None,
        keep_all: bool = False,
    ) -> list[str]:
        return self._interface._build_cli(
            input_xyz,
            rcfile,
            preset_cfg,
            nproc,
            temperature,
            solvent,
            nconf=nconf,
            keep_all=keep_all,
        )

    # ----- Output parsing --------------------------------------------------

    def _parse_censo_json(
        self,
        json_path: Path,
        xyz_path: Path,
    ) -> list[CensoConformerRecord]:
        return self._interface._parse_censo_json(json_path, xyz_path)

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
    "_PRESETS",
    "_PART_FLAGS",
    "_PARSE_PRIORITY",
    "_PART_INDEX_MAP",
    "_part_index",
]
