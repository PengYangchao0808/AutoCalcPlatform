#!/usr/bin/env python3.11
"""HN-03 (plan todo 33, C06-s3) deterministic bounded-fixture generator.

Writes the tracked fixture group under ``tests/fixtures/science_products/bounded/``
that backs the HN-03 consumption contract:

  curve_scatter_normal        40 pts  chunk 16 -> 3 blocks   (reconstruct)
  curve_scatter_empty          0 pts  chunk 16 -> 0 blocks   (report empty)
  curve_scatter_partial       12 pts  chunk  5 -> 3 blocks, pt 3 missing
  curve_scatter_corrupt       derived from the normal set:
                              manifest = valid JSON / schema_version 99
                              block_000000 = valid JSON / bogus state
                              block_000001 = truncated bytes (not JSON)
  structure_trajectory_normal  10 frames chunk 4 -> 3 blocks (reconstruct)
  structure_trajectory_partial 10 frames, frame 4 missing, chunk 4 (map missing)

Every fixture directory holds:
  manifest.json    canonical BoundedProduct.to_dict() (source of truth)
  block_XXXXXX.json  one real C02 product per block (bounded read unit)
  index.json       hn03_block_index v1 — byte/metadata projection (Data only)
  expected.json    expected consumption result (operation + checks + digest)

All numbers reuse the T31/T32 deterministic formulas (no RNG); regenerating
must be byte-identical.  Run from the repo root:

  python3.11 tests/baseline/cccp-evidence/handoffs/walks/hn03_gen_fixtures.py
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from cccp.analysis.bounded import (
    BOUNDED_SCHEMA_VERSION,
    BoundedProduct,
    chunk_curve_product,
    chunk_trajectory_product,
    reconstruct_curve_series,
    reconstruct_structure_frames,
)
from cccp.calculation.products import (
    CoordinateAtom,
    CoordinateUnits,
    CurveSeries,
    FrameRecord,
    MissingReason,
    PointRecord,
    ProductKind,
    SeriesRef,
    build_curve_scatter_product,
    build_structure_trajectory_product,
    frame_stable_id,
    point_stable_id,
    series_stable_id,
)
from cccp.calculation.products.contracts import PRODUCT_SCHEMA_VERSION
from cccp.calculation.products.serialize import product_to_dict

REPO = Path(__file__).resolve().parents[5]
FIXTURES = REPO / "tests" / "fixtures" / "science_products" / "bounded"

INDEX_SCHEMA = "hn03_block_index"
INDEX_SCHEMA_VERSION = 1
DELIVERY_VERSION = "hn-03-bounded-delivery-v1"

SERIES_KEY = "scan_energy"
SERIES_ID = series_stable_id(ProductKind.CURVE_SCATTER, SERIES_KEY)
X_UNIT = "angstrom-or-degree"
Y_UNIT = "hartree"

NORMAL_N = 40
NORMAL_CHUNK = 16
EMPTY_CHUNK = 16
PARTIAL_N = 12
PARTIAL_MISSING = 3
PARTIAL_CHUNK = 5
CORRUPT_CHUNK = 16
TRAJ_N = 10
TRAJ_ATOMS = 4
TRAJ_CHUNK = 4
TRAJ_MISSING_FRAME = 4

DUMPER_KW: dict[str, Any] = {"ensure_ascii": False, "indent": 1}
CANON_KW: dict[str, Any] = {"sort_keys": True, "ensure_ascii": False, "separators": (",", ":")}


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def write_json(path: Path, payload: object) -> bytes:
    data = json.dumps(payload, **DUMPER_KW).encode("utf-8")
    path.write_bytes(data)
    return data


def canonical_digest(payload: object) -> str:
    return sha256_bytes(json.dumps(payload, **CANON_KW).encode("utf-8"))


def energy_y(i: int) -> float:
    """T31/T32 deterministic energy value (identical arithmetic)."""
    x = round(i * 0.01, 2)
    return (
        -114.5 + 0.5 * (1.0 - math.cos(x / 10.0)) + 0.02 * math.sin(x) + 0.005 * math.cos(3.0 * x)
    )


def curve_ref() -> SeriesRef:
    return SeriesRef(series_id=SERIES_ID, x_unit=X_UNIT, y_unit=Y_UNIT, label="scan energy")


def curve_points(n: int, missing_index: int | None = None) -> tuple[PointRecord, ...]:
    points = []
    for i in range(n):
        if i == missing_index:
            points.append(
                PointRecord(
                    point_id=point_stable_id(SERIES_ID, i),
                    missing_reason=MissingReason.MISSING_POINT_VALUES,
                )
            )
        else:
            points.append(
                PointRecord(
                    point_id=point_stable_id(SERIES_ID, i),
                    x=round(i * 0.01, 2),
                    y=round(energy_y(i), 8),
                )
            )
    return tuple(points)


def curve_product(n: int, missing_index: int | None = None, *, partial: bool = False):
    series = CurveSeries(ref=curve_ref(), points=curve_points(n, missing_index))
    raw: dict[str, Any] = {"n": n, "unit": Y_UNIT, "formula": "t31_energy_synth"}
    if partial:
        raw["partial"] = True
    return build_curve_scatter_product(series=[series], raw_input=raw)


def traj_frame(i: int, *, missing: bool) -> FrameRecord:
    frame_id = frame_stable_id(ProductKind.STRUCTURE_TRAJECTORY, i)
    if missing:
        return FrameRecord(
            frame_index=i,
            coordinate_units=CoordinateUnits.ANGSTROM,
            topology_source="synth",
            frame_id=frame_id,
            missing_reason=MissingReason.MISSING_COORDINATES,
        )
    atoms = tuple(
        CoordinateAtom(
            atom_uid=f"C:{j}",
            element="C",
            coordinate=(float(i) + 0.01 * j, 0.01 * j, float(i % 3)),
        )
        for j in range(TRAJ_ATOMS)
    )
    return FrameRecord(
        frame_index=i,
        coordinate_units=CoordinateUnits.ANGSTROM,
        topology_source="synth",
        frame_id=frame_id,
        atoms=atoms,
        block_index=0,
    )


def traj_product(*, partial: bool = False):
    frames = tuple(
        traj_frame(i, missing=partial and i == TRAJ_MISSING_FRAME) for i in range(TRAJ_N)
    )
    raw: dict[str, Any] = {"n": TRAJ_N, "formula": "t32_traj_synth"}
    if partial:
        raw["partial"] = True
    return build_structure_trajectory_product(
        frames=frames,
        topology_source="synth",
        atom_order=tuple(f"C:{j}" for j in range(TRAJ_ATOMS)),
        raw_input=raw,
    )


# ── expected-consumption payloads (shared with hn03_walk.py) ────────────


def curve_checks(
    bounded: BoundedProduct, rebuilt_points: tuple[PointRecord, ...]
) -> dict[str, Any]:
    missing = [
        {
            "native_index": index,
            "point_id": point.point_id,
            "reason": point.missing_reason.value if point.missing_reason else None,
        }
        for index, point in enumerate(rebuilt_points)
        if point.missing_reason is not None
    ]
    return {
        "n_points": len(rebuilt_points),
        "first_point_id": rebuilt_points[0].point_id if rebuilt_points else None,
        "last_point_id": rebuilt_points[-1].point_id if rebuilt_points else None,
        "block_count": len(bounded.blocks),
        "max_block_size": bounded.max_block_size(),
        "total_items": bounded.total_items(),
        "covered_range": list(bounded.covered_range()),
        "block_complete": [block.product.complete for block in bounded.blocks],
        "missing": missing,
    }


def traj_checks(bounded: BoundedProduct, rebuilt_frames: tuple[FrameRecord, ...]) -> dict[str, Any]:
    missing = [
        {
            "frame_index": frame.frame_index,
            "frame_id": frame.frame_id,
            "reason": frame.missing_reason.value if frame.missing_reason else None,
        }
        for frame in rebuilt_frames
        if frame.missing_reason is not None
    ]
    return {
        "n_frames": len(rebuilt_frames),
        "first_frame_id": rebuilt_frames[0].frame_id if rebuilt_frames else None,
        "last_frame_id": rebuilt_frames[-1].frame_id if rebuilt_frames else None,
        "block_count": len(bounded.blocks),
        "max_block_size": bounded.max_block_size(),
        "total_items": bounded.total_items(),
        "covered_range": list(bounded.covered_range()),
        "block_complete": [block.product.complete for block in bounded.blocks],
        "missing": missing,
    }


def empty_checks(bounded: BoundedProduct) -> dict[str, Any]:
    return {
        "block_count": len(bounded.blocks),
        "max_block_size": bounded.max_block_size(),
        "total_items": bounded.total_items(),
        "covered_range": list(bounded.covered_range()),
        "block_complete": [],
        "missing": [],
    }


def rebuild_digest_curve(points: tuple[PointRecord, ...]) -> str:
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


def rebuild_digest_traj(frames: tuple[FrameRecord, ...]) -> str:
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


# ── writers ─────────────────────────────────────────────────────────────


def build_index(
    *,
    bounded: BoundedProduct,
    product_kind: str,
    manifest_bytes: bytes,
    block_files: list[tuple[dict[str, Any], bytes]],
    delivery_state: str,
    expected_consumption: dict[str, Any],
    expected_rejection: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Byte/metadata projection of one fixture set (Data-facing, no science)."""
    blocks = []
    for entry, raw in block_files:
        blocks.append(
            {
                "block_index": entry["block_index"],
                "start": entry["start"],
                "end": entry["end"],
                "resolution": entry["resolution"],
                "item_count": entry["item_count"],
                "file": entry["file"],
                "bytes": len(raw),
                "sha256": sha256_bytes(raw),
                "product_schema_version": entry["product_schema_version"],
                "state": entry["state"],
                "complete": entry["complete"],
                "identity": entry["identity"],
            }
        )
    index: dict[str, Any] = {
        "index_schema": INDEX_SCHEMA,
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "delivery_version": DELIVERY_VERSION,
        "bounded_schema_version": BOUNDED_SCHEMA_VERSION,
        "product_schema_version": PRODUCT_SCHEMA_VERSION,
        "delivery_state": delivery_state,
        "product_kind": product_kind,
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
        "expected_consumption": expected_consumption,
    }
    if expected_rejection is not None:
        index["expected_rejection"] = expected_rejection
    return index


