"""ISOSTAT backend wrapper."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import shutil
import subprocess
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from typing import final

from acp.backends.base import QCBackend, QCResult
from acp.backends.registry import register_backend
from cccp.utils.file_io import read_xyz_multiframe

logger = logging.getLogger(__name__)


def _mapping_value(config: Mapping[str, object], key: str) -> dict[str, object]:
    value = config.get(key)
    if isinstance(value, dict):
        return value
    if isinstance(value, Mapping):
        return {str(sub_key): sub_value for sub_key, sub_value in value.items()}
    return {}


def _int_value(value: object | None, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _str_value(value: object | None, default: str) -> str:
    if value is None:
        return default
    return str(value)


def _stream_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


@final
class IsostatBackend(QCBackend):
    """Subprocess wrapper for ISOSTAT clustering."""

    name: str = "isostat"
    isostat_path: str
    timeout: int

    def __init__(self, config: Mapping[str, object], **kwargs: object) -> None:
        super().__init__(dict(config), **kwargs)

        executables = _mapping_value(config, "executables")
        isostat_config = _mapping_value(executables, "isostat")
        molclus_config = _mapping_value(executables, "molclus")
        configured_isostat_path = self.options.get("isostat_path")

        self.isostat_path = _str_value(
            configured_isostat_path
            if configured_isostat_path is not None
            else isostat_config.get("path", molclus_config.get("isostat_path", "isostat")),
            "isostat",
        )
        self.timeout = _int_value(self.options.get("timeout"), _int_value(isostat_config.get("timeout"), 300))

    @staticmethod
    def _write_process_log(log_file: Path, stdout: str | None, stderr: str | None) -> None:
        parts: list[str] = []
        if stdout:
            parts.append(stdout)
        if stderr:
            parts.append(f"STDERR:\n{stderr}")
        _ = log_file.write_text("\n".join(parts), encoding="utf-8")

    @staticmethod
    def _read_multiframe_xyz(xyz_file: Path) -> tuple[NDArray[np.float64], list[str]]:
        coordinates, symbols = read_xyz_multiframe(xyz_file)
        return np.asarray(coordinates, dtype=np.float64), list(symbols)

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
        target_dir = output_dir or ensemble_xyz.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        cluster_xyz = target_dir / "cluster.xyz"
        log_file = target_dir / "isostat.log"

        command = [
            self.isostat_path,
            str(ensemble_xyz),
            "-Edis",
            str(edis),
            "-Gdis",
            str(gdis),
            "-T",
            str(temperature),
            "-nt",
            str(nthreads),
        ]
        if nout is not None:
            command.extend(["-Nout", str(nout)])

        try:
            result = subprocess.run(
                command,
                cwd=target_dir,
                capture_output=True,
                text=True,
                timeout=_int_value(kwargs.get("timeout"), self.timeout),
                check=True,
            )
        except subprocess.TimeoutExpired as exc:
            self._write_process_log(log_file, _stream_text(exc.stdout), _stream_text(exc.stderr))
            logger.error("ISOSTAT clustering timed out")
            return QCResult(success=False, error_message="ISOSTAT clustering timed out", log_file=log_file)
        except subprocess.CalledProcessError as exc:
            self._write_process_log(log_file, _stream_text(exc.stdout), _stream_text(exc.stderr))
            logger.error("ISOSTAT clustering failed with exit code %s", exc.returncode)
            return QCResult(
                success=False,
                error_message=f"ISOSTAT clustering failed with exit code {exc.returncode}",
                log_file=log_file,
            )
        except OSError as exc:
            logger.error("ISOSTAT execution failed: %s", exc)
            return QCResult(success=False, error_message=f"ISOSTAT execution failed: {exc}")

        self._write_process_log(log_file, result.stdout, result.stderr)
        if not cluster_xyz.exists():
            return QCResult(
                success=False,
                error_message="ISOSTAT completed without producing cluster.xyz",
                log_file=log_file,
            )

        coordinates, symbols = self._read_multiframe_xyz(cluster_xyz)
        return QCResult(
            success=True,
            converged=True,
            coordinates=coordinates,
            symbols=symbols,
            output_file=cluster_xyz,
            log_file=log_file,
        )


register_backend(IsostatBackend)

__all__ = ["IsostatBackend"]
