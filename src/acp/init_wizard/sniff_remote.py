"""Remote sniffing + remote ``~/.cccp.yaml`` write for the ACP init wizard.

Plan acp-init T5 (D8/D9/D12): wraps :func:`acp.scheduler.remote.node_manager.doctor_node`
with a pure-shell PATH fallback probe for nodes where the doctor's software
script cannot run (no synced code, no usable Python), and implements the
D9 remote-config read-modify-write protocol (read with absence/abort rules,
backup → tmp upload → chmod → ``mv``) plus the ACP-side manual-spec dict
mutation and the optional immediate ``~/bin`` symlink creation.

All ``acp.scheduler.remote`` imports are FUNCTION-LOCAL (D7): the package
``__init__`` eagerly imports paramiko-dependent modules, and this module
must stay importable without the ``remote`` extra. The ``TYPE_CHECKING``
imports below never execute at runtime — they exist only for mypy strict.
"""

from __future__ import annotations

import logging
import os
import shlex
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

import yaml

from acp.init_wizard.persist import InitAbort

if TYPE_CHECKING:  # never imported at runtime (D7 — no paramiko here)
    from acp.scheduler.remote.config import RemoteNode
    from acp.scheduler.remote.ssh import SSHConnectionPool

logger = logging.getLogger(__name__)

__all__ = [
    "apply_remote_manual_spec",
    "make_remote_symlinks",
    "read_remote_config",
    "remote_home",
    "shell_path_probe",
    "sniff_remote",
    "write_remote_config",
]

#: Remote config file name inside the user's home (D9).
REMOTE_CONFIG_NAME = ".cccp.yaml"

#: Doctor software-report names, in :data:`_DOCTOR_SOFTWARE_SCRIPT` order.
_DOCTOR_SOFTWARE_NAMES: tuple[str, ...] = (
    "orca",
    "xtb",
    "crest",
    "censo",
    "shermo",
    "isostat",
    "molclus",
)

#: Binary names swept by the D8 shell fallback — includes both Shermo
#: spellings because the installed script name varies by cluster.
_SHELL_PROBE_NAMES: tuple[str, ...] = (
    "orca",
    "xtb",
    "crest",
    "censo",
    "Shermo",
    "shermo",
    "isostat",
    "molclus",
)


def remote_home(pool: SSHConnectionPool, node: RemoteNode) -> str:
    """Resolve the node's absolute home directory via ``echo $HOME``.

    SFTP cannot expand ``~`` (``_norm_remote`` leaves it untouched), so the
    D9 flow resolves the home ONCE and threads absolute paths everywhere.

    Raises:
        RuntimeError: On non-zero exit or an empty ``$HOME``.
    """
    code, out, err = pool.execute(node, "echo $HOME")
    if code != 0:
        raise RuntimeError(f"{node.name}: echo $HOME 失败：{err.strip()}")
    home = out.strip()
    if not home:
        raise RuntimeError(f"{node.name}: $HOME 为空，无法解析远端配置路径")
    return home


def shell_path_probe(pool: SSHConnectionPool, node: RemoteNode) -> dict[str, str | None]:
    """Probe QC binaries via ``command -v`` in ONE SSH command (D8).

    Sweeps :data:`_SHELL_PROBE_NAMES` in a single round-trip so a
    declared-but-never-bootstrapped node can still be sniffed when the
    doctor's Python software script cannot run.

    Returns:
        ``{name: path | None}`` for every swept name (never raises for
        SSH/exit failures — unreachable nodes yield all-None).
    """
    found: dict[str, str | None] = {name: None for name in _SHELL_PROBE_NAMES}
    names = " ".join(_SHELL_PROBE_NAMES)
    command = (
        f"for n in {names}; do "
        'p=$(command -v "$n" 2>/dev/null); '
        'printf \'%s=%s\\n\' "$n" "${p:-}"; '
        "done"
    )
    try:
        code, out, _err = pool.execute(node, command, timeout=30)
    except Exception as exc:  # noqa: BLE001 — report, never abort the sniff
        from acp.scheduler.remote.ssh import SSHExecutionError

        if not isinstance(exc, SSHExecutionError):
            raise
        logger.warning("shell PATH 探测失败 %s: %s", node.name, exc)
        return found
    if code != 0:
        logger.warning("shell PATH 探测在 %s 上退出码 %s", node.name, code)
        return found
    for line in out.splitlines():
        name, sep, path = line.partition("=")
        if sep and name in found and path.strip():
            found[name] = path.strip()
    return found


