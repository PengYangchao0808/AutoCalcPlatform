"""Tests for BatchOptimizeEngine and mechanism-free batch models."""
# pyright: basic, reportArgumentType=false, reportIndexIssue=false, reportOptionalSubscript=false, reportCallIssue=false, reportAny=false

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from acp.backends.base import QCResult
from acp.calculations.batch.engine import (
    _PROFILE_STEPS,
    BatchOptimizeEngine,
    _count_significant_imaginary,
    _ts_frequency_judgment,
)
from acp.calculations.batch.models import (
    BatchCalculationItem,
    BatchStructureItem,
    JsonObject,
    build_tag_title,
    load_batch_request,
    load_items_from_result_manifest,
    parse_tag_comment,
)
from acp.calculations.batch.options import BatchMethodOptions
from acp.calculations.checkpoint import CheckpointMismatchError
from acp.calculations.contracts import StepKind, StructureRole
from tests.conftest import FakeBackend, FakeBackendCall

FIXTURES = Path(__file__).parent / "fixtures"


def _write_xyz(path: Path, comment: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"2\n{comment}\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
        encoding="utf-8",
    )


# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture()
def batch_items_ts_int() -> list[BatchStructureItem]:
    return [
        BatchStructureItem(
            item_id="candidate_001",
            name="TS candidate",
            tag="TS",
            xyz="2\nTAG: TS | candidate_id=candidate_001\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
            candidate_id="candidate_001",
        ),
        BatchStructureItem(
            item_id="int_001",
            name="INT candidate",
            tag="INT",
            xyz="2\nTAG: INT | candidate_id=int_001\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
            candidate_id="int_001",
        ),
    ]


@pytest.fixture()
def engine(tmp_path: Path) -> BatchOptimizeEngine:
    work_root = tmp_path / "task" / "WORK"
    result_root = tmp_path / "task" / "RESULT"
    return BatchOptimizeEngine(work_root=work_root, result_root=result_root)


# ── existing model tests ─────────────────────────────────────────────────


