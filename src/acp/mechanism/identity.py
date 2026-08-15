# pyright: reportAny=false, reportExplicitAny=false, reportImplicitStringConcatenation=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnnecessaryComparison=false, reportUnusedParameter=false
"""TS / stable-state identity utilities.

A frequency analysis giving exactly one imaginary frequency is necessary but
not sufficient for a valid transition state. ``mode_match_score`` quantifies
how strongly the imaginary mode drives the user-defined reaction coordinates:
displace the TS geometry ±δ along the mode and measure how much each driven
coordinate changes. A mode that barely moves the driven coordinates (e.g. a
methyl rotation) scores low even though the frequency count is correct.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from acp.mechanism.models import PathPoint, TsIdentity
from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan
from cccp.utils.geometry_tools import GeometryUtils

logger = logging.getLogger(__name__)

_DIHEDRAL_PERIOD_DEG = 360.0
_HALF_DIHEDRAL_PERIOD_DEG = 180.0

CoordinateMeasure = Callable[[NDArray[np.float64], CoordinateSpec], float]


def compute_mode_match_score(
    mode_vector: NDArray[np.float64],
    plan: ReactionCoordinatePlan,
    *,
    geometry: NDArray[np.float64] | None = None,
    displacement_a: float = 0.05,
    measure_coordinate: Callable[[NDArray[np.float64], tuple[int, ...]], float] | None = None,
) -> float | None:
    """Estimate how well the imaginary mode drives the plan's coordinates.

    Displaces the (optionally supplied) geometry ±δ along the normalized mode
    vector and measures each drive coordinate in both displaced geometries;
    the score is the RMS of per-coordinate |Δq|. ``None`` when the mode vector
    or the plan is unusable.

    Args:
        mode_vector: Mode displacement vectors (N×3).
        plan: Reaction-coordinate plan (drive coordinates only are scored).
        geometry: TS geometry (Å, N×3); when None only the relative
            displacement is scored (self-displacement metric).
        displacement_a: Step size (Å) for the finite displacement.
        measure_coordinate: Coordinate evaluator; default is the Euclidean
            distance between the two atoms of a drive coordinate.
    """
    if mode_vector is None or mode_vector.size == 0:
        return None
    drive = plan.drive_coordinates()
    if not drive:
        return None

    scaled = np.asarray(mode_vector, dtype=float).reshape(-1, 3)
    norm = float(np.linalg.norm(scaled))
    if norm <= 0.0:
        return None
    unit = scaled / norm

    if measure_coordinate is None:

        def _default_measure(coords: NDArray[np.float64], atoms: tuple[int, ...]) -> float:
            delta = coords[atoms[1]] - coords[atoms[0]]
            return float(np.linalg.norm(delta))

        measure_coordinate = _default_measure

    if geometry is not None:
        base = np.asarray(geometry, dtype=float).reshape(-1, 3)
        plus = base + unit * displacement_a
        minus = base - unit * displacement_a
    else:
        plus = unit * displacement_a
        minus = -unit * displacement_a

    deltas: list[float] = []
    for spec in drive:
        q_plus = measure_coordinate(plus, spec.atoms)
        q_minus = measure_coordinate(minus, spec.atoms)
        deltas.append(float(abs(q_plus - q_minus)))
    if not deltas:
        return None
    return float(np.sqrt(np.mean(np.asarray(deltas) ** 2)))


def compute_rc_alignment_score(
    mode_vector: NDArray[np.float64],
    geometry: NDArray[np.float64],
    plan: ReactionCoordinatePlan,
    *,
    mode_amplitude: float = 0.1,
    weights: dict[str, float] | None = None,
    measure: CoordinateMeasure | None = None,
) -> dict[str, Any]:
    """Score reaction-coordinate alignment for a normalized imaginary mode.

    The mode is normalized once, then the TS geometry is displaced as
    ``x± = x_ts ± δ·v̂`` where ``δ`` is ``mode_amplitude`` and ``v̂`` is the
    normalized mode. For each drive coordinate the score tracks the signed
    finite-difference response and whether that response matches the expected
    reaction-coordinate direction.

    Args:
        mode_vector: Mode displacement vectors (N×3).
        geometry: TS geometry in Å.
        plan: Reaction-coordinate plan.
        mode_amplitude: Displacement amplitude applied to the normalized mode.
        weights: Optional per-coordinate weights keyed by ``CoordinateSpec.id``.
        measure: Optional coordinate evaluator. Defaults to distance / angle /
            dihedral measurements from :mod:`cccp.utils.geometry_tools`.

    Returns:
        Dict with ``score`` in ``[0, 1]``, per-coordinate evidence, and the
        applied amplitude.
    """
    unit_mode = _normalized_mode(mode_vector)
    drive_coordinates = plan.drive_coordinates()
    if unit_mode is None or not drive_coordinates:
        return {"score": 0.0, "per_coordinate": {}, "amplitude": float(mode_amplitude)}

    base_geometry = np.asarray(geometry, dtype=float).reshape(-1, 3)
    plus = base_geometry + unit_mode * float(mode_amplitude)
    minus = base_geometry - unit_mode * float(mode_amplitude)
    evaluator = measure or _measure_coordinate

    weighted_matched = 0.0
    weighted_total = 0.0
    per_coordinate: dict[str, dict[str, Any]] = {}

    for spec in drive_coordinates:
        q_plus = float(evaluator(plus, spec))
        q_minus = float(evaluator(minus, spec))
        delta = _coordinate_delta(spec, q_plus, q_minus)
        magnitude = abs(delta)
        weight = _non_negative_weight(weights, spec.id)
        expected_sign = _expected_direction_sign(spec)
        matched = magnitude > 0.0 and (
            expected_sign == 0.0 or np.sign(delta) == np.sign(expected_sign)
        )
        contribution = weight * magnitude
        weighted_total += contribution
        if matched:
            weighted_matched += contribution

        per_coordinate[spec.id] = {
            "kind": spec.kind,
            "atoms": list(spec.atoms),
            "q_plus": q_plus,
            "q_minus": q_minus,
            "delta": delta,
            "magnitude": magnitude,
            "weight": weight,
            "matched": matched,
            "expected_sign": expected_sign,
        }

    score = weighted_matched / weighted_total if weighted_total > 0.0 else 0.0
    return {
        "score": float(np.clip(score, 0.0, 1.0)),
        "per_coordinate": per_coordinate,
        "amplitude": float(mode_amplitude),
    }


def classify_ts_identity(
    imaginary_frequencies: Sequence[float],
    *,
    mode_match_score: float | None = None,
    topology_sane: bool = True,
    imaginary_cutoff_cm1: float = -50.0,
    mode_match_threshold: float = 0.05,
    rc_alignment: float | None = None,
    rc_alignment_threshold: float = 0.5,
) -> TsIdentity:
    """Build a :class:`TsIdentity` from frequency + mode-overlap evidence.

    Valid when exactly one imaginary frequency exists, it is below
    *imaginary_cutoff_cm1*, the mode-match score (when computed) is at or
    above *mode_match_threshold*, and the topology is sane.
    """
    imaginary = [float(f) for f in imaginary_frequencies if float(f) < 0.0]
    count = len(imaginary)
    lowest = min(imaginary) if imaginary else None
    messages: list[str] = []

    if count != 1:
        messages.append(f"imaginary frequency count = {count} (expected 1)")
    elif lowest is not None and lowest > imaginary_cutoff_cm1:
        messages.append(
            f"imaginary frequency {lowest:.1f} cm⁻¹ above cutoff {imaginary_cutoff_cm1:.1f}"
        )
    if mode_match_score is not None and mode_match_score < mode_match_threshold:
        messages.append(
            f"reaction-coordinate mode overlap {mode_match_score:.3f} "
            f"below threshold {mode_match_threshold:.3f}"
        )
    if rc_alignment is not None and rc_alignment < rc_alignment_threshold:
        messages.append(
            f"reaction-coordinate alignment {rc_alignment:.3f} "
            f"below threshold {rc_alignment_threshold:.3f}"
        )
    if not topology_sane:
        messages.append("topology check failed")

    valid = (
        count == 1
        and (lowest is None or lowest <= imaginary_cutoff_cm1)
        and (mode_match_score is None or mode_match_score >= mode_match_threshold)
        and (rc_alignment is None or rc_alignment >= rc_alignment_threshold)
        and topology_sane
    )
    return TsIdentity(
        imaginary_count=count,
        imaginary_frequency_cm1=lowest,
        mode_match_score=mode_match_score,
        topology_sane=topology_sane,
        valid=valid,
        messages=messages,
    )


def validate_path_candidate(
    point: PathPoint,
    *,
    plan: ReactionCoordinatePlan,
    imaginary_frequencies: Sequence[float],
    mode_vector: NDArray[np.float64] | None = None,
) -> TsIdentity:
    """Convenience wrapper: classify a path candidate as a valid TS.

    Displaces along the imaginary mode (when available) to compute the
    reaction-coordinate overlap against the route's drive coordinates.
    """
    score = None
    if mode_vector is not None:
        score = compute_mode_match_score(mode_vector, plan)
    return classify_ts_identity(imaginary_frequencies, mode_match_score=score)


@dataclass(frozen=True)
class StableStateIdentityEvidence:
    """Evidence bundle for stable-state classification.

    Attributes:
        stationary_order: Hessian stationary-point order (0 for a minimum).
        connectivity_signature: Connectivity / identity verdict label.
        reaction_coordinate_state: RC-side verdict such as ``"intermediate"``
            or ``"collapsed_to_product"``.
        rmsd_to_known_states: Mapped RMSD table against known stable states.
        energy_relationship: Relative-energy evidence (label / flags / deltas).
        charge: Observed molecular charge.
        multiplicity: Observed spin multiplicity.
        missing_evidence: Missing evidence labels collected upstream.
    """

    stationary_order: int | None = None
    connectivity_signature: str | None = None
    reaction_coordinate_state: str | None = None
    rmsd_to_known_states: dict[str, float] = field(default_factory=dict)
    energy_relationship: dict[str, Any] = field(default_factory=dict)
    charge: int | None = None
    multiplicity: int | None = None
    missing_evidence: list[str] = field(default_factory=list)


def classify_stable_state(
    evidence: StableStateIdentityEvidence,
    thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify stable-state identity from aggregated ACP-native evidence.

    Args:
        evidence: Stable-state evidence bundle.
        thresholds: Optional threshold / expectation overrides.

    Returns:
        Dict carrying a stable-state identity label, validity flag, and the
        detailed checks used to reach that verdict.
    """
    merged = _stable_state_thresholds()
    if thresholds:
        merged.update(dict(thresholds))

    messages: list[str] = []
    missing = list(evidence.missing_evidence)
    if missing:
        messages.append(f"missing evidence: {', '.join(missing)}")

    stationary_order_ok = evidence.stationary_order == int(merged["expected_stationary_order"])
    if not stationary_order_ok:
        messages.append(
            f"stationary order {evidence.stationary_order!r} != "
            f"{merged['expected_stationary_order']}"
        )

    charge_ok = merged["expected_charge"] is None or evidence.charge == merged["expected_charge"]
    multiplicity_ok = (
        merged["expected_multiplicity"] is None
        or evidence.multiplicity == merged["expected_multiplicity"]
    )
    if not charge_ok:
        messages.append(f"charge {evidence.charge!r} != expected {merged['expected_charge']!r}")
    if not multiplicity_ok:
        messages.append(
            "multiplicity "
            f"{evidence.multiplicity!r} != expected {merged['expected_multiplicity']!r}"
        )

    connectivity_signature = str(evidence.connectivity_signature or "unknown")
    rc_state = str(evidence.reaction_coordinate_state or "unknown")
    energy_label = str(evidence.energy_relationship.get("label") or "")
    closest_state_id, closest_state_rmsd = _closest_rmsd_match(evidence.rmsd_to_known_states)
    rmsd_match_ok = (
        closest_state_rmsd is None
        or closest_state_rmsd > float(merged["max_rmsd_match"])
        or closest_state_id is None
    )

    collapsed_to_product = _is_collapsed_state(
        connectivity_signature=connectivity_signature,
        rc_state=rc_state,
        energy_relationship=evidence.energy_relationship,
        closest_state_id=closest_state_id,
        closest_state_rmsd=closest_state_rmsd,
        rmsd_threshold=float(merged["max_rmsd_match"]),
        state_tokens=set(merged["product_state_tokens"]),
        connectivity_markers=set(merged["collapsed_product_connectivity"]),
        rc_markers=set(merged["collapsed_product_rc_states"]),
        energy_markers=set(merged["collapsed_product_energy_labels"]),
        explicit_flag="collapsed_to_product",
    )
    collapsed_to_reactant = _is_collapsed_state(
        connectivity_signature=connectivity_signature,
        rc_state=rc_state,
        energy_relationship=evidence.energy_relationship,
        closest_state_id=closest_state_id,
        closest_state_rmsd=closest_state_rmsd,
        rmsd_threshold=float(merged["max_rmsd_match"]),
        state_tokens=set(merged["reactant_state_tokens"]),
        connectivity_markers=set(merged["collapsed_reactant_connectivity"]),
        rc_markers=set(merged["collapsed_reactant_rc_states"]),
        energy_markers=set(merged["collapsed_reactant_energy_labels"]),
        explicit_flag="collapsed_to_reactant",
    )

    connectivity_invalid = connectivity_signature in set(merged["invalid_connectivity_signatures"])
    connectivity_ambiguous = connectivity_signature in set(
        merged["ambiguous_connectivity_signatures"]
    )
    rc_ambiguous = rc_state in set(merged["ambiguous_rc_states"])
    energy_invalid = energy_label in set(merged["invalid_energy_labels"])
    energy_valid = not energy_label or energy_label in set(merged["valid_energy_labels"])

    if missing:
        label = "ambiguous"
    elif not stationary_order_ok or not charge_ok or not multiplicity_ok:
        label = "invalid_minimum"
    elif connectivity_invalid or energy_invalid:
        label = "invalid_minimum"
    elif collapsed_to_product:
        label = "collapsed_to_product"
    elif collapsed_to_reactant:
        label = "collapsed_to_reactant"
    elif connectivity_ambiguous or rc_ambiguous or not energy_valid:
        label = "ambiguous"
    elif evidence.reaction_coordinate_state is None or evidence.connectivity_signature is None:
        label = "ambiguous"
    elif not rmsd_match_ok and closest_state_id is not None:
        label = "ambiguous"
        messages.append(
            "closest known-state RMSD "
            f"{closest_state_rmsd:.3f} Å to {closest_state_id} below match cutoff"
        )
    else:
        label = "valid_intermediate"

    return {
        "label": label,
        "valid": label == "valid_intermediate",
        "messages": messages,
        "missing_evidence": missing,
        "checks": {
            "stationary_order_ok": stationary_order_ok,
            "connectivity_signature": connectivity_signature,
            "reaction_coordinate_state": rc_state,
            "closest_state_id": closest_state_id,
            "closest_state_rmsd": closest_state_rmsd,
            "rmsd_match_ok": rmsd_match_ok,
            "energy_label": energy_label or None,
            "charge_ok": charge_ok,
            "multiplicity_ok": multiplicity_ok,
            "collapsed_to_product": collapsed_to_product,
            "collapsed_to_reactant": collapsed_to_reactant,
        },
        "thresholds": merged,
    }


