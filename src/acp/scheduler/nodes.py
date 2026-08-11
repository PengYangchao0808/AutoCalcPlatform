"""
Scheduler Execution Nodes
=========================

Unified execution-target model for the ACP scheduler (DevDoc:
``docs/ACP_Unified_Execution_Target_DevDoc.txt``, Phase 1).

``local`` is a first-class execution target; remote servers are configured
nodes.  This module unifies **node description and selection** only — the
execution mechanisms (local :class:`JobRunner` vs
:class:`RemoteJobRunner`) stay in their respective modules.

Key rules encoded here:

* ``ExecutionTargetError`` — permanent selection/config error, fail fast.
* ``ExecutionCapacityUnavailable`` — temporary; the target is valid but
  cannot accept new work right now (caller keeps the job STARTING and
  retries).
* Node configuration (:class:`NodeSpec`) is static; node state
  (:class:`NodeState`) is dynamic.  The two are never mixed.

Author: QCcalc Team
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from acp.scheduler.remote.config import RemoteNode

__all__ = [
    "ExecutionMode",
    "ExecutionTargetError",
    "ExecutionCapacityUnavailable",
    "NodeSpec",
    "NodeState",
    "NodeRegistry",
    "validate_execution_request",
]

ExecutionMode = Literal["local", "remote"]
"""Typed execution-mode preference — never a bare ``str``."""

LOCAL_NODE_NAME = "local"


class ExecutionTargetError(RuntimeError):
    """Permanent target selection/config error — retrying will not help."""


class ExecutionCapacityUnavailable(RuntimeError):  # noqa: N818 — name fixed by DevDoc §6
    """Temporary: target is valid but cannot accept new jobs right now."""


@dataclass(frozen=True)
class NodeSpec:
    """Static description of one execution target.

    Attributes:
        name: ``"local"`` or a configured remote node name.
        kind: ``"local"`` or ``"remote"``.
        enabled: Whether the target is eligible for dispatch.
        host: Hostname/IP (remote only).
        max_jobs: Concurrent-job ceiling.  Always ``> 0`` — there is no
            ``None = unlimited`` special case.
    """

    name: str
    kind: Literal["local", "remote"]
    enabled: bool = True
    host: str | None = None
    max_jobs: int = 1


@dataclass
class NodeState:
    """Dynamic, observed state of an execution target."""

    status: Literal["ready", "busy", "offline", "draining"]
    running_jobs: int = 0
    last_checked: datetime | None = None
    message: str | None = None


def _to_node_spec(node: RemoteNode) -> NodeSpec:
    """Map a configured :class:`RemoteNode` onto a :class:`NodeSpec`."""
    return NodeSpec(
        name=node.name,
        kind="remote",
        enabled=node.enabled,
        host=node.host,
        max_jobs=max(1, int(node.max_concurrent_jobs)),
    )


def validate_execution_request(spec: Any) -> None:
    """Reject contradictory ``execution_mode`` / ``target_node`` pairs.

    Fails fast (HTTP 400 at the API layer) rather than silently letting one
    field win — silent precedence would mask frontend/client bugs.

    Raises:
        ExecutionTargetError: On a conflicting combination.
    """
    mode = getattr(spec, "execution_mode", None)
    node = getattr(spec, "target_node", None)
    if mode == "remote" and node == LOCAL_NODE_NAME:
        raise ExecutionTargetError("execution_mode=remote conflicts with target_node=local")
    if mode == "local" and node not in (None, LOCAL_NODE_NAME):
        raise ExecutionTargetError(f"execution_mode=local conflicts with target_node={node!r}")


class NodeRegistry:
    """Static node catalogue + single point of target selection.

    The local node is constructed automatically — users never configure it.
    Remote nodes are mapped from the existing ``cluster.nodes`` YAML config;
    no new configuration schema is introduced.

    ``status_provider`` (optional callable ``name -> NodeStatus``) is wired
    by :class:`~acp.scheduler.manager.JobManager` to
    ``NodeManager.get_node_status`` so remote load counts come from the
    existing cached probe (30 s TTL), not a new monitoring thread.
    """

    def __init__(
        self,
        local_max_jobs: int,
        remote_nodes: list[RemoteNode] | None = None,
    ) -> None:
        self._local = NodeSpec(
            name=LOCAL_NODE_NAME,
            kind="local",
            max_jobs=max(1, int(local_max_jobs)),
        )
        self._remotes = [_to_node_spec(n) for n in (remote_nodes or [])]
        self.status_provider: Callable[[str], Any] | None = None

    @property
    def local(self) -> NodeSpec:
        return self._local

    @property
    def nodes(self) -> list[NodeSpec]:
        """``[local, *remotes]`` — list order is the deterministic tie-break."""
        return [self._local, *self._remotes]

    def get(self, name: str) -> NodeSpec | None:
        for spec in self.nodes:
            if spec.name == name:
                return spec
        return None

    def require(self, name: str) -> NodeSpec:
        """Explicit target lookup — unknown/disabled targets fail fast.

        Raises:
            ExecutionTargetError: If the node does not exist or is disabled.
        """
        if name == LOCAL_NODE_NAME:
            return self._local
        for spec in self._remotes:
            if spec.name == name:
                if not spec.enabled:
                    raise ExecutionTargetError(f"target_node {name!r} is disabled")
                return spec
        raise ExecutionTargetError(f"target_node {name!r} not found")

    def derive_local_state(self, running_jobs: int) -> NodeState:
        """Local node state derived from the manager's own job table."""
        status = "ready" if running_jobs < self._local.max_jobs else "busy"
        return NodeState(status=status, running_jobs=running_jobs)

    def select_remote(self, required: frozenset[str] | None = None) -> NodeSpec:
        """Pick an enabled remote node: least loaded (running/max), YAML order ties.

        ``required`` is accepted for Phase 2 capability filtering; Phase 1
        ignores it (capability matching is not implemented yet).

        Raises:
            ExecutionTargetError: No enabled remote nodes configured.
            ExecutionCapacityUnavailable: All enabled nodes are full or
                unreachable (temporary — the caller retries).
        """
        enabled = [s for s in self._remotes if s.enabled]
        if not enabled:
            raise ExecutionTargetError("No enabled remote nodes configured")

        best: NodeSpec | None = None
        best_ratio: float | None = None
        for spec in enabled:
            running = self._remote_running_jobs(spec)
            if running is None:  # unreachable / offline — stop dispatching here
                continue
            if running >= spec.max_jobs:
                continue
            ratio = running / spec.max_jobs
            if best is None or ratio < (best_ratio if best_ratio is not None else 1.0):
                best = spec
                best_ratio = ratio

        if best is None:
            raise ExecutionCapacityUnavailable("All remote nodes are at capacity or unreachable")
        return best

    def remote_running_jobs(self, name: str) -> int | None:
        """Running-job count for a remote node (``None`` when unreachable)."""
        spec = self.get(name)
        if spec is None or spec.kind != "remote":
            return None
        return self._remote_running_jobs(spec)

    def _remote_running_jobs(self, spec: NodeSpec) -> int | None:
        provider = self.status_provider
        if provider is None:
            return 0  # no probe wired — assume empty
        try:
            status = provider(spec.name)
        except Exception:
            return None
        if getattr(status, "status", None) == "offline":
            return None
        return int(getattr(status, "running_jobs", 0))
