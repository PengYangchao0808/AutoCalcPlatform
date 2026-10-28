"""M1-03 (G07/G08) — evidence projection, TaskRef and ResearchDecision.

Covers the single platform projection
(``acp_services.runtime.evidence_projection``), its contract samples and the
experience-client copy of the same vectors.

Named outcomes exercised here:

* ``task_ref_missing_attempt`` / ``task_ref_missing_release`` — a TaskRef is
  never defaulted to attempt 1 / an empty release;
* ``foreign_project`` / ``foreign_research`` / ``foreign_round`` /
  ``foreign_job`` / ``foreign_attempt`` / ``foreign_release`` /
  ``foreign_release_digest`` / ``foreign_task_ref`` — attribution is explicit;
* ``task_result_not_digest_bound`` / ``receipt_not_digest_bound`` /
  ``report_not_digest_bound`` / ``evidence_digest_mismatch`` — only
  digest-bound facts are consumed;
* ``invalid_science_report`` — model text is never a validator or evidence;
* the frozen ``{complete, partial, incomplete, failed}`` mapping for worker
  qualification, step outcome and publication status;
* the decision policy: ``accept`` needs completed + complete + no blocking
  missing check + supported claims, while ``revise``/``reject``/``defer``/
  ``escalate`` stay available;
* one parse path: the projection composes the frozen B18 registration check and
  A's public schema parser, and nothing else on the platform parses evidence.

Nothing here executes science, a subprocess, a model or a cluster: every
fixture is STATIC/SIMULATED and the digests are computed over fixture bytes.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from acp_services.contracts.envelope import RECEIPT_STATES
from acp_services.runtime import evidence_projection as projection
from acp_services.runtime import science_binding, worker
from acp_services.runtime.evidence_projection import (
    ACCEPT_PRECONDITION_CODES,
    DECISION_RATIONALE_CODES,
    EVIDENCE_STATUSES,
    PUBLISH_STATUS_TO_EVIDENCE_STATUS,
    QUALIFICATION_TO_EVIDENCE_STATUS,
    RECEIPT_STATE_TO_EVIDENCE_STATUS,
    RESEARCH_DECISION_SCHEMA,
    STEP_STATUS_TO_EVIDENCE_STATUS,
    EvidenceProjectionError,
    EvidenceView,
    ResearchDecisionError,
    TaskRef,
    TaskRefError,
    combine_evidence_status,
    decide_evidence,
    decision_from_dict,
    evidence_status_for_qualification,
    evidence_status_for_step,
    parse_evidence_report,
    parse_task_ref,
    project_evidence,
    validate_research_decision,
    verify_attribution,
)
from acp_services.runtime.research_session import DECISION_VALUES, TaskBinding
from cccp.analysis.research import evidence as research_evidence

REPO = Path(__file__).resolve().parents[1]
SCIENCE_FIXTURES = REPO / "tests" / "fixtures" / "acp2" / "science" / "evidence_projection"
SAMPLES = (
    REPO
    / "docs"
    / "reference"
    / "next-change"
    / "acp2-contracts"
    / "runtime"
    / "samples"
    / "evidence-projection"
)
RPH_FIXTURES = REPO / "rph" / "fixtures" / "evidence-projection"

PROJECT = "proj-m1-03"
RESEARCH = "res-m1-03"
SESSION = "ses-m1-03"
RELEASE_ID = "release-m1-03"
RELEASE_DIGEST = "sha256:" + "c" * 64
RESULT_DIGEST = "sha256:" + "d" * 64


# ---------------------------------------------------------------------------
# Static bundle helpers (no execution of any kind)
# ---------------------------------------------------------------------------


def _identity(project: str = PROJECT, research: str = RESEARCH, session: str = SESSION) -> Any:
    from acp_services.runtime.research_session import ResearchIdentity

    return ResearchIdentity(
        principal_id="principal-alice",
        project_id=project,
        research_id=research,
        session_id=session,
    )


def _task_ref_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "project_id": PROJECT,
        "task_id": "job-m1-03-static",
        "attempt": 1,
        "release": {"release_id": RELEASE_ID, "release_digest": RELEASE_DIGEST},
    }
    payload.update(overrides)
    return payload


def _task_ref(**overrides: Any) -> TaskRef:
    return parse_task_ref(_task_ref_payload(**overrides))


def _task_result_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema": worker.TASK_RESULT_SCHEMA,
        "task": "frequency",
        "status": "completed",
        "complete": True,
        "truth_table_row": "a",
        "qualification": worker.QUALIFICATION_COMPLETE,
        "partial_result": False,
        "normal_reuse": True,
        "publish": True,
        "publish_policy": "normal",
        "receipt_state": "completed",
        "job_status": "completed",
        "exit_code": 0,
        "required_facts": ["frequency_payload"],
        "missing_required_facts": [],
        "authority": "cccp.calculation.tasks.frequency",
        "result_digest": RESULT_DIGEST,
        "written_at": "2026-10-27T00:00:00Z",
    }
    body.update(overrides)
    return body


def _task_result(**overrides: Any) -> dict[str, Any]:
    body = _task_result_body(**overrides)
    return {**body, "content_digest": worker.task_result_content_digest(body)}


def _receipt(task_result: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "receipt_version": 1,
        "job_id": "job-m1-03-static",
        "attempt": 1,
        "submission_id": "sub-m1-03-static",
        "state": "completed",
        "result_digest": RESULT_DIGEST,
        "written_at": "2026-10-27T00:00:01Z",
        "release_id": RELEASE_ID,
        "release_digest": RELEASE_DIGEST,
        "evidence_digests": {"task_result_digest": task_result.get("content_digest", "")},
    }
    receipt.update(overrides)
    return receipt


def _report_bytes(name: str = "report.supported.positive.json") -> bytes:
    return (SCIENCE_FIXTURES / name).read_bytes()


def _read_root(tmp_path: Path) -> Path:
    # One controlled read root per test; a second projection in the same test
    # reuses it (the fixture set is read-only).
    root = tmp_path / "root"
    if not root.exists():
        shutil.copytree(SCIENCE_FIXTURES, root)
    return root


def _load_wire(name: str) -> dict[str, Any]:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def _project(
    tmp_path: Path,
    *,
    identity: Any | None = None,
    round_index: int = 0,
    task_ref: TaskRef | None = None,
    task_result: dict[str, Any] | None = None,
    receipt: dict[str, Any] | None = None,
    report_name: str = "report.supported.positive.json",
    report: bytes | None = None,
    report_digest: str | None = None,
    step_outcomes: tuple[dict[str, str], ...] = (),
    publication_status: str = "",
) -> EvidenceView:
    result = task_result if task_result is not None else _task_result()
    payload = report if report is not None else _report_bytes(report_name)
    digest = report_digest
    if digest is None:
        digest = "sha256:" + __import__("hashlib").sha256(payload).hexdigest()
    return project_evidence(
        identity=identity if identity is not None else _identity(),
        round_index=round_index,
        task_ref=task_ref if task_ref is not None else _task_ref(),
        receipt=receipt if receipt is not None else _receipt(result),
        task_result=result,
        report=payload,
        report_digest=digest,
        read_root=_read_root(tmp_path),
        step_outcomes=step_outcomes,
        publication_status=publication_status,
    )


# ---------------------------------------------------------------------------
# Contract samples: one vector set, both sides
# ---------------------------------------------------------------------------


def test_contract_samples_replay_on_the_platform_projection() -> None:
    task_ref_samples = sorted(SAMPLES.glob("task-ref*.json"))
    assert task_ref_samples, "the TaskRef vectors are registered"
    for sample_path in task_ref_samples:
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        expected = sample["expected"]
        if expected["valid"]:
            ref = parse_task_ref(sample["payload"])
            assert ref.attempt == 1
            assert ref.job_id == ref.task_id
            assert ref.release_digest == RELEASE_DIGEST
        else:
            with pytest.raises(TaskRefError) as excinfo:
                parse_task_ref(sample["payload"])
            assert excinfo.value.code == expected["code"], sample_path.name

    view = EvidenceView.from_dict(_load_wire("evidence-view.positive.json")["payload"])
    assert view.evidence_status == "complete"
    assert view.overall_claim_status == "supported"
    assert view.evidence_refs

    for name in (
        "evidence-view-foreign-research.negative.json",
        "evidence-view-foreign-round.negative.json",
    ):
        sample = _load_wire(name)
        foreign = EvidenceView.from_dict(sample["payload"])
        with pytest.raises(EvidenceProjectionError) as excinfo:
            verify_attribution(foreign, _identity(), 0)
        assert excinfo.value.code == sample["expected"]["code"], name

    attribution = _load_wire("platform-attribution.positive.json")
    assert attribution["payload"]["task_ref"]["project_id"] == PROJECT
    foreign_attribution = _load_wire("platform-attribution-foreign-project.negative.json")
    assert parse_task_ref(foreign_attribution["payload"]["task_ref"]).project_id == "proj-other"


def test_research_decision_samples_replay_on_both_sides() -> None:
    samples = sorted(SAMPLES.glob("research-decision-*.json"))
    assert len(samples) >= 8
    for sample_path in samples:
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        payload = sample["payload"]
        expected = sample["expected"]
        view = EvidenceView.from_dict(payload["evidence_view"])
        if "decision_record" in payload:
            if expected["valid"]:
                record = decision_from_dict(payload["decision_record"], view=view)
                assert record.projection_digest == view.projection_digest
                assert record.to_dict()["schema"] == RESEARCH_DECISION_SCHEMA
            else:
                with pytest.raises(ResearchDecisionError) as excinfo:
                    decision_from_dict(payload["decision_record"], view=view)
                assert excinfo.value.code == expected["code"], sample_path.name
            continue
        if expected["valid"]:
            record = decide_evidence(view, payload["decision"])
            assert record.decision == payload["decision"]
            assert set(record.rationale_codes) <= set(DECISION_RATIONALE_CODES)
        else:
            with pytest.raises(ResearchDecisionError) as excinfo:
                decide_evidence(view, payload["decision"])
            assert excinfo.value.code == expected["code"], sample_path.name


def test_rph_fixture_copies_match_the_contract_samples() -> None:
    provenance = json.loads((RPH_FIXTURES / "provenance.json").read_text(encoding="utf-8"))
    copied = set(provenance["files"])
    on_disk = {path.name for path in RPH_FIXTURES.glob("*.json")} - {"provenance.json"}
    assert copied == on_disk, "the RPH copy list and the files on disk agree"
    for name, record in provenance["files"].items():
        source = REPO / record["source"]
        assert source.is_file(), f"{name} has a source sample"
        assert source.read_bytes() == (RPH_FIXTURES / name).read_bytes(), (
            f"{name} drifted from {record['source']}"
        )
        assert record["sha256"] == __import__("hashlib").sha256(source.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Projection: statuses preserved, nothing upgraded
# ---------------------------------------------------------------------------


def test_projection_preserves_claim_statuses_and_separates_execution(
    tmp_path: Path,
) -> None:
    view = _project(tmp_path)
    assert view.report_id == "report-m1-03-supported"
    assert view.execution_terminal_state == "completed"
    assert view.qualification == worker.QUALIFICATION_COMPLETE
    assert view.evidence_status == "complete"
    assert view.overall_claim_status == "supported"
    assert [claim.status for claim in view.claims] == ["supported"]
    assert view.blocking_missing_checks == ()
    assert view.limitations == ("STATIC fixture only; no real QC execution.",)
    assert view.provenance_digest.startswith("sha256:")
    assert view.normal_reuse is True
    assert view.evidence_refs and view.evidence_refs[0].sha256.startswith("sha256:")

    wire = view.to_dict()
    assert "claim_supported" not in json.dumps(wire)
    assert wire["schema"] == projection.EVIDENCE_PROJECTION_SCHEMA
    assert wire["evidence_status"] in EVIDENCE_STATUSES
    assert wire["report"]["digest"] == view.report_digest
    # The projection is addressable: rebuilding it re-verifies the digest.
    assert EvidenceView.from_dict(wire).to_dict() == wire


def test_completed_execution_with_inconclusive_claims_is_not_acceptable(
    tmp_path: Path,
) -> None:
    view = _project(tmp_path, report_name="report.completed-inconclusive.positive.json")
    assert view.execution_terminal_state == "completed"
    assert view.evidence_status == "complete"
    assert view.overall_claim_status == "inconclusive"
    assert view.claims[1].status == "inconclusive"
    with pytest.raises(ResearchDecisionError) as excinfo:
        decide_evidence(view, "accept")
    assert excinfo.value.code == "claim_status_not_supported"
    assert decide_evidence(view, "revise").decision == "revise"
    assert decide_evidence(view, "defer").decision == "defer"


def test_blocking_missing_check_is_explicit_and_refuses_accept(
    tmp_path: Path,
) -> None:
    report = json.loads(_report_bytes("report.completed-inconclusive.positive.json"))
    report["claims"][1]["missing_checks"][0]["blocking"] = True
    report_bytes = json.dumps(report).encode("utf-8")
    view = _project(tmp_path, report=report_bytes)
    assert view.blocking_missing_checks == ("c-ts-connects-product/missing_irc",)
    with pytest.raises(ResearchDecisionError) as excinfo:
        decide_evidence(view, "accept")
    assert excinfo.value.code == "blocking_missing_check"


# ---------------------------------------------------------------------------
# Frozen status mapping
# ---------------------------------------------------------------------------


def test_worker_qualification_maps_to_the_frozen_four() -> None:
    assert QUALIFICATION_TO_EVIDENCE_STATUS == {
        "complete": "complete",
        "partial_result": "partial",
        "incomplete_result": "incomplete",
        "task_failed": "failed",
    }
    for token, expected in QUALIFICATION_TO_EVIDENCE_STATUS.items():
        assert evidence_status_for_qualification(token) == expected
    with pytest.raises(EvidenceProjectionError) as excinfo:
        evidence_status_for_qualification("accepted")
    assert excinfo.value.code == "unknown_qualification"


def test_step_and_publication_mapping_is_frozen() -> None:
    assert set(STEP_STATUS_TO_EVIDENCE_STATUS) == {
        "completed",
        "failed",
        "blocked",
        "upstream_failed",
        "cancelled",
        "pending",
        "ready",
        "running",
    }
    assert STEP_STATUS_TO_EVIDENCE_STATUS["completed"] == "complete"
    assert STEP_STATUS_TO_EVIDENCE_STATUS["blocked"] == "failed"
    assert STEP_STATUS_TO_EVIDENCE_STATUS["running"] == "incomplete"
    assert PUBLISH_STATUS_TO_EVIDENCE_STATUS == {
        "published": "complete",
        "not_published": "incomplete",
        "failed": "failed",
    }
    assert RECEIPT_STATE_TO_EVIDENCE_STATUS["blocked"] == "failed"
    assert evidence_status_for_step("completed", "published") == "complete"
    assert evidence_status_for_step("completed", "not_published") == "incomplete"
    assert evidence_status_for_step("completed", "failed") == "failed"
    assert evidence_status_for_step("failed", "published") == "failed"
    assert combine_evidence_status("complete", "partial", "complete") == "partial"
    assert combine_evidence_status("partial", "failed") == "failed"
    assert combine_evidence_status() == "complete"
    with pytest.raises(EvidenceProjectionError) as excinfo:
        combine_evidence_status("complete", "accepted")
    assert excinfo.value.code == "unknown_evidence_status"


def test_partial_qualification_projects_partial_and_is_not_acceptable(
    tmp_path: Path,
) -> None:
    task_result = _task_result(
        qualification=worker.QUALIFICATION_PARTIAL,
        partial_result=True,
        normal_reuse=False,
        publish_policy="partial",
    )
    view = _project(tmp_path, task_result=task_result)
    assert view.evidence_status == "partial"
    assert view.normal_reuse is False, "a partial result never satisfies a normal prerequisite"
    with pytest.raises(ResearchDecisionError) as excinfo:
        decide_evidence(view, "accept")
    assert excinfo.value.code == "evidence_not_complete"


def test_step_and_publication_status_are_combined_into_the_view(tmp_path: Path) -> None:
    failed_step = _project(
        tmp_path,
        step_outcomes=(
            {"status": "completed", "publish_status": "published"},
            {"status": "blocked"},
        ),
    )
    assert failed_step.evidence_status == "failed"
    unpublished = _project(tmp_path, publication_status="not_published")
    assert unpublished.evidence_status == "incomplete"


# ---------------------------------------------------------------------------
# TaskRef normalisation
# ---------------------------------------------------------------------------


def test_task_ref_requires_attempt_and_release() -> None:
    with pytest.raises(TaskRefError) as excinfo:
        parse_task_ref({"project_id": PROJECT, "task_id": "job-1", "release": {"release_id": "r"}})
    assert excinfo.value.code == "task_ref_missing_attempt"

    payload = _task_ref_payload()
    payload.pop("release")
    with pytest.raises(TaskRefError) as excinfo:
        parse_task_ref(payload)
    assert excinfo.value.code == "task_ref_missing_release"

    payload = _task_ref_payload(release={"release_id": RELEASE_ID})
    with pytest.raises(TaskRefError) as excinfo:
        parse_task_ref(payload)
    assert excinfo.value.code == "task_ref_missing_release_digest"

    for attempt in (0, -1, True, 1.5):
        with pytest.raises(TaskRefError):
            parse_task_ref(_task_ref_payload(attempt=attempt))
    with pytest.raises(TaskRefError) as excinfo:
        parse_task_ref(
            _task_ref_payload(release={"release_id": RELEASE_ID, "release_digest": "sha256:xyz"})
        )
    assert excinfo.value.code == "task_ref_invalid_release_digest"


def test_task_ref_normalises_the_legacy_alias_and_flat_release() -> None:
    flat = {
        "project_id": PROJECT,
        "job_id": "job-m1-03-static",
        "attempt": 1,
        "release_id": RELEASE_ID,
        "release_digest": RELEASE_DIGEST,
    }
    assert parse_task_ref(flat).to_dict()["task_id"] == "job-m1-03-static"
    with pytest.raises(TaskRefError) as excinfo:
        parse_task_ref({**flat, "task_id": "job-other"})
    assert excinfo.value.code == "task_ref_alias_conflict"


def test_task_binding_needs_an_explicit_release_digest() -> None:
    binding = TaskBinding(
        project_id=PROJECT, job_id="job-m1-03-static", attempt=1, release_id=RELEASE_ID
    )
    ref = TaskRef.from_task_binding(binding, release_digest=RELEASE_DIGEST)
    assert ref.task_id == "job-m1-03-static"
    with pytest.raises(TaskRefError) as excinfo:
        TaskRef.from_task_binding(binding, release_digest="")
    assert excinfo.value.code == "task_ref_missing_release_digest"


# ---------------------------------------------------------------------------
# Attribution: foreign identities are refused
# ---------------------------------------------------------------------------


def test_foreign_project_is_refused(tmp_path: Path) -> None:
    with pytest.raises(EvidenceProjectionError) as excinfo:
        _project(tmp_path, task_ref=_task_ref(project_id="proj-other"))
    assert excinfo.value.code == "foreign_project"


def test_foreign_research_round_and_task_ref_are_refused(tmp_path: Path) -> None:
    view = _project(tmp_path)
    with pytest.raises(EvidenceProjectionError) as excinfo:
        verify_attribution(view, _identity(research="res-other"), 0)
    assert excinfo.value.code == "foreign_research"
    with pytest.raises(EvidenceProjectionError) as excinfo:
        verify_attribution(view, _identity(), 3)
    assert excinfo.value.code == "foreign_round"
    with pytest.raises(EvidenceProjectionError) as excinfo:
        verify_attribution(view, _identity(), 0, _task_ref(attempt=2))
    assert excinfo.value.code == "foreign_task_ref"

    record = decide_evidence(view, "accept").to_dict()
    foreign = json.loads(json.dumps(record))
    foreign["research_id"] = "res-other"
    with pytest.raises(ResearchDecisionError) as excinfo:
        decision_from_dict(foreign, view=view)
    assert excinfo.value.code == "foreign_research"


def test_foreign_job_attempt_release_and_digest_are_refused(tmp_path: Path) -> None:
    result = _task_result()
    for overrides, code in (
        ({"job_id": "job-other"}, "foreign_job"),
        ({"attempt": 2}, "foreign_attempt"),
        ({"release_id": "release-other"}, "foreign_release"),
        ({"release_digest": "sha256:" + "e" * 64}, "foreign_release_digest"),
    ):
        with pytest.raises(EvidenceProjectionError) as excinfo:
            _project(tmp_path, receipt=_receipt(result, **overrides))
        assert excinfo.value.code == code, overrides


# ---------------------------------------------------------------------------
# Digest binding: only digest-bound facts are consumed
# ---------------------------------------------------------------------------


def test_task_result_without_a_content_digest_is_refused(tmp_path: Path) -> None:
    result = _task_result()
    result.pop("content_digest")
    with pytest.raises(EvidenceProjectionError) as excinfo:
        _project(tmp_path, task_result=result)
    assert excinfo.value.code == "not_digest_bound"


def test_tampered_task_result_is_refused(tmp_path: Path) -> None:
    result = _task_result()
    result["qualification"] = worker.QUALIFICATION_COMPLETE
    result["exit_code"] = 1  # digest no longer covers the body
    with pytest.raises(EvidenceProjectionError) as excinfo:
        _project(tmp_path, task_result=result)
    assert excinfo.value.code == "task_result_not_digest_bound"


def test_receipt_must_point_at_the_task_result_digest(tmp_path: Path) -> None:
    result = _task_result()
    with pytest.raises(EvidenceProjectionError) as excinfo:
        _project(tmp_path, receipt=_receipt(result, evidence_digests={}))
    assert excinfo.value.code == "receipt_not_digest_bound"
    with pytest.raises(EvidenceProjectionError) as excinfo:
        _project(
            tmp_path,
            receipt=_receipt(result, evidence_digests={"task_result_digest": RESULT_DIGEST}),
        )
    assert excinfo.value.code == "receipt_not_digest_bound"


def test_report_must_be_its_exact_digest_bound_bytes(tmp_path: Path) -> None:
    parsed_mapping = json.loads(_report_bytes())  # a parsed value is not evidence
    with pytest.raises(EvidenceProjectionError) as excinfo:
        project_evidence(
            identity=_identity(),
            round_index=0,
            task_ref=_task_ref(),
            receipt=_receipt(_task_result()),
            task_result=_task_result(),
            report=parsed_mapping,  # type: ignore[arg-type]
            report_digest="sha256:" + "a" * 64,
            read_root=_read_root(tmp_path),
        )
    assert excinfo.value.code == "report_not_digest_bound"

    with pytest.raises(EvidenceProjectionError) as excinfo:
        _project(tmp_path, report_digest="sha256:" + "a" * 64)
    assert excinfo.value.code == "report_digest_mismatch"


def test_claim_source_digest_mismatch_is_refused(tmp_path: Path) -> None:
    with pytest.raises(EvidenceProjectionError) as excinfo:
        _project(tmp_path, report_name="report.digest-mismatch.negative.json")
    assert excinfo.value.code == "evidence_digest_mismatch"


def test_missing_claim_source_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "empty_root"
    root.mkdir()
    with pytest.raises(EvidenceProjectionError) as excinfo:
        project_evidence(
            identity=_identity(),
            round_index=0,
            task_ref=_task_ref(),
            receipt=_receipt(_task_result()),
            task_result=_task_result(),
            report=_report_bytes(),
            report_digest=("sha256:" + __import__("hashlib").sha256(_report_bytes()).hexdigest()),
            read_root=root,
        )
    assert excinfo.value.code == "evidence_source_unavailable"


def test_execution_state_conflict_is_refused(tmp_path: Path) -> None:
    result = _task_result(status="failed", qualification=worker.QUALIFICATION_TASK_FAILED)
    with pytest.raises(EvidenceProjectionError) as excinfo:
        _project(
            tmp_path,
            task_result=result,
            receipt=_receipt(result, state="failed"),
        )
    assert excinfo.value.code == "execution_state_conflict"


# ---------------------------------------------------------------------------
# Model text is never evidence
# ---------------------------------------------------------------------------


def test_model_text_in_the_validator_slot_is_refused(tmp_path: Path) -> None:
    with pytest.raises(EvidenceProjectionError) as excinfo:
        _project(tmp_path, report_name="report.model-text.negative.json")
    assert excinfo.value.code == "invalid_science_report"
    assert "validator" in excinfo.value.detail


def test_model_conclusion_key_is_refused_by_the_shared_guard() -> None:
    document = json.loads(_report_bytes())
    document["kind"] = "model"
    with pytest.raises(EvidenceProjectionError) as excinfo:
        parse_evidence_report(document)
    assert excinfo.value.code == "invalid_science_report"
    assert "model" in excinfo.value.detail.lower()

    with pytest.raises(research_evidence.ModelConclusionError):
        research_evidence.reject_model_conclusion_as_evidence(
            {"claim": {"validator_id": "llm:conclusion"}}
        )


def test_a_decision_has_no_field_for_model_text(tmp_path: Path) -> None:
    view = _project(tmp_path)
    record = decide_evidence(view, "accept").to_dict()
    record["reason"] = "The model concludes the claim is supported."
    with pytest.raises(ResearchDecisionError) as excinfo:
        decision_from_dict(record, view=view)
    assert excinfo.value.code == "decision_field_not_allowed"
    assert "reason" in excinfo.value.detail

    record = decide_evidence(view, "accept").to_dict()
    record["rationale_codes"] = ["model_says_supported"]
    with pytest.raises(ResearchDecisionError) as excinfo:
        decision_from_dict(record, view=view)
    assert excinfo.value.code == "decision_field_not_allowed"


# ---------------------------------------------------------------------------
# Decision policy
# ---------------------------------------------------------------------------


def test_accept_precondition_order_is_frozen() -> None:
    assert ACCEPT_PRECONDITION_CODES == (
        "execution_not_completed",
        "evidence_not_complete",
        "blocking_missing_check",
        "claim_status_not_supported",
        "evidence_ref_required",
    )
    assert set(DECISION_VALUES) == {"accept", "revise", "reject", "defer", "escalate"}


def test_decision_requires_digest_bound_evidence(tmp_path: Path) -> None:
    view = _project(tmp_path)
    empty = EvidenceView.from_dict({**view.to_dict(), "evidence_refs": [], "projection_digest": ""})
    assert empty.evidence_refs == ()
    for decision in DECISION_VALUES:
        with pytest.raises(ResearchDecisionError) as excinfo:
            validate_research_decision(empty, decision)
        assert excinfo.value.code == "evidence_ref_required"


def test_decision_record_is_independent_of_every_task_status(tmp_path: Path) -> None:
    view = _project(tmp_path)
    record = decide_evidence(view, "accept").to_dict()
    assert record["schema"] == RESEARCH_DECISION_SCHEMA
    wire = json.dumps(record)
    for status in ("QUEUED", "RUNNING", "WAITING_REVIEW", "COMPLETED", "FAILED"):
        assert status not in wire
    assert record["decision"] == "accept"
    assert record["evidence"]["status"] == "complete"
    assert record["evidence"]["overall_claim_status"] == "supported"
    assert "digest_bound_evidence" in record["rationale_codes"]
    assert record["projection_digest"] == view.projection_digest


def test_projection_digest_detects_tampering(tmp_path: Path) -> None:
    view = _project(tmp_path)
    wire = view.to_dict()
    wire["claims"][0]["status"] = "refuted"
    with pytest.raises(EvidenceProjectionError) as excinfo:
        EvidenceView.from_dict(wire)
    assert excinfo.value.code == "projection_digest_mismatch"


# ---------------------------------------------------------------------------
# One platform parse path
# ---------------------------------------------------------------------------


def test_science_binding_and_projection_share_one_evidence_parse_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    real_validate = science_binding.validate_evidence_report
    real_parse = research_evidence.EvidenceReport.from_dict.__func__

    def spy_validate(document: Any) -> None:
        calls.append("b18_registration")
        real_validate(document)

    def spy_parse(cls: Any, payload: Any, where: str = "report") -> Any:
        calls.append("cccp_parser")
        return real_parse(cls, payload, where)

    monkeypatch.setattr(science_binding, "validate_evidence_report", spy_validate)
    monkeypatch.setattr(projection, "validate_evidence_report", spy_validate)
    monkeypatch.setattr(research_evidence.EvidenceReport, "from_dict", classmethod(spy_parse))

    parse_evidence_report(json.loads(_report_bytes()))
    assert calls == ["b18_registration", "cccp_parser"]

    calls.clear()
    with pytest.raises(Exception):
        parse_evidence_report({"schema_version": 1})
    assert calls == ["b18_registration"], "the registration check runs first and refuses"


def test_no_other_platform_module_parses_an_evidence_report() -> None:
    """The projection is the only B-side consumer of A's report parser."""
    offenders: list[str] = []
    for root in (REPO / "src" / "acp_services", REPO / "src" / "acp"):
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "EvidenceReport.from_dict" in text or "evidence_report_from_dict" in text:
                offenders.append(str(path.relative_to(REPO)))
    assert offenders == ["src/acp_services/runtime/evidence_projection.py"], offenders


def test_science_binding_vocabulary_is_the_cccp_vocabulary() -> None:
    assert set(science_binding.CLAIM_STATUSES) == set(research_evidence.CLAIM_STATUSES)
    assert set(science_binding.EXECUTION_TERMINAL_STATES) == set(research_evidence.TERMINAL_STATES)
    assert science_binding.EVIDENCE_STATUS_NOT_EVALUATED == "not_evaluated"
    document = {"b": [1, 2], "a": "x"}
    assert projection._content_digest(document) == science_binding.document_digest(document)


def test_projection_never_imports_the_legacy_acp_package() -> None:
    text = (REPO / "src" / "acp_services" / "runtime" / "evidence_projection.py").read_text(
        encoding="utf-8"
    )
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("import acp") or stripped.startswith("from acp "):
            raise AssertionError(f"acp_services must not import acp: {stripped}")
        if stripped.startswith("from acp."):
            raise AssertionError(f"acp_services must not import acp: {stripped}")


def test_receipt_states_are_the_frozen_envelope_states() -> None:
    assert set(RECEIPT_STATE_TO_EVIDENCE_STATUS) == set(RECEIPT_STATES)
