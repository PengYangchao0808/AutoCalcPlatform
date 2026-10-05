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
from pathlib import Path
from unittest.mock import patch

import pytest

from acp.calculations import executor as executor_module
from acp.calculations.checkpoint import load_checkpoint, write_checkpoint
from acp.calculations.contracts import (
    CalculationPlan,
    CalculationResult,
    CalculationStep,
    Checkpoint,
    StepKind,
    StructureArtifact,
)
from acp.calculations.executor import CalculationPlanExecutor
from acp.calculations.identity import IDENTITY_SCHEMA

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
