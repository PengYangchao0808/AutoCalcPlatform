"""B02-7 (todo 17) — per-entry authorization + storage budget reconciliation.

Two independent deliverables are proven here:

1. Every real resource entry (download / log / range / frame candidates /
   structure sources / cache read / event stream) is authorized at the point of
   effect via the frozen identity model (``resolve_scope`` +
   ``LocalSingleAccessPolicy``).  An authorized principal is allowed on all
   seven; a forged cross-tenant scope, a missing explicit grant and a revoked
   grant are denied on all seven; an unknown entry is denied (fail closed).
   The endpoint binding is real (the gate runs before the endpoint body), and a
   denial changes no state.  Authorization is re-checked before a download and
   before a publish — an SFTP rejection is *not* a Workspace revocation.

2. Storage reservations account bytes / inodes / scratch / upload / temporary
   amplification, count the peak as temporary + final files together, and
   reconcile against real hard limits.  With no hard limit the report says
   ``hard_limits: unavailable`` (never a fabricated quota); a prefix is never a
   quota; a refused reservation never deletes completed results.

Run with: pytest tests/test_services_entry_authz.py -q
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp_services.contracts.access import (
    LocalSingleAccessPolicy,
    Principal,
    ResourceGrant,
)
from acp_services.data.budget import (
    CAPABILITY_ENFORCED,
    CAPABILITY_UNAVAILABLE,
    HARD_LIMITS_UNAVAILABLE,
    PEAK_DEFINITION,
    BudgetDimension,
    HardLimits,
    StorageBudgetLedger,
    StorageBudgetRequest,
)
from acp_services.workspace.entry_authz import (
    ENTRY_HTTP_FORBIDDEN,
    ENTRY_HTTP_OK,
    EntryAuthorizer,
    EntryClass,
    EntryReasonCode,
    default_entry_authorizer,
)

ENTRIES = (
    EntryClass.DOWNLOAD,
    EntryClass.LOG,
    EntryClass.RANGE,
    EntryClass.FRAME_CANDIDATES,
    EntryClass.STRUCTURE_SOURCES,
    EntryClass.CACHE_READ,
    EntryClass.EVENT_STREAM,
)

PROJECT = "proj-a"
JOB_ID = "authz-job-1"


# ---------------------------------------------------------------------------
# Module-level: the seven-entry authorization matrix
# ---------------------------------------------------------------------------


def test_exactly_seven_entry_classes() -> None:
    assert len(EntryClass.ALL) == 7
    assert set(ENTRIES) == set(EntryClass.ALL)


def _forged_principal() -> Principal:
    return Principal(principal_id="principal_forged", kind="user", tenant_id="other-tenant")


def _forged_authorizer() -> EntryAuthorizer:
    # The project is known to belong to the local tenant; a principal from a
    # different tenant forging that project as its own scope is denied.
    return EntryAuthorizer(
        principal=_forged_principal(), known_projects={PROJECT: "local-single"}
    )


def _grant(resource: str = PROJECT) -> ResourceGrant:
    return ResourceGrant(
        grant_id="g1",
        principal_id="principal_local",
        scope_id=resource,
        resource_id=resource,
        actions=("read", "write", "control"),
        granted_at="2026-10-18T00:00:00+00:00",
    )


@pytest.mark.parametrize("entry", ENTRIES)
def test_authorized_principal_allows_every_entry(entry: str) -> None:
    decision = default_entry_authorizer().authorize(entry, job_id=JOB_ID, project_id=PROJECT)
    assert decision.allowed is True
    assert decision.http_status == ENTRY_HTTP_OK
    assert decision.reason_code == EntryReasonCode.ALLOWED


@pytest.mark.parametrize("entry", ENTRIES)
def test_forged_cross_tenant_scope_denies_every_entry(entry: str) -> None:
    decision = _forged_authorizer().authorize(entry, job_id=JOB_ID, project_id=PROJECT)
    assert decision.allowed is False
    assert decision.http_status == ENTRY_HTTP_FORBIDDEN
    assert decision.reason_code == EntryReasonCode.SCOPE_MISMATCH
    assert decision.error_detail()["code"] == EntryReasonCode.SCOPE_MISMATCH


@pytest.mark.parametrize("entry", ENTRIES)
def test_missing_explicit_grant_denies_every_entry(entry: str) -> None:
    authorizer = EntryAuthorizer(require_grant=True, policy=LocalSingleAccessPolicy())
    decision = authorizer.authorize(
        entry, job_id=JOB_ID, project_id=PROJECT, resource_id=PROJECT
    )
    assert decision.allowed is False
    assert decision.http_status == ENTRY_HTTP_FORBIDDEN


@pytest.mark.parametrize("entry", ENTRIES)
def test_live_explicit_grant_allows_every_entry(entry: str) -> None:
    authorizer = EntryAuthorizer(
        require_grant=True, policy=LocalSingleAccessPolicy(grants=[_grant()])
    )
    decision = authorizer.authorize(
        entry, job_id=JOB_ID, project_id=PROJECT, resource_id=PROJECT
    )
    assert decision.allowed is True


@pytest.mark.parametrize("entry", ENTRIES)
def test_revoked_grant_denies_every_entry(entry: str) -> None:
    policy = LocalSingleAccessPolicy(grants=[_grant()])
    policy.revoke("g1")
    authorizer = EntryAuthorizer(require_grant=True, policy=policy)
    decision = authorizer.authorize(
        entry, job_id=JOB_ID, project_id=PROJECT, resource_id=PROJECT
    )
    assert decision.allowed is False
    assert decision.http_status == ENTRY_HTTP_FORBIDDEN
    assert decision.reason_code == EntryReasonCode.REVOKED


def test_write_action_requires_write_capability() -> None:
    # A read-only grant cannot perform the frame-candidate publish (write).
    read_only = ResourceGrant(
        grant_id="g-read",
        principal_id="principal_local",
        scope_id=PROJECT,
        resource_id=PROJECT,
        actions=("read",),
        granted_at="2026-10-18T00:00:00+00:00",
    )
    authorizer = EntryAuthorizer(
        require_grant=True, policy=LocalSingleAccessPolicy(grants=[read_only])
    )
    denied = authorizer.authorize(
        EntryClass.FRAME_CANDIDATES, job_id=JOB_ID, project_id=PROJECT, action="write"
    )
    assert denied.allowed is False
    allowed = authorizer.authorize(
        EntryClass.FRAME_CANDIDATES, job_id=JOB_ID, project_id=PROJECT, action="read"
    )
    assert allowed.allowed is True


def test_unknown_entry_denied_fail_closed() -> None:
    decision = default_entry_authorizer().authorize("not-a-real-entry", job_id=JOB_ID)
    assert decision.allowed is False
    assert decision.http_status == ENTRY_HTTP_FORBIDDEN
    assert decision.reason_code == EntryReasonCode.UNKNOWN_ENTRY


# ---------------------------------------------------------------------------
# Endpoint binding (real per-entry checks, not list-page filtering)
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1)) as test_client:
        yield test_client


def _seed_job(client: TestClient, tmp_path: Path) -> str:
    manager = client.app.state.job_manager
    work_dir = tmp_path / JOB_ID
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "note.txt").write_text("hello", encoding="utf-8")
    manager.store.create(
        JobRecord(
            id=JOB_ID,
            spec=JobSpec(workflow="optimize", name="authz", project_id=PROJECT),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            project_id=PROJECT,
        )
    )
    return JOB_ID


def _entry_paths(job_id: str) -> dict[str, str]:
    return {
        EntryClass.DOWNLOAD: f"/api/v1/jobs/{job_id}/files/note.txt",
        EntryClass.LOG: f"/api/v1/jobs/{job_id}/logs",
        EntryClass.RANGE: f"/api/v1/jobs/{job_id}/prepared/frame_candidates?offset=0&limit=10",
        EntryClass.FRAME_CANDIDATES: f"/api/v1/jobs/{job_id}/frame-candidates",
        EntryClass.STRUCTURE_SOURCES: "/api/v2/structure-sources?project_id=proj-a",
        EntryClass.CACHE_READ: f"/api/v1/jobs/{job_id}/availability",
        EntryClass.EVENT_STREAM: f"/api/v1/jobs/{job_id}/events",
    }


def _status(client: TestClient, path: str) -> int:
    """GET *path*; read the SSE endpoint without consuming the endless stream."""
    if path.endswith("/events"):
        with client.stream("GET", path) as response:
            return response.status_code
    return client.get(path).status_code


@pytest.mark.parametrize("entry", ENTRIES)
def test_endpoint_authorized_allows(
    client: TestClient, tmp_path: Path, entry: str
) -> None:
    job_id = _seed_job(client, tmp_path)
    status = _status(client, _entry_paths(job_id)[entry])
    assert status != ENTRY_HTTP_FORBIDDEN
    assert status < 500


@pytest.mark.parametrize("entry", ENTRIES)
def test_endpoint_unauthorized_denies(
    client: TestClient, tmp_path: Path, entry: str
) -> None:
    job_id = _seed_job(client, tmp_path)
    client.app.state.entry_authorizer = EntryAuthorizer(
        require_grant=True, policy=LocalSingleAccessPolicy()
    )
    status = _status(client, _entry_paths(job_id)[entry])
    assert status == ENTRY_HTTP_FORBIDDEN


def test_endpoint_forged_scope_denies_with_error_contract(
    client: TestClient, tmp_path: Path
) -> None:
    job_id = _seed_job(client, tmp_path)
    client.app.state.entry_authorizer = _forged_authorizer()
    response = client.get(f"/api/v1/jobs/{job_id}/files/note.txt")
    assert response.status_code == ENTRY_HTTP_FORBIDDEN
    detail = response.json()["detail"]
    assert detail["code"] == EntryReasonCode.SCOPE_MISMATCH
    assert detail["message"]


def test_download_and_log_are_reauthorized_per_request(
    client: TestClient, tmp_path: Path
) -> None:
    job_id = _seed_job(client, tmp_path)
    # Allowed under the default local-single authorizer.
    assert client.get(f"/api/v1/jobs/{job_id}/files/note.txt").status_code == 200
    assert client.get(f"/api/v1/jobs/{job_id}/logs").status_code == 200
    # The next request is re-checked: swapping in a denying policy revokes both.
    client.app.state.entry_authorizer = EntryAuthorizer(
        require_grant=True, policy=LocalSingleAccessPolicy()
    )
    assert client.get(f"/api/v1/jobs/{job_id}/files/note.txt").status_code == 403
    assert client.get(f"/api/v1/jobs/{job_id}/logs").status_code == 403


def test_publish_is_reauthorized_before_write(
    client: TestClient, tmp_path: Path
) -> None:
    job_id = _seed_job(client, tmp_path)
    client.app.state.entry_authorizer = EntryAuthorizer(
        require_grant=True, policy=LocalSingleAccessPolicy()
    )
    response = client.post(
        f"/api/v1/jobs/{job_id}/frame-candidate",
        json={"view_type": "scan", "frame_index": 0, "role": "TS"},
    )
    assert response.status_code == ENTRY_HTTP_FORBIDDEN
    authority = tmp_path / job_id / "RESULT" / "frame_candidates.json"
    assert not authority.exists()


def _table_count(db_path: Path, table: str) -> int:
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0]) if row is not None else 0
    finally:
        connection.close()


def _table_exists(db_path: Path, table: str) -> bool:
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None
    finally:
        connection.close()


def test_unauthorized_access_changes_no_state(
    client: TestClient, tmp_path: Path
) -> None:
    job_id = _seed_job(client, tmp_path)
    manager = client.app.state.job_manager
    db_path = Path(manager.store.db_path)
    jobs_before = _table_count(db_path, "jobs")
    client.app.state.entry_authorizer = EntryAuthorizer(
        require_grant=True, policy=LocalSingleAccessPolicy()
    )
    for entry in ENTRIES:
        assert _status(client, _entry_paths(job_id)[entry]) == 403
    publish = client.post(
        f"/api/v1/jobs/{job_id}/frame-candidate",
        json={"view_type": "scan", "frame_index": 0, "role": "TS"},
    )
    assert publish.status_code == 403

    assert _table_count(db_path, "jobs") == jobs_before
    assert not (tmp_path / job_id / "RESULT" / "frame_candidates.json").exists()
    # The structure-source store/indexer was never initialized by a denied read.
    assert _table_exists(db_path, "structure_source_index") is False


class _PermissionFetcher:
    """Remote fetcher double whose SFTP layer rejects every read."""

    def is_remote_job(self, _record: object) -> bool:
        return True

    def file_stat(self, _record: object, _path: str) -> object:
        raise PermissionError("remote SFTP denied")


def test_sftp_rejection_is_not_a_workspace_revocation(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = "remote-authz-1"
    manager = client.app.state.job_manager
    work_dir = tmp_path / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    manager.store.create(
        JobRecord(
            id=job_id,
            spec=JobSpec(workflow="optimize", name="remote", project_id=PROJECT),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            project_id=PROJECT,
            result={"node": "comp-01", "remote_dir": f"/scratch/qc/acp/{job_id}"},
        )
    )
    # The Workspace authorization allows the download...
    decision = default_entry_authorizer().authorize(
        EntryClass.DOWNLOAD, job_id=job_id, project_id=PROJECT
    )
    assert decision.allowed is True
    monkeypatch.setattr(manager, "_remote_fetcher", _PermissionFetcher())
    # ...yet the remote SFTP layer independently rejects it with its own 403.
    response = client.get(f"/api/v1/jobs/{job_id}/remote-files/RESULT/x.xyz")
    assert response.status_code == 403
    assert "denied" in str(response.json()["detail"]).lower()


# ---------------------------------------------------------------------------
# Storage budget reconciliation
# ---------------------------------------------------------------------------

_REPORT_KEYS = {
    "hard_limits",
    "hard_limits_available",
    "capability",
    "limiting_source",
    "prefix",
    "prefix_is_quota",
    "prefix_note",
    "peak_definition",
    "committed",
    "reservation_count",
    "by_phase",
    "completed_bytes",
    "refusal_count",
    "last_refusal_reason",
}


def test_budget_report_no_hard_limits_is_honest() -> None:
    ledger = StorageBudgetLedger(prefix="/scratch/qc")
    report = ledger.report()
    assert report.hard_limits == HARD_LIMITS_UNAVAILABLE
    assert report.hard_limits_available is False
    assert report.capability == CAPABILITY_UNAVAILABLE
    assert report.prefix_is_quota is False
    assert report.prefix_note
    assert report.peak_definition == PEAK_DEFINITION
    assert set(report.to_dict()) == _REPORT_KEYS


def test_budget_reservation_admitted_without_hard_limit() -> None:
    ledger = StorageBudgetLedger()
    decision = ledger.reserve(
        StorageBudgetRequest(
            phase="cache", final_bytes=10**12, temporary_bytes=10**12, resource_id="r1"
        )
    )
    assert decision.allowed is True
    assert ledger.report().hard_limits == HARD_LIMITS_UNAVAILABLE


def test_budget_peak_counts_temporary_and_final_together() -> None:
    ledger = StorageBudgetLedger()
    decision = ledger.reserve(
        StorageBudgetRequest(
            phase="prepare",
            final_bytes=100,
            temporary_bytes=25,
            inodes=3,
            scratch_bytes=40,
            upload_bytes=60,
        )
    )
    assert decision.reservation is not None
    assert decision.reservation.peak_bytes == 125
    committed = ledger.report().committed
    assert committed[BudgetDimension.BYTES.value] == 125
    assert committed[BudgetDimension.TEMPORARY_AMPLIFICATION.value] == 25
    assert committed[BudgetDimension.INODES.value] == 3
    assert committed[BudgetDimension.SCRATCH.value] == 40
    assert committed[BudgetDimension.UPLOAD.value] == 60


def test_budget_enforced_when_hard_limits_present() -> None:
    limits = HardLimits(
        limits={
            BudgetDimension.BYTES.value: 1000,
            BudgetDimension.INODES.value: 100,
        },
        source="statvfs:/data",
    )
    ledger = StorageBudgetLedger(hard_limits=limits, prefix="/scratch/qc")
    report = ledger.report()
    assert report.hard_limits_available is True
    assert report.capability == CAPABILITY_ENFORCED
    assert report.limiting_source == "statvfs:/data"
    assert report.prefix_is_quota is False
    decision = ledger.reserve(
        StorageBudgetRequest(phase="prepare", final_bytes=900, temporary_bytes=50)
    )
    assert decision.allowed is True


def test_budget_reservation_failure_refuses_and_preserves_results(
    caplog: pytest.LogCaptureFixture,
) -> None:
    limits = HardLimits(limits={BudgetDimension.BYTES.value: 1000}, source="statvfs:/data")
    ledger = StorageBudgetLedger(hard_limits=limits)
    ledger.record_completed("done-1", 500)
    assert ledger.completed_bytes == 500

    with caplog.at_level(logging.WARNING):
        decision = ledger.reserve(
            StorageBudgetRequest(
                phase="prepare", final_bytes=900, temporary_bytes=200, resource_id="new"
            )
        )

    assert decision.allowed is False
    assert decision.reservation is None
    assert decision.reason_code == "budget_exceeded"
    assert decision.exceeded
    assert decision.exceeded[0]["dimension"] == BudgetDimension.BYTES.value
    # peak = temporary + final (900 + 200), not just the final file.
    assert decision.exceeded[0]["requested"] == 1100
    assert "refused" in caplog.text.lower()
    # The refusal never deletes the completed result.
    assert ledger.completed_bytes == 500
    assert ledger.report().completed_bytes == 500
    assert ledger.report().refusal_count == 1


def test_budget_release_frees_reservation() -> None:
    limits = HardLimits(limits={BudgetDimension.BYTES.value: 1000}, source="statvfs:/data")
    ledger = StorageBudgetLedger(hard_limits=limits)
    first = ledger.reserve(StorageBudgetRequest(final_bytes=600, resource_id="a"))
    assert first.allowed and first.reservation is not None
    refused = ledger.reserve(StorageBudgetRequest(final_bytes=600, resource_id="b"))
    assert refused.allowed is False
    assert ledger.release(first.reservation.reservation_id) is True
    retried = ledger.reserve(StorageBudgetRequest(final_bytes=600, resource_id="b"))
    assert retried.allowed is True
