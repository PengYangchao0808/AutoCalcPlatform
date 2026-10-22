"""Adversarial containment checks; simulated kernel reads are not deployment proof."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import replace
from pathlib import Path

import pytest

from acp_services.runtime import attempt_recovery as recovery_module
from acp_services.runtime.attempt_freeze import (
    AttemptLockClaim,
    AttemptLockExecutionRight,
    attempt_lock_path,
)
from acp_services.runtime.attempt_recovery import CgroupAttemptRecovery
from tests.test_services_chain_switch import _envelope


def test_regular_directory_cannot_impersonate_kernel_cgroup(tmp_path: Path) -> None:
    (tmp_path / "cgroup.controllers").write_text("cpu memory pids")
    right = AttemptLockExecutionRight(
        attempt_lock_path(tmp_path), execution_scope=CgroupAttemptRecovery(tmp_path)
    )
    verdict = right.acquire(_envelope())
    assert not verdict.held
    assert "not on a kernel cgroup v2 filesystem" in verdict.detail


@pytest.fixture
def simulated_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Only the kernel filesystem assertion is replaced. This fixture exercises
    # identity/population validation; it is explicitly not real cgroup evidence.
    monkeypatch.setattr(recovery_module, "_require_cgroup_v2", lambda path: None)
    port = CgroupAttemptRecovery(tmp_path)
    envelope = _envelope()
    path = port._path(envelope.job_id, envelope.attempt)
    path.mkdir(parents=True)
    (path / "cgroup.events").write_text("populated 0\nfrozen 0\n")
    (path / "cgroup.procs").write_text("")
    stat = path.stat()
    claim = replace(
        AttemptLockClaim.for_envelope(envelope, lease_seconds=0),
        pid=999_999_999,
        pid_starttime="",
        execution_cgroup_path=str(path),
        execution_cgroup_inode=stat.st_ino,
        execution_cgroup_device=stat.st_dev,
        execution_boot_id=recovery_module._boot_id(),
    )
    return port, path, claim


def test_empty_subtree_recovery_is_bound_to_complete_claim(simulated_boundary) -> None:
    port, path, claim = simulated_boundary
    proof = port.confirm_stopped(claim)
    assert proof is not None and proof.confirms(claim)
    assert not proof.confirms(replace(claim, acquired_at="different owner generation"))
    lock = attempt_lock_path(path.parent.parent)
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps(claim.to_dict()))
    right = AttemptLockExecutionRight(lock, recovery_check=port)
    assert right.acquire(_envelope()).held
    assert right.release(_envelope())


@pytest.mark.parametrize(
    "changed",
    ["populated", "malformed", "inode", "device", "boot", "path", "host", "alive", "legacy"],
)
def test_no_recovery_without_complete_matching_empty_boundary(simulated_boundary, changed) -> None:
    port, path, claim = simulated_boundary
    if changed == "populated":
        # Parent cgroup.procs remains empty: a child in a nested cgroup still
        # makes the kernel's recursive populated flag true.
        (path / "cgroup.events").write_text("populated 1\n")
    elif changed == "malformed":
        (path / "cgroup.events").write_text("frozen 0\n")
    elif changed == "inode":
        claim = replace(claim, execution_cgroup_inode=claim.execution_cgroup_inode + 1)
    elif changed == "device":
        claim = replace(claim, execution_cgroup_device=claim.execution_cgroup_device + 1)
    elif changed == "boot":
        claim = replace(claim, execution_boot_id="previous kernel boot")
    elif changed == "path":
        claim = replace(claim, execution_cgroup_path=str(path.parent))
    elif changed == "host":
        claim = replace(claim, host=socket.gethostname() + "-other")
    elif changed == "alive":
        claim = replace(claim, pid=os.getpid())
    else:
        claim = replace(claim, execution_cgroup_path="", execution_boot_id="")
    assert port.confirm_stopped(claim) is None


def test_bind_precedes_held_claim_and_preserves_owner(simulated_boundary) -> None:
    port, path, _ = simulated_boundary
    right = AttemptLockExecutionRight(attempt_lock_path(path.parent.parent), execution_scope=port)
    assert right.acquire(_envelope()).held
    claim = AttemptLockClaim.from_dict(json.loads(right.lock_path.read_text()))
    assert claim is not None
    assert claim.held_by_current_process(_envelope())
    assert claim.execution_cgroup_path == str(path)
    assert (path / "cgroup.procs").read_text().strip() == str(os.getpid())
    assert right.acquire(_envelope()).held  # idempotent in the same live scope
    assert right.release(_envelope())


def test_other_worker_in_boundary_blocks_new_calculation(simulated_boundary) -> None:
    port, path, _ = simulated_boundary
    (path / "cgroup.events").write_text("populated 1\n")
    (path / "cgroup.procs").write_text("999999999\n")
    right = AttemptLockExecutionRight(attempt_lock_path(path.parent.parent), execution_scope=port)
    assert not right.acquire(_envelope()).held
    assert (path / "cgroup.procs").read_text() == "999999999\n"


def test_live_claim_cannot_move_to_another_boundary(simulated_boundary) -> None:
    port, path, _ = simulated_boundary
    claim = AttemptLockClaim.for_envelope(_envelope(), lease_seconds=60)
    bound = port.bind(claim, _envelope())
    changed = replace(bound, execution_cgroup_inode=bound.execution_cgroup_inode + 1)
    with pytest.raises(ValueError, match="cannot be rebound"):
        port.bind(changed, _envelope())


def test_worker_entry_refuses_invalid_configured_boundary(tmp_path: Path) -> None:
    from acp_services.worker_bootstrap import main
    from tests.test_services_worker_bootstrap import _main_args, _write_frozen

    frozen = _write_frozen(tmp_path)
    args = _main_args(frozen, probe="ok")
    args.extend(["--attempt-lock", "--execution-cgroup-root", str(tmp_path)])
    assert main(args) != 0
    receipt = Path(frozen["receipt"])
    assert not receipt.exists()
    assert receipt.with_name(receipt.name + ".refused.json").exists()
