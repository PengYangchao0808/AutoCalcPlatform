"""M0-11 (G02/G03/G12/G13/G20) — the identity decision freeze is machine-checked.

The freeze document
(``docs/reference/next-change/acp2-contracts/decision-freeze/identity.v1.json``)
is only worth something if it cannot rot.  This file pins four things:

1. **Structure and honesty** — every decision carries the required fields, and a
   ``pending_*`` decision can never be read as ``frozen`` (the very failure the
   EXECUTION_PLAN M0-11 exit forbids).
2. **Vectors resolve** — every ``vectors[].test`` names a real test in a real
   file, resolved independently of the validator's own helper.
3. **The client inventory is the live scan** — the embedded digest must equal a
   fresh run of ``scripts/acceptance/check_v1_request_field_consumers.py``, so a
   new client field cannot slip in without the freeze being regenerated.
4. **Known consumers are snapshotted** — the frontend wizard body builder, the
   RPH bridge tool arguments and the prepare conflict are asserted literally.

Nothing here executes science, a Controller or a browser: it is field/identity
evidence only.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from acp_services.entrypoints.application_params import (  # noqa: E402
    ENTRY_MCP,
    FIELD_POLICY,
    REQUEST_PREPARE,
    SUBMIT_FIELD_POLICY,
    ApplicationParamError,
    validate_request_fields,
)
from scripts.acceptance import check_v1_request_field_consumers as scanner  # noqa: E402
from scripts.acceptance import validate_acp2_decision_freeze as validator  # noqa: E402

FREEZE_PATH = (
    REPO_ROOT / "docs/reference/next-change/acp2-contracts/decision-freeze/identity.v1.json"
)
P2_CONTRACT = (
    REPO_ROOT / "docs/reference/next-change/acp2-contracts/runtime/p2-research-operations.v1.json"
)
RPH_RECORD = REPO_ROOT / "rph/packages/research/src/record.ts"

FROZEN = "frozen"
PENDING_IMPLEMENTATION = "pending_implementation"
PENDING_CONFLICT = "pending_conflict"


@pytest.fixture(scope="module")
def report() -> dict[str, Any]:
    """One live consumer scan shared by the snapshot tests."""
    return scanner.build_report(REPO_ROOT)


@pytest.fixture(scope="module")
def freeze() -> dict[str, Any]:
    """The parsed freeze document (read-only: copy before mutating)."""
    return json.loads(FREEZE_PATH.read_text(encoding="utf-8"))


def _decisions(freeze: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in freeze["decisions"] if isinstance(item, dict)]


def _decision(freeze: dict[str, Any], identifier: str) -> dict[str, Any]:
    for item in _decisions(freeze):
        if item["id"] == identifier:
            return item
    raise AssertionError(f"decision {identifier} is missing from the freeze")


def _consumer(report: dict[str, Any], name: str) -> dict[str, Any]:
    for entry in report["consumers"]:
        if entry["consumer"] == name:
            return entry
    raise AssertionError(f"consumer {name} is missing from the live scan")


def _vector_names(path: Path) -> set[str]:
    """Resolve a test name independently of the validator's helper."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        pattern = r"^\s*(?:async\s+)?def\s+([A-Za-z0-9_]+)\s*\("
    else:
        pattern = r"\b(?:test|it)\(\s*[\"']([^\"']+)[\"']"
    return set(re.findall(pattern, text, re.MULTILINE))


# ---------------------------------------------------------------------------
# 1. Structure, honesty and vector resolution
# ---------------------------------------------------------------------------


def test_freeze_document_is_valid() -> None:
    """The shipped freeze passes the mechanical validator (schema + references)."""
    errors = validator.validate(FREEZE_PATH, REPO_ROOT)
    assert errors == [], errors


