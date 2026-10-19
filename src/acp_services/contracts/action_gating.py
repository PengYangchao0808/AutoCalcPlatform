"""Action-class authorization + in-flight mode-switch guard (B07-5, §36.4).

``acp init`` produces a plan that may imply *side effects outside the config
file*: installing dependencies, bootstrapping a remote directory, creating a
system service/user, migrating a database.  §36.4 draws a hard line around
them: "安装新包、远端 bootstrap、创建系统用户、改权限/证书/数据库、启动服务须在
计划中明确列出并由操作者授权" — and "不借升级隐式扩大权限".

This module is the Services-side owner of that boundary.  It is deliberately
pure and stdlib-only (plus the sibling :mod:`config_plan` contracts): no
third-party dependency and no import of the application package lands on a
core-only path.

Two guarantees:

1. **Five explicit action classes, item-by-item authorization.**  Every action
   a plan implies is classified as one of :class:`ActionClass` and must be
   *listed* in the plan and *authorized by its own id*.  Authorization defaults
   to deny — an id that is absent from the operator's decisions is refused.
   :class:`AuthorizedActionExecutor` wraps a real executor and guarantees a
   refused action reaches the inner executor **zero** times (it raises instead
   of delegating), while :func:`render_authorization` reports what was asked
   and what was granted.
2. **In-flight mode-switch guard.**  Changing the execution mode, storage root,
   identity provider, database or metering mode must route through the
   migration flows; while in-flight tasks or reservations exist an incompatible
   switch is *blocked* and the operator is told to resolve them first
   (§36.4: "存在在途任务/预留时阻止不兼容切换").  The judgement material (which
   tasks count as in-flight, how many reservations are held) is injected
   through :class:`InFlightProbe`, so this module never imports the scheduler.

Nothing here performs I/O: callers own execution, and the plan object is the
single source of what will happen.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from acp_services.contracts.config_plan import (
    ActionKind,
    ConfigPlan,
    ExternalAction,
    ExternalActionExecutor,
)

__all__ = [
    "ACTION_CLASS_LABELS",
    "CONFIG_WRITE_ACTION_ID",
    "MODE_AXIS_LABELS",
    "ActionClass",
    "ActionGate",
    "ActionGatingError",
    "ActionNotAuthorizedError",
    "AuthorizationDecision",
    "AuthorizedActionExecutor",
    "InFlightProbe",
    "InFlightState",
    "ModeAxis",
    "ModeChange",
    "ModeGuardError",
    "ModeSwitchDecision",
    "PlannedAction",
    "StaticInFlightProbe",
    "action_class_for",
    "decide_authorizations",
    "guard_mode_switches",
    "plan_actions",
    "render_authorization",
    "render_mode_switch_decision",
]

# --------------------------------------------------------------------------- #
# Five action classes (§36.4)
# --------------------------------------------------------------------------- #


class ActionClass:
    """The five kinds of action a plan can imply — never implicitly widened.

    They are intentionally coarse: the operator authorizes *what class of
    privileged thing happens* per action, not an implementation detail.
    """

    CONFIG_WRITE = "config_write"
    DEPENDENCY_INSTALL = "dependency_install"
    REMOTE_BOOTSTRAP = "remote_bootstrap"
    SYSTEM_SERVICE = "system_service"
    DB_MIGRATION = "db_migration"

    ALL: tuple[str, ...] = (
        CONFIG_WRITE,
        DEPENDENCY_INSTALL,
        REMOTE_BOOTSTRAP,
        SYSTEM_SERVICE,
        DB_MIGRATION,
    )


#: Human-readable labels (the plan/authorization preview is Chinese-facing).
ACTION_CLASS_LABELS: Mapping[str, str] = {
    ActionClass.CONFIG_WRITE: "纯配置写入",
    ActionClass.DEPENDENCY_INSTALL: "安装依赖",
    ActionClass.REMOTE_BOOTSTRAP: "远端 bootstrap",
    ActionClass.SYSTEM_SERVICE: "创建系统服务/用户",
    ActionClass.DB_MIGRATION: "数据库迁移",
}

#: Stable id for the plan's own config write — listed like any other action.
CONFIG_WRITE_ACTION_ID = "config:write"

#: ``ExternalAction.kind`` → :class:`ActionClass`.  Unknown kinds are refused
#: rather than silently classified (that is the "不隐式扩大权限" guarantee).
_KIND_TO_CLASS: Mapping[str, str] = {
    ActionClass.CONFIG_WRITE: ActionClass.CONFIG_WRITE,
    "config_write": ActionClass.CONFIG_WRITE,
    "dependency_install": ActionClass.DEPENDENCY_INSTALL,
    "pip_install": ActionClass.DEPENDENCY_INSTALL,
    ActionKind.REMOTE_DIR: ActionClass.REMOTE_BOOTSTRAP,
    "remote_bootstrap": ActionClass.REMOTE_BOOTSTRAP,
    ActionKind.SYSTEM_SERVICE: ActionClass.SYSTEM_SERVICE,
    "system_user": ActionClass.SYSTEM_SERVICE,
    ActionKind.DATABASE: ActionClass.DB_MIGRATION,
    "db_migration": ActionClass.DB_MIGRATION,
}


class ActionGatingError(Exception):
    """Base class for action-classification / authorization failures."""


class ModeGuardError(ActionGatingError):
    """A mode-change axis is not a recognised switch axis (fail closed)."""


def action_class_for(kind: str) -> str:
    """Return the :class:`ActionClass` for an ``ExternalAction.kind``.

    Raises:
        ActionGatingError: For a kind that is not registered.  Refusing is the
            point: a new kind must be classified deliberately, so an upgrade can
            never quietly acquire a new privileged action class.
    """
    try:
        return _KIND_TO_CLASS[str(kind)]
    except KeyError as exc:
        raise ActionGatingError(
            f"未登记的动作类别：{kind!r}（须显式分类后才能授权，禁止隐式扩大权限）"
        ) from exc


@dataclass(frozen=True)
class PlannedAction:
    """One action a plan lists, classified and ready for authorization.

    Attributes:
        action_id: Stable id — the operator authorizes exactly this id.
        action_class: One of :class:`ActionClass`.
        description: Human-readable summary.
        external: ``False`` for the plan's own config write (executed by the
            apply engine, not an :class:`ExternalActionExecutor`).
    """

    action_id: str
    action_class: str
    description: str
    external: bool = True

    @property
    def label(self) -> str:
        """The Chinese action-class label."""
        return ACTION_CLASS_LABELS.get(self.action_class, self.action_class)


def plan_actions(
    plan: ConfigPlan, *, include_config_write: bool = True
) -> tuple[PlannedAction, ...]:
    """List every action *plan* implies, classified (§36.4 "明确列出").

    The plan's own config write is listed as :data:`CONFIG_WRITE_ACTION_ID`
    (it is where the config *is* written, so it is the one action that always
    happens); each :class:`ExternalAction` is classified through
    :func:`action_class_for`.

    Raises:
        ActionGatingError: When an external action carries an unregistered kind.
    """
    actions: list[PlannedAction] = []
    if include_config_write:
        actions.append(
            PlannedAction(
                action_id=CONFIG_WRITE_ACTION_ID,
                action_class=ActionClass.CONFIG_WRITE,
                description=f"写入配置 {plan.target_path}",
                external=False,
            )
        )
    for action in plan.external_actions:
        actions.append(
            PlannedAction(
                action_id=action.action_id,
                action_class=action_class_for(action.kind),
                description=action.description,
                external=True,
            )
        )
    return tuple(actions)


# --------------------------------------------------------------------------- #
# Authorization (item-by-item; default deny)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AuthorizationDecision:
    """The outcome of authorizing a plan's listed actions.

    ``authorized``/``refused`` partition the planned actions; a refused action
    must not run.
    """

    authorized: tuple[PlannedAction, ...]
    refused: tuple[PlannedAction, ...]

    @property
    def all_authorized(self) -> bool:
        """True when no listed action was refused."""
        return not self.refused

    @property
    def refused_action_ids(self) -> tuple[str, ...]:
        """Ids of the refused actions, in plan order."""
        return tuple(action.action_id for action in self.refused)

    @property
    def authorized_external_ids(self) -> tuple[str, ...]:
        """Ids of authorized *external* actions (executor-facing)."""
        return tuple(action.action_id for action in self.authorized if action.external)

    @property
    def config_write_authorized(self) -> bool:
        """Whether the plan's own config write was authorized."""
        return any(
            action.action_id == CONFIG_WRITE_ACTION_ID
            and action.action_class == ActionClass.CONFIG_WRITE
            for action in self.authorized
        )


