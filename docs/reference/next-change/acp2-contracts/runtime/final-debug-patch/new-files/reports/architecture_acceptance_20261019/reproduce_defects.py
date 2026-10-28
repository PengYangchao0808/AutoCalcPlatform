"""Acceptance probes for 878e355; failures express the required behavior.

Run from the repository root:
    python3.11 -m pytest reports/architecture_acceptance_20261019/reproduce_defects.py -q

No scientific executable, network connection, production DB, or service is used.
Only pytest temporary directories are written. These are review evidence, not
changes to the project's existing regression expectations.
"""

from dataclasses import replace

import pytest

from acp.results.remote_structure_cache import RemoteStructureCache
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.services_data_bootstrap import build_cache_only_resource_reader
from acp_services.contracts.envelope import ExecutionEnvelope, expected_submission_id
from acp_services.data.contracts import ResourceKind
from acp_services.data.operations import PrepareOperationService, PrepareRequest, PrepareState
from acp_services.runtime.worker import (
    TaskExecutionOutcome,
    _outcome_from_task_result,
    build_receipt,
    write_receipt_atomic,
)
from acp_services.worker_bootstrap import assemble_worker, freeze_frozen_plan
from cccp.calculation.requests import TaskKind
from cccp.calculation.results import TaskResult


def frozen_case(attempt=1):
    plan = freeze_frozen_plan(
        payload={"task": "singlepoint"}, plan_identity="v2:audit", step_identities=()
    )
    envelope = ExecutionEnvelope(
        envelope_version=1,
        job_id="audit-worker",
        attempt=attempt,
        submission_id=expected_submission_id("audit-worker", attempt),
        release_id="release-a",
        release_digest="digest-a",
        owner="audit-owner",
        fencing_token="audit-fence",
        plan_digest=plan.identity.plan_digest,
        plan_identity=plan.identity.plan_identity,
        step_identities=plan.identity.step_identities,
        entrypoint="worker.bootstrap",
    )
    return plan, envelope


class NeverExecute:
    def execute(self, scope):
        raise AssertionError("A conflicting receipt must block execution")


@pytest.mark.parametrize("status", ["failed", "cancelled", "blocked"])
def test_d01_worker_preserves_non_success_status(status):
    result = TaskResult(
        task=TaskKind.SINGLEPOINT, status=status, complete=False, errors=("audit failure",)
    )
    outcome = _outcome_from_task_result(result)
    assert outcome.state == status, (status, outcome.state)


def test_d02_composition_root_does_not_return_previous_attempt_success(tmp_path):
    plan, old = frozen_case(1)
    _, current = frozen_case(2)
    receipt_path = tmp_path / "execution_receipt.json"
    write_receipt_atomic(receipt_path, build_receipt(old, TaskExecutionOutcome("completed")))
    runtime = assemble_worker(
        envelope=current, plan=plan, work_dir=tmp_path, executor=NeverExecute()
    )
    receipt = runtime.run()
    assert receipt.attempt == current.attempt, receipt.to_dict()
    assert receipt.state != "completed"


def test_d02_same_attempt_changed_release_does_not_reuse_old_success(tmp_path):
    plan, old = frozen_case()
    current = replace(old, release_id="release-b", release_digest="digest-b")
    write_receipt_atomic(
        tmp_path / "execution_receipt.json", build_receipt(old, TaskExecutionOutcome("completed"))
    )
    runtime = assemble_worker(
        envelope=current, plan=plan, work_dir=tmp_path, executor=NeverExecute()
    )
    receipt = runtime.run()
    assert receipt.release_id == current.release_id, receipt.to_dict()
    assert receipt.state != "completed"


def test_d03_v1_projection_preserves_execution_identity():
    from acp.api.v1_routes import _record_to_v1_model

    record = JobRecord(
        id="audit-view",
        spec=JobSpec(workflow="singlepoint", name="audit"),
        attempt=6,
        revision=42,
    )
    model = _record_to_v1_model(record)
    assert (model.attempt, model.revision) == (6, 42)


def test_d04_prepared_reader_serves_existing_local_manifest(tmp_path):
    work = tmp_path / "project" / "water"
    manifest = work / "RESULT" / "result_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"audit": true}')
    record = JobRecord(
        id="audit-local",
        spec=JobSpec(workflow="singlepoint", name="audit"),
        status=JobStatus.COMPLETED,
        work_dir=str(work),
    )
    reader = build_cache_only_resource_reader(
        job_lookup=lambda _: record, cache=RemoteStructureCache(tmp_path)
    )
    assert reader.serve("catalog", job_id=record.id, workflow="singlepoint").served
    result = reader.serve("manifest", job_id=record.id, workflow="singlepoint")
    assert result.served, result
    assert result.data == manifest.read_bytes()