def _normalized_mode(mode_vector: NDArray[np.float64]) -> NDArray[np.float64] | None:
    scaled = np.asarray(mode_vector, dtype=float).reshape(-1, 3)
    norm = float(np.linalg.norm(scaled))
    if norm <= 0.0:
        return None
    return scaled / norm


def _measure_coordinate(coords: NDArray[np.float64], spec: CoordinateSpec) -> float:
    if spec.kind == "distance":
        return GeometryUtils.calculate_distance(coords, spec.atoms[0], spec.atoms[1])
    if spec.kind == "angle":
        return GeometryUtils.calculate_angle(coords, spec.atoms[0], spec.atoms[1], spec.atoms[2])
    return GeometryUtils.calculate_dihedral(
        coords,
        spec.atoms[0],
        spec.atoms[1],
        spec.atoms[2],
        spec.atoms[3],
    )


def _coordinate_delta(spec: CoordinateSpec, q_plus: float, q_minus: float) -> float:
    delta = q_plus - q_minus
    if spec.kind != "dihedral":
        return float(delta)
    wrapped = (delta + _HALF_DIHEDRAL_PERIOD_DEG) % _DIHEDRAL_PERIOD_DEG
    return float(wrapped - _HALF_DIHEDRAL_PERIOD_DEG)


