"""Review implementation assumptions with real modules; never launch QC or services."""
from __future__ import annotations

import inspect
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from acp_services.runtime.step_dag import (
    BoundedStepDag, BoundedStepDagExecutor, BudgetCaps, OutputRef,
    ScienceStepKind, StepSpec, cccp_compute_dispatch,
)
from acp_services.runtime.worker import FrozenPlan, frozen_plan_body_digest, outcome_from_task_result
from acp_services.runtime.canonical_digest import frozen_plan_definition, plan_digest
from acp_services.runtime.idempotency import canonical_request_digest, resolve_scope_id
from acp_services.store.store import JobStore
from acp_services.store.records import JobSpec
from acp.scheduler.remote.script_gen import derive_lsf_resources
from cccp.calculation.context import TaskContext
from cccp.calculation.requests import OptimizeOptions, RescueSpec, TaskKind, TaskRequest
from cccp.calculation.results import TaskResult
from cccp.calculation.recipes.compile import materialize_step_identity, output_ref_address
from tests.test_cccp_task_optimize import _RecordingBackend, _request, _ok
from tests.test_acp2_science_recipe import _compiled, _WHOLE
from cccp.calculation.recipes.contracts import OutputRef as ScienceOutputRef
from tests.test_services_worker_contract import _envelope, _receipt, _verify

out = {"scope": "Real adapters, DAG, compiler and stores. Scientific runners replaced with typed TaskResult; optimize uses recording backend; temporary files only."}

def dag(kind=ScienceStepKind.SINGLEPOINT):
    return BoundedStepDag(recipe_id="review", task_ref="review-task", steps=(
        StepSpec(step_id="compute", kind=kind, intent="review",
            implementation_version="review-v1", produces=(OutputRef("compute", "energy"),)),
    ))

def publish(step, outcome):
    return dict(outcome.output_digests)

cases = []
for status, complete, energy in [("failed", False, None), ("completed", False, None),
                                ("completed", True, None), ("completed", True, -40.0)]:
    task_result = TaskResult(task=TaskKind.SINGLEPOINT, status=status, complete=complete,
        energy_hartree=energy,
        errors=("controlled failure",) if status == "failed" else ())
    with patch("cccp.calculation.run_singlepoint", return_value=task_result):
        executor = BoundedStepDagExecutor(dag(),
            dispatch=lambda ctx: cccp_compute_dispatch(ctx, TaskRequest(task=TaskKind.SINGLEPOINT)),
            publish=publish, plan_identity="approved-plan", release_id="review-release")
        state = executor.run()
    cases.append({"input_status": status, "input_complete": complete, "energy_hartree": energy,
        "existing_worker_truth_table_state": outcome_from_task_result(task_result).state,
        "dag_status": state.statuses(), "returned_products": state.states["compute"].output_digests,
        "declared_products": ["energy"]})
assert cases[0]["dag_status"]["compute"] == "completed"
assert cases[0]["existing_worker_truth_table_state"] == "failed"
assert cases[-1]["existing_worker_truth_table_state"] == "completed"
out["dag_task_result_semantics"] = cases
raw = BoundedStepDagExecutor(dag(), dispatch=cccp_compute_dispatch, publish=publish,
    plan_identity="approved-plan", release_id="review-release")
raw.run()
assert "request" in raw.state.states["compute"].reason
out["direct_dispatch_signature"] = {"signature": str(inspect.signature(cccp_compute_dispatch)),
    "state": raw.statuses(), "reason": raw.state.states["compute"].reason}

with tempfile.TemporaryDirectory(prefix="acp-plan-review-") as temporary:
    backend = _RecordingBackend([RuntimeError("optimization failed [scf_failure]"),
        RuntimeError("optimization failed [scf_failure]"), _ok(["C"])])
    request = _request(options=OptimizeOptions(rescue=RescueSpec(policy="adaptive", max_rescue=2)),
        output_dir=Path(temporary) / "opt")
    executor = BoundedStepDagExecutor(dag(ScienceStepKind.OPTIMIZE),
        dispatch=lambda ctx: cccp_compute_dispatch(ctx, request, task_context=TaskContext(backend=backend)),
        publish=publish, plan_identity="approved-plan", release_id="review-release",
        budget_caps=BudgetCaps(qc_invocations=1))
    state = executor.run()
    assert len(backend.calls) == 3 and state.budget.qc_invocations == 1
    out["qc_budget_granularity"] = {"budget_cap": 1,
        "dag_qc_count": state.budget.qc_invocations, "real_task_backend_calls": len(backend.calls),
        "dag_status": state.statuses(), "real_qc_processes": 0}

    store = JobStore(Path(temporary) / "jobs.db")
    digest = canonical_request_digest({"workflow": "singlepoint", "input": {"source": "C"}})
    first = store.claim_idempotency(resolve_scope_id("principal_local", "project-a"), "submit", "stable-old-key", digest)
    new = store.claim_idempotency(resolve_scope_id("principal-alice", "project-a"), "submit", "stable-old-key", digest)
    out["identity_migration_existing_key"] = {"old_outcome": first.kind, "new_outcome": new.kind, "detail": new.detail}

compiled = _compiled()
address = output_ref_address(ScienceOutputRef("sp", "energy", _WHOLE))
try:
    materialize_step_identity(compiled, "opt", {}, {"orca": "6.0.1"})
except ValueError as exc:
    missing = {"type": type(exc).__name__, "message": str(exc)}
first = materialize_step_identity(compiled, "opt", {address: "a" * 64}, {"orca": "6.0.1"})
second = materialize_step_identity(compiled, "opt", {address: "b" * 64}, {"orca": "6.0.1"})
assert first.execution_identity != second.execution_identity
out["late_bound_step_identity"] = {"definition_digest": compiled.plan_definition_digest,
    "missing_upstream": missing, "upstream_a": first.execution_identity, "upstream_b": second.execution_identity}
definition = frozen_plan_definition(workflow="singlepoint", input={"source": "C"})
out["digest_domains"] = {"submit_authorization_plan_digest": plan_digest(definition).digest,
    "runtime_frozen_plan_body_digest": frozen_plan_body_digest(FrozenPlan(payload=definition)),
    "recipe_definition_digest": compiled.plan_definition_digest}
spec = JobSpec(workflow="singlepoint", name="review", input={"source": "C"}, resources={"walltime_s": 1})
out["lsf_walltime_mapping"] = {"request_walltime_s": 1,
    "derived_lsf_walltime_with_config_24h": derive_lsf_resources(spec, walltime="24:00")[3]}
envelope = _envelope()
receipt = _receipt(envelope)
current = _verify(envelope, receipt)
later = _verify(envelope, receipt, current_attempt=receipt.attempt + 1)
assert current.accepted and not later.accepted
out["receipt_verifier_scope"] = {"same_attempt_accepted": current.accepted,
    "after_job_attempt_increment_accepted": later.accepted, "reason": later.code,
    "interpretation": "Correct state-transition behavior; do not reuse with current-job context to reject all historical usage."}

Path(__file__).with_name("assumptions.json").write_text(json.dumps(out, indent=2) + "\n")
print(json.dumps(out, indent=2))
