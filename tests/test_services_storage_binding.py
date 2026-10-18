"""B02-2 (todo 12) — StorageBinding single allocation authority.

Exercises the Services allocator (``acp_services.data.storage_binding``) through
the legacy-side adapter (``acp.services_data_bootstrap.build_storage_allocation_port``),
which wraps the **real** ``acp/scheduler/remote/paths.py`` entry points and the
exclusive claim:

1. four reuse scenarios — same project same name (``__02`` dedupe) / cross
   project same name both stay distinct; rerun / continue reuse the binding and
   isolate receipts under ``WORK/00_RUNTIME/attempts/<N>/``.
2. frozen binding round-trip (``to_dict`` / ``from_dict``).
3. legacy flat-dir read-only adoption (tagged legacy; never allocated).
4. negatives — a new allocation that would land flat is refused; a same-name
   directory missing scheduler markers raises ``RemoteDirConflictError`` with the
   directory untouched and never deleted.
"""

from __future__ import annotations

import json
import posixpath
from pathlib import Path
from unittest.mock import patch

import pytest

from acp.scheduler.remote import ssh as ssh_mod
from acp.scheduler.remote.sftp import FileStager, RemoteDirConflictError
from acp.scheduler.remote.ssh import SSHConnectionPool
from acp.services_data_bootstrap import build_storage_allocation_port
from acp_services.contracts.envelope import StorageBinding
from acp_services.data import storage_binding as sb
from tests.test_remote_phase2 import FakeSFTP, FakeSSHClient, make_node

# ====================================================================== #
# Helpers
# ====================================================================== #


def _node_lookup(node):
    return lambda endpoint: node


def _allocator(*, node=None, stager=None, node_lookup=None):
    if node_lookup is None:
        node_lookup = _node_lookup(node or make_node())
    port = build_storage_allocation_port(node_lookup=node_lookup, stager=stager)
    return sb.StorageBindingAllocator(port)


def _request(
    job_id,
    *,
    project,
    task_leaf,
    run_root,
    endpoint="compute-01",
    attempt=1,
    tenant="tenant-1",
):
    work_dir = str(Path(run_root) / project / task_leaf)
    return sb.StorageAllocationRequest(
        job_id=job_id,
        attempt=attempt,
        work_dir=work_dir,
        run_root=str(run_root),
        project_id=project,
        task_leaf=task_leaf,
        endpoint=endpoint,
        tenant_id=tenant,
    )


def _make_record(job_id, *, work_dir, attempt=1, workflow="optimize"):
    from acp.scheduler.jobs import JobRecord, JobSpec

    spec = JobSpec(workflow=workflow, input={"source": "CCO", "source_type": "smiles"})
    return JobRecord(id=job_id, spec=spec, work_dir=str(work_dir), result={}, attempt=attempt)


def _seed_owner(sftp: FakeSFTP, remote_dir: str, job_id: str, attempt: int = 1) -> None:
    sftp.dirs.add(remote_dir)
    blob = json.dumps({"id": job_id, "attempt": attempt}).encode()
    sftp.files[posixpath.join(remote_dir, "job.json")] = blob


# ====================================================================== #
# 1. four reuse scenarios
# ====================================================================== #


def test_same_project_same_name_stays_distinct(tmp_path):
    root = tmp_path / "runs"
    allocator = _allocator()
    first = allocator.allocate(
        _request("job1", project="projA", task_leaf="mol_opt_x", run_root=root)
    )
    second = allocator.allocate(
        _request("job2", project="projA", task_leaf="mol_opt_x__02", run_root=root)
    )
    assert first.relative_path == "projA/mol_opt_x"
    assert second.relative_path == "projA/mol_opt_x__02"
    assert first.binding_id != second.binding_id
    assert allocator.remote_dir(first) != allocator.remote_dir(second)


def test_cross_project_same_name_stays_distinct(tmp_path):
    root = tmp_path / "runs"
    allocator = _allocator()
    a = allocator.allocate(_request("job1", project="projA", task_leaf="mol_opt_x", run_root=root))
    b = allocator.allocate(_request("job2", project="projB", task_leaf="mol_opt_x", run_root=root))
    assert a.relative_path == "projA/mol_opt_x"
    assert b.relative_path == "projB/mol_opt_x"
    assert a.binding_id != b.binding_id
    assert allocator.remote_dir(a) != allocator.remote_dir(b)


