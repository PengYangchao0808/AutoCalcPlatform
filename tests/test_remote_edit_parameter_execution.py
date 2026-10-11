"""Edited science inputs must be staged before submission to a remote node."""

from __future__ import annotations

import json
import posixpath
from pathlib import Path

import pytest

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec
from acp.scheduler.remote.config import RemoteExecutionConfig
from acp.scheduler.remote.runner import RemoteSubmissionRejected
from tests.test_remote_phase2 import FakeSFTP, make_node
from tests.test_remote_storage_identity import _make_runner, _seed_owner, _submit

XYZ = "3\nwater\nO 0 0 0\nH 0 0 1\nH 0 1 0\n"
SPIN = {
    "execution_mode": "single",
    "states": [
        {"state_id": "triplet", "target_multiplicity": 3, "spin_mode": "unrestricted"},
    ],
}


@pytest.mark.parametrize("resume", [False, True])
def test_remote_reset_or_continue_stages_current_receipts_before_bsub(
    tmp_path: Path, resume: bool
) -> None:
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    sftp = FakeSFTP()
    runner, client, _ = _make_runner(sftp, config)
    work = tmp_path / "runs" / "project" / "mol_opt"
    work.mkdir(parents=True)
    spec = JobSpec(
        workflow="optimize",
        input={"source_type": "xyz_text", "source": XYZ},
        method={"levels": {"opt": {"engine": "orca", "electronic_state": SPIN}}},
    )
    record = JobRecord(
        id="remote_edit",
        spec=spec,
        work_dir=str(work),
        attempt=2,
        result={"remote": {"schema": 1, "relative": "project/mol_opt", "resume": resume}},
    )
    remote = posixpath.join(node.remote_work_dir, "project/mol_opt")
    _seed_owner(sftp, remote, record.id, attempt=1)
    old_files = {
        "scan_config.json": b"old scan config",
        "electronic_state.json": b"old spin config",
        "resume_source.json": b'{"attempt":1}',
        ".exit_code": b"1",
        "WORK/01_OPT/orca.out": b"old science",
        "WORK/00_RUNTIME/checkpoint.json": b"old checkpoint",
    }
    for rel, data in old_files.items():
        sftp.files[posixpath.join(remote, rel)] = data
        sftp.dirs.add(posixpath.dirname(posixpath.join(remote, rel)))
    receipt = {
        "attempt": 2,
        "previous_attempt": 1,
        "compatible": False,
        "incompatible_reason": "legacy",
    }
    if resume:
        (work / "resume_source.json").write_text(json.dumps(receipt))
    archive = posixpath.join(remote, "WORK/00_RUNTIME/attempts/1")
    snapshots = []
    original_submit = runner._submit_lsf

    def submit_spy(*args, **kwargs):
        snapshots.append(dict(sftp.files))
        return original_submit(*args, **kwargs)

    runner._submit_lsf = submit_spy
    _submit(runner, record, JobEventLog(work / "events.jsonl"), client)
    assert len(snapshots) == 1
    files = snapshots[0]
    assert (
        json.loads(files[posixpath.join(remote, "electronic_state.json")])["electronic_state"]
        == SPIN
    )
    assert b"--spin-config electronic_state.json" in files[posixpath.join(remote, "submit.lsf")]
    assert files[posixpath.join(archive, "resume_source.json")] == old_files["resume_source.json"]
    assert posixpath.join(remote, ".exit_code") not in files
    if resume:
        assert json.loads(files[posixpath.join(remote, "resume_source.json")]) == receipt
        assert files[posixpath.join(remote, "WORK/01_OPT/orca.out")] == b"old science"
        assert files[posixpath.join(remote, "WORK/00_RUNTIME/checkpoint.json")] == b"old checkpoint"
    else:
        for rel in old_files:
            assert files[posixpath.join(archive, rel)] == old_files[rel]
        assert posixpath.join(remote, "scan_config.json") not in files
        assert posixpath.join(remote, "resume_source.json") not in files
        assert posixpath.join(remote, "WORK/01_OPT/orca.out") not in files