def decide_authorizations(
    actions: Iterable[PlannedAction],
    authorizations: Mapping[str, bool] | None,
) -> AuthorizationDecision:
    """Partition *actions* by per-id *authorizations* (absent → refused).

    Item-by-item: an action's id must be explicitly present and truthy.  A
    missing key is a refusal, never a default allow.
    """
    decisions = dict(authorizations or {})
    authorized: list[PlannedAction] = []
    refused: list[PlannedAction] = []
    for action in actions:
        if bool(decisions.get(action.action_id, False)):
            authorized.append(action)
        else:
            refused.append(action)
    return AuthorizationDecision(tuple(authorized), tuple(refused))


class ActionGate:
    """Classifies a plan's actions and authorizes them one id at a time."""

    def __init__(self, authorizations: Mapping[str, bool] | None = None) -> None:
        self._authorizations = dict(authorizations or {})

    @property
    def authorizations(self) -> Mapping[str, bool]:
        """A copy of the operator's per-id decisions."""
        return dict(self._authorizations)

    def decide(self, actions: Iterable[PlannedAction]) -> AuthorizationDecision:
        """Authorize an already-listed action set."""
        return decide_authorizations(actions, self._authorizations)

    def authorize_plan(
        self, plan: ConfigPlan, *, include_config_write: bool = True
    ) -> AuthorizationDecision:
        """List *plan*'s actions and authorize them item-by-item."""
        return self.decide(plan_actions(plan, include_config_write=include_config_write))


