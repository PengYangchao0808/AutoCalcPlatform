"""Protocol-runner weight provenance + temperature forwarding (plan todo 5).

Red-first pins for the per-policy provenance wiring in the Confsearch
protocol runners:

* ``rank1`` applies the delegated screening weight table
  (``boltzmann_table.json`` via ``workflow_metadata``); a missing or
  unreadable table degrades to ``weight_source="computed"`` with a warning
  and never raises (D13).
* ``cumulative-99`` / ``all`` carry DFT provenance from the delegated
  workflow metadata; ``screen`` carries ensemble metadata provenance;
  pure-xTB protocols carry ``xtb`` provenance.
* An explicit ``ConfsearchRequest.temperature`` is forwarded to the
  delegated workflows through a non-mutating copy: ``levels.thermo.temperature``
  for the runners that accept ``levels``, and the ``config["censo"]["temperature"]``
  channel for the ``run_ensemble_generation`` callers (no ``levels`` knob).
  Explicit ``levels.thermo.temperature`` always wins.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from acp.confsearch.contracts import ConfsearchRequest
from acp.confsearch.protocols.censo_crest import run_censo_crest
from acp.confsearch.protocols.xtb_crest import run_xtb_crest
from acp.confsearch.protocols.xtb_md import run_xtb_md
from acp.confsearch.protocols.xtbmd_censo import run_xtbmd_censo
from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.core.workflow import WorkflowResult

# ── fixtures ───────────────────────────────────────────────────────────


def _request(tmp_path: Path, *, protocol: str, policy: str, **kw: Any) -> ConfsearchRequest:
    return ConfsearchRequest(
        input_source="CCO",
        output_dir=tmp_path,
        protocol=protocol,
        refinement_policy=policy,
        **kw,
    )


def _record(conf_id: str, source: str, energy: float) -> StructureRecord:
    return StructureRecord(
        structure=Structure(
            id=conf_id,
            symbols=["O", "H", "H"],
            coordinates=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.96], [0.0, 0.93, -0.24]],
            metadata={"conf_id": conf_id, "source": source},
        ),
        energy_hartree=energy,
        free_energy_hartree=energy - 0.001,
        weight=0.5,
    )


def _workflow_result(metadata: dict[str, Any]) -> WorkflowResult:
    return WorkflowResult(
        status="completed",
        ensemble=StructureEnsemble(
            records=[
                _record("conf_0001", "CONF1", -0.5),
                _record("conf_0002", "CONF2", -0.4),
            ]
        ),
        stages_completed=["crest", "censo"],
        metadata=metadata,
    )


def _write_table(tmp_path: Path, **overrides: Any) -> str:
    payload: dict[str, Any] = {
        "temperature_k": 320.0,
        "source": "censo",
        "weights": {"CONF1": 0.7, "CONF2": 0.3},
    }
    payload.update(overrides)
    path = tmp_path / "boltzmann_table.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


# ── rank1: screening weight table application (D1/M3/D13) ──────────────


def test_rank1_applies_screening_weight_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = _write_table(tmp_path)
    monkeypatch.setattr(
        "acp.workflows.energy.run_conformer_energy",
        lambda **kwargs: _workflow_result(
            {
                "boltzmann_table_json": table,
                "temperature_k": 310.0,
                "weight_method": "censo_table_rank1",
                "refined_conf_ids": ["CONF1"],
            }
        ),
    )
    outcome = run_censo_crest(_request(tmp_path, protocol="censo-crest", policy="rank1"), {})
    assert outcome.weight_table == {"CONF1": 0.7, "CONF2": 0.3}
    assert outcome.weight_source == "censo"
    assert outcome.weight_method == "censo_table_rank1"
    assert outcome.population_coverage == 1.0
    # the file's resolved temperature wins over the metadata value
    assert outcome.temperature_k == 320.0
    assert outcome.energy_kind == "censo"


def test_rank1_xtb_source_table_keeps_source_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M3: the cheap rank1 branch writes source=xtb — the label must follow."""
    table = _write_table(tmp_path, source="xtb", weights={"CONF1": 0.9, "CONF2": 0.1})
    monkeypatch.setattr(
        "acp.workflows.energy.run_conformer_energy",
        lambda **kwargs: _workflow_result({"boltzmann_table_json": table}),
    )
    outcome = run_censo_crest(_request(tmp_path, protocol="censo-crest", policy="rank1"), {})
    assert outcome.weight_source == "xtb"
    assert outcome.weight_method == "xtb_table_rank1"


