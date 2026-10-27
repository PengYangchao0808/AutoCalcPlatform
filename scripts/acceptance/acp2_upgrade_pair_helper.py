#!/usr/bin/env python3.11
"""Real writers/readers for the M4-06a upgrade-pair drill.

The harness (``scripts/acceptance/acp2_upgrade_pair.py``) is stdlib-only and
runs this helper **inside one of the two installed release environments**
(previous artifact / candidate artifact, each a fresh venv), with
``PYTHONPATH`` removed.  Every operation therefore uses the code that is
actually installed on that side of the upgrade pair — no repository ``src``
import and no second implementation of the ledger/research/prepare writers.

Actions
-------
``report``    Installed versions, module locations, schema heads and surface
              facts (which optional M0/M2 features this build has).
``seed``      Build the pre-upgrade state with the *installed* writers: a
              committed submit claim under the old local scope, the frozen
              ``auto_`` derived-key row, a pending claim, an unattributable
              claim, one approval, one prepare-v1 operation, a Proposal and a
              ResearchRecord, a historical usage receipt row and a frozen
              capability descriptor snapshot.
``migrate``   Run the installed jobs-DB migration and report the head.
``interrupt`` Run the migration but SIGKILL the process right before a named
              migration is applied (interrupted-transaction drill).
``verify``    Read the upgraded state through the *candidate* installed code and
              assert the M0 frozen legacy semantics (replay only when provable,
              reconcile otherwise, never delete, new writes are v2).

Each action prints one JSON object to stdout (and optionally writes it to
``--out``).  Exit code ``0`` means the action completed; ``verify`` returns
``0`` only when every check passed.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import signal
import sqlite3
import sys
from pathlib import Path
from typing import Any

#: Project every legacy row is attributed to (the Controller's unset default).
PROJECT_ID = "uncategorized"


def _json(payload: Any, out: str | None = None) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text + "\n", encoding="utf-8")
    print(text)


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _module_path(module: Any) -> str:
    return str(getattr(module, "__file__", ""))


def _package_version(name: str) -> str | None:
    from importlib import metadata

    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:  # pragma: no cover - installed always
        return None


def _table_state(db: Path, tables: tuple[str, ...]) -> dict[str, Any]:
    """Row count + content digest per table (rowid ordered, all columns)."""
    if not db.is_file():
        return {"present": False, "counts": {}, "digests": {}}
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        existing = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        counts: dict[str, int] = {}
        digests: dict[str, str] = {}
        for table in tables:
            if table not in existing:
                continue
            rows = conn.execute(f"SELECT * FROM [{table}] ORDER BY rowid").fetchall()
            counts[table] = len(rows)
            material = json.dumps([dict(row) for row in rows], sort_keys=True, default=str).encode(
                "utf-8"
            )
            digests[table] = hashlib.sha256(material).hexdigest()
        return {"present": True, "counts": counts, "digests": digests}
    finally:
        conn.close()


def _row(db: Path, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(query, params).fetchone()
        return None if row is None else dict(row)
    finally:
        conn.close()


def _schema_version(db: Path) -> int:
    if not db.is_file():
        return 0
    conn = sqlite3.connect(str(db))
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_schema_migrations'"
        ).fetchone()
        if exists is None:
            return 0
        return int(conn.execute("SELECT COUNT(*) AS n FROM _schema_migrations").fetchone()[0])
    finally:
        conn.close()


def _prepare_schema_version(db: Path) -> int:
    if not db.is_file():
        return 0
    conn = sqlite3.connect(str(db))
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_prepare_schema_migrations'"
        ).fetchone()
        if exists is None:
            return 0
        row = conn.execute("SELECT MAX(id) AS v FROM _prepare_schema_migrations").fetchone()
        return 0 if row is None or row[0] is None else int(row[0])
    finally:
        conn.close()


def _admission_schema_version(db: Path) -> int:
    if not db.is_file():
        return 0
    conn = sqlite3.connect(str(db))
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='admission_schema_migrations'"
        ).fetchone()
        if exists is None:
            return 0
        row = conn.execute("SELECT MAX(id) AS v FROM admission_schema_migrations").fetchone()
        return 0 if row is None or row[0] is None else int(row[0])
    finally:
        conn.close()


def _indexes(db: Path, table: str) -> list[str]:
    conn = sqlite3.connect(str(db))
    try:
        return [
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=? ORDER BY name",
                (table,),
            )
        ]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def action_report(args: argparse.Namespace) -> int:
    import acp
    import acp_services
    import acp_services.store.migrations as migrations
    from acp_services.runtime import admission as admission_module

    report: dict[str, Any] = {
        "action": "report",
        "side": args.side,
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "versions": {
            "dist.cccp": _package_version("cccp"),
            "dist.acp-worker": _package_version("acp-worker"),
            "dist.acp-web": _package_version("acp-web"),
            "acp": getattr(acp, "__version__", None),
            "acp_services": _package_version("acp-worker"),
        },
        "modules": {
            "acp": _module_path(acp),
            "acp_services": _module_path(acp_services),
        },
        "jobs_migrations": [str(item["id"]) for item in migrations._MIGRATIONS],
        "jobs_schema_version": len(migrations._MIGRATIONS),
        "prepare_schema_version": int(migrations.PREPARE_SCHEMA_VERSION),
        "admission_schema_version": int(getattr(admission_module, "ADMISSION_SCHEMA_VERSION", 0)),
        "surfaces": {
            "science_plugins": _has_module("acp_services.runtime.science_plugins"),
            "verify_usage_history": hasattr(
                admission_module.ReservationLedger, "verify_usage_history"
            ),
            "mcp_derive": _has_module("acp_services.entrypoints.mcp"),
            "prepare_identity_version": getattr(
                _import_maybe("acp_services.data.operations"),
                "PREPARE_IDENTITY_VERSION",
                None,
            ),
            "request_digest_version": getattr(
                _import_maybe("acp_services.runtime.idempotency"),
                "REQUEST_DIGEST_VERSION",
                None,
            ),
            "identity_version": getattr(
                _import_maybe("acp_services.runtime.idempotency"),
                "IDENTITY_VERSION",
                None,
            ),
        },
        "state": {
            "jobs_schema_version_on_disk": _schema_version(Path(args.jobs_db))
            if args.jobs_db
            else 0,
            "prepare_schema_version_on_disk": _prepare_schema_version(Path(args.jobs_db))
            if args.jobs_db
            else 0,
            "admission_schema_version_on_disk": _admission_schema_version(Path(args.admission_db))
            if args.admission_db
            else 0,
        },
    }
    if args.jobs_db:
        idempotency_module = _import_maybe("acp_services.runtime.idempotency")
        report["digest_versions"] = {
            "current": str(getattr(idempotency_module, "REQUEST_DIGEST_VERSION", "1")),
            "legacy": str(getattr(idempotency_module, "LEGACY_REQUEST_DIGEST_VERSION", "1")),
        }
    _json(report, args.out)
    return 0


def _has_module(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


def _import_maybe(name: str) -> Any:
    import importlib

    try:
        return importlib.import_module(name)
    except ImportError:  # pragma: no cover - optional surface
        return None


# ---------------------------------------------------------------------------
# seed — real pre-upgrade writers
# ---------------------------------------------------------------------------

#: The three legacy idempotency rows this drill distinguishes.
COMMITTED_KEY = "legacy-submit-key"
COMMITTED_JOB = "legacy_commit_job"
AUTO_KEY_JOB = "legacy_auto_job"
PENDING_KEY = "legacy-pending-key"
UNSCOPED_SCOPE = "legacy-unscoped"
UNSCOPED_KEY = "legacy-unscoped-key"
PREPARE_JOB = "legacy_prepare_job"
PREPARE_CLIENT_KEY = "legacy-prepare-client-key"
USAGE_JOB = "legacy_usage_job"
USAGE_ID = "usage_legacy_0001"
RESEARCH_ID = "legacy-research-1"
RESEARCH_RESOURCE_ID = "rr_legacyrecord0001"
PROPOSAL_ID = "legacy-proposal-1"
PROPOSAL_RESOURCE_ID = "rr_legacyproposal001"
CAPABILITY_ID = "legacy.energy"
CAPABILITY_TASK = "legacy-capability-task"


def _submit_payload(name: str, source: str) -> dict[str, Any]:
    """A submit request in the frozen v1 field table (no v2-only fields)."""
    return {
        "schema_version": 1,
        "workflow": "singlepoint",
        "name": name,
        "input": {"source": source},
        "project_id": PROJECT_ID,
        "tags": [],
        "node_tags": [],
        "execution_mode": "local",
        "molecule_name": "",
        "task_name": "",
        "remark": "m4-06a legacy fixture",
    }


def _job(run_root: Path, job_id: str, *, status: str = "queued"):
    from acp_services.store.records import JobRecord, JobSpec, JobStatus

    work = run_root / "tasks" / job_id
    work.mkdir(parents=True, exist_ok=True)
    return JobRecord(
        id=job_id,
        spec=JobSpec(
            workflow="singlepoint",
            name=job_id,
            input={"source": "O"},
            project_id=PROJECT_ID,
        ),
        status=JobStatus(status),
        work_dir=str(work),
        project_id=PROJECT_ID,
        group_id=job_id,
    )


def _complete(store: Any, job_id: str) -> None:
    """Drive the freshly committed job to a terminal state (real CAS write)."""
    from acp_services.store.records import JobStatus

    record = store.get(job_id)
    if record is None:  # pragma: no cover - the commit above created it
        raise SystemExit(f"seed job {job_id!r} was not committed")
    if record.status is not JobStatus.COMPLETED:
        store.transition(
            job_id,
            expected_status=record.status,
            expected_revision=record.revision,
            expected_attempt=record.attempt,
            status=JobStatus.COMPLETED,
            result={"exit_code": 0, "legacy_fixture": True},
        )


def _write_capability_fixture(root: Path, contract: Path) -> None:
    """A minimal descriptor/binding pair the capability registry can discover."""
    capability = root / "energy"
    capability.mkdir(parents=True, exist_ok=True)
    contract.mkdir(parents=True, exist_ok=True)
    descriptor = {
        "schema": "acp2.science.capability",
        "schema_version": 1,
        "capability_id": CAPABILITY_ID,
        "implementation_id": "cccp.calculation.tasks.singlepoint",
        "compatibility": {
            "declared_major": 1,
            "unknown_major_version": "reject_and_never_execute",
        },
        "execution_semantics": {"kind": "compute"},
        "projections": {"described": {"state": "present"}},
        "limitations": [],
    }
    binding = {
        "schema": "acp2.science.task_binding",
        "schema_version": 1,
        "selection": {"capability_id": CAPABILITY_ID},
        "binding_output": {
            "binding_metadata": {
                "resolved_task_kind": "singlepoint",
                "implementation_id": "cccp.calculation.tasks.singlepoint",
                "delegation_target": "cccp.calculation.run_singlepoint",
                "request_schema_version": 1,
                "result_schema_version": 1,
            }
        },
        "validation_layers": {
            "json_structure": {"owner": "contract"},
            "cccp_scientific_semantics": {"owner": "cccp"},
            "runtime_platform_constraints": {"owner": "runtime"},
        },
    }
    (capability / "capability.json").write_text(
        json.dumps(descriptor, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (capability / "task-binding.json").write_text(
        json.dumps(binding, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    schema = {
        "$id": "https://acp.local/acp2/science/capability.schema.json",
        "schema_version": 1,
        "required": [
            "schema",
            "schema_version",
            "capability_id",
            "implementation_id",
            "compatibility",
            "execution_semantics",
            "projections",
        ],
        "properties": {
            "schema": {},
            "schema_version": {},
            "capability_id": {},
            "implementation_id": {},
            "compatibility": {},
            "execution_semantics": {},
            "projections": {},
            "limitations": {},
        },
    }
    view_model = {
        "$id": "https://acp.local/acp2/experience/view-model.schema.json",
        "schema": "acp2.experience.view-model.v1",
        "schema_version": "1.0.0",
        "required": ["snapshot_revision", "project_id", "read_only"],
        "properties": {
            "snapshot_revision": {},
            "project_id": {},
            "read_only": {},
        },
        "ui_dialect": {"version": "acp2.ui-subset.v1", "supported_keywords": ["type"]},
    }
    (contract / "capability.schema.json").write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (contract / "view-model.schema.json").write_text(
        json.dumps(view_model, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def action_seed(args: argparse.Namespace) -> int:
    from acp_services.contracts.access import LOCAL_PRINCIPAL_ID
    from acp_services.data.contracts import ResourceKind
    from acp_services.data.operations import PrepareOperationService, PrepareRequest
    from acp_services.data.research_resources import (
        PROPOSAL_SCHEMA,
        RESEARCH_RECORD_SCHEMA,
        ResearchResourceRequest,
        ResearchResourceStore,
    )
    from acp_services.entrypoints import mcp
    from acp_services.runtime.admission import ReservationLedger
    from acp_services.runtime.approvals import ApprovalService
    from acp_services.runtime.capability_registry import (
        CapabilityAuthorization,
        CapabilityRegistry,
    )
    from acp_services.runtime.idempotency import (
        canonical_request_digest,
        resolve_scope_id,
    )
    from acp_services.store.store import JobStore

    run_root = Path(args.run_root)
    jobs_db = Path(args.jobs_db)
    admission_db = Path(args.admission_db)
    run_root.mkdir(parents=True, exist_ok=True)

    store = JobStore(jobs_db)
    approvals = ApprovalService(jobs_db)
    scope = resolve_scope_id(LOCAL_PRINCIPAL_ID, PROJECT_ID)
    seed: dict[str, Any] = {
        "action": "seed",
        "side": args.side,
        "run_root": str(run_root),
        "jobs_db": str(jobs_db),
        "admission_db": str(admission_db),
        "project_id": PROJECT_ID,
        "scope_id": scope,
        "jobs": {},
        "claims": {},
        "prepare": {},
        "research": {},
        "usage": {},
        "approval": {},
        "capability": {},
    }

    # -- 1. committed legacy submit claim + its terminal task ----------------
    committed_payload = _submit_payload(COMMITTED_JOB, "O")
    committed_digest = canonical_request_digest(committed_payload)
    committed_job = _job(run_root, COMMITTED_JOB)
    claim = store.claim_idempotency(
        scope, "submit", COMMITTED_KEY, committed_digest, lease_seconds=3600
    )
    if claim.claim is None:
        raise SystemExit("seed could not claim the committed submit key")
    finalized = store.finalize_idempotent_job(
        committed_job,
        claim.claim.claim_id,
        claim.claim.owner_token,
        claim.claim.lease_epoch,
    )
    _complete(store, committed_job.id)
    seed["claims"]["committed"] = {
        "claim_id": claim.claim.claim_id,
        "scope_id": scope,
        "action": "submit",
        "key": COMMITTED_KEY,
        "request_digest": committed_digest,
        "payload": committed_payload,
        "task_id": committed_job.id,
        "state": "committed",
        "replay_outcome": finalized.kind,
    }
    seed["jobs"][COMMITTED_JOB] = "completed"

    # -- 2. frozen pre-M0-05 MCP `auto_` derived-key row ---------------------
    auto_payload = _submit_payload(AUTO_KEY_JOB, "N")
    auto_key = mcp.derive_idempotency_key(auto_payload)
    auto_digest = canonical_request_digest(auto_payload)
    auto_job = _job(run_root, AUTO_KEY_JOB)
    auto_claim = store.claim_idempotency(scope, "submit", auto_key, auto_digest, lease_seconds=3600)
    if auto_claim.claim is None:
        raise SystemExit("seed could not claim the frozen auto_ key")
    store.finalize_idempotent_job(
        auto_job,
        auto_claim.claim.claim_id,
        auto_claim.claim.owner_token,
        auto_claim.claim.lease_epoch,
    )
    _complete(store, auto_job.id)
    seed["claims"]["auto"] = {
        "claim_id": auto_claim.claim.claim_id,
        "scope_id": scope,
        "action": "submit",
        "key": auto_key,
        "request_digest": auto_digest,
        "payload": auto_payload,
        "task_id": auto_job.id,
        "state": "committed",
        "origin": "frozen_pre_m0_05_mcp_derivation",
    }
    seed["jobs"][AUTO_KEY_JOB] = "completed"

    # -- 3. pending (expired lease) legacy claim -----------------------------
    pending_digest = canonical_request_digest(_submit_payload("legacy_pending_job", "C"))
    pending = store.claim_idempotency(
        scope,
        "submit",
        PENDING_KEY,
        pending_digest,
        lease_seconds=1,
        now="2020-01-01T00:00:00+00:00",
    )
    if pending.claim is None:
        raise SystemExit("seed could not claim the pending key")
    seed["claims"]["pending"] = {
        "claim_id": pending.claim.claim_id,
        "scope_id": scope,
        "action": "submit",
        "key": PENDING_KEY,
        "request_digest": pending_digest,
        "state": "claimed",
        "lease_expires_at": "2020-01-01T00:00:01+00:00",
    }

    # -- 4. unattributable legacy claim (no principal/project scope) --------
    unscoped_digest = canonical_request_digest(_submit_payload("legacy_unscoped_job", "F"))
    unscoped = store.claim_idempotency(
        UNSCOPED_SCOPE, "submit", UNSCOPED_KEY, unscoped_digest, lease_seconds=3600
    )
    if unscoped.claim is None:
        raise SystemExit("seed could not claim the unscoped key")
    seed["claims"]["unscoped"] = {
        "claim_id": unscoped.claim.claim_id,
        "scope_id": UNSCOPED_SCOPE,
        "action": "submit",
        "key": UNSCOPED_KEY,
        "request_digest": unscoped_digest,
        "state": "claimed",
    }

    # -- 5. approval (pre-M1-04a shape: execution plan only) ----------------
    approval, _ = approvals.request(
        proposal_ref="legacy-proposal-approved",
        proposal_digest=_digest("legacy-proposal-body"),
        plan_digest=_digest("legacy-plan-body"),
        project_id=PROJECT_ID,
        requester_principal="legacy-alice",
        plan_definition={"workflow": "singlepoint"},
        budget={"task_ref": 2, "qc_invocation": 2, "attempt": 1},
        idempotency_key="legacy-approval-key",
    )
    approval, _ = approvals.approve(
        approval.approval_id,
        approver_principal="legacy-bob",
        expected_revision=1,
        reason="m4-06a pre-upgrade approval",
    )
    seed["approval"] = {
        "approval_id": approval.approval_id,
        "state": approval.state,
        "revision": approval.revision,
        "proposal_ref": approval.proposal_ref,
        "plan_digest": approval.plan_digest,
    }

    # -- 6. prepare v1 operation (no verified attribution) ------------------
    prepare_service = PrepareOperationService(jobs_db)
    prepare_kwargs: dict[str, Any] = {
        "job_id": PREPARE_JOB,
        "attempt": 1,
        "resource_id": "structure_input",
        "resource_kind": ResourceKind.INPUT,
        "resource_digest": _digest("legacy-prepare-resource"),
        "permission_scope": "local-single",
        "resource_key": "legacy\x1fprepare\x1fstructure",
        "client_idempotency_key": PREPARE_CLIENT_KEY,
        "payload": {"source": "O", "schema_version": 1},
    }
    fields = {field.name for field in dataclasses.fields(PrepareRequest)}
    prepare_request = PrepareRequest(
        **{key: value for key, value in prepare_kwargs.items() if key in fields}
    )
    prepare_record = prepare_service.enqueue(prepare_request)
    seed["prepare"] = {
        "operation_id": prepare_record.operation_id,
        "idempotency_key": prepare_record.idempotency_key,
        "resource_key": prepare_record.resource_key,
        "client_idempotency_key": PREPARE_CLIENT_KEY,
        "permission_scope": "local-single",
        "state": prepare_record.state.value,
        "request_kwargs": {
            key: value
            for key, value in prepare_kwargs.items()
            if key in fields and key not in {"resource_kind", "payload"}
        },
        "payload": prepare_kwargs["payload"],
        "resource_kind": ResourceKind.INPUT.value,
    }

    # -- 7. Proposal + ResearchRecord (Data authority, old revision) --------
    research = ResearchResourceStore(run_root / "acp_research.db")
    try:
        proposal = research.create(
            ResearchResourceRequest(
                resource_id="",
                schema=PROPOSAL_SCHEMA,
                document={
                    "schema": PROPOSAL_SCHEMA,
                    "project_id": PROJECT_ID,
                    "proposal_id": PROPOSAL_ID,
                    "title": "legacy proposal",
                    "created_at": "2026-10-01T00:00:00Z",
                },
                principal_id=LOCAL_PRINCIPAL_ID,
                project_id=PROJECT_ID,
            )
        )
        record = research.create(
            ResearchResourceRequest(
                resource_id="",
                schema=RESEARCH_RECORD_SCHEMA,
                document={
                    "schema": RESEARCH_RECORD_SCHEMA,
                    "project_id": PROJECT_ID,
                    "research_id": RESEARCH_ID,
                    "round": 1,
                    "decision": "continue",
                    "plan_definition_digest": _digest("legacy-plan-body"),
                    "task_refs": [],
                    "evidence_refs": [],
                    "created_at": "2026-10-01T00:00:00Z",
                },
                principal_id=LOCAL_PRINCIPAL_ID,
                project_id=PROJECT_ID,
            )
        )
    finally:
        research.close()
    seed["research"] = {
        "record_resource_id": record.resource_id,
        "record_revision": record.revision,
        "record_digest": record.digest,
        "research_id": RESEARCH_ID,
        "proposal_resource_id": proposal.resource_id,
        "proposal_revision": proposal.revision,
        "proposal_digest": proposal.digest,
        "proposal_id": PROPOSAL_ID,
    }

    # -- 8. historical usage receipt row ------------------------------------
    usage_ledger = ReservationLedger(str(admission_db))
    usage_written = usage_ledger.record_usage_once(
        USAGE_ID,
        reservation_id="res_legacy_0001",
        job_id=USAGE_JOB,
        attempt=1,
        cores=2,
        seconds=60,
    )
    seed["usage"] = {
        "usage_id": USAGE_ID,
        "job_id": USAGE_JOB,
        "attempt": 1,
        "cores": 2,
        "seconds": 60,
        "written": bool(usage_written),
        "totals": usage_ledger.usage_totals(),
    }

    # -- 9. frozen capability descriptor snapshot (read-only history) ------
    capability_root = run_root / "capabilities"
    contract_root = run_root / "capability-contract"
    state_root = run_root / "capability-state"
    _write_capability_fixture(capability_root, contract_root)
    registry = CapabilityRegistry(
        capability_root=capability_root,
        state_root=state_root,
        schema_path=contract_root / "capability.schema.json",
        view_model_path=contract_root / "view-model.schema.json",
        trusted_capabilities=[CAPABILITY_ID],
    )
    discovery = registry.discover()
    item = discovery.capabilities[0]
    registration = registry.register(
        CAPABILITY_ID,
        authorization=CapabilityAuthorization(
            action="capability.register",
            capability_id=CAPABILITY_ID,
            version="1.0.0",
            descriptor_digest=item.descriptor_digest,
            authorized_by="controller",
            issued_at="2026-10-01T00:00:00Z",
        ),
        license_state="approved",
        license_source="test://MIT",
    )
    freeze = registry.admit_for_task(
        CAPABILITY_TASK,
        1,
        CAPABILITY_ID,
        authorization=CapabilityAuthorization(
            action="capability.admit",
            capability_id=CAPABILITY_ID,
            version="1.0.0",
            descriptor_digest=registration.descriptor_digest,
            authorized_by="controller",
            issued_at="2026-10-01T00:00:00Z",
        ),
        purpose="read_only",
    )
    snapshot_dir = state_root / "tasks" / "legacy-capability-task" / "attempt-1"
    snapshot_files = (
        {
            path.name: _sha256_file(path)
            for path in sorted(snapshot_dir.rglob("*"))
            if path.is_file()
        }
        if snapshot_dir.is_dir()
        else {}
    )
    seed["capability"] = {
        "capability_id": CAPABILITY_ID,
        "version": "1.0.0",
        "descriptor_digest": registration.descriptor_digest,
        "task_id": CAPABILITY_TASK,
        "attempt": 1,
        "freeze_at": getattr(freeze, "frozen_at", None),
        "state_root": str(state_root),
        "snapshot_dir": str(snapshot_dir),
        "snapshot_files": snapshot_files,
        "root": str(capability_root),
        "contract": str(contract_root),
    }

    seed["state"] = {
        "jobs_schema_version": _schema_version(jobs_db),
        "prepare_schema_version": _prepare_schema_version(jobs_db),
        "admission_schema_version": _admission_schema_version(admission_db),
        "jobs_migrations": [
            str(row[0])
            for row in sqlite3.connect(str(jobs_db))
            .execute("SELECT id FROM _schema_migrations ORDER BY id")
            .fetchall()
        ]
        if jobs_db.is_file()
        else [],
    }
    _json(seed, args.out)
    return 0


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------


def action_migrate(args: argparse.Namespace) -> int:
    from acp_services.store import migrations

    jobs_db = Path(args.jobs_db)
    applied = migrations.migrate(jobs_db)
    prepare_ok = True
    payload: dict[str, Any] = {
        "action": "migrate",
        "jobs_db": str(jobs_db),
        "applied": applied,
        "jobs_schema_version": _schema_version(jobs_db),
        "prepare_schema_version": _prepare_schema_version(jobs_db),
        "admission_schema_version": _admission_schema_version(Path(args.admission_db))
        if args.admission_db
        else 0,
    }
    if args.admission_db:
        from acp_services.runtime.admission import ReservationLedger

        ReservationLedger(str(args.admission_db))
        payload["admission_schema_version"] = _admission_schema_version(Path(args.admission_db))
        prepare_ok = True
    payload["prepare_ok"] = prepare_ok
    _json(payload, args.out)
    return 0


def action_interrupt(args: argparse.Namespace) -> int:
    """Apply migrations but SIGKILL before ``--kill-at`` (real interruption)."""
    from acp_services.store import migrations

    jobs_db = Path(args.jobs_db)
    original = migrations._apply_migration

    def interrupting(conn: sqlite3.Connection, migration: dict[str, str]) -> bool:
        if migration["id"] == args.kill_at:
            sys.stdout.write(
                json.dumps(
                    {
                        "action": "interrupt",
                        "killed_before": args.kill_at,
                        "schema_version_before_commit": _schema_version(jobs_db),
                        "jobs_before": _row(jobs_db, "SELECT COUNT(*) AS n FROM jobs"),
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
    migrations.migrate(jobs_db)
    # Reaching this line means the injection did not fire.
    _json({"action": "interrupt", "injected": False})
    return 91


# ---------------------------------------------------------------------------
# verify — candidate reads of the upgraded state
# ---------------------------------------------------------------------------


def _checks_to_document(checks: list[dict[str, Any]]) -> dict[str, Any]:
    failing = [check["name"] for check in checks if check["status"] != "PASS"]
    return {
        "total": len(checks),
        "passed": len(checks) - len(failing),
        "failed": len(failing),
        "failing": failing,
        "status": "PASS" if not failing else "FAIL",
    }


def action_verify(args: argparse.Namespace) -> int:
    seed = json.loads(Path(args.seed).read_text(encoding="utf-8"))
    run_root = Path(args.run_root)
    jobs_db = Path(args.jobs_db)
    admission_db = Path(args.admission_db)
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any) -> None:
        checks.append(
            {
                "name": name,
                "status": "PASS" if bool(ok) else "FAIL",
                "detail": detail
                if isinstance(detail, (str, int, float, bool))
                else json.dumps(detail, sort_keys=True, default=str),
            }
        )

    from acp_services.contracts.access import LOCAL_PRINCIPAL_ID
    from acp_services.data.operations import (
        PrepareOperationConflictError,
        PrepareOperationService,
        PrepareRequest,
    )
    from acp_services.entrypoints import mcp
    from acp_services.runtime.admission import ReservationLedger
    from acp_services.runtime.capability_registry import CapabilityRegistry
    from acp_services.runtime.idempotency import (
        MIGRATION_STATE_NATIVE,
        MIGRATION_STATE_PENDING,
        MIGRATION_STATE_UNVERIFIED,
        MIGRATION_STATE_VERIFIED,
        SCOPE_ORIGIN_LEGACY_LOCAL,
        SCOPE_ORIGIN_LEGACY_UNKNOWN,
        SCOPE_ORIGIN_VERIFIED,
        canonical_request_digest,
        resolve_scope_id,
        verify_request_digest,
    )
    from acp_services.store.store import JobStore

    store = JobStore(jobs_db)
    claims = seed["claims"]

    # Snapshot the legacy rows BEFORE any read exercise, so "read is not a
    # rewrite" is proven byte-for-byte at the end.
    def _job_rows() -> dict[str, str]:
        conn = sqlite3.connect(str(jobs_db))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE id IN (?, ?, ?) ORDER BY id",
                (COMMITTED_JOB, AUTO_KEY_JOB, PREPARE_JOB),
            ).fetchall()
        finally:
            conn.close()
        return {
            str(row["id"]): hashlib.sha256(
                json.dumps(dict(row), sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            for row in rows
        }

    legacy_job_rows = _job_rows()

    before = {
        "idempotency": _table_state(jobs_db, ("idempotency_records",)),
        "jobs": _table_state(jobs_db, ("jobs",)),
        "approvals": _table_state(jobs_db, ("approval_records",)),
        "prepare": _table_state(jobs_db, ("data_operations",)),
        "research": _table_state(run_root / "acp_research.db", ("research_resources",)),
        "usage": _table_state(admission_db, ("admission_usage_receipts",)),
        "research_budget": _table_state(admission_db, ("research_budget_receipts",)),
    }

    conn = sqlite3.connect(str(jobs_db))
    try:
        legacy_auto_keys = [
            row[0]
            for row in conn.execute(
                "SELECT idempotency_key FROM idempotency_records "
                "WHERE idempotency_key LIKE 'auto_%' ORDER BY idempotency_key"
            ).fetchall()
        ]
        legacy_op_keys = [
            row[0]
            for row in conn.execute(
                "SELECT idempotency_key FROM idempotency_records "
                "WHERE idempotency_key LIKE 'op_%' ORDER BY idempotency_key"
            ).fetchall()
        ]
    finally:
        conn.close()

    # -- 1. every legacy row is still present, with its exact key/digest ----
    present: dict[str, Any] = {}
    for label, expected in claims.items():
        row = _row(
            jobs_db,
            "SELECT * FROM idempotency_records WHERE claim_id=?",
            (expected["claim_id"],),
        )
        present[label] = row
    check(
        "legacy.claims_preserved",
        all(present[label] is not None for label in claims)
        and all(
            present[label]["idempotency_key"] == claims[label]["key"]
            and present[label]["request_digest"] == claims[label]["request_digest"]
            for label in claims
            if present[label] is not None
        ),
        {
            label: None if row is None else {"key": row["idempotency_key"]}
            for label, row in present.items()
        },
    )

    # -- 2. migration 024 classified the rows (no silent adoption) ----------
    classification = {
        label: None
        if row is None
        else {
            "identity_version": row["identity_version"],
            "request_digest_version": row["request_digest_version"],
            "scope_origin": row["scope_origin"],
            "migration_state": row["migration_state"],
            "principal_id": row["principal_id"],
            "project_id": row["project_id"],
        }
        for label, row in present.items()
    }
    committed_row = classification["committed"] or {}
    auto_row = classification["auto"] or {}
    pending_row = classification["pending"] or {}
    unscoped_row = classification["unscoped"] or {}
    check(
        "legacy.classified_committed_verified",
        committed_row.get("identity_version") == "1"
        and committed_row.get("request_digest_version") == "1"
        and committed_row.get("scope_origin") == SCOPE_ORIGIN_LEGACY_LOCAL
        and committed_row.get("migration_state") == MIGRATION_STATE_VERIFIED
        and committed_row.get("principal_id") == LOCAL_PRINCIPAL_ID,
        committed_row,
    )
    check(
        "legacy.classified_auto_verified",
        auto_row.get("identity_version") == "1"
        and auto_row.get("migration_state") == MIGRATION_STATE_VERIFIED,
        auto_row,
    )
    check(
        "legacy.classified_pending_not_replayable",
        pending_row.get("identity_version") == "1"
        and pending_row.get("migration_state") == MIGRATION_STATE_PENDING,
        pending_row,
    )
    check(
        "legacy.classified_unattributable_unverified",
        unscoped_row.get("identity_version") == "1"
        and unscoped_row.get("scope_origin") == SCOPE_ORIGIN_LEGACY_UNKNOWN
        and unscoped_row.get("migration_state") == MIGRATION_STATE_UNVERIFIED,
        unscoped_row,
    )

    # -- 3. replay only for a provable (scope + digest) retry ---------------
    committed = claims["committed"]
    replay = store.claim_idempotency(
        committed["scope_id"],
        "submit",
        committed["key"],
        committed["request_digest"],
        request_payload=committed["payload"],
    )
    replay_job = store.get(committed["task_id"])
    check(
        "legacy.replay_verified_row_returns_original_task",
        replay.kind == "replay"
        and (replay.task_ref or {}).get("task_id") == committed["task_id"]
        and replay_job is not None,
        {"kind": replay.kind, "task_ref": dict(replay.task_ref or {})},
    )

    other_scope = resolve_scope_id("principal_legacy_other", seed["project_id"])
    cross_scope = store.claim_idempotency(
        other_scope, "submit", committed["key"], committed["request_digest"]
    )
    check(
        "legacy.other_scope_is_conflict_not_replay",
        cross_scope.kind == "conflict",
        {"kind": cross_scope.kind, "detail": cross_scope.detail},
    )

    unfit = store.claim_idempotency(
        seed["claims"]["unscoped"]["scope_id"],
        "submit",
        seed["claims"]["unscoped"]["key"],
        seed["claims"]["unscoped"]["request_digest"],
    )
    check(
        "legacy.unattributable_row_is_conflict_not_adopted",
        unfit.kind == "conflict" and "cannot be attributed" in (unfit.detail or ""),
        {"kind": unfit.kind, "detail": unfit.detail},
    )

    # -- 4. request digest v1 is verified under its own frozen field table --
    digest_verified = verify_request_digest(committed["request_digest"], "1", committed["payload"])
    tampered = dict(committed["payload"])
    tampered["name"] = "legacy_commit_job_tampered"
    digest_tampered = verify_request_digest(committed["request_digest"], "1", tampered)
    tampered_claim = store.claim_idempotency(
        committed["scope_id"],
        "submit",
        committed["key"],
        canonical_request_digest(tampered),
        request_payload=tampered,
    )
    check(
        "legacy.request_digest_v1_verifies_and_rejects_tamper",
        digest_verified
        and not digest_tampered
        and canonical_request_digest(committed["payload"]) == committed["request_digest"]
        and tampered_claim.kind == "conflict",
        {
            "verified": digest_verified,
            "tampered_verified": digest_tampered,
            "tampered_claim": tampered_claim.kind,
        },
    )

    # -- 5. the frozen auto_ derivation still finds the old row -------------
    auto_claim = claims["auto"]
    recomputed = mcp.derive_idempotency_key(auto_claim["payload"])
    found = store.find_idempotency_claim(auto_claim["scope_id"], "submit", auto_claim["key"])
    check(
        "legacy.auto_derivation_is_byte_frozen",
        recomputed == auto_claim["key"]
        and recomputed.startswith(mcp.LEGACY_DERIVED_KEY_PREFIX)
        and found is not None
        and found.task_id == auto_claim["task_id"]
        and found.migration_state == MIGRATION_STATE_VERIFIED,
        {
            "recomputed": recomputed,
            "stored": auto_claim["key"],
            "task_id": None if found is None else found.task_id,
        },
    )

    # -- 6. prepare v1 row: legacy fallback, local-only merge, no adoption --
    prepare_service = PrepareOperationService(jobs_db)
    seed_prepare = seed["prepare"]
    legacy_prepare = prepare_service.get(seed_prepare["operation_id"])
    prepare_kwargs = dict(seed_prepare["request_kwargs"])
    prepare_kwargs["resource_kind"] = seed_prepare["resource_kind"]
    prepare_kwargs["payload"] = seed_prepare["payload"]

    def _request(**extra: Any) -> Any:
        from acp_services.data.contracts import ResourceKind

        values = dict(prepare_kwargs)
        values["resource_kind"] = ResourceKind(values["resource_kind"])
        values.update(extra)
        fields = {field.name for field in dataclasses.fields(PrepareRequest)}
        return PrepareRequest(**{key: value for key, value in values.items() if key in fields})

    local_merge = prepare_service.enqueue(
        _request(principal_id=LOCAL_PRINCIPAL_ID, project_id=seed["project_id"])
    )
    authenticated_refused = False
    authenticated_detail = ""
    try:
        prepare_service.enqueue(
            _request(principal_id="principal_legacy_other", project_id=seed["project_id"])
        )
    except PrepareOperationConflictError as exc:
        authenticated_refused = True
        authenticated_detail = str(exc)
    check(
        "prepare.legacy_v1_row_merge_is_local_only",
        legacy_prepare is not None
        and legacy_prepare.is_legacy_key is True
        and str(getattr(legacy_prepare, "identity_version", "1")) == "1"
        and local_merge.operation_id == seed_prepare["operation_id"]
        and authenticated_refused
        and "cannot be attributed" in authenticated_detail,
        {
            "legacy_key": None if legacy_prepare is None else legacy_prepare.idempotency_key,
            "local_merge_operation": local_merge.operation_id,
            "authenticated_refused": authenticated_refused,
            "authenticated_detail": authenticated_detail,
        },
    )
    new_prepare = prepare_service.enqueue(
        _request(
            job_id="candidate_prepare_job",
            resource_id="candidate_structure_input",
            resource_key="candidate\x1fprepare\x1fstructure",
            client_idempotency_key="candidate-prepare-client-key",
            principal_id=LOCAL_PRINCIPAL_ID,
            project_id=seed["project_id"],
        )
    )
    new_prepare_row = _row(
        jobs_db,
        "SELECT * FROM data_operations WHERE operation_id=?",
        (new_prepare.operation_id,),
    )
    check(
        "prepare.new_row_is_v2",
        new_prepare_row is not None
        and new_prepare_row["identity_version"] == "2"
        and str(new_prepare_row["idempotency_key"]).startswith("prep_")
        and new_prepare_row["principal_id"] == LOCAL_PRINCIPAL_ID,
        None
        if new_prepare_row is None
        else {
            "identity_version": new_prepare_row["identity_version"],
            "key": new_prepare_row["idempotency_key"],
        },
    )
    indexes = _indexes(jobs_db, "data_operations")
    check(
        "prepare.v1_unique_index_survives_v2_upgrade",
        "idx_data_operations_client_key" in indexes
        and "idx_data_operations_identity_client_key" in indexes
        and _prepare_schema_version(jobs_db) == 2,
        {"indexes": indexes, "prepare_schema_version": _prepare_schema_version(jobs_db)},
    )

    # -- 7. historical usage is preserved and never re-labelled trusted -----
    ledger = ReservationLedger(str(admission_db))
    usage_rows = {row["usage_id"]: row for row in ledger.usage_receipts()}
    historical = usage_rows.get(seed["usage"]["usage_id"])
    totals = ledger.usage_totals()
    history = (
        ledger.verify_usage_history(seed["usage"]["job_id"])
        if hasattr(ledger, "verify_usage_history")
        else None
    )
    check(
        "usage.historical_rows_preserved_not_fabricated",
        historical is not None
        and int(historical["cores"]) == seed["usage"]["cores"]
        and int(historical["seconds"]) == seed["usage"]["seconds"]
        and str(historical.get("trusted_source") or "") == ""
        and str(historical.get("chain_digest") or "") == ""
        and int(totals["receipts"]) >= 1
        and int(totals["core_seconds"]) >= seed["usage"]["cores"] * seed["usage"]["seconds"]
        and history is not None
        and history["verified"] is False
        and any(problem["code"] == "binding_missing" for problem in history["problems"]),
        {
            "receipt": historical,
            "totals": totals,
            "history_verified": None if history is None else history["verified"],
            "problems": None if history is None else history["problems"],
        },
    )

    # -- 8. ResearchRecord / Proposal stay readable, revision unchanged -----
    from acp_services.data.research_resources import ResearchResourceStore

    research = ResearchResourceStore(run_root / "acp_research.db")
    try:
        record = research.read(
            seed["research"]["record_resource_id"],
            principal_id=LOCAL_PRINCIPAL_ID,
            project_id=seed["project_id"],
        )
        proposal = research.read(
            seed["research"]["proposal_resource_id"],
            principal_id=LOCAL_PRINCIPAL_ID,
            project_id=seed["project_id"],
        )
    finally:
        research.close()
    check(
        "research.record_and_proposal_read_only_readable",
        record.revision == seed["research"]["record_revision"]
        and record.digest == seed["research"]["record_digest"]
        and record.document.get("research_id") == seed["research"]["research_id"]
        and proposal.revision == seed["research"]["proposal_revision"]
        and proposal.digest == seed["research"]["proposal_digest"],
        {
            "record": {"revision": record.revision, "digest": record.digest},
            "proposal": {"revision": proposal.revision, "digest": proposal.digest},
        },
    )

    # -- 9. frozen capability/plugin descriptor snapshot stays readable -----
    capability = seed["capability"]
    registry = CapabilityRegistry(
        capability_root=capability["root"],
        state_root=capability["state_root"],
        schema_path=Path(capability["contract"]) / "capability.schema.json",
        view_model_path=Path(capability["contract"]) / "view-model.schema.json",
    )
    freeze = registry.read_task_freeze(capability["task_id"], capability["attempt"])
    artifact = registry.read_frozen_artifact(capability["task_id"], capability["attempt"])
    snapshot_dir = Path(capability["snapshot_dir"])
    snapshot_now = (
        {
            path.name: _sha256_file(path)
            for path in sorted(snapshot_dir.rglob("*"))
            if path.is_file()
        }
        if snapshot_dir.is_dir()
        else {}
    )
    check(
        "snapshot.capability_freeze_read_only_readable",
        freeze.registration.descriptor_digest == capability["descriptor_digest"]
        and artifact.descriptor.get("capability_id") == capability["capability_id"]
        and snapshot_now == capability["snapshot_files"],
        {
            "descriptor_digest": freeze.registration.descriptor_digest,
            "expected": capability["descriptor_digest"],
            "snapshot_files": snapshot_now,
        },
    )

    # -- 10. reading/upgrading never rewrote or deleted a legacy row --------
    after = {
        "idempotency": _table_state(jobs_db, ("idempotency_records",)),
        "jobs": _table_state(jobs_db, ("jobs",)),
        "approvals": _table_state(jobs_db, ("approval_records",)),
        "prepare": _table_state(jobs_db, ("data_operations",)),
        "research": _table_state(run_root / "acp_research.db", ("research_resources",)),
        "usage": _table_state(admission_db, ("admission_usage_receipts",)),
        "research_budget": _table_state(admission_db, ("research_budget_receipts",)),
    }
    unchanged = {
        "legacy_job_rows": _job_rows() == legacy_job_rows,
        "jobs_count": after["jobs"]["counts"] == before["jobs"]["counts"],
        "approvals": after["approvals"] == before["approvals"],
        "research": after["research"] == before["research"],
        "usage": after["usage"] == before["usage"],
        "research_budget": after["research_budget"] == before["research_budget"],
        "idempotency_count": after["idempotency"]["counts"] == before["idempotency"]["counts"],
    }
    check(
        "state.no_legacy_row_deleted_or_rewritten",
        all(unchanged.values()),
        {
            "unchanged": unchanged,
            "before_usage": before["usage"]["counts"],
            "after_usage": after["usage"]["counts"],
        },
    )

    # -- 11. new writes use v2 identity/digest and never legacy key spaces --
    new_payload = _submit_payload("candidate_new_job", "S")
    from acp_services.store.records import JobRecord, JobSpec, JobStatus

    new_job = JobRecord(
        id="candidate_new_job",
        spec=JobSpec(
            workflow="singlepoint",
            name="candidate_new_job",
            input={"source": "S"},
            project_id=seed["project_id"],
        ),
        status=JobStatus.QUEUED,
        work_dir=str(run_root / "tasks" / "candidate_new_job"),
        project_id=seed["project_id"],
    )
    (run_root / "tasks" / "candidate_new_job").mkdir(parents=True, exist_ok=True)
    store.create(new_job)
    created = store.claim_idempotency(
        resolve_scope_id(LOCAL_PRINCIPAL_ID, seed["project_id"]),
        "submit",
        "candidate-new-key",
        canonical_request_digest(new_payload),
        request_payload=new_payload,
    )
    created_row = (
        _row(
            jobs_db,
            "SELECT * FROM idempotency_records WHERE claim_id=?",
            (created.claim.claim_id,),
        )
        if created.claim is not None
        else None
    )
    check(
        "new.claim_is_v2_native_and_not_legacy_namespace",
        created.kind == "created"
        and created_row is not None
        and created_row["identity_version"] == "2"
        and created_row["request_digest_version"] == "2"
        and created_row["scope_origin"] == SCOPE_ORIGIN_VERIFIED
        and created_row["migration_state"] == MIGRATION_STATE_NATIVE
        and not str(created_row["idempotency_key"]).startswith(("op_", "auto_")),
        None
        if created_row is None
        else {
            "identity_version": created_row["identity_version"],
            "request_digest_version": created_row["request_digest_version"],
            "scope_origin": created_row["scope_origin"],
            "migration_state": created_row["migration_state"],
            "key": created_row["idempotency_key"],
        },
    )
    legacy_keys = {
        label: claims[label]["key"] for label in ("committed", "auto", "pending", "unscoped")
    }
    rows_after = _table_state(jobs_db, ("idempotency_records",))["counts"]
    conn = sqlite3.connect(str(jobs_db))
    try:
        auto_rows = [
            row[0]
            for row in conn.execute(
                "SELECT idempotency_key FROM idempotency_records "
                "WHERE idempotency_key LIKE 'auto_%' ORDER BY idempotency_key"
            ).fetchall()
        ]
        op_rows = [
            row[0]
            for row in conn.execute(
                "SELECT idempotency_key FROM idempotency_records "
                "WHERE idempotency_key LIKE 'op_%' ORDER BY idempotency_key"
            ).fetchall()
        ]
    finally:
        conn.close()
    check(
        "new.no_auto_or_op_ledger_keys_written",
        auto_rows == legacy_auto_keys
        and op_rows == legacy_op_keys
        and legacy_keys["auto"] in auto_rows,
        {
            "auto_before": legacy_auto_keys,
            "auto_after": auto_rows,
            "op_before": legacy_op_keys,
            "op_after": op_rows,
        },
    )

    check(
        "state.new_rows_only_the_deliberate_ones",
        rows_after.get("idempotency_records")
        == before["idempotency"]["counts"].get("idempotency_records", 0) + 1
        and _table_state(jobs_db, ("jobs",))["counts"].get("jobs", 0)
        == before["jobs"]["counts"].get("jobs", 0) + 1,
        {"before": before["idempotency"]["counts"], "after": rows_after},
    )

    summary = _checks_to_document(checks)
    _json(
        {
            "action": "verify",
            "side": args.side,
            "seed_side": seed.get("side"),
            "checks": checks,
            "summary": summary,
            "state": {
                "jobs_schema_version": _schema_version(jobs_db),
                "prepare_schema_version": _prepare_schema_version(jobs_db),
                "admission_schema_version": _admission_schema_version(admission_db),
                "legacy_claims": {label: claims[label]["key"] for label in claims},
            },
        },
        args.out,
    )
    return 0 if summary["status"] == "PASS" else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

ACTIONS = {
    "report": action_report,
    "seed": action_seed,
    "migrate": action_migrate,
    "interrupt": action_interrupt,
    "verify": action_verify,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=sorted(ACTIONS))
    parser.add_argument("--side", default="unknown")
    parser.add_argument("--run-root", default="")
    parser.add_argument("--jobs-db", default="")
    parser.add_argument("--admission-db", default="")
    parser.add_argument("--seed", default="")
    parser.add_argument("--kill-at", default="024")
    parser.add_argument("--out", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(ACTIONS[args.action](args))


if __name__ == "__main__":
    raise SystemExit(main())
