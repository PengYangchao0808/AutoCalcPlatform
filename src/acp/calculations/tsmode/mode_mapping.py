"""Source-mode → optimizer-mode mapping (plan §6 — launch hard gate).

ORCA's ``TS_Mode {M n}`` numbers modes by the *optimizer's* representation
of the Hessian it reads: ``M 0`` is the lowest eigenvalue.  That numbering
is NOT the printed vibrational-frequency index of a frequency output, and
no unverified "subtract 6 / subtract 5" conversion may be used.

The mapping implemented here is evidence-based: the user's source mode
vector is mass-weighted, stripped of translation/rotation, normalized, and
compared by absolute overlap against every eigenvector of the projected
mass-weighted source Hessian.  The best-match position in the ascending
eigenvalue ordering becomes ``optimizer_mode_index``.  Near-degenerate
competitors (``best − second < gap``) downgrade the status to
``ambiguous``; no confident match is ``mismatch``.

Honesty gate (plan §6.2/§6.3): whether ORCA's eigenvalue-rank numbering is
exactly what the deployed engine consumes must be confirmed against real
ORCA samples (milestone P0).  Until a version string appears in
``TS_MODE_MAPPING_VERIFIED_VERSIONS``, mapping resolutions carry
``verified_against_orca: null`` and the engine blocks directed execution
unless the request explicitly opts out
(:attr:`~acp.calculations.tsmode.contracts.TsmodeOptimizationSettings.require_verified_mapping`
is ``False``).
"""

from __future__ import annotations

import logging

import numpy as np
from numpy.typing import NDArray

from .contracts import (
    MODE_MAPPING_AMBIGUOUS,
    MODE_MAPPING_UNSUPPORTED,
    TARGET_MODE_INVALID,
    FrequencySourceBundle,
    TargetResolution,
    TsmodeError,
    compute_target_mode_id,
)
from .source import HessianModes, compute_hessian_modes, mass_weight_and_project

logger = logging.getLogger(__name__)

__all__ = [
    "MAPPING_METHOD",
    "MAPPING_VERSION",
    "TS_MODE_MAPPING_VERIFIED_VERSIONS",
    "enforce_launch_gate",
    "overlap_evidence",
    "resolve_target_mode",
]

MAPPING_METHOD = "mass_weighted_projected_overlap"
MAPPING_VERSION = "eigenvalue_rank_v1"

#: Default minimum |overlap| for a resolved mapping (plan §6.2 — thresholds
#: are calibrated against real samples; 0.95 is the documented default and
#: the full evidence vector is always recorded).
OVERLAP_THRESHOLD_DEFAULT = 0.95
#: Best−second overlap separation required for a unique (non-ambiguous) map.
DEGENERACY_GAP_DEFAULT = 0.10

#: ORCA versions whose ``TS_Mode {M n}`` numbering has been verified against
#: real samples (milestone P0).  **Empty until that verification runs** —
#: see module docstring.
TS_MODE_MAPPING_VERIFIED_VERSIONS: frozenset[str] = frozenset()


def overlap_evidence(
    source_vector_mw: NDArray[np.float64],
    modes: HessianModes,
) -> list[dict[str, float | int]]:
    """Overlap of one prepared source vector with every vibrational mode.

    ``|⟨u_k, v⟩|`` (absolute inner product) — NOT the componentwise-abs dot,
    which would inflate overlaps between orthogonal vectors.  A global sign
    flip of either vector is the same physical subspace.
    """
    overlaps = np.abs(modes.eigenvectors.reshape((len(modes.eigenvalues), -1)) @ source_vector_mw)
    evidence: list[dict[str, float | int]] = []
    for position, (eigenvalue, frequency, overlap) in enumerate(
        zip(modes.eigenvalues, modes.frequencies_cm1, overlaps)
    ):
        evidence.append(
            {
                "optimizer_mode_index": position,
                "eigenvalue": float(eigenvalue),
                "frequency_cm1": float(frequency),
                "abs_overlap": float(overlap),
            }
        )
    evidence.sort(key=lambda item: float(item["abs_overlap"]), reverse=True)
    return evidence