def test_models(tmp_path: Path) -> None:
    from acp.compat.legacy.batch_loaders import load_items_from_s2_path_manifest

    title = build_tag_title("TS", candidate_id="ts_001", source="test", frame=4)
    assert title == "TAG: TS | candidate_id=ts_001 | source=test | frame=004"
    parsed = parse_tag_comment(title)
    assert parsed == {
        "tag": "TS",
        "candidate_id": "ts_001",
        "source": "test",
        "frame": "004",
    }

    result_task = tmp_path / "result_task"
    _write_xyz(result_task / "RESULT" / "structures" / "ts_001.xyz", "result TS")
    _write_xyz(result_task / "RESULT" / "structures" / "int_001.xyz", "result INT")
    (result_task / "RESULT" / "result_manifest.json").write_text(
        json.dumps(
            {
                "version": 2,
                "workflow": "PESsearch",
                "status": "completed",
                "products": [
                    {
                        "id": "candidate_ts_001",
                        "label": "TS candidate",
                        "path": "structures/ts_001.xyz",
                        "kind": "structure",
                        "role": "transition_state",
                        "candidate_id": "ts_001",
                    },
                    {
                        "id": "candidate_int_001",
                        "label": "Minimum candidate",
                        "path": "structures/int_001.xyz",
                        "kind": "structure",
                        "role": "minimum",
                        "candidate_id": "int_001",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    result_items = load_items_from_result_manifest(result_task)
    assert [(item.candidate_id, item.role) for item in result_items] == [
        ("ts_001", StructureRole.TRANSITION_STATE),
        ("int_001", StructureRole.MINIMUM),
    ]

    legacy_payload: JsonObject = json.loads(
        (FIXTURES / "legacy_s2_path_manifest.json").read_text(encoding="utf-8")
    )
    legacy_task = tmp_path / "legacy_task" / "RESULT" / "mechanism"
    _write_xyz(legacy_task / "input" / "ts_legacy.xyz", "legacy TS")
    legacy_payload["recommendations"]["ts"][0]["geometry_path"] = "input/ts_legacy.xyz"
    legacy_manifest = legacy_task / "s2_path_manifest.json"
    legacy_manifest.parent.mkdir(parents=True, exist_ok=True)
    legacy_manifest.write_text(json.dumps(legacy_payload), encoding="utf-8")
    legacy_items, legacy_read = load_items_from_s2_path_manifest(legacy_manifest)
    assert legacy_read["schema_version"] == "s2_path_v2"
    assert [(item.candidate_id, item.tag) for item in legacy_items] == [("ts_guess_001", "TS")]

    request_items = load_batch_request(FIXTURES / "batch_structures_v1.json")
    assert [(item.item_id, item.role) for item in request_items] == [
        ("candidate_001", StructureRole.TRANSITION_STATE),
        ("int_001", StructureRole.MINIMUM),
    ]


def test_manifest_without_structures_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    result_dir = tmp_path / "RESULT"
    result_dir.mkdir()
    (result_dir / "result_manifest.json").write_text(
        json.dumps(
            {
                "version": 2,
                "workflow": "PESsearch",
                "status": "completed",
                "products": [{"id": "report", "path": "report.json", "kind": "report"}],
            }
        ),
        encoding="utf-8",
    )

    caplog.set_level(logging.WARNING)
    assert load_items_from_result_manifest(tmp_path) == []
    assert "no structure products" in caplog.text


def test_entry(tmp_path: Path, fake_backend: object) -> None:
    from acp.catalog import METHOD_SCHEMAS, WORKFLOW_CATALOG
    from acp.workflows.batch_optimize import run_batch_optimize
    from tests.conftest import FakeBackend

    assert isinstance(fake_backend, FakeBackend)

    output_dir = tmp_path / "batch_output"
    result = run_batch_optimize(
        FIXTURES / "batch_structures_v1.json",
        profile="opt_only",
        output_dir=output_dir,
    )

    assert result.status == "completed"
    assert result.stages_completed == ["prepare", "optimize", "finalize"]
    assert result.metadata["profile"] == "opt_only"
    assert (output_dir / "RESULT" / "result_manifest.json").is_file()

    entry = next(item for item in WORKFLOW_CATALOG if item["id"] == "BatchOptimize")
    assert entry["category"] == "preset"
    assert entry["status"] == "active"
    schema = METHOD_SCHEMAS["batch_optimize"]
    assert [profile["profile_id"] for profile in schema["profiles"]] == [
        "opt_only",
        "opt_freq",
        "opt_freq_sp",
        "opt_freq_sp_thermo",
    ]

    from acp.workflows.registry import get_workflow_entry

    registry_entry = get_workflow_entry("BatchOptimize")
    assert registry_entry is not None
    assert registry_entry.label == "Batch Optimization"
    assert registry_entry.requires_binaries == ["orca", "shermo"]

    from acp.cli import build_parser

    parsed = build_parser().parse_args(
        [
            "run",
            "BatchOptimize",
            "--from-artifact",
            "batch_job",
            "--profile",
            "opt_freq_sp",
            "--select",
            "ts_001,int_001",
            "--minimum-method",
            "r2SCAN-3c",
            "--minimum-basis",
            "def2-TZVP",
            "--transition-state-method",
            "wB97X-D4",
            "--transition-state-basis",
            "def2-TZVPPD",
        ]
    )
    assert parsed.workflow == "BatchOptimize"
    assert parsed.from_artifact == "batch_job"
    assert parsed.profile == "opt_freq_sp"
    assert parsed.minimum_method == "r2SCAN-3c"
    assert parsed.transition_state_basis == "def2-TZVPPD"


def test_single_item_flat_layout_matches_scheduler_task_contract(
    tmp_path: Path, fake_backend: object
) -> None:
    """A scheduler-fanned one-structure task has no batch/item nesting."""
    from tests.conftest import FakeBackend

    assert isinstance(fake_backend, FakeBackend)
    task_root = tmp_path / "task"
    item = BatchStructureItem(
        item_id="item_001",
        name="single molecule",
        tag="INT",
        xyz="2\nTAG: INT | candidate_id=item_001\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
        candidate_id="item_001",
    )
    engine = BatchOptimizeEngine(
        work_root=task_root / "WORK",
        result_root=task_root / "RESULT",
    )

    outcome = engine.run([item], profile="opt_freq", layout_mode="single_flat")

    assert outcome.items[0].status == "completed"
    assert outcome.items[0].input_xyz == "input.xyz"
    assert outcome.items[0].work_dir == "WORK"
    assert (task_root / "input.xyz").is_file()
    assert (task_root / "WORK" / "03_OPT" / "optimized.xyz").is_file()
    assert (task_root / "WORK" / "04_FREQ").is_dir()
    assert not (task_root / "WORK" / "03_OPT" / "batch").exists()
    assert (task_root / "RESULT" / "structures" / "item_001__TAG_INT__optimized.xyz").is_file()


def test_single_item_flat_layout_rejects_multiple_items(
    tmp_path: Path, batch_items_ts_int: list[BatchStructureItem]
) -> None:
    engine = BatchOptimizeEngine(
        work_root=tmp_path / "task" / "WORK",
        result_root=tmp_path / "task" / "RESULT",
    )

    with pytest.raises(ValueError, match="exactly one item"):
        engine.run(batch_items_ts_int, profile="opt_only", layout_mode="single_flat")


def test_batchoptimize_method_flags() -> None:
    from acp.scheduler.jobs import batchoptimize_method_flags

    flags = batchoptimize_method_flags(
        {
            "profile": "opt_freq_sp_thermo",
            "select": ["ts_001", "int_001"],
            "minimum_method": "r2SCAN-3c",
            "minimum_basis": "def2-TZVP",
            "transition_state_method": "wB97X-D4",
            "transition_state_basis": "def2-TZVPPD",
        }
    )

    assert flags == [
        "--profile",
        "opt_freq_sp_thermo",
        "--select",
        "ts_001,int_001",
        "--minimum-method",
        "r2SCAN-3c",
        "--minimum-basis",
        "def2-TZVP",
        "--transition-state-method",
        "wB97X-D4",
        "--transition-state-basis",
        "def2-TZVPPD",
    ]


def test_batchoptimize_method_flags_advanced_opt_scf() -> None:
    from acp.scheduler.jobs import batchoptimize_method_flags

    flags = batchoptimize_method_flags(
        {
            "opt_max_iter": 400,
            "opt_convergence": "verytight",
            "opt_trust_radius": 0.1,
            "opt_initial_hessian": "model",
            "opt_recalc_hess": 20,
            "opt_rescue_policy": "off",
            "opt_max_rescue": 5,
            "scf_max_iter": 500,
            "scf_convergence": "tight",
            "scf_strategy": "soscf",
        }
    )

    assert flags == [
        "--opt-max-iter", "400",
        "--opt-convergence", "verytight",
        "--opt-trust-radius", "0.1",
        "--opt-initial-hessian", "model",
        "--opt-rescue-policy", "off",
        "--opt-max-rescue", "5",
        "--scf-max-iter", "500",
        "--scf-convergence", "tight",
        "--scf-strategy", "soscf",
        "--opt-recalc-hess", "20",
    ]


def test_batchoptimize_method_flags_scf_orbital_inherit_false() -> None:
    from acp.scheduler.jobs import batchoptimize_method_flags

    flags = batchoptimize_method_flags({"scf_orbital_inherit": False})
    assert "--no-scf-orbital-inherit" in flags

    flags_true = batchoptimize_method_flags({"scf_orbital_inherit": True})
    assert "--no-scf-orbital-inherit" not in flags_true
    assert "--scf-orbital-inherit" not in flags_true

    flags_none = batchoptimize_method_flags({})
    assert "--no-scf-orbital-inherit" not in flags_none


def test_batchoptimize_method_flags_include_shared_settings() -> None:
    from acp.scheduler.jobs import batchoptimize_method_flags

    flags = batchoptimize_method_flags(
        {
            "profile": "opt_freq_sp_thermo",
            "optimization_method": "B3LYP",
            "optimization_basis": "def2-SVP",
            "single_point_method": "wB97M-V",
            "single_point_basis": "def2-TZVPP",
            "temperature": 333.15,
            "pressure": 2.0,
            "scale_factor": 0.98,
            "opt_max_iter": 400,
            "opt_convergence": "verytight",
            "opt_trust_radius": 0.1,
            "opt_initial_hessian": "model",
            "opt_recalc_hess": 20,
            "opt_rescue_policy": "off",
            "opt_max_rescue": 5,
            "scf_max_iter": 500,
            "scf_convergence": "tight",
            "scf_strategy": "soscf",
            "scf_orbital_inherit": False,
        }
    )

    assert flags == [
        "--profile",
        "opt_freq_sp_thermo",
        "--method",
        "B3LYP",
        "--basis",
        "def2-SVP",
        "--sp-method",
        "wB97M-V",
        "--sp-basis",
        "def2-TZVPP",
        "--temperature",
        "333.15",
        "--pressure",
        "2.0",
        "--scale-factor",
        "0.98",
        "--opt-max-iter",
        "400",
        "--opt-convergence",
        "verytight",
        "--opt-trust-radius",
        "0.1",
        "--opt-initial-hessian",
        "model",
        "--opt-rescue-policy",
        "off",
        "--opt-max-rescue",
        "5",
        "--scf-max-iter",
        "500",
        "--scf-convergence",
        "tight",
        "--scf-strategy",
        "soscf",
        "--opt-recalc-hess",
        "20",
        "--no-scf-orbital-inherit",
    ]


def test_role_specific_method_overrides_reach_qc_requests(
    tmp_path: Path,
    batch_items_ts_int: list[BatchStructureItem],
    fake_backend: object,
) -> None:
    from acp.calculations.batch.options import BatchMethodOptions
    from tests.conftest import FakeBackend

    assert isinstance(fake_backend, FakeBackend)
    engine = BatchOptimizeEngine(
        work_root=tmp_path / "task" / "WORK",
        result_root=tmp_path / "task" / "RESULT",
    )
    coordinates = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.7]])
    fake_backend.set_results(
        "frequency",
        [
            QCResult(
                success=True,
                coordinates=coordinates,
                symbols=["H", "H"],
                frequencies=[-500.0, 100.0],
                has_frequencies=True,
            ),
            QCResult(
                success=True,
                coordinates=coordinates,
                symbols=["H", "H"],
                frequencies=[100.0, 200.0],
                has_frequencies=True,
            ),
        ],
    )

    outcome = engine.run(
        batch_items_ts_int,
        profile="opt_freq_sp",
        methods=BatchMethodOptions(
            minimum_method="r2SCAN-3c",
            minimum_basis="def2-TZVP",
            transition_state_method="wB97X-D4",
            transition_state_basis="def2-TZVPPD",
        ),
    )

    assert [item.status for item in outcome.items] == ["completed", "completed"]
    for call in fake_backend.calls:
        output_dir = str(call.kwargs["output_dir"])
        if "candidate_001" in output_dir:
            assert call.kwargs["method"] == "wB97X-D4"
            assert call.kwargs["basis"] == "def2-TZVPPD"
        if "int_001" in output_dir:
            assert call.kwargs["method"] == "r2SCAN-3c"
            assert call.kwargs["basis"] == "def2-TZVP"


def test_optimization_and_frequency_share_method_with_separate_sp_settings(
    tmp_path: Path,
    batch_items_ts_int: list[BatchStructureItem],
    fake_backend: object,
) -> None:
    from acp.calculations.batch.options import BatchMethodOptions
    from tests.conftest import FakeBackend

    assert isinstance(fake_backend, FakeBackend)
    coordinates = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.7]])
    fake_backend.set_results(
        "frequency",
        [
            QCResult(
                success=True,
                coordinates=coordinates,
                symbols=["H", "H"],
                frequencies=[-500.0, 100.0],
                has_frequencies=True,
            ),
            QCResult(
                success=True,
                coordinates=coordinates,
                symbols=["H", "H"],
                frequencies=[100.0, 200.0],
                has_frequencies=True,
            ),
        ],
    )

    engine = BatchOptimizeEngine(
        work_root=tmp_path / "task" / "WORK",
        result_root=tmp_path / "task" / "RESULT",
    )
    outcome = engine.run(
        batch_items_ts_int,
        profile="opt_freq_sp",
        methods=BatchMethodOptions(
            optimization_method="B3LYP",
            optimization_basis="def2-SVP",
            single_point_method="wB97M-V",
            single_point_basis="def2-TZVPP",
            frequency_method="M062X",
            frequency_basis="def2-TZVP",
        ),
    )

    assert all(item.status == "completed" for item in outcome.items)
    for call in fake_backend.calls:
        if call.method in {"optimize", "transition_state_opt", "frequency"}:
            assert call.kwargs["method"] == "B3LYP"
            assert call.kwargs["basis"] == "def2-SVP"
        elif call.method == "single_point":
            assert call.kwargs["method"] == "wB97M-V"
            assert call.kwargs["basis"] == "def2-TZVPP"


