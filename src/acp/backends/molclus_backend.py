"""Molclus backend wrapper."""

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
from conformer_search.utils.file_io import read_xyz_multiframe

logger = logging.getLogger(__name__)


def _mapping_value(config: Mapping[str, object], key: str) -> dict[str, object]:
    value = config.get(key)
    if isinstance(value, dict):
        return value
    if isinstance(value, Mapping):
        return {str(sub_key): sub_value for sub_key, sub_value in value.items()}
    return {}


def _float_value(value: object | None, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


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


def _bool_value(value: object | None, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
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
class MolclusBackend(QCBackend):
    """Subprocess wrapper for Molclus conformer searches."""

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

        executables = _mapping_value(config, "executables")
        molclus_config = _mapping_value(executables, "molclus")
        xtb_config = _mapping_value(executables, "xtb")
        isostat_config = _mapping_value(executables, "isostat")
        resources = _mapping_value(config, "resources")
        theory = _mapping_value(config, "theory")
        theory_preopt = _mapping_value(theory, "preoptimization")
        md_config = _mapping_value(config, "md")
        workflow_config = _mapping_value(config, "molclus")

        self.molclus_path = _str_value(molclus_config.get("path"), "molclus")
        self.xtb_path = _str_value(xtb_config.get("path"), "xtb")
        self.isostat_path = _str_value(
            self._first_value(molclus_config.get("isostat_path"), isostat_config.get("path")),
            "isostat",
        )

        self.temperature = _float_value(
            self._first_value(
                kwargs.get("temperature"),
                molclus_config.get("temperature"),
                workflow_config.get("temperature"),
                md_config.get("temperature"),
            ),
            298.15,
        )
        self.time_ps = _float_value(
            self._first_value(
                kwargs.get("time_ps"),
                molclus_config.get("time_ps"),
                workflow_config.get("time_ps"),
                md_config.get("time_ps"),
            ),
            10.0,
        )
        self.dump_fs = _float_value(
            self._first_value(
                kwargs.get("dump_fs"),
                molclus_config.get("dump_fs"),
                workflow_config.get("dump_fs"),
                md_config.get("dump_fs"),
            ),
            100.0,
        )
        self.gfn_level = _int_value(
            self._first_value(
                kwargs.get("gfn_level"),
                molclus_config.get("gfn_level"),
                workflow_config.get("gfn_level"),
                theory_preopt.get("gfn_level"),
            ),
            0,
        )
        self.step_fs = _float_value(
            self._first_value(
                kwargs.get("step_fs"),
                molclus_config.get("step_fs"),
                workflow_config.get("step_fs"),
                md_config.get("step_fs"),
            ),
            1.0,
        )
        self.hmass = _float_value(
            self._first_value(
                kwargs.get("hmass"),
                molclus_config.get("hmass"),
                workflow_config.get("hmass"),
                md_config.get("hmass"),
            ),
            1.0,
        )
        self.shake = _bool_value(
            self._first_value(
                kwargs.get("shake"),
                molclus_config.get("shake"),
                workflow_config.get("shake"),
                md_config.get("shake"),
            ),
            True,
        )
        self.nvt = _bool_value(
            self._first_value(
                kwargs.get("nvt"),
                molclus_config.get("nvt"),
                workflow_config.get("nvt"),
                md_config.get("nvt"),
            ),
            True,
        )
        self.nproc = _int_value(
            self._first_value(kwargs.get("nproc"), resources.get("nproc")),
            1,
        )
        self.timeout = _int_value(
            self._first_value(kwargs.get("timeout"), molclus_config.get("timeout")),
            300,
        )
        self.isostat_timeout = _int_value(
            self._first_value(
                kwargs.get("isostat_timeout"),
                isostat_config.get("timeout"),
                molclus_config.get("isostat_timeout"),
            ),
            self.timeout,
        )

    @staticmethod
    def _first_value(*values: object | None) -> object | None:
        for value in values:
            if value is not None:
                return value
        return None

    @staticmethod
    def _bool_string(value: bool) -> str:
        return "true" if value else "false"

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

    def _run_command(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        log_file: Path,
        timeout: int,
        step_name: str,
    ) -> str | None:
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=True,
            )
        except subprocess.TimeoutExpired as exc:
            self._write_process_log(log_file, _stream_text(exc.stdout), _stream_text(exc.stderr))
            logger.error("%s timed out after %s s", step_name, timeout)
            return f"{step_name} timed out after {timeout} s"
        except subprocess.CalledProcessError as exc:
            self._write_process_log(log_file, _stream_text(exc.stdout), _stream_text(exc.stderr))
            logger.error("%s failed with exit code %s", step_name, exc.returncode)
            return f"{step_name} failed with exit code {exc.returncode}"
        except OSError as exc:
            logger.error("%s execution failed: %s", step_name, exc)
            return f"{step_name} execution failed: {exc}"

        self._write_process_log(log_file, result.stdout, result.stderr)
        return None

    def _write_md_inp(
        self,
        md_input: Path,
        *,
        temperature: float,
        time_ps: float,
        dump_fs: float,
        step_fs: float,
        hmass: float,
        shake: bool,
        nvt: bool,
    ) -> None:
        _ = md_input.write_text(
            "\n".join(
                [
                    "$md",
                    f"  temp={temperature}",
                    f"  time={time_ps}",
                    f"  dump={dump_fs}",
                    f"  step={step_fs}",
                    f"  hmass={hmass}",
                    f"  shake={self._bool_string(shake)}",
                    f"  nvt={self._bool_string(nvt)}",
                    "$end",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def _write_settings_ini(self, settings_file: Path, *, nproc: int, xtb_arg: str) -> None:
        _ = settings_file.write_text(
            "\n".join(
                [
                    "iprog=4",
                    "itask=0",
                    f"nproc={nproc}",
                    f"xtb_arg={xtb_arg}",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def is_available(self) -> bool:
        return shutil.which(self.molclus_path) is not None

    def search(
        self,
        initial_xyz: Path,
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: object,
    ) -> QCResult:
        target_dir = output_dir or initial_xyz.parent
        target_dir.mkdir(parents=True, exist_ok=True)

        molecule_xyz = target_dir / "molecule.xyz"
        md_input = target_dir / "md.inp"
        traj_xyz = target_dir / "traj.xyz"
        xtb_trj = target_dir / "xtb.trj"
        settings_ini = target_dir / "settings.ini"
        isomers_xyz = target_dir / "isomers.xyz"
        cluster_xyz = target_dir / "cluster.xyz"

        try:
            if initial_xyz.resolve() != molecule_xyz.resolve():
                _ = shutil.copyfile(initial_xyz, molecule_xyz)

            self._write_md_inp(
                md_input,
                temperature=_float_value(kwargs.get("temperature"), self.temperature),
                time_ps=_float_value(kwargs.get("time_ps"), self.time_ps),
                dump_fs=_float_value(kwargs.get("dump_fs"), self.dump_fs),
                step_fs=_float_value(kwargs.get("step_fs"), self.step_fs),
                hmass=_float_value(kwargs.get("hmass"), self.hmass),
                shake=_bool_value(kwargs.get("shake"), self.shake),
                nvt=_bool_value(kwargs.get("nvt"), self.nvt),
            )

            gfn_level = _int_value(kwargs.get("gfn_level"), self.gfn_level)
            xtb_timeout = _int_value(kwargs.get("xtb_timeout"), self.timeout)

            xtb_cmd = [
                self.xtb_path,
                molecule_xyz.name,
                "--input",
                md_input.name,
                "--omd",
                "--gfn",
                str(gfn_level),
            ]
            if charge != 0:
                xtb_cmd.extend(["--chrg", str(charge)])
            if multiplicity > 1:
                xtb_cmd.extend(["--uhf", str(multiplicity - 1)])

            error = self._run_command(
                xtb_cmd,
                cwd=target_dir,
                log_file=target_dir / "xtb_md.log",
                timeout=xtb_timeout,
                step_name="xTB-MD",
            )
            if error is not None:
                return QCResult(success=False, error_message=error, log_file=target_dir / "xtb_md.log")

            if not xtb_trj.exists():
                return QCResult(
                    success=False,
                    error_message="xTB-MD completed without producing xtb.trj",
                    log_file=target_dir / "xtb_md.log",
                )

            _ = shutil.copyfile(xtb_trj, traj_xyz)

            nproc = _int_value(kwargs.get("nproc"), self.nproc)
            xtb_arg = _str_value(kwargs.get("xtb_arg"), "--gfn 0")

            self._write_settings_ini(
                settings_ini,
                nproc=nproc,
                xtb_arg=xtb_arg,
            )

            molclus_timeout = _int_value(kwargs.get("molclus_timeout"), self.timeout)

            error = self._run_command(
                [self.molclus_path],
                cwd=target_dir,
                log_file=target_dir / "molclus.log",
                timeout=molclus_timeout,
                step_name="Molclus optimization",
            )
            if error is not None:
                return QCResult(success=False, error_message=error, log_file=target_dir / "molclus.log")

            if not isomers_xyz.exists():
                return QCResult(
                    success=False,
                    error_message="Molclus completed without producing isomers.xyz",
                    log_file=target_dir / "molclus.log",
                )

            edis = _float_value(kwargs.get("edis"), 0.5)
            gdis = _float_value(kwargs.get("gdis"), 0.25)
            cluster_temperature = _float_value(kwargs.get("cluster_temperature"), self.temperature)
            nthreads = _int_value(self._first_value(kwargs.get("nthreads"), kwargs.get("nproc")), self.nproc)
            cluster_cmd = [
                self.isostat_path,
                isomers_xyz.name,
                "-Edis",
                str(edis),
                "-Gdis",
                str(gdis),
                "-T",
                str(cluster_temperature),
                "-nt",
                str(nthreads),
            ]
            nout = kwargs.get("nout")
            if nout is not None:
                cluster_cmd.extend(["-Nout", str(_int_value(nout, 0))])

            isostat_timeout = _int_value(kwargs.get("isostat_timeout"), self.isostat_timeout)

            error = self._run_command(
                cluster_cmd,
                cwd=target_dir,
                log_file=target_dir / "isostat.log",
                timeout=isostat_timeout,
                step_name="ISOSTAT clustering",
            )
            if error is not None:
                return QCResult(success=False, error_message=error, log_file=target_dir / "isostat.log")

            if not cluster_xyz.exists():
                return QCResult(
                    success=False,
                    error_message="ISOSTAT completed without producing cluster.xyz",
                    log_file=target_dir / "isostat.log",
                )

            coordinates, symbols = self._read_multiframe_xyz(cluster_xyz)
            return QCResult(
                success=True,
                converged=True,
                coordinates=coordinates,
                symbols=symbols,
                output_file=cluster_xyz,
                log_file=target_dir / "isostat.log",
                metadata={
                    "trajectory_file": traj_xyz,
                    "ensemble_file": isomers_xyz,
                },
            )
        except Exception as exc:
            logger.exception("Molclus conformer search failed")
            return QCResult(success=False, error_message=str(exc))


register_backend(MolclusBackend)

__all__ = ["MolclusBackend"]
