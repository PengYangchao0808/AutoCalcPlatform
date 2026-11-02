"""Round-9 M13: ``live_status`` must be live, or honestly say it is stale.

The projection now carries ``observed_age_seconds`` / ``stale`` / ``source_mtime``
computed from the state payload on every call, the progress writer stamps
``observed_at`` on every rewrite, and :func:`read_live_status` re-reads the file
instead of reusing a cached payload.  None of these fields is ever fabricated:
a missing/unparseable timestamp yields ``None``, never a healthy ``False``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import acp.calculations.progress as progress_module
from acp.api.job_status_view import (
    LIVE_STATUS_STALE_SECONDS,
    build_live_status,
    read_live_status,
)
from acp.calculations.progress import LiveMetric, ProgressReporter

_NOW = datetime(2026, 11, 2, 12, 0, 0, tzinfo=timezone.utc)


def _state(observed_at: str | None, **extra: object) -> dict[str, object]:
    """Return a state.json-shaped payload with one live metric."""
    data: dict[str, object] = {
        "current_stage": "optimize",
        "stage_index": 2,
        "stage_total": 4,
        "live_metrics": [
            {"key": "opt_step", "value": "Step 4", "kind": "iteration", "priority": 100}
        ],
    }
    if observed_at is not None:
        data["updated_at"] = observed_at
    data.update(extra)
    return data


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _read_state(work_dir: Path) -> dict[str, object]:
    return json.loads((work_dir / "state.json").read_text(encoding="utf-8"))


def test_freshness_window_is_bounded() -> None:
    # Given/When/Then: the contract names a positive bounded window.
    assert LIVE_STATUS_STALE_SECONDS > 0
    assert LIVE_STATUS_STALE_SECONDS == 90.0


def test_recent_observation_is_fresh() -> None:
    # Given: a state payload observed ten seconds ago.
    state = _state(_iso(_NOW - timedelta(seconds=10)))

    # When: it is projected.
    live = build_live_status(state, now=_NOW)

    # Then: the age is reported and the view is not stale.
    assert live is not None
    assert live.observed_at == _iso(_NOW - timedelta(seconds=10))
    assert live.observed_age_seconds == pytest.approx(10.0)
    assert live.stale is False
    assert live.source_mtime is None


def test_observation_older_than_the_window_is_stale() -> None:
    # Given: an observation just past the bounded window.
    state = _state(_iso(_NOW - timedelta(seconds=LIVE_STATUS_STALE_SECONDS + 1)))

    # When: it is projected.
    live = build_live_status(state, now=_NOW)

    # Then: the age crosses the threshold and the view says so.
    assert live is not None
    assert live.observed_age_seconds == pytest.approx(LIVE_STATUS_STALE_SECONDS + 1)
    assert live.stale is True


@pytest.mark.parametrize("observed_at", [None, "", "not-a-timestamp", 12345])
def test_unknown_observation_time_is_not_reported_fresh(observed_at: object) -> None:
    # Given: a payload whose observation time cannot be read.
    state = _state(None)
    if observed_at is not None:
        state["updated_at"] = observed_at

    # When: it is projected.
    live = build_live_status(state, now=_NOW)

    # Then: freshness is unknown, never fabricated as healthy.
    assert live is not None
    assert live.stage_label is not None
    assert live.observed_age_seconds is None
    assert live.stale is None


def test_newer_state_file_mtime_marks_the_reading_stale() -> None:
    # Given: a payload recorded 20s ago whose file was written 5 minutes later.
    observed = _NOW - timedelta(seconds=20)
    state = _state(_iso(observed))

    # When: the read path supplies the file mtime.
    live = build_live_status(
        state,
        now=_NOW,
        source_mtime=(observed + timedelta(minutes=5)).timestamp(),
    )

    # Then: the recorded observation is not presented as current.
    assert live is not None
    assert live.observed_age_seconds == pytest.approx(20.0)
    assert live.stale is True
    assert live.source_mtime is not None
    assert datetime.fromisoformat(live.source_mtime) == observed + timedelta(minutes=5)


def test_write_order_mtime_within_tolerance_is_not_stale() -> None:
    # Given: a normal write where the file mtime trails the payload by 2s.
    observed = _NOW - timedelta(seconds=15)
    state = _state(_iso(observed))

    # When: the mtime is supplied.
    live = build_live_status(
        state,
        now=_NOW,
        source_mtime=(observed + timedelta(seconds=2)).timestamp(),
    )

    # Then: ordinary write ordering is not mistaken for a stale observation.
    assert live is not None
    assert live.stale is False


def test_cached_payload_is_never_reused_as_fresh() -> None:
    # Given: a state payload carrying an old top-level observation and a nested
    # (would-be cached) live_status block claiming a newer one.
    old = _NOW - timedelta(minutes=30)
    state = _state(
        _iso(old),
        live_status={"observed_at": _iso(_NOW), "stale": False},
    )

    # When: it is projected twice at different clocks.
    first = build_live_status(state, now=_NOW)
    second = build_live_status(state, now=_NOW + timedelta(seconds=30))

    # Then: the projection reads the payload timestamp only, and the age grows.
    assert first is not None and second is not None
    assert first.observed_at == _iso(old)
    assert first.stale is True
    assert second.observed_age_seconds == pytest.approx(1830.0)


def test_read_live_status_rereads_the_current_bytes(tmp_path: Path) -> None:
    # Given: a state file observed an hour ago whose mtime matches that write.
    state_path = tmp_path / "state.json"
    old = _NOW - timedelta(hours=1)
    state_path.write_text(json.dumps(_state(_iso(old))), encoding="utf-8")
    os.utime(state_path, (old.timestamp(), old.timestamp()))

    # When: the defensive read path is called.
    stale_live = read_live_status(state_path, now=_NOW)

    # Then: the file's mtime travels with the projection and it is stale.
    assert stale_live is not None
    assert stale_live.observed_age_seconds == pytest.approx(3600.0)
    assert stale_live.stale is True
    assert stale_live.source_mtime is not None

    # When: the writer rewrites the same path with a fresh observation.
    fresh = _NOW - timedelta(seconds=5)
    state_path.write_text(json.dumps(_state(_iso(fresh))), encoding="utf-8")
    os.utime(state_path, (_NOW.timestamp(), _NOW.timestamp()))
    fresh_live = read_live_status(state_path, now=_NOW)

    # Then: the new bytes are used — no cached observed_at is reused.
    assert fresh_live is not None
    assert fresh_live.observed_at == _iso(fresh)
    assert fresh_live.observed_age_seconds == pytest.approx(5.0)
    assert fresh_live.stale is False
    assert fresh_live.source_mtime is not None
    assert datetime.fromisoformat(fresh_live.source_mtime) == _NOW


def test_read_live_status_returns_none_for_missing_or_invalid_files(tmp_path: Path) -> None:
    # Given: an absent path and an unparsable file.
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not json", encoding="utf-8")

    # When/Then: no projection is invented for either.
    assert read_live_status(tmp_path / "missing.json", now=_NOW) is None
    assert read_live_status(invalid, now=_NOW) is None


def test_progress_writer_stamps_observed_at_on_every_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a deterministic clock that advances on every reading.
    ticks = {"n": 0}

    def fake_now() -> str:
        ticks["n"] += 1
        return (_NOW + timedelta(seconds=ticks["n"])).isoformat()

    monkeypatch.setattr(progress_module, "_iso_now", fake_now)

    reporter = ProgressReporter(tmp_path, stages=["optimize"])
    reporter.initialize()
    first = _read_state(tmp_path)

    reporter.set_live_metrics(
        [LiveMetric(key="opt_step", value="Step 2", kind="iteration", priority=100)]
    )
    second = _read_state(tmp_path)

    # Then: each write carries one instant for both fields and it advances.
    assert first["observed_at"] == first["updated_at"]
    assert second["observed_at"] == second["updated_at"]
    assert isinstance(first["observed_at"], str)
    assert isinstance(second["observed_at"], str)
    assert second["observed_at"] > first["observed_at"]

    # And the projection accepts the writer's stamp without a fabricated age.
    live = build_live_status(second, now=_NOW + timedelta(seconds=ticks["n"] + 3))
    assert live is not None
    assert live.observed_age_seconds == pytest.approx(3.0)
    assert live.stale is False


def test_batch_item_transition_is_visible_before_the_first_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a batch reporter whose clock advances on every reading.
    ticks = {"n": 0}

    def fake_now() -> str:
        ticks["n"] += 1
        return (_NOW + timedelta(seconds=ticks["n"])).isoformat()

    monkeypatch.setattr(progress_module, "_iso_now", fake_now)
    reporter = ProgressReporter(tmp_path, stages=["optimize", "frequency"])
    reporter.configure_batch(2, ["optimize", "frequency"])
    reporter.initialize()

    # When: the second item begins, before any stage starts.
    reporter.begin_batch_item(1)
    started = _read_state(tmp_path)

    # Then: the parent snapshot already names the new item and its fresh
    # observation, with every per-item stage reset to pending.
    assert started["batch_item_index"] == 1
    assert started["batch_item_total"] == 2
    assert started["observed_at"] == started["updated_at"]
    assert {info["status"] for info in started["stages"].values()} == {"pending"}

    # When: item-level metrics update and the item finishes.
    reporter.update_live_metrics(
        [LiveMetric(key="batch_step", value="optimize", kind="status", priority=90)]
    )
    running = _read_state(tmp_path)
    reporter.finish_batch_item(1)
    finished = _read_state(tmp_path)

    # Then: the batch context survives and each write refreshes the observation.
    assert {metric["key"] for metric in running["live_metrics"]} == {"batch_step"}
    assert running["observed_at"] > started["observed_at"]
    assert finished["observed_at"] > running["observed_at"]


def test_live_status_metrics_stay_bounded(tmp_path: Path) -> None:
    # Given: four candidate metrics written by the reporter.
    reporter = ProgressReporter(tmp_path, stages=["optimize"])
    reporter.initialize()
    reporter.set_live_metrics(
        [
            LiveMetric(key=f"metric_{index}", value=str(index), kind="count", priority=index)
            for index in range(4)
        ]
    )

    # When: the snapshot is projected.
    live = build_live_status(_read_state(tmp_path))

    # Then: the round-8 bound holds.
    assert live is not None
    assert len(live.metrics) <= 3


def test_api_enrichment_projects_freshness_for_a_live_job(tmp_path: Path) -> None:
    # Given: a running job whose state.json was observed an hour ago.
    from acp.api.v1_routes import _enrich_job_snapshot, _record_to_v1_model
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

    observed = datetime.now(timezone.utc) - timedelta(hours=1)
    (tmp_path / "state.json").write_text(json.dumps(_state(_iso(observed))), encoding="utf-8")
    record = JobRecord(
        id="round9-live-status-freshness",
        spec=JobSpec(workflow="optimize"),
        status=JobStatus.RUNNING,
        work_dir=str(tmp_path),
    )

    # When: the v1 read path enriches the job snapshot.
    enriched = _enrich_job_snapshot(record, _record_to_v1_model(record))

    # Then: the stale observation is visible in the API projection itself — no
    # caller has to recompute the age.
    live = enriched.live_status
    assert live is not None
    assert live.observed_age_seconds is not None
    assert live.observed_age_seconds >= LIVE_STATUS_STALE_SECONDS
    assert live.stale is True
