"""Deterministic reproductions registered by plan todo 1 (R0 contract freeze).

Four reproductions from the improvement review, mirrored as fast, deterministic,
pytest-collectible seeds. All inputs live under ``tmp_path``; no real QC, no
network, no wall-clock/sleep dependence, no unseeded randomness.

Each reproduction pairs a GREEN characterization (the observable defect setup
that must keep holding) with a target-behavior test. Target tests are
``xfail(strict=True)`` because the desired behavior is FUTURE work; the reason
string names the owning todo and the removal rule. When the owning todo lands,
the strict xfail turns RED and forces the owner to convert/remove it — the
suite is never silenced by changing the ``-m "not slow"`` command.

Reproductions:
  (a) TS Mode first run N vectors -> resume 0 vectors   (owning todos 4/12)
  (b) delayed sync: jobs=paused, tasks=running          (owning todos 5/10)
  (c) remote manifest cached, structures/`.out` not     (owning todos 6/11/15)
  (d) scan_method_flags accepts empty/invalid coords    (owning todos 9/13)
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from acp.calculations.contracts import ArtifactRef, CalculationResult
from acp.calculations.tsmode.contracts import TsmodeOptimizationSettings, TsmodeRequest
from acp.calculations.tsmode.engine import TsmodeEngine
from acp.calculations.tsmode.source import load_bundle_from_files
from tests.tsmode_synthetic import make_consistent_pair, write_out_file

# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────


def _tsmode_request() -> TsmodeRequest:
    return TsmodeRequest(
        source={"kind": "test"},
        source_mode_index=8,
        optimization=TsmodeOptimizationSettings(require_verified_mapping=False),
        resources={},
        request_id="req_repro",
    )


def _run_engine_with_vector_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    task_root: Path,
) -> dict[str, int]:
    """Run TsmodeEngine once with mocked stages; the frequency log carries vectors.

    The log is written in the synthetic ORCA format, so the engine parses mode
    vectors exactly like a mocked-subprocess first run. Returns the QC call
    counters shared with the (still-installed) fakes — a later resume that
    re-runs a stage bumps them.
    """
    out_path, hess_path, _coords, freqs_by_native, cart_modes_by_native = make_consistent_pair(
        tmp_path
    )
    bundle = load_bundle_from_files(out_path, hess_path)
    optimized = np.asarray(bundle.coordinates_angstrom) + 0.02

    log_path = tmp_path / "freq_final.log"
    write_out_file(log_path, optimized, freqs_by_native, cart_modes_by_native)
    frequencies = [freqs_by_native[index] for index in sorted(freqs_by_native)]

    calls = {"optimize": 0, "frequency": 0}

    def fake_optimize(req):
        calls["optimize"] += 1
        return CalculationResult(
            energy=-100.5,
            coords=[[float(v) for v in row] for row in optimized],
            status="completed",
            artifacts=[
                ArtifactRef(path=Path("ts_opt.out"), type="output"),
                ArtifactRef(path=Path("ts_opt.log"), type="log"),
            ],
        )

    def fake_frequency(req):
        calls["frequency"] += 1
        return CalculationResult(
            energy=-100.6,
            frequencies=list(frequencies),
            status="completed",
            artifacts=[ArtifactRef(path=log_path, type="log")],
        )

    monkeypatch.setattr("acp.calculations.tsmode.engine.run_optimize", fake_optimize)
    monkeypatch.setattr("acp.calculations.tsmode.engine.run_frequency", fake_frequency)
    TsmodeEngine(config={}).run(_tsmode_request(), bundle, task_root)
    return calls


def _read_normal_modes(task_root: Path) -> dict:
    return json.loads((task_root / "RESULT" / "tsmode" / "normal_modes.json").read_text())


# ─────────────────────────────────────────────────────────────────────────────
# (a) TS Mode: first run publishes vectors for every mode; resume must too
# ─────────────────────────────────────────────────────────────────────────────


def test_repro_a_first_run_publishes_vectors_for_every_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduction setup (GREEN): a mocked first run emits 9 modes WITH vectors.

    Mirrors improvement-plan §1 observation "first run: every mode has vectors".
    The defect appears on resume (see the strict-xfail companion below).
    """
    task_root = tmp_path / "task"
    _run_engine_with_vector_log(tmp_path, monkeypatch, task_root)

    normal_modes = _read_normal_modes(task_root)
    assert normal_modes["schema_version"] == "normal_modes_v1"
    modes = normal_modes["modes"]
    assert len(modes) == 9, f"expected 9 native modes, got {len(modes)}"
    missing = [m["mode_index"] for m in modes if not m["vectors"]]
    assert missing == [], f"first run dropped vectors for modes {missing}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Reproduction (a): first run 9 modes WITH vectors -> resume rewrites "
        "normal_modes with 0 vectors. Desired: resumed vectors non-empty and equal "
        "to the fresh-run control. Owning todos 4/12 (R5). "
        "Removal rule: convert/remove this xfail when todo 4 lands v2 credentials + "
        "resume-equals-fresh-run (acceptance in todo 12)."
    ),
)
def test_repro_a_resume_keeps_mode_vectors_equal_to_fresh_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduction (a): checkpoint resume must not drop the mode vectors."""
    task_root = tmp_path / "task"
    calls = _run_engine_with_vector_log(tmp_path, monkeypatch, task_root)
    first = _read_normal_modes(task_root)
    assert all(m["vectors"] for m in first["modes"]), "fresh run must carry vectors"

    # Second engine.run with the checkpoint intact: pure resume, zero QC calls.
    out_path, hess_path, *_ = make_consistent_pair(tmp_path)
    bundle = load_bundle_from_files(out_path, hess_path)
    TsmodeEngine(config={}).run(_tsmode_request(), bundle, task_root)
    assert calls["optimize"] == 1, "resume re-ran the optimize stage"
    assert calls["frequency"] == 1, "resume re-ran the frequency stage"

    second = _read_normal_modes(task_root)
    assert {m["mode_index"] for m in second["modes"]} == {
        m["mode_index"] for m in first["modes"]
    }, "mode index set changed across resume"
    for mode in second["modes"]:
        assert mode["vectors"], (
            f"resume dropped vectors for mode {mode['mode_index']} "
            "(first run had them; desired: resume equals fresh run)"
        )
        twin = next(m for m in first["modes"] if m["mode_index"] == mode["mode_index"])
        assert mode["vectors"] == twin["vectors"], (
            f"resumed vector for mode {mode['mode_index']} differs from the fresh run"
        )


# ─────────────────────────────────────────────────────────────────────────────
# (b) delayed sync: jobs=paused, tasks=running
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Reproduction (b): a delayed stale RUNNING sync overwrites the newer PAUSED "
        "projection (jobs=paused, tasks=running). Desired: tasks projects the current "
        "jobs row; stale snapshots never overwrite newer states. Owning todos 5/10 "
        "(R3). Removal rule: convert/remove this xfail when todo 5 lands "
        "same-transaction jobs-row projection (acceptance in todo 10)."
    ),
)
def test_repro_b_delayed_sync_keeps_tasks_aligned_with_jobs(tmp_path: Path) -> None:
    """Reproduction (b): after new-PAUSED then delayed-old-RUNNING, tasks must stay paused."""
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
    from acp.scheduler.migrations import migrate
    from acp.scheduler.store import JobStore
    from acp.scheduler.tasks import TaskIndex

    db = tmp_path / "jobs.db"
    migrate(db)
    store = JobStore(db)
    idx = TaskIndex(db)
    spec = JobSpec(
        workflow="Confsearch",
        name="CCO_search",
        molecule_name="CCO",
        task_name="search",
        remark="r",
    )
    # jobs is authoritative: the job is PAUSED.
    paused = JobRecord(id="j_delayed", spec=spec, status=JobStatus.PAUSED, work_dir="/tmp/jd")
    store.create(paused)
    idx.sync_from_job(paused)
    assert store.get("j_delayed").status == JobStatus.PAUSED
    assert idx.get("j_delayed")["status"] == "paused"

    # A delayed observation of the OLD RUNNING state arrives late and syncs.
    stale = JobRecord(id="j_delayed", spec=spec, status=JobStatus.RUNNING, work_dir="/tmp/jd")
    idx.sync_job_transition(stale)

    jobs_status = store.get("j_delayed").status
    tasks_status = idx.get("j_delayed")["status"]
    assert jobs_status == JobStatus.PAUSED, "jobs row must remain the authority (paused)"
    assert tasks_status == jobs_status.value, (
        f"delayed stale sync diverged the projection: jobs={jobs_status.value}, "
        f"tasks={tasks_status} (desired: tasks==jobs)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# (c) remote manifest cached but structures/.out not cached
# ─────────────────────────────────────────────────────────────────────────────

_C_MANIFEST = "RESULT/result_manifest.json"
_C_STRUCTURE = "RESULT/simple/optimized.xyz"
_C_OUT = "RESULT/simple/opt.out"
_C_JOB = "r0_remote_job"


class _ColdCacheFetcher:
    """Fake node: manifest + structure + `.out` always available remotely."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def read_file(self, record: object, rel_path: str) -> bytes:
        self.calls.append(rel_path)
        if rel_path == _C_MANIFEST:
            from acp.storage.manifest import ResultManifest

            manifest = ResultManifest(task_id=_C_JOB, workflow="optimize", status="completed")
            manifest.add_product(
                id="optimized",
                label="optimized structure",
                path="simple/optimized.xyz",
                kind="structure",
            )
            manifest.add_product(
                id="opt_out", label="orca output", path="simple/opt.out", kind="file"
            )
            return json.dumps(manifest.to_dict()).encode()
        if rel_path == _C_STRUCTURE:
            return b"2\nattempt-remote structure\nH 0.0 0.0 0.0\n"
        if rel_path == _C_OUT:
            return b"ORCA fake output\n"
        raise FileNotFoundError(rel_path)


