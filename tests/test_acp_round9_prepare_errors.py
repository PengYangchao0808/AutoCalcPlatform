"""Round-9 M7: every prepare refusal is diagnosable and the race is retryable.

Before round-9 the prepare chain collapsed two very different outcomes into one
opaque code:

* a **parameter error** (the manifest product was never named — the round-9 M6
  template did not carry ``relative_path``) surfaced as a bare
  ``invalid_parameter`` with no ``params.field``/``params.rule``; and
* a **not-ready-yet race** (the job is terminal but the manifest/product has not
  been materialized yet) was indistinguishable from it, so an agent could not
  tell "fix the request" from "wait and retry".

Pinned here:

* the single refusal table (:data:`PREPARE_REFUSAL_RULES`) gives every frozen
  refusal code a non-empty ``field`` + ``rule`` and a ``retryable`` class;
* the worker projects those facts onto the persisted operation error, and the
  ``GET /operations/{id}`` entry exposes the identical ``params``;
* ``invalid_parameter`` stays non-retryable (one attempt), while a body that is
  merely not materialized yet is the retryable ``not_ready_yet`` race and is
  retried until it materializes or the budget ends;
* ``resolve_prepare_target`` is where the race is detected: an absent manifest
  or an unmaterialized product is ``resource_not_ready`` + ``not_ready_yet`` +
  ``retryable=True``; a manifest that *is* readable but does not contain the id
  is the genuine ``input_invalid`` + ``unknown_artifact`` parameter error.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp.api import artifact_descriptors as descriptors
from acp_services.data import contracts as dc
from acp_services.data import operations as ops

_JOB_ID = "20261102_103000_001_frequency"
_PRODUCT_PATH = "frequencies/normal_modes.json"
_RELATIVE_PATH = f"RESULT/{_PRODUCT_PATH}"
_BODY = json.dumps({"modes": [{"frequency_cm1": 1234.5}]}).encode("utf-8")


# --------------------------------------------------------------------------- #
# helpers: a real prepare-operation store and worker
# --------------------------------------------------------------------------- #


class _Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value


def _service(tmp_path: Path) -> ops.PrepareOperationService:
    return ops.PrepareOperationService(
        tmp_path / "acp_jobs.db",
        clock=_Clock(),
        retry_backoff_seconds=0.0,
        default_lease_seconds=10.0,
    )


def _request(
    *,
    job_id: str = _JOB_ID,
    attempt: int = 1,
    resource_id: str = "normal_modes",
    max_attempts: int = 3,
    payload: dict[str, Any] | None = None,
) -> ops.PrepareRequest:
    return ops.PrepareRequest(
        job_id=job_id,
        attempt=attempt,
        resource_id=resource_id,
        resource_kind=dc.ResourceKind.ARTIFACT,
        resource_digest=f"{job_id}\x1f{attempt}\x1f{resource_id}",
        permission_scope="local-single",
        max_attempts=max_attempts,
        resource_generation=1,
        payload=payload or {"purpose": "artifact", "relative_path": _RELATIVE_PATH},
    )


def _result(detail: str, *, retryable: bool = False) -> ops.PrepareExecutionResult:
    return ops.PrepareExecutionResult(
        prepared=False,
        availability=dc.Availability.PREPARING,
        detail=detail,
        retryable=retryable,
    )


class _ScriptedExecutor:
    """Return scripted outcomes; the last one repeats when the script runs out."""

    def __init__(self, results: list[ops.PrepareExecutionResult]) -> None:
        self._results = list(results)
        self.calls: list[str] = []

    def execute(self, lease: ops.PrepareLease) -> ops.PrepareExecutionResult:
        self.calls.append(lease.operation_id)
        if len(self._results) > 1:
            return self._results.pop(0)
        return self._results[0]


def _settle(
    service: ops.PrepareOperationService,
    operation_id: str,
    *,
    states: set[ops.PrepareState],
    timeout: float = 5.0,
) -> ops.PrepareOperationRecord:
    deadline = time.monotonic() + timeout
    record = service.get(operation_id)
    while time.monotonic() < deadline:
        record = service.get(operation_id)
        if record is not None and record.state in states:
            return record
        time.sleep(0.01)
    assert record is not None
    return record


def _run_worker(
    service: ops.PrepareOperationService,
    executor: _ScriptedExecutor,
    operation_id: str,
    *,
    states: set[ops.PrepareState],
) -> ops.PrepareOperationRecord:
    worker = ops.PrepareOperationWorker(service, executor, poll_interval=0.01)
    worker.start()
    try:
        return _settle(service, operation_id, states=states)
    finally:
        worker.stop()


# --------------------------------------------------------------------------- #
# 1. the refusal table is total and machine-readable
# --------------------------------------------------------------------------- #

#: The frozen operation error vocabulary (``_NamedRefusalCodes`` before round-9)
#: plus ``resource_not_ready``, which the HTTP/MCP entries already use for the
#: not-prepared class.  No new code is introduced.
_FROZEN_REFUSAL_CODES = {
    "not_prepared",
    "remote_cache_missing",
    "digest_mismatch",
    "path_traversal",
    "permission_revoked",
    "unsupported_capability",
    "invalid_parameter",
    "artifact_too_large",
    "artifact_error",
    "resource_not_ready",
}


def test_every_known_refusal_has_a_field_a_rule_and_a_retry_class() -> None:
    for token, entry in ops.PREPARE_REFUSAL_RULES.items():
        assert entry.code in _FROZEN_REFUSAL_CODES, token
        assert entry.field, token
        assert entry.rule, token
        assert isinstance(entry.retryable, bool)


def test_parameter_error_and_race_are_distinct_classes() -> None:
    parameter = ops.prepare_refusal("invalid_parameter")
    assert (parameter.code, parameter.field, parameter.rule) == (
        "invalid_parameter",
        "relative_path",
        "required",
    )
    assert parameter.retryable is False

    race = ops.prepare_refusal("not_prepared")
    assert (race.code, race.field, race.rule) == ("not_prepared", "relative_path", "not_ready_yet")
    assert race.retryable is True

    # The executor convention "<code>:<reason>" keeps the code, adds the reason.
    assert ops.prepare_refusal("digest_mismatch:sha256:deadbeef").rule == "digest_mismatch"
    # A transport/unknown token never becomes a typed rule (historical fallback).
    unknown = ops.prepare_refusal("OSError: disk on fire")
    assert (unknown.code, unknown.field, unknown.rule) == ("not_prepared", "", "")


# --------------------------------------------------------------------------- #
# 2. worker projection: parameter errors are not retried
# --------------------------------------------------------------------------- #


def test_invalid_parameter_fails_once_with_field_and_rule(tmp_path: Path) -> None:
    service = _service(tmp_path)
    operation = service.enqueue(_request())
    executor = _ScriptedExecutor([_result("invalid_parameter")])

    failed = _run_worker(
        service, executor, operation.operation_id, states={ops.PrepareState.FAILED}
    )

    assert failed.state is ops.PrepareState.FAILED
    assert failed.error_code == "invalid_parameter"
    assert failed.attempt_count == 1  # a parameter error is never retried
    contract = failed.to_contract()
    assert contract.error == {
        "code": "invalid_parameter",
        "message": "invalid_parameter",
        "params": {"field": "relative_path", "rule": "required"},
        "retryable": False,
    }


# --------------------------------------------------------------------------- #
# 3. the not-ready-yet race is retried and stays explicitly retryable
# --------------------------------------------------------------------------- #


def test_unmaterialized_body_is_retried_until_it_appears(tmp_path: Path) -> None:
    service = _service(tmp_path)
    operation = service.enqueue(_request())
    executor = _ScriptedExecutor(
        [
            _result("not_prepared"),
            ops.PrepareExecutionResult(
                prepared=True,
                availability=dc.Availability.READY,
                detail="artifact_ready",
                retryable=False,
                size_bytes=len(_BODY),
                sha256="a" * 64,
            ),
        ]
    )

    done = _run_worker(
        service, executor, operation.operation_id, states={ops.PrepareState.SUCCEEDED}
    )

    assert done.state is ops.PrepareState.SUCCEEDED
    assert len(executor.calls) == 2  # the race got its retry
    assert done.error_code is None


def test_exhausted_race_keeps_the_code_and_names_not_ready_yet(tmp_path: Path) -> None:
    service = _service(tmp_path)
    operation = service.enqueue(_request(max_attempts=2))
    executor = _ScriptedExecutor([_result("remote_cache_missing")])

    failed = _run_worker(
        service, executor, operation.operation_id, states={ops.PrepareState.FAILED}
    )

    assert failed.state is ops.PrepareState.FAILED
    # The frozen code is unchanged (round-8 pins it); the retry class is added.
    assert failed.error_code == "remote_cache_missing"
    contract = failed.to_contract()
    assert contract.error is not None
    assert contract.error["code"] == "remote_cache_missing"
    assert contract.error["params"] == {"field": "relative_path", "rule": "not_ready_yet"}
    assert contract.error["retryable"] is True
    assert failed.attempt_count == 2


def test_transport_failure_keeps_the_legacy_error_shape(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.enqueue(_request())
    lease = service.lease_next()
    assert lease is not None
    failed = service.fail(
        lease, error_code="OSError", error_message="connection reset", retryable=False
    )
    assert failed.to_contract().error == {"code": "OSError", "message": "connection reset"}


# --------------------------------------------------------------------------- #
# 4. the same facts at the HTTP entry (entry parity)
# --------------------------------------------------------------------------- #


def _write_task(root: Path, *, with_product: bool = True) -> Path:
    result = root / "RESULT"
    if with_product:
        product = result / _PRODUCT_PATH
        product.parent.mkdir(parents=True)
        product.write_bytes(_BODY)
    result.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "acp2.result-manifest.v2",
        "version": 2,
        "task_id": _JOB_ID,
        "workflow": "frequency",
        "status": "completed",
        "products": [
            {
                "id": "normal_modes",
                "kind": "frequency_modes",
                "label": "Normal modes",
                "path": _PRODUCT_PATH,
            }
        ],
    }
    (result / "result_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1)) as test_client:
        yield test_client


def _seed_job(client: TestClient, root: Path) -> None:
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

    work_dir = root / "uncategorized" / f"{_JOB_ID}-1"
    _write_task(work_dir)
    client.app.state.job_manager.store.create(
        JobRecord(
            id=_JOB_ID,
            spec=JobSpec(workflow="frequency", name="freq-errors"),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            attempt=1,
            project_id="uncategorized",
        )
    )


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


def test_http_operation_error_carries_the_same_params(client: TestClient, tmp_path: Path) -> None:
    _seed_job(client, tmp_path)
    prepared = client.post(
        f"/api/v1/jobs/{_JOB_ID}/prepare",
        json={
            "purpose": "artifact",
            "job_id": _JOB_ID,
            "artifact_id": "normal_modes",
            "relative_path": _RELATIVE_PATH,
            "expected_digest": "sha256:" + "0" * 64,
        },
    )
    assert prepared.status_code == 200, prepared.text
    settled = _wait_operation(client, prepared.json()["operation_id"])

    assert settled["state"] == "failed"
    error = settled["error"]
    assert error["code"] == "digest_mismatch"
    assert error["params"] == {"field": "expected_digest", "rule": "digest_mismatch"}
    assert error["retryable"] is False


# --------------------------------------------------------------------------- #
# 5. where the race is detected: server-side prepare-target derivation
# --------------------------------------------------------------------------- #


def test_missing_manifest_is_the_retryable_race(tmp_path: Path) -> None:
    root = tmp_path / "task"
    (root / "RESULT").mkdir(parents=True)
    with pytest.raises(descriptors.PrepareTargetRefusedError) as refused:
        descriptors.resolve_prepare_target(root, artifact_id="normal_modes")
    assert refused.value.code == "resource_not_ready"
    assert refused.value.params == {
        "field": "artifact_id",
        "rule": "not_ready_yet",
        "manifest_reason": "missing",
    }
    assert refused.value.retryable is True


def test_unmaterialized_product_is_the_retryable_race(tmp_path: Path) -> None:
    root = _write_task(tmp_path / "task", with_product=False)
    with pytest.raises(descriptors.PrepareTargetRefusedError) as refused:
        descriptors.resolve_prepare_target(root, artifact_id="normal_modes")
    assert refused.value.code == "resource_not_ready"
    assert refused.value.params == {
        "field": "artifact_id",
        "rule": "not_ready_yet",
        "relative_path": _RELATIVE_PATH,
    }
    assert refused.value.retryable is True


def test_unknown_artifact_is_a_parameter_error(tmp_path: Path) -> None:
    root = _write_task(tmp_path / "task")
    with pytest.raises(descriptors.PrepareTargetRefusedError) as refused:
        descriptors.resolve_prepare_target(root, artifact_id="not_a_product")
    assert refused.value.code == "input_invalid"
    assert refused.value.params["field"] == "artifact_id"
    assert refused.value.params["rule"] == "unknown_artifact"
    assert refused.value.retryable is False


def test_unsafe_artifact_id_and_path_are_refused_before_any_lookup(tmp_path: Path) -> None:
    root = _write_task(tmp_path / "task")
    with pytest.raises(descriptors.PrepareTargetRefusedError) as bad_id:
        descriptors.resolve_prepare_target(root, artifact_id="../escape")
    assert bad_id.value.params == {"field": "artifact_id", "rule": "path_traversal"}
    assert bad_id.value.retryable is False

    with pytest.raises(descriptors.PrepareTargetRefusedError) as bad_path:
        descriptors.resolve_prepare_target(
            root, artifact_id="normal_modes", relative_path="RESULT/../../etc/passwd"
        )
    assert bad_path.value.code == "input_invalid"
    assert bad_path.value.params["field"] == "relative_path"
    assert bad_path.value.params["rule"] == "path_traversal"


def test_unsafe_manifest_path_is_a_manifest_defect(tmp_path: Path) -> None:
    root = _write_task(tmp_path / "task")
    manifest_path = root / "RESULT/result_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["products"][0]["path"] = "../secret.txt"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(descriptors.PrepareTargetRefusedError) as refused:
        descriptors.resolve_prepare_target(root, artifact_id="normal_modes")
    assert refused.value.code == "input_invalid"
    assert refused.value.params == {"field": "artifact_id", "rule": "unsafe_manifest_path"}
    assert refused.value.retryable is False
