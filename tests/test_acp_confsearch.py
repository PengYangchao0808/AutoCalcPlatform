"""Confsearch package tests: contracts, selection, manifest, engine (plan M1-M3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp.confsearch import (
    BACKENDS,
    PROFILES,
    PROTOCOLS,
    REFINEMENT_POLICIES,
    ConformerEntry,
    ConfsearchEngine,
    ConfsearchRequest,
    validate_request,
)
from acp.confsearch.manifest import (
    read_manifest,
    representative_conformer,
    write_conformer_geometries,
    write_ensemble_table,
)
from acp.confsearch.selection import select_for_refinement, threshold_for_policy


def _entry(index: int, weight: float) -> ConformerEntry:
    return ConformerEntry(
        conf_id=f"conf_{index:04d}",
        geometry="",
        energy_hartree=-154.0 - index * 0.001,
        boltzmann_weight=weight,
        rank=index + 1,
    )


# --- contracts ------------------------------------------------------------


def test_protocol_constants_are_the_plan_set() -> None:
    assert PROTOCOLS == ("xtb-crest", "xtb-md", "censo-crest", "xtbmd-censo")
    assert PROFILES == ("light", "default", "high")
    assert REFINEMENT_POLICIES == ("screen", "rank1", "cumulative-99", "all")
    assert BACKENDS == ("native",)


def test_rph_parity_rejected_as_retired() -> None:
    request = ConfsearchRequest(
        input_source="CCO",
        output_dir=Path("."),
        protocol="censo-crest",
        backend="rph-parity",
    )
    with pytest.raises(ValueError, match="RPH 已退役"):
        validate_request(request)


def test_unknown_protocol_rejected() -> None:
    request = ConfsearchRequest(input_source="CCO", output_dir=Path("."), protocol="censo-md")
    with pytest.raises(ValueError, match="Unknown protocol"):
        validate_request(request)


# --- selection (§3.3) -------------------------------------------------------


def test_policy_selection_semantics() -> None:
    entries = [_entry(0, 0.7), _entry(1, 0.2), _entry(2, 0.09), _entry(3, 0.01)]
    assert select_for_refinement("screen", entries) == []
    assert select_for_refinement("rank1", entries) == ["conf_0000"]
    assert select_for_refinement("all", entries) == [e.conf_id for e in entries]
    assert len(select_for_refinement("cumulative-99", entries)) == 3
    assert threshold_for_policy("all") == 1.0
    assert threshold_for_policy("cumulative-99") == 0.99
    assert threshold_for_policy("rank1") is None


def test_cumulative_policy_keeps_at_least_rank1() -> None:
    entries = [_entry(0, 1.0), _entry(1, 0.0)]
    assert select_for_refinement("cumulative-99", entries) == ["conf_0000"]


# --- manifest io (§5) ------------------------------------------------------


def _records(n: int = 2) -> list[dict]:
    return [
        {
            "conf_id": f"conf_{i:04d}",
            "symbols": ["O", "H", "H"],
            "coordinates": [[0.0, 0.0, 0.0], [0.9, 0.0, 0.0], [-0.3, 0.9, 0.0]],
        }
        for i in range(n)
    ]


def test_manifest_roundtrip_and_representative(tmp_path: Path) -> None:
    from acp.confsearch.manifest import build_manifest_payload, write_manifest

    entries = [_entry(0, 0.7), _entry(1, 0.3)]
    for i, e in enumerate(entries):
        e.conf_id = f"conf_{i + 1:04d}"
    write_conformer_geometries(tmp_path, entries, _records(2))
    write_ensemble_table(tmp_path, entries)
    payload = build_manifest_payload(
        protocol="xtb-crest",
        profile="default",
        refinement_policy="screen",
        backend="native",
        input_block={"source": "CCO", "charge": 0, "multiplicity": 1},
        sampling={"method": "crest-gfn2"},
        conformers=entries,
        selected_conformers=[],
        refinement={"policy": "screen", "completed": True, "artifacts": []},
        provenance={"engine": "acp-confsearch"},
        quality_gates={"G1": "PASS"},
    )
    path = write_manifest(tmp_path, payload)

    loaded = read_manifest(path)
    assert loaded["schema_version"] == "confsearch_v1"
    assert loaded["workflow"] == "Confsearch"
    assert loaded["conformers"][0]["geometry"] == "conformers/conf_0001.xyz"
    assert (tmp_path / "ensemble.xyz").is_file()
    assert (tmp_path / "ensemble.csv").is_file()
    assert (tmp_path / "energies.json").is_file()
    assert (tmp_path / "boltzmann.json").is_file()
    assert json.loads((tmp_path / "boltzmann.json").read_text())["weights"] == {
        "conf_0001": 0.7,
        "conf_0002": 0.3,
    }

    conf_id, geometry = representative_conformer(path)
    assert conf_id == "conf_0001"
    assert geometry.is_file()


def test_read_manifest_rejects_foreign_schema(tmp_path: Path) -> None:
    bad = tmp_path / "confsearch_manifest.json"
    bad.write_text(json.dumps({"schema_version": "bogus", "workflow": "Confsearch"}))
    with pytest.raises(ValueError, match="schema_version"):
        read_manifest(bad)


# --- pairing + gate regressions ---------------------------------------------


def test_refinement_block_collects_boltzmann_table(tmp_path: Path) -> None:
    """The artifact key must match write_final_outputs' boltzmann_table_json."""
    from acp.confsearch.contracts import ProtocolOutcome
    from acp.confsearch.result_helpers import refinement_block

    request = ConfsearchRequest(
        input_source="CCO",
        output_dir=tmp_path,
        protocol="censo-crest",
        refinement_policy="rank1",
    )
    outcome = ProtocolOutcome(
        records=[],
        temperature_k=298.15,
        refined_conf_ids=["CONF1"],
        workflow_metadata={
            "boltzmann_table_json": "/p/boltzmann_table.json",
            "thermo_csv": "/p/conformer_thermo.csv",
        },
    )
    block = refinement_block(request, outcome, ["conf_0001"])
    assert "/p/boltzmann_table.json" in block["artifacts"]
    assert block["completed"] is True


