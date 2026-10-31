"""Round-5 PR-05: publication decisions, leases and the persistent intent store.

The use-case layer (``acp_services.runtime.selection_publication``) owns the
idempotency/recovery rules; the store keeps one intent per task so a process
restart replays the original publication instead of creating a second revision,
and an expired lease can be taken over while a stale publisher is fenced out.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from acp_services.runtime.selection_publication import (
    PublicationConflictError,
    PublicationInProgressError,
    PublicationLeaseLostError,
    SelectionPublicationService,
)


class _MemoryStore:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = dict(payload) if payload else None

    def load(self) -> dict | None:
        return dict(self.payload) if self.payload is not None else None

    def save(self, payload: dict) -> None:
        self.payload = dict(payload)


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def _record(service: SelectionPublicationService, *, key: str, digest: str, owner: str, status: str):
    return service.record(
        job_id="job_001",
        key=key,
        digest=digest,
        expected_revision=0,
        target_revision=1,
        stages=("backup", "structures", "manifest_pending", "review", "manifest_commit"),
        completed=(),
        status=status,
        owner_id=owner,
    )


def test_active_lease_blocks_another_publisher() -> None:
    store = _MemoryStore()
    clock = _Clock()
    service = SelectionPublicationService(store, clock=clock)

    decision = service.begin(key="k", digest="d1", expected_revision=0, current_revision=0, owner_id="owner-a")
    assert decision.action == "publish"
    _record(service, key="k", digest="d1", owner="owner-a", status="pending")

    with pytest.raises(PublicationInProgressError):
        service.begin(key="k", digest="d1", expected_revision=0, current_revision=0, owner_id="owner-b")


def test_expired_lease_is_takeover_able_and_stale_owner_is_fenced() -> None:
    store = _MemoryStore()
    clock = _Clock()
    service = SelectionPublicationService(store, clock=clock)
    _record(service, key="k", digest="d1", owner="owner-a", status="pending")

    clock.advance(200)  # lease (120 s) expired
    decision = service.begin(key="k", digest="d1", expected_revision=0, current_revision=0, owner_id="owner-b")
    assert decision.action == "recovering"
    _record(service, key="k", digest="d1", owner="owner-b", status="pending")

    with pytest.raises(PublicationLeaseLostError):
        _record(service, key="k", digest="d1", owner="owner-a", status="pending")


def test_same_key_different_digest_conflicts() -> None:
    store = _MemoryStore()
    service = SelectionPublicationService(store, clock=_Clock())
    _record(service, key="k", digest="d1", owner="a", status="published")
    with pytest.raises(PublicationConflictError):
        service.begin(key="k", digest="d2", expected_revision=0, current_revision=1, owner_id="b")


def test_store_roundtrip_survives_a_new_store_instance(tmp_path: Path) -> None:
    from acp_services.store.store import JobStore

    db_path = tmp_path / "jobs.db"
    first = JobStore(db_path)
    first.save_selection_publication(
        "job_001",
        {
            "job_id": "job_001",
            "idempotency_key": "key-1",
            "payload_digest": "digest-1",
            "expected_revision": 0,
            "target_revision": 1,
            "stages": ["backup", "structures", "manifest_pending", "review", "manifest_commit"],
            "completed_stages": ["backup", "structures"],
            "status": "pending",
            "owner_id": "owner-a",
            "lease_until": "2026-10-31T00:00:00+00:00",  # expired lease
            "error": None,
            "updated_at": "2026-10-31T12:00:00+00:00",
        },
    )

    reopened = JobStore(db_path)  # simulates a process restart
    intent = reopened.load_selection_publication("job_001")
    assert intent is not None
    assert intent["status"] == "pending"
    assert intent["idempotency_key"] == "key-1"
    assert intent["completed_stages"] == ["backup", "structures"]
    assert intent["target_revision"] == 1

    # the service sees the same intent and resumes instead of republishing
    from acp.calculations.pes.publication import StoreIntentStore

    service = SelectionPublicationService(StoreIntentStore(reopened, "job_001"))
    decision = service.begin(
        key="key-1",
        digest="digest-1",
        expected_revision=0,
        current_revision=0,
        owner_id="owner-b",
    )
    assert decision.action == "recovering"
