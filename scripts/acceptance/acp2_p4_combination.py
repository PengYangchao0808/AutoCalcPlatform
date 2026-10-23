#!/usr/bin/env python3.11
"""P4-affected four-combination probe (B24a).

The frozen F0/S0 four-combination harness proves the ordinary API/frontend
surface.  P4 (approval / budget / idempotency) adds durable rows that must
survive service start-up and every F/S pairing.  This probe seeds those rows
into each service run root **before** the service opens its databases, then
starts the real F0/S1 (or S0/S1) services with the same helpers used by
``four_combinations.py`` and asserts the rows are unchanged afterwards.

It is deliberately separate from ``four_combinations.py`` so the frozen
harness behaviour is not modified; the two artefact sets are read together by
the B24a acceptance evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import four_combinations as four  # noqa: E402

SCHEMA = "acp2.p4.combination.v1"

_APPROVAL_DDL = """
CREATE TABLE IF NOT EXISTS approval_records (
    approval_id            TEXT PRIMARY KEY,
    proposal_ref           TEXT NOT NULL,
    proposal_digest        TEXT NOT NULL,
    plan_digest            TEXT NOT NULL,
    plan_definition_json   TEXT NOT NULL DEFAULT '{}',
    project_id             TEXT NOT NULL,
    requester_principal    TEXT NOT NULL,
    approver_principal     TEXT,
    budget_json            TEXT,
    budget_digest          TEXT NOT NULL DEFAULT '',
    allowed_change_range_json TEXT NOT NULL DEFAULT '{}',
    state                  TEXT NOT NULL,
    expires_at             TEXT,
    revision               INTEGER NOT NULL DEFAULT 1,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    decided_at             TEXT,
    reason                 TEXT,
    task_ref_json          TEXT,
    idempotency_key        TEXT,
    last_event_revision    INTEGER NOT NULL DEFAULT 0
);
"""

_IDEMPOTENCY_DDL = """
CREATE TABLE IF NOT EXISTS idempotency_records (
    claim_id         TEXT PRIMARY KEY,
    scope_id         TEXT NOT NULL,
    action           TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL,
    request_digest   TEXT NOT NULL,
    state            TEXT NOT NULL,
    owner_token      TEXT,
    lease_epoch      INTEGER NOT NULL DEFAULT 0,
    lease_expires_at TEXT,
    task_id          TEXT,
    operation_id     TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE (scope_id, action, idempotency_key)
);
"""

_BUDGET_DDL = """
CREATE TABLE IF NOT EXISTS research_budget_policies (
    budget_id     TEXT PRIMARY KEY,
    approval_ref  TEXT NOT NULL DEFAULT '',
    project_id    TEXT NOT NULL DEFAULT '',
    policy_json   TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_budget_reservations (
    reservation_id           TEXT PRIMARY KEY,
    budget_id                TEXT NOT NULL,
    units_json               TEXT NOT NULL,
    state                    TEXT NOT NULL,
    task_ref                 TEXT NOT NULL DEFAULT '',
    run_id                   TEXT NOT NULL DEFAULT '',
    claim_id                 TEXT NOT NULL DEFAULT '',
    admission_reservation_id TEXT NOT NULL DEFAULT '',
    detail                   TEXT NOT NULL DEFAULT '',
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_budget_events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    subject    TEXT NOT NULL,
    event      TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admission_reservations (
    reservation_id       TEXT PRIMARY KEY,
    principal_id         TEXT NOT NULL,
    project_id           TEXT NOT NULL,
    key_id               TEXT,
    job_id               TEXT NOT NULL,
    state                TEXT NOT NULL,
    cores                INTEGER NOT NULL DEFAULT 0,
    memory_mb            INTEGER NOT NULL DEFAULT 0,
    storage_bytes        INTEGER NOT NULL DEFAULT 0,
    duration_s           INTEGER NOT NULL DEFAULT 0,
    queue                TEXT,
    node                 TEXT,
    policy_version       TEXT NOT NULL,
    actual_cores         INTEGER,
    actual_memory_mb     INTEGER,
    actual_storage_bytes INTEGER,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
"""

_P4_TABLES = {
    "jobs": ("approval_records", "idempotency_records"),
    "admission": (
        "research_budget_policies",
        "research_budget_reservations",
        "research_budget_events",
        "admission_reservations",
    ),
}


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _seed(run_root: Path) -> None:
    run_root.mkdir(parents=True, exist_ok=True)
    stamp = "2026-01-01T00:00:00Z"
    jobs = sqlite3.connect(str(run_root / "acp_jobs.db"))
    try:
        jobs.executescript(_APPROVAL_DDL)
        jobs.executescript(_IDEMPOTENCY_DDL)
        rows = [
            (
                "ap_p4_approved",
                "p4-proposal-approved",
                _digest("p4-proposal-approved"),
                _digest("p4-plan-approved"),
                "{}",
                "default",
                "alice",
                "bob",
                None,
                "",
                "{}",
                "approved",
                None,
                2,
                stamp,
                stamp,
                stamp,
                None,
                None,
                None,
                1,
            ),
            (
                "ap_p4_pending",
                "p4-proposal-pending",
                _digest("p4-proposal-pending"),
                _digest("p4-plan-pending"),
                "{}",
                "default",
                "alice",
                None,
                None,
                "",
                "{}",
                "pending",
                None,
                1,
                stamp,
                stamp,
                None,
                None,
                None,
                None,
                1,
            ),
        ]
        jobs.executemany(
            "INSERT OR REPLACE INTO approval_records (approval_id, proposal_ref, "
            "proposal_digest, plan_digest, plan_definition_json, project_id, "
            "requester_principal, approver_principal, budget_json, budget_digest, "
            "allowed_change_range_json, state, expires_at, revision, created_at, "
            "updated_at, decided_at, reason, task_ref_json, idempotency_key, "
            "last_event_revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        jobs.executemany(
            "INSERT OR REPLACE INTO idempotency_records (claim_id, scope_id, action, "
            "idempotency_key, request_digest, state, owner_token, lease_epoch, "
            "lease_expires_at, task_id, operation_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "idem_p4_committed",
                    "project:default",
                    "submit",
                    "p4-key-committed",
                    _digest("p4-request-committed"),
                    "committed",
                    "owner",
                    0,
                    None,
                    "p4_job_committed",
                    None,
                    stamp,
                    stamp,
                ),
                (
                    "idem_p4_expired",
                    "project:default",
                    "submit",
                    "p4-key-expired",
                    _digest("p4-request-expired"),
                    "expired",
                    "owner",
                    0,
                    stamp,
                    None,
                    None,
                    stamp,
                    stamp,
                ),
            ],
        )
        jobs.commit()
    finally:
        jobs.close()

    admission = sqlite3.connect(str(run_root / "acp_admission.db"))
    try:
        admission.executescript(_BUDGET_DDL)
        admission.execute(
            "INSERT OR REPLACE INTO research_budget_policies "
            "(budget_id, approval_ref, project_id, policy_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                "p4-budget",
                "ap_p4_approved",
                "default",
                json.dumps(
                    {"limits": {"task_ref": 2}, "approved_limits": {"task_ref": 2}, "timeouts": {}},
                    sort_keys=True,
                ),
                stamp,
                stamp,
            ),
        )
        admission.execute(
            "INSERT OR REPLACE INTO research_budget_reservations "
            "(reservation_id, budget_id, units_json, state, task_ref, run_id, claim_id, "
            "admission_reservation_id, detail, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "p4-budget-res",
                "p4-budget",
                json.dumps({"task_ref": 1}, sort_keys=True),
                "reserved",
                "p4_job_committed",
                "p4-run",
                "idem_p4_committed",
                "",
                "B24a P4 combination",
                stamp,
                stamp,
            ),
        )
        admission.execute(
            "INSERT INTO research_budget_events (subject, event, detail, created_at) "
            "VALUES (?,?,?,?)",
            ("p4-budget", "budget_defined", "", stamp),
        )
        admission.execute(
            "INSERT OR REPLACE INTO admission_reservations "
            "(reservation_id, principal_id, project_id, key_id, job_id, state, cores, "
            "memory_mb, storage_bytes, duration_s, queue, node, policy_version, "
            "actual_cores, actual_memory_mb, actual_storage_bytes, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "p4-admission-res",
                "alice",
                "default",
                None,
                "p4_job_committed",
                "reserved",
                1,
                1024,
                0,
                60,
                None,
                None,
                "p4",
                None,
                None,
                None,
                stamp,
                stamp,
            ),
        )
        admission.commit()
    finally:
        admission.close()


def _table_digest(db: Path, tables: tuple[str, ...]) -> dict[str, Any]:
    state: dict[str, Any] = {}
    if not db.is_file():
        return state
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        existing = {
            row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table in tables:
            if table not in existing:
                state[table] = {"count": 0, "digest": None}
                continue
            rows = conn.execute(f"SELECT * FROM [{table}] ORDER BY rowid").fetchall()
            material = json.dumps([list(row) for row in rows], sort_keys=True, default=str).encode(
                "utf-8"
            )
            state[table] = {
                "count": len(rows),
                "digest": hashlib.sha256(material).hexdigest(),
            }
    finally:
        conn.close()
    return state


def _state(run_root: Path) -> dict[str, Any]:
    return {
        "jobs_db": _table_digest(run_root / "acp_jobs.db", _P4_TABLES["jobs"]),
        "admission_db": _table_digest(run_root / "acp_admission.db", _P4_TABLES["admission"]),
    }


def run(
    *,
    f0: str,
    f1: str,
    out: Path,
    python: str,
    timeout: float,
    candidate_patch: Path | None = None,
) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="acp-p4-combo-"))
    worktrees: dict[str, Path] = {}
    services: list[subprocess.Popen[str]] = []
    ports = {"S0": four._free_port(), "S1": four._free_port()}
    cells: list[dict[str, Any]] = []
    try:
        for label, revision in (("F0", f0), ("F1", f1)):
            tree = root / label
            result = four._run(
                ["git", "worktree", "add", "--detach", str(tree), revision],
                cwd=four.REPO_ROOT,
                timeout=180,
            )
            if result.returncode != 0:
                raise four.HarnessError(f"cannot create {label} worktree: {result.stderr[-500:]}")
            worktrees[label] = tree
        if candidate_patch is not None:
            applied = four._run(
                ["git", "apply", str(candidate_patch.resolve())],
                cwd=worktrees["F1"],
                timeout=30,
            )
            if applied.returncode != 0:
                raise four.HarnessError(f"cannot apply candidate patch: {applied.stderr[-500:]}")

        before: dict[str, Any] = {}
        service_ok: dict[str, bool] = {}
        for service in ("S0", "S1"):
            tree = worktrees["F0" if service == "S0" else "F1"]
            run_root = root / f"run_{service}"
            _seed(run_root)
            before[service] = _state(run_root)
            proc = four._start_service(python, tree, ports[service], run_root)
            services.append(proc)
        for service in ("S0", "S1"):
            base = f"http://127.0.0.1:{ports[service]}"
            four._wait_http(base + "/api/status", timeout=timeout)
            probe = four._probe_service(base)
            service_ok[service] = bool(probe.get("ok"))
        deadline = time.time() + 5
        while time.time() < deadline:
            current = {service: _state(root / f"run_{service}") for service in ("S0", "S1")}
            if all(current[s] == before[s] for s in current):
                break
            time.sleep(0.2)
        for cell in four.CELL_NAMES:
            front, service = cell.split("/")
            after = _state(root / f"run_{service}")
            cells.append(
                {
                    "cell": cell,
                    "frontend_revision": f0 if front == "F0" else f1,
                    "service_revision": f0 if service == "S0" else f1,
                    "service_ok": service_ok[service],
                    "before": before[service],
                    "after": after,
                    "p4_rows_preserved": before[service] == after,
                }
            )
        document = {
            "schema": SCHEMA,
            "harness": "acp2_p4_combination.py",
            "f0_revision": f0,
            "f1_revision": f1,
            "cells": cells,
            "result": "PASS"
            if cells and all(row["service_ok"] and row["p4_rows_preserved"] for row in cells)
            else "FAIL",
            "note": (
                "approval/idempotency rows live in the jobs DB; research budget and "
                "admission rows live in acp_admission.db. Rows are seeded before the "
                "service opens either database and compared after start-up."
            ),
        }
        (out / "p4-combination.json").write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return document
    finally:
        for proc in services:
            four._stop(proc)
        for tree in worktrees.values():
            four._run(
                ["git", "worktree", "remove", "--force", str(tree)],
                cwd=four.REPO_ROOT,
                timeout=120,
            )
        import shutil

        shutil.rmtree(root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--f0-revision", default=four.F0_REVISION)
    parser.add_argument("--f1-revision", default="HEAD")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--candidate-patch", type=Path)
    args = parser.parse_args(argv)
    try:
        document = run(
            f0=args.f0_revision,
            f1=args.f1_revision,
            out=Path(args.out),
            python=args.python,
            timeout=args.timeout,
            candidate_patch=args.candidate_patch,
        )
    except (four.HarnessError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"P4 HARNESS ERROR: {exc}", file=sys.stderr)
        return 1
    return 0 if document["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
