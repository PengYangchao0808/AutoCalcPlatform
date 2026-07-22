"""
ORCA Interface
=============

Interface for ORCA quantum chemistry software.

Author: QCcalc Team (adapted from RPH)
"""

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

import numpy as np

from conformer_search.qc.interfaces.base import QCInterfaceBase, QCResult
from conformer_search.utils import ensure_dir
from conformer_search.utils.geometry_tools import LogParser
from conformer_search.utils.resource_utils import calc_orca_maxcore, mem_to_mb
from conformer_search.utils.solvent_map import orca_smd_solvent

logger = logging.getLogger(__name__)


def _resolve_method_meta(method: Optional[str]) -> Optional[dict[str, Any]]:
    """Look up ``METHOD_META`` for *method* (case-insensitive).

    Returns ``None`` if ``acp.catalog`` is unavailable or *method* is not
    declared. Imported lazily so that ``conformer_search`` has no
    import-time dependency on the ``acp`` package.
    """
    if not method:
        return None
    try:
        from acp.catalog import METHOD_META, _case_insensitive_get
    except ImportError:
        return None
    return _case_insensitive_get(METHOD_META, method)


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
        self._orca_ld_library_path = orca_config.get("ld_library_path")

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
        self,
        calc_type: str = "opt",
        method: str = None,
        basis: str = None,
        route_extras: list = None,
        geom_maxiter: int = None,
        extra_blocks: list = None,
        recalc_hess: int = None,
        solvent: str = None,
        solvent_model: str = None,
        aux_basis: str = None,
        aux_j_basis: str = None,
        aux_c_basis: str = None,
    ) -> str:
        """
        Build ORCA input blocks.

        Args:
            calc_type: Calculation type
            method: Override method (uses self.method if None)
            basis: Override basis (uses self.basis if None)
            route_extras: Extra route-line keywords appended to the ``!`` line
                (e.g. ``["RIJCOSX", "VeryTightSCF"]``)
            geom_maxiter: Optional MaxIter for the %geom block (opt only)
            extra_blocks: Extra raw input blocks appended after the route
            recalc_hess: Optional Hessian recalculation interval for the %geom
                block (opt only); overrides config when given
            solvent: Override solvent (uses self.solvent if None)
            solvent_model: Override solvent model (uses self.solvent_model if None)
            aux_basis: Legacy auxiliary basis (backward compat, migrated to aux_c_basis)
            aux_j_basis: Auxiliary /J basis for RI-J fitting
            aux_c_basis: Auxiliary /C basis for RI-MP2 correlation

        Returns:
            Input blocks string
        """
        _method = method if method is not None else self.method
        _basis = basis if basis is not None else self.basis
        _route_extras = [str(x) for x in route_extras if x] if route_extras else []
        _solvent = solvent if solvent is not None else self.solvent
        _solvent_model = (solvent_model if solvent_model is not None else self.solvent_model) or "none"

        blocks = []

        calc_type_map = {
            "opt": "Opt",
            "freq": "Freq",
            "sp": "SP",
            "optfreq": "Opt Freq",
            "nmr": "NMR",
        }
        route = calc_type_map.get(calc_type, calc_type)

        if (
            route in ("Freq", "Opt Freq")
            and _solvent
            and _solvent_model.lower() != "cpcm"
            and _solvent_model.lower() != "none"
        ):
            route = route.replace("Freq", "NumFreq")

        meta = _resolve_method_meta(_method)
        basis_inline = True if meta is None else bool(meta.get("basis_inline", True))
        ri_support = (meta or {}).get("ri_support", "user")

        _aux_j = aux_j_basis
        _aux_c = aux_c_basis
        _filtered_extras: list[str] = []

        _aux_basis_pattern = re.compile(r"/([JC])(?=$|[^A-Za-z])")

        if not aux_j_basis and not aux_c_basis and not aux_basis:
            for x in _route_extras:
                xs = str(x)
                m = _aux_basis_pattern.search(xs)
                if m:
                    kind = m.group(1)
                    if kind == "J" and not _aux_j:
                        _aux_j = xs
                    elif kind == "C" and not _aux_c:
                        _aux_c = xs
                    else:
                        _filtered_extras.append(x)
                else:
                    _filtered_extras.append(x)
        else:
            _filtered_extras = list(_route_extras)

        if aux_basis and not _aux_c:
            _aux_c = aux_basis

        if ri_support in ("composite", "automatic"):
            _ri_keywords = {"RI", "RIJCOSX", "RIJK", "NONE"}
            _filtered_extras = [
                x for x in _filtered_extras
                if str(x).upper() not in _ri_keywords
                and not _aux_basis_pattern.search(str(x))
            ]
            _aux_j = None
            if ri_support == "composite":
                _aux_c = None

        builtin = (meta or {}).get("builtin_dispersion")
        if builtin:
            _filtered_extras = [
                x for x in _filtered_extras
                if str(x).upper() != builtin.upper()
            ]

        extras_str = (" " + " ".join(_filtered_extras)) if _filtered_extras else ""

        if not basis_inline:
            method_name = _method
            if _method.lower() == "dlpno-ccsd(t)":
                method_name = "DLPNO-CCSD(T)"

            route_prefix = ""
            if method_name == "DLPNO-CCSD(T)":
                route_prefix = " TightSCF"
            blocks.append(f"! {method_name}{route_prefix} {route}{extras_str}")
        else:
            blocks.append(f"! {_method} {_basis} {route}{extras_str}")

        needs_basis_block = _aux_j or _aux_c or (
            not basis_inline and meta is not None
            and (meta.get("default_aux_j") or meta.get("default_aux_c"))
        )
        if needs_basis_block:
            blocks.append("%basis")
            if not basis_inline:
                effective_basis = _basis or (meta or {}).get("default_basis", "")
                if effective_basis:
                    blocks.append(f'  basis "{effective_basis}"')
            final_aux_j = _aux_j or (meta or {}).get("default_aux_j")
            final_aux_c = _aux_c or (meta or {}).get("default_aux_c")
            for blk in (extra_blocks or []):
                if isinstance(blk, dict):
                    if "auxJ" in blk:
                        final_aux_j = blk["auxJ"]
                    if "auxC" in blk:
                        final_aux_c = blk["auxC"]
            if final_aux_j:
                blocks.append(f'  auxJ  "{final_aux_j}"')
            if final_aux_c:
                blocks.append(f'  auxC  "{final_aux_c}"')
            blocks.append("end")

        blocks.append(f"%maxcore {self.maxcore}")
        blocks.append(f"%pal nprocs {self.nproc} end")

        # Read recalc_hess from config (with fallback default of 10).
        to_cfg = self.config.get("optimization_control") or {}
        if recalc_hess is None:
            recalc_hess = to_cfg.get("recalc_hess", 10)

        # Compute Hessian at step 1 and recalculate every 10 steps for better convergence.
        # Recalc_Hess N: calculate Hessian at the beginning and recalculate after N, 2N, ... steps.
        # If SMD solvation is used and this causes issues (ORCA lacks analytical Hessian
        # with SMD), fall back to Recalc_Hess Num_10 (numerical Hessian) if needed.
        if route.split()[0] == "Opt":
            blocks.append("%geom")
            blocks.append(f"  Recalc_Hess {recalc_hess}")
            if geom_maxiter is not None and geom_maxiter > 0:
                blocks.append(f"  MaxIter {int(geom_maxiter)}")
            blocks.append("end")

        if extra_blocks:
            for blk in extra_blocks:
                # Skip dict entries — they are structured overrides consumed
                # earlier (e.g. DLPNO %basis block auxJ/auxC overrides via
                # R19). Only stringifiable blocks render as raw input.
                if isinstance(blk, dict):
                    continue
                if blk:
                    blocks.append(str(blk))

        if _solvent and _solvent_model.lower() != "none":
            blocks.append("%cpcm")
            if _solvent_model.lower() == "cpcm":
                blocks.append(f'  SMDsolvent "{orca_smd_solvent(_solvent)}"')
            else:  # smd (default)
                blocks.append("  smd true")
                blocks.append(f'  SMDsolvent "{orca_smd_solvent(_solvent)}"')
            blocks.append("end")

        return "\n".join(blocks)

    def _write_input(
        self,
        input_file: Path,
        coordinates: np.ndarray,
        symbols: list[str],
        calc_type: str = "opt",
        charge: Optional[int] = None,
        multiplicity: Optional[int] = None,
        method: str = None,
        basis: str = None,
        route_extras: list = None,
        geom_maxiter: int = None,
        extra_blocks: list = None,
        recalc_hess: int = None,
        solvent: str = None,
        solvent_model: str = None,
        aux_basis: str = None,
        aux_j_basis: str = None,
        aux_c_basis: str = None,
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
            route_extras: Extra route-line keywords (see _build_input_blocks)
            geom_maxiter: Optional MaxIter for the %geom block
            extra_blocks: Extra raw input blocks
            recalc_hess: Optional Hessian recalc interval for the %geom block
            solvent: Override solvent (uses self.solvent if None)
            solvent_model: Override solvent model (uses self.solvent_model if None)
            aux_basis: Legacy auxiliary basis (backward compat)
            aux_j_basis: Auxiliary /J basis for RI-J fitting
            aux_c_basis: Auxiliary /C basis for RI-MP2 correlation
        """
        charge = charge if charge is not None else self.charge
        multiplicity = multiplicity if multiplicity is not None else self.multiplicity

        blocks = self._build_input_blocks(
            calc_type,
            method=method,
            basis=basis,
            route_extras=route_extras,
            geom_maxiter=geom_maxiter,
            extra_blocks=extra_blocks,
            recalc_hess=recalc_hess,
            solvent=solvent,
            solvent_model=solvent_model,
            aux_basis=aux_basis,
            aux_j_basis=aux_j_basis,
            aux_c_basis=aux_c_basis,
        )

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
            env = None
            if self._orca_ld_library_path:
                env = dict(os.environ)
                env["LD_LIBRARY_PATH"] = self._orca_ld_library_path

            result = subprocess.run(
                [str(self.exe_path), str(input_file)],
                cwd=input_file.parent,
                capture_output=True,
                text=True,
                env=env,
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

        _solvent = kwargs.pop("solvent", None)
        _solvent_model = kwargs.pop("solvent_model", None)

        self._write_input(
            input_file,
            coordinates,
            symbols,
            "opt",
            charge,
            multiplicity,
            method=method,
            basis=basis,
            route_extras=kwargs.get("route_extras"),
            geom_maxiter=kwargs.get("geom_maxiter"),
            extra_blocks=kwargs.get("extra_blocks"),
            recalc_hess=kwargs.get("recalc_hess"),
            solvent=_solvent,
            solvent_model=_solvent_model,
            aux_basis=kwargs.get("aux_basis"),
            aux_j_basis=kwargs.get("aux_j_basis"),
            aux_c_basis=kwargs.get("aux_c_basis"),
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

        _solvent = kwargs.pop("solvent", None)
        _solvent_model = kwargs.pop("solvent_model", None)

        self._write_input(
            input_file,
            coordinates,
            symbols,
            "sp",
            charge,
            multiplicity,
            method=method,
            basis=basis,
            route_extras=kwargs.get("route_extras"),
            extra_blocks=kwargs.get("extra_blocks"),
            solvent=_solvent,
            solvent_model=_solvent_model,
            aux_basis=kwargs.get("aux_basis"),
            aux_j_basis=kwargs.get("aux_j_basis"),
            aux_c_basis=kwargs.get("aux_c_basis"),
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

        _solvent = kwargs.pop("solvent", None)
        _solvent_model = kwargs.pop("solvent_model", None)

        self._write_input(
            input_file,
            coordinates,
            symbols,
            "freq",
            charge,
            multiplicity,
            method=method,
            basis=basis,
            route_extras=kwargs.get("route_extras"),
            extra_blocks=kwargs.get("extra_blocks"),
            solvent=_solvent,
            solvent_model=_solvent_model,
            aux_basis=kwargs.get("aux_basis"),
            aux_j_basis=kwargs.get("aux_j_basis"),
            aux_c_basis=kwargs.get("aux_c_basis"),
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

    def opt_freq(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path = None,
        output_name: str = "orca_optfreq",
        method: str = None,
        basis: str = None,
        route_extras: list = None,
        geom_maxiter: int = None,
        extra_blocks: list = None,
        recalc_hess: int = None,
        aux_basis: str = None,
        aux_j_basis: str = None,
        aux_c_basis: str = None,
        **kwargs,
    ) -> QCResult:
        """Run combined optimization + frequency as single ORCA job.

        Uses calc_type='optfreq' -> generates '! ... Opt Freq ...' route line.
        NumFreq fallback is handled automatically by _build_input_blocks.

        Args:
            coordinates: Initial coordinates (N, 3)
            symbols: Element symbols
            charge: Molecular charge
            multiplicity: Spin multiplicity
            output_dir: Output directory
            output_name: Base name for output files
            method: Override method (uses self.method if None)
            basis: Override basis (uses self.basis if None)
            route_extras: Extra route-line keywords
            geom_maxiter: Max geometry iterations
            extra_blocks: Extra raw input blocks
            recalc_hess: Hessian recalculation interval (Recalc_Hess N)
            aux_basis: Legacy auxiliary basis (backward compat)
            aux_j_basis: Auxiliary /J basis for RI-J fitting
            aux_c_basis: Auxiliary /C basis for RI-MP2 correlation
            **kwargs: Additional parameters

        Returns:
            QCResult with opt+freq results
        """
        output_dir = Path(output_dir) if output_dir else Path.cwd()
        ensure_dir(output_dir)

        input_file = output_dir / f"{output_name}.inp"
        output_file = output_dir / f"{output_name}.out"

        _solvent = kwargs.pop("solvent", None)
        _solvent_model = kwargs.pop("solvent_model", None)

        self._write_input(
            input_file,
            coordinates,
            symbols,
            "optfreq",
            charge,
            multiplicity,
            method=method,
            basis=basis,
            route_extras=route_extras,
            geom_maxiter=geom_maxiter,
            extra_blocks=extra_blocks,
            recalc_hess=recalc_hess,
            solvent=_solvent,
            solvent_model=_solvent_model,
            aux_basis=aux_basis,
            aux_j_basis=aux_j_basis,
            aux_c_basis=aux_c_basis,
        )

        success = self._run_orca(input_file, output_file)

        if not success:
            return QCResult(
                success=False,
                error_message="ORCA opt+freq calculation failed",
                output_file=input_file,
                log_file=output_file,
            )

        coords, syms, error = LogParser.extract_last_converged_coords(output_file, "orca")
        energy = LogParser.extract_energy(output_file, "orca")

        frequencies: list[float] = []
        try:
            with open(output_file, encoding="utf-8", errors="replace") as f:
                content = f.read()
            freq_pattern = r"Mode\#\s+\d+\s+:\s+([-+]?\d+\.\d+)\s+cm\*\*-1"
            matches = re.findall(freq_pattern, content)
            frequencies = [float(m) for m in matches]
        except Exception as e:
            logger.warning(f"Could not parse frequency data from optfreq output: {e}")

        if coords is None:
            return QCResult(
                success=False,
                error_message=error or "Could not extract coordinates from optfreq output",
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

        _solvent = kwargs.pop("solvent", None)
        _solvent_model = kwargs.pop("solvent_model", None)

        self._write_input(
            input_file,
            coordinates,
            symbols,
            "nmr",
            charge,
            multiplicity,
            method=method,
            basis=basis,
            solvent=_solvent,
            solvent_model=_solvent_model,
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
