"""
CREST Interface
==============

Interface for CREST conformer search software.

Author: QCcalc Team (adapted from RPH)
"""

import subprocess
import logging
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import numpy as np

from conformer_search.qc.interfaces.base import QCInterfaceBase, QCResult
from conformer_search.utils.file_io import write_xyz, read_xyz_multiframe, write_xyz_multiframe
from conformer_search.utils import ensure_dir

logger = logging.getLogger(__name__)


class CRESTInterface(QCInterfaceBase):
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
        super().__init__(config, **kwargs)
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

    def optimize(
        self,
        coordinates: np.ndarray,
        symbols: List[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path = None,
        **kwargs
    ) -> QCResult:
        """
        Optimize geometry via CREST conformer search.

        Args:
            coordinates: Input coordinates (N, 3)
            symbols: Element symbols
            charge: Molecular charge
            multiplicity: Spin multiplicity
            output_dir: Output directory
            **kwargs: Additional CREST parameters

        Returns:
            QCResult with conformer ensemble / optimized geometry
        """
        return self.run_conformer_search(
            coordinates, symbols,
            output_dir=output_dir or Path.cwd(),
            charge=charge,
            multiplicity=multiplicity,
            **kwargs
        )

    def single_point(
        self,
        coordinates: np.ndarray,
        symbols: List[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path = None,
        **kwargs
    ) -> QCResult:
        """
        Compute single-point energy via xTB.

        CREST does not natively support single-point calculations;
        this delegates to xTB single-point energy evaluation.

        Args:
            coordinates: Molecular coordinates (N, 3)
            symbols: Element symbols
            charge: Molecular charge
            multiplicity: Spin multiplicity
            output_dir: Output directory
            **kwargs: Additional parameters

        Returns:
            QCResult with xTB single-point energy
        """
        output_dir = Path(output_dir) if output_dir else Path.cwd()
        ensure_dir(output_dir)

        input_xyz = output_dir / "crest_sp_input.xyz"
        log_file = output_dir / "crest_sp.log"

        write_xyz(input_xyz, coordinates, symbols, title="CREST SP input")

        xtb_args = [
            str(self.xtb_path),
            str(input_xyz),
            "--sp",
            "--gfn", str(self.gfn_level),
            "--charge", str(charge),
            "--uhf", str(multiplicity - 1),
            "-T", str(self.threads),
        ]

        if self.solvent:
            xtb_args.extend(["--solvent", self.solvent])

        try:
            result = subprocess.run(
                xtb_args,
                cwd=output_dir,
                capture_output=True,
                text=True,
                timeout=kwargs.get('timeout', 300)
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
            logger.error(f"CREST/xTB SP calculation failed: {e}")
            return QCResult(
                success=False,
                error_message=str(e)
            )

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
        
        crest_args = [
            str(self.exe_path),
            str(input_xyz),
            "-T", str(self.threads),
            "-gfn", str(self.gfn_level),
            "-ewin", str(self.energy_window),
            "-charge", str(charge),
            "-uhf", str(multiplicity - 1),
        ]
        
        if self.solvent:
            crest_args.extend(["-solvent", self.solvent])
        
        custom_flags = kwargs.get('crest_flags', '')
        if custom_flags:
            crest_args.extend(custom_flags.split())
        
        try:
            result = subprocess.run(
                crest_args,
                cwd=output_dir,
                capture_output=True,
                text=True,
                timeout=kwargs.get('timeout', None)
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
                        'gfn_level': self.gfn_level,
                        'energy_window': self.energy_window
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

