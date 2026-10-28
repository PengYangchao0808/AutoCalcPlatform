"""A-12b: constructed Local/LSF platform-record cross-check.

Every record produced here is **constructed** and labelled ``constructed:
true``.  The tests prove the cross-check branches (native bytes, TaskResult
body, cache lifecycle, stale rejection, attempt replacement, controller
restart, completed-vs-cancelled TaskRefs and numeric comparability).  They do
not claim a real Local or LSF run; the real rows stay ``NOT_VERIFIED`` because
the platform records, B07 deadlines and integration environment are absent.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS_PATH = REPO_ROOT / "scripts" / "acceptance" / "acp2_science.py"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "acp2" / "science" / "optfreq"
INITIAL_DIGEST = "sha256:1a21db8d08a35e5dcc961cf47ac2b688a5014dbcf8e35e7db34303ea81d376c7"
METHOD = "wB97X-D4"
VERSION = "5.0.4"
BASIS = "def2-SVP"


def _load_harness() -> Any:
    name = "acp2_science_provenance_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _task_result(
    name: str,
    *,
    method: str = METHOD,
    version: str = VERSION,
    energy: float | None = None,
) -> dict[str, Any]:
    payload = json.loads((FIXTURE_DIR / "synthetic" / name).read_text(encoding="utf-8"))
    payload["provenance"]["method"] = method
    payload["provenance"]["version"] = version
    if energy is not None:
        payload["energy_hartree"] = energy
    return payload


def _workspace(
    root: Path,
    *,
    tag: str = "a",
    method: str = METHOD,
    version: str = VERSION,
    energy: float | None = None,
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    optimize_text = (FIXTURE_DIR / "synthetic" / "optimize-minimum.out").read_text(encoding="utf-8")
    frequency_text = (FIXTURE_DIR / "synthetic" / "frequency-minimum.out").read_text(
        encoding="utf-8"
    )
    if energy is not None:
        rendered = f"{energy:.6f}"
        optimize_text = optimize_text.replace("-76.419737", rendered)
        frequency_text = frequency_text.replace("-76.419737", rendered)
    header = f"ORCA {version}\n{method}/{BASIS}\n"
    optimize_path = root / f"{tag}-optimize.out"
    frequency_path = root / f"{tag}-frequency.out"
    optimize_path.write_text(header + optimize_text, encoding="utf-8")
    frequency_path.write_text(header + frequency_text, encoding="utf-8")
    structure_path = root / f"{tag}-optimized.xyz"
    structure_path.write_bytes((FIXTURE_DIR / "optimized.xyz").read_bytes())
    return {
        "root": root,
        "method": method,
        "version": version,
        "artifacts": [
            {"role": "native_output", "path": str(optimize_path)},
            {"role": "frequency_output", "path": str(frequency_path)},
            {"role": "optimized_structure", "path": str(structure_path)},
        ],
        "task_results": [
            _task_result(
                "optimize-converged-result.json", method=method, version=version, energy=energy
            ),
            _task_result(
                "frequency-minimum-result.json", method=method, version=version, energy=energy
            ),
        ],
    }


def _completed_row(
    workspace: dict[str, Any],
    *,
    record_id: str,
    task_ref: str,
    attempt: int = 1,
    sequence: int = 1,
    cache_state: str = "cold",
    artifacts: list[dict[str, Any]] | None = None,
    task_results: list[dict[str, Any]] | None = None,
    constructed: bool = True,
    scheduler: str = "local",
    compare_with: list[str] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    artifact_rows = []
    for raw in artifacts if artifacts is not None else workspace["artifacts"]:
        entry = dict(raw)
        entry.setdefault("sha256", _sha(Path(entry["path"])))
        entry.setdefault("attempt", attempt)
        artifact_rows.append(entry)
    row: dict[str, Any] = {
        "record_id": record_id,
        "constructed": constructed,
        "scheduler": scheduler,
        "run_kind": "completed_science_run",
        "sequence": sequence,
        "task_ref": task_ref,
        "attempt": attempt,
        "terminal_state": "COMPLETED",
        "environment": {
            "source": str(workspace["root"]),
            "interpreter": sys.executable,
            "software": {"name": "ORCA", "version": workspace["version"]},
            "method": workspace["method"],
            "basis": BASIS,
            "input_sha256": INITIAL_DIGEST,
        },
        "cache": {"state": cache_state},
        "artifacts": artifact_rows,
        "task_results": (task_results if task_results is not None else workspace["task_results"]),
    }
    if compare_with is not None:
        row["compare_with"] = compare_with
    row.update(overrides)
    return row


def _cancelled_row(
    *, record_id: str, task_ref: str, attempt: int = 1, sequence: int = 1, **overrides: Any
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "record_id": record_id,
        "constructed": True,
        "scheduler": "local",
        "run_kind": "control_cancel_run",
        "sequence": sequence,
        "task_ref": task_ref,
        "attempt": attempt,
        "terminal_state": "CANCELLED",
        "environment": {
            "source": "/constructed/local",
            "interpreter": "/constructed/python",
            "software": {"name": "ORCA", "version": VERSION},
            "method": METHOD,
            "basis": BASIS,
            "input_sha256": INITIAL_DIGEST,
        },
        "cache": {"state": "cold"},
        "platform": {
            "stop_confirmation": {"confirmed": True, "observed_state": "CANCELLED"},
            "receipt": {"present": True, "sha256": "sha256:" + "0" * 64},
        },
    }
    row.update(overrides)
    return row


def _document(
    records: list[dict[str, Any]], *, constructed: bool = True, **extra: Any
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": "acp2.science.platform-run-records",
        "constructed": constructed,
        "records": records,
    }
    document.update(extra)
    return document


def _row(report: dict[str, Any], record_id: str) -> dict[str, Any]:
    return next(row for row in report["rows"] if row["record_id"] == record_id)


def test_absent_real_platform_records_are_not_verified() -> None:
    harness = _load_harness()
    absent = harness.crosscheck_local_lsf_records(None)
    assert absent["status"] == "NOT_VERIFIED"
    assert absent["real_rows"] == "NOT_VERIFIED"
    assert any("no platform Local/LSF records supplied" in gap for gap in absent["gaps"])
    empty = harness.crosscheck_local_lsf_records(
        {"schema": "acp2.science.platform-run-records", "records": []}
    )
    assert empty["status"] == "NOT_VERIFIED"
    assert empty["real_rows"] == "NOT_VERIFIED"


def test_constructed_records_are_labelled(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "label")
    document = _document([_completed_row(workspace, record_id="r1", task_ref="t1")])
    assert document["constructed"] is True
    assert document["records"][0]["constructed"] is True


def test_constructed_local_completed_row_passes_and_prints_digests(
    tmp_path: Path, capsys: Any
) -> None:
    harness = _load_harness()
    workspace = _workspace(tmp_path / "local")
    report = harness.crosscheck_local_lsf_records(
        _document([_completed_row(workspace, record_id="local-1", task_ref="task-local")])
    )
    assert report["status"] == "PASS"
    assert report["constructed"] is True
    assert report["real_rows"] == "NOT_VERIFIED"
    row = _row(report, "local-1")
    assert row["status"] == "PASS"
    assert len(row["digest_table"]) == 3
    assert row["energy_hartree"] == -76.419737
    assert row["science"]["task_results"]["frequency"]["status"] == "completed"
    assert report["timeout"]["status"] == "NOT_VERIFIED"
    assert any("B07 frozen deadlines not supplied" in gap for gap in report["timeout"]["gaps"])
    harness.print_crosscheck_report(report)
    output = capsys.readouterr().out
    assert "digest" not in output.lower()
    assert "native_output: " in output
    assert "crosscheck status: PASS" in output


def test_constructed_lsf_row_is_not_verified_with_exact_gaps(tmp_path: Path) -> None:
    harness = _load_harness()
    workspace = _workspace(tmp_path / "lsf")
    row = _completed_row(
        workspace,
        record_id="lsf-1",
        task_ref="task-lsf",
        scheduler="lsf",
        platform={"submission_id": "constructed-submission"},
    )
    report = harness.crosscheck_local_lsf_records(_document([row]))
    assert report["status"] == "NOT_VERIFIED"
    lsf = _row(report, "lsf-1")
    assert lsf["status"] == "NOT_VERIFIED"
    assert "real bsub/bjobs/submission/receipt evidence (record is constructed)" in lsf["gaps"]
    assert "frozen deadline max_queue_wait_seconds is missing" in lsf["gaps"]
    assert "frozen deadline cleanup_seconds is missing" in lsf["gaps"]
    assert lsf["status"] != "PASS"


def test_cold_cache_eviction_and_cross_attempt_replacement(tmp_path: Path) -> None:
    harness = _load_harness()
    workspace = _workspace(tmp_path / "cache")
    first = _completed_row(
        workspace, record_id="attempt-1", task_ref="task-cache", attempt=1, sequence=1
    )
    replacement = _completed_row(
        workspace,
        record_id="attempt-2",
        task_ref="task-cache",
        attempt=2,
        sequence=2,
        cache_state="evicted",
    )
    report = harness.crosscheck_local_lsf_records(_document([first, replacement]))
    assert report["status"] == "PASS"
    assert report["cache"]["cold"] == 1
    assert report["cache"]["evicted"] == 1
    assert any(
        check["name"] == "cache_eviction_rebuild" for check in _row(report, "attempt-2")["checks"]
    )
    stale_cache = _completed_row(
        workspace,
        record_id="attempt-2-warm",
        task_ref="task-cache",
        attempt=2,
        sequence=2,
        cache_state="warm",
        artifacts=[{**artifact, "attempt": 1} for artifact in workspace["artifacts"]],
    )
    rejected = harness.crosscheck_local_lsf_records(_document([first, stale_cache]))
    assert rejected["status"] == "FAIL"
    assert any(
        "cached artifact" in message and "older attempt" in message
        for message in _row(rejected, "attempt-2-warm")["contradictions"]
    )


def test_stale_out_of_order_response_is_rejected(tmp_path: Path) -> None:
    harness = _load_harness()
    workspace = _workspace(tmp_path / "stale")
    current = _completed_row(
        workspace, record_id="current", task_ref="task-stale", attempt=2, sequence=2
    )
    stale = _completed_row(
        workspace,
        record_id="stale",
        task_ref="task-stale",
        attempt=1,
        sequence=1,
        cache_state="warm",
    )
    report = harness.crosscheck_local_lsf_records(_document([current, stale]))
    assert _row(report, "stale")["status"] == "STALE_REJECTED"
    assert len(report["stale_responses"]) == 1
    assert report["stale_responses"][0]["record_id"] == "stale"
    assert report["status"] == "PASS"


def test_controller_restart_reuses_task_ref_and_digests(tmp_path: Path) -> None:
    harness = _load_harness()
    workspace = _workspace(tmp_path / "restart")
    first = _completed_row(
        workspace, record_id="before", task_ref="task-restart", attempt=1, sequence=1
    )
    restart = _completed_row(
        workspace,
        record_id="after",
        task_ref="task-restart",
        attempt=1,
        sequence=2,
        cache_state="warm",
        controller_restart_of="before",
    )
    report = harness.crosscheck_local_lsf_records(_document([first, restart]))
    assert report["status"] == "PASS"
    assert any(
        check["name"] == "controller_restart_identity" for check in _row(report, "after")["checks"]
    )
    new_ref = _completed_row(
        workspace,
        record_id="new-ref",
        task_ref="task-new-restart",
        attempt=1,
        sequence=2,
        cache_state="warm",
        controller_restart_of="before",
    )
    rejected = harness.crosscheck_local_lsf_records(_document([first, new_ref]))
    assert rejected["status"] == "FAIL"
    assert any(
        "must not create a new TaskRef" in message
        for message in _row(rejected, "new-ref")["contradictions"]
    )


def test_completed_and_cancelled_runs_use_different_task_refs(tmp_path: Path) -> None:
    harness = _load_harness()
    workspace = _workspace(tmp_path / "cancel")
    completed = _completed_row(
        workspace, record_id="science", task_ref="task-completed", sequence=1
    )
    cancelled = _cancelled_row(record_id="control", task_ref="task-cancelled", sequence=2)
    report = harness.crosscheck_local_lsf_records(_document([completed, cancelled]))
    assert report["status"] == "PASS"
    control = _row(report, "control")
    assert control["status"] == "PASS"
    assert control["control"]["requires_completed_science"] is False
    assert "task_results" not in control
    same_ref = _cancelled_row(record_id="control-same", task_ref="task-completed", sequence=2)
    rejected = harness.crosscheck_local_lsf_records(_document([completed, same_ref]))
    assert rejected["status"] == "FAIL"
    assert any(
        "must use different TaskRefs" in message
        for message in _row(rejected, "control-same")["contradictions"]
    )


def test_numeric_equality_refused_across_different_methods(tmp_path: Path) -> None:
    harness = _load_harness()
    base = _workspace(tmp_path / "base")
    other = _workspace(tmp_path / "other", tag="b", method="B3LYP")
    left = _completed_row(base, record_id="wb97", task_ref="task-wb97", sequence=1)
    right = _completed_row(
        other,
        record_id="b3lyp",
        task_ref="task-b3lyp",
        sequence=1,
        compare_with=["wb97"],
    )
    report = harness.crosscheck_local_lsf_records(_document([left, right]))
    assert report["status"] == "FAIL"
    assert any(
        "numeric equality across differing methods/versions is refused" in message
        for message in _row(report, "b3lyp")["contradictions"]
    )
    assert all(
        comparison["status"] != "equal"
        for comparison in report["numeric_comparisons"]
        if {comparison["left"], comparison["right"]} == {"wb97", "b3lyp"}
    )


def test_same_environment_comparison_uses_frozen_tolerance(tmp_path: Path) -> None:
    harness = _load_harness()
    workspace = _workspace(tmp_path / "same")
    left = _completed_row(workspace, record_id="a", task_ref="task-a", sequence=1)
    same = _completed_row(
        workspace,
        record_id="b",
        task_ref="task-b",
        sequence=1,
        compare_with=["a"],
    )
    report = harness.crosscheck_local_lsf_records(_document([left, same]))
    assert report["status"] == "PASS"
    comparison = next(
        item
        for item in report["numeric_comparisons"]
        if {item["left"], item["right"]} == {"a", "b"}
    )
    assert comparison["status"] == "equal"
    assert comparison["delta_hartree"] <= comparison["tolerance_hartree"]
    shifted = _workspace(tmp_path / "same", tag="c", energy=-76.400000)
    differing = _completed_row(
        shifted,
        record_id="c",
        task_ref="task-c",
        sequence=1,
        compare_with=["a"],
    )
    rejected = harness.crosscheck_local_lsf_records(_document([left, differing]))
    assert rejected["status"] == "FAIL"
    assert any(
        "differ beyond the frozen tolerance" in message
        for message in _row(rejected, "c")["contradictions"]
    )


def test_live_integrated_record_requires_integration_environment(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = _load_harness()
    workspace = _workspace(tmp_path / "live")
    live = _completed_row(
        workspace,
        record_id="live",
        task_ref="task-live",
        constructed=False,
    )
    document = _document(
        [live],
        constructed=False,
        server_revision="server-abc",
        client_revision="client-def",
    )
    monkeypatch.delenv("ACP2_INTEGRATION_SOURCE", raising=False)
    monkeypatch.delenv("ACP2_INTEGRATION_PYTHON", raising=False)
    absent = harness.crosscheck_local_lsf_records(document)
    row = _row(absent, "live")
    assert absent["status"] == "NOT_VERIFIED"
    assert absent["revisions"]["server_revision"] == "server-abc"
    assert absent["revisions"]["client_revision"] == "client-def"
    assert len(absent["revisions"]["harness_revision"]) == 64
    assert "$ACP2_INTEGRATION_SOURCE is not set" in row["gaps"]
    assert "$ACP2_INTEGRATION_PYTHON is not set" in row["gaps"]
    monkeypatch.setenv("ACP2_INTEGRATION_SOURCE", str(tmp_path / "integration-source"))
    monkeypatch.setenv("ACP2_INTEGRATION_PYTHON", str(tmp_path / "integration-python"))
    present = harness.crosscheck_local_lsf_records(document)
    assert not any("is not set" in gap for gap in _row(present, "live")["gaps"])
    assert _row(present, "live")["status"] == "PASS"
    monkeypatch.setenv("ACP2_INTEGRATION_SOURCE", str(REPO_ROOT))
    monkeypatch.setenv("ACP2_INTEGRATION_PYTHON", sys.executable)
    monkeypatch.setenv("ACP2_A_SOURCE", str(REPO_ROOT))
    monkeypatch.setenv("ACP2_A_PYTHON", sys.executable)
    same = harness.crosscheck_local_lsf_records(document)
    assert any("must be distinct" in gap for gap in _row(same, "live")["gaps"])


def test_derive_timeout_from_frozen_and_execution_wiring() -> None:
    harness = _load_harness()
    derived = harness.derive_timeout_from_frozen(
        {
            "max_queue_wait_seconds": 10,
            "max_run_seconds": 20,
            "max_prepare_seconds": 5,
            "cleanup_seconds": 3,
        },
        client_request_seconds=2,
    )
    assert derived["status"] == "OK"
    assert derived["timeout_seconds"] == 38
    assert derived["deadlines"]["queue_wait_seconds"] == 10
    assert derived["deadlines"]["scientific_runtime_seconds"] == 20
    assert derived["deadlines"]["client_request_seconds"] == 2
    assert derived["deadlines"]["overall_acceptance_seconds"] == 38
    missing = harness.derive_timeout_from_frozen(
        {"max_queue_wait_seconds": 10, "max_run_seconds": 20, "max_prepare_seconds": 5}
    )
    assert missing["status"] == "NOT_VERIFIED"
    assert missing["timeout_seconds"] is None
    assert "frozen deadline cleanup_seconds is missing" in missing["gaps"]
    absent = harness.derive_timeout_from_frozen(None)
    assert absent["status"] == "NOT_VERIFIED"
    assert absent["timeout_seconds"] is None
    malformed = harness.derive_timeout_from_frozen(
        {
            "max_queue_wait_seconds": 10,
            "max_run_seconds": True,
            "max_prepare_seconds": 5,
            "cleanup_seconds": 3,
        }
    )
    assert malformed["status"] == "FAIL"
    args = argparse.Namespace(timeout=1800.0)
    run_spec = {
        "frozen_deadlines": {
            "max_queue_wait_seconds": 10,
            "max_run_seconds": 20,
            "max_prepare_seconds": 5,
            "cleanup_seconds": 3,
        }
    }
    assert harness._execution_timeout(args, run_spec) == 38
    assert harness._execution_timeout(args, {}) == 1800.0
    malformed_spec = {
        "frozen_deadlines": {
            "max_queue_wait_seconds": 10,
            "max_run_seconds": True,
            "max_prepare_seconds": 5,
            "cleanup_seconds": 3,
        }
    }
    assert harness._execution_timeout(args, malformed_spec) == 0.0


def test_digest_mismatch_and_missing_body_are_rejected(tmp_path: Path) -> None:
    harness = _load_harness()
    workspace = _workspace(tmp_path / "digest")
    wrong_digest = [
        {**artifact, "sha256": "sha256:" + "f" * 64} for artifact in workspace["artifacts"]
    ]
    row = _completed_row(
        workspace, record_id="bad-digest", task_ref="task-bad", artifacts=wrong_digest
    )
    report = harness.crosscheck_local_lsf_records(_document([row]))
    assert report["status"] == "FAIL"
    assert any(
        "SHA-256 mismatch" in message for message in _row(report, "bad-digest")["contradictions"]
    )
    incomplete = _completed_row(
        workspace,
        record_id="no-frequency-result",
        task_ref="task-incomplete",
        task_results=[workspace["task_results"][0]],
    )
    incomplete_report = harness.crosscheck_local_lsf_records(_document([incomplete]))
    assert incomplete_report["status"] == "NOT_VERIFIED"
    assert any(
        "frequency TaskResult" in gap
        for gap in _row(incomplete_report, "no-frequency-result")["gaps"]
    )
    no_native = _completed_row(
        workspace,
        record_id="no-native",
        task_ref="task-no-native",
        artifacts=[
            artifact for artifact in workspace["artifacts"] if artifact["role"] != "native_output"
        ],
    )
    native_report = harness.crosscheck_local_lsf_records(_document([no_native]))
    assert native_report["status"] == "NOT_VERIFIED"
    assert any("native_output" in gap for gap in _row(native_report, "no-native")["gaps"])
