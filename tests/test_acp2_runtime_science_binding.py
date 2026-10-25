"""B18 — bind A's science recipes and EvidenceReport without owning science.

The static/simulated instances in this file are explicitly **STATIC** and do
not run QC.  They prove the platform contract: contract references, the C P2
minimal-form round trip, the one-TaskRef bounded step DAG, control/recovery and
publication semantics, evidence-status preservation and the Data research
resource CAS.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from acp_services.data.artifacts import ArtifactAuthorization, ArtifactStore
from acp_services.data.research_resources import (
    PROPOSAL_SCHEMA,
    RESEARCH_RECORD_SCHEMA,
    ResearchResourceAuthorizationError,
    ResearchResourceConflictError,
    ResearchResourceRequest,
    ResearchResourceRevokedError,
    ResearchResourceStore,
    research_resource_digest,
)
from acp_services.runtime.canonical_digest import proposal_plan_definition_digest
from acp_services.runtime.science_binding import (
    SCIENCE_SCHEMA_FILES,
    AtomMappingMismatchError,
    ScienceBindingError,
    ScientificPreconditionError,
    assert_atom_mapping,
    build_task_request,
    discover_capability,
    document_digest,
    load_science_contract_refs,
    reaction_mapping_digest,
    register_evidence_report,
    validate_evidence_report,
)
from acp_services.runtime.step_dag import (
    STEP_ENTRYPOINTS,
    BoundedStepDag,
    BoundedStepDagExecutor,
    BudgetCaps,
    ControlIntent,
    OutputRef,
    PublishStatus,
    ScienceStepKind,
    StepDagError,
    StepOutcome,
    StepSpec,
    StepStatus,
    materialize_step_identity,
    verify_worker_chain,
)
from acp_services.runtime.workflow_enablement import (
    CLOSURE_INTEGRATED,
    CLOSURE_PENDING,
    enablement_for,
    require_envelope_entry,
)

#: ``proposal.valid.json``/``research-record.valid.json`` are experience-domain
#: documents, so the B18 fixtures now live in two roots.  Search both instead of
#: hard-coding one path per document.
_FIXTURE_ROOTS = tuple(
    Path(__file__).resolve().parent / "fixtures" / "acp2" / domain / "b18"
    for domain in ("science", "experience")
)
_A_SCIENCE = Path("<acp2-dev>/A/docs/reference/next-change/acp2-contracts/science")
_PLAN = "v2:0123456789abcdef0123456789abcdef"
_RELEASE = "release-b18-static"


def _load(name: str) -> dict[str, Any]:
    for root in _FIXTURE_ROOTS:
        path = root / name
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"{name} not found in {_FIXTURE_ROOTS}")


def _discovery():
    return discover_capability(
        _load("capability-energy.json"), _load("task-binding-singlepoint.json")
    )


def _form() -> dict[str, Any]:
    return dict(_load("p2-minimal-form.json")["form"])


def _reaction() -> dict[str, Any]:
    return _load("reaction-da.json")


# ---------------------------------------------------------------------------
# B18a — A contract refs and the C P2 minimal-form round trip
# ---------------------------------------------------------------------------


def _write_minimal_science_contracts(root: Path) -> None:
    for schema_id, name in SCIENCE_SCHEMA_FILES.items():
        payload = {"schema_version": 1, "title": schema_id}
        (root / name).write_text(json.dumps(payload), encoding="utf-8")
    (root / "semantic-rules.md").write_text("# rules\n", encoding="utf-8")


def test_contract_refs_are_digested_and_never_pending(tmp_path: Path) -> None:
    root = tmp_path / "science"
    root.mkdir()
    _write_minimal_science_contracts(root)
    refs = load_science_contract_refs(root)
    assert set(SCIENCE_SCHEMA_FILES) <= set(refs)
    for ref in refs.values():
        assert ref.digest.startswith("sha256:")
        assert len(ref.digest) == 71
        assert ref.version == "1.0.0"


@pytest.mark.skipif(not _A_SCIENCE.is_dir(), reason="A worktree not present")
def test_contract_refs_read_a_published_files_without_copying_types() -> None:
    refs = load_science_contract_refs(_A_SCIENCE)
    for schema_id in SCIENCE_SCHEMA_FILES:
        expected = "sha256:" + hashlib.sha256(Path(refs[schema_id].path).read_bytes()).hexdigest()
        assert refs[schema_id].digest == expected
    assert all(ref.schema_version in (1, None) for ref in refs.values())


def test_roundtrip_preserves_scientific_parameters() -> None:
    form = _form()
    discovery = _discovery()
    request = build_task_request(form, discovery, reaction=_reaction())
    assert request.task.value == "singlepoint"
    assert request.charge == form["charge"]
    assert request.multiplicity == form["multiplicity"]
    assert request.level.method == form["method"]["method"]
    assert request.level.basis == form["method"]["basis"]
    assert request.level.solvent == form["method"]["solvent"]
    assert request.structure is not None
    assert request.structure.path is not None
    assert str(request.structure.path) == form["structure"]["path"]
    assert request.resources.nproc == form["resources"]["nproc"]

    # The wire form reparses to the same TaskRequest (field mapping, no parser).
    from cccp.calculation.requests import TaskRequest

    reparsed = TaskRequest.from_dict(request.to_dict())
    assert reparsed.to_dict() == request.to_dict()


def test_roundtrip_has_no_platform_default_for_missing_method() -> None:
    form = _form()
    form["method"] = dict(form["method"], method="")
    with pytest.raises(ScientificPreconditionError) as excinfo:
        build_task_request(form, _discovery(), reaction=_reaction())
    assert excinfo.value.code == "method_identity_missing"


def test_atom_mapping_mismatch_is_rejected_before_adoption() -> None:
    reaction = _reaction()
    expected = reaction_mapping_digest(reaction)
    assert assert_atom_mapping(expected, reaction) == expected
    form = _form()
    form["mapping_digest"] = "sha256:" + "0" * 64
    with pytest.raises(AtomMappingMismatchError) as excinfo:
        build_task_request(form, _discovery(), reaction=reaction)
    assert excinfo.value.code == "atom_mapping_mismatch"
    assert excinfo.value.expected == expected


def test_unknown_form_field_is_rejected() -> None:
    form = _form()
    form["job_status"] = "completed"
    with pytest.raises(ScienceBindingError) as excinfo:
        build_task_request(form, _discovery(), reaction=_reaction())
    assert excinfo.value.code == "unknown_execution_field"


def test_unknown_major_version_is_never_executable() -> None:
    capability = _load("capability-energy.json")
    capability["schema_version"] = 2
    with pytest.raises(ScienceBindingError) as excinfo:
        discover_capability(capability, _load("task-binding-singlepoint.json"))
    assert excinfo.value.code == "unknown_major_version"


# ---------------------------------------------------------------------------
# B18b — one TaskRef, bounded step DAG
# ---------------------------------------------------------------------------


def _three_step_dag() -> BoundedStepDag:
    ts = StepSpec(
        step_id="ts_opt",
        kind=ScienceStepKind.OPTIMIZE,
        intent="optimize the frozen TS guess",
        parameters={"optimizer": "r2SCAN-3c"},
        produces=(OutputRef("ts_opt", "ts_geometry"),),
        structure_role="transition_state",
        implementation_version="cccp-opt-1",
        capability="optimize.ts",
        qc_invocations=1,
    )
    frequency = StepSpec(
        step_id="ts_freq",
        kind=ScienceStepKind.FREQUENCY,
        intent="verify one imaginary mode",
        parameters={"thermo": "grimme"},
        consumes=(
            OutputRef(
                "ts_opt",
                "ts_geometry",
                document_digest({"step": "ts_opt", "product": "ts_geometry"}),
            ),
        ),
        produces=(OutputRef("ts_freq", "frequency_summary"),),
        structure_role="transition_state",
        implementation_version="cccp-freq-1",
        capability="frequency.ts",
        qc_invocations=1,
    )
    irc = StepSpec(
        step_id="ts_irc",
        kind=ScienceStepKind.IRC,
        intent="confirm both IRC endpoints",
        parameters={"directions": ["forward", "reverse"]},
        consumes=(
            OutputRef(
                "ts_freq",
                "frequency_summary",
                document_digest({"step": "ts_freq", "product": "frequency_summary"}),
            ),
        ),
        produces=(OutputRef("ts_irc", "irc_paths"),),
        structure_role="transition_state",
        implementation_version="cccp-irc-1",
        capability="irc.ts",
        qc_invocations=1,
    )
    return BoundedStepDag(
        recipe_id="recipe.da.static.v1", task_ref="task-b18-static", steps=(ts, frequency, irc)
    )


class _SimulatedDispatch:
    """STATIC dispatcher: returns deterministic digests, never runs QC."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[str] = []
        self.upstream: dict[str, tuple[str, ...]] = {}
        self.fail_on = fail_on

    def __call__(self, context: Any) -> StepOutcome:
        step_id = context.step.step_id
        self.calls.append(step_id)
        self.upstream[step_id] = tuple(context.upstream_digests)
        if self.fail_on == step_id:
            raise RuntimeError("simulated compute failure")
        return StepOutcome(
            output_digests={
                ref.product_kind: document_digest({"step": step_id, "product": ref.product_kind})
                for ref in context.step.produces
            },
            receipt_digest=document_digest({"receipt": step_id}),
            result_digest=document_digest({"result": step_id}),
            publish_payload={"step_id": step_id, "simulated": True},
        )


