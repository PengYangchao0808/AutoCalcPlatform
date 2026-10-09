"""Tests for todo 47: AnalysisRevision — manual peak revisions recompute the
pure-analysis stages only (no QC rerun), preserve the base report, invalidate
on evidence-hash mismatch and reuse the existing WAITING_REVIEW semantics.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.nmr.analysis_revision import (
    AnalysisRevision,
    EvidenceHashMismatchError,
    NmrAnalysisSnapshot,
    PeakEdit,
    PeakEditError,
    PeakRevision,
    analysis_evidence_hash,
    apply_peak_edits,
    experiment_peak_digest,
    load_revision_index,
    load_revision_record,
    review_only_status,
    revision_identity,
    verify_evidence_hash,
    write_revision_record,
)
from acp.nmr.io import parse_experimental_nmr
from acp.nmr.models import ConformerShielding, NmrConfig
from acp.scheduler.jobs import EXIT_WAITING_REVIEW, JobStatus
from acp.workflows.nmr import revise_nmr_analysis, run_nmr_analysis

_SPECTRUM = "C: 40.0\nH: 4.0, 3.0, 1.0, 0.0"
_SHIELDINGS: dict[int, dict[str, object]] = {
    0: {"symbol": "C", "isotropic": 188.452125 - 40.0},
    1: {"symbol": "H", "isotropic": 32.1243166667 - 4.0},
    2: {"symbol": "H", "isotropic": 32.1243166667 - 3.0},
    3: {"symbol": "H", "isotropic": 32.1243166667 - 1.0},
    4: {"symbol": "H", "isotropic": 32.1243166667 - 0.0},
}


def _shielding_result(shieldings: Mapping[int, Mapping[str, Any]]) -> Any:
    from cccp.calculation.requests import TaskKind
    from cccp.calculation.results import NmrShielding, NmrShieldingPayload, TaskResult

    payload = NmrShieldingPayload(
        shieldings={
            int(index): NmrShielding(
                symbol=str(values.get("symbol", "")),
                isotropic=float(values.get("isotropic", 0.0)),
            )
            for index, values in shieldings.items()
        }
    )
    return TaskResult(
        task=TaskKind.NMR_SHIELDING, status="completed", complete=True, payload=payload
    )


def _make_structure() -> Structure:
    return Structure(
        id="cand",
        charge=0,
        multiplicity=1,
        symbols=["C", "H", "H", "H", "H"],
        coordinates=np.array([(0.0, 0.0, 0.0)] * 5, dtype=float),
    )


def _ensemble_with_shieldings(
    structure: Structure, shieldings: Mapping[int, Mapping[str, object]]
) -> StructureEnsemble:
    normalized = {index: dict(values) for index, values in shieldings.items()}
    ensemble = StructureEnsemble(
        records=[
            StructureRecord(
                structure=structure, energy_hartree=-1.0, free_energy_hartree=-1.0, weight=1.0
            )
        ]
    )
    ensemble.data = [ConformerShielding("conf_000", 1.0, normalized)]
    return ensemble


@contextmanager
def _pipeline_patches(structure: Structure, shieldings: Mapping[int, Mapping[str, object]]):
    with (
        patch("acp.workflows.nmr.StructureReader") as reader_cls,
        patch("acp.workflows.nmr.run_nmr_shielding") as qc_spy,
    ):
        reader = MagicMock()
        reader.read.return_value = structure
        reader_cls.return_value = reader
        qc_spy.side_effect = lambda request, context=None: _shielding_result(shieldings)
        yield qc_spy


def _run_base(root: Path, structure: Structure) -> Any:
    return run_nmr_analysis(
        input_sources=["CCO"],
        spectrum=_SPECTRUM,
        output_dir=str(root),
        skip_conformers=False,
        prebuilt_ensembles=[_ensemble_with_shieldings(structure, _SHIELDINGS)],
        error_model="placeholder-student-t",
    )


def _snapshot(
    root: Path,
    structure: Structure,
    base_report: Path,
    *,
    experiment: Any = None,
    shieldings: Mapping[int, Mapping[str, object]] | None = None,
    nmr_config: NmrConfig | None = None,
    protocol_id: str | None = "v2:base-protocol",
    dp5_model_id: str | None = None,
) -> NmrAnalysisSnapshot:
    cached = [
        ConformerShielding(
            "conf_000",
            1.0,
            {index: dict(values) for index, values in (shieldings or _SHIELDINGS).items()},
            delta_hartree=0.0,
        )
    ]
    return NmrAnalysisSnapshot.create(
        candidates=[structure],
        conformer_shieldings=[cached],
        experiment=experiment if experiment is not None else parse_experimental_nmr(_SPECTRUM),
        nmr_config=(
            nmr_config if nmr_config is not None else NmrConfig(error_model="placeholder-student-t")
        ),
        output_root=root,
        base_report_path=base_report,
        protocol_id=protocol_id,
        dp5_model_id=dp5_model_id,
    )


def _assignment_for(report: dict[str, Any], element: str) -> dict[str, Any]:
    for assignment in report["candidates"][0]["assignment"]:
        if assignment["element"] == element:
            return assignment
    raise AssertionError(f"no {element} assignment in report")


def test_peak_edit_recomputes_analysis_without_qc(tmp_path: Path) -> None:
    structure = _make_structure()
    root = tmp_path / "task"
    with _pipeline_patches(structure, _SHIELDINGS) as qc_spy:
        base = _run_base(root, structure)
        assert base.status == "completed", base.error
        base_report = Path(base.metadata["report_json"])
        base_calls = qc_spy.call_count
        assert base_calls == 1

        snapshot = _snapshot(root, structure, base_report)
        outcome = revise_nmr_analysis(
            snapshot,
            [
                PeakEdit(
                    op="update", element="C", index=0, shift_ppm=42.0, reason="manual re-reference"
                )
            ],
        )

        assert outcome.status == "completed", outcome.error
        assert qc_spy.call_count == base_calls, "revision must not invoke the GIAO QC task"

    revision_dir = Path(outcome.metadata["revision_dir"])
    assert not (revision_dir / "WORK").exists()
    assert not (revision_dir / "checkpoint.json").exists()
    revision_report = json.loads(Path(outcome.metadata["revision_report"]).read_text())
    assert _assignment_for(revision_report, "C")["exp_ppm"] == 42.0
    candidate = revision_report["candidates"][0]
    assert candidate["n_conformers"] == 1
    assert candidate["conformers"][0]["boltzmann_weight"] == 1.0


def test_revision_preserves_base_report_and_links_record(tmp_path: Path) -> None:
    structure = _make_structure()
    root = tmp_path / "task"
    with _pipeline_patches(structure, _SHIELDINGS):
        base = _run_base(root, structure)
        assert base.status == "completed", base.error
        base_report = Path(base.metadata["report_json"])
        base_bytes = base_report.read_bytes()
        base_payload = json.loads(base_bytes)
        assert _assignment_for(base_payload, "C")["exp_ppm"] == 40.0

        snapshot = _snapshot(root, structure, base_report)
        outcome = revise_nmr_analysis(
            snapshot, [PeakEdit(op="update", element="C", index=0, shift_ppm=42.0)]
        )
        assert outcome.status == "completed", outcome.error

        assert base_report.read_bytes() == base_bytes, "base report must never be overwritten"

    revision_dir = Path(outcome.metadata["revision_dir"])
    assert revision_dir != base_report.parent
    record = load_revision_record(revision_dir / "revision.json")
    assert record.report_before == "nmr_report.json"
    assert record.report_after == f"revisions/{record.revision_id}/nmr_report.json"
    assert record.base_evidence_hash == snapshot.evidence_hash
    assert record.peak_revision.base_digest == experiment_peak_digest(snapshot.experiment)
    assert record.result_identity
    assert record.revision_id == outcome.metadata["revision_id"]

    index = load_revision_index(base_report.parent / "revisions")
    assert index[record.revision_id] == record
    assert Path(outcome.metadata["revision_report"]).is_file()


def test_evidence_hash_mismatch_invalidates_without_recompute(tmp_path: Path) -> None:
    structure = _make_structure()
    root = tmp_path / "task"
    with _pipeline_patches(structure, _SHIELDINGS) as qc_spy:
        base = _run_base(root, structure)
        assert base.status == "completed", base.error
        base_report = Path(base.metadata["report_json"])
        snapshot = _snapshot(root, structure, base_report)
        base_calls = qc_spy.call_count

        tampered = replace(
            snapshot,
            conformer_shieldings=(
                (replace(snapshot.conformer_shieldings[0][0], boltzmann_weight=0.5),),
            ),
        )
        outcome = revise_nmr_analysis(
            tampered, [PeakEdit(op="update", element="C", index=0, shift_ppm=42.0)]
        )

        assert outcome.status == "failed"
        assert "evidence hash mismatch" in (outcome.error or "")
        assert outcome.metadata["revision_status"] == "invalidated"
        assert qc_spy.call_count == base_calls

    revisions_root = base_report.parent / "revisions"
    assert not revisions_root.exists()

    with pytest.raises(EvidenceHashMismatchError):
        verify_evidence_hash(
            tampered.evidence_hash,
            analysis_evidence_hash(
                tampered.candidates,
                tampered.conformer_shieldings,
                tampered.nmr_config,
                protocol_id=tampered.protocol_id,
                error_model_id=tampered.error_model_id,
                dp5_model_id=tampered.dp5_model_id,
            ),
        )


def test_review_only_semantics_reuse_existing_waiting_review(tmp_path: Path) -> None:
    structure = _make_structure()
    root = tmp_path / "task"
    with _pipeline_patches(structure, _SHIELDINGS):
        base = _run_base(root, structure)
        assert base.status == "completed", base.error
        snapshot = _snapshot(root, structure, Path(base.metadata["report_json"]))

        with patch(
            "acp.workflows.nmr._sensitivity_analysis",
            return_value={"requires_review": True, "flags": ["leave_one_out_winner_flip"]},
        ):
            outcome = revise_nmr_analysis(
                snapshot, [PeakEdit(op="update", element="C", index=0, shift_ppm=42.0)]
            )

        assert outcome.status == "completed", outcome.error
        assert outcome.metadata["review_required"] is True
        assert outcome.metadata["review_status"] == JobStatus.WAITING_REVIEW.value

    record = load_revision_record(Path(outcome.metadata["revision_dir"]) / "revision.json")
    assert record.requires_review is True
    assert record.review_status == JobStatus.WAITING_REVIEW.value
    assert review_only_status(True) == JobStatus.WAITING_REVIEW.value
    assert review_only_status(False) is None

    assert JobStatus.WAITING_REVIEW.is_active
    assert not JobStatus.WAITING_REVIEW.is_terminal
    assert EXIT_WAITING_REVIEW == 77

    import acp.nmr.analysis_revision as analysis_revision_module

    for lifecycle_name in ("pause", "unpause", "rerun", "resume", "cancel", "requeue"):
        assert not hasattr(analysis_revision_module, lifecycle_name)
    source = inspect.getsource(analysis_revision_module)
    assert "JobManager" not in source


def test_apply_peak_edits_update_remove_add_and_unknown_target() -> None:
    experiment = parse_experimental_nmr("C: 40.0(C1), 20.0\nH: 4.0(H1)")
    revised = apply_peak_edits(
        experiment,
        [
            PeakEdit(op="update", element="C", index=1, shift_ppm=21.5, atom_label="C2"),
            PeakEdit(op="add", element="H", index=None, shift_ppm=7.26, multiplicity=3),
        ],
    )
    assert [peak.index for peak in revised.peaks["C"]] == [0, 1]
    assert revised.peaks["C"][1].shift_ppm == 21.5
    assert revised.peaks["C"][1].label_candidates == ("C2",)
    added = revised.peaks["H"][1]
    assert added.index == 1
    assert added.multiplicity == 3
    assert added.shift_ppm == 7.26
    assert experiment.peaks["C"][1].shift_ppm == 20.0, "base experiment stays untouched"

    removed = apply_peak_edits(experiment, [PeakEdit(op="remove", element="H", index=0)])
    assert removed.peaks["H"] == []

    with pytest.raises(PeakEditError):
        apply_peak_edits(experiment, [PeakEdit(op="update", element="C", index=7, shift_ppm=1.0)])
    with pytest.raises(ValueError):
        PeakEdit(op="update", element="C", index=0)


def test_peak_revision_and_analysis_revision_records_round_trip(tmp_path: Path) -> None:
    experiment = parse_experimental_nmr(_SPECTRUM)
    revised = apply_peak_edits(
        experiment, [PeakEdit(op="update", element="C", index=0, shift_ppm=42.0)]
    )
    peak_revision = PeakRevision(
        base_digest=experiment_peak_digest(experiment),
        revised_digest=experiment_peak_digest(revised),
        edits=(PeakEdit(op="update", element="C", index=0, shift_ppm=42.0),),
        reason="re-reference",
    )
    assert PeakRevision.from_dict(peak_revision.as_dict()) == peak_revision

    revision_id = revision_identity(
        base_evidence_hash="v2:abc",
        peak_revision=peak_revision,
        protocol_id="v2:proto",
        error_model_id="placeholder-student-t",
        dp5_model_id=None,
    )
    assert revision_id == revision_identity(
        base_evidence_hash="v2:abc",
        peak_revision=peak_revision,
        protocol_id="v2:proto",
        error_model_id="placeholder-student-t",
        dp5_model_id=None,
    )

    record = AnalysisRevision(
        revision_id=revision_id,
        base_evidence_hash="v2:abc",
        peak_revision=peak_revision,
        protocol_id="v2:proto",
        error_model_id="placeholder-student-t",
        dp5_model_id=None,
        report_before="nmr_report.json",
        report_after=f"revisions/{revision_id}/nmr_report.json",
        result_identity="v2:result",
        created_at="2026-10-06T00:00:00+00:00",
        requires_review=True,
        review_status=JobStatus.WAITING_REVIEW.value,
    )
    assert AnalysisRevision.from_dict(record.as_dict()) == record
    path = write_revision_record(tmp_path / "revisions" / revision_id, record)
    assert load_revision_record(path) == record
    with pytest.raises(ValueError):
        AnalysisRevision(
            revision_id=revision_id,
            base_evidence_hash="v2:abc",
            peak_revision=peak_revision,
            protocol_id=None,
            error_model_id="placeholder-student-t",
            dp5_model_id=None,
            report_before="nmr_report.json",
            report_after="revisions/x/nmr_report.json",
            result_identity="v2:result",
            created_at="2026-10-06T00:00:00+00:00",
            requires_review=True,
            review_status="",
        )


def test_result_identity_detects_changed_analysis(tmp_path: Path) -> None:
    structure = _make_structure()
    root = tmp_path / "task"
    with _pipeline_patches(structure, _SHIELDINGS):
        base = _run_base(root, structure)
        assert base.status == "completed", base.error
        snapshot = _snapshot(root, structure, Path(base.metadata["report_json"]))
        first = revise_nmr_analysis(
            snapshot, [PeakEdit(op="update", element="C", index=0, shift_ppm=42.0)]
        )
        second = revise_nmr_analysis(
            snapshot, [PeakEdit(op="update", element="C", index=0, shift_ppm=50.0)]
        )
        assert first.status == "completed" and second.status == "completed"
        first_report = json.loads(Path(first.metadata["revision_report"]).read_text())
        assert first.metadata["result_identity"] != second.metadata["result_identity"]
        assert _assignment_for(first_report, "C")["exp_ppm"] == 42.0
