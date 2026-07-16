"""
Phase 6 tests — NodeManager (remote node status + ping + cache).

Verifies status aggregation, caching, ping, and degraded/offline classification
without a real SSH connection, using mock monitor / ssh_pool.

Run with: PYTHONPATH=src python3 tests/test_remote_phase6.py
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.node_manager import NodeManager
from acp.scheduler.remote.ssh import SSHExecutionError


def _node(name: str = "compute-01", enabled: bool = True, max_jobs: int = 5) -> RemoteNode:
    return RemoteNode(
        name=name,
        host="10.16.5.157",
        username="<user>",
        remote_work_dir="/scratch/<user>/acp_jobs",
        remote_code_dir="/home/<user>/acp_code",
        max_concurrent_jobs=max_jobs,
        enabled=enabled,
    )


def _config(nodes: list[RemoteNode], mode: str = "remote") -> RemoteExecutionConfig:
    return RemoteExecutionConfig(execution_mode=mode, nodes=nodes)


def test_list_nodes_empty_when_local() -> None:
    nm = NodeManager(_config([_node()], mode="local"), MagicMock(), monitor=MagicMock())
    assert nm.list_nodes() == []


def test_get_node_status_online() -> None:
    monitor = MagicMock()
    monitor.get_running_job_count.return_value = 2
    monitor.check_disk_usage.return_value = 45
    nm = NodeManager(_config([_node()]), MagicMock(), monitor=monitor)
    status = nm.get_node_status("compute-01")
    assert status.status == "online"
    assert status.running_jobs == 2
    assert status.disk_usage_pct == 45
    assert status.max_jobs == 5


def test_get_node_status_degraded_on_disk() -> None:
    monitor = MagicMock()
    monitor.get_running_job_count.return_value = 1
    monitor.check_disk_usage.return_value = 92
    nm = NodeManager(_config([_node()]), MagicMock(), monitor=monitor)
    assert nm.get_node_status("compute-01").status == "degraded"


def test_get_node_status_degraded_on_capacity() -> None:
    monitor = MagicMock()
    monitor.get_running_job_count.return_value = 5
    monitor.check_disk_usage.return_value = 40
    nm = NodeManager(_config([_node()]), MagicMock(), monitor=monitor)
    assert nm.get_node_status("compute-01").status == "degraded"


def test_get_node_status_offline_on_ssh_error() -> None:
    monitor = MagicMock()
    monitor.get_running_job_count.side_effect = SSHExecutionError("boom")
    nm = NodeManager(_config([_node()]), MagicMock(), monitor=monitor)
    status = nm.get_node_status("compute-01")
    assert status.status == "offline"
    assert status.error is not None


def test_disabled_node_reported_offline() -> None:
    monitor = MagicMock()
    nm = NodeManager(_config([_node(enabled=False)]), MagicMock(), monitor=monitor)
    status = nm.get_node_status("compute-01")
    assert status.status == "offline"
    assert status.error == "disabled"
    monitor.get_running_job_count.assert_not_called()


def test_get_node_status_unknown_raises() -> None:
    nm = NodeManager(_config([_node()]), MagicMock(), monitor=MagicMock())
    with pytest.raises(ValueError):
        nm.get_node_status("nope")


def test_status_cache_avoids_repeated_ssh() -> None:
    monitor = MagicMock()
    monitor.get_running_job_count.return_value = 1
    monitor.check_disk_usage.return_value = 20
    nm = NodeManager(_config([_node()]), MagicMock(), monitor=monitor, cache_ttl=30)
    nm.get_node_status("compute-01")
    nm.get_node_status("compute-01")
    assert monitor.get_running_job_count.call_count == 1


def test_ping_node_reachable_refreshes_cache() -> None:
    pool = MagicMock()
    pool.execute.return_value = (0, "ok", "")
    monitor = MagicMock()
    monitor.get_running_job_count.return_value = 0
    monitor.check_disk_usage.return_value = 10
    nm = NodeManager(_config([_node()]), pool, monitor=monitor)
    assert nm.ping_node("compute-01") is True
    pool.execute.assert_called_once()


def test_ping_node_unreachable_returns_false() -> None:
    pool = MagicMock()
    pool.execute.side_effect = SSHExecutionError("nope")
    nm = NodeManager(_config([_node()]), pool, monitor=MagicMock())
    assert nm.ping_node("compute-01") is False


def test_ping_unknown_node_returns_false() -> None:
    nm = NodeManager(_config([_node()]), MagicMock(), monitor=MagicMock())
    assert nm.ping_node("missing") is False


def test_list_nodes_returns_all_configured() -> None:
    nodes = [_node("compute-01"), _node("compute-02", enabled=False)]
    monitor = MagicMock()
    monitor.get_running_job_count.return_value = 0
    monitor.check_disk_usage.return_value = 10
    nm = NodeManager(_config(nodes), MagicMock(), monitor=monitor)
    statuses = nm.list_nodes()
    assert [s.name for s in statuses] == ["compute-01", "compute-02"]
    assert statuses[1].status == "offline"
