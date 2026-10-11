"""Isolated audit probes: temporary files, no real QC processes or SSH.

Run from the repository root with Python 3.11 and existing project dependencies.
The captured results describe defects; this is evidence, not a green test suite.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

from acp.confsearch.shared.boltzmann import boltzmann_weights
from acp.intake.parsers import parse_gjf_text, parse_orca_inp_text
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.remote.script_gen import build_remote_cli_command
from acp.scheduler.runner import JobRunner
from acp.scheduler.store import JobStore
from acp.scheduler.tasks import TaskIndex
from cccp.calculation._common import load_geometry
from cccp.calculation.requests import ScanCoordinateSpec, ScanOptions
from cccp.calculation.tasks.scan import build_scan_plan, validate_atom_indices
from cccp.core.candidates import CandidateSet, ConformerCandidate
from cccp.io.input_handler import MolecularInputHandler
from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan
from cccp.utils.constants import HARTREE_TO_KCAL
from cccp.utils.file_io import read_gjf, read_xyz_multiframe, write_xyz


def main() -> None:
    evidence = {}
    with tempfile.TemporaryDirectory(prefix="acp-code-audit-") as temporary:
        root = Path(temporary)

        module = {
            "execution_mode": "single_state",
            "states": [
                {
                    "state_id": "triplet",
                    "target_multiplicity": 3,
                    "spin_mode": "unrestricted",
                    "guess": {"strategy": "default"},
                }
            ],
        }
        spec = JobSpec(
            workflow="singlepoint",
            input={"source": "molecule.xyz", "charge": 0, "multiplicity": 3},
            method={
                "levels": {
                    "single_point": {
                        "functional": "PBE0",
                        "basis": "def2-SVP",
                        "electronic_state": module,
                    }
                }
            },
            resources={"nproc": 1, "mem": "1GB"},
        )
        local_argv = JobRunner()._build_cmd(spec, root / "task", input_path="molecule.xyz")
        remote_argv = build_remote_cli_command(spec, input_path="molecule.xyz")
        evidence["remote_electronic_state_loss"] = {
            "local_argv": local_argv,
            "remote_argv": remote_argv,
            "local_has_spin_config": "--spin-config" in local_argv,
            "remote_has_spin_config": "--spin-config" in remote_argv,
            "local_sidecar_exists": (root / "task" / "electronic_state.json").is_file(),
        }
        casscf = JobSpec(
            workflow="casscf",
            input={"source": "molecule.xyz"},
            method={"levels": {"casscf": {"cas_active_electrons": 2, "cas_active_orbitals": 2}}},
        )
        try:
            casscf_remote = build_remote_cli_command(casscf)
        except ValueError as error:
            casscf_remote = str(error)
        evidence["remote_casscf_builder_gap"] = {"result": casscf_remote}

        broken = root / "truncated.xyz"
        broken.write_text(
            "3\nthree declared, only two present\nH 0 0 0\nH 0 0 0.74\n", encoding="utf-8"
        )
        coords, symbols = load_geometry(path=broken)
        evidence["truncated_xyz_accepted_by_task_loader"] = {
            "declared_atoms": 3,
            "loaded_atoms": len(symbols),
            "shape": list(coords.shape),
        }

        not_finite = root / "nan.xyz"
        not_finite.write_text("2\nnonfinite\nH nan 0 0\nH 0 0 0.74\n", encoding="utf-8")
        coords, _symbols = load_geometry(path=not_finite)
        evidence["nonfinite_geometry_accepted_by_task_loader"] = {
            "finite": bool(np.isfinite(coords).all())
        }

        output = root / "mismatched.xyz"
        write_xyz(output, np.zeros((1, 3)), ["H", "H"])
        evidence["xyz_writer_silently_truncates"] = {
            "declared_atoms": int(output.read_text().splitlines()[0]),
            "written_atom_rows": len(output.read_text().splitlines()) - 2,
        }

        ensemble = root / "changed_elements.xyz"
        ensemble.write_text(
            "2\nframe one\nC 0 0 0\nH 1 0 0\n2\nframe two\nN 0 0 0\nH 1 0 0\n", encoding="utf-8"
        )
        coords, symbols = read_xyz_multiframe(ensemble)
        evidence["multiframe_element_identity_loss"] = {
            "actual_frame_symbols": [["C", "H"], ["N", "H"]],
            "returned_symbols_for_all_frames": symbols,
            "shape": list(coords.shape),
        }

        gjf = root / "charged.gjf"
        gjf_text = "#p HF/STO-3G\n\ncharged\n\n1 2\nHe 0 0 0\n\n"
        gjf.write_text(gjf_text, encoding="utf-8")
        _coords, _symbols, charge, multiplicity = read_gjf(gjf)
        handler = MolecularInputHandler.parse_gjf(gjf)
        intake = parse_gjf_text(gjf_text)
        evidence["standard_gjf_charge_multiplicity_loss"] = {
            "input": [1, 2],
            "cccp_reader": [charge, multiplicity],
            "cccp_handler": [handler.charge, handler.multiplicity],
            "acp_intake": [intake.structures[0].charge, intake.structures[0].multiplicity],
        }

        orca_intake = parse_orca_inp_text("! HF STO-3G\n* xyz 1 2\nHe 0 0 0\n*\n")
        evidence["standard_orca_intake_charge_multiplicity_loss"] = {
            "input": [1, 2],
            "acp_intake": [
                orca_intake.structures[0].charge,
                orca_intake.structures[0].multiplicity,
            ],
        }

        one_kcal_gap = 1.0 / HARTREE_TO_KCAL
        candidates = [
            ConformerCandidate(index=i, coordinates=np.zeros((1, 3)), symbols=["He"], energy=energy)
            for i, energy in enumerate((0.0, one_kcal_gap))
        ]
        candidate_set = CandidateSet(candidates)
        candidate_set.calculate_boltzmann_weights()
        evidence["legacy_candidate_energy_unit_mismatch"] = {
            "gap_hartree": one_kcal_gap,
            "gap_kcal_mol": 1.0,
            "legacy_weights": [c.weight for c in candidates],
            "hartree_aware_weights": boltzmann_weights([0.0, one_kcal_gap]),
            "selected_in_half_kcal_window": len(candidate_set.select_by_window(0.5)),
        }

        negative_plan = build_scan_plan(
            ScanOptions(
                coordinates=(
                    ScanCoordinateSpec(atoms=(0, 1), start=1.0, end=2.0, atom_index_base=1),
                ),
                points=3,
            )
        )
        validate_atom_indices(negative_plan, 2)
        evidence["negative_scan_index_passes_task_validation"] = {
            "input_atoms_1_based": [0, 1],
            "backend_atoms_0_based": list(negative_plan.coordinates[0].atoms),
            "validator": "accepted",
        }

        coord = CoordinateSpec(id="bond", kind="distance", atoms=(0, 1), start=1.0, end=3.0)
        nonuniform = ReactionCoordinatePlan(
            coordinates=(coord,), points=3, lambda_values=(0.0, 0.2, 1.0)
        )
        evidence["nonuniform_lambda_targets_use_uniform_progress"] = {
            "lambda_values": list(nonuniform.lambda_values),
            "expected_middle_target": 1.0 + 0.2 * 2.0,
            "actual_middle_target": nonuniform.coordinate_targets(1)["bond"],
            "actual_middle_constraint": nonuniform.frame_constraints(1)[0].target,
        }
        copied = build_scan_plan(ScanOptions(points=4), raw_plan=nonuniform.to_dict())
        evidence["plan_point_override_drops_full_fidelity_fields"] = {
            "original_lambda_values": list(nonuniform.lambda_values),
            "copy_lambda_values": list(copied.lambda_values),
            "copy_points": copied.points,
        }

        for mode in ("zero", "none"):
            try:
                values = boltzmann_weights([0.0, one_kcal_gap], -298.15, missing=mode)
                evidence[f"negative_temperature_accepted_{mode}"] = {"weights": values}
            except (ValueError, ZeroDivisionError, OverflowError) as error:
                evidence[f"negative_temperature_accepted_{mode}"] = {"error": str(error)}

        db = JobStore(root / "audit.sqlite")
        job = JobRecord(id="audit", spec=JobSpec(workflow="fake"), work_dir=str(root / "db_task"))
        db.create(job)
        stale = db.get("audit")
        db.transition(
            "audit",
            expected_revision=0,
            expected_status=JobStatus.QUEUED,
            expected_attempt=1,
            status=JobStatus.COMPLETED,
        )
        db.update(stale)
        observed = db.get("audit")
        evidence["legacy_store_update_revives_terminal"] = {
            "terminal_before_legacy_update": "completed",
            "status_after": observed.status.value,
            "revision_after": observed.revision,
        }
        index = TaskIndex(db.db_path)
        index.sync_from_job(observed)
        db.delete("audit")
        evidence["legacy_store_delete_leaves_task_row"] = {
            "job_exists": db.get("audit") is not None,
            "task_exists": index.get("audit") is not None,
        }

        with patch.dict(os.environ, {"ACP_RUN_ROOT": str(root / "server_import")}):
            from acp.api.server import _load_remote_config

            with patch(
                "cccp.config.load_config", return_value={"cluster": {"execution_mode": "remot"}}
            ):
                configuration = _load_remote_config()
        evidence["invalid_remote_config_becomes_local"] = {
            "input_mode": "remot",
            "resolved_mode": configuration.execution_mode,
        }
        evidence["server_import_creates_run_root"] = {"exists": (root / "server_import").is_dir()}

        # Demonstrate the exact shell/list semantics used by the dormant LSF
        # adapter without executing bsub or reaching a real cluster.
        shell_result = subprocess.run(
            ["read value; printf 'stdin=%s' \"$value\"", "<", "submit.lsf"],
            shell=True,
            input="",
            text=True,
            capture_output=True,
            check=True,
        )
        evidence["legacy_lsf_shell_list_does_not_redirect"] = {
            "shell_argv_shape": ["command", "<", "submit.lsf"],
            "stdout": shell_result.stdout,
            "meaning": (
                "the redirection tokens are positional shell arguments, not stdin redirection"
            ),
        }

    output = Path(__file__).resolve().parent / "evidence" / "reproductions.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
