"""ISOSTAT backend wrapper — thin adapter over cccp IsostatInterface."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import final

from acp.backends.base import QCBackend, QCResult, to_qc_result
from acp.backends.registry import register_backend
from cccp.qc.interfaces.isostat import (
    IsostatInterface,
    _normalise_titles_for_isostat,
)

logger = logging.getLogger(__name__)


@final
class IsostatBackend(QCBackend):
    """Clustering adapter for ISOSTAT (thin wrapper over the cccp interface)."""

    name: str = "isostat"
    isostat_path: str
    timeout: int

    def __init__(self, config: Mapping[str, object], **kwargs: object) -> None:
        super().__init__(dict(config), **kwargs)
        self._interface = IsostatInterface(dict(config), **kwargs)

        # Mirrored attributes for API compatibility (config passthrough).
        self.isostat_path = self._interface.exe_path
        self.timeout = self._interface.timeout

    @staticmethod
    def _normalise_titles_for_isostat(ensemble_xyz: Path) -> Path:
        """Rewrite frame titles as Molclus bare-energy lines for ISOSTAT.

        Delegates to the cccp interface implementation (exit-24 fix); the
        returned temporary file is owned by the caller and must be cleaned
        up (the cccp interface does so in its ``finally`` block).
        """
        return _normalise_titles_for_isostat(ensemble_xyz)

    def is_available(self) -> bool:
        return shutil.which(self.isostat_path) is not None

    def cluster(
        self,
        ensemble_xyz: Path,
        output_dir: Path | None = None,
        edis: float = 0.5,
        gdis: float = 0.25,
        temperature: float = 298.15,
        nout: int | None = None,
        nthreads: int = 1,
        **kwargs: object,
    ) -> QCResult:
        result = self._interface.cluster(
            ensemble_xyz,
            output_dir,
            edis=edis,
            gdis=gdis,
            temperature=temperature,
            nout=nout,
            nthreads=nthreads,
            timeout=kwargs.get("timeout"),
        )
        return to_qc_result(result)


register_backend(IsostatBackend)

__all__ = ["IsostatBackend"]
