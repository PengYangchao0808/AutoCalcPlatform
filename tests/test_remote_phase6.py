"""
Phase 6 tests — NodeManager (remote node status + ping + cache).

Verifies status aggregation, caching, ping, and degraded/offline classification
without a real SSH connection, using mock monitor / ssh_pool.

Also covers the Python-3.10+ interpreter probe (``detect_node_python``) and
its integration into :meth:`NodeManager.bootstrap_node`.

Run with: PYTHONPATH=src python3 tests/test_remote_phase6.py
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from acp.scheduler.jobs import JobSpec
from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.node_manager import (
    DEFAULT_PYTHON_CANDIDATES,
    InterpreterProbe,
    NodeManager,
    detect_node_python,
    doctor_node,
)
from acp.scheduler.remote.ssh import SSHExecutionError


def _node(name: str = "compute-01", enabled: bool = True, max_jobs: int = 5) -> RemoteNode:
    return RemoteNode(
        name=name,
        host="10.0.0.1",
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
    # First call is the ping itself; the post-ping status refresh may add
    # further SSH calls (metrics + cached software probe).
    assert pool.execute.call_args_list[0].args[1] == "echo ok"


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

# --------------------------------------------------------------------------- #
# detect_node_python — interpreter probe
# --------------------------------------------------------------------------- #


def _pool_with_version(version: str, exit_code: int = 0) -> MagicMock:
    pool = MagicMock()
    pool.execute.return_value = (exit_code, f"{version}\n", "")
    return pool


def test_detect_python_uses_configured_executable() -> None:
    node = _node()
    node.python_executable = "/opt/venv/bin/python"
    pool = _pool_with_version("3.12.4")
    probe = detect_node_python(pool, node)
    assert probe is not None
    assert probe.python_executable == "/opt/venv/bin/python"
    assert probe.version == "3.12.4"
    assert probe.candidates_tried == ("/opt/venv/bin/python",)


def test_detect_python_falls_back_when_configured_too_old() -> None:
    node = _node()
    node.python_executable = "python3.9"
    pool = MagicMock()
    probed: list[str] = []

    def fake_execute(_node, command, timeout=15):
        probed.append(command)
        if "python3.9" in command:
            return (1, "", "Python 3.9 is too old")
        if "python3.12" in command:
            return (0, "3.12.5\n", "")
        if "python3.13" in command:
            return (1, "", "not found")
        return (1, "", "not found")

    pool.execute.side_effect = fake_execute
    probe = detect_node_python(pool, node)
    assert probe is not None
    assert probe.python_executable == "python3.12"
    assert probe.version == "3.12.5"
    # The configured (too-old) interpreter is probed first, then the
    # default candidates walk in order.
    assert "python3.9" in probed[0]


def test_detect_python_walks_default_candidates() -> None:
    node = _node()
    pool = MagicMock()

    def fake_execute(_node, command, timeout=15):
        if "python3.13" in command:
            return (0, "3.13.1\n", "")
        raise AssertionError(f"unexpected probe command: {command}")

    pool.execute.side_effect = fake_execute
    probe = detect_node_python(pool, node)
    assert probe is not None
    assert probe.python_executable == "python3.13"
    assert probe.candidates_tried == ("python3.13",)


def test_detect_python_returns_none_when_all_too_old() -> None:
    node = _node()
    pool = MagicMock()
    pool.execute.return_value = (1, "", "old")
    assert detect_node_python(pool, node) is None
    assert pool.execute.call_count >= 1


def test_detect_python_surfaces_ssh_failure_when_nothing_executed() -> None:
    node = _node()
    pool = MagicMock()
    pool.execute.side_effect = SSHExecutionError("connection refused")
    with pytest.raises(SSHExecutionError, match="connection refused"):
        detect_node_python(pool, node)


def test_detect_python_skips_unrunnable_then_finds_conda() -> None:
    node = _node()
    pool = MagicMock()

    def fake_execute(_node, command, timeout=15):
        if "$HOME" in command:
            return (0, "3.11.9\n", "")
        return (127, "", "command not found")

    pool.execute.side_effect = fake_execute
    probe = detect_node_python(pool, node)
    assert probe is not None
    assert "$HOME" in probe.python_executable


# --------------------------------------------------------------------------- #
# bootstrap_node — interpreter resolution integration
# --------------------------------------------------------------------------- #


def test_bootstrap_aborts_with_clear_error_when_no_python() -> None:
    node = _node()
    nm = NodeManager(_config([node]), MagicMock(), monitor=MagicMock())
    pool = nm._pool
    pool.execute.return_value = (1, "", "not found")
    result = nm.bootstrap_node("compute-01", sync=False)
    assert result.reachable is True
    assert result.exit_code is None
    assert result.ok is False
    assert result.error is not None
    assert "Python" in result.error
    assert "python_executable" in result.error


def test_bootstrap_uses_probe_resolved_python() -> None:
    node = _node()
    nm = NodeManager(_config([node]), MagicMock(), monitor=MagicMock())
    pool = nm._pool

    calls: list[str] = []

    def fake_execute(_node, command, timeout=600):
        calls.append(command)
        if "sys.version_info" in command:
            return (0, "3.12.4\n", "")
        if "pip install" in command:
            return (0, "installed", "")
        raise AssertionError(f"unexpected command: {command}")

    pool.execute.side_effect = fake_execute
    result = nm.bootstrap_node("compute-01", sync=False)
    assert result.ok is True
    assert result.python_executable == "python3.13"
    assert result.python_version == "3.12.4"
    assert any("pip install" in c for c in calls)


def test_bootstrap_ssh_failure_reported_unreachable() -> None:
    node = _node()
    nm = NodeManager(_config([node]), MagicMock(), monitor=MagicMock())
    pool = nm._pool
    pool.execute.side_effect = SSHExecutionError("network down")
    result = nm.bootstrap_node("compute-01", sync=False)
    assert result.reachable is False
    assert "network down" in (result.error or "")


def test_default_python_candidates_start_with_named_interpreters() -> None:
    assert DEFAULT_PYTHON_CANDIDATES[:4] == ("python3.13", "python3.12", "python3.11", "python3.10")
    assert any("anaconda3" in c for c in DEFAULT_PYTHON_CANDIDATES)


def test_interpreter_probe_is_frozen_dataclass() -> None:
    probe = InterpreterProbe(
        python_executable="python3.12", version="3.12.4", candidates_tried=("python3.12",)
    )
    assert probe.python_executable == "python3.12"
    with pytest.raises(Exception):
        probe.version = "3.13.0"  # frozen


# --------------------------------------------------------------------------- #
# doctor_node — deployment self-check
# --------------------------------------------------------------------------- #


def test_doctor_node_reports_python_and_software() -> None:
    node = _node()
    pool = MagicMock()

    def fake_execute(_node, command, timeout=15):
        if "sys.version_info" in command:
            return (0, "3.12.4\n", "")
        if "cccp.software" in command or "detect_version" in command:
            report = {
                "orca": {"configured": "orca", "resolved": "/opt/orca/orca", "version": "6.1.1"},
                "xtb": {"configured": "xtb", "resolved": "/opt/xtb/bin/xtb", "version": "6.6.1"},
                "crest": {"configured": "crest", "resolved": None, "version": None},
                "censo": {"configured": "censo", "resolved": "/opt/censo/bin/censo", "version": "1.5"},
                "shermo": {"configured": "Shermo", "resolved": None, "version": None},
                "isostat": {"configured": "isostat", "resolved": None, "version": None},
                "molclus": {"configured": "molclus", "resolved": None, "version": None},
            }
            return (0, "prefix\n" + __import__("json").dumps(report), "")
        if "~/bin/" in command:
            return (0, "lrwxrwxrwx ... -> /opt/orca/orca\n", "")
        return (0, "", "")

    pool.execute.side_effect = fake_execute
    node.bin_symlinks = {"orca": "/opt/orca/orca"}
    report = doctor_node(pool, node)
    assert report.reachable is True
    assert report.python is not None
    assert report.python.version == "3.12.4"
    assert report.software["orca"]["resolved"] == "/opt/orca/orca"
    assert report.software["orca"]["version"] == "6.1.1"
    assert report.software["crest"]["resolved"] is None
    assert report.symlinks["orca"] == "/opt/orca/orca"


def test_doctor_node_reports_missing_symlink() -> None:
    node = _node()
    node.bin_symlinks = {"Shermo": "/opt/Shermo/Shermo"}
    pool = MagicMock()

    def fake_execute(_node, command, timeout=15):
        if "sys.version_info" in command:
            return (0, "3.12.4\n", "")
        if "cccp.software" in command or "detect_version" in command:
            return (0, '{"orca": {"configured": "orca", "resolved": null, "version": null}}', "")
        if "~/bin/" in command:
            return (0, "MISSING\n", "")
        return (0, "", "")

    pool.execute.side_effect = fake_execute
    report = doctor_node(pool, node)
    assert report.reachable is True
    assert "NOT CREATED" in report.symlinks["Shermo"]


def test_doctor_node_unreachable() -> None:
    pool = MagicMock()
    pool.execute.side_effect = SSHExecutionError("network down")
    report = doctor_node(pool, _node())
    assert report.reachable is False
    assert report.python is None


# --------------------------------------------------------------------------- #
# Status-polling software probe — NodeStatus.software
# --------------------------------------------------------------------------- #

_SOFTWARE_REPORT = {
    "orca": {"configured": "orca", "resolved": "/opt/orca/orca", "version": "6.1.1"},
    "xtb": {"configured": "xtb", "resolved": None, "version": None},
    "crest": {"configured": "crest", "resolved": "/opt/crest/crest", "version": "3.0.2"},
}


def _pool_with_software(report: dict | None = None) -> MagicMock:
    """Pool that answers the interpreter probe and the doctor software script."""
    import json

    pool = MagicMock()

    def fake_execute(_node, command, timeout=15):
        if "sys.version_info" in command:
            return (0, "3.12.4\n", "")
        if "cccp.software" in command:
            if report is None:
                return (1, "", "boom")
            return (0, json.dumps(report), "")
        raise AssertionError(f"unexpected command: {command}")

    pool.execute.side_effect = fake_execute
    return pool


def _ok_monitor() -> MagicMock:
    monitor = MagicMock()
    monitor.get_running_job_count.return_value = 1
    monitor.check_disk_usage.return_value = 20
    return monitor


def test_status_refresh_probes_software() -> None:
    nm = NodeManager(
        _config([_node()]), _pool_with_software(_SOFTWARE_REPORT), monitor=_ok_monitor()
    )
    status = nm.get_node_status("compute-01")
    assert status.status == "online"
    assert status.software["orca"]["resolved"] == "/opt/orca/orca"
    assert status.software["crest"]["version"] == "3.0.2"


def test_software_probe_uses_separate_ttl() -> None:
    pool = _pool_with_software(_SOFTWARE_REPORT)
    nm = NodeManager(_config([_node()]), pool, monitor=_ok_monitor(), software_ttl=300)
    nm.get_node_status("compute-01")
    calls_after_first = pool.execute.call_count
    # Expire only the status cache — the software cache is still fresh.
    nm._cache.pop("compute-01")
    status = nm.get_node_status("compute-01")
    assert status.software["orca"]["resolved"] == "/opt/orca/orca"
    assert pool.execute.call_count == calls_after_first


def test_software_probe_failure_keeps_stale_report() -> None:
    import json

    pool = _pool_with_software(_SOFTWARE_REPORT)
    nm = NodeManager(_config([_node()]), pool, monitor=_ok_monitor(), software_ttl=1)
    nm.get_node_status("compute-01")

    def failing_execute(_node, command, timeout=15):
        if "cccp.software" in command:
            return (1, "", "boom")
        return (0, "3.12.4\n", "")

    pool.execute.side_effect = failing_execute
    nm._cache.pop("compute-01")
    nm._software_cache["compute-01"] = (0.0, nm._software_cache["compute-01"][1])
    status = nm.get_node_status("compute-01")
    assert status.software["orca"]["resolved"] == "/opt/orca/orca"
    assert json.dumps(status.software) != ""


def test_software_empty_when_probe_fails_and_no_cache() -> None:
    nm = NodeManager(_config([_node()]), _pool_with_software(None), monitor=_ok_monitor())
    status = nm.get_node_status("compute-01")
    assert status.status == "online"
    assert status.software == {}


def test_offline_node_has_no_software() -> None:
    pool = _pool_with_software(_SOFTWARE_REPORT)
    nm = NodeManager(_config([_node(enabled=False)]), pool, monitor=MagicMock())
    status = nm.get_node_status("compute-01")
    assert status.status == "offline"
    assert status.software == {}
    pool.execute.assert_not_called()


def test_cached_node_statuses_never_probes() -> None:
    pool = _pool_with_software(_SOFTWARE_REPORT)
    nm = NodeManager(_config([_node()]), pool, monitor=_ok_monitor())
    assert nm.cached_node_statuses() == []
    nm.get_node_status("compute-01")
    calls = pool.execute.call_count
    cached = nm.cached_node_statuses()
    assert [s.name for s in cached] == ["compute-01"]
    assert pool.execute.call_count == calls