def test_every_decision_carries_the_required_fields(freeze: dict[str, Any]) -> None:
    """decision/owner/impact/versions/rollback/clients/vectors, per EXECUTION_PLAN."""
    assert len(_decisions(freeze)) >= 10
    identifiers = [item["id"] for item in _decisions(freeze)]
    assert len(identifiers) == len(set(identifiers))
    for decision in _decisions(freeze):
        assert re.fullmatch(r"D-IDENT-\d{2}", decision["id"]), decision["id"]
        assert decision["status"] in {FROZEN, PENDING_IMPLEMENTATION, PENDING_CONFLICT}
        assert decision["owner"] in {"B", "A", "C-RPH", "C-Web", "INT"}
        assert decision["signoff"], decision["id"]
        for field in ("decision", "rationale", "rollback"):
            assert isinstance(decision[field], str) and decision[field].strip(), (
                decision["id"],
                field,
            )
        assert "wire_version" in decision and "migration_version" in decision
        assert decision["impact"]["files"], decision["id"]
        assert decision["impact"]["entries"], decision["id"]
        for relative in decision["impact"]["files"]:
            assert (REPO_ROOT / relative).exists(), (decision["id"], relative)
        assert decision["vectors"], decision["id"]


def test_every_vector_names_an_existing_test(freeze: dict[str, Any]) -> None:
    """A frozen claim must point at a test that exists (no phantom evidence)."""
    for decision in _decisions(freeze):
        for vector in decision["vectors"]:
            relative, _, name = vector["test"].partition("::")
            path = REPO_ROOT / relative
            assert path.is_file(), (decision["id"], relative)
            assert name in _vector_names(path), (decision["id"], relative, name)
            assert vector["asserts"].strip(), (decision["id"], relative)


def test_pending_decisions_are_never_frozen(freeze: dict[str, Any]) -> None:
    """未决项不得写 frozen: every pending decision says why and who closes it."""
    pending_ids = set()
    for decision in _decisions(freeze):
        if decision["status"] == FROZEN:
            assert "pending_reason" not in decision, decision["id"]
            assert "frozen_forward_convention" not in decision, decision["id"]
            continue
        pending_ids.add(decision["id"])
        assert decision["pending_reason"].strip(), decision["id"]
        assert re.match(r"M1-\d\d", decision["consumer_task"]), decision["id"]
        assert isinstance(decision["frozen_forward_convention"], bool), decision["id"]
        assert isinstance(decision["blocking"], bool), decision["id"]
        assert decision["status"] != FROZEN
    listed = {entry["id"]: entry["status"] for entry in freeze["pending_summary"]}
    assert set(listed) == pending_ids
    for decision in _decisions(freeze):
        if decision["id"] in listed:
            assert listed[decision["id"]] == decision["status"]
    # The three forward conventions and the one discovered conflict are pending.
    for identifier in ("D-IDENT-04", "D-IDENT-05", "D-IDENT-07"):
        assert _decision(freeze, identifier)["status"] == PENDING_IMPLEMENTATION
        assert _decision(freeze, identifier)["frozen_forward_convention"] is True
    assert _decision(freeze, "D-IDENT-10")["status"] == PENDING_CONFLICT
    assert _decision(freeze, "D-IDENT-10")["blocking"] is True


def test_status_vocabulary_is_frozen(freeze: dict[str, Any]) -> None:
    """The three statuses are defined, not implied."""
    vocabulary = freeze["status_vocabulary"]
    assert set(vocabulary) == {FROZEN, PENDING_IMPLEMENTATION, PENDING_CONFLICT}
    for description in vocabulary.values():
        assert isinstance(description, str) and len(description) > 40


# ---------------------------------------------------------------------------
# 2. The client inventory is the live scan (no hand-written blast radius)
# ---------------------------------------------------------------------------


