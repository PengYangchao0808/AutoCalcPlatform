"""Live browser branch of ``approval-budget-live`` (M4-02, G06/G11).

The registered scenario id and phase are unchanged (``approval-budget-live``,
``--phase approval``); what changes is that the row is executed in real
browsers against a real Controller instead of only running the approval card's
Node module tests:

* a real Controller in ``api-key`` identity mode over an isolated identity
  store (operator / reviewer / auditor accounts);
* four real proposals + approvals created through the platform writer — the
  *request* step a client performs.  The operator is the requester;
* the **operator** browser (bundled Chromium) attempts to approve its own
  approval (the platform's requester/approver separation refuses it — a real
  negative) and then revokes an approved one;
* the **reviewer** browser (system Microsoft Edge) approves one approval and
  rejects another, so two independent identities really act on the same
  project;
* the **auditor** browser (read-only key) cannot read the approval at all (the
  platform answers the uniform not-found shape for a non-requester) and cannot
  decide — a third, read-only identity can never escalate;
* the **budget** half: a submit bound to an approved approval but carrying a
  budget above the approved range is refused by the platform (typed rule
  recorded) and no task is created.

Every decision is issued by the page's own ``ACPClient.researchCommands`` module
and every platform fact is re-read over HTTP, so the receipt never trusts the
page.  What the browsers saw (statuses, console, page errors, screenshots) is
recorded; a screenshot never replaces the platform receipt.
"""

from __future__ import annotations

import json
import shutil
import time
from typing import Any

from acp2_browser_leg import (
    BrowserPrerequisiteError,
    BrowserSession,
    PlatformController,
    http_json,
    install_bridge,
    provision_identities,
    repo_root,
)
from acp2_experience_scenarios import (
    _add_artifact,
    _assertion,
    _finish,
    _not_verified,
    _out_dir,
    _write_json,
    publish_observations,
)

SCENARIO_ID = "approval-budget-live"
PROJECT_NAME = "ACP 2.0 M4-02 approval browser leg"

PLAN: dict[str, Any] = {
    "workflow": "singlepoint",
    "input": {"source": "CCO", "source_type": "smiles"},
    "method": {"method": "B3LYP D3 ma-def2-SVP"},
    "resources": {"cores": 1, "nproc": 4},
}
BUDGET = {"unit": "task_count", "count": 1, "timeout_s": 600}
OVER_BUDGET = {"unit": "task_count", "count": 5, "timeout_s": 600}
SCIENCE = {
    "capability_steps": ["singlepoint"],
    "initial_inputs": [
        {
            "input_id": "in-m4-02-approval",
            "sha256": "sha256:" + "e" * 64,
            "format": "smiles",
            "role": "minimum",
        }
    ],
    "method": "B3LYP D3 ma-def2-SVP",
    "mapping": "canonical",
    "charge": 0,
    "multiplicity": 1,
    "allowed_substitutions": [],
}
PROPOSAL_BUDGET = {
    "model_requests": 20,
    "input_tokens": 40000,
    "output_tokens": 20000,
    "task_refs": 1,
    "recipe_steps": 1,
    "qc_invocations": 1,
    "wall_seconds": 600,
    "queue_seconds": 600,
}


def _proposal_body(project: str, index: int) -> dict[str, Any]:
    return {
        "proposal_id": f"prop-m4-02-approval-{index}",
        "project_id": project,
        "round": 0,
        "question_ref": "question-m4-02-approval",
        "question_revision": 0,
        "reaction_ref": "reaction-m4-02-approval",
        "reaction_revision": 0,
        "hypothesis": f"approval browser row {index}: the platform is the approval authority",
        "recipe_digest": "sha256:" + "c" * 64,
        "plan_definition_digest": "",
        "science": dict(SCIENCE),
        "budget": dict(PROPOSAL_BUDGET),
        "approval_ref": None,
        "task_refs": [],
        "decisions": [],
        "created_at": "",
        "schema_version": 1,
        "decision": "proposed",
    }


