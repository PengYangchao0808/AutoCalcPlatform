"""Multi-resolution block identities bind the complete point-selection policy."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from cccp.analysis.bounded import (
    GENERATOR_MULTIRESOLUTION,
    KEY_ENDPOINTS,
    BoundedProduct,
    BoundedSchemaError,
    BoundedSpec,
    bounded_identity,
    build_multiresolution_product,
)
from cccp.analysis.bounded.identity import multiresolution_block_spec
from tests.test_cccp_bounded_products import _custom_curve_product


@pytest.fixture()
def source():
    # The extrema at indices 1/2 are absent from a stride-3 display unless the
    # requested key-point policy retains them.
    return _custom_curve_product([0, 100, -100, 2, 3, 1, 0])


@pytest.mark.parametrize(
    ("left_options", "right_options"),
    [
        ({"retain_key_points": False}, {"retain_key_points": True}),
        ({"key_points": [KEY_ENDPOINTS]}, {}),
    ],
    ids=["retain-policy", "key-point-selectors"],
)
def test_selection_policy_changes_block_identity_and_rejects_splicing(
    source, left_options, right_options
):
    left = build_multiresolution_product(source, resolutions=[3], **left_options)
    right = build_multiresolution_product(source, resolutions=[3], **right_options)

    assert left.blocks[0].item_count() == 3
    assert right.blocks[0].item_count() == 5
    assert left.identity != right.identity
    assert left.blocks[0].product.identity != right.blocks[0].product.identity
    assert left.is_cache_adoptable() is True
    assert right.is_cache_adoptable() is True

    document = right.to_dict()
    document["blocks"] = left.to_dict()["blocks"]
    with pytest.raises(BoundedSchemaError, match="not derivable"):
        BoundedProduct.from_dict(document)


def test_honest_multilevel_round_trip_uses_the_same_level_derivation(source):
    generated = build_multiresolution_product(source, resolutions=[1, 2, 3])
    restored = BoundedProduct.from_dict(json.loads(json.dumps(generated.to_dict())))

    assert restored == generated
    assert restored.is_cache_adoptable() is True
    assert restored.spec.algorithm_version == "3"
    for index, block in enumerate(restored.blocks):
        expected = multiresolution_block_spec(restored.spec, level_index=index)
        assert dict(expected.params)["key_points"] == dict(restored.spec.params)["key_points"]
        assert dict(expected.params)["retain_key_points"] is True
        assert block.product.identity.params_digest == expected.params_digest()


def test_additional_selection_parameters_reach_every_current_level(source):
    generated = build_multiresolution_product(source, resolutions=[1, 3])
    changed_spec = replace(
        generated.spec,
        params=(*generated.spec.params, ("future_selection_option", "changed")),
    )

    for index in range(len(generated.blocks)):
        original = multiresolution_block_spec(generated.spec, level_index=index)
        changed = multiresolution_block_spec(changed_spec, level_index=index)
        assert changed.params_digest() != original.params_digest()


def test_v2_policy_incomplete_multiresolution_identity_is_readable_but_not_adoptable(source):
    current = build_multiresolution_product(source, resolutions=[3])
    old_spec = replace(current.spec, algorithm_version="2")
    # Historical v2 levels omitted both key-point policy fields. This is the
    # old on-wire identity, preserved for display rather than silently adopted.
    params = dict(old_spec.params)
    old_block_spec = BoundedSpec(
        method="multiresolution",
        resolution=3,
        start=old_spec.start,
        end=old_spec.end,
        algorithm_version="2",
        params=(
            ("series_id", params["series_id"]),
            ("level_index", 0),
            ("resolutions", [3]),
        ),
        source_identity_digest=old_spec.source_identity_digest,
    )
    old_block = replace(
        current.blocks[0],
        product=replace(
            current.blocks[0].product,
            identity=replace(
                current.blocks[0].product.identity,
                algorithm_version="2",
                params_digest=old_block_spec.params_digest(),
            ),
        ),
    )
    historical = replace(
        current,
        identity=bounded_identity(
            current.base_identity, old_spec, generator=GENERATOR_MULTIRESOLUTION
        ),
        spec=old_spec,
        blocks=(old_block,),
    )
    restored = BoundedProduct.from_dict(json.loads(json.dumps(historical.to_dict())))

    assert restored == historical
    assert restored.is_cache_adoptable() is False
    assert current.is_cache_adoptable() is True
    assert current.identity != restored.identity
    assert current.blocks[0].product.identity != restored.blocks[0].product.identity

    document = current.to_dict()
    document["blocks"] = historical.to_dict()["blocks"]
    with pytest.raises(BoundedSchemaError, match="algorithm_version"):
        BoundedProduct.from_dict(document)
