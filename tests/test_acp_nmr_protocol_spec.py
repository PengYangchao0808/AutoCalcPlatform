"""NmrProtocolSpec: six segments + three modes + unvalidated_protocol (todo 29 / gap G04).

Contract under test (``acp.nmr.protocol`` + protocol-level validation in
``acp.nmr.error_model``):

* the six segments record WHAT ACTUALLY RAN (never an upgrade: censo-light
  without an optimization part must not claim DFT-optimized geometry);
* mode honesty: ``acp_calibrated`` needs the statistical model bound at the
  recorded level + reference state present + optimization executed;
  ``reference_validation`` needs actual reference data; else ``exploratory``;
* any protocol issue (model/level mismatch, missing reference, reference not
  for the level, no optimization) ⇒ ``calibration_status ==
  "unvalidated_protocol"`` — name-only matching is insufficient;
* ``fingerprint()`` is recomputable from the recorded values (to_dict
  round-trip) and per-candidate;
* a missing TMS reference never lets shieldings masquerade as shifts: the
  workflow refuses before stage 4 averaging.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.nmr.protocol import (
    GeometrySegment,
    NmrProtocolSpec,
    PopulationEnergySegment,
    ReferenceSegment,
    SamplingSegment,
    ShieldingSegment,
    StatisticalModelSegment,
    aggregate_protocol_block,
    build_protocol_spec,
    classify_tms_source,
)

_GOODMAN_TMS = {"1H": 32.1243166667, "13C": 188.452125}
_GAS_TMS = {"1H": 32.1352666667, "13C": 188.029225}


def _segments(
    *,
    preset: str = "censo-default",
    generated: bool = True,
    optimization_executed: bool | None = True,
    optimization_level: str | None = "r2scan-3c",
    parts: tuple[str, ...] | None = ("prescreening", "screening", "optimization", "refinement"),
    nmr_method: str = "mPW1PW91",
    nmr_basis: str = "6-311G(d)",
    error_model: str = "goodman-legacy",
    tms_source: str = "exact",
    tms_shieldings: dict[str, float] | None = None,
    missing_nuclei: tuple[str, ...] = (),
    reference_data_present: bool = False,
) -> tuple[Any, ...]:
    return (
        SamplingSegment(
            conformer_preset=preset,
            crest_executed=generated,
            censo_executed=generated and preset != "censo-zero",
            parts=parts,
        ),
        GeometrySegment(
            optimization_executed=optimization_executed,
            optimization_level=optimization_level,
        ),
        PopulationEnergySegment(energy_window_kcal=3.0, boltzmann_temp=298.15),
        ShieldingSegment(nmr_method=nmr_method, nmr_basis=nmr_basis, solvent_model="cpcm"),
        ReferenceSegment(
            tms_source=tms_source,
            effective_solvent="chloroform",
            tms_shieldings=dict(tms_shieldings or _GOODMAN_TMS),
            missing_nuclei=missing_nuclei,
            reference_data_present=reference_data_present,
        ),
        StatisticalModelSegment(
            error_model=error_model,
            dp5_model_id="goodman-dp5",
            dp5_mode="fallback",
            dp5_model_present=True,
        ),
    )


def _spec(**facts: Any) -> NmrProtocolSpec:
    return build_protocol_spec(*_segments(**facts))


# ---------------------------------------------------------------------------
# Mode matrix — each mode's minimum requirements
# ---------------------------------------------------------------------------


def test_acp_calibrated_minimum_requirements() -> None:
    spec = _spec()
    assert spec.mode == "acp_calibrated"
    assert spec.calibration_status == "validated"
    assert spec.issues == ()
    assert spec.geometry.optimization_executed is True


@pytest.mark.parametrize(
    ("kwargs", "forbidden_mode", "expected_issue"),
    [
        (
            {
                "optimization_executed": False,
                "optimization_level": None,
                "parts": ("prescreening", "screening"),
            },
            "acp_calibrated",
            "geometry_not_optimized",
        ),
        ({"missing_nuclei": ("13C",)}, "acp_calibrated", "missing_reference"),
        ({"nmr_method": "B3LYP"}, "acp_calibrated", "statistical_model_not_bound"),
        ({"error_model": "placeholder-student-t"}, "acp_calibrated", "statistical_model_not_bound"),
        ({"tms_source": "unknown"}, "acp_calibrated", "reference_not_for_level"),
    ],
)
def test_acp_calibrated_ablation_each_minimum(
    kwargs: dict[str, Any], forbidden_mode: str, expected_issue: str
) -> None:
    spec = _spec(**kwargs)
    assert expected_issue in spec.issues
    assert spec.calibration_status == "unvalidated_protocol"
    assert spec.mode != forbidden_mode


def test_censo_light_without_optimization_is_exploratory() -> None:
    spec = _spec(
        preset="censo-light",
        parts=("prescreening", "screening"),
        optimization_executed=False,
        optimization_level=None,
    )
    assert spec.mode == "exploratory"
    assert spec.calibration_status == "unvalidated_protocol"
    assert "geometry_not_optimized" in spec.issues
    # honest recording: calling CENSO never fabricates an optimization level
    assert spec.sampling.parts == ("prescreening", "screening")
    assert spec.geometry.optimization_level is None


def test_reference_validation_requires_actual_reference_data() -> None:
    spec = _spec(reference_data_present=True)
    assert spec.mode == "reference_validation"
    spec_without = _spec(reference_data_present=False)
    assert spec_without.mode != "reference_validation"


def test_missing_reference_marks_unvalidated_protocol() -> None:
    spec = _spec(missing_nuclei=("13C",), tms_shieldings={"1H": _GOODMAN_TMS["1H"]})
    assert spec.calibration_status == "unvalidated_protocol"
    assert "missing_reference" in spec.issues
    assert spec.mode == "exploratory"


def test_method_mismatch_marks_unvalidated_protocol() -> None:
    # name-only matching would catch method/basis here — the protocol check
    # must ALSO work when only one segment disagrees with the trained level.
    spec = _spec(nmr_basis="def2-TZVP")
    assert spec.calibration_status == "unvalidated_protocol"
    assert "statistical_model_not_bound" in spec.issues


def test_unknown_reference_source_marks_unvalidated_protocol() -> None:
    spec = _spec(tms_source="unknown")
    assert spec.calibration_status == "unvalidated_protocol"
    assert "reference_not_for_level" in spec.issues


def test_underived_spec_defaults_are_conservative() -> None:
    spec = NmrProtocolSpec(
        **dict(
            zip(
                (
                    "sampling",
                    "geometry",
                    "population_energy",
                    "shielding",
                    "reference",
                    "statistical_model",
                ),
                _segments(),
            )
        )
    )
    assert spec.mode == "exploratory"
    assert spec.calibration_status == "unvalidated_protocol"


def test_derived_is_idempotent() -> None:
    spec = _spec()
    assert spec.derived() == spec
    broken = _spec(optimization_executed=False, optimization_level=None)
    assert broken.derived() == broken


# ---------------------------------------------------------------------------
# Fingerprint — recomputable from the recorded values, per candidate
# ---------------------------------------------------------------------------


def test_fingerprint_recomputable_from_recorded_values() -> None:
    spec = _spec()
    fp = spec.fingerprint()
    assert fp.startswith("v2:")
    rebuilt = NmrProtocolSpec.from_dict(spec.to_dict())
    assert rebuilt == spec
    assert rebuilt.fingerprint() == fp


def test_fingerprint_changes_with_any_segment_value() -> None:
    base = _spec().fingerprint()
    for kwargs in (
        {"nmr_method": "B3LYP"},
        {"missing_nuclei": ("13C",)},
        {"optimization_executed": False, "optimization_level": None},
        {"tms_source": "custom"},
        {"error_model": "placeholder-student-t"},
        {"reference_data_present": True},
        {"preset": "censo-light"},
    ):
        assert _spec(**kwargs).fingerprint() != base, kwargs


def test_per_candidate_fingerprints_differ_with_geometry() -> None:
    conf_a = _spec()
    geometry_b = GeometrySegment(optimization_executed=True, optimization_level="wb97m-v")
    segs = list(_segments())
    segs[1] = geometry_b
    conf_b = build_protocol_spec(*segs)
    assert conf_a.fingerprint() != conf_b.fingerprint()
    block = aggregate_protocol_block([conf_a, conf_b])
    recorded = [entry["fingerprint"] for entry in block["candidates"]]
    assert recorded == [conf_a.fingerprint(), conf_b.fingerprint()]


def test_aggregate_block_is_conservative_and_recomputable() -> None:
    calibrated = _spec()
    light = _spec(
        preset="censo-light",
        parts=("prescreening", "screening"),
        optimization_executed=False,
        optimization_level=None,
    )
    block = aggregate_protocol_block([calibrated, light])
    assert block["mode"] == "exploratory"  # least-claiming wins across candidates
    assert block["calibration_status"] == "unvalidated_protocol"
    assert "geometry_not_optimized" in block["issues"]
    assert block["fingerprint"].startswith("v2:")
    for entry in block["candidates"]:
        fp = entry.pop("fingerprint")
        assert NmrProtocolSpec.from_dict(entry).fingerprint() == fp
        entry["fingerprint"] = fp
    same = aggregate_protocol_block([calibrated, light])
    assert same["fingerprint"] == block["fingerprint"]


# ---------------------------------------------------------------------------
# TMS source — exact / gas-phase fallback / custom / unknown
# ---------------------------------------------------------------------------


def test_classify_tms_source_matrix() -> None:
    assert classify_tms_source("mPW1PW91", "6-311G(d)", "chloroform", _GOODMAN_TMS) == "exact"
    # gas run: the "none" row is keyed for this run's effective solvent
    assert classify_tms_source("mPW1PW91", "6-311G(d)", "", _GAS_TMS) == "exact"
    assert classify_tms_source("mPW1PW91", "6-311G(d)", "none", _GAS_TMS) == "exact"
    # solvated run whose solvent has no row → silent gas-phase row fallback
    assert classify_tms_source("mPW1PW91", "6-311G(d)", "toluene", _GAS_TMS) == "gas_phase_fallback"
    custom = {"1H": 30.0, "13C": 180.0}
    assert classify_tms_source("mPW1PW91", "6-311G(d)", "chloroform", custom) == "custom"
    # level absent from the Goodman table entirely
    assert classify_tms_source("wB97X-D4", "def2-TZVP", "chloroform", custom) == "unknown"


# ---------------------------------------------------------------------------
# Workflow wiring — gate + stage-7/8 surface (protocol_id / provenance)
# ---------------------------------------------------------------------------


def _structure(symbols: list[str]) -> Structure:
    coords = [(0.4 * i, 0.1 * i, -0.05 * i) for i in range(len(symbols))]
    return Structure(
        id="cand",
        charge=0,
        multiplicity=1,
        symbols=symbols,
        coordinates=np.array(coords, dtype=float),
    )


def _shielding_result(shieldings: dict[int, dict[str, Any]]) -> Any:
    from cccp.calculation.requests import TaskKind
    from cccp.calculation.results import NmrShielding, NmrShieldingPayload, TaskResult

    return TaskResult(
        task=TaskKind.NMR_SHIELDING,
        status="completed",
        complete=True,
        payload=NmrShieldingPayload(
            shieldings={
                int(i): NmrShielding(symbol=str(v["symbol"]), isotropic=float(v["isotropic"]))
                for i, v in shieldings.items()
            }
        ),
    )


_SPECTRUM = "C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)"
_SH = {
    0: {"symbol": "C", "isotropic": 148.452125},
    1: {"symbol": "H", "isotropic": 28.1243166667},
    2: {"symbol": "H", "isotropic": 29.1243166667},
    3: {"symbol": "H", "isotropic": 31.1243166667},
    4: {"symbol": "H", "isotropic": 32.1243166667},
}


def test_workflow_missing_reference_gate_refuses_shifts(tmp_path: Path) -> None:
    """A required nucleus without a TMS reference fails the run BEFORE any
    averaging — shieldings must never be used as shifts (todo 29)."""
    from acp.workflows.nmr import run_nmr_analysis

    nitrogen = _structure(["C", "H", "H", "H", "N"])
    ensemble = StructureEnsemble(
        records=[
            StructureRecord(
                structure=nitrogen, energy_hartree=-1.0, free_energy_hartree=-1.0, weight=1.0
            )
        ]
    )
    with (
        patch("acp.workflows.nmr.StructureReader") as reader_cls,
        patch("acp.workflows.nmr.run_nmr_shielding") as mock_shield,
    ):
        reader = MagicMock()
        reader.read.return_value = nitrogen
        reader_cls.return_value = reader
        result = run_nmr_analysis(
            input_sources=["CN"],
            spectrum=_SPECTRUM,
            output_dir=str(tmp_path),
            nuclei=["15N"],
            prebuilt_ensembles=[ensemble],
            skip_conformers=False,
            error_model="placeholder-student-t",
        )

    assert result.status == "failed"
    assert "missing TMS reference" in (result.error or "")
    assert "15N" in (result.error or "")
    mock_shield.assert_not_called()
    assert "report_json" not in result.metadata


def test_workflow_surfaces_protocol_block(tmp_path: Path) -> None:
    """Stage 8: protocol_id + provenance.protocol carry the spec verdict; a
    prebuilt ensemble (geometry provenance unknown) is never upgraded."""
    from acp.workflows.nmr import run_nmr_analysis

    structure = _structure(["C", "H", "H", "H", "H"])
    ensemble = StructureEnsemble(
        records=[
            StructureRecord(
                structure=structure, energy_hartree=-1.0, free_energy_hartree=-1.0, weight=1.0
            )
        ]
    )
    with (
        patch("acp.workflows.nmr.StructureReader") as reader_cls,
        patch("acp.workflows.nmr.run_nmr_shielding", return_value=_shielding_result(_SH)),
    ):
        reader = MagicMock()
        reader.read.return_value = structure
        reader_cls.return_value = reader
        result = run_nmr_analysis(
            input_sources=["CCO"],
            spectrum=_SPECTRUM,
            output_dir=str(tmp_path),
            prebuilt_ensembles=[ensemble],
            skip_conformers=False,
            error_model="placeholder-student-t",
        )

    assert result.status == "completed", result.error
    report = json.loads(Path(result.metadata["report_json"]).read_text(encoding="utf-8"))
    block = report["provenance"]["protocol"]
    assert block is not None
    assert report["protocol_id"] == block["fingerprint"]
    assert report["config"]["protocol_fingerprint"] == block["fingerprint"]
    assert block["calibration_status"] == "unvalidated_protocol"
    assert block["mode"] == "exploratory"
    assert "geometry_not_optimized" in block["issues"]
    assert len(block["candidates"]) == 1
    recorded = dict(block["candidates"][0])
    fp = recorded.pop("fingerprint")
    assert NmrProtocolSpec.from_dict(recorded).fingerprint() == fp

    summary = json.loads(
        (Path(result.metadata["report_json"]).parent / "nmr_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["protocol"]["fingerprint"] == block["fingerprint"]


def test_workflow_censo_default_generation_records_optimized_geometry(tmp_path: Path) -> None:
    """Generation actually ran with a preset whose optimization part executed
    → the protocol records it and may reach acp_calibrated."""
    from acp.workflows.nmr import run_nmr_analysis

    structure = _structure(["C", "H", "H", "H", "H"])
    ensemble = StructureEnsemble(
        records=[
            StructureRecord(
                structure=structure, energy_hartree=-1.0, free_energy_hartree=-1.0, weight=1.0
            )
        ]
    )
    with (
        patch("acp.workflows.nmr.StructureReader") as reader_cls,
        patch("acp.workflows.nmr._run_conformer_generation", return_value=ensemble),
        patch("acp.workflows.nmr.run_nmr_shielding", return_value=_shielding_result(_SH)),
    ):
        reader = MagicMock()
        reader.read.return_value = structure
        reader_cls.return_value = reader
        result = run_nmr_analysis(
            input_sources=["CCO"],
            spectrum=_SPECTRUM,
            output_dir=str(tmp_path),
            conformer_preset="censo-default",
        )

    assert result.status == "completed", result.error
    report = json.loads(Path(result.metadata["report_json"]).read_text(encoding="utf-8"))
    block = report["provenance"]["protocol"]
    assert block["mode"] == "acp_calibrated", block
    assert block["calibration_status"] == "validated"
    assert block["issues"] == []
    candidate = block["candidates"][0]
    assert candidate["sampling"]["conformer_preset"] == "censo-default"
    assert candidate["sampling"]["censo_executed"] is True
    assert "optimization" in candidate["sampling"]["parts"]
    assert candidate["geometry"]["optimization_executed"] is True
    assert candidate["geometry"]["optimization_level"] == "r2scan-3c"
    assert candidate["reference"]["tms_source"] == "exact"
    assert candidate["reference"]["effective_solvent"] == "chloroform"
    assert candidate["statistical_model"]["error_model"] == "goodman-legacy"
    assert candidate["population_energy"]["energy_window_kcal"] == 3.0
    assert candidate["shielding"]["solvent_model"] == "cpcm"
