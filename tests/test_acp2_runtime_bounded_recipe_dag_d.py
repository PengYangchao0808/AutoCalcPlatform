"""M2-04d — public entry, capability field and publication seam.

Covered here (checkpoint d):

* ``acp_services.worker_bootstrap`` injects the DAG ``TaskExecutor`` and one
  ``TaskRef``/``attempt`` executes **many** steps (a real worker assembly from
  the frozen group, no DB, no QC);
* the new recipe workflow is ``closure="integrated"``, joins
  ``DEFAULT_ENVELOPE_WORKFLOWS``, carries the additive
  ``requires_envelope`` field, and a missing/bad frozen group is a **typed
  error** — the attempt protocol never records a legacy fallback;
* ``acp.calculations.attempt_freeze.FREEZE_BUILDERS`` produces the spec and the
  Services ``write_frozen_attempt`` writes the group (no ACP-side writer);
* the ``acp.calculations.primitives`` publication contract is retained and is
  the only implementation of the ``normal_modes`` publication half;
* ``ExecutionResourcePrecheck`` blocks before dispatch and
  ``step_requirements`` never pretends to be a binary check;
* ADR-008 declares the DA step chain and keeps the unmapped rows ``OPEN``;
* the B18 static/simulated proof stays fixture evidence (never production).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from acp_services.runtime import execution_chain as chain
from acp_services.runtime.attempt_freeze import write_frozen_attempt
from acp_services.runtime.execution_chain import (
    DEFAULT_ENVELOPE_WORKFLOWS,
    RECIPE_WORKFLOWS,
    EnvelopeRequiredError,
    resolve_attempt_choice,
    select_execution_entry,
)
from acp_services.runtime.resource_precheck import ExecutionResourcePrecheck
from acp_services.runtime.step_dag_worker import (
    BoundedRecipePlan,
    StepDagTaskExecutor,
)
from acp_services.runtime.step_definition import (
    ADR008_REQUIRED_STEPS,
    adr008_step_declaration,
)
from acp_services.runtime.workflow_enablement import (
    ACTIVE_WORKFLOWS,
    enablement_for,
    require_envelope_entry,
)
from acp_services.worker_bootstrap import assemble_worker
from tests._acp2_bounded_dag_helpers import (
    FakeExecutionRight,
    build_definition,
    envelope,
    fake_runner,
    frozen_plan,
    recipe_payload,
)

M204_RECIPE = recipe_payload()


# ---------------------------------------------------------------------------
# workflow capability + attempt protocol
# ---------------------------------------------------------------------------


def test_recipe_workflow_is_integrated_and_envelope_only() -> None:
    capability = chain.capability_for("BoundedRecipe")
    assert capability.closure == "integrated"
    assert capability.requires_envelope is True
    assert capability.capabilities == ("singlepoint", "optimize", "frequency", "irc")
    assert "BoundedRecipe" in RECIPE_WORKFLOWS
    assert "BoundedRecipe" in DEFAULT_ENVELOPE_WORKFLOWS
    # The recipe workflow is not a 16th catalog workflow.
    assert "BoundedRecipe" not in ACTIVE_WORKFLOWS
    row = capability.to_dict()
    assert row["requires_envelope"] is True


def test_missing_frozen_group_is_a_typed_error_not_a_legacy_fallback() -> None:
    with pytest.raises(EnvelopeRequiredError) as excinfo:
        select_execution_entry("BoundedRecipe", remote=False, envelope_available=False)
    assert excinfo.value.reason == "frozen_group_missing"
    with pytest.raises(EnvelopeRequiredError):
        select_execution_entry("BoundedRecipe", remote=True, envelope_available=False)
    selection = select_execution_entry("BoundedRecipe", remote=False, envelope_available=True)
    assert selection.entry == chain.EXECUTION_ENTRY_ENVELOPE


def test_attempt_protocol_records_envelope_and_never_legacy() -> None:
    choice = resolve_attempt_choice(
        workflow="BoundedRecipe",
        attempt=1,
        remote=False,
        envelope_available=True,
        release_id="rel-m204",
    )
    assert choice.entry == chain.EXECUTION_ENTRY_ENVELOPE
    # A resumed attempt reuses the recorded choice verbatim.
    resumed = resolve_attempt_choice(
        workflow="BoundedRecipe",
        attempt=1,
        remote=False,
        envelope_available=False,
        previous=choice,
    )
    assert resumed.entry == chain.EXECUTION_ENTRY_ENVELOPE
    assert resumed == choice


def test_enablement_policy_refuses_the_recipe_workflow_explicitly() -> None:
    # The enablement matrix stays the catalog scope; the recipe workflow is
    # envelope-only and is not silently added as an enabled catalog row.
    with pytest.raises(KeyError):
        enablement_for("BoundedRecipe")
    assert require_envelope_entry("BoundedRecipe") == "envelope"


# ---------------------------------------------------------------------------
# freeze builder -> Services writer
# ---------------------------------------------------------------------------


def _record(tmp_path: Path, *, workflow: str = "BoundedRecipe") -> SimpleNamespace:
    return SimpleNamespace(
        id="20260101_010_BoundedRecipe",
        attempt=1,
        spec=SimpleNamespace(
            workflow=workflow,
            input={
                "bounded_recipe": {
                    "recipe": M204_RECIPE,
                    "inputs": {},
                    "structure_role": "minimum",
                    "step_requirements": [
                        {"step_id": "relax", "executables": ["env"], "require_version": False}
                    ],
                }
            },
        ),
    )


def _release() -> SimpleNamespace:
    return SimpleNamespace(
        unversioned=True,
        release_id="unversioned-shared",
        release_digest="",
        assert_current=lambda: None,
    )


def test_freeze_builder_registers_the_recipe_workflow() -> None:
    from acp.calculations import attempt_freeze as acp_freeze

    assert set(acp_freeze.FREEZE_BUILDERS) >= {"BoundedRecipe"}
    assert acp_freeze.BOUNDED_RECIPE_WORKFLOW == "BoundedRecipe"


def test_freeze_builder_spec_is_written_by_services(tmp_path: Path) -> None:
    from acp.calculations import attempt_freeze as acp_freeze

    structure = tmp_path / "in.xyz"
    structure.write_text("3\nwater\nO 0 0 0\nH 0 0 1\nH 1 0 0\n", encoding="utf-8")
    spec = acp_freeze.build_bounded_recipe_freeze_spec(
        _record(tmp_path), tmp_path, structure, _release()
    )
    group = write_frozen_attempt(tmp_path, spec)
    assert group.manifest.workflow == "BoundedRecipe"
    plan = json.loads((tmp_path / "WORK/00_RUNTIME/frozen/plan.json").read_text(encoding="utf-8"))
    assert "bounded_step_dag" in plan["payload"]
    assert plan["payload"]["bounded_step_dag"]["definition"]["identity_scheme"] == (
        "acp2.step-execution.v2"
    )
    assert plan["payload"]["bounded_recipe"]["reservation"]["limits"]["recipe_step"] == 2
    # The frozen envelope declares one entry: the worker, never the legacy CLI.
    assert spec.envelope.entrypoint.startswith("worker.")


def test_freeze_builder_refuses_a_non_recipe_workflow(tmp_path: Path) -> None:
    from acp.calculations import attempt_freeze as acp_freeze

    structure = tmp_path / "in.xyz"
    structure.write_text("3\nwater\nO 0 0 0\nH 0 0 1\nH 1 0 0\n", encoding="utf-8")
    with pytest.raises(acp_freeze.FreezeSpecError):
        acp_freeze.build_bounded_recipe_freeze_spec(
            _record(tmp_path, workflow="singlepoint"), tmp_path, structure, _release()
        )


# ---------------------------------------------------------------------------
# worker assembly: one TaskRef, many steps
# ---------------------------------------------------------------------------


def _frozen_work(tmp_path: Path) -> tuple[Path, SimpleNamespace]:
    """Freeze a real group through the ACP builder and return its work dir."""
    from acp.calculations import attempt_freeze as acp_freeze

    work = tmp_path / "work"
    work.mkdir()
    structure = work / "in.xyz"
    structure.write_text("3\nwater\nO 0 0 0\nH 0 0 1\nH 1 0 0\n", encoding="utf-8")
    spec = acp_freeze.build_bounded_recipe_freeze_spec(_record(work), work, structure, _release())
    write_frozen_attempt(work, spec)
    return work, spec


def test_worker_bootstrap_injects_the_dag_executor(tmp_path: Path) -> None:
    work, spec = _frozen_work(tmp_path)
    runtime = assemble_worker(
        envelope=spec.envelope,
        plan=spec.plan,
        work_dir=work,
        execution_right=FakeExecutionRight(),
    )
    assert isinstance(runtime.worker.executor, StepDagTaskExecutor)
    assert runtime.worker.executor.require_precheck is True


def test_dag_executor_requires_a_precheck_and_reservation(tmp_path: Path) -> None:
    from acp_services.runtime.step_dag import StepDagError

    _dag, _definition, payload = build_definition(tmp_path)
    plan = frozen_plan(payload)
    executor = StepDagTaskExecutor(precheck=None)
    with pytest.raises(StepDagError) as excinfo:
        executor.execute(
            SimpleNamespace(
                plan=plan,
                envelope=envelope(),
                work_dir=tmp_path,
                storage_binding=None,
                execution_right=FakeExecutionRight(),
                control_poll=None,
            )
        )
    assert excinfo.value.code == "missing_resource_precheck"


def test_one_task_ref_executes_many_steps(tmp_path: Path) -> None:
    _dag, _definition, payload = build_definition(tmp_path)
    precheck = ExecutionResourcePrecheck(
        resolver=lambda name, configured: Path("/usr/bin/env"),
        version_probe=lambda name, path: "1.0.0",
    )
    runner = fake_runner()
    executor = StepDagTaskExecutor(precheck=precheck, runner_factory=runner)
    outcome = executor.execute(
        SimpleNamespace(
            plan=frozen_plan(payload),
            envelope=envelope(),
            work_dir=tmp_path,
            storage_binding=None,
            execution_right=FakeExecutionRight(),
            control_poll=None,
        )
    )
    assert outcome.state == "completed"
    assert [call["task"] for call in runner.calls] == ["optimize", "frequency"]
    assert [row["step_id"] for row in outcome.step_execution_chain] == ["relax", "freq"]
    assert outcome.chain_digest.startswith("sha256:")
    for row in outcome.step_execution_chain:
        assert row["step_identity"].startswith("v2:")
    # One attempt, one receipt, no second platform task.
    state = json.loads(
        (tmp_path / "WORK/00_RUNTIME/step_dag_state.json").read_text(encoding="utf-8")
    )
    assert state["task_ref"] == "20260101_010_BoundedRecipe"


def test_plan_binding_requires_the_structure_and_role(tmp_path: Path) -> None:
    from acp_services.runtime.step_dag import StepDagError

    _dag, _definition, payload = build_definition(tmp_path)
    payload["bounded_recipe"].pop("structure_role")
    with pytest.raises(StepDagError) as excinfo:
        BoundedRecipePlan.from_plan_payload(payload, work_dir=tmp_path)
    assert excinfo.value.code == "incomplete_recipe_binding"


# ---------------------------------------------------------------------------
# ACP publication contract retained
# ---------------------------------------------------------------------------


def test_primitives_publication_contract_is_retained() -> None:
    import acp.calculations.primitives as primitives
    import acp.calculations.primitives.frequency as frequency

    assert callable(primitives.run_singlepoint)
    assert callable(primitives.run_frequency)
    assert callable(frequency.publish_normal_modes)
    # The publication half is one implementation: the public entry delegates to
    # the module's private product writer (no copy in Services).
    source = Path(frequency.__file__).read_text(encoding="utf-8")
    assert "_publish_normal_modes(" in source
    assert "build_normal_modes_product" in source


def test_acp_step_publication_applies_the_frequency_half(tmp_path: Path) -> None:
    """The retained ACP publication half really produces ``normal_modes.json``."""
    from acp.calculations.step_publication import publish_bounded_step_products
    from cccp.calculation.requests import TaskKind
    from cccp.calculation.results import FrequencyAnalysis, FrequencyPayload, TaskResult

    step_dir = tmp_path / "WORK/00_RUNTIME/steps/freq"
    step_dir.mkdir(parents=True)
    artifact = step_dir / "freq.json"
    artifact.write_text('{"n_imaginary": 0}', encoding="utf-8")
    analysis = FrequencyAnalysis(
        frequencies=(1600.0,),
        mode_frequencies={6: 1600.0},
        mode_vectors={
            6: (
                (0.1, 0.0, 0.0),
                (0.0, 0.1, 0.0),
                (0.0, 0.0, 0.1),
            )
        },
        mode_ir_intensities={6: 12.5},
    )
    result = TaskResult(
        task=TaskKind.FREQUENCY,
        status="completed",
        complete=True,
        frequencies=(1600.0,),
        symbols=("O", "H", "H"),
        payload=FrequencyPayload(n_imaginary=0, analysis=analysis),
    )
    document = {
        "schema": "acp2.runtime.step-result.v1",
        "step_id": "freq",
        "status": "completed",
        "products": [
            {
                "product_kind": "frequencies",
                "path": str(artifact),
                "digest": "sha256:" + "0" * 64,
                "size_bytes": artifact.stat().st_size,
            }
        ],
        "publish_payload": {"result": result.to_dict()},
    }
    (step_dir / "step_result.json").write_text(json.dumps(document), encoding="utf-8")
    publication = publish_bounded_step_products(
        work_dir=tmp_path, result_dir=tmp_path / "RESULT", step_ids=("freq",)
    )
    assert publication.products[0].product_kind == "frequencies"
    assert (tmp_path / "RESULT/steps/freq/freq.json").is_file()
    # The ACP primitive (not Services) wrote the product.
    assert "normal_modes" in publication.applied
    product = json.loads((tmp_path / "RESULT/steps/freq/normal_modes.json").read_text())
    assert product["modes"][0]["mode_index"] == 6


# ---------------------------------------------------------------------------
# ADR-008 declaration
# ---------------------------------------------------------------------------


def test_adr008_declaration_keeps_unmapped_steps_open() -> None:
    declaration = adr008_step_declaration()
    assert declaration["adr"] == "ADR-008"
    assert len(declaration["steps"]) == 9
    assert declaration["complete"] is False
    assert set(declaration["open"]) == {
        "endpoint_prep",
        "candidate_path",
        "energy_thermochemistry",
    }
    mapped = {row.step_id: row for row in ADR008_REQUIRED_STEPS if not row.open}
    assert set(mapped) == {
        "candidate_frequency",
        "ts_optimize",
        "ts_frequency",
        "irc",
        "endpoint_reactants",
        "endpoint_product",
    }
    assert all(row.dag_step_kind for row in mapped.values())
    assert declaration["evidence_chain"]["unmapped_policy"].startswith("OPEN")


def test_adr008_unmapped_task_kinds_are_refused_not_faked() -> None:
    """A recipe whose step cannot be dispatched is refused, never silently dropped."""
    from acp_services.runtime.step_dag import StepDagError
    from acp_services.runtime.step_definition import compile_step_dag_definition

    recipe = recipe_payload(
        [
            {
                "step_id": "endpoint_prep",
                "intent": "conformer search",
                "required": True,
                "diagnostic_only": False,
                "task_kind": "conformer_search",
                "parameters": {},
                "consumes": [],
                "produces": [],
                "validators": [],
                "alternatives": [],
            }
        ],
        recipe_id="recipe.da.partial.v1",
    )
    with pytest.raises(StepDagError) as excinfo:
        compile_step_dag_definition(recipe, {}, task_ref="task-x")
    assert excinfo.value.code == "unsupported_step_kind"


# ---------------------------------------------------------------------------
# B18 static proof stays fixture evidence
# ---------------------------------------------------------------------------


def test_b18_static_dag_is_fixture_only() -> None:
    from acp_services.runtime.step_dag import BoundedStepDag, ScienceStepKind, StepSpec

    static = BoundedStepDag(
        recipe_id="recipe.da.static.v1",
        task_ref="task-b18-static",
        steps=(
            StepSpec(
                step_id="ts_opt",
                kind=ScienceStepKind.OPTIMIZE,
                intent="STATIC/SIMULATED",
                produces=(),
                implementation_version="cccp-opt-static-1",
            ),
        ),
    )
    assert static.has_definition is False
    assert static.plan_definition_digest == ""
    with pytest.raises(Exception):
        static.require_definition()


def test_no_jobs_database_in_the_worker_path() -> None:
    import acp_services.worker_bootstrap as worker_bootstrap

    source = Path(worker_bootstrap.__file__).read_text(encoding="utf-8")
    assert "JobStore" not in source
    assert "sqlite3" not in source
    assert "import acp" not in source
