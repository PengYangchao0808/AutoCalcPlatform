"""B23a — two-user/two-project isolation, idempotency, quota and cross-domain reuse.

The live Controller matrix is executed by
``scripts/acceptance/acp2_runtime.py --scenario multitenant --phase
multitenant:two-project``.  These tests prove the same invariants without a
Controller: same-name/same-key tasks never cross projects, cross-project
reads/control/prepare are non-disclosure, the shared capacity refuses the
second project, a duplicate subscription reuses one stream, and the reused
NMR/thermochemistry artifacts keep their units and provenance.

B23b is intentionally ``NOT_VERIFIED(c_t40a_absent)``: no C T-40a submission is
invented and no selectivity QC is launched.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.tasks import TaskIndex
from acp_services.contracts.events import EventScope
from acp_services.runtime.admission import (
    AdmissionRequest,
    Capacity,
    ReservationLedger,
    ReserveLimits,
)
from acp_services.runtime.events import AuthorizedStreamRegistry
from acp_services.runtime.idempotency import canonical_request_digest, resolve_scope_id
from acp_services.store.layout import runtime_file
from acp_services.store.migrations import migrate
from acp_services.store.store import JobStore
from acp_services.workspace.identity_lifecycle import (
    IdentityLifecycle,
    IdentityLifecyclePolicy,
)
from acp_services.workspace.keys import ApiKeyManager

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "acceptance" / "acp2_runtime.py"
A_CROSS_DOMAIN = Path("/ACPServer/acp2-development/A/tests/fixtures/acp2/science/cross_domain")
TENANT = "acp-b23-test"
ALPHA = "project_alpha"
BETA = "project_beta"
ALPHA_JOB = "b23-alpha-shared-test"
BETA_JOB = "b23-beta-shared-test"


def _load_harness() -> Any:
    spec = importlib.util.spec_from_file_location("acp2_runtime_b23", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["acp2_runtime_b23"] = module
    spec.loader.exec_module(module)
    return module


harness = _load_harness()


def _provision(tmp_path: Path) -> dict[str, Any]:
    db_path = tmp_path / "acp_identity.db"
    key_manager = ApiKeyManager(db_path, iterations=1_000)
    lifecycle = IdentityLifecycle(
        db_path,
        policy=IdentityLifecyclePolicy(
            deployment_tier="lab", capabilities=("authentication", "isolation")
        ),
        key_manager=key_manager,
        password_params={"kdf": "pbkdf2_hmac_sha256", "iterations": 1_000, "salt_bytes": 16},
    )
    accounts = {}
    for name, principal_id in (("alice", "principal_b23_alice"), ("bob", "principal_b23_bob")):
        invitation = lifecycle.invite_account(
            principal_id=principal_id, login_id=f"b23-{name}", roles=["member"]
        )
        accounts[name] = lifecycle.accept_invitation(
            invitation.token, password=f"B23-Strong-Passw0rd-{name}!"
        )
    keys = {
        "alice_write": lifecycle.issue_key(
            accounts["alice"].account_id,
            scope="workspace",
            actions=["read", "write", "control"],
            project=ALPHA,
        ),
        "bob_write": lifecycle.issue_key(
            accounts["bob"].account_id,
            scope="workspace",
            actions=["read", "write", "control"],
            project=BETA,
        ),
    }
    return {
        "db_path": db_path,
        "key_manager": key_manager,
        "lifecycle": lifecycle,
        "accounts": accounts,
        "keys": keys,
    }


def _insert_projects(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        for project_id in (ALPHA, BETA):
            conn.execute(
                """INSERT OR IGNORE INTO projects
                       (project_id, name, description, tags, run_root, settings,
                        created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id,
                    project_id,
                    "B23 test project",
                    "[]",
                    "",
                    "{}",
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:00:00Z",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_store_job(
    store: JobStore,
    tasks: TaskIndex,
    tmp_path: Path,
    *,
    job_id: str,
    project_id: str,
    task_name: str,
) -> None:
    work_dir = tmp_path / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    spec = JobSpec(
        workflow="fake",
        name=task_name,
        project_id=project_id,
        molecule_name="shared-molecule",
        task_name=task_name,
    )
    JobEventLog(runtime_file(work_dir, "events.jsonl")).append(
        "job.paused", idempotency_key=f"seed:{job_id}", stage="paused", job_id=job_id
    )
    record = JobRecord(
        id=job_id,
        spec=spec,
        status=JobStatus.PAUSED,
        work_dir=str(work_dir),
        project_id=project_id,
        group_id=job_id,
    )
    store.create(record)
    tasks.upsert(
        {
            "task_id": job_id,
            "job_id": job_id,
            "project_id": project_id,
            "molecule_name": "shared-molecule",
            "task_name": task_name,
            "remark": "",
            "display_name": spec.task_dir_name(),
            "workflow": "fake",
            "task_dir_name": spec.task_dir_name(),
            "status": "paused",
            "storage_mode": "local",
            "layout_version": 2,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "group_id": job_id,
        }
    )