class _Publish:
    def __init__(self, fail_first_for: str | None = None) -> None:
        self.published: list[str] = []
        self.attempts: list[str] = []
        self.fail_first_for = fail_first_for

    def __call__(self, step: StepSpec, outcome: StepOutcome) -> dict[str, str]:
        self.attempts.append(step.step_id)
        if self.fail_first_for == step.step_id:
            self.fail_first_for = None
            raise OSError("simulated publication failure")
        self.published.append(step.step_id)
        return dict(outcome.output_digests)


def _executor(
    dag: BoundedStepDag,
    dispatch: Any,
    publish: Any,
    *,
    budget_caps: BudgetCaps | None = None,
) -> BoundedStepDagExecutor:
    return BoundedStepDagExecutor(
        dag,
        dispatch,
        publish,
        plan_identity=_PLAN,
        release_id=_RELEASE,
        budget_caps=budget_caps,
    )


def test_dag_is_bounded_and_irc_uses_existing_task_core() -> None:
    dag = _three_step_dag()
    assert dag.validate() == []
    assert dag.topological_order() == ["ts_opt", "ts_freq", "ts_irc"]
    assert STEP_ENTRYPOINTS[ScienceStepKind.IRC] == "cccp.calculation.run_irc"
    document = dag.to_dict()
    assert document["task_ref"] == "task-b18-static"
    assert document["execution_unit"]["one_platform_task"] is True
    assert [step["kind"] for step in document["steps"]] == ["optimize", "frequency", "irc"]


