"""Integration test for the NMR workflow (DevDoc §5).

Mocks the ORCA ``nmr_shielding`` capability and the conformer-generation
backend so the full analysis pipeline (stages 0–8) runs without external
binaries. Exercises both the assigned and unassigned matching paths and
verifies the report artifacts are written.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from acp.calculations.progress import ProgressReporter
from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.nmr.models import ConformerShielding


def _make_orca_backend_cls(
    shieldings_by_charge: dict[int, dict[int, dict[str, str | float]]],
) -> MagicMock:
    """Build a mock ORCA backend class returning canned shieldings."""
    backend_cls = MagicMock()

    def _ctor(cfg, **kwargs):
        backend = MagicMock()
        backend.is_available.return_value = True

        def _nmr(
            coordinates, symbols, charge=0, multiplicity=1, output_dir=None, nuclei=None, **kw
        ):
            result = MagicMock()
            result.success = True
            result.error_message = None
            result.log_file = Path(str(output_dir)) / "nmr.out" if output_dir else None
            key = charge
            sh = shieldings_by_charge.get(key, shieldings_by_charge.get(0, {}))
            result.metadata = {"shieldings": sh}
            return result

        backend.nmr_shielding.side_effect = _nmr
        return backend

    backend_cls.side_effect = _ctor
    return backend_cls


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
        patch("acp.workflows.nmr.get_backend") as get_backend,
    ):
        reader = MagicMock()
        reader.read.return_value = structure
        reader_cls.return_value = reader

        orca_backend = MagicMock()
        orca_backend.is_available.return_value = True
        orca_backend.nmr_shielding.side_effect = [
            MagicMock(
                success=True, error_message=None, log_file=None, metadata={"shieldings": sh_a}
            ),
            MagicMock(
                success=True, error_message=None, log_file=None, metadata={"shieldings": sh_b}
            ),
        ]
        get_backend.return_value = MagicMock(return_value=orca_backend)

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
        patch("acp.workflows.nmr.get_backend") as get_backend,
    ):
        reader = MagicMock()
        reader.read.return_value = structure
        reader_cls.return_value = reader
        orca_backend = MagicMock()
        orca_backend.is_available.return_value = True
        orca_backend.nmr_shielding.return_value = MagicMock(
            success=True, error_message=None, log_file=None, metadata={"shieldings": sh}
        )
        get_backend.return_value = MagicMock(return_value=orca_backend)

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
        patch("acp.workflows.nmr.get_backend") as get_backend,
        patch("acp.workflows.nmr.enumerate_candidates", return_value=fake_isomers),
    ):
        reader = MagicMock()
        reader.read.return_value = structure
        reader_cls.return_value = reader

        orca_backend = MagicMock()
        orca_backend.is_available.return_value = True
        orca_backend.nmr_shielding.return_value = MagicMock(
            success=True, error_message=None, log_file=None, metadata={"shieldings": sh}
        )
        get_backend.return_value = MagicMock(return_value=orca_backend)

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
        patch("acp.workflows.nmr.get_backend") as get_backend,
    ):
        reader = MagicMock()
        reader.read.return_value = structure
        reader_cls.return_value = reader
        orca_backend = MagicMock()
        orca_backend.is_available.return_value = True
        orca_backend.nmr_shielding.return_value = MagicMock(
            success=True, error_message=None, log_file=None, metadata={"shieldings": sh}
        )
        get_backend.return_value = MagicMock(return_value=orca_backend)

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
