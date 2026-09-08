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

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from acp.scheduler.capabilities import (
    NoCapableNodeError,
    is_degraded,
    matches_capabilities,
)

if TYPE_CHECKING:
    from acp.scheduler.remote.config import NodeCapabilities, RemoteNode

__all__ = [
    "ExecutionMode",
    "ExecutionTargetError",
    "ExecutionCapacityUnavailable",
    "NodeSpec",
    "NodeState",
    "NodeRegistry",
    "validate_execution_request",
    "validate_submission_target",
]

ExecutionMode = Literal["local", "remote"]
"""Typed execution-mode preference — never a bare ``str``."""

LOCAL_NODE_NAME = "local"


class ExecutionTargetError(RuntimeError):
    """Permanent target selection/config error — retrying will not help.

    Attributes:
        code: Optional stable machine-readable error code (e.g.
            ``"unknown_target_node"``, ``"target_node_incapable"``);
            ``None`` keeps the legacy codeless behaviour.
        missing_software: Software the rejected target lacked (sorted
            tuple; empty unless capability validation populated it).
        missing_tags: Tags the rejected target lacked (sorted tuple).
    """

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        missing_software: tuple[str, ...] | list[str] | None = None,
        missing_tags: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.missing_software = tuple(sorted(missing_software or ()))
        self.missing_tags = tuple(sorted(missing_tags or ()))


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
        capabilities: Static capability declaration from the remote
            config (``None`` = generic node, design D8).  The live
            probe-inferred state is resolved at match time through the
            wired ``status_provider`` — this field only carries the
            declaration half.
        capability_state: Static tri-state summary — ``"declared"`` when
            ``capabilities`` is set, ``"unknown"`` otherwise (an
            undeclared node may resolve to ``"probe-inferred"`` at match
            time via its software probe).
    """

    name: str
    kind: Literal["local", "remote"]
    enabled: bool = True
    host: str | None = None
    max_jobs: int = 1
    capabilities: NodeCapabilities | None = None
    capability_state: str = "unknown"


@dataclass
class NodeState:
    """Dynamic, observed state of an execution target."""

    status: Literal["ready", "busy", "offline", "draining"]
    running_jobs: int = 0
    last_checked: datetime | None = None
    message: str | None = None


def _to_node_spec(node: RemoteNode) -> NodeSpec:
    """Map a configured :class:`RemoteNode` onto a :class:`NodeSpec`."""
    # ``getattr`` keeps duck-typed RemoteNode stand-ins (tests) working.
    capabilities = getattr(node, "capabilities", None)
    return NodeSpec(
        name=node.name,
        kind="remote",
        enabled=node.enabled,
        host=node.host,
        max_jobs=max(1, int(node.max_concurrent_jobs)),
        capabilities=capabilities,
        capability_state="declared" if capabilities is not None else "unknown",
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


def _probed_software_of(registry: Any, name: str) -> frozenset[str] | None:
    """Resolve a node's probe-inferred software set, or ``None`` (unknown).

    Read-only snapshot helper for creation-time validation: mirrors the
    dispatch-time probe resolution without mutating anything.  A missing
    status provider yields ``None`` — the unknown capability state, under
    which software requirements fall back to generic (D8).
    """
    getter = getattr(registry, "_probed_software", None)
    if not callable(getter):
        return None
    return getter(name)


def validate_submission_target(
    spec: Any,
    *,
    registry: NodeRegistry,
    derive_fn: Callable[[Any], frozenset[str]] | None = None,
    local_satisfies_fn: Callable[[Iterable[str]], bool] | None = None,
) -> None:
    """Validate an explicit or auto execution target at submission time.

    Creation-time fail-fast mirror of the dispatch decisions in
    ``JobManager._resolve_execution_target`` (design ``node-selection-design.md``
    §3.1/D9/D14).  It is a pure decision over the injected ``registry`` node
    snapshot plus the job's own fields — it never mutates reservations and
    never selects a node.

    Two permanent conditions are rejected here so the caller can return an
    immediate HTTP 400 instead of letting a doomed job spin ``STARTING``:

    * an explicit remote ``target_node`` that is unknown
      (``code="unknown_target_node"``), disabled
      (``code="target_node_disabled"``), or cannot satisfy the derived
      requirements (``code="target_node_incapable"`` plus ``missing_*``);
    * an auto job (no ``target_node``, no explicit ``execution_mode``) whose
      derived software the local machine cannot run and no enabled remote
      node can satisfy (``code="no_capable_node"`` plus ``missing_*``).

    Local paths (``execution_mode="local"`` or ``target_node="local"``) are
    never capability-checked (design §3.1).  Explicit ``execution_mode``
    keeps its legacy dispatch behaviour (design §3.1 ③) — only the
    mode-free auto case is gated here.  Capacity conditions (nodes present
    but busy/offline/degraded) are deliberately **not** rejected: the
    dispatch backstop keeps those ``STARTING`` and retries for capacity.

    ``derive_fn`` and ``local_satisfies_fn`` default to the capabilities
    module's functions and are injectable so tests stay deterministic
    regardless of which QC binaries the CI/dev machine happens to have.

    Args:
        spec: The job specification (fields ``workflow``/``method``/
            ``node_tags``/``target_node``/``execution_mode`` are read).
        registry: Node snapshot source.  Must expose ``get(name)`` returning
            a ``NodeSpec``-like object and ``nodes``; probe-inferred state is
            resolved through the registry's optional ``_probed_software``.

    Raises:
        ExecutionTargetError: Unknown/disabled/incapable explicit target.
        NoCapableNodeError: Auto job with no satisfying remote node.
    """
    from acp.scheduler import capabilities as _capabilities

    derive = derive_fn if derive_fn is not None else _capabilities.derive_required_software
    local_ok = (
        local_satisfies_fn if local_satisfies_fn is not None else _capabilities.local_satisfies
    )
    mode = getattr(spec, "execution_mode", None)
    target = getattr(spec, "target_node", None)
    tags = frozenset(getattr(spec, "node_tags", None) or ())

    # Local paths never capability-checked (design §3.1).
    if mode == "local" or target == LOCAL_NODE_NAME:
        return

    derived = derive(spec)

    # Explicit remote target — existence/enabled/capability hard check.
    if target:
        node = registry.get(target)
        if node is None:
            raise ExecutionTargetError(
                f"target_node {target!r} is not a configured node",
                code="unknown_target_node",
            )
        if not node.enabled:
            raise ExecutionTargetError(
                f"target_node {target!r} is disabled",
                code="target_node_disabled",
            )
        if node.kind == "local":
            return
        if derived or tags:
            match = matches_capabilities(
                derived,
                tags,
                declared=node.capabilities,
                probed_software=_probed_software_of(registry, node.name),
            )
            if not match.satisfies:
                raise ExecutionTargetError(
                    f"target_node {target!r} cannot run the job: {'; '.join(match.reasons)}",
                    code="target_node_incapable",
                    missing_software=match.missing_software,
                    missing_tags=match.missing_tags,
                )
        return

    # Auto (no explicit target, no explicit mode): D14 escalation guard.
    if mode is not None or not derived:
        return
    if local_ok(derived):
        return  # dispatch runs this on the local machine (tags ignored, E3).

    # Escalation would go remote: reject only when enabled remote nodes
    # exist but none can satisfy (a permanent, not capacity, condition).
    enabled_remotes = [n for n in registry.nodes if n.kind == "remote" and n.enabled]
    if not enabled_remotes:
        return  # no remote capability configured — dispatch backstop governs.
    for remote in enabled_remotes:
        match = matches_capabilities(
            derived,
            tags,
            declared=remote.capabilities,
            probed_software=_probed_software_of(registry, remote.name),
        )
        if match.satisfies:
            return
    missing_software = frozenset(derived)
    missing_tags = frozenset(tags)
    for remote in enabled_remotes:
        match = matches_capabilities(
            derived,
            tags,
            declared=remote.capabilities,
            probed_software=_probed_software_of(registry, remote.name),
        )
        missing_software &= frozenset(match.missing_software)
        missing_tags &= frozenset(match.missing_tags)
    raise NoCapableNodeError(
        "no enabled remote node can satisfy the job requirements "
        f"(missing everywhere: software={sorted(missing_software)}, "
        f"tags={sorted(missing_tags)})",
        missing_software=missing_software,
        missing_tags=missing_tags,
    )


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
        #: In-flight reservations per remote node (node name → job ids).
        #: Soft-cap accounting for the select→submit window (design §3.3):
        #: NOT synchronised here — every mutation happens under the owning
        #: ``JobManager._lock``.
        self.reservations: dict[str, set[str]] = {}

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

    def require(
        self,
        name: str,
        required: frozenset[str] | None = None,
        required_tags: frozenset[str] | None = None,
    ) -> NodeSpec:
        """Explicit target lookup — unknown/disabled/incapable fail fast.

        Capability hard check (design §3.1 ①): when ``required`` /
        ``required_tags`` are given and the node cannot satisfy them, the
        error carries ``code="target_node_incapable"`` plus the missing
        software/tag fields.  The local target is never capability-checked
        (design §3.1 ③ — local execution stays unchecked).

        Raises:
            ExecutionTargetError: If the node does not exist, is disabled,
                or lacks the required capabilities.
        """
        if name == LOCAL_NODE_NAME:
            return self._local
        for spec in self._remotes:
            if spec.name == name:
                if not spec.enabled:
                    raise ExecutionTargetError(f"target_node {name!r} is disabled")
                if required or required_tags:
                    match = matches_capabilities(
                        frozenset(required or ()),
                        frozenset(required_tags or ()),
                        declared=spec.capabilities,
                        probed_software=self._probed_software(spec.name),
                    )
                    if not match.satisfies:
                        raise ExecutionTargetError(
                            f"target_node {name!r} cannot satisfy the job "
                            f"requirements: {'; '.join(match.reasons)}",
                            code="target_node_incapable",
                            missing_software=match.missing_software,
                            missing_tags=match.missing_tags,
                        )
                return spec
        raise ExecutionTargetError(f"target_node {name!r} not found")

    def reserve(self, node_name: str, job_id: str) -> None:
        """Record one job's in-flight reservation on a node (soft cap).

        Caller must hold the owning ``JobManager._lock`` (design §3.3).
        """
        self.reservations.setdefault(node_name, set()).add(job_id)

    def release(self, node_name: str, job_id: str) -> None:
        """Idempotently drop one reservation (empty sets are pruned)."""
        jobs = self.reservations.get(node_name)
        if jobs is None:
            return
        jobs.discard(job_id)
        if not jobs:
            self.reservations.pop(node_name, None)

    def release_job(self, job_id: str) -> None:
        """Idempotently drop a job's reservation on whichever node holds it."""
        for node_name in list(self.reservations):
            self.release(node_name, job_id)

    def derive_local_state(self, running_jobs: int) -> NodeState:
        """Local node state derived from the manager's own job table."""
        status = "ready" if running_jobs < self._local.max_jobs else "busy"
        return NodeState(status=status, running_jobs=running_jobs)

    def select_remote(
        self,
        required: frozenset[str] | None = None,
        required_tags: frozenset[str] | None = None,
        affinity_node: str | None = None,
    ) -> NodeSpec:
        """Pick an enabled, capability-matching remote node (design §3.2).

        Set terminology (design §2.1): **match set** = enabled ∧ capability
        match (:func:`matches_capabilities`, D8/D13); **candidate set** =
        match set ∧ not offline ∧ not degraded ∧ below capacity counting
        in-flight reservations.  Selection: affinity node when it is a
        candidate → least-loaded ratio ``(running + reservations) /
        max_jobs`` → YAML order tie-break.

        Args:
            required: Required software names (empty = no constraint).
            required_tags: Required node tags (empty = no constraint; only
                declared nodes can ever satisfy tags, D13).
            affinity_node: Preferred node name — returned when it is part
                of the candidate set.

        Raises:
            ExecutionTargetError: No enabled remote nodes configured.
            NoCapableNodeError: The match set is empty — no enabled node
                satisfies the requirements (permanent).
            ExecutionCapacityUnavailable: Match set non-empty but every
                matching node is offline, degraded, or at capacity
                (temporary — the caller retries).
        """
        enabled = [s for s in self._remotes if s.enabled]
        if not enabled:
            raise ExecutionTargetError("No enabled remote nodes configured")

        required_sw = frozenset(required or ())
        required_tag_set = frozenset(required_tags or ())

        match_set: list[NodeSpec] = []
        missing_software = frozenset(required_sw)
        missing_tags = frozenset(required_tag_set)
        for spec in enabled:
            match = matches_capabilities(
                required_sw,
                required_tag_set,
                declared=spec.capabilities,
                probed_software=self._probed_software(spec.name),
            )
            if match.satisfies:
                match_set.append(spec)
            else:
                # Aggregate what NO node provides: the intersection of the
                # per-node missing sets.
                missing_software &= frozenset(match.missing_software)
                missing_tags &= frozenset(match.missing_tags)

        if not match_set:
            raise NoCapableNodeError(
                "No enabled remote node satisfies the job requirements "
                f"(missing everywhere: software={sorted(missing_software)}, "
                f"tags={sorted(missing_tags)})",
                missing_software=missing_software,
                missing_tags=missing_tags,
            )

        candidates: list[tuple[NodeSpec, int]] = []
        for spec in match_set:
            status = self._live_status(spec)
            if status is None:  # offline / unreachable
                continue
            if is_degraded(status):
                continue
            load = int(getattr(status, "running_jobs", 0) or 0) + len(
                self.reservations.get(spec.name, ())
            )
            if load >= spec.max_jobs:
                continue
            candidates.append((spec, load))

        if not candidates:
            raise ExecutionCapacityUnavailable(
                "All capability-matching remote nodes are offline, degraded, or at capacity"
            )

        if affinity_node is not None:
            for spec, _load in candidates:
                if spec.name == affinity_node:
                    return spec

        best_spec, best_load = candidates[0]
        best_ratio = best_load / best_spec.max_jobs
        for spec, load in candidates[1:]:
            ratio = load / spec.max_jobs
            if ratio < best_ratio:  # strict < keeps the YAML-order tie-break
                best_spec, best_ratio = spec, ratio
        return best_spec

    def remote_running_jobs(self, name: str) -> int | None:
        """Running-job count for a remote node (``None`` when unreachable)."""
        spec = self.get(name)
        if spec is None or spec.kind != "remote":
            return None
        return self._remote_running_jobs(spec)

    def _remote_running_jobs(self, spec: NodeSpec) -> int | None:
        status = self._live_status(spec)
        if status is None:
            return None
        return int(getattr(status, "running_jobs", 0))

    def _live_status(self, spec: NodeSpec) -> Any | None:
        """Cached live status for a remote node; ``None`` when offline.

        A missing ``status_provider`` yields a synthetic idle status — the
        Phase-1 contract (no probe wired — assume empty and reachable).
        """
        provider = self.status_provider
        if provider is None:
            return NodeState(status="ready", running_jobs=0)
        try:
            status = provider(spec.name)
        except Exception:
            return None
        if getattr(status, "status", None) == "offline":
            return None
        return status

    def _probed_software(self, name: str) -> frozenset[str] | None:
        """Probe-resolved software names for a node; ``None`` = no probe.

        Reads the wired ``status_provider``'s cached software report (the
        same NodeManager probe the panel uses).  Unreachable/offline nodes
        have no usable probe — ``None`` maps to the *unknown* capability
        state in :func:`matches_capabilities` (software falls back to
        generic, so a temporarily-offline node never fails a match
        permanently).
        """
        spec = self.get(name)
        if spec is None:
            return None
        status = self._live_status(spec)
        if status is None:
            return None
        software = getattr(status, "software", None)
        if not isinstance(software, dict):
            return None
        return frozenset(
            sw for sw, info in software.items() if isinstance(info, dict) and info.get("resolved")
        )
