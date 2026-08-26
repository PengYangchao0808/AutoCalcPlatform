"""PESsearch (S2) — potential-energy-surface search and candidate generation.

Consumes a Confsearch manifest (S1), runs one path strategy
(guided-scan / reverse-peb / direct-ts), and emits ``s2_path_manifest.json``
with TS and intermediate guesses. PESsearch only *discovers*: no TS
optimization, no frequency, no IRC, no final endpoint confirmation
(plan §6.2) — those belong to Lowconfirm/Highconfirm.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

import numpy as np

from acp.confsearch.manifest import read_manifest, representative_conformer
from acp.confsearch.shared.artifacts import write_json_atomic
from acp.confsearch.shared.provenance import source_artifact_ref, utc_now_iso
from cccp.qc.interfaces.constraints import ReactionCoordinatePlan
from cccp.utils.file_io import read_xyz, write_xyz

from .._helpers import fingerprint
from ..batch_models import build_tag_title
from ..models import ArtifactRef, StableState
from ..presets import PATH_STRATEGIES

logger = logging.getLogger(__name__)

S2_SCHEMA_VERSION = "s2_path_v1"
S2_MANIFEST_NAME = "s2_path_manifest.json"

_STRATEGY_WORKDIRS = {
    "guided-scan": ("07_PATH", "s2"),
    "rph-reverse": ("07_PATH", "s2_peb"),
    "direct-ts": ("07_PATH", "s2_direct"),
}


def normalize_strategy(strategy: str | None) -> str:
    """Accept the plan's strategy ids (§6.2), aliasing to the native ones.

    Declared-but-unsupported strategies (e.g. ``endpoint-path``) fall back
    to the default instead of leaking through.
    """
    value = str(strategy or "guided-scan").strip().lower()
    if value == "reverse-peb":
        return "rph-reverse"
    spec = PATH_STRATEGIES.get(value)
    if spec is not None and spec.supported:
        return value
    return _DEFAULT_STRATEGY


_DEFAULT_STRATEGY = "guided-scan"


def _state_from_xyz(
    state_id: str,
    role: Literal["reactant", "product", "intermediate"],
    xyz_path: Path,
    charge: int,
    multiplicity: int,
) -> StableState:
    coordinates, symbols = read_xyz(xyz_path)
    return StableState(
        state_id=state_id,
        role=role,
        canonical_geometry=ArtifactRef(
            path=str(xyz_path),
            sha256=fingerprint({"xyz": str(xyz_path)}),
            kind="input_geometry",
        ),
        charge=charge,
        multiplicity=multiplicity,
        identity_fingerprint=fingerprint(
            {
                "symbols": [str(symbol) for symbol in symbols],
                "charge": charge,
                "multiplicity": multiplicity,
            }
        ),
        metadata={
            "symbols": [str(symbol) for symbol in symbols],
            "coordinates": np.asarray(coordinates, dtype=float).tolist(),
            "charge": charge,
            "multiplicity": multiplicity,
        },
    )


def _build_path_strategy(strategy: str, config: dict[str, Any] | None, work_root: Path):
    if strategy == "rph-reverse":
        from ..providers.native_peb import NativeReversePebStrategy

        return NativeReversePebStrategy(config, work_root=work_root)
    if strategy == "guided-scan":
        from ..providers.guided_scan import GuidedScanPathStrategy

        return GuidedScanPathStrategy(config=config, work_root=work_root)
    from ..study_runner import DirectTsStrategy

    return DirectTsStrategy()


def _export_points(path_dir: Path, path_result: Any) -> dict[str, Any]:
    """Write ``path/points.xyz`` (multi-frame) + ``path_profile.json``."""
    path_dir.mkdir(parents=True, exist_ok=True)
    symbols: list[str] | None = None
    frames: list[str] = []
    profile: list[dict[str, Any]] = []
    for point in path_result.points:
        if point.geometry is None:
            continue
        if symbols is None:
            symbols = _symbols_for(path_result, point)
        if not symbols:
            continue
        title = f"{point.point_id} progress={point.progress:.4f} " + " ".join(
            f"{key}={value}" for key, value in point.energies_hartree.items()
        )
        lines = [
            f"{symbol} {row[0]:15.10f} {row[1]:15.10f} {row[2]:15.10f}"
            for symbol, row in zip(symbols, point.geometry)
        ]
        frames.append(f"{len(symbols)}\n{title}\n" + "\n".join(lines))
        profile.append(
            {
                "point_id": point.point_id,
                "progress": point.progress,
                "energies_hartree": dict(point.energies_hartree),
                "topology_valid": point.topology_valid,
                "frame_index": point.frame_index,
            }
        )
    profile_path = write_json_atomic(
        path_dir / "path_profile.json", {"n_points": len(profile), "points": profile}
    )
    if frames:
        (path_dir / "points.xyz").write_text("\n".join(frames) + "\n", encoding="utf-8")
    return {
        "complete": bool(path_result.complete),
        "n_points": len(profile),
        "points_xyz": "path/points.xyz",
        "profile": "path/path_profile.json",
        "profile_path": str(profile_path),
    }


def _symbols_for(path_result: Any, point: Any) -> list[str]:
    for attr in ("symbols", "atom_symbols"):
        value = getattr(path_result, attr, None)
        if value:
            return [str(symbol) for symbol in value]
    return list(getattr(point, "symbols", None) or [])


def _export_candidates(
    result_dir: Path,
    path_result: Any,
    symbols_fallback: list[str],
) -> list[dict[str, Any]]:
    """Materialize ts_guess_NNN / int_guess_NNN xyz files; return manifest rows."""
    ts_dir = result_dir / "ts_guesses"
    int_dir = result_dir / "intermediate_guesses"
    exported: list[dict[str, Any]] = []
    counters = {"ts_seed": 0, "intermediate_seed": 0}

    seeds = list(path_result.seed_candidates or [])
    if not seeds:
        seeds = _fallback_seeds(path_result)

    for seed in sorted(seeds, key=lambda s: (s.kind != "ts_seed", s.rank)):
        kind = str(seed.kind)
        base = "ts_guess" if kind == "ts_seed" else "int_guess"
        directory = ts_dir if kind == "ts_seed" else int_dir
        rel_dir = "ts_guesses" if kind == "ts_seed" else "intermediate_guesses"
        counters[kind] += 1
        candidate_id = f"{base}_{counters[kind]:03d}"
        geometry, symbols = _seed_geometry(seed, path_result, symbols_fallback)
        if geometry is None or not symbols:
            logger.warning("Seed %s has no exportable geometry — skipped", seed.id)
            counters[kind] -= 1
            continue
        directory.mkdir(parents=True, exist_ok=True)
        xyz_path = directory / f"{candidate_id}.xyz"
        tag = "TS" if kind == "ts_seed" else "INT"
        write_xyz(
            xyz_path,
            np.asarray(geometry, dtype=float),
            [str(symbol) for symbol in symbols],
            title=build_tag_title(
                tag,
                candidate_id=candidate_id,
                source="PESsearch",
                extra=f"seed={seed.id}",
            ),
        )
        exported.append(
            {
                "id": candidate_id,
                "kind": kind,
                "tag": tag,
                "xyz": f"{rel_dir}/{candidate_id}.xyz",
                "rank": counters[kind],
                "confidence": str(seed.confidence),
                "source_seed_id": str(seed.id),
                "selection_mode": str(seed.selection_mode),
                "evidence": dict(seed.evidence),
                "geometry_abs": str(xyz_path),
            }
        )
    return exported


def _fallback_seeds(path_result: Any) -> list[Any]:
    """Derive seeds from PathResult.candidates when seed_candidates is empty."""
    from ..engines.elementary_step import replace_seed_candidate

    seeds: list[Any] = []
    for candidate in path_result.candidates or []:
        if getattr(candidate, "kind", "") not in ("ts_seed", "intermediate_seed"):
            continue
        point = path_result.point_by_id(candidate.point_id)
        if point is None or point.geometry is None:
            continue
        artifact = ArtifactRef(path=f"point://{candidate.point_id}", sha256="", kind="path_point")
        seeds.append(replace_seed_candidate(candidate.candidate_id, artifact, point))
    return seeds


def _seed_geometry(
    seed: Any,
    path_result: Any,
    symbols_fallback: list[str],
) -> tuple[list[list[float]] | None, list[str]]:
    path_str = str(seed.geometry.path)
    point_id = str(seed.evidence.get("point_id") or "")
    if path_str.endswith(".xyz") and Path(path_str).is_file():
        coordinates, symbols = read_xyz(Path(path_str))
        return coordinates.tolist(), [str(symbol) for symbol in symbols]
    point = path_result.point_by_id(point_id) if point_id else None
    if point is not None and point.geometry is not None:
        return np.asarray(point.geometry, dtype=float).tolist(), symbols_fallback
    return None, symbols_fallback


def run_pes_search(
    *,
    from_manifest: Path | str,
    output_dir: Path | str,
    strategy: str = "guided-scan",
    coordinate_plan: dict[str, Any] | None = None,
    product_source: str | None = None,
    product_manifest: Path | str | None = None,
    product_conf: str | None = None,
    reactant_conf: str | None = None,
    ts_guess: str | None = None,
    source_job_id: str | None = None,
    source_relative_path: str = "RESULT/confsearch/confsearch_manifest.json",
    charge: int | None = None,
    multiplicity: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the S2 path search; write ``RESULT/mechanism/s2_path_manifest.json``.

    Returns:
        The manifest payload dict (also persisted to disk).

    Raises:
        ValueError: On missing prerequisites (plan, product for reverse-peb).
        RuntimeError: On path-strategy execution failure.
    """
    manifest_path = Path(from_manifest).resolve()
    payload_in = read_manifest(manifest_path)
    resolved_charge = (
        charge if charge is not None else int((payload_in.get("input") or {}).get("charge") or 0)
    )
    resolved_multiplicity = (
        multiplicity
        if multiplicity is not None
        else int((payload_in.get("input") or {}).get("multiplicity") or 1)
    )

    strategy = normalize_strategy(strategy)
    spec = PATH_STRATEGIES.get(strategy)
    requires_product = bool(spec and spec.requires_product)
    if strategy == "direct-ts" and not ts_guess:
        raise ValueError("direct-ts strategy requires --ts-guess")

    out_root = Path(output_dir).resolve()
    work_root = out_root / "WORK"
    result_dir = out_root / "RESULT" / "mechanism"
    result_dir.mkdir(parents=True, exist_ok=True)
    inputs_dir = work_root / "01_PREPARE" / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    _, reactant_xyz = representative_conformer(manifest_path, reactant_conf)
    reactant_local = inputs_dir / "reactant.xyz"
    reactant_local.write_text(reactant_xyz.read_text(encoding="utf-8"), encoding="utf-8")

    product_local: Path | None = None
    if product_manifest:
        _, product_xyz = representative_conformer(Path(product_manifest), product_conf)
        product_local = inputs_dir / "product.xyz"
        product_local.write_text(product_xyz.read_text(encoding="utf-8"), encoding="utf-8")
    elif product_source:
        product_src = Path(product_source)
        if not product_src.is_file():
            raise ValueError(f"Product structure not found: {product_source}")
        product_local = inputs_dir / "product.xyz"
        product_local.write_text(product_src.read_text(encoding="utf-8"), encoding="utf-8")
    if requires_product and product_local is None:
        raise ValueError(f"Strategy {strategy!r} requires a product structure")

    if strategy == "direct-ts":
        assert ts_guess is not None
        guess_src = Path(ts_guess)  # type: ignore[arg-type]
        if not guess_src.is_file():
            raise ValueError(f"TS guess not found: {ts_guess}")
        guess_local = inputs_dir / "ts_guess.xyz"
        guess_local.write_text(guess_src.read_text(encoding="utf-8"), encoding="utf-8")

    reactant_state = _state_from_xyz(
        "state_reactant", "reactant", reactant_local, resolved_charge, resolved_multiplicity
    )
    product_state = (
        _state_from_xyz(
            "state_product",
            "product",
            product_local,
            resolved_charge,
            resolved_multiplicity,
        )
        if product_local is not None
        else None
    )

    plan = ReactionCoordinatePlan.from_dict(coordinate_plan or {})

    stage_parts = _STRATEGY_WORKDIRS[strategy]
    strategy_root = work_root.joinpath(*stage_parts)
    strategy_root.mkdir(parents=True, exist_ok=True)
    path_strategy = _build_path_strategy(strategy, config, strategy_root)

    logger.info("PESsearch: strategy=%s source=%s", strategy, reactant_state.state_id)
    path_result = path_strategy.search(reactant_state, product_state, plan, "censo-lite")

    path_block = _export_points(result_dir / "path", path_result)
    first_point = path_result.points[0] if path_result.points else None
    symbols_fallback = _symbols_for(path_result, first_point)
    candidates = _export_candidates(result_dir, path_result, symbols_fallback)
    ts_candidates = [c for c in candidates if c["kind"] == "ts_seed"]

    gates: dict[str, Any] = {
        "path_complete": bool(path_result.complete),
        "coordinates_valid": bool(plan.coordinates),
        "at_least_one_candidate": bool(candidates),
        "candidates_traceable": all(c.get("source_seed_id") for c in candidates),
    }
    gates["G2"] = "PASS" if all(gates.values()) else "FAIL"

    for row in candidates:
        row.pop("geometry_abs", None)

    payload = {
        "schema_version": S2_SCHEMA_VERSION,
        "workflow": "PESsearch",
        "stage": "S2",
        "created_at": utc_now_iso(),
        "source": source_artifact_ref(
            source_job_id,
            source_relative_path,
            manifest_path,
            kind="confsearch_manifest",
            stage="S1",
        ),
        "strategy": strategy,
        "charge": resolved_charge,
        "multiplicity": resolved_multiplicity,
        "route": {
            "route_id": "route_001",
            "coordinate_plan": _serialize_plan(plan),
            "reactant_state_id": reactant_state.state_id,
            "product_state_id": product_state.state_id if product_state else None,
        },
        "path": path_block,
        "candidates": candidates,
        "gates": gates,
        "provenance": {
            "engine": "acp-pessearch",
            "strategy": strategy,
            "provider": type(path_strategy).__name__,
            "fingerprint": fingerprint(
                {
                    "strategy": strategy,
                    "plan": _serialize_plan(plan),
                    "source_manifest": str(manifest_path),
                    "charge": resolved_charge,
                }
            ),
        },
    }
    if not ts_candidates:
        payload["warning"] = "No TS seed candidate was extracted from the path"
    manifest_out = write_json_atomic(result_dir / S2_MANIFEST_NAME, payload)
    _register_s2_result_products(out_root, payload, candidates)
    logger.info("PESsearch manifest written: %s (%d candidates)", manifest_out, len(candidates))
    return payload


