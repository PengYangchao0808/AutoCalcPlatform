"""A-20: validator for the ACP2 H4 Diels-Alder science gate (handoff H4).

The gate ledger is a deterministic JSON record.  This module keeps three records
separate that must never be collapsed: a scenario *result*, a scientific claim
*status* and the H4 *closure* ledger.  It enforces the honesty rules that JSON
alone cannot express:

* a required positive row (valid TS, bidirectional IRC, endpoint connectivity,
  comparable energy, search) cannot be ``PASS`` without real published evidence;
* the search row is separate from the known-candidate execution chain and a
  control-loop/known-candidate result never turns it into a ``PASS``;
* a deferral is valid only with an owner-ratified ``search_scope=DEFERRED`` and
  synchronized records, and a deferral never closes H4;
* H4 cannot close while any required row is ``NOT_VERIFIED``/inconclusive;
* all four B08 crash windows are present, and the constructed reconciliation
  helper reports duplicates and undispatched accepted tasks.

Standard-library JSON parsing only.  The B08 helper lives in the acceptance
harness and is exercised on constructed records; no platform job, QC process or
``bsub`` is started here.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
SCIENCE = REPO / "docs" / "reference" / "next-change" / "acp2-contracts" / "science"
H4_PATH = SCIENCE / "handoff-H4.json"
H3_PATH = SCIENCE / "handoff-H3.json"
HARNESS_PATH = REPO / "scripts" / "acceptance" / "acp2_science.py"

ALLOWED_RESULTS = frozenset({"PASS", "FAIL", "NOT_VERIFIED"})
ALLOWED_CLAIM_STATUSES = frozenset({"supported", "refuted", "inconclusive", "not_evaluated"})
ALLOWED_ROW_KINDS = frozenset(
    {
        "required-positive",
        "negative-honesty",
        "search",
        "static-gate",
        "platform-recovery",
        "b08-crash-window",
    }
)
REQUIRED_POSITIVE_ROW_IDS = (
    "H4-01-valid-ts",
    "H4-02-bidirectional-irc",
    "H4-03-endpoint-connectivity",
    "H4-04-comparable-energy",
)
REQUIRED_SEARCH_ROW_ID = "H4-search"
REQUIRED_NEGATIVE_CASE_IDS = (
    "missing-frequency",
    "wrong-endpoint",
    "corrupt-digest",
    "old-provider-version",
    "mismatched-standard-state",
)
B08_WINDOW_IDS = (
    "reservation-before-persist",
    "persisted-before-submission-start",
    "submitted-lost-response",
    "restart-replay",
)
B08_INVARIANTS = (
    "no_duplicate_directory",
    "no_duplicate_reservation",
    "no_duplicate_qc_process",
    "no_duplicate_bsub",
    "no_permanently_undispatched_accepted_task",
    "only_identity_valid_complete_results_reused",
    "publication_retry_does_not_relaunch_qc",
)
PLATFORM_FAILURE_CASE_IDS = (
    "worker-interruption",
    "publication-failure",
    "recovery",
    "late-receipt",
)
REQUIRED_TOP_LEVEL = (
    "handoff_id",
    "handoff_version",
    "contract_set",
    "contract_set_version",
    "contract_set_frozen",
    "contract_version_basis",
    "frozen_at",
    "gate_command",
    "records_separated",
    "rows",
    "known_candidate_chain",
    "search",
    "negative_checks",
    "b08_crash_windows",
    "platform_failure_injections",
    "h4_closure",
    "real_computation",
    "external_evidence",
    "receipt_protocol_gap",
    "source",
)
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_IDENTITY_KEYS = frozenset(
    {"run_key", "attempt", "release_id", "submission_id", "registry_revision"}
)


class HandoffH4Error(ValueError):
    """Raised when the H4 ledger records an unsupported or dishonest claim."""


def load_h4(path: Path = H4_PATH) -> dict[str, Any]:
    """Parse the gate ledger with stdlib JSON only."""
    with path.open(encoding="utf-8") as handle:
        doc: dict[str, Any] = json.load(handle)
    if not isinstance(doc, dict):
        raise HandoffH4Error("handoff root must be a JSON object")
    return doc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_non_empty(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HandoffH4Error(f"{where}: required non-empty text")
    return value


def _require_evidence(evidence: object, where: str) -> None:
    if not isinstance(evidence, dict):
        raise HandoffH4Error(f"{where}: evidence must be an object")
    executed = False
    for key in ("commands", "tests"):
        values = evidence.get(key)
        if isinstance(values, list) and any(
            isinstance(item, str) and item.strip() for item in values
        ):
            executed = True
    artifacts = evidence.get("artifacts")
    if isinstance(artifacts, list):
        for entry in artifacts:
            if not isinstance(entry, dict):
                raise HandoffH4Error(f"{where}: artifact entry must be an object")
            _require_non_empty(entry.get("path"), f"{where} artifact path")
            digest = entry.get("sha256")
            if not isinstance(digest, str) or not DIGEST_PATTERN.match(digest):
                raise HandoffH4Error(f"{where}: artifact needs a 64-hex sha256")
            executed = True
    if not executed:
        raise HandoffH4Error(f"{where}: no executed command, test or digested artifact")


def _rows(doc: dict[str, Any]) -> list[dict[str, Any]]:
    rows = doc.get("rows")
    if not isinstance(rows, list) or not rows:
        raise HandoffH4Error("rows must be a non-empty list")
    for row in rows:
        if not isinstance(row, dict):
            raise HandoffH4Error("every row must be an object")
    return rows


def _row_map(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for row in _rows(doc):
        row_id = _require_non_empty(row.get("row_id"), "row_id")
        if row_id in mapping:
            raise HandoffH4Error(f"duplicate row id {row_id!r}")
        mapping[row_id] = row
    return mapping


def _row(doc: dict[str, Any], row_id: str) -> dict[str, Any]:
    mapping = _row_map(doc)
    if row_id not in mapping:
        raise HandoffH4Error(f"missing row {row_id!r}")
    return mapping[row_id]


def _require_gap(row: dict[str, Any], where: str) -> None:
    _require_non_empty(row.get("gap"), f"{where} gap")


def reject_positive_row_pass_without_real_evidence(
    doc: dict[str, Any], row: dict[str, Any]
) -> None:
    """Reject a required positive row marked PASS without real published evidence."""
    if row.get("row_kind") != "required-positive" or row.get("result") != "PASS":
        return
    where = str(row.get("row_id"))
    if row.get("evidence_kind") != "real":
        raise HandoffH4Error(f"{where}: PASS needs evidence_kind='real'")
    if row.get("real_run") is not True or row.get("constructed") is not False:
        raise HandoffH4Error(f"{where}: PASS needs a real (not constructed) run")
    if row.get("scientific_claim_status") != "supported":
        raise HandoffH4Error(f"{where}: PASS needs a supported claim status")
    if str(row.get("gap") or "").strip():
        raise HandoffH4Error(f"{where}: PASS must not carry a gap")
    real = row.get("real_evidence")
    if not isinstance(real, dict):
        raise HandoffH4Error(f"{where}: PASS needs a real_evidence record")
    for key in ("producer", "artifact_id", "run_key", "authorized", "digest"):
        if key not in real:
            raise HandoffH4Error(f"{where}: real_evidence missing {key!r}")
    if real["producer"] != "B20a-exec":
        raise HandoffH4Error(f"{where}: a required positive needs the B20a-exec producer")
    if real["authorized"] is not True:
        raise HandoffH4Error(f"{where}: real evidence must be authorized")
    if not DIGEST_PATTERN.match(str(real["digest"])):
        raise HandoffH4Error(f"{where}: real evidence needs a 64-hex digest")
    producer = row.get("producer")
    if not isinstance(producer, dict):
        raise HandoffH4Error(f"{where}: producer block required")
    if producer.get("published") is not True:
        raise HandoffH4Error(f"{where}: producer artifact is not published")
    if producer.get("digest") != real["digest"]:
        raise HandoffH4Error(f"{where}: producer digest differs from real_evidence digest")
    external = doc.get("external_evidence")
    if not isinstance(external, dict):
        raise HandoffH4Error(f"{where}: external_evidence block required")
    b20a_exec = external.get("b20a_exec")
    if not isinstance(b20a_exec, dict) or b20a_exec.get("result") != "PASS":
        raise HandoffH4Error(f"{where}: external B20a-exec evidence is not PASS")
    if b20a_exec.get("digest") != real["digest"]:
        raise HandoffH4Error(f"{where}: external B20a-exec digest differs")
    evidence = row.get("evidence")
    _require_evidence(evidence, f"{where} PASS")
    assert isinstance(evidence, dict)
    artifacts = evidence.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise HandoffH4Error(f"{where}: PASS needs digested real artifacts")
    commands = evidence.get("commands")
    if not isinstance(commands, list) or not any(
        isinstance(item, str) and item.strip() for item in commands
    ):
        raise HandoffH4Error(f"{where}: PASS needs an executed command")


def validate_rows(doc: dict[str, Any]) -> None:
    """Validate row taxonomy, three-state results and required-row coverage."""
    mapping = _row_map(doc)
    for row in mapping.values():
        row_id = str(row["row_id"])
        if row.get("row_kind") not in ALLOWED_ROW_KINDS:
            raise HandoffH4Error(f"{row_id}: unknown row_kind {row.get('row_kind')!r}")
        if row.get("result") not in ALLOWED_RESULTS:
            raise HandoffH4Error(f"{row_id}: result {row.get('result')!r} not allowed")
        claim_status = row.get("scientific_claim_status")
        if claim_status is not None and claim_status not in ALLOWED_CLAIM_STATUSES:
            raise HandoffH4Error(f"{row_id}: unknown claim status {claim_status!r}")
        if not isinstance(row.get("required_for_h4_closure"), bool):
            raise HandoffH4Error(f"{row_id}: required_for_h4_closure must be a boolean")
        if row.get("result") != "PASS":
            _require_gap(row, row_id)
        _require_non_empty(row.get("reason"), f"{row_id} reason")
        reject_positive_row_pass_without_real_evidence(doc, row)

    for row_id in REQUIRED_POSITIVE_ROW_IDS:
        row = mapping.get(row_id)
        if row is None:
            raise HandoffH4Error(f"missing required positive row {row_id!r}")
        if row.get("row_kind") != "required-positive":
            raise HandoffH4Error(f"{row_id}: must be a required-positive row")
        if row.get("required_for_h4_closure") is not True:
            raise HandoffH4Error(f"{row_id}: must count toward H4 closure")

    search_rows = [row for row in mapping.values() if row.get("row_kind") == "search"]
    if len(search_rows) != 1 or search_rows[0].get("row_id") != REQUIRED_SEARCH_ROW_ID:
        raise HandoffH4Error("exactly one search row is required")
    if search_rows[0].get("required_for_h4_closure") is not True:
        raise HandoffH4Error("the search row is in P2/H4 scope by default")

    b08_rows = [row for row in mapping.values() if row.get("row_kind") == "b08-crash-window"]
    window_ids = [row.get("window_id") for row in b08_rows]
    if len(b08_rows) != len(B08_WINDOW_IDS) or set(window_ids) != set(B08_WINDOW_IDS):
        raise HandoffH4Error(f"B08 window rows must be exactly {list(B08_WINDOW_IDS)}")
    for row in b08_rows:
        invariants = row.get("required_invariants")
        if not isinstance(invariants, list) or not set(B08_INVARIANTS) <= set(invariants):
            raise HandoffH4Error(f"{row['row_id']}: every B08 no-duplicate invariant is required")


def validate_negative_checks(doc: dict[str, Any]) -> None:
    """Require the five negative cases to be honestly reported and non-substituting."""
    rows = [row for row in _rows(doc) if row.get("row_kind") == "negative-honesty"]
    case_ids = [row.get("case_id") for row in rows]
    if len(rows) != len(REQUIRED_NEGATIVE_CASE_IDS) or set(case_ids) != set(
        REQUIRED_NEGATIVE_CASE_IDS
    ):
        raise HandoffH4Error(
            f"negative rows must contain exactly {list(REQUIRED_NEGATIVE_CASE_IDS)}"
        )
    for row in rows:
        case_id = str(row["case_id"])
        if row.get("result") != "PASS":
            raise HandoffH4Error(f"{case_id}: the negative case was not correctly reported")
        if row.get("correctly_reported") is not True:
            raise HandoffH4Error(f"{case_id}: correctly_reported must be true")
        if row.get("replaces_positive_requirement") is not False:
            raise HandoffH4Error(f"{case_id}: a negative case never replaces the positive")
        if row.get("required_for_h4_closure") is not False:
            raise HandoffH4Error(f"{case_id}: a negative case cannot be a closure requirement")
        observed = row.get("observed_outcome")
        if observed == "supported":
            raise HandoffH4Error(f"{case_id}: a bad artifact must never be supported")
        expected_claim = {
            "inconclusive": "inconclusive",
            "refuted": "refuted",
            "rejected": "not_evaluated",
            "identity_changed_refused": "not_evaluated",
        }.get(observed)
        if expected_claim is None:
            raise HandoffH4Error(f"{case_id}: unknown observed outcome {observed!r}")
        if row.get("scientific_claim_status") != expected_claim:
            raise HandoffH4Error(
                f"{case_id}: claim status {row.get('scientific_claim_status')!r} does not "
                f"match observed outcome {observed!r}"
            )
        _require_evidence(row.get("evidence"), f"{case_id} evidence")

    ledger = doc.get("negative_checks")
    if not isinstance(ledger, list):
        raise HandoffH4Error("negative_checks must be a list")
    ledger_ids = [entry.get("case_id") if isinstance(entry, dict) else None for entry in ledger]
    if set(ledger_ids) != set(REQUIRED_NEGATIVE_CASE_IDS) or len(ledger) != len(
        REQUIRED_NEGATIVE_CASE_IDS
    ):
        raise HandoffH4Error("negative_checks ledger must cover exactly the five cases")
    for entry in ledger:
        assert isinstance(entry, dict)
        if entry.get("replaces_positive_requirement") is not False:
            raise HandoffH4Error(
                f"{entry.get('case_id')}: ledger entry may not replace the positive"
            )


def validate_search(doc: dict[str, Any]) -> None:
    """Reject a search row merged into the known-candidate chain or an unfounded PASS."""
    search = doc.get("search")
    if not isinstance(search, dict):
        raise HandoffH4Error("search must be an object")
    if search.get("row_id") != REQUIRED_SEARCH_ROW_ID:
        raise HandoffH4Error("search must reference its own H4-search row")
    if search.get("separate_from_known_candidate_chain") is not True:
        raise HandoffH4Error("search must stay separate from the known-candidate chain")
    if search.get("producer") != "B20a-search":
        raise HandoffH4Error("search producer must be B20a-search")
    if search.get("merged_into_known_candidate_chain") is True:
        raise HandoffH4Error("search row must not be merged into the known-candidate chain")
    contract = search.get("contract")
    _require_non_empty(contract, "search contract")
    contract_path = REPO / str(contract)
    if not contract_path.is_file():
        raise HandoffH4Error(f"search contract file is missing: {contract}")
    if search.get("contract_sha256") != _sha256(contract_path):
        raise HandoffH4Error("search contract digest does not match its bytes")

    known = doc.get("known_candidate_chain")
    if not isinstance(known, dict):
        raise HandoffH4Error("known_candidate_chain must be an object")
    if known.get("producer") == search.get("producer"):
        raise HandoffH4Error("search producer must differ from the known-candidate producer")
    known_rows = known.get("row_ids")
    if not isinstance(known_rows, list):
        raise HandoffH4Error("known_candidate_chain.row_ids must be a list")
    if REQUIRED_SEARCH_ROW_ID in known_rows:
        raise HandoffH4Error("the search row must not be a known-candidate row")

    scope = search.get("search_scope")
    if not isinstance(scope, dict):
        raise HandoffH4Error("search_scope must be an object")
    scope_status = scope.get("status")
    if scope_status not in {"IN_SCOPE", "DEFERRED"}:
        raise HandoffH4Error(f"unknown search_scope status {scope_status!r}")
    if scope_status == "DEFERRED":
        if scope.get("deferred") is not True:
            raise HandoffH4Error("search_scope=DEFERRED must set deferred=true")
        if scope.get("owner_ratified") is not True:
            raise HandoffH4Error("a deferral requires owner ratification")
        _require_non_empty(scope.get("ratification_ref"), "deferral ratification_ref")
        if scope.get("synchronized_records") is not True:
            raise HandoffH4Error("a deferral requires synchronized B/C/main-plan records")
        if search.get("result") == "PASS":
            raise HandoffH4Error("a deferred search cannot be PASS")
    else:
        if scope.get("deferred") is not False:
            raise HandoffH4Error("search_scope=IN_SCOPE must set deferred=false")

    row = _row(doc, REQUIRED_SEARCH_ROW_ID)
    if row.get("row_kind") != "search":
        raise HandoffH4Error("H4-search row must have row_kind='search'")
    if search.get("result") != row.get("result"):
        raise HandoffH4Error("search block result differs from the H4-search row")
    if search.get("scientific_claim_status") != row.get("scientific_claim_status"):
        raise HandoffH4Error("search block claim status differs from the H4-search row")
    if str(row.get("gap") or "").strip() == "" and search.get("result") != "PASS":
        raise HandoffH4Error("a non-PASS search row needs an exact gap")

    if search.get("result") != "PASS":
        return
    if search.get("source_kind") in {"control-loop", "known-candidate"}:
        raise HandoffH4Error("a control-loop/known-candidate result never proves search")
    if row.get("evidence_kind") != "real" or row.get("real_run") is not True:
        raise HandoffH4Error("search PASS needs real execution evidence")
    if row.get("constructed") is not False:
        raise HandoffH4Error("search PASS cannot be constructed")
    producer = row.get("producer")
    if not isinstance(producer, dict) or producer.get("published") is not True:
        raise HandoffH4Error("search PASS needs a published B20a-search artifact")
    if producer.get("readiness") != "verified":
        raise HandoffH4Error("search PASS needs readiness='verified'")
    if not DIGEST_PATTERN.match(str(producer.get("digest"))):
        raise HandoffH4Error("search PASS needs a 64-hex producer digest")
    if row.get("scientific_claim_status") != "supported":
        raise HandoffH4Error("search PASS needs a supported search claim")
    criteria = search.get("success_criteria_satisfied")
    if not isinstance(criteria, dict) or not criteria:
        raise HandoffH4Error("search PASS needs the success-criteria record")
    if not all(value is True for value in criteria.values()):
        raise HandoffH4Error("search PASS needs every success criterion satisfied")
    if not isinstance(search.get("accepted_generated_candidate_count"), int):
        raise HandoffH4Error("search PASS needs an accepted generated-candidate count")
    if search["accepted_generated_candidate_count"] < 1:
        raise HandoffH4Error("search PASS needs at least one accepted generated candidate")


def validate_b08_windows(doc: dict[str, Any]) -> None:
    """Require all four crash windows and forbid a PASS without platform evidence."""
    block = doc.get("b08_crash_windows")
    if not isinstance(block, dict):
        raise HandoffH4Error("b08_crash_windows must be an object")
    if block.get("producer") != "B08":
        raise HandoffH4Error("B08 producer must be recorded")
    window_ids = block.get("window_ids")
    if not isinstance(window_ids, list) or set(window_ids) != set(B08_WINDOW_IDS):
        raise HandoffH4Error(f"B08 windows must be exactly {list(B08_WINDOW_IDS)}")
    if not isinstance(block.get("required_invariants"), list) or not set(B08_INVARIANTS) <= set(
        block["required_invariants"]
    ):
        raise HandoffH4Error("B08 block must list every no-duplicate invariant")
    published = block.get("published")
    if not isinstance(published, bool):
        raise HandoffH4Error("B08 published must be a boolean")
    digest = block.get("digest")
    if published:
        if not DIGEST_PATTERN.match(str(digest)):
            raise HandoffH4Error("a published B08 artifact needs a 64-hex digest")
    elif digest is not None:
        raise HandoffH4Error("a missing B08 artifact must not carry a digest")

    real = block.get("real_evidence")
    if not isinstance(real, dict) or real.get("result") not in ALLOWED_RESULTS:
        raise HandoffH4Error("B08 real_evidence must carry a three-state result")
    if not published:
        if real.get("result") == "PASS":
            raise HandoffH4Error("B08 cannot be PASS without published platform evidence")
        _require_non_empty(real.get("gap"), "B08 real evidence gap")
    elif real.get("result") == "PASS":
        _require_evidence(real.get("evidence"), "B08 real evidence")

    constructed = block.get("constructed_reconciliation")
    if not isinstance(constructed, dict):
        raise HandoffH4Error("B08 constructed_reconciliation must be an object")
    if constructed.get("constructed") is not True or constructed.get("result") != "PASS":
        raise HandoffH4Error("the constructed B08 reconciliation must be labelled and pass")
    if constructed.get("does_not_close_h4") is not True:
        raise HandoffH4Error("constructed reconciliation must never close H4")
    helper = str(constructed.get("helper") or "")
    if "reconcile_b08_windows" not in helper:
        raise HandoffH4Error("constructed reconciliation must name the B08 helper")
    tests = constructed.get("tests")
    if not isinstance(tests, list) or not tests:
        raise HandoffH4Error("constructed reconciliation needs executed test references")
    for reference in tests:
        path = str(reference).split("::", 1)[0]
        if not (REPO / path).is_file():
            raise HandoffH4Error(f"constructed reconciliation names missing test {path!r}")

    mapping = _row_map(doc)
    row_ids = block.get("row_ids")
    if not isinstance(row_ids, list) or set(row_ids) != {
        f"H4-B08-{window_id}" for window_id in B08_WINDOW_IDS
    }:
        raise HandoffH4Error("B08 row_ids must cover the four windows")
    for window_id in B08_WINDOW_IDS:
        row_id = f"H4-B08-{window_id}"
        row = mapping.get(row_id)
        if row is None:
            raise HandoffH4Error(f"missing B08 window row {row_id!r}")
        if row.get("window_id") != window_id:
            raise HandoffH4Error(f"{row_id}: window_id mismatch")
        if not published and row.get("result") != "NOT_VERIFIED":
            raise HandoffH4Error(f"{row_id}: real B08 row must stay NOT_VERIFIED")
        if not published:
            _require_non_empty(row.get("gap"), f"{row_id} gap")
        invariants = row.get("required_invariants")
        if not isinstance(invariants, list) or not set(B08_INVARIANTS) <= set(invariants):
            raise HandoffH4Error(f"{row_id}: missing a required invariant")


def validate_closure(doc: dict[str, Any]) -> None:
    """Reject H4 closure while any required row is unverified or inconclusive."""
    closure = doc.get("h4_closure")
    if not isinstance(closure, dict):
        raise HandoffH4Error("h4_closure must be an object")
    status = closure.get("status")
    closed = closure.get("closed")
    if status not in {"open", "closed"} or not isinstance(closed, bool):
        raise HandoffH4Error("h4_closure needs status open|closed and a boolean closed")
    if (status == "closed") != closed:
        raise HandoffH4Error("h4_closure.status and closed disagree")

    required_ids = closure.get("required_row_ids")
    expected = set(REQUIRED_POSITIVE_ROW_IDS) | {REQUIRED_SEARCH_ROW_ID}
    if not isinstance(required_ids, list) or set(required_ids) != expected:
        raise HandoffH4Error("h4_closure.required_row_ids must be the four positives + search")
    mapping = _row_map(doc)
    missing = [row_id for row_id in required_ids if row_id not in mapping]
    if missing:
        raise HandoffH4Error(f"h4_closure names missing rows {missing!r}")
    open_required = [row_id for row_id in required_ids if mapping[row_id].get("result") != "PASS"]
    recorded_open = closure.get("open_required_row_ids")
    if not isinstance(recorded_open, list) or set(recorded_open) != set(open_required):
        raise HandoffH4Error("open_required_row_ids does not match the row results")

    scope = doc.get("search", {}).get("search_scope", {})
    if closure.get("search_scope") != scope.get("status"):
        raise HandoffH4Error("h4_closure.search_scope must match the search block")

    if not closed:
        if open_required:
            if closure.get("positive_evidence_present") is True:
                raise HandoffH4Error("positive evidence cannot be present while rows are open")
        _require_non_empty(closure.get("reason"), "h4_closure reason")
        return

    if open_required:
        raise HandoffH4Error(
            f"H4 cannot close while required rows are NOT_VERIFIED/inconclusive: {open_required!r}"
        )
    for row_id in required_ids:
        row = mapping[row_id]
        if row.get("scientific_claim_status") != "supported":
            raise HandoffH4Error(f"{row_id}: a closed H4 needs a supported claim")
        if row.get("evidence_kind") != "real" or row.get("real_run") is not True:
            raise HandoffH4Error(f"{row_id}: a closed H4 needs real evidence")
        if row.get("constructed") is not False:
            raise HandoffH4Error(f"{row_id}: a closed H4 cannot rest on constructed evidence")
    if scope.get("status") == "DEFERRED" and not scope.get("owner_ratified"):
        raise HandoffH4Error("a deferral without owner ratification cannot close H4")
    blockers = closure.get("gate_blocking_row_ids")
    if not isinstance(blockers, list) or blockers:
        raise HandoffH4Error("a closed H4 cannot keep gate-blocking rows open")
    if closure.get("positive_evidence_present") is not True:
        raise HandoffH4Error("a closed H4 must record positive evidence")
    if doc.get("real_computation", {}).get("result") != "PASS":
        raise HandoffH4Error("a closed H4 needs a real_computation PASS")


def validate_real_computation(block: object) -> None:
    """Reject a real-computation PASS without a run spec or executed command."""
    if not isinstance(block, dict):
        raise HandoffH4Error("real_computation must be an object")
    result = block.get("result")
    if result not in ALLOWED_RESULTS | {"NOT_RUN"}:
        raise HandoffH4Error(f"real_computation: invalid result {result!r}")
    _require_non_empty(block.get("reason"), "real_computation reason")
    if result == "PASS":
        spec = block.get("run_spec")
        command = block.get("command")
        has_spec = isinstance(spec, str) and bool(spec.strip())
        has_command = isinstance(command, list) and any(
            isinstance(item, str) and item.strip() for item in command
        )
        if not (has_spec or has_command):
            raise HandoffH4Error("real_computation PASS needs a run spec or command")


def validate_handoff_h4(doc: dict[str, Any]) -> None:
    """Reject any structural, enum, digest or honesty defect in H4."""
    for key in REQUIRED_TOP_LEVEL:
        if key not in doc:
            raise HandoffH4Error(f"handoff missing top-level field {key!r}")
    if doc["handoff_id"] != "H4":
        raise HandoffH4Error("handoff_id must be H4")
    if doc["handoff_version"] != 1:
        raise HandoffH4Error("handoff_version must be 1")
    if doc["contract_set"] != "acp2.science":
        raise HandoffH4Error("contract_set must be acp2.science")
    if doc["contract_set_version"] != 1:
        raise HandoffH4Error("contract_set_version must be 1")
    if doc["contract_set_frozen"] is not True:
        raise HandoffH4Error("contract_set_frozen must be true")
    _require_non_empty(doc.get("frozen_at"), "frozen_at")

    basis = doc["contract_version_basis"]
    if not isinstance(basis, dict):
        raise HandoffH4Error("contract_version_basis must be an object")
    if basis.get("handoff_h3_version") != 1:
        raise HandoffH4Error("contract_version_basis must freeze H3 version 1")
    if basis.get("handoff_h3_sha256") != _sha256(H3_PATH):
        raise HandoffH4Error("contract_version_basis.handoff_h3_sha256 must match H3 bytes")

    gate = doc["gate_command"]
    if not isinstance(gate, list) or len(gate) < 3 or gate[1:3] != ["-m", "pytest"]:
        raise HandoffH4Error("gate_command must be an explicit pytest argv")
    if "tests/test_acp2_science_h4_gate.py" not in gate:
        raise HandoffH4Error("gate_command must include this delivered validator")
    for token in gate:
        if isinstance(token, str) and token.startswith("tests/") and not (REPO / token).is_file():
            raise HandoffH4Error(f"gate_command names missing test {token!r}")

    validate_rows(doc)
    validate_negative_checks(doc)
    validate_search(doc)
    validate_b08_windows(doc)
    validate_closure(doc)
    validate_real_computation(doc["real_computation"])

    failures = doc["platform_failure_injections"]
    if not isinstance(failures, list):
        raise HandoffH4Error("platform_failure_injections must be a list")
    case_ids = [entry.get("case_id") if isinstance(entry, dict) else None for entry in failures]
    if set(case_ids) != set(PLATFORM_FAILURE_CASE_IDS) or len(failures) != len(
        PLATFORM_FAILURE_CASE_IDS
    ):
        raise HandoffH4Error("platform_failure_injections must cover the four cases")
    for entry in failures:
        assert isinstance(entry, dict)
        if entry.get("result") not in ALLOWED_RESULTS:
            raise HandoffH4Error(f"{entry.get('case_id')}: invalid result")
        _require_non_empty(entry.get("gap"), f"{entry.get('case_id')} gap")

    external = doc["external_evidence"]
    if not isinstance(external, dict):
        raise HandoffH4Error("external_evidence must be an object")
    for key in ("b20a_exec", "b20a_search", "b08_four_crash_windows", "b13_publication_recovery"):
        block = external.get(key)
        if not isinstance(block, dict) or block.get("result") not in ALLOWED_RESULTS:
            raise HandoffH4Error(f"external_evidence.{key} needs a three-state result")
        if block.get("result") != "PASS":
            _require_non_empty(block.get("reason"), f"external_evidence.{key} reason")

    gap = doc["receipt_protocol_gap"]
    if not isinstance(gap, dict):
        raise HandoffH4Error("receipt_protocol_gap must be an object")
    if gap.get("status") != "NOT_VERIFIED" or gap.get("samples_delivered") is not False:
        raise HandoffH4Error("receipt_protocol_gap must stay NOT_VERIFIED without samples")
    if "acceptance-result.schema.json" not in str(gap.get("missing_artifact", "")):
        raise HandoffH4Error("receipt_protocol_gap must name the missing B01 artifact")

    source = doc["source"]
    if not isinstance(source, dict):
        raise HandoffH4Error("source must be an object")
    for key in ("branch", "pre_delivery_commit", "python", "worktree"):
        _require_non_empty(source.get(key), f"source.{key}")


# ---------------------------------------------------------------------------
# B08 constructed reconciliation
# ---------------------------------------------------------------------------


def _load_harness() -> Any:
    name = "acp2_science_h4_gate_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _constructed_b08_records() -> list[dict[str, Any]]:
    def add(record: dict[str, Any], event: dict[str, Any]) -> None:
        record["events"].append(event)

    windows: list[dict[str, Any]] = []
    reservation: dict[str, Any] = {"window_id": "reservation-before-persist", "events": []}
    add(reservation, {"type": "reservation_created", "claim_id": "c1", "reservation_id": "r1"})
    add(reservation, {"type": "reservation_swept", "claim_id": "c1", "reservation_id": "r1"})
    add(reservation, {"type": "directory_allocated", "claim_id": "c1", "work_dir": "d1"})
    add(reservation, {"type": "directory_removed", "claim_id": "c1", "work_dir": "d1"})
    add(reservation, {"type": "reservation_created", "claim_id": "c1", "reservation_id": "r2"})
    add(reservation, {"type": "directory_allocated", "claim_id": "c1", "work_dir": "d2"})
    add(
        reservation,
        {"type": "job_persisted", "claim_id": "c1", "task_ref": "t1", "work_dir": "d2"},
    )
    add(
        reservation,
        {"type": "dispatch_started", "task_ref": "t1", "transport": "local", "qc_process_id": "q1"},
    )
    add(reservation, {"type": "dispatch_confirmed", "task_ref": "t1"})
    windows.append(reservation)

    persisted: dict[str, Any] = {"window_id": "persisted-before-submission-start", "events": []}
    add(persisted, {"type": "reservation_created", "claim_id": "c2", "reservation_id": "r3"})
    add(persisted, {"type": "directory_allocated", "claim_id": "c2", "work_dir": "d3"})
    add(persisted, {"type": "job_persisted", "claim_id": "c2", "task_ref": "t2", "work_dir": "d3"})
    add(persisted, {"type": "restart"})
    add(
        persisted,
        {"type": "dispatch_started", "task_ref": "t2", "transport": "local", "qc_process_id": "q2"},
    )
    windows.append(persisted)

    lost: dict[str, Any] = {"window_id": "submitted-lost-response", "events": []}
    add(lost, {"type": "reservation_created", "claim_id": "c3", "reservation_id": "r4"})
    add(lost, {"type": "directory_allocated", "claim_id": "c3", "work_dir": "d4"})
    add(lost, {"type": "job_persisted", "claim_id": "c3", "task_ref": "t3", "work_dir": "d4"})
    add(
        lost,
        {"type": "dispatch_started", "task_ref": "t3", "transport": "bsub", "submission_id": "s1"},
    )
    add(lost, {"type": "dispatch_response_lost", "task_ref": "t3", "submission_id": "s1"})
    add(lost, {"type": "submission_reconciled", "task_ref": "t3", "submission_id": "s1"})
    windows.append(lost)

    replay: dict[str, Any] = {
        "window_id": "restart-replay",
        "events": [],
        "reused_results": [{"task_ref": "t4", "identity_valid": True, "facts_complete": True}],
    }
    add(replay, {"type": "reservation_created", "claim_id": "c4", "reservation_id": "r5"})
    add(replay, {"type": "directory_allocated", "claim_id": "c4", "work_dir": "d5"})
    add(replay, {"type": "job_persisted", "claim_id": "c4", "task_ref": "t4", "work_dir": "d5"})
    add(
        replay,
        {"type": "dispatch_started", "task_ref": "t4", "transport": "local", "qc_process_id": "q4"},
    )
    add(replay, {"type": "dispatch_confirmed", "task_ref": "t4"})
    add(replay, {"type": "publication_retry", "task_ref": "t4"})
    add(replay, {"type": "restart"})
    add(replay, {"type": "replay", "claim_id": "c4", "task_ref": "t4"})
    add(
        replay,
        {"type": "result_reused", "task_ref": "t4", "identity_valid": True, "facts_complete": True},
    )
    windows.append(replay)
    return windows


def _window(records: list[dict[str, Any]], window_id: str) -> dict[str, Any]:
    return next(record for record in records if record["window_id"] == window_id)


# ---------------------------------------------------------------------------
# Deliverable structure
# ---------------------------------------------------------------------------


def test_handoff_h4_parses_and_is_byte_deterministic() -> None:
    raw = H4_PATH.read_text(encoding="utf-8")
    doc = load_h4()
    regenerated = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    assert raw == regenerated
    validate_handoff_h4(doc)


def test_handoff_h4_freezes_the_exact_h3_bytes() -> None:
    doc = load_h4()
    basis = doc["contract_version_basis"]
    assert basis["handoff_h3_sha256"] == _sha256(H3_PATH)
    assert basis["handoff_h3_version"] == 1


def test_every_row_has_scenario_result_claim_status_and_allowed_values() -> None:
    doc = load_h4()
    for row in doc["rows"]:
        assert "result" in row
        assert "scientific_claim_status" in row
        assert "row_kind" in row
        assert row["result"] in ALLOWED_RESULTS
        if row["scientific_claim_status"] is not None:
            assert row["scientific_claim_status"] in ALLOWED_CLAIM_STATUSES


def test_required_positive_rows_stay_not_verified_and_h4_stays_open() -> None:
    doc = load_h4()
    mapping = _row_map(doc)
    for row_id in REQUIRED_POSITIVE_ROW_IDS:
        row = mapping[row_id]
        assert row["result"] == "NOT_VERIFIED"
        assert row["scientific_claim_status"] == "not_evaluated"
        assert row["real_run"] is False
        assert "B20a-exec" in row["gap"]
    assert doc["h4_closure"]["closed"] is False
    assert doc["h4_closure"]["status"] == "open"
    assert doc["h4_closure"]["positive_evidence_present"] is False


def test_negative_rows_are_honest_passes_that_never_replace_positives() -> None:
    doc = load_h4()
    rows = [row for row in doc["rows"] if row["row_kind"] == "negative-honesty"]
    assert {row["case_id"] for row in rows} == set(REQUIRED_NEGATIVE_CASE_IDS)
    for row in rows:
        assert row["result"] == "PASS"
        assert row["replaces_positive_requirement"] is False
        assert row["required_for_h4_closure"] is False
        assert row["observed_outcome"] != "supported"


def test_search_row_is_separate_and_cites_b20a_search_without_a_digest() -> None:
    doc = load_h4()
    search = doc["search"]
    assert search["producer"] == "B20a-search"
    assert search["artifact_id"] == "B20a-search:search-run-evidence"
    assert search["readiness"] == "contract_ready"
    assert search["digest"] is None
    assert search["published"] is False
    assert search["separate_from_known_candidate_chain"] is True
    assert doc["known_candidate_chain"]["producer"] == "B20a-exec"
    assert "H4-search" not in doc["known_candidate_chain"]["row_ids"]
    assert search["result"] == _row(doc, "H4-search")["result"]
    assert search["scientific_claim_status"] == _row(doc, "H4-search")["scientific_claim_status"]
    assert search["contract_sha256"] == _sha256(SCIENCE / "requests" / "search-contract.md")


def test_search_scope_is_in_scope_not_deferred_and_unratified() -> None:
    doc = load_h4()
    scope = doc["search"]["search_scope"]
    assert scope["status"] == "IN_SCOPE"
    assert scope["deferred"] is False
    assert scope["owner_ratified"] is False
    assert scope["ratification_ref"] is None
    assert doc["h4_closure"]["search_scope"] == "IN_SCOPE"


def test_b08_block_and_rows_are_not_verified_with_exact_gaps() -> None:
    doc = load_h4()
    block = doc["b08_crash_windows"]
    assert block["producer"] == "B08"
    assert block["artifact_id"] == "B08:four-crash-window-evidence"
    assert block["digest"] is None
    assert block["published"] is False
    assert block["real_evidence"]["result"] == "NOT_VERIFIED"
    assert set(block["window_ids"]) == set(B08_WINDOW_IDS)
    mapping = _row_map(doc)
    for window_id in B08_WINDOW_IDS:
        row = mapping[f"H4-B08-{window_id}"]
        assert row["result"] == "NOT_VERIFIED"
        assert row["gap"]
        assert set(B08_INVARIANTS) <= set(row["required_invariants"])


def test_platform_failure_rows_are_present_and_not_verified() -> None:
    doc = load_h4()
    cases = {entry["case_id"]: entry for entry in doc["platform_failure_injections"]}
    assert set(cases) == set(PLATFORM_FAILURE_CASE_IDS)
    for entry in cases.values():
        assert entry["result"] == "NOT_VERIFIED"
        assert entry["gap"]


def test_no_run_identity_or_digest_is_invented() -> None:
    doc = load_h4()

    def walk(value: Any, where: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in FORBIDDEN_IDENTITY_KEYS:
                    assert item is None, f"{where}.{key} must be null"
                walk(item, f"{where}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{where}[{index}]")

    walk(doc, "h4")
    for key in ("b20a_exec", "b20a_search", "b08_four_crash_windows", "b13_publication_recovery"):
        assert doc["external_evidence"][key]["digest"] is None


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------


def test_required_positive_pass_without_real_evidence_is_rejected() -> None:
    doc = load_h4()
    row = _row(doc, "H4-01-valid-ts")
    row["result"] = "PASS"
    with pytest.raises(HandoffH4Error, match="evidence_kind='real'"):
        validate_handoff_h4(doc)


def test_required_positive_pass_with_partial_real_fields_is_rejected() -> None:
    doc = load_h4()
    row = _row(doc, "H4-01-valid-ts")
    row["result"] = "PASS"
    row["evidence_kind"] = "real"
    row["real_run"] = True
    row["constructed"] = False
    row["scientific_claim_status"] = "supported"
    row["gap"] = None
    with pytest.raises(HandoffH4Error, match="real_evidence"):
        validate_handoff_h4(doc)


def test_required_positive_pass_with_a_fabricated_digest_is_rejected() -> None:
    doc = load_h4()
    row = _row(doc, "H4-01-valid-ts")
    row["result"] = "PASS"
    row["evidence_kind"] = "real"
    row["real_run"] = True
    row["constructed"] = False
    row["scientific_claim_status"] = "supported"
    row["gap"] = None
    row["real_evidence"] = {
        "producer": "B20a-exec",
        "artifact_id": "B20a-exec:da-execution-artifacts",
        "run_key": "fk-001",
        "authorized": True,
        "digest": "a" * 64,
    }
    with pytest.raises(HandoffH4Error, match="not published"):
        validate_handoff_h4(doc)


def test_search_row_merged_into_known_candidate_chain_is_rejected() -> None:
    doc = load_h4()
    doc["known_candidate_chain"]["row_ids"].append("H4-search")
    with pytest.raises(HandoffH4Error, match="must not be a known-candidate row"):
        validate_handoff_h4(doc)


def test_search_pass_from_control_loop_is_rejected() -> None:
    doc = load_h4()
    search = doc["search"]
    search["result"] = "PASS"
    search["scientific_claim_status"] = "supported"
    search["source_kind"] = "control-loop"
    row = _row(doc, "H4-search")
    row["result"] = "PASS"
    row["evidence_kind"] = "real"
    row["real_run"] = True
    row["constructed"] = False
    row["scientific_claim_status"] = "supported"
    row["gap"] = None
    with pytest.raises(HandoffH4Error, match="control-loop/known-candidate"):
        validate_handoff_h4(doc)


def test_unratified_deferral_is_rejected() -> None:
    doc = load_h4()
    scope = doc["search"]["search_scope"]
    scope["status"] = "DEFERRED"
    scope["deferred"] = True
    with pytest.raises(HandoffH4Error, match="owner ratification"):
        validate_handoff_h4(doc)


def test_ratified_deferral_is_accepted_but_cannot_close_h4() -> None:
    doc = load_h4()
    scope = doc["search"]["search_scope"]
    scope.update(
        {
            "status": "DEFERRED",
            "deferred": True,
            "deferral_requested": True,
            "owner_ratified": True,
            "ratification_ref": "owner-ratification/ADR-008/search-scope",
            "synchronized_records": True,
        }
    )
    doc["h4_closure"]["search_scope"] = "DEFERRED"
    validate_handoff_h4(doc)
    doc["h4_closure"]["closed"] = True
    doc["h4_closure"]["status"] = "closed"
    with pytest.raises(HandoffH4Error, match="H4 cannot close"):
        validate_handoff_h4(doc)


def test_h4_closure_with_unverified_required_rows_is_rejected() -> None:
    doc = load_h4()
    doc["h4_closure"]["closed"] = True
    doc["h4_closure"]["status"] = "closed"
    doc["h4_closure"]["positive_evidence_present"] = True
    with pytest.raises(HandoffH4Error, match="H4 cannot close"):
        validate_handoff_h4(doc)


def test_missing_b08_window_row_is_rejected() -> None:
    doc = load_h4()
    doc["rows"] = [row for row in doc["rows"] if row.get("row_id") != "H4-B08-restart-replay"]
    with pytest.raises(HandoffH4Error, match="B08 window rows must be exactly"):
        validate_handoff_h4(doc)


def test_missing_b08_block_window_is_rejected() -> None:
    doc = load_h4()
    doc["b08_crash_windows"]["window_ids"] = list(B08_WINDOW_IDS[:-1])
    with pytest.raises(HandoffH4Error, match="B08 windows must be exactly"):
        validate_handoff_h4(doc)


def test_b08_pass_without_platform_evidence_is_rejected() -> None:
    doc = load_h4()
    doc["b08_crash_windows"]["real_evidence"]["result"] = "PASS"
    with pytest.raises(HandoffH4Error, match="without published platform evidence"):
        validate_handoff_h4(doc)


def test_unknown_row_result_is_rejected() -> None:
    doc = load_h4()
    _row(doc, "H4-01-valid-ts")["result"] = "DEFERRED"
    with pytest.raises(HandoffH4Error, match="not allowed"):
        validate_handoff_h4(doc)


def test_gate_command_names_only_existing_tests() -> None:
    doc = load_h4()
    for token in doc["gate_command"]:
        if isinstance(token, str) and token.startswith("tests/"):
            assert (REPO / token).is_file(), token


# ---------------------------------------------------------------------------
# Constructed B08 reconciliation
# ---------------------------------------------------------------------------


def test_constructed_b08_four_windows_reconcile_without_violation() -> None:
    harness = _load_harness()
    report = harness.reconcile_b08_windows(_constructed_b08_records())
    assert report["ok"] is True
    assert report["missing_windows"] == []
    assert [window["window_id"] for window in report["windows"]] == list(B08_WINDOW_IDS)
    assert all(window["ok"] for window in report["windows"])
    assert set(report["invariants"]) == set(B08_INVARIANTS)


def test_duplicate_reservation_is_reported() -> None:
    harness = _load_harness()
    records = _constructed_b08_records()
    record = _window(records, "reservation-before-persist")
    record["events"].insert(
        2,
        {"type": "reservation_created", "claim_id": "c1", "reservation_id": "r9"},
    )
    report = harness.reconcile_b08_windows(records)
    assert report["ok"] is False
    assert any("second live reservation" in item for item in report["violations"])


def test_duplicate_directory_is_reported() -> None:
    harness = _load_harness()
    records = _constructed_b08_records()
    record = _window(records, "reservation-before-persist")
    record["events"].insert(
        5,
        {"type": "directory_allocated", "claim_id": "c1", "work_dir": "d9"},
    )
    report = harness.reconcile_b08_windows(records)
    assert report["ok"] is False
    assert any("second live work directory" in item for item in report["violations"])


def test_duplicate_bsub_and_qc_are_reported() -> None:
    harness = _load_harness()
    records = _constructed_b08_records()
    lost = _window(records, "submitted-lost-response")
    lost["events"].append(
        {"type": "dispatch_started", "task_ref": "t3", "transport": "bsub", "submission_id": "s2"}
    )
    replay = _window(records, "restart-replay")
    replay["events"].append(
        {"type": "dispatch_started", "task_ref": "t4", "transport": "local", "qc_process_id": "q5"}
    )
    report = harness.reconcile_b08_windows(records)
    assert report["ok"] is False
    assert any("bsub dispatch" in item for item in report["violations"])
    assert any("local dispatch" in item for item in report["violations"])


def test_permanently_undispatched_accepted_task_is_reported() -> None:
    harness = _load_harness()
    records = _constructed_b08_records()
    persisted = _window(records, "persisted-before-submission-start")
    persisted["events"] = [
        event for event in persisted["events"] if event["type"] != "dispatch_started"
    ]
    report = harness.reconcile_b08_windows(records)
    assert report["ok"] is False
    assert any("permanently" in item.lower() or "t2" in item for item in report["violations"])


def test_identity_changed_reuse_is_reported() -> None:
    harness = _load_harness()
    records = _constructed_b08_records()
    replay = _window(records, "restart-replay")
    replay["reused_results"] = [{"task_ref": "t4", "identity_valid": False, "facts_complete": True}]
    report = harness.reconcile_b08_windows(records)
    assert report["ok"] is False
    assert any("valid identity" in item for item in report["violations"])


def test_publication_retry_that_relaunches_qc_is_reported() -> None:
    harness = _load_harness()
    records = _constructed_b08_records()
    replay = _window(records, "restart-replay")
    replay["events"].append(
        {"type": "dispatch_started", "task_ref": "t4", "transport": "local", "qc_process_id": "q6"}
    )
    report = harness.reconcile_b08_windows(records)
    assert report["ok"] is False
    assert any("publication retry" in item for item in report["violations"])


def test_lost_response_without_reconciliation_is_reported() -> None:
    harness = _load_harness()
    records = _constructed_b08_records()
    lost = _window(records, "submitted-lost-response")
    lost["events"] = [event for event in lost["events"] if event["type"] != "submission_reconciled"]
    report = harness.reconcile_b08_windows(records)
    assert report["ok"] is False
    assert any("was not reconciled" in item for item in report["violations"])


def test_replay_returning_a_second_task_is_reported() -> None:
    harness = _load_harness()
    records = _constructed_b08_records()
    replay = _window(records, "restart-replay")
    replay["events"].append({"type": "replay", "claim_id": "c4", "task_ref": "t9"})
    report = harness.reconcile_b08_windows(records)
    assert report["ok"] is False
    assert any("committed task is t4" in item for item in report["violations"])


def test_missing_window_is_reported() -> None:
    harness = _load_harness()
    records = _constructed_b08_records()[:3]
    report = harness.reconcile_b08_windows(records)
    assert report["ok"] is False
    assert report["missing_windows"] == ["restart-replay"]