def test_dag_rejects_cycle_branch_and_dangling_refs() -> None:
    a = StepSpec(
        step_id="a",
        kind=ScienceStepKind.SINGLEPOINT,
        intent="a",
        produces=(OutputRef("a", "energy"),),
        implementation_version="v1",
    )
    b = StepSpec(
        step_id="b",
        kind=ScienceStepKind.FREQUENCY,
        intent="b",
        consumes=(OutputRef("a", "energy"),),
        produces=(OutputRef("b", "freq"),),
        implementation_version="v1",
    )
    dangling = BoundedStepDag(
        recipe_id="r",
        task_ref="t",
        steps=(
            a,
            StepSpec(
                step_id="c",
                kind=ScienceStepKind.IRC,
                intent="c",
                consumes=(OutputRef("missing", "energy"),),
                implementation_version="v1",
            ),
        ),
    )
    assert any("dangling_output_ref" in error for error in dangling.validate())

    fan_out = BoundedStepDag(
        recipe_id="r",
        task_ref="t",
        steps=(
            a,
            b,
            StepSpec(
                step_id="c",
                kind=ScienceStepKind.SINGLEPOINT,
                intent="c",
                consumes=(OutputRef("a", "energy"),),
                implementation_version="v1",
            ),
        ),
    )
    assert any("fan_out_exceeded" in error for error in fan_out.validate())

    cyclic = BoundedStepDag(
        recipe_id="r",
        task_ref="t",
        steps=(
            StepSpec(
                step_id="a",
                kind=ScienceStepKind.SINGLEPOINT,
                intent="a",
                consumes=(OutputRef("b", "freq"),),
                produces=(OutputRef("a", "energy"),),
                implementation_version="v1",
            ),
            b,
        ),
    )
    assert any("missing_product" in error or "cycle" in error for error in cyclic.validate())


