"""Round-5 PR-09: storage locator, live-status observation and prepare fast path.

The acceptance list requires a *safe relative* task locator (never an absolute
server path), an explainable live-status projection (observation time/source/
unknown reason) and a bounded local fast path that returns ``ready`` for an
artifact the existing Data prepared index already validates.

Remote paths and Controller cache paths must never masquerade as local
run-root-relative paths: the locator names its storage type and, when it cannot
safely project a path, says why.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_acp_api_v1 import make_client


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    with make_client(tmp_path, monkeypatch, max_running=1) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# storage locator
# --------------------------------------------------------------------------- #


def test_local_projection_has_a_relative_locator() -> None:
    from acp_services.entrypoints.status_projection import project_task_status

    projection = project_task_status(
        {
            "id": "20261031_001_frequency",
            "workflow": "frequency",
            "status": "completed",
            "work_dir": "/srv/run/mol_frequency__3",
            "attempt": 2,
        }
    )
    locator = projection["storage_locator"]
    assert locator["schema_version"] == "acp2.storage-locator.v1"
    assert locator["storage_type"] == "local"
    assert locator["binding"] == "run-root"
    assert locator["attempt"] == 2
    assert locator["locatable"] is True
    assert locator["relative_path"] == "mol_frequency__3"
    # the absolute work_dir never leaks into the locator
    assert "/srv/run" not in str(locator)


def test_remote_projection_names_the_storage_without_a_path() -> None:
    from acp_services.entrypoints.status_projection import project_task_status

    projection = project_task_status(
        {
            "id": "20261031_002_frequency",
            "workflow": "frequency",
            "status": "completed",
            "work_dir": "/cache/remote/20261031_002_frequency",
            "remote_job_id": "lsf-42",
            "attempt": 1,
        }
    )
    locator = projection["storage_locator"]
    assert locator["storage_type"] == "remote"
    assert locator["binding"] == "compute-node"
    assert locator["relative_path"] == "20261031_002_frequency"
    assert locator["unknown_reason"] == "remote_not_cached_locally"


def test_missing_work_dir_is_explicitly_unknown() -> None:
    from acp_services.entrypoints.status_projection import project_task_status

    projection = project_task_status(
        {"id": "task-1", "workflow": "frequency", "status": "queued", "attempt": 1}
    )
    locator = projection["storage_locator"]
    assert locator["locatable"] is False
    assert locator["unknown_reason"] == "no_work_dir"


# --------------------------------------------------------------------------- #
# live status observation
# --------------------------------------------------------------------------- #


def test_live_status_reports_observation_and_unknown_reason() -> None:
    from acp.api.job_status_view import build_live_status

    view = build_live_status(
        {
            "current_stage": "opt",
            "stage_index": 2,
            "stage_total": 5,
            "live_metrics": [{"key": "energy", "kind": "count", "label": "Energy", "value": -76.1}],
            "updated_at": "2026-10-31T08:00:00+00:00",
            "live_source": "state.json",
        }
    )
    assert view is not None
    assert view.observed_at == "2026-10-31T08:00:00+00:00"
    assert view.source == "state.json"
    assert view.unknown_reason is None
    assert view.metrics

    bare = build_live_status({"current_stage": "opt"})
    assert bare is not None
    assert bare.unknown_reason == "metrics_unavailable"


# --------------------------------------------------------------------------- #
# prepare fast path
# --------------------------------------------------------------------------- #


class _Snapshot:
    def __init__(self, *, readable: bool, freshness: str = "fresh") -> None:
        self._readable = readable
        self.freshness = type("Freshness", (), {"value": freshness})()

    def is_readable(self) -> bool:
        return self._readable

    def to_dict(self) -> dict:
        return {"availability": "ready" if self._readable else "not_prepared"}


class _Reader:
    def __init__(self, snapshot: _Snapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[dict] = []

    def availability(self, kind: str, **kwargs: object) -> _Snapshot:
        self.calls.append({"kind": kind, **kwargs})
        return self.snapshot


def test_prepare_fast_path_returns_a_readable_five_tuple(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-6 #5: a ready receipt must carry the artifact_ref read needs."""
    from acp.api import v1_routes

    record = type(
        "Record",
        (),
        {"spec": type("Spec", (), {"workflow": "frequency"})(), "attempt": 1, "status": "completed"},
    )()
    monkeypatch.setattr(v1_routes, "_is_remote_job", lambda _record: False)
    reader = _Reader(_Snapshot(readable=True))
    monkeypatch.setattr(v1_routes, "_cache_only_resource_reader", lambda _request: reader)
    monkeypatch.setattr(v1_routes, "_manager", lambda _request: object())
    ref = (
        {"task_id": "job-1", "attempt": 1, "artifact_id": "freq", "generation": 2,
         "digest": "sha256:" + "a" * 64},
        {"kind": "operation", "id": "op_1"},
    )
    monkeypatch.setattr(v1_routes, "_fast_path_artifact_ref", lambda *args: ref)
    receipt = v1_routes._prepare_fast_path(
        None, record, "job-1", purpose="artifact", artifact_id="freq", relative_path="", attempt=1, expected_revision=0
    )
    assert receipt is not None
    assert receipt["status"] == "ready"
    assert receipt["fast_path"] is True
    assert receipt["artifact_ref"]["digest"].startswith("sha256:")
    assert receipt["operation_ref"]["id"] == "op_1"
    assert receipt["read"].startswith("/api/v1/jobs/job-1/prepared/artifact")
    assert reader.calls and reader.calls[0]["kind"] == "artifact"