def resolve_target_mode(
    bundle: FrequencySourceBundle,
    source_mode_index: int,
    *,
    hessian: NDArray[np.float64] | None = None,
    overlap_threshold: float = OVERLAP_THRESHOLD_DEFAULT,
    degeneracy_gap: float = DEGENERACY_GAP_DEFAULT,
    orca_version: str | None = None,
) -> TargetResolution:
    """Bind the user's chosen mode to an optimizer mode index.

    Args:
        bundle: Validated frequency source bundle.
        source_mode_index: Native printed mode index chosen by the user.
        hessian: Pre-parsed ``(3N, 3N)`` Hessian; re-read from the bundle
            file when ``None``.
        overlap_threshold: Minimum |overlap| for ``resolved``.
        degeneracy_gap: Minimum ``best − second`` separation for a unique
            mapping; smaller gaps produce ``ambiguous``.
        orca_version: Backend version to check against the verified matrix.

    Returns:
        :class:`TargetResolution` with status
        ``resolved`` / ``ambiguous`` / ``mismatch`` / ``unsupported``.

    Raises:
        TsmodeError: ``target_mode_invalid`` for non-imaginary, unknown, or
            corrupt modes.
    """
    if isinstance(source_mode_index, bool) or not isinstance(source_mode_index, int):
        raise TsmodeError(
            TARGET_MODE_INVALID,
            f"source_mode_index must be an int, got {type(source_mode_index).__name__}",
        )
    if source_mode_index < 0:
        raise TsmodeError(
            TARGET_MODE_INVALID, f"source_mode_index must be >= 0, got {source_mode_index}"
        )
    mode = bundle.mode_by_index(source_mode_index)
    if mode is None:
        raise TsmodeError(
            TARGET_MODE_INVALID,
            f"mode {source_mode_index} does not exist in the frequency source "
            f"(valid: {min(m.source_mode_index for m in bundle.modes)}.."
            f"{max(m.source_mode_index for m in bundle.modes)})",
        )
    if not mode.is_imaginary:
        raise TsmodeError(
            TARGET_MODE_INVALID,
            f"mode {source_mode_index} has frequency {mode.frequency_cm1:.1f} cm⁻¹; "
            "only imaginary modes can direct a TS Mode optimization",
        )
    if not mode.has_complete_vectors(bundle.n_atoms):
        raise TsmodeError(
            TARGET_MODE_INVALID,
            f"mode {source_mode_index} has incomplete displacement vectors",
        )

    if hessian is None:
        from cccp.qc.interfaces.hess_file import parse_orca_hess_file

        hess_data = parse_orca_hess_file(bundle.hessian_file)
        hessian = hess_data.hessian

    coordinates = np.asarray(bundle.coordinates_angstrom, dtype=np.float64)
    masses = np.asarray(bundle.masses_amu, dtype=np.float64)
    modes = compute_hessian_modes(hessian, masses, coordinates)

    source_vector = np.asarray(mode.vectors, dtype=np.float64)
    prepared = mass_weight_and_project(source_vector, masses, coordinates, modes.projector)
    if not np.isfinite(prepared).all() or float(np.linalg.norm(prepared)) <= 0:
        raise TsmodeError(
            TARGET_MODE_INVALID,
            f"mode {source_mode_index} vector projects to zero after removing translation/rotation",
        )

    evidence_list = overlap_evidence(prepared, modes)
    best = evidence_list[0]
    second = evidence_list[1] if len(evidence_list) > 1 else None
    best_overlap = float(best["abs_overlap"])
    second_overlap = float(second["abs_overlap"]) if second else 0.0

    verified_version = None
    if orca_version:
        for verified in TS_MODE_MAPPING_VERIFIED_VERSIONS:
            if orca_version.startswith(verified):
                verified_version = verified
                break

    status: str
    if best_overlap >= overlap_threshold:
        status = "ambiguous" if best_overlap - second_overlap < degeneracy_gap else "resolved"
    elif best_overlap * best_overlap + second_overlap * second_overlap >= 0.95:
        # The source vector is a mixture of two modes (exact degeneracy
        # makes the eigenbasis arbitrary): the subspace is identified but
        # no unique eigenvector exists — plan §6.3 "ambiguous".
        status = "ambiguous"
    else:
        status = "mismatch"

    target_mode_id = compute_target_mode_id(bundle.hessian_sha256, source_mode_index, mode.vectors)
    optimizer_index = int(best["optimizer_mode_index"]) if status == "resolved" else None

    evidence_payload = {
        "method": MAPPING_METHOD,
        "prepared_source_vector_norm": float(np.linalg.norm(prepared)),
        "best_overlap": best_overlap,
        "best_optimizer_mode_index": int(best["optimizer_mode_index"]),
        "best_eigen_frequency_cm1": float(best["frequency_cm1"]),
        "second_overlap": second_overlap,
        "second_optimizer_mode_index": (int(second["optimizer_mode_index"]) if second else None),
        "overlap_threshold": overlap_threshold,
        "degeneracy_gap": degeneracy_gap,
        "separation": best_overlap - second_overlap,
        "n_zero_modes_removed": modes.n_zero_modes_removed,
        "verified_against_orca": verified_version,
        "assumption": (
            "ORCA TS_Mode {M n} counts eigenvalues of the projected, "
            "mass-weighted Hessian ascending (M 0 = lowest eigenvalue); "
            "pending real-ORCA sample verification (plan §6.2, milestone P0)"
        ),
        "top_matches": evidence_list[:5],
    }

    resolution = TargetResolution(
        source_mode_index=source_mode_index,
        source_frequency_cm1=mode.frequency_cm1,
        target_mode_id=target_mode_id,
        optimizer_mode_index=optimizer_index,
        status=status,  # type: ignore[arg-type]
        mapping_method=MAPPING_METHOD,
        mapping_version=MAPPING_VERSION,
        evidence=evidence_payload,
    )
    if status == "ambiguous":
        logger.warning(
            "tsmode mapping ambiguous for mode %d: best %.3f vs second %.3f",
            source_mode_index,
            best_overlap,
            second_overlap,
        )
    return resolution


