"""One enforcement projection: capability, wiring and the next upgrade gate (M2-03c).

D09 requires *one* matrix, not one per surface, and G13 requires that a
capability is only upgraded by the checkpoint that actually delivers the
evidence.  This module composes the four authorities into one machine-readable
projection, and names for every non-final row the task that may upgrade it:

===================================  ==============================  =================
row                                   capability now                  upgrade gate
===================================  ==============================  =================
``task_ref`` / ``attempt``            enforced                        —
``recipe_step`` / ``qc_invocation``   recorded-only                   M2-04 (DAG dispatch)
``bsub``                              recorded-only                   M4-03 (real LSF)
``model_request``                     recorded-only                   M3-03 (gateway)
``walltime_admission_bound``          enforced                        —
``walltime_process_termination``      enforced (M4-03 receipts)       closed
``queue_deadline``                    enforced (M4-03 receipts)       closed
``cumulative_core_hours``/tokens      recorded-only                   (out of scope)
===================================  ==============================  =================

No row is upgraded by *being wired*: M2-03c wires the surfaces
(:mod:`acp_services.runtime.deadline_wiring`) and keeps every claim truthful.
A test that asserts "recipe_step is enforced" must fail today, which is why
:func:`blocked_upgrades` exists.

Isolation: stdlib + first-party ``acp_services`` only (no ``acp`` import).

Author: ACP Services (B lane, M2-03c).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from acp_services.runtime.approval_policy import (
    approval_policy_statement,
)
from acp_services.runtime.deadline_wiring import deadline_wiring_statement
from acp_services.runtime.execution_limits import (
    EXECUTION_LIMITS,
    execution_limits_statement,
)
from acp_services.runtime.research_budget import (
    UNIT_CAPABILITIES,
    BudgetCapability,
)

__all__ = [
    "ENFORCEMENT_PROJECTION_SCHEMA",
    "UPGRADE_GATES",
    "UpgradeGate",
    "blocked_upgrades",
    "enforcement_projection",
    "unit_capability_row",
]

#: Contract identity of the composed projection.
ENFORCEMENT_PROJECTION_SCHEMA = "acp2.runtime.enforcement-projection.v1"


@dataclass(frozen=True, slots=True)
class UpgradeGate:
    """The only task that may upgrade one row, and the evidence it needs."""

    row: str
    capability: str
    gate: str
    evidence: str

    @property
    def upgradeable(self) -> bool:
        return self.capability == BudgetCapability.ENFORCED

    def to_dict(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "capability": self.capability,
            "upgradeable": self.upgradeable,
            "gate": self.gate,
            "evidence": self.evidence,
        }


#: The frozen upgrade gates.  A capability may only move to ``enforced`` through
#: the named task; wiring, a code path, or an env flag is never evidence.
UPGRADE_GATES: tuple[UpgradeGate, ...] = (
    UpgradeGate(
        row="recipe_step",
        capability=BudgetCapability.RECORDED_ONLY,
        gate="M2-04 (bounded recipe DAG dispatch)",
        evidence="a real per-step quota fetch before the backend call",
    ),
    UpgradeGate(
        row="qc_invocation",
        capability=BudgetCapability.RECORDED_ONLY,
        gate="M2-04 (worst-case executions)",
        evidence="worst_case_executions compiled by CCCP plus a TaskContext meter",
    ),
    UpgradeGate(
        row="model_request",
        capability=BudgetCapability.RECORDED_ONLY,
        gate="M3-03 (controller model gateway)",
        evidence="a zero-call refusal at the gateway before the provider call",
    ),
    UpgradeGate(
        row="bsub",
        capability=BudgetCapability.RECORDED_ONLY,
        gate="M4-03 (real LSF submission)",
        evidence="a real bsub receipt counted before submission",
    ),
    # walltime_process_termination and queue_deadline gates are CLOSED by M4-03
    # real receipts (local process-group kill, native RUNLIMIT via #BSUB -W, and
    # a 40s queue-deadline expiry on a real PEND job); both rows ship as
    # enforced and no longer gate anything.
)

_GATES_BY_ROW = {gate.row: gate for gate in UPGRADE_GATES}


def unit_capability_row(unit: str) -> dict[str, Any]:
    """Return one unit row with its capability and upgrade gate."""
    capability = str(UNIT_CAPABILITIES.get(unit, BudgetCapability.RECORDED_ONLY))
    gate = _GATES_BY_ROW.get(unit)
    return {
        "unit": unit,
        "capability": capability,
        "upgrade_gate": None if gate is None else gate.gate,
        "upgradeable_now": capability == BudgetCapability.ENFORCED,
    }


def blocked_upgrades() -> dict[str, str]:
    """Rows whose capability must **not** be presented as enforced yet."""
    blocked: dict[str, str] = {}
    for unit, capability in UNIT_CAPABILITIES.items():
        if capability != BudgetCapability.ENFORCED:
            gate = _GATES_BY_ROW.get(unit)
            blocked[str(unit)] = gate.gate if gate is not None else "no upgrade path in scope"
    for row in EXECUTION_LIMITS:
        if row.capability != BudgetCapability.ENFORCED:
            gate = _GATES_BY_ROW.get(row.limit)
            blocked[str(row.limit)] = (
                gate.gate if gate is not None else row.evidence or "recorded-only by design"
            )
    return blocked


def enforcement_projection() -> dict[str, Any]:
    """The composed, truthful enforcement projection for one capability read."""
    units = [unit_capability_row(unit) for unit in UNIT_CAPABILITIES]
    return {
        "schema": ENFORCEMENT_PROJECTION_SCHEMA,
        "units": units,
        "execution_limits": execution_limits_statement(),
        "approval_policy": approval_policy_statement(),
        "deadline_wiring": deadline_wiring_statement(),
        "upgrade_gates": [gate.to_dict() for gate in UPGRADE_GATES],
        "enforced": sorted(
            [row["unit"] for row in units if row["capability"] == BudgetCapability.ENFORCED]
            + [row.limit for row in EXECUTION_LIMITS if row.capability == BudgetCapability.ENFORCED]
        ),
        "blocked_upgrades": blocked_upgrades(),
        "rule": (
            "a row moves to enforced only through its named task's real evidence; "
            "wiring, a code path or an environment flag is never that evidence"
        ),
    }
