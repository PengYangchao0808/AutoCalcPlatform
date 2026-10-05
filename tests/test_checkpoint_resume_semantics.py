# pyright: reportUnknownParameterType=false, reportMissingParameterType=false, reportUnusedVariable=false
"""V01 — completed checkpoint facts survive any number of resumes.

Guards the remediation of the probe baseline defect where recovered steps
were rewritten as ``skipped`` in the checkpoint, so a later resume could no
longer see the completed fact and re-ran QC work (probe
``qc_call_counts`` 1→1→2, now pinned to 1→1→1→1).

Contract under test:

* a step loaded from the checkpoint as ``completed`` keeps
  ``status == "completed"`` for this run (``ExecutionResult.step_states``
  included);
* "not executed in this run" is the separate ``StepState.executed_this_run``
  observation, serialised into the checkpoint via ``to_dict()``;
* ``_persist_checkpoint`` merges loaded completed facts — it never writes a
  fresh ``pending`` state over a checkpoint ``completed``;
* ``Checkpoint.resume_count`` is the checkpoint-internal resume counter
  (renamed from ``attempts``): the v1 serialisation key stays ``attempts``
  (frozen fixtures byte-identical), v2 uses ``resume_count``;
* per-step ``last_executed_attempt`` records the ``jobs.attempt`` active
  when that step executed (read from the scheduler ``job.json`` marker).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from acp.calculations import executor as executor_module
from acp.calculations import result_publication
from acp.calculations.checkpoint import load_checkpoint, write_checkpoint
from acp.calculations.contracts import (
    ArtifactRef,
    CalculationPlan,
    CalculationResult,
    CalculationStep,
    Checkpoint,
    StepKind,
    StructureArtifact,
)
from acp.calculations.executor import CalculationPlanExecutor
from acp.calculations.identity import IDENTITY_SCHEMA
from acp.calculations.step_result import (
    STEP_RESULT_SCHEMA_VERSION,
    check_resume_protocol,
    resolve_resume_source,
    write_step_result,
)
from acp.storage.manifest import ResultManifest

# frozen v1 fixture — the legacy serialisation key must never change
_V1_FIXTURE = (
    Path(__file__).parent
    / "baseline"
    / "recovery_fixtures"
    / "checkpoint_mixed"
    / "WORK"
    / "00_RUNTIME"
    / "checkpoint.json"
)


def _plan(root: Path) -> CalculationPlan:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "input.xyz"
    path.write_text("1\ninput\nH 0 0 0\n", encoding="utf-8")
    return CalculationPlan(
        workflow="singlepoint",
        profile="HF",
        items=[StructureArtifact(path=path, elements=["H"])],
        steps=[
            CalculationStep(kind=StepKind.SINGLEPOINT),
            CalculationStep(kind=StepKind.FREQUENCY),
        ],
    )


def _checkpoint_path(root: Path) -> Path:
    return root / "WORK" / "00_RUNTIME" / "checkpoint.json"


def _read_checkpoint(root: Path) -> dict:
    payload = json.loads(_checkpoint_path(root).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _assert_step0_completed(root: Path) -> None:
    """The invariant under test — must fail when the completed fact is lost."""
    payload = _read_checkpoint(root)
    assert payload["step_states"][0]["status"] == "completed"


def _dispatcher(sp_calls: list[int]) -> dict[StepKind, object]:
    def sp(request: object) -> CalculationResult:
        sp_calls.append(1)
        return CalculationResult(energy=-2.0)

    def freq(request: object) -> CalculationResult:
        return CalculationResult(status="failed", errors=["retry pending"])

    return {StepKind.SINGLEPOINT: sp, StepKind.FREQUENCY: freq}


def test_completed_fact_survives_three_resumes(tmp_path: Path) -> None:
    """SP succeeds → FREQ fails; three resumes: call count 1→1→1→1."""
    plan = _plan(tmp_path)
    sp_calls: list[int] = []
    executor = CalculationPlanExecutor()
    observations: list[dict[str, object]] = []

    with patch.dict(executor_module._PRIMITIVE_DISPATCH, _dispatcher(sp_calls)):
        for run in range(4):  # first run + three consecutive resumes
            # jobs.attempt advances per requeue; the marker the executor reads
            (tmp_path / "job.json").write_text(json.dumps({"attempt": run + 1}), encoding="utf-8")
            result = executor.execute(plan, tmp_path)
            payload = _read_checkpoint(tmp_path)
            sp_state = result.step_states[0]
            freq_state = result.step_states[1]

            # ExecutionResult keeps external semantics: completed | failed
            assert result.status == "failed"
            assert sp_state.status == "completed"
            assert freq_state.status == "failed"
            # recovered step: not executed in THIS run — separate observation
            assert sp_state.executed_this_run is (run == 0)
            assert freq_state.executed_this_run is True
            # checkpoint file: the completed fact is never rewritten
            _assert_step0_completed(tmp_path)
            assert payload["step_states"][0]["executed_this_run"] is (run == 0)
            assert payload["step_states"][1]["status"] == "failed"
            # v2 writer: resume_count key replaces attempts
            assert payload["resume_count"] == run
            assert "attempts" not in payload
            # last_executed_attempt = jobs.attempt when that step executed
            assert sp_state.last_executed_attempt == 1
            assert payload["step_states"][0]["last_executed_attempt"] == 1
            assert freq_state.last_executed_attempt == run + 1

            observations.append(
                {
                    "run": run,
                    "sp_calls": len(sp_calls),
                    "sp_status": sp_state.status,
                    "sp_executed_this_run": sp_state.executed_this_run,
                    "checkpoint_sp_status": payload["step_states"][0]["status"],
                    "freq_status": freq_state.status,
                    "resume_count": payload["resume_count"],
                    "sp_last_executed_attempt": sp_state.last_executed_attempt,
                    "freq_last_executed_attempt": freq_state.last_executed_attempt,
                }
            )

    # the defect witness: SP is executed exactly once across all four runs
    assert [o["sp_calls"] for o in observations] == [1, 1, 1, 1]
    assert [o["checkpoint_sp_status"] for o in observations] == ["completed"] * 4
    assert [o["sp_status"] for o in observations] == ["completed"] * 4
    assert [o["sp_executed_this_run"] for o in observations] == [
        True,
        False,
        False,
        False,
    ]


def test_negative_injection_completed_overwrite_detected(tmp_path: Path) -> None:
    """Guard: force the resume branch to write ``pending`` → assertion fails.

    Negative injection (restored automatically on context exit): disables the
    completed-fact merge and rewrites the recovered step's status back to
    ``pending`` exactly as the pre-remediation resume branch did.  The
    invariant assertion must then fail — proving the checkpoint assertions
    are real guards, not vacuous.
    """
    plan = _plan(tmp_path)
    sp_calls: list[int] = []
    executor = CalculationPlanExecutor()

    with patch.dict(executor_module._PRIMITIVE_DISPATCH, _dispatcher(sp_calls)):
        executor.execute(plan, tmp_path)
        _assert_step0_completed(tmp_path)

        def _break_merge(fresh: list[dict], loaded: dict[int, dict]) -> list[dict]:
            broken = [dict(state) for state in fresh]
            for idx in loaded:
                if idx < len(broken) and not broken[idx].get("executed_this_run", True):
                    broken[idx]["status"] = "pending"
            return broken

        with patch.object(executor_module, "_merge_completed_facts", _break_merge):
            executor.execute(plan, tmp_path)

        # the invariant now fails: the completed fact was overwritten
        with pytest.raises(AssertionError):
            _assert_step0_completed(tmp_path)
        payload = _read_checkpoint(tmp_path)
        assert payload["step_states"][0]["status"] == "pending"
        # the skip logic itself was not disabled — QC was still not re-run
        assert len(sp_calls) == 1


def test_serialization_v1_attempts_key_v2_resume_count(tmp_path: Path) -> None:
    """v1 payload keeps the ``attempts`` key; v2 writes ``resume_count``."""
    base = dict(
        task_id="t",
        workflow="singlepoint",
        plan_fingerprint="fp",
        step_states=[],
        items_state={},
    )
    v1_dir = tmp_path / "v1"
    write_checkpoint(v1_dir, Checkpoint(**base, resume_count=3, identity_schema=1))
    v1_payload = json.loads((v1_dir / "checkpoint.json").read_text(encoding="utf-8"))
    assert v1_payload["attempts"] == 3
    assert "resume_count" not in v1_payload
    reloaded_v1 = load_checkpoint(v1_dir, "fp", allow_legacy_fingerprint=True)
    assert reloaded_v1 is not None and reloaded_v1.resume_count == 3

    v2_dir = tmp_path / "v2"
    write_checkpoint(v2_dir, Checkpoint(**base, resume_count=3, identity_schema=IDENTITY_SCHEMA))
    v2_payload = json.loads((v2_dir / "checkpoint.json").read_text(encoding="utf-8"))
    assert v2_payload["resume_count"] == 3
    assert "attempts" not in v2_payload
    reloaded_v2 = load_checkpoint(v2_dir, "fp")
    assert reloaded_v2 is not None and reloaded_v2.resume_count == 3


def test_v1_fixture_serialisation_key_frozen() -> None:
    """The frozen v1 fixture still carries ``attempts`` — byte-identical key."""
    payload = json.loads(_V1_FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert "attempts" in payload
    assert "resume_count" not in payload
    # legacy readers see the counter under the old key
    assert isinstance(payload["attempts"], int)


# ══════════════════════════════════════════════════════════════════════
# V02 — step scientific result persistence + publication rebuild on resume
# ══════════════════════════════════════════════════════════════════════


def _single_step_plan(root: Path) -> CalculationPlan:
    """A one-step (SP) plan bound to a real input file under *root*."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / "input.xyz"
    path.write_text("1\ninput\nH 0 0 0\n", encoding="utf-8")
    return CalculationPlan(
        workflow="singlepoint",
        profile="HF",
        items=[StructureArtifact(path=path, elements=["H"])],
        steps=[CalculationStep(kind=StepKind.SINGLEPOINT)],
    )


