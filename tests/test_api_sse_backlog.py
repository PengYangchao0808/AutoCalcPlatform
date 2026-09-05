# FastAPI's TestClient stubs expose untyped application state in these integration tests.
# pyright: reportAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportFunctionMemberAccess=false, reportAttributeAccessIssue=false, reportUnusedCallResult=false
from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    # Given: a FastAPI app whose scheduler uses an isolated run root
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1)) as test_client:
        yield test_client


def _sse_events(text: str) -> list[tuple[int | None, str]]:
    events: list[tuple[int | None, str]] = []
    event_id: int | None = None
    event_type: str | None = None
    for line in text.splitlines():
        if line.startswith("id: "):
            event_id = int(line[4:])
        elif line.startswith("event: "):
            event_type = line[7:]
        elif not line and event_type is not None:
            events.append((event_id, event_type))
            event_id = None
            event_type = None
    return events


def _create_terminal_backlog_job(client: TestClient, tmp_path: Path) -> str:
    manager = client.app.state.job_manager
    job_id = "sse-backlog"
    work_dir = tmp_path / "sse-backlog-work"
    runtime_dir = work_dir / "WORK" / "00_RUNTIME"
    runtime_dir.mkdir(parents=True)
    manager.store.create(
        JobRecord(
            id=job_id,
            spec=JobSpec(workflow="fake", name=job_id),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            progress=1.0,
            exit_code=0,
        )
    )
    with (runtime_dir / "events.jsonl").open("w", encoding="utf-8") as handle:
        for index in range(10_000):
            handle.write(json.dumps({"type": "progress", "index": index}) + "\n")
    return job_id


def test_sse_initial_replay_is_bounded_by_history(client: TestClient, tmp_path: Path) -> None:
    # Given: a terminal job with a 10,000-event backlog
    job_id = _create_terminal_backlog_job(client, tmp_path)

    # When: the client requests a 200-event initial history
    response = client.get(f"/api/jobs/{job_id}/events?history=200")

    # Then: the burst contains at most history events plus protocol/terminal slack
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert len(events) <= 202
    progress = [
        (event_id, event_type) for event_id, event_type in events if event_type == "progress"
    ]
    assert [event_id for event_id, _ in progress] == list(range(9_801, 10_001))
    assert events[-1] == (10_001, "done")


def test_sse_after_seq_resume_has_no_duplicate_or_gap(client: TestClient, tmp_path: Path) -> None:
    # Given: a terminal job with stable absolute event positions
    job_id = _create_terminal_backlog_job(client, tmp_path)

    # When: the client resumes after event 9,990
    response = client.get(f"/api/jobs/{job_id}/events?after_seq=9990")

    # Then: only the following event ids and one terminal id are emitted
    assert response.status_code == 200
    events = _sse_events(response.text)
    progress_ids = [event_id for event_id, event_type in events if event_type == "progress"]
    assert progress_ids == list(range(9_991, 10_001))
    assert len(progress_ids) == len(set(progress_ids))
    assert events[-1] == (10_001, "done")


def test_sse_terminal_job_stream_closes_after_done(client: TestClient, tmp_path: Path) -> None:
    # Given: a completed job and its persisted event backlog
    job_id = _create_terminal_backlog_job(client, tmp_path)

    # When: a client opens the event stream
    response = client.get(f"/api/jobs/{job_id}/events?history=1")

    # Then: the terminal event is present and the finite response has closed
    assert response.status_code == 200
    assert response.text.endswith("\n\n")
    assert _sse_events(response.text)[-1][1] == "done"