def _create_approval(base_url: str, key: str, project: str, index: int) -> dict[str, Any]:
    result = http_json(
        base_url,
        "POST",
        "/api/v2/research/proposals",
        {
            "project_id": project,
            "proposal": _proposal_body(project, index),
            "plan": PLAN,
            "session_seed": f"m4-02-approval-{index}",
            "budget": BUDGET,
            "idempotency_key": f"m4-02-approval-{index}",
        },
        key=key,
    )
    if not result.ok or not isinstance(result.payload, dict):
        raise RuntimeError(
            f"proposal {index} create failed: HTTP {result.status} "
            f"{json.dumps(result.payload)[:500]}"
        )
    payload = result.payload
    approval = payload.get("approval") or {}
    digests = payload.get("digests") or {}
    return {
        "index": index,
        "approval_ref": str(approval.get("approval_ref") or ""),
        "approval_revision": int(approval.get("revision") or 0),
        "proposal_ref": str(approval.get("proposal_ref") or ""),
        "proposal_digest": str(approval.get("proposal_digest") or ""),
        "plan_digest": str(approval.get("plan_digest") or digests.get("plan_digest") or ""),
        "science_digest": str(
            approval.get("science_digest") or digests.get("science_digest") or ""
        ),
        "budget": approval.get("budget"),
        "requester_ref": str(approval.get("requester_ref") or ""),
        "research_id": str((payload.get("research") or {}).get("research_id") or ""),
    }


def _approval_state(base_url: str, key: str, ref: str) -> str:
    result = http_json(base_url, "GET", f"/api/v2/research/approvals/{ref}", key=key)
    if not result.ok or not isinstance(result.payload, dict):
        return f"http_{result.status}"
    return str(result.payload.get("state") or result.payload.get("status") or "")


def _platform_job_count(base_url: str, key: str, project: str) -> int:
    result = http_json(base_url, "GET", f"/api/v1/jobs?project_id={project}&limit=50", key=key)
    jobs = (result.payload or {}).get("jobs") if isinstance(result.payload, dict) else None
    return len(jobs or [])


