"""Versioned initialization plan: build → preview → apply (B07-2, todo 36).

A plan is a *pure* description of the raw target YAML rewrite: what changes,
where they came from, which external actions the selection implies, and how to
roll back.  ``build_plan`` never touches disk; ``apply_plan`` verifies the plan
digest against the live file (concurrent-modification guard) and writes through
:func:`acp.init_wizard.persist.save_target` (atomic tmp + ``.bak``).

Secrets are modelled as :class:`SecretRef` — a ``source`` plus a *reference*
(``env:NAME`` / ``file:/path`` / ``provider:key``).  A raw value can never enter
a plan: :func:`make_secret_ref` rejects anything without a recognised scheme,
and only references are persisted and rendered (never logged).

This is the seam for todo 38 (versioned/digest/applied idempotency) and todo 39
(external-action authorisation): the plan already carries ``version``,
``digest``, ``changes``, ``external_actions`` and ``rollback``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acp.core.paths import normalize_configured_run_root
from acp.init_wizard.options import (
    COMPONENT_BY_NAME,
    TIER_BY_TIER,
    TierEnablement,
    TierSpec,
    UsageMode,
    component_closure,
    tier_enablement,
    unknown_components,
)
from acp.init_wizard.persist import (
    InitAbort,
    load_target,
    save_target,
    set_cluster_type,
    set_execution_mode_remote,
    set_software_path,
    upsert_node,
)
from acp_services.workspace.boundary import OriginPolicy

logger = logging.getLogger(__name__)

__all__ = [
    "PLAN_VERSION",
    "SECRET_SOURCES",
    "ApplyResult",
    "ConfigChange",
    "InitPlan",
    "SecretRef",
    "apply_plan",
    "build_plan",
    "build_planned_data",
    "digest_of",
    "make_secret_ref",
    "render_plan",
    "plan_to_dict",
]

#: Version of the plan object/answer schema (reported by ``render_plan``).
PLAN_VERSION = 1

#: Allowed secret reference schemes — the only place a secret may come from.
SECRET_SOURCES: tuple[str, ...] = ("env", "file", "provider")


@dataclass(frozen=True)
class SecretRef:
    """A reference to a secret — never the secret value itself."""

    name: str
    source: str
    ref: str

    def as_config(self) -> dict[str, str]:
        """Serialise for the target YAML (reference only)."""
        return {"source": self.source, "ref": self.ref}

    def as_public(self) -> dict[str, str]:
        """Serialise for plan output — identical to the reference form."""
        return {"name": self.name, "source": self.source, "ref": self.ref}


@dataclass(frozen=True)
class ConfigChange:
    """One dotted-path difference between the base and planned target."""

    path: str
    before: Any
    after: Any


@dataclass(frozen=True)
class InitPlan:
    """Immutable, versioned initialization plan."""

    version: int
    target_path: str
    usage: str
    tier: str | None
    components: tuple[str, ...]
    resources: Mapping[str, Any]
    params: Mapping[str, Any]
    secrets: tuple[SecretRef, ...]
    changes: tuple[ConfigChange, ...]
    external_actions: tuple[str, ...]
    rollback: tuple[str, ...]
    digest: str
    enablement: TierEnablement
    source: str
    planned_data: Mapping[str, Any] = field(repr=False)

    @property
    def enabled(self) -> bool:
        """Whether the selected tier (if any) may be applied."""
        return self.enablement.enabled


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of applying a plan."""

    applied: bool
    changed: bool
    detail: str


# --------------------------------------------------------------------------- #
# Secret reference construction (rejects raw values)
# --------------------------------------------------------------------------- #


def make_secret_ref(name: str, reference: str) -> SecretRef:
    """Parse ``source:ref`` into a :class:`SecretRef`, refusing raw values.

    Args:
        name: Secret identifier (e.g. ``db_password``).
        reference: ``env:NAME`` / ``file:/path`` / ``provider:key``.

    Returns:
        The parsed reference.

    Raises:
        InitAbort: When *name* or *reference* is malformed, or the scheme is
            not one of :data:`SECRET_SOURCES` — a bare value (``hunter2``) is
            refused so a plaintext secret can never enter a plan.
    """
    clean_name = (name or "").strip()
    if not clean_name:
        raise InitAbort("秘密必须命名（如 db_password=env:ACP_DB_PASSWORD）")
    text = (reference or "").strip()
    source, sep, ref = text.partition(":")
    if not sep or not ref.strip():
        raise InitAbort(
            f"秘密 {clean_name} 必须使用引用形式（env:NAME / file:/path / provider:key），"
            "禁止写入明文口令"
        )
    source = source.strip().lower()
    if source not in SECRET_SOURCES:
        # Never echo the raw reference: it may be a mistyped plaintext secret.
        raise InitAbort(
            f"秘密 {clean_name} 的来源不受支持（仅支持 {'/'.join(SECRET_SOURCES)}）；"
            "请使用引用形式，勿写入明文口令"
        )
    return SecretRef(name=clean_name, source=source, ref=ref.strip())


