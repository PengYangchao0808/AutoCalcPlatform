"""Tests for the ACP conformer workflow wrapper."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.core.state import WorkflowState
from acp.core.workflow import WorkflowContext, WorkflowResult
from acp.io.structures import InputFormat
import acp.workflows.conformer as conformer_workflow
from conformer_search.config import _get_default_config
from conformer_search.core.candidates import CandidateSet, ConformerCandidate
from conformer_search.core.protocols import HandoffPolicy, FunnelPolicy, ProtocolSpec, resolve_protocol_spec


def test_get_protocol_stages_for_ext_protocol_has_expected_shape():
    """The ext protocol exposes the three search-only ACP stages."""
    config = _get_default_config()

    stages = conformer_workflow.get_protocol_stages("ext", config=config)

    assert [stage.name for stage in stages] == [
        "embed_smiles",
        "crest_search",
        "isostat_cluster",
    ]


def test_get_protocol_stages_for_reference_sp_skips_embed_stage():
    """reference-sp starts from an existing ensemble instead of RDKit embedding."""
    config = _get_default_config()

    stages = conformer_workflow.get_protocol_stages("reference-sp", config=config)

    assert [stage.name for stage in stages] == ["single_point"]


def test_boltzmann_weight_ensemble_uses_lowest_free_energy():
    """Boltzmann weighting prefers the lowest free-energy conformer."""
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


class _FakeStateManager:
    """Minimal state-manager stub for workflow wrapper tests."""

    def __init__(self) -> None:
        self.protocol: str | None = None
        self.smiles: str = ""
        self.two_stage_enabled: bool = False
        self.funnel_signature: dict[str, object] = {}
        self.stage_name: str | None = None
        self.stage_status: str | None = None
        self.stage_result: dict[str, object] = {}

    def start_run(self, smiles: str, two_stage_enabled: bool) -> None:
        self.smiles = smiles
        self.two_stage_enabled = two_stage_enabled

    def set_protocol_signature(self, protocol: str, funnel_signature: dict[str, object]) -> None:
        self.protocol = protocol
        self.funnel_signature = funnel_signature

    def set_stage(self, stage_name: str, status: str = "running") -> None:
        self.stage_name = stage_name
        self.stage_status = status

    def complete_stage(self, stage_name: str, result: dict[str, object]) -> None:
        self.stage_name = stage_name
        self.stage_result = result

    def get_summary(self) -> dict[str, object]:
        return {
            "protocol": self.protocol,
            "stage_name": getattr(self, "stage_name", None),
        }


class _FakeConformerEngine:
    """Legacy-engine test double used to isolate the ACP wrapper."""

    instances: list["_FakeConformerEngine"] = []

    def __init__(
        self,
        config: dict[str, object],
        work_dir: Path,
        molecule_name: str,
        protocol: str = "ext",
        protocol_spec=None,
    ) -> None:
        self.config = config
        self.molecule_name = molecule_name
        self.protocol_spec = protocol_spec or resolve_protocol_spec(config, protocol)
        self.work_dir = Path(work_dir) / molecule_name
        self.final_dft_dir = self.work_dir / "finalDFT"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.final_dft_dir.mkdir(parents=True, exist_ok=True)
        self.state_manager = _FakeStateManager()
        self.calls: list[object] = []
        self.__class__.instances.append(self)

    def run_crest(self, initial_xyz: Path) -> Path:
        self.calls.append("run_crest")
        if self.protocol_spec.two_stage_enabled:
            _, ensemble_xyz = self._step_two_stage_crest(initial_xyz)
            return ensemble_xyz
        return self._step_crest_search(initial_xyz)

    def run_isostat(self, ensemble_xyz: Path) -> list[Path]:
        self.calls.append("run_isostat")
        clustered_xyz = self._step_isostat_clustering(ensemble_xyz)
        return self._step_process_ensemble(clustered_xyz)

    def run_dft_handoff(self, candidate_paths: list[Path]) -> CandidateSet:
        self.calls.append(("run_dft_handoff", len(candidate_paths)))
        return self._run_shared_dft_handoff(candidate_paths)

    def run_zero_sp(self, initial_xyz: Path) -> CandidateSet:
        self.calls.append("run_zero_sp")
        return self._run_zero_protocol(initial_xyz)

    def finalize(self, candidate_set: CandidateSet) -> dict[str, object]:
        self.calls.append("finalize_public")
        return self._finalize_results(candidate_set)

    def _step_rdkit_embed(self, molecular_input) -> Path:
        self.calls.append("embed")
        path = self.work_dir / "rdkit" / f"{self.molecule_name}_init.xyz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("embed\n", encoding="utf-8")
        return path

    def _save_initial_structure(self, molecular_input) -> Path:
        return self._step_rdkit_embed(molecular_input)

    def _step_two_stage_crest(self, initial_xyz: Path) -> tuple[Path, Path]:
        self.calls.append("crest_two_stage")
        stage1 = self.work_dir / "crest" / "stage1.xyz"
        stage2 = self.work_dir / "crest" / "stage2.xyz"
        stage1.parent.mkdir(parents=True, exist_ok=True)
        stage1.write_text("stage1\n", encoding="utf-8")
        stage2.write_text("stage2\n", encoding="utf-8")
        return stage1, stage2

    def _step_crest_search(self, initial_xyz: Path) -> Path:
        self.calls.append("crest")
        path = self.work_dir / "crest" / "ensemble.xyz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ensemble\n", encoding="utf-8")
        return path

    def _step_isostat_clustering(self, ensemble_xyz: Path) -> Path:
        self.calls.append("cluster")
        path = self.work_dir / "cluster" / "clustered.xyz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("cluster\n", encoding="utf-8")
        return path

    def _step_process_ensemble(self, ensemble_xyz: Path) -> list[Path]:
        self.calls.append("split")
        first = self.work_dir / "cluster" / "conf_000.xyz"
        second = self.work_dir / "cluster" / "conf_001.xyz"
        first.write_text("conf0\n", encoding="utf-8")
        second.write_text("conf1\n", encoding="utf-8")
        return [first, second]

    def _run_shared_dft_handoff(self, candidate_paths: list[Path]) -> CandidateSet:
        self.calls.append(("shared_handoff", len(candidate_paths)))
        return CandidateSet(
            candidates=[
                ConformerCandidate(
                    index=0,
                    coordinates=np.array([[0.0, 0.0, 0.0]]),
                    symbols=["C"],
                    energy=-10.0,
                    gibbs_energy=-10.1,
                    g_conc=-10.1,
                    weight=0.75,
                    rank=1,
                    source_file=Path("conf_000.xyz"),
                ),
                ConformerCandidate(
                    index=1,
                    coordinates=np.array([[1.0, 0.0, 0.0]]),
                    symbols=["C"],
                    energy=-9.9,
                    gibbs_energy=-9.95,
                    g_conc=-9.95,
                    weight=0.25,
                    rank=2,
                    source_file=Path("conf_001.xyz"),
                ),
            ]
        )

    def _run_zero_protocol(self, initial_xyz: Path) -> CandidateSet:
        self.calls.append("zero")
        return self._run_shared_dft_handoff([])

    def _finalize_results(self, candidate_set: CandidateSet) -> dict[str, object]:
        self.calls.append("finalize")
        global_min_xyz = self.work_dir / f"{self.molecule_name}_global_min.xyz"
        all_conformers_xyz = self.final_dft_dir / "all_conformers.xyz"
        thermo_csv = self.final_dft_dir / "conformer_thermo.csv"
        global_min_xyz.write_text("global minimum\n", encoding="utf-8")
        all_conformers_xyz.write_text("all conformers\n", encoding="utf-8")
        thermo_csv.write_text("thermo\n", encoding="utf-8")
        return {
            "global_min_xyz": global_min_xyz,
            "global_min_energy": -10.1,
            "n_conformers": len(candidate_set.candidates),
            "metadata": {
                "protocol": self.protocol_spec.name,
                "state_summary": self.state_manager.get_summary(),
            },
        }


def test_run_conformer_search_returns_workflow_result(tmp_path, monkeypatch):
    """The public ACP API runs the stage wrapper and returns WorkflowResult."""
    monkeypatch.setattr(conformer_workflow, "ConformerEngine", _FakeConformerEngine)
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
        lambda self, source, charge=None, multiplicity=None: Structure(
            id="ethanol",
            charge=charge or 0,
            multiplicity=multiplicity or 1,
            symbols=["C"],
            coordinates=np.array([[0.0, 0.0, 0.0]]),
            metadata={"smiles": "CCO"},
        ),
    )

    def _fake_stage_search(ctx, data, **params):
        ctx.params[conformer_workflow._ENSEMBLE_XYZ_KEY] = ctx.work_dir / "ensemble.xyz"
        ctx.params[conformer_workflow._ENSEMBLE_XYZ_KEY].write_text("ensemble\n", encoding="utf-8")
        return data

    monkeypatch.setattr(conformer_workflow, "stage_search", _fake_stage_search)

    _FakeConformerEngine.instances.clear()
    result = conformer_workflow.run_conformer_search(
        "CCO",
        output_dir=tmp_path,
        protocol="ext",
    )

    assert isinstance(result, WorkflowResult)
    assert result.status == "completed"
    assert result.ensemble is not None
    assert len(result.ensemble.records) == 2
    assert result.stages_completed == [
        "embed_smiles",
        "crest_search",
        "isostat_cluster",
    ]
    assert result.metadata["protocol"] == "ext"
    assert Path(result.metadata["global_min_xyz"]).exists()

    engine = _FakeConformerEngine.instances[0]
    assert engine.calls == [
        "embed",
        "run_isostat",
        "cluster",
        "split",
        "finalize_public",
        "finalize",
    ]


def test_run_conformer_search_passes_charge_and_multiplicity(tmp_path, monkeypatch):
    """The workflow forwards explicit charge/multiplicity overrides to the reader."""
    captured: dict[str, int | None] = {}

    monkeypatch.setattr(conformer_workflow, "ConformerEngine", _FakeConformerEngine)
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

    def _fake_read(self, source, charge=None, multiplicity=None):
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

    monkeypatch.setattr(conformer_workflow.StructureReader, "read", _fake_read)

    result = conformer_workflow.run_conformer_search(
        "CCO",
        output_dir=tmp_path,
        protocol="ext",
        charge=1,
        multiplicity=2,
    )

    assert result.status == "completed"
    assert captured == {"charge": 1, "multiplicity": 2}


def _make_protocol_spec(
    *,
    name: str = "ext",
    two_stage_enabled: bool = True,
    ngeom_default: int = 2,
) -> ProtocolSpec:
    """Return a compact protocol spec for ACP stage tests."""
    return ProtocolSpec(
        name=name,
        two_stage_enabled=two_stage_enabled,
        ngeom_default=ngeom_default,
        ngeom_max=4,
        funnel_policy=FunnelPolicy(),
        handoff_policy=HandoffPolicy(),
    )


def _make_candidate_set() -> CandidateSet:
    """Create a single-candidate legacy result for ACP stage tests."""
    return CandidateSet(
        candidates=[
            ConformerCandidate(
                index=0,
                coordinates=np.array([[0.0, 0.0, 0.0]]),
                symbols=["C"],
                energy=-10.0,
                gibbs_energy=-10.1,
                g_conc=-10.1,
                weight=1.0,
                rank=1,
                source_file=Path("conf_000.xyz"),
            )
        ]
    )


def _make_structure_ensemble() -> StructureEnsemble:
    """Create a minimal ACP ensemble with one starting structure."""
    return StructureEnsemble(
        records=[
            StructureRecord(
                structure=Structure(
                    id="ethanol",
                    symbols=["C"],
                    coordinates=np.array([[0.0, 0.0, 0.0]]),
                )
            )
        ]
    )


def _make_context(
    tmp_path: Path,
    *,
    protocol_spec: ProtocolSpec,
    engine: _FakeConformerEngine,
    extra_params: dict[str, object] | None = None,
) -> WorkflowContext:
    """Create a workflow context with a cached fake engine."""
    state = WorkflowState(tmp_path / "workflow_state", "ethanol")
    state.initialize(input_source="CCO")
    params: dict[str, object] = {
        conformer_workflow._MOLECULE_NAME_KEY: "ethanol",
        conformer_workflow._PROTOCOL_SPEC_KEY: protocol_spec,
    }
    if extra_params:
        params.update(extra_params)

    return WorkflowContext(
        work_dir=tmp_path,
        state=state,
        config=_get_default_config(),
        backends={conformer_workflow._ENGINE_KEY: engine},
        params=params,
    )


def test_stage_crest_search_uses_public_engine_method(tmp_path, monkeypatch):
    """CREST stage calls ConformerEngine.run_crest() instead of private helpers."""
    monkeypatch.setattr(conformer_workflow, "ConformerEngine", _FakeConformerEngine)
    protocol_spec = _make_protocol_spec(two_stage_enabled=True)
    engine = _FakeConformerEngine(_get_default_config(), tmp_path, "ethanol", protocol_spec=protocol_spec)
    initial_xyz = tmp_path / "initial.xyz"
    ensemble_xyz = tmp_path / "ensemble.xyz"
    ctx = _make_context(
        tmp_path,
        protocol_spec=protocol_spec,
        engine=engine,
        extra_params={conformer_workflow._INITIAL_XYZ_KEY: initial_xyz},
    )

    monkeypatch.setattr(_FakeConformerEngine, "run_crest", lambda self, path: ensemble_xyz)
    monkeypatch.setattr(
        _FakeConformerEngine,
        "_step_two_stage_crest",
        lambda self, path: (_ for _ in ()).throw(AssertionError("stage should use run_crest")),
    )
    monkeypatch.setattr(
        _FakeConformerEngine,
        "_step_crest_search",
        lambda self, path: (_ for _ in ()).throw(AssertionError("stage should use run_crest")),
    )

    conformer_workflow.stage_crest_search(ctx, _make_structure_ensemble(), two_stage=True)

    assert ctx.params[conformer_workflow._ENSEMBLE_XYZ_KEY] == ensemble_xyz


def test_stage_isostat_cluster_uses_public_engine_method(tmp_path, monkeypatch):
    """ISOSTAT stage calls ConformerEngine.run_isostat() instead of private helpers."""
    monkeypatch.setattr(conformer_workflow, "ConformerEngine", _FakeConformerEngine)
    protocol_spec = _make_protocol_spec()
    engine = _FakeConformerEngine(_get_default_config(), tmp_path, "ethanol", protocol_spec=protocol_spec)
    ensemble_xyz = tmp_path / "ensemble.xyz"
    candidate_paths = [tmp_path / "conf_000.xyz", tmp_path / "conf_001.xyz"]
    ctx = _make_context(
        tmp_path,
        protocol_spec=protocol_spec,
        engine=engine,
        extra_params={conformer_workflow._ENSEMBLE_XYZ_KEY: ensemble_xyz},
    )

    monkeypatch.setattr(_FakeConformerEngine, "run_isostat", lambda self, path: candidate_paths)
    monkeypatch.setattr(
        _FakeConformerEngine,
        "_step_isostat_clustering",
        lambda self, path: (_ for _ in ()).throw(AssertionError("stage should use run_isostat")),
    )
    monkeypatch.setattr(
        _FakeConformerEngine,
        "_step_process_ensemble",
        lambda self, path: (_ for _ in ()).throw(AssertionError("stage should use run_isostat")),
    )

    conformer_workflow.stage_isostat_cluster(ctx, _make_structure_ensemble())

    assert ctx.params[conformer_workflow._CANDIDATE_PATHS_KEY] == candidate_paths


def test_stage_dft_optimize_uses_public_engine_method(tmp_path, monkeypatch):
    """DFT stage calls ConformerEngine.run_dft_handoff() for shared handoff work."""
    monkeypatch.setattr(conformer_workflow, "ConformerEngine", _FakeConformerEngine)
    protocol_spec = _make_protocol_spec(ngeom_default=1)
    engine = _FakeConformerEngine(_get_default_config(), tmp_path, "ethanol", protocol_spec=protocol_spec)
    candidate_paths = [tmp_path / "conf_000.xyz", tmp_path / "conf_001.xyz"]
    candidate_set = _make_candidate_set()
    ctx = _make_context(
        tmp_path,
        protocol_spec=protocol_spec,
        engine=engine,
        extra_params={conformer_workflow._CANDIDATE_PATHS_KEY: candidate_paths},
    )

    monkeypatch.setattr(_FakeConformerEngine, "run_dft_handoff", lambda self, paths: candidate_set)
    monkeypatch.setattr(
        _FakeConformerEngine,
        "_run_shared_dft_handoff",
        lambda self, paths: (_ for _ in ()).throw(AssertionError("stage should use run_dft_handoff")),
    )

    result = conformer_workflow.stage_dft_optimize(
        ctx,
        _make_structure_ensemble(),
        mode="shared_default",
    )

    assert ctx.params[conformer_workflow._CANDIDATE_SET_KEY] is candidate_set
    assert len(result.records) == 1


def test_stage_single_point_zero_mode_uses_public_engine_method(tmp_path, monkeypatch):
    """Zero-mode SP stage calls ConformerEngine.run_zero_sp()."""
    monkeypatch.setattr(conformer_workflow, "ConformerEngine", _FakeConformerEngine)
    protocol_spec = _make_protocol_spec(name="zero")
    engine = _FakeConformerEngine(_get_default_config(), tmp_path, "ethanol", protocol_spec=protocol_spec)
    initial_xyz = tmp_path / "initial.xyz"
    candidate_set = _make_candidate_set()
    ctx = _make_context(
        tmp_path,
        protocol_spec=protocol_spec,
        engine=engine,
        extra_params={conformer_workflow._INITIAL_XYZ_KEY: initial_xyz},
    )

    monkeypatch.setattr(_FakeConformerEngine, "run_zero_sp", lambda self, path: candidate_set)
    monkeypatch.setattr(
        _FakeConformerEngine,
        "_run_zero_protocol",
        lambda self, path: (_ for _ in ()).throw(AssertionError("stage should use run_zero_sp")),
    )

    result = conformer_workflow.stage_single_point(
        ctx,
        _make_structure_ensemble(),
        mode="zero_protocol",
    )

    assert ctx.params[conformer_workflow._CANDIDATE_SET_KEY] is candidate_set
    assert len(result.records) == 1


def test_finalize_conformer_results_uses_public_engine_method(tmp_path, monkeypatch):
    """Workflow finalization delegates through ConformerEngine.finalize()."""
    monkeypatch.setattr(conformer_workflow, "ConformerEngine", _FakeConformerEngine)
    protocol_spec = _make_protocol_spec()
    engine = _FakeConformerEngine(_get_default_config(), tmp_path, "ethanol", protocol_spec=protocol_spec)
    candidate_set = _make_candidate_set()
    final_result = {
        "global_min_xyz": tmp_path / "ethanol_global_min.xyz",
        "global_min_energy": -10.1,
        "n_conformers": 1,
        "metadata": {"protocol": "ext"},
    }
    ctx = _make_context(
        tmp_path,
        protocol_spec=protocol_spec,
        engine=engine,
        extra_params={conformer_workflow._CANDIDATE_SET_KEY: candidate_set},
    )

    monkeypatch.setattr(_FakeConformerEngine, "finalize", lambda self, candidates: final_result)
    monkeypatch.setattr(
        _FakeConformerEngine,
        "_finalize_results",
        lambda self, candidates: (_ for _ in ()).throw(AssertionError("workflow should use finalize")),
    )

    assert conformer_workflow._finalize_conformer_results(ctx) is final_result