def test_quality_gates_pure_xtb_protocol_exempt() -> None:
    """Pure-xTB protocols never refine → selected ids must not fail G1."""
    from acp.confsearch.contracts import ProtocolOutcome
    from acp.confsearch.result_helpers import quality_gates

    entries = [_entry(0, 1.0)]
    outcome = ProtocolOutcome(records=[], temperature_k=298.15, sampling={"method": "stub"})
    censo = quality_gates(entries, outcome, ["conf_0001"], protocol="censo-crest")
    assert censo["refinement_consistent"] is False
    xtb = quality_gates(entries, outcome, ["conf_0001"], protocol="xtb-md")
    assert xtb["refinement_consistent"] is True
    assert xtb["G1"] == "PASS"


# --- engine (delegated protocol → unified tree) -----------------------------


class _StubOutcome:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path

    def __call__(self, request: ConfsearchRequest, overlay: dict) -> object:
        from acp.confsearch.contracts import ProtocolOutcome

        return ProtocolOutcome(
            records=[
                {
                    "conf_id": "c1",
                    "symbols": ["O", "H", "H"],
                    "coordinates": [[0.0, 0.0, 0.0], [0.9, 0.0, 0.0], [-0.3, 0.9, 0.0]],
                    "energy_hartree": -76.01,
                    "free_energy_hartree": -76.0,
                    "weight": 0.75,
                },
                {
                    "conf_id": "c2",
                    "symbols": ["O", "H", "H"],
                    "coordinates": [[0.1, 0.0, 0.0], [0.9, 0.1, 0.0], [-0.2, 0.9, 0.1]],
                    "energy_hartree": -76.00,
                    "free_energy_hartree": -75.99,
                    "weight": 0.25,
                },
            ],
            temperature_k=298.15,
            refined_conf_ids=["c1"] if request.refinement_policy == "rank1" else [],
            sampling={"method": "stub"},
        )


class _StubOutcomeUnranked(_StubOutcome):
    """Records arrive in high-energy-first order (gtot ≠ DFT-rank order)."""

    def __call__(self, request: ConfsearchRequest, overlay: dict) -> object:
        outcome = super().__call__(request, overlay)
        outcome.records.reverse()
        return outcome


def test_engine_geometry_follows_ranked_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """conf_0001.xyz must hold the LOWEST free-energy geometry, not records[0]."""
    (tmp_path / "job.json").write_text("{}", encoding="utf-8")
    (tmp_path / "task.json").write_text("{}", encoding="utf-8")
    monkeypatch.setitem(
        __import__("acp.confsearch.protocols", fromlist=["PROTOCOL_RUNNERS"]).PROTOCOL_RUNNERS,
        "censo-crest",
        _StubOutcomeUnranked(tmp_path),
    )

    from acp.io.structures import StructureReader

    xyz = tmp_path / "water.xyz"
    xyz.write_text("3\nwater\nO 0 0 0\nH 0.9 0 0\nH -0.3 0.9 0\n", encoding="utf-8")
    monkeypatch.setattr(StructureReader, "read", lambda self, *a, **k: _stub_structure())

    request = ConfsearchRequest(
        input_source=str(xyz),
        output_dir=tmp_path,
        protocol="censo-crest",
        refinement_policy="rank1",
    )
    result = ConfsearchEngine().run(request)
    assert result.status == "completed"

    confsearch_dir = tmp_path / "RESULT" / "confsearch"
    first_atom = (
        confsearch_dir.joinpath(*Path(result.conformers[0].geometry).parts)
        .read_text()
        .splitlines()[2]
        .split()
    )
    # c1 (free energy -76.0) has the record order put LAST; its O sits at x=0.0
    assert first_atom[0] == "O"
    assert float(first_atom[1]) == pytest.approx(0.0)


