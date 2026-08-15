# pyright: reportAny=false, reportExplicitAny=false, reportUnannotatedClassAttribute=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnusedImport=false, reportUnusedParameter=false
"""Guided-scan path strategy for the mechanism study layer."""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from acp.backends.base import to_qc_result
from acp.mechanism._helpers import backend_name as _backend_name
from acp.mechanism._helpers import resolve_backend as _resolve_backend
from acp.mechanism._helpers import state_geometry as _state_geometry
from acp.mechanism.candidates import select_candidates, select_primary_int, select_primary_ts
from acp.mechanism.models import (
    ArtifactRef,
    PathPoint,
    PathResult,
    Provenance,
    SeedCandidate,
    StableState,
)
from cccp.config import load_config
from cccp.qc.interfaces.constraints import ReactionCoordinatePlan
from cccp.utils.file_io import write_xyz

logger = logging.getLogger(__name__)

_SCAN_ENERGY_KEY = "gfn2-xtb"
_SP_ENERGY_KEY = "sp_refined"
_TS_CAP = 3
_INT_CAP = 2
_STRATEGY_VERSION = "1.0"


class GuidedScanPathStrategy:
    """Contract-first guided-scan implementation for S2 path discovery."""

    def __init__(
        self,
        scan_backend: str | Any = "xtb",
        sp_backend: str | Any = "orca",
        *,
        config: dict[str, Any] | None = None,
        work_root: Path | str | None = None,
        sp_refinement: bool = False,
        energy_key: str = _SCAN_ENERGY_KEY,
        sp_energy_key: str = _SP_ENERGY_KEY,
        ts_cap: int = _TS_CAP,
        int_cap: int = _INT_CAP,
    ) -> None:
        self._scan_backend_spec = scan_backend
        self._sp_backend_spec = sp_backend
        self.config = dict(config) if config is not None else load_config()
        self.work_root = (
            Path(work_root)
            if work_root is not None
            else Path(tempfile.gettempdir()) / "acp_mechanism_guided_scan"
        )
        self.sp_refinement = sp_refinement
        self.energy_key = energy_key
        self.sp_energy_key = sp_energy_key
        self.ts_cap = ts_cap
        self.int_cap = int_cap
        self.calls = 0
        self._scan_backend_instance: Any | None = None
        self._sp_backend_instance: Any | None = None

    def search(
        self,
        source_state: StableState,
        target_state: StableState | None,
        coordinate_plan: ReactionCoordinatePlan,
        profile: Any,
    ) -> PathResult:
        """Run a multi-coordinate relaxed scan and emit Top-N seed candidates."""
        if not coordinate_plan.drive_coordinates():
            raise ValueError("guided-scan requires at least one drive coordinate")

        self.calls += 1
        route_id = _route_id(source_state, target_state)
        run_dir = self.work_root / f"{route_id}__scan_{self.calls:03d}"
        run_dir.mkdir(parents=True, exist_ok=True)

        coordinates, symbols = _state_geometry(source_state)
        provenance = _build_provenance(
            source_state=source_state,
            target_state=target_state,
            coordinate_plan=coordinate_plan,
            profile=profile,
            provider_name=_backend_name(self._scan_backend_spec),
        )

        scan_backend = self._scan_backend()
        scan = scan_backend.relaxed_scan(
            coordinates,
            symbols,
            run_dir,
            coordinate_plan,
            charge=source_state.charge,
            multiplicity=source_state.multiplicity,
            opt_level="normal",
            fail_fast=True,
        )

        points = _scan_points_to_path_points(scan, self.energy_key, provenance)
        sp_metadata = {
            "enabled": self.sp_refinement,
            "method": _profile_value(profile, "sp_method", "B97-3c"),
            "basis": _profile_value(profile, "sp_basis", ""),
            "successful_frames": 0,
            "failed_frames": [],
        }
        selection_energy_key = self.energy_key

        if self.sp_refinement:
            successful_frames, failed_frames = self._refine_points_single_point(
                points=points,
                symbols=symbols,
                charge=source_state.charge,
                multiplicity=source_state.multiplicity,
                run_dir=run_dir,
                profile=profile,
            )
            sp_metadata["successful_frames"] = successful_frames
            sp_metadata["failed_frames"] = failed_frames
            if _has_complete_energy_profile(points, self.sp_energy_key):
                selection_energy_key = self.sp_energy_key
            else:
                sp_metadata["selection_fallback"] = self.energy_key

        candidates = select_candidates(
            points,
            energy_key=selection_energy_key,
            ts_cap=self.ts_cap,
            int_cap=self.int_cap,
        )
        seed_candidates = self._build_seed_candidates(
            candidates=candidates,
            points=points,
            symbols=symbols,
            run_dir=run_dir,
            selection_energy_key=selection_energy_key,
        )

        result = PathResult(
            points=points,
            candidates=candidates,
            strategy="guided-scan",
            route_id=route_id,
            metadata={
                "energy_key": self.energy_key,
                "selection_energy_key": selection_energy_key,
                "scan_success": bool(getattr(scan, "success", False)),
                "scan_message": str(getattr(scan, "message", "") or ""),
                "points": len(points),
                "sp_refinement": sp_metadata,
                "gate_policies": {
                    "G2": {
                        "require_complete": True,
                        "min_points": coordinate_plan.points,
                        "min_seed_candidates": 1,
                        "selection_caps": {"ts": self.ts_cap, "intermediate": self.int_cap},
                    }
                },
            },
            seed_candidates=seed_candidates,
            strategy_id="guided-scan",
            strategy_version=_STRATEGY_VERSION,
            complete=(
                bool(getattr(scan, "success", False)) and len(points) == coordinate_plan.points
            ),
            endpoint_evidence={
                "source_state_id": source_state.state_id,
                "target_state_id": target_state.state_id if target_state is not None else None,
            },
            artifacts={"scan_dir": str(run_dir)},
        )
        primary_ts = select_primary_ts(result)
        primary_int = select_primary_int(result)
        if primary_ts is not None:
            result.selected_ts_id = primary_ts.candidate_id
        if primary_int is not None:
            result.selected_int_id = primary_int.candidate_id
        return result

    def _scan_backend(self) -> Any:
        if self._scan_backend_instance is None:
            self._scan_backend_instance = _resolve_backend(self._scan_backend_spec, self.config)
        return self._scan_backend_instance

    def _sp_backend(self) -> Any:
        if self._sp_backend_instance is None:
            self._sp_backend_instance = _resolve_backend(self._sp_backend_spec, self.config)
        return self._sp_backend_instance

    def _refine_points_single_point(
        self,
        *,
        points: list[PathPoint],
        symbols: list[str],
        charge: int,
        multiplicity: int,
        run_dir: Path,
        profile: Any,
    ) -> tuple[int, list[int]]:
        sp_backend = self._sp_backend()
        method = _profile_value(profile, "sp_method", "B97-3c")
        basis = _profile_value(profile, "sp_basis", "")
        successful_frames = 0
        failed_frames: list[int] = []

        for point in points:
            if point.geometry is None:
                failed_frames.append(point.frame_index if point.frame_index is not None else -1)
                continue
            sp_dir = run_dir / "sp" / point.point_id
            sp_dir.mkdir(parents=True, exist_ok=True)
            sp_result = to_qc_result(
                sp_backend.single_point(
                    np.asarray(point.geometry, dtype=float),
                    symbols,
                    charge=charge,
                    multiplicity=multiplicity,
                    output_dir=sp_dir,
                    method=method,
                    basis=basis,
                )
            )
            if sp_result.success and sp_result.energy is not None:
                point.energies_hartree[self.sp_energy_key] = float(sp_result.energy)
                successful_frames += 1
            else:
                failed_frames.append(point.frame_index if point.frame_index is not None else -1)
        return successful_frames, failed_frames

    def _build_seed_candidates(
        self,
        *,
        candidates: list[Any],
        points: list[PathPoint],
        symbols: list[str],
        run_dir: Path,
        selection_energy_key: str,
    ) -> list[SeedCandidate]:
        point_index = {point.point_id: point for point in points}
        seed_candidates: list[SeedCandidate] = []
        ts_rank = 0
        int_rank = 0
        for candidate in candidates:
            if candidate.kind == "endpoint":
                continue
            point = point_index.get(candidate.point_id)
            if point is None or point.geometry is None:
                continue
            if candidate.kind == "ts_seed":
                ts_rank += 1
                rank = ts_rank
                score_key = "prominence"
            else:
                int_rank += 1
                rank = int_rank
                score_key = "depth"
            artifact = _materialize_seed_geometry(
                run_dir=run_dir,
                candidate_id=candidate.candidate_id,
                point_id=point.point_id,
                coordinates=np.asarray(point.geometry, dtype=float),
                symbols=symbols,
                kind=(
                    "ts_seed_geometry"
                    if candidate.kind == "ts_seed"
                    else "intermediate_seed_geometry"
                ),
            )
            seed_candidates.append(
                SeedCandidate(
                    id=candidate.candidate_id,
                    kind=candidate.kind,
                    geometry=artifact,
                    rank=rank,
                    selection_mode="local_max_prominence",
                    confidence=("high" if rank == 1 else "medium"),
                    evidence={
                        "point_id": point.point_id,
                        "progress": point.progress,
                        "reason": candidate.reason,
                        score_key: float(candidate.score or 0.0),
                        "selection_energy_key": selection_energy_key,
                        "selected_energy_hartree": point.energies_hartree.get(selection_energy_key),
                    },
                    stationary_point_claimed=False,
                )
            )
        return seed_candidates


