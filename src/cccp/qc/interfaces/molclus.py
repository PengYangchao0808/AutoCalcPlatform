"""
Molclus Interface
=================

Interface for Molclus conformer searches (xTB MD + Molclus + ISOSTAT).

Author: QCcalc Team
"""

import logging
import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from numpy.typing import NDArray

from cccp.qc.interfaces.base import QCResult
from cccp.software import SoftwareNotFoundError, resolve_executable
from cccp.utils.file_io import read_xyz_multiframe
from cccp.utils.solvent_map import xtb_solvent

logger = logging.getLogger(__name__)

#: Accepted xTB MD method names. ``gfnff`` maps to the standalone ``--gfnff``
#: flag; the numeric GFN levels map to ``--gfn {level}``.
_MD_METHODS: Tuple[str, ...] = ("gfnff", "gfn0", "gfn1", "gfn2")

_GFN_LEVEL_BY_METHOD: Dict[str, int] = {"gfn0": 0, "gfn1": 1, "gfn2": 2}

#: Hard minimum number of trajectory frames; below this the MD run is
#: treated as truncated and fails fast instead of feeding a partial ensemble
#: downstream.
_MIN_TRAJECTORY_FRAMES = 50


def _md_method_args(md_method: Optional[str], gfn_level: int) -> List[str]:
    """Return the xTB method flags for a normalized MD method name.

    ``gfnff`` → ``["--gfnff"]``; ``gfn0``/``gfn1``/``gfn2`` → ``["--gfn",
    "0|1|2"]``.  When *md_method* is empty the numeric *gfn_level* fallback
    is used (legacy ``search()`` behaviour).
    """
    method = (md_method or "").strip().lower()
    if method:
        if method not in _MD_METHODS:
            raise ValueError(f"Unknown MD method {md_method!r}. Allowed: {', '.join(_MD_METHODS)}")
        if method == "gfnff":
            return ["--gfnff"]
        return ["--gfn", str(_GFN_LEVEL_BY_METHOD[method])]
    return ["--gfn", str(gfn_level)]


def _solvent_args(solvent: Optional[str], solvent_model: Optional[str]) -> List[str]:
    """Return the xTB solvation flags for the given solvent / model."""
    model = (solvent_model or "none").lower()
    if not solvent or model == "none":
        return []
    if model == "gbsa":
        return ["--gbsa", xtb_solvent(solvent)]
    return ["--alpb", xtb_solvent(solvent)]


def _mapping_value(config: Mapping[str, object], key: str) -> Dict[str, object]:
    value = config.get(key)
    if isinstance(value, dict):
        return value
    if isinstance(value, Mapping):
        return {str(sub_key): sub_value for sub_key, sub_value in value.items()}
    return {}


def _float_value(value: Any, default: float) -> float:
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


def _int_value(value: Any, default: int) -> int:
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


def _bool_value(value: Any, default: bool) -> bool:
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


def _str_value(value: Any, default: str) -> str:
    if value is None:
        return default
    return str(value)


def _stream_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def _thread_env(nproc: int) -> Dict[str, str]:
    """Environment with xTB/BLAS thread counts pinned to *nproc*.

    LSF/OpenLava job environments inject ``OMP_NUM_THREADS`` equal to
    the node's core count, which makes every xTB process spawn the whole
    node's threads regardless of command-line flags (the MD stage passes
    no ``-T``/``-P`` at all).  Pinning the env vars keeps each xTB
    process within its allocation.
    """
    env = dict(os.environ)
    env["OMP_NUM_THREADS"] = str(max(1, int(nproc)))
    env["MKL_NUM_THREADS"] = str(max(1, int(nproc)))
    env["OPENBLAS_NUM_THREADS"] = str(max(1, int(nproc)))
    return env


