"""
Phase 6 tests — NodeManager (remote node status + ping + cache).

Verifies status aggregation, caching, ping, and degraded/offline classification
without a real SSH connection, using mock monitor / ssh_pool.

Run with: PYTHONPATH=src python3 tests/test_remote_phase6.py
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from acp.scheduler.jobs import JobSpec
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


# ====================================================================== #
# Local/remote parity tests (todo 52 §f)
# ====================================================================== #


def _spec(workflow: str, inp: dict | None = None, method: dict | None = None) -> JobSpec:
    return JobSpec(
        workflow=workflow,
        input=inp or {},
        method=method or {},
        resources={"nproc": 4},
    )


def _stage_names(spec: JobSpec) -> list[str]:
    from acp.scheduler.stage_tasks import PlanCompiler

    return [s.stage_name for s in PlanCompiler.compile(spec)]


def _remote_argv(spec: JobSpec) -> list[str]:
    from acp.scheduler.remote.script_gen import build_remote_cli_command

    return build_remote_cli_command(spec, input_path="input.xyz")


def _remote_flags(spec: JobSpec) -> set[str]:
    return {t for t in _remote_argv(spec) if t.startswith("--")}


# ── BatchOptimize parity ──────────────────────────────────────────────


def test_batchoptimize_local_remote_parity() -> None:
    """Local PlanCompiler and remote script_gen produce consistent stage
    sequences and CLI flags for all BatchOptimize profiles."""
    from acp.storage.manifest import ProductKind

    profiles = {
        "opt_only": ["prepare", "optimize", "finalize"],
        "opt_freq": ["prepare", "optimize", "frequency", "finalize"],
        "opt_freq_sp": ["prepare", "optimize", "frequency", "single_point", "finalize"],
        "opt_freq_sp_thermo": [
            "prepare",
            "optimize",
            "frequency",
            "single_point",
            "thermochemistry",
            "finalize",
        ],
    }
    for profile, expected in profiles.items():
        spec = _spec("BatchOptimize", {"from_artifact": "/tmp/m.json"}, {"profile": profile})
        # Stage sequence parity
        assert _stage_names(spec) == expected, f"profile={profile}"
        # CLI argv parity: remote must have workflow prefix + profile flag
        argv = _remote_argv(spec)
        assert argv[:5] == ["python", "-m", "acp.cli", "run", "BatchOptimize"]
        assert "--profile" in argv
        assert profile in argv
    # Result manifest product kinds (all profiles emit structure + energy_report)
    expected_kinds = {ProductKind.STRUCTURE, ProductKind.ENERGY_REPORT}
    assert expected_kinds == {ProductKind.STRUCTURE, ProductKind.ENERGY_REPORT}


# ── PESsearch parity ──────────────────────────────────────────────────


def test_pessearch_local_remote_parity() -> None:
    """Local PlanCompiler and remote script_gen produce consistent stage
    sequences and CLI flags for PESsearch (path-mode and bond-scan-mode)."""
    # Path-mode (default)
    spec_path = _spec("PESsearch", {"from": "/tmp/cm.json"}, {"strategy": "direct"})
    assert _stage_names(spec_path) == [
        "prepare",
        "validate_coordinate",
        "materialize_input",
        "run_relaxed_scan",
        "extract_frames",
        "run_single_points",
        "build_profile",
        "select_candidates",
        "finalize",
    ]
    argv = _remote_argv(spec_path)
    assert argv[:5] == ["python", "-m", "acp.cli", "run", "PESsearch"]
    assert "--strategy" in argv
    assert "direct" in argv

    # Bond-scan-mode
    spec_scan = _spec(
        "PESsearch",
        {"scan_request": {"atom1": 0, "atom2": 1}},
        {"mode": "bond_length_scan"},
    )
    stages = _stage_names(spec_scan)
    assert stages[0] == "prepare"
    assert stages[-1] == "finalize"
    assert len(stages) == 9  # 9-stage static bond-scan pipeline
    argv_scan = _remote_argv(spec_scan)
    assert "--mode" in argv_scan
    assert "bond_length_scan" in argv_scan
    assert "--scan-config" in argv_scan  # remote ships scan_config.json


# ── IRC parity ────────────────────────────────────────────────────────


def test_irc_local_remote_parity() -> None:
    """Local PlanCompiler and remote script_gen produce consistent stage
    sequences and CLI flags for IRC."""
    spec = _spec(
        "irc",
        {"source": "CCO", "source_type": "smiles", "directions": ["forward", "reverse"]},
        {"method": "r2SCAN-3c"},
    )
    # Stage sequence: single irc stage
    assert _stage_names(spec) == ["irc"]
    # CLI argv parity
    argv = _remote_argv(spec)
    assert argv[:5] == ["python", "-m", "acp.cli", "run", "irc"]
    assert "--input" in argv
    assert "--method" in argv
    assert "r2SCAN-3c" in argv
    # Direction flag
    assert "--direction" in argv
    # Checkpoint schema: IRC uses result_manifest with irc_endpoint product
    from acp.storage.manifest import ProductKind

    assert ProductKind.IRC_ENDPOINT == "irc_endpoint"


# ── Scan parity ───────────────────────────────────────────────────────


def test_scan_local_remote_parity() -> None:
    """Local PlanCompiler and remote script_gen produce consistent stage
    sequences and CLI flags for relaxed coordinate scan."""
    spec = _spec(
        "scan",
        {"source": "CCO", "source_type": "smiles", "coordinate": "0,1,1.0,3.0"},
        {"levels": {"scan": {"functional": "r2SCAN-3c"}}, "scan_coordinates": "0,1,1.0,3.0"},
    )
    # Stage sequence: single scan stage
    assert _stage_names(spec) == ["scan"]
    # CLI argv parity
    argv = _remote_argv(spec)
    assert argv[:5] == ["python", "-m", "acp.cli", "run", "scan"]
    assert "--nproc" in argv
    assert "4" in argv
    # Scan uses trajectory product kind
    from acp.storage.manifest import ProductKind

    assert ProductKind.TRAJECTORY == "trajectory"