def test_batchoptimize_cli_passes_role_specific_method_options(tmp_path: Path) -> None:
    from acp.calculations.batch.options import BatchMethodOptions
    from acp.cli import _handle_batch_optimize, build_parser
    from acp.core.workflow import WorkflowResult

    args = build_parser().parse_args(
        [
            "run",
            "BatchOptimize",
            "--items-file",
            str(FIXTURES / "batch_structures_v1.json"),
            "--output",
            str(tmp_path / "batch_output"),
            "--minimum-method",
            "r2SCAN-3c",
            "--minimum-basis",
            "def2-TZVP",
            "--transition-state-method",
            "wB97X-D4",
            "--transition-state-basis",
            "def2-TZVPPD",
            "--opt-max-iter",
            "400",
            "--opt-convergence",
            "verytight",
            "--opt-trust-radius",
            "0.1",
            "--opt-initial-hessian",
            "model",
            "--opt-recalc-hess",
            "20",
            "--opt-rescue-policy",
            "off",
            "--opt-max-rescue",
            "5",
            "--scf-max-iter",
            "500",
            "--scf-convergence",
            "tight",
            "--scf-strategy",
            "soscf",
            "--no-scf-orbital-inherit",
        ]
    )

    with patch("acp.workflows.batch_optimize.run_batch_optimize") as run:
        run.return_value = WorkflowResult(status="completed")
        assert _handle_batch_optimize(args) == 0

    assert run.call_args is not None
    assert run.call_args.kwargs["methods"] == BatchMethodOptions(
        minimum_method="r2SCAN-3c",
        minimum_basis="def2-TZVP",
        transition_state_method="wB97X-D4",
        transition_state_basis="def2-TZVPPD",
        opt_max_iter=400,
        opt_convergence="verytight",
        opt_trust_radius=0.1,
        opt_initial_hessian="model",
        opt_recalc_hess=20,
        opt_rescue_policy="off",
        opt_max_rescue=5,
        scf_max_iter=500,
        scf_convergence="tight",
        scf_strategy="soscf",
        scf_orbital_inherit=False,
    )


@pytest.mark.parametrize("source_key", ["from_artifact", "items_file"])
def test_batchoptimize_runner_remote_command_parity(source_key: str) -> None:
    from acp.scheduler.jobs import JobSpec
    from acp.scheduler.remote.script_gen import build_remote_cli_command
    from acp.scheduler.runner import JobRunner

    spec = JobSpec(
        workflow="BatchOptimize",
        name="batch",
        input={
            source_key: "WORK/01_PREPARE/handoff/batch_structures_v1.json",
            "select": ["ts_001", "int_001"],
        },
        method={
            "profile": "opt_freq_sp_thermo",
            "minimum_method": "r2SCAN-3c",
            "minimum_basis": "def2-TZVP",
            "transition_state_method": "wB97X-D4",
            "transition_state_basis": "def2-TZVPPD",
            "opt_max_iter": 400,
            "opt_convergence": "verytight",
            "opt_trust_radius": 0.1,
            "opt_initial_hessian": "model",
            "opt_recalc_hess": 20,
            "opt_rescue_policy": "off",
            "opt_max_rescue": 5,
            "scf_max_iter": 500,
            "scf_convergence": "tight",
            "scf_strategy": "soscf",
            "scf_orbital_inherit": False,
        },
        resources={"nproc": 4, "mem": "4GB"},
    )

    local = JobRunner()._build_cmd(spec, Path("/tmp/wd"))
    remote = build_remote_cli_command(spec, python_executable=local[0])
    local_for_remote = ["." if value == "/tmp/wd" else value for value in local]
    assert remote == local_for_remote


def test_batchoptimize_stage_plan_is_profile_driven() -> None:
    from acp.scheduler.jobs import JobSpec
    from acp.scheduler.stage_tasks import get_stage_plan

    expected = {
        "opt_only": ["prepare", "optimize", "finalize"],
        "opt_freq": ["prepare", "optimize", "frequency", "finalize"],
        "opt_freq_sp": ["prepare", "optimize", "frequency", "single_point", "finalize"],
        "opt_freq_sp_thermo": [
            "prepare",
            "optimize",
            "frequency",
            "single_point",
            "thermochemistry",
            "finalize",
        ],
    }
    for profile, stage_names in expected.items():
        plan = get_stage_plan(JobSpec(workflow="BatchOptimize", method={"profile": profile}))
        assert [stage.stage_name for stage in plan] == stage_names


def test_batchoptimize_job_submission_initializes_stage_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acp.scheduler.jobs import JobSpec
    from acp.scheduler.manager import JobManager

    manager = JobManager(run_root=tmp_path / "runs", poll_interval=30)
    monkeypatch.setattr(manager, "_start_submission_thread", lambda job_id, thread_name: True)
    try:
        record = manager.submit(
            JobSpec(
                workflow="BatchOptimize",
                name="batch",
                input={"items_file": str(FIXTURES / "batch_structures_v1.json")},
                method={"profile": "opt_freq"},
            )
        )

        assert record.status.value == "queued"
        work_dir = Path(record.work_dir)
        assert (work_dir / "job.json").is_file()
        assert [task.stage_name for task in manager.stage_tasks.list_by_job(record.id)] == [
            "prepare",
            "optimize",
            "frequency",
            "finalize",
        ]
    finally:
        manager.shutdown()


