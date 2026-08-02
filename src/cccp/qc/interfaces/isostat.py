"""
ISOSTAT Interface
=================

Interface for ISOSTAT clustering.

Author: QCcalc Team
"""

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from cccp.qc.interfaces.base import QCResult
from cccp.utils.file_io import read_xyz_multiframe

logger = logging.getLogger(__name__)

#: First float in a frame title — the per-frame energy.  ISOSTAT requires
#: Molclus-style bare-energy titles (``        -11.39433937``); ``Frame N |
#: Energy: X`` titles make it abort with "Unable to load energy from
#: comment line" (exit code 24 on the Fortran build).  The interface
#: normalises titles to the Molclus format before invoking ISOSTAT.
_TITLE_ENERGY_RE = re.compile(r"[-+]?\d+\.\d+(?:[eE][-+]?\d+)?")


def _thread_env(nthreads: int) -> Dict[str, str]:
    """Environment with BLAS/OpenMP thread counts pinned to *nthreads*.

    LSF/OpenLava job environments inject ``OMP_NUM_THREADS`` set to the
    node's full core count, which oversubscribes the node.  Pinning the env
    vars keeps the ISOSTAT process within its allocated cores.
    """
    env = dict(os.environ)
    env["OMP_NUM_THREADS"] = str(max(1, int(nthreads)))
    env["MKL_NUM_THREADS"] = str(max(1, int(nthreads)))
    env["OPENBLAS_NUM_THREADS"] = str(max(1, int(nthreads)))
    return env


def _normalise_titles_for_isostat(ensemble_xyz: Path) -> Path:
    """Rewrite frame titles as Molclus bare-energy lines for ISOSTAT.

    ISOSTAT (a Molclus component) parses the per-frame comment line as
    a bare energy (``        -11.39433937``); ``Frame N | Energy: X``
    titles (our multi-frame writers) make it abort with "Unable to
    load energy from comment line" (exit 24).  This rewrites each
    frame's title to the first float found in it — the energy — while
    leaving coordinates untouched.  Frames without a float keep their
    original title (ISOSTAT will surface the real error).

    Returns:
        A temporary sibling file that the caller owns and must clean up
        (``tempfile.mkstemp`` semantics — a ``finally`` block is the
        convention used by :meth:`IsostatInterface.cluster`).
    """
    text = ensemble_xyz.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        try:
            n_atoms = int(lines[i].strip())
        except ValueError:
            # Malformed frame header — pass through verbatim and let
            # ISOSTAT (or the caller) surface the real error.
            out.append(lines[i])
            i += 1
            continue
        out.append(lines[i])
        i += 1
        if i < len(lines):
            title = lines[i]
            match = _TITLE_ENERGY_RE.search(title)
            if match is not None:
                out.append(f"        {float(match.group()):.10f}")
            else:
                out.append(title)
            i += 1
        for _ in range(n_atoms):
            if i < len(lines):
                out.append(lines[i])
                i += 1

    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{ensemble_xyz.stem}_isostat_",
        suffix=".xyz",
        dir=str(ensemble_xyz.parent),
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    tmp_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return tmp_path


