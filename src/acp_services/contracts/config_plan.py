"""Versioned configuration plan + concurrent-safe, staged, idempotent apply (B07-4).

``acp init`` produces a *plan* before it touches anything.  This module is the
Services-side owner of that plan object and of the apply engine, so the plan can
never drift from what is actually written and so a partially applied plan is
honestly reported instead of being disguised as an all-or-nothing operation.

Four guarantees, in the order they matter:

1. **Versioned + digested.**  :class:`ConfigPlan` carries a schema ``version``, a
   ``base_digest`` (the config as it was when the plan was built) and a
   ``plan_digest`` (the intended result), plus the flattened ``changes``,
   the ``external_actions`` and the ``rollback`` hints.  Nothing measures the
   plan by anything but its digest.
2. **Concurrent-safe, atomic, restricted.**  :func:`apply_config_plan` refuses
   to write when the live file no longer matches ``base_digest`` (a concurrent
   edit must never be silently overwritten).  Writes go through the same
   semantics as the wizard's ``persist`` layer: a writability pre-flight (so a
   read-only target aborts before a ``.bak`` is even made), a timestamped
   ``.bak`` that is never widened, and a temp-file + ``os.replace``.  A plan
   that is already on disk is a no-op.
3. **Staged, idempotent cross-system recovery.**  Remote directories, system
   services and databases cannot be rolled back atomically, so each one is an
   :class:`ExternalAction` with an ``action_id`` idempotency key and a ``stage``.
   Outcomes are journaled as they happen; on replay the journal *and* the
   executor's own ``is_done`` are consulted first, so a default account or a
   namespace is never created twice.
4. **Honest partial completion.**  The result is an :class:`ApplyReport` that
   says ``completed`` / ``not_completed`` and lists the manual recovery steps.
   It deliberately never claims a full atomic rollback of cross-system effects.

The module imports only the standard library at the top level; PyYAML is loaded
lazily by the default serialization helpers, so importing it never pulls a
third-party dependency onto a core-only path.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

__all__ = [
    "CONFIG_PLAN_VERSION",
    "JOURNAL_VERSION",
    "ActionKind",
    "ActionOutcome",
    "ActionStatus",
    "ApplyReport",
    "ConfigChange",
    "ConfigPlan",
    "ConfigPlanError",
    "ConfigWriteError",
    "ExternalAction",
    "ExternalActionExecutor",
    "apply_config_plan",
    "apply_edits",
    "atomic_replace_text",
    "backup_file",
    "build_config_plan",
    "canonical_json",
    "digest_of",
    "diff_data",
    "journal_path_for",
    "load_config_file",
    "load_journal",
    "render_apply_report",
    "save_journal",
    "writable_mode",
    "write_config_file",
]

#: Version of the plan object schema (reported by :func:`ConfigPlan.to_dict`).
CONFIG_PLAN_VERSION = 1

#: Version of the on-disk apply journal.
JOURNAL_VERSION = 1


class ActionKind:
    """Kinds of cross-system action a plan can list (not all are reversible)."""

    REMOTE_DIR = "remote_dir"
    SYSTEM_SERVICE = "system_service"
    DATABASE = "database"


class ActionStatus:
    """Terminal/transient states an :class:`ExternalAction` can be in."""

    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"
    PENDING = "pending"


class ConfigPlanError(Exception):
    """Fatal plan/apply error: a corrupt target, or an unsupported schema."""


class ConfigWriteError(ConfigPlanError):
    """A structured write failure with the fields the caller needs to map.

    Attributes:
        kind: ``"file_not_writable"``, ``"dir_not_writable"`` or ``"write"``.
        path: The target path the write was aimed at.
        dir_path: The parent directory (only for ``"dir_not_writable"``).
        errno: OS errno for ``"write"`` (``None`` when unavailable).
        strerror: OS message for ``"write"`` (``None`` when unavailable).
    """

    def __init__(
        self,
        kind: str,
        path: Path,
        *,
        dir_path: Path | None = None,
        errno: int | None = None,
        strerror: str | None = None,
    ) -> None:
        super().__init__(f"{kind}: {path}")
        self.kind = kind
        self.path = path
        self.dir_path = dir_path
        self.errno = errno
        self.strerror = strerror


@dataclass(frozen=True)
class ConfigChange:
    """One dotted-path difference between the base and the planned target."""

    path: str
    before: Any
    after: Any


@dataclass(frozen=True)
class ExternalAction:
    """A cross-system side effect a plan implies.

    Attributes:
        action_id: Idempotency key — stable for a given logical action, used as
            the journal key so a replay never repeats it.
        kind: One of :class:`ActionKind`.
        description: Human-readable summary for the plan preview.
        stage: Ordering group; lower stages run first.
        reversible: Whether the action can be undone locally at all.
        rollback_hint: Manual recovery hint when ``reversible`` is ``False``.
    """

    action_id: str
    kind: str
    description: str
    stage: int = 0
    reversible: bool = False
    rollback_hint: str = ""


@dataclass(frozen=True)
class ConfigPlan:
    """Immutable, versioned description of a config rewrite.

    ``planned_data`` is the exact mapping that would be written; it is excluded
    from ``repr`` so a plan can be logged without dumping a whole config.
    """

    version: int
    target_path: str
    source: str
    base_digest: str
    plan_digest: str
    changes: tuple[ConfigChange, ...]
    external_actions: tuple[ExternalAction, ...]
    rollback: tuple[str, ...]
    planned_data: Mapping[str, Any] = field(repr=False)

    def action(self, action_id: str) -> ExternalAction | None:
        """Return the action with *action_id*, or ``None``."""
        for action in self.external_actions:
            if action.action_id == action_id:
                return action
        return None

    def to_dict(self) -> dict[str, Any]:
        """Serialise the plan for machine output (no raw secrets live here)."""
        return {
            "version": self.version,
            "target_path": self.target_path,
            "source": self.source,
            "base_digest": self.base_digest,
            "plan_digest": self.plan_digest,
            "changes": [
                {"path": change.path, "before": change.before, "after": change.after}
                for change in self.changes
            ],
            "external_actions": [
                {
                    "action_id": action.action_id,
                    "kind": action.kind,
                    "description": action.description,
                    "stage": action.stage,
                    "reversible": action.reversible,
                    "rollback_hint": action.rollback_hint,
                }
                for action in self.external_actions
            ],
            "rollback": list(self.rollback),
        }


@dataclass(frozen=True)
class ActionOutcome:
    """The outcome of one cross-system action during an apply attempt."""

    action_id: str
    kind: str
    stage: int
    status: str
    detail: str = ""


@dataclass(frozen=True)
class ApplyReport:
    """Honest result of :func:`apply_config_plan`.

    ``completed`` is true only when the file reached the planned content *and*
    every external action is done or already present.  Any other state is
    ``not_completed`` and carries :attr:`manual_recovery` steps.
    """

    completed: bool
    rejected: bool
    cancelled: bool
    file_written: bool
    outcomes: tuple[ActionOutcome, ...]
    manual_recovery: tuple[str, ...]
    detail: str

    @property
    def status(self) -> str:
        """``"completed"`` or ``"not_completed"`` — the rendered verdict."""
        return "completed" if self.completed else "not_completed"

    def by_status(self, status: str) -> tuple[ActionOutcome, ...]:
        """Return the outcomes in *status*."""
        return tuple(outcome for outcome in self.outcomes if outcome.status == status)


class ExternalActionExecutor(Protocol):
    """Executes cross-system actions and reports whether they already exist.

    ``is_done`` is the executor's own idempotency check against real system
    state (a remote directory, a service unit, a database namespace); the
    journal is the applier's record.  Consult both before acting.
    """

    def is_done(self, action: ExternalAction) -> bool:
        """Return ``True`` when the action's effect is already present."""
        ...

    def execute(self, action: ExternalAction) -> None:
        """Perform the action; raise to signal failure."""
        ...