def test_batchoptimize_pause_unpause_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
    from acp.scheduler.manager import JobManager

    manager = JobManager(run_root=tmp_path / "runs", poll_interval=30)
    work_dir = tmp_path / "runs" / "batch_pause"
    work_dir.mkdir(parents=True)
    manager.store.create(
        JobRecord(
            id="batch_pause",
            spec=JobSpec(
                workflow="BatchOptimize",
                name="batch",
                input={"items_file": str(FIXTURES / "batch_structures_v1.json")},
                method={"profile": "opt_freq"},
            ),
            status=JobStatus.RUNNING,
            work_dir=str(work_dir),
        )
    )
    paused_ids: list[str] = []
    resumed_ids: list[str] = []
    monkeypatch.setattr(
        manager.runner,
        "pause_local",
        lambda job_id: paused_ids.append(job_id) or True,
    )
    monkeypatch.setattr(
        manager.runner,
        "resume_local",
        lambda job_id: resumed_ids.append(job_id) or True,
    )

    try:
        paused = manager.pause_job("batch_pause")
        resumed = manager.unpause_job("batch_pause")

        assert paused.status is JobStatus.PAUSED
        assert resumed.status is JobStatus.RUNNING
        assert paused_ids == ["batch_pause"]
        assert resumed_ids == ["batch_pause"]
        events = manager.event_log("batch_pause")
        assert events is not None
        assert [event["type"] for event in events.read_all()[-2:]] == [
            "job.paused",
            "job.resumed",
        ]
    finally:
        manager.shutdown()


# ── TS imaginary-frequency judgment unit tests ───────────────────────────


def test_ts_imaginary_judgment_valid() -> None:
    """Exactly one imaginary below -50 cm⁻¹ is valid."""
    valid, msg = _ts_frequency_judgment([-500.0, 100.0, 200.0])
    assert valid is True
    assert msg == ""


def test_ts_imaginary_judgment_too_many() -> None:
    """Multiple significant imaginaries → higher_order_saddle."""
    valid, msg = _ts_frequency_judgment([-500.0, -200.0, 100.0])
    assert valid is False
    assert "higher_order_saddle" in msg


def test_ts_imaginary_judgment_none() -> None:
    """No significant imaginary → ts_no_imaginary."""
    valid, msg = _ts_frequency_judgment([-10.0, 100.0, 200.0])
    assert valid is False
    assert "ts_no_imaginary" in msg


def test_count_significant_imaginary() -> None:
    assert _count_significant_imaginary([-500.0, -10.0, 100.0], cutoff=-50.0) == 1
    assert _count_significant_imaginary([-500.0, -60.0, 100.0], cutoff=-50.0) == 2
    assert _count_significant_imaginary([100.0, 200.0], cutoff=-50.0) == 0


# ── profile steps ────────────────────────────────────────────────────────


def test_four_profiles_have_correct_steps() -> None:
    assert _PROFILE_STEPS["opt_only"] == ("optimize",)
    assert _PROFILE_STEPS["opt_freq"] == ("optimize", "frequency")
    assert _PROFILE_STEPS["opt_freq_sp"] == ("optimize", "frequency", "singlepoint")
    assert _PROFILE_STEPS["opt_freq_sp_thermo"] == (
        "optimize",
        "frequency",
        "singlepoint",
        "thermochemistry",
    )


# ── IRC rejection ────────────────────────────────────────────────────────


def test_reject_irc_in_request() -> None:
    """IRC is not a StepKind; plans with unsupported step kinds are rejected."""
    from acp.calculations.contracts import CalculationPlan, CalculationStep, validate_plan

    plan = CalculationPlan(
        workflow="BatchOptimize",
        profile="opt_freq",
        items=[],
        steps=[CalculationStep(kind="optimize")],
    )
    errors = validate_plan(plan)
    assert errors == []

    bad_plan = CalculationPlan(
        workflow="BatchOptimize",
        profile="opt_freq",
        items=[],
        steps=[{"kind": "irc"}],
    )
    errors = validate_plan(bad_plan)
    assert len(errors) == 1
    assert "irc" in errors[0].lower()


# ── engine happy path (opt_freq_sp_thermo, mixed TS+INT) ────────────────


def test_mixed_ts_int_opt_freq_sp_thermo(
    tmp_path: Path,
    fake_backend: object,
) -> None:
    """QA happy: 2 items TS+INT → RESULT/structures products + manifest."""
    from acp.calculations.batch.options import BatchMethodOptions
    from tests.conftest import FakeBackend

    assert isinstance(fake_backend, FakeBackend)
    work_root = tmp_path / "task" / "WORK"
    result_root = tmp_path / "task" / "RESULT"

    items = [
        BatchStructureItem(
            item_id="candidate_001",
            name="TS candidate",
            tag="TS",
            xyz="2\nTAG: TS | candidate_id=candidate_001\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
            candidate_id="candidate_001",
        ),
        BatchStructureItem(
            item_id="int_001",
            name="INT candidate",
            tag="INT",
            xyz="2\nTAG: INT | candidate_id=int_001\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
            candidate_id="int_001",
        ),
    ]

    ts_freq_log = work_root / "03_OPT" / "batch" / "candidate_001" / "frequency" / "frequency.log"
    int_freq_log = work_root / "03_OPT" / "batch" / "int_001" / "frequency" / "frequency.log"
    ts_freq_log.parent.mkdir(parents=True, exist_ok=True)
    ts_freq_log.write_text("freq output", encoding="utf-8")
    int_freq_log.parent.mkdir(parents=True, exist_ok=True)
    int_freq_log.write_text("freq output", encoding="utf-8")

    fake_backend.set_results(
        "frequency",
        [
            QCResult(
                success=True,
                energy=-1.1,
                coordinates=np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.7]]),
                symbols=["H", "H"],
                frequencies=[-500.0, 100.0, 200.0],
                has_frequencies=True,
                log_file=str(ts_freq_log),
            ),
            QCResult(
                success=True,
                energy=-1.0,
                coordinates=np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.7]]),
                symbols=["H", "H"],
                frequencies=[100.0, 200.0, 300.0],
                has_frequencies=True,
                log_file=str(int_freq_log),
            ),
        ],
    )

    engine = BatchOptimizeEngine(work_root=work_root, result_root=result_root)

    with patch("acp.calculations.primitives.thermochemistry.run_shermo") as mock_shermo:
        mock_shermo.return_value = {"g_sum": -1.2, "h_sum": -1.1, "s_sum": 0.01}

        outcome = engine.run(
            items,
            profile="opt_freq_sp_thermo",
            charge=0,
            methods=BatchMethodOptions(temperature=333.15, pressure=2.0, scale_factor=0.98),
        )

        assert mock_shermo.call_count == 2
        for call in mock_shermo.call_args_list:
            assert call.kwargs["temperature_k"] == 333.15
            assert call.kwargs["pressure_atm"] == 2.0
            assert call.kwargs["scl_zpe"] == 0.98

    assert len(outcome.items) == 2
    assert all(item.status == "completed" for item in outcome.items)
    assert outcome.errors == []

    result_manifest_path = engine._result_root / "result_manifest.json"
    assert result_manifest_path.exists()
    manifest_data = json.loads(result_manifest_path.read_text(encoding="utf-8"))
    product_ids = [p["id"] for p in manifest_data["products"]]
    assert "batch_candidate_001" in product_ids
    assert "batch_int_001" in product_ids

    structures_dir = engine._result_root / "structures"
    assert (structures_dir / "candidate_001__TAG_TS__optimized.xyz").exists()
    assert (structures_dir / "int_001__TAG_INT__optimized.xyz").exists()

    method_counts: dict[str, int] = {}
    for call in fake_backend.calls:
        method_counts[call.method] = method_counts.get(call.method, 0) + 1
    assert method_counts.get("optimize", 0) == 1
    assert method_counts.get("transition_state_opt", 0) == 1
    assert method_counts.get("frequency", 0) >= 2
    assert method_counts.get("single_point", 0) >= 2


