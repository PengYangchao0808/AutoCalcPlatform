"""Behavior checks and three-state results for the isolated F/S harness."""

from __future__ import annotations

import json
import shlex
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

if __package__:
    from .browser_runtime import chromium_launch_options
else:
    from browser_runtime import chromium_launch_options

PASS = "PASS"
FAIL = "FAIL"
NOT_VERIFIED = "NOT_VERIFIED"


def aggregate_result(checks: list[dict[str, Any]]) -> str:
    """A missing required check cannot become a compatibility PASS."""
    states = [check.get("result", PASS if check.get("ok") else FAIL) for check in checks]
    if any(state == PASS and check.get("ok") is False for check, state in zip(checks, states)):
        return FAIL
    if FAIL in states or any(state not in (PASS, FAIL, NOT_VERIFIED) for state in states):
        return FAIL
    return NOT_VERIFIED if not states or NOT_VERIFIED in states else PASS


def queue_refresh_verified(responses: list[dict[str, Any]], failed_urls: list[str]) -> bool:
    """Cached rows cannot hide a failed or missing queue refresh request."""
    queue_path = "/api/v2/task-view"
    queue = [item for item in responses if urlsplit(item["url"]).path == queue_path]
    return (
        bool(queue)
        and all(200 <= item["status"] < 300 for item in queue)
        and not any(urlsplit(url).path == queue_path for url in failed_urls)
    )


def browser_check(base: str, *, expected_job_id: str = "") -> dict[str, Any]:
    """Execute the actual bundle and its queue refresh against this cell's S."""
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError as exc:
        return {"result": NOT_VERIFIED, "ok": False, "reason": f"Playwright unavailable: {exc}"}
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(**chromium_launch_options(no_sandbox=True))
        except (Error, OSError) as exc:
            return {"result": NOT_VERIFIED, "ok": False, "reason": f"Chromium unavailable: {exc}"}
        errors: list[str] = []
        requests: list[dict[str, Any]] = []
        failed_urls: list[str] = []
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("requestfailed", lambda request: failed_urls.append(request.url))
            page.on(
                "response",
                lambda response: (
                    requests.append(
                        {
                            "url": response.url,
                            "status": response.status,
                        }
                    )
                    if "/api/" in response.url
                    else None
                ),
            )
            response = page.goto(base + "/", wait_until="load", timeout=45000)
            if errors:
                return {
                    "result": FAIL,
                    "ok": False,
                    "page_errors": errors,
                    "api_responses": requests,
                    "chromium": browser.version,
                }
            page.wait_for_function(
                "() => typeof refreshJobs === 'function' && "
                "typeof workflowCatalogCache !== 'undefined' && workflowCatalogCache.length > 0",
                timeout=20000,
            )
            refresh_start = len(requests)
            failed_start = len(failed_urls)
            facts = page.evaluate(
                """async (jobId) => {
                await refreshJobs();
                return {
                    workflows: workflowCatalogCache.length,
                    jobsArray: Array.isArray(jobsCache),
                    jobVisible: !jobId || jobsCache.some(j => String(j.id || j.job_id) === jobId)
                };
            }""",
                expected_job_id,
            )
            ok = bool(
                response
                and response.status == 200
                and not errors
                and facts["jobsArray"]
                and facts["jobVisible"]
                and queue_refresh_verified(requests[refresh_start:], failed_urls[failed_start:])
            )
            return {
                "result": PASS if ok else FAIL,
                "ok": ok,
                "facts": facts,
                "page_errors": errors,
                "api_responses": requests,
                "failed_requests": failed_urls,
                "chromium": browser.version,
            }
        except Error as exc:
            return {
                "result": FAIL,
                "ok": False,
                "reason": str(exc),
                "page_errors": errors,
                "api_responses": requests,
                "chromium": browser.version,
            }
        finally:
            browser.close()


def validate_legacy_scenarios(raw: Any, workflows: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """Require one ordinary API request fixture for every contracted workflow."""
    if not isinstance(raw, dict) or set(raw) != set(workflows):
        raise ValueError(f"legacy scenarios must contain exactly {workflows}")
    for workflow, scenario in raw.items():
        if not isinstance(scenario, dict) or not isinstance(scenario.get("body"), dict):
            raise ValueError(f"{workflow}: an ordinary submission body is required")
        if scenario["body"].get("workflow") != workflow:
            raise ValueError(f"{workflow}: body.workflow must match; fake is not a substitute")
        if not isinstance(scenario["body"].get("input"), dict) or not scenario["body"]["input"]:
            raise ValueError(f"{workflow}: non-empty input is required")
        if scenario.get("expected_status", "completed") not in ("completed", "waiting_review"):
            raise ValueError(f"{workflow}: expected_status must be completed or waiting_review")
    return raw


def legacy_submission_checks(
    base: str,
    workflows: tuple[str, ...],
    scenarios: dict[str, dict[str, Any]] | None,
    *,
    get: Callable[[str, str], tuple[int, Any]],
    post: Callable[..., tuple[int, Any]],
    timeout: float = 120,
) -> list[dict[str, Any]]:
    """Submit each real workflow; missing fixtures remain explicitly unverified.

    The service under test owns validation, materialization, dispatch and
    execution. Fixtures may target a controlled scientific backend, but may
    never replace the workflow by the demo ``fake`` workflow.
    """
    if scenarios is None:
        return [
            {
                "workflow": workflow,
                "result": NOT_VERIFIED,
                "ok": False,
                "reason": (
                    "ordinary submission input/backend fixture missing; "
                    "CLI argument smoke is insufficient"
                ),
            }
            for workflow in workflows
        ]
    validate_legacy_scenarios(scenarios, workflows)
    rows: list[dict[str, Any]] = []
    for workflow in workflows:
        scenario = scenarios[workflow]
        body = scenario["body"]
        code, created = post(base, "/api/jobs", body)
        row: dict[str, Any] = {
            "workflow": workflow,
            "request": body,
            "create_status": code,
            "result": FAIL,
            "ok": False,
        }
        rows.append(row)
        if code != 201 or not isinstance(created, dict) or not created.get("job_id"):
            row["response"] = created
            continue
        job_id = str(created["job_id"])
        row["job_id"] = job_id
        deadline = time.monotonic() + timeout
        status = ""
        while time.monotonic() < deadline:
            status_code, record = get(base, f"/api/jobs/{job_id}")
            if status_code != 200 or not isinstance(record, dict):
                break
            status = str(record.get("status") or "").lower()
            if status in ("completed", "failed", "cancelled", "waiting_review"):
                break
            time.sleep(0.25)
        row["terminal_status"] = status
        events_code, events = get(base, f"/api/jobs/{job_id}/files/WORK/00_RUNTIME/events.jsonl")
        commands: list[list[str]] = []
        if events_code == 200 and isinstance(events, str):
            for line in events.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "process.starting":
                    command = str(event.get("cmd") or "")
                    try:
                        commands.append(shlex.split(command))
                    except ValueError:
                        pass
        legacy = len(commands) == 1 and commands[0][1:5] == ["-m", "acp.cli", "run", workflow]
        row["commands"] = commands
        row["legacy_entry_selected"] = legacy
        row["ok"] = status == scenario.get("expected_status", "completed") and legacy
        row["result"] = PASS if row["ok"] else FAIL
    return rows
