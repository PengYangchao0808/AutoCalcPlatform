"""Doctor capability report for the unified ``acp init`` (B07-2, todo 36).

Produces the step-7 **three-state** capability report (§36.2: PASS / FAIL /
NOT_VERIFIED kept apart — "连接成功不等于科学任务/隔离/备份验收通过").  It reuses
the same authorities the ``acp doctor`` command uses:

* :func:`cccp.software.resolve_executable` for QC binaries (identical to
  ``cli._handle_doctor``'s resolution);
* :func:`acp.scheduler.remote.node_manager.doctor_node` for remote nodes
  (imported function-locally so a core-only install never needs paramiko);
* :func:`acp.core.paths.resolve_run_root` / ``check_run_root_safety`` for the
  data root.

Nothing here executes an external action: components that imply one are reported
NOT_VERIFIED, never PASS.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from acp.core.paths import check_run_root_safety, resolve_run_root
from acp.init_wizard.options import COMPONENT_BY_NAME
from acp.init_wizard.plan import InitPlan
from cccp.software import EXECUTABLES, resolve_executable

logger = logging.getLogger(__name__)

__all__ = [
    "CapabilityCheck",
    "CapabilityReport",
    "CheckState",
    "collect_capability_report",
    "render_capability_report",
]


class CheckState(str, Enum):
    """Three-state result — a skipped/probed-later check is NOT a pass."""

    PASS = "PASS"
    FAIL = "FAIL"
    NOT_VERIFIED = "NOT_VERIFIED"


@dataclass(frozen=True)
class CapabilityCheck:
    """One capability entry."""

    name: str
    state: CheckState
    detail: str = ""


@dataclass(frozen=True)
class CapabilityReport:
    """Ordered capability checks split by state."""

    checks: tuple[CapabilityCheck, ...]

    @property
    def ok(self) -> bool:
        """True when no check FAILed (NOT_VERIFIED does not fail the report)."""
        return all(check.state is not CheckState.FAIL for check in self.checks)

    def by_state(self, state: CheckState) -> tuple[CapabilityCheck, ...]:
        return tuple(check for check in self.checks if check.state is state)


def _configured_path(planned: Mapping[str, Any], name: str) -> str | None:
    executables = planned.get("executables")
    if not isinstance(executables, Mapping):
        return None
    entry = executables.get(name)
    if isinstance(entry, Mapping):
        path = entry.get("path")
        if path:
            return str(path)
    return None


def _software_checks(
    planned: Mapping[str, Any],
    resolver: Callable[[str, str | None], Path | None],
) -> list[CapabilityCheck]:
    checks: list[CapabilityCheck] = []
    for name in EXECUTABLES:
        configured = _configured_path(planned, name)
        resolved = resolver(name, configured)
        if resolved is not None and Path(resolved).is_file():
            checks.append(CapabilityCheck(f"software:{name}", CheckState.PASS, str(resolved)))
        elif configured is not None:
            checks.append(
                CapabilityCheck(
                    f"software:{name}",
                    CheckState.FAIL,
                    f"已配置路径但不可用：{configured}",
                )
            )
        else:
            checks.append(
                CapabilityCheck(
                    f"software:{name}",
                    CheckState.NOT_VERIFIED,
                    "未配置；配置完成但科学能力尚未验证",
                )
            )
    return checks


def _component_checks(plan: InitPlan) -> list[CapabilityCheck]:
    checks: list[CapabilityCheck] = []
    selected = set(plan.components)
    for name in plan.components:
        spec = COMPONENT_BY_NAME.get(name)
        if spec is None:
            checks.append(CapabilityCheck(f"component:{name}", CheckState.FAIL, "未知组件"))
            continue
        missing = [dep for dep in spec.requires if dep not in selected]
        if missing:
            checks.append(
                CapabilityCheck(
                    f"component:{name}",
                    CheckState.FAIL,
                    f"缺少依赖：{'、'.join(missing)}",
                )
            )
        elif spec.external_actions:
            checks.append(
                CapabilityCheck(
                    f"component:{name}",
                    CheckState.NOT_VERIFIED,
                    f"含外部动作，尚未执行：{'、'.join(spec.external_actions)}",
                )
            )
        else:
            checks.append(CapabilityCheck(f"component:{name}", CheckState.PASS, spec.label))
    return checks


def _run_root_check(
    params: Mapping[str, Any],
    run_root_resolver: Callable[[Any], Path],
) -> CapabilityCheck | None:
    raw = params.get("run_root")
    if raw in (None, ""):
        return None
    root = run_root_resolver(raw)
    warnings = check_run_root_safety(root)
    install_overlap = any("install tree" in warning for warning in warnings)
    if root.exists() and not root.is_dir():
        return CapabilityCheck("run_root", CheckState.FAIL, f"{root} 不是目录")
    if root.exists() and not os.access(root, os.W_OK):
        return CapabilityCheck("run_root", CheckState.FAIL, f"{root} 不可写")
    if install_overlap:
        return CapabilityCheck("run_root", CheckState.FAIL, "；".join(warnings))
    detail = "；".join(warnings) if warnings else "就绪"
    return CapabilityCheck("run_root", CheckState.PASS, f"{root} — {detail}")


def _tier_check(plan: InitPlan) -> CapabilityCheck | None:
    if plan.usage != "local" or plan.tier is None:
        return None
    if plan.enablement.enabled:
        return CapabilityCheck("deployment-tier", CheckState.PASS, str(plan.tier))
    return CapabilityCheck("deployment-tier", CheckState.FAIL, plan.enablement.detail)


def _remote_checks(planned: Mapping[str, Any]) -> list[CapabilityCheck]:
    cluster = planned.get("cluster")
    nodes = cluster.get("nodes") if isinstance(cluster, Mapping) else None
    if not isinstance(nodes, list) or not nodes:
        return []
    try:
        import paramiko  # noqa: F401 — presence check only (per-call guard)
    except ImportError:
        return [
            CapabilityCheck(
                "remote-doctor",
                CheckState.NOT_VERIFIED,
                "未安装 paramiko（pip install -e '.[remote]'），远端未探测",
            )
        ]
    from acp.scheduler.remote.config import RemoteNode
    from acp.scheduler.remote.node_manager import doctor_node
    from acp.scheduler.remote.ssh import SSHConnectionPool

    checks: list[CapabilityCheck] = []
    pool = SSHConnectionPool()
    try:
        for entry in nodes:
            try:
                node = RemoteNode.from_config_dict(entry)
            except (ValueError, TypeError) as exc:
                checks.append(CapabilityCheck("remote-doctor", CheckState.FAIL, str(exc)))
                continue
            report = doctor_node(pool, node)
            if not report.reachable:
                checks.append(
                    CapabilityCheck(
                        f"node:{node.name}", CheckState.FAIL, report.error or "unreachable"
                    )
                )
            elif report.python is None:
                checks.append(
                    CapabilityCheck(f"node:{node.name}", CheckState.FAIL, "无可用 Python 3.10+")
                )
            else:
                checks.append(
                    CapabilityCheck(
                        f"node:{node.name}",
                        CheckState.PASS,
                        f"python {report.python.version}",
                    )
                )
    finally:
        pool.close()
    return checks


def collect_capability_report(
    plan: InitPlan,
    *,
    resolver: Callable[[str, str | None], Path | None] | None = None,
    run_root_resolver: Callable[[Any], Path] | None = None,
    probe_remote: bool = False,
) -> CapabilityReport:
    """Collect the three-state capability report for *plan*.

    Args:
        plan: The initialization plan (applied or previewed).
        resolver: QC binary resolver seam (default:
            :func:`cccp.software.resolve_executable`).
        run_root_resolver: run-root resolver seam (default:
            :func:`acp.core.paths.resolve_run_root`).
        probe_remote: When True, probe configured remote nodes with
            ``doctor_node`` (network); otherwise a node list is NOT_VERIFIED.

    Returns:
        A :class:`CapabilityReport`.
    """
    resolve_binary = resolver or resolve_executable
    resolve_root = run_root_resolver or resolve_run_root

    checks: list[CapabilityCheck] = []
    tier_check = _tier_check(plan)
    if tier_check is not None:
        checks.append(tier_check)
    root_check = _run_root_check(plan.params, resolve_root)
    if root_check is not None:
        checks.append(root_check)
    checks.extend(_software_checks(plan.planned_data, resolve_binary))
    checks.extend(_component_checks(plan))
    for action in plan.external_actions:
        checks.append(
            CapabilityCheck(f"external-action:{action}", CheckState.NOT_VERIFIED, "尚未执行")
        )
    if probe_remote:
        checks.extend(_remote_checks(plan.planned_data))
    return CapabilityReport(tuple(checks))


def render_capability_report(report: CapabilityReport) -> str:
    """Render the report with PASS / FAIL / NOT_VERIFIED kept in separate sections."""
    lines = ["doctor 能力报告（PASS / FAIL / NOT_VERIFIED 分列）"]
    for state in (CheckState.PASS, CheckState.FAIL, CheckState.NOT_VERIFIED):
        entries: Sequence[CapabilityCheck] = report.by_state(state)
        lines.append(f"  {state.value}（{len(entries)}）：")
        if not entries:
            lines.append("    - （无）")
        for check in entries:
            detail = f" — {check.detail}" if check.detail else ""
            lines.append(f"    - {check.name}{detail}")
    return "\n".join(lines)
