"""Tests for the NMR Bruker job-materialisation in the scheduler runner.

Covers ``JobRunner._materialize_bruker_asset`` (asset resolution + zip
extraction) and ``_build_nmr_cmd`` with a ``mode: "bruker"`` experiment
payload.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np

from acp.scheduler.jobs import JobSpec
from acp.scheduler.runner import JobRunner


def _make_runner() -> JobRunner:
    """Construct a JobRunner without running its full __init__."""
    runner = JobRunner.__new__(JobRunner)
    runner.python = "/usr/bin/python"
    return runner


def _write_bruker_zip(dest: Path, experiments: dict[str, dict]) -> None:
    """Create a zip containing Bruker experiment subdirs.

    Args:
        experiments: ``{"Proton": {...}}`` — each value is written as
            ``fid`` (int32) + ``acqus`` text inside the subdir.
    """
    with zipfile.ZipFile(dest, "w") as zf:
        for name, files in experiments.items():
            zf.writestr(f"{name}/acqus", files["acqus"])
            raw = files["fid"].astype("<i4")
            zf.writestr(f"{name}/fid", raw.tobytes())


def _acqus_text(nucleus: str, bf1: float, td: int = 32768) -> str:
    return (
        f"##$TD= {td}\n##$SFO1= {bf1}\n##$BF1= {bf1}\n"
        f"##$O1= {5.0 * bf1}\n##$SW_h= {10.0 * bf1}\n##$SW= 10.0\n"
        f"##$NUC1= <{nucleus}>\n##$BYTORDA= 0\n##$DTYPA= 0\n"
        "##$AQ_mod= 1\n##$DECIM= 1\n##$DSPFVS= 0\n##$GRPDLY= 0.0\n##END=\n"
    )


def test_materialize_bruker_asset_extracts_zip(tmp_path: Path) -> None:
    # Simulate the run_root layout: <run_root>/<proj>/uploads/<id>/original/f.zip
    run_root = tmp_path
    proj = "myproj"
    upload_id = "up_abc"
    asset_dir = run_root / proj / "uploads" / upload_id / "original"
    asset_dir.mkdir(parents=True)
    zip_path = asset_dir / "nmr.zip"

    fid = np.zeros(32768, dtype=np.int32)
    _write_bruker_zip(
        zip_path,
        {"Proton": {"acqus": _acqus_text("1H", 500.13), "fid": fid}},
    )

    # work_dir = run_root / "jobs" / "job1"  →  parent.parent = run_root
    work_dir = run_root / "jobs" / "job1"
    inputs_dir = work_dir / "inputs"

    extracted = JobRunner._materialize_bruker_asset(
        experiment={
            "mode": "bruker",
            "spectrum_asset_id": upload_id,
            "filename": "nmr.zip",
            "project_id": proj,
        },
        inputs_dir=inputs_dir,
        work_dir=work_dir,
    )
    assert extracted is not None
    assert (extracted / "Proton" / "fid").is_file()
    assert (extracted / "Proton" / "acqus").is_file()


def test_materialize_bruker_asset_rejects_traversal(tmp_path: Path) -> None:
    run_root = tmp_path
    asset_dir = run_root / "p" / "uploads" / "id" / "original"
    asset_dir.mkdir(parents=True)
    evil = asset_dir / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escape.txt", "boom")
    work_dir = run_root / "jobs" / "j1"
    import pytest

    with pytest.raises(ValueError, match="Unsafe path"):
        JobRunner._materialize_bruker_asset(
            experiment={"mode": "bruker", "spectrum_asset_id": "id",
                        "filename": "evil.zip", "project_id": "p"},
            inputs_dir=work_dir / "inputs",
            work_dir=work_dir,
        )


def test_materialize_bruker_asset_missing_returns_none(tmp_path: Path) -> None:
    result = JobRunner._materialize_bruker_asset(
        experiment=None,
        inputs_dir=tmp_path / "inputs",
        work_dir=tmp_path,
    )
    assert result is None

    result = JobRunner._materialize_bruker_asset(
        experiment={"mode": "bruker"},
        inputs_dir=tmp_path / "inputs",
        work_dir=tmp_path,
    )
    assert result is None


def test_build_nmr_cmd_bruer_mode(tmp_path: Path) -> None:
    """_build_nmr_cmd emits --bruker when experiment.mode == 'bruker'."""
    runner = _make_runner()
    work_dir = tmp_path / "work"

    # Set up the asset
    run_root = work_dir.parent.parent if work_dir.parent.parent.exists() else tmp_path
    # work_dir.parent.parent may not resolve correctly; build explicitly
    run_root = tmp_path
    jobs_dir = run_root / "jobs"
    job_dir = jobs_dir / "job1"
    asset_dir = run_root / "p" / "uploads" / "id" / "original"
    asset_dir.mkdir(parents=True)
    zip_path = asset_dir / "nmr.zip"
    fid = np.zeros(32768, dtype=np.int32)
    _write_bruker_zip(
        zip_path,
        {"Proton": {"acqus": _acqus_text("1H", 500.13), "fid": fid}},
    )

    spec = JobSpec(
        workflow="nmr",
        name="nmr_bruker_test",
        input={
            "source_type": "candidates",
            "candidates": [{"source_type": "smiles", "source": "CCO"}],
            "experiment": {
                "mode": "bruker",
                "spectrum_asset_id": "id",
                "filename": "nmr.zip",
                "project_id": "p",
            },
        },
        method={},
        resources={},
    )

    cmd = runner._build_nmr_cmd(spec, job_dir)
    assert "--bruker" in cmd
    bruker_idx = cmd.index("--bruker")
    assert "bruker" in cmd[bruker_idx + 1]
    # --spectrum must NOT be present in bruker mode
    assert "--spectrum" not in cmd