def block_entry(block, prefix: str) -> dict[str, Any]:
    product_dict = product_to_dict(block.product)
    payload = product_dict.get("payload") or {}
    if "series" in payload:
        item_count = sum(len(series.get("points", [])) for series in payload["series"])
    else:
        item_count = len(payload.get("frames", []))
    return {
        "block_index": block.block_index,
        "start": block.start,
        "end": block.end,
        "resolution": block.resolution,
        "item_count": item_count,
        "file": f"{prefix}{block.block_index:06d}.json",
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
        "product_dict": product_dict,
    }


def write_fixture(
    name: str,
    bounded: BoundedProduct,
    product_kind: str,
    operation: str,
    checks: dict[str, Any],
    result_digest: str,
    *,
    prefix: str = "block_",
) -> Path:
    target = FIXTURES / name
    target.mkdir(parents=True, exist_ok=True)
    for stale in target.glob("*.json"):
        stale.unlink()

    manifest_bytes = write_json(target / "manifest.json", bounded.to_dict())

    block_files: list[tuple[dict[str, Any], bytes]] = []
    for block in bounded.blocks:
        entry = block_entry(block, prefix)
        raw = json.dumps(entry["product_dict"], **DUMPER_KW).encode("utf-8")
        (target / entry["file"]).write_bytes(raw)
        block_files.append((entry, raw))

    expected_consumption: dict[str, Any] = {"operation": operation}
    if result_digest:
        expected_consumption["result_digest"] = result_digest

    index = build_index(
        bounded=bounded,
        product_kind=product_kind,
        manifest_bytes=manifest_bytes,
        block_files=block_files,
        delivery_state="ok",
        expected_consumption=expected_consumption,
    )
    write_json(target / "index.json", index)

    expected = {
        "fixture": name,
        "operation": operation,
        "result_digest": result_digest,
        "checks": checks,
        "expected_rejection": None,
    }
    write_json(target / "expected.json", expected)
    return target