def sniff_remote(pool: SSHConnectionPool, node: RemoteNode) -> dict[str, Any]:
    """Run the deployment doctor on *node*, with the D8 shell fallback.

    Falls back to :func:`shell_path_probe` when the doctor's software probe
    failed — i.e. the report carries an ``error`` OR ``software`` is empty
    (covers both the no-synced-code case and the no-Python-3.10+ early
    return, which leaves ``error=None`` / ``software={}``).

    Returns:
        Plain dict mirroring the :class:`NodeDoctorReport` fields
        (``node``/``host``/``reachable``/``python``/``software``/
        ``symlinks``/``error``) plus:

        - ``software_fallback``: ``True`` when the shell PATH probe ran.
        - Doctor ``software`` entries keep the exact
          ``{configured, resolved, version}`` shape; shell-fallback entries
          mirror that shape and add ``"source": "shell"`` (``version`` is
          always ``None`` — the shell probe resolves paths only).

    Never raises for node failures (mirrors ``doctor_node``); transport
    errors inside the fallback surface as all-``None`` probe results.
    """
    # FUNCTION-LOCAL import (D7): scheduler.remote.__init__ pulls paramiko.
    from acp.scheduler.remote.node_manager import doctor_node

    report = doctor_node(pool, node)
    result = asdict(report)
    software: dict[str, Any] = dict(result.get("software") or {})
    needs_fallback = report.error is not None or not software
    result["software_fallback"] = needs_fallback
    if needs_fallback:
        shell = shell_path_probe(pool, node)
        for name in _DOCTOR_SOFTWARE_NAMES:
            path = shell.get(name) or shell.get(name.capitalize())
            software[name] = {
                "configured": path or name,
                "resolved": path,
                "version": None,
                "source": "shell",
            }
        result["software"] = software
    return result


