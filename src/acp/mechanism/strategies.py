"""Legacy compatibility shims for study-path strategy tests."""

# pyright: reportAny=false, reportExplicitAny=false, reportUnusedParameter=false

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from acp.mechanism.models import ArtifactRef, MechanismRoute, PathResult, StableState
from acp.mechanism.presets import FidelityProfile
from acp.mechanism.providers.native_peb import NativeReversePebStrategy


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
    """Run the native reverse-PEB engine and return the study PathResult."""
    del energy_key
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


__all__ = ["NativeReversePebStrategy", "run_rph_reverse"]
