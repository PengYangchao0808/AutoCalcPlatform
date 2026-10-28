"""M5-02 sign-off: clean BOM, final coverage matrix and the BOM walker.

The structural layer is fast: it loads the committed BOM/coverage/manifest and
runs the walker in dry-run mode (receipts and coverage rows are hashed, artifact
bytes are only stat-checked). The full ``walk_bom.py --verify`` run is the
candidate gate and is exercised by the acceptance command, not by pytest.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE = REPO_ROOT / "docs/reference/next-change/acp2-contracts/release"
COVERAGE = RELEASE / "evidence-coverage.current.json"
BOM = RELEASE / "bom.current.json"
MANIFEST = REPO_ROOT / "docs/reference/next-change/acp2-contracts/manifest.json"
WALKER = REPO_ROOT / "scripts/release/walk_bom.py"
VALIDATOR = REPO_ROOT / "scripts/acceptance/validate_acp2_gap_coverage.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def walker():
    return _load("acp2_walk_bom_signoff", WALKER)


@pytest.fixture(scope="module")
def validator():
    return _load("acp2_gap_validator_signoff", VALIDATOR)


@pytest.fixture(scope="module")
def bom() -> dict:
    return json.loads(BOM.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def coverage() -> dict:
    return json.loads(COVERAGE.read_text(encoding="utf-8"))


def test_bom_is_the_clean_m5_02_candidate(bom) -> None:
    ledger = json.loads(COVERAGE.read_text(encoding="utf-8"))
    assert bom["schema"] == "acp2.release.bom.v1"
    assert bom["task_id"] == "M5-02"
    assert bom["source"]["clean"] is True
    # M5-03 candidate bump (2026-10-28): the operator's F1-F7 fixes moved the
    # candidate from f6b6153 to the commit that carries them.
    assert bom["source"]["revision"].startswith("5f3b867")
    assert bom["source"]["persistent_tag"].startswith("acp2-remediation-m5-02-candidate")
    assert bom["source"]["release_overlay"]
    assert bom["evidence"]["reuse_rule"] == "exact"
    assert bom["evidence"]["supersedes"]
    assert bom["evidence"]["coverage_sha256"] == ledger["coverage_sha256"]
    assert bom["evidence"]["coverage_scope_rows"] == 39
    assert bom["evidence"]["coverage_gap_rows"] == 26


def test_coverage_matrix_is_structurally_complete(coverage, bom, validator) -> None:
    assert coverage["summary"]["tasks_total"] == 39
    assert coverage["summary"]["checkpoints_total"] == 32
    assert coverage["summary"]["gaps_total"] == 26
    assert coverage["summary"]["h_rows_total"] == 8
    assert len(coverage["deferred_rows"]) == 3
    assert coverage["candidate_full"]["allowed"] is False
    for section in ("tasks", "checkpoints", "gaps", "h_rows", "deferred_rows"):
        for row in coverage[section]:
            assert row["candidate"] == bom["source"]["revision"], (section, row["id"])
            assert row["receipt"], (section, row["id"])
            assert len(row["receipt_sha256"]) == 64, (section, row["id"])
            assert row["impact_globs"], (section, row["id"])
            assert row["required_observations"], (section, row["id"])
    schema = json.loads((RELEASE / "evidence-coverage.schema.json").read_text(encoding="utf-8"))
    assert validator.validate_schema(coverage, schema, schema, "") == []


def test_the_documented_final_judgements_are_recorded(coverage) -> None:
    tasks = {row["id"]: row for row in coverage["tasks"]}
    checkpoints = {row["id"]: row for row in coverage["checkpoints"]}
    gaps = {row["id"]: row for row in coverage["gaps"]}
    h_rows = {row["id"]: row for row in coverage["h_rows"]}

    assert (tasks["M0-13"]["scope"], tasks["M0-13"]["result"]) == (
        "NOT_APPLICABLE",
        "NOT_VERIFIED",
    )
    assert checkpoints["M0-02c"]["status"] == "OPEN"
    assert checkpoints["M0-02c"]["result"] == "NOT_VERIFIED"
    assert tasks["M3-03"]["status"] == "OPEN"
    assert tasks["M3-03"]["reuse"] == "rerun_required"
    assert "NOT_VERIFIED" in tasks["M3-03"]["notes"]
    assert tasks["M4-04"]["result"] == "PASS"
    assert "claim_status=refuted" in tasks["M4-04"]["notes"]
    # M5-03 closed by the independent operator's round-2 receipt (2026-10-28).
    assert tasks["M5-03"]["status"] == "CLOSED"
    assert tasks["M5-03"]["result"] == "PASS"
    assert tasks["M5-03"]["reuse"] == "exact"
    assert "round 2" in tasks["M5-03"]["notes"]

    assert (h_rows["H4"]["status"], h_rows["H4"]["result"]) == ("OPEN", "NOT_VERIFIED")
    assert (h_rows["H6"]["status"], h_rows["H6"]["result"]) == ("CLOSED", "PASS")
    assert h_rows["H3"]["result"] == "NOT_VERIFIED"
    assert h_rows["H7"]["result"] == "NOT_VERIFIED"
    assert {row["id"] for row in coverage["h_rows"] if row["status"] == "CLOSED"} == {
        "H0",
        "H1",
        "H2",
        "H5",
        "H6",
    }

    assert gaps["G15"]["status"] == "OPEN"
    assert gaps["G15"]["required_observations"]["DA-ENV-LSF"] == "NOT_VERIFIED"
    assert gaps["G15"]["required_observations"]["bounded_recipe_public_entry"] == "absent"
    assert (gaps["G20"]["status"], gaps["G20"]["result"]) == ("CLOSED", "PASS")
    assert gaps["G18"]["status"] == "OPEN"
    # Closed with the M5-03 operator receipt (round 2); G16's note records
    # the worker-hosted COMPUTE split into DEF-01.
    for gap_id in ("G01", "G06", "G16", "G17", "G19"):
        assert gaps[gap_id]["status"] == "CLOSED"
        assert gaps[gap_id]["result"] == "PASS"
    assert "worker-hosted third-party COMPUTE was SPLIT OUT" in gaps["G16"]["notes"]

    deferred = {row["capability"]: row for row in coverage["deferred_rows"]}
    assert set(deferred) == {"SCOPE-DEF-01", "SCOPE-DEF-02", "SCOPE-DEF-03"}
    for row in deferred.values():
        assert row["scope"] == "DEFERRED"
        assert row["result"] == "NOT_VERIFIED"


def test_b26_registration_flips_the_release_namespace(bom) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    contracts = {entry["id"]: entry for entry in manifest["contracts"]}
    assert manifest["contract_version"] == "1.7.0"
    assert manifest["contract_version_history"][-1]["stage"] == "B26"
    for contract_id in (
        "release-scope",
        "release-evidence-coverage-schema",
        "release-bom-schema",
        "release-bom-current",
        "release-evidence-coverage-current",
    ):
        entry = contracts[contract_id]
        assert entry["status"] == "delivered", contract_id
        assert entry["delivered_by"] == "B26", contract_id
        assert entry["owner"] == "B", contract_id
    assert (
        contracts["release-bom-current"]["sha256"] == hashlib.sha256(BOM.read_bytes()).hexdigest()
    )
    assert (
        contracts["release-evidence-coverage-current"]["sha256"]
        == hashlib.sha256(COVERAGE.read_bytes()).hexdigest()
    )


def test_scope_deferred_rows_match_the_coverage_deferred_rows(coverage) -> None:
    scope = json.loads((RELEASE / "scope.json").read_text(encoding="utf-8"))
    deferred = {
        capability["id"]: capability
        for capability in scope["capabilities"]
        if capability["class"] == "DEFERRED"
    }
    assert set(deferred) == {row["capability"] for row in coverage["deferred_rows"]}
    for row in coverage["deferred_rows"]:
        assert row["statement"] == deferred[row["capability"]]["statement"]
    assert scope["candidate_full_gate"]["allowed"] is False
    assert scope["candidate_full_gate"]["deferred_counts_as_delivered"] is False


def test_walk_bom_dry_run_traverses_every_receipt(walker) -> None:
    report = walker.verify(BOM, root=REPO_ROOT, dry_run=True)
    assert report["result"] == "PASS"
    assert report["artifacts_verified"] >= 27
    assert report["coverage_counts"] == {
        "tasks": 39,
        "checkpoints": 32,
        "gaps": 26,
        "h_rows": 8,
        "deferred_rows": 3,
    }
    assert report["receipts_verified"] == 39 + 32 + 26 + 8 + 3
    assert report["receipts_source"] == "store", (
        "M5-03 F7: coverage receipts must be verified from the delivered store"
    )
    assert report["contract_version"] == "1.7.0"
    # Post-M5-03 sign-off: the operator-closed rows left the open set.
    assert set(report["required_open_rows"]) == {
        "G05",
        "G10",
        "G13",
        "G15",
        "G18",
        "H3",
        "H4",
        "H7",
        "M3-03",
    }


def test_walker_fails_closed_on_missing_receipt(walker, coverage) -> None:
    mutated = copy.deepcopy(coverage)
    mutated["tasks"][0]["receipt"] = "reports/does-not-exist.json"
    with pytest.raises(walker.WalkError, match="does not exist"):
        walker._check_coverage_rows(REPO_ROOT, mutated, candidate=mutated["tasks"][0]["candidate"])


def test_walker_fails_closed_on_candidate_drift(walker, coverage, bom) -> None:
    mutated = copy.deepcopy(coverage)
    mutated["tasks"][0]["candidate"] = "0" * 40
    with pytest.raises(walker.WalkError, match="candidate"):
        walker._check_coverage_rows(REPO_ROOT, mutated, candidate=bom["source"]["revision"])


def test_walker_fails_closed_on_rerun_pass(walker, coverage, bom) -> None:
    mutated = copy.deepcopy(coverage)
    row = next(item for item in mutated["tasks"] if item["id"] == "M3-03")
    row["reuse"] = "rerun_required"
    row["status"] = "CLOSED"
    row["result"] = "PASS"
    with pytest.raises(walker.WalkError, match="rerun_required but claims PASS"):
        walker._check_coverage_rows(REPO_ROOT, mutated, candidate=bom["source"]["revision"])


def test_walker_fails_closed_on_bom_source_drift(walker, tmp_path, bom) -> None:
    mutated = copy.deepcopy(bom)
    mutated["source"]["revision"] = "0" * 40
    path = tmp_path / "bom.current.json"
    path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(walker.WalkError, match="not a commit"):
        walker.verify(path, root=REPO_ROOT, dry_run=True)
