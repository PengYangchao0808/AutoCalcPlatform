"""Real-ORCA scheduler E2E: in-place rerun/edit keep RESULT where promised.

Guards BUG-1(b)/GAP-1 through the production scheduler.  A ``singlepoint``
job is submitted via :class:`JobManager` and run to completion, then re-run in
place once and edit-recalculated in place once.  After EACH round the original
task directory must still be the same directory (no ``<work_dir>_1`` sibling),
its ``RESULT/result_manifest.json`` must exist AND parse through the
downstream reader (:func:`acp.results.manifest.load_result_manifest`), and the
preserved ``.structure_history`` must survive.

Requires the real ORCA binary and ``--run-slow`` (three-state rule: a skipped
run is NOT_VERIFIED, never a pass).  Remote node subprocesses use the same
``_resolve_output_dir`` code path, so local verification covers both.
"""

from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

from acp.results.manifest import load_result_manifest
from acp.scheduler.job_edit import compute_source_revision
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from tests.conftest import requires_orca

_WATER_XYZ = (
    "3\nwater\n"
    "O 0.000000 0.000000 0.117790\n"
    "H 0.000000 0.755450 -0.471160\n"
    "H 0.000000 -0.755450 -0.471160\n"
)

_ORCA_RUNTIME_PATH = "/opt/acp/venv/bin:/home/<user>/openmpi418/bin"
_ORCA_RUNTIME_LD = "/home/<user>/openmpi418/lib:/home/<user>/orca611"


def _singlepoint_spec(name: str, *, basis: str = "def2-SVP") -> JobSpec:
    return JobSpec(
        workflow="singlepoint",
        name=name,
        input={
            "source_type": "xyz_text",
            "source": _WATER_XYZ,
            "charge": 0,
            "multiplicity": 1,
        },
        method={
            "schema_id": "dft_singlepoint",
            "profile_id": "default",
            "levels": {
                "sp": {
                    "functional": "HF",
                    "basis": basis,
                    "dispersion": "none",
                    "solvent_model": "none",
                    "solvent": "",
                }
            },
        },
        resources={"nproc": 1, "mem": 2},
    )


def _wait_terminal(manager: JobManager, job_id: str, timeout: float = 600.0) -> JobRecord:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = manager.get(job_id)
        if (
            record is not None
            and record.status.is_terminal
            and job_id not in manager._submission_jobs
        ):
            return record
        time.sleep(0.5)
    raise AssertionError(f"job {job_id} did not settle within {timeout}s")


def _assert_result_in_place(work_dir: Path) -> None:
    sibling = work_dir.parent / f"{work_dir.name}_1"
    assert not sibling.exists(), f"products redirected to a _1 sibling: {sibling}"
    assert (work_dir / ".structure_history").is_dir(), ".structure_history not preserved"
    manifest_path = work_dir / "RESULT" / "result_manifest.json"
    assert manifest_path.is_file(), f"missing {manifest_path}"
    manifest = load_result_manifest(work_dir)
    assert manifest is not None, "RESULT/result_manifest.json unreadable downstream"


@requires_orca
@pytest.mark.slow
def test_singlepoint_rerun_and_edit_keep_result_manifest_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", f"{_ORCA_RUNTIME_PATH}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("LD_LIBRARY_PATH", _ORCA_RUNTIME_LD)

    manager = JobManager(run_root=tmp_path / "runs", max_running=1)
    try:
        record = manager.submit(_singlepoint_spec("water_sp_e2e"))
        first = _wait_terminal(manager, record.id)
        assert first.status == JobStatus.COMPLETED, first.error
        assert first.exit_code == 0
        work_dir = Path(first.work_dir)
        assert not (work_dir.parent / f"{work_dir.name}_1").exists()
        assert (work_dir / "RESULT" / "result_manifest.json").is_file()

        history = work_dir / ".structure_history"
        history.mkdir(exist_ok=True)
        seed = history / "seed_marker.txt"
        seed.write_text("preserve me\n", encoding="utf-8")

        assert manager.rerun_job(record.id) is not None
        rerun = _wait_terminal(manager, record.id)
        assert rerun.status == JobStatus.COMPLETED, rerun.error
        assert Path(rerun.work_dir) == work_dir
        assert seed.is_file(), ".structure_history lost its prior content on rerun"
        _assert_result_in_place(work_dir)

        edited = replace(rerun.spec, method=_singlepoint_spec("x", basis="def2-TZVP").method)
        manager.edit_recalculate(
            record.id,
            mode="in_place",
            new_spec=edited,
            expected_source_revision=compute_source_revision(rerun),
            request_id="e2e-edit-1",
            payload_hash="e2e-hash",
            payload_json="{}",
        )
        edited_done = _wait_terminal(manager, record.id)
        assert edited_done.status == JobStatus.COMPLETED, edited_done.error
        assert Path(edited_done.work_dir) == work_dir
        assert seed.is_file(), ".structure_history lost on edit-recalculate"
        _assert_result_in_place(work_dir)
    finally:
        manager.shutdown()


def test_rerun_skips_collection_products_and_records_audit(tmp_path: Path) -> None:
    """todo 8 (GAP-7): multi-frame collections never abort an in-place rerun.

    Runs the production manager path without ORCA: the collection product is
    skipped (not raised on), the single product is preserved, and the skip is
    recorded in ``attempt_history``.
    """
    from acp.storage.manifest import ResultManifest

    manager = JobManager(run_root=tmp_path / "runs", poll_interval=30)
    manager._execute_submission = lambda job_id: None  # type: ignore[method-assign]
    try:
        work_dir = manager.run_root / "default" / "recalc_collections_e2e"
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "input.xyz").write_text(_WATER_XYZ, encoding="utf-8")
        record = JobRecord(
            id="recalc_collections_e2e",
            spec=JobSpec(
                workflow="Confsearch", name="recalc_collections_e2e", input={"source": "CCO"}
            ),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            project_id=manager.default_project_id,
            group_id="recalc_collections_e2e",
        )
        manager.store.create(record)

        result_dir = work_dir / "RESULT"
        result_dir.mkdir()
        (result_dir / "all_conformers.xyz").write_text(
            f"{_WATER_XYZ}\n{_WATER_XYZ}", encoding="utf-8"
        )
        (result_dir / "best.xyz").write_text(_WATER_XYZ, encoding="utf-8")
        manifest = ResultManifest(workflow="Confsearch", status="completed")
        manifest.add_product(
            "all_conformers", "Ranked conformers (XYZ)", "all_conformers.xyz", "structure"
        )
        manifest.add_product("rank1", "Rank 1", "best.xyz", "structure")
        manifest.write(result_dir)

        assert manager.rerun_job(record.id) is not None
        updated = manager.get(record.id)
        skipped = updated.result["attempt_history"][-1]["skipped_collections"]
        assert [row["id"] for row in skipped] == ["all_conformers"]
        assert Path(updated.work_dir) == work_dir
        assert not (work_dir.parent / f"{work_dir.name}_1").exists()
    finally:
        manager.shutdown()
