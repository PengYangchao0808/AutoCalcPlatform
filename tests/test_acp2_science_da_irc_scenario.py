"""A-18: the ``science:validate-da-irc`` scenario.

Every snapshot, registry, IRC TaskResult, endpoint reference and bond-change
record here is **constructed**.  The success test proves the code path
(platform registry lookup -> B20a-exec provenance -> frozen fixture -> TS
identity -> IRC completion -> endpoint mapping -> H4 separation) without
claiming a real IRC run.  The one-sided test proves the honest ``inconclusive``
claim and the open H4 state.  No QC/LSF job, controller or production root is
touched.
"""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
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
ARTIFACTS = SCENARIO_DIR / "artifacts"
SUCCESS_SNAPSHOT = SCENARIO_DIR / "constructed-snapshot-irc-success.json"
ONE_SIDED_SNAPSHOT = SCENARIO_DIR / "constructed-snapshot-irc-one-sided.json"
OLD_ATTEMPT_SNAPSHOT = SCENARIO_DIR / "constructed-snapshot-irc-old-attempt.json"
REGISTRY = SCENARIO_DIR / "constructed-platform-registry.json"
SCENARIO_MANIFEST = SCENARIO_DIR / "fixture-manifest.json"


def _load_harness() -> Any:
    import importlib.util

    name = "acp2_science_harness_da_irc_under_test"
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
        "science:validate-da-irc",
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