def _artifact_sp(root: Path, sp_calls: list[int]) -> dict[StepKind, object]:
    """SP dispatcher that persists one real artifact file (``sp.out``)."""

    def sp(request: object) -> CalculationResult:
        sp_calls.append(1)
        step_dir = root / "WORK" / "05_SP"
        step_dir.mkdir(parents=True, exist_ok=True)
        out = step_dir / "sp.out"
        out.write_text("qc output v1\n", encoding="utf-8")
        return CalculationResult(
            energy=-2.0,
            artifacts=[ArtifactRef(path=out, type="output", source="test")],
        )

    return {StepKind.SINGLEPOINT: sp}


def _counting_dispatcher(
    root: Path, sp_calls: list[int], freq_calls: list[int]
) -> dict[StepKind, object]:
    """Both steps succeed with one real artifact each; calls are recorded."""

    def sp(request: object) -> CalculationResult:
        sp_calls.append(1)
        return CalculationResult(energy=-2.0)

    def freq(request: object) -> CalculationResult:
        freq_calls.append(1)
        step_dir = root / "WORK" / "04_FREQ"
        step_dir.mkdir(parents=True, exist_ok=True)
        out = step_dir / "freq.out"
        out.write_text("frequency output v1\n", encoding="utf-8")
        return CalculationResult(
            frequencies=[100.0, 200.0],
            artifacts=[ArtifactRef(path=out, type="log", source="test")],
        )

    return {StepKind.SINGLEPOINT: sp, StepKind.FREQUENCY: freq}


