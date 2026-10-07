"""Real-HTTP double-POST bounded-latency harness for in-place reset (BUG-9(a), T11).

Reproduce-or-refute the repo-external user report: two consecutive in-place-reset
POSTs (``POST /jobs/{id}/rerun`` then ``POST /jobs/{id}/edit-recalculate``
``mode=in_place``) issued by a plain ``urllib`` client left the client with no
response.  Unlike the only prior art (TestClient double-POSTs in
``test_acp_job_edit.py`` / ``test_acp_api_v1.py``) this harness boots a REAL
uvicorn server in a background thread on a temp run root and a dynamic port, so
request handling, threadpool dispatch and the scheduler lock contend exactly as
in production.  CPython ``urllib`` sends ``Connection: close`` per request (the
original report's client semantics; curl keep-alive was fine).

A7 bounds asserted per probe: each reset POST answers within 60 s, and a
concurrent ``GET /api/status`` answers within 5 s while the reset is in
progress.  Observed latencies are recorded in
``.omo/evidence/acp-legacy-bug-remediation/task-11-acp-legacy-bug-remediation.json``
(verdict reproduced / not_reproduced; on a hang a faulthandler thread dump is
saved for TODO 12).

Boundaries: the ``fake`` workflow is runner-internal (no QC binaries; completes
in-process), so NO manifest / output-dir / artifact semantics are asserted —
latency and HTTP status shapes only.  ``fake`` is not in ``EDIT_ACTIVE_WORKFLOWS``
(route gate returns 422 before touching the manager), so the edit probe runs on
a seeded terminal singlepoint job whose QC dispatch is dropped hermetically
(the established ``_execute_submission`` seam from ``test_acp_job_edit.py``);
both probes still traverse the manager's ``_inplace_requeue_locked`` critical
section under ``manager._lock``.
"""

# allow: SIZE_OK — plan T11 mandates ONE harness file; the server fixture, probe
# timing, concurrent health sampling and evidence assembly must stay co-reviewable
# in a single unit, and the repo's test convention is flat monolithic test files
# (test_acp_job_edit.py / test_acp_api_v1.py run 1.5–2.7k lines).

from __future__ import annotations

import atexit
import faulthandler
import http.client
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager

logger = logging.getLogger(__name__)

_HOST = "127.0.0.1"
_POST_BUDGET_S = 60.0  # A7: each in-place-reset POST must answer within 60 s
_HEALTH_BUDGET_S = 5.0  # A7: concurrent GET /api/status must answer within 5 s
_HEALTH_URLOPEN_TIMEOUT_S = 5.5  # > budget: a wedged server surfaces as latency
_POST_URLOPEN_TIMEOUT_S = 65.0  # > wall budget, so the join detects the hang first
_POST_WALL_BUDGET_S = 61.0
_READY_TIMEOUT_S = 15.0
_TERMINAL_TIMEOUT_S = 30.0
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})

_XYZ_COOH = "3\n\nO 0.0 0.0 0.0\nC 1.2 0.0 0.0\nH 2.0 0.0 0.0\n"
_EDIT_PROBE_ID = "t11_edit_probe"
_EDIT_PROBE_INPUT = {
    "source_type": "xyz_text",
    "source": _XYZ_COOH,
    "charge": 0,
    "multiplicity": 1,
}
_EDIT_PROBE_SEED_SPEC = JobSpec(
    workflow="singlepoint",
    name=_EDIT_PROBE_ID,
    input=dict(_EDIT_PROBE_INPUT),
    method={
        "schema_id": "dft_singlepoint",
        "profile_id": "default",
        "levels": {"sp": {"functional": "r2SCAN", "basis": "def2-TZVP"}},
    },
    resources={"nproc": 8, "mem": 16},
)
_FAKE_SUBMIT = {
    "workflow": "fake",
    "name": "t11_double_post_probe",
    "input": {"source": "CCO", "demo_frames": False},
    "method": {"protocol": "ext"},
}

_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / ".omo"
    / "evidence"
    / "acp-legacy-bug-remediation"
    / "task-11-acp-legacy-bug-remediation.json"
)

