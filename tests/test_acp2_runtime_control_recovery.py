"""B13 — control/recovery harness and fault-seam contract tests.

These tests pin the parts that must stay true without a live Controller:

* the scenario owns one separate row per Local fault, each with its own
  timeout/budget and TaskRef;
* the exit semantics keep Local PASS rows visible while LSF is NOT_VERIFIED;
* the publication fault launcher fails *only* the display-manifest write and
  restores it when the fault file disappears;
* a late/old-attempt receipt is rejected by the real receipt gate and a real
  terminal row cannot be advanced by the old CAS;
* the LSF control fixture is distinct from B11's normal-completion fixture and
  never invokes a fake scheduler.

Run with: pytest tests/test_acp2_runtime_control_recovery.py -q
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from acp_services.contracts.envelope import (
    CURRENT_ENVELOPE_VERSION,
    ExecutionEnvelope,
    ExecutionReceipt,
    expected_submission_id,
)
from acp_services.runtime.receipts import verify_receipt
from acp_services.store.records import JobRecord, JobSpec, JobStatus
from acp_services.store.store import JobStateConflictError, JobStore

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "acceptance" / "acp2_runtime.py"
FAULT_SCRIPT = REPO_ROOT / "scripts" / "acceptance" / "acp2_controller_fault.py"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


harness = _load("acp2_runtime_b13", SCRIPT)
fault_launcher = _load("acp2_controller_fault_b13", FAULT_SCRIPT)


def _envelope(**overrides: object) -> ExecutionEnvelope:
    kwargs: dict[str, object] = {
        "envelope_version": CURRENT_ENVELOPE_VERSION,
        "job_id": "20261023_000000_001_singlepoint",
        "attempt": 1,
        "submission_id": expected_submission_id("20261023_000000_001_singlepoint", 1),
        "release_id": "rel-b13",
        "release_digest": "sha256:" + "1" * 64,
        "owner": "local:workstation:1:1",
        "fencing_token": "fence-b13",
        "plan_digest": "v2:" + "2" * 32,
        "plan_identity": "v2:" + "3" * 32,
        "step_identities": ("v2:" + "4" * 32,),
    }
    kwargs.update(overrides)
    return ExecutionEnvelope(**kwargs)  # type: ignore[arg-type]


def _receipt(envelope: ExecutionEnvelope, **overrides: object) -> ExecutionReceipt:
    kwargs: dict[str, object] = {
        "receipt_version": 1,
        "job_id": envelope.job_id,
        "attempt": envelope.attempt,
        "submission_id": envelope.submission_id,
        "state": "completed",
        "result_digest": "sha256:" + "5" * 64,
        "release_id": envelope.release_id,
        "release_digest": envelope.release_digest,
        "owner": envelope.owner,
        "fencing_token": envelope.fencing_token,
        "plan_identity": envelope.plan_identity,
        "step_identities": envelope.step_identities,
        "written_at": "2026-10-23T00:00:00+00:00",
    }
    kwargs.update(overrides)
    return ExecutionReceipt(**kwargs)  # type: ignore[arg-type]


def _verify(envelope: ExecutionEnvelope, receipt: ExecutionReceipt, *, attempt: int) -> Any:
    return verify_receipt(
        receipt,
        envelope,
        current_attempt=attempt,
        current_submission_id=envelope.submission_id,
        current_release_id=envelope.release_id,
        owner_token=envelope.owner,
        fencing_token=envelope.fencing_token,
    )


def test_scenario_registered_and_one_row_per_local_fault() -> None:
    spec = harness.B_SCENARIOS["control-recovery"]
    assert spec.scenario == "control-recovery"
    assert spec.default_owner == "B13"
    assert spec.phases["main"].operation_class == "control"
    races = harness._b13_race_specs()
    assert [row["race_id"] for row in races] == [
        "adapter_eof",
        "controller_restart",
        "publication_failure",
        "cancel_race",
        "timeout_cancel",
    ]
    assert len({row["race_id"] for row in races}) == len(races)
    for row in races:
        assert row["timeout_seconds"] > 0
        assert row["budget"]["jobs"] == 1
        assert row["budget"]["max_orca"] == 1
        assert hashlib.sha256(str(row["xyz"]).encode("utf-8")).hexdigest()


def test_exit_semantics_keeps_local_pass_visible_with_lsf_gap() -> None:
    local_pass = ["PASS"] * 5
    assert harness._b13_exit_semantics(local_pass, "PASS")[0:2] == (0, "PASS")
    code, result, reason = harness._b13_exit_semantics(local_pass, "NOT_VERIFIED")
    assert (code, result) == (2, "NOT_VERIFIED")
    assert reason == "lsf_control_fixture_not_verified"
    assert harness._b13_exit_semantics(local_pass, "NOT_REQUIRED")[0:2] == (0, "PASS")
    assert harness._b13_exit_semantics(["PASS", "FAIL"], "PASS")[0:2] == (1, "FAIL")


def test_controller_argv_parser_extracts_restart_identity() -> None:
    argv = [
        "/venv/python",
        "-m",
        "acp2_controller_fault",
        "--fault-file",
        "/tmp/fault.json",
        "--",
        "run",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "18771",
        "--run-root",
        "/tmp/run-root-b13",
    ]
    parsed = harness._b13_parse_controller_argv(argv)
    assert parsed["module"] == "acp2_controller_fault"
    assert parsed["port"] == 18771
    assert parsed["run_root"] == "/tmp/run-root-b13"
    assert parsed["fault_file"] == "/tmp/fault.json"
    assert parsed["fault_seam"] is True


def test_publication_fault_launcher_is_file_armed_and_restores_writer(
    tmp_path: Path,
) -> None:
    calls: list[tuple[Path, Any, Any]] = []

    def real(result_dir: Path | str, manifest: Any, *, publisher: Any | None = None) -> Path:
        calls.append((Path(result_dir), manifest, publisher))
        return Path(result_dir) / "result_manifest.json"

    module = SimpleNamespace(register_result_manifest=real)
    fault_file = tmp_path / "fault.json"
    fault_file.write_text("{}", encoding="utf-8")
    fault_launcher.install_publication_fault(module, fault_file)

    with pytest.raises(OSError, match=harness.B13_PUBLICATION_FAULT_CODE):
        module.register_result_manifest(tmp_path, object())
    assert calls == [], "armed fault must not reach the real writer"

    fault_file.unlink()
    published = module.register_result_manifest(tmp_path, object(), publisher="data")
    assert published == tmp_path / "result_manifest.json"
    assert calls == [(tmp_path, calls[0][1], "data")]


def test_publication_fault_launcher_argv_split() -> None:
    fault_file, acp_argv = fault_launcher._split_argv(
        ["--fault-file", "/tmp/f.json", "--", "run", "serve", "--port", "18771"]
    )
    assert fault_file == Path("/tmp/f.json")
    assert acp_argv == ["run", "serve", "--port", "18771"]
    with pytest.raises(ValueError, match="separator"):
        fault_launcher._split_argv(["--fault-file", "/tmp/f.json"])


def test_late_receipt_is_identity_valid_but_old_attempt_is_stale() -> None:
    envelope = _envelope()
    receipt = _receipt(envelope)
    assert _verify(envelope, receipt, attempt=envelope.attempt).accepted is True
    old = _verify(envelope, receipt, attempt=envelope.attempt + 1)
    assert old.accepted is False
    assert old.code == "stale_attempt"


def test_terminal_row_rejects_old_cas_write(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "acp_jobs.db")
    record = JobRecord(
        id="b13-terminal",
        spec=JobSpec(workflow="singlepoint", input={}),
        status=JobStatus.CANCELLED,
        work_dir=str(tmp_path / "b13-terminal"),
    )
    store.create(record)
    before = store.get(record.id)
    assert before is not None

    with pytest.raises(JobStateConflictError):
        store.transition(
            record.id,
            expected_status="cancelling",
            expected_revision=before.revision,
            expected_attempt=before.attempt,
            status="completed",
        )

    after = store.get(record.id)
    assert after is not None
    assert after.status == JobStatus.CANCELLED
    assert after.revision == before.revision
    assert after.attempt == before.attempt


def test_lsf_control_fixture_is_separate_and_never_fakes_scheduler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "B13"
    out_dir.mkdir()
    fixture = tmp_path / "control.json"
    fixture.write_text('{"fixture_id": "fx-control-recovery-lsf-v1"}\n', encoding="utf-8")
    digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
    registry = {
        "fixtures": [
            {
                "fixture_id": "fx-lsf-singlepoint-v1",
                "input": str(fixture),
                "task_ref": "b11-normal",
            },
            {
                "fixture_id": "fx-control-recovery-lsf-v1",
                "input": str(fixture),
                "input_digest": digest,
                "task_ref": None,
                "submitted_once": False,
                "budget": {"jobs": 1, "walltime_s": 300},
                "scenario_timeout_seconds": 2400,
            },
        ]
    }
    (tmp_path / "fixture-runs.json").write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(
        harness,
        "_b11_lsf_prerequisites",
        lambda: {"observed": {"bsub": None}, "missing": ["bsub"]},
    )
    row = harness._b13_lsf_control_row(out_dir, str(fixture))
    assert row["registered"] is True
    assert row["distinct_from_normal"] is True
    assert row["task_ref"] is None
    assert row["submitted_once"] is False
    assert row["input_digest_matches"] is True
    assert row["missing_prerequisites"] == ["bsub"]
    assert row["no_fake_scheduler"] is True
    assert row["no_bsub_invoked"] is True
    assert row["status"] == "NOT_VERIFIED"


def test_missing_controller_is_not_verified() -> None:
    code, assertions, extensions = harness._run_control_recovery(
        None, "/tmp/control.json", Path("/tmp/b13-out"), 30.0
    )
    assert code == 2
    assert assertions[0]["status"] == "NOT_VERIFIED"
    assert extensions["b13.reason"] == "missing_controller"


def test_dry_run_has_no_ellipsis(tmp_path: Path) -> None:
    completed = __import__("subprocess").run(
        [
            sys.executable,
            str(SCRIPT),
            "--scenario",
            "control-recovery",
            "--controller",
            "http://127.0.0.1:18771",
            "--fixture",
            "/tmp/control.json",
            "--out",
            str(tmp_path),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--scenario control-recovery" in completed.stdout
    assert "..." not in completed.stdout