# --------------------------------------------------------------------------- #
# Digest + diff
# --------------------------------------------------------------------------- #


def canonical_json(value: Any) -> str:
    """Return the canonical JSON used for every digest in this module."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest_of(data: Mapping[str, Any]) -> str:
    """Return a stable SHA-256 content digest of *data*."""
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


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


def _set_dotted(data: dict[str, Any], path: str, value: Any) -> None:
    parts = [part for part in path.split(".") if part]
    if not parts:
        raise ConfigPlanError("编辑路径不能为空")
    cursor = data
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            if nxt is not None:
                logger.warning("编辑路径 %s 覆盖非映射段 %r", path, nxt)
            nxt = {}
            cursor[part] = nxt
        cursor = nxt
    cursor[parts[-1]] = value


def apply_edits(base: Mapping[str, Any], edits: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deep copy of *base* with dotted-path *edits* applied.

    Unknown fields are preserved by construction: only the exact paths named in
    *edits* are touched, so a plan can never silently drop a key it did not
    intend to change.
    """
    data = copy.deepcopy(dict(base))
    for path, value in edits.items():
        _set_dotted(data, path, copy.deepcopy(value))
    return data


# --------------------------------------------------------------------------- #
# Plan construction
# --------------------------------------------------------------------------- #


def build_config_plan(
    target_path: Path | str,
    base: Mapping[str, Any],
    *,
    edits: Mapping[str, Any] | None = None,
    planned: Mapping[str, Any] | None = None,
    external_actions: tuple[ExternalAction, ...] = (),
    rollback: tuple[str, ...] = (),
    source: str | None = None,
) -> ConfigPlan:
    """Build a versioned :class:`ConfigPlan` (never touches disk).

    Supply either ``edits`` (dotted-path mutations of *base*, the
    unknown-field-preserving route) or a fully-formed ``planned`` mapping.  The
    base digest is the concurrency guard consulted at apply time.

    Args:
        target_path: Raw config file the plan will rewrite.
        base: Current config mapping the plan was built against.
        edits: Dotted-path edits applied to *base* to reach the planned content.
        planned: Explicit planned mapping (mutually exclusive with *edits*).
        external_actions: Cross-system actions the plan implies.
        rollback: Human-readable rollback hints.
        source: Optional config-source label.

    Returns:
        The frozen plan object.

    Raises:
        ConfigPlanError: When both/neither input routes are given, or when an
            edit path is invalid.
    """
    if (edits is None) == (planned is None):
        raise ConfigPlanError("必须且只能提供 edits 或 planned 之一")
    planned_data = (
        apply_edits(base, edits) if edits is not None else copy.deepcopy(dict(planned or {}))
    )
    path = Path(target_path)
    return ConfigPlan(
        version=CONFIG_PLAN_VERSION,
        target_path=str(path),
        source=source or str(path),
        base_digest=digest_of(base),
        plan_digest=digest_of(planned_data),
        changes=diff_data(base, planned_data),
        external_actions=tuple(external_actions),
        rollback=tuple(rollback),
        planned_data=planned_data,
    )