def _expected_direction_sign(spec: CoordinateSpec) -> float:
    if spec.kind == "dihedral":
        return 1.0
    if spec.start is None or spec.end is None:
        return 0.0
    if spec.end > spec.start:
        return 1.0
    if spec.end < spec.start:
        return -1.0
    return 0.0


def _non_negative_weight(weights: dict[str, float] | None, coordinate_id: str) -> float:
    if weights is None:
        return 1.0
    raw = float(weights.get(coordinate_id, 1.0))
    return raw if raw > 0.0 else 0.0


def _closest_rmsd_match(rmsd_to_known_states: dict[str, float]) -> tuple[str | None, float | None]:
    if not rmsd_to_known_states:
        return None, None
    closest_state_id = min(rmsd_to_known_states, key=rmsd_to_known_states.__getitem__)
    return closest_state_id, float(rmsd_to_known_states[closest_state_id])


def _is_collapsed_state(
    *,
    connectivity_signature: str,
    rc_state: str,
    energy_relationship: dict[str, Any],
    closest_state_id: str | None,
    closest_state_rmsd: float | None,
    rmsd_threshold: float,
    state_tokens: set[str],
    connectivity_markers: set[str],
    rc_markers: set[str],
    energy_markers: set[str],
    explicit_flag: str,
) -> bool:
    if connectivity_signature in connectivity_markers:
        return True
    if rc_state in rc_markers:
        return True
    energy_label = str(energy_relationship.get("label") or "")
    if energy_label in energy_markers:
        return True
    if bool(energy_relationship.get(explicit_flag)):
        return True
    if (
        closest_state_id is None
        or closest_state_rmsd is None
        or closest_state_rmsd > rmsd_threshold
    ):
        return False
    lowered = closest_state_id.lower()
    return any(token in lowered for token in state_tokens)