def test_prepare_fast_path_falls_through_without_an_authoritative_ref(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No resolvable manager / no published ref must not yield an unreadable ready."""
    from acp.api import v1_routes

    record = type(
        "Record",
        (),
        {"spec": type("Spec", (), {"workflow": "frequency"})(), "attempt": 1, "status": "completed"},
    )()
    monkeypatch.setattr(v1_routes, "_is_remote_job", lambda _record: False)
    monkeypatch.setattr(
        v1_routes, "_cache_only_resource_reader", lambda _request: _Reader(_Snapshot(readable=True))
    )

    def _missing_manager(_request):
        raise RuntimeError("no manager")

    monkeypatch.setattr(v1_routes, "_manager", _missing_manager)
    assert (
        v1_routes._prepare_fast_path(
            None, record, "job-1", purpose="artifact", artifact_id="freq",
            relative_path="", attempt=1, expected_revision=0,
        )
        is None
    )

    monkeypatch.setattr(v1_routes, "_manager", lambda _request: object())
    monkeypatch.setattr(v1_routes, "_fast_path_artifact_ref", lambda *args: None)
    assert (
        v1_routes._prepare_fast_path(
            None, record, "job-1", purpose="artifact", artifact_id="freq",
            relative_path="", attempt=1, expected_revision=0,
        )
        is None
    )


def test_prepare_fast_path_falls_through_for_unprepared_or_remote(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acp.api import v1_routes

    record = type(
        "Record",
        (),
        {"spec": type("Spec", (), {"workflow": "frequency"})(), "attempt": 1, "status": "completed"},
    )()
    monkeypatch.setattr(v1_routes, "_is_remote_job", lambda _record: False)
    monkeypatch.setattr(
        v1_routes, "_cache_only_resource_reader", lambda _request: _Reader(_Snapshot(readable=False))
    )
    assert (
        v1_routes._prepare_fast_path(
            None, record, "job-1", purpose="artifact", artifact_id="freq", relative_path="", attempt=1, expected_revision=0
        )
        is None
    )

    monkeypatch.setattr(v1_routes, "_is_remote_job", lambda _record: True)
    monkeypatch.setattr(
        v1_routes, "_cache_only_resource_reader", lambda _request: _Reader(_Snapshot(readable=True))
    )
    assert (
        v1_routes._prepare_fast_path(
            None, record, "job-1", purpose="artifact", artifact_id="freq", relative_path="", attempt=1, expected_revision=0
        )
        is None
    )