def write_corrupt_fixture(normal: BoundedProduct) -> Path:
    """Valid-JSON-but-contract-invalid manifest + bogus-state + truncated block."""
    target = FIXTURES / "curve_scatter_corrupt"
    target.mkdir(parents=True, exist_ok=True)
    for stale in target.glob("*.json"):
        stale.unlink()

    manifest_dict = normal.to_dict()
    manifest_dict["schema_version"] = 99  # valid JSON, unsupported contract version
    manifest_bytes = write_json(target / "manifest.json", manifest_dict)

    entries = [block_entry(block, "block_") for block in normal.blocks[:2]]

    # block_000000: valid JSON, contract-invalid state enum
    bad_state = dict(entries[0]["product_dict"])
    bad_state["state"] = "bogus_state"
    raw0 = json.dumps(bad_state, **DUMPER_KW).encode("utf-8")
    (target / entries[0]["file"]).write_bytes(raw0)

    # block_000001: truncated bytes (not JSON at all)
    full1 = json.dumps(entries[1]["product_dict"], **DUMPER_KW).encode("utf-8")
    raw1 = full1[:300]
    (target / entries[1]["file"]).write_bytes(raw1)

    block_files = [(entries[0], raw0), (entries[1], raw1)]
    expected_rejection = {
        "manifest.json": "cccp.analysis.bounded.errors.BoundedSchemaError",
        entries[0]["file"]: "cccp.calculation.products.contracts.ProductSchemaError",
        entries[1]["file"]: "json.JSONDecodeError",
    }
    index = build_index(
        bounded=normal,
        product_kind="curve_scatter",
        manifest_bytes=manifest_bytes,
        block_files=block_files,
        delivery_state="corrupt",
        expected_consumption={"operation": "reject"},
        expected_rejection=expected_rejection,
    )
    # The declared item_count/identity come from the intended (pre-corruption)
    # layout; for a corrupt delivery the only accepted consumption is rejection.
    write_json(target / "index.json", index)

    expected = {
        "fixture": "curve_scatter_corrupt",
        "operation": "reject",
        "result_digest": None,
        "checks": {},
        "expected_rejection": expected_rejection,
    }
    write_json(target / "expected.json", expected)
    return target


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)

    # curve_scatter_normal
    normal = chunk_curve_product(curve_product(NORMAL_N), chunk_size=NORMAL_CHUNK)
    rebuilt = reconstruct_curve_series(normal)
    write_fixture(
        "curve_scatter_normal",
        normal,
        "curve_scatter",
        "reconstruct",
        curve_checks(normal, rebuilt.points),
        rebuild_digest_curve(rebuilt.points),
    )

    # curve_scatter_empty
    empty = chunk_curve_product(curve_product(0), chunk_size=EMPTY_CHUNK)
    write_fixture(
        "curve_scatter_empty",
        empty,
        "curve_scatter",
        "report_empty",
        empty_checks(empty),
        canonical_digest(empty_checks(empty)),
    )

    # curve_scatter_partial
    partial = chunk_curve_product(
        curve_product(PARTIAL_N, PARTIAL_MISSING, partial=True), chunk_size=PARTIAL_CHUNK
    )
    rebuilt_partial = reconstruct_curve_series(partial)
    write_fixture(
        "curve_scatter_partial",
        partial,
        "curve_scatter",
        "reconstruct",
        curve_checks(partial, rebuilt_partial.points),
        rebuild_digest_curve(rebuilt_partial.points),
    )

    # curve_scatter_corrupt (derived from the normal layout)
    write_corrupt_fixture(normal)

    # structure_trajectory_normal
    traj = chunk_trajectory_product(traj_product(), chunk_size=TRAJ_CHUNK)
    frames = reconstruct_structure_frames(traj)
    write_fixture(
        "structure_trajectory_normal",
        traj,
        "structure_trajectory",
        "reconstruct",
        traj_checks(traj, frames),
        rebuild_digest_traj(frames),
    )

    # structure_trajectory_partial (frame 4 missing)
    traj_partial = chunk_trajectory_product(traj_product(partial=True), chunk_size=TRAJ_CHUNK)
    frames_partial = reconstruct_structure_frames(traj_partial)
    write_fixture(
        "structure_trajectory_partial",
        traj_partial,
        "structure_trajectory",
        "reconstruct",
        traj_checks(traj_partial, frames_partial),
        rebuild_digest_traj(frames_partial),
    )

    total_bytes = sum(p.stat().st_size for p in sorted(FIXTURES.rglob("*.json")))
    files = sorted(FIXTURES.rglob("*.json"))
    print(f"RESULT fixtures_root={FIXTURES}")
    print(f"RESULT fixture_dirs={sum(1 for p in FIXTURES.iterdir() if p.is_dir())}")
    print(f"RESULT files={len(files)} total_bytes={total_bytes}")
    for path in files:
        raw = path.read_bytes()
        rel = path.relative_to(REPO)
        print(f"FILE {rel} bytes={len(raw)} {sha256_bytes(raw)}")


if __name__ == "__main__":
    main()