def test_rank1_missing_table_degrades_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        "acp.workflows.energy.run_conformer_energy",
        lambda **kwargs: _workflow_result({"temperature_k": 300.0}),
    )
    with caplog.at_level(logging.WARNING, logger="acp.confsearch.protocols._common"):
        outcome = run_censo_crest(_request(tmp_path, protocol="censo-crest", policy="rank1"), {})
    assert outcome.weight_table is None
    assert outcome.weight_source == "computed"
    assert outcome.weight_method == "computed"
    assert outcome.population_coverage is None
    assert any("boltzmann" in record.message.lower() for record in caplog.records)


def test_rank1_corrupt_table_degrades_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    bad = tmp_path / "boltzmann_table.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(
        "acp.workflows.energy.run_conformer_energy",
        lambda **kwargs: _workflow_result({"boltzmann_table_json": str(bad)}),
    )
    with caplog.at_level(logging.WARNING, logger="acp.confsearch.protocols._common"):
        outcome = run_censo_crest(_request(tmp_path, protocol="censo-crest", policy="rank1"), {})
    assert outcome.weight_source == "computed"
    assert outcome.weight_method == "computed"


def test_rank1_invalid_weights_shape_degrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = _write_table(tmp_path, weights={"CONF1": "high", "CONF2": None})
    monkeypatch.setattr(
        "acp.workflows.energy.run_conformer_energy",
        lambda **kwargs: _workflow_result({"boltzmann_table_json": table}),
    )
    outcome = run_censo_crest(_request(tmp_path, protocol="censo-crest", policy="rank1"), {})
    assert outcome.weight_source == "computed"


# ── cumulative-99 / all: DFT provenance from metadata ──────────────────


@pytest.mark.parametrize("policy", ["cumulative-99", "all"])
def test_dft_policies_take_metadata_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: str
) -> None:
    table = _write_table(tmp_path)
    monkeypatch.setattr(
        "acp.workflows.energy.run_conformer_energy",
        lambda **kwargs: _workflow_result(
            {
                "boltzmann_table_json": table,
                "temperature_k": 310.0,
                "population_coverage": 0.97,
                "weight_method": "dft_table",
                "refined_conf_ids": ["CONF1", "CONF2"],
            }
        ),
    )
    outcome = run_censo_crest(_request(tmp_path, protocol="censo-crest", policy=policy), {})
    assert outcome.weight_source == "dft"
    assert outcome.weight_method == "dft_table"
    assert outcome.population_coverage == 0.97
    assert outcome.temperature_k == 310.0
    assert outcome.energy_kind == "censo"
    # the screening table must NOT be read for non-rank1 policies
    assert outcome.weight_table is None


def test_dft_policy_method_defaults_when_metadata_sparse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "acp.workflows.energy.run_conformer_energy",
        lambda **kwargs: _workflow_result({}),
    )
    outcome = run_censo_crest(
        _request(tmp_path, protocol="censo-crest", policy="cumulative-99"), {}
    )
    assert outcome.weight_source == "dft"
    assert outcome.weight_method == "dft_table"
    assert outcome.population_coverage is None
    assert outcome.temperature_k == 298.15


# ── screen: ensemble metadata provenance ───────────────────────────────


def test_screen_takes_ensemble_metadata_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "acp.workflows.ensemble.run_ensemble_generation",
        lambda **kwargs: _workflow_result(
            {
                "weight_source": "censo",
                "weight_method": "censo_table",
                "population_coverage": 1.0,
                "temperature_k": 300.0,
            }
        ),
    )
    outcome = run_censo_crest(_request(tmp_path, protocol="censo-crest", policy="screen"), {})
    assert outcome.weight_source == "censo"
    assert outcome.weight_method == "censo_table"
    assert outcome.population_coverage == 1.0
    assert outcome.temperature_k == 300.0
    assert outcome.energy_kind == "censo"


def test_screen_defaults_when_metadata_sparse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "acp.workflows.ensemble.run_ensemble_generation",
        lambda **kwargs: _workflow_result({}),
    )
    outcome = run_censo_crest(_request(tmp_path, protocol="censo-crest", policy="screen"), {})
    assert outcome.weight_source == "censo"
    assert outcome.weight_method == "censo_table"
    assert outcome.population_coverage == 1.0
    assert outcome.temperature_k == 298.15


# ── xtbmd-censo: same three-way wiring ─────────────────────────────────


def test_xtbmd_censo_rank1_applies_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    table = _write_table(tmp_path)
    monkeypatch.setattr(
        "acp.workflows.xtbmd_censo_energy.run_xtbmd_censo_energy",
        lambda **kwargs: _workflow_result({"boltzmann_table_json": table, "temperature_k": 315.0}),
    )
    outcome = run_xtbmd_censo(_request(tmp_path, protocol="xtbmd-censo", policy="rank1"), {})
    assert outcome.weight_table == {"CONF1": 0.7, "CONF2": 0.3}
    assert outcome.weight_source == "censo"
    assert outcome.weight_method == "censo_table_rank1"
    assert outcome.population_coverage == 1.0
    assert outcome.temperature_k == 320.0
    assert outcome.energy_kind == "censo"


