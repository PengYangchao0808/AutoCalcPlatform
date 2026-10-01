"""Screen-policy structure-source projection (plan todo 12 / decision D14, M7).

Before the weight-provenance work a ``screen``-policy Confsearch job wrote no
``RESULT/result_manifest.json`` at all.  The engine finalization now merge-
registers the consolidated final report (``confsearch_final_report`` /
``confsearch_final_conformers``) for every policy — including ``screen`` — so
the scheduler structure-source projection sees a registered multi-frame
structure product where it previously saw nothing.

These tests pin the projection contract for that tree:

* a **completed** screen job lists every formally published single conformer in
  rank order — the multi-frame ensemble XYZ is never counted as a structure
  card (single-frame policy);
* a **terminal (failed)** screen job rejects the multi-frame
  ``confsearch/final_conformers.xyz`` ensemble and keeps the ``kind=report``
  JSON hidden.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from acp.confsearch import ConfsearchEngine, ConfsearchRequest
from acp.confsearch.contracts import ProtocolOutcome
from acp.confsearch.protocols import PROTOCOL_RUNNERS
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.store import JobStore
from acp.scheduler.structure_sources import StructureSourceService


def _stub_structure() -> Any:
    from acp.core.models import Structure

    return Structure(
        id="water",
        charge=0,
        multiplicity=1,
        symbols=["O", "H", "H"],
        coordinates=[[0.0, 0.0, 0.0], [0.9, 0.0, 0.0], [-0.3, 0.9, 0.0]],
        metadata={},
    )


def _seed_task_markers(tmp_path: Path) -> Path:
    """Make *tmp_path* look like a scheduler task dir (mol_dir == tmp_path)."""
    (tmp_path / "job.json").write_text("{}", encoding="utf-8")
    (tmp_path / "task.json").write_text("{}", encoding="utf-8")
    return tmp_path


def _screen_outcome(request: ConfsearchRequest, mol_dir: Path) -> ProtocolOutcome:
    """Fabricated screen outcome: three conformers, CENSO table provenance."""
    del request, mol_dir
    coords = [[0.0, 0.0, 0.0], [0.9, 0.0, 0.0], [-0.3, 0.9, 0.0]]
    return ProtocolOutcome(
        records=[
            {
                "conf_id": f"SP{i:04d}",
                "source_conf_id": f"SP{i:04d}",
                "symbols": ["O", "H", "H"],
                "coordinates": [list(c) for c in coords],
                "energy_hartree": -76.01 + 0.01 * (i - 1),
                "free_energy_hartree": -76.00 + 0.01 * (i - 1),
            }
            for i in (1, 2, 3)
        ],
        temperature_k=298.15,
        refined_conf_ids=[],
        sampling={"method": "stub"},
        workflow_metadata={},
        weight_table={"SP0001": 0.5, "SP0002": 0.3, "SP0003": 0.2},
        weight_source="censo",
        weight_method="censo_table",
        population_coverage=1.0,
        energy_kind="censo",
    )


def _build_screen_policy_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run the real engine under ``screen`` policy; return the task mol_dir."""
    from acp.io.structures import StructureReader

    mol_dir = _seed_task_markers(tmp_path)
    monkeypatch.setitem(PROTOCOL_RUNNERS, "censo-crest", _screen_outcome)
    monkeypatch.setattr(StructureReader, "read", lambda self, *a, **k: _stub_structure())
    xyz = tmp_path / "water.xyz"
    xyz.write_text("3\nwater\nO 0.0 0.0 0.0\nH 0.9 0.0 0.0\nH -0.3 0.9 0.0\n", encoding="utf-8")

    request = ConfsearchRequest(
        input_source=str(xyz),
        output_dir=tmp_path,
        protocol="censo-crest",
        refinement_policy="screen",
    )
    result = ConfsearchEngine().run(request)
    assert result.status == "completed", result.error

    # Precondition: the screen tree carries the newly registered manifest.
    result_manifest = json.loads(
        (mol_dir / "RESULT" / "result_manifest.json").read_text(encoding="utf-8")
    )
    products = {product["id"]: product for product in result_manifest["products"]}
    assert products["confsearch_final_conformers"]["kind"] == "structure"
    assert products["confsearch_final_conformers"]["path"] == "confsearch/final_conformers.xyz"
    assert (mol_dir / "RESULT" / "confsearch" / "final_conformers.xyz").is_file()
    return mol_dir


def _make_record(job_id: str, *, status: JobStatus, work_dir: Path) -> JobRecord:
    return JobRecord(
        id=job_id,
        spec=JobSpec(
            workflow="Confsearch",
            name="screen_proj",
            input={},
            project_id="uncategorized",
            molecule_name="INT_S",
        ),
        status=status,
        work_dir=str(work_dir),
        created_at="2026-09-28T09:00:00+00:00",
        updated_at="2026-09-28T09:00:00+00:00",
        completed_at=None if status is JobStatus.FAILED else "2026-09-28T10:00:00+00:00",
        project_id="uncategorized",
        result={},
    )


def test_completed_screen_job_projection_lists_published_conformers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Completed screen job: every published single conformer is listed in rank
    order; the multi-frame final-conformers product adds no card."""
    mol_dir = _build_screen_policy_tree(tmp_path, monkeypatch)
    store = JobStore(tmp_path / "acp_jobs.db")
    store.create(_make_record("screen_done", status=JobStatus.COMPLETED, work_dir=mol_dir))
    service = StructureSourceService(store, tmp_path)

    entries = service.list_recent()
    paths = [entry["path"] for entry in entries]
    assert paths == [
        "RESULT/confsearch/conformers/conf_0001.xyz",
        "RESULT/confsearch/conformers/conf_0002.xyz",
        "RESULT/confsearch/conformers/conf_0003.xyz",
    ], f"completed Confsearch projection must list all published conformers; got {paths}"
    assert "RESULT/confsearch/final_conformers.xyz" not in paths
    assert entries[0]["label"] == "Conformer (conf_0001)"
    assert all(entry["job_status"] == "completed" for entry in entries)


def test_terminal_screen_job_hides_multiframe_ensemble(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed screen job: the multi-frame structure product is rejected by the
    single-frame policy, and the kind=report JSON stays hidden."""
    mol_dir = _build_screen_policy_tree(tmp_path, monkeypatch)
    store = JobStore(tmp_path / "acp_jobs.db")
    store.create(_make_record("screen_fail", status=JobStatus.FAILED, work_dir=mol_dir))
    service = StructureSourceService(store, tmp_path)

    entries = service.list_recent()
    paths = [entry["path"] for entry in entries]
    assert "RESULT/confsearch/final_conformers.xyz" not in paths, (
        "multi-frame ensemble XYZ must never surface as a single structure; "
        f"got {paths}"
    )
    # The kind=report JSON is a document, not a 3D structure — never listed.
    assert "RESULT/confsearch/final_report.json" not in paths
