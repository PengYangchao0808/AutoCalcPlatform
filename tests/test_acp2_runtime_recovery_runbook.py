"""B24b (todo 29) — H6 recovery/rollback runbook and freeze receipt.

Pins the published runbook and the H6 receipt to the frozen candidate:

* every fenced bash command carries a REVERSIBLE/IRREVERSIBLE label, contains no
  literal ``...`` placeholder and never restarts the production service;
* the oldest supported DB schema (21, upgrade 021->023) is stated;
* the four-combination run log matches the clean B-fix-7 rerun and the receipt;
* the legacy-reader result covers all seven ordinary workflows and the read-only
  compat tests;
* A/C readability is not fabricated: the contract references resolve, while the
  missing readability receipts stay ``NOT_VERIFIED(a_readability_absent)`` /
  ``NOT_VERIFIED(c_readability_absent)``;
* H6 stays OPEN with every required open row present, no production release and
  no production restart claim.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "docs" / "reference" / "next-change" / "acp2-contracts" / "runtime"
RUNBOOK = RUNTIME / "recovery-rollback-runbook.md"
EVIDENCE = REPO / "reports" / "acp2_implementation" / "bb45a46" / "B" / "B24b"
RECEIPT = EVIDENCE / "h6-receipt.json"
OPEN_ROWS = EVIDENCE / "open-rows.json"
VALIDATION = EVIDENCE / "runbook-command-validation.json"
RUN_LOG = EVIDENCE / "four-combination-run-log.json"
FOUR = EVIDENCE / "four-combination"
LEGACY = EVIDENCE / "legacy-reader-log.json"
AC = EVIDENCE / "ac-readability.json"
MIGRATION = EVIDENCE / "migration" / "summary.json"

H6_CANDIDATE = "493790709830eac4ce347b5fb81414050f7a1e21"
H6_CANDIDATE_TREE = "f0f940498b56bdf9377e49efb1b9a29e0e3a3d4a"
MIGRATION_CANDIDATE = "f32c6704e0348585a5a58ce8516450ab3afc7077"
LABELS = {"REVERSIBLE", "IRREVERSIBLE"}
LEGACY_WORKFLOWS = (
    "Confsearch",
    "PESsearch",
    "BatchOptimize",
    "XtbPathSearch",
    "XtbConstrainedOpt",
    "tsmode",
    "nmr",
)
REQUIRED_OPEN_ROWS = {
    "H6-LSF-live",
    "H6-DSH-live",
    "H6-C-readability",
    "H6-A-readability",
    "H6-selectivity-B23b",
    "H6-real-DA",
}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _command_blocks(text: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)```", text, re.DOTALL)


def _label(block: str) -> str | None:
    for line in block.splitlines():
        match = re.match(r"#\s*label:\s*(REVERSIBLE|IRREVERSIBLE)\s*$", line.strip())
        if match:
            return match.group(1)
    return None


def test_runbook_exists_and_freezes_the_candidate() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    assert H6_CANDIDATE in text
    assert MIGRATION_CANDIDATE in text
    assert "oldest supported" in text.lower()
    assert "schema 21" in text or "schema **21**" in text or "schema `21`" in text
    assert "021" in text and "022" in text and "023" in text
    assert "production restart" in text.lower()


def test_every_command_block_has_a_label_and_no_ellipsis() -> None:
    blocks = _command_blocks(RUNBOOK.read_text(encoding="utf-8"))
    assert len(blocks) >= 20
    for index, block in enumerate(blocks, 1):
        label = _label(block)
        assert label in LABELS, f"command block {index} has no REVERSIBLE/IRREVERSIBLE label"
        assert "..." not in block, f"command block {index} contains a literal ellipsis"
        assert not re.search(r"systemctl\s+(restart|reload|stop|start)\s+acp", block), (
            f"command block {index} restarts the production unit"
        )
        assert "port 8765" not in block or "grep" in block, (
            f"command block {index} targets the production port"
        )


def test_runbook_labels_cover_irreversible_steps() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    labels = [_label(block) for block in _command_blocks(text)]
    assert labels.count("IRREVERSIBLE") >= 6
    assert labels.count("REVERSIBLE") >= 10
    # The irreversible summary must name restore, interrupted migration, lost
    # worker, cursor, worktree and scratch deletion.
    for expected in (
        "restore",
        "interrupted migration",
        "lost-worker",
        "cursor",
        "worktree",
        "scratch",
    ):
        assert expected.lower() in text.lower(), f"runbook misses irreversible step: {expected}"


def test_command_validation_matches_a_fresh_scan() -> None:
    validation = _read_json(VALIDATION)
    blocks = _command_blocks(RUNBOOK.read_text(encoding="utf-8"))
    assert validation["schema"] == "acp2.b24b.runbook_command_validation.v1"
    assert validation["command_block_count"] == len(blocks)
    assert validation["labelled_count"] == len(blocks)
    assert validation["unlabelled_indices"] == []
    assert validation["literal_ellipsis_indices"] == []
    assert validation["production_restart_indices"] == []
    assert validation["oldest_supported_schema"] == 21
    assert validation["upgrade_path"] == "021->022->023"
    assert validation["production_restart_executed"] is False
    assert validation["runbook_sha256"] == hashlib.sha256(RUNBOOK.read_bytes()).hexdigest()


def test_h6_receipt_is_open_and_never_claims_a_release() -> None:
    receipt = _read_json(RECEIPT)
    assert receipt["schema"] == "acp2.h6.receipt.v1"
    assert receipt["h6_status"] == "OPEN"
    assert receipt["frozen_candidate_sha"] == H6_CANDIDATE
    assert receipt["candidate"]["server_revision"] == H6_CANDIDATE
    assert receipt["candidate"]["server_tree"] == H6_CANDIDATE_TREE
    assert receipt["candidate"]["clean_worktree_status_porcelain"] == []
    assert "candidate_runnable_overlay_path" not in receipt["candidate"]
    overlay = receipt["candidate"]["historical_b24b_overlay"]
    assert overlay["superseded_by"] == H6_CANDIDATE
    assert (
        overlay["sha256"]
        == hashlib.sha256((EVIDENCE / "candidate-runnable-overlay.patch").read_bytes()).hexdigest()
    )
    assert receipt["production_release"] is False
    assert receipt["production_restart_executed"] is False
    assert receipt["signed_by"] == []
    assert receipt["contract"]["contract_digest"].startswith("sha256:")
    assert len(receipt["contract"]["contract_digest"]) == len("sha256:") + 64
    b_rows = {row["row_id"]: row for row in receipt["b_owned_rows"]}
    assert b_rows["H6-B1-migration-backup-restore"]["status"] == "PASS"
    assert b_rows["H6-B2-recovery-runbook"]["status"] == "PASS"
    assert b_rows["H6-B3-four-combination-candidate"]["status"] == "PASS"
    assert b_rows["H6-B4-legacy-readers"]["status"] == "PASS"
    assert b_rows["H6-B6-no-production-action"]["status"] == "PASS"


def test_open_rows_are_complete_and_unsigned() -> None:
    receipt = _read_json(RECEIPT)
    open_rows = _read_json(OPEN_ROWS)
    assert open_rows["schema"] == "acp2.h6.open_rows.v1"
    assert open_rows["h6_status"] == "OPEN"
    assert open_rows["frozen_candidate_sha"] == H6_CANDIDATE
    ids = {row["row_id"] for row in open_rows["rows"]}
    assert REQUIRED_OPEN_ROWS <= ids, f"missing open rows: {sorted(REQUIRED_OPEN_ROWS - ids)}"
    assert {row["row_id"] for row in receipt["open_rows"]} == ids
    for row in open_rows["rows"]:
        assert row["status"].startswith("NOT_VERIFIED")
        assert row["reason"].strip()
        assert row["owner"] in {"A", "B", "C"}


def test_a_c_readability_is_not_fabricated() -> None:
    ac = _read_json(AC)
    assert ac["a"]["result"] == "NOT_VERIFIED(a_readability_absent)"
    assert ac["a"]["contract_handoff"]["present"] is True
    assert ac["a"]["contract_handoff"]["parsed"] is True
    assert ac["a"]["readability_receipt_present"] is False
    assert ac["a"]["expected_readability_receipt"].endswith("A/A-26/result.json")
    assert ac["c"]["result"] == "NOT_VERIFIED(c_readability_absent)"
    for key in ("research_record_schema", "view_model_schema", "manifest_extension_schema"):
        assert ac["c"][key]["present"] is True
        assert ac["c"][key]["parsed"] is True
    assert ac["c"]["readability_receipt_present"] is False
    assert "cross-domain-live" in ac["c"]["expected_readability_receipt"]


def test_four_combination_results_match_the_run_log() -> None:
    run_log = _read_json(RUN_LOG)
    assert run_log["frozen_candidate_sha"] == H6_CANDIDATE
    assert run_log["candidate"]["server_revision"] == H6_CANDIDATE
    assert run_log["candidate"]["server_tree"] == H6_CANDIDATE_TREE
    assert run_log["candidate"]["clean_worktree_status_porcelain"] == []
    assert run_log["candidate"]["candidate_patch"] is None
    assert run_log["harness"]["revision"] == "a44e39a73960b735556dfc47608210300c4c6d95"
    assert run_log["harness"]["status_porcelain"] == []
    assert run_log["exit_code"] == 0
    assert run_log["summary"]["result"] == "PASS"
    by_cell = {cell["cell"]: cell for cell in run_log["cells"]}
    assert by_cell["F0/S1"]["result"] == "PASS"
    assert by_cell["F1/S1"]["result"] == "PASS"
    for cell, name in (("F0/S1", "F0_S1"), ("F1/S1", "F1_S1")):
        document = _read_json(FOUR / f"{name}.json")
        assert document["result"] == "PASS", cell
        assert document["candidate_revision"] == H6_CANDIDATE
        assert document["candidate_patch_sha256"] is None
        legacy = {row["workflow"]: row["result"] for row in document["cell"]["legacy_ordinary"]}
        assert legacy == by_cell[cell]["legacy_readers"]
        assert set(legacy) == set(LEGACY_WORKFLOWS)
        assert all(result == "PASS" for result in legacy.values())
        assert document["cell"]["browser"]["result"] == "PASS"
    # B-fix-8: an executed S0 failure stays FAIL; S0 is out of H6 scope but is
    # never rewritten as NOT_VERIFIED and no S0 closure is claimed.
    assert run_log["s0_cells"]["F0/S0"] == "FAIL"
    assert run_log["s0_cells"]["F1/S0"] == "FAIL"
    assert run_log["s0_cells"]["scope"] == "NOT_APPLICABLE"
    assert run_log["s0_cells"]["raw_evidence"].endswith(
        "B24/four-combination-all-combos-raw/summary.json"
    )
    raw = _read_json(REPO / run_log["s0_cells"]["raw_evidence"])
    raw_cells = {row["scenario"]: row["result"] for row in raw["scenarios"]}
    assert raw_cells["F0/S0"] == "FAIL"
    assert raw_cells["F1/S0"] == "FAIL"
    assert (
        run_log["historical_candidate_overlay"]["sha256"]
        == hashlib.sha256((EVIDENCE / "candidate-runnable-overlay.patch").read_bytes()).hexdigest()
    )
    assert run_log["historical_candidate_overlay"]["superseded_by"] == H6_CANDIDATE


def test_legacy_readers_and_migration_are_bound_to_the_frozen_sha() -> None:
    legacy = _read_json(LEGACY)
    assert legacy["result"] == "PASS"
    assert legacy["candidate_sha"] == H6_CANDIDATE
    assert legacy["candidate_checkout"]["status_porcelain"] == []
    assert legacy["compat_reader_pytest"]["passed"] == 41
    assert legacy["compat_reader_pytest"]["failed"] == 0
    assert set(legacy["ordinary_legacy_cli"][0]["legacy_readers"]) == set(LEGACY_WORKFLOWS)
    migration = _read_json(MIGRATION)
    assert migration["candidate_revision"] == MIGRATION_CANDIDATE
    assert migration["schema"] == "acp2.acceptance.summary.v1"


def test_runbook_does_not_claim_a_production_release() -> None:
    text = RUNBOOK.read_text(encoding="utf-8").lower()
    receipt = _read_json(RECEIPT)
    assert receipt["production_release"] is False
    assert receipt["production_restart_executed"] is False
    assert "production release: **false**" in text
    assert "production restart executed: **false**" in text
    assert "no production restart" in text
    # A positive release/restart claim would use these phrases without a negation.
    assert "production release is complete" not in text
    assert "restarted the production" not in text
