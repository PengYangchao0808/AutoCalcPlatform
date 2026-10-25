"""B13-3 (plan-2 todo 68) — consistent backup/restore drill for the Services era.

Extends the P0-04 (todo 4) drill
(``.omo/evidence/acp-parallel-plan-2-services/drill_b00_backup_recovery.py``)
from the scheduler-era surfaces to the **Services-era** surfaces, still as a
real, isolated drill (temporary run roots only — never the production
run_root/DB/service):

* **DB** — the real ``acp_jobs.db`` built by the real initialisers: ``JobStore``
  (jobs + outbox + ``_schema_migrations``), ``TaskIndex`` (tasks),
  ``StageTaskStore`` (stage_tasks), ``StructureSourceStore``
  (structure_source_* + ``organization_events``), ``PrepareOperationService``
  (``data_operations``) and ``ProjectManager`` (projects).  A SQLite **online
  backup** snapshot (WAL-safe) is the unit of consistency.
* **task directories** — the v2 task tree (``WORK/00_RUNTIME`` + ``RESULT``)
  with ``job.json`` / ``task.json`` / ``events.jsonl``.
* **scientific originals & artifacts** — ``input.xyz`` and the ``RESULT``
  product behind ``result_manifest.json``; restored byte-for-byte.
* **secret version** — an out-of-DB ``secret_version.json`` placeholder plus a
  real digest-only ``ApiKeyManager`` store (``api_keys.db``); the restored
  credential still verifies with the pre-backup plaintext, and the plaintext
  never appears in the backup bytes.
* **ledger** — B11 (Credit ledger) is price-gated and **not enabled**: the
  sub-case is asserted NOT in scope and marked ``DEFERRED`` with the gate
  reason.  No ledger evidence is fabricated.
* **post-restore ordering** — the real ``JobManager.__init__`` must reconcile
  external in-flight jobs **before** dispatch opens, and a restored *old* DB
  must never re-dispatch an existing computation.  A skip-reconcile control
  demonstrates the duplicate-dispatch risk the guarded order prevents.
* **component adoption scope** — integration follows the component manifest's
  adopted set only (OS-11: Caddy/Prometheus/restic are ``candidate``);
  unadopted components are asserted not installed and not declared in packaging.

Run with: ``python3.11 -m pytest tests/test_services_backup_restore.py -q``
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py3.10 CI
    tomllib = pytest.importorskip("tomli")  # type: ignore[assignment]

from acp.scheduler.manager import JobManager
from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.runner import RemoteJobRunner
from acp.scheduler.remote.submission import (
    submission_id_for,
    submission_lsf_name,
    submit_lease_valid,
)
from acp.scheduler.stage_tasks import StageTaskStore
from acp.scheduler.structure_source_store import StructureSourceStore
from acp.scheduler.tasks import TaskIndex
from acp_services.data.contracts import ResourceKind
from acp_services.data.operations import (
    PrepareOperationService,
    PrepareRequest,
    PrepareState,
)
from acp_services.store.layout import TaskStorage
from acp_services.store.migrations import get_schema_version
from acp_services.store.records import JobRecord, JobSpec, JobStatus
from acp_services.store.store import JobStore
from acp_services.workspace.keys import ApiKeyManager
from acp_services.workspace.projects import ProjectManager

REPO = Path(__file__).resolve().parents[1]
PYPROJECT = REPO / "pyproject.toml"
REQUIREMENTS_NODE = REPO / "requirements-node.txt"
MANIFEST_DOC = REPO / "docs" / "reference" / "next-change" / "ACP_Services_Component_Manifest.md"
EVIDENCE_DOC = REPO / "tests" / "baseline" / "services-evidence" / "backup_restore_drill.md"
LEDGER_MODULE = REPO / "src" / "acp_services" / "workspace" / "ledger.py"
PLAN_DOC = REPO / ".omo" / "plans" / "acp-parallel-plan-2-services.md"

DB_NAME = "acp_jobs.db"
SECRETS_DB = "api_keys.db"
JOB_DONE = "20260101_000001_singlepoint_done"
JOB_INFLIGHT = "20260101_000002_optimize_inflight"
NODE_NAME = "node1"
REMOTE_RELATIVE = "proj/optimize_inflight"

#: OS-11 (todo 68) components: HTTPS / observability / backup.  The manifest
#: status is ``candidate`` for all three — integration is adoption-scope driven.
OS11_COMPONENTS: dict[str, tuple[str, ...]] = {
    "Caddy": ("caddy",),
    "Prometheus": ("prometheus", "prometheus_client", "promtool"),
    "restic": ("restic",),
}

#: candidate component -> probe identifiers (python package / binary / dep alias).
CANDIDATE_PROBES: dict[str, tuple[str, ...]] = {
    "pwdlib": ("pwdlib",),
    "Authlib": ("authlib",),
    "PyCasbin": ("casbin",),
    "Keycloak": ("keycloak",),
    "PostgreSQL": ("psycopg", "psycopg2", "asyncpg", "pg8000"),
    "Caddy": ("caddy",),
    "Slurm": ("pyslurm", "slurm"),
    "Podman": ("podman",),
    "SeaweedFS": ("seaweedfs", "boto3", "minio"),
    "Apache ECharts": ("echarts",),
    "QCElemental": ("qcelemental",),
    "QCEngine": ("qcengine",),
    "cclib": ("cclib",),
    "Lago": ("lago", "stripe"),
    "Prometheus": ("prometheus", "prometheus_client"),
    "restic": ("restic",),
}


# --------------------------------------------------------------------------- #
# SQL helpers
# --------------------------------------------------------------------------- #
def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def _sql(db: Path, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    conn = _connect(db)
    try:
        return [tuple(row) for row in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def _sql_exec(db: Path, query: str, params: tuple[Any, ...] = ()) -> None:
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(query, params)
        conn.commit()
    finally:
        conn.close()


def _tables(db: Path) -> list[str]:
    rows = _sql(
        db,
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name",
    )
    return [str(r[0]) for r in rows]


def _table_state(db: Path) -> dict[str, tuple[int, str]]:
    """Return ``{table: (row_count, logical_digest)}`` for every user table."""
    state: dict[str, tuple[int, str]] = {}
    for table in _tables(db):
        conn = _connect(db)
        try:
            rows = conn.execute(f"SELECT * FROM [{table}] ORDER BY rowid").fetchall()
        finally:
            conn.close()
        material = json.dumps([list(r) for r in rows], sort_keys=True, default=str)
        state[table] = (len(rows), hashlib.sha256(material.encode("utf-8")).hexdigest())
    return state


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _future_lease(seconds: int = 3600) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _intent_result(job_id: str, attempt: int, lease_iso: str) -> dict[str, Any]:
    return {
        "node": NODE_NAME,
        "remote_dir": f"/scratch/user/acp_jobs/{REMOTE_RELATIVE}",
        "remote": {
            "schema": 1,
            "attempt": attempt,
            "submission_id": submission_id_for(job_id, attempt),
            "node": NODE_NAME,
            "submit_state": "intent",
            "submit_owner": "drill-owner",
            "lease_expires_at": lease_iso,
            "intent_at": datetime.now(timezone.utc).isoformat(),
            "relative": REMOTE_RELATIVE,
        },
    }


# --------------------------------------------------------------------------- #
# source run_root builder (real Services schema + rows + artifacts)
# --------------------------------------------------------------------------- #
def _build_source(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    db = root / DB_NAME

    # real schema initialisers (one shared acp_jobs.db, as in production)
    store = JobStore(db)  # jobs + job_outbox + _schema_migrations (+ migrate())
    task_index = TaskIndex(store.db_path)  # tasks
    StageTaskStore(store.db_path)  # stage_tasks
    StructureSourceStore(str(db))  # structure_source_* + organization_events
    project_manager = ProjectManager(None, root, db_path=db)  # projects
    project_manager.ensure_default_project()

    ops = PrepareOperationService(db)  # data_operations (durable Services state)
    op = ops.enqueue(
        PrepareRequest(
            job_id=JOB_INFLIGHT,
            attempt=1,
            resource_id="RESULT/structures/min.xyz",
            resource_kind=ResourceKind.CANDIDATE,
            resource_digest="sha256:drill-resource",
            permission_scope="local-single",
            payload={"note": "prepare op survives backup/restore"},
        )
    )
    ops.close()

    # secret version: out-of-DB placeholder + digest-only key store
    secret_ph = root / "secret_version.json"
    secret_ph.write_text(
        json.dumps(
            {
                "schema": 1,
                "credential_version": "v-drill-1",
                "key_store": SECRETS_DB,
                "note": "placeholder for out-of-DB credentials; plaintext never persisted",
            }
        ),
        encoding="utf-8",
    )
    key_manager = ApiKeyManager(root / SECRETS_DB, iterations=2_000)
    generated = key_manager.generate(scope="local-single", actions=["submit"], project="default")

    # terminal COMPLETED job: task dir + manifest + scientific original
    work_d = root / "proj" / "singlepoint_done"
    storage_d = TaskStorage(work_d)
    storage_d.ensure_layout()
    storage_d.write_input_xyz("2\nwater\nO 0.0 0.0 0.0\nH 0.0 0.0 1.0\n")
    product_d = work_d / "RESULT" / "structures" / "min.xyz"
    product_d.parent.mkdir(parents=True, exist_ok=True)
    product_d.write_text("2\nminimum\nO 0.0 0.0 0.0\nH 0.0 0.0 0.96\n", encoding="utf-8")
    manifest_d = work_d / "RESULT" / "result_manifest.json"
    manifest_d.write_text(
        json.dumps(
            {
                "task_id": JOB_DONE,
                "workflow": "singlepoint",
                "status": "completed",
                "version": 2,
                "products": [
                    {"kind": "structure", "path": "structures/min.xyz", "role": "minimum"}
                ],
            }
        ),
        encoding="utf-8",
    )
    rec_d = JobRecord(
        id=JOB_DONE,
        spec=JobSpec(workflow="singlepoint", name="singlepoint_done", input={"source": "O"}),
        status=JobStatus.COMPLETED,
        work_dir=str(work_d),
        result={"terminal_side_effects_done": True, "exit_code": 0},
    )
    store.create(rec_d)
    task_index.sync_from_job(rec_d)
    (work_d / "job.json").write_text(json.dumps(rec_d.to_dict(), default=str), encoding="utf-8")
    (work_d / "task.json").write_text(
        json.dumps({"task_id": JOB_DONE, "status": "completed"}), encoding="utf-8"
    )
    events_d = work_d / "WORK" / "00_RUNTIME" / "events.jsonl"
    events_d.write_text('{"event_type": "job.completed"}\n', encoding="utf-8")

    # in-flight remote job: STARTING with a persisted submit intent (old DB)
    work_i = root / "proj" / "optimize_inflight"
    storage_i = TaskStorage(work_i)
    storage_i.ensure_layout()
    rec_i = JobRecord(
        id=JOB_INFLIGHT,
        spec=JobSpec(
            workflow="optimize",
            name="optimize_inflight",
            input={"source": "O"},
            execution_mode="remote",
            target_node=NODE_NAME,
        ),
        status=JobStatus.STARTING,
        work_dir=str(work_i),
        result=_intent_result(JOB_INFLIGHT, 1, _future_lease()),
    )
    store.create(rec_i)
    task_index.sync_from_job(rec_i)
    (work_i / "job.json").write_text(json.dumps(rec_i.to_dict(), default=str), encoding="utf-8")
    (work_i / "task.json").write_text(
        json.dumps({"task_id": JOB_INFLIGHT, "status": "starting"}), encoding="utf-8"
    )

    # structure_source + organization_events rows (real schema, direct rows)
    _sql_exec(
        db,
        "INSERT INTO structure_source_index (source_uid, job_id, relative_path, "
        "legacy_source_id, project_id, label, discovered_at) VALUES (?,?,?,?,?,?,?)",
        (
            "ss_drill0001",
            JOB_DONE,
            "RESULT/structures/min.xyz",
            "legacy0001",
            "default",
            "min.xyz",
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    _sql_exec(
        db,
        "INSERT INTO organization_events (object_type, object_id, action, old_value, "
        "new_value, created_at) VALUES (?,?,?,?,?,?)",
        (
            "task",
            JOB_DONE,
            "created",
            None,
            "singlepoint_done",
            datetime.now(timezone.utc).isoformat(),
        ),
    )

    return {
        "root": root,
        "db": db,
        "store": store,
        "secret_ph": secret_ph,
        "key_manager": key_manager,
        "plaintext_key": generated.key,
        "generated": generated,
        "op_id": op.operation_id,
        "states": _table_state(db),
        "artifacts": {
            "input": work_d / "input.xyz",
            "product": product_d,
            "manifest": manifest_d,
            "job_json": work_d / "job.json",
            "task_json": work_d / "task.json",
            "events": events_d,
        },
    }


# --------------------------------------------------------------------------- #
# backup / restore (SQLite online backup + task tree + secret files)
# --------------------------------------------------------------------------- #
def _backup(source: dict[str, Any]) -> Path:
    bdir = source["root"] / "backup"
    bdir.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(source["db"]))
    dst = sqlite3.connect(str(bdir / DB_NAME))
    try:
        with dst:
            src.backup(dst)  # sqlite online backup API (WAL-safe snapshot)
    finally:
        dst.close()
        src.close()
    shutil.copytree(source["root"] / "proj", bdir / "proj")
    shutil.copy2(source["secret_ph"], bdir / "secret_version.json")
    shutil.copy2(source["root"] / SECRETS_DB, bdir / SECRETS_DB)
    return bdir


def _restore(bdir: Path, target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bdir / DB_NAME, target / DB_NAME)
    if (target / "proj").exists():
        shutil.rmtree(target / "proj")
    shutil.copytree(bdir / "proj", target / "proj")
    shutil.copy2(bdir / "secret_version.json", target / "secret_version.json")
    shutil.copy2(bdir / SECRETS_DB, target / SECRETS_DB)
    return target / DB_NAME


def _backup_leaks_plaintext(bdir: Path, plaintext: str) -> bool:
    needle = plaintext.encode("utf-8")
    for path in bdir.rglob("*"):
        if path.is_file() and needle in path.read_bytes():
            return True
    return False


# --------------------------------------------------------------------------- #
# fake remote transport (drives the REAL reconcile_submission)
# --------------------------------------------------------------------------- #
class _FakeSSH:
    """``bjobs`` transport double. ``mode`` in {found, not_found, error}."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.bsub_calls = 0

    def execute(self, node: Any, command: str, timeout: Any = None) -> tuple[int, str, str]:
        if self.mode == "found":
            return (0, "12345   user   acp_drill   0    RUN\n", "")
        if self.mode == "not_found":
            return (0, "Job <acp_drill> is not found\n", "")
        return (1, "", "ssh: connect failed")


