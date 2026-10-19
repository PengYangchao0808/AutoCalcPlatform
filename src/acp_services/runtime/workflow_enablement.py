"""Per-workflow envelope enablement matrix + explicit refusal (B06-5 / todo 34).

The C01 (HN-01) per-workflow closure handoff is the gate for switching a
workflow onto the frozen-envelope execution entry. The single authority for the
closure verdict is ``execution_chain.FROZEN_WORKFLOW_CAPABILITY_TABLE`` (applied
by todo 32); this module is the *policy* layer over it:

* :func:`enablement_matrix` renders the enablement matrix — one row per active
  workflow with its entry (``envelope`` | ``legacy``), closure status
  (``integrated`` | ``pending``) and evidence reference.
* :func:`select_entry` returns ``envelope`` **only** for a workflow whose C01
  closure is integrated (and whose target protocol supports it); everything else
  keeps ``legacy``.
* :func:`require_envelope_entry` is the refusal gate: forcing the envelope entry
  on a workflow that is not ready (closure pending, unknown, or a local-only
  workflow targeting a remote node) raises :class:`WorkflowNotEnabledError` — an
  explicit typed refusal, never a silent fallback that a caller could mistake for
  independence.

Two exit gates (review P1-3) stay separate:

* **T34a** — the selected closure workflows enable via the envelope entry, with a
  targeted regression (``tests/test_services_workflow_enablement.py``).
* **T34b** — full 15-active coverage. :func:`coverage_report` is the
  machine-checkable check: it PASSES on matrix integrity/honesty while
  *recording* that the full-coverage gate is still OPEN (``enabled 8/15``). The
  remaining seven keep the legacy path and are never claimed independent; their
  closures are carried by C01 (HN-01 §13, T20–T30).

This module imports stdlib + the Services execution-chain table only (no ``acp``)
and never references the legacy CLI entry as a command.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from acp_services.runtime.execution_chain import (
    EXECUTION_ENTRY_ENVELOPE,
    EXECUTION_ENTRY_LEGACY,
    FROZEN_WORKFLOW_CAPABILITY_TABLE,
)

__all__ = [
    "ACTIVE_WORKFLOWS",
    "CLOSURE_DELIVERED",
    "CLOSURE_INTEGRATED",
    "CLOSURE_PENDING",
    "LOCAL_ONLY_WORKFLOWS",
    "REFUSAL_CLOSURE_PENDING",
    "REFUSAL_LOCAL_ONLY",
    "REFUSAL_UNKNOWN",
    "CoverageReport",
    "WorkflowEnablement",
    "WorkflowNotEnabledError",
    "coverage_report",
    "enablement_for",
    "enablement_matrix",
    "require_envelope_entry",
    "select_entry",
]

#: The 15 active workflow ids in canonical catalog order
#: (``test_architecture_invariants.CURRENT_ACTIVE_IDS``).
ACTIVE_WORKFLOWS: Final[tuple[str, ...]] = (
    "singlepoint",
    "optimize",
    "frequency",
    "scan",
    "irc",
    "tsmode",
    "casscf",
    "xtb_optimize",
    "nmr",
    "Confsearch",
    "PESsearch",
    "BatchOptimize",
    "XtbPathSearch",
    "OrcaGradient",
    "XtbConstrainedOpt",
)

#: Active workflows restricted to local execution: they consume a frozen
#: ``pes2ts_*_request_v1`` payload and are absent from the remote allow-list.
#: ``OrcaGradient`` is *both* an active workflow and local-only — it is a member
#: of this set and of the C01-integrated scope, never counted as a legacy row.
LOCAL_ONLY_WORKFLOWS: Final[frozenset[str]] = frozenset(
    {"XtbPathSearch", "OrcaGradient", "XtbConstrainedOpt"}
)

#: Closure-status vocabulary (consumer perspective).
CLOSURE_INTEGRATED: Final = "integrated"  # C01 delivered + applied (todo 32)
CLOSURE_PENDING: Final = "pending"  # closure not yet delivered by C01
CLOSURE_DELIVERED: Final = "delivered"  # provider delivered, not yet applied

#: Refusal reasons.
REFUSAL_UNKNOWN: Final = "unknown-workflow"
REFUSAL_CLOSURE_PENDING: Final = "closure-not-integrated"
REFUSAL_LOCAL_ONLY: Final = "local-only-workflow-on-remote"

#: Evidence references per workflow (C01 HN-01 record).
_EVIDENCE: Final[dict[str, str]] = {
    "singlepoint": "HN-01 §12.1",
    "optimize": "HN-01 §12.2",
    "frequency": "HN-01 §12.3",
    "scan": "HN-01 §12.4",
    "irc": "HN-01 §12.5",
    "casscf": "HN-01 §12.6",
    "xtb_optimize": "HN-01 §12.7",
    "OrcaGradient": "HN-01 §12.8",
    "Confsearch": "HN-01 §13.1",
    "PESsearch": "HN-01 §13.2",
    "XtbPathSearch": "HN-01 §13.3",
    "tsmode": "HN-01 §13.4",
    "nmr": "HN-01 §13.5",
    "BatchOptimize": "HN-01 §13.6",
    "XtbConstrainedOpt": "HN-01 §13.7",
}


class WorkflowNotEnabledError(Exception):
    """Forcing the envelope entry on a workflow that is not enabled.

    The typed refusal is deliberate: a caller must never silently receive the
    legacy entry and record the workflow as envelope-enabled (or independent).
    """

    def __init__(self, workflow: str, *, reason: str, remote: bool = False) -> None:
        target = "remote" if remote else "local"
        super().__init__(f"envelope entry refused for workflow {workflow!r} ({target}): {reason}")
        self.workflow = workflow
        self.reason = reason
        self.remote = remote


@dataclass(frozen=True)
class WorkflowEnablement:
    """One row of the enablement matrix (frozen).

    ``entry`` is the local execution entry; ``remote_entry`` is the entry when the
    workflow targets a node (a local-only workflow falls back to ``legacy``
    there). ``enabled`` is True only when the local entry is the envelope entry
    (closure integrated and locally capable) — it is never True for a legacy row.
    """

    workflow: str
    closure: str
    entry: str
    remote_entry: str
    local_only: bool
    enabled: bool
    evidence: str

    def to_dict(self) -> dict[str, object]:
        return {
            "workflow": self.workflow,
            "closure": self.closure,
            "entry": self.entry,
            "remote_entry": self.remote_entry,
            "local_only": self.local_only,
            "enabled": self.enabled,
            "evidence": self.evidence,
        }


def _closure_status(workflow: str) -> str:
    """Map the frozen table's closure verdict to the consumer vocabulary."""
    capability = FROZEN_WORKFLOW_CAPABILITY_TABLE.get(workflow)
    if capability is None:
        return CLOSURE_PENDING
    return CLOSURE_INTEGRATED if capability.closure == "integrated" else CLOSURE_PENDING


