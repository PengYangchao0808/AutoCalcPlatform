"""
Shermo Thermodynamics Runner
============================

Run Shermo for thermochemical analysis on frequency output files.

Author: QCcalc Team (adapted from RPH)
"""

import re
import subprocess
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from conformer_search.utils import ensure_dir

logger = logging.getLogger(__name__)


def run_shermo(
    freq_output: Path,
    sp_energy: float,
    output_dir: Path,
    shermo_bin: str = "Shermo",
    output_file: Optional[Path] = None,
    temperature_k: float = 298.15,
    pressure_atm: float = 1.0,
    scl_zpe: float = 0.9905,
    ilowfreq: int = 2,
    imagreal: int = 0,
    conc: Optional[float] = None
) -> Optional[Dict[str, float]]:
    """
    Run Shermo for thermochemical analysis.

    Args:
        freq_output: Path to Gaussian frequency output file (.log)
        sp_energy: ORCA single-point energy (Hartree)
        output_dir: Output directory
        shermo_bin: Shermo executable path
        output_file: Shermo output .sum file path (optional)
        temperature_k: Temperature in Kelvin
        pressure_atm: Pressure in atm
        scl_zpe: ZPE correction factor
        ilowfreq: Low frequency mode threshold
        imagreal: Imaginary frequency handling (0=remove)
        conc: Concentration for Gibbs free energy correction (optional)

    Returns:
        Dictionary with thermochemical data or None on failure:
        - u_sum: Internal energy correction (Hartree)
        - h_sum: Enthalpy correction (Hartree)
        - g_sum: Gibbs free energy correction (Hartree)
        - g_conc: Gibbs free energy at specified concentration (Hartree)
        - s_total: Total entropy (a.u.)
    """
    output_dir = Path(output_dir)
    ensure_dir(output_dir)
    
    if output_file is None:
        output_file = output_dir / "Shermo.sum"
    
    cmd = [
        str(shermo_bin),
        str(freq_output),
        "-E", f"{sp_energy:.12f}",
        "-T", str(temperature_k),
        "-P", str(pressure_atm),
        "-sclZPE", str(scl_zpe),
        "-ilowfreq", str(ilowfreq),
        "-imagreal", str(imagreal),
    ]
    if conc is not None:
        cmd.extend(["-conc", str(conc)])
    
    try:
        result = subprocess.run(
            cmd,
            cwd=output_dir,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(result.stdout)
            if result.stderr:
                f.write("\nSTDERR:\n")
                f.write(result.stderr)
        
        return _parse_sum_file(output_file)
        
    except Exception as e:
        logger.error(f"Shermo calculation failed: {e}")
        return None


def _parse_sum_file(sum_file: Path) -> Optional[Dict[str, float]]:
    """
    Parse Shermo .sum file to extract thermochemical data.

    Args:
        sum_file: Path to Shermo .sum file

    Returns:
        Dictionary with thermochemical data or None on failure
    """
    patterns = {
        'u_sum': r"Sum of electronic energy and thermal correction to U:\s+([-+]?\d+\.\d+)",
        'h_sum': r"Sum of electronic energy and thermal correction to H:\s+([-+]?\d+\.\d+)",
        'g_sum': r"Sum of electronic energy and thermal correction to G:\s+([-+]?\d+\.\d+)",
        'g_conc': r"Gibbs free energy at specified concentration:\s+([-+]?\d+\.\d+)",
        's_total': r"Total S:\s+([-+]?\d+\.\d+)",
    }
    
    try:
        with open(sum_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        thermo_data = {}
        for key, pattern in patterns.items():
            match = re.search(pattern, content)
            if match:
                thermo_data[key] = float(match.group(1))
        
        return thermo_data if thermo_data else None
        
    except Exception as e:
        logger.error(f"Failed to parse Shermo sum file: {e}")
        return None


def batch_process_thermo(
    log_files: List[Path],
    output_dir: Path,
    config: Dict[str, Any],
    temperature: float = 298.15,
    pressure: float = 1.0
) -> Dict[str, Dict[str, float]]:
    """
    Batch process multiple log files with Shermo.

    Args:
        log_files: List of frequency log files
        output_dir: Output directory
        config: Configuration dictionary
        temperature: Temperature in K
        pressure: Pressure in atm

    Returns:
        Dictionary mapping filename to thermochemical data
    """
    results = {}
    
    for log_file in log_files:
        log_output_dir = output_dir / log_file.stem
        ensure_dir(log_output_dir)
        
        # NOTE: sp_energy=0.0 is a placeholder — batch processing does not
        # have per-conformer single-point energies. Users should re-run
        # with explicit sp_energy if electronic energy is needed.
        thermo = run_shermo(
            freq_output=log_file,
            sp_energy=0.0,
            output_dir=log_output_dir,
            temperature_k=temperature,
            pressure_atm=pressure
        )
        if thermo:
            results[log_file.stem] = thermo
        else:
            logger.warning(f"Failed to process {log_file}")
    
    return results
