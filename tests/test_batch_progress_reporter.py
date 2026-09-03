from __future__ import annotations

import json
from pathlib import Path

import pytest

import acp.calculations.batch.engine as batch_engine
import acp.cli as acp_cli
from acp.calculations.batch.models import BatchStructureItem
from acp.calculations.contracts import ArtifactRef, CalculationRequest, CalculationResult
from acp.calculations.progress import ProgressReporter
from acp.core.workflow import WorkflowResult


def test_batch_reporter_tracks_three_items_and_profile_steps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    items = [
        BatchStructureItem(
            item_id=f"item-{index}",
            name=f"Item {index}",
            xyz="1\nitem\nH 0.0 0.0 0.0\n",
        )
        for index in range(1, 4)
    ]
    state_path = tmp_path / "state.json"
    observed: list[tuple[str, str]] = []

    def observe(step: str) -> None:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["stages"][step]["status"] == "running"
        observed.append((step, state["live_metrics"][0]["value"]))

    def fake_optimize(_request: CalculationRequest, **_kwargs) -> CalculationResult:
        observe("optimize")
        return CalculationResult(coords=[[0.0, 0.0, 0.0]])

    def fake_frequency(request: CalculationRequest) -> CalculationResult:
        observe("frequency")
        output_dir = Path(str(request.resources["output_dir"]))
        frequency_log = output_dir / "frequency.out"
        frequency_log.write_text("frequency\n", encoding="utf-8")
        return CalculationResult(
            coords=[[0.0, 0.0, 0.0]],
            frequencies=[-10.0, 20.0],
            artifacts=[ArtifactRef(path=frequency_log, type="frequency_log")],
        )

    def fake_singlepoint(_request: CalculationRequest) -> CalculationResult:
        observe("single_point")
        return CalculationResult(coords=[[0.0, 0.0, 0.0]], energy=-1.0)

    class FakeThermochemistryCalculator:
        def __init__(self, **_kwargs) -> None:
            pass

        def compute(self, **_kwargs) -> CalculationResult:
            observe("thermochemistry")
            return CalculationResult()

    monkeypatch.setattr(batch_engine, "run_optimize", fake_optimize)
    monkeypatch.setattr(batch_engine, "run_frequency", fake_frequency)
    monkeypatch.setattr(batch_engine, "run_singlepoint", fake_singlepoint)
    monkeypatch.setattr(batch_engine, "ThermochemistryCalculator", FakeThermochemistryCalculator)

    reporter = ProgressReporter(
        tmp_path,
        job_name="BatchOptimize",
        stages=["optimize", "frequency", "single_point", "thermochemistry"],
    )
    outcome = batch_engine.BatchOptimizeEngine(
        work_root=tmp_path / "WORK",
        result_root=tmp_path / "RESULT",
    ).run(items, profile="opt_freq_sp_thermo", progress_reporter=reporter)

    assert outcome.manifest.counts["completed"] == 3
    assert observed == [
        (step, item_value)
        for item_value in ("1 / 3", "2 / 3", "3 / 3")
        for step in ("optimize", "frequency", "single_point", "thermochemistry")
    ]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["current_stage"] is None
    assert [state["stages"][step]["status"] for step in ("optimize", "frequency")] == [
        "completed",
        "completed",
    ]
    assert all(
        state["stages"][step]["status"] == "completed"
        for step in ("optimize", "frequency", "single_point", "thermochemistry")
    )


def test_batch_optimize_passes_reporter_to_optimization_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    item = BatchStructureItem(
        item_id="item-1",
        name="Item 1",
        xyz="1\nitem\nH 0.0 0.0 0.0\n",
    )
    reporter = ProgressReporter(tmp_path, stages=["optimize"])
    received: list[ProgressReporter | None] = []

    def fake_optimize(
        _request: CalculationRequest,
        *,
        progress_reporter: ProgressReporter | None = None,
    ) -> CalculationResult:
        received.append(progress_reporter)
        return CalculationResult(coords=[[0.0, 0.0, 0.0]])

    monkeypatch.setattr(batch_engine, "run_optimize", fake_optimize)
    outcome = batch_engine.BatchOptimizeEngine(
        work_root=tmp_path / "WORK",
        result_root=tmp_path / "RESULT",
    ).run([item], profile="opt_only", progress_reporter=reporter)

    assert outcome.items[0].status == "completed"
    assert received == [reporter]


