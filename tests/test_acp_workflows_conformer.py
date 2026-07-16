"""Tests for the ACP conformer workflow wrapper.

The wrapper delegates the full protocol pipeline to the authoritative
``ConformerEngine.run()`` and reconstructs an ACP ensemble from the engine's
``all_conformers.xyz`` output. These tests isolate that delegation with a fake
engine.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import acp.workflows.conformer as conformer_workflow
from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.core.workflow import WorkflowResult
from acp.io.structures import InputFormat
from conformer_search.config import _get_default_config
from conformer_search.core.protocols import resolve_protocol_spec

# ---------------------------------------------------------------------------
# get_protocol_stages
# ---------------------------------------------------------------------------


def test_get_protocol_stages_ext_has_full_pipeline():
    stages = conformer_workflow.get_protocol_stages("ext", config=_get_default_config())
    names = [stage.name for stage in stages]
    assert names[0] == "embed_smiles"
    assert "crest_search" in names
    assert "isostat_cluster" in names
    assert "dft_optimize" in names
    assert "single_point" in names
    assert "shermo_thermo" in names


def test_get_protocol_stages_zero_has_minimal_pipeline():
    stages = conformer_workflow.get_protocol_stages("zero", config=_get_default_config())
    names = [stage.name for stage in stages]
    assert "embed_smiles" in names
    assert "crest_search" in names
    assert "isostat_cluster" in names
    assert "single_point" in names


@pytest.mark.parametrize("protocol", ["ext", "full", "lite", "zero", "benchmark"])
def test_get_protocol_stages_all_authoritative_protocols_resolve(protocol):
    stages = conformer_workflow.get_protocol_stages(protocol, config=_get_default_config())
    assert len(stages) >= 1
    assert all(stage.name for stage in stages)


def test_get_protocol_stages_default_alias_resolves_to_configured():
    config = _get_default_config()
    stages = conformer_workflow.get_protocol_stages("default", config=config)
    assert len(stages) >= 1


# ---------------------------------------------------------------------------
# boltzmann_weight_ensemble
# ---------------------------------------------------------------------------


def test_boltzmann_weight_ensemble_uses_lowest_free_energy():
    ensemble = StructureEnsemble(
        records=[
            StructureRecord(
                structure=Structure(
                    id="conf_000",
                    symbols=["C"],
                    coordinates=np.array([[0.0, 0.0, 0.0]]),
                ),
                energy_hartree=-10.0,
                free_energy_hartree=-10.1,
            ),
            StructureRecord(
                structure=Structure(
                    id="conf_001",
                    symbols=["C"],
                    coordinates=np.array([[1.0, 0.0, 0.0]]),
                ),
                energy_hartree=-10.0,
                free_energy_hartree=-10.0,
            ),
        ]
    )

    conformer_workflow.boltzmann_weight_ensemble(ensemble)

    assert ensemble.records[0].weight is not None
    assert ensemble.records[1].weight is not None
    assert ensemble.records[0].weight > ensemble.records[1].weight
    assert (ensemble.records[0].weight + ensemble.records[1].weight) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# run_conformer_search — delegation to ConformerEngine.run()
# ---------------------------------------------------------------------------


class _FakeEngine:
    """Test double for the authoritative ConformerEngine."""

    instances: list[_FakeEngine] = []

    def __init__(
        self, config, work_dir, molecule_name, protocol="ext", protocol_spec=None, levels=None
    ):
        self.config = config
        self.molecule_name = molecule_name
        self.protocol = protocol
        self.levels = levels
        self.protocol_spec = protocol_spec or resolve_protocol_spec(config, protocol, levels=levels)
        self.work_dir = Path(work_dir) / molecule_name
        self.final_dft_dir = self.work_dir / "finalDFT"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.final_dft_dir.mkdir(parents=True, exist_ok=True)
        self.run_calls: list[object] = []
        self.last_molecular_input = None
        self.__class__.instances.append(self)

    def run(self, molecular_input):
        self.run_calls.append(molecular_input)
        self.last_molecular_input = molecular_input

        global_min_xyz = self.work_dir / f"{self.molecule_name}_global_min.xyz"
        global_min_xyz.write_text("global minimum\n", encoding="utf-8")

        # Write a 2-frame multi-frame XYZ exactly as the authoritative
        # _finalize_results does (atom-count / title / coordinate lines).
        all_conformers = self.final_dft_dir / "all_conformers.xyz"
        frame = (
            "1\nConformer 0, E=-10.000000\nC 0.0 0.0 0.0\n"
            "1\nConformer 1, E=-9.990000\nC 1.0 0.0 0.0\n"
        )
        all_conformers.write_text(frame, encoding="utf-8")

        metadata = {
            "protocol": self.protocol_spec.name,
            "candidates": [
                {
                    "index": 0,
                    "energy": -10.0,
                    "g_conc": -10.1,
                    "gibbs_energy": -10.1,
                    "weight": 0.6,
                    "rank": 1,
                    "source_file": None,
                },
                {
                    "index": 1,
                    "energy": -9.99,
                    "g_conc": -9.95,
                    "gibbs_energy": -9.95,
                    "weight": 0.4,
                    "rank": 2,
                    "source_file": None,
                },
            ],
            "state_summary": {},
        }
        return global_min_xyz, -10.1, metadata


def _install_fake_engine(monkeypatch, tmp_path):
    monkeypatch.setattr(conformer_workflow, "ConformerEngine", _FakeEngine)
    monkeypatch.setattr(
        conformer_workflow,
        "load_config",
        lambda *args, **kwargs: _get_default_config(),
    )
    monkeypatch.setattr(
        conformer_workflow.StructureReader,
        "detect_format",
        lambda self, source: InputFormat.SMILES,
    )
    monkeypatch.setattr(
        conformer_workflow.StructureReader,
        "read",
        lambda self, source, charge=None, multiplicity=None, name=None: Structure(
            id=name or "ethanol",
            charge=charge or 0,
            multiplicity=multiplicity or 1,
            symbols=["C"],
            coordinates=np.array([[0.0, 0.0, 0.0]]),
            metadata={"smiles": "CCO"},
        ),
    )
    _FakeEngine.instances.clear()


def test_run_conformer_search_delegates_to_engine_run(tmp_path, monkeypatch):
    _install_fake_engine(monkeypatch, tmp_path)

    result = conformer_workflow.run_conformer_search("CCO", output_dir=tmp_path, protocol="ext")

    assert isinstance(result, WorkflowResult)
    assert result.status == "completed"
    # Engine.run() was called exactly once with a MolecularInput.
    engine = _FakeEngine.instances[0]
    assert len(engine.run_calls) == 1
    assert engine.last_molecular_input is not None
    # Ensemble rebuilt from the 2-frame all_conformers.xyz.
    assert result.ensemble is not None
    assert len(result.ensemble.records) == 2
    assert result.stages_completed
    assert result.metadata["protocol"] == "ext"
    assert result.metadata["global_min_energy"] == -10.1
    assert Path(result.metadata["global_min_xyz"]).exists()


def test_run_conformer_search_passes_charge_and_multiplicity(tmp_path, monkeypatch):
    captured: dict[str, int | None] = {}

    def _fake_read(self, source, charge=None, multiplicity=None, name=None):
        del name
        captured["charge"] = charge
        captured["multiplicity"] = multiplicity
        return Structure(
            id="ethanol",
            charge=charge or 0,
            multiplicity=multiplicity or 1,
            symbols=["C"],
            coordinates=np.array([[0.0, 0.0, 0.0]]),
            metadata={"smiles": "CCO"},
        )

    monkeypatch.setattr(conformer_workflow, "ConformerEngine", _FakeEngine)
    monkeypatch.setattr(
        conformer_workflow,
        "load_config",
        lambda *args, **kwargs: _get_default_config(),
    )
    monkeypatch.setattr(
        conformer_workflow.StructureReader,
        "detect_format",
        lambda self, source: InputFormat.SMILES,
    )
    monkeypatch.setattr(conformer_workflow.StructureReader, "read", _fake_read)
    _FakeEngine.instances.clear()

    result = conformer_workflow.run_conformer_search(
        "CCO", output_dir=tmp_path, protocol="ext", charge=1, multiplicity=2
    )

    assert result.status == "completed"
    assert captured == {"charge": 1, "multiplicity": 2}
    # Charge/multiplicity forwarded onto the MolecularInput handed to the engine.
    assert _FakeEngine.instances[0].last_molecular_input.charge == 1
    assert _FakeEngine.instances[0].last_molecular_input.multiplicity == 2


def test_run_conformer_search_reports_failure_when_engine_raises(tmp_path, monkeypatch):
    _install_fake_engine(monkeypatch, tmp_path)

    def _boom(self, molecular_input):
        raise RuntimeError("crest exploded")

    monkeypatch.setattr(_FakeEngine, "run", _boom)

    result = conformer_workflow.run_conformer_search("CCO", output_dir=tmp_path, protocol="ext")

    assert result.status == "failed"
    assert "crest exploded" in (result.error or "")
