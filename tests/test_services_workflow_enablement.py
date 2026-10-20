"""B06-5 (todo 34) — per-workflow envelope enablement driven by the C01 closure.

Two exit gates are verified separately:

**T34a — selected closure workflows enable via the envelope entry.**
The C01-integrated workflows (at minimum ``singlepoint`` / ``optimize`` /
``frequency``) resolve to the envelope entry and, with a frozen envelope/plan
pair on disk, the local runner emits the Services worker entry — the targeted
regression. A workflow whose closure is not integrated stays on the legacy entry.

**T34b — full 15-active coverage check (machine-checkable).**
:func:`workflow_enablement.coverage_report` PASSES on matrix integrity/honesty
(every active workflow present, entries consistent with the closure status, no
legacy row marked enabled) while *recording* that the full-coverage gate remains
OPEN (``enabled: 8/15; legacy: 7/15``). The seven pending workflows are never
claimed independent.

Plus the refusal gate (QA failure scenario): forcing the envelope entry on a
not-ready workflow raises an explicit typed refusal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acp.scheduler.runner import JobRunner
from acp_services.runtime import execution_chain as chain
from acp_services.runtime import workflow_enablement as enablement
from acp_services.runtime.workflow_enablement import (
    CLOSURE_INTEGRATED,
    CLOSURE_PENDING,
    REFUSAL_CLOSURE_PENDING,
    REFUSAL_LOCAL_ONLY,
    REFUSAL_UNKNOWN,
    WorkflowNotEnabledError,
)
from tests.test_acp_services_isolation import scan_for_acp_imports
from tests.test_architecture_invariants import CURRENT_ACTIVE_IDS
from tests.test_services_chain_switch import _record, _write_frozen_artifacts

_REPO_ROOT = Path(__file__).resolve().parents[1]

_C01_EIGHT = (
    "singlepoint",
    "optimize",
    "frequency",
    "scan",
    "irc",
    "casscf",
    "xtb_optimize",
    "OrcaGradient",
)
_LEGACY_SEVEN = (
    "Confsearch",
    "PESsearch",
    "BatchOptimize",
    "XtbPathSearch",
    "XtbConstrainedOpt",
    "tsmode",
    "nmr",
)


# ---------------------------------------------------------------------------
# T34a — selected closure workflows enable (targeted envelope regression)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workflow", ["singlepoint", "optimize", "frequency"])
def test_t34a_selected_workflow_resolves_to_envelope(workflow: str) -> None:
    assert enablement.select_entry(workflow) == chain.EXECUTION_ENTRY_ENVELOPE
    assert enablement.require_envelope_entry(workflow) == chain.EXECUTION_ENTRY_ENVELOPE
    row = enablement.enablement_for(workflow)
    assert row.closure == CLOSURE_INTEGRATED
    assert row.enabled is True
    assert row.entry == chain.EXECUTION_ENTRY_ENVELOPE


@pytest.mark.parametrize("workflow", ["singlepoint", "optimize", "frequency"])
def test_t34a_local_runner_emits_worker_entry_for_enabled_closure(
    workflow: str, tmp_path: Path
) -> None:
    """Targeted regression: enabled closure + frozen artifacts -> worker entry."""
    work_dir = tmp_path / workflow
    work_dir.mkdir()
    _write_frozen_artifacts(work_dir, workflow)

    runner = JobRunner(python_executable="python")
    cmd = runner._build_execution_cmd(_record(workflow), work_dir, "CCO", {})
    joined = " ".join(cmd)

    assert chain.WORKER_MODULE in joined
    assert "acp.cli" not in joined
    recorded = chain.read_attempt_choice(work_dir)
    assert recorded is not None
    assert recorded.entry == chain.EXECUTION_ENTRY_ENVELOPE


def test_t34a_pending_workflow_stays_legacy_in_runner(tmp_path: Path) -> None:
    """A not-closed workflow keeps the legacy entry even with frozen artifacts."""
    work_dir = tmp_path / "confsearch"
    work_dir.mkdir()
    _write_frozen_artifacts(work_dir, "Confsearch")

    runner = JobRunner(python_executable="python")
    cmd = runner._build_execution_cmd(_record("Confsearch"), work_dir, "CCO", {})
    joined = " ".join(cmd)

    assert chain.WORKER_MODULE not in joined
    assert "acp.cli" in joined


# ---------------------------------------------------------------------------
# Refusal gate (QA failure scenario: force envelope on a not-ready workflow)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workflow", _LEGACY_SEVEN)
def test_forcing_envelope_on_pending_workflow_refuses(workflow: str) -> None:
    with pytest.raises(WorkflowNotEnabledError) as excinfo:
        enablement.require_envelope_entry(workflow)
    assert excinfo.value.workflow == workflow
    # Every legacy seven is a closure-pending refusal (none is local-only-only).
    assert excinfo.value.reason == REFUSAL_CLOSURE_PENDING
    assert enablement.select_entry(workflow) == chain.EXECUTION_ENTRY_LEGACY


def test_forcing_envelope_on_local_only_remote_refuses() -> None:
    # casscf is C01-integrated but local-only: forcing envelope on a node refuses.
    with pytest.raises(WorkflowNotEnabledError) as excinfo:
        enablement.require_envelope_entry("casscf", remote=True)
    assert excinfo.value.reason == REFUSAL_LOCAL_ONLY
    # The same workflow is enabled locally.
    assert enablement.require_envelope_entry("casscf") == chain.EXECUTION_ENTRY_ENVELOPE


def test_forcing_envelope_on_unknown_workflow_refuses() -> None:
    with pytest.raises(WorkflowNotEnabledError) as excinfo:
        enablement.require_envelope_entry("not-a-workflow")
    assert excinfo.value.reason == REFUSAL_UNKNOWN
    # Selection stays total: an unknown workflow maps to legacy, never envelope.
    assert enablement.select_entry("not-a-workflow") == chain.EXECUTION_ENTRY_LEGACY


def test_refusal_is_not_a_silent_legacy_fallback() -> None:
    """The refusal must not return an entry a caller could record as enabled."""
    with pytest.raises(WorkflowNotEnabledError):
        enablement.require_envelope_entry("nmr")
    # select_entry (the total policy) is the only path that yields legacy.
    assert enablement.select_entry("nmr") == chain.EXECUTION_ENTRY_LEGACY


# ---------------------------------------------------------------------------
# T34b — full 15-active coverage check (machine-checkable)
# ---------------------------------------------------------------------------


def test_active_workflows_match_the_canonical_active_set() -> None:
    assert enablement.ACTIVE_WORKFLOWS == CURRENT_ACTIVE_IDS
    assert len(enablement.ACTIVE_WORKFLOWS) == 15


def test_t34b_coverage_report_integrity_passes_and_records_open_gate() -> None:
    report = enablement.coverage_report()
    assert report.integrity_ok, report.problems
    assert report.problems == ()
    assert report.active_total == 15
    assert report.enabled == 8
    assert report.legacy == 7
    # T34b is the full-coverage gate: still OPEN, not claimed done.
    assert report.full_coverage_gate_open is True
    assert report.summary == "enabled: 8/15; legacy: 7/15; full-coverage gate: OPEN"


def test_matrix_covers_all_fifteen_active_once() -> None:
    rows = enablement.enablement_matrix()
    assert len(rows) == 15
    assert tuple(row.workflow for row in rows) == enablement.ACTIVE_WORKFLOWS
    assert {row.workflow for row in rows} == set(CURRENT_ACTIVE_IDS)


def test_matrix_entries_are_consistent_with_closure_status() -> None:
    for row in enablement.enablement_matrix():
        assert row.entry == enablement.select_entry(row.workflow)
        assert row.remote_entry == enablement.select_entry(row.workflow, remote=True)
        if row.closure == CLOSURE_INTEGRATED:
            assert row.enabled is True
            assert row.entry == chain.EXECUTION_ENTRY_ENVELOPE
        else:
            # A pending closure keeps the legacy path and is never enabled.
            assert row.closure == CLOSURE_PENDING
            assert row.enabled is False
            assert row.entry == chain.EXECUTION_ENTRY_LEGACY


def test_legacy_rows_are_never_marked_independent() -> None:
    for row in enablement.enablement_matrix():
        if row.workflow in _LEGACY_SEVEN:
            assert row.enabled is False
            assert row.entry == chain.EXECUTION_ENTRY_LEGACY
            assert row.closure == CLOSURE_PENDING


# ---------------------------------------------------------------------------
# local-only restriction + OrcaGradient reconciliation
# ---------------------------------------------------------------------------


def test_local_only_set_is_exactly_the_frozen_payload_workflows() -> None:
    assert enablement.LOCAL_ONLY_WORKFLOWS == frozenset(
        {"XtbPathSearch", "OrcaGradient", "XtbConstrainedOpt"}
    )
    for workflow in enablement.LOCAL_ONLY_WORKFLOWS:
        assert enablement.enablement_for(workflow).local_only is True


def test_orca_gradient_is_both_active_and_local_only_reconciled() -> None:
    """OrcaGradient is integrated (one of the eight) *and* local-only."""
    row = enablement.enablement_for("OrcaGradient")
    assert row.workflow in enablement.ACTIVE_WORKFLOWS
    assert row.workflow in enablement.LOCAL_ONLY_WORKFLOWS
    assert row.closure == CLOSURE_INTEGRATED
    assert row.enabled is True
    assert row.entry == chain.EXECUTION_ENTRY_ENVELOPE  # local envelope
    assert row.remote_entry == chain.EXECUTION_ENTRY_LEGACY  # local-only on node
    # It is never counted among the seven legacy (pending) rows.
    assert "OrcaGradient" not in _LEGACY_SEVEN


def test_local_only_pending_workflows_never_enabled() -> None:
    # XtbPathSearch / XtbConstrainedOpt are local-only *and* closure-pending.
    for workflow in ("XtbPathSearch", "XtbConstrainedOpt"):
        row = enablement.enablement_for(workflow)
        assert row.local_only is True
        assert row.closure == CLOSURE_PENDING
        assert row.enabled is False
        assert row.entry == chain.EXECUTION_ENTRY_LEGACY


# ---------------------------------------------------------------------------
# Services-side hygiene: the new module never imports the legacy layer
# ---------------------------------------------------------------------------


def test_enablement_module_is_acp_free() -> None:
    source = (_REPO_ROOT / "src" / "acp_services" / "runtime" / "workflow_enablement.py").read_text(
        encoding="utf-8"
    )
    assert scan_for_acp_imports(source) == []


# ---------------------------------------------------------------------------
# T25: the freeze/release builder adds no activation
# ---------------------------------------------------------------------------


def test_t25_freeze_builder_does_not_activate_any_workflow() -> None:
    from acp.calculations import attempt_freeze as freeze

    assert not hasattr(freeze, "DEFAULT_ENVELOPE_WORKFLOWS")
    assert not hasattr(freeze, "ENABLED_WORKFLOWS")
    # The 8/7 scientific-closure entry policy is unchanged by adding a freeze
    # builder: no workflow becomes default, and every active workflow keeps the
    # entry the existing dispatcher/enablement matrix already recorded.
    assert len(enablement.ACTIVE_WORKFLOWS) == 15
    for row in enablement.enablement_matrix():
        assert row.entry == enablement.select_entry(row.workflow)
        assert row.remote_entry == enablement.select_entry(row.workflow, remote=True)
