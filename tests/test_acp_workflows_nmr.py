"""Integration test for the NMR workflow (DevDoc §5).

Mocks the ``run_nmr_shielding`` task core and the conformer-generation
task cores so the full analysis pipeline (stages 0–8) runs without external
binaries. Exercises both the assigned and unassigned matching paths and
verifies the report artifacts are written.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from acp.calculations.progress import ProgressReporter
from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.nmr.models import ConformerShielding, NmrConfig


def _shielding_result(shieldings: Mapping[int, Mapping[str, Any]]) -> Any:
    """Build a completed ``run_nmr_shielding`` TaskResult with canned shieldings."""
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
        task=TaskKind.NMR_SHIELDING,
        status="completed",
        complete=True,
        payload=payload,
    )


def _make_structure(symbols: list[str], coords: list[tuple[float, float, float]]) -> Structure:
    return Structure(
        id="cand",
        charge=0,
        multiplicity=1,
        symbols=symbols,
        coordinates=np.array(coords, dtype=float),
    )


def _ensemble_with_shieldings(
    structure: Structure, shieldings: Mapping[int, Mapping[str, str | float]]
) -> StructureEnsemble:
    """Build a StructureEnsemble whose .data holds pre-computed shieldings.

    The workflow's ``skip_conformers`` path reads ``ensemble.data`` to
    bypass the GIAO subprocess entirely (test fast-path).
    """
    normalized_shieldings: dict[int, dict[str, object]] = {
        index: dict(values) for index, values in shieldings.items()
    }
    ens = StructureEnsemble(
        records=[
            StructureRecord(
                structure=structure, energy_hartree=-1.0, free_energy_hartree=-1.0, weight=1.0
            )
        ]
    )
    ens.data = [ConformerShielding("conf_000", 1.0, normalized_shieldings)]
    return ens


def test_run_nmr_analysis_assigned_two_candidates(tmp_path: Path) -> None:
    symbols = ["C", "H", "H", "H", "H"]
    coords = [(0.0, 0.0, 0.0)] * 5
    structure = _make_structure(symbols, coords)

    # candidate A: shieldings map to clean shifts; candidate B: noisy
    # (σ_TMS = Goodman TMSdata mPW1PW91/6-311G(d)/chloroform:
    #  13C 188.452125, 1H 32.1243166667)
    sh_a = {
        0: {"symbol": "C", "isotropic": 188.452125 - 40.0},  # δ = 40.0
        1: {"symbol": "H", "isotropic": 32.1243166667 - 4.0},  # δ = 4.0
        2: {"symbol": "H", "isotropic": 32.1243166667 - 3.0},  # 3.0
        3: {"symbol": "H", "isotropic": 32.1243166667 - 1.0},  # 1.0
        4: {"symbol": "H", "isotropic": 32.1243166667 - 0.0},  # 0.0
    }
    sh_b = {
        0: {"symbol": "C", "isotropic": 188.452125 - 30.0},  # 30.0 (off by 10)
        1: {"symbol": "H", "isotropic": 32.1243166667 - 9.0},  # 9.0 (off by 5)
        2: {"symbol": "H", "isotropic": 32.1243166667 - 8.0},
        3: {"symbol": "H", "isotropic": 32.1243166667 - 6.0},
        4: {"symbol": "H", "isotropic": 32.1243166667 - 5.0},
    }

    spectrum = "C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)"

    ens_a = _ensemble_with_shieldings(structure, sh_a)
    ens_b = _ensemble_with_shieldings(structure, sh_b)

    # patch StructureReader.read to return a fixed structure for SMILES input
    with (
        patch("acp.workflows.nmr.StructureReader") as reader_cls,
        patch(
            "acp.workflows.nmr.run_nmr_shielding",
            side_effect=[_shielding_result(sh_a), _shielding_result(sh_b)],
        ),
    ):
        reader = MagicMock()
        reader.read.return_value = structure
        reader_cls.return_value = reader

        from acp.workflows.nmr import run_nmr_analysis

        result = run_nmr_analysis(
            input_sources=["CCO", "CCO"],
            spectrum=spectrum,
            output_dir=str(tmp_path),
            skip_conformers=True,
            prebuilt_ensembles=[ens_a, ens_b],
            error_model="placeholder-student-t",
        )

    assert result.status == "completed", result.error
    assert "report_json" in result.metadata
    winner = result.metadata["winner"]
    assert winner["index"] == 0  # candidate A has smaller residuals → wins
    assert winner["dp4"] > 0.5

    # JSON report exists + is well-formed
    report_path = Path(result.metadata["report_json"])
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    assert report["summary"]["n_candidates"] == 2
    assert len(report["candidates"]) == 2


def test_run_nmr_analysis_unassigned(tmp_path: Path) -> None:
    symbols = ["C", "H", "H", "H", "H"]
    structure = _make_structure(symbols, [(0.0, 0.0, 0.0)] * 5)
    sh = {
        0: {"symbol": "C", "isotropic": 188.452125 - 40.0},
        1: {"symbol": "H", "isotropic": 32.1243166667 - 4.0},
        2: {"symbol": "H", "isotropic": 32.1243166667 - 3.0},
        3: {"symbol": "H", "isotropic": 32.1243166667 - 1.0},
        4: {"symbol": "H", "isotropic": 32.1243166667 - 0.0},
    }
    ens = _ensemble_with_shieldings(structure, sh)
    spectrum = "C: 40.0\nH: 4.0, 3.0, 1.0, 0.0(3)"

    with (
        patch("acp.workflows.nmr.StructureReader") as reader_cls,
        patch(
            "acp.workflows.nmr.run_nmr_shielding",
            return_value=_shielding_result(sh),
        ),
    ):
        reader = MagicMock()
        reader.read.return_value = structure
        reader_cls.return_value = reader

        from acp.workflows.nmr import run_nmr_analysis

        result = run_nmr_analysis(
            input_sources=["CCO"],
            spectrum=spectrum,
            output_dir=str(tmp_path),
            skip_conformers=True,
            prebuilt_ensembles=[ens],
            error_model="placeholder-student-t",
        )

    assert result.status == "completed", result.error
    assert result.metadata["winner"]["index"] == 0


def test_run_nmr_analysis_reports_all_stage_lifecycle(tmp_path: Path) -> None:
    symbols = ["C", "H"]
    structure = _make_structure(symbols, [(0.0, 0.0, 0.0)] * 2)
    shieldings = {
        0: {"symbol": "C", "isotropic": 188.452125 - 40.0},
        1: {"symbol": "H", "isotropic": 32.1243166667 - 4.0},
    }
    ensemble = _ensemble_with_shieldings(structure, shieldings)
    expected_stages = [
        "embed_smiles",
        "crest_search",
        "censo_prescreening",
        "censo_screening",
        "ensemble_export",
        "giao_nmr",
        "boltzmann_average",
        "dp4_dp5_probability",
        "nmr_report",
    ]
    events: list[tuple[str, str]] = []

    class RecordingReporter(ProgressReporter):
        def start_stage(self, name: str) -> None:
            events.append(("start", name))
            super().start_stage(name)

        def complete_stage(self, name: str, result=None) -> None:
            events.append(("complete", name))
            super().complete_stage(name, result)

    reporter = RecordingReporter(
        tmp_path / "progress",
        job_name="nmr",
        stages=expected_stages,
        min_interval=0.0,
    )
    with patch("acp.workflows.nmr.StructureReader") as reader_cls:
        reader = MagicMock()
        reader.read.return_value = structure
        reader_cls.return_value = reader

        from acp.workflows.nmr import run_nmr_analysis

        result = run_nmr_analysis(
            input_sources=["CCO"],
            spectrum="C: 40.0(C1)\nH: 4.0(H1)",
            output_dir=str(tmp_path / "out"),
            skip_conformers=True,
            prebuilt_ensembles=[ensemble],
            error_model="placeholder-student-t",
            progress_reporter=reporter,
        )

    assert result.status == "completed", result.error
    assert events == [
        event for stage in expected_stages for event in (("start", stage), ("complete", stage))
    ]
    state = json.loads((tmp_path / "progress" / "state.json").read_text(encoding="utf-8"))
    assert list(state["stages"]) == expected_stages
    assert all(info["status"] == "completed" for info in state["stages"].values())
    assert state["current_stage"] is None


def test_nmr_cli_constructs_and_completes_reporter(monkeypatch, tmp_path: Path) -> None:
    import acp.cli as acp_cli
    import acp.workflows.nmr as nmr_workflow
    from acp.core.workflow import WorkflowResult

    output_dir = tmp_path / "nmr-output"
    args = acp_cli.build_parser().parse_args(
        [
            "run",
            "nmr",
            "--input",
            "CCO",
            "--spectrum",
            "C: 40.0(C1)",
            "--output",
            str(output_dir),
            "--log-level",
            "ERROR",
        ]
    )
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return WorkflowResult(status="completed", metadata={})

    monkeypatch.setattr(nmr_workflow, "run_nmr_analysis", fake_run)

    assert acp_cli._handle_nmr(args) == 0
    assert isinstance(captured["progress_reporter"], ProgressReporter)
    state = json.loads((output_dir / "state.json").read_text(encoding="utf-8"))
    assert list(state["stages"]) == [
        "embed_smiles",
        "crest_search",
        "censo_prescreening",
        "censo_screening",
        "ensemble_export",
        "giao_nmr",
        "boltzmann_average",
        "dp4_dp5_probability",
        "nmr_report",
    ]
    assert state["status"] == "completed"


def test_run_nmr_analysis_reports_malformed_input_failure(tmp_path: Path) -> None:
    from acp.workflows.nmr import NMR_STAGES, run_nmr_analysis

    reporter = ProgressReporter(
        tmp_path / "progress",
        job_name="nmr",
        stages=list(NMR_STAGES),
        min_interval=0.0,
    )

    result = run_nmr_analysis(
        input_sources=[],
        spectrum="C: 40.0(C1)",
        output_dir=tmp_path / "out",
        progress_reporter=reporter,
    )

    assert result.status == "failed"
    assert result.error == "no candidate structures parsed"
    state = json.loads((tmp_path / "progress" / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert state["stages"]["embed_smiles"]["status"] == "failed"
    assert state["stages"]["embed_smiles"]["error"] == result.error


def test_run_nmr_analysis_rejects_mismatched_error_model(tmp_path: Path) -> None:
    from acp.workflows.nmr import run_nmr_analysis

    result = run_nmr_analysis(
        input_sources=["CCO"],
        spectrum="C: 40.0(C1)",
        output_dir=str(tmp_path),
        nmr_method="wB97X-D4",
        nmr_basis="def2-TZVPPD",
        error_model="goodman-legacy",
    )
    assert result.status == "failed"
    assert "mPW1PW91" in (result.error or "")


def test_run_nmr_analysis_enumerate_expands_candidates(tmp_path: Path) -> None:
    # --enumerate on a single under-specified input must expand it into the
    # full diastereomer set before the per-candidate pipeline runs. We patch
    # enumerate_candidates to a fixed 2-isomer result and supply matching
    # prebuilt ensembles so the heavy compute path is bypassed.
    from acp.nmr.enumerate import EnumeratedCandidate

    symbols = ["C", "H", "H", "H", "C", "H", "Cl", "C", "H", "H", "Cl"]
    structure = _make_structure(symbols, [(0.0, 0.0, 0.0)] * len(symbols))
    sh = {i: {"symbol": s, "isotropic": 30.0} for i, s in enumerate(symbols)}
    ens = _ensemble_with_shieldings(structure, sh)

    fake_isomers = [
        EnumeratedCandidate(smiles="C[C@H](Cl)[C@@H](C)Cl", label="diastereomer_1"),
        EnumeratedCandidate(smiles="C[C@@H](Cl)[C@@H](C)Cl", label="diastereomer_2"),
    ]

    with (
        patch("acp.workflows.nmr.StructureReader") as reader_cls,
        patch(
            "acp.workflows.nmr.run_nmr_shielding",
            return_value=_shielding_result(sh),
        ),
        patch("acp.workflows.nmr.enumerate_candidates", return_value=fake_isomers),
    ):
        reader = MagicMock()
        reader.read.return_value = structure
        reader_cls.return_value = reader

        from acp.workflows.nmr import run_nmr_analysis

        result = run_nmr_analysis(
            input_sources=["CC(Cl)C(Cl)C"],
            spectrum="C: 40.0(C1)",
            output_dir=str(tmp_path),
            enumerate_stereoisomers=True,
            skip_conformers=True,
            prebuilt_ensembles=[ens, ens],
            error_model="placeholder-student-t",
        )

    assert result.status == "completed", result.error
    assert result.metadata["n_candidates"] == 2


def test_run_nmr_analysis_enumerate_requires_single_input(tmp_path: Path) -> None:
    from acp.workflows.nmr import run_nmr_analysis

    result = run_nmr_analysis(
        input_sources=["CCO", "CCN"],
        spectrum="C: 40.0(C1)",
        output_dir=str(tmp_path),
        enumerate_stereoisomers=True,
        error_model="placeholder-student-t",
    )
    assert result.status == "failed"
    assert "exactly one" in (result.error or "")


def test_run_nmr_analysis_bruker_input(tmp_path: Path) -> None:
    """P3: Bruker raw-data input is processed (stage 0a) and feeds the
    unassigned matching path end-to-end."""

    symbols = ["C", "H", "H", "H", "H"]
    structure = _make_structure(symbols, [(0.0, 0.0, 0.0)] * 5)
    sh = {
        0: {"symbol": "C", "isotropic": 188.452125 - 40.0},  # δ = 40.0
        1: {"symbol": "H", "isotropic": 32.1243166667 - 4.0},  # δ = 4.0
        2: {"symbol": "H", "isotropic": 32.1243166667 - 3.0},  # δ = 3.0
        3: {"symbol": "H", "isotropic": 32.1243166667 - 1.0},  # δ = 1.0
        4: {"symbol": "H", "isotropic": 32.1243166667 - 0.0},  # δ = 0.0
    }
    ens = _ensemble_with_shieldings(structure, sh)

    # Write synthetic Bruker experiments with peaks matching the shifts.
    bruker_root = tmp_path / "bruker"
    _write_synthetic_bruker(
        bruker_root / "Proton",
        "1H",
        500.13,
        [(4.0, 1.0, 5.0), (3.0, 1.0, 5.0), (1.0, 1.0, 5.0), (0.0, 1.0, 5.0)],
        sw_ppm=10.0,
        o1_ppm=5.0,
    )
    _write_synthetic_bruker(
        bruker_root / "Carbon",
        "13C",
        125.76,
        [(40.0, 1.0, 4.0)],
        sw_ppm=200.0,
        o1_ppm=100.0,
    )

    with (
        patch("acp.workflows.nmr.StructureReader") as reader_cls,
        patch(
            "acp.workflows.nmr.run_nmr_shielding",
            return_value=_shielding_result(sh),
        ),
    ):
        reader = MagicMock()
        reader.read.return_value = structure
        reader_cls.return_value = reader

        from acp.workflows.nmr import run_nmr_analysis

        result = run_nmr_analysis(
            input_sources=["CCO"],
            bruker=str(bruker_root),
            output_dir=str(tmp_path / "out"),
            skip_conformers=True,
            prebuilt_ensembles=[ens],
            error_model="placeholder-student-t",
        )

    assert result.status == "completed", result.error
    assert (tmp_path / "out" / "bruker_peaks.txt").exists()


def test_run_nmr_analysis_spectrum_bruker_mutual_exclusion(tmp_path: Path) -> None:
    from acp.workflows.nmr import run_nmr_analysis

    result = run_nmr_analysis(
        input_sources=["CCO"],
        spectrum="C: 40.0(C1)",
        bruker=str(tmp_path),
        output_dir=str(tmp_path),
        error_model="placeholder-student-t",
    )
    assert result.status == "failed"
    assert "exactly one" in (result.error or "")


def _write_synthetic_bruker(
    root: Path,
    nucleus: str,
    bf1: float,
    peaks: list[tuple[float, float, float]],
    sw_ppm: float,
    o1_ppm: float,
    td: int = 16384,
) -> None:
    """Write a minimal synthetic Bruker experiment for integration tests."""
    root.mkdir(parents=True, exist_ok=True)
    sw_hz = sw_ppm * bf1
    t = np.arange(td) / sw_hz
    fid = np.zeros(td, dtype=complex)
    for ppm, amp, r2 in peaks:
        nu = (o1_ppm - ppm) * bf1
        fid += amp * np.exp(2j * np.pi * nu * t) * np.exp(-np.pi * r2 * t)
    rng = np.random.default_rng(42)
    fid += rng.normal(0, 0.0002, td) + 1j * rng.normal(0, 0.0002, td)
    fid *= 1e6
    raw = np.empty(2 * td, dtype=np.int32)
    raw[0::2] = np.real(fid).astype(np.int32)
    raw[1::2] = np.imag(fid).astype(np.int32)
    raw.astype("<i4").tofile(root / "fid")
    (root / "acqus").write_text(
        f"##$TD= {2 * td}\n##$SFO1= {bf1}\n##$BF1= {bf1}\n"
        f"##$O1= {o1_ppm * bf1}\n##$SW_h= {sw_hz}\n##$SW= {sw_ppm}\n"
        f"##$NUC1= <{nucleus}>\n##$BYTORDA= 0\n##$DTYPA= 0\n"
        "##$AQ_mod= 1\n##$DECIM= 1\n##$DSPFVS= 0\n##$GRPDLY= 0.0\n##END=\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Task-core seams (todo 27): conformer search / censo refine / GIAO shielding
# ---------------------------------------------------------------------------


def _crest_ensemble_xyz(path: Path, energies: list[float]) -> Path:
    """Write a 2-atom-per-frame multi-frame ensemble XYZ with title energies."""
    lines: list[str] = []
    for i, energy in enumerate(energies):
        lines.extend(
            [
                "2",
                f"Energy: {energy:.10f}",
                f"H 0.0 0.0 {i * 0.1:.3f}",
                f"H 0.0 0.0 {0.74 + i * 0.1:.3f}",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _search_result(ensemble_xyz: Path, energies: list[float]) -> Any:
    """Completed ``run_conformer_search`` TaskResult over *ensemble_xyz*."""
    from cccp.calculation.contracts import ArtifactRef
    from cccp.calculation.requests import TaskKind
    from cccp.calculation.results import ConformerEnergy, ConformerSearchPayload, TaskResult

    payload = ConformerSearchPayload(
        ensemble_ref=ArtifactRef(path=ensemble_xyz, type="ensemble", checksum="", source="crest"),
        conformer_count=len(energies),
        energy_table=tuple(
            ConformerEnergy(conf_id=f"conf_{index}", frame_index=index, energy_hartree=energy)
            for index, energy in enumerate(energies)
        ),
    )
    return TaskResult(
        task=TaskKind.CONFORMER_SEARCH, status="completed", complete=True, payload=payload
    )


def _write_censo_final_part(run_dir: Path) -> None:
    """Write the CENSO final-part ``1_SCREENING.json/.xyz`` fixture files."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "1_SCREENING.json").write_text(
        json.dumps(
            {
                "data": {
                    "CONF1": {
                        "energy": -40.250000,
                        "gsolv": -0.002000,
                        "grrho": 0.010500,
                        "gtot": -40.241500,
                    },
                    "CONF2": {
                        "energy": -40.240000,
                        "gsolv": -0.001000,
                        "grrho": 0.001000,
                        "gtot": -40.240000,
                    },
                },
                "part_name": "screening",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "1_SCREENING.xyz").write_text(
        "2\nCONF1\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74\n2\nCONF2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.76\n",
        encoding="utf-8",
    )


def _censo_refine_result() -> Any:
    from cccp.calculation.requests import TaskKind
    from cccp.calculation.results import TaskResult

    return TaskResult(
        task=TaskKind.CENSO_REFINE,
        status="completed",
        complete=True,
        metadata={"final_part": "screening", "preset": "censo-light", "temperature_k": 298.15},
    )


def _expected_censo_weights(
    gtot_by_conf: dict[str, float], temperature: float = 298.15
) -> dict[str, float]:
    from cccp.qc.interfaces.censo import CensoConformerRecord, CensoRunResult

    records = [
        CensoConformerRecord(
            conf_id=conf_id,
            frame_index=index,
            energy=gtot,
            gsolv=0.0,
            grrho=0.0,
            gtot=gtot,
            coordinates=np.zeros((0, 3)),
            symbols=[],
        )
        for index, (conf_id, gtot) in enumerate(gtot_by_conf.items())
    ]
    return CensoRunResult(
        preset="x", records=records, final_part="", work_dir=None, temperature=temperature
    ).boltzmann_weights()


def test_conformer_generation_default_censo_light_runs_censo(tmp_path: Path) -> None:
    # Default preset is censo-light: CREST then CENSO (run_censo_refine) must run,
    # and the ensemble keeps CENSO energy/free-energy/weight semantics.
    from acp.workflows.nmr import _run_conformer_generation

    structure = _make_structure(["H", "H"], [(0.0, 0.0, 0.0), (0.0, 0.0, 0.74)])
    ensemble_xyz = _crest_ensemble_xyz(tmp_path / "crest_conformers.xyz", [-100.25, -100.24])

    def fake_refine(request, *, context=None):
        _write_censo_final_part(request.output_dir)
        return _censo_refine_result()

    with (
        patch(
            "acp.workflows.nmr.run_conformer_search",
            return_value=_search_result(ensemble_xyz, [-100.25, -100.24]),
        ) as mock_search,
        patch("acp.workflows.nmr.run_censo_refine", side_effect=fake_refine) as mock_censo,
    ):
        ensemble = _run_conformer_generation(
            structure, tmp_path / "02_SEARCH", NmrConfig(), {}, None, None, None
        )

    assert ensemble is not None
    assert mock_search.call_count == 1
    assert mock_censo.call_count == 1  # censo-light runs CENSO
    crest_request = mock_search.call_args.args[0]
    assert crest_request.task.value == "conformer_search"
    assert crest_request.options.energy_window == 6.0
    censo_request = mock_censo.call_args.args[0]
    assert censo_request.task.value == "censo_refine"
    assert censo_request.options.preset == "censo-light"
    assert censo_request.structure.path == ensemble_xyz

    # conformer count / energy source / weight semantics unchanged:
    # free energy = CENSO gtot, energy = CENSO energy, weight = Boltzmann(gtot).
    assert len(ensemble.records) == 2
    expected_gtot = {"CONF1": -40.241500, "CONF2": -40.240000}
    expected_energy = {"CONF1": -40.250000, "CONF2": -40.240000}
    expected_weights = _expected_censo_weights(expected_gtot)
    for record in ensemble.records:
        conf_id = record.structure.metadata["conf_id"]
        assert record.free_energy_hartree == expected_gtot[conf_id]
        assert record.energy_hartree == expected_energy[conf_id]
        assert record.weight == pytest.approx(expected_weights[conf_id])
    assert sum(record.weight for record in ensemble.records) == pytest.approx(1.0)


def test_conformer_generation_censo_zero_skips_censo(tmp_path: Path) -> None:
    # censo-zero must NOT invoke CENSO: CREST ensemble passes through on its
    # xTB title energies (gtot == energy, gsolv/grrho zero).
    from acp.workflows.nmr import _run_conformer_generation

    structure = _make_structure(["H", "H"], [(0.0, 0.0, 0.0), (0.0, 0.0, 0.74)])
    ensemble_xyz = _crest_ensemble_xyz(tmp_path / "crest_conformers.xyz", [-100.25, -100.35])

    with (
        patch(
            "acp.workflows.nmr.run_conformer_search",
            return_value=_search_result(ensemble_xyz, [-100.25, -100.35]),
        ) as mock_search,
        patch("acp.workflows.nmr.run_censo_refine") as mock_censo,
    ):
        ensemble = _run_conformer_generation(
            structure,
            tmp_path / "02_SEARCH",
            NmrConfig(conformer_preset="censo-zero"),
            {},
            None,
            None,
            None,
        )

    assert ensemble is not None
    assert mock_search.call_count == 1
    assert mock_censo.call_count == 0  # censo-zero skips CENSO

    # energy source = xTB title energies from the CREST energy table.
    assert len(ensemble.records) == 2
    expected = {"CONF1": -100.25, "CONF2": -100.35}
    expected_weights = _expected_censo_weights(expected)
    for record in ensemble.records:
        conf_id = record.structure.metadata["conf_id"]
        assert record.energy_hartree == expected[conf_id]
        assert record.free_energy_hartree == expected[conf_id]  # gtot == xTB energy
        assert record.properties["gsolv"] == 0.0
        assert record.properties["grrho"] == 0.0
        assert record.weight == pytest.approx(expected_weights[conf_id])
    # weight semantics: lower-energy conformer carries the larger weight.
    by_conf = {r.structure.metadata["conf_id"]: r for r in ensemble.records}
    assert by_conf["CONF2"].weight > by_conf["CONF1"].weight


def test_run_giao_task_core_keeps_shielding_shape(tmp_path: Path) -> None:
    from acp.workflows.nmr import _run_giao_for_conformers

    structure = _make_structure(
        ["C", "H", "H", "H", "H"],
        [(0.0, 0.0, 0.0)] * 5,
    )
    sh = {
        0: {"symbol": "C", "isotropic": 188.452125 - 40.0},
        1: {"symbol": "H", "isotropic": 32.1243166667 - 4.0},
    }
    captured: dict[str, Any] = {}

    def fake_shielding(request, *, context=None):
        captured["request"] = request
        captured["context"] = context
        return _shielding_result(sh)

    with patch("acp.workflows.nmr.run_nmr_shielding", side_effect=fake_shielding) as mock_sh:
        results = _run_giao_for_conformers(
            [(structure, 0.25, 0.1), (structure, 0.75, 0.0)],
            NmrConfig(),
            tmp_path / "giao",
            {},
            None,
        )

    assert mock_sh.call_count == 2
    assert [item.conformer_id for item in results] == ["conf_000", "conf_001"]
    assert [item.boltzmann_weight for item in results] == [0.25, 0.75]
    # shielding key shape unchanged: atom -> {symbol, isotropic}, 0-based keys.
    assert results[0].shieldings == {
        0: {"symbol": "C", "isotropic": pytest.approx(148.452125)},
        1: {"symbol": "H", "isotropic": pytest.approx(28.1243166667)},
    }
    assert all(isinstance(key, int) for key in results[1].shieldings)
    assert all(set(entry) == {"symbol", "isotropic"} for entry in results[0].shieldings.values())

    request = captured["request"]
    assert request.task.value == "nmr_shielding"
    assert request.charge == 0 and request.multiplicity == 1
    assert request.level.method == "mPW1PW91"
    assert request.level.basis == "6-311G(d)"
    assert request.options.atom_index_base == 0
    # nuclei labels pass through unchanged (ORCA normalises isotope labels)
    assert captured["context"].capability_extras["nuclei"] == ["1H", "13C"]


def test_run_giao_task_core_skips_failed_conformer(tmp_path: Path) -> None:
    from acp.workflows.nmr import _run_giao_for_conformers
    from cccp.calculation.requests import TaskKind
    from cccp.calculation.results import TaskResult

    structure = _make_structure(["H", "H"], [(0.0, 0.0, 0.0), (0.0, 0.0, 0.74)])
    sh = {0: {"symbol": "H", "isotropic": 30.0}, 1: {"symbol": "H", "isotropic": 31.0}}
    failed = TaskResult(
        task=TaskKind.NMR_SHIELDING,
        status="failed",
        complete=False,
        errors=("GIAO did not converge",),
    )

    with patch("acp.workflows.nmr.run_nmr_shielding", side_effect=[failed, _shielding_result(sh)]):
        results = _run_giao_for_conformers(
            [(structure, 1.0, 0.0), (structure, 1.0, 0.0)],
            NmrConfig(),
            tmp_path / "giao",
            {},
            None,
        )

    assert [item.conformer_id for item in results] == ["conf_001"]


# ---------------------------------------------------------------------------
# Effective config + solvent_model end-to-end (todo 21 / gap G04)
# ---------------------------------------------------------------------------


def _build_test_nmr_config(**overrides: Any) -> NmrConfig:
    """``_build_nmr_config`` with explicit ``None`` overrides (no CLI layer)."""
    from acp.workflows.nmr import _build_nmr_config
    from cccp.config import load_config

    kwargs: dict[str, Any] = dict(
        nuclei=None,
        nmr_method=None,
        nmr_basis=None,
        solvent=None,
        boltzmann_temp=None,
        tms_1h=None,
        tms_13c=None,
        error_model=None,
        conformer_preset=None,
        solvent_model=None,
        max_conformers=None,
    )
    kwargs.update(overrides)
    return _build_nmr_config(load_config(), **kwargs)


def _run_giao_capture(nmr_config: NmrConfig, tmp_path: Path) -> tuple[Any, str]:
    """Run the GIAO seam with the REAL task core; return (MethodSpec, input).

    ``ORCAInterface._run_orca`` (the subprocess boundary) is mocked to store
    the generated ``.inp`` text and never execute ORCA, so the full chain
    ``MethodSpec → resolve_spec → render_backend_input → ORCABackend →
    ORCAInterface input writer`` runs unmodified — the returned text is the
    actual ORCA input the workflow would submit.
    """
    from acp.workflows.nmr import _run_giao_for_conformers
    from cccp.backends.orca import ORCABackend
    from cccp.calculation.context import TaskContext
    from cccp.calculation.tasks.nmr_shielding import run_nmr_shielding as real_shielding
    from cccp.config import load_config
    from cccp.qc.interfaces.orca import ORCAInterface

    cfg = load_config()
    backend = ORCABackend(config=cfg)
    structure = _make_structure(["C", "H", "H", "H", "H"], [(0.0, 0.0, 0.0)] * 5)
    inputs: list[str] = []
    levels: list[Any] = []

    def _fake_run_orca(self, input_file, output_file, *args, **kwargs):
        inputs.append(Path(input_file).read_text(encoding="utf-8"))
        return False  # never execute ORCA

    def _fake_shielding(request, *, context=None):
        levels.append(request.level)
        extras = dict((context.capability_extras if context else None) or {})
        return real_shielding(
            request, context=TaskContext(backend=backend, config=cfg, capability_extras=extras)
        )

    with (
        patch.object(ORCAInterface, "_run_orca", _fake_run_orca),
        patch("acp.workflows.nmr.run_nmr_shielding", side_effect=_fake_shielding),
    ):
        _run_giao_for_conformers(
            [(structure, 1.0, 0.0)], nmr_config, tmp_path / "giao", cfg, nmr_config.solvent
        )
    assert levels, "GIAO task core was never invoked"
    assert inputs, "no ORCA input was generated"
    return levels[0], inputs[0]


def test_build_nmr_config_gas_phase_forces_empty_solvent() -> None:
    """T17 contract: ``solvent_model=none`` never falls back to chloroform.

    The recorded effective config must equal what the GIAO level executes,
    and the TMS lookup must key on the gas-phase row (Goodman TMSdata
    ``solvent=none``: 13C 188.029225 / 1H 32.1352666667) instead of the
    chloroform row (188.452125 / 32.1243166667) the old solvent-keyed
    lookup returned.
    """
    conf = _build_test_nmr_config(solvent="", solvent_model="none")
    assert conf.solvent_model == "none"
    assert conf.solvent == ""  # RED (before T21): 'chloroform'
    assert conf.tms_for("13C") == pytest.approx(188.029225)
    assert conf.tms_for("1H") == pytest.approx(32.1352666667)


def test_build_nmr_config_gas_phase_beats_theory_solvent() -> None:
    """An explicit ``solvent_model=none`` beats ``theory.nmr.solvent``."""
    from acp.workflows.nmr import _build_nmr_config
    from cccp.config import load_config

    cfg = load_config(overrides={"theory": {"nmr": {"solvent": "water"}}})
    conf = _build_nmr_config(
        cfg,
        nuclei=None,
        nmr_method=None,
        nmr_basis=None,
        solvent=None,
        boltzmann_temp=None,
        tms_1h=None,
        tms_13c=None,
        error_model=None,
        conformer_preset=None,
        solvent_model="none",
        max_conformers=None,
    )
    assert conf.solvent_model == "none"
    assert conf.solvent == ""  # RED (before T21): 'water'


def test_build_nmr_config_explicit_values_beat_theory_nmr_defaults() -> None:
    """MUST NOT drop the explicit method overrides (regression guard)."""
    from acp.workflows.nmr import _build_nmr_config
    from cccp.config import load_config

    cfg = load_config(
        overrides={
            "theory": {
                "nmr": {
                    "method": "B3LYP",
                    "basis": "def2-SVP",
                    "solvent": "water",
                    "solvent_model": "smd",
                }
            }
        }
    )
    conf = _build_nmr_config(
        cfg,
        nuclei=["13C"],
        nmr_method="mPW1PW91",
        nmr_basis="6-311G(d)",
        solvent="chloroform",
        boltzmann_temp=310.0,
        tms_1h=30.0,
        tms_13c=180.0,
        error_model="goodman-legacy",
        conformer_preset="censo-zero",
        solvent_model="cpcm",
        max_conformers=7,
    )
    assert conf.nmr_method == "mPW1PW91"
    assert conf.nmr_basis == "6-311G(d)"
    assert conf.nuclei == ("13C",)
    assert conf.solvent == "chloroform"
    assert conf.solvent_model == "cpcm"
    assert conf.boltzmann_temp == 310.0
    assert conf.tms_for("1H") == 30.0
    assert conf.tms_for("13C") == 180.0
    assert conf.max_conformers == 7
    assert conf.conformer_preset == "censo-zero"


def test_giao_method_spec_gas_phase_carries_no_solvent(tmp_path: Path) -> None:
    """The task-level MethodSpec never carries a solvent for gas phase —
    even for a directly-built contradictory ``NmrConfig``."""
    conf = NmrConfig(solvent="chloroform", solvent_model="none")
    level, _input_text = _run_giao_capture(conf, tmp_path)
    assert level.solvent_model == "none"
    assert level.solvent in (None, "")  # RED (before T21): 'chloroform'


def test_giao_orca_input_gas_phase_has_no_cpcm(tmp_path: Path) -> None:
    """Acceptance: ``solvent_model=none`` → generated ORCA input has no
    cpcm/SMD token; ``cpcm`` → cpcm present with the right solvent."""
    gas = _build_test_nmr_config(solvent="", solvent_model="none")
    level, gas_input = _run_giao_capture(gas, tmp_path)
    assert level.solvent_model == "none"
    assert "cpcm" not in gas_input.lower()
    assert "smd(" not in gas_input.lower()
    assert "smdsolvent" not in gas_input.lower()

    solvated = _build_test_nmr_config(solvent="chloroform", solvent_model="cpcm")
    level2, solvated_input = _run_giao_capture(solvated, tmp_path)
    assert level2.solvent_model == "cpcm"
    assert "! CPCM(chloroform)" in solvated_input


def test_nmr_config_effective_config_to_dict_round_trip() -> None:
    """The effective-config record is complete + JSON-safe (T24 provenance)."""
    conf = _build_test_nmr_config()
    payload = conf.to_dict()
    assert json.dumps(payload)  # serialisable
    assert payload["nuclei"] == ["1H", "13C"]
    assert payload["nmr_method"] == "mPW1PW91"
    assert payload["nmr_basis"] == "6-311G(d)"
    assert payload["solvent"] == "chloroform"
    assert payload["solvent_model"] == "cpcm"
    assert payload["energy_window_kcal"] == 3.0
    assert payload["max_conformers"] == 10
    assert payload["conformer_preset"] == "censo-light"
    assert payload["boltzmann_temp"] == 298.15
    assert payload["error_model"] == "goodman-legacy"
    assert payload["tms_1h"] == 32.1243166667
    assert payload["tms_13c"] == 188.452125
    assert payload["protocol_fingerprint"] is None  # D-phase placeholder (T29)
    assert conf.protocol_fingerprint is None
