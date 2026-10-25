"""Regression tests for the 2026-10-21 architecture recheck (R01–R12).

Each test pins the specific defect documented in
``reports/architecture_recheck_20261021/REPORT.md``:

* R01 — the persisted ``task_result.json`` carries the full scientific body
  and publication produces viewer-visible energy/structure products;
* R02 — resolved config + resources (nproc/mem) reach the independent worker;
* R03 — dead-claim recovery is atomic (no dual holders);
* R04 — unbound assembly failures never touch the canonical receipt;
* R05 — a worker that lost the execution right never writes ``task_result``;
* R06 — the real remote fetcher receives the full remote location record;
* R07 — an attempt-1 read can never serve attempt-2 bytes;
* R08 — cache eviction expires prepare operations; READY-without-body reads
  as ABSENT; re-enqueue opens a new epoch;
* R09 — prepare fetches manifest-referenced ``frequency_modes`` products;
* R10 — bounded readers reject blocks spliced from a foreign source;
* R12 — the four-combination cell origin serves static F and proxies /api to S.

R11 (NMR late-response guard) is covered by the Node suite
(``frontend/js/module_tests/nmr_viewer.test.js``).
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from acp.calculations.attempt_freeze import (
    _build_singlepoint_request,
)
from acp.calculations.attempt_publication import AttemptResultPublication
from acp.calculations.result_publication import load_scientific_result
from acp.results.structure_viewer import build_structure_viewer_payload
from acp.scheduler.jobs import JobRecord, JobSpec
from acp_services.data.contracts import Availability, Freshness
from acp_services.data.operations import (
    PrepareOperationService,
    PrepareRequest,
    PrepareState,
)
from acp_services.data.read_root import (
    AvailabilityRead,
    OperationFacts,
    ReadOnlyResourceReader,
    ReadRoot,
)
from acp_services.runtime.attempt_freeze import (
    AttemptLockClaim,
    AttemptLockExecutionRight,
    attempt_lock_path,
)
from acp_services.runtime.execution_right import (
    AlwaysHeldExecutionRight,
    ExecutionRightState,
    ExecutionRightVerdict,
)
from acp_services.runtime.worker import (
    TASK_ENVIRONMENT_CONFIG_KEY,
    CccpTaskExecutor,
    ExecutionScope,
    FrozenPlan,
    decide_task_result,
    outcome_from_task_result,
    read_task_result,
    task_result_path,
    write_task_result,
)
from cccp.analysis.bounded.chunking import chunk_curve_product
from cccp.analysis.bounded.errors import BoundedSchemaError
from cccp.analysis.bounded.manifest import BoundedProduct
from cccp.calculation.results import TaskResult
from tests.test_services_chain_switch import _T25_XYZ, _envelope, _freeze_spec, _t28_attempt

# ── R01 ─────────────────────────────────────────────────────────────────


class _SinglePointBody:
    """TaskResult stand-in carrying both duck-typed facts and a body codec."""

    task = "singlepoint"
    status = "completed"
    complete = True
    energy_hartree = -76.0
    symbols = ("O", "H", "H")
    coordinates = ((0.0, 0.0, 0.0), (0.96, 0.0, 0.0), (-0.24, 0.93, 0.0))
    errors: tuple[Any, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "status": self.status,
            "complete": self.complete,
            "energy_hartree": self.energy_hartree,
            "symbols": list(self.symbols),
            "coordinates": [list(row) for row in self.coordinates],
            "errors": [],
        }


def test_r01_task_result_carries_full_scientific_body(tmp_path: Path) -> None:
    result = _SinglePointBody()
    decision = decide_task_result(result)

    write_task_result(tmp_path, decision, result)

    document = read_task_result(tmp_path)
    assert document is not None
    body = document["result"]
    assert body["energy_hartree"] == -76.0
    assert body["coordinates"] == [
        [0.0, 0.0, 0.0],
        [0.96, 0.0, 0.0],
        [-0.24, 0.93, 0.0],
    ]
    assert "content_digest" in document


def test_r01_publication_energy_reaches_viewer(tmp_path: Path) -> None:
    work, snapshot, _digest = _t28_attempt(tmp_path)

    report = AttemptResultPublication(workflow="singlepoint").publish(snapshot)

    document = read_task_result(work)
    assert document is not None and document["result"]["energy_hartree"] == -76.0
    scientific = load_scientific_result(work / "RESULT")
    assert scientific is not None
    assert scientific.summary["energy_hartree"] == -76.0

    product_paths = {product.path for product in report.manifest.products}
    assert "energy/singlepoint_energy.json" in product_paths
    assert "structures/singlepoint_final.xyz" in product_paths
    energy_doc = json.loads((work / "RESULT" / "energy" / "singlepoint_energy.json").read_text())
    assert energy_doc["energy_hartree"] == -76.0

    viewer = build_structure_viewer_payload(
        work, job_id=snapshot.job_id, workflow="singlepoint", job_status="completed"
    )
    assert viewer.entries
    assert viewer.entries[0].energy.value == -76.0


# ── R02 ─────────────────────────────────────────────────────────────────


def _r02_record(root: Path) -> tuple[JobRecord, Path]:
    cfg = root / "custom.yaml"
    cfg.write_text(
        "resources:\n  nproc: 3\n  mem: 2GB\n"
        "executables:\n  orca:\n    path: /tmp/acp-recheck-custom-orca\n",
        encoding="utf-8",
    )
    inp = root / "input.xyz"
    inp.write_text(_T25_XYZ, encoding="utf-8")
    record = JobRecord(
        id="recheck-r02",
        spec=JobSpec(
            workflow="singlepoint",
            name="recheck-r02",
            input={"charge": 0, "multiplicity": 1},
            config_path=str(cfg),
            resources={"nproc": 3, "mem": "2GB"},
        ),
    )
    return record, inp


def test_r02_resources_and_config_reach_worker_executor(tmp_path: Path) -> None:
    record, inp = _r02_record(tmp_path)
    context, request, task_request = _build_singlepoint_request(record, tmp_path, inp)

    assert context.config["resources"]["nproc"] == 3
    assert task_request.resources.nproc == 3
    assert task_request.resources.mem is not None
    assert task_request.resources.mem_total_mb() == 2048.0

    captured: dict[str, Any] = {}

    def scientific_runner(request: Any, *, context: Any) -> TaskResult:
        captured["config"] = context.config
        captured["resources"] = request.resources.to_dict()
        return TaskResult(
            task=request.task,
            status="completed",
            complete=True,
            energy_hartree=-76.0,
            coordinates=((0.0, 0.0, 0.0), (0.96, 0.0, 0.0), (-0.24, 0.93, 0.0)),
            symbols=("O", "H", "H"),
        )

    payload = dict(task_request.to_dict())
    payload[TASK_ENVIRONMENT_CONFIG_KEY] = dict(request.resources["config"])
    scope = ExecutionScope(
        envelope=_envelope(), plan=FrozenPlan(payload=payload), work_dir=tmp_path
    )
    with patch("cccp.calculation.run_singlepoint", scientific_runner):
        outcome = CccpTaskExecutor().execute(scope)

    assert captured["config"] is not None
    assert captured["config"]["executables"]["orca"]["path"] == "/tmp/acp-recheck-custom-orca"
    assert captured["config"]["resources"]["nproc"] == 3
    assert captured["resources"]["nproc"] == 3
    assert outcome.state == "completed"


# ── R03 ─────────────────────────────────────────────────────────────────


_R03_CHILD = r"""
import json, sys, time
from pathlib import Path