def select_entry(workflow: str, *, remote: bool = False) -> str:
    """Return the policy entry for *workflow*: ``envelope`` or ``legacy``.

    ``envelope`` requires an integrated C01 closure *and* protocol support
    (a local-only workflow on a remote target keeps ``legacy``). The function is
    total: an unknown/retired workflow maps to ``legacy`` so existing paths never
    break.
    """
    capability = FROZEN_WORKFLOW_CAPABILITY_TABLE.get(workflow)
    if capability is None or capability.closure != "integrated":
        return EXECUTION_ENTRY_LEGACY
    if remote and not capability.remote_supported:
        return EXECUTION_ENTRY_LEGACY
    return EXECUTION_ENTRY_ENVELOPE


def require_envelope_entry(workflow: str, *, remote: bool = False) -> str:
    """Force the envelope entry for *workflow* or raise an explicit refusal.

    Returns ``envelope`` when the workflow is enabled for the target protocol;
    otherwise raises :class:`WorkflowNotEnabledError` with a typed ``reason`` —
    never a silent legacy fallback.
    """
    entry = select_entry(workflow, remote=remote)
    if entry == EXECUTION_ENTRY_ENVELOPE:
        return entry
    capability = FROZEN_WORKFLOW_CAPABILITY_TABLE.get(workflow)
    if capability is None:
        reason = REFUSAL_UNKNOWN
    elif capability.closure != "integrated":
        reason = REFUSAL_CLOSURE_PENDING
    else:
        reason = REFUSAL_LOCAL_ONLY
    raise WorkflowNotEnabledError(workflow, reason=reason, remote=remote)


