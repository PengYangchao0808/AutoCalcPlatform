"""Evidence-report production for completed envelope attempts (C1 closure).

The A line owns the vocabulary; these tests pin the B-side use case: real
assembly over a completed attempt's native artifacts, deterministic bytes,
source verification against the attempt root, and typed refusals for every
insufficient state. Nothing here is real-QC evidence; the real-chain receipts
live in reports/acp2_dev_batch_20261029/phaseC/.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp_services.runtime.evidence_production import (
    EvidenceProductionError,
    produce_attempt_evidence_report,
)

RECEIPT = {
    "receipt_version": 1,
    "job_id": "job-evd-1",
    "attempt": 1,
    "state": "completed",
    "release_id": "rel-evd-1",
    "release_digest": "ab" * 32,
    "plan_identity": "v2:" + "0a" * 16,
    "step_execution_chain": [],
    "evidence_digests": {"exit_code": "0"},
    "data_bindings": [{"artifact_digests": {"input.xyz": "sha256:" + "0a" * 32}}],
    "errors": [],
    "written_at": "2026-10-29T00:00:00Z",
    "owner": "test:owner:1:1",
    "fencing_token": "1",
    "chain_digest": "cd" * 32,
    "result_digest": "sha256:" + "ef" * 32,
    "step_identities": ["v2:" + "0a" * 16],
    "submission_id": "sub-1",
}

TASK_RESULT = {
    "schema": "acp_task_result_v1",
    "schema_version": 1,
    "complete": True,
    "exit_code": 0,
    "receipt_state": "completed",
    "job_status": "completed",
    "result": {"energy_hartree": -76.4, "status": "completed"},
}

ENERGY_PRODUCT = {
    "schema_version": "acp_energy_product_v1",
    "energy": -76.4,
    "energy_hartree": -76.4,
    "unit": "hartree",
    "step_id": "singlepoint",
    "step_kind": "singlepoint",
}

MANIFEST = {
    "schema_version": 1,
    "products": [
        {"id": "singlepoint_energy", "kind": "energy_report", "label": "energy",
         "path": "energy/singlepoint_energy.json",
         "metadata": {"energy_hartree": -76.4, "workflow": "singlepoint"}},
    ],
}


def _task_result_document() -> dict:
    """A digest-consistent WORK/00_RUNTIME/task_result.json payload."""
    import hashlib

    body = dict(TASK_RESULT)
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    body["content_digest"] = "sha256:" + hashlib.sha256(blob).hexdigest()
    return body


def _attempt_dir(tmp_path: Path) -> Path:
    work = tmp_path / "job-evd-1"
    (work / "RESULT" / "energy").mkdir(parents=True)
    (work / "WORK" / "00_RUNTIME").mkdir(parents=True)
    (work / "execution_receipt.json").write_text(json.dumps(RECEIPT), encoding="utf-8")
    (work / "WORK" / "00_RUNTIME" / "task_result.json").write_text(
        json.dumps(_task_result_document()), encoding="utf-8"
    )
    (work / "RESULT" / "result_manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (work / "RESULT" / "energy" / "singlepoint_energy.json").write_text(
        json.dumps(ENERGY_PRODUCT), encoding="utf-8"
    )
    return work


def test_production_assembles_a_supported_claim_from_native_artifacts(tmp_path: Path) -> None:
    work = _attempt_dir(tmp_path)
    produced = produce_attempt_evidence_report(work_dir=work, job_id="job-evd-1", attempt=1)
    assert produced["claim_statuses"] == {"total-electronic-energy": "supported"}
    document = produced["report"]
    assert document["schema_version"] == 1
    assert document["execution"]["terminal_state"] == "completed"
    claim = document["claims"][0]
    assert claim["status"] == "supported"
    assert claim["validator"]["kind"] == "software"
    assert claim["source_refs"][0]["units"] == "hartree"
    assert document["subject"]["science_identity"] == "v2:" + "0a" * 16
    assert "-76.4" in claim["statement"]


def test_production_is_deterministic_for_one_attempt_state(tmp_path: Path) -> None:
    work = _attempt_dir(tmp_path)
    first = produce_attempt_evidence_report(work_dir=work, job_id="job-evd-1", attempt=1)
    second = produce_attempt_evidence_report(work_dir=work, job_id="job-evd-1", attempt=1)
    assert first["sha256"] == second["sha256"]
    assert (work / "RESULT/evidence_report.json").read_text(encoding="utf-8").endswith("\n")


def test_production_refuses_an_attempt_that_did_not_complete(tmp_path: Path) -> None:
    work = _attempt_dir(tmp_path)
    receipt = dict(RECEIPT, state="failed")
    (work / "execution_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(EvidenceProductionError, match="completed attempts only"):
        produce_attempt_evidence_report(work_dir=work, job_id="job-evd-1", attempt=1)


def test_production_refuses_a_manifest_without_an_energy_product(tmp_path: Path) -> None:
    work = _attempt_dir(tmp_path)
    (work / "RESULT" / "result_manifest.json").write_text(
        json.dumps({"schema_version": 1, "products": []}), encoding="utf-8"
    )
    with pytest.raises(EvidenceProductionError) as excinfo:
        produce_attempt_evidence_report(work_dir=work, job_id="job-evd-1", attempt=1)
    assert excinfo.value.code == "energy_product_missing"


def test_production_refuses_an_attempt_without_a_receipt(tmp_path: Path) -> None:
    work = _attempt_dir(tmp_path)
    (work / "execution_receipt.json").unlink()
    with pytest.raises(EvidenceProductionError) as excinfo:
        produce_attempt_evidence_report(work_dir=work, job_id="job-evd-1", attempt=1)
    assert excinfo.value.code == "attempt_not_ready"


def test_the_report_round_trips_the_platform_validator(tmp_path: Path) -> None:
    """The written document must satisfy the B-side reader (register/read path)."""
    from acp_services.runtime.science_binding import validate_evidence_report

    work = _attempt_dir(tmp_path)
    produce_attempt_evidence_report(work_dir=work, job_id="job-evd-1", attempt=1)
    document = json.loads((work / "RESULT/evidence_report.json").read_text(encoding="utf-8"))
    validate_evidence_report(document)
