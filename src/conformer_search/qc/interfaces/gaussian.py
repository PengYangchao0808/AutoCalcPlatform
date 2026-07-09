"""
Gaussian Interface
===================

Interface for Gaussian quantum chemistry software.

Author: QCcalc Team (adapted from RPH)
"""

import re
import subprocess
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
import numpy as np
from numpy.typing import NDArray

from conformer_search.qc.interfaces.base import QCInterfaceBase, QCResult
from conformer_search.utils.file_io import write_gjf, read_gjf
from conformer_search.utils.geometry_tools import LogParser
from conformer_search.utils.resource_utils import mem_to_mb
from conformer_search.utils.keyword_translator import KeywordTranslator
from conformer_search.utils import ensure_dir

logger = logging.getLogger(__name__)


class GaussianInterface(QCInterfaceBase):
    """
    Interface for Gaussian 16 calculations.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        method: str = "B3LYP",
        basis: str = "def2-SVP",
        dispersion: str = "GD3BJ",
        solvent: Optional[str] = None,
        solvent_model: str = 'smd',
        **kwargs
    ):
        """
        Initialize Gaussian interface.

        Args:
            config: Configuration dictionary
            method: DFT method (default B3LYP)
            basis: Basis set (default def2-SVP)
            dispersion: Dispersion correction (default GD3BJ)
            solvent: Solvent model (default None)
            solvent_model: Solvent model type - smd, pcm, cpcm (default smd)
            **kwargs: Additional parameters (nprocshared, mem, etc.)
        """
        super().__init__(config, **kwargs)
        
        self.method = method
        self.basis = basis
        self.dispersion = dispersion
        self.solvent = solvent
        self.solvent_model = solvent_model
        
        gaussian_config = self.executables.get('gaussian', {})
        self.exe_path = Path(gaussian_config.get('path', 'g16'))
        self.use_wrapper = gaussian_config.get('use_wrapper', False)
        self.wrapper_script = gaussian_config.get('wrapper_path', './scripts/run_g16_worker.sh')
        
        self.nprocshared = kwargs.get('nprocshared', self.resources.get('nproc', 16))
        self.mem_str = kwargs.get('mem', self.resources.get('mem', '16GB'))
        self.mem_mb = mem_to_mb(self.mem_str)
        
        self.charge = kwargs.get('charge', 0)
        self.multiplicity = kwargs.get('multiplicity', 1)

    def _build_route_line(self, calc_type: str = 'opt') -> str:
        """
        Build Gaussian route line.

        Args:
            calc_type: Calculation type (opt, freq, sp, etc.)

        Returns:
            Route line string
        """
        basis = KeywordTranslator.to_gaussian_basis(self.basis)
        dispersion = KeywordTranslator.to_gaussian_dispersion(self.dispersion)
        solvent = KeywordTranslator.to_gaussian_solvent(self.solvent, self.solvent_model) if self.solvent else ''

        route_parts = [self.method, basis]
        if dispersion:
            route_parts.append(dispersion)
        if solvent:
            route_parts.append(solvent)

        calc_type_map = {
            'opt': 'Opt',
            'freq': 'Freq',
            'sp': 'SP',
            'nmr': 'NMR=GIAO',
            'optfreq': 'Opt Freq',
        }

        route = f"{route_parts[0]}/{route_parts[1]}"
        if len(route_parts) > 2:
            route = f"{route} {' '.join(route_parts[2:])}"
        route = f"{route} {calc_type_map.get(calc_type, calc_type)}"
        return f"#{route}"

    def _write_input(
        self,
        input_file: Path,
        coordinates: NDArray[np.float64],
        symbols: List[str],
        calc_type: str = 'opt',
        charge: Optional[int] = None,
        multiplicity: Optional[int] = None
    ):
        """
        Write Gaussian input file.

        Args:
            input_file: Output GJF file path
            coordinates: Molecular coordinates
            symbols: Element symbols
            calc_type: Calculation type
            charge: Molecular charge (uses self.charge if None)
            multiplicity: Spin multiplicity (uses self.multiplicity if None)
        """
        charge = charge if charge is not None else self.charge
        multiplicity = multiplicity if multiplicity is not None else self.multiplicity
        
        route_line = self._build_route_line(calc_type)
        
        link_lines = ""
        if calc_type == 'optfreq':
            link_lines = "--link1--\n"
        
        ensure_dir(input_file.parent)
        
        with open(input_file, 'w', encoding='utf-8') as f:
            f.write(f"%mem={self.mem_str}\n")
            f.write(f"%nprocshared={self.nprocshared}\n")
            f.write(f"{route_line}\n")
            f.write(f"\nTitle\n")
            f.write(f"\n{charge} {multiplicity}\n")

            for symbol, coord in zip(symbols, coordinates):
                f.write(f"{symbol:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}\n")

            f.write(f"\n\n")

            if link_lines:
                f.write(f"{link_lines}\n")
                f.write(f"{route_line}\n")
                f.write(f"\nTitle\n")
                f.write(f"\n{charge} {multiplicity}\n")
                for symbol, coord in zip(symbols, coordinates):
                    f.write(f"{symbol:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}\n")
                f.write(f"\n\n")

    def _run_gaussian(self, input_file: Path, output_file: Path) -> bool:
        """
        Run Gaussian calculation.

        Args:
            input_file: Input GJF file
            output_file: Output log file

        Returns:
            True if calculation completed successfully
        """
        ensure_dir(output_file.parent)
        
        if self.use_wrapper and Path(self.wrapper_script).exists():
            cmd = [self.wrapper_script, str(input_file), str(output_file)]
            try:
                result = subprocess.run(
                    cmd,
                    cwd=input_file.parent,
                    capture_output=True,
                    text=True,
                    timeout=self.config.get('optimization_control', {}).get('timeout', {}).get('default_seconds', 86400)
                )
                return result.returncode == 0
            except subprocess.TimeoutExpired:
                logger.error(f"Gaussian calculation timed out: {input_file}")
                return False
            except Exception as e:
                logger.error(f"Gaussian calculation failed: {e}")
                return False
        else:
            cmd = f'"{self.exe_path}" < "{input_file}" > "{output_file}"'
            try:
                result = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=input_file.parent,
                    capture_output=True,
                    text=True,
                    timeout=self.config.get('optimization_control', {}).get('timeout', {}).get('default_seconds', 86400)
                )
                return result.returncode == 0
            except subprocess.TimeoutExpired:
                logger.error(f"Gaussian calculation timed out: {input_file}")
                return False
            except Exception as e:
                logger.error(f"Gaussian calculation failed: {e}")
                return False

    def optimize(
        self,
        coordinates: NDArray[np.float64],
        symbols: List[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Optional[Path] = None,
        output_name: str = "optimize",
        **kwargs
    ) -> QCResult:
        """
        Perform geometry optimization.

        Args:
            coordinates: Initial coordinates (N, 3)
            symbols: Element symbols
            charge: Molecular charge
            multiplicity: Spin multiplicity
            output_dir: Output directory
            output_name: Base name for output files
            **kwargs: Additional parameters

        Returns:
            QCResult with optimization results
        """
        output_dir = Path(output_dir) if output_dir else Path.cwd()
        ensure_dir(output_dir)
        
        input_file = output_dir / f"{output_name}.gjf"
        output_file = output_dir / f"{output_name}.log"
        
        self._write_input(input_file, coordinates, symbols, 'opt', charge, multiplicity)
        
        success = self._run_gaussian(input_file, output_file)
        
        if not success:
            return QCResult(
                success=False,
                error_message="Gaussian optimization failed",
                output_file=input_file,
                log_file=output_file
            )
        
        coords, syms, error = LogParser.extract_last_converged_coords(output_file, 'gaussian')
        energy = LogParser.extract_energy(output_file, 'gaussian')
        
        if coords is None:
            return QCResult(
                success=False,
                error_message=error or "Could not extract coordinates",
                output_file=input_file,
                log_file=output_file
            )
        
        result = QCResult(
            success=True,
            energy=energy,
            coordinates=coords,
            symbols=syms or symbols,
            converged=True,
            output_file=input_file,
            log_file=output_file
        )
        
        if kwargs.get('calc_frequencies', False):
            freq_result = self.frequency(coords, symbols, charge, multiplicity, output_dir, f"{output_name}_freq")
            if freq_result.success:
                result.frequencies = freq_result.frequencies
                result.has_frequencies = True
                result.zpe = freq_result.zpe
                result.enthalpy = freq_result.enthalpy
                result.gibbs = freq_result.gibbs
                result.entropy = freq_result.entropy
                result.freq_log_file = freq_result.log_file
        
        return result

    def single_point(
        self,
        coordinates: NDArray[np.float64],
        symbols: List[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Optional[Path] = None,
        output_name: str = "sp",
        **kwargs
    ) -> QCResult:
        """
        Perform single-point energy calculation.

        Args:
            coordinates: Molecular coordinates (N, 3)
            symbols: Element symbols
            charge: Molecular charge
            multiplicity: Spin multiplicity
            output_dir: Output directory
            output_name: Base name for output files
            **kwargs: Additional parameters

        Returns:
            QCResult with single-point energy
        """
        output_dir = Path(output_dir) if output_dir else Path.cwd()
        ensure_dir(output_dir)
        
        input_file = output_dir / f"{output_name}.gjf"
        output_file = output_dir / f"{output_name}.log"
        
        self._write_input(input_file, coordinates, symbols, 'sp', charge, multiplicity)
        
        success = self._run_gaussian(input_file, output_file)
        
        if not success:
            return QCResult(
                success=False,
                error_message="Gaussian SP calculation failed",
                output_file=input_file,
                log_file=output_file
            )
        
        energy = LogParser.extract_energy(output_file, 'gaussian')
        
        if energy is None:
            return QCResult(
                success=False,
                error_message="Could not extract energy",
                output_file=input_file,
                log_file=output_file
            )
        
        return QCResult(
            success=True,
            energy=energy,
            coordinates=coordinates,
            symbols=symbols,
            converged=True,
            output_file=input_file,
            log_file=output_file
        )

    def nmr_shielding(
        self,
        coordinates: NDArray[np.float64],
        symbols: List[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Optional[Path] = None,
        output_name: str = "nmr",
        **kwargs
    ) -> QCResult:
        """
        Perform NMR shielding calculation.

        Args:
            coordinates: Molecular coordinates (N, 3)
            symbols: Element symbols
            charge: Molecular charge
            multiplicity: Spin multiplicity
            output_dir: Output directory
            output_name: Base name for output files
            **kwargs: Additional parameters

        Returns:
            QCResult with NMR shielding calculation results
        """
        output_dir = Path(output_dir) if output_dir else Path.cwd()
        ensure_dir(output_dir)

        input_file = output_dir / f"{output_name}.gjf"
        output_file = output_dir / f"{output_name}.log"

        self._write_input(input_file, coordinates, symbols, 'nmr', charge, multiplicity)

        success = self._run_gaussian(input_file, output_file)

        if not success:
            return QCResult(
                success=False,
                error_message="Gaussian NMR calculation failed",
                output_file=input_file,
                log_file=output_file
            )

        energy = LogParser.extract_energy(output_file, 'gaussian')

        return QCResult(
            success=True,
            energy=energy,
            coordinates=coordinates,
            symbols=symbols,
            converged=True,
            output_file=input_file,
            log_file=output_file
        )

    def frequency(
        self,
        coordinates: NDArray[np.float64],
        symbols: List[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Optional[Path] = None,
        output_name: str = "freq",
        **kwargs
    ) -> QCResult:
        """
        Perform frequency calculation.

        Args:
            coordinates: Molecular coordinates (N, 3)
            symbols: Element symbols
            charge: Molecular charge
            multiplicity: Spin multiplicity
            output_dir: Output directory
            output_name: Base name for output files
            **kwargs: Additional parameters

        Returns:
            QCResult with frequency results
        """
        output_dir = Path(output_dir) if output_dir else Path.cwd()
        ensure_dir(output_dir)
        
        input_file = output_dir / f"{output_name}.gjf"
        output_file = output_dir / f"{output_name}.log"
        
        self._write_input(input_file, coordinates, symbols, 'freq', charge, multiplicity)
        
        success = self._run_gaussian(input_file, output_file)
        
        if not success:
            return QCResult(
                success=False,
                error_message="Gaussian frequency calculation failed",
                output_file=input_file,
                log_file=output_file
            )
        
        energy = LogParser.extract_energy(output_file, 'gaussian')
        coords, syms, _ = LogParser.extract_last_converged_coords(output_file, 'gaussian')
        
        frequencies = []
        zpe = None
        enthalpy = None
        gibbs = None
        entropy = None
        
        try:
            with open(output_file, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            
            freq_pattern = r'Frequencies\s+--\s+([\d\.\-]+)'
            matches = re.findall(freq_pattern, content)
            for match in matches:
                for val in match.split():
                    frequencies.append(float(val))
            
            thermo_pattern = r'Thermal correction to\s+(\w+)\s+=\s+([-+]?\d+\.\d+)'
            matches = re.findall(thermo_pattern, content)
            for name, value in matches:
                value = float(value)
                if 'Enthalpy' in name:
                    enthalpy = value
                elif 'Gibbs Free Energy' in name:
                    gibbs = value
                elif 'Entropy' in name:
                    entropy = value
            
            zpe_pattern = r'Zero-point correction\s+=\s+([-+]?\d+\.\d+)'
            match = re.search(zpe_pattern, content)
            if match:
                zpe = float(match.group(1))
                
        except Exception as e:
            logger.warning(f"Could not parse frequency data: {e}")
        
        return QCResult(
            success=True,
            energy=energy,
            coordinates=coords if coords is not None else coordinates,
            symbols=syms or symbols,
            converged=True,
            output_file=input_file,
            log_file=output_file,
            frequencies=frequencies if frequencies else None,
            has_frequencies=len(frequencies) > 0,
            zpe=zpe,
            enthalpy=enthalpy,
            gibbs=gibbs,
            entropy=entropy
        )
