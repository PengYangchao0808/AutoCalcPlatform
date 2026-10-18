"""Non-interactive answer-file schema for ``acp init`` (B07-2, todo 36).

The answer file feeds the **same** plan builder as the interactive steps
(§36.2: "自动化不维护第二套规则"): one schema, one validator, one apply path.
It is versioned (:data:`acp.init_wizard.plan.PLAN_VERSION`) and secret values
are refused — only references are accepted, exactly as in the wizard.

Example (local, Standalone)::

    version: 1
    usage: local
    tier: standalone
    components: [science-core, local-identity, local-storage]
    resources:
      software: {orca: /opt/orca/orca}
      cluster: {type: local}
    params: {run_root: /data/acp/runs}
    secrets:
      - name: db_password
        ref: env:ACP_DB_PASSWORD

Example (connect an existing service)::

    version: 1
    usage: connect
    service: {url: https://acp.example.org, project: demo, credential_ref: env:ACP_TOKEN}
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from acp.init_wizard.options import (
    TIER_BY_KEY,
    TIER_BY_TIER,
    default_component_selection,
    unknown_components,
)
from acp.init_wizard.persist import InitAbort
from acp.init_wizard.plan import (
    PLAN_VERSION,
    InitPlan,
    SecretRef,
    build_plan,
    make_secret_ref,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ANSWER_SCHEMA_VERSION",
    "load_answers",
    "plan_from_answers",
]

#: Answer-file schema version — pinned to the plan version so one number
#: describes a whole request/plan round-trip.
ANSWER_SCHEMA_VERSION = PLAN_VERSION

_USAGE_VALUES = {"local", "connect"}
_EXTERNAL_KEY_NAMES = ("database", "https", "storage")


def load_answers(path: Path) -> dict[str, Any]:
    """Load and structurally validate an answer file.

    Args:
        path: Answer YAML file.

    Returns:
        The raw answer mapping.

    Raises:
        InitAbort: On unreadable/absent file, YAML error, non-mapping root,
            or a ``version`` that is not :data:`ANSWER_SCHEMA_VERSION`.
    """
    if not path.exists():
        raise InitAbort(f"回答文件不存在：{path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InitAbort(f"无法读取回答文件：{path}（errno {exc.errno}）") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InitAbort(f"回答文件解析失败：{path}（{exc}）") from exc
    if not isinstance(data, dict):
        raise InitAbort(f"回答文件根节点必须是映射，{path} 实际为 {type(data).__name__}")
    version = data.get("version")
    if version != ANSWER_SCHEMA_VERSION:
        raise InitAbort(f"回答文件版本不受支持：{version!r}（当前支持 {ANSWER_SCHEMA_VERSION}）")
    usage = data.get("usage")
    if usage not in _USAGE_VALUES:
        raise InitAbort(f"回答文件 usage 必须是 local 或 connect，实际为 {usage!r}")
    return data


def _resolve_tier(answers: dict[str, Any]) -> str:
    tier = answers.get("tier")
    spec = TIER_BY_KEY.get(str(tier).strip().lower()) if tier is not None else None
    if spec is None and tier is not None:
        spec = TIER_BY_TIER.get(str(tier).strip().lower())
    if spec is None:
        raise InitAbort(f"回答文件缺少或含未知档位 tier：{tier!r}")
    return spec.tier


def _parse_software(raw: object) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise InitAbort("resources.software 必须是映射（软件名 -> 可执行文件路径）")
    return {str(name): str(path) for name, path in raw.items() if path}


def _parse_cluster(raw: object) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise InitAbort("resources.cluster 必须是映射")
    return dict(raw)


def _parse_external(raw: object) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise InitAbort("resources 必须是映射")
    external: dict[str, Any] = {}
    for key in _EXTERNAL_KEY_NAMES:
        if key in raw and raw[key]:
            external[key] = raw[key]
    unknown = sorted(set(raw) - {"software", "cluster", *_EXTERNAL_KEY_NAMES})
    if unknown:
        raise InitAbort(f"resources 含未知键：{'、'.join(unknown)}")
    return external


def _parse_secrets(raw: object) -> tuple[SecretRef, ...]:
    if raw in (None, ""):
        return ()
    if not isinstance(raw, list):
        raise InitAbort("secrets 必须是列表")
    refs: list[SecretRef] = []
    for entry in raw:
        if isinstance(entry, str):
            name, sep, reference = entry.partition("=")
            if not sep:
                raise InitAbort(f"secret 条目格式应为 name=source:ref，实际为 {entry!r}")
            refs.append(make_secret_ref(name, reference))
            continue
        if not isinstance(entry, dict):
            raise InitAbort(f"secret 条目必须是映射或字符串，实际为 {type(entry).__name__}")
        if "value" in entry:
            raise InitAbort(
                f"secret {entry.get('name')!r} 含明文 value；只允许 reference（env/file/provider）"
            )
        name = str(entry.get("name", ""))
        reference = entry.get("ref")
        if not isinstance(reference, str) or not reference.strip():
            raise InitAbort(f"secret {name!r} 缺少 ref（如 env:ACP_DB_PASSWORD）")
        source = entry.get("source")
        if ":" not in reference and isinstance(source, str) and source.strip():
            reference = f"{source.strip()}:{reference.strip()}"
        refs.append(make_secret_ref(name, reference))
    return tuple(refs)


def plan_from_answers(
    config_path: Path,
    base: dict[str, Any],
    answers: dict[str, Any],
) -> InitPlan:
    """Build a :class:`InitPlan` from a validated answer mapping.

    Args:
        config_path: Raw target YAML the plan will rewrite.
        base: Current raw target mapping.
        answers: Mapping returned by :func:`load_answers`.

    Returns:
        The initialization plan (tier refusal carried in ``enablement``).

    Raises:
        InitAbort: On unknown components or a malformed resource/secret entry.
    """
    usage = str(answers["usage"])
    if usage == "connect":
        service = answers.get("service")
        if not isinstance(service, dict) or not service.get("url"):
            raise InitAbort("connect 模式必须提供 service.url")
        resources: dict[str, Any] = {"service": dict(service)}
        return build_plan(
            config_path,
            base,
            usage=usage,
            tier=None,
            components=(),
            resources=resources,
            params={},
            secrets=(),
        )

    tier = _resolve_tier(answers)
    raw_components = answers.get("components")
    if raw_components is None:
        components: tuple[str, ...] = default_component_selection()
    else:
        if not isinstance(raw_components, list):
            raise InitAbort("components 必须是列表")
        components = tuple(str(name) for name in raw_components)
        unknown = unknown_components(components)
        if unknown:
            raise InitAbort(f"未知组件：{'、'.join(unknown)}")

    raw_resources = answers.get("resources") or {}
    if not isinstance(raw_resources, dict):
        raise InitAbort("resources 必须是映射")
    software = _parse_software(raw_resources.get("software"))
    cluster = _parse_cluster(raw_resources.get("cluster"))
    external = _parse_external(raw_resources)
    params = answers.get("params") or {}
    if not isinstance(params, dict):
        raise InitAbort("params 必须是映射")

    resources_out: dict[str, Any] = {}
    if software:
        resources_out["software"] = software
    if cluster:
        resources_out["cluster"] = cluster
    if external:
        resources_out["external"] = external

    return build_plan(
        config_path,
        base,
        usage=usage,
        tier=tier,
        components=components,
        resources=resources_out,
        params=params,
        secrets=_parse_secrets(answers.get("secrets")),
    )
