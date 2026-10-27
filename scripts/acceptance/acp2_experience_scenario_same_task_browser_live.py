"""``same-task-browser-live`` — one TaskRef, read in two real browsers.

This body is the M4-02 (G05/G18) delivery of the C15 row.  The registered
scenario id is unchanged; what changes is that the row is now **executed**:

1. a real Controller (``uvicorn`` + SQLite) in ``api-key`` identity mode over an
   isolated identity database with three real accounts — ``operator``
   (read/write/control), ``reviewer`` (read/write) and ``auditor`` (read only);
2. the **operator** browser is the Playwright-bundled Chromium.  It loads the
   committed Workbench page from that Controller, makes one real planning model
   call through the page's own transport (``POST /api/v2/model/plan``), submits a
   real ``singlepoint`` job (real ORCA subprocess), waits for the terminal state
   and reads the TaskRef (job, attempt, release, artifact digests) through the
   page modules and the ``jobDetail`` client query;
3. the operator then **refreshes** the page and re-reads the same TaskRef (the
   refresh must not create a second task and must keep the same identity);
4. the **reviewer** browser is the system Microsoft Edge (``channel="msedge"``);
   it reads the *same* TaskRef and must report the same job/attempt/release;
5. the **auditor** (read-only key) opens a third context, reads the same TaskRef
   (allowed) and attempts a submit (denied by the platform — a real 403 with a
   typed rule).  A read-only identity can never escalate.

Recorded evidence: every API status the browsers saw, console messages, page
errors, screenshots of each step, the platform TaskRef/attempt/release, the
artifact digests read back from the platform, and the denial fact.  A screenshot
never replaces the platform receipt — the receipt and the TaskRef do.

A missing prerequisite (Playwright, browsers, model credential, Node-free page)
answers ``NOT_VERIFIED`` with a precise reason; a wrong result is ``FAIL``.
"""

from __future__ import annotations

import json
import os
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
    EXIT_NOT_VERIFIED,
    EXIT_PASS,
    _add_artifact,
    _assertion,
    _finish,
    _not_verified,
    _out_dir,
    _write_json,
    publish_observations,
    register,
)

SCENARIO_ID = "same-task-browser-live"

PROJECT_NAME = "ACP 2.0 M4-02 browser leg"
JOB_BODY: dict[str, Any] = {
    "workflow": "singlepoint",
    "input": {"source": "CCO", "source_type": "smiles"},
    "method": {"method": "B3LYP D3 ma-def2-SVP"},
    "resources": {"cores": 1, "nproc": 4},
    "name": "m402-browser-singlepoint",
}
MODEL_MESSAGES = [
    {
        "role": "user",
        "content": (
            'Return exactly one JSON object: {"note": "<one short sentence about the '
            'same-task browser acceptance run>"} and nothing else.'
        ),
    }
]

TERMINAL = {"completed", "failed", "cancelled"}