# --------------------------------------------------------------------------- #
# Atomic write + restricted backup (shared semantics with the init wizard)
# --------------------------------------------------------------------------- #


def _default_dumps(data: Any) -> str:
    import yaml

    return str(yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False))


def _default_loads(text: str) -> Any:
    import yaml

    return yaml.safe_load(text)


def _now_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def writable_mode(path: Path, mode: int | None = None) -> int:
    """Pre-flight *path* and return the effective write mode.

    An existing target must pass ``os.access(path, os.W_OK)``; a missing target
    requires a writable parent directory.  ``os.replace`` itself only needs
    directory write permission, so this check is what keeps a read-only target
    from being silently replaced.  The effective mode is the explicit *mode*,
    else the existing target's ``st_mode & 0o777``, else ``0o644`` — a
    restrictive existing file is never widened.

    Raises:
        ConfigWriteError: ``"file_not_writable"`` / ``"dir_not_writable"``.
    """
    if path.exists():
        if not os.access(path, os.W_OK):
            raise ConfigWriteError("file_not_writable", path)
        return mode if mode is not None else path.stat().st_mode & 0o777
    parent = path.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK):
        raise ConfigWriteError("dir_not_writable", path, dir_path=parent)
    return 0o644 if mode is None else mode


def backup_file(path: Path, *, timestamp: str) -> Path:
    """Copy *path* to ``<name>.bak-<timestamp>`` (``-N`` on collision).

    ``shutil.copy2`` preserves the existing file's mode, so the backup is never
    widened either.  The *timestamp* is injected so the caller keeps its own
    (monkeypatchable) clock seam.

    Raises:
        ConfigWriteError: ``"write"`` when the copy fails.
    """
    backup = path.with_name(f"{path.name}.bak-{timestamp}")
    counter = 0
    while backup.exists():
        counter += 1
        backup = path.with_name(f"{path.name}.bak-{timestamp}-{counter}")
    try:
        shutil.copy2(path, backup)
    except OSError as exc:
        raise ConfigWriteError("write", path, errno=exc.errno, strerror=exc.strerror) from exc
    logger.info("已备份 %s -> %s", path, backup)
    return backup


