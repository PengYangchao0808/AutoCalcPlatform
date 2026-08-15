"""Native reverse-PEB path-search strategy for mechanism S2."""

# pyright: reportAny=false, reportArgumentType=false, reportExplicitAny=false, reportImplicitOverride=false, reportMissingTypeArgument=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnannotatedClassAttribute=false, reportUnusedImport=false

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import numpy as np

from acp.backends.orca import ORCABackend
from acp.backends.xtb import XTBBackend
from acp.mechanism._helpers import state_geometry, write_json_atomic
from acp.mechanism.models import (
    ArtifactRef,
    PathCandidate,
    PathPoint,
    PathResult,
    Provenance,
    SeedCandidate,
    StableState,
)
from cccp.config import load_config
from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan
from cccp.qc.interfaces.orca import ORCAInterface
from cccp.qc.interfaces.xtb_path import XTBPathInterface
from cccp.utils.file_io import read_xyz, write_xyz
from cccp.utils.geometry_tools import GeometryUtils

from ..primitives import (
    ScanEnergyRefiner,
    SinglePointSpec,
    build_xtb_path_profile,
    check_scan_trajectory,
    policy_from_config,
    select_path_seeds,
)
from .contracts import PathSearchStrategy

logger = logging.getLogger(__name__)

NATIVE_PROVIDER_NAME = "acp-native-peb"
NATIVE_STRATEGY_ID = "rph-reverse"
NATIVE_STRATEGY_VERSION = "1.0"
NATIVE_SCHEMA_VERSION = "acp_native_reverse_peb_v1"
RPH_SELECTION_ALGORITHM = "endpoint_knee_shift_midpoint_v1"
DEFAULT_COARSE_STEP_A = 0.20
DEFAULT_STRETCH_END_A = 3.40
DEFAULT_PATH_NPOINT = 25
DEFAULT_PATH_ANOPT = 10
DEFAULT_PERSISTENT_DRIFT_POINTS = 2


