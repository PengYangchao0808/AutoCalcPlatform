"""Versioned config plan + staged, idempotent apply (todo 38, B07-4).

Locks the four promises of ``acp_services.contracts.config_plan``:

* the plan is versioned and digested (base + planned content), with a flattened
  diff, external-action list and rollback hints;
* apply refuses a concurrently modified target, never widens a restrictive
  file's mode, and writes atomically through a timestamped ``.bak-*`` backup;
* cross-system actions are staged with idempotency keys, journaled as they
  happen, and replayed without repeating an already-completed action (the
  executor's own ``is_done`` is consulted too);
* the result is an honest ``completed`` / ``not_completed`` report with manual
  recovery steps — a partial apply is never dressed up as an atomic rollback.

The suite is deliberately dependency-light: it uses a fake executor and a
``tmp_path`` target, and never touches a real remote host, service or DB.
"""

from __future__ import annotations

import os
import stat
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from acp_services.contracts.config_plan import (
    CONFIG_PLAN_VERSION,
    ActionKind,
    ActionStatus,
    ApplyReport,
    ConfigPlanError,
    ExternalAction,
    apply_config_plan,
    build_config_plan,
    journal_path_for,
    load_config_file,
    load_journal,
    render_apply_report,
    write_config_file,
)

_BASE: dict[str, object] = {
    "cluster": {"type": "local"},
    "executables": {"xtb": {"path": "/opt/xtb/bin/xtb"}},
    "mystery_unknown_field": {"keep": "me"},
}

_EDITS: dict[str, object] = {
    "deployment.tier": "lab",
    "deployment.components": ["api", "worker"],
}

_ACTIONS = (
    ExternalAction(
        action_id="db:namespace:acp",
        kind=ActionKind.DATABASE,
        description="创建数据库命名空间 acp",
        stage=0,
        rollback_hint="手工删除数据库命名空间 acp",
    ),
    ExternalAction(
        action_id="fs:remote-dir:/scratch/acp",
        kind=ActionKind.REMOTE_DIR,
        description="创建远端工作目录",
        stage=1,
        rollback_hint="手工删除远端目录 /scratch/acp",
    ),
    ExternalAction(
        action_id="svc:acp.service",
        kind=ActionKind.SYSTEM_SERVICE,
        description="安装并启动 acp.service",
        stage=2,
        rollback_hint="systemctl disable --now acp.service",
    ),
)


class FakeExecutor:
    """Records calls and enforces per-action idempotency like a real system."""

    def __init__(self, *, existing: set[str] | None = None, fail_on: set[str] | None = None):
        self.existing = set(existing or ())
        self.fail_on = set(fail_on or ())
        self.executed: list[str] = []
        self.is_done_calls: list[str] = []

    def is_done(self, action: ExternalAction) -> bool:
        self.is_done_calls.append(action.action_id)
        return action.action_id in self.existing

    def execute(self, action: ExternalAction) -> None:
        if action.action_id in self.fail_on:
            raise RuntimeError(f"boom: {action.action_id}")
        self.executed.append(action.action_id)
        self.existing.add(action.action_id)


def _write(path: Path, data: object) -> None:
    path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")


def _plan(path: Path, *, actions: tuple[ExternalAction, ...] = (), base: dict | None = None):
    return build_config_plan(
        path,
        _BASE if base is None else base,
        edits=_EDITS,
        external_actions=actions,
        rollback=("恢复最近一次 .bak-* 备份", "跨系统动作需手工撤销"),
        source="test",
    )


def _apply(path: Path, plan, **kwargs):
    kwargs.setdefault("journal_path", journal_path_for(path.with_suffix(".journal")))
    return apply_config_plan(plan, **kwargs)


# --------------------------------------------------------------------------- #
# Plan object: version / digests / diff / unknown fields
# --------------------------------------------------------------------------- #


