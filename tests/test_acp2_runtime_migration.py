"""B24a — migration, backup/restore and recovery drill acceptance.

The harness scenario is stdlib-only and copies the frozen recovery fixtures
into an isolated workspace; it never mutates the originals.  These tests pin
the fixture resolver, the corrupt-artifact reporter, the snapshot comparator
and one real end-to-end run (seed → backup → migrate → restore → drills) using
the repository's actual Services code through the helper subprocess.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.acceptance import acp2_runtime as harness

SOURCE_FIXTURES = Path(__file__).resolve().parent / "baseline" / "recovery_fixtures"


def _fixture(tmp_path: Path, *, workspace: Path | None = None) -> Path:
    document = {
        "schema": "acp2.migration.fixture.v1",
        "fixture_id": "fx-migration-test",
        "source_fixtures": str(SOURCE_FIXTURES),
        "workspace_root": str(workspace or tmp_path / "workspace"),
        "target_schema_version": 24,
        "legacy_schema_version": 21,
        "corrupt_artifact": "historical_manifest/RESULT_v2/corrupt_manifest.json",
    }
    path = tmp_path / "migration.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_fixture_resolver_accepts_file_and_directory(tmp_path: Path) -> None:
    path = _fixture(tmp_path)
    resolved = harness._migration_fixture(str(path))
    assert resolved["fixture_id"] == "fx-migration-test"

    directory = tmp_path / "migration"
    directory.mkdir()
    shutil.copy2(path, directory / "migration.json")
    assert harness._migration_fixture(str(directory))["fixture_id"] == "fx-migration-test"


def test_fixture_resolver_rejects_wrong_schema(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schema": "acp2.other.v1"}), encoding="utf-8")
    with pytest.raises(ValueError):
        harness._migration_fixture(str(path))


def test_corrupt_artifact_is_reported_not_skipped(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "good.json").write_text("{}", encoding="utf-8")
    (root / "old.json").write_text('{"truncated":', encoding="utf-8")
    corrupt = harness._migration_corrupt_artifacts(root)
    assert [row["path"] for row in corrupt] == ["old.json"]
    assert corrupt[0]["error"].startswith("JSONDecodeError")


def test_snapshot_comparator_detects_count_and_digest_changes() -> None:
    before = {"jobs_db": {"counts": {"approval_records": 3}, "schema_version": 23}}
    same = {"jobs_db": {"counts": {"approval_records": 3}, "schema_version": 23}}
    changed = {"jobs_db": {"counts": {"approval_records": 2}, "schema_version": 23}}
    assert harness._migration_compare(before, same)["equal"] is True
    comparison = harness._migration_compare(before, changed)
    assert comparison["equal"] is False
    assert "approval_records" in comparison["differences"][0]


def test_migration_evidence_kind_is_fixture() -> None:
    assert harness._evidence_kind_for_phase("main", "migration") == "fixture"
    assert harness._evidence_kind_for_phase("main", "local-singlepoint") == "live"


def test_unowned_workspace_is_refused(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "do-not-delete.txt").write_text("keep", encoding="utf-8")
    code, assertions, extensions = harness._run_migration(
        str(_fixture(tmp_path, workspace=workspace)), tmp_path / "out", 60
    )
    assert code == harness.EXIT_FAIL
    assert extensions["b24.reason"] == "workspace_not_owned"
    assert (workspace / "do-not-delete.txt").is_file()
    assert assertions[0]["name"] == "b24.workspace_owned"


@pytest.mark.parametrize("timeout", [300])
def test_migration_scenario_end_to_end(tmp_path: Path, timeout: float) -> None:
    source_before = harness._migration_file_digests(SOURCE_FIXTURES)
    out = tmp_path / "out"
    code, assertions, extensions = harness._run_migration(str(_fixture(tmp_path)), out, timeout)
    assert code == harness.EXIT_PASS, [
        (row["name"], row["detail"]) for row in assertions if row["status"] != "PASS"
    ]
    assert all(row["status"] == "PASS" for row in assertions)
    names = {row["name"] for row in assertions}
    for required in (
        "b24.source_fixtures_read_only",
        "b24.backup.restorable_before_migration",
        "b24.migration.candidate_schema",
        "b24.restore.counts_digests_schema_equal",
        "b24.corrupt_artifact.reported_not_skipped",
        "b24.interrupted_migration.readable_attributed",
        "b24.interrupted_migration.restored",
        "b24.legacy.upgrade",
        "b24.drill.lost_worker.no_terminal_revival",
        "b24.drill.old_receipt.conservative_recompute",
        "b24.drill.duplicate_receipt.idempotent",
        "b24.drill.publication_pending.defined_state",
        "b24.drill.cursor_expiry.no_duplicate",
        "b24.approval_budget_idempotency_preserved",
        "b24.schema_version_matches_fixture",
    ):
        assert required in names

    assert harness._migration_file_digests(SOURCE_FIXTURES) == source_before
    for name in (
        "source-digests.json",
        "migration-log.json",
        "restore-comparison.json",
        "drill-receipts.json",
        "corrupt-artifacts.json",
        "verify-rows.json",
    ):
        assert (out / name).is_file()

    restore = json.loads((out / "restore-comparison.json").read_text(encoding="utf-8"))
    assert restore["before_migration_backup_restore"]["equal"] is True
    assert restore["post_migration_restore"]["equal"] is True
    assert restore["interrupted_migration_restore"]["equal"] is True

    drills = json.loads((out / "drill-receipts.json").read_text(encoding="utf-8"))
    assert drills["lost_worker"]["terminal_revival_blocked"] is True
    assert drills["lost_worker"]["terminal_unchanged"] is True
    assert drills["old_duplicate_receipt"]["old_v1"]["default_adopted"] is False
    assert drills["publication_pending"]["qc_calls"] == 0
    assert drills["cursor_expiry"]["no_duplicate_row"] is True

    verify = json.loads((out / "verify-rows.json").read_text(encoding="utf-8"))
    assert verify["approval"]["state"] == "approved"
    assert verify["idempotency"]["state"] == "committed"
    assert verify["budget"]["budget_id"] == "mig-budget"