class NativeReversePebStrategy(PathSearchStrategy):
    """Native ACP implementation of the RPH reverse-PEB S2 chain."""

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        work_root: Path | str | None = None,
    ) -> None:
        self.config = dict(config) if config is not None else load_config()
        self.work_root = (
            Path(work_root)
            if work_root is not None
            else Path(tempfile.gettempdir()) / "acp_mechanism_native_peb"
        )

    def search(
        self,
        source_state: StableState,
        target_state: StableState | None,
        coordinate_plan: ReactionCoordinatePlan,
        profile: Any,
    ) -> PathResult:
        if target_state is None:
            raise ValueError("RPH reverse path search requires a target/product state")

        forming_bonds = _forming_bonds_from_plan(coordinate_plan)
        if not forming_bonds:
            raise ValueError("RPH reverse path search requires at least one distance coordinate")

        run_dir = self.work_root / "s2" / f"{source_state.state_id}__{target_state.state_id}"
        run_dir.mkdir(parents=True, exist_ok=True)

        product_symbols, product_coordinates = _state_symbols_coordinates(target_state)
        product_xyz = _resolve_geometry_file(target_state, run_dir / "product.xyz")
        route_id = _route_id(source_state, target_state)
        provenance = _build_provenance(
            source_state=source_state,
            target_state=target_state,
            coordinate_plan=coordinate_plan,
            profile=profile,
        )

        scan_cfg = _nested_mapping(self.config, "step2", "scan")
        path_cfg = _nested_mapping(self.config, "step2", "xtb_path")
        rescue_cfg = _nested_mapping(self.config, "step2", "rescue", "relaxed_scan")

        coarse_step = _positive_float(scan_cfg.get("coarse_step_A"), DEFAULT_COARSE_STEP_A)
        stretch_default = _positive_float(scan_cfg.get("stretch_end_A"), DEFAULT_STRETCH_END_A)
        persistent_points = max(
            1,
            _positive_int_default(
                _nested_mapping(self.config, "step2", "scan", "anchor_detection").get(
                    "persistent_drift_points"
                ),
                DEFAULT_PERSISTENT_DRIFT_POINTS,
            ),
        )

        scan_coordinate, points, product_distance, stretch_target = _coarse_scan_coordinate(
            coordinate_plan=coordinate_plan,
            product_coordinates=product_coordinates,
            coarse_step=coarse_step,
            default_stretch_end=stretch_default,
            configured_points=(
                _positive_int(scan_cfg.get("points"), None)
                or _positive_int(scan_cfg.get("scan_steps"), None)
            ),
        )

        coarse_scan_dir = run_dir / "coarse_scan"
        coarse_scan = ORCAInterface(config=self.config).relaxed_scan(
            product_coordinates,
            product_symbols,
            scan_coordinate,
            points,
            charge=target_state.charge,
            multiplicity=target_state.multiplicity,
            output_dir=coarse_scan_dir,
            output_name="coarse_reverse_peb",
            method="GFN2-xTB",
            solvent=_profile_solvent(profile),
            solvent_model=_profile_solvent_model(profile),
        )
        if not coarse_scan.points:
            raise RuntimeError(
                coarse_scan.message or "Native reverse PEB coarse scan produced no frames"
            )

        coarse_frame_paths = [
            coarse_scan.scan_dir / "scan_frames" / f"frame_{point.frame_index:03d}.xyz"
            for point in coarse_scan.points
        ]
        coarse_guard = check_scan_trajectory(
            product_coords=np.asarray(product_coordinates, dtype=float),
            symbols=list(product_symbols),
            forming_bonds=forming_bonds,
            frame_paths=coarse_frame_paths,
        )
        anchor_index, persistent_off_path_start = _anchor_index(
            off_path_indices=cast(Sequence[int], coarse_guard.get("off_path_indices") or []),
            frame_count=len(coarse_frame_paths),
            persistent_points=persistent_points,
        )
        anchor_xyz = coarse_frame_paths[anchor_index]

        path_dir = run_dir / "xtb_path"
        path_result = XTBPathInterface(config=self.config).path_search(
            start_xyz=anchor_xyz,
            end_xyz=product_xyz,
            output_dir=path_dir,
            npoint=_positive_int_default(path_cfg.get("npoint"), DEFAULT_PATH_NPOINT),
            anopt=_positive_int_default(path_cfg.get("anopt"), DEFAULT_PATH_ANOPT),
            charge=target_state.charge,
            multiplicity=target_state.multiplicity,
            solvent=_profile_solvent(profile),
        )
        if not path_result.success or not path_result.frame_paths:
            raise RuntimeError(
                path_result.error_message or "Native reverse PEB xTB PATH produced no frames"
            )

        oriented_frame_paths = _orient_product_to_stretch(
            frame_paths=path_result.frame_paths,
            forming_bonds=forming_bonds,
        )

        xtb_backend = XTBBackend(self.config)
        xtb_energies = _single_point_energies_from_frames(
            backend=xtb_backend,
            frame_paths=oriented_frame_paths,
            charge=target_state.charge,
            multiplicity=target_state.multiplicity,
            output_dir=run_dir / "xtb_frame_sp",
        )

        point_ids = [f"p{index:03d}" for index in range(len(oriented_frame_paths))]
        orca_backend = ORCABackend(self.config)
        refiner = ScanEnergyRefiner(
            run_dir / "b97_sp",
            sp_callable=_b97_sp_callable(
                backend=orca_backend,
                charge=target_state.charge,
                multiplicity=target_state.multiplicity,
                profile=profile,
                output_dir=run_dir / "b97_sp_jobs",
            ),
            spec=SinglePointSpec(
                engine="orca",
                task="sp",
                method="B97-3c",
                basis="",
                solvent=str(_profile_solvent(profile) or ""),
                solvent_model=_profile_solvent_model(profile),
                charge=target_state.charge,
                multiplicity=target_state.multiplicity,
                nproc=_positive_int_default(
                    _nested_mapping(self.config, "resources").get("nproc"),
                    1,
                ),
            ),
            parallel_jobs=1,
        )
        b97_refinement = refiner.refine(oriented_frame_paths, point_ids=point_ids)
        b97_energies = [
            None if value is None else float(value)
            for value in cast(Sequence[float | None], b97_refinement.get("energies_hartree") or [])
        ]
        if len(b97_energies) < len(oriented_frame_paths):
            b97_energies.extend([None] * (len(oriented_frame_paths) - len(b97_energies)))

        path_guard = check_scan_trajectory(
            product_coords=np.asarray(product_coordinates, dtype=float),
            symbols=list(product_symbols),
            forming_bonds=forming_bonds,
            frame_paths=oriented_frame_paths,
        )
        off_path_indices = cast(Sequence[int], path_guard.get("off_path_indices") or [])
        path_profile = build_xtb_path_profile(
            frame_paths=oriented_frame_paths,
            energies_hartree=xtb_energies,
            forming_bonds=forming_bonds,
            product_xyz=product_xyz,
            off_path_indices=list(off_path_indices),
            source_provenance={
                "selection_source": "xtb_path",
                "generation_method": "native_reverse_peb",
                "anchor_frame_index": anchor_index,
                "anchor_xyz": str(anchor_xyz),
                "coarse_product_distance_A": product_distance,
                "coarse_stretch_target_A": stretch_target,
                "persistent_off_path_start": persistent_off_path_start,
            },
        )

        selection_policy = policy_from_config(
            _nested_mapping(self.config, "step2", "scan", "selection"),
            rescue_cfg,
        )
        selection = select_path_seeds(path_profile, selection_policy)
        points_by_frame = _path_points_from_profile(
            path_profile=path_profile,
            coordinate_plan=coordinate_plan,
            provenance=provenance,
            xtb_energies=xtb_energies,
            b97_energies=b97_energies,
        )
        point_ids_by_frame = {
            int(point.frame_index or 0): point.point_id for point in points_by_frame
        }
        progress_by_frame = {
            int(point.frame_index or 0): float(point.progress) for point in points_by_frame
        }
        seed_candidates = _seed_selection_to_seed_candidates(
            selection,
            point_ids_by_frame=point_ids_by_frame,
        )
        path_candidates = _selection_to_path_candidates(
            selection,
            point_ids_by_frame=point_ids_by_frame,
            progress_by_frame=progress_by_frame,
        )

        gate_policy = _g2_policy(
            path_profile=path_profile, b97_energies=b97_energies, selection=selection
        )
        geometry_sha256_by_point = {
            point.point_id: _file_sha256(
                Path(str(path_profile.frames[int(point.frame_index or 0)].xyz))
            )
            for point in points_by_frame
        }
        selected_ts_id = next(
            (
                candidate.candidate_id
                for candidate in path_candidates
                if candidate.kind == "ts_seed"
            ),
            None,
        )
        selected_int_id = next(
            (
                candidate.candidate_id
                for candidate in path_candidates
                if candidate.kind == "intermediate_seed"
            ),
            None,
        )

        scan_profile_path = run_dir / "scan_profile.json"
        profile_payload = _scan_profile_payload(
            product_xyz=product_xyz,
            forming_bonds=forming_bonds,
            path_profile=path_profile,
            xtb_energies=xtb_energies,
            b97_energies=b97_energies,
            selection=selection,
            coarse_scan=coarse_scan,
            coarse_guard=coarse_guard,
            path_guard=path_guard,
            anchor_index=anchor_index,
            anchor_xyz=anchor_xyz,
            route_id=route_id,
            persistent_off_path_start=persistent_off_path_start,
        )
        write_json_atomic(scan_profile_path, profile_payload)

        return PathResult(
            points=points_by_frame,
            candidates=path_candidates,
            strategy=NATIVE_STRATEGY_ID,
            route_id=route_id,
            selected_ts_id=selected_ts_id,
            selected_int_id=selected_int_id,
            metadata={
                "provider": NATIVE_PROVIDER_NAME,
                "selection_source": "xtb_path",
                "selection_diagnostics": copy.deepcopy(getattr(selection, "diagnostics", {})),
                "gate_policies": {"G2": gate_policy},
                "wiring": (
                    "Used native ORCA relaxed scan for coarse reverse PEB, selected the last "
                    "topology-valid anchor before persistent drift, ran xTB PATH to the product, "
                    "recomputed xTB frame energies, refined every frame with ORCA B97-3c SP, "
                    "and replayed endpoint_knee_shift_midpoint_v1 inside ACP."
                ),
            },
            seed_candidates=seed_candidates,
            strategy_id=NATIVE_STRATEGY_ID,
            strategy_version=NATIVE_STRATEGY_VERSION,
            complete=bool(
                getattr(path_profile, "complete", False) and gate_policy["b97_full_coverage"]
            ),
            endpoint_evidence=dict(
                copy.deepcopy(
                    cast(Mapping[str, Any] | None, getattr(selection, "endpoint_evidence", None))
                    or {}
                )
            ),
            topology_segments=[
                {"start": int(start), "end": int(end), "valid": True}
                for start, end in cast(
                    Sequence[tuple[int, int]],
                    getattr(path_profile, "topology_valid_intervals", ()),
                )
            ],
            artifacts={
                "scan_profile": str(scan_profile_path),
                "product_xyz": str(product_xyz),
                "geometry_sha256_by_point": geometry_sha256_by_point,
            },
        )


