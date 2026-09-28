"""Tests for ``acp.confsearch.report`` — final report writer + merge registration.

Covers plan todo 6 (confsearch-weight-provenance-final-report):
``final_report.json`` schema ``confsearch_final_report_v1``,
``final_conformers.xyz`` frames, and idempotent merge registration into
``RESULT/result_manifest.json``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from acp.confsearch.contracts import ConformerEntry, ConfsearchRequest, ProtocolOutcome
from acp.confsearch.report import register_final_report, write_final_report
from acp.storage.manifest import MANIFEST_FILENAME, ProductKind, ResultManifest

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _make_request(
    tmp_path: Path,
    protocol: str = "censo-crest",
    refinement_policy: str = "cumulative-99",
) -> ConfsearchRequest:
    return ConfsearchRequest(
        input_source="CCO",
        output_dir=tmp_path,
        protocol=protocol,
        profile="default",
        refinement_policy=refinement_policy,
    )


def _write_geometry(confsearch_dir: Path, conf_id: str, energy: float) -> str:
    """Materialize ``conformers/<conf_id>.xyz`` and return the relative ref."""
    rel = f"conformers/{conf_id}.xyz"
    path = confsearch_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"3\n{conf_id} E={energy}\nO 0.0 0.0 0.0\nC 1.4 0.0 0.0\nH 0.0 1.0 0.0\n",
        encoding="utf-8",
    )
    return rel


def _make_outcome(**overrides: object) -> ProtocolOutcome:
    values: dict = {
        "records": [],
        "temperature_k": 310.15,
        "refined_conf_ids": ["conf_0001"],
        "workflow_metadata": {
            "temperature_k": 310.15,
            "population_coverage": 0.97,
            "weight_source": "dft",
            "weight_method": "dft_thermo_table",
            "total_gibbs_hartree": -101.75,
            "total_gibbs_kcal_mol": -63.85,
            "boltzmann_table_json": "RESULT/ensembles/boltzmann_table.json",
        },
        "weight_table": {"CONF1": 0.61, "CONF2": 0.39},
        "weight_source": "dft",
        "weight_method": "dft_thermo_table",
        "population_coverage": 0.97,
        "energy_kind": "censo",
    }
    values.update(overrides)
    return ProtocolOutcome(**values)


def _entry(conf_id: str, geometry: str, *, rank: int, **overrides: object) -> ConformerEntry:
    values: dict = {
        "conf_id": conf_id,
        "geometry": geometry,
        "energy_hartree": -101.2,
        "free_energy_hartree": -100.5,
        "relative_energy_kcal": 0.0,
        "boltzmann_weight": 0.61,
        "rank": rank,
        "source_conf_id": None,
        "weight_source": None,
        "refined": False,
    }
    values.update(overrides)
    return ConformerEntry(**values)


def _standard_entries(confsearch_dir: Path) -> list[ConformerEntry]:
    """Two entries passed out of rank order; rank 1 refined (dft), rank 2 censo."""
    geo1 = _write_geometry(confsearch_dir, "conf_0001", -101.2)
    geo2 = _write_geometry(confsearch_dir, "conf_0002", -101.1)
    return [
        _entry(
            "conf_0002",
            geo2,
            rank=2,
            source_conf_id="CONF2",
            weight_source="censo",
            boltzmann_weight=0.39,
        ),
        _entry(
            "conf_0001",
            geo1,
            rank=1,
            refined=True,
            source_conf_id="CONF1",
            boltzmann_weight=0.61,
        ),
    ]


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _xyz_titles(path: Path) -> list[str]:
    titles: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        n_atoms = int(lines[i].strip())
        titles.append(lines[i + 1])
        i += 2 + n_atoms
    return titles


# --------------------------------------------------------------------------- #
# write_final_report — JSON schema
# --------------------------------------------------------------------------- #


def test_final_report_schema_keys_and_values(tmp_path: Path) -> None:
    confsearch_dir = tmp_path / "RESULT" / "confsearch"
    confsearch_dir.mkdir(parents=True)
    entries = _standard_entries(confsearch_dir)
    outcome = _make_outcome()
    request = _make_request(tmp_path)

    report_path, xyz_path = write_final_report(
        confsearch_dir, request=request, entries=entries, outcome=outcome
    )

    assert report_path == confsearch_dir / "final_report.json"
    assert xyz_path == confsearch_dir / "final_conformers.xyz"
    assert report_path.is_file() and xyz_path.is_file()

    payload = _read_json(report_path)
    assert payload["schema_version"] == "confsearch_final_report_v1"
    assert payload["workflow"] == "Confsearch"
    assert payload["protocol"] == "censo-crest"
    assert payload["profile"] == "default"
    assert payload["refinement_policy"] == "cumulative-99"
    assert payload["temperature_k"] == 310.15
    assert payload["weight_table"] == {
        "source": "dft",
        "method": "dft_thermo_table",
        "population_coverage": 0.97,
        "reference": "RESULT/ensembles/boltzmann_table.json",
    }
    assert payload["total_gibbs_hartree"] == -101.75
    assert payload["total_gibbs_kcal_mol"] == -63.85

    conformers = payload["conformers"]
    assert len(conformers) == 2
    expected_keys = {
        "conf_id",
        "source_conf_id",
        "rank",
        "refined",
        "geometry",
        "energy_hartree",
        "free_energy_hartree",
        "energy_kind",
        "relative_energy_kcal",
        "boltzmann_weight",
        "weight_source",
    }
    for row in conformers:
        assert set(row) == expected_keys

    by_id = {row["conf_id"]: row for row in conformers}
    refined = by_id["conf_0001"]
    assert refined["refined"] is True
    assert refined["energy_kind"] == "dft"
    assert refined["geometry"] == "conformers/conf_0001.xyz"
    assert refined["source_conf_id"] == "CONF1"
    assert refined["rank"] == 1
    assert refined["energy_hartree"] == -101.2
    assert refined["free_energy_hartree"] == -100.5
    assert refined["relative_energy_kcal"] == 0.0
    assert refined["boltzmann_weight"] == 0.61
    # entry.weight_source is None → falls back to outcome.weight_source
    assert refined["weight_source"] == "dft"

    screened = by_id["conf_0002"]
    assert screened["refined"] is False
    assert screened["energy_kind"] == "censo"  # outcome.energy_kind
    assert screened["weight_source"] == "censo"  # entry-level wins
    assert screened["source_conf_id"] == "CONF2"


def test_final_report_omits_gibbs_keys_when_absent(tmp_path: Path) -> None:
    confsearch_dir = tmp_path / "RESULT" / "confsearch"
    confsearch_dir.mkdir(parents=True)
    entries = _standard_entries(confsearch_dir)
    outcome = _make_outcome()
    outcome.workflow_metadata.pop("total_gibbs_hartree")
    outcome.workflow_metadata.pop("total_gibbs_kcal_mol")

    report_path, _ = write_final_report(
        confsearch_dir,
        request=_make_request(tmp_path),
        entries=entries,
        outcome=outcome,
    )
    payload = _read_json(report_path)
    assert "total_gibbs_hartree" not in payload
    assert "total_gibbs_kcal_mol" not in payload


def test_final_report_energy_kind_defaults_by_protocol(tmp_path: Path) -> None:
    confsearch_dir = tmp_path / "RESULT" / "confsearch"
    confsearch_dir.mkdir(parents=True)
    geo = _write_geometry(confsearch_dir, "conf_0001", -5.5)
    entries = [_entry("conf_0001", geo, rank=1)]
    outcome = _make_outcome(energy_kind=None)

    report_path, _ = write_final_report(
        confsearch_dir,
        request=_make_request(tmp_path, protocol="xtb-crest", refinement_policy="screen"),
        entries=entries,
        outcome=outcome,
    )
    payload = _read_json(report_path)
    assert payload["conformers"][0]["energy_kind"] == "xtb"
    assert payload["refinement_policy"] == "screen"


# --------------------------------------------------------------------------- #
# write_final_report — XYZ frames
# --------------------------------------------------------------------------- #


def test_final_conformers_xyz_frames_in_rank_order(tmp_path: Path) -> None:
    confsearch_dir = tmp_path / "RESULT" / "confsearch"
    confsearch_dir.mkdir(parents=True)
    entries = _standard_entries(confsearch_dir)  # passed rank 2 first

    _, xyz_path = write_final_report(
        confsearch_dir,
        request=_make_request(tmp_path),
        entries=entries,
        outcome=_make_outcome(),
    )

    titles = _xyz_titles(xyz_path)
    assert len(titles) == 2
    assert titles[0] == "conf_0001 G=-100.5 w=0.61 src=dft refined"
    assert titles[1] == "conf_0002 G=-100.5 w=0.39 src=censo"

    # frame body preserved verbatim from the source geometry
    text = xyz_path.read_text(encoding="utf-8")
    assert "O 0.0 0.0 0.0" in text
    assert "C 1.4 0.0 0.0" in text


def test_final_conformers_xyz_skips_missing_geometry_but_raises_when_all_missing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    confsearch_dir = tmp_path / "RESULT" / "confsearch"
    confsearch_dir.mkdir(parents=True)
    geo1 = _write_geometry(confsearch_dir, "conf_0001", -101.2)
    entries = [
        _entry("conf_0001", geo1, rank=1, refined=True),
        _entry("conf_0002", "conformers/conf_0002.xyz", rank=2),
        _entry("conf_0003", "", rank=3),  # empty geometry ref
    ]

    with caplog.at_level(logging.WARNING, logger="acp.confsearch.report"):
        _, xyz_path = write_final_report(
            confsearch_dir,
            request=_make_request(tmp_path),
            entries=entries,
            outcome=_make_outcome(),
        )
    assert len(_xyz_titles(xyz_path)) == 1
    assert any("conf_0002" in record.message for record in caplog.records)
    assert any("conf_0003" in record.message for record in caplog.records)

    # ALL geometries missing → raise
    missing_all = tmp_path / "missing_all" / "RESULT" / "confsearch"
    missing_all.mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="geometry"):
        write_final_report(
            missing_all,
            request=_make_request(tmp_path),
            entries=[_entry("conf_0001", "conformers/conf_0001.xyz", rank=1)],
            outcome=_make_outcome(),
        )


def test_all_missing_geometries_raise_without_orphan_final_report(tmp_path: Path) -> None:
    """A failed XYZ write must not leave an unregistered ``final_report.json``."""
    confsearch_dir = tmp_path / "RESULT" / "confsearch"
    confsearch_dir.mkdir(parents=True)
    geo1 = _write_geometry(confsearch_dir, "conf_0001", -101.2)
    entries = [_entry("conf_0001", geo1, rank=1, refined=True)]
    (confsearch_dir / geo1).unlink()  # all geometries deleted

    with pytest.raises(FileNotFoundError, match="geometry"):
        write_final_report(
            confsearch_dir,
            request=_make_request(tmp_path),
            entries=entries,
            outcome=_make_outcome(),
        )

    assert not (confsearch_dir / "final_report.json").exists()
    assert not (confsearch_dir / "final_conformers.xyz").exists()
    assert sorted(path.name for path in confsearch_dir.iterdir()) == ["conformers"]


def test_failed_json_write_leaves_no_orphan_xyz(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed JSON write must clean up the already-written XYZ (pair atomicity)."""
    confsearch_dir = tmp_path / "RESULT" / "confsearch"
    confsearch_dir.mkdir(parents=True)
    entries = _standard_entries(confsearch_dir)

    def _boom(path: Path, payload: dict) -> Path:
        raise OSError("simulated JSON write failure")

    monkeypatch.setattr("acp.confsearch.report.write_json_atomic", _boom)

    with pytest.raises(OSError, match="simulated JSON write failure"):
        write_final_report(
            confsearch_dir,
            request=_make_request(tmp_path),
            entries=entries,
            outcome=_make_outcome(),
        )

    assert not (confsearch_dir / "final_conformers.xyz").exists()
    assert not (confsearch_dir / "final_report.json").exists()
    assert sorted(path.name for path in confsearch_dir.iterdir()) == ["conformers"]