def test_client_inventory_matches_the_live_scan(
    freeze: dict[str, Any], report: dict[str, Any]
) -> None:
    """The embedded inventory digest/summary equal a fresh run of the scanner."""
    inventory = freeze["client_inventory"]
    assert inventory["digest"] == report["digest"]
    assert inventory["summary"] == report["summary"]
    assert len(inventory["consumers"]) == len(report["consumers"])
    embedded = {(entry["consumer"], entry["path"]) for entry in inventory["consumers"]}
    live = {(entry["consumer"], entry["path"]) for entry in report["consumers"]}
    assert embedded == live
    assert inventory["exclusions"], "the scan must say what it skipped"
    # Every consumer the v1 field decision claims as compatible is in the scan.
    claimed = {
        (entry["consumer"], entry["path"])
        for entry in _decision(freeze, "D-IDENT-02")["compatible_clients"]
    }
    assert claimed == live


def test_scanner_is_deterministic(report: dict[str, Any]) -> None:
    """A second run yields the same digest (no clock, no hash-order drift)."""
    again = scanner.build_report(REPO_ROOT)
    assert again["digest"] == report["digest"]
    assert [entry["consumer"] for entry in again["consumers"]] == [
        entry["consumer"] for entry in report["consumers"]
    ]


# ---------------------------------------------------------------------------
# 3. Known consumers (snapshots)
# ---------------------------------------------------------------------------


def test_scanner_reports_the_frontend_structure_body_builder(report: dict[str, Any]) -> None:
    """The wizard's frozen submit body builder is the first frontend consumer."""
    entry = _consumer(report, "frontend:buildStructureJobBody")
    assert entry["kind"] == "frontend-js"
    assert entry["path"] == "frontend/js/workbench/features/wizard/submit-edit.js"
    assert entry["request_kind"] == "submit"
    assert entry["entry"] == "http"
    assert entry["disposition"] == scanner.DISPOSITION_COMPLIANT
    assert entry["keys"] == [
        "input",
        "method",
        "molecule_name",
        "project_id",
        "remark",
        "resources",
        "task_name",
        "workflow",
    ]
    assert entry["extra_keys"] == []
    # The node-selection helper adds the two target fields at submit time.
    stage = _consumer(report, "frontend:submitStageWorkflowJob")
    assert {"target_node", "node_tags"} <= set(stage["keys"])
    assert stage["disposition"] == scanner.DISPOSITION_COMPLIANT


def test_scanner_reports_the_rph_build_tool_arguments(report: dict[str, Any]) -> None:
    """The bridge's P1 submit arguments are exactly the frozen six keys."""
    submit = _consumer(report, "rph:buildToolArguments:submit")
    assert submit["path"] == "rph/packages/bridge/src/index.ts"
    assert submit["entry"] == "mcp"
    assert submit["keys"] == [
        "idempotency_key",
        "input",
        "method",
        "project_id",
        "resources",
        "workflow",
    ]
    assert submit["disposition"] == scanner.DISPOSITION_COMPLIANT
    validate = _consumer(report, "rph:buildToolArguments:validate")
    assert validate["keys"] == submit["keys"]
    # `explicit` is the analyze/evidence-only argument of the shared case block.
    evidence = _consumer(report, "rph:buildToolArguments:evidence")
    assert "explicit" in evidence["keys"]
    assert evidence["disposition"] == scanner.DISPOSITION_OTHER_KIND


def test_prepare_resource_id_is_recorded_as_a_conflict(
    freeze: dict[str, Any], report: dict[str, Any]
) -> None:
    """The prepare argument vocabulary disagrees with the prepare field policy."""
    prepare = _consumer(report, "rph:buildToolArguments:prepare")
    assert prepare["keys"] == ["attempt", "idempotency_key", "job_id", "resource_id"]
    assert prepare["extra_keys"] == ["resource_id"]
    assert prepare["disposition"] == scanner.DISPOSITION_BREAKING
    with pytest.raises(ApplicationParamError) as info:
        validate_request_fields(
            {"job_id": "job-1", "attempt": 1, "resource_id": "freq.out"},
            request_kind=REQUEST_PREPARE,
            entry=ENTRY_MCP,
        )
    assert info.value.params["rule"] == "unknown_field"
    assert info.value.params["field"] == "resource_id"
    conflict = _decision(freeze, "D-IDENT-10")
    assert conflict["status"] == PENDING_CONFLICT
    assert "resource_id" in conflict["decision"]
    assert any(
        client["consumer"] == "rph:buildToolArguments:prepare"
        for client in conflict["compatible_clients"]
    )