def _sp_products(root: Path) -> list[str]:
    """Product ids published for the SP step (index 0) of ``_plan``."""
    manifest = ResultManifest.read(root / "RESULT")
    return [p.id for p in manifest.products if p.id.startswith("step_0_singlepoint")]


def test_products_survive_resume(tmp_path: Path) -> None:
    """SP products stay in RESULT across two resumes (probe 1→0→1 reversed)."""
    plan = _plan(tmp_path)
    sp_calls: list[int] = []
    counts: list[int] = []

    with patch.dict(executor_module._PRIMITIVE_DISPATCH, _dispatcher(sp_calls)):
        for _ in range(3):  # first run + two resumes
            result = executor_module.CalculationPlanExecutor().execute(plan, tmp_path)
            assert result.status == "failed"  # FREQ keeps failing
            counts.append(len(_sp_products(tmp_path)))

    assert counts == [1, 1, 1], f"SP products lost on resume: {counts}"
    assert sp_calls == [1], "SP must never re-execute across resumes"


def test_publish_failure_retries_without_qc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manifest write failure is retried as publication only — no QC rerun."""
    plan = _plan(tmp_path)
    sp_calls: list[int] = []
    freq_calls: list[int] = []
    real_register = result_publication.register_result_manifest
    injection = {"armed": True}

    def flaky_register(result_dir, manifest):  # type: ignore[no-untyped-def]
        if injection["armed"] and manifest.workflow == "frequency":
            injection["armed"] = False
            raise OSError("injected manifest write failure")
        return real_register(result_dir, manifest)

    monkeypatch.setattr(result_publication, "register_result_manifest", flaky_register)

    with patch.dict(
        executor_module._PRIMITIVE_DISPATCH,
        _counting_dispatcher(tmp_path, sp_calls, freq_calls),
    ):
        # run 1: both steps compute; the FREQ publication fails.
        result1 = CalculationPlanExecutor().execute(plan, tmp_path)
        assert len(sp_calls) == 1 and len(freq_calls) == 1
        assert result1.status == "failed"  # publication failure is observable…

        # run 2 (resume): only the publication is retried.
        result2 = CalculationPlanExecutor().execute(plan, tmp_path)

    assert len(sp_calls) == 1, "SP QC must not re-run on publish retry"
    assert len(freq_calls) == 1, "FREQ QC must not re-run on publish retry"
    assert result2.status == "completed", result2.errors
    state = result_publication.load_publication_state(tmp_path / "WORK" / "04_FREQ")
    assert state is not None and state.complete is True
    # RESULT manifest rebuilt from adopted results — products are durable.
    manifest = ResultManifest.read(tmp_path / "RESULT")
    assert any(p.id.startswith("step_0_singlepoint") for p in manifest.products)
    assert any(p.id.startswith("step_1_frequency") for p in manifest.products)


def test_step_result_digest_mismatch_recomputes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A tampered recorded artifact invalidates adoption → recompute."""
    plan = _single_step_plan(tmp_path)
    sp_calls: list[int] = []
    with patch.dict(executor_module._PRIMITIVE_DISPATCH, _artifact_sp(tmp_path, sp_calls)):
        with caplog.at_level(logging.INFO):
            CalculationPlanExecutor().execute(plan, tmp_path)
            assert sp_calls == [1]
            (tmp_path / "WORK" / "05_SP" / "sp.out").write_text("tampered\n", encoding="utf-8")
            CalculationPlanExecutor().execute(plan, tmp_path)

    assert len(sp_calls) == 2, "tampered artifact must force a recompute"
    assert "step_result_digest_mismatch" in caplog.text


