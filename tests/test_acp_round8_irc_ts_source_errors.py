"""Round-8 一.3(4): IRC ``ts_source`` failures are distinguishable.

``input.ts_source`` is derived by the platform from
``source_job_id``/``source_product_id``; a client must not hand-write it.  A
submitted payload that is present but incomplete used to fail the locked-field
equality check and surface as "IRC source evidence changed since the previous
submission" — misleading, because nothing changed.  These tests pin the two
distinct, greppable messages and prove a real evidence change is still
rejected with the locked-field check.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp.calculations.irc.source import resolve_verified_ts_source
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import _IRC_TS_SOURCE_LOCKED_FIELDS, JobManager

_LOCKED_FIELDS = (
    "job_id",
    "product_id",
    "geometry_sha256",
    "method",
    "basis",
    "charge",
    "multiplicity",
    "imaginary_frequency_cm1",
    "frequency_evidence_sha256",
    "source_completed_at",
)

PARTIAL_MESSAGE = (
    "input.ts_source is partial; omit it — the platform derives provenance "
    "from source_job_id/source_product_id"
)
CHANGED_MESSAGE = "IRC source evidence changed since the previous submission"


def test_locked_fields_match_the_submit_boundary() -> None:
    """Completeness (runtime) and the submit 422 must describe the same fields."""
    from acp.api.submission_contract import IRC_TS_SOURCE_LOCKED_FIELDS

    assert _IRC_TS_SOURCE_LOCKED_FIELDS == _LOCKED_FIELDS
    assert IRC_TS_SOURCE_LOCKED_FIELDS == _LOCKED_FIELDS


def _source(tmp_path: Path) -> JobRecord:
    """Minimal completed BatchOptimize TS product with verified frequencies."""
    root = tmp_path / "source"
    structure = root / "RESULT" / "structures" / "item_001__TAG_TS__optimized.xyz"
    structure.parent.mkdir(parents=True)
    structure.write_text("2\nTAG: TS\nH 0 0 0\nH 0 0 0.7\n", encoding="utf-8")
    optimized = root / "WORK" / "03_OPT" / "optimized.xyz"
    optimized.parent.mkdir(parents=True)
    optimized.write_text("2\noptimized\nH 0 0 0\nH 0 0 0.7\n", encoding="utf-8")
    (root / "RESULT" / "result_manifest.json").write_text(
        json.dumps(
            {
                "workflow": "BatchOptimize",
                "products": [
                    {
                        "id": "batch_item_001",
                        "kind": "structure",
                        "path": "structures/item_001__TAG_TS__optimized.xyz",
                        "label": "TS result",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    runtime = root / "WORK" / "00_RUNTIME"
    runtime.mkdir(parents=True)
    (runtime / "checkpoint.json").write_text(
        json.dumps(
            {
                "items_state": {
                    "item_001": {
                        "status": "completed",
                        "tag": "TS",
                        "optimized_xyz": "WORK/03_OPT/optimized.xyz",
                        "charge": 0,
                        "multiplicity": 1,
                        "frequency": {
                            "status": "completed",
                            "frequencies": [-321.5, 44.0],
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (root / "RESULT" / "batch_provenance.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "item_id": "item_001",
                        "role": "ts",
                        "effective_config": {"method": "B97-3c", "basis": "def2-mTZVPP"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return JobRecord(
        id="source-job",
        status=JobStatus.COMPLETED,
        work_dir=str(root),
        spec=JobSpec(
            workflow="BatchOptimize",
            method={
                "batch_roles": {
                    "ts": {"method": "B97-3c", "basis": "def2-mTZVPP"},
                    "int": {"method": "r2SCAN-3c", "basis": ""},
                }
            },
        ),
    )


def _manager(record: JobRecord) -> JobManager:
    manager = JobManager.__new__(JobManager)
    manager.store = type("Store", (), {"get": lambda self, job_id: record})()
    return manager


def _spec(record: JobRecord, **input_payload: object) -> JobSpec:
    return JobSpec(
        workflow="irc",
        input={
            "source_job_id": record.id,
            "source_product_id": "batch_item_001",
            **input_payload,
        },
        method={},
    )


def _provenance(record: JobRecord) -> dict[str, object]:
    return dict(resolve_verified_ts_source(record, "batch_item_001").provenance())


def test_contract_shell_payload_reports_partial_not_changed(tmp_path: Path) -> None:
    """The contract example ``{"schema": "irc_ts_source_v1"}`` is a trap, not a change."""
    record = _source(tmp_path)
    manager = _manager(record)
    with pytest.raises(ValueError) as caught:
        manager._verified_irc_spec(_spec(record, ts_source={"schema": "irc_ts_source_v1"}))
    message = str(caught.value)
    assert message == PARTIAL_MESSAGE
    assert "partial" in message
    assert "omit it" in message
    assert "source_job_id/source_product_id" in message
    assert "changed" not in message.lower()


@pytest.mark.parametrize(
    "partial",
    [
        {},
        "irc_ts_source_v1",
        ["irc_ts_source_v1"],
        {"schema": "irc_ts_source_v1", "job_id": "source-job"},
    ],
)
def test_any_incomplete_ts_source_is_partial(tmp_path: Path, partial: object) -> None:
    record = _source(tmp_path)
    manager = _manager(record)
    with pytest.raises(ValueError) as caught:
        manager._verified_irc_spec(_spec(record, ts_source=partial))
    assert str(caught.value) == PARTIAL_MESSAGE


def test_provenance_missing_one_locked_field_is_partial(tmp_path: Path) -> None:
    record = _source(tmp_path)
    manager = _manager(record)
    for field in _LOCKED_FIELDS:
        payload = _provenance(record)
        del payload[field]
        with pytest.raises(ValueError) as caught:
            manager._verified_irc_spec(_spec(record, ts_source=payload))
        assert str(caught.value) == PARTIAL_MESSAGE, field


def test_complete_provenance_with_changed_evidence_keeps_locked_check(
    tmp_path: Path,
) -> None:
    """A real evidence change must still be rejected by the equality check."""
    record = _source(tmp_path)
    manager = _manager(record)
    for field in _LOCKED_FIELDS:
        payload = _provenance(record)
        payload[field] = "changed-by-the-round-8-test"
        with pytest.raises(ValueError) as caught:
            manager._verified_irc_spec(_spec(record, ts_source=payload))
        message = str(caught.value)
        assert message == CHANGED_MESSAGE, field
        assert "partial" not in message.lower()


def test_omitted_ts_source_is_derived_by_the_platform(tmp_path: Path) -> None:
    record = _source(tmp_path)
    manager = _manager(record)
    derived = _provenance(record)
    spec = manager._verified_irc_spec(_spec(record))
    assert spec.input["ts_source"] == derived
    assert spec.input["source_type"] == "xyz_text"
    assert spec.input["source_job_id"] == record.id


def test_complete_matching_provenance_is_accepted(tmp_path: Path) -> None:
    """Idempotent replay / CLI compatibility: a full provenance still passes."""
    record = _source(tmp_path)
    manager = _manager(record)
    payload = _provenance(record)
    spec = manager._verified_irc_spec(_spec(record, ts_source=payload))
    assert spec.input["ts_source"] == payload
    assert spec.input["charge"] == 0
    assert spec.input["multiplicity"] == 1