def render_authorization(decision: AuthorizationDecision) -> str:
    """Render the authorization matrix for the plan preview / refusal reason."""
    lines = ["动作授权："]
    if not decision.authorized and not decision.refused:
        lines.append("  （无动作）")
        return "\n".join(lines)
    for action in decision.authorized:
        lines.append(f"  [已授权] {action.action_id} — {action.label}：{action.description}")
    for action in decision.refused:
        reason = f"  [未授权] {action.action_id} — {action.label}：{action.description}（不会执行）"
        lines.append(reason)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Gated executor (zero unauthorized executions)
# --------------------------------------------------------------------------- #


class ActionNotAuthorizedError(ActionGatingError):
    """Raised when a gated executor is asked to run an unauthorized action.

    Attributes:
        action_id: The refused action id.
        action_class: Its :class:`ActionClass`.
    """

    def __init__(self, action_id: str, action_class: str) -> None:
        label = ACTION_CLASS_LABELS.get(action_class, action_class)
        super().__init__(f"动作未授权，拒绝执行：{action_id}（{label}）")
        self.action_id = action_id
        self.action_class = action_class


class AuthorizedActionExecutor:
    """Delegates to *inner* only for explicitly authorized action ids.

    This is the belt-and-braces half of the guarantee: even if a caller were to
    hand an unauthorized action to the apply engine, :meth:`execute` refuses
    without ever touching the inner executor.  It records ``executed`` and
    ``refused`` so the receipt can report both.
    """

    def __init__(self, inner: ExternalActionExecutor, decision: AuthorizationDecision) -> None:
        self._inner = inner
        self._authorized_ids = set(decision.authorized_external_ids)
        self.executed: list[str] = []
        self.refused: list[str] = []

    def is_done(self, action: ExternalAction) -> bool:
        """Never claim an unauthorized action is already done."""
        if action.action_id not in self._authorized_ids:
            return False
        return self._inner.is_done(action)

    def execute(self, action: ExternalAction) -> None:
        """Run an authorized action; refuse an unauthorized one.

        Raises:
            ActionNotAuthorizedError: For an id that was not authorized — raised
                *before* the inner executor is contacted.
        """
        if action.action_id not in self._authorized_ids:
            self.refused.append(action.action_id)
            raise ActionNotAuthorizedError(action.action_id, action_class_for(action.kind))
        self._inner.execute(action)
        self.executed.append(action.action_id)


