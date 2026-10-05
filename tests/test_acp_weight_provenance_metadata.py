"""Weight-provenance metadata regression tests (confsearch plan todo 3).

Pins the additive metadata keys emitted by the delegated energy workflows:

- ``energy_shared.write_final_outputs`` return dict gains ``temperature_k``,
  ``population_coverage``, ``weight_source`` and ``weight_method`` (existing
  keys and on-disk file formats must not change).
- ``energy_shared.build_result_ensemble`` records gain the screening join key
  ``Structure.metadata["conf_id"]`` (falls back to the built structure id).
- ``workflows.ensemble.run_ensemble_generation`` ``WorkflowResult.metadata``
  gains ``temperature_k`` / ``weight_source`` / ``weight_method`` /
  ``population_coverage``.

Author: QCcalc Team
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest

from cccp.qc.interfaces.censo import CensoConformerRecord, CensoRunResult


def _candidate(
    index: int,
    source: str | None,
    energy: float,
    gibbs: float,
) -> dict[str, Any]:
    cand: dict[str, Any] = {
        "index": index,
        "symbols": ["C", "H", "H"],
        "coordinates": np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.089], [1.027, 0.0, -0.363]]),
        "energy": energy,
        "gibbs": gibbs,
    }
    if source is not None:
        cand["source"] = source
    return cand


def _two_candidates() -> list[dict[str, Any]]:
    return [
        _candidate(0, "CONF2", -154.912345, -154.834525),
        _candidate(1, "CONF1", -154.911876, -154.834033),
    ]


# ---------------------------------------------------------------------------
# (a) write_final_outputs — additive provenance keys
# ---------------------------------------------------------------------------


def test_write_final_outputs_dft_provenance(tmp_path: Path) -> None:
    """Workflow 1 (no external table): DFT weights + population coverage."""
    from acp.workflows.energy_shared import write_final_outputs

    candidates = _two_candidates()
    population_weights = {"CONF2": 0.62, "CONF1": 0.30, "CONF3": 0.08}

    outputs = write_final_outputs(
        candidates,
        tmp_path,
        "ethanol",
        320.0,
        population_weights=population_weights,
    )

    assert outputs["temperature_k"] == pytest.approx(320.0)
    assert outputs["weight_source"] == "dft"
    assert outputs["weight_method"] == "dft_table"
    coverage = outputs["population_coverage"]
    assert 0.0 < coverage < 1.0
    assert coverage == pytest.approx(0.92)


def test_write_final_outputs_censo_table_provenance(tmp_path: Path) -> None:
    """Workflow 2 (external CENSO table): censo provenance, coverage 1.0."""
    from acp.workflows.energy_shared import write_final_outputs

    candidates = _two_candidates()
    external_weights = {"CONF1": 0.4, "CONF2": 0.6}

    outputs = write_final_outputs(
        candidates,
        tmp_path,
        "ethanol",
        298.15,
        external_weights=external_weights,
        external_table_source="censo",
    )

    assert outputs["temperature_k"] == pytest.approx(298.15)
    assert outputs["weight_source"] == "censo"
    assert outputs["weight_method"] == "censo_table_rank1"
    assert outputs["population_coverage"] == pytest.approx(1.0)


def test_write_final_outputs_xtb_table_provenance(tmp_path: Path) -> None:
    """Workflow 2 (external xTB table): xtb provenance."""
    from acp.workflows.energy_shared import write_final_outputs

    candidates = _two_candidates()
    external_weights = {"CONF1": 0.7, "CONF2": 0.3}

    outputs = write_final_outputs(
        candidates,
        tmp_path,
        "ethanol",
        298.15,
        external_weights=external_weights,
        external_table_source="xtb",
    )

    assert outputs["weight_source"] == "xtb"
    assert outputs["weight_method"] == "xtb_table_rank1"
    assert outputs["population_coverage"] == pytest.approx(1.0)


def test_write_final_outputs_legacy_keys_and_files_unchanged(tmp_path: Path) -> None:
    """Existing output keys and on-disk formats survive the additions."""
    from acp.workflows.energy_shared import write_final_outputs

    candidates = _two_candidates()

    outputs = write_final_outputs(
        candidates,
        tmp_path,
        "ethanol",
        298.15,
        external_weights={"CONF1": 0.4, "CONF2": 0.6},
    )

    for key in (
        "all_conformers_xyz",
        "thermo_csv",
        "global_min_xyz",
        "total_gibbs_hartree",
        "total_gibbs_kcal_mol",
        "ensemble_thermo_json",
        "boltzmann_table_json",
    ):
        assert key in outputs, f"legacy output key missing: {key}"

    assert Path(outputs["all_conformers_xyz"]).exists()
    assert Path(outputs["thermo_csv"]).exists()
    assert Path(outputs["global_min_xyz"]).exists()
    assert Path(outputs["ensemble_thermo_json"]).exists()
    assert Path(outputs["boltzmann_table_json"]).exists()

    csv_header = Path(outputs["thermo_csv"]).read_text().splitlines()[0]
    assert csv_header == (
        "index,rank,energy_hartree,gibbs_correction,gibbs_hartree,"
        "h_correction,u_correction,s_total,g_conc,weight,source"
    )


# ---------------------------------------------------------------------------
# (b) build_result_ensemble — conf_id join key
# ---------------------------------------------------------------------------


def _parent_structure() -> Any:
    from acp.core.models import Structure

    return Structure(
        id="ethanol",
        charge=0,
        multiplicity=1,
        symbols=["C", "H", "H"],
        coordinates=[[0.0, 0.0, 0.0], [0.0, 0.0, 1.089], [1.027, 0.0, -0.363]],
    )


def test_build_result_ensemble_conf_id_join_key() -> None:
    """Each record metadata carries conf_id = source (rank1-style candidate)."""
    from acp.workflows.energy_shared import build_result_ensemble

    candidates = _two_candidates()
    for i, cand in enumerate(candidates):
        cand["rank"] = i + 1
        cand["weight"] = 0.6 if i == 0 else 0.4

    ensemble = build_result_ensemble(candidates, _parent_structure())

    assert len(ensemble.records) == 2
    by_source = {rec.structure.metadata.get("source"): rec for rec in ensemble.records}
    for source, rec in by_source.items():
        assert rec.structure.metadata["conf_id"] == source
        assert rec.structure.metadata["rank"] is not None
        assert rec.structure.metadata["source"] == source

    rank1 = by_source["CONF2"]
    assert rank1.structure.metadata["conf_id"] == "CONF2"


def test_build_result_ensemble_conf_id_falls_back_to_struct_id() -> None:
    """Missing source → conf_id falls back to the built structure id."""
    from acp.workflows.energy_shared import build_result_ensemble

    candidates = [_candidate(0, None, -154.912345, -154.834525)]
    candidates[0]["rank"] = 1
    candidates[0]["weight"] = 1.0

    ensemble = build_result_ensemble(candidates, _parent_structure())

    assert len(ensemble.records) == 1
    metadata = ensemble.records[0].structure.metadata
    assert metadata["conf_id"] == ensemble.records[0].structure.id
    assert metadata["conf_id"] == "ethanol_conf000"


# ---------------------------------------------------------------------------
# (c) run_ensemble_generation — metadata provenance
# ---------------------------------------------------------------------------


def _mock_censo_result(temperature: float) -> CensoRunResult:
    rec1 = CensoConformerRecord(
        conf_id="CONF1",
        frame_index=0,
        energy=-154.912345,
        gsolv=-0.004521,
        grrho=0.082341,
        gtot=-154.834525,
        coordinates=np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.089], [1.027, 0.0, -0.363]]),
        symbols=["C", "H", "H"],
    )
    rec2 = CensoConformerRecord(
        conf_id="CONF2",
        frame_index=1,
        energy=-154.911876,
        gsolv=-0.004612,
        grrho=0.082455,
        gtot=-154.834033,
        coordinates=np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.089], [-1.027, 0.0, -0.363]]),
        symbols=["C", "H", "H"],
    )
    result = CensoRunResult(
        preset="censo-light",
        records=[rec1, rec2],
        final_part="screening",
        temperature=temperature,
    )
    result.sort_by_gtot()
    return result


def test_run_ensemble_generation_metadata_censo_provenance(
    tmp_path: Path,
    sample_config: dict[str, Any],
) -> None:
    """CENSO branch: metadata carries the resolved T + censo table source."""
    from acp.workflows.ensemble import run_ensemble_generation

    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text(
        "3\nFrame 0\nC 0 0 0\nH 0 0 1.089\nH 1.027 0 -0.363\n"
        "3\nFrame 1\nC 0 0 0\nH 0 0 1.089\nH -1.027 0 -0.363\n"
    )

    mock_result = _mock_censo_result(temperature=318.0)
    # todo 26b rewire: the CENSO seam is now the task-core helper imported
    # into ``ensemble``; a fake returns the typed CensoRunResult directly.
    with patch(
        "acp.workflows.ensemble._censo_refine_via_task", return_value=mock_result
    ) as mock_censo:
        result = run_ensemble_generation(
            input_source=str(input_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-light",
            config=sample_config,
        )

    mock_censo.assert_called_once()

    assert result.status == "completed"
    metadata = result.metadata
    assert metadata["temperature_k"] == pytest.approx(318.0)
    assert metadata["weight_source"] == "censo"
    assert metadata["weight_method"] == "censo_table"
    assert metadata["population_coverage"] == pytest.approx(1.0)


def test_run_ensemble_generation_metadata_censo_zero_xtb_provenance(
    tmp_path: Path,
    sample_config: dict[str, Any],
) -> None:
    """censo-zero passthrough: metadata carries xtb table source."""
    from acp.workflows.ensemble import run_ensemble_generation

    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text(
        "3\n-154.912345 Frame 0\nC 0 0 0\nH 0 0 1.089\nH 1.027 0 -0.363\n"
        "3\n-154.911876 Frame 1\nC 0 0 0\nH 0 0 1.089\nH -1.027 0 -0.363\n"
    )

    result = run_ensemble_generation(
        input_source=str(input_xyz),
        output_dir=str(tmp_path / "out"),
        preset="censo-zero",
        config=sample_config,
    )

    assert result.status == "completed"
    metadata = result.metadata
    assert metadata["temperature_k"] == pytest.approx(298.15)
    assert metadata["weight_source"] == "xtb"
    assert metadata["weight_method"] == "xtb_table"
    assert metadata["population_coverage"] == pytest.approx(1.0)
