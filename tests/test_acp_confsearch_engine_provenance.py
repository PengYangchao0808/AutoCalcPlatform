"""Engine finalization provenance tests (plan todo 7).

Pins ``ConfsearchEngine._finalize`` wiring of the weight-provenance metadata
(``temperature_k`` / ``weight_table``) and the consolidated final report
(``final_report.json`` / ``final_conformers.xyz`` merge-registered into
``RESULT/result_manifest.json``), the policy-aware rank1 quality gate, and the
report-failure propagation contract (report errors must surface as a
``failed`` result, never be swallowed).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp.confsearch import ConfsearchEngine, ConfsearchRequest
from acp.confsearch.contracts import ProtocolOutcome
from acp.confsearch.manifest import read_manifest
from acp.confsearch.protocols import PROTOCOL_RUNNERS

TABLE_WEIGHT = 0.8442


def _stub_structure() -> object:
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


def _seed_input(tmp_path: Path) -> Path:
    xyz = tmp_path / "water.xyz"
    xyz.write_text("3\nwater\nO 0.0 0.0 0.0\nH 0.9 0.0 0.0\nH -0.3 0.9 0.0\n", encoding="utf-8")
    return xyz


def _provenance_outcome(request: ConfsearchRequest, mol_dir: Path) -> ProtocolOutcome:
    """Fabricated rank1 outcome with the full T2 provenance surface."""
    table_path = mol_dir / "RESULT" / "ensembles" / "boltzmann_table.json"
    return ProtocolOutcome(
        records=[
            {
                "conf_id": "CONF1",
                "source_conf_id": "CONF1",
                "symbols": ["O", "H", "H"],
                "coordinates": [[0.0, 0.0, 0.0], [0.9, 0.0, 0.0], [-0.3, 0.9, 0.0]],
                "energy_hartree": -76.01,
                "free_energy_hartree": -76.0,
            },
        ],
        temperature_k=310.0,
        refined_conf_ids=["CONF1"] if request.refinement_policy == "rank1" else [],
        sampling={"method": "stub"},
        workflow_metadata={"boltzmann_table_json": str(table_path)},
        weight_table={"CONF1": TABLE_WEIGHT},
        weight_source="censo",
        weight_method="censo_table_rank1",
        population_coverage=1.0,
        energy_kind="dft",
    )


# --- happy path: manifest provenance + report artifacts + rank1 gate ---------


def test_engine_finalizes_manifest_with_provenance_and_final_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mol_dir = _seed_task_markers(tmp_path)
    table_path = mol_dir / "RESULT" / "ensembles" / "boltzmann_table.json"
    table_path.parent.mkdir(parents=True, exist_ok=True)
    table_path.write_text(
        json.dumps({"temperature_k": 310.0, "source": "censo", "weights": {"CONF1": TABLE_WEIGHT}}),
        encoding="utf-8",
    )
    monkeypatch.setitem(
        PROTOCOL_RUNNERS,
        "censo-crest",
        lambda request, _overlay: _provenance_outcome(request, mol_dir),
    )

    from acp.io.structures import StructureReader

    monkeypatch.setattr(StructureReader, "read", lambda self, *a, **k: _stub_structure())
    xyz = _seed_input(tmp_path)

    request = ConfsearchRequest(
        input_source=str(xyz),
        output_dir=tmp_path,
        protocol="censo-crest",
        refinement_policy="rank1",
    )
    result = ConfsearchEngine().run(request)

    assert result.status == "completed"
    # Policy-aware rank1 gate: single p1=0.8442 entry must PASS (sum≈1 would fail).
    assert result.quality_gates["boltzmann_weights_valid"] is True
    assert result.quality_gates["G1"] == "PASS"
    assert result.quality_gates["weight_source"] == "censo"
    assert result.conformers[0].boltzmann_weight == pytest.approx(TABLE_WEIGHT)

    assert result.manifest_path is not None
    payload = read_manifest(result.manifest_path)
    assert payload["temperature_k"] == pytest.approx(310.0)
    assert payload["weight_table"] == {
        "source": "censo",
        "method": "censo_table_rank1",
        "temperature_k": pytest.approx(310.0),
        "population_coverage": pytest.approx(1.0),
        "reference": "RESULT/ensembles/boltzmann_table.json",
    }
    assert payload["report"] == {
        "json": "confsearch/final_report.json",
        "xyz": "confsearch/final_conformers.xyz",
    }

    confsearch_dir = tmp_path / "RESULT" / "confsearch"
    report_path = confsearch_dir / "final_report.json"
    xyz_path = confsearch_dir / "final_conformers.xyz"
    assert report_path.is_file()
    assert xyz_path.is_file()

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == "confsearch_final_report_v1"
    assert report["refinement_policy"] == "rank1"
    assert report["temperature_k"] == pytest.approx(310.0)
    assert report["weight_table"]["reference"] == "RESULT/ensembles/boltzmann_table.json"
    assert report["conformers"][0]["boltzmann_weight"] == pytest.approx(TABLE_WEIGHT)

    result_manifest = json.loads(
        (tmp_path / "RESULT" / "result_manifest.json").read_text(encoding="utf-8")
    )
    products = {product["id"]: product for product in result_manifest["products"]}
    assert products["confsearch_final_report"]["kind"] == "report"
    assert products["confsearch_final_report"]["path"] == "confsearch/final_report.json"
    assert products["confsearch_final_conformers"]["kind"] == "structure"
    assert products["confsearch_final_conformers"]["path"] == "confsearch/final_conformers.xyz"

    # Provenance is additive: weights/weight_sum keep the pre-provenance shape.
    boltzmann = json.loads((confsearch_dir / "boltzmann.json").read_text(encoding="utf-8"))
    assert boltzmann["weights"] == {"conf_0001": TABLE_WEIGHT}
    assert boltzmann["weight_sum"] == pytest.approx(TABLE_WEIGHT)
    assert boltzmann["source"] == "censo"
    assert boltzmann["method"] == "censo_table_rank1"
    assert boltzmann["temperature_k"] == pytest.approx(310.0)
    assert boltzmann["population_coverage"] == pytest.approx(1.0)
    assert boltzmann["reference"] == "RESULT/ensembles/boltzmann_table.json"


def test_engine_emits_boltzmann_provenance_for_cumulative_99(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cumulative-99 → DFT-table provenance + partial coverage in boltzmann.json."""
    _seed_task_markers(tmp_path)

    def _stub(request: ConfsearchRequest, _overlay: object) -> ProtocolOutcome:
        return ProtocolOutcome(
            records=[
                {
                    "conf_id": "CONF1",
                    "source_conf_id": "CONF1",
                    "symbols": ["O", "H", "H"],
                    "coordinates": [[0.0, 0.0, 0.0], [0.9, 0.0, 0.0], [-0.3, 0.9, 0.0]],
                    "energy_hartree": -76.01,
                    "free_energy_hartree": -76.0,
                },
                {
                    "conf_id": "CONF2",
                    "source_conf_id": "CONF2",
                    "symbols": ["O", "H", "H"],
                    "coordinates": [[0.1, 0.0, 0.0], [0.9, 0.1, 0.0], [-0.2, 0.9, 0.1]],
                    "energy_hartree": -76.00,
                    "free_energy_hartree": -75.99,
                },
            ],
            temperature_k=320.0,
            refined_conf_ids=["CONF1", "CONF2"],
            sampling={"method": "stub"},
            weight_table={"CONF1": 0.6, "CONF2": 0.4},
            weight_source="dft",
            weight_method="dft_table_cumulative99",
            population_coverage=0.87,
        )

    monkeypatch.setitem(PROTOCOL_RUNNERS, "censo-crest", _stub)

    from acp.io.structures import StructureReader

    monkeypatch.setattr(StructureReader, "read", lambda self, *a, **k: _stub_structure())
    xyz = _seed_input(tmp_path)

    request = ConfsearchRequest(
        input_source=str(xyz),
        output_dir=tmp_path,
        protocol="censo-crest",
        refinement_policy="cumulative-99",
    )
    result = ConfsearchEngine().run(request)

    assert result.status == "completed"
    assert result.quality_gates["G1"] == "PASS"
    boltzmann = json.loads(
        (tmp_path / "RESULT" / "confsearch" / "boltzmann.json").read_text(encoding="utf-8")
    )
    assert boltzmann["source"] == "dft"
    assert boltzmann["method"] == "dft_table_cumulative99"
    assert boltzmann["temperature_k"] == pytest.approx(320.0)
    assert boltzmann["population_coverage"] == pytest.approx(0.87)
    assert boltzmann["weights"] == {"conf_0001": 0.6, "conf_0002": 0.4}
    assert "reference" not in boltzmann


