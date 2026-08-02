"""Molclus backend wrapper — thin adapter over cccp MolclusInterface."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import final

from acp.backends.base import QCBackend, QCResult, to_qc_result
from acp.backends.registry import register_backend
from cccp.qc.interfaces.molclus import MolclusInterface

logger = logging.getLogger(__name__)


@final
class MolclusBackend(QCBackend):
    """Conformer-search adapter for Molclus (thin wrapper over the cccp
    interface — no subprocess logic lives here)."""

    name: str = "molclus"
    molclus_path: str
    xtb_path: str
    isostat_path: str
    temperature: float
    time_ps: float
    dump_fs: float
    gfn_level: int
    step_fs: float
    hmass: float
    shake: bool
    nvt: bool
    nproc: int
    timeout: int
    isostat_timeout: int

    def __init__(self, config: Mapping[str, object], **kwargs: object) -> None:
        super().__init__(dict(config), **kwargs)
        self._interface = MolclusInterface(dict(config), **kwargs)

        # Mirrored attributes for API compatibility (config passthrough).
        self.molclus_path = self._interface.molclus_path
        self.xtb_path = self._interface.xtb_path
        self.isostat_path = self._interface.isostat_path
        self.temperature = self._interface.temperature
        self.time_ps = self._interface.time_ps
        self.dump_fs = self._interface.dump_fs
        self.gfn_level = self._interface.gfn_level
        self.step_fs = self._interface.step_fs
        self.hmass = self._interface.hmass
        self.shake = self._interface.shake
        self.nvt = self._interface.nvt
        self.nproc = self._interface.nproc
        self.timeout = self._interface.timeout
        self.isostat_timeout = self._interface.isostat_timeout

    def is_available(self) -> bool:
        return self._interface.is_available()

    def search(
        self,
        initial_xyz: Path,
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: object,
    ) -> QCResult:
        return to_qc_result(
            self._interface.search(
                initial_xyz,
                charge=charge,
                multiplicity=multiplicity,
                output_dir=output_dir,
                **kwargs,
            )
        )

    def run_md(
        self,
        initial_xyz: Path,
        *,
        md_method: str = "gfnff",
        gfn_level: int = 0,
        temperature: float = 400.0,
        time_ps: float = 100.0,
        dump_fs: float = 100.0,
        step_fs: float = 1.0,
        hmass: float = 1.0,
        shake: bool = True,
        nvt: bool = True,
        seed: int = 42,
        solvent: str | None = None,
        solvent_model: str = "none",
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
    ) -> QCResult:
        return to_qc_result(
            self._interface.run_md(
                initial_xyz,
                md_method=md_method,
                gfn_level=gfn_level,
                temperature=temperature,
                time_ps=time_ps,
                dump_fs=dump_fs,
                step_fs=step_fs,
                hmass=hmass,
                shake=shake,
                nvt=nvt,
                seed=seed,
                solvent=solvent,
                solvent_model=solvent_model,
                charge=charge,
                multiplicity=multiplicity,
                output_dir=output_dir,
            )
        )


register_backend(MolclusBackend)

__all__ = ["MolclusBackend"]