def test_step_result_identity_mismatch_not_adopted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A plan-identity change must not adopt leftover complete results."""
    plan = _plan(tmp_path)
    sp_calls: list[int] = []
    with patch.dict(executor_module._PRIMITIVE_DISPATCH, _dispatcher(sp_calls)):
        with caplog.at_level(logging.INFO):
            CalculationPlanExecutor().execute(plan, tmp_path)
            assert sp_calls == [1]
            # input content changes → plan/step identity changes
            (tmp_path / "input.xyz").write_text("1\ninput\nH 0 0 1\n", encoding="utf-8")
            CalculationPlanExecutor().execute(plan, tmp_path)

    assert len(sp_calls) == 2, "identity change must not adopt the stale result"
    assert "step_identity_mismatch" in caplog.text


def test_artifact_integrity_missing_recomputes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Deleting a recorded artifact → recompute + ``identity_artifact_missing``."""
    plan = _single_step_plan(tmp_path)
    sp_calls: list[int] = []
    with patch.dict(executor_module._PRIMITIVE_DISPATCH, _artifact_sp(tmp_path, sp_calls)):
        with caplog.at_level(logging.INFO):
            CalculationPlanExecutor().execute(plan, tmp_path)
            assert sp_calls == [1]
            (tmp_path / "WORK" / "05_SP" / "sp.out").unlink()
            CalculationPlanExecutor().execute(plan, tmp_path)

    assert len(sp_calls) == 2, "missing recorded artifact must force a recompute"
    assert "identity_artifact_missing" in caplog.text


