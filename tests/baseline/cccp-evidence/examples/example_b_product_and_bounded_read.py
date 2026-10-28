#!/usr/bin/env python3.11
"""Example B (T43) — versioned product contract read + bounded chunk read.

Offline science sample against the tracked C02/C06 fixture group under
``tests/fixtures/science_products/``:

1. read a plain ``structure_trajectory`` product with the single C02 reader
   (``product_from_dict``) and inspect identity / state / frames;
2. read a bounded chunked product directory (manifest + block files) — the
   C06 bounded layer reusing the C02 skeleton — reconstruct the lossless
   curve series and prove every selected point maps to its native index;
3. feed a corrupt manifest to the same reader and observe the typed
   ``BoundedSchemaError`` (never a silent success).

No QC program is launched.

Run from the repo root (offline, no external software):

    PYTHONPATH=src /usr/bin/python3.11 \
        tests/baseline/cccp-evidence/examples/example_b_product_and_bounded_read.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from cccp.analysis.bounded import (
    BOUNDED_SCHEMA_VERSION,
    BoundedSchemaError,
    BoundedProduct,
    reconstruct_curve_series,
    selected_point_mapping,
)
from cccp.calculation.products import ProductKind, product_from_dict

REPO = Path(__file__).resolve().parents[4]
PRODUCTS = REPO / "tests" / "fixtures" / "science_products"
TRAJECTORY = PRODUCTS / "structure_trajectory_normal.json"
BOUNDED_CURVE = PRODUCTS / "bounded" / "curve_scatter_normal"
BOUNDED_CORRUPT = PRODUCTS / "bounded" / "curve_scatter_corrupt"


def read_product(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    return product_from_dict(payload)


def main() -> int:
    print(f"python: {sys.version.split()[0]} ({sys.executable})")

    # 1) C02 product contract read (single reader, typed payload)
    product = read_product(TRAJECTORY)
    print(f"[c02]    fixture={TRAJECTORY.relative_to(REPO)}")
    print(
        f"[c02]    kind={product.identity.product_kind.value} state={product.state.value} "
        f"complete={product.complete} schema_version={product.schema_version}"
    )
    print(
        f"[c02]    generator={product.identity.generator} "
        f"input_digest={product.identity.input_digest[:19]}..."
    )
    assert product.payload is not None  # AVAILABLE -> typed payload present
    frames = product.payload.frames
    print(
        f"[c02]    frames={len(frames)} "
        f"coordinate_units={product.payload.coordinate_units.value} "
        f"topology_source={product.payload.topology_source}"
    )
    first = frames[0]
    print(f"[c02]    frame_ids={[f.frame_id for f in frames]}")
    print(f"[c02]    frame0 atoms={[(a.atom_uid, a.element) for a in first.atoms]}")

    # 2) bounded chunk read (C06 over C02)
    manifest = json.loads((BOUNDED_CURVE / "manifest.json").read_text(encoding="utf-8"))
    bounded = BoundedProduct.from_dict(manifest)
    print(f"[c06]    fixture={BOUNDED_CURVE.relative_to(REPO)}")
    print(
        f"[c06]    bounded_schema={bounded.schema_version} (pkg={BOUNDED_SCHEMA_VERSION})"
    )
    print(
        f"[c06]    spec.method={bounded.spec.method} resolution={bounded.spec.resolution} "
        f"range=[{bounded.spec.start}, {bounded.spec.end})"
    )
    print(
        f"[c06]    blocks={len(bounded.blocks)} items={bounded.total_items()} "
        f"max_block={bounded.max_block_size()}"
    )
    block_files = sorted(p.name for p in BOUNDED_CURVE.glob("block_*.json"))
    print(f"[c06]    block files on disk={block_files}")
    for name in block_files:
        block_product = read_product(BOUNDED_CURVE / name)
        assert block_product.identity.product_kind is ProductKind.CURVE_SCATTER
    print("[c06]    all block files parse via the single C02 reader")

    series = reconstruct_curve_series(bounded)
    print(
        f"[c06]    reconstructed series: {len(series.points)} points, "
        f"series_id={series.ref.series_id}"
    )
    mapping = selected_point_mapping(bounded)
    native_indices = [index for index, _point in mapping]
    print(
        f"[c06]    native index coverage: {native_indices[0]}..{native_indices[-1]} "
        f"contiguous={native_indices == list(range(len(native_indices)))}"
    )

    # 3) corrupt-manifest boundary: typed schema error, never a silent success
    corrupt = json.loads((BOUNDED_CORRUPT / "manifest.json").read_text(encoding="utf-8"))
    try:
        BoundedProduct.from_dict(corrupt)
    except BoundedSchemaError as exc:
        print(f"[c06]    corrupt manifest rejected with BoundedSchemaError: {exc}")
    else:  # pragma: no cover - the fixture must fail
        raise AssertionError("corrupt manifest must not parse")

    # 4) assertions (offline self-check)
    assert bounded.schema_version == BOUNDED_SCHEMA_VERSION == 1
    assert bounded.total_items() == 40 and len(bounded.blocks) == 3
    assert len(series.points) == 40
    assert native_indices == list(range(40))
    print("EXAMPLE B: PASS (product read + bounded chunk read + assertions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
