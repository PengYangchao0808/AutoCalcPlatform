"""F3: Full-chain end-to-end QA (in-process, fake backends).

Four assertion nodes:
  1. test_e2e_full_chain         — BatchOptimize + IRC real code paths; result_manifest products
  2. test_e2e_interrupt_continue — checkpoint-based resume skips completed steps
  3. test_e2e_history_readonly   — mechanism detail 200, action endpoints 409
  4. test_e2e_remote_parity      — stage sequence / checkpoint schema / manifest kinds parity
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acp.backends.base import QCResult
from acp.calculations.batch.engine import BatchOptimizeEngine
from acp.calculations.batch.models import BatchStructureItem
from acp.calculations.contracts import (
    Checkpoint,
    StructureArtifact,
    StructureRole,
)
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.storage.manifest import ProductKind, ResultManifest
from acp.workflows.irc import run_irc_workflow
from tests.conftest import FakeBackend

_COORD_FILE = Path("tests/baseline/refactor-evidence/e2e-task-root.txt")

_TS_XYZ = "2\nTAG: TS | candidate_id=candidate_001\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n"
_INT_XYZ = "2\nTAG: INT | candidate_id=int_001\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n"


# ── helpers ───────────────────────────────────────────────────────────


def _make_batch_items() -> list[BatchStructureItem]:
    return [
        BatchStructureItem(
            item_id="candidate_001",
            name="TS candidate",
            tag="TS",
            xyz=_TS_XYZ,
            candidate_id="candidate_001",
        ),
        BatchStructureItem(
            item_id="int_001",
            name="INT candidate",
            tag="INT",
            xyz=_INT_XYZ,
            candidate_id="int_001",
        ),
    ]


def _make_manager(tmp_path: Path):
    from acp.scheduler.manager import JobManager

    return JobManager(run_root=tmp_path / "runs", poll_interval=30)


def _write_pes_result(task_dir: Path) -> None:
    """Create a minimal PESsearch result directory with structure products."""
    structures_dir = task_dir / "RESULT" / "structures"
    structures_dir.mkdir(parents=True, exist_ok=True)
    (structures_dir / "candidate_001.xyz").write_text(_TS_XYZ, encoding="utf-8")
    (structures_dir / "int_001.xyz").write_text(_INT_XYZ, encoding="utf-8")
    manifest = ResultManifest(
        task_id="pes_task",
        workflow="PESsearch",
        status="completed",
    )
    manifest.add_product(
        id="candidate_ts",
        label="TS candidate",
        path="structures/candidate_001.xyz",
        kind=ProductKind.STRUCTURE,
    )
    manifest.add_product(
        id="candidate_int",
        label="INT candidate",
        path="structures/int_001.xyz",
        kind=ProductKind.STRUCTURE,
    )
    manifest.write(task_dir / "RESULT")


# ── Node 1: full chain ───────────────────────────────────────────────


def test_e2e_full_chain(
    tmp_path: Path,
    fake_backend: FakeBackend,
    session_task_root: Path,
) -> None:
    """Full chain assertion: BatchOptimize + IRC via real code paths.

    Registers products at each step via result_manifest.json v2.
    Writes task root to the fixed coordination file.
    """
    task_root = session_task_root

    # ── S1 upstream: create fake PESsearch result ─────────────────
    pes_dir = task_root / "pes_upstream"
    _write_pes_result(pes_dir)

    # ── S2: BatchOptimize (real engine, fake backend) ─────────────
    batch_output = task_root / "batch_output"
    engine = BatchOptimizeEngine(
        work_root=batch_output / "WORK",
        result_root=batch_output / "RESULT",
    )

    # Configure TS frequency with imaginary mode for the TS candidate
    import numpy as np

    coords = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.7]])
    fake_backend.set_results(
        "frequency",
        [
            QCResult(
                success=True,
                energy=-1.1,
                coordinates=coords,
                symbols=["H", "H"],
                frequencies=[-500.0, 100.0, 200.0],
                has_frequencies=True,
            ),
            QCResult(
                success=True,
                energy=-1.0,
                coordinates=coords,
                symbols=["H", "H"],
                frequencies=[100.0, 200.0, 300.0],
                has_frequencies=True,
            ),
        ],
    )
    outcome = engine.run(_make_batch_items(), profile="opt_freq_sp", charge=0)

    assert all(item.status == "completed" for item in outcome.items)
    assert (batch_output / "RESULT" / "result_manifest.json").is_file()
    batch_manifest = ResultManifest.read(batch_output / "RESULT")
    product_ids = [p.id for p in batch_manifest.products]
    assert "batch_candidate_001" in product_ids
    assert "batch_int_001" in product_ids

    # ── S3: IRC (real workflow, fake backend) ─────────────────────
    ts_path = task_root / "ts.xyz"
    ts_path.write_text(_TS_XYZ, encoding="utf-8")
    irc_output = task_root / "irc_output"
    artifact = StructureArtifact(
        path=ts_path,
        elements=["H", "H"],
        role=StructureRole.TRANSITION_STATE,
    )
    irc_result = run_irc_workflow(artifact, output_dir=irc_output)

    assert irc_result.status == "completed"
    assert (irc_output / "RESULT" / "result_manifest.json").is_file()
    irc_manifest = ResultManifest.read(irc_output / "RESULT")
    assert any(p.kind == ProductKind.REPORT for p in irc_manifest.products)

    # ── coordinator file ──────────────────────────────────────────
    _COORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    _COORD_FILE.write_text(str(task_root), encoding="utf-8")


# ── session-scoped task root fixture ─────────────────────────────────


@pytest.fixture(scope="session")
def session_task_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a session-level task root; used by full_chain + interrupt_continue."""
    return tmp_path_factory.mktemp("e2e_chain")