# ── engine failure isolation ─────────────────────────────────────────────


def test_item_failure_isolated(
    tmp_path: Path,
    fake_backend: object,
) -> None:
    """QA failure: item2 raises → item1 completes, item2 failed, structured record."""
    from tests.conftest import FakeBackend

    assert isinstance(fake_backend, FakeBackend)

    items = [
        BatchStructureItem(
            item_id="ts_001",
            name="TS",
            tag="TS",
            xyz="2\nTAG: TS\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
            candidate_id="ts_001",
        ),
        BatchStructureItem(
            item_id="int_001",
            name="INT",
            tag="INT",
            xyz="2\nTAG: INT\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
            candidate_id="int_001",
        ),
    ]

    def _failing_opt(*_args: object, **_kwargs: object) -> QCResult:
        raise RuntimeError("fake optimize failure")

    fake_backend.optimize = _failing_opt  # type: ignore[method-assign]

    work_root = tmp_path / "task" / "WORK"
    result_root = tmp_path / "task" / "RESULT"
    engine = BatchOptimizeEngine(work_root=work_root, result_root=result_root)
    outcome = engine.run(items, profile="opt_only", charge=0)

    item1 = next(i for i in outcome.items if i.item_id == "ts_001")
    item2 = next(i for i in outcome.items if i.item_id == "int_001")
    assert item1.status == "completed"
    assert item2.status == "failed"
    assert "fake optimize failure" in item2.error

    assert len(outcome.manifest.items) == 2
    assert outcome.manifest.counts["completed"] == 1
    assert outcome.manifest.counts["failed"] == 1


# ── cache hit skips completed ────────────────────────────────────────────


def test_cache_hit_skips_completed(
    tmp_path: Path,
    fake_backend: object,
    batch_items_ts_int: list[BatchStructureItem],
) -> None:
    """Re-run with same profile skips previously completed items."""
    from tests.conftest import FakeBackend

    assert isinstance(fake_backend, FakeBackend)
    work_root = tmp_path / "task" / "WORK"
    result_root = tmp_path / "task" / "RESULT"
    engine = BatchOptimizeEngine(work_root=work_root, result_root=result_root)

    outcome1 = engine.run(batch_items_ts_int, profile="opt_only", charge=0)
    assert all(item.status == "completed" for item in outcome1.items)
    calls_after_first = len(fake_backend.calls)
    assert calls_after_first > 0

    outcome2 = engine.run(batch_items_ts_int, profile="opt_only", charge=0)
    assert all(item.status == "skipped" for item in outcome2.items)
    assert len(outcome2.carried_items) == 2
    assert len(fake_backend.calls) == calls_after_first


def test_resume_skips_completed(
    tmp_path: Path,
    fake_backend: object,
    batch_items_ts_int: list[BatchStructureItem],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.conftest import FakeBackend

    assert isinstance(fake_backend, FakeBackend)
    work_root = tmp_path / "task" / "WORK"
    result_root = tmp_path / "task" / "RESULT"
    engine = BatchOptimizeEngine(work_root=work_root, result_root=result_root)
    original_process = engine._process_item
    process_count = 0

    def interrupt_after_first(
        item: BatchStructureItem,
        record: BatchCalculationItem,
        steps: tuple[StepKind, ...],
        charge: int,
        multiplicity: int,
    ) -> None:
        nonlocal process_count
        process_count += 1
        if process_count == 2:
            raise KeyboardInterrupt
        original_process(item, record, steps, charge, multiplicity)

    monkeypatch.setattr(engine, "_process_item", interrupt_after_first)
    with pytest.raises(KeyboardInterrupt):
        engine.run(batch_items_ts_int, profile="opt_only", charge=0)

    checkpoint_path = work_root / "00_RUNTIME" / "checkpoint.json"
    checkpoint_data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    item1_state = checkpoint_data["items_state"]["candidate_001"]
    assert item1_state["status"] == "completed"
    assert item1_state["cache_key"]
    assert item1_state["error"] == ""
    assert checkpoint_data["items_state"]["__batch__"]["next_item_index"] == 1

    calls_after_interrupt = len(fake_backend.calls)
    monkeypatch.setattr(engine, "_process_item", original_process)
    outcome = engine.run(batch_items_ts_int, profile="opt_only", charge=0)

    assert [item.status for item in outcome.items] == ["skipped", "completed"]
    assert len(fake_backend.calls) == calls_after_interrupt + 1
    assert [call.method for call in fake_backend.calls].count("transition_state_opt") == 1


def test_fingerprint_change_rejects_old_checkpoint(
    tmp_path: Path,
    fake_backend: object,
    batch_items_ts_int: list[BatchStructureItem],
) -> None:
    from tests.conftest import FakeBackend

    assert isinstance(fake_backend, FakeBackend)
    engine = BatchOptimizeEngine(
        work_root=tmp_path / "task" / "WORK",
        result_root=tmp_path / "task" / "RESULT",
    )
    engine.run(batch_items_ts_int, profile="opt_only", charge=0)
    calls_after_first = len(fake_backend.calls)

    with pytest.raises(CheckpointMismatchError):
        engine.run(batch_items_ts_int, profile="opt_freq", charge=0)

    assert len(fake_backend.calls) == calls_after_first


# ── profile mismatch triggers full re-run ────────────────────────────────


def test_profile_mismatch_full_rerun(
    tmp_path: Path,
    fake_backend: object,
    batch_items_ts_int: list[BatchStructureItem],
) -> None:
    """Changing profile triggers full re-run even with same items."""
    from tests.conftest import FakeBackend

    assert isinstance(fake_backend, FakeBackend)
    work_root = tmp_path / "task" / "WORK"
    result_root = tmp_path / "task" / "RESULT"
    engine = BatchOptimizeEngine(work_root=work_root, result_root=result_root)

    outcome1 = engine.run(batch_items_ts_int, profile="opt_only", charge=0)
    assert all(item.status == "completed" for item in outcome1.items)
    calls_after_first = len(fake_backend.calls)

    outcome2 = engine.run(batch_items_ts_int, profile="opt_only", charge=0)
    assert all(item.status == "skipped" for item in outcome2.items)
    assert len(fake_backend.calls) == calls_after_first


# ── engine with opt_only profile ─────────────────────────────────────────


def test_opt_only_profile(
    engine: BatchOptimizeEngine,
    batch_items_ts_int: list[BatchStructureItem],
    fake_backend: object,
) -> None:
    """opt_only profile: only optimize calls, no frequency/sp/thermo."""
    from tests.conftest import FakeBackend

    assert isinstance(fake_backend, FakeBackend)
    outcome = engine.run(batch_items_ts_int, profile="opt_only", charge=0)
    assert all(item.status == "completed" for item in outcome.items)

    methods = [call.method for call in fake_backend.calls]
    assert "frequency" not in methods
    assert "single_point" not in methods


# ── invalid profile rejected ─────────────────────────────────────────────


def test_invalid_profile_rejected(engine: BatchOptimizeEngine) -> None:
    item = BatchStructureItem(
        item_id="x",
        name="x",
        tag="INT",
        xyz="2\ncomment\nH 0 0 0\nH 0 0 1\n",
    )
    with pytest.raises(ValueError, match="unknown batch profile"):
        engine.run([item], profile="invalid_profile")


# ── empty items rejected ────────────────────────────────────────────────


def test_empty_items_rejected(engine: BatchOptimizeEngine) -> None:
    with pytest.raises(ValueError, match="at least one"):
        engine.run([], profile="opt_only")


# ── TS frequency failure aborts item ─────────────────────────────────────


def test_ts_frequency_failure_aborts_item(
    engine: BatchOptimizeEngine,
    fake_backend: object,
) -> None:
    """TS with no significant imaginary frequencies → item fails."""
    from tests.conftest import FakeBackend

    assert isinstance(fake_backend, FakeBackend)
    ts_item = BatchStructureItem(
        item_id="ts_001",
        name="TS",
        tag="TS",
        xyz="2\nTAG: TS\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
        candidate_id="ts_001",
    )
    # All positive frequencies → TS judgment fails
    fake_backend.set_result(
        "frequency",
        QCResult(
            success=True,
            energy=-1.1,
            coordinates=np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.7]]),
            symbols=["H", "H"],
            frequencies=[100.0, 200.0, 300.0],
            has_frequencies=True,
        ),
    )

    outcome = engine.run([ts_item], profile="opt_freq", charge=0)
    assert outcome.items[0].status == "failed"
    assert "ts_no_imaginary" in outcome.items[0].error