def test_batch_reporter_marks_failed_item_step_and_keeps_metrics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    items = [
        BatchStructureItem(
            item_id=f"item-{index}",
            name=f"Item {index}",
            xyz="1\nitem\nH 0.0 0.0 0.0\n",
        )
        for index in range(1, 4)
    ]
    state_path = tmp_path / "state.json"

    def fake_optimize(request: CalculationRequest, **_kwargs) -> CalculationResult:
        if request.resources["trajectory_item_id"] == "item-2":
            raise RuntimeError("stub item failure")
        return CalculationResult(coords=[[0.0, 0.0, 0.0]])

    class RecordingReporter(ProgressReporter):
        def __init__(self) -> None:
            super().__init__(tmp_path, stages=["optimize"])
            self.failed_states = []

        def fail_stage(self, name: str, error: str) -> None:
            super().fail_stage(name, error)
            self.failed_states.append(json.loads(state_path.read_text(encoding="utf-8")))

    monkeypatch.setattr(batch_engine, "run_optimize", fake_optimize)
    reporter = RecordingReporter()

    outcome = batch_engine.BatchOptimizeEngine(
        work_root=tmp_path / "WORK",
        result_root=tmp_path / "RESULT",
    ).run(items, profile="opt_only", progress_reporter=reporter)

    assert [item.status for item in outcome.items] == ["completed", "failed", "completed"]
    assert len(reporter.failed_states) == 1
    failure_state = reporter.failed_states[0]
    assert failure_state["current_stage"] == "optimize"
    assert failure_state["stages"]["optimize"]["status"] == "failed"
    assert failure_state["stages"]["optimize"]["error"] == "stub item failure"
    assert failure_state["live_metrics"] == [
        {
            "key": "batch_item",
            "label_key": "live.batch_item",
            "label": None,
            "value": "2 / 3",
            "kind": "count",
            "priority": 100,
            "detail": None,
        },
        {
            "key": "batch_step",
            "label_key": "live.batch_step",
            "label": "结构优化",
            "value": "optimize",
            "kind": "status",
            "priority": 90,
            "detail": None,
        },
    ]


def test_batch_without_reporter_does_not_write_state_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    items = [
        BatchStructureItem(
            item_id=f"item-{index}",
            name=f"Item {index}",
            xyz="1\nitem\nH 0.0 0.0 0.0\n",
        )
        for index in range(1, 4)
    ]

    def fake_optimize(_request: CalculationRequest) -> CalculationResult:
        return CalculationResult(coords=[[0.0, 0.0, 0.0]])

    monkeypatch.setattr(batch_engine, "run_optimize", fake_optimize)
    outcome = batch_engine.BatchOptimizeEngine(
        work_root=tmp_path / "WORK",
        result_root=tmp_path / "RESULT",
    ).run(items, profile="opt_only")

    assert [item.status for item in outcome.items] == ["completed", "completed", "completed"]
    assert not (tmp_path / "state.json").exists()


def test_empty_batch_has_no_progress_metrics(tmp_path: Path) -> None:
    reporter = ProgressReporter(tmp_path, stages=["optimize"])

    outcome = batch_engine.BatchOptimizeEngine(
        work_root=tmp_path / "WORK",
        result_root=tmp_path / "RESULT",
    ).run([], profile="opt_only", progress_reporter=reporter)

    assert outcome.items == []
    assert not (tmp_path / "state.json").exists()


def test_batch_cli_constructs_reporter_for_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output_dir = tmp_path / "batch-output"
    items_file = tmp_path / "items.xyz"
    items_file.write_text("1\nitem\nH 0.0 0.0 0.0\n", encoding="utf-8")
    args = acp_cli.build_parser().parse_args(
        [
            "run",
            "BatchOptimize",
            "--items-file",
            str(items_file),
            "--profile",
            "opt_freq_sp",
            "--output",
            str(output_dir),
        ]
    )
    captured = {}

    def fake_run_batch_optimize(source, **kwargs):
        captured["source"] = source
        captured.update(kwargs)
        return WorkflowResult(status="completed", metadata={})

    import acp.workflows.batch_optimize as batch_workflow

    monkeypatch.setattr(batch_workflow, "run_batch_optimize", fake_run_batch_optimize)

    assert acp_cli._handle_batch_optimize(args) == 0

    reporter = captured["progress_reporter"]
    assert isinstance(reporter, ProgressReporter)
    assert captured["output_dir"] == output_dir
    state = json.loads((output_dir / "state.json").read_text(encoding="utf-8"))
    assert list(state["stages"]) == ["optimize", "frequency", "single_point"]
    assert state["status"] == "completed"