# --------------------------------------------------------------------------- #
# register_final_report — merge registration
# --------------------------------------------------------------------------- #


def _write_report_artifacts(confsearch_dir: Path) -> tuple[Path, Path]:
    report_path = confsearch_dir / "final_report.json"
    xyz_path = confsearch_dir / "final_conformers.xyz"
    report_path.write_text("{}", encoding="utf-8")
    xyz_path.write_text("1\ntitle\nX 0.0 0.0 0.0\n", encoding="utf-8")
    return report_path, xyz_path


def test_register_final_report_fresh_manifest(tmp_path: Path) -> None:
    mol_dir = tmp_path / "mol"
    confsearch_dir = mol_dir / "RESULT" / "confsearch"
    confsearch_dir.mkdir(parents=True)
    report_path, xyz_path = _write_report_artifacts(confsearch_dir)

    register_final_report(mol_dir, report_path, xyz_path)

    manifest = ResultManifest.read(mol_dir / "RESULT")
    assert manifest.workflow == "Confsearch"
    assert manifest.status == "completed"
    by_id = {product.id: product for product in manifest.products}
    assert by_id["confsearch_final_report"].path == "confsearch/final_report.json"
    assert by_id["confsearch_final_report"].kind is ProductKind.REPORT
    assert by_id["confsearch_final_conformers"].path == "confsearch/final_conformers.xyz"
    assert by_id["confsearch_final_conformers"].kind is ProductKind.STRUCTURE


