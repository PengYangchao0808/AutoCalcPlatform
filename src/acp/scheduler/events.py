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
from typing import Any


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