def test_plan_is_versioned_and_digested(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    _write(target, _BASE)

    plan = _plan(target)

    assert plan.version == CONFIG_PLAN_VERSION
    assert plan.base_digest and plan.plan_digest
    assert plan.base_digest != plan.plan_digest
    assert plan.source == "test"
    assert plan.rollback
    # Flattened dotted-path diff, not a whole-config dump.
    paths = {change.path for change in plan.changes}
    assert "deployment.tier" in paths
    assert "deployment.components" in paths


def test_plan_to_dict_has_all_contract_fields(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "config.yaml", actions=_ACTIONS)

    payload = plan.to_dict()

    assert payload["version"] == CONFIG_PLAN_VERSION
    assert payload["base_digest"] == plan.base_digest
    assert payload["plan_digest"] == plan.plan_digest
    assert [a["action_id"] for a in payload["external_actions"]] == [
        action.action_id for action in _ACTIONS
    ]
    assert payload["rollback"] == list(plan.rollback)
    assert "planned_data" not in payload


def test_unknown_fields_are_preserved_never_silently_dropped(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    _write(target, _BASE)
    plan = _plan(target)

    assert plan.planned_data["mystery_unknown_field"] == {"keep": "me"}

    report = _apply(target, plan)

    assert report.completed is True
    on_disk = load_config_file(target)
    assert on_disk["mystery_unknown_field"] == {"keep": "me"}
    assert on_disk["executables"]["xtb"]["path"] == "/opt/xtb/bin/xtb"
    assert on_disk["deployment"]["tier"] == "lab"


def test_unsupported_plan_version_raises(tmp_path: Path) -> None:
    plan = replace(_plan(tmp_path / "config.yaml"), version=CONFIG_PLAN_VERSION + 1)

    with pytest.raises(ConfigPlanError):
        apply_config_plan(plan)


def test_build_plan_requires_exactly_one_input_route(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    with pytest.raises(ConfigPlanError):
        build_config_plan(target, _BASE)
    with pytest.raises(ConfigPlanError):
        build_config_plan(target, _BASE, edits=_EDITS, planned={"a": 1})


# --------------------------------------------------------------------------- #
# Concurrent modification + corrupt config + read-only target
# --------------------------------------------------------------------------- #


def test_concurrent_modification_is_rejected_without_writing(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    _write(target, _BASE)
    plan = _plan(target)

    # A concurrent writer changes the raw file after the plan was built.
    _write(target, {**_BASE, "concurrent": "edit"})
    before = target.read_text(encoding="utf-8")

    report = _apply(target, plan, executor=FakeExecutor())

    assert report.rejected is True
    assert report.completed is False
    assert report.file_written is False
    assert target.read_text(encoding="utf-8") == before
    assert any("并发" in step for step in report.manual_recovery)
    assert "并发修改被拒绝" in render_apply_report(report)


def test_concurrent_modification_is_rejected_even_when_actions_would_run(
    tmp_path: Path,
) -> None:
    target = tmp_path / "config.yaml"
    _write(target, _BASE)
    plan = _plan(target, actions=_ACTIONS)
    executor = FakeExecutor()
    _write(target, {**_BASE, "concurrent": "edit"})

    report = _apply(target, plan, executor=executor)

    assert report.rejected is True
    # No cross-system side effect may happen when the config was not written.
    assert executor.executed == []
    assert not journal_path_for(target.with_suffix(".journal")).exists()


def test_corrupt_config_aborts_without_overwriting(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    target.write_text("cluster: [unclosed\n", encoding="utf-8")
    plan = _plan(target)

    report = _apply(target, plan, executor=FakeExecutor())

    assert report.completed is False
    assert report.rejected is False
    assert report.file_written is False
    assert target.read_text(encoding="utf-8") == "cluster: [unclosed\n"
    assert any("损坏" in step for step in report.manual_recovery)


def test_read_only_target_aborts_before_backup(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    _write(target, _BASE)
    target.chmod(0o444)
    if os.access(target, os.W_OK):
        target.chmod(0o644)
        pytest.skip("current user bypasses file write permission (e.g. root)")
    plan = _plan(target)
    try:
        report = _apply(target, plan, executor=FakeExecutor())
    finally:
        target.chmod(0o644)

    assert report.completed is False
    assert report.file_written is False
    assert "file_not_writable" in report.detail
    # The pre-flight must run before the backup is created.
    assert list(tmp_path.glob("config.yaml.bak-*")) == []


def test_write_is_atomic_and_backup_keeps_restrictive_mode(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    _write(target, _BASE)
    target.chmod(0o600)
    plan = _plan(target)

    report = _apply(target, plan)

    assert report.file_written is True
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    backups = list(tmp_path.glob("config.yaml.bak-*"))
    assert len(backups) == 1
    # copy2 preserves the source mode — the backup is never widened.
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert not list(tmp_path.glob("config.yaml.tmp-*"))


# --------------------------------------------------------------------------- #
# Staged, idempotent cross-system actions
# --------------------------------------------------------------------------- #


def test_actions_run_in_stage_order_and_are_journaled(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    _write(target, _BASE)
    plan = _plan(target, actions=_ACTIONS)
    executor = FakeExecutor()
    journal = journal_path_for(target.with_suffix(".journal"))

    report = _apply(target, plan, executor=executor)

    assert report.completed is True
    assert executor.executed == [action.action_id for action in _ACTIONS]
    assert [o.status for o in report.outcomes] == [ActionStatus.DONE] * len(_ACTIONS)
    persisted = load_journal(journal, plan)
    assert set(persisted["actions"]) == {action.action_id for action in _ACTIONS}
    assert persisted["file"]["status"] == "written"


def test_apply_is_idempotent_on_replay(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    _write(target, _BASE)
    plan = _plan(target, actions=_ACTIONS)
    executor = FakeExecutor()

    first = _apply(target, plan, executor=executor)
    side_effects_after_first = list(executor.executed)
    backups_after_first = list(tmp_path.glob("config.yaml.bak-*"))

    second = _apply(target, plan, executor=executor)

    assert first.completed is True
    assert second.completed is True
    assert second.file_written is False  # already-on-disk plan is a no-op
    assert executor.executed == side_effects_after_first  # no duplicate creation
    assert list(tmp_path.glob("config.yaml.bak-*")) == backups_after_first
    assert [o.status for o in second.outcomes] == [ActionStatus.SKIPPED] * len(_ACTIONS)


def test_executor_is_done_prevents_recreating_existing_systems(tmp_path: Path) -> None:
    """A journal-less replay still trusts the executor's own existence check."""
    target = tmp_path / "config.yaml"
    _write(target, _BASE)
    plan = _plan(target, actions=_ACTIONS)
    executor = FakeExecutor(existing={"db:namespace:acp"})

    report = _apply(target, plan, executor=executor)

    assert report.completed is True
    assert executor.executed == ["fs:remote-dir:/scratch/acp", "svc:acp.service"]
    db_outcome = next(o for o in report.outcomes if o.action_id == "db:namespace:acp")
    assert db_outcome.status == ActionStatus.SKIPPED


def test_journal_is_reset_when_the_plan_changes(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    _write(target, _BASE)
    failing = FakeExecutor(fail_on={"fs:remote-dir:/scratch/acp"})
    _apply(target, _plan(target, actions=_ACTIONS), executor=failing)

    changed = build_config_plan(
        target,
        load_config_file(target),
        edits={"deployment.components": ["worker"]},
        external_actions=_ACTIONS,
    )
    fresh = FakeExecutor()
    report = _apply(target, changed, executor=fresh)

    # A new plan digest must not inherit the old plan's "done" records.
    assert report.completed is True
    assert fresh.executed == [action.action_id for action in _ACTIONS]


# --------------------------------------------------------------------------- #
# Partial completion + cancel + honest reporting
# --------------------------------------------------------------------------- #


def test_partial_completion_reports_failed_and_pending(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    _write(target, _BASE)
    plan = _plan(target, actions=_ACTIONS)
    executor = FakeExecutor(fail_on={"fs:remote-dir:/scratch/acp"})

    report = _apply(target, plan, executor=executor)

    assert report.completed is False
    assert report.status == "not_completed"
    assert report.file_written is True  # config write happened; no rollback claimed
    statuses = {o.action_id: o.status for o in report.outcomes}
    assert statuses["db:namespace:acp"] == ActionStatus.DONE
    assert statuses["fs:remote-dir:/scratch/acp"] == ActionStatus.FAILED
    assert statuses["svc:acp.service"] == ActionStatus.PENDING
    assert executor.executed == ["db:namespace:acp"]

    rendered = render_apply_report(report)
    assert "应用结果：未完成" in rendered
    assert "手工恢复步骤" in rendered
    assert "重放同一计划" in rendered
    # Honest: never claims an atomic rollback of cross-system actions.
    assert "不承诺跨系统动作的原子回滚" in rendered or any(
        "不承诺" in step for step in report.manual_recovery
    )


def test_partial_apply_can_resume_by_replaying_same_plan(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    _write(target, _BASE)
    plan = _plan(target, actions=_ACTIONS)
    bad = FakeExecutor(fail_on={"fs:remote-dir:/scratch/acp"})
    _apply(target, plan, executor=bad)

    good = FakeExecutor(existing={"db:namespace:acp"})
    report = _apply(target, plan, executor=good)

    assert report.completed is True
    # The already-done DB action is never repeated; the failed one resumes.
    assert "db:namespace:acp" not in good.executed
    assert good.executed == ["fs:remote-dir:/scratch/acp", "svc:acp.service"]


def test_actions_without_executor_are_reported_pending(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    _write(target, _BASE)
    plan = _plan(target, actions=_ACTIONS)

    report = _apply(target, plan)

    assert report.completed is False
    assert report.file_written is True
    assert all(o.status == ActionStatus.PENDING for o in report.outcomes)
    assert any("无执行器" in o.detail for o in report.outcomes)


def test_cancel_before_apply_touches_nothing(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    _write(target, _BASE)
    plan = _plan(target, actions=_ACTIONS)
    before = target.read_text(encoding="utf-8")

    report = _apply(target, plan, executor=FakeExecutor(), cancel_check=lambda: True)

    assert report.cancelled is True
    assert report.completed is False
    assert report.file_written is False
    assert target.read_text(encoding="utf-8") == before
    assert "未做任何更改" in report.manual_recovery[0]


def test_cancel_mid_apply_stops_remaining_actions_and_reports_pending(
    tmp_path: Path,
) -> None:
    target = tmp_path / "config.yaml"
    _write(target, _BASE)
    plan = _plan(target, actions=_ACTIONS)
    state = {"cancelled": False}

    class CancellingExecutor(FakeExecutor):
        def execute(self, action: ExternalAction) -> None:
            super().execute(action)
            state["cancelled"] = True

    executor = CancellingExecutor()
    report = _apply(target, plan, executor=executor, cancel_check=lambda: state["cancelled"])

    assert report.completed is False
    assert report.cancelled is True
    assert executor.executed == ["db:namespace:acp"]
    statuses = {o.action_id: o.status for o in report.outcomes}
    assert statuses["db:namespace:acp"] == ActionStatus.DONE
    assert statuses["fs:remote-dir:/scratch/acp"] == ActionStatus.PENDING
    assert statuses["svc:acp.service"] == ActionStatus.PENDING
    assert "已取消" in render_apply_report(report)


def test_nothing_to_do_is_a_completed_noop(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    _write(target, _BASE)
    # A plan whose planned content equals the current content.
    plan = build_config_plan(target, _BASE, planned=_BASE)
    executor = FakeExecutor()

    report = _apply(target, plan, executor=executor)

    assert report.completed is True
    assert report.file_written is False
    assert report.manual_recovery == ()
    assert list(tmp_path.glob("config.yaml.bak-*")) == []


def test_report_is_frozen_and_status_property() -> None:
    report = ApplyReport(
        completed=False,
        rejected=False,
        cancelled=False,
        file_written=False,
        outcomes=(),
        manual_recovery=(),
        detail="x",
    )
    assert report.status == "not_completed"
    with pytest.raises(Exception):
        report.completed = True  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Write primitive edge cases (shared semantics with the init wizard)
# --------------------------------------------------------------------------- #


def test_write_config_file_rejects_missing_parent(tmp_path: Path) -> None:
    from acp_services.contracts.config_plan import ConfigWriteError

    missing = tmp_path / "absent" / "config.yaml"
    with pytest.raises(ConfigWriteError) as excinfo:
        write_config_file(missing, {"a": 1})
    assert excinfo.value.kind == "dir_not_writable"


def test_missing_target_is_created_with_default_mode(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"

    report = _apply(target, _plan(target, base={}))

    assert report.completed is True
    assert target.exists()
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
