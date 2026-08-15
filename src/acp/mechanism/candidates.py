"""Path candidate selection: TS / intermediate / endpoint seeds.

A relaxed scan yields an energy profile; the candidates module turns it into
a ranked set of TS seeds (local energy maxima), intermediate seeds (local
minima after a barrier) and endpoints. Seeding multiple candidates and
refining each with a cheap TS optimization is far more robust than guessing a
single PEB point (RPH S2 lesson).
"""

from __future__ import annotations

from collections.abc import Sequence

from acp.mechanism.models import PathCandidate, PathPoint, PathResult


def select_candidates(
    points: Sequence[PathPoint],
    *,
    energy_key: str,
    ts_cap: int = 3,
    int_cap: int = 2,
    endpoint_cap: int = 2,
) -> list[PathCandidate]:
    """Select TS / intermediate / endpoint candidates from an energy profile.

    Selection rules (energies read from ``point.energies_hartree[energy_key]``):

    * **TS seed** — a point whose energy is higher than both neighbours
      (local maximum) or a plateau turning point (equal high neighbours);
    * **Intermediate seed** — a local minimum strictly after a TS seed (i.e.
      on the product side of the first barrier);
    * **Endpoint** — the two extreme points (lowest/highest progress) are
      always treated as endpoints, never as seeds.

    Candidates are ranked by descending |ΔE| from the profile minimum (TS)
    or ascending ΔE (intermediate), then truncated to the caps. Points with
    missing energies are skipped.

    Args:
        points: Path points sorted by progress.
        energy_key: Method key in ``point.energies_hartree`` (e.g.
            ``"gfn2-xtb"`` or ``"b97-3c"``).
        ts_cap: Maximum number of TS seeds to emit.
        int_cap: Maximum number of intermediate seeds.
        endpoint_cap: Maximum number of endpoint candidates.

    Returns:
        Ordered candidate list (endpoints first, then TS seeds by rank, then
        intermediate seeds).
    """
    if len(points) < 3:
        return []

    scored: list[tuple[int, float]] = []
    for idx, point in enumerate(points):
        energy = point.energies_hartree.get(energy_key)
        if energy is None:
            continue
        scored.append((idx, float(energy)))
    if len(scored) < 3:
        return []
    scored.sort(key=lambda pair: pair[0])

    energies: dict[int, float] = dict(scored)
    profile_min = min(energies.values())
    profile_max = max(energies.values())
    span = profile_max - profile_min if profile_max > profile_min else 1.0

    ts_seeds: list[tuple[float, int]] = []
    int_seeds: list[tuple[float, int]] = []
    for idx in sorted(energies):
        prev_e = energies.get(idx - 1)
        next_e = energies.get(idx + 1)
        if prev_e is None or next_e is None:
            continue
        current = energies[idx]
        is_maximum = current > prev_e and current >= next_e
        is_plateau_peak = current == prev_e == next_e and current > profile_min + 0.25 * span
        if is_maximum or is_plateau_peak:
            prominence = current - profile_min
            ts_seeds.append((prominence, idx))

    first_ts_idx: int | None = None
    if ts_seeds:
        first_ts_idx = min(i for _, i in ts_seeds)
    for idx in sorted(energies):
        if first_ts_idx is not None and idx <= first_ts_idx:
            continue
        prev_e = energies.get(idx - 1)
        next_e = energies.get(idx + 1)
        if prev_e is None or next_e is None:
            continue
        current = energies[idx]
        if current < prev_e and current <= next_e:
            int_seeds.append((current - profile_min, idx))

    endpoints: list[PathCandidate] = []
    ts_candidates: list[PathCandidate] = []
    int_candidates: list[PathCandidate] = []

    ordered_indices = sorted(energies)
    if ordered_indices:
        for kind, idx in (("start", ordered_indices[0]), ("end", ordered_indices[-1])):
            if len(endpoints) >= endpoint_cap:
                break
            point = points[idx]
            endpoints.append(
                PathCandidate(
                    candidate_id=f"endpoint_{kind}",
                    kind="endpoint",
                    point_id=point.point_id,
                    reason=f"path_{kind}",
                    progress=point.progress,
                    score=float(energies[idx]),
                )
            )

    ts_seeds.sort(key=lambda pair: pair[0], reverse=True)
    for rank, (prominence, idx) in enumerate(ts_seeds[:ts_cap]):
        point = points[idx]
        ts_candidates.append(
            PathCandidate(
                candidate_id=f"ts_candidate_{rank + 1:02d}",
                kind="ts_seed",
                point_id=point.point_id,
                reason="local_energy_maximum",
                progress=point.progress,
                score=float(prominence),
            )
        )

    int_seeds.sort(key=lambda pair: pair[0])
    for rank, (depth, idx) in enumerate(int_seeds[:int_cap]):
        point = points[idx]
        int_candidates.append(
            PathCandidate(
                candidate_id=f"int_candidate_{rank + 1:02d}",
                kind="intermediate_seed",
                point_id=point.point_id,
                reason="local_minimum_after_barrier",
                progress=point.progress,
                score=float(depth),
            )
        )

    return endpoints + ts_candidates + int_candidates


def select_primary_ts(result: PathResult) -> PathCandidate | None:
    """Return the highest-ranked TS seed of a path result (or None)."""
    ts_seeds = [c for c in result.candidates if c.kind == "ts_seed"]
    if not ts_seeds:
        return None
    ts_seeds.sort(key=lambda c: (c.score is None, -(c.score or 0.0)))
    return ts_seeds[0]


def select_primary_int(result: PathResult) -> PathCandidate | None:
    """Return the highest-ranked intermediate seed (or None)."""
    int_seeds = [c for c in result.candidates if c.kind == "intermediate_seed"]
    if not int_seeds:
        return None
    int_seeds.sort(key=lambda c: (c.score is None, c.score or 0.0))
    return int_seeds[0]


__all__ = ["select_candidates", "select_primary_int", "select_primary_ts"]
