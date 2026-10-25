"""B04-1 (todo 22) — versioned persistent event protocol tests.

Covers the acceptance clauses:

1. frozen wire fields — ``EventEnvelope`` round-trips the twelve §3.1 fields and
   the tracked ``events_*.json`` samples classify exactly as the frozen
   ``classify_events`` semantics (normal / empty / partial / corrupt /
   old-version);
2. append + replay by cursor over the **real** ``JobEventLog`` JSONL (via the
   acp-side bootstrap adapter);
3. idempotent dedup on the stable event ID (double append appends nothing);
4. expired cursor → explicit snapshot-reset signal, and a negative guard that it
   can never masquerade as a silent empty success;
5. legacy ``after_seq`` JSONL compatibility (old records migrated, old readers
   unaffected);
6. no ``acp`` import on the Services side of the new modules.

The old SSE endpoint is intentionally *not* modified by this todo; its regression
is owned by the existing ``tests/test_api_sse_backlog.py`` suite (run alongside).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from acp.scheduler.events import JobEventLog
from acp.services_events_bootstrap import (
    JobEventLogPort,
    open_persistent_event_service_for_path,
)
from acp_services.contracts.events import (
    CURRENT_EVENT_SCHEMA_VERSION,
    EVENT_ENVELOPE_FIELDS,
    OWNER_COMPONENT_BY_TYPE,
    EventCursor,
    EventCursorError,
    EventEnvelope,
    EventOwner,
    EventScope,
    EventSerializationError,
    EventStreamCorruptError,
    OwnerType,
    validate_event_sequence,
)
from acp_services.runtime.events import (
    EventLogPort,
    EventReplay,
    PersistentEventService,
    envelopes_from_records,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SAMPLES = _REPO_ROOT / "tests" / "baseline" / "services-evidence" / "contract_samples"

_JOB = OwnerType.JOB
_JOB_ID = "job-1"
_SCOPE = EventScope.TASK


def _sample(kind: str) -> dict:
    return json.loads((_SAMPLES / f"events_{kind}.json").read_text(encoding="utf-8"))["body"]


def _service(path: Path, *, retention: int | None = None) -> PersistentEventService:
    return open_persistent_event_service_for_path(
        path, owner_type=_JOB, owner_id=_JOB_ID, scope=_SCOPE, retention=retention
    )


# ---------------------------------------------------------------------------
# (1) frozen wire shape + sample classification
# ---------------------------------------------------------------------------


def test_envelope_wire_fields_are_the_frozen_twelve() -> None:
    envelope = EventEnvelope(
        event_id="e1",
        seq=1,
        owner_type=_JOB,
        owner_id="j1",
        scope=_SCOPE,
        event_type="state.changed",
        emitted_at="2026-10-18T00:00:00Z",
    )

    # Given/When: the envelope is serialized
    wire = envelope.to_dict()

    # Then: exactly the frozen twelve fields are present, in canonical order
    assert tuple(wire) == EVENT_ENVELOPE_FIELDS
    # cursor/dedup/coverage/schema_version are derived, never left blank
    assert wire["cursor"] == "1"
    assert wire["dedup_key"] == "e1"
    assert wire["coverage"] == "task"
    assert wire["schema_version"] == CURRENT_EVENT_SCHEMA_VERSION


def test_envelope_round_trips_normal_sample() -> None:
    body = _sample("normal")

    # When: the frozen normal events are decoded
    events = [EventEnvelope.from_dict(item) for item in body["events"]]

    # Then: identity, cursor and owner/scope are preserved
    assert [event.seq for event in events] == [1, 2]
    assert events[0].event_type == "state.changed"
    assert events[0].cursor_value.value == "1"
    validate_event_sequence(events)
    assert events[0].publishing_component is EventOwner.RUNTIME


def test_empty_stream_is_valid() -> None:
    # Given/When: the frozen empty sample
    body = _sample("empty")

    # Then: an empty event list decodes to no envelopes and is not an error
    assert body["events"] == []
    assert envelopes_from_records([], owner_type=_JOB, owner_id=_JOB_ID, scope=_SCOPE) == []


def test_corrupt_sample_seq_regression_is_rejected() -> None:
    body = _sample("corrupt")
    events = [EventEnvelope.from_dict(item) for item in body["events"]]

    # When/Then: a per-owner seq regression raises rather than being reordered
    with pytest.raises(EventStreamCorruptError):
        validate_event_sequence(events)


def test_old_version_sample_migrates_to_explicit_envelope() -> None:
    body = _sample("old-version")
    legacy = body["events"][0]

    # When: the legacy record (after_seq + type only) is migrated
    envelope = EventEnvelope.from_legacy_record(
        legacy, owner_type=_JOB, owner_id=_JOB_ID, scope=_SCOPE, seq=1
    )

    # Then: owner/scope are filled and the legacy type is preserved
    assert envelope.seq == 1
    assert envelope.event_type == "state.changed"
    assert envelope.owner_type is _JOB
    assert envelope.scope is _SCOPE
    assert envelope.schema_version == CURRENT_EVENT_SCHEMA_VERSION


def test_partial_sample_decodes_within_reserved_keys() -> None:
    body = _sample("partial")

    # When/Then: a single well-formed event decodes without error
    events = [EventEnvelope.from_dict(item) for item in body["events"]]
    validate_event_sequence(events)
    assert events[0].payload == {}


def test_owner_component_mapping_is_documented() -> None:
    # Then: the plan's Runtime/Data/Workspace vocabulary maps onto the wire enums
    assert OWNER_COMPONENT_BY_TYPE[OwnerType.JOB] is EventOwner.RUNTIME
    assert OWNER_COMPONENT_BY_TYPE[OwnerType.TASK] is EventOwner.RUNTIME
    assert OWNER_COMPONENT_BY_TYPE[OwnerType.PROJECT] is EventOwner.WORKSPACE
    assert OWNER_COMPONENT_BY_TYPE[OwnerType.DATA] is EventOwner.DATA


def test_unknown_field_and_cursor_mismatch_are_rejected() -> None:
    # Given: a wire dict with an unexpected field
    wire = EventEnvelope(
        event_id="e1",
        seq=1,
        owner_type=_JOB,
        owner_id="j1",
        scope=_SCOPE,
        event_type="x",
    ).to_dict()
    wire["surprise"] = 1
    with pytest.raises(EventSerializationError):
        EventEnvelope.from_dict(wire)

    # Given: a cursor that disagrees with seq
    with pytest.raises(EventCursorError):
        EventEnvelope(
            event_id="e2",
            seq=2,
            owner_type=_JOB,
            owner_id="j1",
            scope=_SCOPE,
            event_type="x",
            cursor="9",
        )


def test_cursor_wire_roundtrip() -> None:
    cursor = EventCursor(owner_type=_JOB, owner_id="j1", seq=7)
    assert cursor.value == "7"
    assert EventCursor.from_wire("7", owner_type=_JOB, owner_id="j1") == cursor
    assert cursor.advance().seq == 8
    with pytest.raises(EventCursorError):
        EventCursor.from_wire("nope", owner_type=_JOB, owner_id="j1")


# ---------------------------------------------------------------------------
# (2) append + replay by cursor (real JobEventLog)
# ---------------------------------------------------------------------------


def test_append_then_replay_by_cursor(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    service = _service(path)

    # Given: three durable facts
    service.append("state.changed", payload={"status": "QUEUED"}, dedup_key="k1")
    service.append("state.changed", payload={"status": "RUNNING"}, dedup_key="k2")
    service.append("progress", payload={"p": 0.5}, dedup_key="k3")

    # When: replaying from the start
    replay = service.replay(0)

    # Then: all three are returned in order with a persistent cursor
    assert [event.seq for event in replay.events] == [1, 2, 3]
    assert replay.cursor is not None and replay.cursor.value == "3"
    assert replay.reset is False

    # When: resuming strictly after event 1
    resume = service.replay(1)

    # Then: only the following events are emitted (no duplicate, no gap)
    assert [event.seq for event in resume.events] == [2, 3]

    # And: a fresh process can rebuild the cursor from the durable file alone
    reopened = _service(path)
    assert [event.seq for event in reopened.replay(0).events] == [1, 2, 3]


def test_replay_limit_is_respected(tmp_path: Path) -> None:
    service = _service(tmp_path / "events.jsonl")
    for index in range(5):
        service.append("progress", payload={"i": index}, dedup_key=f"k{index}")

    replay = service.replay(0, limit=2)

    assert [event.seq for event in replay.events] == [1, 2]
    assert replay.cursor is not None and replay.cursor.value == "2"


def test_replay_rejects_negative_cursor(tmp_path: Path) -> None:
    service = _service(tmp_path / "events.jsonl")
    with pytest.raises(EventCursorError):
        service.replay(-1)


# ---------------------------------------------------------------------------
# (3) idempotent dedup on the stable event ID
# ---------------------------------------------------------------------------


def test_double_append_with_same_dedup_key_is_single_event(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    service = _service(path)

    first = service.append("state.changed", payload={"status": "DONE"}, dedup_key="stable-1")
    second = service.append("state.changed", payload={"status": "DONE"}, dedup_key="stable-1")

    # Then: the same envelope is returned and nothing was written twice
    assert first == second
    assert JobEventLog(path).count() == 1
    assert len(service.snapshot()) == 1


def test_derived_event_id_is_stable_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    _service(path).append("state.changed", payload={"status": "A"})

    reopened = _service(path)
    # Re-delivering the derived identity appends nothing (crash-before-ack replay)
    again = reopened.append("state.changed", payload={"status": "A"}, dedup_key="job:job-1:1")
    assert again.seq == 1
    assert JobEventLog(path).count() == 1


# ---------------------------------------------------------------------------
# (4) expired cursor → explicit snapshot reset (never a silent drop)
# ---------------------------------------------------------------------------


def test_expired_cursor_signals_snapshot_reset(tmp_path: Path) -> None:
    service = _service(tmp_path / "events.jsonl", retention=2)
    for index in range(5):
        service.append("progress", payload={"i": index}, dedup_key=f"k{index}")

    # When: the consumer resumes from a cursor older than the retained window
    replay = service.replay(1)

    # Then: an explicit reset is signalled, not a silent empty success
    assert replay.reset is True
    assert replay.reset_reason == "cursor_expired"
    assert replay.events == ()
    assert replay.earliest_seq == 4
    assert replay.latest_seq == 5


def test_failure_expired_cursor_does_not_silently_drop(tmp_path: Path) -> None:
    """Negative QA: an expired cursor must never look like a caught-up success."""
    service = _service(tmp_path / "events.jsonl", retention=2)
    for index in range(5):
        service.append("progress", payload={"i": index}, dedup_key=f"k{index}")

    replay = service.replay(1)

    # An empty result is only legitimate when reset=False *and* no events exist
    # after the cursor. Here events 2..5 exist, so an empty non-reset result
    # would be a silent drop.
    assert not (replay.events == () and replay.reset is False), "expired cursor silently dropped"

    # Recovery is an explicit full re-read
    snapshot = service.snapshot()
    assert [event.seq for event in snapshot] == [1, 2, 3, 4, 5]


def test_fresh_subscription_returns_retained_tail(tmp_path: Path) -> None:
    service = _service(tmp_path / "events.jsonl", retention=2)
    for index in range(5):
        service.append("progress", payload={"i": index}, dedup_key=f"k{index}")

    replay = service.replay(0)

    assert [event.seq for event in replay.events] == [4, 5]
    assert replay.reset is False


def test_caught_up_replay_is_empty_and_not_reset(tmp_path: Path) -> None:
    service = _service(tmp_path / "events.jsonl", retention=2)
    for index in range(3):
        service.append("progress", payload={"i": index}, dedup_key=f"k{index}")

    replay = service.replay(3)

    assert replay.events == ()
    assert replay.reset is False
    assert replay.cursor is not None and replay.cursor.value == "3"


# ---------------------------------------------------------------------------
# (5) legacy after_seq JSONL compatibility
# ---------------------------------------------------------------------------


def test_legacy_jsonl_records_are_migrated_on_read(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = JobEventLog(path)
    # A record written by the legacy code paths: no schema_version / owner fields
    log.append("progress", index=0)

    service = _service(path)
    snapshot = service.snapshot()

    # Then: the legacy record is migrated with owner/scope and payload preserved
    assert len(snapshot) == 1
    assert snapshot[0].event_type == "progress"
    assert snapshot[0].owner_type is _JOB
    assert snapshot[0].payload == {"index": 0}

    # And: the underlying log still exposes the raw legacy record untouched
    raw = JobEventLog(path).read_all()
    assert raw[0]["type"] == "progress"
    assert "schema_version" not in raw[0]


def test_mixed_envelope_and_legacy_records_stay_monotonic(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    service = _service(path)
    service.append("modern", payload={"a": 1}, dedup_key="m1")
    # A late legacy write that lands after the envelope records
    JobEventLog(path).append("legacy", foo="bar")

    snapshot = service.snapshot()

    assert [event.event_type for event in snapshot] == ["modern", "legacy"]
    assert [event.seq for event in snapshot] == [1, 2]
    validate_event_sequence(snapshot)


def test_corrupt_envelope_pair_in_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    first = EventEnvelope(
        event_id="e2",
        seq=2,
        owner_type=_JOB,
        owner_id=_JOB_ID,
        scope=_SCOPE,
        event_type="x",
    ).to_dict()
    second = EventEnvelope(
        event_id="e1",
        seq=1,
        owner_type=_JOB,
        owner_id=_JOB_ID,
        scope=_SCOPE,
        event_type="x",
    ).to_dict()
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(first) + "\n")
        handle.write(json.dumps(second) + "\n")

    service = _service(path)
    with pytest.raises(EventStreamCorruptError):
        service.snapshot()


# ---------------------------------------------------------------------------
# (6) port adaptation + Services-side acp-import ban
# ---------------------------------------------------------------------------


def test_job_event_log_port_satisfies_the_protocol(tmp_path: Path) -> None:
    port = JobEventLogPort(JobEventLog(tmp_path / "events.jsonl"))
    assert isinstance(port, EventLogPort)


def test_replay_dataclass_defaults_are_falsey_and_safe() -> None:
    empty = EventReplay()
    assert bool(empty) is False
    assert empty.reset is False


def test_services_event_modules_never_import_acp() -> None:
    for rel in (
        "runtime/events.py",
        "contracts/events.py",
        "events.py",
    ):
        source = (_REPO_ROOT / "src" / "acp_services" / rel).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith("acp.") for alias in node.names), rel
                assert all(alias.name != "acp" for alias in node.names), rel
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module != "acp" and not module.startswith("acp."), f"{rel}: {module}"
