"""Resource menu + flow orchestration for the `acp init` wizard (plan T8).

``run_init`` owns the repeat loop: a resource menu (本地 / 已声明的计算节点 /
声明新集群 / q) rebuilt from the RAW target YAML after every completed flow.
The menu reads plain ``cluster.nodes[]`` dicts through a LOCAL display
validator mirroring every raising check of
``RemoteNode.from_config_dict`` (required keys, remote_work_dir space-free,
``int(port)``/``int(max_concurrent_jobs)`` coercion) — broken entries render
``name (配置无效: <原因>)`` and are unselectable, disabled entries render
``(已禁用)`` and are refused with the reason (D16c/D16d).

Import discipline (D7, round-1 O1): ZERO ``acp.scheduler.remote`` imports at
module level — the scheduler remote package eagerly imports paramiko.  The
existing-node flow guards paramiko PER-FLOW and imports the remote stack
function-locally; the menu and the local flow run on a core-only install.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from acp.init_wizard.newnode import (
    PromptBundle,
    _validate_remote_path,
    run_new_node,
)
from acp.init_wizard.persist import InitAbort, load_target, save_target
from acp.init_wizard.prompts import WizardAborted
from acp.init_wizard.sniff_local import (
    apply_local_spec,
    manual_spec_local,
    render_sniff_table,
    sniff_local,
)
from acp.init_wizard.sniff_remote import (
    apply_remote_manual_spec,
    make_remote_symlinks,
    read_remote_config,
    remote_home,
    sniff_remote,
    write_remote_config,
)

logger = logging.getLogger(__name__)

__all__ = ["run_init"]

#: Required keys, mirroring ``RemoteNode.from_config_dict`` (config.py:170) —
#: the ONLY raising checks there are these, the remote_work_dir space rule,
#: and ``int()`` coercion of port / max_concurrent_jobs.
_REQUIRED_NODE_KEYS: tuple[str, ...] = (
    "name",
    "host",
    "username",
    "remote_work_dir",
    "remote_code_dir",
)


# --------------------------------------------------------------------------- #
# Local display validator over RAW node dicts (no scheduler.remote import)
# --------------------------------------------------------------------------- #


def _node_entry_error(entry: Any) -> str | None:
    """Return the reason *entry* would fail ``RemoteNode.from_config_dict``.

    Mirrors EVERY raising check (config.py:158-167/:176/:181); everything
    else in ``from_config_dict`` degrades gracefully and never raises.
    """
    if not isinstance(entry, dict):
        return "节点条目必须是映射"
    for key in _REQUIRED_NODE_KEYS:
        if key not in entry:
            return f"缺少必需字段 {key!r}"
    work_dir = str(entry["remote_work_dir"])
    if " " in work_dir:
        return f"remote_work_dir 不能包含空格（{work_dir!r}）"
    for key in ("port", "max_concurrent_jobs"):
        if key in entry:
            try:
                int(entry[key])
            except (TypeError, ValueError):
                return f"{key} 不是有效整数（{entry[key]!r}）"
    return None


def _entry_name(entry: Any) -> str:
    """Best-effort node name for messages (works for broken entries too)."""
    if isinstance(entry, dict):
        return str(entry.get("name", "<未命名>"))
    return "<未命名>"


def _node_label(entry: Any) -> str:
    """Menu label for one raw node entry (D16c/D16d decorations)."""
    error = _node_entry_error(entry)
    name = _entry_name(entry)
    if error is not None:
        return f"{name} (配置无效: {error})"
    if isinstance(entry, dict) and not entry.get("enabled", True):
        return f"{name} (已禁用)"
    return name


def _declared_node_entries(target_data: dict[str, Any]) -> list[Any]:
    """Raw ``cluster.nodes[]`` entries from the target dict."""
    cluster = target_data.get("cluster")
    nodes = cluster.get("nodes", []) if isinstance(cluster, dict) else []
    if not isinstance(nodes, list):
        logger.warning("cluster.nodes 不是列表，按空处理（%r）", type(nodes).__name__)
        return []
    return list(nodes)


# --------------------------------------------------------------------------- #
# Resource menu
# --------------------------------------------------------------------------- #


def _resource_menu(prompts: PromptBundle, target_data: dict[str, Any]) -> tuple[str, Any] | None:
    """Show the resource menu until a selectable action is chosen.

    Returns:
        ``("local", None)``, ``("node", entry)`` or ``("new", None)``;
        ``None`` when the user quits (``q``).
    """
    while True:
        entries = _declared_node_entries(target_data)
        options = ["本地", *(_node_label(entry) for entry in entries), "声明新集群"]
        choice = prompts.menu("选择要初始化的资源", options, allow_q=True)
        if choice == 0:
            return None
        if choice == 1:
            return ("local", None)
        if choice == len(options):
            return ("new", None)
        entry = entries[choice - 2]
        error = _node_entry_error(entry)
        if error is not None:
            # Broken entries are unselectable: show the reason, re-show menu.
            print(f"节点 {_entry_name(entry)} 配置无效：{error}，请先修正配置文件")
            continue
        if isinstance(entry, dict) and not entry.get("enabled", True):
            print(
                f"节点 {_entry_name(entry)} 已禁用（enabled: false），无法初始化；请先在配置中启用"
            )
            continue
        return ("node", entry)


# --------------------------------------------------------------------------- #
# Local flow (T4 machinery — no paramiko anywhere on this path)
# --------------------------------------------------------------------------- #


def _run_local_flow(config_path: Path, target_data: dict[str, Any], prompts: Any) -> None:
    """Sniff local QC software, prompt manual paths for the missing ones.

    ``prompts`` is annotated ``Any`` because the package keeps parallel,
    structurally-identical ``PromptBundle`` copies per module (T4/T6/T7
    convention) — sniff_local's copy is nominally distinct from newnode's.
    """
    print("\n== 本地计算软件检测 ==")
    entries = sniff_local(config_path)
    print(render_sniff_table(entries))
    missing = [name for name, entry in entries.items() if not entry.resolved]
    specs = manual_spec_local(missing, prompts)
    if specs:
        fresh = apply_local_spec(config_path, target_data, specs)
        print("已写入手动路径，重新检测结果：")
        print(render_sniff_table(fresh))


# --------------------------------------------------------------------------- #
# Existing-node flow (T5 machinery + D16a/b + D6 hint; paramiko-guarded)
# --------------------------------------------------------------------------- #


def _print_remote_report(node_name: str, report: dict[str, Any]) -> None:
    """Render the sniff result as a compact found/missing summary."""
    software = report.get("software") or {}
    found = [name for name, info in software.items() if (info or {}).get("resolved")]
    missing = [name for name, info in software.items() if not (info or {}).get("resolved")]
    print(
        f"远端软件嗅探（{node_name}）：已找到 {'、'.join(found) if found else '无'}；"
        f"未找到 {'、'.join(missing) if missing else '无'}"
    )
    if report.get("software_fallback"):
        print("（doctor 软件脚本探测失败，已自动改用 shell PATH 探测）")
    if report.get("error"):
        print(f"注意：{report['error']}")


def _existing_node_session(
    pool: Any, node: Any, target_data: dict[str, Any], prompts: PromptBundle
) -> bool:
    """One SSH session: sniff → manual spec → remote config write → re-sniff.

    Manual paths follow D9/D12: the ACP-side node entry gains
    ``executables``/``bin_symlinks``/``capabilities.software`` (in-memory,
    caller persists), the remote ``~/.cccp.yaml`` gains the executables
    paths, and immediate ``~/bin`` symlinks are optional.  A re-sniff (D10)
    confirms every specified path resolves.

    Returns:
        ``True`` when any spec was applied (caller then persists the target).
    """
    report = sniff_remote(pool, node)
    _print_remote_report(node.name, report)
    software = report.get("software") or {}
    missing = [name for name, info in software.items() if not (info or {}).get("resolved")]

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
        return False

    apply_remote_manual_spec(target_data, node.name, specs)
    home = remote_home(pool, node)
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
    if prompts.menu("是否立即在节点上创建 ~/bin 符号链接？", ["创建", "跳过"]) == 1:
        make_remote_symlinks(pool, node, specs)

    print("重新嗅探确认：")
    _print_remote_report(node.name, sniff_remote(pool, node))
    return True


def _run_existing_node_flow(
    config_path: Path,
    target_data: dict[str, Any],
    entry: dict[str, Any],
    prompts: PromptBundle,
) -> None:
    """Existing-node flow: guarded parse → credentials → sniff/spec session.

    Any failure returns control to the resource menu — this function never
    terminates the wizard (round-3 O-MINOR-3), not even when paramiko is
    missing (install hint instead, D7 per-flow guard).
    """
    try:
        import paramiko  # noqa: F401 — presence check only (per-flow guard, D7)
    except ImportError:
        print("远程节点流程需要 paramiko。请先安装后重试：pip install -e '.[remote]'")
        return

    # Defense-in-depth (round-2 O-MAJOR-1): the menu already validated the
    # raw entry, but no validator gap may ever traceback — a failure here
    # prints the reason and returns to the menu.
    try:
        from acp.scheduler.remote.config import RemoteNode

        node = RemoteNode.from_config_dict(entry)
    except (ValueError, TypeError) as exc:
        print(f"节点 {_entry_name(entry)} (配置无效: {exc})")
        return

    from acp.scheduler.remote.config import _env_var_name
    from acp.scheduler.remote.ssh import SSHConnectionPool

    # D16b credential-absent handling: no password field, no env var, no
    # key_file → offer a runtime password (never persisted).
    env_var = _env_var_name(node.name)
    original_password = node.password
    if node.password is None and node.key_file is None and not os.environ.get(env_var):
        print(f"节点 {node.name} 未配置凭据（无 password、无环境变量 {env_var}、无 key_file）")
        choice = prompts.menu(
            "如何提供本次连接密码？",
            ["输入密码（仅本次使用，不保存）", "返回菜单"],
            allow_q=False,
        )
        if choice != 1:
            return
        node.password = prompts.ask_secret("请输入密码")

    pool = SSHConnectionPool()
    try:
        while True:
            try:
                applied = _existing_node_session(pool, node, target_data, prompts)
                if applied:
                    save_target(config_path, target_data)
                    print(f"已将手动路径合并到 ACP 配置（{config_path}）")
                return
            except (InitAbort, WizardAborted):
                raise
            except Exception as exc:  # noqa: BLE001 — SSH failures get a menu, not a crash
                logger.debug("existing-node flow failed on %s: %s", node.name, exc)
                print(f"\n连接或嗅探失败：{exc}")
                if node.host_key_policy == "reject":
                    print(
                        "提示：若因未知主机被拒绝，可为该节点设置 host_key_policy: auto_add，"
                        "或预先将主机密钥登记到 ~/.ssh/known_hosts"
                    )
                action = prompts.menu(
                    "连接失败",
                    ["重试", "重新输入认证信息（仅本次不保存）", "返回菜单"],
                    allow_q=False,
                )
                if action == 2:
                    node.password = prompts.ask_secret("请输入密码")
                elif action != 1:
                    return
    finally:
        node.password = original_password  # runtime credential never persists
        pool.close()


# --------------------------------------------------------------------------- #
# New-node flow (T7 contract) + run_init repeat loop
# --------------------------------------------------------------------------- #


def _run_new_node_flow(
    config_path: Path, target_data: dict[str, Any], prompts: PromptBundle
) -> None:
    """Declare a new cluster node via :func:`run_new_node`; always returns."""
    result = run_new_node(prompts, config_path, target_data)
    if result.persisted and result.node_name:
        print(f"节点 {result.node_name} 已保存到 {config_path}")


def run_init(config_path: Path) -> int:
    """``acp init`` entrypoint: resource menu + repeat loop (D15/D18b).

    Args:
        config_path: Target user config file to create/update (write target,
            NOT a merge source like ``run --config``).

    Returns:
        0 — a flow completed, or the user quit (``q`` / EOF / Ctrl-C);
        1 — fatal error (unusable target file, unwritable target, ...).
    """
    prompts = PromptBundle()
    try:
        target_data = load_target(config_path)
    except InitAbort as exc:
        print(f"错误：{exc}")
        return 1
    print(f"ACP 初始化向导 — 配置文件：{config_path}")
    try:
        while True:
            action = _resource_menu(prompts, target_data)
            if action is None:
                print("再见")
                return 0
            kind, payload = action
            if kind == "local":
                _run_local_flow(config_path, target_data, prompts)
            elif kind == "node":
                _run_existing_node_flow(config_path, target_data, payload, prompts)
            else:
                _run_new_node_flow(config_path, target_data, prompts)
            print("\n（返回资源菜单）")
    except WizardAborted:
        print("\n已中止（已保存的修改保持不变）")
        return 0
    except InitAbort as exc:
        print(f"\n错误：{exc}")
        return 1
