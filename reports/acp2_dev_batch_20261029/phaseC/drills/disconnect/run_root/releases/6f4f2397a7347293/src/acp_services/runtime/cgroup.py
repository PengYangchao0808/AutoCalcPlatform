"""Dependency-free cgroup resource plans shared by isolation and worker recovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from acp_services.contracts.envelope import ExecutionEnvelope

__all__ = ["CGROUP_BASE", "CgroupV2Plan", "EnvelopeProcessGroup", "plan_cgroup_v2"]

CGROUP_BASE = "/acp"


@dataclass(frozen=True)
class EnvelopeProcessGroup:
    """The process group created for exactly one envelope attempt.

    Resource limits bind here (via ``cgroup.procs``), not to a bare env var.
    ``envelope_key`` is the envelope ``submission_id`` — one attempt, one group.
    """

    envelope_key: str
    job_id: str
    attempt: int
    pgid: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "envelope_key": self.envelope_key,
            "job_id": self.job_id,
            "attempt": self.attempt,
            "pgid": self.pgid,
        }


@dataclass(frozen=True)
class CgroupV2Plan:
    """A cgroup v2 resource-control plan bound to an envelope process group."""

    cgroup_path: str
    envelope_key: str
    cpu_max: str | None = None
    memory_max_bytes: int | None = None
    pids_max: int | None = None

    @property
    def procs_file(self) -> str:
        return f"{self.cgroup_path}/cgroup.procs"

    def placement_writes(self, pgid: int) -> tuple[tuple[str, str], ...]:
        """Return ``(path, value)`` writes that bind the group to the cgroup."""
        writes: list[tuple[str, str]] = [(f"{self.cgroup_path}/cgroup.procs", str(pgid))]
        if self.cpu_max is not None:
            writes.append((f"{self.cgroup_path}/cpu.max", self.cpu_max))
        if self.memory_max_bytes is not None:
            writes.append((f"{self.cgroup_path}/memory.max", str(self.memory_max_bytes)))
        if self.pids_max is not None:
            writes.append((f"{self.cgroup_path}/pids.max", str(self.pids_max)))
        return tuple(writes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cgroup_path": self.cgroup_path,
            "envelope_key": self.envelope_key,
            "cpu_max": self.cpu_max,
            "memory_max_bytes": self.memory_max_bytes,
            "pids_max": self.pids_max,
        }


def plan_cgroup_v2(
    envelope: ExecutionEnvelope,
    process_group: EnvelopeProcessGroup,
    *,
    cgroup_root: str,
    cgroup_base: str = CGROUP_BASE,
    cpu_max: str | None = None,
    memory_max_bytes: int | None = None,
    pids_max: int | None = None,
) -> CgroupV2Plan:
    """Plan resource ceilings and placement without probing or database access."""
    budget = envelope.resource_budget
    if cpu_max is None and budget.nproc is not None:
        cpu_max = f"{int(budget.nproc) * 100000} 100000"
    if memory_max_bytes is None and budget.mem_mb is not None:
        memory_max_bytes = int(budget.mem_mb) * 1024 * 1024
    if pids_max is None:
        # Never unlimited: a bounded process count is part of resource control.
        pids_max = max(16, int(budget.nproc or 1) * 8)
    return CgroupV2Plan(
        cgroup_path=f"{cgroup_root.rstrip('/')}{cgroup_base}/{envelope.submission_id}",
        envelope_key=process_group.envelope_key,
        cpu_max=cpu_max,
        memory_max_bytes=memory_max_bytes,
        pids_max=pids_max,
    )