def _wait_job(browser: BrowserSession, job_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.time() + timeout
    payload: dict[str, Any] = {}
    while time.time() < deadline:
        result = browser.evaluate("(id) => window.__m402.job(id)", job_id)
        if isinstance(result, dict) and result.get("ok"):
            payload = result.get("value") or {}
            if str(payload.get("status") or "") in TERMINAL:
                return payload
        time.sleep(1.0)
    return payload


def _read_same_task(browser: BrowserSession, job_id: str) -> dict[str, Any]:
    """Read the TaskRef through the page's own client query + job record."""
    record = browser.evaluate("(id) => window.__m402.job(id)", job_id)
    detail = browser.evaluate(
        "(id) => window.__m402.call('GET', '/v1/jobs/' + encodeURIComponent(id) + '/detail')",
        job_id,
    )
    return {"record": record, "detail": detail}


def handle(context: Any) -> int:
    """Run the row; Playwright's sync API needs a thread without a live loop."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run_row, context)
        try:
            return int(future.result(timeout=max(60.0, float(context.timeout))))
        except Exception as exc:  # noqa: BLE001 - reported as a row failure
            import traceback

            _assertion(
                context,
                "browser.row",
                False,
                f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-4000:]}",
            )
            return _finish(context, "browser_row_exception")


def _run_row(context: Any) -> int:
    out = _out_dir(context)
    runtime_root = out / "_runtime"
    started = time.time()

    if not (repo_root() / "frontend" / "ACP_Workbench_v2.html").is_file():
        return _not_verified(context, "source_tree_absent:frontend/ACP_Workbench_v2.html")

    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError as exc:
        return _not_verified(context, f"playwright_absent:{exc}")

    # The row makes one real model call: without an approved credential the
    # declared ``model=present`` observation cannot be honest.
    if not os.environ.get("ACP2_MODEL_API_KEY"):
        return _not_verified(context, "no_model_credential:ACP2_MODEL_API_KEY_unset")

    from playwright.sync_api import sync_playwright

    runtime_root.mkdir(parents=True, exist_ok=True)
    identity_db = runtime_root / "acp_identity.db"
    platform = PlatformController(
        run_root=runtime_root / "runs",
        identity_db=identity_db,
        log_path=out / "controller.log",
    )
    observed_browsers: dict[str, str] = {}
    playwright = sync_playwright().start()
    try:
        platform.start()
        base_url = platform.base_url

        # ---- identity bootstrap: one unscoped operator key creates the project,
        # then the three real identities are scoped to it.
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
            principal_id="principal_m402_bootstrap",
            login_id="m402-bootstrap",
            roles=["member"],
        )
        bootstrap_account = bootstrap_lifecycle.accept_invitation(
            invitation.token, password="M402-bootstrap-Strong-Passw0rd!"
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
            _assertion(
                context,
                "platform.project.create",
                False,
                f"HTTP {created.status}: {json.dumps(created.payload)[:500]}",
            )
            return _finish(context, "platform_project_create_failed")
        project_id = str((created.payload or {})["project_id"])
        identities = provision_identities(identity_db, project=project_id)

        # ---- operator browser (bundled Chromium) -----------------------
        operator = BrowserSession(
            label="operator-chromium",
            key=identities["operator"]["key"],
            out_dir=out,
            base_url=base_url,
            playwright=playwright,
        )
        operator.open()
        observed_browsers["operator"] = f"chromium/{operator.version}"
        title = operator.goto_workbench()
        install_bridge(operator.page, project_id)
        _assertion(context, "browser.page_loaded", bool(title), title)
        _add_artifact(context, operator.screenshot("01-operator-workbench.png").name)
        _add_artifact(context, operator.html("01-operator-workbench.html").name)

        # ---- one real planning model call, issued by the page ----------
        operator.recorder.window()
        model = operator.evaluate(
            "(body) => window.__m402.call('POST', '/v2/model/plan', body)",
            {
                "phase": "plan",
                "round": 0,
                "messages": MODEL_MESSAGES,
                "max_output_tokens": 64,
            },
        )
        model_ok = bool(isinstance(model, dict) and model.get("ok"))
        model_calls = 1 if model_ok else 0
        model_receipt = ""
        if model_ok:
            model_receipt = str((model.get("value") or {}).get("receipt_id") or "")
        _assertion(
            context,
            "browser.model_call",
            model_ok and bool(model_receipt),
            json.dumps({"ok": model_ok, "receipt_present": bool(model_receipt)})[:800],
        )
        _write_json(out, "model-call.json", model if isinstance(model, dict) else {"value": model})

        # ---- submit a real job from the page, then wait -----------------
        submitted = operator.evaluate(
            "(body) => window.__m402.submitJob(body)", dict(JOB_BODY, project_id=project_id)
        )
        submit_ok = bool(isinstance(submitted, dict) and submitted.get("ok"))
        job_id = ""
        if submit_ok:
            job_id = str((submitted.get("value") or {}).get("job_id") or "")
        _assertion(
            context,
            "browser.submit",
            submit_ok and bool(job_id),
            json.dumps(submitted)[:800],
        )
        _add_artifact(context, operator.screenshot("02-operator-submitted.png").name)
        record = (
            _wait_job(operator, job_id, timeout=min(float(context.timeout), 420.0))
            if job_id
            else {"status": "not_submitted"}
        )
        _write_json(out, "job-record.json", record)
        completed = str(record.get("status") or "") == "completed"
        _assertion(context, "platform.job_completed", completed, str(record.get("status")))
        _add_artifact(context, operator.screenshot("03-operator-job-completed.png").name)

        first_read = _read_same_task(operator, job_id)
        detail = first_read["detail"]
        detail_value = (detail or {}).get("value") if isinstance(detail, dict) else None
        artifacts = []
        if isinstance(detail_value, dict):
            job = detail_value.get("job") or {}
            artifacts = (job.get("result") or {}).get("artifacts") or []
        digests = sorted(
            {
                str(item.get("checksum"))
                for item in artifacts
                if isinstance(item, dict) and item.get("checksum")
            }
        )
        _assertion(
            context,
            "browser.artifact_digest_read",
            bool(digests),
            f"{len(digests)} digest(s): {digests[:2]}",
        )
        attempt = int(record.get("attempt") or 0)
        release_id = str(record.get("release_id") or "")
        release_digest = str(record.get("release_digest") or "")
        _assertion(
            context,
            "platform.task_ref_identity",
            bool(job_id) and attempt >= 1 and bool(release_id) and bool(release_digest),
            json.dumps({"job_id": job_id, "attempt": attempt, "release_id": release_id}),
        )

        # ---- refresh: same task, same identity, no second task ----------
        operator.goto_workbench()
        install_bridge(operator.page, project_id)
        refreshed = _read_same_task(operator, job_id)
        refreshed_record = (refreshed["record"] or {}).get("value") or {}
        _add_artifact(context, operator.screenshot("04-operator-refreshed.png").name)
        _assertion(
            context,
            "browser.refresh_same_task",
            str(refreshed_record.get("id") or "") == job_id
            and int(refreshed_record.get("attempt") or 0) == attempt
            and str(refreshed_record.get("release_id") or "") == release_id,
            json.dumps(
                {
                    "id": refreshed_record.get("id"),
                    "attempt": refreshed_record.get("attempt"),
                    "release_id": refreshed_record.get("release_id"),
                }
            ),
        )

        # ---- reviewer browser: system Edge, second identity ------------
        reviewer = BrowserSession(
            label="reviewer-edge",
            key=identities["reviewer"]["key"],
            out_dir=out,
            base_url=base_url,
            channel="msedge",
            playwright=playwright,
        )
        reviewer.open()
        observed_browsers["reviewer"] = f"msedge/{reviewer.version}"
        reviewer.goto_workbench()
        install_bridge(reviewer.page, project_id)
        second = _read_same_task(reviewer, job_id)
        second_record = (second["record"] or {}).get("value") or {}
        _assertion(
            context,
            "second_identity.same_task",
            str(second_record.get("id") or "") == job_id
            and int(second_record.get("attempt") or 0) == attempt
            and str(second_record.get("release_id") or "") == release_id,
            json.dumps(
                {
                    "browser": reviewer.version,
                    "id": second_record.get("id"),
                    "attempt": second_record.get("attempt"),
                    "release_id": second_record.get("release_id"),
                }
            ),
        )
        _add_artifact(context, reviewer.screenshot("05-reviewer-edge-same-task.png").name)

        # ---- auditor: read-only identity cannot escalate ---------------
        auditor = BrowserSession(
            label="auditor-chromium-readonly",
            key=identities["auditor"]["key"],
            out_dir=out,
            base_url=base_url,
            playwright=playwright,
        )
        auditor.open()
        observed_browsers["auditor"] = f"chromium/{auditor.version}"
        auditor.goto_workbench()
        install_bridge(auditor.page, project_id)
        read_allowed = auditor.evaluate("(id) => window.__m402.job(id)", job_id)
        denied_submit = auditor.evaluate(
            "(body) => window.__m402.submitJob(body)", dict(JOB_BODY, project_id=project_id)
        )
        denial_status = int((denied_submit or {}).get("status") or 0)
        denial_code = str((denied_submit or {}).get("code") or "")
        _assertion(
            context,
            "auditor.read_allowed",
            bool(isinstance(read_allowed, dict) and read_allowed.get("ok")),
            json.dumps((read_allowed or {}).get("status")),
        )
        _assertion(
            context,
            "auditor.submit_denied",
            denial_status == 403 and not (denied_submit or {}).get("ok"),
            json.dumps(
                {
                    "status": denial_status,
                    "code": denial_code,
                    "message": str((denied_submit or {}).get("message") or "")[:300],
                }
            ),
        )
        _add_artifact(context, auditor.screenshot("06-auditor-readonly-denied.png").name)
        _write_json(
            out,
            "auditor-denial.json",
            {
                "identity": "auditor",
                "actions": identities["auditor"]["actions"],
                "read": {"ok": bool((read_allowed or {}).get("ok"))},
                "submit": {
                    "ok": bool((denied_submit or {}).get("ok")),
                    "status": denial_status,
                    "code": denial_code,
                    "message": str((denied_submit or {}).get("message") or "")[:600],
                },
            },
        )

        # ---- browser-side records --------------------------------------
        windows = {
            "operator": operator.recorder.to_dict(),
            "reviewer": reviewer.recorder.to_dict(),
            "auditor": auditor.recorder.to_dict(),
        }
        _write_json(out, "browser-console.json", windows)
        _write_json(
            out,
            "browser-network.json",
            {
                label: {
                    "statuses": record["response_statuses"],
                    "failures": [item for item in record["responses"] if item["status"] >= 400],
                    "requests_failed": record["requests_failed"],
                }
                for label, record in windows.items()
            },
        )
        page_errors = [
            error for record in windows.values() for error in record.get("page_errors") or []
        ]
        _assertion(
            context,
            "browser.no_page_errors",
            not page_errors,
            json.dumps(page_errors[:3]),
        )
        # The page's own bootstrap asks for its demo project; the row binds the
        # real project right after load, so only the driven window is asserted.
        driven_console = operator.recorder.console_errors(since=max(0, operator.recorder.mark))
        _assertion(
            context,
            "browser.no_console_errors_after_bind",
            not driven_console,
            json.dumps(driven_console[:3]),
        )
        for name in (
            "browser-console.json",
            "browser-network.json",
            "job-record.json",
            "model-call.json",
            "auditor-denial.json",
        ):
            _add_artifact(context, name)

        observations = publish_observations(
            context,
            {
                "transport": "present",
                "controller": "present",
                "model": "present",
                "qc": "present",
                "lsf": "absent",
                "browser": "present",
                "artifact": "present",
                "denial": "present",
            },
            model_calls=model_calls,
            model_evidence=(
                f"real planning call from the browser through the Controller gateway; "
                f"receipt={model_receipt}"
                if model_ok
                else "no accepted model receipt"
            ),
            denial_evidence=(
                "authorization_denied: read-only auditor identity attempted a real submit "
                f"and the platform answered HTTP {denial_status} ({denial_code})"
            ),
            denial_kind="authorization",
        )
        _assertion(context, "observations.published", bool(observations.get("observed")))

        context.refs.update(
            {
                "job_id": job_id,
                "attempt": attempt,
                "release_id": release_id or None,
            }
        )
        context.extensions.update(
            {
                "acceptance.operation_class": "read",
                "acceptance.target": "local",
                "experience.task_ref": {
                    "job_id": job_id,
                    "attempt": attempt,
                    "release_id": release_id,
                    "release_digest": release_digest,
                },
                "experience.artifact_digests": digests,
                "experience.browsers": observed_browsers,
                "experience.project_id": project_id,
                "experience.browser_seconds": round(time.time() - started, 1),
            }
        )
        console_summary = {
            label: {
                "total_errors": len(record.console_errors()),
                "after_bind_errors": len(record.console_errors(since=record.mark))
                if label == "operator-chromium"
                else None,
                "entries": record.console_errors()[:6],
            }
            for label, record in (
                ("operator-chromium", operator.recorder),
                ("reviewer-edge", reviewer.recorder),
                ("auditor-chromium-readonly", auditor.recorder),
            )
        }
        _write_json(out, "browser-console-errors.json", console_summary)
        _add_artifact(context, "browser-console-errors.json")
        _write_json(
            out,
            "same-task-browser.json",
            {
                "schema": "acp2.m4-02.same-task-browser.v1",
                "task_ref": {
                    "job_id": job_id,
                    "attempt": attempt,
                    "release_id": release_id,
                    "release_digest": release_digest,
                },
                "artifact_digests": digests,
                "browsers": observed_browsers,
                "auditor_denial": {"status": denial_status, "code": denial_code},
                "model_receipt": model_receipt,
            },
        )
        _add_artifact(context, "same-task-browser.json")
        _add_artifact(context, "controller.log")
        code = _finish(context, "browser_row_failed")
        return code if code != EXIT_PASS else EXIT_PASS
    except BrowserPrerequisiteError as exc:
        return _not_verified(context, f"browser_prerequisite:{exc}")
    except Exception as exc:  # noqa: BLE001 - the scenario boundary reports failures
        import traceback

        _assertion(
            context,
            "browser.row",
            False,
            f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-5000:]}",
        )
        return _finish(context, "browser_row_exception")
    finally:
        try:
            playwright.stop()
        finally:
            platform.stop()
            # Cleanup: the isolated run root + identity database are not evidence.
            shutil.rmtree(runtime_root, ignore_errors=True)


register(SCENARIO_ID, handle)


if __name__ == "__main__":  # pragma: no cover - bodies run under the harness
    raise SystemExit(EXIT_NOT_VERIFIED)