def test_xtbmd_censo_cumulative_takes_dft_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "acp.workflows.xtbmd_censo_energy.run_xtbmd_censo_energy",
        lambda **kwargs: _workflow_result(
            {
                "temperature_k": 315.0,
                "population_coverage": 0.98,
                "weight_method": "dft_table",
            }
        ),
    )
    outcome = run_xtbmd_censo(
        _request(tmp_path, protocol="xtbmd-censo", policy="cumulative-99"), {}
    )
    assert outcome.weight_source == "dft"
    assert outcome.weight_method == "dft_table"
    assert outcome.population_coverage == 0.98
    assert outcome.temperature_k == 315.0


def test_xtbmd_censo_screen_takes_metadata_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "acp.workflows.xtbmd_censo_energy.run_xtbmd_censo_energy",
        lambda **kwargs: _workflow_result(
            {
                "weight_source": "censo",
                "weight_method": "censo_table",
                "population_coverage": 1.0,
                "temperature_k": 310.0,
            }
        ),
    )
    outcome = run_xtbmd_censo(_request(tmp_path, protocol="xtbmd-censo", policy="screen"), {})
    assert outcome.weight_source == "censo"
    assert outcome.weight_method == "censo_table"
    assert outcome.population_coverage == 1.0
    assert outcome.temperature_k == 310.0


# ── pure-xTB protocols: constant xtb provenance ────────────────────────


def test_xtb_crest_carries_xtb_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "acp.workflows.ensemble.run_ensemble_generation",
        lambda **kwargs: _workflow_result(
            {"weight_source": "xtb", "weight_method": "xtb_table", "temperature_k": 310.0}
        ),
    )
    outcome = run_xtb_crest(_request(tmp_path, protocol="xtb-crest", policy="screen"), {})
    assert outcome.weight_source == "xtb"
    assert outcome.weight_method == "xtb_table"
    assert outcome.population_coverage == 1.0
    assert outcome.temperature_k == 310.0
    assert outcome.energy_kind == "xtb"


def test_xtb_md_carries_xtb_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # minimal single-atom ensemble so the real xtb_passthrough_result can
    # parse titles + coordinates from the fixture file
    ensemble_xyz = tmp_path / "cluster.xyz"
    ensemble_xyz.write_text("1\n-0.5\nHe 0.0 0.0 0.0\n1\n-0.4\nHe 0.0 0.0 0.0\n", encoding="utf-8")

    class _FakeIsostat:
        def __init__(self, cfg: dict[str, Any] | None = None) -> None:
            pass

        def cluster(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(success=True, output_file=str(ensemble_xyz), error_message=None)

    def fake_filter(*args: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(ensemble_xyz=ensemble_xyz, n_after_filter=2)

    monkeypatch.setattr(
        "acp.io.structures.StructureReader.read",
        lambda self, *a, **k: Structure(
            id="water",
            symbols=["O", "H", "H"],
            coordinates=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.96], [0.0, 0.93, -0.24]],
        ),
    )
    monkeypatch.setattr(
        "acp.workflows.xtbmd_md.run_md_replicas",
        lambda *a, **k: SimpleNamespace(
            success=True, metadata={"n_frames": 2, "replica_frames": None}, error_message=None
        ),
    )
    monkeypatch.setattr(
        "acp.workflows.xtbmd_censo_energy._batch_opt_frames",
        lambda *a, **k: SimpleNamespace(n_ok=2),
    )
    monkeypatch.setattr("acp.backends.registry.get_backend", lambda name: _FakeIsostat)
    monkeypatch.setattr("acp.workflows.xtbmd_censo_energy._filter_energy_window", fake_filter)

    outcome = run_xtb_md(
        _request(tmp_path, protocol="xtb-md", policy="screen", temperature=310.0), {}
    )
    assert outcome.weight_source == "xtb"
    assert outcome.weight_method == "xtb_table"
    assert outcome.population_coverage == 1.0
    assert outcome.energy_kind == "xtb"
    assert outcome.temperature_k == 310.0
    assert len(outcome.records) == 2


# ── temperature forwarding (D6 / Metis M2) ─────────────────────────────


def test_temperature_forwarded_as_levels_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_energy(**kwargs: Any) -> WorkflowResult:
        captured.update(kwargs)
        return _workflow_result({"boltzmann_table_json": _write_table(tmp_path)})

    monkeypatch.setattr("acp.workflows.energy.run_conformer_energy", fake_energy)
    levels = {"dft_opt": {"method": "wB97X-D4"}}
    request = _request(
        tmp_path, protocol="censo-crest", policy="rank1", temperature=320.0, levels=levels
    )
    run_censo_crest(request, {})
    assert captured["levels"] == {
        "dft_opt": {"method": "wB97X-D4"},
        "thermo": {"temperature": 320.0},
    }
    # the request's own levels dict is never mutated
    assert request.levels == {"dft_opt": {"method": "wB97X-D4"}}


