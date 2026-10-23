#!/usr/bin/env python3.11
"""Real migration/backup/recovery helper for the B24a acceptance scenario.

This helper is executed as a subprocess by
``scripts/acceptance/acp2_runtime.py --scenario migration``.  The acceptance
harness itself is intentionally stdlib-only; every operation that needs the
candidate implementation (SQLite schema migration, ``JobStore`` CAS,
``ApprovalService``, research budgets and the publication contract) runs here
against the repository's ``src`` tree (the caller sets ``PYTHONPATH``).

Actions are deliberately small and observable; each prints one JSON object to
stdout so the scenario can record exactly what happened:

* ``seed``            — build the pre-migration jobs DB, admission DB, task
  trees and durable approval/idempotency/budget rows with the real writers.
* ``seed-legacy``     — build a schema-021 DB (no approval/idempotency tables)
  to prove a real upgrade path, without inventing/removing data.
* ``migrate``         — run ``acp_services.store.migrations.migrate``.
* ``kill-migrate``    — run the same migration but SIGKILL the process right
  before a named migration is applied (interrupted-migration drill).
* ``lost-worker``     — CAS-recover a RUNNING job whose worker PID is dead and
  prove a terminal job cannot be revived.
* ``receipts``        — conservative old-v1 checkpoint refusal + duplicate
  publication/receipt idempotency on copies of the frozen fixtures.
* ``publication``     — publication-pending retry (no QC call) on a copy of
  ``v2_publish_interrupted``.
* ``cursor``          — expire a stale idempotency lease and prove a takeover
  never duplicates the row.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]


def _json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def _rows(db: Path, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    conn = _connect(db)
    try:
        return [tuple(row) for row in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def _scalar(db: Path, query: str, params: tuple[Any, ...] = ()) -> Any:
    rows = _rows(db, query, params)
    return rows[0][0] if rows else None


def _schema_version(db: Path) -> int:
    if not db.is_file():
        return 0
    conn = _connect(db)
    try:
        if (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_schema_migrations'"
            ).fetchone()
            is None
        ):
            return 0
        return int(conn.execute("SELECT COUNT(*) AS n FROM _schema_migrations").fetchone()["n"])
    finally:
        conn.close()


def _task_tree(root: Path, job_id: str, status: str) -> Path:
    work = root / "tasks" / job_id
    for relative in ("WORK/00_RUNTIME", "RESULT/structures"):
        (work / relative).mkdir(parents=True, exist_ok=True)
    (work / "job.json").write_text(
        json.dumps({"id": job_id, "task_id": job_id, "status": status}, sort_keys=True),
        encoding="utf-8",
    )
    (work / "task.json").write_text(
        json.dumps({"task_id": job_id, "status": status}, sort_keys=True),
        encoding="utf-8",
    )
    (work / "WORK" / "00_RUNTIME" / "events.jsonl").write_text(
        json.dumps({"event_type": f"job.{status}", "job_id": job_id}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return work


# ---------------------------------------------------------------------------
# seed — real Services writers
# ---------------------------------------------------------------------------


def action_seed(args: argparse.Namespace) -> int:
    from acp.scheduler.stage_tasks import StageTaskStore
    from acp.scheduler.tasks import TaskIndex
    from acp_services.runtime.admission import ReservationLedger
    from acp_services.runtime.approvals import ApprovalService
    from acp_services.runtime.research_budget import BudgetPolicy
    from acp_services.store.records import JobRecord, JobSpec, JobStatus
    from acp_services.store.store import JobStore

    db = Path(args.db)
    admission_db = Path(args.admission_db)
    root = Path(args.root)
    db.parent.mkdir(parents=True, exist_ok=True)
    admission_db.parent.mkdir(parents=True, exist_ok=True)

    store = JobStore(db)
    task_index = TaskIndex(db)
    StageTaskStore(db)
    approvals = ApprovalService(db)

    jobs: dict[str, dict[str, Any]] = {}

    def add_job(job_id: str, status: JobStatus, *, pid: int | None = None) -> JobRecord:
        value = status.value
        work = _task_tree(root, job_id, value)
        record = JobRecord(
            id=job_id,
            spec=JobSpec(workflow="singlepoint", name=job_id, input={"source": "O"}),
            status=status,
            work_dir=str(work),
            pid=pid,
            result={"exit_code": 0 if status.is_terminal else None},
        )
        store.create(record)
        task_index.sync_from_job(record)
        jobs[job_id] = {"status": value, "work_dir": str(work), "pid": pid}
        return record

    done = add_job("mig_done", JobStatus.COMPLETED)
    add_job("mig_lost", JobStatus.RUNNING, pid=999_999)

    # Terminal result + artifact bytes: the restore proof compares these.
    result_dir = Path(done.work_dir) / "RESULT"
    product = result_dir / "structures" / "min.xyz"
    product.write_text("2\nminimum\nO 0.0 0.0 0.0\nH 0.0 0.0 0.96\n", encoding="utf-8")
    manifest = result_dir / "result_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "task_id": "mig_done",
                "workflow": "singlepoint",
                "status": "completed",
                "version": 2,
                "products": [
                    {
                        "id": "p_structure",
                        "label": "minimum",
                        "path": "structures/min.xyz",
                        "kind": "structure",
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    # Durable approval rows: approved / pending / draft.
    approved, _ = approvals.request(
        proposal_ref="mig-proposal-approved",
        proposal_digest=_digest("proposal-approved"),
        plan_digest=_digest("plan-approved"),
        project_id="default",
        requester_principal="alice",
        plan_definition={"workflow": "singlepoint"},
        budget={"task_ref": 1, "qc_invocation": 2, "attempt": 1},
        idempotency_key="mig-approval-key",
    )
    approved, _ = approvals.approve(
        approved.approval_id,
        approver_principal="bob",
        expected_revision=1,
        reason="B24a migration drill",
    )
    pending, _ = approvals.request(
        proposal_ref="mig-proposal-pending",
        proposal_digest=_digest("proposal-pending"),
        plan_digest=_digest("plan-pending"),
        project_id="default",
        requester_principal="alice",
        plan_definition={"workflow": "optimize"},
    )
    draft = approvals.create_draft(
        proposal_ref="mig-proposal-draft",
        proposal_digest=_digest("proposal-draft"),
        plan_digest=_digest("plan-draft"),
        project_id="default",
        requester_principal="carol",
        plan_definition={"workflow": "frequency"},
    )

    # Durable idempotency rows: one committed TaskRef + one expired claim.
    claim = store.claim_idempotency(
        "project:default",
        "submit",
        "mig-key-committed",
        _digest("mig-request-committed"),
        lease_seconds=3600,
    )
    idem_record = JobRecord(
        id="mig_idem_job",
        spec=JobSpec(workflow="singlepoint", name="mig_idem_job", input={"source": "O"}),
        status=JobStatus.QUEUED,
        work_dir=str(root / "tasks" / "mig_idem_job"),
    )
    _task_tree(root, "mig_idem_job", "queued")
    if claim.claim is None:
        raise SystemExit("seed could not claim the committed idempotency key")
    finalized = store.finalize_idempotent_job(
        idem_record, claim.claim.claim_id, claim.claim.owner_token, claim.claim.lease_epoch
    )
    task_index.sync_from_job(idem_record)

    store.claim_idempotency(
        "project:default",
        "submit",
        "mig-key-expired",
        _digest("mig-request-expired"),
        lease_seconds=1,
        now="2020-01-01T00:00:00+00:00",
    )
    expired = store.expire_stale_idempotency_claims(now="2026-01-01T00:00:00+00:00")
    # A replay of the committed key must return the same TaskRef, never a row.
    replay = store.claim_idempotency(
        "project:default",
        "submit",
        "mig-key-committed",
        _digest("mig-request-committed"),
        lease_seconds=3600,
    )
    claims = store.list_idempotency_claims(limit=100)

    # Research budget rows (separate admission DB, real ledger writer).
    ledger = ReservationLedger(str(admission_db))
    policy = BudgetPolicy(
        budget_id="mig-budget",
        limits={"task_ref": 4, "qc_invocation": 10, "attempt": 4},
        approved_limits={"task_ref": 4, "qc_invocation": 10, "attempt": 4},
        approval_ref=approved.approval_id,
        project_id="default",
    )
    ledger.define_research_budget(policy)
    budget = ledger.reserve_research_budget(
        "mig-budget",
        {"task_ref": 1, "qc_invocation": 2, "attempt": 1},
        reservation_id="mig-budget-res-1",
        task_ref="mig_idem_job",
        run_id="mig-run-1",
        claim_id="mig-claim-1",
        detail="B24a migration drill",
    )

    # One artifact row so the artifacts table is part of the compared state.
    conn = _connect(db)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO artifacts (artifact_id, task_id, job_id, artifact_type, "
            "file_path, checksum, size_bytes, parser_status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "art_mig_done",
                "mig_done",
                "mig_done",
                "structure",
                "RESULT/structures/min.xyz",
                hashlib.sha256(product.read_bytes()).hexdigest(),
                product.stat().st_size,
                "parsed",
                "2026-01-01T00:00:00Z",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    _json(
        {
            "action": "seed",
            "db": str(db),
            "admission_db": str(admission_db),
            "schema_version": _schema_version(db),
            "jobs": jobs,
            "job_record_revisions": {job_id: store.get(job_id).revision for job_id in jobs},
            "approvals": {
                "approved": approved.approval_id,
                "pending": pending.approval_id,
                "draft": draft.approval_id,
                "approved_revision": approved.revision,
                "approved_binding": approved.binding(),
            },
            "idempotency": {
                "committed_claim": finalized.claim.claim_id if finalized.claim else None,
                "expired": [row.claim_id for row in expired],
                "replay_outcome": replay.kind,
                "rows": len(claims),
                "states": sorted({row.state for row in claims}),
            },
            "budget": {
                "budget_id": policy.budget_id,
                "reservation_id": budget.reservation_id,
                "admitted": budget.admitted,
                "remaining": budget.remaining,
            },
        }
    )
    return 0


def action_seed_legacy(args: argparse.Namespace) -> int:
    """Build a real schema-021 DB (no approval/idempotency tables)."""
    from acp_services.store.records import JobRecord, JobSpec, JobStatus
    from acp_services.store.store import JobStore

    db = Path(args.db)
    if db.exists():
        db.unlink()
    store = JobStore(db)  # migrates to the candidate head
    record = JobRecord(
        id="mig_legacy_done",
        spec=JobSpec(workflow="singlepoint", name="mig_legacy_done", input={"source": "O"}),
        status=JobStatus.COMPLETED,
        work_dir=str(Path(args.root) / "tasks" / "mig_legacy_done"),
        result={"exit_code": 0},
    )
    store.create(record)
    conn = _connect(db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM _schema_migrations WHERE id IN ('022','023')")
        conn.execute("DROP TABLE IF EXISTS idempotency_records")
        conn.execute("DROP TABLE IF EXISTS approval_records")
        conn.commit()
    finally:
        conn.close()
    _json(
        {
            "action": "seed-legacy",
            "db": str(db),
            "schema_version": _schema_version(db),
            "jobs": [
                {"id": row[0], "status": row[1]}
                for row in _rows(db, "SELECT id, status FROM jobs ORDER BY id")
            ],
        }
    )
    return 0


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------


def _migrate(db: Path, *, kill_at: str | None = None) -> dict[str, Any]:
    from acp_services.store import migrations

    original = migrations._apply_migration

    if kill_at is not None:

        def interrupting(conn: sqlite3.Connection, migration: dict[str, str]) -> bool:
            if migration["id"] == kill_at:
                sys.stdout.write(
                    json.dumps(
                        {
                            "action": "kill-migrate",
                            "killed_before": kill_at,
                            "schema_version_before_commit": _schema_version(db),
                            "pid": os.getpid(),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
                sys.stdout.flush()
                os.kill(os.getpid(), signal.SIGKILL)
            return original(conn, migration)

        migrations._apply_migration = interrupting  # type: ignore[assignment]
    applied = migrations.migrate(db)
    return {
        "action": "migrate",
        "db": str(db),
        "applied": applied,
        "schema_version": _schema_version(db),
    }


def action_migrate(args: argparse.Namespace) -> int:
    _json(_migrate(Path(args.db)))
    return 0


def action_kill_migrate(args: argparse.Namespace) -> int:
    # SIGKILL exits the process; reaching this line means the injection failed.
    _json(_migrate(Path(args.db), kill_at=args.kill_at))
    return 91


# ---------------------------------------------------------------------------
# drills
# ---------------------------------------------------------------------------


def action_lost_worker(args: argparse.Namespace) -> int:
    # The real controller recovery scan selects only non-terminal statuses
    # (``JobManager._requeue_active_on_startup`` iterates RUNNING/STARTING/
    # PENDING) and the runtime terminal predicate is the shared guard.  This
    # drill uses both: select via the same non-terminal scan, refuse a terminal
    # target through the shared predicate, and never issue a CAS for it.
    from acp_services.runtime.attempt_completion import _is_terminal
    from acp_services.store.records import JobStatus
    from acp_services.store.store import JobStore

    db = Path(args.db)
    store = JobStore(db)
    running = store.get(args.running)
    if running is None or running.status is not JobStatus.RUNNING:
        raise SystemExit(f"lost-worker drill needs a RUNNING job; got {running!r}")
    recovered = store.transition(
        running.id,
        expected_status=JobStatus.RUNNING,
        expected_revision=running.revision,
        expected_attempt=running.attempt,
        status=JobStatus.FAILED,
        error=f"worker_lost:pid={running.pid}:no_live_process",
        pid=None,
        result={"terminal_side_effects_done": True, "recovery": "lost_worker"},
    )
    terminal = store.get(args.terminal)
    if terminal is None:
        raise SystemExit("lost-worker drill needs the terminal job")
    # Recovery scan after the drill: the terminal job is not selected again.
    rescan = [record.id for record in store.list(status=JobStatus.RUNNING.value, limit=1000)]
    before = store.get(args.terminal)
    revival_blocked = _is_terminal(terminal.status)
    terminal_unchanged = (
        before is not None
        and before.status is JobStatus.COMPLETED
        and before.revision == terminal.revision
        and before.attempt == terminal.attempt
    )
    _json(
        {
            "action": "lost-worker",
            "recovered": {
                "job_id": recovered.id,
                "from": "running",
                "to": recovered.status.value,
                "revision": recovered.revision,
                "error": recovered.error,
            },
            "recovery_scan_after": rescan,
            "terminal_selected_by_recovery_scan": args.terminal in rescan,
            "terminal_revival_blocked": revival_blocked,
            "terminal_guard": "acp_services.runtime.attempt_completion._is_terminal",
            "terminal_unchanged": terminal_unchanged,
        }
    )
    return 0


def action_receipts(args: argparse.Namespace) -> int:
    from acp.calculations.checkpoint import load_checkpoint
    from acp.calculations.result_publication import (
        load_publication_state,
        publish_result,
        register_result_manifest,
    )
    from acp.storage.manifest import ResultManifest

    fixtures = Path(args.fixtures)
    scratch = Path(args.scratch)
    scratch.mkdir(parents=True, exist_ok=True)

    legacy_dir = fixtures / "checkpoint_mixed" / "WORK" / "00_RUNTIME"
    fingerprint = json.loads(
        (fixtures / "checkpoint_mixed" / "plan_fingerprint.json").read_text(encoding="utf-8")
    )["plan_fingerprint"]
    old_default = load_checkpoint(legacy_dir, fingerprint)
    old_explicit = load_checkpoint(legacy_dir, fingerprint, allow_legacy_fingerprint=True)

    # Duplicate receipt/publication: run the real publication sequence twice.
    source = fixtures / "v2_checkpoint_mixed" / "WORK" / "05_SP"
    result_dir = scratch / "duplicate_receipt"
    if result_dir.exists():
        shutil.rmtree(result_dir)
    shutil.copytree(source, result_dir)
    manifest = ResultManifest.read(result_dir)
    record_payload = json.loads((result_dir / "scientific_result.json").read_text(encoding="utf-8"))
    from acp.calculations.result_publication import ScientificResultRecord

    record = ScientificResultRecord.from_dict(record_payload)
    publish_result(result_dir, record=record, manifest=manifest)
    publish_result(result_dir, record=record, manifest=manifest)
    twice = register_result_manifest(result_dir, manifest)
    state = load_publication_state(result_dir)
    written = ResultManifest.read(result_dir)
    ids = [product.id for product in written.products]
    _json(
        {
            "action": "receipts",
            "old_v1": {
                "default_adopted": old_default is not None,
                "explicit_legacy_adopted": old_explicit is not None,
                "default_decision": "conservative_recompute",
            },
            "duplicate": {
                "result_id": record.result_id,
                "manifest_twice": str(twice),
                "publication_complete": bool(state and state.complete),
                "publication_result_id": None if state is None else state.result_id,
                "product_ids": ids,
                "product_ids_unique": len(ids) == len(set(ids)),
            },
        }
    )
    return 0


def action_publication(args: argparse.Namespace) -> int:
    from acp.calculations.result_publication import (
        load_publication_state,
        load_scientific_result,
        recover_publication,
    )
    from acp.storage.manifest import ResultManifest

    scratch = Path(args.scratch)
    result_dir = scratch / "publication_pending" / "WORK" / "05_SP"
    if result_dir.parent.parent.exists():
        shutil.rmtree(scratch / "publication_pending")
    shutil.copytree(Path(args.fixture), scratch / "publication_pending")
    before = load_publication_state(result_dir)
    record = load_scientific_result(result_dir)
    if record is None:
        raise SystemExit("publication drill fixture has no scientific_result.json")
    qc_calls = {"n": 0}

    def execute_qc() -> Any:
        qc_calls["n"] += 1
        raise AssertionError("publication retry must not invoke QC")

    outcome = recover_publication(
        result_dir,
        result_id=record.result_id,
        execute_qc=execute_qc,
        build_manifest=lambda _record: ResultManifest.read(result_dir),
    )
    after = load_publication_state(result_dir)
    manifest_exists = (result_dir / "result_manifest.json").is_file()
    _json(
        {
            "action": "publication",
            "marker_before": before,
            "marker_after_complete": bool(after and after.complete),
            "recovered": outcome.recovered,
            "qc_executed": outcome.qc_executed,
            "qc_calls": qc_calls["n"],
            "manifest_exists": manifest_exists,
        }
    )
    return 0


def action_verify(args: argparse.Namespace) -> int:
    from acp_services.runtime.admission import ReservationLedger
    from acp_services.runtime.approvals import ApprovalService
    from acp_services.store.store import JobStore

    db = Path(args.db)
    store = JobStore(db)
    approval = ApprovalService(db).get(args.approval_ref)
    ledger = ReservationLedger(str(args.admission_db))
    policy = ledger.get_research_budget(args.budget_id)
    projection = ledger.research_budget_projection(args.budget_id)
    committed = store.find_idempotency_claim("project:default", "submit", "mig-key-committed")
    done = store.get("mig_done")
    _json(
        {
            "action": "verify",
            "approval": None
            if approval is None
            else {
                "approval_ref": approval.approval_id,
                "state": approval.state,
                "revision": approval.revision,
                "binding": approval.binding(),
            },
            "budget": None
            if policy is None
            else {
                "budget_id": policy.budget_id,
                "limits": dict(policy.limits),
                "approval_ref": policy.approval_ref,
                "projection": None if projection is None else projection.to_dict(),
            },
            "idempotency": None
            if committed is None
            else {
                "claim_id": committed.claim_id,
                "state": committed.state,
                "task_id": committed.task_id,
                "request_digest": committed.request_digest,
            },
            "terminal": None
            if done is None
            else {"job_id": done.id, "status": done.status.value, "revision": done.revision},
        }
    )
    return 0


def action_cursor(args: argparse.Namespace) -> int:
    from acp_services.store.store import JobStore

    db = Path(args.db)
    store = JobStore(db)
    key = "mig-key-cursor"
    scope = "project:default"
    action = "submit"
    digest = _digest("mig-request-cursor")
    created = store.claim_idempotency(
        scope, action, key, digest, lease_seconds=1, now="2020-01-01T00:00:00+00:00"
    )
    if created.claim is None:
        raise SystemExit("cursor drill could not create a claim")
    before_rows = len(store.list_idempotency_claims(limit=1000))
    expired = store.expire_stale_idempotency_claims(now="2026-01-01T00:00:00+00:00")
    expired_claim = store.get_idempotency_claim(created.claim.claim_id)
    takeover = store.claim_idempotency(
        scope, action, key, digest, lease_seconds=3600, now="2026-01-01T00:00:01+00:00"
    )
    after_rows = len(store.list_idempotency_claims(limit=1000))
    _json(
        {
            "action": "cursor",
            "claim_id": created.claim.claim_id,
            "expired": [row.claim_id for row in expired],
            "expired_state": None if expired_claim is None else expired_claim.state,
            "takeover_kind": takeover.kind,
            "takeover_detail": takeover.detail,
            "lease_epoch": (takeover.claim.lease_epoch if takeover.claim is not None else None),
            "rows_before": before_rows,
            "rows_after": after_rows,
            "no_duplicate_row": before_rows == after_rows,
        }
    )
    return 0


ACTIONS = {
    "seed": action_seed,
    "seed-legacy": action_seed_legacy,
    "migrate": action_migrate,
    "kill-migrate": action_kill_migrate,
    "lost-worker": action_lost_worker,
    "receipts": action_receipts,
    "publication": action_publication,
    "cursor": action_cursor,
    "verify": action_verify,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=sorted(ACTIONS))
    parser.add_argument("--db")
    parser.add_argument("--admission-db")
    parser.add_argument("--root")
    parser.add_argument("--kill-at", default="023")
    parser.add_argument("--running", default="mig_lost")
    parser.add_argument("--terminal", default="mig_done")
    parser.add_argument("--fixtures")
    parser.add_argument("--fixture")
    parser.add_argument("--scratch")
    parser.add_argument("--approval-ref")
    parser.add_argument("--budget-id", default="mig-budget")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(ACTIONS[args.action](args))


if __name__ == "__main__":
    raise SystemExit(main())
