"""
xTB Interface
=============

Interface for xTB semi-empirical calculations.

Author: QCcalc Team
"""

import subprocess
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
import numpy as np

from cccp.qc.interfaces.base import QCResult
from cccp.qc.interfaces.xtb_thermo import run_xtb_enso, XTBThermoResult, _xyz_to_coord
from cccp.utils.file_io import write_xyz, read_xyz
from cccp.utils import ensure_dir
from cccp.utils.solvent_map import xtb_solvent

logger = logging.getLogger(__name__)


class XTBInterface:
    """
    Interface for xTB calculations.
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
        Initialize xTB interface.

        Args:
            config: Configuration dictionary
            gfn_level: GFN-xTB level (0, 1, or 2)
            solvent: Solvent for xTB solvation
            solvent_model: Solvation model ("none", "alpb", "gbsa")
            **kwargs: Additional parameters
        """
        self.config = config
        executables = config.get('executables', {})

        xtb_config = executables.get('xtb', {})
        self.exe_path = Path(xtb_config.get('path', 'xtb'))

        self.gfn_level = gfn_level
        self.solvent = solvent
        self.solvent_model = (solvent_model or "none").lower()

        resources = config.get('resources', {})
        self.nproc = kwargs.get('nproc', resources.get('nproc', 16))

    def _solvent_args(self, solvent: Optional[str] = None) -> List[str]:
        """Return xTB solvation command-line flags based on solvent_model."""
        sol = solvent if solvent is not None else self.solvent
        if not sol or self.solvent_model == "none":
            return []
        if self.solvent_model == "gbsa":
            return ["--gbsa", xtb_solvent(sol)]
        return ["--alpb", xtb_solvent(sol)]

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
        output_file = output_dir / "xtbopt.xyz"
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

        xtb_args.extend(self._solvent_args())

        max_steps = kwargs.get('max_steps')
        if max_steps is not None:
            xcontrol_path = output_dir / ".xcontrol"
            xcontrol_path.write_text(
                f"$opt\n  maxcycle={int(max_steps)}\n$end\n",
                encoding="utf-8",
            )
            xtb_args.extend(["--input", str(xcontrol_path)])

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

        xtb_args.extend(self._solvent_args())

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
