"""B01 acceptance harness tests: contract validation, CLI exit codes, cleanup."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "acceptance" / "acp2_runtime.py"
SAMPLES = REPO_ROOT / "docs/reference/next-change/acp2-contracts/runtime/samples/acceptance"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("acp2_runtime", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["acp2_runtime"] = module
    spec.loader.exec_module(module)
    return module


harness = _load_module()


def _run(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_help_lists_15_scenarios_and_phase_registry() -> None:
    result = _run("--help")
    assert result.returncode == 0
    for scenario in harness.EXPECTED_B_SCENARIOS:
        assert scenario in result.stdout
    assert "Phase registry" in result.stdout
    assert "mcp-stdio" in result.stdout
    assert "--launch" in result.stdout
    assert "dsh-local-singlepoint is C-owned" in result.stdout
    assert "never a scenario PASS" in result.stdout


def test_contract_version_tracks_delivered_manifest() -> None:
    manifest = _read(REPO_ROOT / "docs/reference/next-change/acp2-contracts/manifest.json")
    assert harness.contract_version() == manifest["contract_version"]
    # M0-01 review vector: registered release contracts live inside the same
    # annotated delivery set, so the frozen manifest version is 1.4.0.
    # M0-12 (2026-10-27): the decision-freeze registrations move it to 1.5.0.
    # M1-01/M1-02: contract unification + research-session -> 1.6.0.
    assert harness.contract_version() == "1.6.0"


def test_scenario_registry_completeness() -> None:
    assert tuple(harness.B_SCENARIOS) == harness.EXPECTED_B_SCENARIOS
    assert len(harness.B_SCENARIOS) == 15
    assert "dsh-local-singlepoint" not in harness.B_SCENARIOS
    owners: set[str] = set()
    for scenario, spec in harness.B_SCENARIOS.items():
        assert spec.scenario == scenario
        assert spec.phases
        for phase_name, phase in spec.phases.items():
            assert phase.operation_class in harness.OPERATION_CLASSES
            assert phase.target in {"local", "remote", "either"}
            assert phase.timeout > 0
            assert "out" in phase.required_args
            if phase.controller_required:
                assert "controller" in phase.prerequisites
            owners.add(phase.owner)
    expected_owners = {
        "B04a",
        "B05",
        "B07",
        "B08",
        "B10",
        "B11",
        "B12",
        "B13",
        "B15",
        "B16",
        "B17",
        "B18",
        "B20a",
        "B20b",
        "B20c",
        "B21",
        "B22",
        "B23",
        "B24a",
        "B25b",
    }
    assert owners == expected_owners


def test_dry_run_creates_no_files(tmp_path: Path) -> None:
    run_root = tmp_path / "run-root"
    run_root.mkdir()
    marker = tmp_path / "marker"
    marker.write_text("marker\n", encoding="utf-8")
    time.sleep(0.02)
    out = run_root / "out"
    result = _run("--scenario", "env-runtime", "--dry-run", "--out", str(out))
    assert result.returncode == 0
    assert "DRY-RUN" in result.stdout
    assert out.exists() is False
    created = [
        path
        for path in run_root.rglob("*")
        if path.is_file() and path.stat().st_mtime > marker.stat().st_mtime
    ]
    assert created == []
    assert not (run_root / "acp_jobs.db").exists()


def test_missing_controller_exits_2_and_creates_no_job(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = _run("--scenario", "entry-readonly", "--out", str(out))
    assert result.returncode == 2
    assert "missing_controller" in result.stderr
    assert list(tmp_path.rglob("job.json")) == []
    summary = _read(out / "summary.json")
    assert summary["schema"] == "acp2.acceptance.summary.v1"
    run = _read(out / summary["runs"][0])
    assert run["result"] == "NOT_VERIFIED"
    assert run["reason"] == "missing_controller"


def test_mcp_stdio_without_launch_exits_3(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = _run("--scenario", "mcp-stdio", "--out", str(out))
    assert result.returncode == 3
    assert "--launch" in result.stderr
    assert list(tmp_path.rglob("job.json")) == []
    assert not out.exists()


def test_mcp_stdio_launch_without_controller_exits_2(tmp_path: Path) -> None:
    launch = tmp_path / "launch.json"
    launch.write_text("{}\n", encoding="utf-8")
    out = tmp_path / "out"
    result = _run(
        "--scenario",
        "mcp-stdio",
        "--launch",
        str(launch),
        "--out",
        str(out),
    )
    assert result.returncode == 2
    assert "missing_controller" in result.stderr
    assert list(tmp_path.rglob("job.json")) == []


def test_da_submit_without_timeout_is_usage_error(tmp_path: Path) -> None:
    completed = _run(
        "--scenario",
        "da-runtime",
        "--phase",
        "da-submit",
        "--fixture",
        str(tmp_path / "da-run.json"),
        "--out",
        str(tmp_path),
    )
    assert completed.returncode == 3
    assert "ACP2_DA_TIMEOUT" in completed.stderr
    assert "silent default" in completed.stderr
    assert not (tmp_path / "summary.json").exists()


def test_unknown_scenario_and_undeclared_phase_exit_3(tmp_path: Path) -> None:
    unknown = _run("--scenario", "not-a-scenario", "--out", str(tmp_path / "a"))
    assert unknown.returncode == 3
    undeclared = _run(
        "--scenario",
        "env-runtime",
        "--phase",
        "approval",
        "--out",
        str(tmp_path / "b"),
    )
    assert undeclared.returncode == 3
    assert "undeclared" in undeclared.stderr
    assert not (tmp_path / "a").exists()
    assert not (tmp_path / "b").exists()


def test_validate_samples_corpus() -> None:
    result = _run("--validate-samples")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SUMMARY: 30/30 sample expectations matched" in result.stdout
    assert result.stdout.count("ACCEPT") == 10
    assert result.stdout.count("REJECT") == 20


def test_sample_corpus_directly_validates() -> None:
    cases = _read(SAMPLES / "cases.json")["samples"]
    assert len(cases) == 30
    for case in cases:
        path = SAMPLES / case["path"]
        document = _read(path)
        reasons = harness.validate_receipt(
            document, verify_artifacts=True, artifact_root=path.parent
        )
        if case["expect"] == "accept":
            assert reasons == [], (case["path"], reasons)
        else:
            assert reasons, case["path"]
            assert case["expect_reason"] in reasons[0], (case["path"], reasons)


def test_platform_alias_is_normalized_and_conflict_rejected() -> None:
    sample_path = SAMPLES / "positive/b-da-submit.json"
    document = _read(sample_path)
    platform = {
        key: document.pop(key) for key in harness.PLATFORM_REF_KEYS if document.get(key) is not None
    }
    document["platform"] = platform
    assert harness.validate_receipt(document) == []

    conflict = _read(sample_path)
    conflict["platform"] = {"job_id": "job-different"}
    reasons = harness.validate_receipt(conflict)
    assert reasons[0].startswith("platform_alias_conflict:job_id")


def test_a_compatibility_fields_and_owner_prefix() -> None:
    document = _read(SAMPLES / "positive/b-env-runtime-discover.json")
    document["owner"] = "A"
    document["scenario"] = "science:demo-run"
    document["extensions"] = {
        "science.run_key": "run-key-0001",
        "science.receipt": {"kind": "a-owned"},
        "acceptance.operation_class": "standalone",
    }
    assert harness.validate_receipt(document) == []

    document["scenario"] = "env-runtime"
    reasons = harness.validate_receipt(document)
    assert "owner_scenario_prefix_mismatch:A" in reasons


def test_check_only_validates_summary_and_artifacts(tmp_path: Path) -> None:
    out = tmp_path / "out"
    stub = _run("--scenario", "package-install", "--out", str(out))
    assert stub.returncode == 2
    summary = out / "summary.json"
    valid = _run("--check-only", str(summary))
    assert valid.returncode == 0
    assert "tool check only" in valid.stdout

    positive = SAMPLES / "positive/b-artifact-read.json"
    accepted = _run("--check-only", str(positive))
    assert accepted.returncode == 0

    summary_document = _read(summary)
    summary_document["runs"].append("missing-run.json")
    missing_summary = tmp_path / "missing-summary.json"
    missing_summary.write_text(json.dumps(summary_document), encoding="utf-8")
    missing = _run("--check-only", str(missing_summary))
    assert missing.returncode == 1
    assert "summary_missing_run:missing-run.json" in missing.stderr


def test_check_only_rejects_corrupt_digest_and_missing_artifact(
    tmp_path: Path,
) -> None:
    corrupt = _run("--check-only", str(SAMPLES / "negative/corrupt-digest.json"))
    assert corrupt.returncode == 1
    assert "artifact_sha256_mismatch" in corrupt.stderr

    document = _read(SAMPLES / "positive/b-artifact-read.json")
    document["artifacts"][0]["path"] = "artifacts/does-not-exist.txt"
    missing_path = tmp_path / "missing-artifact.json"
    missing_path.write_text(json.dumps(document), encoding="utf-8")
    missing = _run("--check-only", str(missing_path))
    assert missing.returncode == 1
    assert "artifact_missing:artifacts/does-not-exist.txt" in missing.stderr


def test_timeout_cleanup_record(tmp_path: Path) -> None:
    def slow_handler(ledger: Any) -> int:
        ledger.track_pid(4242)
        ledger.track_task(job_id="job-timeout-0001", attempt=1, submission_id="sub-0001")
        ledger.track_submission("sub-0001")
        time.sleep(0.5)
        return harness.EXIT_PASS

    ledger = harness.CleanupLedger()
    code, record = harness.run_handler_with_timeout(
        slow_handler,
        0.05,
        ledger,
        tmp_path,
        scenario="env-runtime",
        phase="main",
        run_id="20261022T000000Z-timeout-0001",
    )
    assert code == harness.EXIT_NOT_VERIFIED
    assert record is not None
    assert record["timed_out"] is True
    assert record["tracked_pids"] == [4242]
    assert record["tracked_tasks"][0]["job_id"] == "job-timeout-0001"
    assert record["tracked_submissions"] == ["sub-0001"]
    assert record["started_controller"] is False
    assert record["cleanup_order"][-1].startswith(
        "stop the test Controller only if the harness started it"
    )
    written = _read(tmp_path / "timeout-cleanup.json")
    assert written == record


def test_help_dry_run_and_check_only_are_not_scenario_pass(
    tmp_path: Path,
) -> None:
    help_result = _run("--help")
    assert help_result.returncode == 0
    assert "result" not in help_result.stdout.lower()

    out = tmp_path / "dry"
    dry = _run("--scenario", "env-runtime", "--dry-run", "--out", str(out))
    assert dry.returncode == 0
    assert not out.exists()
    assert "DRY-RUN" in dry.stdout

    stub_out = tmp_path / "stub"
    stub = _run("--scenario", "package-install", "--out", str(stub_out))
    assert stub.returncode == 2
    summary = stub_out / "summary.json"
    before = summary.read_text(encoding="utf-8")
    check = _run("--check-only", str(summary))
    assert check.returncode == 0
    assert "not a scenario PASS" in check.stdout
    assert "tool check only" in check.stdout
    assert summary.read_text(encoding="utf-8") == before

    run_document = _read(stub_out / _read(summary)["runs"][0])
    assert run_document["result"] == "NOT_VERIFIED"
    # B25b implements package-install for real; without --fixture it records a
    # named prerequisite failure, not the old B01 skeleton placeholder.
    assert run_document["extensions"]["b25.reason"].startswith("fixture_missing")
    assert "b01.skeleton" not in run_document["extensions"]


def test_live_receipt_identity_comes_from_controller_health(monkeypatch: Any) -> None:
    health_revision = "a" * 40
    monkeypatch.delenv("ACP_CANDIDATE_REVISION", raising=False)
    monkeypatch.setattr(harness, "controller_reachable", lambda _controller: True)
    monkeypatch.setattr(
        harness,
        "_http_json",
        lambda _url, _timeout: {"candidate_revision": health_revision},
    )
    candidate, server, error = harness.resolve_candidate_identity(
        "live", "http://127.0.0.1:18765", 1.0
    )
    assert (candidate, server, error) == (health_revision, health_revision, None)


def test_live_receipt_identity_rejects_env_mismatch(monkeypatch: Any) -> None:
    health_revision = "a" * 40
    env_revision = "b" * 40
    monkeypatch.setenv("ACP_CANDIDATE_REVISION", env_revision)
    monkeypatch.setattr(harness, "controller_reachable", lambda _controller: True)
    monkeypatch.setattr(
        harness,
        "_http_json",
        lambda _url, _timeout: {"candidate_revision": health_revision},
    )
    candidate, server, error = harness.resolve_candidate_identity(
        "live", "http://127.0.0.1:18765", 1.0
    )
    assert (candidate, server) == (health_revision, health_revision)
    assert error == f"candidate_revision_mismatch:{env_revision}!={health_revision}"


def test_fixture_receipt_identity_keeps_frozen_baseline(monkeypatch: Any) -> None:
    monkeypatch.delenv("ACP_CANDIDATE_REVISION", raising=False)
    candidate, server, error = harness.resolve_candidate_identity("fixture", None, 1.0)
    assert candidate == harness.BASELINE_CANDIDATE_REVISION
    assert server == harness.SERVER_CANDIDATE_REVISION
    assert error is None


def _load_package_install_module() -> Any:
    script = REPO_ROOT / "scripts" / "acceptance" / "acp2_package_install.py"
    spec = importlib.util.spec_from_file_location("acp2_package_install", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_package_install_candidate_revision_comes_from_release_manifest(
    tmp_path: Path,
) -> None:
    module = _load_package_install_module()
    revision = "abec2bc4f1d302827248d36171973df5bc1bc4dd"
    (tmp_path / "release-manifest.json").write_text(
        json.dumps({"source": {"revision": revision, "clean": True}}),
        encoding="utf-8",
    )

    assert module.candidate_revision_from_release(tmp_path) == (
        revision,
        "release_manifest.source.revision",
    )
    assert (
        harness.candidate_revision_from_extensions(
            "package-install", {"b25.candidate_revision": revision}
        )
        == revision
    )
    assert (
        harness.candidate_revision_from_extensions(
            "package-install", {"b25.candidate_revision": harness.BASELINE_CANDIDATE_REVISION}
        )
        == harness.BASELINE_CANDIDATE_REVISION
    )
    assert harness.candidate_revision_from_extensions("local-singlepoint", {}) is None


def test_package_install_candidate_revision_falls_back_to_source_json(
    tmp_path: Path,
) -> None:
    module = _load_package_install_module()
    (tmp_path / "release-manifest.json").write_text("{}", encoding="utf-8")
    revision = "0123456789abcdef0123456789abcdef01234567"
    (tmp_path / "source.json").write_text(
        json.dumps({"source_revision": revision}), encoding="utf-8"
    )

    assert module.candidate_revision_from_release(tmp_path) == (
        revision,
        "source.json.source_revision",
    )


def test_package_install_candidate_revision_rejects_baseline_only(tmp_path: Path) -> None:
    module = _load_package_install_module()
    (tmp_path / "release-manifest.json").write_text(
        json.dumps({"source": {"revision": "not-a-sha"}}), encoding="utf-8"
    )
    assert module.candidate_revision_from_release(tmp_path) == (None, "unavailable")