def _remote_record(tmp_path: Path):
    """Completed remote JobRecord whose work_dir deliberately has NO RESULT tree."""
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

    work_dir = tmp_path / "r0_remote_task"
    work_dir.mkdir(parents=True, exist_ok=True)
    return JobRecord(
        id=_C_JOB,
        spec=JobSpec(
            workflow="optimize",
            name="r0_remote_task",
            molecule_name="m",
            task_name="opt",
            remark="final",
        ),
        status=JobStatus.COMPLETED,
        work_dir=str(work_dir),
        remote_job_id="12345",
        result={"node": "fake_node", "remote_dir": "/fake/project/task"},
    )


@pytest.fixture()
def remote_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    """TestClient whose manager cache is seeded manifest-only (cold file cache)."""
    from acp.results.remote_structure_cache import RemoteStructureCache

    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1)) as client:
        manager = client.app.state.job_manager
        record = _remote_record(tmp_path)
        manager.store.create(record)
        # structure_cache is a read-only property; inject the backing singleton.
        fetcher = _ColdCacheFetcher()
        manager._remote_structure_cache = RemoteStructureCache(
            tmp_path, fetcher_factory=lambda _job_id: fetcher
        )
        manager.structure_cache.fetch_catalog(record, "optimize")
        yield client


def test_repro_c_manifest_only_cache_is_the_registered_setup(
    remote_client: TestClient,
) -> None:
    """Reproduction setup (GREEN): manifest cached; structure and `.out` are not.

    Mirrors improvement-plan §1 "fetch_catalog succeeded, manifest cached, but
    remote-present structure and .out were both uncached" — the cold-cache state
    full-warmup fixtures would otherwise mask.
    """
    cache = remote_client.app.state.job_manager.structure_cache
    assert cache.get_cached(_C_JOB, _C_MANIFEST) is not None
    assert cache.get_cached(_C_JOB, _C_STRUCTURE) is None
    assert cache.get_cached(_C_JOB, _C_OUT) is None