class IsostatInterface:
    """
    Interface for ISOSTAT clustering.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        isostat_path: Optional[str] = None,
        timeout: Optional[int] = None,
        **kwargs,
    ):
        """
        Initialize ISOSTAT interface.

        Args:
            config: Configuration dictionary
            isostat_path: ISOSTAT binary path (overrides config)
            timeout: Subprocess timeout in seconds
            **kwargs: Additional parameters
        """
        self.config = config
        executables = config.get("executables", {})

        isostat_cfg = executables.get("isostat", {})
        molclus_cfg = executables.get("molclus", {})
        self.exe_path = str(
            isostat_path or isostat_cfg.get("path") or molclus_cfg.get("isostat_path") or "isostat"
        )

        try:
            self.timeout = int(timeout) if timeout else int(isostat_cfg.get("timeout", 300))
        except (TypeError, ValueError):
            self.timeout = 300

    def is_available(self) -> bool:
        """Return True when the ISOSTAT binary is on PATH."""
        import shutil

        return shutil.which(self.exe_path) is not None

    def cluster(
        self,
        ensemble_xyz: Path,
        output_dir: Optional[Path] = None,
        edis: float = 0.5,
        gdis: float = 0.25,
        temperature: float = 298.15,
        nout: Optional[int] = None,
        nthreads: int = 1,
        **kwargs,
    ) -> QCResult:
        """
        Run ISOSTAT clustering on a conformer ensemble.

        Args:
            ensemble_xyz: Multi-frame input XYZ (titles are normalised to
                Molclus bare-energy format first — fixes ISOSTAT exit 24).
            output_dir: Working directory (defaults to the input's parent).
            edis: Energy distance cutoff (``-Edis``).
            gdis: Geometry distance cutoff (``-Gdis``).
            temperature: Temperature for Boltzmann weighting (``-T``).
            nout: Maximum number of output clusters (``-Nout``).
            nthreads: Number of threads (``-nt``) — also pins the
                OMP/MKL/OPENBLAS thread count in the subprocess env.
            **kwargs: ``timeout`` overrides the configured subprocess
                timeout (seconds).

        Returns:
            QCResult with clustered coordinates/symbols on success;
            ``success=False`` with a classified error message otherwise
            (timeout / non-zero exit / OSError).
        """
        target_dir = output_dir or ensemble_xyz.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        cluster_xyz = target_dir / "cluster.xyz"
        log_file = target_dir / "isostat.log"

        # ISOSTAT only understands Molclus bare-energy titles; our writers
        # emit "Frame N | Energy: X".  Normalise to a temporary input file
        # so ISOSTAT parses the per-frame energy (fixes exit 24 failures).
        isostat_input = _normalise_titles_for_isostat(ensemble_xyz)

        command = [
            self.exe_path,
            str(isostat_input),
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

        timeout = kwargs.get("timeout")
        if timeout is None:
            timeout = self.timeout
        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            timeout = self.timeout

        def _write_log(stdout: Optional[str], stderr: Optional[str]) -> None:
            parts: list[str] = []
            if stdout:
                parts.append(stdout)
            if stderr:
                parts.append(f"STDERR:\n{stderr}")
            log_file.write_text("\n".join(parts), encoding="utf-8")

        try:
            result = subprocess.run(
                command,
                cwd=target_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=True,
                env=_thread_env(nthreads),
            )
        except subprocess.TimeoutExpired as exc:
            _write_log(
                exc.stdout if exc.stdout is not None else None,
                exc.stderr if exc.stderr is not None else None,
            )
            logger.error("ISOSTAT clustering timed out after %s s", timeout)
            return QCResult(
                success=False,
                error_message=f"ISOSTAT clustering timed out after {timeout} s",
                log_file=log_file,
            )
        except subprocess.CalledProcessError as exc:
            _write_log(
                exc.stdout if exc.stdout is not None else None,
                exc.stderr if exc.stderr is not None else None,
            )
            logger.error("ISOSTAT clustering failed with exit code %s", exc.returncode)
            return QCResult(
                success=False,
                error_message=(f"ISOSTAT clustering failed with exit code {exc.returncode}"),
                log_file=log_file,
            )
        except OSError as exc:
            logger.error("ISOSTAT execution failed: %s", exc)
            return QCResult(
                success=False,
                error_message=f"ISOSTAT execution failed: {exc}",
            )
        finally:
            try:
                isostat_input.unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to clean up ISOSTAT temp input %s", isostat_input)

        _write_log(result.stdout, result.stderr)
        if not cluster_xyz.exists():
            return QCResult(
                success=False,
                error_message="ISOSTAT completed without producing cluster.xyz",
                log_file=log_file,
            )

        coordinates, symbols = read_xyz_multiframe(cluster_xyz)
        return QCResult(
            success=True,
            converged=True,
            coordinates=np.asarray(coordinates, dtype=np.float64),
            symbols=list(symbols),
            output_file=cluster_xyz,
            log_file=log_file,
        )