class MolclusInterface:
    """
    Interface for Molclus conformer searches.
    """

    def __init__(self, config: Dict[str, Any], **kwargs):
        """
        Initialize Molclus interface.

        Args:
            config: Configuration dictionary
            **kwargs: Additional parameters (temperature, time_ps, dump_fs,
                gfn_level, step_fs, hmass, shake, nvt, nproc, timeout,
                isostat_timeout)
        """
        self.config = config
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
        self.executable: Optional[Path] = resolve_executable(
            "molclus", configured_path=self.molclus_path
        )
        self.xtb_executable: Optional[Path] = resolve_executable(
            "xtb", configured_path=self.xtb_path
        )
        self.isostat_executable: Optional[Path] = resolve_executable(
            "isostat", configured_path=self.isostat_path
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
    def _first_value(*values: Any) -> Any:
        for value in values:
            if value is not None:
                return value
        return None

    @staticmethod
    def _bool_string(value: bool) -> str:
        return "true" if value else "false"

    @staticmethod
    def _write_process_log(log_file: Path, stdout: Optional[str], stderr: Optional[str]) -> None:
        parts: List[str] = []
        if stdout:
            parts.append(stdout)
        if stderr:
            parts.append(f"STDERR:\n{stderr}")
        _ = log_file.write_text("\n".join(parts), encoding="utf-8")

    @staticmethod
    def _read_multiframe_xyz(xyz_file: Path) -> Tuple[NDArray[np.float64], List[str]]:
        coordinates, symbols = read_xyz_multiframe(xyz_file)
        return np.asarray(coordinates, dtype=np.float64), list(symbols)

    def is_available(self) -> bool:
        """Return True when the Molclus binary is on PATH."""
        return self.executable is not None

    def _require(self, executable: Optional[Path], name: str) -> str:
        if executable is None:
            env_var = f"CONFSEARCH_{name.upper()}_PATH"
            raise SoftwareNotFoundError(
                f"Executable '{name}' was not found. Add '{name}' to PATH, set {env_var}, "
                f"or configure executables.{name}.path."
            )
        return str(executable)

    def _run_command(
        self,
        cmd: List[str],
        *,
        cwd: Path,
        log_file: Path,
        timeout: int,
        step_name: str,
        env: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=True,
                env=env,
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
        seed: Optional[int] = None,
    ) -> None:
        lines = [
            "$md",
            f"  temp={temperature}",
            f"  time={time_ps}",
            f"  dump={dump_fs}",
            f"  step={step_fs}",
            f"  hmass={hmass}",
            f"  shake={self._bool_string(shake)}",
            f"  nvt={self._bool_string(nvt)}",
        ]
        if seed is not None:
            # Whether the $md-block keyword is honoured depends on the xTB
            # version; the global --seed flag is the stable dual insurance.
            lines.append(f"  seed={seed}")
        lines.extend(["$end", ""])
        _ = md_input.write_text("\n".join(lines), encoding="utf-8")

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

    def search(
        self,
        initial_xyz: Path,
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Optional[Path] = None,
        **kwargs,
    ) -> QCResult:
        """
        Run the full Molclus pipeline: xTB MD → Molclus optimization → ISOSTAT.

        Args:
            initial_xyz: Input structure (single-frame XYZ).
            charge: Total charge.
            multiplicity: Spin multiplicity.
            output_dir: Working directory (defaults to the input's parent).
            **kwargs: seed/md_method/temperature/time_ps/dump_fs/step_fs/hmass/
                shake/nvt/gfn_level/xtb_timeout/solvent/solvent_model/nproc/
                xtb_arg/molclus_timeout/edis/gdis/cluster_temperature/nthreads/
                nout/isostat_timeout.

        Returns:
            QCResult whose metadata carries ``trajectory_file`` and
            ``ensemble_file``.
        """
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

            seed_value: Optional[int] = None
            if kwargs.get("seed") is not None:
                seed_value = _int_value(kwargs.get("seed"), 0)
            md_method = kwargs.get("md_method")

            self._write_md_inp(
                md_input,
                temperature=_float_value(kwargs.get("temperature"), self.temperature),
                time_ps=_float_value(kwargs.get("time_ps"), self.time_ps),
                dump_fs=_float_value(kwargs.get("dump_fs"), self.dump_fs),
                step_fs=_float_value(kwargs.get("step_fs"), self.step_fs),
                hmass=_float_value(kwargs.get("hmass"), self.hmass),
                shake=_bool_value(kwargs.get("shake"), self.shake),
                nvt=_bool_value(kwargs.get("nvt"), self.nvt),
                seed=seed_value,
            )

            gfn_level = _int_value(kwargs.get("gfn_level"), self.gfn_level)
            xtb_timeout = _int_value(kwargs.get("xtb_timeout"), self.timeout)

            xtb_cmd = [
                self._require(self.xtb_executable, "xtb"),
                molecule_xyz.name,
                "--input",
                md_input.name,
                "--omd",
            ]
            xtb_cmd.extend(_md_method_args(md_method, gfn_level))
            if seed_value is not None:
                xtb_cmd.extend(["--seed", str(seed_value)])
            xtb_cmd.extend(_solvent_args(kwargs.get("solvent"), kwargs.get("solvent_model")))
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
                env=_thread_env(max(1, self.nproc)),
            )
            if error is not None:
                return QCResult(
                    success=False, error_message=error, log_file=target_dir / "xtb_md.log"
                )

            if not xtb_trj.exists():
                return QCResult(
                    success=False,
                    error_message="xTB-MD completed without producing xtb.trj",
                    log_file=target_dir / "xtb_md.log",
                )

            _ = shutil.copyfile(xtb_trj, traj_xyz)

            nproc = _int_value(kwargs.get("nproc"), self.nproc)
            if str(md_method or "").strip().lower() == "gfnff":
                xtb_arg = _str_value(kwargs.get("xtb_arg"), "--gfnff")
            else:
                xtb_arg = _str_value(kwargs.get("xtb_arg"), "--gfn 0")

            self._write_settings_ini(
                settings_ini,
                nproc=nproc,
                xtb_arg=xtb_arg,
            )

            molclus_timeout = _int_value(kwargs.get("molclus_timeout"), self.timeout)

            error = self._run_command(
                [self._require(self.executable, "molclus")],
                cwd=target_dir,
                log_file=target_dir / "molclus.log",
                timeout=molclus_timeout,
                step_name="Molclus optimization",
                env=_thread_env(max(1, nproc)),
            )
            if error is not None:
                return QCResult(
                    success=False, error_message=error, log_file=target_dir / "molclus.log"
                )

            if not isomers_xyz.exists():
                return QCResult(
                    success=False,
                    error_message="Molclus completed without producing isomers.xyz",
                    log_file=target_dir / "molclus.log",
                )

            edis = _float_value(kwargs.get("edis"), 0.5)
            gdis = _float_value(kwargs.get("gdis"), 0.25)
            cluster_temperature = _float_value(kwargs.get("cluster_temperature"), self.temperature)
            nthreads = _int_value(
                self._first_value(kwargs.get("nthreads"), kwargs.get("nproc")), self.nproc
            )
            cluster_cmd = [
                self._require(self.isostat_executable, "isostat"),
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
                env=_thread_env(max(1, nthreads)),
            )
            if error is not None:
                return QCResult(
                    success=False, error_message=error, log_file=target_dir / "isostat.log"
                )

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
        except SoftwareNotFoundError:
            raise
        except Exception as exc:
            logger.exception("Molclus conformer search failed")
            return QCResult(success=False, error_message=str(exc))

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
        solvent: Optional[str] = None,
        solvent_model: str = "none",
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Optional[Path] = None,
    ) -> QCResult:
        """
        Run a single xTB MD trajectory (GFN-FF or GFNn) and return it.

        Only the dynamics stage is executed — no Molclus batch optimization
        and no ISOSTAT clustering (use :meth:`search` for the full pipeline).
        The trajectory is copied to ``traj.xyz`` and validated against a hard
        minimum frame count (a truncated trajectory fails fast instead of
        silently feeding a partial ensemble downstream).

        Args:
            initial_xyz: Input structure (single-frame XYZ).
            md_method: ``gfnff`` (default) / ``gfn0`` / ``gfn1`` / ``gfn2``.
            gfn_level: Numeric GFN fallback used when *md_method* is empty.
            temperature: Target temperature (K).
            time_ps: Simulation length (ps).
            dump_fs: Trajectory dump interval (fs).
            step_fs: Integration time step (fs).
            hmass: Hydrogen mass scaling.
            shake: Constrain X–H bonds via SHAKE.
            nvt: NVT ensemble (False selects NPT).
            seed: Random seed — written to the ``$md`` block and repeated as
                the global ``--seed`` flag (the block keyword is honoured
                depending on the xTB version).
            solvent: Solvent name (e.g. ``water``); ``None`` for vacuum.
            solvent_model: ``alpb`` (default) or ``gbsa``.
            charge: Total charge.
            multiplicity: Spin multiplicity.
            output_dir: Working directory (defaults to the input's parent).

        Returns:
            QCResult whose metadata carries ``trajectory_file`` and
            ``n_frames``.
        """
        target_dir = output_dir or initial_xyz.parent
        target_dir.mkdir(parents=True, exist_ok=True)

        molecule_xyz = target_dir / "molecule.xyz"
        md_input = target_dir / "md.inp"
        traj_xyz = target_dir / "traj.xyz"
        xtb_trj = target_dir / "xtb.trj"

        try:
            if initial_xyz.resolve() != molecule_xyz.resolve():
                _ = shutil.copyfile(initial_xyz, molecule_xyz)

            self._write_md_inp(
                md_input,
                temperature=temperature,
                time_ps=time_ps,
                dump_fs=dump_fs,
                step_fs=step_fs,
                hmass=hmass,
                shake=shake,
                nvt=nvt,
                seed=seed,
            )

            xtb_cmd = [
                self._require(self.xtb_executable, "xtb"),
                molecule_xyz.name,
                "--input",
                md_input.name,
                "--omd",
            ]
            xtb_cmd.extend(_md_method_args(md_method, gfn_level))
            xtb_cmd.extend(["--seed", str(seed)])
            xtb_cmd.extend(_solvent_args(solvent, solvent_model))
            if charge != 0:
                xtb_cmd.extend(["--chrg", str(charge)])
            if multiplicity > 1:
                xtb_cmd.extend(["--uhf", str(multiplicity - 1)])

            error = self._run_command(
                xtb_cmd,
                cwd=target_dir,
                log_file=target_dir / "xtb_md.log",
                timeout=self.timeout,
                step_name="xTB-MD",
                env=_thread_env(max(1, self.nproc)),
            )
            if error is not None:
                return QCResult(
                    success=False,
                    error_message=error,
                    log_file=target_dir / "xtb_md.log",
                )

            if not xtb_trj.exists():
                return QCResult(
                    success=False,
                    error_message="xTB-MD completed without producing xtb.trj",
                    log_file=target_dir / "xtb_md.log",
                )

            _ = shutil.copyfile(xtb_trj, traj_xyz)

            n_frames = self._count_xyz_frames(traj_xyz)
            if n_frames < _MIN_TRAJECTORY_FRAMES:
                hint = (
                    f"unreadable/corrupt trajectory ({traj_xyz.name})"
                    if n_frames == 0
                    else f"only {n_frames} frames"
                )
                return QCResult(
                    success=False,
                    error_message=(
                        f"xTB-MD trajectory invalid: {hint} "
                        f"(minimum {_MIN_TRAJECTORY_FRAMES} frames)"
                    ),
                    log_file=target_dir / "xtb_md.log",
                )

            return QCResult(
                success=True,
                converged=True,
                output_file=traj_xyz,
                log_file=target_dir / "xtb_md.log",
                metadata={
                    "trajectory_file": str(traj_xyz),
                    "n_frames": n_frames,
                },
            )
        except SoftwareNotFoundError:
            raise
        except Exception as exc:
            logger.exception("xTB-MD run failed")
            return QCResult(success=False, error_message=str(exc))

    @staticmethod
    def _count_xyz_frames(xyz_file: Path) -> int:
        """Return the number of frames in a trajectory XYZ file (0 on parse
        failure — a truncated/corrupt trajectory then fails the frame-count
        check in :meth:`run_md`)."""
        try:
            coordinates, symbols = read_xyz_multiframe(xyz_file)
        except Exception:
            logger.warning("Failed to parse trajectory %s", xyz_file, exc_info=True)
            return 0
        if len(symbols) == 0 or coordinates.shape[0] == 0:
            return 0
        return coordinates.shape[0] // len(symbols)
