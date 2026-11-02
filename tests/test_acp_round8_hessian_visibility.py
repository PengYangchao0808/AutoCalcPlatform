"""Round-8 三.5(1): make the duplicated numerical Hessian visible.

The external agent noticed that a TS ``opt_freq_sp`` run computes the Hessian
twice — once for the optimization's initial Hessian (TS default
``opt_initial_hessian="calculate"``) and again in the independent FREQUENCY step
(ORCA ``NumFreq``) — and nothing in the status/step projection said so.

Round-8 deliberately changes **no** optimization/frequency default (Hessian
*reuse* is a deferred scientific decision: it needs real ORCA validation), it
only publishes the observed cost:

* ``hessian_recomputed: true`` + ``hessian_source: "freq_numfreq"`` when the
  item ran a completed frequency step;
* ``hessian_source: "opt_initial"`` when the optimization itself asked ORCA to
  calculate an initial Hessian and no frequency step ran;
* neither key when no Hessian was provably computed (no claim, no zeros).

These tests also pin that the defaults and the profile DAG are untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from acp.calculations.batch._items import BatchCalculationItem, BatchStructureItem
from acp.calculations.batch.engine import BatchOptimizeEngine
from acp.calculations.batch.models import BatchCalculationManifest
from acp.calculations.batch.options import BatchMethodOptions
from acp.calculations.contracts import CalculationResult, StepKind

_XYZ = (
    "2\nTAG: INT\n"
    "H 0.0000000000 0.0000000000 0.0000000000\n"
    "H 0.0000000000 0.0000000000 0.7400000000\n"
)


def _engine(tmp_path: Path) -> BatchOptimizeEngine:
    return BatchOptimizeEngine(
        work_root=tmp_path / "task" / "WORK",
        result_root=tmp_path / "task" / "RESULT",
    )


def _item(tmp_path: Path, *, tag: str = "INT", frequency_status: str | None = "completed"):
    item_id = "ts_001" if tag == "TS" else "int_001"
    geometry = (
        tmp_path / "task" / "RESULT" / "structures" / f"{item_id}__TAG_{tag}__optimized.xyz"
    )
    geometry.parent.mkdir(parents=True, exist_ok=True)
    geometry.write_text(_XYZ, encoding="utf-8")
    frequency: dict[str, object] = {}
    if frequency_status is not None:
        frequency = {"frequencies": [102.33, 144.02], "status": frequency_status}
    return BatchCalculationItem(
        item_id=item_id,
        candidate_id=item_id,
        name=tag,
        tag=tag,
        status="completed",
        optimized_xyz=geometry.relative_to(tmp_path / "task" / "RESULT").as_posix(),
        frequency=frequency,
    )


def _structure_metadata(tmp_path: Path, item: BatchCalculationItem, profile: str) -> dict:
    import json

    engine = _engine(tmp_path)
    engine._materialize_result_products(
        BatchCalculationManifest(profile=profile, items=[item])
    )
    manifest = json.loads(
        (tmp_path / "task" / "RESULT" / "result_manifest.json").read_text(encoding="utf-8")
    )
    product = next(p for p in manifest["products"] if p["id"] == f"batch_{item.item_id}")
    return product["metadata"]


# ── batch projection ────────────────────────────────────────────────────


def test_frequency_step_marks_the_recomputed_hessian(tmp_path: Path) -> None:
    metadata = _structure_metadata(tmp_path, _item(tmp_path), "opt_freq_sp")

    assert metadata["hessian_recomputed"] is True
    assert metadata["hessian_source"] == "freq_numfreq"


def test_step_result_records_the_recomputed_hessian(tmp_path: Path) -> None:
    import json

    engine = _engine(tmp_path)
    record = _item(tmp_path)
    structure_item = BatchStructureItem(
        item_id=record.item_id, name=record.name, tag=record.tag, xyz=_XYZ
    )
    engine._write_item_step_result(
        structure_item, record, index=0, profile="opt_freq_sp", workflow="BatchOptimize"
    )

    payload = json.loads(
        (
            tmp_path / "task" / "WORK" / "03_OPT" / "batch" / "int_001" / "step_result.json"
        ).read_text(encoding="utf-8")
    )
    assert payload["metadata"]["hessian_recomputed"] is True
    assert payload["metadata"]["hessian_source"] == "freq_numfreq"


def test_ts_optimization_without_frequency_reports_the_initial_hessian(tmp_path: Path) -> None:
    """TS role default ``opt_initial_hessian="calculate"`` computes one."""
    engine = _engine(tmp_path)
    item = _item(tmp_path, tag="TS", frequency_status=None)

    assert engine._hessian_facts(item) == {
        "hessian_recomputed": True,
        "hessian_source": "opt_initial",
    }


def test_int_without_frequency_makes_no_hessian_claim(tmp_path: Path) -> None:
    """The INT role default leaves ORCA's model Hessian: no recompute claimed."""
    engine = _engine(tmp_path)
    item = _item(tmp_path, tag="INT", frequency_status=None)

    assert engine._hessian_facts(item) == {}


