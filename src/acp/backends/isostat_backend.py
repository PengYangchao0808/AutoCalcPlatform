"""ISOSTAT backend wrapper — thin adapter over cccp IsostatInterface."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import final

from acp.backends.base import QCBackend, QCResult, to_qc_result
from acp.backends.registry import register_backend
from cccp.qc.interfaces.isostat import IsostatInterface

logger = logging.getLogger(__name__)


@final
class IsostatBackend(QCBackend):
    """Clustering adapter for ISOSTAT (thin wrapper over the cccp interface)."""

    name: str = "isostat"

    def __init__(self, config: Mapping[str, object], **kwargs: object) -> None:
        super().__init__(dict(config), **kwargs)
        self._interface = IsostatInterface(dict(config), **kwargs)

    def is_available(self) -> bool:
        return self._interface.is_available()

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