def _route_id(source_state: StableState, target_state: StableState | None) -> str:
    route_id = str(source_state.metadata.get("route_id") or "")
    if route_id:
        return route_id
    if target_state is not None:
        return f"{source_state.state_id}__{target_state.state_id}"
    return f"{source_state.state_id}__guided_scan"


def _scan_points_to_path_points(
    scan: Any,
    energy_key: str,
    provenance: Provenance,
) -> list[PathPoint]:
    path_points: list[PathPoint] = []
    for frame in getattr(scan, "points", []):
        path_points.append(
            PathPoint(
                point_id=f"p{frame.frame_index:03d}",
                progress=float(frame.progress),
                coordinate_values=dict(frame.coordinate_values),
                reaction_coordinates=dict(frame.coordinate_values),
                geometry=(
                    np.asarray(frame.coordinates, dtype=float)
                    if frame.coordinates is not None
                    else None
                ),
                energies_hartree=(
                    {energy_key: float(frame.energy_hartree)}
                    if frame.energy_hartree is not None
                    else {}
                ),
                frame_index=int(frame.frame_index),
                topology_valid=bool(frame.success),
                diagnostics={"frame_success": bool(frame.success)},
                provenance=provenance,
            )
        )
    return path_points


def _build_provenance(
    *,
    source_state: StableState,
    target_state: StableState | None,
    coordinate_plan: ReactionCoordinatePlan,
    profile: Any,
    provider_name: str,
) -> Provenance:
    return Provenance(
        provider=provider_name,
        provider_version="unknown",
        provider_commit="",
        strategy="guided-scan",
        strategy_version=_STRATEGY_VERSION,
        profile_id=_profile_id(profile),
        schema_version="m2",
        input_signature=_sha_payload(
            {
                "source_state_id": source_state.state_id,
                "target_state_id": target_state.state_id if target_state is not None else None,
                "coordinate_plan": {
                    "coordinates": [
                        {
                            "id": spec.id,
                            "kind": spec.kind,
                            "atoms": list(spec.atoms),
                            "role": spec.role,
                            "start": spec.start,
                            "end": spec.end,
                        }
                        for spec in coordinate_plan.coordinates
                    ],
                    "points": coordinate_plan.points,
                },
                "profile": _profile_id(profile),
            }
        ),
    )


