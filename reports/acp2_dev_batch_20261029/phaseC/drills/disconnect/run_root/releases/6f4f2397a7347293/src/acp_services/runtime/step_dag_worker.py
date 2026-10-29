"""M2-04d — the worker-side DAG ``TaskExecutorPort`` (one attempt, many steps).

``acp_services.worker_bootstrap`` injects :class:`StepDagTaskExecutor` when the
frozen plan carries a bounded step DAG.  One ``TaskRef``/``attempt`` therefore
executes **many** science steps without a second platform task, and the worker
still writes exactly one authoritative receipt.

Responsibilities (and nothing else):

* deserialize the frozen DAG + definition (the worker never converts, never
  re-freezes and never writes back to the envelope);
* run :class:`acp_services.runtime.resource_precheck.ExecutionResourcePrecheck`
  **before** the first dispatch (missing binary/version/asset → ``blocked``);
* bind the persistent quota reservation and the CCCP meter (draw before every
  backend call);
* write every per-step ``step_result.json`` and the ``DagRunState`` through the
  fenced commit port (staging → fence+right → atomic replace);
* return one :class:`~acp_services.runtime.worker.TaskExecutionOutcome` whose
  ``step_execution_chain`` is the additive receipt evidence.

It never publishes a task manifest: the acp ``result_publication`` seam stays
the only task-level publisher.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acp_services.runtime.execution_quota import (
    PersistentQuotaLedger,
    QuotaReservation,
    TaskMeterAdapter,
)
from acp_services.runtime.fenced_commit import (
    CommitFence,
    FencedCommitPort,
    FileFencedCommit,
    assert_production_fence,
)
from acp_services.runtime.receipts import chain_digest_of
from acp_services.runtime.resource_precheck import (
    ExecutionResourcePrecheck,
    ResourceRequirement,
)
from acp_services.runtime.step_dag import (
    PUBLISH_POLICY_NORMAL,
    BoundedStepDag,
    BoundedStepDagExecutor,
    DagRunState,
    StepDagError,
    StepSpec,
    StepStatus,
)
from acp_services.runtime.step_definition import (
    definition_from_frozen_payload,
    verify_plan_digests,
)
from acp_services.runtime.step_dispatch import (
    StepDispatchAdapter,
    StepInputs,
    StepSettings,
    validate_declared_products,
    validate_publish_payload,
)
from acp_services.runtime.worker import (
    TASK_ENVIRONMENT_CONFIG_KEY,
    TaskExecutionOutcome,
    frozen_plan_body_digest,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BOUNDED_RECIPE_PAYLOAD_KEY",
    "STEP_RESULT_FILENAME",
    "BoundedRecipePlan",
    "FileControlPoll",
    "StepDagTaskExecutor",
    "fenced_step_publish",
    "step_result_path",
]

#: Frozen plan payload key carrying the bounded recipe execution binding.
BOUNDED_RECIPE_PAYLOAD_KEY: str = "bounded_recipe"

#: Per-step result document (written through the fenced commit port).
STEP_RESULT_FILENAME: str = "step_result.json"


def step_result_path(step_id: str) -> str:
    """Return the work-dir relative path of one step result document."""
    return f"WORK/00_RUNTIME/steps/{step_id}/{STEP_RESULT_FILENAME}"


@dataclass(frozen=True, slots=True)
class BoundedRecipePlan:
    """The frozen execution binding of one bounded recipe attempt."""

    dag: BoundedStepDag
    structure_path: Path
    structure_role: str
    structure_digest: str
    settings: StepSettings
    requirements: tuple[ResourceRequirement, ...] = ()
    input_roles: Mapping[str, str] = field(default_factory=dict)
    reservation: QuotaReservation | None = None
    #: The approved ``sha256:`` plan digest (verified separately from the
    #: ``v2:`` frozen body digest — neither compensates for the other).
    approval_plan_digest: str = ""
    definition_digests: tuple[str, str] = ("", "")

    @classmethod
    def from_plan_payload(cls, payload: Mapping[str, Any], *, work_dir: Path) -> BoundedRecipePlan:
        """Parse the frozen plan payload (typed refusal when anything is missing)."""
        dag, definition = definition_from_frozen_payload(payload)
        binding = payload.get(BOUNDED_RECIPE_PAYLOAD_KEY)
        if not isinstance(binding, Mapping):
            raise StepDagError(
                "missing_recipe_binding",
                f"the frozen plan carries no {BOUNDED_RECIPE_PAYLOAD_KEY!r} binding",
            )
        structure_path = Path(str(binding.get("structure_path") or ""))
        if not structure_path.is_absolute():
            structure_path = work_dir / structure_path
        if not structure_path.is_file():
            raise StepDagError(
                "missing_structure_input",
                f"the frozen structure {structure_path} is missing",
            )
        role = str(binding.get("structure_role") or "")
        digest = str(binding.get("structure_digest") or "")
        if not role or not digest:
            raise StepDagError(
                "incomplete_recipe_binding",
                "the frozen binding must declare structure_role and structure_digest",
            )
        environment = payload.get(TASK_ENVIRONMENT_CONFIG_KEY)
        raw_settings = binding.get("settings") or {}
        if not isinstance(raw_settings, Mapping):
            raise StepDagError("incomplete_recipe_binding", "settings must be a mapping")
        settings = StepSettings(
            charge=int(raw_settings.get("charge") or 0),
            multiplicity=int(raw_settings.get("multiplicity") or 1),
            method=dict(raw_settings.get("method") or {}),
            backend=raw_settings.get("backend"),
            electronic_state=(
                dict(raw_settings["electronic_state"])
                if isinstance(raw_settings.get("electronic_state"), Mapping)
                else None
            ),
            resources=dict(raw_settings.get("resources") or {}),
            config=(
                dict(environment)
                if isinstance(environment, Mapping) and environment
                else (
                    dict(raw_settings["config"])
                    if isinstance(raw_settings.get("config"), Mapping)
                    else None
                )
            ),
            software=dict(raw_settings.get("software") or {}),
        )
        requirements = tuple(
            ResourceRequirement(
                step_id=str(item.get("step_id") or ""),
                executables=tuple(item.get("executables") or ()),
                require_version=bool(item.get("require_version", False)),
                assets=tuple(item.get("assets") or ()),
                lab_capabilities=tuple(item.get("lab_capabilities") or ()),
            )
            for item in (binding.get("step_requirements") or ())
            if isinstance(item, Mapping)
        )
        roles = binding.get("input_roles")
        return cls(
            dag=dag,
            structure_path=structure_path,
            structure_role=role,
            structure_digest=digest,
            settings=settings,
            requirements=requirements,
            input_roles=dict(roles) if isinstance(roles, Mapping) else {},
            reservation=(
                QuotaReservation.from_dict(binding["reservation"])
                if isinstance(binding.get("reservation"), Mapping)
                else None
            ),
            approval_plan_digest=str(binding.get("approval_plan_digest") or ""),
            definition_digests=(
                str(getattr(definition, "approval_plan_digest", "") or ""),
                str(getattr(definition, "frozen_plan_body_digest", "") or ""),
            ),
        )


def fenced_step_publish(
    commit: FencedCommitPort,
    *,
    work_root: Path,
) -> Any:
    """Return the ``PublishFn`` that validates and records one step's products.

    Publication state is separate from computation state: this only records the
    publication document (through the fenced port) and returns the published
    digests; a failure is retryable without recomputing science.
    """

    def publish(step: StepSpec, outcome: Any) -> Mapping[str, str]:
        validate_declared_products(step, outcome, work_root=work_root)
        validate_publish_payload(step, outcome)
        document = {
            "schema": "acp2.runtime.step-publication.v1",
            "step_id": step.step_id,
            "publish_policy": outcome.publish_policy,
            "products": [product.to_dict() for product in outcome.products],
            "result_digest": outcome.result_digest,
            "execution_identity": outcome.execution_identity,
        }
        staged = commit.stage(f"WORK/00_RUNTIME/steps/{step.step_id}/publication.json", document)
        commit.commit_if_fenced(staged)
        return {product.product_kind: product.digest for product in outcome.products}

    return publish


@dataclass(frozen=True, slots=True)
class FileControlPoll:
    """Poll the attempt's control-intent file before each dispatch (M2-04c).

    The worker never reads the jobs DB: the Controller writes the intent it
    wants propagated into ``WORK/00_RUNTIME/control_intent.json`` and the loop
    re-reads it immediately before every step dispatch.  An unreadable or
    unknown intent is *not* treated as ``continue`` silently — it raises, so a
    corrupted control channel cannot launch another step.
    """

    work_dir: Path
    filename: str = "control_intent.json"

    @property
    def path(self) -> Path:
        return Path(self.work_dir) / "WORK" / "00_RUNTIME" / self.filename

    def __call__(self) -> str:
        target = self.path
        if not target.is_file():
            return "continue"
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StepDagError(
                "unreadable_control_intent", f"{target} cannot be decoded: {exc}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise StepDagError("unreadable_control_intent", f"{target} is not a mapping")
        intent = str(payload.get("intent") or "").lower()
        if intent not in {"pause", "continue", "cancel"}:
            raise StepDagError("unknown_control_intent", f"{target} declares intent {intent!r}")
        return intent


@dataclass(frozen=True, slots=True)
class StepDagTaskExecutor:
    """``TaskExecutorPort`` implementation for a bounded recipe attempt."""

    precheck: ExecutionResourcePrecheck | None = None
    runner_factory: Any | None = None
    require_precheck: bool = True

    def execute(self, scope: Any) -> TaskExecutionOutcome:
        """Run every ready step of the frozen DAG; return one typed outcome."""
        work_dir = Path(scope.work_dir)
        plan = BoundedRecipePlan.from_plan_payload(dict(scope.plan.payload), work_dir=work_dir)
        envelope = scope.envelope
        execution_right = getattr(scope, "execution_right", None)
        if execution_right is None:
            raise StepDagError(
                "missing_execution_right",
                "a production bounded-recipe attempt requires the persistent execution right "
                "(fenced commit and quota binding are meaningless without it)",
            )
        # The transitional always-held default is a test fixture; it must never
        # back an authoritative step/checkpoint commit (M2-04c).
        assert_production_fence(execution_right, seam="acp_services.step_dag_worker")

        self._verify_plan_digests(plan, scope)
        blocked = self._precheck(plan)
        if blocked is not None:
            return blocked

        commit = FileFencedCommit(
            work_dir=work_dir,
            envelope=envelope,
            execution_right=execution_right,
            fence=CommitFence.from_envelope(envelope),
        )
        quota = self._quota(work_dir, plan, envelope)
        dispatch = StepDispatchAdapter(
            inputs=StepInputs(
                structure_path=plan.structure_path,
                structure_digest=plan.structure_digest,
                structure_role=plan.structure_role,
                role_paths={
                    role: work_dir / relative for role, relative in plan.input_roles.items()
                },
            ),
            settings=plan.settings,
            meter=TaskMeterAdapter(ledger=quota) if quota is not None else None,
            runner_factory=self.runner_factory or _default_runner,
            work_root=work_dir,
        )
        executor = BoundedStepDagExecutor(
            plan.dag,
            dispatch,
            fenced_step_publish(commit, work_root=work_dir),
            plan_identity=envelope.plan_identity,
            release_id=envelope.release_id,
            attempt=envelope.attempt,
            owner=envelope.owner,
            fencing=envelope.fencing_token,
            input_digest=plan.structure_digest,
            work_dir=str(work_dir),
            structure_path=str(plan.structure_path),
            input_roles=plan.input_roles,
            method=plan.settings.method,
            resources=plan.settings.resources,
            software=plan.settings.software,
            control=(
                getattr(scope, "control_poll", None)
                if getattr(scope, "control_poll", None) is not None
                else FileControlPoll(work_dir)
            ),
            require_definition=True,
        )
        state = executor.run()
        self._persist_state(commit, state, executor)
        return self._outcome(state, executor)

    # -- helpers ---------------------------------------------------------

    def _verify_plan_digests(self, plan: BoundedRecipePlan, scope: Any) -> None:
        """Verify the approval (sha256:) and frozen body (v2:) digests separately."""
        definition_approval, definition_body = plan.definition_digests
        recomputed_body = frozen_plan_body_digest(scope.plan)
        if definition_body and definition_body != recomputed_body:
            raise StepDagError(
                "body_digest_mismatch",
                f"frozen body digest {definition_body!r} != recomputed {recomputed_body!r}",
            )
        approval = plan.approval_plan_digest or definition_approval
        if approval and definition_approval and approval != definition_approval:
            raise StepDagError(
                "approval_digest_mismatch",
                f"binding approval digest {approval!r} != frozen definition "
                f"{definition_approval!r}",
            )
        if approval:
            verify_plan_digests(
                approval_plan_digest=approval,
                frozen_plan_body_digest=definition_body or recomputed_body,
                expected_approval_plan_digest=approval,
                expected_frozen_plan_body_digest=recomputed_body,
            )

    def _precheck(self, plan: BoundedRecipePlan) -> TaskExecutionOutcome | None:
        if self.precheck is None:
            if self.require_precheck:
                raise StepDagError(
                    "missing_resource_precheck",
                    "a production bounded-recipe attempt requires an "
                    "ExecutionResourcePrecheck (binaries/versions/assets are verified "
                    "before the first dispatch)",
                )
            return None
        result = self.precheck.check(plan.requirements)
        if result.ok:
            return None
        return TaskExecutionOutcome(
            state="blocked",
            result_digest="",
            errors=tuple(
                f"{blocker.code}: {blocker.step_id}: {blocker.detail}"
                for blocker in result.blockers
            ),
        )

    def _quota(
        self, work_dir: Path, plan: BoundedRecipePlan, envelope: Any
    ) -> PersistentQuotaLedger | None:
        reservation = plan.reservation
        if reservation is None:
            raise StepDagError(
                "missing_quota_reservation",
                "a production bounded-recipe attempt requires the persistent worst-case "
                "reservation (allowance is never minted by the worker)",
            )
        if reservation.attempt != envelope.attempt:
            raise StepDagError(
                "quota_reservation_mismatch",
                f"reservation attempt {reservation.attempt} != envelope {envelope.attempt}",
            )
        return PersistentQuotaLedger(work_dir, reservation)

    def _persist_state(
        self,
        commit: FencedCommitPort,
        state: DagRunState,
        executor: BoundedStepDagExecutor,
    ) -> None:
        for step in executor.dag.steps:
            step_state = state.states[step.step_id]
            document = {
                "schema": "acp2.runtime.step-result.v1",
                "step_id": step.step_id,
                "status": step_state.status.value,
                "qualification": step_state.qualification,
                "publish_policy": step_state.publish_policy,
                "publish_status": step_state.publish_status.value,
                "execution_identity": step_state.step_identity,
                "identity_scheme": step_state.identity_scheme,
                "upstream_digests": list(step_state.upstream_digests),
                "output_digests": dict(step_state.output_digests),
                "products": [product.to_dict() for product in step_state.products],
                "publish_payload": dict(step_state.publish_payload),
                "reason": step_state.reason,
            }
            staged = commit.stage(step_result_path(step.step_id), document)
            commit.commit_if_fenced(staged)
        staged = commit.stage("WORK/00_RUNTIME/step_dag_state.json", state.to_dict())
        commit.commit_if_fenced(staged)

    def _outcome(
        self, state: DagRunState, executor: BoundedStepDagExecutor
    ) -> TaskExecutionOutcome:
        required = [step for step in executor.dag.steps if step.required]
        required_failed = [
            step.step_id
            for step in required
            if state.states[step.step_id].status is not StepStatus.COMPLETED
        ]
        chain = tuple(executor.execution_chain())
        digest = chain_digest_of(chain) if chain else ""
        evidence = {
            f"step:{step.step_id}": state.states[step.step_id].step_identity
            for step in executor.dag.steps
            if state.states[step.step_id].step_identity
        }
        if required_failed:
            return TaskExecutionOutcome(
                state="failed",
                result_digest=digest,
                errors=tuple(
                    f"required step {step_id} did not complete" for step_id in required_failed
                ),
                evidence_digests=evidence,
                step_execution_chain=chain,
                chain_digest=digest,
            )
        published = all(
            state.states[step.step_id].publish_status.value == "published"
            for step in required
            if state.states[step.step_id].publish_policy == PUBLISH_POLICY_NORMAL
        )
        if not published:
            return TaskExecutionOutcome(
                state="failed",
                result_digest=digest,
                errors=("publication_failed: retry publication without recomputing",),
                evidence_digests=evidence,
                step_execution_chain=chain,
                chain_digest=digest,
            )
        return TaskExecutionOutcome(
            state="completed",
            result_digest=digest,
            evidence_digests=evidence,
            step_execution_chain=chain,
            chain_digest=digest,
        )


def _default_runner(entrypoint: str) -> Any:
    from acp_services.runtime.step_dispatch import _default_runner as resolve

    return resolve(entrypoint)


def read_step_result(work_dir: Path | str, step_id: str) -> dict[str, Any] | None:
    """Read one persisted ``step_result.json`` (``None`` when absent)."""
    path = Path(work_dir) / step_result_path(step_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def step_statuses(work_dir: Path | str) -> dict[str, str]:
    """Return ``{step_id: status}`` from the persisted DAG run state."""
    path = Path(work_dir) / "WORK" / "00_RUNTIME" / "step_dag_state.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(entry.get("step_id")): str(entry.get("status"))
        for entry in payload.get("states") or ()
        if isinstance(entry, Mapping)
    }
