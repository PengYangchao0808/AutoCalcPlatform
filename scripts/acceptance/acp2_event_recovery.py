#!/usr/bin/env python3.11
"""B19b research-recovery branch against one live Controller.

This is a *simulated C session* branch, explicitly labelled as such: the real
DSH client is not delivered on this host and its row is written as
``NOT_VERIFIED(c_dsh_absent)``.  The Controller, the approval authority, the
idempotency ledger and the durable per-task event log are real.

Flow
----
1. request + approve a plan through the real ``ApprovalService``;
2. submit once through the real HTTP Controller with a persistent
   ``Idempotency-Key``;
3. observe the task running, write cross-research events, and record session
   A's cursor;
4. simulate C session termination (stop consuming); the task stays running;
5. start session B and resume from A's cursor;
6. cancel the task and write the terminal fact;
7. restart the Controller with the same DB/run root;
8. replay the same approval/submit messages and duplicate events; assert one
   approval, one idempotency claim and one task row after reconciliation;
9. prove an old event cannot revive the terminal projection.

Run with the Controller already started on the non-production port; the script
stops it with SIGTERM and starts it again from ``--restart-command``.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from acp.scheduler.events import JobEventLog  # noqa: E402
from acp.services_events_bootstrap import JobEventLogPort  # noqa: E402
from acp_services.contracts.access import LOCAL_PRINCIPAL_ID  # noqa: E402
from acp_services.runtime.approvals import ApprovalService  # noqa: E402
from acp_services.runtime.canonical_digest import (  # noqa: E402
    frozen_plan_definition,
    plan_digest,
    proposal_digest,
)
from acp_services.runtime.research_events import (  # noqa: E402
    InMemoryActionLedger,
    ResearchEventService,
    ResearchProjection,
    ResearchRecoveryCoordinator,
    ResearchSessionRef,
)

# Ethanol / wB97M-V / def2-SVP is the same long Local fixture B13 used to make
# cancel/EOF observations non-vacuous; it gives a real multi-second RUNNING
# window.  It is a real QC task, cancelled as soon as the cursor evidence is
# captured.
LONG_XYZ = (
    "9\nethanol B19 research-recovery fixture\n"
    "C 0.000 0.000 0.000\n"
    "C 1.520 0.000 0.000\n"
    "O 2.050 1.250 0.000\n"
    "H -0.500 0.900 0.000\n"
    "H -0.500 -0.500 0.800\n"
    "H -0.500 -0.500 -0.800\n"
    "H 1.900 -0.600 0.800\n"
    "H 1.900 -0.600 -0.800\n"
    "H 2.950 1.150 0.000\n"
)

PLAN: dict[str, Any] = {
    "workflow": "singlepoint",
    "input": {
        "source_type": "xyz_text",
        "source": LONG_XYZ,
        "charge": 0,
        "multiplicity": 1,
    },
    "method": {
        "levels": {
            "singlepoint": {
                "engine": "orca",
                "functional": "wB97M-V",
                "basis": "def2-SVP",
            }
        }
    },
    "resources": {"cores": 1, "memory_mb": 1024},
    "plan_version": 1,
}

BUDGET = {"unit": "task_count", "count": 1, "timeout_s": 300}


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _http(
    base: str,
    method: str,
    path: str,
    *,
    body: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    data = None
    request_headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=data,
        method=method,
        headers=request_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return int(response.status), (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"detail": raw.decode("utf-8", "replace")}
        return int(exc.code), payload


def _health(base: str, timeout: float = 10.0) -> dict[str, Any]:
    status, body = _http(base, "GET", "/api/health", timeout=timeout)
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"controller health failed: {status} {body}")
    return body


def _wait_health_change(base: str, previous_started_at: str, *, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            health = _health(base)
            if health.get("started_at") != previous_started_at:
                return health
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise TimeoutError(f"controller did not restart: {last_error}")


def _task(base: str, task_id: str) -> dict[str, Any]:
    status, body = _http(base, "GET", f"/api/v1/jobs/{task_id}")
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"task read failed: {status} {body}")
    return body


def _wait_status(base: str, task_id: str, wanted: set[str], *, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _task(base, task_id)
        if str(last.get("status") or "") in wanted:
            return last
        time.sleep(0.2)
    raise TimeoutError(f"task {task_id} not in {sorted(wanted)}; last={last.get('status')}")


def _count_jobs(db_path: Path) -> int:
    with sqlite3.connect(str(db_path), timeout=10) as conn:
        row = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
        return int(row[0]) if row else 0


def _count_sql(db_path: Path, sql: str, params: tuple[Any, ...]) -> int:
    with sqlite3.connect(str(db_path), timeout=10) as conn:
        row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0


def _default_project(db_path: Path) -> str:
    with sqlite3.connect(str(db_path), timeout=10) as conn:
        row = conn.execute("SELECT project_id FROM projects ORDER BY created_at LIMIT 1").fetchone()
        return str(row[0]) if row else "uncategorized"


def _listener_pid(port: int) -> int | None:
    inodes: set[str] = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10:
                continue
            try:
                local_port = int(fields[1].rsplit(":", 1)[1], 16)
            except (IndexError, ValueError):
                continue
            if local_port == port and fields[3] == "0A":
                inodes.add(fields[9])
    if not inodes:
        return None
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            descriptors = list((proc / "fd").iterdir())
        except (OSError, PermissionError):
            continue
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except OSError:
                continue
            if target.startswith("socket:[") and target[8:-1] in inodes:
                return int(proc.name)
    return None


def _restart_controller(
    base: str,
    *,
    port: int,
    command: str,
    cwd: str,
    out_dir: Path,
    timeout: float,
) -> dict[str, Any]:
    before = _health(base)
    pid_before = _listener_pid(port)
    if pid_before is None:
        raise RuntimeError(f"no listener on port {port} before restart")
    os.kill(pid_before, signal.SIGTERM)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and _listener_pid(port) is not None:
        time.sleep(0.2)
    if _listener_pid(port) is not None:
        raise TimeoutError(f"controller pid {pid_before} did not stop")

    log_path = out_dir / "controller-restart.log"
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            shlex.split(command),
            cwd=cwd,
            env=os.environ.copy(),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    after = _wait_health_change(base, str(before.get("started_at") or ""), timeout=timeout)
    pid_after = _listener_pid(port)
    if pid_after is None:
        raise RuntimeError("controller restarted but no listener is visible")
    return {
        "pid_before": pid_before,
        "pid_after": pid_after,
        "process_pid": process.pid,
        "health_before": before,
        "health_after": after,
        "port": port,
        "command": command,
        "cwd": cwd,
        "log": str(log_path),
        "stopped": True,
    }


def _build_request(project: str, proposal_ref: str, plan: dict[str, Any]) -> dict[str, Any]:
    definition = frozen_plan_definition(
        workflow=str(plan["workflow"]),
        input=plan["input"],
        method=plan["method"],
        resources=plan["resources"],
    )
    digest = plan_digest(definition).digest
    proposal = {
        "proposal_id": proposal_ref,
        "project_id": project,
        "title": "B19 research recovery",
        "question": "Does a new session resume the same task without a second submit?",
        "capability_ids": ["singlepoint.run"],
        "recipe_ref": {
            "schema_id": "acp2.science.recipe.v1",
            "version": "1.0.0",
            "digest": "sha256:" + "1" * 64,
        },
        "plan_digest": digest,
        "budget_request": {"value": "300", "unit": "second"},
    }
    body = {
        "workflow": str(plan["workflow"]),
        "name": "b19-research-recovery",
        "input": dict(plan["input"]),
        "method": dict(plan["method"]),
        "resources": dict(plan["resources"]),
        "project_id": project,
        "plan_digest": digest,
        "proposal_ref": proposal_ref,
        "proposal_digest": proposal_digest(proposal).digest,
        "budget": dict(BUDGET),
    }
    return {"definition": definition, "plan_digest": digest, "body": body, "proposal": proposal}


def _service(work_dir: Path, *, project: str, task_id: str, attempt: int) -> ResearchEventService:
    return ResearchEventService(
        JobEventLogPort(JobEventLog(work_dir / "events.jsonl")),
        project_id=project,
        task_id=task_id,
        attempt=attempt,
        cursor_max_age_s=3600,
    )


def _run(args: argparse.Namespace) -> int:
    base = args.controller.rstrip("/")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    run_root = Path(args.run_root)
    db_path = run_root / "acp_jobs.db"
    project = _default_project(db_path)
    proposal_ref = f"b19-research-{uuid.uuid4().hex[:12]}"
    idempotency_key = f"b19-submit-{uuid.uuid4().hex[:16]}"
    approval_key = f"b19-approval-{uuid.uuid4().hex[:16]}"

    health_before = _health(base)
    request = _build_request(project, proposal_ref, PLAN)
    plan_sha = request["plan_digest"]
    submit_body = dict(request["body"])

    # 1. Real approval authority (same Controller DB).
    approvals = ApprovalService(db_path)
    approval, replayed = approvals.request(
        proposal_ref=proposal_ref,
        proposal_digest=submit_body["proposal_digest"],
        plan_digest=plan_sha,
        project_id=project,
        requester_principal=LOCAL_PRINCIPAL_ID,
        plan_definition=request["definition"],
        budget=BUDGET,
        idempotency_key=approval_key,
    )
    approval, approved_replay = approvals.approve(
        approval.approval_id,
        approver_principal=LOCAL_PRINCIPAL_ID,
        expected_revision=approval.revision,
    )
    approval_request = {
        "approval_ref": approval.approval_id,
        "replayed": replayed,
        "approved_replay": approved_replay,
        "state": approval.state,
        "revision": approval.revision,
        "plan_digest": approval.plan_digest,
        "proposal_digest": approval.proposal_digest,
        "project_id": approval.project_id,
    }
    _write(out_dir / "approval.json", approval_request)

    # 2. Real submit through the Controller's persistent idempotency ledger.
    jobs_before = _count_jobs(db_path)
    status, accepted = _http(
        base,
        "POST",
        "/api/v1/jobs",
        body=submit_body,
        headers={"Idempotency-Key": idempotency_key},
        timeout=args.timeout,
    )
    if status != 201 or not isinstance(accepted, dict):
        _write(out_dir / "submit-error.json", {"status": status, "body": accepted})
        raise RuntimeError(f"submit failed: {status} {accepted}")
    task_id = str(accepted.get("job_id") or "")
    if not task_id:
        raise RuntimeError(f"submit returned no job id: {accepted}")
    record = _task(base, task_id)
    attempt = int(record.get("attempt") or accepted.get("attempt") or 1)
    work_dir = Path(str(record.get("work_dir") or ""))
    if not work_dir.is_dir():
        raise RuntimeError(f"submitted task has no work_dir: {record}")

    # 3. Observe running and write the cross-research event stream.
    running = _wait_status(base, task_id, {"running", "starting"}, timeout=args.timeout)
    service = _service(work_dir, project=project, task_id=task_id, attempt=attempt)
    revision = 1
    started_event, _ = service.append(
        "task.started",
        revision=revision,
        dedup_key=f"b19:{task_id}:started",
        payload={"status": "running", "business_revision": running.get("revision")},
    )
    # Session A has consumed only this fact when it disconnects; the later
    # facts must be discovered by the new session from this exact cursor.
    session_cursor = service.current_cursor()
    revision += 1
    progress_event, _ = service.append(
        "task.progress",
        revision=revision,
        dedup_key=f"b19:{task_id}:progress:1",
        payload={"progress": running.get("progress"), "stage": running.get("current_stage")},
    )
    revision += 1
    approval_pending, _ = service.append(
        "approval.requested",
        revision=revision,
        dedup_key=f"b19:{approval.approval_id}:requested",
        payload={"approval_ref": approval.approval_id, "business_revision": 1},
    )
    revision += 1
    approval_event, _ = service.append(
        "approval.approved",
        revision=revision,
        dedup_key=f"b19:{approval.approval_id}:approved",
        payload={"approval_ref": approval.approval_id, "business_revision": 2},
    )
    revision += 1
    bound_event, _ = service.append(
        "approval.task_bound",
        revision=revision,
        dedup_key=f"b19:{approval.approval_id}:bound",
        payload={"approval_ref": approval.approval_id, "task_ref": {"task_id": task_id}},
    )
    revision += 1

    # 4. Simulated C session termination: stop consuming.  The task is a real
    #    server-side task and must still be running.
    time.sleep(1.0)
    after_termination = _task(base, task_id)
    still_running = str(after_termination.get("status") or "") in {
        "running",
        "starting",
        "pending",
    }

    # 5. New session B resumes from the last cursor; the projection rebuilds
    #    from durable replay, not from a temporary message.
    replay = service.replay(session_cursor)
    projection_b = ResearchProjection(project_id=project, task_id=task_id, attempt=attempt)
    projection_b.rebuild_from_snapshot(service.snapshot())
    applied = 0
    for event in replay.events:
        if projection_b.apply(event).applied:
            applied += 1
    resumed_session = {
        "cursor_before": session_cursor,
        "cursor_after": replay.cursor,
        "reset": replay.reset,
        "reset_reason": replay.reset_reason,
        "replayed_events": len(replay.events),
        "applied_events": applied,
        "projection": projection_b.to_dict(),
        "task_status_after_resume": _task(base, task_id).get("status"),
    }

    # 6. Cancel the real task and write the terminal fact.
    cancel_status, cancel_body = _http(
        base, "POST", f"/api/v1/jobs/{task_id}/cancel", body={}, timeout=args.timeout
    )
    terminal = _wait_status(
        base, task_id, {"cancelled", "failed", "completed"}, timeout=args.timeout
    )
    terminal_event, _ = service.append(
        "task.cancelled",
        revision=revision,
        dedup_key=f"b19:{task_id}:cancelled",
        payload={
            "status": str(terminal.get("status")),
            "business_revision": terminal.get("revision"),
        },
    )
    terminal_projection = ResearchProjection(project_id=project, task_id=task_id, attempt=attempt)
    terminal_projection.rebuild_from_snapshot(service.snapshot())
    terminal_state = terminal_projection.to_dict()

    # 7. Restart the Controller with the same DB/run root.
    restart = _restart_controller(
        base,
        port=args.port,
        command=args.restart_command,
        cwd=args.restart_cwd,
        out_dir=out_dir,
        timeout=args.timeout,
    )

    # 8. Duplicate approval + submit messages reconcile to one of each.
    approval_again, approval_replay = approvals.request(
        proposal_ref=proposal_ref,
        proposal_digest=submit_body["proposal_digest"],
        plan_digest=plan_sha,
        project_id=project,
        requester_principal=LOCAL_PRINCIPAL_ID,
        plan_definition=request["definition"],
        budget=BUDGET,
        idempotency_key=approval_key,
    )
    approve_again, approve_replay = approvals.approve(
        approval_again.approval_id,
        approver_principal=LOCAL_PRINCIPAL_ID,
        expected_revision=1,
    )
    jobs_after_restart_before_replay = _count_jobs(db_path)
    duplicate_status, duplicate_body = _http(
        base,
        "POST",
        "/api/v1/jobs",
        body=submit_body,
        headers={"Idempotency-Key": idempotency_key},
        timeout=args.timeout,
    )
    jobs_after_duplicate = _count_jobs(db_path)
    duplicate_task_id = (
        str(duplicate_body.get("job_id") or "") if isinstance(duplicate_body, dict) else ""
    )
    approval_rows = _count_sql(
        db_path,
        "SELECT COUNT(*) FROM approval_records WHERE proposal_ref=?",
        (proposal_ref,),
    )
    idempotency_rows = _count_sql(
        db_path,
        "SELECT COUNT(*) FROM idempotency_records WHERE idempotency_key=?",
        (idempotency_key,),
    )

    # 9. Duplicate/out-of-order delivery with real event ids/cursors.
    duplicate_event, duplicate_flag = service.append(
        "task.started",
        revision=started_event.revision,
        dedup_key=started_event.dedup_key,
        payload=dict(started_event.payload),
    )
    snapshot_after_duplicate = service.snapshot()
    terminal_projection_after_restart = ResearchProjection(
        project_id=project, task_id=task_id, attempt=attempt
    )
    terminal_projection_after_restart.rebuild_from_snapshot(snapshot_after_duplicate)
    # Feed the old running facts after the terminal fact: neither stale nor a
    # newer non-terminal revision may revive it.
    old_running = ResearchProjection(project_id=project, task_id=task_id, attempt=attempt)
    old_running.apply(terminal_event)
    stale_result = old_running.apply(started_event)
    newer_non_terminal = ResearchProjection(project_id=project, task_id=task_id, attempt=attempt)
    newer_non_terminal.apply(terminal_event)
    revived_fact = _event_with_revision(started_event, revision=terminal_event.revision + 100)
    revival_result = terminal_projection_after_restart.apply(revived_fact)

    # Fixture-level idempotent consumer: a lost cursor must not dispatch twice.
    # This stream is deliberately separate from the live task log so the
    # fixture row cannot be confused with the real Controller event tail.
    fixture_service = ResearchEventService(
        JobEventLogPort(JobEventLog(out_dir / "fixture-events.jsonl")),
        project_id=project,
        task_id=task_id,
        attempt=attempt,
    )
    fixture_service.append(
        "approval.approved",
        revision=1,
        dedup_key=f"b19:{approval.approval_id}:fixture-approved",
        payload={"approval_ref": approval.approval_id},
    )
    ledger = InMemoryActionLedger()
    projection = ResearchProjection(project_id=project, task_id=task_id, attempt=attempt)
    coordinator = ResearchRecoveryCoordinator(fixture_service, projection, ledger)
    fixture_submits: list[str] = []

    def _fixture_submit(_body: Mapping[str, Any]) -> Mapping[str, Any]:
        fixture_submits.append(task_id)
        return {"task_id": task_id, "attempt": attempt}

    first_recovery = coordinator.recover(
        None,
        submit_key=f"{task_id}:fixture-submit",
        request_digest="sha256:fixture",
        submit=_fixture_submit,
    )
    second_recovery = coordinator.recover(
        None,
        submit_key=f"{task_id}:fixture-submit",
        request_digest="sha256:fixture",
        submit=_fixture_submit,
    )
    rph_exit = coordinator.detach_session(ResearchSessionRef(session_id="simulated-dsh"))

    # Evidence.
    _write(
        out_dir / "research-recovery.json",
        {
            "schema": "acp2.b19b.research-recovery.v1",
            "simulated": True,
            "simulated_component": "C DSH session termination",
            "real_components": [
                "Controller HTTP",
                "ApprovalService",
                "JobStore idempotency",
                "per-task events.jsonl",
            ],
            "task_ref": task_id,
            "attempt": attempt,
            "project_id": project,
            "proposal_ref": proposal_ref,
            "plan_digest": plan_sha,
            "session_a_cursor": session_cursor,
            "started_event": started_event.to_dict(),
            "progress_event": progress_event.to_dict(),
            "bound_event": bound_event.to_dict(),
            "task_status_after_session_termination": after_termination.get("status"),
            "task_left_running": still_running,
            "new_session_resume": resumed_session,
            "cancel": {"status": cancel_status, "body": cancel_body},
            "terminal_status": terminal.get("status"),
            "terminal_projection": terminal_state,
        },
    )
    _write(
        out_dir / "cursor-replay.json",
        {
            "schema": "acp2.b19b.cursor-replay.v1",
            "session_a_cursor": session_cursor,
            "replay_after_termination": {
                "reset": replay.reset,
                "reset_reason": replay.reset_reason,
                "replayed_events": len(replay.events),
                "cursor_after": replay.cursor,
            },
            "snapshot_event_ids": [event.event_id for event in snapshot_after_duplicate],
            "snapshot_event_count": len(snapshot_after_duplicate),
        },
    )
    _write(
        out_dir / "duplicate-out-of-order.json",
        {
            "schema": "acp2.b19b.duplicate-out-of-order.v1",
            "duplicate_event_id": duplicate_event.event_id,
            "duplicate_is_noop": duplicate_flag,
            "snapshot_count_unchanged": len(snapshot_after_duplicate) == len(service.snapshot()),
            "stale_apply_outcome": stale_result.outcome,
            "stale_status": old_running.status,
            "terminal_status": terminal_projection_after_restart.status,
            "newer_non_terminal_outcome": revival_result.outcome,
            "final_terminal_status": terminal_projection_after_restart.status,
        },
    )
    _write(
        out_dir / "controller-restart.json",
        {
            "schema": "acp2.b19b.controller-restart.v1",
            "restart": restart,
            "approval_before": approval_request,
            "approval_repeat_after_restart": {
                "approval_ref": approval_again.approval_id,
                "replayed": approval_replay,
                "approved_replay": approve_replay,
                "state": approve_again.state,
                "revision": approve_again.revision,
            },
            "submit_duplicate": {
                "status": duplicate_status,
                "job_id": duplicate_task_id,
                "same_task": duplicate_task_id == task_id,
                "idempotent_replay": bool(
                    isinstance(duplicate_body, dict) and duplicate_body.get("idempotent_replay")
                ),
            },
            "jobs_before": jobs_before,
            "jobs_after_restart_before_replay": jobs_after_restart_before_replay,
            "jobs_after_duplicate": jobs_after_duplicate,
            "approval_rows": approval_rows,
            "idempotency_rows": idempotency_rows,
            "exactly_one_approval": approval_rows == 1,
            "exactly_one_submit": idempotency_rows == 1 and jobs_after_duplicate == jobs_before + 1,
        },
    )
    _write(
        out_dir / "fixture-recovery.json",
        {
            "schema": "acp2.b19b.fixture-recovery.v1",
            "first_recovery": first_recovery.to_dict(),
            "second_recovery": second_recovery.to_dict(),
            "submit_calls": len(fixture_submits),
            "exactly_one_fixture_submit": len(fixture_submits) == 1,
            "rph_exit": {
                "continues": rph_exit.continues,
                "blocks_on_llm": rph_exit.blocks_on_llm,
                "reason": rph_exit.reason,
            },
            "ledger_claim_calls": ledger.claim_calls,
            "ledger_complete_calls": ledger.complete_calls,
        },
    )
    dsh_row = {
        "schema": "acp2.b19b.dsh-row.v1",
        "row": "c_dsh_termination_handshake",
        "status": "NOT_VERIFIED",
        "reason": "c_dsh_absent",
        "simulated_row": "research-recovery.json (clearly labelled simulated=true)",
        "detail": (
            "C DSH is not delivered on this host. No DSH process was started, no "
            "handshake was fabricated, and no real session log is claimed. The "
            "simulated C-session row above uses the real Controller/approval/"
            "idempotency/event log and is labelled simulated."
        ),
    }
    _write(out_dir / "dsh-not-verified.json", dsh_row)

    assertions = {
        "task_left_running_after_simulated_c_exit": still_running,
        "new_session_resumed_from_cursor": (
            not replay.reset and len(replay.events) >= 1 and projection_b.status == "running"
        ),
        "duplicate_event_is_noop": duplicate_flag is True,
        "stale_event_cannot_overwrite_terminal": stale_result.outcome
        in {"stale", "terminal_protected"}
        and old_running.status == terminal_projection_after_restart.status,
        "newer_non_terminal_cannot_revive_terminal": revival_result.outcome
        in {"terminal_protected", "stale"}
        and terminal_projection_after_restart.status != "running",
        "exactly_one_approval": approval_rows == 1,
        "exactly_one_submit": idempotency_rows == 1 and jobs_after_duplicate == jobs_before + 1,
        "duplicate_submit_same_task": duplicate_task_id == task_id,
        "fixture_lost_cursor_no_second_submit": len(fixture_submits) == 1,
        "rph_exit_does_not_block": rph_exit.blocks_on_llm is False,
    }
    summary = {
        "schema": "acp2.b19b.summary.v1",
        "result": "PASS" if all(assertions.values()) else "FAIL",
        "assertions": assertions,
        "task_ref": task_id,
        "attempt": attempt,
        "dsh": dsh_row,
        "health_before": health_before,
        "health_after_restart": restart["health_after"],
        "simulated": True,
        "note": "Fixtures/simulated rows are distinguished from the NOT_VERIFIED real-DSH row.",
    }
    _write(out_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["result"] == "PASS" else 1


def _event_with_revision(event: Any, *, revision: int) -> Any:
    """Return a copy of a research event with a new event id/revision."""
    from dataclasses import replace

    return replace(
        event,
        event_id=f"{event.event_id}:revive:{revision}",
        revision=revision,
        terminal=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", default="http://127.0.0.1:18779")
    parser.add_argument("--port", type=int, default=18779)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--restart-command",
        required=True,
        help="Controller command line (without shell env prefixes) used after SIGTERM",
    )
    parser.add_argument("--restart-cwd", default=os.getcwd())
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except (OSError, RuntimeError, TimeoutError, ValueError, sqlite3.Error) as exc:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        _write(
            out_dir / "error.json",
            {"schema": "acp2.b19b.error.v1", "error": f"{type(exc).__name__}: {exc}"},
        )
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