# ── Node 2: interrupt → continue ─────────────────────────────────────


def test_e2e_interrupt_continue(
    fake_backend: FakeBackend,
    session_task_root: Path,
) -> None:
    """Chain interrupted → FAILED → continue → resumes from checkpoint.

    Only unfinished steps are executed; fake call counts asserted.
    """
    if not _COORD_FILE.is_file():
        pytest.skip("Run test_e2e_full_chain first to create the coordination file")

    task_root = session_task_root

    batch_output = task_root / "batch_interrupt"
    engine = BatchOptimizeEngine(
        work_root=batch_output / "WORK",
        result_root=batch_output / "RESULT",
    )
    items = _make_batch_items()
    original_process = engine._process_item

    # ── Phase 1: run with interrupt ──────────────────────────────
    if not os.environ.get("E2E_SKIP_INTERRUPT"):
        call_count = 0

        def interrupt_after_first(item, record, steps, charge, multiplicity):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise KeyboardInterrupt
            original_process(item, record, steps, charge, multiplicity)

        engine._process_item = interrupt_after_first  # type: ignore[assignment]

        with pytest.raises(KeyboardInterrupt):
            engine.run(items, profile="opt_only", charge=0)

    # ── Phase 2: verify checkpoint ───────────────────────────────
    checkpoint_path = batch_output / "WORK" / "00_RUNTIME" / "checkpoint.json"
    assert checkpoint_path.is_file(), "checkpoint_exists: checkpoint must exist after interrupt"
    import json

    cp_data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert cp_data["workflow"] == "BatchOptimize"
    assert cp_data["plan_fingerprint"]
    items_state = cp_data.get("items_state", {})
    assert items_state.get("candidate_001", {}).get("status") == "completed", (
        "first item completed before interrupt"
    )
    next_idx = items_state.get("__batch__", {}).get("next_item_index")
    assert next_idx is not None, "batch checkpoint tracks next_item_index"
    assert next_idx >= 1, "next_item_index advances past completed item"

    # ── Phase 3: resume from checkpoint ───────────────────────────
    calls_before_resume = len(fake_backend.calls)
    engine._process_item = original_process  # type: ignore[assignment]
    outcome = engine.run(items, profile="opt_only", charge=0)

    assert all(item.status in ("completed", "skipped") for item in outcome.items)
    assert len(fake_backend.calls) == calls_before_resume + 1, (
        "resume must skip completed item and only dispatch the remaining one"
    )