# --------------------------------------------------------------------------- #
# Digest + diff
# --------------------------------------------------------------------------- #


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest_of(data: Mapping[str, Any]) -> str:
    """Return a stable content digest of *data* (config concurrency guard)."""
    return hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()


def _diff(before: Any, after: Any, prefix: str) -> list[ConfigChange]:
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        changes: list[ConfigChange] = []
        for key in sorted({*before, *after}, key=str):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in before:
                changes.extend(_diff({}, after[key], path))
            elif key not in after:
                changes.extend(_diff(before[key], {}, path))
            else:
                changes.extend(_diff(before[key], after[key], path))
        return changes
    if before != after:
        return [ConfigChange(prefix, before, after)]
    return []


def diff_data(before: Mapping[str, Any], after: Mapping[str, Any]) -> tuple[ConfigChange, ...]:
    """Return the flattened dotted-path differences between two mappings."""
    return tuple(_diff(before, after, ""))


# --------------------------------------------------------------------------- #
# Planned target construction
# --------------------------------------------------------------------------- #


def _apply_software(data: dict[str, Any], software: Mapping[str, Any]) -> None:
    for name, path in software.items():
        if path:
            set_software_path(data, str(name), str(path))


def _apply_cluster(data: dict[str, Any], cluster: Mapping[str, Any]) -> None:
    if "type" in cluster and cluster["type"]:
        set_cluster_type(data, str(cluster["type"]))
    if cluster.get("execution_mode") == "remote":
        set_execution_mode_remote(data)
    nodes = cluster.get("nodes")
    if isinstance(nodes, list):
        for node in nodes:
            if isinstance(node, dict) and node.get("name"):
                upsert_node(data, node)


def build_planned_data(
    base: Mapping[str, Any],
    *,
    usage: str,
    tier: str | None,
    components: Iterable[str],
    resources: Mapping[str, Any],
    params: Mapping[str, Any],
    secrets: Iterable[SecretRef],
) -> dict[str, Any]:
    """Return a deep-copied target mapping with the deployment rewrite applied.

    Software paths and the cluster block are projected into their existing
    ``cccp.config`` schema homes (``executables.<name>.path`` / ``cluster.*``);
    external resource references, parameters and secret references are recorded
    under the additive ``deployment`` section.
    """
    data = copy.deepcopy(dict(base))
    software = resources.get("software") or {}
    cluster = resources.get("cluster") or {}
    if isinstance(software, Mapping):
        _apply_software(data, software)
    if isinstance(cluster, Mapping):
        _apply_cluster(data, cluster)

    deployment = data.get("deployment")
    if not isinstance(deployment, dict):
        deployment = {}
        data["deployment"] = deployment
    deployment["usage"] = usage

    if usage == UsageMode.CONNECT.value:
        # Connect mode configures ONLY the service address / project / credential
        # reference — it never creates a local server, database or cluster.
        service = resources.get("service") or {}
        deployment["service"] = {
            key: service[key]
            for key in ("url", "project", "credential_ref")
            if isinstance(service, Mapping) and service.get(key)
        }
        return data

    deployment["tier"] = tier
    deployment["components"] = list(components)
    external = resources.get("external") or {}
    if isinstance(external, Mapping) and external:
        deployment["resources"] = copy.deepcopy(dict(external))
    if params:
        existing_params = deployment.get("params")
        merged_params = dict(existing_params) if isinstance(existing_params, Mapping) else {}
        merged_params.update(copy.deepcopy(dict(params)))
        deployment["params"] = merged_params
    secret_list = list(secrets)
    if secret_list:
        deployment["secrets"] = {secret.name: secret.as_config() for secret in secret_list}
    return data


# --------------------------------------------------------------------------- #
# Build / render / apply
# --------------------------------------------------------------------------- #