# --------------------------------------------------------------------------- #
# In-flight mode-switch guard
# --------------------------------------------------------------------------- #


class ModeAxis:
    """The five switch axes §36.4 says must route through migration flows."""

    EXECUTION_MODE = "execution_mode"
    STORAGE_ROOT = "storage_root"
    IDENTITY_PROVIDER = "identity_provider"
    DATABASE = "database"
    METERING_MODE = "metering_mode"

    ALL: tuple[str, ...] = (
        EXECUTION_MODE,
        STORAGE_ROOT,
        IDENTITY_PROVIDER,
        DATABASE,
        METERING_MODE,
    )


MODE_AXIS_LABELS: Mapping[str, str] = {
    ModeAxis.EXECUTION_MODE: "执行模式",
    ModeAxis.STORAGE_ROOT: "存储根",
    ModeAxis.IDENTITY_PROVIDER: "身份 provider",
    ModeAxis.DATABASE: "数据库",
    ModeAxis.METERING_MODE: "计量模式",
}


@dataclass(frozen=True)
class ModeChange:
    """One requested change on a :class:`ModeAxis`.

    Attributes:
        axis: One of :class:`ModeAxis`.
        before: Current value.
        after: Requested value.
        compatible_during_inflight: Opt-in for a switch proven safe to perform
            while work is running.  Defaults to ``False`` (blocked).
    """

    axis: str
    before: str
    after: str
    compatible_during_inflight: bool = False

    def is_switch(self) -> bool:
        """True when the value actually changes."""
        return str(self.before) != str(self.after)

    @property
    def label(self) -> str:
        """The Chinese axis label."""
        return MODE_AXIS_LABELS.get(self.axis, self.axis)


@dataclass(frozen=True)
class InFlightState:
    """In-flight judgement material supplied by an injected probe.

    Attributes:
        active_tasks: Count of non-terminal (active) tasks.
        reservations: Count of held resource reservations (e.g. remote nodes).
        detail: Optional human-readable provenance.
    """

    active_tasks: int = 0
    reservations: int = 0
    detail: str = ""

    @property
    def has_in_flight(self) -> bool:
        """True when any task or reservation is in flight."""
        return self.active_tasks > 0 or self.reservations > 0

    def summary(self) -> str:
        """Compact description used in the guard message."""
        parts: list[str] = []
        if self.active_tasks:
            parts.append(f"{self.active_tasks} 个在途任务")
        if self.reservations:
            parts.append(f"{self.reservations} 个预留")
        return "、".join(parts) if parts else "无在途任务/预留"


class InFlightProbe(Protocol):
    """Supplies in-flight judgement material without importing the scheduler.

    The production adapter reuses the same status/reservation facts the startup
    recovery path consults; tests inject a static stub.
    """

    def snapshot(self) -> InFlightState:
        """Return the current in-flight state."""
        ...


@dataclass(frozen=True)
class StaticInFlightProbe:
    """An :class:`InFlightProbe` returning a fixed state (tests / dry runs)."""

    state: InFlightState

    def snapshot(self) -> InFlightState:
        return self.state


