"""M4-06a upgrade-pair drill: structural pins plus the slow end-to-end run.

The fast layer pins the harness contract that the evidence relies on:

* the pair is built by ``scripts/build_release.py`` and every
  ``release-manifest.json`` digest is re-verified against the bytes on disk;
* the drill refuses to touch the production run root;
* the state/probe helpers report schema versions and table digests truthfully;
* the **admission-DB upgrade ordering fix** is real: a pre-M0-07 admission DB
  (no ``job_id`` column on ``admission_usage_receipts``) upgrades without
  "no such column" and keeps its historical usage row exactly as recorded.

The slow layer runs the real acceptance harness
(``scripts/acceptance/acp2_upgrade_pair.py``) against the pre-built artifact
pair, which needs two release builds and two venvs.  It is skipped unless the
artifact store exists (``ACP2_UPGRADE_PAIR_ARTIFACTS`` overrides the default).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.acceptance import acp2_upgrade_pair as harness
from scripts.acceptance import acp2_upgrade_pair_helper as helper

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = Path("/ACPServer/acp2-artifacts/remediation/m4-06a")


# ---------------------------------------------------------------------------
# Fast layer: harness contract
# ---------------------------------------------------------------------------


def test_evidence_kind_is_fixture_aggregate() -> None:
    assert harness.EVIDENCE_KIND == "fixture+aggregate"
    assert harness.SCHEMA == "acp2.upgrade-pair.v1"
    assert harness.PREVIOUS_REF == "acp2-baseline-20261026"


def test_harness_never_targets_the_production_run_root(tmp_path: Path) -> None:
    production = Path("/ACPServer/acp2-runs/remediation")
    assert harness._forbidden_production_root(production) == production
    assert harness._forbidden_production_root(production / "run_root") == production
    assert harness._forbidden_production_root(Path("/ACPServer/acp2-runs")) is not None
    # An isolated work dir is allowed (and so is a look-alike sibling).
    assert harness._forbidden_production_root(tmp_path / "run_root") is None
    assert harness._forbidden_production_root(Path("/ACPServer/acp2-runs-other")) is None


def test_release_manifest_digests_are_verified_not_trusted(tmp_path: Path) -> None:
    directory = tmp_path / "release"
    directory.mkdir()
    artifact = directory / "cccp-0.3.0-py3-none-any.whl"
    artifact.write_bytes(b"wheel-bytes")
    import hashlib

    digest = hashlib.sha256(b"wheel-bytes").hexdigest()
    document = {
        "schema": "acp2.release.manifest.v1",
        "version": "0.3.0",
        "source": {"git": True, "revision": "abc", "clean": True},
        "roles": {"cccp": {}, "worker": {}, "web": {}},
        "artifacts": {artifact.name: digest},
    }
    (directory / "release-manifest.json").write_text(json.dumps(document), encoding="utf-8")
    verified = harness._verify_release_manifest(directory)
    assert verified["digest_ok"] is True
    assert verified["roles"] == ["cccp", "web", "worker"]

    artifact.write_bytes(b"tampered-bytes")
    tampered = harness._verify_release_manifest(directory)
    assert tampered["digest_ok"] is False
    assert tampered["digest_failing"]


def test_release_manifest_requires_the_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        harness._verify_release_manifest(tmp_path)


def test_table_state_and_schema_versions_are_truthful(tmp_path: Path) -> None:
    run_root = tmp_path / "run_root"
    run_root.mkdir()
    db = run_root / "acp_jobs.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("CREATE TABLE _schema_migrations (id TEXT PRIMARY KEY, applied_at TEXT)")
        conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT)")
        conn.execute("INSERT INTO _schema_migrations VALUES ('001','now')")
        conn.execute("INSERT INTO jobs VALUES ('j1','completed')")
        conn.commit()
    finally:
        conn.close()
    state = harness._schema_versions(run_root)
    assert state["jobs"]["count"] == 1
    assert state["jobs"]["ids"] == ["001"]
    assert state["jobs"]["integrity"] == "ok"
    assert state["prepare"] == 0
    tables = harness._table_state(db, harness.JOBS_TABLES)
    assert tables["counts"] == {"jobs": 1}
    assert tables["present"] is True


def test_copy_tree_is_byte_identical(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "tasks").mkdir(parents=True)
    (source / "tasks" / "one.json").write_text("{}", encoding="utf-8")
    (source / "acp_jobs.db").write_bytes(b"sqlite-ish")
    target = tmp_path / "target"
    harness._copy_tree(source, target)
    assert harness._tree_digests(source) == harness._tree_digests(target)


def test_assertion_summary_fails_on_any_failure() -> None:
    assertions = harness.Assertions()
    assertions.add("a", True, "")
    assert assertions.summary()["status"] == "PASS"
    assertions.add("b", False, {"why": "x"})
    summary = assertions.summary()
    assert summary["status"] == "FAIL"
    assert summary["failing"] == ["b"]
    assert summary["passed"] == 1 and summary["failed"] == 1


def test_mcp_refusal_parser_reads_the_typed_error() -> None:
    response = {
        "call": {
            "result": {
                "structuredContent": {
                    "error": {
                        "code": "input_invalid",
                        "params": {"rule": "idempotency_key_required"},
                    }
                }
            }
        }
    }
    code, params = harness._mcp_error_code(response)
    assert code == "input_invalid"
    assert params["rule"] == "idempotency_key_required"


def test_helper_legacy_constants_are_the_frozen_shapes() -> None:
    assert helper.COMMITTED_KEY == "legacy-submit-key"
    assert helper.UNSCOPED_SCOPE == "legacy-unscoped"
    assert helper.PROJECT_ID == "uncategorized"
    # The seed payload must stay inside the frozen v1 digest field table.
    from acp_services.runtime.idempotency import LEGACY_REQUEST_DIGEST_FIELDS

    payload = helper._submit_payload("legacy_commit_job", "O")
    assert set(payload) <= set(LEGACY_REQUEST_DIGEST_FIELDS)


# ---------------------------------------------------------------------------
# Fast layer: the admission-DB upgrade ordering fix (M4-06a finding)
# ---------------------------------------------------------------------------

#: The pre-M0-07 admission schema, copied from the frozen baseline tag
#: (``acp2-baseline-20261026``) — the old ``admission_usage_receipts`` has no
#: ``job_id``/``attempt`` columns, which is exactly what broke the candidate's
#: startup when the index was created before the additive columns.
_LEGACY_ADMISSION_DDL = """
CREATE TABLE admission_reservations (
    reservation_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL, project_id TEXT NOT NULL,
    key_id TEXT, job_id TEXT NOT NULL, state TEXT NOT NULL, cores INTEGER NOT NULL DEFAULT 0,
    memory_mb INTEGER NOT NULL DEFAULT 0, storage_bytes INTEGER NOT NULL DEFAULT 0,
    duration_s INTEGER NOT NULL DEFAULT 0, queue TEXT, node TEXT, policy_version TEXT NOT NULL,
    actual_cores INTEGER, actual_memory_mb INTEGER, actual_storage_bytes INTEGER,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE admission_usage_receipts (
    usage_id TEXT PRIMARY KEY, reservation_id TEXT NOT NULL, job_id TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1, cores INTEGER NOT NULL DEFAULT 0,
    seconds INTEGER NOT NULL DEFAULT 0, core_hours TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE INDEX idx_admission_usage_reservation ON admission_usage_receipts(reservation_id);
CREATE TABLE research_budget_receipts (
    receipt_id TEXT PRIMARY KEY, budget_id TEXT NOT NULL, reservation_id TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '', usage_json TEXT NOT NULL, trusted_source TEXT NOT NULL,
    applied INTEGER NOT NULL DEFAULT 0, recorded_at TEXT NOT NULL
);
"""


def _legacy_admission_db(tmp_path: Path) -> Path:
    db = tmp_path / "acp_admission.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(_LEGACY_ADMISSION_DDL)
        conn.execute(
            "INSERT INTO admission_usage_receipts "
            "(usage_id, reservation_id, job_id, attempt, cores, seconds, core_hours, recorded_at) "
            "VALUES ('usage_legacy_1','res_1','job_1',1,2,60,'0.033','2026-01-01T00:00:00Z')"
        )
        conn.commit()
    finally:
        conn.close()
    return db


def test_legacy_admission_db_upgrades_without_column_error(tmp_path: Path) -> None:
    from acp_services.runtime.admission import ReservationLedger

    db = _legacy_admission_db(tmp_path)
    ledger = ReservationLedger(str(db))  # must not raise "no such column: job_id"
    receipts = ledger.usage_receipts()
    assert [row["usage_id"] for row in receipts] == ["usage_legacy_1"]
    assert receipts[0]["trusted_source"] == ""
    assert receipts[0]["chain_digest"] == ""
    totals = ledger.usage_totals()
    assert totals["receipts"] == 1
    assert totals["core_seconds"] == 2 * 60

    conn = sqlite3.connect(str(db))
    try:
        version = conn.execute("SELECT MAX(id) FROM admission_schema_migrations").fetchone()[0]
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name IN ('admission_usage_receipts','research_budget_receipts')"
            )
        }
    finally:
        conn.close()
    assert version == 26
    assert "idx_admission_usage_job" in indexes
    assert "idx_research_budget_receipts_job" in indexes


def test_historical_usage_is_reported_unverified_not_fabricated(tmp_path: Path) -> None:
    from acp_services.runtime.admission import ReservationLedger

    ledger = ReservationLedger(str(_legacy_admission_db(tmp_path)))
    history = ledger.verify_usage_history("job_1")
    assert history["verified"] is False
    assert [problem["code"] for problem in history["problems"]] == ["binding_missing"]
    assert history["receipts"][0]["usage_id"] == "usage_legacy_1"
    assert history["receipts"][0]["trusted_source"] == ""


def test_fresh_admission_db_still_gets_the_same_indexes(tmp_path: Path) -> None:
    from acp_services.runtime.admission import ReservationLedger

    db = tmp_path / "fresh.db"
    ReservationLedger(str(db))
    conn = sqlite3.connect(str(db))
    try:
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name IN ('admission_usage_receipts','research_budget_receipts')"
            )
        }
    finally:
        conn.close()
    assert {"idx_admission_usage_job", "idx_research_budget_receipts_job"} <= indexes


# ---------------------------------------------------------------------------
# Slow layer: the real upgrade pair
# ---------------------------------------------------------------------------


def _slow_artifact_root() -> Path:
    import os

    return Path(os.environ.get("ACP2_UPGRADE_PAIR_ARTIFACTS", str(ARTIFACT_ROOT)))


@pytest.mark.slow
def test_upgrade_pair_drill_end_to_end(tmp_path: Path) -> None:
    """Real previous-artifact -> candidate upgrade, rollback and interruption."""
    artifact_root = _slow_artifact_root()
    for side in ("previous", "candidate"):
        manifest = artifact_root / side / "release-manifest.json"
        if not manifest.is_file():
            pytest.skip(
                f"upgrade-pair artifacts absent at {manifest}; build them with "
                "scripts/build_release.py or set ACP2_UPGRADE_PAIR_ARTIFACTS"
            )
    candidate_source = artifact_root / "candidate-src"
    out = tmp_path / "evidence"
    argv = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "acceptance" / "acp2_upgrade_pair.py"),
        "--out",
        str(out),
        "--artifact-root",
        str(artifact_root),
        "--work-dir",
        str(tmp_path / "work"),
        "--reuse-artifacts",
    ]
    if candidate_source.is_dir():
        argv.extend(["--candidate-source", str(candidate_source)])
    completed = subprocess.run(
        argv, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=3600, check=False
    )
    summary_path = out / "summary.json"
    assert summary_path.is_file(), completed.stderr[-2000:]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["status"] == "PASS", summary.get("assertions")
    assert summary["evidence_kind"] == "fixture+aggregate"
    assert summary["not_production_data"] is True
    for name in (
        "upgrade-trace.json",
        "rollback-trace.json",
        "failure-injection-trace.json",
        "legacy-semantics.json",
    ):
        assert (out / name).is_file(), name
    failing = [row for row in summary["assertions"] if row["status"] != "PASS"]
    assert failing == []
