"""Checked recovery must account for orphaned scientific child processes."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from acp_services.runtime.attempt_freeze import (
    AttemptLockClaim,
    AttemptLockExecutionRight,
    AttemptRecoveryProof,
    attempt_lock_path,
    read_attempt_lock,
)
from tests.test_services_chain_switch import _envelope

_ORPHAN_HOLDER = r"""
import json, os, subprocess, sys, time
from pathlib import Path
from acp_services.contracts.envelope import ExecutionEnvelope
from acp_services.runtime.attempt_freeze import AttemptLockExecutionRight, attempt_lock_path

root = Path(sys.argv[1])
envelope = ExecutionEnvelope.from_dict(json.loads((root / "envelope.json").read_text()))
assert AttemptLockExecutionRight(attempt_lock_path(root)).acquire(envelope).held
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(120)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
(root / "child.json").write_text(json.dumps({"pid": child.pid}))
while True:
    time.sleep(1)
"""


def _running(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    # A reaped or zombie process cannot keep calculating.  Do not require the
    # container's PID 1 to reap an adopted child before this test can finish.
    return stat.rpartition(")")[2].split()[0] not in {"Z", "X"}


def _await_stopped(pid: int) -> None:
    deadline = time.monotonic() + 10
    while _running(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _running(pid)


def test_dead_worker_orphan_requires_child_stop_and_bound_recovery_proof(tmp_path: Path) -> None:
    envelope = _envelope()
    (tmp_path / "envelope.json").write_text(json.dumps(envelope.to_dict()), encoding="utf-8")
    env = dict(os.environ)
    src_root = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (src_root, env.get("PYTHONPATH"))))
    worker = subprocess.Popen([sys.executable, "-c", _ORPHAN_HOLDER, str(tmp_path)], env=env)
    child_pid: int | None = None
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "child.json").exists() and time.monotonic() < deadline:
            assert worker.poll() is None
            time.sleep(0.01)
        child_pid = int(json.loads((tmp_path / "child.json").read_text())["pid"])
        worker.kill()  # Only this test-created worker; leave its QC child alive.
        worker.wait(timeout=10)
        assert _running(child_pid)

        lock_path = attempt_lock_path(tmp_path)
        original = lock_path.read_bytes()
        claim = read_attempt_lock(lock_path)
        assert claim is not None
        assert claim.pid == worker.pid

        verdict = AttemptLockExecutionRight(lock_path).acquire(envelope)
        assert not verdict.held
        assert "children are not confirmed stopped" in verdict.detail
        assert lock_path.read_bytes() == original
        assert _running(child_pid)
        assert not (tmp_path / "execution_receipt.json").exists()

        class TestProcessReconciliation:
            def confirm_stopped(self, observed: AttemptLockClaim) -> AttemptRecoveryProof | None:
                # This fixture launched exactly one scientific child and
                # retained its pid.  No production process/group is signaled.
                if worker.poll() is None or _running(child_pid):
                    return None
                return AttemptRecoveryProof(
                    observed.content_digest(), True, True, "test: worker reaped and child stopped"
                )

        right = AttemptLockExecutionRight(lock_path, recovery_check=TestProcessReconciliation())
        assert not right.acquire(envelope).held
        assert lock_path.read_bytes() == original
        os.kill(child_pid, signal.SIGTERM)
        _await_stopped(child_pid)
        assert right.acquire(envelope).held
        assert right.verify(envelope).held
        assert right.release(envelope)
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=10)
        if child_pid is not None and _running(child_pid):
            os.kill(child_pid, signal.SIGKILL)
            _await_stopped(child_pid)


def _dead_local_claim(tmp_path: Path) -> tuple[Path, AttemptLockClaim]:
    envelope = _envelope()
    claim = replace(
        AttemptLockClaim.for_envelope(envelope, lease_seconds=0),
        pid=999_999_999,
        pid_starttime="",
    )
    path = attempt_lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(claim.to_dict()), encoding="utf-8")
    return path, claim


@pytest.mark.parametrize(
    "invalid", ["boolean", "stale", "holder_running", "children_running", "no_source"]
)
def test_checked_recovery_rejects_incomplete_or_wrong_claim_proof(
    tmp_path: Path, invalid: str
) -> None:
    path, claim = _dead_local_claim(tmp_path)
    original = path.read_bytes()
    proof = AttemptRecoveryProof(claim.content_digest(), True, True, "test authority")
    candidate: object = {
        "boolean": True,
        "stale": replace(proof, claim_digest="unrelated-claim"),
        "holder_running": replace(proof, holder_stopped=False),
        "children_running": replace(proof, children_stopped=False),
        "no_source": replace(proof, source=""),
    }[invalid]

    class InvalidProof:
        def confirm_stopped(self, observed: AttemptLockClaim) -> object:
            return candidate

    verdict = AttemptLockExecutionRight(path, recovery_check=InvalidProof()).acquire(_envelope())
    assert not verdict.held
    assert path.read_bytes() == original


@pytest.mark.parametrize("host", ["", "some-other-compute-node"])
def test_unknown_or_foreign_host_never_uses_local_recovery_proof(tmp_path: Path, host: str) -> None:
    path, claim = _dead_local_claim(tmp_path)
    claim = replace(claim, host=host)
    path.write_text(json.dumps(claim.to_dict()), encoding="utf-8")
    original = path.read_bytes()

    class MustNotReconcileLocally:
        def confirm_stopped(self, observed: AttemptLockClaim) -> AttemptRecoveryProof:
            pytest.fail("local PID liveness cannot verify an unknown/foreign host")

    assert (
        not AttemptLockExecutionRight(path, recovery_check=MustNotReconcileLocally())
        .acquire(_envelope())
        .held
    )
    assert path.read_bytes() == original


def test_recovery_proof_cannot_replace_claim_changed_during_reconciliation(tmp_path: Path) -> None:
    path, claim = _dead_local_claim(tmp_path)
    changed = replace(claim, owner="new-owner", fencing_token="new-fencing")

    class ChangedClaim:
        def confirm_stopped(self, observed: AttemptLockClaim) -> AttemptRecoveryProof:
            path.write_text(json.dumps(changed.to_dict()), encoding="utf-8")
            return AttemptRecoveryProof(observed.content_digest(), True, True, "test authority")

    verdict = AttemptLockExecutionRight(path, recovery_check=ChangedClaim()).acquire(_envelope())
    assert not verdict.held
    assert "changed during checked recovery" in verdict.detail
    assert read_attempt_lock(path) == changed


def test_failed_process_reconciliation_preserves_the_existing_claim(tmp_path: Path) -> None:
    path, _claim = _dead_local_claim(tmp_path)
    original = path.read_bytes()

    class UnavailableProcessReconciliation:
        def confirm_stopped(self, observed: AttemptLockClaim) -> AttemptRecoveryProof | None:
            raise OSError("process observer unavailable")

    verdict = AttemptLockExecutionRight(
        path, recovery_check=UnavailableProcessReconciliation()
    ).acquire(_envelope())
    assert not verdict.held
    assert path.read_bytes() == original


def test_claim_records_local_host_for_recovery_boundaries() -> None:
    claim = AttemptLockClaim.for_envelope(_envelope(), lease_seconds=1)
    assert claim.host == socket.gethostname()
    assert AttemptLockClaim.from_dict(claim.to_dict()) == claim


def test_legacy_unknown_host_cannot_match_current_pid_as_its_holder(tmp_path: Path) -> None:
    envelope = _envelope()
    claim = replace(AttemptLockClaim.for_envelope(envelope, lease_seconds=1), host="")
    path = attempt_lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(claim.to_dict()), encoding="utf-8")
    original = path.read_bytes()

    assert not AttemptLockExecutionRight(path).acquire(envelope).held
    assert path.read_bytes() == original
