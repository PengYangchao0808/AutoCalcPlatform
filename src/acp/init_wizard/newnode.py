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

from acp.init_wizard.prompts import ask, ask_local_path, ask_remote_dir, ask_secret, menu

logger = logging.getLogger(__name__)

__all__ = [
    "BootstrapOutcome",
    "ConnectedAuth",
    "ConnectResult",
    "PromptBundle",
    "TofuCapture",
    "connect_once",
    "run_bootstrap_stage",
    "run_connect_stage",
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
