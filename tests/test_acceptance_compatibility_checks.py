"""Required compatibility coverage cannot be replaced by successful smoke probes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/acceptance"))
from compatibility_checks import (
    aggregate_result,
    browser_check,
    legacy_submission_checks,
    queue_refresh_verified,
    validate_legacy_scenarios,
)
from four_combinations import LEGACY_WORKFLOWS, _CellOriginServer


def _scenarios() -> dict:
    return {
        workflow: {"body": {"workflow": workflow, "input": {"source": "CCO"}}}
        for workflow in LEGACY_WORKFLOWS
    }


def test_successful_smoke_cannot_hide_unverified_behavior() -> None:
    assert aggregate_result([{"ok": True}, {"result": "NOT_VERIFIED"}]) == "NOT_VERIFIED"
    assert aggregate_result([{"result": "NOT_VERIFIED"}, {"ok": False}]) == "FAIL"
    assert aggregate_result([]) == "NOT_VERIFIED"
    assert aggregate_result([{"result": "PASS", "ok": False}]) == "FAIL"


def test_old_cached_rows_cannot_hide_failed_queue_refresh() -> None:
    queue = "http://cell.test/api/v2/task-view?offset=0"
    assert queue_refresh_verified([{"url": queue, "status": 200}], [])
    assert not queue_refresh_verified([{"url": queue, "status": 500}], [])
    assert not queue_refresh_verified([{"url": queue, "status": 403}], [])
    assert not queue_refresh_verified([{"url": queue, "status": 200}], [queue])
    assert not queue_refresh_verified([], [])


def test_missing_ordinary_fixtures_remain_seven_unverified_rows() -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("missing fixtures must not submit a fake replacement")

    rows = legacy_submission_checks("unused", LEGACY_WORKFLOWS, None, get=forbidden, post=forbidden)
    assert {row["workflow"] for row in rows} == set(LEGACY_WORKFLOWS)
    assert all(row["result"] == "NOT_VERIFIED" for row in rows)
    assert aggregate_result(rows) == "NOT_VERIFIED"


def test_fake_workflow_cannot_stand_in_for_a_legacy_workflow() -> None:
    scenarios = _scenarios()
    scenarios["nmr"]["body"]["workflow"] = "fake"
    with pytest.raises(ValueError, match="fake is not a substitute"):
        validate_legacy_scenarios(scenarios, LEGACY_WORKFLOWS)


@pytest.mark.parametrize("wrong_entry,failed", [(False, False), (True, False), (False, True)])
def test_ordinary_submissions_require_each_workflow_and_actual_legacy_dispatch(
    wrong_entry, failed
) -> None:
    submitted = []

    def post(base, path, body):
        assert path == "/api/jobs"
        submitted.append(body["workflow"])
        return 201, {"job_id": body["workflow"]}

    def get(base, path):
        workflow = path.split("/")[3]
        if path.endswith("events.jsonl"):
            command = f"python -m acp.cli run {workflow} --input CCO"
            if wrong_entry:
                command = "python -m acp_services.worker_bootstrap --envelope frozen.json"
            return 200, json.dumps({"type": "process.starting", "cmd": command}) + "\n"
        return 200, {"status": "failed" if failed else "completed"}

    rows = legacy_submission_checks("unused", LEGACY_WORKFLOWS, _scenarios(), get=get, post=post)
    assert submitted == list(LEGACY_WORKFLOWS)
    assert aggregate_result(rows) == ("FAIL" if wrong_entry or failed else "PASS")


def test_real_browser_rejects_successfully_served_invalid_javascript(tmp_path: Path) -> None:
    pytest.importorskip("playwright.sync_api", reason="NOT_VERIFIED: Playwright unavailable")
    (tmp_path / "ACP_Workbench_v2.html").write_text('<script src="/broken.js"></script>')
    (tmp_path / "broken.js").write_text("this is deliberately invalid JavaScript {")
    origin = _CellOriginServer(tmp_path, 1)
    origin.start()
    try:
        result = browser_check(origin.base)
    finally:
        origin.stop()
    if result["result"] == "NOT_VERIFIED":
        pytest.skip(result["reason"])
    assert result["result"] == "FAIL"
    assert result["page_errors"]


def test_real_browser_old_rows_do_not_hide_a_failed_api_refresh(tmp_path: Path) -> None:
    pytest.importorskip("playwright.sync_api", reason="NOT_VERIFIED: Playwright unavailable")
    # The production refresh handler retains the previous rows after catching
    # a transport failure. A live browser must still reject that stale view.
    (tmp_path / "ACP_Workbench_v2.html").write_text("""<script>
        let workflowCatalogCache = [{id:'singlepoint'}];
        let jobsCache = [{id:'cached-job'}];
        async function refreshJobs() {
            try {
                const response = await fetch('/api/v2/task-view');
                if (!response.ok) throw new Error('refresh failed');
                jobsCache = await response.json();
            } catch (error) { console.error(error); }
        }
        </script>""")
    origin = _CellOriginServer(tmp_path, 1)
    origin.start()
    try:
        result = browser_check(origin.base, expected_job_id="cached-job")
    finally:
        origin.stop()
    if result["result"] == "NOT_VERIFIED":
        pytest.skip(result["reason"])
    assert result["facts"]["jobVisible"] is True
    assert result["page_errors"] == []
    assert result["result"] == "FAIL"
