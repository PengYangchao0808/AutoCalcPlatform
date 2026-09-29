"""Manifest weight-provenance regressions (plan todo 4).

Pins the additive `temperature_k` / `weight_table` / `report` keys of
``build_manifest_payload`` and the provenance kwargs of ``write_ensemble_table``
while asserting the pre-existing payload / boltzmann shapes stay intact for
callers that do not pass the new arguments (D7: additive schema only).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from acp.confsearch.contracts import ConformerEntry
from acp.confsearch.manifest import (
    build_manifest_payload,
    write_conformer_geometries,
    write_ensemble_table,
)


def _entry(index: int, weight: float) -> ConformerEntry:
    return ConformerEntry(
        conf_id=f"conf_{index:04d}",
        geometry="",
        energy_hartree=-154.0 - index * 0.001,
        free_energy_hartree=-153.9 - index * 0.001,
        relative_energy_kcal=float(index),
        boltzmann_weight=weight,
        rank=index + 1,
    )


def _records(n: int = 2) -> list[dict]:
    return [
        {
            "conf_id": f"conf_{i:04d}",
            "symbols": ["O", "H", "H"],
            "coordinates": [[0.0, 0.0, 0.0], [0.9, 0.0, 0.0], [-0.3, 0.9, 0.0]],
        }
        for i in range(n)
    ]


def _payload_kwargs() -> dict:
    return dict(
        protocol="xtb-crest",
        profile="default",
        refinement_policy="screen",
        backend="native",
        input_block={"source": "CCO", "charge": 0, "multiplicity": 1},
        sampling={"method": "crest-gfn2"},
        selected_conformers=[],
        refinement={"policy": "screen", "completed": True, "artifacts": []},
        provenance={"engine": "acp-confsearch"},
        quality_gates={"G1": "PASS"},
    )


_OLD_PAYLOAD_KEYS = {
    "schema_version",
    "workflow",
    "protocol",
    "profile",
    "refinement_policy",
    "backend",
    "input",
    "sampling",
    "conformers",
    "selected_conformers",
    "refinement",
    "provenance",
    "quality_gates",
}


def test_manifest_payload_includes_provenance_when_provided(tmp_path: Path) -> None:
    entries = [_entry(0, 0.7), _entry(1, 0.3)]
    weight_table = {
        "source": "censo",
        "method": "censo_table_rank1",
        "temperature_k": 298.15,
        "population_coverage": 1.0,
        "reference": "RESULT/ensembles/boltzmann_table.json",
    }
    report = {
        "json": "confsearch/final_report.json",
        "xyz": "confsearch/final_conformers.xyz",
    }
    payload = build_manifest_payload(
        conformers=entries,
        temperature_k=298.15,
        weight_table=weight_table,
        report=report,
        **_payload_kwargs(),
    )
    assert payload["temperature_k"] == 298.15
    assert payload["weight_table"] == weight_table
    assert payload["report"] == report
    # exact key set: old keys + exactly the three additive ones
    assert set(payload) == _OLD_PAYLOAD_KEYS | {"temperature_k", "weight_table", "report"}


def test_manifest_payload_omits_provenance_keys_when_unset() -> None:
    """Old callers keep exactly the old shape (no additive keys at all)."""
    entries = [_entry(0, 1.0)]
    payload = build_manifest_payload(conformers=entries, **_payload_kwargs())
    assert set(payload) == _OLD_PAYLOAD_KEYS
    assert "temperature_k" not in payload
    assert "weight_table" not in payload
    assert "report" not in payload


def test_manifest_payload_non_dict_weight_table_omitted_with_warning(
    tmp_path: Path, caplog
) -> None:
    """Malformed weight_table must be omitted (with a warning), never crash."""
    entries = [_entry(0, 1.0)]
    with caplog.at_level(logging.WARNING, logger="acp.confsearch.manifest"):
        payload = build_manifest_payload(
            conformers=entries,
            temperature_k=298.15,
            weight_table=["bad"],  # type: ignore[arg-type]
            report={
                "json": "confsearch/final_report.json",
                "xyz": "confsearch/final_conformers.xyz",
            },
            **_payload_kwargs(),
        )
    assert "weight_table" not in payload
    assert payload["temperature_k"] == 298.15
    assert payload["report"] == {
        "json": "confsearch/final_report.json",
        "xyz": "confsearch/final_conformers.xyz",
    }
    assert any("weight_table" in record.getMessage() for record in caplog.records)


def test_boltzmann_json_provenance_keys(tmp_path: Path) -> None:
    entries = [_entry(0, 0.7), _entry(1, 0.3)]
    for i, e in enumerate(entries):
        e.conf_id = f"conf_{i + 1:04d}"
    write_conformer_geometries(tmp_path, entries, _records(2))
    write_ensemble_table(
        tmp_path,
        entries,
        temperature_k=298.15,
        weight_source="censo",
        weight_method="censo_table_rank1",
        population_coverage=1.0,
        reference="RESULT/ensembles/boltzmann_table.json",
    )
    boltzmann = json.loads((tmp_path / "boltzmann.json").read_text(encoding="utf-8"))
    assert set(boltzmann) == {
        "weights",
        "weight_sum",
        "temperature_k",
        "source",
        "method",
        "population_coverage",
        "reference",
    }
    assert boltzmann["weights"] == {"conf_0001": 0.7, "conf_0002": 0.3}
    assert boltzmann["weight_sum"] == 1.0
    assert boltzmann["temperature_k"] == 298.15
    assert boltzmann["source"] == "censo"
    assert boltzmann["method"] == "censo_table_rank1"
    assert boltzmann["population_coverage"] == 1.0
    assert boltzmann["reference"] == "RESULT/ensembles/boltzmann_table.json"


def test_boltzmann_json_old_shape_without_kwargs(tmp_path: Path) -> None:
    entries = [_entry(0, 0.7), _entry(1, 0.3)]
    for i, e in enumerate(entries):
        e.conf_id = f"conf_{i + 1:04d}"
    write_conformer_geometries(tmp_path, entries, _records(2))
    write_ensemble_table(tmp_path, entries)
    boltzmann = json.loads((tmp_path / "boltzmann.json").read_text(encoding="utf-8"))
    assert set(boltzmann) == {"weights", "weight_sum"}
    assert boltzmann["weights"] == {"conf_0001": 0.7, "conf_0002": 0.3}
    assert boltzmann["weight_sum"] == 1.0


def test_boltzmann_json_partial_kwargs_only_adds_given_keys(tmp_path: Path) -> None:
    entries = [_entry(0, 1.0)]
    write_conformer_geometries(tmp_path, entries, _records(1))
    write_ensemble_table(tmp_path, entries, temperature_k=273.15)
    boltzmann = json.loads((tmp_path / "boltzmann.json").read_text(encoding="utf-8"))
    assert set(boltzmann) == {"weights", "weight_sum", "temperature_k"}
    assert boltzmann["temperature_k"] == 273.15


def test_ensemble_outputs_shape_unchanged_by_provenance(tmp_path: Path) -> None:
    """ensemble.xyz / ensemble.csv / energies.json must be byte-identical
    whether or not provenance kwargs are passed (only boltzmann.json gains
    the additive keys)."""
    plain = tmp_path / "plain"
    provenance = tmp_path / "provenance"
    written: dict[str, list[ConformerEntry]] = {}
    for name, target in (("plain", plain), ("provenance", provenance)):
        target.mkdir()
        entries = [_entry(0, 0.7), _entry(1, 0.3)]
        for i, e in enumerate(entries):
            e.conf_id = f"conf_{i + 1:04d}"
        write_conformer_geometries(target, entries, _records(2))
        written[name] = entries
    write_ensemble_table(plain, written["plain"])
    write_ensemble_table(
        provenance,
        written["provenance"],
        temperature_k=298.15,
        weight_source="dft",
        weight_method="dft_table",
        population_coverage=0.99,
        reference="RESULT/ensembles/boltzmann_table.json",
    )
    for name in ("ensemble.xyz", "ensemble.csv", "energies.json"):
        assert (plain / name).is_file(), name
        assert (provenance / name).is_file(), name
        assert (plain / name).read_bytes() == (provenance / name).read_bytes(), name