def test_register_final_report_merges_and_preserves_header(tmp_path: Path) -> None:
    mol_dir = tmp_path / "mol"
    confsearch_dir = mol_dir / "RESULT" / "confsearch"
    confsearch_dir.mkdir(parents=True)
    report_path, xyz_path = _write_report_artifacts(confsearch_dir)

    seeded = ResultManifest(task_id="job-123", workflow="energy", status="completed")
    seeded.add_product(
        "existing_thermo",
        "Ensemble thermo",
        "energies/ensemble_thermo.json",
        ProductKind.ENERGY_REPORT,
    )
    seeded.write(mol_dir / "RESULT")

    register_final_report(mol_dir, report_path, xyz_path)

    merged = ResultManifest.read(mol_dir / "RESULT")
    assert merged.task_id == "job-123"
    assert merged.workflow == "energy"
    assert merged.status == "completed"
    by_id = {product.id: product for product in merged.products}
    assert by_id["existing_thermo"].kind is ProductKind.ENERGY_REPORT
    assert by_id["confsearch_final_report"].kind is ProductKind.REPORT
    assert by_id["confsearch_final_conformers"].kind is ProductKind.STRUCTURE
    assert set(by_id) == {
        "existing_thermo",
        "confsearch_final_report",
        "confsearch_final_conformers",
    }