def _register_s2_result_products(
    out_root: Path,
    payload: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    """Register S2 candidate structures as standard result products.

    Every exported TS/INT guess becomes a ``kind: "structure"`` product in
    ``RESULT/result_manifest.json`` (plus a legacy ``result_summary.json``
    pointer) so the task-result list can offer PESsearch candidates for
    downstream batch confirmation (batch plan §5/§6).
    """
    from acp.storage.manifest import ResultManifest
    from acp.workflows._helpers import write_result_summary

    if not candidates:
        return
    result_root = out_root / "RESULT"
    result_manifest = ResultManifest(
        task_id=str(payload.get("provenance", {}).get("job_id") or ""),
        workflow="PESsearch",
        status="completed",
    )
    summary_products: list[dict[str, Any]] = []
    for row in candidates:
        rel = str(row.get("xyz") or "")
        if not rel:
            continue
        candidate_id = str(row.get("id") or Path(rel).stem)
        tag = "TS" if str(row.get("kind")) == "ts_seed" else "INT"
        result_manifest.add_product(
            f"s2_candidate_{candidate_id}",
            f"S2 candidate {candidate_id} ({tag})",
            f"mechanism/{rel}",
            "structure",
        )
        summary_products.append(
            {
                "label": f"{candidate_id} ({tag})",
                "path": f"mechanism/{rel}",
                "kind": "xyz",
                "role": "final_stable_structure",
            }
        )
    result_manifest.add_product(
        "s2_path_manifest",
        "S2 path manifest",
        "mechanism/s2_path_manifest.json",
        "file",
    )
    result_manifest.write(result_root)
    write_result_summary(result_root, "PESsearch", summary_products)


def _serialize_plan(plan: ReactionCoordinatePlan) -> dict[str, Any]:
    return {
        "coordinates": [
            {
                "id": spec.id,
                "kind": spec.kind,
                "atoms": list(spec.atoms),
                "role": spec.role,
                "start": spec.start,
                "end": spec.end,
            }
            for spec in plan.coordinates
        ],
        "points": plan.points,
        "coupling": plan.coupling,
        "start_from": plan.start_from,
    }


__all__ = ["S2_MANIFEST_NAME", "S2_SCHEMA_VERSION", "normalize_strategy", "run_pes_search"]