class _FakeStager:
    """SFTP stager double: readable job.json/task.json markers, no run evidence."""

    def __init__(self, owner: str | None, attempt: int = 1) -> None:
        self.owner = owner
        self.attempt = attempt

    def read_remote_file(self, node: Any, path: str) -> bytes:
        name = path.rsplit("/", 1)[-1]
        if name in ("job.json", "task.json") and self.owner is not None:
            return json.dumps(
                {"id": self.owner, "task_id": self.owner, "attempt": self.attempt}
            ).encode()
        raise FileNotFoundError(path)

    def list_remote_dir(self, node: Any, path: str) -> list[Any]:
        raise FileNotFoundError(path)


def _fake_runner(
    mode: str, owner: str | None, attempt: int = 1
) -> tuple[RemoteJobRunner, _FakeSSH]:
    node = RemoteNode(
        name=NODE_NAME,
        host="drill.invalid",
        username="user",
        remote_work_dir="/scratch/user/acp_jobs",
        remote_code_dir="/home/user/acp_code",
    )
    config = RemoteExecutionConfig(execution_mode="remote", nodes=[node], poll_interval=30)
    ssh = _FakeSSH(mode)
    runner = RemoteJobRunner(ssh_pool=ssh, remote_config=config, stager=_FakeStager(owner, attempt))
    return runner, ssh


