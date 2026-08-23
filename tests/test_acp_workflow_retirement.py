"""Old-entry retirement tests (plan §16, M7).

Covers the acceptance criterion "旧任务可以查看，新任务不能使用旧入口":
catalog status flips, scheduler submission rejection, CLI rejection, and
runner/script_gen command mapping for the new stage workflows.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp.catalog import WORKFLOW_CATALOG
from acp.scheduler.jobs import SUPPORTED_WORKFLOWS, JobSpec, confsearch_method_flags

RETIRED_ENTRIES = (
    "ensemble",
    "energy",
    "xtbmd_censo_energy",
    "mechanism",
    "mech-conf",
    "mech-step",
    "mech-confirm",
    "mech-chain",
)
STAGE_ENTRIES = ("Confsearch", "PESsearch", "Lowconfirm", "Highconfirm")


def test_all_legacy_entries_retired_and_kept_for_history() -> None:
    by_id = {entry["id"]: entry for entry in WORKFLOW_CATALOG}
    for legacy in RETIRED_ENTRIES:
        assert by_id[legacy]["status"] == "retired", legacy
        assert by_id[legacy]["visible"] is False, legacy
        assert legacy not in SUPPORTED_WORKFLOWS


def test_stage_entries_active() -> None:
    for stage in STAGE_ENTRIES:
        assert stage in SUPPORTED_WORKFLOWS


def test_manager_rejects_retired_workflow(tmp_path: Path) -> None:
    from acp.scheduler.manager import JobManager

    mgr = JobManager(run_root=tmp_path)
    try:
        for legacy in ("ensemble", "energy", "mechanism"):
            with pytest.raises(ValueError, match="Unsupported workflow"):
                mgr.submit(JobSpec(workflow=legacy, name="legacy"))
    finally:
        mgr.shutdown()


def test_cli_rejects_retired_workflows_with_mapping_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from acp.cli import main

    for legacy in ("ensemble", "energy", "xtbmd_censo_energy", "mechanism", "mech-conf"):
        rc = main(["run", legacy, "--input", "CCO", "--output", str(tmp_path)])
        assert rc == 2, legacy
    stderr = capsys.readouterr().err
    assert "The workflow has been retired." in stderr
    assert "Use Confsearch, PESsearch, Lowconfirm or Highconfirm." in stderr


def test_confsearch_method_flag_emission() -> None:
    flags = confsearch_method_flags(
        {
            "profile_id": "xtbmd-censo",
            "profile": "light",
            "refinement_policy": "rank1",
            "md_time_ps": 50,
            "levels": {"refinement_threshold": 0.99},
        }
    )
    assert flags[:2] == ["--protocol", "xtbmd-censo"]
    assert ["--profile", "light"] in [flags[i : i + 2] for i in range(0, len(flags), 2)] or [
        "--profile",
        "light",
    ] == flags[2:4]
    joined = " ".join(flags)
    assert "--refinement-policy rank1" in joined
    assert "--md-time 50" in joined
    assert "--levels" in joined


def _write_confsearch_source_job(root: Path) -> Path:
    job_dir = root / "mol_Confsearch_test"
    manifest_dir = job_dir / "RESULT" / "confsearch" / "conformers"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "conf_0001.xyz").write_text(
        "3\nwater\nO 0.0 0.0 0.0\nH 0.9 0.0 0.0\nH -0.3 0.9 0.0\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "confsearch_v1",
        "workflow": "Confsearch",
        "conformers": [
            {"conf_id": "conf_0001", "geometry": "conformers/conf_0001.xyz", "rank": 1}
        ],
    }
    (job_dir / "RESULT" / "confsearch" / "confsearch_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return job_dir


def test_runner_builds_confsearch_cmd(tmp_path: Path) -> None:
    from acp.scheduler.runner import JobRunner

    runner = JobRunner()
    spec = JobSpec(
        workflow="Confsearch",
        name="demo",
        input={"source": "CCO", "source_type": "smiles"},
        method={
            "protocol": "xtb-crest",
            "refinement_policy": "screen",
            "ewin": 8.0,
        },
    )
    cmd = runner._build_cmd(spec, tmp_path / "job")
    joined = " ".join(cmd)
    assert "run Confsearch" in joined
    assert "--protocol xtb-crest" in joined
    assert "--refinement-policy screen" in joined
    assert "--ewin 8.0" in joined


def test_runner_builds_stage_cmd_with_handoff_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from acp.scheduler.runner import JobRunner

    source_job = _write_confsearch_source_job(tmp_path)
    runner = JobRunner()
    work_dir = tmp_path / "stage_job"
    spec = JobSpec(
        workflow="PESsearch",
        name="pes",
        input={
            "from": str(source_job / "RESULT" / "confsearch" / "confsearch_manifest.json"),
            "coordinate_plan": {
                "coordinates": [
                    {"id": "rc1", "kind": "distance", "atoms": [0, 1], "start": 2.0, "end": 1.0}
                ],
                "points": 21,
            },
        },
        method={"strategy": "guided-scan"},
    )
    cmd = runner._build_cmd(spec, work_dir)
    joined = " ".join(cmd)
    assert "run PESsearch" in joined
    assert "--strategy guided-scan" in joined
    assert "--plan" in joined
    handoff = work_dir / "WORK" / "01_PREPARE" / "handoff"
    assert (handoff / "confsearch_manifest.json").is_file()
    assert (handoff / "conformers" / "conf_0001.xyz").is_file()
    assert f"--from {handoff / 'confsearch_manifest.json'}" in joined


def test_remote_script_gen_stage_and_confsearch(tmp_path: Path) -> None:
    from acp.scheduler.remote.script_gen import build_remote_cli_command

    confsearch_cmd = build_remote_cli_command(
        JobSpec(
            workflow="Confsearch",
            input={"source": "CCO"},
            method={"protocol": "censo-crest", "refinement_policy": "rank1"},
        ),
        input_path="inputs/input.xyz",
    )
    joined = " ".join(confsearch_cmd)
    assert "run Confsearch" in joined
    assert "--protocol censo-crest" in joined
    assert "--refinement-policy rank1" in joined

    stage_cmd = build_remote_cli_command(
        JobSpec(
            workflow="Lowconfirm",
            input={"from": "/abs/s2_path_manifest.json"},
            method={"select": ["ts_guess_001"]},
        ),
    )
    stage_joined = " ".join(stage_cmd)
    assert "run Lowconfirm" in stage_joined
    assert "--from /abs/s2_path_manifest.json" in stage_joined
    assert "--select ts_guess_001" in stage_joined
