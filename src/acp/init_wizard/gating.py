"""Init-wizard bridge to the Services action-gating contracts (B07-5, todo 39).

The authorization/switch-guard *contract* lives in
:mod:`acp_services.contracts.action_gating` (pure, no application imports).
This additive module is the application-side adapter the wizard uses, and the
single place that turns real scheduler state into the contract's
:class:`~acp_services.contracts.action_gating.InFlightState`.

It deliberately does **not** edit ``flows.py`` / ``plan.py`` / ``persist.py``
(todos 36/38): downstream Lab-init (todo 52) and Hosted-combination (todo 65)
call these helpers at their own seam.  Nothing here touches disk or a live
scheduler — constructing a probe is cheap and only reads when the guard asks.

Judgement material reuse: "in-flight" is exactly the set of non-terminal
(statuses whose :attr:`JobStatus.is_active` is true) tasks plus held
reservations — the same facts the manager's startup recovery consults.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from acp_services.contracts.action_gating import (
    ACTION_CLASS_LABELS,
    CONFIG_WRITE_ACTION_ID,
    MODE_AXIS_LABELS,
    ActionClass,
    ActionGate,
    ActionGatingError,
    ActionNotAuthorizedError,
    AuthorizationDecision,
    AuthorizedActionExecutor,
    InFlightProbe,
    InFlightState,
    ModeAxis,
    ModeChange,
    ModeGuardError,
    ModeSwitchDecision,
    PlannedAction,
    StaticInFlightProbe,
    action_class_for,
    decide_authorizations,
    guard_mode_switches,
    plan_actions,
    render_authorization,
    render_mode_switch_decision,
)
from acp_services.store.records import JobStatus

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
    "StoreInFlightProbe",
    "action_class_for",
    "active_status_values",
    "decide_authorizations",
    "guard_mode_switches",
    "manager_in_flight_probe",
    "plan_actions",
    "render_authorization",
    "render_mode_switch_decision",
    "store_in_flight_probe",
]


def active_status_values() -> tuple[str, ...]:
    """Return the status *values* that count as in-flight.

    Reuses :attr:`JobStatus.is_active` so the guard can never drift from the
    lifecycle definition the scheduler itself uses.
    """
    return tuple(status.value for status in JobStatus if status.is_active)


class StoreInFlightProbe:
    """In-flight probe backed by a job store's status counts.

    The store only needs ``counts() -> Mapping[str, int]`` (the existing
    :meth:`JobStore.counts`); reservations are an injected count callable so a
    caller can wire the node registry without this module importing it.
    """

    def __init__(
        self,
        store: Any,
        *,
        reservations: Callable[[], int] | None = None,
    ) -> None:
        self._store = store
        self._reservations = reservations or (lambda: 0)
        self._active_values = frozenset(active_status_values())

    def snapshot(self) -> InFlightState:
        """Count active tasks from the store plus injected reservations."""
        counts = self._store.counts() or {}
        active = sum(int(counts.get(value, 0)) for value in self._active_values)
        reservations = max(0, int(self._reservations()))
        return InFlightState(
            active_tasks=active,
            reservations=reservations,
            detail=f"active_statuses={sorted(self._active_values)}",
        )


def store_in_flight_probe(
    store: Any,
    *,
    reservations: Callable[[], int] | None = None,
) -> StoreInFlightProbe:
    """Build a :class:`StoreInFlightProbe` for *store*."""
    return StoreInFlightProbe(store, reservations=reservations)


def manager_in_flight_probe(manager: Any) -> StoreInFlightProbe:
    """Build a probe from a live ``JobManager`` (duck-typed, no import).

    Uses ``manager.store`` for active counts and the node registry's held
    reservations, so the wizard blocks a mode switch exactly while the manager
    can still be holding work.
    """

    def _reservations() -> int:
        registry = getattr(manager, "registry", None)
        reservations = getattr(registry, "reservations", None) or {}
        return sum(len(job_ids) for job_ids in reservations.values())

    return StoreInFlightProbe(getattr(manager, "store", manager), reservations=_reservations)
