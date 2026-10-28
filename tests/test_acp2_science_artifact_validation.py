"""A-12a: artifact-snapshot content validation and the platform boundary.

All snapshots here are **synthetic**: they exercise the static consumer against
constructed files under a temporary root.  Nothing claims a real QC run or a
real platform export.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from cccp.analysis.research import (
    ArtifactDigestMismatchError,
    ArtifactPathEscapeError,
    ArtifactScienceIdentityError,
    ArtifactSnapshot,
    ArtifactStructuralMismatchError,
    UnsupportedSnapshotSchemaVersionError,
    load_verified_snapshot,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "src" / "cccp" / "analysis" / "research" / "artifact_manifest.py"
HARNESS_PATH = REPO_ROOT / "scripts" / "acceptance" / "acp2_science.py"
FIXTURE_MANIFEST = (
    REPO_ROOT / "tests" / "fixtures" / "acp2" / "science" / "singlepoint" / "fixture-manifest.json"
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


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _write(path: Path, data: bytes) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"sha256": _sha256(data), "size_bytes": len(data)}


def _platform_block(*, export_eligible: bool = True) -> dict[str, Any]:
    return {
        "owner": "B",
        "binding": {
            "task_ref": "synthetic-task-a12",
            "job_id": "synthetic-job-a12",
            "attempt": 1,
            "release_id": "synthetic-release-1",
            "submission_id": "synthetic-submission-1",
        },
        "export": {
            "export_eligible": export_eligible,
            "authorized": True,
            "frozen": True,
            "revoked": False,
            "snapshot_id": "synthetic-snapshot-a12",
        },
    }


def _synthetic_snapshot(tmp_path: Path, *, export_eligible: bool = True) -> dict[str, Any]:
    root = tmp_path / "frozen-root"
    coordinates = b"O 0 0 0\nH 0 0 1\nH 0 1 0\n"
    start = _write(root / "inputs" / "start.xyz", b"3\nsynthetic start\n" + coordinates)
    final = _write(root / "outputs" / "final.xyz", b"3\nsynthetic final\n" + coordinates)
    freq = _write(root / "outputs" / "freq.out", b"synthetic frequency table\n")
    mapping = {
        "left_to_right": {
            "start:O1": "final:O1",
            "start:H1": "final:H1",
            "start:H2": "final:H2",
        },
        "right_to_left": {
            "final:O1": "start:O1",
            "final:H1": "start:H1",
            "final:H2": "start:H2",
        },
        "unmapped_left": [],
        "unmapped_right": [],
    }
    return {
        "snapshot_id": "synthetic-a12-valid",
        "snapshot_schema": "acp2.science.artifact-snapshot",
        "snapshot_schema_version": 1,
        "generation": "synthetic-gen-0001",
        "read_root": str(root),
        "platform": _platform_block(export_eligible=export_eligible),
        "scientific_identity": {
            "structure_sha256": final["sha256"],
            "method": "wB97X-D4",
            "electronic_state": {"charge": 0, "multiplicity": 1, "state_id": "ground"},
            "energy_unit": "hartree",
            "atom_mapping": mapping,
            "basis": "def2-SVP",
        },
        "artifacts": [
            {
                "role": "input_structure",
                "path": "inputs/start.xyz",
                "sha256": start["sha256"],
                "size_bytes": start["size_bytes"],
            },
            {
                "role": "optimized_structure",
                "path": "outputs/final.xyz",
                "sha256": final["sha256"],
                "size_bytes": final["size_bytes"],
            },
            {
                "role": "frequency_output",
                "path": "outputs/freq.out",
                "sha256": freq["sha256"],
                "size_bytes": freq["size_bytes"],
            },
        ],
    }


def test_valid_snapshot_resolves_three_artifacts_and_matches_digests(tmp_path: Path) -> None:
    payload = _synthetic_snapshot(tmp_path)
    snapshot = ArtifactSnapshot.from_dict(payload)

    root = Path(payload["read_root"]).resolve()
    assert len(snapshot.artifacts) == 3
    assert snapshot.generation == "synthetic-gen-0001"
    assert snapshot.snapshot_schema_version == 1
    for artifact in snapshot.artifacts:
        assert artifact.resolved_path.is_relative_to(root)
        assert artifact.resolved_path.is_file()
        # Paths in the document stay relative; the absolute path is local only.
        assert not Path(artifact.to_dict()["path"]).is_absolute()

    table = snapshot.verify_content()
    assert len(table) == 3
    declared = {artifact.relative_path: artifact.declared_sha256 for artifact in snapshot.artifacts}
    assert {row.relative_path: row.sha256 for row in table} == declared
    assert snapshot.digest_table() == [row.to_dict() for row in table]

    # Canonical serialization is deterministic and ends with exactly one newline.
    assert snapshot.to_json() == ArtifactSnapshot.from_dict(copy.deepcopy(payload)).to_json()
    assert snapshot.to_json().endswith("\n")

    # Public package export is the same object, not a second implementation.
    import cccp.analysis.research as research

    assert research.ArtifactSnapshot is ArtifactSnapshot


@pytest.mark.parametrize("bad_path", ["../outside.xyz", "{absolute}"])
def test_path_escape_is_rejected_with_typed_error(tmp_path: Path, bad_path: str) -> None:
    payload = _synthetic_snapshot(tmp_path)
    outside = tmp_path / "outside.xyz"
    outside.write_bytes(b"outside the declared root\n")
    payload["artifacts"][0]["path"] = str(outside) if bad_path == "{absolute}" else bad_path

    with pytest.raises(ArtifactPathEscapeError):
        ArtifactSnapshot.from_dict(payload)


def test_path_escape_through_symlink_is_rejected(tmp_path: Path) -> None:
    payload = _synthetic_snapshot(tmp_path)
    root = Path(payload["read_root"])
    outside = tmp_path / "outside-symlink-target.xyz"
    outside.write_bytes(b"outside via symlink\n")
    (root / "inputs" / "link.xyz").symlink_to(outside)
    payload["artifacts"][0]["path"] = "inputs/link.xyz"

    with pytest.raises(ArtifactPathEscapeError):
        ArtifactSnapshot.from_dict(payload)


def test_bad_digest_is_rejected_with_typed_error(tmp_path: Path) -> None:
    payload = _synthetic_snapshot(tmp_path)
    # A non-structure artifact may keep the scientific identity intact, so the
    # failure is reached by content verification rather than by the identity tie.
    payload["artifacts"][2]["sha256"] = "sha256:" + "0" * 64

    snapshot = ArtifactSnapshot.from_dict(payload)
    with pytest.raises(ArtifactDigestMismatchError):
        snapshot.verify_content()


def test_structural_mismatch_is_rejected_with_typed_error(tmp_path: Path) -> None:
    payload = _synthetic_snapshot(tmp_path)
    payload["artifacts"][0]["role"] = "role-that-does-not-exist"
    with pytest.raises(ArtifactStructuralMismatchError):
        ArtifactSnapshot.from_dict(payload)

    payload = _synthetic_snapshot(tmp_path)
    payload["snapshot_schema_version"] = 99
    with pytest.raises(UnsupportedSnapshotSchemaVersionError):
        ArtifactSnapshot.from_dict(payload)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["scientific_identity"].update(
            {"structure_sha256": "sha256:" + "1" * 64}
        ),
        lambda payload: payload["scientific_identity"].update({"energy_unit": "electronvolt"}),
        lambda payload: payload["scientific_identity"].update({"method": ""}),
    ],
)
def test_scientific_identity_mismatch_is_rejected_with_typed_error(
    tmp_path: Path, mutate: Any
) -> None:
    payload = _synthetic_snapshot(tmp_path)
    mutate(payload)
    with pytest.raises(ArtifactScienceIdentityError):
        ArtifactSnapshot.from_dict(payload)


def _run_harness(manifest_path: Path, out: Path) -> int:
    harness = _load_harness()
    return harness.main(
        [
            "--scenario",
            "science:validate-artifacts",
            "--fixture",
            str(FIXTURE_MANIFEST),
            "--input-manifest",
            str(manifest_path),
            "--out",
            str(out),
        ]
    )


@pytest.mark.parametrize("mode", ["ineligible", "missing"])
def test_harness_rejects_bad_export_while_cccp_validates_content(tmp_path: Path, mode: str) -> None:
    payload = _synthetic_snapshot(tmp_path, export_eligible=(mode != "ineligible"))
    if mode == "missing":
        del payload["platform"]["export"]

    # The scientific content is valid in both cases.
    snapshot, table = load_verified_snapshot(payload)
    assert len(snapshot.artifacts) == 3
    assert len(table) == 3

    # The harness refuses the platform envelope even though content is valid.
    harness = _load_harness()
    check = harness.check_platform_export(payload)
    assert check.eligible is False
    assert check.status == "INELIGIBLE"
    if mode == "missing":
        assert any("platform.export" in reason for reason in check.reasons)

    manifest_path = tmp_path / f"manifest-{mode}.json"
    manifest_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    out = tmp_path / f"evidence-{mode}"
    assert _run_harness(manifest_path, out) == 1
    record = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert record["result"] == "FAIL"
    assert record["exit_code"] == 1
    assert record["extensions"]["science"]["platform_export"]["eligible"] is False


def test_harness_accepts_eligible_platform_binding(tmp_path: Path) -> None:
    payload = _synthetic_snapshot(tmp_path)
    harness = _load_harness()
    check = harness.check_platform_export(payload)
    assert check.eligible is True
    assert check.status == "ELIGIBLE"
    assert check.binding is not None and check.binding["task_ref"] == "synthetic-task-a12"


def test_content_failure_types_are_distinct() -> None:
    families = (
        ArtifactPathEscapeError,
        ArtifactDigestMismatchError,
        ArtifactStructuralMismatchError,
        ArtifactScienceIdentityError,
    )
    for left in families:
        for right in families:
            if left is not right:
                assert not issubclass(left, right), (left.__name__, right.__name__)


def test_artifact_manifest_source_has_no_platform_decision_or_platform_import() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in {"acp", "acp_services"}, alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module.split(".")[0] not in {"acp", "acp_services"}, module

    forbidden = (
        "authorization",
        "authorisation",
        "revoked",
        "revocation",
        "export_eligible",
        "current_generation",
    )
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "src" / "cccp").rglob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        offenders.extend(f"{path}:{token}" for token in forbidden if token in text)
    assert offenders == [], f"platform decision tokens leaked into cccp: {offenders}"