def test_remote_continue_missing_receipt_rejects_before_bsub(tmp_path: Path) -> None:
    node = make_node()
    sftp = FakeSFTP()
    runner, client, _ = _make_runner(
        sftp, RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    )
    spec = JobSpec(workflow="singlepoint", input={"source_type": "xyz_text", "source": XYZ})
    work = tmp_path / "runs" / "p" / "sp"
    record = JobRecord(
        id="missing_receipt",
        spec=spec,
        work_dir=str(work),
        attempt=2,
        result={"remote": {"resume": True}},
    )
    bsub = []
    runner._submit_lsf = lambda *args, **kwargs: bsub.append(True)
    with pytest.raises(RemoteSubmissionRejected, match="resume_source"):
        _submit(runner, record, JobEventLog(work / "events.jsonl"), client)
    assert not bsub


def test_remote_reset_unreadable_directory_rejects_before_bsub(tmp_path: Path) -> None:
    node = make_node()
    sftp = FakeSFTP()
    runner, client, _ = _make_runner(
        sftp, RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    )
    work = tmp_path / "runs" / "p" / "opt"
    spec = JobSpec(workflow="optimize", input={"source_type": "xyz_text", "source": XYZ})
    remote = posixpath.join(node.remote_work_dir, "p/opt")
    record = JobRecord(
        id="unreadable_reset",
        spec=spec,
        work_dir=str(work),
        attempt=2,
        result={"remote": {"relative": "p/opt"}},
    )
    _seed_owner(sftp, remote, record.id, attempt=1)
    bsub = []
    runner._submit_lsf = lambda *args, **kwargs: bsub.append(True)

    def unreadable(*args):
        raise PermissionError("directory unreadable")

    runner._stager.list_remote_dir = unreadable
    with pytest.raises(RemoteSubmissionRejected, match="unreadable"):
        _submit(runner, record, JobEventLog(work / "events.jsonl"), client)
    assert not bsub


def test_remote_nmr_stages_every_candidate_and_the_experiment(tmp_path: Path) -> None:
    node = make_node()
    sftp = FakeSFTP()
    runner, client, _ = _make_runner(
        sftp, RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    )
    work = tmp_path / "runs" / "p" / "nmr"
    spec = JobSpec(
        workflow="nmr",
        input={
            "candidates": [
                {
                    "source_type": "xyz_text",
                    "source": XYZ,
                    "name": "first",
                    "charge": -1,
                    "multiplicity": 2,
                },
                {
                    "source_type": "xyz_text",
                    "source": XYZ.replace("H 0 1 0", "H 0 1.2 0"),
                    "name": "second",
                },
            ],
            "experiment": {"mode": "assigned", "content": "13C\nC1 20.1"},
        },
    )
    record = JobRecord(id="nmr_remote", spec=spec, work_dir=str(work))
    snapshots = []
    original_submit = runner._submit_lsf

    def submit_spy(*args, **kwargs):
        snapshots.append(dict(sftp.files))
        return original_submit(*args, **kwargs)

    runner._submit_lsf = submit_spy
    _submit(runner, record, JobEventLog(work / "events.jsonl"), client)
    files = snapshots[0]
    remote = record.result["remote_dir"]
    assert (
        files[posixpath.join(remote, "inputs/input_0.xyz")]
        != files[posixpath.join(remote, "inputs/input_1.xyz")]
    )
    assert files[posixpath.join(remote, "inputs/experiment.txt")] == b"13C\nC1 20.1"
    argv = runner._job_states[record.id]["cli_cmd"]
    inputs = [argv[i + 1] for i, flag in enumerate(argv) if flag == "--input"]
    assert inputs == ["inputs/input_0.xyz", "inputs/input_1.xyz"]
    assert json.loads(argv[argv.index("--candidate-metadata-json") + 1]) == [
        {"name": "first", "charge": -1, "multiplicity": 2},
        {"name": "second"},
    ]
    assert (
        record.spec is spec
    )  # Staging paths are ephemeral, never persisted into the input identity.
