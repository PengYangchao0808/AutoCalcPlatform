"""Unified ``acp init`` vocabularies: usage modes, deployment tiers, components.

B07-2 (plan todo 36).  This module holds **policy vocabularies only** — it
carries no configuration defaults, no run_root, and no executable resolution.
The configuration authority stays ``cccp.config`` (built-in
``_get_default_config``) and :func:`acp.core.paths.resolve_run_root`; the
deployment-tier *capability* gate is reused from
:mod:`acp_services.workspace.boundary` so the wizard never grows a second
tier rule (main plan §36.3).

The component manifest is a **capability list** — what ACP can assemble and
which external actions that may entail — NOT a configuration default table.
It carries no resource limits, memory figures or executable paths, so it can
never drift from ``cccp.config``.

§36.2 defaults honoured here: payment, object store and external IAM are
*never* enabled by default (``DEFAULT_EXCLUDED_COMPONENTS``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from acp_services.contracts.access import DeploymentTier
from acp_services.workspace.boundary import TierCapability, tier_capability_verdict

__all__ = [
    "CAPABILITY_COMPONENTS",
    "COMPONENT_BY_NAME",
    "COMPONENT_SPECS",
    "DEFAULT_EXCLUDED_COMPONENTS",
    "TIER_BY_KEY",
    "TIER_BY_TIER",
    "TIER_SPECS",
    "ComponentCategory",
    "ComponentSpec",
    "TierEnablement",
    "TierSpec",
    "TierVerification",
    "UsageMode",
    "component_closure",
    "default_component_selection",
    "tier_enablement",
    "tier_spec_for_key",
    "tier_spec_for_tier",
    "unknown_components",
]


class UsageMode(str, Enum):
    """Step-1 choice: initialise this machine, or connect an existing service."""

    LOCAL = "local"
    CONNECT = "connect"


class TierVerification(str, Enum):
    """Whether a deployment tier's recipe has been verified.

    ``NOT_VERIFIED`` tiers can be *chosen* (so the reason is visible) but
    :func:`tier_enablement` refuses to enable them — Hosted is never a silent
    default upgrade (§36.2).
    """

    VERIFIED = "verified"
    NOT_VERIFIED = "not_verified"


class ComponentCategory(str, Enum):
    """Component taxonomy used by the step-4 combination menu."""

    SCIENCE = "science"
    WEB = "web"
    MCP = "mcp"
    IDENTITY = "identity"
    STORAGE = "storage"
    USAGE = "usage"
    PAYMENT = "payment"
    OBJECT_STORE = "object_store"
    EXTERNAL_IAM = "external_iam"


@dataclass(frozen=True)
class ComponentSpec:
    """One selectable component: dependencies, conflicts and external actions.

    ``external_actions`` names side effects (identity setup, object-store
    provisioning, billing) that the step-6 plan must list and the operator must
    authorise separately (seam for todo 39).  Declaring them here does NOT
    execute anything.
    """

    name: str
    category: ComponentCategory
    label: str
    requires: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    default_enabled: bool = False
    external_actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class TierSpec:
    """Deployment tier metadata (recipe-level, not a configuration default)."""

    key: str
    tier: str
    label: str
    verification: TierVerification
    required_components: tuple[str, ...]
    required_resources: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class TierEnablement:
    """Result of checking whether a tier may be enabled for a selection."""

    enabled: bool
    detail: str
    error_code: str | None = None


# --------------------------------------------------------------------------- #
# Component manifest (capability list — never a defaults table)
# --------------------------------------------------------------------------- #

COMPONENT_SPECS: tuple[ComponentSpec, ...] = (
    ComponentSpec(
        "science-core",
        ComponentCategory.SCIENCE,
        "科学核心（本地摄入/计划/执行/导出）",
        default_enabled=True,
    ),
    ComponentSpec(
        "local-identity",
        ComponentCategory.IDENTITY,
        "本地身份（单用户）",
        requires=("science-core",),
        default_enabled=True,
    ),
    ComponentSpec(
        "local-storage",
        ComponentCategory.STORAGE,
        "本地存储（run_root）",
        requires=("science-core",),
        default_enabled=True,
    ),
    ComponentSpec(
        "web-ui",
        ComponentCategory.WEB,
        "Web 界面",
        requires=("science-core",),
    ),
    ComponentSpec(
        "mcp-server",
        ComponentCategory.MCP,
        "MCP 服务",
        requires=("science-core",),
    ),
    ComponentSpec(
        "authentication",
        ComponentCategory.IDENTITY,
        "认证能力",
        requires=("local-identity",),
        external_actions=("configure-identity",),
    ),
    ComponentSpec(
        "isolation",
        ComponentCategory.IDENTITY,
        "多用户隔离",
        requires=("local-identity",),
    ),
    ComponentSpec(
        "external-iam",
        ComponentCategory.EXTERNAL_IAM,
        "外部 IAM/OIDC 提供方",
        requires=("authentication",),
        external_actions=("configure-external-iam",),
    ),
    ComponentSpec(
        "object-store",
        ComponentCategory.OBJECT_STORE,
        "对象存储",
        requires=("local-storage",),
        external_actions=("provision-object-store",),
    ),
    ComponentSpec(
        "usage-metering",
        ComponentCategory.USAGE,
        "用量计量",
        requires=("science-core",),
    ),
    ComponentSpec(
        "payment",
        ComponentCategory.PAYMENT,
        "支付/账单",
        requires=("usage-metering",),
        external_actions=("configure-billing",),
    ),
)

COMPONENT_BY_NAME: dict[str, ComponentSpec] = {spec.name: spec for spec in COMPONENT_SPECS}

#: §36.2 — never enabled by default.
DEFAULT_EXCLUDED_COMPONENTS: tuple[str, ...] = ("payment", "object-store", "external-iam")

#: Component names whose selection satisfies a :class:`TierCapability`.
CAPABILITY_COMPONENTS: frozenset[str] = frozenset(
    {TierCapability.AUTHENTICATION, TierCapability.ISOLATION}
)


# --------------------------------------------------------------------------- #
# Tier manifest (verification + component/resource requirements)
# --------------------------------------------------------------------------- #

_STANDALONE_REQUIRED = ("science-core", "local-identity", "local-storage")
_LAB_REQUIRED = (
    "science-core",
    "local-identity",
    "local-storage",
    "web-ui",
    "authentication",
    "isolation",
)
_HOSTED_REQUIRED = (*_LAB_REQUIRED, "external-iam", "object-store", "usage-metering")

TIER_SPECS: tuple[TierSpec, ...] = (
    TierSpec(
        key="standalone",
        tier=DeploymentTier.LOCAL_SINGLE,
        label="Standalone（单机）",
        verification=TierVerification.VERIFIED,
        required_components=_STANDALONE_REQUIRED,
        required_resources=(),
        summary="最小受支持：本地单用户，数据与计算同机。",
    ),
    TierSpec(
        key="lab",
        tier=DeploymentTier.LAB,
        label="Lab Server（实验室服务器）",
        verification=TierVerification.VERIFIED,
        required_components=_LAB_REQUIRED,
        required_resources=("database",),
        summary="实验室共享：需认证、隔离与共享数据库。",
    ),
    TierSpec(
        key="hosted",
        tier=DeploymentTier.HOSTED,
        label="Hosted（托管）",
        verification=TierVerification.NOT_VERIFIED,
        required_components=_HOSTED_REQUIRED,
        required_resources=("database", "storage"),
        summary="托管多租户：能力尚未验证，不可启用。",
    ),
)

TIER_BY_KEY: dict[str, TierSpec] = {spec.key: spec for spec in TIER_SPECS}
TIER_BY_TIER: dict[str, TierSpec] = {spec.tier: spec for spec in TIER_SPECS}


# --------------------------------------------------------------------------- #
# Selection helpers
# --------------------------------------------------------------------------- #


def default_component_selection() -> tuple[str, ...]:
    """Return the default component set (payment/object-store/external-IAM off)."""
    return tuple(spec.name for spec in COMPONENT_SPECS if spec.default_enabled)


def unknown_components(names: object) -> tuple[str, ...]:
    """Return *names* that are not in the manifest (order-preserving)."""
    try:
        items = [str(name) for name in names]  # type: ignore[union-attr]
    except TypeError:
        return ()
    return tuple(name for name in items if name not in COMPONENT_BY_NAME)


def component_closure(names: object) -> tuple[str, ...]:
    """Expand *names* transitively to include every ``requires`` dependency.

    Unknown names are ignored here — callers validate with
    :func:`unknown_components` first.
    """
    try:
        pending = [str(name) for name in names]  # type: ignore[union-attr]
    except TypeError:
        return ()
    selected: set[str] = set()
    while pending:
        name = pending.pop()
        if name in selected or name not in COMPONENT_BY_NAME:
            continue
        selected.add(name)
        pending.extend(COMPONENT_BY_NAME[name].requires)
    return tuple(sorted(selected))


def tier_spec_for_key(key: object) -> TierSpec | None:
    """Resolve a wizard tier key (``standalone``/``lab``/``hosted``)."""
    if key is None:
        return None
    return TIER_BY_KEY.get(str(key).strip().lower())


def tier_spec_for_tier(tier: object) -> TierSpec | None:
    """Resolve a deployment-tier value (``local-single``/``lab``/``hosted``)."""
    if tier is None:
        return None
    return TIER_BY_TIER.get(str(tier).strip().lower())


def tier_enablement(
    spec: TierSpec,
    components: object,
    resources: object = (),
) -> TierEnablement:
    """Decide whether *spec* may be enabled for a component/resource selection.

    Three independent refusals, none of which silently downgrades the tier:

    1. an unverified tier (Hosted) is never enableable;
    2. the reused :func:`acp_services.workspace.boundary.tier_capability_verdict`
        gate — Lab/Hosted require the ``authentication`` and ``isolation``
        capabilities;
    3. the tier's own required components and required resources.

    Args:
        spec: Tier being requested.
        components: Iterable of selected component names.
        resources: Iterable of provided external resource keys
            (``database``/``https``/``storage``).

    Returns:
        A :class:`TierEnablement`; ``enabled`` is False with a reason on refusal.
    """
    if spec.verification is not TierVerification.VERIFIED:
        return TierEnablement(
            False,
            f"档位 {spec.label} 能力尚未验证，不可启用",
            "tier_unverified",
        )

    try:
        selected_components = {str(name) for name in components}  # type: ignore[union-attr]
    except TypeError:
        selected_components = set()
    try:
        provided_resources = {str(name) for name in resources}  # type: ignore[union-attr]
    except TypeError:
        provided_resources = set()

    capabilities = selected_components & CAPABILITY_COMPONENTS
    verdict = tier_capability_verdict(spec.tier, capabilities)
    if not verdict.allowed:
        missing = "、".join(verdict.missing_capabilities) or "所需能力"
        return TierEnablement(
            False,
            f"档位 {spec.label} 缺少能力：{missing}（缺少组件依赖，不静默降级）",
            verdict.error_code,
        )

    missing_components = [
        name for name in spec.required_components if name not in selected_components
    ]
    if missing_components:
        return TierEnablement(
            False,
            f"档位 {spec.label} 缺少必需组件：{'、'.join(missing_components)}（不静默降级）",
            "tier_components_missing",
        )

    missing_resources = [name for name in spec.required_resources if name not in provided_resources]
    if missing_resources:
        return TierEnablement(
            False,
            f"档位 {spec.label} 缺少必需资源：{'、'.join(missing_resources)}（不静默降级）",
            "tier_resources_missing",
        )

    return TierEnablement(True, "")