_rollback_hint = (
    "恢复目标文件的上一次 .bak-<时间戳> 备份",
    "删除新增的 deployment 段（若不需要）",
    "跨系统资源（数据库/对象存储/系统服务）需手工撤销，本计划不承诺全原子回滚",
)


def _resolve_tier(tier: object) -> TierSpec:
    spec = TIER_BY_TIER.get(str(tier).strip().lower()) if tier is not None else None
    if spec is None:
        raise InitAbort(f"未知部署档位：{tier!r}（应为 local-single/lab/hosted）")
    return spec


def build_plan(
    target_path: Path,
    base: Mapping[str, Any],
    *,
    usage: str,
    tier: str | None = None,
    components: Iterable[str] = (),
    resources: Mapping[str, Any] | None = None,
    params: Mapping[str, Any] | None = None,
    secrets: Iterable[SecretRef] = (),
    source: str | None = None,
) -> InitPlan:
    """Build a versioned :class:`InitPlan` (no disk writes).

    Args:
        target_path: Raw target YAML the plan will rewrite.
        base: Current raw target mapping (the digest base).
        usage: :class:`~acp.init_wizard.options.UsageMode` value.
        tier: Deployment tier value for local usage (``None`` for connect).
        components: Selected component names (closure is expanded here).
        resources: Resource mapping (software/cluster/service/external).
        params: Parameter mapping (e.g. ``run_root``).
        secrets: Secret references only.
        source: Config source label (defaults to *target_path*).

    Returns:
        The plan object.

    Raises:
        InitAbort: On unknown components, conflicts, or an unknown tier value.
    """
    usage_value = str(usage)
    resources_map: dict[str, Any] = dict(resources or {})
    params_map = dict(params or {})
    if "run_root" in params_map:
        try:
            params_map["run_root"] = str(normalize_configured_run_root(params_map["run_root"]))
        except ValueError as exc:
            raise InitAbort(str(exc)) from exc
    if "public_origin" in params_map:
        public_origin = params_map["public_origin"]
        if not isinstance(public_origin, str):
            raise InitAbort("params.public_origin 必须是字符串；直接访问时使用空字符串")
        public_origin = public_origin.strip()
        try:
            OriginPolicy(public_origin=public_origin or None)
        except ValueError as exc:
            raise InitAbort(f"params.public_origin 无效：{exc}") from exc
        params_map["public_origin"] = public_origin
    selected = component_closure(components)
    unknown = unknown_components(components)
    if unknown:
        raise InitAbort(f"未知组件：{'、'.join(unknown)}")
    conflicts = sorted(
        {
            name
            for name in selected
            if name in COMPONENT_BY_NAME
            for conflict in COMPONENT_BY_NAME[name].conflicts
            if conflict in selected
        }
    )
    if conflicts:
        raise InitAbort(f"组件冲突：{'、'.join(conflicts)}")

    if usage_value == UsageMode.CONNECT.value:
        enablement = TierEnablement(True, "")
        tier_value: str | None = None
    else:
        spec = _resolve_tier(tier)
        tier_value = spec.tier
        external = resources_map.get("external") or {}
        provided_resources = set(external) if isinstance(external, Mapping) else set()
        enablement = tier_enablement(spec, selected, provided_resources)

    planned = build_planned_data(
        base,
        usage=usage_value,
        tier=tier_value,
        components=selected,
        resources=resources_map,
        params=params_map,
        secrets=secrets,
    )
    external_actions = tuple(
        sorted(
            {
                action
                for name in selected
                if name in COMPONENT_BY_NAME
                for action in COMPONENT_BY_NAME[name].external_actions
            }
        )
    )
    return InitPlan(
        version=PLAN_VERSION,
        target_path=str(target_path),
        usage=usage_value,
        tier=tier_value,
        components=selected,
        resources=resources_map,
        params=params_map,
        secrets=tuple(secrets),
        changes=diff_data(base, planned),
        external_actions=external_actions,
        rollback=_rollback_hint,
        digest=digest_of(base),
        enablement=enablement,
        source=source or str(target_path),
        planned_data=planned,
    )