def enablement_for(workflow: str) -> WorkflowEnablement:
    """Return the enablement row for *workflow* (raises for unknown)."""
    if workflow not in ACTIVE_WORKFLOWS:
        raise KeyError(f"no enablement row for workflow: {workflow!r}")
    entry = select_entry(workflow, remote=False)
    return WorkflowEnablement(
        workflow=workflow,
        closure=_closure_status(workflow),
        entry=entry,
        remote_entry=select_entry(workflow, remote=True),
        local_only=workflow in LOCAL_ONLY_WORKFLOWS,
        enabled=entry == EXECUTION_ENTRY_ENVELOPE,
        evidence=_EVIDENCE.get(workflow, ""),
    )


def enablement_matrix() -> tuple[WorkflowEnablement, ...]:
    """Return the enablement matrix — one row per active workflow, in order."""
    return tuple(enablement_for(workflow) for workflow in ACTIVE_WORKFLOWS)


@dataclass(frozen=True)
class CoverageReport:
    """Machine-checkable coverage result (T34b).

    ``integrity_ok`` is the matrix honesty check (all active workflows present,
    entries consistent with the closure status, no legacy row marked enabled).
    ``full_coverage_gate_open`` is the release gate: still OPEN until every
    active workflow is enabled (todo 41b / 76).
    """

    active_total: int
    enabled: int
    legacy: int
    integrity_ok: bool
    full_coverage_gate_open: bool
    problems: tuple[str, ...]

    @property
    def summary(self) -> str:
        gate = "OPEN" if self.full_coverage_gate_open else "CLOSED"
        return (
            f"enabled: {self.enabled}/{self.active_total}; "
            f"legacy: {self.legacy}/{self.active_total}; "
            f"full-coverage gate: {gate}"
        )


def coverage_report() -> CoverageReport:
    """Check matrix integrity/honesty and record the full-coverage gate state."""
    rows = enablement_matrix()
    problems: list[str] = []

    present = {row.workflow for row in rows}
    missing = [workflow for workflow in ACTIVE_WORKFLOWS if workflow not in present]
    if missing:
        problems.append(f"missing active workflows: {missing}")
    extra = sorted(present - set(ACTIVE_WORKFLOWS))
    if extra:
        problems.append(f"unexpected workflows: {extra}")

    for row in rows:
        expected_entry = select_entry(row.workflow, remote=False)
        if row.entry != expected_entry:
            problems.append(f"{row.workflow}: entry {row.entry!r} != expected {expected_entry!r}")
        if row.remote_entry != select_entry(row.workflow, remote=True):
            problems.append(f"{row.workflow}: remote_entry inconsistent")
        if row.local_only != (row.workflow in LOCAL_ONLY_WORKFLOWS):
            problems.append(f"{row.workflow}: local_only flag inconsistent")
        if row.enabled and row.closure != CLOSURE_INTEGRATED:
            problems.append(f"{row.workflow}: enabled but closure is {row.closure!r}")
        if row.enabled and row.entry != EXECUTION_ENTRY_ENVELOPE:
            problems.append(f"{row.workflow}: enabled but entry is {row.entry!r}")
        if not row.enabled and row.entry != EXECUTION_ENTRY_LEGACY:
            problems.append(f"{row.workflow}: legacy row marked {row.entry!r}")
        # A legacy row must never be claimed independent.
        if row.entry == EXECUTION_ENTRY_LEGACY and row.closure == CLOSURE_INTEGRATED:
            # Integrated + local-only on the local target cannot be legacy; the
            # local target always supports an integrated closure, so this is a bug.
            problems.append(f"{row.workflow}: integrated closure but legacy local entry")

    enabled = sum(1 for row in rows if row.enabled)
    legacy = len(rows) - enabled
    return CoverageReport(
        active_total=len(ACTIVE_WORKFLOWS),
        enabled=enabled,
        legacy=legacy,
        integrity_ok=not problems,
        # The full-coverage gate opens only when every active workflow is enabled.
        full_coverage_gate_open=enabled != len(ACTIVE_WORKFLOWS),
        problems=tuple(problems),
    )