def enforce_launch_gate(
    resolution: TargetResolution,
    *,
    require_verified: bool,
    orca_version: str | None,
) -> None:
    """Apply the plan §6.3 launch hard gate.

    Raises:
        TsmodeError: ``mode_mapping_ambiguous`` for a non-unique mapping,
            or ``mode_mapping_unsupported`` when the eigenvalue-rank
            assumption has not been verified for the deployed engine (and
            the request did not explicitly allow unverified mapping).
    """
    if resolution.status == "ambiguous":
        raise TsmodeError(
            MODE_MAPPING_AMBIGUOUS,
            "target mode could not be uniquely mapped to an optimizer mode "
            "(near-degenerate modes) — exit this flow and start a plain TS "
            "optimization instead",
        )
    if resolution.status in ("mismatch",):
        raise TsmodeError(
            MODE_MAPPING_AMBIGUOUS,
            "source mode does not correspond to any Hessian eigenvector — "
            "the source files are inconsistent",
        )
    if resolution.status != "resolved" or resolution.optimizer_mode_index is None:
        raise TsmodeError(
            MODE_MAPPING_UNSUPPORTED,
            f"target resolution status {resolution.status!r} cannot drive a directed optimization",
        )
    verified = resolution.evidence.get("verified_against_orca")
    if require_verified and not verified:
        raise TsmodeError(
            MODE_MAPPING_UNSUPPORTED,
            "the TS_Mode eigenvalue-rank mapping has not been verified against "
            f"real ORCA {orca_version or '(version unknown)'} samples yet "
            "(plan §6, milestone P0); resubmit with require_verified_mapping="
            "false only after completing that verification",
        )
