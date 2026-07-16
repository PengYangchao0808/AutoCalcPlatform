"""
Remote Node Manager
===================

Status/caching manager for configured remote compute nodes.  Provides a
small API surface used by the web dashboard (``/api/v1/nodes``).
"""

from __future__ import annotations

import logging
import posixpath
import shlex
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.monitor import RemoteJobMonitor
from acp.scheduler.remote.ssh import SSHConnectionPool, SSHExecutionError

logger = logging.getLogger(__name__)

__all__ = ["BootstrapResult", "NodeManager", "NodeStatus"]


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


@dataclass
class NodeStatus:
    """Live-ish status of a single remote node."""

    name: str
    host: str
    status: str  # "online" | "offline" | "degraded"
    running_jobs: int = 0
    max_jobs: int = 0
    disk_usage_pct: int = 0
    last_check: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BootstrapResult:
    """Outcome of :meth:`NodeManager.bootstrap_node`.

    Attributes:
        node: Name of the bootstrapped node.
        reachable: Whether the SSH/pip command could be executed at all.
        exit_code: pip process exit code (``None`` when the command could not
            be launched, e.g. SSH failure).
        python_executable: Interpreter used to drive ``pip``.
        requirements_path: Remote path of the requirements file installed.
        stdout: Full pip stdout (may be long; callers typically tail it).
        stderr: Full pip stderr.
        sync_uploaded: Number of files uploaded by the pre-bootstrap code sync
            (the requirements file is synced alongside the code).
        sync_errors: Per-file sync errors, if any.
        error: Human-readable failure message when ``reachable`` is False.
    """

    node: str
    reachable: bool
    exit_code: int | None = None
    python_executable: str = "python"
    requirements_path: str = ""
    stdout: str = ""
    stderr: str = ""
    sync_uploaded: int = 0
    sync_errors: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True when pip ran and exited cleanly (exit 0)."""
        return self.reachable and self.exit_code == 0


class NodeManager:
    """Cache-aware remote node status manager.

    The dashboard polls node state every few seconds; probing SSH on each
    poll would be expensive.  This manager keeps a short-lived in-memory cache
    (default 30 s) and only refreshes a node when the cache entry has expired
    or the caller explicitly requests a fresh probe.
    """

    def __init__(
        self,
        remote_config: RemoteExecutionConfig,
        ssh_pool: SSHConnectionPool,
        monitor: RemoteJobMonitor | None = None,
        cache_ttl: int = 30,
    ) -> None:
        self.config = remote_config
        self._pool = ssh_pool
        self._monitor = monitor or RemoteJobMonitor(ssh_pool, _stager_from_pool(ssh_pool))
        self._cache_ttl = max(1, cache_ttl)
        self._cache: dict[str, tuple[float, NodeStatus]] = {}
        self._lock = threading.Lock()

    def list_nodes(self) -> list[NodeStatus]:
        """Return all configured nodes, with cached status."""
        if not self.config.is_remote:
            return []
        return [self.get_node_status(node.name) for node in self.config.nodes]

    def get_node_status(self, node_name: str) -> NodeStatus:
        """Return the status of a node by name, using cache if fresh."""
        node = self.config.get_node(node_name)
        if node is None:
            raise ValueError(f"Node not found: {node_name}")
        with self._lock:
            cached = self._cache.get(node_name)
            if cached is not None and (time.monotonic() - cached[0]) < self._cache_ttl:
                return cached[1]
        # SSH probe outside the lock so concurrent requests for *different*
        # nodes don't serialise.  A duplicate probe for the *same* node is
        # harmless (last write wins).
        status = self._refresh_status(node)
        with self._lock:
            self._cache[node_name] = (time.monotonic(), status)
        return status

    def ping_node(self, node_name: str) -> bool:
        """Test SSH connectivity to *node_name* and refresh its status."""
        node = self.config.get_node(node_name)
        if node is None or not node.enabled:
            return False
        try:
            self._pool.execute(node, "echo ok", timeout=15)
        except SSHExecutionError as exc:
            logger.debug("Ping failed for %s: %s", node_name, exc)
            status = NodeStatus(
                name=node.name,
                host=node.host,
                status="offline",
                running_jobs=0,
                max_jobs=node.max_concurrent_jobs,
                disk_usage_pct=0,
                last_check=_utc_now(),
                error=str(exc),
            )
            with self._lock:
                self._cache[node_name] = (time.monotonic(), status)
            return False
        # Refresh full metrics after a successful ping.
        with self._lock:
            self._cache.pop(node_name, None)
        self.get_node_status(node_name)
        return True

    def bootstrap_node(
        self,
        node_name: str,
        timeout: int = 600,
        sync: bool = True,
    ) -> BootstrapResult:
        """Provision *node_name* with the ACP runtime dependencies.

        Syncs the code (so ``requirements-node.txt`` is fresh on the node)
        then runs ``<python_executable> -m pip install --user -r
        <remote_code_dir>/requirements-node.txt`` over SSH.  This is the
        reproducible, per-node equivalent of ``pip install -e .`` on the
        server — the dependency set is version-controlled with the code and
        travels to every node, so adding or rebuilding a node is a one-call
        operation.

        Args:
            node_name: Node to bootstrap (must be configured and enabled).
            timeout: SSH command timeout in seconds (pip may take a while on
                a fresh node).  Defaults to 600.
            sync: When True (default), run a code sync first so the
                requirements file is up to date.

        Returns:
            :class:`BootstrapResult` with pip stdout/stderr and exit code.
        """
        node = self.config.get_node(node_name)
        if node is None:
            raise ValueError(f"Node not found: {node_name}")
        if not node.enabled:
            return BootstrapResult(
                node=node.name,
                reachable=False,
                error="node is disabled",
            )

        requirements_remote = posixpath.join(node.remote_code_dir, "requirements-node.txt")

        sync_uploaded = 0
        sync_errors: list[str] = []
        if sync:
            try:
                from acp.scheduler.remote.sync import CodeSyncer

                sync_result = CodeSyncer(self._pool).sync_code(node, force=False)
                sync_uploaded = sync_result.uploaded
                sync_errors = list(sync_result.errors)
            except Exception as exc:  # noqa: BLE001 — report, don't abort
                logger.warning("Bootstrap pre-sync for %s failed: %s", node.name, exc)
                sync_errors.append(f"sync failed: {exc}")

        py = node.python_executable or "python"
        # Quote paths/interpreter for the remote shell and chain pip so a
        # missing pip module surfaces a clear exit code rather than a hang.
        cmd = " && ".join(
            [
                f"test -f {shlex.quote(requirements_remote)}",
                f"{shlex.quote(py)} -m pip install --user --disable-pip-version-check "
                f"-r {shlex.quote(requirements_remote)}",
            ]
        )
        try:
            exit_code, out, err = self._pool.execute(node, cmd, timeout=timeout)
        except SSHExecutionError as exc:
            logger.error("Bootstrap SSH failure on %s: %s", node.name, exc)
            return BootstrapResult(
                node=node.name,
                reachable=False,
                python_executable=py,
                requirements_path=requirements_remote,
                sync_uploaded=sync_uploaded,
                sync_errors=sync_errors,
                error=str(exc),
            )

        ok = exit_code == 0
        logger.info(
            "Bootstrap %s on %s: pip exit=%s (%s)",
            "succeeded" if ok else "failed",
            node.name,
            exit_code,
            f"{sync_uploaded} files synced" if sync else "sync skipped",
        )
        return BootstrapResult(
            node=node.name,
            reachable=True,
            exit_code=exit_code,
            python_executable=py,
            requirements_path=requirements_remote,
            stdout=out,
            stderr=err,
            sync_uploaded=sync_uploaded,
            sync_errors=sync_errors,
        )

    def _refresh_status(self, node: RemoteNode) -> NodeStatus:
        """SSH probe: running job count + disk usage."""
        if not node.enabled:
            return NodeStatus(
                name=node.name,
                host=node.host,
                status="offline",
                running_jobs=0,
                max_jobs=node.max_concurrent_jobs,
                disk_usage_pct=0,
                last_check=_utc_now(),
                error="disabled",
            )
        try:
            running = self._monitor.get_running_job_count(node)
            disk = self._monitor.check_disk_usage(node, node.remote_work_dir)
        except SSHExecutionError as exc:
            return NodeStatus(
                name=node.name,
                host=node.host,
                status="offline",
                running_jobs=0,
                max_jobs=node.max_concurrent_jobs,
                disk_usage_pct=0,
                last_check=_utc_now(),
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Unexpected status refresh error for %s: %s", node.name, exc)
            return NodeStatus(
                name=node.name,
                host=node.host,
                status="offline",
                running_jobs=0,
                max_jobs=node.max_concurrent_jobs,
                disk_usage_pct=0,
                last_check=_utc_now(),
                error=str(exc),
            )

        status = "online"
        if disk >= 90 or running >= node.max_concurrent_jobs:
            status = "degraded"
        return NodeStatus(
            name=node.name,
            host=node.host,
            status=status,
            running_jobs=running,
            max_jobs=node.max_concurrent_jobs,
            disk_usage_pct=disk,
            last_check=_utc_now(),
            error=None,
        )


def _stager_from_pool(pool: SSHConnectionPool) -> Any:
    """Build a :class:`FileStager` bound to *pool* without a top-level import."""
    from acp.scheduler.remote.sftp import FileStager

    return FileStager(pool)
