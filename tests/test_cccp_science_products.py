"""Tests for the versioned science-product contracts (plan todo 11, C02-s1).

Covers the happy round-trip, the "empty"/typed-unavailable separation, the
no-fabrication rules for empty/partial payloads, explicit failure on corrupt
input, old-version (v0) conversion, the self-describing registry, the
identity quarantine (no platform identity) and cccp isolation (no ``acp``).
"""

from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path

import pytest

from cccp.calculation import TaskInputError
from cccp.calculation.products import (
    PRODUCT_SCHEMA_VERSION,
    CoordinateAtom,
    CoordinateUnits,
    FrameRecord,
    MissingReason,
    PointRecord,
    ProductIdentity,
    ProductKind,
    ProductSchemaError,
    ProductState,
    ScienceProduct,
    StructureTrajectoryPayload,
    build_structure_trajectory_product,
    product_from_dict,
    product_spec,
    product_specs,
    product_to_dict,
)

FIXTURES = Path(__file__).parent / "fixtures" / "science_products"
_PRODUCTS_DIR = Path(__file__).resolve().parents[1] / "src" / "cccp" / "calculation" / "products"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ── happy round-trip ────────────────────────────────────────────────────


def test_normal_round_trip_is_lossless():
    product = product_from_dict(_load("structure_trajectory_normal.json"))
    assert product.state is ProductState.AVAILABLE
    assert product.complete is True
    assert product.identity.product_kind is ProductKind.STRUCTURE_TRAJECTORY
    assert product.identity.schema_version == PRODUCT_SCHEMA_VERSION
    assert product.identity.generator.endswith("build_structure_trajectory_product")

    payload = product.payload
    assert isinstance(payload, StructureTrajectoryPayload)
    assert payload.coordinate_units is CoordinateUnits.ANGSTROM
    assert payload.topology_source == "orca_xyz"
    assert payload.atom_order == ("C:0", "H:0", "H:1", "O:0")
    # Frame order and block index are preserved verbatim.
    assert [f.frame_index for f in payload.frames] == [0, 1]
    assert [f.block_index for f in payload.frames] == [0, 0]
    assert payload.frames[0].source is not None
    assert payload.frames[0].source.native_index == 0

    # serialize -> deserialize equality (the core contract invariant).
    assert product_from_dict(product_to_dict(product)) == product


def test_empty_frames_is_valid_and_fabricates_nothing():
    product = product_from_dict(_load("structure_trajectory_empty_frames.json"))
    assert product.state is ProductState.AVAILABLE
    assert product.complete is True
    assert product.payload is not None
    assert product.payload.frames == ()
    assert product.payload.is_complete() is True
    assert product_from_dict(product_to_dict(product)) == product


# ── typed unavailable separated from payloads ───────────────────────────


def test_empty_payload_is_typed_unavailable_not_a_zero_value():
    product = product_from_dict(_load("structure_trajectory_empty.json"))
    assert product.state is ProductState.UNAVAILABLE
    assert product.available is False
    assert product.complete is False
    assert product.payload is None
    assert product.missing_reason is MissingReason.SOURCE_MISSING


def test_envelope_rejects_unavailable_without_reason():
    identity = ProductIdentity(product_kind=ProductKind.STRUCTURE_TRAJECTORY)
    with pytest.raises(ProductSchemaError):
        ScienceProduct(identity=identity, state=ProductState.UNAVAILABLE)


def test_envelope_rejects_available_without_payload():
    identity = ProductIdentity(product_kind=ProductKind.STRUCTURE_TRAJECTORY)
    with pytest.raises(ProductSchemaError):
        ScienceProduct(identity=identity, state=ProductState.AVAILABLE)


def test_envelope_rejects_failed_without_errors():
    identity = ProductIdentity(product_kind=ProductKind.STRUCTURE_TRAJECTORY)
    with pytest.raises(ProductSchemaError):
        ScienceProduct(
            identity=identity,
            state=ProductState.FAILED,
            missing_reason=MissingReason.PARSE_FAILURE,
        )


# ── partial: missing atom_uid -> explicit reason, never zero-fill (QA) ──


def test_partial_missing_atom_uid_reports_reason_not_zero_fill():
    """QA negative assertion for C02-s1.

    A frame whose first atom omits ``atom_uid`` must not be zero-filled: the
    frame is retained with an explicit ``missing_atom_uid`` reason and an
    empty atom tuple, and the product is marked ``complete=False``.
    """
    product = product_from_dict(_load("structure_trajectory_partial.json"))
    assert product.state is ProductState.AVAILABLE
    assert product.complete is False
    payload = product.payload
    assert isinstance(payload, StructureTrajectoryPayload)
    assert len(payload.frames) == 3

    bad = payload.frames[1]
    assert bad.missing_reason is MissingReason.MISSING_ATOM_UID
    assert bad.available is False
    # Negative assertion: nothing fabricated.
    assert bad.atoms == ()
    assert not any(atom.atom_uid in ("", "C:0") and atom.element == "C" for atom in bad.atoms)
    assert [f.frame_index for f in payload.missing_frames()] == [1]
    assert payload.is_complete() is False

    # A product with a missing sub-item must round-trip the same way.
    assert product_from_dict(product_to_dict(product)) == product


def test_envelope_rejects_available_but_incomplete_without_marking():
    payload = StructureTrajectoryPayload(
        coordinate_units=CoordinateUnits.ANGSTROM,
        topology_source="orca_xyz",
        frames=(
            FrameRecord(
                frame_index=0,
                coordinate_units=CoordinateUnits.ANGSTROM,
                topology_source="orca_xyz",
                missing_reason=MissingReason.MISSING_ATOM_UID,
            ),
        ),
    )
    identity = ProductIdentity(product_kind=ProductKind.STRUCTURE_TRAJECTORY)
    with pytest.raises(ProductSchemaError):
        ScienceProduct(
            identity=identity,
            state=ProductState.AVAILABLE,
            payload=payload,
            complete=True,
        )