def test_repro_c_v2_results_served_from_remote_cache(remote_client: TestClient) -> None:
    response = remote_client.get(f"/api/v2/tasks/{_C_JOB}/results")
    assert response.status_code == 200, (
        f"manifest-only remote cache not consulted: {response.status_code} {response.text}"
    )
    body = response.json()
    product_ids = {product["id"] for product in body["products"]}
    assert {"optimized", "opt_out"} <= product_ids


def test_repro_c_v2_structure_download_fetches_on_demand(remote_client: TestClient) -> None:
    response = remote_client.get(f"/api/v2/tasks/{_C_JOB}/structures/optimized")
    assert response.status_code == 200, (
        f"structure not fetched on demand: {response.status_code} {response.text}"
    )
    assert b"attempt-remote structure" in response.content


def test_repro_c_v2_out_file_download_fetches_on_demand(remote_client: TestClient) -> None:
    response = remote_client.get(f"/api/v2/tasks/{_C_JOB}/files/{_C_OUT}")
    assert response.status_code == 200, (
        f".out not fetched on demand: {response.status_code} {response.text}"
    )
    assert b"ORCA fake output" in response.content


# ─────────────────────────────────────────────────────────────────────────────
# (d) scan_method_flags accepts empty/invalid coordinates
# ─────────────────────────────────────────────────────────────────────────────


