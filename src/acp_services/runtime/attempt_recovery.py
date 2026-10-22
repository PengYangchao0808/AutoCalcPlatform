"""Kernel-backed reconciliation for explicitly delegated local cgroup v2 workers.

This is process containment for accidental worker death, not a hostile-code
sandbox. Without a delegated root and a recorded boundary, recovery refuses.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, replace
from pathlib import Path

from acp_services.contracts.envelope import ExecutionEnvelope, expected_submission_id
from acp_services.runtime.attempt_freeze import (
    AttemptLockClaim,
    AttemptRecoveryProof,
    _holder_alive,
)
from acp_services.runtime.cgroup import CGROUP_BASE, EnvelopeProcessGroup, plan_cgroup_v2

__all__ = ["CgroupAttemptRecovery"]


def _boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()


def _require_cgroup_v2(path: Path) -> None:
    """Reject ordinary files, including directories imitating kernel controls."""
    device = path.stat().st_dev
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if "-" not in fields or len(fields) < 7:
            continue
        major, minor = fields[2].split(":")
        if fields[fields.index("-") + 1] != "cgroup2":
            continue
        if device != os.makedev(int(major), int(minor)):
            continue
        # mountinfo escapes spaces/backslashes in mount paths.
        mount = fields[4]
        for escaped, literal in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
            mount = mount.replace(escaped, literal)
        if path.is_relative_to(Path(mount)):
            return
    raise ValueError("execution boundary is not on a kernel cgroup v2 filesystem")


def _populated(path: Path) -> bool:
    events = dict(line.split() for line in (path / "cgroup.events").read_text().splitlines())
    if events.get("populated") not in {"0", "1"}:
        raise ValueError("cgroup.events has no authoritative populated value")
    return events["populated"] == "1"


@dataclass(frozen=True)
class CgroupAttemptRecovery:
    """Bind before execution; prove a dead holder's entire subtree is empty.

    The controller must delegate a private root to the worker account. Children
    inherit membership, including detached sessions. The kernel's populated
    flag includes nested groups. No PID/group is killed by this adapter.
    """

    root: Path | None = None

    def _path(self, job_id: str, attempt: int) -> Path:
        if self.root is None:
            raise ValueError("no delegated execution cgroup root configured")
        root = self.root.resolve(strict=True)
        _require_cgroup_v2(root)
        path = root / CGROUP_BASE.lstrip("/") / expected_submission_id(job_id, attempt)
        if path.resolve().parent != root / CGROUP_BASE.lstrip("/"):
            raise ValueError("execution cgroup escaped its delegated root")
        return path

    def bind(self, claim: AttemptLockClaim, envelope: ExecutionEnvelope) -> AttemptLockClaim:
        path = self._path(envelope.job_id, envelope.attempt)
        if not claim.held_by_current_process(envelope):
            raise ValueError("only the live claim holder may bind execution")
        plan = plan_cgroup_v2(
            envelope,
            EnvelopeProcessGroup(
                envelope.submission_id, envelope.job_id, envelope.attempt, os.getpid()
            ),
            cgroup_root=str(self.root.resolve()),
        )
        if Path(plan.cgroup_path) != path:
            raise ValueError("cgroup resource plan does not match the attempt")
        path.mkdir(parents=True, exist_ok=True)
        _require_cgroup_v2(path)
        stat = path.stat()
        if claim.execution_cgroup_path and (
            claim.execution_cgroup_path != str(path)
            or claim.execution_cgroup_inode != stat.st_ino
            or claim.execution_cgroup_device != stat.st_dev
            or claim.execution_boot_id != _boot_id()
        ):
            raise ValueError("an existing execution boundary cannot be rebound")
        if _populated(path):
            current = (path / "cgroup.procs").read_text().split()
            if str(os.getpid()) not in current or not claim.execution_cgroup_path:
                raise ValueError("execution cgroup already contains another worker")
        # Set limits before moving this worker; every later QC child inherits.
        writes = plan.placement_writes(os.getpid())
        for filename, value in (*writes[1:], writes[0]):
            Path(filename).write_text(value + "\n", encoding="ascii")
        if str(os.getpid()) not in (path / "cgroup.procs").read_text().split():
            raise ValueError("kernel did not confirm worker placement")
        stat = path.stat()
        return replace(
            claim,
            execution_cgroup_path=str(path),
            execution_cgroup_inode=stat.st_ino,
            execution_cgroup_device=stat.st_dev,
            execution_boot_id=_boot_id(),
        )

    def confirm_stopped(self, claim: AttemptLockClaim) -> AttemptRecoveryProof | None:
        if claim.host != socket.gethostname() or _holder_alive(claim) is not False:
            return None
        try:
            path = self._path(claim.job_id, claim.attempt)
            if str(path) != claim.execution_cgroup_path or claim.execution_boot_id != _boot_id():
                return None
            before = path.stat()
            if (before.st_ino, before.st_dev) != (
                claim.execution_cgroup_inode,
                claim.execution_cgroup_device,
            ):
                return None
            _require_cgroup_v2(path)
            if _populated(path):
                return None
            after = path.stat()
            if (after.st_ino, after.st_dev) != (before.st_ino, before.st_dev):
                return None
        except (OSError, ValueError):
            return None
        return AttemptRecoveryProof(
            claim.content_digest(), True, True, f"kernel cgroup v2 empty subtree: {path}"
        )