# ── BatchMethodOptions → FakeBackend kwargs forwarding ──────────────────


class TestBatchMethodOptionsForwarding:

    def _run_batch(
        self,
        tmp_path: Path,
        fake_backend: object,
        items: list[BatchStructureItem] | None = None,
        methods: BatchMethodOptions | None = None,
        profile: str = "opt_freq_sp",
    ) -> list[FakeBackendCall]:
        assert isinstance(fake_backend, FakeBackend)
        if items is None:
            items = [
                BatchStructureItem(
                    item_id="candidate_001",
                    name="TS",
                    tag="TS",
                    xyz="2\nTAG: TS | candidate_id=candidate_001\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
                    candidate_id="candidate_001",
                ),
                BatchStructureItem(
                    item_id="int_001",
                    name="INT",
                    tag="INT",
                    xyz="2\nTAG: INT | candidate_id=int_001\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
                    candidate_id="int_001",
                ),
            ]
        coordinates = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.7]])
        fake_backend.set_results(
            "frequency",
            [
                QCResult(
                    success=True, coordinates=coordinates, symbols=["H", "H"],
                    frequencies=[-500.0, 100.0], has_frequencies=True,
                ),
                QCResult(
                    success=True, coordinates=coordinates, symbols=["H", "H"],
                    frequencies=[100.0, 200.0], has_frequencies=True,
                ),
            ],
        )
        engine = BatchOptimizeEngine(
            work_root=tmp_path / "task" / "WORK",
            result_root=tmp_path / "task" / "RESULT",
        )
        engine.run(
            items,
            profile=profile,
            charge=0,
            methods=methods or BatchMethodOptions(),
        )
        return fake_backend.calls

    def test_default_ts_gets_role_defaults(
        self, tmp_path: Path, fake_backend: object,
    ) -> None:
        from tests.conftest import FakeBackend
        assert isinstance(fake_backend, FakeBackend)

        calls = self._run_batch(tmp_path, fake_backend)
        ts_opt = next(
            c for c in calls
            if c.method in ("transition_state_opt", "optimize")
            and "candidate_001" in str(c.kwargs.get("output_dir", ""))
        )
        assert ts_opt.kwargs["max_cycles"] == 200
        assert ts_opt.kwargs["trust_radius"] == 0.3
        assert ts_opt.kwargs["initial_hessian"] == "calculate"
        assert ts_opt.kwargs["recalc_hess"] == 5
        assert ts_opt.kwargs["opt_level"] == "tight"

    def test_default_int_gets_bare_defaults(
        self, tmp_path: Path, fake_backend: object,
    ) -> None:
        from tests.conftest import FakeBackend
        assert isinstance(fake_backend, FakeBackend)

        calls = self._run_batch(tmp_path, fake_backend)
        int_opt = next(
            c for c in calls
            if c.method in ("transition_state_opt", "optimize")
            and "int_001" in str(c.kwargs.get("output_dir", ""))
        )
        assert int_opt.kwargs["max_cycles"] == 200
        assert "trust_radius" not in int_opt.kwargs
        assert "initial_hessian" not in int_opt.kwargs
        assert "recalc_hess" not in int_opt.kwargs

    def test_user_overrides_reach_opt_kwargs(
        self, tmp_path: Path, fake_backend: object,
    ) -> None:
        from acp.calculations.batch.options import BatchMethodOptions
        from tests.conftest import FakeBackend
        assert isinstance(fake_backend, FakeBackend)

        methods = BatchMethodOptions(
            opt_max_iter=100,
            opt_convergence="verytight",
            opt_trust_radius=0.2,
            opt_initial_hessian="calculate",
            opt_recalc_hess=10,
        )
        calls = self._run_batch(tmp_path, fake_backend, methods=methods)
        ts_opt = next(
            c for c in calls
            if c.method in ("transition_state_opt", "optimize")
            and "candidate_001" in str(c.kwargs.get("output_dir", ""))
        )
        assert ts_opt.kwargs["max_cycles"] == 100
        assert ts_opt.kwargs["opt_level"] == "verytight"
        assert ts_opt.kwargs["trust_radius"] == 0.2
        assert ts_opt.kwargs["initial_hessian"] == "calculate"
        assert ts_opt.kwargs["recalc_hess"] == 10

    def test_scf_trio_forwarded_to_opt(
        self, tmp_path: Path, fake_backend: object,
    ) -> None:
        from acp.calculations.batch.options import BatchMethodOptions
        from tests.conftest import FakeBackend
        assert isinstance(fake_backend, FakeBackend)

        methods = BatchMethodOptions(
            scf_max_iter=500,
            scf_convergence="verytight",
            scf_strategy="slowconv",
        )
        calls = self._run_batch(tmp_path, fake_backend, methods=methods)
        ts_opt = next(
            c for c in calls
            if c.method in ("transition_state_opt", "optimize")
            and "candidate_001" in str(c.kwargs.get("output_dir", ""))
        )
        assert ts_opt.kwargs["scf_maxiter"] == 500
        assert ts_opt.kwargs["scf_convergence"] == "verytight"
        assert ts_opt.kwargs["scf_strategy"] == "slowconv"

    def test_scf_trio_forwarded_to_freq(
        self, tmp_path: Path, fake_backend: object,
    ) -> None:
        from acp.calculations.batch.options import BatchMethodOptions
        from tests.conftest import FakeBackend
        assert isinstance(fake_backend, FakeBackend)

        methods = BatchMethodOptions(
            scf_max_iter=400,
            scf_convergence="loose",
            scf_strategy="soscf",
        )
        calls = self._run_batch(tmp_path, fake_backend, methods=methods)
        freq_calls = [c for c in calls if c.method == "frequency"]
        assert len(freq_calls) >= 2
        for fc in freq_calls:
            assert fc.kwargs["scf_maxiter"] == 400
            assert fc.kwargs["scf_convergence"] == "loose"
            assert fc.kwargs["scf_strategy"] == "soscf"

    def test_scf_trio_forwarded_to_sp(
        self, tmp_path: Path, fake_backend: object,
    ) -> None:
        from acp.calculations.batch.options import BatchMethodOptions
        from tests.conftest import FakeBackend
        assert isinstance(fake_backend, FakeBackend)

        methods = BatchMethodOptions(
            scf_max_iter=600,
            scf_convergence="tight",
            scf_strategy="slowconv",
        )
        calls = self._run_batch(tmp_path, fake_backend, methods=methods)
        sp_calls = [c for c in calls if c.method == "single_point"]
        assert len(sp_calls) >= 2
        for spc in sp_calls:
            assert spc.kwargs["scf_maxiter"] == 600
            assert spc.kwargs["scf_convergence"] == "tight"
            assert spc.kwargs["scf_strategy"] == "slowconv"

    def test_default_scf_trio_values(
        self, tmp_path: Path, fake_backend: object,
    ) -> None:
        from tests.conftest import FakeBackend
        assert isinstance(fake_backend, FakeBackend)

        calls = self._run_batch(tmp_path, fake_backend)
        ts_opt = next(
            c for c in calls
            if c.method in ("transition_state_opt", "optimize")
            and "candidate_001" in str(c.kwargs.get("output_dir", ""))
        )
        assert ts_opt.kwargs["scf_maxiter"] == 300
        assert ts_opt.kwargs["scf_convergence"] == "tight"
        assert ts_opt.kwargs["scf_strategy"] == "normal"

    def test_rescue_and_damp_shift_unchanged(
        self, tmp_path: Path, fake_backend: object,
    ) -> None:
        from acp.calculations.batch.options import BatchMethodOptions
        from tests.conftest import FakeBackend
        assert isinstance(fake_backend, FakeBackend)

        methods = BatchMethodOptions(
            opt_rescue_policy="none",
            opt_max_rescue=0,
            scf_damp=True,
            scf_damp_fac=0.7,
            scf_shift=True,
            scf_shift_fac=0.5,
        )
        calls = self._run_batch(tmp_path, fake_backend, methods=methods)
        ts_opt = next(
            c for c in calls
            if c.method in ("transition_state_opt", "optimize")
        )
        assert ts_opt.kwargs["opt_rescue_policy"] == "none"
        assert ts_opt.kwargs["opt_max_rescue"] == 0
        assert ts_opt.kwargs["scf_damp"] is True
        assert ts_opt.kwargs["scf_damp_fac"] == 0.7
        assert ts_opt.kwargs["scf_shift"] is True
        assert ts_opt.kwargs["scf_shift_fac"] == 0.5


