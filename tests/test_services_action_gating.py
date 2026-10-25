"""Action-class authorization + in-flight mode-switch guard (B07-5, todo 39).

Locks the §36.4 security boundary owned by
``acp_services.contracts.action_gating``:

* five explicit action classes — pure config write / dependency install /
  remote bootstrap / system service-user creation / DB migration — each listed
  in the plan and authorized by its own id;
* authorization defaults to deny, and an unauthorized action reaches the real
  executor **zero** times (the gated executor raises before delegating);
* a config-only plan triggers no external action at all;
* changing execution mode / storage root / identity provider / database /
  metering mode is blocked while in-flight tasks or reservations exist, using
  injected judgement material (no scheduler import);
* upgrade never implicitly widens permissions — an unregistered action kind is
  refused instead of guessed.

The suite is dependency-light and hermetic: a ``tmp_path`` config target, an
in-memory fake executor and a static in-flight probe; no remote host, service,
database or live scheduler is touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from acp_services.contracts.action_gating import (
    ACTION_CLASS_LABELS,
    CONFIG_WRITE_ACTION_ID,
    ActionClass,
    ActionGate,
    ActionGatingError,
    ActionNotAuthorizedError,
    AuthorizedActionExecutor,
    InFlightState,
    ModeAxis,
    ModeChange,
    ModeGuardError,
    StaticInFlightProbe,
    action_class_for,
    decide_authorizations,
    guard_mode_switches,
    plan_actions,
    render_authorization,
    render_mode_switch_decision,
)
from acp_services.contracts.config_plan import (
    ActionKind,
    ExternalAction,
    apply_config_plan,
    build_config_plan,
)

_BASE: dict[str, object] = {
    "cluster": {"type": "local"},
    "mystery_unknown_field": {"keep": "me"},
}
_EDITS: dict[str, object] = {"deployment.tier": "lab"}

# One external action per external class, in stage order.  The fifth class
# (pure config write) is implied by the plan itself.
_DB = ExternalAction(
    action_id="db:namespace:acp",
    kind=ActionKind.DATABASE,
    description="创建数据库命名空间 acp",
    stage=0,
)
_DEP = ExternalAction(
    action_id="dep:pip:paramiko",
    kind="dependency_install",
    description="安装依赖 paramiko",
    stage=1,
)
_REMOTE = ExternalAction(
    action_id="fs:remote-dir:/scratch/acp",
    kind=ActionKind.REMOTE_DIR,
    description="创建远端工作目录",
    stage=2,
)
_SERVICE = ExternalAction(
    action_id="svc:acp.service",
    kind=ActionKind.SYSTEM_SERVICE,
    description="创建并启动 acp.service",
    stage=3,
)
_ACTIONS = (_DB, _DEP, _REMOTE, _SERVICE)


class FakeExecutor:
    """Records calls; mimics a real post-condition via ``existing``."""

    def __init__(self, *, existing: set[str] | None = None) -> None:
        self.existing = set(existing or ())
        self.executed: list[str] = []
        self.is_done_calls: list[str] = []

    def is_done(self, action: ExternalAction) -> bool:
        self.is_done_calls.append(action.action_id)
        return action.action_id in self.existing

    def execute(self, action: ExternalAction) -> None:
        self.executed.append(action.action_id)
        self.existing.add(action.action_id)


class ExplodingProbe:
    """A probe that fails if consulted (proves the guard stays lazy)."""

    def snapshot(self) -> InFlightState:  # pragma: no cover - must not run
        raise AssertionError("probe must not be consulted when there is no switch")


def _plan(path: Path, actions: tuple[ExternalAction, ...] = _ACTIONS):
    path.write_text(yaml.dump(_BASE, allow_unicode=True), encoding="utf-8")
    return build_config_plan(
        path,
        _BASE,
        edits=_EDITS,
        external_actions=actions,
        rollback=("恢复最近一次 .bak-* 备份",),
        source="test",
    )


# --------------------------------------------------------------------------- #
# Five action classes
# --------------------------------------------------------------------------- #


def test_five_action_classes_are_classified(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "config.yaml")

    actions = plan_actions(plan)

    assert {action.action_class for action in actions} == set(ActionClass.ALL)
    assert len(ActionClass.ALL) == 5
    assert ACTION_CLASS_LABELS.keys() == set(ActionClass.ALL)
    # The plan's own config write is listed like any other action.
    assert actions[0].action_id == CONFIG_WRITE_ACTION_ID
    assert actions[0].action_class == ActionClass.CONFIG_WRITE
    assert actions[0].external is False
    classes = {action.action_id: action.action_class for action in actions}
    assert classes["dep:pip:paramiko"] == ActionClass.DEPENDENCY_INSTALL
    assert classes["fs:remote-dir:/scratch/acp"] == ActionClass.REMOTE_BOOTSTRAP
    assert classes["svc:acp.service"] == ActionClass.SYSTEM_SERVICE
    assert classes["db:namespace:acp"] == ActionClass.DB_MIGRATION


def test_unregistered_action_kind_is_refused_not_guessed() -> None:
    """Upgrade must not silently widen permissions (no implicit new class)."""
    with pytest.raises(ActionGatingError):
        action_class_for("magic-new-privileged-action")


def test_plan_actions_refuses_unknown_kind(tmp_path: Path) -> None:
    plan = _plan(
        tmp_path / "config.yaml",
        actions=(
            ExternalAction(action_id="x", kind="row-level-security", description="?", stage=0),
        ),
    )
    with pytest.raises(ActionGatingError):
        plan_actions(plan)


# --------------------------------------------------------------------------- #
# Item-by-item authorization matrix
# --------------------------------------------------------------------------- #


def test_authorization_matrix_partitions_by_action_id(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "config.yaml")
    gate = ActionGate(
        {
            CONFIG_WRITE_ACTION_ID: True,
            "db:namespace:acp": True,
            "dep:pip:paramiko": True,
            "fs:remote-dir:/scratch/acp": False,
            # svc:acp.service intentionally absent -> default deny
        }
    )

    decision = gate.authorize_plan(plan)

    assert decision.all_authorized is False
    assert decision.config_write_authorized is True
    assert decision.authorized_external_ids == ("db:namespace:acp", "dep:pip:paramiko")
    assert decision.refused_action_ids == ("fs:remote-dir:/scratch/acp", "svc:acp.service")
    rendered = render_authorization(decision)
    assert "[已授权] db:namespace:acp" in rendered
    assert "[未授权] svc:acp.service" in rendered


def test_authorization_defaults_to_deny(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "config.yaml")

    decision = decide_authorizations(plan_actions(plan), {})

    assert decision.authorized == ()
    assert decision.config_write_authorized is False
    assert set(decision.refused_action_ids) == {
        CONFIG_WRITE_ACTION_ID,
        "db:namespace:acp",
        "dep:pip:paramiko",
        "fs:remote-dir:/scratch/acp",
        "svc:acp.service",
    }


def test_authorization_full_grant(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "config.yaml")
    all_ids = [action.action_id for action in plan_actions(plan)]

    decision = decide_authorizations(plan_actions(plan), dict.fromkeys(all_ids, True))

    assert decision.all_authorized is True
    assert decision.refused_action_ids == ()
    assert decision.config_write_authorized is True


# --------------------------------------------------------------------------- #
# Zero unauthorized execution
# --------------------------------------------------------------------------- #


def test_unauthorized_external_action_executes_zero_times(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    plan = _plan(target)
    gate = ActionGate(
        {
            CONFIG_WRITE_ACTION_ID: True,
            "db:namespace:acp": True,
            "dep:pip:paramiko": True,
            "fs:remote-dir:/scratch/acp": False,
            "svc:acp.service": False,
        }
    )
    decision = gate.authorize_plan(plan)
    inner = FakeExecutor()
    gated = AuthorizedActionExecutor(inner, decision)

    report = apply_config_plan(plan, executor=gated)

    # Only the two authorized ids reached the real executor.
    assert inner.executed == ["db:namespace:acp", "dep:pip:paramiko"]
    assert gated.executed == ["db:namespace:acp", "dep:pip:paramiko"]
    # The first refused action raised before delegating; it is reported.
    assert gated.refused == ["fs:remote-dir:/scratch/acp"]
    assert "fs:remote-dir:/scratch/acp" not in inner.executed
    assert "svc:acp.service" not in inner.executed
    statuses = {o.action_id: o.status for o in report.outcomes}
    assert statuses["db:namespace:acp"] == "done"
    assert statuses["fs:remote-dir:/scratch/acp"] == "failed"
    assert report.completed is False
    refusal = next(o for o in report.outcomes if o.action_id == "fs:remote-dir:/scratch/acp")
    assert "未授权" in refusal.detail


def test_gated_executor_never_claims_unauthorized_done() -> None:
    # Inner believes the remote dir already exists; an unauthorized gate must
    # not let apply skip it as "already present".
    inner = FakeExecutor(existing={"fs:remote-dir:/scratch/acp"})
    decision = decide_authorizations([], {})
    gated = AuthorizedActionExecutor(inner, decision)

    assert gated.is_done(_REMOTE) is False
    assert inner.is_done_calls == []


def test_gated_executor_raises_before_delegating() -> None:
    inner = FakeExecutor()
    decision = decide_authorizations([], {})
    gated = AuthorizedActionExecutor(inner, decision)

    with pytest.raises(ActionNotAuthorizedError) as excinfo:
        gated.execute(_SERVICE)

    assert excinfo.value.action_id == "svc:acp.service"
    assert excinfo.value.action_class == ActionClass.SYSTEM_SERVICE
    assert inner.executed == []
    assert gated.refused == ["svc:acp.service"]


def test_zero_authorization_applies_no_external_action(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    plan = _plan(target)
    inner = FakeExecutor()
    gated = AuthorizedActionExecutor(inner, ActionGate().authorize_plan(plan))

    report = apply_config_plan(plan, executor=gated)

    assert inner.executed == []
    assert inner.is_done_calls == []
    assert report.completed is False
    assert all(o.status in ("failed", "pending") for o in report.outcomes)


# --------------------------------------------------------------------------- #
# Config-only path: no external action
# --------------------------------------------------------------------------- #


def test_config_only_plan_triggers_no_external_action(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    plan = _plan(target, actions=())
    inner = FakeExecutor()
    decision = ActionGate({CONFIG_WRITE_ACTION_ID: True}).authorize_plan(plan)
    assert decision.config_write_authorized is True

    report = apply_config_plan(plan, executor=AuthorizedActionExecutor(inner, decision))

    assert report.completed is True
    assert report.file_written is True
    assert report.outcomes == ()
    assert inner.executed == []
    assert inner.is_done_calls == []
    assert target.exists()


def test_config_write_not_authorized_is_visible(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "config.yaml", actions=())

    decision = ActionGate({}).authorize_plan(plan)

    assert decision.config_write_authorized is False
    assert decision.refused_action_ids == (CONFIG_WRITE_ACTION_ID,)


# --------------------------------------------------------------------------- #
# In-flight mode-switch guard
# --------------------------------------------------------------------------- #

_IN_FLIGHT = StaticInFlightProbe(InFlightState(active_tasks=1))
_RESERVED_ONLY = StaticInFlightProbe(InFlightState(active_tasks=0, reservations=2))
_IDLE = StaticInFlightProbe(InFlightState())


@pytest.mark.parametrize("axis", list(ModeAxis.ALL))
def test_in_flight_blocks_each_mode_switch(axis: str) -> None:
    decision = guard_mode_switches([ModeChange(axis=axis, before="a", after="b")], _IN_FLIGHT)

    assert decision.allowed is False
    assert decision.error_code == "in_flight_blocks_switch"
    assert len(decision.blocked) == 1
    assert decision.blocked[0].axis == axis
    assert decision.migration_required == decision.blocked
    assert decision.state is not None and decision.state.has_in_flight
    assert "阻止" in render_mode_switch_decision(decision)


def test_reservations_alone_block_a_switch() -> None:
    decision = guard_mode_switches(
        [ModeChange(axis=ModeAxis.EXECUTION_MODE, before="remote", after="local")],
        _RESERVED_ONLY,
    )

    assert decision.allowed is False
    assert decision.error_code == "in_flight_blocks_switch"
    assert "预留" in decision.detail


def test_no_in_flight_allows_switch_but_requires_migration() -> None:
    decision = guard_mode_switches(
        [ModeChange(axis=ModeAxis.STORAGE_ROOT, before="/a", after="/b")], _IDLE
    )

    assert decision.allowed is True
    assert decision.blocked == ()
    assert len(decision.migration_required) == 1
    assert decision.migration_required[0].axis == ModeAxis.STORAGE_ROOT


def test_compatible_switch_allowed_during_in_flight() -> None:
    decision = guard_mode_switches(
        [
            ModeChange(
                axis=ModeAxis.METERING_MODE,
                before="quota",
                after="credit",
                compatible_during_inflight=True,
            )
        ],
        _IN_FLIGHT,
    )

    assert decision.allowed is True
    assert decision.blocked == ()
    assert decision.migration_required


def test_partial_block_only_blocks_incompatible() -> None:
    decision = guard_mode_switches(
        [
            ModeChange(axis=ModeAxis.EXECUTION_MODE, before="local", after="remote"),
            ModeChange(
                axis=ModeAxis.METERING_MODE,
                before="quota",
                after="credit",
                compatible_during_inflight=True,
            ),
        ],
        _IN_FLIGHT,
    )

    assert decision.allowed is False
    assert [change.axis for change in decision.blocked] == [ModeAxis.EXECUTION_MODE]
    assert len(decision.migration_required) == 2


def test_no_switch_returns_allowed_without_consulting_probe() -> None:
    decision = guard_mode_switches(
        [ModeChange(axis=ModeAxis.DATABASE, before="sqlite", after="sqlite")],
        ExplodingProbe(),
    )

    assert decision.allowed is True
    assert decision.changed is False
    assert decision.state is None
    assert decision.migration_required == ()


def test_unknown_mode_axis_fails_closed() -> None:
    with pytest.raises(ModeGuardError):
        guard_mode_switches([ModeChange(axis="hard_disk_magic", before="a", after="b")], _IDLE)


# --------------------------------------------------------------------------- #
# Init-wizard adapter (injected probe reuses JobStatus.is_active)
# --------------------------------------------------------------------------- #


def test_active_status_values_match_jobstatus_is_active() -> None:
    from acp.init_wizard.gating import active_status_values
    from acp_services.store.records import JobStatus

    expected = {status.value for status in JobStatus if status.is_active}
    assert set(active_status_values()) == expected
    # Terminal statuses are never in-flight.
    for status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
        assert status.value not in active_status_values()


def test_store_probe_counts_active_tasks_and_reservations() -> None:
    from acp.init_wizard.gating import store_in_flight_probe

    class FakeStore:
        def counts(self) -> dict[str, int]:
            return {"running": 2, "queued": 1, "completed": 5, "failed": 1}

    probe = store_in_flight_probe(FakeStore(), reservations=lambda: 3)
    state = probe.snapshot()

    assert state.active_tasks == 3
    assert state.reservations == 3
    assert state.has_in_flight is True


def test_manager_probe_sums_registry_reservations() -> None:
    from acp.init_wizard.gating import manager_in_flight_probe

    class FakeRegistry:
        reservations = {"node-a": {"job1", "job2"}, "node-b": {"job3"}}

    class FakeManager:
        registry = FakeRegistry()

        class store:  # noqa: N801 - duck-typed nested store
            @staticmethod
            def counts() -> dict[str, int]:
                return {"paused": 1}

    state = manager_in_flight_probe(FakeManager()).snapshot()

    assert state.active_tasks == 1
    assert state.reservations == 3
    assert state.has_in_flight is True