# ---------------------------------------------------------------------------
# 4. The field decision matches the live policy
# ---------------------------------------------------------------------------


def test_v1_field_decision_matches_the_live_policy(freeze: dict[str, Any]) -> None:
    """extra=forbid / 422 / empty allowlist, checked against the code, not prose."""
    field_decision = _decision(freeze, "D-IDENT-02")["field_decision"]
    assert field_decision["additional_properties"] is False
    assert field_decision["http_status"] == 422
    assert field_decision["error_code"] == "input_invalid"
    assert field_decision["rules"] == ["unknown_field", "unsupported_server_path"]
    accepted = sorted(
        name
        for name, row in SUBMIT_FIELD_POLICY.items()
        if row.disposition != "server_path_rejected"
    )
    assert field_decision["accepted_submit_fields"] == accepted
    assert field_decision["server_path_fields"] == ["config_path", "output_dir"]
    allowlist = field_decision["legacy_allowlist"]
    assert allowlist["rows"] == []
    assert allowlist["per_field_exit_conditions"] == []
    assert allowlist["removal_version"].startswith("application-api.v2")
    assert "exit_condition" in allowlist["exit_condition_rule"]
    # Prepare is a different policy; the freeze must say so explicitly.
    assert (
        sorted(FIELD_POLICY[REQUEST_PREPARE])
        == freeze["field_policy_authority"]["prepare_kind_declared_fields"]
    )


def test_p2_rows_use_the_frozen_status_enum(freeze: dict[str, Any]) -> None:
    """P2 implementation_status is planned|delivered|verified, and the values are recorded."""
    contract = json.loads(P2_CONTRACT.read_text(encoding="utf-8"))
    observed = {
        row["id"]: row["implementation_status"]
        for row in contract["rows"]
        if isinstance(row, dict) and "implementation_status" in row
    }
    assert observed == {
        "P2-R1": "planned",
        "P2-R2": "delivered",
        "P2-R3": "delivered",
        "P2-R4": "planned",
        "P2-R5": "planned",
    }
    assert all(status in {"planned", "delivered", "verified"} for status in observed.values())
    recorded = _decision(freeze, "D-IDENT-03")["current_values"]
    assert {row["id"]: row["implementation_status"] for row in recorded["rows"]} == observed
    # No row is verified yet: contract-level evidence is not live evidence.
    assert recorded["verified_rows"] == []
    enum_text = _decision(freeze, "D-IDENT-03")["decision"]
    for status in ("planned", "delivered", "verified"):
        assert status in enum_text


# ---------------------------------------------------------------------------
# 5. Forward-only conventions really are forward-only
# ---------------------------------------------------------------------------


def _src_text() -> str:
    """Concatenate every ``src/**/*.py`` (producer search for pending decisions)."""
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted((REPO_ROOT / "src").rglob("*.py"))
    )


def test_planning_claim_namespace_is_declared_forward_only(freeze: dict[str, Any]) -> None:
    """The plan|... claim namespace has no producer in src/ yet (so: pending)."""
    decision = _decision(freeze, "D-IDENT-04")
    assert decision["status"] == PENDING_IMPLEMENTATION
    assert "plan|<project>|<research>|<session>|<round>|<call_seq>" in decision["decision"]
    assert "never a submit ledger key" in decision["decision"]
    source = _src_text()
    assert "call_seq" not in source
    assert '"plan|' not in source and "'plan|" not in source


def test_model_receipt_namespace_is_declared_forward_only(freeze: dict[str, Any]) -> None:
    """The model receipt namespace binds research/round/call, never job/attempt."""
    decision = _decision(freeze, "D-IDENT-05")
    assert decision["status"] == PENDING_IMPLEMENTATION
    assert (
        "modelrcpt|<project>|<research>|<session>|<round>|<call_seq>|<request_digest>"
        in (decision["decision"])
    )
    assert "NOT to a job_id or attempt" in decision["decision"]
    assert "modelrcpt" not in _src_text()


