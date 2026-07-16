# pyright: reportMissingTypeStubs=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownLambdaType=false, reportUnusedCallResult=false, reportUnannotatedClassAttribute=false, reportArgumentType=false
"""Tests for the benchmark meta-workflow and CLI entry point."""

from __future__ import annotations

from pathlib import Path

import pytest

import acp.workflows.benchmark as benchmark_workflow
from acp import cli as acp_cli
from acp.core.workflow import WorkflowResult


def _write_input_xyz(path: Path) -> None:
    """Write a minimal XYZ file for benchmark tests."""
    path.write_text("1\nmock\nH 0.0 0.0 0.0\n", encoding="utf-8")


def _candidate(conformer_id: str, energy: float, weight: float) -> dict[str, int | float | str]:
    """Create one normalized legacy-candidate payload."""
    index = int(conformer_id.split("_")[-1])
    return {
        "index": index,
        "energy": energy,
        "gibbs_energy": energy,
        "g_conc": energy,
        "weight": weight,
        "source_file": f"/mock/{conformer_id}.xyz",
    }


def _workflow_metadata(
    output_dir: Path,
    name: str,
    candidates: list[dict[str, int | float | str]],
) -> dict[str, object]:
    """Build workflow metadata mirroring the conformer workflow contract."""
    molecule_dir = output_dir / name
    final_dir = molecule_dir / "finalDFT"
    final_dir.mkdir(parents=True, exist_ok=True)

    global_min_xyz = molecule_dir / f"{name}_global_min.xyz"
    global_min_xyz.write_text("global minimum\n", encoding="utf-8")
    (final_dir / "all_conformers.xyz").write_text("ensemble\n", encoding="utf-8")

    energies = [float(candidate["g_conc"]) for candidate in candidates]
    return {
        "global_min_xyz": str(global_min_xyz),
        "global_min_energy": min(energies),
        "candidates": candidates,
    }


def test_benchmark_runner_computes_metrics_and_writes_outputs(tmp_path, monkeypatch):
    """BenchmarkRunner writes summary artifacts and computes pairwise metrics."""
    input_xyz = tmp_path / "molecule.xyz"
    _write_input_xyz(input_xyz)

    monkeypatch.setattr(
        benchmark_workflow,
        "load_config",
        lambda overrides=None: {"thermo": {"temperature_k": 298.15}},
    )

    def _fake_run_conformer_search(
        input_source: str,
        output_dir: str | Path,
        protocol: str,
        config,
        name: str | None = None,
        charge: int | None = None,
        multiplicity: int | None = None,
    ) -> WorkflowResult:
        del input_source, config, charge, multiplicity
        candidates_by_protocol = {
            "censo-full": [
                _candidate("conf_000", -10.0010, 0.7),
                _candidate("conf_001", -10.0000, 0.3),
            ],
            "allopt": [
                _candidate("conf_001", -10.0020, 0.6),
                _candidate("conf_000", -10.0015, 0.4),
            ],
        }
        metadata = _workflow_metadata(
            Path(output_dir), name or "molecule", candidates_by_protocol[protocol]
        )
        return WorkflowResult(status="completed", metadata=metadata)

    monkeypatch.setattr(benchmark_workflow, "run_conformer_search", _fake_run_conformer_search)

    runner = benchmark_workflow.BenchmarkRunner(
        config={},
        protocols=["censo-full", "allopt"],
        output_dir=tmp_path / "benchmark",
    )
    summary = runner.run(input_xyz)

    assert (tmp_path / "benchmark" / "benchmark_summary.json").exists()
    assert (tmp_path / "benchmark" / "benchmark_summary.txt").exists()
    assert summary["metrics"]["reference_protocol"] == "allopt"
    assert summary["metrics"]["global_min_id"] == {
        "censo-full": "conf_000",
        "allopt": "conf_001",
    }
    assert summary["metrics"]["global_min_agreement"] is False
    assert summary["metrics"]["deltaG_vs_reference"]["allopt"] == pytest.approx(0.0)
    assert summary["metrics"]["deltaG_vs_reference"]["censo-full"] == pytest.approx(
        0.627509,
        abs=1e-6,
    )
    assert summary["metrics"]["rank_spearman"]["censo-full vs allopt"] == pytest.approx(-1.0)
    assert summary["metrics"]["boltzmann_overlap"]["censo-full vs allopt"] == pytest.approx(0.7)
    assert "Benchmark summary" in runner.format_summary_table(summary)


def test_benchmark_runner_uses_previous_ensemble_for_reference_sp(tmp_path, monkeypatch):
    """reference-sp reuses the latest successful prior ensemble and failures do not abort."""
    input_xyz = tmp_path / "molecule.xyz"
    _write_input_xyz(input_xyz)
    protocol_inputs: dict[str, str] = {}

    monkeypatch.setattr(
        benchmark_workflow,
        "load_config",
        lambda overrides=None: {"thermo": {"temperature_k": 298.15}},
    )

    def _fake_run_conformer_search(
        input_source: str,
        output_dir: str | Path,
        protocol: str,
        config,
        name: str | None = None,
        charge: int | None = None,
        multiplicity: int | None = None,
    ) -> WorkflowResult:
        del config, charge, multiplicity
        protocol_inputs[protocol] = input_source
        if protocol == "allopt":
            raise RuntimeError("allopt failed")

        candidates = [_candidate("conf_000", -10.0, 1.0)]
        metadata = _workflow_metadata(Path(output_dir), name or "molecule", candidates)
        return WorkflowResult(status="completed", metadata=metadata)

    monkeypatch.setattr(benchmark_workflow, "run_conformer_search", _fake_run_conformer_search)

    runner = benchmark_workflow.BenchmarkRunner(
        config={},
        protocols=["censo-full", "allopt", "reference-sp"],
        output_dir=tmp_path / "benchmark",
    )
    summary = runner.run(input_xyz)

    censo_ensemble = (
        tmp_path / "benchmark" / "censo-full" / "molecule" / "finalDFT" / "all_conformers.xyz"
    )
    assert protocol_inputs["censo-full"] == str(tmp_path / "benchmark" / "shared_input.xyz")
    assert protocol_inputs["reference-sp"] == str(censo_ensemble)
    assert summary["protocols"]["allopt"]["success"] is False
    assert summary["protocols"]["reference-sp"]["success"] is True


def test_acp_benchmark_cli_prints_summary(tmp_path, monkeypatch, capsys):
    """`acp benchmark` dispatches through BenchmarkRunner and prints the summary."""
    input_xyz = tmp_path / "molecule.xyz"
    _write_input_xyz(input_xyz)

    class _FakeBenchmarkRunner:
        def __init__(self, config, protocols, output_dir):
            self.config = config
            self.protocols = protocols
            self.output_dir = output_dir

        def run(self, input_xyz: Path, charge: int = 0, multiplicity: int = 1):
            assert input_xyz.exists()
            assert charge == 0
            assert multiplicity == 1
            return {
                "input": str(input_xyz),
                "protocols": {
                    "censo-zero": {"success": True, "walltime_seconds": 0.1},
                    "censo-lite": {"success": True, "walltime_seconds": 0.2},
                },
                "metrics": {},
            }

        def format_summary_table(self, summary):
            del summary
            return "mock benchmark table"

    monkeypatch.setattr(benchmark_workflow, "BenchmarkRunner", _FakeBenchmarkRunner)

    exit_code = acp_cli.main(["benchmark", "--input", str(input_xyz), "--benchmark-level", "quick"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "mock benchmark table" in captured.out