def _stability_plan(root: Path) -> CalculationPlan:
    """One OPT step carrying the FINAL_GEOMETRY stability diagnostic module."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / "input.xyz"
    path.write_text("1\ninput\nH 0 0 0\n", encoding="utf-8")
    electronic_state: dict[str, object] = {
        "schema_version": 1,
        "execution_mode": "single",
        "states": [
            {
                "state_id": "s1",
                "target_multiplicity": 1,
                "spin_mode": "restricted",
                "diagnostics": {"stability": "final_geometry"},
            }
        ],
    }
    spec: dict[str, object] = {
        "method": "wB97X-D4",
        "backend": "orca",
        "electronic_state": electronic_state,
    }
    return CalculationPlan(
        workflow="optimize",
        profile="default",
        items=[StructureArtifact(path=path, elements=["H"])],
        steps=[CalculationStep(kind=StepKind.OPTIMIZE, spec=spec)],  # type: ignore[arg-type]
    )


def test_stability_node_stable_identity_across_resumes(
    tmp_path: Path,
) -> None:
    """The appended stability node keeps a stable step_id and never re-runs."""
    task_root = tmp_path / "task"
    plan = _stability_plan(task_root)
    opt_calls: list[int] = []
    stability_calls: list[int] = []

    def fake_opt(request: object) -> CalculationResult:
        opt_calls.append(1)
        return CalculationResult(
            energy=-1.5, coords=[[0.0, 0.0, 0.0]], metadata={"optimization_status": "converged"}
        )

    def fake_stability(request: object) -> CalculationResult:
        stability_calls.append(1)
        return CalculationResult(energy=-1.6)

    with patch.dict(executor_module._PRIMITIVE_DISPATCH, {StepKind.OPTIMIZE: fake_opt}):
        with patch.object(executor_module, "run_singlepoint", fake_stability):
            first = CalculationPlanExecutor().execute(plan, task_root)
            second = CalculationPlanExecutor().execute(plan, task_root)

    assert len(opt_calls) == 1, "OPT must be adopted, not re-run"
    assert len(stability_calls) == 1, "stability node must not re-run on resume"
    assert first.status == "completed" and len(first.step_states) == 2
    assert first.step_states[1].status == "completed"
    assert second.step_states[1].status == "completed"
    assert second.step_states[1].executed_this_run is False

    stability_result = task_root / "WORK" / "05_SP" / "stability" / "step_result.json"
    assert stability_result.is_file()
    payload = json.loads(stability_result.read_text(encoding="utf-8"))
    assert payload["step_id"] == "step_1_singlepoint"  # stable across resumes
    assert payload["schema_version"] == STEP_RESULT_SCHEMA_VERSION
    assert isinstance(payload["step_identity"], str) and payload["step_identity"]


def test_continue_via_scheduler_requeue_keeps_completed_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real scheduler continue: step 1 primitive count stays 1 after requeue."""
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
    from acp.scheduler.manager import JobManager

    manager = JobManager(run_root=tmp_path / "runs", poll_interval=30)
    work_dir = tmp_path / "runs" / "singlepoint-task"
    work_dir.mkdir(parents=True)
    (work_dir / "job.json").write_text(
        json.dumps({"id": "v02-continue", "attempt": 1}), encoding="utf-8"
    )
    (work_dir / "task.json").write_text("{}", encoding="utf-8")
    record = JobRecord(
        id="v02-continue",
        spec=JobSpec(workflow="singlepoint", name="v02-continue"),
        status=JobStatus.FAILED,
        work_dir=str(work_dir),
        exit_code=1,
    )
    manager.store.create(record)

    try:
        plan = _plan(work_dir)
        sp_calls: list[int] = []
        with patch.dict(executor_module._PRIMITIVE_DISPATCH, _dispatcher(sp_calls)):
            CalculationPlanExecutor().execute(plan, work_dir)
            assert sp_calls == [1]

            persisted = manager.get(record.id)
            assert persisted is not None
            persisted.status = JobStatus.FAILED
            manager.store.update(persisted)
            submissions: list[str] = []
            monkeypatch.setattr(
                manager,
                "_start_submission_thread",
                lambda job_id, thread_name: submissions.append(job_id) or True,
            )

            continued = manager.continue_job(record.id)
            assert continued.status == JobStatus.QUEUED
            assert submissions == [record.id]

            # the continue receipt is protocol-compatible and points at attempt 1
            source = resolve_resume_source(work_dir)
            assert source is not None and source.compatible is True
            receipt = json.loads((work_dir / "resume_source.json").read_text(encoding="utf-8"))
            assert receipt["previous_attempt"] == 1
            assert receipt["compatible"] is True

            result = CalculationPlanExecutor().execute(plan, work_dir)

        assert sp_calls == [1], "step 1 primitive must not re-run after scheduler continue"
        assert result.step_states[0].status == "completed"
        assert result.step_states[0].executed_this_run is False
        assert result.step_states[0].reused_from_attempt == 1
    finally:
        manager.shutdown()


