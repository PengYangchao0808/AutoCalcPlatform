"""
xTB Interface
=============

Interface for xTB semi-empirical quantum chemistry calculations.

Author: QCcalc Team (adapted from RPH)
"""

import subprocess
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
import numpy as np

from conformer_search.qc.interfaces.base import QCInterfaceBase, QCResult
from conformer_search.utils.file_io import write_xyz, read_xyz
from conformer_search.utils import ensure_dir

logger = logging.getLogger(__name__)


class XTBInterface(QCInterfaceBase):
    """
    Interface for xTB calculations.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        gfn_level: int = 2,
        solvent: Optional[str] = None,
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
        super().__init__(config, **kwargs)
        
        xtb_config = self.executables.get('xtb', {})
        self.exe_path = Path(xtb_config.get('path', 'xtb'))
        
        self.gfn_level = gfn_level
        self.solvent = solvent
        
        self.nproc = kwargs.get('nproc', self.resources.get('nproc', 16))

    def optimize(
        self,
        coordinates: np.ndarray,
        symbols: List[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Optional[Path] = None,
        opt_level: str = "normal",
        **kwargs
    ) -> QCResult:
        """
        Run xTB optimization.

        Args:
            coordinates: Input coordinates
            symbols: Element symbols
            charge: Molecular charge
            multiplicity: Spin multiplicity
            output_dir: Output directory
            opt_level: Optimization level (crude, normal, tight)
            **kwargs: Additional parameters

        Returns:
            QCResult with optimized geometry
        """
        output_dir = Path(output_dir or Path.cwd())
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
            xtb_args.extend(["--solvent", self.solvent])
        
        try:
            result = subprocess.run(
                xtb_args,
                cwd=output_dir,
                capture_output=True,
                text=True,
                timeout=kwargs.get('timeout', 600)
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
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Optional[Path] = None,
        **kwargs
    ) -> QCResult:
        """
        Run xTB single-point energy calculation.

        Args:
            coordinates: Input coordinates
            symbols: Element symbols
            charge: Molecular charge
            multiplicity: Spin multiplicity
            output_dir: Output directory
            **kwargs: Additional parameters

        Returns:
            QCResult with energy
        """
        output_dir = Path(output_dir or Path.cwd())
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
            logger.error(f"xTB SP calculation failed: {e}")
            return QCResult(
                success=False,
                error_message=str(e)
            )
