"""Reproduce observed defects using real ACP code and synthetic QC output.

This script does not connect to the production manager or execute ORCA.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[2] / "src"))

import numpy as np

from acp.api.v1_routes import _enrich_job_snapshot, _record_to_v1_model
from acp.backends.base import QCResult
from acp.calculations.batch.engine import batch_stage_names
from acp.calculations.primitives._common import CalculationInputs
from acp.calculations.primitives.optimize import _run_attempt
from acp.calculations.progress import LiveMetric, ProgressReporter
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.stage_tasks import PlanCompiler
from cccp.qc.interfaces.orca import ORCAInterface

findings = {}
with TemporaryDirectory(prefix=".test_tmp_repro_", dir=ROOT) as temporary:
    tmp = Path(temporary)
    reporter = ProgressReporter(tmp / "phase", stages=batch_stage_names("opt_freq_sp_thermo"))
    reporter.start_stage("optimize")
    reporter.set_live_metrics([
        LiveMetric(key="batch_item", value="1 / 3", kind="count", priority=100),
    ])
    during_frequency = {}

    class SyntheticTSBackend:
        def transition_state_opt(self, coordinates, symbols, **kwargs):
            callback = kwargs["output_callback"]
            for line in [
                "CYCLE 47",
                "FINAL SINGLE POINT ENERGY -670.927457901471",
                "*** THE OPTIMIZATION HAS CONVERGED ***",
                "ORCA NUMERICAL FREQUENCIES",
                "<< Calculating gradient on displaced geometry 160 (of 168) >>",
            ]:
                callback(line)
            during_frequency.update(json.loads((tmp / "phase" / "state.json").read_text()))
            return QCResult(success=True, coordinates=coordinates, symbols=symbols)

    inputs = CalculationInputs(np.zeros((1, 3)), ("C",), 0, 1)
    _run_attempt(
        SyntheticTSBackend(), "transition_state_opt", inputs, tmp / "opt", {},
        selected_backend="orca", progress_reporter=reporter,
    )
    assert during_frequency["current_stage"] == "optimize"
    assert during_frequency["stages"]["frequency"]["status"] == "pending"
    assert during_frequency["overall_progress"] == 0
    assert "batch_item" not in [metric["key"] for metric in during_frequency["live_metrics"]]
    findings["embedded_frequency_without_stage_transition"] = during_frequency

    spec = JobSpec(workflow="BatchOptimize", method={"profile": "opt_freq_sp_thermo"})
    reporter_stages = batch_stage_names("opt_freq_sp_thermo")
    scheduler_stages = [stage.stage_name for stage in PlanCompiler.compile(spec)]
    assert len(reporter_stages) == 4 and len(scheduler_stages) == 6
    findings["stage_contract_mismatch"] = {
        "reporter": reporter_stages, "scheduler": scheduler_stages,
    }

    batch = ProgressReporter(tmp / "batch", stages=reporter_stages)
    for stage in reporter_stages:
        batch.start_stage(stage)
        batch.complete_stage(stage)
    end_first = batch._overall_progress()
    batch.start_stage("optimize")
    start_second = batch._overall_progress()
    assert end_first == 1 and start_second == 0.75
    findings["multi_item_overall_progress_regresses"] = {
        "after_first_item": end_first, "start_second_item": start_second,
    }

    cache_dir = tmp / "cache"
    cache_dir.mkdir()
    (cache_dir / "state.json").write_text(json.dumps({
        "current_stage": "optimize", "stage_index": 1, "stage_total": 4,
        "progress_state": "indeterminate", "stages": {"optimize": {"status": "running"}},
    }))
    first = JobRecord(
        id="audit_cache_case", spec=spec, status=JobStatus.RUNNING,
        work_dir=str(cache_dir), updated_at="2026-10-01T14:22:37+08:00",
        current_stage="optimize", progress=0,
    )
    initial = _enrich_job_snapshot(first, _record_to_v1_model(first), include_event=True)
    later = replace(first, updated_at="2026-10-01T14:52:00+08:00")
    cached = _enrich_job_snapshot(later, _record_to_v1_model(later), include_event=True)
    assert cached.updated_at == initial.updated_at != later.updated_at
    findings["full_model_cache_returns_old_db_timestamp"] = {
        "db_updated_at": later.updated_at, "response_updated_at": cached.updated_at,
    }

    interface = object.__new__(ORCAInterface)
    interface.method = "wB97X-D4"
    interface.basis = "def2-SVP"
    interface.nproc = 16

    def fake_run(_input, output, **_kwargs):
        output.write_text("Synthetic failure: no real QC executable invoked\n")
        return False

    interface._run_orca = fake_run
    interface.transition_state_opt(
        np.zeros((1, 3)), ["C"], output_dir=tmp / "ts_input",
        scf_maxiter=300, scf_convergence="tight", scf_strategy="normal",
        scf_options={"hf_typ": "UHF", "guess_mix_angle": 45, "no_use_sym": True},
    )
    generated = (tmp / "ts_input" / "ts_opt.inp").read_text()
    assert "NumFreq" in generated
    assert "%scf" not in generated and "GuessMix" not in generated
    assert "NoUseSym" not in generated and "300" not in generated
    findings["ts_input_discards_electronic_and_scf_options"] = generated

destination = ROOT / "evidence" / "python_reproductions.json"
destination.write_text(json.dumps(findings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"5 reproduction checks confirmed; evidence: {destination}")
