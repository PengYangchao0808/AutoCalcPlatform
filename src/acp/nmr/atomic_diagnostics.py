# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false
"""Atomic/signal-level risk diagnostics + per-nucleus DP4 decomposition (todo 39 / G16).

Gap G16: the workflow used to return only per-candidate floats. This module
builds the typed explanatory layer the gap doc asks for:

* per-nucleus DP4 decomposition ``dp4_13c`` / ``dp4_1h`` / ``dp4_combined``,
  all normalized from the same per-nucleus log-likelihoods (the combined
  value is the product over nuclei);
* per-signal residual records with their Goodman σ and uncalibrated
  ``z_score`` risk indicator;
* the todo-38 typed DP5 records (``Dp5ProbabilityRecord`` — consumed
  verbatim, including ``formal_probability`` and ``calibration_status``)
  and per-atom neighbour-support records built from
  ``kernel_similarity_support`` (``FchlSupport``);
* leave-one-signal-out ranking deltas (deterministic and recomputable);
* the inter-candidate claim/conflict matrix over experimental observations.

**Naming discipline (G16):** everything in this module is serialized under
the ``diagnostics`` namespace (see :data:`RISK_NAMESPACE`) and holds
atomic/signal RISK INDICATORS or set-normalized DP4 values. The
molecule-level calibrated probabilities stay in the ``probability``
namespace (:data:`CALIBRATED_NAMESPACE`, :class:`acp.nmr.models.CandidateProbability`)
— risk names never enter it and vice versa. Per-atom DP5 values are
diagnostic contributions, not formal calibrated probabilities; a
:class:`Dp5ProbabilityRecord` says so itself via ``calibration_status`` /
``formal_probability``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from acp.nmr.error_model import Dp5ProbabilityRecord, ErrorModel
from acp.nmr.fchl import AtomFchlProbability, FchlSupport
from acp.nmr.models import (
    Assignment,
    CandidateResult,
    ExperimentalNmr,
    ExperimentalPeak,
    NmrConfig,
    element_of_nucleus,
    nucleus_label,
)
from acp.nmr.probability import compute_dp4, normalize_dp4_gated

#: Namespace key under which risk indicators serialize (per candidate and at
#: the report top level). Never mixed with calibrated probabilities.
RISK_NAMESPACE = "diagnostics"
#: Namespace key under which molecule-level calibrated probabilities live.
CALIBRATED_NAMESPACE = "probability"
#: Discriminant written into every diagnostics block.
DIAGNOSTICS_KIND = "atomic_risk_indicators"

#: Convenience DP4 decomposition fields requested by the gap doc, keyed by
#: element symbol (the only nuclei the fields name explicitly).
_DP4_FIELD_BY_ELEMENT: dict[str, str] = {"H": "dp4_1h", "C": "dp4_13c"}


# ---------------------------------------------------------------------------
# typed records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NucleusDp4:
    """One nucleus's contribution to a candidate's DP4 state.

    Attributes:
        nucleus: Nucleus label (``"13C"`` / ``"1H"``).
        n_signals: Matched signals (residuals) for this nucleus.
        log_likelihood: Unnormalized per-nucleus log-likelihood (RISK scale),
            ``None`` when the candidate has no evidence for this nucleus.
        probability: Candidate-set-normalized DP4 over this nucleus alone,
            or ``None`` when the candidate is excluded / has no evidence.
        status: Evidence status of the candidate for this nucleus, or
            ``None`` when no evidence exists.
    """

    nucleus: str
    n_signals: int
    log_likelihood: float | None
    probability: float | None
    status: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "nucleus": self.nucleus,
            "n_signals": int(self.n_signals),
            "log_likelihood": self.log_likelihood,
            "probability": self.probability,
            "status": self.status,
        }


@dataclass(frozen=True)
class Dp4Decomposition:
    """Per-nucleus + combined DP4 decomposition for one candidate (G16).

    ``dp4_combined`` is the single value the ``probability`` namespace also
    reports: the product over nuclei of the same per-nucleus likelihoods,
    normalized across candidates. Missing nuclei stay ``None``.
    """

    dp4_13c: float | None
    dp4_1h: float | None
    dp4_combined: float | None
    nuclei: tuple[NucleusDp4, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "dp4_13c": self.dp4_13c,
            "dp4_1h": self.dp4_1h,
            "dp4_combined": self.dp4_combined,
            "nuclei": [record.as_dict() for record in self.nuclei],
        }


@dataclass(frozen=True)
class SignalRiskRecord:
    """One matched signal's uncalibrated residual risk indicators (G16).

    ``z_score`` is ``|residual| / σ`` against the error model's per-nucleus
    σ; ``None`` when the model exposes no σ for the nucleus (never
    fabricated). The residual convention is Goodman's ``scaled - exp``.
    """

    signal_id: str
    atom_label: str
    element: str
    nucleus: str
    exp_ppm: float
    calc_ppm: float
    scaled_ppm: float
    residual_ppm: float
    abs_residual_ppm: float
    sigma_ppm: float | None
    z_score: float | None
    observation_id: str | None
    signal_uids: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "signal_id": self.signal_id,
            "atom": self.atom_label,
            "element": self.element,
            "nucleus": self.nucleus,
            "exp_ppm": self.exp_ppm,
            "calc_ppm": self.calc_ppm,
            "scaled_ppm": self.scaled_ppm,
            "residual_ppm": self.residual_ppm,
            "abs_residual_ppm": self.abs_residual_ppm,
            "sigma_ppm": self.sigma_ppm,
            "z_score": self.z_score,
            "observation_id": self.observation_id,
            "signal_uids": list(self.signal_uids),
        }


@dataclass(frozen=True)
class AtomDp5Support:
    """One atom/signal's DP5 contribution + todo-38 neighbour support (G16).

    ``probability`` is the Boltzmann-weighted per-atom DP5 diagnostic value
    (NOT a molecule-level calibrated probability). ``support`` is the
    :class:`~acp.nmr.fchl.FchlSupport` of the worst-supported conformer (the
    conservative risk view); ``None`` when the unweighted global KDE ran and
    no neighbour weighting exists — support is never invented. ``mode`` is
    the todo-38 per-atom mode (``"fchl"``/``"fallback"``) of that worst
    conformer.
    """

    atom_label: str
    nucleus: str
    exp_ppm: float
    mode: str
    probability: float
    support: FchlSupport | None
    out_of_domain: bool
    n_conformers: int
    signal_uids: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "atom": self.atom_label,
            "nucleus": self.nucleus,
            "exp_ppm": self.exp_ppm,
            "mode": self.mode,
            "probability": self.probability,
            "support": self.support.as_dict() if self.support is not None else None,
            "out_of_domain": bool(self.out_of_domain),
            "n_conformers": int(self.n_conformers),
            "signal_uids": list(self.signal_uids),
        }


@dataclass(frozen=True)
class LeaveOneSignalOut:
    """Ranking/probability effect of dropping one matched signal (G16).

    Each record removes ONE signal from ITS candidate only (the other
    candidates keep their residuals) and re-normalizes the DP4. The
    evidence status after removal is recomputed from the signal count, so a
    candidate that drops below the evidence threshold is excluded — recorded
    as ``status_after`` + ``probability_after=None``, never silently kept.
    The ranking is DP4-only (ties: smallest candidate index first); DP5 is
    not recomputed for this diagnostic.
    """

    candidate_index: int
    signal_id: str
    nucleus: str
    exp_ppm: float
    n_signals_after: int
    status_after: str
    probability_before: float | None
    probability_after: float | None
    probability_delta: float | None
    winner_before: int | None
    winner_after: int | None
    winner_changed: bool
    ranking_before: tuple[int, ...]
    ranking_after: tuple[int, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "candidate_index": int(self.candidate_index),
            "signal_id": self.signal_id,
            "nucleus": self.nucleus,
            "exp_ppm": self.exp_ppm,
            "n_signals_after": int(self.n_signals_after),
            "status_after": self.status_after,
            "probability_before": self.probability_before,
            "probability_after": self.probability_after,
            "probability_delta": self.probability_delta,
            "winner_before": self.winner_before,
            "winner_after": self.winner_after,
            "winner_changed": bool(self.winner_changed),
            "ranking_before": list(self.ranking_before),
            "ranking_after": list(self.ranking_after),
        }


@dataclass(frozen=True)
class SignalClaim:
    """Which candidates claim one experimental observation (G16).

    ``candidate_indices`` lists the DISTINCT candidates claiming the
    observation; ``atom_labels`` is parallel (first claim per candidate).
    ``n_claims`` counts all claims, including a within-candidate duplicate.
    ``conflict`` is True when more than one candidate claims the observation.
    """

    observation_id: str
    nucleus: str
    exp_ppm: float | None
    candidate_indices: tuple[int, ...]
    atom_labels: tuple[str, ...]
    n_claims: int

    @property
    def conflict(self) -> bool:
        """True when several candidates claim the same observation."""
        return len(self.candidate_indices) > 1

    def as_dict(self) -> dict[str, object]:
        return {
            "observation_id": self.observation_id,
            "nucleus": self.nucleus,
            "exp_ppm": self.exp_ppm,
            "candidate_indices": list(self.candidate_indices),
            "atom_labels": list(self.atom_labels),
            "n_claims": int(self.n_claims),
            "conflict": self.conflict,
        }


@dataclass(frozen=True)
class ConflictMatrix:
    """Inter-candidate claim/conflict matrix over experimental signals (G16).

    Rows are the experimental observations (all configured-nuclei peaks when
    an experiment is supplied, plus any claimed observation not present
    there), ordered by element + numeric index. ``conflicting_observation_ids``
    are the observations claimed by more than one candidate.
    """

    candidate_indices: tuple[int, ...]
    observation_ids: tuple[str, ...]
    claims: tuple[SignalClaim, ...]
    conflicting_observation_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "candidate_indices": list(self.candidate_indices),
            "observation_ids": list(self.observation_ids),
            "conflicting_observation_ids": list(self.conflicting_observation_ids),
            "claims": [claim.as_dict() for claim in self.claims],
        }


@dataclass(frozen=True)
class CandidateAtomicDiagnostics:
    """Full per-candidate diagnostics block (G16, ``diagnostics`` namespace)."""

    candidate_index: int
    label: str
    dp4: Dp4Decomposition
    signals: tuple[SignalRiskRecord, ...]
    dp5: Dp5ProbabilityRecord | None
    atom_support: tuple[AtomDp5Support, ...]
    leave_one_signal_out: tuple[LeaveOneSignalOut, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": DIAGNOSTICS_KIND,
            "candidate_index": int(self.candidate_index),
            "label": self.label,
            "dp4": self.dp4.as_dict(),
            "signals": [record.as_dict() for record in self.signals],
            "dp5": self.dp5.as_dict() if self.dp5 is not None else None,
            "atom_support": [record.as_dict() for record in self.atom_support],
            "leave_one_signal_out": [record.as_dict() for record in self.leave_one_signal_out],
        }


@dataclass(frozen=True)
class AtomicDiagnosticsBundle:
    """Per-candidate diagnostics + the cross-candidate conflict matrix (G16)."""

    candidates: tuple[CandidateAtomicDiagnostics, ...]
    conflict_matrix: ConflictMatrix

    @property
    def leave_one_signal_out(self) -> tuple[LeaveOneSignalOut, ...]:
        """Every leave-one-signal-out record, in candidate order."""
        return tuple(
            record for candidate in self.candidates for record in candidate.leave_one_signal_out
        )

    @property
    def winner_flips(self) -> tuple[LeaveOneSignalOut, ...]:
        """Leave-one-signal-out records that changed the DP4-only winner."""
        return tuple(record for record in self.leave_one_signal_out if record.winner_changed)

    def report_block(self) -> dict[str, object]:
        """Report-level diagnostics block (conflict matrix + flip summary).

        Full per-signal cases stay in each candidate's block; the report
        keeps the counts and the actionable winner-flip records.
        """
        flips = self.winner_flips
        return {
            "kind": DIAGNOSTICS_KIND,
            "conflict_matrix": self.conflict_matrix.as_dict(),
            "leave_one_signal_out": {
                "n_cases": len(self.leave_one_signal_out),
                "n_winner_flips": len(flips),
                "winner_flips": [record.as_dict() for record in flips],
            },
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _nucleus_for_element(element: str) -> str:
    """Canonical nucleus label for an assignment element (``"C"`` → ``"13C"``)."""
    return nucleus_label(element)


def _sigma_for(error_model: ErrorModel, nucleus: str) -> float | None:
    """Per-nucleus σ from the error model, or ``None`` when not exposed."""
    for attribute in ("SIGMA", "sigma"):
        sigmas = getattr(error_model, attribute, None)
        if isinstance(sigmas, Mapping):
            value = sigmas.get(nucleus)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                number = float(value)
                if math.isfinite(number) and number > 0:
                    return number
    return None


def _residuals_by_nucleus(
    candidate: CandidateResult, nuclei: Sequence[str]
) -> dict[str, list[float]]:
    return {
        nucleus: [
            assignment.residual
            for assignment in candidate.assignments
            if _nucleus_for_element(assignment.element) == nucleus
        ]
        for nucleus in nuclei
    }


def _combined_log_likelihood(
    candidate: CandidateResult,
    nuclei: Sequence[str],
    error_model: ErrorModel,
) -> float:
    """Same sum-of-per-nucleus-log-likelihoods the DP4 stage uses."""
    residuals = _residuals_by_nucleus(candidate, nuclei)
    return sum(compute_dp4({nucleus: values}, error_model) for nucleus, values in residuals.items())


def _signal_ids(candidate: CandidateResult) -> tuple[str, ...]:
    """Stable per-candidate signal ids (observation id when recorded)."""
    counts: dict[str, int] = {}
    ids: list[str] = []
    for assignment in candidate.assignments:
        nucleus = _nucleus_for_element(assignment.element)
        if assignment.observation_id:
            ids.append(assignment.observation_id)
        else:
            position = counts.get(nucleus, 0)
            ids.append(f"{nucleus}#{position + 1}")
        counts[nucleus] = counts.get(nucleus, 0) + 1
    return tuple(ids)


def _signal_records(
    candidate: CandidateResult, error_model: ErrorModel
) -> tuple[SignalRiskRecord, ...]:
    records: list[SignalRiskRecord] = []
    for signal_id, assignment in zip(_signal_ids(candidate), candidate.assignments, strict=True):
        nucleus = _nucleus_for_element(assignment.element)
        sigma = _sigma_for(error_model, nucleus)
        residual = float(assignment.residual)
        group = assignment.signal_group
        records.append(
            SignalRiskRecord(
                signal_id=signal_id,
                atom_label=assignment.atom_label,
                element=assignment.element,
                nucleus=nucleus,
                exp_ppm=assignment.exp_ppm,
                calc_ppm=assignment.calc_ppm,
                scaled_ppm=assignment.scaled_ppm,
                residual_ppm=residual,
                abs_residual_ppm=abs(residual),
                sigma_ppm=sigma,
                z_score=(abs(residual) / sigma if sigma is not None else None),
                observation_id=assignment.observation_id,
                signal_uids=(
                    tuple(group.atom_uids) if group is not None else (assignment.atom_label,)
                ),
            )
        )
    return tuple(records)


def _rank(probabilities: Sequence[float | None]) -> tuple[int, ...]:
    """DP4-only ranking: probability desc, then smallest candidate index."""
    ranked = [(index, value) for index, value in enumerate(probabilities) if value is not None]
    ranked.sort(key=lambda pair: (-pair[1], pair[0]))
    return tuple(index for index, _ in ranked)


def _status_after_removal(baseline_status: str, total_after: int) -> str:
    """Evidence status after dropping a signal (count-based, conservative)."""
    if baseline_status == "invalid":
        return "invalid"
    if total_after == 0:
        return "invalid"
    if total_after <= 2:
        return "evidence_insufficient"
    return "valid"


def _nucleus_records(
    candidates: Sequence[CandidateResult],
    nuclei: Sequence[str],
    error_model: ErrorModel,
    statuses: Sequence[str],
) -> dict[str, list[NucleusDp4]]:
    records: dict[str, list[NucleusDp4]] = {nucleus: [] for nucleus in nuclei}
    for nucleus in nuclei:
        counts: list[int] = []
        likelihoods: list[float | None] = []
        for candidate in candidates:
            residuals = [
                assignment.residual
                for assignment in candidate.assignments
                if _nucleus_for_element(assignment.element) == nucleus
            ]
            counts.append(len(residuals))
            likelihoods.append(
                compute_dp4({nucleus: residuals}, error_model) if residuals else None
            )
        nucleus_statuses = [
            statuses[index] if counts[index] > 0 else "invalid" for index in range(len(candidates))
        ]
        probabilities = normalize_dp4_gated(
            [value if value is not None else 0.0 for value in likelihoods],
            nucleus_statuses,
        )
        for index in range(len(candidates)):
            records[nucleus].append(
                NucleusDp4(
                    nucleus=nucleus,
                    n_signals=counts[index],
                    log_likelihood=likelihoods[index],
                    probability=probabilities[index],
                    status=(None if counts[index] == 0 else statuses[index]),
                )
            )
    return records


def _decomposition_for(
    index: int,
    records: Mapping[str, Sequence[NucleusDp4]],
    nuclei: Sequence[str],
    combined: Sequence[float | None],
) -> Dp4Decomposition:
    per_nucleus = tuple(records[nucleus][index] for nucleus in nuclei)
    fields: dict[str, float | None] = {}
    for record in per_nucleus:
        field = _DP4_FIELD_BY_ELEMENT.get(element_of_nucleus(record.nucleus))
        if field is not None:
            fields[field] = record.probability
    return Dp4Decomposition(
        dp4_13c=fields.get("dp4_13c"),
        dp4_1h=fields.get("dp4_1h"),
        dp4_combined=combined[index],
        nuclei=per_nucleus,
    )


def _leave_one_signal_out(
    candidates: Sequence[CandidateResult],
    nuclei: Sequence[str],
    error_model: ErrorModel,
    statuses: Sequence[str],
    baseline_likelihoods: Sequence[float],
    baseline_probabilities: Sequence[float | None],
) -> tuple[LeaveOneSignalOut, ...]:
    baseline_ranking = _rank(baseline_probabilities)
    baseline_winner = baseline_ranking[0] if baseline_ranking else None
    records: list[LeaveOneSignalOut] = []
    for position, candidate in enumerate(candidates):
        signal_ids = _signal_ids(candidate)
        for signal_position, assignment in enumerate(candidate.assignments):
            remaining_likelihood = 0.0
            for nucleus in nuclei:
                residuals = [
                    other.residual
                    for other_position, other in enumerate(candidate.assignments)
                    if other_position != signal_position
                    and _nucleus_for_element(other.element) == nucleus
                ]
                if residuals:
                    remaining_likelihood += compute_dp4({nucleus: residuals}, error_model)
            perturbed_likelihoods = list(baseline_likelihoods)
            perturbed_likelihoods[position] = remaining_likelihood
            status_after = _status_after_removal(statuses[position], len(candidate.assignments) - 1)
            perturbed_statuses = list(statuses)
            perturbed_statuses[position] = status_after
            probabilities = normalize_dp4_gated(perturbed_likelihoods, perturbed_statuses)
            ranking = _rank(probabilities)
            winner = ranking[0] if ranking else None
            before = baseline_probabilities[position]
            after = probabilities[position]
            records.append(
                LeaveOneSignalOut(
                    candidate_index=candidate.index,
                    signal_id=signal_ids[signal_position],
                    nucleus=_nucleus_for_element(assignment.element),
                    exp_ppm=assignment.exp_ppm,
                    n_signals_after=len(candidate.assignments) - 1,
                    status_after=status_after,
                    probability_before=before,
                    probability_after=after,
                    probability_delta=(
                        after - before if before is not None and after is not None else None
                    ),
                    winner_before=baseline_winner,
                    winner_after=winner,
                    winner_changed=(winner != baseline_winner),
                    ranking_before=baseline_ranking,
                    ranking_after=ranking,
                )
            )
    return tuple(records)


def _peak_observation_id(peak: ExperimentalPeak, position: int) -> str:
    """Stable observation id: ``"element:index"`` (parser position fallback)."""
    index = peak.index if peak.index is not None else position
    return f"{peak.element}:{index}"


def _observation_sort_key(observation_id: str) -> tuple[str, int, int, str]:
    element, _, rest = observation_id.partition(":")
    try:
        return (element, 0, int(rest), observation_id)
    except ValueError:
        return (element, 1, 0, observation_id)


def _conflict_matrix(
    candidates: Sequence[CandidateResult],
    experiment: ExperimentalNmr | None,
    nuclei: Sequence[str],
) -> ConflictMatrix:
    rows: dict[str, tuple[str, float | None]] = {}
    if experiment is not None:
        for nucleus in nuclei:
            element = element_of_nucleus(nucleus)
            for position, peak in enumerate(experiment.peaks_for(element)):
                observation_id = _peak_observation_id(peak, position)
                rows.setdefault(observation_id, (nucleus_label(peak.element), peak.shift_ppm))
    claims_by_candidate: dict[str, dict[int, str]] = {}
    claim_counts: dict[str, int] = {}
    for candidate in candidates:
        for assignment in candidate.assignments:
            observation_id = assignment.observation_id
            if not observation_id:
                continue
            rows.setdefault(
                observation_id,
                (_nucleus_for_element(assignment.element), assignment.exp_ppm),
            )
            claims_by_candidate.setdefault(observation_id, {})
            claims_by_candidate[observation_id].setdefault(candidate.index, assignment.atom_label)
            claim_counts[observation_id] = claim_counts.get(observation_id, 0) + 1
    ordered_ids = sorted(rows, key=_observation_sort_key)
    claims: list[SignalClaim] = []
    for observation_id in ordered_ids:
        by_candidate = claims_by_candidate.get(observation_id, {})
        candidate_indices = tuple(sorted(by_candidate))
        label, exp_ppm = rows[observation_id]
        claims.append(
            SignalClaim(
                observation_id=observation_id,
                nucleus=label,
                exp_ppm=exp_ppm,
                candidate_indices=candidate_indices,
                atom_labels=tuple(by_candidate[index] for index in candidate_indices),
                n_claims=claim_counts.get(observation_id, 0),
            )
        )
    return ConflictMatrix(
        candidate_indices=tuple(sorted(candidate.index for candidate in candidates)),
        observation_ids=tuple(ordered_ids),
        claims=tuple(claims),
        conflicting_observation_ids=tuple(
            claim.observation_id for claim in claims if claim.conflict
        ),
    )


# ---------------------------------------------------------------------------
# public builders
# ---------------------------------------------------------------------------


def build_atomic_diagnostics(
    candidate_results: Sequence[CandidateResult],
    nmr_config: NmrConfig,
    error_model: ErrorModel,
    *,
    experiment: ExperimentalNmr | None = None,
    dp5_records: Mapping[int, Dp5ProbabilityRecord] | None = None,
    atom_support: Mapping[int, Sequence[AtomDp5Support]] | None = None,
) -> AtomicDiagnosticsBundle:
    """Build the full atomic/signal diagnostics bundle for a candidate set.

    Pure analysis over already-computed assignments/conformations — never
    runs QC and never invents missing evidence. The DP4 values are
    re-derived from the same per-candidate residuals the stage-7 gate used,
    so ``dp4_combined`` matches the calibrated DP4 probability exactly.

    Args:
        candidate_results: Per-candidate analyses (index/label/assignments;
            evidence statuses drive the same exclusion as stage 7).
        nmr_config: Effective NMR configuration (configured nuclei).
        error_model: DP4 error model (σ lookup for the risk ``z_score``).
        experiment: Parsed experimental input; when given, the conflict
            matrix carries a row for every experimental observation of the
            configured nuclei (including unclaimed ones).
        dp5_records: Per-candidate todo-38 typed DP5 records, keyed by
            candidate index (consumed verbatim).
        atom_support: Per-candidate per-atom FCHL support records
            (:func:`aggregate_atom_support` output), keyed by candidate index.

    Returns:
        Frozen :class:`AtomicDiagnosticsBundle`; candidates keep their input
        order.
    """
    candidates = list(candidate_results)
    nuclei = tuple(nmr_config.nuclei)
    statuses = [
        candidate.evidence.status if candidate.evidence is not None else "valid"
        for candidate in candidates
    ]
    baseline_likelihoods = [
        _combined_log_likelihood(candidate, nuclei, error_model) for candidate in candidates
    ]
    baseline_probabilities = normalize_dp4_gated(baseline_likelihoods, statuses)
    nucleus_records = _nucleus_records(candidates, nuclei, error_model, statuses)
    loo_records = _leave_one_signal_out(
        candidates,
        nuclei,
        error_model,
        statuses,
        baseline_likelihoods,
        baseline_probabilities,
    )
    dp5_by_index = dict(dp5_records) if dp5_records is not None else {}
    support_by_index = dict(atom_support) if atom_support is not None else {}
    per_candidate = tuple(
        CandidateAtomicDiagnostics(
            candidate_index=candidate.index,
            label=candidate.label,
            dp4=_decomposition_for(index, nucleus_records, nuclei, baseline_probabilities),
            signals=_signal_records(candidate, error_model),
            dp5=dp5_by_index.get(candidate.index),
            atom_support=tuple(support_by_index.get(candidate.index, ())),
            leave_one_signal_out=tuple(
                record for record in loo_records if record.candidate_index == candidate.index
            ),
        )
        for index, candidate in enumerate(candidates)
    )
    return AtomicDiagnosticsBundle(
        candidates=per_candidate,
        conflict_matrix=_conflict_matrix(candidates, experiment, nuclei),
    )


def aggregate_atom_support(
    assignments: Sequence[Assignment],
    exp_values: Sequence[float],
    atom_records: Sequence[Sequence[AtomFchlProbability]],
    weights: Sequence[float],
) -> tuple[AtomDp5Support, ...]:
    """Aggregate per-conformer todo-38 atom records into per-signal support.

    ``atom_records[c][i]`` is the :class:`AtomFchlProbability` the weighted
    KDE pass computed for signal *i* in conformer *c* (same computation, no
    re-derivation). The returned probability is the Boltzmann-weighted mean
    of the per-conformer probabilities; the support is the worst-supported
    conformer's typed :class:`FchlSupport`, and ``out_of_domain`` is True
    when any conformer had no contributing neighbour.

    Raises:
        ValueError: Parallel lengths disagree — a partial aggregation is
            never produced.
    """
    assignment_list = list(assignments)
    values = list(exp_values)
    conformer_rows = [list(row) for row in atom_records]
    weight_list = list(weights)
    if len(assignment_list) != len(values):
        raise ValueError(
            f"length mismatch: {len(assignment_list)} assignments vs {len(values)} exp values"
        )
    if len(weight_list) != len(conformer_rows):
        raise ValueError(
            f"length mismatch: {len(weight_list)} weights vs {len(conformer_rows)} conformers"
        )
    for row in conformer_rows:
        if len(row) != len(assignment_list):
            raise ValueError(
                f"length mismatch: conformer row has {len(row)} atoms vs "
                f"{len(assignment_list)} signals"
            )
    if not assignment_list or not conformer_rows:
        return ()
    records: list[AtomDp5Support] = []
    for index, assignment in enumerate(assignment_list):
        atoms = [row[index] for row in conformer_rows]
        probability = math.fsum(
            weight * atom.probability for weight, atom in zip(weight_list, atoms, strict=True)
        )
        worst = min(
            atoms,
            key=lambda atom: (
                atom.support.effective_neighbors,
                atom.support.support_fraction,
                atom.mode,
            ),
        )
        group = assignment.signal_group
        records.append(
            AtomDp5Support(
                atom_label=assignment.atom_label,
                nucleus=_nucleus_for_element(assignment.element),
                exp_ppm=float(values[index]),
                mode=worst.mode,
                probability=probability,
                support=worst.support,
                out_of_domain=any(atom.out_of_domain for atom in atoms),
                n_conformers=len(atoms),
                signal_uids=(
                    tuple(group.atom_uids) if group is not None else (assignment.atom_label,)
                ),
            )
        )
    return tuple(records)


__all__ = [
    "CALIBRATED_NAMESPACE",
    "DIAGNOSTICS_KIND",
    "RISK_NAMESPACE",
    "AtomDp5Support",
    "AtomicDiagnosticsBundle",
    "CandidateAtomicDiagnostics",
    "ConflictMatrix",
    "Dp4Decomposition",
    "LeaveOneSignalOut",
    "NucleusDp4",
    "SignalClaim",
    "SignalRiskRecord",
    "aggregate_atom_support",
    "build_atomic_diagnostics",
]