def test_int_initial_hessian_override_is_reported(tmp_path: Path) -> None:
    engine = BatchOptimizeEngine(
        work_root=tmp_path / "task" / "WORK",
        result_root=tmp_path / "task" / "RESULT",
        methods=BatchMethodOptions(opt_initial_hessian="calculate"),
    )
    item = _item(tmp_path, tag="INT", frequency_status=None)

    assert engine._hessian_facts(item)["hessian_source"] == "opt_initial"


# ── simple frequency workflow projection ────────────────────────────────


def _execution(status: str) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        errors=[],
        step_states=[
            SimpleNamespace(
                kind=StepKind.FREQUENCY,
                status=status,
                result=CalculationResult(frequencies=[100.0, 200.0]),
            )
        ]
    )


def test_simple_frequency_workflow_exposes_the_recomputed_hessian(tmp_path: Path) -> None:
    from acp.workflows.simple import _workflow_result

    result = _workflow_result(_execution("completed"), tmp_path)

    assert result.metadata["hessian_recomputed"] is True
    assert result.metadata["hessian_source"] == "freq_numfreq"
    assert result.metadata["has_frequencies"] is True


def test_simple_failed_frequency_makes_no_hessian_claim(tmp_path: Path) -> None:
    from acp.workflows.simple import _workflow_result

    result = _workflow_result(_execution("failed"), tmp_path)

    assert "hessian_recomputed" not in result.metadata
    assert "hessian_source" not in result.metadata


# ── nothing scientific changed ──────────────────────────────────────────


def test_scientific_defaults_are_unchanged() -> None:
    options = BatchMethodOptions()
    ts = options.resolve_role_options(True)
    assert ts["opt_initial_hessian"] == "calculate"
    assert ts["opt_recalc_hess"] == 5
    assert ts["opt_trust_radius"] == 0.3
    # The INT role keeps the element-based "auto" resolution downstream.
    assert options.resolve_role_options(False) == {}


def test_hessian_reuse_policy_is_still_absent(tmp_path: Path) -> None:
    """K1 (Hessian reuse) is deferred: no policy knob, no reuse wiring."""
    assert not hasattr(BatchMethodOptions(), "freq_hessian_policy")

    engine = _engine(tmp_path)
    optimized = CalculationResult(
        coords=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.74]],
    )
    request = engine._build_freq_request(
        optimized,
        BatchStructureItem(item_id="int_001", name="INT", tag="INT", xyz=_XYZ),
        0,
        1,
        tmp_path / "freq",
        ["H", "H"],
        BatchMethodOptions(),
    )
    # The FREQUENCY request never reads a staged Hessian: it is a fresh NumFreq.
    for forbidden in ("hess_file", "initial_hessian", "calc_hess", "hessian_source"):
        assert forbidden not in request.resources


# ── live status metric ──────────────────────────────────────────────────


def test_live_status_names_the_hessian_source(tmp_path: Path) -> None:
    """The running job's ``live_metrics`` carries the Hessian source."""
    from acp.api.job_status_view import build_live_status
    from acp.calculations.progress import LiveMetric, ProgressReporter

    engine = _engine(tmp_path)
    reporter = ProgressReporter(
        tmp_path / "task" / "WORK",
        job_name="batch",
        stages=["optimize", "frequency"],
        min_interval=0.0,
    )
    reporter.initialize()
    reporter.start_stage("frequency")
    reporter.set_live_metrics(
        [
            # The engine appends this same metric while the frequency runs.
            LiveMetric(
                key="hessian_source",
                label="Hessian",
                value=engine._step_hessian_source(
                    _item(tmp_path), StepKind.FREQUENCY
                ),
                kind="status",
                priority=80,
            )
        ]
    )

    live = build_live_status(json.loads((tmp_path / "task" / "WORK" / "state.json").read_text()))
    assert live is not None
    metric = next(m for m in live.metrics if m.key == "hessian_source")
    assert metric.value == "freq_numfreq"
    assert metric.label == "Hessian"


def test_step_hessian_source_is_step_specific(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    ts_item = _item(tmp_path, tag="TS", frequency_status=None)
    int_item = _item(tmp_path, tag="INT", frequency_status=None)

    assert engine._step_hessian_source(ts_item, StepKind.OPTIMIZE) == "opt_initial"
    assert engine._step_hessian_source(int_item, StepKind.OPTIMIZE) is None
    assert engine._step_hessian_source(int_item, StepKind.FREQUENCY) == "freq_numfreq"
    assert engine._step_hessian_source(int_item, StepKind.SINGLEPOINT) is None
