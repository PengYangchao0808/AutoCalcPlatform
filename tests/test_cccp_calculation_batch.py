"""Tests for the generic homogeneous-task batch executor (plan todo 12).

Execution functions are test doubles: no binaries, no subprocesses, no
backend capability calls — the batch layer only orchestrates concurrency,
cache identity/validation, precheck gating and progress.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from cccp.calculation.batch import (
    CACHE_SCHEMA_VERSION,
    BatchEntry,
    BatchResources,
    CacheRecord,
    CacheVersionPolicy,
    EffectiveTaskParams,
    FileSystemCacheStore,
    ItemResources,
    ItemRunOutcome,
    MemoryCacheStore,
    cache_identity,
    resolve_effective_params,
    run_batch,
    version_matches,
)
from cccp.calculation.errors import BackendUnavailableError, TaskInputError

RUN_CONFIG = {"basis": "def2-SVP"}


def _request(entry: str = "e0", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "task": "singlepoint",
        "backend": "orca",
        "method": "wB97X-D4",
        "charge": 0,
        "multiplicity": 1,
        "inputs": {"primary": f"geometry-{entry}"},
    }
    payload.update(overrides)
    return payload


@dataclass
class _RecordingExecutor:
    """Fake single-task executor recording calls and producing artifacts."""

    calls: list[tuple[str, EffectiveTaskParams, int]] = field(default_factory=list)
    fail_ids: set[str] = field(default_factory=set)
    artifact_dir: Path | None = None
    software_version: str | None = "6.0.1"
    payload: dict[str, object] = field(default_factory=lambda: {"energy": -1.0})

    def __call__(
        self,
        request: dict[str, object],
        params: EffectiveTaskParams,
        resources: ItemResources,
    ) -> ItemRunOutcome:
        entry_id = str(request.get("inputs", {}).get("primary"))
        self.calls.append((entry_id, params, resources.cores))
        if params.task == "boom" or entry_id in self.fail_ids:
            raise RuntimeError(f"executive failure for {entry_id}")
        artifacts: tuple[tuple[str, str], ...] = ()
        if self.artifact_dir is not None:
            out = self.artifact_dir / f"{entry_id}.out"
            out.write_text(f"result for {entry_id}", encoding="utf-8")
            artifacts = (("output", str(out)),)
        return ItemRunOutcome(
            success=True,
            payload=dict(self.payload),
            software_version=self.software_version,
            artifact_paths=artifacts,
        )


def _entries(*ids: str, **overrides: object) -> list[BatchEntry]:
    return [BatchEntry(entry_id, _request(entry_id, **overrides)) for entry_id in ids]


def test_concurrency_cap_and_input_order_preserved() -> None:
    """In-flight items never exceed resources.concurrency; results keep order."""
    in_flight = 0
    max_in_flight = 0
    lock = threading.Lock()
    done_ids: list[str] = []

    def executor(request, params, resources):
        nonlocal in_flight, max_in_flight
        with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        time.sleep(0.03)
        with lock:
            in_flight -= 1
            done_ids.append(str(request["inputs"]["primary"]))
        assert resources.cores == 2
        return ItemRunOutcome(success=True, payload={}, software_version="1.0")

    entries = _entries("a", "b", "c", "d")
    result = run_batch(
        entries,
        executor,
        cache=MemoryCacheStore(),
        resources=BatchResources(concurrency=2, per_item_cores=2, total_core_budget=8),
        run_config=RUN_CONFIG,
    )
    assert max_in_flight == 2
    assert [r.entry_id for r in result.results] == ["a", "b", "c", "d"]
    assert result.n_success == 4 and result.n_failed == 0


def test_resource_separation_enforces_total_usage_cap() -> None:
    """concurrency x per_item_cores must fit the budget; the executor sees
    per_item_cores, never the batch concurrency."""
    with pytest.raises(TaskInputError):
        BatchResources(concurrency=4, per_item_cores=3, total_core_budget=8)
    with pytest.raises(TaskInputError):
        BatchResources(concurrency=0, per_item_cores=1, total_core_budget=8)

    seen_cores: list[int] = []

    def executor(request, params, resources):
        seen_cores.append(resources.cores)
        return ItemRunOutcome(success=True, payload={}, software_version="1.0")

    resources = BatchResources(concurrency=4, per_item_cores=2, total_core_budget=8)
    result = run_batch(
        _entries("a", "b", "c", "d"),
        executor,
        cache=MemoryCacheStore(),
        resources=resources,
        run_config=RUN_CONFIG,
    )
    assert result.n_success == 4
    assert seen_cores == [2, 2, 2, 2]
    assert all(cores != resources.concurrency for cores in seen_cores)


def test_complete_version_matched_cache_hit_skips_precheck_and_execution(
    tmp_path: Path,
) -> None:
    """A complete, version-matched cache hit succeeds with no binaries: the
    precheck and the single-task execution function are both untouched."""
    executor = _RecordingExecutor(artifact_dir=tmp_path)
    cache = MemoryCacheStore()
    entries = _entries("a")
    first = run_batch(
        entries,
        executor,
        cache=cache,
        resources=BatchResources(1, 1, 1),
        run_config=RUN_CONFIG,
    )
    assert first.n_success == 1 and first.n_cache_hits == 0
    assert len(executor.calls) == 1

    def forbidden_precheck(request, params):
        raise AssertionError("precheck must not run on a cache hit")

    def forbidden_executor(request, params, resources):
        raise AssertionError("execution must not run on a cache hit")

    second = run_batch(
        entries,
        forbidden_executor,
        cache=cache,
        resources=BatchResources(1, 1, 1),
        run_config=RUN_CONFIG,
        precheck=forbidden_precheck,
    )
    assert second.n_success == 1 and second.n_cache_hits == 1
    assert second.results[0].from_cache is True
    assert second.results[0].payload == {"energy": -1.0}

    # The precheck models "binary unavailable": a hit must not consult it.
    def missing_binary_precheck(request, params):
        raise BackendUnavailableError("orca binary missing")

    third = run_batch(
        entries,
        forbidden_executor,
        cache=cache,
        resources=BatchResources(1, 1, 1),
        run_config=RUN_CONFIG,
        precheck=missing_binary_precheck,
    )
    assert third.n_success == 1 and third.n_cache_hits == 1


def test_cache_miss_runs_precheck_before_execution() -> None:
    order: list[str] = []

    def precheck(request, params):
        order.append("precheck")

    def executor(request, params, resources):
        order.append("execute")
        return ItemRunOutcome(success=True, payload={}, software_version="1.0")

    result = run_batch(
        _entries("a"),
        executor,
        cache=MemoryCacheStore(),
        resources=BatchResources(1, 1, 1),
        run_config=RUN_CONFIG,
        precheck=precheck,
    )
    assert result.n_success == 1
    assert order == ["precheck", "execute"]

    def failing_precheck(request, params):
        raise BackendUnavailableError("no binary")

    def never_called(request, params, resources):
        raise AssertionError("executor must not run when the precheck fails")

    failed = run_batch(
        _entries("a"),
        never_called,
        cache=MemoryCacheStore(),
        resources=BatchResources(1, 1, 1),
        run_config=RUN_CONFIG,
        precheck=failing_precheck,
    )
    assert failed.results[0].status == "failed"
    assert "no binary" in (failed.results[0].error_message or "")


@pytest.mark.parametrize(
    ("constraint", "actual", "policy", "expected"),
    (
        ("6.0.1", "6.0.1", CacheVersionPolicy.REQUIRE_RECORDED, True),
        ("6.0.1", "6.1.0", CacheVersionPolicy.REQUIRE_RECORDED, False),
        ("6.0.*", "6.0.1", CacheVersionPolicy.REQUIRE_RECORDED, True),
        ("6.0.1", None, CacheVersionPolicy.REQUIRE_RECORDED, False),
        (None, "6.0.1", CacheVersionPolicy.REQUIRE_RECORDED, True),
        (None, None, CacheVersionPolicy.REQUIRE_RECORDED, False),
        (None, None, CacheVersionPolicy.ANY, True),
    ),
)
def test_version_matching_rules(constraint, actual, policy, expected) -> None:
    assert version_matches(constraint, actual, policy) is expected


def test_version_constraint_mismatch_is_a_miss() -> None:
    executor = _RecordingExecutor(software_version="6.0.1")
    cache = MemoryCacheStore()
    entries = _entries("a", version_constraint="6.1.0")
    first = run_batch(
        entries,
        executor,
        cache=cache,
        resources=BatchResources(1, 1, 1),
        run_config=RUN_CONFIG,
    )
    assert first.n_success == 1
    second = run_batch(
        entries,
        executor,
        cache=cache,
        resources=BatchResources(1, 1, 1),
        run_config=RUN_CONFIG,
    )
    assert second.n_cache_hits == 0
    assert second.results[0].miss_reason == "version_incompatible"
    assert len(executor.calls) == 2

    matched = _entries("a", version_constraint="6.0.1")
    third = run_batch(
        matched,
        executor,
        cache=cache,
        resources=BatchResources(1, 1, 1),
        run_config=RUN_CONFIG,
    )
    # identity includes the constraint, so this is a fresh key -> miss
    assert third.n_cache_hits == 0
    fourth = run_batch(
        matched,
        executor,
        cache=cache,
        resources=BatchResources(1, 1, 1),
        run_config=RUN_CONFIG,
    )
    assert fourth.n_cache_hits == 1


def test_missing_recorded_version_cannot_prove_compatibility() -> None:
    executor = _RecordingExecutor(software_version=None)
    cache = MemoryCacheStore()
    entries = _entries("a")
    run_batch(entries, executor, cache=cache, resources=BatchResources(1, 1, 1), run_config=RUN_CONFIG)
    second = run_batch(
        entries,
        executor,
        cache=cache,
        resources=BatchResources(1, 1, 1),
        run_config=RUN_CONFIG,
    )
    assert second.n_cache_hits == 0
    assert second.results[0].miss_reason == "version_incompatible"
    assert len(executor.calls) == 2


def test_unprovable_legacy_cache_is_explicit_miss(tmp_path: Path) -> None:
    """Old cache payloads without provable completeness never hit."""
    executor = _RecordingExecutor()
    store = FileSystemCacheStore(tmp_path)
    entries = _entries("a")
    run_batch(entries, executor, cache=store, resources=BatchResources(1, 1, 1), run_config=RUN_CONFIG)
    assert len(executor.calls) == 1

    identity = cache_identity(resolve_effective_params(_request("a"), run_config=RUN_CONFIG))
    record_path = tmp_path / f"{identity.digest}.json"

    legacy_payload = json.loads(record_path.read_text(encoding="utf-8"))
    del legacy_payload["complete"]
    record_path.write_text(json.dumps(legacy_payload), encoding="utf-8")
    second = run_batch(
        entries, executor, cache=store, resources=BatchResources(1, 1, 1), run_config=RUN_CONFIG
    )
    assert second.n_cache_hits == 0 and len(executor.calls) == 2

    record_path.write_text('{"schema_version": 0, "complete": true}', encoding="utf-8")
    third = run_batch(
        entries, executor, cache=store, resources=BatchResources(1, 1, 1), run_config=RUN_CONFIG
    )
    assert third.n_cache_hits == 0 and len(executor.calls) == 3

    store.write(
        CacheRecord(
            identity_digest=identity.digest,
            schema_version=CACHE_SCHEMA_VERSION,
            complete=False,
            software_version="6.0.1",
            artifacts=(),
            result_payload={},
        )
    )
    fourth = run_batch(
        entries, executor, cache=store, resources=BatchResources(1, 1, 1), run_config=RUN_CONFIG
    )
    assert fourth.n_cache_hits == 0 and fourth.results[0].miss_reason == "incomplete_record"


def test_same_request_different_effective_config_different_cache_identity() -> None:
    """Same raw request + different effective configuration -> different
    identity, because identity is built from resolved effective parameters."""
    request = _request("a")
    params_a = resolve_effective_params(request, run_config={"basis": "def2-SVP"})
    params_b = resolve_effective_params(request, run_config={"basis": "def2-TZVP"})
    identity_a = cache_identity(params_a)
    identity_b = cache_identity(params_b)
    assert identity_a.digest != identity_b.digest
    assert identity_a.payload["effective_params"] != identity_b.payload["effective_params"]

    executor = _RecordingExecutor()
    cache = MemoryCacheStore()
    entries = _entries("a")
    run_batch(entries, executor, cache=cache, resources=BatchResources(1, 1, 1), run_config={"basis": "def2-SVP"})
    run_batch(entries, executor, cache=cache, resources=BatchResources(1, 1, 1), run_config={"basis": "def2-SVP"})
    assert len(executor.calls) == 1  # second run hit the cache
    run_batch(entries, executor, cache=cache, resources=BatchResources(1, 1, 1), run_config={"basis": "def2-TZVP"})
    assert len(executor.calls) == 2  # different effective config -> miss


def test_partial_failure_does_not_abort_siblings() -> None:
    executor = _RecordingExecutor(fail_ids={"geometry-b"})
    result = run_batch(
        _entries("a", "b", "c"),
        executor,
        cache=MemoryCacheStore(),
        resources=BatchResources(2, 1, 2),
        run_config=RUN_CONFIG,
    )
    assert result.n_total == 3
    assert result.n_success == 2
    assert result.n_failed == 1
    statuses = {r.entry_id: r.status for r in result.results}
    assert statuses == {"a": "success", "b": "failed", "c": "success"}
    failure = next(r for r in result.results if r.entry_id == "b")
    assert "executive failure" in (failure.error_message or "")


def test_progress_callback_reaches_total_including_validation_failures() -> None:
    events: list[tuple[int, int]] = []
    executor = _RecordingExecutor(fail_ids={"geometry-b"})
    entries = _entries("a", "b", "c")
    entries.append(BatchEntry("bad", {"task": "singlepoint"}))  # missing backend
    result = run_batch(
        entries,
        executor,
        cache=MemoryCacheStore(),
        resources=BatchResources(2, 1, 2),
        run_config=RUN_CONFIG,
        progress_callback=lambda done, total: events.append((done, total)),
    )
    assert result.n_total == 4
    assert events[-1] == (4, 4)
    assert [done for done, _total in events] == sorted(done for done, _total in events)
    assert all(total == 4 for _done, total in events)


def test_invalid_entry_is_a_recorded_failure_not_a_batch_abort() -> None:
    result = run_batch(
        [BatchEntry("bad", {"backend": "orca"}), *_entries("a")],
        _RecordingExecutor(),
        cache=MemoryCacheStore(),
        resources=BatchResources(2, 1, 2),
        run_config=RUN_CONFIG,
    )
    by_id = {r.entry_id: r for r in result.results}
    assert by_id["bad"].status == "failed"
    assert "task" in (by_id["bad"].error_message or "")
    assert by_id["a"].status == "success"


def test_corrupted_artifact_forces_miss(tmp_path: Path) -> None:
    executor = _RecordingExecutor(artifact_dir=tmp_path)
    cache = MemoryCacheStore()
    entries = _entries("a")
    run_batch(entries, executor, cache=cache, resources=BatchResources(1, 1, 1), run_config=RUN_CONFIG)
    artifact = tmp_path / "geometry-a.out"
    artifact.write_text("tampered", encoding="utf-8")
    second = run_batch(
        entries, executor, cache=cache, resources=BatchResources(1, 1, 1), run_config=RUN_CONFIG
    )
    assert second.n_cache_hits == 0
    assert second.results[0].miss_reason == "artifact_hash_mismatch"
    assert len(executor.calls) == 2

    artifact.unlink()
    third = run_batch(
        entries, executor, cache=cache, resources=BatchResources(1, 1, 1), run_config=RUN_CONFIG
    )
    assert third.results[0].miss_reason == "artifact_missing"


def test_homogeneous_batch_guard_rejects_mixed_scientific_parameters() -> None:
    entries = [BatchEntry("a", _request("a")), BatchEntry("b", _request("b", task="optimize"))]
    with pytest.raises(TaskInputError):
        run_batch(
            entries,
            _RecordingExecutor(),
            cache=MemoryCacheStore(),
            resources=BatchResources(2, 1, 2),
            run_config=RUN_CONFIG,
        )


def test_duplicate_entry_ids_rejected() -> None:
    with pytest.raises(TaskInputError):
        run_batch(
            _entries("a", "a"),
            _RecordingExecutor(),
            cache=MemoryCacheStore(),
            resources=BatchResources(2, 1, 2),
            run_config=RUN_CONFIG,
        )


def test_cache_identity_covers_all_mandated_components() -> None:
    base = resolve_effective_params(_request("a"), run_config=RUN_CONFIG)
    payload = cache_identity(base).payload
    for key in (
        "cache_schema",
        "task",
        "backend",
        "effective_params",
        "charge",
        "multiplicity",
        "electronic_state",
        "inputs",
        "version_constraint",
    ):
        assert key in payload
    assert payload["cache_schema"] == CACHE_SCHEMA_VERSION

    variants = [
        _request("a", task="optimize"),
        _request("a", backend="xtb"),
        _request("a", charge=1),
        _request("a", multiplicity=2),
        _request("a", electronic_state={"state_id": "triplet", "target_multiplicity": 3}),
        _request("a", inputs={"primary": "different-geometry"}),
        _request("a", inputs={"primary": "geometry-a", "hessian": "hess-content"}),
        _request("a", inputs={"primary": "geometry-a", "orbitals": "gbw-content"}),
        _request("a", version_constraint="6.0.1"),
    ]
    base_digest = cache_identity(base).digest
    for variant in variants:
        other = cache_identity(resolve_effective_params(variant, run_config=RUN_CONFIG))
        assert other.digest != base_digest, variant

    aux_a = resolve_effective_params(
        _request("a", inputs={"primary": "g", "hessian": "old"}), run_config=RUN_CONFIG
    )
    aux_b = resolve_effective_params(
        _request("a", inputs={"primary": "g", "hessian": "new"}), run_config=RUN_CONFIG
    )
    assert cache_identity(aux_a).digest != cache_identity(aux_b).digest


def test_executor_receives_the_same_resolved_params_used_for_identity() -> None:
    """Single resolution point: the executor gets the exact params object the
    cache identity was computed from — no second parsing pass."""
    seen: list[EffectiveTaskParams] = []

    def executor(request, params, resources):
        seen.append(params)
        return ItemRunOutcome(success=True, payload={}, software_version="1.0")

    run_batch(
        _entries("a"),
        executor,
        cache=MemoryCacheStore(),
        resources=BatchResources(1, 1, 1),
        run_config=RUN_CONFIG,
    )
    assert len(seen) == 1
    recomputed = cache_identity(resolve_effective_params(_request("a"), run_config=RUN_CONFIG))
    assert cache_identity(seen[0]).digest == recomputed.digest


def test_batch_module_never_calls_backend_capabilities_or_task_modules() -> None:
    """Structural guard mirroring the acceptance greps."""
    source = (Path(__file__).resolve().parents[1] / "src/cccp/calculation/batch.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "backend.single_point(",
        "backend.optimize(",
        "backend.frequency(",
        "import subprocess",
        "from subprocess",
    ):
        assert forbidden not in source, forbidden
    assert "from cccp.calculation.requests" not in source
    assert "from cccp.calculation.context" not in source
    assert "run_singlepoint(" not in source


def test_progress_callback_failure_does_not_fail_the_science() -> None:
    def broken_callback(done, total):
        raise RuntimeError("consumer died")

    result = run_batch(
        _entries("a"),
        _RecordingExecutor(),
        cache=MemoryCacheStore(),
        resources=BatchResources(1, 1, 1),
        run_config=RUN_CONFIG,
        progress_callback=broken_callback,
    )
    assert result.n_success == 1
    assert result.results[0].status == "success"


def test_empty_batch_returns_empty_result() -> None:
    result = run_batch(
        [],
        _RecordingExecutor(),
        cache=MemoryCacheStore(),
        resources=BatchResources(1, 1, 1),
        run_config=RUN_CONFIG,
    )
    assert result.n_total == 0
    assert result.results == ()