def atomic_replace_text(path: Path, text: str, *, mode: int) -> None:
    """Write *text* to a same-directory temp file, chmod it, then ``os.replace``.

    The temp file is created ``0600`` and chmod'ed to *mode* before the replace
    so the final path never appears world-readable, even briefly.

    Raises:
        ConfigWriteError: ``"write"`` on any OS error; the temp file is removed.
    """
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except OSError as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("临时文件清理失败：%s", tmp_path)
        raise ConfigWriteError("write", path, errno=exc.errno, strerror=exc.strerror) from exc
    logger.info("已写入配置 %s（mode=%04o）", path, mode)


def write_config_file(
    path: Path,
    data: Mapping[str, Any],
    *,
    mode: int | None = None,
    dumps: Callable[[Any], str] | None = None,
    timestamp: str | None = None,
) -> None:
    """Atomically persist *data* to *path*, backing up any prior content.

    Order is the security contract: writability pre-flight → serialise →
    ``.bak`` → temp+replace.  A read-only target therefore aborts before any
    file (including the backup) is created.
    """
    effective_mode = writable_mode(path, mode)
    text = (dumps or _default_dumps)(data)
    if path.exists():
        backup_file(path, timestamp=timestamp or _now_timestamp())
    atomic_replace_text(path, text, mode=effective_mode)