def _profile_id(profile: Any) -> str:
    if isinstance(profile, dict):
        return str(profile.get("name") or profile)
    return str(getattr(profile, "name", profile))


def _profile_value(profile: Any, key: str, default: str) -> str:
    if isinstance(profile, dict):
        value = profile.get(key)
    else:
        value = getattr(profile, key, None)
    if value in (None, ""):
        return default
    return str(value)


def _has_complete_energy_profile(points: list[PathPoint], energy_key: str) -> bool:
    return all(point.energies_hartree.get(energy_key) is not None for point in points)


def _materialize_seed_geometry(
    *,
    run_dir: Path,
    candidate_id: str,
    point_id: str,
    coordinates: NDArray[np.float64],
    symbols: list[str],
    kind: str,
) -> ArtifactRef:
    seed_dir = run_dir / "seeds"
    seed_dir.mkdir(parents=True, exist_ok=True)
    seed_path = seed_dir / f"{candidate_id}.xyz"
    write_xyz(seed_path, coordinates, symbols, title=f"{candidate_id} from {point_id}")
    payload = {
        "candidate_id": candidate_id,
        "point_id": point_id,
        "symbols": symbols,
        "coordinates": np.asarray(coordinates, dtype=float).tolist(),
        "kind": kind,
    }
    return ArtifactRef(path=str(seed_path), sha256=_sha_payload(payload), kind=kind)


def _sha_payload(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


__all__ = ["GuidedScanPathStrategy"]
