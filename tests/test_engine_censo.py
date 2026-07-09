"""Engine-level CENSO runtime and dispatch tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from conformer_search.core.candidates import CandidateSet, ConformerCandidate
from conformer_search.core.engine import ConformerEngine
from conformer_search.core.specs import PROTOCOL_REGISTRY
from conformer_search.io.input_handler import InputFormat, MolecularInput
from conformer_search.qc.interfaces.base import QCResult
from conformer_search.utils.file_io import write_xyz


def _make_min_config() -> dict[str, object]:
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
        "protocols": {},
    }


def _make_candidate_set(source_file: Path) -> CandidateSet:
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
                source_file=source_file,
            )
        ]
    )


def test_run_dispatch_routes_censo_protocol_to_censo_runner(tmp_path, monkeypatch) -> None:
    """ConformerEngine.run() dispatches CENSO-family protocols through _run_censo_protocol."""
    engine = ConformerEngine(
        config=_make_min_config(),
        work_dir=tmp_path,
        molecule_name="censo_dispatch",
        protocol="censo-zero",
    )
    initial_xyz = engine.work_dir / "rdkit" / "censo_dispatch_init.xyz"
    write_xyz(initial_xyz, np.array([[0.0, 0.0, 0.0]]), ["C"], title="initial")
    expected_candidate_set = _make_candidate_set(initial_xyz)
    captured: dict[str, object] = {}

    monkeypatch.setattr(engine, "_save_initial_structure", lambda molecular_input: initial_xyz)

    def _fake_run_censo_protocol(path: Path, spec=None) -> CandidateSet:
        captured["routed_path"] = path
        captured["spec"] = spec
        return expected_candidate_set

    def _fake_finalize_results(candidate_set: CandidateSet) -> dict[str, object]:
        captured["candidate_set"] = candidate_set
        return {
            "global_min_xyz": initial_xyz,
            "global_min_energy": -10.1,
            "n_conformers": len(candidate_set.candidates),
            "metadata": {"protocol": engine.protocol_spec.name},
        }

    monkeypatch.setattr(engine, "_run_censo_protocol", _fake_run_censo_protocol)
    monkeypatch.setattr(engine, "_finalize_results", _fake_finalize_results)

    result = engine.run(
        MolecularInput(
            name="censo_dispatch",
            coordinates=np.array([[0.0, 0.0, 0.0]]),
            symbols=["C"],
            charge=0,
            multiplicity=1,
            source_format=InputFormat.XYZ,
        )
    )

    assert captured["routed_path"] == initial_xyz
    assert captured["candidate_set"] is expected_candidate_set
    assert isinstance(captured["candidate_set"], CandidateSet)
    assert result == (initial_xyz, -10.1, {"protocol": "censo-zero"})


def test_run_censo_protocol_writes_expected_funnel_snapshots(tmp_path, monkeypatch) -> None:
    """The mock CENSO runtime executes Part0–Part3 and writes funnel snapshots."""
    engine = ConformerEngine(
        config=_make_min_config(),
        work_dir=tmp_path,
        molecule_name="censo_runtime",
        protocol="censo-full",
    )
    initial_xyz = engine.work_dir / "rdkit" / "censo_runtime_init.xyz"
    write_xyz(initial_xyz, np.array([[0.0, 0.0, 0.0]]), ["C"], title="initial")

    candidate_paths = [tmp_path / "conf_000.xyz", tmp_path / "conf_001.xyz"]
    write_xyz(candidate_paths[0], np.array([[0.0, 0.0, 0.0]]), ["C"], title="conf0")
    write_xyz(candidate_paths[1], np.array([[1.0, 0.0, 0.0]]), ["C"], title="conf1")

    monkeypatch.setattr(engine, "_step_crest_search", lambda path: path)
    monkeypatch.setattr(engine, "_step_isostat_clustering", lambda path: path)
    monkeypatch.setattr(engine, "_step_process_ensemble", lambda path: candidate_paths)

    energies = iter([-10.0, -9.9])

    def _fake_xtb_single_point(coordinates, symbols, charge=0, multiplicity=1, output_dir=None, **kwargs):
        return QCResult(
            success=True,
            coordinates=coordinates,
            symbols=symbols,
            energy=next(energies),
        )

    monkeypatch.setattr(engine.xtb_interface, "single_point", _fake_xtb_single_point)

    candidate_set = engine._run_censo_protocol(
        initial_xyz,
        spec=PROTOCOL_REGISTRY["censo-full"],
    )

    funnel_dir = engine.work_dir / "funnel"
    assert isinstance(candidate_set, CandidateSet)
    assert len(candidate_set.candidates) == 1
    assert (funnel_dir / "00_part0_prescreen.json").exists()
    assert (funnel_dir / "01_part1_screening.json").exists()
    assert (funnel_dir / "02_part2_optimization.json").exists()
    assert (funnel_dir / "03_part3_refinement.json").exists()
