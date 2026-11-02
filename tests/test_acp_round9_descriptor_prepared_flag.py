"""Round-9 M15: the descriptor's ``prepared`` flag reflects the prepare state.

Round-8 kept ``prepared`` hard-``False`` by design ("a descriptor is not an
ArtifactRef").  The external agent's cost was real: it could not tell whether a
descriptor still needed a prepare, so it prepared the same product again and
again.

Pinned here:

* ``describe_job_artifacts(prepare_service=...)`` reads the **authoritative**
  durable operation row (``PrepareOperationService.find``) — ``prepared`` is
  true only for a ``succeeded`` row that committed digest + size, and the
  descriptor then carries the row's generation/digest/observed time (never
  invented);
* a queried-but-unprepared product is ``prepared: False`` + ``prepared_state``
  (``absent``/``queued``/``failed``/…), not an unknown;
* an unavailable service/key is ``prepared: False`` plus
  ``prepared_unknown_reason`` — the flag never guesses;
* the lookup is **zero-side-effect**: no operation row is created or modified,
  no artifact store is touched, and the task directory is unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from acp.api import artifact_descriptors as descriptors
from acp_services.data import contracts as dc
from acp_services.data import operations as ops

_JOB_ID = "20261102_110000_001_frequency"
_PRODUCT_PATH = "frequencies/normal_modes.json"
_RELATIVE_PATH = f"RESULT/{_PRODUCT_PATH}"
_SHA = "b" * 64


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


def _write_task(root: Path) -> Path:
    result = root / "RESULT"
    product = result / _PRODUCT_PATH
    product.parent.mkdir(parents=True)
    body = json.dumps({"modes": [1, 2, 3]}).encode("utf-8")
    product.write_bytes(body)
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


def _enqueue(
    service: ops.PrepareOperationService, *, attempt: int = 1, generation: int = 2
) -> ops.PrepareOperationRecord:
    return service.enqueue(
        ops.PrepareRequest(
            job_id=_JOB_ID,
            attempt=attempt,
            resource_id="normal_modes",
            resource_kind=dc.ResourceKind.ARTIFACT,
            resource_digest="dg-m15",
            permission_scope="local-single",
            resource_generation=generation,
            payload={"purpose": "artifact", "relative_path": _RELATIVE_PATH},
        )
    )


def _complete(
    service: ops.PrepareOperationService,
    operation: ops.PrepareOperationRecord,
    *,
    size_bytes: int = 21,
) -> ops.PrepareOperationRecord:
    lease = service.lease_next()
    assert lease is not None
    return service.complete(
        lease,
        ops.PrepareCompletion(
            availability=dc.Availability.READY,
            result={"detail": "artifact_ready"},
            size_bytes=size_bytes,
            sha256=_SHA,
        ),
    )


def _snapshot(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def _rows(service: ops.PrepareOperationService) -> list[tuple[Any, ...]]:
    return [
        (
            record.operation_id,
            record.state.value,
            record.attempt_count,
            record.updated_at,
            record.error_code,
            record.size_bytes,
            record.sha256,
        )
        for record in service.list()
    ]


def _describe(
    task: Path, service: ops.PrepareOperationService | None, *, attempt: int = 1
) -> dict[str, Any]:
    items, truncated = descriptors.describe_job_artifacts(
        task,
        attempt=attempt,
        job_id=_JOB_ID,
        prepare_service=service,
    )
    assert truncated is False
    assert len(items) == 1
    return items[0]


# --------------------------------------------------------------------------- #
# 1. ground truth: no service / no key is explicit, never a guessed False
# --------------------------------------------------------------------------- #


def test_without_a_prepare_service_the_descriptor_says_why(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "task")
    descriptor = _describe(task, None)
    assert descriptor["prepared"] is False
    assert descriptor["prepared_unknown_reason"] == "no_prepare_service"
    assert "prepared_state" not in descriptor


def test_without_a_lookup_key_the_descriptor_says_why(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "task")
    service = _service(tmp_path)
    items, _ = descriptors.describe_job_artifacts(task, prepare_service=service)
    descriptor = items[0]
    assert descriptor["prepared"] is False
    assert descriptor["prepared_unknown_reason"] == "no_prepare_key"


# --------------------------------------------------------------------------- #
# 2. a queried product distinguishes "not prepared" from "unknown"
# --------------------------------------------------------------------------- #


def test_unprepared_product_reports_absent_with_zero_side_effects(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "task")
    service = _service(tmp_path)
    before_rows = _rows(service)
    before_files = _snapshot(task)

    descriptor = _describe(task, service)
    assert descriptor["prepared"] is False
    assert descriptor["prepared_state"] == "absent"
    assert "prepared_unknown_reason" not in descriptor

    # The read-only lookup created/modified no row and wrote no file.
    assert _rows(service) == before_rows == []
    assert _snapshot(task) == before_files
    assert not (tmp_path / "artifact-store").exists()


def test_failed_prepare_reports_the_state(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "task")
    service = _service(tmp_path)
    operation = _enqueue(service)
    lease = service.lease_next()
    assert lease is not None
    failed = service.fail(
        lease, error_code="invalid_parameter", error_message="invalid_parameter", retryable=False
    )
    assert failed.state is ops.PrepareState.FAILED

    descriptor = _describe(task, service)
    assert descriptor["prepared"] is False
    assert descriptor["prepared_state"] == "failed"
    assert descriptor["prepared_operation_id"] == operation.operation_id
    assert "prepared_digest" not in descriptor


def test_another_attempt_is_not_this_descriptors_prepare(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "task")
    service = _service(tmp_path)
    _complete(service, _enqueue(service, attempt=1))

    descriptor = _describe(task, service, attempt=2)
    assert descriptor["prepared"] is False
    assert descriptor["prepared_state"] == "absent"


# --------------------------------------------------------------------------- #
# 3. a succeeded prepare flips the flag with the committed facts
# --------------------------------------------------------------------------- #


def test_succeeded_prepare_flips_the_flag_with_committed_facts(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "task")
    service = _service(tmp_path)
    operation = _enqueue(service, generation=2)
    completed = _complete(service, operation, size_bytes=21)
    assert completed.state is ops.PrepareState.SUCCEEDED

    before_rows = _rows(service)
    before_files = _snapshot(task)
    descriptor = _describe(task, service)

    assert descriptor["prepared"] is True
    assert descriptor["prepared_state"] == "succeeded"
    assert descriptor["prepared_generation"] == 2
    assert descriptor["prepared_digest"] == f"sha256:{_SHA}"
    assert descriptor["prepared_at"] == completed.updated_at
    assert descriptor["prepared_operation_id"] == operation.operation_id
    assert "prepared_unknown_reason" not in descriptor
    # The prepare template stays alongside the flag (a re-prepare/rebuild step
    # still needs its exact call), and it names the same product.
    assert descriptor["prepare"]["arguments"]["artifact_id"] == descriptor["resource_id"]
    assert descriptor["prepare"]["arguments"]["relative_path"] == _RELATIVE_PATH
    # size_bytes keeps its on-disk meaning; the prepare row does not override it.
    assert descriptor["size_bytes"] == (task / _RELATIVE_PATH).stat().st_size

    # Zero side effects: the same row (state/attempt_count/updated_at) and tree.
    assert _rows(service) == before_rows
    assert _snapshot(task) == before_files
    assert not (tmp_path / "artifact-store").exists()


def test_succeeded_without_a_committed_digest_is_not_prepared(tmp_path: Path) -> None:
    """A row that never committed bytes must not claim ``prepared``."""
    task = _write_task(tmp_path / "task")
    service = _service(tmp_path)
    _enqueue(service)
    lease = service.lease_next()
    assert lease is not None
    service.complete(lease, ops.PrepareCompletion(availability=dc.Availability.READY))

    descriptor = _describe(task, service)
    assert descriptor["prepared"] is False
    assert descriptor["prepared_state"] == "succeeded"
    assert "prepared_digest" not in descriptor
