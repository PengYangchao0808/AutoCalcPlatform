"""
CREST Interface
==============

Interface for CREST conformer search software.

Author: QCcalc Team (adapted from RPH)
"""

import os
import subprocess
import logging
import shutil
import warnings
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import numpy as np

from conformer_search.qc.interfaces.base import QCResult
from conformer_search.qc.interfaces.xtb_thermo import run_xtb_enso, XTBThermoResult, _xyz_to_coord
from conformer_search.utils.file_io import write_xyz, read_xyz, read_xyz_multiframe, write_xyz_multiframe
from conformer_search.utils import ensure_dir
from conformer_search.utils.solvent_map import xtb_solvent

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
        **kwargs
    ):
        """
        Initialize CREST interface.

        Args:
            config: Configuration dictionary
            gfn_level: GFN-xTB level (0, 1, or 2)
            solvent: Solvent for COSMO-RS
            **kwargs: Additional parameters
        """
        self.config = config
        executables = config.get('executables', {})
        
        crest_config = executables.get('crest', {})
        self.exe_path = Path(crest_config.get('path', 'crest'))
        
        xtb_config = executables.get('xtb', {})
        self.xtb_path = Path(xtb_config.get('path', 'xtb'))
        
        self.gfn_level = gfn_level
        self.solvent = solvent
        
        resources = config.get('resources', {})
        self.threads = kwargs.get('threads', resources.get('nproc', 16))
        self.energy_window = kwargs.get('energy_window', 6.0)

    def _find_xtb_executable(self) -> Path:
        """Find xTB executable for CREST to use."""
        if self.xtb_path and self.xtb_path.exists():
            return self.xtb_path
        
        xtb_path = shutil.which('xtb')
        if xtb_path:
            return Path(xtb_path)
        
        return Path('/opt/xtb/bin/xtb')

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

        crest_args = [
            str(self.exe_path),
            str(input_xyz),
            "-T", str(self.threads),
            "-P", str(self.threads),
            "-gfn", str(gfn_level),
            "-charge", str(charge),
            "-uhf", str(multiplicity - 1),
        ]

        if ew is not None:
            crest_args.extend(["-ewin", str(ew)])
        
        if self.solvent:
            crest_args.extend(["--alpb", xtb_solvent(self.solvent)])
        
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

    def run_two_stage_search(
        self,
        coordinates: np.ndarray,
        symbols: List[str],
        output_dir: Path,
        stage1_kwargs: Dict[str, Any] = None,
        stage2_kwargs: Dict[str, Any] = None,
        charge: int = 0,
        multiplicity: int = 1
    ) -> Tuple[QCResult, QCResult]:
        """
        Run two-stage CREST search (GFN0 -> GFN2).

        Args:
            coordinates: Input coordinates
            symbols: Element symbols
            output_dir: Output directory
            stage1_kwargs: Options for GFN0 stage
            stage2_kwargs: Options for GFN2 stage
            charge: Molecular charge
            multiplicity: Spin multiplicity

        Returns:
            Tuple of (stage1_result, stage2_result)
        """
        warnings.warn(
            "run_two_stage_search() is deprecated. Use engine._step_two_stage_crest() instead.",
            DeprecationWarning, stacklevel=2
        )
        output_dir = Path(output_dir)
        ensure_dir(output_dir)
        
        stage1_dir = output_dir / "stage1_gfn0"
        stage2_dir = output_dir / "stage2_gfn2"
        
        stage1_kwargs = stage1_kwargs or {}
        stage2_kwargs = stage2_kwargs or {}
        
        stage1_result = self.run_conformer_search(
            coordinates,
            symbols,
            stage1_dir,
            output_name="stage1",
            charge=charge,
            multiplicity=multiplicity,
            gfn_level=0,
            **stage1_kwargs
        )
        
        if not stage1_result.success:
            logger.warning("Stage 1 GFN0 search failed, continuing with input")
            stage1_coords = coordinates
        else:
            stage1_coords = stage1_result.coordinates
        
        stage2_result = self.run_conformer_search(
            stage1_coords,
            symbols,
            stage2_dir,
            output_name="stage2",
            charge=charge,
            multiplicity=multiplicity,
            gfn_level=2,
            **stage2_kwargs
        )
        
        return stage1_result, stage2_result

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

        crest_args = [
            str(self.exe_path),
            "-mdopt",
            "crest_ensemble.xyz",
            "-gfn", str(gfn_level),
            "-T", str(self.threads),
            "-P", str(self.threads),
        ]

        ew = energy_window
        if ew is not None:
            crest_args.extend(["-ewin", str(ew)])

        sol = solvent if solvent is not None else self.solvent
        if sol:
            crest_args.extend(["--alpb", xtb_solvent(sol)])

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