def test_three_step_static_run_passes_outputrefs() -> None:
    dispatch = _SimulatedDispatch()
    publish = _Publish()
    executor = _executor(_three_step_dag(), dispatch, publish)
    state = executor.run()
    assert dispatch.calls == ["ts_opt", "ts_freq", "ts_irc"]
    assert state.statuses() == {
        "ts_opt": "completed",
        "ts_freq": "completed",
        "ts_irc": "completed",
    }
    assert executor.publish_statuses() == {
        "ts_opt": "published",
        "ts_freq": "published",
        "ts_irc": "published",
    }
    assert dispatch.upstream["ts_opt"] == ()
    assert dispatch.upstream["ts_freq"] == (
        document_digest({"step": "ts_opt", "product": "ts_geometry"}),
    )
    assert state.budget.to_dict() == {
        "task_refs": 1,
        "steps": 3,
        "qc_invocations": 3,
        "attempts": 3,
        "bsub": 0,
    }
    assert state.dispatch_count == 3


def test_required_upstream_failure_blocks_dependents_never_completed() -> None:
    dispatch = _SimulatedDispatch(fail_on="ts_freq")
    state = _executor(_three_step_dag(), dispatch, _Publish()).run()
    assert state.states["ts_opt"].status is StepStatus.COMPLETED
    assert state.states["ts_freq"].status is StepStatus.FAILED
    assert state.states["ts_irc"].status is StepStatus.UPSTREAM_FAILED
    assert state.states["ts_irc"].status is not StepStatus.COMPLETED
    assert "upstream_failed:ts_freq" == state.states["ts_irc"].reason


def test_budget_is_checked_before_dispatch() -> None:
    dispatch = _SimulatedDispatch()
    caps = BudgetCaps(task_refs=1, steps=1, qc_invocations=1, attempts=1, bsub=0)
    state = _executor(_three_step_dag(), dispatch, _Publish(), budget_caps=caps).run()
    assert dispatch.calls == ["ts_opt"]
    assert state.states["ts_freq"].status is StepStatus.BLOCKED
    assert state.states["ts_freq"].reason == "budget_exceeded"


def test_publication_failure_does_not_recompute() -> None:
    dispatch = _SimulatedDispatch()
    publish = _Publish(fail_first_for="ts_freq")
    executor = _executor(_three_step_dag(), dispatch, publish)
    state = executor.run()
    assert dispatch.calls == ["ts_opt", "ts_freq"]
    assert state.states["ts_freq"].status is StepStatus.COMPLETED
    assert state.states["ts_freq"].publish_status is PublishStatus.FAILED
    assert state.states["ts_irc"].status is StepStatus.BLOCKED
    assert state.states["ts_irc"].reason == "upstream_unpublished:ts_freq"
    dispatches = state.dispatch_count
    assert executor.retry_publication("ts_freq") is True
    assert state.dispatch_count == dispatches
    assert dispatch.calls == ["ts_opt", "ts_freq"]
    assert state.states["ts_freq"].publish_status is PublishStatus.PUBLISHED
    resumed = executor.run()
    assert resumed.statuses()["ts_irc"] == "completed"
    assert dispatch.calls == ["ts_opt", "ts_freq", "ts_irc"]


