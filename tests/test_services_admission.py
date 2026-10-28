"""B09-2 (todo 49) — resource admission core (MU-05).

Exercises ``acp_services.runtime.admission``:

1. **Effective resources are an intersection, never a sum.**  User ∧ project ∧
   key ∧ node/queue bounds are combined per dimension; two API keys of one user
   draw from the same user/project aggregate and therefore never stack.
2. **Atomic pre-dispatch reserve + re-validate.**  Concurrent submissions race a
   single ``BEGIN IMMEDIATE`` check-and-insert: exactly one wins on a contended
   dimension, and a refusal leaves no partial reservation.
3. **PAUSED occupancy is real.**  A paused reservation keeps its actual memory
   and disk (CPU released) — never counted as zero.
4. **Executable bounds or an explicit refusal.**  Quota-only requires a policy
   version, an enforced hard-limit capability and a finite per-job bound; a
   missing bound is refused and only an explicit ``usage-only`` mode bypasses it.
5. **Structured refusal reasons** (typed code + machine-readable ``exceeded``).
6. **Policy-version snapshot** recorded at reserve time and re-checked at dispatch.
7. **No bypass** — the controller never writes SQL and dispatch requires a
   reservation.
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path

import pytest

from acp_services.contracts import access
from acp_services.runtime import admission

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ADMISSION_SRC = _REPO_ROOT / "src" / "acp_services" / "runtime" / "admission.py"

Q = admission.AdmissionMode.QUOTA_ONLY
U = admission.AdmissionMode.USAGE_ONLY
R = admission.AdmissionReason
S = admission.ReservationState


# --------------------------------------------------------------------------- #
# Fakes (injected ports)
# --------------------------------------------------------------------------- #


def _grant(**overrides) -> access.ResourceGrantSpec:
    base: dict = {
        "grant_id": "g",
        "principal_id": "alice",
        "scope_id": "s",
        "resource_id": "proj",
        "actions": ("read",),
        "granted_at": "2026-10-19T00:00:00Z",
    }
    base.update(overrides)
    return access.ResourceGrantSpec(**base)


class FakeGrants:
    """Static :class:`GrantProvider` — ``policy_version`` is mutable for the
    policy-change test."""

    def __init__(
        self,
        *,
        user: access.ResourceGrantSpec | None = None,
        project: access.ResourceGrantSpec | None = None,
        keys: dict[str, access.ResourceGrantSpec] | None = None,
        policy_version: str = "pv1",
        capability: str = admission.HARD_LIMIT_ENFORCED,
        allowed_queues: tuple[str, ...] | None = None,
        allowed_nodes: tuple[str, ...] | None = None,
        queue_capacity: dict[str, admission.Capacity] | None = None,
        node_capacity: dict[str, admission.Capacity] | None = None,
    ) -> None:
        self.user = user
        self.project = project
        self.keys = dict(keys or {})
        self.policy_version = policy_version
        self.capability = capability
        self.allowed_queues = allowed_queues
        self.allowed_nodes = allowed_nodes
        self.queue_capacity = dict(queue_capacity or {})
        self.node_capacity = dict(node_capacity or {})
        self.resolve_calls = 0

    def resolve(self, request: admission.AdmissionRequest) -> admission.GrantResolution:
        self.resolve_calls += 1
        return admission.GrantResolution(
            policy_version=self.policy_version,
            user_grant=self.user,
            project_grant=self.project,
            key_grant=self.keys.get(request.key_id) if request.key_id else None,
            hard_limit_capability=self.capability,
            allowed_queues=self.allowed_queues,
            allowed_nodes=self.allowed_nodes,
            queue_capacity=self.queue_capacity,
            node_capacity=self.node_capacity,
        )


class FakeCapacity:
    """Static :class:`CapacityProvider` — ``cap`` is mutable."""

    def __init__(self, cap: admission.Capacity) -> None:
        self.cap = cap

    def capacity(self) -> admission.Capacity:
        return self.cap


def _req(**overrides) -> admission.AdmissionRequest:
    base: dict = {
        "principal_id": "alice",
        "project_id": "proj",
        "job_id": "job-1",
        "cores": 1,
        "duration_s": 60,
    }
    base.update(overrides)
    return admission.AdmissionRequest(**base)


def _controller(
    tmp_path: Path,
    *,
    user: access.ResourceGrantSpec | None = None,
    project: access.ResourceGrantSpec | None = None,
    keys: dict[str, access.ResourceGrantSpec] | None = None,
    capacity: admission.Capacity | None = None,
    **grant_kw,
):
    ledger = admission.ReservationLedger(str(tmp_path / "admission.db"))
    grants = FakeGrants(user=user, project=project, keys=keys, **grant_kw)
    cap = FakeCapacity(
        capacity
        or admission.Capacity(
            cores=64, memory_mb=262144, concurrency=32, storage_bytes=1 << 40, source="test"
        )
    )
    controller = admission.AdmissionController(grants=grants, capacity=cap, ledger=ledger)
    return controller, ledger, grants, cap


_FULL = _grant(cores=8, memory_mb=8192, concurrency=8, duration_s=3600, storage_bytes=1_000_000)


# --------------------------------------------------------------------------- #
# 1. Intersection, never a sum
# --------------------------------------------------------------------------- #


def test_effective_bounds_intersect_not_sum(tmp_path: Path) -> None:
    controller, _ledger, _grants, _cap = _controller(
        tmp_path,
        user=_grant(
            cores=8,
            memory_mb=4096,
            concurrency=4,
            duration_s=3600,
            storage_bytes=1000,
            queue="batch",
        ),
        project=_grant(
            cores=6,
            memory_mb=8192,
            concurrency=8,
            duration_s=1800,
            storage_bytes=5000,
            queue="batch",
        ),
        keys={
            "k1": _grant(
                cores=4,
                memory_mb=2048,
                concurrency=2,
                duration_s=900,
                storage_bytes=2000,
                queue="batch",
            )
        },
        allowed_queues=("batch", "test"),
    )
    bounds = controller.effective_bounds(_req(key_id="k1", queue="batch"))
    assert bounds.cores == 4  # min(8, 6, 4), never 8+6+4
    assert bounds.memory_mb == 2048
    assert bounds.concurrency == 2
    assert bounds.duration_s == 900
    assert bounds.storage_bytes == 1000
    assert bounds.queues == ("batch",)  # allowed ∧ user ∧ project ∧ key


def test_multiple_keys_do_not_stack(tmp_path: Path) -> None:
    controller, ledger, _grants, _cap = _controller(
        tmp_path,
        user=_grant(cores=4, duration_s=3600, storage_bytes=1000),
        keys={
            "k1": _grant(cores=100, duration_s=3600, storage_bytes=1000),
            "k2": _grant(cores=100, duration_s=3600, storage_bytes=1000),
        },
        capacity=admission.Capacity(
            cores=64, memory_mb=1 << 20, concurrency=32, storage_bytes=1 << 30
        ),
    )
    first = controller.admit(_req(key_id="k1", cores=3))
    second = controller.admit(_req(job_id="job-2", key_id="k2", cores=3))

    assert first.admitted
    # Two keys each with a 100-core sub-budget must not stack to 200: both draw
    # from the same user ceiling of 4 cores.
    assert not second.admitted
    assert second.reason_code == R.EXCEEDS_CORES
    assert second.exceeded[0]["scope"] == "user"
    assert second.exceeded[0]["limit"] == 4
    assert ledger.count(active_only=True) == 1


# --------------------------------------------------------------------------- #
# 2. Atomic reservation + race
# --------------------------------------------------------------------------- #


def test_concurrent_reservation_race_never_over_allocates(tmp_path: Path) -> None:
    controller, ledger, _grants, _cap = _controller(
        tmp_path,
        user=_grant(cores=4, memory_mb=8192, duration_s=3600, storage_bytes=1_000_000),
        capacity=admission.Capacity(
            cores=4, memory_mb=8192, concurrency=8, storage_bytes=1_000_000
        ),
    )
    barrier = threading.Barrier(2)
    results: list[admission.AdmissionDecision] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        barrier.wait()
        decision = controller.admit(_req(job_id=f"job-{index}", cores=3))
        with lock:
            results.append(decision)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    admitted = [d for d in results if d.admitted]
    refused = [d for d in results if not d.admitted]
    assert len(results) == 2
    assert len(admitted) == 1, "the contended ceiling must admit exactly one"
    assert len(refused) == 1
    assert refused[0].reason_code in {R.EXCEEDS_CORES, R.CAPACITY_CORES}
    # Only the winner inserted a row; the loser's check-and-insert rolled back.
    assert ledger.count(active_only=True) == 1
    assert controller.occupancy()["cores"] == 3


def test_refusal_leaves_no_partial_reservation(tmp_path: Path) -> None:
    controller, ledger, _grants, _cap = _controller(
        tmp_path,
        user=_grant(cores=None),  # no cores ceiling
    )
    refused = controller.admit(_req(cores=4), mode=Q)
    assert not refused.admitted
    assert refused.reason_code == R.UNBOUNDED_REQUEST
    assert ledger.count() == 0, "a refused reservation must not be persisted"

    ok = controller.admit(_req(cores=1), mode=U)
    assert ok.admitted
    assert ledger.count() == 1


# --------------------------------------------------------------------------- #
# 3. PAUSED occupancy
# --------------------------------------------------------------------------- #


def test_paused_counts_actual_memory_and_disk_never_zero(tmp_path: Path) -> None:
    controller, _ledger, _grants, _cap = _controller(
        tmp_path,
        user=_grant(cores=8, memory_mb=16384, duration_s=3600, storage_bytes=2_000_000),
        capacity=admission.Capacity(
            cores=8, memory_mb=8192, concurrency=8, storage_bytes=1_000_000
        ),
    )
    decision = controller.admit(_req(cores=2, memory_mb=4096, storage_bytes=500_000))
    assert decision.admitted
    reservation_id = decision.reservation_id
    assert reservation_id is not None

    paused = controller.mark_paused(reservation_id, memory_mb=4096, storage_bytes=500_000)
    assert paused is not None and paused.state == S.PAUSED
    assert paused.counted_cores() == 0
    assert paused.counted_memory_mb() == 4096
    assert paused.counted_storage_bytes() == 500_000

    occ = controller.occupancy()
    assert occ["cores"] == 0, "PAUSED releases CPU"
    assert occ["memory_mb"] == 4096, "PAUSED keeps actual memory"
    assert occ["storage_bytes"] == 500_000, "PAUSED keeps actual disk"

    # A PAUSED job is not zero resource: the retained memory blocks a new job
    # whose demand would exceed the pool's actual capacity.
    second = controller.admit(_req(job_id="job-2", cores=1, memory_mb=8192, storage_bytes=1000))
    assert not second.admitted
    assert second.reason_code == R.CAPACITY_MEMORY


# --------------------------------------------------------------------------- #
# 4. Queued reservation: storage yes, CPU no
# --------------------------------------------------------------------------- #


def test_queued_holds_storage_not_cpu_then_dispatch_revalidates(tmp_path: Path) -> None:
    controller, _ledger, _grants, _cap = _controller(
        tmp_path,
        user=_grant(cores=8, memory_mb=8192, duration_s=3600, storage_bytes=1_000_000),
        capacity=admission.Capacity(
            cores=4, memory_mb=8192, concurrency=8, storage_bytes=1_000_000
        ),
    )
    first = controller.admit(_req(cores=4, memory_mb=2048, storage_bytes=100_000, queued=True))
    second = controller.admit(
        _req(job_id="job-2", cores=4, memory_mb=2048, storage_bytes=100_000, queued=True)
    )
    assert first.admitted and second.admitted

    occ = controller.occupancy()
    assert occ["cores"] == 0, "queued jobs hold no running CPU"
    assert occ["memory_mb"] == 0
    assert occ["storage_bytes"] == 200_000, "queued jobs may reserve storage"
    assert occ["concurrency"] == 2

    assert first.reservation_id is not None and second.reservation_id is not None
    promoted = controller.revalidate(first.reservation_id)
    assert promoted.admitted and promoted.enforced
    assert controller.reservation(first.reservation_id).state == S.DISPATCHED
    assert controller.occupancy()["cores"] == 4

    # The pool only had 4 cores: the second queued job cannot be dispatched.
    blocked = controller.revalidate(second.reservation_id)
    assert not blocked.admitted
    assert blocked.reason_code == R.CAPACITY_CORES
    assert controller.reservation(second.reservation_id).state == S.QUEUED


# --------------------------------------------------------------------------- #
# 5. Structured refusal reasons
# --------------------------------------------------------------------------- #


def test_structured_refusal_reasons(tmp_path: Path) -> None:
    # no policy version
    controller, ledger, grants, _cap = _controller(tmp_path, user=_FULL, policy_version="")
    d = controller.admit(_req())
    assert not d.admitted and d.reason_code == R.NO_POLICY_VERSION
    assert d.to_dict()["reason_code"] == R.NO_POLICY_VERSION

    # hard-limit capability unavailable -> quota-only refuses
    grants.policy_version = "pv1"
    grants.capability = admission.HARD_LIMIT_UNAVAILABLE
    d = controller.admit(_req())
    assert not d.admitted and d.reason_code == R.HARD_LIMIT_UNAVAILABLE

    # usage-only is the explicit escape hatch
    d = controller.admit(_req(), mode=U)
    assert d.admitted and d.reason_code == R.USAGE_ONLY and d.enforced is False
    assert d.reservation_id is not None
    ledger.release(d.reservation_id)

    # unbounded request
    grants.capability = admission.HARD_LIMIT_ENFORCED
    c2, _l2, _g2, _cap2 = _controller(
        tmp_path / "c2", user=_grant(duration_s=3600, storage_bytes=1000)
    )
    d = c2.admit(_req(cores=4))
    assert not d.admitted and d.reason_code == R.UNBOUNDED_REQUEST

    # queue / node not allowed
    c3, _l3, _g3, _cap3 = _controller(
        tmp_path / "c3",
        user=_FULL,
        allowed_queues=("batch",),
        allowed_nodes=("n1",),
    )
    d = c3.admit(_req(queue="test"))
    assert not d.admitted and d.reason_code == R.QUEUE_NOT_ALLOWED
    d = c3.admit(_req(node="n2"))
    assert not d.admitted and d.reason_code == R.NODE_NOT_ALLOWED

    # per-job duration ceiling
    c4, _l4, _g4, _cap4 = _controller(
        tmp_path / "c4", user=_grant(cores=8, duration_s=100, storage_bytes=1000)
    )
    d = c4.admit(_req(cores=1, duration_s=200))
    assert not d.admitted and d.reason_code == R.EXCEEDS_DURATION
    assert d.exceeded[0]["scope"] == "request"

    # actual capacity: concurrency
    c5, _l5, _g5, _cap5 = _controller(
        tmp_path / "c5",
        user=_grant(cores=8, duration_s=3600, storage_bytes=1000),
        capacity=admission.Capacity(
            cores=8, memory_mb=8192, concurrency=1, storage_bytes=1_000_000
        ),
    )
    assert c5.admit(_req(cores=1)).admitted
    d = c5.admit(_req(job_id="job-2", cores=1))
    assert not d.admitted and d.reason_code == R.CAPACITY_CONCURRENCY
    assert d.exceeded[0]["scope"] == "capacity"


# --------------------------------------------------------------------------- #
# 6. Policy-version snapshot + dispatch re-validation
# --------------------------------------------------------------------------- #


def test_policy_version_snapshot_and_reenvalidate(tmp_path: Path) -> None:
    controller, _ledger, grants, cap = _controller(tmp_path, user=_FULL, policy_version="pv1")
    first = controller.admit(_req())
    assert first.policy_version == "pv1"
    assert first.reservation_id is not None
    snapshot = controller.reservation(first.reservation_id)
    assert snapshot is not None and snapshot.policy_version == "pv1"
    assert snapshot.state == S.RESERVED

    # unchanged policy -> dispatch succeeds and is recorded
    promoted = controller.revalidate(first.reservation_id)
    assert promoted.admitted and promoted.enforced
    assert controller.reservation(first.reservation_id).state == S.DISPATCHED

    # policy bump between reserve and dispatch -> refused, never silently ignored
    second = controller.admit(_req(job_id="job-2"))
    assert second.reservation_id is not None
    grants.policy_version = "pv2"
    stale = controller.revalidate(second.reservation_id)
    assert not stale.admitted
    assert stale.reason_code == R.POLICY_VERSION_CHANGED
    assert stale.policy_version == "pv2"
    assert controller.reservation(second.reservation_id).state == S.RESERVED


# --------------------------------------------------------------------------- #
# 7. Quota-only refusal when bounds unavailable; usage-only explicit
# --------------------------------------------------------------------------- #


def test_quota_only_refused_when_bounds_unavailable_only_usage_only_allowed(
    tmp_path: Path,
) -> None:
    controller, ledger, _grants, _cap = _controller(
        tmp_path, user=_grant(cores=None), capacity=admission.Capacity(cores=64)
    )
    quota = controller.admit(_req(cores=4), mode=Q)
    assert not quota.admitted
    assert quota.reason_code == R.UNBOUNDED_REQUEST
    assert quota.enforced is False
    assert ledger.count() == 0

    usage = controller.admit(_req(cores=4), mode=U)
    assert usage.admitted
    assert usage.reason_code == R.USAGE_ONLY
    assert usage.enforced is False
    assert ledger.count() == 1


# --------------------------------------------------------------------------- #
# 8. Ports, release, and no-bypass architecture guards
# --------------------------------------------------------------------------- #


def test_ports_satisfied_by_fakes(tmp_path: Path) -> None:
    ledger = admission.ReservationLedger(str(tmp_path / "a.db"))
    grants = FakeGrants(user=_FULL)
    cap = FakeCapacity(admission.Capacity(cores=8, concurrency=4))
    assert isinstance(grants, admission.GrantProvider)
    assert isinstance(cap, admission.CapacityProvider)
    controller = admission.AdmissionController(grants=grants, capacity=cap, ledger=ledger)
    decision = controller.admit(_req(cores=1))
    assert decision.admitted and decision.reservation_id is not None
    assert controller.release(decision.reservation_id) is True
    assert controller.occupancy()["reservations"] == 0
    assert controller.reservation(decision.reservation_id).state == S.RELEASED


def test_dispatch_requires_a_reservation(tmp_path: Path) -> None:
    controller, _ledger, _grants, _cap = _controller(tmp_path, user=_FULL)
    missing = controller.revalidate("res_does_not_exist")
    assert not missing.admitted
    assert missing.reason_code == R.RESERVATION_NOT_FOUND


def _import_roots(source: str) -> set[str]:
    tree = ast.parse(source)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_admission_module_is_isolated_from_acp_and_networks() -> None:
    source = _ADMISSION_SRC.read_text(encoding="utf-8")
    roots = _import_roots(source)
    assert "acp" not in roots, "acp_services must not import acp"
    for forbidden in ("subprocess", "socket", "paramiko"):
        assert forbidden not in roots, f"{forbidden} is not allowed in admission"


def test_controller_never_writes_sql_directly() -> None:
    """The atomic ledger is the only SQL writer — a bypass is a design failure."""
    tree = ast.parse(_ADMISSION_SRC.read_text(encoding="utf-8"))
    controller_cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AdmissionController"
    )
    sql_tokens = ("INSERT", "UPDATE", "SELECT", "DELETE")
    for node in ast.walk(controller_cls):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not any(token in node.value for token in sql_tokens), (
                f"AdmissionController contains raw SQL: {node.value!r}"
            )


def test_quota_only_and_usage_only_are_the_only_modes(tmp_path: Path) -> None:
    assert set(admission.AdmissionMode.ALL) == {"quota-only", "usage-only"}
    controller, _ledger, _grants, _cap = _controller(tmp_path, user=_FULL)
    with pytest.raises(admission.AdmissionValidationError):
        controller.admit(_req(), mode="unlimited")


def test_capacity_minimum_folds_queue_and_node_limits(tmp_path: Path) -> None:
    controller, _ledger, _grants, _cap = _controller(
        tmp_path,
        user=_grant(cores=64, duration_s=3600, storage_bytes=1 << 40),
        capacity=admission.Capacity(
            cores=32, memory_mb=1 << 20, concurrency=16, storage_bytes=1 << 40
        ),
        queue_capacity={
            "batch": admission.Capacity(
                cores=4, memory_mb=1 << 20, concurrency=2, storage_bytes=1 << 40
            )
        },
        node_capacity={
            "n1": admission.Capacity(
                cores=2, memory_mb=1 << 20, concurrency=1, storage_bytes=1 << 40
            )
        },
        allowed_queues=("batch",),
        allowed_nodes=("n1",),
    )
    # node capacity (2) is tighter than queue (4) and global (32)
    d = controller.admit(_req(cores=3, queue="batch", node="n1"))
    assert not d.admitted
    assert d.reason_code == R.CAPACITY_CORES
    assert d.exceeded[0]["limit"] == 2