def test_repro_d_generator_accepts_empty_and_invalid_coordinates() -> None:
    """Reproduction (GREEN): scan_method_flags is a generator, not a validator.

    Mirrors improvement-plan §1: empty coordinates emit only ``--scan-points``,
    and ``99,99,nan,inf`` / negative indices pass straight through. This is the
    observable defect the shared submission boundary (todos 9/13) must reject;
    the generator itself stays permissive by design (plan todo 9).
    """
    from acp.scheduler.jobs import scan_method_flags

    empty = scan_method_flags({"scan_points": 1}, {"coordinate": []})
    assert "--coordinate" not in empty
    assert empty == ["--scan-points", "1"]

    invalid = scan_method_flags({"scan_points": 2}, {"coordinate": "99,99,nan,inf"})
    assert invalid == ["--coordinate", "99,99,nan,inf", "--scan-points", "2"]

    negative = scan_method_flags({"scan_points": 2}, {"coordinate": "-1,0,1.0,2.0"})
    assert negative == ["--coordinate", "-1,0,1.0,2.0", "--scan-points", "2"]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Reproduction (d): the ACP submission boundary must reject empty/invalid "
        "scan coordinates with 422 (empty list, non-finite/out-of-range atoms, "
        "negative indices, points < 2). Today the boundary only catches a MISSING "
        "coordinate because it delegates to the permissive generator. Owning todos "
        "9/13 (R8). Removal rule: convert/remove this xfail when todo 9 lands the "
        "shared v1/v2 submission validation (acceptance in todo 13)."
    ),
)
def test_repro_d_submission_boundary_rejects_invalid_coordinates() -> None:
    from fastapi import HTTPException

    from acp.api.v1_routes import _validate_scan_submission

    invalid_cases = [
        ({"scan_points": 2}, {"coordinate": []}, "empty coordinate list"),
        ({"scan_points": 2}, {"coordinate": "99,99,nan,inf"}, "non-finite/out-of-range atoms"),
        ({"scan_points": 2}, {"coordinate": "-1,0,1.0,2.0"}, "negative atom index"),
        ({"scan_points": 1}, {"coordinate": "0,1,1.0,3.0"}, "points < 2"),
    ]
    for method, inp, label in invalid_cases:
        with pytest.raises(HTTPException) as excinfo:
            _validate_scan_submission("scan", method, inp)
        assert excinfo.value.status_code == 422, f"{label}: expected 422"