# ── per-role override resolution (P2a) ────────────────────────────────────


def _options_from_cli(tmp_path: Path, extra: list[str]) -> BatchMethodOptions:
    """Parse extra BatchOptimize flags and capture the built options."""
    from acp.cli import _handle_batch_optimize, build_parser
    from acp.core.workflow import WorkflowResult

    args = build_parser().parse_args(
        [
            "run",
            "BatchOptimize",
            "--items-file",
            str(FIXTURES / "batch_structures_v1.json"),
            "--output",
            str(tmp_path / "batch_output"),
            *extra,
        ]
    )
    with patch("acp.workflows.batch_optimize.run_batch_optimize") as run:
        run.return_value = WorkflowResult(status="completed")
        assert _handle_batch_optimize(args) == 0
    return run.call_args.kwargs["methods"]


class TestRoleOverrides:
    """resolve_role_options priority matrix + for_role fix + cross-contamination."""

    # -- resolve_role_options: priority chain ──────────────────────────────

    def test_ts_default_gets_role_defaults(self) -> None:
        from acp.calculations.batch.options import BatchMethodOptions

        opts = BatchMethodOptions()
        resolved = opts.resolve_role_options(is_transition_state=True)
        assert resolved["opt_trust_radius"] == 0.3
        assert resolved["opt_initial_hessian"] == "calculate"
        assert resolved["opt_recalc_hess"] == 5

    def test_int_default_omits_all(self) -> None:
        from acp.calculations.batch.options import BatchMethodOptions

        opts = BatchMethodOptions()
        resolved = opts.resolve_role_options(is_transition_state=False)
        assert resolved == {}

    def test_role_override_beats_common(self) -> None:
        from acp.calculations.batch.options import BatchMethodOptions

        opts = BatchMethodOptions(
            opt_trust_radius=0.25,
            transition_state_opt_trust_radius=0.15,
        )
        ts_resolved = opts.resolve_role_options(is_transition_state=True)
        assert ts_resolved["opt_trust_radius"] == 0.15
        int_resolved = opts.resolve_role_options(is_transition_state=False)
        assert int_resolved["opt_trust_radius"] == 0.25

    def test_int_role_override_beats_common(self) -> None:
        from acp.calculations.batch.options import BatchMethodOptions

        opts = BatchMethodOptions(
            opt_trust_radius=0.25,
            minimum_opt_trust_radius=0.10,
        )
        int_resolved = opts.resolve_role_options(is_transition_state=False)
        assert int_resolved["opt_trust_radius"] == 0.10
        ts_resolved = opts.resolve_role_options(is_transition_state=True)
        assert ts_resolved["opt_trust_radius"] == 0.25

    def test_empty_string_override_inherits_common(self) -> None:
        from acp.calculations.batch.options import BatchMethodOptions

        opts = BatchMethodOptions(
            opt_trust_radius=0.20,
            transition_state_opt_trust_radius="",
        )
        ts_resolved = opts.resolve_role_options(is_transition_state=True)
        assert ts_resolved["opt_trust_radius"] == 0.20

    def test_none_override_inherits_common(self) -> None:
        from acp.calculations.batch.options import BatchMethodOptions

        opts = BatchMethodOptions(
            opt_initial_hessian="model",
            transition_state_opt_initial_hessian=None,
        )
        ts_resolved = opts.resolve_role_options(is_transition_state=True)
        assert ts_resolved["opt_initial_hessian"] == "model"

    def test_auto_sentinel_inherits_common_for_hessian(self) -> None:
        from acp.calculations.batch.options import BatchMethodOptions

        opts = BatchMethodOptions(
            opt_initial_hessian="model",
            transition_state_opt_initial_hessian="auto",
        )
        ts_resolved = opts.resolve_role_options(is_transition_state=True)
        assert ts_resolved["opt_initial_hessian"] == "model"

    def test_auto_sentinel_for_recalc_hess_inherits_common(self) -> None:
        from acp.calculations.batch.options import BatchMethodOptions

        opts = BatchMethodOptions(
            opt_recalc_hess=10,
            transition_state_opt_recalc_hess="auto",
        )
        ts_resolved = opts.resolve_role_options(is_transition_state=True)
        assert ts_resolved["opt_recalc_hess"] == 10

    def test_none_common_falls_to_role_default(self) -> None:
        from acp.calculations.batch.options import BatchMethodOptions

        opts = BatchMethodOptions()
        ts_resolved = opts.resolve_role_options(is_transition_state=True)
        assert ts_resolved["opt_recalc_hess"] == 5

    # -- cross-contamination: TS override never affects INT ────────────────

    def test_ts_override_never_affects_int(self) -> None:
        from acp.calculations.batch.options import BatchMethodOptions

        opts = BatchMethodOptions(
            transition_state_opt_trust_radius=0.15,
            transition_state_opt_initial_hessian="model",
            transition_state_opt_recalc_hess=3,
        )
        ts_resolved = opts.resolve_role_options(is_transition_state=True)
        int_resolved = opts.resolve_role_options(is_transition_state=False)
        assert ts_resolved["opt_trust_radius"] == 0.15
        assert ts_resolved["opt_initial_hessian"] == "model"
        assert ts_resolved["opt_recalc_hess"] == 3
        assert int_resolved == {}

    def test_int_override_never_affects_ts(self) -> None:
        from acp.calculations.batch.options import BatchMethodOptions

        opts = BatchMethodOptions(
            minimum_opt_trust_radius=0.10,
            minimum_opt_initial_hessian="calculate",
            minimum_opt_recalc_hess=2,
        )
        ts_resolved = opts.resolve_role_options(is_transition_state=True)
        int_resolved = opts.resolve_role_options(is_transition_state=False)
        assert ts_resolved["opt_trust_radius"] == 0.3
        assert ts_resolved["opt_initial_hessian"] == "calculate"
        assert ts_resolved["opt_recalc_hess"] == 5
        assert int_resolved["opt_trust_radius"] == 0.10
        assert int_resolved["opt_initial_hessian"] == "calculate"
        assert int_resolved["opt_recalc_hess"] == 2

    # -- for_role priority fix ────────────────────────────────────────────

    def test_for_role_int_override_beats_common(self) -> None:
        from acp.calculations.batch.options import BatchMethodOptions

        opts = BatchMethodOptions(
            optimization_method="B3LYP",
            minimum_method="r2SCAN-3c",
        )
        method, basis = opts.for_role(is_transition_state=False)
        assert method == "r2SCAN-3c"

    def test_for_role_ts_isolation_from_minimum(self) -> None:
        from acp.calculations.batch.options import BatchMethodOptions

        opts = BatchMethodOptions(
            minimum_method="r2SCAN-3c",
            optimization_method="B3LYP",
        )
        method, _ = opts.for_role(is_transition_state=True)
        assert method == "B3LYP"

    def test_for_role_ts_uses_transition_state_method(self) -> None:
        from acp.calculations.batch.options import BatchMethodOptions

        opts = BatchMethodOptions(
            optimization_method="B3LYP",
            transition_state_method="wB97X-D4",
        )
        method, _ = opts.for_role(is_transition_state=True)
        assert method == "wB97X-D4"

    # -- cache_key includes role-override fields ──────────────────────────

    def test_cache_key_includes_role_overrides(self) -> None:
        from acp.calculations.batch.options import BatchMethodOptions

        opts_base = BatchMethodOptions()
        opts_ts = BatchMethodOptions(transition_state_opt_trust_radius=0.15)
        opts_int = BatchMethodOptions(minimum_opt_trust_radius=0.10)
        assert opts_base.cache_key != opts_ts.cache_key
        assert opts_base.cache_key != opts_int.cache_key
        assert opts_ts.cache_key != opts_int.cache_key

    # -- flags emission for role-override fields ──────────────────────────

    def test_flags_emission_role_overrides(self) -> None:
        from acp.scheduler.jobs import batchoptimize_method_flags

        method = {
            "transition_state_opt_trust_radius": 0.15,
            "transition_state_opt_initial_hessian": "model",
            "transition_state_opt_recalc_hess": 3,
            "minimum_opt_trust_radius": 0.10,
            "minimum_opt_initial_hessian": "calculate",
            "minimum_opt_recalc_hess": 2,
        }
        flags = batchoptimize_method_flags(method)
        assert "--transition-state-opt-trust-radius" in flags
        assert "0.15" in flags
        assert "--transition-state-opt-initial-hessian" in flags
        assert "model" in flags
        assert "--transition-state-opt-recalc-hess" in flags
        assert "3" in flags
        assert "--minimum-opt-trust-radius" in flags
        assert "0.1" in flags
        assert "--minimum-opt-initial-hessian" in flags
        assert "calculate" in flags
        assert "--minimum-opt-recalc-hess" in flags
        assert "2" in flags

    # -- CLI round trip with aliases ──────────────────────────────────────

    def test_cli_round_trip_with_canonical_names(self, tmp_path: Path) -> None:
        options = _options_from_cli(
            tmp_path,
            [
                "--transition-state-opt-trust-radius", "0.15",
                "--transition-state-opt-initial-hessian", "model",
                "--transition-state-opt-recalc-hess", "3",
                "--minimum-opt-trust-radius", "0.10",
                "--minimum-opt-initial-hessian", "calculate",
                "--minimum-opt-recalc-hess", "2",
            ],
        )
        assert options.transition_state_opt_trust_radius == 0.15
        assert options.transition_state_opt_initial_hessian == "model"
        assert options.transition_state_opt_recalc_hess == 3
        assert options.minimum_opt_trust_radius == 0.10
        assert options.minimum_opt_initial_hessian == "calculate"
        assert options.minimum_opt_recalc_hess == 2

    def test_cli_round_trip_with_aliases(self, tmp_path: Path) -> None:
        options = _options_from_cli(
            tmp_path,
            [
                "--ts-opt-trust-radius", "0.15",
                "--ts-opt-initial-hessian", "model",
                "--ts-opt-recalc-hess", "3",
                "--int-opt-trust-radius", "0.10",
                "--int-opt-initial-hessian", "calculate",
                "--int-opt-recalc-hess", "2",
            ],
        )
        assert options.transition_state_opt_trust_radius == 0.15
        assert options.transition_state_opt_initial_hessian == "model"
        assert options.transition_state_opt_recalc_hess == 3
        assert options.minimum_opt_trust_radius == 0.10
        assert options.minimum_opt_initial_hessian == "calculate"
        assert options.minimum_opt_recalc_hess == 2

    def test_cli_recalc_hess_role_auto_normalized(self, tmp_path: Path) -> None:
        options = _options_from_cli(
            tmp_path,
            ["--transition-state-opt-recalc-hess", "auto"],
        )
        assert options.transition_state_opt_recalc_hess == "auto"

    # -- engine .inp level: TS override reaches input ─────────────────────

    def test_ts_override_reaches_engine_kwargs(
        self, tmp_path: Path, fake_backend: object,
    ) -> None:
        from acp.calculations.batch.options import BatchMethodOptions
        from tests.conftest import FakeBackend

        assert isinstance(fake_backend, FakeBackend)
        methods = BatchMethodOptions(
            transition_state_opt_trust_radius=0.15,
            transition_state_opt_initial_hessian="model",
            transition_state_opt_recalc_hess=3,
        )
        engine = BatchOptimizeEngine(
            work_root=tmp_path / "task" / "WORK",
            result_root=tmp_path / "task" / "RESULT",
            methods=methods,
        )
        kwargs = engine._optimization_kwargs(is_ts=True)
        assert kwargs["trust_radius"] == 0.15
        assert kwargs["initial_hessian"] == "model"
        assert kwargs["recalc_hess"] == 3

    def test_int_override_reaches_engine_kwargs(
        self, tmp_path: Path, fake_backend: object,
    ) -> None:
        from acp.calculations.batch.options import BatchMethodOptions
        from tests.conftest import FakeBackend

        assert isinstance(fake_backend, FakeBackend)
        methods = BatchMethodOptions(
            minimum_opt_trust_radius=0.10,
            minimum_opt_initial_hessian="calculate",
            minimum_opt_recalc_hess=2,
        )
        engine = BatchOptimizeEngine(
            work_root=tmp_path / "task" / "WORK",
            result_root=tmp_path / "task" / "RESULT",
            methods=methods,
        )
        kwargs = engine._optimization_kwargs(is_ts=False)
        assert kwargs["trust_radius"] == 0.10
        assert kwargs["initial_hessian"] == "calculate"
        assert kwargs["recalc_hess"] == 2

    def test_ts_override_int_unaffected(
        self, tmp_path: Path, fake_backend: object,
    ) -> None:
        from acp.calculations.batch.options import BatchMethodOptions
        from tests.conftest import FakeBackend

        assert isinstance(fake_backend, FakeBackend)
        methods = BatchMethodOptions(
            transition_state_opt_trust_radius=0.15,
        )
        engine = BatchOptimizeEngine(
            work_root=tmp_path / "task" / "WORK",
            result_root=tmp_path / "task" / "RESULT",
            methods=methods,
        )
        ts_kwargs = engine._optimization_kwargs(is_ts=True)
        int_kwargs = engine._optimization_kwargs(is_ts=False)
        assert ts_kwargs["trust_radius"] == 0.15
        assert "trust_radius" not in int_kwargs