def test_register_final_report_is_idempotent(tmp_path: Path) -> None:
    mol_dir = tmp_path / "mol"
    confsearch_dir = mol_dir / "RESULT" / "confsearch"
    confsearch_dir.mkdir(parents=True)
    report_path, xyz_path = _write_report_artifacts(confsearch_dir)

    register_final_report(mol_dir, report_path, xyz_path)
    manifest_path = mol_dir / "RESULT" / MANIFEST_FILENAME
    first = json.loads(manifest_path.read_text(encoding="utf-8"))
    register_final_report(mol_dir, report_path, xyz_path)
    second = json.loads(manifest_path.read_text(encoding="utf-8"))

    ids = [product["id"] for product in second["products"]]
    assert len(ids) == len(set(ids)) == 2
    assert second == first


def test_register_final_report_missing_artifact_raises(tmp_path: Path) -> None:
    mol_dir = tmp_path / "mol"
    (mol_dir / "RESULT" / "confsearch").mkdir(parents=True)
    report_path, xyz_path = _write_report_artifacts(mol_dir / "RESULT" / "confsearch")
    xyz_path.unlink()

    with pytest.raises(FileNotFoundError, match="final_conformers.xyz"):
        register_final_report(mol_dir, report_path, xyz_path)
    assert not (mol_dir / "RESULT" / MANIFEST_FILENAME).exists()