@dataclass(frozen=True)
class ModeSwitchDecision:
    """The guard's verdict for a set of requested mode changes.

    Attributes:
        allowed: True when no switch is blocked by in-flight work.
        blocked: Switches refused *because* in-flight work exists.
        migration_required: Switches that must route through the migration
            flows regardless (they are never a plain config rewrite).
        state: The probe's snapshot when one was needed (``None`` when there
            were no switches at all, so no probe I/O happened).
        detail: Human-readable verdict.
        error_code: ``"in_flight_blocks_switch"`` when blocked, else ``None``.
    """

    allowed: bool
    blocked: tuple[ModeChange, ...]
    migration_required: tuple[ModeChange, ...]
    state: InFlightState | None
    detail: str
    error_code: str | None = None

    @property
    def changed(self) -> bool:
        """True when any switch was requested."""
        return bool(self.blocked or self.migration_required)


def guard_mode_switches(
    changes: Iterable[ModeChange],
    probe: InFlightProbe,
) -> ModeSwitchDecision:
    """Block incompatible mode switches while work is in flight (§36.4).

    A *switch* is a change on one of :class:`ModeAxis.ALL`.  When the injected
    probe reports in-flight tasks/reservations, every switch that is not
    explicitly marked ``compatible_during_inflight`` is blocked; the caller is
    told to resolve the in-flight work first.  All switches — blocked or not —
    are reported as ``migration_required`` because identity provider, storage
    root, database and metering changes must route through the migration flows.

    Args:
        changes: Requested changes.  Non-switches (same before/after) are
            ignored.
        probe: Injected in-flight material.  Consulted only when a switch
            exists.

    Returns:
        A :class:`ModeSwitchDecision`.

    Raises:
        ModeGuardError: When a change's axis is not a recognised mode axis
            (fail closed rather than silently skip a mistyped axis).
    """
    requested = tuple(changes)
    for change in requested:
        if change.axis not in ModeAxis.ALL:
            known = "/".join(ModeAxis.ALL)
            raise ModeGuardError(f"未知模式切换轴：{change.axis!r}（应为 {known}）")

    switches = tuple(change for change in requested if change.is_switch())
    if not switches:
        return ModeSwitchDecision(
            allowed=True,
            blocked=(),
            migration_required=(),
            state=None,
            detail="无模式切换请求",
        )

    state = probe.snapshot()
    incompatible = tuple(change for change in switches if not change.compatible_during_inflight)

    if state.has_in_flight and incompatible:
        axes = "、".join(sorted({change.label for change in incompatible}))
        return ModeSwitchDecision(
            allowed=False,
            blocked=incompatible,
            migration_required=switches,
            state=state,
            detail=(
                f"存在{state.summary()}：不兼容模式切换被阻止（{axes}）。"
                "请先完成、暂停或取消在途任务并释放预留，再经迁移流程切换"
            ),
            error_code="in_flight_blocks_switch",
        )

    axes = "、".join(sorted({change.label for change in switches}))
    suffix = "（在途任务/预留存在，仅允许标记为兼容的切换）" if state.has_in_flight else ""
    return ModeSwitchDecision(
        allowed=True,
        blocked=(),
        migration_required=switches,
        state=state,
        detail=f"允许切换：{axes}；须经对应迁移流程执行{suffix}",
    )


def render_mode_switch_decision(decision: ModeSwitchDecision) -> str:
    """Render a :class:`ModeSwitchDecision` for the operator."""
    lines = [f"模式切换：{'允许' if decision.allowed else '阻止'}", decision.detail]
    if decision.blocked:
        lines.append("被阻止的切换：")
        for change in decision.blocked:
            lines.append(f"  - {change.label}: {change.before} → {change.after}")
    if decision.migration_required:
        lines.append("需经迁移流程的切换：")
        for change in decision.migration_required:
            lines.append(f"  - {change.label}: {change.before} → {change.after}")
    return "\n".join(lines)
