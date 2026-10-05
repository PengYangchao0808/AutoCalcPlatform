"""Reference-validation mode: pinned upstream settings vs ACP migration (todo 31).

Contract under test (``acp.nmr.reference_validation`` + the protocol gate in
``acp.nmr.protocol`` / ``acp.nmr.error_model``):

* pinned upstream opt/SP/NMR settings record VALUES + explicit citations
  (DevDoc §8.0 / asset NOTICE) — no value without a source; the conflicting
  older ``6-31G(d)``/``6-31G**`` notes are not pinned;
* side-by-side comparison math is exact: per-item
  ``difference = migrated - reference`` and aggregate n/mean/MAE/RMSE/max
  are computed from the raw floats — never display-rounded or approximated;
* a missing/empty reference returns a typed ``unavailable`` outcome with a
  reason; migrated or placeholder values never stand in for references;
* the protocol gate: ``reference_data_present`` is set True only when a real
  dataset is attached; requested-but-absent stays ``exploratory`` +
  ``unvalidated_protocol`` + ``missing_reference``;
* ``asset_hashes()`` records sha256 for the NOTICE-listed assets and
  degrades to ``None`` for missing files;
* the reproducible script emits the side-by-side table as JSON.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from acp.nmr.protocol import (
    GeometrySegment,
    NmrProtocolSpec,
    PopulationEnergySegment,
    ReferenceSegment,
    SamplingSegment,
    ShieldingSegment,
    StatisticalModelSegment,
    build_protocol_spec,
)
from acp.nmr.reference_validation import (
    ASSET_FILES,
    PinnedSetting,
    PinnedUpstreamSettings,
    ReferenceDataset,
    ReferenceRecord,
    apply_reference_validation,
    assess_reference_dataset,
    asset_hashes,
    attach_reference_segment,
    compare_reference_vs_migration,
    pinned_goodman_upstream,
)

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "nmr_reference_validation.py"
MODELS_DIR = ROOT / "src" / "acp" / "nmr" / "models"

#: sha256 values recorded in ``src/acp/nmr/models/NOTICE.md`` (Source revision).
NOTICE_HASHES = {
    "atomic_reps.gz": "bb8f798c1dd898811801a9fdd9f4bf037e434968f66e050acf3fb616a7652a65",
    "frag_reps.gz": "e5daae0f7b525632beed6cbd9b972b0be466f33e28c5cdb7efd1f1caaafb6c64",
}

#: Fixed synthetic pin — values are clearly labelled synthetic test inputs.
_MIGRATED = {"C1": 48.5, "H1": 3.35, "H2": 3.33, "H4": 3.62}


def _dataset() -> ReferenceDataset:
    return ReferenceDataset(
        dataset_id="synthetic-test-pin",
        source="synthetic fixture — NOT a literature value",
        structure_identity="methanol (CH3OH), fixed atom order C1/H1-H4",
        units="ppm",
        value_kind="shift",
        records=(
            ReferenceRecord(item_id="C1", nucleus="13C", value=49.0, source="synthetic row 1"),
            ReferenceRecord(item_id="H1", nucleus="1H", value=3.31, source="synthetic row 2"),
            ReferenceRecord(item_id="H2", nucleus="1H", value=3.31, source="synthetic row 3"),
            ReferenceRecord(item_id="H3", nucleus="1H", value=3.31, source="synthetic row 4"),
            ReferenceRecord(item_id="H4", nucleus="1H", value=3.70, source="synthetic row 5"),
        ),
    )


def _empty_dataset() -> ReferenceDataset:
    return ReferenceDataset(
        dataset_id="empty-pin",
        source="synthetic fixture — NOT a literature value",
        structure_identity="none",
        records=(),
    )


# ---------------------------------------------------------------------------
# Comparison math — fixed inputs → exact differences
# ---------------------------------------------------------------------------


def test_comparison_rows_and_stats_are_exact() -> None:
    result = compare_reference_vs_migration(_dataset(), _MIGRATED)

    assert result.status == "computed"
    assert result.reason is None
    assert result.dataset_id == "synthetic-test-pin"
    assert result.units == "ppm"
    assert result.value_kind == "shift"
    assert result.n_reference == 5
    assert result.n_matched == 4
    assert result.n_missing_migrated == 1
    assert result.missing_item_ids == ("H3",)
    assert result.extra_migrated_item_ids == ()

    assert [row.item_id for row in result.rows] == ["C1", "H1", "H2", "H3", "H4"]
    row = {r.item_id: r for r in result.rows}
    assert row["C1"].nucleus == "13C"
    assert row["C1"].reference_value == 49.0
    assert row["C1"].migrated_value == 48.5
    # difference = migrated - reference, computed from the raw floats
    assert row["C1"].difference == pytest.approx(-0.5, abs=1e-15)
    assert row["H1"].difference == pytest.approx(0.04, abs=1e-15)
    assert row["H2"].difference == pytest.approx(0.02, abs=1e-15)
    assert row["H4"].difference == pytest.approx(-0.08, abs=1e-15)
    assert row["H3"].migrated_value is None
    assert row["H3"].difference is None

    # aggregates over the four matched pairs only
    assert result.mean_difference == pytest.approx(-0.13, abs=1e-12)
    assert result.mae == pytest.approx(0.16, abs=1e-12)
    assert result.max_abs_difference == pytest.approx(0.5, abs=1e-12)
    assert result.rmse == pytest.approx(math.sqrt(0.2584 / 4), abs=1e-12)


def test_migrated_items_without_reference_are_recorded_not_compared() -> None:
    result = compare_reference_vs_migration(_dataset(), {"C1": 48.5, "X9": 1.0})
    assert result.status == "computed"
    assert result.n_matched == 1
    assert result.n_missing_migrated == 4
    assert result.extra_migrated_item_ids == ("X9",)
    assert all(row.item_id != "X9" for row in result.rows)
    assert result.mean_difference == pytest.approx(-0.5, abs=1e-15)


def test_no_migrated_values_leaves_computed_rows_with_none_differences() -> None:
    result = compare_reference_vs_migration(_dataset(), None)
    assert result.status == "computed"
    assert result.n_reference == 5
    assert result.n_matched == 0
    assert result.n_missing_migrated == 5
    assert all(row.difference is None for row in result.rows)
    assert result.mean_difference is None
    assert result.mae is None
    assert result.rmse is None
    assert result.max_abs_difference is None


def test_non_finite_or_bool_migrated_values_are_rejected_explicitly() -> None:
    with pytest.raises(ValueError, match="not finite"):
        compare_reference_vs_migration(_dataset(), {"C1": float("nan")})
    with pytest.raises(ValueError, match="not finite"):
        compare_reference_vs_migration(_dataset(), {"C1": float("inf")})
    with pytest.raises(ValueError, match="finite number"):
        compare_reference_vs_migration(_dataset(), {"C1": True})


# ---------------------------------------------------------------------------
# Absence handling — typed unavailable, never a placeholder
# ---------------------------------------------------------------------------


def test_missing_reference_is_typed_unavailable_and_never_placeholder() -> None:
    result = compare_reference_vs_migration(None, {"C1": 48.5, "H1": 3.35})
    assert result.status == "unavailable"
    assert result.reason == "missing_reference"
    assert result.rows == ()
    assert result.n_matched == 0
    assert result.mae is None
    assert result.rmse is None
    assert result.max_abs_difference is None
    payload = json.dumps(result.as_dict())
    # migrated values never stand in for the missing reference
    assert "C1" not in payload
    assert "48.5" not in payload


def test_empty_reference_dataset_is_typed_unavailable() -> None:
    result = compare_reference_vs_migration(_empty_dataset(), {"C1": 1.0})
    assert result.status == "unavailable"
    assert result.reason == "empty_reference"
    assert result.rows == ()

    availability = assess_reference_dataset(_empty_dataset())
    assert availability.available is False
    assert availability.reason == "empty_reference"
    assert availability.n_records == 0


def test_assess_reference_dataset_matrix() -> None:
    ok = assess_reference_dataset(_dataset())
    assert ok.available is True
    assert ok.status == "available"
    assert ok.reason is None
    assert ok.n_records == 5
    assert ok.dataset_id == "synthetic-test-pin"

    missing = assess_reference_dataset(None)
    assert missing.available is False
    assert missing.status == "unavailable"
    assert missing.reason == "missing_reference"
    assert missing.dataset_id is None
    assert missing.n_records == 0


def test_availability_and_comparison_require_a_reason_when_unavailable() -> None:
    from acp.nmr.reference_validation import ReferenceAvailability

    with pytest.raises(ValueError, match="reason"):
        ReferenceAvailability(status="unavailable", reason=None, dataset_id=None, n_records=0)
    with pytest.raises(ValueError, match="reason"):
        ReferenceAvailability(status="available", reason="stale", dataset_id="d", n_records=1)


# ---------------------------------------------------------------------------
# JSON safety / round-trip
# ---------------------------------------------------------------------------


def test_comparison_and_dataset_as_dict_are_json_safe_with_nulls() -> None:
    result = compare_reference_vs_migration(_dataset(), {"C1": 48.5})
    roundtrip = json.loads(json.dumps(result.as_dict()))
    assert roundtrip["status"] == "computed"
    assert roundtrip["n_missing_migrated"] == 4
    rows = {row["item_id"]: row for row in roundtrip["rows"]}
    assert rows["H1"]["migrated_value"] is None
    assert rows["H1"]["difference"] is None
    assert roundtrip["dataset_fingerprint"].startswith("v2:")

    rebuilt = ReferenceDataset.from_dict(json.loads(json.dumps(_dataset().as_dict())))
    assert rebuilt == _dataset()
    assert rebuilt.fingerprint() == _dataset().fingerprint()


# ---------------------------------------------------------------------------
# Pinned upstream settings — value + citation for every pin
# ---------------------------------------------------------------------------


def test_pinned_upstream_settings_values_match_dev_doc_section_8_0() -> None:
    pinned = pinned_goodman_upstream()
    expected = {
        "nmr_method": "mPW1PW91",
        "nmr_basis": "6-311G(d)",
        "opt_method": "B3LYP",
        "opt_basis": "6-31G(d,p)",
        "energy_method": "M062X",
        "energy_basis": "def2-TZVP",
    }
    for key, value in expected.items():
        setting = pinned.get(key)
        assert setting.value == value, key
        assert "DevDoc" in setting.source and "§8.0" in setting.source, key

    # conflicting older description is not pinned as the opt basis
    assert pinned.get("opt_basis").value != "6-31G(d)"
    assert pinned.get("opt_basis").value != "6-31G**"


def test_every_pinned_setting_carries_a_citation() -> None:
    pinned = pinned_goodman_upstream()
    assert pinned.settings, "pinned settings must not be empty"
    for setting in pinned.settings:
        assert setting.source.strip(), setting.key
        assert ("DevDoc" in setting.source) or ("NOTICE" in setting.source), setting.key


def test_pinned_settings_tie_to_notice_and_tms_asset() -> None:
    pinned = pinned_goodman_upstream()
    revision = pinned.get("dp5_source_revision")
    assert revision.value == "b6cf559007a5d13fe79654f37daf945ee1661a23"
    assert "NOTICE" in revision.source
    assert pinned.get("dp4_sigma_13c").value == 2.269372270818724
    assert pinned.get("dp4_sigma_1h").value == 0.18731058105269952
    assert pinned.get("tms_reference_13c_chloroform").value == 188.452125
    assert pinned.get("tms_reference_1h_chloroform").value == 32.1243166667
    assert pinned.get("tms_reference_13c_gas").value == 188.029225
    assert pinned.get("tms_reference_1h_gas").value == 32.1352666667
    giao = pinned.get("giao")
    assert "GIAO" in str(giao.value)
    assert "DevDoc" in giao.source


def test_pinned_settings_validation_and_json_safety() -> None:
    with pytest.raises(ValueError, match="source"):
        PinnedSetting(key="nmr_method", value="mPW1PW91", source="   ")
    with pytest.raises(ValueError, match="duplicate"):
        PinnedUpstreamSettings(
            settings=(
                PinnedSetting(key="a", value=1, source="DevDoc §8.0"),
                PinnedSetting(key="a", value=2, source="DevDoc §8.0"),
            )
        )
    payload = json.loads(json.dumps(pinned_goodman_upstream().as_dict()))
    assert payload["nmr_method"]["value"] == "mPW1PW91"
    assert payload["nmr_method"]["source"]


# ---------------------------------------------------------------------------
# Protocol gate — reference_validation requires a real dataset
# ---------------------------------------------------------------------------


def _spec(reference: ReferenceSegment | None = None) -> NmrProtocolSpec:
    return build_protocol_spec(
        SamplingSegment(
            conformer_preset="censo-default",
            crest_executed=True,
            censo_executed=True,
            parts=("prescreening", "screening", "optimization", "refinement"),
        ),
        GeometrySegment(optimization_executed=True, optimization_level="r2scan-3c"),
        PopulationEnergySegment(energy_window_kcal=3.0, boltzmann_temp=298.15),
        ShieldingSegment(nmr_method="mPW1PW91", nmr_basis="6-311G(d)", solvent_model="cpcm"),
        reference
        if reference is not None
        else ReferenceSegment(
            tms_source="exact",
            effective_solvent="chloroform",
            tms_shieldings={"1H": 32.1243166667, "13C": 188.452125},
        ),
        StatisticalModelSegment(
            error_model="goodman-legacy",
            dp5_model_id="goodman-dp5",
            dp5_mode="fallback",
            dp5_model_present=True,
        ),
    )


def test_calibrated_spec_records_no_reference_validation_claim() -> None:
    spec = _spec()
    assert spec.mode == "acp_calibrated"
    assert spec.calibration_status == "validated"
    assert spec.reference.reference_data_present is False
    assert spec.reference.reference_validation_requested is False


def test_requested_but_absent_reference_cannot_claim_reference_validation() -> None:
    spec = apply_reference_validation(_spec(), None)
    assert spec.reference.reference_validation_requested is True
    assert spec.reference.reference_data_present is False
    assert spec.mode == "exploratory"
    assert spec.calibration_status == "unvalidated_protocol"
    assert "missing_reference" in spec.issues
    assert "acp_calibrated" not in {spec.mode}


def test_empty_dataset_request_also_stays_exploratory() -> None:
    spec = apply_reference_validation(_spec(), _empty_dataset())
    assert spec.reference.reference_validation_requested is True
    assert spec.reference.reference_data_present is False
    assert spec.mode == "exploratory"
    assert spec.calibration_status == "unvalidated_protocol"
    assert "missing_reference" in spec.issues


def test_attached_reference_dataset_enters_reference_validation_mode() -> None:
    spec = apply_reference_validation(_spec(), _dataset())
    assert spec.reference.reference_data_present is True
    assert spec.reference.reference_validation_requested is True
    assert spec.mode == "reference_validation"
    assert spec.issues == ()


def test_attach_reference_segment_only_flags_real_datasets() -> None:
    base = _spec().reference
    absent = attach_reference_segment(base, None)
    assert absent.reference_data_present is False
    assert absent.reference_validation_requested is True

    empty = attach_reference_segment(base, _empty_dataset())
    assert empty.reference_data_present is False

    real = attach_reference_segment(base, _dataset())
    assert real.reference_data_present is True


def test_reference_request_round_trips_and_changes_fingerprint() -> None:
    requested = apply_reference_validation(_spec(), None)
    rebuilt = NmrProtocolSpec.from_dict(requested.to_dict())
    assert rebuilt == requested
    assert rebuilt.fingerprint() == requested.fingerprint()
    assert requested.fingerprint() != _spec().fingerprint()

    validated = apply_reference_validation(_spec(), _dataset())
    rebuilt_validated = NmrProtocolSpec.from_dict(validated.to_dict())
    assert rebuilt_validated == validated
    assert rebuilt_validated.reference.reference_data_present is True
    assert requested.fingerprint() != validated.fingerprint()


# ---------------------------------------------------------------------------
# Asset hashes — NOTICE assets, stable, graceful None for missing
# ---------------------------------------------------------------------------


def test_asset_hashes_match_notice_and_are_stable() -> None:
    hashes = asset_hashes()
    assert set(hashes) == set(ASSET_FILES)
    for name, value in hashes.items():
        assert value is None or (
            len(value) == 64 and all(char in "0123456789abcdef" for char in value)
        ), name
    assert asset_hashes() == hashes
    for name, expected in NOTICE_HASHES.items():
        if (MODELS_DIR / name).is_file():
            assert hashes[name] == expected, name
        else:
            assert hashes[name] is None, name


def test_asset_hashes_missing_files_degrade_to_none(tmp_path: Path) -> None:
    hashes = asset_hashes(tmp_path)
    assert set(hashes) == set(ASSET_FILES)
    assert all(value is None for value in hashes.values())


# ---------------------------------------------------------------------------
# Reproducible script — side-by-side table + differences as JSON
# ---------------------------------------------------------------------------


def _run_script(*args: str) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}"
    payload: dict[str, Any] = json.loads(proc.stdout)
    return payload


def test_script_default_fixture_prints_computable_side_by_side() -> None:
    payload = _run_script()
    assert payload["pinned_upstream"]["nmr_method"]["value"] == "mPW1PW91"
    assert payload["reference"]["dataset_id"] == "methanol-synthetic-pin-v1"
    comparison = payload["comparison"]
    assert comparison["status"] == "computed"
    assert comparison["n_matched"] == 4
    rows = {row["item_id"]: row for row in comparison["rows"]}
    assert rows["C1"]["difference"] == pytest.approx(-0.5, abs=1e-12)
    assert rows["H4"]["difference"] == pytest.approx(-0.08, abs=1e-12)
    assert rows["H3"]["difference"] is None
    assert payload["asset_hashes"]["tms_references.txt"] is not None


def test_script_accepts_reference_and_migrated_overrides(tmp_path: Path) -> None:
    reference_file = tmp_path / "reference.json"
    reference_file.write_text(json.dumps(_dataset().as_dict()), encoding="utf-8")
    migrated_file = tmp_path / "migrated.json"
    migrated_file.write_text(json.dumps({"C1": 49.1}), encoding="utf-8")
    payload = _run_script("--reference", str(reference_file), "--migrated", str(migrated_file))
    assert payload["reference"]["dataset_id"] == "synthetic-test-pin"
    comparison = payload["comparison"]
    assert comparison["n_matched"] == 1
    assert comparison["n_missing_migrated"] == 4
    rows = {row["item_id"]: row for row in comparison["rows"]}
    assert rows["C1"]["difference"] == pytest.approx(0.1, abs=1e-12)


def test_script_reports_unavailable_without_placeholder_rows(tmp_path: Path) -> None:
    reference_file = tmp_path / "empty.json"
    reference_file.write_text(json.dumps(_empty_dataset().as_dict()), encoding="utf-8")
    migrated_file = tmp_path / "migrated.json"
    migrated_file.write_text(json.dumps({"C1": 48.5}), encoding="utf-8")
    payload = _run_script("--reference", str(reference_file), "--migrated", str(migrated_file))
    comparison = payload["comparison"]
    assert comparison["status"] == "unavailable"
    assert comparison["reason"] == "empty_reference"
    assert comparison["rows"] == []