def test_rerun_reuses_binding_and_isolates_attempt_receipts(tmp_path):
    root = tmp_path / "runs"
    work_dir = root / "projA" / "mol_opt_x"
    work_dir.mkdir(parents=True)
    allocator = _allocator()
    attempt1 = allocator.allocate(
        _request("job1", project="projA", task_leaf="mol_opt_x", run_root=root, attempt=1)
    )
    attempt2 = allocator.allocate(
        _request("job1", project="projA", task_leaf="mol_opt_x", run_root=root, attempt=2)
    )
    # attempt never enters the binding: rerun reuses the same identity + dir.
    assert attempt1.binding_id == attempt2.binding_id
    assert attempt1.to_dict() == attempt2.to_dict()

    from acp.scheduler.manager import JobManager

    (work_dir / ".exit_code").write_text("1\n", encoding="utf-8")
    (work_dir / "state.json").write_text('{"status": "failed", "attempt": 1}', encoding="utf-8")
    record = _make_record("job1", work_dir=work_dir, attempt=2)
    JobManager.__new__(JobManager)._archive_attempt_receipts(record, previous_attempt=1)

    archive = work_dir / "WORK" / "00_RUNTIME" / "attempts" / "1"
    assert (archive / ".exit_code").is_file()
    assert (archive / "state.json").is_file()
    assert not (work_dir / ".exit_code").exists()
    # The task directory itself is reused, never renamed.
    assert work_dir.is_dir()


def test_continue_reuses_binding_and_archives_resume_source(tmp_path):
    root = tmp_path / "runs"
    work_dir = root / "projA" / "mol_opt_x"
    work_dir.mkdir(parents=True)
    allocator = _allocator()
    binding = allocator.allocate(
        _request("job1", project="projA", task_leaf="mol_opt_x", run_root=root)
    )
    again = allocator.allocate(
        _request("job1", project="projA", task_leaf="mol_opt_x", run_root=root, attempt=2)
    )
    assert binding.binding_id == again.binding_id

    from acp.scheduler.manager import JobManager

    work_dir.joinpath("resume_source.json").write_text('{"attempt": 1}', encoding="utf-8")
    record = _make_record("job1", work_dir=work_dir, attempt=2)
    JobManager.__new__(JobManager)._archive_previous_resume_source(record, previous_attempt=1)
    archived = work_dir / "WORK" / "00_RUNTIME" / "attempts" / "1" / "resume_source.json"
    assert archived.is_file()
    assert not (work_dir / "resume_source.json").exists()


# ====================================================================== #
# 2. frozen binding round-trip
# ====================================================================== #


def test_frozen_binding_round_trip(tmp_path):
    root = tmp_path / "runs"
    binding = _allocator().allocate(
        _request("job1", project="projA", task_leaf="mol_opt_remark", run_root=root)
    )
    assert isinstance(binding, StorageBinding)
    assert binding.binding_id.startswith(sb.BINDING_ID_PREFIX)
    assert binding.tenant_id == "tenant-1"
    assert binding.project_id == "projA"
    assert binding.endpoint == "compute-01"
    assert binding.root == str(root)
    assert binding.relative_path == "projA/mol_opt_remark"
    assert binding.mapping_version == sb.MAPPING_VERSION_CURRENT
    assert dict(binding.artifact_digests) == {}
    # human-readable path: job id / timestamps never enter it (ANTI #15).
    assert "job1" not in binding.relative_path
    assert binding == StorageBinding.from_dict(binding.to_dict())


def test_adapter_satisfies_allocation_port(tmp_path):
    node = make_node()
    port = build_storage_allocation_port(node_lookup=_node_lookup(node))
    assert isinstance(port, sb.StorageAllocationPort)


# ====================================================================== #
# 3. legacy flat-dir read-only adoption
# ====================================================================== #


def test_legacy_flat_dir_read_only_adoption(tmp_path):
    root = tmp_path / "runs"
    allocator = _allocator()
    request = _request("job1", project="projA", task_leaf="mol_ens_remark", run_root=root)
    # Only the legacy flat candidate is owned; the new-style candidate is not.
    binding = allocator.adopt(
        request,
        probe=lambda remote: remote == "/scratch/test/acp_jobs/mol_ens_remark",
    )
    assert binding.relative_path == "mol_ens_remark"
    assert binding.mapping_version == sb.MAPPING_VERSION_LEGACY
    assert allocator.remote_dir(binding) == "/scratch/test/acp_jobs/mol_ens_remark"
    # Adoption is read-only: it never creates the local run tree.
    assert not root.exists()


