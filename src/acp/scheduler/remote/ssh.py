"""
SSH Connection Pool
===================

Thread-safe SSH connection pool built on paramiko.  Each :class:`RemoteNode`
gets its own bounded ``queue.Queue`` of ``SSHClient`` objects whose size equals
``node.max_concurrent_jobs``.  Callers *borrow* a connection, use it, and
*return* it; exhausted pools block until a connection is returned.

paramiko's ``SSHClient`` is **not** thread-safe, so the pool guarantees that
no two threads ever hold the same client simultaneously.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import paramiko

from acp.scheduler.remote.config import RemoteNode

logger = logging.getLogger(__name__)

__all__ = ["SSHConnectionPool", "SSHExecutionError"]

_MAX_RETRIES = 2
_DEFAULT_TIMEOUT = 30


class SSHExecutionError(RuntimeError):
    """Raised when a remote command cannot be executed after retries."""


class _WarnThenAddPolicy(paramiko.MissingHostKeyPolicy):
    """Log a warning for an unknown host key, then accept it (AutoAdd-style)."""

    def missing_host_key(self, client, hostname, key):  # type: ignore[override]
        logger.warning(
            "Unknown SSH host key for %s — accepting (host_key_policy='warn'). Fingerprint: %s",
            hostname,
            key.get_fingerprint().hex() if key.get_fingerprint() else "<unknown>",
        )
        client.get_host_keys().add(hostname, key.get_name(), key)


def _host_key_policy(name: str) -> paramiko.MissingHostKeyPolicy:
    """Map a policy name string to a paramiko policy instance."""
    if name == "auto_add":
        return paramiko.AutoAddPolicy()
    if name == "warn":
        return _WarnThenAddPolicy()
    # Default: strictest
    return paramiko.RejectPolicy()


def _redact_node(node: RemoteNode) -> dict[str, Any]:
    """Return a log-safe summary of *node* (no password/key content)."""
    has_pw = bool(node.resolved_password())
    auth = "env-password" if has_pw else ("key" if node.key_file else "none")
    return {
        "name": node.name,
        "host": node.host,
        "port": node.port,
        "username": node.username,
        "auth": auth,
    }


def _create_client(node: RemoteNode, timeout: int = _DEFAULT_TIMEOUT) -> paramiko.SSHClient:
    """Create and connect a fresh ``SSHClient`` for *node*."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_host_key_policy(node.host_key_policy))

    password = node.resolved_password()
    connect_kwargs: dict[str, Any] = {
        "hostname": node.host,
        "port": node.port,
        "username": node.username,
        "timeout": timeout,
    }
    if password:
        connect_kwargs["password"] = password
    if node.key_file:
        connect_kwargs["key_filename"] = node.key_file

    logger.debug("Connecting SSH to node %s", _redact_node(node))
    client.connect(**connect_kwargs)
    return client


def _client_alive(client: paramiko.SSHClient) -> bool:
    """Cheap liveness check — verify the transport is still active."""
    try:
        transport = client.get_transport()
        if transport is None or not transport.is_active():
            return False
        return True
    except Exception:
        return False


class _NodePool:
    """Per-node bounded queue of ready SSHClient objects."""

    def __init__(self, node: RemoteNode):
        self.node = node
        self._capacity = max(1, node.max_concurrent_jobs)
        self._queue: queue.Queue[paramiko.SSHClient | None] = queue.Queue(maxsize=self._capacity)
        self._created = 0
        self._lock = threading.Lock()

    def borrow(self, timeout: float = 60.0) -> paramiko.SSHClient:
        """Get a connected client, creating one lazily if the pool isn't full.

        If every queued connection turns out to be dead, we retry up to
        ``_capacity + 2`` times before giving up — this bounds the work
        instead of recursing unboundedly (which could overflow the stack
        when a remote node reboots and all idle connections go stale).
        """
        max_retries = self._capacity + 2
        for _ in range(max_retries):
            # Fast path: reuse an existing idle client.
            try:
                client = self._queue.get_nowait()
                if client is not None and _client_alive(client):
                    return client
                if client is not None:
                    _safe_close(client)
                    with self._lock:
                        self._created -= 1
            except queue.Empty:
                pass

            # Slow path: maybe we can create a new one.
            with self._lock:
                if self._created < self._capacity:
                    self._created += 1
                    can_create = True
                else:
                    can_create = False

            if can_create:
                try:
                    return _create_client(self.node)
                except Exception:
                    with self._lock:
                        self._created -= 1
                    raise

            # Pool exhausted — block until someone returns one.
            client = self._queue.get(timeout=timeout)
            if client is not None and _client_alive(client):
                return client
            if client is not None:
                _safe_close(client)
                with self._lock:
                    self._created -= 1
            # Dead client returned — loop and retry.
        raise SSHExecutionError(
            f"No live SSH connection available for node {self.node.name!r} "
            f"after {max_retries} retries"
        )

    def release(self, client: paramiko.SSHClient, broken: bool = False) -> None:
        """Return a client to the pool (or discard it if *broken*)."""
        if broken:
            _safe_close(client)
            with self._lock:
                self._created -= 1
            return
        try:
            self._queue.put_nowait(client)
        except queue.Full:
            _safe_close(client)
            with self._lock:
                self._created -= 1

    def close_all(self) -> None:
        """Close every idle client currently in the queue."""
        while True:
            try:
                client = self._queue.get_nowait()
            except queue.Empty:
                break
            if client is not None:
                _safe_close(client)
            with self._lock:
                self._created -= 1