def test_rph_research_key_encoding_is_unchanged(freeze: dict[str, Any]) -> None:
    """The recorded RPH key grammar still matches rph/packages/research/src/record.ts."""
    source = RPH_RECORD.read_text(encoding="utf-8")
    start = source.index("export function researchRoundKey")
    round_key = source[start : source.index("export function researchEventKey")]
    assert '"record"' in round_key
    assert '.join(":")' in round_key
    assert "round-" in round_key
    # project_id is the first segment of the joined array.
    assert round_key.index("record.project_id") < round_key.index('"record"')
    assert ":event:" in source
    decision = _decision(freeze, "D-IDENT-06")
    assert decision["status"] == FROZEN
    for segment in (":record:", ":event:", "round-", "<project_id>"):
        assert segment in decision["decision"]


def test_rph_key_plan_digest_segment_is_forward_only(freeze: dict[str, Any]) -> None:
    """plan_digest is required for the derived key but is not implemented today."""
    source = RPH_RECORD.read_text(encoding="utf-8")
    assert "plan_digest" not in source
    decision = _decision(freeze, "D-IDENT-07")
    assert decision["status"] == PENDING_IMPLEMENTATION
    assert decision["frozen_forward_convention"] is True
    assert "plan_digest" in decision["decision"]


# ---------------------------------------------------------------------------
# 6. The scanner is executable: --check semantics
# ---------------------------------------------------------------------------


def test_scanner_check_mode_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``--out`` writes the report (exit 0); ``--check`` fails only on a breaking key."""
    out = tmp_path / "consumers.json"
    assert scanner.main(["--out", str(out)]) == scanner.EXIT_OK
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == scanner.SCHEMA
    assert payload["summary"]["breaking_consumers"] == ["rph:buildToolArguments:prepare"]
    capsys.readouterr()
    assert scanner.main(["--check"]) == scanner.EXIT_BREAKING
    assert "BREAKING rph:buildToolArguments:prepare" in capsys.readouterr().err
    empty = tmp_path / "empty-root"
    empty.mkdir()
    assert scanner.main(["--check", "--repo-root", str(empty)]) == scanner.EXIT_ERROR


def test_validator_rejects_a_frozen_pending_decision(tmp_path: Path) -> None:
    """A pending decision relabelled frozen is INVALID (no silent promotion)."""
    doctored = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    for decision in doctored["decisions"]:
        if decision["id"] == "D-IDENT-04":
            decision["status"] = FROZEN
    path = tmp_path / "identity.v1.json"
    path.write_text(json.dumps(doctored), encoding="utf-8")
    errors = validator.validate(path, REPO_ROOT)
    assert any("must not carry pending fields" in error for error in errors), errors


def test_validator_rejects_an_unresolvable_vector(tmp_path: Path) -> None:
    """A phantom test vector is INVALID (evidence must exist)."""
    doctored = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    doctored["decisions"][0]["vectors"][0]["test"] = (
        "tests/test_acp2_decision_freeze_identity.py::test_not_a_real_test"
    )
    path = tmp_path / "identity.v1.json"
    path.write_text(json.dumps(doctored), encoding="utf-8")
    errors = validator.validate(path, REPO_ROOT)
    assert any("not found in" in error for error in errors), errors


def test_validator_rejects_a_stale_client_inventory(tmp_path: Path) -> None:
    """A hand-edited inventory digest is INVALID (the scan is the authority)."""
    doctored = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    doctored["client_inventory"]["digest"] = "sha256:" + "0" * 64
    path = tmp_path / "identity.v1.json"
    path.write_text(json.dumps(doctored), encoding="utf-8")
    errors = validator.validate(path, REPO_ROOT)
    assert any("stale" in error for error in errors), errors
