"""
ORCA Interface
=============

Interface for ORCA quantum chemistry software.

Author: QCcalc Team (adapted from RPH)
"""

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from conformer_search.qc.interfaces.base import QCInterfaceBase, QCResult
from conformer_search.utils import ensure_dir
from conformer_search.utils.geometry_tools import LogParser
from conformer_search.utils.resource_utils import calc_orca_maxcore, mem_to_mb
from conformer_search.utils.solvent_map import orca_smd_solvent

logger = logging.getLogger(__name__)


class ORCAInterface(QCInterfaceBase):
    """
    Interface for ORCA calculations.
    """

    def __init__(
        self,
        config: dict[str, Any],
        method: str = "M062X",
        basis: str = "def2-TZVPP",
        solvent: str = None,
        solvent_model: str = "none",
        **kwargs,
    ):
        """
        Initialize ORCA interface.

        Args:
            config: Configuration dictionary
            method: DFT method
            basis: Basis set
            solvent: Solvent model
            solvent_model: Solvent model type - none, smd, cpcm (default none)
            **kwargs: Additional parameters
        """
        super().__init__(config, **kwargs)

        self.method = method
        self.basis = basis
        self.solvent = solvent
        self.solvent_model = solvent_model

        orca_config = self.executables.get("orca", {})
        self.exe_path = Path(orca_config.get("path", "orca"))

        resources = self.resources
        orca_nproc_config = orca_config.get("nproc")
        self.nproc = kwargs.get(
            "nprocs",
            orca_nproc_config if orca_nproc_config is not None else resources.get("nproc", 16),
        )

        self.mem_str = resources.get("mem", "32GB")
        self.mem_mb = mem_to_mb(self.mem_str)

        orca_maxcore_config = orca_config.get("maxcore")
        if orca_maxcore_config is not None:
            self.maxcore = orca_maxcore_config
        else:
            self.maxcore = calc_orca_maxcore(
                self.mem_mb, self.nproc, resources.get("orca_maxcore_safety", 0.8)
            )

        self.charge = kwargs.get("charge", 0)
        self.multiplicity = kwargs.get("multiplicity", 1)

    def _build_input_blocks(
        self, calc_type: str = "opt", method: str = None, basis: str = None
    ) -> str:
        """
        Build ORCA input blocks.

        Args:
            calc_type: Calculation type
            method: Override method (uses self.method if None)
            basis: Override basis (uses self.basis if None)

        Returns:
            Input blocks string
        """
        _method = method if method is not None else self.method
        _basis = basis if basis is not None else self.basis

        blocks = []

        calc_type_map = {
            "opt": "Opt",
            "freq": "Freq",
            "sp": "SP",
            "optfreq": "Opt Freq",
            "nmr": "NMR",
        }
        route = calc_type_map.get(calc_type, calc_type)

        # ORCA does not support analytical Hessian with SMD solvation.
        # Fall back to numerical frequencies (NumFreq) to avoid abort.
        if (
            route in ("Freq", "Opt Freq")
            and self.solvent
            and (self.solvent_model or "").lower() != "cpcm"
        ):
            route = route.replace("Freq", "NumFreq")

        # Composite methods (PBEh-3c, r2SCAN-3c) bundle their own basis set
        if _method.lower() in ("pbeh-3c", "r2scan-3c"):
            blocks.append(f"! {_method} {route}")
        elif _method == "DLPNO-CCSD(T)":
            blocks.append(f"! DLPNO-CCSD(T) TightSCF {route}")
            blocks.append("%basis")
            blocks.append('  basis "def2-TZVPP"')
            blocks.append('  auxJ  "def2/J"')
            blocks.append('  auxC  "def2-TZVPP/C"')
            blocks.append("end")
        else:
            blocks.append(f"! {_method} {_basis} {route}")

        blocks.append(f"%maxcore {self.maxcore}")
        blocks.append(f"%pal nprocs {self.nproc} end")

        # Read recalc_hess from config (with fallback default of 10).
        to_cfg = self.config.get("optimization_control") or {}
        recalc_hess = to_cfg.get("recalc_hess", 10)

        # Compute Hessian at step 1 and recalculate every 10 steps for better convergence.
        # Recalc_Hess N: calculate Hessian at the beginning and recalculate after N, 2N, ... steps.
        # If SMD solvation is used and this causes issues (ORCA lacks analytical Hessian
        # with SMD), fall back to Recalc_Hess Num_10 (numerical Hessian) if needed.
        if route.split()[0] == "Opt":
            blocks.append("%geom")
            blocks.append(f"  Recalc_Hess {recalc_hess}")
            blocks.append("end")

        if self.solvent:
            blocks.append("%cpcm")
            if self.solvent_model.lower() == "cpcm":
                blocks.append(f'  SMDsolvent "{orca_smd_solvent(self.solvent)}"')
            else:  # smd (default)
                blocks.append("  smd true")
                blocks.append(f'  SMDsolvent "{orca_smd_solvent(self.solvent)}"')
            blocks.append("end")

        return "\n".join(blocks)

    def _write_input(
        self,
        input_file: Path,
        coordinates: np.ndarray,
        symbols: list[str],
        calc_type: str = "opt",
        charge: int = None,
        multiplicity: int = None,
        method: str = None,
        basis: str = None,
    ):
        """
        Write ORCA input file.

        Args:
            input_file: Output input file path
            coordinates: Molecular coordinates
            symbols: Element symbols
            calc_type: Calculation type
            charge: Molecular charge
            multiplicity: Spin multiplicity
            method: Override method (uses self.method if None)
            basis: Override basis (uses self.basis if None)
        """
        charge = charge if charge is not None else self.charge
        multiplicity = multiplicity if multiplicity is not None else self.multiplicity

        blocks = self._build_input_blocks(calc_type, method=method, basis=basis)

        ensure_dir(input_file.parent)

        with open(input_file, "w", encoding="utf-8") as f:
            f.write(blocks + "\n")
            f.write(f"\n* xyz {charge} {multiplicity}\n")

            for symbol, coord in zip(symbols, coordinates):
                f.write(f"{symbol:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}\n")

            f.write("*\n")

    def _run_orca(self, input_file: Path, output_file: Path) -> bool:
        """
        Run ORCA calculation.

        Args:
            input_file: Input file
            output_file: Output file

        Returns:
            True if calculation completed successfully
        """
        ensure_dir(output_file.parent)

        to_cfg = self.config.get("optimization_control") or {}
        to_val = to_cfg.get("timeout") or {}
        timeout = to_val.get("default_seconds", 864000) if isinstance(to_val, dict) else 864000

        try:
            result = subprocess.run(
                [str(self.exe_path), str(input_file)],
                cwd=input_file.parent,
                capture_output=True,
                text=True,
                env=None,
                timeout=timeout,
            )

            with open(output_file, "w", encoding="utf-8") as f:
                f.write(result.stdout)
                if result.stderr:
                    f.write("\nSTDERR:\n")
                    f.write(result.stderr)

            return result.returncode == 0

        except subprocess.TimeoutExpired:
            logger.error(f"ORCA calculation timed out: {input_file}")
            return False
        except Exception as e:
            logger.error(f"ORCA calculation failed: {e}")
            return False

    def optimize(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path = None,
        output_name: str = "optimize",
        method: str = None,
        basis: str = None,
        **kwargs,
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
            method: Override method (uses self.method if None)
            basis: Override basis (uses self.basis if None)
            **kwargs: Additional parameters

        Returns:
            QCResult with optimization results
        """
        output_dir = Path(output_dir) if output_dir else Path.cwd()
        ensure_dir(output_dir)

        input_file = output_dir / f"{output_name}.inp"
        output_file = output_dir / f"{output_name}.out"

        self._write_input(
            input_file,
            coordinates,
            symbols,
            "opt",
            charge,
            multiplicity,
            method=method,
            basis=basis,
        )

        success = self._run_orca(input_file, output_file)

        if not success:
            return QCResult(
                success=False,
                error_message="ORCA optimization failed",
                output_file=input_file,
                log_file=output_file,
            )

        coords, syms, error = LogParser.extract_last_converged_coords(output_file, "orca")
        energy = LogParser.extract_energy(output_file, "orca")

        if coords is None:
            return QCResult(
                success=False,
                error_message=error or "Could not extract coordinates",
                output_file=input_file,
                log_file=output_file,
            )

        return QCResult(
            success=True,
            energy=energy,
            coordinates=coords,
            symbols=syms or symbols,
            converged=True,
            output_file=input_file,
            log_file=output_file,
        )

    def single_point(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path = None,
        output_name: str = "sp",
        method: str = None,
        basis: str = None,
        **kwargs,
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
            method: Override method (uses self.method if None)
            basis: Override basis (uses self.basis if None)
            **kwargs: Additional parameters

        Returns:
            QCResult with single-point energy
        """
        output_dir = Path(output_dir) if output_dir else Path.cwd()
        ensure_dir(output_dir)

        input_file = output_dir / f"{output_name}.inp"
        output_file = output_dir / f"{output_name}.out"

        self._write_input(
            input_file, coordinates, symbols, "sp", charge, multiplicity, method=method, basis=basis
        )

        success = self._run_orca(input_file, output_file)

        if not success:
            return QCResult(
                success=False,
                error_message="ORCA SP calculation failed",
                output_file=input_file,
                log_file=output_file,
            )

        energy = LogParser.extract_energy(output_file, "orca")

        if energy is None:
            return QCResult(
                success=False,
                error_message="Could not extract energy",
                output_file=input_file,
                log_file=output_file,
            )

        return QCResult(
            success=True,
            energy=energy,
            coordinates=coordinates,
            symbols=symbols,
            converged=True,
            output_file=input_file,
            log_file=output_file,
        )

    def frequency(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path = None,
        output_name: str = "freq",
        method: str = None,
        basis: str = None,
        **kwargs,
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
            method: Override method (uses self.method if None)
            basis: Override basis (uses self.basis if None)
            **kwargs: Additional parameters

        Returns:
            QCResult with frequency results
        """
        output_dir = Path(output_dir) if output_dir else Path.cwd()
        ensure_dir(output_dir)

        input_file = output_dir / f"{output_name}.inp"
        output_file = output_dir / f"{output_name}.out"

        self._write_input(
            input_file,
            coordinates,
            symbols,
            "freq",
            charge,
            multiplicity,
            method=method,
            basis=basis,
        )

        success = self._run_orca(input_file, output_file)

        if not success:
            return QCResult(
                success=False,
                error_message="ORCA frequency calculation failed",
                output_file=input_file,
                log_file=output_file,
            )

        energy = LogParser.extract_energy(output_file, "orca")
        coords, syms, _ = LogParser.extract_last_converged_coords(output_file, "orca")

        frequencies = []
        try:
            with open(output_file, encoding="utf-8", errors="replace") as f:
                content = f.read()

            freq_pattern = r"Mode\#\s+\d+\s+:\s+([-+]?\d+\.\d+)\s+cm\*\*-1"
            matches = re.findall(freq_pattern, content)
            frequencies = [float(m) for m in matches]

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
        )

    def nmr_shielding(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path = None,
        output_name: str = "nmr",
        method: str = None,
        basis: str = None,
        **kwargs,
    ) -> QCResult:
        """
        Perform NMR shielding calculation using ORCA's NMR keyword.

        Args:
            coordinates: Molecular coordinates (N, 3)
            symbols: Element symbols
            charge: Molecular charge
            multiplicity: Spin multiplicity
            output_dir: Output directory
            output_name: Base name for output files
            method: Override DFT method (uses self.method if None)
            basis: Override basis set (uses self.basis if None)
            **kwargs: Additional parameters (ignored for compatibility)

        Returns:
            QCResult with NMR shielding calculation results
        """
        output_dir = Path(output_dir) if output_dir else Path.cwd()
        ensure_dir(output_dir)

        input_file = output_dir / f"{output_name}.inp"
        output_file = output_dir / f"{output_name}.out"

        self._write_input(
            input_file,
            coordinates,
            symbols,
            "nmr",
            charge,
            multiplicity,
            method=method,
            basis=basis,
        )

        success = self._run_orca(input_file, output_file)

        if not success:
            return QCResult(
                success=False,
                error_message="ORCA NMR calculation failed",
                output_file=input_file,
                log_file=output_file,
            )

        energy = LogParser.extract_energy(output_file, "orca")

        return QCResult(
            success=True,
            energy=energy,
            coordinates=coordinates,
            symbols=symbols,
            converged=True,
            output_file=input_file,
            log_file=output_file,
        )