def _rule_of(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    body = result.get("body")
    if isinstance(body, dict):
        detail = body.get("detail") if isinstance(body.get("detail"), dict) else body
        if isinstance(detail, dict):
            params = detail.get("params")
            if isinstance(params, dict) and params.get("rule"):
                return str(params["rule"])
            if detail.get("code"):
                return str(detail["code"])
    message = str(result.get("message") or "")
    return message.split(":", 1)[0] if message else ""


def run(context: Any) -> int:
    """Entry the approval body delegates to for a live run (own thread)."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run_row, context)
        try:
            return int(future.result(timeout=max(60.0, float(context.timeout))))
        except Exception as exc:  # noqa: BLE001 - reported as a row failure
            _assertion(context, "approval_budget.row", False, f"{type(exc).__name__}: {exc}")
            return _finish(context, "approval_budget_row_exception")


def _run_row(context: Any) -> int:
    out = _out_dir(context)
    runtime_root = out / "_runtime"
    started = time.time()
    if not (repo_root() / "frontend" / "ACP_Workbench_v2.html").is_file():
        return _not_verified(context, "source_tree_absent:frontend/ACP_Workbench_v2.html")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return _not_verified(context, f"playwright_absent:{exc}")

    runtime_root.mkdir(parents=True, exist_ok=True)
    identity_db = runtime_root / "acp_identity.db"
    platform = PlatformController(
        run_root=runtime_root / "runs",
        identity_db=identity_db,
        log_path=out / "controller.log",
    )
    playwright = sync_playwright().start()
    try:
        platform.start()
        base_url = platform.base_url

        # ---- one unscoped bootstrap key creates the project, then the three
        # real identities are scoped to it.
        from acp_services.workspace.identity_lifecycle import (
            IdentityLifecycle,
            IdentityLifecyclePolicy,
        )
        from acp_services.workspace.keys import ApiKeyManager

        bootstrap_manager = ApiKeyManager(identity_db, iterations=2_000)
        bootstrap_lifecycle = IdentityLifecycle(
            identity_db,
            policy=IdentityLifecyclePolicy(
                deployment_tier="lab", capabilities=("authentication", "isolation")
            ),
            key_manager=bootstrap_manager,
            password_params={
                "kdf": "pbkdf2_hmac_sha256",
                "iterations": 2_000,
                "salt_bytes": 16,
            },
        )
        invitation = bootstrap_lifecycle.invite_account(
            principal_id="principal_m402_approval_bootstrap",
            login_id="m402-approval-bootstrap",
            roles=["member"],
        )
        bootstrap_account = bootstrap_lifecycle.accept_invitation(
            invitation.token, password="M402-approval-Bootstrap-Passw0rd!"
        )
        bootstrap_key = bootstrap_lifecycle.issue_key(
            bootstrap_account.account_id,
            scope="workspace",
            actions=["read", "write", "control"],
        )
        created = http_json(
            base_url, "POST", "/api/v1/projects", {"name": PROJECT_NAME}, key=bootstrap_key.key
        )
        if not created.ok:
            _assertion(context, "platform.project.create", False, json.dumps(created.payload)[:400])
            return _finish(context, "platform_project_create_failed")
        project_id = str((created.payload or {})["project_id"])
        identities = provision_identities(identity_db, project=project_id)
        operator_key = identities["operator"]["key"]

        # ---- the request step: four real proposals + pending approvals ----
        approvals = [
            _create_approval(base_url, operator_key, project_id, index) for index in range(1, 5)
        ]
        _assertion(
            context,
            "platform.approvals_created",
            all(item["approval_ref"] and item["requester_ref"] for item in approvals)
            and all(
                _approval_state(base_url, operator_key, item["approval_ref"]) == "pending"
                for item in approvals
            ),
            json.dumps(
                [
                    {
                        "ref": item["approval_ref"],
                        "requester": item["requester_ref"],
                        "budget": item["budget"],
                    }
                    for item in approvals
                ]
            )[:900],
        )
        _write_json(out, "approvals.json", approvals)

        observed_browsers: dict[str, str] = {}

        def open_browser(label: str, identity: str, channel: str = "") -> BrowserSession:
            session = BrowserSession(
                label=label,
                key=identities[identity]["key"],
                out_dir=out,
                base_url=base_url,
                channel=channel,
                playwright=playwright,
            )
            session.open()
            observed_browsers[label] = f"{channel or 'chromium'}/{session.version}"
            session.goto_workbench()
            install_bridge(session.page, project_id)
            return session

        operator = open_browser("operator-chromium", "operator")
        reviewer = open_browser("reviewer-edge", "reviewer", channel="msedge")
        auditor = open_browser("auditor-chromium-readonly", "auditor")
        _add_artifact(context, operator.screenshot("01-operator-workbench.png").name)
        _add_artifact(context, reviewer.screenshot("02-reviewer-edge-workbench.png").name)

        # ---- requester/approver separation: the operator cannot self-approve
        self_approve = operator.evaluate(
            "(args) => window.__m402.researchDecide(args.ref, 'approve', args.revision)",
            {
                "ref": approvals[0]["approval_ref"],
                "revision": approvals[0]["approval_revision"],
            },
        )
        _assertion(
            context,
            "operator.self_approve_denied",
            not self_approve.get("ok")
            and _rule_of(self_approve) == "access_denied"
            and "different verified principal" in str(self_approve.get("message") or ""),
            json.dumps(
                {
                    "status": self_approve.get("status"),
                    "rule": _rule_of(self_approve),
                    "message": str(self_approve.get("message") or "")[:300],
                }
            ),
        )
        _add_artifact(context, operator.screenshot("03-operator-self-approve-denied.png").name)

        # ---- the reviewer (second identity) approves two approvals --------
        approved_first = reviewer.evaluate(
            "(args) => window.__m402.researchDecide(args.ref, 'approve', args.revision)",
            {
                "ref": approvals[0]["approval_ref"],
                "revision": approvals[0]["approval_revision"],
            },
        )
        first_state = _approval_state(base_url, operator_key, approvals[0]["approval_ref"])
        approved_second = reviewer.evaluate(
            "(args) => window.__m402.researchDecide(args.ref, 'approve', args.revision)",
            {
                "ref": approvals[2]["approval_ref"],
                "revision": approvals[2]["approval_revision"],
            },
        )
        second_state = _approval_state(base_url, operator_key, approvals[2]["approval_ref"])
        _assertion(
            context,
            "reviewer.approve_two",
            bool(approved_first.get("ok"))
            and bool(approved_second.get("ok"))
            and first_state == "approved"
            and second_state == "approved",
            json.dumps(
                {
                    "first": {
                        "browser": approved_first.get("ok"),
                        "platform": first_state,
                        "message": str(approved_first.get("message") or "")[:240],
                        "args": approvals[0]["approval_revision"],
                    },
                    "second": {
                        "browser": approved_second.get("ok"),
                        "platform": second_state,
                        "message": str(approved_second.get("message") or "")[:240],
                        "args": approvals[2]["approval_revision"],
                    },
                }
            )[:1000],
        )
        _add_artifact(context, reviewer.screenshot("04-reviewer-edge-approved.png").name)

        # ---- rejection negative (reviewer) -------------------------------
        rejected = reviewer.evaluate(
            "(args) => window.__m402.researchDecide(args.ref, 'reject', args.revision)",
            {
                "ref": approvals[1]["approval_ref"],
                "revision": approvals[1]["approval_revision"],
            },
        )
        rejected_state = _approval_state(base_url, operator_key, approvals[1]["approval_ref"])
        _assertion(
            context,
            "reviewer.reject_negative",
            bool(rejected.get("ok")) and rejected_state == "rejected",
            json.dumps({"browser": rejected.get("ok"), "platform": rejected_state})[:400],
        )
        _add_artifact(context, reviewer.screenshot("05-reviewer-edge-rejected.png").name)

        # ---- revocation (the approver, not the requester) ----------------
        revoked = reviewer.evaluate(
            "(ref) => window.__m402.researchRevoke(ref)", approvals[2]["approval_ref"]
        )
        revoked_state = _approval_state(base_url, operator_key, approvals[2]["approval_ref"])
        _assertion(
            context,
            "operator.revoke_negative",
            bool(revoked.get("ok")) and revoked_state == "revoked",
            json.dumps(
                {"browser": revoked.get("ok"), "platform": revoked_state, "rule": _rule_of(revoked)}
            )[:400],
        )
        _add_artifact(context, reviewer.screenshot("06-reviewer-revoked.png").name)

        # ---- a revoked approval can no longer authorize a submit ----------
        revoked_submit = reviewer.evaluate(
            "(args) => window.__m402.submitWithApproval(args.ref, args.body)",
            {
                "ref": approvals[2]["approval_ref"],
                "body": dict(PLAN, project_id=project_id, budget=BUDGET),
            },
        )
        _assertion(
            context,
            "revoked_approval.cannot_submit",
            not revoked_submit.get("ok"),
            json.dumps(
                {
                    "status": revoked_submit.get("status"),
                    "rule": _rule_of(revoked_submit),
                    "message": str(revoked_submit.get("message") or "")[:300],
                }
            ),
        )

        # ---- auditor: read-only identity cannot read or decide ------------
        read = auditor.evaluate(
            "(ref) => window.__m402.researchFetch(ref)", approvals[0]["approval_ref"]
        )
        escalate = auditor.evaluate(
            "(args) => window.__m402.researchDecide(args.ref, 'approve', args.revision)",
            {
                "ref": approvals[3]["approval_ref"],
                "revision": approvals[3]["approval_revision"],
            },
        )
        denial_status = int(escalate.get("status") or 0)
        read_status = int(read.get("status") or 0)
        _assertion(
            context,
            "auditor.not_requester_not_approver",
            not read.get("ok") and not read.get("value"),
            json.dumps(
                {
                    "status": read_status,
                    "rule": _rule_of(read),
                    "message": str(read.get("message") or "")[:240],
                }
            ),
        )
        _assertion(
            context,
            "auditor.decision_denied",
            not escalate.get("ok") and not escalate.get("value"),
            json.dumps(
                {
                    "status": denial_status,
                    "rule": _rule_of(escalate),
                    "message": str(escalate.get("message") or "")[:300],
                }
            ),
        )
        _add_artifact(context, auditor.screenshot("07-auditor-readonly-denied.png").name)

        # ---- budget: the approved range cannot be widened at submit ------
        before_jobs = _platform_job_count(base_url, operator_key, project_id)
        over_budget_submit = operator.evaluate(
            "(args) => window.__m402.submitWithApproval(args.ref, args.body)",
            {
                "ref": approvals[0]["approval_ref"],
                "body": dict(PLAN, project_id=project_id, budget=OVER_BUDGET),
            },
        )
        after_jobs = _platform_job_count(base_url, operator_key, project_id)
        budget_status = int(over_budget_submit.get("status") or 0)
        budget_rule = _rule_of(over_budget_submit)
        budget_message = str(over_budget_submit.get("message") or "")
        budget_rules = {"approved_budget_mismatch", "budget_exceeds_approved_range"}
        _assertion(
            context,
            "budget.over_approved_refused",
            not over_budget_submit.get("ok")
            and (budget_rule in budget_rules or budget_status in {400, 403, 409, 422}),
            json.dumps(
                {"status": budget_status, "rule": budget_rule, "message": budget_message[:300]}
            )[:600],
        )
        _assertion(
            context,
            "budget.no_task_created",
            after_jobs == before_jobs == 0,
            f"jobs before={before_jobs} after={after_jobs}",
        )
        _add_artifact(context, operator.screenshot("08-operator-budget-denied.png").name)
        _write_json(
            out,
            "budget-denial.json",
            {
                "approved_range": BUDGET,
                "over_budget_request": OVER_BUDGET,
                "approval_ref": approvals[0]["approval_ref"],
                "submit": {
                    "ok": bool(over_budget_submit.get("ok")),
                    "status": budget_status,
                    "rule": budget_rule,
                    "message": budget_message[:600],
                },
                "job_count": {"before": before_jobs, "after": after_jobs},
            },
        )

        # ---- browser records -------------------------------------------
        records = {
            "operator": operator.recorder.to_dict(),
            "reviewer": reviewer.recorder.to_dict(),
            "auditor": auditor.recorder.to_dict(),
        }
        _write_json(out, "browser-console.json", records)
        _write_json(
            out,
            "browser-network.json",
            {
                label: {
                    "statuses": record["response_statuses"],
                    "failures": [item for item in record["responses"] if item["status"] >= 400],
                    "requests_failed": record["requests_failed"],
                }
                for label, record in records.items()
            },
        )
        page_errors = [
            error for record in records.values() for error in record.get("page_errors") or []
        ]
        _assertion(context, "browser.no_page_errors", not page_errors, json.dumps(page_errors[:3]))

        budget_denial = budget_rule.startswith("budget") or "budget" in budget_message.lower()
        denial_evidence = (
            "budget_denied: a submit bound to an approved approval but carrying a budget above "
            "the approved range was refused "
            f"(HTTP {budget_status}, rule={budget_rule or 'untyped'}); "
            f"no task was created (jobs {before_jobs}->{after_jobs})"
            if budget_denial
            else "authorization_denied: the platform refused the over-budget submit "
            f"(HTTP {budget_status}, rule={budget_rule or 'untyped'}) and refused the read-only "
            f"auditor's decision (HTTP {denial_status}); no task was created"
        )
        denial_kind = "budget" if budget_denial else "authorization"
        observations = publish_observations(
            context,
            {
                "transport": "present",
                "controller": "present",
                "model": "absent",
                "qc": "absent",
                "lsf": "absent",
                "browser": "present",
                "artifact": "absent",
                "denial": "present",
            },
            model_calls=0,
            model_evidence=(
                "this row spends no model request: approval and budget decisions are platform "
                "authority actions, not planning calls (0 calls)"
            ),
            denial_evidence=denial_evidence,
            denial_kind=denial_kind,
        )
        _assertion(context, "observations.published", bool(observations.get("observed")))

        for name in (
            "approvals.json",
            "budget-denial.json",
            "browser-console.json",
            "browser-network.json",
            "controller.log",
        ):
            _add_artifact(context, name)
        _write_json(
            out,
            "approval-browser.json",
            {
                "schema": "acp2.m4-02.approval-browser.v1",
                "project_id": project_id,
                "approvals": approvals,
                "states": {
                    "first_approved": first_state,
                    "second_approved_then_revoked": revoked_state,
                    "rejected": rejected_state,
                },
                "self_approve": {
                    "status": self_approve.get("status"),
                    "rule": _rule_of(self_approve),
                },
                "revoked_submit": {
                    "status": revoked_submit.get("status"),
                    "rule": _rule_of(revoked_submit),
                },
                "auditor": {"read_status": read_status, "decision_status": denial_status},
                "budget_denial": {
                    "status": budget_status,
                    "rule": budget_rule,
                    "denial_kind": denial_kind,
                    "job_count": {"before": before_jobs, "after": after_jobs},
                },
                "browsers": observed_browsers,
                "seconds": round(time.time() - started, 1),
            },
        )
        _add_artifact(context, "approval-browser.json")
        context.extensions.update(
            {
                "acceptance.operation_class": "standalone",
                "acceptance.target": "local",
                "experience.approval_budget": {
                    "phase": "approval",
                    "mode": "live-browser",
                    "approvals": [
                        {
                            "ref": item["approval_ref"],
                            "requester": item["requester_ref"],
                            "budget": item["budget"],
                        }
                        for item in approvals
                    ],
                    "states": {
                        "first_approved": first_state,
                        "second_approved_then_revoked": revoked_state,
                        "rejected": rejected_state,
                    },
                    "self_approve_refused": _rule_of(self_approve),
                    "auditor": {"read_status": read_status, "decision_status": denial_status},
                    "budget_denial": {
                        "status": budget_status,
                        "rule": budget_rule,
                        "denial_kind": denial_kind,
                    },
                    "browsers": observed_browsers,
                },
            }
        )
        return _finish(context, "approval_budget_row_failed")
    except BrowserPrerequisiteError as exc:
        return _not_verified(context, f"browser_prerequisite:{exc}")
    except Exception as exc:  # noqa: BLE001 - the scenario boundary reports failures
        import traceback

        _assertion(
            context,
            "approval_budget.row",
            False,
            f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-4000:]}",
        )
        return _finish(context, "approval_budget_row_exception")
    finally:
        try:
            playwright.stop()
        finally:
            platform.stop()
            shutil.rmtree(runtime_root, ignore_errors=True)
