"""Tests for the extended science-product contracts (plan todo 12, C02-s2).

Covers the curve/scatter, vibration and derived product classes added on top
of the todo 11 skeleton: round-trip fidelity, explicit units, native mode
numbering (never renumbered), atom identity/alignment, source mapping and
versions, typed ``unavailable`` separation (never an empty success), explicit
failure on corrupt input and the self-describing registry.

QA anchors:
* mode numbering matches ``tests/fixtures/structure_viewer/orca_freq_modes_full.txt``
  parsed through the single cccp frequency parse;
* an input without a frequency section yields a typed ``unavailable`` product.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cccp.calculation import TaskInputError
from cccp.calculation.frequency_parse import parse_orca_frequency_text
from cccp.calculation.products import (
    PRODUCT_SCHEMA_VERSION,
    CurveScatterPayload,
    CurveSeries,
    DerivedPayload,
    FrequencyUnits,
    MissingReason,
    PointRecord,
    ProductIdentity,
    ProductKind,
    ProductParameter,
    ProductSchemaError,
    ProductState,
    ReferenceFrame,
    ScienceProduct,
    SeriesRef,
    SourceRef,
    VibrationMode,
    VibrationPayload,
    build_curve_scatter_product,
    build_derived_product,
    build_vibration_product,
    product_from_dict,
    product_spec,
    product_specs,
    product_to_dict,
)

FIXTURES = Path(__file__).parent / "fixtures" / "science_products"
ORCA_FREQ_FIXTURE = (
    Path(__file__).parent / "fixtures" / "structure_viewer" / "orca_freq_modes_full.txt"
)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _source(index: int = 0) -> SourceRef:
    return SourceRef(
        source_kind="orca_output",
        artifact="RESULT/trajectories/scan.json",
        native_index=index,
    )


# ── curve / scatter ─────────────────────────────────────────────────────


def test_curve_normal_round_trip_units_and_source():
    product = product_from_dict(_load("curve_scatter_normal.json"))
    assert product.state is ProductState.AVAILABLE
    assert product.complete is True
    assert product.identity.product_kind is ProductKind.CURVE_SCATTER

    payload = product.payload
    assert isinstance(payload, CurveScatterPayload)
    assert payload.reference_frame == ReferenceFrame()
    assert len(payload.series) == 1
    series = payload.series[0]
    assert series.ref.series_id == "curve_scatter:series:scan_energy"
    assert series.ref.x_unit == "angstrom-or-degree"
    assert series.ref.y_unit == "hartree"
    assert [point.point_id for point in series.points] == [
        "curve_scatter:series:scan_energy:point:0",
        "curve_scatter:series:scan_energy:point:1",
        "curve_scatter:series:scan_energy:point:2",
    ]
    assert series.points[0].source is not None
    assert series.points[0].source.native_index == 0

    assert product_from_dict(product_to_dict(product)) == product


def test_curve_partial_missing_point_reports_reason_not_zero_fill():
    product = product_from_dict(_load("curve_scatter_partial.json"))
    assert product.state is ProductState.AVAILABLE
    assert product.complete is False
    payload = product.payload
    assert isinstance(payload, CurveScatterPayload)
    series = payload.series[0]
    assert series.points[0].available is True
    bad = series.points[1]
    assert bad.missing_reason is MissingReason.MISSING_POINT_VALUES
    assert bad.available is False
    assert bad.x is None and bad.y is None
    assert series.is_complete() is False
    assert product_from_dict(product_to_dict(product)) == product


def test_curve_corrupt_series_id_raises_typed_error():
    doc = _load("curve_scatter_normal.json")
    del doc["payload"]["series"][0]["series_id"]
    with pytest.raises(ProductSchemaError) as excinfo:
        product_from_dict(doc)
    assert isinstance(excinfo.value, TaskInputError)


def test_curve_available_series_requires_explicit_units():
    with pytest.raises(ProductSchemaError):
        CurveSeries(
            ref=SeriesRef(series_id="curve_scatter:series:s", x_unit="", y_unit="hartree"),
            points=(PointRecord(point_id="p0", x=1.0, y=2.0),),
        )


def test_curve_builder_empty_series_is_typed_unavailable():
    product = build_curve_scatter_product(series=())
    assert product.state is ProductState.UNAVAILABLE
    assert product.missing_reason is MissingReason.SOURCE_MISSING
    assert product.payload is None


def test_curve_series_with_reason_must_not_carry_points():
    with pytest.raises(ProductSchemaError):
        CurveSeries(
            ref=SeriesRef(series_id="curve_scatter:series:s", x_unit="a", y_unit="b"),
            points=(PointRecord(point_id="p0", x=1.0, y=2.0),),
            missing_reason=MissingReason.MISSING_SERIES,
        )


# ── vibration ───────────────────────────────────────────────────────────


def test_vibration_normal_round_trip_native_mode_numbering():
    product = product_from_dict(_load("vibration_normal.json"))
    assert product.state is ProductState.AVAILABLE
    assert product.complete is True
    assert product.identity.product_kind is ProductKind.VIBRATION

    payload = product.payload
    assert isinstance(payload, VibrationPayload)
    assert payload.frequency_units is FrequencyUnits.CM1
    assert payload.displacement_units == "dimensionless_orca_normal_mode"
    assert payload.displacement_normalization == "mass_weighted_normalized_not_orthogonal"
    assert payload.atom_order == ("O:0", "H:0", "H:1")

    indices = [mode.mode_index for mode in payload.modes]
    assert indices == list(range(9))
    assert all(len(mode.displacements) == 3 for mode in payload.modes)
    assert payload.modes[6].frequency == pytest.approx(-797.72)
    assert payload.modes[6].imaginary is True
    assert payload.modes[7].frequency == pytest.approx(-791.36)
    assert payload.modes[8].frequency == pytest.approx(1411.55)
    assert payload.modes[8].imaginary is False
    assert payload.modes[6].ir_intensity == pytest.approx(66.542)

    assert product_from_dict(product_to_dict(product)) == product


def test_vibration_partial_reports_missing_frequency_and_displacements():
    product = product_from_dict(_load("vibration_partial.json"))
    assert product.state is ProductState.AVAILABLE
    assert product.complete is False
    payload = product.payload
    assert isinstance(payload, VibrationPayload)

    modes = {mode.mode_index: mode for mode in payload.modes}
    assert modes[6].available is True
    assert modes[7].missing_reason is MissingReason.MISSING_FREQUENCY
    assert modes[7].frequency is None
    assert modes[8].missing_reason is MissingReason.MISSING_DISPLACEMENTS
    assert modes[8].displacements == ()
    assert modes[8].frequency == pytest.approx(1411.55)
    assert len(payload.missing_modes()) == 2
    assert product_from_dict(product_to_dict(product)) == product


def test_vibration_unavailable_fixture_is_typed_not_empty_success():
    product = product_from_dict(_load("vibration_unavailable.json"))
    assert product.state is ProductState.UNAVAILABLE
    assert product.available is False
    assert product.payload is None
    assert product.missing_reason is MissingReason.MISSING_FREQUENCY


def test_vibration_builder_missing_frequency_section_is_unavailable():
    """QA failure path: an input without a frequency section is never empty success."""
    product = build_vibration_product(
        mode_frequencies={},
        mode_vectors={},
        atom_order=("O:0", "H:0", "H:1"),
    )
    assert product.state is ProductState.UNAVAILABLE
    assert product.payload is None
    assert product.missing_reason is MissingReason.MISSING_FREQUENCY


def test_vibration_builder_missing_atom_identity_is_unavailable():
    product = build_vibration_product(
        mode_frequencies={0: 100.0},
        mode_vectors={0: ((0.0, 0.0, 0.0),)},
        atom_order=(),
    )
    assert product.state is ProductState.UNAVAILABLE
    assert product.missing_reason is MissingReason.MISSING_ATOM_UID


def test_vibration_mode_numbering_matches_source_fixture():
    """QA: native mode numbering mirrors ``orca_freq_modes_full.txt``."""
    analysis = parse_orca_frequency_text(ORCA_FREQ_FIXTURE.read_text(encoding="utf-8"))
    product = build_vibration_product(
        mode_frequencies=analysis.mode_frequencies,
        mode_vectors=analysis.mode_vectors,
        atom_order=("O:0", "H:0", "H:1"),
        mode_ir_intensities=analysis.mode_ir_intensities,
    )
    assert product.state is ProductState.AVAILABLE
    payload = product.payload
    assert isinstance(payload, VibrationPayload)

    indices = [mode.mode_index for mode in payload.modes]
    assert indices == list(range(9))
    by_index = {mode.mode_index: mode for mode in payload.modes}
    for zero_mode in range(6):
        assert by_index[zero_mode].frequency == pytest.approx(0.0)
        assert by_index[zero_mode].imaginary is False
    assert by_index[6].frequency == pytest.approx(-797.72)
    assert by_index[6].imaginary is True
    assert by_index[7].frequency == pytest.approx(-791.36)
    assert by_index[8].frequency == pytest.approx(1411.55)
    assert by_index[8].imaginary is False
    assert all(len(mode.displacements) == 3 for mode in payload.modes)


def test_vibration_payload_rejects_out_of_order_modes():
    with pytest.raises(ProductSchemaError):
        VibrationPayload(
            frequency_units=FrequencyUnits.CM1,
            displacement_units="dimensionless_orca_normal_mode",
            displacement_normalization="mass_weighted_normalized_not_orthogonal",
            atom_order=("H:0",),
            modes=(
                VibrationMode(mode_index=1, frequency=2.0, displacements=((0.1, 0.2, 0.3),)),
                VibrationMode(mode_index=0, frequency=1.0, displacements=((0.1, 0.2, 0.3),)),
            ),
        )


def test_vibration_corrupt_mode_index_type_raises():
    doc = _load("vibration_normal.json")
    doc["payload"]["modes"][0]["mode_index"] = "zero"
    with pytest.raises(ProductSchemaError):
        product_from_dict(doc)


# ── derived ─────────────────────────────────────────────────────────────


def test_derived_normal_round_trip_sources_and_versions():
    product = product_from_dict(_load("derived_normal.json"))
    assert product.state is ProductState.AVAILABLE
    assert product.complete is True
    assert product.identity.product_kind is ProductKind.DERIVED

    payload = product.payload
    assert isinstance(payload, DerivedPayload)
    assert len(payload.sources) == 1
    assert payload.sources[0].source_kind == "product"
    assert payload.source_versions == ("1",)
    assert payload.parameters[0].name == "energy_unit"
    assert payload.parameters[0].value == "kcal_mol"
    assert payload.raw_io[0].role == "input"
    assert payload.raw_io[0].value == [-114.5, -114.42, -114.31]

    assert product_from_dict(product_to_dict(product)) == product


def test_derived_partial_missing_source_version():
    product = product_from_dict(_load("derived_partial.json"))
    assert product.state is ProductState.AVAILABLE
    assert product.complete is False
    payload = product.payload
    assert isinstance(payload, DerivedPayload)
    assert payload.is_complete() is False
    assert product_from_dict(product_to_dict(product)) == product


def test_derived_builder_empty_sources_is_typed_unavailable():
    product = build_derived_product(sources=())
    assert product.state is ProductState.UNAVAILABLE
    assert product.missing_reason is MissingReason.SOURCE_MISSING
    assert product.payload is None


def test_envelope_rejects_complete_derived_without_sources():
    payload = DerivedPayload()
    identity = ProductIdentity(product_kind=ProductKind.DERIVED)
    with pytest.raises(ProductSchemaError):
        ScienceProduct(
            identity=identity,
            state=ProductState.AVAILABLE,
            payload=payload,
            complete=True,
        )


# ── shared envelope / registry continuity ───────────────────────────────


def test_schema_version_is_still_one():
    assert PRODUCT_SCHEMA_VERSION == 1


def test_registry_covers_new_kinds_with_full_contract():
    specs = {spec.product_kind: spec for spec in product_specs()}
    assert set(specs) == set(ProductKind)
    for kind in (ProductKind.CURVE_SCATTER, ProductKind.VIBRATION, ProductKind.DERIVED):
        spec = product_spec(kind)
        assert spec.schema_version == PRODUCT_SCHEMA_VERSION
        assert spec.generating_function
        assert spec.generation_timing
        assert spec.stable_identity_rule
        assert spec.raw_input_digest_rule
        assert spec.old_version_conversion
        assert spec.missing_reasons
    assert product_spec(ProductKind.VIBRATION).generating_function.endswith(
        "build_vibration_product"
    )


def test_v0_document_rejected_for_extended_kinds():
    doc = {
        "version": 0,
        "product_kind": "vibration",
        "generator": "legacy.v0",
        "modes": [],
    }
    with pytest.raises(ProductSchemaError):
        product_from_dict(doc)


def test_extended_identity_excludes_platform_identity():
    product = product_from_dict(_load("vibration_normal.json"))
    serialised = json.dumps(product_to_dict(product)).lower()
    for forbidden in ("job_id", "account", "price"):
        assert forbidden not in serialised


def test_parameter_value_round_trips_verbatim():
    parameter = ProductParameter(name="dispersion", value="D4", unit="")
    assert parameter.to_dict()["value"] == "D4"
    assert ProductParameter.from_dict(parameter.to_dict()) == parameter