def test_engine_writes_unified_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "job.json").write_text("{}", encoding="utf-8")
    (tmp_path / "task.json").write_text("{}", encoding="utf-8")
    monkeypatch.setitem(
        __import__("acp.confsearch.protocols", fromlist=["PROTOCOL_RUNNERS"]).PROTOCOL_RUNNERS,
        "censo-crest",
        _StubOutcome(tmp_path),
    )

    from acp.io.structures import StructureReader

    xyz = tmp_path / "water.xyz"
    xyz.write_text("3\nwater\nO 0.0 0.0 0.0\nH 0.9 0.0 0.0\nH -0.3 0.9 0.0\n", encoding="utf-8")
    monkeypatch.setattr(StructureReader, "read", lambda self, *a, **k: _stub_structure())

    request = ConfsearchRequest(
        input_source=str(xyz),
        output_dir=tmp_path,
        protocol="censo-crest",
        refinement_policy="rank1",
    )
    result = ConfsearchEngine().run(request)

    assert result.status == "completed"
    assert result.manifest_path is not None
    assert result.manifest_path.parent == tmp_path / "RESULT" / "confsearch"
    payload = read_manifest(result.manifest_path)
    assert payload["selected_conformers"] == ["conf_0001"]
    assert [c["conf_id"] for c in payload["conformers"]] == ["conf_0001", "conf_0002"]
    assert payload["conformers"][0]["rank"] == 1
    assert payload["refinement"]["policy"] == "rank1"
    assert payload["quality_gates"]["G1"] == "PASS"


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


def test_engine_reports_protocol_failure_as_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(request: ConfsearchRequest, overlay: dict) -> object:
        raise RuntimeError("crest exploded")

    monkeypatch.setitem(
        __import__("acp.confsearch.protocols", fromlist=["PROTOCOL_RUNNERS"]).PROTOCOL_RUNNERS,
        "xtb-crest",
        _boom,
    )
    request = ConfsearchRequest(
        input_source="CCO",
        output_dir=tmp_path,
        protocol="xtb-crest",
    )
    result = ConfsearchEngine().run(request)
    assert result.status == "failed"
    assert "crest exploded" in (result.error or "")


# --- RPH retirement (Wave 8, todo 46) --------------------------------------


def test_rph_provider_retired() -> None:
    """provider_backend='rph' → ValueError containing 'RPH 已退役' (zero mechanism refs)."""
    for backend_value in ("rph", "rph-parity"):
        request = ConfsearchRequest(
            input_source="CCO",
            output_dir=Path("."),
            protocol="censo-crest",
            backend=backend_value,
        )
        with pytest.raises(ValueError, match="RPH 已退役"):
            validate_request(request)


def test_native_default_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default NATIVE backend smoke — no mechanism imports, protocol stub."""
    (tmp_path / "job.json").write_text("{}", encoding="utf-8")
    (tmp_path / "task.json").write_text("{}", encoding="utf-8")
    monkeypatch.setitem(
        __import__("acp.confsearch.protocols", fromlist=["PROTOCOL_RUNNERS"]).PROTOCOL_RUNNERS,
        "censo-crest",
        _StubOutcome(tmp_path),
    )

    from acp.io.structures import StructureReader

    xyz = tmp_path / "water.xyz"
    xyz.write_text("3\nwater\nO 0.0 0.0 0.0\nH 0.9 0.0 0.0\nH -0.3 0.9 0.0\n", encoding="utf-8")
    monkeypatch.setattr(StructureReader, "read", lambda self, *a, **k: _stub_structure())

    request = ConfsearchRequest(
        input_source=str(xyz),
        output_dir=tmp_path,
        protocol="censo-crest",
        backend="native",
        refinement_policy="screen",
    )
    result = ConfsearchEngine().run(request)
    assert result.status == "completed"
    assert result.manifest_path is not None