def _safe_close(client: paramiko.SSHClient) -> None:
    try:
        client.close()
    except Exception:
        logger.debug("Error closing SSH client", exc_info=True)


class SSHConnectionPool:
    """Thread-safe, multi-node SSH connection pool.

    A single ``SSHConnectionPool`` instance is shared across all worker
    threads in the :class:`~acp.scheduler.manager.JobManager`.  Each
    :class:`RemoteNode` is independently pooled.
    """

    def __init__(self) -> None:
        self._pools: dict[str, _NodePool] = {}
        self._lock = threading.Lock()

    def _pool_for(self, node: RemoteNode) -> _NodePool:
        key = node.name
        existing = self._pools.get(key)
        if existing is not None:
            return existing
        with self._lock:
            existing = self._pools.get(key)
            if existing is not None:
                return existing
            new_pool = _NodePool(node)
            self._pools[key] = new_pool
            return new_pool

    def borrow(self, node: RemoteNode, timeout: float = 60.0) -> paramiko.SSHClient:
        """Borrow a live SSH client for *node*."""
        return self._pool_for(node).borrow(timeout=timeout)

    def release(self, node: RemoteNode, client: paramiko.SSHClient, broken: bool = False) -> None:
        """Return a borrowed client.  Set *broken* if the client is dead."""
        self._pool_for(node).release(client, broken=broken)

    def execute(
        self,
        node: RemoteNode,
        command: str,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> tuple[int, str, str]:
        """Run *command* on *node* and return ``(exit_code, stdout, stderr)``.

        Retries up to :data:`_MAX_RETRIES` times with a fresh connection on
        failure.
        """
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            client = self.borrow(node)
            broken = False
            try:
                _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
                chan = stdout.channel
                exit_code = chan.recv_exit_status()
                out = stdout.read().decode("utf-8", errors="replace")
                err = stderr.read().decode("utf-8", errors="replace")
                return exit_code, out, err
            except Exception as exc:
                broken = True
                last_exc = exc
                logger.warning(
                    "SSH execute failed on node %s (attempt %d/%d): %s",
                    node.name,
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    exc,
                )
            finally:
                self.release(node, client, broken=broken)

        raise SSHExecutionError(
            f"Failed to execute command on node {node.name!r} after "
            f"{_MAX_RETRIES + 1} attempts: {last_exc}"
        ) from last_exc

    @contextmanager
    def sftp_session(self, node: RemoteNode) -> Iterator[paramiko.SFTPClient]:
        """Context manager that yields an SFTP channel and cleans up after.

        Example::

            with pool.sftp_session(node) as sftp:
                sftp.put("local.txt", "/remote/path.txt")
        """
        client = self.borrow(node)
        broken = False
        try:
            sftp = client.open_sftp()
        except Exception:
            self.release(node, client, broken=True)
            raise
        try:
            yield sftp
        except Exception:
            broken = True
            raise
        finally:
            try:
                sftp.close()
            except Exception:
                logger.debug("Error closing SFTP channel", exc_info=True)
            self.release(node, client, broken=broken)

    def close(self, node_name: str | None = None) -> None:
        """Close all idle connections for *node_name* (or every node)."""
        if node_name is None:
            with self._lock:
                pools = list(self._pools.values())
                self._pools.clear()
            for p in pools:
                p.close_all()
        else:
            pool = self._pools.get(node_name)
            if pool is not None:
                pool.close_all()

    def __enter__(self) -> SSHConnectionPool:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
