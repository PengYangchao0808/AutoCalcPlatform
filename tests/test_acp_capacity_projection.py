"""Admission-view capacity projection (U04: agents choose resources from facts).

The projection must reuse the existing authorities — AdmissionLimits/Capacity,
``resolve_job_resources`` and the single memory parser — instead of re-deriving
resource rules (round-12 maintainer note).
"""

from __future__ import annotations

from acp.api.capacity import build_capacity_projection
from acp_services.runtime.local_admission import AdmissionLimits


class _Capacity:
    cores = 16
    memory_mb = 65536
    concurrency = 4
    storage_bytes = 10**12
    source = "local-single-enforced"
    available = True


class _Controller:
    def capacity(self) -> _Capacity:
        return _Capacity()


class _Admission:
    limits = AdmissionLimits(
        max_cores=32, max_memory_mb=131072, max_tasks=8, max_walltime_s=86400,
        max_storage_bytes=2 * 10**12,
    )
    controller = _Controller()


class _Record:
    class _Spec:
        resources = {"nproc": 4, "mem": "8GB"}

    spec = _Spec()


class _Store:
    def __init__(self, running: list, queued: list) -> None:
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

    def list(self, status: str | None = None, limit: int = 200) -> list:
        return list(self._running if status == "running" else self._queued)


class _Manager:
    def __init__(self, running: list, queued: list, max_running: int = 4) -> None:
        self.store = _Store(running, queued)
        self.max_running = max_running


def test_projection_reads_the_admission_authority() -> None:
    cap = build_capacity_projection(
        _Manager([_Record(), _Record()], [_Record()]), admission=_Admission()
    )
    assert cap["schema"] == "acp2.capacity.view.v1"
    assert "admission-view" in cap["semantics"]
    # limits come from AdmissionLimits (the same definitions admission enforces)
    assert cap["limits"]["max_cores"] == 32
    assert cap["limits"]["max_memory_mb"] == 131072
    # pool comes from controller.capacity()
    assert cap["pool"]["cores"] == 16
    assert cap["pool"]["source"] == "local-single-enforced"
    # reservations are normalised by the shared resolver: 3 x (4 cores, 8 GB)
    assert cap["in_use"]["cores"] == 12
    assert cap["in_use"]["memory_mb"] == 3 * 8192
    assert cap["available"]["cores"] == 4
    assert cap["concurrency"]["slots_free"] == 2
    assert "unknown" in cap


def test_missing_authority_is_explicit_unknown_not_a_zero() -> None:
    class _NoStore:
        pass

    cap = build_capacity_projection(_NoStore())
    assert "in_use" in cap["unknown"]
    assert "limits" in cap["unknown"]
    assert cap["available"]["cores"] is None


def test_fallback_to_limits_when_no_pool_authority() -> None:
    class _NoController:
        limits = _Admission.limits

    cap = build_capacity_projection(_Manager([], []), admission=_NoController())
    assert cap["pool"]["source"] == "admission_limits_fallback"
    assert cap["pool"]["cores"] == 32
    assert "pool" in cap["unknown"]