def test_cancel_creates_no_new_step() -> None:
    dispatch = _SimulatedDispatch()
    executor = _executor(_three_step_dag(), dispatch, _Publish())
    statuses = executor.apply_control(ControlIntent.CANCEL)
    assert statuses == {"ts_opt": "cancelled", "ts_freq": "cancelled", "ts_irc": "cancelled"}
    executor.run()
    assert executor.state.dispatch_count == 0
    assert dispatch.calls == []


def test_pause_and_continue_propagate_to_step_control() -> None:
    holder: dict[str, BoundedStepDagExecutor] = {}

    class _PauseAfterFirst(_SimulatedDispatch):
        def __call__(self, context: Any) -> StepOutcome:
            outcome = super().__call__(context)
            if len(self.calls) == 1:
                holder["executor"].apply_control(ControlIntent.PAUSE)
            return outcome

    dispatch = _PauseAfterFirst()
    executor = _executor(_three_step_dag(), dispatch, _Publish())
    holder["executor"] = executor
    state = executor.run()
    assert dispatch.calls == ["ts_opt"]
    assert state.paused is True
    assert state.states["ts_freq"].status is StepStatus.PENDING
    assert state.states["ts_irc"].status is StepStatus.PENDING
    executor.apply_control(ControlIntent.CONTINUE)
    state = executor.run()
    assert state.paused is False
    assert dispatch.calls == ["ts_opt", "ts_freq", "ts_irc"]


def test_restart_recovers_only_unfinished_steps() -> None:
    dag = _three_step_dag()
    first_dispatch = _SimulatedDispatch()
    executor = _executor(dag, first_dispatch, _Publish())
    executor.run()
    persisted = executor.state.to_dict()
    by_id = {state["step_id"]: state for state in persisted["states"]}

    # Simulate a Controller restart where the third step was still running.
    by_id["ts_irc"]["status"] = "running"
    by_id["ts_freq"]["status"] = "completed"
    by_id["ts_freq"]["publish_status"] = "published"
    second_dispatch = _SimulatedDispatch()
    recovered = BoundedStepDagExecutor.recover(
        dag,
        second_dispatch,
        _Publish(),
        plan_identity=_PLAN,
        release_id=_RELEASE,
        persisted=persisted,
    )
    assert recovered.state.states["ts_opt"].status is StepStatus.COMPLETED
    assert recovered.state.states["ts_freq"].status is StepStatus.COMPLETED
    assert recovered.state.states["ts_irc"].status is StepStatus.PENDING
    recovered.run()
    assert second_dispatch.calls == ["ts_irc"]
    assert recovered.state.states["ts_irc"].status is StepStatus.COMPLETED


def test_step_identity_requires_release_and_chains_receipt() -> None:
    step = _three_step_dag().steps[0]
    with pytest.raises(StepDagError) as excinfo:
        materialize_step_identity(
            step, step_index=0, plan_identity=_PLAN, upstream_digests=(), release_id=""
        )
    assert excinfo.value.code == "missing_release"
    identity = materialize_step_identity(
        step, step_index=0, plan_identity=_PLAN, upstream_digests=(), release_id=_RELEASE
    )
    assert identity.step_identity.startswith("sha256:")
    receipt = {
        "plan_identity": _PLAN,
        "release_id": _RELEASE,
        "input_digest": "sha256:" + "1" * 64,
        "atom_mapping_digest": "sha256:" + "2" * 64,
        "result_digest": identity.step_identity,
    }
    result = {
        "plan_identity": _PLAN,
        "release_id": _RELEASE,
        "input_digest": "sha256:" + "1" * 64,
        "atom_mapping_digest": "sha256:" + "2" * 64,
        "result_digest": identity.step_identity,
    }
    verify_worker_chain(
        plan_identity=_PLAN,
        release_id=_RELEASE,
        input_digest="sha256:" + "1" * 64,
        atom_mapping_digest="sha256:" + "2" * 64,
        receipt=receipt,
        result=result,
    )
    result["atom_mapping_digest"] = "sha256:" + "3" * 64
    with pytest.raises(StepDagError) as excinfo:
        verify_worker_chain(
            plan_identity=_PLAN,
            release_id=_RELEASE,
            input_digest="sha256:" + "1" * 64,
            atom_mapping_digest="sha256:" + "2" * 64,
            receipt=receipt,
            result=result,
        )
    assert excinfo.value.code == "digest_mismatch"


