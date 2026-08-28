"""
Remote Node Manager
===================

Status/caching manager for configured remote compute nodes.  Provides a
small API surface used by the web dashboard (``/api/v1/nodes``).
"""

from __future__ import annotations

import json
import logging
import posixpath
import shlex
import threading
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.monitor import RemoteJobMonitor
from acp.scheduler.remote.ssh import SSHConnectionPool, SSHExecutionError

logger = logging.getLogger(__name__)

__all__ = [
    "BootstrapResult",
    "InterpreterProbe",
    "NodeDoctorReport",
    "NodeManager",
    "NodeStatus",
    "detect_node_python",
    "doctor_node",
]

# ACP requires Python 3.10+ (``typing.TypeAlias``, PEP 604 unions, ...).
MIN_PYTHON_VERSION = (3, 10)

# Candidates probed in order when ``RemoteNode.python_executable`` is not
# configured.  Named interpreters first, then common conda installs
# (``$HOME`` is expanded by the remote shell, never by this code).
DEFAULT_PYTHON_CANDIDATES: tuple[str, ...] = (
    "python3.13",
    "python3.12",
    "python3.11",
    "python3.10",
    "python3",
    "python",
    "$HOME/anaconda3/bin/python",
    "$HOME/miniconda3/bin/python",
    "/opt/anaconda3/bin/python",
    "/opt/miniconda3/bin/python",
    "/usr/local/bin/python3",
)

_VERSION_PROBE_SCRIPT = (
    "import sys; "
    "print('%d.%d.%d' % sys.version_info[:3]); "
    f"sys.exit(0 if sys.version_info >= {MIN_PYTHON_VERSION} else 1)"
)


@dataclass(frozen=True)
class InterpreterProbe:
    """A usable (Python 3.10+) interpreter found on a remote node.

    Attributes:
        python_executable: Resolved interpreter path/name on the node.
        version: ``"major.minor.patch"`` reported by the interpreter.
        candidates_tried: Ordered list of candidates that were probed.
    """

    python_executable: str
    version: str
    candidates_tried: tuple[str, ...]


def _quote_probe_target(py: str) -> str:
    """Quote a probe target for the remote shell.

    ``$HOME/...`` style candidates must stay unquoted so the remote shell
    expands them; everything else (plain names, absolute paths) is quoted.
    """
    if py.startswith("$"):
        return py
    return shlex.quote(py)


