# pyright: basic, reportAny=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportMissingTypeArgument=false, reportUnusedCallResult=false, reportUnusedParameter=false, reportUnknownLambdaType=false, reportPrivateUsage=false
"""Progress reporting tests for the unified Confsearch engine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp.calculations.progress import ProgressReporter
from acp.confsearch import ConfsearchEngine, ConfsearchRequest, ConfsearchResult
from acp.confsearch.contracts import ProtocolOutcome
from acp.confsearch.engine import CONFSEARCH_STAGES
from acp.confsearch.protocols import PROTOCOL_RUNNERS


def _stub_structure():
    from acp.core.models import Structure

    return Structure(
        id="water",
        charge=0,
        multiplicity=1,
        symbols=["O", "H", "H"],
        coordinates=[[0.0, 0.0, 0.0], [0.9, 0.0, 0.0], [-0.3, 0.9, 0.0]],
        metadata={},
    )


def _stub_outcome(request: ConfsearchRequest, _overlay: dict) -> ProtocolOutcome:
    return ProtocolOutcome(
        records=[
            {
                "conf_id": "c1",
                "symbols": ["O", "H", "H"],
                "coordinates": [[0.0, 0.0, 0.0], [0.9, 0.0, 0.0], [-0.3, 0.9, 0.0]],
                "energy_hartree": -76.01,
                "free_energy_hartree": -76.0,
            },
            {
                "conf_id": "c2",
                "symbols": ["O", "H", "H"],
                "coordinates": [[0.1, 0.0, 0.0], [0.9, 0.1, 0.0], [-0.2, 0.9, 0.1]],
                "energy_hartree": -76.00,
                "free_energy_hartree": -75.99,
            },
        ],
        temperature_k=298.15,
        refined_conf_ids=["c1"] if request.refinement_policy == "rank1" else [],
        sampling={"method": "stub"},
    )


def test_engine_reports_confsearch_stages_and_candidate_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "job.json").write_text("{}", encoding="utf-8")
    (tmp_path / "task.json").write_text("{}", encoding="utf-8")
    monkeypatch.setitem(PROTOCOL_RUNNERS, "censo-crest", _stub_outcome)

    from acp.io.structures import StructureReader

    xyz = tmp_path / "water.xyz"
    xyz.write_text(
        "3\nwater\nO 0.0 0.0 0.0\nH 0.9 0.0 0.0\nH -0.3 0.9 0.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(StructureReader, "read", lambda self, *a, **k: _stub_structure())

    reporter = ProgressReporter(
        tmp_path,
        job_name="Confsearch",
        stages=list(CONFSEARCH_STAGES),
        min_interval=0.0,
    )
    request = ConfsearchRequest(
        input_source=str(xyz),
        output_dir=tmp_path,
        protocol="censo-crest",
        refinement_policy="rank1",
    )

    result = ConfsearchEngine(progress_reporter=reporter).run(request)

    assert result.status == "completed"
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert list(state["stages"]) == list(CONFSEARCH_STAGES)
    assert all(info["status"] == "completed" for info in state["stages"].values())
    metrics = {metric["key"]: metric for metric in state["live_metrics"]}
    assert metrics["conformers_sampled"]["value"] == "2"
    assert metrics["conformers_sampled"]["label_key"] == "live.conformers_sampled"
    assert metrics["conformers_kept"]["value"] == "1"
    assert metrics["conformers_kept"]["label_key"] == "live.conformers_kept"


def test_engine_reports_only_stages_when_protocol_returns_no_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "job.json").write_text("{}", encoding="utf-8")
    (tmp_path / "task.json").write_text("{}", encoding="utf-8")

    def _missing_count(_request: ConfsearchRequest, _overlay: dict) -> ProtocolOutcome | None:
        return None

    monkeypatch.setitem(PROTOCOL_RUNNERS, "censo-crest", _missing_count)
    reporter = ProgressReporter(
        tmp_path,
        job_name="Confsearch",
        stages=list(CONFSEARCH_STAGES),
        min_interval=0.0,
    )
    request = ConfsearchRequest(
        input_source="CCO",
        output_dir=tmp_path,
        protocol="censo-crest",
    )

    result = ConfsearchEngine(progress_reporter=reporter).run(request)

    assert result.status == "failed"
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert set(state["stages"]) == set(CONFSEARCH_STAGES)
    assert "live_metrics" not in state


def test_confsearch_cli_constructs_progress_reporter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import acp.cli as acp_cli

    args = acp_cli.build_parser().parse_args(
        ["run", "Confsearch", "--input", "CCO", "--output", str(tmp_path)]
    )
    captured: list[ProgressReporter | None] = []

    class _StubEngine:
        def run(
            self,
            _request: ConfsearchRequest,
            *,
            progress_reporter: ProgressReporter | None = None,
        ) -> ConfsearchResult:
            captured.append(progress_reporter)
            assert progress_reporter is not None
            progress_reporter.initialize()
            progress_reporter.complete()
            return ConfsearchResult(
                status="completed",
                protocol="censo-crest",
                profile="default",
                refinement_policy="screen",
            )

    monkeypatch.setattr("acp.confsearch.ConfsearchEngine", _StubEngine)

    assert acp_cli._handle_confsearch(args) == 0
    reporter = captured[0]
    assert reporter is not None
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert list(state["stages"]) == list(CONFSEARCH_STAGES)