def test_d05_prepare_new_generation_is_not_old_success(tmp_path):
    service = PrepareOperationService(tmp_path / "prepare.db")
    try:
        original = PrepareRequest(
            job_id="audit-prepare",
            attempt=1,
            resource_id="catalog:singlepoint",
            resource_kind=ResourceKind.CACHE,
            resource_generation=1,
            resource_digest="sha256:old",
        )
        first = service.enqueue(original)
        lease = service.lease_next()
        assert lease is not None
        service.complete(lease)
        # Same task/attempt/resource, but current content/cache generation changed.
        newer = replace(original, resource_generation=2, resource_digest="sha256:new")
        second = service.enqueue(newer)
        assert second.resource_generation == 2, second
        assert second.state != PrepareState.SUCCEEDED
        assert service.lease_next() is not None
        assert first.operation_id
    finally:
        service.close()


def test_d06_prepare_adapter_does_not_mark_old_attempt_ready(tmp_path):
    from acp.services_data_bootstrap import build_prepare_executor

    work = tmp_path / "project" / "water"
    path = work / "RESULT" / "result_manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"attempt": 2}')
    record = JobRecord(
        id="audit-prepare",
        spec=JobSpec(workflow="singlepoint", name="audit"),
        status=JobStatus.COMPLETED,
        attempt=2,
        work_dir=str(work),
    )
    service = PrepareOperationService(tmp_path / "prepare.db")
    try:
        service.enqueue(
            PrepareRequest(
                job_id=record.id,
                attempt=1,
                resource_id="catalog:singlepoint",
                resource_kind=ResourceKind.CACHE,
            )
        )
        old_lease = service.lease_next()
        assert old_lease is not None and old_lease.attempt == 1
        executor = build_prepare_executor(
            job_lookup=lambda _: record,
            cache_provider=lambda: RemoteStructureCache(tmp_path),
            is_remote=lambda _: True,
        )
        result = executor.execute(old_lease)
        assert not result.prepared, result
    finally:
        service.close()


def test_d07_default_dispatch_produces_frozen_artifacts_for_enabled_workflow(tmp_path):
    from acp.scheduler.runner import JobRunner
    from acp_services.runtime.execution_chain import ENABLED_WORKFLOWS

    assert "singlepoint" in ENABLED_WORKFLOWS
    record = JobRecord(
        id="audit-dispatch",
        spec=JobSpec(workflow="singlepoint", name="audit"),
        work_dir=str(tmp_path),
    )
    command = JobRunner()._build_execution_cmd(record, tmp_path, "water.xyz", None)
    assert command[1:3] == ["-m", "acp_services.worker_bootstrap"], command


def test_d10_bounded_product_keeps_source_algorithm_identity():
    from cccp.analysis.bounded import chunk_curve_product
    from cccp.calculation.products import (
        CurveSeries,
        PointRecord,
        SeriesRef,
        build_curve_scatter_product,
    )

    def source(y, version):
        series = CurveSeries(
            ref=SeriesRef(series_id="curve", x_unit="angstrom", y_unit="hartree"),
            points=(PointRecord(point_id="p0", x=0.0, y=y),),
        )
        return build_curve_scatter_product(
            series=[series],
            input_digest="sha256:same-raw-input",
            algorithm="parser",
            algorithm_version=version,
            params_digest=version,
        )

    a, b = source(-1.0, "1"), source(-2.0, "2")
    assert a.identity != b.identity
    ca, cb = chunk_curve_product(a, chunk_size=1), chunk_curve_product(b, chunk_size=1)
    assert ca.identity != cb.identity, "Different source algorithms collapse to one cache identity"
    assert ca.blocks[0].product.identity != cb.blocks[0].product.identity


def test_d11_default_availability_reports_pending_current_attempt(tmp_path):
    from acp_services.data.contracts import Availability

    record = JobRecord(
        id="audit-availability",
        spec=JobSpec(workflow="singlepoint", name="audit"),
        status=JobStatus.COMPLETED,
        attempt=2,
        work_dir=str(tmp_path / "work"),
        result={"node": "audit-node", "remote_dir": "/scratch/audit"},
    )
    service = PrepareOperationService(tmp_path / "prepare.db")
    try:
        service.enqueue(
            PrepareRequest(
                job_id=record.id,
                attempt=record.attempt,
                resource_id="catalog:singlepoint",
                resource_kind=ResourceKind.CACHE,
            )
        )
        reader = build_cache_only_resource_reader(
            job_lookup=lambda _: record,
            cache=RemoteStructureCache(tmp_path),
            operations=service,
        )
        # The public availability endpoint defaults its optional attempt to None.
        default = reader.availability("catalog", job_id=record.id, workflow="singlepoint")
        explicit = reader.availability(
            "catalog",
            job_id=record.id,
            workflow="singlepoint",
            attempt=2,
        )
        assert explicit.availability == Availability.PREPARING
        assert default.availability == explicit.availability, (default, explicit)
    finally:
        service.close()