def _materialize(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    root = tmp_path / "root"
    shutil.copytree(ARTIFACTS, root)
    payload = _success_wrapper()
    payload["snapshot"]["read_root"] = str(root)
    return payload, root


def _update_artifact_digest(payload: dict[str, Any], relative: str, digest: str) -> None:
    for artifact in payload["snapshot"]["artifacts"]:
        if artifact["path"] == relative:
            artifact["sha256"] = digest
            return
    raise AssertionError(f"artifact {relative!r} is absent from the snapshot")


def test_constructed_irc_fixtures_are_explicitly_labelled() -> None:
    manifest = json.loads(SCENARIO_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["constructed"] is True
    assert manifest["real_run"] is False
    assert "irc_success_snapshot" in manifest["fixture_files"]
    assert "irc_one_sided_snapshot" in manifest["fixture_files"]
    assert "irc_old_attempt_snapshot" in manifest["fixture_files"]
    for relative, digest in manifest["file_digests"].items():
        assert hashlib.sha256((SCENARIO_DIR / relative).read_bytes()).hexdigest() == digest

    success = _success_wrapper()
    one_sided = json.loads(ONE_SIDED_SNAPSHOT.read_text(encoding="utf-8"))
    assert success["schema"] == "acp2.science.constructed-snapshot"
    assert one_sided["schema"] == "acp2.science.constructed-snapshot"
    execution = success["snapshot"]["platform"]["execution"]
    assert execution["producer"] == "B20a-exec"
    assert execution["artifact_id"] == "B20a-exec:da-execution-artifacts"
    assert execution["sub_deliverable"] == "22b"
    assert execution["semantic_candidate"] == "B.B20a.exec"
    assert execution["b20a_ready"]["cited_as_execution_evidence"] is False
    receipt_steps = {receipt["step_id"] for receipt in execution["step_receipts"]}
    assert {"ts_optimize", "ts_frequency", "irc", "endpoint_assignment"} <= receipt_steps

    selection = json.loads((ARTIFACTS / "irc" / "irc-selection.json").read_text(encoding="utf-8"))
    assert selection["schema"] == "acp2.science.da-irc-selection"
    assert selection["endpoint_optimization_performed"] is False
    assert selection["bond_change_count"] == 6
    assert selection["expected_path"] == "two_direction"
    assert selection["bond_changes"]
    one_selection = json.loads(
        (ARTIFACTS / "irc" / "irc-selection-one-sided.json").read_text(encoding="utf-8")
    )
    assert one_selection["expected_path"] == "one_sided"
    assert one_selection["endpoint_optimization_performed"] is False


def test_two_direction_irc_is_supported_and_prints_both_endpoint_mappings(
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
    assert science["connectivity_status"] == "supported"
    assert science["constructed_fixture"] is True
    assert science["real_run"] is False
    assert science["ts_validation"]["status"] == "supported"
    assert science["irc_completion"]["status"] == "supported"
    assert science["direction_assignment"] == "swapped"
    assert science["forward_endpoint"]["target_endpoint_id"] == "product"
    assert science["reverse_endpoint"]["target_endpoint_id"] == "reactant"
    assert len(science["forward_endpoint"]["atom_mapping"]) == 16
    assert len(science["reverse_endpoint"]["atom_mapping"]) == 16
    assert len(science["effective_atom_mapping"]) == 16
    assert science["endpoint_optimization"] == {
        "performed": False,
        "endpoints_may_not_be_minima": True,
        "note": "No endpoint optimization was performed; both endpoints may not be minima.",
    }
    assert len(science["bond_changes"]) == 6
    assert science["b20a_exec_artifact_id"] == "B20a-exec:da-execution-artifacts"
    assert science["b20a_exec_sub_deliverable"] == "22b"
    assert science["b20a_exec_semantic_candidate"] == "B.B20a.exec"
    assert science["b20a_ready_cited_as_execution_evidence"] is False
    assert science["example_ts_substituted"] is False
    assert science["second_da_job_started"] is False
    assert science["h4"]["closed"] is False
    assert science["h4"]["status"] == "open"
    assert science["h4"]["endpoints_may_not_be_minima"] is True
    rows = {row["row_id"]: row for row in science["h4"]["rows"]}
    assert rows["H4-02-bidirectional-irc"]["claim_status"] == "supported"
    assert rows["H4-02-bidirectional-irc"]["state"] == "open"
    assert rows["H4-03-endpoint-connectivity"]["claim_status"] == "supported"
    assert rows["H4-04-comparable-energy"]["scenario_result"] == "NOT_VERIFIED"

    assert "scenario result: PASS" in captured
    assert "claim status: supported" in captured
    assert "H4 readiness: open (closed=False)" in captured
    assert "IRC completion: supported" in captured
    assert "forward endpoint" in captured
    assert "reverse endpoint" in captured
    assert "endpoints_may_not_be_minima" in captured

    for name in (
        "fixture-validation.json",
        "irc-selection.json",
        "irc-reference.json",
        "irc-completion.json",
        "irc-connectivity.json",
        "scientific-claim.json",
        "h4-readiness.json",
        "artifact-digests.json",
    ):
        assert (tmp_path / "evidence" / name).is_file(), name


def test_one_sided_irc_is_inconclusive_and_keeps_h4_open(tmp_path: Path, capsys: Any) -> None:
    assert _invoke(tmp_path, snapshot=ONE_SIDED_SNAPSHOT) == 0
    captured = capsys.readouterr().out
    record = _record(tmp_path)
    science = _science(tmp_path)

    assert record["result"] == "PASS"
    assert record["exit_code"] == 0
    assert science["scenario_result"] == "PASS"
    assert science["claim_status"] == "inconclusive"
    assert science["connectivity_status"] == "inconclusive"
    assert science["irc_completion"]["status"] == "inconclusive"
    assert science["reverse_endpoint"] is None
    assert science["direction_assignment"] is None
    assert science["h4"]["closed"] is False
    assert science["h4"]["status"] == "open"
    rows = {row["row_id"]: row for row in science["h4"]["rows"]}
    assert rows["H4-02-bidirectional-irc"]["claim_status"] == "inconclusive"
    assert rows["H4-02-bidirectional-irc"]["state"] == "open"
    assert "claim status: inconclusive" in captured
    assert "H4 readiness: open (closed=False)" in captured
    assert "endpoints_may_not_be_minima" in captured


def test_real_manifest_absent_is_not_verified(tmp_path: Path) -> None:
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


def test_missing_irc_result_is_not_verified(tmp_path: Path) -> None:
    payload = _success_wrapper()
    payload["snapshot"]["artifacts"] = [
        artifact
        for artifact in payload["snapshot"]["artifacts"]
        if artifact["path"] != "irc/irc-result.json"
    ]
    manifest = _write_json(tmp_path / "snapshot.json", payload)
    assert _invoke(tmp_path, snapshot=manifest) == 2
    record = _record(tmp_path)
    assert record["result"] == "NOT_VERIFIED"
    assert "irc/irc-result.json" in record["reason"]
    assert record["extensions"]["science"]["claim_status"] is None


def test_missing_irc_reference_is_not_verified(tmp_path: Path) -> None:
    payload = _success_wrapper()
    payload["snapshot"]["artifacts"] = [
        artifact
        for artifact in payload["snapshot"]["artifacts"]
        if artifact["path"] != "irc/irc-reference.json"
    ]
    manifest = _write_json(tmp_path / "snapshot.json", payload)
    assert _invoke(tmp_path, snapshot=manifest) == 2
    record = _record(tmp_path)
    assert record["result"] == "NOT_VERIFIED"
    assert "irc/irc-reference.json" in record["reason"]


def test_old_attempt_fixture_is_rejected(tmp_path: Path) -> None:
    assert _invoke(tmp_path, snapshot=OLD_ATTEMPT_SNAPSHOT) == 1
    record = _record(tmp_path)
    assert record["result"] == "FAIL"
    assert "old-attempt" in record["reason"]


def test_old_attempt_selection_is_rejected(tmp_path: Path) -> None:
    payload, root = _materialize(tmp_path)
    selection_path = root / "irc" / "irc-selection.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selection["attempt"] = 2
    _write_json(selection_path, selection)
    _update_artifact_digest(payload, "irc/irc-selection.json", _sha(selection_path))
    manifest = _write_json(tmp_path / "snapshot.json", payload)

    assert _invoke(tmp_path, snapshot=manifest) == 1
    record = _record(tmp_path)
    assert record["result"] == "FAIL"
    assert "old-attempt" in record["reason"]


def test_irc_method_mismatch_is_rejected(tmp_path: Path) -> None:
    payload, root = _materialize(tmp_path)
    result_path = root / "irc" / "irc-result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["provenance"]["method"] = "B3LYP"
    _write_json(result_path, result)
    result_sha = _sha(result_path)
    selection_path = root / "irc" / "irc-selection.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selection["irc_provenance_sha256"] = result_sha
    _write_json(selection_path, selection)
    _update_artifact_digest(payload, "irc/irc-result.json", result_sha)
    _update_artifact_digest(payload, "irc/irc-selection.json", _sha(selection_path))
    manifest = _write_json(tmp_path / "snapshot.json", payload)

    assert _invoke(tmp_path, snapshot=manifest) == 1
    record = _record(tmp_path)
    assert record["result"] == "FAIL"
    assert "IRC method" in record["reason"]
    assert "differs from the snapshot method" in record["reason"]


def test_b20a_ready_citation_is_fail(tmp_path: Path) -> None:
    payload = _success_wrapper()
    payload["snapshot"]["platform"]["execution"]["b20a_ready"]["cited_as_execution_evidence"] = True
    manifest = _write_json(tmp_path / "snapshot.json", payload)
    assert _invoke(tmp_path, snapshot=manifest) == 1
    record = _record(tmp_path)
    assert record["result"] == "FAIL"
    assert "b20a_ready" in record["reason"]


def test_harness_does_not_run_a_second_job_or_substitute_example() -> None:
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
