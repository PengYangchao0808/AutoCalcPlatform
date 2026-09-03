from __future__ import annotations

import json
from pathlib import Path

from acp.scheduler.events import JobEventLog


def _write_jsonl(path: Path, lines: list[str]) -> None:
    _ = path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def test_incremental_readers_handle_missing_and_empty_files(tmp_path: Path) -> None:
    # Given: a missing event log, followed by an empty one
    log = JobEventLog(tmp_path / "missing" / "events.jsonl")

    # When: each incremental reader is called
    count = log.count()
    recent = log.read_recent(200)
    last = log.read_last()

    # Then: all readers return their empty-file contracts
    assert count == 0
    assert recent == (0, [])
    assert last is None

    # When: the missing path is materialized as an empty file
    _ = log.path.touch()

    # Then: an empty file has the same reader contract
    assert log.count() == 0
    assert log.read_recent(200) == (0, [])
    assert log.read_last() is None


def test_read_recent_uses_absolute_start_for_large_log(tmp_path: Path) -> None:
    # Given: a 10,000-line append-only event log
    path = tmp_path / "events.jsonl"
    _write_jsonl(
        path,
        [json.dumps({"type": "progress", "index": index}) for index in range(10_000)],
    )
    log = JobEventLog(path)

    # When: the last 200 event slots are requested
    start, events = log.read_recent(200)

    # Then: the physical absolute window is preserved and only that window is returned
    assert log.count() == 10_000
    assert start == 9_800
    assert len(events) == 200
    assert events[0]["index"] == 9_800
    assert events[-1]["index"] == 9_999
    assert log.read_recent(0) == (10_000, [])


def test_incremental_readers_ignore_partial_trailing_line(tmp_path: Path) -> None:
    # Given: two complete events and a JSON line without its terminating newline
    path = tmp_path / "events.jsonl"
    _ = path.write_text(
        '{"type":"progress","index":0}\n{"type":"progress","index":1}\n'
        + '{"type":"progress","index":2}',
        encoding="utf-8",
    )
    log = JobEventLog(path)

    # When: the log is read before the trailing line is complete
    start, events = log.read_recent(10)

    # Then: the partial line has not consumed a slot or appeared in output
    assert log.count() == 2
    assert start == 0
    assert [event["index"] for event in events] == [0, 1]
    last = log.read_last()
    assert last is not None
    assert events[-1]["index"] == last["index"]

    # When: the missing newline is appended
    with path.open("ab") as handle:
        _ = handle.write(b"\n")

    # Then: the formerly partial event becomes readable
    assert log.count() == 3
    last = log.read_last()
    assert last is not None
    assert last["index"] == 2


def test_bad_json_consumes_absolute_sequence_slot(tmp_path: Path) -> None:
    # Given: valid events separated by an undecodable JSON line
    path = tmp_path / "events.jsonl"
    _write_jsonl(
        path,
        [
            '{"type":"progress","index":0}',
            "{not-json}",
            '{"type":"progress","index":2}',
        ],
    )
    log = JobEventLog(path)

    # When: recent windows and the last event are read
    all_start, all_events = log.read_recent(3)
    recent_start, recent_events = log.read_recent(2)
    last = log.read_last()

    # Then: invalid output is skipped, but its absolute position remains counted
    assert log.count() == 3
    assert all_start == 0
    assert [event["index"] for event in all_events] == [0, 2]
    assert recent_start == 1
    assert [event["index"] for event in recent_events] == [2]
    assert last is not None
    assert last["index"] == 2