def test_explicit_levels_thermo_temperature_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_energy(**kwargs: Any) -> WorkflowResult:
        captured.update(kwargs)
        return _workflow_result({"boltzmann_table_json": _write_table(tmp_path)})

    monkeypatch.setattr("acp.workflows.energy.run_conformer_energy", fake_energy)
    request = _request(
        tmp_path,
        protocol="censo-crest",
        policy="rank1",
        temperature=320.0,
        levels={"thermo": {"temperature": 350.0}},
    )
    run_censo_crest(request, {})
    assert captured["levels"] == {"thermo": {"temperature": 350.0}}


def test_no_forwarding_without_explicit_request_temperature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_energy(**kwargs: Any) -> WorkflowResult:
        captured.update(kwargs)
        return _workflow_result({"boltzmann_table_json": _write_table(tmp_path)})

    monkeypatch.setattr("acp.workflows.energy.run_conformer_energy", fake_energy)
    run_censo_crest(_request(tmp_path, protocol="censo-crest", policy="rank1"), {})
    assert captured["levels"] is None


def test_xtbmd_censo_temperature_forwarded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_xtbmd(**kwargs: Any) -> WorkflowResult:
        captured.update(kwargs)
        return _workflow_result({"boltzmann_table_json": _write_table(tmp_path)})

    monkeypatch.setattr("acp.workflows.xtbmd_censo_energy.run_xtbmd_censo_energy", fake_xtbmd)
    run_xtbmd_censo(
        _request(tmp_path, protocol="xtbmd-censo", policy="rank1", temperature=320.0), {}
    )
    assert captured["levels"] == {"thermo": {"temperature": 320.0}}


def test_xtb_crest_temperature_forwarded_via_config_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_ensemble_generation has no levels knob — the config channel is
    ``cfg["censo"]["temperature"]`` (censo-zero passthrough + CENSO rcfile)."""
    captured: dict[str, Any] = {}

    def fake_ensemble(**kwargs: Any) -> WorkflowResult:
        captured.update(kwargs)
        return _workflow_result({"temperature_k": 310.0})

    monkeypatch.setattr("acp.workflows.ensemble.run_ensemble_generation", fake_ensemble)
    config = {"censo": {"ewin": 6.0}}
    request = _request(
        tmp_path, protocol="xtb-crest", policy="screen", temperature=310.0, config=config
    )
    run_xtb_crest(request, {})
    assert captured["config"]["censo"]["temperature"] == 310.0
    assert captured["config"]["censo"]["ewin"] == 6.0
    # the request's own config dict is never mutated
    assert config == {"censo": {"ewin": 6.0}}
    assert request.config is config


def test_censo_crest_screen_temperature_forwarded_via_config_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_ensemble(**kwargs: Any) -> WorkflowResult:
        captured.update(kwargs)
        return _workflow_result({"temperature_k": 310.0})

    monkeypatch.setattr("acp.workflows.ensemble.run_ensemble_generation", fake_ensemble)
    run_censo_crest(
        _request(tmp_path, protocol="censo-crest", policy="screen", temperature=325.0), {}
    )
    assert captured["config"]["censo"]["temperature"] == 325.0


def test_explicit_levels_thermo_wins_over_config_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """levels.thermo.temperature pins the temperature — no config override."""
    captured: dict[str, Any] = {}

    def fake_ensemble(**kwargs: Any) -> WorkflowResult:
        captured.update(kwargs)
        return _workflow_result({"temperature_k": 350.0})

    monkeypatch.setattr("acp.workflows.ensemble.run_ensemble_generation", fake_ensemble)
    config = {"censo": {"temperature": 298.15}}
    run_xtb_crest(
        _request(
            tmp_path,
            protocol="xtb-crest",
            policy="screen",
            temperature=310.0,
            levels={"thermo": {"temperature": 350.0}},
            config=config,
        ),
        {},
    )
    assert captured["config"] is config  # untouched — explicit levels win


def test_outcome_metadata_carries_delegated_temperature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """outcome_from_workflow_result consumes metadata temperature_k (D6)."""
    monkeypatch.setattr(
        "acp.workflows.energy.run_conformer_energy",
        lambda **kwargs: _workflow_result({"temperature_k": 333.0}),
    )
    outcome = run_censo_crest(
        _request(tmp_path, protocol="censo-crest", policy="cumulative-99"), {}
    )
    assert outcome.temperature_k == 333.0
    assert np.isfinite(outcome.temperature_k)
