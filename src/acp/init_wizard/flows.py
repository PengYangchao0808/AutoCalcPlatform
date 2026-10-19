"""Resource menu + flow orchestration for the `acp init` wizard (plan T8).

``run_init`` owns the repeat loop: a resource menu (本地 / 已声明的计算节点 /
声明新集群 / q) rebuilt from the RAW target YAML after every completed flow.
The menu reads plain ``cluster.nodes[]`` dicts through the **shared pure**
node validator (``acp_services.contracts.config_validation``) — the same rule
set ``RemoteNode.from_config_dict`` enforces — so broken entries render
``name (配置无效: <原因>)`` and are unselectable, disabled entries render
``(已禁用)`` and are refused with the reason (D16c/D16d).  The pure module is
stdlib-only, so it adds no paramiko (or ``acp``) dependency to this path.

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

from acp.core.paths import check_run_root_safety, resolve_run_root
from acp.init_wizard.answers import load_answers, plan_from_answers
from acp.init_wizard.capabilities import (
    collect_capability_report,
    render_capability_report,
)
from acp.init_wizard.newnode import (
    PromptBundle,
    _validate_remote_path,
    run_new_node,
)
from acp.init_wizard.options import (
    COMPONENT_SPECS,
    TIER_SPECS,
    ComponentSpec,
    TierSpec,
    TierVerification,
    UsageMode,
    default_component_selection,
)
from acp.init_wizard.persist import InitAbort, load_target, save_target, set_software_path
from acp.init_wizard.plan import (
    InitPlan,
    SecretRef,
    apply_plan,
    build_plan,
    make_secret_ref,
    render_plan,
)
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
from acp_services.contracts.config_validation import node_entry_error

logger = logging.getLogger(__name__)

__all__ = ["run_init"]


def _entry_name(entry: Any) -> str:
    """Best-effort node name for messages (works for broken entries too)."""
    if isinstance(entry, dict):
        return str(entry.get("name", "<未命名>"))
    return "<未命名>"


def _node_label(entry: Any) -> str:
    """Menu label for one raw node entry (D16c/D16d decorations)."""
    error = node_entry_error(entry)
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
        error = node_entry_error(entry)
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
    # A unique bounded-scan hit is safe to pin now.  Persisting the absolute
    # path makes later service starts independent of their shell PATH.
    scanned = {
        name: str(entry.resolved)
        for name, entry in entries.items()
        if entry.source == "scan" and entry.resolved is not None
    }
    if scanned:
        entries = apply_local_spec(config_path, target_data, scanned)
        print("已固定自动发现的软件路径：" + ", ".join(sorted(scanned)))
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
    paths, and immediate ``~/bin`` symlinks are optional. MPI is stored as
    the ORCA runtime path plus an ``mpirun`` symlink, not as a backend
    capability. A re-sniff (D10) confirms every specified path resolves.

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
    for sw_name, sw_path in specs.items():
        set_software_path(remote_data, sw_name, sw_path)
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


def run_init(
    config_path: Path,
    *,
    answers_path: Path | None = None,
    plan_only: bool = False,
    prompts: PromptBundle | None = None,
) -> int:
    """``acp init`` entrypoint — unified step flow (B07-2, plan todo 36).

    Interactive order (§36.2): usage (local / connect existing service) → tier
    (Standalone / Lab / Hosted; an unverified tier cannot be enabled) → existing
    resources → components (payment / object-store / external IAM off by
    default) → params & secret *references* → preview & apply → doctor
    capability report.  With ``answers_path`` the SAME plan builder runs
    non-interactively from a versioned answer file (no prompts).

    Args:
        config_path: Target user config file to create/update (write target,
            NOT a merge source like ``run --config``).
        answers_path: Optional answer file for the non-interactive path.
        plan_only: Render the plan but do not apply it (``acp init --plan``).
        prompts: Prompt seam (tests inject a scripted bundle); defaults to the
            real TTY prompts.

    Returns:
        0 — the wizard completed (or the user quit); 1 — fatal error, an answer
        file refusal, or a refused tier in non-interactive mode.
    """
    prompt_bundle = prompts or PromptBundle()
    try:
        target_data = load_target(config_path)
    except InitAbort as exc:
        print(f"错误：{exc}")
        return 1
    if answers_path is not None:
        return _run_answer_file(config_path, target_data, Path(answers_path))
    print(f"ACP 初始化向导 — 配置文件：{config_path}")
    try:
        return _run_unified_loop(config_path, target_data, prompt_bundle, plan_only=plan_only)
    except WizardAborted:
        print("\n已中止（已保存的修改保持不变）")
        return 0
    except InitAbort as exc:
        print(f"\n错误：{exc}")
        return 1


# --------------------------------------------------------------------------- #
# Unified step flow (usage → tier → resources → components → params/secrets
#                    → preview/apply → doctor)
# --------------------------------------------------------------------------- #

_USAGE_OPTIONS: tuple[str, ...] = (
    "初始化本机部署（本地软件与计算节点）",
    "连接已有 ACP 服务",
)


def _usage_menu(prompts: PromptBundle) -> UsageMode | None:
    """Step 1: local deployment vs connecting an existing service."""
    choice = prompts.menu("选择使用方式", list(_USAGE_OPTIONS), allow_q=True)
    if choice == 0:
        return None
    return UsageMode.CONNECT if choice == 2 else UsageMode.LOCAL


def _tier_option_label(spec: TierSpec) -> str:
    if spec.verification is TierVerification.VERIFIED:
        return spec.label
    return f"{spec.label}［能力未验证，不可启用］"


def _select_tier(prompts: PromptBundle) -> TierSpec | None:
    """Step 2: pick a deployment tier; unverified tiers are refused on selection."""
    options = [_tier_option_label(spec) for spec in TIER_SPECS]
    while True:
        choice = prompts.menu("选择部署档位", options, allow_q=True)
        if choice == 0:
            return None
        spec = TIER_SPECS[choice - 1]
        if spec.verification is not TierVerification.VERIFIED:
            print(f"档位 {spec.label} 能力尚未验证，不可启用；请选择已验证档位")
            continue
        return spec


def _run_resources_step(
    config_path: Path,
    target_data: dict[str, Any],
    prompts: PromptBundle,
    tier_spec: TierSpec,
) -> dict[str, Any]:
    """Step 3: existing resources — reuse the resource menu, then extra refs.

    Reuses :func:`_resource_menu` and the existing local/existing-node/new-node
    flows verbatim.  ``q`` finishes this step (the usage menu owns full quit).
    """
    print("\n== 已有资源 ==")
    print("（在资源菜单按 q 完成资源选择并继续）")
    while True:
        action = _resource_menu(prompts, target_data)
        if action is None:
            break
        kind, payload = action
        if kind == "local":
            _run_local_flow(config_path, target_data, prompts)
        elif kind == "node":
            _run_existing_node_flow(config_path, target_data, payload, prompts)
        else:
            _run_new_node_flow(config_path, target_data, prompts)
        print("\n（返回资源菜单）")

    external: dict[str, Any] = {}
    for name in tier_spec.required_resources:
        raw = prompts.ask(
            f"提供 {name} 引用（如 env:ACP_DB_URL / file:/path；直接回车跳过）",
            allow_empty=True,
        )
        if raw:
            external[name] = {"ref": raw}
    return {"external": external} if external else {}


def _optional_components() -> list[ComponentSpec]:
    return [spec for spec in COMPONENT_SPECS if not spec.default_enabled]


def _components_step(prompts: PromptBundle) -> tuple[str, ...]:
    """Step 4: component combination (payment/object-store/external IAM off)."""
    selected = set(default_component_selection())
    optional = _optional_components()
    print("\n== 组件组合 ==")
    print("默认不启用：支付、对象存储、外部 IAM（需显式选择）。")
    for index, spec in enumerate(optional, start=1):
        deps = f"（依赖：{'、'.join(spec.requires)}）" if spec.requires else ""
        print(f"  {index}) {spec.label}{deps}")
    raw = prompts.ask("额外启用组件编号（逗号分隔，直接回车使用默认组合）", allow_empty=True)
    for token in raw.replace("，", ",").split(","):
        token = token.strip()
        if not token:
            continue
        if not token.isdigit() or not (1 <= int(token) <= len(optional)):
            print(f"忽略无效组件编号：{token}")
            continue
        selected.add(optional[int(token) - 1].name)
    return tuple(sorted(selected))


def _parse_secret_refs(text: str) -> list[SecretRef]:
    """Parse ``name=source:ref`` entries; a raw value is refused (never stored)."""
    refs: list[SecretRef] = []
    for item in text.replace("，", ",").split(","):
        item = item.strip()
        if not item:
            continue
        name, sep, reference = item.partition("=")
        if not sep:
            raise InitAbort(f"secret 引用格式应为 name=source:ref（{item!r}）")
        refs.append(make_secret_ref(name, reference))
    return refs


def _params_secrets_step(prompts: PromptBundle) -> tuple[dict[str, Any], tuple[SecretRef, ...]]:
    """Step 5: parameters (run_root authority) and secret *references* only."""
    print("\n== 参数与秘密 ==")
    resolved = resolve_run_root()
    print(f"当前解析的 run_root（原生文件系统，安装目录与数据目录分离）：{resolved}")
    for warning in check_run_root_safety(resolved):
        print(f"  [警告] {warning}")
    raw_root = prompts.ask("原生数据根 run_root", default=str(resolved))
    params: dict[str, Any] = {"run_root": raw_root}
    raw_secret = prompts.ask(
        "如需凭据，输入 secret 引用（如 db_password=env:ACP_DB_PASSWORD；直接回车跳过）",
        allow_empty=True,
    )
    return params, tuple(_parse_secret_refs(raw_secret))


def _preview_and_apply(prompts: PromptBundle, plan: InitPlan, *, plan_only: bool) -> bool:
    """Step 6: show the plan (diff/source/rollback) and apply it."""
    print("\n== 初始化计划预览 ==")
    print(render_plan(plan))
    if not plan.enabled:
        print(f"\n档位启用被拒绝：{plan.enablement.detail}")
        return False
    if plan_only:
        print("\n（--plan：仅预览，未写入配置）")
        return False
    if prompts.menu("应用该计划？", ["应用", "取消"], allow_q=False) != 1:
        print("已取消，未写入配置")
        return False
    result = apply_plan(plan)
    print(result.detail)
    return True


def _print_doctor(plan: InitPlan) -> bool:
    """Step 7: three-state doctor capability report; returns overall ok."""
    report = collect_capability_report(plan)
    print("\n== 验证（doctor） ==")
    print(render_capability_report(report))
    return report.ok


def _run_local_unified(
    config_path: Path,
    target_data: dict[str, Any],
    prompts: PromptBundle,
    *,
    plan_only: bool,
) -> None:
    tier_spec = _select_tier(prompts)
    if tier_spec is None:
        return
    resources = _run_resources_step(config_path, target_data, prompts, tier_spec)
    components = _components_step(prompts)
    params, secrets = _params_secrets_step(prompts)
    plan = build_plan(
        config_path,
        target_data,
        usage=UsageMode.LOCAL.value,
        tier=tier_spec.tier,
        components=components,
        resources=resources,
        params=params,
        secrets=secrets,
    )
    if not _preview_and_apply(prompts, plan, plan_only=plan_only):
        return
    _print_doctor(plan)


def _run_connect_flow(
    config_path: Path,
    target_data: dict[str, Any],
    prompts: PromptBundle,
    *,
    plan_only: bool,
) -> None:
    """Connect-existing-service step: service address/project/credential only."""
    print("\n== 连接已有 ACP 服务 ==")
    print("仅配置服务地址、项目与凭据引用；不创建本地服务器/数据库/集群。")
    url = prompts.ask("服务地址（如 https://acp.example.org）")
    project = prompts.ask("项目标识（直接回车跳过）", allow_empty=True)
    credential_ref = prompts.ask("凭据引用（如 env:ACP_TOKEN；直接回车跳过）", allow_empty=True)
    plan = build_plan(
        config_path,
        target_data,
        usage=UsageMode.CONNECT.value,
        tier=None,
        components=(),
        resources={"service": {"url": url, "project": project, "credential_ref": credential_ref}},
        params={},
        secrets=(),
    )
    if not _preview_and_apply(prompts, plan, plan_only=plan_only):
        return
    _print_doctor(plan)


def _run_unified_loop(
    config_path: Path,
    target_data: dict[str, Any],
    prompts: PromptBundle,
    *,
    plan_only: bool,
) -> int:
    while True:
        usage = _usage_menu(prompts)
        if usage is None:
            print("再见")
            return 0
        if usage is UsageMode.CONNECT:
            _run_connect_flow(config_path, target_data, prompts, plan_only=plan_only)
        else:
            _run_local_unified(config_path, target_data, prompts, plan_only=plan_only)
        print("\n（返回使用方式菜单）")


def _run_answer_file(config_path: Path, target_data: dict[str, Any], answers_path: Path) -> int:
    """Non-interactive path: answer file → same plan builder → apply → doctor."""
    try:
        answers = load_answers(answers_path)
        plan = plan_from_answers(config_path, target_data, answers)
    except InitAbort as exc:
        print(f"错误：{exc}")
        return 1
    print(f"ACP 初始化（非交互回答文件：{answers_path}）")
    print(render_plan(plan))
    if not plan.enabled:
        print(f"\n档位启用被拒绝：{plan.enablement.detail}")
        return 1
    try:
        result = apply_plan(plan)
    except InitAbort as exc:
        print(f"错误：{exc}")
        return 1
    print(result.detail)
    return 0 if _print_doctor(plan) else 1