def _batch_items() -> list[object]:
    from acp.calculations.batch._items import BatchStructureItem

    return [
        BatchStructureItem(
            item_id="cand_a",
            name="A",
            tag="INT",
            xyz="2\nTAG: INT\nH 0.0 0.0 0.0\nH 0.0 0.0 0.70\n",
            candidate_id="cand_a",
        ),
        BatchStructureItem(
            item_id="cand_b",
            name="B",
            tag="INT",
            xyz="2\nTAG: INT\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74\n",
            candidate_id="cand_b",
        ),
    ]


def _run_batch_once(engine, items, *, fail_item: str | None, monkeypatch):  # type: ignore[no-untyped-def]
    """Run the batch, optionally raising inside one item's processing."""
    original = getattr(engine, "_v02_original_process", None)
    if original is None:
        original = engine._process_item
        setattr(engine, "_v02_original_process", original)
    executed: list[str] = []

    def wrapped(item, record, steps, charge, multiplicity):  # type: ignore[no-untyped-def]
        if fail_item is not None and record.item_id == fail_item:
            raise RuntimeError(f"synthetic failure for {record.item_id}")
        executed.append(record.item_id)
        return original(item, record, steps, charge, multiplicity)

    monkeypatch.setattr(engine, "_process_item", wrapped)
    return engine.run(list(items), profile="opt_only", charge=0), executed


def _archive_batch_science(task_root: Path, *, item_ids: list[str]) -> Path:
    """Move checkpoint + per-item step_result into ``attempts/1/`` (contract B)."""
    work = task_root / "WORK"
    archive = work / "00_RUNTIME" / "attempts" / "1"
    checkpoint = work / "00_RUNTIME" / "checkpoint.json"
    assert checkpoint.is_file()
    (archive / "WORK" / "00_RUNTIME").mkdir(parents=True, exist_ok=True)
    checkpoint.rename(archive / "WORK" / "00_RUNTIME" / "checkpoint.json")
    for item_id in item_ids:
        src = work / "03_OPT" / "batch" / item_id / "step_result.json"
        if not src.is_file():
            continue
        dest = archive / "WORK" / "03_OPT" / "batch" / item_id / "step_result.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dest)
    return archive


