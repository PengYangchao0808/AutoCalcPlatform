"""Guard the B20c H4 closeout (todo 24) against over-claiming.

The closeout aggregates P2 phase evidence. These tests pin the invariants that
must never drift:

* H4 stays OPEN with no promoted/supported/closed claim;
* no A/C row is signed by a non-owner (B signs execution facts only);
* the closeout input digest excludes the closeout's own digest, while the final
  index consumes the closeout's self-excluding reference digest;
* the shared run spec is exactly 13380/900/120/14400 and does not fail the
  evidence phase;
* every required open row (H4-01..H4-04, H4-search, A-20, C25) exists.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "reports/acp2_implementation/bb45a46/B/B20c"
CLOSEOUT = EVIDENCE / "h4-closeout.json"
INPUT_SET = EVIDENCE / "closeout-input-set.json"
OPEN_ROWS = EVIDENCE / "h4-open-rows.json"
CANDIDATE = EVIDENCE / "p2-candidate.json"
FINAL_INDEX = EVIDENCE / "h4-final-index.json"
RERUN = EVIDENCE / "rerun-index.json"
REUSABLE = EVIDENCE / "reusable-research-records.json"
RECEIPT = EVIDENCE / "h4-receipt.md"
CANDIDATE_FILES = EVIDENCE / "candidate-files.sha256"
OVERLAY = Path("/opt/acp2-dev/data/b/b20-candidate")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _artifact(name: str) -> dict:
    path = EVIDENCE / name
    assert path.is_file(), f"missing evidence artifact: {path}"
    return _load(path)


def test_evidence_files_present_and_parse() -> None:
    for name in (
        "p2-candidate.json",
        "closeout-input-set.json",
        "h4-closeout.json",
        "h4-open-rows.json",
        "h4-final-index.json",
        "rerun-index.json",
        "reusable-research-records.json",
    ):
        _artifact(name)
    assert RECEIPT.is_file()
    assert CANDIDATE_FILES.is_file()


def test_h4_stays_open_with_no_promotion() -> None:
    closeout = _load(CLOSEOUT)
    assert closeout["schema"] == "acp2.h4.closeout.v1"
    assert closeout["h4_status"] == "OPEN"
    assert closeout["closed"] is False
    assert closeout["promotion_allowed"] is False
    assert closeout["mechanism_proven"] is False
    assert closeout["scientific_result"] == "NOT_VERIFIED"
    assert closeout["aggregation_result"] == "PASS"
    assert closeout["result_scope"] == "aggregation_only"

    rows = _load(OPEN_ROWS)
    assert rows["h4_status"] == "OPEN"
    assert rows["mechanism_proven"] is False
    assert rows["supported_claims"] == 0
    assert rows["closed_claims"] == 0
    assert all(row["claim_status"] == "not_evaluated" for row in rows["rows"])


def test_zero_non_owner_signatures() -> None:
    closeout = _load(CLOSEOUT)
    rows = _load(OPEN_ROWS)

    assert closeout["signers"]["A"] is None
    assert closeout["signers"]["C"] is None
    assert closeout["signers"]["non_owner_signatures"] == []
    assert rows["non_owner_signature_count"] == 0
    assert rows["non_owner_signatures"] == []

    for row in rows["rows"]:
        assert row["owner"] in {"A", "B", "C"}
        assert row["signature_required_from"] == row["owner"]
        assert row["signed_by"] == []
        for signature in row["signed_by"]:
            assert signature["owner"] == row["owner"]

    b_signed = closeout["signers"]["B"]
    assert b_signed["signed"] is True
    assert "A-20" in b_signed["does_not_sign"]
    assert "C25" in b_signed["does_not_sign"]
    assert "H4-search" in b_signed["does_not_sign"]
    assert "mechanism_conclusion" in b_signed["does_not_sign"]


def test_required_open_rows_present() -> None:
    rows = _load(OPEN_ROWS)
    ids = {row["row_id"] for row in rows["rows"]}
    required = {
        "H4-01-valid-ts",
        "H4-02-bidirectional-irc",
        "H4-03-endpoint-connectivity",
        "H4-04-comparable-energy",
        "H4-search",
        "A-20-science-signoff",
        "C25-experience-closeout",
    }
    assert required <= ids
    assert rows["row_count"] == len(rows["rows"])

    for row in rows["rows"]:
        assert row["open_reason"].strip(), row["row_id"]
        assert row["status"].startswith("NOT_VERIFIED"), row["row_id"]
        if row["row_id"] in {"A-20-science-signoff", "C25-experience-closeout"}:
            assert row["present"] is False
            assert row["digest"] is None
            assert row["fabricated_digest"] is False
            assert row["expected_path"]

    search = next(row for row in rows["rows"] if row["row_id"] == "H4-search")
    assert search["owner"] == "A"
    assert "search_not_declared" in search["status"]
    assert "known-candidate" in search["open_reason"].lower()


def test_a20_and_c25_absences_not_fabricated() -> None:
    closeout = _load(CLOSEOUT)
    a20 = closeout["a20_ref"]
    c25 = closeout["c25_ref"]

    assert a20["status"] == "NOT_VERIFIED(a20_absent)"
    assert a20["owner"] == "A"
    assert a20["digest"] is None
    assert a20["fabricated_digest"] is False
    assert a20["expected_path"].endswith("/A/A-20/result.json")
    assert a20["expected_schema"] == "acp2.acceptance.v1"
    assert a20["expected_handoff_schema"].startswith("handoff_id=H4")
    assert a20["observed_contract_handoff"]["h4_closure_closed"] is False

    assert c25["status"] == "NOT_VERIFIED(c25_absent)"
    assert c25["owner"] == "C"
    assert c25["digest"] is None
    assert c25["fabricated_digest"] is False
    assert c25["expected_path"].endswith("/experience/handoffs/H4.json")
    assert c25["expected_receipt_schema"] == "acp2.acceptance.v1"
    assert c25["observed"]["c24a_present"] is False
    assert c25["observed"]["c24b_present"] is False


def test_closeout_input_set_excludes_its_own_digest() -> None:
    closeout = _load(CLOSEOUT)
    input_set = _load(INPUT_SET)

    closeout_path = str(CLOSEOUT.relative_to(REPO))
    entry_paths = {entry["path"] for entry in input_set["entries"]}
    assert closeout_path not in entry_paths
    assert closeout["self_exclusion"]["closeout_self_in_input_set"] is False
    assert input_set["self_exclusion"]["self_included"] is False
    assert input_set["self_exclusion"]["closeout_result_path"] == closeout_path

    closeout_file_digest = _sha256(CLOSEOUT)
    entry_digests = {entry["sha256"] for entry in input_set["entries"]}
    assert closeout_file_digest not in entry_digests

    expected_lines = sorted(
        f"{entry['repo_relative_path']} {entry['sha256']}\n" for entry in input_set["entries"]
    )
    recomputed = hashlib.sha256("".join(expected_lines).encode("utf-8")).hexdigest()
    assert recomputed == input_set["closeout_input_digest"]
    assert closeout["closeout_input_digest"] == recomputed

    phases = {entry["phase"] for entry in input_set["entries"]}
    assert phases == {
        "B20a-ready",
        "B20a-exec",
        "B20a-search",
        "B20b",
        "B19",
        "B18",
        "B17",
        "B16",
    }
    assert input_set["entry_count"] == 8


def test_final_reference_digest_and_index_chain() -> None:
    closeout = _load(CLOSEOUT)
    index = _load(FINAL_INDEX)
    input_set = _load(INPUT_SET)

    stored = closeout["final_reference_digest"]
    assert HEX64.match(stored)

    unsigned = dict(closeout)
    unsigned["final_reference_digest"] = ""
    recomputed = hashlib.sha256(_canonical(unsigned).encode("utf-8")).hexdigest()
    assert recomputed == stored

    assert index["closeout"]["final_reference_digest"] == stored
    assert index["closeout"]["closeout_input_digest"] == input_set["closeout_input_digest"]
    assert index["closeout"]["path"] == str(CLOSEOUT.relative_to(REPO))
    assert index["h4_status"] == "OPEN"
    assert index["closed"] is False
    assert index["mechanism_proven"] is False

    assert str(FINAL_INDEX.relative_to(REPO)) not in {
        entry["path"] for entry in input_set["entries"]
    }
    assert str((EVIDENCE / "h4-final-index.json").relative_to(REPO)) not in {
        entry["path"] for entry in input_set["entries"]
    }


def test_run_spec_values_are_exact_and_do_not_fail_evidence_phase() -> None:
    closeout = _load(CLOSEOUT)
    spec = closeout["run_spec"]
    assert spec["science_deadline_seconds"] == 13380
    assert spec["export_budget_seconds"] == 900
    assert spec["aggregate_budget_seconds"] == 120
    assert spec["total_budget_seconds"] == 14400
    assert 13380 + 900 + 120 == spec["total_budget_seconds"]
    assert spec["started_at"] is None
    assert spec["started_at_status"] == "NOT_VERIFIED(c24a_absent)"
    assert spec["verdict"]["evidence_phase_failed_by_elapsed_time"] is False


@pytest.mark.skipif(
    not OVERLAY.is_dir(),
    reason="B20 candidate worktree is external evidence (not part of a clean checkout)",
)
def test_candidate_freeze_is_verified_and_immutable() -> None:
    candidate = _load(CANDIDATE)
    assert candidate["schema"] == "acp2.b20c.p2-candidate.v1"
    assert HEX40.match(candidate["candidate_sha"])
    assert HEX40.match(candidate["candidate_tree"])
    assert candidate["candidate_sha"] == candidate["server_revision"]
    assert candidate["client_revision"] == "39d4aeb20451c984e20d27e12270a15070adce12"
    assert HEX64.match(candidate["contract_digest"])
    assert HEX64.match(candidate["env_digest"])
    assert HEX64.match(candidate["harness_revision"])
    assert candidate["p1_candidate"]["modified"] is False
    assert candidate["moving_worktree_claimed"] is False
    assert candidate["integration_worktree_touched"] is False

    verification = candidate["overlay_verification"]
    assert verification["result"] == "PASS"
    assert verification["mismatches"] == []
    assert verification["extra_files"] == []
    assert verification["verified_files"] == verification["committed_files"]
    assert verification["committed_files"] > 0
    assert verification["tree_digest"] == candidate["candidate_revision"].split(":", 1)[1]

    manifest = candidate["candidate_files_manifest"]
    assert manifest["file_count"] == verification["committed_files"]
    assert _sha256(CANDIDATE_FILES) == manifest["sha256"]

    for entry in candidate["per_file_digests"]:
        assert HEX64.match(entry["sha256"]), entry["path"]
        assert entry["size_bytes"] == len(Path(entry["path"]).read_bytes())


@pytest.mark.skipif(not OVERLAY.is_dir(), reason="immutable overlay not materialized on this host")
def test_overlay_files_match_recorded_digests() -> None:
    candidate = _load(CANDIDATE)
    for entry in candidate["per_file_digests"]:
        relative = Path(entry["path"]).relative_to(OVERLAY)
        overlay_file = OVERLAY / relative
        assert overlay_file.is_file(), overlay_file
        assert _sha256(overlay_file) == entry["sha256"], entry["path"]


def test_rerun_commands_are_exact_and_placeholder_free() -> None:
    rerun = _load(RERUN)
    assert rerun["schema"] == "acp2.b20c.rerun-index.v1"
    assert rerun["commands"]

    payload = _canonical(rerun)
    assert "..." not in payload
    assert "<" not in payload

    for command in rerun["commands"]:
        argv = command["argv"]
        if argv is None:
            assert command.get("reason")
            assert command["result_expected"].startswith("NOT_VERIFIED")
            continue
        assert isinstance(argv, list) and argv
        assert all(isinstance(part, str) and part for part in argv)

    exec_command = next(c for c in rerun["commands"] if c["phase"] == "B20a-exec")
    assert exec_command["argv"] == [
        "/opt/acp2-dev/venvs/b/bin/python",
        "scripts/acceptance/acp2_runtime.py",
        "--scenario",
        "da-runtime",
        "--phase",
        "da-submit",
        "--controller",
        "http://127.0.0.1:18781",
        "--fixture",
        "/opt/acp2-dev/data/b/fixtures/da/da-run.json",
        "--out",
        "reports/acp2_implementation/bb45a46/B/B20",
        "--timeout",
        "14400",
    ]
    phases = {command["phase"] for command in rerun["commands"]}
    assert {"B20a-exec", "B20a-search", "B20b", "B20c-validation"} <= phases


def test_reusable_records_separate_facts_from_signoff() -> None:
    records = _load(REUSABLE)
    assert records["schema"] == "acp2.b20c.reusable-research-records.v1"
    reusable_kinds = {entry["kind"] for entry in records["reusable"]}
    assert "p2_candidate" in reusable_kinds
    assert "execution_facts" in reusable_kinds

    blocked = {entry["kind"]: entry["status"] for entry in records["not_reusable"]}
    assert blocked["a_scientific_signoff"] == "NOT_VERIFIED(a20_absent)"
    assert blocked["c_experience_signoff"] == "NOT_VERIFIED(c25_absent)"
    assert blocked["real_da_chain"] == "NOT_VERIFIED(c24a_absent)"
    assert blocked["search_evidence"] == "NOT_VERIFIED(search_not_declared)"
    assert blocked["supported_science_claims"] == "none"
    assert blocked["mechanism_conclusion"] == "not_claimed"