class XTBInterface:
    """
    Interface for xTB calculations.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        gfn_level: int = 2,
        solvent: str = None,
        **kwargs
    ):
        """
        Initialize xTB interface.

        Args:
            config: Configuration dictionary
            gfn_level: GFN-xTB level (0, 1, or 2)
            solvent: Solvent for COSMO-RS
            **kwargs: Additional parameters
        """
        self.config = config
        executables = config.get('executables', {})
        
        xtb_config = executables.get('xtb', {})
        self.exe_path = Path(xtb_config.get('path', 'xtb'))
        
        self.gfn_level = gfn_level
        self.solvent = solvent
        
        resources = config.get('resources', {})
        self.nproc = kwargs.get('nproc', resources.get('nproc', 16))

    def optimize(
        self,
        coordinates: np.ndarray,
        symbols: List[str],
        output_dir: Path,
        charge: int = 0,
        multiplicity: int = 1,
        opt_level: str = "normal",
        **kwargs
    ) -> QCResult:
        """
        Run xTB optimization.

        Args:
            coordinates: Input coordinates
            symbols: Element symbols
            output_dir: Output directory
            charge: Molecular charge
            multiplicity: Spin multiplicity
            opt_level: Optimization level (crude, normal, tight)
            **kwargs: Additional parameters

        Returns:
            QCResult with optimized geometry
        """
        output_dir = Path(output_dir)
        ensure_dir(output_dir)
        
        input_xyz = output_dir / "xtb_input.xyz"
        output_file = output_dir / "xtb_output.xyz"
        log_file = output_dir / "xtb.log"
        
        write_xyz(input_xyz, coordinates, symbols, title="xTB input")
        
        xtb_args = [
            str(self.exe_path),
            str(input_xyz),
            "--opt", opt_level,
            "--gfn", str(self.gfn_level),
            "--charge", str(charge),
            "--uhf", str(multiplicity - 1),
            "-T", str(self.nproc),
            "-P", str(self.nproc),
        ]
        
        if self.solvent:
            xtb_args.extend(["--solvent", xtb_solvent(self.solvent)])
        
        try:
            result = subprocess.run(
                xtb_args,
                cwd=output_dir,
                capture_output=True,
                text=True,
                timeout=kwargs.get('timeout', None)
            )
            
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write(result.stdout)
                if result.stderr:
                    f.write("\nSTDERR:\n")
                    f.write(result.stderr)
            
            coords, syms = None, None
            energy = None
            
            if output_file.exists():
                coords, syms = read_xyz(output_file)
            
            for line in result.stdout.split('\n'):
                if 'TOTAL ENERGY' in line:
                    parts = line.split()
                    try:
                        energy = float(parts[3])
                    except (ValueError, IndexError):
                        pass
            
            return QCResult(
                success=coords is not None,
                coordinates=coords,
                symbols=syms or symbols,
                energy=energy,
                output_file=output_file,
                log_file=log_file
            )
            
        except Exception as e:
            logger.error(f"xTB optimization failed: {e}")
            return QCResult(
                success=False,
                error_message=str(e),
                output_file=input_xyz
            )

    def single_point(
        self,
        coordinates: np.ndarray,
        symbols: List[str],
        output_dir: Path,
        charge: int = 0,
        multiplicity: int = 1,
        **kwargs
    ) -> QCResult:
        """
        Run xTB single-point energy calculation.

        Args:
            coordinates: Input coordinates
            symbols: Element symbols
            output_dir: Output directory
            charge: Molecular charge
            multiplicity: Spin multiplicity
            **kwargs: Additional parameters

        Returns:
            QCResult with energy
        """
        output_dir = Path(output_dir)
        ensure_dir(output_dir)
        
        input_xyz = output_dir / "xtb_sp_input.xyz"
        log_file = output_dir / "xtb_sp.log"
        
        write_xyz(input_xyz, coordinates, symbols, title="xTB SP input")
        
        xtb_args = [
            str(self.exe_path),
            str(input_xyz),
            "--sp",
            "--gfn", str(self.gfn_level),
            "--charge", str(charge),
            "--uhf", str(multiplicity - 1),
            "-T", str(self.nproc),
        ]
        
        if self.solvent:
            xtb_args.extend(["--solvent", xtb_solvent(self.solvent)])
        
        try:
            result = subprocess.run(
                xtb_args,
                cwd=output_dir,
                capture_output=True,
                text=True,
                timeout=kwargs.get('timeout', None)
            )
            
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write(result.stdout)
                if result.stderr:
                    f.write("\nSTDERR:\n")
                    f.write(result.stderr)
            
            energy = None
            for line in result.stdout.split('\n'):
                if 'TOTAL ENERGY' in line:
                    parts = line.split()
                    try:
                        energy = float(parts[3])
                    except (ValueError, IndexError):
                        pass
            
            return QCResult(
                success=energy is not None,
                coordinates=coordinates,
                symbols=symbols,
                energy=energy,
                log_file=log_file
            )
            
        except Exception as e:
            logger.error(f"xTB SP calculation failed: {e}")
            return QCResult(
                success=False,
                error_message=str(e)
            )

    def enso_thermo(
        self,
        coordinates: np.ndarray,
        symbols: List[str],
        output_dir: Path,
        *,
        gfn_level: int = 2,
        temperature_k: float = 298.15,
        sthr: float = 50.0,
        imagthr: float = -100.0,
        charge: int = 0,
        multiplicity: int = 1,
        solvent: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> XTBThermoResult:
        """
        Run xTB single-point Hessian + MRRHO (--bhess --enso) calculation.

        Writes the input geometry to XYZ, converts to xTB .coord format,
        then calls run_xtb_enso() which handles the xTB subprocess invocation
        and xtb_enso.json parsing.

        Args:
            coordinates: Atomic coordinates (N, 3) array.
            symbols: Element symbols list.
            output_dir: Output directory (xtb SPH files go in output_dir/xtb_enso/).
            gfn_level: GFN-xTB level (0, 1, 2). Default 2.
            temperature_k: Temperature in Kelvin. Default 298.15.
            sthr: Rotational/vibrational entropy threshold. Default 50.0.
            imagthr: Imaginary frequency threshold. Default -100.0.
            charge: Molecular charge. Default 0.
            multiplicity: Spin multiplicity (determines unpaired electrons).
            solvent: ALPB solvent name. Default None.
            timeout: Subprocess timeout in seconds. Default 600.

        Returns:
            XTBThermoResult with parsed thermochemical data.
        """
        output_dir = Path(output_dir)
        xtb_enso_dir = output_dir / "xtb_enso"
        xtb_enso_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Write XYZ input
        input_xyz = xtb_enso_dir / "xtb_input.xyz"
        write_xyz(input_xyz, coordinates, symbols, title="xTB SPH input")

        # Step 2: Convert XYZ to xTB .coord format
        coord_file = xtb_enso_dir / "xtb.coord"
        _xyz_to_coord(input_xyz, coord_file)

        # Step 3: Run xTB SPH + MRRHO
        unpaired = multiplicity - 1  # Convert multiplicity to unpaired electrons
        return run_xtb_enso(
            xtb_bin=self.exe_path,
            coord_file=coord_file,
            output_dir=xtb_enso_dir,
            nproc=self.nproc,
            gfn_level=gfn_level,
            temperature_k=temperature_k,
            sthr=sthr,
            imagthr=imagthr,
            charge=charge,
            unpaired=unpaired,
            solvent=solvent,
            timeout=timeout,
        )
