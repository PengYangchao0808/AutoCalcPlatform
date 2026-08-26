"""Regression tests for task-boundary molecule-name inheritance."""

from __future__ import annotations

from acp.api.v1_routes import _job_input_source_stem, _resolve_job_molecule_name
from acp.scheduler.jobs import JobRecord, JobSpec
from acp.scheduler.naming import canonical_molecule_name


class _Manager:
    def __init__(self, record: JobRecord) -> None:
        self.record = record

    def get(self, job_id: str) -> JobRecord | None:
        return self.record if job_id == self.record.id else None


def test_structured_source_is_not_stringified_into_task_name() -> None:
    source = {
        "source_type": "task_artifact",
        "source_job_id": "20260824_194151_001_PESsearch",
        "artifact_path": "RESULT/mechanism/s2_path_manifest.json",
    }
    assert _job_input_source_stem({"source": source}) == "s2_path_manifest"
    assert "source_job_id" not in _job_input_source_stem({"source": source})


def test_source_job_inherits_only_canonical_molecule_name() -> None:
    source_record = JobRecord(
        id="20260824_194151_001_PESsearch",
        spec=JobSpec(
            workflow="PESsearch",
            name="INT_P_energy_mt5g72i5_PESsearch_old_remark",
            molecule_name="INT_P_energy_mt5g72i5",
            task_name="PESsearch",
            remark="old_remark",
        ),
    )
    manager = _Manager(source_record)
    source = {
        "source_type": "task_artifact",
        "source_job_id": source_record.id,
        "artifact_path": "RESULT/mechanism/s2_path_manifest.json",
    }
    assert (
        _resolve_job_molecule_name(
            source_record.id[-24:],
            {"source": source},
            manager,
        )
        == "INT_P_energy_mt5g72i5"
    )


def test_job_id_and_molecular_suffixes_are_not_molecule_names() -> None:
    job_id = "20260824_194151_001_PESsearch"
    assert canonical_molecule_name(job_id, fallback="mol") == "mol"
    assert canonical_molecule_name(r"C:\\tmp\\molecule.xyz") == "molecule"