_INERT_ROOT: Path | None = None


@dataclass(frozen=True)
class _LiveApi:
    """Handle to the harness server: HTTP base + in-process manager access."""

    base_url: str
    port: int
    manager: JobManager
    edit_probe_id: str


def _ensure_inert_root() -> Path:
    """Create the throwaway run root that pins server.py's module-level app.

    ``acp.api.server`` runs ``app = create_app()`` at import, spawning a live
    JobManager (poller/reconciler threads + instance lock).  If that module-level
    manager ever landed on THIS test's run root it would be a second, unshimmed
    scheduler race-dispatching queued jobs (including the singlepoint edit probe
    → real QC) against the fixture's manager.  The first import of the session
    is therefore pinned to an inert directory; later imports hit the module cache.
    """
    global _INERT_ROOT  # session-scoped singleton (atexit-cleaned)
    if _INERT_ROOT is None:
        _INERT_ROOT = Path(tempfile.mkdtemp(prefix="acp_t11_inert_"))
        atexit.register(shutil.rmtree, _INERT_ROOT, ignore_errors=True)
    return _INERT_ROOT


def _http(
    base_url: str,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float,
) -> dict[str, Any]:
    """One urllib request (fresh connection per call — ``Connection: close``)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    request = urllib.request.Request(base_url + path, data=data, headers=headers, method=method)
    started = time.monotonic()
    http_status: int | None = None
    raw = ""
    error: str | None = None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            http_status = response.status
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        http_status = exc.code
        raw = exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
        error = f"{type(exc).__name__}: {exc}"
    latency = round(time.monotonic() - started, 4)
    parsed: Any = None
    if raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
    return {
        "http": http_status,
        "latency_s": latency,
        "error": error,
        "body": parsed,
        "raw": raw[:600],
    }


def _wait_ready(base_url: str) -> float:
    """Bounded readiness poll on GET /api/status; returns first-hit latency."""
    deadline = time.monotonic() + _READY_TIMEOUT_S
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _http(base_url, "GET", "/api/status", timeout=5.0)
        if last["http"] == 200 and last["error"] is None:
            return float(last["latency_s"])
        time.sleep(0.1)
    pytest.fail(f"GET /api/status not ready within {_READY_TIMEOUT_S}s: last={last}")


def _wait_terminal(
    base_url: str, job_id: str, manager: JobManager
) -> tuple[str | None, str | None, float]:
    """Poll job status until terminal AND the submission slot clears (T2 lesson)."""
    deadline = time.monotonic() + _TERMINAL_TIMEOUT_S
    started = time.monotonic()
    last_status: str | None = None
    while time.monotonic() < deadline:
        response = _http(base_url, "GET", f"/api/v1/jobs/{job_id}", timeout=10.0)
        payload = response["body"]
        if response["http"] == 200 and isinstance(payload, dict):
            last_status = str(payload.get("status") or "")
            if last_status in _TERMINAL_STATUSES and job_id not in manager._submission_jobs:
                return last_status, last_status, round(time.monotonic() - started, 3)
        time.sleep(0.25)
    return None, last_status, round(time.monotonic() - started, 3)


def _install_hermetic_dispatch(manager: JobManager) -> None:
    """Drop non-fake dispatches so no QC subprocess can ever spawn (A2 boundary).

    ``fake`` passes through to the real in-process runner; the seeded singlepoint
    edit probe requeues as QUEUED but is never executed.  Mirrors the
    ``manager._execute_submission`` seam used by ``test_acp_job_edit.py``.
    """
    original = manager._execute_submission  # type: ignore[method-assign]

    def _dispatch(job_id: str) -> None:
        record = manager.store.get(job_id)
        if record is not None and record.spec.workflow != "fake":
            logger.info("T11 harness: dropping %s dispatch (hermetic, no QC)", job_id)
            with manager._lock:
                manager._submission_jobs.discard(job_id)
            return
        original(job_id)

    manager._execute_submission = _dispatch  # type: ignore[method-assign]


def _seed_edit_probe(manager: JobManager) -> str:
    """Create the terminal singlepoint record the edit-recalculate probe edits."""
    work_dir = Path(manager.run_root) / "default" / _EDIT_PROBE_ID
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "input.xyz").write_text(_XYZ_COOH, encoding="utf-8")
    manager.store.create(
        JobRecord(
            id=_EDIT_PROBE_ID,
            spec=_EDIT_PROBE_SEED_SPEC,
            status=JobStatus.FAILED,
            work_dir=str(work_dir),
            project_id=manager.default_project_id,
        )
    )
    return _EDIT_PROBE_ID


def _probe_post_with_health(
    base_url: str, path: str, *, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """POST one reset while a tight-loop GET /api/status probe runs concurrently."""
    stop = threading.Event()
    samples: list[dict[str, Any]] = []

    def _health_loop() -> None:
        while not stop.is_set():
            result = _http(base_url, "GET", "/api/status", timeout=_HEALTH_URLOPEN_TIMEOUT_S)
            ended = time.monotonic()
            samples.append(
                {
                    "t_start": round(ended - float(result["latency_s"]), 4),
                    "t_end": round(ended, 4),
                    "latency_s": result["latency_s"],
                    "http": result["http"],
                    "error": result["error"],
                }
            )

    health_thread = threading.Thread(target=_health_loop, daemon=True, name="t11-health-probe")
    health_thread.start()
    time.sleep(0.05)  # one baseline sample in flight before the POST lands

    post: dict[str, Any] = {"t_start": round(time.monotonic(), 4)}

    def _run_post() -> None:
        post.update(_http(base_url, "POST", path, body=body, timeout=_POST_URLOPEN_TIMEOUT_S))
        post["t_end"] = round(time.monotonic(), 4)

    post_thread = threading.Thread(target=_run_post, daemon=True, name="t11-reset-post")
    post_thread.start()
    post_thread.join(timeout=_POST_WALL_BUDGET_S)
    hung = post_thread.is_alive()
    if hung:
        post["hung"] = True
        post["latency_s"] = round(time.monotonic() - float(post["t_start"]), 3)
        post["error"] = post.get("error") or f"no response within {_POST_WALL_BUDGET_S}s"
    stop.set()
    health_thread.join(timeout=10)

    t_end = float(post.get("t_end") or time.monotonic())
    concurrent = sum(
        1
        for sample in samples
        if float(sample["t_start"]) <= t_end and float(sample["t_end"]) >= float(post["t_start"])
    )
    return {"post": post, "health": samples, "concurrent_samples": concurrent, "hung": hung}


def _summarize_health(report: dict[str, Any]) -> dict[str, Any]:
    samples: list[dict[str, Any]] = report["health"]
    latencies = [float(sample["latency_s"]) for sample in samples]
    return {
        "samples": len(samples),
        "concurrent_with_post": report["concurrent_samples"],
        "max_latency_s": max(latencies) if latencies else None,
        "errors": sum(1 for sample in samples if sample["error"]),
        "non_200": sum(1 for sample in samples if sample["http"] != 200),
        "detail": samples,
    }


def _dump_threads(reason: str) -> str:
    """Capture all-thread state (lock holders etc.) for TODO 12 dissection."""
    path = _EVIDENCE_PATH.with_name("task-11-thread-dump.txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(f"# T11 thread dump — {reason}\n")
        faulthandler.dump_traceback(file=handle, all_threads=True)
    return str(path)


def _write_evidence(payload: dict[str, Any]) -> Path:
    _EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _EVIDENCE_PATH.with_name(_EVIDENCE_PATH.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, _EVIDENCE_PATH)
    return _EVIDENCE_PATH


@pytest.fixture()
def live_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[_LiveApi, None, None]:
    """Real uvicorn service on a temp run root + dynamic free port (no TestClient)."""
    import acp.scheduler.capabilities as capabilities_module
    import acp.scheduler.manager as manager_module

    # Pin server.py's module-level app to an inert root BEFORE the first import.
    monkeypatch.setenv("ACP_RUN_ROOT", str(_ensure_inert_root()))
    from acp.api.server import create_app

    # Local capability always satisfied → seeded singlepoint edit validation is
    # hermetic on machines without QC binaries (same seam as test_acp_job_edit).
    monkeypatch.setattr(capabilities_module, "local_satisfies", lambda required: True)
    monkeypatch.setattr(manager_module, "local_satisfies", lambda required: True)
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))

    app = create_app(run_root=tmp_path, max_running=2)
    config = uvicorn.Config(app, host=_HOST, port=0, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="t11-uvicorn")
    thread.start()
    try:
        deadline = time.monotonic() + 20.0
        while not server.started:
            if not thread.is_alive():
                pytest.fail("uvicorn thread exited before startup completed")
            if time.monotonic() > deadline:
                pytest.fail("uvicorn did not report started within 20 s")
            time.sleep(0.05)
        port = int(server.servers[0].sockets[0].getsockname()[1])
        base_url = f"http://{_HOST}:{port}"
        ready_latency = _wait_ready(base_url)
        manager = app.state.job_manager
        _install_hermetic_dispatch(manager)
        edit_probe_id = _seed_edit_probe(manager)
        logger.info("T11 harness ready: %s (GET /api/status %.3fs)", base_url, ready_latency)
        yield _LiveApi(base_url=base_url, port=port, manager=manager, edit_probe_id=edit_probe_id)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=10)


@pytest.mark.slow
def test_double_in_place_reset_posts_bounded_latency(live_api: _LiveApi) -> None:
    """Two consecutive urllib reset POSTs must answer within A7 bounds (IS-9)."""
    base = live_api.base_url
    errors: list[str] = []
    evidence: dict[str, Any] = {
        "task": "T11",
        "goal": "acp-legacy-bug-remediation",
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "harness": "tests/test_api_inplace_reset_http.py",
        "bounds_s": {"post": _POST_BUDGET_S, "health": _HEALTH_BUDGET_S},
        "environment": {
            "python": f"{os.sys.version_info.major}.{os.sys.version_info.minor}",
            "uvicorn": uvicorn.__version__,
            "host": _HOST,
            "port": live_api.port,
            "client": "urllib.request — one fresh connection per request",
            "connection_header": "Connection: close (CPython urllib default)",
        },
        "jobs": {"edit_probe_job": live_api.edit_probe_id, "edit_probe_workflow": "singlepoint"},
        "setup": {},
        "post_rerun": {},
        "post_edit_in_place": {},
        "health": {},
        "adversarial_classes": {
            "hung_commands": "THE class under test — 60s POST / 5s health bounds; "
            "faulthandler thread dump captured on any latency violation",
            "flaky_tests": "dynamic free port (bind :0), bounded readiness poll, "
            "tight-loop health probe with generous-but-bounded timeouts; run 3x for stability",
            "stale_state": "fresh tmp run_root per run; module-level server app pinned "
            "to an inert root; no cross-test state",
            "repeated_interrupts": "the two consecutive in-place-reset POSTs ARE the "
            "repeated-operation probe",
            "prompt_injection": "not applicable — fixed API payloads, no untrusted text",
            "malformed_input": "not applicable — payloads are the API's own contract shapes",
            "dirty_worktree": "not applicable beyond forbidden files — harness writes only "
            "the evidence JSON / thread dump",
        },
        "observations": [],
        "probes_completed": False,
        "errors": errors,
        "thread_dump_path": None,
        "verdict": "not_reproduced",
    }
    try:
        # -- Setup: submit the fake workflow (in-process runner, no QC binaries).
        submit = _http(base, "POST", "/api/v1/jobs", body=_FAKE_SUBMIT, timeout=30.0)
        evidence["setup"]["submit"] = {
            "http": submit["http"],
            "latency_s": submit["latency_s"],
            "error": submit["error"],
        }
        job_id: str | None = None
        if submit["http"] == 201 and isinstance(submit["body"], dict):
            job_id = str(submit["body"].get("job_id") or "") or None
        if job_id is None:
            errors.append(
                f"fake submit failed: http={submit['http']} error={submit['error']} "
                f"raw={submit['raw'][:300]!r}"
            )
        else:
            evidence["jobs"]["fake_workflow_job"] = job_id

        # -- Setup: wait for the fake job to reach terminal (rerun precondition).
        if not errors:
            terminal, last, waited = _wait_terminal(base, job_id, live_api.manager)
            evidence["setup"]["fake_terminal"] = {
                "status": terminal,
                "last_status": last,
                "waited_s": waited,
            }
            if terminal is None:
                errors.append(
                    f"fake job {job_id} not terminal within {_TERMINAL_TIMEOUT_S}s "
                    f"(last={last}, submission_slots={set(live_api.manager._submission_jobs)})"
                )

        # -- Setup: edit draft → source revision for the in-place edit probe.
        draft: dict[str, Any] = {"http": None, "latency_s": None, "error": "skipped"}
        if not errors:
            draft = _http(
                base, "GET", f"/api/v1/jobs/{live_api.edit_probe_id}/edit-draft", timeout=15.0
            )
            draft_payload = draft["body"]
            revision = (
                str(draft_payload.get("source_revision") or "")
                if isinstance(draft_payload, dict)
                else ""
            )
            evidence["setup"]["edit_draft"] = {
                "http": draft["http"],
                "latency_s": draft["latency_s"],
                "error": draft["error"],
            }
            if draft["http"] != 200 or not revision:
                errors.append(
                    f"edit-draft failed: http={draft['http']} error={draft['error']} "
                    f"raw={draft['raw'][:300]!r}"
                )

        # -- POST #1: in-place rerun (manager._inplace_requeue_locked under _lock).
        if not errors:
            rerun = _probe_post_with_health(base, f"/api/v1/jobs/{job_id}/rerun")
            post = rerun["post"]
            evidence["post_rerun"] = {
                "path": f"/api/v1/jobs/{job_id}/rerun",
                "http": post.get("http"),
                "latency_s": post.get("latency_s"),
                "error": post.get("error"),
                "hung": rerun["hung"],
                "body_status": (post.get("body") or {}).get("status")
                if isinstance(post.get("body"), dict)
                else None,
            }
            evidence["health"]["during_rerun"] = _summarize_health(rerun)

        # -- POST #2: edit-recalculate mode=in_place, issued back-to-back.
        if not errors:
            edit_body = {
                "mode": "in_place",
                "input": dict(_EDIT_PROBE_INPUT),
                "method": {"levels": {"sp": {"functional": "wB97X-D4", "basis": "def2-TZVP"}}},
                "resources": {"nproc": 16},
                "expected_source_revision": revision,
                "request_id": "req-t11-inplace-probe",
            }
            edit = _probe_post_with_health(
                base, f"/api/v1/jobs/{live_api.edit_probe_id}/edit-recalculate", body=edit_body
            )
            post = edit["post"]
            parsed = post.get("body") if isinstance(post.get("body"), dict) else {}
            evidence["post_edit_in_place"] = {
                "path": f"/api/v1/jobs/{live_api.edit_probe_id}/edit-recalculate",
                "http": post.get("http"),
                "latency_s": post.get("latency_s"),
                "error": post.get("error"),
                "hung": edit["hung"],
                "operation": parsed.get("operation"),
                "attempt": parsed.get("attempt"),
                "body_status": parsed.get("status"),
            }
            evidence["health"]["during_edit"] = _summarize_health(edit)
    finally:
        posts = [evidence["post_rerun"], evidence["post_edit_in_place"]]
        health_summaries = [summary for summary in evidence["health"].values() if summary]
        latency_violation = any(
            post.get("hung") or float(post.get("latency_s") or 0.0) > _POST_BUDGET_S
            for post in posts
        )
        health_violation = any(
            (summary.get("errors") or 0) > 0
            or (summary.get("non_200") or 0) > 0
            or float(summary.get("max_latency_s") or 0.0) > _HEALTH_BUDGET_S
            for summary in health_summaries
        )
        evidence["probes_completed"] = all(
            post.get("latency_s") is not None for post in posts
        ) and not any(post.get("hung") for post in posts)
        if latency_violation or health_violation:
            evidence["verdict"] = "reproduced"
            reason = "hang" if any(post.get("hung") for post in posts) else "slow-response"
            evidence["thread_dump_path"] = _dump_threads(reason)
        for name, summary in evidence["health"].items():
            if summary:
                evidence["observations"].append(
                    f"health during {name}: {summary['samples']} samples, "
                    f"{summary['concurrent_with_post']} concurrent with POST, "
                    f"max {summary['max_latency_s']}s"
                )
        evidence["observations"].append(
            "edit probe runs on a seeded singlepoint job because fake is excluded "
            "from EDIT_ACTIVE_WORKFLOWS (route-level 422); its QC dispatch is "
            "dropped hermetically — both probes traverse "
            "manager._inplace_requeue_locked under manager._lock"
        )
        evidence["observations"].append(
            "fake is runner-internal: no manifest/output-dir semantics asserted "
            "(latency + HTTP status shapes only)"
        )
        evidence_path = _write_evidence(evidence)
        print(
            json.dumps(
                {
                    "verdict": evidence["verdict"],
                    "probes_completed": evidence["probes_completed"],
                    "post_rerun": evidence["post_rerun"],
                    "post_edit_in_place": evidence["post_edit_in_place"],
                    "health_max_s": {
                        name: summary.get("max_latency_s")
                        for name, summary in evidence["health"].items()
                        if summary
                    },
                    "thread_dump_path": evidence["thread_dump_path"],
                    "errors": errors,
                    "evidence": str(evidence_path),
                },
                indent=2,
                ensure_ascii=False,
            ),
            flush=True,
        )

    # -- Assertions (after evidence is on disk so failures keep their data).
    rerun = evidence["post_rerun"]
    edit = evidence["post_edit_in_place"]
    assert not errors, "harness errors: " + " | ".join(errors)
    assert not rerun["hung"], (
        f"rerun POST never answered within {_POST_WALL_BUDGET_S}s — hang reproduced; "
        f"thread dump: {evidence['thread_dump_path']}"
    )
    assert rerun["latency_s"] <= _POST_BUDGET_S, (
        f"rerun POST took {rerun['latency_s']}s > {_POST_BUDGET_S}s bound"
    )
    assert rerun["http"] == 200, f"rerun POST http={rerun['http']} error={rerun['error']}"
    assert not edit["hung"], (
        f"edit-recalculate POST never answered within {_POST_WALL_BUDGET_S}s — hang reproduced; "
        f"thread dump: {evidence['thread_dump_path']}"
    )
    assert edit["latency_s"] <= _POST_BUDGET_S, (
        f"edit-recalculate POST took {edit['latency_s']}s > {_POST_BUDGET_S}s bound"
    )
    assert edit["http"] == 200, (
        f"edit-recalculate POST http={edit['http']} error={edit['error']} "
        f"(expected 200 in_place requeue)"
    )
    assert edit["operation"] == "in_place", f"unexpected edit operation: {edit['operation']!r}"
    for label, key in (("rerun", "during_rerun"), ("edit", "during_edit")):
        summary = evidence["health"].get(key) or {}
        assert summary.get("samples"), f"no health samples collected during {label} probe"
        assert not summary.get("errors"), (
            f"GET /api/status errored during {label} reset: "
            f"{[s for s in summary['detail'] if s['error']][:3]}"
        )
        assert not summary.get("non_200"), (
            f"GET /api/status non-200 during {label} reset: "
            f"{[s for s in summary['detail'] if s['http'] != 200][:3]}"
        )
        assert float(summary.get("max_latency_s") or 999.0) <= _HEALTH_BUDGET_S, (
            f"GET /api/status during {label} reset took {summary.get('max_latency_s')}s "
            f"> {_HEALTH_BUDGET_S}s bound"
        )
        assert summary.get("concurrent_with_post", 0) >= 1, (
            f"no health sample overlapped the {label} POST window — probe vacuous"
        )
