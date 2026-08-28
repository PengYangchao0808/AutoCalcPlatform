"""
QC Runners
==========

Runners for auxiliary QC tasks like clustering and thermodynamics.

Author: QCcalc Team (adapted from RPH)
"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


# Shermo (Fortran binary) truncates input file paths at 200 characters and
# then fails with "Error: Unable to find <truncated path>" even though the
# file exists (verified against Shermo 2.6 x86-64: 200 chars pass, 201 fail).
_SHERMO_INPUT_PATH_LIMIT = 200


def _shermo_input_path(freq_output: Path, output_dir: Path) -> str:
    """Return a Shermo-safe input path argument.

    Prefers a path relative to *output_dir* (the subprocess cwd), which is
    always short when the frequency file lives under the output directory.
    When the candidate still exceeds Shermo's path buffer (file outside the
    output dir on a deeply nested run root), the file is copied to a short
    name inside *output_dir* and that basename is returned instead.

    Args:
        freq_output: Path to the frequency output file.
        output_dir: Directory the Shermo subprocess runs in.

    Returns:
        Path string safe to pass as the Shermo input file argument.
    """
    freq_path = Path(freq_output)
    try:
        candidate = str(freq_path.resolve().relative_to(Path(output_dir).resolve()))
    except ValueError:
        candidate = str(freq_path)
    if len(candidate) > _SHERMO_INPUT_PATH_LIMIT:
        short_copy = Path(output_dir) / freq_path.name
        if short_copy.resolve() != freq_path.resolve():
            shutil.copy2(freq_path, short_copy)
            logger.debug(
                "Shermo input path exceeds %d chars; copied to %s",
                _SHERMO_INPUT_PATH_LIMIT,
                short_copy,
            )
        candidate = freq_path.name
    return candidate


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
        _shermo_input_path(freq_output, output_dir),
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