sys.path.insert(0, %(src)r)
from acp_services.contracts.envelope import ExecutionEnvelope
from acp_services.runtime.attempt_freeze import (
    AttemptLockExecutionRight,
    AttemptRecoveryProof,
    attempt_lock_path,
    read_attempt_lock,
)

root = Path(%(root)r)
name = %(name)r
lock_path = attempt_lock_path(root / "lock")
envelope = ExecutionEnvelope.from_dict(json.loads((root / "envelope.json").read_text()))

# Pre-read the dead claim (the shared observation that raced pre-R03), then
# synchronize with the other recoverer before either attempts recovery.
read_attempt_lock(lock_path)
(root / ("ready-" + name)).write_text("1")
deadline = time.time() + 30
while not (root / ("ready-A")).exists() or not (root / ("ready-B")).exists():
    if time.time() > deadline:
        sys.exit(3)
    time.sleep(0.01)

class ReconciledFixture:
    def confirm_stopped(self, claim):
        # The parent constructed a synthetic, never-launched holder, so this
        # fixture has no execution children.  Parent pid death by itself is no
        # longer accepted as production recovery proof.
        return AttemptRecoveryProof(
            claim.content_digest(), True, True, "test: synthetic childless holder"
        )

right = AttemptLockExecutionRight(
    lock_path=lock_path, lease_seconds=60.0, recovery_check=ReconciledFixture()
)
verdict = right.acquire(envelope)
import os
print(json.dumps({
    "name": name,
    "held": bool(verdict.held),
    "detail": verdict.detail,
    "pid": os.getpid(),
}))
time.sleep(0.5)
"""


def test_r03_concurrent_dead_claim_recovery_yields_single_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    envelope = _envelope()
    (tmp_path / "envelope.json").write_text(json.dumps(envelope.to_dict()), encoding="utf-8")
    lock_path = attempt_lock_path(tmp_path / "lock")
    dead_claim = AttemptLockClaim(
        job_id=envelope.job_id,
        attempt=envelope.attempt,
        owner="dead-holder",
        fencing_token="dead",
        pid=999_999_999,
        host=socket.gethostname(),
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(dead_claim.to_dict()), encoding="utf-8")

    src_root = str(Path(__file__).resolve().parents[1] / "src")
    procs = []
    for name in ("A", "B"):
        script = _R03_CHILD % {"src": src_root, "root": str(tmp_path), "name": name}
        procs.append(
            subprocess.Popen(
                [sys.executable, "-c", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )
    outputs = [proc.communicate(timeout=60) for proc in procs]
    for proc, (out, err) in zip(procs, outputs):
        assert proc.returncode == 0, f"child failed: {err}"
    children = [SimpleNamespace(stdout=out) for out, _err in outputs]
    verdicts = [json.loads(child.stdout.strip().splitlines()[-1]) for child in children]

    held = [entry["name"] for entry in verdicts if entry["held"]]
    assert len(held) == 1, f"exactly one recoverer may hold the claim, got {verdicts}"
    final_claim = json.loads(lock_path.read_text(encoding="utf-8"))
    assert final_claim["owner"] != "dead-holder"


def test_r03_cross_host_claim_is_never_stolen(tmp_path: Path) -> None:
    work = tmp_path / "task"
    work.mkdir()
    lock_path = attempt_lock_path(work)
    envelope = _envelope()
    foreign = AttemptLockClaim(
        job_id=envelope.job_id,
        attempt=envelope.attempt,
        owner="other-node",
        fencing_token="x",
        pid=999_999_999,
        host="some-other-lsf-node",
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(foreign.to_dict()), encoding="utf-8")

    verdict = AttemptLockExecutionRight(lock_path=lock_path).acquire(envelope)

    assert not verdict.held
    assert "other-node" in (verdict.detail or "")
    assert json.loads(lock_path.read_text(encoding="utf-8"))["owner"] == "other-node"


# ── R04 ─────────────────────────────────────────────────────────────────


class _NotHeldRight(AlwaysHeldExecutionRight):
    """Both seams refuse: no execution right is ever held."""

    def acquire(self, envelope: Any) -> ExecutionRightVerdict:
        return ExecutionRightVerdict(
            state=ExecutionRightState.NOT_HELD, detail="not held"
        )

    def verify(self, envelope: Any) -> ExecutionRightVerdict:
        return ExecutionRightVerdict(
            state=ExecutionRightState.NOT_HELD, detail="not held"
        )


def test_r04_unbound_failure_preserves_existing_receipt(tmp_path: Path) -> None:
    from acp_services.worker_bootstrap import main as worker_main

    work = tmp_path / "task"
    work.mkdir()
    (work / "input.xyz").write_text(_T25_XYZ, encoding="utf-8")
    receipt_path = work / "execution_receipt.json"
    completed_bytes = b'{"receipt_version": 1, "state": "completed"}\n'
    receipt_path.write_bytes(completed_bytes)

    exit_code = worker_main(
        [
            "--envelope",
            str(tmp_path / "missing-envelope.json"),
            "--plan",
            str(tmp_path / "missing-plan.json"),
            "--work-dir",
            str(work),
            "--receipt",
            str(receipt_path),
        ],
        execution_right=_NotHeldRight(),
    )

    assert exit_code == 2
    assert receipt_path.read_bytes() == completed_bytes
    unbound = json.loads(
        (work / "execution_receipt.json.unbound.json").read_text(encoding="utf-8")
    )
    assert unbound["schema"] == "worker_assembly_error"


# ── R05 ─────────────────────────────────────────────────────────────────


class _RightThatVerifiesLost(AlwaysHeldExecutionRight):
    """acquire → HELD; verify (the write seam) → LOST."""

    def verify(self, envelope: Any) -> ExecutionRightVerdict:
        return ExecutionRightVerdict(state=ExecutionRightState.LOST, detail="claim taken over")


def _r05_runtime(tmp_path: Path, *, execution_right: Any) -> tuple[Any, Any]:
    from acp_services.runtime.attempt_freeze import write_frozen_attempt
    from acp_services.worker_bootstrap import (
        assemble_worker,
        load_frozen_envelope,
        load_frozen_plan,
    )

    work = tmp_path / "task"
    work.mkdir(parents=True)
    group = write_frozen_attempt(work, _freeze_spec("singlepoint"))
    envelope = load_frozen_envelope(group.envelope_path)
    plan = load_frozen_plan(group.plan_path)

    class _Executor:
        def __init__(self) -> None:
            self.executed = False

        def execute(self, scope: Any) -> Any:
            self.executed = True
            return outcome_from_task_result(_SinglePointBody())

    executor = _Executor()
    runtime = assemble_worker(
        envelope=envelope,
        plan=plan,
        work_dir=work,
        receipt_path=work / "execution_receipt.json",
        executor=executor,
        execution_right=execution_right,
    )
    return runtime, executor


def test_r05_lost_right_worker_never_writes_task_result(tmp_path: Path) -> None:
    from acp_services.runtime.execution_right import WorkerRefusal

    runtime, executor = _r05_runtime(tmp_path, execution_right=_RightThatVerifiesLost())
    work = Path(runtime.work_dir)

    result = runtime.run()

    assert isinstance(result, WorkerRefusal)
    assert executor.executed
    assert not task_result_path(work).exists()
    assert not (work / "execution_receipt.json").exists()
    assert (work / "execution_receipt.json.refused.json").is_file()


def test_r05_held_right_worker_pends_task_result_until_seam(tmp_path: Path) -> None:
    runtime, executor = _r05_runtime(tmp_path, execution_right=AlwaysHeldExecutionRight())
    work = Path(runtime.work_dir)

    receipt = runtime.run()

    assert executor.executed
    assert receipt.state == "completed"
    document = read_task_result(work)
    assert document is not None and document["result"]["energy_hartree"] == -76.0
    evidence = json.loads(
        (work / "execution_receipt.json").read_text(encoding="utf-8")
    )
    assert evidence["evidence_digests"]["task_result_digest"] == document["content_digest"]


# ── R06 ─────────────────────────────────────────────────────────────────


class _CapturingFetcher:
    def __init__(self) -> None:
        self.records: list[Any] = []

    def read_file(self, record: Any, filename: str) -> bytes:
        self.records.append(record)
        return b"{}"

    def write_file(self, record: Any, filename: str, data: bytes) -> None:
        self.records.append(record)


def test_r06_source_adapter_supplies_remote_location(tmp_path: Path) -> None:
    from acp.results.remote_structure_cache import RemoteStructureCache
    from acp.services_data_bootstrap import build_transfer_source_port

    fetcher = _CapturingFetcher()
    cache = RemoteStructureCache(tmp_path / "run", fetcher_factory=lambda _jid: fetcher)
    job_record = SimpleNamespace(
        id="job-r06",
        attempt=1,
        result={"node": "lsf-1", "remote_dir": "/remote/job/r06"},
    )
    source = build_transfer_source_port(cache, job_lookup=lambda _jid: job_record)

    read = source.read("job-r06", 1, "RESULT/result_manifest.json")

    assert read.data == b"{}"
    assert fetcher.records, "the real fetcher must have been called"
    served = fetcher.records[-1]
    assert served.result["node"] == "lsf-1"
    assert served.result["remote_dir"] == "/remote/job/r06"


def test_r06_missing_record_degrades_to_typed_not_remote(tmp_path: Path) -> None:
    from acp.results.remote_structure_cache import RemoteStructureCache
    from acp.services_data_bootstrap import build_transfer_source_port
    from acp_services.data import transfer as tx

    class _RemoteMetaFetcher:
        def __init__(self) -> None:
            self.record: Any = None

        def read_file(self, record: Any, filename: str) -> bytes:
            self.record = record
            raise FileNotFoundError(filename)

    fetcher = _RemoteMetaFetcher()
    cache = RemoteStructureCache(tmp_path / "run", fetcher_factory=lambda _jid: fetcher)
    source = build_transfer_source_port(cache, job_lookup=lambda _jid: None)

    with pytest.raises(tx.TransferSourceError) as excinfo:
        source.read("job-r06", 1, "RESULT/missing.json")
    assert excinfo.value.kind is tx.SourceFailureKind.NOT_FOUND
    assert isinstance(fetcher.record.result, dict)


# ── R07 ─────────────────────────────────────────────────────────────────


def _r07_cache(tmp_path: Path, attempt_files: dict[str, bytes]) -> Any:
    from acp.results.remote_structure_cache import RemoteStructureCache

    cache = RemoteStructureCache(tmp_path / "run")
    for rel, data in attempt_files.items():
        target = cache.cache_path("job-r07", rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        cache._write_attempt_sidecar(target, 1)
    cache._write_fence("job-r07", attempt=2, generation=0)
    return cache


def test_r07_attempt1_read_never_serves_attempt2_body(tmp_path: Path) -> None:
    from acp.services_data_bootstrap import _ReadCacheIndexAdapter

    rel = "RESULT/result_manifest.json"
    cache = _r07_cache(tmp_path, {rel: b'{"attempt": 1}'})

    # The publisher atomically replaces body+sidecar with attempt 2.
    target = cache.cache_path("job-r07", rel)
    target.write_bytes(b'{"attempt": 2}')
    cache._write_attempt_sidecar(target, 2)

    root = ReadRoot(
        job_id="job-r07",
        path=str(cache.job_root("job-r07")),
        is_remote=True,
        found=True,
        attempt=1,
    )
    adapter = _ReadCacheIndexAdapter(cache)
    facts = adapter.entry(root, rel, attempt=1)
    assert facts.present is False


def test_r07_reader_holding_lock_pins_attempt1_bytes(tmp_path: Path) -> None:
    from acp.services_data_bootstrap import _ReadCacheIndexAdapter

    rel = "RESULT/result_manifest.json"
    cache = _r07_cache(tmp_path, {rel: b'{"attempt": 1}'})
    root = ReadRoot(
        job_id="job-r07",
        path=str(cache.job_root("job-r07")),
        is_remote=True,
        found=True,
        attempt=1,
    )
    adapter = _ReadCacheIndexAdapter(cache)

    # Hook the approve→open window: while the reader is between approval and
    # the descriptor open, the publish path's generation lock must be held by
    # the reader (mutual exclusion with the attempt-2 publisher).
    original_snapshot = _ReadCacheIndexAdapter._snapshot_facts
    inside_window = threading.Event()
    lock_blocked = threading.Event()
    leave_window = threading.Event()
    result: dict[str, Any] = {}

    def hooked(root_dir, relative_path, approved, generation, *, snapshot):
        inside_window.set()
        lock = cache._get_generation_lock("job-r07")
        if not lock.acquire(timeout=2.0):
            lock_blocked.set()
        else:
            lock.release()
        leave_window.wait(timeout=10)
        return original_snapshot(
            root_dir, relative_path, approved, generation, snapshot=snapshot
        )

    def reader() -> None:
        with patch.object(
            _ReadCacheIndexAdapter, "_snapshot_facts", staticmethod(hooked)
        ):
            result["facts"] = adapter.entry(root, rel, attempt=1, snapshot=True)

    thread = threading.Thread(target=reader)
    thread.start()
    assert inside_window.wait(timeout=10)
    assert lock_blocked.wait(timeout=5), "entry must hold the generation lock across approve→open"
    leave_window.set()
    thread.join(timeout=10)

    import os

    facts = result["facts"]
    assert facts.present is True
    assert facts.snapshot is not None
    # The publisher (same lock) could not swap the body during the window; the
    # pinned descriptor still serves the approved attempt-1 bytes.
    assert b'"attempt": 1' in os.read(facts.snapshot, 4096)


# ── R08 ─────────────────────────────────────────────────────────────────


def _r08_request(attempt: int = 1) -> PrepareRequest:
    from acp_services.data.contracts import ResourceKind

    return PrepareRequest(
        job_id="job-r08",
        attempt=attempt,
        resource_id="catalog:singlepoint",
        resource_kind=ResourceKind.CACHE,
        resource_digest="job-r08\x1f1\x1fsinglepoint",
        payload={"workflow": "singlepoint"},
    )


def test_r08_expire_for_job_allows_new_epoch(tmp_path: Path) -> None:
    service = PrepareOperationService(tmp_path / "ops.db")

    first = service.enqueue(_r08_request())
    assert first.state is PrepareState.QUEUED

    expired = service.expire_for_job("job-r08", reason="cache_purged")
    assert expired >= 1
    stale = service.get(first.operation_id)
    assert stale is not None and stale.state is PrepareState.EXPIRED

    second = service.enqueue(_r08_request())
    assert second.operation_id != first.operation_id
    assert second.state is PrepareState.QUEUED


def test_r08_availability_ready_without_body_is_absent(tmp_path: Path) -> None:
    class _Resolver:
        def resolve(self, job_id: str, attempt: int | None = None) -> ReadRoot:
            return ReadRoot(
                job_id=job_id,
                path=str(tmp_path),
                is_remote=True,
                found=True,
                attempt=attempt if attempt is not None else 1,
            )

    class _MissingIndex:
        def entry(self, root: ReadRoot, rel: str, **_: Any) -> Any:
            return SimpleNamespace(present=False, total_size=None, generation=0, cached_path=None)

        def catalog(self, root: ReadRoot, workflow: str) -> Any:
            return SimpleNamespace(present=False, total_size=None, generation=0, cached_path=None)

    class _SucceededOperations:
        def facts(self, request: Any) -> OperationFacts:
            return OperationFacts(
                exists=True,
                availability=Availability.READY,
                freshness=Freshness.CURRENT,
                basis_revision=3,
                expected_revision=3,
                detail="operation",
            )

    reader = ReadOnlyResourceReader(
        resolver=_Resolver(),
        cache_index=_MissingIndex(),
        operations=_SucceededOperations(),
        clock=lambda: "2026-10-21T00:00:00+00:00",
    )
    snapshot = reader.availability(
        AvailabilityRead(
            job_id="job-r08",
            resource_id="catalog:singlepoint",
            relative_path="RESULT/result_manifest.json",
            attempt=1,
        )
    )
    assert snapshot.availability is Availability.ABSENT
    assert snapshot.prepared_root is None
    assert snapshot.detail == "operation_ready_body_missing"


# ── R09 ─────────────────────────────────────────────────────────────────


def test_r09_prepare_discovers_frequency_mode_products(tmp_path: Path) -> None:
    from acp.results.remote_structure_cache import RemoteStructureCache
    from acp.services_data_bootstrap import _PrepareExecutorAdapter

    cache = RemoteStructureCache(tmp_path / "run")
    manifest = {
        "products": [
            {"kind": "frequency_modes", "path": "frequencies/normal_modes.json"},
            {"kind": "structure", "path": "structures/final.xyz"},
        ]
    }
    manifest_target = cache.cache_path("job-r09", "RESULT/result_manifest.json")
    manifest_target.parent.mkdir(parents=True, exist_ok=True)
    manifest_target.write_text(json.dumps(manifest), encoding="utf-8")
    cache._write_attempt_sidecar(manifest_target, 1)

    lease = SimpleNamespace(
        job_id="job-r09",
        attempt=1,
        payload={},
    )
    paths = _PrepareExecutorAdapter._aux_frequency_mode_paths(
        cache, lease, "frequency"  # type: ignore[arg-type]
    )
    assert paths == ("RESULT/frequencies/normal_modes.json",)


# ── R10 ─────────────────────────────────────────────────────────────────


def _r10_curve_product(n: int, version_tag: str) -> Any:
    from tests.test_cccp_bounded_products import _curve_product

    product = _curve_product(n)
    identity = replace(
        product.identity,
        algorithm_version=version_tag,
        params_digest=product.identity.params_digest,
    )
    return replace(product, identity=identity)


def test_r10_spliced_foreign_block_is_rejected(tmp_path: Path) -> None:
    from tests.test_cccp_bounded_products import _curve_product

    new_source = _curve_product(64)
    old_source = _r10_curve_product(64, "9")
    new_bounded = chunk_curve_product(new_source, chunk_size=32)
    old_bounded = chunk_curve_product(old_source, chunk_size=32)
    assert old_bounded.blocks[0].product.identity.params_digest != (
        new_bounded.blocks[0].product.identity.params_digest
    )

    # Splice: keep the NEW shell, embed the OLD source's block, and rewrite
    # the shell digests so only the block's derived identity stays foreign.
    spliced_block = replace(
        old_bounded.blocks[0],
        source_identity_digest=new_bounded.spec.source_identity_digest,
    )
    with pytest.raises(BoundedSchemaError, match="not derivable"):
        BoundedProduct(
            identity=new_bounded.identity,
            base_identity=new_bounded.base_identity,
            spec=new_bounded.spec,
            display_subsampled=new_bounded.display_subsampled,
            note=new_bounded.note,
            blocks=(spliced_block, *new_bounded.blocks[1:]),
            key_points=new_bounded.key_points,
            schema_version=new_bounded.schema_version,
        )

    payload = new_bounded.to_dict()
    old_block_payload = old_bounded.to_dict()["blocks"][0]
    old_block_payload["source_identity_digest"] = new_bounded.spec.source_identity_digest
    payload["blocks"] = [old_block_payload]
    with pytest.raises(BoundedSchemaError, match="not derivable"):
        BoundedProduct.from_dict(payload)


def test_r10_honest_manifests_still_round_trip(tmp_path: Path) -> None:
    from tests.test_cccp_bounded_products import _curve_product

    bounded = chunk_curve_product(_curve_product(80), chunk_size=32)
    restored = BoundedProduct.from_dict(json.loads(json.dumps(bounded.to_dict())))
    assert restored.is_cache_adoptable() is True
    assert restored.total_items() == bounded.total_items()


# ── R12 ─────────────────────────────────────────────────────────────────


def test_r12_cell_origin_serves_static_and_proxies_api(tmp_path: Path) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from scripts.acceptance import four_combinations as fc

    class _ApiStub(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            payload = json.dumps({"status": "stub"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

    api = ThreadingHTTPServer(("127.0.0.1", 0), _ApiStub)
    api_thread = threading.Thread(target=api.serve_forever, daemon=True)
    api_thread.start()
    frontend = tmp_path / "frontend"
    (frontend / "js").mkdir(parents=True)
    (frontend / "ACP_Workbench_v2.html").write_text(
        '<html><script src="js/workbench.js"></script></html>', encoding="utf-8"
    )
    (frontend / "js" / "workbench.js").write_text("console.log('ok');", encoding="utf-8")

    origin = fc._CellOriginServer(frontend, api.server_address[1])
    origin.start()
    try:
        probe = fc._probe_frontend_load(origin.base)
        assert probe["ok"], probe

        code, body = fc._get(origin.base, "/api/status")
        assert code == 200
        assert isinstance(body, dict) and body["status"] == "stub"
    finally:
        origin.stop()
        api.shutdown()
        api.server_close()
