"""Bounded block identity collision across multiresolution retention policy."""
from cccp.analysis.bounded import BoundedProduct, build_multiresolution_product
from tests.test_cccp_bounded_products import _custom_curve_product

source = _custom_curve_product([0, 100, -100, 2, 3, 1, 0])
without = build_multiresolution_product(source, resolutions=[3], retain_key_points=False)
with_keys = build_multiresolution_product(source, resolutions=[3], retain_key_points=True)
print("same_block_identity:", without.blocks[0].product.identity == with_keys.blocks[0].product.identity)
print("block_sizes:", without.blocks[0].item_count(), with_keys.blocks[0].item_count())
print("different_parent_identity:", without.identity != with_keys.identity)
print("both_adoptable:", without.is_cache_adoptable(), with_keys.is_cache_adoptable())
payload = with_keys.to_dict()
payload["blocks"][0] = without.to_dict()["blocks"][0]
mixed = BoundedProduct.from_dict(payload)
print("foreign_policy_block_accepted:", mixed.is_cache_adoptable())
print("accepted_size:", mixed.blocks[0].item_count())
