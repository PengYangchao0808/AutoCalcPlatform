"""
xTB Single-Point Hessian + MRRHO Thermo Interface
==================================================

Implements the CENSO-style ``xtb --bhess --enso`` workflow for
thermochemical property calculation via xTB SPH + mRRHO.

Author: QCcalc Team
"""

import os
import subprocess
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class XTBThermoResult:
    """Result from xTB SPH + mRRHO thermochemistry calculation.

    Attributes:
        g_total: Gibbs free energy G(T) from xtb_enso.json (Hartree).
        zpve: Zero-point vibrational energy (Hartree), optional.
        h_total: Enthalpy H(T) (Hartree), optional.
        success: Whether the calculation completed successfully.
        error: Error message if the calculation failed, else None.
    """
    g_total: float
    zpve: Optional[float] = None
    h_total: Optional[float] = None
    success: bool = True
    error: Optional[str] = None


def _xyz_to_coord(xyz_path: Path, coord_path: Path) -> None:
    """Convert an XYZ file to xTB .coord format.

    xTB .coord format uses ``$coord`` / ``$end`` delimiters with
    coordinates in Å (same as XYZ) and lowercase element symbols.

    Args:
        xyz_path: Path to the input XYZ file.
        coord_path: Path to write the .coord file.
    """
    xyz_path = Path(xyz_path)
    coord_path = Path(coord_path)

    with open(xyz_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    if len(lines) < 3:
        raise ValueError(f"XYZ file too short: {xyz_path}")

    natoms = int(lines[0].strip())
    atom_lines = lines[2:2 + natoms]

    with open(coord_path, 'w', encoding='utf-8') as f:
        f.write("$coord\n")
        for line in atom_lines:
            parts = line.strip().split()
            if len(parts) < 4:
                continue
            symbol = parts[0].lower()
            x = float(parts[1])
            y = float(parts[2])
            z = float(parts[3])
            f.write(f"{x:16.10f}  {y:16.10f}  {z:16.10f}  {symbol}\n")
        f.write("$end\n")


def _write_xcontrol(
    xcontrol_path: Path,
    temperature_k: float,
    sthr: float,
    imagthr: float,
) -> None:
    """Write the .xcontrol file with $thermo and $symmetry blocks.

    Args:
        xcontrol_path: Path to the .xcontrol file to write.
        temperature_k: Temperature in Kelvin.
        sthr: Rotational/vibrational entropy threshold (cm⁻¹ / K).
        imagthr: Imaginary frequency threshold for thermo (cm⁻¹).
    """
    content = (
        f"$thermo\n"
        f"    temp={temperature_k}\n"
        f"    sthr={sthr}\n"
        f"    imagthr={imagthr}\n"
        f"$symmetry\n"
        f"     maxat=1000\n"
        f"$end\n"
    )
    xcontrol_path.write_text(content, encoding='utf-8')


def run_xtb_enso(
    xtb_bin: Path,
    coord_file: Path,
    output_dir: Path,
    *,
    nproc: int = 1,
    gfn_level: int = 2,
    temperature_k: float = 298.15,
    sthr: float = 50.0,
    imagthr: float = -100.0,
    charge: int = 0,
    unpaired: int = 0,
    solvent: Optional[str] = None,
    timeout: Optional[int] = None,
) -> XTBThermoResult:
    """Run xTB single-point Hessian + mRRHO (--bhess --enso) calculation.

    Writes a ``.xcontrol`` file into *output_dir*, invokes xTB, and
    parses the resulting ``xtb_enso.json`` file to extract G(T), ZPVE,
    and H(T).

    Args:
        xtb_bin: Path to the xTB binary.
        coord_file: Path to the .coord file (input geometry).
        output_dir: Directory for all output files (created if needed).
        nproc: Number of threads/cores. Sets ``-T`` flag and
            ``OMP_NUM_THREADS`` / ``MKL_NUM_THREADS`` /
            ``OPENBLAS_NUM_THREADS`` environment variables. Default 1.
        gfn_level: GFN-xTB level (0, 1, or 2). Default 2.
        temperature_k: Temperature in Kelvin. Default 298.15.
        sthr: Rotational/vibrational entropy threshold. Default 50.0.
        imagthr: Imaginary frequency threshold. Default -100.0.
        charge: Molecular charge. Default 0.
        unpaired: Number of unpaired electrons. Default 0.
        solvent: ALPB solvent name (e.g. ``"toluene"``). Default None.
        timeout: Subprocess timeout in seconds. Default 600.

    Returns:
        XTBThermoResult with parsed thermochemical data on success,
        or error details on failure.
    """
    output_dir = Path(output_dir)
    coord_file = Path(coord_file)
    xtb_bin = Path(xtb_bin)

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write .xcontrol
    xcontrol_name = "xcontrol"
    xcontrol_path = output_dir / xcontrol_name
    _write_xcontrol(xcontrol_path, temperature_k, sthr, imagthr)

    # Build command
    cmd: List[str] = [
        str(xtb_bin),
        str(coord_file),
        "--gfn", str(gfn_level),
        "--bhess",
        "vtight",
        "--enso",
        "--chrg", str(charge),
        "--uhf", str(unpaired),
        "-T", str(nproc),
        "-I", xcontrol_name,
    ]

    if solvent:
        cmd.extend(["--alpb", solvent])

    # Thread environment
    xtb_env = os.environ.copy()
    xtb_env["OMP_NUM_THREADS"] = str(nproc)
    xtb_env["MKL_NUM_THREADS"] = str(nproc)
    xtb_env["OPENBLAS_NUM_THREADS"] = str(nproc)

    logger.info(
        "Running xTB SPH+MRRHO: %s in %s",
        " ".join(cmd), output_dir,
    )

    # Run subprocess
    try:
        result = subprocess.run(
            cmd,
            cwd=output_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=xtb_env,
        )
    except subprocess.TimeoutExpired:
        error_msg = f"xTB SPH+MRRHO timed out after {timeout}s"
        logger.error(error_msg)
        return XTBThermoResult(
            g_total=0.0,
            success=False,
            error=error_msg,
        )
    except FileNotFoundError:
        error_msg = f"xTB binary not found: {xtb_bin}"
        logger.error(error_msg)
        return XTBThermoResult(
            g_total=0.0,
            success=False,
            error=error_msg,
        )

    # Check return code
    if result.returncode != 0:
        stderr_snippet = result.stderr[:200] if result.stderr else "(no stderr)"
        error_msg = f"xTB SPH+MRRHO failed (rc={result.returncode}): {stderr_snippet}"
        logger.error(error_msg)
        return XTBThermoResult(
            g_total=0.0,
            success=False,
            error=error_msg,
        )

    # Parse xtb_enso.json
    json_path = output_dir / "xtb_enso.json"
    if not json_path.exists():
        error_msg = f"xTB SPH+MRRHO completed but xtb_enso.json not found in {output_dir}"
        logger.error(error_msg)
        return XTBThermoResult(
            g_total=0.0,
            success=False,
            error=error_msg,
        )

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        error_msg = f"Failed to parse xtb_enso.json: {e}"
        logger.error(error_msg)
        return XTBThermoResult(
            g_total=0.0,
            success=False,
            error=error_msg,
        )

    # Extract required field G(T)
    try:
        g_total = float(data["G(T)"])
    except (KeyError, TypeError, ValueError) as e:
        error_msg = f"Missing or invalid 'G(T)' in xtb_enso.json: {e}"
        logger.error(error_msg)
        return XTBThermoResult(
            g_total=0.0,
            success=False,
            error=error_msg,
        )

    # Extract optional fields
    zpve = None
    if "ZPVE" in data:
        try:
            zpve = float(data["ZPVE"])
        except (TypeError, ValueError):
            logger.warning("Invalid 'ZPVE' value in xtb_enso.json, ignoring")

    h_total = None
    if "H(T)" in data:
        try:
            h_total = float(data["H(T)"])
        except (TypeError, ValueError):
            logger.warning("Invalid 'H(T)' value in xtb_enso.json, ignoring")

    return XTBThermoResult(
        g_total=g_total,
        zpve=zpve,
        h_total=h_total,
        success=True,
        error=None,
    )
