"""B12 (todo 13) — real artifact prepare/read lifecycle and negative matrix.

The tests exercise the B06 route shells as *real* operations against the
persistent prepare state machine and the B12 artifact store:

* prepare returns a durable ``operation_ref`` and is idempotent per
  (job, attempt, resource); a different generation is a different operation;
* a read is cache-only: it never fetches, never enqueues and never parses, so a
  cold cache is ``not_prepared`` and the operation count is unchanged;
* every refusal is named (not-prepared, corrupt digest, old generation,
  path traversal, revoked permission, missing remote cache, cross-attempt);
* an auxiliary frequency artifact keeps its own digest;
* A's EvidenceReport is stored/read verbatim with schema/source refs and is
  never interpreted as ``claim supported``;
* analyze stays explicit-only and registry-gated.

Run with: pytest tests/test_acp2_runtime_artifact.py -q
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

JOB_ID = "20261023_031453_001_singlepoint"
ENERGY_PATH = "RESULT/energy/singlepoint_energy.json"
FREQUENCY_PATH = "RESULT/frequencies/normal_modes.json"
ENERGY_BODY = b'{"energy_hartree": -1.116759307204, "units": "hartree"}\n'
FREQUENCY_BODY = b'{"modes": [{"frequency_cm1": 4401.2}], "units": "cm-1"}\n'


def sha256_digest(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def _seed_local_job(
    manager: Any,
    root: Path,
    *,
    job_id: str = JOB_ID,
    attempt: int = 1,
    project_id: str = "uncategorized",
    remote: bool = False,
) -> Path:
    work_dir = root / "uncategorized" / f"{job_id}-{attempt}"
    _write(work_dir / ENERGY_PATH, ENERGY_BODY)
    _write(work_dir / FREQUENCY_PATH, FREQUENCY_BODY)
    result: dict[str, Any] = {}
    if remote:
        result = {"node": "node-1", "remote_dir": f"/scratch/{job_id}"}
    manager.store.create(
        JobRecord(
            id=job_id,
            spec=JobSpec(workflow="singlepoint", name=job_id, project_id=project_id),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            attempt=attempt,
            project_id=project_id,
            result=result,
        )
    )
    return work_dir


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1)) as test_client:
        yield test_client


def _operation_count(client: TestClient) -> int:
    manager = client.app.state.job_manager
    conn = sqlite3.connect(str(manager.store.db_path))
    try:
        row = conn.execute("SELECT COUNT(*) FROM data_operations").fetchone()
        return int(row[0]) if row is not None else 0
    finally:
        conn.close()


def _prepare(
    client: TestClient,
    *,
    job_id: str = JOB_ID,
    artifact_id: str = "singlepoint_energy",
    relative_path: str = ENERGY_PATH,
    attempt: int = 1,
    generation: int = 1,
    expected_digest: str | None = None,
    expected_size_bytes: int | None = None,
    evidence_report: dict[str, Any] | None = None,
    schema_ref: str | None = None,
    source_refs: list[dict[str, Any]] | None = None,
    idempotency_key: str | None = None,
) -> Any:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "purpose": "artifact",
        "job_id": job_id,
        "artifact_id": artifact_id,
        "attempt": attempt,
        "generation": generation,
    }
    if relative_path:
        payload["relative_path"] = relative_path
    if expected_digest is not None:
        payload["expected_digest"] = expected_digest
    if expected_size_bytes is not None:
        payload["expected_size_bytes"] = expected_size_bytes
    if evidence_report is not None:
        payload["evidence_report"] = evidence_report
    if schema_ref is not None:
        payload["schema_ref"] = schema_ref
    if source_refs is not None:
        payload["source_refs"] = source_refs
    if idempotency_key is not None:
        payload["idempotency_key"] = idempotency_key
    return client.post(f"/api/v1/jobs/{job_id}/prepare", json=payload)


def _wait_operation(
    client: TestClient, operation_id: str, *, timeout: float = 5.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/operations/{operation_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["state"] in {"ready", "failed", "cancelled"}:
            return body
        time.sleep(0.01)
    raise AssertionError(f"operation did not settle: {body}")


def _read(
    client: TestClient,
    *,
    job_id: str = JOB_ID,
    artifact_id: str = "singlepoint_energy",
    attempt: int = 1,
    generation: int = 1,
    digest: str | None,
    max_bytes: int | None = None,
) -> Any:
    params: dict[str, Any] = {"attempt": attempt, "expected_revision": generation}
    if digest is not None:
        params["digest"] = digest
    if max_bytes is not None:
        params["max_bytes"] = max_bytes
    return client.get(f"/api/v1/jobs/{job_id}/prepared/{artifact_id}", params=params)


# ---------------------------------------------------------------------------
# Happy path + idempotency
# ---------------------------------------------------------------------------


def test_prepare_and_read_real_artifact_digest_generation_agree(
    client: TestClient, tmp_path: Path
) -> None:
    _seed_local_job(client.app.state.job_manager, tmp_path)
    prepared = _prepare(client)
    assert prepared.status_code == 200, prepared.text
    body = prepared.json()
    operation_ref = body["operation_ref"]
    assert operation_ref["kind"] == "operation" and operation_ref["id"]

    settled = _wait_operation(client, operation_ref["id"])
    assert settled["state"] == "ready"
    assert settled["availability"] == "ready"
    assert settled["generation"] == 1
    assert settled["artifact_ref"]["digest"] == sha256_digest(ENERGY_BODY)
    assert settled["artifact_ref"]["authorization"]["principal_id"] == "principal_local"

    read = _read(client, digest=sha256_digest(ENERGY_BODY))
    assert read.status_code == 200, read.text
    payload = read.json()
    assert payload["digest"] == sha256_digest(ENERGY_BODY)
    assert payload["size_bytes"] == len(ENERGY_BODY)
    assert payload["generation"] == 1
    assert payload["truncated"] is False
    assert payload["body"].encode("utf-8") == ENERGY_BODY
    assert payload["artifact_ref"]["operation_id"] == operation_ref["id"]

    replay = _prepare(client)
    assert replay.status_code == 200
    assert replay.json()["operation_id"] == body["operation_id"]

    newer = _prepare(client, generation=2)
    assert newer.status_code == 200
    assert newer.json()["operation_id"] != body["operation_id"]
    assert newer.json()["generation"] == 2


def test_cold_cache_is_not_prepared_and_get_creates_nothing(
    client: TestClient, tmp_path: Path
) -> None:
    _seed_local_job(client.app.state.job_manager, tmp_path)
    before = _operation_count(client)
    missing = _read(client, artifact_id="never_prepared", digest=sha256_digest(ENERGY_BODY))
    assert missing.status_code == 409
    detail = missing.json()["detail"]
    assert detail["code"] == "resource_not_ready"
    assert "not_prepared" in detail["message"]
    assert _operation_count(client) == before


def test_read_requires_a_digest(client: TestClient, tmp_path: Path) -> None:
    _seed_local_job(client.app.state.job_manager, tmp_path)
    response = _read(client, digest="")
    assert response.status_code == 400
    assert response.json()["detail"]["params"]["rule"] == "required"


# ---------------------------------------------------------------------------
# Negative matrix (named errors, no bytes)
# ---------------------------------------------------------------------------


def test_corrupt_digest_is_refused_without_bytes(client: TestClient, tmp_path: Path) -> None:
    manager = client.app.state.job_manager
    _seed_local_job(manager, tmp_path)
    wrong = "sha256:" + "0" * 64
    failed_prepare = _prepare(client, expected_digest=wrong)
    assert failed_prepare.status_code == 200
    operation_id = failed_prepare.json()["operation_id"]
    settled = _wait_operation(client, operation_id)
    assert settled["state"] == "failed"
    assert settled["error"]["code"] == "digest_mismatch"

    good = _prepare(client, artifact_id="energy_ok")
    assert good.status_code == 200
    op = _wait_operation(client, good.json()["operation_id"])
    assert op["state"] == "ready"
    digest = op["artifact_ref"]["digest"]

    mismatch = _read(client, artifact_id="energy_ok", digest=wrong)
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "revision_conflict"
    assert mismatch.json()["detail"]["params"]["rule"] == "digest_mismatch"
    assert "body" not in mismatch.json()

    # Tamper with the published body: the read recomputes and refuses.
    store_root = manager.run_root / "artifact-store"
    bodies = list(store_root.rglob("*.body"))
    assert bodies
    target = next(path for path in bodies if path.read_bytes() == ENERGY_BODY)
    target.write_bytes(b"tampered")
    tampered = _read(client, artifact_id="energy_ok", digest=digest)
    assert tampered.status_code == 409
    assert tampered.json()["detail"]["params"]["rule"] == "digest_mismatch"


def test_evicted_body_is_not_prepared_and_explicit_reprepare_rebuilds(
    client: TestClient, tmp_path: Path
) -> None:
    manager = client.app.state.job_manager
    _seed_local_job(manager, tmp_path)
    prepared = _prepare(client, artifact_id="evictable")
    assert prepared.status_code == 200
    first_operation = prepared.json()["operation_id"]
    assert _wait_operation(client, first_operation)["state"] == "ready"
    assert (
        _read(client, artifact_id="evictable", digest=sha256_digest(ENERGY_BODY)).status_code == 200
    )

    store_dir = manager.run_root / "artifact-store" / JOB_ID / "1" / "evictable" / "1"
    removed = 0
    for body in store_dir.glob("*.body"):
        body.unlink()
        removed += 1
    assert removed == 1

    cold = _read(client, artifact_id="evictable", digest=sha256_digest(ENERGY_BODY))
    assert cold.status_code == 409
    assert cold.json()["detail"]["code"] == "resource_not_ready"
    assert "not_prepared" in cold.json()["detail"]["message"]

    reprepare = _prepare(client, artifact_id="evictable")
    assert reprepare.status_code == 200
    second_operation = reprepare.json()["operation_id"]
    assert second_operation != first_operation
    assert _wait_operation(client, second_operation)["state"] == "ready"
    rebuilt = _read(client, artifact_id="evictable", digest=sha256_digest(ENERGY_BODY))
    assert rebuilt.status_code == 200
    assert rebuilt.json()["body"].encode("utf-8") == ENERGY_BODY


def test_old_generation_is_stale(client: TestClient, tmp_path: Path) -> None:
    manager = client.app.state.job_manager
    _seed_local_job(manager, tmp_path)
    first = _prepare(client, generation=1)
    assert first.status_code == 200
    assert _wait_operation(client, first.json()["operation_id"])["state"] == "ready"
    second = _prepare(client, generation=2)
    assert second.status_code == 200
    assert _wait_operation(client, second.json()["operation_id"])["state"] == "ready"

    stale = _read(client, generation=1, digest=sha256_digest(ENERGY_BODY))
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "revision_conflict"
    assert stale.json()["detail"]["params"]["rule"] == "generation_stale"


def test_path_traversal_is_refused_before_filesystem_access(
    client: TestClient, tmp_path: Path
) -> None:
    manager = client.app.state.job_manager
    _seed_local_job(manager, tmp_path)
    outside = tmp_path.parent / "b12-escape-proof.json"
    if outside.exists():
        outside.unlink()
    before = _operation_count(client)
    traversal = _prepare(client, relative_path="../../b12-escape-proof.json")
    assert traversal.status_code == 400
    assert traversal.json()["detail"]["params"]["rule"] == "path_traversal"
    assert not outside.exists()
    assert _operation_count(client) == before

    unsafe_id = _prepare(client, artifact_id="../escape", relative_path=ENERGY_PATH)
    assert unsafe_id.status_code == 400
    assert unsafe_id.json()["detail"]["params"]["rule"] == "path_traversal"


def test_cross_attempt_read_never_serves_newer_bytes(client: TestClient, tmp_path: Path) -> None:
    manager = client.app.state.job_manager
    work_dir = _seed_local_job(manager, tmp_path, attempt=2)
    archive_one = work_dir / "WORK" / "00_RUNTIME" / "attempts" / "1"
    _write(archive_one / ENERGY_PATH, ENERGY_BODY)
    newer_body = b'{"energy_hartree": -2.0, "units": "hartree"}\n'
    _write(work_dir / ENERGY_PATH, newer_body)

    old = _prepare(client, attempt=1, expected_digest=sha256_digest(ENERGY_BODY))
    assert old.status_code == 200
    old_op = _wait_operation(client, old.json()["operation_id"])
    assert old_op["state"] == "ready", old_op
    assert old_op["artifact_ref"]["digest"] == sha256_digest(ENERGY_BODY)

    current = _prepare(client, attempt=2, expected_digest=sha256_digest(newer_body))
    assert current.status_code == 200
    current_op = _wait_operation(client, current.json()["operation_id"])
    assert current_op["state"] == "ready"
    assert current_op["artifact_ref"]["digest"] == sha256_digest(newer_body)

    # Attempt 2 prepared first: the attempt-1 operation is expired and the read
    # is a named refusal, never the attempt-2 bytes.
    stale_attempt = _read(client, attempt=1, digest=sha256_digest(ENERGY_BODY))
    assert stale_attempt.status_code == 409
    assert stale_attempt.json()["detail"]["code"] == "resource_not_ready"
    read_current = _read(client, attempt=2, digest=sha256_digest(newer_body))
    assert read_current.status_code == 200
    assert read_current.json()["body"].encode("utf-8") == newer_body
    assert read_current.json()["body"].encode("utf-8") != ENERGY_BODY


def test_missing_remote_cache_is_named_and_never_fetches(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = client.app.state.job_manager
    _seed_local_job(manager, tmp_path, remote=True)

    class FetchSpy:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def read_file(self, record: Any, filename: str) -> bytes:
            self.calls.append(filename)
            raise FileNotFoundError(filename)

        def __getattr__(self, name: str) -> Any:  # pragma: no cover - strict spy
            raise AssertionError(f"unexpected fetcher attribute: {name}")

    spy = FetchSpy()
    monkeypatch.setattr(manager, "_remote_fetcher", spy, raising=False)
    before = _operation_count(client)

    prepared = _prepare(client)
    assert prepared.status_code == 200
    settled = _wait_operation(client, prepared.json()["operation_id"])
    assert settled["state"] == "failed"
    assert settled["error"]["code"] == "remote_cache_missing"

    read = _read(client, digest=sha256_digest(ENERGY_BODY))
    assert read.status_code == 409
    assert read.json()["detail"]["code"] == "resource_not_ready"
    assert "not_prepared" in read.json()["detail"]["message"]
    assert spy.calls == []
    assert _operation_count(client) == before + 1  # only the explicit prepare row


def test_project_scope_denial_refuses_prepare_read_analyze(
    client: TestClient, tmp_path: Path
) -> None:
    from acp_services.workspace.server_identity import ServerIdentity

    _seed_local_job(client.app.state.job_manager, tmp_path)
    ok = _prepare(client)
    assert ok.status_code == 200
    digest = sha256_digest(ENERGY_BODY)

    pinned = ServerIdentity.build(verified_project="other-project")
    client.app.state.server_identity = pinned
    client.app.state.entry_authorizer = pinned.authorizer
    denied_prepare = _prepare(client, artifact_id="denied")
    assert denied_prepare.status_code in {403, 404}
    denied_read = _read(client, digest=digest)
    assert denied_read.status_code in {403, 404}
    denied_analyze = client.post(
        f"/api/v1/jobs/{JOB_ID}/analyze",
        json={
            "analysis_id": "vibration.analyze",
            "input_refs": [{"task_id": JOB_ID}],
            "explicit": True,
        },
    )
    assert denied_analyze.status_code in {403, 404}


def test_old_response_after_switching_tasks_is_refused(client: TestClient, tmp_path: Path) -> None:
    manager = client.app.state.job_manager
    _seed_local_job(manager, tmp_path, job_id="job-a")
    _seed_local_job(manager, tmp_path, job_id="job-b")
    prepared = _prepare(client, job_id="job-a")
    assert prepared.status_code == 200
    digest = sha256_digest(ENERGY_BODY)
    assert _wait_operation(client, prepared.json()["operation_id"])["state"] == "ready"

    switched = _read(client, job_id="job-b", digest=digest)
    assert switched.status_code == 409
    assert switched.json()["detail"]["code"] == "resource_not_ready"
    assert "body" not in switched.json()


def test_auxiliary_frequency_artifact_has_its_own_digest(
    client: TestClient, tmp_path: Path
) -> None:
    _seed_local_job(client.app.state.job_manager, tmp_path)
    energy = _prepare(client)
    assert energy.status_code == 200
    energy_op = _wait_operation(client, energy.json()["operation_id"])
    frequency = _prepare(
        client,
        artifact_id="frequency_modes",
        relative_path=FREQUENCY_PATH,
        expected_digest=sha256_digest(FREQUENCY_BODY),
    )
    assert frequency.status_code == 200
    frequency_op = _wait_operation(client, frequency.json()["operation_id"])
    assert frequency_op["state"] == "ready"
    assert frequency_op["artifact_ref"]["digest"] == sha256_digest(FREQUENCY_BODY)
    assert frequency_op["artifact_ref"]["digest"] != energy_op["artifact_ref"]["digest"]
    read_frequency = _read(
        client,
        artifact_id="frequency_modes",
        digest=sha256_digest(FREQUENCY_BODY),
    )
    assert read_frequency.status_code == 200
    assert read_frequency.json()["body"].encode("utf-8") == FREQUENCY_BODY


# ---------------------------------------------------------------------------
# EvidenceReport storage (not interpretation)
# ---------------------------------------------------------------------------

EVIDENCE_REPORT = {
    "schema_version": 1,
    "report_id": "er_b12_fixture",
    "subject": {"subject_id": "subject-1", "subject_kind": "calculation_result"},
    "execution": {"terminal_state": "completed", "argv": [], "exit_code": 0},
    "claims": [
        {
            "claim_id": "c1",
            "statement": "A static fixture claim.",
            "status": "not_evaluated",
            "scope": {"subject": "fixture", "applicability": "system_specific"},
            "source_refs": [],
            "missing_checks": [{"check_id": "not_executed", "reason": "static fixture"}],
        }
    ],
    "limitations": [],
}


def test_evidence_report_is_stored_verbatim_and_not_interpreted(
    client: TestClient, tmp_path: Path
) -> None:
    _seed_local_job(client.app.state.job_manager, tmp_path)
    body = json.dumps(
        EVIDENCE_REPORT, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    digest = sha256_digest(body)
    prepared = _prepare(
        client,
        artifact_id="evidence_report",
        relative_path="",
        expected_digest=digest,
        evidence_report=EVIDENCE_REPORT,
        schema_ref="acp2.science.evidence.v1",
        source_refs=[
            {
                "artifact_path": ENERGY_PATH,
                "digest": sha256_digest(ENERGY_BODY),
                "role": "native_output",
                "units": "hartree",
            }
        ],
    )
    assert prepared.status_code == 200, prepared.text
    settled = _wait_operation(client, prepared.json()["operation_id"])
    assert settled["state"] == "ready"
    assert settled["artifact_ref"]["source_schema"] == "acp2.science.evidence.v1"
    assert settled["artifact_ref"]["digest"] == digest

    read = _read(client, artifact_id="evidence_report", digest=digest)
    assert read.status_code == 200, read.text
    payload = read.json()
    served = json.loads(payload["body"])
    assert served == EVIDENCE_REPORT
    assert served["claims"][0]["status"] == "not_evaluated"
    assert payload["source_schema"] == "acp2.science.evidence.v1"
    assert payload["source_refs"][0]["artifact_path"] == ENERGY_PATH
    # The platform never adds an interpretation of its own.
    assert "claim_supported" not in payload
    assert "claim_supported" not in payload["artifact_ref"]


# ---------------------------------------------------------------------------
# Analyze: explicit + registry-gated, zero science
# ---------------------------------------------------------------------------


def test_analyze_requires_explicit_and_registry(client: TestClient, tmp_path: Path) -> None:
    """M2-02: explicit-only, and the *injected* registry decides executability.

    ``vibration.analyze`` is executable (a real handler over prepared artifacts),
    so an unprepared reference is the named ``resource_not_ready`` refusal;
    ``vibration.compute`` stays a registered-not-executable compute capability.
    Neither path creates a job or a prepare operation.
    """
    _seed_local_job(client.app.state.job_manager, tmp_path)
    reference = {
        "task_id": JOB_ID,
        "attempt": 1,
        "artifact_id": "x",
        "generation": 1,
        "digest": "sha256:" + "0" * 64,
    }
    implicit = client.post(
        f"/api/v1/jobs/{JOB_ID}/analyze",
        json={"analysis_id": "vibration.analyze", "input_refs": [reference], "explicit": False},
    )
    assert implicit.status_code == 400
    assert implicit.json()["detail"]["code"] == "input_invalid"
    explicit = client.post(
        f"/api/v1/jobs/{JOB_ID}/analyze",
        json={"analysis_id": "vibration.analyze", "input_refs": [reference], "explicit": True},
    )
    assert explicit.status_code == 409
    assert explicit.json()["detail"]["code"] == "resource_not_ready"
    assert "not_prepared" in explicit.json()["detail"]["message"]
    compute = client.post(
        f"/api/v1/jobs/{JOB_ID}/analyze",
        json={"analysis_id": "vibration.compute", "input_refs": [reference], "explicit": True},
    )
    assert compute.status_code == 422
    assert compute.json()["detail"]["code"] == "unsupported_capability"
    assert compute.json()["detail"]["params"]["rule"] == "analysis_capability_not_executable"
    assert _operation_count(client) == 0
