"""
CREST Interface
===============

Interface for CREST conformer search software.

Author: QCcalc Team (adapted from RPH)
"""

import os
import subprocess
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
import numpy as np

from cccp.qc.interfaces.base import QCResult
from cccp.software import SoftwareNotFoundError, resolve_executable
from cccp.utils.file_io import write_xyz, read_xyz_multiframe, write_xyz_multiframe
from cccp.utils import ensure_dir
from cccp.utils.solvent_map import xtb_solvent

logger = logging.getLogger(__name__)


class CRESTInterface:
    """
    Interface for CREST conformer search.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        gfn_level: int = 2,
        solvent: str = None,
        solvent_model: str = "none",
        **kwargs
    ):
        """
        Initialize CREST interface.

        Args:
            config: Configuration dictionary
            gfn_level: GFN-xTB level (0, 1, or 2)
            solvent: Solvent for xTB/CREST solvation
            solvent_model: Solvation model ("none", "alpb", "gbsa")
            **kwargs: Additional parameters
        """
        self.config = config
        executables = config.get('executables', {})

        crest_config = executables.get('crest', {})
        self.exe_path = Path(crest_config.get('path', 'crest'))
        self.executable = resolve_executable('crest', configured_path=crest_config.get('path', 'crest'))

        xtb_config = executables.get('xtb', {})
        self.xtb_path = Path(xtb_config.get('path', 'xtb'))

        self.gfn_level = gfn_level
        self.solvent = solvent
        self.solvent_model = (solvent_model or "none").lower()

        resources = config.get('resources', {})
        self.threads = kwargs.get('threads', resources.get('nproc', 16))
        self.energy_window = kwargs.get('energy_window', 6.0)

    def _require_executable(self) -> str:
        if self.executable is None:
            raise SoftwareNotFoundError(
                "CREST executable not found. Add 'crest' to PATH or configure executables.crest.path."
            )
        return str(self.executable)

    def is_available(self) -> bool:
        """Return True when the CREST binary resolved successfully."""
        return self.executable is not None

    def _solvent_args(self, solvent: Optional[str] = None) -> List[str]:
        """Return CREST solvation command-line flags based on solvent_model."""
        sol = solvent if solvent is not None else self.solvent
        if not sol or self.solvent_model == "none":
            return []
        if self.solvent_model == "gbsa":
            return ["--gbsa", xtb_solvent(sol)]
        return ["--alpb", xtb_solvent(sol)]

    def run_conformer_search(
        self,
        coordinates: np.ndarray,
        symbols: List[str],
        output_dir: Path,
        output_name: str = "crest_ensemble",
        charge: int = 0,
        multiplicity: int = 1,
        energy_window: Optional[float] = None,
        **kwargs
    ) -> QCResult:
        """
        Run CREST conformer search.

        Args:
            coordinates: Input coordinates (N, 3)
            symbols: Element symbols
            output_dir: Output directory
            output_name: Base name for output
            charge: Molecular charge
            multiplicity: Spin multiplicity
            **kwargs: Additional CREST flags

        Returns:
            QCResult with conformer ensemble
        """
        output_dir = Path(output_dir)
        ensure_dir(output_dir)

        input_xyz = output_dir / "crest_input.xyz"
        if len(coordinates) > len(symbols):
            n_frames = len(coordinates) // len(symbols)
            write_xyz_multiframe(
                input_xyz, coordinates, symbols,
                titles=[f"Stage1 conformer {i}" for i in range(n_frames)]
            )
        else:
            write_xyz(input_xyz, coordinates, symbols, title=f"CREST input for {output_name}")

        gfn_level = kwargs.get('gfn_level', self.gfn_level)
        ew = energy_window  # None = no -ewin flag (callers specify explicitly)
        executable = self._require_executable()

        crest_args = [
            executable,
            str(input_xyz),
            "-T", str(self.threads),
            "-P", str(self.threads),
            "-gfn", str(gfn_level),
            "-charge", str(charge),
            "-uhf", str(multiplicity - 1),
        ]

        if ew is not None:
            crest_args.extend(["-ewin", str(ew)])

        crest_args.extend(self._solvent_args())

        custom_flags = kwargs.get('crest_flags', '')
        if custom_flags:
            crest_args.extend(custom_flags.split())

        crest_env = os.environ.copy()
        crest_env["OMP_NUM_THREADS"] = str(self.threads)
        crest_env["MKL_NUM_THREADS"] = str(self.threads)
        crest_env["OPENBLAS_NUM_THREADS"] = str(self.threads)
        crest_env["OMP_STACKSIZE"] = "400M"

        try:
            result = subprocess.run(
                crest_args,
                cwd=output_dir,
                capture_output=True,
                text=True,
                env=crest_env,
                timeout=kwargs.get('timeout', None)
            )

            if result.returncode != 0:
                stderr_tail = result.stderr.strip()[-500:] if result.stderr else "(empty)"
                logger.error(
                    "CREST exited with code %d for %s: %s",
                    result.returncode, input_xyz, stderr_tail,
                )

            ensemble_xyz = output_dir / "crest_conformers.xyz"

            if not ensemble_xyz.exists():
                for f in output_dir.glob("*.xyz"):
                    if "conformer" in f.name.lower() or "ensemble" in f.name.lower():
                        ensemble_xyz = f
                        break

            if ensemble_xyz.exists():
                ens_coords, ens_symbols = read_xyz_multiframe(ensemble_xyz)

                if len(ens_symbols) == 0:
                    logger.warning(f"Empty ensemble file: {ensemble_xyz}")
                    return QCResult(
                        success=False,
                        error_message=f"CREST output empty: {ensemble_xyz}",
                        output_file=input_xyz
                    )

                n_conformers = len(ens_coords) // len(symbols)

                return QCResult(
                    success=True,
                    coordinates=ens_coords,
                    symbols=ens_symbols,
                    output_file=ensemble_xyz,
                    metadata={
                        'n_conformers': n_conformers,
                        'gfn_level': gfn_level,
                        'energy_window': ew
                    }
                )
            else:
                return QCResult(
                    success=False,
                    error_message=f"CREST output not found in {output_dir}",
                    output_file=input_xyz
                )

        except subprocess.TimeoutExpired:
            logger.error(f"CREST calculation timed out: {input_xyz}")
            return QCResult(
                success=False,
                error_message="CREST timed out",
                output_file=input_xyz
            )
        except Exception as e:
            logger.error(f"CREST calculation failed: {e}")
            return QCResult(
                success=False,
                error_message=str(e),
                output_file=input_xyz
            )

    def run_batch_optimization(
        self,
        coordinates: np.ndarray,
        symbols: List[str],
        output_dir: Path,
        gfn_level: int = 2,
        charge: int = 0,
        multiplicity: int = 1,
        solvent: str = None,
        additional_flags: str = None,
        energy_window: Optional[float] = None
    ) -> QCResult:
        """
        Run CREST batch optimization (-mdopt mode) on an ensemble of conformers.

        This method optimizes an existing ensemble without re-sampling, used for
        Stage 2 refinement after GFN0 screening and ISOSTAT clustering.

        Args:
            coordinates: Input coordinates (N, 3) or stacked (n_frames * N, 3)
            symbols: Element symbols
            output_dir: Output directory
            gfn_level: GFN-xTB level (0, 1, or 2)
            charge: Molecular charge
            multiplicity: Spin multiplicity
            solvent: Solvent for COSMO-RS (overrides instance solvent)
            additional_flags: Additional CREST flags
            energy_window: Energy window for conformer screening (kcal/mol).
                When provided, -ewin flag is passed to CREST.

        Returns:
            QCResult with optimized conformer ensemble
        """
        output_dir = Path(output_dir)
        ensure_dir(output_dir)

        input_xyz = output_dir / "crest_ensemble.xyz"

        if len(coordinates) > len(symbols):
            n_frames = len(coordinates) // len(symbols)
            write_xyz_multiframe(
                input_xyz, coordinates, symbols,
                titles=[f"Conformer {i}" for i in range(n_frames)]
            )
        else:
            write_xyz(input_xyz, coordinates, symbols, title="CREST mdopt input")

        executable = self._require_executable()

        crest_args = [
            executable,
            "-mdopt",
            "crest_ensemble.xyz",
            "-gfn", str(gfn_level),
            "-T", str(self.threads),
            "-P", str(self.threads),
            "-charge", str(charge),
            "-uhf", str(multiplicity - 1),
        ]

        ew = energy_window
        if ew is not None:
            crest_args.extend(["-ewin", str(ew)])

        sol = solvent if solvent is not None else self.solvent
        crest_args.extend(self._solvent_args(sol))

        if additional_flags:
            crest_args.extend(additional_flags.split())

        crest_env = os.environ.copy()
        crest_env["OMP_NUM_THREADS"] = str(self.threads)
        crest_env["MKL_NUM_THREADS"] = str(self.threads)
        crest_env["OPENBLAS_NUM_THREADS"] = str(self.threads)
        crest_env["OMP_STACKSIZE"] = "400M"

        try:
            result = subprocess.run(
                crest_args,
                cwd=output_dir,
                capture_output=True,
                text=True,
                env=crest_env,
                timeout=None
            )

            if result.returncode != 0:
                logger.warning(
                    f"CREST batch optimization returned {result.returncode}: {result.stderr}"
                )

            ensemble_xyz = output_dir / "crest_ensemble.xyz"

            if not ensemble_xyz.exists():
                return QCResult(
                    success=False,
                    error_message=f"CREST output not found: {ensemble_xyz}",
                    output_file=input_xyz
                )

            ens_coords, ens_symbols = read_xyz_multiframe(ensemble_xyz)

            if len(ens_symbols) == 0:
                logger.warning(f"Empty ensemble file: {ensemble_xyz}")
                return QCResult(
                    success=False,
                    error_message=f"CREST output empty: {ensemble_xyz}",
                    output_file=input_xyz
                )

            n_conformers = len(ens_coords) // len(symbols)

            return QCResult(
                success=True,
                coordinates=ens_coords,
                symbols=ens_symbols,
                output_file=ensemble_xyz,
                metadata={
                    'n_conformers': n_conformers,
                    'gfn_level': gfn_level
                }
            )

        except FileNotFoundError:
            logger.error(f"CREST executable not found: {self.exe_path}")
            return QCResult(
                success=False,
                error_message=f"CREST executable not found: {self.exe_path}",
                output_file=input_xyz
            )
        except Exception as e:
            logger.error(f"CREST batch optimization failed: {e}")
            return QCResult(
                success=False,
                error_message=str(e),
                output_file=input_xyz
            )
