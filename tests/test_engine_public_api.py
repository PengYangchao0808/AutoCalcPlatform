"""Tests for the stable ConformerEngine public API."""

from pathlib import Path

import numpy as np

from conformer_search.core.candidates import CandidateSet, ConformerCandidate
from conformer_search.core.engine import ConformerEngine
from conformer_search.core.protocols import FunnelPolicy, HandoffPolicy, ProtocolSpec


def _make_min_config() -> dict[str, object]:
    """Return a minimal config suitable for constructing ConformerEngine."""
    return {
        "executables": {
            "gaussian": {"path": "g16"},
            "orca": {"path": "orca"},
            "crest": {"path": "crest"},
            "xtb": {"path": "xtb"},
            "isostat": {"path": "isostat"},
            "shermo": {"path": "Shermo"},
        },
        "resources": {"nproc": 1, "mem": "1GB"},
        "theory": {
            "optimization": {
                "engine": "gaussian",
                "method": "B3LYP",
                "basis": "def2-SVP",
                "dispersion": "GD3BJ",
            },
            "frequency": {"engine": "gaussian"},
            "single_point": {"method": "M062X", "basis": "def2-TZVPP"},
            "preoptimization": {"gfn_level": 2},
        },
        "thermo": {"temperature_k": 298.15},
    }


def _make_protocol_spec(
    *,
    name: str = "ext",
    two_stage_enabled: bool = True,
    enable_crest: bool = True,
    enable_clustering: bool = True,
) -> ProtocolSpec:
    """Return a compact protocol spec for public-API tests."""
    return ProtocolSpec(
        name=name,
        two_stage_enabled=two_stage_enabled,
        ngeom_default=2,
        ngeom_max=3,
        funnel_policy=FunnelPolicy(),
        handoff_policy=HandoffPolicy(),
        enable_crest=enable_crest,
        enable_clustering=enable_clustering,
    )


def _make_engine(tmp_path, *, protocol_spec: ProtocolSpec | None = None) -> ConformerEngine:
    """Create a ConformerEngine instance with a lightweight config."""
    spec = protocol_spec or _make_protocol_spec()
    return ConformerEngine(
        config=_make_min_config(),
        work_dir=tmp_path,
        molecule_name="test_mol",
        protocol=spec.name,
        protocol_spec=spec,
    )


def _make_candidate_set() -> CandidateSet:
    """Return a small candidate set for delegation tests."""
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


def test_run_crest_uses_two_stage_search_when_enabled(tmp_path, monkeypatch):
    """run_crest() returns the stage-2 ensemble for two-stage protocols."""
    engine = _make_engine(tmp_path, protocol_spec=_make_protocol_spec(two_stage_enabled=True))
    initial_xyz = tmp_path / "initial.xyz"
    stage1_xyz = tmp_path / "stage1.xyz"
    ensemble_xyz = tmp_path / "ensemble.xyz"

    monkeypatch.setattr(
        engine,
        "_step_two_stage_crest",
        lambda path: (stage1_xyz, ensemble_xyz),
    )
    monkeypatch.setattr(
        engine,
        "_step_crest_search",
        lambda path: (_ for _ in ()).throw(AssertionError("single-stage helper should not be used")),
    )

    assert engine.run_crest(initial_xyz) == ensemble_xyz


def test_run_crest_uses_single_stage_search_when_disabled(tmp_path, monkeypatch):
    """run_crest() uses the single-stage helper when two-stage mode is off."""
    engine = _make_engine(tmp_path, protocol_spec=_make_protocol_spec(two_stage_enabled=False))
    initial_xyz = tmp_path / "initial.xyz"
    ensemble_xyz = tmp_path / "ensemble.xyz"

    monkeypatch.setattr(
        engine,
        "_step_two_stage_crest",
        lambda path: (_ for _ in ()).throw(AssertionError("two-stage helper should not be used")),
    )
    monkeypatch.setattr(engine, "_step_crest_search", lambda path: ensemble_xyz)

    assert engine.run_crest(initial_xyz) == ensemble_xyz


def test_run_isostat_clusters_then_splits_candidates(tmp_path, monkeypatch):
    """run_isostat() composes clustering and ensemble splitting."""
    engine = _make_engine(tmp_path)
    ensemble_xyz = tmp_path / "ensemble.xyz"
    clustered_xyz = tmp_path / "clustered.xyz"
    candidate_paths = [tmp_path / "conf_000.xyz", tmp_path / "conf_001.xyz"]

    monkeypatch.setattr(engine, "_step_isostat_clustering", lambda path: clustered_xyz)
    monkeypatch.setattr(engine, "_step_process_ensemble", lambda path: candidate_paths)

    assert engine.run_isostat(ensemble_xyz) == candidate_paths


def test_run_dft_handoff_delegates_to_private_handoff(tmp_path, monkeypatch):
    """run_dft_handoff() delegates to the shared private handoff helper."""
    engine = _make_engine(tmp_path)
    candidate_paths = [tmp_path / "conf_000.xyz"]
    candidate_set = _make_candidate_set()

    monkeypatch.setattr(engine, "_run_shared_dft_handoff", lambda paths: candidate_set)

    assert engine.run_dft_handoff(candidate_paths) is candidate_set


def test_run_zero_sp_delegates_to_private_zero_protocol(tmp_path, monkeypatch):
    """run_zero_sp() delegates to the zero-protocol implementation."""
    engine = _make_engine(tmp_path, protocol_spec=_make_protocol_spec(name="zero"))
    initial_xyz = tmp_path / "initial.xyz"
    candidate_set = _make_candidate_set()

    monkeypatch.setattr(engine, "_run_zero_protocol", lambda path: candidate_set)

    assert engine.run_zero_sp(initial_xyz) is candidate_set


def test_finalize_delegates_to_private_finalizer(tmp_path, monkeypatch):
    """finalize() preserves the legacy finalization path and return shape."""
    engine = _make_engine(tmp_path)
    candidate_set = _make_candidate_set()
    final_result = {
        "global_min_xyz": tmp_path / "global_min.xyz",
        "global_min_energy": -10.1,
        "n_conformers": 1,
        "metadata": {"protocol": "ext"},
    }

    monkeypatch.setattr(engine, "_finalize_results", lambda candidates: final_result)

    assert engine.finalize(candidate_set) is final_result
