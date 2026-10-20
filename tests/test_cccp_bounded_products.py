"""Tests for the C06-s2 bounded science-product layer (plan todo 32).

Cover the three bounded operations (lossless chunking, display subsampling,
multi-resolution) and their mandated assertions:

* every block is bounded and, for chunking, the full sequence round-trips
  exactly (values + order + stable ids);
* subsampling only proves selected-point -> original-point mapping plus key
  point retention, and explicitly labels omitted key points;
* the derived identity is stable for identical input/parameters and distinct
  across range/resolution/parameters, and carries the original input digest
  (never platform job identity);
* generation runs through the real production entry points and the manifest
  serialises over the single C02 reader/writer.

The synthetic series reuse the T31 (C06-s1) fixed-scale energy-sample formula
so the numbers are deterministic and match the baseline sample shape.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from cccp.analysis.bounded import (
    BOUNDED_IDENTITY_RULE_VERSION,
    BOUNDED_SCHEMA_VERSION,
    DISPLAY_SAMPLING_NOTE,
    GENERATOR_CHUNK,
    GENERATOR_MULTIRESOLUTION,
    GENERATOR_SUBSAMPLE,
    GENERATOR_TRAJECTORY_CHUNK,
    KEY_ENDPOINTS,
    KEY_GLOBAL_MAX,
    KEY_GLOBAL_MIN,
    KEY_LOCAL_MIN,
    LEGACY_BOUNDED_IDENTITY_RULE_VERSION,
    LEGACY_BOUNDED_SCHEMA_VERSION,
    BoundedProduct,
    BoundedSchemaError,
    BoundedSpec,
    bounded_identity,
    build_multiresolution_product,
    chunk_curve_product,
    chunk_trajectory_product,
    find_key_point_indices,
    native_index_of,
    reconstruct_curve_series,
    reconstruct_structure_frames,
    selected_point_mapping,
    source_identity_digest,
    subsample_curve_product,
)
from cccp.calculation.products import (
    CoordinateAtom,
    CoordinateUnits,
    CurveSeries,
    FrameRecord,
    MissingReason,
    PointRecord,
    ProductKind,
    ProductState,
    ScienceProduct,
    SeriesRef,
    build_curve_scatter_product,
    build_structure_trajectory_product,
    frame_stable_id,
    point_stable_id,
    product_from_dict,
    product_to_dict,
    series_stable_id,
)

# ── deterministic fixtures (T31 formula) ────────────────────────────────

SERIES_KEY = "scan_energy"
SERIES_ID = series_stable_id(ProductKind.CURVE_SCATTER, SERIES_KEY)
X_UNIT = "angstrom-or-degree"
Y_UNIT = "hartree"


def _energy_y(i: int) -> float:
    """Deterministic energy value (identical to the T31 synthetic sample)."""
    x = round(i * 0.01, 2)
    return (
        -114.5 + 0.5 * (1.0 - math.cos(x / 10.0)) + 0.02 * math.sin(x) + 0.005 * math.cos(3.0 * x)
    )


def _series(n: int, ys: list[float] | None = None) -> CurveSeries:
    points = []
    for i in range(n):
        y = ys[i] if ys is not None else _energy_y(i)
        points.append(
            PointRecord(
                point_id=point_stable_id(SERIES_ID, i),
                x=round(i * 0.01, 2),
                y=round(y, 8),
            )
        )
    return CurveSeries(
        ref=SeriesRef(series_id=SERIES_ID, x_unit=X_UNIT, y_unit=Y_UNIT, label="scan energy"),
        points=tuple(points),
    )


def _curve_product(n: int) -> ScienceProduct:
    return build_curve_scatter_product(series=[_series(n)], raw_input={"n": n, "unit": Y_UNIT})


def _custom_curve_product(ys: list[float]) -> ScienceProduct:
    return build_curve_scatter_product(series=[_series(len(ys), ys)], raw_input={"ys": ys})


def _frame(i: int, n_atoms: int = 4) -> FrameRecord:
    atoms = tuple(
        CoordinateAtom(
            atom_uid=f"C:{j}",
            element="C",
            coordinate=(float(i) + 0.01 * j, 0.01 * j, float(i % 3)),
        )
        for j in range(n_atoms)
    )
    return FrameRecord(
        frame_index=i,
        coordinate_units=CoordinateUnits.ANGSTROM,
        topology_source="synth",
        frame_id=frame_stable_id(ProductKind.STRUCTURE_TRAJECTORY, i),
        atoms=atoms,
        block_index=0,
    )


def _traj_product(n: int) -> ScienceProduct:
    return build_structure_trajectory_product(
        frames=[_frame(i) for i in range(n)],
        topology_source="synth",
        atom_order=tuple(f"C:{j}" for j in range(4)),
        raw_input={"n": n},
    )


# ── lossless chunking ───────────────────────────────────────────────────


def test_chunk_blocks_are_bounded():
    product = _curve_product(1000)
    bounded = chunk_curve_product(product, chunk_size=128)
    assert len(bounded.blocks) == math.ceil(1000 / 128)
    assert bounded.max_block_size() <= 128
    assert [block.block_index for block in bounded.blocks] == list(range(len(bounded.blocks)))
    assert [block.start for block in bounded.blocks] == [0, 128, 256, 384, 512, 640, 768, 896]
    assert bounded.blocks[-1].end == 1000
    assert bounded.total_items() == 1000
    assert bounded.covered_range() == (0, 1000)
    assert bounded.display_subsampled is False
    assert bounded.note == ""


def test_chunk_round_trip_is_lossless():
    product = _curve_product(1000)
    bounded = chunk_curve_product(product, chunk_size=128)
    original = product.payload.series[0]
    rebuilt = reconstruct_curve_series(bounded)
    assert rebuilt.ref == original.ref
    assert [
        (point.point_id, point.x, point.y, point.missing_reason) for point in rebuilt.points
    ] == [(point.point_id, point.x, point.y, point.missing_reason) for point in original.points]
    assert len(rebuilt.points) == len(original.points)


@pytest.mark.parametrize("chunk_size,n", [(1, 5), (7, 40), (1000, 40), (3, 10)])
def test_chunk_size_and_block_boundary_matrix(chunk_size, n):
    bounded = chunk_curve_product(_curve_product(n), chunk_size=chunk_size)
    assert bounded.max_block_size() <= chunk_size
    assert bounded.total_items() == n
    rebuilt = reconstruct_curve_series(bounded)
    assert len(rebuilt.points) == n
    assert [native_index_of(point) for point in rebuilt.points] == list(range(n))


def test_chunk_preserves_point_ids_and_native_indices():
    bounded = chunk_curve_product(_curve_product(500), chunk_size=99)
    original = _curve_product(500).payload.series[0].points
    rebuilt = reconstruct_curve_series(bounded)
    assert [point.point_id for point in rebuilt.points] == [point.point_id for point in original]
    assert [native_index_of(point) for point in rebuilt.points] == list(range(500))


def test_chunk_retains_missing_point_as_reason_not_zero():
    series = _series(10)
    points = list(series.points)
    points[3] = PointRecord(
        point_id=points[3].point_id,
        missing_reason=MissingReason.MISSING_POINT_VALUES,
    )
    partial = build_curve_scatter_product(
        series=[CurveSeries(ref=series.ref, points=tuple(points))],
        raw_input={"n": 10, "partial": True},
    )
    bounded = chunk_curve_product(partial, chunk_size=4)
    rebuilt = reconstruct_curve_series(bounded)
    missing = [point for point in rebuilt.points if point.point_id == point_stable_id(SERIES_ID, 3)]
    assert len(missing) == 1
    assert missing[0].missing_reason is MissingReason.MISSING_POINT_VALUES
    assert missing[0].x is None and missing[0].y is None
    assert bounded.blocks[0].product.complete is False
    assert all(block.product.complete for block in bounded.blocks[1:])


def test_chunk_rejects_bad_arguments_and_products():
    product = _curve_product(10)
    with pytest.raises(BoundedSchemaError):
        chunk_curve_product(product, chunk_size=0)
    with pytest.raises(BoundedSchemaError):
        chunk_curve_product(product, chunk_size=True)  # type: ignore[arg-type]
    unavailable = build_curve_scatter_product(series=())
    assert unavailable.state is ProductState.UNAVAILABLE
    with pytest.raises(BoundedSchemaError):
        chunk_curve_product(unavailable, chunk_size=4)
    with pytest.raises(BoundedSchemaError):
        chunk_curve_product(_traj_product(3), chunk_size=2)


def test_reconstruct_rejects_subsampled_manifest():
    product = _curve_product(100)
    subsampled = subsample_curve_product(product, stride=10)
    with pytest.raises(BoundedSchemaError):
        reconstruct_curve_series(subsampled)


# ── display subsampling ─────────────────────────────────────────────────


def test_subsample_selected_points_map_to_original():
    product = _curve_product(200)
    bounded = subsample_curve_product(product, stride=7)
    original = product.payload.series[0].points
    mapping = selected_point_mapping(bounded)
    indices = [index for index, _point in mapping]
    assert set(range(0, 200, 7)) <= set(indices)
    assert indices == sorted(set(indices))
    for index, point in mapping:
        assert point.point_id == original[index].point_id
        assert point.x == original[index].x
        assert point.y == original[index].y
    assert bounded.display_subsampled is True
    assert bounded.note == DISPLAY_SAMPLING_NOTE
    assert "display sampling" in bounded.note and "显示抽样" in bounded.note


def test_subsample_retains_key_points():
    product = _curve_product(500)
    bounded = subsample_curve_product(product, stride=37)
    indices = {index for index, _point in selected_point_mapping(bounded)}
    original = product.payload.series[0].points
    ys = [point.y for point in original]

    kinds = {record.kind: record for record in bounded.key_points}
    assert kinds[KEY_GLOBAL_MIN].native_index == min(range(len(ys)), key=ys.__getitem__)
    assert kinds[KEY_GLOBAL_MAX].native_index == max(range(len(ys)), key=ys.__getitem__)
    endpoints = {
        record.native_index for record in bounded.key_points if record.kind == KEY_ENDPOINTS
    }
    assert endpoints >= {0, 499}
    assert all(record.retained for record in bounded.key_points)
    for record in bounded.key_points:
        assert record.native_index in indices
        assert record.reason == ""


def test_subsample_labels_omitted_key_points():
    # min at index 4, max at index 8; stride 5 selects 0,5,10 only.
    product = _custom_curve_product([5.0, 4.0, 3.0, 2.0, 1.0, 2.0, 3.0, 4.0, 9.0, 4.0, 3.0])
    bounded = subsample_curve_product(
        product, stride=5, key_points=(KEY_GLOBAL_MIN, KEY_GLOBAL_MAX), retain_key_points=False
    )
    records = {record.kind: record for record in bounded.key_points}
    assert records[KEY_GLOBAL_MIN].native_index == 4
    assert records[KEY_GLOBAL_MAX].native_index == 8
    assert records[KEY_GLOBAL_MIN].retained is False
    assert records[KEY_GLOBAL_MIN].reason
    assert records[KEY_GLOBAL_MAX].retained is False
    assert records[KEY_GLOBAL_MAX].reason
    # no key point is silently dropped: every omission is labeled.
    assert all(record.retained or record.reason for record in bounded.key_points)


def test_subsample_find_key_point_indices_local_minima():
    points = _custom_curve_product([5.0, 4.0, 1.0, 4.0, 0.5, 4.0, 3.0]).payload.series[0].points
    found = find_key_point_indices(points, (KEY_LOCAL_MIN,))
    assert [index for _kind, index in found] == [2, 4]


def test_subsample_retains_local_minima():
    product = _custom_curve_product([5.0, 4.0, 1.0, 4.0, 0.5, 4.0, 3.0])
    bounded = subsample_curve_product(product, stride=5, key_points=(KEY_LOCAL_MIN,))
    indices = {index for index, _point in selected_point_mapping(bounded)}
    assert {2, 4} <= indices
    assert all(record.retained for record in bounded.key_points)
    assert {record.native_index for record in bounded.key_points} == {2, 4}


def test_subsample_rejects_bad_stride():
    with pytest.raises(BoundedSchemaError):
        subsample_curve_product(_curve_product(10), stride=0)


# ── derived identity ────────────────────────────────────────────────────


def test_identity_stable_for_same_input_and_distinct_for_parameters():
    product = _curve_product(100)
    first = chunk_curve_product(product, chunk_size=10).identity
    second = chunk_curve_product(product, chunk_size=10).identity
    coarser = chunk_curve_product(product, chunk_size=20).identity
    assert first == second
    assert first.params_digest == second.params_digest
    assert first.params_digest != coarser.params_digest
    assert first != coarser
    assert first.input_digest == product.identity.input_digest
    assert first.generator == GENERATOR_CHUNK
    assert first.algorithm == "chunk"
    assert first.algorithm_version == "2"
    # platform identity never enters the derived identity
    assert "job" not in json.dumps(first.to_dict())
    assert "account" not in json.dumps(first.to_dict())


def test_identity_distinct_for_range_and_method():
    product = _curve_product(100)
    base = product.identity
    short = bounded_identity(
        base, BoundedSpec(method="chunk", resolution=10, start=0, end=50), generator=GENERATOR_CHUNK
    )
    long = bounded_identity(
        base,
        BoundedSpec(method="chunk", resolution=10, start=0, end=100),
        generator=GENERATOR_CHUNK,
    )
    stride = subsample_curve_product(product, stride=10).identity
    assert short.params_digest != long.params_digest
    assert short.params_digest != stride.params_digest
    assert stride.generator == GENERATOR_SUBSAMPLE
    assert stride.algorithm == "stride"


def test_identity_distinct_for_multiresolution_level_set():
    product = _curve_product(100)
    two = build_multiresolution_product(product, resolutions=[1, 10]).identity
    three = build_multiresolution_product(product, resolutions=[1, 10, 100]).identity
    assert two.params_digest != three.params_digest
    assert two.generator == GENERATOR_MULTIRESOLUTION


def _source_from_same_input(*, y: float, version: str):
    """Two sources over the *same* raw input digest but different generators."""
    series = CurveSeries(
        ref=SeriesRef(series_id="curve", x_unit="angstrom", y_unit="hartree"),
        points=(PointRecord(point_id="p0", x=0.0, y=y),),
    )
    return build_curve_scatter_product(
        series=[series],
        input_digest="sha256:same-raw-input",
        algorithm="parser",
        algorithm_version=version,
        params_digest=f"sha256:params:{version}",
    )


def test_d10_counterexample_distinct_source_science_keeps_derived_identity():
    """D10: two sources over one input must not collapse to one cache identity."""
    a, b = _source_from_same_input(y=-1.0, version="1"), _source_from_same_input(
        y=-2.0, version="2"
    )
    assert a.identity != b.identity
    chunked_a = chunk_curve_product(a, chunk_size=1)
    chunked_b = chunk_curve_product(b, chunk_size=1)
    assert chunked_a.identity != chunked_b.identity, (
        "different source algorithms collapse to one derived identity"
    )
    assert chunked_a.blocks[0].product.identity != chunked_b.blocks[0].product.identity, (
        "different source algorithms collapse to one block identity"
    )
    assert chunked_a.identity.params_digest != chunked_b.identity.params_digest
    assert chunked_a.blocks[0].product.identity.params_digest != (
        chunked_b.blocks[0].product.identity.params_digest
    )
    assert chunked_a.identity.input_digest == a.identity.input_digest
    assert chunked_a.identity.algorithm_version == "2"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("generator", "cccp.other.build_curve_scatter_product"),
        ("algorithm", "other_parser"),
        ("algorithm_version", "9"),
        ("params_digest", "sha256:other-params"),
    ],
)
def test_source_identity_fields_reach_derived_and_block_identities(field, value):
    product = _curve_product(12)
    variant = replace(product, identity=replace(product.identity, **{field: value}))
    reference = chunk_curve_product(product, chunk_size=4)
    changed = chunk_curve_product(variant, chunk_size=4)
    assert changed.identity != reference.identity
    assert changed.identity.params_digest != reference.identity.params_digest
    assert len(changed.blocks) == len(reference.blocks)
    for changed_block, reference_block in zip(changed.blocks, reference.blocks):
        assert changed_block.product.identity != reference_block.product.identity
        assert (
            changed_block.product.identity.params_digest
            != reference_block.product.identity.params_digest
        )


def test_every_block_carries_the_verified_source_identity_digest():
    curve = _curve_product(40)
    manifests = (
        chunk_curve_product(curve, chunk_size=8),
        subsample_curve_product(curve, stride=7),
        build_multiresolution_product(curve, resolutions=[1, 5]),
        chunk_trajectory_product(_traj_product(9), chunk_size=4),
    )
    for manifest in manifests:
        expected = source_identity_digest(manifest.base_identity)
        assert manifest.schema_version == BOUNDED_SCHEMA_VERSION
        assert manifest.spec.identity_rule_version == BOUNDED_IDENTITY_RULE_VERSION
        assert manifest.spec.source_identity_digest == expected
        assert manifest.identity.params_digest == manifest.spec.params_digest()
        assert manifest.blocks, "a bounded product always carries blocks"
        assert all(block.source_identity_digest == expected for block in manifest.blocks)
        assert manifest.is_cache_adoptable() is True


def test_forged_source_identity_digest_is_rejected():
    product = _curve_product(10)
    forged = BoundedSpec(
        method="chunk",
        resolution=5,
        start=0,
        end=10,
        source_identity_digest="sha256:" + "0" * 64,
    )
    with pytest.raises(BoundedSchemaError, match="does not match"):
        bounded_identity(product.identity, forged, generator=GENERATOR_CHUNK)
    # The legacy rule cannot smuggle a digest past verification either.
    with pytest.raises(BoundedSchemaError):
        BoundedSpec(
            method="chunk",
            resolution=5,
            start=0,
            end=10,
            identity_rule_version=LEGACY_BOUNDED_IDENTITY_RULE_VERSION,
            source_identity_digest="sha256:" + "1" * 64,
        )


_BOUNDED_FIXTURE = (
    Path(__file__).parent / "fixtures" / "science_products" / "bounded"
    / "curve_scatter_normal" / "manifest.json"
)


def test_v1_manifest_reads_with_old_digest_but_is_not_cache_adoptable():
    document = json.loads(_BOUNDED_FIXTURE.read_text())
    legacy = BoundedProduct.from_dict(document)
    assert legacy.schema_version == LEGACY_BOUNDED_SCHEMA_VERSION
    assert legacy.spec.identity_rule_version == LEGACY_BOUNDED_IDENTITY_RULE_VERSION
    assert legacy.identity.params_digest == document["bounded_identity"]["params_digest"]
    # The v1 params payload is reproduced byte-for-byte (old rule, no source digest).
    assert legacy.spec.params_digest() == legacy.identity.params_digest
    assert legacy.is_cache_adoptable() is False
    again = BoundedProduct.from_dict(json.loads(json.dumps(legacy.to_dict())))
    assert again == legacy
    assert again.is_cache_adoptable() is False


def test_v1_to_v2_migration_rebuilds_under_the_new_identity():
    legacy = BoundedProduct.from_dict(json.loads(_BOUNDED_FIXTURE.read_text()))
    series = reconstruct_curve_series(legacy)
    base = build_curve_scatter_product(
        series=[series],
        input_digest=legacy.base_identity.input_digest,
        algorithm=legacy.base_identity.algorithm,
        algorithm_version=legacy.base_identity.algorithm_version,
        params_digest=legacy.base_identity.params_digest,
    )
    assert base.identity.to_dict() == legacy.base_identity.to_dict()
    rebuilt = chunk_curve_product(base, chunk_size=legacy.spec.resolution)
    assert rebuilt.schema_version == BOUNDED_SCHEMA_VERSION
    assert rebuilt.identity.input_digest == legacy.identity.input_digest
    assert rebuilt.identity.params_digest != legacy.identity.params_digest
    assert rebuilt.is_cache_adoptable() is True
    assert legacy.is_cache_adoptable() is False


# ── trajectory chunking ─────────────────────────────────────────────────


def test_trajectory_chunk_bounded_and_lossless_round_trip():
    product = _traj_product(25)
    bounded = chunk_trajectory_product(product, chunk_size=10)
    assert [block.item_count() for block in bounded.blocks] == [10, 10, 5]
    assert bounded.max_block_size() <= 10
    assert bounded.display_subsampled is False
    frames = reconstruct_structure_frames(bounded)
    assert frames == product.payload.frames
    assert [frame.frame_index for frame in frames] == list(range(25))
    assert [frame.frame_id for frame in frames] == [
        frame_stable_id(ProductKind.STRUCTURE_TRAJECTORY, i) for i in range(25)
    ]


def test_trajectory_chunk_rejects_curve_product():
    with pytest.raises(BoundedSchemaError):
        chunk_trajectory_product(_curve_product(5), chunk_size=2)


# ── multi-resolution ────────────────────────────────────────────────────


def test_multiresolution_levels_map_back_and_are_bounded():
    product = _curve_product(1000)
    original = product.payload.series[0]
    bounded = build_multiresolution_product(product, resolutions=[1, 10, 100])
    assert [block.resolution for block in bounded.blocks] == [1, 10, 100]
    assert bounded.display_subsampled is True
    assert bounded.note == DISPLAY_SAMPLING_NOTE
    by_resolution = {block.resolution: block for block in bounded.blocks}
    for resolution, block in by_resolution.items():
        series = block.product.payload.series[0]
        assert series.ref.series_id == f"{SERIES_ID}:res:{resolution}"
        indices = [native_index_of(point) for point in series.points]
        assert indices == sorted(set(indices))
        assert set(range(0, 1000, resolution)) <= set(indices)
        for index, point in zip(indices, series.points):
            assert point.x == original.points[index].x
            assert point.y == original.points[index].y
    assert len(by_resolution[1].product.payload.series[0].points) == 1000
    assert len(by_resolution[100].product.payload.series[0].points) < 1000
    for record in bounded.key_points:
        assert record.resolution in {1, 10, 100}
        assert record.retained or record.reason


def test_multiresolution_key_points_retained_per_level():
    product = _curve_product(1000)
    bounded = build_multiresolution_product(
        product, resolutions=[1, 50], key_points=(KEY_GLOBAL_MIN,)
    )
    per_level = {(record.kind, record.resolution): record for record in bounded.key_points}
    for resolution in (1, 50):
        record = per_level[(KEY_GLOBAL_MIN, resolution)]
        assert record.retained
        assert record.native_index in {
            native_index_of(point)
            for point in {block.resolution: block for block in bounded.blocks}[resolution]
            .product.payload.series[0]
            .points
        }


# ── serialization + real entry points ───────────────────────────────────


def _all_manifests():
    product = _curve_product(120)
    return (
        chunk_curve_product(product, chunk_size=32),
        subsample_curve_product(product, stride=9),
        build_multiresolution_product(product, resolutions=[1, 10]),
        chunk_trajectory_product(_traj_product(12), chunk_size=5),
    )


@pytest.mark.parametrize("manifest", _all_manifests())
def test_manifest_and_blocks_round_trip_serialization(manifest):
    document = json.loads(json.dumps(manifest.to_dict()))
    assert BoundedProduct.from_dict(document) == manifest
    for block in manifest.blocks:
        restored = product_from_dict(product_to_dict(block.product))
        assert restored == block.product
        assert product_to_dict(block.product) == block.product.to_dict()


def test_generation_runs_through_real_production_entry_points():
    curve = _curve_product(20)
    bounded = chunk_curve_product(curve, chunk_size=6)
    real_curve_generator = "cccp.calculation.products.curve.build_curve_scatter_product"
    real_traj_generator = "cccp.calculation.products.structure.build_structure_trajectory_product"
    assert bounded.base_identity.generator == real_curve_generator
    assert bounded.identity.generator == GENERATOR_CHUNK
    for block in bounded.blocks:
        assert block.product.identity.generator == real_curve_generator
        assert block.product.identity.input_digest == bounded.base_identity.input_digest

    traj = _traj_product(5)
    traj_bounded = chunk_trajectory_product(traj, chunk_size=2)
    assert traj_bounded.base_identity.generator == real_traj_generator
    assert traj_bounded.identity.generator == GENERATOR_TRAJECTORY_CHUNK
    assert traj_bounded.blocks[0].product.identity.generator == real_traj_generator


def test_bounded_error_is_a_product_schema_error():
    from cccp.calculation.products import ProductSchemaError

    assert issubclass(BoundedSchemaError, ProductSchemaError)