def _backup_timestamp() -> str:
    """Timestamp used in remote backup file names (monkeypatch-friendly seam)."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _remote_file_mode(pool: SSHConnectionPool, node: RemoteNode, path: str) -> int | None:
    """Read the remote file's octal mode via ``stat -c %a`` (FileStager has no API).

    Returns:
        The mode as an int, or ``None`` when the stat fails/is unparseable
        (the write then defaults to 0o644).
    """
    code, out, err = pool.execute(node, f"stat -c %a {shlex.quote(path)}")
    if code != 0:
        logger.warning("stat %s:%s 失败（%s），将使用默认权限 644", node.name, path, err.strip())
        return None
    try:
        return int(out.strip(), 8)
    except ValueError:
        logger.warning("stat %s:%s 输出不可解析（%r），将使用默认权限 644", node.name, path, out)
        return None


def read_remote_config(
    pool: SSHConnectionPool, node: RemoteNode, home: str
) -> tuple[dict[str, Any], int | None]:
    """Read the remote ``<home>/.cccp.yaml`` with the D1 abort rules.

    File-absence is NOT an error (fresh-node path): ``remote_exists`` is
    checked first and a missing file yields ``({}, None)``. A present file
    is read via ``FileStager.read_remote_text`` and must parse as a YAML
    mapping (parse error or non-dict non-None root aborts — a user config
    file is never auto-rewritten). The original octal mode is read via SSH
    ``stat`` so the write can preserve it.

    Returns:
        ``(data, original_mode)`` — ``original_mode`` is ``None`` for a
        missing/unstat-able file.

    Raises:
        InitAbort: On YAML parse error or a non-dict root (NO write
            side-effects have happened at that point).
    """
    from acp.scheduler.remote.sftp import FileStager

    stager = FileStager(pool)
    path = f"{home}/{REMOTE_CONFIG_NAME}"
    if not stager.remote_exists(node, path):
        logger.info("远端 %s:%s 不存在，按全新节点处理", node.name, path)
        return {}, None
    try:
        text = stager.read_remote_text(node, path)
    except OSError as exc:
        raise InitAbort(f"无法读取远端配置：{node.name}:{path}（{exc}）") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InitAbort(f"远端配置解析失败：{node.name}:{path}（{exc}）") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise InitAbort(
            f"远端配置根节点必须是映射，{node.name}:{path} 实际为 {type(data).__name__}"
        )
    return data, _remote_file_mode(pool, node, path)


def write_remote_config(
    pool: SSHConnectionPool,
    node: RemoteNode,
    home: str,
    data: dict[str, Any],
    original_mode: int | None,
) -> None:
    """Write *data* to the remote ``<home>/.cccp.yaml`` (D9 protocol).

    Sequence: SSH ``cp -p`` backup to ``<path>.bak-<ts>`` (skipped on the
    fresh-node path when the target does not exist) → ``FileStager.upload_text``
    to ``<path>.tmp-<pid>`` → SSH ``chmod`` (preserved *original_mode*, else
    ``644``) → SSH ``mv`` over the target. SFTP cannot expand ``~`` — pass
    the absolute path via :func:`remote_home`.

    Raises:
        InitAbort: On upload/backup/chmod/mv failure (the tmp file is
            cleaned up best-effort before raising).
    """
    from acp.scheduler.remote.sftp import FileStager

    stager = FileStager(pool)
    path = f"{home}/{REMOTE_CONFIG_NAME}"
    text = yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
    tmp_path = f"{path}.tmp-{os.getpid()}"
    mode_arg = f"{original_mode:o}" if original_mode is not None else "644"

    if stager.remote_exists(node, path):
        backup_path = f"{path}.bak-{_backup_timestamp()}"
        code, _out, err = pool.execute(
            node, f"cp -p {shlex.quote(path)} {shlex.quote(backup_path)}"
        )
        if code != 0:
            raise InitAbort(f"远端备份失败：{node.name}:{path}（{err.strip()}）")
        logger.info("已备份 %s:%s -> %s", node.name, path, backup_path)

    try:
        stager.upload_text(node, text, tmp_path)
    except Exception as exc:  # noqa: BLE001 — mid-upload SFTP failure gets cleanup + InitAbort
        try:
            pool.execute(node, f"rm -f {shlex.quote(tmp_path)}")
        except Exception as cleanup_exc:  # noqa: BLE001 — best-effort cleanup only
            logger.warning("远端 tmp 清理失败 %s:%s：%s", node.name, tmp_path, cleanup_exc)
        raise InitAbort(f"远端上传失败：{node.name}:{tmp_path}（{exc}）") from exc

    code, _out, err = pool.execute(node, f"chmod {mode_arg} {shlex.quote(tmp_path)}")
    if code != 0:
        pool.execute(node, f"rm -f {shlex.quote(tmp_path)}")
        raise InitAbort(f"远端 chmod 失败：{node.name}:{tmp_path}（{err.strip()}）")
    code, _out, err = pool.execute(node, f"mv {shlex.quote(tmp_path)} {shlex.quote(path)}")
    if code != 0:
        pool.execute(node, f"rm -f {shlex.quote(tmp_path)}")
        raise InitAbort(f"远端写入失败（mv）：{node.name}:{path}（{err.strip()}）")
    logger.info("已写入远端配置 %s:%s（mode=%s）", node.name, path, mode_arg)


def apply_remote_manual_spec(
    target_data: dict[str, Any], node_name: str, specs: dict[str, str]
) -> None:
    """Merge manual remote-path specs into a config dict (D9 + D12).

    Pure-dict mutation — no I/O. Locates (or creates) the
    ``cluster.nodes`` entry named *node_name* and, for every
    ``{software: remote_path}`` in *specs*:

    - sets ``executables.<name>.path`` on the node entry (the exact shape
      the remote resolver and ``doctor_node`` read),
    - sets ``bin_symlinks[name] = path`` on the node entry,
    - merges *name* into ``capabilities.software`` — plain-list dict shape,
      preserving existing order, appending new names, deduplicated
      (``_parse_declared_software`` semantics).

    All validation happens BEFORE any mutation (D12): a spec path that is
    empty or contains a space raises ValueError — bootstrap's symlink
    command leaves the target unquoted, so spaces would break it.

    Raises:
        ValueError: On any empty/space-containing spec path (target_data
            is left untouched).
    """
    for name, path in specs.items():
        if not path or " " in path:
            raise ValueError(f"软件 {name!r} 的远端路径必须非空且不含空格（D12）：{path!r}")

    cluster = target_data.setdefault("cluster", {})
    nodes = cluster.setdefault("nodes", [])
    node_entry: dict[str, Any] | None = None
    for existing in nodes:
        if isinstance(existing, dict) and existing.get("name") == node_name:
            node_entry = existing
            break
    if node_entry is None:
        node_entry = {"name": node_name}
        nodes.append(node_entry)

    for name, path in specs.items():
        executables = node_entry.setdefault("executables", {})
        entry = executables.get(name)
        if not isinstance(entry, dict):
            entry = {}
            executables[name] = entry
        entry["path"] = path

        bin_symlinks = node_entry.setdefault("bin_symlinks", {})
        bin_symlinks[name] = path

        capabilities = node_entry.get("capabilities")
        if not isinstance(capabilities, dict):
            capabilities = {}
            node_entry["capabilities"] = capabilities
        software = capabilities.get("software")
        if not isinstance(software, list):
            software = []
            capabilities["software"] = software
        if name not in software:
            software.append(name)


def make_remote_symlinks(
    pool: SSHConnectionPool, node: RemoteNode, symlinks: dict[str, str]
) -> None:
    """Create ``~/bin`` symlinks with the same command bootstrap uses.

    Per entry one combined ``mkdir -p ~/bin && ln -sf <path> ~/bin/<name>``
    execute (mirrors ``NodeManager.bootstrap_node`` node_manager.py:550).
    Individual failures are logged and skipped, matching the bootstrap
    precedent; whether to call this at all is the flow's decision (T7/T8).
    """
    for name, target in symlinks.items():
        command = f"mkdir -p ~/bin && ln -sf {target} ~/bin/{shlex.quote(name)}"
        try:
            code, _out, err = pool.execute(node, command, timeout=30)
        except Exception as exc:  # noqa: BLE001 — one bad symlink must not kill the loop
            logger.error("Symlink %r on %s failed: %s", name, node.name, exc)
            continue
        if code == 0:
            logger.info("Symlinked %s -> %s on %s", name, target, node.name)
        else:
            logger.error(
                "Symlink %r on %s failed (exit=%s): %s", name, node.name, code, err.strip()
            )
