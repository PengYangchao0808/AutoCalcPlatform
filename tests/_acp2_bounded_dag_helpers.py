"""Shared helpers for the M2-04 bounded-recipe DAG test families (never collected).

The helpers build a **real** compiled recipe definition (CCCP is the only
identity authority), a frozen plan payload, a fake CCCP runner that returns typed
``TaskResult`` objects, and a fake execution right.  No QC binary is ever
executed and no jobs DB is opened.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from acp_services.contracts.envelope import ExecutionEnvelope
from acp_services.runtime.execution_quota import reserve_worst_case
from acp_services.runtime.execution_right import (
    ExecutionRightState,
    ExecutionRightVerdict,
)
from acp_services.runtime.step_dag import BoundedStepDag, StepSpec
from acp_services.runtime.step_dag_worker import BOUNDED_RECIPE_PAYLOAD_KEY
from acp_services.runtime.step_definition import (
    FROZEN_STEP_DAG_KEY,
    compile_step_dag_definition,
    output_ref_address,
)
from acp_services.runtime.worker import FrozenPlan, FrozenPlanIdentity, FrozenStep
from cccp.calculation.contracts import ArtifactRef, Provenance
from cccp.calculation.requests import TaskKind
from cccp.calculation.results import FrequencyPayload, TaskResult

PLAN_IDENTITY = "v2:plan-identity-m204"
APPROVAL_DIGEST = "sha256:" + "a" * 64
STRUCTURE = "3\nwater\nO 0 0 0\nH 0 0 1\nH 1 0 0\n"
WHOLE = {"selector_kind": "whole_product"}


def ref(from_step: str, product_kind: str) -> dict[str, Any]:
    return {
        "from_step": from_step,
        "product_kind": product_kind,
        "selector": dict(WHOLE),
        "required": True,
    }


def recipe_payload(
    steps: list[dict[str, Any]] | None = None,
    *,
    recipe_id: str = "recipe.bounded.relax_freq.v1",
) -> dict[str, Any]:
    """Return a canonical two-step (optimize -> frequency) recipe payload."""
    if steps is None:
        steps = [
            {
                "step_id": "relax",
                "intent": "relax the frozen guess",
                "required": True,
                "diagnostic_only": False,
                "task_kind": "optimize",
                "parameters": {},
                "consumes": [],
                "produces": [ref("relax", "geometry")],
                "validators": [],
                "alternatives": [],
            },
            {
                "step_id": "freq",
                "intent": "verify the relaxed structure",
                "required": True,
                "diagnostic_only": False,
                "task_kind": "frequency",
                "parameters": {},
                "consumes": [ref("relax", "geometry")],
                "produces": [ref("freq", "frequencies")],
                "validators": [],
                "alternatives": [],
            },
        ]
    edges = [
        {
            "edge_id": f"{item['step_id']}_dep_{index}",
            "from_step": dependency["from_step"],
            "to_step": item["step_id"],
            "output_ref": dict(dependency),
        }
        for item in steps
        for index, dependency in enumerate(item.get("consumes") or ())
    ]
    return {
        "schema_version": 1,
        "recipe_id": recipe_id,
        "applicability": {"supported_reaction_classes": [], "preconditions": []},
        "limits": {"max_steps": 8, "max_edges": 8},
        "steps": steps,
        "edges": edges,
        "validators": [],
        "alternatives": [],
    }


def build_definition(
    work_dir: Path,
    *,
    recipe: dict[str, Any] | None = None,
    task_ref: str = "20260101_010_BoundedRecipe",
    attempt: int = 1,
    step_definition_identities: tuple[str, ...] = (),
    approval_plan_digest: str = "",
) -> tuple[BoundedStepDag, Any, dict[str, Any]]:
    """Compile the recipe at freeze time, reserve the worst case, return the plan."""
    work_dir.mkdir(parents=True, exist_ok=True)
    structure = work_dir / "input.xyz"
    structure.write_text(STRUCTURE, encoding="utf-8")
    payload = recipe or recipe_payload()
    dag, definition = compile_step_dag_definition(
        payload,
        {},
        task_ref=task_ref,
        approval_plan_digest=approval_plan_digest,
        step_definition_identities=step_definition_identities,
    )
    reservation = reserve_worst_case(
        work_dir,
        reservation_id=f"{task_ref}-a{attempt}",
        task_ref=task_ref,
        attempt=attempt,
        plan_definition_digest=definition.plan_definition_digest,
        worst_case=dict(definition.worst_case_executions),
    )
    plan_payload = {
        FROZEN_STEP_DAG_KEY: dag.to_frozen_payload(),
        BOUNDED_RECIPE_PAYLOAD_KEY: {
            "structure_path": structure.name,
            "structure_role": "minimum",
            "structure_digest": "sha256:" + hashlib.sha256(STRUCTURE.encode()).hexdigest(),
            "step_requirements": [
                {"step_id": "relax", "executables": ["orca"], "require_version": True}
            ],
            "approval_plan_digest": approval_plan_digest,
            "reservation": reservation.to_dict(),
            "settings": {
                "charge": 0,
                "multiplicity": 1,
                "method": {"method": "r2SCAN-3c"},
            },
        },
    }
    return dag, definition, plan_payload


def frozen_plan(plan_payload: dict[str, Any], *, attempt: int = 1) -> FrozenPlan:
    """Return the frozen plan whose identity is the v2 body digest."""
    from acp_services.runtime.worker import frozen_plan_body_digest

    body = FrozenPlan(
        payload=plan_payload,
        steps=tuple(
            FrozenStep(kind=str(step["kind"]), payload={"step_id": step["step_id"]})
            for step in plan_payload[FROZEN_STEP_DAG_KEY]["steps"]
        ),
        item_symbols=("O", "H", "H"),
    )
    return FrozenPlan(
        payload=body.payload,
        steps=body.steps,
        item_symbols=body.item_symbols,
        identity=FrozenPlanIdentity(
            plan_digest=frozen_plan_body_digest(body),
            plan_identity=PLAN_IDENTITY,
            step_identities=tuple(
                plan_payload[FROZEN_STEP_DAG_KEY]["definition"]["step_definition_identities"]
            ),
        ),
    )


def envelope(
    *,
    attempt: int = 1,
    owner: str = "worker-1",
    fencing: str = "fence-1",
    job_id: str = "20260101_010_BoundedRecipe",
) -> ExecutionEnvelope:
    from acp_services.contracts.envelope import expected_submission_id

    return ExecutionEnvelope(
        envelope_version=1,
        job_id=job_id,
        attempt=attempt,
        submission_id=expected_submission_id(job_id, attempt),
        release_id="rel-m204",
        release_digest="sha256:" + "0" * 64,
        owner=owner,
        fencing_token=fencing,
        plan_digest="sha256:" + "1" * 64,
        plan_identity=PLAN_IDENTITY,
        protocol="local",
        entrypoint="worker.bootstrap",
        required_capabilities=("optimize", "frequency"),
    )


def provenance() -> Provenance:
    return Provenance(backend="orca", method="r2SCAN-3c", version="0.1", input_signature="sig")


def optimize_result(
    out_dir: Path,
    *,
    complete: bool = True,
    status: str = "completed",
    marker: str = "optimized",
) -> TaskResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact = out_dir / "final.xyz"
    artifact.write_text(STRUCTURE.replace("water", marker), encoding="utf-8")
    return TaskResult(
        task=TaskKind.OPTIMIZE,
        status=status,
        complete=complete,
        converged=complete,
        coordinates=((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
        symbols=("O", "H", "H"),
        artifacts=(ArtifactRef(path=artifact, type="geometry"),),
        provenance=provenance(),
    )


def frequency_result(
    out_dir: Path, *, complete: bool = True, status: str = "completed"
) -> TaskResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact = out_dir / "freq.json"
    artifact.write_text('{"n_imaginary": 0}', encoding="utf-8")
    return TaskResult(
        task=TaskKind.FREQUENCY,
        status=status,
        complete=complete,
        frequencies=(1600.0, 3700.0, 3800.0),
        artifacts=(ArtifactRef(path=artifact, type="frequencies"),),
        payload=FrequencyPayload(n_imaginary=0),
        provenance=provenance(),
    )


def fake_runner(*, record: dict[str, Any] | None = None, overrides: dict[str, Any] | None = None):
    """Return a runner factory that never executes QC."""
    calls: list[dict[str, Any]] = []

    def factory(entrypoint: str):
        def run(request, *, context=None):
            calls.append(
                {
                    "entrypoint": entrypoint,
                    "task": request.task.value,
                    "output_dir": str(request.output_dir),
                    "structure": str(getattr(request.structure, "path", "") or ""),
                    "role": getattr(request.structure, "role", None),
                    "level": request.level.to_dict(),
                    "config_present": context is not None and context.config is not None,
                    "meter_present": context is not None and context.meter is not None,
                }
            )
            if record is not None:
                record.setdefault("calls", []).append(calls[-1])
            out_dir = Path(request.output_dir)
            override = (overrides or {}).get(request.task.value)
            if override is not None:
                return override(out_dir)
            if request.task is TaskKind.OPTIMIZE:
                return optimize_result(out_dir)
            if request.task is TaskKind.FREQUENCY:
                return frequency_result(out_dir)
            raise AssertionError(f"unexpected task {request.task.value}")

        return run

    factory.calls = calls  # type: ignore[attr-defined]
    return factory


def StepDispatchAdapterFactory(work: Path, *, runner=None):  # noqa: N802 - test factory
    """Return a real :class:`StepDispatchAdapter` bound to *work*."""
    from acp_services.runtime.step_dispatch import (
        StepDispatchAdapter,
        StepInputs,
        StepSettings,
    )

    structure = Path(work) / "input.xyz"
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
        runner_factory=runner or fake_runner(),
        work_root=Path(work),
    )


class FakeExecutionRight:
    """Controllable execution right (``held`` can be flipped mid-flight)."""

    def __init__(self, *, held: bool = True, lose_after: int | None = None) -> None:
        self.held = held
        self.remaining = lose_after
        self.verify_calls = 0

    def acquire(self, envelope: ExecutionEnvelope) -> ExecutionRightVerdict:
        if self.held:
            return ExecutionRightVerdict()
        return ExecutionRightVerdict(ExecutionRightState.NOT_HELD, "another worker holds it")

    def verify(self, envelope: ExecutionEnvelope) -> ExecutionRightVerdict:
        self.verify_calls += 1
        if self.remaining is not None:
            self.remaining -= 1
            if self.remaining < 0:
                self.held = False
        if self.held:
            return ExecutionRightVerdict()
        return ExecutionRightVerdict(ExecutionRightState.LOST, "taken over")


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def step_ids(dag: BoundedStepDag) -> list[str]:
    return [step.step_id for step in dag.steps]


def step_by_id(dag: BoundedStepDag, step_id: str) -> StepSpec:
    return dag.by_id()[step_id]


def upstream_address(from_step: str, product_kind: str) -> str:
    return output_ref_address(from_step, product_kind)
