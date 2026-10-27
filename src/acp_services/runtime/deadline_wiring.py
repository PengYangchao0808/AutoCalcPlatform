"""Approval deadline → the three termination surfaces (M2-03c / D09 + R03).

One approval deadline has to reach three independent mechanisms:

============================  ==================================================
surface                        consumer
============================  ==================================================
local process group            :func:`~acp_services.runtime.local_admission.
                               LocalAdmission.request_for_spec` clamps the
                               admission-bound duration (the enforcer itself is
                               the runner's process group, M4-03 evidence)
CCCP subprocess timeout        ``TaskResources.timeout_s`` through
                               :func:`apply_to_task_resources`
LSF ``-W``                     the Controller's LSF script generator
                               (``derive_lsf_resources(walltime_minutes=...)``)
============================  ==================================================

Before this module the value was derived per surface: the admission used the
spec's ``resources.walltime_s``, the CCCP request took whatever the P2 form
declared, and the LSF script used the cluster default.  The approval deadline
reached none of them.

What stays unchanged (and is asserted by the M2-03c tests)
---------------------------------------------------------

Wiring a surface is **not** evidence that it terminates anything:

* ``walltime_admission_bound`` stays ``enforced`` (a request wider than the
  ceiling is refused before dispatch);
* ``walltime_process_termination`` and ``queue_deadline`` stay ``NOT_VERIFIED``
  until M4-03 produces a real termination/expiry receipt;
* ``recipe_step`` / ``qc_invocation`` stay ``recorded-only`` until M2-04;
* ``model_request`` stays ``recorded-only`` until M3-03.

Isolation: stdlib + first-party ``acp_services`` only (no ``acp`` import).

Author: ACP Services (B lane, M2-03c).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from acp_services.runtime.execution_limits import (
    LIMIT_ADMISSION_BOUND,
    LIMIT_PROCESS_TERMINATION,
    LIMIT_QUEUE_DEADLINE,
    ResourceLimits,
    normalize_walltime_s,
    resolve_resource_limits,
)
from acp_services.runtime.research_budget import BudgetCapability

__all__ = [
    "DEADLINE_SURFACES",
    "DEADLINE_WIRING_SCHEMA",
    "DeadlineWiring",
    "apply_to_task_resources",
    "deadline_wiring",
    "deadline_wiring_statement",
    "lsf_walltime_string",
]

#: Contract identity of the deadline wiring projection.
DEADLINE_WIRING_SCHEMA = "acp2.runtime.deadline-wiring.v1"

WIRING_WIRED = "wired"
WIRING_NOT_WIRED = "not-wired"


@dataclass(frozen=True, slots=True)
class DeadlineSurface:
    """One termination surface, its consumer and its truthful capability."""

    surface: str
    #: The execution-limit *kind* this surface implements
    #: (``admission-bound`` / ``process-termination`` / ``queue-deadline``).
    kind: str
    carrier: str
    consumer: str
    wiring: str
    capability: str
    evidence: str
    upgrade_gate: str = ""

    @property
    def wired(self) -> bool:
        return self.wiring == WIRING_WIRED

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "kind": self.kind,
            "carrier": self.carrier,
            "consumer": self.consumer,
            "wiring": self.wiring,
            "wired": self.wired,
            "capability": self.capability,
            "evidence": self.evidence,
            "upgrade_gate": self.upgrade_gate,
        }


#: The frozen surface table.  ``wiring`` says the value reaches the mechanism;
#: ``capability`` says whether the platform has *evidence* that the mechanism
#: acts.  The second never follows from the first.
DEADLINE_SURFACES: tuple[DeadlineSurface, ...] = (
    DeadlineSurface(
        surface="local_admission",
        kind=LIMIT_ADMISSION_BOUND,
        carrier="AdmissionRequest.duration_s (clamped by the approval wall bound)",
        consumer="LocalAdmission.request_for_spec / _ControllerReservationPort.reserve_for_claim",
        wiring=WIRING_WIRED,
        capability=BudgetCapability.ENFORCED,
        evidence="tests/test_acp2_m203_deadline_projection.py (refused before dispatch)",
    ),
    DeadlineSurface(
        surface="local_process_group",
        kind=LIMIT_PROCESS_TERMINATION,
        carrier="local_terminate_after_s (same resolver value)",
        consumer="JobRunner process group (killpg) — consumer lands with M4-03",
        wiring=WIRING_WIRED,
        capability=BudgetCapability.NOT_VERIFIED,
        evidence="wiring only: no termination receipt exists yet",
        upgrade_gate="M4-03 (real local process-group termination receipt)",
    ),
    DeadlineSurface(
        surface="cccp_subprocess",
        kind=LIMIT_PROCESS_TERMINATION,
        carrier="TaskResources.timeout_s (cccp resources.timeout_s)",
        consumer="runtime.deadline_wiring.apply_to_task_resources",
        wiring=WIRING_WIRED,
        capability=BudgetCapability.NOT_VERIFIED,
        evidence="subprocess-level only; wiring tested against cccp TaskResources",
        upgrade_gate="M4-03 (real subprocess timeout receipt)",
    ),
    DeadlineSurface(
        surface="remote_lsf",
        kind=LIMIT_PROCESS_TERMINATION,
        carrier="bsub -W HH:MM (lsf_walltime_minutes from the same resolver)",
        consumer="Controller LSF script generator derive_lsf_resources(walltime_minutes=...)",
        wiring=WIRING_WIRED,
        capability=BudgetCapability.NOT_VERIFIED,
        evidence="argument mapping only; no LSF queue/submission in this environment",
        upgrade_gate="M4-03 (real LSF -W plus a terminal LSF state)",
    ),
    DeadlineSurface(
        surface="queue_clock",
        kind=LIMIT_QUEUE_DEADLINE,
        carrier="queue_deadline_s (persistent absolute deadline at admission)",
        consumer="runtime.execution_limits.compute_attempt_clock",
        wiring=WIRING_WIRED,
        capability=BudgetCapability.NOT_VERIFIED,
        evidence="clock computed; expiry -> terminal transition receipt pending",
        upgrade_gate="M4-03 (expiry -> terminal transition receipt)",
    ),
)


def lsf_walltime_string(minutes: int | None) -> str | None:
    """Render an LSF ``-W`` value (``HH:MM``) for a positive minute count.

    ``None`` means "not limited" and is never rendered as ``00:00`` (a zero
    walltime would make bsub reject the job, not run it unbounded).
    """
    if minutes is None:
        return None
    value = int(minutes)
    if value <= 0:
        return None
    hours, rest = divmod(value, 60)
    return f"{hours:02d}:{rest:02d}"


@dataclass(frozen=True, slots=True)
class DeadlineWiring:
    """The resolved deadline on every surface plus its truthful capability."""

    resources: ResourceLimits = field(default_factory=ResourceLimits)
    cluster_walltime_s: int | None = None
    surfaces: tuple[DeadlineSurface, ...] = DEADLINE_SURFACES

    @property
    def local_terminate_after_s(self) -> int | None:
        return self.resources.local_terminate_after_s

    @property
    def ccp_resources_timeout_s(self) -> float | None:
        return self.resources.ccp_resources_timeout_s

    @property
    def lsf_walltime_minutes(self) -> int | None:
        return self.resources.lsf_walltime_minutes

    @property
    def lsf_walltime(self) -> str | None:
        return lsf_walltime_string(self.resources.lsf_walltime_minutes)

    @property
    def queue_deadline_s(self) -> int | None:
        return self.resources.queue_deadline_s

    @property
    def bounded(self) -> bool:
        return bool(self.resources.bounded)

    def capability(self, surface: str) -> str:
        for row in self.surfaces:
            if row.surface == surface:
                return row.capability
        raise KeyError(surface)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": DEADLINE_WIRING_SCHEMA,
            "local_terminate_after_s": self.local_terminate_after_s,
            "ccp_resources_timeout_s": self.ccp_resources_timeout_s,
            "lsf_walltime_minutes": self.lsf_walltime_minutes,
            "lsf_walltime": self.lsf_walltime,
            "queue_deadline_s": self.queue_deadline_s,
            "bounded": self.bounded,
            "cluster_walltime_s": self.cluster_walltime_s,
            "recorded_only": dict(self.resources.recorded_only),
            "surfaces": [row.to_dict() for row in self.surfaces],
            "enforced": [
                row.surface for row in self.surfaces if row.capability == BudgetCapability.ENFORCED
            ],
            "not_verified": [
                {"surface": row.surface, "reason": row.evidence}
                for row in self.surfaces
                if row.capability == BudgetCapability.NOT_VERIFIED
            ],
        }


def deadline_wiring(
    budget: Mapping[str, Any] | None,
    *,
    cluster_walltime_s: int | None = None,
) -> DeadlineWiring:
    """Resolve one approval/proposal budget onto the three termination surfaces.

    Args:
        budget: The approval budget (D09 shape) or any accepted budget
            vocabulary; an empty budget means "not bounded on any surface".
        cluster_walltime_s: The deployment/cluster walltime, when the caller has
            one.  It only ever *narrows* the resolved value; an empty cluster
            walltime is "not limited" and never becomes the limit.

    Returns:
        The :class:`DeadlineWiring`.  Values are ``None`` when unbounded — a
        surface that is not bounded is never published as a limit.
    """
    payload: dict[str, Any] = dict(budget or {})
    resolved = resolve_resource_limits(payload, timeout_override_s=cluster_walltime_s)
    return DeadlineWiring(
        resources=resolved,
        cluster_walltime_s=normalize_walltime_s(cluster_walltime_s),
    )


def apply_to_task_resources(
    resources: Mapping[str, Any] | None,
    wiring: DeadlineWiring,
) -> dict[str, Any]:
    """Apply the resolved deadline to one CCCP ``TaskResources`` payload.

    The existing ``timeout_s`` is only ever narrowed: a P2 form that declares a
    shorter subprocess timeout keeps it, and an absent one takes the approval
    deadline.  An unbounded wiring adds nothing (no invented default).
    """
    payload = dict(resources or {})
    deadline = wiring.ccp_resources_timeout_s
    if deadline is None:
        return payload
    current = payload.get("timeout_s")
    if current is None:
        payload["timeout_s"] = float(deadline)
        return payload
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        # A malformed explicit value is the request validator's answer, not ours.
        return payload
    payload["timeout_s"] = float(min(float(current), float(deadline)))
    return payload


def deadline_wiring_statement() -> dict[str, Any]:
    """The machine-readable wiring publication (surfaces, wiring, capability)."""
    return {
        "schema": DEADLINE_WIRING_SCHEMA,
        "resolver": "acp_services.runtime.execution_limits.resolve_resource_limits",
        "surfaces": [row.to_dict() for row in DEADLINE_SURFACES],
        "wired": [row.surface for row in DEADLINE_SURFACES if row.wired],
        "enforced": [
            row.surface for row in DEADLINE_SURFACES if row.capability == BudgetCapability.ENFORCED
        ],
        "not_verified": [
            {"surface": row.surface, "reason": row.evidence, "upgrade_gate": row.upgrade_gate}
            for row in DEADLINE_SURFACES
            if row.capability == BudgetCapability.NOT_VERIFIED
        ],
        "rule": (
            "wiring is not termination evidence: a surface stays NOT_VERIFIED until its "
            "receipt exists (M4-03)"
        ),
    }
