#!/usr/bin/env python3.11
"""HN-03 (plan todo 33, C06-s3) per-fixture consumption walk.

Consumption contract check for B (Data byte/metadata) and A (limited product
consumption).  "The sample is valid JSON" is NOT sufficient (review X2): every
fixture gets a full walk —

  read index -> (byte/metadata verification) -> read block via the block index
  -> decode product through the single C02 reader -> reconstruct (lossless) or
  map/report (partial/empty) or typed rejection (corrupt)

— and the result is compared against ``expected.json`` and the
``expected_consumption`` block recorded in ``index.json`` (versioned delivery
fields).  Step 6 independently re-derives the index data fields from the
manifest + block files and diffs them against the stored ``index.json``
(block <-> index consistency).

Usage (from any cwd):

  python3.11 tests/baseline/cccp-evidence/handoffs/walks/hn03_walk.py <fixture>
  python3.11 tests/baseline/cccp-evidence/handoffs/walks/walk.py --all
  python3.11 .../hn03_walk.py qa [fixture]     # steps 1-6 only (index consistency)

Exit code 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from cccp.analysis.bounded import (
    BOUNDED_SCHEMA_VERSION,
    BoundedProduct,
    native_index_of,
    reconstruct_curve_series,
    reconstruct_structure_frames,
)
from cccp.calculation.products.contracts import PRODUCT_SCHEMA_VERSION
from cccp.calculation.products.serialize import product_from_dict, product_to_dict

REPO = Path(__file__).resolve().parents[5]
FIXTURES = REPO / "tests" / "fixtures" / "science_products" / "bounded"

INDEX_SCHEMA = "hn03_block_index"
INDEX_SCHEMA_VERSION = 1
DELIVERY_VERSION = "hn-03-bounded-delivery-v1"

ALL_FIXTURES = (
    "curve_scatter_normal",
    "curve_scatter_empty",
    "curve_scatter_partial",
    "curve_scatter_corrupt",
    "structure_trajectory_normal",
    "structure_trajectory_partial",
)

CANON_KW: dict[str, Any] = {"sort_keys": True, "ensure_ascii": False, "separators": (",", ":")}
DATA_FIELDS = (
    "manifest",
    "bounded_identity",
    "base_identity",
    "spec",
    "display_subsampled",
    "note",
    "block_count",
    "max_block_size",
    "total_items",
    "covered_range",
    "blocks",
)


class WalkError(Exception):
    """Walk assertion failure (ruff N818 naming)."""


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_digest(payload: object) -> str:
    return sha256_bytes(json.dumps(payload, **CANON_KW).encode("utf-8"))


def qualified(exc: BaseException) -> str:
    """Public import path of ``exc``'s type (``json.decoder`` -> ``json``)."""
    module = type(exc).__module__
    if module == "json.decoder":
        module = "json"
    return f"{module}.{type(exc).__qualname__}"


def load_json(path: Path) -> tuple[bytes, Any]:
    raw = path.read_bytes()
    return raw, json.loads(raw)


def expect_rejection(label: str, thunk: Any, expected: str) -> None:
    try:
        thunk()
    except Exception as exc:  # rejection paths are the assertion under test
        got = qualified(exc)
        if got != expected:
            raise WalkError(f"{label}: expected rejection {expected}, got {got}: {exc}") from exc
        print(f"  REJECT {label} -> {got}: {str(exc)[:100]}")
        return
    raise WalkError(f"{label}: expected rejection {expected}, but no exception was raised")


# ── index re-derivation (independent of the generator) ──────────────────


def item_count_of(product_dict: dict[str, Any]) -> int:
    payload = product_dict.get("payload") or {}
    if "series" in payload:
        return sum(len(series.get("points", [])) for series in payload["series"])
    return len(payload.get("frames", []))


