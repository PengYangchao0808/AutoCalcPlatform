"""Tests for auxiliary basis backward compatibility (Phase 5.5).

Verifies that legacy aux_basis fields are correctly migrated to aux_j_basis
and aux_c_basis, both in catalog layer and SQLite store layer.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_legacy_aux_basis_migrates_to_auxj_for_normal_dft() -> None:
    """Legacy aux_basis field should migrate to aux_j_basis for normal DFT."""
    from acp.catalog import _clamp_to_functional

    level = {"functional": "B3LYP", "basis": "def2-TZVPP", "aux_basis": "def2/J"}
    _clamp_to_functional(level, method_key="functional")
    assert "aux_basis" not in level
    assert level["aux_j_basis"] == "def2/J"
    assert "aux_c_basis" not in level or level["aux_c_basis"] == ""


def test_legacy_aux_basis_migrates_to_auxc_for_dlpno() -> None:
    """DLPNO's legacy aux_basis should migrate to aux_c_basis."""
    from acp.catalog import _clamp_to_functional

    level = {
        "functional": "DLPNO-CCSD(T)",
        "basis": "def2-TZVPP",
        "aux_basis": "cc-pVTZ/C",
    }
    _clamp_to_functional(level, method_key="functional")
    assert level["aux_c_basis"] == "cc-pVTZ/C"
    assert "aux_basis" not in level


def test_row_to_record_normalizes_legacy_aux_basis(tmp_path: Path) -> None:
    """SQLite read path should auto-normalize legacy aux_basis in method.levels."""
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
    from acp.scheduler.store import JobStore

    store = JobStore(tmp_path / "test.db")
    # Write v1.0 format spec (simulate historical job)
    legacy_spec = JobSpec(
        workflow="singlepoint",
        name="legacy",
        input={"smiles": "CCO"},
        method={
            "levels": {
                "single_point": {
                    "engine": "orca",
                    "functional": "B3LYP",
                    "basis": "def2-TZVPP",
                    "aux_basis": "def2/J",
                }
            }
        },
    )
    record = JobRecord(
        id="job-legacy",
        spec=legacy_spec,
        status=JobStatus.COMPLETED,
        work_dir=str(tmp_path),
    )
    store.create(record)

    # Read path should have normalized via _row_to_record
    out = store.get("job-legacy")
    assert out is not None
    sp = out.spec.method["levels"]["single_point"]
    assert "aux_basis" not in sp
    assert sp["aux_j_basis"] == "def2/J"


def test_row_to_record_new_format_is_idempotent(tmp_path: Path) -> None:
    """New format spec should be idempotent through normalize_legacy_method."""
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
    from acp.scheduler.store import JobStore

    store = JobStore(tmp_path / "test.db")
    new_spec = JobSpec(
        workflow="singlepoint",
        name="new",
        input={"smiles": "CCO"},
        method={
            "levels": {
                "single_point": {
                    "engine": "orca",
                    "functional": "PWPB95",
                    "basis": "def2-TZVPP",
                    "aux_j_basis": "def2/J",
                    "aux_c_basis": "def2-TZVPP/C",
                }
            }
        },
    )
    record = JobRecord(
        id="job-new",
        spec=new_spec,
        status=JobStatus.COMPLETED,
        work_dir=str(tmp_path),
    )
    store.create(record)
    out = store.get("job-new")
    assert out is not None
    sp = out.spec.method["levels"]["single_point"]
    assert sp["aux_j_basis"] == "def2/J"
    assert sp["aux_c_basis"] == "def2-TZVPP/C"
    assert "aux_basis" not in sp


def test_normalize_legacy_method_dlpno_routes_to_aux_c() -> None:
    """DLPNO's legacy aux_basis should normalize to aux_c_basis."""
    from acp.catalog import normalize_legacy_method

    method = {
        "levels": {
            "sp": {
                "functional": "DLPNO-CCSD(T)",
                "basis": "def2-TZVPP",
                "aux_basis": "cc-pVTZ/C",
            }
        }
    }
    out = normalize_legacy_method(method)
    assert out["levels"]["sp"]["aux_c_basis"] == "cc-pVTZ/C"
    assert "aux_basis" not in out["levels"]["sp"]
