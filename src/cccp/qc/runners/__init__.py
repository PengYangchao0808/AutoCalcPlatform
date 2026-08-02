"""
QC Runners
==========

Runners for auxiliary QC tasks like clustering and thermodynamics.

Author: QCcalc Team (adapted from RPH)
"""

import re
import subprocess
import logging
import os
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import numpy as np

from cccp.utils.file_io import read_xyz, write_xyz
from cccp.utils import ensure_dir

logger = logging.getLogger(__name__)


def _pinned_env(threads: int) -> Dict[str, str]:
    """Environment with BLAS/OpenMP thread counts pinned to *threads*.

    LSF/OpenLava job environments inject ``OMP_NUM_THREADS`` set to the
    node's full core count, which oversubscribes the node.  Pinning the env
    vars keeps every subprocess within its allocated cores.
    """
    pinned = max(1, int(threads))
    env = dict(os.environ)
    env["OMP_NUM_THREADS"] = str(pinned)
    env["MKL_NUM_THREADS"] = str(pinned)
    env["OPENBLAS_NUM_THREADS"] = str(pinned)
    return env


def run_isostat(
    ensemble_xyz: Path,
    output_dir: Path,
    config: Dict[str, Any],
    gdis: float = 0.125,
    edis: float = 1.0,
    temperature: float = 298.15,
    threads: int = 8,
    energy_window: Optional[float] = None
) -> Tuple[Optional[Path], List[Tuple[np.ndarray, List[str], float]]]:
    """
    Run ISOSTAT clustering on conformer ensemble.

    .. deprecated::
        Delegate to :class:`cccp.qc.interfaces.isostat.IsostatInterface`
        (or the ``acp.backends`` adapters) instead.  This wrapper is
        retained for the dormant ``ConformerEngine`` call chain and the
        ``acp.backends.external`` re-export, and now inherits the
        interface's production fixes (title normalisation — exit 24 fix,
        error classification, pinned thread env).

    Args:
        ensemble_xyz: Path to ensemble XYZ file
        output_dir: Output directory
        config: Configuration dictionary
        gdis: Geometry distance cutoff
        edis: Energy distance cutoff
        temperature: Temperature for Boltzmann weighting
        threads: Number of threads

    Returns:
        Tuple of (output_xyz_path, list of (coords, symbols, energy)).
        On failure the original ensemble path and an empty list are
        returned (legacy silent semantics — production callers should use
        the QCResult-carrying interface instead).
    """
    from cccp.qc.interfaces.isostat import IsostatInterface

    if energy_window is not None:
        logger.warning(
            "energy_window parameter is deprecated — ISOSTAT v2022.05 does not support "
            "-Ewin. Energy window filtering is no longer applied via ISOSTAT CLI. "
            "Consider using post-run log parsing instead (like RPH)."
        )

    output_dir = Path(output_dir)
    ensure_dir(output_dir)

    interface = IsostatInterface(config)
    result = interface.cluster(
        ensemble_xyz,
        output_dir,
        gdis=gdis,
        edis=edis,
        temperature=temperature,
        nthreads=threads,
    )

    if not result.success or result.output_file is None:
        logger.error("ISOSTAT clustering failed: %s", result.error_message)
        return ensemble_xyz, []

    cluster_xyz = result.output_file
    if not cluster_xyz.exists():
        logger.warning("ISOSTAT clustering failed, copying ensemble directly")
        import shutil
        shutil.copy(ensemble_xyz, cluster_xyz)
        return cluster_xyz, []

    coords_list = _parse_cluster_xyz(cluster_xyz)
    return cluster_xyz, coords_list


def _parse_cluster_xyz(
    cluster_xyz: Path,
) -> List[Tuple[np.ndarray, List[str], float]]:
    """Parse a clustered multi-frame XYZ into (coords, symbols, energy) tuples.

    Energies are read from ``Title | Energy: X`` comments (legacy format)
    or from bare Molclus-style numeric titles.
    """
    coords_list: List[Tuple[np.ndarray, List[str], float]] = []
    text = cluster_xyz.read_text(encoding="utf-8")
    lines = text.splitlines()
    i = 0
    n_atoms = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        match = re.match(r'(\d+)', stripped)
        if match is None:
            i += 1
            continue
        n_atoms = int(match.group(1))
        if n_atoms <= 0:
            i += 1
            continue
        if i + 2 + n_atoms > len(lines):
            break

        title = lines[i + 1].strip()
        energy: Optional[float] = None
        title_parts = title.split('|')
        if len(title_parts) >= 2:
            try:
                energy = float(title_parts[1].split(':')[1].strip())
            except (ValueError, IndexError):
                pass
        if energy is None:
            try:
                energy = float(title)
            except ValueError:
                pass

        coords = []
        symbols = []
        for line in lines[i + 2:i + 2 + n_atoms]:
            parts = line.strip().split()
            if len(parts) >= 4:
                symbols.append(parts[0])
                coords.append([float(parts[1]), float(parts[2]), float(parts[3])])

        if coords:
            coords_list.append((np.array(coords), symbols, energy))
        i += 2 + n_atoms

    return coords_list


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
            timeout=None,
            env=_pinned_env(1)
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
    sp_energy: float = 0.0,
    temperature: float = 298.15,
    pressure: float = 1.0,
) -> Dict[str, Dict[str, float]]:
    """
    Batch process multiple frequency log files with Shermo.

    Args:
        log_files: List of frequency log files (Gaussian .log)
        output_dir: Base output directory
        config: Configuration dictionary (for Shermo binary path)
        sp_energy: Single-point energy in Hartree for each file.
            **Must be provided for correct thermochemistry.**
            If the same SP energy applies to all files, pass it here;
            otherwise call :func:`run_shermo` per file directly.
        temperature: Temperature in K
        pressure: Pressure in atm

    Returns:
        Dictionary mapping log file stem to thermochemical data

    Note:
        The ``sp_energy`` is required for meaningful results.  A default of
        0.0 is provided only to prevent crashes; the returned thermodynamic
        data will be incorrect if ``sp_energy`` is not overridden.
    """
    if sp_energy == 0.0:
        logger.warning(
            "batch_process_thermo: sp_energy is 0.0 — thermochemical data "
            "will be incorrect.  Provide the single-point energy (Hartree)."
        )

    shermo_bin = str(
        config.get("executables", {}).get("shermo", {}).get("path", "Shermo")
    )
    results = {}

    for log_file in log_files:
        log_output_dir = output_dir / log_file.stem
        ensure_dir(log_output_dir)

        thermo = run_shermo(
            freq_output=log_file,
            sp_energy=sp_energy,
            output_dir=log_output_dir,
            shermo_bin=shermo_bin,
            temperature_k=temperature,
            pressure_atm=pressure,
        )
        if thermo:
            results[log_file.stem] = thermo
        else:
            logger.warning(f"Failed to process {log_file}")

    return results
