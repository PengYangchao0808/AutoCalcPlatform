"""
Scheduler Events
================

Append-only JSONL event log per job. The SSE endpoint replays history then
tails the file for live updates, which keeps events durable across restarts.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Final

_READ_CHUNK_SIZE: Final = 64 * 1024


def _decode_event(line: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(line.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _newline_positions_backwards(handle: BinaryIO, end: int) -> Iterator[int]:
    position = end
    while position > 0:
        start = max(0, position - _READ_CHUNK_SIZE)
        handle.seek(start)
        chunk = handle.read(position - start)
        for offset in range(len(chunk) - 1, -1, -1):
            if chunk[offset] == 10:
                yield start + offset
        position = start


def _complete_lines(handle: BinaryIO, start: int, end: int) -> Iterator[bytes]:
    handle.seek(start)
    remaining = max(0, end - start)
    buffer = bytearray()
    while remaining > 0:
        chunk = handle.read(min(_READ_CHUNK_SIZE, remaining))
        if not chunk:
            return
        remaining -= len(chunk)
        buffer.extend(chunk)
        consumed = 0
        while True:
            newline = buffer.find(b"\n", consumed)
            if newline < 0:
                break
            yield bytes(buffer[consumed:newline])
            consumed = newline + 1
        if consumed:
            del buffer[:consumed]


def _complete_end(handle: BinaryIO, file_size: int) -> int:
    for position in _newline_positions_backwards(handle, file_size):
        return position + 1
    return 0


def _complete_lines_backwards(handle: BinaryIO, file_size: int) -> Iterator[bytes]:
    pending = bytearray()
    complete_boundary_seen = False
    position = file_size
    while position > 0:
        start = max(0, position - _READ_CHUNK_SIZE)
        handle.seek(start)
        chunk = handle.read(position - start)
        for byte in reversed(chunk):
            if byte == 10:
                if complete_boundary_seen:
                    yield bytes(reversed(pending))
                complete_boundary_seen = True
                pending.clear()
            elif complete_boundary_seen:
                pending.append(byte)
        position = start
    if complete_boundary_seen:
        yield bytes(reversed(pending))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobEventLog:
    """Append/read the ``events.jsonl`` stream for one job."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event_type: str, **data: Any) -> None:
        record: dict[str, Any] = {"type": event_type, "timestamp": _utc_now_iso()}
        record.update(data)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")

    def count(self) -> int:
        """Count newline-terminated event slots without decoding the file."""
        try:
            with self.path.open("rb") as handle:
                total = 0
                while chunk := handle.read(_READ_CHUNK_SIZE):
                    total += chunk.count(b"\n")
                return total
        except FileNotFoundError:
            return 0

    def read_recent(self, n: int) -> tuple[int, list[dict[str, Any]]]:
        """Return the last *n* complete event slots and their absolute start."""
        total = self.count()
        if n <= 0 or total == 0:
            return total, []

        start_index = max(0, total - n)
        try:
            with self.path.open("rb") as handle:
                file_size = handle.seek(0, 2)
                complete_end = _complete_end(handle, file_size)
                if complete_end == 0:
                    return 0, []

                start_offset = 0
                if start_index > 0:
                    boundary_count = 0
                    for position in _newline_positions_backwards(handle, complete_end):
                        boundary_count += 1
                        if boundary_count == n + 1:
                            start_offset = position + 1
                            break

                events = []
                for line in _complete_lines(handle, start_offset, complete_end):
                    event = _decode_event(line)
                    if event is not None:
                        events.append(event)
                return start_index, events
        except FileNotFoundError:
            return 0, []

    def read_last(self) -> dict[str, Any] | None:
        """Return the last decodable complete event, ignoring corrupt lines."""
        try:
            with self.path.open("rb") as handle:
                file_size = handle.seek(0, 2)
                for line in _complete_lines_backwards(handle, file_size):
                    event = _decode_event(line)
                    if event is not None:
                        return event
        except FileNotFoundError:
            return None
        return None

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return out

    def tail(self, since_index: int = 0) -> Iterator[dict[str, Any]]:
        """Yield events from ``since_index`` onward, polling for new lines."""
        idx = 0
        pos = 0
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if idx >= since_index:
                    line = line.strip()
                    if line:
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            pass
                idx += 1
            pos = handle.tell()
        while True:
            time.sleep(0.5)
            with self.path.open("r", encoding="utf-8") as handle:
                handle.seek(pos)
                for line in handle:
                    line = line.strip()
                    pos += len(line.encode("utf-8")) + 1
                    if line:
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            pass


__all__ = ["JobEventLog"]
