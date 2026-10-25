"""Build the constructed A-25b selectivity report scenario.

Every artifact here is a **constructed** placeholder: no QC job, TS search, IRC,
energy or experimental measurement ran.  The path evidence reports are assembled
with the frozen EvidenceReport contract so the A-25b consumer can be tested
against the same digests/claims it would receive from B23b.

Run from the repository root with the A interpreter::

    "$ACP2_A_PYTHON" \
        tests/fixtures/acp2/science/selectivity/v1/report-scenario/build_report_fixture.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cccp.analysis.research.evidence import (
    ArtifactSourceRef,
    ClaimDraft,
    ClaimScope,
    Digest,
    EvidenceValidator,
    ExecutionFacts,
    Subject,
    assemble_evidence_report,
)
from cccp.analysis.research.validators import (
    ACTIVATION_ENERGY_COMPARABILITY_VALIDATOR_ID,
    OPTIMIZATION_CONVERGENCE_VALIDATOR_ID,
    TS_CONNECTIVITY_VALIDATOR_ID,
    TS_SADDLE_CANDIDATE_VALIDATOR_ID,
    VALIDATOR_VERSION,
)

ROOT = Path(__file__).resolve().parent
FIXTURE_DIR = ROOT.parent
MANIFEST = json.loads((FIXTURE_DIR / "fixture-manifest.json").read_text(encoding="utf-8"))
ARTIFACTS = ROOT / "artifacts"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _manifest_path(path_id: str) -> dict:
    for item in MANIFEST["paths"]:
        if item["path_id"] == path_id:
            return item
    raise KeyError(path_id)


def _comparison_conditions() -> dict:
    comparison = MANIFEST["comparison"]
    return {
        "method": comparison["method"],
        "basis": comparison["basis"],
        "charge": comparison["charge"],
        "multiplicity": comparison["multiplicity"],
        "temperature_k": comparison["temperature_k"],
        "pressure_pa": comparison["pressure_pa"],
        "standard_state": comparison["standard_state"],
        "reference_state": comparison["reference_state"],
        "energy_unit": comparison["comparison_energy_unit"],
    }


def _build_path_evidence(path_id: str) -> Path:
    evidence_dir = ARTIFACTS / path_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    log = evidence_dir / "evidence.log"
    log.write_text(f"constructed placeholder for {path_id}; no QC process ran\n", encoding="utf-8")
    source = ArtifactSourceRef(
        artifact_path=f"artifacts/{path_id}/evidence.log",
        digest=Digest.from_bytes(log.read_bytes()),
        role="path_evidence",
        units=None,
    )
    scope = ClaimScope(
        subject=f"{path_id}-evidence",
        applicability="system_specific",
        conditions="constructed A-25a frozen conditions",
    )
    claims = (
        (
            "c-optimization-converged",
            "The constructed path records a converged stationary-point optimization.",
            OPTIMIZATION_CONVERGENCE_VALIDATOR_ID,
        ),
        (
            "c-ts-first-order-saddle-candidate",
            "The constructed path records exactly one mapped imaginary mode.",
            TS_SADDLE_CANDIDATE_VALIDATOR_ID,
        ),
        (
            "c-ts-connects-intended-endpoints",
            "The constructed path records bidirectional IRC endpoint connectivity.",
            TS_CONNECTIVITY_VALIDATOR_ID,
        ),
        (
            "c-electronic-activation-barrier-comparable",
            "The constructed path records a comparable electronic activation barrier.",
            ACTIVATION_ENERGY_COMPARABILITY_VALIDATOR_ID,
        ),
    )
    drafts = tuple(
        ClaimDraft(
            claim_id=claim_id,
            statement=statement,
            status="supported",
            scope=scope,
            source_refs=(source,),
            validator=EvidenceValidator(validator_id, VALIDATOR_VERSION, "deterministic"),
        )
        for claim_id, statement, validator_id in claims
    )
    report = assemble_evidence_report(
        report_id=f"{path_id}-constructed-evidence",
        subject=Subject(
            subject_id=path_id,
            subject_kind="calculation_result",
            description=f"Constructed B23b path evidence for {path_id}",
            input_digests=(source.digest,),
        ),
        execution=ExecutionFacts(
            terminal_state="completed",
            argv=("constructed-b23b-path-evidence",),
            exit_code=0,
        ),
        drafts=drafts,
    )
    report_path = ROOT / f"{path_id.replace('.', '-')}-evidence.json"
    _write_json(report_path, report.to_dict())
    return report_path


def _path_record(path_id: str, report_path: Path, activation_kcal: float) -> dict:
    manifest_path = _manifest_path(path_id)
    signature = manifest_path["signature"]
    return {
        "path_id": path_id,
        "report_path": report_path.name,
        "report_digest": _digest(report_path),
        "endpoint_signature": {
            "stereocenter_atom_id": signature["stereocenter_atom_id"],
            "cip_configuration": signature["cip_configuration"],
            "signed_volume_sign": signature["signed_volume_sign"],
            "face": signature["face"],
        },
        "mapping_topology_digest": manifest_path["mapping_topology_digest"],
        "product_digest": manifest_path["endpoint_digest"],
        "comparison_conditions": _comparison_conditions(),
        "activation_energy_kcal_per_mol": activation_kcal,
        "free_energy_kcal_per_mol": None,
    }


def main() -> None:
    for stale in (
        "path-endo-evidence.json",
        "path-exo-evidence.json",
        "b23b-export-qualified.json",
        "b23b-export-one-path.json",
        "b23b-export-absent.json",
        "cross-domain-budget.json",
        "cross-domain-budget-mismatch.json",
        "reference-records.json",
        "reference-records-duplicate-key.json",
        "fixture-manifest.json",
    ):
        path = ROOT / stale
        if path.exists():
            path.unlink()
    endo_report = _build_path_evidence("path.endo")
    exo_report = _build_path_evidence("path.exo")
    endo_record = _path_record("path.endo", endo_report, 12.0)
    exo_record = _path_record("path.exo", exo_report, 15.0)
    run_id = "constructed-run-0001"
    authorized = [
        {
            "path_id": "path.endo",
            "report_path": endo_report.name,
            "digest": _digest(endo_report),
            "role": "path_evidence",
            "reused": False,
        },
        {
            "path_id": "path.exo",
            "report_path": exo_report.name,
            "digest": _digest(exo_report),
            "role": "path_evidence",
            "reused": False,
        },
    ]
    b23b_qualified = {
        "artifact_id": "constructed-b23b-selectivity-artifacts",
        "constructed": True,
        "readiness": "integration_ready",
        "run_id": run_id,
        "real_qc_binary_ran": False,
        "paths": ["path.endo", "path.exo"],
        "authorized_artifacts": authorized,
        "record_path": "b23b-export-qualified.json",
        "path_records": [endo_record, exo_record],
    }
    _write_json(ROOT / "b23b-export-qualified.json", b23b_qualified)

    b23b_one_path = {
        **b23b_qualified,
        "artifact_id": "constructed-b23b-selectivity-artifacts-one-path",
        "authorized_artifacts": authorized[:1],
        "record_path": "b23b-export-one-path.json",
        "path_records": [endo_record],
    }
    _write_json(ROOT / "b23b-export-one-path.json", b23b_one_path)

    b23b_absent = {
        "artifact_id": "constructed-b23b-selectivity-artifacts-absent",
        "readiness": "contract_ready",
        "run_id": run_id,
        "real_qc_binary_ran": False,
        "paths": [],
        "authorized_artifacts": [],
        "record_path": "b23b-export-absent.json",
        "path_records": [],
    }
    _write_json(ROOT / "b23b-export-absent.json", b23b_absent)

    budget = {
        "run_id": run_id,
        "subrows": [
            {
                "domain": "nmr",
                "run_id": run_id,
                "task_ref": "task.nmr.reused",
                "task_ref_count": 0,
                "step_count": 0,
                "qc_count": 0,
                "reused": True,
                "reused_artifacts": ["A-24a:nmr-comparison:constructed"],
            },
            {
                "domain": "thermo",
                "run_id": run_id,
                "task_ref": "task.thermo.reused",
                "task_ref_count": 0,
                "step_count": 0,
                "qc_count": 0,
                "reused": True,
                "reused_artifacts": ["A-24b:ensemble-thermo:constructed"],
            },
            {
                "domain": "selectivity",
                "run_id": run_id,
                "task_ref": "task.selectivity.constructed",
                "task_ref_count": 1,
                "step_count": 8,
                "qc_count": 14,
                "reused": False,
                "reused_artifacts": [],
            },
        ],
        "cumulative": {
            "task_refs": 1,
            "steps": 8,
            "qc_calls": 14,
            "wall_seconds": None,
        },
        "previous_run_ids": [],
        "previous_cumulative": None,
        "record_path": "cross-domain-budget.json",
    }
    _write_json(ROOT / "cross-domain-budget.json", budget)
    mismatch = {**budget, "record_path": "cross-domain-budget-mismatch.json"}
    mismatch["cumulative"] = {**budget["cumulative"], "task_refs": 2}
    _write_json(ROOT / "cross-domain-budget-mismatch.json", mismatch)

    reference = {
        "owner": "C",
        "scenario": "experience:cross-domain-live",
        "subcase": "selectivity",
        "phase": "experience:selectivity",
        "run_id": run_id,
    }
    _write_json(ROOT / "reference-records.json", [reference])
    _write_json(
        ROOT / "reference-records-duplicate-key.json",
        [
            reference,
            {
                **reference,
                "subcase": "nmr",
            },
        ],
    )

    files = sorted(
        path.name
        for path in ROOT.iterdir()
        if path.is_file()
        and path.name != "fixture-manifest.json"
        and path.name != "build_report_fixture.py"
    )
    manifest = {
        "schema": "acp2.science.fixture-manifest",
        "schema_version": 1,
        "constructed": True,
        "real_run": False,
        "description": (
            "Constructed A-25b selectivity report scenario: two path evidence reports, "
            "B23b export records, cross-domain budget tables and reference records. No "
            "QC job, TS search, IRC, energy or experimental measurement ran."
        ),
        "file_digests": {name: _digest(ROOT / name) for name in files},
    }
    _write_json(ROOT / "fixture-manifest.json", manifest)
    print("wrote", len(files) + 1, "files to", ROOT)


if __name__ == "__main__":
    main()