def _stable_state_thresholds() -> dict[str, Any]:
    return {
        "expected_stationary_order": 0,
        "expected_charge": None,
        "expected_multiplicity": None,
        "max_rmsd_match": 0.35,
        "valid_energy_labels": {
            "between_endpoints",
            "below_ts",
            "intermediate_like",
            "stable",
            "well_defined",
        },
        "invalid_energy_labels": {"above_ts", "unstable", "fragmented"},
        "collapsed_product_energy_labels": {"product_like", "matches_product"},
        "collapsed_reactant_energy_labels": {"reactant_like", "matches_reactant"},
        "invalid_connectivity_signatures": {"invalid", "broken", "fragmented"},
        "ambiguous_connectivity_signatures": {"ambiguous", "unknown"},
        "collapsed_product_connectivity": {"matches_product", "product_like"},
        "collapsed_reactant_connectivity": {"matches_reactant", "reactant_like"},
        "collapsed_product_rc_states": {"collapsed_to_product", "product_like"},
        "collapsed_reactant_rc_states": {"collapsed_to_reactant", "reactant_like"},
        "ambiguous_rc_states": {"ambiguous", "unknown"},
        "product_state_tokens": {"product"},
        "reactant_state_tokens": {"reactant"},
    }


__all__ = [
    "StableStateIdentityEvidence",
    "classify_stable_state",
    "classify_ts_identity",
    "compute_rc_alignment_score",
    "compute_mode_match_score",
    "validate_path_candidate",
]
