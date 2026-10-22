"""A-17: the ``science:validate-da-ts`` scenario.

Every snapshot, registry, TaskResult and geometry here is **constructed**.
The success test proves the code path (platform registry lookup -> B20a-exec
provenance -> frozen fixture/budget checks -> snapshot content verification ->
candidate selection -> TS/frequency validators) without claiming a real QC run.
The real row stays ``NOT_VERIFIED`` because the platform ``da-runtime`` /
``da-research-live`` registry and the B20a-exec artifacts do not exist on this
host.  H4 remains open even when the constructed saddle claim is supported.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS_PATH = REPO_ROOT / "scripts" / "acceptance" / "acp2_science.py"
FIXTURE_MANIFEST = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "acp2"
    / "science"
    / "diels_alder"
    / "v1"
    / "fixture-manifest.json"
)
SCENARIO_DIR = FIXTURE_MANIFEST.parent / "ts-scenario"
SUCCESS_SNAPSHOT = SCENARIO_DIR / "constructed-snapshot-success.json"
NO_CANDIDATE_SNAPSHOT = SCENARIO_DIR / "constructed-snapshot-no-candidate.json"
OLD_ATTEMPT_SNAPSHOT = SCENARIO_DIR / "constructed-snapshot-old-attempt.json"
REGISTRY = SCENARIO_DIR / "constructed-platform-registry.json"
SCENARIO_MANIFEST = SCENARIO_DIR / "fixture-manifest.json"


def _load_harness() -> Any:
    import importlib.util

    name = "acp2_science_harness_da_ts_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _invoke(
    tmp_path: Path,
    *,
    snapshot: Any = SUCCESS_SNAPSHOT,
    registry: Any = REGISTRY,
    input_manifest: Any = None,
    out_name: str = "evidence",
) -> int:
    harness = _load_harness()
    argv = [
        "--scenario",
        "science:validate-da-ts",
        "--fixture",
        str(FIXTURE_MANIFEST),
        "--input-manifest",
        str(snapshot) if input_manifest is None else str(input_manifest),
        "--out",
        str(tmp_path / out_name),
        "--timeout",
        "300",
    ]
    if registry is not None:
        argv.extend(["--platform-registry", str(registry)])
    return int(harness.main(argv))


def _record(tmp_path: Path, out_name: str = "evidence") -> dict[str, Any]:
    return json.loads((tmp_path / out_name / "result.json").read_text(encoding="utf-8"))


def _science(tmp_path: Path, out_name: str = "evidence") -> dict[str, Any]:
    return _record(tmp_path, out_name)["extensions"]["science"]


def _write_json(path: Path, payload: Any) -> Path:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _success_wrapper() -> dict[str, Any]:
    payload = json.loads(SUCCESS_SNAPSHOT.read_text(encoding="utf-8"))
    assert payload["constructed"] is True
    return payload


def _no_candidate_wrapper() -> dict[str, Any]:
    payload = json.loads(NO_CANDIDATE_SNAPSHOT.read_text(encoding="utf-8"))
    assert payload["constructed"] is True
    return payload


def _write_registry(tmp_path: Path, ids: list[str]) -> Path:
    return _write_json(
        tmp_path / "registry.json",
        {
            "schema": "acp2.science.platform-registry",
            "schema_version": 1,
            "constructed": True,
            "entries": [{"id": item, "constructed": True} for item in ids],
        },
    )


def test_constructed_fixtures_are_explicitly_labelled() -> None:
    manifest = json.loads(SCENARIO_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["constructed"] is True
    assert manifest["real_run"] is False
    for relative, digest in manifest["file_digests"].items():
        import hashlib

        assert hashlib.sha256((SCENARIO_DIR / relative).read_bytes()).hexdigest() == digest

    success = _success_wrapper()
    no_candidate = _no_candidate_wrapper()
    assert success["schema"] == "acp2.science.constructed-snapshot"
    assert no_candidate["schema"] == "acp2.science.constructed-snapshot"
    execution = success["snapshot"]["platform"]["execution"]
    assert execution["producer"] == "B20a-exec"
    assert execution["artifact_id"] == "B20a-exec:da-execution-artifacts"
    assert execution["sub_deliverable"] == "22b"
    assert execution["semantic_candidate"] == "B.B20a.exec"
    assert execution["b20a_ready"]["cited_as_execution_evidence"] is False

    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert registry["constructed"] is True
    assert [entry["id"] for entry in registry["entries"]] == [
        "da-runtime",
        "da-research-live",
    ]


def test_constructed_qualified_candidate_separates_scenario_claim_and_h4(
    tmp_path: Path, capsys: Any
) -> None:
    assert _invoke(tmp_path) == 0
    captured = capsys.readouterr().out
    record = _record(tmp_path)
    science = _science(tmp_path)

    assert record["result"] == "PASS"
    assert record["exit_code"] == 0
    assert science["scenario_result"] == "PASS"
    assert science["claim_status"] == "supported"
    assert science["connectivity_status"] == "inconclusive"
    assert science["constructed_fixture"] is True
    assert science["real_run"] is False
    assert science["no_candidate"] is False
    assert science["selected_candidate_id"] == "cand-001"
    assert science["starting_structure_source"] == "frozen_known_candidate"
    assert science["search_capability_claim"] == "not_claimed"
    assert science["b20a_exec_artifact_id"] == "B20a-exec:da-execution-artifacts"
    assert science["b20a_exec_sub_deliverable"] == "22b"
    assert science["b20a_exec_semantic_candidate"] == "B.B20a.exec"
    assert science["b20a_ready_cited_as_execution_evidence"] is False
    assert science["example_ts_substituted"] is False
    assert science["second_da_job_started"] is False

    claim = science["scientific_claim"]
    assert claim["claim_id"] == "c-ts-first-order-saddle-candidate"
    assert claim["status"] == "supported"
    assert claim["classification"] == "first_order_saddle_candidate"
    assert claim["imaginary_mode_count"] == 1
    assert claim["mapped_mode_index"] == 6
    assert claim["connectivity_status"] == "inconclusive"

    h4 = science["h4"]
    assert h4["closed"] is False
    assert h4["status"] == "open"
    assert h4["positive_evidence"] is False
    rows = {row["row_id"]: row for row in h4["rows"]}
    assert rows["H4-01-valid-ts"]["scenario_result"] == "PASS"
    assert rows["H4-01-valid-ts"]["claim_status"] == "supported"
    assert rows["H4-01-valid-ts"]["state"] == "open"
    assert rows["H4-02-bidirectional-irc"]["scenario_result"] == "NOT_VERIFIED"
    assert rows["H4-03-endpoint-connectivity"]["claim_status"] == "inconclusive"
    assert rows["H4-04-comparable-energy"]["scenario_result"] == "NOT_VERIFIED"
    assert rows["H4-05-honest-negatives"]["claim_status"] == "not_evaluated"

    assert science["candidate_records"] == [
        {
            "candidate_id": "cand-001",
            "source": "frozen_known_candidate",
            "status": "selected",
            "reason": (
                "selected for TS optimization and frequency validation; this proves the "
                "execution/validation chain, not research search capability"
            ),
        },
        {
            "candidate_id": "cand-002",
            "source": "generated_candidate",
            "status": "rejected",
            "reason": (
                "rejected before submission: the frozen cap max_ts_candidates=1 is already "
                "consumed by cand-001"
            ),
        },
    ]
    assert science["search_range"]["max_candidate_paths"] == 1
    assert science["search_range"]["max_ts_candidates"] == 1
    assert science["search_range"]["max_conformers_per_endpoint"] == 1
    assert science["fixture_validation"]["status"] == "PASS"
    assert science["fixture_validation"]["geometry"]["reactant_atoms"] == 16
    assert science["fixture_validation"]["budget"]["small_budget"] is True

    for name in (
        "fixture-validation.json",
        "candidate-selection.json",
        "ts-validation-report.json",
        "scientific-claim.json",
        "h4-readiness.json",
        "artifact-digests.json",
    ):
        assert (tmp_path / "evidence" / name).is_file(), name

    assert "scenario result: PASS" in captured
    assert "claim status: supported" in captured
    assert "H4 readiness: open (closed=False)" in captured
    assert "connectivity: inconclusive" in captured


def test_constructed_no_candidate_path_is_inconclusive_and_keeps_h4_open(
    tmp_path: Path, capsys: Any
) -> None:
    assert _invoke(tmp_path, snapshot=NO_CANDIDATE_SNAPSHOT) == 0
    captured = capsys.readouterr().out
    record = _record(tmp_path)
    science = _science(tmp_path)

    assert record["result"] == "PASS"
    assert record["exit_code"] == 0
    assert science["scenario_result"] == "PASS"
    assert science["claim_status"] == "inconclusive"
    assert science["no_candidate"] is True
    assert science["selected_candidate_id"] is None
    assert science["connectivity_status"] == "not_evaluated"
    assert science["h4"]["closed"] is False
    assert science["h4"]["status"] == "open"
    assert science["scientific_claim"]["status"] == "inconclusive"
    assert [candidate["status"] for candidate in science["candidate_records"]] == [
        "rejected",
        "rejected",
    ]
    assert all(candidate["reason"] for candidate in science["candidate_records"])
    assert "claim status: inconclusive" in captured
    assert "H4 readiness: open (closed=False)" in captured


def test_missing_input_manifest_is_not_verified(tmp_path: Path) -> None:
    assert _invoke(tmp_path, input_manifest="") == 2
    record = _record(tmp_path)
    assert record["result"] == "NOT_VERIFIED"
    assert "authorized input manifest path is empty" in record["reason"]


def test_missing_platform_registry_is_not_verified(tmp_path: Path) -> None:
    assert _invoke(tmp_path, registry=None) == 2
    record = _record(tmp_path)
    science = _science(tmp_path)
    assert record["result"] == "NOT_VERIFIED"
    assert "da-runtime" in record["reason"]
    assert "da-research-live" in record["reason"]
    assert science["platform_registry"]["status"] == "MISSING"
    assert science["platform_registry"]["registered"] is False


def test_registry_missing_one_required_id_is_not_verified(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path, ["da-runtime"])
    assert _invoke(tmp_path, registry=registry) == 2
    record = _record(tmp_path)
    assert record["result"] == "NOT_VERIFIED"
    assert "da-research-live" in record["reason"]
    assert record["extensions"]["science"]["platform_registry"]["status"] == "UNREGISTERED"


def test_old_attempt_selection_is_rejected(tmp_path: Path) -> None:
    assert _invoke(tmp_path, snapshot=OLD_ATTEMPT_SNAPSHOT) == 1
    record = _record(tmp_path)
    assert record["result"] == "FAIL"
    assert record["exit_code"] == 1
    assert "old-attempt" in record["reason"]


def test_snapshot_digest_mismatch_is_fail_not_inconclusive(tmp_path: Path) -> None:
    payload = _success_wrapper()
    for artifact in payload["snapshot"]["artifacts"]:
        if artifact["role"] == "optimized_structure":
            artifact["sha256"] = "sha256:" + "0" * 64
    manifest = _write_json(tmp_path / "snapshot.json", payload)

    assert _invoke(tmp_path, snapshot=manifest) == 1
    record = _record(tmp_path)
    assert record["result"] == "FAIL"
    assert "snapshot content rejected" in record["reason"]
    assert record["extensions"]["science"]["snapshot_error"]["type"].startswith("Artifact")


def test_b20a_ready_cannot_be_cited_as_execution_evidence(tmp_path: Path) -> None:
    payload = _success_wrapper()
    execution = payload["snapshot"]["platform"]["execution"]
    execution["b20a_ready"]["cited_as_execution_evidence"] = True
    manifest = _write_json(tmp_path / "snapshot.json", payload)

    assert _invoke(tmp_path, snapshot=manifest) == 1
    record = _record(tmp_path)
    assert record["result"] == "FAIL"
    assert "b20a_ready" in record["reason"]
    assert "cited as execution evidence" in record["reason"]


def test_b20a_ready_only_producer_is_not_execution_evidence(tmp_path: Path) -> None:
    payload = _success_wrapper()
    execution = payload["snapshot"]["platform"]["execution"]
    execution["producer"] = "B20a-ready"
    execution["artifact_id"] = "B20a-ready:da-execution-interface-preflight"
    manifest = _write_json(tmp_path / "snapshot.json", payload)

    assert _invoke(tmp_path, snapshot=manifest) == 1
    record = _record(tmp_path)
    assert record["result"] == "FAIL"
    assert "B20a-ready is the execution interface" in record["reason"]


def test_missing_frequency_provenance_is_not_verified(tmp_path: Path) -> None:
    payload = _success_wrapper()
    payload["snapshot"]["artifacts"] = [
        artifact
        for artifact in payload["snapshot"]["artifacts"]
        if artifact["path"] != "ts-frequency-result.json"
    ]
    manifest = _write_json(tmp_path / "snapshot.json", payload)

    assert _invoke(tmp_path, snapshot=manifest) == 2
    record = _record(tmp_path)
    assert record["result"] != "PASS"
    assert record["extensions"]["science"]["claim_status"] is None
    assert record["result"] in {"NOT_VERIFIED", "FAIL"}


def test_candidate_range_exceeding_the_frozen_cap_is_fail(tmp_path: Path) -> None:
    payload = _success_wrapper()
    # The selection record is an artifact; modify it in a temporary read root.
    source = SCENARIO_DIR / "artifacts"
    root = tmp_path / "root"
    root.mkdir()
    for path in source.iterdir():
        if path.is_file():
            (root / path.name).write_bytes(path.read_bytes())
    selection = json.loads((root / "candidate-selection.json").read_text(encoding="utf-8"))
    selection["search_range"]["max_ts_candidates"] = 2
    (root / "candidate-selection.json").write_text(
        json.dumps(selection, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    import hashlib

    selection_sha = (
        "sha256:" + hashlib.sha256((root / "candidate-selection.json").read_bytes()).hexdigest()
    )
    for artifact in payload["snapshot"]["artifacts"]:
        if artifact["path"] == "candidate-selection.json":
            artifact["sha256"] = selection_sha
    payload["snapshot"]["read_root"] = str(root)
    manifest = _write_json(tmp_path / "snapshot.json", payload)

    assert _invoke(tmp_path, snapshot=manifest) == 1
    record = _record(tmp_path)
    assert record["result"] == "FAIL"
    assert "exceeds the frozen cap" in record["reason"]


def test_harness_does_not_start_a_second_job_or_substitute_an_example() -> None:
    source = HARNESS_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "cccp" not in imported
    assert "acp" not in imported
    assert "acp_services" not in imported
    assert "subprocess" not in imported
    for forbidden in ("acp.scheduler", "run_irc", "run_optimize"):
        assert forbidden not in source
    assert 'second_da_job_started": False' in source
    assert 'example_ts_substituted": False' in source


def test_snapshot_method_mismatch_against_the_fixture_is_fail(tmp_path: Path) -> None:
    payload = _success_wrapper()
    payload["snapshot"]["scientific_identity"]["method"] = "B3LYP"
    manifest = _write_json(tmp_path / "snapshot.json", payload)

    assert _invoke(tmp_path, snapshot=manifest) == 1
    record = _record(tmp_path)
    assert record["result"] == "FAIL"
    assert "differs from the frozen fixture method" in record["reason"]
    assert record["extensions"]["science"]["snapshot_fixture_identity"]["status"] == "FAIL"


def test_failed_b20a_exec_step_receipt_is_fail(tmp_path: Path) -> None:
    payload = _success_wrapper()
    payload["snapshot"]["platform"]["execution"]["step_receipts"][0]["status"] = "failed"
    manifest = _write_json(tmp_path / "snapshot.json", payload)

    assert _invoke(tmp_path, snapshot=manifest) == 1
    record = _record(tmp_path)
    assert record["result"] == "FAIL"
    assert "step receipt" in record["reason"]
    assert "not 'completed'" in record["reason"]