def test_batch_resume_reads_resume_source(
    tmp_path: Path, fake_backend: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Batch continue adopts completed items from the ARCHIVED step_result."""
    from acp.calculations.batch._items import BatchStructureItem
    from acp.calculations.batch.engine import BatchOptimizeEngine

    assert isinstance(BatchStructureItem, type)
    task_root = tmp_path / "task"
    work_root = task_root / "WORK"
    items = _batch_items()
    engine = BatchOptimizeEngine(work_root=work_root, result_root=task_root / "RESULT")

    outcome1, executed1 = _run_batch_once(engine, items, fail_item="cand_b", monkeypatch=monkeypatch)
    assert executed1 == ["cand_a"]
    statuses1 = {r.item_id: r.status for r in outcome1.manifest.items}
    assert statuses1 == {"cand_a": "completed", "cand_b": "failed"}

    # per-item step_result self-certifies the resolved cache identity
    item_step_result = work_root / "03_OPT" / "batch" / "cand_a" / "step_result.json"
    assert item_step_result.is_file()
    certified = json.loads(item_step_result.read_text(encoding="utf-8"))
    assert certified["schema_version"] == STEP_RESULT_SCHEMA_VERSION
    assert certified["step_identity"].startswith("sha256:")

    _archive_batch_science(task_root, item_ids=["cand_a"])
    # active dir no longer owns the science — only resume_source.json does
    assert not (work_root / "00_RUNTIME" / "checkpoint.json").exists()
    assert not item_step_result.exists()
    (task_root / "resume_source.json").write_text(
        json.dumps(
            {
                "attempt": 2,
                "previous_attempt": 1,
                "continued_from": "failed",
                "compatible": True,
            }
        ),
        encoding="utf-8",
    )

    outcome2, executed2 = _run_batch_once(engine, items, fail_item=None, monkeypatch=monkeypatch)

    assert executed2 == ["cand_b"], "completed item adopted from the archived attempt"
    statuses2 = {r.item_id: r.status for r in outcome2.manifest.items}
    assert statuses2 == {"cand_a": "skipped", "cand_b": "completed"}
    # checkpoint rebuilt in the active dir for the new attempt
    assert (work_root / "00_RUNTIME" / "checkpoint.json").is_file()


def test_batch_protocol_predicate_ignores_identity_schema(tmp_path: Path) -> None:
    """Batch compatibility is judged by per-item step_result — never schema=1."""
    root = tmp_path / "task"
    runtime = root / "WORK" / "00_RUNTIME"
    runtime.mkdir(parents=True)
    # batch checkpoints are pinned at identity_schema=1 by contract
    write_checkpoint(
        runtime,
        Checkpoint(
            task_id="batch",
            workflow="BatchOptimize",
            plan_fingerprint="fp",
            step_states=[],
            items_state={"cand_a": {"status": "completed", "cache_key": "sha256:x"}},
            resume_count=1,
            identity_schema=1,
        ),
    )

    ok, reason = check_resume_protocol(root, kind="batch")
    assert ok is False and "step_result" in reason, reason
    # the executor predicate DOES consult identity_schema — batch must not
    ok_x, reason_x = check_resume_protocol(root, kind="executor")
    assert ok_x is False and "identity_schema" in reason_x, reason_x

    sr_dir = root / "WORK" / "03_OPT" / "batch" / "cand_a"
    sr_dir.mkdir(parents=True)
    write_step_result(
        sr_dir / "step_result.json",
        {"schema_version": STEP_RESULT_SCHEMA_VERSION, "step_identity": "sha256:x"},
    )

    ok_batch, reason_batch = check_resume_protocol(root, kind="batch")
    assert ok_batch is True, reason_batch  # schema=1 did NOT make it incompatible
    ok_executor, reason_executor = check_resume_protocol(root, kind="executor")
    assert ok_executor is False and "identity_schema" in reason_executor, reason_executor


def test_archive_protocol_incompatible_not_adopted(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-protocol attempt → ``compatible:false`` + event + full recompute."""
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
    from acp.scheduler.manager import JobManager

    manager = JobManager(run_root=tmp_path / "runs", poll_interval=30)
    work_dir = tmp_path / "runs" / "singlepoint-task"
    work_dir.mkdir(parents=True)
    (work_dir / "job.json").write_text("{}", encoding="utf-8")
    (work_dir / "task.json").write_text("{}", encoding="utf-8")
    record = JobRecord(
        id="v02-incompatible",
        spec=JobSpec(workflow="singlepoint", name="v02-incompatible"),
        status=JobStatus.FAILED,
        work_dir=str(work_dir),
        exit_code=1,
    )
    manager.store.create(record)

    try:
        plan = _plan(work_dir)
        sp_calls: list[int] = []
        with patch.dict(executor_module._PRIMITIVE_DISPATCH, _dispatcher(sp_calls)):
            CalculationPlanExecutor().execute(plan, work_dir)
            assert sp_calls == [1]

            # the "old attempt" predates the V02 protocol markers
            (work_dir / "WORK" / "05_SP" / "step_result.json").unlink()

            persisted = manager.get(record.id)
            assert persisted is not None
            persisted.status = JobStatus.FAILED
            manager.store.update(persisted)
            monkeypatch.setattr(
                manager, "_start_submission_thread", lambda job_id, name: True
            )

            with caplog.at_level(logging.INFO):
                manager.continue_job(record.id)

            receipt = json.loads((work_dir / "resume_source.json").read_text(encoding="utf-8"))
            assert receipt["compatible"] is False
            assert "step_result" in receipt.get("incompatible_reason", "")

            events = (work_dir / "WORK" / "00_RUNTIME" / "events.jsonl").read_text(
                encoding="utf-8"
            )
            assert "recovery.protocol_incompatible" in events

            with caplog.at_level(logging.INFO):
                CalculationPlanExecutor().execute(plan, work_dir)

        # no silent reuse: the whole attempt recomputes
        assert len(sp_calls) == 2, "incompatible attempt must fully recompute"
        assert "recovery.protocol_incompatible" in caplog.text
    finally:
        manager.shutdown()


def test_archive_protocol_incompatible_not_adopted_batch(
    tmp_path: Path,
    fake_backend: object,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Batch variant: no per-item step_result → incompatible → full recompute."""
    from acp.calculations.batch.engine import BatchOptimizeEngine

    task_root = tmp_path / "task"
    work_root = task_root / "WORK"
    items = _batch_items()
    engine = BatchOptimizeEngine(work_root=work_root, result_root=task_root / "RESULT")

    outcome1, _ = _run_batch_once(engine, items, fail_item="cand_b", monkeypatch=monkeypatch)
    statuses1 = {r.item_id: r.status for r in outcome1.manifest.items}
    assert statuses1 == {"cand_a": "completed", "cand_b": "failed"}

    # old attempt: checkpoint archived, per-item step_result NOT present
    archive = _archive_batch_science(task_root, item_ids=[])
    stale = work_root / "03_OPT" / "batch" / "cand_a" / "step_result.json"
    stale.unlink()
    assert not stale.exists()
    (task_root / "resume_source.json").write_text(
        json.dumps({"attempt": 2, "previous_attempt": 1, "continued_from": "failed"}),
        encoding="utf-8",
    )
    # the batch predicate judges this attempt incompatible (schema=1 irrelevant)
    ok, reason = check_resume_protocol(archive, kind="batch")
    assert ok is False and "step_result" in reason

    with caplog.at_level(logging.INFO):
        outcome2, executed2 = _run_batch_once(
            engine, items, fail_item=None, monkeypatch=monkeypatch
        )

    assert sorted(executed2) == ["cand_a", "cand_b"], "no per-item result → full recompute"
    assert all(r.status == "completed" for r in outcome2.manifest.items)
    assert "recovery.protocol_incompatible" in caplog.text