def load_config_file(path: Path, *, loads: Callable[[str], Any] | None = None) -> dict[str, Any]:
    """Load a raw config file.

    A missing or empty file loads as ``{}``.  A parse error or a non-mapping,
    non-``None`` root raises :class:`ConfigPlanError` — a user config file is
    never auto-rewritten or silently discarded.
    """
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigPlanError(f"无法读取配置文件：{path}") from exc
    parse = loads or _default_loads
    try:
        data = parse(text)
    except Exception as exc:  # noqa: BLE001 — yaml error types are third-party
        raise ConfigPlanError(f"配置文件解析失败：{path}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigPlanError(f"配置文件根节点必须是映射，{path} 实际为 {type(data).__name__}")
    return data


# --------------------------------------------------------------------------- #
# Journal (idempotency keys for cross-system actions)
# --------------------------------------------------------------------------- #


def journal_path_for(target_path: Path | str) -> Path:
    """Return the apply-journal path that sits beside *target_path*."""
    path = Path(target_path)
    return path.with_name(f"{path.name}.apply-journal.json")


def _empty_journal(plan: ConfigPlan) -> dict[str, Any]:
    return {
        "version": JOURNAL_VERSION,
        "target": plan.target_path,
        "base_digest": plan.base_digest,
        "plan_digest": plan.plan_digest,
        "actions": {},
        "file": {},
    }


def load_journal(path: Path, plan: ConfigPlan) -> dict[str, Any]:
    """Load the journal for *plan*, resetting it when the plan changed.

    A journal written for a different ``plan_digest`` describes a previous
    attempt and must not be trusted for this one, so a fresh journal is
    returned.  A corrupt journal is likewise treated as absent.
    """
    if not path.exists():
        return _empty_journal(plan)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("应用日志损坏，按全新计划处理：%s", path)
        return _empty_journal(plan)
    if not isinstance(data, dict) or data.get("plan_digest") != plan.plan_digest:
        return _empty_journal(plan)
    data.setdefault("actions", {})
    data.setdefault("file", {})
    return data


def save_journal(path: Path, journal: Mapping[str, Any]) -> None:
    """Atomically persist *journal* (crash-safe; replay resumes from here)."""
    journal = dict(journal)
    journal["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_replace_text(path, json.dumps(journal, ensure_ascii=False, indent=2), mode=0o600)


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #


def _rollback_hints_for_plan(plan: ConfigPlan) -> tuple[str, ...]:
    return tuple(plan.rollback)


def _recovery_lines(
    plan: ConfigPlan,
    outcomes: tuple[ActionOutcome, ...],
    *,
    file_written: bool,
) -> tuple[str, ...]:
    lines: list[str] = []
    unfinished = any(o.status not in (ActionStatus.DONE, ActionStatus.SKIPPED) for o in outcomes)
    if file_written and unfinished:
        lines.append(
            f"配置文件已写入 {plan.target_path}；如需回退，恢复其最近一次 .bak-* 备份"
            "（本计划不承诺跨系统动作的原子回滚）"
        )
    for outcome in outcomes:
        action = plan.action(outcome.action_id)
        description = action.description if action is not None else outcome.action_id
        if outcome.status == ActionStatus.FAILED:
            hint = action.rollback_hint if action is not None and action.rollback_hint else ""
            lines.append(
                f"动作 {outcome.action_id}（{description}）失败：{outcome.detail}；"
                f"修复后重放同一计划将继续，已完成步骤不会重复" + (f"；{hint}" if hint else "")
            )
        elif outcome.status == ActionStatus.PENDING:
            lines.append(
                f"动作 {outcome.action_id}（{description}）尚未执行；"
                f"重放同一计划将从该动作继续（幂等键 {outcome.action_id}）"
            )
    if unfinished:
        lines.extend(_rollback_hints_for_plan(plan))
    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for line in lines:
        if line and line not in seen:
            seen.add(line)
            unique.append(line)
    return tuple(unique)


def _ordered_actions(plan: ConfigPlan) -> tuple[ExternalAction, ...]:
    return tuple(sorted(plan.external_actions, key=lambda action: (action.stage,)))


def apply_config_plan(
    plan: ConfigPlan,
    *,
    executor: ExternalActionExecutor | None = None,
    journal_path: Path | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> ApplyReport:
    """Apply *plan*, staged and idempotently, and report honestly.

    The write is attempted only when the live file still matches the plan's
    base digest; an already-applied plan is a no-op.  Cross-system actions run
    in stage order and are journaled per action, so a replay skips what is
    already done and resumes at the first pending action.  The function returns
    a report for every expected failure (concurrent edit, read-only target,
    corrupt config, cancel, action failure) rather than raising, so the caller
    can always render "completed / not completed" plus manual recovery steps.
    It raises :class:`ConfigPlanError` only for an unsupported schema version.

    Args:
        plan: The plan to apply.
        executor: Executor for cross-system actions.  ``None`` leaves those
            actions ``pending`` (honest partial) instead of pretending.
        journal_path: Override for the journal location (defaults beside target).
        cancel_check: Polled between stages; returning ``True`` stops early and
            reports the remainder as pending.

    Returns:
        The :class:`ApplyReport`.
    """
    if plan.version != CONFIG_PLAN_VERSION:
        raise ConfigPlanError(f"不支持的配置计划版本：{plan.version}")

    path = Path(plan.target_path)
    actions = _ordered_actions(plan)
    journal_path = journal_path or journal_path_for(path)
    journal: dict[str, Any] | None = None

    def _cancelled() -> bool:
        return cancel_check is not None and cancel_check()

    if _cancelled():
        return ApplyReport(
            completed=False,
            rejected=False,
            cancelled=True,
            file_written=False,
            outcomes=tuple(
                ActionOutcome(action.action_id, action.kind, action.stage, ActionStatus.PENDING)
                for action in actions
            ),
            manual_recovery=("应用在写入前取消；未做任何更改，可直接重放同一计划",),
            detail="已取消：应用在写入前被取消",
        )

    try:
        current = load_config_file(path)
    except ConfigPlanError as exc:
        return ApplyReport(
            completed=False,
            rejected=False,
            cancelled=False,
            file_written=False,
            outcomes=tuple(
                ActionOutcome(action.action_id, action.kind, action.stage, ActionStatus.PENDING)
                for action in actions
            ),
            manual_recovery=(f"配置文件损坏（{exc}）；请修复或恢复备份后重新生成计划",),
            detail=f"未完成：目标配置无法读取（{exc}）",
        )

    planned = dict(plan.planned_data)
    file_already = canonical_json(current) == canonical_json(planned)

    if not file_already and digest_of(current) != plan.base_digest:
        return ApplyReport(
            completed=False,
            rejected=True,
            cancelled=False,
            file_written=False,
            outcomes=tuple(
                ActionOutcome(action.action_id, action.kind, action.stage, ActionStatus.PENDING)
                for action in actions
            ),
            manual_recovery=(
                "目标配置在计划生成后被并发修改；未写入任何内容。"
                "请重新运行 plan 以基于最新配置生成新计划",
            ),
            detail="未完成：目标配置已被并发修改，拒绝覆盖",
        )

    if actions:
        journal = load_journal(journal_path, plan)
        journal["base_digest"] = plan.base_digest

    outcomes: list[ActionOutcome] = []
    file_written = False

    if not file_already:
        try:
            write_config_file(path, planned)
        except ConfigWriteError as exc:
            if journal is not None:
                save_journal(journal_path, journal)
            return ApplyReport(
                completed=False,
                rejected=False,
                cancelled=False,
                file_written=False,
                outcomes=tuple(
                    ActionOutcome(action.action_id, action.kind, action.stage, ActionStatus.PENDING)
                    for action in actions
                ),
                manual_recovery=(
                    f"配置写入失败（{exc.kind}）；未执行任何跨系统动作，修复后重放同一计划",
                ),
                detail=f"未完成：配置写入失败（{exc}）",
            )
        file_written = True
        if journal is not None:
            journal["file"] = {"status": "written"}

    if journal is not None:
        journal["file"] = {"status": "written" if file_written else "skipped"}

    halted = False
    for action in actions:
        if halted:
            outcomes.append(
                ActionOutcome(
                    action.action_id, action.kind, action.stage, ActionStatus.PENDING, "前序未完成"
                )
            )
            continue
        record = journal.get("actions", {}).get(action.action_id) if journal is not None else None
        if record is not None and record.get("status") == ActionStatus.DONE:
            outcomes.append(
                ActionOutcome(
                    action.action_id, action.kind, action.stage, ActionStatus.SKIPPED, "日志已记录"
                )
            )
            continue
        if executor is not None and executor.is_done(action):
            if journal is not None:
                journal["actions"][action.action_id] = {
                    "status": ActionStatus.DONE,
                    "kind": action.kind,
                    "stage": action.stage,
                    "detail": "已存在于目标系统",
                }
                save_journal(journal_path, journal)
            outcomes.append(
                ActionOutcome(
                    action.action_id,
                    action.kind,
                    action.stage,
                    ActionStatus.SKIPPED,
                    "目标系统已存在",
                )
            )
            continue
        if _cancelled():
            outcomes.append(
                ActionOutcome(
                    action.action_id, action.kind, action.stage, ActionStatus.PENDING, "已取消"
                )
            )
            halted = True
            continue
        if executor is None:
            outcomes.append(
                ActionOutcome(
                    action.action_id, action.kind, action.stage, ActionStatus.PENDING, "无执行器"
                )
            )
            continue
        try:
            executor.execute(action)
        except Exception as exc:  # noqa: BLE001 — executor is a cross-system boundary
            if journal is not None:
                journal["actions"][action.action_id] = {
                    "status": ActionStatus.FAILED,
                    "kind": action.kind,
                    "stage": action.stage,
                    "detail": str(exc),
                }
                save_journal(journal_path, journal)
            outcomes.append(
                ActionOutcome(
                    action.action_id, action.kind, action.stage, ActionStatus.FAILED, str(exc)
                )
            )
            halted = True
            continue
        if journal is not None:
            journal["actions"][action.action_id] = {
                "status": ActionStatus.DONE,
                "kind": action.kind,
                "stage": action.stage,
            }
            save_journal(journal_path, journal)
        outcomes.append(
            ActionOutcome(action.action_id, action.kind, action.stage, ActionStatus.DONE)
        )

    outcome_tuple = tuple(outcomes)
    all_actions_ok = all(
        outcome.status in (ActionStatus.DONE, ActionStatus.SKIPPED) for outcome in outcome_tuple
    )
    completed = (file_already or file_written) and all_actions_ok

    if completed:
        detail = "已完成：配置与全部跨系统动作均已就绪（重复应用为无副作用幂等）"
    else:
        detail = "未完成：见下方待处理动作与手工恢复步骤"

    return ApplyReport(
        completed=completed,
        rejected=False,
        cancelled=_cancelled(),
        file_written=file_written,
        outcomes=outcome_tuple,
        manual_recovery=_recovery_lines(plan, outcome_tuple, file_written=file_written),
        detail=detail,
    )


def render_apply_report(report: ApplyReport) -> str:
    """Render an :class:`ApplyReport` as the human "已完成/未完成" summary."""
    lines = [
        f"应用结果：{'已完成' if report.completed else '未完成'}",
        report.detail,
    ]
    if report.rejected:
        lines.append("原因：并发修改被拒绝")
    if report.cancelled:
        lines.append("原因：已取消")
    lines.append(f"配置文件写入：{'是' if report.file_written else '否'}")
    if report.outcomes:
        lines.append("跨系统动作：")
        for outcome in report.outcomes:
            lines.append(
                f"  [{outcome.status}] {outcome.action_id}（stage {outcome.stage}）"
                + (f"：{outcome.detail}" if outcome.detail else "")
            )
    if report.manual_recovery:
        lines.append("手工恢复步骤：")
        for step in report.manual_recovery:
            lines.append(f"  - {step}")
    return "\n".join(lines)