@pytest.fixture()
def multiuser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    provisioned = _provision(tmp_path)
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("ACP_IDENTITY_MODE", "api-key")
    monkeypatch.setenv("ACP_IDENTITY_DB", str(provisioned["db_path"]))
    monkeypatch.setenv("ACP_IDENTITY_TENANT", TENANT)
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1)) as client:
        store = client.app.state.job_manager.store
        tasks = TaskIndex(store.db_path)
        _insert_projects(store.db_path)
        _seed_store_job(
            store, tasks, tmp_path, job_id=ALPHA_JOB, project_id=ALPHA, task_name="shared-task"
        )
        _seed_store_job(
            store, tasks, tmp_path, job_id=BETA_JOB, project_id=BETA, task_name="shared-task"
        )
        yield {"client": client, **provisioned}


def _headers(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def test_same_name_and_same_key_are_isolated(tmp_path: Path) -> None:
    migrate(tmp_path / "jobs.db")
    store = JobStore(tmp_path / "jobs.db")
    shared_key = "b23-shared-key"
    digest = canonical_request_digest(
        {"workflow": "fake", "name": "shared-task", "molecule_name": "shared-molecule"}
    )
    alpha_scope = resolve_scope_id("principal_b23_alice", ALPHA)
    beta_scope = resolve_scope_id("principal_b23_bob", BETA)
    assert alpha_scope != beta_scope

    alpha_claim = store.claim_idempotency(alpha_scope, "submit", shared_key, digest)
    assert alpha_claim.created
    alpha_record = JobRecord(
        id="alpha-shared",
        spec=JobSpec(
            workflow="fake",
            name="shared-task",
            project_id=ALPHA,
            molecule_name="shared-molecule",
            task_name="shared-task",
        ),
        status=JobStatus.PAUSED,
        work_dir=str(tmp_path / "alpha-shared"),
        project_id=ALPHA,
    )
    finalized = store.finalize_idempotent_job(
        alpha_record,
        alpha_claim.claim.claim_id,
        alpha_claim.claim.owner_token or "",
        alpha_claim.claim.lease_epoch,
    )
    assert finalized.created
    assert finalized.task_ref["job_id"] == "alpha-shared"

    beta_conflict = store.claim_idempotency(beta_scope, "submit", shared_key, digest)
    assert beta_conflict.conflict
    assert store.find_idempotency_claim(beta_scope, "submit", shared_key) is None

    beta_claim = store.claim_idempotency(beta_scope, "submit", "b23-shared-key-beta", digest)
    beta_record = JobRecord(
        id="beta-shared",
        spec=JobSpec(
            workflow="fake",
            name="shared-task",
            project_id=BETA,
            molecule_name="shared-molecule",
            task_name="shared-task",
        ),
        status=JobStatus.PAUSED,
        work_dir=str(tmp_path / "beta-shared"),
        project_id=BETA,
    )
    store.finalize_idempotent_job(
        beta_record,
        beta_claim.claim.claim_id,
        beta_claim.claim.owner_token or "",
        beta_claim.claim.lease_epoch,
    )
    assert alpha_record.spec.task_dir_name() == beta_record.spec.task_dir_name()
    assert store.find_idempotency_claim(alpha_scope, "submit", shared_key).task_id == "alpha-shared"
    assert (
        store.find_idempotency_claim(beta_scope, "submit", "b23-shared-key-beta").task_id
        == "beta-shared"
    )


def test_cross_project_read_control_prepare_and_v2_are_non_disclosure(
    multiuser: dict[str, Any],
) -> None:
    client = multiuser["client"]
    alice = _headers(multiuser["keys"]["alice_write"].key)
    bob = _headers(multiuser["keys"]["bob_write"].key)

    assert client.get(f"/api/v1/jobs/{ALPHA_JOB}", headers=alice).status_code == 200
    cross = client.get(f"/api/v1/jobs/{ALPHA_JOB}", headers=bob)
    missing = client.get("/api/v1/jobs/b23-missing", headers=bob)
    assert cross.status_code == missing.status_code == 404
    assert set(cross.json()) == set(missing.json()) == {"detail"}

    assert client.get(f"/api/v2/tasks/{ALPHA_JOB}", headers=bob).status_code == 404
    assert client.get(f"/api/v2/tasks/{ALPHA_JOB}/tree", headers=bob).status_code == 404
    assert client.post(f"/api/v1/jobs/{ALPHA_JOB}/cancel", headers=bob).status_code == 404
    assert (
        client.post(
            f"/api/v1/jobs/{ALPHA_JOB}/prepare",
            json={"purpose": "artifact", "artifact_id": "manifest"},
            headers=bob,
        ).status_code
        == 404
    )
    assert client.get(f"/api/v1/jobs/{BETA_JOB}", headers=alice).status_code == 404

    bob_view = client.get("/api/v2/task-view", headers=bob)
    assert bob_view.status_code == 200
    rows = [row for group in bob_view.json()["groups"] for row in group["jobs"]]
    assert rows and {row["project_id"] for row in rows} == {BETA}
    assert {item["project_id"] for item in client.get("/api/v2/projects", headers=bob).json()} == {
        BETA
    }
    assert client.get(f"/api/v2/task-view?project_id={ALPHA}", headers=bob).status_code == 404


def test_shared_node_capacity_is_enforced_across_projects(tmp_path: Path) -> None:
    ledger = ReservationLedger(str(tmp_path / "admission.db"))
    limits = ReserveLimits(
        capacity=Capacity(cores=1, memory_mb=512, concurrency=2, storage_bytes=10_000_000)
    )
    first = ledger.reserve(
        AdmissionRequest(
            principal_id="principal_b23_alice", project_id=ALPHA, job_id=ALPHA_JOB, cores=1
        ),
        limits,
    )
    second = ledger.reserve(
        AdmissionRequest(
            principal_id="principal_b23_bob", project_id=BETA, job_id=BETA_JOB, cores=1
        ),
        limits,
    )
    assert first.allowed is True
    assert second.allowed is False
    assert any(item["scope"] == "capacity" for item in second.exceeded)
    occupancy = ledger.occupancy()
    assert occupancy["cores"] == 1
    assert occupancy["reservations"] == 1


def test_duplicate_subscription_reuses_one_stream(tmp_path: Path) -> None:
    logs = {}
    for job_id in (ALPHA_JOB, BETA_JOB):
        work_dir = tmp_path / job_id
        work_dir.mkdir(parents=True)
        log = JobEventLog(runtime_file(work_dir, "events.jsonl"))
        log.append("job.paused", idempotency_key=f"seed:{job_id}", stage="paused", job_id=job_id)
        logs[job_id] = log

    registry = AuthorizedStreamRegistry()
    kwargs = {
        "principal_id": "principal_b23_alice",
        "scope": EventScope.TASK,
        "service_epoch": "test",
        "log_for": lambda job_id: logs[job_id],
    }
    first = registry.open(job_ids=[ALPHA_JOB], **kwargs)
    second = registry.open(job_ids=[BETA_JOB], **kwargs)
    assert first is second
    assert registry.active_count == 1
    assert sorted(second.task_ids) == sorted([ALPHA_JOB, BETA_JOB])
    batch = second.poll([])
    assert len(batch.events) == 2
    registry.close_all("test")


def _cross_domain_fixture(tmp_path: Path) -> dict[str, Any]:
    nmr = {
        "schema": "acp2.science.domain-fixture",
        "schema_version": 1,
        "domain": "nmr",
        "fixture_id": "nmr-b23-test",
        "real_run": False,
        "constructed": True,
        "reference": {
            "method": "mPW1PW91",
            "basis": "6-311G(d)",
            "solvent": "chloroform",
            "solvent_model": "cpcm",
            "scaling_mode": "goodman",
            "reference_source": "goodman-tmsdata",
            "error_model": "goodman-legacy",
            "required_nuclei": ["1H", "13C"],
            "temperature_kelvin": 298.15,
        },
    }
    thermo = {
        "schema": "acp2.science.domain-fixture",
        "schema_version": 1,
        "domain": "thermochemistry",
        "fixture_id": "thermo-b23-test",
        "real_run": False,
        "constructed": True,
        "standard_state": "1atm",
        "temperature_kelvin": 298.15,
        "weights_mode": "raw",
        "weight_tolerance": 1e-09,
        "weights_sum_supplied": 0.8,
    }
    nmr_path = tmp_path / "nmr.json"
    thermo_path = tmp_path / "thermo.json"
    nmr_path.write_text(json.dumps(nmr), encoding="utf-8")
    thermo_path.write_text(json.dumps(thermo), encoding="utf-8")
    return {
        "b23": {
            "cross_domain": {"nmr": str(nmr_path), "thermo": str(thermo_path)},
            "selectivity": {
                "run_spec_ref": "run-spec-da-selectivity-001",
                "proposed_budget_seconds": 3600,
                "proposed_max_qc_calls": 14,
            },
        }
    }


def test_cross_domain_units_provenance_and_budget_rows(tmp_path: Path) -> None:
    fixture = _cross_domain_fixture(tmp_path)
    cross = harness._b23_cross_domain(tmp_path, fixture)
    rows = {row["domain"]: row for row in cross["rows"]}
    assert set(rows) == {"nmr", "thermochemistry"}
    assert rows["nmr"]["units"]["isotropic_shielding"] == "ppm"
    assert rows["nmr"]["units"]["temperature"] == "K"
    assert rows["thermochemistry"]["units"]["gibbs_energy"] == "hartree"
    assert rows["thermochemistry"]["units"]["mixing_entropy"] == "kcal/(mol*K)"
    assert all(row["provenance"]["schema"] == "acp2.science.provenance.v1" for row in rows.values())
    assert cross["missing_metrics"]["nmr_experimental_spectrum_acquired"] is None
    assert cross["missing_metrics"]["thermochemistry_real_qc_energy"] is None

    budget = harness._b23_budget_rows(tmp_path, cross, fixture)
    budget_rows = {row["domain"]: row for row in budget["rows"]}
    assert set(budget_rows) == {"nmr", "thermo", "selectivity"}
    assert budget_rows["nmr"]["new_qc_calls"] == budget_rows["thermo"]["new_qc_calls"] == 0
    selectivity = budget_rows["selectivity"]
    assert selectivity["max_task_refs"] == 1
    assert selectivity["result"] == "NOT_VERIFIED"
    assert selectivity["reason"] == "c_t40a_absent"
    assert selectivity["frozen"] is False
    assert selectivity["budget_seconds"] is None
    assert selectivity["max_qc_calls"] is None
    assert selectivity["submission"] is None
    assert budget["reconcile"]["all_frozen"] is False


@pytest.mark.skipif(
    not (A_CROSS_DOMAIN / "nmr_shielding_constructed.json").is_file(),
    reason="A-25a constructed cross-domain fixtures are not present on this machine",
)
def test_existing_a_cross_domain_artifacts_keep_their_domains() -> None:
    nmr = json.loads((A_CROSS_DOMAIN / "nmr_shielding_constructed.json").read_text())
    thermo = json.loads((A_CROSS_DOMAIN / "thermochemistry_weights_constructed.json").read_text())
    assert nmr["domain"] == "nmr" and nmr["real_run"] is False
    assert thermo["domain"] == "thermochemistry" and thermo["real_run"] is False
    assert nmr["reference"]["temperature_kelvin"] > 0
    assert thermo["standard_state"] in {"1atm", "1M"}


def test_b23b_stays_not_verified_without_c_t40a(tmp_path: Path) -> None:
    fixture = _cross_domain_fixture(tmp_path)
    row = harness._b23_b23b_row(tmp_path, fixture)
    assert row["result"] == "NOT_VERIFIED"
    assert row["reason"] == "c_t40a_absent"
    assert row["deferred"] is False
    assert row["submission"] is None
    assert row["task_refs"] == []
    assert row["h6_fed"] is False
    assert row["paths"] == {"path.endo": None, "path.exo": None}


def test_dependency_edges_and_p4_sync_are_published() -> None:
    edges = harness._b23_dependency_edges()
    assert edges["all_present"] is True
    assert edges["missing"] == []
    assert edges["p4_sync"]["published_before_dispatch"] is True
    assert edges["p4_sync"]["identity"] == harness.B23_IDENTITY
    assert edges["p4_sync"]["gating"]["C-T40a"] == "B23a"
    evidence = REPO_ROOT / "reports/acp2_implementation/bb45a46/B/B23/p4-sub-delivery-sync.json"
    assert evidence.is_file()
    published = json.loads(evidence.read_text())
    assert published["published_before_dispatch"] is True
    assert [item["id"] for item in published["sub_deliveries"]] == ["B23a", "B23b"]


def test_identity_is_the_single_registered_triple() -> None:
    assert harness.B23_IDENTITY == {
        "scenario": "experience:cross-domain-live",
        "phase": "experience:selectivity",
        "subcase": "selectivity",
        "scenario_key": "experience:cross-domain-live/selectivity",
    }
    assert "selectivity-live" not in json.dumps(harness.B23_IDENTITY)


def test_multitenant_two_project_phase_is_b23_and_1800s() -> None:
    phase = harness.B_SCENARIOS["multitenant"].phases["multitenant:two-project"]
    assert phase.owner == "B23"
    assert phase.timeout == 1800
    assert phase.controller_required is True


def test_measurements_keep_untested_remote_metrics_null(tmp_path: Path) -> None:
    fixture = _cross_domain_fixture(tmp_path)
    fixture["controller"] = {
        "run_root": str(tmp_path / "run-root"),
        "jobs_database": str(tmp_path / "run-root" / "acp_jobs.db"),
    }
    fixture["projects"] = [
        {"project_id": ALPHA, "name": "Alpha"},
        {"project_id": BETA, "name": "Beta"},
    ]
    fixture["b23"]["jobs"] = [
        {
            "job_id": "alpha-measured",
            "project_id": ALPHA,
            "principal": "principal_b23_alice",
            "task_name": "shared-task",
            "molecule_name": "shared-molecule",
            "idem_key": "b23-measured-alpha",
        },
        {
            "job_id": "beta-measured",
            "project_id": BETA,
            "principal": "principal_b23_bob",
            "task_name": "shared-task",
            "molecule_name": "shared-molecule",
            "conflict_key": "b23-measured-alpha",
            "idem_key": "b23-measured-beta",
        },
    ]
    fixture["b23"]["event_sequence"] = [["job.queued", "queued"], ["job.paused", "paused"]]
    (tmp_path / "run-root").mkdir()
    seeded = harness._b23_seed_matrix(fixture, tmp_path)
    measurements = harness._b23_measurements(seeded, "alpha-measured", "beta-measured")
    assert measurements["reads"]["remote_live"]["value"] is None
    assert measurements["reads"]["remote_live"]["reason"] == "lsf_unavailable"
    assert measurements["duplicate_subscription"]["active_streams"] == 1
    assert measurements["duplicate_subscription"]["db_statements_delta"] == 0
    assert measurements["quota"]["beta_admitted"] is False
    assert measurements["storage"]["hosted_hard_limit_bytes"] is None