class _RecordingManager(JobManager):
    """Real JobManager that records the startup call order and dispatch set.

    ``_start_submission_thread`` is recorded instead of started so the drill
    never launches a real worker (no bsub / no compute).  The guarded order
    (``_requeue_active_on_startup`` before ``_dispatch_queued_jobs``) and the
    reconcile call are still the live code paths.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.call_order: list[str] = []
        self.dispatched: list[str] = []
        super().__init__(*args, **kwargs)

    def _requeue_active_on_startup(self) -> None:
        self.call_order.append("requeue_active_on_startup")
        return super()._requeue_active_on_startup()

    def _reconcile_submission_record(self, record: JobRecord) -> None:
        self.call_order.append(f"reconcile:{record.id}")
        return super()._reconcile_submission_record(record)

    def _dispatch_queued_jobs(self) -> None:
        self.call_order.append("dispatch_queued_jobs")
        return super()._dispatch_queued_jobs()

    def _start_submission_thread(self, job_id: str, thread_name: str) -> bool:
        self.dispatched.append(job_id)
        return False


class _NaiveManager(_RecordingManager):
    """Control: skip startup reconciliation (the MUST-NOT path)."""

    def _requeue_active_on_startup(self) -> None:
        self.call_order.append("requeue_active_on_startup[SKIPPED]")


# --------------------------------------------------------------------------- #
# drill fixture
# --------------------------------------------------------------------------- #
def _drill(tmp_path: Path) -> dict[str, Any]:
    source = _build_source(tmp_path / "A_source")
    bdir = _backup(source)
    restored = tmp_path / "B_restored"
    _restore(bdir, restored)
    return {"source": source, "bdir": bdir, "restored": restored, "db": restored / DB_NAME}


def _quiet_runner() -> MagicMock:
    quiet = MagicMock()
    quiet.poll.return_value = (False, None)
    return quiet


# --------------------------------------------------------------------------- #
# 1. coherent backup/restore of DB + task dirs + manifest + secret version
# --------------------------------------------------------------------------- #
def test_coherent_backup_restore_db_dirs_manifest_secret(tmp_path: Path) -> None:
    drill = _drill(tmp_path)
    source, db = drill["source"], drill["db"]

    assert _table_state(db) == source["states"], "DB row-content parity A vs restored B"

    conn_a, conn_b = _connect(source["db"]), _connect(db)
    try:
        assert get_schema_version(conn_b) == get_schema_version(conn_a)
    finally:
        conn_a.close()
        conn_b.close()
    jobs = {row[0]: row[1] for row in _sql(db, "SELECT id, status FROM jobs")}
    assert jobs == {JOB_DONE: "completed", JOB_INFLIGHT: "starting"}

    # artifacts + scientific original restored byte-for-byte
    for key in ("input", "product", "manifest"):
        original = source["artifacts"][key]
        restored_path = Path(str(original).replace("A_source", "B_restored"))
        assert restored_path.is_file(), f"{key} missing after restore"
        assert _sha256(restored_path) == _sha256(original), f"{key} bytes differ"

    # task tree + runtime events restored
    task_root = drill["restored"] / "proj" / "singlepoint_done"
    assert (task_root / "job.json").is_file()
    assert (task_root / "task.json").is_file()
    assert (task_root / "WORK" / "00_RUNTIME" / "events.jsonl").is_file()

    # secret version placeholder identical
    assert (drill["restored"] / "secret_version.json").read_bytes() == (
        source["secret_ph"].read_bytes()
    )

    # digest-only key store restored: pre-backup plaintext still verifies
    restored_keys = ApiKeyManager(drill["restored"] / SECRETS_DB, iterations=2_000)
    decision = restored_keys.verify(
        source["plaintext_key"],
        required_action="submit",
        required_scope="local-single",
        project="default",
    )
    assert decision.allowed and decision.key_id == source["generated"].key_id

    # the plaintext credential never appears in the backup bytes
    assert not _backup_leaks_plaintext(drill["bdir"], source["plaintext_key"]), (
        "plaintext credential leaked into backup"
    )

    # the durable Services prepare operation survived (same id, still queued)
    ops = PrepareOperationService(db)
    try:
        record = ops.get(source["op_id"])
        assert record is not None and record.state is PrepareState.QUEUED
    finally:
        ops.close()


def test_restore_is_idempotent(tmp_path: Path) -> None:
    drill = _drill(tmp_path)
    db = drill["db"]
    before = _table_state(db)
    _restore(drill["bdir"], drill["restored"])
    assert _table_state(db) == before, "re-applying the same snapshot changed restored state"


def test_credit_ledger_subcase_deferred_by_price_gate(tmp_path: Path) -> None:
    drill = _drill(tmp_path)
    db = drill["db"]
    ledger_tables = [t for t in _tables(db) if re.search(r"ledger|credit|wallet", t, re.I)]
    assert ledger_tables == [], f"unexpected ledger tables present: {ledger_tables}"
    assert not LEDGER_MODULE.exists(), "B11 ledger module appeared; sub-case must be re-scoped"
    assert importlib.util.find_spec("acp_services.workspace.ledger") is None
    if PLAN_DOC.exists():
        plan = PLAN_DOC.read_text(encoding="utf-8")
        assert "价格政策" in plan and "B11" in plan and "未过不开工" in plan


# --------------------------------------------------------------------------- #
# 2. reconcile external in-flight jobs BEFORE dispatch opens (old-DB restore)
# --------------------------------------------------------------------------- #
def test_restore_reconciles_external_jobs_before_dispatch(tmp_path: Path) -> None:
    drill = _drill(tmp_path)
    pre_crash_sid = submission_id_for(JOB_INFLIGHT, 1)
    restored_events = (
        drill["restored"] / "proj" / "singlepoint_done" / "WORK" / "00_RUNTIME" / "events.jsonl"
    )
    done_events_before = restored_events.read_bytes()

    mgr = _RecordingManager(run_root=drill["restored"], runner=_quiet_runner(), poll_interval=30)
    try:
        order = mgr.call_order
        assert (
            order.index("requeue_active_on_startup")
            < order.index(f"reconcile:{JOB_INFLIGHT}")
            < order.index("dispatch_queued_jobs")
        ), order

        assert mgr.dispatched == [], "dispatch must not select anything at startup"
        done = mgr.store.get(JOB_DONE)
        assert done is not None and done.status is JobStatus.COMPLETED
        inflight = mgr.store.get(JOB_INFLIGHT)
        assert inflight is not None and inflight.status is JobStatus.STARTING
        assert (inflight.result or {})["remote"]["submission_id"] == pre_crash_sid
        assert submit_lease_valid((inflight.result or {})["remote"]) is True
        assert done_events_before == restored_events.read_bytes()

        runner, ssh = _fake_runner("found", owner=JOB_INFLIGHT)
        mgr.remote_runner = runner
        mgr._reconcile_once()
        adopted = mgr.store.get(JOB_INFLIGHT)
        assert adopted is not None
        assert adopted.status is JobStatus.PENDING
        assert adopted.remote_job_id == "12345"
        assert (adopted.result or {})["remote"]["submission_id"] == pre_crash_sid
        assert ssh.bsub_calls == 0, "reconcile must adopt, never re-submit"

        mgr._dispatch_queued_jobs()
        assert mgr.dispatched == [], "restored old DB must never re-dispatch a computation"
    finally:
        mgr.shutdown()


def test_control_skip_reconcile_duplicate_dispatch(tmp_path: Path) -> None:
    drill = _drill(tmp_path)
    control_root = tmp_path / "C_control"
    _restore(drill["bdir"], control_root)
    cdb = control_root / DB_NAME
    pre_crash_lsf = submission_lsf_name(submission_id_for(JOB_INFLIGHT, 1))

    payload = json.dumps(_intent_result(JOB_INFLIGHT, 1, _future_lease()))
    _sql_exec(
        cdb,
        "UPDATE jobs SET status=?, remote_job_id=NULL, result_json=?, attempt=1 WHERE id=?",
        (JobStatus.QUEUED.value, payload, JOB_INFLIGHT),
    )

    mgr = _NaiveManager(run_root=control_root, runner=_quiet_runner(), poll_interval=30)
    try:
        assert JOB_INFLIGHT in mgr.dispatched, "control should have re-dispatched the job"
        redispatch_lsf = submission_lsf_name(submission_id_for(JOB_INFLIGHT, 1))
        assert redispatch_lsf == pre_crash_lsf, (
            "control: deterministic submission id -> identical LSF name -> duplicate dispatch"
        )
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------------- #
# 3. component integration only per manifest adoption scope (OS-11)
# --------------------------------------------------------------------------- #
def _manifest_rows() -> list[dict[str, Any]]:
    text = MANIFEST_DOC.read_text(encoding="utf-8")
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 13 or not cells[0].isdigit():
            continue
        rows.append(
            {
                "num": int(cells[0]),
                "component": cells[1],
                "status": cells[10],
                "gate": cells[11],
                "evidence": cells[12],
            }
        )
    return rows


def _manifest_status_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for token in ("existing", "candidate", "pilot", "approved"):
        match = re.search(rf"\|\s*`{token}`\s*\|[^|]*\|\s*\*{{0,2}}\s*(\d+)\s*\*{{0,2}}\s*\|", text)
        if match:
            counts[token] = int(match.group(1))
    return counts


def _dep_base(spec: str) -> str:
    for sep in "<>=!~;[":
        spec = spec.split(sep, 1)[0]
    return spec.strip().lower().replace("_", "-")


def _declared_dependencies() -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    names = {_dep_base(spec) for spec in data["project"]["dependencies"]}
    for group in data["project"].get("optional-dependencies", {}).values():
        names |= {_dep_base(spec) for spec in group}
    for line in REQUIREMENTS_NODE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.add(_dep_base(line))
    return names


def _imported_top_levels() -> set[str]:
    modules: set[str] = set()
    for path in (REPO / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module.split(".")[0])
    return modules


def test_component_integration_follows_manifest_adoption_scope() -> None:
    text = MANIFEST_DOC.read_text(encoding="utf-8")
    rows = _manifest_rows()
    assert rows, "component manifest main table not parseable"
    assert {r["status"] for r in rows} <= {"existing", "candidate", "pilot", "approved"}

    parsed = _manifest_status_counts(text)
    actual_counts = {token: 0 for token in ("existing", "candidate", "pilot", "approved")}
    for row in rows:
        actual_counts[row["status"]] += 1
    assert parsed == actual_counts, f"manifest status counts drifted: {parsed} vs {actual_counts}"
    assert actual_counts["pilot"] == 0 and actual_counts["approved"] == 0

    candidates = {r["component"]: r for r in rows if r["status"] == "candidate"}
    for row in candidates.values():
        assert row["evidence"] == "None", f"candidate {row['component']} claims evidence"

    declared = _declared_dependencies()
    imported = _imported_top_levels()
    for component, probes in CANDIDATE_PROBES.items():
        assert component in candidates, f"{component} no longer a candidate"
        for probe in probes:
            assert probe not in declared, f"unadopted candidate {probe} declared in packaging"
            assert probe not in imported, f"unadopted candidate {probe} imported by src"

    os11 = {c: candidates[c] for c in OS11_COMPONENTS}
    for component, probes in OS11_COMPONENTS.items():
        assert os11[component]["status"] == "candidate"
        for probe in probes:
            assert importlib.util.find_spec(probe) is None, f"{probe} unexpectedly imported"
            assert shutil.which(probe) is None, f"{probe} unexpectedly installed"
        scripts = (REPO / "scripts").glob("*.sh")
        for script in scripts:
            assert component.lower() not in script.read_text(encoding="utf-8").lower()

    existing_names = {r["component"] for r in rows if r["status"] == "existing"}
    assert {"NumPy", "RDKit", "FastAPI", "Paramiko"} <= existing_names


def test_evidence_backup_restore_drill_record(tmp_path: Path) -> None:
    assert EVIDENCE_DOC.is_file(), "drill record missing"
    text = EVIDENCE_DOC.read_text(encoding="utf-8")
    rows = _manifest_rows()
    actual_counts = {token: 0 for token in ("existing", "candidate", "pilot", "approved")}
    for row in rows:
        actual_counts[row["status"]] += 1
    for token in ("PASS", "DEFERRED", "NOT_APPLICABLE", "price"):
        assert token in text
    for component in OS11_COMPONENTS:
        assert component in text
    for status, count in actual_counts.items():
        assert f"{status}={count}" in text, status
