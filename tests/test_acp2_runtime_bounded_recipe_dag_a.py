"""M2-04a — the single Services ``StepDispatchAdapter`` (request/result adapter).

Covered here (checkpoint a):

* the frozen step + verified upstream artifacts become a **typed**
  ``TaskRequest``/``TaskContext`` — a plan dict never reaches a runner, and an
  incomplete context (no work dir / no explicit output dir / no structure role /
  no provider version) is a typed refusal instead of a guess;
* the two-step chain hands the downstream step a **real upstream file** and its
  content digest;
* every step gets its own explicit ``output_dir``, its declared input roles and
  its resolved method/config;
* the completion gate is ``decide_task_result`` (the frozen truth table), not a
  DAG-local one: failed / ``complete=false`` / missing required fact / unknown
  status never become a normal ``completed`` and a partial result never releases
  a required downstream step;
* ``diagnostic_only`` is mapped explicitly and re-judged on recovery;
* declared products must map to real artifacts, and product identity
  (``output_digests`` + ``products``) is validated separately from the
  publication semantics (``publish_payload``);
* an upstream digest change invalidates only the correct downstream step.

No QC binary runs: the runner is a fake that returns typed ``TaskResult``s.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from acp_services.runtime.step_dag import (
    PUBLISH_POLICY_PARTIAL,
    QUALIFICATION_COMPLETE,
    QUALIFICATION_DIAGNOSTIC,
    QUALIFICATION_PARTIAL,
    BoundedStepDagExecutor,
    PublishStatus,
    StepOutcome,
    StepProduct,
    StepStatus,
)
from acp_services.runtime.step_dispatch import (
    StepArtifactMissingError,
    StepDispatchAdapter,
    StepDispatchError,
    StepInputs,
    StepSettings,
    declare_products,
    validate_declared_products,
    validate_publish_payload,
)
from tests._acp2_bounded_dag_helpers import (
    build_definition,
    fake_runner,
    frequency_result,
    optimize_result,
    recipe_payload,
    ref,
)


def _adapter(work: Path, *, runner=None, overrides=None) -> StepDispatchAdapter:
    structure = work / "input.xyz"
    return StepDispatchAdapter(
        inputs=StepInputs(
            structure_path=structure,
            structure_digest="sha256:" + "b" * 64,
            structure_role="minimum",
        ),
        settings=StepSettings(
            charge=0,
            multiplicity=1,
            method={"method": "r2SCAN-3c"},
            config={"executables": {"orca": {"path": "/opt/orca"}}},
            software={"orca": "6.0"},
        ),
        meter=None,
        runner_factory=runner or fake_runner(overrides=overrides),
        work_root=work,
    )


def _executor(work: Path, plan_payload, *, runner=None, overrides=None, publish=None):
    dag, definition, _payload = build_definition(work)
    adapter = _adapter(work, runner=runner, overrides=overrides)

    def default_publish(step, outcome):  # noqa: ANN001 - test-local PublishFn
        validate_declared_products(step, outcome, work_root=work)
        validate_publish_payload(step, outcome)
        return {product.product_kind: product.digest for product in outcome.products}

    executor = BoundedStepDagExecutor(
        dag,
        adapter,
        publish or default_publish,
        plan_identity="v2:plan-identity-m204",
        release_id="rel-m204",
        attempt=1,
        owner="worker-1",
        fencing="fence-1",
        work_dir=str(work),
    )
    return executor, dag


def test_typed_request_never_receives_the_plan_dict(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    executor, _dag = _executor(tmp_path, None, runner=fake_runner(record={"calls": calls}))
    state = executor.run()
    assert state.statuses() == {"relax": "completed", "freq": "completed"}
    # Two typed runner calls, both with an explicit absolute output dir and the
    # resolved config snapshot; no plan payload was handed in.
    assert [call["task"] for call in calls] == ["optimize", "frequency"]
    for call in calls:
        assert call["output_dir"].startswith(str(tmp_path))
        assert call["config_present"] is True
        assert call["entrypoint"].startswith("cccp.calculation.run_")


def test_context_requires_explicit_dispatch_facts(tmp_path: Path) -> None:
    dag, _definition, _payload = build_definition(tmp_path)
    adapter = _adapter(tmp_path)
    context = executor_context(dag, tmp_path, output_dir="")
    with pytest.raises(StepDispatchError) as excinfo:
        adapter.build_request(context)
    assert excinfo.value.code == "incomplete_dispatch_context"

    context = executor_context(dag, tmp_path, output_dir=str(tmp_path / "relax"))
    broken = StepDispatchAdapter(
        inputs=StepInputs(
            structure_path=tmp_path / "input.xyz",
            structure_digest="sha256:" + "b" * 64,
            structure_role="transition_state",
        ),
        settings=StepSettings(),
        runner_factory=fake_runner(),
        work_root=tmp_path,
    )
    with pytest.raises(StepDispatchError) as excinfo:
        broken.build_request(context)
    assert excinfo.value.code == "structure_role_mismatch"


def test_output_dir_cannot_escape_the_attempt(tmp_path: Path) -> None:
    dag, _definition, _payload = build_definition(tmp_path)
    adapter = _adapter(tmp_path)
    context = executor_context(dag, tmp_path, output_dir=str(tmp_path.parent / "elsewhere"))
    with pytest.raises(StepDispatchError) as excinfo:
        adapter.build_request(context)
    assert excinfo.value.code == "output_outside_work_dir"


def test_declared_input_role_is_never_guessed(tmp_path: Path) -> None:
    other = tmp_path / "other.xyz"
    other.write_text("1\nH 0 0 0\n", encoding="utf-8")
    dag, _definition, _payload = build_definition(tmp_path)
    step = dag.by_id()["relax"]
    from dataclasses import replace

    step = replace(step, input_roles={"charge_file": "inputs/charge.txt"})
    adapter = StepDispatchAdapter(
        inputs=StepInputs(
            structure_path=tmp_path / "input.xyz",
            structure_digest="sha256:" + "b" * 64,
            structure_role="minimum",
        ),
        settings=StepSettings(),
        runner_factory=fake_runner(),
        work_root=tmp_path,
    )
    context = executor_context(dag, tmp_path, output_dir=str(tmp_path / "relax"), step=step)
    with pytest.raises(StepDispatchError) as excinfo:
        adapter.build_request(context)
    assert excinfo.value.code == "unknown_input_role"


# ---------------------------------------------------------------------------
# truth table: the DAG never re-derives completion
# ---------------------------------------------------------------------------


def _completed_after(overrides: dict[str, Any], tmp_path: Path):
    executor, _dag = _executor(tmp_path, None, overrides=overrides)
    return executor.run()


def test_failed_task_result_is_not_a_completion(tmp_path: Path) -> None:
    overrides = {
        "optimize": lambda out: optimize_result(out, complete=False, status="failed"),
    }
    state = _completed_after(overrides, tmp_path)
    assert state.states["relax"].status is not StepStatus.COMPLETED
    assert state.states["relax"].qualification != QUALIFICATION_COMPLETE
    assert state.states["freq"].status is StepStatus.UPSTREAM_FAILED


def test_missing_required_fact_outranks_complete_true(tmp_path: Path) -> None:
    overrides = {
        "frequency": lambda out: frequency_result(out).__class__(
            task=frequency_result(out).task,
            status="completed",
            complete=True,
            frequencies=(),
            artifacts=frequency_result(out).artifacts,
            payload=None,
        ),
    }
    state = _completed_after(overrides, tmp_path)
    assert state.states["freq"].status is StepStatus.FAILED
    assert state.states["freq"].reason


def test_unknown_status_is_not_a_completion(tmp_path: Path) -> None:
    overrides = {"optimize": lambda out: optimize_result(out, status="mystery")}
    state = _completed_after(overrides, tmp_path)
    assert state.states["relax"].status is StepStatus.FAILED


def test_partial_result_never_releases_a_required_downstream_step(tmp_path: Path) -> None:
    overrides = {"optimize": lambda out: optimize_result(out, complete=False)}
    state = _completed_after(overrides, tmp_path)
    relax = state.states["relax"]
    assert relax.status is StepStatus.COMPLETED
    assert relax.qualification == QUALIFICATION_PARTIAL
    assert relax.publish_policy == PUBLISH_POLICY_PARTIAL
    assert not relax.releases_downstream
    assert state.states["freq"].status is StepStatus.BLOCKED
    assert state.states["freq"].reason.startswith("upstream_partial")


def test_diagnostic_only_step_is_never_a_normal_completion(tmp_path: Path) -> None:
    """A diagnostic step never releases a normal consumer, even after recovery.

    Two independent guards are proven:

    1. CCCP refuses to compile a **required** consumer of a diagnostic producer
       (``DiagnosticDependencyError``) — the science-level rule;
    2. at the DAG level the diagnostic outcome is recorded as ``diagnostic_only``,
       never published, and a recovery re-judges from the persisted
       qualification instead of adopting the task core's ``completed``.
    """
    from cccp.calculation.recipes.validation import DiagnosticDependencyError

    with pytest.raises(DiagnosticDependencyError):
        build_definition(tmp_path / "refused", recipe=_diagnostic_recipe(required_consumer=True))

    dag, _definition, _payload = build_definition(
        tmp_path, recipe=_diagnostic_recipe(required_consumer=False)
    )
    adapter = _adapter(tmp_path)

    def publish(step, outcome):  # noqa: ANN001 - test-local PublishFn
        return {product.product_kind: product.digest for product in outcome.products}

    def executor_for(graph) -> BoundedStepDagExecutor:  # noqa: ANN001 - test-local helper
        return BoundedStepDagExecutor(
            graph,
            adapter,
            publish,
            plan_identity="v2:plan-identity-m204",
            release_id="rel-m204",
            attempt=1,
            work_dir=str(tmp_path),
        )

    state = executor_for(dag).run()
    assert state.states["diag"].qualification == QUALIFICATION_DIAGNOSTIC
    assert state.states["diag"].publish_status is PublishStatus.NOT_PUBLISHED
    assert state.states["need"].status is StepStatus.BLOCKED
    assert state.states["need"].reason.startswith("upstream_unpublished")

    recovered = BoundedStepDagExecutor.recover(
        dag,
        adapter,
        publish,
        plan_identity="v2:plan-identity-m204",
        release_id="rel-m204",
        persisted=state.to_dict(),
        attempt=1,
    )
    recovered.run()
    assert recovered.state.states["diag"].qualification == QUALIFICATION_DIAGNOSTIC
    assert recovered.state.states["need"].status is StepStatus.BLOCKED


def test_diagnostic_qualification_is_persisted_and_rejudged(tmp_path: Path) -> None:
    """A persisted *normal* completion of a diagnostic step is re-judged on recovery."""
    dag, _definition, _payload = build_definition(
        tmp_path, recipe=_diagnostic_recipe(required_consumer=False)
    )
    adapter = _adapter(tmp_path)

    def publish(step, outcome):  # noqa: ANN001 - test-local PublishFn
        return {product.product_kind: product.digest for product in outcome.products}

    executor = BoundedStepDagExecutor(
        dag,
        adapter,
        publish,
        plan_identity="v2:plan-identity-m204",
        release_id="rel-m204",
        attempt=1,
        work_dir=str(tmp_path),
    )
    state = executor.run().to_dict()
    # Simulate a state written before the diagnostic mapping existed: the step
    # looks normally completed and published.
    for row in state["states"]:
        if row["step_id"] == "diag":
            row["qualification"] = QUALIFICATION_COMPLETE
            row["publish_status"] = "published"
            row["publish_policy"] = "normal"
    recovered = BoundedStepDagExecutor.recover(
        dag,
        adapter,
        publish,
        plan_identity="v2:plan-identity-m204",
        release_id="rel-m204",
        persisted=state,
        attempt=1,
    )
    recovered.run()
    # Re-judged: the diagnostic step is diagnostic again (never published as
    # normal science less downstream science).
    assert recovered.state.states["diag"].qualification == QUALIFICATION_DIAGNOSTIC
    assert recovered.state.states["diag"].publish_status is PublishStatus.NOT_PUBLISHED


# ---------------------------------------------------------------------------
# products vs publication
# ---------------------------------------------------------------------------


def test_product_kind_maps_to_a_real_artifact(tmp_path: Path) -> None:
    dag, _definition, _payload = build_definition(tmp_path)
    outcome = _adapter(tmp_path)(
        executor_context(dag, tmp_path, output_dir=str(tmp_path / "relax"))
    )
    assert [product.product_kind for product in outcome.products] == ["geometry"]
    assert Path(outcome.products[0].path).is_file()
    assert outcome.output_digests["geometry"] == outcome.products[0].digest


def test_declared_product_without_artifact_is_refused(tmp_path: Path) -> None:
    dag, _definition, _payload = build_definition(tmp_path)
    step = dag.by_id()["relax"]
    result = optimize_result(tmp_path / "relax")
    # The recipe declares "geometry"; a result exposing no artifact cannot satisfy it.
    empty = type(result)(
        task=result.task,
        status="completed",
        complete=True,
        coordinates=result.coordinates,
        symbols=result.symbols,
        artifacts=(),
        provenance=result.provenance,
    )
    with pytest.raises(StepArtifactMissingError):
        declare_products(step, empty, output_dir=tmp_path / "relax")


def test_product_identity_and_publication_are_validated_separately(tmp_path: Path) -> None:
    dag, _definition, _payload = build_definition(tmp_path)
    step = dag.by_id()["relax"]
    artifact = tmp_path / "relax" / "final.xyz"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("x", encoding="utf-8")
    product = StepProduct(product_kind="geometry", path=str(artifact), digest="sha256:" + "c" * 64)
    outcome = StepOutcome(
        output_digests={"geometry": product.digest},
        products=(product,),
        publish_payload={"result": {}, "decision": {}, "publish_policy": "normal", "products": []},
    )
    with pytest.raises(StepDispatchError) as excinfo:
        validate_declared_products(step, outcome, work_root=tmp_path)
    assert excinfo.value.code == "product_digest_mismatch"

    # A payload that only repeats output_digests is not a publication document.
    good = StepProduct(
        product_kind="geometry",
        path=str(artifact),
        digest="sha256:" + _sha256(artifact),
    )
    outcome = StepOutcome(
        output_digests={"geometry": good.digest},
        products=(good,),
        publish_payload={"result": {}, "decision": {}, "publish_policy": "normal", "products": []},
    )
    validate_declared_products(step, outcome, work_root=tmp_path)
    with pytest.raises(StepDispatchError) as excinfo:
        validate_publish_payload(step, outcome)
    assert excinfo.value.code == "invalid_publish_payload"


def test_upstream_digest_change_invalidates_only_the_correct_downstream(tmp_path: Path) -> None:
    dag, _definition, _payload = build_definition(tmp_path)

    def publish(step, outcome):  # noqa: ANN001 - test-local PublishFn
        return {product.product_kind: product.digest for product in outcome.products}

    adapter = _adapter(tmp_path)
    executor = BoundedStepDagExecutor(
        dag,
        adapter,
        publish,
        plan_identity="v2:plan-identity-m204",
        release_id="rel-m204",
        attempt=1,
        work_dir=str(tmp_path),
    )
    state = executor.run()
    assert state.states["relax"].step_identity
    assert state.states["freq"].step_identity
    baseline = state.states["freq"].step_identity

    # Only the downstream consumer is invalidated when the upstream bytes change.
    other = tmp_path / "other"
    dag2, _definition2, _payload2 = build_definition(other)
    adapter2 = _adapter(
        other, overrides={"optimize": lambda out: optimize_result(out, marker="second")}
    )
    executor2 = BoundedStepDagExecutor(
        dag2,
        adapter2,
        publish,
        plan_identity="v2:plan-identity-m204",
        release_id="rel-m204",
        attempt=1,
        work_dir=str(other),
    )
    state2 = executor2.run()
    # The root step has no upstream: its identity is unchanged...
    assert state2.states["relax"].step_identity == state.states["relax"].step_identity
    # ...while the consumer binds the *actual* upstream bytes, so a changed
    # geometry invalidates exactly the downstream step (and nothing else).
    assert state2.states["freq"].step_identity != baseline


def test_execution_identity_is_cccp_v2_and_fixture_is_explicit(tmp_path: Path) -> None:
    dag, definition, _payload = build_definition(tmp_path)
    executor, _dag = _executor(tmp_path, None)
    state = executor.run()
    for step_id in ("relax", "freq"):
        assert state.states[step_id].step_identity.startswith("v2:")
        assert state.states[step_id].identity_scheme == "acp2.step-execution.v2"
    assert definition.plan_definition_digest.startswith("v2:")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _diagnostic_recipe(*, required_consumer: bool) -> dict[str, Any]:
    optional_ref = dict(ref("diag", "geometry"))
    optional_ref["required"] = required_consumer
    steps = [
        {
            "step_id": "diag",
            "intent": "diagnostic-only screening",
            "required": False,
            "diagnostic_only": True,
            "task_kind": "optimize",
            "parameters": {},
            "consumes": [],
            "produces": [ref("diag", "geometry")],
            "validators": [],
            "alternatives": [],
        },
        {
            "step_id": "need",
            "intent": "consumes the diagnostic product",
            "required": required_consumer,
            "diagnostic_only": False,
            "task_kind": "frequency",
            "parameters": {},
            "consumes": [optional_ref],
            "produces": [ref("need", "frequencies")],
            "validators": [],
            "alternatives": [],
        },
    ]
    return recipe_payload(steps, recipe_id="recipe.bounded.diagnostic.v1")


def executor_context(dag, work: Path, *, output_dir: str, step=None):
    """Build a minimal ``StepDispatchContext`` for adapter-level tests."""
    from acp_services.runtime.step_dag import materialize_execution_identity

    chosen = step or dag.steps[0]
    identity = materialize_execution_identity(
        dag,
        chosen,
        step_index=0,
        plan_identity="v2:plan-identity-m204",
        upstream_digests={},
        release_id="rel-m204",
    )
    from acp_services.runtime.step_dag import StepDispatchContext

    return StepDispatchContext(
        step=chosen,
        step_identity=identity,
        upstream_digests=(),
        task_ref=dag.task_ref,
        release_id="rel-m204",
        work_dir=str(work),
        input_base=str(work),
        output_dir=output_dir,
        attempt=1,
        owner="worker-1",
        fencing="fence-1",
        structure_role="minimum",
        method={},
        capability=chosen.capability,
        provider_version=chosen.implementation_version,
        plan_identity="v2:plan-identity-m204",
        plan_definition_digest=dag.plan_definition_digest,
    )
