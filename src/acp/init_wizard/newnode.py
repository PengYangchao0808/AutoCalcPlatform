"""New-Cluster Wizard — connection test + bootstrap stage.

Stage half of the new-node flow (plan T6): a wizard-local TOFU connect
test building its OWN ``paramiko.SSHClient`` (never the shared pool, D6),
the D11 3-attempt connect loop, and the ``NodeManager.bootstrap_node``
orchestration against an IN-MEMORY config (the node is not persisted
here — T7 owns persistence).

Import discipline (D7): no ``paramiko`` and no ``acp.scheduler.remote``
import at module level — the remote stack (and paramiko) is imported
inside the functions that need it, so this module imports cleanly on a
core-only install.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from acp.init_wizard.persist import (
    save_target,
    set_cluster_type,
    set_execution_mode_remote,
    upsert_node,
)
from acp.init_wizard.prompts import ask, ask_local_path, ask_remote_dir, ask_secret, menu
from acp.init_wizard.sniff_remote import (
    apply_remote_manual_spec,
    make_remote_symlinks,
    read_remote_config,
    remote_home,
    sniff_remote,
    write_remote_config,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BootstrapOutcome",
    "ConnectedAuth",
    "ConnectResult",
    "FlowResult",
    "PromptBundle",
    "TofuCapture",
    "connect_once",
    "run_bootstrap_stage",
    "run_connect_stage",
    "run_new_node",
]

#: D11 — the connect stage gives up (back to the parent menu) after this
#: many failed ``connect_once`` attempts.
_MAX_CONNECT_ATTEMPTS = 3

#: SSH connect timeout (seconds) for the wizard's one-shot test client.
_CONNECT_TIMEOUT = 15

#: Truncation point for bootstrap stderr tails surfaced to the user.
_STDERR_TAIL_CHARS = 2000

#: Error prefix produced by ``NodeManager.bootstrap_node`` when the python
#: interpreter probe fails (node_manager.py) — triggers the D18a path prompt.
_PY_PROBE_ERROR_PREFIX = "no usable Python interpreter"


# --------------------------------------------------------------------------- #
# Prompt seam (pinned contract — identical copies exist across the package)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PromptBundle:
    """Bundle of prompt helpers so stages are testable without a TTY."""

    menu: Callable[..., int] = menu
    ask: Callable[..., str] = ask
    ask_secret: Callable[[str], str] = ask_secret
    ask_local_path: Callable[[str], Path] = ask_local_path
    ask_remote_dir: Callable[[str, str], str] = ask_remote_dir


class _FingerprintedPolicy(Protocol):
    """Structural type for a host-key policy carrying a captured fingerprint."""

    fingerprint: str | None


# --------------------------------------------------------------------------- #
# Connection stage (T6 first half): TOFU client + D11 connect loop
# --------------------------------------------------------------------------- #


class TofuCapture:
    """Duck-typed host-key capture policy (NO paramiko subclass, D6).

    paramiko calls ``missing_host_key`` on any object providing it (no
    isinstance check), so no ``MissingHostKeyPolicy`` inheritance — and no
    module-level paramiko import — is required.  The raised RuntimeError
    propagates out of ``connect()`` where :func:`connect_once` maps it to
    the ``unknown-host`` result via the captured fingerprint.

    Fingerprint format: colon-separated lowercase hex pairs (OpenSSH
    visual style, e.g. ``de:ad:be:ef:...``).
    """

    def __init__(self) -> None:
        self.fingerprint: str | None = None

    def missing_host_key(self, client: object, hostname: str, key: Any) -> None:
        """Record the key fingerprint, then reject by raising."""
        digest = key.get_fingerprint()
        self.fingerprint = ":".join(f"{byte:02x}" for byte in digest)
        logger.debug("TOFU unknown host %s, fingerprint=%s", hostname, self.fingerprint)
        raise RuntimeError("TOFU-unknown-host")


@dataclass(frozen=True)
class ConnectResult:
    """Outcome of one :func:`connect_once` probe.

    Attributes:
        ok: Whether the SSH connection succeeded.
        detail: Human-readable outcome (``"unknown-host"``, MITM abort
            text, or the raw error text for auth/network failures).
        fingerprint: Captured host-key fingerprint for the unknown-host
            case; ``None`` otherwise.
        policy_to_persist: ``"auto_add"`` only when the caller explicitly
            passed ``policy="auto_add"`` and the connect succeeded.
    """

    ok: bool
    detail: str
    fingerprint: str | None = None
    policy_to_persist: str | None = None


@dataclass(frozen=True)
class ConnectedAuth:
    """Connection parameters validated by a successful :func:`connect_once`."""

    host: str
    port: int
    username: str
    password: str | None = None
    key_file: str | None = None
    host_key_policy: str = "reject"


def connect_once(
    host: str,
    port: int,
    username: str,
    password: str | None = None,
    key_file: str | None = None,
    policy: str | _FingerprintedPolicy | None = None,
) -> ConnectResult:
    """Run ONE wizard-local SSH connect probe (D6) — never via the pool.

    Builds its own ``paramiko.SSHClient``; ``load_system_host_keys()`` keeps
    known-host verification against ``~/.ssh/known_hosts``.  A changed key on
    a known host raises ``BadHostKeyException`` (mapped to a MITM abort — D6:
    no accept prompt is possible for a *changed* key), while a
    :class:`TofuCapture` policy surfaces unknown hosts as the
    ``unknown-host`` result with the captured fingerprint.

    Args:
        host: Remote hostname or address.
        port: SSH port.
        username: SSH login user.
        password: Password credential (omitted from connect kwargs when None;
            never logged).
        key_file: Private-key path (``expanduser``-ed before passing —
            paramiko does not expand ``~``).
        policy: ``None`` → RejectPolicy (strictest); ``"auto_add"`` →
            AutoAddPolicy (report ``policy_to_persist`` on success);
            otherwise a duck-typed policy object such as :class:`TofuCapture`.

    Returns:
        :class:`ConnectResult` describing the probe outcome.
    """
    import paramiko

    if policy is None or policy == "reject":
        resolved: Any = paramiko.RejectPolicy()
    elif policy == "auto_add":
        resolved = paramiko.AutoAddPolicy()
    else:
        resolved = policy
    policy_obj: _FingerprintedPolicy | None = None if isinstance(policy, str) else policy

    connect_kwargs: dict[str, Any] = {
        "hostname": host,
        "port": port,
        "username": username,
        "timeout": _CONNECT_TIMEOUT,
    }
    if password:
        connect_kwargs["password"] = password
    if key_file:
        connect_kwargs["key_filename"] = str(Path(key_file).expanduser())

    auth_mode = "password" if password else ("key" if key_file else "none")
    logger.debug("wizard connect probe to %s@%s:%s auth=%s", username, host, port, auth_mode)

    client = paramiko.SSHClient()
    try:
        client.load_system_host_keys()
        client.set_missing_host_key_policy(resolved)
        client.connect(**connect_kwargs)
    except paramiko.BadHostKeyException as exc:
        # D6: a CHANGED key on a known host can never be accepted here —
        # abort with explicit MITM guidance instead of prompting.
        detail = (
            f"主机密钥已变更，可能存在中间人（MITM）攻击：{exc}. "
            "请在 ~/.ssh/known_hosts 中核对该主机的条目后再试"
        )
        logger.error("wizard connect MITM abort for %s: %s", host, exc)
        return ConnectResult(ok=False, detail=detail)
    except Exception as exc:
        fingerprint = getattr(policy_obj, "fingerprint", None)
        if isinstance(fingerprint, str) and fingerprint:
            return ConnectResult(ok=False, detail="unknown-host", fingerprint=fingerprint)
        detail = str(exc) or exc.__class__.__name__
        logger.debug("wizard connect probe to %s failed: %s", host, detail)
        return ConnectResult(ok=False, detail=detail)
    finally:
        client.close()

    return ConnectResult(
        ok=True,
        detail=f"connected to {host}:{port}",
        policy_to_persist="auto_add" if policy == "auto_add" else None,
    )


def _validate_port(raw: str) -> str | None:
    """Prompt validator: 1-65535 decimal port."""
    if raw.isdigit() and 0 < int(raw) <= 65535:
        return None
    return "端口必须是 1-65535 之间的数字"


def _opt_str(value: Any) -> str | None:
    """Normalize a start-value into a stripped non-empty string or None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_int(value: Any) -> int | None:
    """Normalize a start-value into an int or None (unparseable → prompt)."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ask_auth(prompts: PromptBundle) -> tuple[str | None, str | None] | None:
    """Ask for one credential (D5/D14b): password via getpass or key path.

    Returns:
        ``(password, key_file)`` — exactly one is set; ``None`` when the
        user quits the menu.
    """
    choice = prompts.menu("认证方式", ["密码", "SSH 私钥路径"], allow_q=True)
    if choice == 0:
        return None
    if choice == 1:
        return prompts.ask_secret("请输入密码"), None
    raw = prompts.ask("请输入私钥路径")
    return None, str(Path(raw).expanduser())


def run_connect_stage(prompts: PromptBundle, start_values: dict[str, Any]) -> ConnectedAuth | None:
    """Interactive connect-test loop (D11) — up to 3 attempts total.

    Prompts for any connection parameter missing from *start_values*, asks
    for one credential, probes with :func:`connect_once`, handles the TOFU
    unknown-host accept/retry branch, and re-offers 重试 / 重新输入认证信息 /
    返回上级菜单 on ordinary failures.  A MITM (changed host key) aborts
    immediately — no accept prompt exists for a changed key (D6).

    Args:
        prompts: Prompt helper bundle.
        start_values: Optional pre-filled ``host`` / ``port`` / ``username``
            / ``password`` / ``key_file``; any key may be absent.

    Returns:
        The validated :class:`ConnectedAuth`, or ``None`` to return to the
        parent menu (user abort or exhausted attempts).
    """
    host = _opt_str(start_values.get("host"))
    port = _opt_int(start_values.get("port"))
    username = _opt_str(start_values.get("username"))
    password = _opt_str(start_values.get("password"))
    key_file = _opt_str(start_values.get("key_file"))

    # First probe runs under the duck-typed TOFU capture so unknown hosts
    # surface with a fingerprint (D6); after an explicit accept the mode
    # flips to auto_add for the retry and any later attempts.
    tofu = TofuCapture()
    policy_mode = "tofu"

    for attempt in range(1, _MAX_CONNECT_ATTEMPTS + 1):
        if host is None:
            host = prompts.ask("请输入节点主机地址")
        if port is None:
            port = int(prompts.ask("SSH 端口", default="22", validate=_validate_port))
        if username is None:
            username = prompts.ask("请输入 SSH 用户名")
        if password is None and key_file is None:
            creds = _ask_auth(prompts)
            if creds is None:
                return None
            password, key_file = creds

        auth_mode = "password" if password else ("key" if key_file else "none")
        logger.debug(
            "connect attempt %d/%d: %s@%s:%s auth=%s",
            attempt,
            _MAX_CONNECT_ATTEMPTS,
            username,
            host,
            port,
            auth_mode,
        )
        result = connect_once(
            host,
            port,
            username,
            password=password,
            key_file=key_file,
            policy=tofu if policy_mode == "tofu" else "auto_add",
        )

        if result.ok:
            return ConnectedAuth(
                host=host,
                port=port,
                username=username,
                password=password,
                key_file=key_file,
                host_key_policy=result.policy_to_persist or "reject",
            )

        if "MITM" in result.detail:
            print(f"连接已中止：{result.detail}")
            return None

        if result.detail == "unknown-host":
            print(f"未知主机 {host}，密钥指纹: {result.fingerprint}")
            choice = prompts.menu(
                "是否接受并记住该主机密钥？",
                ["接受并记住（警告：记住 = 该节点未来的密钥变更将被静默接受）", "中止"],
                allow_q=False,
            )
            if choice != 1:
                return None
            policy_mode = "auto_add"
            result = connect_once(
                host,
                port,
                username,
                password=password,
                key_file=key_file,
                policy="auto_add",
            )
            if result.ok:
                return ConnectedAuth(
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    key_file=key_file,
                    host_key_policy="auto_add",
                )

        print(f"连接失败：{result.detail}")
        if attempt >= _MAX_CONNECT_ATTEMPTS:
            print(f"连续 {_MAX_CONNECT_ATTEMPTS} 次连接失败，返回上级菜单")
            return None
        choice = prompts.menu(
            f"连接失败（第 {attempt}/{_MAX_CONNECT_ATTEMPTS} 次尝试）",
            ["重试", "重新输入认证信息", "返回上级菜单"],
            allow_q=False,
        )
        if choice == 2:
            password = None
            key_file = None
        elif choice != 1:
            return None
    return None


# --------------------------------------------------------------------------- #
# Bootstrap stage (T6 second half): NodeManager.bootstrap_node orchestration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BootstrapOutcome:
    """Wizard-facing projection of a bootstrap run.

    Attributes:
        ok: Whether the bootstrap (code sync + pip install) succeeded.
        python_executable: Interpreter reported by the manager (pass-through
            of ``BootstrapResult.python_executable``).
        error: Human-readable failure reason; ``None`` on success.
        stderr_tail: Tail of the pip stderr plus any sync errors.
    """

    ok: bool
    python_executable: str | None
    error: str | None
    stderr_tail: str = ""


def run_bootstrap_stage(pool: Any, node: Any, prompts: PromptBundle) -> BootstrapOutcome:
    """Bootstrap *node* via ``NodeManager.bootstrap_node`` (D18a menu).

    The node is NOT persisted here: the manager is constructed from an
    IN-MEMORY ``RemoteExecutionConfig(execution_mode="remote",
    nodes=[node])`` whose ``get_node(name)`` resolves the very same object,
    so interpreter-path retries can mutate ``node.python_executable`` in
    memory before T7 persists anything.

    Failure menu is EXACTLY 重试 bootstrap / 放弃（节点不保存）; on a
    python-probe failure the 重试 branch additionally offers a python-path
    prompt (empty = keep the built-in candidate list).

    Args:
        pool: Live SSH connection pool owned by the caller (T7 flow).
        node: In-memory :class:`~acp.scheduler.remote.config.RemoteNode`.
        prompts: Prompt helper bundle.

    Returns:
        :class:`BootstrapOutcome` — 放弃 returns ``ok=False`` immediately.
    """
    from acp.scheduler.remote.config import RemoteExecutionConfig
    from acp.scheduler.remote.node_manager import NodeManager

    config = RemoteExecutionConfig(execution_mode="remote", nodes=[node])
    manager = NodeManager(config, pool)

    while True:
        result = manager.bootstrap_node(node.name)
        tail_parts = [result.stderr[-_STDERR_TAIL_CHARS:].strip(), *result.sync_errors]
        tail = "\n".join(part for part in tail_parts if part)
        if result.ok:
            return BootstrapOutcome(
                ok=True,
                python_executable=result.python_executable or None,
                error=None,
                stderr_tail=tail,
            )

        error = result.error or f"bootstrap failed (exit={result.exit_code})"
        print(f"节点初始化失败：{error}")
        if tail:
            print(tail)
        choice = prompts.menu(
            "节点初始化失败",
            ["重试 bootstrap", "放弃（节点不保存）"],
            allow_q=False,
        )
        if choice != 1:
            return BootstrapOutcome(
                ok=False,
                python_executable=result.python_executable or None,
                error=error,
                stderr_tail=tail,
            )
        if error.startswith(_PY_PROBE_ERROR_PREFIX):
            answer = prompts.ask(
                "请输入节点上可用的 Python 路径（留空使用内置候选列表）",
                default="",
                allow_empty=True,
            )
            if answer:
                node.python_executable = answer
                logger.debug("bootstrap retry with python_executable=%s", answer)


# --------------------------------------------------------------------------- #
# T7 placeholder: the full new-cluster wizard flow (run_new_node) appends
# BELOW this separator — keep the connection/bootstrap sections above stable.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FlowResult:
    """Outcome of one :func:`run_new_node` invocation.

    Attributes:
        persisted: Whether the node entry was written to the target config.
        node_name: Name of the persisted node (``None`` when nothing was
            persisted).
        aborted_cleanly: ``True`` when the user quit mid-flow (menu ``q``,
            connect abort, bootstrap 放弃) — nothing was persisted and the
            caller (T8 resource menu) simply returns to the menu.
    """

    persisted: bool
    node_name: str | None
    aborted_cleanly: bool


def _validate_positive_int(raw: str) -> str | None:
    """Prompt validator: positive decimal integer (max_concurrent_jobs)."""
    if raw.isdigit() and int(raw) > 0:
        return None
    return "请输入正整数"


def _validate_remote_path(raw: str) -> str | None:
    """Prompt validator for remote absolute executable paths (D12)."""
    if " " in raw:
        return "路径不能包含空格"
    if not (raw.startswith("/") or raw.startswith("~/")):
        return "路径必须是绝对路径或以 ~/ 开头"
    return None


def _node_name_exists(target_data: dict[str, Any], name: str) -> bool:
    """Whether ``cluster.nodes[]`` already contains an entry named *name*."""
    cluster = target_data.get("cluster")
    nodes = cluster.get("nodes", []) if isinstance(cluster, dict) else []
    return any(isinstance(entry, dict) and entry.get("name") == name for entry in nodes)


def _ask_node_name(prompts: PromptBundle, target_data: dict[str, Any], host: str) -> str:
    """Ask the node name (default = host) with the D14d collision loop.

    A name colliding with an existing ``cluster.nodes[]`` entry triggers a
    confirm-overwrite menu whose default (换个名称) re-prompts; only an
    explicit 覆盖 choice keeps the colliding name.
    """
    name = prompts.ask("节点名称", default=host)
    while _node_name_exists(target_data, name):
        choice = prompts.menu(
            f"节点名称 {name} 已存在于配置中",
            ["换个名称（推荐）", "覆盖已有节点"],
            allow_q=False,
        )
        if choice == 2:
            break
        name = prompts.ask("节点名称", default=host)
    return name


def _password_decision(prompts: PromptBundle, node: Any) -> bool:
    """D5 password decision — default keeps the password OUT of the YAML.

    Opt-out (default, also on menu ``q``) prints the
    ``ACP_REMOTE_PASSWORD_<NAME>`` export instruction plus shell-profile
    advice (history hygiene).  Opt-in requires the confirm phrase: the
    user must RE-TYPE the password via ``ask_secret`` (getpass — never
    echoed, never logged); an exact match stores it, any mismatch falls
    back to the opt-out guidance.

    Returns:
        ``True`` only when the user opted in AND the confirm phrase matched.
    """
    if not node.password:
        return False
    choice = prompts.menu(
        "是否将密码写入配置文件？",
        ["否，使用环境变量（推荐）", "是，写入配置文件（0600 权限）"],
        allow_q=True,
    )
    if choice == 2:
        confirm = prompts.ask_secret("请再次输入密码以确认写入（两次一致才会写入）")
        if confirm == node.password:
            print("密码将以 0600 权限写入配置文件")
            return True
        print("两次输入不一致，密码不会写入配置文件")
    from acp.scheduler.remote.config import _env_var_name

    print("请在运行 ACP 的环境中设置以下环境变量提供密码：")
    print(f"export {_env_var_name(node.name)}={node.password}")
    print("建议将该 export 写入 shell 配置文件（如 ~/.bashrc），避免留在命令历史中")
    return False


def _sniff_and_specify(
    prompts: PromptBundle, pool: Any, node: Any, target_data: dict[str, Any]
) -> None:
    """Remote sniff + manual spec + remote-config merge (reuses T5).

    Renders a compact missing summary, prompts one absolute remote path per
    missing software (empty = skip; space-free / absolute validated per
    D12), applies the spec to the ACP-side target dict, offers immediate
    ``~/bin`` symlink creation, and — when anything was specified — merges
    the SAME executables paths into the REMOTE ``~/.cccp.yaml`` and writes
    it back (D9).  With no specs the remote config file is left untouched.
    """
    home = remote_home(pool, node)
    report = sniff_remote(pool, node)
    software = report.get("software") or {}
    missing = [sn for sn, info in software.items() if not (info or {}).get("resolved")]
    found = [sn for sn, info in software.items() if (info or {}).get("resolved")]
    print(
        f"远端软件嗅探：已找到 {'、'.join(found) if found else '无'}；"
        f"未找到 {'、'.join(missing) if missing else '无'}"
    )

    specs: dict[str, str] = {}
    for sw_name in missing:
        raw = prompts.ask(
            f"请输入 {sw_name} 在节点上的绝对路径（直接回车跳过）",
            validate=_validate_remote_path,
            allow_empty=True,
        )
        if raw:
            specs[sw_name] = raw
    if not specs:
        return

    apply_remote_manual_spec(target_data, node.name, specs)
    # Mirror the spec onto the in-memory node so the authoritative
    # to_config_dict serialization (upsert replaces the placeholder entry)
    # still carries bin_symlinks + capabilities.software (D9b).
    from acp.scheduler.remote.config import NodeCapabilities

    node.bin_symlinks.update(specs)
    existing = node.capabilities.software if node.capabilities is not None else ()
    tags = node.capabilities.tags if node.capabilities is not None else ()
    node.capabilities = NodeCapabilities(
        software=(*existing, *(sw for sw in specs if sw not in existing)),
        tags=tags,
    )
    if prompts.menu("是否立即在节点上创建 ~/bin 符号链接？", ["创建", "跳过"]) == 1:
        make_remote_symlinks(pool, node, specs)

    remote_data, remote_mode = read_remote_config(pool, node, home)
    executables = remote_data.get("executables")
    if not isinstance(executables, dict):
        executables = {}
        remote_data["executables"] = executables
    for sw_name, sw_path in specs.items():
        entry = executables.get(sw_name)
        if not isinstance(entry, dict):
            entry = {}
            executables[sw_name] = entry
        entry["path"] = sw_path
    write_remote_config(pool, node, home, remote_data, remote_mode)


def run_new_node(
    prompts: PromptBundle, target_path: Path, target_data: dict[str, Any]
) -> FlowResult:
    """Full new-cluster wizard flow (T7): declare → connect → bootstrap → persist.

    Orchestration order: 集群类型 menu → host → name (default host, D14d
    collision confirm) → port → username → auth menu (D14b expanduser) →
    :func:`run_connect_stage` → in-memory ``RemoteNode`` carrying the
    auth credentials + prompted dirs/max_concurrent_jobs/queue → ONE
    ``SSHConnectionPool`` (closed in ``finally`` — no exit path leaks it)
    reused for bootstrap, sniff and the remote config write → D14c python
    back-write → D5 password decision → password sanitization BEFORE
    serialization → ``RemoteNode.to_config_dict()`` + ``upsert_node`` +
    ``set_cluster_type`` (D2: only absent/"local") + D3 execution_mode
    confirm → ``save_target``.  ``cluster.enabled`` is never written
    (GAP-1).  The 添加另一个节点 loop is T8's job.

    Args:
        prompts: Prompt helper bundle (fakes injectable in tests).
        target_path: Wizard target YAML file path (for ``save_target``).
        target_data: Raw target mapping (mutated in place, then saved).

    Returns:
        :class:`FlowResult` describing the outcome.

    Raises:
        WizardAborted: On EOF/Ctrl-C (propagates per D15; pool still closed).
        InitAbort: On unrecoverable config errors (propagates; pool closed).
    """
    choice = prompts.menu("集群类型", ["LSF", "Openlava"])
    if choice == 0:
        return FlowResult(persisted=False, node_name=None, aborted_cleanly=True)
    node_type = "lsf" if choice == 1 else "openlava"

    host = prompts.ask("请输入节点主机地址")
    name = _ask_node_name(prompts, target_data, host)
    port = int(prompts.ask("SSH 端口", default="22", validate=_validate_port))
    username = prompts.ask("请输入 SSH 用户名")

    creds = _ask_auth(prompts)
    if creds is None:
        return FlowResult(persisted=False, node_name=None, aborted_cleanly=True)
    password, key_file = creds

    auth = run_connect_stage(
        prompts,
        {
            "host": host,
            "port": port,
            "username": username,
            "password": password,
            "key_file": key_file,
        },
    )
    if auth is None:
        return FlowResult(persisted=False, node_name=None, aborted_cleanly=True)

    # FUNCTION-LOCAL imports (D7): the scheduler.remote package eagerly
    # imports paramiko-dependent modules — never at module level here.
    from acp.scheduler.remote.config import RemoteNode
    from acp.scheduler.remote.ssh import SSHConnectionPool

    # Pool lifecycle + credential ordering (round-2 O-MINOR-7): the node is
    # built FIRST carrying the auth credentials; dirs/limits are prompted
    # next and set on it; the pool is created last and closed in finally.
    node = RemoteNode(
        name=name,
        host=auth.host,
        port=auth.port,
        username=auth.username,
        remote_work_dir="~/acp_jobs",
        remote_code_dir="~/acp_code",
        password=auth.password,
        key_file=auth.key_file,
        host_key_policy=auth.host_key_policy,
        type=node_type,
    )
    node.remote_code_dir = prompts.ask_remote_dir("远端代码目录", "~/acp_code")
    node.remote_work_dir = prompts.ask_remote_dir("远端任务目录", "~/acp_jobs")
    node.max_concurrent_jobs = int(
        prompts.ask("最大并发任务数", default="5", validate=_validate_positive_int)
    )
    node.queue = prompts.ask("LSF 队列名称", default="normal")

    pool = SSHConnectionPool()
    try:
        outcome = run_bootstrap_stage(pool, node, prompts)
        if not outcome.ok:
            # D18a: 放弃 persists NOTHING — no node, no cluster.* writes.
            return FlowResult(persisted=False, node_name=None, aborted_cleanly=True)
        if outcome.python_executable and outcome.python_executable != "python":
            node.python_executable = outcome.python_executable  # D14c back-write

        _sniff_and_specify(prompts, pool, node, target_data)

        store_password = _password_decision(prompts, node)
        # PASSWORD SANITIZATION (round-3 O-MAJOR) — before ANY serialization:
        # opt-out clears the credential FIRST so the pool-credentialed node
        # can never leak it into the YAML via to_config_dict.
        save_mode: int | None = None
        if store_password:
            save_mode = 0o600
        else:
            node.password = None
        upsert_node(target_data, node.to_config_dict())
        set_cluster_type(target_data, node_type)
        if prompts.menu("是否将该集群的执行模式设为 remote？", ["设为 remote", "保持 local"]) == 1:
            set_execution_mode_remote(target_data)
        save_target(target_path, target_data, mode=save_mode)
        return FlowResult(persisted=True, node_name=node.name, aborted_cleanly=False)
    finally:
        pool.close()
