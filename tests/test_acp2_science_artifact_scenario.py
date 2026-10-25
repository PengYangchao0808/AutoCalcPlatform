"""A-11b: the shared ``science:validate-artifacts`` scenario.

Every snapshot, registry and native output here is **constructed**.  The
success test proves the code path (platform registry lookup -> snapshot content
verification -> stationary-point validators) without claiming a real QC run;
the real row stays ``NOT_VERIFIED`` because the platform registry and the
authorized ``dsh-optfreq-live`` snapshot do not exist on this host.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS_PATH = REPO_ROOT / "scripts" / "acceptance" / "acp2_science.py"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "acp2" / "science" / "optfreq"
FIXTURE_MANIFEST = FIXTURE_DIR / "fixture-manifest.json"
CONSTRUCTED_SNAPSHOT = FIXTURE_DIR / "constructed-snapshot.json"
CONSTRUCTED_REGISTRY = FIXTURE_DIR / "constructed-platform-registry.json"
WRONG_STRUCTURE_FREQUENCY_SHA256 = (
    "sha256:4bb08eff4ace19885fa49cc8bddc57313779f61c44d51b437f2038aec8907cbf"
)


def _load_harness() -> Any:
    name = "acp2_science_harness_under_test"
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
    manifest: Any = CONSTRUCTED_SNAPSHOT,
    registry: Any = CONSTRUCTED_REGISTRY,
    out_name: str = "evidence",
) -> int:
    harness = _load_harness()
    argv = [
        "--scenario",
        "science:validate-artifacts",
        "--fixture",
        str(FIXTURE_MANIFEST),
        "--input-manifest",
        str(manifest),
        "--out",
        str(tmp_path / out_name),
    ]
    if registry is not None:
        argv.extend(["--platform-registry", str(registry)])
    return int(harness.main(argv))


def _record(tmp_path: Path, out_name: str = "evidence") -> dict[str, Any]:
    return json.loads((tmp_path / out_name / "result.json").read_text(encoding="utf-8"))


def _snapshot_copy(tmp_path: Path) -> dict[str, Any]:
    payload = json.loads(CONSTRUCTED_SNAPSHOT.read_text(encoding="utf-8"))
    assert payload["constructed"] is True
    return payload


def _write_snapshot(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _write_registry(tmp_path: Path, entries: list[dict[str, Any]]) -> Path:
    path = tmp_path / "registry.json"
    payload = {
        "schema": "acp2.science.platform-registry",
        "constructed": True,
        "entries": entries,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_fixtures_are_explicitly_constructed() -> None:
    snapshot_wrapper = json.loads(CONSTRUCTED_SNAPSHOT.read_text(encoding="utf-8"))
    registry = json.loads(CONSTRUCTED_REGISTRY.read_text(encoding="utf-8"))
    assert snapshot_wrapper["constructed"] is True
    assert snapshot_wrapper["schema"] == "acp2.science.constructed-snapshot"
    assert snapshot_wrapper["snapshot"]["snapshot_schema"] == "acp2.science.artifact-snapshot"
    assert registry["constructed"] is True
    assert registry["schema"] == "acp2.science.platform-registry"
    assert registry["entries"][0]["id"] == "dsh-optfreq-live"
    assert registry["entries"][0]["constructed"] is True


def test_constructed_snapshot_reaches_a_supported_stationary_point(
    tmp_path: Path, capsys: Any
) -> None:
    assert _invoke(tmp_path) == 0
    captured = capsys.readouterr().out
    record = _record(tmp_path)
    science = record["extensions"]["science"]

    assert record["result"] == "PASS"
    assert record["exit_code"] == 0
    assert science["constructed_fixture"] is True
    assert science["real_run"] is False
    assert science["platform_registry"]["status"] == "REGISTERED"
    assert science["platform_registry"]["ids"] == ["dsh-optfreq-live"]
    assert science["snapshot"]["snapshot_id"] == "constructed-optfreq-live"
    assert len(science["snapshot"]["artifact_digests"]) == 6

    report = science["stationary_point_report"]
    assert report["status"] == "supported"
    assert report["convergence"]["status"] == "supported"
    assert report["convergence"]["reason"] == "affirmed"
    assert report["imaginary_mode_count"] == 0
    assert report["mode_count"] == 3
    assert report["all_modes"] == [1642.31, 3817.55, 3921.08]
    assert (tmp_path / "evidence" / "stationary-point-report.json").is_file()

    assert "imaginary-mode count: 0" in captured
    assert "all modes (cm-1): 1642.31, 3817.55, 3921.08" in captured
    assert "convergence: supported (affirmed)" in captured

    assert science["candidate"] == {
        "server_revision": None,
        "client_revision": None,
        "harness_revision": None,
    }
    assert science["candidate_status"] == "NOT_VERIFIED"
    assert science["candidate_gaps"]["server_revision"]
    assert science["p1_test_candidate"]["candidate_revision"] is None
    assert science["p1_test_candidate"]["status"] == "NOT_VERIFIED"
    assert science["p1_test_candidate"]["gap"]
    assert science["receipt_protocol_gap"] == "NOT_VERIFIED"


def test_altered_structure_digest_is_fail_not_not_verified(tmp_path: Path) -> None:
    payload = _snapshot_copy(tmp_path)
    for artifact in payload["snapshot"]["artifacts"]:
        if artifact["role"] == "optimized_structure":
            artifact["sha256"] = "sha256:" + "0" * 64
    manifest = _write_snapshot(tmp_path, payload)

    assert _invoke(tmp_path, manifest=manifest) == 1
    record = _record(tmp_path)
    assert record["result"] == "FAIL"
    assert record["exit_code"] == 1
    assert "snapshot content rejected" in record["reason"]
    assert record["extensions"]["science"]["snapshot_error"]["type"].startswith("Artifact")


def test_missing_input_manifest_is_not_verified(tmp_path: Path) -> None:
    assert _invoke(tmp_path, manifest="") == 2
    record = _record(tmp_path)
    assert record["result"] == "NOT_VERIFIED"
    assert "authorized input manifest path is empty" in record["reason"]


def test_missing_platform_registry_is_not_verified(tmp_path: Path) -> None:
    assert _invoke(tmp_path, registry=None) == 2
    record = _record(tmp_path)
    science = record["extensions"]["science"]
    assert record["result"] == "NOT_VERIFIED"
    assert "dsh-optfreq-live" in record["reason"]
    assert "platform registry is not published" in record["reason"]
    assert science["platform_registry"]["status"] == "MISSING"
    assert science["platform_registry"]["registered"] is False


def test_unregistered_id_is_not_verified(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path, [{"id": "some-other-run"}])
    assert _invoke(tmp_path, registry=registry) == 2
    record = _record(tmp_path)
    assert record["result"] == "NOT_VERIFIED"
    assert "absent from the supplied platform registry" in record["reason"]
    assert record["extensions"]["science"]["platform_registry"]["status"] == "UNREGISTERED"


def test_frequency_from_a_different_structure_is_fail(tmp_path: Path) -> None:
    payload = _snapshot_copy(tmp_path)
    for artifact in payload["snapshot"]["artifacts"]:
        if (
            artifact["role"] == "provenance"
            and artifact["path"] == "synthetic/frequency-minimum-result.json"
        ):
            artifact["path"] = "synthetic/frequency-wrong-structure-result.json"
            artifact["sha256"] = WRONG_STRUCTURE_FREQUENCY_SHA256
    manifest = _write_snapshot(tmp_path, payload)

    assert _invoke(tmp_path, manifest=manifest) == 1
    record = _record(tmp_path)
    science = record["extensions"]["science"]
    assert record["result"] == "FAIL"
    assert "stationary-point validation refuted" in record["reason"]
    assert science["stationary_point_report"]["status"] == "refuted"
    assert science["stationary_point_report"]["convergence"]["status"] == "supported"


def test_missing_native_frequency_output_is_not_verified(tmp_path: Path) -> None:
    payload = _snapshot_copy(tmp_path)
    payload["snapshot"]["artifacts"] = [
        artifact
        for artifact in payload["snapshot"]["artifacts"]
        if artifact["role"] != "frequency_output"
    ]
    manifest = _write_snapshot(tmp_path, payload)

    assert _invoke(tmp_path, manifest=manifest) == 2
    record = _record(tmp_path)
    assert record["result"] == "NOT_VERIFIED"
    assert "frequency_output" in record["reason"]


def test_method_mismatch_against_the_snapshot_is_fail(tmp_path: Path) -> None:
    payload = _snapshot_copy(tmp_path)
    payload["snapshot"]["scientific_identity"]["method"] = "B3LYP"
    manifest = _write_snapshot(tmp_path, payload)

    assert _invoke(tmp_path, manifest=manifest) == 1
    record = _record(tmp_path)
    assert record["result"] == "FAIL"
    assert "differs from snapshot method" in record["reason"]
    assert record["extensions"]["science"]["science_identity"]["status"] == "FAIL"


def test_harness_keeps_project_imports_lazy() -> None:
    tree = ast.parse(HARNESS_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "cccp" not in imported
    assert "acp" not in imported
    assert "acp_services" not in imported
