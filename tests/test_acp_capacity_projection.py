"""Admission-view capacity projection (U04: agents choose resources from facts)."""

from __future__ import annotations

from acp.api.capacity import _parse_mem_mb, build_capacity_projection


class _Record:
    def __init__(self, cores: int, mem: str) -> None:
        class _Spec:
            resources = {"nproc": cores, "mem": mem}

        self.spec = _Spec()


class _Store:
    def __init__(self, running: list[_Record], queued: list[_Record]) -> None:
        self._running = running
        self._queued = queued

    def counts(self) -> dict[str, int]:
        return {
            "running": len(self._running),
            "queued": len(self._queued),
            "paused": 0,
            "waiting_review": 0,
            "completed": 3,
        }

    def list(self, status: str | None = None, limit: int = 200) -> list[_Record]:
        return list(self._running if status == "running" else self._queued)


class _Manager:
    def __init__(self, running: list[_Record], queued: list[_Record], max_running: int = 4) -> None:
        self.store = _Store(running, queued)
        self.max_running = max_running


def test_memory_parser_handles_units_and_plain_numbers() -> None:
    assert _parse_mem_mb("8GB") == 8192
    assert _parse_mem_mb("4GiB") == 4096
    assert _parse_mem_mb("4096") == 4096
    assert _parse_mem_mb("512MB") == 512
    assert _parse_mem_mb(None) is None
    assert _parse_mem_mb("lots") is None


def test_projection_sums_active_reservations_and_slots() -> None:
    cap = build_capacity_projection(
        _Manager([_Record(4, "8GB"), _Record(2, "4GB")], [_Record(1, "1GB")])
    )
    assert cap["schema"] == "acp2.capacity.view.v1"
    assert "admission-view" in cap["semantics"]
    assert cap["in_use"]["cores"] == 7
    assert cap["in_use"]["memory_mb"] == 13312
    assert cap["in_use"]["active_jobs"] == 3
    assert cap["concurrency"]["running"] == 2
    assert cap["concurrency"]["slots_free"] == 2
    assert cap["available"]["cores"] is not None
    assert cap["guidance"]


def test_unknown_is_explicit_not_a_zero() -> None:
    class _NoStore:
        pass

    cap = build_capacity_projection(_NoStore())
    assert "in_use" in cap["unknown"]
    assert cap["available"]["cores"] is None or cap["in_use"]["cores"] == 0