def detect_node_python(
    pool: SSHConnectionPool,
    node: RemoteNode,
    candidates: Sequence[str] | None = None,
    timeout: int = 15,
) -> InterpreterProbe | None:
    """Find the first Python 3.10+ interpreter available on *node*.

    When :attr:`RemoteNode.python_executable` is configured it is probed
    first (and, if usable, returned without touching the default list);
    otherwise :data:`DEFAULT_PYTHON_CANDIDATES` is walked in order.  Each
    candidate runs a tiny version probe over SSH; the first one that exits
    0 with a ``major.minor.patch`` banner wins.

    Args:
        pool: SSH connection pool used for the remote probes.
        node: The remote node to probe.
        candidates: Optional override of the ordered candidate list (after
            ``node.python_executable``).
        timeout: Per-candidate SSH timeout in seconds.

    Returns:
        An :class:`InterpreterProbe` for the first usable interpreter, or
        ``None`` when no candidate satisfies the version floor.
    """
    ordered: list[str] = []
    if node.python_executable and node.python_executable != "python":
        # ``python`` is the dataclass default (== "not configured") — only a
        # *distinct* value counts as an explicit pin.
        ordered.append(node.python_executable)
    if candidates is not None:
        ordered.extend(c for c in candidates if c not in ordered)
    else:
        ordered.extend(c for c in DEFAULT_PYTHON_CANDIDATES if c not in ordered)

    last_ssh_error: SSHExecutionError | None = None
    executed_ok = False

    for py in ordered:
        command = f"{_quote_probe_target(py)} -c {shlex.quote(_VERSION_PROBE_SCRIPT)}"
        try:
            code, out, err = pool.execute(node, command, timeout=timeout)
        except SSHExecutionError as exc:
            logger.debug("Python probe %r on %s failed: %s", py, node.name, exc)
            last_ssh_error = exc
            continue
        executed_ok = True
        if code != 0:
            logger.debug(
                "Python probe %r on %s: exit=%s (below %s.%s or not runnable)",
                py,
                node.name,
                code,
                *MIN_PYTHON_VERSION,
            )
            continue
        version = (out or "").strip().splitlines()[-1] if (out or "").strip() else ""
        if version:
            logger.info(
                "Resolved node python for %s: %s (version %s)",
                node.name,
                py,
                version,
            )
            return InterpreterProbe(
                python_executable=py,
                version=version,
                candidates_tried=tuple(ordered[: ordered.index(py) + 1]),
            )
    # Every candidate either failed transport or was too old.  If the node
    # itself was unreachable (no candidate ever executed), surface the SSH
    # failure instead of reporting a misleading "no usable interpreter".
    if not executed_ok and last_ssh_error is not None:
        raise last_ssh_error
    return None


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
        python_version: ``"major.minor.patch"`` of the interpreter that ran
            pip (empty when probing failed).
        requirements_path: Remote path of the requirements file installed.
        stdout: Full pip stdout (may be long; callers typically tail it).
        stderr: Full pip stderr.
        sync_uploaded: Number of files uploaded by the pre-bootstrap code sync
            (the requirements file is synced alongside the code).
        sync_errors: Per-file sync errors, if any.
        symlinks_applied: Names of ``~/bin`` symlinks created from
            ``node.bin_symlinks``.
        error: Human-readable failure message when ``reachable`` is False.
    """

    node: str
    reachable: bool
    exit_code: int | None = None
    python_executable: str = "python"
    python_version: str = ""
    requirements_path: str = ""
    stdout: str = ""
    stderr: str = ""
    sync_uploaded: int = 0
    sync_errors: list[str] = field(default_factory=list)
    symlinks_applied: list[str] = field(default_factory=list)
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

        Before installing, the node's Python interpreter is resolved via
        :func:`detect_node_python` — ``node.python_executable`` is honoured
        when it satisfies the Python 3.10 floor, otherwise the default
        candidates (named interpreters then conda installs) are probed.  A
        node whose only interpreters are older than 3.10 is reported with a
        clear error instead of installing into a broken runtime.

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

        # Resolve a Python 3.10+ interpreter on the node before pip.
        try:
            probe = detect_node_python(self._pool, node)
        except SSHExecutionError as exc:
            logger.error("Bootstrap SSH failure on %s: %s", node.name, exc)
            return BootstrapResult(
                node=node.name,
                reachable=False,
                python_executable=node.python_executable or "python",
                requirements_path=requirements_remote,
                sync_uploaded=sync_uploaded,
                sync_errors=sync_errors,
                error=str(exc),
            )
        if probe is None:
            configured = node.python_executable
            hint = (
                f"configured python_executable {configured!r} is not a runnable "
                "Python 3.10+ interpreter"
                if configured
                else "no Python 3.10+ interpreter found — configure "
                "cluster.nodes[].python_executable (e.g. an anaconda/miniconda "
                "python) or install Python 3.10+ on the node"
            )
            logger.error(
                "Bootstrap %s aborted: %s (probed %s)",
                node.name,
                hint,
                ", ".join(DEFAULT_PYTHON_CANDIDATES),
            )
            return BootstrapResult(
                node=node.name,
                reachable=True,
                python_executable=node.python_executable or "python",
                requirements_path=requirements_remote,
                sync_uploaded=sync_uploaded,
                sync_errors=sync_errors,
                error=f"no usable Python interpreter: {hint}",
            )

        py = probe.python_executable

        # Apply declared ~/bin symlinks (node.bin_symlinks) so QC binaries
        # are resolvable from PATH on the node regardless of how the
        # cluster's login-shell environment is composed.
        symlinks_applied: list[str] = []
        for name, target in node.bin_symlinks.items():
            symlink_cmd = (
                f"mkdir -p ~/bin && ln -sf {target} ~/bin/{shlex.quote(name)}"
            )
            try:
                code, _out, _err = self._pool.execute(node, symlink_cmd, timeout=30)
            except SSHExecutionError as exc:
                logger.error(
                    "Bootstrap symlink %r on %s failed: %s", name, node.name, exc
                )
                continue
            if code == 0:
                symlinks_applied.append(name)
                logger.info("Symlinked %s -> %s on %s", name, target, node.name)
            else:
                logger.error(
                    "Bootstrap symlink %r on %s failed (exit=%s): %s",
                    name,
                    node.name,
                    code,
                    _err.strip() or _out.strip(),
                )

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
                python_version=probe.version,
                requirements_path=requirements_remote,
                sync_uploaded=sync_uploaded,
                sync_errors=sync_errors,
                error=str(exc),
            )

        ok = exit_code == 0
        logger.info(
            "Bootstrap %s on %s: pip exit=%s (python %s, %s)",
            "succeeded" if ok else "failed",
            node.name,
            exit_code,
            probe.version,
            f"{sync_uploaded} files synced" if sync else "sync skipped",
        )
        return BootstrapResult(
            node=node.name,
            reachable=True,
            exit_code=exit_code,
            python_executable=py,
            python_version=probe.version,
            requirements_path=requirements_remote,
            stdout=out,
            stderr=err,
            sync_uploaded=sync_uploaded,
            sync_errors=sync_errors,
            symlinks_applied=symlinks_applied,
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


# --------------------------------------------------------------------------- #
# Deployment doctor — per-node self-check for `acp doctor --node`
# --------------------------------------------------------------------------- #

_DOCTOR_SOFTWARE_SCRIPT = (
    "import json, os, sys\n"
    "cfg = {}\n"
    "try:\n"
    "    import yaml\n"
    "    for cand in ('~/.cccp.yaml', '~/.conformer_search.yaml'):\n"
    "        p = os.path.expanduser(cand)\n"
    "        if os.path.isfile(p):\n"
    "            with open(p) as fh:\n"
    "                cfg = yaml.safe_load(fh) or {}\n"
    "            break\n"
    "except Exception:\n"
    "    cfg = {}\n"
    "from cccp.software import detect_version, resolve_executable\n"
    "names = ['orca', 'xtb', 'crest', 'censo', 'shermo', 'isostat', 'molclus']\n"
    "exes = cfg.get('executables') or {}\n"
    "report = {}\n"
    "for name in names:\n"
    "    configured = ((exes.get(name) or {}).get('path')) or name\n"
    "    resolved = resolve_executable(name, configured_path=configured)\n"
    "    report[name] = {\n"
    "        'configured': configured,\n"
    "        'resolved': str(resolved) if resolved else None,\n"
    "        'version': detect_version(name, resolved) if resolved else None,\n"
    "    }\n"
    "print(json.dumps(report))\n"
)


@dataclass(frozen=True)
class NodeDoctorReport:
    """Per-node deployment self-check result (``acp doctor``)."""

    node: str
    host: str
    reachable: bool
    python: InterpreterProbe | None = None
    software: dict[str, dict[str, Any]] = field(default_factory=dict)
    symlinks: dict[str, str] = field(default_factory=dict)
    error: str | None = None


def doctor_node(pool: SSHConnectionPool, node: RemoteNode, timeout: int = 30) -> NodeDoctorReport:
    """Run the deployment self-check for a single *node*.

    Probes the Python interpreter (:func:`detect_node_python`), resolves
    every known QC binary with the same centralized resolver the workflows
    use (driven by the synced codebase under ``remote_code_dir``), and
    reports the ``~/bin`` symlink state for ``node.bin_symlinks``.

    Returns:
        :class:`NodeDoctorReport` — never raises for node failures; SSH
        transport errors surface as ``reachable=False``.
    """
    if not node.enabled:
        return NodeDoctorReport(
            node=node.name, host=node.host, reachable=False, error="node is disabled"
        )
    try:
        probe = detect_node_python(pool, node)
    except SSHExecutionError as exc:
        return NodeDoctorReport(
            node=node.name, host=node.host, reachable=False, error=str(exc)
        )

    report = NodeDoctorReport(
        node=node.name,
        host=node.host,
        reachable=True,
        python=probe,
    )
    if probe is None:
        return report

    script_arg = shlex.quote(_DOCTOR_SOFTWARE_SCRIPT)
    command = (
        "bash -lc "
        + shlex.quote(
            f"export PYTHONPATH={node.remote_code_dir}/src:$PYTHONPATH && "
            f"{probe.python_executable} -c {script_arg}"
        )
    )
    try:
        code, out, _err = pool.execute(node, command, timeout=timeout)
    except SSHExecutionError as exc:
        return NodeDoctorReport(
            node=node.name,
            host=node.host,
            reachable=False,
            error=f"software probe failed: {exc}",
        )
    if code == 0 and out.strip():
        try:
            report = NodeDoctorReport(
                node=node.name,
                host=node.host,
                reachable=True,
                python=probe,
                software=json.loads(out.strip().splitlines()[-1]),
            )
        except Exception as exc:  # noqa: BLE001 — report, don't abort
            report = NodeDoctorReport(
                node=node.name,
                host=node.host,
                reachable=True,
                python=probe,
                error=f"software report unparseable: {exc}",
            )

    symlinks: dict[str, str] = {}
    for name, target in node.bin_symlinks.items():
        symlink_cmd = (
            f"ls -l ~/bin/{shlex.quote(name)} 2>/dev/null || echo MISSING"
        )
        try:
            scode, sout, _serr = pool.execute(node, symlink_cmd, timeout=15)
            if scode == 0 and sout.strip() and "MISSING" not in sout:
                symlinks[name] = target
            else:
                symlinks[name] = f"{target} (NOT CREATED — run node bootstrap)"
        except SSHExecutionError:
            symlinks[name] = f"{target} (unreachable)"
    report = NodeDoctorReport(
        node=report.node,
        host=report.host,
        reachable=report.reachable,
        python=report.python,
        software=report.software,
        symlinks=symlinks,
        error=report.error,
    )
    return report