def _format_value(value: Any) -> str:
    if value is None:
        return "<未设置>"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def render_plan(plan: InitPlan) -> str:
    """Render a plan for human preview (secret references only)."""
    lines: list[str] = [
        f"ACP 初始化计划 v{plan.version}",
        f"目标配置：{plan.target_path}（来源：{plan.source}）",
    ]
    if plan.usage == UsageMode.CONNECT.value:
        lines.append("使用方式：连接已有 ACP 服务（不创建本地服务器/数据库/集群）")
        service = plan.resources.get("service") or {}
        for key in ("url", "project", "credential_ref"):
            if isinstance(service, Mapping) and service.get(key):
                lines.append(f"  {key}: {service[key]}")
    else:
        spec = TIER_BY_TIER.get(plan.tier or "")
        label = spec.label if spec else plan.tier
        lines.append("使用方式：本机部署")
        lines.append(f"部署档位：{label} ({plan.tier})")
        lines.append(f"组件组合：{', '.join(plan.components) if plan.components else '（无）'}")
        external = (
            (plan.resources.get("external") or {}) if isinstance(plan.resources, Mapping) else {}
        )
        if isinstance(external, Mapping) and external:
            lines.append("外部资源：")
            for name, value in external.items():
                lines.append(f"  {name}: {_format_value(value)}")
        if plan.params:
            lines.append("参数：")
            for name, value in plan.params.items():
                lines.append(f"  {name}: {_format_value(value)}")
    if plan.secrets:
        lines.append("秘密引用（仅引用，不含明文）：")
        for secret in plan.secrets:
            lines.append(f"  {secret.name} <- {secret.source}:{secret.ref}")
    else:
        lines.append("秘密引用：无")
    if plan.external_actions:
        lines.append("外部动作（需在计划内单独授权）：")
        for action in plan.external_actions:
            lines.append(f"  - {action}")
    lines.append("回退：")
    for step in plan.rollback:
        lines.append(f"  - {step}")
    lines.append("配置差异：")
    if not plan.changes:
        lines.append("  （无变化）")
    else:
        for change in plan.changes:
            lines.append(
                f"  {change.path}: {_format_value(change.before)} → {_format_value(change.after)}"
            )
    if not plan.enablement.enabled:
        lines.append(f"档位校验：拒绝启用 — {plan.enablement.detail}")
    return "\n".join(lines)


def plan_to_dict(plan: InitPlan) -> dict[str, Any]:
    """Serialise a plan (versioned; secrets redacted to references)."""
    return {
        "version": plan.version,
        "target_path": plan.target_path,
        "source": plan.source,
        "usage": plan.usage,
        "tier": plan.tier,
        "components": list(plan.components),
        "resources": _redact_resources(plan.resources),
        "params": dict(plan.params),
        "secrets": [secret.as_public() for secret in plan.secrets],
        "external_actions": list(plan.external_actions),
        "rollback": list(plan.rollback),
        "changes": [
            {"path": change.path, "before": change.before, "after": change.after}
            for change in plan.changes
        ],
        "digest": plan.digest,
        "enabled": plan.enablement.enabled,
        "enablement_detail": plan.enablement.detail,
    }


def _redact_resources(resources: Mapping[str, Any]) -> dict[str, Any]:
    """Deep-copy resources, replacing obvious value keys with a placeholder."""
    redacted = copy.deepcopy(dict(resources))
    for key in list(redacted):
        if "password" in key.lower() or "secret" in key.lower() or "token" in key.lower():
            redacted[key] = "<redacted>"
    return redacted


def apply_plan(plan: InitPlan) -> ApplyResult:
    """Apply *plan* to its target file, atomically and idempotently.

    Ordering matters: an already-applied plan (on-disk data == planned data) is
    a no-op; otherwise a digest mismatch against the plan's base refuses the
    write (concurrent modification), and only then is the file rewritten.

    Args:
        plan: Plan built by :func:`build_plan`.

    Returns:
        An :class:`ApplyResult` (``changed=False`` for the idempotent no-op).

    Raises:
        InitAbort: When the plan's tier is refused, or the target changed since
            planning (concurrent modification), or the write itself fails.
    """
    if not plan.enablement.enabled:
        raise InitAbort(f"拒绝应用：{plan.enablement.detail}")
    path = Path(plan.target_path)
    current = load_target(path)
    if _canonical(current) == _canonical(plan.planned_data):
        return ApplyResult(True, False, f"配置已是最新，无需写入（幂等）：{path}")
    if digest_of(current) != plan.digest:
        raise InitAbort("目标配置在计划生成后被修改，拒绝覆盖；请重新运行以生成新计划")
    save_target(path, dict(plan.planned_data))
    return ApplyResult(True, True, f"已应用计划并写入 {path}")
