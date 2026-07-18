"""
ConformerEngine
===============

Main conformer search engine.

Author: QCcalc Team (adapted from RPH)
"""

import logging
import math
import re
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import numpy as np

from conformer_search.utils.constants import HARTREE_TO_KCAL

from conformer_search.core.protocols import (
    is_ext_protocol,
    is_full_protocol,
    is_lite_protocol,
    is_zero_protocol,
    is_benchmark_protocol,
    ProtocolSpec,
    resolve_protocol_spec,
)
from conformer_search.core.candidates import (
    CandidateSet,
    ConformerCandidate,
    candidate_set_from_paths,
    clone_candidate_set,
)
from conformer_search.core.state_manager import ConformerStateManager
from conformer_search.io.input_handler import MolecularInput, InputFormat
from conformer_search.qc.interfaces import (
    ORCAInterface,
    CRESTInterface,
    XTBInterface,
    QCResult,
    XTBThermoResult,
)
from conformer_search.qc.runners import run_isostat, run_shermo
from conformer_search.utils.file_io import write_xyz, read_xyz, read_xyz_multiframe
from conformer_search.utils.geometry_tools import GeometryUtils
from conformer_search.utils import ensure_dir

logger = logging.getLogger(__name__)


class ConformerEngine:
    """
    Main conformer search engine.

    Implements the complete conformer search workflow:
    1. RDKit 3D embedding (if starting from SMILES)
    2. CREST conformer search (single or two-stage)
    3. ISOSTAT clustering
    4. DFT optimization and single-point energy
    5. Boltzmann weighting and selection
    """

    def __init__(
        self,
        config: Dict[str, Any],
        work_dir: Path,
        molecule_name: str,
        protocol: str = "ext",
        protocol_spec: ProtocolSpec = None,
        levels: Optional[dict[str, Any]] = None,
    ):
        """
        Initialize conformer engine.

        Args:
            config: Configuration dictionary
            work_dir: Working directory
            molecule_name: Name for this molecule
            protocol: Protocol name (ext, full, lite, zero)
            protocol_spec: Pre-resolved protocol spec (optional)
            levels: Optional per-job method overrides passed to
                :func:`resolve_protocol_spec`.
        """
        self.config = config
        self.molecule_name = molecule_name
        self.protocol = protocol.lower()
        self.levels = levels

        work_dir = Path(work_dir).resolve()
        self.work_dir = work_dir / molecule_name
        ensure_dir(self.work_dir)

        self.protocol_spec = protocol_spec or resolve_protocol_spec(
            config, self.protocol, levels=self.levels
        )

        self._setup_directories()
        self._setup_qc_interfaces()
        self._setup_state_manager()

        logger.info(f"ConformerEngine initialized for {molecule_name}")
        logger.info(f"  Protocol: {self.protocol_spec.name}")
        logger.info(f"  Two-stage: {self.protocol_spec.two_stage_enabled}")

    def _setup_directories(self):
        """Create directory structure for conformer search."""
        self.rdkit_dir = self.work_dir / "rdkit"
        self.crest_dir = self.work_dir / "crest"
        self.cluster_dir = self.work_dir / "cluster"
        self.xtb_dir = self.work_dir / "xtb"
        self.prescan_dir = self.work_dir / "prescan"
        self.fastsp_dir = self.work_dir / "fastsp"
        self.final_dft_dir = self.work_dir / "finalDFT"

        for d in [
            self.rdkit_dir,
            self.crest_dir,
            self.cluster_dir,
            self.xtb_dir,
            self.prescan_dir,
            self.fastsp_dir,
            self.final_dft_dir,
        ]:
            ensure_dir(d)

    def _create_qc_interface(self, engine: str, theory_config: dict):
        """Create QC interface instance based on engine type."""
        if engine == "orca":
            return ORCAInterface(
                config=self.config,
                method=theory_config.get("method"),
                basis=theory_config.get("basis"),
                solvent=theory_config.get("solvent"),
                solvent_model=theory_config.get("solvent_model"),
            )
        else:
            raise ValueError(f"Unknown engine: {engine}")

    def _setup_qc_interfaces(self):
        """Initialize QC interfaces."""
        self.theory_opt = self.config.get("theory", {}).get("optimization", {})
        self.theory_sp = self.config.get("theory", {}).get("single_point", {})
        self.theory_preopt = self.config.get("theory", {}).get("preoptimization", {})

        # Apply per-protocol opt_method/opt_basis override (protocol > theory)
        theory_opt_effective = dict(self.theory_opt)
        if self.protocol_spec.opt_method:
            theory_opt_effective["method"] = self.protocol_spec.opt_method
        if self.protocol_spec.opt_basis or self.protocol_spec.opt_basis == "":
            theory_opt_effective["basis"] = self.protocol_spec.opt_basis

        # Apply per-protocol solvent overrides for opt/freq
        if self.protocol_spec.opt_solvent is not None:
            theory_opt_effective["solvent"] = self.protocol_spec.opt_solvent
        if self.protocol_spec.opt_solvent_model is not None:
            theory_opt_effective["solvent_model"] = self.protocol_spec.opt_solvent_model

        self.opt_interface = self._create_qc_interface(
            self.protocol_spec.opt_engine, theory_opt_effective
        )
        self.freq_interface = self._create_qc_interface(
            self.protocol_spec.freq_engine, theory_opt_effective
        )

        # Create effective theory config for SP with per-protocol overrides
        theory_sp_effective = dict(self.theory_sp)
        if self.protocol_spec.sp_solvent is not None:
            theory_sp_effective["solvent"] = self.protocol_spec.sp_solvent
        if self.protocol_spec.sp_solvent_model is not None:
            theory_sp_effective["solvent_model"] = self.protocol_spec.sp_solvent_model

        self.sp_interface = self._create_qc_interface(
            self.protocol_spec.sp_engine, theory_sp_effective
        )

        crest_gfn = self.config.get("executables", {}).get("crest", {}).get("gfn_level", 2)
        self.crest_interface = CRESTInterface(
            config=self.config,
            gfn_level=crest_gfn,
            solvent=self.theory_preopt.get("solvent"),
            solvent_model=self.theory_preopt.get("solvent_model", "none"),
        )

        xtb_gfn = self.theory_preopt.get("gfn_level", 2)
        self.xtb_interface = XTBInterface(
            config=self.config,
            gfn_level=xtb_gfn,
            solvent=self.theory_preopt.get("solvent"),
            solvent_model=self.theory_preopt.get("solvent_model", "none"),
        )

        self.shermo_bin = self.config.get("executables", {}).get("shermo", {}).get("path", "Shermo")
        self.thermo_config = self.config.get("thermo", {})

        if self.protocol_spec.enable_shermo:
            t = self.thermo_config.get("temperature_k", 298.15)
            p = self.thermo_config.get("pressure_atm", 1.0)
            s = self.thermo_config.get("scl_zpe", 0.9905)
            il = self.thermo_config.get("shermo_ilowfreq", 2)
            ir = self.thermo_config.get("shermo_imagreal", 0)
            c = self.thermo_config.get("shermo_conc")
            conc_str = f"{c}" if c is not None else "not set"
            logger.info(
                f"  Shermo: T={t}K, P={p}atm, sclZPE={s}, ilowfreq={il}, imagreal={ir}, conc={conc_str}, bin={self.shermo_bin}"
            )

        opt_m = theory_opt_effective.get("method", "B3LYP")
        opt_b = theory_opt_effective.get("basis", "def2-SVP")
        logger.info(
            f"  QC — opt({self.protocol_spec.opt_engine}:{opt_m}/{opt_b}), "
            f"freq({self.protocol_spec.freq_engine}:{opt_m}/{opt_b}), "
            f"sp({self.protocol_spec.sp_engine}:{self.protocol_spec.final_sp_method}/{self.protocol_spec.final_sp_basis}, "
            f"solvent_model={theory_sp_effective.get('solvent_model', 'none')}, "
            f"solvent={theory_sp_effective.get('solvent', 'None')})"
        )

    def _setup_state_manager(self):
        """Initialize state manager."""
        self.state_manager = ConformerStateManager(self.work_dir, self.molecule_name)

    def run(self, molecular_input: MolecularInput) -> Tuple[Path, float, Dict[str, Any]]:
        """
        Execute full conformer search workflow.

        Args:
            molecular_input: Input molecular structure

        Returns:
            Tuple of (global_min_xyz, energy_hartree, metadata_dict)
        """
        logger.info(f"Starting conformer search for {self.molecule_name}")

        self.state_manager.start_run(
            smiles=molecular_input.metadata.get("smiles", "unknown"),
            two_stage_enabled=self.protocol_spec.two_stage_enabled,
        )

        self.state_manager.set_protocol_signature(
            protocol=self.protocol_spec.name,
            funnel_signature={
                "search_mode": self.protocol_spec.funnel_policy.search_mode,
                "two_stage": self.protocol_spec.two_stage_enabled,
                "ngeom_default": self.protocol_spec.ngeom_default,
            },
        )

        # Store molecular properties for QC calculations
        self._current_charge = molecular_input.charge
        self._current_multiplicity = molecular_input.multiplicity
        logger.info(
            f"Molecular charge: {self._current_charge}, multiplicity: {self._current_multiplicity}"
        )

        if molecular_input.source_format == InputFormat.SMILES:
            initial_xyz = self._step_rdkit_embed(molecular_input)
        else:
            initial_xyz = self._save_initial_structure(molecular_input)

        if is_ext_protocol(self.protocol_spec):
            candidate_set = self._run_ext_protocol(initial_xyz)
        elif is_benchmark_protocol(self.protocol_spec):
            candidate_set = self._run_ext_protocol(initial_xyz)
        elif is_full_protocol(self.protocol_spec):
            candidate_set = self._run_full_protocol(initial_xyz)
        elif is_lite_protocol(self.protocol_spec):
            candidate_set = self._run_lite_protocol(initial_xyz)
        elif is_zero_protocol(self.protocol_spec):
            candidate_set = self._run_zero_protocol(initial_xyz)
        else:
            candidate_set = self._run_ext_protocol(initial_xyz)

        final_result = self._finalize_results(candidate_set)

        self.state_manager.mark_completed()

        return (
            final_result["global_min_xyz"],
            final_result["global_min_energy"],
            final_result["metadata"],
        )

    def _step_rdkit_embed(self, molecular_input: MolecularInput) -> Path:
        """
        Generate 3D structure from SMILES using RDKit.

        Args:
            molecular_input: Input with SMILES

        Returns:
            Path to initial XYZ file
        """
        logger.info("[S1] Step 1: RDKit 3D embedding")

        self.state_manager.set_stage("rdkit_embed")

        output_path = self.rdkit_dir / f"{self.molecule_name}_init.xyz"

        write_xyz(
            output_path,
            molecular_input.coordinates,
            molecular_input.symbols,
            title=f"RDKit embedding for {self.molecule_name}",
        )

        self.state_manager.complete_stage(
            "rdkit_embed",
            {"output_file": str(output_path), "n_atoms": len(molecular_input.symbols)},
        )

        return output_path

    def _save_initial_structure(self, molecular_input: MolecularInput) -> Path:
        """Save initial structure to rdkit directory."""
        output_path = self.rdkit_dir / f"{self.molecule_name}_init.xyz"
        write_xyz(
            output_path,
            molecular_input.coordinates,
            molecular_input.symbols,
            title=f"Initial structure for {self.molecule_name}",
        )
        return output_path

    def _step_crest_search(self, initial_xyz: Path, energy_window: Optional[float] = None) -> Path:
        """
        Run CREST conformer search.

        Args:
            initial_xyz: Initial XYZ file
            energy_window: Energy window in kcal/mol for CREST ensemble pruning

        Returns:
            Path to CREST ensemble XYZ
        """
        logger.info("[S1] Step 2: CREST conformer search")

        self.state_manager.set_stage("crest_search")

        result = self.crest_interface.run_conformer_search(
            *read_xyz(initial_xyz),
            output_dir=self.crest_dir,
            output_name=self.molecule_name,
            charge=self._current_charge,
            multiplicity=self._current_multiplicity,
            energy_window=energy_window,
        )

        if not result.success:
            raise RuntimeError(f"CREST search failed: {result.error_message}")

        ensemble_path = result.output_file

        self.state_manager.complete_stage(
            "crest_search",
            {
                "ensemble_file": str(ensemble_path),
                "n_conformers": result.metadata.get("n_conformers", 0),
            },
        )

        return ensemble_path

    def _extract_xtb_energies_from_ensemble(self, ensemble_xyz: Path) -> List[Optional[float]]:
        """Extract xTB energies from CREST ensemble XYZ title lines.

        Parses multi-frame XYZ looking for CREST energy annotations in title lines.
        CREST format: ``energy: -XX.XXXXX Hartree``.
        """
        ensemble_xyz = Path(ensemble_xyz)
        if not ensemble_xyz.exists():
            logger.warning(f"Ensemble file not found: {ensemble_xyz}")
            return []

        with open(ensemble_xyz, "r", encoding="utf-8") as f:
            lines = f.readlines()

        energies: List[Optional[float]] = []
        offset = 0

        while offset < len(lines):
            try:
                atom_count = int(lines[offset].strip())
            except (ValueError, IndexError):
                offset += 1
                continue

            if atom_count == 0:
                break

            if offset + 2 + atom_count > len(lines):
                break

            title_line = lines[offset + 1].strip()
            energy_match = re.search(r"energy:\s*([-+]?\d+\.?\d*)", title_line, re.IGNORECASE)
            if not energy_match:
                energy_match = re.search(r"^\s*([-+]?\d+\.\d+)", title_line)
            if energy_match:
                energies.append(float(energy_match.group(1)))
            else:
                logger.warning(
                    f"No energy found in title line for frame {len(energies)}: {title_line}"
                )
                energies.append(None)

            offset += atom_count + 2

        return energies

    def _step_two_stage_crest(
        self, initial_xyz: Path, energy_window: Optional[float] = None
    ) -> Tuple[Path, Path]:
        """
        Run two-stage CREST search (GFN0 → ISOSTAT → GFN2 -mdopt).

        Stage 1: GFN0-xTB for fast, broad conformational space sampling.
        Intermediate ISOSTAT: cluster GFN0 ensemble to reduce conformer count.
        Stage 2: GFN2-xTB batch optimization for refinement of clustered conformers.

        Args:
            initial_xyz: Initial XYZ file
            energy_window: Energy window in kcal/mol for Stage 2 ensemble pruning

        Returns:
            Tuple of (stage1_ensemble, stage2_ensemble)
        """
        logger.info("[S1] Step 2: Two-stage CREST (GFN0 → ISOSTAT → GFN2 -mdopt)")

        coords, symbols = read_xyz(initial_xyz)

        # ===========================================================
        # Stage 1: GFN0 Conformer Search
        # ===========================================================
        self.state_manager.set_stage("crest_stage1")

        stage1_dir = self.xtb_dir / "stage1_gfn0"
        ensure_dir(stage1_dir)

        stage1_result = self.crest_interface.run_conformer_search(
            coords,
            symbols,
            stage1_dir,
            gfn_level=0,
            energy_window=10.0,
            charge=self._current_charge,
            multiplicity=self._current_multiplicity,
        )

        if not stage1_result.success:
            raise RuntimeError(f"Stage 1 GFN0 search failed: {stage1_result.error_message}")

        self.state_manager.complete_stage(
            "crest_stage1",
            {
                "output": str(stage1_result.output_file),
                "n_conformers": stage1_result.metadata.get("n_conformers", 0),
            },
        )

        # ===========================================================
        # Intermediate: ISOSTAT Clustering (reduce GFN0 ensemble)
        # ===========================================================
        cluster_xyz = self._run_intermediate_isostat(
            stage1_result.output_file, "stage1_gfn0", "stage1_isostat"
        )

        self.state_manager.mark_intermediate_clustering("completed", str(cluster_xyz))

        # ===========================================================
        # Stage 2: GFN2 Batch Optimization (-mdopt)
        # ===========================================================
        self.state_manager.set_stage("crest_stage2")

        stage2_dir = self.xtb_dir / "stage2_gfn2"
        ensure_dir(stage2_dir)

        clustered_coords, clustered_symbols = read_xyz_multiframe(cluster_xyz)

        stage2_result = self.crest_interface.run_batch_optimization(
            clustered_coords,
            clustered_symbols,
            stage2_dir,
            gfn_level=2,
            charge=self._current_charge,
            multiplicity=self._current_multiplicity,
            energy_window=energy_window,
        )

        # Fallback if -mdopt fails
        if not stage2_result.success:
            logger.warning(
                f"GFN2 -mdopt batch optimization failed, falling back to GFN2 search: "
                f"{stage2_result.error_message}"
            )
            stage2_result = self.crest_interface.run_conformer_search(
                clustered_coords,
                clustered_symbols,
                stage2_dir,
                gfn_level=2,
                charge=self._current_charge,
                multiplicity=self._current_multiplicity,
                energy_window=energy_window,
            )

        if not stage2_result.success:
            raise RuntimeError(f"Stage 2 GFN2 failed: {stage2_result.error_message}")

        # Copy stage2 output to canonical ensemble location
        ensemble_path = self.crest_dir / "ensemble.xyz"
        shutil.copy(stage2_result.output_file, ensemble_path)

        self.state_manager.complete_stage(
            "crest_stage2",
            {
                "ensemble_file": str(ensemble_path),
                "n_conformers": stage2_result.metadata.get("n_conformers", 0),
            },
        )

        return stage1_result.output_file, ensemble_path

    def _step_isostat_clustering(self, ensemble_xyz: Path) -> Path:
        """
        Run ISOSTAT clustering on ensemble.

        Args:
            ensemble_xyz: CREST ensemble XYZ

        Returns:
            Path to clustered XYZ
        """
        logger.info("[S1] Step 3: ISOSTAT clustering")

        self.state_manager.set_stage("clustering")

        energy_window = self.config.get("resources", {}).get("isostat_energy_window_kcal", 3.0)

        cluster_xyz, cluster_data = run_isostat(
            ensemble_xyz=ensemble_xyz,
            output_dir=self.cluster_dir,
            config=self.config,
            gdis=self.config.get("resources", {}).get("isostat_gdis", 1.0),
            edis=1.0,
            temperature=298.15,
            threads=self.config.get("resources", {}).get("nproc", 8),
        )

        self.state_manager.complete_stage(
            "clustering", {"clustered_file": str(cluster_xyz), "n_clusters": len(cluster_data)}
        )

        return cluster_xyz

    def _run_intermediate_isostat(
        self, ensemble_xyz: Path, stage_name: str, output_subdir: str
    ) -> Path:
        """
        Run intermediate ISOSTAT clustering between CREST stages.

        Used in two-stage CREST flow to reduce the number of conformers
        after GFN0 screening before the more expensive GFN2 refinement.

        Args:
            ensemble_xyz: Multi-frame ensemble XYZ from preceding stage
            stage_name: Descriptive name for logging
            output_subdir: Subdirectory name under crest_dir for output

        Returns:
            Path to cluster.xyz
        """
        logger.info(f"[S1] Intermediate ISOSTAT clustering ({stage_name})")

        output_dir = self.crest_dir / output_subdir
        ensure_dir(output_dir)

        gdis = self.config.get("resources", {}).get("isostat_intermediate_gdis")
        if gdis is None:
            gdis = self.config.get("resources", {}).get("isostat_gdis", 0.5)

        energy_window = self.config.get("resources", {}).get(
            "isostat_intermediate_energy_window_kcal", 10.0
        )

        cluster_xyz, cluster_data = run_isostat(
            ensemble_xyz=ensemble_xyz,
            output_dir=output_dir,
            config=self.config,
            gdis=gdis,
            edis=1.0,
            temperature=298.15,
            threads=self.config.get("resources", {}).get("nproc", 8),
        )

        logger.info(f"  Intermediate ISOSTAT: {len(cluster_data)} clusters → {cluster_xyz}")

        return cluster_xyz

    def _step_process_ensemble(self, ensemble_xyz: Path) -> List[Path]:
        """
        Process ensemble to get candidate paths.

        Args:
            ensemble_xyz: Ensemble XYZ file

        Returns:
            List of candidate XYZ paths
        """
        from conformer_search.utils.file_io import read_xyz_multiframe

        coords, symbols = read_xyz_multiframe(ensemble_xyz)

        n_atoms = len(symbols)
        n_conformers = len(coords) // n_atoms

        candidate_paths = []

        for i in range(n_conformers):
            start_idx = i * n_atoms
            end_idx = start_idx + n_atoms

            conf_coords = coords[start_idx:end_idx]
            conf_xyz = self.cluster_dir / f"conf_{i:03d}.xyz"

            write_xyz(conf_xyz, conf_coords, symbols, title=f"Conformer {i}")
            candidate_paths.append(conf_xyz)

        return candidate_paths

    def _run_shared_dft_handoff(self, candidate_paths: List[Path]) -> CandidateSet:
        """
        Run DFT optimization and SP on candidates.

        Args:
            candidate_paths: List of candidate XYZ files

        Returns:
            CandidateSet with DFT results
        """
        logger.info("[S1] Step 4: DFT OPT-SP handoff")

        self.state_manager.set_stage("dft_handoff")

        spec = self.protocol_spec

        # Log which substages are enabled
        enabled_substages = []
        if spec.enable_optimization:
            enabled_substages.append(f"opt({spec.opt_engine}:{spec.opt_method}/{spec.opt_basis})")
        if spec.enable_frequency:
            enabled_substages.append(f"freq({spec.freq_engine}:{spec.opt_method}/{spec.opt_basis})")
        if spec.enable_single_point:
            enabled_substages.append(
                f"sp({spec.sp_engine}:{spec.final_sp_method}/{spec.final_sp_basis})"
            )
        if spec.enable_shermo:
            enabled_substages.append("shermo")
        logger.info(
            f"  Substages enabled: {', '.join(enabled_substages) if enabled_substages else 'none'}"
        )

        if spec.enable_shermo:
            t = self.thermo_config.get("temperature_k", 298.15)
            p = self.thermo_config.get("pressure_atm", 1.0)
            s = self.thermo_config.get("scl_zpe", 0.9905)
            il = self.thermo_config.get("shermo_ilowfreq", 2)
            ir = self.thermo_config.get("shermo_imagreal", 0)
            c = self.thermo_config.get("shermo_conc")
            conc_str = f"{c}" if c is not None else "not set"
            logger.info(
                f"  Shermo parameters: T={t}K, P={p}atm, sclZPE={s}, ilowfreq={il}, imagreal={ir}, conc={conc_str}, bin={self.shermo_bin}"
            )

        candidates = []

        n_to_optimize = min(len(candidate_paths), spec.ngeom_max)

        for i, path in enumerate(candidate_paths[:n_to_optimize]):
            logger.info(f"  Candidate {i + 1}/{n_to_optimize}: {path.name}")

            coords, symbols = read_xyz(path)

            opt_dir = self.final_dft_dir / f"conf_{i:03d}"
            ensure_dir(opt_dir)

            # Step 1: Optimization using configured opt engine
            if spec.enable_optimization:
                logger.info(
                    f"    [opt] Running geometry optimization ({spec.opt_engine}:{spec.opt_method}/{spec.opt_basis})"
                )
                opt_result = self.opt_interface.optimize(
                    coords,
                    symbols,
                    charge=self._current_charge,
                    multiplicity=self._current_multiplicity,
                    output_dir=opt_dir,
                    output_name=f"conf_{i:03d}_opt",
                )
            else:
                opt_result = QCResult(success=True, coordinates=coords, symbols=symbols, energy=0.0)

            # Default values (used when opt, freq, or sp fails)
            opt_success = opt_result.success
            sp_success = False
            freq_success = False
            opt_energy = opt_result.energy
            sp_energy = None
            gibbs_energy = None
            gibbs_correction = None
            h_correction = None
            u_correction = None
            s_total = None
            g_conc = None
            opt_log = str(opt_result.log_file) if opt_result.log_file else None
            sp_log = None

            if opt_success:
                # Step 2: Frequency using configured freq engine
                if spec.enable_frequency:
                    logger.info(
                        f"    [freq] Running frequency calculation ({spec.freq_engine}:{spec.opt_method}/{spec.opt_basis})"
                    )
                    freq_result = self.freq_interface.frequency(
                        opt_result.coordinates,
                        opt_result.symbols,
                        charge=self._current_charge,
                        multiplicity=self._current_multiplicity,
                        output_dir=opt_dir,
                        output_name=f"conf_{i:03d}_freq",
                    )
                    freq_success = freq_result.success
                else:
                    freq_success = False
                    freq_result = QCResult(success=False)

                # Step 3: Single-point energy (ORCA)
                if spec.enable_single_point:
                    logger.info(
                        f"    [sp] Running single-point ({spec.final_sp_method}/{spec.final_sp_basis})"
                    )
                    sp_result = self.sp_interface.single_point(
                        opt_result.coordinates,
                        opt_result.symbols,
                        charge=self._current_charge,
                        multiplicity=self._current_multiplicity,
                        output_dir=opt_dir,
                        output_name=f"conf_{i:03d}_sp",
                        method=spec.final_sp_method,
                        basis=spec.final_sp_basis,
                    )
                    sp_success = sp_result.success
                    sp_energy = sp_result.energy if sp_success else None
                    sp_log = str(sp_result.log_file) if sp_success else None
                else:
                    sp_success = True
                    sp_energy = opt_result.energy
                    sp_log = None
                    sp_result = QCResult(success=True, energy=opt_result.energy)

                # Step 4: Shermo thermochemistry (only if freq and sp succeeded)
                if spec.enable_shermo and freq_success and sp_success:
                    logger.info("    [shermo] Running thermodynamic correction")
                    shermo_result = run_shermo(
                        freq_output=freq_result.log_file,
                        sp_energy=sp_result.energy,
                        output_dir=opt_dir,
                        shermo_bin=self.shermo_bin,
                        output_file=opt_dir / f"conf_{i:03d}_Shermo.sum",
                        temperature_k=self.thermo_config.get("temperature_k", 298.15),
                        pressure_atm=self.thermo_config.get("pressure_atm", 1.0),
                        scl_zpe=self.thermo_config.get("scl_zpe", 0.9905),
                        ilowfreq=self.thermo_config.get("shermo_ilowfreq", 2),
                        imagreal=self.thermo_config.get("shermo_imagreal", 0),
                        conc=self.thermo_config.get("shermo_conc"),
                    )
                    if shermo_result:
                        g_sum = shermo_result.get("g_sum")
                        g_conc = shermo_result.get("g_conc")
                        gibbs_energy = g_conc if g_conc is not None else g_sum
                        h_correction = shermo_result.get("h_sum")
                        u_correction = shermo_result.get("u_sum")
                        s_total = shermo_result.get("s_total")
                        gibbs_correction = g_sum

            # Append candidate (opt failure → fall back to input coords/symbols)
            candidate_coords = (
                opt_result.coordinates if opt_result.coordinates is not None else coords
            )
            candidate_symbols = opt_result.symbols if opt_result.symbols is not None else symbols
            candidate_energy = sp_energy if sp_success else opt_energy
            if candidate_energy is None:
                candidate_energy = float("inf")
            candidates.append(
                ConformerCandidate(
                    index=i,
                    coordinates=candidate_coords,
                    symbols=candidate_symbols,
                    energy=candidate_energy,
                    gibbs_energy=gibbs_energy,
                    gibbs_correction=gibbs_correction,
                    h_correction=h_correction,
                    u_correction=u_correction,
                    s_total=s_total,
                    g_conc=g_conc,
                    source_file=path,
                    metadata={"opt_log": opt_log, "sp_out": sp_log},
                )
            )

        candidate_set = CandidateSet(candidates=candidates)
        candidate_set.calculate_boltzmann_weights_gibbs(
            temperature_k=self.thermo_config.get("temperature_k", 298.15)
        )
        candidate_set.update_ranks()

        self.state_manager.complete_stage(
            "dft_handoff",
            {
                "n_optimized": len(candidates),
                "reference_energy": candidate_set.candidates[0].energy if candidates else None,
            },
        )

        return candidate_set

    def _run_ext_protocol(self, initial_xyz: Path) -> CandidateSet:
        """Run EXT protocol (two-stage CREST + full DFT handoff)."""
        logger.info("[S1] Running EXT protocol")

        spec = self.protocol_spec

        if spec.enable_crest:
            if spec.two_stage_enabled:
                ewin = getattr(self.protocol_spec, "stage2_energy_window_kcal", None)
                _, ensemble_xyz = self._step_two_stage_crest(initial_xyz, energy_window=ewin)
            else:
                ensemble_xyz = self._step_crest_search(initial_xyz)
        else:
            ensemble_xyz = initial_xyz

        if spec.enable_clustering:
            clustered_xyz = self._step_isostat_clustering(ensemble_xyz)
        else:
            clustered_xyz = ensemble_xyz

        candidate_paths = self._step_process_ensemble(clustered_xyz)

        return self._run_shared_dft_handoff(candidate_paths)

    def _run_full_protocol(self, initial_xyz: Path) -> CandidateSet:
        """Run FULL protocol (CREST + fast SP screening + DFT)."""
        logger.info("[S1] Running FULL protocol")

        spec = self.protocol_spec

        if spec.enable_crest:
            ensemble_xyz = self._step_crest_search(initial_xyz)
        else:
            ensemble_xyz = initial_xyz

        if spec.enable_clustering:
            clustered_xyz = self._step_isostat_clustering(ensemble_xyz)
        else:
            clustered_xyz = ensemble_xyz

        candidate_paths = self._step_process_ensemble(clustered_xyz)

        # ===========================================================
        # Fast-SP Prescreen: PBEh-3c → energy window filter
        # ===========================================================
        if self.state_manager.is_stage_completed("fastsp_prescreen"):
            logger.info("[S1] Fast-SP prescreen already completed, resuming")
            prescreen_result = self.state_manager.get_stage_result("fastsp_prescreen")
            prescreen_survivors = [Path(p) for p in (prescreen_result or {}).get("survivors", [])]
        else:
            prescreen_energies = self._run_fast_sp_profile(candidate_paths, "PBEh-3c", "prescreen")
            prescreen_window = getattr(spec.funnel_policy, "prescreen_window_kcal", None) or 4.0
            prescreen_survivors = self._select_by_energy_window(
                prescreen_energies, prescreen_window
            )
            self.state_manager.complete_stage(
                "fastsp_prescreen",
                {
                    "method": "PBEh-3c",
                    "window_kcal": prescreen_window,
                    "n_input": len(candidate_paths),
                    "n_survivors": len(prescreen_survivors),
                    "survivors": [str(p) for p in prescreen_survivors],
                },
            )

        # ===========================================================
        # Fast-SP Screening: r2SCAN-3c → energy window filter
        # ===========================================================
        if self.state_manager.is_stage_completed("fastsp_screening"):
            logger.info("[S1] Fast-SP screening already completed, resuming")
            screening_result = self.state_manager.get_stage_result("fastsp_screening")
            final_paths = [Path(p) for p in (screening_result or {}).get("survivors", [])]
        else:
            screening_energies = self._run_fast_sp_profile(
                prescreen_survivors, "r2SCAN-3c", "screening"
            )
            screening_window = getattr(spec.funnel_policy, "screening_window_kcal", None) or 3.5
            screen_survivors = self._select_by_energy_window(screening_energies, screening_window)
            final_paths = screen_survivors[: spec.ngeom_max]
            self.state_manager.complete_stage(
                "fastsp_screening",
                {
                    "method": "r2SCAN-3c",
                    "window_kcal": screening_window,
                    "n_input": len(prescreen_survivors),
                    "n_survivors": len(screen_survivors),
                    "capped_at": spec.ngeom_max,
                    "survivors": [str(p) for p in final_paths],
                },
            )

        # ===========================================================
        # MRRHO Correction: xTB SPH+MRRHO approximate free energy
        # ===========================================================
        if spec.funnel_policy.use_mrrho_like_correction and final_paths:
            logger.info("[S1]   [MRRHO] Computing approximate free energies via xTB SPH+MRRHO...")
            mrrho_success = 0
            mrrho_total = len(final_paths)

            path_to_g: List[Tuple[Path, float]] = []
            for i, path in enumerate(final_paths):
                # Find the screening energy for this path
                path_lookup = path.resolve()
                screening_val = None
                for p, e in screening_energies if isinstance(screening_energies, list) else []:
                    if Path(p).resolve() == path_lookup:
                        screening_val = e
                        break

                mrrho_work_dir = self.fastsp_dir / path.stem
                g_total = self._compute_xtb_mrrho_free_energy(
                    xyz_file=path,
                    sp_energy=screening_val or 0.0,
                    output_dir=mrrho_work_dir,
                )

                if g_total is not None:
                    path_to_g.append((path, g_total))
                    mrrho_success += 1
                else:
                    # Fallback: use screening energy as proxy for G
                    path_to_g.append((path, screening_val if screening_val else float("inf")))

            # Sort by G (ascending) and apply survivor window
            path_to_g.sort(key=lambda x: x[1])
            if path_to_g:
                min_g = path_to_g[0][1]
                survivor_window = getattr(spec.funnel_policy, "survivor_window_kcal", None) or 3.0
                threshold = survivor_window / HARTREE_TO_KCAL  # kcal → Hartree

                mrrho_survivors = [p for p, g in path_to_g if (g - min_g) <= threshold]
                if not mrrho_survivors and path_to_g:
                    mrrho_survivors = [path_to_g[0][0]]
                final_paths = mrrho_survivors[: spec.ngeom_max]

            self.state_manager.complete_stage(
                "fastsp_mrrho",
                {
                    "n_input": mrrho_total,
                    "n_success": mrrho_success,
                    "n_survivors": len(final_paths),
                },
            )

            logger.info(
                "  [mrrho] xTB SPH+MRRHO: %d/%d conformers successful, %d survivors after window",
                mrrho_success,
                mrrho_total,
                len(final_paths),
            )

        return self._run_shared_dft_handoff(final_paths)

    def _run_lite_protocol(self, initial_xyz: Path) -> CandidateSet:
        """Run LITE protocol (CREST + fast-SP screening + MRRHO + DFT handoff)."""
        logger.info("[S1] Running LITE protocol")

        spec = self.protocol_spec

        # --- Steps 1-3: CREST → ISOSTAT clustering → ensemble processing ---
        if spec.enable_crest:
            ewin = getattr(self.protocol_spec, "crest_energy_window_kcal", None)
            ensemble_xyz = self._step_crest_search(initial_xyz, energy_window=ewin)
        else:
            ensemble_xyz = initial_xyz

        if spec.enable_clustering:
            clustered_xyz = self._step_isostat_clustering(ensemble_xyz)
        else:
            clustered_xyz = ensemble_xyz

        candidate_paths = self._step_process_ensemble(clustered_xyz)

        # Cap at ngeom_max before expensive fast-SP screening
        n_before = len(candidate_paths)
        if spec.ngeom_max is not None and spec.ngeom_max > 0 and n_before > spec.ngeom_max:
            candidate_paths = candidate_paths[: spec.ngeom_max]
            logger.info(
                "ngeom_max cap: %d → %d candidates before fast-SP screening",
                n_before,
                spec.ngeom_max,
            )

        # --- Step 4: Extract xTB energies from CREST ensemble ---
        xtb_energies = self._extract_xtb_energies_from_ensemble(ensemble_xyz)

        # --- Step 5: Build CandidateSet with xtb energies ---
        candidate_set = candidate_set_from_paths(candidate_paths, energies=xtb_energies)
        candidate_set.ranking_basis = "xtb_energy"

        # --- Step 6: r2SCAN-3c fast-SP screening ---
        sp_results = self._run_fast_sp_profile(candidate_paths, "r2SCAN-3c", "screening")

        # Populate screening_energy field on each candidate
        sp_map = {path: energy for path, energy in sp_results}
        for candidate in candidate_set.candidates:
            if candidate.source_file in sp_map:
                candidate.screening_energy = sp_map[candidate.source_file]

        # --- Step 7: MRRHO-like correction (CENSO-style via xTB SPH+MRRHO) ---
        mrrho_success = 0
        mrrho_total = 0
        if spec.funnel_policy.use_mrrho_like_correction:
            for candidate in candidate_set.candidates:
                mrrho_total += 1
                if candidate.source_file is not None and candidate.screening_energy is not None:
                    # Compute approximate free energy via xTB SPH+MRRHO
                    mrrho_work_dir = self.fastsp_dir / candidate.source_file.stem
                    g_total = self._compute_xtb_mrrho_free_energy(
                        xyz_file=candidate.source_file,
                        sp_energy=candidate.screening_energy,
                        output_dir=mrrho_work_dir,
                    )
                    if g_total is not None:
                        candidate.xtb_free_energy = g_total
                        mrrho_success += 1
                    else:
                        # MRRHO failed, fallback to xtb_energy
                        candidate.xtb_free_energy = candidate.xtb_energy
                        logger.debug(
                            "  [mrrho] Fallback to xtb_energy for %s",
                            candidate.source_file.name if candidate.source_file else "unknown",
                        )
                else:
                    # No source_file or screening_energy, use xtb_energy as fallback
                    candidate.xtb_free_energy = candidate.xtb_energy

            if mrrho_success > 0:
                candidate_set.ranking_basis = "xtb_free_energy"
                candidate_set.metadata["approx_thermo_applied"] = True
                candidate_set.metadata["mrrho_success_count"] = mrrho_success
                candidate_set.metadata["mrrho_total_count"] = mrrho_total

            logger.info(
                "  [mrrho] xTB SPH+MRRHO: %d/%d conformers successful, ranking basis: %s",
                mrrho_success,
                mrrho_total,
                candidate_set.ranking_basis,
            )

        # Store intermediate CandidateSet for test inspection
        self._lite_intermediate_cs = candidate_set

        # --- Step 8: Build (path, xtb_free_energy) tuples for Boltzmann ---
        sp_results_with_xtb_free_energy = [
            (
                c.source_file,
                c.xtb_free_energy
                if c.xtb_free_energy is not None
                else c.screening_energy
                if c.screening_energy is not None
                else c.energy,
            )
            for c in candidate_set.candidates
            if c.source_file is not None
        ]

        # Apply Boltzmann cutoff using xtb_free_energy
        boltzmann_filtered = self._apply_boltzmann_cutoff(
            sp_results_with_xtb_free_energy, cutoff=spec.funnel_policy.boltzmann_cutoff or 0.90
        )

        # --- Step 9: optimize_limit enforcement ---
        optimize_limit = spec.funnel_policy.optimize_limit
        if optimize_limit is not None and optimize_limit > 0:
            selected = boltzmann_filtered[:optimize_limit]
        else:
            selected = boltzmann_filtered

        # --- Step 10: top2 fallback ---
        if (
            spec.funnel_policy.top2_fallback_enabled
            and len(sp_results_with_xtb_free_energy) >= 2
            and len(selected) < 2
        ):
            # Sort the full candidate list by xtb_free_energy to find rank1/rank2
            sorted_full = sorted(sp_results_with_xtb_free_energy, key=lambda x: x[1])
            rank1_path, rank1_energy = sorted_full[0]
            rank2_path, rank2_energy = sorted_full[1]
            gap_kcal = abs(rank1_energy - rank2_energy) * HARTREE_TO_KCAL
            small_gap = spec.handoff_policy.small_gap_kcal or 1.0
            if gap_kcal <= small_gap:
                if rank2_path not in selected:
                    selected.append(rank2_path)
                    logger.info(
                        f"Top2 fallback triggered: gap={gap_kcal:.2f} "
                        f"kcal ≤ {small_gap} kcal, adding rank2"
                    )

        # --- Step 11: DFT optimization + SP + Shermo handoff ---
        return self._run_shared_dft_handoff(selected)

    def _run_zero_protocol(self, initial_xyz: Path) -> CandidateSet:
        """Run ZERO protocol (CREST → ISOSTAT → narrow window → DFT opt → SP)."""
        logger.info("[S1] Running ZERO protocol")

        spec = self.protocol_spec

        # --- Step 1: CREST conformer search ---
        if spec.enable_crest:
            ensemble_xyz = self._step_crest_search(initial_xyz)
        else:
            ensemble_xyz = initial_xyz

        # --- Step 2: Clustering ---
        if spec.enable_clustering:
            self.state_manager.set_stage("clustering")
            clustering_mode = spec.funnel_policy.clustering_mode
            if clustering_mode == "minimal":
                candidate_paths = self._step_process_ensemble(ensemble_xyz)
            else:
                clustered_xyz = self._step_isostat_clustering(ensemble_xyz)
                candidate_paths = self._step_process_ensemble(clustered_xyz)
            self.state_manager.complete_stage(
                "clustering",
                {
                    "n_candidates": len(candidate_paths),
                },
            )
        else:
            candidate_paths = self._step_process_ensemble(ensemble_xyz)

        # --- Step 3: Extract xTB energies from CREST ensemble ---
        xtb_energies = self._extract_xtb_energies_from_ensemble(ensemble_xyz)

        # --- Step 4: Build CandidateSet with xtb energies ---
        candidate_set = candidate_set_from_paths(candidate_paths, energies=xtb_energies)
        candidate_set.ranking_basis = "xtb_energy"

        # --- Step 5: Build (path, xtb_energy) tuples for filtering ---
        sp_results_with_energy = [
            (c.source_file, c.xtb_energy if c.xtb_energy is not None else c.energy)
            for c in candidate_set.candidates
            if c.source_file is not None and (c.xtb_energy is not None or c.energy != 0.0)
        ]

        # --- Step 6: Apply narrow window filter ---
        if sp_results_with_energy:
            window = spec.funnel_policy.narrow_window_kcal or 0.5
            filtered = self._select_by_energy_window(sp_results_with_energy, window)
        else:
            filtered = candidate_paths[:]

        # --- Step 7: optimize_limit enforcement ---
        optimize_limit = spec.funnel_policy.optimize_limit
        if optimize_limit is not None and optimize_limit > 0:
            filtered = filtered[:optimize_limit]

        # --- Step 8: Fallback — if filtered is empty, use wider window ---
        if not filtered and sp_results_with_energy:
            fallback_window = spec.funnel_policy.survivor_window_kcal or 3.0
            logger.info(
                f"  [zero-fallback] narrow window ({window} kcal) yielded no candidates, "
                f"expanding to {fallback_window} kcal"
            )
            filtered = self._select_by_energy_window(sp_results_with_energy, fallback_window)
            if optimize_limit is not None and optimize_limit > 0:
                filtered = filtered[:optimize_limit]

        # --- Step 9: DFT Optimization ---
        if spec.enable_optimization:
            self.state_manager.set_stage("optimization")
            opt_dir = self.final_dft_dir
            ensure_dir(opt_dir)
            optimized_paths = []
            for i, path in enumerate(filtered):
                logger.info(
                    f"  [opt] Optimization ({spec.opt_engine}:"
                    f"{spec.opt_method}/{spec.opt_basis}) "
                    f"for candidate {i + 1}/{len(filtered)}"
                )
                coords, symbols = read_xyz(path)
                opt_result = self.opt_interface.optimize(
                    coords,
                    symbols,
                    charge=self._current_charge,
                    multiplicity=self._current_multiplicity,
                    output_dir=opt_dir,
                    output_name=f"conf_{i:03d}_opt",
                )
                if opt_result.success and opt_result.energy is not None:
                    opt_xyz = opt_dir / f"conf_{i:03d}_opt.xyz"
                    write_xyz(opt_xyz, opt_result.coordinates, symbols)
                    optimized_paths.append((path, opt_xyz, True, opt_result.energy))
                else:
                    logger.warning(
                        f"  [opt] Optimization failed for {path.name}, using input coordinates"
                    )
                    optimized_paths.append((path, path, False, 0.0))
            self.state_manager.complete_stage(
                "optimization",
                {
                    "n_input": len(filtered),
                    "n_optimized": sum(1 for _, _, ok, *_ in optimized_paths if ok),
                },
            )
            coord_source = optimized_paths
        else:
            coord_source = [(p, p, False, 0.0) for p in filtered]

        # --- Step 9.5: Frequency Calculation ---
        if spec.enable_frequency:
            self.state_manager.set_stage("frequency")
            freq_dir = self.final_dft_dir
            ensure_dir(freq_dir)
            for j, (orig_path, xyz_path, opt_ok, *_) in enumerate(coord_source):
                logger.info(
                    f"  [freq] Frequency calculation ({spec.freq_engine}:"
                    f"{spec.opt_method}/{spec.opt_basis}) "
                    f"for candidate {j + 1}/{len(coord_source)}"
                )
                coords, symbols = read_xyz(xyz_path)
                freq_result = self.freq_interface.frequency(
                    coords,
                    symbols,
                    charge=self._current_charge,
                    multiplicity=self._current_multiplicity,
                    output_dir=freq_dir,
                    output_name=f"conf_{j:03d}_freq",
                )
                if freq_result.success:
                    logger.info(f"  [freq] Frequency successful for candidate {j + 1}")
                else:
                    logger.warning(
                        f"  [freq] Frequency failed for candidate {j + 1}, skipping thermo"
                    )
            self.state_manager.complete_stage(
                "frequency",
                {
                    "n_candidates": len(coord_source),
                },
            )

        # --- Step 10: High-precision Single-Point ---
        sp_results = {}
        shermo_results = {}
        if spec.enable_single_point:
            logger.info(
                f"  [sp] Running SP: {spec.final_sp_method}/{spec.final_sp_basis}, "
                f"solvent={spec.sp_solvent or 'gas phase'}"
            )
            self.state_manager.set_stage("single_point")
            for i, (orig_path, xyz_path, opt_ok, opt_energy) in enumerate(coord_source):
                coords, symbols = read_xyz(xyz_path)
                sp_dir = self.final_dft_dir
                ensure_dir(sp_dir)
                sp_result = self.sp_interface.single_point(
                    coords,
                    symbols,
                    charge=self._current_charge,
                    multiplicity=self._current_multiplicity,
                    output_dir=sp_dir,
                    output_name=f"conf_{i:03d}_sp",
                    method=spec.final_sp_method,
                    basis=spec.final_sp_basis,
                )
                if sp_result.success and sp_result.energy is not None:
                    sp_results[orig_path] = sp_result.energy
                else:
                    logger.warning(f"  [sp] SP failed for {orig_path.name}, fallback to opt energy")
                    sp_results[orig_path] = opt_energy if opt_ok else 0.0
            self.state_manager.complete_stage(
                "single_point",
                {
                    "n_total": len(coord_source),
                    "n_success": sum(1 for v in sp_results.values() if v is not None),
                },
            )

        # --- Step 11: Shermo thermochemistry ---
        if spec.enable_shermo and spec.enable_frequency and spec.enable_single_point:
            self.state_manager.set_stage("shermo")
            for i, (orig_path, xyz_path, opt_ok, opt_energy) in enumerate(coord_source):
                sp_energy = sp_results.get(orig_path)
                freq_log = self.final_dft_dir / f"conf_{i:03d}_freq.log"
                freq_out = self.final_dft_dir / f"conf_{i:03d}_freq.out"
                freq_log_path = (
                    freq_log if freq_log.exists() else freq_out if freq_out.exists() else None
                )

                if freq_log_path is not None and sp_energy is not None:
                    shermo_result = run_shermo(
                        freq_output=freq_log_path,
                        sp_energy=sp_energy,
                        output_dir=self.final_dft_dir,
                        shermo_bin=self.shermo_bin,
                        output_file=self.final_dft_dir / f"conf_{i:03d}_Shermo.sum",
                        temperature_k=self.thermo_config.get("temperature_k", 298.15),
                        pressure_atm=self.thermo_config.get("pressure_atm", 1.0),
                        scl_zpe=self.thermo_config.get("scl_zpe", 0.9905),
                        ilowfreq=self.thermo_config.get("shermo_ilowfreq", 2),
                        imagreal=self.thermo_config.get("shermo_imagreal", 0),
                        conc=self.thermo_config.get("shermo_conc"),
                    )
                    if shermo_result:
                        shermo_results[orig_path] = shermo_result
                else:
                    logger.warning(f"  [shermo] Skipping for {orig_path.name}")
            n_shermo = len(shermo_results)
            logger.info(f"  [shermo] Completed for {n_shermo}/{len(coord_source)} candidates")
            self.state_manager.complete_stage(
                "shermo",
                {
                    "n_total": len(coord_source),
                    "n_success": n_shermo,
                },
            )

        # --- Step 12: Final ranking with SP + Shermo ---
        logger.info("[S1] Step 12: Final ranking with SP+Shermo energies")
        self.state_manager.set_stage("final_ranking")

        candidates = []
        shermo_available = len(shermo_results) > 0
        ranking_basis = "shermo_gibbs" if shermo_available else "final_sp_minimum"

        for i, (orig_path, xyz_path, opt_ok, opt_energy) in enumerate(coord_source):
            coords, symbols = read_xyz(xyz_path)
            sp_energy = sp_results.get(orig_path)
            energy = sp_energy if sp_energy is not None else (opt_energy if opt_ok else 0.0)

            shermo_data = shermo_results.get(orig_path, {})
            candidates.append(
                ConformerCandidate(
                    index=i,
                    coordinates=coords,
                    symbols=symbols,
                    energy=energy,
                    gibbs_energy=shermo_data.get("g_conc") or shermo_data.get("g_sum"),
                    gibbs_correction=shermo_data.get("g_sum"),
                    h_correction=shermo_data.get("h_sum"),
                    u_correction=shermo_data.get("u_sum"),
                    s_total=shermo_data.get("s_total"),
                    source_file=orig_path,
                )
            )

        result_set = CandidateSet(candidates=candidates)
        result_set.calculate_boltzmann_weights()
        result_set.update_ranks()
        result_set.ranking_basis = ranking_basis
        result_set.metadata = {
            "shermo_available": shermo_available,
            "sp_method": spec.final_sp_method,
            "sp_basis": spec.final_sp_basis,
            "funnel_narrow_window_kcal": spec.funnel_policy.narrow_window_kcal,
            "funnel_optimize_limit": optimize_limit,
        }

        self.state_manager.complete_stage(
            "final_ranking",
            {
                "n_candidates": len(candidates),
                "ranking_basis": ranking_basis,
            },
        )

        return result_set

    def _cleanup_temp_files(self):
        """Remove .tmp files from finalDFT directory."""
        if not self.final_dft_dir.exists():
            return
        tmp_files = list(self.final_dft_dir.glob("**/*.tmp"))
        for f in tmp_files:
            f.unlink(missing_ok=True)
        logger.info(f"[S1] Cleaned up {len(tmp_files)} .tmp files")

    def _finalize_results(self, candidate_set: CandidateSet) -> Dict[str, Any]:
        """
        Finalize results and write output files.

        Args:
            candidate_set: Final candidate set

        Returns:
            Dictionary with final results
        """
        logger.info("[S1] Finalizing results")

        self.state_manager.set_stage("finalization")

        if not candidate_set.candidates:
            logger.warning("[S1] No conformers survived the pipeline — returning empty result")
            return {
                "global_min_xyz": None,
                "global_min_energy": None,
                "n_conformers": 0,
                "ensemble_file": None,
                "thermo_csv": None,
                "metadata": {
                    "protocol": self.protocol_spec.name,
                    "candidates": [],
                    "state_summary": self.state_manager.get_summary(),
                },
            }

        global_min = candidate_set.get_lowest_gibbs()

        global_min_xyz = self.work_dir / f"{self.molecule_name}_global_min.xyz"
        global_min_energy_val = (
            global_min.g_conc
            if global_min.g_conc is not None
            else (
                global_min.gibbs_energy
                if global_min.gibbs_energy is not None
                else global_min.energy
            )
        )
        global_min_energy_val = (
            global_min_energy_val if global_min_energy_val is not None else float("inf")
        )
        write_xyz(
            global_min_xyz,
            global_min.coordinates,
            global_min.symbols,
            title=f"Global minimum for {self.molecule_name}",
            energy=global_min_energy_val,
            comment=f"Rank {global_min.rank}, Weight {global_min.weight:.4f}",
        )

        ensemble_xyz = self.final_dft_dir / "all_conformers.xyz"
        with open(ensemble_xyz, "w") as f:
            for c in candidate_set.candidates:
                if c.symbols is None or c.coordinates is None:
                    continue
                e_fmt = f"{c.energy:.6f}" if c.energy is not None else "N/A"
                f.write(f"{len(c.symbols)}\n")
                f.write(f"Conformer {c.index}, E={e_fmt}, Rank={c.rank}, Weight={c.weight:.4f}\n")
                for sym, coord in zip(c.symbols, c.coordinates):
                    f.write(f"{sym:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}\n")

        thermo_csv = self.final_dft_dir / "conformer_thermo.csv"
        with open(thermo_csv, "w") as f:
            f.write(
                "index,rank,energy_hartree,gibbs_correction,gibbs_hartree,h_correction,u_correction,s_total,g_conc,weight,source\n"
            )
            for c in candidate_set.candidates:
                f.write(f"{c.index},{c.rank},")
                f.write(
                    f"{c.energy:.10f},"
                    if c.energy is not None and c.energy != float("inf")
                    else "N/A,"
                )
                f.write(f"{c.gibbs_correction:.10f}," if c.gibbs_correction is not None else f",")
                f.write(f"{c.gibbs_energy:.10f}," if c.gibbs_energy is not None else f",")
                f.write(f"{c.h_correction:.10f}," if c.h_correction is not None else f",")
                f.write(f"{c.u_correction:.10f}," if c.u_correction is not None else f",")
                f.write(f"{c.s_total:.10f}," if c.s_total is not None else f",")
                f.write(f"{c.g_conc:.10f}," if c.g_conc is not None else f",")
                f.write(f"{c.weight:.6f},")
                f.write(f"{c.source_file.name if c.source_file else 'unknown'}\n")

        self.state_manager.complete_stage(
            "finalization",
            {
                "global_min_file": str(global_min_xyz),
                "global_min_energy": global_min_energy_val,
                "n_conformers": len(candidate_set.candidates),
                "ensemble_file": str(ensemble_xyz),
            },
        )

        self._cleanup_temp_files()

        return {
            "global_min_xyz": global_min_xyz,
            "global_min_energy": global_min_energy_val,
            "n_conformers": len(candidate_set.candidates),
            "metadata": {
                "protocol": self.protocol_spec.name,
                "candidates": [c.to_dict() for c in candidate_set.candidates],
                "state_summary": self.state_manager.get_summary(),
            },
        }

    def _compute_xtb_mrrho_free_energy(
        self,
        xyz_file: Path,
        sp_energy: float,
        output_dir: Path,
    ) -> Optional[float]:
        """
        Compute approximate Gibbs free energy for a single conformer
        using xTB Single-Point Hessian + MRRHO (CENSO-style).

        Uses the CENSO formula: G_total = sp_energy + G_mRRHO.

        Args:
            xyz_file: Path to conformer XYZ file.
            sp_energy: r2SCAN-3c single-point energy (Hartree).
                Used in CENSO formula: G_total = sp_energy + G_mRRHO.
            output_dir: Output directory for xTB SPH files.

        Returns:
            G_total = sp_energy + G_mRRHO in Hartree on success, None on failure.
            Uses the CENSO formula to combine DFT electronic energy with
            xTB SPH+mRRHO thermal corrections.
        """
        # Read MRRHO settings — protocol-level takes priority, fall back to top-level
        mrrho_settings = {}
        if self.protocol_spec and self.protocol_spec.mrrho_settings:
            mrrho_settings = self.protocol_spec.mrrho_settings
        if not mrrho_settings:
            mrrho_settings = self.config.get("mrrho_settings", {})

        output_dir = Path(output_dir)

        try:
            coords, symbols = read_xyz(xyz_file)
        except Exception as e:
            logger.warning("  [mrrho] Failed to read XYZ for MRRHO: %s: %s", xyz_file.name, e)
            return None

        unpaired = self._current_multiplicity - 1

        try:
            _solvent = mrrho_settings.get("solvent") or self.theory_preopt.get("solvent")
            solvent = str(_solvent) if _solvent else None
            logger.info(f"  [mrrho] Solvent resolved: {solvent}")
            xtb_result = self.xtb_interface.enso_thermo(
                coordinates=coords,
                symbols=symbols,
                output_dir=output_dir,
                gfn_level=int(mrrho_settings.get("gfn_level", 2)),
                temperature_k=float(
                    mrrho_settings.get(
                        "temperature_k", self.config.get("thermo", {}).get("temperature_k", 298.15)
                    )
                ),
                sthr=float(mrrho_settings.get("sthr", 50.0)),
                imagthr=float(mrrho_settings.get("imagthr", -100.0)),
                charge=self._current_charge,
                multiplicity=self._current_multiplicity,
                solvent=solvent,
            )
        except Exception as e:
            logger.warning("  [mrrho] xTB SPH+MRRHO failed for %s: %s", xyz_file.name, e)
            return None

        if not xtb_result.success:
            logger.warning(
                "  [mrrho] xTB SPH+MRRHO failed for %s: %s", xyz_file.name, xtb_result.error
            )
            return None

        g_total = sp_energy + float(xtb_result.g_total)
        logger.info(
            "  [mrrho] G_total=%.8f Hartree (SP: %.8f + corr: %.8f) for %s",
            g_total,
            sp_energy,
            float(xtb_result.g_total),
            xyz_file.name,
        )
        return g_total

    def _run_fast_sp_profile(
        self, candidate_paths: List[Path], method: str, output_subdir: str
    ) -> List[Tuple[Path, float]]:
        """
        Run fast single-point calculations on conformer candidates.

        Performs sequential ORCA single-point energy calculations for each
        candidate conformer using the specified composite method (e.g.,
        'PBEh-3c' or 'r2SCAN-3c'). Failed calculations are logged as
        warnings and excluded from the returned list.

        Args:
            candidate_paths: List of paths to conformer XYZ files.
            method: ORCA method keyword (e.g., 'PBEh-3c', 'r2SCAN-3c').
            output_subdir: Subdirectory name under fastsp_dir.

        Returns:
            List of (path, energy_hartree) tuples for successfully computed
            candidates.
        """
        stage_name = f"fastsp_{output_subdir}"
        self.state_manager.set_stage(stage_name)

        out_dir = self.fastsp_dir / output_subdir
        ensure_dir(out_dir)

        results: List[Tuple[Path, float]] = []
        n_total = len(candidate_paths)

        for i, xyz_path in enumerate(candidate_paths):
            logger.info(f"  [fast-SP {output_subdir}] {i + 1}/{n_total}: {xyz_path.name}")

            try:
                coords, symbols = read_xyz(xyz_path)
                if len(symbols) == 0:
                    logger.warning(
                        f"  [fast-SP {output_subdir}] Empty coordinates in "
                        f"{xyz_path.name}, skipping"
                    )
                    continue

                sp_result = self.sp_interface.single_point(
                    coords,
                    symbols,
                    charge=self._current_charge,
                    multiplicity=self._current_multiplicity,
                    output_dir=out_dir,
                    output_name=f"{xyz_path.stem}_{output_subdir}",
                    method=method,
                )

                if sp_result.success and sp_result.energy is not None:
                    results.append((xyz_path, sp_result.energy))
                    logger.info(f"    Energy: {sp_result.energy:.8f} Hartree")
                else:
                    logger.warning(
                        f"  [fast-SP {output_subdir}] SP failed for "
                        f"{xyz_path.name}: {sp_result.error_message}"
                    )

            except Exception as exc:
                logger.warning(f"  [fast-SP {output_subdir}] Exception for {xyz_path.name}: {exc}")

        self.state_manager.complete_stage(
            stage_name,
            {
                "n_input": n_total,
                "n_completed": len(results),
                "method": method,
                "output_dir": str(out_dir),
            },
        )

        logger.info(f"  [fast-SP {output_subdir}] Completed {len(results)}/{n_total} candidates")

        return results

    def _select_by_energy_window(
        self, candidates_with_energy: List[Tuple[Path, float]], window_kcal: float
    ) -> List[Path]:
        """
        Filter candidates within an energy window of the global minimum.

        Args:
            candidates_with_energy: List of (path, energy_hartree) tuples.
            window_kcal: Energy window in kcal/mol.

        Returns:
            List of Path objects for candidates within the window.
        """
        if not candidates_with_energy:
            return []

        min_energy = min(e for _, e in candidates_with_energy)
        window_hartree = window_kcal / HARTREE_TO_KCAL

        kept: List[Path] = []
        for path, energy in candidates_with_energy:
            if (energy - min_energy) <= window_hartree:
                kept.append(path)

        logger.info(
            f"  [energy-window] {len(kept)}/{len(candidates_with_energy)} "
            f"conformers within {window_kcal:.1f} kcal/mol "
            f"({window_hartree:.6f} Hartree) window"
        )

        return kept

    def _apply_boltzmann_cutoff(
        self, candidates_with_energy: List[Tuple[Path, float]], cutoff: float = 0.90
    ) -> List[Path]:
        """
        Filter candidates by cumulative Boltzmann weight.

        Converts Hartree energies to Boltzmann weights at the configured
        temperature, sorts by ascending energy, and accumulates weights
        until the cumulative sum reaches *cutoff*.

        Args:
            candidates_with_energy: List of (path, energy_hartree) tuples.
            cutoff: Cumulative Boltzmann weight threshold (default 0.90).

        Returns:
            List of Path objects for kept candidates, in ascending energy
            order.
        """
        logger.debug(
            "MRRho-like correction not implemented, using raw "
            f"{self.config.get('theory', {}).get('single_point', {}).get('method', 'unknown')} energies"
        )

        if not candidates_with_energy:
            return []

        temperature_k = self.thermo_config.get("temperature_k", 298.15)

        R_KCAL = 0.0019872041  # kcal/(mol·K)

        valid = [(p, e) for p, e in candidates_with_energy if e is not None]
        if not valid:
            return [p for p, _ in candidates_with_energy]
        min_energy = min(e for _, e in valid)

        weights: List[float] = []
        for _, energy in valid:
            delta_kcal = (energy - min_energy) * HARTREE_TO_KCAL
            weight = math.exp(-delta_kcal / (R_KCAL * temperature_k))
            weights.append(weight)

        total_weight = sum(weights)
        if total_weight > 0:
            weights = [w / total_weight for w in weights]

        sorted_candidates = sorted(zip(valid, weights), key=lambda item: item[0][1])

        kept: List[Path] = []
        cumulative = 0.0
        for (path, energy), weight in sorted_candidates:
            kept.append(path)
            cumulative += weight
            if cumulative >= cutoff:
                break

        logger.info(
            f"  [boltzmann-cutoff] {len(kept)}/{len(candidates_with_energy)} "
            f"conformers retained (cutoff={cutoff:.2f}, "
            f"cumulative={cumulative:.4f})"
        )

        return kept

    def _apply_mrrho_reranking(self, candidate_set: "CandidateSet") -> "CandidateSet":
        """Placeholder for MRRho-like reranking correction.

        Currently a no-op. The MRRho correction formula is not yet implemented.

        Args:
            candidate_set: CandidateSet to potentially re-rank.
        Returns:
            CandidateSet unchanged.
        """
        logger.debug("MRRho-like reranking not implemented — returning candidates unchanged")
        return candidate_set