def test_frame_with_reason_must_not_carry_atoms():
    with pytest.raises(ProductSchemaError):
        FrameRecord(
            frame_index=0,
            coordinate_units=CoordinateUnits.ANGSTROM,
            topology_source="orca_xyz",
            frame_id="f0",
            atoms=(CoordinateAtom("C:0", "C", (0.0, 0.0, 0.0)),),
            missing_reason=MissingReason.MISSING_ATOM_UID,
        )


def test_point_record_no_zero_fill():
    point = PointRecord(point_id="p0", missing_reason=MissingReason.MISSING_COORDINATES)
    assert point.available is False
    assert point.x is None and point.y is None
    with pytest.raises(ProductSchemaError):
        PointRecord(point_id="p1", x=0.0, y=0.0, missing_reason=MissingReason.PARSE_FAILURE)


# ── corrupt input fails explicitly with a typed error ───────────────────


def test_corrupt_payload_raises_typed_error():
    with pytest.raises(ProductSchemaError) as excinfo:
        product_from_dict(_load("structure_trajectory_corrupt.json"))
    # ProductSchemaError is a TaskInputError, so generic handlers still catch it.
    assert isinstance(excinfo.value, TaskInputError)


def test_unknown_product_kind_is_corrupt():
    doc = _load("structure_trajectory_normal.json")
    doc["product_kind"] = "does_not_exist"
    with pytest.raises(ProductSchemaError):
        product_from_dict(doc)


def test_non_finite_coordinate_is_corrupt():
    doc = _load("structure_trajectory_normal.json")
    doc["payload"]["frames"][0]["atoms"][0]["coordinate"][0] = "not-a-number"
    with pytest.raises(ProductSchemaError):
        product_from_dict(doc)


def test_unsupported_newer_version_is_rejected():
    doc = _load("structure_trajectory_normal.json")
    doc["schema_version"] = PRODUCT_SCHEMA_VERSION + 1
    with pytest.raises(ProductSchemaError):
        product_from_dict(doc)


# ── old version conversion ──────────────────────────────────────────────


def test_old_version_is_migrated_to_current():
    product = product_from_dict(_load("structure_trajectory_old.json"))
    assert product.identity.schema_version == PRODUCT_SCHEMA_VERSION
    assert product.state is ProductState.AVAILABLE
    assert product.migrations  # conversions are recorded, never silent
    payload = product.payload
    assert isinstance(payload, StructureTrajectoryPayload)
    assert len(payload.frames) == 2
    assert payload.coordinate_units is CoordinateUnits.ANGSTROM
    assert payload.topology_source == "legacy_v0"
    # atom_uid derived with the documented element:rank scheme.
    assert payload.atom_order == ("C:0", "H:0", "H:1", "O:0")
    assert [f.frame_index for f in payload.frames] == [0, 1]
    # Migrated products round-trip through the current reader unchanged.
    assert product_from_dict(product_to_dict(product)) == product


# ── identity quarantine (no platform identity) ──────────────────────────


def test_identity_excludes_platform_identity():
    doc = _load("structure_trajectory_normal.json")
    keys = set(doc["identity"].keys())
    for forbidden in ("job_id", "account", "price", "project", "owner", "user"):
        assert forbidden not in keys
    product = product_from_dict(doc)
    serialised = json.dumps(product_to_dict(product)).lower()
    for forbidden in ("job_id", "account", "price"):
        assert forbidden not in serialised


# ── builder + registry ──────────────────────────────────────────────────


def test_builder_empty_frames_yields_typed_unavailable():
    product = build_structure_trajectory_product(frames=(), topology_source="orca_xyz")
    assert product.state is ProductState.UNAVAILABLE
    assert product.missing_reason is MissingReason.SOURCE_MISSING
    assert product.payload is None


def test_builder_round_trips_with_digest():
    frame = FrameRecord(
        frame_index=0,
        coordinate_units=CoordinateUnits.ANGSTROM,
        topology_source="orca_xyz",
        frame_id="f0",
        atoms=(
            CoordinateAtom("C:0", "C", (0.0, 0.0, 0.0)),
            CoordinateAtom("H:0", "H", (1.09, 0.0, 0.0)),
        ),
    )
    product = build_structure_trajectory_product(
        frames=(frame,), topology_source="orca_xyz", raw_input={"source": "unit-test"}
    )
    assert product.identity.input_digest.startswith("sha256:")
    assert product_from_dict(product_to_dict(product)) == product


def test_registry_covers_every_kind_and_paths_resolve():
    specs = product_specs()
    assert {spec.product_kind for spec in specs} == set(ProductKind)
    for spec in specs:
        assert spec.schema_version == PRODUCT_SCHEMA_VERSION
        for attr in ("generating_function", "reader", "writer", "old_version_conversion"):
            assert getattr(spec, attr)
        assert spec.missing_reasons
        for dotted in (spec.generating_function, spec.reader, spec.writer):
            module_name, _, attr_name = dotted.rpartition(".")
            module = importlib.import_module(module_name)
            assert callable(getattr(module, attr_name))
    assert product_spec(ProductKind.STRUCTURE_TRAJECTORY).generating_function.endswith(
        "build_structure_trajectory_product"
    )


# ── isolation: products never import acp ────────────────────────────────


def test_products_package_has_no_acp_import():
    assert _PRODUCTS_DIR.is_dir()
    for path in sorted(_PRODUCTS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("acp"), f"{path}: import {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.startswith("acp"), f"{path}: from {module}"
