"""B20b — evidence integrity for the A-audit / C-ResearchRecord handoff.

B20a-exec is ``NOT_VERIFIED(c24a_absent)``, so this file proves the *handoff
shape and honesty rules*, not a scientific result.  It asserts that:

* the scenario result, the scientific claim status and the H4 gate stay three
  independent fields;
* no proposition is ``supported`` without a real source artifact and digest;
* every proposition uses one of the four frozen claim statuses;
* B's execution facts are copied from the B20a receipt and counters, and C's
  reference cannot overwrite them;
* the H4 rows stay ``NOT_VERIFIED`` with no promotion; and
* one imaginary frequency is never recorded as IRC connectivity.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parents[1]
_B20B = _REPO / "reports" / "acp2_implementation" / "bb45a46" / "B" / "B20b"
_B20 = _REPO / "reports" / "acp2_implementation" / "bb45a46" / "B" / "B20"
_A_ROOT = Path("<acp2-dev>/A")
_C_ROOT = Path("<acp2-dev>/C")

_CLAIM_STATUSES = {"supported", "refuted", "inconclusive", "not_evaluated"}
_REQUIRED_TOP_LEVEL = (
    "run_spec_ref",
    "b_execution_facts",
    "a_audit_ref",
    "c_t35_ref",
    "propositions",
    "result_vs_claim",
    "negative_case_rule",
    "one_imaginary_frequency_not_connectivity",
)
_C_MAY_NOT_OVERRIDE = (
    "job_id",
    "attempt",
    "release_id",
    "submission_id",
    "manifest_generation",
    "task_ref",
    "execution_result",
)
_B_EXECUTION_REF_KEYS = (
    "job_id",
    "attempt",
    "release_id",
    "submission_id",
    "operation_id",
    "manifest_generation",
)
_H4_ROW_LABELS = ("H4-01", "H4-02", "H4-03", "H4-04", "H4-search")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _handoff() -> dict[str, Any]:
    return _load(_B20B / "science-handoff.json")


def _ledger() -> dict[str, Any]:
    return _load(_B20B / "h4-ledger.json")


def _matrix() -> dict[str, Any]:
    return _load(_B20B / "reference-matrix.json")


def _validate_handoff(document: dict[str, Any]) -> list[str]:
    """Return every honesty/shape violation in ``document``.

    This is intentionally the same rule set the evidence was generated under,
    so a reviewer can copy the file, mark an inconclusive row as supported, and
    see the audit reject it.
    """

    errors: list[str] = []
    for key in _REQUIRED_TOP_LEVEL:
        if key not in document:
            errors.append(f"missing top-level field: {key}")
    if errors:
        return errors

    for prop in document["propositions"]:
        claim_id = prop.get("claim_id", "<missing>")
        status = prop.get("status")
        if status not in _CLAIM_STATUSES:
            errors.append(f"{claim_id}: unknown claim status {status!r}")
        if status == "supported":
            digest = (prop.get("source_digest") or {}).get("value")
            if not prop.get("source_artifact") or not digest:
                errors.append(f"{claim_id}: supported without source artifact+digest")

    separation = document["result_vs_claim"]
    for axis in ("scenario_result", "claim_status", "h4_gate"):
        if axis not in separation or "value" not in separation[axis]:
            errors.append(f"result_vs_claim missing axis: {axis}")
    if not errors:
        values = [
            separation[axis]["value"] for axis in ("scenario_result", "claim_status", "h4_gate")
        ]
        statuses = [prop["status"] for prop in document["propositions"]]
        if "supported" in values[:2] and "supported" not in statuses:
            errors.append("claim_status promoted above every proposition")
        if separation["h4_gate"]["value"] == "open" and "supported" in statuses:
            errors.append("H4 gate open while a proposition is supported")

    facts = document["b_execution_facts"]
    if facts.get("status") == "NOT_VERIFIED":
        if facts.get("task_ref") is not None:
            errors.append("not-verified execution cannot carry a TaskRef")
        if facts.get("execution_chain_executed"):
            errors.append("not-verified execution cannot claim the chain executed")

    for key in _C_MAY_NOT_OVERRIDE:
        if document["c_t35_ref"].get(key) is not None:
            errors.append(f"C reference overrides B task truth: {key}")

    if document["negative_case_rule"].get("negative_satisfies_positive_gate"):
        errors.append("negative case cannot satisfy the positive gate")
    if document["negative_case_rule"].get("negative_closes_h4"):
        errors.append("negative case cannot close H4")
    if document["one_imaginary_frequency_not_connectivity"].get(
        "one_imaginary_frequency_proves_irc_connectivity"
    ):
        errors.append("one imaginary frequency cannot prove IRC connectivity")

    return errors


def _validate_ledger(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if document.get("h4_gate") != "open":
        errors.append("B20b must leave the H4 gate open")
    labels = [row.get("label") for row in document.get("rows", [])]
    if tuple(labels) != _H4_ROW_LABELS:
        errors.append(f"unexpected H4 row set: {labels!r}")
    for row in document.get("rows", []):
        if row.get("status") == "PASS":
            errors.append(f"{row.get('row_id')}: B20b promoted a NOT_VERIFIED row to PASS")
        if row.get("promoted"):
            errors.append(f"{row.get('row_id')}: promoted without a real source")
        if not row.get("source_gap"):
            errors.append(f"{row.get('row_id')}: missing source gap")
    if document.get("promotions"):
        errors.append("B20b recorded a promotion")
    return errors


def test_handoff_has_required_shape_and_three_separate_axes() -> None:
    handoff = _handoff()
    assert handoff["schema"] == "acp2.b20b.science-handoff.v1"
    assert _validate_handoff(handoff) == []
    axis = handoff["result_vs_claim"]
    assert axis["scenario_result"]["value"] == "NOT_VERIFIED"
    assert axis["claim_status"]["value"] == "not_evaluated"
    assert axis["h4_gate"]["value"] == "open"
    assert axis["independent"] is True


def test_zero_supported_rows_and_every_row_uses_the_frozen_vocabulary() -> None:
    handoff = _handoff()
    statuses = [prop["status"] for prop in handoff["propositions"]]
    assert statuses, "the handoff must list propositions"
    assert "supported" not in statuses
    assert set(statuses) <= _CLAIM_STATUSES
    assert handoff["validation"]["supported_rows"] == 0
    assert handoff["validation"]["supported_rows_without_source_artifact"] == 0


def test_run_spec_matches_b20a_and_never_resets_the_clock() -> None:
    handoff = _handoff()
    source = _load(_B20 / "run-spec.json")
    ref = handoff["run_spec_ref"]
    assert ref["sha256"] == _sha256(_B20 / "run-spec.json")
    for key in (
        "started_at",
        "started_at_status",
        "science_deadline_seconds",
        "export_budget_seconds",
        "aggregate_budget_seconds",
        "total_budget_seconds",
        "timeout_env",
        "queue_accounting",
        "inflight_stop_policy",
    ):
        assert ref[key] == source[key], key
    assert ref["started_at"] is None
    assert ref["clock_reset_allowed"] is False
    assert ref["stop_new_science_at_deadline"] is True
    assert ref["evidence_phase_fails_on_elapsed_time_alone"] is False
    assert (
        ref["science_deadline_seconds"]
        + ref["export_budget_seconds"]
        + ref["aggregate_budget_seconds"]
        == ref["total_budget_seconds"]
        == 14400
    )


def test_b_execution_facts_come_from_the_b20a_receipt_and_counters() -> None:
    handoff = _handoff()
    receipt = _load(_B20 / "da-runtime-da-submit-20261023T091047Z-1425776-5bf82a03.json")
    counters = _load(_B20 / "counters.json")
    facts = handoff["b_execution_facts"]

    assert facts["receipt"]["sha256"] == _sha256(
        _B20 / "da-runtime-da-submit-20261023T091047Z-1425776-5bf82a03.json"
    )
    assert facts["receipt"]["run_id"] == receipt["run_id"]
    assert facts["receipt"]["phase"] == receipt["phase"]
    assert facts["receipt"]["result"] == receipt["result"] == "NOT_VERIFIED"
    assert facts["receipt"]["reason"] == receipt["reason"] == "c24a_absent"
    for key in _B_EXECUTION_REF_KEYS:
        assert facts[key] == receipt[key], key
        assert facts[key] is None, key
    assert facts["task_ref"] is None
    assert facts["execution_chain_executed"] is False

    recorded = facts["counters"]
    assert recorded["sha256"] == _sha256(_B20 / "counters.json")
    for key in ("tasks_created", "prepare_operations", "qc_invocations", "reservations", "bsub"):
        assert recorded[key] == counters[key] == 0, key
    assert recorded["submission_attempted"] == counters["submission_attempted"] is False
    assert facts["budget_counts"]["science_clock_started"] is False


def test_a_and_c_references_are_attached_by_reference_not_fabricated() -> None:
    handoff = _handoff()
    a_ref = handoff["a_audit_ref"]
    assert a_ref["owner"] == "A-20"
    assert a_ref["status"] == "NOT_VERIFIED"
    assert a_ref["attached_by_reference"] is True
    assert a_ref["fabricated_receipt"] is False
    assert a_ref["expected_artifact"].endswith("science/handoff-H4.json")
    assert "handoff_id=H4" in a_ref["expected_schema"]
    assert a_ref["digest"]["value"] == _sha256(Path(a_ref["expected_artifact"]))

    c_ref = handoff["c_t35_ref"]
    assert c_ref["owner"] == "C T-35 / C24b"
    assert c_ref["status"] == "NOT_VERIFIED"
    assert c_ref["attached_by_reference"] is True
    assert c_ref["fabricated_receipt"] is False
    assert c_ref["present"] is False
    assert c_ref["digest"] is None
    for expected in c_ref["expected_artifacts"]:
        assert expected["path"].startswith(
            "<repo>/reports/acp2_implementation/bb45a46/C/da-research-live/b/"
        )
        assert expected["schema"].startswith(("acp2.experience.", "acp2.acceptance."))
    assert c_ref["does_not_overwrite_b_task_truth"] is True


def test_h4_ledger_keeps_every_row_open_without_promotion() -> None:
    ledger = _ledger()
    assert ledger["schema"] == "acp2.b20b.h4-ledger.v1"
    assert _validate_ledger(ledger) == []
    assert [row["label"] for row in ledger["rows"]] == list(_H4_ROW_LABELS)
    assert all(row["status"] == "NOT_VERIFIED" for row in ledger["rows"])
    assert all(row["claim_status"] == "not_evaluated" for row in ledger["rows"])
    assert all(row["promoted"] is False for row in ledger["rows"])
    assert all(row["source_gap"] for row in ledger["rows"])


def test_negative_honesty_and_single_imaginary_frequency_rules() -> None:
    handoff = _handoff()
    negative = handoff["negative_case_rule"]
    assert negative["negative_can_pass_scenario"] is True
    assert negative["negative_satisfies_positive_gate"] is False
    assert negative["negative_closes_h4"] is False

    frequency = handoff["one_imaginary_frequency_not_connectivity"]
    assert frequency["one_imaginary_frequency_proves_first_order_saddle_candidate"] is True
    assert frequency["one_imaginary_frequency_proves_irc_connectivity"] is False
    assert "real forward IRC completion" in frequency["irc_connectivity_requires"]
    assert "real reverse IRC completion" in frequency["irc_connectivity_requires"]


def test_reference_matrix_digests_match_published_files() -> None:
    if not (_A_ROOT / "docs").is_dir() or not (_C_ROOT / "docs").is_dir():
        pytest.skip("A/C track checkouts are not present in this environment")
    matrix = _matrix()
    assert matrix["schema"] == "acp2.b20b.reference-matrix.v1"
    for ref in matrix["references"]:
        path = Path(ref["path"])
        if ref["present"]:
            assert path.is_file(), ref["path"]
            assert ref["sha256"] == _sha256(path), ref["path"]
        else:
            assert ref["sha256"] is None, ref["path"]
            assert ref["status"].startswith("NOT_VERIFIED"), ref["path"]


def test_audit_rejects_a_supported_row_without_evidence() -> None:
    document = copy.deepcopy(_handoff())
    document["propositions"][0]["status"] = "supported"
    document["propositions"][0]["source_artifact"] = None
    document["propositions"][0]["source_digest"] = None
    errors = _validate_handoff(document)
    assert any("supported without source artifact+digest" in item for item in errors)


def test_audit_rejects_a_supported_digestless_row() -> None:
    document = copy.deepcopy(_handoff())
    document["propositions"][1]["status"] = "supported"
    document["propositions"][1]["source_digest"] = {"algorithm": "sha256", "value": None}
    errors = _validate_handoff(document)
    assert any("supported without source artifact+digest" in item for item in errors)


def test_audit_rejects_claim_promotion_without_a_supported_proposition() -> None:
    document = copy.deepcopy(_handoff())
    document["result_vs_claim"]["claim_status"]["value"] = "supported"
    errors = _validate_handoff(document)
    assert any("promoted above every proposition" in item for item in errors)


def test_audit_rejects_unknown_status_and_c_overwrite_of_b_task_truth() -> None:
    document = copy.deepcopy(_handoff())
    document["propositions"][0]["status"] = "confirmed"
    document["c_t35_ref"]["attempt"] = 1
    errors = _validate_handoff(document)
    assert any("unknown claim status" in item for item in errors)
    assert any("C reference overrides B task truth" in item for item in errors)


def test_audit_rejects_h4_promotion_and_negative_substitution() -> None:
    ledger = copy.deepcopy(_ledger())
    ledger["rows"][0]["status"] = "PASS"
    ledger["rows"][0]["promoted"] = True
    ledger["promotions"] = [{"row_id": "H4-01-valid-ts"}]
    errors = _validate_ledger(ledger)
    assert any("promoted a NOT_VERIFIED row" in item for item in errors)
    assert any("promoted without a real source" in item for item in errors)
    assert any("recorded a promotion" in item for item in errors)

    handoff = copy.deepcopy(_handoff())
    handoff["negative_case_rule"]["negative_closes_h4"] = True
    handoff["one_imaginary_frequency_not_connectivity"][
        "one_imaginary_frequency_proves_irc_connectivity"
    ] = True
    errors = _validate_handoff(handoff)
    assert any("negative case cannot close H4" in item for item in errors)
    assert any("one imaginary frequency cannot prove IRC connectivity" in item for item in errors)


def test_validation_and_no_relabel_records_are_current() -> None:
    validation = _load(_B20B / "validation-log.json")
    no_relabel = _load(_B20B / "no-relabel-check.json")
    assert validation["result"] == "PASS"
    assert no_relabel["result"] == "PASS"
    for name in ("science-handoff.json", "h4-ledger.json", "reference-matrix.json"):
        assert validation["artifacts"][name]["sha256"] == _sha256(_B20B / name), name
    assert no_relabel["source_facts"]["b20a_receipt"]["sha256"] == _sha256(
        _B20 / "da-runtime-da-submit-20261023T091047Z-1425776-5bf82a03.json"
    )
    assert no_relabel["source_facts"]["b20a_run_spec"]["sha256"] == _sha256(_B20 / "run-spec.json")
    assert no_relabel["source_facts"]["b20a_counters"]["sha256"] == _sha256(_B20 / "counters.json")
