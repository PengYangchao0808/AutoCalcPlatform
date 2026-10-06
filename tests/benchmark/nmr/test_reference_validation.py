"""Acceptance suite for the reference-validation comparison harness (todo 54 / gap §10.3 trial A).

TDD acceptance for ``tests/benchmark/nmr/reference_validation.py``:

* the comparison table juxtaposes the ORIGINAL reference data (todo-31
  ``reference_validation`` pins + the todo-36 layered FCHL golden) with the
  values the ACP migration actually produces at runtime (``lookup_tms_shieldings``
  / ``GoodmanErrorModel.SIGMA`` / ``GoodmanDP5Model.folded_errors`` and a fresh
  recomputation of :func:`compute_layer_values`) — never a comparison of
  profile/revision labels;
* every row carries reference value, ACP value, delta, tolerance and a
  three-state verdict (``match`` / ``mismatch`` / ``not_verified``);
* the asset-hash manifest covers every consumed asset (path, sha256, size,
  NOTICE pin + match); a present-but-changed asset is refused with the typed
  ``ReferenceAssetHashMismatchError`` (a relabelled asset cannot pass);
* missing reference assets/reference values degrade to ``not_verified`` with
  explicit reasons — never a formal calibration/validation status;
* the table is canonical JSON and byte-identical across processes (pinned
  clock, canonical todo-48 writer); the module CLI main() regenerates it;
* both todo-31 and todo-36 sources are represented — no single-source shortcut.

All fixtures are real committed assets; no QC binary is ever started.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from acp.nmr.reference_validation import ASSET_FILES
from tests.benchmark.nmr.reference_validation import (
    EXACT_TOLERANCE,
    REFERENCE_VALIDATION_SCHEMA,
    SOURCES,
    VERDICTS,
    ReferenceAssetError,
    ReferenceAssetHashMismatchError,
    ReferenceValidationError,
    build_reference_validation_table,
    collect_asset_manifest,
    compare_numeric_values,
    parse_notice_pins,
    write_reference_validation_table,
)
from tests.nmr_spectra_benchmark import canonical_json_bytes

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_DIR = REPO_ROOT / "tests" / "baseline" / "nmr"
MODELS_DIR = REPO_ROOT / "src" / "acp" / "nmr" / "models"
GOLDEN_PATH = BASELINE_DIR / "fchl_golden.json"
TOLERANCES_PATH = BASELINE_DIR / "fchl_golden_tolerances.json"

FIXED_NOW = "2026-10-06T00:00:00+00:00"

GOLDEN = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
TOLERANCES = json.loads(TOLERANCES_PATH.read_text(encoding="utf-8"))
GOLDEN_KEYS = set(GOLDEN["golden"])
TOLERANCE_KEYS = set(TOLERANCES["layers"])

#: sha256 pins recorded in ``src/acp/nmr/models/NOTICE.md``.
NOTICE_PINS = {
    "atomic_reps.gz": "bb8f798c1dd898811801a9fdd9f4bf037e434968f66e050acf3fb616a7652a65",
    "frag_reps.gz": "e5daae0f7b525632beed6cbd9b972b0be466f33e28c5cdb7efd1f1caaafb6c64",
}

#: todo-31 pinned-setting rows exposed by the comparison table.
T31_ROW_IDS = (
    "tms_reference_13c_chloroform",
    "tms_reference_1h_chloroform",
    "tms_reference_13c_gas",
    "tms_reference_1h_gas",
    "dp4_sigma_13c",
    "dp4_sigma_1h",
    "dp5_folded_scaled_residuals",
)


def _row(table: dict[str, Any], source: str, row_id: str) -> dict[str, Any]:
    for row in table["rows"]:
        if row["source"] == source and row["row_id"] == row_id:
            return row
    raise AssertionError(f"row ({source!r}, {row_id!r}) missing from the table")


def _manifest_entry(table: dict[str, Any], name: str) -> dict[str, Any]:
    for entry in table["asset_manifest"]:
        if entry["name"] == name:
            return entry
    raise AssertionError(f"asset manifest entry {name!r} missing")


@pytest.fixture(scope="module")
def committed_table() -> dict[str, Any]:
    """One real recomputation of the ACP side against the committed goldens."""
    return build_reference_validation_table(now=FIXED_NOW)


# ---------------------------------------------------------------------------
# table shape / three-state discipline
# ---------------------------------------------------------------------------


def test_table_schema_claim_and_sources(committed_table: dict[str, Any]) -> None:
    assert committed_table["schema"] == REFERENCE_VALIDATION_SCHEMA
    assert "NOT a calibration" in committed_table["claim"]
    assert "calibration_status" not in committed_table
    assert committed_table["acp_side"]["layers_source"] == "recomputed"
    assert committed_table["acp_side"]["layers_reason"] is None
    assert {row["source"] for row in committed_table["rows"]} == set(SOURCES)


def test_both_sources_are_represented_no_single_source_shortcut(
    committed_table: dict[str, Any],
) -> None:
    t31_ids = {
        row["row_id"]
        for row in committed_table["rows"]
        if row["source"] == "todo31_pinned_upstream"
    }
    assert set(T31_ROW_IDS) <= t31_ids
    layer_ids = {
        row["row_id"] for row in committed_table["rows"] if row["source"] == "todo36_fchl_golden"
    }
    assert GOLDEN_KEYS <= layer_ids
    assert TOLERANCE_KEYS <= layer_ids
    by_source = committed_table["summary"]["by_source"]
    assert set(by_source) == set(SOURCES)
    for source in SOURCES:
        assert by_source[source]["n_rows"] > 0


def test_every_row_has_the_frozen_field_set_and_valid_verdict(
    committed_table: dict[str, Any],
) -> None:
    expected_keys = {
        "source",
        "row_id",
        "kind",
        "units",
        "reference_value",
        "acp_value",
        "delta",
        "delta_kind",
        "tolerance",
        "n_entries",
        "n_exceeding",
        "worst_flat_index",
        "verdict",
        "reasons",
        "reference_provenance",
        "acp_provenance",
    }
    for row in committed_table["rows"]:
        assert set(row) == expected_keys, row["row_id"]
        assert row["verdict"] in VERDICTS
        assert row["reference_provenance"]
        assert row["acp_provenance"]
        if row["verdict"] == "not_verified":
            assert row["reasons"], row["row_id"]
        else:
            assert row["reasons"] == [], row["row_id"]


def test_table_is_json_safe_and_canonical(committed_table: dict[str, Any]) -> None:
    assert json.loads(canonical_json_bytes(committed_table)) == committed_table
    assert json.loads(json.dumps(committed_table)) == committed_table


# ---------------------------------------------------------------------------
# todo-36 layered golden — real recomputation vs frozen tolerances
# ---------------------------------------------------------------------------


def test_committed_goldens_match_the_recomputed_acp_layers(
    committed_table: dict[str, Any],
) -> None:
    for key in sorted(GOLDEN_KEYS):
        row = _row(committed_table, "todo36_fchl_golden", key)
        tolerance = TOLERANCES["layers"][key]
        assert row["verdict"] == "match", (key, row["delta"], row["reasons"])
        assert row["tolerance"] == {
            "rtol": float(tolerance["rtol"]),
            "atol": float(tolerance["atol"]),
        }
        assert row["delta"] is not None
        assert row["n_exceeding"] == 0


def test_kernel_qml_row_is_not_verified_from_the_frozen_tolerance_entry(
    committed_table: dict[str, Any],
) -> None:
    row = _row(committed_table, "todo36_fchl_golden", "kernel_qml")
    assert row["verdict"] == "not_verified"
    assert row["reference_value"] is None
    assert row["acp_value"] is None
    assert row["delta"] is None
    assert any(
        reason.startswith("tolerance_entry_status_NOT_VERIFIED") for reason in row["reasons"]
    )


def test_perturbed_acp_layer_yields_mismatch_and_locates_the_drift() -> None:
    layers = copy.deepcopy(GOLDEN["golden"])
    layers["probability_single"] = float(layers["probability_single"]) + 1.0
    vector = copy.deepcopy(layers["descriptor_c0"])
    vector[0][0] = float(vector[0][0]) + 1e-3
    layers["descriptor_c0"] = vector

    table = build_reference_validation_table(acp_layers=layers, recompute=False, now=FIXED_NOW)
    scalar_row = _row(table, "todo36_fchl_golden", "probability_single")
    assert scalar_row["verdict"] == "mismatch"
    assert scalar_row["n_exceeding"] == 1
    assert scalar_row["delta"] == pytest.approx(1.0, abs=1e-12)

    vector_row = _row(table, "todo36_fchl_golden", "descriptor_c0")
    assert vector_row["verdict"] == "mismatch"
    assert vector_row["n_exceeding"] == 1
    assert vector_row["delta"] == pytest.approx(1e-3, abs=1e-15)
    assert vector_row["worst_flat_index"] == 0

    untouched = _row(table, "todo36_fchl_golden", "kernel_numpy_self4")
    assert untouched["verdict"] == "match"


def test_supplied_layers_missing_one_key_are_not_verified() -> None:
    layers = dict(GOLDEN["golden"])
    layers.pop("kde_weighted_fixed")
    table = build_reference_validation_table(acp_layers=layers, recompute=False, now=FIXED_NOW)
    row = _row(table, "todo36_fchl_golden", "kde_weighted_fixed")
    assert row["verdict"] == "not_verified"
    assert any(reason.startswith("acp_value_missing") for reason in row["reasons"])


# ---------------------------------------------------------------------------
# todo-31 pinned upstream — value comparison against the migrated runtime path
# ---------------------------------------------------------------------------


def test_todo31_rows_match_the_migrated_runtime_values(committed_table: dict[str, Any]) -> None:
    expected = {
        "tms_reference_13c_chloroform": 188.452125,
        "tms_reference_1h_chloroform": 32.1243166667,
        "tms_reference_13c_gas": 188.029225,
        "tms_reference_1h_gas": 32.1352666667,
        "dp4_sigma_13c": 2.269372270818724,
        "dp4_sigma_1h": 0.18731058105269952,
        "dp5_folded_scaled_residuals": 106416.0,
    }
    for row_id, value in expected.items():
        row = _row(committed_table, "todo31_pinned_upstream", row_id)
        assert row["verdict"] == "match", (row_id, row["reasons"])
        assert row["reference_value"] == pytest.approx(value, abs=0.0)
        assert row["acp_value"] == pytest.approx(value, abs=0.0)
        assert row["delta"] == pytest.approx(0.0, abs=0.0)
        assert row["tolerance"] == EXACT_TOLERANCE
        assert row["kind"] == "scalar"
        assert row["delta_kind"] == "signed_difference"
        assert (
            "lookup_tms_shieldings" in row["acp_provenance"] or "Goodman" in row["acp_provenance"]
        )


# ---------------------------------------------------------------------------
# asset-hash manifest
# ---------------------------------------------------------------------------


def test_hash_manifest_is_complete_and_pins_match(committed_table: dict[str, Any]) -> None:
    manifest = committed_table["asset_manifest"]
    by_name = {entry["name"]: entry for entry in manifest}
    assert set(by_name) == set(ASSET_FILES) | {
        "fchl_golden.json",
        "fchl_golden_tolerances.json",
        "NOTICE.md",
    }
    for name, entry in by_name.items():
        assert entry["exists"] is True, name
        assert entry["role"], name
        assert entry["path"], name
        assert isinstance(entry["sha256"], str) and len(entry["sha256"]) == 64, name
        assert all(char in "0123456789abcdef" for char in entry["sha256"]), name
        resolved = REPO_ROOT / entry["path"]
        assert resolved.is_file(), name
        assert entry["size_bytes"] == resolved.stat().st_size, name
        assert entry["sha256"] == hashlib.sha256(resolved.read_bytes()).hexdigest(), name

    atomic = by_name["atomic_reps.gz"]
    assert atomic["notice_pin"] == NOTICE_PINS["atomic_reps.gz"]
    assert atomic["notice_pin_match"] is True
    assert atomic["golden_pin"] == GOLDEN["asset_sha256"]["atomic_reps.gz"]
    assert atomic["golden_pin_match"] is True
    assert atomic["pins_verified"] is True

    frag = by_name["frag_reps.gz"]
    assert frag["notice_pin"] == NOTICE_PINS["frag_reps.gz"]
    assert frag["notice_pin_match"] is True

    # tms_references.txt is consumed but not pinned anywhere: honest nulls.
    tms = by_name["tms_references.txt"]
    assert tms["notice_pin"] is None
    assert tms["notice_pin_match"] is None
    assert tms["golden_pin"] is None
    assert tms["pins_verified"] is None

    golden_entry = by_name["fchl_golden.json"]
    assert golden_entry["tolerance_pin"] == TOLERANCES["golden_sha256"]
    assert golden_entry["tolerance_pin_match"] is True
    assert golden_entry["pins_verified"] is True


def test_parse_notice_pins_reads_the_declared_hashes() -> None:
    assert parse_notice_pins() == NOTICE_PINS


def test_relabelled_but_changed_asset_is_refused(tmp_path: Path) -> None:
    """Same file name, same pinned revision label, different bytes → refusal."""
    models = tmp_path / "models"
    models.mkdir()
    (models / "atomic_reps.gz").write_bytes(b"same name and revision label, different bytes")
    with pytest.raises(ReferenceAssetHashMismatchError) as excinfo:
        collect_asset_manifest(models_dir=models)
    message = str(excinfo.value)
    assert "atomic_reps.gz" in message
    assert NOTICE_PINS["atomic_reps.gz"] in message


def test_tampered_golden_is_refused_by_the_tolerance_pin(tmp_path: Path) -> None:
    tampered = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    tampered["golden"]["probability_single"] = 0.123456789
    tampered_path = tmp_path / "fchl_golden.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ReferenceAssetHashMismatchError) as excinfo:
        collect_asset_manifest(golden_path=tampered_path, tolerances_path=TOLERANCES_PATH)
    assert "fchl_golden.json" in str(excinfo.value)


def test_malformed_golden_is_a_typed_asset_error(tmp_path: Path) -> None:
    broken = tmp_path / "fchl_golden.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ReferenceAssetError):
        collect_asset_manifest(golden_path=broken)


# ---------------------------------------------------------------------------
# reference-missing discipline (three-state, never a calibration status)
# ---------------------------------------------------------------------------


def test_missing_reference_assets_degrade_to_not_verified(tmp_path: Path) -> None:
    table = build_reference_validation_table(
        models_dir=tmp_path, acp_layers=None, recompute=False, now=FIXED_NOW
    )
    for row_id in ("tms_reference_13c_chloroform", "tms_reference_1h_gas"):
        row = _row(table, "todo31_pinned_upstream", row_id)
        assert row["verdict"] == "not_verified"
        assert any("reference_asset_missing" in reason for reason in row["reasons"])
    folded = _row(table, "todo31_pinned_upstream", "dp5_folded_scaled_residuals")
    assert folded["verdict"] == "not_verified"
    assert any("reference_asset_missing" in reason for reason in folded["reasons"])

    # Model-constant rows stay comparable: no asset was consumed.
    for row_id in ("dp4_sigma_13c", "dp4_sigma_1h"):
        assert _row(table, "todo31_pinned_upstream", row_id)["verdict"] == "match"

    for row in table["rows"]:
        if row["source"] == "todo36_fchl_golden" and row["row_id"] != "kernel_qml":
            assert row["verdict"] == "not_verified"
            assert any(reason.startswith("acp_values_not_supplied") for reason in row["reasons"]), (
                row["row_id"]
            )

    assert table["summary"]["n_assets_missing"] > 0
    assert table["summary"]["verdicts"]["mismatch"] == 0
    assert "NOT a calibration" in table["claim"]


def test_missing_golden_marks_layer_rows_not_verified(tmp_path: Path) -> None:
    table = build_reference_validation_table(
        golden_path=tmp_path / "fchl_golden.json",
        acp_layers=dict(GOLDEN["golden"]),
        recompute=False,
        now=FIXED_NOW,
    )
    layer_rows = [row for row in table["rows"] if row["source"] == "todo36_fchl_golden"]
    assert layer_rows, "tolerance keys must still enumerate rows"
    descriptor = _row(table, "todo36_fchl_golden", "descriptor_c0")
    assert descriptor["verdict"] == "not_verified"
    assert any(
        "reference_asset_missing: fchl_golden.json" in reason for reason in descriptor["reasons"]
    )
    for row in layer_rows:
        assert row["verdict"] == "not_verified"
        assert row["reasons"]


def test_missing_tolerances_marks_layer_rows_not_verified(tmp_path: Path) -> None:
    table = build_reference_validation_table(
        tolerances_path=tmp_path / "fchl_golden_tolerances.json",
        acp_layers=dict(GOLDEN["golden"]),
        recompute=False,
        now=FIXED_NOW,
    )
    descriptor = _row(table, "todo36_fchl_golden", "descriptor_c0")
    assert descriptor["verdict"] == "not_verified"
    assert any(
        "reference_asset_missing: fchl_golden_tolerances.json" in reason
        for reason in descriptor["reasons"]
    )


def test_missing_notice_degrades_pins_to_null_not_fabricated(tmp_path: Path) -> None:
    manifest = collect_asset_manifest(models_dir=MODELS_DIR, notice_path=tmp_path / "NOTICE.md")
    by_name = {entry["name"]: entry for entry in manifest}
    atomic = by_name["atomic_reps.gz"]
    assert atomic["notice_pin"] is None
    assert atomic["notice_pin_match"] is None
    # The golden's own pins are still verifiable without NOTICE.md.
    assert atomic["golden_pin"] == GOLDEN["asset_sha256"]["atomic_reps.gz"]
    assert atomic["golden_pin_match"] is True
    notice_entry = by_name["NOTICE.md"]
    assert notice_entry["exists"] is False
    assert notice_entry["sha256"] is None

    table = build_reference_validation_table(
        notice_path=tmp_path / "NOTICE.md",
        acp_layers=dict(GOLDEN["golden"]),
        recompute=False,
        now=FIXED_NOW,
    )
    assert table["provenance"]["notice"]["status"] == "not_verified"
    assert table["provenance"]["notice"]["pins"] == {}


# ---------------------------------------------------------------------------
# comparison helper unit rules
# ---------------------------------------------------------------------------


def test_compare_numeric_values_scalar_and_vector_rules() -> None:
    scalar = compare_numeric_values(10.0, 10.5, tolerance=EXACT_TOLERANCE)
    assert scalar["verdict"] == "mismatch"
    assert scalar["delta"] == pytest.approx(0.5, abs=1e-15)
    assert scalar["delta_kind"] == "signed_difference"
    assert scalar["n_exceeding"] == 1
    assert scalar["worst_flat_index"] == 0
    assert scalar["reasons"] == []

    within = compare_numeric_values([10.0, 20.0], [10.0, 20.0], tolerance=EXACT_TOLERANCE)
    assert within["verdict"] == "match"
    assert within["delta"] == pytest.approx(0.0, abs=0.0)
    assert within["n_entries"] == 2

    vector = compare_numeric_values(
        [1.0, 2.0, 3.0], [1.0, 2.5, 3.0], tolerance={"rtol": 0.0, "atol": 1e-9}
    )
    assert vector["verdict"] == "mismatch"
    assert vector["n_exceeding"] == 1
    assert vector["delta"] == pytest.approx(0.5, abs=1e-15)
    assert vector["worst_flat_index"] == 1
    assert vector["delta_kind"] == "max_abs_difference"

    non_finite = compare_numeric_values(1.0, float("nan"), tolerance=EXACT_TOLERANCE)
    assert non_finite["verdict"] == "not_verified"
    assert non_finite["reasons"] == ["non_finite_value"]
    assert non_finite["delta"] is None
    assert non_finite["n_exceeding"] is None

    with pytest.raises(ReferenceValidationError, match="length"):
        compare_numeric_values([1.0], [1.0, 2.0], tolerance=EXACT_TOLERANCE)
    with pytest.raises(ReferenceValidationError):
        compare_numeric_values(True, 1.0, tolerance=EXACT_TOLERANCE)


# ---------------------------------------------------------------------------
# persistence / CLI / determinism
# ---------------------------------------------------------------------------


def test_write_reference_validation_table_is_canonical_atomic(
    committed_table: dict[str, Any], tmp_path: Path
) -> None:
    destination = tmp_path / "reference_validation.json"
    path = write_reference_validation_table(committed_table, destination)
    assert path == destination
    assert destination.read_bytes() == canonical_json_bytes(committed_table)
    assert json.loads(destination.read_text(encoding="utf-8")) == committed_table


def test_cli_no_recompute_receipt(tmp_path: Path) -> None:
    destination = tmp_path / "table.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.benchmark.nmr.reference_validation",
            "--output",
            str(destination),
            "--now",
            FIXED_NOW,
            "--no-recompute",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt["output"] == str(destination)
    assert receipt["verdicts"]["mismatch"] == 0
    table = json.loads(destination.read_text(encoding="utf-8"))
    assert table["acp_side"]["layers_source"] == "unavailable"
    assert table["acp_side"]["layers_reason"] == "acp_values_not_supplied"
    layer_rows = [row for row in table["rows"] if row["source"] == "todo36_fchl_golden"]
    assert all(row["verdict"] == "not_verified" for row in layer_rows)


def test_cli_table_is_byte_identical_across_processes(
    committed_table: dict[str, Any], tmp_path: Path
) -> None:
    outputs: list[bytes] = []
    for hash_seed in ("1", "98765"):
        destination = tmp_path / f"table-{hash_seed}.json"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.benchmark.nmr.reference_validation",
                "--output",
                str(destination),
                "--now",
                FIXED_NOW,
            ],
            cwd=REPO_ROOT,
            env={
                **os.environ,
                "PYTHONPATH": str(REPO_ROOT / "src"),
                "PYTHONHASHSEED": hash_seed,
            },
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        outputs.append(destination.read_bytes())
        assert outputs[-1] == canonical_json_bytes(committed_table)
    assert outputs[0] == outputs[1]