# ---------------------------------------------------------------------------
# EvidenceReport registration (statuses preserved; completed != supported)
# ---------------------------------------------------------------------------


def _report() -> dict[str, Any]:
    return _load("evidence-report.json")


def test_evidence_report_statuses_are_preserved_and_completed_is_not_supported(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    report = _report()
    manifest = register_evidence_report(
        report,
        store=store,
        job_id="b18-static",
        attempt=1,
        native_files=(
            {"relative_path": "native/stdout.txt", "role": "native_output", "body": "static\n"},
            {"relative_path": "native/stderr.txt", "role": "native_output", "body": "none\n"},
        ),
        provenance_digest="sha256:" + "c" * 64,
        authorization=ArtifactAuthorization(principal_id="b18", project_id="proj-b18"),
    )
    assert dict(manifest.claim_statuses) == {
        "c-static-supported": "supported",
        "c-static-refuted": "refuted",
        "c-static-inconclusive": "inconclusive",
        "c-static-not-evaluated": "not_evaluated",
    }
    assert manifest.execution_terminal_state == "completed"
    assert manifest.task_execution_completed is True
    assert manifest.claim_interpretation == "not_interpreted"
    assert len(manifest.native_files) == 2
    payload = manifest.to_dict()
    assert "claim_supported" not in payload
    assert all(
        row["status"] in {"supported", "refuted", "inconclusive", "not_evaluated"}
        for row in payload["claim_statuses"]
    )
    served = store.read(manifest.report_ref)
    assert json.loads(served.decode("utf-8")) == report


def test_evidence_report_rejects_a_fifth_claim_status() -> None:
    report = _report()
    report["claims"][0]["status"] = "confirmed"
    with pytest.raises(Exception) as excinfo:
        validate_evidence_report(report)
    assert getattr(excinfo.value, "code", "") == "unknown_claim_status"


def test_evidence_report_completed_with_only_not_evaluated_stays_not_evaluated(
    tmp_path: Path,
) -> None:
    report = _report()
    for claim in report["claims"]:
        claim["status"] = "not_evaluated"
        claim["source_refs"] = []
        claim["missing_checks"] = [
            {"check_id": "not_executed", "reason": "STATIC fixture", "blocking": True}
        ]
    validate_evidence_report(report)
    manifest = register_evidence_report(
        report,
        store=ArtifactStore(tmp_path / "artifacts"),
        job_id="b18-static",
        attempt=1,
    )
    assert {status for _claim, status in manifest.claim_statuses} == {"not_evaluated"}
    assert manifest.task_execution_completed is True
    assert "claim_supported" not in manifest.to_dict()


# ---------------------------------------------------------------------------
# C plan-identity projection (B16 canonicalization)
# ---------------------------------------------------------------------------


def test_c_plan_identity_projection_matches_the_frozen_vector() -> None:
    proposal = _load("proposal.valid.json")
    projection = proposal_plan_definition_digest(proposal)
    assert projection.digest == proposal["plan_definition_digest"]
    assert (
        projection.digest
        == "sha256:32bd96dd1c8d2eb962a419590ff91ebf695adef1c587c147185f522be5422d9b"
    )


# ---------------------------------------------------------------------------
# Data research resources (Proposal / ResearchRecord CAS)
# ---------------------------------------------------------------------------


def test_research_resource_create_read_cas_conflict_and_revocation(tmp_path: Path) -> None:
    proposal = _load("proposal.valid.json")
    store = ResearchResourceStore(tmp_path / "resources.db")
    try:
        created = store.create(
            ResearchResourceRequest(
                resource_id="rr-proposal-b18",
                schema=PROPOSAL_SCHEMA,
                document=proposal,
                principal_id="principal-b18",
                project_id="proj-acp2-da",
            )
        )
        assert created.revision == 1
        assert created.digest == research_resource_digest(proposal)
        read = store.read(
            "rr-proposal-b18", principal_id="principal-b18", project_id="proj-acp2-da"
        )
        assert read.document == proposal
        assert read.revision == created.revision
        assert read.digest == created.digest

        changed = dict(proposal)
        changed["hypothesis"] = changed["hypothesis"] + " (revised)"
        updated = store.compare_and_swap(
            "rr-proposal-b18",
            expected_revision=1,
            document=changed,
            principal_id="principal-b18",
            project_id="proj-acp2-da",
        )
        assert updated.revision == 2
        assert updated.digest == research_resource_digest(changed)
        assert (
            store.read(
                "rr-proposal-b18", principal_id="principal-b18", project_id="proj-acp2-da"
            ).document
            == changed
        )

        with pytest.raises(ResearchResourceConflictError) as conflict:
            store.compare_and_swap(
                "rr-proposal-b18",
                expected_revision=1,
                document=proposal,
                principal_id="principal-b18",
                project_id="proj-acp2-da",
            )
        assert conflict.value.current_revision == 2

        with pytest.raises(ResearchResourceAuthorizationError):
            store.read("rr-proposal-b18", principal_id="principal-b18", project_id="other-project")

        record = _load("research-record.valid.json")
        record_store = store.create(
            ResearchResourceRequest(
                resource_id="rr-record-b18",
                schema=RESEARCH_RECORD_SCHEMA,
                document=record,
                principal_id="principal-b18",
                project_id="proj-acp2-da",
            )
        )
        assert record_store.revision == 1
        store.revoke(
            "rr-record-b18",
            principal_id="principal-b18",
            project_id="proj-acp2-da",
            reason="B18 revocation negative",
        )
        with pytest.raises(ResearchResourceRevokedError):
            store.read("rr-record-b18", principal_id="principal-b18", project_id="proj-acp2-da")
        with pytest.raises(ResearchResourceRevokedError):
            store.compare_and_swap(
                "rr-record-b18",
                expected_revision=1,
                document=record,
                principal_id="principal-b18",
                project_id="proj-acp2-da",
            )
        versions = store.versions("rr-proposal-b18")
        assert [item.revision for item in versions] == [1, 2]
        assert versions[0].digest == research_resource_digest(proposal)
    finally:
        store.close()


def test_research_resource_rejects_cross_project_task_ref(tmp_path: Path) -> None:
    record = _load("research-record.valid.json")
    record["task_refs"][0]["project_id"] = "other-project"
    store = ResearchResourceStore(tmp_path / "resources.db")
    try:
        with pytest.raises(Exception) as excinfo:
            store.create(
                ResearchResourceRequest(
                    resource_id="rr-record-cross",
                    schema=RESEARCH_RECORD_SCHEMA,
                    document=record,
                    principal_id="principal-b18",
                    project_id="proj-acp2-da",
                )
            )
        assert "crosses the resource project" in str(excinfo.value)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Existing workflow/Task dispatch: TS/frequency/IRC evidence + legacy retained
# ---------------------------------------------------------------------------


def test_frequency_and_irc_are_enabled_on_existing_dispatch_and_ts_mode_keeps_legacy() -> None:
    frequency = enablement_for("frequency")
    irc = enablement_for("irc")
    tsmode = enablement_for("tsmode")
    assert frequency.closure == CLOSURE_INTEGRATED
    assert frequency.entry == "envelope"
    assert irc.closure == CLOSURE_INTEGRATED
    assert irc.entry == "envelope"
    assert require_envelope_entry("irc") == "envelope"
    assert tsmode.closure == CLOSURE_PENDING
    assert tsmode.entry == "legacy"
    assert frequency.evidence and irc.evidence and tsmode.evidence


def test_read_record_schema_is_not_a_task_table() -> None:
    proposal = _load("proposal.valid.json")
    for forbidden in ("job_id", "attempt", "queue_position", "status", "state"):
        assert forbidden not in proposal
    record = _load("research-record.valid.json")
    assert "record_revision" in record
    assert "attempt" not in record
