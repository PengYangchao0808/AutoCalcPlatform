"""Regression coverage for the OPT/FREQ progress synchronization incident."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from acp.api.v1_routes import _enrich_job_snapshot, _overlay_state_on_entries, _record_to_v1_model
from acp.calculations.batch.engine import BatchOptimizeEngine, batch_stage_names
from acp.calculations.batch.models import BatchStructureItem
from acp.calculations.contracts import CalculationResult
from acp.calculations.progress import LiveMetric, ProgressReporter
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.stage_tasks import PlanCompiler, StageTaskObserver, StageTaskStore
from cccp.qc.interfaces.orca import ORCAInterface


@pytest.mark.parametrize("profile", ["opt_only", "opt_freq", "opt_freq_sp", "opt_freq_sp_thermo"])
def test_batch_stage_authorities_agree(profile):
    spec = JobSpec(workflow="BatchOptimize", method={"profile": profile})
    assert [stage.stage_name for stage in PlanCompiler.compile(spec)] == batch_stage_names(profile)


def test_ts_input_consumes_spin_scf_and_resource_settings(sample_config, monkeypatch, tmp_path):
    interface = ORCAInterface(sample_config, method="wB97X-D4", basis="def2-SVP")

    def fake_run(_input, output, **_kwargs):
        output.write_text("Synthetic failure, no actual ORCA run\n")
        return False

    monkeypatch.setattr(interface, "_run_orca", fake_run)
    interface.transition_state_opt(
        np.zeros((1, 3)), ["C"], output_dir=tmp_path,
        calculate_frequencies=False, scf_maxiter=300, scf_convergence="tight",
        scf_strategy="slowconv", opt_level="tight", trust_radius=0.3,
        scf_options={"hf_typ": "UHF", "guess_mix_angle": 45, "no_use_sym": True},
    )
    text = (tmp_path / "ts_opt.inp").read_text()
    for token in ("OptTS", "TightOpt", "TightSCF", "SlowConv", "HFTyp UHF", "GuessMix 45", "NoUseSym", "MaxIter 300", "%maxcore", "Trust 0.3"):
        assert token in text
    assert "NumFreq" not in text


def test_batch_ts_has_one_explicit_frequency_step(monkeypatch, tmp_path):
    import acp.calculations.batch.engine as engine
    seen = []

    def optimize(request, **_kwargs):
        assert request.resources["calculate_frequencies"] is False
        seen.append("optimize")
        return CalculationResult(coords=[[0.0, 0.0, 0.0]])

    def frequency(_request):
        seen.append("frequency")
        return CalculationResult(coords=[[0.0, 0.0, 0.0]], frequencies=[-100.0, 50.0])

    monkeypatch.setattr(engine, "run_optimize", optimize)
    monkeypatch.setattr(engine, "run_frequency", frequency)
    item = BatchStructureItem(item_id="one", name="TS", tag="TS", xyz="1\nTS\nC 0 0 0\n")
    outcome = BatchOptimizeEngine(work_root=tmp_path / "WORK", result_root=tmp_path / "RESULT").run([item], profile="opt_freq")
    assert outcome.items[0].status == "completed"
    assert seen == ["optimize", "frequency"]


def test_batch_progress_aggregates_items_and_preserves_context(tmp_path):
    reporter = ProgressReporter(tmp_path)
    stages = batch_stage_names("opt_freq")
    reporter.configure_batch(2, stages)
    reporter.begin_batch_item(0)
    reporter.start_stage("optimize")
    reporter.set_live_metrics([LiveMetric(key="batch_item", value="1 / 2", kind="count", priority=100)])
    reporter.update_live_metrics([
        LiveMetric(key="opt_step", value="Step 47", kind="iteration", priority=100),
        LiveMetric(key="opt_convergence", value="converged", kind="status", priority=90),
    ])
    snapshot = json.loads((tmp_path / "state.json").read_text())
    assert {metric["key"] for metric in snapshot["live_metrics"]} == {"batch_item", "opt_step", "opt_convergence"}
    fractions = []
    for index in range(2):
        reporter.begin_batch_item(index)
        for stage in stages:
            reporter.start_stage(stage)
            fractions.append(reporter._overall_progress())
            reporter.complete_stage(stage)
            fractions.append(reporter._overall_progress())
        reporter.finish_batch_item(index)
    assert fractions == sorted(fractions)
    assert fractions[3] == 0.5
    assert fractions[-1] == 1.0
    assert json.loads((tmp_path / "state.json").read_text())["stage_order"] == stages


def test_failed_and_skipped_units_do_not_overflow_progress(tmp_path):
    reporter = ProgressReporter(tmp_path, stages=["optimize", "frequency"])
    reporter.configure_batch(2, ["optimize", "frequency"])
    reporter.begin_batch_item(0)
    reporter.start_stage("optimize")
    reporter.update_stage("optimize", completed=1, total=2)
    reporter.fail_stage("optimize", "synthetic failure")
    reporter.finish_batch_item(0)
    assert reporter._overall_progress() == 0.5
    reporter.begin_batch_item(1)
    reporter.finish_batch_item(1)  # cached item
    assert reporter._overall_progress() == 1.0


def test_snapshot_cache_merges_current_db_fields(tmp_path):
    reporter = ProgressReporter(tmp_path, stages=["optimize", "frequency"])
    reporter.start_stage("optimize")
    record = JobRecord(id="progress-cache-regression", spec=JobSpec(workflow="BatchOptimize"), status=JobStatus.RUNNING, work_dir=str(tmp_path), updated_at="old", result={"attempts": 1})
    _enrich_job_snapshot(record, _record_to_v1_model(record), include_event=True)
    updated = replace(record, updated_at="new", result={"attempts": 2})
    enriched = _enrich_job_snapshot(updated, _record_to_v1_model(updated), include_event=True)
    assert enriched.updated_at == "new"
    assert enriched.result == {"attempts": 2}
    assert enriched.stage_order == ["optimize", "frequency"]
    terminal = replace(updated, status=JobStatus.COMPLETED)
    completed = _enrich_job_snapshot(terminal, _record_to_v1_model(terminal), include_event=True)
    assert completed.progress == 1 and completed.progress_state == "determinate"


def test_stage_lifecycle_mirrors_state_and_resets_for_next_item(tmp_path):
    store = StageTaskStore(tmp_path / "jobs.db")
    observer = StageTaskObserver(store)
    observer.initialize_job_stages("job", JobSpec(workflow="BatchOptimize", method={"profile": "opt_freq"}))
    reporter = ProgressReporter(tmp_path, stages=["optimize", "frequency"])
    reporter.configure_batch(2, ["optimize", "frequency"])
    reporter.begin_batch_item(0)
    reporter.start_stage("optimize")
    observer.poll_and_mirror("job", tmp_path)
    tasks = store.list_by_job("job")
    assert tasks[0].state == "running" and tasks[0].started_at
    reporter.complete_stage("optimize")
    observer.poll_and_mirror("job", tmp_path)
    assert store.list_by_job("job")[0].completed_at
    reporter.begin_batch_item(1)
    reporter.start_stage("optimize")
    observer.poll_and_mirror("job", tmp_path)
    task = store.list_by_job("job")[0]
    assert task.state == "running" and task.completed_at is None


def test_detail_overlay_retains_reporter_times(tmp_path):
    store = StageTaskStore(tmp_path / "jobs.db")
    observer = StageTaskObserver(store)
    tasks = observer.initialize_job_stages("job", JobSpec(workflow="BatchOptimize", method={"profile": "opt_only"}))
    entries = _overlay_state_on_entries(tasks, {"optimize": {"status": "completed", "started_at": "start", "completed_at": "finish"}})
    assert entries[0].started_at == "start"
    assert entries[0].completed_at == "finish"


def test_batch_detail_uses_current_item_even_before_observer_poll(tmp_path):
    store = StageTaskStore(tmp_path / "jobs.db")
    tasks = StageTaskObserver(store).initialize_job_stages("job", JobSpec(workflow="BatchOptimize", method={"profile": "opt_only"}))
    tasks[0].state = "completed"
    tasks[0].completed_at = "previous-item-finish"
    entries = _overlay_state_on_entries(tasks, {"optimize": {"status": "running", "started_at": "new-item-start"}}, prefer_state=True)
    assert entries[0].status == "running"
    assert entries[0].completed_at is None


def test_workbench_progress_and_timeline():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    root = Path(__file__).parents[1]
    subprocess.run([node, str(root / "tests/fixtures/progress_sync_regression.cjs"), str(root / "frontend/ACP_Workbench_v2.html")], check=True, capture_output=True, text=True)


def test_opt_only_does_not_return_initial_hessian_frequencies(sample_config, monkeypatch, tmp_path):
    from tests.test_cccp_orca_ts_extensions import TS_OUTPUT, TS_COORDINATES, TS_SYMBOLS

    interface = ORCAInterface(sample_config)

    def fake_run(_input, output, **_kwargs):
        output.write_text(TS_OUTPUT)
        return True

    monkeypatch.setattr(interface, "_run_orca", fake_run)
    result = interface.transition_state_opt(TS_COORDINATES, TS_SYMBOLS, output_dir=tmp_path, calculate_frequencies=False)
    assert result.success
    assert result.all_frequencies == [] and result.imaginary_frequencies == []