# --- failure path: report writer failure must surface as a failed result -----


def test_engine_surfaces_report_writer_failure_as_failed_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mol_dir = _seed_task_markers(tmp_path)
    monkeypatch.setitem(
        PROTOCOL_RUNNERS,
        "censo-crest",
        lambda request, _overlay: _provenance_outcome(request, mol_dir),
    )

    from acp.io.structures import StructureReader

    monkeypatch.setattr(StructureReader, "read", lambda self, *a, **k: _stub_structure())
    xyz = _seed_input(tmp_path)

    def _boom(*_args: object, **_kwargs: object) -> tuple[Path, Path]:
        raise RuntimeError("report writer exploded")

    monkeypatch.setattr("acp.confsearch.engine.write_final_report", _boom, raising=False)

    request = ConfsearchRequest(
        input_source=str(xyz),
        output_dir=tmp_path,
        protocol="censo-crest",
        refinement_policy="rank1",
    )
    result = ConfsearchEngine().run(request)

    assert result.status == "failed"
    assert "report writer exploded" in (result.error or "")


# --- degraded path: outcome without provenance keeps the job green -----------


def test_engine_omits_weight_table_without_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_task_markers(tmp_path)

    def _plain(_request: ConfsearchRequest, _overlay: object) -> ProtocolOutcome:
        return ProtocolOutcome(
            records=[
                {
                    "conf_id": "c1",
                    "symbols": ["O", "H", "H"],
                    "coordinates": [[0.0, 0.0, 0.0], [0.9, 0.0, 0.0], [-0.3, 0.9, 0.0]],
                    "energy_hartree": -76.01,
                    "free_energy_hartree": -76.0,
                },
            ],
            temperature_k=298.15,
            refined_conf_ids=["c1"],
            sampling={"method": "stub"},
        )

    monkeypatch.setitem(PROTOCOL_RUNNERS, "censo-crest", _plain)

    from acp.io.structures import StructureReader

    monkeypatch.setattr(StructureReader, "read", lambda self, *a, **k: _stub_structure())
    xyz = _seed_input(tmp_path)

    request = ConfsearchRequest(
        input_source=str(xyz),
        output_dir=tmp_path,
        protocol="censo-crest",
        refinement_policy="rank1",
    )
    result = ConfsearchEngine().run(request)

    assert result.status == "completed"
    payload = read_manifest(result.manifest_path)
    assert "weight_table" not in payload
    assert payload["temperature_k"] == pytest.approx(298.15)
    assert payload["report"] == {
        "json": "confsearch/final_report.json",
        "xyz": "confsearch/final_conformers.xyz",
    }
    # Degraded rank1 (D13): no table → only the physical range 0 < w <= 1.
    assert result.quality_gates["boltzmann_weights_valid"] is True
    assert result.quality_gates["G1"] == "PASS"
    assert result.quality_gates["weight_source"] is None
