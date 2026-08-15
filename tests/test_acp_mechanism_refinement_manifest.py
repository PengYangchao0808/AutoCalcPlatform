# pyright: reportAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnusedCallResult=false
"""Tests for ACP mechanism refinement manifest I/O."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp.mechanism.refinement_manifest import (
    LEGACY_S3_SCHEMA,
    REFINEMENT_MANIFEST_V1,
    read_refinement_manifest,
    write_refinement_manifest,
)


def _minimal_manifest_payload() -> dict[str, object]:
    return {
        "schema_version": REFINEMENT_MANIFEST_V1,
        "stage": "S3",
        "fidelity": "low",
        "profile_id": "b97_3c_r2scan_3c_v1",
        "run_id": "run-001",
        "signature": "sha256:payload-signature",
        "file_signature": "sha256:file-signature",
        "summary": {"complete": 1},
        "structures": [
            {
                "id": "product_major_ts",
                "role": "ts",
                "kind": "ts",
                "status": "complete",
                "charge": 0,
                "multiplicity": 1,
                "forming_bonds": [[0, 1]],
                "opt_status": "complete",
                "frequency_status": "complete",
                "canonical_frequency_status": "complete",
                "sp_status": "complete",
                "opt_xyz": "stages/ts_opt/ts.xyz",
                "canonical_xyz": "stages/canonical/canonical.xyz",
                "input_xyz_sha256": "abc123",
                "atom_mapping_sha256": "def456",
                "pass2_rescue_attempts": [],
                "attempt_history": [],
            }
        ],
    }


def test_refinement_manifest_v1_round_trip_preserves_signature_fields(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    payload = _minimal_manifest_payload()

    _ = write_refinement_manifest(payload, manifest_path)

    assert manifest_path.exists()
    assert read_refinement_manifest(manifest_path) == payload


def test_write_refinement_manifest_validates_schema_version(tmp_path: Path) -> None:
    payload = _minimal_manifest_payload()
    payload["schema_version"] = "unexpected_schema"

    with pytest.raises(ValueError, match="schema_version"):
        _ = write_refinement_manifest(payload, tmp_path / "manifest.json")


def test_read_refinement_manifest_adapts_legacy_s3_payload(tmp_path: Path) -> None:
    manifest_path = tmp_path / "legacy_s3.json"
    legacy_structure = {
        "id": "int-1",
        "role": "intermediate",
        "kind": "minimum",
        "status": "complete",
    }
    legacy_payload = {
        "schema_version": LEGACY_S3_SCHEMA,
        "structures": {
            "row-1": legacy_structure,
        },
    }
    _ = manifest_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    normalized = read_refinement_manifest(manifest_path)

    assert normalized["schema_version"] == LEGACY_S3_SCHEMA
    assert normalized["stage"] == "S3"
    assert normalized["fidelity"] == "low"
    assert normalized["profile_id"] == "b97_3c_r2scan_3c_v1_legacy"
    assert normalized["structures"] == [legacy_structure]