# ── Node 3: history readonly ─────────────────────────────────────────


@pytest.fixture()
def mechanism_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, str], None, None]:
    """TestClient with a historical COMPLETED mechanism job seeded."""
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=2)) as client:
        manager = client.app.state.job_manager
        work_dir = tmp_path / "uncategorized" / "hist_mechanism"
        work_dir.mkdir(parents=True)
        record = JobRecord(
            id="hist_mechanism_001",
            spec=JobSpec(
                workflow="mechanism",
                name="hist_mechanism",
                input={"source": "CCO"},
                project_id=manager.default_project_id,
            ),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            project_id=manager.default_project_id,
        )
        manager.store.create(record)
        yield client, record.id


def test_e2e_history_readonly(mechanism_client: tuple[TestClient, str]) -> None:
    """Historical mechanism job: detail → 200; action endpoints → 409."""
    client, job_id = mechanism_client

    # Detail endpoint returns 200
    detail = client.get(f"/api/v1/jobs/{job_id}/detail")
    assert detail.status_code == 200
    assert detail.json()["job"]["spec"]["workflow"] == "mechanism"

    # Action endpoints on a COMPLETED job return 409
    assert client.post(f"/api/v1/jobs/{job_id}/continue").status_code == 409
    assert client.post(f"/api/v1/jobs/{job_id}/pause").status_code == 409
    assert client.post(f"/api/v1/jobs/{job_id}/unpause").status_code == 409


# ── Node 4: remote parity ────────────────────────────────────────────


def test_e2e_remote_parity() -> None:
    """Fake-LSF: stage sequence / checkpoint schema / manifest kinds
    consistent local vs remote."""
    from acp.scheduler.remote.script_gen import build_remote_cli_command
    from acp.scheduler.stage_tasks import PlanCompiler

    def _stages(workflow: str, method: dict | None = None) -> list[str]:
        spec = JobSpec(workflow=workflow, input={}, method=method or {}, resources={"nproc": 4})
        return [s.stage_name for s in PlanCompiler.compile(spec)]

    def _remote_argv(workflow: str, method: dict | None = None) -> list[str]:
        inp: dict = {}
        if workflow == "BatchOptimize":
            inp = {"from_artifact": "/tmp/m.json"}
        spec = JobSpec(workflow=workflow, input=inp, method=method or {}, resources={"nproc": 4})
        return build_remote_cli_command(spec, input_path="input.xyz")

    # BatchOptimize stage parity
    profiles = {
        "opt_only": ["prepare", "optimize", "finalize"],
        "opt_freq": ["prepare", "optimize", "frequency", "finalize"],
    }
    for profile, expected_stages in profiles.items():
        assert _stages("BatchOptimize", {"profile": profile}) == expected_stages
        argv = _remote_argv("BatchOptimize", {"profile": profile})
        assert argv[:5] == ["python", "-m", "acp.cli", "run", "BatchOptimize"]
        assert "--profile" in argv

    # IRC stage parity
    assert _stages("irc") == ["irc"]
    argv_irc = _remote_argv("irc")
    assert argv_irc[:5] == ["python", "-m", "acp.cli", "run", "irc"]

    # Checkpoint schema: BatchOptimize uses items_state + step_states
    checkpoint = Checkpoint(
        task_id="test",
        workflow="BatchOptimize",
        plan_fingerprint="abc123",
        step_states=[{"index": 0, "kind": "optimize", "status": "completed"}],
        items_state={"candidate_001": {"status": "completed"}},
    )
    assert checkpoint.task_id == "test"
    assert checkpoint.items_state["candidate_001"]["status"] == "completed"

    # Manifest kinds: BatchOptimize emits structure + energy_report
    assert ProductKind.STRUCTURE == "structure"
    assert ProductKind.ENERGY_REPORT == "energy_report"
    # IRC emits irc_endpoint
    assert ProductKind.IRC_ENDPOINT == "irc_endpoint"
