"""
ConformerEngine
===============

Main conformer search engine.

Author: QCcalc Team (adapted from RPH)
"""

import logging
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import numpy as np

from conformer_search.core.protocols import (
    is_censo_protocol, is_ext_protocol, ProtocolSpec, resolve_protocol_spec
)
from conformer_search.core.spec_adapter import resolve_any_protocol
from conformer_search.core.specs import ConformerWorkflowSpec
from conformer_search.core.candidates import (
    CandidateSet, 
    ConformerCandidate,
    candidate_set_from_paths,
    clone_candidate_set
)
from conformer_search.ensemble.candidate_set import FunnelRecordSet
from conformer_search.core.state_manager import ConformerStateManager
from conformer_search.io.input_handler import MolecularInput, InputFormat
from conformer_search.qc.interfaces import (
    GaussianInterface,
    ORCAInterface,
    CRESTInterface,
    XTBInterface,
    QCResult
)
from conformer_search.recipes.adapter import (
    funnel_records_from_paths,
    candidate_set_from_funnel_records,
)
from conformer_search.recipes.censo_parts import (
    run_part0,
    run_part1,
    run_part2,
    run_part3,
    KEY_XTB,
    KEY_LOWCOST,
    KEY_R2SCAN,
    KEY_FINAL_E,
    KEY_FINAL_G,
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
        protocol_spec: Optional[ProtocolSpec] = None
    ):
        """
        Initialize conformer engine.

        Args:
            config: Configuration dictionary
            work_dir: Working directory
            molecule_name: Name for this molecule
        protocol: Protocol name (ext, censo-*, legacy-*, reference-sp, allopt)
        protocol_spec: Pre-resolved protocol spec (optional)
        """
        self.config = config
        self.molecule_name = molecule_name
        self.protocol = protocol.lower()
        self._current_charge = 0
        self._current_multiplicity = 1
        
        work_dir = Path(work_dir).resolve()
        self.work_dir = work_dir / molecule_name
        ensure_dir(self.work_dir)
        
        self.protocol_spec = protocol_spec or resolve_protocol_spec(config, self.protocol)
        
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
        self.prescan_dir = self.work_dir / "prescan"
        self.fastsp_dir = self.work_dir / "fastsp"
        self.final_dft_dir = self.work_dir / "finalDFT"
        
        for d in [self.rdkit_dir, self.crest_dir, self.cluster_dir, 
                  self.prescan_dir, self.fastsp_dir, self.final_dft_dir]:
            ensure_dir(d)

    def _create_qc_interface(self, engine: str, theory_config: Dict[str, Any]):
        """Create QC interface instance based on engine type."""
        method = theory_config.get('method') or 'B3LYP'
        basis = theory_config.get('basis') or 'def2-SVP'
        dispersion = theory_config.get('dispersion') or 'GD3BJ'
        solvent_model = theory_config.get('solvent_model') or 'smd'

        if engine == "gaussian":
            return GaussianInterface(
                config=self.config,
                method=method,
                basis=basis,
                dispersion=dispersion,
                solvent=theory_config.get('solvent'),
                solvent_model=solvent_model
            )
        elif engine == "orca":
            return ORCAInterface(
                config=self.config,
                method=method,
                basis=basis,
                solvent=theory_config.get('solvent'),
                solvent_model=solvent_model
            )
        else:
            raise ValueError(f"Unknown engine: {engine}")

    def _setup_qc_interfaces(self):
        """Initialize QC interfaces via unified method resolution."""
        from conformer_search.core.method_resolution import resolve_qc_method

        self.theory_opt = self.config.get('theory', {}).get('optimization', {})
        self.theory_sp = self.config.get('theory', {}).get('single_point', {})
        self.theory_preopt = self.config.get('theory', {}).get('preoptimization', {})

        proto_name = self.protocol_spec.name
        qc_opt = resolve_qc_method(self.config, stage="optimization", protocol_name=proto_name)
        self.gaussian_interface = GaussianInterface(
            config=self.config,
            method=qc_opt.method,
            basis=qc_opt.basis or 'def2-SVP',
            dispersion=qc_opt.dispersion,
            solvent=qc_opt.solvent,
            solvent_model=qc_opt.solvent_model,
        )

        opt_engine = self.protocol_spec.opt_engine or qc_opt.engine or 'gaussian'
        self.opt_interface = self._create_qc_interface(opt_engine, {
            'method': qc_opt.method,
            'basis': qc_opt.basis or 'def2-SVP',
            'dispersion': qc_opt.dispersion,
            'solvent': qc_opt.solvent,
            'solvent_model': qc_opt.solvent_model,
        })
        freq_engine = self.protocol_spec.freq_engine or opt_engine
        self.freq_interface = self._create_qc_interface(freq_engine, {
            'method': qc_opt.method,
            'basis': qc_opt.basis or 'def2-SVP',
            'dispersion': qc_opt.dispersion,
            'solvent': qc_opt.solvent,
            'solvent_model': qc_opt.solvent_model,
        })

        qc_sp = resolve_qc_method(self.config, stage="final_sp", protocol_name=proto_name)
        sp_engine = qc_sp.engine or 'gaussian'
        if sp_engine == 'gaussian':
            self.orca_interface = GaussianInterface(
                config=self.config,
                method=qc_sp.method,
                basis=qc_sp.basis or 'def2-TZVPP',
                solvent=qc_sp.solvent,
                solvent_model=qc_sp.solvent_model,
            )
        else:
            self.orca_interface = ORCAInterface(
                config=self.config,
                method=qc_sp.method,
                basis=qc_sp.basis or 'def2-TZVPP',
                solvent=qc_sp.solvent,
                solvent_model=qc_sp.solvent_model,
            )
        
        crest_gfn = self.config.get('executables', {}).get('crest', {}).get('gfn_level', 2)
        self.crest_interface = CRESTInterface(
            config=self.config,
            gfn_level=crest_gfn,
            solvent=self.theory_preopt.get('solvent')
        )
        
        xtb_gfn = self.theory_preopt.get('gfn_level', 2)
        self.xtb_interface = XTBInterface(
            config=self.config,
            gfn_level=xtb_gfn,
            solvent=self.theory_preopt.get('solvent')
        )
        
        self.shermo_bin = self.config.get('executables', {}).get('shermo', {}).get('path', 'Shermo')
        self.thermo_config = self.config.get('thermo', {})
        
        logger.info(f"  QC engines — opt: {self.protocol_spec.opt_engine}, freq: {self.protocol_spec.freq_engine}, sp: orca")

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
            smiles=molecular_input.metadata.get('smiles', 'unknown'),
            two_stage_enabled=self.protocol_spec.two_stage_enabled
        )
        
        self.state_manager.set_protocol_signature(
            protocol=self.protocol_spec.name,
            funnel_signature={
                'search_mode': self.protocol_spec.funnel_policy.search_mode,
                'two_stage': self.protocol_spec.two_stage_enabled,
                'ngeom_default': self.protocol_spec.ngeom_default,
            }
        )
        
        # Store molecular properties for QC calculations
        self._current_charge = molecular_input.charge
        self._current_multiplicity = molecular_input.multiplicity
        logger.info(f"Molecular charge: {self._current_charge}, multiplicity: {self._current_multiplicity}")

        if molecular_input.source_format == InputFormat.SMILES:
            initial_xyz = self._step_rdkit_embed(molecular_input)
        else:
            initial_xyz = self._save_initial_structure(molecular_input)
        
        if is_censo_protocol(self.protocol_spec):
            workflow_spec = resolve_any_protocol(self.protocol_spec.name, config=self.config)
            candidate_set = self._run_censo_protocol(initial_xyz, spec=workflow_spec)
        else:
            # ext, allopt, reference-sp — all use the shared ext-style pipeline
            candidate_set = self._run_ext_protocol(initial_xyz)
        
        final_result = self.finalize(candidate_set)
        
        self.state_manager.mark_completed()
        
        return (
            final_result['global_min_xyz'],
            final_result['global_min_energy'],
            final_result['metadata']
        )

    def run_crest(self, initial_xyz: Path) -> Path:
        """Run the configured CREST search and return the ensemble XYZ path."""
        if not self.protocol_spec.enable_crest:
            return initial_xyz

        if self.protocol_spec.two_stage_enabled:
            _, ensemble_xyz = self._step_two_stage_crest(initial_xyz)
            return ensemble_xyz

        return self._step_crest_search(initial_xyz)

    def run_isostat(self, ensemble_xyz: Path) -> list[Path]:
        """Run ISOSTAT clustering and split the clustered ensemble into XYZ candidates."""
        clustered_xyz = self._step_isostat_clustering(ensemble_xyz)
        return self._step_process_ensemble(clustered_xyz)

    def run_dft_handoff(self, candidate_paths: list[Path]) -> CandidateSet:
        """Run the shared DFT handoff over candidate XYZ paths."""
        return self._run_shared_dft_handoff(candidate_paths)

    def run_zero_sp(self, initial_xyz: Path) -> CandidateSet:
        """Run single-point-only workflow (used by reference-sp)."""
        return self._run_zero_protocol(initial_xyz)

    def finalize(self, candidate_set: CandidateSet) -> dict[str, Any]:
        """Finalize a candidate set and write the legacy output artifacts."""
        return self._finalize_results(candidate_set)

    def _step_rdkit_embed(self, molecular_input: MolecularInput) -> Path:
        """
        Generate 3D structure from SMILES using RDKit.

        Args:
            molecular_input: Input with SMILES

        Returns:
            Path to initial XYZ file
        """
        logger.info("[S1] Step 1: RDKit 3D embedding")
        
        self.state_manager.set_stage('rdkit_embed')
        
        output_path = self.rdkit_dir / f"{self.molecule_name}_init.xyz"
        
        write_xyz(
            output_path,
            molecular_input.coordinates,
            molecular_input.symbols,
            title=f"RDKit embedding for {self.molecule_name}"
        )
        
        self.state_manager.complete_stage('rdkit_embed', {
            'output_file': str(output_path),
            'n_atoms': len(molecular_input.symbols)
        })
        
        return output_path

    def _save_initial_structure(self, molecular_input: MolecularInput) -> Path:
        """Save initial structure to rdkit directory."""
        output_path = self.rdkit_dir / f"{self.molecule_name}_init.xyz"
        write_xyz(
            output_path,
            molecular_input.coordinates,
            molecular_input.symbols,
            title=f"Initial structure for {self.molecule_name}"
        )
        return output_path

    def _step_crest_search(self, initial_xyz: Path) -> Path:
        """
        Run CREST conformer search.

        Args:
            initial_xyz: Initial XYZ file

        Returns:
            Path to CREST ensemble XYZ
        """
        logger.info("[S1] Step 2: CREST conformer search")
        
        self.state_manager.set_stage('crest_search')
        
        result = self.crest_interface.run_conformer_search(
            *read_xyz(initial_xyz),
            output_dir=self.crest_dir,
            output_name=self.molecule_name,
            charge=self._current_charge,
            multiplicity=self._current_multiplicity
        )
        
        if not result.success:
            raise RuntimeError(f"CREST search failed: {result.error_message}")
        if result.output_file is None:
            raise RuntimeError("CREST search succeeded without an ensemble output file")
        
        ensemble_path = result.output_file
        
        self.state_manager.complete_stage('crest_search', {
            'ensemble_file': str(ensemble_path),
            'n_conformers': result.metadata.get('n_conformers', 0)
        })
        
        return ensemble_path

    def _step_two_stage_crest(self, initial_xyz: Path) -> Tuple[Path, Path]:
        """
        Run two-stage CREST search (GFN0 -> GFN2).

        Args:
            initial_xyz: Initial XYZ file

        Returns:
            Tuple of (stage1_ensemble, stage2_ensemble)
        """
        logger.info("[S1] Step 2: Two-stage CREST (GFN0 → GFN2)")
        
        self.state_manager.set_stage('crest_stage1')
        
        stage1_kwargs = {
            'crest_flags': '--gfn0',
            'energy_window': 10.0
        }
        
        stage1_result, stage2_result = self.crest_interface.run_two_stage_search(
            *read_xyz(initial_xyz),
            output_dir=self.crest_dir,
            stage1_kwargs=stage1_kwargs,
            charge=self._current_charge,
            multiplicity=self._current_multiplicity
        )
        
        if not stage2_result.success:
            raise RuntimeError(f"Two-stage CREST failed: {stage2_result.error_message}")
        if stage1_result.output_file is None:
            raise RuntimeError("Two-stage CREST stage1 did not produce an output file")
        if stage2_result.output_file is None:
            raise RuntimeError("Two-stage CREST stage2 did not produce an output file")
        
        self.state_manager.complete_stage('crest_stage1', {
            'output': str(stage1_result.output_file) if stage1_result.success else None
        })
        
        self.state_manager.set_stage('crest_stage2')
        self.state_manager.complete_stage('crest_stage2', {
            'ensemble_file': str(stage2_result.output_file),
            'n_conformers': stage2_result.metadata.get('n_conformers', 0)
        })
        
        return stage1_result.output_file, stage2_result.output_file

    def _step_isostat_clustering(self, ensemble_xyz: Path) -> Path:
        """
        Run ISOSTAT clustering on ensemble.

        Args:
            ensemble_xyz: CREST ensemble XYZ

        Returns:
            Path to clustered XYZ
        """
        logger.info("[S1] Step 3: ISOSTAT clustering")
        
        self.state_manager.set_stage('clustering')
        
        cluster_xyz, cluster_data = run_isostat(
            ensemble_xyz=ensemble_xyz,
            output_dir=self.cluster_dir,
            config=self.config,
            gdis=0.125,
            edis=1.0,
            temperature=298.15
        )
        if cluster_xyz is None:
            raise RuntimeError("ISOSTAT clustering did not produce an output file")
        
        self.state_manager.complete_stage('clustering', {
            'clustered_file': str(cluster_xyz),
            'n_clusters': len(cluster_data)
        })
        
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

    def _run_xtb_single_point_energies(self, candidate_paths: List[Path]) -> List[float]:
        """Return xTB single-point energies for initial funnel records."""
        energies = []

        for i, path in enumerate(candidate_paths):
            energy = 0.0

            try:
                coords, symbols = read_xyz(path)
                xtb_output_dir = self.fastsp_dir / f"conf_{i:03d}"
                ensure_dir(xtb_output_dir)
                result = self.xtb_interface.single_point(
                    coords,
                    symbols,
                    charge=self._current_charge,
                    multiplicity=self._current_multiplicity,
                    output_dir=xtb_output_dir,
                )
                if result.success and result.energy is not None:
                    energy = float(result.energy)
                else:
                    logger.warning(
                        "xTB single-point failed for %s; using 0.0 Hartree placeholder",
                        path.name,
                    )
            except Exception as exc:
                logger.warning(
                    "xTB single-point failed for %s (%s); using 0.0 Hartree placeholder",
                    path.name,
                    exc,
                )

            # TODO: replace this placeholder fallback with strict part-level QC execution.
            energies.append(energy)

        return energies

    def _build_censo_funnel_records(self, candidate_paths: List[Path]) -> FunnelRecordSet:
        """Create initial funnel records from candidate XYZ files."""
        xtb_energies = self._run_xtb_single_point_energies(candidate_paths)
        return funnel_records_from_paths(
            candidate_paths,
            energy_key=KEY_XTB,
            energies=xtb_energies,
        )

    def _seed_censo_energy_key(
        self,
        records: FunnelRecordSet,
        target_key: str,
        fallback_keys: List[str],
    ) -> None:
        """Populate a funnel energy key from prior stage energies when absent."""
        for record in records:
            if record.energies.get(target_key) is not None:
                continue

            energy_value = None
            for fallback_key in fallback_keys:
                candidate_energy = record.energies.get(fallback_key)
                if candidate_energy is not None:
                    energy_value = float(candidate_energy)
                    break

            if energy_value is None:
                energy_value = 0.0

            record.energies[target_key] = energy_value

    def _fallback_keys_for_censo_energy(self, target_key: str) -> List[str]:
        """Return the carry-forward energy keys used when QC SP work fails."""
        fallback_map = {
            KEY_LOWCOST: [KEY_XTB],
            KEY_R2SCAN: [KEY_LOWCOST, KEY_XTB],
            KEY_FINAL_E: [KEY_R2SCAN, KEY_LOWCOST, KEY_XTB],
            KEY_FINAL_G: [KEY_FINAL_E, KEY_R2SCAN, KEY_LOWCOST, KEY_XTB],
        }
        return list(fallback_map.get(target_key, []))

    def _run_dft_sp_for_records(
        self,
        records: FunnelRecordSet,
        target_key: str,
        stage: str = "final_sp",
    ) -> bool:
        from conformer_search.core.method_resolution import resolve_qc_method

        fallback_keys = self._fallback_keys_for_censo_energy(target_key)
        qc = resolve_qc_method(self.config, stage=stage, protocol_name=self.protocol_spec.name)
        engine_name = qc.engine or "orca"

        interface = self._create_qc_interface(engine_name, {
            'method': qc.method,
            'basis': qc.basis or '',
            'dispersion': qc.dispersion,
            'solvent': qc.solvent,
            'solvent_model': qc.solvent_model,
        })
        exe_path = getattr(interface, "exe_path", None)
        if exe_path is not None and shutil.which(str(exe_path)) is None:
            logger.warning(
                "%s executable not found at %s; falling back to seeded '%s' energies",
                engine_name,
                exe_path,
                target_key,
            )
            self._seed_censo_energy_key(records, target_key, fallback_keys)
            return False

        active_records = [record for record in records if record.status == "active"]
        if not active_records:
            self._seed_censo_energy_key(records, target_key, fallback_keys)
            return False

        any_real = False
        for record in active_records:
            xyz_path_value = record.metadata.get("xyz_path") or record.xyz_path
            if xyz_path_value is None:
                logger.warning(
                    "Skipping %s for '%s': missing XYZ path",
                    record.conformer_id,
                    target_key,
                )
                continue

            xyz_path = Path(xyz_path_value)
            if not xyz_path.exists():
                logger.warning(
                    "Skipping %s for '%s': XYZ file not found at %s",
                    record.conformer_id,
                    target_key,
                    xyz_path,
                )
                continue

            try:
                coordinates, symbols = read_xyz(xyz_path)
                output_dir = self.work_dir / "censo_sp" / target_key / record.conformer_id
                ensure_dir(output_dir)
                result = interface.single_point(
                    coordinates,
                    symbols,
                    charge=self._current_charge,
                    multiplicity=self._current_multiplicity,
                    output_dir=output_dir,
                    output_name=f"{record.conformer_id}_{target_key}",
                )
            except Exception as exc:
                logger.warning(
                    "DFT SP failed for %s at '%s': %s",
                    record.conformer_id,
                    target_key,
                    exc,
                )
                continue

            if result.success and result.energy is not None:
                record.energies[target_key] = float(result.energy)
                any_real = True
                continue

            logger.warning(
                "DFT SP failed for %s at '%s'%s",
                record.conformer_id,
                target_key,
                f": {result.error_message}" if result.error_message else "",
            )

        self._seed_censo_energy_key(records, target_key, fallback_keys)
        return any_real

    def _run_dft_opt_freq_for_records(
        self,
        records: FunnelRecordSet,
        target_key: str,
        stage: str = "optimization",
    ) -> bool:
        from conformer_search.core.method_resolution import resolve_qc_method

        fallback_keys = self._fallback_keys_for_censo_energy(target_key)
        qc = resolve_qc_method(self.config, stage=stage, protocol_name=self.protocol_spec.name)
        engine_name = qc.engine or 'gaussian'

        interface = self._create_qc_interface(engine_name, {
            'method': qc.method,
            'basis': qc.basis or '',
            'dispersion': qc.dispersion,
            'solvent': qc.solvent,
            'solvent_model': qc.solvent_model,
        })
        exe_path = getattr(interface, "exe_path", None)
        if exe_path is not None and shutil.which(str(exe_path)) is None:
            logger.warning(
                "%s executable not found; falling back to seeded '%s' energies",
                engine_name, target_key,
            )
            self._seed_censo_energy_key(records, target_key, fallback_keys)
            return False

        active_records = [r for r in records if r.status == "active"]
        if not active_records:
            self._seed_censo_energy_key(records, target_key, fallback_keys)
            return False

        any_real = False
        for record in active_records:
            xyz_path_value = record.metadata.get("xyz_path") or record.xyz_path
            if xyz_path_value is None:
                continue
            xyz_path = Path(xyz_path_value)
            if not xyz_path.exists():
                continue

            try:
                coordinates, symbols = read_xyz(xyz_path)
                output_dir = self.work_dir / "censo_optfreq" / target_key / record.conformer_id
                ensure_dir(output_dir)

                opt_result = interface.optimize(
                    coordinates, symbols,
                    charge=self._current_charge,
                    multiplicity=self._current_multiplicity,
                    output_dir=output_dir,
                    output_name=f"{record.conformer_id}_opt",
                )
                if not opt_result.success or opt_result.coordinates is None:
                    logger.warning("Opt failed for %s at '%s'", record.conformer_id, target_key)
                    continue

                opt_coords = opt_result.coordinates
                opt_energy = opt_result.energy

                freq_result = interface.frequency(
                    opt_coords, symbols,
                    charge=self._current_charge,
                    multiplicity=self._current_multiplicity,
                    output_dir=output_dir,
                    output_name=f"{record.conformer_id}_freq",
                )

                if opt_energy is not None:
                    record.energies[target_key] = float(opt_energy)
                    any_real = True

                if freq_result.success and freq_result.log_file is not None:
                    record.metadata["freq_log"] = str(freq_result.log_file)
                    opt_xyz = output_dir / f"{record.conformer_id}_optimized.xyz"
                    write_xyz(opt_xyz, opt_coords, symbols)
                    record.metadata["xyz_path"] = str(opt_xyz)
                    record.metadata["optimized"] = True

                if freq_result.zpe is not None:
                    record.metadata["zpe"] = freq_result.zpe

            except Exception as exc:
                logger.warning("Opt+freq failed for %s at '%s': %s", record.conformer_id, target_key, exc)

        if not any_real:
            self._seed_censo_energy_key(records, target_key, fallback_keys)
        return any_real

    def _apply_thermo_correction(
        self,
        records: FunnelRecordSet,
        energy_key: str,
        gibbs_key: str,
        temperature: float = 298.15,
    ) -> bool:
        from conformer_search.qc.runners.shermo import run_shermo

        shermo_bin = self.config.get('executables', {}).get('shermo', {}).get('path', 'Shermo')
        if shutil.which(shermo_bin) is None:
            logger.warning("Shermo not found; skipping thermal correction for '%s'", gibbs_key)
            self._seed_censo_energy_key(records, gibbs_key, [energy_key])
            return False

        thermo_cfg = self.config.get('thermo', {})
        any_corrected = False

        for record in records:
            if record.status != "active":
                continue

            sp_energy = record.energies.get(energy_key)
            freq_log = record.metadata.get("freq_log")

            if sp_energy is None or freq_log is None:
                continue

            freq_path = Path(freq_log)
            if not freq_path.exists():
                continue

            try:
                output_dir = self.work_dir / "censo_thermo" / record.conformer_id
                ensure_dir(output_dir)
                shermo_result = run_shermo(
                    freq_output=freq_path,
                    sp_energy=sp_energy,
                    output_dir=output_dir,
                    shermo_bin=shermo_bin,
                    output_file=output_dir / f"{record.conformer_id}_Shermo.sum",
                    temperature_k=thermo_cfg.get('temperature_k', temperature),
                    pressure_atm=thermo_cfg.get('pressure_atm', 1.0),
                    scl_zpe=thermo_cfg.get('scl_zpe', 0.9905),
                    ilowfreq=thermo_cfg.get('shermo_ilowfreq', 2),
                    imagreal=thermo_cfg.get('shermo_imagreal', 0),
                    conc=thermo_cfg.get('shermo_conc'),
                )
                if shermo_result and shermo_result.get('g_sum') is not None:
                    g_sum = shermo_result['g_sum']
                    g_conc = shermo_result.get('g_conc')
                    record.energies[gibbs_key] = float(g_conc if g_conc is not None else g_sum)
                    record.metadata["gibbs_correction"] = float(g_sum) - float(sp_energy)
                    record.metadata["h_correction"] = shermo_result.get('h_sum')
                    record.metadata["s_total"] = shermo_result.get('s_total')
                    record.metadata["shermo_g_sum"] = float(g_sum)
                    any_corrected = True
                else:
                    logger.warning("Shermo failed for %s", record.conformer_id)
            except Exception as exc:
                logger.warning("Shermo error for %s: %s", record.conformer_id, exc)

        if not any_corrected:
            self._seed_censo_energy_key(records, gibbs_key, [energy_key])
        return any_corrected

    def _make_orca_interface(self, method: str, basis: str):
        """Create an ORCAInterface via unified method resolution."""
        from conformer_search.core.method_resolution import resolve_qc_method
        qc = resolve_qc_method(self.config, stage="low_cost_sp",
                              explicit_overrides={"method": method, "basis": basis})
        return ORCAInterface(
            config=self.config,
            method=qc.method,
            basis=qc.basis or "def2-TZVPP",
            solvent=qc.solvent,
            solvent_model=qc.solvent_model,
        )

    def _make_gaussian_interface(self, method: str, basis: str):
        """Create a GaussianInterface via unified method resolution."""
        from conformer_search.core.method_resolution import resolve_qc_method
        qc = resolve_qc_method(self.config, stage="final_sp",
                              explicit_overrides={"method": method, "basis": basis})
        return GaussianInterface(
            config=self.config,
            method=qc.method,
            basis=qc.basis or "def2-TZVPP",
            dispersion=qc.dispersion,
            solvent=qc.solvent,
            solvent_model=qc.solvent_model,
        )

    def _run_censo_protocol(
        self,
        initial_xyz: Path,
        spec: Optional["ConformerWorkflowSpec"] = None,
    ) -> CandidateSet:
        """Run the CENSO Part0–Part3 funnel runtime."""
        logger.info("[S1] Running CENSO protocol")

        workflow_spec = spec or resolve_any_protocol(self.protocol_spec.name, config=self.config)
        recipe = workflow_spec.recipe

        self.state_manager.set_stage('censo_funnel')

        ensemble_xyz = self.run_crest(initial_xyz)
        if self.protocol_spec.enable_clustering:
            candidate_paths = self.run_isostat(ensemble_xyz)
        else:
            candidate_paths = self._step_process_ensemble(ensemble_xyz)
        records = self._build_censo_funnel_records(candidate_paths)

        if recipe.run_part0:
            records = run_part0(
                records,
                recipe.part0_window_kcal,
                work_dir=self.work_dir,
                protocol=workflow_spec.name,
            )

        if recipe.run_part1:
            got_real = self._run_dft_sp_for_records(records, KEY_LOWCOST, stage="low_cost_sp")
            if not got_real:
                self._seed_censo_energy_key(records, KEY_LOWCOST, [KEY_XTB])
            records = run_part1(
                records,
                recipe.part1_window_kcal,
                work_dir=self.work_dir,
                protocol=workflow_spec.name,
            )

        if recipe.run_part2:
            got_real = self._run_dft_opt_freq_for_records(records, KEY_R2SCAN, stage="optimization")
            if not got_real:
                self._seed_censo_energy_key(records, KEY_R2SCAN, [KEY_LOWCOST, KEY_XTB])
            records = run_part2(
                records,
                recipe.part2_window_kcal,
                work_dir=self.work_dir,
                protocol=workflow_spec.name,
            )

        if recipe.run_part3:
            got_real = self._run_dft_sp_for_records(records, KEY_FINAL_E, stage="final_sp")
            if not got_real:
                self._seed_censo_energy_key(records, KEY_FINAL_E, [KEY_R2SCAN, KEY_LOWCOST, KEY_XTB])
            if workflow_spec.thermo.backend == "shermo":
                self._apply_thermo_correction(
                    records, KEY_FINAL_E, KEY_FINAL_G,
                    temperature=workflow_spec.thermo.temperature,
                )
            else:
                self._seed_censo_energy_key(records, KEY_FINAL_G, [KEY_FINAL_E, KEY_R2SCAN, KEY_LOWCOST, KEY_XTB])
            records = run_part3(
                records,
                cutoff=recipe.boltzmann_cutoff,
                temperature=workflow_spec.thermo.temperature,
                work_dir=self.work_dir,
                protocol=workflow_spec.name,
            )

        selected_records: FunnelRecordSet
        top_n = getattr(recipe, "top_n", None)

        if recipe.select_mode == "rank1":
            selected_records, _ = records.select_rank1(
                KEY_FINAL_G,
                stage="final_selection",
            )
            records = selected_records
        elif (
            recipe.select_mode == "topN"
            and hasattr(recipe, "top_n")
            and isinstance(top_n, int)
            and top_n
        ):
            selected_records, _ = records.select_top_n(
                KEY_FINAL_G,
                top_n,
                stage="final_selection",
            )
            records = selected_records

        candidate_set = candidate_set_from_funnel_records(records)
        candidate_set.update_ranks()

        self.state_manager.complete_stage('censo_funnel', {
            'protocol': workflow_spec.name,
            'n_candidates': len(candidate_set.candidates),
            'funnel_dir': str(self.work_dir / 'funnel'),
        })

        return candidate_set

    def _run_shared_dft_handoff(self, candidate_paths: List[Path]) -> CandidateSet:
        """
        Run DFT optimization and SP on candidates.

        Args:
            candidate_paths: List of candidate XYZ files

        Returns:
            CandidateSet with DFT results
        """
        logger.info("[S1] Step 4: DFT OPT-SP handoff")
        
        self.state_manager.set_stage('dft_handoff')
        
        spec = self.protocol_spec
        
        # Log which substages are enabled
        enabled_substages = []
        if spec.enable_optimization:
            enabled_substages.append(f"opt({spec.opt_engine})")
        if spec.enable_frequency:
            enabled_substages.append(f"freq({spec.freq_engine})")
        if spec.enable_single_point:
            enabled_substages.append(f"sp({spec.final_sp_method}/{spec.final_sp_basis})")
        if spec.enable_shermo:
            enabled_substages.append("shermo")
        logger.info(f"  Substages enabled: {', '.join(enabled_substages) if enabled_substages else 'none'}")
        
        candidates = []
        
        n_to_optimize = min(len(candidate_paths), spec.ngeom_max)
        
        for i, path in enumerate(candidate_paths[:n_to_optimize]):
            logger.info(f"  Candidate {i+1}/{n_to_optimize}: {path.name}")
            
            coords, symbols = read_xyz(path)
            
            opt_dir = self.final_dft_dir / f"conf_{i:03d}"
            ensure_dir(opt_dir)
            
            # Step 1: Optimization using configured opt engine
            if spec.enable_optimization:
                opt_result = self.opt_interface.optimize(
                    coords, symbols,
                    charge=self._current_charge,
                    multiplicity=self._current_multiplicity,
                    output_dir=opt_dir,
                    output_name=f"conf_{i:03d}_opt",
                )
            else:
                opt_result = QCResult(
                    success=True,
                    coordinates=coords,
                    symbols=symbols,
                    energy=0.0
                )
            
            # Default values (used when opt, freq, or sp fails)
            opt_success = (
                opt_result.success
                and opt_result.coordinates is not None
                and opt_result.symbols is not None
            )
            sp_success = False
            freq_success = False
            opt_energy = opt_result.energy if opt_result.energy is not None else 0.0
            sp_energy = None
            gibbs_energy = None
            gibbs_correction = None
            h_correction = None
            u_correction = None
            s_total = None
            g_conc = None
            opt_log = str(opt_result.log_file) if opt_result.log_file else None
            sp_log = None
            final_coordinates = opt_result.coordinates if opt_result.coordinates is not None else coords
            final_symbols = opt_result.symbols if opt_result.symbols is not None else symbols

            if opt_success:
                opt_coordinates = final_coordinates
                opt_symbols = final_symbols

                # Step 2: Frequency using configured freq engine
                if spec.enable_frequency:
                    logger.info(f"    [freq] Running frequency calculation ({spec.freq_engine})")
                    freq_result = self.freq_interface.frequency(
                        opt_coordinates,
                        opt_symbols,
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
                    logger.info(f"    [sp] Running single-point ({spec.final_sp_method}/{spec.final_sp_basis})")
                    sp_result = self.orca_interface.single_point(
                        opt_coordinates,
                        opt_symbols,
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
                    sp_energy = opt_energy
                    sp_log = None
                    sp_result = QCResult(success=True, energy=opt_energy)

                # Step 4: Shermo thermochemistry (only if freq and sp succeeded)
                if (
                    spec.enable_shermo
                    and freq_success
                    and sp_success
                    and freq_result.log_file is not None
                    and sp_result.energy is not None
                ):
                    logger.info("    [shermo] Running thermodynamic correction")
                    shermo_result = run_shermo(
                        freq_output=freq_result.log_file,
                        sp_energy=sp_result.energy,
                        output_dir=opt_dir,
                        shermo_bin=self.shermo_bin,
                        output_file=opt_dir / f"conf_{i:03d}_Shermo.sum",
                        temperature_k=self.thermo_config.get('temperature_k', 298.15),
                        pressure_atm=self.thermo_config.get('pressure_atm', 1.0),
                        scl_zpe=self.thermo_config.get('scl_zpe', 0.9905),
                        ilowfreq=self.thermo_config.get('shermo_ilowfreq', 2),
                        imagreal=self.thermo_config.get('shermo_imagreal', 0),
                        conc=self.thermo_config.get('shermo_conc')
                    )
                    if shermo_result:
                        g_sum = shermo_result.get('g_sum')
                        g_conc = shermo_result.get('g_conc')
                        gibbs_energy = g_conc if g_conc is not None else g_sum
                        h_correction = shermo_result.get('h_sum')
                        u_correction = shermo_result.get('u_sum')
                        s_total = shermo_result.get('s_total')
                        gibbs_correction = (g_sum - sp_energy) if (g_sum is not None and sp_energy is not None) else None

            # Append candidate (opt failure → energy from opt, thermo fields None)
            candidates.append(ConformerCandidate(
                index=i,
                coordinates=final_coordinates,
                symbols=final_symbols,
                energy=sp_energy if sp_success and sp_energy is not None else opt_energy,
                gibbs_energy=gibbs_energy,
                gibbs_correction=gibbs_correction,
                h_correction=h_correction,
                u_correction=u_correction,
                s_total=s_total,
                g_conc=g_conc,
                source_file=path,
                metadata={
                    'opt_log': opt_log,
                    'sp_out': sp_log
                }
            ))
        
        candidate_set = CandidateSet(candidates=candidates)
        candidate_set.calculate_boltzmann_weights_gibbs(
            temperature_k=self.thermo_config.get('temperature_k', 298.15)
        )
        candidate_set.update_ranks()
        
        self.state_manager.complete_stage('dft_handoff', {
            'n_optimized': len(candidates),
            'reference_energy': candidate_set.candidates[0].energy if candidates else None
        })
        
        return candidate_set

    def _run_ext_protocol(self, initial_xyz: Path) -> CandidateSet:
        """Run EXT protocol (two-stage CREST + full DFT handoff)."""
        logger.info("[S1] Running EXT protocol")
        
        spec = self.protocol_spec
        
        ensemble_xyz = self.run_crest(initial_xyz)
        if spec.enable_clustering:
            candidate_paths = self.run_isostat(ensemble_xyz)
        else:
            candidate_paths = self._step_process_ensemble(ensemble_xyz)

        return self.run_dft_handoff(candidate_paths)

    def _run_zero_protocol(self, initial_xyz: Path) -> CandidateSet:
        """Run SP-only pipeline (used by reference-sp via run_zero_sp)."""
        logger.info("[S1] Running zero protocol (SP only)")

        spec = self.protocol_spec

        ensemble_xyz = self.run_crest(initial_xyz)
        if spec.enable_clustering:
            candidate_paths = self.run_isostat(ensemble_xyz)
        else:
            candidate_paths = self._step_process_ensemble(ensemble_xyz)

        logger.info("[S1] Step 4: Single-point energy (ORCA)")
        self.state_manager.set_stage('single_point')

        candidates = []
        for i, path in enumerate(candidate_paths[:spec.ngeom_default]):
            logger.info(f"  SP calculation for candidate {i+1}/{min(len(candidate_paths), spec.ngeom_default)}")
            coords, symbols = read_xyz(path)

            sp_result = self.orca_interface.single_point(
                coords, symbols,
                charge=self._current_charge,
                multiplicity=self._current_multiplicity,
                output_dir=self.final_dft_dir / f"conf_{i:03d}",
                output_name=f"conf_{i:03d}_sp"
            )

            candidates.append(ConformerCandidate(
                index=i,
                coordinates=coords,
                symbols=symbols,
                energy=sp_result.energy if sp_result.success and sp_result.energy is not None else 0.0,
                source_file=path
            ))

        self.state_manager.complete_stage('single_point', {
            'n_candidates': len(candidates),
            'energies': [c.energy for c in candidates] if candidates else [],
        })

        candidate_set = CandidateSet(candidates=candidates)
        candidate_set.calculate_boltzmann_weights()
        candidate_set.update_ranks()

        return candidate_set

    def _cleanup_temp_files(self):
        """Remove .tmp files from finalDFT directory."""
        if not self.final_dft_dir.exists():
            return
        tmp_files = list(self.final_dft_dir.glob('**/*.tmp'))
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
        
        self.state_manager.set_stage('finalization')
        
        if not candidate_set.candidates:
            raise RuntimeError("No conformers found")
        
        global_min = candidate_set.get_lowest_gibbs()
        if global_min is None:
            raise RuntimeError("No conformers found")
        global_min_energy = (
            global_min.g_conc
            if global_min.g_conc is not None
            else (global_min.gibbs_energy if global_min.gibbs_energy is not None else global_min.energy)
        )
        
        global_min_xyz = self.work_dir / f"{self.molecule_name}_global_min.xyz"
        write_xyz(
            global_min_xyz,
            global_min.coordinates,
            global_min.symbols,
            title=f"Global minimum for {self.molecule_name}",
            energy=global_min_energy,
            comment=f"Rank {global_min.rank}, Weight {global_min.weight:.4f}"
        )
        
        ensemble_xyz = self.final_dft_dir / "all_conformers.xyz"
        with open(ensemble_xyz, 'w') as f:
            for c in candidate_set.candidates:
                f.write(f"{len(c.symbols)}\n")
                f.write(f"Conformer {c.index}, E={c.energy:.6f}, Rank={c.rank}, Weight={c.weight:.4f}\n")
                for sym, coord in zip(c.symbols, c.coordinates):
                    f.write(f"{sym:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}\n")
        
        thermo_csv = self.final_dft_dir / "conformer_thermo.csv"
        with open(thermo_csv, 'w') as f:
            f.write("index,rank,energy_hartree,gibbs_correction,gibbs_hartree,h_correction,u_correction,s_total,g_conc,weight,source\n")
            for c in candidate_set.candidates:
                f.write(f"{c.index},{c.rank},{c.energy:.10f},")
                f.write(f"{c.gibbs_correction:.10f}," if c.gibbs_correction is not None else f",")
                f.write(f"{c.gibbs_energy:.10f}," if c.gibbs_energy is not None else f",")
                f.write(f"{c.h_correction:.10f}," if c.h_correction is not None else f",")
                f.write(f"{c.u_correction:.10f}," if c.u_correction is not None else f",")
                f.write(f"{c.s_total:.10f}," if c.s_total is not None else f",")
                f.write(f"{c.g_conc:.10f}," if c.g_conc is not None else f",")
                source_name = c.source_file.name if c.source_file is not None else ""
                f.write(f"{c.weight:.6f},{source_name}\n")
        
        self.state_manager.complete_stage('finalization', {
            'global_min_file': str(global_min_xyz),
            'global_min_energy': global_min_energy,
            'n_conformers': len(candidate_set.candidates),
            'ensemble_file': str(ensemble_xyz)
        })
        
        self._cleanup_temp_files()
        
        return {
            'global_min_xyz': global_min_xyz,
            'global_min_energy': global_min_energy,
            'n_conformers': len(candidate_set.candidates),
            'metadata': {
                'protocol': self.protocol_spec.name,
                'candidates': [c.to_dict() for c in candidate_set.candidates],
                'state_summary': self.state_manager.get_summary()
            }
        }