def _positive_float(value: object, default: float) -> float:
    try:
        if value is None:
            raise ValueError
        parsed = float(value)
        if parsed <= 0.0:
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        return float(default)


def _positive_int(value: object, default: int | None) -> int | None:
    try:
        if value is None:
            raise ValueError
        parsed = int(value)
        if parsed <= 0:
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        return default


def _positive_int_default(value: object, default: int) -> int:
    parsed = _positive_int(value, default)
    return default if parsed is None else int(parsed)


def _nested_mapping(mapping: Mapping[str, Any] | None, *keys: str) -> dict[str, Any]:
    current: Any = mapping or {}
    for key in keys:
        if not isinstance(current, Mapping):
            return {}
        current = current.get(key)
    return dict(current) if isinstance(current, Mapping) else {}


def _sha256_bytes(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _sha256_text(payload: str) -> str:
    return _sha256_bytes(payload.encode("utf-8"))


def _json_hash(payload: Any) -> str:
    return _sha256_text(json.dumps(payload, sort_keys=True, default=str))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _artifact_ref(path: Path | str, kind: str) -> ArtifactRef:
    candidate = Path(str(path))
    if candidate.is_file():
        checksum = _file_sha256(candidate)
        resolved_path = str(candidate)
    else:
        resolved_path = str(path)
        checksum = _sha256_text(resolved_path)
    return ArtifactRef(path=resolved_path, sha256=checksum, kind=kind)


def _profile_id(profile: Any) -> str:
    if isinstance(profile, dict):
        return str(profile.get("name") or profile.get("profile_id") or NATIVE_STRATEGY_ID)
    return str(getattr(profile, "name", profile) or NATIVE_STRATEGY_ID)


def _profile_solvent(profile: Any) -> str | None:
    value = (
        getattr(profile, "solvent", None)
        if not isinstance(profile, dict)
        else profile.get("solvent")
    )
    if value in (None, ""):
        return None
    return str(value)


def _profile_solvent_model(profile: Any) -> str:
    value = (
        getattr(profile, "solvent_model", None)
        if not isinstance(profile, dict)
        else profile.get("solvent_model")
    )
    return str(value or "none")


def _state_symbols_coordinates(state: StableState) -> tuple[list[str], np.ndarray[Any, Any]]:
    coordinates, symbols = state_geometry(state)
    return list(symbols), np.asarray(coordinates, dtype=float)


def _resolve_geometry_file(state: StableState, destination: Path) -> Path:
    candidate = Path(str(state.canonical_geometry.path))
    if candidate.is_file():
        return candidate
    symbols, coordinates = _state_symbols_coordinates(state)
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_xyz(destination, coordinates, symbols, title=state.state_id)
    return destination


def _coordinate_plan_payload(plan: ReactionCoordinatePlan) -> dict[str, Any]:
    return {
        "coordinates": [
            {
                "id": spec.id,
                "kind": spec.kind,
                "atoms": list(spec.atoms),
                "role": spec.role,
                "start": spec.start,
                "end": spec.end,
                "force_constant": spec.force_constant,
            }
            for spec in plan.coordinates
        ],
        "points": plan.points,
        "coupling": plan.coupling,
        "start_from": plan.start_from,
    }


def _build_provenance(
    *,
    source_state: StableState,
    target_state: StableState,
    coordinate_plan: ReactionCoordinatePlan,
    profile: Any,
) -> Provenance:
    return Provenance(
        provider=NATIVE_PROVIDER_NAME,
        provider_version=NATIVE_STRATEGY_VERSION,
        provider_commit="",
        strategy=NATIVE_STRATEGY_ID,
        strategy_version=NATIVE_STRATEGY_VERSION,
        profile_id=_profile_id(profile),
        schema_version=NATIVE_SCHEMA_VERSION,
        input_signature=_json_hash(
            {
                "source_state": source_state.to_dict(),
                "target_state": target_state.to_dict(),
                "coordinate_plan": _coordinate_plan_payload(coordinate_plan),
            }
        ),
    )


def _route_id(source_state: StableState, target_state: StableState) -> str:
    route_id = str(source_state.metadata.get("route_id") or "")
    if route_id:
        return route_id
    route_id = str(target_state.metadata.get("route_id") or "")
    if route_id:
        return route_id
    return f"{source_state.state_id}__{target_state.state_id}"


def _forming_bonds_from_plan(coordinate_plan: Any) -> list[tuple[int, int]]:
    bonds: list[tuple[int, int]] = []
    for spec in getattr(coordinate_plan, "coordinates", ()):
        if str(getattr(spec, "kind", "")).lower() != "distance":
            continue
        atoms = tuple(int(atom) for atom in getattr(spec, "atoms", ())[:2])
        if len(atoms) != 2:
            continue
        pair = (min(atoms), max(atoms))
        if pair not in bonds:
            bonds.append(pair)
    return bonds


def _coarse_scan_coordinate(
    *,
    coordinate_plan: ReactionCoordinatePlan,
    product_coordinates: np.ndarray,
    coarse_step: float,
    default_stretch_end: float,
    configured_points: int | None,
) -> tuple[CoordinateSpec, int, float, float]:
    primary_distance = next(
        (
            spec
            for spec in coordinate_plan.coordinates
            if spec.kind == "distance" and spec.role == "drive"
        ),
        next((spec for spec in coordinate_plan.coordinates if spec.kind == "distance"), None),
    )
    if primary_distance is None:
        raise ValueError("RPH reverse path search requires at least one distance coordinate")

    product_distance = float(
        GeometryUtils.calculate_distance(
            np.asarray(product_coordinates, dtype=float),
            int(primary_distance.atoms[0]),
            int(primary_distance.atoms[1]),
        )
    )
    override_candidates = [
        float(value)
        for value in (primary_distance.start, primary_distance.end)
        if value is not None and float(value) > product_distance + 0.05
    ]
    stretch_target = (
        max(override_candidates, key=lambda value: abs(value - product_distance))
        if override_candidates
        else float(default_stretch_end)
    )
    stretch_target = max(stretch_target, product_distance + 0.05)
    derived_points = max(2, int(math.ceil((stretch_target - product_distance) / coarse_step)) + 1)
    points = configured_points if configured_points is not None else derived_points
    return (
        CoordinateSpec(
            id=primary_distance.id,
            kind="distance",
            atoms=primary_distance.atoms,
            role="drive",
            start=product_distance,
            end=stretch_target,
            force_constant=primary_distance.force_constant,
        ),
        int(points),
        product_distance,
        stretch_target,
    )


def _persistent_off_path_start(
    off_path_indices: Sequence[int],
    frame_count: int,
    persistent_points: int,
) -> int | None:
    off_path = {int(index) for index in off_path_indices}
    required = max(1, int(persistent_points))
    for start in range(frame_count):
        if all((start + offset) in off_path for offset in range(required)):
            return start
    return None


def _anchor_index(
    *,
    off_path_indices: Sequence[int],
    frame_count: int,
    persistent_points: int,
) -> tuple[int, int | None]:
    persistent_start = _persistent_off_path_start(off_path_indices, frame_count, persistent_points)
    invalid = {int(index) for index in off_path_indices}
    if persistent_start is not None:
        anchor = persistent_start - 1
    else:
        valid = [index for index in range(frame_count) if index not in invalid]
        anchor = valid[-1] if valid else -1
    if anchor < 0:
        raise RuntimeError("Native reverse PEB could not identify a topology-valid anchor frame")
    return anchor, persistent_start


def _mean_forming_bond_distance(
    frame_path: Path, forming_bonds: Sequence[tuple[int, int]]
) -> float | None:
    try:
        coordinates, _symbols = read_xyz(Path(frame_path))
    except (OSError, ValueError):
        return None
    distances = [
        float(GeometryUtils.calculate_distance(coordinates, int(atom_i), int(atom_j)))
        for atom_i, atom_j in forming_bonds
    ]
    if not distances:
        return None
    return float(sum(distances) / len(distances))


def _orient_product_to_stretch(
    *,
    frame_paths: Sequence[Path],
    forming_bonds: Sequence[tuple[int, int]],
) -> list[Path]:
    ordered = [Path(frame) for frame in frame_paths]
    if len(ordered) < 2:
        return ordered
    first = _mean_forming_bond_distance(ordered[0], forming_bonds)
    last = _mean_forming_bond_distance(ordered[-1], forming_bonds)
    if first is not None and last is not None and first > last:
        ordered.reverse()
    return ordered


def _single_point_energies_from_frames(
    *,
    backend: XTBBackend,
    frame_paths: Sequence[Path],
    charge: int,
    multiplicity: int,
    output_dir: Path,
) -> list[float | None]:
    energies: list[float | None] = []
    for index, frame_path in enumerate(frame_paths):
        coordinates, symbols = read_xyz(Path(frame_path))
        result = backend.single_point(
            np.asarray(coordinates, dtype=float),
            list(symbols),
            charge=charge,
            multiplicity=multiplicity,
            output_dir=output_dir / f"frame_{index:03d}",
        )
        energies.append(
            float(result.energy) if result.success and result.energy is not None else None
        )
    return energies


def _b97_sp_callable(
    *,
    backend: ORCABackend,
    charge: int,
    multiplicity: int,
    profile: Any,
    output_dir: Path,
) -> Any:
    solvent = _profile_solvent(profile)
    solvent_model = _profile_solvent_model(profile)

    def run(frame_xyz: Path) -> float | None:
        coordinates, symbols = read_xyz(Path(frame_xyz))
        result = backend.single_point(
            np.asarray(coordinates, dtype=float),
            list(symbols),
            charge=charge,
            multiplicity=multiplicity,
            output_dir=output_dir / Path(frame_xyz).stem,
            method="B97-3c",
            basis="",
            solvent=solvent,
            solvent_model=solvent_model,
        )
        if result.success and result.energy is not None:
            return float(result.energy)
        return None

    return run


def _coordinate_labels(
    coordinate_plan: ReactionCoordinatePlan,
    reaction_coordinates: Sequence[float],
) -> list[str]:
    plan_coordinates = list(getattr(coordinate_plan, "coordinates", ()) or ())
    if len(plan_coordinates) == len(reaction_coordinates) and plan_coordinates:
        return [
            str(getattr(spec, "id", f"rc{index + 1}"))
            for index, spec in enumerate(plan_coordinates)
        ]
    return [f"rc{index + 1}" for index in range(len(reaction_coordinates))]


def _path_points_from_profile(
    *,
    path_profile: Any,
    coordinate_plan: ReactionCoordinatePlan,
    provenance: Provenance,
    xtb_energies: Sequence[float | None],
    b97_energies: Sequence[float | None],
) -> list[PathPoint]:
    points: list[PathPoint] = []
    for frame in cast(Sequence[Any], getattr(path_profile, "frames", ())):
        frame_index = int(frame.frame_index)
        labels = _coordinate_labels(coordinate_plan, getattr(frame, "reaction_coordinates", ()))
        coordinate_values = {
            label: float(value)
            for label, value in zip(labels, getattr(frame, "reaction_coordinates", ()), strict=True)
        }
        coordinates, _symbols = read_xyz(Path(str(frame.xyz)))
        points.append(
            PathPoint(
                point_id=f"p{frame_index:03d}",
                progress=float(frame.progress),
                coordinate_values=coordinate_values,
                reaction_coordinates=dict(coordinate_values),
                geometry=np.asarray(coordinates, dtype=float),
                energies_hartree={
                    "xtb": xtb_energies[frame_index] if frame_index < len(xtb_energies) else None,
                    "b97-3c": b97_energies[frame_index]
                    if frame_index < len(b97_energies)
                    else None,
                },
                topology={"valid": bool(frame.topology_valid), "reason": frame.topology_reason},
                frame_index=frame_index,
                arc_length=float(frame.progress),
                topology_valid=bool(frame.topology_valid),
                diagnostics={
                    "rmsd_to_product": frame.rmsd_to_product,
                    "neighbor_rmsd": frame.neighbor_rmsd,
                    "gradient_proxy": frame.gradient_proxy,
                    "curvature_proxy": frame.curvature_proxy,
                    "source": frame.source,
                    "frame_xyz": str(frame.xyz),
                },
                provenance=provenance,
            )
        )
    return points


def _seed_selection_to_seed_candidates(
    selection: Any,
    *,
    point_ids_by_frame: Mapping[int, str],
    selection_mode: str = RPH_SELECTION_ALGORITHM,
) -> list[SeedCandidate]:
    """Kept local for self-contained native parity with the adapter converters."""

    candidates: list[SeedCandidate] = []
    ts_seed = cast(Mapping[str, Any] | None, getattr(selection, "ts_search_seed", None))
    int_seed = cast(Mapping[str, Any] | None, getattr(selection, "int_search_seed", None))
    if ts_seed is not None:
        frame_index = int(ts_seed.get("frame_index") or 0)
        point_id = point_ids_by_frame.get(frame_index, f"p{frame_index:03d}")
        candidates.append(
            SeedCandidate(
                id=f"ts_seed_{point_id}",
                kind="ts_seed",
                geometry=_artifact_ref(Path(str(ts_seed.get("xyz"))), "ts_seed_geometry"),
                rank=1,
                selection_mode=selection_mode,
                confidence=str(ts_seed.get("confidence") or "medium"),
                evidence={
                    "frame_index": frame_index,
                    "point_id": point_id,
                    "seed": dict(ts_seed),
                    "seed_evidence": getattr(selection, "seed_evidence", None),
                },
                stationary_point_claimed=False,
            )
        )
    if int_seed is not None:
        frame_index = int(int_seed.get("frame_index") or 0)
        point_id = point_ids_by_frame.get(frame_index, f"p{frame_index:03d}")
        candidates.append(
            SeedCandidate(
                id=f"int_seed_{point_id}",
                kind="intermediate_seed",
                geometry=_artifact_ref(
                    Path(str(int_seed.get("xyz"))),
                    "intermediate_seed_geometry",
                ),
                rank=1,
                selection_mode=selection_mode,
                confidence="medium" if not bool(int_seed.get("shared_with_ts")) else "low",
                evidence={
                    "frame_index": frame_index,
                    "point_id": point_id,
                    "seed": dict(int_seed),
                    "seed_evidence": getattr(selection, "seed_evidence", None),
                    "shared_with_ts": bool(int_seed.get("shared_with_ts", False)),
                    "has_independent_int": bool(getattr(selection, "has_independent_int", False)),
                },
                stationary_point_claimed=False,
            )
        )
    return candidates


def _selection_to_path_candidates(
    selection: Any,
    *,
    point_ids_by_frame: Mapping[int, str],
    progress_by_frame: Mapping[int, float],
) -> list[PathCandidate]:
    candidates: list[PathCandidate] = []
    ts_seed = cast(Mapping[str, Any] | None, getattr(selection, "ts_search_seed", None))
    if ts_seed is not None:
        frame_index = int(ts_seed.get("frame_index") or 0)
        point_id = point_ids_by_frame.get(frame_index, f"p{frame_index:03d}")
        candidates.append(
            PathCandidate(
                candidate_id=f"ts_candidate_{point_id}",
                kind="ts_seed",
                point_id=point_id,
                reason=RPH_SELECTION_ALGORITHM,
                progress=float(progress_by_frame.get(frame_index, 0.0)),
                score=None,
            )
        )
    int_seed = cast(Mapping[str, Any] | None, getattr(selection, "int_search_seed", None))
    if int_seed is not None:
        frame_index = int(int_seed.get("frame_index") or 0)
        point_id = point_ids_by_frame.get(frame_index, f"p{frame_index:03d}")
        candidates.append(
            PathCandidate(
                candidate_id=f"int_candidate_{point_id}",
                kind="intermediate_seed",
                point_id=point_id,
                reason=RPH_SELECTION_ALGORITHM,
                progress=float(progress_by_frame.get(frame_index, 0.0)),
                score=None,
            )
        )
    return candidates


def _g2_policy(
    *,
    path_profile: Any,
    b97_energies: Sequence[float | None],
    selection: Any,
) -> dict[str, Any]:
    endpoint_evidence = cast(
        Mapping[str, Any] | None, getattr(selection, "endpoint_evidence", None)
    )
    knee_evidence = cast(Mapping[str, Any] | None, getattr(selection, "knee_evidence", None))
    b97_full_coverage = bool(b97_energies) and all(value is not None for value in b97_energies)
    return {
        "require_profile_complete": True,
        "require_b97_full_coverage": True,
        "require_valid_corridor": bool(getattr(path_profile, "topology_valid_intervals", ())),
        "require_effective_endpoint": bool(
            endpoint_evidence and endpoint_evidence.get("effective_endpoint_index") is not None
        ),
        "require_knee": bool(knee_evidence and knee_evidence.get("frame_index") is not None),
        "require_ts_seed": getattr(selection, "ts_search_seed", None) is not None,
        "profile_complete": bool(getattr(path_profile, "complete", False)),
        "b97_full_coverage": b97_full_coverage,
        "selector": RPH_SELECTION_ALGORITHM,
    }


def _scan_profile_payload(
    *,
    product_xyz: Path,
    forming_bonds: Sequence[tuple[int, int]],
    path_profile: Any,
    xtb_energies: Sequence[float | None],
    b97_energies: Sequence[float | None],
    selection: Any,
    coarse_scan: Any,
    coarse_guard: Mapping[str, Any],
    path_guard: Mapping[str, Any],
    anchor_index: int,
    anchor_xyz: Path,
    route_id: str,
    persistent_off_path_start: int | None,
) -> dict[str, Any]:
    return {
        "profile_schema_version": NATIVE_SCHEMA_VERSION,
        "generation_method": "native_reverse_peb",
        "selection_source": "xtb_path",
        "route_id": route_id,
        "product_xyz": str(product_xyz),
        "forming_bonds": [list(pair) for pair in forming_bonds],
        "frames": [str(Path(str(frame.xyz))) for frame in cast(Sequence[Any], path_profile.frames)],
        "energy_curves": {
            "xtb": {
                "method": "GFN2-xTB",
                "status": "complete"
                if all(value is not None for value in xtb_energies)
                else "partial",
                "energies_hartree": list(xtb_energies),
            },
            "b973c": {
                "method": "B97-3c",
                "status": "complete"
                if all(value is not None for value in b97_energies)
                else "partial",
                "energies_hartree": list(b97_energies),
            },
        },
        "trajectory_quality": {
            "coarse_scan_off_path_indices": list(
                cast(Sequence[int], coarse_guard.get("off_path_indices") or [])
            ),
            "off_path_indices": list(cast(Sequence[int], path_guard.get("off_path_indices") or [])),
            "excluded_frames": [
                int(index) for index in getattr(path_profile, "excluded_frames", ())
            ],
            "anchor_frame_index": anchor_index,
            "anchor_xyz": str(anchor_xyz),
            "persistent_off_path_start": persistent_off_path_start,
        },
        "coarse_scan": {
            "success": bool(getattr(coarse_scan, "success", False)),
            "message": str(getattr(coarse_scan, "message", "") or ""),
            "frame_count": len(getattr(coarse_scan, "points", ())),
        },
        "selection_diagnostics": copy.deepcopy(getattr(selection, "diagnostics", {})),
        "endpoint_evidence": dict(
            copy.deepcopy(
                cast(Mapping[str, Any] | None, getattr(selection, "endpoint_evidence", None)) or {}
            )
        ),
        "knee_evidence": dict(
            copy.deepcopy(
                cast(Mapping[str, Any] | None, getattr(selection, "knee_evidence", None)) or {}
            )
        ),
        "ts_search_seed": None
        if getattr(selection, "ts_search_seed", None) is None
        else dict(cast(Mapping[str, Any], getattr(selection, "ts_search_seed"))),
        "int_search_seed": None
        if getattr(selection, "int_search_seed", None) is None
        else dict(cast(Mapping[str, Any], getattr(selection, "int_search_seed"))),
        "has_independent_int": bool(getattr(selection, "has_independent_int", False)),
        "rejection_reason": getattr(selection, "rejection_reason", None),
    }


__all__ = ["NativeReversePebStrategy"]
