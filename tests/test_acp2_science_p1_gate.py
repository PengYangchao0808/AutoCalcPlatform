"""A-13: validator for the ACP2 P1 same-task science gate (handoff H3).

The gate ledger is a deterministic JSON record.  This module separates three
records that must never be collapsed: a scenario *result*, a scientific claim
*status* and the H3 *closure* ledger.  It enforces the honesty rules that JSON
alone cannot express:

* the CLI, HTTP/Workbench and MCP/DSH entries reference exactly one first-submit
  task id and attempt and agree on result, product schema, manifest generation,
  manifest digest and scientific validation;
* every ``(entry, environment)`` combination is present, uses only
  ``PASS``/``FAIL``/``NOT_VERIFIED`` and a ``PASS`` carries executed evidence;
* the three negative rows (completed-but-missing-energy, old attempt, old
  generation) are actually rejected;
* the active-workflow contract still names the frozen fifteen workflows and
  cites its guard tests.

Standard-library JSON parsing only.  The same validator is exercised with
tampered copies so a fresh CLI submit that yields a second task id cannot be
recorded as the same task.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
SCIENCE = REPO / "docs" / "reference" / "next-change" / "acp2-contracts" / "science"
H3_PATH = SCIENCE / "handoff-H3.json"
H2_PATH = SCIENCE / "handoff-H2.json"

ALLOWED_STATUS = frozenset({"PASS", "FAIL", "NOT_VERIFIED"})
ENTRY_IDS = ("cli", "http_workbench", "mcp_dsh")
ENVIRONMENTS = ("local", "lsf")
SHARED_KEYS = (
    "result",
    "product_schema",
    "manifest_generation",
    "manifest_digest",
    "scientific_validation",
)
NEGATIVE_CASE_IDS = frozenset({"completed-but-missing-energy", "old-attempt", "old-generation"})
ACTIVE_WORKFLOW_IDS = (
    "singlepoint",
    "optimize",
    "frequency",
    "scan",
    "irc",
    "tsmode",
    "casscf",
    "xtb_optimize",
    "nmr",
    "Confsearch",
    "PESsearch",
    "BatchOptimize",
    "XtbPathSearch",
    "OrcaGradient",
    "XtbConstrainedOpt",
)
ACTIVE_WORKFLOW_GUARDS = frozenset(
    {
        "tests/test_architecture_invariants.py::test_current_active_workflow_ids_are_exact_and_ordered",
        "tests/test_acp2_science_handoff.py::test_task_compatibility_matrix_has_fifteen_rows",
    }
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
    "same_task_matrix",
    "combinations",
    "negative_checks",
    "active_workflow_contract",
    "real_computation",
    "receipt_protocol_gap",
    "source",
)


class HandoffH3Error(ValueError):
    """Raised when the H3 ledger records an unsupported or dishonest claim."""


def load_h3(path: Path = H3_PATH) -> dict[str, Any]:
    """Parse the gate ledger with stdlib JSON only."""
    with path.open(encoding="utf-8") as handle:
        doc: dict[str, Any] = json.load(handle)
    if not isinstance(doc, dict):
        raise HandoffH3Error("handoff root must be a JSON object")
    return doc


def _require_non_empty(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HandoffH3Error(f"{where}: required non-empty text")
    return value


def _require_evidence(evidence: object, where: str) -> None:
    if not isinstance(evidence, dict):
        raise HandoffH3Error(f"{where}: evidence must be an object")
    executed = False
    for key in ("commands", "tests"):
        values = evidence.get(key)
        if isinstance(values, list) and any(
            isinstance(item, str) and item.strip() for item in values
        ):
            executed = True
    if not executed:
        raise HandoffH3Error(f"{where}: no executed command or test reference")


def _validate_first_submit(first: object) -> None:
    if not isinstance(first, dict):
        raise HandoffH3Error("same_task_matrix.first_submit must be an object")
    for key in ("task_id", "attempt", *SHARED_KEYS):
        if key not in first:
            raise HandoffH3Error(f"first_submit: missing field {key!r}")
    task_id = first["task_id"]
    attempt = first["attempt"]
    if task_id is None:
        if attempt is not None:
            raise HandoffH3Error("first_submit: attempt must be null without a task id")
        _require_non_empty(first.get("gap"), "first_submit gap")
        if any(first.get(key) is not None for key in SHARED_KEYS):
            raise HandoffH3Error(
                "first_submit: no shared result/schema/generation/digest may exist "
                "without a platform first submit"
            )
        return
    _require_non_empty(task_id, "first_submit task_id")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise HandoffH3Error("first_submit: a real task needs a positive integer attempt")
    for key in SHARED_KEYS:
        if first.get(key) is None:
            raise HandoffH3Error(f"first_submit: a real first submit must state its shared {key}")


def validate_same_task_matrix(matrix: object) -> None:
    """Reject a matrix that does not resolve to one first-submit task+attempt."""
    if not isinstance(matrix, dict):
        raise HandoffH3Error("same_task_matrix must be an object")
    entries = matrix.get("entries")
    if not isinstance(entries, list):
        raise HandoffH3Error("same_task_matrix.entries must be a list")
    ids = [entry.get("entry") if isinstance(entry, dict) else None for entry in entries]
    if len(entries) != len(ENTRY_IDS) or set(ids) != set(ENTRY_IDS):
        raise HandoffH3Error(
            f"same_task_matrix must contain exactly {list(ENTRY_IDS)}, got {ids!r}"
        )
    first = matrix.get("first_submit")
    shared = matrix.get("shared")
    if not isinstance(shared, dict):
        raise HandoffH3Error("same_task_matrix.shared must be an object")
    _validate_first_submit(first)
    assert isinstance(first, dict)  # narrowed by _validate_first_submit
    for key in SHARED_KEYS:
        if key not in shared:
            raise HandoffH3Error(f"same_task_matrix.shared: missing field {key!r}")
        if shared[key] != first.get(key):
            raise HandoffH3Error(f"same_task_matrix.shared.{key} differs from the first submit")
    if first["task_id"] is None:
        _require_non_empty(shared.get("gap"), "same_task_matrix.shared gap")
    for entry in entries:
        assert isinstance(entry, dict)
        entry_id = entry["entry"]
        if entry.get("first_submit_ref") != "first-submit":
            raise HandoffH3Error(f"{entry_id}: must reference the one first-submit record")
        for key in ("task_id", "attempt"):
            if entry.get(key) != first[key]:
                raise HandoffH3Error(
                    f"{entry_id}: {key} must use the first-submit {key} "
                    f"({first[key]!r}), got {entry.get(key)!r}"
                )
        for key in SHARED_KEYS:
            if entry.get(key) != shared[key]:
                raise HandoffH3Error(
                    f"{entry_id}: {key} differs from the shared first-submit value"
                )
        if first["task_id"] is None:
            _require_non_empty(entry.get("gap"), f"{entry_id} gap")


def validate_combinations(doc: dict[str, Any]) -> None:
    """Require every matrix cell and forbid an unevidenced PASS."""
    combinations = doc.get("combinations")
    if not isinstance(combinations, list):
        raise HandoffH3Error("combinations must be a list")
    seen: list[tuple[object, object]] = []
    for combination in combinations:
        if not isinstance(combination, dict):
            raise HandoffH3Error("every combination must be an object")
        entry = combination.get("entry")
        environment = combination.get("environment")
        where = f"combination {entry}/{environment}"
        if entry not in ENTRY_IDS:
            raise HandoffH3Error(f"{where}: unknown entry")
        if environment not in ENVIRONMENTS:
            raise HandoffH3Error(f"{where}: unknown environment")
        seen.append((entry, environment))
        status = combination.get("status")
        if status not in ALLOWED_STATUS:
            raise HandoffH3Error(f"{where}: status {status!r} not in {sorted(ALLOWED_STATUS)}")
        task_id = combination.get("task_id")
        attempt = combination.get("attempt")
        if task_id != doc["same_task_matrix"]["first_submit"]["task_id"]:
            raise HandoffH3Error(f"{where}: task_id must equal the first-submit task id")
        if attempt != doc["same_task_matrix"]["first_submit"]["attempt"]:
            raise HandoffH3Error(f"{where}: attempt must equal the first-submit attempt")
        if status == "PASS":
            if combination.get("executed") is not True:
                raise HandoffH3Error(f"{where}: PASS without executed=true")
            _require_evidence(combination.get("evidence"), f"{where} PASS")
            if task_id is None:
                raise HandoffH3Error(f"{where}: PASS requires a real task id")
            if str(combination.get("gap", "")).strip():
                raise HandoffH3Error(f"{where}: PASS must not carry a gap")
        elif status == "FAIL":
            _require_evidence(combination.get("evidence"), f"{where} FAIL")
            _require_non_empty(combination.get("gap"), f"{where} failure")
        else:
            _require_non_empty(combination.get("gap"), f"{where} gap")
    expected = {(entry, environment) for entry in ENTRY_IDS for environment in ENVIRONMENTS}
    if len(seen) != len(expected) or set(seen) != expected:
        missing = sorted(expected - set(seen))
        raise HandoffH3Error(f"combinations: missing or duplicate cells {missing!r}")


def validate_negative_checks(doc: dict[str, Any]) -> None:
    """Require the three negative cases to be actually rejected."""
    rows = doc.get("negative_checks")
    if not isinstance(rows, list):
        raise HandoffH3Error("negative_checks must be a list")
    case_ids = [row.get("case_id") if isinstance(row, dict) else None for row in rows]
    if set(case_ids) != set(NEGATIVE_CASE_IDS) or len(rows) != len(NEGATIVE_CASE_IDS):
        raise HandoffH3Error(f"negative_checks must contain exactly {sorted(NEGATIVE_CASE_IDS)}")
    for row in rows:
        assert isinstance(row, dict)
        case_id = row["case_id"]
        if row.get("rejected") is not True:
            raise HandoffH3Error(f"{case_id}: negative case is not actually rejected")
        if row.get("expected") != "reject":
            raise HandoffH3Error(f"{case_id}: expected must be 'reject'")
        _require_non_empty(row.get("rejection_rule"), f"{case_id} rejection_rule")
        if row.get("live_result") not in ALLOWED_STATUS:
            raise HandoffH3Error(f"{case_id}: invalid live_result")
        _require_evidence(row.get("evidence"), f"{case_id} evidence")


def validate_active_workflow_contract(doc: dict[str, Any]) -> None:
    """Require the frozen fifteen-workflow inventory and its guard evidence."""
    block = doc.get("active_workflow_contract")
    if not isinstance(block, dict):
        raise HandoffH3Error("active_workflow_contract must be an object")
    if block.get("intact") is not True:
        raise HandoffH3Error("active_workflow_contract.intact must be true")
    ids = block.get("active_workflow_ids")
    if not isinstance(ids, list) or tuple(ids) != ACTIVE_WORKFLOW_IDS:
        raise HandoffH3Error(
            "active_workflow_contract must list the frozen fifteen workflows in order"
        )
    if block.get("expected_active_count") != 15 or len(ids) != 15:
        raise HandoffH3Error("active_workflow_contract must record the count 15")
    guard = block.get("guard_evidence")
    if not isinstance(guard, dict):
        raise HandoffH3Error("active_workflow_contract.guard_evidence must be an object")
    tests = guard.get("tests")
    if not isinstance(tests, list) or not ACTIVE_WORKFLOW_GUARDS <= set(tests):
        raise HandoffH3Error("active_workflow_contract must cite both workflow guard tests")
    for reference in tests:
        path = str(reference).split("::", 1)[0]
        if not (REPO / path).is_file():
            raise HandoffH3Error(f"active_workflow_contract names missing test {path!r}")
    _require_non_empty(guard.get("summary"), "active_workflow_contract summary")


def validate_real_computation(block: object) -> None:
    """Reject a real-computation PASS that has no run spec or command."""
    if not isinstance(block, dict):
        raise HandoffH3Error("real_computation must be an object")
    result = block.get("result")
    if result not in ALLOWED_STATUS | {"NOT_RUN"}:
        raise HandoffH3Error(f"real_computation: invalid result {result!r}")
    _require_non_empty(block.get("reason"), "real_computation reason")
    if result == "PASS":
        spec = block.get("run_spec")
        command = block.get("command")
        has_spec = isinstance(spec, str) and bool(spec.strip())
        has_command = isinstance(command, list) and any(
            isinstance(item, str) and item.strip() for item in command
        )
        if not (has_spec or has_command):
            raise HandoffH3Error("real_computation PASS requires a run spec or an executed command")


def validate_handoff_h3(doc: dict[str, Any]) -> None:
    """Reject any structural, enum, digest or honesty defect in H3."""
    for key in REQUIRED_TOP_LEVEL:
        if key not in doc:
            raise HandoffH3Error(f"handoff missing top-level field {key!r}")
    if doc["handoff_id"] != "H3":
        raise HandoffH3Error("handoff_id must be H3")
    if doc["handoff_version"] != 1:
        raise HandoffH3Error("handoff_version must be 1")
    if doc["contract_set"] != "acp2.science":
        raise HandoffH3Error("contract_set must be acp2.science")
    if doc["contract_set_version"] != 1:
        raise HandoffH3Error("contract_set_version must be 1")
    if doc["contract_set_frozen"] is not True:
        raise HandoffH3Error("contract_set_frozen must be true")
    _require_non_empty(doc.get("frozen_at"), "frozen_at")

    basis = doc["contract_version_basis"]
    if not isinstance(basis, dict):
        raise HandoffH3Error("contract_version_basis must be an object")
    if basis.get("handoff_h2_version") != 1:
        raise HandoffH3Error("contract_version_basis must freeze H2 version 1")
    expected_digest = hashlib.sha256(H2_PATH.read_bytes()).hexdigest()
    if basis.get("handoff_h2_sha256") != expected_digest:
        raise HandoffH3Error(
            "contract_version_basis.handoff_h2_sha256 must match the exact H2 bytes"
        )

    gate = doc["gate_command"]
    if not isinstance(gate, list) or len(gate) < 3 or gate[1:3] != ["-m", "pytest"]:
        raise HandoffH3Error("gate_command must be an explicit pytest argv")
    if "tests/test_acp2_science_p1_gate.py" not in gate:
        raise HandoffH3Error("gate_command must include this delivered validator")
    for token in gate:
        if isinstance(token, str) and token.startswith("tests/") and not (REPO / token).is_file():
            raise HandoffH3Error(f"gate_command names missing test {token!r}")

    validate_same_task_matrix(doc["same_task_matrix"])
    validate_combinations(doc)
    validate_negative_checks(doc)
    validate_active_workflow_contract(doc)
    validate_real_computation(doc["real_computation"])

    gap = doc["receipt_protocol_gap"]
    if not isinstance(gap, dict):
        raise HandoffH3Error("receipt_protocol_gap must be an object")
    if gap.get("status") != "NOT_VERIFIED" or gap.get("samples_delivered") is not False:
        raise HandoffH3Error("receipt_protocol_gap must stay NOT_VERIFIED without samples")
    if "acceptance-result.schema.json" not in str(gap.get("missing_artifact", "")):
        raise HandoffH3Error("receipt_protocol_gap must name the missing B01 artifact")

    source = doc["source"]
    if not isinstance(source, dict):
        raise HandoffH3Error("source must be an object")
    for key in ("branch", "pre_delivery_commit", "python", "worktree"):
        _require_non_empty(source.get(key), f"source.{key}")


def reject_completed_missing_energy(row: dict[str, Any]) -> None:
    """Reject a COMPLETED row that carries no parsed energy with a unit."""
    if row.get("task_status") == "COMPLETED" and row.get("energy") is None:
        raise HandoffH3Error(
            "COMPLETED task without a parsed energy/unit is rejected as science evidence"
        )
    if row.get("energy") is not None and not row.get("energy_unit"):
        raise HandoffH3Error("an energy without an explicit unit is rejected")


def reject_non_first_attempt(row: dict[str, Any], *, first_attempt: int) -> None:
    """Reject an observation taken from an attempt other than the first submit."""
    if row.get("attempt") != first_attempt:
        raise HandoffH3Error(
            f"attempt {row.get('attempt')!r} is not the first-submit attempt {first_attempt!r}"
        )


def reject_non_shared_generation(row: dict[str, Any], *, shared_generation: str) -> None:
    """Reject an observation from a manifest generation other than the shared one."""
    if row.get("manifest_generation") != shared_generation:
        raise HandoffH3Error(
            f"generation {row.get('manifest_generation')!r} is not the shared "
            f"generation {shared_generation!r}"
        )


# ---------------------------------------------------------------------------
# Deliverable structure
# ---------------------------------------------------------------------------


def test_handoff_h3_parses_and_is_deterministic() -> None:
    raw = H3_PATH.read_text(encoding="utf-8")
    doc = load_h3()
    regenerated = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    assert regenerated == raw, "handoff must be byte-identical with sorted keys and a newline"


def test_handoff_h3_structure_and_status_enum() -> None:
    doc = load_h3()
    validate_handoff_h3(doc)
    for row in doc["negative_checks"]:
        assert row["live_result"] in ALLOWED_STATUS


def test_same_task_entries_share_one_first_submit_reference() -> None:
    matrix = load_h3()["same_task_matrix"]
    validate_same_task_matrix(matrix)
    first = matrix["first_submit"]
    assert first["task_id"] is None
    assert first["attempt"] is None
    assert first["gap"].strip()
    for entry in matrix["entries"]:
        assert entry["task_id"] == first["task_id"]
        assert entry["attempt"] == first["attempt"]
        for key in SHARED_KEYS:
            assert entry[key] == matrix["shared"][key]


def test_every_combination_is_present_with_an_allowed_status() -> None:
    doc = load_h3()
    validate_combinations(doc)
    seen = {(row["entry"], row["environment"]) for row in doc["combinations"]}
    assert seen == {(entry, environment) for entry in ENTRY_IDS for environment in ENVIRONMENTS}
    for row in doc["combinations"]:
        assert row["status"] in ALLOWED_STATUS
        assert row["status"] != "PASS", "no unexecuted combination may be PASS"
        assert row["gap"].strip()


def test_missing_combination_is_rejected() -> None:
    doc = copy.deepcopy(load_h3())
    doc["combinations"] = doc["combinations"][:-1]
    with pytest.raises(HandoffH3Error, match="missing or duplicate cells"):
        validate_combinations(doc)


def test_pass_combination_without_evidence_is_rejected() -> None:
    doc = copy.deepcopy(load_h3())
    row = doc["combinations"][0]
    row["status"] = "PASS"
    row["executed"] = True
    row["gap"] = ""
    row["evidence"] = {"artifacts": [], "commands": [], "summary": "", "tests": []}
    with pytest.raises(HandoffH3Error, match="no executed command or test reference"):
        validate_combinations(doc)


def test_pass_combination_without_execution_flag_is_rejected() -> None:
    doc = copy.deepcopy(load_h3())
    row = doc["combinations"][0]
    row["status"] = "PASS"
    row["executed"] = False
    with pytest.raises(HandoffH3Error, match="PASS without executed=true"):
        validate_combinations(doc)


def test_unknown_combination_status_is_rejected() -> None:
    doc = copy.deepcopy(load_h3())
    doc["combinations"][0]["status"] = "VERIFIED"
    with pytest.raises(HandoffH3Error, match="status 'VERIFIED'"):
        validate_combinations(doc)


def test_negative_row_that_is_not_rejected_is_rejected() -> None:
    doc = copy.deepcopy(load_h3())
    doc["negative_checks"][0]["rejected"] = False
    with pytest.raises(HandoffH3Error, match="not actually rejected"):
        validate_negative_checks(doc)


def test_active_workflow_contract_preserves_fifteen_workflows() -> None:
    doc = load_h3()
    validate_active_workflow_contract(doc)
    block = doc["active_workflow_contract"]
    assert tuple(block["active_workflow_ids"]) == ACTIVE_WORKFLOW_IDS
    assert block["expected_active_count"] == len(ACTIVE_WORKFLOW_IDS) == 15
    assert ACTIVE_WORKFLOW_GUARDS <= set(block["guard_evidence"]["tests"])


def test_active_workflow_contract_rejects_a_short_inventory() -> None:
    doc = copy.deepcopy(load_h3())
    doc["active_workflow_contract"]["active_workflow_ids"] = list(ACTIVE_WORKFLOW_IDS[:-1])
    with pytest.raises(HandoffH3Error, match="frozen fifteen workflows"):
        validate_active_workflow_contract(doc)


# ---------------------------------------------------------------------------
# Negative QA: the three negative rows and the fresh-submit case must fail
# ---------------------------------------------------------------------------


def test_completed_but_missing_energy_is_rejected() -> None:
    row = {"task_status": "COMPLETED", "energy": None, "energy_unit": None}
    with pytest.raises(HandoffH3Error, match="without a parsed energy"):
        reject_completed_missing_energy(row)


def test_old_attempt_is_rejected() -> None:
    with pytest.raises(HandoffH3Error, match="not the first-submit attempt"):
        reject_non_first_attempt({"attempt": 2}, first_attempt=1)


def test_old_generation_is_rejected() -> None:
    with pytest.raises(HandoffH3Error, match="not the shared generation"):
        reject_non_shared_generation(
            {"manifest_generation": "generation-0001"},
            shared_generation="generation-0002",
        )


def test_fresh_cli_submit_task_id_is_rejected() -> None:
    """A second task id from a fresh CLI submit cannot claim sameness."""
    matrix = copy.deepcopy(load_h3()["same_task_matrix"])
    matrix["entries"][0]["task_id"] = "fresh-cli-task-0002"
    matrix["entries"][0]["attempt"] = 1
    with pytest.raises(HandoffH3Error, match="first-submit task_id"):
        validate_same_task_matrix(matrix)


def test_consistent_second_task_id_is_still_rejected() -> None:
    """Even a fresh submit observed by all three entries is not the first submit."""
    matrix = copy.deepcopy(load_h3()["same_task_matrix"])
    for row in matrix["entries"]:
        row["task_id"] = "fresh-cli-task-0002"
        row["attempt"] = 1
        row["gap"] = ""
    with pytest.raises(HandoffH3Error, match="first-submit task_id"):
        validate_same_task_matrix(matrix)


def test_entry_with_a_different_manifest_digest_is_rejected() -> None:
    matrix = copy.deepcopy(load_h3()["same_task_matrix"])
    matrix["entries"][1]["manifest_digest"] = "sha256:" + "0" * 64
    with pytest.raises(HandoffH3Error, match="manifest_digest differs"):
        validate_same_task_matrix(matrix)
