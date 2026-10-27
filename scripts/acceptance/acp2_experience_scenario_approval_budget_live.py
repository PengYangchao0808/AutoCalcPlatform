"""``approval-budget-live`` — persistent approval card bound to plan digest.

T-27 / C19a's approval row plus the budget-phase placeholder.  Phase rows are
harness-resolved through ``context.identity.phase``:

* ``approval`` — runs the zero-dependency Node module tests of the persistent
  approval card (``frontend/js/module_tests/acp2_approval.test.js``) and
  records the plan-digest bind/invalidate matrix.  No Controller, DSH, model,
  QC, LSF or browser process is started: the approval evidence content is
  fixture, recorded as ``experience.approval.evidence_kind == "fixture"``.
  "The feature owns no transport" is a layering rule, not a PASS criterion:
  the row additionally proves that the injected ``ACPClient.research`` adapter
  and its wiring exist (static/controlled check), so a module with no adapter
  at all can never be accepted just because it performs no fetch.
* ``budget`` — ``NOT_VERIFIED(b16_handoff)``: no Runtime budget endpoint is
  delivered (B16/B17), so no budget/deny/expire/revoke row may claim evidence.
* ``aggregate`` — never reaches a scenario body: the harness aggregates the
  child rows and must stay ``NOT_VERIFIED`` while the budget row is
  ``NOT_VERIFIED``.  Run order is approval → budget → aggregate.

The harness normalizes ``--phase approval`` to the public phase
``experience:fixture`` through a scenario-level phase alias
(harness-contract.md §4), so the receipt honestly publishes
``evidence_kind=fixture``.  The requested phase remains visible to this body as
``context.identity.requested_phase``.  No live approval row is ever claimed,
and the row publishes ``experience.observations`` with every live observation
absent plus a zero-model-call fact.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from acp2_experience_scenarios import (
    _add_artifact,
    _assertion,
    _env_path,
    _finish,
    _node,
    _not_verified,
    _out_dir,
    _run,
    _tool_env,
    _truncate,
    _write_json,
    _write_text,
    publish_observations,
    register,
)

SCENARIO_ID = "approval-budget-live"

FEATURE_SOURCE = "frontend/js/workbench/features/research-approval.js"
TEST_SOURCE = "frontend/js/module_tests/acp2_approval.test.js"
RUN_ALL = "frontend/js/module_tests/run_all.js"
PLAN_IDENTITY = "docs/reference/next-change/acp2-contracts/experience/plan-identity.md"
CLIENT_RESEARCH = "frontend/js/client/research-query.js"

# Injected adapter/wiring the approval card must have.  The card owns no
# transport of its own (``FORBIDDEN_PATTERNS``), so "no fetch" is only honest
# when the real ``ACPClient.research`` adapter exists and is injected.  These
# anchors are static and cost no network/model call.
ADAPTER_WIRING: tuple[tuple[str, str, str], ...] = (
    (
        "client_research_namespace",
        CLIENT_RESEARCH,
        "ACPClient.research = (function",
    ),
    ("client_fetch_proposal", CLIENT_RESEARCH, "fetchProposal: fetchProposal"),
    (
        "feature_adapter_injection",
        FEATURE_SOURCE,
        "deps.research = next.research",
    ),
    (
        "feature_real_namespace",
        FEATURE_SOURCE,
        "window.ACPClient.research",
    ),
    (
        "module_test_real_namespace",
        TEST_SOURCE,
        "uses the real ACPClient.research namespace",
    ),
)

MIN_APPROVAL_CASES = 8

REQUIRED_TEST_PHRASES = {
    "card_digests": "renders proposal and recipe digests",
    "card_limits_caps_basis": "scientific limits, resource caps and cost basis",
    "card_expiry": "displays the approval expiry",
    "digest_vector": "frozen T-03 reference vector",
    "matrix_invalidates": "method, mapping, initial input and budget changes invalidate",
    "matrix_appended_refs": "appended task and evidence refs keep the approval valid",
    "matrix_display_time": "display name and timestamp changes keep the approval valid",
    "dsh_platform_separation": "dsh tool approval never changes platform approval state",
    "submit_bound_digest": "unbound submit is blocked without the bound digest",
    "other_user_rejected": "another user's approve is rejected",
    "model_claim_rejected": "model claim of user agreement is never an approval",
}

# Fixture content rules: the approval module is pure UI/state and must not own
# transport, a viewer, or a process launcher of its own.
FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("fetch_call", re.compile(r"(?<![A-Za-z0-9_$])fetch\s*\(")),
    ("xhr", re.compile(r"XMLHttpRequest")),
    ("viewer_create", re.compile(r"(?:\$3Dmol\.createViewer|ACPVisualization\.mol3d\.create)\(")),
    ("child_process", re.compile(r"child_process")),
)

BIND_TUPLE = [
    "plan_definition_digest",
    "project_id",
    "budget",
    "allowed_change_scope",
]

INVALIDATE_MATRIX = {
    "science.method": "invalidates",
    "science.mapping": "invalidates",
    "science.initial_inputs": "invalidates",
    "budget.*": "invalidates",
    "appended task_refs/evidence_refs": "keeps_valid",
    "display_name/created_at/updated_at": "keeps_valid",
    "approval_ref/approval_status": "keeps_valid",
}

BUDGET_ROWS = (
    "budget.reservation",
    "budget.deny",
    "budget.expire",
    "budget.revoke",
)


def _source_scan(source: Path) -> dict[str, Any]:
    path = source / FEATURE_SOURCE
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    hits = [
        {"file": FEATURE_SOURCE, "rule": rule}
        for rule, pattern in FORBIDDEN_PATTERNS
        if pattern.search(text)
    ]
    return {
        "file": FEATURE_SOURCE,
        "scanned": bool(text),
        "forbidden_hits": hits,
        "rules": [rule for rule, _ in FORBIDDEN_PATTERNS],
    }


def _adapter_wiring(source: Path) -> dict[str, Any]:
    """Static check that the injected research adapter and its wiring exist."""

    checks: list[dict[str, Any]] = []
    for name, relative, anchor in ADAPTER_WIRING:
        path = source / relative
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        checks.append(
            {
                "name": name,
                "file": relative,
                "anchor": anchor,
                "present": bool(text) and anchor in text,
            }
        )
    missing = [check["name"] for check in checks if not check["present"]]
    return {"checks": checks, "present": not missing, "missing": missing}


def _phase_budget(context: Any) -> int:
    out = _out_dir(context)
    rows = {name: "NOT_VERIFIED(b16_handoff)" for name in BUDGET_ROWS}
    _write_json(
        out,
        "approval-budget-gap.json",
        {
            "evidence_kind": "fixture",
            "phase": "budget",
            "rows": rows,
            "reason": "b16_handoff: Runtime budget endpoint (B16/B17) is not delivered",
        },
    )
    _add_artifact(context, "approval-budget-gap.json")
    context.extensions["experience.approval_budget"] = {
        "phase": "budget",
        "rows": rows,
    }
    return _not_verified(
        context,
        "b16_handoff: Runtime budget endpoint (B16/B17) is not delivered; "
        "budget/deny/expire/revoke rows claim no evidence",
    )


def _phase_approval_live(context: Any) -> int:
    """M4-02: the real browser approval/budget row (registered id, live mode)."""
    from acp2_approval_browser import run as run_browser_row

    return run_browser_row(context)


def _phase_approval(context: Any) -> int:
    if getattr(context.identity, "evidence_kind", None) == "live":
        return _phase_approval_live(context)
    source = _env_path("ACP2_C_SOURCE")
    if source is None:
        return _not_verified(context, "missing_prerequisite:ACP2_C_SOURCE")
    required = (FEATURE_SOURCE, TEST_SOURCE, RUN_ALL, PLAN_IDENTITY)
    missing = [rel for rel in required if not (source / rel).is_file()]
    if missing:
        return _not_verified(
            context, "missing_prerequisite:approval_module_absent:" + ",".join(missing)
        )
    node = _node()
    if node is None:
        return _not_verified(context, "missing_prerequisite:node")

    kind = getattr(context.identity, "evidence_kind", None)
    out = _out_dir(context)
    timeout = max(10.0, float(context.timeout))
    env = _tool_env(node)

    scan = _source_scan(source)
    _write_json(out, "approval-source-scan.json", scan)
    _add_artifact(context, "approval-source-scan.json")
    # Layering rule: the approval card owns no transport/viewer/process of its
    # own.  This is NOT a PASS criterion by itself -- see the adapter wiring
    # check below, which proves the injected ACPClient.research adapter exists.
    _assertion(
        context,
        "approval.feature_no_direct_transport",
        bool(scan["scanned"]) and not scan["forbidden_hits"],
        json.dumps(scan["forbidden_hits"], sort_keys=True)[:400],
    )

    wiring = _adapter_wiring(source)
    _write_json(out, "approval-adapter-wiring.json", wiring)
    _add_artifact(context, "approval-adapter-wiring.json")
    _assertion(
        context,
        "approval.injected_adapter_wiring_present",
        bool(wiring["present"]),
        f"missing={wiring['missing']}",
    )

    run = _run(
        [node, RUN_ALL, "acp2_approval"],
        cwd=source,
        timeout=timeout,
        env=env,
    )
    log = (run.stdout or "") + (run.stderr or "")
    _write_text(out, "approval-node-tests.log", _truncate(log, 40000))
    _add_artifact(context, "approval-node-tests.log")
    _assertion(
        context,
        "approval.node_suite",
        run.returncode == 0,
        f"rc={run.returncode} {log.strip()[-400:]}",
    )

    count = re.search(r"(\d+)\s+passed,\s+(\d+)\s+failed", log)
    passed = int(count.group(1)) if count else 0
    failed = int(count.group(2)) if count else -1
    _assertion(
        context,
        "approval.module_tests_ge_8",
        failed == 0 and passed >= MIN_APPROVAL_CASES,
        f"passed={passed} failed={failed} minimum={MIN_APPROVAL_CASES}",
    )

    missing_phrases = [name for name, phrase in REQUIRED_TEST_PHRASES.items() if phrase not in log]
    covered = sorted(set(REQUIRED_TEST_PHRASES) - set(missing_phrases))
    _assertion(
        context,
        "approval.bind_invalidate_matrix_covered",
        not missing_phrases,
        f"missing={missing_phrases} covered={covered}",
    )

    public_phase = getattr(context.identity, "phase", None)
    matrix = {
        "evidence_kind": "fixture",
        "public_evidence_kind": kind,
        "requested_phase": "approval",
        "public_phase": public_phase,
        "phase": "approval",
        "feature_module": FEATURE_SOURCE,
        "test_module": TEST_SOURCE,
        "plan_identity": PLAN_IDENTITY,
        "node_tests": {
            "passed": passed,
            "failed": failed,
            "minimum": MIN_APPROVAL_CASES,
        },
        "bind_tuple": BIND_TUPLE,
        "invalidate_matrix": INVALIDATE_MATRIX,
        "separation": {
            "dsh_tool_approval": "separate mapping; never changes platform state",
            "platform_research_approval": "approval_card only; approve binds the real principal",
        },
        "gating": {
            "submit_requires_bound_digest": True,
            "model_claim_is_never_approval": True,
            "other_user_approve_rejected": True,
            "expiry_displayed": True,
            "display_name_and_timestamps_do_not_invalidate": True,
        },
        "live_b16_rows": {name: "NOT_VERIFIED(b16_handoff)" for name in BUDGET_ROWS},
        "adapter_wiring": {
            "present": wiring["present"],
            "missing": wiring["missing"],
            "checks": wiring["checks"],
        },
        "evidence_layering": {
            "feature_module": "no fetch/XMLHttpRequest/viewer/child_process",
            "adapter": "ACPClient.research injected by the workbench",
            "public_evidence_kind": "fixture",
            "live_rows": "NOT_VERIFIED(b16_handoff)",
        },
    }
    _write_json(out, "approval-bind-matrix.json", matrix)
    _add_artifact(context, "approval-bind-matrix.json")

    _assertion(
        context,
        "approval.public_evidence_kind_fixture",
        kind == "fixture",
        f"public evidence_kind={kind}",
    )

    context.extensions["experience.approval_budget"] = {
        "phase": "approval",
        "public_phase": public_phase,
        "evidence_kind": "fixture",
        "public_evidence_kind": kind,
        "bind_tuple": BIND_TUPLE,
        "node_tests": {"passed": passed, "failed": failed},
        "live_b16_rows": {name: "NOT_VERIFIED(b16_handoff)" for name in BUDGET_ROWS},
        "adapter_wiring": {
            "present": wiring["present"],
            "missing": wiring["missing"],
        },
    }
    publish_observations(
        context,
        {
            "transport": "absent",
            "controller": "absent",
            "model": "absent",
            "qc": "absent",
            "lsf": "absent",
            "browser": "absent",
            "artifact": "absent",
            "denial": "absent",
        },
        model_calls=0,
        model_evidence=(
            "fixture approval card: Node module tests only; no model client is "
            "configured or invoked (0 calls)"
        ),
    )
    return _finish(context, "approval_assertion_failed")


def scenario_approval_budget_live(context: Any) -> int:
    identity = context.identity
    phase = getattr(identity, "requested_phase", None) or getattr(identity, "phase", None)
    if phase == "approval":
        return _phase_approval(context)
    if phase == "budget":
        return _phase_budget(context)
    return _not_verified(context, f"phase_not_implemented:{phase}")


register(SCENARIO_ID, scenario_approval_budget_live)
