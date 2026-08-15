"""Path-search strategies: guided-scan / rph-reverse / direct-ts.

Each strategy consumes a :class:`MechanismRoute` and produces a
:class:`PathResult` (path points + TS/INT/endpoint candidates). Strategies
are backend-driven functions, not classes — the workflow selects one via
``resolve_path_strategy`` and calls it with the same signature.
"""

# pyright: reportAny=false, reportExplicitAny=false, reportUnusedFunction=false, reportUnusedParameter=false

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from acp.mechanism.candidates import select_candidates, select_primary_int, select_primary_ts
from acp.mechanism.models import (
    ArtifactRef,
    MechanismRoute,
    PathCandidate,
    PathPoint,
    PathResult,
    StableState,
)
from acp.mechanism.presets import PATH_STRATEGIES, FidelityProfile, resolve_strategy
from acp.mechanism.providers.native_peb import NativeReversePebStrategy
from cccp.qc.interfaces.xtb_scan import RelaxedScanResult

logger = logging.getLogger(__name__)


def _frame_points(scan: RelaxedScanResult, energy_key: str) -> list[PathPoint]:
    points: list[PathPoint] = []
    for i, frame in enumerate(scan.points):
        points.append(
            PathPoint(
                point_id=f"p{i:03d}",
                progress=frame.progress,
                coordinate_values=dict(frame.coordinate_values),
                geometry=frame.coordinates,
                energies_hartree=(
                    {energy_key: frame.energy_hartree} if frame.energy_hartree is not None else {}
                ),
                frame_index=frame.frame_index,
            )
        )
    return points


def run_guided_scan(
    route: MechanismRoute,
    *,
    coordinates: NDArray[np.float64],
    symbols: list[str],
    charge: int,
    multiplicity: int,
    scan_dir: Path,
    backend: Any,
    fidelity: FidelityProfile,
    energy_key: str = "gfn2-xtb",
) -> PathResult:
    """Drive the route's coordinates with a synchronous xTB relaxed scan.

    Delegates to the backend's ``relaxed_scan`` (xTB constrained
    optimization per frame, previous-frame seeding) and then selects
    TS / intermediate / endpoint candidates from the energy profile.
    """
    if not route.coordinate_plan.drive_coordinates():
        raise ValueError("guided-scan requires at least one drive coordinate")

    scan: RelaxedScanResult = backend.relaxed_scan(
        coordinates,
        symbols,
        scan_dir,
        route.coordinate_plan,
        charge=charge,
        multiplicity=multiplicity,
        opt_level="normal",
        fail_fast=True,
    )

    points = _frame_points(scan, energy_key)
    candidates = select_candidates(points, energy_key=energy_key)
    result = PathResult(
        points=points,
        candidates=candidates,
        strategy="guided-scan",
        route_id=route.route_id,
        metadata={
            "energy_key": energy_key,
            "scan_success": scan.success,
            "scan_message": scan.message,
            "points": scan.points.__len__(),
        },
    )
    primary_ts = select_primary_ts(result)
    primary_int = select_primary_int(result)
    if primary_ts is not None:
        result.selected_ts_id = primary_ts.candidate_id
    if primary_int is not None:
        result.selected_int_id = primary_int.candidate_id
    return result


def run_rph_reverse(
    route: MechanismRoute,
    *,
    coordinates: NDArray[np.float64],
    symbols: list[str],
    charge: int,
    multiplicity: int,
    scan_dir: Path,
    backend: Any,
    fidelity: FidelityProfile,
    energy_key: str = "gfn2-xtb",
) -> PathResult:
    """Run the native reverse-PEB engine and return the workflow PathResult."""
    if not route.product_id:
        raise ValueError("rph-reverse requires a product structure (route.product_id)")

    config = getattr(backend, "config", None)
    strategy = NativeReversePebStrategy(config=config, work_root=scan_dir)
    source_state = StableState(
        state_id=route.reactant_id or f"{route.route_id}__reactant",
        role="reactant",
        canonical_geometry=ArtifactRef(
            path=f"memory://{route.route_id}/reactant",
            sha256=f"sha256:{route.reactant_id or route.route_id}:reactant",
            kind="stable_state_geometry",
        ),
        charge=charge,
        multiplicity=multiplicity,
        identity_fingerprint=f"sha256:{route.reactant_id or route.route_id}:reactant",
        metadata={"route_id": route.route_id},
    )
    target_state = StableState(
        state_id=route.product_id,
        role="product",
        canonical_geometry=ArtifactRef(
            path=f"memory://{route.route_id}/product",
            sha256=f"sha256:{route.product_id}:product",
            kind="stable_state_geometry",
        ),
        charge=charge,
        multiplicity=multiplicity,
        identity_fingerprint=f"sha256:{route.product_id}:product",
        metadata={
            "route_id": route.route_id,
            "coordinates": np.asarray(coordinates, dtype=float).tolist(),
            "symbols": list(symbols),
        },
    )
    return strategy.search(source_state, target_state, route.coordinate_plan, fidelity)


def _reversed_spec(spec: Any) -> Any:
    return type(spec)(
        id=spec.id,
        kind=spec.kind,
        atoms=spec.atoms,
        role=spec.role,
        start=spec.end,
        end=spec.start,
        force_constant=spec.force_constant,
    )


def run_direct_ts(
    route: MechanismRoute,
    *,
    coordinates: NDArray[np.float64],
    symbols: list[str],
    charge: int,
    multiplicity: int,
    scan_dir: Path,
    backend: Any,
    fidelity: FidelityProfile,
    energy_key: str = "gfn2-xtb",
) -> PathResult:
    """Bypass path search: treat the supplied TS guess as the single seed.

    Requires ``route.ts_guess_id``; produces a one-point path with a single
    TS candidate so downstream stages (TS optimize → validate → IRC) still
    run unchanged.
    """
    if not route.ts_guess_id:
        raise ValueError("direct-ts requires a TS guess (route.ts_guess_id)")

    point = PathPoint(
        point_id="p000",
        progress=0.5,
        coordinate_values=dict(route.coordinate_plan.coordinate_targets(0)),
        geometry=coordinates,
    )
    candidate = PathCandidate(
        candidate_id="ts_candidate_01",
        kind="ts_seed",
        point_id="p000",
        reason="user_supplied_ts_guess",
        progress=0.5,
        score=0.0,
    )
    result = PathResult(
        points=[point],
        candidates=[candidate],
        strategy="direct-ts",
        route_id=route.route_id,
        selected_ts_id="ts_candidate_01",
        metadata={"energy_key": energy_key},
    )
    return result


_STRATEGY_IMPLS: dict[str, Callable[..., PathResult]] = {
    "guided-scan": run_guided_scan,
    "rph-reverse": run_rph_reverse,
    "direct-ts": run_direct_ts,
}


def resolve_path_strategy(strategy: str | None) -> Callable[..., PathResult]:
    """Return the strategy callable for a (possibly unset) strategy id.

    Unknown / unimplemented strategies fall back to ``guided-scan`` with a
    warning (endpoint-path is a declared-but-unimplemented hook).
    """
    name = resolve_strategy(strategy)
    impl = _STRATEGY_IMPLS.get(name)
    if impl is not None:
        return impl
    if name in PATH_STRATEGIES and not PATH_STRATEGIES[name].supported:
        logger.warning("strategy %r not implemented; falling back to guided-scan", name)
    return run_guided_scan


__all__ = [
    "resolve_path_strategy",
    "run_direct_ts",
    "run_guided_scan",
    "run_rph_reverse",
]
