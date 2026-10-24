"""Validator for the ACP2 P0 science handoff (A-08).

The handoff is a deterministic JSON record.  This module validates its shape and
the honesty rules that cannot be expressed by JSON alone:

* every required P0 row is present and uses only ``PASS``/``FAIL``/``NOT_VERIFIED``;
* every ``PASS`` row carries a non-empty summary and at least one executed
  command or test reference;
* ``real_computation`` is a separate block and a ``PASS`` without a run spec or
  command is rejected (negative QA);
* the external-evidence block separates B01/B05 gaps from server, client and
  harness revisions, which stay null with an exact gap while unpublished;
* the unsupported list names every family that is not in the built-in catalog;
* the existing cross-track ledger still has no missing id and no cycle.

Standard-library JSON parsing only.  The inventory validator is reused by
import, so the ledger checks cannot drift from their original definition.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from cccp.calculation.capabilities import (
    BUILTIN_CAPABILITY_IDS,
    BUILTIN_FAMILIES,
    UNSUPPORTED_FAMILIES,
)
from cccp.calculation.requests import TaskKind
from tests.test_acp2_science_inventory import find_cycles, load_ledger, validate_ledger

REPO = Path(__file__).resolve().parents[1]
SCIENCE = REPO / "docs" / "reference" / "next-change" / "acp2-contracts" / "science"
HANDOFF_PATH = SCIENCE / "handoff-H2.json"
H1_PATH = SCIENCE / "handoff-H1.json"
MATRIX_PATH = SCIENCE / "task-compatibility-matrix.md"

ALLOWED_ROW_RESULTS = frozenset({"PASS", "FAIL", "NOT_VERIFIED"})
REQUIRED_ROW_IDS = frozenset(
    {
        "H1-capability-task-contracts",
        "capability-registry",
        "capability-task-binding",
        "recipe-contracts",
        "recipe-compile-identity",
        "capability-catalog",
        "task-compatibility-matrix",
        "fifteen-task-export-repair",
        "contract-registry-binding-recipe-test-suites",
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
    "rows",
    "real_computation",
    "external_evidence",
    "unsupported_capabilities",
    "compatibility_notes",
    "receipt_protocol_gap",
)


def load_handoff(path: Path = HANDOFF_PATH) -> dict[str, Any]:
    """Parse the handoff with stdlib JSON only."""
    with path.open(encoding="utf-8") as handle:
        doc: dict[str, Any] = json.load(handle)
    assert isinstance(doc, dict), "handoff root must be a JSON object"
    return doc


def validate_real_computation(block: dict[str, Any]) -> None:
    """Reject a real-computation claim that has no run spec or command.

    This is the negative QA hook: switching the block to ``PASS`` without an
    actual run specification (or a recorded command) must fail here.
    """
    assert isinstance(block, dict), "real_computation must be an object"
    result = block.get("result")
    allowed = ALLOWED_ROW_RESULTS | {"NOT_RUN"}
    assert result in allowed, f"invalid real_computation result {result!r}"
    assert isinstance(block.get("reason"), str) and block["reason"].strip(), (
        "real_computation requires a reason"
    )
    if result == "PASS":
        spec = block.get("run_spec")
        command = block.get("command")
        has_spec = isinstance(spec, str) and bool(spec.strip())
        has_command = isinstance(command, list) and any(
            isinstance(item, str) and item.strip() for item in command
        )
        assert has_spec or has_command, (
            "real_computation PASS requires a run spec or an executed command"
        )


def _has_executed_reference(evidence: dict[str, Any]) -> bool:
    for key in ("commands", "tests"):
        values = evidence.get(key)
        if isinstance(values, list) and any(
            isinstance(item, str) and item.strip() for item in values
        ):
            return True
    return False


def validate_handoff(doc: dict[str, Any]) -> None:
    """Reject any structural, enum, digest or honesty defect."""
    for key in REQUIRED_TOP_LEVEL:
        assert key in doc, f"handoff missing top-level field {key!r}"

    assert doc["handoff_id"] == "H2"
    assert doc["handoff_version"] == 1
    assert doc["contract_set"] == "acp2.science"
    assert isinstance(doc["contract_set_version"], int) and not isinstance(
        doc["contract_set_version"], bool
    )
    assert doc["contract_set_version"] >= 1
    assert doc["contract_set_frozen"] is True
    assert isinstance(doc["frozen_at"], str) and doc["frozen_at"].strip()

    basis = doc["contract_version_basis"]
    assert basis["handoff_h1_version"] == 1
    expected_digest = hashlib.sha256(H1_PATH.read_bytes()).hexdigest()
    assert basis["handoff_h1_sha256"] == expected_digest, (
        "H2 must freeze the exact H1 bytes it was built from"
    )

    gate = doc["gate_command"]
    assert isinstance(gate, list) and gate[:3] == [gate[0], "-m", "pytest"], (
        "gate_command must be an explicit pytest argv"
    )
    assert "tests/test_acp2_science_handoff.py" in gate, (
        "the gate command must include this delivered validator"
    )
    for token in gate:
        if isinstance(token, str) and token.startswith("tests/"):
            assert (REPO / token).exists(), f"gate command names missing test {token!r}"

    rows = doc["rows"]
    assert isinstance(rows, list) and rows, "handoff must contain rows"
    ids = [row.get("row_id") for row in rows]
    assert all(isinstance(value, str) and value for value in ids), "every row needs a row_id"
    assert len(set(ids)) == len(ids), "duplicate row_id"
    assert REQUIRED_ROW_IDS <= set(ids), f"missing P0 rows: {sorted(REQUIRED_ROW_IDS - set(ids))}"

    for row in rows:
        where = row["row_id"]
        for key in ("row_id", "item", "result", "scope", "evidence"):
            assert key in row, f"{where}: missing field {key!r}"
        assert isinstance(row["item"], str) and row["item"].strip(), f"{where}: empty item"
        assert row["result"] in ALLOWED_ROW_RESULTS, (
            f"{where}: result {row['result']!r} not in {sorted(ALLOWED_ROW_RESULTS)}"
        )
        assert isinstance(row["scope"], str) and row["scope"].strip(), f"{where}: empty scope"
        evidence = row["evidence"]
        assert isinstance(evidence, dict), f"{where}: evidence must be an object"
        if row["result"] == "PASS":
            assert isinstance(evidence.get("summary"), str) and evidence["summary"].strip(), (
                f"{where}: PASS needs a non-empty evidence summary"
            )
            assert _has_executed_reference(evidence), (
                f"{where}: PASS needs an executed command or test reference"
            )

    validate_real_computation(doc["real_computation"])

    external = doc["external_evidence"]
    assert isinstance(external, dict), "external_evidence must be an object"
    for producer in ("b01_receipt_protocol", "b05_stdio_launch"):
        block = external.get(producer)
        assert isinstance(block, dict), f"external_evidence missing {producer}"
        assert block.get("result") in ALLOWED_ROW_RESULTS, f"{producer}: invalid result"
        assert block.get("missing_artifact"), f"{producer}: missing_artifact must be named"
        assert block.get("reason"), f"{producer}: reason must state the exact gap"

    b04 = external.get("b04_premature_mcp_adapter_check")
    assert isinstance(b04, dict) and b04.get("is_h2_evidence") is False, (
        "B04's premature MCP-adapter check must be recorded as NOT H2 evidence"
    )

    candidate = external.get("h2_test_candidate")
    assert isinstance(candidate, dict), "external_evidence missing h2_test_candidate"
    assert candidate.get("result") in ALLOWED_ROW_RESULTS
    if candidate.get("result") != "PASS":
        assert candidate.get("gap"), "an unverified H2 candidate must state its gap"

    for key in ("server_revision", "client_revision", "harness_revision"):
        assert key in external, f"external_evidence missing {key}"
        value = external[key]
        assert value is None or (isinstance(value, str) and value.strip()), (
            f"{key} must be null or an actual revision, never an invented placeholder"
        )
        if value is None:
            assert external.get(f"{key}_gap", "").strip(), (
                f"{key} is null and must carry its exact gap"
            )

    unsupported = doc["unsupported_capabilities"]
    assert isinstance(unsupported, dict), "unsupported_capabilities must be an object"
    entries = unsupported.get("families")
    assert isinstance(entries, list) and entries, "unsupported_capabilities.families is required"
    names = {entry["family"] for entry in entries}
    assert names == set(UNSUPPORTED_FAMILIES), (
        "unsupported list must name exactly every unseeded family: "
        f"{sorted(set(UNSUPPORTED_FAMILIES) - names)} missing"
    )
    assert names.isdisjoint(set(BUILTIN_FAMILIES)), "a registered family cannot be unsupported"
    assert set(unsupported["registered_capability_ids"]) == set(BUILTIN_CAPABILITY_IDS)
    for entry in entries:
        assert entry.get("registered") is False, f"{entry['family']}: registered must be false"
        assert isinstance(entry.get("reason"), str) and entry["reason"].strip(), (
            f"{entry['family']}: unsupported family needs a reason"
        )

    assert isinstance(doc["compatibility_notes"], list) and doc["compatibility_notes"]
    gap = doc["receipt_protocol_gap"]
    assert gap.get("status") == "NOT_VERIFIED" and gap.get("samples_delivered") is False
    assert "acceptance-result.schema.json" in gap.get("missing_artifact", "")


def test_handoff_h2_parses_and_is_deterministic() -> None:
    raw = HANDOFF_PATH.read_text(encoding="utf-8")
    doc = load_handoff()
    regenerated = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    assert regenerated == raw, "handoff must be byte-identical with sorted keys and a newline"


def test_handoff_h2_structure_and_result_enum() -> None:
    doc = load_handoff()
    validate_handoff(doc)  # raises AssertionError naming the exact defect
    for row in doc["rows"]:
        assert row["result"] in ALLOWED_ROW_RESULTS


def test_every_pass_row_has_executed_evidence() -> None:
    doc = load_handoff()
    for row in doc["rows"]:
        if row["result"] == "PASS":
            assert _has_executed_reference(row["evidence"]), row["row_id"]


def test_real_computation_pass_without_run_spec_is_rejected() -> None:
    tampered = copy.deepcopy(load_handoff()["real_computation"])
    tampered["result"] = "PASS"
    tampered["run_spec"] = None
    tampered["command"] = []
    with pytest.raises(AssertionError, match="run spec or an executed command"):
        validate_real_computation(tampered)


def test_pass_row_without_evidence_is_rejected() -> None:
    tampered = copy.deepcopy(load_handoff())
    tampered["rows"][0]["result"] = "PASS"
    tampered["rows"][0]["evidence"] = {
        "summary": "",
        "commands": [],
        "tests": [],
        "artifacts": [],
    }
    with pytest.raises(AssertionError, match="non-empty evidence summary"):
        validate_handoff(tampered)


def test_external_evidence_separates_gaps_and_revisions() -> None:
    external = load_handoff()["external_evidence"]
    for producer in ("b01_receipt_protocol", "b05_stdio_launch"):
        assert external[producer]["result"] == "NOT_VERIFIED"
    for key in ("server_revision", "client_revision", "harness_revision"):
        assert external[key] is None
        assert external[f"{key}_gap"].strip()
    assert external["h2_test_candidate"]["result"] == "NOT_VERIFIED"
    assert external["b04_premature_mcp_adapter_check"]["is_h2_evidence"] is False


def test_unsupported_families_match_the_builtin_catalog() -> None:
    doc = load_handoff()
    names = {entry["family"] for entry in doc["unsupported_capabilities"]["families"]}
    registered = {capability_id.split(".", 1)[0] for capability_id in BUILTIN_CAPABILITY_IDS}
    assert names == set(UNSUPPORTED_FAMILIES)
    assert names.isdisjoint(registered)


def test_task_compatibility_matrix_has_fifteen_rows() -> None:
    lines = MATRIX_PATH.read_text(encoding="utf-8").splitlines()
    data_rows = [line for line in lines if re.match(r"^\|\s*\d+\s*\|", line)]
    assert len(data_rows) == 15, f"matrix must have 15 task rows, found {len(data_rows)}"
    kinds = {kind.value for kind in TaskKind}
    matrix_kinds = set()
    for line in data_rows:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        matrix_kinds.add(cells[1].strip("`"))
        assert "`implemented`" in line, f"row must name execution status: {cells[1]}"
        assert "`static-fact`" in line, f"row must name static evidence: {cells[1]}"
    assert matrix_kinds == kinds, f"matrix/task-kind drift: {matrix_kinds ^ kinds}"


def test_cross_track_dependencies_have_no_missing_id_or_cycle() -> None:
    doc = load_ledger()
    validate_ledger(doc)  # resolves producer/consumer and precondition ids
    assert find_cycles(doc) == [], "cross-track dependency ledger must be acyclic"