def test_legacy_fallback_marked_legacy(tmp_path):
    root = tmp_path / "runs"
    allocator = _allocator()
    request = _request("job1", project="projA", task_leaf="mol_ens_remark", run_root=root)
    # No mapping and no ownership probe -> legacy flat fallback, tagged legacy.
    binding = allocator.adopt(request)
    assert binding.relative_path == "mol_ens_remark"
    assert binding.mapping_version == sb.MAPPING_VERSION_LEGACY
    assert not root.exists()


def test_persisted_new_style_mapping_keeps_current_version(tmp_path):
    root = tmp_path / "runs"
    allocator = _allocator()
    request = _request("job1", project="projA", task_leaf="mol_opt_x", run_root=root)
    binding = allocator.adopt(
        request,
        persisted_relative="projA/mol_opt_x",
    )
    assert binding.relative_path == "projA/mol_opt_x"
    assert binding.mapping_version == sb.MAPPING_VERSION_CURRENT


# ====================================================================== #
# 4. negatives
# ====================================================================== #


def test_negative_new_allocation_that_would_land_flat_is_refused(tmp_path):
    root = tmp_path / "runs"
    allocator = _allocator()
    # A task dir directly under run_root derives a single-component relative
    # path -> would compose to the legacy flat layout -> refused (adopt-only).
    flat_request = sb.StorageAllocationRequest(
        job_id="job1",
        attempt=1,
        work_dir=str(root / "orphan_task"),
        run_root=str(root),
        endpoint="compute-01",
    )
    with pytest.raises(sb.FlatAllocationRefusedError):
        allocator.allocate(flat_request)
    assert not root.exists()


def test_negative_same_name_dir_missing_markers_conflicts_without_delete(tmp_path):
    node = make_node()
    sftp = FakeSFTP()
    client = FakeSSHClient(sftp)
    stager = FileStager(SSHConnectionPool())
    allocator = _allocator(node=node, stager=stager)
    request = _request("job1", project="projA", task_leaf="mol_opt_x", run_root=tmp_path / "runs")
    binding = allocator.allocate(request)

    # A same-name directory exists but carries no scheduler markers (no owner).
    target = posixpath.join(node.remote_work_dir, "projA/mol_opt_x")
    sftp.dirs.add(target)
    sftp.files[posixpath.join(target, "someone_elses.txt")] = b"data"
    before = dict(sftp.files)

    with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: client):
        with pytest.raises(RemoteDirConflictError):
            allocator.claim(request, binding)

    assert dict(sftp.files) == before, "conflicting dir bytes must be unchanged"
    assert target in sftp.dirs, "conflicting dir must never be deleted"


def test_claim_creates_missing_dir(tmp_path):
    node = make_node()
    sftp = FakeSFTP()
    client = FakeSSHClient(sftp)
    stager = FileStager(SSHConnectionPool())
    allocator = _allocator(node=node, stager=stager)
    request = _request("job1", project="projA", task_leaf="mol_opt_x", run_root=tmp_path / "runs")
    binding = allocator.allocate(request)
    target = posixpath.join(node.remote_work_dir, "projA/mol_opt_x")
    with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: client):
        disposition = allocator.claim(request, binding)
    assert disposition == "created"
    assert target in sftp.dirs


def test_claim_reuses_owned_dir_across_attempts(tmp_path):
    node = make_node()
    sftp = FakeSFTP()
    client = FakeSSHClient(sftp)
    stager = FileStager(SSHConnectionPool())
    allocator = _allocator(node=node, stager=stager)
    request = _request(
        "job1", project="projA", task_leaf="mol_opt_x", run_root=tmp_path / "runs", attempt=2
    )
    binding = allocator.allocate(request)
    target = posixpath.join(node.remote_work_dir, "projA/mol_opt_x")
    _seed_owner(sftp, target, "job1", attempt=1)
    with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: client):
        disposition = allocator.claim(request, binding)
    assert disposition == "reused"


def test_freeze_binding_rejects_unsafe_relative_path():
    with pytest.raises(sb.StorageAllocationError):
        sb.freeze_binding(StorageBinding(binding_id="", relative_path="../etc/passwd"))
    with pytest.raises(sb.StorageAllocationError):
        sb.freeze_binding(StorageBinding(binding_id="", relative_path=""))