def rederive_index(
    stored: dict[str, Any],
    manifest_bytes: bytes,
    manifest_dict: dict[str, Any],
    block_raws: dict[str, bytes],
    block_products: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Rebuild the data-derived index fields from manifest + block files."""
    bounded = BoundedProduct.from_dict(manifest_dict)
    blocks = []
    for entry in stored["blocks"]:
        product_dict = block_products[entry["file"]]
        blocks.append(
            {
                "block_index": entry["block_index"],
                "start": entry["start"],
                "end": entry["end"],
                "resolution": entry["resolution"],
                "item_count": item_count_of(product_dict),
                "file": entry["file"],
                "bytes": len(block_raws[entry["file"]]),
                "sha256": sha256_bytes(block_raws[entry["file"]]),
                "product_schema_version": product_dict.get("schema_version"),
                "state": product_dict.get("state"),
                "complete": product_dict.get("complete"),
                "identity": {
                    "generator": product_dict["identity"]["generator"],
                    "algorithm": product_dict["identity"]["algorithm"],
                    "algorithm_version": product_dict["identity"]["algorithm_version"],
                    "params_digest": product_dict["identity"]["params_digest"],
                    "input_digest": product_dict["identity"]["input_digest"],
                },
            }
        )
    return {
        "index_schema": INDEX_SCHEMA,
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "delivery_version": DELIVERY_VERSION,
        "bounded_schema_version": BOUNDED_SCHEMA_VERSION,
        "product_schema_version": PRODUCT_SCHEMA_VERSION,
        "product_kind": stored["product_kind"],
        "manifest": {
            "file": "manifest.json",
            "bytes": len(manifest_bytes),
            "sha256": sha256_bytes(manifest_bytes),
        },
        "bounded_identity": bounded.identity.to_dict(),
        "base_identity": bounded.base_identity.to_dict(),
        "spec": bounded.spec.to_dict(),
        "display_subsampled": bounded.display_subsampled,
        "note": bounded.note,
        "block_count": len(bounded.blocks),
        "max_block_size": bounded.max_block_size(),
        "total_items": bounded.total_items(),
        "covered_range": list(bounded.covered_range()),
        "blocks": blocks,
    }


# ── consumption (reconstruct / map from block files) ────────────────────


def points_from_products(block_objects: list[Any]) -> list[Any]:
    """Rebuild the ordered point sequence from decoded block products."""
    entries: list[tuple[int, Any]] = []
    for product in block_objects:
        payload = product.payload
        for series in payload.series:
            for point in series.points:
                entries.append((native_index_of(point), point))
    entries.sort(key=lambda item: item[0])
    indices = [index for index, _point in entries]
    if indices != list(range(len(indices))):
        raise WalkError(f"block-file reconstruction does not cover 0..N-1: {indices[:8]}...")
    return [point for _index, point in entries]


def frames_from_products(block_objects: list[Any]) -> list[Any]:
    """Rebuild the ordered frame sequence from decoded block products."""
    frames: list[Any] = []
    for product in block_objects:
        frames.extend(product.payload.frames)
    frames.sort(key=lambda frame: frame.frame_index)
    indices = [frame.frame_index for frame in frames]
    if indices != list(range(len(indices))):
        raise WalkError(f"block-file reconstruction does not cover frame 0..N-1: {indices[:8]}...")
    return frames


def curve_checks(
    block_count: int,
    max_block: int,
    total: int,
    covered: list[int],
    complete: list[bool],
    points: list[Any],
) -> dict[str, Any]:
    missing = [
        {
            "native_index": index,
            "point_id": point.point_id,
            "reason": point.missing_reason.value if point.missing_reason else None,
        }
        for index, point in enumerate(points)
        if point.missing_reason is not None
    ]
    return {
        "n_points": len(points),
        "first_point_id": points[0].point_id if points else None,
        "last_point_id": points[-1].point_id if points else None,
        "block_count": block_count,
        "max_block_size": max_block,
        "total_items": total,
        "covered_range": covered,
        "block_complete": complete,
        "missing": missing,
    }


def traj_checks(
    block_count: int,
    max_block: int,
    total: int,
    covered: list[int],
    complete: list[bool],
    frames: list[Any],
) -> dict[str, Any]:
    missing = [
        {
            "frame_index": frame.frame_index,
            "frame_id": frame.frame_id,
            "reason": frame.missing_reason.value if frame.missing_reason else None,
        }
        for frame in frames
        if frame.missing_reason is not None
    ]
    return {
        "n_frames": len(frames),
        "first_frame_id": frames[0].frame_id if frames else None,
        "last_frame_id": frames[-1].frame_id if frames else None,
        "block_count": block_count,
        "max_block_size": max_block,
        "total_items": total,
        "covered_range": covered,
        "block_complete": complete,
        "missing": missing,
    }


def curve_digest(points: list[Any]) -> str:
    return canonical_digest(
        [
            [
                point.point_id,
                point.x,
                point.y,
                point.missing_reason.value if point.missing_reason else None,
            ]
            for point in points
        ]
    )


def traj_digest(frames: list[Any]) -> str:
    return canonical_digest(
        [
            [
                frame.frame_id,
                frame.frame_index,
                frame.missing_reason.value if frame.missing_reason else None,
                [[atom.atom_uid, atom.element, list(atom.coordinate)] for atom in frame.atoms]
                if frame.missing_reason is None
                else None,
            ]
            for frame in frames
        ]
    )


def assert_eq(label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        raise WalkError(f"{label} mismatch:\n    actual  = {actual!r}\n    expected= {expected!r}")


# ── walk ────────────────────────────────────────────────────────────────


def walk(name: str, *, qa_only: bool = False) -> None:
    target = FIXTURES / name
    if not target.is_dir():
        raise WalkError(f"fixture directory not found: {target}")
    expected_raw, expected = load_json(target / "expected.json")
    print(f"WALK fixture={name}")
    print(f"  expected.json bytes={len(expected_raw)} sha256={sha256_bytes(expected_raw)}")

    # STEP 1-2: index header (Data byte/metadata entry point)
    index_raw, index = load_json(target / "index.json")
    print(f"  STEP1 index.json bytes={len(index_raw)} sha256={sha256_bytes(index_raw)}")
    header_ok = (
        index.get("index_schema") == INDEX_SCHEMA
        and index.get("index_schema_version") == INDEX_SCHEMA_VERSION
        and index.get("delivery_version") == DELIVERY_VERSION
        and index.get("bounded_schema_version") == BOUNDED_SCHEMA_VERSION
        and index.get("product_schema_version") == PRODUCT_SCHEMA_VERSION
    )
    if not header_ok:
        raise WalkError(
            f"index header mismatch: {index.get('index_schema')} "
            f"v{index.get('index_schema_version')}"
        )
    print(
        f"  STEP2 header ok: {index['index_schema']} v{index['index_schema_version']} "
        f"delivery={index['delivery_version']} bounded_schema={index['bounded_schema_version']} "
        f"product_schema={index['product_schema_version']} state={index['delivery_state']} "
        f"kind={index['product_kind']}"
    )
    is_corrupt = expected["operation"] == "reject"
    assert_eq("delivery_state", index["delivery_state"], "corrupt" if is_corrupt else "ok")
    assert_eq(
        "operation(index vs expected)",
        index["expected_consumption"]["operation"],
        expected["operation"],
    )
    assert_eq(
        "expected_rejection(index vs expected)",
        index.get("expected_rejection"),
        expected["expected_rejection"],
    )

    # STEP 3: manifest byte verification via index
    manifest_raw, manifest_dict = load_json(target / "manifest.json")
    assert_eq("manifest.bytes", index["manifest"]["bytes"], len(manifest_raw))
    assert_eq("manifest.sha256", index["manifest"]["sha256"], sha256_bytes(manifest_raw))
    print(
        f"  STEP3 manifest bytes={len(manifest_raw)} "
        f"sha256={sha256_bytes(manifest_raw)} matches_index=True"
    )

    if is_corrupt:
        # STEP 4: every declared rejection must be typed and complete
        print("  STEP4 corrupt delivery: consumption = typed rejection only (no partial data)")
        expect_rejection(
            "manifest.json",
            lambda: BoundedProduct.from_dict(manifest_dict),
            expected["expected_rejection"]["manifest.json"],
        )
        block_raws: dict[str, bytes] = {}
        for entry in index["blocks"]:
            raw = (target / entry["file"]).read_bytes()
            block_raws[entry["file"]] = raw
            assert_eq(f"{entry['file']}.bytes", entry["bytes"], len(raw))
            assert_eq(f"{entry['file']}.sha256", entry["sha256"], sha256_bytes(raw))
            expect_rejection(
                entry["file"],
                lambda raw=raw: product_from_dict(json.loads(raw)),
                expected["expected_rejection"][entry["file"]],
            )
        print("  STEP6 index re-derivation: SKIPPED (manifest is contract-invalid by design)")
        print(
            f"WALK RESULT: PASS fixture={name} op=reject "
            f"rejections={len(expected['expected_rejection'])}"
        )
        return

    # STEP 4: manifest decode through the single C02/bounded reader
    bounded = BoundedProduct.from_dict(manifest_dict)
    assert_eq("block_count(index vs manifest)", index["block_count"], len(bounded.blocks))
    print(
        f"  STEP4 manifest decode ok: blocks={len(bounded.blocks)} "
        f"display_subsampled={bounded.display_subsampled} "
        f"spec.method={bounded.spec.method} resolution={bounded.spec.resolution} "
        f"range=[{bounded.spec.start},{bounded.spec.end}]"
    )

    # STEP 5: read every block VIA the block index -> decode -> cross-check
    block_raws = {}
    block_objects: list[Any] = []
    block_products: list[dict[str, Any]] = []
    complete_flags: list[bool] = []
    for pos, entry in enumerate(index["blocks"]):
        raw = (target / entry["file"]).read_bytes()
        block_raws[entry["file"]] = raw
        assert_eq(f"blocks[{pos}].bytes", entry["bytes"], len(raw))
        assert_eq(f"blocks[{pos}].sha256", entry["sha256"], sha256_bytes(raw))
        product = product_from_dict(json.loads(raw))
        block_objects.append(product)
        product_dict = product_to_dict(product)
        block_products.append(product_dict)
        manifest_block = bounded.blocks[pos]
        assert_eq(f"blocks[{pos}].block_index", entry["block_index"], manifest_block.block_index)
        assert_eq(f"blocks[{pos}].start", entry["start"], manifest_block.start)
        assert_eq(f"blocks[{pos}].end", entry["end"], manifest_block.end)
        assert_eq(f"blocks[{pos}].resolution", entry["resolution"], manifest_block.resolution)
        assert_eq(f"blocks[{pos}].item_count", entry["item_count"], manifest_block.item_count())
        assert_eq(f"blocks[{pos}].state", entry["state"], product_dict["state"])
        assert_eq(f"blocks[{pos}].complete", entry["complete"], product_dict["complete"])
        assert_eq(
            f"blocks[{pos}].identity",
            entry["identity"],
            {
                "generator": product_dict["identity"]["generator"],
                "algorithm": product_dict["identity"]["algorithm"],
                "algorithm_version": product_dict["identity"]["algorithm_version"],
                "params_digest": product_dict["identity"]["params_digest"],
                "input_digest": product_dict["identity"]["input_digest"],
            },
        )
        assert_eq(
            f"blocks[{pos}] manifest product", product_dict, product_to_dict(manifest_block.product)
        )
        # lossless-chunk invariant: block range equals carried items
        if bounded.spec.method == "chunk":
            assert_eq(
                f"blocks[{pos}].end-start", entry["end"] - entry["start"], entry["item_count"]
            )
        complete_flags.append(bool(product_dict["complete"]))
        print(
            f"  STEP5 block[{pos}] file={entry['file']} bytes={len(raw)} "
            f"sha256={entry['sha256'][:23]}... decode={product_dict['product_kind']} "
            f"state={product_dict['state']} complete={product_dict['complete']} "
            f"items={entry['item_count']} "
            f"range=[{entry['start']},{entry['end']}) metadata_match=True"
        )
    indices = [entry["block_index"] for entry in index["blocks"]]
    assert_eq("block indices contiguous", indices, list(range(len(indices))))
    assert_eq(
        "covered_range",
        index["covered_range"],
        [index["blocks"][0]["start"], index["blocks"][-1]["end"]]
        if index["blocks"]
        else [bounded.spec.start, bounded.spec.start],
    )

    # STEP 6: independent index re-derivation (block <-> index consistency)
    rederived = rederive_index(
        index,
        manifest_raw,
        manifest_dict,
        block_raws,
        {e["file"]: p for e, p in zip(index["blocks"], block_products)},
    )
    for field in DATA_FIELDS:
        assert_eq(f"rederived[{field}]", rederived[field], index[field])
    assert_eq("rederived header.product_kind", rederived["product_kind"], index["product_kind"])
    print(
        f"  STEP6 index re-derivation: MATCH fields={len(DATA_FIELDS) + 1} "
        f"(manifest+blocks -> index)"
    )

    if qa_only:
        print(f"WALK RESULT: PASS fixture={name} qa=consistency blocks={len(index['blocks'])}")
        return

    # STEP 7: consumption — reconstruct (from block files) or report empty
    op = expected["operation"]
    print(f"  STEP7 consumption op={op}")
    if op == "report_empty":
        payload = {
            "block_count": index["block_count"],
            "max_block_size": index["max_block_size"],
            "total_items": index["total_items"],
            "covered_range": index["covered_range"],
            "block_complete": [],
            "missing": [],
        }
        checks = payload
        digest = canonical_digest(payload)
        assert_eq("index blocks", index["blocks"], [])
    elif op == "reconstruct":
        payload_kind = index["product_kind"]
        if payload_kind == "curve_scatter":
            points = points_from_products(block_objects)
            checks = curve_checks(
                index["block_count"],
                index["max_block_size"],
                index["total_items"],
                index["covered_range"],
                complete_flags,
                points,
            )
            digest = curve_digest(points)
            # cross-check: manifest-side real API reconstruct must agree
            rebuilt = reconstruct_curve_series(bounded)
            assert_eq("manifest reconstruct digest", curve_digest(list(rebuilt.points)), digest)
            print(
                f"  STEP7a reconstruct from block files: n_points={len(points)} "
                f"first={points[0].point_id} last={points[-1].point_id}"
            )
            print(f"  STEP7b reconstruct_curve_series(manifest) digest agrees: {digest[:23]}...")
        else:
            frames = frames_from_products(block_objects)
            checks = traj_checks(
                index["block_count"],
                index["max_block_size"],
                index["total_items"],
                index["covered_range"],
                complete_flags,
                frames,
            )
            digest = traj_digest(frames)
            rebuilt_frames = reconstruct_structure_frames(bounded)
            assert_eq("manifest reconstruct digest", traj_digest(list(rebuilt_frames)), digest)
            print(
                f"  STEP7a reconstruct from block files: n_frames={len(frames)} "
                f"first={frames[0].frame_id} last={frames[-1].frame_id}"
            )
            print(
                f"  STEP7b reconstruct_structure_frames(manifest) digest agrees: {digest[:23]}..."
            )
    else:
        raise WalkError(f"unknown expected operation {op!r}")

    # versioned delivery fields: expected.json <-> index.expected_consumption <-> actual
    assert_eq("expected.checks", checks, expected["checks"])
    assert_eq("expected.result_digest", digest, expected["result_digest"])
    assert_eq(
        "index.expected_consumption.result_digest",
        index["expected_consumption"].get("result_digest"),
        digest,
    )
    if expected["result_digest"]:
        print(
            f"  STEP7c result_digest={digest} matches expected.json AND index.expected_consumption"
        )
    if checks.get("missing"):
        print(f"  STEP7d missing items preserved (never zero-filled): {checks['missing']}")
    print(
        f"WALK RESULT: PASS fixture={name} op={op} blocks={index['block_count']} "
        f"total_items={index['total_items']} digest={digest[:23]}..."
    )


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    try:
        if argv[0] == "--all":
            names = list(ALL_FIXTURES)
            qa_only = False
        elif argv[0] == "qa":
            names = (
                [argv[1]]
                if len(argv) > 1
                else ["curve_scatter_normal", "structure_trajectory_partial"]
            )
            qa_only = True
        else:
            names = [argv[0]]
            qa_only = False
        failures = 0
        for name in names:
            try:
                walk(name, qa_only=qa_only)
            except WalkError as fail:
                failures += 1
                print(f"WALK RESULT: FAIL fixture={name}: {fail}")
            except Exception as exc:  # unexpected walk crash = failure evidence
                failures += 1
                print(f"WALK RESULT: ERROR fixture={name}: {qualified(exc)}: {exc}")
        print(f"SUMMARY fixtures={len(names)} failures={failures}")
        return 1 if failures else 0
    except WalkError as fail:
        print(f"WALK RESULT: FAIL: {fail}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
